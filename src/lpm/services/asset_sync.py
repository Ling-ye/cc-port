"""Asset-level inventory, planning, and two-way synchronization."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import frontmatter

from ..core.config import Config, default_state_dir, load_config, resource_repo_auth_token
from ..core.models import ITEM_NAME_RE, ItemKind, Registry, RegistryItem, ResourceKey
from ..core.ownership import (
    is_lpm_managed,
    is_lpm_managed_mcp,
    managed_mcp_resource_key,
    managed_resource_key,
    mark_lpm_managed_mcp,
    mcp_ownership_path,
    write_managed_marker,
)
from ..core.platforms import PlatformProfile, build_platform
from ..core.registry import DEFAULT_REGISTRY_FILENAME, load_registry, save_registry
from ..core.secrets import sanitize_mcp_config_for_storage
from ..core.tool_adapters import tool_adapter_by_id
from ..core.validator import validate_item
from ..infrastructure import git_ops
from .env_manager import EnvDiscoveryResult, discover_environment
from .install_planner import copy_resource_tree
from .local_transaction import (
    ChangeTarget,
    LocalChangeTransaction,
    resource_hash_path,
)
from .mcp_installer import inject_mcp_server, list_mcp_servers
from .resource_commit import commit_resource_changes_unlocked
from .resource_repo import ensure_structure, resource_root
from .resource_repo_lock import resource_repo_write_lock
from .resource_sync import load_resource_sync_plan

AssetStatus = Literal[
    "remote-only",
    "local-only",
    "same",
    "content-different",
    "metadata-only",
    "read-only-reference",
    "target-conflict",
    "uncomparable",
]
AssetAction = Literal[
    "download",
    "upload",
    "copy-to-local",
    "copy-to-remote",
    "set-platform-install-name",
]

ASSET_STATE_DIR = "assets"
ASSET_PLAN_DIR = "asset-plans"
ASSET_PLAN_SCHEMA_VERSION = 1
REMOTE_CACHE_DIR = "remotes"
REMOTE_SNAPSHOT_DIR = "snapshots"
REMOTE_WRITE_ACTIONS = {"upload", "copy-to-remote", "set-platform-install-name"}
LOCAL_WRITE_ACTIONS = {"download", "copy-to-local"}
RESOURCE_PARENT_BY_KIND: dict[ItemKind, str] = {
    "skill": "skills",
    "mcp": "mcp",
    "rule": "rules",
    "prompt": "prompts",
    "plugin": "plugins",
}
DERIVED_METADATA_FIELDS = ("description", "version", "author", "license")


@dataclass
class RemoteSnapshot:
    root: Path
    registry: Registry
    commit: str
    branch: str
    repo_url: str
    available: bool = True
    warning: str = ""


@dataclass
class AssetPlatformRow:
    resource_key: str
    kind: ItemKind
    name: str
    platform: str
    local_instance_id: str
    local_locator: str
    install_name: str
    configured: bool
    enabled: bool
    detected: bool
    supported: bool
    remote_exists: bool
    local_exists: bool
    remote_writable: bool
    read_only_reference: bool
    remote_path: Path | None
    local_path: Path | None
    target_path: Path | None
    ownership: str
    status: AssetStatus
    remote_commit: str
    reference_commit: str = ""
    remote_content_fingerprint: str = ""
    remote_asset_fingerprint: str = ""
    local_fingerprint: str = ""
    metadata_differences: list[str] = field(default_factory=list)
    diff_summary: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    available_actions: list[str] = field(default_factory=list)
    entry: RegistryItem | None = None


@dataclass
class AssetInventory:
    branch: str
    remote_commit: str
    repo_url: str
    remote_available: bool
    remote_warning: str
    scanned_local: bool
    generated_at: str
    legacy_write_blocker: str
    rows: list[AssetPlatformRow]


@dataclass
class AssetActionPlan:
    operation_id: str
    action: AssetAction
    resource_key: str
    target_resource_key: str
    kind: ItemKind
    name: str
    platform: str
    local_instance_id: str
    local_locator: str
    remote_commit: str
    remote_target_exists: bool
    remote_target_fingerprint: str
    local_source_fingerprint: str
    target_path: Path | None
    target_exists: bool
    target_fingerprint: str
    target_managed: bool
    overwrite_unmanaged: bool = False
    new_name: str = ""
    new_install_name: str = ""
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    blocked: bool = False
    created_at: str = ""
    schema_version: int = ASSET_PLAN_SCHEMA_VERSION


@dataclass
class AssetActionResult:
    operation_id: str
    action: AssetAction
    status: str
    resource_key: str
    target_resource_key: str
    platform: str
    message: str
    remote_commit: str = ""
    local_path: Path | None = None
    replayed_on_latest: bool = False
    push_retry_count: int = 0
    warnings: list[str] = field(default_factory=list)
    operation_status: str = ""


@dataclass
class _PlatformContext:
    profile: PlatformProfile
    configured: bool
    detected: bool
    supported_kinds: set[ItemKind]


class AssetSyncError(RuntimeError):
    """Base error for asset-level synchronization."""


class AssetPlanInvalid(AssetSyncError):
    """Raised when a persisted plan cannot be trusted."""


class _StaleAssetTarget(AssetSyncError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def build_asset_inventory(
    *,
    config: Config | None = None,
    scan_local: bool = False,
    refresh_remote: bool = True,
    remote_snapshot: RemoteSnapshot | None = None,
) -> AssetInventory:
    """Build one row per asset/platform/local instance without writing user targets."""
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    _cleanup_expired_asset_plans(cfg)
    snapshot = remote_snapshot or _refresh_remote_snapshot(cfg, refresh=refresh_remote)
    discovery = discover_environment() if scan_local else None
    contexts = _platform_contexts(cfg, discovery)
    reference_commits: dict[tuple[str, str], str] = {}
    rows: list[AssetPlatformRow] = []
    seen_local_paths: set[tuple[str, str, str, str]] = set()

    for entry in snapshot.registry.items:
        for platform_name, context in contexts.items():
            row = _expected_row(
                entry,
                platform_name,
                context,
                snapshot,
                cfg,
                reference_commits=reference_commits,
            )
            if row is None:
                continue
            rows.append(row)
            if row.local_exists and row.local_path is not None:
                seen_local_paths.add(
                    _local_identity(
                        row.platform,
                        row.kind,
                        row.local_path,
                        row.install_name if row.kind == "mcp" else "",
                    )
                )

    if discovery is not None:
        rows.extend(
            _discovered_rows(
                discovery,
                snapshot,
                cfg,
                contexts,
                seen_local_paths=seen_local_paths,
                reference_commits=reference_commits,
            )
        )

    _mark_target_collisions(rows)
    _mark_duplicate_remote_content(rows)
    for row in rows:
        _finalize_row_actions(row, snapshot)

    rows.sort(
        key=lambda item: (
            item.kind,
            item.name,
            item.platform,
            item.local_path.as_posix().lower() if item.local_path else "",
        )
    )
    blocker = _legacy_write_blocker(cfg, fetch=False)
    return AssetInventory(
        branch=snapshot.branch,
        remote_commit=snapshot.commit,
        repo_url=snapshot.repo_url,
        remote_available=snapshot.available,
        remote_warning=snapshot.warning,
        scanned_local=scan_local,
        generated_at=_utc_now(),
        legacy_write_blocker=blocker,
        rows=rows,
    )


def build_asset_action_plan(
    action: str,
    *,
    kind: ItemKind,
    name: str,
    platform: str,
    local_instance_id: str = "",
    new_name: str = "",
    new_install_name: str = "",
    overwrite_unmanaged: bool = False,
    config: Config | None = None,
) -> AssetActionPlan:
    """Persist a revalidatable plan for exactly one asset/platform row."""
    if action not in {
        "download",
        "upload",
        "copy-to-local",
        "copy-to-remote",
        "set-platform-install-name",
    }:
        raise ValueError(f"Unsupported asset action: {action}")
    cfg = config or load_config()
    snapshot = _refresh_remote_snapshot(cfg, refresh=True)
    inventory = build_asset_inventory(
        config=cfg,
        scan_local=True,
        refresh_remote=False,
        remote_snapshot=snapshot,
    )
    key = ResourceKey(kind=kind, name=name)
    candidates = [
        row
        for row in inventory.rows
        if row.resource_key == str(key) and row.platform == platform
    ]
    if local_instance_id:
        candidates = [
            row for row in candidates if row.local_instance_id == local_instance_id
        ]
    row = _select_plan_row(candidates, action=action, local_instance_id=local_instance_id)
    blockers: list[str] = []
    warnings = list(row.warnings)
    target_key = row.resource_key
    target_path = row.target_path
    target_exists = row.local_exists
    target_fingerprint = row.local_fingerprint
    target_managed = row.ownership == "managed"
    remote_target_exists = row.remote_exists
    remote_target_fingerprint = row.remote_asset_fingerprint

    normalized_new_name = new_name.strip()
    normalized_install_name = new_install_name.strip()
    if action in {"copy-to-local", "copy-to-remote"}:
        if not normalized_new_name:
            blockers.append("A new resource name is required.")
        elif not ITEM_NAME_RE.match(normalized_new_name):
            blockers.append(
                "The new resource name must use lowercase letters, digits, and hyphens."
            )
        else:
            target_key = str(ResourceKey(kind=kind, name=normalized_new_name))

    if action == "download":
        blockers.extend(_download_plan_blockers(row, overwrite_unmanaged))
    elif action == "upload":
        blockers.extend(_upload_plan_blockers(row))
    elif action == "copy-to-local":
        blockers.extend(_copy_to_local_blockers(row, snapshot.registry, normalized_new_name))
        target_path, target_exists, target_fingerprint, target_managed = _copy_local_target_state(
            cfg,
            row,
            normalized_new_name,
        )
        if target_exists:
            blockers.append("The new local name already resolves to an existing target.")
        if normalized_new_name and any(
            item.local_exists
            and item.resource_key == target_key
            and item.local_instance_id != row.local_instance_id
            for item in inventory.rows
        ):
            blockers.append("The new local name already exists as another local instance.")
    elif action == "copy-to-remote":
        blockers.extend(_copy_to_remote_blockers(row, snapshot.registry, normalized_new_name))
        target_entry = (
            snapshot.registry.get(normalized_new_name, kind)
            if normalized_new_name
            else None
        )
        remote_target_exists = target_entry is not None
        remote_target_fingerprint = (
            _remote_asset_fingerprint(snapshot.root, target_entry)
            if target_entry is not None
            else ""
        )
    else:
        blockers.extend(
            _install_alias_plan_blockers(
                row,
                snapshot.registry,
                cfg,
                normalized_install_name,
            )
        )

    if action in REMOTE_WRITE_ACTIONS:
        if not snapshot.available:
            blockers.append(
                snapshot.warning
                or "The configured remote branch is unavailable; remote writes are blocked."
            )
        legacy_blocker = _legacy_write_blocker(cfg, fetch=True)
        if legacy_blocker:
            blockers.append(legacy_blocker)
    if (
        action in {"upload", "copy-to-remote"}
        and not remote_target_exists
        and row.local_fingerprint
    ):
        duplicates = _remote_duplicate_keys(
            snapshot,
            kind,
            row.local_fingerprint,
            exclude_key=target_key,
        )
        if duplicates:
            warnings.append(
                "Identical content already exists under: " + ", ".join(duplicates)
            )

    plan = AssetActionPlan(
        operation_id=uuid.uuid4().hex,
        action=action,  # type: ignore[arg-type]
        resource_key=row.resource_key,
        target_resource_key=target_key,
        kind=kind,
        name=name,
        platform=platform,
        local_instance_id=row.local_instance_id,
        local_locator=row.local_locator,
        remote_commit=snapshot.commit,
        remote_target_exists=remote_target_exists,
        remote_target_fingerprint=remote_target_fingerprint,
        local_source_fingerprint=row.local_fingerprint,
        target_path=target_path,
        target_exists=target_exists,
        target_fingerprint=target_fingerprint,
        target_managed=target_managed,
        overwrite_unmanaged=overwrite_unmanaged,
        new_name=normalized_new_name,
        new_install_name=normalized_install_name,
        warnings=warnings,
        blockers=_unique_strings(blockers),
        blocked=bool(blockers),
        created_at=_utc_now(),
    )
    _save_asset_plan(plan)
    return plan


def apply_asset_action_plan(
    operation_id: str,
    *,
    config: Config | None = None,
) -> AssetActionResult:
    """Revalidate and apply one persisted asset action plan."""
    existing = _load_asset_result(operation_id)
    if existing is not None:
        return existing
    plan = load_asset_action_plan(operation_id)
    if plan.blocked:
        result = AssetActionResult(
            operation_id=plan.operation_id,
            action=plan.action,
            status="blocked",
            resource_key=plan.resource_key,
            target_resource_key=plan.target_resource_key,
            platform=plan.platform,
            message="; ".join(plan.blockers) or "The asset action plan is blocked.",
            warnings=plan.warnings,
        )
        _save_asset_result(result)
        return result

    cfg = config or load_config()
    try:
        if plan.action in LOCAL_WRITE_ACTIONS:
            result = _apply_local_asset_action(plan, cfg)
        else:
            result = _apply_remote_asset_action(plan, cfg)
    except _StaleAssetTarget as exc:
        result = AssetActionResult(
            operation_id=plan.operation_id,
            action=plan.action,
            status=exc.code,
            resource_key=plan.resource_key,
            target_resource_key=plan.target_resource_key,
            platform=plan.platform,
            message=str(exc),
            warnings=plan.warnings,
        )
    _save_asset_result(result)
    return result


def load_asset_action_plan(operation_id: str) -> AssetActionPlan:
    path = _asset_plan_dir(operation_id) / "plan.json"
    if not path.is_file():
        raise FileNotFoundError(f"Unknown asset action plan: {operation_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if int(data.get("schema_version", 0)) != ASSET_PLAN_SCHEMA_VERSION:
        raise AssetPlanInvalid("Unsupported asset action plan schema.")
    if str(data.get("operation_id") or "") != operation_id:
        raise AssetPlanInvalid("Asset action plan id does not match its directory.")
    action = str(data.get("action") or "")
    if action not in {
        "download",
        "upload",
        "copy-to-local",
        "copy-to-remote",
        "set-platform-install-name",
    }:
        raise AssetPlanInvalid(f"Unsupported persisted asset action: {action}")
    key = ResourceKey.parse(str(data.get("resource_key") or ""))
    target_key = ResourceKey.parse(str(data.get("target_resource_key") or ""))
    if key.kind != str(data.get("kind") or "") or key.name != str(data.get("name") or ""):
        raise AssetPlanInvalid("Asset action plan resource fields are inconsistent.")
    target_path_value = str(data.get("target_path") or "")
    return AssetActionPlan(
        operation_id=operation_id,
        action=action,  # type: ignore[arg-type]
        resource_key=str(key),
        target_resource_key=str(target_key),
        kind=key.kind,
        name=key.name,
        platform=str(data.get("platform") or ""),
        local_instance_id=str(data.get("local_instance_id") or ""),
        local_locator=str(data.get("local_locator") or ""),
        remote_commit=str(data.get("remote_commit") or ""),
        remote_target_exists=bool(data.get("remote_target_exists", False)),
        remote_target_fingerprint=str(data.get("remote_target_fingerprint") or ""),
        local_source_fingerprint=str(data.get("local_source_fingerprint") or ""),
        target_path=Path(target_path_value) if target_path_value else None,
        target_exists=bool(data.get("target_exists", False)),
        target_fingerprint=str(data.get("target_fingerprint") or ""),
        target_managed=bool(data.get("target_managed", False)),
        overwrite_unmanaged=bool(data.get("overwrite_unmanaged", False)),
        new_name=str(data.get("new_name") or ""),
        new_install_name=str(data.get("new_install_name") or ""),
        warnings=[str(item) for item in data.get("warnings", [])],
        blockers=[str(item) for item in data.get("blockers", [])],
        blocked=bool(data.get("blocked", False)),
        created_at=str(data.get("created_at") or ""),
        schema_version=ASSET_PLAN_SCHEMA_VERSION,
    )


def _refresh_remote_snapshot(cfg: Config, *, refresh: bool) -> RemoteSnapshot:
    branch = cfg.resources.branch or "main"
    repo_url = _configured_remote_url(cfg)
    if not repo_url:
        return _local_compatibility_snapshot(
            cfg,
            warning="No remote resource repository URL is configured; remote writes are blocked.",
        )

    cache_key = hashlib.sha256(f"{repo_url}\0{branch}".encode()).hexdigest()[:24]
    state_root = default_state_dir() / ASSET_STATE_DIR
    transport = state_root / REMOTE_CACHE_DIR / cache_key
    try:
        with resource_repo_write_lock(
            transport,
            timeout_seconds=cfg.state.lock_timeout_seconds,
        ):
            if not git_ops.is_repo(transport):
                _remove_internal_path(transport, state_root)
                try:
                    git_ops.clone(
                        repo_url,
                        transport,
                        ref=branch,
                        token=resource_repo_auth_token(cfg),
                    )
                except git_ops.GitError:
                    _remove_internal_path(transport, state_root)
                    git_ops.clone(
                        repo_url,
                        transport,
                        token=resource_repo_auth_token(cfg),
                    )
            else:
                git_ops.set_remote(transport, "origin", repo_url)

            if refresh:
                git_ops.fetch(
                    transport,
                    ref=branch,
                    token=resource_repo_auth_token(cfg),
                )
            remote_commit = git_ops.rev_parse(transport, f"origin/{branch}")
            if remote_commit is None:
                remote_commit = git_ops.head_commit(transport)
            if remote_commit:
                git_ops.checkout_branch_at(transport, branch, remote_commit)
            else:
                git_ops.checkout_local_branch(transport, branch)

            snapshot_root = (
                state_root
                / REMOTE_SNAPSHOT_DIR
                / cache_key
                / (remote_commit or "unborn")
            )
            if not snapshot_root.exists():
                snapshot_root.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(
                    transport,
                    snapshot_root,
                    symlinks=True,
                    ignore=lambda _directory, names: {".git"} & set(names),
                )
            registry_path = snapshot_root / DEFAULT_REGISTRY_FILENAME
            registry = load_registry(registry_path)
            return RemoteSnapshot(
                root=snapshot_root,
                registry=registry,
                commit=remote_commit or "",
                branch=branch,
                repo_url=repo_url,
            )
    except Exception as exc:
        cached = _latest_cached_snapshot(state_root / REMOTE_SNAPSHOT_DIR / cache_key)
        if cached is not None:
            return RemoteSnapshot(
                root=cached,
                registry=load_registry(cached / DEFAULT_REGISTRY_FILENAME),
                commit="" if cached.name == "unborn" else cached.name,
                branch=branch,
                repo_url=repo_url,
                available=False,
                warning=f"Remote refresh failed; showing the latest cached snapshot: {exc}",
            )
        return _local_compatibility_snapshot(
            cfg,
            repo_url=repo_url,
            warning=f"Remote refresh failed; showing the legacy local snapshot read-only: {exc}",
        )


def _local_compatibility_snapshot(
    cfg: Config,
    *,
    repo_url: str = "",
    warning: str,
) -> RemoteSnapshot:
    root = resource_root(cfg)
    registry = load_registry(root / DEFAULT_REGISTRY_FILENAME)
    commit = git_ops.head_commit(root) if git_ops.is_repo(root) else ""
    return RemoteSnapshot(
        root=root,
        registry=registry,
        commit=commit or "",
        branch=cfg.resources.branch or "main",
        repo_url=repo_url,
        available=False,
        warning=warning,
    )


def _configured_remote_url(cfg: Config) -> str:
    if cfg.resources.repo_url.strip():
        return cfg.resources.repo_url.strip()
    root = resource_root(cfg)
    if git_ops.is_repo(root):
        return git_ops.current_remote_url(root) or ""
    return ""


def _latest_cached_snapshot(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    candidates = [path for path in root.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _platform_contexts(
    cfg: Config,
    discovery: EnvDiscoveryResult | None,
) -> dict[str, _PlatformContext]:
    contexts: dict[str, _PlatformContext] = {}
    for profile in cfg.platforms.profiles:
        adapter = tool_adapter_by_id(profile.name)
        contexts[profile.name] = _PlatformContext(
            profile=profile,
            configured=True,
            detected=False,
            supported_kinds=set(adapter.supports_kinds) if adapter else set(RESOURCE_PARENT_BY_KIND),
        )
    if discovery is None:
        return contexts
    for tool in discovery.tools:
        if not tool.detected:
            continue
        existing = contexts.get(tool.id)
        if existing is not None:
            existing.detected = True
            existing.supported_kinds.update(tool.supports_kinds)
            continue
        profile = build_platform(tool.id)
        profile.enabled = False
        contexts[tool.id] = _PlatformContext(
            profile=profile,
            configured=False,
            detected=True,
            supported_kinds=set(tool.supports_kinds),
        )
    return contexts


def _expected_row(
    entry: RegistryItem,
    platform_name: str,
    context: _PlatformContext,
    snapshot: RemoteSnapshot,
    cfg: Config,
    *,
    reference_commits: dict[tuple[str, str], str],
) -> AssetPlatformRow | None:
    install_name = entry.install_target_name(platform_name)
    target = context.profile.resolve_install_path(entry.kind, install_name)
    if target is None:
        return None
    supported = (
        entry.supports_platform(platform_name)
        and (not context.supported_kinds or entry.kind in context.supported_kinds)
    )
    target = target.expanduser().absolute()
    remote_path = _remote_content_path(snapshot.root, entry)
    remote_writable = _is_private_repo_asset(entry)
    read_only = not remote_writable
    remote_content = _entry_content_fingerprint(entry, remote_path)
    remote_asset = _remote_asset_fingerprint(snapshot.root, entry)
    reference_commit = ""
    if read_only and entry.repo:
        reference_commit = _reference_commit(entry, cfg, reference_commits)

    local_exists, local_fingerprint, ownership = _expected_local_state(
        entry,
        context.profile,
        target,
        install_name,
    )
    metadata_differences: list[str] = []
    if local_exists:
        local_metadata = _derive_metadata(
            entry.kind,
            target,
            mcp_config=_read_mcp_server(target, install_name)
            if entry.kind == "mcp"
            else None,
        )
        metadata_differences = _metadata_differences(entry, local_metadata)
    status = _asset_status(
        remote_exists=True,
        local_exists=local_exists,
        remote_fingerprint=remote_content,
        local_fingerprint=local_fingerprint,
        metadata_differences=metadata_differences,
        read_only=read_only,
    )
    local_id = _instance_id(
        "expected",
        entry.resource_key,
        platform_name,
        target,
        install_name if entry.kind == "mcp" else "",
    )
    blockers: list[str] = []
    if not context.configured:
        blockers.append("Platform is detected but not configured; configure it before downloading.")
    elif not context.profile.enabled:
        blockers.append("Platform is configured but disabled; enable it before downloading.")
    if not supported:
        blockers.append("The resource is not enabled for this platform.")
    if read_only:
        blockers.append("This registry item is a read-only reference in asset sync.")
    if not snapshot.available:
        blockers.append(snapshot.warning or "Remote snapshot is not current.")
    return AssetPlatformRow(
        resource_key=entry.resource_key,
        kind=entry.kind,
        name=entry.name,
        platform=platform_name,
        local_instance_id=local_id,
        local_locator="expected",
        install_name=install_name,
        configured=context.configured,
        enabled=context.profile.enabled if context.configured else False,
        detected=context.detected,
        supported=supported,
        remote_exists=True,
        local_exists=local_exists,
        remote_writable=remote_writable,
        read_only_reference=read_only,
        remote_path=remote_path,
        local_path=target if local_exists else None,
        target_path=target,
        ownership=ownership,
        status=status,
        remote_commit=snapshot.commit,
        reference_commit=reference_commit,
        remote_content_fingerprint=remote_content,
        remote_asset_fingerprint=remote_asset,
        local_fingerprint=local_fingerprint,
        metadata_differences=metadata_differences,
        diff_summary=_diff_summary(status, metadata_differences),
        blockers=_unique_strings(blockers),
        entry=entry,
    )


def _discovered_rows(
    discovery: EnvDiscoveryResult,
    snapshot: RemoteSnapshot,
    cfg: Config,
    contexts: dict[str, _PlatformContext],
    *,
    seen_local_paths: set[tuple[str, str, str, str]],
    reference_commits: dict[tuple[str, str], str],
) -> list[AssetPlatformRow]:
    rows: list[AssetPlatformRow] = []
    for candidate in discovery.resources:
        identity = _local_identity(candidate.tool, candidate.kind, candidate.path, "")
        if identity in seen_local_paths:
            continue
        context = contexts.get(candidate.tool) or _detected_context(candidate.tool, candidate.kind)
        marker_key = managed_resource_key(candidate.path) if candidate.path.is_dir() else ""
        key = _safe_resource_key(marker_key, candidate.kind, candidate.name_hint)
        entry = snapshot.registry.get(key.name, key.kind)
        row = _local_candidate_row(
            snapshot,
            cfg,
            context,
            platform=candidate.tool,
            key=key,
            local_instance_id=candidate.id,
            locator="discovered-resource",
            local_path=candidate.path,
            install_name=candidate.path.name,
            local_fingerprint=resource_hash_path(candidate.path),
            ownership="managed" if marker_key else "unmanaged",
            entry=entry,
            reference_commits=reference_commits,
        )
        rows.append(row)
        seen_local_paths.add(identity)

    for server in discovery.mcp_servers:
        identity = _local_identity(server.tool, "mcp", server.config_path, server.name)
        if identity in seen_local_paths:
            continue
        context = contexts.get(server.tool) or _detected_context(server.tool, "mcp")
        marker_key = managed_mcp_resource_key(server.config_path, server.name)
        key = _safe_resource_key(marker_key, "mcp", server.name)
        entry = snapshot.registry.get(key.name, "mcp")
        sanitized = sanitize_mcp_config_for_storage(server.config) or {}
        row = _local_candidate_row(
            snapshot,
            cfg,
            context,
            platform=server.tool,
            key=key,
            local_instance_id=server.id,
            locator="discovered-mcp",
            local_path=server.config_path,
            install_name=server.name,
            local_fingerprint=_json_fingerprint(sanitized),
            ownership="managed" if marker_key else "unmanaged",
            entry=entry,
            reference_commits=reference_commits,
        )
        rows.append(row)
        seen_local_paths.add(identity)
    return rows


def _detected_context(platform: str, kind: ItemKind) -> _PlatformContext:
    profile = build_platform(platform)
    profile.enabled = False
    return _PlatformContext(
        profile=profile,
        configured=False,
        detected=True,
        supported_kinds={kind},
    )


def _local_candidate_row(
    snapshot: RemoteSnapshot,
    cfg: Config,
    context: _PlatformContext,
    *,
    platform: str,
    key: ResourceKey,
    local_instance_id: str,
    locator: str,
    local_path: Path,
    install_name: str,
    local_fingerprint: str,
    ownership: str,
    entry: RegistryItem | None,
    reference_commits: dict[tuple[str, str], str],
) -> AssetPlatformRow:
    remote_exists = entry is not None
    remote_path = _remote_content_path(snapshot.root, entry) if entry else None
    remote_content = _entry_content_fingerprint(entry, remote_path) if entry else ""
    remote_asset = _remote_asset_fingerprint(snapshot.root, entry) if entry else ""
    read_only = bool(entry and not _is_private_repo_asset(entry))
    reference_commit = (
        _reference_commit(entry, cfg, reference_commits)
        if entry and read_only and entry.repo
        else ""
    )
    local_metadata = _derive_metadata(
        key.kind,
        local_path,
        mcp_config=_read_mcp_server(local_path, install_name)
        if key.kind == "mcp"
        else None,
    )
    metadata_differences = (
        _metadata_differences(entry, local_metadata) if entry else []
    )
    status = _asset_status(
        remote_exists=remote_exists,
        local_exists=True,
        remote_fingerprint=remote_content,
        local_fingerprint=local_fingerprint,
        metadata_differences=metadata_differences,
        read_only=read_only,
    )
    supported = not context.supported_kinds or key.kind in context.supported_kinds
    blockers: list[str] = []
    if not context.configured:
        blockers.append("Platform is detected but not configured; configure it before downloading.")
    if not supported:
        blockers.append("The detected platform does not declare support for this resource kind.")
    if read_only:
        blockers.append("This registry item is a read-only reference in asset sync.")
    return AssetPlatformRow(
        resource_key=str(key),
        kind=key.kind,
        name=key.name,
        platform=platform,
        local_instance_id=local_instance_id,
        local_locator=locator,
        install_name=install_name,
        configured=context.configured,
        enabled=context.profile.enabled if context.configured else False,
        detected=True,
        supported=supported,
        remote_exists=remote_exists,
        local_exists=True,
        remote_writable=bool(entry and _is_private_repo_asset(entry)),
        read_only_reference=read_only,
        remote_path=remote_path,
        local_path=local_path,
        target_path=local_path,
        ownership=ownership,
        status=status,
        remote_commit=snapshot.commit,
        reference_commit=reference_commit,
        remote_content_fingerprint=remote_content,
        remote_asset_fingerprint=remote_asset,
        local_fingerprint=local_fingerprint,
        metadata_differences=metadata_differences,
        diff_summary=_diff_summary(status, metadata_differences),
        blockers=_unique_strings(blockers),
        warnings=list(getattr(entry, "warnings", [])) if entry else [],
        entry=entry,
    )


def _safe_resource_key(marker_key: str, kind: ItemKind, name: str) -> ResourceKey:
    if marker_key:
        try:
            return ResourceKey.parse(marker_key)
        except ValueError:
            pass
    return ResourceKey(kind=kind, name=name)


def _expected_local_state(
    entry: RegistryItem,
    platform: PlatformProfile,
    target: Path,
    install_name: str,
) -> tuple[bool, str, str]:
    if entry.kind == "mcp":
        config = _read_mcp_server(target, install_name)
        exists = config is not None
        fingerprint = (
            _json_fingerprint(sanitize_mcp_config_for_storage(config) or {})
            if exists
            else ""
        )
        managed = (
            is_lpm_managed_mcp(
                target,
                install_name,
                resource_key=entry.resource_key,
            )
            if exists
            else False
        )
        return exists, fingerprint, "managed" if managed else "unmanaged" if exists else "missing"
    exists = target.exists() and not target.is_symlink()
    fingerprint = resource_hash_path(target) if exists else ""
    managed = (
        is_lpm_managed(target, resource_key=entry.resource_key)
        if exists and target.is_dir()
        else False
    )
    return exists, fingerprint, "managed" if managed else "unmanaged" if exists else "missing"


def _entry_content_fingerprint(entry: RegistryItem | None, path: Path | None) -> str:
    if entry is None:
        return ""
    if entry.kind == "mcp" and entry.mcp_config is not None:
        return _json_fingerprint(
            sanitize_mcp_config_for_storage(entry.mcp_config) or {}
        )
    if path is None or path.is_symlink() or not path.exists():
        if entry.repo and not _is_private_repo_asset(entry):
            return _json_fingerprint(
                {
                    "repo": entry.repo,
                    "ref": entry.ref,
                    "subdir": entry.subdir,
                }
            )
        return ""
    return resource_hash_path(path)


def _remote_asset_fingerprint(root: Path, entry: RegistryItem | None) -> str:
    if entry is None:
        return ""
    payload = entry.model_dump(
        mode="json",
        exclude={"last_checked", "reachable"},
    )
    payload["content_fingerprint"] = _entry_content_fingerprint(
        entry,
        _remote_content_path(root, entry),
    )
    return _json_fingerprint(payload)


def _remote_content_path(root: Path, entry: RegistryItem | None) -> Path | None:
    if entry is None or not entry.path:
        return None
    target = (root / entry.path).absolute()
    root_abs = root.absolute()
    if target != root_abs and root_abs not in target.parents:
        return None
    return target


def _asset_status(
    *,
    remote_exists: bool,
    local_exists: bool,
    remote_fingerprint: str,
    local_fingerprint: str,
    metadata_differences: list[str],
    read_only: bool,
) -> AssetStatus:
    if read_only:
        return "read-only-reference"
    if remote_exists and not local_exists:
        return "remote-only"
    if local_exists and not remote_exists:
        return "local-only"
    if not remote_fingerprint or not local_fingerprint:
        return "uncomparable"
    if remote_fingerprint != local_fingerprint:
        return "content-different"
    if metadata_differences:
        return "metadata-only"
    return "same"


def _diff_summary(status: AssetStatus, metadata: list[str]) -> list[str]:
    if status == "remote-only":
        return ["Remote content is available; no local instance exists."]
    if status == "local-only":
        return ["Local content is not present in the remote registry."]
    if status == "same":
        return ["Content fingerprints match."]
    if status == "content-different":
        return ["Local and remote content fingerprints differ."]
    if status == "metadata-only":
        return [f"Derived metadata differs: {', '.join(metadata)}."]
    if status == "read-only-reference":
        return ["The item is tracked by an external or pathless repository reference."]
    if status == "target-conflict":
        return ["Multiple resources resolve to the same platform target."]
    return ["The content cannot be compared safely."]


def _mark_target_collisions(rows: list[AssetPlatformRow]) -> None:
    groups: dict[tuple[str, str, str], list[AssetPlatformRow]] = {}
    for row in rows:
        if (
            row.local_locator != "expected"
            or row.target_path is None
            or not row.remote_exists
        ):
            continue
        target_key = os.path.normcase(str(row.target_path.absolute()))
        logical_name = row.install_name if row.kind == "mcp" else ""
        groups.setdefault((row.platform, target_key, logical_name), []).append(row)
    for group in groups.values():
        resource_keys = {row.resource_key for row in group}
        if len(resource_keys) <= 1:
            continue
        detail = "Target is shared by: " + ", ".join(sorted(resource_keys))
        for row in group:
            row.status = "target-conflict"
            row.blockers.append(detail)
            row.diff_summary = _diff_summary("target-conflict", [])


def _mark_duplicate_remote_content(rows: list[AssetPlatformRow]) -> None:
    groups: dict[tuple[ItemKind, str], set[str]] = {}
    for row in rows:
        if row.remote_exists and row.remote_content_fingerprint:
            groups.setdefault(
                (row.kind, row.remote_content_fingerprint),
                set(),
            ).add(row.resource_key)
    for row in rows:
        names = groups.get((row.kind, row.remote_content_fingerprint), set())
        if len(names) > 1:
            others = sorted(names - {row.resource_key})
            row.warnings.append(
                "Identical content also exists under: " + ", ".join(others)
            )


def _remote_duplicate_keys(
    snapshot: RemoteSnapshot,
    kind: ItemKind,
    fingerprint: str,
    *,
    exclude_key: str,
) -> list[str]:
    matches: list[str] = []
    for entry in snapshot.registry.items:
        if entry.kind != kind or entry.resource_key == exclude_key:
            continue
        if _entry_content_fingerprint(
            entry,
            _remote_content_path(snapshot.root, entry),
        ) == fingerprint:
            matches.append(entry.resource_key)
    return sorted(matches)


def _finalize_row_actions(row: AssetPlatformRow, snapshot: RemoteSnapshot) -> None:
    actions: list[str] = []
    active = row.entry is None or row.entry.lifecycle == "active"
    target_clear = row.status != "target-conflict"
    if (
        active
        and row.remote_exists
        and row.remote_writable
        and row.configured
        and row.enabled
        and row.supported
        and target_clear
        and snapshot.available
    ):
        actions.extend(["download", "copy-to-local", "set-platform-install-name"])
    elif active and row.remote_exists and row.remote_writable and snapshot.available:
        actions.append("set-platform-install-name")
    if active and row.local_exists:
        if not row.remote_exists or row.remote_writable:
            actions.append("upload")
        actions.append("copy-to-remote")
    row.available_actions = list(dict.fromkeys(actions))
    row.blockers = _unique_strings(row.blockers)
    row.warnings = _unique_strings(row.warnings)


def _select_plan_row(
    rows: list[AssetPlatformRow],
    *,
    action: str,
    local_instance_id: str,
) -> AssetPlatformRow:
    if not rows:
        raise ValueError("No matching asset/platform row exists. Refresh and scan local assets.")
    if len(rows) == 1:
        return rows[0]
    if local_instance_id:
        raise ValueError("The requested local instance id is not unique.")
    if action in {"download", "set-platform-install-name"}:
        expected = [row for row in rows if row.local_locator == "expected"]
        if len(expected) == 1:
            return expected[0]
    local = [row for row in rows if row.local_exists]
    if len(local) == 1:
        return local[0]
    raise ValueError("Multiple local instances match; pass local_instance_id explicitly.")


def _download_plan_blockers(
    row: AssetPlatformRow,
    overwrite_unmanaged: bool,
) -> list[str]:
    blockers: list[str] = []
    if not row.remote_exists:
        blockers.append("The remote asset does not exist.")
    if not row.remote_writable:
        blockers.append("Read-only references cannot be downloaded from the private asset snapshot.")
    if not row.configured or not row.enabled:
        blockers.append("The platform must be configured and enabled before downloading.")
    if not row.supported:
        blockers.append("The resource is not supported on this platform.")
    if row.status == "target-conflict":
        blockers.append("Change the platform install name before downloading.")
    if row.local_exists and row.ownership != "managed" and not overwrite_unmanaged:
        blockers.append("The target is unmanaged; explicitly confirm overwrite to continue.")
    return blockers


def _upload_plan_blockers(row: AssetPlatformRow) -> list[str]:
    blockers: list[str] = []
    if not row.local_exists:
        blockers.append("The local source does not exist.")
    if row.remote_exists and not row.remote_writable:
        blockers.append(
            "The matching remote item is a read-only reference; use copy-to-remote."
        )
    if not row.local_fingerprint:
        blockers.append("The local source cannot be fingerprinted safely.")
    return blockers


def _copy_to_local_blockers(
    row: AssetPlatformRow,
    registry: Registry,
    new_name: str,
) -> list[str]:
    blockers: list[str] = []
    if not row.remote_exists:
        blockers.append("The remote source does not exist.")
    if not row.remote_writable:
        blockers.append("Only private-repository assets can be copied to a local target.")
    if not row.configured or not row.enabled:
        blockers.append("The platform must be configured and enabled before copying locally.")
    if new_name and registry.get(new_name, row.kind) is not None:
        blockers.append("The new name already exists in the remote registry for this kind.")
    return blockers


def _copy_to_remote_blockers(
    row: AssetPlatformRow,
    registry: Registry,
    new_name: str,
) -> list[str]:
    blockers: list[str] = []
    if not row.local_exists:
        blockers.append("The local source does not exist.")
    if not row.local_fingerprint:
        blockers.append("The local source cannot be fingerprinted safely.")
    if new_name and registry.get(new_name, row.kind) is not None:
        blockers.append("The new remote name already exists for this kind.")
    return blockers


def _install_alias_plan_blockers(
    row: AssetPlatformRow,
    registry: Registry,
    cfg: Config,
    install_name: str,
) -> list[str]:
    blockers: list[str] = []
    if not row.remote_exists or not row.remote_writable:
        blockers.append("Only private-repository assets can store a platform install name.")
    if not install_name:
        blockers.append("A platform install name is required.")
    elif not ITEM_NAME_RE.match(install_name):
        blockers.append(
            "The platform install name must use lowercase letters, digits, and hyphens."
        )
    if install_name and _install_name_collision(
        registry,
        cfg,
        platform=row.platform,
        kind=row.kind,
        install_name=install_name,
        exclude_key=row.resource_key,
    ):
        blockers.append("The platform install name collides with another resource target.")
    return blockers


def _copy_local_target_state(
    cfg: Config,
    row: AssetPlatformRow,
    new_name: str,
) -> tuple[Path | None, bool, str, bool]:
    platform = cfg.platforms.get(row.platform)
    if platform is None or not new_name:
        return None, False, "", False
    target = platform.resolve_install_path(row.kind, new_name)
    if target is None:
        return None, False, "", False
    target = target.expanduser().absolute()
    if row.kind == "mcp":
        config = _read_mcp_server(target, new_name)
        exists = config is not None
        fingerprint = (
            _json_fingerprint(sanitize_mcp_config_for_storage(config) or {})
            if exists
            else ""
        )
        managed = (
            is_lpm_managed_mcp(target, new_name, resource_key=f"{row.kind}:{new_name}")
            if exists
            else False
        )
    else:
        exists = target.exists()
        fingerprint = resource_hash_path(target) if exists else ""
        managed = (
            is_lpm_managed(target, resource_key=f"{row.kind}:{new_name}")
            if exists and target.is_dir()
            else False
        )
    return target, exists, fingerprint, managed


def _install_name_collision(
    registry: Registry,
    cfg: Config,
    *,
    platform: str,
    kind: ItemKind,
    install_name: str,
    exclude_key: str,
) -> bool:
    profile = cfg.platforms.get(platform)
    if profile is None:
        profile = build_platform(platform)
    candidate = profile.resolve_install_path(kind, install_name)
    if candidate is None:
        return False
    candidate_key = os.path.normcase(str(candidate.expanduser().absolute()))
    for entry in registry.items:
        if entry.resource_key == exclude_key:
            continue
        other_name = entry.install_target_name(platform)
        other = profile.resolve_install_path(entry.kind, other_name)
        if other is None:
            continue
        if entry.kind == "mcp" and kind == "mcp":
            if other_name == install_name:
                return True
            continue
        if os.path.normcase(str(other.expanduser().absolute())) == candidate_key:
            return True
    return False


def _apply_local_asset_action(
    plan: AssetActionPlan,
    cfg: Config,
) -> AssetActionResult:
    snapshot = _refresh_remote_snapshot(cfg, refresh=True)
    source_key = ResourceKey.parse(plan.resource_key)
    entry = snapshot.registry.get(source_key.name, source_key.kind)
    current_remote_exists = entry is not None
    current_remote_fingerprint = (
        _remote_asset_fingerprint(snapshot.root, entry) if entry else ""
    )
    if (
        current_remote_exists != plan.remote_target_exists
        or current_remote_fingerprint != plan.remote_target_fingerprint
    ):
        raise _StaleAssetTarget(
            "stale-target",
            "The remote asset changed after planning. Refresh and create a new plan.",
        )
    if entry is None:
        raise _StaleAssetTarget("stale-target", "The remote asset no longer exists.")
    if not snapshot.available:
        raise _StaleAssetTarget(
            "remote-unavailable",
            snapshot.warning or "The configured remote branch is unavailable.",
        )

    profile = cfg.platforms.get(plan.platform)
    if profile is None or not profile.enabled:
        raise _StaleAssetTarget(
            "stale-platform",
            "The target platform is no longer configured and enabled.",
        )
    install_name = (
        plan.new_name
        if plan.action == "copy-to-local"
        else entry.install_target_name(plan.platform)
    )
    target = profile.resolve_install_path(entry.kind, install_name)
    if target is None:
        raise _StaleAssetTarget(
            "stale-platform",
            "The platform no longer has a target for this resource kind.",
        )
    target = target.expanduser().absolute()
    current_exists, current_fingerprint, current_managed = _current_target_assertion(
        entry.kind,
        target,
        install_name,
        plan.target_resource_key,
    )
    if (
        current_exists != plan.target_exists
        or current_fingerprint != plan.target_fingerprint
        or current_managed != plan.target_managed
    ):
        raise _StaleAssetTarget(
            "stale-local-target",
            "The local target changed after planning. Refresh and create a new plan.",
        )
    if current_exists and not current_managed and not plan.overwrite_unmanaged:
        raise _StaleAssetTarget(
            "unmanaged-target",
            "The target is unmanaged and overwrite was not confirmed.",
        )

    targets = [
        ChangeTarget(
            path=target,
            change_action=plan.action,
            resource=plan.target_resource_key,
            platform=plan.platform,
        )
    ]
    if entry.kind == "mcp":
        targets.append(
            ChangeTarget(
                path=mcp_ownership_path(),
                change_action=plan.action,
                resource=plan.target_resource_key,
                platform=plan.platform,
            )
        )
    transaction = LocalChangeTransaction.begin(
        f"asset-{plan.action}",
        targets,
        metadata={
            "asset_plan": plan.operation_id,
            "resource_key": plan.resource_key,
            "target_resource_key": plan.target_resource_key,
            "platform": plan.platform,
        },
        lock_timeout_seconds=cfg.state.lock_timeout_seconds,
    )
    transaction.mark_attempted(item.path for item in targets)
    try:
        marker_entry = entry
        if plan.action == "copy-to-local":
            marker_entry = entry.model_copy(
                deep=True,
                update={
                    "name": plan.new_name,
                    "install_dir": "",
                    "platform_install_dirs": {},
                },
            )
        if entry.kind == "mcp":
            config = sanitize_mcp_config_for_storage(entry.mcp_config) or {}
            if not config:
                raise AssetSyncError("The remote MCP asset has no safe configuration.")
            inject_mcp_server(target, install_name, config)
            mark_lpm_managed_mcp(
                target,
                install_name,
                resource_name=marker_entry.name,
                resource_kind=marker_entry.kind,
                resource_key=marker_entry.resource_key,
                platform=plan.platform,
            )
            actual = sanitize_mcp_config_for_storage(
                list_mcp_servers(target).get(install_name)
            )
            if actual != config:
                raise AssetSyncError("MCP download verification failed.")
        else:
            source = _remote_content_path(snapshot.root, entry)
            if source is None or not source.exists() or source.is_symlink():
                raise AssetSyncError("The remote asset content is unavailable or unsafe.")
            _copy_asset_content(source, target, entry.kind)
            write_managed_marker(target, marker_entry, platform=plan.platform)
            if resource_hash_path(source) != resource_hash_path(target):
                raise AssetSyncError("Downloaded asset verification failed.")
        record = transaction.complete(message=f"Applied {plan.action} for {plan.target_resource_key}.")
    except Exception as exc:
        transaction.rollback(str(exc))
        raise

    return AssetActionResult(
        operation_id=plan.operation_id,
        action=plan.action,
        status="succeeded",
        resource_key=plan.resource_key,
        target_resource_key=plan.target_resource_key,
        platform=plan.platform,
        message=f"Applied {plan.action} for {plan.target_resource_key}.",
        remote_commit=snapshot.commit,
        local_path=target,
        replayed_on_latest=snapshot.commit != plan.remote_commit,
        warnings=plan.warnings,
        operation_status=record.status,
    )


def _apply_remote_asset_action(
    plan: AssetActionPlan,
    cfg: Config,
) -> AssetActionResult:
    blocker = _legacy_write_blocker(cfg, fetch=True)
    if blocker:
        return AssetActionResult(
            operation_id=plan.operation_id,
            action=plan.action,
            status="blocked",
            resource_key=plan.resource_key,
            target_resource_key=plan.target_resource_key,
            platform=plan.platform,
            message=blocker,
            warnings=plan.warnings,
        )
    repo_url = _configured_remote_url(cfg)
    if not repo_url:
        return AssetActionResult(
            operation_id=plan.operation_id,
            action=plan.action,
            status="blocked",
            resource_key=plan.resource_key,
            target_resource_key=plan.target_resource_key,
            platform=plan.platform,
            message="No remote resource repository URL is configured.",
            warnings=plan.warnings,
        )

    last_push_error: Exception | None = None
    for attempt in range(2):
        with tempfile.TemporaryDirectory(
            prefix="lpm-asset-write-",
            ignore_cleanup_errors=True,
        ) as temporary:
            worktree = Path(temporary) / "repo"
            _clone_remote_for_write(repo_url, worktree, cfg)
            registry_path = worktree / DEFAULT_REGISTRY_FILENAME
            if not registry_path.is_file():
                ensure_structure(worktree)
            registry = load_registry(registry_path)
            latest_commit = git_ops.head_commit(worktree) or ""
            target_key = ResourceKey.parse(plan.target_resource_key)
            target_entry = registry.get(target_key.name, target_key.kind)
            target_exists = target_entry is not None
            target_fingerprint = (
                _remote_asset_fingerprint(worktree, target_entry)
                if target_entry is not None
                else ""
            )
            if (
                target_exists != plan.remote_target_exists
                or target_fingerprint != plan.remote_target_fingerprint
            ):
                raise _StaleAssetTarget(
                    "stale-target",
                    "The target remote asset changed after planning.",
                )

            current_snapshot = RemoteSnapshot(
                root=worktree,
                registry=registry,
                commit=latest_commit,
                branch=cfg.resources.branch or "main",
                repo_url=repo_url,
            )
            source_row: AssetPlatformRow | None = None
            if plan.action in {"upload", "copy-to-remote"}:
                inventory = build_asset_inventory(
                    config=cfg,
                    scan_local=True,
                    refresh_remote=False,
                    remote_snapshot=current_snapshot,
                )
                source_row = _find_planned_local_row(inventory, plan)
                if (
                    not source_row.local_exists
                    or source_row.local_fingerprint != plan.local_source_fingerprint
                ):
                    raise _StaleAssetTarget(
                        "stale-local-source",
                        "The local source changed after planning.",
                    )

            changed = _mutate_remote_asset(
                worktree,
                registry,
                plan,
                source_row,
            )
            if not changed:
                return AssetActionResult(
                    operation_id=plan.operation_id,
                    action=plan.action,
                    status="unchanged",
                    resource_key=plan.resource_key,
                    target_resource_key=plan.target_resource_key,
                    platform=plan.platform,
                    message="The requested remote state already matches.",
                    remote_commit=latest_commit,
                    replayed_on_latest=latest_commit != plan.remote_commit,
                    push_retry_count=attempt,
                    warnings=plan.warnings,
                )

            commit_resource_changes_unlocked(
                worktree,
                message=_asset_commit_message(plan),
            )
            committed = git_ops.head_commit(worktree) or ""
            try:
                git_ops.push(
                    worktree,
                    branch=cfg.resources.branch or "main",
                    token=resource_repo_auth_token(cfg),
                )
            except git_ops.GitError as exc:
                last_push_error = exc
                if attempt == 0:
                    continue
                raise
            return AssetActionResult(
                operation_id=plan.operation_id,
                action=plan.action,
                status="succeeded",
                resource_key=plan.resource_key,
                target_resource_key=plan.target_resource_key,
                platform=plan.platform,
                message=f"Applied {plan.action} and pushed one asset-level commit.",
                remote_commit=committed,
                replayed_on_latest=latest_commit != plan.remote_commit,
                push_retry_count=attempt,
                warnings=plan.warnings,
            )
    raise AssetSyncError(str(last_push_error or "Remote push failed."))


def _clone_remote_for_write(repo_url: str, destination: Path, cfg: Config) -> None:
    branch = cfg.resources.branch or "main"
    try:
        git_ops.clone(
            repo_url,
            destination,
            ref=branch,
            token=resource_repo_auth_token(cfg),
        )
    except git_ops.GitError:
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        git_ops.clone(
            repo_url,
            destination,
            token=resource_repo_auth_token(cfg),
        )
        git_ops.checkout_local_branch(destination, branch)


def _find_planned_local_row(
    inventory: AssetInventory,
    plan: AssetActionPlan,
) -> AssetPlatformRow:
    matches = [
        row
        for row in inventory.rows
        if row.resource_key == plan.resource_key
        and row.platform == plan.platform
        and row.local_instance_id == plan.local_instance_id
        and row.local_locator == plan.local_locator
    ]
    if len(matches) != 1:
        raise _StaleAssetTarget(
            "stale-local-source",
            "The planned local instance can no longer be resolved uniquely.",
        )
    return matches[0]


def _mutate_remote_asset(
    root: Path,
    registry: Registry,
    plan: AssetActionPlan,
    source_row: AssetPlatformRow | None,
) -> bool:
    target_key = ResourceKey.parse(plan.target_resource_key)
    existing = registry.get(target_key.name, target_key.kind)
    registry_path = root / DEFAULT_REGISTRY_FILENAME
    if plan.action == "set-platform-install-name":
        if existing is None or not _is_private_repo_asset(existing):
            raise _StaleAssetTarget(
                "stale-target",
                "The target asset is no longer writable in the private repository.",
            )
        if existing.platform_install_dirs.get(plan.platform) == plan.new_install_name:
            return False
        updated = existing.model_copy(deep=True)
        updated.platform_install_dirs[plan.platform] = plan.new_install_name
        registry.upsert(updated)
        save_registry(registry, registry_path)
        return True

    if source_row is None or source_row.local_path is None:
        raise _StaleAssetTarget("stale-local-source", "The local source is unavailable.")
    local_path = source_row.local_path
    local_mcp_config = (
        _read_mcp_server(local_path, source_row.install_name)
        if target_key.kind == "mcp"
        else None
    )
    if target_key.kind == "mcp":
        local_mcp_config = sanitize_mcp_config_for_storage(local_mcp_config)
        if not local_mcp_config:
            raise AssetSyncError("The local MCP source cannot be parsed safely.")
        validate_item(local_path, "mcp", mcp_config=local_mcp_config)
    else:
        validate_item(local_path, target_key.kind)

    if existing is not None and not _is_private_repo_asset(existing):
        raise _StaleAssetTarget(
            "stale-target",
            "The target asset became a read-only reference.",
        )
    relative_path = (
        existing.path
        if existing is not None and existing.path
        else f"{RESOURCE_PARENT_BY_KIND[target_key.kind]}/{target_key.name}"
    )
    destination = (root / relative_path).absolute()
    root_abs = root.absolute()
    if destination == root_abs or root_abs not in destination.parents:
        raise AssetSyncError("The remote asset path is outside the resource repository.")

    if target_key.kind == "mcp":
        if destination.exists():
            _remove_asset_path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "mcp.json").write_text(
            json.dumps(local_mcp_config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    else:
        _copy_asset_content(local_path, destination, target_key.kind)

    derived = _derive_metadata(
        target_key.kind,
        destination,
        mcp_config=local_mcp_config,
    )
    if existing is None:
        updated = RegistryItem(
            name=target_key.name,
            kind=target_key.kind,
            source="local",
            path=relative_path,
            repo="",
            ref="",
        )
    else:
        updated = existing.model_copy(deep=True)
    updated.name = target_key.name
    updated.kind = target_key.kind
    if existing is None:
        updated.source = "local"
    updated.path = relative_path
    for field_name in DERIVED_METADATA_FIELDS:
        value = derived.get(field_name)
        if value not in (None, ""):
            setattr(updated, field_name, str(value))
    if target_key.kind == "mcp" and isinstance(derived.get("mcp_config"), dict):
        updated.mcp_config = derived["mcp_config"]
    registry.upsert(updated)
    save_registry(registry, registry_path)
    return True


def _asset_commit_message(plan: AssetActionPlan) -> str:
    if plan.action == "upload":
        return f"lpm: update {plan.target_resource_key}"
    if plan.action == "copy-to-remote":
        return f"lpm: create {plan.target_resource_key}"
    return f"lpm: set install name for {plan.target_resource_key} on {plan.platform}"


def _current_target_assertion(
    kind: ItemKind,
    target: Path,
    install_name: str,
    resource_key: str,
) -> tuple[bool, str, bool]:
    if kind == "mcp":
        config = _read_mcp_server(target, install_name)
        exists = config is not None
        fingerprint = (
            _json_fingerprint(sanitize_mcp_config_for_storage(config) or {})
            if exists
            else ""
        )
        managed = (
            is_lpm_managed_mcp(target, install_name, resource_key=resource_key)
            if exists
            else False
        )
        return exists, fingerprint, managed
    exists = target.exists()
    fingerprint = resource_hash_path(target) if exists else ""
    managed = (
        is_lpm_managed(target, resource_key=resource_key)
        if exists and target.is_dir()
        else False
    )
    return exists, fingerprint, managed


def _copy_asset_content(source: Path, destination: Path, kind: ItemKind) -> None:
    if source.is_file() and kind in {"rule", "prompt"}:
        if destination.exists():
            _remove_asset_path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        copy_resource_tree(source, destination / source.name)
        return
    copy_resource_tree(source, destination)


def _remove_asset_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _read_mcp_server(path: Path, server_name: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = list_mcp_servers(path).get(server_name)
    except Exception:
        return None
    return dict(value) if isinstance(value, dict) else None


def _derive_metadata(
    kind: ItemKind,
    path: Path,
    *,
    mcp_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if kind == "mcp":
        sanitized = sanitize_mcp_config_for_storage(mcp_config)
        return {"mcp_config": sanitized} if sanitized else {}

    candidates: list[Path] = []
    if path.is_file():
        candidates = [path]
    elif kind == "skill":
        candidates = [path / "SKILL.md"]
    elif kind in {"rule", "prompt"}:
        candidates = sorted(
            item
            for item in path.rglob("*")
            if item.is_file() and item.suffix.lower() in {".md", ".mdc"}
        )
    elif kind == "plugin":
        candidates = [
            path / "package.json",
            path / ".codex-plugin" / "plugin.json",
            path / ".claude-plugin" / "plugin.json",
            path / "plugin.json",
        ]

    if kind == "plugin":
        for candidate in candidates:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            author = data.get("author")
            if isinstance(author, dict):
                author = author.get("name")
            license_value = data.get("license")
            if isinstance(license_value, dict):
                license_value = license_value.get("type")
            return _non_empty_metadata(
                {
                    "description": data.get("description"),
                    "version": data.get("version"),
                    "author": author,
                    "license": license_value,
                }
            )
        return {}

    for candidate in candidates:
        if not candidate.is_file() or candidate.is_symlink():
            continue
        try:
            post = frontmatter.load(candidate)
        except Exception:
            continue
        return _non_empty_metadata(
            {
                "description": post.get("description"),
                "version": post.get("version"),
                "author": post.get("author"),
                "license": post.get("license"),
            }
        )
    return {}


def _non_empty_metadata(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.strip() if isinstance(value, str) else value
        for key, value in values.items()
        if value not in (None, "")
        and (not isinstance(value, str) or value.strip())
    }


def _metadata_differences(
    entry: RegistryItem,
    derived: dict[str, Any],
) -> list[str]:
    differences: list[str] = []
    for field_name in DERIVED_METADATA_FIELDS:
        if field_name not in derived:
            continue
        if str(getattr(entry, field_name, "") or "") != str(derived[field_name] or ""):
            differences.append(field_name)
    if entry.kind == "mcp" and isinstance(derived.get("mcp_config"), dict):
        expected = sanitize_mcp_config_for_storage(entry.mcp_config) or {}
        if expected != derived["mcp_config"]:
            differences.append("mcp_config")
    return differences


def _reference_commit(
    entry: RegistryItem,
    cfg: Config,
    cache: dict[tuple[str, str], str],
) -> str:
    key = (entry.repo, entry.ref or "main")
    if key in cache:
        return cache[key]
    try:
        value = git_ops.remote_url_commit(
            entry.repo,
            entry.ref or "main",
            token=cfg.github.token or None,
        )
    except Exception:
        value = None
    cache[key] = value or ""
    return cache[key]


def _is_private_repo_asset(entry: RegistryItem) -> bool:
    return entry.source in {"local", "owned"} and bool(entry.path)


def _legacy_write_blocker(cfg: Config, *, fetch: bool) -> str:
    root = resource_root(cfg)
    if git_ops.is_repo(root):
        try:
            if git_ops.status_short(root):
                return (
                    "The legacy resource workspace is dirty. Use the deprecated resource "
                    "commit/sync commands to commit, cancel, or clean it before remote asset writes."
                )
            if fetch:
                git_ops.fetch(
                    root,
                    ref=cfg.resources.branch or "main",
                    token=resource_repo_auth_token(cfg),
                )
            divergence = git_ops.divergence(
                root,
                branch=cfg.resources.branch or "main",
            )
            current_branch = git_ops.current_branch(root)
            if current_branch != (cfg.resources.branch or "main"):
                return (
                    "The legacy resource workspace is on the wrong branch. Resolve it "
                    "with the deprecated resource sync commands before remote asset writes."
                )
            if divergence.state in {"ahead", "diverged"}:
                return (
                    f"The legacy resource workspace is {divergence.state}. Resolve its "
                    "local commits with the deprecated resource sync commands before "
                    "remote asset writes."
                )
        except Exception as exc:
            return f"The legacy resource workspace state cannot be verified: {exc}"

    sync_root = default_state_dir() / "sync"
    if sync_root.is_dir():
        for plan_path in sorted(sync_root.glob("*/plan.json")):
            try:
                plan = load_resource_sync_plan(plan_path.parent.name)
            except Exception:
                continue
            if plan.status not in {"applied", "cancelled", "abandoned"}:
                return (
                    f"Legacy sync plan {plan.operation_id} is still pending. Apply, cancel, "
                    "or clean it with the deprecated resource sync commands first."
                )
    return ""


def _save_asset_plan(plan: AssetActionPlan) -> Path:
    path = _asset_plan_dir(plan.operation_id) / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, _jsonable(asdict(plan)))
    return path


def _save_asset_result(result: AssetActionResult) -> Path:
    path = _asset_plan_dir(result.operation_id) / "result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, _jsonable(asdict(result)))
    return path


def _load_asset_result(operation_id: str) -> AssetActionResult | None:
    path = _asset_plan_dir(operation_id) / "result.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    action = str(data.get("action") or "")
    if action not in {
        "download",
        "upload",
        "copy-to-local",
        "copy-to-remote",
        "set-platform-install-name",
    }:
        return None
    local_path = str(data.get("local_path") or "")
    return AssetActionResult(
        operation_id=operation_id,
        action=action,  # type: ignore[arg-type]
        status=str(data.get("status") or ""),
        resource_key=str(data.get("resource_key") or ""),
        target_resource_key=str(data.get("target_resource_key") or ""),
        platform=str(data.get("platform") or ""),
        message=str(data.get("message") or ""),
        remote_commit=str(data.get("remote_commit") or ""),
        local_path=Path(local_path) if local_path else None,
        replayed_on_latest=bool(data.get("replayed_on_latest", False)),
        push_retry_count=int(data.get("push_retry_count", 0)),
        warnings=[str(item) for item in data.get("warnings", [])],
        operation_status=str(data.get("operation_status") or ""),
    )


def _asset_plan_dir(operation_id: str) -> Path:
    if (
        not operation_id
        or operation_id in {".", ".."}
        or any(char in operation_id for char in "/\\\0")
    ):
        raise AssetPlanInvalid("Invalid asset action operation id.")
    return default_state_dir() / ASSET_PLAN_DIR / operation_id


def _cleanup_expired_asset_plans(cfg: Config) -> None:
    root = default_state_dir() / ASSET_PLAN_DIR
    if not root.is_dir():
        return
    cutoff = datetime.now(timezone.utc).timestamp() - cfg.state.retention_days * 86400
    for path in root.iterdir():
        if not path.is_dir():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                _remove_internal_path(path, root)
        except OSError:
            continue


def _remove_internal_path(path: Path, allowed_root: Path) -> None:
    resolved = path.absolute()
    root = allowed_root.absolute()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"Refusing to remove path outside internal state: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved, ignore_errors=True)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _json_fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _instance_id(
    locator: str,
    resource_key: str,
    platform: str,
    path: Path,
    logical_name: str,
) -> str:
    payload = f"{locator}\0{resource_key}\0{platform}\0{path.absolute()}\0{logical_name}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _local_identity(
    platform: str,
    kind: ItemKind,
    path: Path,
    logical_name: str,
) -> tuple[str, str, str, str]:
    return (
        platform,
        kind,
        os.path.normcase(str(path.expanduser().absolute())),
        logical_name,
    )


def _unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
