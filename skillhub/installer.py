"""Sync skills from registry.yaml to a local installation directory."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from . import git_ops
from .config import Config
from .models import Registry, SkillEntry
from .registry import load_registry


class SyncAction(str, Enum):
    INSTALLED = "installed"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class SkillStatus:
    name: str
    install_path: Path
    installed: bool
    local_commit: str | None
    remote_commit: str | None
    has_update: bool


@dataclass
class SyncResult:
    name: str
    install_path: Path
    action: SyncAction
    detail: str = ""


def _install_root(config: Config) -> Path:
    return config.install.target_path


def _install_path(config: Config, entry: SkillEntry) -> Path:
    root = _install_root(config)
    if entry.subdir:
        # If a subdir is configured, materialize only that subdir contents under
        # the target name. The clone happens to a sibling staging dir.
        return root / entry.install_target_name()
    return root / entry.install_target_name()


def _clone_path(config: Config, entry: SkillEntry) -> Path:
    """Where the actual git clone lives.

    For full-repo skills it is the install path itself.
    For subdir skills we keep the bare clone in a hidden staging area and
    expose only the subdir at the install path via copy.
    """
    if not entry.subdir:
        return _install_path(config, entry)
    return _install_root(config) / ".skillhub" / "clones" / entry.name


def sync_one(
    entry: SkillEntry,
    *,
    config: Config,
    token: str | None = None,
) -> SyncResult:
    install_path = _install_path(config, entry)
    clone_path = _clone_path(config, entry)
    install_path.parent.mkdir(parents=True, exist_ok=True)
    clone_path.parent.mkdir(parents=True, exist_ok=True)

    auth_token = token or config.github.token or None
    auth_url = git_ops.with_token(entry.repo, auth_token)

    try:
        if not git_ops.is_repo(clone_path):
            git_ops.clone(auth_url, clone_path, ref=entry.ref)
            if entry.subdir:
                git_ops.sparse_checkout(clone_path, entry.subdir)
            action = SyncAction.INSTALLED
        else:
            before = git_ops.head_commit(clone_path)
            git_ops.set_remote(clone_path, "origin", auth_url)
            git_ops.pull(clone_path, ref=entry.ref)
            git_ops.set_remote(clone_path, "origin", entry.repo)
            after = git_ops.head_commit(clone_path)
            action = SyncAction.UPDATED if before != after else SyncAction.UNCHANGED

        if entry.subdir:
            _materialize_subdir(clone_path, entry.subdir, install_path)

        # Always restore non-token URL so we never persist secrets to disk.
        if git_ops.is_repo(clone_path):
            git_ops.set_remote(clone_path, "origin", entry.repo)

        return SyncResult(name=entry.name, install_path=install_path, action=action)
    except git_ops.GitError as exc:
        return SyncResult(
            name=entry.name,
            install_path=install_path,
            action=SyncAction.FAILED,
            detail=str(exc),
        )


def sync_all(
    *,
    config: Config,
    registry: Registry | None = None,
    registry_path: Path | None = None,
    only: list[str] | None = None,
) -> list[SyncResult]:
    reg = registry or load_registry(registry_path)
    results: list[SyncResult] = []
    for entry in reg.skills:
        if only and entry.name not in only:
            continue
        results.append(sync_one(entry, config=config))
    return results


def status_all(
    *,
    config: Config,
    registry: Registry | None = None,
    registry_path: Path | None = None,
) -> list[SkillStatus]:
    reg = registry or load_registry(registry_path)
    out: list[SkillStatus] = []
    for entry in reg.skills:
        out.append(_skill_status(entry, config))
    return out


def _skill_status(entry: SkillEntry, config: Config) -> SkillStatus:
    install_path = _install_path(config, entry)
    clone_path = _clone_path(config, entry)
    installed = install_path.exists()
    local = git_ops.head_commit(clone_path) if git_ops.is_repo(clone_path) else None
    remote = git_ops.remote_commit(clone_path, ref=entry.ref) if git_ops.is_repo(clone_path) else None
    has_update = bool(local and remote and local != remote)
    return SkillStatus(
        name=entry.name,
        install_path=install_path,
        installed=installed,
        local_commit=local,
        remote_commit=remote,
        has_update=has_update,
    )


def uninstall_one(entry: SkillEntry, *, config: Config) -> bool:
    install_path = _install_path(config, entry)
    clone_path = _clone_path(config, entry)
    removed = False
    for p in {install_path, clone_path}:
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            removed = True
    return removed


def _materialize_subdir(clone_path: Path, subdir: str, install_path: Path) -> None:
    """Copy `clone_path/subdir` to `install_path` (replace if exists)."""
    src = clone_path / subdir
    if not src.is_dir():
        raise git_ops.GitError(f"subdir {subdir!r} not found inside cloned repo {clone_path}.")
    if install_path.exists():
        shutil.rmtree(install_path)
    install_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, install_path)
