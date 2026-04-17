"""Publish a local skill directory to a new GitHub repository."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import git_ops
from .config import Config
from .github_client import GithubClient
from .models import Registry, SkillEntry
from .registry import load_registry, save_registry
from .validator import parse_skill


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
    entry: SkillEntry


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
) -> PublishResult:
    """Validate, create repo, push, and record in the registry.

    Args:
        private: True = private repo, False = public, None = use config default.
        update_visibility: When True and an existing repo has a different
            visibility than requested, flip it via the GitHub API. When False
            and there is a mismatch, `VisibilityMismatchError` is raised before
            anything is pushed.
    """
    skill_dir = Path(path).expanduser().resolve()
    meta = parse_skill(skill_dir)

    skill_name = _slug(name or meta.name)
    skill_description = (description or meta.description).strip()
    requested_private = config.github.default_private if private is None else private

    if not config.github.token:
        from .github_client import GithubAuthError

        raise GithubAuthError(
            "No GitHub token configured. Set SKILLHUB_GITHUB_TOKEN or run `skillhub init`."
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
    entry = SkillEntry(
        name=skill_name,
        repo=repo.https_url,
        source="owned",
        subdir="",
        ref=repo.default_branch,
        install_dir="",
        description=skill_description,
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
    """Flip an `owned` skill's GitHub repo visibility (public <-> private).

    Returns the new state for both the registry entry and the GitHub repo.
    """
    if not config.github.token:
        from .github_client import GithubAuthError

        raise GithubAuthError(
            "No GitHub token configured. Set SKILLHUB_GITHUB_TOKEN or run `skillhub init`."
        )

    registry = load_registry(registry_path)
    entry = registry.get(name)
    if entry is None:
        raise ValueError(f"No skill named {name!r} in registry.")
    if entry.source != "owned":
        raise ValueError(
            f"Skill {name!r} is registered as {entry.source!r}; "
            "visibility can only be changed for `owned` skills."
        )

    owner, repo_name = _parse_owner_repo(entry.repo)
    client = GithubClient(config.github.token)
    repo = client.set_repo_visibility(owner, repo_name, private=private)
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
        path = cleaned[len("https://github.com/") :]
    else:
        raise ValueError(f"Cannot parse owner/repo from {github_url!r}.")
    parts = path.split("/")
    if len(parts) < 2:
        raise ValueError(f"URL {github_url!r} does not look like owner/repo.")
    return parts[0], parts[1]


def _git_publish(skill_dir: Path, https_url: str, branch: str, token: str) -> bool:
    """Initialize (if needed), commit, and push the skill directory.

    Returns True if a push was performed (i.e. there was anything to send).
    """
    if not git_ops.is_repo(skill_dir):
        git_ops.init_repo(skill_dir, default_branch=branch)

    git_ops.add_all(skill_dir)
    try:
        git_ops.commit(skill_dir, message="skillhub: publish skill")
    except git_ops.GitError as exc:
        if "nothing to commit" not in str(exc).lower():
            raise

    authed = git_ops.with_token(https_url, token)
    git_ops.set_remote(skill_dir, "origin", authed)
    git_ops.push(skill_dir, remote="origin", branch=branch, set_upstream=True)
    git_ops.set_remote(skill_dir, "origin", https_url)
    return True


def add_external_skill(
    github_url: str,
    *,
    name: str | None = None,
    subdir: str | None = None,
    ref: str = "main",
    description: str = "",
    registry_path: Path | None = None,
) -> SkillEntry:
    """Register a third-party skill repository in the registry."""
    inferred_name = name or _infer_name_from_url(github_url, subdir)
    entry = SkillEntry(
        name=_slug(inferred_name),
        repo=github_url.rstrip("/"),
        source="external",
        subdir=(subdir or "").strip().strip("/"),
        ref=ref or "main",
        install_dir="",
        description=description.strip(),
    )
    registry = load_registry(registry_path)
    registry.upsert(entry)
    save_registry(registry, registry_path)
    return entry


def remove_skill(
    name: str,
    *,
    registry_path: Path | None = None,
) -> SkillEntry | None:
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
