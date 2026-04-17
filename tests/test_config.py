"""Tests for config loading and writing with platform support."""

from __future__ import annotations

from pathlib import Path

from skillhub.config import Config, GithubConfig, InstallConfig, load_config, write_config
from skillhub.platforms import PlatformProfile, PlatformsConfig


def test_write_and_load_with_platforms(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg = Config(
        github=GithubConfig(token="tok", owner="alice"),
        install=InstallConfig(target="~/.cursor/skills"),
        platforms=PlatformsConfig(profiles=[
            PlatformProfile(name="cursor", enabled=True, skills_dir="~/.cursor/skills", mcp_json="~/.cursor/mcp.json"),
            PlatformProfile(name="claude-code", enabled=True, skills_dir="~/.claude/skills", mcp_json="~/.claude.json"),
        ]),
    )
    write_config(cfg, cfg_path)
    text = cfg_path.read_text()
    assert "[platforms.cursor]" in text
    assert "[platforms.claude-code]" in text

    loaded = load_config(cfg_path)
    assert len(loaded.platforms.profiles) == 2
    assert loaded.platforms.profiles[0].name == "cursor"
    assert loaded.platforms.profiles[1].name == "claude-code"
    assert loaded.platforms.profiles[1].skills_dir == "~/.claude/skills"


def test_load_without_platforms(tmp_path: Path) -> None:
    """Config without [platforms] should default to cursor."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        '[github]\ntoken = ""\n\n[install]\ntarget = "~/.cursor/skills"\n',
        encoding="utf-8",
    )
    loaded = load_config(cfg_path)
    assert len(loaded.platforms.profiles) == 1
    assert loaded.platforms.profiles[0].name == "cursor"


def test_load_nonexistent_config(tmp_path: Path) -> None:
    """Loading a nonexistent config should return sensible defaults."""
    loaded = load_config(tmp_path / "nonexistent.toml")
    assert loaded.github.token == ""
    assert len(loaded.platforms.profiles) == 1
