"""Create a read-only, fail-closed plan for one CC Port live remote E2E run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUN_ID_RE = re.compile(r"^e2e-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
REPOSITORY_RE = re.compile(r"^cc-port-e2e-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$")


def _git(root: Path, *args: str, required: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if required and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise RuntimeError(message)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_bytes(root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip() or "git command failed"
        raise RuntimeError(message)
    return completed.stdout


def _untracked_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_name in _git_bytes(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if not raw_name:
            continue
        name = raw_name.decode("utf-8")
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("Git returned an unsafe untracked path.")
        candidate = root / relative
        if candidate.is_symlink():
            payload = b"symlink\0" + os.readlink(candidate).encode("utf-8")
            result[name] = hashlib.sha256(payload).hexdigest()
        elif candidate.is_file():
            result[name] = _sha256(candidate)
        else:
            raise RuntimeError("Untracked source entry is not a regular file or symbolic link.")
    return dict(sorted(result.items()))


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return path != parent


def build_plan(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    root = args.repo_root.resolve(strict=True)
    installer = args.installer.resolve(strict=True)
    evidence_root = args.evidence_root.resolve()
    if not installer.is_file():
        raise RuntimeError("Installer must be a regular file.")
    resolved_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if resolved_root != root:
        raise RuntimeError("--repo-root must be the cc-port Git repository root.")
    if not (root / "pyproject.toml").is_file() or not (root / "desktop" / "src-tauri").is_dir():
        raise RuntimeError("Repository does not look like a CC Port source checkout.")

    expected_evidence_root = (root / "build" / "live-e2e").resolve()
    if evidence_root != expected_evidence_root:
        raise RuntimeError("Evidence root must be <cc-port-root>/build/live-e2e.")
    if _inside(evidence_root, installer.parent) or _inside(installer, evidence_root):
        raise RuntimeError("Installer and evidence directories must not contain each other.")

    status_text = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    status = status_text.splitlines() if status_text else []
    staged_text = _git(root, "diff", "--cached", "--name-only")
    staged = staged_text.splitlines() if staged_text else []
    if status and not args.allow_dirty:
        raise RuntimeError("Source worktree is dirty; pass --allow-dirty only after reviewing it.")
    if staged and not args.allow_staged:
        raise RuntimeError("Source index is not empty; pass --allow-staged only after reviewing it.")

    now = datetime.now(timezone.utc)
    suffix = secrets.token_hex(4)
    run_id = f"e2e-{now:%Y%m%dT%H%M%SZ}-{suffix}"
    repository_name = f"cc-port-e2e-{now:%Y%m%d}-{now:%H%M%S}-{suffix}"
    if not RUN_ID_RE.fullmatch(run_id) or not REPOSITORY_RE.fullmatch(repository_name):
        raise RuntimeError("Generated run identity failed its safety pattern.")
    evidence_dir = evidence_root / run_id
    if evidence_dir.exists():
        raise RuntimeError("Generated evidence directory already exists.")
    evidence_dir.mkdir(parents=True)
    output = evidence_dir / "preflight.json"

    plan: dict[str, Any] = {
        "schema_version": 1,
        "created_at_utc": now.isoformat(),
        "run_id": run_id,
        "repository_name": repository_name,
        "repository_visibility": "private",
        "retain_repository": True,
        "evidence_dir": str(evidence_dir),
        "source": {
            "root": str(root),
            "branch": _git(root, "branch", "--show-current"),
            "head": _git(root, "rev-parse", "HEAD"),
            "origin_main": _git(root, "rev-parse", "origin/main", required=False),
            "status_porcelain": status,
            "staged_paths": staged,
            "unstaged_diff_sha256": hashlib.sha256(
                _git_bytes(root, "diff", "--binary", "--no-ext-diff")
            ).hexdigest(),
            "staged_diff_sha256": hashlib.sha256(
                _git_bytes(root, "diff", "--cached", "--binary", "--no-ext-diff")
            ).hexdigest(),
            "untracked_file_sha256": _untracked_hashes(root),
        },
        "installer": {
            "path": str(installer),
            "size_bytes": installer.stat().st_size,
            "sha256": _sha256(installer),
        },
        "scope": {
            "platform_id": "package-test",
            "resource_key": "skill:cc-port-e2e-skill",
            "resource_kind": "skill",
            "single_resource_actions": ["upload", "download"],
        },
        "safety": {
            "external_writes_authorized": False,
            "desktop_control_authorized": False,
            "real_profiles_allowed": False,
            "source_git_writes_allowed": False,
            "repository_delete_allowed": False,
            "credentials_persisted": False,
        },
    }
    output.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return plan, output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--allow-staged", action="store_true")
    args = parser.parse_args()
    try:
        plan, output = build_plan(args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "output": str(output), "plan": plan}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
