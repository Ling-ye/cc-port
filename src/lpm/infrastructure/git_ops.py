"""Thin wrappers around the local ``git`` binary.

HTTPS credentials use process-local Git configuration so secrets never appear
in ``.git/config``, remote URLs, command arguments, or temporary scripts.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse


class GitError(RuntimeError):
    """Raised when a git command exits non-zero."""


@dataclass(frozen=True)
class GitDivergence:
    local_commit: str | None
    remote_commit: str | None
    merge_base: str | None
    ahead: int
    behind: int

    @property
    def state(self) -> str:
        if self.local_commit is None:
            return "unborn"
        if self.remote_commit is None:
            return "no-remote"
        if self.ahead and self.behind:
            return "diverged"
        if self.ahead:
            return "ahead"
        if self.behind:
            return "behind"
        return "clean"


@dataclass(frozen=True)
class GitStatusEntry:
    status: str
    path: str
    original_path: str = ""

    @property
    def action(self) -> str:
        if "D" in self.status:
            return "deleted"
        if self.status == "??" or "A" in self.status:
            return "added"
        return "modified"


@dataclass(frozen=True)
class GitCommitFile:
    commit: str
    path: str
    text: str | None
    mode: str = ""


@dataclass(frozen=True)
class GitRuntime:
    path: Path | None
    source: str
    requested: str = ""


@dataclass(frozen=True)
class RemoteBindingProbe:
    default_branch: str
    branches: list[str]
    remote_empty: bool


_CONFIGURED_GIT_EXECUTABLE = ""


def configure_git_executable(value: str | None) -> None:
    """Set a process-local preferred Git executable from LPM configuration."""
    global _CONFIGURED_GIT_EXECUTABLE
    _CONFIGURED_GIT_EXECUTABLE = str(value or "").strip()


def discover_git_executable(configured: str | None = None) -> GitRuntime:
    requested = str(
        configured
        if configured is not None
        else os.environ.get("LPM_GIT_EXECUTABLE", "").strip()
        or _CONFIGURED_GIT_EXECUTABLE
    ).strip()
    if requested:
        resolved = _resolve_executable(requested)
        if resolved is not None:
            return GitRuntime(path=resolved, source="configured", requested=requested)
        return GitRuntime(path=None, source="configured-missing", requested=requested)

    on_path = shutil.which("git")
    if on_path:
        return GitRuntime(path=Path(on_path).resolve(), source="PATH")

    for candidate in _git_install_candidates():
        if candidate.is_file():
            return GitRuntime(path=candidate.resolve(), source="auto-detected")
    return GitRuntime(path=None, source="missing")


def _git_executable() -> str:
    runtime = discover_git_executable()
    if runtime.path is None:
        detail = (
            f"Configured Git executable does not exist: {runtime.requested}"
            if runtime.requested
            else "Git executable was not found in PATH or standard install locations."
        )
        raise GitError(detail)
    return str(runtime.path)


def _resolve_executable(value: str) -> Path | None:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    found = shutil.which(value)
    return Path(found).resolve() if found else None


def _git_install_candidates() -> list[Path]:
    candidates: list[Path] = []
    executable_dir = Path(sys.executable).resolve().parent
    if sys.platform == "win32":
        for base in (
            os.environ.get("ProgramFiles", ""),
            os.environ.get("ProgramFiles(x86)", ""),
            os.environ.get("LOCALAPPDATA", ""),
        ):
            if base:
                candidates.append(Path(base) / "Git" / "cmd" / "git.exe")
        candidates.extend(
            [
                executable_dir / "git" / "cmd" / "git.exe",
                executable_dir / "MinGit" / "cmd" / "git.exe",
            ]
        )
        for drive_code in range(ord("C"), ord("Z") + 1):
            candidates.append(
                Path(f"{chr(drive_code)}:/Git/cmd/git.exe")
            )
    else:
        candidates.extend(
            [
                Path("/usr/bin/git"),
                Path("/usr/local/bin/git"),
                Path("/opt/homebrew/bin/git"),
                executable_dir / "git",
            ]
        )
    return candidates


def _token_env(token: str | None) -> dict[str, str]:
    """Build process-local Git config for HTTPS authentication.

    The token is carried in the child environment, never in command arguments,
    repository config, remote URLs, or temporary scripts.
    """
    if not token:
        return {}
    try:
        index = max(0, int(os.environ.get("GIT_CONFIG_COUNT", "0") or "0"))
    except ValueError:
        index = 0
    while f"GIT_CONFIG_KEY_{index}" in os.environ:
        index += 1
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
    return {
        "GIT_CONFIG_COUNT": str(index + 1),
        f"GIT_CONFIG_KEY_{index}": "http.extraHeader",
        f"GIT_CONFIG_VALUE_{index}": f"Authorization: Basic {basic}",
    }


def _cleanup_askpass(extra_env: dict[str, str]) -> None:
    """Backward-compatible no-op; credential transport creates no files."""
    return None


_NO_PROMPT_ENV: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
}
"""Baseline env overrides that prevent git from ever opening credential popups."""

_BIND_HTTPS_ENV: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "auto",
    "GCM_PROVIDER": "github",
}
"""Explicit binding may open GCM's GUI, but never prompt in the hidden terminal."""


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
            [_git_executable(), *args],
            cwd=str(cwd) if cwd else None,
            check=check,
            capture_output=True,
            text=True,
            errors="replace",
            env=env,
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on env
        raise GitError("`git` executable not found on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = redact_git_text((exc.stderr or "").strip())
        safe_args = " ".join(redact_git_text(item) for item in args)
        raise GitError(f"git {safe_args} failed: {stderr or exc.__class__.__name__}") from exc


def strip_url_credentials(url: str) -> str:
    """Remove URL userinfo before storing, logging, or passing a remote URL."""
    parsed = urlparse(url)
    if not parsed.scheme or parsed.hostname is None or parsed.username is None:
        return url
    netloc = parsed.hostname
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


_URL_USERINFO_RE = re.compile(r"(?P<scheme>https?://)[^/@\s]+@(?P<host>[^/\s]+)", re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(
    r"Authorization:\s*(?:Basic|Bearer)\s+[A-Za-z0-9+/=_-]+",
    re.IGNORECASE,
)


def redact_git_text(value: str) -> str:
    """Redact URL userinfo that Git may echo in diagnostics."""
    without_userinfo = _URL_USERINFO_RE.sub(r"\g<scheme>***@\g<host>", value)
    return _AUTH_HEADER_RE.sub("Authorization: ***", without_userinfo)


def _validate_argument(value: str, label: str) -> str:
    if not value or value.startswith("-") or any(char in value for char in "\r\n\0"):
        raise ValueError(f"Invalid Git {label}: {value!r}")
    return value


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
    env_args = _commit_identity_args(path)
    _run([*env_args, *args], cwd=path)


def configured_commit_identity(path: Path) -> tuple[str, str] | None:
    name = _run(["config", "--get", "user.name"], cwd=path, check=False)
    email = _run(["config", "--get", "user.email"], cwd=path, check=False)
    user_name = name.stdout.strip() if name.returncode == 0 else ""
    user_email = email.stdout.strip() if email.returncode == 0 else ""
    return (user_name, user_email) if user_name and user_email else None


def _commit_identity_args(path: Path) -> list[str]:
    if configured_commit_identity(path) is not None:
        return []
    return [
        "-c",
        "user.email=lpm@local",
        "-c",
        "user.name=LingyePluginMarketplace",
    ]


def set_remote(path: Path, name: str, url: str) -> None:
    name = _validate_argument(name, "remote name")
    url = strip_url_credentials(_validate_argument(url, "remote URL"))
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
    remote = _validate_argument(remote, "remote name")
    branch = _validate_argument(branch, "branch")
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
    url = strip_url_credentials(_validate_argument(url, "remote URL"))
    args = ["clone"]
    if depth:
        args += ["--depth", str(depth)]
    if ref:
        ref = _validate_argument(ref, "ref")
        args += ["--branch", ref]
    args += [url, str(dest)]
    env = _token_env(token)
    try:
        _run(args, extra_env=env)
    finally:
        _cleanup_askpass(env)


def pull(path: Path, ref: str | None = None, token: str | None = None) -> None:
    """Fast-forward a local branch from its remote.

    This intentionally refuses diverged history. Higher-level resource sync
    handles three-way merge planning; a normal pull must never hard-reset the
    user's local commits.
    """
    env = _token_env(token)
    try:
        if ref:
            ref = _validate_argument(ref, "ref")
            _run(["fetch", "origin", ref], cwd=path, extra_env=env)
            checkout_local_branch(path, ref)
            _run(["merge", "--ff-only", f"origin/{ref}"], cwd=path)
        else:
            _run(["pull", "--ff-only"], cwd=path, extra_env=env)
    finally:
        _cleanup_askpass(env)


def fetch(path: Path, remote: str = "origin", ref: str | None = None, token: str | None = None) -> None:
    remote = _validate_argument(remote, "remote name")
    args = ["fetch", remote]
    if ref:
        ref = _validate_argument(ref, "ref")
        args.append(ref)
    env = _token_env(token)
    try:
        _run(args, cwd=path, extra_env=env)
    finally:
        _cleanup_askpass(env)


def merge_ff_only(path: Path, target: str) -> None:
    _run(["merge", "--ff-only", target], cwd=path)


def checkout_remote_branch(path: Path, branch: str, remote: str = "origin") -> None:
    _run(["checkout", "-B", branch, f"{remote}/{branch}"], cwd=path)


def current_remote_url(path: Path, remote: str = "origin") -> str | None:
    res = _run(["remote", "get-url", remote], cwd=path, check=False)
    if res.returncode != 0:
        return None
    value = res.stdout.strip()
    return strip_url_credentials(value) if value else None


def common_dir(path: Path) -> Path | None:
    """Return the canonical Git common directory for repository identity checks."""
    res = _run(["rev-parse", "--git-common-dir"], cwd=path, check=False)
    if res.returncode != 0 or not res.stdout.strip():
        return None
    value = Path(res.stdout.strip())
    if not value.is_absolute():
        value = path / value
    return value.resolve()


def current_branch(path: Path) -> str | None:
    res = _run(["branch", "--show-current"], cwd=path, check=False)
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def checkout_branch(path: Path, branch: str) -> None:
    _run(["checkout", "-B", branch], cwd=path)


def checkout_branch_at(path: Path, branch: str, commit_ref: str) -> None:
    _run(["checkout", "-B", branch, commit_ref], cwd=path)


def checkout_local_branch(path: Path, branch: str) -> None:
    exists = _run(["rev-parse", "--verify", branch], cwd=path, check=False)
    if exists.returncode == 0:
        _run(["checkout", branch], cwd=path)
    else:
        checkout_branch(path, branch)


def status_short(path: Path) -> str:
    res = _run(["status", "--short"], cwd=path, check=False)
    return res.stdout.strip() if res.returncode == 0 else ""


def status_entries(path: Path) -> list[GitStatusEntry]:
    result = _run(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=path,
    )
    records = result.stdout.split("\0")
    entries: list[GitStatusEntry] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            raise GitError("Unable to parse Git working-tree status.")
        status = record[:2]
        item_path = record[3:]
        original_path = ""
        if "R" in status or "C" in status:
            if index >= len(records) or not records[index]:
                raise GitError("Unable to parse Git rename status.")
            original_path = records[index]
            index += 1
        entries.append(
            GitStatusEntry(
                status=status,
                path=item_path.replace("\\", "/"),
                original_path=original_path.replace("\\", "/"),
            )
        )
    return entries


def head_commit(path: Path) -> str | None:
    res = _run(["rev-parse", "HEAD"], cwd=path, check=False)
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def rev_parse(path: Path, ref: str) -> str | None:
    res = _run(["rev-parse", "--verify", ref], cwd=path, check=False)
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def merge_base(path: Path, left: str, right: str) -> str | None:
    res = _run(["merge-base", left, right], cwd=path, check=False)
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def rev_list_count(path: Path, revision_range: str) -> int:
    res = _run(["rev-list", "--count", revision_range], cwd=path)
    return int(res.stdout.strip() or "0")


def outgoing_commit_files(
    path: Path,
    *,
    base_commit: str | None,
) -> list[GitCommitFile]:
    """Return file snapshots introduced by commits not present at *base_commit*."""
    revision = f"{base_commit}..HEAD" if base_commit else "HEAD"
    commits_result = _run(["rev-list", "--reverse", revision], cwd=path, check=False)
    if commits_result.returncode != 0:
        return []
    files: list[GitCommitFile] = []
    for commit in [line.strip() for line in commits_result.stdout.splitlines() if line.strip()]:
        changed = _run(
            [
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-m",
                "-z",
                commit,
            ],
            cwd=path,
        )
        for file_path in sorted(set(item for item in changed.stdout.split("\0") if item)):
            tree = _run(
                ["ls-tree", commit, "--", file_path],
                cwd=path,
                check=False,
            )
            mode = tree.stdout.split(maxsplit=1)[0] if tree.returncode == 0 else ""
            blob = _run(["show", f"{commit}:{file_path}"], cwd=path, check=False)
            text = blob.stdout if blob.returncode == 0 and "\x00" not in blob.stdout else None
            files.append(
                GitCommitFile(
                    commit=commit,
                    path=file_path.replace("\\", "/"),
                    text=text,
                    mode=mode,
                )
            )
    return files


def divergence(path: Path, *, branch: str, remote: str = "origin") -> GitDivergence:
    local = head_commit(path)
    remote_ref = f"{remote}/{branch}"
    incoming = rev_parse(path, remote_ref)
    if local is None or incoming is None:
        return GitDivergence(
            local_commit=local,
            remote_commit=incoming,
            merge_base=None,
            ahead=1 if local and not incoming else 0,
            behind=1 if incoming and not local else 0,
        )
    base = merge_base(path, local, incoming)
    ahead = rev_list_count(path, f"{incoming}..{local}")
    behind = rev_list_count(path, f"{local}..{incoming}")
    return GitDivergence(
        local_commit=local,
        remote_commit=incoming,
        merge_base=base,
        ahead=ahead,
        behind=behind,
    )


def worktree_add(path: Path, destination: Path, commit_ref: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run(["worktree", "add", "--detach", str(destination), commit_ref], cwd=path)


def worktree_remove(path: Path, destination: Path, *, force: bool = False) -> None:
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(destination))
    _run(args, cwd=path, check=False)


def worktree_prune(path: Path) -> None:
    _run(["worktree", "prune"], cwd=path, check=False)


def merge_no_ff(path: Path, ref: str) -> tuple[bool, str]:
    env_args = _commit_identity_args(path)
    result = _run(
        [*env_args, "merge", "--no-ff", "--no-edit", ref],
        cwd=path,
        check=False,
    )
    detail = (result.stderr or result.stdout or "").strip()
    return result.returncode == 0, detail


def unresolved_paths(path: Path) -> list[str]:
    result = _run(["diff", "--name-only", "--diff-filter=U"], cwd=path, check=False)
    return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())


def checkout_conflict_version(path: Path, file_path: str, *, choice: str) -> None:
    if choice not in {"local", "incoming"}:
        raise ValueError("Git conflict choice must be 'local' or 'incoming'.")
    side = "--ours" if choice == "local" else "--theirs"
    _run(["checkout", side, "--", file_path], cwd=path)
    _run(["add", "--", file_path], cwd=path)


def show_index_stage(path: Path, file_path: str, stage: int) -> str | None:
    result = _run(["show", f":{stage}:{file_path}"], cwd=path, check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def add_paths(path: Path, paths: list[str]) -> None:
    if paths:
        _run(["add", "--", *paths], cwd=path)


def commit_pending_merge(path: Path) -> str:
    env_args = _commit_identity_args(path)
    _run([*env_args, "commit", "--no-edit"], cwd=path)
    commit_id = head_commit(path)
    if not commit_id:
        raise GitError("Merge commit was not created.")
    return commit_id


def remote_commit(
    path: Path,
    ref: str = "main",
    remote: str = "origin",
    token: str | None = None,
) -> str | None:
    ref = _validate_argument(ref, "ref")
    remote = _validate_argument(remote, "remote name")
    env = _token_env(token)
    try:
        res = _run(["ls-remote", remote, ref], cwd=path, check=False, extra_env=env)
        if res.returncode != 0 or not res.stdout.strip():
            return None
        return res.stdout.split()[0]
    finally:
        _cleanup_askpass(env)


def remote_url_commit(
    url: str,
    ref: str = "main",
    *,
    token: str | None = None,
) -> str | None:
    """Resolve one ref on a remote URL without creating or modifying a repository."""
    url = strip_url_credentials(_validate_argument(url, "remote URL"))
    ref = _validate_argument(ref, "ref")
    env = _token_env(token)
    try:
        res = _run(["ls-remote", url, ref], check=False, extra_env=env)
        if res.returncode != 0 or not res.stdout.strip():
            return None
        return res.stdout.split()[0]
    finally:
        _cleanup_askpass(env)


def remote_branches(
    url: str,
    *,
    token: str | None = None,
    timeout: int = 15,
) -> tuple[str, list[str]]:
    """Return the default branch and branch names advertised by a remote.

    Native HTTPS credentials are tried first without opening prompts. GitHub
    HTTPS URLs fall back to SSH for compatibility with older SSH-only setups.
    """
    env = {**os.environ, **_NO_PROMPT_ENV}
    query_url = strip_url_credentials(_validate_argument(url, "remote URL"))
    token_env = _token_env(token)
    env.update(token_env)
    try:
        result = _run_remote_branch_query(query_url, timeout=timeout, env=env)
        if result.returncode != 0 and token:
            native_env = {**os.environ, **_NO_PROMPT_ENV}
            result = _run_remote_branch_query(query_url, timeout=timeout, env=native_env)
        if result.returncode != 0:
            ssh_url = _github_ssh_url(url)
            if ssh_url:
                ssh_env = {
                    **os.environ,
                    **_NO_PROMPT_ENV,
                    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=10",
                }
                result = _run_remote_branch_query(ssh_url, timeout=timeout, env=ssh_env)
    except FileNotFoundError as exc:
        raise GitError("`git` executable not found on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"Timed out while reading branches from {url}.") from exc
    finally:
        _cleanup_askpass(token_env)

    if result.returncode != 0:
        detail = redact_git_text((result.stderr or "").strip())
        raise GitError(f"Unable to read remote branches: {detail or 'git ls-remote failed.'}")

    default_branch = ""
    branches: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "ref:" and parts[2] == "HEAD":
            default_branch = parts[1].removeprefix("refs/heads/")
        elif len(parts) >= 2 and parts[1].startswith("refs/heads/"):
            branches.add(parts[1].removeprefix("refs/heads/"))
    if default_branch:
        branches.add(default_branch)
    return default_branch, sorted(branches)


def _run_remote_branch_query(
    url: str,
    *,
    timeout: int,
    env: dict[str, str],
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            _git_executable(),
            "ls-remote",
            "--symref",
            url,
            "HEAD",
            "refs/heads/*",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def probe_remote_binding(
    url: str,
    *,
    transport: str,
    read_timeout: int = 180,
    write_timeout: int = 60,
) -> RemoteBindingProbe:
    """Verify read and write authentication without transferring repository data.

    The write check uses ``git push --dry-run`` from a temporary repository to
    a unique branch name. Git performs authentication and policy checks but
    sends no ref update.
    """
    safe_url = strip_url_credentials(_validate_argument(url, "remote URL"))
    if transport not in {"https", "ssh"}:
        raise ValueError(f"Unsupported Git binding transport: {transport}")
    env = _binding_env(transport)
    try:
        read_result = _run_remote_branch_query(
            safe_url,
            timeout=read_timeout if transport == "https" else min(read_timeout, 30),
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError("Timed out while authenticating with the GitHub repository.") from exc
    except FileNotFoundError as exc:
        raise GitError("`git` executable not found on PATH.") from exc
    if read_result.returncode != 0:
        raise GitError(_binding_error(read_result.stderr, transport, action="read"))

    default_branch, branches = _parse_remote_branches(read_result.stdout)
    branch = default_branch or "main"
    probe_ref = f"refs/heads/lpm-bind-probe-{uuid.uuid4().hex}"
    try:
        with tempfile.TemporaryDirectory(prefix="lpm-bind-probe-") as temp_dir:
            probe_root = Path(temp_dir)
            _run(["init", "-q"], cwd=probe_root)
            _run(
                [
                    "-c",
                    "user.name=LingyePluginMarketplace",
                    "-c",
                    "user.email=lpm@local",
                    "commit",
                    "--allow-empty",
                    "-q",
                    "-m",
                    "lpm: connection probe",
                ],
                cwd=probe_root,
            )
            write_result = subprocess.run(
                [
                    _git_executable(),
                    "-C",
                    str(probe_root),
                    "push",
                    "--dry-run",
                    "--porcelain",
                    safe_url,
                    f"HEAD:{probe_ref}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=write_timeout,
                env=env,
            )
    except subprocess.TimeoutExpired as exc:
        raise GitError("Timed out while verifying GitHub write access.") from exc
    except FileNotFoundError as exc:
        raise GitError("`git` executable not found on PATH.") from exc
    if write_result.returncode != 0:
        detail = write_result.stderr or write_result.stdout
        raise GitError(_binding_error(detail, transport, action="write"))

    return RemoteBindingProbe(
        default_branch=branch,
        branches=branches,
        remote_empty=not branches,
    )


def _binding_env(transport: str) -> dict[str, str]:
    if transport == "https":
        return {**os.environ, **_BIND_HTTPS_ENV}
    return {
        **os.environ,
        **_NO_PROMPT_ENV,
        "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=10",
    }


def _binding_error(detail: str | None, transport: str, *, action: str) -> str:
    safe_detail = redact_git_text((detail or "").strip())
    if transport == "https":
        guidance = (
            "Configure Git Credential Manager, use an SSH URL, or select token authentication "
            "in Advanced settings."
        )
    else:
        guidance = "Load a GitHub SSH key into your agent or bind the HTTPS URL instead."
    reason = safe_detail or "Authentication or repository policy rejected the request."
    return f"Unable to {action} the GitHub repository. {reason} {guidance}"


def _parse_remote_branches(output: str) -> tuple[str, list[str]]:
    default_branch = ""
    branches: set[str] = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "ref:" and parts[2] == "HEAD":
            default_branch = parts[1].removeprefix("refs/heads/")
        elif len(parts) >= 2 and parts[1].startswith("refs/heads/"):
            branches.add(parts[1].removeprefix("refs/heads/"))
    if default_branch:
        branches.add(default_branch)
    return default_branch, sorted(branches)


def _github_ssh_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        return None
    path = parsed.path.lstrip("/")
    if not path:
        return None
    return f"git@github.com:{path}"


def probe_remote(
    url: str,
    ref: str = "main",
    *,
    token: str | None = None,
    timeout: int = 15,
) -> bool:
    """Return True if the remote repo (and optional ref) is reachable.

    Uses ``git ls-remote`` without cloning.  Returns False on network errors,
    authentication failures, or non-existent repositories.
    """
    try:
        url = strip_url_credentials(_validate_argument(url, "remote URL"))
        ref = _validate_argument(ref, "ref")
        token_env = _token_env(token)
        env = {**os.environ, **_NO_PROMPT_ENV, **token_env}
        result = subprocess.run(
            [_git_executable(), "ls-remote", "--exit-code", url, ref],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    finally:
        if "token_env" in locals():
            _cleanup_askpass(token_env)


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
