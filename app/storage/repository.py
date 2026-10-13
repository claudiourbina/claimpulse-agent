"""Dual-backend storage repository (Serverless Cloud Firestore + Local SQLite fallback)."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core.models import (
    AdjusterProfile,
    AppointmentRecord,
    ClaimRecord,
    DispatchRecord,
    PolicyRecord,
    RoadsideUnit,
    WorkshopProfile,
)
from app.data.seed_data import (
    SEED_ADJUSTERS,
    SEED_POLICIES,
    SEED_ROADSIDE_UNITS,
    SEED_WORKSHOPS,
)
from app.telemetry.scrubber import PIIScrubber
from app.telemetry.tracer import get_logger, traced_operation

logger = get_logger("claimpulse.storage")


class ClaimPulseRepository(Protocol):
    """Unified storage interface shared by FirestoreRepository and SQLiteRepository."""

    backend_name: str

    def get_policy(self, policy_number: str) -> PolicyRecord | None: ...
    def list_policies(self) -> list[PolicyRecord]: ...
    def save_claim(self, claim: ClaimRecord) -> ClaimRecord: ...
    def get_claim(self, claim_id: str) -> ClaimRecord | None: ...
    def list_claims_by_policy(self, policy_number: str) -> list[ClaimRecord]: ...
    def find_adjusters(self, specialty: str | None = None) -> list[AdjusterProfile]: ...
    def find_workshops(self, specialty: str | None = None) -> list[WorkshopProfile]: ...
    def find_roadside_unit(self, location: str) -> RoadsideUnit: ...
    def save_appointment(self, appointment: AppointmentRecord) -> AppointmentRecord: ...
    def save_dispatch(self, dispatch: DispatchRecord) -> DispatchRecord: ...
    def save_memory_fact(self, policy_number: str, fact_key: str, fact_value: str) -> None: ...
    def get_memory_facts(self, policy_number: str) -> dict[str, str]: ...


class SQLiteRepository:
    """Thread-safe SQLite repository with automatic seeding for local dev, CI, and fallback."""

    backend_name = "sqlite"

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or os.environ.get(
            "CLAIM_PULSE_SQLITE_PATH", ":memory:"
        )
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self.seed_defaults()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS policies (
                    policy_number TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS adjusters (
                    adjuster_id TEXT PRIMARY KEY,
                    specialty TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workshops (
                    workshop_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS roadside_units (
                    unit_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS claims (
                    claim_id TEXT PRIMARY KEY,
                    policy_number TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS appointments (
                    appointment_id TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL,
                    policy_number TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dispatches (
                    dispatch_id TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL,
                    policy_number TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_facts (
                    policy_number TEXT NOT NULL,
                    fact_key TEXT NOT NULL,
                    fact_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (policy_number, fact_key)
                );
                """
            )

    def seed_defaults(self) -> None:
        """Populates initial insurance policies, adjusters, workshops, and roadside units."""
        with self._lock, self._conn:
            for p in SEED_POLICIES:
                self._conn.execute(
                    "INSERT OR REPLACE INTO policies (policy_number, payload_json) VALUES (?, ?)",
                    (p.policy_number, p.model_dump_json()),
                )
            for a in SEED_ADJUSTERS:
                self._conn.execute(
                    "INSERT OR REPLACE INTO adjusters (adjuster_id, specialty, payload_json) VALUES (?, ?, ?)",
                    (a.adjuster_id, a.specialty, a.model_dump_json()),
                )
            for w in SEED_WORKSHOPS:
                self._conn.execute(
                    "INSERT OR REPLACE INTO workshops (workshop_id, payload_json) VALUES (?, ?)",
                    (w.workshop_id, w.model_dump_json()),
                )
            for u in SEED_ROADSIDE_UNITS:
                self._conn.execute(
                    "INSERT OR REPLACE INTO roadside_units (unit_id, payload_json) VALUES (?, ?)",
                    (u.unit_id, u.model_dump_json()),
                )

    def get_policy(self, policy_number: str) -> PolicyRecord | None:
        with traced_operation("db.sqlite.get_policy", {"policy_number": policy_number}):
            with self._lock:
                row = self._conn.execute(
                    "SELECT payload_json FROM policies WHERE policy_number = ?",
                    (policy_number.strip().upper(),),
                ).fetchone()
                if not row:
                    return None
                return PolicyRecord.model_validate_json(row["payload_json"])

    def list_policies(self) -> list[PolicyRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT payload_json FROM policies").fetchall()
            return [PolicyRecord.model_validate_json(r["payload_json"]) for r in rows]

    def save_claim(self, claim: ClaimRecord) -> ClaimRecord:
        with traced_operation("db.sqlite.save_claim", {"claim_id": claim.claim_id}):
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO claims (claim_id, policy_number, created_at, payload_json) VALUES (?, ?, ?, ?)",
                    (
                        claim.claim_id,
                        claim.policy_number,
                        claim.created_at,
                        claim.model_dump_json(),
                    ),
                )
            return claim

    def get_claim(self, claim_id: str) -> ClaimRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM claims WHERE claim_id = ?",
                (claim_id.strip().upper(),),
            ).fetchone()
            if not row:
                return None
            return ClaimRecord.model_validate_json(row["payload_json"])

    def list_claims_by_policy(self, policy_number: str) -> list[ClaimRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload_json FROM claims WHERE policy_number = ? ORDER BY created_at DESC",
                (policy_number.strip().upper(),),
            ).fetchall()
            return [ClaimRecord.model_validate_json(r["payload_json"]) for r in rows]

    def find_adjusters(self, specialty: str | None = None) -> list[AdjusterProfile]:
        with self._lock:
            rows = self._conn.execute("SELECT payload_json FROM adjusters").fetchall()
            all_adj = [AdjusterProfile.model_validate_json(r["payload_json"]) for r in rows]
            if not specialty:
                return all_adj
            spec_lower = specialty.lower()
            matched = [
                a
                for a in all_adj
                if spec_lower in a.specialty.lower()
                or any(tok in a.specialty.lower() for tok in spec_lower.split())
            ]
            return matched or all_adj

    def find_workshops(self, specialty: str | None = None) -> list[WorkshopProfile]:
        with self._lock:
            rows = self._conn.execute("SELECT payload_json FROM workshops").fetchall()
            all_ws = [WorkshopProfile.model_validate_json(r["payload_json"]) for r in rows]
            if not specialty:
                return all_ws
            spec_lower = specialty.lower()
            matched = [
                w
                for w in all_ws
                if any(
                    spec_lower in s.lower()
                    or any(tok in s.lower() for tok in spec_lower.split())
                    for s in w.specialties
                )
            ]
            return matched or all_ws

    def find_roadside_unit(self, location: str) -> RoadsideUnit:
        with self._lock:
            rows = self._conn.execute("SELECT payload_json FROM roadside_units").fetchall()
            units = [RoadsideUnit.model_validate_json(r["payload_json"]) for r in rows]
            loc_lower = location.lower()
            for unit in units:
                if any(
                    token in loc_lower
                    for token in unit.coverage_corridor.lower().replace("/", " ").split()
                    if len(token) > 3
                ):
                    return unit
            return units[0]

    def save_appointment(self, appointment: AppointmentRecord) -> AppointmentRecord:
        with traced_operation(
            "db.sqlite.save_appointment", {"appointment_id": appointment.appointment_id}
        ):
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO appointments (appointment_id, claim_id, policy_number, payload_json) VALUES (?, ?, ?, ?)",
                    (
                        appointment.appointment_id,
                        appointment.claim_id,
                        appointment.policy_number,
                        appointment.model_dump_json(),
                    ),
                )
            return appointment

    def save_dispatch(self, dispatch: DispatchRecord) -> DispatchRecord:
        with traced_operation(
            "db.sqlite.save_dispatch", {"dispatch_id": dispatch.dispatch_id}
        ):
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO dispatches (dispatch_id, claim_id, policy_number, payload_json) VALUES (?, ?, ?, ?)",
                    (
                        dispatch.dispatch_id,
                        dispatch.claim_id,
                        dispatch.policy_number,
                        dispatch.model_dump_json(),
                    ),
                )
            return dispatch

    def save_memory_fact(self, policy_number: str, fact_key: str, fact_value: str) -> None:
        scrubbed_val = PIIScrubber.scrub_text(fact_value)
        now_iso = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO memory_facts (policy_number, fact_key, fact_value, updated_at) VALUES (?, ?, ?, ?)",
                (policy_number.strip().upper(), fact_key, scrubbed_val, now_iso),
            )

    def get_memory_facts(self, policy_number: str) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT fact_key, fact_value FROM memory_facts WHERE policy_number = ?",
                (policy_number.strip().upper(),),
            ).fetchall()
            return {r["fact_key"]: r["fact_value"] for r in rows}


