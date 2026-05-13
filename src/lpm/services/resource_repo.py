"""Private resource repository management for LPM user data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from ..core.config import (
    DEFAULT_RESOURCE_BRANCH,
    DEFAULT_RESOURCE_REPO_NAME,
    Config,
    ResourcesConfig,
    default_config_path,
    load_config,
    write_config,
)
from ..core.registry import CURRENT_REGISTRY_VERSION, DEFAULT_REGISTRY_FILENAME
from ..infrastructure import git_ops
from ..infrastructure.github_client import GithubClient

RESOURCE_DIRS = ("skills", "rules", "prompts", "mcp", "plugins", ".claude-plugin")
LPM_HOMEPAGE = "https://github.com/Ling-ye/LingyePluginMarketplace"


@dataclass
class ResourceRepoInfo:
    local_path: Path
    registry_path: Path
    repo_name: str
    repo_url: str
    branch: str
    exists: bool
    is_git_repo: bool
    dirty: bool
    current_branch: str
    remote_url: str


def resource_root(config: Config | None = None) -> Path:
    cfg = config or load_config()
    return cfg.resources.local_path_value.expanduser().resolve()


def registry_path(config: Config | None = None) -> Path:
    return resource_root(config) / DEFAULT_REGISTRY_FILENAME


def ensure_structure(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in RESOURCE_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(_resource_readme(root.name), encoding="utf-8")
    reg = root / DEFAULT_REGISTRY_FILENAME
    if not reg.exists():
        reg.write_text(f"version: {CURRENT_REGISTRY_VERSION}\nitems: []\n", encoding="utf-8")
    plugin_json = root / ".claude-plugin" / "plugin.json"
    if not plugin_json.exists():
        plugin_json.write_text('{\n  "name": "lpm-resources",\n  "skills": []\n}\n', encoding="utf-8")


def _resource_readme(repo_name: str) -> str:
    return (
        f"# {repo_name}\n\n"
        "This is a private AI resource repository managed by "
        f"[LPM (LingyePluginMarketplace)]({LPM_HOMEPAGE}).\n\n"
        "It stores your selected skills, rules, prompts, MCP configs, plugins, "
        "and `registry.yaml` for syncing your AI coding environment across machines.\n\n"
        "Typical commands:\n\n"
        "```bash\n"
        "lpm collect <github-url-or-tree-url>\n"
        "lpm upload <local-path>\n"
        "lpm sync\n"
        "lpm resource pull\n"
        "lpm resource push\n"
        "```\n\n"
        "Keep this repository private if it contains personal resources or metadata.\n"
    )


def init_resource_repo(
    *,
    name: str | None = None,
    config: Config | None = None,
    config_path: Path | None = None,
) -> ResourceRepoInfo:
    """Create or connect the user's private GitHub resource repository."""
    cfg = config or load_config(config_path)
    repo_name = name or cfg.resources.repo_name or DEFAULT_RESOURCE_REPO_NAME
    branch = cfg.resources.branch or DEFAULT_RESOURCE_BRANCH
    if not cfg.github.token:
        from ..infrastructure.github_client import GithubAuthError

        raise GithubAuthError("No GitHub token configured. Set LPM_GITHUB_TOKEN or run `lpm init`.")

    client = GithubClient(cfg.github.token)
    owner = cfg.github.owner.strip() or client.authenticated_login()
    repo, _created = client.ensure_repo(
        owner=owner,
        name=repo_name,
        description="Private LPM AI resources repository.",
        private=True,
    )

    local_path = _local_path_for_repo(cfg.resources, repo_name)
    if not local_path.exists():
        git_ops.clone(repo.https_url, local_path, token=cfg.github.token)
    else:
        local_path.mkdir(parents=True, exist_ok=True)
        if not git_ops.is_repo(local_path):
            git_ops.init_repo(local_path, default_branch=branch)
        git_ops.set_remote(local_path, "origin", repo.https_url)

    git_ops.checkout_branch(local_path, branch)
    ensure_structure(local_path)
    _commit_and_push_if_needed(local_path, branch=branch, token=cfg.github.token)

    cfg.resources.repo_name = repo_name
    cfg.resources.repo_url = repo.https_url
    cfg.resources.local_path = str(local_path)
    cfg.resources.branch = branch
    write_config(cfg, config_path or cfg.source_path or default_config_path())
    return inspect_resource_repo(cfg)


