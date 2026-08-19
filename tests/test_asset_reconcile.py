from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from cc_port.core.config import Config, PluginProjectConfig, ResourcesConfig
from cc_port.core.models import Registry, RegistryItem
from cc_port.core.platforms import PlatformProfile, PlatformsConfig
from cc_port.services import (
    approval,
    asset_reconcile,
    asset_sync,
    env_manager,
    plugin_management,
)
from cc_port.services.asset_sync import (
    AssetInventory,
    AssetLocalInstance,
    AssetPlatformRow,
    AssetRemoteState,
    AssetResourceRow,
    RemoteSnapshot,
)
from cc_port.services.local_path_probe import LocalPathProbe, probe_local_path
from cc_port.services.registry_audit import RegistryHealthSummary
from cc_port.services.ui_messages import UiMessageRef


def _config(tmp_path: Path) -> Config:
    return Config(
        resources=ResourcesConfig(
            repo_url="https://secret.example/Alice/resources.git",
            local_path=str(tmp_path / "resource-repository"),
            branch="main",
        ),
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="codex.windows",
                    tool_id="codex",
                    environment_kind="windows",
                    display_name="Codex (Windows)",
                    home_dir=r"C:\Users\Alice",
                    enabled=True,
                    skills_dir=r"C:\Users\Alice\.agents\skills",
                ),
                PlatformProfile(
                    name="claude.wsl.ubuntu",
                    tool_id="claude-code",
                    environment_kind="wsl",
                    environment_name="Ubuntu",
                    display_name="Claude Code (Ubuntu)",
                    home_dir="/home/alice",
                    enabled=False,
                    skills_dir="/home/alice/.claude/skills",
                ),
            ]
        ),
    )


def _row(
    tmp_path: Path,
    *,
    name: str,
    status: str,
    local_body: str,
    remote_body: str,
) -> tuple[AssetPlatformRow, AssetResourceRow]:
    local_path = tmp_path / "private" / "Alice" / "skills" / name
    remote_path = tmp_path / "resource-repository" / "skills" / name
    local_path.mkdir(parents=True)
    remote_path.mkdir(parents=True)
    (local_path / "SKILL.md").write_text(local_body, encoding="utf-8")
    (remote_path / "SKILL.md").write_text(remote_body, encoding="utf-8")
    local_fingerprint = f"local-{name}"
    remote_fingerprint = f"remote-{name}"
    if status == "same":
        local_fingerprint = remote_fingerprint = f"same-{name}"
    local_instance_id = f"codex.windows:skill:{name}"
    row = AssetPlatformRow(
        resource_key=f"skill:{name}",
        kind="skill",
        name=name,
        platform="codex.windows",
        local_instance_id=local_instance_id,
        local_locator="expected",
        install_name=name,
        configured=True,
        enabled=True,
        detected=True,
        supported=True,
        remote_exists=True,
        local_exists=True,
        remote_writable=True,
        read_only_reference=False,
        remote_path=remote_path,
        local_path=local_path,
        target_path=local_path,
        ownership="managed",
        status=status,  # type: ignore[arg-type]
        remote_commit="abc123",
        remote_content_fingerprint=remote_fingerprint,
        remote_asset_fingerprint=f"remote-asset-{name}",
        local_fingerprint=local_fingerprint,
        local_content_path=local_path,
        metadata_differences=["description"] if status == "metadata-only" else [],
        diff_summary=[f"Private source: {local_path}"],
        warnings=[f"Cannot inspect {local_path}"],
        available_actions=["download", "upload"],
        tool_id="codex",
        environment_kind="windows",
        display_name="Codex (Windows)",
    )
    instance = AssetLocalInstance(
        id=local_instance_id,
        platform="codex.windows",
        install_name=name,
        path=local_path,
        ownership="managed",
        fingerprint=local_fingerprint,
        description=f"Local {name}",
        status=status,  # type: ignore[arg-type]
        content_path=local_path,
        link_target=r"C:\Users\Alice\private-target",
        tool_id="codex",
        environment_kind="windows",
        display_name="Codex (Windows)",
    )
    resource = AssetResourceRow(
        resource_key=f"skill:{name}",
        kind="skill",
        name=name,
        description=f"Resource {name}",
        description_source="local",
        local_status="single",
        remote_status="present",
        status=status,  # type: ignore[arg-type]
        remote=AssetRemoteState(
            exists=True,
            status="present",
            writable=True,
            read_only=False,
            commit="abc123",
            path=remote_path,
            description=f"Remote {name}",
        ),
        local_instances=[instance],
        metadata_differences=list(row.metadata_differences),
        diff_summary=list(row.diff_summary),
        warnings=list(row.warnings),
        available_actions=list(row.available_actions),
    )
    return row, resource


