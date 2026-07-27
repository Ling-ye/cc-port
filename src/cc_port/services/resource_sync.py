"""Safe Git synchronization for the private resource repository."""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..core.config import Config, default_state_dir, load_config, resource_repo_auth_token
from ..core.models import Registry, RegistryItem
from ..core.registry import CURRENT_REGISTRY_VERSION, save_registry
from ..infrastructure import git_ops
from .resource_commit import validate_outgoing_resource_commits
from .resource_repo import resource_root
from .resource_repo_lock import resource_repo_write_lock

SYNC_STATE_DIR = "sync"
REGISTRY_FILE = "registry.yaml"
SYNC_PLAN_SCHEMA_VERSION = 2


@dataclass
class SyncConflict:
    id: str
    path: str
    resource: str = ""
    reason: str = ""


@dataclass
class ResourceSyncPlan:
    operation_id: str
    repo_path: Path
    branch: str
    status: str
    local_commit: str | None
    remote_commit: str | None
    merge_base: str | None
    ahead: int
    behind: int
    schema_version: int = SYNC_PLAN_SCHEMA_VERSION
    repo_common_dir: str = ""
    repo_remote_url: str = ""
    worktree_path: Path | None = None
    merge_commit: str | None = None
    conflicts: list[SyncConflict] = field(default_factory=list)
    detail: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def blocked(self) -> bool:
        return bool(self.conflicts) or self.status in {
            "dirty",
            "no-remote",
            "wrong-branch",
        }


@dataclass
class StaleResourceSyncPlan:
    operation_id: str
    status: str
    repo_path: Path
    worktree_path: Path
    updated_at: str
    age_hours: float
    reason: str


def inspect_resource_sync(*, config: Config | None = None, fetch: bool = False) -> ResourceSyncPlan:
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    if fetch:
        with resource_repo_write_lock(
            resource_root(cfg),
            timeout_seconds=cfg.state.lock_timeout_seconds,
        ):
            return _inspect_resource_sync_unlocked(cfg=cfg, fetch=True)
    return _inspect_resource_sync_unlocked(cfg=cfg, fetch=False)


def _inspect_resource_sync_unlocked(*, cfg: Config, fetch: bool) -> ResourceSyncPlan:
    root = resource_root(cfg)
    branch = cfg.resources.branch or "main"
    if not git_ops.is_repo(root):
        raise git_ops.GitError(f"Resource repo is not a git repository: {root}")
    if fetch:
        git_ops.fetch(root, token=resource_repo_auth_token(cfg))
    divergence = git_ops.divergence(root, branch=branch)
    current_branch = git_ops.current_branch(root)
    if git_ops.status_short(root):
        status = "dirty"
        detail = "Commit or discard local changes before applying synchronization."
    elif current_branch != branch:
        status = "wrong-branch"
        detail = (
            f"Configured branch is {branch!r}, but the repository is on "
            f"{current_branch or 'a detached HEAD'!r}."
        )
    else:
        status = divergence.state
        detail = ""
    return ResourceSyncPlan(
        operation_id="",
        repo_path=root,
        branch=branch,
        status=status,
        local_commit=divergence.local_commit,
        remote_commit=divergence.remote_commit,
        merge_base=divergence.merge_base,
        ahead=divergence.ahead,
        behind=divergence.behind,
        detail=detail,
    )


