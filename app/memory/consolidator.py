"""Asynchronous and synchronous memory consolidator for policyholder & claim context."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.storage.repository import ClaimPulseRepository, get_repository
from app.telemetry.scrubber import PIIScrubber
from app.telemetry.tracer import get_logger, traced_operation

logger = get_logger("claimpulse.memory.consolidator")

_POLICY_REGEX = re.compile(r"\b(POL-[A-Z]{2}-\d{4})\b", re.IGNORECASE)
_CLAIM_REGEX = re.compile(r"\b(CLM-\d{6,10})\b", re.IGNORECASE)


class AsyncMemoryConsolidator:
    """Extracts structured facts from conversation turns and persists them to Firestore/SQLite."""

    def __init__(self, repository: ClaimPulseRepository | None = None) -> None:
        self._repo = repository
        self._pending_tasks: set[asyncio.Task[dict[str, str]]] = set()

    @property
    def repo(self) -> ClaimPulseRepository:
        return self._repo or get_repository()

    def extract_facts(
        self, text: str, extra_state: dict[str, Any] | None = None
    ) -> dict[str, str]:
        """Extracts scrubbed insurance domain facts from a user or agent message."""
        facts: dict[str, str] = {}
        if match := _POLICY_REGEX.search(text):
            facts["policy_number"] = match.group(1).upper()
        if match := _CLAIM_REGEX.search(text):
            facts["last_claim_id"] = match.group(1).upper()

        lower = text.lower()
        if "virtual" in lower or "meet" in lower or "videollamada" in lower:
            facts["preferred_modality"] = "Virtual Google Meet"
        elif "presencial" in lower or "in-person" in lower or "taller" in lower:
            facts["preferred_modality"] = "In-Person Workshop"

        if "parabrisas" in lower or "windshield" in lower or "vidrio" in lower:
            facts["damage_category"] = "glass"
        elif "parachoques" in lower or "bumper" in lower or "rayón" in lower:
            facts["damage_category"] = "cosmetic"
        elif "autopista" in lower or "ruta" in lower or "humo" in lower:
            facts["damage_category"] = "highway_emergency"

        if extra_state:
            for key in (
                "verified_policy_number",
                "active_claim_id",
                "last_triage_tier",
                "holder_name",
                "vehicle_make_model",
            ):
                if val := extra_state.get(key):
                    facts[key] = PIIScrubber.scrub_text(str(val))

        return {k: PIIScrubber.scrub_text(v) for k, v in facts.items()}

    def consolidate_sync(
        self,
        policy_number: str,
        utterance: str,
        extra_state: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Synchronously extracts and persists facts for a policyholder."""
        with traced_operation(
            "memory.consolidator.consolidate_sync",
            {"policy_number": policy_number},
        ):
            normalized_policy = policy_number.strip().upper()
            extracted = self.extract_facts(utterance, extra_state=extra_state)
            for k, v in extracted.items():
                self.repo.save_memory_fact(normalized_policy, k, v)
            return self.repo.get_memory_facts(normalized_policy)

    async def consolidate_async(
        self,
        policy_number: str,
        utterance: str,
        extra_state: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Runs memory consolidation asynchronously without blocking the main agent loop."""
        return await asyncio.to_thread(
            self.consolidate_sync, policy_number, utterance, extra_state
        )

    def schedule_background_consolidation(
        self,
        policy_number: str,
        utterance: str,
        extra_state: dict[str, Any] | None = None,
    ) -> asyncio.Task[dict[str, str]] | None:
        """Schedules a non-blocking background task if an event loop is running."""
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(
                self.consolidate_async(policy_number, utterance, extra_state)
            )
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
            return task
        except RuntimeError:
            self.consolidate_sync(policy_number, utterance, extra_state)
            return None
