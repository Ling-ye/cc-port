from __future__ import annotations

import json
from pathlib import Path

from lpm.core.config import Config, InstallConfig, PlatformsConfig, ResourcesConfig
from lpm.core.models import Registry, RegistryItem
from lpm.core.platforms import PlatformProfile
from lpm.core.registry import save_registry
from lpm.services import installer, resource_manager
from lpm.services.install_planner import copy_resource_tree, load_resource_manifest
from lpm.services.mcp_installer import inject_mcp_server


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

    copy_resource_tree(src, dest)

    assert (dest / "SKILL.md").is_file()
    assert (dest / "notes.md").is_file()
    assert not (dest / "node_modules").exists()
    assert not (dest / ".venv").exists()
    assert not (dest / "dist").exists()


def test_manifest_limits_copied_resource_paths(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    (src / "SKILL.md").write_text("---\nname: demo\ndescription: demo\n---\n", encoding="utf-8")
    (src / "keep").mkdir()
    (src / "keep" / "tool.md").write_text("keep", encoding="utf-8")
    (src / "extra.md").write_text("drop", encoding="utf-8")
    (src / "lpm.resource.json").write_text(
        json.dumps({"skills": ["SKILL.md"], "commands": ["keep"]}),
        encoding="utf-8",
    )

    manifest = load_resource_manifest(src)
    copy_resource_tree(src, dest, manifest=manifest)

    assert (dest / "SKILL.md").is_file()
    assert (dest / "keep" / "tool.md").is_file()
    assert not (dest / "extra.md").exists()
    assert (dest / "lpm.resource.json").is_file()


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


def test_mcp_injection_creates_one_backup_and_preserves_other_servers(tmp_path: Path) -> None:
    mcp_json = tmp_path / "mcp.json"
    mcp_json.write_text(
        json.dumps({"mcpServers": {"existing": {"command": "old"}}}, indent=2),
        encoding="utf-8",
    )

    inject_mcp_server(mcp_json, "demo", {"command": "demo"})
    first_backup_text = (tmp_path / "mcp.json.lpm.bak").read_text(encoding="utf-8")
    inject_mcp_server(mcp_json, "demo", {"command": "demo2"})

    data = json.loads(mcp_json.read_text(encoding="utf-8"))
    assert data["mcpServers"]["existing"] == {"command": "old"}
    assert data["mcpServers"]["demo"] == {"command": "demo2"}
    assert (tmp_path / "mcp.json.lpm.bak").read_text(encoding="utf-8") == first_backup_text
