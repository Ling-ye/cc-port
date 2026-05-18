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
from ..core.platforms import PlatformProfile
from ..core.registry import find_registry_path, load_registry, save_registry
from ..core.secrets import sanitize_mcp_config_for_storage
from ..infrastructure import git_ops
from .install_planner import InstallPlan, copy_resource_tree, plan_install
from .mcp_installer import inject_mcp_server, remove_mcp_server

# Backward-compatible alias
SkillEntry = RegistryItem
DEFAULT_SYNC_KINDS = {"skill"}
OPTIONAL_SYNC_KINDS = {"mcp", "rule", "prompt", "plugin"}


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


@dataclass
class SyncResult:
    name: str
    install_path: Path
    action: SyncAction
    detail: str = ""
    platforms_installed: list[str] = field(default_factory=list)


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
    return _install_root(config) / ".lpm" / "clones" / entry.name


# ---- Platform-aware install helpers ---- #


def _install_skill_to_platform(
    source_path: Path, platform: PlatformProfile, entry: RegistryItem
) -> Path | None:
    """Copy a skill directory to a platform's skills_dir."""
    target_dir = platform.resolve_install_path("skill", entry.install_target_name())
    if target_dir is None:
        return None
    try:
        if source_path.resolve() == target_dir.resolve():
            return target_dir
    except OSError:
        pass
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        _remove_path(target_dir)
    copy_resource_tree(source_path, target_dir)
    return target_dir


def _install_mcp_to_platform(
    platform: PlatformProfile, entry: RegistryItem
) -> Path | None:
    """Inject MCP config into a platform's mcp.json."""
    mcp_path = platform.mcp_json_path()
    if mcp_path is None or entry.mcp_config is None:
        return None
    inject_mcp_server(mcp_path, entry.name, sanitize_mcp_config_for_storage(entry.mcp_config))
    return mcp_path


def _install_rule_to_platform(
    source_path: Path, platform: PlatformProfile, entry: RegistryItem
) -> Path | None:
    """Copy rule files to a platform's rules_dir."""
    target_dir = platform.resolve_install_path("rule", entry.install_target_name())
    if target_dir is None:
        return None
    try:
        if source_path.resolve() == target_dir.resolve():
            return target_dir
    except OSError:
        pass
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        _remove_path(target_dir)
    copy_resource_tree(source_path, target_dir)
    return target_dir


def _install_plugin_to_platform(
    source_path: Path, platform: PlatformProfile, entry: RegistryItem
) -> Path | None:
    """Copy a plugin directory to a platform's plugin target."""
    target_dir = platform.resolve_install_path("plugin", entry.install_target_name())
    if target_dir is None:
        return None
    try:
        if source_path.resolve() == target_dir.resolve():
            return target_dir
    except OSError:
        pass
    if target_dir.exists():
        _remove_path(target_dir)
    copy_resource_tree(source_path, target_dir)
    return target_dir


def _distribute_to_platforms(
    config: Config,
    entry: RegistryItem,
    clone_path: Path,
    *,
    platform_filter: str | None = None,
) -> list[str]:
    """Distribute an item to all enabled platforms based on its kind.

    Returns list of platform names where installation succeeded.
    """
    platforms = config.platforms.enabled()
    if platform_filter:
        platforms = [p for p in platforms if p.name == platform_filter]

    installed_on: list[str] = []

    source = clone_path / entry.subdir if entry.subdir else clone_path

    for plat in platforms:
        result_path: Path | None = None
        if entry.kind == "skill":
            result_path = _install_skill_to_platform(source, plat, entry)
        elif entry.kind == "mcp":
            result_path = _install_mcp_to_platform(plat, entry)
        elif entry.kind in {"rule", "prompt"}:
            result_path = _install_rule_to_platform(source, plat, entry)
        elif entry.kind == "plugin":
            result_path = _install_plugin_to_platform(source, plat, entry)

        if result_path is not None:
            installed_on.append(plat.name)

    return installed_on


