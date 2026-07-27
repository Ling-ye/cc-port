from __future__ import annotations

import json
from pathlib import Path

from cc_port.core.config import Config, InstallConfig, PlatformsConfig, ResourcesConfig
from cc_port.core.models import Registry, RegistryItem
from cc_port.core.platforms import PlatformProfile
from cc_port.core.registry import save_registry
from cc_port.services import installer, resource_manager
from cc_port.services.install_planner import copy_resource_tree, load_resource_manifest
from cc_port.services.mcp_installer import inject_mcp_server


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
