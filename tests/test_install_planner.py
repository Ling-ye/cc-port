from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_port.core.config import Config, InstallConfig, PlatformsConfig, ResourcesConfig
from cc_port.core.models import (
    PluginInstallation,
    PluginOrigin,
    PluginSpec,
    Registry,
    RegistryItem,
)
from cc_port.core.ownership import is_cc_port_managed, managed_marker_path
from cc_port.core.platforms import PlatformProfile
from cc_port.core.registry import save_registry
from cc_port.services import installer, resource_manager
from cc_port.services.install_planner import copy_resource_tree, load_resource_manifest
from cc_port.services.mcp_installer import inject_mcp_server
from cc_port.services.operation_state import load_operation


def _config(root: Path, install: Path, *, platforms: list[PlatformProfile] | None = None) -> Config:
    return Config(
        install=InstallConfig(target=str(install)),
        resources=ResourcesConfig(local_path=str(root)),
        platforms=PlatformsConfig(profiles=platforms or []),
    )


def test_copy_resource_tree_filters_redundant_directories(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    (src / "node_modules" / "pkg").mkdir(parents=True)
    (src / ".venv" / "lib").mkdir(parents=True)
    (src / "dist").mkdir(parents=True)
    (src / "SKILL.md").write_text("---\nname: demo\ndescription: demo\n---\n", encoding="utf-8")
    (src / "notes.md").write_text("keep", encoding="utf-8")
    (src / "node_modules" / "pkg" / "index.js").write_text("drop", encoding="utf-8")
    (src / ".venv" / "lib" / "x.py").write_text("drop", encoding="utf-8")
    (src / "dist" / "bundle.js").write_text("drop", encoding="utf-8")
    (src / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (src / ".env.local").write_text("TOKEN=secret", encoding="utf-8")
    (src / ".env.example").write_text("TOKEN=${TOKEN}", encoding="utf-8")

    copy_resource_tree(src, dest)

    assert (dest / "SKILL.md").is_file()
    assert (dest / "notes.md").is_file()
    assert not (dest / "node_modules").exists()
    assert not (dest / ".venv").exists()
    assert not (dest / "dist").exists()
    assert not (dest / ".env").exists()
    assert not (dest / ".env.local").exists()
    assert (dest / ".env.example").is_file()


def test_install_plan_and_sync_respect_resource_platform_allowlist(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    skill = root / "skills" / "cursor-only"
    install = tmp_path / "install"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: cursor-only\ndescription: Cursor-only skill\n---\n",
        encoding="utf-8",
    )
    entry = RegistryItem(
        name="cursor-only",
        kind="skill",
        source="local",
        path="skills/cursor-only",
        platforms=["cursor"],
    )
    registry_path = root / "registry.yaml"
    save_registry(Registry(items=[entry]), registry_path)
    cursor_skills = tmp_path / "cursor" / "skills"
    codex_skills = tmp_path / "codex" / "skills"
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(name="cursor", enabled=True, skills_dir=str(cursor_skills)),
            PlatformProfile(name="codex", enabled=True, skills_dir=str(codex_skills)),
        ],
    )

    plan = resource_manager.resource_install_plan(
        "cursor-only",
        config=cfg,
        registry_path=registry_path,
    )
    result = installer.sync_one(entry, config=cfg, registry_root=root)

    assert [target.platform for target in plan.targets] == ["cursor"]
    assert result.platforms_installed == ["cursor"]
    assert (cursor_skills / "cursor-only" / "SKILL.md").is_file()
    assert not (codex_skills / "cursor-only").exists()


