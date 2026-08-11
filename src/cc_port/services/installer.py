"""Sync registry items to local installation directories across platforms."""

from __future__ import annotations

import shutil
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from ..core.config import Config
from ..core.models import Registry, RegistryItem
from ..core.ownership import (
    is_cc_port_managed,
    is_cc_port_managed_mcp,
    managed_marker_path,
    mark_cc_port_managed_mcp,
    mcp_ownership_path,
    remove_managed_marker,
    unmark_cc_port_managed_mcp,
    write_managed_marker,
)
from ..core.platforms import PlatformProfile
from ..core.registry import find_registry_path, load_registry, save_registry
from ..core.secrets import sanitize_mcp_config_for_storage
from ..core.validator import validate_item
from ..infrastructure import git_ops
from .install_planner import InstallPlan, copy_resource_tree, plan_install
from .local_path_probe import probe_local_path
from .local_transaction import (
    ChangeTarget,
    LocalChangeTransaction,
    resource_hash_path,
)
from .mcp_installer import (
    has_mcp_server,
    inject_mcp_server,
    list_mcp_servers,
    remove_mcp_server,
)

# Backward-compatible alias
SkillEntry = RegistryItem
DEFAULT_SYNC_KINDS = {"skill"}
OPTIONAL_SYNC_KINDS = {
    "mcp",
    "rule",
    "prompt",
    "plugin",
    "instruction",
    "memory",
}


class SyncAction(str, Enum):
    INSTALLED = "installed"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    FAILED = "failed"
    SKIPPED = "skipped"
    REPO_GONE = "repo_gone"


@dataclass
class SkillStatus:
    name: str
    install_path: Path
    installed: bool
    local_commit: str | None
    remote_commit: str | None
    has_update: bool
    kind: str = ""
    resource_key: str = ""


@dataclass
class SyncResult:
    name: str
    install_path: Path
    action: SyncAction
    detail: str = ""
    platforms_installed: list[str] = field(default_factory=list)
    operation_id: str = ""
    operation_status: str = ""
    backup_root: Path | None = None
    rolled_back: bool = False


@dataclass
class SyncPreviewItem:
    name: str
    kind: str
    source: str
    planned_action: str
    install_path: Path
    target_platforms: list[str] = field(default_factory=list)
    target_paths: list[Path] = field(default_factory=list)
    installed: bool = False
    has_update: bool = False
    blocked: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class SyncPreviewResult:
    registry_path: Path | None
    items: list[SyncPreviewItem]


# ---- Path helpers ---- #


def _install_root(config: Config) -> Path:
    return config.install.target_path


def _install_path(config: Config, entry: RegistryItem) -> Path:
    return _install_root(config) / entry.install_target_name()


def _clone_path(config: Config, entry: RegistryItem) -> Path:
    """Where the actual git clone lives.

    For full-repo items it is the install path itself.
    For subdir items we keep the clone in a hidden staging area.
    """
    if not entry.subdir:
        return _install_path(config, entry)
    return _install_root(config) / ".cc-port" / "clones" / entry.name


# ---- Platform-aware install helpers ---- #


def _entry_install_name(entry: RegistryItem, platform: PlatformProfile) -> str:
    if entry.kind == "memory":
        if platform.memory_layout == "direct":
            return entry.name
        mapped = platform.memory_install_names.get(entry.name)
        if mapped:
            # PlatformProfile.resolve_install_path owns the logical-name to
            # local Claude project-slot mapping.  Passing the slot here would
            # apply a chained mapping twice (A -> B -> C).
            return entry.name
        existing = platform.resolve_install_path("memory", entry.name)
        if existing is None or not existing.exists():
            raise ValueError(
                "Memory installation requires an exact local Claude project-slot mapping."
            )
        return entry.name
    return (
        entry.platform_install_dirs.get(platform.effective_tool_id)
        or entry.install_dir
        or entry.name
    )


def _uses_sibling_marker(entry: RegistryItem, target: Path) -> bool:
    return entry.kind in {"instruction", "memory"} or _is_file_prompt_target(entry, target)


def _install_skill_to_platform(
    source_path: Path,
    platform: PlatformProfile,
    entry: RegistryItem,
    *,
    force_unmanaged: bool = False,
) -> Path | None:
    """Copy a skill directory to a platform's skills_dir."""
    target_dir = platform.resolve_install_path(
        "skill",
        _entry_install_name(entry, platform),
    )
    if target_dir is None:
        return None
    try:
        if source_path.resolve() == target_dir.resolve():
            return target_dir
    except OSError:
        pass
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists() or target_dir.is_symlink():
        if (
            not force_unmanaged
            and not is_cc_port_managed(target_dir, resource_key=entry.resource_key)
        ):
            raise RuntimeError(f"Target exists and is not managed by CC Port: {target_dir}")
        if (
            is_cc_port_managed(target_dir, resource_key=entry.resource_key)
            and resource_hash_path(source_path) == resource_hash_path(target_dir)
        ):
            return target_dir
        _remove_path(target_dir)
    copy_resource_tree(source_path, target_dir)
    return target_dir


