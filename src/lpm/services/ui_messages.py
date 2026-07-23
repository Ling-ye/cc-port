"""Stable semantic message references for desktop localization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeAlias

UiMessageParam: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True)
class UiMessageRef:
    """A desktop-localizable message with a legacy English fallback."""

    code: str
    fallback: str
    params: dict[str, UiMessageParam] = field(default_factory=dict)


def ui_message(
    code: str,
    fallback: str,
    **params: UiMessageParam,
) -> UiMessageRef:
    """Build one message reference at the semantic decision point."""

    return UiMessageRef(code=code, fallback=fallback, params=dict(params))


def fallback_text(refs: list[UiMessageRef]) -> list[str]:
    """Return compatibility strings without reverse-mapping human text."""

    return [ref.fallback for ref in refs]


def ui_message_from_data(value: Any) -> UiMessageRef | None:
    """Read an optional reference from persisted or JSON-compatible data."""

    if not isinstance(value, dict):
        return None
    code = str(value.get("code") or "").strip()
    fallback = str(value.get("fallback") or "")
    if not code:
        return None
    raw_params = value.get("params")
    params = (
        {
            str(key): item
            for key, item in raw_params.items()
            if item is None or isinstance(item, str | int | float | bool)
        }
        if isinstance(raw_params, dict)
        else {}
    )
    return UiMessageRef(code=code, fallback=fallback, params=params)


def ui_messages_from_data(value: Any) -> list[UiMessageRef]:
    """Read a list of valid references and ignore malformed compatibility data."""

    if not isinstance(value, list):
        return []
    return [
        ref
        for item in value
        if (ref := ui_message_from_data(item)) is not None
    ]
