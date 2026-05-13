"""Secret-safe helpers for MCP configs and public tool responses."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

REDACTED = "***REDACTED***"


def sanitize_mcp_config_for_storage(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Replace concrete MCP env secret values with ${ENV_NAME} placeholders.

    The registry is meant to be committed.  MCP configs often contain env vars,
    so non-empty literal values are converted to placeholders before storage or
    platform installation. Existing placeholders such as ${GITHUB_TOKEN} are kept.
    """
    if config is None:
        return None
    out = deepcopy(config)
    env = out.get("env")
    if not isinstance(env, dict):
        return out
    for key, value in list(env.items()):
        if isinstance(value, str) and _is_placeholder(value):
            continue
        if value in ("", None):
            continue
        env[key] = f"${{{key}}}"
    return out


def redact_mcp_config(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a display-safe copy of an MCP config."""
    if config is None:
        return None
    out = deepcopy(config)
    env = out.get("env")
    if isinstance(env, dict):
        for key, value in list(env.items()):
            if isinstance(value, str) and _is_placeholder(value):
                continue
            if value in ("", None):
                continue
            env[key] = REDACTED
    return out


def redact_item_dump(item: dict[str, Any]) -> dict[str, Any]:
    """Redact MCP config fields in a serialized registry item."""
    out = deepcopy(item)
    if "mcp_config" in out:
        out["mcp_config"] = redact_mcp_config(out.get("mcp_config"))
    return out


def _is_placeholder(value: str) -> bool:
    value = value.strip()
    return value.startswith("${") and value.endswith("}") and len(value) > 3
