from __future__ import annotations

from collections.abc import Callable

import pytest

from cc_port.core.config import Config
from cc_port.core.platforms import PlatformProfile, PlatformsConfig, build_platform
from cc_port.interfaces import cli, desktop_api, mcp_server


@pytest.fixture
def runtime_config() -> Config:
    return Config(
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="claude-windows",
                    tool_id="claude-code",
                    environment_kind="windows",
                ),
                PlatformProfile(
                    name="codex-wsl",
                    tool_id="codex",
                    environment_kind="wsl",
                    environment_name="Ubuntu",
                ),
                PlatformProfile(
                    name="private-runtime",
                    tool_id="configured-custom-tool",
                ),
            ]
        )
    )


@pytest.fixture(params=["cli", "desktop", "mcp"])
def normalize(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    runtime_config: Config,
) -> Callable[[str, list[str]], list[str] | None]:
    if request.param == "cli":
        return lambda kind, values: cli._portable_resource_platforms(
            runtime_config,
            kind,
            values,
        )
    if request.param == "desktop":
        return lambda kind, values: desktop_api._portable_resource_platforms(
            runtime_config,
            kind,  # type: ignore[arg-type]
            values,
        )
    monkeypatch.setattr(mcp_server, "load_config", lambda: runtime_config)
    return lambda kind, values: mcp_server._portable_resource_platforms(kind, values)


def test_portable_binding_matrix_is_consistent_across_public_interfaces(
    normalize: Callable[[str, list[str]], list[str] | None],
) -> None:
    assert normalize("instruction", [" claude-windows ", "claude-code"]) == [
        "claude-code"
    ]
    assert normalize("skill", ["private-runtime"]) == ["configured-custom-tool"]
    assert normalize("memory", ["codex-wsl"]) == ["codex"]

    with pytest.raises(ValueError, match="exactly one portable source tool"):
        normalize("instruction", ["claude-windows", "codex-wsl"])
    with pytest.raises(ValueError, match="explicit portable tool binding"):
        normalize("memory", [])
    with pytest.raises(ValueError, match="exactly one supported source tool"):
        normalize("memory", ["claude-windows", "codex-wsl"])
    with pytest.raises(ValueError, match="Unknown platform"):
        normalize("skill", ["claude-wsl-typo"])


@pytest.mark.parametrize(
    "unsafe_tool_id",
    [r"C:\\Users\\alice", "../claude", "claude/code", "claude:code", "bad\nname"],
)
def test_configured_tool_ids_reject_paths_and_control_characters(
    unsafe_tool_id: str,
) -> None:
    with pytest.raises(ValueError, match="portable tool ids"):
        build_platform(
            "local-profile",
            {"tool_id": unsafe_tool_id},
        )


def test_runtime_home_must_be_explicitly_absolute() -> None:
    with pytest.raises(ValueError, match="home_dir must be"):
        build_platform(
            "claude-wsl",
            {
                "tool_id": "claude-code",
                "home_dir": "relative/wsl-home",
            },
        )


def test_direct_profile_with_unsafe_tool_id_cannot_be_normalized(
    runtime_config: Config,
) -> None:
    runtime_config.platforms.profiles.append(
        PlatformProfile(name="unsafe-profile", tool_id=r"C:\\Users\\alice")
    )
    with pytest.raises(ValueError, match="portable tool ids"):
        desktop_api._portable_resource_platforms(
            runtime_config,
            "skill",
            ["unsafe-profile"],
        )


def test_remote_allowlists_never_match_local_profile_ids(
    runtime_config: Config,
) -> None:
    profile = runtime_config.platforms.get("claude-windows")
    assert profile is not None
    assert profile.supports_resource_platforms(["claude-code"]) is True
    assert profile.supports_resource_platforms(["claude-windows"]) is False


def test_memory_project_slots_are_unique_within_each_profile() -> None:
    with pytest.raises(ValueError, match="must be unique"):
        build_platform(
            "claude-windows",
            {
                "tool_id": "claude-code",
                "memory_install_names": {
                    "first-memory": "same-slot",
                    "second-memory": "same-slot",
                },
            },
        )

    direct = PlatformProfile(
        name="claude-direct-construction",
        tool_id="claude-code",
        memories_dir="/tmp/claude-projects",
        memory_install_names={
            "first-memory": "same-slot",
            "second-memory": "same-slot",
        },
    )
    with pytest.raises(ValueError, match="must be unique"):
        direct.resolve_install_path("memory", "first-memory")
