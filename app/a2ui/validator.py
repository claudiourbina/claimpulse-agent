"""Strict schema and security validator gate for A2UI v0.8 payloads."""

from __future__ import annotations

import re
from typing import Any

from app.a2ui.components import ALLOWED_A2UI_COMPONENTS, A2UIComponent, A2UISurface

_FORBIDDEN_MARKUP_RE = re.compile(
    r"(<\s*script\b|<\s*iframe\b|<\s*object\b|javascript\s*:|onerror\s*=|onload\s*=|<\s*html\b|<\s*div\b)",
    re.IGNORECASE,
)


class A2UIValidationError(ValueError):
    """Raised when an A2UI v0.8 payload violates the catalog or security invariant."""


class A2UICatalogValidator:
    """Enforces the Core Invariant: pure catalog-only A2UI v0.8 JSON, zero HTML/JS."""

    @classmethod
    def validate_surface(cls, surface: A2UISurface | dict[str, Any]) -> dict[str, Any]:
        """Validates an A2UISurface or raw dict and returns the canonical serialized dict."""
        if isinstance(surface, dict):
            parsed = A2UISurface.model_validate(surface)
        else:
            parsed = surface

        if parsed.version != "0.8":
            raise A2UIValidationError(
                f"Unsupported A2UI version '{parsed.version}'; expected '0.8'."
            )
        if not parsed.surface_id.strip():
            raise A2UIValidationError("surface_id cannot be empty.")

        seen_ids: set[str] = set()
        cls._validate_component(parsed.root, seen_ids=seen_ids)
        return parsed.model_dump()

    @classmethod
    def _validate_component(
        cls, component: A2UIComponent, seen_ids: set[str]
    ) -> None:
        if component.type not in ALLOWED_A2UI_COMPONENTS:
            raise A2UIValidationError(
                f"Component type '{component.type}' is not in the A2UI v0.8 catalog."
            )
        if component.id in seen_ids:
            raise A2UIValidationError(
                f"Duplicate component ID '{component.id}' detected on surface."
            )
        seen_ids.add(component.id)

        cls._inspect_value_for_injection(component.props, context=component.id)
        for child in component.children:
            cls._validate_component(child, seen_ids=seen_ids)

    @classmethod
    def _inspect_value_for_injection(cls, value: Any, context: str) -> None:
        if isinstance(value, str):
            if _FORBIDDEN_MARKUP_RE.search(value):
                raise A2UIValidationError(
                    f"Forbidden HTML/JS markup or script URI detected in component '{context}'."
                )
        elif isinstance(value, dict):
            for k, v in value.items():
                if k == "url" and isinstance(v, str):
                    if not (v.startswith("https://") or v.startswith("tel:")):
                        raise A2UIValidationError(
                            f"Invalid URL scheme in '{context}': only https:// or tel: allowed."
                        )
                cls._inspect_value_for_injection(v, context=f"{context}.{k}")
        elif isinstance(value, list):
            for item in value:
                cls._inspect_value_for_injection(item, context=context)
