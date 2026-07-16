"""Query persisted local operations and restore successful change sets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.config import Config, default_state_dir, load_config
from .local_transaction import (
    ChangeTarget,
    LocalChangeTransaction,
    hash_path,
    remove_path_if_exists,
    restore_path_exact,
)
from .operation_state import (
    OperationRecord,
    OperationTarget,
    list_operations,
    load_operation,
    operation_path,
)
from .state_lock import acquire_target_locks

RESTORABLE_KINDS = {
    "environment-deploy",
    "resource-install",
    "resource-uninstall",
}


@dataclass
class OperationHistorySummary:
    operation_id: str
    kind: str
    status: str
    started_at: str
    finished_at: str
    message: str
    rolled_back: bool
    target_count: int
    changed_target_count: int
    restorable: bool


@dataclass
class OperationHistoryEntry(OperationHistorySummary):
    metadata: dict[str, object]
    targets: list[OperationTarget]


@dataclass
class OperationHistoryPage:
    operations: list[OperationHistorySummary]
    total: int
    offset: int
    limit: int
    has_more: bool


@dataclass
class OperationRestoreResult:
    source_operation_id: str
    operation: OperationRecord


class OperationRestoreError(RuntimeError):
    def __init__(self, message: str, operation: OperationRecord | None = None) -> None:
        super().__init__(message)
        self.operation = operation


def operation_history(*, limit: int = 100) -> list[OperationHistoryEntry]:
    return [_history_entry(record) for record in list_operations(limit=limit)]


def operation_history_page(
    *,
    offset: int = 0,
    limit: int = 20,
) -> OperationHistoryPage:
    if offset < 0:
        raise ValueError("Operation history offset must be non-negative.")
    if limit < 1 or limit > 100:
        raise ValueError("Operation history page size must be between 1 and 100.")
    records = list_operations()
    page_records = records[offset : offset + limit]
    return OperationHistoryPage(
        operations=[_history_summary(record) for record in page_records],
        total=len(records),
        offset=offset,
        limit=limit,
        has_more=offset + len(page_records) < len(records),
    )


def operation_detail(operation_id: str) -> OperationHistoryEntry:
    return _history_entry(load_operation(operation_id))


def restore_operation(
    operation_id: str,
    *,
    force: bool = False,
    config: Config | None = None,
) -> OperationRestoreResult:
    cfg = config or load_config()
    source_backup_root = default_state_dir() / "backups" / operation_id
    with acquire_target_locks(
        [operation_path(operation_id), source_backup_root],
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        return _restore_operation_locked(operation_id, force=force, config=cfg)


def _restore_operation_locked(
    operation_id: str,
    *,
    force: bool,
    config: Config,
) -> OperationRestoreResult:
    source = load_operation(operation_id)
    changed_targets = [
        target for target in source.targets if target.before_hash != target.after_hash
    ]
    if source.status != "succeeded" or source.kind not in RESTORABLE_KINDS:
        raise ValueError(f"Operation {operation_id} is not restorable.")
    if not changed_targets:
        raise ValueError(f"Operation {operation_id} did not change any targets.")

    _validate_source_backups(source, changed_targets)
    transaction = LocalChangeTransaction.begin(
        "operation-restore",
        [
            ChangeTarget(
                path=Path(target.path),
                change_action="restore",
                resource=target.resource,
                platform=target.platform,
            )
            for target in changed_targets
        ],
        metadata={
            "source_operation_id": source.operation_id,
            "force": force,
        },
        lock_timeout_seconds=config.state.lock_timeout_seconds,
    )
    drifted = [
        target.path
        for target in changed_targets
        if transaction.snapshots[
            Path(target.path).expanduser().absolute()
        ].before_hash
        != target.after_hash
    ]
    if drifted and not force:
        message = (
            "Restore blocked because targets changed after the operation: "
            + ", ".join(drifted)
        )
        transaction.abort(message)
        raise ValueError(message)
    paths = [Path(target.path) for target in changed_targets]
    transaction.mark_attempted(paths)
    try:
        for target in changed_targets:
            path = Path(target.path)
            remove_path_if_exists(path)
            if target.action == "restore":
                restore_path_exact(Path(target.backup_path), path)
            elif target.action != "remove":
                raise ValueError(f"Unknown restore action {target.action!r} for {path}.")
            current_hash = hash_path(path)
            if current_hash != target.before_hash:
                raise RuntimeError(f"Restore verification failed for {path}.")
        record = transaction.complete(
            message=f"Restored operation {source.operation_id}."
        )
        return OperationRestoreResult(
            source_operation_id=source.operation_id,
            operation=record,
        )
    except Exception as exc:
        errors = transaction.rollback(str(exc))
        suffix = "" if not errors else " | rollback errors: " + "; ".join(errors)
        raise OperationRestoreError(
            f"{exc}{suffix} (restore operation {transaction.record.operation_id})",
            transaction.record,
        ) from exc


def _history_entry(record: OperationRecord) -> OperationHistoryEntry:
    summary = _history_summary(record)
    return OperationHistoryEntry(
        **summary.__dict__,
        metadata=dict(record.metadata),
        targets=record.targets,
    )


def _history_summary(record: OperationRecord) -> OperationHistorySummary:
    changed = sum(
        1 for target in record.targets if target.before_hash != target.after_hash
    )
    raw_changed = record.metadata.get("changed_target_count", changed)
    try:
        changed_target_count = int(raw_changed)
    except (TypeError, ValueError):
        changed_target_count = changed
    changed_targets = [
        target for target in record.targets if target.before_hash != target.after_hash
    ]
    return OperationHistorySummary(
        operation_id=record.operation_id,
        kind=record.kind,
        status=record.status,
        started_at=record.started_at,
        finished_at=record.finished_at,
        message=record.message,
        rolled_back=record.rolled_back,
        target_count=len(record.targets),
        changed_target_count=changed_target_count,
        restorable=(
            record.status == "succeeded"
            and record.kind in RESTORABLE_KINDS
            and changed > 0
            and all(target.verified for target in changed_targets)
        ),
    )


def _validate_source_backups(
    source: OperationRecord,
    targets: list[OperationTarget],
) -> None:
    allowed_root = (
        default_state_dir() / "backups" / source.operation_id
    ).expanduser().absolute()
    for target in targets:
        if target.action == "remove":
            continue
        if target.action != "restore" or not target.backup_path:
            raise ValueError(f"Operation target has no valid restore action: {target.path}")
        backup = Path(target.backup_path).expanduser().absolute()
        try:
            within_root = os.path.commonpath([str(allowed_root), str(backup)]) == str(
                allowed_root
            )
        except ValueError:
            within_root = False
        if not within_root:
            raise ValueError(f"Backup path is outside the operation backup root: {backup}")
        if not backup.exists() and not backup.is_symlink():
            raise FileNotFoundError(f"Operation backup is missing: {backup}")