def _install_mcp_to_platform(
    platform: PlatformProfile,
    entry: RegistryItem,
    *,
    force_unmanaged: bool = False,
) -> Path | None:
    """Inject MCP config into a platform's mcp.json."""
    mcp_path = platform.mcp_json_path()
    if mcp_path is None or entry.mcp_config is None:
        return None
    server_name = _entry_install_name(entry, platform)
    if (
        mcp_path.exists()
        and has_mcp_server(mcp_path, server_name)
        and not force_unmanaged
        and not is_cc_port_managed_mcp(
            mcp_path,
            server_name,
            resource_key=entry.resource_key,
        )
    ):
        raise RuntimeError(
            f"MCP server exists and is not managed by CC Port: {server_name} in {mcp_path}"
        )
    expected = sanitize_mcp_config_for_storage(entry.mcp_config)
    if (
        mcp_path.exists()
        and list_mcp_servers(mcp_path).get(server_name) == expected
        and is_cc_port_managed_mcp(
            mcp_path,
            server_name,
            resource_key=entry.resource_key,
        )
    ):
        return mcp_path
    inject_mcp_server(mcp_path, server_name, expected)
    mark_cc_port_managed_mcp(
        mcp_path,
        server_name,
        resource_name=entry.name,
        resource_kind=entry.kind,
        resource_key=entry.resource_key,
        platform=platform.name,
    )
    return mcp_path


def _install_rule_to_platform(
    source_path: Path,
    platform: PlatformProfile,
    entry: RegistryItem,
    *,
    force_unmanaged: bool = False,
) -> Path | None:
    """Copy a rule or prompt to its platform-native target."""
    target_dir = platform.resolve_install_path(
        entry.kind,
        _entry_install_name(entry, platform),
    )
    if target_dir is None:
        return None
    if _is_file_asset_target(entry, target_dir):
        return _install_file_asset_to_platform(
            source_path,
            target_dir,
            entry,
            force_unmanaged=force_unmanaged,
        )
    try:
        if source_path.resolve() == target_dir.resolve():
            return target_dir
    except OSError:
        pass
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists() or target_dir.is_symlink():
        file_target = _uses_sibling_marker(entry, target_dir)
        if (
            not force_unmanaged
            and not is_cc_port_managed(
                target_dir,
                resource_key=entry.resource_key,
                file_target=file_target,
            )
        ):
            raise RuntimeError(f"Target exists and is not managed by CC Port: {target_dir}")
        if (
            is_cc_port_managed(
                target_dir,
                resource_key=entry.resource_key,
                file_target=file_target,
            )
            and resource_hash_path(source_path) == resource_hash_path(target_dir)
        ):
            return target_dir
        _remove_path(target_dir)
    if entry.kind == "memory":
        validate_item(source_path, "memory")
        shutil.copytree(source_path, target_dir)
    else:
        copy_resource_tree(source_path, target_dir)
    return target_dir


def _install_file_asset_to_platform(
    source_path: Path,
    target_file: Path,
    entry: RegistryItem,
    *,
    force_unmanaged: bool,
) -> Path:
    payload = _file_asset_payload_path(source_path, kind=entry.kind)
    marker = managed_marker_path(target_file, file_target=True)
    if marker.is_symlink():
        raise RuntimeError(
            f"Resource ownership sidecar must not be a symbolic link: {marker}"
        )
    try:
        if payload.resolve() == target_file.resolve():
            return target_file
    except OSError:
        pass
    target_file.parent.mkdir(parents=True, exist_ok=True)
    if target_file.exists() or target_file.is_symlink():
        managed = is_cc_port_managed(
            target_file,
            resource_key=entry.resource_key,
            file_target=True,
        )
        if not force_unmanaged and not managed:
            raise RuntimeError(
                f"Target exists and is not managed by CC Port: {target_file}"
            )
        if (
            managed
            and not target_file.is_symlink()
            and resource_hash_path(payload) == resource_hash_path(target_file)
        ):
            return target_file
        _remove_path(target_file)
    shutil.copy2(payload, target_file)
    return target_file


def _file_asset_payload_path(source_path: Path, *, kind: str) -> Path:
    """Return the single Markdown payload for a file-style resource target."""
    if source_path.is_symlink():
        raise RuntimeError(
            f"File-style {kind} source must not be a symbolic link: {source_path}"
        )
    if source_path.is_file():
        if source_path.suffix.lower() == ".md":
            return source_path
        raise RuntimeError(
            f"File-style {kind} source must be a Markdown file: {source_path}"
        )
    if not source_path.is_dir():
        raise RuntimeError(f"File-style {kind} source is unavailable: {source_path}")

    candidates = sorted(
        path
        for path in source_path.iterdir()
        if path.suffix.lower() == ".md"
        and path.is_file()
        and not path.is_symlink()
    )
    if len(candidates) != 1:
        raise RuntimeError(
            f"File-style {kind} source must contain exactly one root-level "
            f"non-symlink .md file: {source_path}"
        )
    return candidates[0]


def _prompt_payload_path(source_path: Path) -> Path:
    """Backward-compatible helper for existing prompt call sites."""
    return _file_asset_payload_path(source_path, kind="prompt")


def _is_file_prompt_target(entry: RegistryItem, target: Path) -> bool:
    return entry.kind == "prompt" and target.suffix.lower() == ".md"


def _is_file_asset_target(entry: RegistryItem, target: Path) -> bool:
    return entry.kind == "instruction" or _is_file_prompt_target(entry, target)


def _install_plugin_to_platform(
    source_path: Path,
    platform: PlatformProfile,
    entry: RegistryItem,
    *,
    force_unmanaged: bool = False,
) -> Path | None:
    """Copy a plugin directory to a platform's plugin target."""
    target_dir = platform.resolve_install_path(
        "plugin",
        _entry_install_name(entry, platform),
    )
    if target_dir is None:
        return None
    try:
        if source_path.resolve() == target_dir.resolve():
            return target_dir
    except OSError:
        pass
    if target_dir.exists():
        if (
            not force_unmanaged
            and not is_cc_port_managed(target_dir, resource_key=entry.resource_key)
        ):
            raise RuntimeError(f"Target exists and is not managed by CC Port: {target_dir}")
        if (
            is_cc_port_managed(target_dir, resource_key=entry.resource_key)
            and resource_hash_path(source_path) == resource_hash_path(target_dir)
        ):
            return target_dir
        _remove_path(target_dir)
    copy_resource_tree(source_path, target_dir)
    return target_dir


