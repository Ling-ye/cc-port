from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from cc_port.core.config import Config, GitConfig, InstallConfig, ResourcesConfig
from cc_port.core.models import Registry, RegistryResource
from cc_port.core.registry import canonical_registry_text, load_registry, save_registry
from cc_port.infrastructure import git_ops
from cc_port.services import registry_audit
from cc_port.services.registry_audit import (
    RegistryRepairChoice,
    apply_registry_repair,
    audit_registry_root,
    build_registry_repair_plan,
)

_GIT_RUNTIME = git_ops.discover_git_executable(configured="")
GIT = _GIT_RUNTIME.path


def _skill(path: Path, *, body: str = "body") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo resource\n---\n" + body + "\n",
        encoding="utf-8",
    )


def _git(path: Path, *args: str) -> str:
    assert GIT is not None
    result = subprocess.run(
        [str(GIT), *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _config(tmp_path: Path, repo_url: str) -> Config:
    assert GIT is not None
    return Config(
        git=GitConfig(executable=str(GIT)),
        install=InstallConfig(target=str(tmp_path / "install")),
        resources=ResourcesConfig(
            repo_url=repo_url,
            local_path=str(tmp_path / "local"),
            branch="main",
        ),
    )


def test_canonical_registry_round_trip_preserves_unknown_portable_data(tmp_path: Path) -> None:
    registry = Registry(
        resources=[
            RegistryResource.model_validate(
                {
                    "kind": "dataset",
                    "name": "records",
                    "source": {
                        "type": "future-hub",
                        "locator": "example/records",
                        "revision": "stable",
                    },
                    "consumer": {"format": "parquet"},
                }
            ),
            RegistryResource(kind="skill", name="demo", path="skills/demo"),
        ]
    )
    path = save_registry(registry, tmp_path / "registry.yaml")
    first = path.read_bytes()

    loaded = load_registry(path)
    save_registry(loaded, path)

    assert path.read_bytes() == first
    assert canonical_registry_text(loaded).encode() == first
    unknown = next(item for item in loaded.resources if item.kind == "dataset")
    assert unknown.model_extra == {"consumer": {"format": "parquet"}}
    assert unknown.source is not None and unknown.source.type == "future-hub"


def test_audit_discovers_direct_file_and_directory_resources_for_all_known_kinds(
    tmp_path: Path,
) -> None:
    save_registry(Registry(), tmp_path / "registry.yaml")
    _skill(tmp_path / "skills" / "demo")
    (tmp_path / "mcp" / "server").mkdir(parents=True)
    (tmp_path / "mcp" / "server" / "mcp.json").write_text(
        json.dumps({"command": "demo"}),
        encoding="utf-8",
    )
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "policy.md").write_text("rule\n", encoding="utf-8")
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "ask.md").write_text("prompt\n", encoding="utf-8")
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "tool.js").write_text("export default {}\n", encoding="utf-8")

    plan = audit_registry_root(tmp_path, remote_commit="abc")

    additions = [issue for issue in plan.issues if issue.code == "unregistered-resource"]
    assert {issue.kind for issue in additions} == {"skill", "mcp", "rule", "prompt", "plugin"}
    assert plan.executable_count == 5
    assert plan.blocked_count == 0
    assert plan.registry_status == "issues"


def test_candidate_with_empty_slug_requires_an_explicit_safe_name(tmp_path: Path) -> None:
    save_registry(Registry(), tmp_path / "registry.yaml")
    _skill(tmp_path / "skills" / "---")

    blocked = audit_registry_root(tmp_path)
    issue = next(issue for issue in blocked.issues if issue.code == "invalid-resource-name")
    repaired = audit_registry_root(
        tmp_path,
        choices=[
            RegistryRepairChoice(
                issue_id=issue.id,
                action="add",
                name="manual-name",
            )
        ],
    )

    assert blocked.blocked_count == 1
    assert issue.name == ""
    assert repaired.blocked_count == 0
    assert "name: manual-name" in repaired.resulting_registry_text
    assert "path: skills/---" in repaired.resulting_registry_text