def _inventory(tmp_path: Path, *, names: tuple[str, ...] = ("demo",)) -> AssetInventory:
    rows_and_resources: list[tuple[AssetPlatformRow, AssetResourceRow]] = []
    for name in names:
        status = "same" if name.startswith("same") else "content-different"
        remote_body = f"remote {name}\n"
        rows_and_resources.append(
            _row(
                tmp_path,
                name=name,
                status=status,
                local_body=remote_body if status == "same" else f"local {name}\n",
                remote_body=remote_body,
            )
        )
    return AssetInventory(
        branch="main",
        remote_commit="abc123",
        repo_url="https://secret.example/Alice/resources.git",
        remote_available=True,
        remote_warning="",
        scanned_local=True,
        generated_at="2026-08-17T00:00:00Z",
        legacy_write_blocker="",
        rows=[item[0] for item in rows_and_resources],
        resources=[item[1] for item in rows_and_resources],
        registry_health=RegistryHealthSummary(
            status="healthy",
            checked_commit="abc123",
            issue_count=0,
            repairable_count=0,
            blocked_count=0,
            message="healthy",
        ),
    )


def test_reconcile_context_uses_fresh_configured_scan_and_safe_decision_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    inventory = _inventory(tmp_path)
    captured: dict[str, object] = {}

    def fake_inventory(**kwargs: object) -> AssetInventory:
        captured.update(kwargs)
        return inventory

    monkeypatch.setattr(asset_reconcile, "build_asset_inventory", fake_inventory)

    def forbidden_write(*_args: object, **_kwargs: object) -> None:
        pytest.fail("a reconciliation context must not plan, approve, or apply writes")

    for name in (
        "build_asset_action_plan",
        "build_asset_batch_plan",
        "apply_asset_action_plan",
        "apply_asset_batch_plan",
        "_cleanup_expired_asset_plans",
    ):
        monkeypatch.setattr(asset_sync, name, forbidden_write)
        monkeypatch.setattr(asset_reconcile, name, forbidden_write, raising=False)
    monkeypatch.setattr(approval, "create_approval_request", forbidden_write)
    monkeypatch.setattr(
        asset_reconcile,
        "create_approval_request",
        forbidden_write,
        raising=False,
    )

    context = asset_reconcile.build_asset_reconcile_context(
        config=cfg,
        page_size=20,
        include_same=False,
    )
    payload = context.model_dump(mode="json")

    assert captured == {
        "config": cfg,
        "scan_local": True,
        "refresh_remote": True,
        "scan_global": True,
        "project_ids": None,
        "cleanup_expired_plans": False,
        "enabled_profiles_only": True,
    }
    assert payload["context_schema_version"] == 1
    assert len(payload["context_id"]) == 64
    assert payload["scope"]["mode"] == "configured-enabled"
    assert payload["scope"]["arbitrary_filesystem_scan"] is False
    assert payload["scope"]["includes_saved_projects"] is True
    assert payload["scope"]["saved_project_count"] == 0
    assert payload["scope"]["scanned_saved_project_count"] == 0
    assert payload["scope"]["unavailable_saved_project_count"] == 0
    assert payload["remote"]["branch"] == "main"
    assert payload["remote"]["commit"] == "abc123"
    assert "repo_url" not in payload["remote"]
    assert "repository_id" not in payload["remote"]
    assert {item["profile_id"] for item in payload["coverage"]} == {
        "codex.windows",
        "claude.wsl.ubuntu",
    }
    disabled = next(
        item for item in payload["coverage"] if item["profile_id"] == "claude.wsl.ubuntu"
    )
    assert disabled["configuration_state"] == "disabled"
    assert disabled["scan_state"] == "not-scanned-disabled"
    assert payload["summary"]["review_count"] == 1
    assert payload["page"] == {
        "offset": 0,
        "page_size": 20,
        "returned": 1,
        "total": 1,
        "has_more": False,
        "next_cursor": "",
    }

    resource = payload["resources"][0]
    assert resource["resource_key"] == "skill:demo"
    assert resource["resource_status"] == "content-different"
    assert resource["local_multiplicity"] == "single"
    assert resource["remote"]["manifest"]["entries"][0]["relative_path"] == "SKILL.md"
    assert resource["local_instances"][0]["manifest"]["entries"][0][
        "relative_path"
    ] == "SKILL.md"
    comparison = resource["comparisons"][0]
    assert "comparison_id" not in comparison
    assert comparison["profile_id"] == "codex.windows"
    assert comparison["local_instance_id"] == "codex.windows:skill:demo"
    assert comparison["diff_available"] is True
    assert comparison["baseline"] == {
        "status": "unknown",
        "reason_code": "baseline.not-recorded",
    }
    assert {check["action"] for check in comparison["action_checks"]} == {
        "upload",
        "download",
    }

    encoded = json.dumps(payload, ensure_ascii=False)
    for private_value in (
        "Alice",
        r"C:\Users",
        "/home/alice",
        str(tmp_path),
        "secret.example",
        "private-target",
        "remote-asset-demo",
        "local-demo",
    ):
        assert private_value not in encoded


