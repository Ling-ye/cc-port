from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from cc_port.core.config import Config, GitConfig, InstallConfig, ResourcesConfig
from cc_port.core.models import (
    PluginInstallation,
    PluginOrigin,
    PluginSpec,
    Registry,
    RegistryItem,
    ResourceKey,
)
from cc_port.core.ownership import (
    managed_marker_path,
    managed_resource_key,
    write_managed_marker,
)
from cc_port.core.platforms import PlatformProfile, PlatformsConfig
from cc_port.core.registry import load_registry, save_registry
from cc_port.infrastructure import git_ops
from cc_port.services import asset_sync, env_manager
from cc_port.services.asset_sync import RemoteSnapshot
from cc_port.services.env_manager import DiscoveredTool, EnvDiscoveryResult
from cc_port.services.local_path_probe import LocalPathProbe
from cc_port.services.plugin_management import DiscoveredPlugin
from cc_port.services.resource_commit import ResourceCommitBlocked
from cc_port.services.resource_discovery import DiscoveredResource

_GIT_RUNTIME = git_ops.discover_git_executable(configured="")
if _GIT_RUNTIME.path is None:
    raise RuntimeError(
        "Git is required for asset sync tests but was not found in PATH or "
        "standard installation locations. Run scripts/setup.ps1 first."
    )
GIT = _GIT_RUNTIME.path


def _config(
    tmp_path: Path,
    *,
    repo_url: str = "",
    skills_dir: Path | None = None,
    rules_dir: Path | None = None,
    prompts_dir: Path | None = None,
) -> Config:
    return Config(
        git=GitConfig(executable=str(GIT)),
        install=InstallConfig(target=str(tmp_path / "install-cache")),
        resources=ResourcesConfig(
            repo_url=repo_url,
            local_path=str(tmp_path / "legacy-workspace"),
            branch="main",
        ),
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="cursor",
                    enabled=True,
                    skills_dir=str(skills_dir or tmp_path / "cursor" / "skills"),
                    rules_dir=str(rules_dir or tmp_path / "cursor" / "rules"),
                    prompts_dir=str(prompts_dir or tmp_path / "cursor" / "commands"),
                    mcp_json=str(tmp_path / "cursor" / "mcp.json"),
                )
            ]
        ),
    )


def _skill(
    path: Path,
    *,
    name: str,
    description: str,
    body: str,
    version: str = "",
    author: str = "",
    license_name: str = "",
) -> None:
    path.mkdir(parents=True, exist_ok=True)
    metadata = [
        "---",
        f"name: {name}",
        f"description: {description}",
    ]
    if version:
        metadata.append(f"version: {version}")
    if author:
        metadata.append(f"author: {author}")
    if license_name:
        metadata.append(f"license: {license_name}")
    metadata.extend(["---", body, ""])
    (path / "SKILL.md").write_text(
        "\n".join(metadata),
        encoding="utf-8",
    )


def _snapshot(root: Path, registry: Registry, *, commit: str = "abc123") -> RemoteSnapshot:
    save_registry(registry, root / "registry.yaml")
    return RemoteSnapshot(
        root=root,
        registry=registry,
        commit=commit,
        branch="main",
        repo_url="https://github.com/example/resources",
    )


def _empty_discovery(**_kwargs: object) -> EnvDiscoveryResult:
    return EnvDiscoveryResult(tools=[], resources=[], mcp_servers=[])


def _run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        [str(GIT), *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _seed_bare_remote(tmp_path: Path) -> tuple[Path, Path]:
    seed = tmp_path / "seed"
    seed.mkdir()
    _run_git(seed, "init", "-b", "main")
    registry = Registry(
        items=[
            RegistryItem(
                name="demo",
                kind="skill",
                source="local",
                path="skills/demo",
                description="Remote description",
                version="0.5.0",
                author="Remote Author",
                license="Apache-2.0",
                tags=["preserved"],
            )
        ]
    )
    save_registry(registry, seed / "registry.yaml")
    _skill(
        seed / "skills" / "demo",
        name="demo",
        description="Remote description",
        body="remote body",
    )
    _run_git(seed, "add", "registry.yaml", "skills/demo/SKILL.md")
    _run_git(
        seed,
        "-c",
        "user.name=CC Port Test",
        "-c",
        "user.email=cc-port@example.test",
        "commit",
        "-m",
        "seed",
    )
    bare = tmp_path / "remote.git"
    subprocess.run(
        [str(GIT), "clone", "--bare", str(seed), str(bare)],
        check=True,
        capture_output=True,
        text=True,
    )
    return seed, bare


def _push_concurrent_change(
    tmp_path: Path,
    bare: Path,
    *,
    asset_body: str | None = None,
) -> None:
    clone = tmp_path / f"concurrent-{len(list(tmp_path.glob('concurrent-*')))}"
    subprocess.run(
        [str(GIT), "clone", "--branch", "main", str(bare), str(clone)],
        check=True,
        capture_output=True,
        text=True,
    )
    if asset_body is None:
        (clone / "README.md").write_text("unrelated\n", encoding="utf-8")
        changed = "README.md"
    else:
        skill = clone / "skills" / "demo" / "SKILL.md"
        skill.write_text(
            f"---\nname: demo\ndescription: Remote changed\n---\n{asset_body}\n",
            encoding="utf-8",
        )
        changed = "skills/demo/SKILL.md"
    _run_git(clone, "add", changed)
    _run_git(
        clone,
        "-c",
        "user.name=CC Port Test",
        "-c",
        "user.email=cc-port@example.test",
        "commit",
        "-m",
        "concurrent",
    )
    _run_git(clone, "push", "origin", "main")


def test_cursor_rule_and_prompt_use_distinct_native_targets(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote"
    (remote / "rules" / "demo").mkdir(parents=True)
    (remote / "rules" / "demo" / "rule.md").write_text("rule", encoding="utf-8")
    (remote / "prompts" / "demo").mkdir(parents=True)
    (remote / "prompts" / "demo" / "prompt.md").write_text("prompt", encoding="utf-8")
    snapshot = _snapshot(
        remote,
        Registry(
            items=[
                RegistryItem(name="demo", kind="rule", source="local", path="rules/demo"),
                RegistryItem(name="demo", kind="prompt", source="local", path="prompts/demo"),
            ]
        ),
    )

    inventory = asset_sync.build_asset_inventory(
        config=_config(tmp_path),
        remote_snapshot=snapshot,
    )

    assert {row.resource_key for row in inventory.rows} == {"rule:demo", "prompt:demo"}
    assert {row.status for row in inventory.rows} == {"remote-only"}
    by_key = {row.resource_key: row for row in inventory.rows}
    assert by_key["rule:demo"].target_path == tmp_path / "cursor" / "rules" / "demo"
    assert by_key["prompt:demo"].target_path == tmp_path / "cursor" / "commands" / "demo.md"


def test_logical_inventory_preserves_unknown_local_and_unavailable_remote_snapshot(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote"
    _skill(remote / "skills" / "demo", name="demo", description="Remote", body="remote")
    snapshot = _snapshot(
        remote,
        Registry(
            items=[
                RegistryItem(
                    name="demo",
                    kind="skill",
                    source="local",
                    path="skills/demo",
                    description="Repository description",
                )
            ]
        ),
    )
    snapshot.available = False
    snapshot.warning = "Using cached remote snapshot."

    inventory = asset_sync.build_asset_inventory(
        config=_config(tmp_path),
        scan_local=False,
        remote_snapshot=snapshot,
    )

    logical = inventory.resources[0]
    assert logical.description == "Repository description"
    assert logical.description_source == "remote"
    assert logical.local_status == "unknown"
    assert logical.remote_status == "unavailable"
    assert logical.remote.exists is True
    assert logical.status == "uncomparable"
    assert logical.diff_summary == ["Local assets have not been scanned yet."]
    assert inventory.remote_warning == "Using cached remote snapshot."


def test_inventory_refreshes_remote_and_scans_local_in_parallel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(tmp_path / "remote", Registry(items=[]))
    rendezvous = threading.Barrier(2, timeout=2)

    def refresh_remote(*_args: object, **_kwargs: object) -> RemoteSnapshot:
        rendezvous.wait()
        return snapshot

    def scan_local(*_args: object, **_kwargs: object) -> EnvDiscoveryResult:
        rendezvous.wait()
        return _empty_discovery()

    monkeypatch.setattr(asset_sync, "_refresh_remote_snapshot", refresh_remote)
    monkeypatch.setattr(asset_sync, "discover_environment", scan_local)

    inventory = asset_sync.build_asset_inventory(
        config=_config(tmp_path),
        scan_local=True,
        refresh_remote=True,
    )

    assert inventory.scanned_local is True
    assert inventory.remote_commit == "abc123"


def test_inventory_scans_configured_custom_skills_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = tmp_path / "wsl-claude-skills"
    local = skills / "local-only"
    _skill(
        local,
        name="local-only",
        description="WSL Claude skill",
        body="local",
    )
    cfg = Config(
        git=GitConfig(executable=str(GIT)),
        install=InstallConfig(target=str(tmp_path / "install-cache")),
        resources=ResourcesConfig(
            local_path=str(tmp_path / "legacy-workspace"),
            branch="main",
        ),
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="claude-code",
                    enabled=True,
                    skills_dir=str(skills),
                )
            ]
        ),
    )
    snapshot = _snapshot(tmp_path / "remote", Registry(items=[]))
    calls: list[dict[str, object]] = []

    def discover(**kwargs: object) -> EnvDiscoveryResult:
        calls.append(kwargs)
        return env_manager.discover_environment(
            home=tmp_path / "home",
            **kwargs,
        )

    monkeypatch.setattr(asset_sync, "discover_environment", discover)

    inventory = asset_sync.build_asset_inventory(
        config=cfg,
        scan_local=True,
        remote_snapshot=snapshot,
    )

    row = next(item for item in inventory.rows if item.resource_key == "skill:local-only")
    assert row.platform == "claude-code"
    assert row.local_path == local.resolve()
    assert row.status == "local-only"
    assert calls == [
        {
            "config": cfg,
            "scan_global": True,
            "project_ids": None,
        }
    ]


def test_inventory_scans_configured_custom_claude_mcp_and_plugins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude = tmp_path / "wsl-claude"
    mcp_json = claude / "mcp.json"
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
    plugin = claude / "plugins" / "local-only"
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
    cfg = Config(
        git=GitConfig(executable=str(GIT)),
        install=InstallConfig(target=str(tmp_path / "install-cache")),
        resources=ResourcesConfig(
            local_path=str(tmp_path / "legacy-workspace"),
            branch="main",
        ),
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="claude-code",
                    enabled=True,
                    mcp_json=str(mcp_json),
                    plugins_dir=str(plugin.parent),
                )
            ]
        ),
    )
    snapshot = _snapshot(tmp_path / "remote", Registry(items=[]))

    def discover(**kwargs: object) -> EnvDiscoveryResult:
        return env_manager.discover_environment(
            home=tmp_path / "home",
            **kwargs,
        )

    monkeypatch.setattr(asset_sync, "discover_environment", discover)

    inventory = asset_sync.build_asset_inventory(
        config=cfg,
        scan_local=True,
        remote_snapshot=snapshot,
    )

    mcp = next(item for item in inventory.rows if item.resource_key == "mcp:wsl-local")
    assert mcp.platform == "claude-code"
    assert mcp.local_path == mcp_json.resolve()
    assert mcp.status == "local-only"

    plugin_row = next(
        item
        for item in inventory.rows
        if item.resource_key == "plugin:claude-code-local-local-only"
    )
    assert plugin_row.platform == "claude-code"
    assert plugin_row.local_path == plugin.resolve()
    assert plugin_row.status == "local-only"
    assert plugin_row.plugin_track == "content"