def test_registered_content_change_does_not_change_registry(tmp_path: Path) -> None:
    _skill(tmp_path / "skills" / "demo", body="first")
    save_registry(
        Registry(resources=[RegistryResource(kind="skill", name="demo", path="skills/demo")]),
        tmp_path / "registry.yaml",
    )
    first = audit_registry_root(tmp_path, remote_commit="one")
    (tmp_path / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Changed\n---\nsecond\n",
        encoding="utf-8",
    )
    second = audit_registry_root(tmp_path, remote_commit="one")

    assert first.registry_status == second.registry_status == "healthy"
    assert first.registry_diff == second.registry_diff == ""


def test_missing_registered_path_defaults_to_registry_entry_removal(tmp_path: Path) -> None:
    save_registry(
        Registry(resources=[RegistryResource(kind="skill", name="gone", path="skills/gone")]),
        tmp_path / "registry.yaml",
    )

    plan = audit_registry_root(tmp_path)

    issue = next(issue for issue in plan.issues if issue.code == "missing-resource")
    choice = next(choice for choice in plan.choices if choice.issue_id == issue.id)
    assert choice.action == "remove"
    assert "resources: []" in plan.resulting_registry_text


@pytest.mark.parametrize(
    ("content", "status", "code"),
    [
        (None, "missing", "missing-registry"),
        ("version: [\n", "invalid", "invalid-yaml"),
    ],
)
def test_missing_or_invalid_registry_is_report_only(
    tmp_path: Path,
    content: str | None,
    status: str,
    code: str,
) -> None:
    if content is not None:
        (tmp_path / "registry.yaml").write_text(content, encoding="utf-8")

    plan = audit_registry_root(tmp_path)

    assert plan.registry_status == status
    assert plan.repairable is False
    assert plan.registry_diff == ""
    assert [issue.code for issue in plan.issues] == [code]


def test_legacy_v7_rebuild_reports_discarded_count(tmp_path: Path) -> None:
    _skill(tmp_path / "skills" / "demo")
    (tmp_path / "registry.yaml").write_text(
        "version: 7\nitems:\n"
        "- kind: skill\n  name: old\n  path: skills/missing\n"
        "- kind: plugin\n  name: reference\n  repo: https://example.invalid/plugin\n",
        encoding="utf-8",
    )

    plan = audit_registry_root(tmp_path)

    assert plan.registry_status == "legacy"
    assert plan.legacy_item_count == 2
    assert plan.rebuilt_item_count == 1
    assert plan.dropped_item_count == 2
    assert "name: demo" in plan.resulting_registry_text
    assert "reference" not in plan.resulting_registry_text


def test_legacy_v7_drop_count_uses_identity_not_entity_count(tmp_path: Path) -> None:
    _skill(tmp_path / "skills" / "demo")
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "extra.md").write_text("rule\n", encoding="utf-8")
    (tmp_path / "registry.yaml").write_text(
        "version: 7\nitems:\n"
        "- kind: skill\n  name: demo\n  path: skills/demo\n"
        "- kind: plugin\n  name: reference\n  repo: https://example.invalid/plugin\n",
        encoding="utf-8",
    )

    plan = audit_registry_root(tmp_path)

    assert plan.legacy_item_count == 2
    assert plan.rebuilt_item_count == 2
    assert plan.dropped_item_count == 1


def test_duplicate_identity_requires_explicit_entry_selection(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "registry.yaml").write_text(
        "version: 1\nresources:\n"
        "- kind: dataset\n  name: same\n  path: a\n"
        "- kind: dataset\n  name: same\n  path: b\n",
        encoding="utf-8",
    )
    blocked = audit_registry_root(tmp_path)
    issue = next(issue for issue in blocked.issues if issue.code == "duplicate-key")

    selected = audit_registry_root(
        tmp_path,
        choices=[
            RegistryRepairChoice(
                issue_id=issue.id,
                action="select-entry",
                name="1",
            )
        ],
    )

    assert blocked.blocked_count == 1
    assert selected.blocked_count == 0
    assert selected.executable_count == 1
    assert "path: b" in selected.resulting_registry_text
    assert "path: a" not in selected.resulting_registry_text


