"""Preview and explicitly prune persisted operation records and backups."""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..core.config import Config, default_state_dir, load_config
from .operation_state import OperationRecord, list_operations, operation_path
from .state_lock import acquire_target_locks
from .state_maintenance import (
    list_orphan_backups,
    path_size,
    write_maintenance_audit,
)

MIB = 1024 * 1024


@dataclass(frozen=True)
class StateRetentionPolicy:
    retention_days: int
    keep_latest_operations: int
    max_backup_mb: int
    max_backup_bytes: int


@dataclass
class StateRetentionCandidate:
    operation_id: str
    kind: str
    status: str
    timestamp: str
    age_days: float
    record_bytes: int
    backup_bytes: int
    reclaimable_bytes: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class StateRetentionPlan:
    generated_at: str
    state_root: Path
    policy: StateRetentionPolicy
    operation_count: int
    running_operation_count: int
    protected_operation_count: int
    operation_record_bytes: int
    backup_bytes: int
    orphan_backup_count: int
    orphan_backup_bytes: int
    candidate_count: int
    reclaimable_bytes: int
    projected_backup_bytes: int
    candidates: list[StateRetentionCandidate]


@dataclass
class StatePruneFailure:
    operation_id: str
    error: str


@dataclass
class StatePruneResult:
    cleanup_id: str
    deleted_operation_ids: list[str]
    failed: list[StatePruneFailure]
    reclaimed_bytes: int
    audit_path: Path


def build_state_retention_plan(
    *,
    config: Config | None = None,
    retention_days: int | None = None,
    keep_latest_operations: int | None = None,
    max_backup_mb: int | None = None,
    now: datetime | None = None,
) -> StateRetentionPlan:
    cfg = config or load_config()
    policy = _policy(
        cfg,
        retention_days=retention_days,
        keep_latest_operations=keep_latest_operations,
        max_backup_mb=max_backup_mb,
    )
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    records = list_operations()
    terminal = [record for record in records if record.status != "running"]
    protected_ids = {
        record.operation_id
        for record in terminal[: policy.keep_latest_operations]
    }
    backup_root = default_state_dir() / "backups"
    backup_sizes = {
        record.operation_id: path_size(backup_root / record.operation_id)
        for record in records
    }
    record_sizes = {
        record.operation_id: path_size(operation_path(record.operation_id))
        for record in records
    }
    candidate_map: dict[str, StateRetentionCandidate] = {}

    for record in reversed(terminal):
        if record.operation_id in protected_ids:
            continue
        timestamp = _record_timestamp(record)
        age_days = max(0.0, (current - timestamp).total_seconds() / 86400)
        if age_days >= policy.retention_days:
            candidate_map[record.operation_id] = _candidate(
                record,
                timestamp,
                age_days,
                record_sizes[record.operation_id],
                backup_sizes[record.operation_id],
                ["retention-expired"],
            )

    total_backup_bytes = sum(backup_sizes.values())
    projected_backup_bytes = total_backup_bytes - sum(
        candidate.backup_bytes for candidate in candidate_map.values()
    )
    if policy.max_backup_bytes > 0 and projected_backup_bytes > policy.max_backup_bytes:
        for record in reversed(terminal):
            if record.operation_id in protected_ids:
                continue
            timestamp = _record_timestamp(record)
            age_days = max(0.0, (current - timestamp).total_seconds() / 86400)
            candidate = candidate_map.get(record.operation_id)
            if candidate is None:
                if backup_sizes[record.operation_id] == 0:
                    continue
                candidate = _candidate(
                    record,
                    timestamp,
                    age_days,
                    record_sizes[record.operation_id],
                    backup_sizes[record.operation_id],
                    ["backup-capacity"],
                )
                candidate_map[record.operation_id] = candidate
                projected_backup_bytes -= candidate.backup_bytes
            elif "backup-capacity" not in candidate.reasons:
                candidate.reasons.append("backup-capacity")
            if projected_backup_bytes <= policy.max_backup_bytes:
                break

    candidates = sorted(
        candidate_map.values(),
        key=lambda item: (item.timestamp, item.operation_id),
    )
    orphan_backups = list_orphan_backups()
    orphan_backup_bytes = sum(item.size_bytes for item in orphan_backups)
    operation_root = default_state_dir() / "operations"
    operation_record_bytes = (
        sum(path_size(path) for path in operation_root.glob("*.json"))
        if operation_root.is_dir()
        else 0
    )
    return StateRetentionPlan(
        generated_at=current.isoformat(),
        state_root=default_state_dir(),
        policy=policy,
        operation_count=len(records),
        running_operation_count=sum(
            1 for record in records if record.status == "running"
        ),
        protected_operation_count=len(protected_ids),
        operation_record_bytes=operation_record_bytes,
        backup_bytes=total_backup_bytes + orphan_backup_bytes,
        orphan_backup_count=len(orphan_backups),
        orphan_backup_bytes=orphan_backup_bytes,
        candidate_count=len(candidates),
        reclaimable_bytes=sum(item.reclaimable_bytes for item in candidates),
        projected_backup_bytes=max(0, projected_backup_bytes) + orphan_backup_bytes,
        candidates=candidates,
    )