def test_reconcile_reports_unavailable_saved_projects_without_mutating_inventory_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    safe_project = tmp_path / "safe-project"
    safe_project.mkdir()
    cfg.plugin_projects = [
        PluginProjectConfig(id="safe", path=str(safe_project)),
        PluginProjectConfig(id="unavailable", path=str(tmp_path / "missing-project")),
    ]
    inventory = _inventory(tmp_path)
    captured: dict[str, object] = {}

    def fake_inventory(**kwargs: object) -> AssetInventory:
        captured.update(kwargs)
        return inventory

    monkeypatch.setattr(asset_reconcile, "build_asset_inventory", fake_inventory)
    monkeypatch.setattr(
        asset_reconcile,
        "_profile_environment_state",
        lambda _profile: (True, ""),
    )
    monkeypatch.setattr(
        asset_sync,
        "_profile_scan_available",
        lambda _profile, *, runtime_home: (True, ""),
    )

    payload = asset_reconcile.build_asset_reconcile_context(
        config=cfg,
    ).model_dump(mode="json")

    scan_cfg = captured["config"]
    assert isinstance(scan_cfg, Config)
    assert scan_cfg is cfg
    assert [project.id for project in scan_cfg.plugin_projects] == [
        "safe",
        "unavailable",
    ]
    assert [project.id for project in cfg.plugin_projects] == ["safe", "unavailable"]
    assert payload["scope"] == {
        "mode": "configured-enabled",
        "arbitrary_filesystem_scan": False,
        "includes_saved_projects": True,
        "saved_project_count": 2,
        "scanned_saved_project_count": 1,
        "unavailable_saved_project_count": 1,
        "include_same": False,
    }
    assert payload["completeness"] == "partial"


def test_reconcile_marks_every_saved_project_unavailable_without_compatible_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    first = tmp_path / "first-project"
    second = tmp_path / "second-project"
    first.mkdir()
    second.mkdir()
    cfg.plugin_projects = [
        PluginProjectConfig(id="first", path=str(first)),
        PluginProjectConfig(id="second", path=str(second)),
    ]
    captured: dict[str, object] = {}

    def fake_inventory(**kwargs: object) -> AssetInventory:
        captured.update(kwargs)
        return _inventory(tmp_path)

    def forbidden_project_probe(*_args: object, **_kwargs: object) -> LocalPathProbe:
        pytest.fail("saved projects must not be probed without an available runtime")

    monkeypatch.setattr(asset_reconcile, "build_asset_inventory", fake_inventory)
    monkeypatch.setattr(
        asset_reconcile,
        "_profile_environment_state",
        lambda _profile: (False, "runtime unavailable"),
    )
    monkeypatch.setattr(
        asset_sync,
        "_profile_scan_available",
        lambda _profile, *, runtime_home: (False, "runtime unavailable"),
    )
    monkeypatch.setattr(
        plugin_management,
        "probe_local_path",
        forbidden_project_probe,
    )

    payload = asset_reconcile.build_asset_reconcile_context(
        config=cfg,
    ).model_dump(mode="json")

    assert captured["config"] is cfg
    assert payload["scope"] == {
        "mode": "configured-enabled",
        "arbitrary_filesystem_scan": False,
        "includes_saved_projects": True,
        "saved_project_count": 2,
        "scanned_saved_project_count": 0,
        "unavailable_saved_project_count": 2,
        "include_same": False,
    }
    assert payload["completeness"] == "partial"


