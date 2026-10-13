"""Dynamic Agent Runtime discovery, strategic model routing, and runner-wide safety guardrails."""

from app.discovery.model_router import (
    ClaimPulseModelRouter,
    ClaimPulseSafetyGuardrailPlugin,
    RoutingDecision,
)
from app.discovery.registry_discovery import (
    AgentRegistryDiscoveryService,
    DiscoveredSpecialist,
    get_discovery_service,
)

__all__ = [
    "AgentRegistryDiscoveryService",
    "ClaimPulseModelRouter",
    "ClaimPulseSafetyGuardrailPlugin",
    "DiscoveredSpecialist",
    "RoutingDecision",
    "get_discovery_service",
]