def _distribute_to_platforms(
    config: Config,
    entry: RegistryItem,
    clone_path: Path,
    *,
    platform_filter: str | None = None,
    force_unmanaged: bool = False,
) -> list[str]:
    """Distribute an item to all enabled platforms based on its kind.

    Returns list of platform names where installation succeeded.
    """
    platforms = [
        platform
        for platform in config.platforms.enabled()
        if platform.supports_resource(entry.kind, entry.platforms)
    ]
    if platform_filter:
        platforms = [p for p in platforms if p.name == platform_filter]

    installed_on: list[str] = []

    source = clone_path / entry.subdir if entry.subdir else clone_path

    for plat in platforms:
        result_path: Path | None = None
        if entry.kind == "skill":
            result_path = _install_skill_to_platform(
                source,
                plat,
                entry,
                force_unmanaged=force_unmanaged,
            )
        elif entry.kind == "mcp":
            result_path = _install_mcp_to_platform(
                plat,
                entry,
                force_unmanaged=force_unmanaged,
            )
        elif entry.kind in {"rule", "prompt", "instruction", "memory"}:
            result_path = _install_rule_to_platform(
                source,
                plat,
                entry,
                force_unmanaged=force_unmanaged,
            )
        elif entry.kind == "plugin":
            result_path = _install_plugin_to_platform(
                source,
                plat,
                entry,
                force_unmanaged=force_unmanaged,
            )

        if result_path is not None:
            file_target = _uses_sibling_marker(entry, result_path)
            if (
                entry.kind != "mcp"
                and not is_cc_port_managed(
                    result_path,
                    resource_key=entry.resource_key,
                    file_target=file_target,
                )
            ):
                write_managed_marker(
                    result_path,
                    entry,
                    platform=plat.name,
                    file_target=file_target,
                )
            installed_on.append(plat.name)

    return installed_on


def _platform_targets(
    config: Config,
    entry: RegistryItem,
    *,
    platform_filter: str | None = None,
) -> list[tuple[str, Path]]:
    if entry.kind in {"instruction", "memory"}:
        return []
    platforms = [
        platform
        for platform in config.platforms.enabled()
        if platform.supports_resource(entry.kind, entry.platforms)
    ]
    if platform_filter:
        platforms = [p for p in platforms if p.name == platform_filter]

    targets: list[tuple[str, Path]] = []
    for platform in platforms:
        try:
            target_path = platform.resolve_install_path(
                entry.kind,
                _entry_install_name(entry, platform),
            )
        except ValueError:
            continue
        if target_path is not None:
            targets.append((platform.name, target_path))
    return targets


# ---- Core sync logic ---- #


def sync_one(
    entry: RegistryItem,
    *,
    config: Config,
    token: str | None = None,
    platform_filter: str | None = None,
    registry_root: Path | None = None,
    force_unmanaged: bool = False,
    transactional: bool = True,
) -> SyncResult:
    binding_problem = _legacy_resource_binding_problem(entry)
    if binding_problem:
        return SyncResult(
            name=entry.name,
            install_path=_install_path(config, entry),
            action=SyncAction.SKIPPED,
            detail=binding_problem,
        )
    if not transactional:
        return _sync_one_unsafe(
            entry,
            config=config,
            token=token,
            platform_filter=platform_filter,
            registry_root=registry_root,
            force_unmanaged=force_unmanaged,
        )
    if entry.lifecycle != "active":
        return SyncResult(
            name=entry.name,
            install_path=_install_path(config, entry),
            action=SyncAction.SKIPPED,
            detail="Resource has been removed from the active registry.",
        )
    selected_profile = config.platforms.get(platform_filter) if platform_filter else None
    if platform_filter and (
        selected_profile is None
        or not selected_profile.supports_resource(entry.kind, entry.platforms)
    ):
        return SyncResult(
            name=entry.name,
            install_path=_install_path(config, entry),
            action=SyncAction.SKIPPED,
            detail=f"Resource is not allowed on platform {platform_filter!r}.",
        )

    targets = _resource_change_targets(
        config,
        entry,
        platform_filter=platform_filter,
        change_action="install",
    )
    transaction = LocalChangeTransaction.begin(
        "resource-install",
        targets,
        metadata={
            "resource": entry.resource_key,
            "platform": platform_filter or "",
        },
        lock_timeout_seconds=config.state.lock_timeout_seconds,
    )
    transaction.mark_attempted(target.path for target in targets)
    result: SyncResult | None = None
    try:
        result = _sync_one_unsafe(
            entry,
            config=config,
            token=token,
            platform_filter=platform_filter,
            registry_root=registry_root,
            force_unmanaged=force_unmanaged,
        )
        if result.action in {
            SyncAction.FAILED,
            SyncAction.REPO_GONE,
            SyncAction.SKIPPED,
        }:
            raise RuntimeError(result.detail or result.action.value)
        _verify_resource_install(
            entry,
            config=config,
            platforms_installed=result.platforms_installed,
        )
        transaction.complete()
        result.operation_id = transaction.record.operation_id
        result.operation_status = transaction.record.status
        result.backup_root = transaction.backup_root
        return result
    except Exception as exc:
        rollback_errors = transaction.rollback(str(exc))
        detail = str(exc)
        if rollback_errors:
            detail += " | rollback errors: " + "; ".join(rollback_errors)
        return SyncResult(
            name=entry.name,
            install_path=_install_path(config, entry),
            action=(
                result.action
                if result is not None
                and result.action in {SyncAction.FAILED, SyncAction.REPO_GONE}
                else SyncAction.FAILED
            ),
            detail=detail,
            operation_id=transaction.record.operation_id,
            operation_status=transaction.record.status,
            backup_root=transaction.backup_root,
            rolled_back=transaction.record.rolled_back,
        )