def test_configured_content_diff_prunes_linked_saved_project_before_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_home = tmp_path / "profile-home"
    local_skill = profile_home / "skills" / "demo"
    remote_root = tmp_path / "remote-snapshot"
    remote_skill = remote_root / "skills" / "demo"
    linked_project = tmp_path / "linked-project"
    external_target = tmp_path / "external-target"
    linked_project.mkdir()
    external_target.mkdir()
    local_skill.mkdir(parents=True)
    remote_skill.mkdir(parents=True)
    (local_skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: local\n---\nlocal body\n",
        encoding="utf-8",
    )
    (remote_skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: remote\n---\nremote body\n",
        encoding="utf-8",
    )
    cfg = Config(
        resources=ResourcesConfig(
            repo_url="https://example.invalid/resources.git",
            local_path=str(tmp_path / "legacy-workspace"),
            branch="main",
        ),
        plugin_projects=[
            PluginProjectConfig(id="linked", path=str(linked_project)),
        ],
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="codex.demo",
                    tool_id="codex",
                    home_dir=str(profile_home),
                    enabled=True,
                    skills_dir=str(profile_home / "skills"),
                )
            ]
        ),
    )
    snapshot = RemoteSnapshot(
        root=remote_root,
        registry=Registry(
            items=[
                RegistryItem(
                    name="demo",
                    kind="skill",
                    source="local",
                    path="skills/demo",
                )
            ]
        ),
        commit="abc123",
        branch="main",
        repo_url="https://example.invalid/resources.git",
        registry_health=RegistryHealthSummary(
            status="healthy",
            checked_commit="abc123",
            issue_count=0,
            repairable_count=0,
            blocked_count=0,
            message="healthy",
        ),
    )
    original_probe = plugin_management.probe_local_path

    def fake_project_probe(path: Path | str) -> LocalPathProbe:
        candidate = Path(path).absolute()
        if candidate == linked_project.absolute():
            return LocalPathProbe(
                logical_path=candidate,
                content_path=external_target.absolute(),
                path_kind="junction",
                health="ready",
                raw_target=str(external_target),
            )
        return original_probe(path)

    visited_projects: list[str] = []

    def record_plugin_discovery(
        _profile: PlatformProfile,
        *,
        runtime_home: Path,
        project: PluginProjectConfig | None = None,
    ) -> list[plugin_management.DiscoveredPlugin]:
        del runtime_home
        if project is not None:
            visited_projects.append(project.id)
        return []

    monkeypatch.setattr(plugin_management, "probe_local_path", fake_project_probe)
    monkeypatch.setattr(
        plugin_management,
        "_discover_profile_plugins",
        record_plugin_discovery,
    )
    monkeypatch.setattr(
        asset_sync,
        "_refresh_remote_snapshot",
        lambda _cfg, *, refresh: snapshot,
    )

    inventory = asset_sync.build_asset_inventory(
        config=cfg,
        scan_local=True,
        refresh_remote=False,
        remote_snapshot=snapshot,
        cleanup_expired_plans=False,
        enabled_profiles_only=True,
    )
    local_instance_id = next(
        row.local_instance_id
        for row in inventory.rows
        if row.resource_key == "skill:demo" and row.local_exists
    )
    visited_projects.clear()

    result = asset_sync.build_asset_content_diff(
        "skill:demo",
        local_instance_id,
        config=cfg,
        enabled_profiles_only=True,
    )

    assert result.local_instance_id == local_instance_id
    assert result.modified_files == 1
    assert result.files
    assert visited_projects == []


