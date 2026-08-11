from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "tools" / "packaging" / "agent" / "build_agent.py"
)
MODULE_SPEC = importlib.util.spec_from_file_location("cc_port_build_agent", MODULE_PATH)
assert MODULE_SPEC and MODULE_SPEC.loader
build_agent = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(build_agent)


def test_agent_pyinstaller_command_includes_mcp_and_package_data(tmp_path: Path) -> None:
    command = build_agent.build_pyinstaller_command(
        "python-for-agent",
        tmp_path / "work",
        clean=True,
    )

    assert command[:5] == [
        "python-for-agent",
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconfirm",
    ]
    assert "--clean" in command
    assert _option_values(command, "--collect-submodules") == ["cc_port", "fastmcp", "mcp"]
    assert _option_values(command, "--collect-data") == ["cc_port"]
    assert _option_values(command, "--copy-metadata") == ["fastmcp", "mcp"]
    assert "fastmcp" not in _option_values(command, "--exclude-module")
    assert "mcp" not in _option_values(command, "--exclude-module")
    assert command[-1] == str(build_agent.ENTRY_SCRIPT)


@pytest.mark.parametrize("clean", [True, False])
def test_agent_build_copies_target_named_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean: bool,
) -> None:
    root = tmp_path / "repo"
    out = tmp_path / "tauri-binaries"
    monkeypatch.setattr(build_agent, "ROOT", root)
    monkeypatch.setattr(build_agent.sys, "executable", "python-for-agent")
    monkeypatch.setattr(build_agent.sysconfig, "get_config_var", lambda _name: ".exe")
    monkeypatch.setattr(build_agent, "ensure_pyinstaller", lambda _python: None)

    def fake_run(command: list[str], *, check: bool):
        assert check is False
        dist = root / "build" / "agent" / "dist"
        dist.mkdir(parents=True)
        (dist / "cc-port.exe").write_bytes(b"agent")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(build_agent.subprocess, "run", fake_run)

    result = build_agent.build("x86_64-pc-windows-msvc", out, clean=clean)

    assert result == out / "cc-port-x86_64-pc-windows-msvc.exe"
    assert result.read_bytes() == b"agent"


def _option_values(command: list[str], option: str) -> list[str]:
    return [command[index + 1] for index, value in enumerate(command[:-1]) if value == option]
