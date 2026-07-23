from __future__ import annotations

from pathlib import Path

import pytest

from lpm.core.config import Config, GithubConfig, ResourcesConfig
from lpm.core.registry import load_registry
from lpm.infrastructure.github_client import CreatedRepo
from lpm.services import publisher


def test_add_external_skill_resolves_branch_to_commit_and_stores_reference_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "registry.yaml"
    commit = "1" * 40

    def fake_resolve(url: str, ref: str, *, token: str | None = None) -> str:
        assert url == "https://github.com/example/demo"
        assert ref == "release"
        assert token == "test-token"
        return commit

    monkeypatch.setattr(publisher.git_ops, "remote_url_commit", fake_resolve)
    monkeypatch.setattr(
        publisher.git_ops,
        "probe_remote",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("resolved floating refs do not need a second remote probe")
        ),
    )

    entry = publisher.add_external_skill(
        "https://github.com/example/demo",
        name="demo",
        subdir="skills/demo",
        ref="release",
        registry_path=registry_path,
        token="test-token",
    )

    assert entry.source == "external"
    assert entry.ref == commit
    assert entry.path == ""
    assert load_registry(registry_path).get("demo") == entry
    assert not (tmp_path / "skills").exists()


def test_add_external_skill_does_not_store_unresolved_floating_ref_when_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "registry.yaml"
    monkeypatch.setattr(publisher.git_ops, "remote_url_commit", lambda *_args, **_kwargs: None)

    with pytest.raises(publisher.RepoUnreachableError):
        publisher.add_external_skill(
            "https://github.com/example/demo",
            ref="main",
            registry_path=registry_path,
            skip_verify=True,
        )

    assert not registry_path.exists()


def test_add_external_skill_does_not_write_registry_for_unsafe_subdir(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry.yaml"

    with pytest.raises(ValueError, match="subdir"):
        publisher.add_external_skill(
            "https://github.com/example/demo",
            ref="a" * 40,
            subdir="skills/../outside",
            registry_path=registry_path,
            skip_verify=True,
        )

    assert not registry_path.exists()


def test_add_external_mcp_accepts_offline_sha_and_sanitizes_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "registry.yaml"
    commit = "ABCDEF0123456789ABCDEF0123456789ABCDEF01"
    monkeypatch.setattr(
        publisher.git_ops,
        "remote_url_commit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an offline full SHA must not need ref resolution")
        ),
    )
    monkeypatch.setattr(
        publisher.git_ops,
        "probe_remote",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("skip_verify must allow an already pinned full SHA")
        ),
    )

    entry = publisher.add_external_skill(
        "https://github.com/example/demo",
        name="demo-mcp",
        ref=commit,
        kind="mcp",
        mcp_config={
            "command": "demo",
            "args": ["--token=${EXISTING_TOKEN}"],
            "env": {
                "SECRET_TOKEN": "literal-secret",
                "EXISTING_TOKEN": "${EXISTING_TOKEN}",
            },
        },
        registry_path=registry_path,
        skip_verify=True,
    )

    assert entry.ref == commit.lower()
    assert entry.mcp_config == {
        "command": "demo",
        "args": ["--token=${EXISTING_TOKEN}"],
        "env": {
            "SECRET_TOKEN": "${SECRET_TOKEN}",
            "EXISTING_TOKEN": "${EXISTING_TOKEN}",
        },
    }
    assert "literal-secret" not in registry_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "mcp_config",
    [
        {
            "command": "demo",
            "args": ["--token=ghp_1234567890abcdef1234"],
        },
        {
            "url": "https://example.test/mcp?token=ghp_1234567890abcdef1234",
        },
    ],
)
def test_add_external_mcp_rejects_secrets_outside_env_without_echoing_them(
    tmp_path: Path,
    mcp_config: dict,
) -> None:
    registry_path = tmp_path / "registry.yaml"
    commit = "b" * 40
    secret = "ghp_1234567890abcdef1234"

    with pytest.raises(publisher.UnsafeMcpConfigError) as error:
        publisher.add_external_skill(
            "https://github.com/example/demo",
            name="demo-mcp",
            ref=commit,
            kind="mcp",
            mcp_config=mcp_config,
            registry_path=registry_path,
            skip_verify=True,
        )

    assert secret not in str(error.value)
    assert not registry_path.exists()


def test_add_external_full_sha_verifies_exact_commit_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "registry.yaml"
    commit = "a" * 40
    probes: list[tuple[str, str]] = []

    def fake_probe(url: str, ref: str, **_kwargs) -> bool:
        probes.append((url, ref))
        return False

    monkeypatch.setattr(publisher.git_ops, "probe_remote", fake_probe)

    with pytest.raises(publisher.RepoUnreachableError):
        publisher.add_external_skill(
            "https://github.com/example/demo",
            ref=commit,
            registry_path=registry_path,
        )

    assert probes == [("https://github.com/example/demo", commit)]
    assert not registry_path.exists()


def test_publish_uses_bound_repo_owner_for_creation_and_default_author(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    registry_path = tmp_path / "registry.yaml"
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
                True,
            )

    monkeypatch.setattr(publisher, "GithubClient", FakeClient)
    monkeypatch.setattr(publisher, "_git_publish", lambda *_args, **_kwargs: True)
    cfg = Config(
        github=GithubConfig(token="token", owner="LegacyOwner"),
        resources=ResourcesConfig(repo_url="https://github.com/BoundOwner/resources.git"),
    )

    result = publisher.publish_local_skill(
        source,
        config=cfg,
        name="demo",
        kind="rule",
        registry_path=registry_path,
    )

    assert captured["owner"] == "BoundOwner"
    assert result.entry.author == "BoundOwner"


def test_publish_falls_back_to_authenticated_login_without_configured_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    captured: dict[str, str] = {}

    class FakeClient:
        def __init__(self, token: str) -> None:
            assert token == "token"

        def authenticated_login(self) -> str:
            return "OAuthOwner"

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
                True,
            )

    monkeypatch.setattr(publisher, "GithubClient", FakeClient)
    monkeypatch.setattr(publisher, "_git_publish", lambda *_args, **_kwargs: True)

    result = publisher.publish_local_skill(
        source,
        config=Config(github=GithubConfig(token="token")),
        name="demo",
        kind="rule",
        registry_path=tmp_path / "registry.yaml",
    )

    assert captured["owner"] == "OAuthOwner"
    assert result.entry.author == "OAuthOwner"
