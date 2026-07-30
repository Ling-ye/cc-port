"""Safe local-path inspection for resource discovery and upload."""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

WINDOWS_REPARSE_TAG_MOUNT_POINT = 0xA0000003
WINDOWS_REPARSE_TAG_SYMLINK = 0xA000000C
WINDOWS_REPARSE_TAG_LX_SYMLINK = 0xA000001D

_AGENT_DIRECTORY_NAMES = {
    ".claude",
    ".codex",
    ".cursor",
    ".gemini",
    ".opencode",
    ".windsurf",
}


@dataclass(frozen=True)
class LocalPathProbe:
    logical_path: Path
    content_path: Path | None
    path_kind: str
    health: str
    raw_target: str = ""
    reparse_tag: int = 0
    problem: str = ""

    @property
    def ready(self) -> bool:
        return self.health == "ready" and self.content_path is not None

    @property
    def is_link(self) -> bool:
        return self.path_kind in {"symlink", "junction"}

    @property
    def reparse_tag_hex(self) -> str:
        return f"0x{self.reparse_tag:08X}" if self.reparse_tag else ""


@dataclass(frozen=True)
class ResourceTreeIssue:
    path: Path
    relative_path: str
    code: str
    detail: str


def probe_local_path(path: Path | str) -> LocalPathProbe:
    """Inspect one path without allowing an inaccessible reparse point to escape."""
    logical = Path(path).expanduser().absolute()
    try:
        info = logical.lstat()
    except FileNotFoundError:
        return LocalPathProbe(
            logical_path=logical,
            content_path=None,
            path_kind="missing",
            health="missing",
            problem=f"The local path no longer exists: {logical}",
        )
    except OSError as exc:
        reparse_tag = _windows_reparse_tag(logical)
        if reparse_tag == WINDOWS_REPARSE_TAG_LX_SYMLINK:
            return _unsupported_wsl_probe(logical, reparse_tag)
        return LocalPathProbe(
            logical_path=logical,
            content_path=None,
            path_kind="reparse-point" if reparse_tag else "unreadable",
            health="unsupported-reparse" if reparse_tag else "unreadable",
            reparse_tag=reparse_tag,
            problem=f"The local path cannot be inspected safely: {exc}",
        )

    reparse_tag = int(getattr(info, "st_reparse_tag", 0) or 0)
    file_attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    is_reparse_point = bool(
        file_attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    )
    if is_reparse_point and not reparse_tag:
        reparse_tag = _windows_reparse_tag(logical)
    if reparse_tag == WINDOWS_REPARSE_TAG_LX_SYMLINK:
        return _unsupported_wsl_probe(logical, reparse_tag)

    is_symlink = stat.S_ISLNK(info.st_mode) or reparse_tag == WINDOWS_REPARSE_TAG_SYMLINK
    is_junction = reparse_tag == WINDOWS_REPARSE_TAG_MOUNT_POINT
    if is_reparse_point and not (is_symlink or is_junction):
        return LocalPathProbe(
            logical_path=logical,
            content_path=None,
            path_kind="reparse-point",
            health="unsupported-reparse",
            reparse_tag=reparse_tag,
            problem=(
                "The local path uses an unsupported Windows reparse point"
                f"{f' ({reparse_tag:#010x})' if reparse_tag else ''}: {logical}"
            ),
        )
    if not (is_symlink or is_junction):
        return LocalPathProbe(
            logical_path=logical,
            content_path=logical,
            path_kind="regular",
            health="ready",
            reparse_tag=reparse_tag,
        )

    path_kind = "junction" if is_junction else "symlink"
    try:
        raw_target = os.readlink(logical)
    except OSError as exc:
        return LocalPathProbe(
            logical_path=logical,
            content_path=None,
            path_kind=path_kind,
            health="unreadable",
            reparse_tag=reparse_tag,
            problem=f"The link target cannot be read safely: {exc}",
        )
    try:
        content_path = logical.resolve(strict=True)
    except FileNotFoundError:
        return LocalPathProbe(
            logical_path=logical,
            content_path=None,
            path_kind=path_kind,
            health="dangling",
            raw_target=str(raw_target),
            reparse_tag=reparse_tag,
            problem=f"The link target does not exist: {logical} -> {raw_target}",
        )
    except RuntimeError as exc:
        return LocalPathProbe(
            logical_path=logical,
            content_path=None,
            path_kind=path_kind,
            health="loop",
            raw_target=str(raw_target),
            reparse_tag=reparse_tag,
            problem=f"The link target forms a loop: {exc}",
        )
    except OSError as exc:
        return LocalPathProbe(
            logical_path=logical,
            content_path=None,
            path_kind=path_kind,
            health="unreadable",
            raw_target=str(raw_target),
            reparse_tag=reparse_tag,
            problem=f"The link target cannot be accessed safely: {exc}",
        )
    return LocalPathProbe(
        logical_path=logical,
        content_path=content_path,
        path_kind=path_kind,
        health="ready",
        raw_target=str(raw_target),
        reparse_tag=reparse_tag,
    )


