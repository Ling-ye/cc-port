"""Inspect, preserve, quarantine, and audit machine-local state."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..core.config import Config, default_state_dir, load_config
from .operation_state import list_operations
from .state_lock import acquire_target_locks

_SAFE_AUDIT_ID = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class OrphanBackup:
    name: str
    path: Path
    kind: str
    size_bytes: int
    modified_at: str


@dataclass
class OrphanBackupExport:
    name: str
    output_path: Path
    size_bytes: int
    exported_at: str


@dataclass
class OrphanQuarantine:
    quarantine_id: str
    created_at: str
    names: list[str]
    item_count: int
    size_bytes: int
    path: Path


@dataclass
class OrphanQuarantineResult:
    quarantine: OrphanQuarantine
    audit_path: Path


@dataclass
class OrphanDeleteResult:
    delete_id: str
    quarantine_id: str
    deleted: bool
    reclaimed_bytes: int
    error: str
    audit_path: Path


@dataclass
class MaintenanceAudit:
    audit_id: str
    action: str
    status: str
    created_at: str
    item_count: int
    reclaimed_bytes: int
    path: Path


def list_orphan_backups() -> list[OrphanBackup]:
    backup_root = default_state_dir() / "backups"
    if not backup_root.is_dir():
        return []
    known_ids = {record.operation_id for record in list_operations()}
    entries: list[OrphanBackup] = []
    for path in backup_root.iterdir():
        if path.name in known_ids:
            continue
        try:
            modified_at = datetime.fromtimestamp(
                path.lstat().st_mtime,
                tz=timezone.utc,
            ).isoformat()
        except OSError:
            modified_at = ""
        entries.append(
            OrphanBackup(
                name=path.name,
                path=path,
                kind=_path_kind(path),
                size_bytes=path_size(path),
                modified_at=modified_at,
            )
        )
    entries.sort(key=lambda item: item.name.casefold())
    return entries


def export_orphan_backup(
    name: str,
    *,
    output_path: Path | None = None,
    config: Config | None = None,
) -> OrphanBackupExport:
    cfg = config or load_config()
    safe_name = _direct_name(name, "orphan backup name")
    state_root = default_state_dir()
    source = state_root / "backups" / safe_name
    operation_record = state_root / "operations" / f"{safe_name}.json"
    destination = output_path or _default_export_path(safe_name)
    destination = destination.expanduser().absolute()
    if destination.exists():
        raise FileExistsError(f"Export destination already exists: {destination}")
    if source.is_dir() and not source.is_symlink() and _is_within(
        source,
        destination,
    ):
        raise ValueError("Export destination cannot be inside the orphan backup.")

    with acquire_target_locks(
        [source, operation_record],
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        orphan = _require_orphan(safe_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        os.close(fd)
        temporary_path = Path(temporary)
        try:
            with zipfile.ZipFile(
                temporary_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                _write_zip_path(archive, orphan.path, orphan.name)
            temporary_path.replace(destination)
        finally:
            temporary_path.unlink(missing_ok=True)

    return OrphanBackupExport(
        name=orphan.name,
        output_path=destination,
        size_bytes=destination.stat().st_size,
        exported_at=_now(),
    )


def quarantine_orphan_backups(
    names: list[str],
    *,
    config: Config | None = None,
) -> OrphanQuarantineResult:
    cfg = config or load_config()
    requested = list(
        dict.fromkeys(_direct_name(name, "orphan backup name") for name in names)
    )
    if not requested:
        raise ValueError("Select at least one orphan backup to quarantine.")

    state_root = default_state_dir()
    maintenance_lock = state_root / "maintenance" / "orphans"
    lock_targets = [maintenance_lock]
    for name in requested:
        lock_targets.extend(
            [
                state_root / "backups" / name,
                state_root / "operations" / f"{name}.json",
            ]
        )

    with acquire_target_locks(
        lock_targets,
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        orphans = [_require_orphan(name) for name in requested]
        quarantine_id = uuid4().hex
        batch_root = state_root / "maintenance" / "orphans" / quarantine_id
        backup_root = batch_root / "backups"
        backup_root.mkdir(parents=True, exist_ok=False)
        moved: list[tuple[Path, Path]] = []
        try:
            for orphan in orphans:
                destination = backup_root / orphan.name
                orphan.path.replace(destination)
                moved.append((orphan.path, destination))
            manifest = {
                "quarantine_id": quarantine_id,
                "created_at": _now(),
                "names": requested,
                "item_count": len(requested),
                "size_bytes": sum(item.size_bytes for item in orphans),
            }
            _write_json_atomic(batch_root / "manifest.json", manifest)
        except Exception:
            for source, destination in reversed(moved):
                try:
                    destination.replace(source)
                except OSError:
                    pass
            shutil.rmtree(batch_root, ignore_errors=True)
            raise

        quarantine = _load_quarantine(batch_root)
        audit_id = f"orphan-quarantine-{quarantine_id}"
        audit_path = write_maintenance_audit(
            audit_id,
            {
                "audit_id": audit_id,
                "action": "orphan-quarantine",
                "status": "succeeded",
                "created_at": _now(),
                "quarantine_id": quarantine_id,
                "names": requested,
                "item_count": len(requested),
                "reclaimed_bytes": 0,
                "quarantined_bytes": quarantine.size_bytes,
            },
        )
    return OrphanQuarantineResult(quarantine=quarantine, audit_path=audit_path)


def list_orphan_quarantines() -> list[OrphanQuarantine]:
    root = default_state_dir() / "maintenance" / "orphans"
    if not root.is_dir():
        return []
    out: list[OrphanQuarantine] = []
    for path in root.iterdir():
        if not path.is_dir() or path.is_symlink():
            continue
        try:
            out.append(_load_quarantine(path))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    out.sort(key=lambda item: item.created_at, reverse=True)
    return out


def delete_orphan_quarantine(
    quarantine_id: str,
    *,
    config: Config | None = None,
) -> OrphanDeleteResult:
    cfg = config or load_config()
    safe_id = _identifier(quarantine_id, "quarantine id")
    state_root = default_state_dir()
    batch_root = state_root / "maintenance" / "orphans" / safe_id
    maintenance_lock = state_root / "maintenance" / "orphans"

    with acquire_target_locks(
        [maintenance_lock, batch_root],
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        quarantine = _load_quarantine(batch_root)
        delete_id = uuid4().hex
        audit_id = f"orphan-delete-{delete_id}"
        audit_payload: dict[str, Any] = {
            "audit_id": audit_id,
            "action": "orphan-delete",
            "status": "running",
            "created_at": _now(),
            "quarantine_id": safe_id,
            "names": quarantine.names,
            "item_count": quarantine.item_count,
            "reclaimed_bytes": 0,
            "error": "",
        }
        audit_path = write_maintenance_audit(audit_id, audit_payload)
        try:
            shutil.rmtree(batch_root)
        except Exception as exc:
            audit_payload["status"] = "failed"
            audit_payload["error"] = str(exc)
            write_maintenance_audit(audit_id, audit_payload)
            return OrphanDeleteResult(
                delete_id=delete_id,
                quarantine_id=safe_id,
                deleted=False,
                reclaimed_bytes=0,
                error=str(exc),
                audit_path=audit_path,
            )

        audit_payload["status"] = "succeeded"
        audit_payload["reclaimed_bytes"] = quarantine.size_bytes
        write_maintenance_audit(audit_id, audit_payload)
        return OrphanDeleteResult(
            delete_id=delete_id,
            quarantine_id=safe_id,
            deleted=True,
            reclaimed_bytes=quarantine.size_bytes,
            error="",
            audit_path=audit_path,
        )


def list_maintenance_audits(*, limit: int = 50) -> list[MaintenanceAudit]:
    if limit < 1 or limit > 500:
        raise ValueError("Audit limit must be between 1 and 500.")
    root = default_state_dir() / "maintenance"
    if not root.is_dir():
        return []
    audits: list[MaintenanceAudit] = []
    for path in root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            audits.append(_audit_summary(path, payload))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    audits.sort(key=lambda item: item.created_at, reverse=True)
    return audits[:limit]


def load_maintenance_audit(audit_id: str) -> dict[str, Any]:
    safe_id = _identifier(audit_id, "audit id")
    path = default_state_dir() / "maintenance" / f"{safe_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Maintenance audit is not a JSON object: {safe_id}")
    return payload


def write_maintenance_audit(
    audit_id: str,
    payload: dict[str, Any],
) -> Path:
    safe_id = _identifier(audit_id, "audit id")
    path = default_state_dir() / "maintenance" / f"{safe_id}.json"
    _write_json_atomic(path, payload)
    return path


def path_size(path: Path) -> int:
    try:
        if path.is_symlink():
            return path.lstat().st_size
        if path.is_file():
            return path.stat().st_size
        if not path.is_dir():
            return 0
    except OSError:
        return 0
    total = 0
    try:
        entries = list(os.scandir(path))
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_symlink():
                total += entry.stat(follow_symlinks=False).st_size
            elif entry.is_dir(follow_symlinks=False):
                total += path_size(Path(entry.path))
            else:
                total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return total


def _require_orphan(name: str) -> OrphanBackup:
    safe_name = _direct_name(name, "orphan backup name")
    state_root = default_state_dir()
    operation_ids = {record.operation_id for record in list_operations()}
    if safe_name in operation_ids:
        raise ValueError(f"Backup is no longer orphaned: {safe_name}")
    path = state_root / "backups" / safe_name
    if not path.exists() and not path.is_symlink():
        raise FileNotFoundError(f"Orphan backup does not exist: {safe_name}")
    try:
        modified_at = datetime.fromtimestamp(
            path.lstat().st_mtime,
            tz=timezone.utc,
        ).isoformat()
    except OSError:
        modified_at = ""
    return OrphanBackup(
        name=safe_name,
        path=path,
        kind=_path_kind(path),
        size_bytes=path_size(path),
        modified_at=modified_at,
    )


def _load_quarantine(path: Path) -> OrphanQuarantine:
    manifest_path = path / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    quarantine_id = _identifier(
        str(payload.get("quarantine_id") or ""),
        "quarantine id",
    )
    if quarantine_id != path.name:
        raise ValueError("Quarantine manifest id does not match its directory.")
    names = [
        _direct_name(str(name), "orphan backup name")
        for name in payload.get("names", [])
    ]
    backup_root = path / "backups"
    existing_names = [
        name
        for name in names
        if (backup_root / name).exists() or (backup_root / name).is_symlink()
    ]
    return OrphanQuarantine(
        quarantine_id=quarantine_id,
        created_at=str(payload.get("created_at") or ""),
        names=existing_names,
        item_count=len(existing_names),
        size_bytes=path_size(backup_root),
        path=path,
    )


def _audit_summary(path: Path, payload: dict[str, Any]) -> MaintenanceAudit:
    audit_id = _identifier(path.stem, "audit id")
    return MaintenanceAudit(
        audit_id=audit_id,
        action=str(payload.get("action") or "unknown"),
        status=str(payload.get("status") or "unknown"),
        created_at=str(payload.get("created_at") or ""),
        item_count=_safe_int(payload.get("item_count")),
        reclaimed_bytes=_safe_int(payload.get("reclaimed_bytes")),
        path=path,
    )


def _write_zip_path(
    archive: zipfile.ZipFile,
    source: Path,
    archive_name: str,
) -> None:
    normalized_name = archive_name.replace("\\", "/").rstrip("/")
    if source.is_symlink():
        info = zipfile.ZipInfo(normalized_name)
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, os.readlink(source))
        return
    if source.is_dir():
        directory_info = zipfile.ZipInfo(f"{normalized_name}/")
        directory_info.external_attr = (stat.S_IFDIR | 0o755) << 16
        archive.writestr(directory_info, b"")
        for child in sorted(source.iterdir(), key=lambda item: item.name.casefold()):
            _write_zip_path(
                archive,
                child,
                f"{normalized_name}/{child.name}",
            )
        return
    archive.write(source, normalized_name)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _default_export_path(name: str) -> Path:
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-") or "orphan"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        default_state_dir()
        / "exports"
        / "orphans"
        / f"{safe_stem}-{timestamp}-{uuid4().hex[:8]}.zip"
    )


def _direct_name(value: str, label: str) -> str:
    text = value.strip()
    if (
        not text
        or text in {".", ".."}
        or Path(text).name != text
        or "/" in text
        or "\\" in text
        or "\0" in text
    ):
        raise ValueError(f"Invalid {label}: {value!r}")
    return text


def _identifier(value: str, label: str) -> str:
    text = value.strip()
    if not text or not _SAFE_AUDIT_ID.fullmatch(text):
        raise ValueError(f"Invalid {label}: {value!r}")
    return text


def _path_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "other"


def _is_within(parent: Path, child: Path) -> bool:
    try:
        normalized_parent = os.path.normcase(str(parent.resolve()))
        normalized_child = os.path.normcase(str(child.resolve(strict=False)))
        return os.path.commonpath(
            [normalized_parent, normalized_child]
        ) == normalized_parent
    except (OSError, ValueError):
        return False


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
