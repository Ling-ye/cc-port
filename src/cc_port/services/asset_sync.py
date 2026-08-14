"""Asset-level inventory, planning, and two-way synchronization."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import frontmatter
import yaml

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - py310 fallback
    import tomli as tomllib

from ..core.claude_plugins import inspect_claude_plugin, inspect_claude_skill
from ..core.config import (
    Config,
    default_state_dir,
    load_config,
    resource_repo_auth_token,
    resource_repo_private_path_conflicts,
)
from ..core.models import (
    ITEM_NAME_RE,
    ItemKind,
    PluginInstallation,
    PluginOrigin,
    PluginProjectIdentity,
    PluginSpec,
    Registry,
    RegistryItem,
    ResourceKey,
)
from ..core.ownership import (
    is_cc_port_managed,
    is_cc_port_managed_mcp,
    managed_marker_path,
    managed_mcp_resource_key,
    managed_resource_key,
    mark_cc_port_managed_mcp,
    mcp_ownership_path,
    write_managed_marker,
)
from ..core.platforms import (
    PlatformProfile,
    build_platform,
    current_environment_identity,
    is_cross_platform_absolute_path,
    resolve_portable_resource_platforms,
    validate_portable_tool_id,
)
from ..core.registry import DEFAULT_REGISTRY_FILENAME, load_registry, save_registry
from ..core.resource_files import is_resource_path_excluded
from ..core.secret_scan import find_secret_text
from ..core.secrets import sanitize_mcp_config_for_storage
from ..core.tool_adapters import tool_adapter_by_id
from ..core.validator import validate_item
from ..infrastructure import git_ops
from .claude_plugin_installer import (
    claude_cli_context,
    install_marketplace_plugin,
    installable_marketplace_source,
    marketplace_install_ready,
    set_marketplace_plugin_enabled,
)
from .env_manager import EnvDiscoveryResult, discover_environment
from .install_planner import copy_resource_tree
from .local_path_probe import (
    LocalPathProbe,
    is_known_canonical_link_target,
    probe_local_path,
    resource_tree_issues,
)
from .local_transaction import (
    ChangeTarget,
    LocalChangeTransaction,
    resource_hash_path,
)
from .mcp_installer import inject_mcp_server, list_mcp_servers
from .plugin_management import plugin_resource_name
from .registry_audit import RegistryHealthSummary, audit_registry_root
from .resource_commit import commit_resource_changes_unlocked
from .resource_repo import ensure_structure, resource_root
from .resource_repo_lock import resource_repo_write_lock
from .resource_sync import load_resource_sync_plan
from .ui_messages import (
    UiMessageRef,
    fallback_text,
    ui_message,
    ui_message_from_data,
    ui_messages_from_data,
)

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
    "align-plugin-state",
    "plugin-delete",
]

ASSET_STATE_DIR = "assets"
ASSET_PLAN_DIR = "asset-plans"
ASSET_PLAN_SCHEMA_VERSION = 3
REMOTE_CACHE_DIR = "remotes"
REMOTE_SNAPSHOT_DIR = "snapshots"
REMOTE_SNAPSHOT_FORMAT_FILE = ".cc-port-snapshot-format"
REMOTE_SNAPSHOT_REGISTRY_HEALTH_FILE = ".cc-port-registry-health.json"
REMOTE_SNAPSHOT_FORMAT_VERSION = "host-autocrlf-disabled-v2"
REMOTE_WRITE_ACTIONS = {"upload", "copy-to-remote", "set-platform-install-name"}
LOCAL_WRITE_ACTIONS = {"download", "copy-to-local", "align-plugin-state"}
RESOURCE_PARENT_BY_KIND: dict[ItemKind, str] = {
    "skill": "skills",
    "mcp": "mcp",
    "rule": "rules",
    "prompt": "prompts",
    "plugin": "plugins",
    "instruction": "instructions",
    "memory": "memories",
}
DERIVED_METADATA_FIELDS = ("description", "version", "author", "license")
ASSET_DIFF_MAX_FILES = 200
ASSET_DIFF_MAX_FILE_BYTES = 1_000_000
ASSET_DIFF_MAX_FILE_CHARS = 60_000
ASSET_DIFF_MAX_TOTAL_CHARS = 240_000


@dataclass
class RemoteSnapshot:
    root: Path
    registry: Registry | None
    commit: str
    branch: str
    repo_url: str
    available: bool = True
    warning: str = ""
    warning_ref: UiMessageRef | None = None
    registry_health: RegistryHealthSummary | None = None


@dataclass
class AssetPlatformRow:
    resource_key: str
    kind: str
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
    local_content_path: Path | None = None
    path_kind: str = "regular"
    link_health: str = "ready"
    link_target: str = ""
    reparse_tag: str = ""
    link_target_trusted: bool = True
    metadata_differences: list[str] = field(default_factory=list)
    diff_summary: list[str] = field(default_factory=list)
    diff_summary_refs: list[UiMessageRef] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    blocker_refs: list[UiMessageRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    warning_refs: list[UiMessageRef] = field(default_factory=list)
    available_actions: list[str] = field(default_factory=list)
    entry: RegistryItem | None = None
    plugin_track: str = ""
    plugin_id: str = ""
    plugin_scope: str = ""
    plugin_project_id: str = ""
    plugin_source_kind: str = ""
    plugin_source_id: str = ""
    plugin_marketplace: str = ""
    plugin_marketplace_source: str = ""
    plugin_selector: str = ""
    plugin_observed_version: str = ""
    plugin_enabled: bool | None = None
    plugin_writable: bool = True
    plugin_data: dict[str, Any] = field(default_factory=dict)
    tool_id: str = ""
    environment_kind: str = ""
    environment_name: str = ""
    display_name: str = ""
    memory_layout: str = ""


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
    remote_warning_ref: UiMessageRef | None = None
    legacy_write_blocker_ref: UiMessageRef | None = None
    resources: list[AssetResourceRow] = field(default_factory=list)
    registry_health: RegistryHealthSummary | None = None


@dataclass
class AssetLocalInstance:
    id: str
    platform: str
    install_name: str
    path: Path | None
    ownership: str
    fingerprint: str
    description: str
    status: AssetStatus
    warnings: list[str] = field(default_factory=list)
    warning_refs: list[UiMessageRef] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    blocker_refs: list[UiMessageRef] = field(default_factory=list)
    track: str = ""
    scope: str = ""
    project_id: str = ""
    source_kind: str = ""
    source_id: str = ""
    selector: str = ""
    observed_version: str = ""
    enabled: bool | None = None
    writable: bool = True
    content_path: Path | None = None
    path_kind: str = "regular"
    link_health: str = "ready"
    link_target: str = ""
    reparse_tag: str = ""
    link_target_trusted: bool = True
    tool_id: str = ""
    environment_kind: str = ""
    environment_name: str = ""
    display_name: str = ""
    memory_layout: str = ""


@dataclass
class AssetRemoteState:
    exists: bool
    status: str
    writable: bool
    read_only: bool
    commit: str
    path: Path | None
    description: str


@dataclass
class AssetResourceRow:
    resource_key: str
    kind: str
    name: str
    description: str
    description_source: str
    local_status: str
    remote_status: str
    status: AssetStatus
    remote: AssetRemoteState
    local_instances: list[AssetLocalInstance]
    metadata_differences: list[str] = field(default_factory=list)
    diff_summary: list[str] = field(default_factory=list)
    diff_summary_refs: list[UiMessageRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    warning_refs: list[UiMessageRef] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    blocker_refs: list[UiMessageRef] = field(default_factory=list)
    available_actions: list[str] = field(default_factory=list)
    plugin_track: str = ""
    plugin_platform: str = ""
    plugin_id: str = ""
    plugin_source_kind: str = ""
    plugin_source_id: str = ""
    plugin_marketplace: str = ""
    plugin_marketplace_source: str = ""
    plugin_selector: str = ""
    plugin_observed_version: str = ""


@dataclass
class AssetDiffFile:
    path: str
    status: Literal["added", "deleted", "modified"]
    diff: str
    binary: bool = False
    truncated: bool = False


@dataclass
class AssetContentDiff:
    resource_key: str
    local_instance_id: str
    platform: str
    remote_commit: str
    files: list[AssetDiffFile]
    added_files: int
    deleted_files: int
    modified_files: int
    binary_files: int
    truncated: bool = False


@dataclass
class _AssetDiffBlob:
    data: bytes
    size: int
    truncated: bool = False


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
    source_path: Path | None = None
    source_content_path: Path | None = None
    source_path_kind: str = "regular"
    source_link_health: str = "ready"
    source_link_target: str = ""
    source_reparse_tag: str = ""
    link_target_confirmed: bool = False
    overwrite_unmanaged: bool = False
    new_name: str = ""
    new_install_name: str = ""
    tool_id: str = ""
    environment_kind: str = ""
    environment_name: str = ""
    warnings: list[str] = field(default_factory=list)
    warning_refs: list[UiMessageRef] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    blocker_refs: list[UiMessageRef] = field(default_factory=list)
    blocked: bool = False
    created_at: str = ""
    plugin_data: dict[str, Any] = field(default_factory=dict)
    remote_repo_hash: str = ""
    remote_branch: str = ""
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
    message_ref: UiMessageRef | None = None
    remote_commit: str = ""
    local_path: Path | None = None
    replayed_on_latest: bool = False
    push_retry_count: int = 0
    warnings: list[str] = field(default_factory=list)
    warning_refs: list[UiMessageRef] = field(default_factory=list)
    operation_status: str = ""


@dataclass
class AssetBatchChoice:
    resource_key: str
    platform: str = ""
    local_instance_id: str = ""
    resolution: str = "overwrite"
    new_name: str = ""
    overwrite_unmanaged: bool = False
    plugin_track: str = ""
    ownership_confirmed: bool = False
    link_target_confirmed: bool = False
    reference_origin: dict[str, str] = field(default_factory=dict)
    plugin_dependencies: dict[str, str] = field(default_factory=dict)


@dataclass
class AssetBatchPlanItem:
    id: str
    resource_key: str
    platform: str
    local_instance_id: str
    action: str
    disposition: str
    target_resource_key: str
    reason: str = ""
    reason_ref: UiMessageRef | None = None
    warnings: list[str] = field(default_factory=list)
    warning_refs: list[UiMessageRef] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    blocker_refs: list[UiMessageRef] = field(default_factory=list)
    plan: AssetActionPlan | None = None


@dataclass
class AssetBatchResourceCheck:
    resource_key: str
    local_status: str
    remote_status: str
    status: str
    local_instances: list[AssetLocalInstance] = field(default_factory=list)


@dataclass
class AssetBatchPlan:
    direction: str
    resource_keys: list[str]
    target_platforms: list[str]
    remote_commit: str
    plan_hash: str
    items: list[AssetBatchPlanItem]
    executable_count: int
    blocked_count: int
    skipped_count: int
    status: str = "planned"
    checked_resources: list[AssetBatchResourceCheck] = field(default_factory=list)
    remote_repo_hash: str = ""
    remote_branch: str = ""


@dataclass
class AssetBatchResult:
    status: str
    plan_hash: str
    results: list[AssetActionResult]
    stale_plan: AssetBatchPlan | None = None


@dataclass
class PluginReferenceResult:
    status: str
    resource_key: str
    entry: RegistryItem
    remote_commit: str = ""
    pushed: bool = False


@dataclass
class PluginDeleteInstancePlan:
    id: str
    platform: str
    scope: str
    project_id: str
    enabled: bool | None
    writable: bool
    selectable: bool
    method: str
    detail: str
    detail_ref: UiMessageRef | None = None
    local_path: Path | None = None
    state_path: Path | None = None
    installation: dict[str, Any] = field(default_factory=dict)
    plugin_id: str = ""
    source_id: str = ""


@dataclass
class PluginDeletePlan:
    resource_key: str
    remote_commit: str
    selected_instance_ids: list[str]
    instances: list[PluginDeleteInstancePlan]
    plan_hash: str
    blocked: bool = False
    blockers: list[str] = field(default_factory=list)
    blocker_refs: list[UiMessageRef] = field(default_factory=list)


@dataclass
class PluginDeleteResult:
    status: str
    resource_key: str
    plan_hash: str
    results: list[AssetActionResult]
    remote_deleted: bool = False
    remote_commit: str = ""
    stale_plan: PluginDeletePlan | None = None


@dataclass
class _PlatformContext:
    profile: PlatformProfile
    configured: bool
    detected: bool
    supported_kinds: set[ItemKind]
    environment_available: bool = True
    environment_problem: str = ""
    memory_problem: str = ""


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
    scan_global: bool = True,
    project_ids: list[str] | None = None,
) -> AssetInventory:
    """Build logical resources plus internal platform comparison rows without writes."""
    cfg = config or load_config()
    _assert_private_asset_boundaries(cfg)
    git_ops.configure_git_executable(cfg.git.executable)
    _cleanup_expired_asset_plans(cfg)
    discovery: EnvDiscoveryResult | None = None
    if scan_local and remote_snapshot is None:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="cc-port-inventory") as executor:
            remote_future = executor.submit(
                _refresh_remote_snapshot,
                cfg,
                refresh=refresh_remote,
            )
            local_future = executor.submit(
                _discover_inventory_environment,
                cfg,
                scan_global=scan_global,
                project_ids=project_ids,
            )
            snapshot = remote_future.result()
            discovery = local_future.result()
    else:
        snapshot = remote_snapshot or _refresh_remote_snapshot(cfg, refresh=refresh_remote)
        if scan_local:
            discovery = _discover_inventory_environment(
                cfg,
                scan_global=scan_global,
                project_ids=project_ids,
            )
    if discovery is None:
        # Even a remote-only inventory must resolve adapter settings that alter
        # native write targets (notably Claude autoMemoryDirectory).  This scan
        # reads only configured tool metadata; it does not enumerate resources,
        # MCP servers, plugins, or projects.
        discovery = discover_environment(
            config=cfg,
            scan_global=False,
            project_ids=[],
        )
    if snapshot.registry is None:
        snapshot = replace(
            snapshot,
            registry=Registry(),
            warning=(
                snapshot.warning
                or (snapshot.registry_health.message if snapshot.registry_health else "")
                or "The remote registry is unavailable."
            ),
        )
    assert snapshot.registry is not None
    registry_entries = snapshot.registry.items
    known_entries = [entry for entry in registry_entries if entry.kind in RESOURCE_PARENT_BY_KIND]
    unknown_entries = [
        entry for entry in registry_entries if entry.kind not in RESOURCE_PARENT_BY_KIND
    ]
    for entry in known_entries:
        _hydrate_remote_metadata(snapshot.root, entry)
    contexts = _platform_contexts(cfg, discovery)
    reference_commits: dict[tuple[str, str], str] = {}
    rows: list[AssetPlatformRow] = []
    seen_local_paths: set[tuple[str, str, str, str]] = set()

    for entry in known_entries:
        if entry.kind == "plugin" and entry.plugin is not None:
            plugin_rows = _expected_plugin_rows(entry, snapshot, cfg, contexts)
            rows.extend(plugin_rows)
            for plugin_row in plugin_rows:
                if plugin_row.local_exists and plugin_row.local_path is not None:
                    seen_local_paths.add(
                        _local_identity(
                            plugin_row.platform,
                            "plugin",
                            plugin_row.local_path,
                            "",
                        )
                    )
            continue
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
    blocker_ref = _legacy_write_blocker_message(cfg, fetch=False)
    blocker = blocker_ref.fallback if blocker_ref else ""
    inventory = AssetInventory(
        branch=snapshot.branch,
        remote_commit=snapshot.commit,
        repo_url=snapshot.repo_url,
        remote_available=snapshot.available,
        remote_warning=snapshot.warning,
        remote_warning_ref=snapshot.warning_ref,
        scanned_local=scan_local,
        generated_at=_utc_now(),
        legacy_write_blocker=blocker,
        legacy_write_blocker_ref=blocker_ref,
        rows=rows,
        registry_health=snapshot.registry_health,
    )
    inventory.resources = _aggregate_resource_rows(inventory)
    inventory.resources.extend(
        _unknown_registry_resource(entry, inventory, snapshot.root) for entry in unknown_entries
    )
    inventory.resources.sort(key=lambda item: (item.kind, item.name))
    return inventory


def build_asset_content_diff(
    resource_key: str,
    local_instance_id: str,
    *,
    config: Config | None = None,
) -> AssetContentDiff:
    """Build a bounded, read-only remote-to-local content diff on demand."""
    cfg = config or load_config()
    snapshot = _refresh_remote_snapshot(cfg, refresh=False)
    inventory = build_asset_inventory(
        config=cfg,
        scan_local=True,
        refresh_remote=False,
        remote_snapshot=snapshot,
    )
    matching_rows = [row for row in inventory.rows if row.resource_key == resource_key]
    if not matching_rows:
        raise ValueError(f"Resource is not available: {resource_key}")
    row = next(
        (
            candidate
            for candidate in matching_rows
            if candidate.local_instance_id == local_instance_id and candidate.local_exists
        ),
        None,
    )
    if row is None:
        raise ValueError(f"Local instance is not available: {local_instance_id}")
    if not row.remote_exists:
        raise ValueError(f"Remote content is not available: {resource_key}")

    remote_files, local_files, source_truncated = _asset_diff_file_maps(row)
    files: list[AssetDiffFile] = []
    total_chars = 0
    response_truncated = source_truncated
    for path in sorted(set(remote_files) | set(local_files)):
        remote_blob = remote_files.get(path)
        local_blob = local_files.get(path)
        if (
            remote_blob is not None
            and local_blob is not None
            and remote_blob.size == local_blob.size
            and not remote_blob.truncated
            and not local_blob.truncated
            and remote_blob.data == local_blob.data
        ):
            continue
        status: Literal["added", "deleted", "modified"] = (
            "added" if remote_blob is None else "deleted" if local_blob is None else "modified"
        )
        file_diff = _asset_diff_file(path, status, remote_blob, local_blob)
        remaining = ASSET_DIFF_MAX_TOTAL_CHARS - total_chars
        if remaining <= 0:
            file_diff.diff = ""
            file_diff.truncated = True
            response_truncated = True
        elif len(file_diff.diff) > remaining:
            file_diff.diff = file_diff.diff[:remaining]
            file_diff.truncated = True
            response_truncated = True
        total_chars += len(file_diff.diff)
        response_truncated = response_truncated or file_diff.truncated
        files.append(file_diff)

    return AssetContentDiff(
        resource_key=resource_key,
        local_instance_id=local_instance_id,
        platform=row.platform,
        remote_commit=snapshot.commit,
        files=files,
        added_files=sum(item.status == "added" for item in files),
        deleted_files=sum(item.status == "deleted" for item in files),
        modified_files=sum(item.status == "modified" for item in files),
        binary_files=sum(item.binary for item in files),
        truncated=response_truncated,
    )


def _asset_diff_file_maps(
    row: AssetPlatformRow,
) -> tuple[dict[str, _AssetDiffBlob], dict[str, _AssetDiffBlob], bool]:
    if row.kind == "mcp":
        local_mcp_path = row.local_content_path or row.local_path
        remote_config = (
            sanitize_mcp_config_for_storage(row.entry.mcp_config)
            if row.entry and row.entry.mcp_config is not None
            else None
        )
        local_config = (
            sanitize_mcp_config_for_storage(_read_mcp_server(local_mcp_path, row.install_name))
            if local_mcp_path is not None
            else None
        )
        if remote_config is None or local_config is None:
            raise ValueError("MCP configuration is not available for comparison.")
        return (
            {"mcp.json": _asset_diff_json_blob(remote_config)},
            {"mcp.json": _asset_diff_json_blob(local_config)},
            False,
        )

    remote_source = row.remote_path
    local_source = row.local_content_path or row.local_path
    if (
        row.kind == "plugin"
        and row.entry is not None
        and row.entry.plugin is not None
        and row.entry.plugin.track == "content"
    ):
        remote_source = _plugin_remote_content_source(
            remote_source,
            row.entry.plugin,
        )
    if row.kind == "prompt" and local_source is not None and local_source.is_file():
        remote_source, problem = _prompt_payload_path(remote_source)
        if remote_source is None:
            raise ValueError(problem or "Remote Prompt content is not available.")
        remote_blob = _asset_diff_read_file(remote_source)
        local_blob = _asset_diff_read_file(local_source)
        return (
            {"prompt.md": remote_blob},
            {"prompt.md": local_blob},
            remote_blob.truncated or local_blob.truncated,
        )
    if remote_source is None or not remote_source.exists():
        raise ValueError("Remote content is not available for comparison.")
    if local_source is None or not local_source.exists():
        raise ValueError("Local content is not available for comparison.")
    if remote_source.is_file() and local_source.is_file():
        remote_blob = _asset_diff_read_file(remote_source)
        local_blob = _asset_diff_read_file(local_source)
        display_name = remote_source.name if remote_source.name == local_source.name else "content"
        return (
            {display_name: remote_blob},
            {display_name: local_blob},
            remote_blob.truncated or local_blob.truncated,
        )
    include_all = row.kind == "memory"
    remote_files, remote_truncated = _asset_diff_collect_files(
        remote_source,
        include_excluded=include_all,
    )
    local_files, local_truncated = _asset_diff_collect_files(
        local_source,
        include_excluded=include_all,
    )
    return remote_files, local_files, remote_truncated or local_truncated


def _asset_diff_json_blob(value: object) -> _AssetDiffBlob:
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return _AssetDiffBlob(data=data, size=len(data))


def _asset_diff_collect_files(
    root: Path,
    *,
    include_excluded: bool = False,
) -> tuple[dict[str, _AssetDiffBlob], bool]:
    if root.is_symlink():
        raise ValueError("Symbolic-link content cannot be compared safely.")
    if root.is_file():
        blob = _asset_diff_read_file(root)
        return {root.name: blob}, blob.truncated

    files: dict[str, _AssetDiffBlob] = {}
    truncated = False
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not (current / name).is_symlink()
            and (
                include_excluded
                or not is_resource_path_excluded((current / name).relative_to(root))
            )
        )
        for name in sorted(filenames):
            item = current / name
            relative = item.relative_to(root)
            if (
                not item.is_file()
                or item.is_symlink()
                or (not include_excluded and is_resource_path_excluded(relative))
            ):
                continue
            if len(files) >= ASSET_DIFF_MAX_FILES:
                return files, True
            blob = _asset_diff_read_file(item)
            files[relative.as_posix()] = blob
            truncated = truncated or blob.truncated
    return files, truncated


def _asset_diff_read_file(path: Path) -> _AssetDiffBlob:
    size = path.stat().st_size
    with path.open("rb") as handle:
        data = handle.read(ASSET_DIFF_MAX_FILE_BYTES + 1)
    truncated = len(data) > ASSET_DIFF_MAX_FILE_BYTES
    if truncated:
        data = data[:ASSET_DIFF_MAX_FILE_BYTES]
    return _AssetDiffBlob(data=data, size=size, truncated=truncated)


def _asset_diff_file(
    path: str,
    status: Literal["added", "deleted", "modified"],
    remote_blob: _AssetDiffBlob | None,
    local_blob: _AssetDiffBlob | None,
) -> AssetDiffFile:
    remote_data = remote_blob.data if remote_blob is not None else b""
    local_data = local_blob.data if local_blob is not None else b""
    binary = _asset_diff_is_binary(remote_data) or _asset_diff_is_binary(local_data)
    truncated = bool(
        (remote_blob and remote_blob.truncated) or (local_blob and local_blob.truncated)
    )
    if binary:
        return AssetDiffFile(
            path=path,
            status=status,
            diff="",
            binary=True,
            truncated=truncated,
        )

    remote_text = remote_data.decode("utf-8")
    local_text = local_data.decode("utf-8")
    lines = list(
        difflib.unified_diff(
            remote_text.splitlines(),
            local_text.splitlines(),
            fromfile=f"remote/{path}",
            tofile=f"local/{path}",
            lineterm="",
        )
    )
    diff = "\n".join(lines)
    if not diff and remote_data != local_data:
        diff = "No visible line changes; encoding or line endings differ."
    if len(diff) > ASSET_DIFF_MAX_FILE_CHARS:
        diff = diff[:ASSET_DIFF_MAX_FILE_CHARS]
        truncated = True
    return AssetDiffFile(
        path=path,
        status=status,
        diff=diff,
        truncated=truncated,
    )


def _asset_diff_is_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _discover_inventory_environment(
    cfg: Config,
    *,
    scan_global: bool,
    project_ids: list[str] | None,
) -> EnvDiscoveryResult:
    if (
        cfg.plugin_projects
        or not scan_global
        or project_ids is not None
        or _requires_configured_resource_discovery(cfg)
    ):
        return discover_environment(
            config=cfg,
            scan_global=scan_global,
            project_ids=project_ids,
        )
    # Preserve the public zero-argument discovery seam used by existing
    # integrations while configured scan filters remain opt-in.
    return discover_environment()


def _requires_configured_resource_discovery(cfg: Config) -> bool:
    tool_ids = [profile.effective_tool_id for profile in cfg.platforms.enabled()]
    if len(tool_ids) != len(set(tool_ids)):
        return True
    for profile in cfg.platforms.enabled():
        if profile.name != profile.effective_tool_id:
            return True
        adapter = tool_adapter_by_id(profile.effective_tool_id)
        if adapter is not None:
            if profile.settings_path != adapter.settings_path:
                return True
            if profile.home_dir not in {"", "~"}:
                return True
        configured_paths = (
            (
                profile.skills_path(),
                adapter.skills_dir if adapter is not None else "",
                "dir",
            ),
            (
                profile.rules_path(),
                adapter.rules_dir if adapter is not None else "",
                "dir",
            ),
            (
                profile.prompts_path(),
                adapter.prompts_dir if adapter is not None else "",
                "dir",
            ),
            (
                profile.mcp_json_path(),
                adapter.mcp_json if adapter is not None else "",
                "file",
            ),
            (
                profile.plugins_path(),
                adapter.plugins_dir if adapter is not None else "",
                "dir",
            ),
            (
                profile.instructions_file(),
                adapter.instructions_path if adapter is not None else "",
                "file",
            ),
            (
                profile.memories_path(),
                adapter.memories_dir if adapter is not None else "",
                "dir",
            ),
        )
        for configured_path, default_value, path_type in configured_paths:
            if configured_path is None or not (
                configured_path.is_file() if path_type == "file" else configured_path.is_dir()
            ):
                continue
            default_path = Path(default_value).expanduser() if default_value else None
            if default_path is None or os.path.normcase(
                str(configured_path.absolute())
            ) != os.path.normcase(str(default_path.absolute())):
                return True
    return False


def _aggregate_resource_rows(inventory: AssetInventory) -> list[AssetResourceRow]:
    grouped: dict[str, list[AssetPlatformRow]] = {}
    for row in inventory.rows:
        grouped.setdefault(row.resource_key, []).append(row)

    resources: list[AssetResourceRow] = []
    for resource_key, rows in grouped.items():
        exemplar = rows[0]
        entry = next((row.entry for row in rows if row.entry is not None), None)
        local_rows = [row for row in rows if row.local_exists]
        local_marketplaces = _unique_strings(
            [row.plugin_marketplace for row in local_rows if row.plugin_marketplace]
        )
        local_marketplace_sources = _unique_strings(
            [row.plugin_marketplace_source for row in local_rows if row.plugin_marketplace_source]
        )
        local_instances = [_local_instance_summary(row) for row in local_rows]
        local_descriptions = _unique_strings(
            [item.description for item in local_instances if item.description]
        )
        remote_description = str(getattr(entry, "description", "") or "")
        description = remote_description or (local_descriptions[0] if local_descriptions else "")
        description_source = "remote" if remote_description else "local" if description else "none"
        remote_exists = entry is not None
        read_only = bool(entry and not _is_private_repo_asset(entry))
        remote_status = (
            "unavailable"
            if not inventory.remote_available
            else "read-only"
            if read_only
            else "present"
            if remote_exists
            else "missing"
        )
        local_status = _aggregate_local_status(local_rows, scanned=inventory.scanned_local)
        status = _aggregate_asset_status(
            rows,
            scanned=inventory.scanned_local,
            remote_available=inventory.remote_available,
            remote_exists=remote_exists,
            local_status=local_status,
        )
        metadata_differences = _unique_strings(
            [item for row in rows for item in row.metadata_differences]
        )
        if remote_description and any(
            item and item != remote_description for item in local_descriptions
        ):
            metadata_differences = _unique_strings([*metadata_differences, "description"])
        diff_summary_refs = _aggregate_diff_summary_refs(
            status,
            local_status,
            remote_status,
        )
        resources.append(
            AssetResourceRow(
                resource_key=resource_key,
                kind=exemplar.kind,
                name=exemplar.name,
                description=description,
                description_source=description_source,
                local_status=local_status,
                remote_status=remote_status,
                status=status,
                remote=AssetRemoteState(
                    exists=remote_exists,
                    status=remote_status,
                    writable=bool(entry and _is_private_repo_asset(entry)),
                    read_only=read_only,
                    commit=inventory.remote_commit,
                    path=next(
                        (row.remote_path for row in rows if row.remote_path is not None),
                        None,
                    ),
                    description=remote_description,
                ),
                local_instances=local_instances,
                metadata_differences=metadata_differences,
                diff_summary=fallback_text(diff_summary_refs),
                diff_summary_refs=diff_summary_refs,
                warnings=_unique_strings([item for row in rows for item in row.warnings]),
                warning_refs=_unique_message_refs(
                    [item for row in rows for item in row.warning_refs]
                ),
                blockers=_unique_strings([item for row in rows for item in row.blockers]),
                blocker_refs=_unique_message_refs(
                    [item for row in rows for item in row.blocker_refs]
                ),
                available_actions=_unique_strings(
                    [item for row in rows for item in row.available_actions]
                ),
                plugin_track=(
                    entry.plugin.track if entry and entry.plugin else exemplar.plugin_track
                ),
                plugin_platform=(
                    entry.plugin.platform
                    if entry and entry.plugin
                    else exemplar.platform
                    if exemplar.kind == "plugin"
                    else ""
                ),
                plugin_id=(
                    entry.plugin.plugin_id if entry and entry.plugin else exemplar.plugin_id
                ),
                plugin_source_kind=(
                    entry.plugin.origin.type
                    if entry and entry.plugin
                    else exemplar.plugin_source_kind
                ),
                plugin_source_id=(
                    _plugin_origin_source_id(entry.plugin.origin)
                    if entry and entry.plugin
                    else exemplar.plugin_source_id
                ),
                plugin_marketplace=(
                    entry.plugin.origin.marketplace
                    if entry and entry.plugin and entry.plugin.origin.type == "marketplace"
                    else local_marketplaces[0]
                    if len(local_marketplaces) == 1
                    else ""
                ),
                plugin_marketplace_source=(
                    entry.plugin.origin.source
                    if entry and entry.plugin and entry.plugin.origin.type == "marketplace"
                    else local_marketplace_sources[0]
                    if len(local_marketplace_sources) == 1
                    else ""
                ),
                plugin_selector=(
                    entry.plugin.origin.selector
                    if entry and entry.plugin
                    else exemplar.plugin_selector
                ),
                plugin_observed_version=(
                    entry.plugin.observed_version
                    if entry and entry.plugin
                    else exemplar.plugin_observed_version
                ),
            )
        )
    resources.sort(key=lambda item: (item.kind, item.name))
    return resources


def _unknown_registry_resource(
    entry: RegistryItem,
    inventory: AssetInventory,
    snapshot_root: Path,
) -> AssetResourceRow:
    """Expose portable unknown kinds without allowing CC Port write operations."""
    available = inventory.remote_available
    warning_ref = ui_message(
        "asset.warning.unsupported_registry_kind",
        f"Resource kind {entry.kind!r} is not supported by CC Port and is read-only.",
        kind=entry.kind,
    )
    remote_status = "read-only" if available else "unavailable"
    diff_summary_ref = ui_message(
        "asset.platform_diff.read_only_reference",
        "The item is tracked as a portable read-only registry resource.",
    )
    return AssetResourceRow(
        resource_key=entry.resource_key,
        kind=entry.kind,
        name=entry.name,
        description="",
        description_source="none",
        local_status="not-scanned" if not inventory.scanned_local else "missing",
        remote_status=remote_status,
        status="read-only-reference" if available else "uncomparable",
        remote=AssetRemoteState(
            exists=True,
            status=remote_status,
            writable=False,
            read_only=True,
            commit=inventory.remote_commit,
            path=_remote_content_path(snapshot_root, entry),
            description="",
        ),
        local_instances=[],
        diff_summary=[diff_summary_ref.fallback],
        diff_summary_refs=[diff_summary_ref],
        warnings=[warning_ref.fallback],
        warning_refs=[warning_ref],
        available_actions=[],
    )


def _hydrate_remote_metadata(root: Path, entry: RegistryItem) -> None:
    """Derive display/install metadata from current content without persisting it."""
    if entry.kind not in RESOURCE_PARENT_BY_KIND:
        return
    content_path = _remote_content_path(root, entry)
    if content_path is None:
        return
    mcp_config = _read_portable_mcp_resource(content_path) if entry.kind == "mcp" else None
    derived = _derive_metadata(entry.kind, content_path, mcp_config=mcp_config)
    for field_name in DERIVED_METADATA_FIELDS:
        value = derived.get(field_name)
        if value not in (None, ""):
            setattr(entry, field_name, str(value))
    if entry.kind == "mcp" and isinstance(derived.get("mcp_config"), dict):
        entry.mcp_config = derived["mcp_config"]


def _local_instance_summary(row: AssetPlatformRow) -> AssetLocalInstance:
    description = ""
    if row.plugin_data:
        description = str(row.plugin_data.get("description") or "")
    elif row.local_content_path is not None or row.local_path is not None:
        content_path = row.local_content_path or row.local_path
        assert content_path is not None
        metadata = _derive_metadata(
            row.kind,
            content_path,
            mcp_config=_read_mcp_server(content_path, row.install_name)
            if row.kind == "mcp"
            else None,
        )
        description = str(metadata.get("description") or "")
    return AssetLocalInstance(
        id=row.local_instance_id,
        platform=row.platform,
        install_name=row.install_name,
        path=row.local_path,
        ownership=row.ownership,
        fingerprint=row.local_fingerprint,
        description=description,
        status=row.status,
        warnings=list(row.warnings),
        warning_refs=list(row.warning_refs),
        blockers=list(row.blockers),
        blocker_refs=list(row.blocker_refs),
        track=row.plugin_track,
        scope=row.plugin_scope,
        project_id=row.plugin_project_id,
        source_kind=row.plugin_source_kind,
        source_id=row.plugin_source_id,
        selector=row.plugin_selector,
        observed_version=row.plugin_observed_version,
        enabled=row.plugin_enabled,
        writable=row.plugin_writable,
        content_path=row.local_content_path,
        path_kind=row.path_kind,
        link_health=row.link_health,
        link_target=row.link_target,
        reparse_tag=row.reparse_tag,
        link_target_trusted=row.link_target_trusted,
        tool_id=row.tool_id,
        environment_kind=row.environment_kind,
        environment_name=row.environment_name,
        display_name=row.display_name,
        memory_layout=row.memory_layout,
    )


def _aggregate_local_status(rows: list[AssetPlatformRow], *, scanned: bool) -> str:
    if not scanned:
        return "unknown"
    if not rows:
        return "missing"
    if all(row.plugin_track == "reference" for row in rows):
        return "single" if len(rows) == 1 else "identical-copies"
    fingerprints = {row.local_fingerprint for row in rows if row.local_fingerprint}
    if len(rows) == 1:
        return "single"
    if any(not row.local_fingerprint for row in rows):
        return "variants"
    if len(fingerprints) <= 1:
        return "identical-copies"
    return "variants"


def _aggregate_asset_status(
    rows: list[AssetPlatformRow],
    *,
    scanned: bool,
    remote_available: bool,
    remote_exists: bool,
    local_status: str,
) -> AssetStatus:
    if not scanned or not remote_available:
        return "uncomparable"
    local_exists = local_status not in {"unknown", "missing"}
    if local_exists and not remote_exists:
        return "local-only"
    if remote_exists and not local_exists:
        return "remote-only"
    statuses = {row.status for row in rows if row.local_exists}
    if "target-conflict" in statuses:
        return "target-conflict"
    if local_status == "variants" or "content-different" in statuses:
        return "content-different"
    if "metadata-only" in statuses:
        return "metadata-only"
    if local_exists and remote_exists and statuses and statuses <= {"same"}:
        return "same"
    return "uncomparable"


def _aggregate_diff_summary(
    status: AssetStatus,
    local_status: str,
    remote_status: str,
) -> list[str]:
    return fallback_text(_aggregate_diff_summary_refs(status, local_status, remote_status))


def _aggregate_diff_summary_refs(
    status: AssetStatus,
    local_status: str,
    remote_status: str,
) -> list[UiMessageRef]:
    if local_status == "unknown":
        return [
            ui_message(
                "asset.diff.local_not_scanned",
                "Local assets have not been scanned yet.",
            )
        ]
    if status == "local-only":
        return [
            ui_message(
                "asset.diff.local_only",
                "Local content is not present in the remote repository.",
            )
        ]
    if status == "remote-only":
        return [
            ui_message(
                "asset.diff.remote_only",
                "Remote content is not installed in any scanned local tool.",
            )
        ]
    if status == "same":
        return [
            ui_message(
                "asset.diff.same",
                "Local and remote content fingerprints match.",
            )
        ]
    if status == "content-different":
        return [
            ui_message(
                "asset.diff.content_different",
                "Local and remote content differ, or multiple local variants exist.",
            )
        ]
    if status == "metadata-only":
        return [
            ui_message(
                "asset.diff.metadata_only",
                "Content matches but metadata differs.",
            )
        ]
    if status == "target-conflict":
        return [
            ui_message(
                "asset.diff.target_conflict",
                "Multiple resources resolve to the same local target.",
            )
        ]
    if remote_status == "unavailable":
        return [
            ui_message(
                "asset.diff.remote_unavailable",
                "The current remote state is unavailable; no absence is inferred.",
            )
        ]
    return [
        ui_message(
            "asset.diff.uncomparable",
            "The resource cannot be compared safely.",
        )
    ]


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
    link_target_confirmed: bool = False,
    config: Config | None = None,
    _remote_snapshot: RemoteSnapshot | None = None,
    _inventory: AssetInventory | None = None,
    _persist: bool = True,
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
    snapshot = _remote_snapshot or _refresh_remote_snapshot(cfg, refresh=True)
    inventory = _inventory or build_asset_inventory(
        config=cfg,
        scan_local=True,
        refresh_remote=False,
        remote_snapshot=snapshot,
    )
    registry = snapshot.registry or Registry()
    key = ResourceKey(kind=kind, name=name)
    candidates = [
        row for row in inventory.rows if row.resource_key == str(key) and row.platform == platform
    ]
    if local_instance_id:
        candidates = [row for row in candidates if row.local_instance_id == local_instance_id]
    row = _select_plan_row(candidates, action=action, local_instance_id=local_instance_id)
    blockers: list[str] = list(row.blockers)
    blocker_refs = list(row.blocker_refs)
    warnings = list(row.warnings)
    warning_refs = list(row.warning_refs)
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
            _append_message(
                blockers,
                blocker_refs,
                ui_message("asset.blocker.new_name_required", "A new resource name is required."),
            )
        elif not ITEM_NAME_RE.match(normalized_new_name):
            _append_message(
                blockers,
                blocker_refs,
                ui_message(
                    "asset.blocker.new_name_invalid",
                    "The new resource name must use lowercase letters, digits, and hyphens.",
                ),
            )
        else:
            target_key = str(ResourceKey(kind=kind, name=normalized_new_name))

    if action == "download":
        _extend_messages(
            blockers,
            blocker_refs,
            _download_plan_blocker_refs(row, overwrite_unmanaged),
        )
    elif action == "upload":
        _extend_messages(
            blockers,
            blocker_refs,
            _upload_plan_blocker_refs(row, link_target_confirmed),
        )
        if kind in {"instruction", "memory"} and not row.tool_id:
            blockers.append(
                "The local source tool identity is unavailable; portable tool binding cannot be inferred safely."
            )
        if kind == "memory" and row.tool_id and row.tool_id != "claude-code":
            blockers.append("Memory resources can only originate from Claude Code.")
    elif action == "copy-to-local":
        _extend_messages(
            blockers,
            blocker_refs,
            _copy_to_local_blocker_refs(row, registry, normalized_new_name),
        )
        copy_profile = cfg.platforms.get(row.platform)
        if (
            kind == "memory"
            and copy_profile is not None
            and copy_profile.memory_layout == "projects"
            and normalized_new_name not in copy_profile.memory_install_names
        ):
            _append_message(
                blockers,
                blocker_refs,
                ui_message(
                    "asset.blocker.memory_target_mapping_required",
                    "Map the new memory resource name to an exact local Claude project slot before copying.",
                ),
            )
        target_path, target_exists, target_fingerprint, target_managed = _copy_local_target_state(
            cfg,
            row,
            normalized_new_name,
        )
        if target_exists:
            _append_message(
                blockers,
                blocker_refs,
                ui_message(
                    "asset.blocker.local_target_exists",
                    "The new local name already resolves to an existing target.",
                ),
            )
        if normalized_new_name and any(
            item.local_exists
            and item.resource_key == target_key
            and item.local_instance_id != row.local_instance_id
            for item in inventory.rows
        ):
            _append_message(
                blockers,
                blocker_refs,
                ui_message(
                    "asset.blocker.local_instance_exists",
                    "The new local name already exists as another local instance.",
                ),
            )
    elif action == "copy-to-remote":
        _extend_messages(
            blockers,
            blocker_refs,
            _copy_to_remote_blocker_refs(
                row,
                registry,
                normalized_new_name,
                link_target_confirmed,
            ),
        )
        if kind in {"instruction", "memory"} and not row.tool_id:
            blockers.append(
                "The local source tool identity is unavailable; portable tool binding cannot be inferred safely."
            )
        if kind == "memory" and row.tool_id and row.tool_id != "claude-code":
            blockers.append("Memory resources can only originate from Claude Code.")
        target_entry = registry.get(normalized_new_name, kind) if normalized_new_name else None
        remote_target_exists = target_entry is not None
        remote_target_fingerprint = (
            _remote_asset_fingerprint(snapshot.root, target_entry)
            if target_entry is not None
            else ""
        )
    else:
        if kind == "memory":
            _append_message(
                blockers,
                blocker_refs,
                ui_message(
                    "asset.blocker.memory_install_name_local_only",
                    "Memory project-slot mappings are local profile configuration and cannot be stored remotely.",
                ),
            )
        else:
            _extend_messages(
                blockers,
                blocker_refs,
                _install_alias_plan_blocker_refs(
                    row,
                    registry,
                    cfg,
                    normalized_install_name,
                ),
            )

    if action in REMOTE_WRITE_ACTIONS:
        if not snapshot.available:
            blockers.append(
                snapshot.warning
                or "The configured remote branch is unavailable; remote writes are blocked."
            )
            if snapshot.warning_ref:
                blocker_refs.append(snapshot.warning_ref)
            elif not snapshot.warning:
                blocker_refs.append(
                    ui_message(
                        "asset.blocker.remote_unavailable",
                        "The configured remote branch is unavailable; remote writes are blocked.",
                    )
                )
        legacy_blocker_ref = _legacy_write_blocker_message(cfg, fetch=True)
        if legacy_blocker_ref:
            _append_message(blockers, blocker_refs, legacy_blocker_ref)
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
            _append_message(
                warnings,
                warning_refs,
                ui_message(
                    "asset.warning.identical_remote_content",
                    "Identical content already exists under: " + ", ".join(duplicates),
                    resource_keys=", ".join(duplicates),
                ),
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
        source_path=row.local_path,
        source_content_path=row.local_content_path or row.local_path,
        source_path_kind=row.path_kind,
        source_link_health=row.link_health,
        source_link_target=row.link_target,
        source_reparse_tag=row.reparse_tag,
        link_target_confirmed=link_target_confirmed,
        overwrite_unmanaged=overwrite_unmanaged,
        new_name=normalized_new_name,
        new_install_name=normalized_install_name,
        tool_id=row.tool_id,
        environment_kind=row.environment_kind,
        environment_name=row.environment_name,
        warnings=_unique_strings(warnings),
        warning_refs=_unique_message_refs(warning_refs),
        blockers=_unique_strings(blockers),
        blocker_refs=_unique_message_refs(blocker_refs),
        blocked=bool(blockers),
        created_at=_utc_now(),
        plugin_data=dict(row.plugin_data),
        remote_repo_hash=_remote_repo_hash(snapshot.repo_url),
        remote_branch=snapshot.branch,
    )
    if _persist:
        _save_asset_plan(plan, cfg)
    return plan


def apply_asset_action_plan(
    operation_id: str,
    *,
    config: Config | None = None,
) -> AssetActionResult:
    """Revalidate and apply one persisted asset action plan."""
    cfg = config or load_config()
    _assert_private_asset_boundaries(cfg)
    existing = _load_asset_result(operation_id, cfg)
    if existing is not None:
        return existing
    plan = load_asset_action_plan(operation_id, config=cfg)
    if plan.blocked:
        blocked_message = "; ".join(plan.blockers) or "The asset action plan is blocked."
        result = AssetActionResult(
            operation_id=plan.operation_id,
            action=plan.action,
            status="blocked",
            resource_key=plan.resource_key,
            target_resource_key=plan.target_resource_key,
            platform=plan.platform,
            message=blocked_message,
            message_ref=ui_message(
                "asset.result.plan_blocked",
                blocked_message,
                detail="; ".join(plan.blockers),
            ),
            warnings=plan.warnings,
            warning_refs=plan.warning_refs,
        )
        _save_asset_result(result, cfg)
        return result

    current_snapshot = _refresh_remote_snapshot(cfg, refresh=True)
    current_repo_hash = _remote_repo_hash(current_snapshot.repo_url)
    current_branch = current_snapshot.branch
    if plan.remote_repo_hash != current_repo_hash or plan.remote_branch != current_branch:
        result = AssetActionResult(
            operation_id=plan.operation_id,
            action=plan.action,
            status="stale-plan",
            resource_key=plan.resource_key,
            target_resource_key=plan.target_resource_key,
            platform=plan.platform,
            message="The configured remote repository identity changed after planning.",
            message_ref=ui_message(
                "asset.result.stale",
                "The configured remote repository identity changed after planning.",
                detail="remote repository identity changed",
            ),
            warnings=plan.warnings,
            warning_refs=plan.warning_refs,
        )
        _save_asset_result(result, cfg)
        return result

    try:
        if plan.action in LOCAL_WRITE_ACTIONS:
            result = _apply_local_asset_action(plan, cfg)
        else:
            result = _apply_remote_asset_action(plan, cfg)
    except _StaleAssetTarget as exc:
        detail = str(exc)
        result = AssetActionResult(
            operation_id=plan.operation_id,
            action=plan.action,
            status=exc.code,
            resource_key=plan.resource_key,
            target_resource_key=plan.target_resource_key,
            platform=plan.platform,
            message=detail,
            message_ref=ui_message(
                "asset.result.stale",
                detail,
                detail=detail,
            ),
            warnings=plan.warnings,
            warning_refs=plan.warning_refs,
        )
    _save_asset_result(result, cfg)
    return result


def build_asset_batch_plan(
    direction: str,
    *,
    resource_keys: list[str],
    target_platforms: list[str] | None = None,
    choices: list[AssetBatchChoice] | None = None,
    config: Config | None = None,
) -> AssetBatchPlan:
    """Build a stateless, revalidatable plan for selected logical resources."""
    normalized_direction = direction.strip().lower()
    if normalized_direction not in {"upload", "download"}:
        raise ValueError("Batch direction must be 'upload' or 'download'.")
    keys = list(dict.fromkeys(item.strip() for item in resource_keys if item.strip()))
    platforms = list(
        dict.fromkeys(item.strip() for item in (target_platforms or []) if item.strip())
    )
    cfg = config or load_config()
    snapshot = _refresh_remote_snapshot(cfg, refresh=True)
    inventory = build_asset_inventory(
        config=cfg,
        scan_local=True,
        refresh_remote=False,
        remote_snapshot=snapshot,
    )
    rows_by_key: dict[str, list[AssetPlatformRow]] = {}
    for row in inventory.rows:
        rows_by_key.setdefault(row.resource_key, []).append(row)
    resources_by_key = {item.resource_key: item for item in inventory.resources}
    choice_items = choices or []
    choice_map = {(item.resource_key, item.platform): item for item in choice_items}
    items: list[AssetBatchPlanItem] = []
    for resource_key in keys:
        rows = rows_by_key.get(resource_key, [])
        if normalized_direction == "upload":
            upload_choices = [
                choice for choice in choice_items if choice.resource_key == resource_key
            ]
            if len(upload_choices) > 1:
                items.extend(
                    _build_batch_upload_item(
                        resource_key,
                        rows,
                        choice,
                        cfg=cfg,
                        snapshot=snapshot,
                        inventory=inventory,
                    )
                    for choice in upload_choices
                )
            else:
                items.append(
                    _build_batch_upload_item(
                        resource_key,
                        rows,
                        upload_choices[0] if upload_choices else None,
                        cfg=cfg,
                        snapshot=snapshot,
                        inventory=inventory,
                    )
                )
            continue
        if not platforms:
            items.append(
                _batch_non_action_item(
                    resource_key,
                    action="download",
                    disposition="blocked",
                    reason="Select at least one target platform.",
                    reason_ref=ui_message(
                        "asset.batch.select_target_platform",
                        "Select at least one target platform.",
                    ),
                )
            )
            continue
        for platform in platforms:
            download_choice = choice_map.get((resource_key, platform)) or choice_map.get(
                (resource_key, "")
            )
            content_instances = [
                row
                for row in rows
                if row.platform == platform
                and row.local_locator == "plugin-expected"
                and row.entry is not None
                and row.entry.plugin is not None
                and row.entry.plugin.track == "content"
            ]
            selected_instances = content_instances if len(content_instances) > 1 else [None]
            for instance in selected_instances:
                scoped_choice = download_choice
                if instance is not None:
                    scoped_choice = (
                        download_choice
                        if download_choice is not None
                        and download_choice.local_instance_id == instance.local_instance_id
                        else AssetBatchChoice(
                            resource_key=resource_key,
                            platform=platform,
                            local_instance_id=instance.local_instance_id,
                            resolution=download_choice.resolution
                            if download_choice
                            else "overwrite",
                            new_name=download_choice.new_name if download_choice else "",
                            overwrite_unmanaged=(
                                download_choice.overwrite_unmanaged if download_choice else False
                            ),
                        )
                    )
                items.append(
                    _build_batch_download_item(
                        resource_key,
                        platform,
                        rows,
                        scoped_choice,
                        cfg=cfg,
                        snapshot=snapshot,
                        inventory=inventory,
                    )
                )
    _block_duplicate_batch_targets(items, direction=normalized_direction)
    plan = AssetBatchPlan(
        direction=normalized_direction,
        resource_keys=keys,
        target_platforms=platforms,
        remote_commit=snapshot.commit,
        plan_hash="",
        items=items,
        executable_count=sum(
            item.plan is not None
            and not item.blockers
            and item.disposition in {"create", "update", "rename"}
            for item in items
        ),
        blocked_count=sum(item.disposition == "blocked" for item in items),
        skipped_count=sum(item.disposition in {"skip", "unchanged"} for item in items),
        checked_resources=[
            AssetBatchResourceCheck(
                resource_key=resource_key,
                local_status=resources_by_key[resource_key].local_status,
                remote_status=resources_by_key[resource_key].remote_status,
                status=resources_by_key[resource_key].status,
                local_instances=list(resources_by_key[resource_key].local_instances),
            )
            for resource_key in keys
            if resource_key in resources_by_key
        ],
        remote_repo_hash=_remote_repo_hash(snapshot.repo_url),
        remote_branch=snapshot.branch,
    )
    plan.plan_hash = _asset_batch_plan_hash(plan)
    return plan


def apply_asset_batch_plan(
    direction: str,
    *,
    resource_keys: list[str],
    expected_plan_hash: str,
    target_platforms: list[str] | None = None,
    choices: list[AssetBatchChoice] | None = None,
    config: Config | None = None,
) -> AssetBatchResult:
    """Rebuild and apply a batch plan only when its normalized hash is current."""
    cfg = config or load_config()
    current = build_asset_batch_plan(
        direction,
        resource_keys=resource_keys,
        target_platforms=target_platforms,
        choices=choices,
        config=cfg,
    )
    if not expected_plan_hash or current.plan_hash != expected_plan_hash:
        return AssetBatchResult(
            status="stale-plan",
            plan_hash=current.plan_hash,
            results=[],
            stale_plan=current,
        )
    executable = [
        item.plan
        for item in current.items
        if item.plan is not None
        and not item.blockers
        and item.disposition in {"create", "update", "rename"}
    ]
    passive = [
        _batch_passive_result(item)
        for item in current.items
        if item.plan is None or item.disposition not in {"create", "update", "rename"}
    ]
    if current.direction == "upload":
        applied = _apply_remote_asset_batch(executable, cfg)
    else:
        applied = []
        for plan in executable:
            try:
                applied.append(_apply_local_asset_action(plan, cfg))
            except _StaleAssetTarget as exc:
                applied.append(_batch_error_result(plan, exc.code, str(exc)))
            except Exception as exc:  # noqa: BLE001 - continue independent local transactions
                applied.append(_batch_error_result(plan, "failed", str(exc)))
    results = [*passive, *applied]
    failures = [
        item for item in results if item.status not in {"succeeded", "unchanged", "skipped"}
    ]
    successes = [item for item in results if item.status in {"succeeded", "unchanged"}]
    needs_action = [item for item in results if item.status == "needs-action"]
    hard_failures = [item for item in failures if item.status != "needs-action"]
    partial_results = [item for item in results if item.status == "partial"]
    status = (
        "partial"
        if partial_results or successes and failures
        else "failed"
        if hard_failures
        else "needs-action"
        if needs_action
        else "succeeded"
    )
    return AssetBatchResult(status=status, plan_hash=current.plan_hash, results=results)


def add_plugin_reference(
    *,
    platform: str,
    plugin_id: str,
    origin_type: str,
    scope: str = "user",
    enabled: bool = True,
    marketplace: str = "",
    source: str = "",
    package: str = "",
    repo: str = "",
    selector: str = "",
    observed_version: str = "",
    project_id: str = "",
    name: str = "",
    description: str = "",
    push: bool = True,
    config: Config | None = None,
) -> PluginReferenceResult:
    """Add or merge one reference plugin without copying local plugin files."""
    cfg = config or load_config()
    project_identity: PluginProjectIdentity | None = None
    if scope in {"project", "local"}:
        project = next((item for item in cfg.plugin_projects if item.id == project_id), None)
        if project is None:
            raise ValueError("Project/local plugin references require a saved project mapping.")
        if not project.repo:
            raise ValueError("Projects without a Git remote are observation-only.")
        project_identity = PluginProjectIdentity(repo=project.repo, subdir=project.subdir)
    elif project_id:
        raise ValueError("User/managed plugin references must not include a project id.")
    origin = PluginOrigin(
        type=origin_type,
        marketplace=marketplace,
        source=source,
        package=package,
        repo=repo,
        selector=selector,
    )
    installation = PluginInstallation(
        scope=scope,
        enabled=enabled,
        project=project_identity,
    )
    spec = PluginSpec(
        track="reference",
        platform=platform,
        plugin_id=plugin_id,
        origin=origin,
        observed_version=observed_version,
        installations=[installation],
    )
    preferred_name = name.strip()
    if not push:
        root = resource_root(cfg)
        ensure_structure(root)
        registry_path = root / DEFAULT_REGISTRY_FILENAME
        with resource_repo_write_lock(root, timeout_seconds=cfg.state.lock_timeout_seconds):
            registry = load_registry(registry_path)
            key = _plugin_resource_key_for_spec(registry, spec, preferred_name=preferred_name)
            existing = registry.get(key.name, "plugin")
            _mutate_plugin_reference(
                registry,
                registry_path,
                key,
                existing,
                spec,
                description=description,
            )
            entry = registry.get(key.name, "plugin")
        if entry is None:  # pragma: no cover - guarded by mutation
            raise AssetSyncError("Plugin reference was not recorded.")
        return PluginReferenceResult("saved", str(key), entry, pushed=False)

    blocker = _legacy_write_blocker(cfg, fetch=True)
    if blocker:
        raise AssetSyncError(blocker)
    repo_url = _configured_remote_url(cfg)
    if not repo_url:
        raise AssetSyncError("No remote resource repository URL is configured.")
    last_error: Exception | None = None
    for attempt in range(2):
        with tempfile.TemporaryDirectory(
            prefix="cc-port-plugin-reference-", ignore_cleanup_errors=True
        ) as temporary:
            worktree = Path(temporary) / "repo"
            _clone_remote_for_write(repo_url, worktree, cfg)
            registry_path = worktree / DEFAULT_REGISTRY_FILENAME
            if not registry_path.is_file():
                ensure_structure(worktree)
            registry = load_registry(registry_path)
            key = _plugin_resource_key_for_spec(registry, spec, preferred_name=preferred_name)
            existing = registry.get(key.name, "plugin")
            changed = _mutate_plugin_reference(
                registry,
                registry_path,
                key,
                existing,
                spec,
                description=description,
            )
            entry = registry.get(key.name, "plugin")
            if entry is None:  # pragma: no cover - guarded by mutation
                raise AssetSyncError("Plugin reference was not recorded.")
            if not changed:
                return PluginReferenceResult(
                    "unchanged",
                    str(key),
                    entry,
                    remote_commit=git_ops.head_commit(worktree) or "",
                    pushed=False,
                )
            commit_resource_changes_unlocked(
                worktree,
                message=f"cc-port: save plugin reference {key.name}",
                config=cfg,
            )
            committed = git_ops.head_commit(worktree) or ""
            try:
                git_ops.push(
                    worktree,
                    branch=cfg.resources.branch or "main",
                    token=resource_repo_auth_token(cfg),
                )
            except git_ops.GitError as exc:
                last_error = exc
                if attempt == 0:
                    continue
                raise
            return PluginReferenceResult(
                "succeeded",
                str(key),
                entry,
                remote_commit=committed,
                pushed=True,
            )
    raise AssetSyncError(str(last_error or "Plugin reference push failed."))


def _plugin_resource_key_for_spec(
    registry: Registry,
    spec: PluginSpec,
    *,
    preferred_name: str = "",
) -> ResourceKey:
    existing = next(
        (
            item
            for item in registry.items
            if item.kind == "plugin"
            and item.plugin is not None
            and item.lifecycle == "active"
            and item.plugin.platform == spec.platform
            and item.plugin.plugin_id == spec.plugin_id
            and item.plugin.origin.type == spec.origin.type
            and _plugin_origin_source_id(item.plugin.origin)
            == _plugin_origin_source_id(spec.origin)
        ),
        None,
    )
    if existing is not None:
        return existing.key()
    source_id = (
        f"{spec.plugin_id}@{spec.origin.marketplace}"
        if spec.origin.type == "marketplace"
        else _plugin_origin_source_id(spec.origin)
    )
    base = preferred_name or plugin_resource_name(spec.platform, spec.origin.type, source_id)
    key = ResourceKey(kind="plugin", name=base)
    occupied = registry.get(key.name, "plugin")
    if occupied is None or preferred_name:
        return key
    identity = f"{spec.platform}\0{spec.plugin_id}\0{spec.origin.type}\0{source_id}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    return ResourceKey(kind="plugin", name=f"{base[:55].rstrip('-')}-{digest}")


def build_plugin_delete_plan(
    resource_key: str,
    *,
    selected_instance_ids: list[str] | None = None,
    config: Config | None = None,
) -> PluginDeletePlan:
    """Build an instance-level plugin uninstall plan without changing local or remote state."""
    cfg = config or load_config()
    key = ResourceKey.parse(resource_key)
    if key.kind != "plugin":
        raise ValueError("Plugin deletion only accepts plugin resource keys.")
    snapshot = _refresh_remote_snapshot(cfg, refresh=True)
    if snapshot.registry is None:
        raise ValueError("Plugin deletion is blocked because the remote registry is unavailable.")
    entry = snapshot.registry.get(key.name, "plugin")
    if entry is None or entry.plugin is None:
        raise ValueError("Plugin deletion requires an active plugin entry.")
    inventory = build_asset_inventory(
        config=cfg,
        scan_local=True,
        refresh_remote=False,
        remote_snapshot=snapshot,
    )
    rows = [row for row in inventory.rows if row.resource_key == resource_key]
    local_rows = [
        row
        for row in rows
        if row.local_exists and row.local_locator in {"plugin-adapter", "plugin-expected"}
    ]
    instances: list[PluginDeleteInstancePlan] = []
    for installation in entry.plugin.installations:
        project = installation.project.model_dump(mode="json") if installation.project else None
        local = next(
            (
                row
                for row in local_rows
                if row.plugin_scope == installation.scope
                and row.plugin_data.get("plugin", {}).get("installations", [{}])[0].get("project")
                == project
            ),
            None,
        )
        instances.append(_plugin_delete_instance(entry.plugin, installation, local))
    requested = list(dict.fromkeys(selected_instance_ids or []))
    if not requested:
        requested = [item.id for item in instances if item.selectable]
    known_ids = {item.id for item in instances}
    blockers: list[str] = []
    blocker_refs: list[UiMessageRef] = []
    if not requested:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "plugin.delete.select_instance",
                "Select at least one writable plugin instance.",
            ),
        )
    unknown = [item for item in requested if item not in known_ids]
    if unknown:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "plugin.delete.unknown_instance",
                "Unknown plugin instance selection: " + ", ".join(unknown),
                instance_ids=", ".join(unknown),
            ),
        )
    unselectable = [item.id for item in instances if item.id in requested and not item.selectable]
    if unselectable:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "plugin.delete.managed_instance",
                "Managed plugin instances cannot be selected: " + ", ".join(unselectable),
                instance_ids=", ".join(unselectable),
            ),
        )
    payload = {
        "resource_key": resource_key,
        "remote_commit": snapshot.commit,
        "selected_instance_ids": requested,
        "instances": [_jsonable(asdict(item)) for item in instances],
    }
    plan_hash = _json_fingerprint(payload)
    return PluginDeletePlan(
        resource_key=resource_key,
        remote_commit=snapshot.commit,
        selected_instance_ids=requested,
        instances=instances,
        plan_hash=plan_hash,
        blocked=bool(blockers),
        blockers=blockers,
        blocker_refs=blocker_refs,
    )


def apply_plugin_delete_plan(
    resource_key: str,
    *,
    selected_instance_ids: list[str],
    expected_plan_hash: str,
    config: Config | None = None,
) -> PluginDeleteResult:
    """Apply selected uninstalls and only then remove their desired remote state."""
    cfg = config or load_config()
    current = build_plugin_delete_plan(
        resource_key,
        selected_instance_ids=selected_instance_ids,
        config=cfg,
    )
    if not expected_plan_hash or current.plan_hash != expected_plan_hash:
        return PluginDeleteResult(
            status="stale-plan",
            resource_key=resource_key,
            plan_hash=current.plan_hash,
            results=[],
            stale_plan=current,
        )
    if current.blocked:
        message = "; ".join(current.blockers)
        return PluginDeleteResult(
            status="failed",
            resource_key=resource_key,
            plan_hash=current.plan_hash,
            results=[
                _plugin_delete_action_result(
                    resource_key,
                    "blocked",
                    message,
                    message_ref=ui_message(
                        "plugin.delete.result.blocked",
                        message,
                        detail=message,
                    ),
                )
            ],
        )
    selected = [item for item in current.instances if item.id in set(selected_instance_ids)]
    results = [_apply_plugin_delete_instance(resource_key, item, cfg) for item in selected]
    pending = [item for item in results if item.status not in {"succeeded", "unchanged"}]
    if pending:
        successes = [item for item in results if item.status in {"succeeded", "unchanged"}]
        return PluginDeleteResult(
            status="partial"
            if successes
            else "needs-action"
            if all(item.status == "needs-action" for item in pending)
            else "failed",
            resource_key=resource_key,
            plan_hash=current.plan_hash,
            results=results,
        )
    verification = build_plugin_delete_plan(resource_key, selected_instance_ids=[], config=cfg)
    still_present = {
        item.id
        for item in verification.instances
        if item.id in {selected_item.id for selected_item in selected}
        and item.method != "verified-absent"
    }
    if still_present:
        message = "Instances remain after uninstall: " + ", ".join(sorted(still_present))
        results.append(
            _plugin_delete_action_result(
                resource_key,
                "verification-failed",
                message,
                message_ref=ui_message(
                    "plugin.delete.result.verification_failed",
                    message,
                    instance_ids=", ".join(sorted(still_present)),
                ),
            )
        )
        return PluginDeleteResult(
            status="failed",
            resource_key=resource_key,
            plan_hash=current.plan_hash,
            results=results,
        )
    remote_commit, remote_deleted = _remove_plugin_remote_installations(
        current,
        selected,
        cfg,
    )
    return PluginDeleteResult(
        status="succeeded",
        resource_key=resource_key,
        plan_hash=current.plan_hash,
        results=results,
        remote_deleted=remote_deleted,
        remote_commit=remote_commit,
    )


def _plugin_delete_instance(
    spec: PluginSpec,
    installation: PluginInstallation,
    row: AssetPlatformRow | None,
) -> PluginDeleteInstancePlan:
    installation_data = installation.model_dump(mode="json")
    instance_id = (
        row.local_instance_id
        if row is not None
        else _json_fingerprint({"platform": spec.platform, "installation": installation_data})[:24]
    )
    if installation.scope == "managed":
        method = "managed-policy"
        detail_ref = ui_message(
            "plugin.delete.detail.managed_policy",
            "Organization policy controls this instance; CC Port cannot uninstall it.",
        )
        selectable = False
    elif row is None:
        method = "verified-absent"
        detail_ref = ui_message(
            "plugin.delete.detail.verified_absent",
            "No matching local installation was found; only desired remote state will be removed.",
        )
        selectable = True
    elif (
        spec.platform == "claude-code"
        and spec.track == "reference"
        and spec.origin.type == "marketplace"
        and shutil.which("claude")
    ):
        method = "claude-cli"
        detail_ref = ui_message(
            "plugin.delete.detail.claude_cli",
            f"Run the scope-aware Claude plugin uninstall for {spec.plugin_id}.",
            plugin_id=spec.plugin_id,
        )
        selectable = True
    elif (
        spec.platform == "opencode"
        and spec.origin.type == "npm"
        and row.plugin_data.get("state_path")
    ):
        method = "opencode-config"
        detail_ref = ui_message(
            "plugin.delete.detail.opencode_config",
            "Remove only this npm declaration from opencode.json.",
        )
        selectable = True
    elif spec.track == "content" and row.local_path is not None:
        method = "delete-content"
        detail_ref = ui_message(
            "plugin.delete.detail.delete_content",
            "Transactionally delete this selected local plugin content path.",
        )
        selectable = True
    else:
        method = "manual"
        detail_ref = ui_message(
            "plugin.delete.detail.manual",
            "No stable automatic uninstall entry is available; uninstall manually and rescan.",
        )
        selectable = True
    detail = detail_ref.fallback
    return PluginDeleteInstancePlan(
        id=instance_id,
        platform=spec.platform,
        scope=installation.scope,
        project_id=row.plugin_project_id if row else "",
        enabled=installation.enabled,
        writable=bool(row.plugin_writable) if row else True,
        selectable=selectable,
        method=method,
        detail=detail,
        detail_ref=detail_ref,
        local_path=row.local_path if row else None,
        state_path=Path(str(row.plugin_data.get("state_path")))
        if row and row.plugin_data.get("state_path")
        else None,
        installation=installation_data,
        plugin_id=spec.plugin_id,
        source_id=(
            f"{spec.plugin_id}@{spec.origin.marketplace}"
            if spec.origin.type == "marketplace"
            else spec.origin.package
            if spec.origin.type == "npm"
            else spec.origin.repo
        ),
    )


def _apply_plugin_delete_instance(
    resource_key: str,
    instance: PluginDeleteInstancePlan,
    cfg: Config,
) -> AssetActionResult:
    if instance.method == "managed-policy":
        return _plugin_delete_action_result(
            resource_key,
            "blocked",
            instance.detail,
            instance,
            message_ref=instance.detail_ref,
        )
    if instance.method == "manual":
        return _plugin_delete_action_result(
            resource_key,
            "needs-action",
            instance.detail,
            instance,
            message_ref=instance.detail_ref,
        )
    if instance.method == "verified-absent":
        return _plugin_delete_action_result(
            resource_key,
            "unchanged",
            instance.detail,
            instance,
            message_ref=instance.detail_ref,
        )
    if instance.method == "claude-cli":
        result = subprocess.run(
            [
                "claude",
                "plugin",
                "uninstall",
                instance.source_id or instance.plugin_id,
                "--scope",
                instance.scope,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return _plugin_delete_action_result(
                resource_key,
                "failed",
                (result.stderr or result.stdout or "Claude plugin uninstall failed.").strip(),
                instance,
            )
        message_ref = ui_message(
            "plugin.delete.result.claude_complete",
            "Claude CLI uninstall completed.",
        )
        return _plugin_delete_action_result(
            resource_key,
            "succeeded",
            message_ref.fallback,
            instance,
            message_ref=message_ref,
        )
    target = instance.state_path if instance.method == "opencode-config" else instance.local_path
    if target is None:
        message_ref = ui_message(
            "plugin.delete.result.target_unavailable",
            "The planned local target is unavailable.",
        )
        return _plugin_delete_action_result(
            resource_key,
            "failed",
            message_ref.fallback,
            instance,
            message_ref=message_ref,
        )
    transaction = LocalChangeTransaction.begin(
        "plugin-delete",
        [
            ChangeTarget(
                path=target,
                change_action="plugin-delete",
                resource=resource_key,
                platform=instance.platform,
            )
        ],
        metadata={"resource_key": resource_key, "instance_id": instance.id},
        lock_timeout_seconds=cfg.state.lock_timeout_seconds,
    )
    transaction.mark_attempted([target])
    try:
        if instance.method == "opencode-config":
            payload = json.loads(target.read_text(encoding="utf-8"))
            declared = payload.get("plugin", [])
            if not isinstance(declared, list):
                raise AssetSyncError("OpenCode plugin declarations are not a list.")
            payload["plugin"] = [
                value
                for value in declared
                if _split_plugin_package(str(value))[0] != instance.source_id
            ]
            target.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        else:
            _remove_asset_path(target)
        record = transaction.complete(message=f"Removed plugin instance {instance.id}.")
    except Exception as exc:
        transaction.rollback(str(exc))
        return _plugin_delete_action_result(resource_key, "failed", str(exc), instance)
    message_ref = ui_message(
        "plugin.delete.result.removed",
        f"Local plugin instance removed ({record.status}).",
        status=record.status,
    )
    return _plugin_delete_action_result(
        resource_key,
        "succeeded",
        message_ref.fallback,
        instance,
        message_ref=message_ref,
    )


def _plugin_delete_action_result(
    resource_key: str,
    status: str,
    message: str,
    instance: PluginDeleteInstancePlan | None = None,
    *,
    message_ref: UiMessageRef | None = None,
) -> AssetActionResult:
    return AssetActionResult(
        operation_id="",
        action="plugin-delete",
        status=status,
        resource_key=resource_key,
        target_resource_key=resource_key,
        platform=instance.platform if instance else "",
        message=message,
        message_ref=message_ref,
        local_path=instance.local_path if instance else None,
    )


def _split_plugin_package(value: str) -> tuple[str, str]:
    if value.startswith("@"):
        slash = value.find("/")
        marker = value.find("@", slash + 1) if slash >= 0 else -1
    else:
        marker = value.rfind("@")
    return (value[:marker], value[marker + 1 :]) if marker > 0 else (value, "")


def _remove_plugin_remote_installations(
    plan: PluginDeletePlan,
    selected: list[PluginDeleteInstancePlan],
    cfg: Config,
) -> tuple[str, bool]:
    repo_url = _configured_remote_url(cfg)
    if not repo_url:
        raise AssetSyncError("No remote resource repository URL is configured.")
    selected_identities = {
        json.dumps(item.installation, sort_keys=True, separators=(",", ":")) for item in selected
    }
    with tempfile.TemporaryDirectory(
        prefix="cc-port-plugin-delete-", ignore_cleanup_errors=True
    ) as temporary:
        worktree = Path(temporary) / "repo"
        _clone_remote_for_write(repo_url, worktree, cfg)
        if (git_ops.head_commit(worktree) or "") != plan.remote_commit:
            raise _StaleAssetTarget(
                "stale-target", "The remote registry changed after deletion planning."
            )
        registry_path = worktree / DEFAULT_REGISTRY_FILENAME
        registry = load_registry(registry_path)
        key = ResourceKey.parse(plan.resource_key)
        entry = registry.get(key.name, "plugin")
        if entry is None or entry.plugin is None:
            raise _StaleAssetTarget("stale-target", "The plugin record no longer exists.")
        updated = entry.model_copy(deep=True)
        updated.plugin.installations = [
            item
            for item in updated.plugin.installations
            if json.dumps(item.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            not in selected_identities
        ]
        deleted = not updated.plugin.installations
        if deleted:
            updated.lifecycle = "removed"
            updated.removed_at = _utc_now()
            updated.removed_reason = "All selected plugin instances were uninstalled and verified."
            updated.removed_effect = "index_only"
        registry.upsert(updated)
        save_registry(registry, registry_path)
        commit_resource_changes_unlocked(
            worktree,
            message=f"cc-port: delete plugin instances {key.name}",
            config=cfg,
        )
        committed = git_ops.head_commit(worktree) or ""
        git_ops.push(
            worktree,
            branch=cfg.resources.branch or "main",
            token=resource_repo_auth_token(cfg),
        )
        return committed, deleted


def _build_batch_upload_item(
    resource_key: str,
    rows: list[AssetPlatformRow],
    choice: AssetBatchChoice | None,
    *,
    cfg: Config,
    snapshot: RemoteSnapshot,
    inventory: AssetInventory,
) -> AssetBatchPlanItem:
    physical_local_rows = [row for row in rows if row.local_exists]
    local_rows = [row for row in physical_local_rows if row.local_fingerprint]
    if not local_rows:
        if physical_local_rows:
            blocked_row = sorted(
                physical_local_rows,
                key=lambda row: (row.platform, row.local_instance_id),
            )[0]
            reason_ref = (
                blocked_row.blocker_refs[0]
                if blocked_row.blocker_refs
                else ui_message(
                    "asset.blocker.local_fingerprint_missing",
                    "The local source cannot be fingerprinted safely.",
                )
            )
            return _batch_non_action_item(
                resource_key,
                action="upload",
                disposition="blocked",
                platform=blocked_row.platform,
                local_instance_id=blocked_row.local_instance_id,
                reason=reason_ref.fallback,
                reason_ref=reason_ref,
            )
        return _batch_non_action_item(
            resource_key,
            action="upload",
            disposition="skip",
            reason="No scanned local source exists.",
            reason_ref=ui_message(
                "asset.batch.no_local_source",
                "No scanned local source exists.",
            ),
        )
    selected: AssetPlatformRow | None = None
    reference_rows = [
        row for row in local_rows if row.kind == "plugin" and row.plugin_track == "reference"
    ]
    if reference_rows and len(reference_rows) == len(local_rows):
        selected = sorted(
            reference_rows,
            key=lambda row: (row.platform, row.plugin_scope, row.local_instance_id),
        )[0]
        merged_spec: PluginSpec | None = None
        selector_known = False
        for reference_row in reference_rows:
            incoming = PluginSpec.model_validate(reference_row.plugin_data.get("plugin"))
            incoming_selector_known = bool(reference_row.plugin_data.get("selector_known", True))
            if merged_spec is not None and not incoming_selector_known:
                incoming.origin.selector = merged_spec.origin.selector
            merged_spec = _merge_plugin_installations(merged_spec, incoming)
            selector_known = selector_known or incoming_selector_known
        selected.plugin_data = json.loads(json.dumps(selected.plugin_data))
        selected.plugin_data["plugin"] = merged_spec.model_dump(mode="json") if merged_spec else {}
        selected.plugin_data["selector_known"] = selector_known
    elif choice and choice.local_instance_id:
        selected = next(
            (row for row in local_rows if row.local_instance_id == choice.local_instance_id),
            None,
        )
        if selected is None:
            return _batch_non_action_item(
                resource_key,
                action="upload",
                disposition="blocked",
                reason="The selected local instance no longer exists.",
                reason_ref=ui_message(
                    "asset.batch.local_instance_missing",
                    "The selected local instance no longer exists.",
                ),
            )
    else:
        fingerprint_groups = {row.local_fingerprint for row in local_rows if row.local_fingerprint}
        if len(fingerprint_groups) > 1:
            return _batch_non_action_item(
                resource_key,
                action="upload",
                disposition="blocked",
                reason="Multiple different local versions exist; select a source instance.",
                reason_ref=ui_message(
                    "asset.batch.select_source_instance",
                    "Multiple different local versions exist; select a source instance.",
                ),
            )
        selected = sorted(local_rows, key=lambda row: (row.platform, row.local_instance_id))[0]
    if selected.kind == "plugin":
        if choice and choice.plugin_track == "skip":
            return _batch_non_action_item(
                resource_key,
                action="upload",
                disposition="skip",
                platform=selected.platform,
                local_instance_id=selected.local_instance_id,
                reason="Plugin candidate was explicitly skipped.",
                reason_ref=ui_message(
                    "asset.batch.plugin_skipped",
                    "Plugin candidate was explicitly skipped.",
                ),
            )
        if selected.plugin_track == "content" and (
            not selected.plugin_writable or selected.plugin_scope == "managed"
        ):
            return _batch_non_action_item(
                resource_key,
                action="upload",
                disposition="blocked",
                platform=selected.platform,
                local_instance_id=selected.local_instance_id,
                reason="Managed or read-only plugin content cannot be uploaded.",
                reason_ref=ui_message(
                    "asset.batch.plugin_read_only",
                    "Managed or read-only plugin content cannot be uploaded.",
                ),
            )
        existing_spec = selected.entry.plugin if selected.entry is not None else None
        if selected.plugin_track == "content" and existing_spec is None:
            requested_track = (choice.plugin_track if choice else "").strip()
            if requested_track == "reference":
                converted, conversion_error = _content_plugin_reference_data(selected, choice)
                if conversion_error:
                    return _batch_non_action_item(
                        resource_key,
                        action="upload",
                        disposition="blocked",
                        platform=selected.platform,
                        local_instance_id=selected.local_instance_id,
                        reason=conversion_error,
                    )
                selected.plugin_data = converted
            elif not (choice and choice.ownership_confirmed):
                return _batch_non_action_item(
                    resource_key,
                    action="upload",
                    disposition="blocked",
                    platform=selected.platform,
                    local_instance_id=selected.local_instance_id,
                    reason=(
                        "Confirm that this is owned source content, choose reference with a portable origin, "
                        "or skip it."
                    ),
                    reason_ref=ui_message(
                        "asset.batch.plugin_source_choice_required",
                        "Confirm that this is owned source content, choose reference with a portable origin, "
                        "or skip it.",
                    ),
                )
            elif selected.platform == "opencode":
                selected.plugin_data["plugin"]["dependencies"] = dict(
                    choice.plugin_dependencies if choice else {}
                )
    resolution = choice.resolution if choice else "overwrite"
    new_name = choice.new_name.strip() if choice else ""
    action = "copy-to-remote" if resolution == "rename" else "upload"
    if (
        action == "upload"
        and selected.remote_exists
        and selected.status == "same"
        and not selected.metadata_differences
    ):
        return _batch_non_action_item(
            resource_key,
            action=action,
            disposition="unchanged",
            platform=selected.platform,
            local_instance_id=selected.local_instance_id,
            target_resource_key=resource_key,
            reason="Local and remote content already match.",
            reason_ref=ui_message(
                "asset.batch.content_already_matches",
                "Local and remote content already match.",
            ),
        )
    plan = build_asset_action_plan(
        action,
        kind=selected.kind,
        name=selected.name,
        platform=selected.platform,
        local_instance_id=selected.local_instance_id,
        new_name=new_name,
        link_target_confirmed=bool(choice and choice.link_target_confirmed),
        config=cfg,
        _remote_snapshot=snapshot,
        _inventory=inventory,
        _persist=False,
    )
    disposition = (
        "blocked"
        if plan.blocked
        else "rename"
        if action == "copy-to-remote"
        else "update"
        if plan.remote_target_exists
        else "create"
    )
    reason = (
        "; ".join(plan.blockers)
        or (
            "Save plugin reference without content."
            if selected.plugin_data.get("plugin", {}).get("track") == "reference"
            else "Upload plugin content."
        )
        if selected.kind == "plugin"
        else ""
    )
    reason_ref = (
        ui_message("asset.batch.blocked", reason, detail=reason)
        if plan.blocked
        else ui_message(
            "asset.batch.save_plugin_reference",
            reason,
        )
        if selected.kind == "plugin"
        and selected.plugin_data.get("plugin", {}).get("track") == "reference"
        else ui_message("asset.batch.upload_plugin_content", reason)
        if selected.kind == "plugin"
        else None
    )
    return AssetBatchPlanItem(
        id=f"upload:{resource_key}:{selected.local_instance_id}",
        resource_key=resource_key,
        platform=selected.platform,
        local_instance_id=selected.local_instance_id,
        action=action,
        disposition=disposition,
        target_resource_key=plan.target_resource_key,
        reason=reason,
        reason_ref=reason_ref,
        warnings=list(plan.warnings),
        warning_refs=list(plan.warning_refs),
        blockers=list(plan.blockers),
        blocker_refs=list(plan.blocker_refs),
        plan=plan,
    )


def _build_batch_download_item(
    resource_key: str,
    platform: str,
    rows: list[AssetPlatformRow],
    choice: AssetBatchChoice | None,
    *,
    cfg: Config,
    snapshot: RemoteSnapshot,
    inventory: AssetInventory,
) -> AssetBatchPlanItem:
    remote_row = next((row for row in rows if row.entry is not None), None)
    if remote_row is None:
        return _batch_non_action_item(
            resource_key,
            action="download",
            disposition="skip",
            platform=platform,
            reason="No remote asset exists.",
            reason_ref=ui_message(
                "asset.batch.remote_asset_missing",
                "No remote asset exists.",
            ),
        )
    if (
        remote_row.entry is not None
        and remote_row.entry.kind == "plugin"
        and remote_row.entry.plugin is not None
        and remote_row.entry.plugin.track == "reference"
    ):
        return _build_plugin_reference_download_item(
            resource_key,
            platform,
            rows,
            remote_row.entry,
            cfg,
        )
    if remote_row.read_only_reference:
        return _batch_non_action_item(
            resource_key,
            action="download",
            disposition="skip",
            platform=platform,
            reason="The read-only reference has no private snapshot that can be installed safely.",
            reason_ref=ui_message(
                "asset.batch.read_only_snapshot_missing",
                "The read-only reference has no private snapshot that can be installed safely.",
            ),
        )
    platform_rows = [row for row in rows if row.platform == platform]
    if not platform_rows:
        return _batch_non_action_item(
            resource_key,
            action="download",
            disposition="skip",
            platform=platform,
            reason="The resource has no compatible target on this platform.",
            reason_ref=ui_message(
                "asset.batch.compatible_target_missing",
                "The resource has no compatible target on this platform.",
            ),
        )
    expected = next(
        (
            row
            for row in platform_rows
            if choice
            and choice.local_instance_id
            and row.local_instance_id == choice.local_instance_id
        ),
        None,
    ) or next(
        (row for row in platform_rows if row.local_locator in {"expected", "plugin-expected"}),
        platform_rows[0],
    )
    if not expected.configured or not expected.enabled:
        return _batch_non_action_item(
            resource_key,
            action="download",
            disposition="skip",
            platform=platform,
            reason="The target platform is not enabled.",
            reason_ref=ui_message(
                "asset.batch.platform_disabled",
                "The target platform is not enabled.",
            ),
        )
    if not expected.supported:
        return _batch_non_action_item(
            resource_key,
            action="download",
            disposition="skip",
            platform=platform,
            reason="The resource is not compatible with this platform.",
            reason_ref=ui_message(
                "asset.batch.platform_incompatible",
                "The resource is not compatible with this platform.",
            ),
        )
    resolution = choice.resolution if choice else "overwrite"
    new_name = choice.new_name.strip() if choice else ""
    action = "copy-to-local" if resolution == "rename" else "download"
    if (
        action == "download"
        and expected.local_exists
        and expected.status == "same"
        and not expected.metadata_differences
    ):
        return _batch_non_action_item(
            resource_key,
            action=action,
            disposition="unchanged",
            platform=platform,
            local_instance_id=expected.local_instance_id,
            target_resource_key=resource_key,
            reason="The target already matches the remote asset.",
            reason_ref=ui_message(
                "asset.batch.target_already_matches",
                "The target already matches the remote asset.",
            ),
        )
    plan = build_asset_action_plan(
        action,
        kind=remote_row.kind,
        name=remote_row.name,
        platform=platform,
        local_instance_id=(
            expected.local_instance_id if expected.local_locator == "plugin-expected" else ""
        ),
        new_name=new_name,
        overwrite_unmanaged=bool(choice and choice.overwrite_unmanaged),
        config=cfg,
        _remote_snapshot=snapshot,
        _inventory=inventory,
        _persist=False,
    )
    disposition = (
        "blocked"
        if plan.blocked
        else "rename"
        if action == "copy-to-local"
        else "update"
        if plan.target_exists
        else "create"
    )
    reason = "; ".join(plan.blockers)
    return AssetBatchPlanItem(
        id=f"download:{resource_key}:{platform}:{plan.local_instance_id}",
        resource_key=resource_key,
        platform=platform,
        local_instance_id=plan.local_instance_id,
        action=action,
        disposition=disposition,
        target_resource_key=plan.target_resource_key,
        reason=reason,
        reason_ref=(
            ui_message("asset.batch.blocked", reason, detail=reason) if plan.blocked else None
        ),
        warnings=list(plan.warnings),
        warning_refs=list(plan.warning_refs),
        blockers=list(plan.blockers),
        blocker_refs=list(plan.blocker_refs),
        plan=plan,
    )


def _build_plugin_reference_download_item(
    resource_key: str,
    platform: str,
    rows: list[AssetPlatformRow],
    entry: RegistryItem,
    cfg: Config | None = None,
) -> AssetBatchPlanItem:
    cfg = cfg or Config()
    spec = entry.plugin
    profile = cfg.platforms.get(platform) or build_platform(platform)
    if spec is None or profile is None or profile.effective_tool_id != spec.platform:
        return _batch_non_action_item(
            resource_key,
            action="download",
            disposition="skip",
            platform=platform,
            reason="This plugin reference belongs to a different platform.",
            reason_ref=ui_message(
                "asset.batch.plugin_platform_mismatch",
                "This plugin reference belongs to a different platform.",
            ),
        )
    local_rows = [
        row
        for row in rows
        if row.local_exists and row.local_locator == "plugin-adapter" and row.platform == platform
    ]
    pending: list[str] = []
    alignments: list[dict[str, Any]] = []
    unchanged = 0
    for installation in spec.installations:
        project = installation.project.model_dump(mode="json") if installation.project else None
        match = next(
            (
                row
                for row in local_rows
                if row.plugin_scope == installation.scope
                and (
                    row.plugin_data.get("plugin", {}).get("installations", [{}])[0].get("project")
                    == project
                )
            ),
            None,
        )
        if installation.scope == "managed":
            pending.append(
                "managed: organization policy requires this state; no local write is allowed"
            )
        elif match is None:
            install_alignment = _claude_plugin_install_alignment(
                cfg,
                profile,
                spec,
                installation,
            )
            if install_alignment is not None:
                alignments.append(install_alignment)
            else:
                pending.append(_plugin_install_instruction(spec, installation))
        elif match.plugin_enabled != installation.enabled:
            state_path = str(match.plugin_data.get("state_path") or "")
            method = _plugin_alignment_method(spec, match, state_path, profile)
            if method:
                state_target, project_root = _claude_plugin_state_target(
                    cfg,
                    profile,
                    installation,
                )
                if spec.platform == "claude-code" and (
                    state_target is None or not _same_local_path(state_target, Path(state_path))
                ):
                    pending.append(
                        f"{installation.scope}: the configured Claude plugin state target does not match this profile"
                    )
                    continue
                alignments.append(
                    {
                        "method": method,
                        "scope": installation.scope,
                        "project": project,
                        "enabled": installation.enabled,
                        "local_instance_id": match.local_instance_id,
                        "state_path": state_path,
                        "state_fingerprint": (
                            resource_hash_path(Path(state_path)) if state_path else ""
                        ),
                        "state_exists": True,
                        "project_root": (str(project_root) if project_root is not None else ""),
                    }
                )
            else:
                pending.append(
                    f"{installation.scope}: set enabled={str(installation.enabled).lower()} using the platform configuration or CLI"
                )
        else:
            unchanged += 1
    if alignments:
        first_path = Path(alignments[0]["state_path"])
        plugin_data = {
            "plugin": spec.model_dump(mode="json"),
            "alignments": alignments,
            "manual": pending,
        }
        plan = AssetActionPlan(
            operation_id=uuid.uuid4().hex,
            action="align-plugin-state",
            resource_key=resource_key,
            target_resource_key=resource_key,
            kind="plugin",
            name=entry.name,
            platform=platform,
            local_instance_id=str(alignments[0]["local_instance_id"]),
            local_locator="plugin-adapter",
            remote_commit=rows[0].remote_commit if rows else "",
            remote_target_exists=True,
            remote_target_fingerprint=(
                next((row.remote_asset_fingerprint for row in rows if row.entry is not None), "")
            ),
            local_source_fingerprint="",
            target_path=first_path,
            target_exists=first_path.is_file(),
            target_fingerprint=(resource_hash_path(first_path) if first_path.is_file() else ""),
            target_managed=False,
            tool_id=profile.effective_tool_id,
            environment_kind=profile.environment_kind,
            environment_name=profile.environment_name,
            warnings=list(pending),
            plugin_data=plugin_data,
            created_at=_utc_now(),
        )
        reason = f"Align {len(alignments)} installed plugin state(s)." + (
            " Remaining manual actions: " + "; ".join(pending) if pending else ""
        )
        return AssetBatchPlanItem(
            id=f"download:{resource_key}:{platform}:state",
            resource_key=resource_key,
            platform=platform,
            local_instance_id=plan.local_instance_id,
            action="align-plugin-state",
            disposition=(
                "create"
                if any(
                    str(alignment.get("method") or "") == "claude-install"
                    for alignment in alignments
                )
                else "update"
            ),
            target_resource_key=resource_key,
            reason=reason,
            reason_ref=ui_message(
                "asset.batch.align_plugin_state",
                reason,
                count=len(alignments),
                detail="; ".join(pending),
            ),
            warnings=list(pending),
            plan=plan,
        )
    if not pending:
        return _batch_non_action_item(
            resource_key,
            action="download",
            disposition="unchanged",
            platform=platform,
            reason=f"{unchanged} plugin installation state(s) already match.",
            reason_ref=ui_message(
                "asset.batch.plugin_state_already_matches",
                f"{unchanged} plugin installation state(s) already match.",
                count=unchanged,
            ),
        )
    return _batch_non_action_item(
        resource_key,
        action="download",
        disposition="manual",
        platform=platform,
        reason="; ".join(pending),
    )


def _claude_plugin_install_alignment(
    cfg: Config,
    profile: PlatformProfile,
    spec: PluginSpec,
    installation: PluginInstallation,
) -> dict[str, Any] | None:
    if (
        spec.platform != "claude-code"
        or spec.origin.type != "marketplace"
        or installation.scope == "managed"
        or claude_cli_context(profile) is None
    ):
        return None
    state_path, project_root = _claude_plugin_state_target(
        cfg,
        profile,
        installation,
    )
    if state_path is None:
        return None
    if not marketplace_install_ready(
        profile,
        spec,
        project_root=project_root,
    ):
        return None
    state_exists = state_path.is_file() and not state_path.is_symlink()
    return {
        "method": "claude-install",
        "scope": installation.scope,
        "project": (installation.project.model_dump(mode="json") if installation.project else None),
        "enabled": installation.enabled,
        "local_instance_id": "",
        "state_path": str(state_path),
        "state_exists": state_exists,
        "state_fingerprint": resource_hash_path(state_path) if state_exists else "",
        "project_root": str(project_root) if project_root is not None else "",
    }


def _claude_plugin_state_target(
    cfg: Config,
    profile: PlatformProfile,
    installation: PluginInstallation,
) -> tuple[Path | None, Path | None]:
    if installation.scope == "user":
        return profile.settings_file(), None
    if installation.scope not in {"project", "local"} or installation.project is None:
        return None, None
    mapping = next(
        (
            item
            for item in cfg.plugin_projects
            if item.repo == installation.project.repo and item.subdir == installation.project.subdir
        ),
        None,
    )
    if mapping is None or not mapping.path_value.is_dir():
        return None, None
    filename = "settings.local.json" if installation.scope == "local" else "settings.json"
    return mapping.path_value / ".claude" / filename, mapping.path_value


def _plugin_alignment_method(
    spec: PluginSpec,
    row: AssetPlatformRow,
    state_path: str,
    profile: PlatformProfile,
) -> str:
    if not row.plugin_writable or row.plugin_scope == "managed" or not state_path:
        return ""
    path = Path(state_path)
    if not path.is_file() or path.is_symlink():
        return ""
    if spec.platform == "claude-code":
        return "claude-cli" if claude_cli_context(profile) is not None else "claude-config"
    if spec.platform == "opencode":
        return "opencode-config"
    if spec.platform == "codex":
        qualified = _plugin_reference_source_label(spec)
        header = f"[plugins.{json.dumps(qualified, ensure_ascii=False)}]"
        try:
            editable = any(
                line.strip() == header or line.strip().startswith(header + " #")
                for line in path.read_text(encoding="utf-8").splitlines()
            )
        except OSError:
            editable = False
        return "codex-config" if editable else ""
    return ""


def _plugin_install_instruction(spec: PluginSpec, installation: PluginInstallation) -> str:
    scope = installation.scope
    selector = spec.origin.selector
    if spec.platform == "codex" and spec.origin.type == "marketplace":
        return f"{scope}: install {spec.plugin_id}@{spec.origin.marketplace} from its marketplace, preserving selector {selector or 'floating'}"
    if spec.platform == "claude-code" and spec.origin.type == "marketplace":
        return f"{scope}: run the Claude plugin install flow for {spec.plugin_id}@{spec.origin.marketplace} with scope {scope}"
    if spec.platform == "opencode" and spec.origin.type == "npm":
        declaration = spec.origin.package + (f"@{selector}" if selector else "")
        return f"{scope}: add {declaration} to the opencode plugin configuration"
    return f"{scope}: install {spec.plugin_id} from {spec.origin.type} source without copying cache content"


def _batch_non_action_item(
    resource_key: str,
    *,
    action: str,
    disposition: str,
    reason: str,
    reason_ref: UiMessageRef | None = None,
    platform: str = "",
    local_instance_id: str = "",
    target_resource_key: str = "",
) -> AssetBatchPlanItem:
    return AssetBatchPlanItem(
        id=f"{action}:{resource_key}:{platform or local_instance_id}",
        resource_key=resource_key,
        platform=platform,
        local_instance_id=local_instance_id,
        action=action,
        disposition=disposition,
        target_resource_key=target_resource_key or resource_key,
        reason=reason,
        reason_ref=reason_ref,
        blockers=[reason] if disposition == "blocked" else [],
        blocker_refs=[reason_ref] if disposition == "blocked" and reason_ref else [],
    )


def _block_duplicate_batch_targets(
    items: list[AssetBatchPlanItem],
    *,
    direction: str,
) -> None:
    grouped: dict[tuple[str, str], list[AssetBatchPlanItem]] = {}
    for item in items:
        if item.plan is None or item.disposition not in {"create", "update", "rename"}:
            continue
        if direction == "download":
            if item.plan.target_path is None:
                continue
            target_path = os.path.normcase(str(item.plan.target_path.expanduser().absolute()))
            target_key = ResourceKey.parse(item.plan.target_resource_key)
            target = (
                target_path,
                target_key.name if target_key.kind == "mcp" else "",
            )
        else:
            target = (item.target_resource_key, "")
        grouped.setdefault(target, []).append(item)
    for duplicates in grouped.values():
        if len(duplicates) < 2:
            continue
        message_ref = ui_message(
            "asset.batch.duplicate_target",
            "Multiple batch items resolve to the same target.",
        )
        message = message_ref.fallback
        for item in duplicates:
            item.disposition = "blocked"
            item.reason = message
            item.reason_ref = message_ref
            item.blockers = _unique_strings([*item.blockers, message])
            item.blocker_refs = _unique_message_refs([*item.blocker_refs, message_ref])
            if item.plan is not None:
                item.plan.blockers = _unique_strings([*item.plan.blockers, message])
                item.plan.blocker_refs = _unique_message_refs(
                    [*item.plan.blocker_refs, message_ref]
                )
                item.plan.blocked = True


def _asset_batch_plan_hash(plan: AssetBatchPlan) -> str:
    payload = {
        "direction": plan.direction,
        "resource_keys": plan.resource_keys,
        "target_platforms": plan.target_platforms,
        "remote_commit": plan.remote_commit,
        "remote_repo_hash": plan.remote_repo_hash,
        "remote_branch": plan.remote_branch,
        "checked_resources": [
            {
                "resource_key": item.resource_key,
                "local_status": item.local_status,
                "remote_status": item.remote_status,
                "status": item.status,
                "local_instances": [
                    {
                        "id": instance.id,
                        "platform": instance.platform,
                        "install_name": instance.install_name,
                        "path": str(instance.path) if instance.path is not None else "",
                        "fingerprint": instance.fingerprint,
                        "path_kind": instance.path_kind,
                        "link_health": instance.link_health,
                        "link_target": instance.link_target,
                        "reparse_tag": instance.reparse_tag,
                        "link_target_trusted": instance.link_target_trusted,
                    }
                    for instance in item.local_instances
                ],
            }
            for item in plan.checked_resources
        ],
        "items": [
            {
                "resource_key": item.resource_key,
                "platform": item.platform,
                "local_instance_id": item.local_instance_id,
                "action": item.action,
                "disposition": item.disposition,
                "target_resource_key": item.target_resource_key,
                "reason": item.reason,
                "warnings": item.warnings,
                "blockers": item.blockers,
                "assertions": _batch_plan_assertions(item.plan),
            }
            for item in plan.items
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _batch_plan_assertions(plan: AssetActionPlan | None) -> dict[str, Any]:
    if plan is None:
        return {}
    return {
        "local_locator": plan.local_locator,
        "remote_target_exists": plan.remote_target_exists,
        "remote_target_fingerprint": plan.remote_target_fingerprint,
        "local_source_fingerprint": plan.local_source_fingerprint,
        "source_path": str(plan.source_path) if plan.source_path is not None else "",
        "source_content_path": (
            str(plan.source_content_path) if plan.source_content_path is not None else ""
        ),
        "source_path_kind": plan.source_path_kind,
        "source_link_health": plan.source_link_health,
        "source_link_target": plan.source_link_target,
        "source_reparse_tag": plan.source_reparse_tag,
        "link_target_confirmed": plan.link_target_confirmed,
        "target_exists": plan.target_exists,
        "target_path": str(plan.target_path) if plan.target_path is not None else "",
        "target_fingerprint": plan.target_fingerprint,
        "target_managed": plan.target_managed,
        "overwrite_unmanaged": plan.overwrite_unmanaged,
        "new_name": plan.new_name,
        "new_install_name": plan.new_install_name,
        "tool_id": plan.tool_id,
        "environment_kind": plan.environment_kind,
        "environment_name": plan.environment_name,
        "plugin_data": plan.plugin_data,
    }


def _batch_passive_result(item: AssetBatchPlanItem) -> AssetActionResult:
    status = (
        "blocked"
        if item.disposition == "blocked"
        else "needs-action"
        if item.disposition == "manual"
        else "unchanged"
        if item.disposition == "unchanged"
        else "skipped"
    )
    return AssetActionResult(
        operation_id="",
        action=item.action,  # type: ignore[arg-type]
        status=status,
        resource_key=item.resource_key,
        target_resource_key=item.target_resource_key,
        platform=item.platform,
        message=item.reason or item.disposition,
        message_ref=item.reason_ref,
        warnings=item.warnings,
        warning_refs=item.warning_refs,
    )


def _batch_error_result(
    plan: AssetActionPlan,
    status: str,
    message: str,
    message_ref: UiMessageRef | None = None,
) -> AssetActionResult:
    return AssetActionResult(
        operation_id=plan.operation_id,
        action=plan.action,
        status=status,
        resource_key=plan.resource_key,
        target_resource_key=plan.target_resource_key,
        platform=plan.platform,
        message=message,
        message_ref=message_ref,
        warnings=plan.warnings,
        warning_refs=plan.warning_refs,
    )


def load_asset_action_plan(
    operation_id: str,
    *,
    config: Config | None = None,
) -> AssetActionPlan:
    cfg = config or load_config()
    path = _asset_plan_dir(operation_id, config=cfg) / "plan.json"
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
        "align-plugin-state",
    }:
        raise AssetPlanInvalid(f"Unsupported persisted asset action: {action}")
    key = ResourceKey.parse(str(data.get("resource_key") or ""))
    target_key = ResourceKey.parse(str(data.get("target_resource_key") or ""))
    if key.kind != str(data.get("kind") or "") or key.name != str(data.get("name") or ""):
        raise AssetPlanInvalid("Asset action plan resource fields are inconsistent.")
    target_path_value = str(data.get("target_path") or "")
    source_path_value = str(data.get("source_path") or "")
    source_content_path_value = str(data.get("source_content_path") or "")
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
        source_path=Path(source_path_value) if source_path_value else None,
        source_content_path=(
            Path(source_content_path_value) if source_content_path_value else None
        ),
        source_path_kind=str(data.get("source_path_kind") or "regular"),
        source_link_health=str(data.get("source_link_health") or "ready"),
        source_link_target=str(data.get("source_link_target") or ""),
        source_reparse_tag=str(data.get("source_reparse_tag") or ""),
        link_target_confirmed=bool(data.get("link_target_confirmed", False)),
        overwrite_unmanaged=bool(data.get("overwrite_unmanaged", False)),
        new_name=str(data.get("new_name") or ""),
        new_install_name=str(data.get("new_install_name") or ""),
        tool_id=str(data.get("tool_id") or ""),
        environment_kind=str(data.get("environment_kind") or ""),
        environment_name=str(data.get("environment_name") or ""),
        warnings=[str(item) for item in data.get("warnings", [])],
        warning_refs=ui_messages_from_data(data.get("warning_refs")),
        blockers=[str(item) for item in data.get("blockers", [])],
        blocker_refs=ui_messages_from_data(data.get("blocker_refs")),
        blocked=bool(data.get("blocked", False)),
        created_at=str(data.get("created_at") or ""),
        plugin_data=dict(data.get("plugin_data") or {}),
        remote_repo_hash=str(data.get("remote_repo_hash") or ""),
        remote_branch=str(data.get("remote_branch") or ""),
        schema_version=ASSET_PLAN_SCHEMA_VERSION,
    )


def _refresh_remote_snapshot(cfg: Config, *, refresh: bool) -> RemoteSnapshot:
    _assert_private_asset_boundaries(cfg)
    branch = cfg.resources.branch or "main"
    repo_url = _configured_remote_url(cfg)
    if not repo_url:
        warning_ref = ui_message(
            "asset.remote.not_configured",
            "No remote resource repository URL is configured; remote writes are blocked.",
        )
        return _local_compatibility_snapshot(
            cfg,
            warning=warning_ref.fallback,
            warning_ref=warning_ref,
        )

    cache_key = hashlib.sha256(f"{repo_url}\0{branch}".encode()).hexdigest()[:24]
    state_root = default_state_dir() / ASSET_STATE_DIR
    transport = state_root / REMOTE_CACHE_DIR / cache_key
    try:
        _assert_internal_path(transport, state_root)
        with resource_repo_write_lock(
            transport,
            timeout_seconds=cfg.state.lock_timeout_seconds,
            allow_state_target=True,
        ):
            if transport.is_symlink():
                _remove_internal_path(transport, state_root)
            transport_is_repo = git_ops.is_repo(transport)
            if not transport_is_repo and not refresh:
                cached = _latest_cached_snapshot(state_root / REMOTE_SNAPSHOT_DIR / cache_key)
                if cached is not None:
                    cached_at = datetime.fromtimestamp(
                        cached.stat().st_mtime,
                        tz=timezone.utc,
                    ).isoformat()
                    warning_ref = ui_message(
                        "asset.remote.refresh_skipped_cached",
                        "Remote refresh was skipped; showing the latest cached "
                        f"snapshot from {cached_at}.",
                        cached_at=cached_at,
                    )
                    registry, registry_health = _snapshot_registry_state(
                        cached,
                        commit="" if cached.name == "unborn" else cached.name,
                        repo_url=repo_url,
                        branch=branch,
                    )
                    return RemoteSnapshot(
                        root=cached,
                        registry=registry,
                        commit="" if cached.name == "unborn" else cached.name,
                        branch=branch,
                        repo_url=repo_url,
                        available=False,
                        warning=warning_ref.fallback,
                        warning_ref=warning_ref,
                        registry_health=registry_health,
                    )
                warning_ref = ui_message(
                    "asset.remote.refresh_skipped_legacy",
                    "Remote refresh was skipped and no cached snapshot is "
                    "available; showing the legacy local snapshot read-only.",
                )
                return _local_compatibility_snapshot(
                    cfg,
                    repo_url=repo_url,
                    warning=warning_ref.fallback,
                    warning_ref=warning_ref,
                )

            if not transport_is_repo:
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
            git_ops.configure_host_autocrlf_disabled_checkout(transport)

            snapshot_cache_root = state_root / REMOTE_SNAPSHOT_DIR / cache_key
            _assert_internal_path(snapshot_cache_root, state_root)
            if snapshot_cache_root.is_symlink():
                _remove_internal_path(snapshot_cache_root, state_root)
            snapshot_root = snapshot_cache_root / (remote_commit or "unborn")
            if not snapshot_root.exists() or not _snapshot_format_is_current(snapshot_root):
                source_registry_health = audit_registry_root(
                    transport,
                    remote_commit=remote_commit or "",
                    repo_url=repo_url,
                    branch=branch,
                ).health
                _materialize_remote_snapshot(
                    transport,
                    snapshot_root,
                    snapshot_cache_root,
                    registry_health=source_registry_health,
                )
            registry, registry_health = _snapshot_registry_state(
                snapshot_root,
                commit=remote_commit or "",
                repo_url=repo_url,
                branch=branch,
            )
            return RemoteSnapshot(
                root=snapshot_root,
                registry=registry,
                commit=remote_commit or "",
                branch=branch,
                repo_url=repo_url,
                registry_health=registry_health,
            )
    except Exception as exc:
        cached = _latest_cached_snapshot(state_root / REMOTE_SNAPSHOT_DIR / cache_key)
        if cached is not None:
            cached_at = datetime.fromtimestamp(
                cached.stat().st_mtime,
                tz=timezone.utc,
            ).isoformat()
            warning_ref = ui_message(
                "asset.remote.refresh_failed_cached",
                "Remote refresh failed; showing the latest cached snapshot "
                f"from {cached_at}: {exc}",
                cached_at=cached_at,
                detail=str(exc),
            )
            registry, registry_health = _snapshot_registry_state(
                cached,
                commit="" if cached.name == "unborn" else cached.name,
                repo_url=repo_url,
                branch=branch,
            )
            return RemoteSnapshot(
                root=cached,
                registry=registry,
                commit="" if cached.name == "unborn" else cached.name,
                branch=branch,
                repo_url=repo_url,
                available=False,
                warning=warning_ref.fallback,
                warning_ref=warning_ref,
                registry_health=registry_health,
            )
        warning_ref = ui_message(
            "asset.remote.refresh_failed_legacy",
            f"Remote refresh failed; showing the legacy local snapshot read-only: {exc}",
            detail=str(exc),
        )
        return _local_compatibility_snapshot(
            cfg,
            repo_url=repo_url,
            warning=warning_ref.fallback,
            warning_ref=warning_ref,
        )


def _local_compatibility_snapshot(
    cfg: Config,
    *,
    repo_url: str = "",
    warning: str,
    warning_ref: UiMessageRef | None = None,
) -> RemoteSnapshot:
    root = resource_root(cfg)
    commit = git_ops.head_commit(root) if git_ops.is_repo(root) else ""
    registry, registry_health = _snapshot_registry_state(
        root,
        commit=commit or "",
        repo_url=repo_url,
        branch=cfg.resources.branch or "main",
    )
    return RemoteSnapshot(
        root=root,
        registry=registry,
        commit=commit or "",
        branch=cfg.resources.branch or "main",
        repo_url=repo_url,
        available=False,
        warning=warning,
        warning_ref=warning_ref,
        registry_health=registry_health,
    )


def _configured_remote_url(cfg: Config) -> str:
    if cfg.resources.repo_url.strip():
        return cfg.resources.repo_url.strip()
    root = resource_root(cfg)
    if git_ops.is_repo(root):
        return git_ops.current_remote_url(root) or ""
    return ""


def _remote_repo_hash(repo_url: str) -> str:
    """Bind plans to one configured remote without exposing its URL."""

    return hashlib.sha256(str(repo_url).strip().encode("utf-8")).hexdigest()


def _latest_cached_snapshot(root: Path) -> Path | None:
    root_probe = probe_local_path(root)
    if (
        root_probe.path_kind != "regular"
        or not root_probe.ready
        or root_probe.content_path is None
        or not root_probe.content_path.is_dir()
    ):
        return None
    candidates = [
        path
        for path in root.iterdir()
        if _regular_ready_directory(path)
        and (path.name == "unborn" or git_ops.is_full_commit_sha(path.name))
        and _snapshot_format_is_current(path)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _snapshot_format_is_current(root: Path) -> bool:
    marker = root / REMOTE_SNAPSHOT_FORMAT_FILE
    health = root / REMOTE_SNAPSHOT_REGISTRY_HEALTH_FILE
    if not marker.is_file() or marker.is_symlink() or not health.is_file() or health.is_symlink():
        return False
    try:
        return marker.read_text(encoding="utf-8").strip() == REMOTE_SNAPSHOT_FORMAT_VERSION
    except OSError:
        return False


def _write_snapshot_format(root: Path) -> None:
    marker = root / REMOTE_SNAPSHOT_FORMAT_FILE
    fd, temporary = tempfile.mkstemp(prefix=".fmt-", dir=root)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(f"{REMOTE_SNAPSHOT_FORMAT_VERSION}\n".encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, marker)
    finally:
        temporary_path.unlink(missing_ok=True)


def _snapshot_copy_ignore(_directory: str, names: list[str]) -> set[str]:
    """Reject links and repository-controlled internal snapshot control paths."""
    internal = {
        ".git",
        REMOTE_SNAPSHOT_FORMAT_FILE,
        REMOTE_SNAPSHOT_REGISTRY_HEALTH_FILE,
    }
    ignored = internal & set(names)
    directory = Path(_directory)
    for name in names:
        probe = probe_local_path(directory / name)
        if probe.path_kind != "regular" or not probe.ready:
            ignored.add(name)
    return ignored


def _regular_ready_directory(path: Path) -> bool:
    probe = probe_local_path(path)
    return bool(
        probe.path_kind == "regular"
        and probe.ready
        and probe.content_path is not None
        and probe.content_path.is_dir()
    )


def _write_snapshot_registry_health(
    root: Path,
    health: RegistryHealthSummary,
) -> None:
    path = root / REMOTE_SNAPSHOT_REGISTRY_HEALTH_FILE
    fd, temporary = tempfile.mkstemp(prefix=".health-", dir=root)
    temporary_path = Path(temporary)
    try:
        payload = json.dumps(asdict(health), ensure_ascii=False, sort_keys=True)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _read_snapshot_registry_health(
    root: Path,
    *,
    commit: str,
) -> RegistryHealthSummary | None:
    path = _safe_snapshot_member_path(
        root,
        root / REMOTE_SNAPSHOT_REGISTRY_HEALTH_FILE,
    )
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        status = str(data.get("status") or "")
        checked_commit = str(data.get("checked_commit") or "")
        if status not in {"healthy", "issues", "legacy", "missing", "invalid", "unavailable"}:
            return None
        if checked_commit != commit:
            return None
        return RegistryHealthSummary(
            status=status,  # type: ignore[arg-type]
            checked_commit=checked_commit,
            issue_count=int(data.get("issue_count") or 0),
            repairable_count=int(data.get("repairable_count") or 0),
            blocked_count=int(data.get("blocked_count") or 0),
            message=str(data.get("message") or ""),
        )
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _snapshot_registry_path(root: Path) -> Path | None:
    candidate = _safe_snapshot_member_path(
        root,
        root / DEFAULT_REGISTRY_FILENAME,
    )
    if candidate is None or not candidate.is_file():
        return None
    return candidate


def _load_snapshot_registry(root: Path) -> Registry | None:
    registry_path = _snapshot_registry_path(root)
    if registry_path is None:
        return None
    try:
        return load_registry(registry_path)
    except Exception:
        return None


def _snapshot_registry_state(
    root: Path,
    *,
    commit: str,
    repo_url: str,
    branch: str,
) -> tuple[Registry | None, RegistryHealthSummary]:
    health = _read_snapshot_registry_health(root, commit=commit)
    if health is None:
        health = audit_registry_root(
            root,
            remote_commit=commit,
            repo_url=repo_url,
            branch=branch,
        ).health
    registry = _load_snapshot_registry(root) if health.status in {"healthy", "issues"} else None
    if health.status in {"healthy", "issues"} and registry is None:
        health = audit_registry_root(
            root,
            remote_commit=commit,
            repo_url=repo_url,
            branch=branch,
        ).health
    return registry, health


def _materialize_remote_snapshot(
    transport: Path,
    snapshot_root: Path,
    snapshot_cache_root: Path,
    *,
    registry_health: RegistryHealthSummary | None = None,
) -> None:
    """Build a complete snapshot before replacing an older cache entry."""
    snapshot_cache_root.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex[:8]
    temporary = snapshot_cache_root / f".snap-{nonce}"
    backup = snapshot_cache_root / f".old-{nonce}"
    _assert_internal_path(temporary, snapshot_cache_root)
    _assert_internal_path(backup, snapshot_cache_root)
    backup_created = False
    published = False
    try:
        shutil.copytree(
            transport,
            temporary,
            symlinks=True,
            ignore=_snapshot_copy_ignore,
        )
        source_health = registry_health or audit_registry_root(transport).health
        _write_snapshot_registry_health(temporary, source_health)
        _write_snapshot_format(temporary)
        if snapshot_root.exists() or snapshot_root.is_symlink():
            os.replace(snapshot_root, backup)
            backup_created = True
        try:
            os.replace(temporary, snapshot_root)
            published = True
        except Exception:
            if backup_created and not snapshot_root.exists():
                os.replace(backup, snapshot_root)
                backup_created = False
            raise
        if backup_created:
            _remove_internal_path(backup, snapshot_cache_root)
            backup_created = False
    finally:
        if temporary.exists() or temporary.is_symlink():
            _remove_internal_path(temporary, snapshot_cache_root)
        if published and backup_created:
            _remove_internal_path(backup, snapshot_cache_root)


def _platform_contexts(
    cfg: Config,
    discovery: EnvDiscoveryResult | None,
) -> dict[str, _PlatformContext]:
    contexts: dict[str, _PlatformContext] = {}
    for profile in cfg.platforms.profiles:
        adapter = tool_adapter_by_id(profile.effective_tool_id)
        available, problem = _profile_environment_state(profile)
        contexts[profile.name] = _PlatformContext(
            profile=replace(profile),
            configured=True,
            detected=False,
            supported_kinds=set(adapter.supports_kinds)
            if adapter
            else set(RESOURCE_PARENT_BY_KIND),
            environment_available=available,
            environment_problem=problem,
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
            if tool.instruction_path is not None:
                existing.profile.instructions_path = str(tool.instruction_path)
            if tool.memories_path is not None:
                existing.profile.memories_dir = str(tool.memories_path)
                existing.profile.memory_layout = tool.memory_layout
            existing.memory_problem = tool.memory_blocker
            continue
        profile = build_platform(
            tool.id,
            {
                "tool_id": tool.tool_id or tool.id,
                "environment_kind": tool.environment_kind,
                "environment_name": tool.environment_name,
                "display_name": tool.display_name,
            },
        )
        if tool.instruction_path is not None:
            profile.instructions_path = str(tool.instruction_path)
        if tool.memories_path is not None:
            profile.memories_dir = str(tool.memories_path)
            profile.memory_layout = tool.memory_layout
        profile.enabled = False
        contexts[tool.id] = _PlatformContext(
            profile=profile,
            configured=False,
            detected=True,
            supported_kinds=set(tool.supports_kinds),
            memory_problem=tool.memory_blocker,
        )
    return contexts


def _profile_environment_state(profile: PlatformProfile) -> tuple[bool, str]:
    current_kind, current_name = current_environment_identity()
    configured_kind = profile.environment_kind.strip().lower()
    if profile.home_dir in {"", "~"} and configured_kind:
        kind_matches = configured_kind == current_kind
        name_matches = configured_kind != "wsl" or (
            bool(profile.environment_name)
            and bool(current_name)
            and profile.environment_name.casefold() == current_name.casefold()
        )
        if not kind_matches or not name_matches:
            return (
                False,
                "The configured runtime identity differs from the current process; "
                "set an explicit accessible home_dir for this profile.",
            )
    if not profile.home_dir or profile.home_dir == "~":
        return True, ""
    if not is_cross_platform_absolute_path(profile.home_dir):
        return (
            False,
            "The configured runtime home_dir is not absolute; access is blocked.",
        )
    home = profile.home_path()
    home_text = str(home).replace("/", "\\")
    wsl_unc = re.match(
        r"^\\\\(?:wsl\.localhost|wsl\$)\\([^\\]+)(?:\\|$)",
        home_text,
        flags=re.IGNORECASE,
    )
    if wsl_unc is not None and (
        configured_kind != "wsl"
        or not profile.environment_name
        or wsl_unc.group(1).casefold() != profile.environment_name.casefold()
    ):
        return (
            False,
            "The configured WSL UNC home does not match the runtime distro identity.",
        )
    unsafe_home = _local_target_ancestor_problem(home / ".cc-port-home-probe")
    if unsafe_home:
        return False, unsafe_home
    if home.is_dir():
        return True, ""
    return (
        False,
        f"The configured {profile.environment_kind or 'runtime'} home is unavailable: {home}",
    )


def _effective_runtime_profile(
    cfg: Config,
    profile: PlatformProfile,
    *,
    kind: ItemKind,
) -> PlatformProfile:
    """Re-evaluate trusted adapter settings before resolving a write target."""
    discovery = discover_environment(
        config=cfg,
        scan_global=False,
        project_ids=[],
    )
    tool = next((item for item in discovery.tools if item.id == profile.name), None)
    effective = replace(profile)
    if tool is None:
        return effective
    if kind == "memory" and tool.memory_blocker:
        raise _StaleAssetTarget("stale-platform", tool.memory_blocker)
    if tool.instruction_path is not None:
        effective.instructions_path = str(tool.instruction_path)
    if tool.memories_path is not None:
        effective.memories_dir = str(tool.memories_path)
        effective.memory_layout = tool.memory_layout
    return effective


def _expected_row(
    entry: RegistryItem,
    platform_name: str,
    context: _PlatformContext,
    snapshot: RemoteSnapshot,
    cfg: Config,
    *,
    reference_commits: dict[tuple[str, str], str],
) -> AssetPlatformRow | None:
    install_name = _entry_install_name(entry, context.profile)
    target = _resolve_entry_install_path(context.profile, entry)
    if target is None:
        return None
    supported = (
        context.profile.supports_resource(entry.kind, entry.platforms)
        and (not context.supported_kinds or entry.kind in context.supported_kinds)
        and not (entry.kind == "memory" and context.memory_problem)
    )
    target = target.expanduser().absolute()
    remote_path = _remote_content_path(snapshot.root, entry)
    remote_writable = _is_private_repo_asset(entry)
    read_only = not remote_writable
    remote_content = _platform_content_fingerprint(entry, remote_path, target)
    remote_asset = _remote_asset_fingerprint(snapshot.root, entry)
    reference_commit = ""
    if read_only and entry.repo:
        reference_commit = _reference_commit(entry, cfg, reference_commits)

    target_ancestor_problem = (
        _local_target_ancestor_problem(target) if entry.kind in {"instruction", "memory"} else ""
    )
    if target_ancestor_problem:
        local_exists = False
        local_fingerprint = ""
        ownership = "unknown"
        local_probe = None
        local_tree_blockers = []
    else:
        (
            local_exists,
            local_fingerprint,
            ownership,
            local_probe,
            local_tree_blockers,
        ) = _expected_local_state(
            entry,
            context.profile,
            target,
            install_name,
        )
    local_content_path = (
        local_probe.content_path
        if local_probe is not None and local_probe.ready
        else target
        if entry.kind == "mcp" and local_exists
        else None
    )
    metadata_differences: list[str] = []
    if local_fingerprint and local_content_path is not None:
        local_metadata = _derive_metadata(
            entry.kind,
            local_content_path,
            mcp_config=(
                _read_mcp_server(local_content_path, install_name) if entry.kind == "mcp" else None
            ),
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
    blocker_refs: list[UiMessageRef] = []
    if target_ancestor_problem:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.local_path_unsafe",
                target_ancestor_problem,
                detail=target_ancestor_problem,
            ),
        )
    if (
        local_probe is not None
        and not local_probe.ready
        and local_probe.health != "missing"
        and not _is_replaceable_dangling_target_symlink(local_probe)
    ):
        problem = local_probe.problem or "The local path cannot be read safely."
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.wsl_link_unsupported"
                if local_probe.health == "unsupported-wsl"
                else "asset.blocker.local_path_unsafe",
                problem,
                detail=problem,
            ),
        )
    for problem in local_tree_blockers:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.nested_link_unsafe",
                problem,
                detail=problem,
            ),
        )
    if (
        entry.kind == "memory"
        and context.profile.memory_layout == "projects"
        and entry.name not in context.profile.memory_install_names
        and not local_exists
    ):
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.memory_target_mapping_required",
                "Map this memory resource to the exact local Claude project slot before downloading.",
            ),
        )
    if entry.kind == "memory" and context.memory_problem:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.memory_settings_untrusted",
                context.memory_problem,
                detail=context.memory_problem,
            ),
        )
    if not context.configured:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.platform_not_configured",
                "Platform is detected but not configured; configure it before downloading.",
            ),
        )
    elif not context.environment_available:
        problem = (
            context.environment_problem or "The configured runtime environment is unavailable."
        )
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.environment_unavailable",
                problem,
                detail=problem,
            ),
        )
    elif not context.profile.enabled:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.platform_disabled",
                "Platform is configured but disabled; enable it before downloading.",
            ),
        )
    if entry.kind == "instruction" and not entry.platforms:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.instruction_tool_binding_required",
                "Instruction resources require an explicit tool binding before download.",
            ),
        )
    elif not supported:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.resource_not_enabled",
                "The resource is not enabled for this platform.",
            ),
        )
    if read_only:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.read_only_reference",
                "This registry item is a read-only reference in asset sync.",
            ),
        )
    if not snapshot.available:
        blockers.append(snapshot.warning or "Remote snapshot is not current.")
        if snapshot.warning_ref:
            blocker_refs.append(snapshot.warning_ref)
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
        local_content_path=local_content_path,
        target_path=target,
        ownership=ownership,
        status=status,
        remote_commit=snapshot.commit,
        reference_commit=reference_commit,
        remote_content_fingerprint=remote_content,
        remote_asset_fingerprint=remote_asset,
        local_fingerprint=local_fingerprint,
        path_kind=local_probe.path_kind if local_probe is not None else "regular",
        link_health=local_probe.health if local_probe is not None else "ready",
        link_target=local_probe.raw_target if local_probe is not None else "",
        reparse_tag=local_probe.reparse_tag_hex if local_probe is not None else "",
        link_target_trusted=(
            not local_probe.is_link or is_known_canonical_link_target(local_probe)
            if local_probe is not None
            else True
        ),
        metadata_differences=metadata_differences,
        diff_summary=_diff_summary(status, metadata_differences),
        diff_summary_refs=_diff_summary_refs(status, metadata_differences),
        blockers=_unique_strings(blockers),
        blocker_refs=_unique_message_refs(blocker_refs),
        entry=entry,
        tool_id=context.profile.effective_tool_id,
        environment_kind=context.profile.environment_kind,
        environment_name=context.profile.environment_name,
        display_name=context.profile.effective_display_name,
        memory_layout=context.profile.memory_layout if entry.kind == "memory" else "",
    )


def _entry_install_name(entry: RegistryItem, profile: PlatformProfile) -> str:
    if entry.kind == "memory":
        return profile.memory_install_names.get(entry.name) or entry.name
    return (
        entry.platform_install_dirs.get(profile.effective_tool_id)
        or entry.install_dir
        or entry.name
    )


def _resolve_entry_install_path(
    profile: PlatformProfile,
    entry: RegistryItem,
    *,
    logical_name: str | None = None,
    install_name: str | None = None,
) -> Path | None:
    """Resolve one target while applying local memory-slot mapping exactly once."""
    if entry.kind == "memory":
        return profile.resolve_install_path("memory", logical_name or entry.name)
    return profile.resolve_install_path(
        entry.kind,
        install_name or _entry_install_name(entry, profile),
    )


def _is_replaceable_dangling_target_symlink(probe: LocalPathProbe) -> bool:
    """Leave native dangling symlink replacement to download confirmation."""
    return probe.path_kind == "symlink" and probe.health == "dangling"


def _expected_plugin_rows(
    entry: RegistryItem,
    snapshot: RemoteSnapshot,
    cfg: Config,
    contexts: dict[str, _PlatformContext],
) -> list[AssetPlatformRow]:
    spec = entry.plugin
    if spec is None:
        return []
    matching_contexts = [
        context
        for context in contexts.values()
        if context.profile.effective_tool_id == spec.platform
    ]
    if not matching_contexts:
        matching_contexts = [_detected_context(spec.platform, "plugin")]
    installations = spec.installations or [PluginInstallation(scope="user", enabled=True)]
    rows: list[AssetPlatformRow] = []
    for context in matching_contexts:
        profile = context.profile
        for installation in installations:
            installation_spec = spec.model_copy(deep=True)
            installation_spec.installations = [installation]
            project_id = _configured_plugin_project_id(cfg, installation.project)
            remote_path = _remote_content_path(snapshot.root, entry)
            target = (
                _plugin_content_target(entry, installation, cfg, context)
                if spec.track == "content"
                else None
            )
            local_exists = bool(target and target.exists() and not target.is_symlink())
            remote_source = _plugin_remote_content_source(remote_path, spec)
            remote_content = (
                _plugin_installation_fingerprint(spec, installation)
                if spec.track == "reference"
                else resource_hash_path(remote_source)
                if remote_source and remote_source.exists()
                else ""
            )
            local_fingerprint = (
                resource_hash_path(target) if local_exists and target is not None else ""
            )
            status = _asset_status(
                remote_exists=True,
                local_exists=local_exists,
                remote_fingerprint=remote_content,
                local_fingerprint=local_fingerprint,
                metadata_differences=[],
                read_only=False,
            )
            blockers: list[str] = []
            blocker_refs: list[UiMessageRef] = []
            if spec.track == "content" and target is None:
                _append_message(
                    blockers,
                    blocker_refs,
                    ui_message(
                        "asset.blocker.plugin_target_missing",
                        "No portable local target is configured for this plugin installation.",
                    ),
                )
            if installation.scope == "managed":
                _append_message(
                    blockers,
                    blocker_refs,
                    ui_message(
                        "asset.blocker.managed_plugin_read_only",
                        "Managed plugin installations are read-only.",
                    ),
                )
            plugin_data: dict[str, Any] = {"plugin": installation_spec.model_dump(mode="json")}
            if spec.platform == "claude-code" and spec.track == "content":
                state_path, project_root = _claude_plugin_state_target(
                    cfg,
                    profile,
                    installation,
                )
                if state_path is not None:
                    state_exists = state_path.is_file() and not state_path.is_symlink()
                    plugin_data.update(
                        {
                            "state_path": str(state_path),
                            "state_exists": state_exists,
                            "state_fingerprint": (
                                resource_hash_path(state_path) if state_exists else ""
                            ),
                            "project_root": (str(project_root) if project_root is not None else ""),
                        }
                    )
            local_id = _instance_id(
                "plugin-expected",
                entry.resource_key,
                profile.name,
                target or Path(project_id or installation.scope),
                installation.scope,
            )
            rows.append(
                AssetPlatformRow(
                    resource_key=entry.resource_key,
                    kind="plugin",
                    name=entry.name,
                    platform=profile.name,
                    local_instance_id=local_id,
                    local_locator="plugin-expected",
                    install_name=spec.plugin_id,
                    configured=context.configured
                    or (spec.track == "content" and target is not None),
                    enabled=context.profile.enabled if context.configured else target is not None,
                    detected=context.detected,
                    supported=profile.supports_resource(entry.kind, entry.platforms),
                    remote_exists=True,
                    local_exists=local_exists,
                    remote_writable=True,
                    read_only_reference=False,
                    remote_path=remote_path,
                    local_path=target if local_exists else None,
                    target_path=target,
                    ownership=(
                        "managed"
                        if local_exists
                        and target is not None
                        and target.is_dir()
                        and is_cc_port_managed(target, resource_key=entry.resource_key)
                        else "unmanaged"
                        if local_exists
                        else "missing"
                    ),
                    status=status,
                    remote_commit=snapshot.commit,
                    remote_content_fingerprint=remote_content,
                    remote_asset_fingerprint=_remote_asset_fingerprint(snapshot.root, entry),
                    local_fingerprint=local_fingerprint,
                    diff_summary=_diff_summary(status, []),
                    diff_summary_refs=_diff_summary_refs(status, []),
                    blockers=blockers,
                    blocker_refs=blocker_refs,
                    entry=entry,
                    plugin_track=spec.track,
                    plugin_id=spec.plugin_id,
                    plugin_scope=installation.scope,
                    plugin_project_id=project_id,
                    plugin_source_kind=spec.origin.type,
                    plugin_source_id=_plugin_origin_source_id(spec.origin),
                    plugin_marketplace=(
                        spec.origin.marketplace if spec.origin.type == "marketplace" else ""
                    ),
                    plugin_marketplace_source=(
                        spec.origin.source if spec.origin.type == "marketplace" else ""
                    ),
                    plugin_selector=spec.origin.selector,
                    plugin_observed_version=spec.observed_version,
                    plugin_enabled=installation.enabled,
                    plugin_writable=installation.scope != "managed",
                    plugin_data=plugin_data,
                    tool_id=profile.effective_tool_id,
                    environment_kind=profile.environment_kind,
                    environment_name=profile.environment_name,
                    display_name=profile.effective_display_name,
                )
            )
    return rows


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
        marker_key = (
            managed_resource_key(
                candidate.path,
                file_target=_uses_sibling_marker(candidate.kind, candidate.path),
            )
            if candidate.content_path is not None
            else ""
        )
        key = _safe_resource_key(marker_key, candidate.kind, candidate.name_hint)
        entry = snapshot.registry.get(key.name, key.kind)
        local_content_path = (
            candidate.content_path or candidate.path
            if candidate.link_health == "ready" and not candidate.blockers
            else None
        )
        local_fingerprint = (
            _safe_local_resource_fingerprint(local_content_path, candidate.kind)
            if local_content_path is not None and not candidate.blockers
            else ""
        )
        row = _local_candidate_row(
            snapshot,
            cfg,
            context,
            platform=candidate.tool,
            key=key,
            local_instance_id=candidate.id,
            locator="discovered-resource",
            local_path=candidate.path,
            local_content_path=local_content_path,
            install_name=candidate.install_name_hint or candidate.path.name,
            local_fingerprint=local_fingerprint,
            ownership="managed" if marker_key else "unmanaged",
            entry=entry,
            reference_commits=reference_commits,
            path_kind=candidate.path_kind,
            link_health=candidate.link_health,
            link_target=candidate.link_target,
            reparse_tag=candidate.reparse_tag,
            link_target_trusted=candidate.link_target_trusted,
            discovery_warnings=candidate.warnings,
            discovery_blockers=candidate.blockers,
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
    for candidate in discovery.plugins:
        if candidate.path is not None:
            identity = _local_identity(candidate.platform, "plugin", candidate.path, "")
            if identity in seen_local_paths:
                continue
        entry = _registry_plugin_entry_for_candidate(snapshot.registry, candidate)
        key = (
            entry.key()
            if entry is not None
            else _plugin_candidate_resource_key(snapshot.registry, candidate)
        )
        context = contexts.get(candidate.platform) or _detected_context(
            candidate.platform, "plugin"
        )
        rows.append(_plugin_candidate_row(candidate, key, entry, snapshot, context))
    return rows


def _registry_plugin_entry_for_candidate(
    registry: Registry,
    candidate: Any,
) -> RegistryItem | None:
    for entry in registry.items:
        spec = entry.plugin
        if entry.kind != "plugin" or spec is None or entry.lifecycle != "active":
            continue
        if (
            spec.platform == (candidate.tool_id or candidate.platform)
            and spec.plugin_id == candidate.plugin_id
            and spec.origin.type == candidate.origin_type
            and _plugin_origin_source_id(spec.origin) == _candidate_plugin_source_id(candidate)
        ):
            return entry
    return None


def _candidate_plugin_source_id(candidate: Any) -> str:
    if candidate.origin_type == "marketplace":
        return str(candidate.marketplace or "")
    if candidate.origin_type == "npm":
        return str(candidate.package or "")
    if candidate.origin_type == "git":
        return str(candidate.repo or "")
    return str(candidate.origin_source or "")


def _plugin_candidate_resource_key(registry: Registry, candidate: Any) -> ResourceKey:
    base = candidate.resource_name
    key = ResourceKey(kind="plugin", name=base)
    if registry.get(base, "plugin") is None:
        return key
    identity = (
        f"{candidate.tool_id or candidate.platform}\0{candidate.plugin_id}\0{candidate.origin_type}"
        f"\0{_candidate_plugin_source_id(candidate)}"
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    return ResourceKey(kind="plugin", name=f"{base[:55].rstrip('-')}-{digest}")


def _plugin_candidate_row(
    candidate: Any,
    key: ResourceKey,
    entry: RegistryItem | None,
    snapshot: RemoteSnapshot,
    context: _PlatformContext,
) -> AssetPlatformRow:
    plugin_data = _plugin_data_from_candidate(candidate)
    local_fingerprint = (
        resource_hash_path(candidate.path)
        if candidate.track == "content" and candidate.path is not None and candidate.path.exists()
        else _json_fingerprint(_plugin_reference_fingerprint_payload(plugin_data["plugin"]))
    )
    remote_content = ""
    if entry is not None and entry.plugin is not None:
        if candidate.track == "content" and entry.plugin.track == "content":
            remote_content = _entry_content_fingerprint(
                entry,
                _remote_content_path(snapshot.root, entry),
            )
        elif candidate.track == "reference" and entry.plugin.track == "reference":
            matched = _matching_plugin_installation(entry.plugin, plugin_data["plugin"])
            remote_content = (
                _plugin_installation_fingerprint(entry.plugin, matched)
                if matched is not None
                else "missing-installation"
            )
    remote_exists = entry is not None
    status = _asset_status(
        remote_exists=remote_exists,
        local_exists=True,
        remote_fingerprint=remote_content,
        local_fingerprint=local_fingerprint,
        metadata_differences=[],
        read_only=False,
    )
    blockers: list[str] = []
    blocker_refs: list[UiMessageRef] = []
    if context.configured and not context.environment_available:
        problem = (
            context.environment_problem or "The configured runtime environment is unavailable."
        )
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.environment_unavailable",
                problem,
                detail=problem,
            ),
        )
    if not candidate.complete:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.plugin_discovery_incomplete",
                "Plugin discovery is incomplete; missing fields must not be inferred.",
            ),
        )
    if candidate.scope in {"project", "local"} and not candidate.project_repo:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.project_without_remote",
                "Projects without a Git remote are observation-only and cannot be uploaded.",
            ),
        )
    return AssetPlatformRow(
        resource_key=str(key),
        kind="plugin",
        name=key.name,
        platform=candidate.platform,
        local_instance_id=candidate.id,
        local_locator="plugin-adapter",
        install_name=candidate.plugin_id,
        configured=context.configured,
        enabled=context.profile.enabled if context.configured else False,
        detected=True,
        supported=(entry is None or context.profile.supports_resource(entry.kind, entry.platforms)),
        remote_exists=remote_exists,
        local_exists=True,
        remote_writable=bool(entry is None or entry.plugin is not None),
        read_only_reference=False,
        remote_path=_remote_content_path(snapshot.root, entry),
        local_path=candidate.path if candidate.track == "content" else None,
        target_path=None,
        ownership="managed" if candidate.scope == "managed" else "unmanaged",
        status=status,
        remote_commit=snapshot.commit,
        remote_content_fingerprint=remote_content,
        remote_asset_fingerprint=_remote_asset_fingerprint(snapshot.root, entry) if entry else "",
        local_fingerprint=local_fingerprint,
        diff_summary=_diff_summary(status, []),
        diff_summary_refs=_diff_summary_refs(status, []),
        blockers=_unique_strings(blockers),
        blocker_refs=_unique_message_refs(blocker_refs),
        warnings=list(candidate.warnings),
        entry=entry,
        plugin_track=candidate.track,
        plugin_id=candidate.plugin_id,
        plugin_scope=candidate.scope,
        plugin_project_id=candidate.project_id,
        plugin_source_kind=candidate.origin_type,
        plugin_source_id=candidate.source_id,
        plugin_marketplace=(
            candidate.marketplace if candidate.origin_type == "marketplace" else ""
        ),
        plugin_marketplace_source=(
            candidate.origin_source if candidate.origin_type == "marketplace" else ""
        ),
        plugin_selector=candidate.selector,
        plugin_observed_version=candidate.observed_version,
        plugin_enabled=candidate.enabled,
        plugin_writable=candidate.writable,
        plugin_data=plugin_data,
        tool_id=context.profile.effective_tool_id,
        environment_kind=context.profile.environment_kind,
        environment_name=context.profile.environment_name,
        display_name=context.profile.effective_display_name,
        memory_layout=context.profile.memory_layout if key.kind == "memory" else "",
    )


def _plugin_data_from_candidate(candidate: Any) -> dict[str, Any]:
    project = None
    if candidate.scope in {"project", "local"} and candidate.project_repo:
        project = {"repo": candidate.project_repo, "subdir": candidate.project_subdir}
    origin = {
        "type": candidate.origin_type,
        "marketplace": candidate.marketplace,
        "source": candidate.origin_source,
        "package": candidate.package,
        "repo": candidate.repo,
        "selector": candidate.selector,
    }
    installation = {
        "scope": candidate.scope,
        "enabled": True if candidate.enabled is None else candidate.enabled,
        "project": project,
    }
    return {
        "plugin": {
            "track": candidate.track,
            "platform": candidate.tool_id or candidate.platform,
            "plugin_id": candidate.plugin_id,
            "origin": origin,
            "observed_version": candidate.observed_version,
            "installations": [installation],
            "dependencies": dict(candidate.dependencies),
        },
        "description": candidate.description,
        "complete": candidate.complete,
        "selector_known": bool(getattr(candidate, "selector_known", True)),
        "state_path": str(candidate.state_path) if candidate.state_path is not None else "",
    }


def _content_plugin_reference_data(
    row: AssetPlatformRow,
    choice: AssetBatchChoice | None,
) -> tuple[dict[str, Any], str]:
    origin = dict(choice.reference_origin if choice else {})
    origin_type = str(origin.get("type") or "").strip()
    if origin_type not in {"marketplace", "npm", "git"}:
        return {}, "Reference conversion requires marketplace, npm, or git origin fields."
    base = json.loads(json.dumps(row.plugin_data))
    plugin = base.get("plugin", {})
    plugin_platform = str(plugin.get("platform") or row.tool_id or row.platform).strip()
    claude_error = _claude_plugin_reference_error(
        platform=plugin_platform,
        origin_type=origin_type,
        marketplace=str(origin.get("marketplace") or ""),
        source=str(origin.get("source") or ""),
        selector=str(origin.get("selector") or ""),
    )
    if claude_error:
        return {}, claude_error
    plugin["track"] = "reference"
    plugin["origin"] = {
        "type": origin_type,
        "marketplace": str(origin.get("marketplace") or ""),
        "source": str(origin.get("source") or ""),
        "package": str(origin.get("package") or ""),
        "repo": str(origin.get("repo") or ""),
        "selector": str(origin.get("selector") or ""),
    }
    plugin["observed_version"] = row.plugin_observed_version
    plugin["dependencies"] = {}
    try:
        PluginSpec.model_validate(plugin)
    except Exception as exc:  # noqa: BLE001 - return a stable batch blocker
        return {}, f"Invalid plugin reference origin: {exc}"
    return base, ""


def _claude_plugin_reference_error(
    *,
    platform: str,
    origin_type: str,
    marketplace: str,
    source: str,
    selector: str,
) -> str:
    if platform != "claude-code":
        return ""
    if origin_type != "marketplace":
        return "Claude Code reference plugins require a marketplace origin."
    if not marketplace.strip() or not source.strip():
        return (
            "Claude Code marketplace references require both a marketplace name "
            "and a portable marketplace source."
        )
    if not installable_marketplace_source(source, selector):
        return "Claude Code marketplace source is not safely installable."
    return ""


def _plugin_origin_source_id(origin: PluginOrigin) -> str:
    if origin.type == "marketplace":
        return origin.marketplace
    if origin.type == "npm":
        return origin.package
    if origin.type == "git":
        return origin.repo
    return origin.source


def _plugin_reference_fingerprint_payload(plugin: dict[str, Any]) -> dict[str, Any]:
    data = json.loads(json.dumps(plugin))
    data.pop("observed_version", None)
    return data


def _plugin_installation_fingerprint(
    spec: PluginSpec,
    installation: PluginInstallation,
) -> str:
    payload = spec.model_dump(mode="json")
    payload["installations"] = [installation.model_dump(mode="json")]
    return _json_fingerprint(_plugin_reference_fingerprint_payload(payload))


def _matching_plugin_installation(
    spec: PluginSpec,
    candidate: dict[str, Any],
) -> PluginInstallation | None:
    raw_installations = candidate.get("installations", [])
    if not raw_installations:
        return None
    wanted = raw_installations[0]
    for installation in spec.installations:
        current = installation.model_dump(mode="json")
        if current.get("scope") == wanted.get("scope") and current.get("project") == wanted.get(
            "project"
        ):
            return installation
    return None


def _configured_plugin_project_id(
    cfg: Config,
    project: PluginProjectIdentity | None,
) -> str:
    if project is None:
        return ""
    for item in cfg.plugin_projects:
        if item.repo == project.repo and item.subdir == project.subdir:
            return item.id
    return ""


def _plugin_content_target(
    entry: RegistryItem,
    installation: PluginInstallation,
    cfg: Config,
    context: _PlatformContext,
) -> Path | None:
    spec = entry.plugin
    if spec is None or spec.track != "content" or installation.scope == "managed":
        return None
    base: Path | None = None
    if installation.scope in {"project", "local"}:
        project = installation.project
        mapping = next(
            (
                item
                for item in cfg.plugin_projects
                if project is not None
                and item.repo == project.repo
                and item.subdir == project.subdir
            ),
            None,
        )
        if mapping is None or not mapping.path_value.is_dir():
            return None
        if spec.platform == "opencode":
            base = mapping.path_value / ".opencode" / "plugins"
        elif spec.platform == "codex":
            base = mapping.path_value / ".agents" / "plugins"
        elif spec.platform == "claude-code":
            if installation.scope == "local":
                # Claude has no local-only skills-directory location.  A local
                # marketplace install is a different reference-track workflow.
                return None
            base = mapping.path_value / ".claude" / "skills"
    else:
        base = (
            context.profile.skills_path()
            if spec.platform == "claude-code"
            else context.profile.plugins_path()
        )
    if base is None:
        return None
    source_name = Path(spec.origin.source).name if spec.origin.source else ""
    if source_name and source_name != spec.origin.source.replace("\\", "/"):
        return None
    if spec.platform == "opencode" and Path(source_name).suffix.lower() in {".js", ".ts"}:
        return (base / source_name).expanduser().absolute()
    if spec.platform == "claude-code":
        return (base / spec.plugin_id).expanduser().absolute()
    return (base / _entry_install_name(entry, context.profile)).expanduser().absolute()


def _plugin_remote_content_source(remote_path: Path | None, spec: PluginSpec) -> Path | None:
    if remote_path is None:
        return None
    source_name = Path(spec.origin.source).name if spec.origin.source else ""
    if source_name and source_name == spec.origin.source.replace("\\", "/"):
        candidate = remote_path / source_name
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return remote_path


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
    local_content_path: Path | None = None,
    install_name: str,
    local_fingerprint: str,
    ownership: str,
    entry: RegistryItem | None,
    reference_commits: dict[tuple[str, str], str],
    path_kind: str = "regular",
    link_health: str = "ready",
    link_target: str = "",
    reparse_tag: str = "",
    link_target_trusted: bool = True,
    discovery_warnings: list[str] | None = None,
    discovery_blockers: list[str] | None = None,
) -> AssetPlatformRow:
    effective_local_path = local_content_path or local_path
    remote_exists = entry is not None
    remote_path = _remote_content_path(snapshot.root, entry) if entry else None
    remote_content = (
        _platform_content_fingerprint(entry, remote_path, effective_local_path) if entry else ""
    )
    remote_asset = _remote_asset_fingerprint(snapshot.root, entry) if entry else ""
    read_only = bool(entry and not _is_private_repo_asset(entry))
    reference_commit = (
        _reference_commit(entry, cfg, reference_commits)
        if entry and read_only and entry.repo
        else ""
    )
    local_metadata = (
        _derive_metadata(
            key.kind,
            effective_local_path,
            mcp_config=(
                _read_mcp_server(effective_local_path, install_name) if key.kind == "mcp" else None
            ),
        )
        if local_fingerprint
        else {}
    )
    metadata_differences = _metadata_differences(entry, local_metadata) if entry else []
    status = _asset_status(
        remote_exists=remote_exists,
        local_exists=True,
        remote_fingerprint=remote_content,
        local_fingerprint=local_fingerprint,
        metadata_differences=metadata_differences,
        read_only=read_only,
    )
    supported = (not context.supported_kinds or key.kind in context.supported_kinds) and (
        entry is None or context.profile.supports_resource(entry.kind, entry.platforms)
    )
    blockers: list[str] = list(discovery_blockers or [])
    blocker_refs: list[UiMessageRef] = []
    for blocker in discovery_blockers or []:
        message_key = (
            "asset.blocker.wsl_link_unsupported"
            if link_health == "unsupported-wsl"
            else "asset.blocker.nested_link_unsafe"
            if blocker.startswith("Nested link or unreadable entry")
            else "asset.blocker.local_path_unsafe"
        )
        blocker_refs.append(ui_message(message_key, blocker, detail=blocker))
    if context.configured and not context.environment_available:
        problem = (
            context.environment_problem or "The configured runtime environment is unavailable."
        )
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.environment_unavailable",
                problem,
                detail=problem,
            ),
        )
    if not context.configured:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.platform_not_configured",
                "Platform is detected but not configured; configure it before downloading.",
            ),
        )
    if entry is not None and entry.kind == "instruction" and not entry.platforms:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.instruction_tool_binding_required",
                "Instruction resources require an explicit tool binding before download.",
            ),
        )
    elif not supported:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.kind_not_supported",
                "The detected platform does not declare support for this resource kind.",
            ),
        )
    if read_only:
        _append_message(
            blockers,
            blocker_refs,
            ui_message(
                "asset.blocker.read_only_reference",
                "This registry item is a read-only reference in asset sync.",
            ),
        )
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
        local_content_path=local_content_path,
        target_path=local_path,
        ownership=ownership,
        status=status,
        remote_commit=snapshot.commit,
        reference_commit=reference_commit,
        remote_content_fingerprint=remote_content,
        remote_asset_fingerprint=remote_asset,
        local_fingerprint=local_fingerprint,
        path_kind=path_kind,
        link_health=link_health,
        link_target=link_target,
        reparse_tag=reparse_tag,
        link_target_trusted=link_target_trusted,
        metadata_differences=metadata_differences,
        diff_summary=_diff_summary(status, metadata_differences),
        diff_summary_refs=_diff_summary_refs(status, metadata_differences),
        blockers=_unique_strings(blockers),
        blocker_refs=_unique_message_refs(blocker_refs),
        warnings=_unique_strings(
            [
                *(list(getattr(entry, "warnings", [])) if entry else []),
                *(discovery_warnings or []),
            ]
        ),
        entry=entry,
        tool_id=context.profile.effective_tool_id,
        environment_kind=context.profile.environment_kind,
        environment_name=context.profile.environment_name,
        display_name=context.profile.effective_display_name,
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
) -> tuple[bool, str, str, LocalPathProbe | None, list[str]]:
    if entry.kind == "mcp":
        config = _read_mcp_server(target, install_name)
        exists = config is not None
        fingerprint = (
            _json_fingerprint(sanitize_mcp_config_for_storage(config) or {}) if exists else ""
        )
        managed = (
            is_cc_port_managed_mcp(
                target,
                install_name,
                resource_key=entry.resource_key,
            )
            if exists
            else False
        )
        return (
            exists,
            fingerprint,
            "managed" if managed else "unmanaged" if exists else "missing",
            None,
            [],
        )
    probe = probe_local_path(target)
    exists = probe.health != "missing"
    tree_blockers: list[str] = []
    if probe.ready and probe.content_path is not None and probe.content_path.is_dir():
        tree_blockers = [
            f"Nested link or unreadable entry at {item.relative_path}: {item.detail}"
            for item in resource_tree_issues(probe.content_path)
        ]
    fingerprint = (
        _safe_local_resource_fingerprint(probe.content_path, entry.kind)
        if probe.ready and probe.content_path is not None and not tree_blockers
        else ""
    )
    managed = (
        is_cc_port_managed(
            target,
            resource_key=entry.resource_key,
            file_target=_uses_sibling_marker(entry.kind, target),
        )
        if probe.ready
        else False
    )
    return (
        exists,
        fingerprint,
        "managed" if managed else "unmanaged" if exists else "missing",
        probe,
        tree_blockers,
    )


def _safe_local_resource_fingerprint(
    path: Path,
    kind: ItemKind | None = None,
) -> str:
    try:
        return _asset_resource_fingerprint(path, kind)
    except (OSError, RuntimeError):
        return ""


def _asset_resource_fingerprint(path: Path, kind: ItemKind | None) -> str:
    """Hash every validated memory Markdown entry, including cache-like names."""
    if kind != "memory":
        return resource_hash_path(path)
    if not path.exists() or path.is_symlink() or not path.is_dir():
        return ""
    digest = hashlib.sha256()
    digest.update(b"resource-dir\0")
    for item in sorted(path.rglob("*"), key=lambda value: value.as_posix()):
        relative = item.relative_to(path)
        if item.is_symlink():
            raise OSError(f"Memory contains a symbolic link: {relative}")
        digest.update(relative.as_posix().encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        if item.is_file():
            digest.update(b"file\0")
            digest.update(item.read_bytes())
        else:
            digest.update(b"dir\0")
    return digest.hexdigest()


def _platform_content_fingerprint(
    entry: RegistryItem,
    remote_path: Path | None,
    local_target: Path | None,
) -> str:
    if entry.kind == "prompt" and local_target is not None and _is_file_prompt_target(local_target):
        payload, _problem = _prompt_payload_path(remote_path)
        return resource_hash_path(payload) if payload is not None else ""
    if entry.kind == "instruction" and local_target is not None:
        payload, _problem = _instruction_payload_path(remote_path)
        return resource_hash_path(payload) if payload is not None else ""
    return _entry_content_fingerprint(entry, remote_path)


def _entry_content_fingerprint(entry: RegistryItem | None, path: Path | None) -> str:
    if entry is None:
        return ""
    if entry.kind == "mcp" and entry.mcp_config is not None:
        return _json_fingerprint(sanitize_mcp_config_for_storage(entry.mcp_config) or {})
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
    return _asset_resource_fingerprint(path, entry.kind)


def _remote_asset_fingerprint(root: Path, entry: RegistryItem | None) -> str:
    if entry is None:
        return ""
    plugin = entry.plugin.model_dump(mode="json") if entry.plugin is not None else None
    if plugin is not None:
        plugin.pop("observed_version", None)
    payload = {
        "kind": entry.kind,
        "name": entry.name,
        "path": entry.path,
        "source": (
            entry.external_source.model_dump(mode="json", exclude_none=True)
            if entry.external_source is not None
            else None
        ),
        "install_name": entry.install_dir,
        "install_names": entry.platform_install_dirs,
        "platforms": entry.platforms,
        "plugin": plugin,
    }
    payload["content_fingerprint"] = _entry_content_fingerprint(
        entry,
        _remote_content_path(root, entry),
    )
    return _json_fingerprint(payload)


def _remote_content_path(root: Path, entry: RegistryItem | None) -> Path | None:
    if entry is None or not entry.path:
        return None
    return _safe_snapshot_member_path(root, root / entry.path)


def _safe_snapshot_member_path(root: Path, target: Path) -> Path | None:
    """Return a snapshot member only through regular, no-follow components."""
    root_abs = root.expanduser().absolute()
    target_abs = target.expanduser().absolute()
    root_probe = probe_local_path(root_abs)
    if root_probe.path_kind != "regular" or not root_probe.ready:
        return None
    try:
        relative = target_abs.relative_to(root_abs)
        root_resolved = root_abs.resolve(strict=False)
        target_resolved = target_abs.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None
    if target_resolved != root_resolved and root_resolved not in target_resolved.parents:
        return None
    current = root_abs
    for part in relative.parts:
        current /= part
        probe = probe_local_path(current)
        if probe.health == "missing":
            continue
        if probe.path_kind != "regular" or not probe.ready:
            return None
    return target_abs


def _local_target_ancestor_problem(target: Path) -> str:
    """Reject an existing linked/unreadable ancestor before any local write."""
    logical = target.expanduser().absolute()
    for component in reversed(logical.parents):
        probe = probe_local_path(component)
        if probe.health == "missing":
            continue
        if probe.is_link or not probe.ready:
            return probe.problem or f"The local target ancestor cannot be used safely: {component}"
    return ""


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
    return fallback_text(_diff_summary_refs(status, metadata))


def _diff_summary_refs(
    status: AssetStatus,
    metadata: list[str],
) -> list[UiMessageRef]:
    if status == "remote-only":
        return [
            ui_message(
                "asset.platform_diff.remote_only",
                "Remote content is available; no local instance exists.",
            )
        ]
    if status == "local-only":
        return [
            ui_message(
                "asset.platform_diff.local_only",
                "Local content is not present in the remote registry.",
            )
        ]
    if status == "same":
        return [
            ui_message(
                "asset.platform_diff.same",
                "Content fingerprints match.",
            )
        ]
    if status == "content-different":
        return [
            ui_message(
                "asset.platform_diff.content_different",
                "Local and remote content fingerprints differ.",
            )
        ]
    if status == "metadata-only":
        fields = ", ".join(metadata)
        return [
            ui_message(
                "asset.platform_diff.metadata_only",
                f"Derived metadata differs: {fields}.",
                fields=fields,
            )
        ]
    if status == "read-only-reference":
        return [
            ui_message(
                "asset.platform_diff.read_only_reference",
                "The item is tracked by an external or pathless repository reference.",
            )
        ]
    if status == "target-conflict":
        return [
            ui_message(
                "asset.platform_diff.target_conflict",
                "Multiple resources resolve to the same platform target.",
            )
        ]
    return [
        ui_message(
            "asset.platform_diff.uncomparable",
            "The content cannot be compared safely.",
        )
    ]


def _mark_target_collisions(rows: list[AssetPlatformRow]) -> None:
    groups: dict[tuple[str, str, str], list[AssetPlatformRow]] = {}
    for row in rows:
        if (
            row.local_locator not in {"expected", "plugin-expected"}
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
        detail_ref = ui_message(
            "asset.blocker.target_shared",
            detail,
            resource_keys=", ".join(sorted(resource_keys)),
        )
        for row in group:
            row.status = "target-conflict"
            _append_message(row.blockers, row.blocker_refs, detail_ref)
            row.diff_summary_refs = _diff_summary_refs("target-conflict", [])
            row.diff_summary = fallback_text(row.diff_summary_refs)


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
            warning_ref = ui_message(
                "asset.warning.identical_content",
                "Identical content also exists under: " + ", ".join(others),
                resource_keys=", ".join(others),
            )
            _append_message(row.warnings, row.warning_refs, warning_ref)


def _remote_duplicate_keys(
    snapshot: RemoteSnapshot,
    kind: ItemKind,
    fingerprint: str,
    *,
    exclude_key: str,
) -> list[str]:
    matches: list[str] = []
    if snapshot.registry is None:
        return matches
    for entry in snapshot.registry.items:
        if entry.kind != kind or entry.resource_key == exclude_key:
            continue
        if (
            _entry_content_fingerprint(
                entry,
                _remote_content_path(snapshot.root, entry),
            )
            == fingerprint
        ):
            matches.append(entry.resource_key)
    return sorted(matches)


def _finalize_row_actions(row: AssetPlatformRow, snapshot: RemoteSnapshot) -> None:
    actions: list[str] = []
    active = row.entry is None or row.entry.lifecycle == "active"
    target_clear = row.status != "target-conflict"
    registry_available = snapshot.registry_health is None or snapshot.registry_health.status in {
        "healthy",
        "issues",
    }
    remote_ready = snapshot.available and registry_available
    if not registry_available:
        _append_message(
            row.blockers,
            row.blocker_refs,
            ui_message(
                "asset.blocker.registry_unavailable",
                "The remote registry is unavailable; remote resource actions are blocked.",
            ),
        )
    if row.plugin_track == "reference":
        if active and row.remote_exists and remote_ready:
            actions.append("download")
        if active and row.local_exists and not row.blockers and remote_ready:
            actions.append("upload")
        row.available_actions = actions
        row.blockers = _unique_strings(row.blockers)
        row.warnings = _unique_strings(row.warnings)
        return
    if (
        active
        and row.remote_exists
        and row.remote_writable
        and row.configured
        and row.enabled
        and row.supported
        and target_clear
        and remote_ready
    ):
        if not row.blockers:
            actions.extend(["download", "copy-to-local"])
        if row.kind != "memory":
            actions.append("set-platform-install-name")
    elif (
        row.kind != "memory"
        and active
        and row.remote_exists
        and row.remote_writable
        and remote_ready
    ):
        actions.append("set-platform-install-name")
    if active and row.local_exists and remote_ready:
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


def _download_plan_blocker_refs(
    row: AssetPlatformRow,
    overwrite_unmanaged: bool,
) -> list[UiMessageRef]:
    blockers: list[UiMessageRef] = []
    if not row.remote_exists:
        blockers.append(
            ui_message("asset.blocker.remote_asset_missing", "The remote asset does not exist.")
        )
    if not row.remote_writable:
        blockers.append(
            ui_message(
                "asset.blocker.read_only_download",
                "Read-only references cannot be downloaded from the private asset snapshot.",
            )
        )
    if not row.configured or not row.enabled:
        blockers.append(
            ui_message(
                "asset.blocker.platform_not_ready",
                "The platform must be configured and enabled before downloading.",
            )
        )
    if not row.supported:
        blockers.append(
            ui_message(
                "asset.blocker.platform_unsupported",
                "The resource is not supported on this platform.",
            )
        )
    if row.status == "target-conflict":
        blockers.append(
            ui_message(
                "asset.blocker.change_install_name",
                "Change the platform install name before downloading.",
            )
        )
    if row.local_exists and row.ownership != "managed" and not overwrite_unmanaged:
        blockers.append(
            ui_message(
                "asset.blocker.unmanaged_target",
                "The target is unmanaged; explicitly confirm overwrite to continue.",
            )
        )
    marker_problem = _sibling_marker_problem(row)
    if marker_problem:
        blockers.append(
            ui_message(
                "asset.blocker.prompt_marker_unsafe",
                marker_problem,
                detail=marker_problem,
            )
        )
    payload_problem = _file_asset_payload_problem(row)
    if payload_problem:
        blockers.append(
            ui_message(
                "asset.blocker.prompt_payload_ambiguous",
                payload_problem,
                detail=payload_problem,
            )
        )
    return blockers


def _upload_plan_blocker_refs(
    row: AssetPlatformRow,
    link_target_confirmed: bool = False,
) -> list[UiMessageRef]:
    blockers: list[UiMessageRef] = []
    if not row.local_exists:
        blockers.append(
            ui_message("asset.blocker.local_source_missing", "The local source does not exist.")
        )
    if row.remote_exists and not row.remote_writable:
        blockers.append(
            ui_message(
                "asset.blocker.remote_read_only",
                "The matching remote item is a read-only reference; use copy-to-remote.",
            )
        )
    if not row.local_fingerprint:
        blockers.append(
            ui_message(
                "asset.blocker.local_fingerprint_missing",
                "The local source cannot be fingerprinted safely.",
            )
        )
    if (
        row.path_kind in {"symlink", "junction"}
        and not row.link_target_trusted
        and not link_target_confirmed
    ):
        blockers.append(
            ui_message(
                "asset.blocker.link_target_confirmation_required",
                "Confirm this external link target before uploading its contents.",
                target=str(row.local_content_path or row.link_target),
            )
        )
    return blockers


def _copy_to_local_blocker_refs(
    row: AssetPlatformRow,
    registry: Registry,
    new_name: str,
) -> list[UiMessageRef]:
    blockers: list[UiMessageRef] = []
    if not row.remote_exists:
        blockers.append(
            ui_message("asset.blocker.remote_source_missing", "The remote source does not exist.")
        )
    if not row.remote_writable:
        blockers.append(
            ui_message(
                "asset.blocker.copy_local_private_only",
                "Only private-repository assets can be copied to a local target.",
            )
        )
    if not row.configured or not row.enabled:
        blockers.append(
            ui_message(
                "asset.blocker.copy_local_platform_not_ready",
                "The platform must be configured and enabled before copying locally.",
            )
        )
    marker_problem = _sibling_marker_problem(row)
    if marker_problem:
        blockers.append(
            ui_message(
                "asset.blocker.prompt_marker_unsafe",
                marker_problem,
                detail=marker_problem,
            )
        )
    payload_problem = _file_asset_payload_problem(row)
    if payload_problem:
        blockers.append(
            ui_message(
                "asset.blocker.prompt_payload_ambiguous",
                payload_problem,
                detail=payload_problem,
            )
        )
    if new_name and registry.get(new_name, row.kind) is not None:
        blockers.append(
            ui_message(
                "asset.blocker.remote_name_exists",
                "The new name already exists in the remote registry for this kind.",
            )
        )
    return blockers


def _copy_to_remote_blocker_refs(
    row: AssetPlatformRow,
    registry: Registry,
    new_name: str,
    link_target_confirmed: bool = False,
) -> list[UiMessageRef]:
    blockers: list[UiMessageRef] = []
    if not row.local_exists:
        blockers.append(
            ui_message("asset.blocker.local_source_missing", "The local source does not exist.")
        )
    if not row.local_fingerprint:
        blockers.append(
            ui_message(
                "asset.blocker.local_fingerprint_missing",
                "The local source cannot be fingerprinted safely.",
            )
        )
    if (
        row.path_kind in {"symlink", "junction"}
        and not row.link_target_trusted
        and not link_target_confirmed
    ):
        blockers.append(
            ui_message(
                "asset.blocker.link_target_confirmation_required",
                "Confirm this external link target before uploading its contents.",
                target=str(row.local_content_path or row.link_target),
            )
        )
    if new_name and registry.get(new_name, row.kind) is not None:
        blockers.append(
            ui_message(
                "asset.blocker.new_remote_name_exists",
                "The new remote name already exists for this kind.",
            )
        )
    return blockers


def _install_alias_plan_blocker_refs(
    row: AssetPlatformRow,
    registry: Registry,
    cfg: Config,
    install_name: str,
) -> list[UiMessageRef]:
    blockers: list[UiMessageRef] = []
    if not row.remote_exists or not row.remote_writable:
        blockers.append(
            ui_message(
                "asset.blocker.install_name_private_only",
                "Only private-repository assets can store a platform install name.",
            )
        )
    if not install_name:
        blockers.append(
            ui_message(
                "asset.blocker.install_name_required",
                "A platform install name is required.",
            )
        )
    elif not ITEM_NAME_RE.match(install_name):
        blockers.append(
            ui_message(
                "asset.blocker.install_name_invalid",
                "The platform install name must use lowercase letters, digits, and hyphens.",
            )
        )
    if install_name and _install_name_collision(
        registry,
        cfg,
        platform=row.platform,
        kind=row.kind,
        install_name=install_name,
        exclude_key=row.resource_key,
    ):
        blockers.append(
            ui_message(
                "asset.blocker.install_name_collision",
                "The platform install name collides with another resource target.",
            )
        )
    return blockers


def _copy_local_target_state(
    cfg: Config,
    row: AssetPlatformRow,
    new_name: str,
) -> tuple[Path | None, bool, str, bool]:
    platform = cfg.platforms.get(row.platform)
    if platform is None or not new_name:
        return None, False, "", False
    if (
        row.kind == "memory"
        and platform.memory_layout == "projects"
        and new_name not in platform.memory_install_names
    ):
        return None, False, "", False
    install_name = (
        platform.memory_install_names[new_name]
        if row.kind == "memory" and new_name in platform.memory_install_names
        else new_name
    )
    target = (
        platform.resolve_install_path("memory", new_name)
        if row.kind == "memory"
        else platform.resolve_install_path(row.kind, install_name)
    )
    if target is None:
        return None, False, "", False
    target = target.expanduser().absolute()
    if row.kind in {"instruction", "memory"} and _local_target_ancestor_problem(target):
        return target, False, "", False
    if row.kind == "mcp":
        config = _read_mcp_server(target, new_name)
        exists = config is not None
        fingerprint = (
            _json_fingerprint(sanitize_mcp_config_for_storage(config) or {}) if exists else ""
        )
        managed = (
            is_cc_port_managed_mcp(target, new_name, resource_key=f"{row.kind}:{new_name}")
            if exists
            else False
        )
    else:
        exists = target.exists() or target.is_symlink()
        fingerprint = (
            _asset_resource_fingerprint(target, row.kind)
            if exists and not target.is_symlink()
            else ""
        )
        managed = (
            is_cc_port_managed(
                target,
                resource_key=f"{row.kind}:{new_name}",
                file_target=_uses_sibling_marker(row.kind, target),
            )
            if exists
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
    if kind == "memory":
        # Memory slots are local-only profile configuration and never use the
        # portable install-name mutation workflow.
        return False
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
        other_name = _entry_install_name(entry, profile)
        other = _resolve_entry_install_path(
            profile,
            entry,
            install_name=other_name,
        )
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
    if snapshot.registry is None:
        raise _StaleAssetTarget(
            "registry-unavailable",
            snapshot.registry_health.message
            if snapshot.registry_health
            else "The remote registry is unavailable.",
        )
    source_key = ResourceKey.parse(plan.resource_key)
    entry = snapshot.registry.get(source_key.name, source_key.kind)
    current_remote_exists = entry is not None
    current_remote_fingerprint = _remote_asset_fingerprint(snapshot.root, entry) if entry else ""
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
    if (
        entry.kind == "plugin"
        and entry.plugin is not None
        and plan.action == "align-plugin-state"
        and profile is None
    ):
        # Reference-state plans operate on the explicitly asserted state file
        # discovered by the adapter and do not require a content install root.
        return _apply_plugin_reference_state(plan, cfg, snapshot, entry)
    if profile is None or not profile.enabled:
        raise _StaleAssetTarget(
            "stale-platform",
            "The target platform is no longer configured and enabled.",
        )
    if (
        plan.tool_id != profile.effective_tool_id
        or plan.environment_kind != profile.environment_kind
        or plan.environment_name != profile.environment_name
    ):
        raise _StaleAssetTarget(
            "stale-platform",
            "The target platform identity changed after planning.",
        )
    environment_available, environment_problem = _profile_environment_state(profile)
    if not environment_available:
        raise _StaleAssetTarget(
            "stale-platform",
            environment_problem or "The configured runtime environment is unavailable.",
        )
    if entry.kind == "plugin" and entry.plugin is not None:
        if entry.plugin.track == "content":
            return _apply_plugin_content_download(plan, cfg, snapshot, entry)
        if plan.action == "align-plugin-state":
            return _apply_plugin_reference_state(plan, cfg, snapshot, entry)
    profile = _effective_runtime_profile(cfg, profile, kind=entry.kind)
    if (
        entry.kind == "memory"
        and plan.action == "copy-to-local"
        and profile.memory_layout == "projects"
        and plan.new_name not in profile.memory_install_names
    ):
        raise _StaleAssetTarget(
            "stale-platform",
            "The copied memory resource no longer has an exact local project-slot mapping.",
        )
    install_name = (
        profile.memory_install_names.get(plan.new_name, plan.new_name)
        if plan.action == "copy-to-local"
        else _entry_install_name(entry, profile)
    )
    target = _resolve_entry_install_path(
        profile,
        entry,
        logical_name=plan.new_name if plan.action == "copy-to-local" else entry.name,
        install_name=install_name,
    )
    if target is None:
        raise _StaleAssetTarget(
            "stale-platform",
            "The platform no longer has a target for this resource kind.",
        )
    target = target.expanduser().absolute()
    if entry.kind in {"instruction", "memory"}:
        ancestor_problem = _local_target_ancestor_problem(target)
        if ancestor_problem:
            raise _StaleAssetTarget("stale-local-target", ancestor_problem)
    if not _same_local_path(target, plan.target_path):
        raise _StaleAssetTarget(
            "stale-local-target",
            "The local target path changed after planning. Refresh and create a new plan.",
        )
    marker = (
        managed_marker_path(target, file_target=True)
        if _uses_sibling_marker(entry.kind, target)
        else None
    )
    if marker is not None and marker.is_symlink():
        raise _StaleAssetTarget(
            "stale-local-target",
            "The resource ownership sidecar is a symbolic link.",
        )
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
    elif _uses_sibling_marker(entry.kind, target):
        targets.append(
            ChangeTarget(
                path=managed_marker_path(target, file_target=True),
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
        if entry.kind in {"instruction", "memory"}:
            ancestor_problem = _local_target_ancestor_problem(target)
            if ancestor_problem:
                raise _StaleAssetTarget("stale-local-target", ancestor_problem)
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
            mark_cc_port_managed_mcp(
                target,
                install_name,
                resource_name=marker_entry.name,
                resource_kind=marker_entry.kind,
                resource_key=marker_entry.resource_key,
                platform=plan.platform,
            )
            actual = sanitize_mcp_config_for_storage(list_mcp_servers(target).get(install_name))
            if actual != config:
                raise AssetSyncError("MCP download verification failed.")
        else:
            source = _remote_content_path(snapshot.root, entry)
            if source is None or not source.exists() or source.is_symlink():
                raise AssetSyncError("The remote asset content is unavailable or unsafe.")
            if entry.kind == "skill" and profile.effective_tool_id == "claude-code":
                inspect_claude_skill(source)
            verification_source = _installable_asset_source(source, target, entry.kind)
            _copy_asset_content(source, target, entry.kind)
            if marker is not None and marker.is_symlink():
                raise AssetSyncError("The resource ownership sidecar became a symbolic link.")
            written_marker = write_managed_marker(
                target,
                marker_entry,
                platform=plan.platform,
                file_target=marker is not None,
            )
            if written_marker is None or not is_cc_port_managed(
                target,
                resource_key=marker_entry.resource_key,
                file_target=marker is not None,
            ):
                raise AssetSyncError("Downloaded asset ownership verification failed.")
            if _asset_resource_fingerprint(
                verification_source,
                entry.kind,
            ) != _asset_resource_fingerprint(target, entry.kind):
                raise AssetSyncError("Downloaded asset verification failed.")
        record = transaction.complete(
            message=f"Applied {plan.action} for {plan.target_resource_key}."
        )
    except Exception as exc:
        transaction.rollback(str(exc))
        raise

    message_ref = ui_message(
        "asset.result.applied",
        f"Applied {plan.action} for {plan.target_resource_key}.",
        action=plan.action,
        resource_key=plan.target_resource_key,
    )
    return AssetActionResult(
        operation_id=plan.operation_id,
        action=plan.action,
        status="succeeded",
        resource_key=plan.resource_key,
        target_resource_key=plan.target_resource_key,
        platform=plan.platform,
        message=message_ref.fallback,
        message_ref=message_ref,
        remote_commit=snapshot.commit,
        local_path=target,
        replayed_on_latest=snapshot.commit != plan.remote_commit,
        warnings=plan.warnings,
        warning_refs=plan.warning_refs,
        operation_status=record.status,
    )


def _apply_plugin_content_download(
    plan: AssetActionPlan,
    cfg: Config,
    snapshot: RemoteSnapshot,
    entry: RegistryItem,
) -> AssetActionResult:
    spec = entry.plugin
    target = plan.target_path
    if spec is None or target is None:
        raise _StaleAssetTarget("stale-platform", "The plugin content target is unavailable.")
    source = _plugin_remote_content_source(_remote_content_path(snapshot.root, entry), spec)
    if source is None or not source.exists() or source.is_symlink():
        raise AssetSyncError("The remote plugin content is unavailable or unsafe.")
    if spec.platform == "claude-code":
        metadata = inspect_claude_plugin(source, require_manifest=True)
        if metadata.name != spec.plugin_id:
            raise AssetSyncError(
                "The Claude plugin manifest name no longer matches the planned plugin id."
            )
    current_exists = target.exists() and not target.is_symlink()
    current_fingerprint = resource_hash_path(target) if current_exists else ""
    current_managed = bool(
        current_exists
        and target.is_dir()
        and is_cc_port_managed(target, resource_key=plan.target_resource_key)
    )
    if (
        current_exists != plan.target_exists
        or current_fingerprint != plan.target_fingerprint
        or current_managed != plan.target_managed
    ):
        raise _StaleAssetTarget(
            "stale-local-target",
            "The local plugin target changed after planning.",
        )
    if current_exists and not current_managed and not plan.overwrite_unmanaged:
        raise _StaleAssetTarget(
            "unmanaged-target",
            "The plugin target is unmanaged and overwrite was not confirmed.",
        )
    package_json = _opencode_dependency_target(target, spec)
    targets = [
        ChangeTarget(
            path=target,
            change_action=plan.action,
            resource=plan.target_resource_key,
            platform=plan.platform,
        )
    ]
    claude_state_path: Path | None = None
    claude_installation: PluginInstallation | None = None
    if spec.platform == "claude-code":
        raw_plugin = plan.plugin_data.get("plugin")
        planned_spec = PluginSpec.model_validate(raw_plugin)
        if len(planned_spec.installations) != 1:
            raise _StaleAssetTarget(
                "stale-target",
                "The Claude content plugin plan does not identify one installation.",
            )
        claude_installation = planned_spec.installations[0]
        profile = cfg.platforms.get(plan.platform)
        if profile is None:
            raise _StaleAssetTarget(
                "stale-platform",
                "The Claude plugin profile is unavailable.",
            )
        expected_state_path, expected_project_root = _claude_plugin_state_target(
            cfg,
            profile,
            claude_installation,
        )
        asserted_state_path = Path(str(plan.plugin_data.get("state_path") or ""))
        asserted_project_root = str(plan.plugin_data.get("project_root") or "")
        if (
            expected_state_path is None
            or not _same_local_path(expected_state_path, asserted_state_path)
            or (
                asserted_project_root
                and (
                    expected_project_root is None
                    or not _same_local_path(
                        expected_project_root,
                        Path(asserted_project_root),
                    )
                )
            )
        ):
            raise _StaleAssetTarget(
                "stale-local-target",
                "The Claude plugin settings target changed after planning.",
            )
        state_exists = asserted_state_path.is_file() and not asserted_state_path.is_symlink()
        if state_exists != bool(plan.plugin_data.get("state_exists")) or (
            state_exists
            and resource_hash_path(asserted_state_path)
            != str(plan.plugin_data.get("state_fingerprint") or "")
        ):
            raise _StaleAssetTarget(
                "stale-local-target",
                "The Claude plugin settings changed after planning.",
            )
        claude_state_path = asserted_state_path
        targets.append(
            ChangeTarget(
                path=claude_state_path,
                change_action="align-plugin-state",
                resource=plan.target_resource_key,
                platform=plan.platform,
            )
        )
    if package_json is not None and spec.dependencies:
        targets.append(
            ChangeTarget(
                path=package_json,
                change_action="merge-plugin-dependencies",
                resource=plan.target_resource_key,
                platform=plan.platform,
            )
        )
    transaction = LocalChangeTransaction.begin(
        "asset-download-plugin-content",
        targets,
        metadata={
            "asset_plan": plan.operation_id,
            "resource_key": plan.resource_key,
            "platform": plan.platform,
        },
        lock_timeout_seconds=cfg.state.lock_timeout_seconds,
    )
    transaction.mark_attempted(item.path for item in targets)
    try:
        if target.suffix.lower() in {".js", ".ts"} and source.is_file():
            if target.exists():
                _remove_asset_path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            copy_resource_tree(source, target)
        else:
            _copy_asset_content(source, target, "plugin")
            if target.is_dir():
                write_managed_marker(target, entry, platform=plan.platform)
        if package_json is not None and spec.dependencies:
            _merge_package_dependencies(package_json, spec.dependencies)
        if claude_state_path is not None and claude_installation is not None:
            _write_claude_plugin_state(
                claude_state_path,
                spec,
                claude_installation.enabled,
            )
        if resource_hash_path(source) != resource_hash_path(target):
            raise AssetSyncError("Downloaded plugin content verification failed.")
        if (
            claude_state_path is not None
            and claude_installation is not None
            and not _configured_plugin_state_matches(
                claude_state_path,
                spec,
                claude_installation.enabled,
            )
        ):
            raise AssetSyncError("Claude skills-directory plugin state verification failed.")
        record = transaction.complete(
            message=f"Downloaded plugin content for {plan.target_resource_key}."
        )
    except Exception as exc:
        transaction.rollback(str(exc))
        raise
    message_ref = ui_message(
        "asset.result.plugin_downloaded",
        f"Downloaded plugin content for {plan.target_resource_key}.",
        resource_key=plan.target_resource_key,
    )
    return AssetActionResult(
        operation_id=plan.operation_id,
        action=plan.action,
        status="succeeded",
        resource_key=plan.resource_key,
        target_resource_key=plan.target_resource_key,
        platform=plan.platform,
        message=message_ref.fallback,
        message_ref=message_ref,
        remote_commit=snapshot.commit,
        local_path=target,
        replayed_on_latest=snapshot.commit != plan.remote_commit,
        warnings=plan.warnings,
        warning_refs=plan.warning_refs,
        operation_status=record.status,
    )


def _apply_plugin_reference_state(
    plan: AssetActionPlan,
    cfg: Config,
    snapshot: RemoteSnapshot,
    entry: RegistryItem,
) -> AssetActionResult:
    spec = entry.plugin
    raw_alignments = plan.plugin_data.get("alignments", [])
    if spec is None or spec.track != "reference" or not isinstance(raw_alignments, list):
        raise _StaleAssetTarget("stale-target", "The plugin reference alignment is unavailable.")
    alignments = [item for item in raw_alignments if isinstance(item, dict)]
    if not alignments:
        raise _StaleAssetTarget("stale-target", "The plugin reference alignment is empty.")
    requires_native_cli = any(
        str(item.get("method") or "") in {"claude-install", "claude-cli"} for item in alignments
    )
    configured_profile = cfg.platforms.get(plan.platform)
    if requires_native_cli and (
        configured_profile is None
        or not configured_profile.enabled
        or configured_profile.effective_tool_id != spec.platform
    ):
        raise _StaleAssetTarget(
            "stale-platform",
            "The selected plugin runtime profile is no longer enabled.",
        )
    profile = configured_profile or build_platform(spec.platform)
    targets: list[ChangeTarget] = []
    for alignment in alignments:
        method = str(alignment.get("method") or "")
        scope = str(alignment.get("scope") or "")
        project = alignment.get("project")
        enabled = bool(alignment.get("enabled"))
        desired = next(
            (
                item
                for item in spec.installations
                if item.scope == scope
                and (item.project.model_dump(mode="json") if item.project else None) == project
            ),
            None,
        )
        if desired is None or desired.enabled != enabled:
            raise _StaleAssetTarget(
                "stale-target",
                "The desired plugin installation state changed after planning.",
            )
        state_path = Path(str(alignment.get("state_path") or ""))
        if method in {"claude-install", "claude-cli"}:
            expected_state_path, expected_project_root = _claude_plugin_state_target(
                cfg,
                profile,
                desired,
            )
            asserted_project_root = str(alignment.get("project_root") or "")
            if (
                expected_state_path is None
                or not _same_local_path(expected_state_path, state_path)
                or (
                    asserted_project_root
                    and (
                        expected_project_root is None
                        or not _same_local_path(
                            expected_project_root,
                            Path(asserted_project_root),
                        )
                    )
                )
            ):
                raise _StaleAssetTarget(
                    "stale-local-target",
                    "The Claude plugin installation target changed after planning.",
                )
            state_exists = state_path.is_file() and not state_path.is_symlink()
            if state_exists != bool(alignment.get("state_exists")) or (
                state_exists
                and resource_hash_path(state_path) != str(alignment.get("state_fingerprint") or "")
            ):
                raise _StaleAssetTarget(
                    "stale-local-target",
                    "The Claude plugin settings changed after planning.",
                )
        else:
            if not state_path.is_file() or state_path.is_symlink():
                raise _StaleAssetTarget(
                    "stale-local-target",
                    "The plugin state file is unavailable.",
                )
            if resource_hash_path(state_path) != str(alignment.get("state_fingerprint") or ""):
                raise _StaleAssetTarget(
                    "stale-local-target",
                    "The plugin state file changed after planning.",
                )
        targets.append(
            ChangeTarget(
                path=state_path,
                change_action="align-plugin-state",
                resource=entry.resource_key,
                platform=plan.platform,
            )
        )
    transaction = LocalChangeTransaction.begin(
        "plugin-state-align",
        targets,
        metadata={
            "asset_plan": plan.operation_id,
            "resource_key": entry.resource_key,
            "platform": spec.platform,
        },
        lock_timeout_seconds=cfg.state.lock_timeout_seconds,
    )
    transaction.mark_attempted(item.path for item in targets)
    try:
        for alignment in alignments:
            method = str(alignment.get("method") or "")
            state_path = Path(str(alignment.get("state_path") or ""))
            enabled = bool(alignment.get("enabled"))
            scope = str(alignment.get("scope") or "user")
            if method == "claude-install":
                project_root_value = str(alignment.get("project_root") or "")
                desired = next(
                    item
                    for item in spec.installations
                    if item.scope == scope
                    and (item.project.model_dump(mode="json") if item.project else None)
                    == alignment.get("project")
                )
                install_marketplace_plugin(
                    profile,
                    spec,
                    desired,
                    project_root=(Path(project_root_value) if project_root_value else None),
                )
            elif method == "claude-cli":
                project_root_value = str(alignment.get("project_root") or "")
                desired = next(
                    item
                    for item in spec.installations
                    if item.scope == scope
                    and (item.project.model_dump(mode="json") if item.project else None)
                    == alignment.get("project")
                )
                set_marketplace_plugin_enabled(
                    profile,
                    spec,
                    desired,
                    project_root=(Path(project_root_value) if project_root_value else None),
                )
            elif method == "claude-config":
                _write_claude_plugin_state(state_path, spec, enabled)
            elif method == "opencode-config":
                _write_opencode_plugin_state(state_path, spec, enabled)
            elif method == "codex-config":
                _write_codex_plugin_state(state_path, spec, enabled)
            else:
                raise AssetSyncError(f"Unsupported plugin state alignment method: {method}")
        for alignment in alignments:
            method = str(alignment.get("method") or "")
            state_path = Path(str(alignment.get("state_path") or ""))
            enabled = bool(alignment.get("enabled"))
            scope = str(alignment.get("scope") or "user")
            verified = _configured_plugin_state_matches(state_path, spec, enabled)
            if not verified:
                raise AssetSyncError(
                    f"Plugin state verification failed for {spec.platform} {scope}."
                )
        record = transaction.complete(
            message=f"Aligned {len(alignments)} plugin installation state(s)."
        )
    except Exception as exc:
        transaction.rollback(str(exc))
        raise
    manual = [str(item) for item in plan.plugin_data.get("manual", []) if str(item)]
    message = f"Aligned {len(alignments)} plugin installation state(s)."
    if manual:
        message += " Manual actions remain: " + "; ".join(manual)
    message_ref = ui_message(
        "asset.result.plugin_state_aligned",
        message,
        count=len(alignments),
        detail="; ".join(manual),
    )
    return AssetActionResult(
        operation_id=plan.operation_id,
        action=plan.action,
        status="partial" if manual else "succeeded",
        resource_key=plan.resource_key,
        target_resource_key=plan.target_resource_key,
        platform=plan.platform,
        message=message,
        message_ref=message_ref,
        remote_commit=snapshot.commit,
        local_path=targets[0].path if targets else None,
        replayed_on_latest=snapshot.commit != plan.remote_commit,
        warnings=[*plan.warnings, *manual],
        warning_refs=plan.warning_refs,
        operation_status=record.status,
    )


def _plugin_reference_source_label(spec: PluginSpec) -> str:
    if spec.origin.type == "marketplace":
        return f"{spec.plugin_id}@{spec.origin.marketplace}"
    if spec.origin.type == "npm":
        return spec.origin.package
    if spec.platform == "claude-code" and spec.origin.type == "local":
        return f"{spec.plugin_id}@skills-dir"
    return spec.plugin_id


def _write_claude_plugin_state(path: Path, spec: PluginSpec, enabled: bool) -> None:
    if path.is_symlink():
        raise AssetSyncError("Claude settings must not be a symbolic link.")
    payload = _read_json_object(path, label="Claude settings") if path.is_file() else {}
    current = payload.get("enabledPlugins", {})
    if current is None:
        current = {}
    if not isinstance(current, dict):
        raise AssetSyncError("Claude enabledPlugins is not an object.")
    current[_plugin_reference_source_label(spec)] = enabled
    payload["enabledPlugins"] = current
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_opencode_plugin_state(path: Path, spec: PluginSpec, enabled: bool) -> None:
    payload = _read_json_object(path, label="OpenCode configuration")
    declared = payload.get("plugin", [])
    if not isinstance(declared, list):
        raise AssetSyncError("OpenCode plugin declarations are not a list.")
    package = spec.origin.package
    remaining = [value for value in declared if _split_plugin_package(str(value))[0] != package]
    if enabled:
        remaining.append(f"{package}@{spec.origin.selector}" if spec.origin.selector else package)
    payload["plugin"] = remaining
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_codex_plugin_state(path: Path, spec: PluginSpec, enabled: bool) -> None:
    try:
        with path.open("rb") as handle:
            before = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AssetSyncError("Codex config.toml cannot be parsed safely.") from exc
    qualified = _plugin_reference_source_label(spec)
    plugins = before.get("plugins", {}) if isinstance(before, dict) else {}
    if not isinstance(plugins, dict) or qualified not in plugins:
        raise AssetSyncError("The Codex plugin section is missing from config.toml.")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    header = f"[plugins.{json.dumps(qualified, ensure_ascii=False)}]"
    section_start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip() == header or line.strip().startswith(header + " #")
        ),
        None,
    )
    if section_start is None:
        raise AssetSyncError("The Codex plugin section cannot be edited without rewriting TOML.")
    section_end = next(
        (
            index
            for index in range(section_start + 1, len(lines))
            if lines[index].lstrip().startswith("[")
        ),
        len(lines),
    )
    enabled_line = next(
        (
            index
            for index in range(section_start + 1, section_end)
            if re.match(r"^\s*enabled\s*=", lines[index])
        ),
        None,
    )
    newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
    replacement = f"enabled = {'true' if enabled else 'false'}{newline}"
    if enabled_line is None:
        lines.insert(section_start + 1, replacement)
    else:
        indent = lines[enabled_line][: len(lines[enabled_line]) - len(lines[enabled_line].lstrip())]
        comment = ""
        if "#" in lines[enabled_line]:
            comment = " #" + lines[enabled_line].split("#", 1)[1].rstrip("\r\n")
        lines[enabled_line] = (
            f"{indent}enabled = {'true' if enabled else 'false'}{comment}{newline}"
        )
    updated = "".join(lines)
    try:
        parsed = tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise AssetSyncError("The Codex plugin state edit would produce invalid TOML.") from exc
    updated_plugins = parsed.get("plugins", {}) if isinstance(parsed, dict) else {}
    state = updated_plugins.get(qualified, {}) if isinstance(updated_plugins, dict) else {}
    if not isinstance(state, dict) or bool(state.get("enabled", True)) is not enabled:
        raise AssetSyncError("The Codex plugin state edit could not be verified.")
    path.write_text(updated, encoding="utf-8", newline="")


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetSyncError(f"{label} cannot be parsed safely.") from exc
    if not isinstance(payload, dict):
        raise AssetSyncError(f"{label} is not an object.")
    return payload


def _configured_plugin_state_matches(
    path: Path,
    spec: PluginSpec,
    enabled: bool,
) -> bool:
    if spec.platform == "codex":
        try:
            with path.open("rb") as handle:
                toml_payload = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return False
        plugins = toml_payload.get("plugins", {}) if isinstance(toml_payload, dict) else {}
        state = (
            plugins.get(_plugin_reference_source_label(spec), {})
            if isinstance(plugins, dict)
            else {}
        )
        return isinstance(state, dict) and bool(state.get("enabled", True)) is enabled
    try:
        payload = _read_json_object(path, label="Plugin configuration")
    except AssetSyncError:
        return False
    if spec.platform == "claude-code":
        current = payload.get("enabledPlugins", {})
        return (
            isinstance(current, dict)
            and current.get(_plugin_reference_source_label(spec)) is enabled
        )
    if spec.platform == "opencode":
        declared = payload.get("plugin", [])
        if not isinstance(declared, list):
            return False
        present = any(
            _split_plugin_package(str(value))[0] == spec.origin.package for value in declared
        )
        return present is enabled
    return False


def _opencode_dependency_target(target: Path, spec: PluginSpec) -> Path | None:
    if spec.platform != "opencode" or not spec.dependencies:
        return None
    plugins_dir = (
        target.parent
        if target.is_file() or target.suffix.lower() in {".js", ".ts"}
        else target.parent
    )
    return plugins_dir.parent / "package.json"


def _merge_package_dependencies(path: Path, dependencies: dict[str, str]) -> None:
    payload: dict[str, Any] = {}
    if path.is_file():
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise AssetSyncError("Target package.json is not an object.")
        payload = value
    current = payload.get("dependencies", {})
    if current is None:
        current = {}
    if not isinstance(current, dict):
        raise AssetSyncError("Target package.json dependencies are not an object.")
    payload["dependencies"] = {**current, **dependencies}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _assert_remote_plan_platform_identity(
    plan: AssetActionPlan,
    cfg: Config,
) -> None:
    """Revalidate the local-only profile behind a portable remote mutation."""
    profile = cfg.platforms.get(plan.platform)
    if profile is None or not profile.enabled:
        raise _StaleAssetTarget(
            "stale-platform",
            "The source platform is no longer configured and enabled.",
        )
    if (
        plan.tool_id != profile.effective_tool_id
        or plan.environment_kind != profile.environment_kind
        or plan.environment_name != profile.environment_name
    ):
        raise _StaleAssetTarget(
            "stale-platform",
            "The source platform identity changed after planning.",
        )
    environment_available, environment_problem = _profile_environment_state(profile)
    if not environment_available:
        raise _StaleAssetTarget(
            "stale-platform",
            environment_problem or "The source runtime environment is unavailable.",
        )
    try:
        portable_tool_id = validate_portable_tool_id(plan.tool_id)
        if plan.kind in {"instruction", "memory"}:
            resolved = resolve_portable_resource_platforms(
                cfg.platforms,
                plan.kind,
                [portable_tool_id],
            )
            if resolved != [portable_tool_id]:
                raise ValueError("The source tool binding is not portable.")
    except ValueError as exc:
        raise _StaleAssetTarget("stale-platform", str(exc)) from exc


def _apply_remote_asset_action(
    plan: AssetActionPlan,
    cfg: Config,
) -> AssetActionResult:
    _assert_remote_plan_platform_identity(plan, cfg)
    blocker_ref = _legacy_write_blocker_message(cfg, fetch=True)
    if blocker_ref:
        return AssetActionResult(
            operation_id=plan.operation_id,
            action=plan.action,
            status="blocked",
            resource_key=plan.resource_key,
            target_resource_key=plan.target_resource_key,
            platform=plan.platform,
            message=blocker_ref.fallback,
            message_ref=blocker_ref,
            warnings=plan.warnings,
            warning_refs=plan.warning_refs,
        )
    repo_url = _configured_remote_url(cfg)
    if not repo_url:
        message_ref = ui_message(
            "asset.result.remote_not_configured",
            "No remote resource repository URL is configured.",
        )
        return AssetActionResult(
            operation_id=plan.operation_id,
            action=plan.action,
            status="blocked",
            resource_key=plan.resource_key,
            target_resource_key=plan.target_resource_key,
            platform=plan.platform,
            message=message_ref.fallback,
            message_ref=message_ref,
            warnings=plan.warnings,
            warning_refs=plan.warning_refs,
        )

    last_push_error: Exception | None = None
    for attempt in range(2):
        with tempfile.TemporaryDirectory(
            prefix="cc-port-asset-write-",
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
                if not _planned_local_source_matches(source_row, plan):
                    raise _StaleAssetTarget(
                        "stale-local-source",
                        "The local source changed after planning.",
                    )
                _validate_remote_batch_source(source_row, target_key.kind)

            changed = _mutate_remote_asset(
                worktree,
                registry,
                plan,
                source_row,
            )
            if not changed:
                message_ref = ui_message(
                    "asset.result.remote_already_matches",
                    "The requested remote state already matches.",
                )
                return AssetActionResult(
                    operation_id=plan.operation_id,
                    action=plan.action,
                    status="unchanged",
                    resource_key=plan.resource_key,
                    target_resource_key=plan.target_resource_key,
                    platform=plan.platform,
                    message=message_ref.fallback,
                    message_ref=message_ref,
                    remote_commit=latest_commit,
                    replayed_on_latest=latest_commit != plan.remote_commit,
                    push_retry_count=attempt,
                    warnings=plan.warnings,
                    warning_refs=plan.warning_refs,
                )

            commit_resource_changes_unlocked(
                worktree,
                message=_asset_commit_message(plan),
                config=cfg,
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
            message_ref = ui_message(
                "asset.result.remote_applied",
                f"Applied {plan.action} and pushed one asset-level commit.",
                action=plan.action,
            )
            return AssetActionResult(
                operation_id=plan.operation_id,
                action=plan.action,
                status="succeeded",
                resource_key=plan.resource_key,
                target_resource_key=plan.target_resource_key,
                platform=plan.platform,
                message=message_ref.fallback,
                message_ref=message_ref,
                remote_commit=committed,
                replayed_on_latest=latest_commit != plan.remote_commit,
                push_retry_count=attempt,
                warnings=plan.warnings,
                warning_refs=plan.warning_refs,
            )
    raise AssetSyncError(str(last_push_error or "Remote push failed."))


def _apply_remote_asset_batch(
    plans: list[AssetActionPlan],
    cfg: Config,
) -> list[AssetActionResult]:
    if not plans:
        return []
    blocker_ref = _legacy_write_blocker_message(cfg, fetch=True)
    if blocker_ref:
        return [
            _batch_error_result(
                plan,
                "blocked",
                blocker_ref.fallback,
                blocker_ref,
            )
            for plan in plans
        ]
    repo_url = _configured_remote_url(cfg)
    if not repo_url:
        message_ref = ui_message(
            "asset.result.remote_not_configured",
            "No remote resource repository URL is configured.",
        )
        return [
            _batch_error_result(
                plan,
                "blocked",
                message_ref.fallback,
                message_ref,
            )
            for plan in plans
        ]

    last_push_error: Exception | None = None
    for attempt in range(2):
        with tempfile.TemporaryDirectory(
            prefix="cc-port-asset-batch-write-",
            ignore_cleanup_errors=True,
        ) as temporary:
            worktree = Path(temporary) / "repo"
            _clone_remote_for_write(repo_url, worktree, cfg)
            registry_path = worktree / DEFAULT_REGISTRY_FILENAME
            if not registry_path.is_file():
                ensure_structure(worktree)
            registry = load_registry(registry_path)
            latest_commit = git_ops.head_commit(worktree) or ""
            current_snapshot = RemoteSnapshot(
                root=worktree,
                registry=registry,
                commit=latest_commit,
                branch=cfg.resources.branch or "main",
                repo_url=repo_url,
            )
            inventory = build_asset_inventory(
                config=cfg,
                scan_local=True,
                refresh_remote=False,
                remote_snapshot=current_snapshot,
            )
            attempt_results: list[AssetActionResult] = []
            prepared: list[tuple[AssetActionPlan, AssetPlatformRow]] = []
            for plan in plans:
                try:
                    _assert_remote_plan_platform_identity(plan, cfg)
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
                    source_row = _find_planned_local_row(inventory, plan)
                    if not _planned_local_source_matches(source_row, plan):
                        raise _StaleAssetTarget(
                            "stale-local-source",
                            "The local source changed after planning.",
                        )
                    if plan.plugin_data:
                        source_row.plugin_data = json.loads(json.dumps(plan.plugin_data))
                    _validate_remote_batch_source(source_row, target_key.kind)
                    prepared.append((plan, source_row))
                except _StaleAssetTarget as exc:
                    detail = str(exc)
                    attempt_results.append(
                        _batch_error_result(
                            plan,
                            exc.code,
                            detail,
                            ui_message("asset.result.stale", detail, detail=detail),
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - exclude invalid source and continue
                    attempt_results.append(_batch_error_result(plan, "failed", str(exc)))

            changed: list[AssetActionPlan] = []
            try:
                for plan, source_row in prepared:
                    if _mutate_remote_asset(
                        worktree,
                        registry,
                        plan,
                        source_row,
                    ):
                        changed.append(plan)
                    else:
                        message_ref = ui_message(
                            "asset.result.remote_already_matches",
                            "The requested remote state already matches.",
                        )
                        attempt_results.append(
                            AssetActionResult(
                                operation_id=plan.operation_id,
                                action=plan.action,
                                status="unchanged",
                                resource_key=plan.resource_key,
                                target_resource_key=plan.target_resource_key,
                                platform=plan.platform,
                                message=message_ref.fallback,
                                message_ref=message_ref,
                                remote_commit=latest_commit,
                                replayed_on_latest=latest_commit != plan.remote_commit,
                                push_retry_count=attempt,
                                warnings=plan.warnings,
                                warning_refs=plan.warning_refs,
                            )
                        )
            except Exception as exc:  # noqa: BLE001 - discard the uncommitted worktree
                return [
                    *attempt_results,
                    *[
                        _batch_error_result(plan, "failed", str(exc))
                        for plan, _source_row in prepared
                    ],
                ]
            if not changed:
                return attempt_results
            try:
                commit_resource_changes_unlocked(
                    worktree,
                    message=f"cc-port: batch upload {len(changed)} assets",
                    config=cfg,
                )
            except Exception as exc:  # noqa: BLE001 - no remote write occurred
                return [
                    *attempt_results,
                    *[_batch_error_result(plan, "failed", str(exc)) for plan in changed],
                ]
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
                return [
                    *attempt_results,
                    *[_batch_error_result(plan, "failed", str(exc)) for plan in changed],
                ]
            return [
                *attempt_results,
                *[
                    AssetActionResult(
                        operation_id=plan.operation_id,
                        action=plan.action,
                        status="succeeded",
                        resource_key=plan.resource_key,
                        target_resource_key=plan.target_resource_key,
                        platform=plan.platform,
                        message=f"Applied {plan.action} in one batch commit.",
                        message_ref=ui_message(
                            "asset.result.batch_applied",
                            f"Applied {plan.action} in one batch commit.",
                            action=plan.action,
                        ),
                        remote_commit=committed,
                        replayed_on_latest=latest_commit != plan.remote_commit,
                        push_retry_count=attempt,
                        warnings=plan.warnings,
                        warning_refs=plan.warning_refs,
                    )
                    for plan in changed
                ],
            ]
    return [
        _batch_error_result(
            plan,
            "failed",
            str(last_push_error or "Remote batch push failed."),
        )
        for plan in plans
    ]


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
    git_ops.configure_host_autocrlf_disabled_checkout(destination)


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


def _planned_local_source_matches(
    row: AssetPlatformRow,
    plan: AssetActionPlan,
) -> bool:
    content_path = row.local_content_path or row.local_path
    return (
        row.local_exists
        and row.local_fingerprint == plan.local_source_fingerprint
        and _same_local_path(row.local_path, plan.source_path)
        and _same_local_path(content_path, plan.source_content_path)
        and row.path_kind == plan.source_path_kind
        and row.link_health == plan.source_link_health
        and row.link_target == plan.source_link_target
        and row.reparse_tag == plan.source_reparse_tag
        and row.tool_id == plan.tool_id
        and row.environment_kind == plan.environment_kind
        and row.environment_name == plan.environment_name
    )


def _same_local_path(left: Path | None, right: Path | None) -> bool:
    if left is None or right is None:
        return left is right
    return os.path.normcase(str(left.expanduser().absolute())) == os.path.normcase(
        str(right.expanduser().absolute())
    )


def _validate_remote_batch_source(row: AssetPlatformRow, kind: ItemKind) -> None:
    if kind == "plugin" and row.plugin_data:
        spec = PluginSpec.model_validate(row.plugin_data.get("plugin"))
        if spec.track == "reference":
            return
        plugin_source_path = row.local_content_path or row.local_path
        if plugin_source_path is not None and "cache" in {
            part.lower() for part in plugin_source_path.parts
        }:
            raise AssetSyncError("Plugin cache content is never an uploadable source.")
        if spec.platform == "claude-code":
            if plugin_source_path is None:
                raise AssetSyncError("The Claude plugin source is unavailable.")
            metadata = inspect_claude_plugin(
                plugin_source_path,
                require_manifest=True,
            )
            if metadata.name != spec.plugin_id:
                raise AssetSyncError(
                    "The Claude plugin manifest name does not match the discovered plugin id."
                )
    source = row.local_content_path or row.local_path
    if source is None:
        raise AssetSyncError("The local source is unavailable.")
    if kind == "mcp":
        sanitized = sanitize_mcp_config_for_storage(_read_mcp_server(source, row.install_name))
        if not sanitized:
            raise AssetSyncError("The local MCP source cannot be parsed safely.")
        validate_item(source, "mcp", mcp_config=sanitized)
        _raise_for_batch_secret(
            json.dumps(sanitized, ensure_ascii=False),
            display_path=f"{row.resource_key}/mcp.json",
        )
        return

    if kind == "skill" and row.tool_id == "claude-code":
        inspect_claude_skill(source)
    else:
        validate_item(source, kind)
    candidates = [source] if source.is_file() else sorted(source.rglob("*"))
    for candidate in candidates:
        if not candidate.is_file() or candidate.is_symlink():
            continue
        relative = candidate.name if source.is_file() else candidate.relative_to(source)
        if kind != "memory" and is_resource_path_excluded(Path(relative)):
            continue
        try:
            raw = candidate.read_bytes()
        except OSError as exc:
            raise AssetSyncError(f"The local source cannot be read safely: {exc}") from exc
        if b"\x00" in raw[: min(len(raw), 4096)]:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        _raise_for_batch_secret(text, display_path=str(relative))


def _raise_for_batch_secret(text: str, *, display_path: str) -> None:
    finding = find_secret_text(text)
    if finding is not None:
        raise AssetSyncError(f"Secret-like content in {display_path}: {finding.reason}")


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
        if target_key.kind == "memory":
            raise AssetSyncError(
                "Memory project-slot mappings are local-only and cannot be stored remotely."
            )
        if existing is None or not _is_private_repo_asset(existing):
            raise _StaleAssetTarget(
                "stale-target",
                "The target asset is no longer writable in the private repository.",
            )
        portable_platform = validate_portable_tool_id(plan.tool_id)
        if not portable_platform:
            raise AssetSyncError(
                "The portable source tool identity is unavailable; local profile ids cannot be stored remotely."
            )
        if existing.platform_install_dirs.get(portable_platform) == plan.new_install_name:
            return False
        updated = existing.model_copy(deep=True)
        updated.platform_install_dirs[portable_platform] = plan.new_install_name
        registry.upsert(updated)
        save_registry(registry, registry_path)
        return True

    plugin_spec: PluginSpec | None = None
    plugin_selector_known = True
    if target_key.kind == "plugin":
        planned_plugin = plan.plugin_data.get("plugin") if plan.plugin_data else None
        scanned_plugin = (
            source_row.plugin_data.get("plugin")
            if source_row is not None and source_row.plugin_data
            else None
        )
        plugin_payload = planned_plugin or scanned_plugin
        if plugin_payload is not None:
            plugin_spec = PluginSpec.model_validate(plugin_payload)
            plugin_selector_known = bool(
                plan.plugin_data.get("selector_known", True)
                if plan.plugin_data
                else source_row.plugin_data.get("selector_known", True)
                if source_row is not None
                else True
            )
    if plugin_spec is not None:
        if plugin_spec.track == "reference":
            return _mutate_plugin_reference(
                registry,
                registry_path,
                target_key,
                existing,
                plugin_spec,
                description=str(source_row.plugin_data.get("description") or ""),
                preserve_selector=not plugin_selector_known,
            )

    if source_row is None or source_row.local_path is None:
        raise _StaleAssetTarget("stale-local-source", "The local source is unavailable.")
    source_tool_id = ""
    if target_key.kind in {"instruction", "memory"}:
        raw_source_tool_id = source_row.tool_id.strip()
        if not raw_source_tool_id:
            raise AssetSyncError(
                "The portable source tool identity is unavailable; local profile ids cannot be stored remotely."
            )
        try:
            source_tool_id = validate_portable_tool_id(raw_source_tool_id)
        except ValueError as exc:
            raise AssetSyncError(str(exc)) from exc
        if target_key.kind == "memory" and source_tool_id != "claude-code":
            raise AssetSyncError("Memory resources can only originate from Claude Code.")
    local_path = source_row.local_content_path or source_row.local_path
    local_mcp_config = (
        _read_mcp_server(local_path, source_row.install_name) if target_key.kind == "mcp" else None
    )
    if target_key.kind == "mcp":
        local_mcp_config = sanitize_mcp_config_for_storage(local_mcp_config)
        if not local_mcp_config:
            raise AssetSyncError("The local MCP source cannot be parsed safely.")
        validate_item(local_path, "mcp", mcp_config=local_mcp_config)
    elif target_key.kind == "skill" and source_row.tool_id == "claude-code":
        inspect_claude_skill(local_path)
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
    safe_destination = _safe_snapshot_member_path(root_abs, destination)
    if safe_destination is None:
        raise AssetSyncError(
            "The remote asset path crosses a symbolic link or leaves the resource repository."
        )
    destination = safe_destination

    if target_key.kind == "mcp":
        if destination.exists():
            _remove_asset_path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "mcp.json").write_text(
            json.dumps(local_mcp_config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    else:
        with _ordinary_upload_snapshot(
            local_path,
            logical_path=source_row.local_path,
            expected_fingerprint=plan.local_source_fingerprint,
            kind=target_key.kind,
        ) as snapshot_path:
            _copy_asset_content(snapshot_path, destination, target_key.kind)

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
    if target_key.kind in {"instruction", "memory"}:
        updated.platforms = [source_tool_id]
    if target_key.kind == "plugin" and plugin_spec is not None:
        updated.plugin = _merge_plugin_installations(
            existing.plugin if existing else None, plugin_spec
        )
    for field_name in DERIVED_METADATA_FIELDS:
        value = derived.get(field_name)
        if value not in (None, ""):
            setattr(updated, field_name, str(value))
    if target_key.kind == "mcp" and isinstance(derived.get("mcp_config"), dict):
        updated.mcp_config = derived["mcp_config"]
    registry.upsert(updated)
    save_registry(registry, registry_path)
    return True


def _mutate_plugin_reference(
    registry: Registry,
    registry_path: Path,
    target_key: ResourceKey,
    existing: RegistryItem | None,
    plugin_spec: PluginSpec,
    *,
    description: str,
    preserve_selector: bool = False,
) -> bool:
    claude_error = _claude_plugin_reference_error(
        platform=plugin_spec.platform,
        origin_type=plugin_spec.origin.type,
        marketplace=plugin_spec.origin.marketplace,
        source=plugin_spec.origin.source,
        selector=plugin_spec.origin.selector,
    )
    if claude_error:
        raise AssetSyncError(claude_error)
    duplicate = next(
        (
            item
            for item in registry.items
            if item.kind == "plugin"
            and item.plugin is not None
            and item.lifecycle == "active"
            and item.resource_key != str(target_key)
            and item.plugin.platform == plugin_spec.platform
            and item.plugin.plugin_id == plugin_spec.plugin_id
            and item.plugin.origin.type == plugin_spec.origin.type
            and _plugin_origin_source_id(item.plugin.origin)
            == _plugin_origin_source_id(plugin_spec.origin)
        ),
        None,
    )
    if duplicate is not None:
        raise _StaleAssetTarget(
            "stale-target",
            f"This plugin distribution already uses resource key {duplicate.resource_key}.",
        )
    if existing is not None and existing.plugin is None:
        raise _StaleAssetTarget(
            "stale-target",
            "A legacy or non-dual-track plugin already uses this resource key.",
        )
    if existing is not None and existing.plugin is not None:
        current = existing.plugin
        if (
            current.platform != plugin_spec.platform
            or current.plugin_id != plugin_spec.plugin_id
            or current.origin.type != plugin_spec.origin.type
            or _plugin_origin_source_id(current.origin)
            != _plugin_origin_source_id(plugin_spec.origin)
        ):
            raise _StaleAssetTarget(
                "stale-target",
                "The resource key now belongs to a different plugin source.",
            )
    merged = _merge_plugin_installations(
        existing.plugin if existing else None,
        plugin_spec,
        preserve_selector=preserve_selector,
    )
    updated = (
        existing.model_copy(deep=True)
        if existing is not None
        else RegistryItem(
            name=target_key.name,
            kind="plugin",
            source="external",
            path="",
            repo="",
            ref="",
            plugin=plugin_spec,
        )
    )
    updated.name = target_key.name
    updated.kind = "plugin"
    updated.source = "external"
    updated.path = ""
    updated.repo = plugin_spec.origin.repo if plugin_spec.origin.type == "git" else ""
    updated.ref = plugin_spec.origin.selector
    updated.plugin = merged
    if description:
        updated.description = description
    if existing is not None and updated.model_dump(mode="json") == existing.model_dump(mode="json"):
        return False
    registry.upsert(updated)
    save_registry(registry, registry_path)
    return True


def _merge_plugin_installations(
    existing: PluginSpec | None,
    incoming: PluginSpec,
    *,
    preserve_selector: bool = False,
) -> PluginSpec:
    if existing is None or (
        existing.platform != incoming.platform
        or existing.plugin_id != incoming.plugin_id
        or existing.origin.type != incoming.origin.type
        or _plugin_origin_source_id(existing.origin) != _plugin_origin_source_id(incoming.origin)
    ):
        return incoming
    merged = existing.model_copy(deep=True)
    merged.track = incoming.track
    incoming_origin = incoming.origin.model_copy(deep=True)
    if preserve_selector:
        incoming_origin.selector = existing.origin.selector
    merged.origin = incoming_origin
    merged.observed_version = incoming.observed_version
    merged.dependencies = dict(incoming.dependencies)
    by_identity: dict[tuple[str, str, str], PluginInstallation] = {}
    for installation in [*existing.installations, *incoming.installations]:
        project = installation.project
        key = (
            installation.scope,
            project.repo if project else "",
            project.subdir if project else "",
        )
        by_identity[key] = installation
    merged.installations = list(by_identity.values())
    return PluginSpec.model_validate(merged.model_dump(mode="json"))


def _asset_commit_message(plan: AssetActionPlan) -> str:
    if plan.action == "upload":
        return f"cc-port: update {plan.target_resource_key}"
    if plan.action == "copy-to-remote":
        return f"cc-port: create {plan.target_resource_key}"
    return f"cc-port: set install name for {plan.target_resource_key} on {plan.tool_id or 'tool'}"


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
            _json_fingerprint(sanitize_mcp_config_for_storage(config) or {}) if exists else ""
        )
        managed = (
            is_cc_port_managed_mcp(target, install_name, resource_key=resource_key)
            if exists
            else False
        )
        return exists, fingerprint, managed
    exists = target.exists() or target.is_symlink()
    fingerprint = (
        _asset_resource_fingerprint(target, kind) if exists and not target.is_symlink() else ""
    )
    managed = (
        is_cc_port_managed(
            target,
            resource_key=resource_key,
            file_target=_uses_sibling_marker(kind, target),
        )
        if exists
        else False
    )
    return exists, fingerprint, managed


def _copy_asset_content(source: Path, destination: Path, kind: ItemKind) -> None:
    if destination.is_symlink():
        _remove_asset_path(destination)
    if kind == "memory":
        validate_item(source, "memory")
        if destination.exists() or destination.is_symlink():
            _remove_asset_path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, symlinks=False)
        return
    if kind == "prompt" and _is_file_prompt_target(destination):
        payload = _installable_asset_source(source, destination, kind)
        copy_resource_tree(payload, destination)
        return
    if kind == "instruction" and destination.suffix.lower() == ".md":
        payload = _installable_asset_source(source, destination, kind)
        copy_resource_tree(payload, destination)
        return
    if kind == "instruction" and source.is_file():
        if destination.exists() or destination.is_symlink():
            _remove_asset_path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        copy_resource_tree(source, destination / source.name)
        return
    if source.is_file() and kind in {"rule", "prompt", "plugin"}:
        if destination.exists() or destination.is_symlink():
            _remove_asset_path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        copy_resource_tree(source, destination / source.name)
        return
    copy_resource_tree(source, destination)


@contextmanager
def _ordinary_upload_snapshot(
    source: Path,
    *,
    logical_path: Path,
    expected_fingerprint: str,
    kind: ItemKind = "skill",
) -> Iterator[Path]:
    issues = resource_tree_issues(source)
    if issues:
        raise _StaleAssetTarget(
            "stale-local-source",
            "The local source now contains nested links or unreadable entries.",
        )
    if _safe_local_resource_fingerprint(source, kind) != expected_fingerprint:
        raise _StaleAssetTarget(
            "stale-local-source",
            "The local source changed after planning.",
        )
    with tempfile.TemporaryDirectory(
        prefix="cc-port-upload-source-",
        ignore_cleanup_errors=True,
    ) as temporary:
        snapshot_path = Path(temporary) / (logical_path.name or "resource")
        if kind == "memory":
            validate_item(source, "memory")
            shutil.copytree(source, snapshot_path, symlinks=False)
        else:
            copy_resource_tree(source, snapshot_path)
        if resource_tree_issues(source):
            raise _StaleAssetTarget(
                "stale-local-source",
                "The local source changed while its upload snapshot was created.",
            )
        if _safe_local_resource_fingerprint(source, kind) != expected_fingerprint:
            raise _StaleAssetTarget(
                "stale-local-source",
                "The local source changed while its upload snapshot was created.",
            )
        yield snapshot_path


def _installable_asset_source(source: Path, destination: Path, kind: ItemKind) -> Path:
    if kind == "instruction":
        payload, problem = _instruction_payload_path(source)
        if payload is None:
            raise AssetSyncError(problem or "The instruction has no installable Markdown payload.")
        return payload
    if kind != "prompt" or not _is_file_prompt_target(destination):
        return source
    payload, problem = _prompt_payload_path(source)
    if payload is None:
        raise AssetSyncError(problem or "The Prompt has no installable Markdown payload.")
    return payload


def _file_asset_payload_problem(row: AssetPlatformRow) -> str:
    if row.kind == "instruction" and row.remote_path is not None:
        _payload, problem = _instruction_payload_path(row.remote_path)
        return problem
    if (
        row.kind != "prompt"
        or row.target_path is None
        or not _is_file_prompt_target(row.target_path)
        or row.remote_path is None
    ):
        return ""
    _payload, problem = _prompt_payload_path(row.remote_path)
    return problem


def _instruction_payload_path(source: Path | None) -> tuple[Path | None, str]:
    if source is None or not source.exists() or source.is_symlink():
        return None, "The instruction payload is unavailable or unsafe."
    if source.is_file():
        if source.suffix.lower() == ".md":
            return source, ""
        return None, "The instruction payload must be a Markdown file."
    markdown = sorted(
        item
        for item in source.iterdir()
        if item.is_file() and not item.is_symlink() and item.suffix.lower() == ".md"
    )
    if len(markdown) != 1:
        return (
            None,
            "An instruction resource requires exactly one root Markdown payload; "
            f"found {len(markdown)}.",
        )
    return markdown[0], ""


def _sibling_marker_problem(row: AssetPlatformRow) -> str:
    if row.target_path is None or not _uses_sibling_marker(row.kind, row.target_path):
        return ""
    marker = managed_marker_path(row.target_path, file_target=True)
    return "The asset ownership sidecar must not be a symbolic link." if marker.is_symlink() else ""


def _prompt_payload_path(source: Path | None) -> tuple[Path | None, str]:
    if source is None or not source.exists() or source.is_symlink():
        return None, "The Prompt payload is unavailable or unsafe."
    if source.is_file():
        if source.suffix.lower() == ".md":
            return source, ""
        return None, "The Prompt payload must be a Markdown file."
    markdown = sorted(
        item
        for item in source.iterdir()
        if item.is_file() and not item.is_symlink() and item.suffix.lower() == ".md"
    )
    if len(markdown) != 1:
        return (
            None,
            "A file-based Prompt requires exactly one root Markdown payload; "
            f"found {len(markdown)}.",
        )
    return markdown[0], ""


def _is_file_prompt_target(path: Path) -> bool:
    return path.suffix.lower() == ".md"


def _uses_sibling_marker(kind: str, path: Path) -> bool:
    return kind in {"instruction", "memory"} or (kind == "prompt" and _is_file_prompt_target(path))


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


def _read_portable_mcp_resource(path: Path) -> dict[str, Any] | None:
    candidates = (
        [path]
        if path.is_file()
        else [
            path / "mcp.json",
            path / "mcp.yaml",
            path / "mcp.yml",
        ]
    )
    for candidate in candidates:
        if not candidate.is_file() or candidate.is_symlink():
            continue
        try:
            payload = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(payload, dict):
            continue
        servers = payload.get("mcpServers")
        if isinstance(servers, dict) and len(servers) == 1:
            payload = next(iter(servers.values()))
        if not isinstance(payload, dict):
            continue
        sanitized = sanitize_mcp_config_for_storage(payload)
        if sanitized:
            return sanitized
    return None


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
        if value not in (None, "") and (not isinstance(value, str) or value.strip())
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
    ref = _legacy_write_blocker_message(cfg, fetch=fetch)
    return ref.fallback if ref else ""


def _legacy_write_blocker_message(
    cfg: Config,
    *,
    fetch: bool,
) -> UiMessageRef | None:
    root = resource_root(cfg)
    if git_ops.is_repo(root):
        try:
            if git_ops.status_short(root):
                return ui_message(
                    "asset.legacy.dirty",
                    "The legacy resource workspace is dirty. Use the deprecated resource "
                    "commit/sync commands to commit, cancel, or clean it before remote asset writes.",
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
                return ui_message(
                    "asset.legacy.wrong_branch",
                    "The legacy resource workspace is on the wrong branch. Resolve it "
                    "with the deprecated resource sync commands before remote asset writes.",
                )
            if divergence.state in {"ahead", "diverged"}:
                return ui_message(
                    "asset.legacy.diverged",
                    f"The legacy resource workspace is {divergence.state}. Resolve its "
                    "local commits with the deprecated resource sync commands before "
                    "remote asset writes.",
                    state=divergence.state,
                )
        except Exception as exc:
            return ui_message(
                "asset.legacy.unverifiable",
                f"The legacy resource workspace state cannot be verified: {exc}",
                detail=str(exc),
            )

    sync_root = default_state_dir() / "sync"
    if sync_root.is_dir():
        for plan_path in sorted(sync_root.glob("*/plan.json")):
            try:
                plan = load_resource_sync_plan(plan_path.parent.name)
            except Exception:
                continue
            if plan.status not in {"applied", "cancelled", "abandoned"}:
                return ui_message(
                    "asset.legacy.pending_plan",
                    f"Legacy sync plan {plan.operation_id} is still pending. Apply, cancel, "
                    "or clean it with the deprecated resource sync commands first.",
                    operation_id=plan.operation_id,
                )
    return None


def _save_asset_plan(plan: AssetActionPlan, cfg: Config) -> Path:
    path = _asset_plan_dir(plan.operation_id, config=cfg) / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_state_storage_path(path)
    _atomic_write_json(path, _jsonable(asdict(plan)))
    return path


def _save_asset_result(result: AssetActionResult, cfg: Config) -> Path:
    path = _asset_plan_dir(result.operation_id, config=cfg) / "result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_state_storage_path(path)
    _atomic_write_json(path, _jsonable(asdict(result)))
    return path


def _load_asset_result(operation_id: str, cfg: Config) -> AssetActionResult | None:
    path = _asset_plan_dir(operation_id, config=cfg) / "result.json"
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
        "align-plugin-state",
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
        message_ref=ui_message_from_data(data.get("message_ref")),
        remote_commit=str(data.get("remote_commit") or ""),
        local_path=Path(local_path) if local_path else None,
        replayed_on_latest=bool(data.get("replayed_on_latest", False)),
        push_retry_count=int(data.get("push_retry_count", 0)),
        warnings=[str(item) for item in data.get("warnings", [])],
        warning_refs=ui_messages_from_data(data.get("warning_refs")),
        operation_status=str(data.get("operation_status") or ""),
    )


def _asset_plan_dir(
    operation_id: str,
    *,
    config: Config | None = None,
) -> Path:
    if (
        not operation_id
        or operation_id in {".", ".."}
        or any(char in operation_id for char in "/\\\0")
    ):
        raise AssetPlanInvalid("Invalid asset action operation id.")
    cfg = config or load_config()
    _assert_private_asset_boundaries(cfg)
    target = default_state_dir() / ASSET_PLAN_DIR / operation_id
    _assert_state_storage_path(target / "plan.json")
    return target


def _cleanup_expired_asset_plans(cfg: Config) -> None:
    _assert_private_asset_boundaries(cfg)
    root = default_state_dir() / ASSET_PLAN_DIR
    _assert_state_storage_path(root / ".cleanup-check")
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
    target = _checked_internal_path(path, allowed_root)
    if target.is_symlink():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target, ignore_errors=True)


def _assert_internal_path(path: Path, allowed_root: Path) -> None:
    _checked_internal_path(path, allowed_root)


def _checked_internal_path(path: Path, allowed_root: Path) -> Path:
    root = allowed_root.expanduser().resolve(strict=False)
    raw = path.expanduser().absolute()
    target = raw.parent.resolve(strict=False) / raw.name
    if target == root or root not in target.parents:
        raise ValueError(f"Refusing to access path outside internal state: {target}")
    return target


def _assert_private_asset_boundaries(cfg: Config) -> None:
    conflicts = resource_repo_private_path_conflicts(cfg)
    if conflicts:
        raise AssetPlanInvalid(
            "The resource repository overlaps machine-local configuration, state, "
            "or platform paths. Move one of the configured roots before planning."
        )


def _assert_state_storage_path(path: Path) -> None:
    probe = probe_local_path(path)
    unsafe_leaf = probe.health != "missing" and (probe.path_kind != "regular" or not probe.ready)
    if unsafe_leaf or _local_target_ancestor_problem(path):
        raise AssetPlanInvalid(
            "The machine-local state path contains a linked, reparse, or unreadable "
            "ancestor and cannot store asset plans safely."
        )


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


def _append_message(
    values: list[str],
    refs: list[UiMessageRef],
    ref: UiMessageRef,
) -> None:
    values.append(ref.fallback)
    refs.append(ref)


def _extend_messages(
    values: list[str],
    refs: list[UiMessageRef],
    new_refs: list[UiMessageRef],
) -> None:
    values.extend(fallback_text(new_refs))
    refs.extend(new_refs)


def _unique_message_refs(values: list[UiMessageRef]) -> list[UiMessageRef]:
    seen: set[tuple[str, str, tuple[tuple[str, object], ...]]] = set()
    unique: list[UiMessageRef] = []
    for value in values:
        key = (value.code, value.fallback, tuple(sorted(value.params.items())))
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
