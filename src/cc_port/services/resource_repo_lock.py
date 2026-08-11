"""Re-entrant process-local and cross-process locks for resource repositories."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..core.config import DEFAULT_LOCK_TIMEOUT_SECONDS, default_state_dir
from .state_lock import TargetLockSet, acquire_target_locks

_LOCKS_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.RLock] = {}
_THREAD_STATE = threading.local()


@contextmanager
def resource_repo_write_lock(
    repo_path: Path,
    *,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    allow_state_target: bool = False,
) -> Iterator[None]:
    """Serialize all mutating Git workflows for one resource repository."""
    target = repo_path.expanduser().resolve(strict=False)
    state_root = default_state_dir().expanduser().resolve(strict=False)
    if not allow_state_target and (
        target == state_root
        or target in state_root.parents
        or state_root in target.parents
    ):
        raise ValueError(
            "The machine-local state directory must not overlap the resource repository."
        )
    key = os.path.normcase(str(target))
    with _LOCKS_GUARD:
        local_lock = _LOCAL_LOCKS.setdefault(key, threading.RLock())
    acquired = local_lock.acquire(timeout=timeout_seconds)
    if not acquired:
        raise TimeoutError(f"Resource repository is busy: {target}")

    held: dict[str, tuple[int, TargetLockSet]] = getattr(_THREAD_STATE, "held", {})
    _THREAD_STATE.held = held
    try:
        current = held.get(key)
        if current is not None:
            held[key] = (current[0] + 1, current[1])
            yield
            return

        lock_set = acquire_target_locks([target], timeout_seconds=timeout_seconds)
        held[key] = (1, lock_set)
        try:
            yield
        finally:
            count, current_lock_set = held[key]
            if count != 1:
                raise RuntimeError("Resource repository lock nesting is inconsistent.")
            del held[key]
            current_lock_set.release()
    finally:
        current = held.get(key)
        if current is not None and current[0] > 1:
            held[key] = (current[0] - 1, current[1])
        local_lock.release()
