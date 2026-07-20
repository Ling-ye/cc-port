"""Side-effect-free GitHub resource repository binding."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..core.config import (
    DEFAULT_RESOURCE_BRANCH,
    default_config_path,
    load_raw_config,
    write_config,
)
from ..infrastructure import git_ops


class ResourceBindingError(RuntimeError):
    """Raised when a repository cannot be safely bound."""


class StaleResourceBindingError(ResourceBindingError):
    """Raised when Settings changed after the binding form was loaded."""


@dataclass(frozen=True)
class ParsedGithubRepository:
    owner: str
    name: str
    canonical_url: str
    transport: str


@dataclass(frozen=True)
class ResourceBindingResult:
    owner: str
    repo_name: str
    repo_url: str
    branch: str
    branches: list[str]
    transport: str
    credential_mode: str
    read_verified: bool
    write_verified: bool
    remote_empty: bool
    local_path: str
    replaced_repo_url: str


_SCP_GITHUB_RE = re.compile(
    r"^git@github\.com:(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def parse_github_repo_url(value: str) -> ParsedGithubRepository:
    """Parse and canonicalize one root github.com repository URL."""
    raw = str(value or "").strip()
    if not raw or any(char in raw for char in "\r\n\0"):
        raise ValueError("Enter a GitHub repository URL.")

    scp_match = _SCP_GITHUB_RE.fullmatch(raw)
    if scp_match:
        return _parsed_repo(
            scp_match.group("owner"),
            scp_match.group("repo"),
            transport="ssh",
        )

    parsed = urlparse(raw)
    if parsed.query or parsed.fragment:
        raise ValueError("GitHub repository URLs cannot include query parameters or fragments.")
    if parsed.port is not None:
        raise ValueError("Custom ports are not supported for github.com repository binding.")
    if (parsed.hostname or "").lower() != "github.com":
        raise ValueError("Only github.com repository URLs can be bound.")

    if parsed.scheme == "https":
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Repository URLs must not contain embedded credentials.")
        transport = "https"
    elif parsed.scheme == "ssh":
        if parsed.username != "git" or parsed.password is not None:
            raise ValueError("GitHub SSH URLs must use the git user and contain no password.")
        transport = "ssh"
    else:
        raise ValueError("Use a GitHub HTTPS or SSH repository URL.")

    path = unquote(parsed.path).strip("/")
    parts = path.split("/") if path else []
    if len(parts) != 2:
        raise ValueError("Use the repository root URL, without tree, issue, or file subpaths.")
    return _parsed_repo(parts[0], parts[1], transport=transport)


def bind_resource_repo(
    repo_url: str,
    *,
    expected_current_repo_url: str,
    config_path: Path | None = None,
) -> ResourceBindingResult:
    """Verify a remote Git channel and persist it without cloning or pushing."""
    cfg = load_raw_config(config_path)
    current_url = cfg.resources.repo_url.strip()
    if current_url != str(expected_current_repo_url or "").strip():
        raise StaleResourceBindingError(
            "The resource repository setting changed after this page loaded. Reload Settings and retry."
        )

    parsed = parse_github_repo_url(repo_url)
    probe = git_ops.probe_remote_binding(
        parsed.canonical_url,
        transport=parsed.transport,
    )
    same_repo = _same_repository(current_url, parsed)
    replaced_url = "" if same_repo else current_url

    cfg.resources.repo_name = parsed.name
    cfg.resources.repo_url = parsed.canonical_url
    cfg.resources.branch = probe.default_branch or DEFAULT_RESOURCE_BRANCH
    cfg.resources.credential_mode = "native"
    if not same_repo:
        cfg.resources.local_path = ""

    write_config(cfg, config_path or cfg.source_path or default_config_path())
    local_path = cfg.resources.local_path_value.expanduser().resolve()
    return ResourceBindingResult(
        owner=parsed.owner,
        repo_name=parsed.name,
        repo_url=parsed.canonical_url,
        branch=cfg.resources.branch,
        branches=probe.branches,
        transport=parsed.transport,
        credential_mode="native",
        read_verified=True,
        write_verified=True,
        remote_empty=probe.remote_empty,
        local_path=str(local_path),
        replaced_repo_url=replaced_url,
    )


def _parsed_repo(owner: str, repo: str, *, transport: str) -> ParsedGithubRepository:
    clean_owner = owner.strip()
    clean_repo = repo.strip().removesuffix(".git")
    if not _OWNER_RE.fullmatch(clean_owner):
        raise ValueError("The GitHub repository owner is invalid.")
    if not clean_repo or not _REPO_RE.fullmatch(clean_repo) or clean_repo in {".", ".."}:
        raise ValueError("The GitHub repository name is invalid.")
    if transport == "https":
        canonical = f"https://github.com/{clean_owner}/{clean_repo}.git"
    else:
        canonical = f"git@github.com:{clean_owner}/{clean_repo}.git"
    return ParsedGithubRepository(
        owner=clean_owner,
        name=clean_repo,
        canonical_url=canonical,
        transport=transport,
    )


def _same_repository(current_url: str, incoming: ParsedGithubRepository) -> bool:
    if not current_url:
        return False
    try:
        current = parse_github_repo_url(current_url)
    except ValueError:
        return False
    return (
        current.owner.lower(),
        current.name.lower(),
        current.transport,
    ) == (
        incoming.owner.lower(),
        incoming.name.lower(),
        incoming.transport,
    )
