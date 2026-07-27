"""Persisted operation records for auditable, recoverable workflows."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..core.config import default_state_dir


@dataclass
class OperationTarget:
    path: str
    action: str
    change_action: str = ""
    backup_path: str = ""
    resource: str = ""
    platform: str = ""
    before_hash: str = ""
    after_hash: str = ""
    verified: bool = False


@dataclass
class OperationRecord:
    operation_id: str
    kind: str
    status: str
    started_at: str
    finished_at: str = ""
    message: str = ""
    rolled_back: bool = False
    targets: list[OperationTarget] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def start_operation(
    kind: str,
    *,
    targets: list[OperationTarget] | None = None,
    metadata: dict[str, Any] | None = None,
) -> OperationRecord:
    record = OperationRecord(
        operation_id=uuid4().hex,
        kind=kind,
        status="running",
        started_at=_now(),
        targets=targets or [],
        metadata=metadata or {},
    )
    save_operation(record)
    return record


def finish_operation(
    record: OperationRecord,
    *,
    status: str,
    message: str = "",
    rolled_back: bool = False,
) -> OperationRecord:
    record.status = status
    record.message = message
    record.rolled_back = rolled_back
    record.finished_at = _now()
    save_operation(record)
    return record


def save_operation(record: OperationRecord) -> Path:
    path = operation_path(record.operation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(path, asdict(record))
    return path


def load_operation(operation_id: str) -> OperationRecord:
    data = json.loads(operation_path(operation_id).read_text(encoding="utf-8"))
    data["targets"] = [OperationTarget(**target) for target in data.get("targets", [])]
    return OperationRecord(**data)


def list_operations(*, limit: int | None = None) -> list[OperationRecord]:
    root = default_state_dir() / "operations"
    if not root.is_dir():
        return []
    records: list[OperationRecord] = []
    for path in root.glob("*.json"):
        try:
            records.append(load_operation(path.stem))
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            continue
    records.sort(key=lambda item: item.started_at, reverse=True)
    if limit is None:
        return records
    return records[: max(0, limit)]


def operation_path(operation_id: str) -> Path:
    if not operation_id or any(char not in "0123456789abcdef" for char in operation_id):
        raise ValueError("Invalid operation id")
    return default_state_dir() / "operations" / f"{operation_id}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
