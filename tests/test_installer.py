from __future__ import annotations

from pathlib import Path

from lpm.config import Config
from lpm.installer import SyncAction, status_all, sync_all
from lpm.models import Registry, RegistryItem


def _registry_with_local_repo(local_path: Path) -> Registry:
    """Build a Registry that points at a local file path."""
    return Registry(
        items=[
            RegistryItem.model_construct(
                name="upstream-skill",
                kind="skill",
                repo=str(local_path),
                source="external",
                subdir="",
                ref="main",
                install_dir="",
                description="",
                mcp_config=None,
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
        items=[
            RegistryItem(
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


def test_sync_with_kind_filter(tmp_path: Path, cfg: Config) -> None:
    reg = Registry(items=[
        RegistryItem(
            name="s1", kind="skill",
            repo="https://github.com/foo/s1", source="external",
        ),
        RegistryItem(
            name="m1", kind="mcp",
            repo="https://github.com/foo/m1", source="external",
            mcp_config={"command": "test"},
        ),
    ])
    results = sync_all(config=cfg, registry=reg, kind="mcp")
    assert len(results) == 1
    assert results[0].name == "m1"
