"""Local end-to-end verification script for ClaimPulse 3-Tier Triage, Discovery, and A2UI v0.8."""

from __future__ import annotations

import json

from app.tools import (
    assess_claim_severity,
    discover_available_specialists,
    dispatch_roadside_assistance,
    schedule_adjuster_and_workshop,
    verify_policy_and_coverage,
)


def main() -> None:
    print("=== [1] Dynamic Agent Runtime Specialist Discovery ===")
    disc = discover_available_specialists("all")
    print(
        json.dumps(
            {
                "status": disc["status"],
                "deployment_mode": disc["deployment_mode"],
                "specialists": [s["name"] for s in disc["discovered_specialists"]],
            },
            indent=2,
            ensure_ascii=False,
        )
    )

    print("\n=== [2] Policy Verification (POL-CL-1001) ===")
    pol = verify_policy_and_coverage("POL-CL-1001", "cosmetic")
    print(
        json.dumps(
            {
                "status": pol["status"],
                "holder": pol["policy"]["holder_name"],
                "vehicle": pol["policy"]["vehicle_make_model"],
                "a2ui_surface_id": pol["a2ui_surface"]["surface_id"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )

    print("\n=== [3] Tier 1 Fast-Track Triage (Severity 2/10) ===")
    t1 = assess_claim_severity(
        policy_number="POL-CL-1001",
        damage_description="Rayón superficial en parachoques trasero estacionando",
        severity_score=2,
        is_drivable=True,
        has_injuries=False,
        location="Las Condes, Santiago",
    )
    print(
        json.dumps(
            t1["triage_decision"],
            indent=2,
            ensure_ascii=False,
        )
    )

    print("\n=== [4] Tier 2 Moderate Collision + Adjuster Scheduling (POL-CL-1002) ===")
    t2 = assess_claim_severity(
        policy_number="POL-CL-1002",
        damage_description="Choque en cruce, abolladura de capó y foco delantero roto",
        severity_score=6,
        is_drivable=True,
        has_injuries=False,
        location="Providencia, Santiago",
    )
    sched = schedule_adjuster_and_workshop(
        claim_id=t2["claim_id"],
        specialty=t2["triage_decision"]["recommended_specialty"],
        preferred_date="2026-10-15T10:00:00",
        modality="Virtual Google Meet",
    )
    print(
        json.dumps(
            sched["appointment"],
            indent=2,
            ensure_ascii=False,
        )
    )

    print("\n=== [5] Tier 3 Critical Highway Emergency + Roadside Tow Dispatch (POL-CL-1003) ===")
    t3 = assess_claim_severity(
        policy_number="POL-CL-1003",
        damage_description="Choque grave en Ruta 5 Sur km 42, humo del motor, vehículo inmovilizado",
        severity_score=9,
        is_drivable=False,
        has_injuries=True,
        location="Ruta 5 Sur km 42",
    )
    disp = dispatch_roadside_assistance(
        claim_id=t3["claim_id"],
        location="Ruta 5 Sur km 42",
        hazard_level="CRITICAL",
        requires_ambulance=True,
    )
    print(
        json.dumps(
            {
                "dispatch": disp["dispatch"],
                "emergency_protocol": disp["emergency_protocol"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
