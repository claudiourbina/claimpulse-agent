"""Dynamic Agent Runtime discovery, strategic LLM model swapping, HITL approval gates, and safety guardrails."""

from app.discovery.model_router import (
    FAST_MODEL,
    PRO_MODEL,
    ClaimPulseModelRouter,
    ClaimPulseSafetyGuardrailPlugin,
    HumanInTheLoopApprovalGate,
    RoutingDecision,
)
from app.discovery.registry_discovery import (
    AgentRegistryDiscoveryService,
    DiscoveredSpecialist,
    get_discovery_service,
)

__all__ = [
    "FAST_MODEL",
    "PRO_MODEL",
    "AgentRegistryDiscoveryService",
    "ClaimPulseModelRouter",
    "ClaimPulseSafetyGuardrailPlugin",
    "DiscoveredSpecialist",
    "HumanInTheLoopApprovalGate",
    "RoutingDecision",
    "get_discovery_service",
]
