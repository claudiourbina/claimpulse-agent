"""CLI utility to seed remote Cloud Firestore or local SQLite with ClaimPulse insurance data."""

from __future__ import annotations

import argparse
import os

from app.storage.repository import FirestoreRepository, SQLiteRepository


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed ClaimPulse insurance database (Firestore or SQLite)."
    )
    parser.add_argument(
        "--backend",
        choices=["firestore", "sqlite"],
        default=os.environ.get("CLAIM_PULSE_STORAGE_BACKEND", "sqlite"),
        help="Target storage backend to seed.",
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        help="GCP project ID (for Firestore).",
    )
    args = parser.parse_args()

    if args.backend == "firestore":
        repo = FirestoreRepository(project_id=args.project)
        repo.seed_defaults()
        print(
            f"Successfully seeded Cloud Firestore in project '{args.project}' with ClaimPulse data."
        )
    else:
        repo = SQLiteRepository()
        repo.seed_defaults()
        print("Successfully seeded local SQLite database with ClaimPulse data.")


if __name__ == "__main__":
    main()
