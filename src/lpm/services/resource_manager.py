"""Resource inventory and lifecycle operations for the desktop UI."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import Config, load_config
from ..core.models import RegistryItem, RemovedEffect
from ..core.registry import find_registry_path, load_registry, save_registry
from ..infrastructure.github_client import GithubClient
from .installer import (
    SkillStatus,
    SyncAction,
    SyncPreviewItem,
    SyncResult,
    _clone_path,
    _install_path,
    preview_sync_all,
    status_all,
    sync_one,
    uninstall_one,
)


@dataclass
class ResourceRemoteState:
    repo: str
    ref: str
    subdir: str
    reachable: bool | None
    last_checked: str | None
    can_delete_remote: bool
    delete_remote_reason: str = ""


@dataclass
class ResourceLocalState:
    source_path: Path | None
    source_exists: bool
    install_path: Path
    installed: bool
    open_path: Path | None
    target_paths: list[Path] = field(default_factory=list)


@dataclass
class ResourceActionState:
    can_install: bool
    can_uninstall: bool
    can_preview: bool
    can_open: bool
    can_delete_resource: bool
    can_delete_remote: bool
    install_reason: str = ""
    delete_reason: str = ""


@dataclass
class ResourceInventoryItem:
    entry: RegistryItem
    status: SkillStatus | None
    sync_preview: SyncPreviewItem | None
    remote_state: ResourceRemoteState
    local_state: ResourceLocalState
    actions: ResourceActionState


@dataclass
class ResourcePreviewResult:
    name: str
    path: Path | str
    text: str
    truncated: bool
    warning: str = ""


@dataclass
class ResourceDeleteResult:
    name: str
    effect: RemovedEffect
    entry: RegistryItem
    deleted_path: Path | None = None
    deleted_local_files: bool = False
    remote_repo_deleted: bool = False


def build_resource_inventory(
    *,
    config: Config | None = None,
    registry_path: Path | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    reg_path = registry_path or find_registry_path()
    registry = load_registry(reg_path)
    entries = [entry for entry in registry.items if not kind or entry.kind == kind]
    statuses = {
        item.name: item
        for item in status_all(config=cfg, registry=registry, registry_path=reg_path, kind=kind)
    }
    previews = {
        item.name: item
        for item in preview_sync_all(
            config=cfg,
            registry=registry,
            registry_path=reg_path,
            kind=kind,
            include_optional=True,
        ).items
    }

    return {
        "registry_path": reg_path,
        "items": [
            _inventory_item(
                entry,
                cfg,
                reg_path,
                status=statuses.get(entry.name),
                preview=previews.get(entry.name),
            )
            for entry in entries
        ],
    }


def install_resource(
    name: str,
    *,
    config: Config | None = None,
    registry_path: Path | None = None,
    platform_filter: str | None = None,
) -> SyncResult:
    cfg = config or load_config()
    reg_path = registry_path or find_registry_path()
    entry = _require_entry(name, reg_path)
    if entry.lifecycle != "active":
        raise ValueError(f"Resource {name!r} has been removed from the active registry.")
    result = sync_one(
        entry,
        config=cfg,
        platform_filter=platform_filter,
        registry_root=reg_path.parent,
    )
    if result.action in {SyncAction.FAILED, SyncAction.REPO_GONE, SyncAction.SKIPPED}:
        detail = f": {result.detail}" if result.detail else ""
        raise RuntimeError(f"Resource install failed for {name}: {result.action.value}{detail}")
    return result


def uninstall_resource(
    name: str,
    *,
    config: Config | None = None,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    reg_path = registry_path or find_registry_path()
    entry = _require_entry(name, reg_path)
    removed = uninstall_one(entry, config=cfg)
    status = next(
        (item for item in status_all(config=cfg, registry_path=reg_path) if item.name == name),
        None,
    )
    return {"name": name, "uninstalled": removed, "status": status}


def preview_resource(
    name: str,
    *,
    config: Config | None = None,
    registry_path: Path | None = None,
    max_bytes: int = 60_000,
) -> ResourcePreviewResult:
    cfg = config or load_config()
    reg_path = registry_path or find_registry_path()
    entry = _require_entry(name, reg_path)

    for root in _content_roots(entry, cfg, reg_path):
        target = _preview_file(root, entry)
        if target is None:
            continue
        text, truncated = _read_text_preview(target, max_bytes=max_bytes)
        return ResourcePreviewResult(
            name=name,
            path=target,
            text=text,
            truncated=truncated,
        )

    if entry.kind == "mcp" and entry.mcp_config:
        text = json.dumps(entry.mcp_config, indent=2, ensure_ascii=False)
        return ResourcePreviewResult(
            name=name,
            path="registry:mcp_config",
            text=text,
            truncated=False,
            warning="Previewing MCP config stored in registry.yaml.",
        )

    raise FileNotFoundError("No local content is available to preview. Download/register it first.")


def delete_resource(
    name: str,
    *,
    config: Config | None = None,
    registry_path: Path | None = None,
    confirm_name: str | None = None,
    reason: str = "",
) -> ResourceDeleteResult:
    cfg = config or load_config()
    reg_path = registry_path or find_registry_path()
    registry = load_registry(reg_path)
    entry = registry.get(name)
    if entry is None:
        raise ValueError(f"No item named {name!r} in registry.")

    deleted_path: Path | None = None
    deleted_local_files = False
    remote_repo_deleted = False

    if entry.source == "external":
        effect: RemovedEffect = "index_only"
    elif entry.source == "owned" and entry.repo:
        if confirm_name != name:
            raise ValueError("Type the resource name to confirm remote repository deletion.")
        if not cfg.github.token:
            raise ValueError("Set a GitHub token before deleting an owned remote repository.")
        owner, repo_name = _parse_owner_repo(entry.repo)
        remote_repo_deleted = GithubClient(cfg.github.token).delete_repo(owner, repo_name)
        effect = "remote_repo_deleted"
        entry.reachable = False
        entry.last_checked = _utc_now()
    elif entry.path:
        deleted_path, deleted_local_files = _delete_local_resource_path(reg_path.parent, entry.path)
        effect = "local_files_deleted"
    else:
        effect = "index_only"

    entry.lifecycle = "removed"
    entry.removed_at = _utc_now()
    entry.removed_reason = reason.strip() or _default_remove_reason(entry, effect)
    entry.removed_effect = effect
    save_registry(registry, reg_path)

    return ResourceDeleteResult(
        name=name,
        effect=effect,
        entry=entry,
        deleted_path=deleted_path,
        deleted_local_files=deleted_local_files,
        remote_repo_deleted=remote_repo_deleted,
    )


def resource_open_path(
    name: str,
    *,
    config: Config | None = None,
    registry_path: Path | None = None,
) -> Path:
    cfg = config or load_config()
    reg_path = registry_path or find_registry_path()
    entry = _require_entry(name, reg_path)
    path = _open_path(entry, cfg, reg_path)
    if path is None:
        raise FileNotFoundError("No local resource directory is available to open.")
    return path


def _inventory_item(
    entry: RegistryItem,
    config: Config,
    registry_path: Path,
    *,
    status: SkillStatus | None,
    preview: SyncPreviewItem | None,
) -> ResourceInventoryItem:
    install_path = status.install_path if status else _install_path(config, entry)
    source_path = _source_path(entry, registry_path)
    source_exists = bool(source_path and source_path.exists())
    open_path = _open_path(entry, config, registry_path, status=status)
    can_install = entry.lifecycle == "active" and not (preview.blocked if preview else False)
    install_reason = ""
    if entry.lifecycle != "active":
        install_reason = "Resource is removed."
    elif preview and preview.blocked:
        install_reason = "Resource is blocked by preview warnings."

    remote_can_delete = entry.source == "owned" and bool(entry.repo) and entry.lifecycle == "active"
    remote_reason = "" if remote_can_delete else "Only active owned GitHub resources can be deleted remotely."

    return ResourceInventoryItem(
        entry=entry,
        status=status,
        sync_preview=preview,
        remote_state=ResourceRemoteState(
            repo=entry.repo,
            ref=entry.ref,
            subdir=entry.subdir,
            reachable=entry.reachable,
            last_checked=entry.last_checked,
            can_delete_remote=remote_can_delete,
            delete_remote_reason=remote_reason,
        ),
        local_state=ResourceLocalState(
            source_path=source_path,
            source_exists=source_exists,
            install_path=install_path,
            installed=status.installed if status else install_path.exists(),
            open_path=open_path,
            target_paths=preview.target_paths if preview else [],
        ),
        actions=ResourceActionState(
            can_install=can_install,
            can_uninstall=bool(status and status.installed),
            can_preview=_has_preview(entry, config, registry_path),
            can_open=open_path is not None,
            can_delete_resource=entry.lifecycle == "active",
            can_delete_remote=remote_can_delete,
            install_reason=install_reason,
            delete_reason="" if entry.lifecycle == "active" else "Resource is already removed.",
        ),
    )


def _require_entry(name: str, registry_path: Path) -> RegistryItem:
    entry = load_registry(registry_path).get(name)
    if entry is None:
        raise ValueError(f"No item named {name!r} in registry.")
    return entry


def _source_path(entry: RegistryItem, registry_path: Path) -> Path | None:
    if not entry.path:
        return None
    return (registry_path.parent / entry.path).resolve()


def _content_roots(entry: RegistryItem, config: Config, registry_path: Path) -> list[Path]:
    roots: list[Path] = []
    install_path = _install_path(config, entry)
    if install_path.exists():
        roots.append(install_path)
    source_path = _source_path(entry, registry_path)
    if source_path and source_path.exists():
        roots.append(source_path)
    clone_path = _clone_path(config, entry)
    if clone_path.exists() and clone_path not in roots:
        clone_content = clone_path / entry.subdir if entry.subdir else clone_path
        roots.append(clone_content if clone_content.exists() else clone_path)
    return roots


def _open_path(
    entry: RegistryItem,
    config: Config,
    registry_path: Path,
    *,
    status: SkillStatus | None = None,
) -> Path | None:
    install_path = status.install_path if status else _install_path(config, entry)
    if install_path.exists():
        return install_path if install_path.is_dir() else install_path.parent
    source_path = _source_path(entry, registry_path)
    if source_path and source_path.exists():
        return source_path if source_path.is_dir() else source_path.parent
    clone_path = _clone_path(config, entry)
    if clone_path.exists():
        return clone_path if clone_path.is_dir() else clone_path.parent
    return None


def _has_preview(entry: RegistryItem, config: Config, registry_path: Path) -> bool:
    if entry.kind == "mcp" and entry.mcp_config:
        return True
    return any(_preview_file(root, entry) is not None for root in _content_roots(entry, config, registry_path))


def _preview_file(root: Path, entry: RegistryItem) -> Path | None:
    if root.is_file():
        return root if _is_probably_text(root) else None

    preferred = {
        "skill": ["SKILL.md", "README.md"],
        "mcp": ["mcp.json", "mcp.yaml", "mcp.yml", "README.md"],
        "rule": ["README.md"],
        "prompt": ["README.md"],
        "plugin": ["plugin.json", "package.json", "README.md", "SKILL.md"],
    }.get(entry.kind, ["README.md"])

    for name in preferred:
        candidate = root / name
        if candidate.is_file() and _is_probably_text(candidate):
            return candidate

    extensions = {".md", ".mdc", ".txt", ".json", ".yaml", ".yml", ".toml"}
    for candidate in sorted(root.rglob("*")):
        try:
            rel_parts = candidate.relative_to(root).parts
        except ValueError:
            continue
        if ".git" in rel_parts or not candidate.is_file():
            continue
        if candidate.suffix.lower() in extensions and _is_probably_text(candidate):
            return candidate
    return None


def _read_text_preview(path: Path, *, max_bytes: int) -> tuple[str, bool]:
    with path.open("rb") as f:
        data = f.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    return data.decode("utf-8", errors="replace"), truncated


def _is_probably_text(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            sample = f.read(2048)
    except OSError:
        return False
    return b"\x00" not in sample


def _delete_local_resource_path(root: Path, relative_path: str) -> tuple[Path, bool]:
    root = root.resolve()
    target = (root / relative_path).resolve()
    if target == root or root not in target.parents:
        raise ValueError(f"Refusing to delete path outside resource repository: {target}")
    if not target.exists():
        return target, False
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return target, True


def _parse_owner_repo(github_url: str) -> tuple[str, str]:
    cleaned = github_url.strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[: -len(".git")]
    if cleaned.startswith("git@github.com:"):
        path = cleaned.split(":", 1)[1]
    elif cleaned.startswith("https://github.com/"):
        path = cleaned[len("https://github.com/"):]
    else:
        raise ValueError(f"Cannot parse owner/repo from {github_url!r}.")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        raise ValueError(f"URL {github_url!r} does not look like owner/repo.")
    return parts[0], parts[1]


def _default_remove_reason(entry: RegistryItem, effect: RemovedEffect) -> str:
    if effect == "local_files_deleted":
        return "Removed local resource files from the private resource repository."
    if effect == "remote_repo_deleted":
        return "Deleted owned remote repository."
    if entry.source == "external":
        return "Removed third-party resource from the active index."
    return "Removed resource from the active index."


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
