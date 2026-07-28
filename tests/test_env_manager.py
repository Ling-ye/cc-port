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
