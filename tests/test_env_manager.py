from __future__ import annotations

import json
from pathlib import Path

import cc_port.services.env_manager as env_manager
from cc_port.core.config import Config
from cc_port.core.platforms import PlatformProfile, PlatformsConfig


def test_discovery_finds_resources_and_marks_mcp_secret_fields(tmp_path: Path) -> None:
    home = tmp_path / "home"
    skill = home / ".cursor" / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill\n---\n",
        encoding="utf-8",
    )
    (home / ".cursor" / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "command": "npx",
                        "env": {
                            "GITHUB_TOKEN": "secret-value",
                            "PLACEHOLDER": "${SAFE_VALUE}",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = env_manager.discover_environment(home=home)

    cursor = next(tool for tool in result.tools if tool.id == "cursor")
    assert cursor.detected is True
    assert "skill" in cursor.supports_kinds
    assert [(item.kind, item.name_hint) for item in result.resources] == [("skill", "demo")]
    assert [(item.name, item.secret_keys) for item in result.mcp_servers] == [
        ("github", ["GITHUB_TOKEN"])
    ]


def test_discovery_finds_prompt_in_configured_cursor_directory(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    prompts = home / "custom-prompt-assets"
    prompts.mkdir(parents=True)
    prompt = prompts / "local-only.md"
    prompt.write_text("# Local command\n", encoding="utf-8")
    config = Config(
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="cursor",
                    enabled=True,
                    prompts_dir="~/custom-prompt-assets",
                )
            ]
        )
    )

    result = env_manager.discover_environment(home=home, config=config)

    matches = [
        item
        for item in result.resources
        if item.tool == "cursor" and item.kind == "prompt"
    ]
    assert [(item.name_hint, item.path) for item in matches] == [
        ("local-only", prompt.resolve())
    ]


def test_discovery_finds_skill_in_configured_claude_directory(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    skills = tmp_path / "wsl-claude-skills"
    skill = skills / "local-only"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: local-only\ndescription: WSL Claude skill\n---\n",
        encoding="utf-8",
    )
    config = Config(
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="claude-code",
                    enabled=True,
                    skills_dir=str(skills),
                )
            ]
        )
    )

    result = env_manager.discover_environment(home=home, config=config)

    matches = [
        item
        for item in result.resources
        if item.tool == "claude-code" and item.kind == "skill"
    ]
    assert [(item.name_hint, item.path) for item in matches] == [
        ("local-only", skill.resolve())
    ]


def test_discovery_finds_mcp_in_configured_claude_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    mcp_json = tmp_path / "wsl-claude" / "mcp.json"
    mcp_json.parent.mkdir(parents=True)
    mcp_json.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "wsl-local": {
                        "command": "printf",
                        "args": ["ready"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config = Config(
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="claude-code",
                    enabled=True,
                    mcp_json=str(mcp_json),
                )
            ]
        )
    )

    result = env_manager.discover_environment(home=home, config=config)

    matches = [
        item
        for item in result.mcp_servers
        if item.tool == "claude-code" and item.name == "wsl-local"
    ]
    assert [(item.config_path, item.config) for item in matches] == [
        (
            mcp_json.resolve(),
            {"command": "printf", "args": ["ready"]},
        )
    ]


def test_discovery_finds_plugin_in_configured_claude_directory(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    plugin = tmp_path / "wsl-claude-plugins" / "local-only"
    manifest = plugin / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "name": "local-only",
                "version": "1.0.0",
                "description": "WSL Claude plugin",
            }
        ),
        encoding="utf-8",
    )
    config = Config(
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="claude-code",
                    enabled=True,
                    plugins_dir=str(plugin.parent),
                )
            ]
        )
    )

    result = env_manager.discover_environment(home=home, config=config)

    matches = [
        item
        for item in result.plugins
        if item.platform == "claude-code" and item.plugin_id == "local-only"
    ]
    assert [(item.track, item.path, item.origin_source) for item in matches] == [
        ("content", plugin.resolve(), "local-only")
    ]


def test_configured_default_commands_directory_is_not_scanned_twice(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    commands = home / ".cursor" / "commands"
    commands.mkdir(parents=True)
    prompt = commands / "deduplicated.md"
    prompt.write_text("# Deduplicated command\n", encoding="utf-8")
    config = Config(
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="cursor",
                    enabled=True,
                    prompts_dir="~/.cursor/commands",
                )
            ]
        )
    )

    result = env_manager.discover_environment(home=home, config=config)

    matches = [
        item
        for item in result.resources
        if item.tool == "cursor"
        and item.kind == "prompt"
        and item.path == prompt.resolve()
    ]
    assert len(matches) == 1


def test_configured_default_skills_directory_is_not_scanned_twice(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    skill = home / ".claude" / "skills" / "deduplicated"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: deduplicated\ndescription: Deduplicated skill\n---\n",
        encoding="utf-8",
    )
    config = Config(
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="claude-code",
                    enabled=True,
                    skills_dir="~/.claude/skills",
                )
            ]
        )
    )

    result = env_manager.discover_environment(home=home, config=config)

    matches = [
        item
        for item in result.resources
        if item.tool == "claude-code"
        and item.kind == "skill"
        and item.path == skill.resolve()
    ]
    assert len(matches) == 1


def test_removed_environment_mutation_api_is_not_exposed() -> None:
    removed = {
        "capture_environment",
        "export_environment_snapshot",
        "build_env_push_diff",
        "apply_env_push",
        "build_env_pull_diff",
        "apply_env_pull",
        "build_env_import_diff",
        "apply_env_import",
        "build_deploy_plan",
        "deploy_environment",
    }

    assert all(not hasattr(env_manager, name) for name in removed)
