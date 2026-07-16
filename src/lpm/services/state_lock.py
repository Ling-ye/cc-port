"""Cross-process advisory locks for local change targets."""

from __future__ import annotations

import hashlib
import os
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ..core.config import default_state_dir


class TargetLockTimeout(RuntimeError):
    def __init__(self, target: Path, timeout_seconds: float) -> None:
        self.target = target
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Timed out after {timeout_seconds:g}s waiting for another LPM process "
            f"to release target: {target}"
        )


@dataclass
class _HeldLock:
    target: Path
    path: Path
    handle: BinaryIO


class TargetLockSet:
    def __init__(self, held: list[_HeldLock]) -> None:
        self._held = held
        self._released = False

    @property
    def targets(self) -> list[Path]:
        return [item.target for item in self._held]

    def release(self) -> None:
        if self._released:
            return
        for item in reversed(self._held):
            try:
                _unlock_file(item.handle)
            except OSError:
                # The process may already have lost the advisory lock during
                # interpreter or handle shutdown. Continue releasing the rest.
                pass
            finally:
                item.handle.close()
        self._released = True

    def __enter__(self) -> TargetLockSet:
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def acquire_target_locks(
    targets: Iterable[Path],
    *,
    timeout_seconds: float,
) -> TargetLockSet:
    if timeout_seconds <= 0:
        raise ValueError("Lock timeout must be greater than zero.")
    normalized_by_key: dict[str, Path] = {}
    for path in targets:
        target = _normalized_target(path)
        normalized_by_key.setdefault(os.path.normcase(str(target)), target)
    ordered = [
        normalized_by_key[key]
        for key in sorted(normalized_by_key)
    ]
    root = default_state_dir() / "locks"
    root.mkdir(parents=True, exist_ok=True)
    held: list[_HeldLock] = []
    deadline = time.monotonic() + timeout_seconds
    try:
        for target in ordered:
            lock_path = root / f"{_lock_key(target)}.lock"
            handle = lock_path.open("a+b")
            _ensure_lock_byte(handle)
            while True:
                try:
                    _lock_file(handle)
                    held.append(
                        _HeldLock(target=target, path=lock_path, handle=handle)
                    )
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        handle.close()
                        raise TargetLockTimeout(target, timeout_seconds) from None
                    time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    except Exception:
        TargetLockSet(held).release()
        raise
    return TargetLockSet(held)


def _normalized_target(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(str(path.expanduser()))))


def _lock_key(target: Path) -> str:
    value = os.path.normcase(str(target)).encode("utf-8", errors="surrogateescape")
    return hashlib.sha256(value).hexdigest()


def _ensure_lock_byte(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)


def _lock_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
