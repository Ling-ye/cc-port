from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cc_port.core.config import Config, ResourcesConfig
from cc_port.services.resource_commit import (
    ResourceCommitBlocked,
    build_resource_commit_plan,
    commit_resource_changes,
    validate_outgoing_resource_commits,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def test_commit_plan_blocks_unmanaged_paths_and_only_commits_managed_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty_global = tmp_path / "empty-global.gitconfig"
    empty_global.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_global))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = _repo(tmp_path)
    cfg = Config(resources=ResourcesConfig(local_path=str(repo)))
    skill = repo / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Demo\n", encoding="utf-8")
    notes = repo / "notes.txt"
    notes.write_text("do not commit me\n", encoding="utf-8")

    blocked = build_resource_commit_plan(config=cfg)

    assert blocked.blocked is True
    assert [item.path for item in blocked.blocked_paths] == ["notes.txt"]
    assert blocked.managed_paths == ["skills/demo/SKILL.md"]
    with pytest.raises(ResourceCommitBlocked):
        commit_resource_changes(message="cc-port: add demo", config=cfg)

    notes.unlink()
    committed = commit_resource_changes(message="cc-port: add demo", config=cfg)

    assert committed.blocked is False
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.strip() == (
        "skills/demo/SKILL.md"
    )
    assert "CC_PORT-Device:" in _git(repo, "log", "-1", "--format=%B").stdout


def test_commit_plan_blocks_secret_and_excluded_environment_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    cfg = Config(resources=ResourcesConfig(local_path=str(repo)))
    skill = repo / "skills" / "secret-demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "api_key: sk_test_secret_123456789\n",
        encoding="utf-8",
    )
    (skill / ".env").write_text("TOKEN=real_secret_value\n", encoding="utf-8")

    plan = build_resource_commit_plan(config=cfg)

    assert plan.blocked is True
    assert any(item.path.endswith(".env") for item in plan.blocked_paths)
    assert any(item.path.endswith("SKILL.md") for item in plan.secret_findings)


def test_outgoing_commit_scan_blocks_hand_committed_secret_and_unmanaged_path(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    secret = repo / "skills" / "secret-demo" / "SKILL.md"
    secret.parent.mkdir(parents=True)
    secret.write_text("token: ghp_1234567890abcdefghijkl\n", encoding="utf-8")
    (repo / "manual.txt").write_text("outside scope\n", encoding="utf-8")
    _commit(repo, "unsafe")

    with pytest.raises(ResourceCommitBlocked) as error:
        validate_outgoing_resource_commits(repo, base_commit=base)

    plan = error.value.plan
    assert [item.path for item in plan.blocked_paths] == ["manual.txt"]
    assert [item.path for item in plan.secret_findings] == [
        "skills/secret-demo/SKILL.md"
    ]
    assert plan.secret_findings[0].commit


def test_outgoing_commit_scan_blocks_excluded_environment_file(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    env_file = repo / "skills" / "demo" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("MODE=production\n", encoding="utf-8")
    _commit(repo, "manual environment file")

    with pytest.raises(ResourceCommitBlocked) as error:
        validate_outgoing_resource_commits(repo, base_commit=base)

    assert error.value.plan.blocked_paths[0].path == "skills/demo/.env"
    assert "excluded" in error.value.plan.blocked_paths[0].reason


def test_placeholder_secret_values_are_allowed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    cfg = Config(resources=ResourcesConfig(local_path=str(repo)))
    skill = repo / "skills" / "placeholder" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("api_key: ${DEMO_API_KEY}\n", encoding="utf-8")

    plan = build_resource_commit_plan(config=cfg)

    assert plan.secret_findings == []
    assert plan.blocked is False


def test_configured_git_identity_is_used_without_device_trailer(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "config", "user.name", "Resource Owner")
    _git(repo, "config", "user.email", "owner@example.test")
    cfg = Config(resources=ResourcesConfig(local_path=str(repo)))
    skill = repo / "skills" / "owned" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Owned\n", encoding="utf-8")

    commit_resource_changes(message="cc-port: add owned", config=cfg)

    log = _git(repo, "log", "-1", "--format=%an|%ae|%B").stdout
    assert log.startswith("Resource Owner|owner@example.test|")
    assert "CC_PORT-Device:" not in log


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "registry.yaml").write_text("version: 1\nresources: []\n", encoding="utf-8")
    (repo / "README.md").write_text("# Resources\n", encoding="utf-8")
    _commit(repo, "base")
    return repo


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=CC Port Test",
        "-c",
        "user.email=cc-port@example.test",
        "commit",
        "-m",
        message,
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