def _sync_one_unsafe(
    entry: RegistryItem,
    *,
    config: Config,
    token: str | None = None,
    platform_filter: str | None = None,
    registry_root: Path | None = None,
    force_unmanaged: bool = False,
) -> SyncResult:
    install_path = _install_path(config, entry)
    clone_path = _clone_path(config, entry)
    binding_problem = _legacy_resource_binding_problem(entry)
    if binding_problem:
        return SyncResult(
            name=entry.name,
            install_path=install_path,
            action=SyncAction.SKIPPED,
            detail=binding_problem,
        )
    if entry.lifecycle != "active":
        return SyncResult(
            name=entry.name,
            install_path=install_path,
            action=SyncAction.SKIPPED,
            detail="Resource has been removed from the active registry.",
        )
    selected_profile = config.platforms.get(platform_filter) if platform_filter else None
    if platform_filter and (
        selected_profile is None
        or not selected_profile.supports_resource(entry.kind, entry.platforms)
    ):
        return SyncResult(
            name=entry.name,
            install_path=install_path,
            action=SyncAction.SKIPPED,
            detail=f"Resource is not allowed on platform {platform_filter!r}.",
        )
    install_path.parent.mkdir(parents=True, exist_ok=True)
    clone_path.parent.mkdir(parents=True, exist_ok=True)

    auth_token = token or config.github.token or None

    try:
        if _is_local_resource(entry):
            source_path = _local_source_path(entry, registry_root=registry_root)
            if not source_path.exists():
                return SyncResult(
                    name=entry.name,
                    install_path=source_path,
                    action=SyncAction.FAILED,
                    detail=f"Local resource path does not exist: {source_path}",
                )
            if entry.kind == "mcp":
                platforms_installed = _distribute_to_platforms(
                    config,
                    entry,
                    source_path,
                    platform_filter=platform_filter,
                    force_unmanaged=force_unmanaged,
                )
                return SyncResult(
                    name=entry.name,
                    install_path=source_path,
                    action=SyncAction.INSTALLED,
                    platforms_installed=platforms_installed,
                )

            if entry.kind in {"instruction", "memory"}:
                validate_item(source_path, entry.kind)
            if install_path.exists() or install_path.is_symlink():
                _remove_path(install_path)
            install_path.parent.mkdir(parents=True, exist_ok=True)
            if source_path.is_file() and entry.kind in {"prompt", "instruction"}:
                install_path.mkdir(parents=True)
                payload = _file_asset_payload_path(source_path, kind=entry.kind)
                shutil.copy2(payload, install_path / payload.name)
            elif source_path.is_dir():
                if entry.kind in {"instruction", "memory"}:
                    shutil.copytree(source_path, install_path)
                else:
                    copy_resource_tree(source_path, install_path)
            else:
                install_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, install_path)
            platforms_installed = _distribute_to_platforms(
                config,
                entry,
                install_path,
                platform_filter=platform_filter,
                force_unmanaged=force_unmanaged,
            )
            return SyncResult(
                name=entry.name,
                install_path=install_path,
                action=SyncAction.INSTALLED,
                platforms_installed=platforms_installed,
            )

        if entry.kind == "mcp" and entry.mcp_config and not _needs_clone(entry):
            platforms_installed = _distribute_to_platforms(
                config,
                entry,
                clone_path,
                platform_filter=platform_filter,
                force_unmanaged=force_unmanaged,
            )
            return SyncResult(
                name=entry.name,
                install_path=install_path,
                action=SyncAction.INSTALLED,
                platforms_installed=platforms_installed,
            )

        if clone_path.exists() and not git_ops.is_repo(clone_path):
            _remove_path(clone_path)

        if not git_ops.is_repo(clone_path):
            git_ops.clone(entry.repo, clone_path, ref=entry.ref, token=auth_token)
            if entry.subdir:
                git_ops.sparse_checkout(clone_path, entry.subdir)
            action = SyncAction.INSTALLED
        else:
            before = git_ops.head_commit(clone_path)
            git_ops.set_remote(clone_path, "origin", entry.repo)
            git_ops.pull(clone_path, ref=entry.ref, token=auth_token)
            after = git_ops.head_commit(clone_path)
            action = SyncAction.UPDATED if before != after else SyncAction.UNCHANGED

        if entry.subdir:
            _materialize_subdir(clone_path, entry.subdir, install_path)

        platforms_installed = _distribute_to_platforms(
            config,
            entry,
            clone_path,
            platform_filter=platform_filter,
            force_unmanaged=force_unmanaged,
        )

        return SyncResult(
            name=entry.name,
            install_path=install_path,
            action=action,
            platforms_installed=platforms_installed,
        )
    except git_ops.GitError as exc:
        action = SyncAction.FAILED
        detail = str(exc)
        if git_ops.looks_like_repo_gone(detail):
            action = SyncAction.REPO_GONE
            detail = f"Repository appears to have been deleted or is inaccessible: {detail}"
        return SyncResult(
            name=entry.name,
            install_path=install_path,
            action=action,
            detail=detail,
        )


def _needs_clone(entry: RegistryItem) -> bool:
    """Determine if this item needs a git clone or is config-only.

    Pure MCP config entries (kind=mcp with mcp_config but no subdir and no
    repo-hosted source code to install) can be distributed without cloning.
    """
    if entry.kind == "mcp" and entry.mcp_config and not entry.subdir:
        return False
    return True