def test_plan_hash_changes_with_commit_content_and_choices(tmp_path: Path) -> None:
    save_registry(Registry(), tmp_path / "registry.yaml")
    _skill(tmp_path / "skills" / "demo", body="first")
    initial = audit_registry_root(tmp_path, remote_commit="one")
    issue = next(issue for issue in initial.issues if issue.code == "unregistered-resource")
    kept = audit_registry_root(
        tmp_path,
        remote_commit="one",
        choices=[RegistryRepairChoice(issue_id=issue.id, action="keep")],
    )
    different_commit = audit_registry_root(tmp_path, remote_commit="two")
    _skill(tmp_path / "skills" / "demo", body="second")
    different_content = audit_registry_root(tmp_path, remote_commit="one")

    assert len({
        initial.plan_hash,
        kept.plan_hash,
        different_commit.plan_hash,
        different_content.plan_hash,
    }) == 4


def test_secret_like_source_is_blocked_without_echoing_secret(tmp_path: Path) -> None:
    secret = "ghp_1234567890abcdefghijkl"
    (tmp_path / "registry.yaml").write_text(
        "version: 1\nresources:\n"
        "- kind: skill\n  name: demo\n  source:\n"
        "    type: git\n"
        f"    locator: https://user:{secret}@example.invalid/repo\n",
        encoding="utf-8",
    )

    plan = audit_registry_root(tmp_path)
    serialized = json.dumps(asdict(plan), ensure_ascii=False, default=str)

    assert plan.blocked_count == 1
    assert plan.issues[0].code == "invalid-source"
    assert secret not in serialized

    removed = audit_registry_root(
        tmp_path,
        choices=[
            RegistryRepairChoice(
                issue_id=plan.issues[0].id,
                action="remove",
            )
        ],
    )
    assert removed.blocked_count == 0
    assert "resources: []" in removed.resulting_registry_text
    assert secret not in json.dumps(asdict(removed), ensure_ascii=False, default=str)


def test_unsafe_registered_path_blocks_until_entry_is_explicitly_removed(
    tmp_path: Path,
) -> None:
    (tmp_path / "registry.yaml").write_text(
        "version: 1\nresources:\n"
        "- kind: skill\n  name: escape\n  path: ../outside\n",
        encoding="utf-8",
    )

    blocked = audit_registry_root(tmp_path)
    issue = next(issue for issue in blocked.issues if issue.code == "unsafe-path")
    removed = audit_registry_root(
        tmp_path,
        choices=[RegistryRepairChoice(issue_id=issue.id, action="remove")],
    )

    assert blocked.blocked_count == 1
    assert issue.default_action == "keep"
    assert removed.blocked_count == 0
    assert "resources: []" in removed.resulting_registry_text


