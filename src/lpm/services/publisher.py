"""Publish a local resource directory to a new GitHub repository."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.config import Config
from ..core.models import ItemKind, Registry, RegistryItem
from ..core.registry import load_registry, save_registry
from ..core.secrets import sanitize_mcp_config_for_storage
from ..core.validator import parse_skill
from ..infrastructure import git_ops
from ..infrastructure.github_client import GithubClient

# Keep backward-compatible alias
SkillEntry = RegistryItem


@dataclass
class PublishResult:
    name: str
    repo_url: str
    full_name: str
    created: bool
    pushed: bool
    private: bool
    visibility_changed: bool
    visibility_mismatch: bool
    entry: RegistryItem


class VisibilityMismatchError(RuntimeError):
    """Raised when an existing repo's visibility differs from the requested one
    and the caller did not opt-in to changing it."""

    def __init__(self, full_name: str, current_private: bool, requested_private: bool):
        self.full_name = full_name
        self.current_private = current_private
        self.requested_private = requested_private
        cur = "private" if current_private else "public"
        req = "private" if requested_private else "public"
        super().__init__(
            f"Repository {full_name} is currently {cur}, but you requested {req}. "
            f"Pass --update-visibility (CLI) or update_visibility=True (MCP/API) to change it."
        )


def _slug(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "skill"


def publish_local_skill(
    path: Path | str,
    *,
    config: Config,
    name: str | None = None,
    description: str | None = None,
    private: bool | None = None,
    update_visibility: bool = False,
    registry_path: Path | None = None,
    kind: ItemKind = "skill",
    mcp_config: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    category: str = "",
    platforms: list[str] | None = None,
    version: str = "",
    author: str = "",
    item_license: str = "",
) -> PublishResult:
    """Validate, create repo, push, and record in the registry.

    Args:
        private: True = private repo, False = public, None = use config default.
        update_visibility: When True and an existing repo has a different
            visibility than requested, flip it via the GitHub API.
        kind: Resource type to publish (skill, mcp, rule).
        mcp_config: MCP server configuration dict (only for kind=mcp).
    """
    skill_dir = Path(path).expanduser().resolve()

    if kind == "skill":
        meta = parse_skill(skill_dir)
        skill_name = _slug(name or meta.name)
        skill_description = (description or meta.description).strip()
    else:
        skill_name = _slug(name or skill_dir.name)
        skill_description = (description or "").strip()

    requested_private = config.github.default_private if private is None else private

    if not config.github.token:
        from ..infrastructure.github_client import GithubAuthError

        raise GithubAuthError(
            "No GitHub token configured. Set LPM_GITHUB_TOKEN or run `lpm init`."
        )

    client = GithubClient(config.github.token)
    owner = config.github.owner.strip() or client.authenticated_login()
    repo_name = f"{config.github.repo_prefix}{skill_name}" if config.github.repo_prefix else skill_name

    repo, created = client.ensure_repo(
        owner=owner,
        name=repo_name,
        description=skill_description[:350],
        private=requested_private,
    )

    visibility_mismatch = (not created) and (repo.private != requested_private)
    visibility_changed = False
    if visibility_mismatch:
        if update_visibility:
            repo = client.set_repo_visibility(owner, repo_name, private=requested_private)
            visibility_changed = True
            visibility_mismatch = False
        else:
            raise VisibilityMismatchError(
                full_name=repo.full_name,
                current_private=repo.private,
                requested_private=requested_private,
            )

    pushed = _git_publish(skill_dir, repo.https_url, repo.default_branch, config.github.token)

    registry = load_registry(registry_path)
    entry = RegistryItem(
        name=skill_name,
        kind=kind,
        repo=repo.https_url,
        source="owned",
        subdir="",
        ref=repo.default_branch,
        install_dir="",
        description=skill_description,
        mcp_config=sanitize_mcp_config_for_storage(mcp_config),
        private=repo.private,
        tags=tags or [],
        category=category,
        platforms=platforms or [],
        version=version,
        author=author or config.github.owner,
        license=item_license,
    )
    registry.upsert(entry)
    save_registry(registry, registry_path)

    return PublishResult(
        name=skill_name,
        repo_url=repo.https_url,
        full_name=repo.full_name,
        created=created,
        pushed=pushed,
        private=repo.private,
        visibility_changed=visibility_changed,
        visibility_mismatch=visibility_mismatch,
        entry=entry,
    )


def set_skill_visibility(
    name: str,
    *,
    config: Config,
    private: bool,
    registry_path: Path | None = None,
) -> dict:
    """Flip an ``owned`` item's GitHub repo visibility (public <-> private)."""
    if not config.github.token:
        from ..infrastructure.github_client import GithubAuthError

        raise GithubAuthError(
            "No GitHub token configured. Set LPM_GITHUB_TOKEN or run `lpm init`."
        )

    registry = load_registry(registry_path)
    entry = registry.get(name)
    if entry is None:
        raise ValueError(f"No item named {name!r} in registry.")
    if entry.source != "owned":
        raise ValueError(
            f"Item {name!r} is registered as {entry.source!r}; "
            "visibility can only be changed for `owned` items."
        )

    owner, repo_name = _parse_owner_repo(entry.repo)
    client = GithubClient(config.github.token)
    repo = client.set_repo_visibility(owner, repo_name, private=private)

    entry.private = repo.private
    save_registry(registry, registry_path)

    return {
        "name": name,
        "repo": entry.repo,
        "full_name": repo.full_name,
        "private": repo.private,
    }