def test_configured_only_inventory_discovery_never_enters_disabled_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enabled_home = tmp_path / "enabled-home"
    disabled_home = tmp_path / "disabled-home"
    enabled_home.mkdir()
    disabled_home.mkdir()
    cfg = Config(
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="enabled-profile",
                    tool_id="cursor",
                    home_dir=str(enabled_home),
                    enabled=True,
                    skills_dir=str(enabled_home / "skills"),
                ),
                PlatformProfile(
                    name="disabled-profile",
                    tool_id="claude-code",
                    home_dir=str(disabled_home),
                    enabled=False,
                    skills_dir=str(disabled_home / "skills"),
                ),
            ]
        )
    )
    calls: list[dict[str, object]] = []
    visited_profiles: list[str] = []
    original_discover_profile = env_manager._discover_profile_tool

    def record_profile(
        profile: PlatformProfile,
        *,
        home: Path,
    ) -> env_manager.DiscoveredTool:
        visited_profiles.append(profile.name)
        return original_discover_profile(profile, home=home)

    def discover(**kwargs: object) -> env_manager.EnvDiscoveryResult:
        calls.append(kwargs)
        return env_manager.discover_environment(
            home=tmp_path / "runtime-home",
            **kwargs,
        )

    monkeypatch.setattr(env_manager, "_discover_profile_tool", record_profile)
    monkeypatch.setattr(asset_sync, "discover_environment", discover)

    def forbidden_plugin_fallback(*_args: object, **_kwargs: object) -> None:
        pytest.fail("configured-only discovery must not scan default plugin runtimes")

    for name in ("_discover_codex", "_discover_claude", "_discover_opencode"):
        monkeypatch.setattr(plugin_management, name, forbidden_plugin_fallback)

    result = asset_sync._discover_inventory_environment(
        cfg,
        scan_global=True,
        project_ids=None,
        configured_profiles_only=True,
    )

    assert calls == [
        {
            "config": cfg,
            "scan_global": True,
            "project_ids": None,
            "configured_profiles_only": True,
        }
    ]
    assert visited_profiles == ["enabled-profile"]
    assert [tool.id for tool in result.tools] == ["enabled-profile"]


def test_discovered_mcp_id_is_pathless_stable_and_reused_by_reconcile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_home = tmp_path / "profile"
    profile_home.mkdir()
    mcp_json = profile_home / "mcp.json"
    mcp_json.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "demo-server": {
                        "command": "printf",
                        "args": ["ready"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = Config(
        resources=ResourcesConfig(
            repo_url="https://example.invalid/resources.git",
            local_path=str(tmp_path / "legacy-workspace"),
            branch="main",
        ),
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="claude.demo",
                    tool_id="claude-code",
                    home_dir=str(profile_home),
                    enabled=True,
                    mcp_json=str(mcp_json),
                )
            ]
        ),
    )
    remote_root = tmp_path / "remote"
    remote_root.mkdir()
    snapshot = asset_sync.RemoteSnapshot(
        root=remote_root,
        registry=Registry(),
        commit="abc123",
        branch="main",
        repo_url="https://example.invalid/resources.git",
        registry_health=RegistryHealthSummary(
            status="healthy",
            checked_commit="abc123",
            issue_count=0,
            repairable_count=0,
            blocked_count=0,
            message="healthy",
        ),
    )

    def inventory() -> AssetInventory:
        return asset_sync.build_asset_inventory(
            config=cfg,
            scan_local=True,
            remote_snapshot=snapshot,
            cleanup_expired_plans=False,
            enabled_profiles_only=True,
        )

    first = inventory()
    second = inventory()
    first_row = next(row for row in first.rows if row.resource_key == "mcp:demo-server")
    second_row = next(row for row in second.rows if row.resource_key == "mcp:demo-server")

    assert first_row.local_instance_id == second_row.local_instance_id
    assert first_row.local_instance_id.startswith("claude.demo:mcp:")
    assert len(first_row.local_instance_id.removeprefix("claude.demo:mcp:")) == 24
    assert str(mcp_json) not in first_row.local_instance_id
    assert "\\" not in first_row.local_instance_id
    assert "/" not in first_row.local_instance_id

    monkeypatch.setattr(
        asset_reconcile,
        "build_asset_inventory",
        lambda **_kwargs: first,
    )
    context = asset_reconcile.build_asset_reconcile_context(config=cfg)
    resource = next(item for item in context.resources if item.resource_key == "mcp:demo-server")

    assert resource.local_instances[0].local_instance_id == first_row.local_instance_id
    assert resource.comparisons[0].local_instance_id == first_row.local_instance_id
    assert resource.comparisons[0].action_checks[0].action == "upload"
    assert resource.comparisons[0].action_checks[0].state == "eligible"


