from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from lpm.core.config import Config, InstallConfig, ResourcesConfig
from lpm.core.platforms import PlatformProfile, PlatformsConfig
from lpm.infrastructure import git_ops
from lpm.services.env_manager import (
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
    skill_target = target_home / ".cursor" / "skills" / "cursor-skill-demo-skill"
    assert (skill_target / "SKILL.md").is_file()
    assert (skill_target / ".lpm-managed.json").is_file()

    mcp_text = (target_home / ".cursor" / "mcp.json").read_text(encoding="utf-8")
    assert "${GITHUB_TOKEN}" in mcp_text
    assert MCP_SECRET not in mcp_text


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

    resource_item = next(item for item in plan.items if item.id == "resource:cursor-skill-demo-skill")
    assert resource_item.status == "conflict"
    assert resource_item.default_choice == "incoming"
    assert "Remote skill" in resource_item.preview

    apply_env_pull(config=cfg, choices={"resource:cursor-skill-demo-skill": "incoming"})

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

    resource_item = next(item for item in plan.items if item.id == "resource:cursor-skill-demo-skill")
    assert resource_item.status == "conflict"
    assert resource_item.default_choice == "incoming"

    apply_env_import(snapshot, config=cfg, choices={"resource:cursor-skill-demo-skill": "incoming"})

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
