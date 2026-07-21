from __future__ import annotations

import json
from pathlib import Path

import pytest

from lpm.core.config import Config, PluginProjectConfig, load_raw_config
from lpm.services import plugin_management


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_codex_adapter_separates_marketplace_cache_from_owned_content(tmp_path: Path) -> None:
    codex = tmp_path / ".codex"
    (codex / "config.toml").parent.mkdir(parents=True, exist_ok=True)
    (codex / "config.toml").write_text(
        '[marketplaces.openai]\nsource = "https://github.com/openai/plugins"\n'
        '[plugins."chrome@openai"]\nenabled = true\n',
        encoding="utf-8",
    )
    _json(
        codex / "plugins" / "cache" / "openai" / "chrome" / "1.2.3" / ".codex-plugin" / "plugin.json",
        {"name": "chrome", "version": "1.2.3"},
    )
    _json(
        codex / "plugins" / "owned-tool" / ".codex-plugin" / "plugin.json",
        {"name": "owned-tool", "version": "0.1.0"},
    )

    items = plugin_management.discover_plugins(Config(), home=tmp_path)

    chrome = next(item for item in items if item.plugin_id == "chrome")
    owned = next(item for item in items if item.plugin_id == "owned-tool")
    assert chrome.track == "reference"
    assert chrome.origin_type == "marketplace"
    assert chrome.observed_version == "1.2.3"
    assert "cache" in chrome.path.parts
    assert owned.track == "content"
    assert "cache" not in owned.path.parts


def test_claude_adapter_preserves_scope_and_disabled_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_management, "_claude_cli_plugins", lambda _cwd: [])
    _json(
        tmp_path / ".claude" / "settings.json",
        {"enabledPlugins": {"formatter@team": False}},
    )
    _json(
        tmp_path / ".claude" / "plugins" / "known_marketplaces.json",
        {"team": {"source": {"repo": "https://github.com/acme/plugins"}}},
    )
    _json(
        tmp_path / ".claude" / "plugins" / "cache" / "team" / "formatter" / "2.0.0" / ".claude-plugin" / "plugin.json",
        {"name": "formatter", "version": "2.0.0"},
    )

    item = next(
        item
        for item in plugin_management.discover_plugins(Config(), home=tmp_path)
        if item.platform == "claude-code" and item.plugin_id == "formatter"
    )
    assert item.scope == "user"
    assert item.enabled is False
    assert item.state_path == tmp_path / ".claude" / "settings.json"
    assert item.track == "reference"


def test_opencode_adapter_keeps_npm_selector_and_local_file_separate(tmp_path: Path) -> None:
    root = tmp_path / ".config" / "opencode"
    _json(root / "opencode.json", {"plugin": ["@acme/tool@^3.1.0", "plain-plugin"]})
    (root / "plugins").mkdir(parents=True)
    (root / "plugins" / "owned.ts").write_text("export default {}\n", encoding="utf-8")

    items = plugin_management.discover_plugins(Config(), home=tmp_path)
    npm = next(item for item in items if item.package == "@acme/tool")
    local = next(item for item in items if item.plugin_id == "owned")
    assert npm.track == "reference"
    assert npm.selector == "^3.1.0"
    assert npm.state_path == root / "opencode.json"
    assert local.track == "content"
    assert local.path == root / "plugins" / "owned.ts"


def test_project_without_git_remote_is_observation_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(plugin_management.git_ops, "repository_root", lambda _path: None)

    inspected = plugin_management.inspect_plugin_project(project, config=Config())

    assert inspected.repo == ""
    assert inspected.id.startswith("project-")


def test_project_mapping_roundtrip_does_not_touch_project_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(plugin_management.git_ops, "repository_root", lambda _path: None)

    added = plugin_management.add_plugin_project(project, config_path=config_path)
    removed = plugin_management.remove_plugin_project(added.id, config_path=config_path)

    assert removed.path == project
    assert load_raw_config(config_path).plugin_projects == []
    assert list(project.iterdir()) == []


def test_git_identity_strips_credentials_and_transport() -> None:
    assert plugin_management.normalize_git_identity("git@github.com:Acme/Repo.git") == "github.com/Acme/Repo"
    assert plugin_management.normalize_git_identity("https://user:secret@github.com/Acme/Repo.git") == "github.com/Acme/Repo"
    assert plugin_management.normalize_git_identity(r"C:\private\repo.git") == ""
    assert plugin_management._portable_marketplace_source(r"C:\runtime\plugins", "bundled") == "bundled"


def test_explicit_project_root_detects_owned_claude_plugin_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "owned-plugin"
    _json(
        project / ".claude-plugin" / "plugin.json",
        {"name": "owned-plugin", "version": "1.0.0"},
    )
    mapping = PluginProjectConfig(
        id="project-owned",
        path=str(project),
        repo="github.com/acme/owned-plugin",
    )
    monkeypatch.setattr(plugin_management, "_claude_cli_plugins", lambda _cwd: [])

    items = plugin_management.discover_plugins(
        Config(plugin_projects=[mapping]),
        home=tmp_path,
        scan_global=False,
        project_ids=[mapping.id],
    )

    owned = next(item for item in items if item.platform == "claude-code")
    assert owned.track == "content"
    assert owned.path == project
    assert owned.project_repo == "github.com/acme/owned-plugin"


def test_same_named_local_plugins_in_different_projects_have_distinct_portable_sources(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root in (first, second):
        plugin = root / ".opencode" / "plugins" / "tool.ts"
        plugin.parent.mkdir(parents=True)
        plugin.write_text("export default {}\n", encoding="utf-8")
    mappings = [
        PluginProjectConfig(
            id="project-first",
            path=str(first),
            repo="github.com/acme/first",
        ),
        PluginProjectConfig(
            id="project-second",
            path=str(second),
            repo="github.com/acme/second",
        ),
    ]

    items = plugin_management.discover_plugins(
        Config(plugin_projects=mappings),
        home=tmp_path,
        scan_global=False,
    )
    plugins = [item for item in items if item.platform == "opencode" and item.plugin_id == "tool"]

    assert len(plugins) == 2
    assert {item.origin_source for item in plugins} == {
        "github.com/acme/first#.opencode/plugins/tool.ts",
        "github.com/acme/second#.opencode/plugins/tool.ts",
    }
    assert len({item.resource_name for item in plugins}) == 2