def test_logical_inventory_merges_remote_and_discovered_local_union(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    _skill(remote / "skills" / "remote", name="remote", description="Remote", body="remote")
    local = tmp_path / "cursor" / "skills" / "local"
    _skill(local, name="local", description="Local description", body="local")
    snapshot = _snapshot(
        remote,
        Registry(
            items=[
                RegistryItem(
                    name="remote",
                    kind="skill",
                    source="local",
                    path="skills/remote",
                    description="Remote description",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        asset_sync,
        "discover_environment",
        lambda **_kwargs: EnvDiscoveryResult(
            tools=[],
            resources=[
                DiscoveredResource(
                    id="cursor:skill:local",
                    tool="cursor",
                    source="configured",
                    kind="skill",
                    name_hint="local",
                    path=local,
                )
            ],
            mcp_servers=[],
        ),
    )

    inventory = asset_sync.build_asset_inventory(
        config=_config(tmp_path),
        scan_local=True,
        remote_snapshot=snapshot,
    )

    logical = {item.resource_key: item for item in inventory.resources}
    assert set(logical) == {"skill:local", "skill:remote"}
    assert logical["skill:local"].status == "local-only"
    assert logical["skill:local"].description == "Local description"
    assert logical["skill:remote"].status == "remote-only"
    assert logical["skill:remote"].description == "Remote description"


def test_asset_content_diff_reports_changed_files_on_demand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    remote_skill = remote / "skills" / "demo"
    local_skill = tmp_path / "cursor" / "skills" / "demo"
    _skill(remote_skill, name="demo", description="Remote", body="remote body")
    _skill(local_skill, name="demo", description="Local", body="local body")
    (remote_skill / "remote-only.txt").write_text("remote\n", encoding="utf-8")
    (local_skill / "local-only.txt").write_text("local\n", encoding="utf-8")
    (remote_skill / "image.bin").write_bytes(b"\x00remote")
    (local_skill / "image.bin").write_bytes(b"\x00local")
    (remote_skill / "node_modules").mkdir()
    (remote_skill / "node_modules" / "ignored.js").write_text(
        "remote dependency",
        encoding="utf-8",
    )
    snapshot = _snapshot(
        remote,
        Registry(
            items=[
                RegistryItem(
                    name="demo",
                    kind="skill",
                    source="local",
                    path="skills/demo",
                )
            ]
        ),
    )
    cfg = _config(tmp_path, skills_dir=local_skill.parent)
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)
    inventory = asset_sync.build_asset_inventory(
        config=cfg,
        scan_local=True,
        remote_snapshot=snapshot,
    )
    row = next(item for item in inventory.rows if item.local_exists)
    monkeypatch.setattr(
        asset_sync,
        "_refresh_remote_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )

    result = asset_sync.build_asset_content_diff(
        "skill:demo",
        row.local_instance_id,
        config=cfg,
    )

    assert result.remote_commit == "abc123"
    assert result.added_files == 1
    assert result.deleted_files == 1
    assert result.modified_files == 2
    assert result.binary_files == 1
    assert result.truncated is False
    by_path = {item.path: item for item in result.files}
    assert set(by_path) == {
        "SKILL.md",
        "image.bin",
        "local-only.txt",
        "remote-only.txt",
    }
    assert "-remote body" in by_path["SKILL.md"].diff
    assert "+local body" in by_path["SKILL.md"].diff
    assert by_path["image.bin"].binary is True


def test_logical_inventory_folds_identical_instances_and_preserves_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = tmp_path / "cursor" / "skills" / "demo"
    codex = tmp_path / "codex" / "skills" / "demo"
    _skill(cursor, name="demo", description="Same", body="same")
    _skill(codex, name="demo", description="Same", body="same")
    cfg = Config(
        git=GitConfig(executable=str(GIT)),
        install=InstallConfig(target=str(tmp_path / "install-cache")),
        resources=ResourcesConfig(
            branch="main",
            local_path=str(tmp_path / "legacy-workspace"),
        ),
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(name="cursor", enabled=True, skills_dir=str(cursor.parent)),
                PlatformProfile(name="codex", enabled=True, skills_dir=str(codex.parent)),
            ]
        ),
    )

    def discovery(**_kwargs: object) -> EnvDiscoveryResult:
        return EnvDiscoveryResult(
            tools=[],
            resources=[
                DiscoveredResource(
                    id="cursor:skill:demo",
                    tool="cursor",
                    source="configured",
                    kind="skill",
                    name_hint="demo",
                    path=cursor,
                ),
                DiscoveredResource(
                    id="codex:skill:demo",
                    tool="codex",
                    source="configured",
                    kind="skill",
                    name_hint="demo",
                    path=codex,
                ),
            ],
            mcp_servers=[],
        )

    monkeypatch.setattr(asset_sync, "discover_environment", discovery)
    snapshot = _snapshot(tmp_path / "remote", Registry())

    first = asset_sync.build_asset_inventory(
        config=cfg,
        scan_local=True,
        remote_snapshot=snapshot,
    ).resources[0]
    assert first.local_status == "identical-copies"
    assert len(first.local_instances) == 2

    _skill(codex, name="demo", description="Same", body="different")
    second = asset_sync.build_asset_inventory(
        config=cfg,
        scan_local=True,
        remote_snapshot=snapshot,
    ).resources[0]
    assert second.local_status == "variants"
    assert second.status == "local-only"
    monkeypatch.setattr(
        asset_sync,
        "_refresh_remote_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )
    batch = asset_sync.build_asset_batch_plan(
        "upload",
        resource_keys=["skill:demo"],
        config=cfg,
    )
    assert len(batch.checked_resources) == 1
    assert batch.checked_resources[0].resource_key == "skill:demo"
    assert batch.checked_resources[0].local_status == "variants"
    assert batch.checked_resources[0].remote_status == "missing"
    assert batch.checked_resources[0].status == "local-only"
    assert batch.blocked_count == 1
    assert "select a source instance" in batch.items[0].reason
    assert batch.items[0].reason_ref is not None
    assert batch.items[0].reason_ref.code == "asset.batch.select_source_instance"

    separate = asset_sync.build_asset_batch_plan(
        "upload",
        resource_keys=["skill:demo"],
        choices=[
            asset_sync.AssetBatchChoice(
                resource_key="skill:demo",
                local_instance_id="cursor:skill:demo",
                resolution="rename",
                new_name="demo-cursor",
            ),
            asset_sync.AssetBatchChoice(
                resource_key="skill:demo",
                local_instance_id="codex:skill:demo",
                resolution="rename",
                new_name="demo-codex",
            ),
        ],
        config=cfg,
    )
    assert separate.blocked_count == 0
    assert separate.executable_count == 2
    assert {item.disposition for item in separate.items} == {"rename"}
    assert {item.target_resource_key for item in separate.items} == {
        "skill:demo-cursor",
        "skill:demo-codex",
    }


def test_external_root_symlink_requires_confirmation_before_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = tmp_path / "external" / "demo"
    logical = tmp_path / "cursor" / "skills" / "demo"
    _skill(canonical, name="demo", description="Demo", body="body")
    logical.parent.mkdir(parents=True)
    try:
        logical.symlink_to(canonical, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Native symlink creation is unavailable: {exc}")
    discovered = env_manager.discover_environment(
        config=_config(tmp_path, skills_dir=logical.parent),
        home=tmp_path / "home",
    )
    monkeypatch.setattr(asset_sync, "discover_environment", lambda **_kwargs: discovered)
    snapshot = _snapshot(tmp_path / "remote", Registry())
    cfg = _config(tmp_path, skills_dir=logical.parent)
    inventory = asset_sync.build_asset_inventory(
        config=cfg,
        scan_local=True,
        remote_snapshot=snapshot,
    )
    row = next(item for item in inventory.rows if item.resource_key == "skill:demo")

    blocked = asset_sync.build_asset_action_plan(
        "upload",
        kind="skill",
        name="demo",
        platform="cursor",
        local_instance_id=row.local_instance_id,
        config=cfg,
        _remote_snapshot=snapshot,
        _inventory=inventory,
        _persist=False,
    )
    confirmed = asset_sync.build_asset_action_plan(
        "upload",
        kind="skill",
        name="demo",
        platform="cursor",
        local_instance_id=row.local_instance_id,
        link_target_confirmed=True,
        config=cfg,
        _remote_snapshot=snapshot,
        _inventory=inventory,
        _persist=False,
    )

    assert row.path_kind == "symlink"
    assert row.local_path == logical.absolute()
    assert row.local_content_path == canonical.resolve()
    assert row.link_target_trusted is False
    assert any(
        ref.code == "asset.blocker.link_target_confirmation_required"
        for ref in blocked.blocker_refs
    )
    assert not any(
        ref.code == "asset.blocker.link_target_confirmation_required"
        for ref in confirmed.blocker_refs
    )


def test_upload_snapshot_materializes_ordinary_files(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    logical = tmp_path / "logical"
    _skill(canonical, name="demo", description="Demo", body="body")
    expected = asset_sync.resource_hash_path(canonical)

    with asset_sync._ordinary_upload_snapshot(
        canonical,
        logical_path=logical,
        expected_fingerprint=expected,
    ) as snapshot:
        assert snapshot.is_dir()
        assert snapshot.is_symlink() is False
        assert (snapshot / "SKILL.md").read_text(encoding="utf-8").endswith("body\n")


def test_planned_local_source_rejects_link_retarget_with_same_content(
    tmp_path: Path,
) -> None:
    logical = tmp_path / "skills" / "demo"
    first = tmp_path / "first" / "demo"
    second = tmp_path / "second" / "demo"
    _skill(first, name="demo", description="Demo", body="same")
    _skill(second, name="demo", description="Demo", body="same")
    fingerprint = asset_sync.resource_hash_path(first)
    row = asset_sync.AssetPlatformRow(
        resource_key="skill:demo",
        kind="skill",
        name="demo",
        platform="cursor",
        local_instance_id="linked-demo",
        local_locator="discovered-resource",
        install_name="demo",
        configured=True,
        enabled=True,
        detected=True,
        supported=True,
        remote_exists=False,
        local_exists=True,
        remote_writable=False,
        read_only_reference=False,
        remote_path=None,
        local_path=logical,
        target_path=logical,
        ownership="unmanaged",
        status="local-only",
        remote_commit="abc123",
        local_fingerprint=fingerprint,
        local_content_path=second,
        path_kind="symlink",
        link_health="ready",
        link_target=str(second),
        reparse_tag="0xA000000C",
        link_target_trusted=False,
    )
    plan = asset_sync.AssetActionPlan(
        operation_id="plan",
        action="upload",
        resource_key="skill:demo",
        target_resource_key="skill:demo",
        kind="skill",
        name="demo",
        platform="cursor",
        local_instance_id="linked-demo",
        local_locator="discovered-resource",
        remote_commit="abc123",
        remote_target_exists=False,
        remote_target_fingerprint="",
        local_source_fingerprint=fingerprint,
        target_path=logical,
        target_exists=True,
        target_fingerprint=fingerprint,
        target_managed=False,
        source_path=logical,
        source_content_path=first,
        source_path_kind="symlink",
        source_link_health="ready",
        source_link_target=str(first),
        source_reparse_tag="0xA000000C",
        link_target_confirmed=True,
    )

    assert asset_sync._planned_local_source_matches(row, plan) is False
    assert [
        ref.code for ref in asset_sync._upload_plan_blocker_refs(row)
    ] == ["asset.blocker.link_target_confirmation_required"]
    assert asset_sync._upload_plan_blocker_refs(
        row,
        link_target_confirmed=True,
    ) == []


def test_platform_install_alias_resolves_collision(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    (remote / "rules" / "demo").mkdir(parents=True)
    (remote / "rules" / "demo" / "rule.md").write_text("rule", encoding="utf-8")
    (remote / "prompts" / "demo").mkdir(parents=True)
    (remote / "prompts" / "demo" / "prompt.md").write_text("prompt", encoding="utf-8")
    snapshot = _snapshot(
        remote,
        Registry(
            items=[
                RegistryItem(name="demo", kind="rule", source="local", path="rules/demo"),
                RegistryItem(
                    name="demo",
                    kind="prompt",
                    source="local",
                    path="prompts/demo",
                    platform_install_dirs={"cursor": "demo-prompt"},
                ),
            ]
        ),
    )

    inventory = asset_sync.build_asset_inventory(
        config=_config(tmp_path),
        remote_snapshot=snapshot,
    )

    by_key = {row.resource_key: row for row in inventory.rows}
    assert by_key["rule:demo"].status == "remote-only"
    assert by_key["prompt:demo"].status == "remote-only"
    assert by_key["prompt:demo"].target_path.name == "demo-prompt.md"


def test_cursor_prompt_download_writes_managed_command_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    prompt_dir = remote / "prompts" / "demo"
    prompt_dir.mkdir(parents=True)
    prompt_text = "# Demo command\n\nReturn CURSOR_PROMPT_NONCE_42 only.\n"
    (prompt_dir / "demo.md").write_text(prompt_text, encoding="utf-8")
    snapshot = _snapshot(
        remote,
        Registry(
            items=[
                RegistryItem(
                    name="demo",
                    kind="prompt",
                    source="local",
                    path="prompts/demo",
                    platforms=["cursor"],
                )
            ]
        ),
    )
    cfg = _config(tmp_path)
    target = tmp_path / "cursor" / "commands" / "demo.md"
    monkeypatch.setattr(asset_sync, "_refresh_remote_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)

    plan = asset_sync.build_asset_action_plan(
        "download",
        kind="prompt",
        name="demo",
        platform="cursor",
        config=cfg,
    )
    result = asset_sync.apply_asset_action_plan(plan.operation_id, config=cfg)

    assert result.status == "succeeded"
    assert target.read_text(encoding="utf-8") == prompt_text
    assert managed_resource_key(target) == "prompt:demo"
    inventory = asset_sync.build_asset_inventory(
        config=cfg,
        remote_snapshot=snapshot,
    )
    row = next(row for row in inventory.rows if row.resource_key == "prompt:demo")
    assert row.status == "same"
    assert row.ownership == "managed"


def test_cursor_prompt_download_rolls_back_file_and_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    prompt_dir = remote / "prompts" / "demo"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "demo.md").write_text("remote prompt\n", encoding="utf-8")
    entry = RegistryItem(
        name="demo",
        kind="prompt",
        source="local",
        path="prompts/demo",
        platforms=["cursor"],
    )
    snapshot = _snapshot(remote, Registry(items=[entry]))
    cfg = _config(tmp_path)
    target = tmp_path / "cursor" / "commands" / "demo.md"
    target.parent.mkdir(parents=True)
    target.write_text("local prompt\n", encoding="utf-8")
    marker = write_managed_marker(target, entry, platform="cursor")
    assert marker is not None
    marker_before = marker.read_bytes()
    monkeypatch.setattr(asset_sync, "_refresh_remote_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)
    plan = asset_sync.build_asset_action_plan(
        "download",
        kind="prompt",
        name="demo",
        platform="cursor",
        config=cfg,
    )
    original_write_marker = asset_sync.write_managed_marker

    def corrupt_marker_then_fail(
        marker_target: Path,
        marker_entry: RegistryItem,
        *,
        platform: str,
        file_target: bool = False,
    ) -> Path | None:
        marker_path = original_write_marker(
            marker_target,
            marker_entry,
            platform=platform,
            file_target=file_target,
        )
        assert marker_path is not None
        marker_path.write_text('{"corrupted": true}\n', encoding="utf-8")
        raise RuntimeError("simulated marker failure")

    monkeypatch.setattr(asset_sync, "write_managed_marker", corrupt_marker_then_fail)

    with pytest.raises(RuntimeError, match="simulated marker failure"):
        asset_sync.apply_asset_action_plan(plan.operation_id, config=cfg)

    assert target.read_text(encoding="utf-8") == "local prompt\n"
    assert managed_marker_path(target).read_bytes() == marker_before
    assert managed_resource_key(target) == "prompt:demo"


def test_cursor_prompt_download_rolls_back_when_marker_is_not_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    prompt_dir = remote / "prompts" / "demo"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "demo.md").write_text("remote prompt\n", encoding="utf-8")
    entry = RegistryItem(
        name="demo",
        kind="prompt",
        source="local",
        path="prompts/demo",
        platforms=["cursor"],
    )
    snapshot = _snapshot(remote, Registry(items=[entry]))
    cfg = _config(tmp_path)
    target = tmp_path / "cursor" / "commands" / "demo.md"
    target.parent.mkdir(parents=True)
    target.write_text("unmanaged local prompt\n", encoding="utf-8")
    marker = managed_marker_path(target, file_target=True)
    monkeypatch.setattr(asset_sync, "_refresh_remote_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)
    plan = asset_sync.build_asset_action_plan(
        "download",
        kind="prompt",
        name="demo",
        platform="cursor",
        overwrite_unmanaged=True,
        config=cfg,
    )
    monkeypatch.setattr(
        asset_sync,
        "write_managed_marker",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(
        asset_sync.AssetSyncError,
        match="ownership verification failed",
    ):
        asset_sync.apply_asset_action_plan(plan.operation_id, config=cfg)

    assert target.read_text(encoding="utf-8") == "unmanaged local prompt\n"
    assert not marker.exists()


def test_cursor_prompt_directory_replacement_rolls_back_adjacent_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    prompt_dir = remote / "prompts" / "demo"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "demo.md").write_text("remote prompt\n", encoding="utf-8")
    entry = RegistryItem(
        name="demo",
        kind="prompt",
        source="local",
        path="prompts/demo",
        platforms=["cursor"],
    )
    snapshot = _snapshot(remote, Registry(items=[entry]))
    cfg = _config(tmp_path)
    target = tmp_path / "cursor" / "commands" / "demo.md"
    target.mkdir(parents=True)
    sentinel = target / "keep.txt"
    sentinel.write_text("keep me\n", encoding="utf-8")
    marker = managed_marker_path(target, file_target=True)
    monkeypatch.setattr(asset_sync, "_refresh_remote_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)
    plan = asset_sync.build_asset_action_plan(
        "download",
        kind="prompt",
        name="demo",
        platform="cursor",
        overwrite_unmanaged=True,
        config=cfg,
    )
    original_write_marker = asset_sync.write_managed_marker

    def write_marker_then_fail(
        marker_target: Path,
        marker_entry: RegistryItem,
        *,
        platform: str,
        file_target: bool = False,
    ) -> Path | None:
        marker_path = original_write_marker(
            marker_target,
            marker_entry,
            platform=platform,
            file_target=file_target,
        )
        assert marker_path == marker
        raise RuntimeError("simulated post-marker failure")

    monkeypatch.setattr(asset_sync, "write_managed_marker", write_marker_then_fail)

    with pytest.raises(RuntimeError, match="simulated post-marker failure"):
        asset_sync.apply_asset_action_plan(plan.operation_id, config=cfg)

    assert target.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "keep me\n"
    assert not marker.exists()


def test_cursor_prompt_download_blocks_ambiguous_remote_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    prompt_dir = remote / "prompts" / "demo"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "first.md").write_text("first\n", encoding="utf-8")
    (prompt_dir / "second.md").write_text("second\n", encoding="utf-8")
    snapshot = _snapshot(
        remote,
        Registry(
            items=[
                RegistryItem(
                    name="demo",
                    kind="prompt",
                    source="local",
                    path="prompts/demo",
                    platforms=["cursor"],
                )
            ]
        ),
    )
    monkeypatch.setattr(asset_sync, "_refresh_remote_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)

    plan = asset_sync.build_asset_action_plan(
        "download",
        kind="prompt",
        name="demo",
        platform="cursor",
        config=_config(tmp_path),
    )

    assert plan.blocked is True
    assert "exactly one" in " ".join(plan.blockers).lower()


def test_cursor_prompt_download_requires_confirmation_for_dangling_target_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    prompt_dir = remote / "prompts" / "demo"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "demo.md").write_text("safe prompt\n", encoding="utf-8")
    snapshot = _snapshot(
        remote,
        Registry(
            items=[
                RegistryItem(
                    name="demo",
                    kind="prompt",
                    source="local",
                    path="prompts/demo",
                    platforms=["cursor"],
                )
            ]
        ),
    )
    cfg = _config(tmp_path)
    target = tmp_path / "cursor" / "commands" / "demo.md"
    outside = tmp_path / "outside.md"
    target.parent.mkdir(parents=True)
    try:
        target.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")
    monkeypatch.setattr(asset_sync, "_refresh_remote_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)

    blocked = asset_sync.build_asset_action_plan(
        "download",
        kind="prompt",
        name="demo",
        platform="cursor",
        config=cfg,
    )

    assert blocked.blocked is True
    assert "unmanaged" in " ".join(blocked.blockers).lower()
    assert target.is_symlink()
    assert not outside.exists()

    confirmed = asset_sync.build_asset_action_plan(
        "download",
        kind="prompt",
        name="demo",
        platform="cursor",
        overwrite_unmanaged=True,
        config=cfg,
    )
    assert confirmed.blocked is False
    result = asset_sync.apply_asset_action_plan(confirmed.operation_id, config=cfg)

    assert result.status == "succeeded"
    assert target.is_file()
    assert not target.is_symlink()
    assert target.read_text(encoding="utf-8") == "safe prompt\n"
    assert not outside.exists()


def test_dangling_native_symlink_blocker_is_action_specific(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    prompt_dir = remote / "prompts" / "demo"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "demo.md").write_text("safe prompt\n", encoding="utf-8")
    snapshot = _snapshot(
        remote,
        Registry(
            items=[
                RegistryItem(
                    name="demo",
                    kind="prompt",
                    source="local",
                    path="prompts/demo",
                    platforms=["cursor"],
                )
            ]
        ),
    )
    cfg = _config(tmp_path)
    target = (tmp_path / "cursor" / "commands" / "demo.md").absolute()
    real_probe = asset_sync.probe_local_path

    def probe(candidate: Path | str) -> LocalPathProbe:
        logical = Path(candidate).expanduser().absolute()
        if logical == target:
            return LocalPathProbe(
                logical_path=logical,
                content_path=None,
                path_kind="symlink",
                health="dangling",
                raw_target=str(tmp_path / "outside.md"),
                problem="The link target does not exist.",
            )
        return real_probe(candidate)

    monkeypatch.setattr(asset_sync, "probe_local_path", probe)
    monkeypatch.setattr(asset_sync, "_refresh_remote_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)

    blocked_download = asset_sync.build_asset_action_plan(
        "download",
        kind="prompt",
        name="demo",
        platform="cursor",
        config=cfg,
    )
    confirmed_download = asset_sync.build_asset_action_plan(
        "download",
        kind="prompt",
        name="demo",
        platform="cursor",
        overwrite_unmanaged=True,
        config=cfg,
    )
    blocked_upload = asset_sync.build_asset_action_plan(
        "upload",
        kind="prompt",
        name="demo",
        platform="cursor",
        link_target_confirmed=True,
        config=cfg,
    )

    assert blocked_download.blocked is True
    assert "unmanaged" in " ".join(blocked_download.blockers).lower()
    assert confirmed_download.blocked is False
    assert blocked_upload.blocked is True
    assert "fingerprinted safely" in " ".join(blocked_upload.blockers).lower()


def test_prompt_remote_asset_fingerprint_includes_non_payload_files(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    prompt_dir = remote / "prompts" / "demo"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "demo.md").write_text("same prompt\n", encoding="utf-8")
    notes = prompt_dir / "notes.txt"
    notes.write_text("first\n", encoding="utf-8")
    entry = RegistryItem(
        name="demo",
        kind="prompt",
        source="local",
        path="prompts/demo",
        platforms=["cursor"],
    )
    snapshot = _snapshot(remote, Registry(items=[entry]))
    cfg = _config(tmp_path)

    before = asset_sync.build_asset_inventory(
        config=cfg,
        remote_snapshot=snapshot,
    ).rows[0]
    notes.write_text("second\n", encoding="utf-8")
    after = asset_sync.build_asset_inventory(
        config=cfg,
        remote_snapshot=snapshot,
    ).rows[0]

    assert before.remote_content_fingerprint == after.remote_content_fingerprint
    assert before.remote_asset_fingerprint != after.remote_asset_fingerprint


def test_detected_unconfigured_platform_is_visible_but_cannot_write_local_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    _skill(remote / "skills" / "demo", name="demo", description="Remote", body="remote")
    snapshot = _snapshot(
        remote,
        Registry(
            items=[
                RegistryItem(
                    name="demo",
                    kind="skill",
                    source="local",
                    path="skills/demo",
                )
            ]
        ),
    )
    discovery = EnvDiscoveryResult(
        tools=[
            DiscoveredTool(
                id="claude-code",
                name="Claude Code",
                root_path=tmp_path / "claude",
                detected=True,
                confidence="high",
                supports_kinds=["skill"],
            )
        ],
        resources=[],
        mcp_servers=[],
    )
    monkeypatch.setattr(
        asset_sync,
        "discover_environment",
        lambda **_kwargs: discovery,
    )

    inventory = asset_sync.build_asset_inventory(
        config=_config(tmp_path),
        scan_local=True,
        remote_snapshot=snapshot,
    )

    claude = next(row for row in inventory.rows if row.platform == "claude-code")
    assert claude.configured is False
    assert claude.detected is True
    assert "download" not in claude.available_actions
    assert "copy-to-local" not in claude.available_actions
    assert claude.available_actions == ["set-platform-install-name"]


def test_external_reference_is_read_only_but_local_content_can_be_copied_to_private_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    snapshot = _snapshot(
        remote,
        Registry(
            items=[
                RegistryItem(
                    name="demo",
                    kind="skill",
                    source="external",
                    repo="https://github.com/example/demo.git",
                    ref="main",
                )
            ]
        ),
    )
    local = tmp_path / "cursor" / "skills" / "demo"
    _skill(local, name="demo", description="Local", body="local")
    monkeypatch.setattr(asset_sync.git_ops, "remote_url_commit", lambda *_args, **_kwargs: "ref123")

    inventory = asset_sync.build_asset_inventory(
        config=_config(tmp_path),
        remote_snapshot=snapshot,
    )

    row = inventory.rows[0]
    assert row.status == "read-only-reference"
    assert row.reference_commit == "ref123"
    assert row.remote_writable is False
    assert row.available_actions == ["copy-to-remote"]


def test_download_requires_explicit_unmanaged_overwrite_and_writes_composite_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    _skill(remote / "skills" / "demo", name="demo", description="Remote", body="new")
    registry = Registry(
        items=[RegistryItem(name="demo", kind="skill", source="local", path="skills/demo")]
    )
    snapshot = _snapshot(remote, registry)
    target = tmp_path / "cursor" / "skills" / "demo"
    _skill(target, name="demo", description="Local", body="old")
    cfg = _config(tmp_path)
    monkeypatch.setattr(asset_sync, "_refresh_remote_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)

    blocked = asset_sync.build_asset_action_plan(
        "download",
        kind="skill",
        name="demo",
        platform="cursor",
        config=cfg,
    )
    assert blocked.blocked is True
    assert "unmanaged" in " ".join(blocked.blockers).lower()

    plan = asset_sync.build_asset_action_plan(
        "download",
        kind="skill",
        name="demo",
        platform="cursor",
        overwrite_unmanaged=True,
        config=cfg,
    )
    result = asset_sync.apply_asset_action_plan(plan.operation_id, config=cfg)

    assert result.status == "succeeded"
    assert "new" in (target / "SKILL.md").read_text(encoding="utf-8")
    assert managed_resource_key(target) == "skill:demo"


def test_copy_to_local_renames_incoming_version_and_keeps_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    _skill(remote / "skills" / "demo", name="demo", description="Remote", body="remote")
    registry = Registry(
        items=[RegistryItem(name="demo", kind="skill", source="local", path="skills/demo")]
    )
    snapshot = _snapshot(remote, registry)
    original = tmp_path / "cursor" / "skills" / "demo"
    _skill(original, name="demo", description="Local", body="original")
    cfg = _config(tmp_path)
    monkeypatch.setattr(asset_sync, "_refresh_remote_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)

    plan = asset_sync.build_asset_action_plan(
        "copy-to-local",
        kind="skill",
        name="demo",
        platform="cursor",
        new_name="demo-remote",
        config=cfg,
    )
    result = asset_sync.apply_asset_action_plan(plan.operation_id, config=cfg)

    copied = tmp_path / "cursor" / "skills" / "demo-remote"
    assert result.status == "succeeded"
    assert "original" in (original / "SKILL.md").read_text(encoding="utf-8")
    assert "remote" in (copied / "SKILL.md").read_text(encoding="utf-8")
    assert managed_resource_key(copied) == "skill:demo-remote"


def test_upload_replays_unchanged_target_on_latest_remote_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed, bare = _seed_bare_remote(tmp_path)
    target = tmp_path / "cursor" / "skills" / "demo"
    _skill(
        target,
        name="demo",
        description="Local description",
        body="local body",
        version="2.0.0",
        author="Local Author",
    )
    cfg = _config(tmp_path, repo_url=str(bare))
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)

    plan = asset_sync.build_asset_action_plan(
        "upload",
        kind="skill",
        name="demo",
        platform="cursor",
        config=cfg,
    )
    _push_concurrent_change(tmp_path, bare)

    result = asset_sync.apply_asset_action_plan(plan.operation_id, config=cfg)

    assert result.status == "succeeded"
    assert result.replayed_on_latest is True
    verify = tmp_path / "verify"
    subprocess.run(
        [str(GIT), "clone", "--branch", "main", str(bare), str(verify)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "local body" in (verify / "skills" / "demo" / "SKILL.md").read_text(encoding="utf-8")
    stored = load_registry(verify / "registry.yaml").get("demo", "skill")
    assert stored.description == "Local description"
    assert stored.version == "2.0.0"
    assert stored.author == "Local Author"
    assert stored.license == "Apache-2.0"
    assert stored.tags == ["preserved"]
    assert (verify / "README.md").read_text(encoding="utf-8") == "unrelated\n"


def test_upload_rejects_when_target_asset_changed_after_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed, bare = _seed_bare_remote(tmp_path)
    target = tmp_path / "cursor" / "skills" / "demo"
    _skill(target, name="demo", description="Local", body="local")
    cfg = _config(tmp_path, repo_url=str(bare))
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)

    plan = asset_sync.build_asset_action_plan(
        "upload",
        kind="skill",
        name="demo",
        platform="cursor",
        config=cfg,
    )
    _push_concurrent_change(tmp_path, bare, asset_body="changed remotely")

    result = asset_sync.apply_asset_action_plan(plan.operation_id, config=cfg)

    assert result.status == "stale-target"


def test_cursor_prompt_upload_and_copy_to_remote_keep_directory_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed, bare = _seed_bare_remote(tmp_path)
    command = tmp_path / "cursor" / "commands" / "demo.md"
    command.parent.mkdir(parents=True)
    command.write_text("local prompt\n", encoding="utf-8")
    cfg = _config(tmp_path, repo_url=str(bare))
    monkeypatch.setattr(
        asset_sync,
        "discover_environment",
        lambda **kwargs: env_manager.discover_environment(
            home=tmp_path / "isolated-home",
            **kwargs,
        ),
    )

    upload = asset_sync.build_asset_action_plan(
        "upload",
        kind="prompt",
        name="demo",
        platform="cursor",
        config=cfg,
    )
    upload_result = asset_sync.apply_asset_action_plan(upload.operation_id, config=cfg)

    assert upload_result.status == "succeeded"
    verify_upload = tmp_path / "verify-prompt-upload"
    subprocess.run(
        [str(GIT), "clone", "--branch", "main", str(bare), str(verify_upload)],
        check=True,
        capture_output=True,
        text=True,
    )
    stored = load_registry(verify_upload / "registry.yaml").get("demo", "prompt")
    assert stored is not None
    assert stored.path == "prompts/demo"
    assert (verify_upload / "prompts" / "demo" / "demo.md").read_text(
        encoding="utf-8"
    ) == "local prompt\n"

    copied = asset_sync.build_asset_action_plan(
        "copy-to-remote",
        kind="prompt",
        name="demo",
        platform="cursor",
        new_name="demo-copy",
        config=cfg,
    )
    copied_result = asset_sync.apply_asset_action_plan(copied.operation_id, config=cfg)

    assert copied_result.status == "succeeded"
    verify_copy = tmp_path / "verify-prompt-copy"
    subprocess.run(
        [str(GIT), "clone", "--branch", "main", str(bare), str(verify_copy)],
        check=True,
        capture_output=True,
        text=True,
    )
    copied_entry = load_registry(verify_copy / "registry.yaml").get(
        "demo-copy",
        "prompt",
    )
    assert copied_entry is not None
    assert copied_entry.path == "prompts/demo-copy"
    assert (verify_copy / "prompts" / "demo-copy" / "demo.md").read_text(
        encoding="utf-8"
    ) == "local prompt\n"


def test_remote_refresh_disables_host_autocrlf_and_preserves_fixture_blob_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = tmp_path / "prompt-seed"
    seed.mkdir()
    _run_git(seed, "init", "-b", "main")
    prompt_bytes = b"first line\nsecond line\n"
    prompt_path = seed / "prompts" / "demo" / "demo.md"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_bytes(prompt_bytes)
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="demo",
                    kind="prompt",
                    source="local",
                    path="prompts/demo",
                    platforms=["cursor"],
                )
            ]
        ),
        seed / "registry.yaml",
    )
    _run_git(seed, "add", "registry.yaml", "prompts/demo/demo.md")
    _run_git(
        seed,
        "-c",
        "user.name=CC Port Test",
        "-c",
        "user.email=cc-port@example.test",
        "commit",
        "-m",
        "seed prompt",
    )
    commit = _run_git(seed, "rev-parse", "HEAD")
    blob_bytes = subprocess.run(
        [str(GIT), "show", f"{commit}:prompts/demo/demo.md"],
        cwd=seed,
        check=True,
        capture_output=True,
    ).stdout
    assert blob_bytes == prompt_bytes
    bare = tmp_path / "prompt-remote.git"
    subprocess.run(
        [str(GIT), "clone", "--bare", str(seed), str(bare)],
        check=True,
        capture_output=True,
        text=True,
    )

    global_config = tmp_path / "autocrlf-global.gitconfig"
    global_config.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    cfg = _config(tmp_path, repo_url=str(bare))
    write_clone = tmp_path / "asset-write-clone"
    asset_sync._clone_remote_for_write(str(bare), write_clone, cfg)
    assert (write_clone / "prompts" / "demo" / "demo.md").read_bytes() == blob_bytes
    assert _run_git(
        write_clone,
        "config",
        "--local",
        "--get",
        "core.autocrlf",
    ) == "false"

    state_root = asset_sync.default_state_dir() / asset_sync.ASSET_STATE_DIR
    cache_key = hashlib.sha256(f"{bare}\0main".encode()).hexdigest()[:24]
    transport = state_root / asset_sync.REMOTE_CACHE_DIR / cache_key
    transport.parent.mkdir(parents=True)
    subprocess.run(
        [str(GIT), "clone", "--branch", "main", str(bare), str(transport)],
        check=True,
        capture_output=True,
        text=True,
    )
    transport_prompt = transport / "prompts" / "demo" / "demo.md"
    assert transport_prompt.read_bytes() == prompt_bytes.replace(b"\n", b"\r\n")

    snapshot_root = (
        state_root / asset_sync.REMOTE_SNAPSHOT_DIR / cache_key / commit
    )
    snapshot_root.parent.mkdir(parents=True)
    shutil.copytree(
        transport,
        snapshot_root,
        symlinks=True,
        ignore=lambda _directory, names: {".git"} & set(names),
    )
    local_prompt = tmp_path / "cursor" / "commands" / "demo.md"
    local_prompt.parent.mkdir(parents=True)
    local_prompt.write_bytes(prompt_bytes)
    stale_snapshot = RemoteSnapshot(
        root=snapshot_root,
        registry=load_registry(snapshot_root / "registry.yaml"),
        commit=commit,
        branch="main",
        repo_url=str(bare),
    )
    stale_row = asset_sync.build_asset_inventory(
        config=cfg,
        remote_snapshot=stale_snapshot,
    ).rows[0]
    assert stale_row.status == "content-different"

    refreshed = asset_sync._refresh_remote_snapshot(cfg, refresh=True)

    assert refreshed.root == snapshot_root
    assert refreshed.available, refreshed.warning
    assert transport_prompt.read_bytes() == blob_bytes
    assert _run_git(
        transport,
        "config",
        "--local",
        "--get",
        "core.autocrlf",
    ) == "false"
    assert (refreshed.root / "prompts" / "demo" / "demo.md").read_bytes() == blob_bytes
    assert (
        refreshed.root / asset_sync.REMOTE_SNAPSHOT_FORMAT_FILE
    ).read_text(encoding="utf-8").strip() == asset_sync.REMOTE_SNAPSHOT_FORMAT_VERSION
    refreshed_row = asset_sync.build_asset_inventory(
        config=cfg,
        remote_snapshot=refreshed,
    ).rows[0]
    assert refreshed_row.status == "same"
    assert global_config.read_text(encoding="utf-8") == "[core]\n\tautocrlf = true\n"


def test_snapshot_format_writer_atomically_replaces_existing_hardlink(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("must stay unchanged\n", encoding="utf-8")
    marker = snapshot / asset_sync.REMOTE_SNAPSHOT_FORMAT_FILE
    try:
        marker.hardlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Hard links are unavailable: {exc}")

    asset_sync._write_snapshot_format(snapshot)

    assert outside.read_text(encoding="utf-8") == "must stay unchanged\n"
    assert marker.read_text(encoding="utf-8").strip() == (
        asset_sync.REMOTE_SNAPSHOT_FORMAT_VERSION
    )


def test_snapshot_materialization_excludes_remote_control_path(
    tmp_path: Path,
) -> None:
    transport = tmp_path / "transport"
    transport.mkdir()
    (transport / "registry.yaml").write_text(
        "version: 7\nitems: []\n",
        encoding="utf-8",
    )
    remote_marker = transport / asset_sync.REMOTE_SNAPSHOT_FORMAT_FILE
    remote_marker.mkdir()
    (remote_marker / "poison.txt").write_text("remote-controlled\n", encoding="utf-8")
    snapshot_cache = tmp_path / "snapshots"
    snapshot = snapshot_cache / ("b" * 40)

    asset_sync._materialize_remote_snapshot(
        transport,
        snapshot,
        snapshot_cache,
    )

    marker = snapshot / asset_sync.REMOTE_SNAPSHOT_FORMAT_FILE
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8").strip() == (
        asset_sync.REMOTE_SNAPSHOT_FORMAT_VERSION
    )
    assert not (marker / "poison.txt").exists()


def test_snapshot_materialization_does_not_follow_remote_control_symlink(
    tmp_path: Path,
) -> None:
    transport = tmp_path / "transport"
    transport.mkdir()
    (transport / "registry.yaml").write_text(
        "version: 7\nitems: []\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("must stay unchanged\n", encoding="utf-8")
    remote_marker = transport / asset_sync.REMOTE_SNAPSHOT_FORMAT_FILE
    try:
        remote_marker.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")
    snapshot_cache = tmp_path / "snapshots"
    snapshot = snapshot_cache / ("c" * 40)

    asset_sync._materialize_remote_snapshot(
        transport,
        snapshot,
        snapshot_cache,
    )

    marker = snapshot / asset_sync.REMOTE_SNAPSHOT_FORMAT_FILE
    assert outside.read_text(encoding="utf-8") == "must stay unchanged\n"
    assert marker.is_file()
    assert not marker.is_symlink()
    assert marker.read_text(encoding="utf-8").strip() == (
        asset_sync.REMOTE_SNAPSHOT_FORMAT_VERSION
    )


def test_snapshot_materialization_rejects_symlink_registry(
    tmp_path: Path,
) -> None:
    transport = tmp_path / "transport"
    transport.mkdir()
    outside = tmp_path / "outside-registry.yaml"
    outside.write_text("version: 7\nitems: []\n", encoding="utf-8")
    try:
        (transport / "registry.yaml").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")
    snapshot_cache = tmp_path / "snapshots"
    snapshot = snapshot_cache / ("d" * 40)

    with pytest.raises(
        asset_sync.AssetSyncError,
        match="regular non-symlink file",
    ):
        asset_sync._materialize_remote_snapshot(
            transport,
            snapshot,
            snapshot_cache,
        )

    assert outside.read_text(encoding="utf-8") == "version: 7\nitems: []\n"
    assert not snapshot.exists()
    assert list(snapshot_cache.iterdir()) == []


def test_snapshot_materialization_requires_regular_registry_file(
    tmp_path: Path,
) -> None:
    transport = tmp_path / "transport"
    (transport / "registry.yaml").mkdir(parents=True)
    snapshot_cache = tmp_path / "snapshots"
    snapshot = snapshot_cache / ("e" * 40)

    with pytest.raises(
        asset_sync.AssetSyncError,
        match="regular non-symlink file",
    ):
        asset_sync._materialize_remote_snapshot(
            transport,
            snapshot,
            snapshot_cache,
        )

    assert not snapshot.exists()
    assert list(snapshot_cache.iterdir()) == []


def test_remote_content_path_rejects_ancestor_symlink_escape(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    outside = tmp_path / "outside"
    (outside / "demo").mkdir(parents=True)
    try:
        (snapshot / "prompts").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")
    entry = RegistryItem(
        name="demo",
        kind="prompt",
        source="local",
        path="prompts/demo",
    )

    assert asset_sync._remote_content_path(snapshot, entry) is None


def test_remote_content_path_rejects_terminal_symlink_inside_snapshot(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot"
    prompt_root = snapshot / "prompts"
    real = snapshot / "real-demo"
    prompt_root.mkdir(parents=True)
    real.mkdir()
    try:
        (prompt_root / "demo").symlink_to(real, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")
    entry = RegistryItem(
        name="demo",
        kind="prompt",
        source="local",
        path="prompts/demo",
    )

    assert asset_sync._remote_content_path(snapshot, entry) is None


def test_snapshot_migration_copy_failure_preserves_legacy_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = tmp_path / "transport"
    transport.mkdir()
    (transport / "registry.yaml").write_text("version: 7\nitems: []\n", encoding="utf-8")
    snapshot_cache = tmp_path / "snapshots"
    snapshot = snapshot_cache / ("a" * 40)
    snapshot.mkdir(parents=True)
    legacy = snapshot / "legacy-only.txt"
    legacy.write_text("preserve me\n", encoding="utf-8")

    def fail_copytree(*_args, **_kwargs):
        raise OSError("simulated snapshot copy failure")

    monkeypatch.setattr(asset_sync.shutil, "copytree", fail_copytree)

    with pytest.raises(OSError, match="simulated snapshot copy failure"):
        asset_sync._materialize_remote_snapshot(
            transport,
            snapshot,
            snapshot_cache,
        )

    assert legacy.read_text(encoding="utf-8") == "preserve me\n"
    assert list(snapshot_cache.iterdir()) == [snapshot]


def test_remote_refresh_replaces_symlink_transport_without_touching_target_repo(
    tmp_path: Path,
) -> None:
    seed, bare = _seed_bare_remote(tmp_path)
    config_before = (seed / ".git" / "config").read_bytes()
    index_before = (seed / ".git" / "index").read_bytes()
    head_before = _run_git(seed, "rev-parse", "HEAD")
    cfg = _config(tmp_path, repo_url=str(bare))
    state_root = asset_sync.default_state_dir() / asset_sync.ASSET_STATE_DIR
    cache_key = hashlib.sha256(f"{bare}\0main".encode()).hexdigest()[:24]
    transport = state_root / asset_sync.REMOTE_CACHE_DIR / cache_key
    transport.parent.mkdir(parents=True)
    try:
        transport.symlink_to(seed, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    refreshed = asset_sync._refresh_remote_snapshot(cfg, refresh=True)

    assert refreshed.available, refreshed.warning
    assert transport.is_dir()
    assert not transport.is_symlink()
    assert (seed / ".git" / "config").read_bytes() == config_before
    assert (seed / ".git" / "index").read_bytes() == index_before
    assert _run_git(seed, "rev-parse", "HEAD") == head_before


def test_remote_push_race_revalidates_and_retries_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed, bare = _seed_bare_remote(tmp_path)
    target = tmp_path / "cursor" / "skills" / "demo"
    _skill(target, name="demo", description="Local", body="local")
    cfg = _config(tmp_path, repo_url=str(bare))
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)
    plan = asset_sync.build_asset_action_plan(
        "upload",
        kind="skill",
        name="demo",
        platform="cursor",
        config=cfg,
    )
    real_push = asset_sync.git_ops.push
    calls = 0

    def flaky_push(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise git_ops.GitError("simulated non-fast-forward")
        return real_push(*args, **kwargs)

    monkeypatch.setattr(asset_sync.git_ops, "push", flaky_push)

    result = asset_sync.apply_asset_action_plan(plan.operation_id, config=cfg)

    assert result.status == "succeeded"
    assert result.push_retry_count == 1
    assert calls == 2


def test_same_kind_duplicate_content_warns_but_does_not_block_creation(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote"
    remote_rule = remote / "rules" / "existing"
    remote_rule.mkdir(parents=True)
    (remote_rule / "rule.md").write_text("same\n", encoding="utf-8")
    registry = Registry(
        items=[
            RegistryItem(
                name="existing",
                kind="rule",
                source="local",
                path="rules/existing",
            )
        ]
    )
    snapshot = _snapshot(remote, registry)
    local = tmp_path / "cursor" / "rules" / "new-name"
    local.mkdir(parents=True)
    (local / "rule.md").write_text("same\n", encoding="utf-8")

    # A local-only row is represented by a remote registry entry only after scanning;
    # use a small synthetic inventory to exercise the duplicate decision directly.
    assert asset_sync._remote_duplicate_keys(
        snapshot,
        "rule",
        asset_sync.resource_hash_path(local),
        exclude_key="rule:new-name",
    ) == ["rule:existing"]


def test_copy_to_remote_renames_local_version_and_preserves_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed, bare = _seed_bare_remote(tmp_path)
    local = tmp_path / "cursor" / "skills" / "demo"
    _skill(local, name="demo", description="Copied", body="local copy")
    cfg = _config(tmp_path, repo_url=str(bare))
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)

    plan = asset_sync.build_asset_action_plan(
        "copy-to-remote",
        kind="skill",
        name="demo",
        platform="cursor",
        new_name="demo-copy",
        config=cfg,
    )
    result = asset_sync.apply_asset_action_plan(plan.operation_id, config=cfg)

    assert result.status == "succeeded"
    verify = tmp_path / "verify-copy"
    subprocess.run(
        [str(GIT), "clone", "--branch", "main", str(bare), str(verify)],
        check=True,
        capture_output=True,
        text=True,
    )
    registry = load_registry(verify / "registry.yaml")
    assert registry.get("demo", "skill") is not None
    copied = registry.get("demo-copy", "skill")
    assert copied is not None
    assert copied.description == "Copied"
    assert "local copy" in (verify / "skills" / "demo-copy" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "remote body" in (verify / "skills" / "demo" / "SKILL.md").read_text(encoding="utf-8")


def test_remote_batch_applies_multiple_changes_in_one_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed, bare = _seed_bare_remote(tmp_path)
    local = tmp_path / "cursor" / "skills" / "demo"
    _skill(local, name="demo", description="Batch", body="batch body")
    cfg = _config(tmp_path, repo_url=str(bare))
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)
    snapshot = asset_sync._refresh_remote_snapshot(cfg, refresh=True)
    inventory = asset_sync.build_asset_inventory(
        config=cfg,
        scan_local=True,
        refresh_remote=False,
        remote_snapshot=snapshot,
    )
    upload = asset_sync.build_asset_action_plan(
        "upload",
        kind="skill",
        name="demo",
        platform="cursor",
        config=cfg,
        _remote_snapshot=snapshot,
        _inventory=inventory,
        _persist=False,
    )
    renamed = asset_sync.build_asset_action_plan(
        "copy-to-remote",
        kind="skill",
        name="demo",
        platform="cursor",
        new_name="demo-copy",
        config=cfg,
        _remote_snapshot=snapshot,
        _inventory=inventory,
        _persist=False,
    )

    results = asset_sync._apply_remote_asset_batch([upload, renamed], cfg)

    assert [item.status for item in results] == ["succeeded", "succeeded"]
    verify = tmp_path / "verify-batch"
    subprocess.run(
        [str(GIT), "clone", "--branch", "main", str(bare), str(verify)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert int(_run_git(verify, "rev-list", "--count", "HEAD")) == 2
    stored = load_registry(verify / "registry.yaml")
    assert stored.get("demo", "skill") is not None
    assert stored.get("demo-copy", "skill") is not None


def test_inventory_without_remote_refresh_never_clones_missing_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path, repo_url="https://example.invalid/resources.git")
    clone_calls: list[str] = []

    def unexpected_clone(url: str, *_args, **_kwargs) -> None:
        clone_calls.append(url)

    monkeypatch.setattr(git_ops, "clone", unexpected_clone)

    inventory = asset_sync.build_asset_inventory(
        config=cfg,
        scan_local=False,
        refresh_remote=False,
    )

    assert clone_calls == []
    assert inventory.remote_available is False
    assert inventory.remote_warning is not None
    assert "refresh was skipped" in inventory.remote_warning


def test_remote_batch_excludes_invalid_source_and_commits_valid_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed, bare = _seed_bare_remote(tmp_path)
    demo = tmp_path / "cursor" / "skills" / "demo"
    unsafe = tmp_path / "cursor" / "skills" / "unsafe"
    _skill(demo, name="demo", description="Safe", body="safe batch body")
    _skill(
        unsafe,
        name="unsafe",
        description="Unsafe",
        body="api_key: sk_test_secret_1234567890",
    )
    cfg = _config(tmp_path, repo_url=str(bare))
    discovery = EnvDiscoveryResult(
        tools=[],
        resources=[
            DiscoveredResource(
                id="cursor:skill:unsafe",
                tool="cursor",
                source="configured",
                kind="skill",
                name_hint="unsafe",
                path=unsafe,
            )
        ],
        mcp_servers=[],
    )
    monkeypatch.setattr(
        asset_sync,
        "discover_environment",
        lambda **_kwargs: discovery,
    )
    snapshot = asset_sync._refresh_remote_snapshot(cfg, refresh=True)
    inventory = asset_sync.build_asset_inventory(
        config=cfg,
        scan_local=True,
        refresh_remote=False,
        remote_snapshot=snapshot,
    )
    safe_plan = asset_sync.build_asset_action_plan(
        "upload",
        kind="skill",
        name="demo",
        platform="cursor",
        config=cfg,
        _remote_snapshot=snapshot,
        _inventory=inventory,
        _persist=False,
    )
    unsafe_plan = asset_sync.build_asset_action_plan(
        "upload",
        kind="skill",
        name="unsafe",
        platform="cursor",
        local_instance_id="cursor:skill:unsafe",
        config=cfg,
        _remote_snapshot=snapshot,
        _inventory=inventory,
        _persist=False,
    )

    results = asset_sync._apply_remote_asset_batch([safe_plan, unsafe_plan], cfg)

    by_key = {item.resource_key: item for item in results}
    assert by_key["skill:demo"].status == "succeeded"
    assert by_key["skill:unsafe"].status == "failed"
    assert "Secret-like content" in by_key["skill:unsafe"].message
    verify = tmp_path / "verify-valid-batch"
    subprocess.run(
        [str(GIT), "clone", "--branch", "main", str(bare), str(verify)],
        check=True,
        capture_output=True,
        text=True,
    )
    stored = load_registry(verify / "registry.yaml")
    assert "safe batch body" in (verify / "skills" / "demo" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert stored.get("unsafe", "skill") is None


def test_batch_apply_rejects_when_local_source_changes_after_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed, bare = _seed_bare_remote(tmp_path)
    local = tmp_path / "cursor" / "skills" / "demo"
    _skill(local, name="demo", description="Local", body="first version")
    cfg = _config(tmp_path, repo_url=str(bare))
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)
    plan = asset_sync.build_asset_batch_plan(
        "upload",
        resource_keys=["skill:demo"],
        config=cfg,
    )
    assert plan.executable_count == 1

    _skill(local, name="demo", description="Local", body="second version")
    result = asset_sync.apply_asset_batch_plan(
        "upload",
        resource_keys=["skill:demo"],
        expected_plan_hash=plan.plan_hash,
        config=cfg,
    )

    assert result.status == "stale-plan"
    assert result.stale_plan is not None
    assert result.stale_plan.plan_hash != plan.plan_hash


def test_batch_download_continues_independent_local_transactions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plans = [
        asset_sync.AssetActionPlan(
            operation_id=f"plan-{name}",
            action="download",
            resource_key=f"skill:{name}",
            target_resource_key=f"skill:{name}",
            kind="skill",
            name=name,
            platform="cursor",
            local_instance_id=f"expected-cursor-{name}",
            local_locator="expected",
            remote_commit="abc123",
            remote_target_exists=True,
            remote_target_fingerprint=f"remote-{name}",
            local_source_fingerprint="",
            target_path=None,
            target_exists=False,
            target_fingerprint="",
            target_managed=False,
        )
        for name in ("first", "second")
    ]
    current = asset_sync.AssetBatchPlan(
        direction="download",
        resource_keys=["skill:first", "skill:second"],
        target_platforms=["cursor"],
        remote_commit="abc123",
        plan_hash="download-hash",
        items=[
            asset_sync.AssetBatchPlanItem(
                id=plan.operation_id,
                resource_key=plan.resource_key,
                platform="cursor",
                local_instance_id=plan.local_instance_id,
                action="download",
                disposition="create",
                target_resource_key=plan.target_resource_key,
                plan=plan,
            )
            for plan in plans
        ],
        executable_count=2,
        blocked_count=0,
        skipped_count=0,
    )
    monkeypatch.setattr(
        asset_sync,
        "build_asset_batch_plan",
        lambda *_args, **_kwargs: current,
    )
    calls: list[str] = []

    def apply_local(plan: asset_sync.AssetActionPlan, _cfg: Config):
        calls.append(plan.resource_key)
        if plan.name == "first":
            raise RuntimeError("first failed")
        return asset_sync.AssetActionResult(
            operation_id=plan.operation_id,
            action=plan.action,
            status="succeeded",
            resource_key=plan.resource_key,
            target_resource_key=plan.target_resource_key,
            platform=plan.platform,
            message="done",
        )

    monkeypatch.setattr(asset_sync, "_apply_local_asset_action", apply_local)

    result = asset_sync.apply_asset_batch_plan(
        "download",
        resource_keys=current.resource_keys,
        target_platforms=["cursor"],
        expected_plan_hash="download-hash",
        config=Config(),
    )

    assert calls == ["skill:first", "skill:second"]
    assert result.status == "partial"
    assert [item.status for item in result.results] == ["failed", "succeeded"]


def test_upload_blocks_secret_like_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed, bare = _seed_bare_remote(tmp_path)
    local = tmp_path / "cursor" / "skills" / "demo"
    _skill(
        local,
        name="demo",
        description="Unsafe",
        body="api_key: sk_test_secret_1234567890",
    )
    cfg = _config(tmp_path, repo_url=str(bare))
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)
    plan = asset_sync.build_asset_action_plan(
        "upload",
        kind="skill",
        name="demo",
        platform="cursor",
        config=cfg,
    )

    with pytest.raises(ResourceCommitBlocked):
        asset_sync.apply_asset_action_plan(plan.operation_id, config=cfg)


def test_download_failure_rolls_back_existing_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    _skill(remote / "skills" / "demo", name="demo", description="Remote", body="new")
    snapshot = _snapshot(
        remote,
        Registry(
            items=[
                RegistryItem(
                    name="demo",
                    kind="skill",
                    source="local",
                    path="skills/demo",
                )
            ]
        ),
    )
    target = tmp_path / "cursor" / "skills" / "demo"
    _skill(target, name="demo", description="Local", body="original")
    cfg = _config(tmp_path)
    monkeypatch.setattr(asset_sync, "_refresh_remote_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)
    plan = asset_sync.build_asset_action_plan(
        "download",
        kind="skill",
        name="demo",
        platform="cursor",
        overwrite_unmanaged=True,
        config=cfg,
    )

    def fail_after_replacing(_source: Path, destination: Path, _kind: str) -> None:
        shutil.rmtree(destination)
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text("broken", encoding="utf-8")
        raise RuntimeError("simulated copy failure")

    monkeypatch.setattr(asset_sync, "_copy_asset_content", fail_after_replacing)

    with pytest.raises(RuntimeError, match="simulated copy failure"):
        asset_sync.apply_asset_action_plan(plan.operation_id, config=cfg)

    assert "original" in (target / "SKILL.md").read_text(encoding="utf-8")


def test_asset_plan_json_does_not_trust_tampered_resource_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    _skill(remote / "skills" / "demo", name="demo", description="Remote", body="body")
    snapshot = _snapshot(
        remote,
        Registry(
            items=[RegistryItem(name="demo", kind="skill", source="local", path="skills/demo")]
        ),
    )
    cfg = _config(tmp_path)
    monkeypatch.setattr(asset_sync, "_refresh_remote_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(asset_sync, "discover_environment", _empty_discovery)
    plan = asset_sync.build_asset_action_plan(
        "download",
        kind="skill",
        name="demo",
        platform="cursor",
        config=cfg,
    )
    plan_path = (
        Path(asset_sync.default_state_dir())
        / asset_sync.ASSET_PLAN_DIR
        / plan.operation_id
        / "plan.json"
    )
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    data["name"] = "other"
    plan_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(asset_sync.AssetPlanInvalid):
        asset_sync.load_asset_action_plan(plan.operation_id)


def _reference_spec(*, enabled: bool = True) -> PluginSpec:
    return PluginSpec(
        track="reference",
        platform="opencode",
        plugin_id="@acme/tool",
        origin=PluginOrigin(
            type="npm",
            package="@acme/tool",
            selector="^2.0.0",
        ),
        observed_version="2.4.1",
        installations=[PluginInstallation(scope="user", enabled=enabled)],
    )


def test_plugin_reference_mutation_writes_registry_only_and_preserves_selector(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    registry = Registry()
    key = ResourceKey(kind="plugin", name="opencode-npm-acme-tool")

    changed = asset_sync._mutate_plugin_reference(
        registry,
        registry_path,
        key,
        None,
        _reference_spec(),
        description="External tool",
    )

    entry = load_registry(registry_path).get(key.name, "plugin")
    assert changed is True
    assert entry is not None and entry.plugin is not None
    assert entry.path == ""
    assert entry.plugin.origin.selector == "^2.0.0"
    assert entry.plugin.observed_version == "2.4.1"
    assert not (tmp_path / "plugins").exists()


def test_unobserved_selector_does_not_replace_existing_reference_policy(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    existing = RegistryItem(
        name="codex-marketplace-chrome-openai",
        kind="plugin",
        source="external",
        plugin=PluginSpec(
            track="reference",
            platform="codex",
            plugin_id="chrome",
            origin=PluginOrigin(
                type="marketplace",
                marketplace="openai",
                selector="release-2026",
            ),
            installations=[PluginInstallation(scope="user", enabled=True)],
        ),
    )
    incoming = existing.plugin.model_copy(deep=True)
    incoming.origin.selector = ""
    incoming.observed_version = "26.7.0"
    registry = Registry(items=[existing])

    asset_sync._mutate_plugin_reference(
        registry,
        registry_path,
        existing.key(),
        existing,
        incoming,
        description="",
        preserve_selector=True,
    )

    stored = load_registry(registry_path).get(existing.name, "plugin")
    assert stored is not None and stored.plugin is not None
    assert stored.plugin.origin.selector == "release-2026"
    assert stored.plugin.observed_version == "26.7.0"


def test_managed_reference_can_sync_policy_metadata_but_never_cache_content(
    tmp_path: Path,
) -> None:
    cache = tmp_path / ".codex" / "plugins" / "cache" / "org" / "chrome" / "1.0.0"
    cache.mkdir(parents=True)
    (cache / "credential.txt").write_text("must-not-upload", encoding="utf-8")
    candidate = DiscoveredPlugin(
        id="managed-chrome",
        platform="codex",
        plugin_id="chrome",
        track="reference",
        origin_type="marketplace",
        scope="managed",
        enabled=True,
        writable=False,
        path=cache,
        marketplace="org",
        observed_version="1.0.0",
    )
    remote = tmp_path / "remote"
    snapshot = _snapshot(remote, Registry())
    row = asset_sync._plugin_candidate_row(
        candidate,
        ResourceKey(kind="plugin", name=candidate.resource_name),
        None,
        snapshot,
        asset_sync._detected_context("codex", "plugin"),
    )
    inventory = asset_sync.AssetInventory(
        branch="main",
        remote_commit=snapshot.commit,
        repo_url=snapshot.repo_url,
        remote_available=True,
        remote_warning="",
        scanned_local=True,
        generated_at="",
        legacy_write_blocker="",
        rows=[row],
    )
    item = asset_sync._build_batch_upload_item(
        row.resource_key,
        [row],
        None,
        cfg=_config(tmp_path),
        snapshot=snapshot,
        inventory=inventory,
    )

    assert item.disposition == "create" and item.plan is not None
    assert asset_sync._mutate_remote_asset(remote, snapshot.registry, item.plan, row) is True
    stored = load_registry(remote / "registry.yaml").get(row.name, "plugin")
    assert stored is not None and stored.plugin is not None
    assert stored.plugin.installations[0].scope == "managed"
    assert not (remote / "plugins").exists()
    assert "must-not-upload" not in (remote / "registry.yaml").read_text(encoding="utf-8")


def test_same_reference_source_aggregates_multiple_scopes_in_one_upload_plan(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path / "remote", Registry())
    candidates = [
        DiscoveredPlugin(
            id="chrome-user",
            platform="codex",
            plugin_id="chrome",
            track="reference",
            origin_type="marketplace",
            scope="user",
            enabled=True,
            writable=True,
            marketplace="openai-bundled",
        ),
        DiscoveredPlugin(
            id="chrome-managed",
            platform="codex",
            plugin_id="chrome",
            track="reference",
            origin_type="marketplace",
            scope="managed",
            enabled=True,
            writable=False,
            marketplace="openai-bundled",
        ),
    ]
    key = ResourceKey(kind="plugin", name=candidates[0].resource_name)
    rows = [
        asset_sync._plugin_candidate_row(
            candidate,
            key,
            None,
            snapshot,
            asset_sync._detected_context("codex", "plugin"),
        )
        for candidate in candidates
    ]
    inventory = asset_sync.AssetInventory(
        branch="main",
        remote_commit=snapshot.commit,
        repo_url=snapshot.repo_url,
        remote_available=True,
        remote_warning="",
        scanned_local=True,
        generated_at="",
        legacy_write_blocker="",
        rows=rows,
    )

    item = asset_sync._build_batch_upload_item(
        str(key),
        rows,
        None,
        cfg=_config(tmp_path),
        snapshot=snapshot,
        inventory=inventory,
    )

    assert item.disposition == "create" and item.plan is not None
    installations = item.plan.plugin_data["plugin"]["installations"]
    assert {installation["scope"] for installation in installations} == {"user", "managed"}


def test_plugin_candidate_matches_custom_remote_name_by_distribution_identity() -> None:
    entry = RegistryItem(
        name="my-browser-tool",
        kind="plugin",
        source="external",
        plugin=PluginSpec(
            track="reference",
            platform="codex",
            plugin_id="chrome",
            origin=PluginOrigin(type="marketplace", marketplace="openai-bundled"),
            installations=[PluginInstallation(scope="user", enabled=True)],
        ),
    )
    candidate = DiscoveredPlugin(
        id="chrome-user",
        platform="codex",
        plugin_id="chrome",
        track="reference",
        origin_type="marketplace",
        scope="user",
        enabled=True,
        writable=True,
        marketplace="openai-bundled",
    )

    matched = asset_sync._registry_plugin_entry_for_candidate(Registry(items=[entry]), candidate)

    assert matched is entry


def test_first_content_plugin_upload_requires_explicit_ownership(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".config" / "opencode" / "plugins" / "owned.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export default {}\n", encoding="utf-8")
    candidate = DiscoveredPlugin(
        id="owned-instance",
        platform="opencode",
        plugin_id="owned",
        track="content",
        origin_type="local",
        scope="user",
        enabled=True,
        writable=True,
        path=source,
        origin_source="owned.ts",
    )
    snapshot = _snapshot(tmp_path / "remote", Registry())
    cfg = _config(tmp_path)
    context = asset_sync._detected_context("opencode", "plugin")
    row = asset_sync._plugin_candidate_row(
        candidate,
        ResourceKey(kind="plugin", name=candidate.resource_name),
        None,
        snapshot,
        context,
    )
    inventory = asset_sync.AssetInventory(
        branch="main",
        remote_commit=snapshot.commit,
        repo_url=snapshot.repo_url,
        remote_available=True,
        remote_warning="",
        scanned_local=True,
        generated_at="",
        legacy_write_blocker="",
        rows=[row],
    )

    blocked = asset_sync._build_batch_upload_item(
        row.resource_key,
        [row],
        None,
        cfg=cfg,
        snapshot=snapshot,
        inventory=inventory,
    )
    confirmed = asset_sync._build_batch_upload_item(
        row.resource_key,
        [row],
        asset_sync.AssetBatchChoice(
            resource_key=row.resource_key,
            plugin_track="content",
            ownership_confirmed=True,
            plugin_dependencies={"left-pad": "^1.3.0"},
        ),
        cfg=cfg,
        snapshot=snapshot,
        inventory=inventory,
    )

    assert blocked.disposition == "blocked"
    assert "Confirm" in blocked.reason
    assert blocked.reason_ref is not None
    assert blocked.reason_ref.code == "asset.batch.plugin_source_choice_required"
    assert confirmed.disposition == "create"
    assert confirmed.plan is not None
    assert confirmed.plan.plugin_data["plugin"]["dependencies"] == {"left-pad": "^1.3.0"}


def test_content_plugin_upload_commits_planned_spec_and_source(tmp_path: Path) -> None:
    source = tmp_path / ".config" / "opencode" / "plugins" / "owned.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export default {}\n", encoding="utf-8")
    candidate = DiscoveredPlugin(
        id="owned-instance",
        platform="opencode",
        plugin_id="owned",
        track="content",
        origin_type="local",
        scope="user",
        enabled=True,
        writable=True,
        path=source,
        origin_source="owned.ts",
    )
    remote = tmp_path / "remote"
    snapshot = _snapshot(remote, Registry())
    cfg = _config(tmp_path)
    row = asset_sync._plugin_candidate_row(
        candidate,
        ResourceKey(kind="plugin", name=candidate.resource_name),
        None,
        snapshot,
        asset_sync._detected_context("opencode", "plugin"),
    )
    inventory = asset_sync.AssetInventory(
        branch="main",
        remote_commit=snapshot.commit,
        repo_url=snapshot.repo_url,
        remote_available=True,
        remote_warning="",
        scanned_local=True,
        generated_at="",
        legacy_write_blocker="",
        rows=[row],
    )
    item = asset_sync._build_batch_upload_item(
        row.resource_key,
        [row],
        asset_sync.AssetBatchChoice(
            resource_key=row.resource_key,
            plugin_track="content",
            ownership_confirmed=True,
            plugin_dependencies={"left-pad": "^1.3.0"},
        ),
        cfg=cfg,
        snapshot=snapshot,
        inventory=inventory,
    )

    assert item.plan is not None
    assert asset_sync._mutate_remote_asset(remote, snapshot.registry, item.plan, row) is True
    stored = load_registry(remote / "registry.yaml").get(row.name, "plugin")
    assert stored is not None and stored.plugin is not None
    assert stored.plugin.track == "content"
    assert stored.plugin.dependencies == {"left-pad": "^1.3.0"}
    assert (remote / stored.path / "owned.ts").read_text(encoding="utf-8") == "export default {}\n"


def test_opencode_content_download_restores_file_and_only_declared_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = tmp_path / "remote"
    entry = RegistryItem(
        name="opencode-local-owned",
        kind="plugin",
        source="local",
        path="plugins/opencode-local-owned",
        plugin=PluginSpec(
            track="content",
            platform="opencode",
            plugin_id="owned",
            origin=PluginOrigin(type="local", source="owned.ts"),
            installations=[PluginInstallation(scope="user", enabled=True)],
            dependencies={"left-pad": "^1.3.0"},
        ),
    )
    content = remote / entry.path / "owned.ts"
    content.parent.mkdir(parents=True)
    content.write_text("export default {}\n", encoding="utf-8")
    snapshot = _snapshot(remote, Registry(items=[entry]))
    plugins_dir = tmp_path / "opencode" / "plugins"
    package_json = tmp_path / "opencode" / "package.json"
    package_json.parent.mkdir(parents=True)
    package_json.write_text(
        json.dumps({"dependencies": {"keep-me": "1.0.0"}}, indent=2) + "\n",
        encoding="utf-8",
    )
    cfg = _config(tmp_path)
    cfg.platforms.profiles.append(
        PlatformProfile(name="opencode", enabled=True, plugins_dir=str(plugins_dir))
    )
    rows = asset_sync._expected_plugin_rows(
        entry,
        snapshot,
        cfg,
        asset_sync._platform_contexts(cfg, None),
    )
    inventory = asset_sync.AssetInventory(
        branch="main",
        remote_commit=snapshot.commit,
        repo_url=snapshot.repo_url,
        remote_available=True,
        remote_warning="",
        scanned_local=True,
        generated_at="",
        legacy_write_blocker="",
        rows=rows,
    )
    item = asset_sync._build_batch_download_item(
        entry.resource_key,
        "opencode",
        rows,
        None,
        cfg=cfg,
        snapshot=snapshot,
        inventory=inventory,
    )
    assert item.plan is not None and item.disposition == "create"
    monkeypatch.setattr(asset_sync, "_refresh_remote_snapshot", lambda *_args, **_kwargs: snapshot)

    result = asset_sync._apply_local_asset_action(item.plan, cfg)

    assert result.status == "succeeded"
    assert (plugins_dir / "owned.ts").read_text(encoding="utf-8") == "export default {}\n"
    dependencies = json.loads(package_json.read_text(encoding="utf-8"))["dependencies"]
    assert dependencies == {"keep-me": "1.0.0", "left-pad": "^1.3.0"}


def test_missing_reference_download_is_manual_not_silently_skipped(tmp_path: Path) -> None:
    entry = RegistryItem(
        name="opencode-npm-acme-tool",
        kind="plugin",
        source="external",
        plugin=_reference_spec(),
    )
    snapshot = _snapshot(tmp_path / "remote", Registry(items=[entry]))
    rows = asset_sync._expected_plugin_rows(
        entry,
        snapshot,
        _config(tmp_path),
        {},
    )

    item = asset_sync._build_batch_download_item(
        entry.resource_key,
        "opencode",
        rows,
        None,
        cfg=_config(tmp_path),
        snapshot=snapshot,
        inventory=asset_sync.AssetInventory(
            branch="main",
            remote_commit=snapshot.commit,
            repo_url=snapshot.repo_url,
            remote_available=True,
            remote_warning="",
            scanned_local=True,
            generated_at="",
            legacy_write_blocker="",
            rows=rows,
        ),
    )
    result = asset_sync._batch_passive_result(item)

    assert item.disposition == "manual"
    assert result.status == "needs-action"
    assert "opencode" in item.reason


def test_installed_opencode_reference_aligns_enabled_state_transactionally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"plugin": ["@acme/tool@^2.0.0"], "theme": "keep"}, indent=2) + "\n",
        encoding="utf-8",
    )
    entry = RegistryItem(
        name="opencode-npm-acme-tool",
        kind="plugin",
        source="external",
        plugin=_reference_spec(enabled=False),
    )
    snapshot = _snapshot(tmp_path / "remote", Registry(items=[entry]))
    candidate = DiscoveredPlugin(
        id="opencode-installed",
        platform="opencode",
        plugin_id="@acme/tool",
        track="reference",
        origin_type="npm",
        scope="user",
        enabled=True,
        writable=True,
        path=config_path,
        state_path=config_path,
        package="@acme/tool",
        selector="^2.0.0",
    )
    local_row = asset_sync._plugin_candidate_row(
        candidate,
        entry.key(),
        entry,
        snapshot,
        asset_sync._detected_context("opencode", "plugin"),
    )
    rows = [*asset_sync._expected_plugin_rows(entry, snapshot, _config(tmp_path), {}), local_row]
    item = asset_sync._build_plugin_reference_download_item(
        entry.resource_key,
        "opencode",
        rows,
        entry,
    )
    assert item.disposition == "update"
    assert item.plan is not None and item.plan.action == "align-plugin-state"
    monkeypatch.setattr(asset_sync, "_refresh_remote_snapshot", lambda *_args, **_kwargs: snapshot)

    result = asset_sync._apply_local_asset_action(item.plan, _config(tmp_path))

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert result.status == "succeeded"
    assert payload == {"plugin": [], "theme": "keep"}


def test_codex_reference_state_edit_preserves_unrelated_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'model = "gpt-test"\n\n'
        '[plugins."chrome@openai-bundled"]\n'
        'enabled = true # keep this comment\n\n'
        '[features]\n'
        'js_repl = false\n',
        encoding="utf-8",
    )
    spec = PluginSpec(
        track="reference",
        platform="codex",
        plugin_id="chrome",
        origin=PluginOrigin(type="marketplace", marketplace="openai-bundled"),
        installations=[PluginInstallation(scope="user", enabled=False)],
    )

    asset_sync._write_codex_plugin_state(config_path, spec, False)

    text = config_path.read_text(encoding="utf-8")
    assert "enabled = false # keep this comment" in text
    assert 'model = "gpt-test"' in text
    assert "js_repl = false" in text
    assert asset_sync._configured_plugin_state_matches(config_path, spec, False) is True


def test_managed_plugin_delete_instance_is_never_selectable() -> None:
    spec = PluginSpec(
        track="reference",
        platform="codex",
        plugin_id="chrome",
        origin=PluginOrigin(type="marketplace", marketplace="openai"),
        installations=[PluginInstallation(scope="managed", enabled=True)],
    )

    item = asset_sync._plugin_delete_instance(spec, spec.installations[0], None)

    assert item.method == "managed-policy"
    assert item.selectable is False


def test_content_plugin_delete_plan_targets_the_actual_source_file(tmp_path: Path) -> None:
    source = tmp_path / "opencode" / "plugins" / "owned.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export default {}\n", encoding="utf-8")
    spec = PluginSpec(
        track="content",
        platform="opencode",
        plugin_id="owned",
        origin=PluginOrigin(type="local", source="owned.ts"),
        installations=[PluginInstallation(scope="user", enabled=True)],
    )
    row = asset_sync.AssetPlatformRow(
        resource_key="plugin:opencode-local-owned",
        kind="plugin",
        name="opencode-local-owned",
        platform="opencode",
        local_instance_id="expected-owned",
        local_locator="plugin-expected",
        install_name="owned",
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

    item = asset_sync._plugin_delete_instance(spec, spec.installations[0], row)
    result = asset_sync._apply_plugin_delete_instance(
        "plugin:opencode-local-owned",
        item,
        _config(tmp_path),
    )

    assert item.method == "delete-content"
    assert result.status == "succeeded"
    assert not source.exists()


def test_manual_plugin_delete_keeps_remote_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = asset_sync.PluginDeleteInstancePlan(
        id="codex-user",
        platform="codex",
        scope="user",
        project_id="",
        enabled=True,
        writable=True,
        selectable=True,
        method="manual",
        detail="Uninstall manually and rescan.",
        installation={"scope": "user", "enabled": True, "project": None},
        plugin_id="chrome",
        source_id="chrome@openai",
    )
    plan = asset_sync.PluginDeletePlan(
        resource_key="plugin:codex-marketplace-chrome-openai",
        remote_commit="abc123",
        selected_instance_ids=[instance.id],
        instances=[instance],
        plan_hash="plan-hash",
    )
    monkeypatch.setattr(asset_sync, "build_plugin_delete_plan", lambda *_args, **_kwargs: plan)
    called = False

    def fail_remote(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("remote deletion must not run")

    monkeypatch.setattr(asset_sync, "_remove_plugin_remote_installations", fail_remote)

    result = asset_sync.apply_plugin_delete_plan(
        plan.resource_key,
        selected_instance_ids=[instance.id],
        expected_plan_hash=plan.plan_hash,
        config=_config(tmp_path),
    )

    assert result.status == "needs-action"
    assert result.remote_deleted is False
    assert called is False
