"""Unit tests for ClaimPulse 3-tier triage engine and all 6 Pydantic-validated ADK tools."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.models import TriageTier
from app.storage.repository import SQLiteRepository, reset_repository_for_tests
from app.tools import (
    assess_claim_severity,
    confirm_human_in_the_loop_action,
    discover_available_specialists,
    dispatch_roadside_assistance,
    get_claim_status_and_history,
    high_stakes_dispatch_tool,
    schedule_adjuster_and_workshop,
    verify_policy_and_coverage,
)


class DummyToolContext:
    """Minimal ToolContext stub for testing state persistence in tools."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def fresh_sqlite_repo() -> None:
    """Ensures each test runs against a clean in-memory SQLite repository."""
    reset_repository_for_tests(SQLiteRepository(":memory:"))


def test_verify_policy_active_success() -> None:
    ctx = DummyToolContext()
    res = verify_policy_and_coverage("pol-cl-1001", "collision", tool_context=ctx)  # type: ignore[arg-type]
    assert res["status"] == "success"
    assert res["policy"]["policy_number"] == "POL-CL-1001"
    assert res["policy"]["holder_name"] == "Camila Valdés Rojas"
    assert ctx.state["verified_policy_number"] == "POL-CL-1001"
    assert res["a2ui_surface"]["version"] == "0.8"


def test_verify_policy_invalid_format_returns_recovery_guidance() -> None:
    res = verify_policy_and_coverage("INVALID-999", "collision")
    assert res["status"] == "error"
    assert res["error_code"] == "INVALID_POLICY_FORMAT"
    assert "POL-" in res["recovery_guidance"]


def test_verify_policy_not_found_returns_recovery_guidance() -> None:
    res = verify_policy_and_coverage("POL-CL-0000", "collision")
    assert res["status"] == "error"
    assert res["error_code"] == "POLICY_NOT_FOUND"
    assert "POL-CL-1001" in res["recovery_guidance"]


def test_verify_policy_expired_blocked() -> None:
    res = verify_policy_and_coverage("POL-CL-9999", "collision")
    assert res["status"] == "error"
    assert res["error_code"] == "POLICY_INACTIVE"


