"""Deterministic A2UI v0.8 view builders for ClaimPulse workflows."""

from __future__ import annotations

from typing import Any

from app.a2ui.components import (
    A2UISurface,
    build_badge,
    build_button,
    build_card,
    build_checkbox,
    build_column,
    build_datetime_input,
    build_list,
    build_multiple_choice,
    build_row,
    build_slider,
    build_tabs,
    build_text,
    build_text_field,
)
from app.a2ui.validator import A2UICatalogValidator
from app.core.models import (
    AppointmentRecord,
    ClaimRecord,
    DispatchRecord,
    PolicyRecord,
    TriageDecision,
    TriageTier,
)


def build_policy_intake_view(policy: PolicyRecord) -> dict[str, Any]:
    """Constructs and validates the Policy Verification & FNOL Intake A2UI v0.8 surface."""
    tone = "success" if policy.status == "ACTIVE" else "danger"
    card = build_card(
        component_id=f"card_policy_{policy.policy_number.lower()}",
        title=f"Policy {policy.policy_number} — {policy.plan_tier}",
        subtitle=f"Holder: {policy.holder_name} | Vehicle/Asset: {policy.vehicle_make_model} ({policy.vehicle_year})",
        status_tone=tone,
        children=[
            build_row(
                component_id="row_policy_badges",
                children=[
                    build_badge("badge_status", f"Status: {policy.status}", tone=tone),
                    build_badge(
                        "badge_deductible",
                        f"Deductible: USD {policy.deductible_usd:.0f}",
                        tone="info",
                    ),
                    build_badge(
                        "badge_roadside",
                        f"Roadside 24/7: {'Included' if policy.roadside_assistance_included else 'Not Included'}",
                        tone="info",
                    ),
                ],
            ),
            build_list(
                component_id="list_covered_perils",
                title="Covered Perils & Endorsements",
                items=policy.covered_perils,
            ),
            build_text_field(
                component_id="input_incident_notes",
                label="Incident Description",
                placeholder="Describe damage, location, and whether anyone is injured...",
            ),
            build_checkbox(
                component_id="chk_fnol_consent",
                label="I confirm that the reported incident details are accurate under policy terms.",
                checked=True,
            ),
        ],
    )
    surface = A2UISurface(
        surface_id=f"surface_policy_{policy.policy_number.lower()}",
        title="ClaimPulse Policy Verification",
        root=card,
        metadata={"policy_number": policy.policy_number, "status": policy.status},
    )
    return A2UICatalogValidator.validate_surface(surface)


def build_triage_resolution_view(
    claim: ClaimRecord, decision: TriageDecision
) -> dict[str, Any]:
    """Constructs and validates the 3-Tier Claim Triage & Reserve A2UI v0.8 surface."""
    tone_map = {
        TriageTier.TIER_1_FAST_TRACK: "success",
        TriageTier.TIER_2_ADJUSTER_INSPECTION: "warning",
        TriageTier.TIER_3_CRITICAL_EMERGENCY: "danger",
    }
    tone = tone_map.get(decision.tier, "info")

    tabs = build_tabs(
        component_id=f"tabs_triage_{claim.claim_id.lower()}",
        tabs=[
            {"id": "tab_summary", "label": "Triage Pathway"},
            {"id": "tab_financials", "label": "Reserve & Deductible"},
            {"id": "tab_safety", "label": "Protocol & Alerts"},
        ],
        children=[
            build_column(
                component_id="col_triage_details",
                children=[
                    build_row(
                        component_id="row_tier_header",
                        children=[
                            build_badge(
                                "badge_tier",
                                decision.tier_label,
                                tone=tone,
                                icon="health_and_safety",
                            ),
                            build_badge(
                                "badge_specialty",
                                decision.recommended_specialty,
                                tone="info",
                                icon="engineering",
                            ),
                        ],
                    ),
                    build_slider(
                        component_id="slider_severity",
                        label="Assessed Damage Severity Index (1-10)",
                        value=decision.severity_score,
                    ),
                    build_multiple_choice(
                        component_id="mc_pathway_options",
                        label="Recommended Resolution Modality",
                        options=[
                            "Express Digital Payout",
                            "Certified Workshop Inspection",
                            "Emergency Roadside Tow & Bridge",
                        ],
                        selected=(
                            "Emergency Roadside Tow & Bridge"
                            if decision.requires_emergency_dispatch
                            else (
                                "Certified Workshop Inspection"
                                if decision.requires_adjuster_booking
                                else "Express Digital Payout"
                            )
                        ),
                    ),
                    build_text(
                        component_id="txt_financial_breakdown",
                        text=(
                            f"Estimated Repair: USD {decision.estimated_repair_usd:.2f} | "
                            f"Deductible: USD {decision.applied_deductible_usd:.2f} | "
                            f"Estimated Net Payout: USD {decision.estimated_payout_usd:.2f}"
                        ),
                        variant="subtitle",
                    ),
                    build_list(
                        component_id="list_immediate_actions",
                        title="Required Next Steps",
                        items=decision.immediate_actions,
                    ),
                    build_list(
                        component_id="list_red_flags",
                        title="Safety & Coverage Advisories",
                        items=decision.red_flag_warnings,
                    ),
                ],
            )
        ],
    )

    card = build_card(
        component_id=f"card_triage_{claim.claim_id.lower()}",
        title=f"Claim {claim.claim_id} — {decision.tier_label}",
        subtitle=f"Policy {claim.policy_number} | Location: {claim.location}",
        status_tone=tone,
        children=[tabs],
    )
    surface = A2UISurface(
        surface_id=f"surface_triage_{claim.claim_id.lower()}",
        title="ClaimPulse 3-Tier Triage Assessment",
        root=card,
        metadata={
            "claim_id": claim.claim_id,
            "tier": decision.tier.value,
            "severity_score": decision.severity_score,
        },
    )
    return A2UICatalogValidator.validate_surface(surface)


