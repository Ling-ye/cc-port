"""Resource-level commit planning and outgoing-content safety checks."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from ..core.config import (
    Config,
    default_state_dir,
    load_config,
    resource_repo_private_path_conflicts,
)
from ..core.registry import DEFAULT_CC_PORT_FILENAME, load_cc_port_settings, load_registry
from ..core.resource_files import is_resource_path_excluded
from ..core.secret_scan import find_secret_text
from ..infrastructure import git_ops
from .resource_repo_lock import resource_repo_write_lock

MANAGED_ROOT_FILES = {
    "README.md",
    "registry.yaml",
    "cc-port.yaml",
    "secrets.example.yaml",
}
MANAGED_DIRECTORIES = {
    "profiles",
    "skills",
    "rules",
    "prompts",
    "mcp",
    "plugins",
    "instructions",
    "memories",
    "resources",
}
MANAGED_EXACT_PATHS = {
    ".claude-plugin/plugin.json",
}
RESOURCE_KIND_BY_DIR = {
    "skills": "skill",
    "rules": "rule",
    "prompts": "prompt",
    "mcp": "mcp",
    "plugins": "plugin",
    "instructions": "instruction",
    "memories": "memory",
}


@dataclass(frozen=True)
class ResourceCommitIssue:
    path: str
    reason: str


@dataclass(frozen=True)
class ResourceSecretFinding:
    path: str
    reason: str
    preview: str
    commit: str = ""


@dataclass
class ResourceCommitChange:
    name: str
    kind: str
    action: str
    paths: list[str] = field(default_factory=list)


@dataclass
class ResourceCommitPlan:
    repo_path: Path
    changed_paths: list[str]
    managed_paths: list[str]
    resources: list[ResourceCommitChange]
    blocked_paths: list[ResourceCommitIssue]
    secret_findings: list[ResourceSecretFinding]
    suggested_message: str
    blocked: bool = False

    def __post_init__(self) -> None:
        self.blocked = bool(self.blocked_paths or self.secret_findings)


class ResourceCommitBlocked(git_ops.GitError):
    def __init__(self, plan: ResourceCommitPlan, *, operation: str = "commit") -> None:
        self.plan = plan
        details = [
            *(f"{item.path}: {item.reason}" for item in plan.blocked_paths[:5]),
            *(f"{item.path}: {item.reason}" for item in plan.secret_findings[:5]),
        ]
        suffix = "" if len(details) <= 5 else " ..."
        super().__init__(
            f"Resource {operation} is blocked: "
            + ("; ".join(details[:5]) or "unsafe resource changes")
            + suffix
        )


def build_resource_commit_plan(*, config: Config | None = None) -> ResourceCommitPlan:
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    root = cfg.resources.local_path_value.expanduser().resolve()
    if resource_repo_private_path_conflicts(cfg, resource_root=root):
        return _build_resource_commit_plan_unlocked(root, config=cfg)
    with resource_repo_write_lock(
        root,
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        return _build_resource_commit_plan_unlocked(root, config=cfg)


def commit_resource_changes(
    *,
    message: str,
    config: Config | None = None,
) -> ResourceCommitPlan:
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    root = cfg.resources.local_path_value.expanduser().resolve()
    if resource_repo_private_path_conflicts(cfg, resource_root=root):
        plan = _build_resource_commit_plan_unlocked(root, config=cfg)
        raise ResourceCommitBlocked(plan)
    with resource_repo_write_lock(
        root,
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        return commit_resource_changes_unlocked(root, message=message, config=cfg)


def commit_resource_changes_unlocked(
    root: Path,
    *,
    message: str,
    config: Config | None = None,
) -> ResourceCommitPlan:
    plan = _build_resource_commit_plan_unlocked(root, config=config)
    if plan.blocked:
        raise ResourceCommitBlocked(plan)
    if not plan.managed_paths:
        return plan
    git_ops.add_paths(root, plan.managed_paths)
    git_ops.commit(root, message=_commit_message(root, message))
    return plan


def validate_outgoing_resource_commits(
    root: Path,
    *,
    base_commit: str | None,
    config: Config | None = None,
) -> None:
    blocked_paths: dict[str, ResourceCommitIssue] = {}
    findings: list[ResourceSecretFinding] = []
    _add_private_boundary_issues(root, blocked_paths, config=config)
    _add_cc_port_overlay_issue(root, blocked_paths)
    registry_items = _load_registry_items(root, blocked_paths)
    memory_roots = _registered_memory_roots(registry_items)
    for item in git_ops.outgoing_commit_files(root, base_commit=base_commit):
        if not is_managed_resource_path(item.path):
            blocked_paths.setdefault(
                item.path,
                ResourceCommitIssue(
                    path=item.path,
                    reason="outgoing commit contains a non-managed path",
                ),
            )
            continue
        if item.mode == "120000":
            blocked_paths.setdefault(
                item.path,
                ResourceCommitIssue(
                    path=item.path,
                    reason="outgoing commit contains a symbolic link",
                ),
            )
            continue
        memory_path = _matching_memory_root(item.path, memory_roots)
        if memory_path is not None and Path(item.path).suffix.lower() != ".md":
            blocked_paths.setdefault(
                item.path,
                ResourceCommitIssue(
                    path=item.path,
                    reason="memory resources may contain only Markdown files",
                ),
            )
            continue
        if memory_path is None and is_resource_path_excluded(Path(item.path)):
            blocked_paths.setdefault(
                item.path,
                ResourceCommitIssue(
                    path=item.path,
                    reason="outgoing commit contains a path excluded by resource policy",
                ),
            )
            continue
        if item.text is None:
            continue
        match = find_secret_text(item.text)
        if match is not None:
            findings.append(
                ResourceSecretFinding(
                    path=item.path,
                    reason=match.reason,
                    preview=match.preview,
                    commit=item.commit,
                )
            )
    if blocked_paths or findings:
        raise ResourceCommitBlocked(
            ResourceCommitPlan(
                repo_path=root,
                changed_paths=[],
                managed_paths=[],
                resources=[],
                blocked_paths=sorted(blocked_paths.values(), key=lambda item: item.path),
                secret_findings=findings,
                suggested_message="",
            ),
            operation="push",
        )


def is_managed_resource_path(path: str) -> bool:
    normalized = _safe_relative_path(path)
    if normalized is None:
        return False
    value = normalized.as_posix()
    if value in MANAGED_ROOT_FILES or value in MANAGED_EXACT_PATHS:
        return True
    return bool(normalized.parts and normalized.parts[0] in MANAGED_DIRECTORIES)


def _build_resource_commit_plan_unlocked(
    root: Path,
    *,
    config: Config | None = None,
) -> ResourceCommitPlan:
    if not git_ops.is_repo(root):
        raise git_ops.GitError(f"Resource repo is not a git repository: {root}")
    entries = git_ops.status_entries(root)
    changed_paths: dict[str, str] = {}
    blocked: dict[str, ResourceCommitIssue] = {}
    _add_private_boundary_issues(root, blocked, config=config)
    _add_cc_port_overlay_issue(root, blocked)
    for entry in entries:
        for item_path in filter(None, (entry.path, entry.original_path)):
            changed_paths[item_path] = _merge_action(
                changed_paths.get(item_path),
                entry.action,
            )
            if not is_managed_resource_path(item_path):
                blocked.setdefault(
                    item_path,
                    ResourceCommitIssue(
                        path=item_path,
                        reason="path is outside the CC Port-managed resource scope",
                    ),
                )

    registry_items: dict[str, object] = {}
    registry_path = root / "registry.yaml"
    if "registry.yaml" in changed_paths and changed_paths["registry.yaml"] == "deleted":
        blocked["registry.yaml"] = ResourceCommitIssue(
            path="registry.yaml",
            reason="the resource registry cannot be deleted",
        )
    elif registry_path.is_file():
        registry_items = _load_registry_items(root, blocked)

    memory_roots = _registered_memory_roots(registry_items)

    managed_paths = sorted(
        path for path in changed_paths if is_managed_resource_path(path)
    )
    findings: list[ResourceSecretFinding] = []
    for item_path in managed_paths:
        if changed_paths[item_path] == "deleted":
            continue
        relative = _safe_relative_path(item_path)
        if relative is None:
            blocked[item_path] = ResourceCommitIssue(
                path=item_path,
                reason="path is not a safe repository-relative path",
            )
            continue
        target = root.joinpath(*relative.parts)
        if target.is_symlink():
            blocked[item_path] = ResourceCommitIssue(
                path=item_path,
                reason="symbolic links cannot be committed as managed resources",
            )
            continue
        memory_path = _matching_memory_root(item_path, memory_roots)
        if memory_path is not None and Path(item_path).suffix.lower() != ".md":
            blocked[item_path] = ResourceCommitIssue(
                path=item_path,
                reason="memory resources may contain only Markdown files",
            )
            continue
        if memory_path is None and is_resource_path_excluded(Path(item_path)):
            blocked[item_path] = ResourceCommitIssue(
                path=item_path,
                reason="path is excluded by the resource file policy",
            )
            continue
        finding = _scan_file(target, item_path)
        if finding is not None:
            findings.append(finding)

    changes = _resource_changes(changed_paths, registry_items)
    return ResourceCommitPlan(
        repo_path=root,
        changed_paths=sorted(changed_paths),
        managed_paths=managed_paths,
        resources=changes,
        blocked_paths=sorted(blocked.values(), key=lambda item: item.path),
        secret_findings=findings,
        suggested_message=_suggested_message(changes),
    )


def _add_private_boundary_issues(
    root: Path,
    blocked: dict[str, ResourceCommitIssue],
    *,
    config: Config | None,
) -> None:
    cfg = config or load_config()
    for label in resource_repo_private_path_conflicts(cfg, resource_root=root):
        key = f"<machine-local:{label}>"
        blocked.setdefault(
            key,
            ResourceCommitIssue(
                path=key,
                reason=(
                    "the resource repository overlaps a machine-local path; "
                    "move one of the configured roots before committing or pushing"
                ),
            ),
        )


def _add_cc_port_overlay_issue(
    root: Path,
    blocked: dict[str, ResourceCommitIssue],
) -> None:
    overlay = root / DEFAULT_CC_PORT_FILENAME
    if overlay.is_symlink() or (overlay.exists() and not overlay.is_file()):
        blocked.setdefault(
            DEFAULT_CC_PORT_FILENAME,
            ResourceCommitIssue(
                path=DEFAULT_CC_PORT_FILENAME,
                reason="cc-port.yaml must be a regular non-symlink file",
            ),
        )
        return
    try:
        load_cc_port_settings(overlay)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError):
        blocked.setdefault(
            DEFAULT_CC_PORT_FILENAME,
            ResourceCommitIssue(
                path=DEFAULT_CC_PORT_FILENAME,
                reason=(
                    "cc-port.yaml is invalid; repair its portable resource settings "
                    "before committing or pushing"
                ),
            ),
        )


def _load_registry_items(
    root: Path,
    blocked: dict[str, ResourceCommitIssue],
) -> dict[str, object]:
    registry_path = root / "registry.yaml"
    if not registry_path.is_file() or registry_path.is_symlink():
        blocked.setdefault(
            "registry.yaml",
            ResourceCommitIssue(
                path="registry.yaml",
                reason="resource registry must be a regular non-symlink file",
            ),
        )
        return {}
    try:
        return {
            item.resource_key: item
            for item in load_registry(registry_path).items
        }
    except Exception:
        blocked.setdefault(
            "registry.yaml",
            ResourceCommitIssue(
                path="registry.yaml",
                reason="resource registry is invalid and must be repaired before writing",
            ),
        )
        return {}


def _registered_memory_roots(registry_items: dict[str, object]) -> set[str]:
    return {
        str(getattr(item, "path", "") or "").strip("/")
        for item in registry_items.values()
        if getattr(item, "kind", "") == "memory"
        and str(getattr(item, "path", "") or "").strip("/")
    }


def _matching_memory_root(path: str, memory_roots: set[str]) -> str | None:
    return next(
        (
            root
            for root in sorted(memory_roots, key=len, reverse=True)
            if path.startswith(root + "/")
        ),
        None,
    )


def _scan_file(path: Path, display_path: str) -> ResourceSecretFinding | None:
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return ResourceSecretFinding(
            path=display_path,
            reason=f"file cannot be read safely: {exc}",
            preview="",
        )
    if b"\x00" in raw[: min(len(raw), 4096)]:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    match = find_secret_text(text)
    if match is None:
        return None
    return ResourceSecretFinding(
        path=display_path,
        reason=match.reason,
        preview=match.preview,
    )


def _resource_changes(
    changed_paths: dict[str, str],
    registry_items: dict[str, object],
) -> list[ResourceCommitChange]:
    grouped: dict[tuple[str, str], ResourceCommitChange] = {}
    registry_paths = sorted(
        (
            str(getattr(item, "path", "") or "").strip("/"),
            name,
            str(getattr(item, "kind", "resource")),
        )
        for name, item in registry_items.items()
        if str(getattr(item, "path", "") or "").strip("/")
    )
    for path, action in sorted(changed_paths.items()):
        name, kind = _resource_for_commit_path(path, registry_paths)
        key = (name, kind)
        current = grouped.get(key)
        if current is None:
            current = ResourceCommitChange(name=name, kind=kind, action=action)
            grouped[key] = current
        else:
            current.action = _merge_action(current.action, action)
        current.paths.append(path)
    return sorted(grouped.values(), key=lambda item: (item.kind, item.name))


def _resource_for_commit_path(
    path: str,
    registry_paths: list[tuple[str, str, str]],
) -> tuple[str, str]:
    for resource_path, name, kind in sorted(
        registry_paths,
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if path == resource_path or path.startswith(resource_path + "/"):
            return name, kind
    parts = PurePosixPath(path).parts
    if parts and parts[0] == "resources" and len(parts) >= 3:
        return parts[2], RESOURCE_KIND_BY_DIR.get(parts[1], "resource")
    if parts and parts[0] in RESOURCE_KIND_BY_DIR and len(parts) >= 2:
        return parts[1], RESOURCE_KIND_BY_DIR[parts[0]]
    return path, "metadata"


def _safe_relative_path(path: str) -> PurePosixPath | None:
    if not path or path.startswith("/") or "\\" in path:
        return None
    candidate = PurePosixPath(path)
    if any(part in {"", ".", ".."} for part in candidate.parts):
        return None
    return candidate


def _merge_action(current: str | None, incoming: str) -> str:
    if current is None or current == incoming:
        return incoming
    return "modified"


def _suggested_message(changes: list[ResourceCommitChange]) -> str:
    resource_count = sum(1 for item in changes if item.kind != "metadata")
    if resource_count == 1:
        item = next(item for item in changes if item.kind != "metadata")
        return f"cc-port: {item.action} {item.kind} {item.name}"
    if resource_count:
        return f"cc-port: update {resource_count} resources"
    return "cc-port: update resource metadata"


def _commit_message(root: Path, message: str) -> str:
    normalized = message.strip() or "cc-port: update resources"
    if git_ops.configured_commit_identity(root) is not None:
        return normalized
    if "\nCC_PORT-Device:" in normalized:
        return normalized
    return f"{normalized}\n\nCC_PORT-Device: {_anonymous_device_id()}"


def _anonymous_device_id() -> str:
    path = default_state_dir() / "device-id"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = uuid.uuid4().hex[:12]
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(value + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return value
    except FileExistsError:
        existing = path.read_text(encoding="utf-8").strip()
        return existing or value
