"""Deterministic 3-tier insurance claim triage and settlement calculation engine."""

from __future__ import annotations

import hashlib
from urllib.parse import quote

from app.core.models import PolicyRecord, TriageDecision, TriageTier

_CRITICAL_KEYWORDS: tuple[str, ...] = (
    "incendio",
    "fire",
    "humo",
    "smoke",
    "herido",
    "injured",
    "injuries",
    "ambulancia",
    "ambulance",
    "volcamiento",
    "rollover",
    "atrapado",
    "trapped",
    "fuga de gas",
    "gas leak",
    "pérdida total",
    "total loss",
    "airbag",
    "no enciende",
    "inmovilizado",
)

_GLASS_KEYWORDS: tuple[str, ...] = (
    "parabrisas",
    "windshield",
    "vidrio",
    "glass",
    "luneta",
    "ventana",
)

_STRUCTURAL_PROPERTY_KEYWORDS: tuple[str, ...] = (
    "techo",
    "roof",
    "filtración",
    "inundación",
    "flood",
    "cañería",
    "pipe",
    "hogar",
    "home",
    "pared",
    "structural",
)


class ClaimTriageEngine:
    """Evaluates claim inputs deterministically to produce a 3-tier triage pathway."""

    @staticmethod
    def evaluate(
        *,
        policy: PolicyRecord,
        damage_description: str,
        severity_score: int,
        is_drivable: bool,
        has_injuries: bool,
    ) -> TriageDecision:
        """Runs deterministic 3-tier triage classification and reserve estimation."""
        desc_lower = damage_description.lower()
        has_critical_keyword = any(kw in desc_lower for kw in _CRITICAL_KEYWORDS)
        is_glass_claim = any(kw in desc_lower for kw in _GLASS_KEYWORDS)
        is_property_claim = any(kw in desc_lower for kw in _STRUCTURAL_PROPERTY_KEYWORDS)

        # Determine Tier
        if (
            has_injuries
            or not is_drivable
            or severity_score >= 8
            or has_critical_keyword
        ):
            tier = TriageTier.TIER_3_CRITICAL_EMERGENCY
            tier_label = "Tier 3 — Critical Emergency & Immediate Roadside Dispatch"
        elif severity_score >= 4:
            tier = TriageTier.TIER_2_ADJUSTER_INSPECTION
            tier_label = "Tier 2 — Certified Adjuster & Workshop Inspection"
        else:
            tier = TriageTier.TIER_1_FAST_TRACK
            tier_label = "Tier 1 — Fast-Track Digital Settlement & Express Repair"

        # Estimate repair cost based on severity score
        base_cost_map = {
            1: 220.0,
            2: 380.0,
            3: 620.0,
            4: 1250.0,
            5: 2100.0,
            6: 3400.0,
            7: 5200.0,
            8: 9500.0,
            9: 15800.0,
            10: 24500.0,
        }
        estimated_repair = base_cost_map.get(severity_score, 1500.0)

        # Deductible logic (zero deductible for glass if policy includes it)
        if is_glass_claim and policy.glass_zero_deductible and tier == TriageTier.TIER_1_FAST_TRACK:
            applied_deductible = 0.0
        else:
            applied_deductible = policy.deductible_usd

        raw_payout = max(0.0, estimated_repair - applied_deductible)
        estimated_payout = min(raw_payout, policy.max_coverage_usd)

        # Determine specialty
        if tier == TriageTier.TIER_3_CRITICAL_EMERGENCY:
            specialty = "Emergency Heavy Collision & Catastrophic Loss"
        elif is_property_claim:
            specialty = "Structural Property & Water Damage"
        elif is_glass_claim:
            specialty = "Express Glass & Cosmetic Calibration"
        elif tier == TriageTier.TIER_2_ADJUSTER_INSPECTION:
            specialty = "Senior Automotive Collision & Liability"
        else:
            specialty = "Fast-Track Cosmetic & Minor Bodywork"

        # Immediate actions and red-flag warnings
        if tier == TriageTier.TIER_3_CRITICAL_EMERGENCY:
            immediate_actions = [
                "Move all occupants to a safe zone away from traffic or hazards immediately.",
                "Turn on hazard lights and deploy reflective safety triangles if safe.",
                "Call Emergency Services (911 / 131 SAMU / 133 Carabineros) if anyone is injured.",
                "Stay connected on the Emergency Adjuster Google Meet bridge while the tow unit is en route.",
            ]
            red_flag_warnings = [
                "CRITICAL SAFETY ALERT: Do not attempt to drive an immobilized or smoking vehicle.",
                "If airbag deployment or head/neck pain is present, wait for paramedic clearance.",
            ]
        elif tier == TriageTier.TIER_2_ADJUSTER_INSPECTION:
            immediate_actions = [
                "Upload 4 corner photos of the damage and the incident location.",
                "Exchange driver's license, plate, and insurance details if a third party is involved.",
                "Confirm your scheduled certified adjuster inspection and Google Meet pre-check.",
            ]
            red_flag_warnings = [
                "If fluid leaks, wheel misalignment, or warning lights appear, request roadside tow immediately.",
            ]
        else:
            immediate_actions = [
                "Review your Fast-Track digital payout breakdown or choose an Express Workshop.",
                "Take 2 clear photos of the cosmetic/glass damage for instant archival.",
                "Accept one-click digital settlement or schedule an express 45-minute repair bay.",
            ]
            red_flag_warnings = [
                "Fast-Track settlement covers visible cosmetic/glass items; hidden structural damage upgrades to Tier 2.",
            ]

        return TriageDecision(
            tier=tier,
            tier_label=tier_label,
            severity_score=severity_score,
            is_drivable=is_drivable,
            has_injuries=has_injuries,
            estimated_repair_usd=round(estimated_repair, 2),
            applied_deductible_usd=round(applied_deductible, 2),
            estimated_payout_usd=round(estimated_payout, 2),
            recommended_specialty=specialty,
            immediate_actions=immediate_actions,
            red_flag_warnings=red_flag_warnings,
            requires_emergency_dispatch=(tier == TriageTier.TIER_3_CRITICAL_EMERGENCY),
            requires_adjuster_booking=(tier == TriageTier.TIER_2_ADJUSTER_INSPECTION),
            fast_track_eligible=(tier == TriageTier.TIER_1_FAST_TRACK),
        )

    @staticmethod
    def generate_workspace_links(
        *,
        claim_id: str,
        title: str,
        slot_iso: str,
        location_or_workshop: str,
        attendee_email: str,
    ) -> tuple[str, str]:
        """Generates deterministic Google Calendar 1-click template URL and Google Meet room link."""
        digest = hashlib.sha256(f"{claim_id}:{slot_iso}".encode()).hexdigest()[:10]
        meet_code = f"{digest[:3]}-{digest[3:7]}-{digest[7:10]}"
        meet_url = f"https://meet.google.com/{meet_code}"

        # Normalize compact date format for Google Calendar URL
        clean_date = (
            slot_iso.replace("-", "").replace(":", "").replace(" ", "T")
        )
        if "T" not in clean_date:
            clean_date = f"{clean_date[:8]}T100000Z/{clean_date[:8]}T110000Z"
        else:
            start_token = clean_date[:15] + "Z"
            end_token = clean_date[:11] + "1100Z"
            clean_date = f"{start_token}/{end_token}"

        details = (
            f"ClaimPulse Inspection for Claim {claim_id}. "
            f"Virtual Bridge: {meet_url}"
        )
        calendar_url = (
            "https://calendar.google.com/calendar/render?action=TEMPLATE"
            f"&text={quote(title)}"
            f"&dates={quote(clean_date)}"
            f"&details={quote(details)}"
            f"&location={quote(location_or_workshop)}"
            f"&add={quote(attendee_email)}"
        )
        return calendar_url, meet_url
