"""Tests for the platforms module."""

from __future__ import annotations

from lpm.platforms import (
    PLATFORM_PRESETS,
    PlatformProfile,
    PlatformsConfig,
    build_platform,
    load_platforms_from_dict,
)


def test_presets_exist() -> None:
    assert "cursor" in PLATFORM_PRESETS
    assert "claude-code" in PLATFORM_PRESETS
    assert PLATFORM_PRESETS["cursor"].skills_dir == "~/.cursor/skills"
    assert PLATFORM_PRESETS["claude-code"].skills_dir == "~/.claude/skills"


def test_build_platform_from_preset() -> None:
    p = build_platform("cursor")
    assert p.name == "cursor"
    assert p.enabled is True
    assert "cursor" in p.skills_dir


def test_build_platform_with_overrides() -> None:
    p = build_platform("cursor", {"skills_dir": "/custom/path", "enabled": False})
    assert p.skills_dir == "/custom/path"
    assert p.enabled is False


def test_build_platform_custom() -> None:
    p = build_platform("my-tool", {"skills_dir": "~/.my-tool/skills", "enabled": True})
    assert p.name == "my-tool"
    assert p.enabled is True
    assert p.skills_dir == "~/.my-tool/skills"


def test_resolve_install_path() -> None:
    p = PlatformProfile(
        name="test",
        skills_dir="/tmp/skills",
        mcp_json="/tmp/mcp.json",
        rules_dir="/tmp/rules",
    )
    assert str(p.resolve_install_path("skill", "my-skill")).endswith("my-skill")
    assert str(p.resolve_install_path("mcp", "my-mcp")).endswith("mcp.json")
    assert str(p.resolve_install_path("rule", "my-rule")).endswith("my-rule")


def test_resolve_install_path_none() -> None:
    p = PlatformProfile(name="empty")
    assert p.resolve_install_path("skill", "x") is None
    assert p.resolve_install_path("mcp", "x") is None
    assert p.resolve_install_path("rule", "x") is None


def test_platforms_config_enabled() -> None:
    cfg = PlatformsConfig(profiles=[
        PlatformProfile(name="a", enabled=True, skills_dir="/a"),
        PlatformProfile(name="b", enabled=False, skills_dir="/b"),
        PlatformProfile(name="c", enabled=True, skills_dir="/c"),
    ])
    enabled = cfg.enabled()
    assert [p.name for p in enabled] == ["a", "c"]


def test_platforms_config_get() -> None:
    cfg = PlatformsConfig(profiles=[
        PlatformProfile(name="cursor", skills_dir="/c"),
    ])
    assert cfg.get("cursor").name == "cursor"
    assert cfg.get("missing") is None


def test_load_platforms_from_dict_with_config() -> None:
    data = {
        "platforms": {
            "cursor": {"enabled": True, "skills_dir": "~/.cursor/skills"},
            "claude-code": {"enabled": True, "skills_dir": "~/.claude/skills"},
        }
    }
    cfg = load_platforms_from_dict(data)
    assert len(cfg.profiles) == 2
    assert cfg.profiles[0].name == "cursor"
    assert cfg.profiles[1].name == "claude-code"


def test_load_platforms_from_dict_empty() -> None:
    """If no platforms configured, default to cursor."""
    cfg = load_platforms_from_dict({})
    assert len(cfg.profiles) == 1
    assert cfg.profiles[0].name == "cursor"
    assert cfg.profiles[0].enabled is True


def test_primary_skills_path() -> None:
    cfg = PlatformsConfig(profiles=[
        PlatformProfile(name="a", enabled=False, skills_dir="/a"),
        PlatformProfile(name="b", enabled=True, skills_dir="/b/skills"),
    ])
    assert str(cfg.primary_skills_path()).endswith("skills")
