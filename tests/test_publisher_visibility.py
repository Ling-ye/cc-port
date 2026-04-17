"""Tests for repo visibility flow in publisher."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from skillhub.config import Config, GithubConfig, InstallConfig
from skillhub.github_client import CreatedRepo
from skillhub.platforms import PlatformsConfig
from skillhub.publisher import (
    VisibilityMismatchError,
    _parse_owner_repo,
    publish_local_skill,
    set_skill_visibility,
)


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        github=GithubConfig(token="t", owner="alice", repo_prefix="cursor-skill-"),
        install=InstallConfig(target=str(tmp_path / "install")),
        platforms=PlatformsConfig(),
    )


@pytest.fixture
def fake_skill(tmp_path: Path) -> Path:
    d = tmp_path / "demo-skill"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo for tests.\n---\n# x\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    p = tmp_path / "registry.yaml"
    p.write_text("version: 2\nitems: []\n", encoding="utf-8")
    return p


def _patch_clients(monkeypatch, *, existing_private: bool | None, public_repo: bool):
    from skillhub import publisher as pub_mod

    fake_client = MagicMock()
    fake_client.authenticated_login.return_value = "alice"

    if existing_private is None:
        repo = CreatedRepo(
            full_name="alice/cursor-skill-demo-skill",
            https_url="https://github.com/alice/cursor-skill-demo-skill.git",
            ssh_url="git@github.com:alice/cursor-skill-demo-skill.git",
            default_branch="main",
            private=not public_repo,
        )
        fake_client.ensure_repo.return_value = (repo, True)
    else:
        repo = CreatedRepo(
            full_name="alice/cursor-skill-demo-skill",
            https_url="https://github.com/alice/cursor-skill-demo-skill.git",
            ssh_url="git@github.com:alice/cursor-skill-demo-skill.git",
            default_branch="main",
            private=existing_private,
        )
        fake_client.ensure_repo.return_value = (repo, False)
        flipped = CreatedRepo(
            full_name=repo.full_name,
            https_url=repo.https_url,
            ssh_url=repo.ssh_url,
            default_branch=repo.default_branch,
            private=not existing_private,
        )
        fake_client.set_repo_visibility.return_value = flipped

    monkeypatch.setattr(pub_mod, "GithubClient", lambda token: fake_client)
    monkeypatch.setattr(pub_mod, "_git_publish", lambda *a, **kw: True)
    return fake_client


def test_publish_explicit_public(monkeypatch, cfg, fake_skill, registry_path):
    fake = _patch_clients(monkeypatch, existing_private=None, public_repo=True)
    result = publish_local_skill(
        fake_skill,
        config=cfg,
        private=False,
        registry_path=registry_path,
    )
    assert result.private is False
    assert result.created is True
    assert result.visibility_changed is False
    fake.ensure_repo.assert_called_once()
    assert fake.ensure_repo.call_args.kwargs["private"] is False


def test_publish_explicit_private(monkeypatch, cfg, fake_skill, registry_path):
    fake = _patch_clients(monkeypatch, existing_private=None, public_repo=False)
    result = publish_local_skill(
        fake_skill,
        config=cfg,
        private=True,
        registry_path=registry_path,
    )
    assert result.private is True
    assert fake.ensure_repo.call_args.kwargs["private"] is True


def test_publish_visibility_mismatch_raises(monkeypatch, cfg, fake_skill, registry_path):
    _patch_clients(monkeypatch, existing_private=False, public_repo=True)
    with pytest.raises(VisibilityMismatchError) as ei:
        publish_local_skill(
            fake_skill,
            config=cfg,
            private=True,
            registry_path=registry_path,
        )
    assert ei.value.current_private is False
    assert ei.value.requested_private is True


def test_publish_visibility_mismatch_force_updates(monkeypatch, cfg, fake_skill, registry_path):
    fake = _patch_clients(monkeypatch, existing_private=False, public_repo=True)
    result = publish_local_skill(
        fake_skill,
        config=cfg,
        private=True,
        update_visibility=True,
        registry_path=registry_path,
    )
    assert result.visibility_changed is True
    assert result.private is True
    fake.set_repo_visibility.assert_called_once()


def test_publish_default_uses_config(monkeypatch, fake_skill, registry_path, tmp_path):
    cfg = Config(
        github=GithubConfig(token="t", owner="alice", default_private=True),
        install=InstallConfig(target=str(tmp_path / "i")),
        platforms=PlatformsConfig(),
    )
    fake = _patch_clients(monkeypatch, existing_private=None, public_repo=False)
    publish_local_skill(fake_skill, config=cfg, registry_path=registry_path)
    assert fake.ensure_repo.call_args.kwargs["private"] is True


def test_publish_saves_kind_in_registry(monkeypatch, cfg, fake_skill, registry_path):
    """Published skill should be stored with kind=skill in registry."""
    _patch_clients(monkeypatch, existing_private=None, public_repo=True)
    result = publish_local_skill(
        fake_skill, config=cfg, private=False, registry_path=registry_path
    )
    assert result.entry.kind == "skill"

    from skillhub.registry import load_registry
    reg = load_registry(registry_path)
    assert reg.items[0].kind == "skill"


def test_set_skill_visibility(monkeypatch, cfg, registry_path):
    from skillhub import publisher as pub_mod

    registry_path.write_text(
        "version: 2\n"
        "items:\n"
        "  - name: demo\n"
        "    kind: skill\n"
        "    repo: https://github.com/alice/cursor-skill-demo\n"
        "    source: owned\n"
        "    subdir: \"\"\n"
        "    ref: main\n"
        "    install_dir: \"\"\n"
        "    description: demo\n",
        encoding="utf-8",
    )
    fake = MagicMock()
    fake.set_repo_visibility.return_value = CreatedRepo(
        full_name="alice/cursor-skill-demo",
        https_url="https://github.com/alice/cursor-skill-demo.git",
        ssh_url="git@github.com:alice/cursor-skill-demo.git",
        default_branch="main",
        private=True,
    )
    monkeypatch.setattr(pub_mod, "GithubClient", lambda token: fake)
    out = set_skill_visibility("demo", config=cfg, private=True, registry_path=registry_path)
    assert out["private"] is True
    fake.set_repo_visibility.assert_called_once_with(
        "alice", "cursor-skill-demo", private=True
    )


def test_set_skill_visibility_rejects_external(monkeypatch, cfg, registry_path):
    registry_path.write_text(
        "version: 2\n"
        "items:\n"
        "  - name: third\n"
        "    kind: skill\n"
        "    repo: https://github.com/foo/bar\n"
        "    source: external\n"
        "    subdir: \"\"\n"
        "    ref: main\n"
        "    install_dir: \"\"\n"
        "    description: \"\"\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="owned"):
        set_skill_visibility("third", config=cfg, private=True, registry_path=registry_path)


def test_parse_owner_repo() -> None:
    assert _parse_owner_repo("https://github.com/alice/repo") == ("alice", "repo")
    assert _parse_owner_repo("https://github.com/alice/repo.git") == ("alice", "repo")
    assert _parse_owner_repo("https://github.com/alice/repo/") == ("alice", "repo")
    assert _parse_owner_repo("git@github.com:alice/repo.git") == ("alice", "repo")
    with pytest.raises(ValueError):
        _parse_owner_repo("https://gitlab.com/alice/repo")
