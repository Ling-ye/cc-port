from __future__ import annotations

from pathlib import Path

import pytest

from lpm.services import operation_history as history_service
from lpm.services.local_transaction import ChangeTarget, LocalChangeTransaction
from lpm.services.operation_history import (
    operation_detail,
    operation_history,
    operation_history_page,
    restore_operation,
)
from lpm.services.operation_state import OperationRecord, OperationTarget, save_operation


def test_operation_history_restores_before_state_and_blocks_drift(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("before", encoding="utf-8")
    transaction = LocalChangeTransaction.begin(
        "resource-install",
        [ChangeTarget(target, "install", resource="demo")],
    )
    transaction.mark_attempted([target])
    target.write_text("after", encoding="utf-8")
    transaction.complete()

    item = operation_history(limit=1)[0]
    assert item.operation_id == transaction.record.operation_id
    assert item.restorable is True

    target.write_text("drift", encoding="utf-8")
    with pytest.raises(ValueError, match="targets changed"):
        restore_operation(item.operation_id)
    assert target.read_text(encoding="utf-8") == "drift"

    result = restore_operation(item.operation_id, force=True)
    assert result.operation.status == "succeeded"
    assert target.read_text(encoding="utf-8") == "before"


def test_failed_manual_restore_rolls_back_restore_attempt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("before-first", encoding="utf-8")
    second.write_text("before-second", encoding="utf-8")
    transaction = LocalChangeTransaction.begin(
        "resource-install",
        [
            ChangeTarget(first, "install"),
            ChangeTarget(second, "install"),
        ],
    )
    transaction.mark_attempted([first, second])
    first.write_text("after-first", encoding="utf-8")
    second.write_text("after-second", encoding="utf-8")
    transaction.complete()

    original_restore = history_service.restore_path_exact
    calls = 0

    def fail_second_restore(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated restore failure")
        original_restore(source, destination)

    monkeypatch.setattr(history_service, "restore_path_exact", fail_second_restore)

    with pytest.raises(history_service.OperationRestoreError, match="simulated restore failure"):
        restore_operation(transaction.record.operation_id)

    assert first.read_text(encoding="utf-8") == "after-first"
    assert second.read_text(encoding="utf-8") == "after-second"
    latest = operation_history(limit=1)[0]
    assert latest.kind == "operation-restore"
    assert latest.status == "rolled_back"


def test_operation_history_page_returns_summaries_and_detail_on_demand(
    tmp_path: Path,
) -> None:
    for index in range(3):
        operation_id = str(index + 1) * 32
        save_operation(
            OperationRecord(
                operation_id=operation_id,
                kind="resource-install",
                status="succeeded",
                started_at=f"2026-07-1{index}T00:00:00+00:00",
                targets=[
                    OperationTarget(
                        path=f"/target/{index}",
                        action="remove",
                        before_hash="",
                        after_hash="after",
                        verified=True,
                    )
                ],
            )
        )

    first = operation_history_page(offset=0, limit=2)
    second = operation_history_page(offset=2, limit=2)
    detail = operation_detail(first.operations[0].operation_id)

    assert first.total == 3
    assert first.has_more is True
    assert len(first.operations) == 2
    assert not hasattr(first.operations[0], "targets")
    assert second.has_more is False
    assert len(second.operations) == 1
    assert len(detail.targets) == 1