def _legacy_resource_binding_problem(entry: RegistryItem) -> str:
    if entry.kind in {"instruction", "memory"}:
        return (
            "Instruction and memory resources require environment-aware asset sync; "
            "legacy sync is disabled for these resource kinds."
        )
    return ""


def _is_local_resource(entry: RegistryItem) -> bool:
    return bool(entry.path) and entry.source in {"local", "owned"}


def _local_source_path(entry: RegistryItem, *, registry_root: Path | None = None) -> Path:
    root = (registry_root or find_registry_path().parent).expanduser().absolute()
    root_probe = probe_local_path(root)
    if root_probe.is_link or not root_probe.ready:
        raise ValueError(
            root_probe.problem
            or "The local registry root is linked or cannot be read safely."
        )
    candidate = (root / entry.path).absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Local resource path leaves the registry root.") from exc
    current = root
    for part in relative.parts:
        current /= part
        probe = probe_local_path(current)
        if probe.health == "missing":
            continue
        if probe.is_link or not probe.ready:
            raise ValueError(
                probe.problem
                or f"Local resource path crosses a linked or unreadable component: {current}"
            )
    return candidate


def sync_all(
    *,
    config: Config,
    registry: Registry | None = None,
    registry_path: Path | None = None,
    only: list[str] | None = None,
    kind: str | None = None,
    tags: list[str] | None = None,
    include_optional: bool = False,
    include_kinds: set[str] | None = None,
    platform_filter: str | None = None,
    force_unmanaged: bool = False,
    transactional: bool = True,
) -> list[SyncResult]:
    effective_registry_path = registry_path or (find_registry_path() if registry is None else None)
    reg = registry or load_registry(effective_registry_path)
    registry_root = effective_registry_path.parent if effective_registry_path else None
    tag_set = {t.lower() for t in tags or []}
    allowed_kinds = _allowed_sync_kinds(
        kind=kind,
        include_optional=include_optional,
        include_kinds=include_kinds,
    )
    results: list[SyncResult] = []
    for entry in reg.items:
        if entry.lifecycle != "active":
            continue
        if only and entry.name not in only and entry.resource_key not in only:
            continue
        if entry.kind not in allowed_kinds:
            continue
        if tag_set and not (tag_set & {t.lower() for t in entry.tags}):
            continue
        results.append(
            sync_one(
                entry,
                config=config,
                platform_filter=platform_filter,
                registry_root=registry_root,
                force_unmanaged=force_unmanaged,
                transactional=transactional,
            )
        )
    return results


def create_install_plan(
    entry: RegistryItem,
    *,
    config: Config,
    platform_filter: str | None = None,
    registry_root: Path | None = None,
) -> InstallPlan:
    if _is_local_resource(entry):
        source_path = _local_source_path(entry, registry_root=registry_root)
    else:
        clone_path = _clone_path(config, entry)
        source_path = clone_path / entry.subdir if entry.subdir else clone_path
    return plan_install(
        entry,
        source_path,
        config.platforms.profiles,
        platform_filter=platform_filter,
    )


def preview_sync_all(
    *,
    config: Config,
    registry: Registry | None = None,
    registry_path: Path | None = None,
    only: list[str] | None = None,
    kind: str | None = None,
    tags: list[str] | None = None,
    include_optional: bool = False,
    include_kinds: set[str] | None = None,
    platform_filter: str | None = None,
) -> SyncPreviewResult:
    effective_registry_path = registry_path or (find_registry_path() if registry is None else None)
    reg = registry or load_registry(effective_registry_path)
    registry_root = effective_registry_path.parent if effective_registry_path else None
    tag_set = {t.lower() for t in tags or []}
    allowed_kinds = _allowed_sync_kinds(
        kind=kind,
        include_optional=include_optional,
        include_kinds=include_kinds,
    )
    preview_entries: list[RegistryItem] = []
    for entry in reg.items:
        if entry.lifecycle != "active":
            continue
        if only and entry.name not in only and entry.resource_key not in only:
            continue
        if entry.kind not in allowed_kinds:
            continue
        if tag_set and not (tag_set & {t.lower() for t in entry.tags}):
            continue
        preview_entries.append(entry)

    statuses = {
        entry.resource_key: _skill_status(entry, config, registry_root=registry_root)
        for entry in preview_entries
    }

    items: list[SyncPreviewItem] = []
    for entry in preview_entries:
        items.append(
            _preview_sync_item(
                config,
                entry,
                status=statuses.get(entry.resource_key),
                platform_filter=platform_filter,
                registry_root=registry_root,
            )
        )

    return SyncPreviewResult(registry_path=effective_registry_path, items=items)


def status_all(
    *,
    config: Config,
    registry: Registry | None = None,
    registry_path: Path | None = None,
    kind: str | None = None,
) -> list[SkillStatus]:
    effective_registry_path = registry_path or (find_registry_path() if registry is None else None)
    reg = registry or load_registry(effective_registry_path)
    registry_root = effective_registry_path.parent if effective_registry_path else None
    out: list[SkillStatus] = []
    for entry in reg.items:
        if kind and entry.kind != kind:
            continue
        out.append(_skill_status(entry, config, registry_root=registry_root))
    return out