def _platform_targets(
    config: Config,
    entry: RegistryItem,
    *,
    platform_filter: str | None = None,
) -> list[tuple[str, Path]]:
    platforms = config.platforms.enabled()
    if platform_filter:
        platforms = [p for p in platforms if p.name == platform_filter]

    targets: list[tuple[str, Path]] = []
    for platform in platforms:
        target_path = platform.resolve_install_path(entry.kind, entry.install_target_name())
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
) -> SyncResult:
    install_path = _install_path(config, entry)
    clone_path = _clone_path(config, entry)
    if entry.lifecycle != "active":
        return SyncResult(
            name=entry.name,
            install_path=install_path,
            action=SyncAction.SKIPPED,
            detail="Resource has been removed from the active registry.",
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
                    config, entry, source_path, platform_filter=platform_filter
                )
                return SyncResult(
                    name=entry.name,
                    install_path=source_path,
                    action=SyncAction.INSTALLED,
                    platforms_installed=platforms_installed,
                )

            if install_path.exists():
                _remove_path(install_path)
            install_path.parent.mkdir(parents=True, exist_ok=True)
            if source_path.is_dir():
                copy_resource_tree(source_path, install_path)
            else:
                install_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, install_path)
            platforms_installed = _distribute_to_platforms(
                config, entry, install_path, platform_filter=platform_filter
            )
            return SyncResult(
                name=entry.name,
                install_path=install_path,
                action=SyncAction.INSTALLED,
                platforms_installed=platforms_installed,
            )

        if entry.kind == "mcp" and entry.mcp_config and not _needs_clone(entry):
            platforms_installed = _distribute_to_platforms(
                config, entry, clone_path, platform_filter=platform_filter
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
            config, entry, clone_path, platform_filter=platform_filter
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


def _is_local_resource(entry: RegistryItem) -> bool:
    return bool(entry.path) and entry.source in {"local", "owned"}


def _local_source_path(entry: RegistryItem, *, registry_root: Path | None = None) -> Path:
    root = registry_root or find_registry_path().parent
    return (root / entry.path).resolve()


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
        if only and entry.name not in only:
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
        if only and entry.name not in only:
            continue
        if entry.kind not in allowed_kinds:
            continue
        if tag_set and not (tag_set & {t.lower() for t in entry.tags}):
            continue
        preview_entries.append(entry)

    statuses = {
        entry.name: _skill_status(entry, config, registry_root=registry_root)
        for entry in preview_entries
    }

    items: list[SyncPreviewItem] = []
    for entry in preview_entries:
        items.append(
            _preview_sync_item(
                config,
                entry,
                status=statuses.get(entry.name),
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
    )


def uninstall_one(
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
            if p.exists():
                _remove_path(p)
                removed = True

    platforms = config.platforms.enabled()
    if platform_filter:
        platforms = [plat for plat in platforms if plat.name == platform_filter]

    for plat in platforms:
        if _remove_platform_installation(entry, plat):
            removed = True

    return removed


def _remove_platform_installation(entry: RegistryItem, platform: PlatformProfile) -> bool:
    if entry.kind == "skill":
        target = platform.resolve_install_path("skill", entry.install_target_name())
    elif entry.kind == "mcp":
        mcp_path = platform.mcp_json_path()
        return bool(mcp_path and remove_mcp_server(mcp_path, entry.name))
    elif entry.kind in {"rule", "prompt"}:
        target = platform.resolve_install_path("rule", entry.install_target_name())
    elif entry.kind == "plugin":
        target = platform.resolve_install_path("plugin", entry.install_target_name())
    else:
        target = None

    if target and target.exists():
        _remove_path(target)
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

    probe_url = git_ops.with_token(entry.repo, token) if token else entry.repo
    reachable = git_ops.probe_remote(probe_url, entry.ref)

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
    if not path.exists():
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
