"""Thin wrappers around the local `git` binary."""

from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse


class GitError(RuntimeError):
    """Raised when a git command exits non-zero."""


def _run(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            check=check,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on env
        raise GitError("`git` executable not found on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise GitError(f"git {' '.join(args)} failed: {stderr or exc}") from exc


def with_token(url: str, token: str | None) -> str:
    """Inject a token into an HTTPS URL for non-interactive auth.

    SSH URLs and tokenless calls are returned unchanged.
    """
    if not token or not url.startswith("https://"):
        return url
    parsed = urlparse(url)
    netloc = f"x-access-token:{token}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def is_repo(path: Path) -> bool:
    return (path / ".git").exists()


def init_repo(path: Path, default_branch: str = "main") -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(["init", "-b", default_branch], cwd=path)


def add_all(path: Path) -> None:
    _run(["add", "-A"], cwd=path)


def commit(path: Path, message: str, allow_empty: bool = False) -> None:
    args = ["commit", "-m", message]
    if allow_empty:
        args.append("--allow-empty")
    env_args = [
        "-c",
        "user.email=lpm@local",
        "-c",
        "user.name=LingyePluginMarketplace",
    ]
    _run([*env_args, *args], cwd=path)


def set_remote(path: Path, name: str, url: str) -> None:
    existing = _run(["remote"], cwd=path).stdout.split()
    if name in existing:
        _run(["remote", "set-url", name, url], cwd=path)
    else:
        _run(["remote", "add", name, url], cwd=path)


def push(path: Path, remote: str = "origin", branch: str = "main", set_upstream: bool = True) -> None:
    args = ["push"]
    if set_upstream:
        args.append("-u")
    args += [remote, branch]
    _run(args, cwd=path)


def clone(url: str, dest: Path, ref: str | None = None, depth: int | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone"]
    if depth:
        args += ["--depth", str(depth)]
    if ref:
        args += ["--branch", ref]
    args += [url, str(dest)]
    _run(args)


def pull(path: Path, ref: str | None = None) -> None:
    if ref:
        _run(["fetch", "origin", ref], cwd=path)
        _run(["checkout", ref], cwd=path)
        _run(["reset", "--hard", f"origin/{ref}"], cwd=path)
    else:
        _run(["pull", "--ff-only"], cwd=path)


def current_remote_url(path: Path, remote: str = "origin") -> str | None:
    res = _run(["remote", "get-url", remote], cwd=path, check=False)
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def head_commit(path: Path) -> str | None:
    res = _run(["rev-parse", "HEAD"], cwd=path, check=False)
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def remote_commit(path: Path, ref: str = "main", remote: str = "origin") -> str | None:
    res = _run(["ls-remote", remote, ref], cwd=path, check=False)
    if res.returncode != 0 or not res.stdout.strip():
        return None
    return res.stdout.split()[0]


def sparse_checkout(path: Path, subdir: str) -> None:
    """Configure sparse-checkout so only `subdir` materializes on disk."""
    _run(["sparse-checkout", "init", "--cone"], cwd=path)
    _run(["sparse-checkout", "set", subdir], cwd=path)