class FirestoreRepository:
    """Serverless Google Cloud Firestore repository for distributed Agent Runtime instances."""

    backend_name = "firestore"

    def __init__(
        self,
        project_id: str | None = None,
        database: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._project_id = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self._database = database or os.environ.get(
            "CLAIM_PULSE_FIRESTORE_DB", "claimpulse-db"
        )
        if client is not None:
            self._client = client
        else:
            from google.cloud import firestore

            self._client = firestore.Client(
                project=self._project_id, database=self._database
            )
        self._prefix = os.environ.get("CLAIM_PULSE_COLLECTION_PREFIX", "claimpulse")
        self._ensure_seeded()

    def _col(self, name: str) -> Any:
        return self._client.collection(f"{self._prefix}_{name}")

    def _ensure_seeded(self) -> None:
        """Idempotently seeds Firestore if the policies collection does not have POL-CL-1001."""
        try:
            doc = self._col("policies").document("POL-CL-1001").get()
            if not doc.exists:
                self.seed_defaults()
        except Exception as exc:
            logger.warning(f"Firestore seed check skipped or deferred: {exc}")

    def seed_defaults(self) -> None:
        """Writes initial seed documents into Cloud Firestore."""
        with traced_operation("db.firestore.seed_defaults"):
            for p in SEED_POLICIES:
                self._col("policies").document(p.policy_number).set(p.model_dump())
            for a in SEED_ADJUSTERS:
                self._col("adjusters").document(a.adjuster_id).set(a.model_dump())
            for w in SEED_WORKSHOPS:
                self._col("workshops").document(w.workshop_id).set(w.model_dump())
            for u in SEED_ROADSIDE_UNITS:
                self._col("roadside_units").document(u.unit_id).set(u.model_dump())

    def get_policy(self, policy_number: str) -> PolicyRecord | None:
        with traced_operation(
            "db.firestore.get_policy", {"policy_number": policy_number}
        ):
            doc = self._col("policies").document(policy_number.strip().upper()).get()
            if not doc.exists:
                return None
            return PolicyRecord.model_validate(doc.to_dict())

    def list_policies(self) -> list[PolicyRecord]:
        docs = self._col("policies").stream()
        return [PolicyRecord.model_validate(d.to_dict()) for d in docs]

    def save_claim(self, claim: ClaimRecord) -> ClaimRecord:
        with traced_operation("db.firestore.save_claim", {"claim_id": claim.claim_id}):
            self._col("claims").document(claim.claim_id).set(claim.model_dump())
            return claim

    def get_claim(self, claim_id: str) -> ClaimRecord | None:
        doc = self._col("claims").document(claim_id.strip().upper()).get()
        if not doc.exists:
            return None
        return ClaimRecord.model_validate(doc.to_dict())

    def list_claims_by_policy(self, policy_number: str) -> list[ClaimRecord]:
        normalized = policy_number.strip().upper()
        docs = (
            self._col("claims")
            .where("policy_number", "==", normalized)
            .stream()
        )
        claims = [ClaimRecord.model_validate(d.to_dict()) for d in docs]
        return sorted(claims, key=lambda c: c.created_at, reverse=True)

    def find_adjusters(self, specialty: str | None = None) -> list[AdjusterProfile]:
        docs = self._col("adjusters").stream()
        all_adj = [AdjusterProfile.model_validate(d.to_dict()) for d in docs]
        if not all_adj:
            all_adj = list(SEED_ADJUSTERS)
        if not specialty:
            return all_adj
        spec_lower = specialty.lower()
        matched = [
            a
            for a in all_adj
            if spec_lower in a.specialty.lower()
            or any(tok in a.specialty.lower() for tok in spec_lower.split())
        ]
        return matched or all_adj

    def find_workshops(self, specialty: str | None = None) -> list[WorkshopProfile]:
        docs = self._col("workshops").stream()
        all_ws = [WorkshopProfile.model_validate(d.to_dict()) for d in docs]
        if not all_ws:
            all_ws = list(SEED_WORKSHOPS)
        if not specialty:
            return all_ws
        spec_lower = specialty.lower()
        matched = [
            w
            for w in all_ws
            if any(
                spec_lower in s.lower()
                or any(tok in s.lower() for tok in spec_lower.split())
                for s in w.specialties
            )
        ]
        return matched or all_ws

    def find_roadside_unit(self, location: str) -> RoadsideUnit:
        docs = self._col("roadside_units").stream()
        units = [RoadsideUnit.model_validate(d.to_dict()) for d in docs]
        if not units:
            units = list(SEED_ROADSIDE_UNITS)
        loc_lower = location.lower()
        for unit in units:
            if any(
                token in loc_lower
                for token in unit.coverage_corridor.lower().replace("/", " ").split()
                if len(token) > 3
            ):
                return unit
        return units[0]

    def save_appointment(self, appointment: AppointmentRecord) -> AppointmentRecord:
        with traced_operation(
            "db.firestore.save_appointment",
            {"appointment_id": appointment.appointment_id},
        ):
            self._col("appointments").document(appointment.appointment_id).set(
                appointment.model_dump()
            )
            return appointment

    def save_dispatch(self, dispatch: DispatchRecord) -> DispatchRecord:
        with traced_operation(
            "db.firestore.save_dispatch", {"dispatch_id": dispatch.dispatch_id}
        ):
            self._col("dispatches").document(dispatch.dispatch_id).set(
                dispatch.model_dump()
            )
            return dispatch

    def save_memory_fact(
        self, policy_number: str, fact_key: str, fact_value: str
    ) -> None:
        scrubbed_val = PIIScrubber.scrub_text(fact_value)
        doc_ref = self._col("memories").document(policy_number.strip().upper())
        doc = doc_ref.get()
        existing = doc.to_dict() if doc.exists else {}
        facts = dict(existing.get("facts", {}))
        facts[fact_key] = scrubbed_val
        doc_ref.set(
            {
                "policy_number": policy_number.strip().upper(),
                "facts": facts,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    def get_memory_facts(self, policy_number: str) -> dict[str, str]:
        doc = self._col("memories").document(policy_number.strip().upper()).get()
        if not doc.exists:
            return {}
        data = doc.to_dict() or {}
        return dict(data.get("facts", {}))


_REPOSITORY_SINGLETON: ClaimPulseRepository | None = None


def get_repository() -> ClaimPulseRepository:
    """Returns the configured remote (Firestore) or local (SQLite) repository instance."""
    global _REPOSITORY_SINGLETON
    if _REPOSITORY_SINGLETON is not None:
        return _REPOSITORY_SINGLETON

    backend = os.environ.get("CLAIM_PULSE_STORAGE_BACKEND", "").strip().lower()
    if not backend:
        # Default to firestore when deployed on Agent Runtime unless overridden
        if os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID"):
            backend = "firestore"
        else:
            backend = "sqlite"

    if backend == "firestore":
        try:
            _REPOSITORY_SINGLETON = FirestoreRepository()
            logger.info("Initialized remote Cloud Firestore repository.")
            return _REPOSITORY_SINGLETON
        except Exception as exc:
            logger.warning(
                f"Firestore initialization failed ({exc}); falling back to SQLiteRepository."
            )

    _REPOSITORY_SINGLETON = SQLiteRepository()
    return _REPOSITORY_SINGLETON


def reset_repository_for_tests(repo: ClaimPulseRepository | None = None) -> None:
    """Resets or injects a repository instance for deterministic unit testing."""
    global _REPOSITORY_SINGLETON
    _REPOSITORY_SINGLETON = repo