def build_resource_sync_plan(*, config: Config | None = None) -> ResourceSyncPlan:
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    root = resource_root(cfg)
    with resource_repo_write_lock(
        root,
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        active = _active_plan_for_repo(root)
        if active is not None:
            raise git_ops.GitError(
                "Resource repository already has a pending synchronization "
                f"operation: {active.operation_id}. Apply, cancel, or clean it first."
            )
        return _build_resource_sync_plan_unlocked(cfg=cfg)


def _build_resource_sync_plan_unlocked(*, cfg: Config) -> ResourceSyncPlan:
    root = resource_root(cfg)
    branch = cfg.resources.branch or "main"
    if not git_ops.is_repo(root):
        raise git_ops.GitError(f"Resource repo is not a git repository: {root}")
    if git_ops.status_short(root):
        return _persist_plan(
            ResourceSyncPlan(
                operation_id=uuid.uuid4().hex,
                repo_path=root,
                branch=branch,
                status="dirty",
                local_commit=git_ops.head_commit(root),
                remote_commit=None,
                merge_base=None,
                ahead=0,
                behind=0,
                detail="Commit or discard local changes before applying synchronization.",
            )
        )
    current_branch = git_ops.current_branch(root)
    if current_branch != branch:
        return _persist_plan(
            ResourceSyncPlan(
                operation_id=uuid.uuid4().hex,
                repo_path=root,
                branch=branch,
                status="wrong-branch",
                local_commit=git_ops.head_commit(root),
                remote_commit=git_ops.rev_parse(root, f"origin/{branch}"),
                merge_base=None,
                ahead=0,
                behind=0,
                detail=(
                    f"Configured branch is {branch!r}, but the repository is on "
                    f"{current_branch or 'a detached HEAD'!r}."
                ),
            )
        )

    git_ops.fetch(root, token=resource_repo_auth_token(cfg))
    divergence = git_ops.divergence(root, branch=branch)
    plan = ResourceSyncPlan(
        operation_id=uuid.uuid4().hex,
        repo_path=root,
        branch=branch,
        status=divergence.state,
        local_commit=divergence.local_commit,
        remote_commit=divergence.remote_commit,
        merge_base=divergence.merge_base,
        ahead=divergence.ahead,
        behind=divergence.behind,
    )
    if divergence.state != "diverged":
        return _persist_plan(plan)
    if not divergence.local_commit or not divergence.remote_commit:
        plan.status = "no-remote"
        plan.detail = "Both local and remote commits are required for a three-way merge."
        return _persist_plan(plan)

    worktree = _operation_dir(plan.operation_id) / "worktree"
    git_ops.worktree_add(root, worktree, divergence.local_commit)
    plan.worktree_path = worktree
    merged, detail = git_ops.merge_no_ff(worktree, divergence.remote_commit)
    plan.detail = detail
    if merged:
        plan.status = "ready"
        plan.merge_commit = git_ops.head_commit(worktree)
    else:
        plan.status = "conflict"
        plan.conflicts = _build_conflicts(worktree)
        if not plan.conflicts:
            _cleanup_worktree(plan)
            raise git_ops.GitError(f"Git merge failed without resolvable conflicts: {detail}")
    return _persist_plan(plan)


def resolve_resource_sync_plan(
    operation_id: str,
    choices: dict[str, str],
    *,
    config: Config | None = None,
) -> ResourceSyncPlan:
    initial = load_resource_sync_plan(operation_id)
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    with resource_repo_write_lock(
        initial.repo_path,
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        return _resolve_resource_sync_plan_unlocked(
            operation_id,
            choices,
            config=config,
        )


def _resolve_resource_sync_plan_unlocked(
    operation_id: str,
    choices: dict[str, str],
    *,
    config: Config | None,
) -> ResourceSyncPlan:
    plan = load_resource_sync_plan(operation_id)
    _validate_plan_repository(plan, config=config)
    if plan.status != "conflict" or not plan.worktree_path:
        raise ValueError("Sync plan is not waiting for conflict resolution.")
    worktree = plan.worktree_path
    normalized = _normalize_choices(choices)

    unresolved: list[str] = []
    if REGISTRY_FILE in git_ops.unresolved_paths(worktree):
        unresolved.extend(_resolve_registry(worktree, normalized))

    for conflict in plan.conflicts:
        if conflict.path == REGISTRY_FILE:
            continue
        choice = normalized.get(conflict.id) or normalized.get(f"file:{conflict.path}")
        if choice is None:
            unresolved.append(conflict.id)
            continue
        _resolve_file_conflict(worktree, conflict.path, choice)

    remaining_paths = git_ops.unresolved_paths(worktree)
    if unresolved or remaining_paths:
        missing = sorted(set(unresolved + [f"file:{item}" for item in remaining_paths]))
        raise ValueError("Unresolved sync choices: " + ", ".join(missing))

    plan.merge_commit = git_ops.commit_pending_merge(worktree)
    plan.status = "ready"
    plan.conflicts = []
    plan.detail = ""
    return _persist_plan(plan)


def apply_resource_sync_plan(
    operation_id: str,
    *,
    config: Config | None = None,
) -> ResourceSyncPlan:
    initial = load_resource_sync_plan(operation_id)
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    with resource_repo_write_lock(
        initial.repo_path,
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        return _apply_resource_sync_plan_unlocked(operation_id, config=config)


def _apply_resource_sync_plan_unlocked(
    operation_id: str,
    *,
    config: Config | None,
) -> ResourceSyncPlan:
    plan = load_resource_sync_plan(operation_id)
    _validate_plan_repository(plan, config=config)
    root = plan.repo_path
    if git_ops.status_short(root):
        raise git_ops.GitError("Resource repo changed after planning; rebuild the sync plan.")
    if git_ops.head_commit(root) != plan.local_commit:
        raise git_ops.GitError("Resource repo HEAD changed after planning; rebuild the sync plan.")

    if plan.status == "behind":
        if not plan.remote_commit:
            raise git_ops.GitError("Sync plan has no remote commit.")
        git_ops.merge_ff_only(root, plan.remote_commit)
    elif plan.status == "unborn":
        if not plan.remote_commit:
            raise git_ops.GitError("Sync plan has no remote commit.")
        git_ops.checkout_branch_at(root, plan.branch, plan.remote_commit)
    elif plan.status == "ready":
        if not plan.merge_commit:
            raise git_ops.GitError("Resolved sync plan has no merge commit.")
        git_ops.merge_ff_only(root, plan.merge_commit)
    elif plan.status in {"clean", "ahead"}:
        pass
    else:
        raise git_ops.GitError(f"Sync plan cannot be applied while status is {plan.status!r}.")

    plan.status = "applied"
    _cleanup_worktree(plan)
    plan.worktree_path = None
    return _persist_plan(plan)


def push_resource_sync(*, config: Config | None = None) -> ResourceSyncPlan:
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    with resource_repo_write_lock(
        resource_root(cfg),
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        return _push_resource_sync_unlocked(cfg=cfg)


def _push_resource_sync_unlocked(*, cfg: Config) -> ResourceSyncPlan:
    plan = _inspect_resource_sync_unlocked(cfg=cfg, fetch=True)
    if plan.status == "dirty":
        raise git_ops.GitError("Commit local resource changes before pushing.")
    if plan.status == "wrong-branch":
        raise git_ops.GitError(plan.detail)
    if plan.status in {"behind", "diverged"}:
        raise git_ops.GitError("Remote resource history changed; build and apply a sync plan first.")
    validate_outgoing_resource_commits(
        plan.repo_path,
        base_commit=plan.remote_commit,
    )
    git_ops.push(
        plan.repo_path,
        branch=plan.branch,
        token=resource_repo_auth_token(cfg),
    )
    return _inspect_resource_sync_unlocked(cfg=cfg, fetch=False)


def cancel_resource_sync_plan(
    operation_id: str,
    *,
    config: Config | None = None,
) -> ResourceSyncPlan:
    initial = load_resource_sync_plan(operation_id)
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    with resource_repo_write_lock(
        initial.repo_path,
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        return _cancel_resource_sync_plan_unlocked(operation_id, config=config)


def _cancel_resource_sync_plan_unlocked(
    operation_id: str,
    *,
    config: Config | None,
) -> ResourceSyncPlan:
    plan = load_resource_sync_plan(operation_id)
    _validate_plan_repository(plan, config=config, allow_missing=True)
    if plan.status == "applied":
        raise ValueError("An applied sync plan cannot be cancelled.")
    _cleanup_worktree(plan)
    plan.worktree_path = None
    plan.status = "cancelled"
    plan.conflicts = []
    plan.detail = ""
    return _persist_plan(plan)


def list_stale_resource_sync_plans(
    *,
    min_age_hours: float = 24,
    now: datetime | None = None,
) -> list[StaleResourceSyncPlan]:
    current = now or datetime.now(timezone.utc)
    stale: list[StaleResourceSyncPlan] = []
    root = default_state_dir() / SYNC_STATE_DIR
    if not root.is_dir():
        return stale
    for plan_path in root.glob("*/plan.json"):
        try:
            plan = load_resource_sync_plan(plan_path.parent.name)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
        if plan.status not in {"conflict", "ready"} or plan.worktree_path is None:
            continue
        updated = _plan_timestamp(plan, plan_path)
        age = max(0.0, (current - updated).total_seconds() / 3600)
        if age < min_age_hours:
            continue
        stale.append(
            StaleResourceSyncPlan(
                operation_id=plan.operation_id,
                status=plan.status,
                repo_path=plan.repo_path,
                worktree_path=plan.worktree_path,
                updated_at=updated.isoformat(),
                age_hours=round(age, 2),
                reason=f"Pending sync plan has not changed for at least {min_age_hours:g} hours.",
            )
        )
    return sorted(stale, key=lambda item: item.updated_at)


def cleanup_stale_resource_sync_plan(
    operation_id: str,
    *,
    min_age_hours: float = 24,
    force: bool = False,
    config: Config | None = None,
) -> ResourceSyncPlan:
    initial = load_resource_sync_plan(operation_id)
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    with resource_repo_write_lock(
        initial.repo_path,
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        return _cleanup_stale_resource_sync_plan_unlocked(
            operation_id,
            min_age_hours=min_age_hours,
            force=force,
            config=config,
        )


def _cleanup_stale_resource_sync_plan_unlocked(
    operation_id: str,
    *,
    min_age_hours: float,
    force: bool,
    config: Config | None,
) -> ResourceSyncPlan:
    plan = load_resource_sync_plan(operation_id)
    _validate_plan_repository(plan, config=config, allow_missing=True)
    if plan.status not in {"conflict", "ready"} or plan.worktree_path is None:
        raise ValueError("Only pending conflict or ready plans with a worktree can be cleaned.")
    if not force:
        stale_ids = {
            item.operation_id
            for item in list_stale_resource_sync_plans(min_age_hours=min_age_hours)
        }
        if operation_id not in stale_ids:
            raise ValueError(
                f"Sync plan is newer than {min_age_hours:g} hours; use force to abandon it."
            )
    _cleanup_worktree(plan)
    plan.worktree_path = None
    plan.status = "abandoned"
    plan.conflicts = []
    plan.detail = "Temporary merge worktree was explicitly cleaned."
    return _persist_plan(plan)


def load_resource_sync_plan(operation_id: str) -> ResourceSyncPlan:
    path = _operation_dir(operation_id) / "plan.json"
    if not path.is_file():
        raise FileNotFoundError(f"Unknown resource sync operation: {operation_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    schema_version = int(data.get("schema_version", 0))
    if schema_version != SYNC_PLAN_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported resource sync plan schema: {schema_version or 'missing'}."
        )
    stored_operation_id = str(data.get("operation_id") or "")
    if stored_operation_id != operation_id:
        raise ValueError("Resource sync plan operation id does not match its directory.")
    repo_path = _canonical_path(Path(str(data["repo_path"])))
    has_worktree = bool(data.get("has_worktree", False))
    return ResourceSyncPlan(
        operation_id=stored_operation_id,
        repo_path=repo_path,
        branch=str(data["branch"]),
        status=str(data["status"]),
        local_commit=data.get("local_commit"),
        remote_commit=data.get("remote_commit"),
        merge_base=data.get("merge_base"),
        ahead=int(data.get("ahead", 0)),
        behind=int(data.get("behind", 0)),
        schema_version=schema_version,
        repo_common_dir=str(data.get("repo_common_dir") or ""),
        repo_remote_url=str(data.get("repo_remote_url") or ""),
        worktree_path=_safe_worktree_path(operation_id) if has_worktree else None,
        merge_commit=data.get("merge_commit"),
        conflicts=[SyncConflict(**item) for item in data.get("conflicts", [])],
        detail=str(data.get("detail") or ""),
        created_at=str(data.get("created_at") or ""),
        updated_at=str(data.get("updated_at") or ""),
    )


def _active_plan_for_repo(repo_path: Path) -> ResourceSyncPlan | None:
    sync_root = default_state_dir() / SYNC_STATE_DIR
    if not sync_root.is_dir():
        return None
    expected_repo = _canonical_path(repo_path)
    for plan_path in sorted(sync_root.glob("*/plan.json")):
        try:
            plan = load_resource_sync_plan(plan_path.parent.name)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
        if plan.repo_path == expected_repo and plan.status in {"conflict", "ready"}:
            return plan
    return None


def _build_conflicts(worktree: Path) -> list[SyncConflict]:
    registry_paths = _registry_resource_paths(worktree)
    conflicts: list[SyncConflict] = []
    for path in git_ops.unresolved_paths(worktree):
        if path == REGISTRY_FILE:
            for resource_name in _registry_conflicting_names(worktree):
                conflicts.append(
                    SyncConflict(
                        id=f"resource:{resource_name}",
                        path=path,
                        resource=resource_name,
                        reason="Resource metadata changed on both sides.",
                    )
                )
            continue
        resource_name = _resource_for_path(path, registry_paths)
        conflict_id = f"resource:{resource_name}" if resource_name else f"file:{path}"
        conflicts.append(
            SyncConflict(
                id=conflict_id,
                path=path,
                resource=resource_name,
                reason="Resource content changed on both sides.",
            )
        )
    unique: dict[tuple[str, str], SyncConflict] = {}
    for conflict in conflicts:
        unique[(conflict.id, conflict.path)] = conflict
    return sorted(unique.values(), key=lambda item: (item.id, item.path))


def _registry_conflicting_names(worktree: Path) -> list[str]:
    base, local, incoming = _registry_stages(worktree)
    names = set(base) | set(local) | set(incoming)
    return sorted(
        name
        for name in names
        if local.get(name) != incoming.get(name)
        and local.get(name) != base.get(name)
        and incoming.get(name) != base.get(name)
    )


def _resolve_registry(worktree: Path, choices: dict[str, str]) -> list[str]:
    base, local, incoming = _registry_stages(worktree)
    selected: list[RegistryItem] = []
    unresolved: list[str] = []
    for name in sorted(set(base) | set(local) | set(incoming)):
        base_item = base.get(name)
        local_item = local.get(name)
        incoming_item = incoming.get(name)
        if local_item == incoming_item:
            value = local_item
        elif local_item == base_item:
            value = incoming_item
        elif incoming_item == base_item:
            value = local_item
        else:
            conflict_id = f"resource:{name}"
            choice = choices.get(conflict_id)
            if choice is None and ":" in name:
                choice = choices.get(f"resource:{name.split(':', 1)[1]}")
            if choice is None:
                unresolved.append(conflict_id)
                continue
            value = local_item if choice == "local" else incoming_item
        if value is not None:
            selected.append(RegistryItem.model_validate(value))
    if unresolved:
        return unresolved
    save_registry(
        Registry(version=CURRENT_REGISTRY_VERSION, items=selected),
        worktree / REGISTRY_FILE,
    )
    git_ops.add_paths(worktree, [REGISTRY_FILE])
    return []


def _registry_stages(
    worktree: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    return tuple(
        _registry_item_map(git_ops.show_index_stage(worktree, REGISTRY_FILE, stage))
        for stage in (1, 2, 3)
    )  # type: ignore[return-value]


def _registry_item_map(text: str | None) -> dict[str, dict[str, Any]]:
    if not text:
        return {}
    data = yaml.safe_load(text) or {}
    raw_items = data.get("items", data.get("skills", [])) if isinstance(data, dict) else []
    if not isinstance(raw_items, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in raw_items:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        kind = str(item.get("kind") or "skill")
        name = str(item["name"])
        result[f"{kind}:{name}"] = dict(item)
    return result


def _registry_resource_paths(worktree: Path) -> dict[str, str]:
    paths: dict[str, str] = {}
    for stage in (2, 3):
        for name, item in _registry_item_map(
            git_ops.show_index_stage(worktree, REGISTRY_FILE, stage)
        ).items():
            relative_path = str(item.get("path") or "").strip("/")
            if relative_path:
                paths[relative_path] = name
    if not paths:
        registry_path = worktree / REGISTRY_FILE
        if registry_path.is_file():
            for name, item in _registry_item_map(
                registry_path.read_text(encoding="utf-8")
            ).items():
                relative_path = str(item.get("path") or "").strip("/")
                if relative_path:
                    paths[relative_path] = name
    return paths


def _resource_for_path(path: str, resource_paths: dict[str, str]) -> str:
    normalized = path.replace("\\", "/").strip("/")
    matches = [
        (resource_path, name)
        for resource_path, name in resource_paths.items()
        if normalized == resource_path or normalized.startswith(resource_path + "/")
    ]
    if not matches:
        return ""
    return max(matches, key=lambda item: len(item[0]))[1]


def _resolve_file_conflict(worktree: Path, path: str, choice: str) -> None:
    stage = 2 if choice == "local" else 3
    if git_ops.show_index_stage(worktree, path, stage) is None:
        target = worktree / path
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        git_ops.add_paths(worktree, [path])
        return
    git_ops.checkout_conflict_version(worktree, path, choice=choice)


def _normalize_choices(choices: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in choices.items():
        item_id = str(key).strip()
        choice = str(value).strip()
        if choice not in {"local", "incoming"}:
            raise ValueError(f"Choice for {item_id!r} must be 'local' or 'incoming'.")
        normalized[item_id] = choice
    return normalized


def _persist_plan(plan: ResourceSyncPlan) -> ResourceSyncPlan:
    operation_dir = _operation_dir(plan.operation_id)
    operation_dir.mkdir(parents=True, exist_ok=True)
    plan.schema_version = SYNC_PLAN_SCHEMA_VERSION
    plan.repo_path = _canonical_path(plan.repo_path)
    if git_ops.is_repo(plan.repo_path):
        common_dir = git_ops.common_dir(plan.repo_path)
        if common_dir is None:
            raise git_ops.GitError(
                f"Unable to identify resource Git repository: {plan.repo_path}"
            )
        if not plan.repo_common_dir:
            plan.repo_common_dir = str(common_dir)
        if not plan.repo_remote_url:
            plan.repo_remote_url = git_ops.current_remote_url(plan.repo_path) or ""
    now = datetime.now(timezone.utc).isoformat()
    if not plan.created_at:
        plan.created_at = now
    plan.updated_at = now
    payload = asdict(plan)
    payload["repo_path"] = str(plan.repo_path)
    payload.pop("worktree_path", None)
    payload["has_worktree"] = plan.worktree_path is not None
    temp_path = operation_dir / ".plan.json.tmp"
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp_path.replace(operation_dir / "plan.json")
    return plan


def _cleanup_worktree(plan: ResourceSyncPlan) -> None:
    if plan.worktree_path:
        expected = _safe_worktree_path(plan.operation_id)
        if _canonical_path(plan.worktree_path) != expected:
            raise ValueError("Sync worktree path is outside its operation directory.")
        _assert_safe_delete_target(expected, plan.operation_id)
        repo_exists = git_ops.is_repo(plan.repo_path)
        if repo_exists:
            git_ops.worktree_remove(plan.repo_path, expected, force=True)
        if expected.exists():
            _assert_safe_delete_target(expected, plan.operation_id)
            shutil.rmtree(expected, ignore_errors=True)
        if repo_exists:
            git_ops.worktree_prune(plan.repo_path)


def _operation_dir(operation_id: str) -> Path:
    safe_id = operation_id.strip()
    if not safe_id or any(char not in "0123456789abcdef" for char in safe_id.lower()):
        raise ValueError("Invalid sync operation id.")
    return _canonical_path(default_state_dir() / SYNC_STATE_DIR / safe_id)


def _safe_worktree_path(operation_id: str) -> Path:
    return _canonical_path(_operation_dir(operation_id) / "worktree")


def _canonical_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _assert_safe_delete_target(path: Path, operation_id: str) -> None:
    operation_dir = _operation_dir(operation_id)
    sync_root = _canonical_path(default_state_dir() / SYNC_STATE_DIR)
    target = _canonical_path(path)
    if operation_dir.parent != sync_root:
        raise ValueError("Sync operation directory is outside the CC Port state directory.")
    if target != operation_dir / "worktree" or target.parent != operation_dir:
        raise ValueError("Refusing to remove a path outside the sync operation directory.")
    if path.is_symlink() or operation_dir.is_symlink():
        raise ValueError("Refusing to remove a symbolic-link sync worktree.")


def _validate_plan_repository(
    plan: ResourceSyncPlan,
    *,
    config: Config | None,
    allow_missing: bool = False,
) -> None:
    if plan.schema_version != SYNC_PLAN_SCHEMA_VERSION:
        raise ValueError("Resource sync plan schema is invalid.")
    if config is not None and _canonical_path(resource_root(config)) != plan.repo_path:
        raise git_ops.GitError(
            "Configured resource repository changed after planning; rebuild the sync plan."
        )
    if not git_ops.is_repo(plan.repo_path):
        if allow_missing and not plan.repo_path.exists():
            return
        raise git_ops.GitError(
            f"Planned resource repository is no longer available: {plan.repo_path}"
        )
    common_dir = git_ops.common_dir(plan.repo_path)
    if common_dir is None or str(common_dir) != plan.repo_common_dir:
        raise git_ops.GitError(
            "Resource repository identity changed after planning; rebuild the sync plan."
        )
    remote_url = git_ops.current_remote_url(plan.repo_path) or ""
    if remote_url != plan.repo_remote_url:
        raise git_ops.GitError(
            "Resource repository remote changed after planning; rebuild the sync plan."
        )


def _plan_timestamp(plan: ResourceSyncPlan, plan_path: Path) -> datetime:
    raw = plan.updated_at or plan.created_at
    if raw:
        try:
            parsed = datetime.fromisoformat(raw)
            return (
                parsed.replace(tzinfo=timezone.utc)
                if parsed.tzinfo is None
                else parsed.astimezone(timezone.utc)
            )
        except ValueError:
            pass
    return datetime.fromtimestamp(plan_path.stat().st_mtime, tz=timezone.utc)