def test_install_plan_warns_for_disallowed_platform_filter(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    skill = root / "skills" / "cursor-only"
    install = tmp_path / "install"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Cursor only\n", encoding="utf-8")
    entry = RegistryItem(
        name="cursor-only",
        kind="skill",
        source="local",
        path="skills/cursor-only",
        platforms=["cursor"],
    )
    registry_path = root / "registry.yaml"
    save_registry(Registry(items=[entry]), registry_path)
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="codex",
                enabled=True,
                skills_dir=str(tmp_path / "codex" / "skills"),
            )
        ],
    )

    plan = resource_manager.resource_install_plan(
        "cursor-only",
        config=cfg,
        registry_path=registry_path,
        platform_filter="codex",
    )

    assert plan.targets == []
    assert plan.warnings == ["Resource is not allowed on platform 'codex'."]


def test_manifest_limits_copied_resource_paths(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    (src / "SKILL.md").write_text("---\nname: demo\ndescription: demo\n---\n", encoding="utf-8")
    (src / "keep").mkdir()
    (src / "keep" / "tool.md").write_text("keep", encoding="utf-8")
    (src / "node_modules" / "pkg").mkdir(parents=True)
    (src / "node_modules" / "pkg" / "index.js").write_text("drop", encoding="utf-8")
    (src / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (src / "extra.md").write_text("drop", encoding="utf-8")
    (src / "cc-port.resource.json").write_text(
        json.dumps(
            {
                "skills": ["SKILL.md"],
                "commands": ["keep", "node_modules/pkg/index.js"],
                "hooks": [".env"],
            }
        ),
        encoding="utf-8",
    )

    manifest = load_resource_manifest(src)
    copy_resource_tree(src, dest, manifest=manifest)

    assert (dest / "SKILL.md").is_file()
    assert (dest / "keep" / "tool.md").is_file()
    assert not (dest / "extra.md").exists()
    assert not (dest / "node_modules").exists()
    assert not (dest / ".env").exists()
    assert (dest / "cc-port.resource.json").is_file()


def test_install_plan_maps_plugin_to_enabled_plugin_target(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    plugin = root / "plugins" / "demo-plugin"
    install = tmp_path / "install"
    plugin_target = tmp_path / "opencode" / "plugins"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text("{}", encoding="utf-8")
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="demo-plugin",
                    kind="plugin",
                    source="local",
                    path="plugins/demo-plugin",
                )
            ]
        ),
        registry_path,
    )
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="opencode",
                enabled=True,
                plugins_dir=str(plugin_target),
            )
        ],
    )

    plan = resource_manager.resource_install_plan(
        "demo-plugin",
        config=cfg,
        registry_path=registry_path,
    )

    assert plan.targets[0].platform == "opencode"
    assert plan.targets[0].install_mechanism == "native_plugin_commands_agents"
    assert plan.targets[0].path == plugin_target / "demo-plugin"


def test_sync_installs_plugin_to_platform_plugin_dir(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    plugin = root / "plugins" / "demo-plugin"
    install = tmp_path / "install"
    plugin_target = tmp_path / "opencode" / "plugins"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text("{}", encoding="utf-8")
    entry = RegistryItem(
        name="demo-plugin",
        kind="plugin",
        source="local",
        path="plugins/demo-plugin",
    )
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="opencode",
                enabled=True,
                plugins_dir=str(plugin_target),
            )
        ],
    )

    result = installer.sync_one(entry, config=cfg, registry_root=root)

    assert result.action == installer.SyncAction.INSTALLED
    assert result.platforms_installed == ["opencode"]
    assert (plugin_target / "demo-plugin" / "plugin.json").is_file()


