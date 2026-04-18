"""Thin wrappers around the local ``git`` binary.

Token injection uses ``GIT_ASKPASS`` so that credentials never appear in
``.git/config`` or process argument lists.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse, urlunparse


class GitError(RuntimeError):
    """Raised when a git command exits non-zero."""


def _token_env(token: str | None) -> dict[str, str]:
    """Build env-var overrides that let git authenticate via GIT_ASKPASS.

    Returns a dict that can be merged into ``subprocess.run(env=…)``.
    When *token* is falsy the dict is empty (no auth).
    """
    if not token:
        return {}
    if sys.platform == "win32":
        cmd = f"@echo {token}"
        suffix = ".bat"
    else:
        cmd = f"#!/bin/sh\necho '{token}'"
        suffix = ".sh"
    fd, path = tempfile.mkstemp(prefix="lpm-askpass-", suffix=suffix)
    with os.fdopen(fd, "w") as f:
        f.write(cmd)
    if sys.platform != "win32":
        os.chmod(path, 0o700)
    return {"GIT_ASKPASS": path, "_LPM_ASKPASS_TMP": path}


def _cleanup_askpass(extra_env: dict[str, str]) -> None:
    tmp = extra_env.get("_LPM_ASKPASS_TMP")
    if tmp:
        try:
            os.unlink(tmp)
        except OSError:
            pass


_NO_PROMPT_ENV: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
}
"""Baseline env overrides that prevent git from ever opening credential popups."""


def _run(
    args: list[str],
    cwd: Path | None = None,
    check: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, **_NO_PROMPT_ENV}
    if extra_env:
        env.update(extra_env)
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            check=check,
            capture_output=True,
            text=True,
            env=env,
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on env
        raise GitError("`git` executable not found on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise GitError(f"git {' '.join(args)} failed: {stderr or exc}") from exc


def with_token(url: str, token: str | None) -> str:
    """Inject a token into an HTTPS URL for non-interactive auth.

    SSH URLs and tokenless calls are returned unchanged.

    .. deprecated::
        Prefer :func:`_token_env` + ``GIT_ASKPASS`` for new code paths.
        This function is kept for backward compatibility with callers that
        need an authenticated URL (e.g. ``git clone <url>``).
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


def push(
    path: Path,
    remote: str = "origin",
    branch: str = "main",
    set_upstream: bool = True,
    token: str | None = None,
) -> None:
    args = ["push"]
    if set_upstream:
        args.append("-u")
    args += [remote, branch]
    env = _token_env(token)
    try:
        _run(args, cwd=path, extra_env=env)
    finally:
        _cleanup_askpass(env)


def clone(
    url: str,
    dest: Path,
    ref: str | None = None,
    depth: int | None = None,
    token: str | None = None,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone"]
    if depth:
        args += ["--depth", str(depth)]
    if ref:
        args += ["--branch", ref]
    args += [url, str(dest)]
    env = _token_env(token)
    try:
        _run(args, extra_env=env)
    finally:
        _cleanup_askpass(env)


def pull(path: Path, ref: str | None = None, token: str | None = None) -> None:
    env = _token_env(token)
    try:
        if ref:
            _run(["fetch", "origin", ref], cwd=path, extra_env=env)
            _run(["checkout", ref], cwd=path)
            _run(["reset", "--hard", f"origin/{ref}"], cwd=path)
        else:
            _run(["pull", "--ff-only"], cwd=path, extra_env=env)
    finally:
        _cleanup_askpass(env)


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


def probe_remote(url: str, ref: str = "main", *, timeout: int = 15) -> bool:
    """Return True if the remote repo (and optional ref) is reachable.

    Uses ``git ls-remote`` without cloning.  Returns False on network errors,
    authentication failures, or non-existent repositories.
    """
    try:
        env = {**os.environ, **_NO_PROMPT_ENV}
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", url, ref],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


_REPO_GONE_PATTERNS = (
    "repository not found",
    "does not exist",
    "could not read from remote",
    "not found",
    "the requested url returned error: 403",
)


def looks_like_repo_gone(error_message: str) -> bool:
    """Heuristic: does a git error look like the remote repo no longer exists?"""
    lower = error_message.lower()
    return any(pat in lower for pat in _REPO_GONE_PATTERNS)


def sparse_checkout(path: Path, subdir: str) -> None:
    """Configure sparse-checkout so only `subdir` materializes on disk."""
    _run(["sparse-checkout", "init", "--cone"], cwd=path)
    _run(["sparse-checkout", "set", subdir], cwd=path)
