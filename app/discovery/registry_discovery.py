"""Dynamic discovery of specialist Reasoning Engines deployed on Vertex AI Agent Runtime."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

from google.adk.agents.remote_a2a_agent import (
    AGENT_CARD_WELL_KNOWN_PATH,
    RemoteA2aAgent,
)

from app.telemetry.tracer import get_logger, traced_operation

logger = get_logger("claimpulse.discovery")


@dataclass
class DiscoveredSpecialist:
    """Metadata for a specialist agent discovered in Vertex AI Agent Runtime / Registry."""

    name: str
    role: str
    display_name: str
    description: str
    capabilities: list[str]
    reasoning_engine_id: str
    agent_card_url: str
    transport: str  # "remote_a2a_agent_runtime" or "local_in_process"
    status: str = "READY"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SPECIALIST_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "name": "claimpulse_intake_specialist",
        "role": "intake",
        "env_var": "CLAIM_PULSE_INTAKE_ENGINE_ID",
        "service_name": "claimpulse-intake-agent",
        "display_name": "ClaimPulse Policy & Identity Intake Specialist",
        "description": (
            "Verifies insurance policy status, coverage perils, deductibles, "
            "and records First Notice of Loss (FNOL) intake data."
        ),
        "capabilities": ["policy_verification", "coverage_lookup", "fnol_intake"],
    },
    {
        "name": "claimpulse_triage_specialist",
        "role": "triage",
        "env_var": "CLAIM_PULSE_TRIAGE_ENGINE_ID",
        "service_name": "claimpulse-triage-agent",
        "display_name": "ClaimPulse 3-Tier Severity & Reserve Triage Specialist",
        "description": (
            "Evaluates claim severity (1-10), drivable status, and injuries to classify "
            "claims into Tier 1 (Fast-Track), Tier 2 (Adjuster Inspection), or Tier 3 (Emergency)."
        ),
        "capabilities": [
            "severity_triage",
            "reserve_calculation",
            "deductible_analysis",
        ],
    },
    {
        "name": "claimpulse_dispatch_specialist",
        "role": "dispatch",
        "env_var": "CLAIM_PULSE_DISPATCH_ENGINE_ID",
        "service_name": "claimpulse-dispatch-agent",
        "display_name": "ClaimPulse Emergency Roadside & Adjuster Dispatch Specialist",
        "description": (
            "Dispatches emergency tow trucks, assigns certified workshops and accredited "
            "adjusters, and generates Google Calendar and Google Meet links."
        ),
        "capabilities": [
            "roadside_dispatch",
            "adjuster_scheduling",
            "workspace_calendar_meet",
        ],
    },
)


class AgentRegistryDiscoveryService:
    """Discovers remote specialist Reasoning Engines on Vertex AI Agent Runtime.

    In ``agent_runtime`` mode, queries Vertex AI Reasoning Engines in the active
    GCP project (or reads explicit ``CLAIM_PULSE_*_ENGINE_ID`` resource names),
    constructs each specialist's Agent Runtime ``/api/a2a/...`` Agent Card URL,
    and binds ADK ``RemoteA2aAgent`` proxies. Automatically falls back to local
    in-process ADK specialists when running offline or in unit tests.
    """

    def __init__(
        self,
        project_id: str | None = None,
        location: str | None = None,
        deployment_mode: str | None = None,
    ) -> None:
        self.project_id = project_id or os.environ.get(
            "GOOGLE_CLOUD_PROJECT", "local-dev-project"
        )
        self.location = (
            location
            or os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
            or "us-central1"
        )
        self.deployment_mode = (
            deployment_mode
            or os.environ.get("CLAIM_PULSE_DEPLOYMENT_MODE", "local")
        ).lower()

    @staticmethod
    def build_agent_runtime_card_url(
        reasoning_engine_resource: str,
        location: str = "us-central1",
        agent_dir: str = "app",
    ) -> str:
        """Builds the canonical Agent Runtime A2A Agent Card URL via `/api` passthrough."""
        return (
            f"https://{location}-aiplatform.googleapis.com/reasoningEngines/v1/"
            f"{reasoning_engine_resource}/api/a2a/{agent_dir}{AGENT_CARD_WELL_KNOWN_PATH}"
        )

    def _query_vertex_reasoning_engines(self) -> dict[str, str]:
        """Queries Vertex AI Reasoning Engines API to discover deployed specialists by display_name."""
        discovered_map: dict[str, str] = {}
        # 1. Check explicit environment variables first (populated by Terraform / deploy script)
        for spec in _SPECIALIST_CATALOG:
            if env_val := os.environ.get(spec["env_var"], "").strip():
                discovered_map[spec["role"]] = env_val

        if len(discovered_map) == len(_SPECIALIST_CATALOG):
            return discovered_map

        # 2. Query live Vertex AI ReasoningEngine API if running in agent_runtime mode
        if self.deployment_mode != "agent_runtime":
            return discovered_map

        try:
            import vertexai
            from vertexai.preview import reasoning_engines

            vertexai.init(project=self.project_id, location=self.location)
            engines = reasoning_engines.ReasoningEngine.list()
            for eng in engines:
                disp = getattr(eng, "display_name", "") or ""
                res_name = getattr(eng, "resource_name", "") or ""
                for spec in _SPECIALIST_CATALOG:
                    if (
                        spec["role"] not in discovered_map
                        and spec["service_name"] in disp.lower()
                        and res_name
                    ):
                        discovered_map[spec["role"]] = res_name
        except Exception as exc:
            logger.warning(
                f"Live ReasoningEngine discovery query fallback ({exc}); using configured state."
            )

        return discovered_map

    def discover_specialists(
        self, capability_filter: str = "all"
    ) -> list[DiscoveredSpecialist]:
        """Returns the list of discovered specialist agents and their A2A endpoints."""
        with traced_operation(
            "discovery.discover_specialists",
            {
                "deployment_mode": self.deployment_mode,
                "capability_filter": capability_filter,
            },
        ):
            engine_map = self._query_vertex_reasoning_engines()
            results: list[DiscoveredSpecialist] = []
            cap_lower = capability_filter.strip().lower()

            for spec in _SPECIALIST_CATALOG:
                if cap_lower not in ("all", "", "*"):
                    if (
                        cap_lower != spec["role"]
                        and not any(cap_lower in c for c in spec["capabilities"])
                    ):
                        continue

                role = spec["role"]
                engine_id = engine_map.get(role)
                if engine_id:
                    card_url = self.build_agent_runtime_card_url(
                        engine_id, location=self.location
                    )
                    transport = "remote_a2a_agent_runtime"
                else:
                    engine_id = (
                        f"projects/{self.project_id}/locations/{self.location}/"
                        f"reasoningEngines/{spec['service_name']}-local"
                    )
                    card_url = f"http://localhost:8000/a2a/app{AGENT_CARD_WELL_KNOWN_PATH}"
                    transport = "local_in_process"

                results.append(
                    DiscoveredSpecialist(
                        name=spec["name"],
                        role=role,
                        display_name=spec["display_name"],
                        description=spec["description"],
                        capabilities=list(spec["capabilities"]),
                        reasoning_engine_id=engine_id,
                        agent_card_url=card_url,
                        transport=transport,
                        status="READY",
                    )
                )

            return results

    def build_remote_a2a_sub_agents(self) -> list[RemoteA2aAgent]:
        """Creates ADK `RemoteA2aAgent` instances for specialists discovered on Agent Runtime."""
        remote_agents: list[RemoteA2aAgent] = []
        for item in self.discover_specialists():
            if item.transport == "remote_a2a_agent_runtime":
                remote_agents.append(
                    RemoteA2aAgent(
                        name=item.name,
                        description=item.description,
                        agent_card=item.agent_card_url,
                    )
                )
        return remote_agents


_DISCOVERY_SINGLETON: AgentRegistryDiscoveryService | None = None


def get_discovery_service() -> AgentRegistryDiscoveryService:
    """Returns the process-wide AgentRegistryDiscoveryService."""
    global _DISCOVERY_SINGLETON
    if _DISCOVERY_SINGLETON is None:
        _DISCOVERY_SINGLETON = AgentRegistryDiscoveryService()
    return _DISCOVERY_SINGLETON
