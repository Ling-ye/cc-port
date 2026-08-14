from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cc_port.core.claude_plugins import (
    ClaudePluginFormatError,
    inspect_claude_plugin,
    inspect_claude_skill,
)
from cc_port.core.config import Config
from cc_port.core.models import (
    PluginInstallation,
    PluginOrigin,
    PluginSpec,
    Registry,
    RegistryItem,
    ResourceKey,
)
from cc_port.core.platforms import PlatformProfile, PlatformsConfig
from cc_port.core.registry import load_registry, save_registry
from cc_port.services import asset_sync
from cc_port.services.claude_plugin_installer import (
    ClaudeCliContext,
    install_marketplace_plugin,
    installable_marketplace_source,
    marketplace_install_ready,
    set_marketplace_plugin_enabled,
)
from cc_port.services.env_manager import discover_environment
from cc_port.services.local_transaction import resource_hash_path
from cc_port.services.plugin_management import discover_plugins


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _claude_plugin(path: Path, *, name: str = "review-tools") -> None:
    _json(
        path / ".claude-plugin" / "plugin.json",
        {
            "name": name,
            "description": "Review helpers",
            "version": "1.2.0",
        },
    )
    skill = path / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        "---\ndescription: Review code changes\n---\n\nReview the change.\n",
        encoding="utf-8",
    )


def _claude_profile(home: Path) -> PlatformProfile:
    return PlatformProfile(
        name="claude-windows",
        tool_id="claude-code",
        environment_kind="",
        home_dir=str(home),
        enabled=True,
        skills_dir="~/.claude/skills",
        settings_path="~/.claude/settings.json",
        plugins_dir="",
    )


def test_claude_manifest_plugin_is_not_misclassified_as_plain_skill(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    skills_root = tmp_path / "custom-claude-skills"
    plugin = skills_root / "review-tools"
    plain_skill = skills_root / "plain-skill"
    _claude_plugin(plugin)
    plain_skill.mkdir(parents=True)
    (plain_skill / "SKILL.md").write_text(
        "---\ndescription: A plain skill\n---\n\nDo one thing.\n",
        encoding="utf-8",
    )
    profile = _claude_profile(home)
    profile.skills_dir = str(skills_root)
    config = Config(platforms=PlatformsConfig(profiles=[profile]))

    plugins = discover_plugins(config, home=tmp_path)
    environment = discover_environment(config=config, home=tmp_path)

    assert [(item.plugin_id, item.path, item.track) for item in plugins] == [
        ("review-tools", plugin.resolve(), "content")
    ]
    assert {item.name_hint for item in environment.resources} == {"plain-skill"}
    assert {item.plugin_id for item in environment.plugins} == {"review-tools"}


def test_native_claude_skill_allows_optional_name_and_description(tmp_path: Path) -> None:
    skill = tmp_path / "review-changes"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\ndisable-model-invocation: true\n---\n\nReview the current diff.\n",
        encoding="utf-8",
    )

    metadata = inspect_claude_skill(skill)

    assert metadata.name == "review-changes"
    assert metadata.description == "Review the current diff."


