from __future__ import annotations

from pathlib import Path

from lpm.core.agent_providers import detect_agents


def test_provider_detection_uses_strong_and_soft_signals(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / ".codex" / "skills").mkdir(parents=True)
    (home / ".vscode" / "extensions").mkdir(parents=True)

    monkeypatch.setattr("lpm.core.agent_providers.shutil.which", lambda *_args, **_kwargs: None)

    detections = {item.provider.id: item for item in detect_agents(home=home)}

    assert detections["codex"].detected is True
    assert detections["codex"].auto_install is True
    assert detections["cline"].detected is True
    assert detections["cline"].auto_install is False


def test_provider_detection_uses_command_signal(tmp_path: Path, monkeypatch) -> None:
    def fake_which(command: str, **_kwargs):
        return str(tmp_path / f"{command}.cmd") if command == "opencode" else None

    monkeypatch.setattr("lpm.core.agent_providers.shutil.which", fake_which)

    detections = {item.provider.id: item for item in detect_agents(home=tmp_path / "home")}

    assert detections["opencode"].detected is True
    assert detections["opencode"].auto_install is True