def test_legacy_sync_defers_claude_marketplace_reference_to_asset_workflow(
    tmp_path: Path,
) -> None:
    entry = RegistryItem(
        name="claude-marketplace-review-tools",
        kind="plugin",
        source="external",
        plugin=PluginSpec(
            track="reference",
            platform="claude-code",
            plugin_id="review-tools",
            origin=PluginOrigin(
                type="marketplace",
                marketplace="team-tools",
                source="acme/claude-plugins",
            ),
            installations=[PluginInstallation(scope="user", enabled=True)],
        ),
    )
    profile = PlatformProfile(
        name="claude-windows",
        tool_id="claude-code",
        home_dir=str(tmp_path / "home"),
        skills_dir="~/.claude/skills",
        settings_path="~/.claude/settings.json",
    )
    cfg = _config(
        tmp_path / "resources",
        tmp_path / "install",
        platforms=[profile],
    )

    plan = installer.create_install_plan(entry, config=cfg)
    result = installer.sync_one(entry, config=cfg)

    assert plan.targets == []
    assert "native installer" in " ".join(plan.warnings)
    assert result.action == installer.SyncAction.SKIPPED
    assert "profile-aware asset sync" in result.detail
    assert not profile.skills_path().exists()


def test_legacy_sync_defers_instruction_to_environment_aware_asset_sync(
    tmp_path: Path,
) -> None:
    root = tmp_path / "resources"
    source = root / "instructions" / "claude-code-user-instructions"
    source.mkdir(parents=True)
    (source / "CLAUDE.md").write_text("# Portable instruction\n", encoding="utf-8")
    target = tmp_path / "claude-home" / ".claude" / "CLAUDE.md"
    entry = RegistryItem(
        name="claude-code-user-instructions",
        kind="instruction",
        source="local",
        path="instructions/claude-code-user-instructions",
        platforms=["claude-code"],
    )
    cfg = _config(
        root,
        tmp_path / "install",
        platforms=[
            PlatformProfile(
                name="claude-wsl",
                tool_id="claude-code",
                environment_kind="wsl",
                home_dir=str(tmp_path / "claude-home"),
                instructions_path="~/.claude/CLAUDE.md",
            )
        ],
    )

    result = installer.sync_one(entry, config=cfg, registry_root=root)

    assert result.action == installer.SyncAction.SKIPPED
    assert "environment-aware asset sync" in result.detail
    assert not target.exists()
    assert not managed_marker_path(target, file_target=True).exists()
    assert installer.uninstall_one(entry, config=cfg) is False


def test_legacy_sync_defers_memory_to_environment_aware_asset_sync(
    tmp_path: Path,
) -> None:
    root = tmp_path / "resources"
    source = root / "memories" / "shared-memory"
    source.mkdir(parents=True)
    (source / "MEMORY.md").write_text("# Portable memory\n", encoding="utf-8")
    (source / "topic.md").write_text("# Topic\n", encoding="utf-8")
    projects = tmp_path / "claude-home" / ".claude" / "projects"
    target = projects / "slot-b" / "memory"
    entry = RegistryItem(
        name="shared-memory",
        kind="memory",
        source="local",
        path="memories/shared-memory",
        platforms=["claude-code"],
    )
    cfg = _config(
        root,
        tmp_path / "install",
        platforms=[
            PlatformProfile(
                name="claude-wsl",
                tool_id="claude-code",
                environment_kind="wsl",
                home_dir=str(tmp_path / "claude-home"),
                memories_dir="~/.claude/projects",
                memory_install_names={
                    "shared-memory": "slot-b",
                    "slot-b": "slot-c",
                },
            )
        ],
    )

    result = installer.sync_one(entry, config=cfg, registry_root=root)

    plan = installer.create_install_plan(
        entry,
        config=cfg,
        registry_root=root,
    )
    assert result.action == installer.SyncAction.SKIPPED
    assert plan.targets == []
    assert "environment-aware asset sync" in " ".join(plan.warnings)
    assert not target.exists()
    assert not managed_marker_path(target, file_target=True).exists()
    assert not (projects / "slot-c" / "memory").exists()


