from __future__ import annotations

from pathlib import Path

import pytest

from skillhub.config import Config, GithubConfig, InstallConfig
from skillhub.installer import SyncAction, status_all, sync_all
from skillhub.models import Registry, SkillEntry


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        github=GithubConfig(token=""),
        install=InstallConfig(target=str(tmp_path / "skills")),
    )


def _registry_with_local_repo(local_path: Path) -> Registry:
    """Build a Registry that points at a local file path.

    The strict URL validator only allows GitHub URLs, so we bypass it via
    `model_construct` for testing against a local bare repo.
    """
    return Registry(
        skills=[
            SkillEntry.model_construct(
                name="upstream-skill",
                repo=str(local_path),
                source="external",
                subdir="",
                ref="main",
                install_dir="",
                description="",
            )
        ]
    )


def test_sync_clones_and_is_idempotent(tmp_path: Path, cfg: Config, fake_remote_repo: Path) -> None:
    reg = _registry_with_local_repo(fake_remote_repo)

    results = sync_all(config=cfg, registry=reg)
    assert len(results) == 1
    assert results[0].action is SyncAction.INSTALLED
    assert (cfg.install.target_path / "upstream-skill" / "SKILL.md").is_file()

    results2 = sync_all(config=cfg, registry=reg)
    assert results2[0].action in {SyncAction.UNCHANGED, SyncAction.UPDATED}


def test_status_when_not_installed(tmp_path: Path, cfg: Config) -> None:
    reg = Registry(
        skills=[
            SkillEntry(
                name="missing",
                repo="https://github.com/foo/bar",
                source="external",
                ref="main",
            )
        ]
    )
    rows = status_all(config=cfg, registry=reg)
    assert rows[0].installed is False
    assert rows[0].local_commit is None
