"""Tests for the MCP installer (mcp.json read/write)."""

from __future__ import annotations

import json
from pathlib import Path

from lpm.mcp_installer import (
    has_mcp_server,
    inject_mcp_server,
    list_mcp_servers,
    remove_mcp_server,
)


def test_inject_creates_file(tmp_path: Path) -> None:
    mcp_json = tmp_path / "mcp.json"
    inject_mcp_server(mcp_json, "test-server", {"command": "npx", "args": ["-y", "@test/mcp"]})
    assert mcp_json.is_file()
    data = json.loads(mcp_json.read_text())
    assert "test-server" in data["mcpServers"]
    assert data["mcpServers"]["test-server"]["command"] == "npx"


def test_inject_preserves_existing(tmp_path: Path) -> None:
    mcp_json = tmp_path / "mcp.json"
    mcp_json.write_text(json.dumps({
        "mcpServers": {"existing": {"command": "node", "args": ["server.js"]}}
    }))
    inject_mcp_server(mcp_json, "new-server", {"command": "npx"})
    data = json.loads(mcp_json.read_text())
    assert "existing" in data["mcpServers"]
    assert "new-server" in data["mcpServers"]


def test_inject_updates_existing(tmp_path: Path) -> None:
    mcp_json = tmp_path / "mcp.json"
    inject_mcp_server(mcp_json, "s", {"command": "old"})
    inject_mcp_server(mcp_json, "s", {"command": "new"})
    data = json.loads(mcp_json.read_text())
    assert data["mcpServers"]["s"]["command"] == "new"


def test_remove_mcp_server(tmp_path: Path) -> None:
    mcp_json = tmp_path / "mcp.json"
    inject_mcp_server(mcp_json, "a", {"command": "a"})
    inject_mcp_server(mcp_json, "b", {"command": "b"})
    assert remove_mcp_server(mcp_json, "a") is True
    assert remove_mcp_server(mcp_json, "a") is False
    data = json.loads(mcp_json.read_text())
    assert "a" not in data["mcpServers"]
    assert "b" in data["mcpServers"]


def test_remove_from_nonexistent(tmp_path: Path) -> None:
    mcp_json = tmp_path / "mcp.json"
    assert remove_mcp_server(mcp_json, "nope") is False


def test_list_mcp_servers(tmp_path: Path) -> None:
    mcp_json = tmp_path / "mcp.json"
    inject_mcp_server(mcp_json, "x", {"command": "x"})
    inject_mcp_server(mcp_json, "y", {"command": "y"})
    servers = list_mcp_servers(mcp_json)
    assert set(servers.keys()) == {"x", "y"}


def test_list_empty(tmp_path: Path) -> None:
    mcp_json = tmp_path / "mcp.json"
    assert list_mcp_servers(mcp_json) == {}


def test_has_mcp_server(tmp_path: Path) -> None:
    mcp_json = tmp_path / "mcp.json"
    inject_mcp_server(mcp_json, "present", {"command": "test"})
    assert has_mcp_server(mcp_json, "present") is True
    assert has_mcp_server(mcp_json, "absent") is False


def test_handles_claude_json_format(tmp_path: Path) -> None:
    """Claude Code's ~/.claude.json may have other top-level keys."""
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(json.dumps({
        "version": 1,
        "settings": {"theme": "dark"},
        "mcpServers": {"old": {"command": "old"}},
    }))
    inject_mcp_server(claude_json, "new", {"command": "new"})
    data = json.loads(claude_json.read_text())
    assert data["version"] == 1
    assert data["settings"]["theme"] == "dark"
    assert "old" in data["mcpServers"]
    assert "new" in data["mcpServers"]
