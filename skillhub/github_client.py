"""Thin wrapper around PyGithub for repo creation and lookup."""

from __future__ import annotations

from dataclasses import dataclass

from github import Github, GithubException
from github.Repository import Repository


class GithubAuthError(RuntimeError):
    """Raised when no token is configured or the token is rejected."""


@dataclass
class CreatedRepo:
    full_name: str
    https_url: str
    ssh_url: str
    default_branch: str
    private: bool


class GithubClient:
    def __init__(self, token: str):
        if not token:
            raise GithubAuthError(
                "No GitHub token configured. Set SKILLHUB_GITHUB_TOKEN or run `skillhub init`."
            )
        self._gh = Github(login_or_token=token)
        self._cached_user_login: str | None = None

    def authenticated_login(self) -> str:
        if self._cached_user_login is None:
            try:
                self._cached_user_login = self._gh.get_user().login
            except GithubException as exc:
                raise GithubAuthError(f"GitHub token rejected: {exc.data}") from exc
        return self._cached_user_login

    def get_repo(self, owner: str, name: str) -> Repository | None:
        try:
            return self._gh.get_repo(f"{owner}/{name}")
        except GithubException as exc:
            if exc.status == 404:
                return None
            raise

    def create_repo(
        self,
        owner: str,
        name: str,
        *,
        description: str = "",
        private: bool = False,
        homepage: str = "",
    ) -> CreatedRepo:
        """Create a repository under `owner`. If `owner` is the authenticated
        user, uses the user endpoint; otherwise creates under that organization.
        """
        me = self.authenticated_login()
        target_owner = owner or me
        if target_owner == me:
            user = self._gh.get_user()
            repo = user.create_repo(
                name=name,
                description=description,
                private=private,
                homepage=homepage,
                auto_init=False,
            )
        else:
            org = self._gh.get_organization(target_owner)
            repo = org.create_repo(
                name=name,
                description=description,
                private=private,
                homepage=homepage,
                auto_init=False,
            )
        return CreatedRepo(
            full_name=repo.full_name,
            https_url=repo.clone_url,
            ssh_url=repo.ssh_url,
            default_branch=repo.default_branch or "main",
            private=bool(repo.private),
        )

    def ensure_repo(
        self,
        owner: str,
        name: str,
        *,
        description: str = "",
        private: bool = False,
    ) -> tuple[CreatedRepo, bool]:
        """Get or create a repository. Returns (repo, created)."""
        existing = self.get_repo(owner, name)
        if existing is not None:
            return (
                CreatedRepo(
                    full_name=existing.full_name,
                    https_url=existing.clone_url,
                    ssh_url=existing.ssh_url,
                    default_branch=existing.default_branch or "main",
                    private=bool(existing.private),
                ),
                False,
            )
        return self.create_repo(owner, name, description=description, private=private), True

    def set_repo_visibility(self, owner: str, name: str, *, private: bool) -> CreatedRepo:
        """Flip an existing repository's visibility (public <-> private)."""
        repo = self.get_repo(owner, name)
        if repo is None:
            raise GithubException(404, {"message": f"Repository {owner}/{name} not found."}, None)
        if bool(repo.private) != private:
            repo.edit(private=private)
            repo = self.get_repo(owner, name)  # refresh
        return CreatedRepo(
            full_name=repo.full_name,
            https_url=repo.clone_url,
            ssh_url=repo.ssh_url,
            default_branch=repo.default_branch or "main",
            private=bool(repo.private),
        )
