"""Ownership markers for directories managed by CC Port."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import default_state_dir
from .models import RegistryItem

MANAGED_MARKER = ".cc-port-managed.json"
MCP_OWNERSHIP_VERSION = 2


def managed_marker_path(target: Path) -> Path:
    return target / MANAGED_MARKER


def read_managed_marker(target: Path) -> dict[str, Any] | None:
    marker = managed_marker_path(target)
    if not marker.is_file():
        return None
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def managed_resource_key(target: Path) -> str:
    marker = read_managed_marker(target)
    if not marker or marker.get("managed_by") != "cc-port":
        return ""
    stored_key = str(marker.get("resource_key") or "")
    if stored_key:
        return stored_key
    name = str(marker.get("resource") or "")
    kind = str(marker.get("kind") or "")
    return f"{kind}:{name}" if kind and name else ""


def is_cc_port_managed(
    target: Path,
    *,
    resource_name: str | None = None,
    resource_kind: str | None = None,
    resource_key: str | None = None,
) -> bool:
    if not target.is_dir():
        return False
    marker = read_managed_marker(target)
    if not marker or marker.get("managed_by") != "cc-port":
        return False
    stored_name = str(marker.get("resource") or "")
    stored_kind = str(marker.get("kind") or "")
    stored_key = str(marker.get("resource_key") or "")
    expected_key = resource_key or (
        f"{resource_kind}:{resource_name}"
        if resource_kind and resource_name
        else ""
    )
    if expected_key:
        if stored_key:
            return stored_key == expected_key
        expected_kind, _, expected_name = expected_key.partition(":")
        return stored_name == expected_name and (not stored_kind or stored_kind == expected_kind)
    return not resource_name or not stored_name or stored_name == resource_name


def write_managed_marker(
    target: Path,
    entry: RegistryItem,
    *,
    platform: str,
) -> Path | None:
    """Mark a copied directory as owned by CC Port."""
    if not target.is_dir():
        return None
    marker = managed_marker_path(target)
    payload = {
        "managed_by": "cc-port",
        "resource": entry.name,
        "kind": entry.kind,
        "resource_key": entry.resource_key,
        "platform": platform,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    marker.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return marker


def mcp_ownership_path() -> Path:
    return default_state_dir() / "ownership" / "mcp.json"


def managed_mcp_resource_key(target: Path, server_name: str) -> str:
    state = _read_mcp_ownership()
    servers = state["targets"].get(_target_key(target), {})
    record = servers.get(server_name)
    if not isinstance(record, dict):
        return ""
    stored_key = str(record.get("resource_key") or "")
    if stored_key:
        return stored_key
    name = str(record.get("resource") or "")
    kind = str(record.get("kind") or "mcp")
    return f"{kind}:{name}" if name else ""


def is_cc_port_managed_mcp(
    target: Path,
    server_name: str,
    *,
    resource_name: str | None = None,
    resource_kind: str | None = None,
    resource_key: str | None = None,
) -> bool:
    state = _read_mcp_ownership()
    servers = state["targets"].get(_target_key(target), {})
    record = servers.get(server_name)
    if not isinstance(record, dict):
        return False
    stored_name = str(record.get("resource") or "")
    stored_kind = str(record.get("kind") or "")
    stored_key = str(record.get("resource_key") or "")
    expected_key = resource_key or (
        f"{resource_kind}:{resource_name}"
        if resource_kind and resource_name
        else ""
    )
    if expected_key:
        if stored_key:
            return stored_key == expected_key
        expected_kind, _, expected_name = expected_key.partition(":")
        return stored_name == expected_name and (not stored_kind or stored_kind == expected_kind)
    return not resource_name or not stored_name or stored_name == resource_name


def mark_cc_port_managed_mcp(
    target: Path,
    server_name: str,
    *,
    resource_name: str,
    platform: str,
    resource_kind: str = "mcp",
    resource_key: str | None = None,
) -> Path:
    state = _read_mcp_ownership()
    servers = state["targets"].setdefault(_target_key(target), {})
    servers[server_name] = {
        "resource": resource_name,
        "kind": resource_kind,
        "resource_key": resource_key or f"{resource_kind}:{resource_name}",
        "platform": platform,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return _write_mcp_ownership(state)


def unmark_cc_port_managed_mcp(target: Path, server_name: str) -> None:
    state = _read_mcp_ownership()
    target_key = _target_key(target)
    servers = state["targets"].get(target_key)
    if not isinstance(servers, dict):
        return
    servers.pop(server_name, None)
    if not servers:
        state["targets"].pop(target_key, None)
    _write_mcp_ownership(state)


def _target_key(target: Path) -> str:
    return os.path.normcase(str(target.expanduser().absolute()))


def _read_mcp_ownership() -> dict[str, Any]:
    path = mcp_ownership_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    targets = data.get("targets") if isinstance(data, dict) else None
    return {
        "version": MCP_OWNERSHIP_VERSION,
        "targets": targets if isinstance(targets, dict) else {},
    }


def _write_mcp_ownership(state: dict[str, Any]) -> Path:
    path = mcp_ownership_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".mcp.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path
