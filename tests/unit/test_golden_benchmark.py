"""Golden benchmark evaluation suite verifying 100% accuracy across 25 insurance triage scenarios."""

from __future__ import annotations

import pytest

from app.core.models import TriageTier
from app.storage.repository import SQLiteRepository, reset_repository_for_tests
from app.tools import assess_claim_severity

GOLDEN_TRIAGE_CASES: list[tuple[str, str, int, bool, bool, TriageTier]] = [
    # (policy, description, severity, is_drivable, has_injuries, expected_tier)
    ("POL-CL-1001", "Light paint scratch on rear door", 1, True, False, TriageTier.TIER_1_FAST_TRACK),
    ("POL-CL-1001", "Small bumper scuff in parking lot", 2, True, False, TriageTier.TIER_1_FAST_TRACK),
    ("POL-CL-1001", "Small stone chip on front windshield glass", 2, True, False, TriageTier.TIER_1_FAST_TRACK),
    ("POL-CL-1002", "Side mirror housing cosmetic crack", 3, True, False, TriageTier.TIER_1_FAST_TRACK),
    ("POL-CL-1003", "Minor door ding at supermarket", 1, True, False, TriageTier.TIER_1_FAST_TRACK),
    ("POL-CL-1005", "Superficial key scratch on trunk lid", 2, True, False, TriageTier.TIER_1_FAST_TRACK),
    ("POL-CL-1004", "Broken window glass pane from wind", 3, True, False, TriageTier.TIER_1_FAST_TRACK),
    ("POL-CL-1002", "Front fender dent and broken headlight", 4, True, False, TriageTier.TIER_2_ADJUSTER_INSPECTION),
    ("POL-CL-1002", "Low-speed rear collision with third party", 5, True, False, TriageTier.TIER_2_ADJUSTER_INSPECTION),
    ("POL-CL-1001", "Dented hood and radiator grille misalignment", 6, True, False, TriageTier.TIER_2_ADJUSTER_INSPECTION),
    ("POL-CL-1003", "Side impact dent across two doors, still drivable", 7, True, False, TriageTier.TIER_2_ADJUSTER_INSPECTION),
    ("POL-CL-1004", "Water pipe leak damaging living room ceiling", 6, True, False, TriageTier.TIER_2_ADJUSTER_INSPECTION),
    ("POL-CL-1004", "Roof tiles damaged after storm with water filtration", 5, True, False, TriageTier.TIER_2_ADJUSTER_INSPECTION),
    ("POL-CL-1005", "Parking garage pillar scrape with deep quarter panel dent", 4, True, False, TriageTier.TIER_2_ADJUSTER_INSPECTION),
    ("POL-CL-1003", "Highway crash on Ruta 5 Sur, airbag deployed", 9, False, True, TriageTier.TIER_3_CRITICAL_EMERGENCY),
    ("POL-CL-1001", "Engine smoke and leaking fluid after hitting median", 8, False, False, TriageTier.TIER_3_CRITICAL_EMERGENCY),
    ("POL-CL-1002", "Vehicle rollover on highway ramp", 10, False, True, TriageTier.TIER_3_CRITICAL_EMERGENCY),
    ("POL-CL-1003", "Minor fender bender but passenger has whiplash injury", 3, True, True, TriageTier.TIER_3_CRITICAL_EMERGENCY),
    ("POL-CL-1001", "Broken axle, vehicle immobilized blocking traffic lane", 5, False, False, TriageTier.TIER_3_CRITICAL_EMERGENCY),
    ("POL-CL-1004", "Active kitchen fire and structural smoke damage", 9, False, False, TriageTier.TIER_3_CRITICAL_EMERGENCY),
    ("POL-CL-1005", "Total loss multi-vehicle pileup on Ruta 68", 10, False, True, TriageTier.TIER_3_CRITICAL_EMERGENCY),
    ("POL-CL-1002", "Small gas leak smell after rear impact", 4, True, False, TriageTier.TIER_3_CRITICAL_EMERGENCY),
    ("POL-CL-1001", "Trapped inside car on highway shoulder", 8, False, False, TriageTier.TIER_3_CRITICAL_EMERGENCY),
    ("POL-CL-1003", "Electrical fire under hood, no enciende", 9, False, False, TriageTier.TIER_3_CRITICAL_EMERGENCY),
    ("POL-CL-1005", "Pedestrian injured in crosswalk incident", 7, True, True, TriageTier.TIER_3_CRITICAL_EMERGENCY),
]


@pytest.fixture(autouse=True)
def clean_repo() -> None:
    reset_repository_for_tests(SQLiteRepository(":memory:"))


@pytest.mark.parametrize(
    ("policy", "desc", "severity", "drivable", "injuries", "expected_tier"),
    GOLDEN_TRIAGE_CASES,
)
def test_golden_triage_benchmark_accuracy(
    policy: str,
    desc: str,
    severity: int,
    drivable: bool,
    injuries: bool,
    expected_tier: TriageTier,
) -> None:
    res = assess_claim_severity(
        policy_number=policy,
        damage_description=desc,
        severity_score=severity,
        is_drivable=drivable,
        has_injuries=injuries,
        location="Santiago, Chile",
    )
    assert res["status"] == "success"
    assert res["triage_decision"]["tier"] == expected_tier.value
    assert res["a2ui_surface"]["version"] == "0.8"