def prune_state(
    operation_ids: list[str],
    *,
    config: Config | None = None,
    retention_days: int | None = None,
    keep_latest_operations: int | None = None,
    max_backup_mb: int | None = None,
) -> StatePruneResult:
    cfg = config or load_config()
    requested = list(dict.fromkeys(operation_ids))
    if not requested:
        raise ValueError("Select at least one operation from a retention plan.")
    for operation_id in requested:
        operation_path(operation_id)

    state_root = default_state_dir()
    maintenance_lock = state_root / "maintenance" / "retention"
    with acquire_target_locks(
        [maintenance_lock],
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        plan = build_state_retention_plan(
            config=cfg,
            retention_days=retention_days,
            keep_latest_operations=keep_latest_operations,
            max_backup_mb=max_backup_mb,
        )
        current_candidates = {
            item.operation_id: item for item in plan.candidates
        }
        invalid = [item for item in requested if item not in current_candidates]
        if invalid:
            raise ValueError(
                "Operations are no longer eligible under the current retention plan: "
                + ", ".join(invalid)
            )

        lock_targets: list[Path] = []
        for operation_id in requested:
            lock_targets.extend(
                [
                    operation_path(operation_id),
                    state_root / "backups" / operation_id,
                ]
            )
        with acquire_target_locks(
            lock_targets,
            timeout_seconds=cfg.state.lock_timeout_seconds,
        ):
            result = _prune_locked(requested, plan, current_candidates)
    return result


def _prune_locked(
    requested: list[str],
    plan: StateRetentionPlan,
    candidates: dict[str, StateRetentionCandidate],
) -> StatePruneResult:
    cleanup_id = uuid4().hex
    state_root = default_state_dir()
    trash_root = state_root / "maintenance" / "trash" / cleanup_id
    deleted: list[str] = []
    failures: list[StatePruneFailure] = []
    reclaimed = 0

    for operation_id in requested:
        record = operation_path(operation_id)
        backup = state_root / "backups" / operation_id
        stage = trash_root / operation_id
        stage.mkdir(parents=True, exist_ok=True)
        moved_record = stage / "operation.json"
        moved_backup = stage / "backup"
        try:
            if backup.exists() or backup.is_symlink():
                backup.replace(moved_backup)
            record.replace(moved_record)
            shutil.rmtree(stage)
            deleted.append(operation_id)
            reclaimed += candidates[operation_id].reclaimable_bytes
        except Exception as exc:
            restore_errors: list[str] = []
            try:
                if moved_record.exists():
                    moved_record.replace(record)
            except OSError as restore_exc:
                restore_errors.append(f"record restore: {restore_exc}")
            try:
                if moved_backup.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    moved_backup.replace(backup)
            except OSError as restore_exc:
                restore_errors.append(f"backup restore: {restore_exc}")
            detail = str(exc)
            if restore_errors:
                detail += " | " + "; ".join(restore_errors)
            failures.append(
                StatePruneFailure(operation_id=operation_id, error=detail)
            )
            try:
                stage.rmdir()
            except OSError:
                pass

    if trash_root.exists():
        try:
            trash_root.rmdir()
            trash_root.parent.rmdir()
        except OSError:
            pass
    audit_path = _write_prune_audit(
        cleanup_id,
        plan=plan,
        requested=requested,
        deleted=deleted,
        failures=failures,
        reclaimed_bytes=reclaimed,
    )
    return StatePruneResult(
        cleanup_id=cleanup_id,
        deleted_operation_ids=deleted,
        failed=failures,
        reclaimed_bytes=reclaimed,
        audit_path=audit_path,
    )


def _policy(
    config: Config,
    *,
    retention_days: int | None,
    keep_latest_operations: int | None,
    max_backup_mb: int | None,
) -> StateRetentionPolicy:
    days = config.state.retention_days if retention_days is None else retention_days
    keep = (
        config.state.keep_latest_operations
        if keep_latest_operations is None
        else keep_latest_operations
    )
    capacity_mb = config.state.max_backup_mb if max_backup_mb is None else max_backup_mb
    if days < 0 or keep < 0 or capacity_mb < 0:
        raise ValueError("Retention values must be non-negative.")
    return StateRetentionPolicy(
        retention_days=days,
        keep_latest_operations=keep,
        max_backup_mb=capacity_mb,
        max_backup_bytes=capacity_mb * MIB,
    )


def _candidate(
    record: OperationRecord,
    timestamp: datetime,
    age_days: float,
    record_bytes: int,
    backup_bytes: int,
    reasons: list[str],
) -> StateRetentionCandidate:
    return StateRetentionCandidate(
        operation_id=record.operation_id,
        kind=record.kind,
        status=record.status,
        timestamp=timestamp.isoformat(),
        age_days=round(age_days, 2),
        record_bytes=record_bytes,
        backup_bytes=backup_bytes,
        reclaimable_bytes=record_bytes + backup_bytes,
        reasons=reasons,
    )


def _record_timestamp(record: OperationRecord) -> datetime:
    raw = record.finished_at or record.started_at
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _write_prune_audit(
    cleanup_id: str,
    *,
    plan: StateRetentionPlan,
    requested: list[str],
    deleted: list[str],
    failures: list[StatePruneFailure],
    reclaimed_bytes: int,
) -> Path:
    audit_id = f"prune-{cleanup_id}"
    payload = {
        "audit_id": audit_id,
        "action": "state-prune",
        "status": (
            "partial"
            if deleted and failures
            else "failed"
            if failures
            else "succeeded"
        ),
        "cleanup_id": cleanup_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "policy": asdict(plan.policy),
        "requested_operation_ids": requested,
        "deleted_operation_ids": deleted,
        "failed": [asdict(item) for item in failures],
        "item_count": len(requested),
        "reclaimed_bytes": reclaimed_bytes,
    }
    return write_maintenance_audit(audit_id, payload)
