from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lpm.core.config import Config, ResourcesConfig
from lpm.infrastructure.git_ops import GitError
from lpm.services.resource_sync import (
    SYNC_PLAN_SCHEMA_VERSION,
    apply_resource_sync_plan,
    build_resource_sync_plan,
    cancel_resource_sync_plan,
    cleanup_stale_resource_sync_plan,
    list_stale_resource_sync_plans,
    load_resource_sync_plan,
    push_resource_sync,
    resolve_resource_sync_plan,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def test_stale_sync_worktree_requires_explicit_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("LPM_STATE_HOME", str(state))
    operation_id = "a" * 32
    operation_dir = state / "sync" / operation_id
    worktree = operation_dir / "worktree"
    repo = tmp_path / "repo"
    operation_dir.mkdir(parents=True)
    worktree.mkdir()
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    old = datetime.now(timezone.utc) - timedelta(hours=30)
    (operation_dir / "plan.json").write_text(
        json.dumps(
            {
                "schema_version": SYNC_PLAN_SCHEMA_VERSION,
                "operation_id": operation_id,
                "repo_path": str(repo),
                "repo_common_dir": str((repo / ".git").resolve()),
                "repo_remote_url": "",
                "branch": "main",
                "status": "ready",
                "local_commit": "local",
                "remote_commit": "remote",
                "merge_base": "base",
                "ahead": 1,
                "behind": 1,
                "has_worktree": True,
                "merge_commit": "merge",
                "conflicts": [],
                "detail": "",
                "created_at": old.isoformat(),
                "updated_at": old.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []
    monkeypatch.setattr(
        "lpm.services.resource_sync.git_ops.worktree_remove",
        lambda *_args, **_kwargs: calls.append("remove"),
    )
    monkeypatch.setattr(
        "lpm.services.resource_sync.git_ops.worktree_prune",
        lambda *_args, **_kwargs: calls.append("prune"),
    )

    stale = list_stale_resource_sync_plans(min_age_hours=24)
    assert [item.operation_id for item in stale] == [operation_id]
    assert worktree.exists()

    cleaned = cleanup_stale_resource_sync_plan(operation_id)

    assert cleaned.status == "abandoned"
    assert not worktree.exists()
    assert calls == ["remove", "prune"]
    assert load_resource_sync_plan(operation_id).status == "abandoned"


def test_sync_plan_ignores_tampered_worktree_path_and_keeps_external_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("LPM_STATE_HOME", str(state))
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    seed = _clone(remote, tmp_path / "seed")
    _write_resource(seed, "# Base\n")
    _commit(seed, "base")
    _git(seed, "push", "-u", "origin", "main")
    machine_a = _clone(remote, tmp_path / "machine-a")
    machine_b = _clone(remote, tmp_path / "machine-b")
    cfg_b = Config(
        resources=ResourcesConfig(
            repo_url=str(remote),
            local_path=str(machine_b),
            branch="main",
        )
    )
    _write_resource(machine_a, "# Remote\n")
    _commit(machine_a, "remote")
    _git(machine_a, "push")
    _write_resource(machine_b, "# Local\n")
    _commit(machine_b, "local")
    plan = build_resource_sync_plan(config=cfg_b)
    assert plan.status == "conflict"

    external = tmp_path / "must-not-delete"
    external.mkdir()
    (external / "sentinel.txt").write_text("keep", encoding="utf-8")
    plan_path = state / "sync" / plan.operation_id / "plan.json"
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["worktree_path"] = str(external)
    plan_path.write_text(json.dumps(payload), encoding="utf-8")

    cancel_resource_sync_plan(plan.operation_id, config=cfg_b)

    assert (external / "sentinel.txt").read_text(encoding="utf-8") == "keep"


def test_sync_plan_rejects_mismatched_operation_id(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("LPM_STATE_HOME", str(state))
    operation_id = "b" * 32
    operation_dir = state / "sync" / operation_id
    operation_dir.mkdir(parents=True)
    (operation_dir / "plan.json").write_text(
        json.dumps(
            {
                "schema_version": SYNC_PLAN_SCHEMA_VERSION,
                "operation_id": "c" * 32,
                "repo_path": str(tmp_path / "repo"),
                "repo_common_dir": "",
                "repo_remote_url": "",
                "branch": "main",
                "status": "clean",
                "local_commit": None,
                "remote_commit": None,
                "merge_base": None,
                "ahead": 0,
                "behind": 0,
                "has_worktree": False,
                "conflicts": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="operation id"):
        load_resource_sync_plan(operation_id)


def test_resource_sync_fast_forward_and_three_way_conflict(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LPM_STATE_HOME", str(tmp_path / "state"))
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    seed = _clone(remote, tmp_path / "seed")
    _write_resource(seed, "# Base\n")
    _commit(seed, "base")
    _git(seed, "push", "-u", "origin", "main")

    machine_a = _clone(remote, tmp_path / "machine-a")
    machine_b = _clone(remote, tmp_path / "machine-b")
    cfg_b = Config(
        resources=ResourcesConfig(
            repo_url=str(remote),
            local_path=str(machine_b),
            branch="main",
        )
    )

    _write_resource(machine_a, "# From A\n")
    _commit(machine_a, "machine a")
    _git(machine_a, "push")

    behind = build_resource_sync_plan(config=cfg_b)
    assert behind.status == "behind"
    applied = apply_resource_sync_plan(behind.operation_id)
    assert applied.status == "applied"
    assert _skill_text(machine_b) == "# From A\n"

    _write_resource(machine_a, "# Remote conflict\n")
    _commit(machine_a, "remote conflict")
    _git(machine_a, "push")
    _write_resource(machine_b, "# Local conflict\n")
    _commit(machine_b, "local conflict")

    conflict = build_resource_sync_plan(config=cfg_b)

    assert conflict.status == "conflict"
    assert any(item.id == "resource:demo" for item in conflict.conflicts)
    with pytest.raises(GitError, match="pending synchronization"):
        build_resource_sync_plan(config=cfg_b)
    worktree = conflict.worktree_path
    cancelled = cancel_resource_sync_plan(conflict.operation_id)
    assert cancelled.status == "cancelled"
    assert worktree is not None and not worktree.exists()

    conflict = build_resource_sync_plan(config=cfg_b)
    ready = resolve_resource_sync_plan(
        conflict.operation_id,
        {"resource:demo": "incoming"},
    )
    assert ready.status == "ready"
    final = apply_resource_sync_plan(ready.operation_id)
    assert final.status == "applied"
    assert _skill_text(machine_b) == "# Remote conflict\n"
    assert int(_git(machine_b, "rev-list", "--count", "HEAD").stdout.strip()) >= 4


def test_resource_sync_auto_merges_non_conflicting_commits(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LPM_STATE_HOME", str(tmp_path / "state"))
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    seed = _clone(remote, tmp_path / "seed")
    _write_resource(seed, "# Base\n")
    _commit(seed, "base")
    _git(seed, "push", "-u", "origin", "main")
    machine_a = _clone(remote, tmp_path / "machine-a")
    machine_b = _clone(remote, tmp_path / "machine-b")
    cfg_b = Config(
        resources=ResourcesConfig(
            repo_url=str(remote),
            local_path=str(machine_b),
            branch="main",
        )
    )

    from_a = machine_a / "skills" / "from-a" / "SKILL.md"
    from_a.parent.mkdir(parents=True)
    from_a.write_text("a\n", encoding="utf-8")
    _commit(machine_a, "from a")
    _git(machine_a, "push")
    from_b = machine_b / "skills" / "from-b" / "SKILL.md"
    from_b.parent.mkdir(parents=True)
    from_b.write_text("b\n", encoding="utf-8")
    _commit(machine_b, "from b")

    plan = build_resource_sync_plan(config=cfg_b)

    assert plan.status == "ready"
    assert plan.merge_commit
    apply_resource_sync_plan(plan.operation_id)
    assert (machine_b / "skills" / "from-a" / "SKILL.md").is_file()
    assert (machine_b / "skills" / "from-b" / "SKILL.md").is_file()

    remote_race = machine_a / "skills" / "remote-race" / "SKILL.md"
    remote_race.parent.mkdir(parents=True)
    remote_race.write_text("race\n", encoding="utf-8")
    _commit(machine_a, "remote race")
    _git(machine_a, "push")

    with pytest.raises(GitError, match="Remote resource history changed"):
        push_resource_sync(config=cfg_b)

    verifier = _clone(remote, tmp_path / "verifier")
    assert (verifier / "skills" / "remote-race" / "SKILL.md").is_file()


def test_resource_sync_handles_unborn_clone_and_empty_remote(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LPM_STATE_HOME", str(tmp_path / "state"))
    seeded_remote = tmp_path / "seeded.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(seeded_remote))
    seed = _clone(seeded_remote, tmp_path / "seed")
    _write_resource(seed, "# Seeded\n")
    _commit(seed, "seed")
    _git(seed, "push", "-u", "origin", "main")

    unborn = tmp_path / "unborn"
    unborn.mkdir()
    _git(unborn, "init", "-b", "main")
    _git(unborn, "remote", "add", "origin", str(seeded_remote))
    unborn_cfg = Config(
        resources=ResourcesConfig(
            repo_url=str(seeded_remote),
            local_path=str(unborn),
            branch="main",
        )
    )

    plan = build_resource_sync_plan(config=unborn_cfg)
    assert plan.status == "unborn"
    apply_resource_sync_plan(plan.operation_id)
    assert _skill_text(unborn) == "# Seeded\n"
    _git(unborn, "checkout", "-b", "feature")
    wrong_branch = build_resource_sync_plan(config=unborn_cfg)
    assert wrong_branch.status == "wrong-branch"
    with pytest.raises(GitError, match="Configured branch"):
        push_resource_sync(config=unborn_cfg)

    empty_remote = tmp_path / "empty.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(empty_remote))
    first_machine = tmp_path / "first-machine"
    first_machine.mkdir()
    _git(first_machine, "init", "-b", "main")
    _git(first_machine, "remote", "add", "origin", str(empty_remote))
    _write_resource(first_machine, "# First\n")
    _commit(first_machine, "first")
    first_cfg = Config(
        resources=ResourcesConfig(
            repo_url=str(empty_remote),
            local_path=str(first_machine),
            branch="main",
        )
    )

    pushed = push_resource_sync(config=first_cfg)
    assert pushed.status == "clean"
    verifier = _clone(empty_remote, tmp_path / "empty-verifier")
    assert _skill_text(verifier) == "# First\n"


def _clone(remote: Path, destination: Path) -> Path:
    _git(destination.parent, "clone", str(remote), str(destination))
    if _git(destination, "show-ref", "--verify", "refs/remotes/origin/main", check=False).returncode == 0:
        _git(destination, "checkout", "-B", "main", "origin/main")
    else:
        _git(destination, "checkout", "--orphan", "main")
    return destination


def _write_resource(repo: Path, text: str) -> None:
    skill = repo / "skills" / "demo"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(text, encoding="utf-8")
    (repo / "registry.yaml").write_text(
        "\n".join(
            [
                "version: 5",
                "items:",
                "  - name: demo",
                "    kind: skill",
                "    source: local",
                "    path: skills/demo",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _skill_text(repo: Path) -> str:
    return (repo / "skills" / "demo" / "SKILL.md").read_text(encoding="utf-8")


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=LPM Test",
        "-c",
        "user.email=lpm@example.test",
        "commit",
        "-m",
        message,
    )


def _git(
    cwd: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )
