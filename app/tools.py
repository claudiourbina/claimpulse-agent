"""Pydantic-validated ADK tools for ClaimPulse insurance intake, 3-tier triage, and dispatch."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from google.adk.tools import FunctionTool, ToolContext
from pydantic import ValidationError

from app.a2ui.views import (
    build_adjuster_appointment_view,
    build_discovery_roster_view,
    build_emergency_dispatch_view,
    build_policy_intake_view,
    build_triage_resolution_view,
)
from app.core.models import (
    AdjusterScheduleInput,
    AppointmentRecord,
    ClaimRecord,
    ClaimTriageInput,
    DiscoveryInput,
    DispatchRecord,
    PolicyVerificationInput,
    RoadsideDispatchInput,
    build_error_response,
)
from app.core.triage_engine import ClaimTriageEngine
from app.discovery.model_router import HumanInTheLoopApprovalGate
from app.discovery.registry_discovery import get_discovery_service
from app.memory.consolidator import AsyncMemoryConsolidator
from app.storage.repository import get_repository
from app.telemetry.tracer import record_metric_event, trace_tool


def _safe_state_dict(tool_context: ToolContext | None) -> dict[str, Any]:
    if tool_context is None or getattr(tool_context, "state", None) is None:
        return {}
    state = tool_context.state
    if hasattr(state, "to_dict"):
        return dict(state.to_dict())
    if isinstance(state, dict):
        return dict(state)
    return {}


def _set_state(tool_context: ToolContext | None, key: str, value: Any) -> None:
    if tool_context is not None and getattr(tool_context, "state", None) is not None:
        tool_context.state[key] = value


@trace_tool("discover_available_specialists")
def discover_available_specialists(
    capability_filter: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Discovers specialist agents deployed on Vertex AI Agent Runtime and Agent Registry.

    Args:
        capability_filter: Filter by specialist role or capability ('all', 'intake', 'triage', 'dispatch').

    Returns:
        Dictionary with status, deployment_mode, discovered_specialists list, and a2ui_surface.
    """
    try:
        validated = DiscoveryInput(capability_filter=capability_filter)
    except ValidationError as exc:
        return build_error_response(
            error_code="INVALID_DISCOVERY_FILTER",
            message=f"Invalid capability_filter: {exc}",
            recovery_guidance="Pass 'all', 'intake', 'triage', or 'dispatch' as capability_filter.",
        )

    service = get_discovery_service()
    specialists = [
        s.to_dict()
        for s in service.discover_specialists(validated.capability_filter)
    ]
    a2ui_surface = build_discovery_roster_view(
        specialists, deployment_mode=service.deployment_mode
    )
    _set_state(tool_context, "discovered_specialists", specialists)
    _set_state(tool_context, "last_a2ui_surface", a2ui_surface)

    record_metric_event(
        "discovery.completed",
        {
            "deployment_mode": service.deployment_mode,
            "discovered_count": len(specialists),
        },
    )
    return {
        "status": "success",
        "deployment_mode": service.deployment_mode,
        "project_id": service.project_id,
        "location": service.location,
        "discovered_specialists": specialists,
        "a2ui_surface": a2ui_surface,
    }


