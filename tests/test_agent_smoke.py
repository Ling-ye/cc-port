from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "tools" / "packaging" / "agent" / "smoke_agent.py"
)
MODULE_SPEC = importlib.util.spec_from_file_location("cc_port_smoke_agent", MODULE_PATH)
assert MODULE_SPEC and MODULE_SPEC.loader
smoke_agent = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(smoke_agent)


def test_smoke_agent_checks_cli_and_real_tool_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = tmp_path / "cc-port.exe"
    agent.write_bytes(b"binary")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="CC Port\n", stderr="")

    monkeypatch.setattr(smoke_agent.subprocess, "run", fake_run)
    monkeypatch.setattr(
        smoke_agent,
        "_run_mcp_smoke",
        lambda selected, *, timeout_seconds: (
            ["asset_inventory"],
            selected == agent and timeout_seconds == 30.0,
        ),
    )

    result = smoke_agent.smoke(agent)

    assert result["ok"] is True
    assert result["tools"] == ["asset_inventory"]
    assert result["status_call_ok"] is True
    assert calls == [[str(agent), "--help"]]


def test_smoke_agent_rejects_missing_required_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = tmp_path / "cc-port.exe"
    agent.write_bytes(b"binary")

    def fake_run(command: list[str], **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="CC Port\n", stderr="")

    monkeypatch.setattr(smoke_agent.subprocess, "run", fake_run)
    monkeypatch.setattr(smoke_agent, "_run_mcp_smoke", lambda *_args, **_kwargs: ([], True))

    with pytest.raises(RuntimeError, match="missing asset_inventory"):
        smoke_agent.smoke(agent)


def test_smoke_agent_error_log_has_a_real_subprocess_handle() -> None:
    with smoke_agent._open_error_log() as error_log:
        assert isinstance(error_log.fileno(), int)