def use_resource_repo(target: str, *, config: Config | None = None, config_path: Path | None = None) -> ResourceRepoInfo:
    cfg = config or load_config(config_path)
    branch = cfg.resources.branch or DEFAULT_RESOURCE_BRANCH

    if _looks_like_git_url(target):
        repo_url = target.rstrip("/")
        repo_name = _repo_name_from_url(repo_url)
        local_path = _local_path_for_repo(cfg.resources, repo_name)
        if not local_path.exists():
            git_ops.clone(repo_url, local_path, token=cfg.github.token or None)
        elif not git_ops.is_repo(local_path):
            git_ops.init_repo(local_path, default_branch=branch)
            git_ops.set_remote(local_path, "origin", repo_url)
    else:
        local_path = Path(target).expanduser().resolve()
        if not local_path.exists():
            local_path.mkdir(parents=True, exist_ok=True)
        if not git_ops.is_repo(local_path):
            git_ops.init_repo(local_path, default_branch=branch)
        repo_url = git_ops.current_remote_url(local_path) if git_ops.is_repo(local_path) else ""
        repo_name = local_path.name

    ensure_structure(local_path)
    cfg.resources.repo_name = repo_name
    cfg.resources.repo_url = repo_url or ""
    cfg.resources.local_path = str(local_path)
    cfg.resources.branch = branch
    write_config(cfg, config_path or cfg.source_path or default_config_path())
    return inspect_resource_repo(cfg)


def inspect_resource_repo(config: Config | None = None) -> ResourceRepoInfo:
    cfg = config or load_config()
    root = resource_root(cfg)
    is_repo = git_ops.is_repo(root)
    status = git_ops.status_short(root) if is_repo else ""
    return ResourceRepoInfo(
        local_path=root,
        registry_path=root / DEFAULT_REGISTRY_FILENAME,
        repo_name=cfg.resources.repo_name or root.name,
        repo_url=cfg.resources.repo_url,
        branch=cfg.resources.branch or DEFAULT_RESOURCE_BRANCH,
        exists=root.exists(),
        is_git_repo=is_repo,
        dirty=bool(status),
        current_branch=(git_ops.current_branch(root) or "") if is_repo else "",
        remote_url=(git_ops.current_remote_url(root) or "") if is_repo else "",
    )


def pull_resource_repo(config: Config | None = None) -> ResourceRepoInfo:
    cfg = config or load_config()
    root = resource_root(cfg)
    if not git_ops.is_repo(root):
        raise git_ops.GitError(f"Resource repo is not a git repository: {root}")
    if git_ops.status_short(root):
        raise git_ops.GitError("Resource repo has local changes. Commit or push them before pulling.")
    git_ops.pull(root, ref=cfg.resources.branch or DEFAULT_RESOURCE_BRANCH, token=cfg.github.token or None)
    ensure_structure(root)
    return inspect_resource_repo(cfg)


def push_resource_repo(message: str = "lpm: update resources", config: Config | None = None) -> ResourceRepoInfo:
    cfg = config or load_config()
    root = resource_root(cfg)
    if not git_ops.is_repo(root):
        raise git_ops.GitError(f"Resource repo is not a git repository: {root}")
    ensure_structure(root)
    if git_ops.status_short(root):
        git_ops.add_all(root)
        git_ops.commit(root, message=message)
    git_ops.push(
        root,
        branch=cfg.resources.branch or DEFAULT_RESOURCE_BRANCH,
        token=cfg.github.token or None,
    )
    return inspect_resource_repo(cfg)


def _commit_and_push_if_needed(path: Path, *, branch: str, token: str | None) -> None:
    if git_ops.status_short(path):
        git_ops.add_all(path)
        git_ops.commit(path, message="lpm: initialize resource repository")
    git_ops.push(path, branch=branch, token=token)


def _local_path_for_repo(resources: ResourcesConfig, repo_name: str) -> Path:
    if resources.local_path:
        return Path(resources.local_path).expanduser().resolve()
    return (Path.home() / repo_name).resolve()


def _looks_like_git_url(value: str) -> bool:
    return value.startswith("https://") or value.startswith("git@")


def _repo_name_from_url(url: str) -> str:
    if url.startswith("git@"):
        tail = url.split(":", 1)[-1]
    else:
        tail = urlparse(url).path
    name = tail.rstrip("/").split("/")[-1]
    return name.removesuffix(".git") or DEFAULT_RESOURCE_REPO_NAME