def test_claude_runtime_plugin_root_is_never_treated_as_owned_content(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime_plugin = home / ".claude" / "plugins" / "looks-owned"
    _claude_plugin(runtime_plugin, name="looks-owned")
    profile = _claude_profile(home)
    profile.plugins_dir = "~/.claude/plugins"

    plugins = discover_plugins(
        Config(platforms=PlatformsConfig(profiles=[profile])),
        home=tmp_path,
    )

    assert all(item.path != runtime_plugin.resolve() for item in plugins)


def test_claude_plugin_validation_rejects_components_inside_metadata_dir(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "bad-plugin"
    _json(plugin / ".claude-plugin" / "plugin.json", {"name": "bad-plugin"})
    misplaced = plugin / ".claude-plugin" / "skills" / "bad" / "SKILL.md"
    misplaced.parent.mkdir(parents=True)
    misplaced.write_text("---\ndescription: bad\n---\n", encoding="utf-8")

    with pytest.raises(ClaudePluginFormatError, match="plugin root"):
        inspect_claude_plugin(plugin, require_manifest=True)


def test_claude_plugin_validation_accepts_monitor_array(tmp_path: Path) -> None:
    plugin = tmp_path / "monitored-plugin"
    _claude_plugin(plugin, name="monitored-plugin")
    _json(
        plugin / "monitors" / "monitors.json",
        [{"name": "error-log", "command": "tail -F errors.log"}],
    )

    metadata = inspect_claude_plugin(plugin, require_manifest=True)

    assert "monitors/monitors.json" in metadata.components


def test_marketplace_source_and_name_survive_registry_roundtrip(tmp_path: Path) -> None:
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
    registry_path = tmp_path / "registry.yaml"

    save_registry(Registry(items=[entry]), registry_path)
    loaded = load_registry(registry_path).get(entry.name, "plugin")

    assert loaded is not None and loaded.plugin is not None
    assert loaded.plugin.origin.marketplace == "team-tools"
    assert loaded.plugin.origin.source == "acme/claude-plugins"
    assert "locator: acme/claude-plugins" in registry_path.read_text(encoding="utf-8")
    assert "marketplace: team-tools" in (tmp_path / "cc-port.yaml").read_text(encoding="utf-8")


def test_claude_marketplace_inventory_keeps_name_and_portable_source_separate(
    tmp_path: Path,
) -> None:
    profile = _claude_profile(tmp_path / "home")
    config = Config(platforms=PlatformsConfig(profiles=[profile]))
    spec = PluginSpec(
        track="reference",
        platform="claude-code",
        plugin_id="review-tools",
        origin=PluginOrigin(
            type="marketplace",
            marketplace="team-tools",
            source="acme/claude-plugins",
            selector="release/v2",
        ),
        installations=[PluginInstallation(scope="user", enabled=True)],
    )
    entry = RegistryItem(
        name="claude-marketplace-review-tools",
        kind="plugin",
        source="external",
        plugin=spec,
    )
    remote = tmp_path / "remote"
    remote.mkdir()
    snapshot = asset_sync.RemoteSnapshot(
        root=remote,
        registry=Registry(items=[entry]),
        commit="abc123",
        branch="main",
        repo_url="https://example.invalid/resources",
    )
    rows = asset_sync._expected_plugin_rows(
        entry,
        snapshot,
        config,
        asset_sync._platform_contexts(config, None),
    )
    inventory = asset_sync.AssetInventory(
        branch="main",
        remote_commit="abc123",
        repo_url=snapshot.repo_url,
        remote_available=True,
        remote_warning="",
        scanned_local=True,
        generated_at="2026-08-12T00:00:00Z",
        legacy_write_blocker="",
        rows=rows,
    )

    resource = asset_sync._aggregate_resource_rows(inventory)[0]

    assert resource.plugin_source_id == "team-tools"
    assert resource.plugin_marketplace == "team-tools"
    assert resource.plugin_marketplace_source == "acme/claude-plugins"

    local_row = rows[0]
    local_row.entry = None
    local_row.remote_exists = False
    local_row.local_exists = True
    local_row.status = "local-only"
    local_row.plugin_source_id = "review-tools@team-tools"
    local_inventory = asset_sync.AssetInventory(
        branch="main",
        remote_commit="",
        repo_url=snapshot.repo_url,
        remote_available=True,
        remote_warning="",
        scanned_local=True,
        generated_at="2026-08-12T00:00:00Z",
        legacy_write_blocker="",
        rows=[local_row],
    )

    local_resource = asset_sync._aggregate_resource_rows(local_inventory)[0]

    assert local_resource.plugin_source_id == "review-tools@team-tools"
    assert local_resource.plugin_marketplace == "team-tools"
    assert local_resource.plugin_marketplace_source == "acme/claude-plugins"


def test_claude_content_reference_conversion_requires_installable_marketplace_source(
    tmp_path: Path,
) -> None:
    profile = _claude_profile(tmp_path / "home")
    config = Config(platforms=PlatformsConfig(profiles=[profile]))
    spec = PluginSpec(
        track="reference",
        platform="claude-code",
        plugin_id="review-tools",
        origin=PluginOrigin(
            type="marketplace",
            marketplace="team-tools",
            source="acme/claude-plugins",
        ),
        installations=[PluginInstallation(scope="user", enabled=True)],
    )
    entry = RegistryItem(
        name="claude-marketplace-review-tools",
        kind="plugin",
        source="external",
        plugin=spec,
    )
    remote = tmp_path / "remote"
    remote.mkdir()
    snapshot = asset_sync.RemoteSnapshot(
        root=remote,
        registry=Registry(items=[entry]),
        commit="abc123",
        branch="main",
        repo_url="https://example.invalid/resources",
    )
    row = asset_sync._expected_plugin_rows(
        entry,
        snapshot,
        config,
        asset_sync._platform_contexts(config, None),
    )[0]
    row.plugin_data["plugin"]["track"] = "content"
    row.plugin_data["plugin"]["origin"] = {
        "type": "local",
        "marketplace": "",
        "source": "review-tools",
        "package": "",
        "repo": "",
        "selector": "",
    }

    _, missing_source = asset_sync._content_plugin_reference_data(
        row,
        asset_sync.AssetBatchChoice(
            resource_key=entry.resource_key,
            plugin_track="reference",
            reference_origin={"type": "marketplace", "marketplace": "team-tools"},
        ),
    )
    _, direct_git = asset_sync._content_plugin_reference_data(
        row,
        asset_sync.AssetBatchChoice(
            resource_key=entry.resource_key,
            plugin_track="reference",
            reference_origin={"type": "git", "repo": "https://github.com/acme/plugin.git"},
        ),
    )
    converted, error = asset_sync._content_plugin_reference_data(
        row,
        asset_sync.AssetBatchChoice(
            resource_key=entry.resource_key,
            plugin_track="reference",
            reference_origin={
                "type": "marketplace",
                "marketplace": "team-tools",
                "source": "acme/claude-plugins",
                "selector": "release/v2",
            },
        ),
    )

    assert "portable marketplace source" in missing_source
    assert "require a marketplace origin" in direct_git
    assert error == ""
    assert converted["plugin"]["origin"] == {
        "type": "marketplace",
        "marketplace": "team-tools",
        "source": "acme/claude-plugins",
        "package": "",
        "repo": "",
        "selector": "release/v2",
    }

    unsafe_spec = PluginSpec(
        track="reference",
        platform="claude-code",
        plugin_id="review-tools",
        origin=PluginOrigin(
            type="marketplace",
            marketplace="team-tools",
            source="team-tools",
        ),
        installations=[PluginInstallation(scope="user", enabled=True)],
    )
    with pytest.raises(asset_sync.AssetSyncError, match="not safely installable"):
        asset_sync._mutate_plugin_reference(
            Registry(),
            tmp_path / "registry.yaml",
            ResourceKey(kind="plugin", name="claude-marketplace-review-tools"),
            None,
            unsafe_spec,
            description="",
        )
    assert not (tmp_path / "registry.yaml").exists()


def test_skills_directory_plugin_delete_never_uses_marketplace_uninstall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "skills" / "review-tools"
    _claude_plugin(source, name="review-tools")
    spec = PluginSpec(
        track="content",
        platform="claude-code",
        plugin_id="review-tools",
        origin=PluginOrigin(type="local", source="review-tools"),
        installations=[PluginInstallation(scope="user", enabled=True)],
    )
    row = asset_sync.AssetPlatformRow(
        resource_key="plugin:review-tools",
        kind="plugin",
        name="review-tools",
        platform="claude-windows",
        local_instance_id="claude-windows:user:review-tools",
        local_locator="plugin-adapter",
        install_name="review-tools",
        configured=True,
        enabled=True,
        detected=True,
        supported=True,
        remote_exists=True,
        local_exists=True,
        remote_writable=True,
        read_only_reference=False,
        remote_path=None,
        local_path=source,
        target_path=source,
        ownership="unmanaged",
        status="same",
        remote_commit="abc123",
        plugin_track="content",
        plugin_scope="user",
        plugin_writable=True,
        plugin_data={"plugin": spec.model_dump(mode="json")},
    )
    monkeypatch.setattr(asset_sync.shutil, "which", lambda _name: "/usr/bin/claude")

    item = asset_sync._plugin_delete_instance(spec, spec.installations[0], row)

    assert item.method == "delete-content"
    assert item.local_path == source


def test_native_claude_installer_adds_marketplace_installs_and_disables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "fake_claude.py"
    script.write_text(
        """
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
config = Path(os.environ["CLAUDE_CONFIG_DIR"])
settings = config / "settings.json"
known_path = config / "plugins" / "known_marketplaces.json"

def read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")

if args[:4] == ["plugin", "marketplace", "list", "--json"]:
    known = read(known_path)
    print(json.dumps([
        {"name": name, "repo": value.get("source", {}).get("repo", "")}
        for name, value in known.items()
    ]))
elif args[:3] == ["plugin", "marketplace", "add"]:
    known = read(known_path)
    known["team-tools"] = {"source": {"repo": args[3]}}
    write(known_path, known)
elif args[:2] == ["plugin", "install"]:
    qualified = args[2]
    payload = read(settings)
    payload.setdefault("enabledPlugins", {})[qualified] = True
    write(settings, payload)
    plugin, marketplace = qualified.rsplit("@", 1)
    manifest = config / "plugins" / "cache" / marketplace / plugin / "1.2.0" / ".claude-plugin" / "plugin.json"
    write(manifest, {"name": plugin, "version": "1.2.0"})
elif args[:2] == ["plugin", "disable"]:
    payload = read(settings)
    payload.setdefault("enabledPlugins", {})[args[2]] = False
    write(settings, payload)
elif args[:2] == ["plugin", "enable"]:
    payload = read(settings)
    payload.setdefault("enabledPlugins", {})[args[2]] = True
    write(settings, payload)
elif args[:3] == ["plugin", "marketplace", "remove"]:
    known = read(known_path)
    known.pop(args[3], None)
    write(known_path, known)
elif args[:3] == ["plugin", "list", "--json"]:
    enabled = read(settings).get("enabledPlugins", {})
    print(json.dumps([
        {"id": key, "scope": "user", "enabled": value}
        for key, value in enabled.items()
    ]))
else:
    raise SystemExit(2)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    profile = _claude_profile(home)
    context = ClaudeCliContext(
        command=(sys.executable, str(script)),
        config_dir=home / ".claude",
    )
    monkeypatch.setattr(
        "cc_port.services.claude_plugin_installer.claude_cli_context",
        lambda _profile: context,
    )
    spec = PluginSpec(
        track="reference",
        platform="claude-code",
        plugin_id="review-tools",
        origin=PluginOrigin(
            type="marketplace",
            marketplace="team-tools",
            source="acme/claude-plugins",
        ),
        installations=[PluginInstallation(scope="user", enabled=False)],
    )

    result = install_marketplace_plugin(profile, spec, spec.installations[0])

    assert result.marketplace_added is True
    assert result.enabled is False
    settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["enabledPlugins"]["review-tools@team-tools"] is False
    assert (
        home
        / ".claude"
        / "plugins"
        / "cache"
        / "team-tools"
        / "review-tools"
        / "1.2.0"
        / ".claude-plugin"
        / "plugin.json"
    ).is_file()
    assert marketplace_install_ready(profile, spec) is True

    enabled_installation = spec.installations[0].model_copy(update={"enabled": True})
    set_marketplace_plugin_enabled(profile, spec, enabled_installation)
    enabled_settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert enabled_settings["enabledPlugins"]["review-tools@team-tools"] is True


def test_reference_download_plan_installs_for_exact_claude_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    profile = _claude_profile(home)
    config = Config(platforms=PlatformsConfig(profiles=[profile]))
    monkeypatch.setattr(asset_sync, "claude_cli_context", lambda _profile: object())
    monkeypatch.setattr(
        asset_sync,
        "marketplace_install_ready",
        lambda *_args, **_kwargs: True,
    )
    spec = PluginSpec(
        track="reference",
        platform="claude-code",
        plugin_id="review-tools",
        origin=PluginOrigin(
            type="marketplace",
            marketplace="team-tools",
            source="acme/claude-plugins",
        ),
        installations=[PluginInstallation(scope="user", enabled=True)],
    )
    entry = RegistryItem(
        name="claude-marketplace-review-tools",
        kind="plugin",
        source="external",
        plugin=spec,
    )

    item = asset_sync._build_plugin_reference_download_item(
        entry.resource_key,
        profile.name,
        [],
        entry,
        config,
    )

    assert item.plan is not None
    assert item.platform == "claude-windows"
    assert item.disposition == "create"
    assert item.plan.plugin_data["alignments"][0]["method"] == "claude-install"
    assert item.plan.target_path == profile.settings_file()


def test_reference_download_apply_invokes_native_installer_and_verifies_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    profile = _claude_profile(home)
    config = Config(platforms=PlatformsConfig(profiles=[profile]))
    spec = PluginSpec(
        track="reference",
        platform="claude-code",
        plugin_id="review-tools",
        origin=PluginOrigin(
            type="marketplace",
            marketplace="team-tools",
            source="acme/claude-plugins",
        ),
        installations=[PluginInstallation(scope="user", enabled=True)],
    )
    entry = RegistryItem(
        name="claude-marketplace-review-tools",
        kind="plugin",
        source="external",
        plugin=spec,
    )
    registry = Registry(items=[entry])
    remote = tmp_path / "remote"
    remote.mkdir()
    snapshot = asset_sync.RemoteSnapshot(
        root=remote,
        registry=registry,
        commit="abc123",
        branch="main",
        repo_url="https://example.invalid/resources",
    )
    context = asset_sync._PlatformContext(
        profile=profile,
        configured=True,
        detected=True,
        supported_kinds={"plugin"},
    )
    rows = asset_sync._expected_plugin_rows(
        entry,
        snapshot,
        config,
        {profile.name: context},
    )
    monkeypatch.setattr(asset_sync, "claude_cli_context", lambda _profile: object())
    monkeypatch.setattr(
        asset_sync,
        "marketplace_install_ready",
        lambda *_args, **_kwargs: True,
    )
    item = asset_sync._build_plugin_reference_download_item(
        entry.resource_key,
        profile.name,
        rows,
        entry,
        config,
    )
    assert item.plan is not None
    assert item.plan.tool_id == "claude-code"
    monkeypatch.setattr(
        asset_sync,
        "_refresh_remote_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )

    def install(
        _profile: PlatformProfile,
        desired: PluginSpec,
        installation: PluginInstallation,
        *,
        project_root: Path | None = None,
    ) -> object:
        assert project_root is None
        settings = profile.settings_file()
        assert settings is not None
        asset_sync._write_claude_plugin_state(settings, desired, installation.enabled)
        return object()

    monkeypatch.setattr(asset_sync, "install_marketplace_plugin", install)

    result = asset_sync._apply_local_asset_action(item.plan, config)

    settings = profile.settings_file()
    assert settings is not None
    payload = json.loads(settings.read_text(encoding="utf-8"))
    assert result.status == "succeeded"
    assert payload["enabledPlugins"]["review-tools@team-tools"] is True


def test_marketplace_source_preserves_safe_ref_and_rejects_ambiguous_url_ref() -> None:
    assert (
        installable_marketplace_source("acme/claude-plugins", "release/v2")
        == "acme/claude-plugins@release/v2"
    )
    assert (
        installable_marketplace_source(
            "https://gitlab.example.com/acme/plugins.git",
            "release/v2",
        )
        == "https://gitlab.example.com/acme/plugins.git#release/v2"
    )
    assert (
        installable_marketplace_source(
            "https://example.com/marketplace.json",
            "release/v2",
        )
        == ""
    )


def test_claude_content_download_uses_skills_dir_and_updates_native_state(
    tmp_path: Path,
) -> None:
    profile = _claude_profile(tmp_path / "home")
    config = Config(platforms=PlatformsConfig(profiles=[profile]))
    remote = tmp_path / "remote"
    source = remote / "plugins" / "review-tools-resource"
    _claude_plugin(source)
    spec = PluginSpec(
        track="content",
        platform="claude-code",
        plugin_id="review-tools",
        origin=PluginOrigin(type="local", source="review-tools"),
        installations=[PluginInstallation(scope="user", enabled=True)],
    )
    entry = RegistryItem(
        name="review-tools-resource",
        kind="plugin",
        source="local",
        path="plugins/review-tools-resource",
        plugin=spec,
    )
    context = asset_sync._PlatformContext(
        profile=profile,
        configured=True,
        detected=True,
        supported_kinds={"plugin"},
    )
    target = asset_sync._plugin_content_target(
        entry,
        spec.installations[0],
        config,
        context,
    )
    assert target == profile.skills_path() / "review-tools"
    settings = profile.settings_file()
    assert settings is not None
    plan = asset_sync.AssetActionPlan(
        operation_id="claude-content-install",
        action="download",
        resource_key=entry.resource_key,
        target_resource_key=entry.resource_key,
        kind="plugin",
        name=entry.name,
        platform=profile.name,
        local_instance_id="expected",
        local_locator="plugin-expected",
        remote_commit="abc123",
        remote_target_exists=True,
        remote_target_fingerprint="remote",
        local_source_fingerprint="",
        target_path=target,
        target_exists=False,
        target_fingerprint="",
        target_managed=False,
        tool_id="claude-code",
        plugin_data={
            "plugin": spec.model_dump(mode="json"),
            "state_path": str(settings),
            "state_exists": False,
            "state_fingerprint": "",
            "project_root": "",
        },
    )
    snapshot = asset_sync.RemoteSnapshot(
        root=remote,
        registry=Registry(items=[entry]),
        commit="abc123",
        branch="main",
        repo_url="https://example.invalid/resources",
    )

    result = asset_sync._apply_plugin_content_download(
        plan,
        config,
        snapshot,
        entry,
    )

    assert result.status == "succeeded"
    assert target is not None and (target / ".claude-plugin" / "plugin.json").is_file()
    assert resource_hash_path(source) == resource_hash_path(target)
    state = json.loads(settings.read_text(encoding="utf-8"))
    assert state["enabledPlugins"]["review-tools@skills-dir"] is True