def test_tier_1_fast_track_cosmetic_triage() -> None:
    ctx = DummyToolContext()
    res = assess_claim_severity(
        policy_number="POL-CL-1001",
        damage_description="Minor scratch on rear bumper while parking",
        severity_score=2,
        is_drivable=True,
        has_injuries=False,
        location="Las Condes, Santiago",
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert res["status"] == "success"
    decision = res["triage_decision"]
    assert decision["tier"] == TriageTier.TIER_1_FAST_TRACK.value
    assert decision["fast_track_eligible"] is True
    assert decision["requires_emergency_dispatch"] is False
    assert decision["applied_deductible_usd"] == 150.0
    assert decision["estimated_payout_usd"] == 230.0
    assert ctx.state["last_triage_tier"] == "TIER_1_FAST_TRACK"


def test_tier_1_glass_zero_deductible_applied() -> None:
    res = assess_claim_severity(
        policy_number="POL-CL-1001",
        damage_description="Small stone chip on front windshield glass",
        severity_score=2,
        is_drivable=True,
        has_injuries=False,
        location="Vitacura, Santiago",
    )
    assert res["status"] == "success"
    decision = res["triage_decision"]
    assert decision["tier"] == TriageTier.TIER_1_FAST_TRACK.value
    assert decision["applied_deductible_usd"] == 0.0
    assert decision["estimated_payout_usd"] == decision["estimated_repair_usd"]


def test_tier_2_moderate_collision_and_adjuster_scheduling() -> None:
    ctx = DummyToolContext()
    triage_res = assess_claim_severity(
        policy_number="POL-CL-1002",
        damage_description="Low-speed intersection collision, dented hood and broken headlight",
        severity_score=6,
        is_drivable=True,
        has_injuries=False,
        location="Providencia, Santiago",
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert triage_res["status"] == "success"
    decision = triage_res["triage_decision"]
    assert decision["tier"] == TriageTier.TIER_2_ADJUSTER_INSPECTION.value
    assert decision["requires_adjuster_booking"] is True

    claim_id = triage_res["claim_id"]
    sched_res = schedule_adjuster_and_workshop(
        claim_id=claim_id,
        specialty=decision["recommended_specialty"],
        preferred_date="2026-10-15T10:00:00",
        modality="Virtual Google Meet",
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert sched_res["status"] == "success"
    appt = sched_res["appointment"]
    assert appt["claim_id"] == claim_id
    assert appt["google_calendar_url"].startswith(
        "https://calendar.google.com/calendar/render"
    )
    assert appt["google_meet_url"].startswith("https://meet.google.com/")
    assert sched_res["a2ui_surface"]["version"] == "0.8"


def test_tier_3_critical_emergency_and_roadside_dispatch() -> None:
    ctx = DummyToolContext()
    triage_res = assess_claim_severity(
        policy_number="POL-CL-1003",
        damage_description="High-speed collision on Ruta 5 Sur, smoke coming from engine, immobilized",
        severity_score=9,
        is_drivable=False,
        has_injuries=True,
        location="Ruta 5 Sur km 42",
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert triage_res["status"] == "success"
    decision = triage_res["triage_decision"]
    assert decision["tier"] == TriageTier.TIER_3_CRITICAL_EMERGENCY.value
    assert decision["requires_emergency_dispatch"] is True

    claim_id = triage_res["claim_id"]
    disp_res = dispatch_roadside_assistance(
        claim_id=claim_id,
        location="Ruta 5 Sur km 42",
        hazard_level="CRITICAL",
        requires_ambulance=True,
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert disp_res["status"] == "success"
    assert disp_res["dispatch"]["unit_id"] == "TOW-R5-101"
    assert disp_res["dispatch"]["requires_ambulance"] is True
    assert disp_res["emergency_protocol"]["live_adjuster_meet"].startswith(
        "https://meet.google.com/"
    )


def test_triage_escalates_to_tier_3_when_injuries_present_even_low_severity_score() -> None:
    res = assess_claim_severity(
        policy_number="POL-CL-1001",
        damage_description="Minor rear-end bump but passenger has neck pain",
        severity_score=2,
        is_drivable=True,
        has_injuries=True,
        location="Av. Apoquindo 4500",
    )
    assert res["status"] == "success"
    assert (
        res["triage_decision"]["tier"]
        == TriageTier.TIER_3_CRITICAL_EMERGENCY.value
    )


def test_get_claim_status_and_history_returns_claims_and_memories() -> None:
    assess_claim_severity(
        policy_number="POL-CL-1001",
        damage_description="Parabrisas trizado en autopista",
        severity_score=2,
        is_drivable=True,
        has_injuries=False,
        location="Costanera Norte",
    )
    hist = get_claim_status_and_history("POL-CL-1001")
    assert hist["status"] == "success"
    assert hist["claims_count"] == 1
    assert hist["memory_facts"].get("damage_category") == "glass"


def test_discover_available_specialists_tool() -> None:
    res = discover_available_specialists("all")
    assert res["status"] == "success"
    assert len(res["discovered_specialists"]) == 3
    roles = {s["role"] for s in res["discovered_specialists"]}
    assert roles == {"intake", "triage", "dispatch"}


def test_schedule_adjuster_claim_not_found_returns_recovery_guidance() -> None:
    res = schedule_adjuster_and_workshop(
        claim_id="CLM-NONEXISTENT",
        specialty="Senior Automotive Collision & Liability",
        preferred_date="2026-10-15T10:00:00",
        modality="Virtual Google Meet",
    )
    assert res["status"] == "error"
    assert res["error_code"] == "CLAIM_NOT_FOUND"
    assert "assess_claim_severity" in res["recovery_guidance"]


def test_dispatch_roadside_invalid_input_returns_recovery_guidance() -> None:
    res = dispatch_roadside_assistance(
        claim_id="C1",  # too short (<4 chars)
        location="R",
        hazard_level="CRITICAL",
        requires_ambulance=False,
    )
    assert res["status"] == "error"
    assert res["error_code"] == "INVALID_DISPATCH_INPUT"
    assert "recovery_guidance" in res


def test_get_claim_status_invalid_policy_prefix() -> None:
    res = get_claim_status_and_history("BAD-1001")
    assert res["status"] == "error"
    assert res["error_code"] == "INVALID_POLICY_NUMBER"


def test_human_in_the_loop_confirmation_gate_for_emergency_dispatch() -> None:
    ctx = DummyToolContext()
    ctx.state["require_hitl_approval"] = True

    # 1. High-stakes dispatch pauses for HITL confirmation when gate is armed
    pending_res = dispatch_roadside_assistance(
        claim_id="CLM-998877",
        location="Ruta 5 Sur km 42",
        hazard_level="CRITICAL",
        requires_ambulance=True,
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert pending_res["status"] == "pending_human_approval"
    assert pending_res["hitl_confirmation_required"] is True
    action_id = pending_res["action_id"]

    # 2. Human supervisor approves via confirm_human_in_the_loop_action
    approval = confirm_human_in_the_loop_action(
        action_id=action_id,
        approved=True,
        supervisor_notes="Verified emergency telemetry and passenger status; approved.",
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert approval["status"] == "success"
    assert approval["hitl_approved"] is True
    assert ctx.state["hitl_approved"] is True

    # 3. Subsequent execution of dispatch_roadside_assistance proceeds immediately
    dispatched = dispatch_roadside_assistance(
        claim_id="CLM-998877",
        location="Ruta 5 Sur km 42",
        hazard_level="CRITICAL",
        requires_ambulance=True,
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert dispatched["status"] == "success"
    assert dispatched["dispatch"]["status"] == "DISPATCHED"


def test_human_in_the_loop_rejection_records_audit_trail() -> None:
    ctx = DummyToolContext()
    rej = confirm_human_in_the_loop_action(
        action_id="HITL-DSP-CLM-0001",
        approved=False,
        supervisor_notes="Duplicate dispatch request cancelled by operator.",
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert rej["status"] == "rejected_by_human"
    assert rej["hitl_approved"] is False
    assert high_stakes_dispatch_tool is not None


