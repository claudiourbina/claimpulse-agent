"""Strategic LLM model router (Flash vs Pro swapping), Human-in-the-Loop (HITL) approval gates, and ADK safety plugin."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import Gemini
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

FAST_MODEL = os.environ.get("CLAIM_PULSE_FAST_MODEL", "gemini-3.8-flash")
PRO_MODEL = os.environ.get("CLAIM_PULSE_PRO_MODEL", "gemini-3.8-pro")

_PROMPT_INJECTION_PATTERNS = re.compile(
    r"(ignore\s+all\s+previous\s+instructions|system\s+override\s+mode|"
    r"drop\s+table\s+policies|<\s*script\b|reveal\s+system\s+prompt)",
    re.IGNORECASE,
)

_HIGH_COMPLEXITY_PATTERNS = re.compile(
    r"(múltiples\s+vehículos|multi-vehicle|responsabilidad\s+civil|subrogación|"
    r"pérdida\s+total|incendio|volcamiento|heridos|disputa\s+legal|catastrófico|"
    r"severidad\s*[89]|severidad\s*10|severity\s*[89]|severity\s*10)",
    re.IGNORECASE,
)


@dataclass
class RoutingDecision:
    """Represents a complexity-aware LLM model swap and specialist routing decision."""

    tier_complexity: str  # "FAST_PATH" or "DEEP_REASONING"
    selected_model: str   # Swaps between FAST_MODEL (gemini-3.8-flash) and PRO_MODEL (gemini-3.8-pro)
    target_specialist: str
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ClaimPulseModelRouter:
    """Dynamically selects and swaps between Gemini Flash (low latency) and Gemini Pro (deep reasoning)."""

    def __init__(
        self,
        fast_model: str = FAST_MODEL,
        pro_model: str = PRO_MODEL,
    ) -> None:
        self.fast_model = fast_model
        self.pro_model = pro_model

    def route(self, user_prompt: str) -> RoutingDecision:
        """Evaluates prompt complexity to swap between `self.fast_model` and `self.pro_model`."""
        with traced_operation("router.evaluate_prompt"):
            lower = user_prompt.lower()
            is_complex = (
                bool(_HIGH_COMPLEXITY_PATTERNS.search(user_prompt))
                or len(user_prompt) > 320
            )

            if any(
                kw in lower
                for kw in (
                    "grúa",
                    "tow",
                    "emergencia",
                    "humo",
                    "no enciende",
                    "agendar",
                    "perito",
                    "cita",
                    "calendar",
                )
            ):
                target = "claimpulse_dispatch_specialist"
            elif any(
                kw in lower
                for kw in (
                    "severidad",
                    "choque",
                    "rayón",
                    "abolladura",
                    "parabrisas",
                    "daño",
                    "siniestro",
                )
            ):
                target = "claimpulse_triage_specialist"
            else:
                target = "claimpulse_intake_specialist"

            if is_complex:
                return RoutingDecision(
                    tier_complexity="DEEP_REASONING",
                    selected_model=self.pro_model,
                    target_specialist=target,
                    rationale=(
                        f"High-severity or multi-party liability detected; swapped LLM to {self.pro_model}."
                    ),
                )
            return RoutingDecision(
                tier_complexity="FAST_PATH",
                selected_model=self.fast_model,
                target_specialist=target,
                rationale=(
                    f"Standard intake or low-complexity claim turn; routed to {self.fast_model}."
                ),
            )

    def create_llm_for_prompt(self, user_prompt: str) -> Gemini:
        """Instantiates a configured ADK `Gemini` model object for the routed complexity tier."""
        decision = self.route(user_prompt)
        return Gemini(
            model=decision.selected_model,
            retry_options=types.HttpRetryOptions(attempts=3),
        )


class HumanInTheLoopApprovalGate:
    """Enforces Human-in-the-Loop (HITL) confirmation hooks for high-stakes tool executions.

    High-stakes actions requiring explicit human confirmation when the HITL gate is armed:
    - Emergency roadside & ambulance dispatch (`dispatch_roadside_assistance` with CRITICAL hazard or ambulance)
    - High-value claim reserve settlements exceeding the configured USD threshold ($10,000)
    """

    HIGH_STAKES_TOOLS: frozenset[str] = frozenset(
        {
            "dispatch_roadside_assistance",
            "assess_claim_severity",
        }
    )

    def __init__(self, payout_threshold_usd: float = 10000.0) -> None:
        self.payout_threshold_usd = payout_threshold_usd

    @staticmethod
    def requires_confirmation_predicate(
        claim_id: str = "",
        location: str = "",
        hazard_level: str = "HIGH",
        requires_ambulance: bool = False,
        **kwargs: Any,
    ) -> bool:
        """Predicate used by `FunctionTool(..., require_confirmation=...)` for ADK HITL."""
        enforce_env = os.environ.get(
            "CLAIM_PULSE_ENFORCE_HITL_GATE", "false"
        ).lower() in ("true", "1")
        if not enforce_env:
            return False
        return (
            hazard_level.strip().upper() == "CRITICAL"
            or bool(requires_ambulance)
        )

    def evaluate_tool_call(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_context: ToolContext | None,
    ) -> dict[str, Any] | None:
        """Checks if a high-stakes tool invocation must pause for human confirmation.

        Returns `None` if approved to proceed, or a structured `PENDING_HUMAN_APPROVAL`
        dictionary if human-in-the-loop confirmation is required first.
        """
        state = (
            tool_context.state
            if (tool_context is not None and getattr(tool_context, "state", None) is not None)
            else {}
        )
        gate_armed = bool(state.get("require_hitl_approval", False)) or (
            os.environ.get("CLAIM_PULSE_ENFORCE_HITL_GATE", "false").lower()
            in ("true", "1")
        )
        if not gate_armed:
            return None

        # Check if human supervisor already approved this action in session state
        if state.get("hitl_approved") is True:
            return None

        if tool_name == "dispatch_roadside_assistance":
            hazard = str(tool_args.get("hazard_level", "")).upper()
            ambulance = bool(tool_args.get("requires_ambulance", False))
            if hazard in ("CRITICAL", "HIGH") or ambulance:
                action_id = f"HITL-DSP-{tool_args.get('claim_id', 'UNKNOWN')}"
                state["pending_hitl_action"] = {
                    "action_id": action_id,
                    "tool_name": tool_name,
                    "tool_args": PIIScrubber.scrub_value(tool_args),
                }
                if tool_context is not None and hasattr(
                    tool_context, "request_confirmation"
                ):
                    try:
                        tool_context.request_confirmation(
                            hint=(
                                f"Confirm high-stakes emergency roadside/ambulance dispatch "
                                f"({action_id}) at {tool_args.get('location')}?"
                            )
                        )
                    except Exception:
                        pass
                record_metric_event(
                    "hitl.confirmation_requested",
                    {"action_id": action_id, "tool_name": tool_name},
                )
                return {
                    "status": "pending_human_approval",
                    "hitl_confirmation_required": True,
                    "action_id": action_id,
                    "tool_name": tool_name,
                    "message": (
                        f"Human-in-the-Loop confirmation required before executing high-stakes "
                        f"emergency dispatch ({action_id})."
                    ),
                    "recovery_guidance": (
                        "Ask the human operator/policyholder to confirm emergency dispatch "
                        "and call `confirm_human_in_the_loop_action(action_id, approved=True, supervisor_notes=...)`."
                    ),
                }

        if tool_name == "assess_claim_severity":
            severity = int(tool_args.get("severity_score", 1))
            if severity >= 9 and state.get("require_hitl_for_catastrophic_reserve"):
                action_id = f"HITL-RSV-{tool_args.get('policy_number', 'UNKNOWN')}"
                state["pending_hitl_action"] = {
                    "action_id": action_id,
                    "tool_name": tool_name,
                    "tool_args": PIIScrubber.scrub_value(tool_args),
                }
                return {
                    "status": "pending_human_approval",
                    "hitl_confirmation_required": True,
                    "action_id": action_id,
                    "tool_name": tool_name,
                    "message": (
                        f"Catastrophic reserve (severity={severity}/10) requires Senior Claims "
                        f"Supervisor HITL sign-off ({action_id})."
                    ),
                    "recovery_guidance": (
                        "Call `confirm_human_in_the_loop_action` after obtaining human supervisor sign-off."
                    ),
                }

        return None


class ClaimPulseSafetyGuardrailPlugin(BasePlugin):
    """Runner-wide ADK plugin enforcing safety guardrails, dynamic LLM model swapping, HITL hooks, and memory compaction."""

    def __init__(
        self,
        fast_model: str = FAST_MODEL,
        pro_model: str = PRO_MODEL,
    ) -> None:
        super().__init__(name="claimpulse_safety_guardrail")
        self.router = ClaimPulseModelRouter(
            fast_model=fast_model, pro_model=pro_model
        )
        self.hitl_gate = HumanInTheLoopApprovalGate()
        self.compactor = HistoryCompactor(max_turns=8, retain_recent_turns=4)
        self.consolidator = AsyncMemoryConsolidator()

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        """Inspects incoming prompt for injection attacks, dynamically swaps LLM model, and compacts history."""
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
            callback_context.state["active_llm_model"] = decision.selected_model
            # Explicitly swap the LLM model on llm_request when dynamic model routing is active
            if hasattr(llm_request, "model"):
                llm_request.model = decision.selected_model
            record_metric_event(
                "router.model_swapped",
                {
                    "tier_complexity": decision.tier_complexity,
                    "selected_model": decision.selected_model,
                    "target_specialist": decision.target_specialist,
                },
            )

        if len(turn_messages) > self.compactor.max_turns:
            compacted = self.compactor.compact(
                turn_messages,
                pinned_facts={
                    k: str(callback_context.state.get(k))
                    for k in (
                        "verified_policy_number",
                        "active_claim_id",
                        "last_triage_tier",
                    )
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
        """Logs scrubbed tool invocations and enforces Human-in-the-Loop (HITL) approval gates."""
        tool_name = getattr(tool, "name", str(tool))
        record_metric_event(
            "tool.invocation.start",
            {
                "tool_name": tool_name,
                "args": PIIScrubber.scrub_value(tool_args),
            },
        )
        hitl_intercept = self.hitl_gate.evaluate_tool_call(
            tool_name=tool_name,
            tool_args=tool_args,
            tool_context=tool_context,
        )
        if hitl_intercept is not None:
            return hitl_intercept
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
        status = (
            result.get("status", "success") if isinstance(result, dict) else "success"
        )
        record_metric_event(
            "tool.invocation.complete",
            {
                "tool_name": getattr(tool, "name", str(tool)),
                "status": status,
            },
        )
        return None