def _parse_owner_repo(github_url: str) -> tuple[str, str]:
    cleaned = github_url.strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[: -len(".git")]
    if cleaned.startswith("git@github.com:"):
        path = cleaned.split(":", 1)[1]
    elif cleaned.startswith("https://github.com/"):
        path = cleaned[len("https://github.com/"):]
    else:
        raise ValueError(f"Cannot parse owner/repo from {github_url!r}.")
    parts = path.split("/")
    if len(parts) < 2:
        raise ValueError(f"URL {github_url!r} does not look like owner/repo.")
    return parts[0], parts[1]


def _git_publish(skill_dir: Path, https_url: str, branch: str, token: str) -> bool:
    """Initialize (if needed), commit, and push the skill directory.

    Authentication uses GIT_ASKPASS so the token never touches .git/config.
    """
    if not git_ops.is_repo(skill_dir):
        git_ops.init_repo(skill_dir, default_branch=branch)

    git_ops.add_all(skill_dir)
    try:
        git_ops.commit(skill_dir, message="lpm: publish resource")
    except git_ops.GitError as exc:
        if "nothing to commit" not in str(exc).lower():
            raise

    git_ops.set_remote(skill_dir, "origin", https_url)
    git_ops.push(skill_dir, remote="origin", branch=branch, set_upstream=True, token=token)
    return True


class RepoUnreachableError(RuntimeError):
    """Raised when a remote repository cannot be reached during pre-verification."""

    def __init__(self, repo: str, ref: str):
        self.repo = repo
        self.ref = ref
        super().__init__(
            f"Repository {repo} (ref={ref}) is unreachable. "
            f"Pass --no-verify (CLI) or skip_verify=True to skip this check."
        )


def add_external_skill(
    github_url: str,
    *,
    name: str | None = None,
    subdir: str | None = None,
    ref: str = "main",
    description: str = "",
    registry_path: Path | None = None,
    kind: ItemKind = "skill",
    mcp_config: dict[str, Any] | None = None,
    skip_verify: bool = False,
    token: str | None = None,
    tags: list[str] | None = None,
    category: str = "",
    platforms: list[str] | None = None,
) -> RegistryItem:
    """Register a third-party resource in the registry.

    When *skip_verify* is False (the default), the remote repository is probed
    with ``git ls-remote`` before writing the entry.  Set *skip_verify* to True
    to allow offline or private-without-token registrations.
    """
    repo_url = github_url.rstrip("/")
    effective_ref = ref or "main"

    if not skip_verify:
        probe_url = git_ops.with_token(repo_url, token) if token else repo_url
        if not git_ops.probe_remote(probe_url, effective_ref):
            raise RepoUnreachableError(repo_url, effective_ref)

    inferred_name = name or _infer_name_from_url(github_url, subdir)
    entry = RegistryItem(
        name=_slug(inferred_name),
        kind=kind,
        repo=repo_url,
        source="external",
        subdir=(subdir or "").strip().strip("/"),
        ref=effective_ref,
        install_dir="",
        description=description.strip(),
        mcp_config=sanitize_mcp_config_for_storage(mcp_config),
        tags=tags or [],
        category=category,
        platforms=platforms or [],
    )
    registry = load_registry(registry_path)
    registry.upsert(entry)
    save_registry(registry, registry_path)
    return entry


def remove_skill(
    name: str,
    *,
    registry_path: Path | None = None,
) -> RegistryItem | None:
    registry = load_registry(registry_path)
    removed = registry.remove(name)
    if removed is not None:
        save_registry(registry, registry_path)
    return removed


def _infer_name_from_url(url: str, subdir: str | None) -> str:
    if subdir:
        leaf = subdir.strip("/").split("/")[-1]
        if leaf:
            return leaf
    tail = url.rstrip("/").split("/")[-1]
    return tail.removesuffix(".git") or "skill"


def _ensure_registry(path: Path | None) -> tuple[Registry, Path | None]:
    return load_registry(path), path
