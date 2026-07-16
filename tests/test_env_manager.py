from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from lpm.core.config import Config, InstallConfig, ResourcesConfig
from lpm.core.models import Registry, RegistryItem
from lpm.core.ownership import write_managed_marker
from lpm.core.platforms import PlatformProfile, PlatformsConfig
from lpm.core.registry import save_registry
from lpm.infrastructure import git_ops
from lpm.services.env_manager import (
    DeploymentTransactionError,
    EnvSecretScanError,
    apply_env_import,
    apply_env_pull,
    build_deploy_plan,
    build_env_import_diff,
    build_env_pull_diff,
    capture_environment,
    deploy_environment,
    discover_environment,
    export_environment_snapshot,
)
from lpm.services.installer import SyncAction, SyncResult
from lpm.services.operation_state import load_operation

MCP_SECRET = "ghp_cursor_secret_1234567890"
SKILL_SECRET = "sk_test_skill_secret_123456"


def test_capture_sanitizes_mcp_and_text_resources(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    source_home = _source_home(tmp_path)
    resource_repo = tmp_path / "resource-repo"
    cfg = _config(resource_repo, tmp_path / "target-home")

    discovery = discover_environment(home=source_home, registry_path_override=resource_repo / "registry.yaml")

    assert any(tool.id == "cursor" and tool.detected for tool in discovery.tools)
    assert {resource.kind for resource in discovery.resources} == {"skill", "prompt", "rule"}
    assert [server.name for server in discovery.mcp_servers] == ["github"]

    result = capture_environment(config=cfg, home=source_home)

    captured_names = {item.name for item in result.captured}
    assert "cursor-skill-demo-skill" in captured_names
    assert "cursor-mcp-github" in captured_names
    assert result.profile_path == resource_repo / "profiles" / "default.yaml"
    assert result.secrets_path == resource_repo / "secrets.example.yaml"

    registry_text = (resource_repo / "registry.yaml").read_text(encoding="utf-8")
    assert "${GITHUB_TOKEN}" in registry_text
    assert MCP_SECRET not in registry_text

    skill_text = (resource_repo / "resources" / "skills" / "cursor" / "cursor-skill-demo-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "${SECRET_VALUE}" in skill_text
    assert SKILL_SECRET not in skill_text
    captured_skill = resource_repo / "resources" / "skills" / "cursor" / "cursor-skill-demo-skill"
    assert not (captured_skill / ".env").exists()
    assert (captured_skill / ".env.example").is_file()

    secrets_text = result.secrets_path.read_text(encoding="utf-8")
    assert "GITHUB_TOKEN" in secrets_text
    assert MCP_SECRET not in secrets_text
    _assert_secret_absent(resource_repo, MCP_SECRET)
    _assert_secret_absent(resource_repo, SKILL_SECRET)

    snapshot = export_environment_snapshot(tmp_path / "snapshot.zip", config=cfg)
    assert snapshot.is_file()
    with zipfile.ZipFile(snapshot) as archive:
        names = set(archive.namelist())
        assert "registry.yaml" in names
        assert "profiles/default.yaml" in names
        assert "secrets.example.yaml" in names
        assert "resources/mcp/cursor/cursor-mcp-github/mcp.json" in names
        for name in names:
            data = archive.read(name)
            assert MCP_SECRET.encode() not in data
            assert SKILL_SECRET.encode() not in data


def test_deploy_from_capture_creates_targets_and_keeps_placeholders(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    source_home = _source_home(tmp_path)
    resource_repo = tmp_path / "resource-repo"
    target_home = tmp_path / "target-home"
    capture_environment(config=_config(resource_repo, tmp_path / "unused-target"), home=source_home)
    cfg = _config(resource_repo, target_home)

    plan = build_deploy_plan(config=cfg)

    actions = {(item.name, item.kind): item.action for item in plan.items}
    assert actions[("cursor-skill-demo-skill", "skill")] == "create"
    assert actions[("cursor-mcp-github", "mcp")] == "create"
    assert [secret.name for secret in plan.missing_secrets] == ["GITHUB_TOKEN"]

    result = deploy_environment(config=cfg)

    assert result.dry_run is False
    assert result.status == "succeeded"
    operation = load_operation(result.operation_id)
    assert operation.status == "succeeded"
    assert operation.targets
    assert all(target.verified for target in operation.targets)
    skill_target = target_home / ".cursor" / "skills" / "cursor-skill-demo-skill"
    assert (skill_target / "SKILL.md").is_file()
    assert (skill_target / ".lpm-managed.json").is_file()

    mcp_text = (target_home / ".cursor" / "mcp.json").read_text(encoding="utf-8")
    assert "${GITHUB_TOKEN}" in mcp_text
    assert MCP_SECRET not in mcp_text

    repeat_plan = build_deploy_plan(config=cfg)
    repeat_actions = {(item.name, item.kind): item.action for item in repeat_plan.items}
    assert repeat_actions[("cursor-skill-demo-skill", "skill")] == "skip"
    assert repeat_actions[("cursor-mcp-github", "mcp")] == "skip"
    assert deploy_environment(config=cfg).status == "succeeded"


def test_deploy_skips_conflicting_platform_target_but_updates_safe_target(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    source_home = _source_home(tmp_path)
    resource_repo = tmp_path / "resource-repo"
    target_home = tmp_path / "target-home"
    capture_environment(config=_config(resource_repo, tmp_path / "unused-target"), home=source_home)

    windsurf_skill = target_home / ".windsurf" / "skills" / "cursor-skill-demo-skill"
    windsurf_skill.mkdir(parents=True)
    (windsurf_skill / "SKILL.md").write_text("local windsurf copy\n", encoding="utf-8")
    cfg = _config(resource_repo, target_home, include_windsurf=True)

    plan = build_deploy_plan(config=cfg)
    skill_actions = {
        item.platform: item.action
        for item in plan.items
        if item.name == "cursor-skill-demo-skill" and item.kind == "skill"
    }

    assert skill_actions == {"cursor": "create", "windsurf": "conflict"}

    deploy_environment(config=cfg)

    assert (target_home / ".cursor" / "skills" / "cursor-skill-demo-skill" / "SKILL.md").is_file()
    assert (windsurf_skill / "SKILL.md").read_text(encoding="utf-8") == "local windsurf copy\n"
    assert not (windsurf_skill / ".lpm-managed.json").exists()

    deploy_environment(
        config=cfg,
        force=True,
        names=["cursor-skill-demo-skill"],
    )

    assert "local windsurf copy" not in (
        windsurf_skill / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert (windsurf_skill / ".lpm-managed.json").is_file()


def test_deploy_plan_respects_resource_platform_allowlist(tmp_path: Path) -> None:
    resource_repo = tmp_path / "resource-repo"
    skill = resource_repo / "skills" / "cursor-only"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Cursor only\n", encoding="utf-8")
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="cursor-only",
                    kind="skill",
                    source="local",
                    path="skills/cursor-only",
                    platforms=["cursor"],
                )
            ]
        ),
        resource_repo / "registry.yaml",
    )
    target_home = tmp_path / "target-home"
    cfg = Config(
        install=InstallConfig(target=str(target_home / ".lpm-cache")),
        resources=ResourcesConfig(local_path=str(resource_repo)),
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="cursor",
                    enabled=True,
                    skills_dir=str(target_home / ".cursor" / "skills"),
                ),
                PlatformProfile(
                    name="codex",
                    enabled=True,
                    skills_dir=str(target_home / ".codex" / "skills"),
                ),
            ]
        ),
    )

    plan = build_deploy_plan(config=cfg)

    assert [(item.platform, item.action) for item in plan.items] == [("cursor", "create")]


