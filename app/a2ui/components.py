"""Typed Python builders for the A2UI v0.8 declarative UI catalog."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

ALLOWED_A2UI_COMPONENTS: frozenset[str] = frozenset(
    {
        "Card",
        "Row",
        "Column",
        "Text",
        "Badge",
        "Button",
        "Slider",
        "DateTimeInput",
        "CheckBox",
        "MultipleChoice",
        "TextField",
        "Tabs",
        "List",
    }
)


class A2UIComponent(BaseModel):
    """Represents a single typed A2UI v0.8 component node."""

    id: str
    type: str
    props: dict[str, Any] = Field(default_factory=dict)
    children: list[A2UIComponent] = Field(default_factory=list)


class A2UISurface(BaseModel):
    """Top-level A2UI v0.8 declarative surface envelope."""

    version: str = "0.8"
    surface_id: str
    namespace: str = "claimpulse"
    title: str
    root: A2UIComponent
    metadata: dict[str, Any] = Field(default_factory=dict)


def build_text(
    component_id: str, text: str, variant: str = "body", icon: str | None = None
) -> A2UIComponent:
    props: dict[str, Any] = {"text": text, "variant": variant}
    if icon:
        props["icon"] = icon
    return A2UIComponent(id=component_id, type="Text", props=props)


def build_badge(
    component_id: str, label: str, tone: str = "info", icon: str = "verified"
) -> A2UIComponent:
    return A2UIComponent(
        id=component_id,
        type="Badge",
        props={"label": label, "tone": tone, "icon": icon},
    )


def build_slider(
    component_id: str,
    label: str,
    value: int,
    min_value: int = 1,
    max_value: int = 10,
) -> A2UIComponent:
    return A2UIComponent(
        id=component_id,
        type="Slider",
        props={
            "label": label,
            "value": value,
            "min": min_value,
            "max": max_value,
            "step": 1,
        },
    )


def build_datetime_input(
    component_id: str, label: str, value: str
) -> A2UIComponent:
    return A2UIComponent(
        id=component_id,
        type="DateTimeInput",
        props={"label": label, "value": value},
    )


def build_checkbox(
    component_id: str, label: str, checked: bool = True
) -> A2UIComponent:
    return A2UIComponent(
        id=component_id,
        type="CheckBox",
        props={"label": label, "checked": checked},
    )


def build_multiple_choice(
    component_id: str, label: str, options: list[str], selected: str
) -> A2UIComponent:
    return A2UIComponent(
        id=component_id,
        type="MultipleChoice",
        props={"label": label, "options": options, "selected": selected},
    )


def build_text_field(
    component_id: str, label: str, placeholder: str, value: str = ""
) -> A2UIComponent:
    return A2UIComponent(
        id=component_id,
        type="TextField",
        props={"label": label, "placeholder": placeholder, "value": value},
    )


def build_button(
    component_id: str,
    label: str,
    action_id: str,
    url: str | None = None,
    variant: str = "primary",
) -> A2UIComponent:
    props: dict[str, Any] = {
        "label": label,
        "actionId": action_id,
        "variant": variant,
    }
    if url:
        props["url"] = url
    return A2UIComponent(id=component_id, type="Button", props=props)


def build_list(
    component_id: str, title: str, items: list[str]
) -> A2UIComponent:
    return A2UIComponent(
        id=component_id,
        type="List",
        props={"title": title, "items": items},
    )


def build_row(
    component_id: str, children: list[A2UIComponent], gap: int = 12
) -> A2UIComponent:
    return A2UIComponent(
        id=component_id,
        type="Row",
        props={"gap": gap},
        children=children,
    )


def build_column(
    component_id: str, children: list[A2UIComponent], gap: int = 12
) -> A2UIComponent:
    return A2UIComponent(
        id=component_id,
        type="Column",
        props={"gap": gap},
        children=children,
    )


def build_tabs(
    component_id: str, tabs: list[dict[str, Any]], children: list[A2UIComponent]
) -> A2UIComponent:
    return A2UIComponent(
        id=component_id,
        type="Tabs",
        props={"tabs": tabs},
        children=children,
    )


def build_card(
    component_id: str,
    title: str,
    subtitle: str,
    children: list[A2UIComponent],
    status_tone: str = "info",
) -> A2UIComponent:
    return A2UIComponent(
        id=component_id,
        type="Card",
        props={
            "title": title,
            "subtitle": subtitle,
            "statusTone": status_tone,
        },
        children=children,
    )