def _preview_sync_item(
    config: Config,
    entry: RegistryItem,
    *,
    status: SkillStatus | None,
    platform_filter: str | None,
    registry_root: Path | None,
) -> SyncPreviewItem:
    install_path = _install_path(config, entry)
    clone_path = _clone_path(config, entry)
    warnings: list[str] = []
    blocked = False
    try:
        install_plan = create_install_plan(
            entry,
            config=config,
            platform_filter=platform_filter,
            registry_root=registry_root,
        )
        target_pairs = [(target.platform, target.path) for target in install_plan.targets]
        warnings.extend(install_plan.warnings)
    except Exception as exc:  # noqa: BLE001 - preview should report plan failures
        install_plan = None
        target_pairs = _platform_targets(config, entry, platform_filter=platform_filter)
        warnings.append(f"Install plan could not be built: {exc}")

    if entry.source in {"local", "owned"}:
        if not entry.path:
            blocked = True
            warnings.append("Local resource path is missing.")
            install_path = _local_source_path(entry, registry_root=registry_root)
        else:
            source_path = _local_source_path(entry, registry_root=registry_root)
            if not source_path.exists():
                blocked = True
                warnings.append(f"Local resource path does not exist: {source_path}")
            install_path = source_path if blocked else install_path

    if not target_pairs:
        if platform_filter:
            warnings.append(f"No enabled target path found for platform {platform_filter}.")
        else:
            warnings.append("No enabled platform has a target path for this resource type.")

    if entry.kind == "mcp" and not entry.mcp_config:
        warnings.append("MCP config is missing; no MCP server entry can be injected.")

    if install_plan is not None:
        soft_targets = [target.platform for target in install_plan.targets if not target.auto_install]
        if soft_targets:
            warnings.append(
                "Some detected agent targets require explicit selection: "
                + ", ".join(sorted(soft_targets))
            )

    for platform_name, target_path in target_pairs:
        profile = config.platforms.get(platform_name)
        file_target = _uses_sibling_marker(entry, target_path)
        unmanaged_directory = (
            entry.kind != "mcp"
            and (target_path.exists() or target_path.is_symlink())
            and not is_cc_port_managed(
                target_path,
                resource_key=entry.resource_key,
                file_target=file_target,
            )
        )
        unmanaged_mcp = False
        if entry.kind == "mcp" and target_path.exists():
            try:
                server_name = (
                    _entry_install_name(entry, profile)
                    if profile is not None
                    else entry.install_target_name(platform_name)
                )
                unmanaged_mcp = (
                    has_mcp_server(target_path, server_name)
                    and not is_cc_port_managed_mcp(
                        target_path,
                        server_name,
                        resource_key=entry.resource_key,
                    )
                )
            except (OSError, ValueError) as exc:
                blocked = True
                warnings.append(
                    f"Cannot read MCP target for {platform_name}: {target_path}: {exc}"
                )
        if unmanaged_directory or unmanaged_mcp:
            blocked = True
            warnings.append(
                f"Target for {platform_name} exists and is not managed by CC Port: {target_path}"
            )

    return SyncPreviewItem(
        name=entry.name,
        kind=entry.kind,
        source=entry.source,
        planned_action=_planned_sync_action(entry, clone_path),
        install_path=install_path,
        target_platforms=[name for name, _ in target_pairs],
        target_paths=[path for _, path in target_pairs],
        installed=status.installed if status else False,
        has_update=status.has_update if status else False,
        blocked=blocked,
        warnings=warnings,
    )


def _planned_sync_action(entry: RegistryItem, clone_path: Path) -> str:
    if entry.kind == "mcp" and entry.mcp_config and not _needs_clone(entry):
        return "inject_mcp"
    if entry.source in {"local", "owned"} and entry.path:
        return "copy"
    if git_ops.is_repo(clone_path):
        return "pull"
    return "clone"


def _allowed_sync_kinds(
    *,
    kind: str | None,
    include_optional: bool,
    include_kinds: set[str] | None,
) -> set[str]:
    if kind:
        return {kind}
    if include_optional:
        return DEFAULT_SYNC_KINDS | OPTIONAL_SYNC_KINDS
    return DEFAULT_SYNC_KINDS | (include_kinds or set())


def _skill_status(
    entry: RegistryItem, config: Config, *, registry_root: Path | None = None
) -> SkillStatus:
    install_path = _install_path(config, entry)
    if _is_local_resource(entry):
        return SkillStatus(
            name=entry.name,
            install_path=install_path,
            installed=install_path.exists(),
            local_commit=None,
            remote_commit=None,
            has_update=False,
            kind=entry.kind,
            resource_key=entry.resource_key,
        )
    clone_path = _clone_path(config, entry)
    installed = install_path.exists()
    local = git_ops.head_commit(clone_path) if git_ops.is_repo(clone_path) else None
    remote = git_ops.remote_commit(clone_path, ref=entry.ref) if git_ops.is_repo(clone_path) else None
    has_update = bool(local and remote and local != remote)
    return SkillStatus(
        name=entry.name,
        install_path=install_path,
        installed=installed,
        local_commit=local,
        remote_commit=remote,
        has_update=has_update,
        kind=entry.kind,
        resource_key=entry.resource_key,
    )


def uninstall_one(
    entry: RegistryItem,
    *,
    config: Config,
    platform_filter: str | None = None,
    transactional: bool = True,
) -> bool:
    if entry.kind in {"instruction", "memory"}:
        return False
    if not transactional:
        return _uninstall_one_unsafe(
            entry,
            config=config,
            platform_filter=platform_filter,
        )
    targets = _resource_change_targets(
        config,
        entry,
        platform_filter=platform_filter,
        change_action="uninstall",
        include_cache=platform_filter is None,
    )
    transaction = LocalChangeTransaction.begin(
        "resource-uninstall",
        targets,
        metadata={
            "resource": entry.resource_key,
            "platform": platform_filter or "",
        },
        lock_timeout_seconds=config.state.lock_timeout_seconds,
    )
    transaction.mark_attempted(target.path for target in targets)
    try:
        removed = _uninstall_one_unsafe(
            entry,
            config=config,
            platform_filter=platform_filter,
        )
        _verify_resource_uninstall(
            entry,
            config=config,
            platform_filter=platform_filter,
        )
        transaction.complete()
        return removed
    except Exception as exc:
        errors = transaction.rollback(str(exc))
        detail = str(exc)
        if errors:
            detail += " | rollback errors: " + "; ".join(errors)
        raise RuntimeError(
            f"{detail} (operation {transaction.record.operation_id}, "
            f"status {transaction.record.status})"
        ) from exc