def is_known_canonical_link_target(probe: LocalPathProbe) -> bool:
    """Return whether a ready root link targets a recognized canonical skill store."""
    if not probe.is_link or not probe.ready or probe.content_path is None:
        return False
    if probe.logical_path.parent.name.lower() != "skills":
        return False
    roots = [Path.home() / ".agents" / "skills"]
    for parent in probe.logical_path.parents:
        if parent.name.lower() in _AGENT_DIRECTORY_NAMES:
            roots.append(parent.parent / ".agents" / "skills")
    return any(_is_within(probe.content_path, root) for root in roots)


def resource_tree_issues(root: Path, *, limit: int = 16) -> list[ResourceTreeIssue]:
    """Find nested links, reparse points, and unreadable entries without following them."""
    source = root.expanduser().absolute()
    if not source.is_dir():
        return []
    issues: list[ResourceTreeIssue] = []

    def add_issue(path: Path, code: str, detail: str) -> None:
        if len(issues) >= limit:
            return
        try:
            relative = path.relative_to(source).as_posix()
        except ValueError:
            relative = path.name
        issues.append(
            ResourceTreeIssue(
                path=path,
                relative_path=relative,
                code=code,
                detail=detail,
            )
        )

    def on_error(exc: OSError) -> None:
        filename = Path(str(exc.filename or source))
        add_issue(filename, "unreadable", f"The resource tree cannot be read safely: {exc}")

    for directory, dirnames, filenames in os.walk(source, topdown=True, followlinks=False, onerror=on_error):
        current = Path(directory)
        retained: list[str] = []
        for name in sorted(dirnames):
            candidate = current / name
            probe = probe_local_path(candidate)
            if probe.path_kind != "regular" or not probe.ready:
                add_issue(
                    candidate,
                    probe.health,
                    probe.problem or f"Nested links are not uploadable: {candidate}",
                )
                continue
            retained.append(name)
        dirnames[:] = retained
        for name in sorted(filenames):
            candidate = current / name
            probe = probe_local_path(candidate)
            if probe.path_kind != "regular" or not probe.ready:
                add_issue(
                    candidate,
                    probe.health,
                    probe.problem or f"Nested links are not uploadable: {candidate}",
                )
        if len(issues) >= limit:
            break
    return issues


def _unsupported_wsl_probe(path: Path, reparse_tag: int) -> LocalPathProbe:
    return LocalPathProbe(
        logical_path=path,
        content_path=None,
        path_kind="wsl-symlink",
        health="unsupported-wsl",
        reparse_tag=reparse_tag,
        problem=(
            "This link was created by WSL and cannot be followed safely by the Windows "
            "desktop service. Recreate it from Windows or install a copied resource."
        ),
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        target = path.resolve(strict=False)
        canonical_root = root.expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    try:
        target.relative_to(canonical_root)
    except ValueError:
        return False
    return True


def _windows_reparse_tag(path: Path) -> int:
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        create_file = ctypes.windll.kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        device_io_control = ctypes.windll.kernel32.DeviceIoControl
        device_io_control.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        device_io_control.restype = wintypes.BOOL
        close_handle = ctypes.windll.kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = create_file(
            str(path),
            0,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,
            0x00200000 | 0x02000000,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            return 0
        try:
            buffer = ctypes.create_string_buffer(16 * 1024)
            returned = wintypes.DWORD()
            if not device_io_control(
                handle,
                0x000900A8,
                None,
                0,
                buffer,
                len(buffer),
                ctypes.byref(returned),
                None,
            ):
                return 0
            return int.from_bytes(buffer.raw[:4], byteorder="little", signed=False)
        finally:
            close_handle(handle)
    except (AttributeError, OSError, ValueError):
        return 0