def test_registered_path_excluded_by_resource_policy_is_unsafe(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("repository control\n", encoding="utf-8")
    save_registry(
        Registry(
            resources=[
                RegistryResource(
                    kind="dataset",
                    name="control",
                    path=".git/config",
                )
            ]
        ),
        tmp_path / "registry.yaml",
    )

    plan = audit_registry_root(tmp_path)

    issue = next(issue for issue in plan.issues if issue.code == "unsafe-path")
    assert issue.path == ".git/config"
    assert issue.blocking is True


def test_unregistered_symlink_candidate_blocks_repair_without_stopping_scan(
    tmp_path: Path,
) -> None:
    save_registry(Registry(), tmp_path / "registry.yaml")
    _skill(tmp_path / "skills" / "safe")
    outside = tmp_path / "outside"
    _skill(outside)
    try:
        (tmp_path / "skills" / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    plan = audit_registry_root(tmp_path)

    assert any(
        issue.code == "unregistered-resource" and issue.path == "skills/safe"
        for issue in plan.issues
    )
    unsafe = next(issue for issue in plan.issues if issue.path == "skills/linked")
    assert unsafe.code == "unsafe-link"
    assert unsafe.blocking is True
    assert plan.blocked_count == 1


@pytest.mark.skipif(GIT is None, reason="Git is required for registry repair integration")
def test_apply_commits_only_registry_yaml_with_fixed_title(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    save_registry(Registry(), seed / "registry.yaml")
    _skill(seed / "skills" / "demo")
    (seed / "README.md").write_text("portable resources\n", encoding="utf-8")
    _git(seed, "add", "registry.yaml", "skills/demo/SKILL.md", "README.md")
    _git(seed, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "seed")
    bare = tmp_path / "remote.git"
    subprocess.run(
        [str(GIT), "clone", "--bare", str(seed), str(bare)],
        check=True,
        capture_output=True,
        text=True,
    )
    cfg = _config(tmp_path, str(bare))
    plan = build_registry_repair_plan(config=cfg)

    result = apply_registry_repair(
        expected_plan_hash=plan.plan_hash,
        config=cfg,
        choices=plan.choices,
    )

    assert result.status == "succeeded"
    verify = tmp_path / "verify"
    subprocess.run(
        [str(GIT), "clone", "--branch", "main", str(bare), str(verify)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert _git(verify, "log", "-1", "--format=%s") == "修复资源索引"
    assert _git(verify, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == "registry.yaml"
    assert load_registry(verify / "registry.yaml").get("demo", "skill") is not None
    assert (verify / "README.md").read_text(encoding="utf-8") == "portable resources\n"


@pytest.mark.skipif(GIT is None, reason="Git is required for registry repair integration")
def test_apply_returns_stale_when_remote_advances_before_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    save_registry(Registry(), seed / "registry.yaml")
    _skill(seed / "skills" / "demo")
    _git(seed, "add", "registry.yaml", "skills/demo/SKILL.md")
    _git(
        seed,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "seed",
    )
    bare = tmp_path / "remote.git"
    subprocess.run(
        [str(GIT), "clone", "--bare", str(seed), str(bare)],
        check=True,
        capture_output=True,
        text=True,
    )
    cfg = _config(tmp_path, str(bare))
    plan = build_registry_repair_plan(config=cfg)
    original_push = registry_audit.git_ops.push
    injected = False

    def race_then_push(*args: object, **kwargs: object) -> object:
        nonlocal injected
        if not injected:
            injected = True
            racer = tmp_path / "racer"
            subprocess.run(
                [str(GIT), "clone", "--branch", "main", str(bare), str(racer)],
                check=True,
                capture_output=True,
                text=True,
            )
            (racer / "race.txt").write_text("won race\n", encoding="utf-8")
            _git(racer, "add", "race.txt")
            _git(
                racer,
                "-c",
                "user.name=Racer",
                "-c",
                "user.email=racer@example.invalid",
                "commit",
                "-m",
                "race",
            )
            _git(racer, "push", "origin", "main")
        return original_push(*args, **kwargs)

    monkeypatch.setattr(registry_audit.git_ops, "push", race_then_push)

    result = apply_registry_repair(
        expected_plan_hash=plan.plan_hash,
        config=cfg,
        choices=plan.choices,
    )

    assert result.status == "stale"
    assert result.stale_plan is not None
    assert result.stale_plan.remote_commit != plan.remote_commit
    verify = tmp_path / "verify-race"
    subprocess.run(
        [str(GIT), "clone", "--branch", "main", str(bare), str(verify)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert _git(verify, "log", "-1", "--format=%s") == "race"
    assert (verify / "race.txt").read_text(encoding="utf-8") == "won race\n"
    assert load_registry(verify / "registry.yaml").resources == []
