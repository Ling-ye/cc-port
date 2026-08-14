from __future__ import annotations

import json
from pathlib import Path

import cc_port.services.env_manager as env_manager
import cc_port.services.resource_discovery as resource_discovery
from cc_port.core.config import Config
from cc_port.core.platforms import PlatformProfile, PlatformsConfig
from cc_port.services.resource_discovery import discover_resources


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


def test_gemini_discovery_only_adapter_keeps_commands_and_rules(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    command = home / ".gemini" / "commands" / "review.md"
    rule = home / ".gemini" / "rules" / "safety.md"
    command.parent.mkdir(parents=True)
    rule.parent.mkdir(parents=True)
    command.write_text("# Review command\n", encoding="utf-8")
    rule.write_text("# Safety rule\n", encoding="utf-8")

    result = env_manager.discover_environment(home=home)

    gemini = next(tool for tool in result.tools if tool.id == "gemini")
    assert gemini.detected is True
    assert {
        (resource.kind, resource.name_hint, resource.path)
        for resource in result.resources
        if resource.tool == "gemini"
    } == {
        ("prompt", "review", command.resolve()),
        ("rule", "safety", rule.resolve()),
    }


def test_plugin_discovery_keeps_same_tool_windows_and_wsl_instances_separate(
    tmp_path: Path,
) -> None:
    profiles: list[PlatformProfile] = []
    expected: set[tuple[str, str, str, Path]] = set()
    for name, environment_kind in (
        ("claude-windows", "windows"),
        ("claude-wsl-ubuntu", "wsl"),
    ):
        home = tmp_path / name
        plugin = home / ".claude" / "skills" / "same-plugin"
        manifest = plugin / ".claude-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": "same-plugin"}), encoding="utf-8")
        profiles.append(
            PlatformProfile(
                name=name,
                tool_id="claude-code",
                environment_kind=environment_kind,
                environment_name="Ubuntu" if environment_kind == "wsl" else "",
                home_dir=str(home),
                skills_dir="~/.claude/skills",
                plugins_dir="",
            )
        )
        expected.add((name, "claude-code", environment_kind, plugin.resolve()))

    result = env_manager.discover_environment(
        home=tmp_path / "unused",
        config=Config(platforms=PlatformsConfig(profiles=profiles)),
    )

    matches = [item for item in result.plugins if item.plugin_id == "same-plugin"]
    assert {
        (item.platform, item.tool_id, item.environment_kind, item.path)
        for item in matches
    } == expected
    assert len({item.id for item in matches}) == 2


def test_project_instruction_files_are_detected_but_observation_only(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    files = [
        project / "CLAUDE.md",
        project / "CLAUDE.local.md",
        project / ".claude" / "CLAUDE.md",
        project / "AGENTS.md",
        project / "AGENTS.override.md",
    ]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.name}\n", encoding="utf-8")

    resources = discover_resources(scope="directory", root_path=project)
    instructions = [resource for resource in resources if resource.kind == "instruction"]

    assert {resource.path for resource in instructions} == {path.resolve() for path in files}
    assert all(resource.status == "blocked" for resource in instructions)
    assert all("observation-only" in " ".join(resource.blockers) for resource in instructions)


def test_project_claude_rules_are_not_promoted_to_user_rules(tmp_path: Path) -> None:
    project = tmp_path / "project"
    rule = project / ".claude" / "rules" / "security" / "CLAUDE.md"
    rule.parent.mkdir(parents=True)
    rule.write_text("# Project-only security rule\n", encoding="utf-8")

    resources = discover_resources(scope="directory", root_path=project)
    candidate = next(resource for resource in resources if resource.path == rule.resolve())

    assert candidate.kind == "rule"
    assert candidate.status == "blocked"
    assert "cannot be promoted to user rules" in " ".join(candidate.blockers)


def test_global_generic_scan_defers_claude_user_instruction_to_profiles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    claude = tmp_path / ".claude"
    instruction = claude / "CLAUDE.md"
    rule = claude / "rules" / "nested" / "CLAUDE.md"
    rule.parent.mkdir(parents=True)
    instruction.write_text("# User instruction\n", encoding="utf-8")
    rule.write_text("# User rule with an instruction-like basename\n", encoding="utf-8")
    monkeypatch.setattr(
        resource_discovery,
        "_roots_for_scope",
        lambda **_kwargs: [
            ("claude-code", claude / "rules"),
            ("claude-code", instruction),
        ],
    )

    resources = discover_resources(scope="global")

    assert {(item.path, item.kind) for item in resources} == {
        (rule.resolve(), "rule"),
    }


def test_global_generic_scan_defers_codex_user_instruction_to_profiles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex = tmp_path / ".codex"
    codex.mkdir()
    base = codex / "AGENTS.md"
    override = codex / "AGENTS.override.md"
    base.write_text("# Base\n", encoding="utf-8")
    override.write_text("  \n", encoding="utf-8")
    monkeypatch.setattr(
        resource_discovery,
        "_roots_for_scope",
        lambda **_kwargs: [("codex", codex)],
    )

    assert not discover_resources(scope="global")

    base.write_text("\n", encoding="utf-8")
    assert not [
        item
        for item in discover_resources(scope="global")
        if item.kind == "instruction"
    ]
