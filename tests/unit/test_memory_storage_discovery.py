"""Unit tests for Firestore/SQLite repositories, memory compaction, dynamic discovery, and multi-role agents."""

from __future__ import annotations

from typing import Any

from app.agent import build_root_agent_for_role
from app.discovery.model_router import (
    ClaimPulseModelRouter,
    ClaimPulseSafetyGuardrailPlugin,
)
from app.discovery.registry_discovery import AgentRegistryDiscoveryService
from app.memory.compactor import HistoryCompactor
from app.memory.consolidator import AsyncMemoryConsolidator
from app.storage.repository import FirestoreRepository, SQLiteRepository


class FakeFirestoreDocSnapshot:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return self._data


class FakeFirestoreDocumentRef:
    def __init__(self, store: dict[str, dict[str, Any]], doc_id: str) -> None:
        self._store = store
        self._doc_id = doc_id

    def set(self, data: dict[str, Any]) -> None:
        self._store[self._doc_id] = dict(data)

    def get(self) -> FakeFirestoreDocSnapshot:
        return FakeFirestoreDocSnapshot(self._store.get(self._doc_id))


class FakeFirestoreCollection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def document(self, doc_id: str) -> FakeFirestoreDocumentRef:
        return FakeFirestoreDocumentRef(self._store, doc_id)

    def stream(self) -> list[FakeFirestoreDocSnapshot]:
        return [FakeFirestoreDocSnapshot(v) for v in self._store.values()]

    def where(self, field: str, op: str, value: Any) -> FakeFirestoreCollection:
        filtered = {
            k: v for k, v in self._store.items() if op == "==" and v.get(field) == value
        }
        return FakeFirestoreCollection(filtered)


class FakeFirestoreClient:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> FakeFirestoreCollection:
        if name not in self.collections:
            self.collections[name] = {}
        return FakeFirestoreCollection(self.collections[name])


def test_firestore_repository_seeds_and_queries_records() -> None:
    fake_client = FakeFirestoreClient()
    repo = FirestoreRepository(project_id="test-proj", client=fake_client)

    policy = repo.get_policy("POL-CL-1001")
    assert policy is not None
    assert policy.holder_name == "Camila Valdés Rojas"

    adjusters = repo.find_adjusters("Collision")
    assert len(adjusters) >= 1

    unit = repo.find_roadside_unit("Ruta 5 Sur km 40")
    assert unit.unit_id == "TOW-R5-101"

    repo.save_memory_fact("POL-CL-1001", "contact", "Email me at user@test.com")
    facts = repo.get_memory_facts("POL-CL-1001")
    assert facts["contact"] == "Email me at [REDACTED_EMAIL]"


def test_history_compactor_compacts_long_sessions_and_retains_recent_turns() -> None:
    compactor = HistoryCompactor(max_turns=4, retain_recent_turns=2, token_threshold=100)
    messages = [
        {"role": "user", "content": "Hola, mi RUT es 12.345.678-9 y tengo póliza POL-CL-1001"},
        {"role": "model", "content": "Póliza verificada para Toyota RAV4."},
        {"role": "user", "content": "Tuve un rayón en el parachoques trasero ayer."},
        {"role": "model", "content": "Clasificado como Tier 1 Fast-Track."},
        {"role": "user", "content": "¿Cuál es el taller rápido más cercano?"},
        {"role": "model", "content": "ClaimPulse Certified Center en Las Condes."},
    ]
    res = compactor.compact(
        messages, pinned_facts={"verified_policy_number": "POL-CL-1001"}
    )
    assert res.was_compacted is True
    assert res.retained_turn_count == 3  # 1 system summary + 2 recent turns
    assert "COMPACTED_SESSION_HISTORY" in res.compacted_summary
    assert "POL-CL-1001" in res.compacted_summary
    assert "12.345.678-9" not in res.compacted_summary
    assert "[REDACTED_RUT]" in res.compacted_summary


def test_async_memory_consolidator_extracts_policy_and_modality() -> None:
    repo = SQLiteRepository(":memory:")
    consolidator = AsyncMemoryConsolidator(repository=repo)
    facts = consolidator.consolidate_sync(
        "POL-CL-1002",
        "Tengo la póliza POL-CL-1002, se rompió el parabrisas y prefiero inspección virtual por Meet.",
        extra_state={"active_claim_id": "CLM-ABCDEF"},
    )
    assert facts["policy_number"] == "POL-CL-1002"
    assert facts["preferred_modality"] == "Virtual Google Meet"
    assert facts["damage_category"] == "glass"
    assert facts["active_claim_id"] == "CLM-ABCDEF"


def test_agent_registry_discovery_service_local_and_agent_runtime_modes(
    monkeypatch: Any,
) -> None:
    # Local mode
    local_svc = AgentRegistryDiscoveryService(
        project_id="demo-proj", location="us-central1", deployment_mode="local"
    )
    local_specs = local_svc.discover_specialists("all")
    assert len(local_specs) == 3
    assert all(s.transport == "local_in_process" for s in local_specs)

    # Remote Agent Runtime mode with engine IDs configured
    monkeypatch.setenv(
        "CLAIM_PULSE_INTAKE_ENGINE_ID",
        "projects/123/locations/us-central1/reasoningEngines/111",
    )
    monkeypatch.setenv(
        "CLAIM_PULSE_TRIAGE_ENGINE_ID",
        "projects/123/locations/us-central1/reasoningEngines/222",
    )
    monkeypatch.setenv(
        "CLAIM_PULSE_DISPATCH_ENGINE_ID",
        "projects/123/locations/us-central1/reasoningEngines/333",
    )
    remote_svc = AgentRegistryDiscoveryService(
        project_id="demo-proj", location="us-central1", deployment_mode="agent_runtime"
    )
    remote_specs = remote_svc.discover_specialists("all")
    assert len(remote_specs) == 3
    assert all(s.transport == "remote_a2a_agent_runtime" for s in remote_specs)
    assert (
        "reasoningEngines/v1/projects/123/locations/us-central1/reasoningEngines/111/api/a2a/app/.well-known/agent-card.json"
        in remote_specs[0].agent_card_url
    )
    remote_a2a_agents = remote_svc.build_remote_a2a_sub_agents()
    assert len(remote_a2a_agents) == 3


def test_model_router_and_role_factory() -> None:
    router = ClaimPulseModelRouter()
    fast_decision = router.route("Hola, quiero consultar mi póliza POL-CL-1001")
    assert fast_decision.tier_complexity == "FAST_PATH"
    assert fast_decision.target_specialist == "claimpulse_intake_specialist"

    complex_decision = router.route(
        "Sufrí un volcamiento con múltiples vehículos y heridos en la autopista, hay humo y necesito grúa de emergencia"
    )
    assert complex_decision.tier_complexity == "DEEP_REASONING"
    assert complex_decision.target_specialist == "claimpulse_dispatch_specialist"

    # Verify all 4 Agent Runtime role entrypoints build cleanly
    for role in ("supervisor", "intake", "triage", "dispatch"):
        agent = build_root_agent_for_role(role)
        assert agent.name == "claimpulse_agent"

    plugin = ClaimPulseSafetyGuardrailPlugin()
    assert plugin.name == "claimpulse_safety_guardrail"
