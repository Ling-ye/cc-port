from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cc_port.core.config import Config, ResourcesConfig
from cc_port.core.platforms import PlatformProfile, PlatformsConfig
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


def test_commit_and_push_validation_reject_invalid_private_overlay(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    cfg = Config(resources=ResourcesConfig(local_path=str(repo)))
    private_slot = "C--Users-private-work-project"
    (repo / "cc-port.yaml").write_text(
        "version: 1\nresources:\n  memory:shared:\n"
        f"    install_name: {private_slot}\n",
        encoding="utf-8",
    )

    plan = build_resource_commit_plan(config=cfg)

    assert plan.blocked is True
    issue = next(item for item in plan.blocked_paths if item.path == "cc-port.yaml")
    assert "invalid" in issue.reason
    assert private_slot not in issue.reason
    with pytest.raises(ResourceCommitBlocked):
        commit_resource_changes(message="cc-port: unsafe overlay", config=cfg)

    _commit(repo, "manual invalid overlay")
    with pytest.raises(ResourceCommitBlocked):
        validate_outgoing_resource_commits(repo, base_commit="HEAD~1", config=cfg)


def test_commit_plan_rejects_state_root_inside_resource_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    state_root = repo / "memories" / "asset-plans"
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(state_root))
    cfg = Config(resources=ResourcesConfig(local_path=str(repo)))
    skill = repo / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Demo\n", encoding="utf-8")

    plan = build_resource_commit_plan(config=cfg)

    assert plan.blocked is True
    assert any(item.path == "<machine-local:state directory>" for item in plan.blocked_paths)
    with pytest.raises(ResourceCommitBlocked):
        commit_resource_changes(message="cc-port: unsafe state", config=cfg)
    assert not state_root.exists()


@pytest.mark.parametrize("private_kind", ["config", "profile"])
def test_commit_plan_rejects_config_or_profile_target_inside_resource_repo(
    tmp_path: Path,
    private_kind: str,
) -> None:
    repo = _repo(tmp_path)
    cfg = Config(resources=ResourcesConfig(local_path=str(repo)))
    if private_kind == "config":
        cfg.source_path = repo / "instructions" / "config.toml"
        expected = "<machine-local:config file>"
    else:
        cfg.platforms = PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="claude-windows",
                    tool_id="claude-code",
                    instructions_path=str(repo / "instructions" / "CLAUDE.md"),
                )
            ]
        )
        expected = "<machine-local:platform instructions_path>"
    skill = repo / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Demo\n", encoding="utf-8")

    plan = build_resource_commit_plan(config=cfg)

    assert plan.blocked is True
    assert any(item.path == expected for item in plan.blocked_paths)


def test_memory_markdown_in_generic_excluded_directories_round_trips_commit_guard(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    cfg = Config(resources=ResourcesConfig(local_path=str(repo)))
    (repo / "registry.yaml").write_text(
        "version: 1\nresources:\n"
        "- kind: memory\n  name: shared\n  path: memories/shared\n",
        encoding="utf-8",
    )
    for relative in ("cache/topic.md", "build/notes.md", "tmp/context.md"):
        target = repo / "memories" / "shared" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {relative}\n", encoding="utf-8")

    plan = build_resource_commit_plan(config=cfg)

    assert plan.blocked is False
    assert plan.secret_findings == []
    commit_resource_changes(message="cc-port: add exact memory", config=cfg)
    validate_outgoing_resource_commits(repo, base_commit=base, config=cfg)


def test_memory_excluded_directory_markdown_is_still_secret_scanned(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    cfg = Config(resources=ResourcesConfig(local_path=str(repo)))
    (repo / "registry.yaml").write_text(
        "version: 1\nresources:\n"
        "- kind: memory\n  name: shared\n  path: memories/shared\n",
        encoding="utf-8",
    )
    secret = repo / "memories" / "shared" / "cache" / "token.md"
    secret.parent.mkdir(parents=True)
    secret.write_text("token: ghp_1234567890abcdefghijkl\n", encoding="utf-8")

    plan = build_resource_commit_plan(config=cfg)

    assert plan.blocked is True
    assert [item.path for item in plan.secret_findings] == [
        "memories/shared/cache/token.md"
    ]


def test_memory_non_markdown_file_is_not_allowed_by_exact_tree_exception(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    cfg = Config(resources=ResourcesConfig(local_path=str(repo)))
    (repo / "registry.yaml").write_text(
        "version: 1\nresources:\n"
        "- kind: memory\n  name: shared\n  path: memories/shared\n",
        encoding="utf-8",
    )
    invalid = repo / "memories" / "shared" / "cache" / "state.json"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("{}\n", encoding="utf-8")

    plan = build_resource_commit_plan(config=cfg)

    assert plan.blocked is True
    assert any(item.path == "memories/shared/cache/state.json" for item in plan.blocked_paths)


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
