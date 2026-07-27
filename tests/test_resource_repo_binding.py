from __future__ import annotations

from pathlib import Path

import pytest

from cc_port.core.config import Config, GithubConfig, ResourcesConfig
from cc_port.infrastructure import git_ops
from cc_port.infrastructure.github_client import CreatedRepo
from cc_port.services import resource_repo


def test_first_pull_clones_bound_repo_with_native_credentials(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "resources"
    cfg = Config(
        github=GithubConfig(token="expired-token"),
        resources=ResourcesConfig(
            repo_name="resources",
            repo_url="https://github.com/example/resources.git",
            local_path=str(root),
            branch="main",
            credential_mode="native",
        ),
    )
    clone_tokens: list[str | None] = []

    def fake_clone(url: str, destination: Path, **kwargs) -> None:
        assert url == cfg.resources.repo_url
        clone_tokens.append(kwargs.get("token"))
        (destination / ".git").mkdir(parents=True)

    monkeypatch.setattr(resource_repo.git_ops, "clone", fake_clone)
    monkeypatch.setattr(resource_repo.git_ops, "rev_parse", lambda *_args, **_kwargs: "abc123")
    monkeypatch.setattr(resource_repo.git_ops, "current_branch", lambda *_args, **_kwargs: "main")
    monkeypatch.setattr(resource_repo.git_ops, "head_commit", lambda *_args, **_kwargs: "abc123")
    monkeypatch.setattr(resource_repo.git_ops, "status_short", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        resource_repo.git_ops,
        "current_remote_url",
        lambda *_args, **_kwargs: cfg.resources.repo_url,
    )

    result = resource_repo.pull_resource_repo(cfg)

    assert clone_tokens == [None]
    assert result.is_git_repo is True
    assert (root / "registry.yaml").is_file()


def test_first_pull_never_overwrites_nonempty_non_repo_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "resources"
    root.mkdir()
    (root / "user-file.txt").write_text("keep", encoding="utf-8")
    cfg = Config(
        resources=ResourcesConfig(
            repo_url="https://github.com/example/resources.git",
            local_path=str(root),
            credential_mode="native",
        )
    )
    clone_calls: list[tuple] = []
    monkeypatch.setattr(
        resource_repo.git_ops,
        "clone",
        lambda *args, **_kwargs: clone_calls.append(args),
    )

    with pytest.raises(git_ops.GitError, match="not an empty Git repository"):
        resource_repo.pull_resource_repo(cfg)

    assert (root / "user-file.txt").read_text(encoding="utf-8") == "keep"
    assert clone_calls == []


def test_push_before_first_pull_is_blocked(tmp_path: Path) -> None:
    cfg = Config(
        resources=ResourcesConfig(
            repo_url="https://github.com/example/resources.git",
            local_path=str(tmp_path / "missing"),
            credential_mode="native",
        )
    )

    with pytest.raises(git_ops.GitError, match="has not been pulled yet"):
        resource_repo.push_resource_repo(config=cfg)


def test_init_resource_repo_uses_bound_url_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class FakeClient:
        def __init__(self, token: str) -> None:
            assert token == "token"

        def authenticated_login(self) -> str:
            raise AssertionError("bound repository owner must avoid account fallback")

        def ensure_repo(
            self,
            owner: str,
            name: str,
            *,
            description: str,
            private: bool,
        ) -> tuple[CreatedRepo, bool]:
            captured["owner"] = owner
            return (
                CreatedRepo(
                    full_name=f"{owner}/{name}",
                    https_url=f"https://github.com/{owner}/{name}.git",
                    ssh_url=f"git@github.com:{owner}/{name}.git",
                    default_branch="main",
                    private=private,
                ),
                False,
            )

    cfg = Config(
        github=GithubConfig(token="token", owner="LegacyOwner"),
        resources=ResourcesConfig(
            repo_name="resources",
            repo_url="git@github.com:BoundOwner/resources.git",
            local_path=str(tmp_path / "resources"),
        ),
    )
    monkeypatch.setattr(resource_repo, "GithubClient", FakeClient)
    monkeypatch.setattr(
        resource_repo,
        "prepare_local_resource_repo",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(resource_repo, "inspect_resource_repo", lambda *_args, **_kwargs: "ready")

    result = resource_repo.init_resource_repo(
        config=cfg,
        config_path=tmp_path / "config.toml",
    )

    assert result == "ready"
    assert captured["owner"] == "BoundOwner"
