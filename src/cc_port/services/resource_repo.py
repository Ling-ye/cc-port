"""Portable Git resource repository management."""

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
    resource_repo_auth_token,
    write_config,
)
from ..core.registry import CURRENT_REGISTRY_VERSION, DEFAULT_REGISTRY_FILENAME, load_registry
from ..infrastructure import git_ops
from ..infrastructure.github_client import GithubClient
from .resource_binding import configured_github_owner
from .resource_commit import commit_resource_changes_unlocked
from .resource_repo_lock import resource_repo_write_lock

RESOURCE_DIRS = ("skills", "rules", "prompts", "mcp", "plugins")
CC_PORT_HOMEPAGE = "https://github.com/Ling-ye/cc-port"


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
        reg.write_text(
            f"version: {CURRENT_REGISTRY_VERSION}\nresources: []\n",
            encoding="utf-8",
        )


def _resource_readme(repo_name: str) -> str:
    return (
        f"# {repo_name}\n\n"
        "This is a portable AI resource repository. "
        f"[CC Port]({CC_PORT_HOMEPAGE}) can consume it, but the repository and "
        "its `registry.yaml` manifest are not exclusive to CC Port.\n\n"
        "`registry.yaml` lists resource identities and their repository paths or "
        "external sources. Resource content remains the source of truth. See the "
        f"[Registry v1 specification]({CC_PORT_HOMEPAGE}/blob/main/docs/specs/registry-v1.md).\n\n"
        "Optional CC Port checks:\n\n"
        "```bash\n"
        "cc-port resource registry-check\n"
        "cc-port resource registry-repair --dry-run\n"
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
    git_ops.configure_git_executable(cfg.git.executable)
    repo_name = name or cfg.resources.repo_name or DEFAULT_RESOURCE_REPO_NAME
    branch = cfg.resources.branch or DEFAULT_RESOURCE_BRANCH
    if not cfg.github.token:
        from ..infrastructure.github_client import GithubAuthError

        raise GithubAuthError("No GitHub token configured. Set CC_PORT_GITHUB_TOKEN or run `cc-port init`.")

    client = GithubClient(cfg.github.token)
    owner = configured_github_owner(cfg) or client.authenticated_login()
    repo, _created = client.ensure_repo(
        owner=owner,
        name=repo_name,
        description="Private CC Port AI resources repository.",
        private=True,
    )

    local_path = _local_path_for_repo(cfg.resources, repo_name)
    prepare_local_resource_repo(
        local_path,
        repo_url=repo.https_url,
        branch=branch,
        token=resource_repo_auth_token(cfg),
        config=cfg,
    )

    cfg.resources.repo_name = repo_name
    cfg.resources.repo_url = repo.https_url
    cfg.resources.local_path = str(local_path)
    cfg.resources.branch = branch
    write_config(cfg, config_path or cfg.source_path or default_config_path())
    return inspect_resource_repo(cfg)


def use_resource_repo(target: str, *, config: Config | None = None, config_path: Path | None = None) -> ResourceRepoInfo:
    cfg = config or load_config(config_path)
    git_ops.configure_git_executable(cfg.git.executable)
    branch = cfg.resources.branch or DEFAULT_RESOURCE_BRANCH

    if _looks_like_git_url(target):
        repo_url = target.rstrip("/")
        repo_name = _repo_name_from_url(repo_url)
        local_path = _local_path_for_repo(cfg.resources, repo_name)
        with resource_repo_write_lock(
            local_path,
            timeout_seconds=cfg.state.lock_timeout_seconds,
        ):
            _connect_local_resource_repo_unlocked(
                local_path,
                repo_url=repo_url,
                branch=branch,
                token=resource_repo_auth_token(cfg),
            )
            ensure_structure(local_path)
    else:
        local_path = Path(target).expanduser().resolve()
        with resource_repo_write_lock(
            local_path,
            timeout_seconds=cfg.state.lock_timeout_seconds,
        ):
            if not local_path.exists():
                local_path.mkdir(parents=True, exist_ok=True)
            if not git_ops.is_repo(local_path):
                git_ops.init_repo(local_path, default_branch=branch)
            ensure_structure(local_path)
        repo_url = git_ops.current_remote_url(local_path) if git_ops.is_repo(local_path) else ""
        repo_name = local_path.name

    cfg.resources.repo_name = repo_name
    cfg.resources.repo_url = repo_url or ""
    cfg.resources.local_path = str(local_path)
    cfg.resources.branch = branch
    write_config(cfg, config_path or cfg.source_path or default_config_path())
    return inspect_resource_repo(cfg)


def connect_local_resource_repo(
    local_path: Path,
    *,
    repo_url: str,
    branch: str = DEFAULT_RESOURCE_BRANCH,
    token: str | None = None,
    config: Config | None = None,
) -> None:
    """Ensure a local resource directory is connected to its configured remote.

    If the local directory is only the empty scaffold generated by CC Port and the
    configured remote already has data, prefer the remote branch. This repairs
    the common case where settings saved a repo URL while continuing to read an
    unconnected empty local registry.
    """
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    with resource_repo_write_lock(
        local_path,
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        _connect_local_resource_repo_unlocked(
            local_path,
            repo_url=repo_url,
            branch=branch,
            token=token,
        )


def _connect_local_resource_repo_unlocked(
    local_path: Path,
    *,
    repo_url: str,
    branch: str,
    token: str | None,
) -> None:
    local_path = local_path.expanduser().resolve()
    if not local_path.exists():
        git_ops.clone(repo_url, local_path, token=token)
    else:
        local_path.mkdir(parents=True, exist_ok=True)
        if not git_ops.is_repo(local_path):
            git_ops.init_repo(local_path, default_branch=branch)

    git_ops.set_remote(local_path, "origin", repo_url)
    _sync_branch_from_remote_if_present(local_path, branch=branch, token=token)


def prepare_local_resource_repo(
    local_path: Path,
    *,
    repo_url: str,
    branch: str = DEFAULT_RESOURCE_BRANCH,
    token: str | None = None,
    config: Config | None = None,
) -> None:
    """Connect, scaffold, commit, and push one resource repo under one lock."""
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    with resource_repo_write_lock(
        local_path,
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        _connect_local_resource_repo_unlocked(
            local_path,
            repo_url=repo_url,
            branch=branch,
            token=token,
        )
        ensure_structure(local_path)
        _commit_and_push_if_needed(
            local_path,
            branch=branch,
            token=token,
        )


def inspect_resource_repo(config: Config | None = None) -> ResourceRepoInfo:
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
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


def _clone_resource_repo_for_first_pull(cfg: Config, root: Path) -> None:
    repo_url = cfg.resources.repo_url.strip()
    if not repo_url:
        raise git_ops.GitError("No bound resource repository URL is configured.")
    if root.exists():
        if not root.is_dir():
            raise git_ops.GitError(f"Resource repo path is not a directory: {root}")
        if any(root.iterdir()):
            raise git_ops.GitError(
                f"Resource repo path exists and is not an empty Git repository: {root}"
            )
    git_ops.clone(repo_url, root, token=resource_repo_auth_token(cfg))
    branch = cfg.resources.branch or DEFAULT_RESOURCE_BRANCH
    remote_branch = git_ops.rev_parse(root, f"origin/{branch}")
    if remote_branch:
        if git_ops.current_branch(root) != branch:
            git_ops.checkout_remote_branch(root, branch)
    elif git_ops.head_commit(root) is None:
        git_ops.checkout_local_branch(root, branch)
    else:
        raise git_ops.GitError(
            f"Configured resource branch {branch!r} does not exist in the bound repository."
        )


def _assert_expected_remote(cfg: Config, root: Path) -> None:
    expected = _normalized_remote(cfg.resources.repo_url)
    actual_url = git_ops.current_remote_url(root) or ""
    actual = _normalized_remote(actual_url)
    if not actual_url:
        raise git_ops.GitError(f"Resource repo has no origin remote: {root}")
    if expected and actual != expected:
        raise git_ops.GitError(
            f"Resource repo origin is {actual_url}, not the bound repository {cfg.resources.repo_url}."
        )


def _normalized_remote(value: str) -> str:
    return value.strip().removesuffix(".git").rstrip("/").lower()


def pull_resource_repo(config: Config | None = None) -> ResourceRepoInfo:
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    root = resource_root(cfg)
    with resource_repo_write_lock(
        root,
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        if not git_ops.is_repo(root):
            _clone_resource_repo_for_first_pull(cfg, root)
            ensure_structure(root)
            return inspect_resource_repo(cfg)
        _assert_expected_remote(cfg, root)
        from .resource_sync import apply_resource_sync_plan, build_resource_sync_plan

        plan = build_resource_sync_plan(config=cfg)
        if plan.status == "conflict":
            raise git_ops.GitError(
                "Resource history has conflicts. Resolve sync operation "
                f"{plan.operation_id} before pulling."
            )
        if plan.blocked:
            raise git_ops.GitError(plan.detail or f"Resource sync is blocked: {plan.status}")
        apply_resource_sync_plan(plan.operation_id, config=cfg)
        ensure_structure(root)
    return inspect_resource_repo(cfg)


def push_resource_repo(message: str = "cc-port: update resources", config: Config | None = None) -> ResourceRepoInfo:
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    root = resource_root(cfg)
    with resource_repo_write_lock(
        root,
        timeout_seconds=cfg.state.lock_timeout_seconds,
    ):
        if not git_ops.is_repo(root):
            raise git_ops.GitError(
                f"Resource repo has not been pulled yet: {root}. Pull it before pushing."
            )
        _assert_expected_remote(cfg, root)
        ensure_structure(root)
        if git_ops.status_short(root):
            commit_resource_changes_unlocked(root, message=message)
        from .resource_sync import push_resource_sync

        push_resource_sync(config=cfg)
    return inspect_resource_repo(cfg)


def _sync_branch_from_remote_if_present(path: Path, *, branch: str, token: str | None) -> None:
    if git_ops.remote_commit(path, branch, token=token) is None:
        git_ops.checkout_local_branch(path, branch)
        return

    if git_ops.status_short(path):
        raise git_ops.GitError(
            f"Resource repo has local changes: {path}. Commit or clean them before connecting remote data."
        )

    git_ops.fetch(path, ref=branch, token=token)
    if git_ops.head_commit(path) is None:
        git_ops.checkout_remote_branch(path, branch)
        return

    git_ops.checkout_local_branch(path, branch)
    try:
        git_ops.merge_ff_only(path, f"origin/{branch}")
    except git_ops.GitError as exc:
        if _is_generated_empty_scaffold(path):
            git_ops.checkout_remote_branch(path, branch)
            return
        raise git_ops.GitError(
            "Configured resource repo remote has history that cannot be fast-forwarded "
            f"into the existing local repo at {path}. Choose an empty local path or "
            "resolve the local repository history before preparing again."
        ) from exc


def _is_generated_empty_scaffold(root: Path) -> bool:
    reg = root / DEFAULT_REGISTRY_FILENAME
    if not reg.is_file():
        return False
    try:
        if load_registry(reg).items:
            return False
    except Exception:
        return False

    allowed_files = {root / "README.md", reg}
    for path in root.rglob("*"):
        if ".git" in path.relative_to(root).parts:
            continue
        if path.is_file() and path not in allowed_files:
            return False

    readme = root / "README.md"
    if readme.is_file() and readme.read_text(encoding="utf-8") != _resource_readme(root.name):
        return False

    return True


def _commit_and_push_if_needed(path: Path, *, branch: str, token: str | None) -> None:
    if git_ops.status_short(path):
        commit_resource_changes_unlocked(
            path,
            message="cc-port: initialize resource repository",
        )
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