def test_deploy_backup_is_outside_resource_repo(tmp_path: Path, monkeypatch) -> None:
    resource_repo = tmp_path / "resource-repo"
    skill = resource_repo / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Updated\n", encoding="utf-8")
    entry = RegistryItem(
        name="demo",
        kind="skill",
        source="local",
        path="skills/demo",
        platforms=["cursor"],
    )
    save_registry(Registry(items=[entry]), resource_repo / "registry.yaml")
    target = tmp_path / "target-home" / ".cursor" / "skills" / "demo"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("# Previous\n", encoding="utf-8")
    write_managed_marker(target, entry, platform="cursor")
    state_home = tmp_path / "state"
    monkeypatch.setenv("LPM_STATE_HOME", str(state_home))
    cfg = Config(
        install=InstallConfig(target=str(tmp_path / "cache")),
        resources=ResourcesConfig(local_path=str(resource_repo)),
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="cursor",
                    enabled=True,
                    skills_dir=str(target.parent),
                )
            ]
        ),
    )

    result = deploy_environment(config=cfg)

    assert result.backup_root is not None
    assert state_home in result.backup_root.parents
    assert resource_repo not in result.backup_root.parents
    assert not (resource_repo / ".lpm-backups").exists()


def test_deploy_failure_rolls_back_all_attempted_targets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    resource_repo = tmp_path / "resource-repo"
    entries = [
        RegistryItem(
            name="a-first",
            kind="skill",
            source="local",
            path="skills/a-first",
            platforms=["cursor"],
        ),
        RegistryItem(
            name="b-second",
            kind="skill",
            source="local",
            path="skills/b-second",
            platforms=["cursor"],
        ),
    ]
    for entry in entries:
        source = resource_repo / entry.path
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(f"# {entry.name}\n", encoding="utf-8")
    save_registry(Registry(items=entries), resource_repo / "registry.yaml")

    target_root = tmp_path / "target-home" / ".cursor" / "skills"
    first_target = target_root / "a-first"
    second_target = target_root / "b-second"
    first_target.mkdir(parents=True)
    (first_target / "SKILL.md").write_text("# Previous\n", encoding="utf-8")
    write_managed_marker(first_target, entries[0], platform="cursor")
    state_home = tmp_path / "state"
    monkeypatch.setenv("LPM_STATE_HOME", str(state_home))
    cfg = Config(
        install=InstallConfig(target=str(tmp_path / "cache")),
        resources=ResourcesConfig(local_path=str(resource_repo)),
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="cursor",
                    enabled=True,
                    skills_dir=str(target_root),
                )
            ]
        ),
    )

    def fake_sync_all(**kwargs):
        name = kwargs["only"][0]
        target = target_root / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(f"# Deployed {name}\n", encoding="utf-8")
        cache = tmp_path / "cache" / name
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "SKILL.md").write_text(f"# Deployed {name}\n", encoding="utf-8")
        action = SyncAction.UPDATED if name == "a-first" else SyncAction.FAILED
        return [
            SyncResult(
                name=name,
                install_path=cache,
                action=action,
                detail="injected failure" if action == SyncAction.FAILED else "",
            )
        ]

    monkeypatch.setattr("lpm.services.env_manager.sync_all", fake_sync_all)

    with pytest.raises(DeploymentTransactionError) as error:
        deploy_environment(config=cfg)

    assert error.value.plan.status == "rolled_back"
    assert error.value.plan.rolled_back is True
    assert (first_target / "SKILL.md").read_text(encoding="utf-8") == "# Previous\n"
    assert not second_target.exists()
    operation = load_operation(error.value.plan.operation_id)
    assert operation.status == "rolled_back"
    assert operation.rolled_back is True
    assert all(target.verified for target in operation.targets)