def test_legacy_memory_sync_requires_mapping_for_new_projects_target(
    tmp_path: Path,
) -> None:
    root = tmp_path / "resources"
    source = root / "memories" / "shared-memory"
    source.mkdir(parents=True)
    (source / "MEMORY.md").write_text("# Portable memory\n", encoding="utf-8")
    projects = tmp_path / "claude-home" / ".claude" / "projects"
    entry = RegistryItem(
        name="shared-memory",
        kind="memory",
        source="local",
        path="memories/shared-memory",
        platforms=["claude-code"],
    )
    cfg = _config(
        root,
        tmp_path / "install",
        platforms=[
            PlatformProfile(
                name="claude-wsl",
                tool_id="claude-code",
                environment_kind="wsl",
                home_dir=str(tmp_path / "claude-home"),
                memories_dir="~/.claude/projects",
            )
        ],
    )

    plan = installer.create_install_plan(entry, config=cfg, registry_root=root)
    result = installer.sync_one(entry, config=cfg, registry_root=root)

    assert plan.targets == []
    assert "environment-aware asset sync" in " ".join(plan.warnings)
    assert result.action == installer.SyncAction.SKIPPED
    assert not (projects / entry.name / "memory").exists()


def test_legacy_memory_sync_never_bypasses_direct_runtime_settings(
    tmp_path: Path,
) -> None:
    root = tmp_path / "resources"
    source = root / "memories" / "shared-memory"
    source.mkdir(parents=True)
    (source / "MEMORY.md").write_text("# Direct memory\n", encoding="utf-8")
    target = tmp_path / "claude-home" / "direct-memory"
    entry = RegistryItem(
        name="shared-memory",
        kind="memory",
        source="local",
        path="memories/shared-memory",
        platforms=["claude-code"],
    )
    cfg = _config(
        root,
        tmp_path / "install",
        platforms=[
            PlatformProfile(
                name="claude-wsl",
                tool_id="claude-code",
                environment_kind="wsl",
                home_dir=str(tmp_path / "claude-home"),
                memories_dir="~/direct-memory",
                memory_layout="direct",
            )
        ],
    )

    result = installer.sync_one(entry, config=cfg, registry_root=root)

    assert result.action == installer.SyncAction.SKIPPED
    assert "environment-aware asset sync" in result.detail
    assert not target.exists()


def test_legacy_sync_skips_instruction_without_explicit_tool_binding(
    tmp_path: Path,
) -> None:
    root = tmp_path / "resources"
    source = root / "instructions" / "unbound"
    source.mkdir(parents=True)
    (source / "CLAUDE.md").write_text("# Must not install\n", encoding="utf-8")
    target = tmp_path / "claude-home" / ".claude" / "CLAUDE.md"
    entry = RegistryItem(
        name="unbound",
        kind="instruction",
        source="local",
        path="instructions/unbound",
    )
    cfg = _config(
        root,
        tmp_path / "install",
        platforms=[
            PlatformProfile(
                name="claude-wsl",
                tool_id="claude-code",
                home_dir=str(tmp_path / "claude-home"),
                instructions_path="~/.claude/CLAUDE.md",
            )
        ],
    )

    plan = installer.create_install_plan(entry, config=cfg, registry_root=root)
    result = installer.sync_one(entry, config=cfg, registry_root=root)

    assert plan.targets == []
    assert result.action == installer.SyncAction.SKIPPED
    assert "environment-aware asset sync" in result.detail
    assert not target.exists()


