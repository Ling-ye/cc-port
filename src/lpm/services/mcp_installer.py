"""Read/write MCP server entries in mcp.json / .claude.json files.

Both Cursor (``~/.cursor/mcp.json``) and Claude Code (``.mcp.json`` /
``~/.claude.json``) use the same JSON schema::

    {
      "mcpServers": {
        "<name>": { "command": "...", "args": [...], "env": {...} }
      }
    }

Claude Code's ``~/.claude.json`` embeds ``mcpServers`` inside a larger
settings object.  This module handles both layouts transparently.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    return json.loads(text)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup_once(path)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _backup_once(path: Path) -> None:
    if not path.is_file():
        return
    backup = path.with_name(f"{path.name}.lpm.bak")
    if backup.exists():
        return
    shutil.copy2(path, backup)


def _get_servers_section(data: dict[str, Any]) -> dict[str, Any]:
    """Extract or create the ``mcpServers`` dict from the JSON root."""
    return data.setdefault("mcpServers", {})


def inject_mcp_server(
    mcp_json_path: Path,
    server_name: str,
    server_config: dict[str, Any],
) -> None:
    """Add or update one MCP server entry in the given JSON file."""
    data = _read_json(mcp_json_path)
    servers = _get_servers_section(data)
    servers[server_name] = server_config
    _write_json(mcp_json_path, data)


def remove_mcp_server(mcp_json_path: Path, server_name: str) -> bool:
    """Remove an MCP server entry. Returns True if it was present."""
    data = _read_json(mcp_json_path)
    servers = _get_servers_section(data)
    if server_name not in servers:
        return False
    del servers[server_name]
    _write_json(mcp_json_path, data)
    return True


def list_mcp_servers(mcp_json_path: Path) -> dict[str, Any]:
    """Return all MCP server entries from the file."""
    data = _read_json(mcp_json_path)
    return dict(_get_servers_section(data))


def has_mcp_server(mcp_json_path: Path, server_name: str) -> bool:
    data = _read_json(mcp_json_path)
    return server_name in _get_servers_section(data)
