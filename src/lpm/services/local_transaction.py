"""Shared snapshots and rollback for local filesystem changes."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.config import default_state_dir, load_config
from ..core.resource_files import is_resource_path_excluded
from .operation_state import (
    OperationRecord,
    OperationTarget,
    finish_operation,
    save_operation,
    start_operation,
)
from .state_lock import TargetLockSet, acquire_target_locks

BACKUP_DIR = "backups"


@dataclass(frozen=True)
class ChangeTarget:
    path: Path
    change_action: str
    resource: str = ""
    platform: str = ""


@dataclass
class TargetSnapshot:
    path: Path
    existed: bool
    backup_path: Path | None
    before_hash: str


class LocalChangeTransaction:
    """Persist a change set, snapshot targets, and provide rollback."""

    def __init__(
        self,
        record: OperationRecord,
        backup_root: Path,
        snapshots: dict[Path, TargetSnapshot],
        locks: TargetLockSet,
    ) -> None:
        self.record = record
        self.backup_root = backup_root
        self.snapshots = snapshots
        self.locks = locks
        self.attempted_paths: set[Path] = set()

    @classmethod
    def begin(
        cls,
        kind: str,
        targets: Iterable[ChangeTarget],
        *,
        metadata: dict[str, Any] | None = None,
        lock_timeout_seconds: float | None = None,
    ) -> LocalChangeTransaction:
        unique_targets: dict[str, ChangeTarget] = {}
        for target in targets:
            normalized = normalize_path(target.path)
            unique_targets.setdefault(
                os.path.normcase(str(normalized)),
                ChangeTarget(
                    path=normalized,
                    change_action=target.change_action,
                    resource=target.resource,
                    platform=target.platform,
                ),
            )

        ordered_targets = [
            unique_targets[key]
            for key in sorted(unique_targets)
        ]
        timeout = (
            lock_timeout_seconds
            if lock_timeout_seconds is not None
            else load_config().state.lock_timeout_seconds
        )
        locks = acquire_target_locks(
            (target.path for target in ordered_targets),
            timeout_seconds=timeout,
        )
        operation_metadata = dict(metadata or {})
        operation_metadata["locked_target_count"] = len(ordered_targets)
        try:
            record = start_operation(kind, metadata=operation_metadata)
        except Exception:
            locks.release()
            raise
        backup_root = default_state_dir() / BACKUP_DIR / record.operation_id
        snapshots: dict[Path, TargetSnapshot] = {}
        try:
            for index, target in enumerate(ordered_targets):
                path = target.path
                existed = path.exists() or path.is_symlink()
                backup_path = (
                    backup_path_exact(path, backup_root / f"{index:04d}")
                    if existed
                    else None
                )
                snapshot = TargetSnapshot(
                    path=path,
                    existed=existed,
                    backup_path=backup_path,
                    before_hash=hash_path(path),
                )
                snapshots[path] = snapshot
                record.targets.append(
                    OperationTarget(
                        path=str(path),
                        action="restore" if existed else "remove",
                        change_action=target.change_action,
                        backup_path=str(backup_path or ""),
                        resource=target.resource,
                        platform=target.platform,
                        before_hash=snapshot.before_hash,
                    )
                )
            save_operation(record)
        except Exception as exc:
            try:
                finish_operation(record, status="failed", message=str(exc))
            finally:
                locks.release()
            raise
        return cls(record, backup_root, snapshots, locks)

    def mark_attempted(self, paths: Iterable[Path]) -> None:
        self.attempted_paths.update(normalize_path(path) for path in paths)

    def complete(self, *, message: str = "") -> OperationRecord:
        changed = 0
        for target in self.record.targets:
            target.after_hash = hash_path(Path(target.path))
            target.verified = True
            if target.after_hash == target.before_hash:
                target.change_action = "unchanged"
            else:
                changed += 1
        self.record.metadata["changed_target_count"] = changed
        try:
            return finish_operation(self.record, status="succeeded", message=message)
        finally:
            self.locks.release()

    def abort(self, message: str, *, status: str = "blocked") -> OperationRecord:
        for target in self.record.targets:
            target.after_hash = target.before_hash
            target.verified = True
            target.change_action = "unchanged"
        self.record.metadata["changed_target_count"] = 0
        try:
            return finish_operation(self.record, status=status, message=message)
        finally:
            self.locks.release()

    def rollback(self, message: str) -> list[str]:
        errors = rollback_snapshots(self.snapshots, self.attempted_paths)
        for target in self.record.targets:
            current_hash = hash_path(Path(target.path))
            target.after_hash = current_hash
            target.verified = current_hash == target.before_hash
        self.record.metadata["changed_target_count"] = 0
        status = "rolled_back" if not errors else "rollback_failed"
        detail = message
        if errors:
            detail += " | rollback errors: " + "; ".join(errors)
        try:
            finish_operation(
                self.record,
                status=status,
                message=detail,
                rolled_back=not errors,
            )
        finally:
            self.locks.release()
        return errors


def rollback_snapshots(
    snapshots: dict[Path, TargetSnapshot],
    attempted_paths: Iterable[Path],
) -> list[str]:
    errors: list[str] = []
    attempted = {normalize_path(path) for path in attempted_paths}
    for path, snapshot in reversed(list(snapshots.items())):
        if path not in attempted:
            continue
        try:
            remove_path_if_exists(path)
            if snapshot.existed and snapshot.backup_path is not None:
                restore_path_exact(snapshot.backup_path, path)
        except Exception as exc:
            errors.append(f"{path}: {exc}")
    return errors


def backup_path_exact(target: Path, backup: Path) -> Path:
    backup.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        backup.symlink_to(os.readlink(target), target_is_directory=target.is_dir())
    elif target.is_dir():
        shutil.copytree(target, backup, symlinks=True)
    else:
        shutil.copy2(target, backup)
    return backup


def restore_path_exact(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        destination.symlink_to(
            os.readlink(source),
            target_is_directory=source.is_dir(),
        )
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination)


def remove_path_if_exists(path: Path) -> None:
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


def hash_path(path: Path, *, ignore_managed_marker: bool = False) -> str:
    if not path.exists() and not path.is_symlink():
        return ""
    digest = hashlib.sha256()
    if path.is_symlink():
        digest.update(b"link\0")
        digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        return digest.hexdigest()
    if path.is_file():
        digest.update(b"file\0")
        digest.update(path.read_bytes())
        return digest.hexdigest()

    digest.update(b"dir\0")
    for item in sorted(path.rglob("*"), key=lambda value: value.as_posix()):
        if ignore_managed_marker and item.name == ".lpm-managed.json":
            continue
        relative = item.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        if item.is_symlink():
            digest.update(b"link\0")
            digest.update(os.readlink(item).encode("utf-8", errors="surrogateescape"))
        elif item.is_file():
            digest.update(b"file\0")
            digest.update(item.read_bytes())
        else:
            digest.update(b"dir\0")
    return digest.hexdigest()


def resource_hash_path(path: Path) -> str:
    """Hash deployable resource content using the shared exclusion policy."""
    if not path.exists() or path.is_symlink():
        return ""
    if path.is_file():
        return hash_path(path)

    digest = hashlib.sha256()
    digest.update(b"resource-dir\0")
    for item in sorted(path.rglob("*"), key=lambda value: value.as_posix()):
        relative = item.relative_to(path)
        if (
            item.name == ".lpm-managed.json"
            or item.is_symlink()
            or is_resource_path_excluded(relative)
        ):
            continue
        digest.update(relative.as_posix().encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        if item.is_file():
            digest.update(b"file\0")
            digest.update(item.read_bytes())
        else:
            digest.update(b"dir\0")
    return digest.hexdigest()


def normalize_path(path: Path) -> Path:
    return path.expanduser().absolute()


def _make_writable_and_retry(function, path: str, _exc_info) -> None:
    target = Path(path)
    try:
        target.chmod(target.stat().st_mode | stat.S_IWRITE)
    except OSError:
        pass
    function(path)