def test_codex_memory_requires_an_explicit_codex_binding(tmp_path: Path) -> None:
    profile = PlatformProfile(
        name="codex-custom",
        tool_id="codex",
        memories_dir=str(tmp_path / "must-not-write"),
        memory_layout="direct",
    )
    entry = RegistryItem(
        name="shared-memory",
        kind="memory",
        source="local",
        path="memories/shared-memory",
        platforms=["codex"],
    )
    source = tmp_path / "resources" / "memories" / "shared-memory"
    source.mkdir(parents=True)
    (source / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")

    plan = installer.create_install_plan(
        entry,
        config=_config(tmp_path / "resources", tmp_path / "install", platforms=[profile]),
        registry_root=tmp_path / "resources",
    )

    assert profile.supports_resource("memory", ["codex"]) is True
    assert profile.supports_resource("memory", []) is False
    assert plan.targets == []


def test_sync_updates_and_uninstalls_file_prompt_with_marker(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    source = root / "prompts" / "demo-prompt"
    install = tmp_path / "install"
    commands = tmp_path / "cursor" / "commands"
    source.mkdir(parents=True)
    payload = source / "command.md"
    payload.write_text("remote-v1", encoding="utf-8")
    (source / "notes.txt").write_text("ignored metadata", encoding="utf-8")
    entry = RegistryItem(
        name="demo-prompt",
        kind="prompt",
        source="local",
        path="prompts/demo-prompt",
    )
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="cursor",
                enabled=True,
                prompts_dir=str(commands),
            )
        ],
    )

    first = installer.sync_one(entry, config=cfg, registry_root=root)
    target = commands / "demo-prompt.md"
    marker = managed_marker_path(target)

    assert first.action == installer.SyncAction.INSTALLED
    assert first.operation_status == "succeeded"
    assert target.read_text(encoding="utf-8") == "remote-v1"
    assert marker.is_file()
    assert is_cc_port_managed(target, resource_key=entry.resource_key)
    operation_paths = {
        Path(item.path)
        for item in load_operation(first.operation_id).targets
    }
    assert target.absolute() in operation_paths
    assert marker.absolute() in operation_paths

    payload.write_text("remote-v2", encoding="utf-8")
    second = installer.sync_one(entry, config=cfg, registry_root=root)

    assert second.action == installer.SyncAction.INSTALLED
    assert second.operation_status == "succeeded"
    assert target.read_text(encoding="utf-8") == "remote-v2"
    assert is_cc_port_managed(target, resource_key=entry.resource_key)

    removed = installer.uninstall_one(
        entry,
        config=cfg,
        platform_filter="cursor",
    )

    assert removed is True
    assert not target.exists()
    assert not marker.exists()
    assert (install / "demo-prompt").is_dir()


def test_sync_file_prompt_rejects_multiple_root_markdown_payloads(
    tmp_path: Path,
) -> None:
    root = tmp_path / "resources"
    source = root / "prompts" / "ambiguous-prompt"
    install = tmp_path / "install"
    commands = tmp_path / "cursor" / "commands"
    source.mkdir(parents=True)
    (source / "one.md").write_text("one", encoding="utf-8")
    (source / "two.md").write_text("two", encoding="utf-8")
    entry = RegistryItem(
        name="ambiguous-prompt",
        kind="prompt",
        source="local",
        path="prompts/ambiguous-prompt",
    )
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="cursor",
                enabled=True,
                prompts_dir=str(commands),
            )
        ],
    )

    result = installer.sync_one(entry, config=cfg, registry_root=root)
    target = commands / "ambiguous-prompt.md"

    assert result.action == installer.SyncAction.FAILED
    assert result.operation_status == "rolled_back"
    assert result.rolled_back is True
    assert "exactly one root-level non-symlink .md file" in result.detail
    assert not (install / "ambiguous-prompt").exists()
    assert not target.exists()
    assert not managed_marker_path(target).exists()


def test_sync_prompt_keeps_legacy_directory_target(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    source = root / "prompts" / "legacy-prompt"
    install = tmp_path / "install"
    rules = tmp_path / "legacy" / "rules"
    source.mkdir(parents=True)
    (source / "one.md").write_text("one", encoding="utf-8")
    (source / "two.md").write_text("two", encoding="utf-8")
    entry = RegistryItem(
        name="legacy-prompt",
        kind="prompt",
        source="local",
        path="prompts/legacy-prompt",
    )
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="legacy",
                enabled=True,
                rules_dir=str(rules),
            )
        ],
    )

    result = installer.sync_one(entry, config=cfg, registry_root=root)
    target = rules / "legacy-prompt"

    assert result.action == installer.SyncAction.INSTALLED
    assert (target / "one.md").is_file()
    assert (target / "two.md").is_file()
    assert is_cc_port_managed(target, resource_key=entry.resource_key)

    assert installer.uninstall_one(
        entry,
        config=cfg,
        platform_filter="legacy",
    )
    assert not target.exists()


