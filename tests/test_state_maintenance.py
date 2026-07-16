from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lpm.core.config import Config, StateConfig, default_state_dir
from lpm.services.operation_state import OperationRecord, save_operation
from lpm.services.state_maintenance import (
    delete_orphan_quarantine,
    export_orphan_backup,
    list_maintenance_audits,
    list_orphan_backups,
    list_orphan_quarantines,
    load_maintenance_audit,
    quarantine_orphan_backups,
)


def test_orphan_backup_can_be_exported_without_mutation(tmp_path: Path) -> None:
    orphan = default_state_dir() / "backups" / "lost-backup"
    orphan.mkdir(parents=True)
    (orphan / "data.txt").write_text("recover me", encoding="utf-8")
    output = tmp_path / "orphan.zip"

    listed = list_orphan_backups()
    result = export_orphan_backup(
        "lost-backup",
        output_path=output,
        config=_config(),
    )

    assert [item.name for item in listed] == ["lost-backup"]
    assert result.output_path == output
    assert orphan.is_dir()
    with zipfile.ZipFile(output) as archive:
        assert archive.read("lost-backup/data.txt") == b"recover me"


def test_orphan_quarantine_then_explicit_delete_writes_audits(
    tmp_path: Path,
) -> None:
    orphan = default_state_dir() / "backups" / "lost-backup"
    orphan.mkdir(parents=True)
    (orphan / "data.bin").write_bytes(b"x" * 64)

    quarantined = quarantine_orphan_backups(
        ["lost-backup"],
        config=_config(),
    )

    assert not orphan.exists()
    assert quarantined.quarantine.item_count == 1
    assert quarantined.quarantine.size_bytes >= 64
    assert list_orphan_quarantines()[0].quarantine_id == (
        quarantined.quarantine.quarantine_id
    )
    quarantine_audit = load_maintenance_audit(
        f"orphan-quarantine-{quarantined.quarantine.quarantine_id}"
    )
    assert quarantine_audit["status"] == "succeeded"

    deleted = delete_orphan_quarantine(
        quarantined.quarantine.quarantine_id,
        config=_config(),
    )

    assert deleted.deleted is True
    assert deleted.reclaimed_bytes >= 64
    assert not quarantined.quarantine.path.exists()
    audits = list_maintenance_audits(limit=10)
    assert {item.action for item in audits} == {
        "orphan-quarantine",
        "orphan-delete",
    }


def test_orphan_quarantine_revalidates_against_new_operation_record(
    tmp_path: Path,
) -> None:
    operation_id = "a" * 32
    orphan = default_state_dir() / "backups" / operation_id
    orphan.mkdir(parents=True)
    save_operation(
        OperationRecord(
            operation_id=operation_id,
            kind="resource-install",
            status="succeeded",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
    )

    with pytest.raises(ValueError, match="no longer orphaned"):
        quarantine_orphan_backups([operation_id], config=_config())
    assert orphan.is_dir()


def test_orphan_names_and_audit_ids_reject_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid orphan backup name"):
        export_orphan_backup("../escape", config=_config())
    with pytest.raises(ValueError, match="Invalid audit id"):
        load_maintenance_audit("../escape")


def test_orphan_export_rejects_destination_inside_backup(tmp_path: Path) -> None:
    orphan = default_state_dir() / "backups" / "lost-backup"
    orphan.mkdir(parents=True)
    (orphan / "data.txt").write_text("recover me", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot be inside"):
        export_orphan_backup(
            "lost-backup",
            output_path=orphan / "self.zip",
            config=_config(),
        )


def test_invalid_maintenance_audit_is_not_listed(tmp_path: Path) -> None:
    maintenance = default_state_dir() / "maintenance"
    maintenance.mkdir(parents=True)
    (maintenance / "broken.json").write_text("{", encoding="utf-8")
    (maintenance / "valid.json").write_text(
        json.dumps(
            {
                "action": "test",
                "status": "succeeded",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    audits = list_maintenance_audits(limit=10)

    assert [item.audit_id for item in audits] == ["valid"]


def _config() -> Config:
    return Config(
        state=StateConfig(
            lock_timeout_seconds=1,
            retention_days=90,
            keep_latest_operations=20,
            max_backup_mb=2048,
        )
    )
