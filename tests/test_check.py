"""Tests for repository reachability checking: probe_remote, add pre-verify,
check_one/check_all, and sync REPO_GONE detection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lpm.config import Config
from lpm.git_ops import looks_like_repo_gone, probe_remote
from lpm.installer import (
    SyncAction,
    check_all,
    check_one,
    sync_all,
)
from lpm.models import Registry, RegistryItem
from lpm.publisher import RepoUnreachableError, add_external_skill


def _make_registry(*names: str) -> Registry:
    return Registry(items=[
        RegistryItem(
            name=n,
            kind="skill",
            repo=f"https://github.com/test/{n}",
            source="external",
        )
        for n in names
    ])


# ---------------------------------------------------------------------------
# probe_remote
# ---------------------------------------------------------------------------


class TestProbeRemote:
    def test_returns_true_for_reachable_repo(self, fake_remote_repo: Path) -> None:
        assert probe_remote(str(fake_remote_repo), "main") is True

    def test_returns_false_for_nonexistent_repo(self) -> None:
        assert probe_remote("https://github.com/__nonexistent__/repo", "main") is False

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_returns_false_when_git_missing(self, _mock: object) -> None:
        assert probe_remote("https://github.com/a/b", "main") is False


# ---------------------------------------------------------------------------
# looks_like_repo_gone
# ---------------------------------------------------------------------------


class TestLooksLikeRepoGone:
    @pytest.mark.parametrize("msg", [
        "fatal: repository 'https://github.com/x/y' not found",
        "ERROR: Repository not found.",
        "remote: Repository not found.\nfatal: Could not read from remote repository.",
        "The requested URL returned error: 403",
    ])
    def test_matches_known_patterns(self, msg: str) -> None:
        assert looks_like_repo_gone(msg) is True

    def test_rejects_unrelated_error(self) -> None:
        assert looks_like_repo_gone("permission denied (publickey)") is False


# ---------------------------------------------------------------------------
# add_external_skill pre-verify
# ---------------------------------------------------------------------------


class TestAddPreVerify:
    def test_rejects_unreachable_repo(self, registry_path: Path) -> None:
        with patch("lpm.git_ops.probe_remote", return_value=False):
            with pytest.raises(RepoUnreachableError):
                add_external_skill(
                    "https://github.com/gone/repo",
                    registry_path=registry_path,
                )

    def test_skip_verify_bypasses_check(self, registry_path: Path) -> None:
        with patch("lpm.git_ops.probe_remote", return_value=False) as mock_probe:
            entry = add_external_skill(
                "https://github.com/gone/repo",
                registry_path=registry_path,
                skip_verify=True,
            )
            mock_probe.assert_not_called()
            assert entry.name == "repo"

    def test_passes_token_to_probe(self, registry_path: Path) -> None:
        with patch("lpm.git_ops.probe_remote", return_value=True) as mock_probe:
            add_external_skill(
                "https://github.com/test/skill",
                registry_path=registry_path,
                token="ghp_xxx",
            )
            url_arg = mock_probe.call_args[0][0]
            assert "ghp_xxx" in url_arg


# ---------------------------------------------------------------------------
# check_one / check_all
# ---------------------------------------------------------------------------


class TestCheckOne:
    def test_reachable(self) -> None:
        entry = RegistryItem(
            name="ok", repo="https://github.com/test/ok", source="external",
        )
        with patch("lpm.git_ops.probe_remote", return_value=True):
            result = check_one(entry)
        assert result.reachable is True
        assert entry.reachable is True
        assert entry.last_checked is not None

    def test_unreachable(self) -> None:
        entry = RegistryItem(
            name="gone", repo="https://github.com/test/gone", source="external",
        )
        with patch("lpm.git_ops.probe_remote", return_value=False):
            result = check_one(entry)
        assert result.reachable is False
        assert entry.reachable is False


class TestCheckAll:
    def test_reports_mixed_results(self, cfg: Config) -> None:
        reg = _make_registry("good", "bad")

        def _probe(url: str, ref: str = "main", **_kw: object) -> bool:
            return "good" in url

        with patch("lpm.git_ops.probe_remote", side_effect=_probe):
            results, pruned = check_all(config=cfg, registry=reg)

        assert len(results) == 2
        assert results[0].reachable is True
        assert results[1].reachable is False
        assert pruned == []

    def test_prune_removes_unreachable(self, cfg: Config, tmp_path: Path) -> None:
        reg_path = tmp_path / "registry.yaml"
        reg_path.write_text("version: 2\nitems: []\n", encoding="utf-8")
        reg = _make_registry("keep", "remove")
        from lpm.registry import save_registry
        save_registry(reg, reg_path)

        def _probe(url: str, ref: str = "main", **_kw: object) -> bool:
            return "keep" in url

        with patch("lpm.git_ops.probe_remote", side_effect=_probe):
            results, pruned = check_all(
                config=cfg, registry_path=reg_path, prune=True,
            )

        assert pruned == ["remove"]
        from lpm.registry import load_registry
        remaining = load_registry(reg_path)
        assert len(remaining.items) == 1
        assert remaining.items[0].name == "keep"

    def test_kind_filter(self, cfg: Config) -> None:
        reg = Registry(items=[
            RegistryItem(
                name="s1", kind="skill",
                repo="https://github.com/test/s1", source="external",
            ),
            RegistryItem(
                name="m1", kind="mcp",
                repo="https://github.com/test/m1", source="external",
                mcp_config={"command": "test"},
            ),
        ])
        with patch("lpm.git_ops.probe_remote", return_value=True):
            results, _ = check_all(config=cfg, registry=reg, kind="mcp")
        assert len(results) == 1
        assert results[0].name == "m1"


# ---------------------------------------------------------------------------
# sync REPO_GONE detection
# ---------------------------------------------------------------------------


class TestSyncRepoGone:
    def test_repo_gone_detected(self, cfg: Config) -> None:
        reg = _make_registry("dead-skill")
        error_msg = "fatal: repository 'https://github.com/test/dead-skill' not found"

        from lpm import git_ops
        with patch.object(
            git_ops, "is_repo", return_value=False,
        ), patch.object(
            git_ops, "clone", side_effect=git_ops.GitError(error_msg),
        ):
            results = sync_all(config=cfg, registry=reg)

        assert len(results) == 1
        assert results[0].action is SyncAction.REPO_GONE

    def test_other_git_error_stays_failed(self, cfg: Config) -> None:
        reg = _make_registry("broken-skill")
        error_msg = "fatal: unable to access: Timeout"

        from lpm import git_ops
        with patch.object(
            git_ops, "is_repo", return_value=False,
        ), patch.object(
            git_ops, "clone", side_effect=git_ops.GitError(error_msg),
        ):
            results = sync_all(config=cfg, registry=reg)

        assert len(results) == 1
        assert results[0].action is SyncAction.FAILED