def test_sync_file_prompt_replaces_dangling_symlink_only_when_forced(
    tmp_path: Path,
) -> None:
    root = tmp_path / "resources"
    source = root / "prompts" / "demo-prompt"
    install = tmp_path / "install"
    commands = tmp_path / "cursor" / "commands"
    source.mkdir(parents=True)
    (source / "command.md").write_text("safe prompt", encoding="utf-8")
    entry = RegistryItem(
        name="demo-prompt",
        kind="prompt",
        source="local",
        path="prompts/demo-prompt",
    )
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="cursor",
                enabled=True,
                prompts_dir=str(commands),
            )
        ],
    )
    target = commands / "demo-prompt.md"
    outside = tmp_path / "outside.md"
    target.parent.mkdir(parents=True)
    try:
        target.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    blocked = installer.sync_one(entry, config=cfg, registry_root=root)

    assert blocked.action == installer.SyncAction.FAILED
    assert target.is_symlink()
    assert not outside.exists()

    installed = installer.sync_one(
        entry,
        config=cfg,
        registry_root=root,
        force_unmanaged=True,
    )

    assert installed.action == installer.SyncAction.INSTALLED
    assert target.is_file()
    assert not target.is_symlink()
    assert target.read_text(encoding="utf-8") == "safe prompt"
    assert not outside.exists()


def test_sync_file_prompt_directory_replacement_rolls_back_adjacent_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "resources"
    source = root / "prompts" / "demo-prompt"
    install = tmp_path / "install"
    commands = tmp_path / "cursor" / "commands"
    source.mkdir(parents=True)
    (source / "command.md").write_text("safe prompt", encoding="utf-8")
    entry = RegistryItem(
        name="demo-prompt",
        kind="prompt",
        source="local",
        path="prompts/demo-prompt",
    )
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="cursor",
                enabled=True,
                prompts_dir=str(commands),
            )
        ],
    )
    target = commands / "demo-prompt.md"
    target.mkdir(parents=True)
    sentinel = target / "keep.txt"
    sentinel.write_text("keep me", encoding="utf-8")
    marker = managed_marker_path(target, file_target=True)

    def fail_verification(*_args, **_kwargs) -> None:
        raise RuntimeError("simulated verification failure")

    monkeypatch.setattr(installer, "_verify_resource_install", fail_verification)

    result = installer.sync_one(
        entry,
        config=cfg,
        registry_root=root,
        force_unmanaged=True,
    )

    assert result.action == installer.SyncAction.FAILED
    assert result.operation_status == "rolled_back"
    assert result.rolled_back is True
    assert target.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "keep me"
    assert not marker.exists()


def test_uninstall_file_prompt_removes_marker_when_command_is_already_missing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "resources"
    source = root / "prompts" / "demo-prompt"
    install = tmp_path / "install"
    commands = tmp_path / "cursor" / "commands"
    source.mkdir(parents=True)
    (source / "command.md").write_text("prompt", encoding="utf-8")
    entry = RegistryItem(
        name="demo-prompt",
        kind="prompt",
        source="local",
        path="prompts/demo-prompt",
    )
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="cursor",
                enabled=True,
                prompts_dir=str(commands),
            )
        ],
    )
    installed = installer.sync_one(entry, config=cfg, registry_root=root)
    target = commands / "demo-prompt.md"
    marker = managed_marker_path(target)
    assert installed.action == installer.SyncAction.INSTALLED
    target.unlink()
    assert marker.is_file()

    removed = installer.uninstall_one(
        entry,
        config=cfg,
        platform_filter="cursor",
    )

    assert removed is True
    assert not marker.exists()