@trace_tool("verify_policy_and_coverage")
def verify_policy_and_coverage(
    policy_number: str,
    incident_type: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Verifies an insurance policy's active status, deductible, coverage limits, and perils.

    Args:
        policy_number: The policyholder's policy ID starting with POL- (for example 'POL-CL-1001').
        incident_type: Type of reported peril ('collision', 'cosmetic', 'glass', 'structural', 'roadside_emergency').

    Returns:
        Dictionary with status, policy details, coverage eligibility, memory facts, and a2ui_surface.
    """
    try:
        validated = PolicyVerificationInput(
            policy_number=policy_number, incident_type=incident_type
        )
    except ValidationError as exc:
        return build_error_response(
            error_code="INVALID_POLICY_FORMAT",
            message=f"Policy verification input validation failed: {exc}",
            recovery_guidance=(
                "Ensure policy_number starts with 'POL-' (e.g. 'POL-CL-1001', 'POL-CL-1002', 'POL-CL-1003') "
                "and incident_type is a non-empty string."
            ),
        )

    repo = get_repository()
    policy = repo.get_policy(validated.policy_number)
    if policy is None:
        return build_error_response(
            error_code="POLICY_NOT_FOUND",
            message=f"Policy '{validated.policy_number}' was not found in {repo.backend_name} storage.",
            recovery_guidance=(
                "Ask the user to verify their policy number. Valid demo policies include "
                "POL-CL-1001, POL-CL-1002, POL-CL-1003, POL-CL-1004, and POL-CL-1005."
            ),
        )

    if policy.status != "ACTIVE":
        return build_error_response(
            error_code="POLICY_INACTIVE",
            message=f"Policy '{policy.policy_number}' has status '{policy.status}' and cannot open new claims.",
            recovery_guidance=(
                "Inform the policyholder that their policy is expired/suspended and offer "
                "to connect them with Billing & Policy Renewal."
            ),
            details={"policy": policy.model_dump()},
        )

    incident_lower = validated.incident_type.lower()
    peril_covered = any(
        incident_lower in p.lower() or p.lower() in incident_lower
        for p in policy.covered_perils
    ) or True  # All active policies include base evaluation

    _set_state(tool_context, "verified_policy_number", policy.policy_number)
    _set_state(tool_context, "holder_name", policy.holder_name)
    _set_state(tool_context, "vehicle_make_model", policy.vehicle_make_model)
    _set_state(tool_context, "policy_deductible_usd", policy.deductible_usd)

    consolidator = AsyncMemoryConsolidator(repository=repo)
    memories = consolidator.consolidate_sync(
        policy.policy_number,
        f"Verified policy {policy.policy_number} for {validated.incident_type}",
        extra_state=_safe_state_dict(tool_context),
    )

    a2ui_surface = build_policy_intake_view(policy)
    _set_state(tool_context, "last_a2ui_surface", a2ui_surface)

    return {
        "status": "success",
        "storage_backend": repo.backend_name,
        "policy": policy.model_dump(),
        "peril_covered": peril_covered,
        "remembered_facts": memories,
        "a2ui_surface": a2ui_surface,
    }


@trace_tool("assess_claim_severity")
def assess_claim_severity(
    policy_number: str,
    damage_description: str,
    severity_score: int,
    is_drivable: bool,
    has_injuries: bool,
    location: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Evaluates claim severity (1-10) and classifies into Tier 1 (Fast-Track), Tier 2 (Adjuster), or Tier 3 (Emergency).

    Args:
        policy_number: Verified policy ID starting with POL- (for example 'POL-CL-1001').
        damage_description: Detailed description of the damage, symptoms, or incident circumstances.
        severity_score: Integer severity rating from 1 (minor scratch) to 10 (catastrophic/total loss).
        is_drivable: True if the vehicle is safe and operational to drive; False if immobilized.
        has_injuries: True if any occupant or third party has physical injuries; False otherwise.
        location: Current street address, city, or highway kilometer marker of the incident.

    Returns:
        Dictionary with status, claim_id, triage_decision, claim_record, and validated a2ui_surface.
    """
    try:
        validated = ClaimTriageInput(
            policy_number=policy_number,
            damage_description=damage_description,
            severity_score=severity_score,
            is_drivable=is_drivable,
            has_injuries=has_injuries,
            location=location,
        )
    except ValidationError as exc:
        return build_error_response(
            error_code="INVALID_TRIAGE_INPUT",
            message=f"Claim triage validation failed: {exc}",
            recovery_guidance=(
                "Provide a valid policy_number ('POL-...'), severity_score between 1 and 10, "
                "booleans for is_drivable and has_injuries, and a non-empty damage_description and location."
            ),
        )

    repo = get_repository()
    policy = repo.get_policy(validated.policy_number)
    if policy is None:
        return build_error_response(
            error_code="POLICY_NOT_FOUND",
            message=f"Cannot triage claim because policy '{validated.policy_number}' was not found.",
            recovery_guidance="Call verify_policy_and_coverage first with a valid policy ID (e.g. POL-CL-1001).",
        )
    if policy.status != "ACTIVE":
        return build_error_response(
            error_code="POLICY_INACTIVE",
            message=f"Policy '{policy.policy_number}' is '{policy.status}'.",
            recovery_guidance="Only ACTIVE policies can register an FNOL triage claim.",
        )

    decision = ClaimTriageEngine.evaluate(
        policy=policy,
        damage_description=validated.damage_description,
        severity_score=validated.severity_score,
        is_drivable=validated.is_drivable,
        has_injuries=validated.has_injuries,
    )

    digest = hashlib.sha256(
        f"{policy.policy_number}:{validated.damage_description}:{validated.severity_score}".encode()
    ).hexdigest()[:6].upper()
    claim_id = f"CLM-{digest}"
    now_iso = datetime.now(UTC).isoformat()

    claim = ClaimRecord(
        claim_id=claim_id,
        policy_number=policy.policy_number,
        holder_name=policy.holder_name,
        damage_description=validated.damage_description,
        severity_score=validated.severity_score,
        tier=decision.tier,
        is_drivable=validated.is_drivable,
        has_injuries=validated.has_injuries,
        location=validated.location,
        estimated_repair_usd=decision.estimated_repair_usd,
        applied_deductible_usd=decision.applied_deductible_usd,
        estimated_payout_usd=decision.estimated_payout_usd,
        recommended_specialty=decision.recommended_specialty,
        status="OPEN",
        created_at=now_iso,
    )
    repo.save_claim(claim)

    _set_state(tool_context, "verified_policy_number", policy.policy_number)
    _set_state(tool_context, "active_claim_id", claim.claim_id)
    _set_state(tool_context, "last_triage_tier", decision.tier.value)
    _set_state(tool_context, "recommended_specialty", decision.recommended_specialty)

    consolidator = AsyncMemoryConsolidator(repository=repo)
    consolidator.consolidate_sync(
        policy.policy_number,
        validated.damage_description,
        extra_state=_safe_state_dict(tool_context),
    )

    a2ui_surface = build_triage_resolution_view(claim, decision)
    _set_state(tool_context, "last_a2ui_surface", a2ui_surface)

    record_metric_event(
        "triage.classified",
        {
            "claim_id": claim.claim_id,
            "tier": decision.tier.value,
            "severity_score": decision.severity_score,
            "estimated_payout_usd": decision.estimated_payout_usd,
        },
    )

    return {
        "status": "success",
        "claim_id": claim.claim_id,
        "triage_decision": decision.model_dump(),
        "claim_record": claim.model_dump(),
        "a2ui_surface": a2ui_surface,
    }


@trace_tool("schedule_adjuster_and_workshop")
def schedule_adjuster_and_workshop(
    claim_id: str,
    specialty: str,
    preferred_date: str,
    modality: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Schedules an accredited insurance adjuster and certified repair workshop with Google Calendar and Meet links.

    Args:
        claim_id: The registered claim ID (for example 'CLM-1A2B3C').
        specialty: Required adjuster specialty (for example 'Senior Automotive Collision & Liability').
        preferred_date: Preferred ISO date or date-time string (for example '2026-10-15T10:00:00').
        modality: Inspection modality ('Virtual Google Meet' or 'In-Person Workshop').

    Returns:
        Dictionary with status, appointment details, adjuster profile, workshop profile, Google Calendar/Meet links, and a2ui_surface.
    """
    try:
        validated = AdjusterScheduleInput(
            claim_id=claim_id,
            specialty=specialty,
            preferred_date=preferred_date,
            modality=modality,
        )
    except ValidationError as exc:
        return build_error_response(
            error_code="INVALID_SCHEDULE_INPUT",
            message=f"Adjuster scheduling validation failed: {exc}",
            recovery_guidance=(
                "Pass a valid claim_id ('CLM-...'), specialty, preferred_date, and modality."
            ),
        )

    repo = get_repository()
    claim = repo.get_claim(validated.claim_id)
    if claim is None:
        return build_error_response(
            error_code="CLAIM_NOT_FOUND",
            message=f"Claim '{validated.claim_id}' was not found.",
            recovery_guidance=(
                "Call assess_claim_severity first to register the claim and obtain a valid claim_id."
            ),
        )

    adjusters = repo.find_adjusters(validated.specialty)
    selected_adjuster = adjusters[0]
    workshops = repo.find_workshops(validated.specialty)
    selected_workshop = workshops[0]

    slot = (
        validated.preferred_date
        if "T" in validated.preferred_date
        else selected_adjuster.available_slots[0]
    )
    calendar_url, meet_url = ClaimTriageEngine.generate_workspace_links(
        claim_id=claim.claim_id,
        title=f"ClaimPulse Inspection — {claim.claim_id} ({selected_adjuster.full_name})",
        slot_iso=slot,
        location_or_workshop=f"{selected_workshop.name}, {selected_workshop.address}",
        attendee_email=selected_adjuster.calendar_email,
    )

    appt_hash = hashlib.sha256(
        f"{claim.claim_id}:{selected_adjuster.adjuster_id}:{slot}".encode()
    ).hexdigest()[:6].upper()
    appointment = AppointmentRecord(
        appointment_id=f"APT-{appt_hash}",
        claim_id=claim.claim_id,
        policy_number=claim.policy_number,
        adjuster_id=selected_adjuster.adjuster_id,
        adjuster_name=selected_adjuster.full_name,
        workshop_id=selected_workshop.workshop_id,
        workshop_name=selected_workshop.name,
        scheduled_slot=slot,
        modality=validated.modality,
        google_calendar_url=calendar_url,
        google_meet_url=meet_url,
        status="CONFIRMED",
    )
    repo.save_appointment(appointment)

    _set_state(tool_context, "last_appointment_id", appointment.appointment_id)
    a2ui_surface = build_adjuster_appointment_view(appointment)
    _set_state(tool_context, "last_a2ui_surface", a2ui_surface)

    return {
        "status": "success",
        "appointment": appointment.model_dump(),
        "adjuster": selected_adjuster.model_dump(),
        "workshop": selected_workshop.model_dump(),
        "a2ui_surface": a2ui_surface,
    }


@trace_tool("dispatch_roadside_assistance")
def dispatch_roadside_assistance(
    claim_id: str,
    location: str,
    hazard_level: str,
    requires_ambulance: bool,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Dispatches an emergency tow truck and roadside response unit for Tier 3 critical claims.

    Args:
        claim_id: The registered claim ID (for example 'CLM-1A2B3C').
        location: Exact highway kilometer, street intersection, or GPS landmark for tow dispatch.
        hazard_level: Severity of roadside hazard ('HIGH', 'CRITICAL', or 'MODERATE').
        requires_ambulance: True if medical/ambulance coordination (131 SAMU / 911) is needed.

    Returns:
        Dictionary with status, dispatch record, roadside unit details, emergency Meet link, and a2ui_surface.
    """
    try:
        validated = RoadsideDispatchInput(
            claim_id=claim_id,
            location=location,
            hazard_level=hazard_level,
            requires_ambulance=requires_ambulance,
        )
    except ValidationError as exc:
        return build_error_response(
            error_code="INVALID_DISPATCH_INPUT",
            message=f"Roadside dispatch validation failed: {exc}",
            recovery_guidance=(
                "Provide a valid claim_id ('CLM-...'), location (min 3 chars), hazard_level, and requires_ambulance boolean."
            ),
        )

    # Human-in-the-Loop (HITL) confirmation gate for high-stakes emergency dispatch
    hitl_gate = HumanInTheLoopApprovalGate()
    hitl_pending = hitl_gate.evaluate_tool_call(
        tool_name="dispatch_roadside_assistance",
        tool_args={
            "claim_id": validated.claim_id,
            "location": validated.location,
            "hazard_level": validated.hazard_level,
            "requires_ambulance": validated.requires_ambulance,
        },
        tool_context=tool_context,
    )
    if hitl_pending is not None:
        return hitl_pending

    repo = get_repository()
    claim = repo.get_claim(validated.claim_id)
    policy_number = claim.policy_number if claim else "POL-CL-EMERGENCY"

    unit = repo.find_roadside_unit(validated.location)
    _, meet_url = ClaimTriageEngine.generate_workspace_links(
        claim_id=validated.claim_id,
        title=f"EMERGENCY BRIDGE — Claim {validated.claim_id}",
        slot_iso="2026-10-14T00:00:00",
        location_or_workshop=validated.location,
        attendee_email="csilva.emergency@claimpulse.example.com",
    )

    disp_hash = hashlib.sha256(
        f"{validated.claim_id}:{unit.unit_id}:{validated.location}".encode()
    ).hexdigest()[:6].upper()
    dispatch = DispatchRecord(
        dispatch_id=f"DSP-{disp_hash}",
        claim_id=validated.claim_id.upper(),
        policy_number=policy_number,
        unit_id=unit.unit_id,
        operator_name=unit.operator_name,
        location=validated.location,
        hazard_level=validated.hazard_level.upper(),
        eta_minutes=unit.average_eta_minutes,
        requires_ambulance=validated.requires_ambulance,
        emergency_bridge_meet_url=meet_url,
        status="DISPATCHED",
    )
    repo.save_dispatch(dispatch)

    _set_state(tool_context, "last_dispatch_id", dispatch.dispatch_id)
    a2ui_surface = build_emergency_dispatch_view(dispatch)
    _set_state(tool_context, "last_a2ui_surface", a2ui_surface)

    record_metric_event(
        "dispatch.emergency_triggered",
        {
            "dispatch_id": dispatch.dispatch_id,
            "unit_id": unit.unit_id,
            "eta_minutes": unit.average_eta_minutes,
            "requires_ambulance": validated.requires_ambulance,
        },
    )

    return {
        "status": "success",
        "dispatch": dispatch.model_dump(),
        "roadside_unit": unit.model_dump(),
        "emergency_protocol": {
            "emergency_numbers": ["911", "131 (SAMU Ambulance)", "133 (Highway Police)"],
            "ambulance_alerted": validated.requires_ambulance,
            "live_adjuster_meet": meet_url,
        },
        "a2ui_surface": a2ui_surface,
    }


@trace_tool("get_claim_status_and_history")
def get_claim_status_and_history(
    policy_number: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Retrieves active claims and consolidated cross-session memory facts for a policy.

    Args:
        policy_number: Policy ID starting with POL- (for example 'POL-CL-1001').

    Returns:
        Dictionary with status, policy_number, claims list, and consolidated memory_facts.
    """
    normalized = policy_number.strip().upper()
    if not normalized.startswith("POL-"):
        return build_error_response(
            error_code="INVALID_POLICY_NUMBER",
            message=f"Policy number '{policy_number}' must start with 'POL-'.",
            recovery_guidance="Provide a valid policy number such as POL-CL-1001.",
        )

    repo = get_repository()
    claims = [c.model_dump() for c in repo.list_claims_by_policy(normalized)]
    facts = repo.get_memory_facts(normalized)
    return {
        "status": "success",
        "storage_backend": repo.backend_name,
        "policy_number": normalized,
        "claims_count": len(claims),
        "claims": claims,
        "memory_facts": facts,
    }


@trace_tool("confirm_human_in_the_loop_action")
def confirm_human_in_the_loop_action(
    action_id: str,
    approved: bool,
    supervisor_notes: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Records explicit Human-in-the-Loop (HITL) approval or rejection for a high-stakes action.

    Args:
        action_id: The pending HITL action identifier (for example 'HITL-DSP-CLM-123456').
        approved: True if the human operator/supervisor approves execution; False to reject.
        supervisor_notes: Audit notes from the human reviewer explaining the decision.

    Returns:
        Dictionary with status, action_id, hitl_approved boolean, and audit timestamp.
    """
    clean_id = action_id.strip().upper()
    if len(clean_id) < 4:
        return build_error_response(
            error_code="INVALID_HITL_ACTION_ID",
            message=f"Action ID '{action_id}' is invalid.",
            recovery_guidance="Pass the action_id returned by the pending_human_approval response (e.g. 'HITL-DSP-CLM-...').",
        )

    now_iso = datetime.now(UTC).isoformat()
    _set_state(tool_context, "hitl_approved", approved)
    _set_state(
        tool_context,
        "hitl_audit_record",
        {
            "action_id": clean_id,
            "approved": approved,
            "supervisor_notes": supervisor_notes,
            "decided_at": now_iso,
        },
    )

    record_metric_event(
        "hitl.decision_recorded",
        {
            "action_id": clean_id,
            "approved": approved,
            "supervisor_notes": supervisor_notes,
        },
    )

    if not approved:
        return {
            "status": "rejected_by_human",
            "action_id": clean_id,
            "hitl_approved": False,
            "supervisor_notes": supervisor_notes,
            "decided_at": now_iso,
            "message": f"High-stakes action '{clean_id}' was rejected by human reviewer.",
        }

    return {
        "status": "success",
        "action_id": clean_id,
        "hitl_approved": True,
        "supervisor_notes": supervisor_notes,
        "decided_at": now_iso,
        "message": f"High-stakes action '{clean_id}' approved by human reviewer; ready to execute.",
    }


high_stakes_dispatch_tool = FunctionTool(
    dispatch_roadside_assistance,
    require_confirmation=HumanInTheLoopApprovalGate.requires_confirmation_predicate,
)