def test_reconcile_context_filters_same_rows_but_keeps_snapshot_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = _inventory(tmp_path, names=("demo", "same-copy"))
    monkeypatch.setattr(
        asset_reconcile,
        "build_asset_inventory",
        lambda **_kwargs: inventory,
    )

    default = asset_reconcile.build_asset_reconcile_context(
        config=_config(tmp_path),
        include_same=False,
    ).model_dump(mode="json")
    expanded = asset_reconcile.build_asset_reconcile_context(
        config=_config(tmp_path),
        include_same=True,
        page_size=200,
    ).model_dump(mode="json")

    assert default["summary"]["same_count"] == 1
    assert default["page"]["total"] == 1
    assert [item["resource_key"] for item in default["resources"]] == ["skill:demo"]
    assert expanded["summary"]["same_count"] == 1
    assert expanded["page"]["page_size"] == 200
    assert expanded["page"]["total"] == 2
    assert [item["resource_key"] for item in expanded["resources"]] == [
        "skill:demo",
        "skill:same-copy",
    ]


def test_reconcile_legacy_write_blocker_blocks_upload_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = _inventory(tmp_path)
    inventory.legacy_write_blocker = "Legacy workspace is dirty."
    inventory.legacy_write_blocker_ref = UiMessageRef(
        code="asset.legacy.dirty",
        fallback="Legacy workspace is dirty.",
    )
    monkeypatch.setattr(
        asset_reconcile,
        "build_asset_inventory",
        lambda **_kwargs: inventory,
    )

    payload = asset_reconcile.build_asset_reconcile_context(
        config=_config(tmp_path),
    ).model_dump(mode="json")

    checks = {
        item["action"]: item
        for item in payload["resources"][0]["comparisons"][0]["action_checks"]
    }
    assert checks["upload"]["state"] == "blocked"
    assert checks["upload"]["required_confirmations"] == []
    assert checks["upload"]["issues"] == [
        {
            "code": "asset.legacy.dirty",
            "severity": "blocker",
            "scope": "upload",
            "message": "Legacy workspace is dirty.",
        }
    ]
    assert checks["download"]["state"] == "eligible"


def test_reconcile_manifest_withholds_secret_hash_and_never_echoes_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "ghp_1234567890abcdef"
    inventory = _inventory(tmp_path)
    row = inventory.rows[0]
    assert row.local_content_path is not None
    (row.local_content_path / "SKILL.md").write_text(
        f"---\nname: demo\ndescription: safe\n---\ntoken={secret}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        asset_reconcile,
        "build_asset_inventory",
        lambda **_kwargs: inventory,
    )

    payload = asset_reconcile.build_asset_reconcile_context(
        config=_config(tmp_path),
    ).model_dump(mode="json")

    resource = payload["resources"][0]
    local = resource["local_instances"][0]
    entry = local["manifest"]["entries"][0]
    assert entry["relative_path"] == "SKILL.md"
    assert entry["hash_status"] == "withheld-secret"
    assert entry["sha256"] == ""
    assert local["manifest"]["complete"] is False
    assert payload["completeness"] == "partial"
    assert {
        issue["code"] for issue in local["issues"]
    } >= {"asset.reconcile.manifest_secret_withheld"}
    assert secret not in json.dumps(payload, ensure_ascii=False)


