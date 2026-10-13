"""Pydantic v2 domain models and strict input/output validation schemas for ClaimPulse."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TriageTier(StrEnum):
    """3-Tier insurance claim severity classification."""

    TIER_1_FAST_TRACK = "TIER_1_FAST_TRACK"
    TIER_2_ADJUSTER_INSPECTION = "TIER_2_ADJUSTER_INSPECTION"
    TIER_3_CRITICAL_EMERGENCY = "TIER_3_CRITICAL_EMERGENCY"


class PolicyRecord(BaseModel):
    """Represents an insurance policy stored in Firestore or SQLite."""

    model_config = ConfigDict(extra="ignore")

    policy_number: str = Field(..., description="Unique policy identifier (e.g. POL-CL-1001)")
    holder_name: str = Field(..., description="Full name of the policyholder")
    plan_tier: str = Field(..., description="Coverage plan tier (Platinum, Gold, Comprehensive)")
    status: str = Field(default="ACTIVE", description="Policy status: ACTIVE, SUSPENDED, EXPIRED")
    vehicle_make_model: str = Field(..., description="Insured vehicle or property description")
    vehicle_year: int = Field(..., ge=1990, le=2030)
    deductible_usd: float = Field(..., ge=0.0, description="Policy deductible in USD")
    max_coverage_usd: float = Field(..., gt=0.0, description="Maximum coverage limit in USD")
    roadside_assistance_included: bool = Field(default=True)
    glass_zero_deductible: bool = Field(default=True)
    covered_perils: list[str] = Field(default_factory=list)


class AdjusterProfile(BaseModel):
    """Represents an accredited claims adjuster."""

    model_config = ConfigDict(extra="ignore")

    adjuster_id: str
    full_name: str
    specialty: str
    credentials: str
    years_experience: int = Field(ge=1)
    rating: float = Field(ge=1.0, le=5.0)
    modalities: list[str] = Field(default_factory=list)
    available_slots: list[str] = Field(default_factory=list)
    calendar_email: str


class WorkshopProfile(BaseModel):
    """Represents a certified repair workshop."""

    model_config = ConfigDict(extra="ignore")

    workshop_id: str
    name: str
    city: str
    address: str
    specialties: list[str]
    express_fast_track: bool = True
    rating: float = Field(ge=1.0, le=5.0)


class RoadsideUnit(BaseModel):
    """Represents an emergency tow truck or roadside assistance unit."""

    model_config = ConfigDict(extra="ignore")

    unit_id: str
    operator_name: str
    unit_type: str
    coverage_corridor: str
    average_eta_minutes: int = Field(ge=5, le=180)
    emergency_phone: str


class TriageDecision(BaseModel):
    """Deterministic output produced by ClaimTriageEngine."""

    tier: TriageTier
    tier_label: str
    severity_score: int = Field(ge=1, le=10)
    is_drivable: bool
    has_injuries: bool
    estimated_repair_usd: float = Field(ge=0.0)
    applied_deductible_usd: float = Field(ge=0.0)
    estimated_payout_usd: float = Field(ge=0.0)
    recommended_specialty: str
    immediate_actions: list[str]
    red_flag_warnings: list[str]
    requires_emergency_dispatch: bool
    requires_adjuster_booking: bool
    fast_track_eligible: bool


class ClaimRecord(BaseModel):
    """Persisted First Notice of Loss (FNOL) claim record."""

    model_config = ConfigDict(extra="ignore")

    claim_id: str
    policy_number: str
    holder_name: str
    damage_description: str
    severity_score: int = Field(ge=1, le=10)
    tier: TriageTier
    is_drivable: bool
    has_injuries: bool
    location: str
    estimated_repair_usd: float
    applied_deductible_usd: float
    estimated_payout_usd: float
    recommended_specialty: str
    status: str = "OPEN"
    created_at: str


class AppointmentRecord(BaseModel):
    """Persisted adjuster and workshop inspection appointment."""

    model_config = ConfigDict(extra="ignore")

    appointment_id: str
    claim_id: str
    policy_number: str
    adjuster_id: str
    adjuster_name: str
    workshop_id: str
    workshop_name: str
    scheduled_slot: str
    modality: str
    google_calendar_url: str
    google_meet_url: str
    status: str = "CONFIRMED"


class DispatchRecord(BaseModel):
    """Persisted emergency roadside assistance / tow dispatch record."""

    model_config = ConfigDict(extra="ignore")

    dispatch_id: str
    claim_id: str
    policy_number: str
    unit_id: str
    operator_name: str
    location: str
    hazard_level: str
    eta_minutes: int
    requires_ambulance: bool
    emergency_bridge_meet_url: str
    status: str = "DISPATCHED"


# --- Tool Input Validation Models ---


class PolicyVerificationInput(BaseModel):
    """Validated input for policy and coverage verification."""

    policy_number: str = Field(..., min_length=4, max_length=32)
    incident_type: str = Field(..., min_length=2, max_length=64)

    @field_validator("policy_number")
    @classmethod
    def normalize_policy_number(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if not cleaned.startswith("POL-"):
            raise ValueError(
                "Policy number must start with 'POL-' (e.g. 'POL-CL-1001')."
            )
        return cleaned


class ClaimTriageInput(BaseModel):
    """Validated input for 3-tier claim severity triage."""

    policy_number: str = Field(..., min_length=4, max_length=32)
    damage_description: str = Field(..., min_length=5, max_length=1000)
    severity_score: int = Field(..., ge=1, le=10)
    is_drivable: bool
    has_injuries: bool
    location: str = Field(..., min_length=2, max_length=200)

    @field_validator("policy_number")
    @classmethod
    def normalize_policy_number(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if not cleaned.startswith("POL-"):
            raise ValueError(
                "Policy number must start with 'POL-' (e.g. 'POL-CL-1001')."
            )
        return cleaned


class AdjusterScheduleInput(BaseModel):
    """Validated input for scheduling an adjuster and workshop."""

    claim_id: str = Field(..., min_length=4, max_length=40)
    specialty: str = Field(..., min_length=2, max_length=80)
    preferred_date: str = Field(..., min_length=4, max_length=64)
    modality: str = Field(..., min_length=3, max_length=32)


class RoadsideDispatchInput(BaseModel):
    """Validated input for emergency roadside tow/assistance dispatch."""

    claim_id: str = Field(..., min_length=4, max_length=40)
    location: str = Field(..., min_length=3, max_length=200)
    hazard_level: str = Field(..., min_length=3, max_length=32)
    requires_ambulance: bool


class DiscoveryInput(BaseModel):
    """Validated input for discovering specialist agents on Agent Runtime."""

    capability_filter: str = Field(default="all", min_length=1, max_length=64)


def build_error_response(
    error_code: str, message: str, recovery_guidance: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Constructs a standardized, LLM-recoverable tool error dictionary."""
    return {
        "status": "error",
        "error_code": error_code,
        "message": message,
        "recovery_guidance": recovery_guidance,
        "details": details or {},
    }
