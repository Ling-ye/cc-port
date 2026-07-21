from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lpm.core.config import Config, GitConfig, InstallConfig, ResourcesConfig
from lpm.core.models import Registry, RegistryItem
from lpm.core.ownership import managed_resource_key
from lpm.core.platforms import PlatformProfile, PlatformsConfig
from lpm.core.registry import load_registry, save_registry
from lpm.infrastructure import git_ops
from lpm.services import asset_sync
from lpm.services.asset_sync import RemoteSnapshot
from lpm.services.env_manager import DiscoveredTool, EnvDiscoveryResult
from lpm.services.resource_commit import ResourceCommitBlocked
from lpm.services.resource_discovery import DiscoveredResource

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


def _empty_discovery() -> EnvDiscoveryResult:
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
        "user.name=LPM Test",
        "-c",
        "user.email=lpm@example.test",
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
        "user.name=LPM Test",
        "-c",
        "user.email=lpm@example.test",
        "commit",
        "-m",
        "concurrent",
    )
    _run_git(clone, "push", "origin", "main")


def test_inventory_uses_platform_rows_and_blocks_rule_prompt_target_collision(
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
    assert {row.status for row in inventory.rows} == {"target-conflict"}
    assert all(row.available_actions == ["set-platform-install-name"] for row in inventory.rows)


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
        lambda: EnvDiscoveryResult(
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
        resources=ResourcesConfig(branch="main"),
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(name="cursor", enabled=True, skills_dir=str(cursor.parent)),
                PlatformProfile(name="codex", enabled=True, skills_dir=str(codex.parent)),
            ]
        ),
    )

    def discovery() -> EnvDiscoveryResult:
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
    assert batch.blocked_count == 1
    assert "select a source instance" in batch.items[0].reason

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
    assert by_key["prompt:demo"].target_path.name == "demo-prompt"


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
    monkeypatch.setattr(asset_sync, "discover_environment", lambda: discovery)

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
    monkeypatch.setattr(asset_sync, "discover_environment", lambda: discovery)
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