def test_manifest_prunes_nested_reparse_content_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "asset"
    nested = root / "nested-private-target"
    nested.mkdir(parents=True)
    (root / "safe.md").write_text("safe\n", encoding="utf-8")
    (nested / "must-not-read.txt").write_text(
        "ghp_1234567890abcdef\n",
        encoding="utf-8",
    )
    probed: list[Path] = []

    def probe(path: Path | str) -> LocalPathProbe:
        logical = Path(path).absolute()
        probed.append(logical)
        if logical == nested.absolute():
            return LocalPathProbe(
                logical_path=logical,
                content_path=logical,
                path_kind="junction",
                health="ready",
                raw_target=str(tmp_path / "outside"),
            )
        return probe_local_path(logical)

    monkeypatch.setattr(asset_reconcile, "probe_local_path", probe)

    manifest, issues = asset_reconcile._manifest_from_path(root)
    payload = manifest.model_dump(mode="json")

    assert payload["complete"] is False
    assert payload["entries_truncated"] is True
    assert payload["entry_count"] == 1
    assert [item["relative_path"] for item in payload["entries"]] == ["safe.md"]
    assert payload["tree_sha256"] == ""
    assert [issue.code for issue in issues] == ["asset.reconcile.manifest_nested_link"]
    assert nested.absolute() in probed
    assert (nested / "must-not-read.txt").absolute() not in probed
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "must-not-read" not in encoded
    assert "ghp_1234567890abcdef" not in encoded


def test_codex_memory_manifest_excludes_only_root_private_git(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    private_git = root / ".git"
    topic_git = root / "topic" / ".git"
    private_git.mkdir(parents=True)
    topic_git.mkdir(parents=True)
    (root / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    (private_git / "HEAD").write_text("private history\n", encoding="utf-8")
    (topic_git / "notes.md").write_text("# Topic\n", encoding="utf-8")

    manifest, issues = asset_reconcile._manifest_from_path(
        root,
        include_excluded=True,
        exclude_codex_memory_git=True,
    )

    assert not issues
    assert [entry.relative_path for entry in manifest.entries] == [
        "MEMORY.md",
        "topic/.git/notes.md",
    ]


def test_reconcile_cursor_is_stable_then_fails_closed_after_snapshot_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = _inventory(tmp_path, names=("alpha", "beta"))
    current = {"inventory": inventory}
    monkeypatch.setattr(
        asset_reconcile,
        "build_asset_inventory",
        lambda **_kwargs: current["inventory"],
    )
    cfg = _config(tmp_path)

    first = asset_reconcile.build_asset_reconcile_context(
        config=cfg,
        page_size=1,
        include_same=True,
    ).model_dump(mode="json")
    assert first["page"]["has_more"] is True
    assert first["page"]["next_cursor"]

    second = asset_reconcile.build_asset_reconcile_context(
        config=cfg,
        cursor=first["page"]["next_cursor"],
        page_size=1,
        include_same=True,
    ).model_dump(mode="json")
    assert second["context_id"] == first["context_id"]
    assert second["page"]["offset"] == 1
    assert {
        first["resources"][0]["resource_key"],
        second["resources"][0]["resource_key"],
    } == {"skill:alpha", "skill:beta"}

    with pytest.raises(asset_reconcile.AssetReconcileInvalidRequest, match="retain"):
        asset_reconcile.build_asset_reconcile_context(
            config=cfg,
            cursor=first["page"]["next_cursor"],
            page_size=2,
            include_same=True,
        )

    cursor = first["page"]["next_cursor"]
    replacement = "A" if cursor[-1] != "A" else "B"
    with pytest.raises(asset_reconcile.AssetReconcileInvalidRequest, match="cursor"):
        asset_reconcile.build_asset_reconcile_context(
            config=cfg,
            cursor=f"{cursor[:-1]}{replacement}",
            page_size=1,
            include_same=True,
        )

    current["inventory"] = replace(inventory, remote_commit="changed-commit")
    with pytest.raises(asset_reconcile.AssetReconcileStaleContext):
        asset_reconcile.build_asset_reconcile_context(
            config=cfg,
            cursor=first["page"]["next_cursor"],
            page_size=1,
            include_same=True,
        )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"context_schema_version": 2}, "schema"),
        ({"page_size": 0}, "page"),
        ({"page_size": 1.5}, "page"),
        ({"page_size": 201}, "page"),
        ({"cursor": "not-a-valid-cursor"}, "cursor"),
    ],
)
def test_reconcile_rejects_invalid_machine_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, object],
    match: str,
) -> None:
    monkeypatch.setattr(
        asset_reconcile,
        "build_asset_inventory",
        lambda **_kwargs: _inventory(tmp_path),
    )

    with pytest.raises(asset_reconcile.AssetReconcileInvalidRequest, match=match):
        asset_reconcile.build_asset_reconcile_context(
            config=_config(tmp_path),
            **kwargs,
        )
