"""Sliding-window conversation history compactor for long multi-turn insurance sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.telemetry.scrubber import PIIScrubber
from app.telemetry.tracer import traced_operation


@dataclass
class CompactionResult:
    """Result returned after evaluating and compacting conversation history."""

    was_compacted: bool
    original_turn_count: int
    retained_turn_count: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    compacted_summary: str
    messages: list[dict[str, str]] = field(default_factory=list)


class HistoryCompactor:
    """Compacts older conversation turns into a deterministic, PII-scrubbed summary."""

    def __init__(
        self,
        max_turns: int = 8,
        retain_recent_turns: int = 4,
        token_threshold: int = 2000,
    ) -> None:
        self.max_turns = max_turns
        self.retain_recent_turns = retain_recent_turns
        self.token_threshold = token_threshold

    @staticmethod
    def estimate_tokens(messages: list[dict[str, Any]]) -> int:
        """Approximates token count (~4 characters per token)."""
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        return max(1, total_chars // 4)

    def compact(
        self,
        messages: list[dict[str, str]],
        pinned_facts: dict[str, str] | None = None,
    ) -> CompactionResult:
        """Compacts older turns if turn count or token count exceeds configured thresholds."""
        with traced_operation(
            "memory.history_compactor.compact",
            {"turn_count": len(messages), "max_turns": self.max_turns},
        ):
            tokens_before = self.estimate_tokens(messages)
            if (
                len(messages) <= self.max_turns
                and tokens_before <= self.token_threshold
            ):
                return CompactionResult(
                    was_compacted=False,
                    original_turn_count=len(messages),
                    retained_turn_count=len(messages),
                    estimated_tokens_before=tokens_before,
                    estimated_tokens_after=tokens_before,
                    compacted_summary="",
                    messages=list(messages),
                )

            cutoff = max(0, len(messages) - self.retain_recent_turns)
            older = messages[:cutoff]
            recent = messages[cutoff:]

            bullet_points: list[str] = []
            if pinned_facts:
                facts_str = ", ".join(
                    f"{k}={PIIScrubber.scrub_text(str(v))}"
                    for k, v in pinned_facts.items()
                )
                bullet_points.append(f"Pinned Policy Context: {facts_str}")

            for msg in older:
                role = msg.get("role", "user")
                snippet = PIIScrubber.scrub_text(msg.get("content", ""))[:120]
                bullet_points.append(f"[{role}] {snippet}")

            summary_text = (
                "COMPACTED_SESSION_HISTORY: " + " | ".join(bullet_points)
            )
            compacted_messages: list[dict[str, str]] = [
                {"role": "system", "content": summary_text},
                *recent,
            ]
            tokens_after = self.estimate_tokens(compacted_messages)
            return CompactionResult(
                was_compacted=True,
                original_turn_count=len(messages),
                retained_turn_count=len(compacted_messages),
                estimated_tokens_before=tokens_before,
                estimated_tokens_after=tokens_after,
                compacted_summary=summary_text,
                messages=compacted_messages,
            )
