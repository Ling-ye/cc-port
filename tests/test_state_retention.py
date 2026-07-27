from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cc_port.core.config import Config, StateConfig, default_state_dir
from cc_port.services import state_retention
from cc_port.services.operation_state import (
    OperationRecord,
    OperationTarget,
    operation_path,
    save_operation,
)
from cc_port.services.state_retention import build_state_retention_plan, prune_state


def test_retention_plan_protects_latest_and_running_and_reports_orphans(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    old_one = _operation("1" * 32, now - timedelta(days=100))
    old_two = _operation("2" * 32, now - timedelta(days=80))
    latest = _operation("3" * 32, now - timedelta(days=1))
    running = _operation("4" * 32, now - timedelta(days=120), status="running")
    for record in [old_one, old_two, latest, running]:
        save_operation(record)
        _backup(record.operation_id, 20)
    orphan = default_state_dir() / "backups" / ("f" * 32)
    orphan.mkdir(parents=True)
    (orphan / "data.bin").write_bytes(b"x" * 17)

    plan = build_state_retention_plan(
        config=_config(retention_days=30, keep_latest=1, max_backup_mb=0),
        now=now,
    )

    assert [item.operation_id for item in plan.candidates] == [
        old_one.operation_id,
        old_two.operation_id,
    ]
    assert plan.running_operation_count == 1
    assert plan.protected_operation_count == 1
    assert plan.orphan_backup_count == 1
    assert plan.orphan_backup_bytes >= 17


def test_capacity_plan_adds_oldest_non_protected_backups(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    records = [
        _operation("1" * 32, now - timedelta(days=3)),
        _operation("2" * 32, now - timedelta(days=2)),
        _operation("3" * 32, now - timedelta(days=1)),
    ]
    for record in records:
        save_operation(record)
        _backup(record.operation_id, 700 * 1024)

    plan = build_state_retention_plan(
        config=_config(retention_days=999, keep_latest=1, max_backup_mb=1),
        now=now,
    )

    assert [item.operation_id for item in plan.candidates] == [
        records[0].operation_id,
        records[1].operation_id,
    ]
    assert all("backup-capacity" in item.reasons for item in plan.candidates)
    assert plan.projected_backup_bytes <= 1024 * 1024


def test_prune_revalidates_candidates_and_writes_audit(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    record = _operation("a" * 32, now - timedelta(days=100))
    save_operation(record)
    backup = _backup(record.operation_id, 64)
    cfg = _config(retention_days=30, keep_latest=0, max_backup_mb=0)

    result = prune_state([record.operation_id], config=cfg)

    assert result.deleted_operation_ids == [record.operation_id]
    assert result.reclaimed_bytes > 0
    assert result.audit_path.is_file()
    assert not operation_path(record.operation_id).exists()
    assert not backup.exists()


def test_prune_rejects_operation_that_became_running(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    record = _operation("b" * 32, now - timedelta(days=100))
    save_operation(record)
    _backup(record.operation_id, 32)
    cfg = _config(retention_days=30, keep_latest=0, max_backup_mb=0)
    plan = build_state_retention_plan(config=cfg)
    assert [item.operation_id for item in plan.candidates] == [record.operation_id]

    record.status = "running"
    record.finished_at = ""
    save_operation(record)

    with pytest.raises(ValueError, match="no longer eligible"):
        prune_state([record.operation_id], config=cfg)
    assert operation_path(record.operation_id).is_file()


def test_prune_failure_restores_staged_record_and_backup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc)
    record = _operation("c" * 32, now - timedelta(days=100))
    save_operation(record)
    backup = _backup(record.operation_id, 32)
    cfg = _config(retention_days=30, keep_latest=0, max_backup_mb=0)
    monkeypatch.setattr(
        state_retention.shutil,
        "rmtree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("simulated delete failure")),
    )

    result = prune_state([record.operation_id], config=cfg)

    assert not result.deleted_operation_ids
    assert result.reclaimed_bytes == 0
    assert result.failed[0].operation_id == record.operation_id
    assert operation_path(record.operation_id).is_file()
    assert backup.is_dir()


def _operation(
    operation_id: str,
    timestamp: datetime,
    *,
    status: str = "succeeded",
) -> OperationRecord:
    text = timestamp.isoformat()
    return OperationRecord(
        operation_id=operation_id,
        kind="resource-install",
        status=status,
        started_at=text,
        finished_at=text if status != "running" else "",
        targets=[
            OperationTarget(
                path=f"/target/{operation_id}",
                action="restore",
                before_hash="before",
                after_hash="after",
                verified=True,
            )
        ],
    )


def _backup(operation_id: str, size: int) -> Path:
    path = default_state_dir() / "backups" / operation_id
    path.mkdir(parents=True)
    (path / "data.bin").write_bytes(b"x" * size)
    return path


def _config(
    *,
    retention_days: int,
    keep_latest: int,
    max_backup_mb: int,
) -> Config:
    return Config(
        state=StateConfig(
            lock_timeout_seconds=1,
            retention_days=retention_days,
            keep_latest_operations=keep_latest,
            max_backup_mb=max_backup_mb,
        )
    )
