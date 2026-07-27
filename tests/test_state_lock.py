from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from cc_port.services.local_transaction import ChangeTarget, LocalChangeTransaction
from cc_port.services.resource_repo_lock import resource_repo_write_lock
from cc_port.services.state_lock import TargetLockTimeout, acquire_target_locks


def test_target_lock_blocks_another_process_and_releases(
    tmp_path: Path,
) -> None:
    target = tmp_path / "shared-target"
    script = """
import sys
from pathlib import Path
from cc_port.services.state_lock import TargetLockTimeout, acquire_target_locks

try:
    locks = acquire_target_locks([Path(sys.argv[1])], timeout_seconds=0.2)
except TargetLockTimeout:
    raise SystemExit(7)
else:
    locks.release()
"""
    env = dict(os.environ)
    with acquire_target_locks([target], timeout_seconds=1):
        blocked = subprocess.run(
            [sys.executable, "-c", script, str(target)],
            check=False,
            env=env,
        )
    released = subprocess.run(
        [sys.executable, "-c", script, str(target)],
        check=False,
        env=env,
    )

    assert blocked.returncode == 7
    assert released.returncode == 0


def test_target_locks_use_stable_order_and_allow_disjoint_targets(
    tmp_path: Path,
) -> None:
    first = tmp_path / "z-target"
    second = tmp_path / "a-target"

    with acquire_target_locks([first, second, first], timeout_seconds=1) as locks:
        assert locks.targets == sorted(
            {first.absolute(), second.absolute()},
            key=lambda item: os.path.normcase(str(item)),
        )
        with acquire_target_locks([tmp_path / "other"], timeout_seconds=1):
            pass


@pytest.mark.skipif(os.name != "nt", reason="Windows path comparison is case-insensitive.")
def test_target_locks_deduplicate_case_variants_on_windows(
    tmp_path: Path,
) -> None:
    target = tmp_path / "CaseSensitiveSpelling"
    case_variant = Path(str(target).swapcase())

    with acquire_target_locks(
        [target, case_variant],
        timeout_seconds=0.2,
    ) as locks:
        assert len(locks.targets) == 1
        assert os.path.normcase(str(locks.targets[0])) == os.path.normcase(
            str(target.absolute())
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows path comparison is case-insensitive.")
def test_transaction_deduplicates_case_variants_on_windows(
    tmp_path: Path,
) -> None:
    target = tmp_path / "CaseSensitiveSpelling"
    transaction = LocalChangeTransaction.begin(
        "resource-install",
        [
            ChangeTarget(target, "install"),
            ChangeTarget(Path(str(target).swapcase()), "install"),
        ],
        lock_timeout_seconds=0.2,
    )

    record = transaction.complete()

    assert len(record.targets) == 1
    assert record.metadata["locked_target_count"] == 1


def test_transaction_lock_timeout_does_not_create_operation_record(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    operations = Path(os.environ["CC_PORT_STATE_HOME"]) / "operations"

    with acquire_target_locks([target], timeout_seconds=1):
        with pytest.raises(TargetLockTimeout):
            LocalChangeTransaction.begin(
                "resource-install",
                [ChangeTarget(target, "install")],
                lock_timeout_seconds=0.1,
            )

    assert not operations.exists() or not list(operations.glob("*.json"))


def test_resource_repo_lock_is_reentrant_and_blocks_another_process(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "resource-repo"
    script = """
import sys
from pathlib import Path
from cc_port.services.resource_repo_lock import resource_repo_write_lock
from cc_port.services.state_lock import TargetLockTimeout

try:
    with resource_repo_write_lock(Path(sys.argv[1]), timeout_seconds=0.2):
        pass
except (TargetLockTimeout, TimeoutError):
    raise SystemExit(7)
"""
    env = dict(os.environ)
    with resource_repo_write_lock(repo, timeout_seconds=1):
        with resource_repo_write_lock(repo, timeout_seconds=1):
            blocked = subprocess.run(
                [sys.executable, "-c", script, str(repo)],
                check=False,
                env=env,
            )

    released = subprocess.run(
        [sys.executable, "-c", script, str(repo)],
        check=False,
        env=env,
    )

    assert blocked.returncode == 7
    assert released.returncode == 0