def test_env_pull_diff_and_apply_incoming_choice(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    source_home = _source_home(tmp_path)
    local_repo = tmp_path / "local-repo"
    remote_repo = tmp_path / "remote-repo"
    target_home = tmp_path / "target-home"
    capture_environment(config=_config(local_repo, target_home), home=source_home)
    shutil.copytree(local_repo, remote_repo)
    remote_skill = remote_repo / "resources" / "skills" / "cursor" / "cursor-skill-demo-skill" / "SKILL.md"
    remote_skill.write_text("# Remote skill\n", encoding="utf-8")
    _commit_resource_repo(remote_repo, "remote env")

    local_skill = local_repo / "resources" / "skills" / "cursor" / "cursor-skill-demo-skill" / "SKILL.md"
    local_skill.write_text("# Local skill\n", encoding="utf-8")
    cfg = _config(local_repo, target_home, repo_url=str(remote_repo))

    plan = build_env_pull_diff(config=cfg)

    resource_id = "resource:skill:cursor-skill-demo-skill"
    resource_item = next(item for item in plan.items if item.id == resource_id)
    assert resource_item.status == "conflict"
    assert resource_item.default_choice == "incoming"
    assert "Remote skill" in resource_item.preview

    apply_env_pull(config=cfg, choices={resource_id: "incoming"})

    assert local_skill.read_text(encoding="utf-8") == "# Remote skill\n"


def test_env_snapshot_import_dry_run_and_apply(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    source_home = _source_home(tmp_path)
    local_repo = tmp_path / "local-repo"
    snapshot_repo = tmp_path / "snapshot-repo"
    target_home = tmp_path / "target-home"
    capture_environment(config=_config(local_repo, target_home), home=source_home)
    shutil.copytree(local_repo, snapshot_repo)

    local_skill = local_repo / "resources" / "skills" / "cursor" / "cursor-skill-demo-skill" / "SKILL.md"
    snapshot_skill = snapshot_repo / "resources" / "skills" / "cursor" / "cursor-skill-demo-skill" / "SKILL.md"
    local_skill.write_text("# Local snapshot base\n", encoding="utf-8")
    snapshot_skill.write_text("# Snapshot skill\n", encoding="utf-8")
    snapshot = export_environment_snapshot(tmp_path / "snapshot.zip", config=_config(snapshot_repo, target_home))
    cfg = _config(local_repo, target_home)

    plan = build_env_import_diff(snapshot, config=cfg)

    resource_id = "resource:skill:cursor-skill-demo-skill"
    resource_item = next(item for item in plan.items if item.id == resource_id)
    assert resource_item.status == "conflict"
    assert resource_item.default_choice == "incoming"

    apply_env_import(snapshot, config=cfg, choices={resource_id: "incoming"})

    assert local_skill.read_text(encoding="utf-8") == "# Snapshot skill\n"


def test_env_snapshot_import_rejects_unsafe_zip_path(tmp_path: Path) -> None:
    snapshot = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(snapshot, "w") as archive:
        archive.writestr("../registry.yaml", "version: 5\nitems: []\n")

    with pytest.raises(ValueError, match="Unsafe snapshot path"):
        build_env_import_diff(snapshot, config=_config(tmp_path / "repo", tmp_path / "target"))


def test_env_snapshot_secret_guard_blocks_apply(tmp_path: Path) -> None:
    snapshot = tmp_path / "secret.zip"
    with zipfile.ZipFile(snapshot, "w") as archive:
        archive.writestr(
            "registry.yaml",
            """version: 5
items:
- name: secret-prompt
  kind: prompt
  source: local
  path: resources/prompts/secret-prompt
""",
        )
        archive.writestr("resources/prompts/secret-prompt/prompt.md", "api_key: sk_test_secret_123456\n")

    cfg = _config(tmp_path / "repo", tmp_path / "target")
    plan = build_env_import_diff(snapshot, config=cfg)

    assert plan.blocked is True
    assert plan.secret_findings[0].path.as_posix() == "resources/prompts/secret-prompt/prompt.md"
    with pytest.raises(EnvSecretScanError):
        apply_env_import(snapshot, config=cfg)


def _source_home(tmp_path: Path) -> Path:
    home = tmp_path / "source-home"
    skill_dir = home / ".cursor" / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: demo-skill",
                "description: Demo skill",
                "---",
                "# Demo",
                "api_key: " + SKILL_SECRET,
                "",
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / ".env").write_text("MODE=development\n", encoding="utf-8")
    (skill_dir / ".env.example").write_text("TOKEN=${TOKEN}\n", encoding="utf-8")
    prompt_dir = home / ".cursor" / "prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "review.md").write_text("# Review prompt\n", encoding="utf-8")
    rule_dir = home / ".cursor" / "rules"
    rule_dir.mkdir(parents=True)
    (rule_dir / "style.mdc").write_text("Always keep changes scoped.\n", encoding="utf-8")
    (home / ".cursor" / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-github"],
                        "env": {"GITHUB_TOKEN": MCP_SECRET},
                    }
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return home


def _config(
    resource_repo: Path,
    target_home: Path,
    *,
    include_windsurf: bool = False,
    repo_url: str = "",
) -> Config:
    platforms = [
        PlatformProfile(
            name="cursor",
            enabled=True,
            skills_dir=str(target_home / ".cursor" / "skills"),
            mcp_json=str(target_home / ".cursor" / "mcp.json"),
            rules_dir=str(target_home / ".cursor" / "rules"),
            plugins_dir=str(target_home / ".cursor" / "plugins"),
        )
    ]
    if include_windsurf:
        platforms.append(
            PlatformProfile(
                name="windsurf",
                enabled=True,
                skills_dir=str(target_home / ".windsurf" / "skills"),
                mcp_json=str(target_home / ".windsurf" / "mcp.json"),
                rules_dir=str(target_home / ".windsurf" / "rules"),
                plugins_dir=str(target_home / ".windsurf" / "plugins"),
            )
        )
    return Config(
        install=InstallConfig(target=str(target_home / ".lpm-cache")),
        resources=ResourcesConfig(repo_name="resource-repo", local_path=str(resource_repo), repo_url=repo_url, branch="main"),
        platforms=PlatformsConfig(profiles=platforms),
    )


def _commit_resource_repo(path: Path, message: str) -> None:
    git_ops.init_repo(path, default_branch="main")
    git_ops.add_all(path)
    git_ops.commit(path, message=message, allow_empty=True)


def _assert_secret_absent(root: Path, secret: str) -> None:
    needle = secret.encode()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        assert needle not in path.read_bytes(), path
