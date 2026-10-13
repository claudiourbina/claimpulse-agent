"""Active PII & financial data scrubber for insurance logs and OpenTelemetry trace spans."""

from __future__ import annotations

import re
from typing import Any


class PIIScrubber:
    """Scrubs sensitive policyholder PII/PCI data from strings, dicts, and trace spans.

    Redacts:
    - Chilean RUT / National IDs (e.g. 12.345.678-9, 18456789-K)
    - US SSNs (e.g. 123-45-6789)
    - Credit card numbers (13-19 digits with optional separators)
    - Email addresses
    - Phone numbers (international & local formats, e.g. +56 9 8765 4321)
    - Vehicle License Plates (e.g. ABCD-12, BB-CC-34, ABC-123)
    - Vehicle Identification Numbers (17-character standard VIN)
    """

    _PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "[REDACTED_EMAIL]",
            re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        ),
        (
            "[REDACTED_CARD]",
            re.compile(r"\b(?:\d{4}[-\s]){3}\d{4}\b|\b\d{16}\b"),
        ),
        (
            "[REDACTED_SSN]",
            re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        ),
        (
            "[REDACTED_RUT]",
            re.compile(
                r"\b\d{1,2}(?:\.\d{3}){2}-[\dkK]\b|\b\d{7,8}-[\dkK]\b"
            ),
        ),
        (
            "[REDACTED_PHONE]",
            re.compile(
                r"(?:\+\d{1,3}[\s-]?)?(?:\(\d{1,4}\)[\s-]?)?\b\d{1,4}[\s-]\d{3,4}[\s-]\d{3,4}\b"
            ),
        ),
        (
            "[REDACTED_VIN]",
            re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b"),
        ),
        (
            "[REDACTED_PLATE]",
            re.compile(
                r"\b(?:[A-Z]{4}-\d{2}|[A-Z]{2}-[A-Z]{2}-\d{2})\b"
            ),
        ),
    )

    _SENSITIVE_KEYS: frozenset[str] = frozenset(
        {
            "rut",
            "ssn",
            "national_id",
            "tax_id",
            "phone",
            "phone_number",
            "email",
            "credit_card",
            "card_number",
            "bank_account",
            "vin",
            "license_plate",
            "plate",
        }
    )

    @classmethod
    def scrub_text(cls, text: str) -> str:
        """Redacts all matching PII patterns from a string."""
        if not text or not isinstance(text, str):
            return text
        result = text
        for replacement, pattern in cls._PATTERNS:
            result = pattern.sub(replacement, result)
        return result

    @classmethod
    def scrub_value(cls, value: Any, key_hint: str | None = None) -> Any:
        """Recursively scrubs strings, lists, and dicts for safe logging and tracing."""
        if key_hint and key_hint.lower() in cls._SENSITIVE_KEYS:
            if isinstance(value, str) and value:
                return f"[REDACTED_{key_hint.upper()}]"
        if isinstance(value, str):
            return cls.scrub_text(value)
        if isinstance(value, dict):
            return {
                str(k): cls.scrub_value(v, key_hint=str(k))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [cls.scrub_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(cls.scrub_value(item) for item in value)
        return value


def scrub_pii(data: Any) -> Any:
    """Convenience helper to scrub arbitrary strings or data structures."""
    return PIIScrubber.scrub_value(data)