def _uninstall_one_unsafe(
    entry: RegistryItem,
    *,
    config: Config,
    platform_filter: str | None = None,
) -> bool:
    """Remove an item's local files and clean up platform installations."""
    install_path = _install_path(config, entry)
    clone_path = _clone_path(config, entry)
    removed = False

    if platform_filter is None:
        for p in {install_path, clone_path}:
            if p.exists() or p.is_symlink():
                _remove_path(p)
                removed = True

    # Allowlist changes must not make stale CC Port-managed targets impossible
    # to remove. Ownership markers still gate every deletion below.
    platforms = list(config.platforms.enabled())
    if platform_filter:
        platforms = [plat for plat in platforms if plat.name == platform_filter]

    for plat in platforms:
        if _remove_platform_installation(entry, plat):
            removed = True

    return removed


def _resource_change_targets(
    config: Config,
    entry: RegistryItem,
    *,
    platform_filter: str | None,
    change_action: str,
    include_cache: bool = True,
) -> list[ChangeTarget]:
    targets: list[ChangeTarget] = []
    if include_cache:
        if not (
            entry.kind == "mcp"
            and (
                _is_local_resource(entry)
                or (entry.mcp_config and not _needs_clone(entry))
            )
        ):
            targets.append(
                ChangeTarget(
                    path=_install_path(config, entry),
                    change_action=change_action,
                    resource=entry.resource_key,
                )
            )
            clone_path = _clone_path(config, entry)
            if clone_path != _install_path(config, entry):
                targets.append(
                    ChangeTarget(
                        path=clone_path,
                        change_action=change_action,
                        resource=entry.resource_key,
                    )
                )

    platform_targets = _platform_targets(
        config,
        entry,
        platform_filter=platform_filter,
    )
    for platform, path in platform_targets:
        targets.append(
            ChangeTarget(
                path=path,
                change_action=change_action,
                resource=entry.resource_key,
                platform=platform,
            )
        )
        profile = config.platforms.get(platform)
        if profile is not None and _uses_sibling_marker(entry, path):
            targets.append(
                ChangeTarget(
                    path=managed_marker_path(path, file_target=True),
                    change_action=change_action,
                    resource=entry.resource_key,
                    platform=platform,
                )
            )
    if entry.kind == "mcp" and platform_targets:
        targets.append(
            ChangeTarget(
                path=mcp_ownership_path(),
                change_action=change_action,
                resource=entry.resource_key,
            )
        )
    return targets


def _verify_resource_install(
    entry: RegistryItem,
    *,
    config: Config,
    platforms_installed: list[str],
) -> None:
    if entry.kind == "mcp":
        if _needs_clone(entry) and not _clone_path(config, entry).exists():
            raise RuntimeError(
                f"Install cache is missing for {entry.name}: {_clone_path(config, entry)}"
            )
        expected = sanitize_mcp_config_for_storage(entry.mcp_config or {})
        for platform_name in platforms_installed:
            platform = config.platforms.get(platform_name)
            mcp_path = platform.mcp_json_path() if platform else None
            server_name = (
                _entry_install_name(entry, platform)
                if platform is not None
                else entry.install_target_name(platform_name)
            )
            if (
                mcp_path is None
                or list_mcp_servers(mcp_path).get(server_name) != expected
                or not is_cc_port_managed_mcp(
                    mcp_path,
                    server_name,
                    resource_key=entry.resource_key,
                )
            ):
                raise RuntimeError(
                    f"Install verification failed for {entry.name} on {platform_name}."
                )
        return

    cache_path = _install_path(config, entry)
    if not cache_path.exists():
        raise RuntimeError(f"Install cache is missing for {entry.name}: {cache_path}")
    cache_hash = resource_hash_path(cache_path)
    for platform_name in platforms_installed:
        platform = config.platforms.get(platform_name)
        target = (
            platform.resolve_install_path(
                entry.kind,
                _entry_install_name(entry, platform),
            )
            if platform
            else None
        )
        expected_hash = cache_hash
        if target is not None and _is_file_asset_target(entry, target):
            expected_hash = resource_hash_path(
                _file_asset_payload_path(cache_path, kind=entry.kind)
            )
        if (
            target is None
            or not is_cc_port_managed(
                target,
                resource_key=entry.resource_key,
                file_target=_uses_sibling_marker(entry, target),
            )
            or resource_hash_path(target) != expected_hash
        ):
            raise RuntimeError(
                f"Install verification failed for {entry.name} on {platform_name}."
            )


