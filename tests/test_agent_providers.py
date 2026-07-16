from __future__ import annotations

from pathlib import Path

from lpm.core.agent_providers import detect_agents
from lpm.core.platforms import PLATFORM_PRESETS
from lpm.core.tool_adapters import TOOL_ADAPTERS, stable_tool_adapters
from lpm.services.env_manager import TOOL_SPECS


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


def test_internal_adapter_registry_is_the_single_source_for_tool_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("lpm.core.agent_providers.shutil.which", lambda *_args, **_kwargs: None)
    adapter_ids = {adapter.id for adapter in TOOL_ADAPTERS}
    detection_ids = {
        detection.provider.id
        for detection in detect_agents(home=tmp_path / "missing-home")
    }

    assert detection_ids == adapter_ids
    assert {spec.id for spec in TOOL_SPECS} == {
        adapter.id for adapter in TOOL_ADAPTERS if adapter.discovery_root
    }
    assert set(PLATFORM_PRESETS) == {
        adapter.id for adapter in TOOL_ADAPTERS if adapter.expose_platform_preset
    }
    assert {adapter.id for adapter in stable_tool_adapters()} == {
        "codex",
        "claude-code",
        "cursor",
    }
