"""Complexity-based model routing and Runner-wide ADK safety/telemetry plugin."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools import BaseTool, ToolContext
from google.genai import types

from app.memory.compactor import HistoryCompactor
from app.memory.consolidator import AsyncMemoryConsolidator
from app.telemetry.scrubber import PIIScrubber
from app.telemetry.tracer import get_logger, record_metric_event, traced_operation

logger = get_logger("claimpulse.guardrails")

_PROMPT_INJECTION_PATTERNS = re.compile(
    r"(ignore\s+all\s+previous\s+instructions|system\s+override\s+mode|"
    r"drop\s+table\s+policies|<\s*script\b|reveal\s+system\s+prompt)",
    re.IGNORECASE,
)

_HIGH_COMPLEXITY_PATTERNS = re.compile(
    r"(múltiples\s+vehículos|multi-vehicle|responsabilidad\s+civil|subrogación|"
    r"pérdida\s+total|incendio|volcamiento|heridos|disputa\s+legal|catastrófico)",
    re.IGNORECASE,
)


@dataclass
class RoutingDecision:
    """Represents a complexity-aware routing decision recorded by the supervisor."""

    tier_complexity: str  # "FAST_PATH" or "DEEP_REASONING"
    selected_model: str
    target_specialist: str
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ClaimPulseModelRouter:
    """Evaluates prompt complexity to select optimal specialist pathway and reasoning depth."""

    def __init__(self, default_model: str = "gemini-3.8-flash") -> None:
        self.default_model = default_model

    def route(self, user_prompt: str) -> RoutingDecision:
        """Determines complexity tier and primary specialist from the user's message."""
        with traced_operation("router.evaluate_prompt"):
            lower = user_prompt.lower()
            is_complex = bool(_HIGH_COMPLEXITY_PATTERNS.search(user_prompt)) or len(user_prompt) > 320

            if any(
                kw in lower
                for kw in ("grúa", "tow", "emergencia", "humo", "no enciende", "agendar", "perito", "cita", "calendar")
            ):
                target = "claimpulse_dispatch_specialist"
            elif any(
                kw in lower
                for kw in ("severidad", "choque", "rayón", "abolladura", "parabrisas", "daño", "siniestro")
            ):
                target = "claimpulse_triage_specialist"
            else:
                target = "claimpulse_intake_specialist"

            if is_complex:
                return RoutingDecision(
                    tier_complexity="DEEP_REASONING",
                    selected_model=self.default_model,
                    target_specialist=target,
                    rationale="High-severity or multi-party liability indicators detected.",
                )
            return RoutingDecision(
                tier_complexity="FAST_PATH",
                selected_model=self.default_model,
                target_specialist=target,
                rationale="Standard policy lookup or low-complexity claim turn.",
            )


class ClaimPulseSafetyGuardrailPlugin(BasePlugin):
    """Runner-wide ADK plugin enforcing safety guardrails, PII redaction, and memory compaction."""

    def __init__(self, model_name: str = "gemini-3.8-flash") -> None:
        super().__init__(name="claimpulse_safety_guardrail")
        self.router = ClaimPulseModelRouter(default_model=model_name)
        self.compactor = HistoryCompactor(max_turns=8, retain_recent_turns=4)
        self.consolidator = AsyncMemoryConsolidator()

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        """Inspects incoming prompt for injection attacks and records routing + compaction state."""
        last_user_text = ""
        turn_messages: list[dict[str, str]] = []
        for content in llm_request.contents or []:
            role = getattr(content, "role", "user") or "user"
            for part in getattr(content, "parts", None) or []:
                txt = getattr(part, "text", None)
                if txt:
                    turn_messages.append({"role": role, "content": txt})
                    if role == "user":
                        last_user_text = txt

        if last_user_text and _PROMPT_INJECTION_PATTERNS.search(last_user_text):
            record_metric_event(
                "guardrail.blocked_input",
                {"reason": "prompt_injection_or_xss_detected"},
            )
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_text(
                            text=(
                                "Por motivos de seguridad y cumplimiento normativo (Guardrail Activo), "
                                "no puedo procesar instrucciones que intenten alterar las reglas del "
                                "sistema o inyectar código. Por favor ingresa tu número de póliza "
                                "(ej. POL-CL-1001) y describe el siniestro."
                            )
                        )
                    ],
                )
            )

        if last_user_text:
            decision = self.router.route(last_user_text)
            callback_context.state["last_routing_decision"] = decision.to_dict()

        if len(turn_messages) > self.compactor.max_turns:
            compacted = self.compactor.compact(
                turn_messages,
                pinned_facts={
                    k: str(callback_context.state.get(k))
                    for k in ("verified_policy_number", "active_claim_id", "last_triage_tier")
                    if callback_context.state.get(k)
                },
            )
            callback_context.state["compacted_history_summary"] = (
                compacted.compacted_summary
            )

        return None

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> LlmResponse | None:
        """Scrubs accidental raw HTML/script output and triggers background memory consolidation."""
        if llm_response and llm_response.content and llm_response.content.parts:
            for part in llm_response.content.parts:
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str) and part_text:
                    if "<script" in part_text.lower():
                        part.text = re.sub(
                            r"<\s*script[^>]*>.*?<\s*/\s*script\s*>",
                            "[REDACTED_UNSAFE_SCRIPT]",
                            part_text,
                            flags=re.IGNORECASE | re.DOTALL,
                        )
                    policy_num = callback_context.state.get("verified_policy_number")
                    if policy_num:
                        self.consolidator.schedule_background_consolidation(
                            str(policy_num),
                            part.text or part_text,
                            extra_state=dict(callback_context.state.to_dict())
                            if hasattr(callback_context.state, "to_dict")
                            else {},
                        )
        return None

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any] | None:
        """Logs scrubbed tool invocations before execution."""
        record_metric_event(
            "tool.invocation.start",
            {
                "tool_name": getattr(tool, "name", str(tool)),
                "args": PIIScrubber.scrub_value(tool_args),
            },
        )
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Records tool completion telemetry."""
        status = result.get("status", "success") if isinstance(result, dict) else "success"
        record_metric_event(
            "tool.invocation.complete",
            {
                "tool_name": getattr(tool, "name", str(tool)),
                "status": status,
            },
        )
        return None