def test_sync_file_prompt_accepts_markdown_file_resource(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    source = root / "prompts" / "single.md"
    install = tmp_path / "install"
    commands = tmp_path / "cursor" / "commands"
    source.parent.mkdir(parents=True)
    source.write_text("single file prompt", encoding="utf-8")
    entry = RegistryItem(
        name="single",
        kind="prompt",
        source="local",
        path="prompts/single.md",
    )
    cfg = _config(
        root,
        install,
        platforms=[
            PlatformProfile(
                name="cursor",
                enabled=True,
                prompts_dir=str(commands),
            )
        ],
    )

    result = installer.sync_one(entry, config=cfg, registry_root=root)
    target = commands / "single.md"

    assert result.action == installer.SyncAction.INSTALLED
    assert target.read_text(encoding="utf-8") == "single file prompt"
    assert is_cc_port_managed(target, resource_key=entry.resource_key)


def test_mcp_injection_uses_state_backups_and_preserves_other_servers(
    tmp_path: Path,
) -> None:
    mcp_json = tmp_path / "mcp.json"
    mcp_json.write_text(
        json.dumps({"mcpServers": {"existing": {"command": "old"}}}, indent=2),
        encoding="utf-8",
    )

    inject_mcp_server(mcp_json, "demo", {"command": "demo"})
    inject_mcp_server(mcp_json, "demo", {"command": "demo2"})

    data = json.loads(mcp_json.read_text(encoding="utf-8"))
    assert data["mcpServers"]["existing"] == {"command": "old"}
    assert data["mcpServers"]["demo"] == {"command": "demo2"}
    backups = sorted((tmp_path / ".cc-port-state" / "backups" / "mcp").rglob("*-mcp.json"))
    assert len(backups) == 2
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {
        "mcpServers": {"existing": {"command": "old"}}
    }
    assert json.loads(backups[1].read_text(encoding="utf-8"))["mcpServers"]["demo"] == {
        "command": "demo"
    }
    assert not (tmp_path / "mcp.json.cc-port.bak").exists()


def test_mcp_backup_names_stay_unique_when_clock_does_not_advance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from datetime import datetime, timezone

    import cc_port.services.mcp_installer as mcp_installer

    frozen = datetime(2026, 7, 20, 14, 45, 51, 790342, tzinfo=timezone.utc)

    class FrozenDateTime:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr(mcp_installer, "datetime", FrozenDateTime)

    mcp_json = tmp_path / "mcp.json"
    mcp_json.write_text(
        json.dumps({"mcpServers": {"existing": {"command": "old"}}}, indent=2),
        encoding="utf-8",
    )

    inject_mcp_server(mcp_json, "demo", {"command": "demo"})
    inject_mcp_server(mcp_json, "demo", {"command": "demo2"})

    backups = sorted((tmp_path / ".cc-port-state" / "backups" / "mcp").rglob("*-mcp.json"))
    assert [path.name for path in backups] == [
        "20260720T144551790342Z-0000-mcp.json",
        "20260720T144551790342Z-0001-mcp.json",
    ]
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {
        "mcpServers": {"existing": {"command": "old"}}
    }
    assert json.loads(backups[1].read_text(encoding="utf-8"))["mcpServers"]["demo"] == {
        "command": "demo"
    }


def test_legacy_local_source_rejects_linked_registry_ancestors(
    tmp_path: Path,
) -> None:
    registry_root = tmp_path / "registry"
    registry_root.mkdir()
    outside = tmp_path / "outside"
    skill = outside / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo\n---\n",
        encoding="utf-8",
    )
    try:
        (registry_root / "skills").symlink_to(
            outside,
            target_is_directory=True,
        )
    except OSError as exc:
        pytest.skip(f"Directory symbolic links are unavailable: {exc}")
    entry = RegistryItem(
        name="demo",
        kind="skill",
        source="local",
        path="skills/demo",
    )

    with pytest.raises(ValueError, match="link|symbolic",):
        installer._local_source_path(entry, registry_root=registry_root)