def build_adjuster_appointment_view(
    appointment: AppointmentRecord,
) -> dict[str, Any]:
    """Constructs and validates the Adjuster & Certified Workshop Booking A2UI v0.8 surface."""
    card = build_card(
        component_id=f"card_appt_{appointment.appointment_id.lower()}",
        title=f"Inspection Confirmed — {appointment.adjuster_name}",
        subtitle=f"Workshop: {appointment.workshop_name} ({appointment.modality})",
        status_tone="success",
        children=[
            build_datetime_input(
                component_id="dt_appointment_slot",
                label="Scheduled Inspection Date & Time",
                value=appointment.scheduled_slot,
            ),
            build_row(
                component_id="row_workspace_actions",
                children=[
                    build_button(
                        component_id="btn_google_calendar",
                        label="Add to Google Calendar",
                        action_id="sync_google_calendar",
                        url=appointment.google_calendar_url,
                        variant="primary",
                    ),
                    build_button(
                        component_id="btn_google_meet",
                        label="Join Virtual Inspection (Google Meet)",
                        action_id="open_google_meet",
                        url=appointment.google_meet_url,
                        variant="secondary",
                    ),
                ],
            ),
        ],
    )
    surface = A2UISurface(
        surface_id=f"surface_appt_{appointment.appointment_id.lower()}",
        title="ClaimPulse Adjuster & Workshop Schedule",
        root=card,
        metadata={"appointment_id": appointment.appointment_id},
    )
    return A2UICatalogValidator.validate_surface(surface)


def build_emergency_dispatch_view(dispatch: DispatchRecord) -> dict[str, Any]:
    """Constructs and validates the Tier 3 Critical Emergency Roadside Dispatch A2UI v0.8 surface."""
    card = build_card(
        component_id=f"card_dispatch_{dispatch.dispatch_id.lower()}",
        title=f"EMERGENCY ROADSIDE DISPATCH — {dispatch.unit_id}",
        subtitle=f"Operator: {dispatch.operator_name} | ETA: {dispatch.eta_minutes} minutes",
        status_tone="danger",
        children=[
            build_row(
                component_id="row_emergency_badges",
                children=[
                    build_badge(
                        "badge_eta",
                        f"Live Tow ETA: {dispatch.eta_minutes} min",
                        tone="danger",
                        icon="local_shipping",
                    ),
                    build_badge(
                        "badge_hazard",
                        f"Hazard Level: {dispatch.hazard_level.upper()}",
                        tone="danger",
                        icon="warning",
                    ),
                ],
            ),
            build_text(
                component_id="txt_dispatch_location",
                text=f"Dispatch Coordinates / Corridor: {dispatch.location}",
                variant="body",
            ),
            build_row(
                component_id="row_emergency_buttons",
                children=[
                    build_button(
                        component_id="btn_emergency_call",
                        label="Call Emergency Services (911 / 131)",
                        action_id="call_emergency_services",
                        url="tel:911",
                        variant="danger",
                    ),
                    build_button(
                        component_id="btn_emergency_meet",
                        label="Join Live On-Call Adjuster Bridge",
                        action_id="join_emergency_meet",
                        url=dispatch.emergency_bridge_meet_url,
                        variant="primary",
                    ),
                ],
            ),
        ],
    )
    surface = A2UISurface(
        surface_id=f"surface_dispatch_{dispatch.dispatch_id.lower()}",
        title="ClaimPulse Tier 3 Emergency Dispatch",
        root=card,
        metadata={"dispatch_id": dispatch.dispatch_id, "eta_minutes": dispatch.eta_minutes},
    )
    return A2UICatalogValidator.validate_surface(surface)


def build_discovery_roster_view(
    discovered_agents: list[dict[str, Any]], deployment_mode: str
) -> dict[str, Any]:
    """Constructs and validates an A2UI v0.8 card showing dynamically discovered Agent Runtime specialists."""
    items = [
        f"{a['name']} ({a['role']}) — Mode: {a['transport']} | Status: {a['status']}"
        for a in discovered_agents
    ]
    card = build_card(
        component_id="card_agent_discovery",
        title="Gemini Enterprise Agent Runtime — Specialist Roster",
        subtitle=f"Active Discovery Mode: {deployment_mode.upper()}",
        status_tone="info",
        children=[
            build_list(
                component_id="list_discovered_specialists",
                title="Discovered Specialist Reasoning Engines",
                items=items,
            )
        ],
    )
    surface = A2UISurface(
        surface_id="surface_agent_discovery",
        title="Agent Registry Dynamic Discovery",
        root=card,
        metadata={"count": len(discovered_agents), "mode": deployment_mode},
    )
    return A2UICatalogValidator.validate_surface(surface)