def _verify_resource_uninstall(
    entry: RegistryItem,
    *,
    config: Config,
    platform_filter: str | None,
) -> None:
    if platform_filter is None:
        for path in {_install_path(config, entry), _clone_path(config, entry)}:
            if path.exists() or path.is_symlink():
                raise RuntimeError(
                    f"Uninstall verification failed; cache still exists for {entry.name}: {path}"
                )

    platforms = list(config.platforms.enabled())
    if platform_filter:
        platforms = [platform for platform in platforms if platform.name == platform_filter]
    for platform in platforms:
        if entry.kind == "mcp":
            mcp_path = platform.mcp_json_path()
            server_name = _entry_install_name(entry, platform)
            if (
                mcp_path
                and is_cc_port_managed_mcp(
                    mcp_path,
                    server_name,
                    resource_key=entry.resource_key,
                )
            ):
                raise RuntimeError(
                    f"Uninstall verification failed for {entry.name} on {platform.name}."
                )
            continue
        try:
            target = platform.resolve_install_path(
                entry.kind,
                _entry_install_name(entry, platform),
            )
        except ValueError:
            continue
        if target and is_cc_port_managed(
            target,
            resource_key=entry.resource_key,
            file_target=_uses_sibling_marker(entry, target),
        ):
            raise RuntimeError(
                f"Uninstall verification failed for {entry.name} on {platform.name}."
            )


def _remove_platform_installation(entry: RegistryItem, platform: PlatformProfile) -> bool:
    if entry.kind == "mcp":
        mcp_path = platform.mcp_json_path()
        server_name = _entry_install_name(entry, platform)
        if (
            not mcp_path
            or not is_cc_port_managed_mcp(
                mcp_path,
                server_name,
                resource_key=entry.resource_key,
            )
        ):
            return False
        removed = remove_mcp_server(mcp_path, server_name)
        if removed:
            unmark_cc_port_managed_mcp(mcp_path, server_name)
        return removed
    elif entry.kind in {
        "skill",
        "rule",
        "prompt",
        "plugin",
        "instruction",
        "memory",
    }:
        try:
            target = platform.resolve_install_path(
                entry.kind,
                _entry_install_name(entry, platform),
            )
        except ValueError:
            return False
    else:
        target = None

    if target is None:
        return False
    sibling_marker = _uses_sibling_marker(entry, target)
    marker = managed_marker_path(target, file_target=True) if sibling_marker else None
    if marker is not None and marker.is_symlink():
        return False
    target_exists = target.exists() or target.is_symlink()
    if not target_exists and sibling_marker:
        if not is_cc_port_managed(
            target,
            resource_key=entry.resource_key,
            file_target=True,
        ):
            return False
        return remove_managed_marker(target, file_target=True)
    if target_exists:
        if not is_cc_port_managed(
            target,
            resource_key=entry.resource_key,
            file_target=sibling_marker,
        ):
            return False
        _remove_path(target)
        if marker is not None:
            _remove_path(marker)
        return True
    return False


@dataclass
class CheckResult:
    name: str
    kind: str
    repo: str
    reachable: bool


def check_one(
    entry: RegistryItem,
    *,
    token: str | None = None,
    registry_root: Path | None = None,
) -> CheckResult:
    """Probe whether the remote repository for *entry* is reachable."""
    if _is_local_resource(entry):
        reachable = _local_source_path(entry, registry_root=registry_root).exists()
        entry.reachable = reachable
        entry.last_checked = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return CheckResult(
            name=entry.name,
            kind=entry.kind,
            repo=entry.path,
            reachable=reachable,
        )

    reachable = git_ops.probe_remote(entry.repo, entry.ref, token=token)

    entry.reachable = reachable
    entry.last_checked = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return CheckResult(
        name=entry.name,
        kind=entry.kind,
        repo=entry.repo,
        reachable=reachable,
    )


def check_all(
    *,
    config: Config,
    registry: Registry | None = None,
    registry_path: Path | None = None,
    kind: str | None = None,
    prune: bool = False,
    uninstall: bool = False,
) -> tuple[list[CheckResult], list[str]]:
    """Check reachability of every item in the registry.

    Returns ``(results, pruned_names)``.  When *prune* is True, unreachable
    entries are removed from the registry (and optionally uninstalled).
    The ``last_checked`` / ``reachable`` metadata is always persisted.
    """
    effective_registry_path = registry_path or (find_registry_path() if registry is None else None)
    reg = registry or load_registry(effective_registry_path)
    registry_root = effective_registry_path.parent if effective_registry_path else None
    token = config.github.token or None
    results: list[CheckResult] = []
    pruned: list[str] = []
    dirty = False

    for entry in list(reg.items):
        if kind and entry.kind != kind:
            continue
        cr = check_one(entry, token=token, registry_root=registry_root)
        dirty = True
        results.append(cr)
        if prune and not cr.reachable:
            if uninstall:
                uninstall_one(entry, config=config)
            reg.remove(entry.name)
            pruned.append(entry.name)

    if (dirty or pruned) and effective_registry_path is not None:
        save_registry(reg, effective_registry_path)

    return results, pruned


def _materialize_subdir(clone_path: Path, subdir: str, install_path: Path) -> None:
    """Copy ``clone_path/subdir`` to ``install_path`` (replace if exists)."""
    src = clone_path / subdir
    if not src.is_dir():
        raise git_ops.GitError(f"subdir {subdir!r} not found inside cloned repo {clone_path}.")
    if install_path.exists():
        _remove_path(install_path)
    install_path.parent.mkdir(parents=True, exist_ok=True)
    copy_resource_tree(src, install_path)


def _remove_path(path: Path) -> None:
    """Remove a file or directory, including read-only files left by git clones."""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, onerror=_make_writable_and_retry)
    else:
        try:
            path.unlink()
        except PermissionError:
            path.chmod(path.stat().st_mode | stat.S_IWRITE)
            path.unlink()


def _make_writable_and_retry(function, path: str, _exc_info) -> None:
    target = Path(path)
    try:
        target.chmod(target.stat().st_mode | stat.S_IWRITE)
    except OSError:
        pass
    function(path)
