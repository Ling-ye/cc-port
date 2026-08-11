from __future__ import annotations

import io
from pathlib import Path

import pytest

from cc_port.core.config import (
    Config,
    GitConfig,
    GithubConfig,
    ResourcesConfig,
    StateConfig,
    load_raw_config,
    write_config,
)
from cc_port.core.models import RegistryItem
from cc_port.core.platforms import PlatformProfile, PlatformsConfig
from cc_port.core.resource_detection import DetectedRemoteResource
from cc_port.infrastructure import git_ops
from cc_port.interfaces import desktop_api
from cc_port.services.asset_sync import (
    AssetActionPlan,
    AssetActionResult,
    AssetBatchPlan,
    AssetBatchResourceCheck,
    AssetBatchResult,
    AssetContentDiff,
    AssetDiffFile,
    AssetInventory,
)
from cc_port.services.local_resources import ImportLocalResult
from cc_port.services.resource_commit import (
    ResourceCommitChange,
    ResourceCommitIssue,
    ResourceCommitPlan,
)
from cc_port.services.resource_manager import ResourceDeleteResult
from cc_port.services.resource_sync import ResourceSyncPlan, SyncConflict


def test_desktop_main_reads_bom_tolerant_json_from_stdin(monkeypatch) -> None:
    responses: list[dict] = []
    monkeypatch.setitem(desktop_api.ACTIONS, "stdin_test", lambda payload: payload)
    monkeypatch.setattr(desktop_api.sys, "stdin", io.StringIO('\ufeff{"value": 7}'))
    monkeypatch.setattr(desktop_api, "_write_json_response", responses.append)

    exit_code = desktop_api.main(["stdin_test"])

    assert exit_code == 0
    assert responses == [{"ok": True, "data": {"value": 7}}]


def test_desktop_main_consumes_packaged_payload_environment_before_action(monkeypatch) -> None:
    responses: list[dict] = []

    def handler(payload: dict) -> dict:
        assert desktop_api.DESKTOP_PAYLOAD_ENV_VAR not in desktop_api.os.environ
        return payload

    monkeypatch.setitem(desktop_api.ACTIONS, "environment_payload_test", handler)
    monkeypatch.setenv(desktop_api.DESKTOP_PAYLOAD_ENV_VAR, '{"value": 9}')
    monkeypatch.setattr(desktop_api, "_write_json_response", responses.append)

    exit_code = desktop_api.main(["environment_payload_test"])

    assert exit_code == 0
    assert responses == [{"ok": True, "data": {"value": 9}}]


def test_desktop_main_structures_invalid_json_and_non_object_payloads(monkeypatch) -> None:
    responses: list[dict] = []
    monkeypatch.setattr(desktop_api, "_write_json_response", responses.append)

    monkeypatch.setattr(desktop_api.sys, "stdin", io.StringIO("{"))
    invalid_json_exit = desktop_api.main(["summary"])
    monkeypatch.setattr(desktop_api.sys, "stdin", io.StringIO("[]"))
    invalid_payload_exit = desktop_api.main(["summary"])

    assert invalid_json_exit == 1
    assert responses[0]["error"]["code"] == "invalid_json"
    assert responses[0]["error"]["message_ref"]["code"] == "api.invalid_json"
    assert responses[0]["error"]["message_ref"]["fallback"] == responses[0]["error"]["message"]
    assert responses[0]["error"]["message_ref"]["params"] == {
        "detail": responses[0]["error"]["message"],
    }
    assert invalid_payload_exit == 1
    assert responses[1]["error"] == {
        "code": "invalid_payload",
        "message": "Payload must be a JSON object.",
        "message_ref": {
            "code": "api.invalid_payload",
            "fallback": "Payload must be a JSON object.",
            "params": {},
        },
    }


def test_desktop_unknown_action_returns_message_reference(monkeypatch) -> None:
    monkeypatch.setattr(desktop_api, "load_config", Config)

    result = desktop_api.run_action("missing_desktop_action")

    assert result["ok"] is False
    assert result["error"]["code"] == "unknown_action"
    assert result["error"]["message"] == "Unknown UI API action: missing_desktop_action"
    assert result["error"]["message_ref"] == {
        "code": "api.unknown_action",
        "fallback": "Unknown UI API action: missing_desktop_action",
        "params": {"action": "missing_desktop_action"},
    }


def test_desktop_upload_pushes_resource_repo_by_default(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "demo"
    stored = tmp_path / "resources" / "skills" / "demo"
    source.mkdir()
    (source / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
    calls: list[str] = []

    def fake_import_local_resource(path: Path, **_kwargs) -> ImportLocalResult:
        assert path == source
        return ImportLocalResult(
            entry=RegistryItem(name="demo", kind="skill", source="local", path="skills/demo"),
            source_path=source,
            stored_path=stored,
        )

    def fake_push_resource_repo(*, config: Config):
        calls.append("push")
        return {"local_path": str(tmp_path / "resources")}

    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(
        desktop_api, "detect_local_resource_type", lambda *_args, **_kwargs: "skill"
    )
    monkeypatch.setattr(desktop_api, "import_local_resource", fake_import_local_resource)
    monkeypatch.setattr(desktop_api, "push_resource_repo", fake_push_resource_repo)

    result = desktop_api.run_action("upload", {"path": str(source)})

    assert result["ok"] is True
    assert calls == ["push"]
    assert result["data"]["push"]["local_path"].endswith("resources")


def test_desktop_upload_allows_explicit_no_push(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "demo"
    stored = tmp_path / "resources" / "skills" / "demo"
    source.mkdir()
    (source / "SKILL.md").write_text("# Demo\n", encoding="utf-8")

    def fake_import_local_resource(path: Path, **_kwargs) -> ImportLocalResult:
        assert path == source
        return ImportLocalResult(
            entry=RegistryItem(name="demo", kind="skill", source="local", path="skills/demo"),
            source_path=source,
            stored_path=stored,
        )

    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(
        desktop_api, "detect_local_resource_type", lambda *_args, **_kwargs: "skill"
    )
    monkeypatch.setattr(desktop_api, "import_local_resource", fake_import_local_resource)
    monkeypatch.setattr(
        desktop_api,
        "push_resource_repo",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("push should be skipped")),
    )

    result = desktop_api.run_action("upload", {"path": str(source), "no_push": True})

    assert result["ok"] is True
    assert result["data"]["push"] is None


def test_portable_resource_platforms_never_persist_local_profile_ids() -> None:
    cfg = Config(
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="claude-windows",
                    tool_id="claude-code",
                    environment_kind="windows",
                ),
                PlatformProfile(
                    name="codex-wsl-ubuntu",
                    tool_id="codex",
                    environment_kind="wsl",
                    environment_name="Ubuntu-24.04",
                ),
            ]
        )
    )

    assert desktop_api._portable_resource_platforms(
        cfg,
        "instruction",
        ["claude-windows", "claude-code"],
    ) == ["claude-code"]
    assert desktop_api._portable_resource_platforms(cfg, "memory", []) == [
        "claude-code"
    ]
    with pytest.raises(ValueError, match="explicit portable tool binding"):
        desktop_api._portable_resource_platforms(cfg, "instruction", [])
    with pytest.raises(ValueError, match="only be bound to Claude Code"):
        desktop_api._portable_resource_platforms(
            cfg,
            "memory",
            ["codex-wsl-ubuntu"],
        )
    with pytest.raises(ValueError, match="exactly one portable source tool"):
        desktop_api._portable_resource_platforms(
            cfg,
            "instruction",
            ["claude-windows", "codex-wsl-ubuntu"],
        )
    with pytest.raises(ValueError, match="Unknown platform"):
        desktop_api._portable_resource_platforms(
            cfg,
            "skill",
            ["claude-wsl-typo"],
        )


def test_desktop_collect_mcp_requires_and_forwards_portable_config(monkeypatch) -> None:
    detected = DetectedRemoteResource(
        repo_url="https://github.com/example/demo-mcp",
        ref="main",
        subdir="",
        kind="mcp",
        name_hint="demo-mcp",
        tags=["mcp"],
    )
    captured: dict = {}

    def fake_add_external_skill(repo: str, **kwargs) -> RegistryItem:
        captured["repo"] = repo
        captured.update(kwargs)
        return RegistryItem(
            name="demo-mcp",
            kind="mcp",
            source="external",
            repo=repo,
            ref="a" * 40,
            mcp_config=kwargs["mcp_config"],
        )

    detection: dict = {}

    def fake_detect_remote_resource(url: str, **kwargs) -> DetectedRemoteResource:
        detection["url"] = url
        detection.update(kwargs)
        return detected

    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(desktop_api, "detect_remote_resource", fake_detect_remote_resource)
    monkeypatch.setattr(desktop_api.publisher, "add_external_skill", fake_add_external_skill)

    config = {
        "command": "npx",
        "args": ["-y", "@example/demo-mcp@1.0.0"],
        "env": {"EXAMPLE_TOKEN": "${EXAMPLE_TOKEN}"},
    }
    result = desktop_api.run_action(
        "collect",
        {"github_url": detected.repo_url, "kind": "mcp", "mcp_config": config},
    )

    assert result["ok"] is True
    assert captured["repo"] == detected.repo_url
    assert captured["mcp_config"] == config
    assert captured["token"] is None
    assert detection == {
        "url": detected.repo_url,
        "explicit_type": "mcp",
        "token": None,
    }


def test_desktop_collect_rejects_mcp_without_portable_config(monkeypatch) -> None:
    detected = DetectedRemoteResource(
        repo_url="https://github.com/example/demo-mcp",
        ref="main",
        subdir="",
        kind="mcp",
        name_hint="demo-mcp",
        tags=["mcp"],
    )
    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(desktop_api, "detect_remote_resource", lambda *_args, **_kwargs: detected)
    monkeypatch.setattr(
        desktop_api.publisher,
        "add_external_skill",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid MCP collection must not reach the publisher")
        ),
    )

    result = desktop_api.run_action(
        "collect",
        {"github_url": detected.repo_url, "kind": "mcp"},
    )

    assert result["ok"] is False
    assert "require a portable mcp_config" in result["error"]["message"]


def test_desktop_collect_requires_explicit_type_before_remote_detection(monkeypatch) -> None:
    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(
        desktop_api,
        "detect_remote_resource",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("desktop collection must not call the GitHub API for type detection")
        ),
    )

    result = desktop_api.run_action(
        "collect",
        {"github_url": "https://github.com/example/demo"},
    )

    assert result["ok"] is False
    assert result["error"]["message"] == "Missing required field: kind"


def test_desktop_resource_delete_pushes_resource_repo_by_default(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[str, str]] = []
    entry = RegistryItem(
        name="demo",
        kind="skill",
        source="local",
        path="skills/demo",
        lifecycle="removed",
        removed_effect="local_files_deleted",
    )

    def fake_delete_resource(name: str, **kwargs) -> ResourceDeleteResult:
        assert name == "demo"
        assert isinstance(kwargs["config"], Config)
        calls.append(("delete", name))
        return ResourceDeleteResult(
            name=name,
            effect="local_files_deleted",
            entry=entry,
            deleted_path=tmp_path / "resources" / "skills" / "demo",
            deleted_local_files=True,
        )

    def fake_push_resource_repo(*, message: str, config: Config):
        assert isinstance(config, Config)
        calls.append(("push", message))
        return {"local_path": str(tmp_path / "resources")}

    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(
        desktop_api, "resource_delete_requires_remote_scope", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(desktop_api, "delete_resource", fake_delete_resource)
    monkeypatch.setattr(desktop_api, "push_resource_repo", fake_push_resource_repo)

    result = desktop_api.run_action("resource_delete", {"name": "demo"})

    assert result["ok"] is True
    assert calls == [("delete", "demo"), ("push", "cc-port: delete resource demo")]
    assert result["data"]["name"] == "demo"
    assert result["data"]["deleted_local_files"] is True
    assert result["data"]["push"]["local_path"].endswith("resources")


def test_desktop_owned_remote_delete_is_rejected_before_side_effects(monkeypatch) -> None:
    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(
        desktop_api,
        "resource_delete_requires_remote_scope",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        desktop_api,
        "delete_resource",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("delete must not run before scope validation")
        ),
    )

    result = desktop_api.run_action("resource_delete", {"name": "demo", "kind": "skill"})

    assert result["ok"] is False
    assert result["error"]["code"] == "DesktopRemoteRepositoryMutationError"


def test_platform_toggle_preserves_hidden_and_custom_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    cfg = Config(
        github=GithubConfig(token="token", owner="Lingye", repo_prefix="keep-"),
        git=GitConfig(executable="D:/Git/bin/git.exe"),
        resources=ResourcesConfig(
            repo_url="https://github.com/Lingye/resources.git",
            local_path="D:/private/resources",
            branch="release",
            credential_mode="auto",
        ),
        state=StateConfig(retention_days=17),
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(
                    name="cursor",
                    enabled=True,
                    skills_dir="D:/custom/cursor/skills",
                    prompts_dir="D:/custom/cursor/commands",
                ),
                PlatformProfile(
                    name="private-tool",
                    tool_id="claude-code",
                    environment_kind="wsl",
                    environment_name="Ubuntu-24.04",
                    display_name="Claude Code",
                    home_dir="//wsl.localhost/Ubuntu-24.04/home/lingye",
                    enabled=True,
                    skills_dir="D:/custom/private/skills",
                    instructions_path="D:/custom/private/CLAUDE.md",
                    memories_dir="D:/custom/private/projects",
                    settings_path="D:/custom/private/settings.json",
                    memory_install_names={"project-one": "wsl-project-slot"},
                ),
            ]
        ),
    )
    write_config(cfg, config_path)
    monkeypatch.setenv("CC_PORT_CONFIG", str(config_path))

    result = desktop_api.run_action("platform_set_enabled", {"name": "cursor", "enabled": False})
    updated = load_raw_config(config_path)

    assert result["ok"] is True
    assert updated.platforms.get("cursor").enabled is False
    assert updated.platforms.get("cursor").skills_dir == "D:/custom/cursor/skills"
    assert updated.platforms.get("cursor").prompts_dir == "D:/custom/cursor/commands"
    assert updated.platforms.get("private-tool").enabled is True
    assert updated.platforms.get("private-tool").tool_id == "claude-code"
    assert updated.platforms.get("private-tool").environment_kind == "wsl"
    assert updated.platforms.get("private-tool").environment_name == "Ubuntu-24.04"
    assert updated.platforms.get("private-tool").home_dir == (
        "//wsl.localhost/Ubuntu-24.04/home/lingye"
    )
    assert updated.platforms.get("private-tool").instructions_path.endswith("CLAUDE.md")
    assert updated.platforms.get("private-tool").memories_dir.endswith("projects")
    assert updated.platforms.get("private-tool").settings_path.endswith("settings.json")
    assert updated.platforms.get("private-tool").memory_install_names == {
        "project-one": "wsl-project-slot"
    }
    assert updated.github.repo_prefix == "keep-"
    assert updated.git.executable == "D:/Git/bin/git.exe"
    assert updated.resources.local_path == "D:/private/resources"
    assert updated.resources.branch == "release"
    assert updated.resources.credential_mode == "auto"
    assert updated.state.retention_days == 17

    custom_result = desktop_api.run_action(
        "platform_set_enabled",
        {"name": "private-tool", "enabled": False},
    )
    custom_updated = load_raw_config(config_path)

    assert custom_result["ok"] is True
    assert custom_updated.platforms.get("private-tool").enabled is False
    custom_json = next(
        item
        for item in custom_result["data"]["config"]["platforms"]
        if item["name"] == "private-tool"
    )
    assert custom_json["tool_id"] == "claude-code"
    assert custom_json["environment_kind"] == "wsl"
    assert custom_json["home_dir"] == "//wsl.localhost/Ubuntu-24.04/home/lingye"
    assert custom_json["settings_path"] == "D:/custom/private/settings.json"
    assert custom_json["memory_install_names"] == {
        "project-one": "wsl-project-slot"
    }


def test_desktop_environment_actions_are_removed() -> None:
    assert not any(action.startswith("env_") for action in desktop_api.ACTIONS)


def test_desktop_resource_sync_plan_serializes_conflicts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(
        desktop_api,
        "build_resource_sync_plan",
        lambda **_kwargs: ResourceSyncPlan(
            operation_id="abc123",
            repo_path=tmp_path / "resources",
            branch="main",
            status="conflict",
            local_commit="local",
            remote_commit="remote",
            merge_base="base",
            ahead=1,
            behind=1,
            worktree_path=tmp_path / "state" / "worktree",
            conflicts=[
                SyncConflict(
                    id="resource:demo",
                    path="skills/demo/SKILL.md",
                    resource="demo",
                    reason="changed on both sides",
                )
            ],
        ),
    )

    result = desktop_api.run_action("resource_sync_plan")

    assert result["ok"] is True
    assert result["data"]["repo_path"] == str(tmp_path / "resources")
    assert result["data"]["conflicts"][0]["id"] == "resource:demo"
    assert result["deprecated"] is True
    assert "asset_inventory" in result["warnings"][0]


def test_desktop_asset_inventory_plan_and_apply(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_inventory(**kwargs) -> AssetInventory:
        calls.append(("inventory", (kwargs["scan_local"], kwargs["refresh_remote"])))
        return AssetInventory(
            branch="main",
            remote_commit="abc123",
            repo_url="https://example.test/resources.git",
            remote_available=True,
            remote_warning="",
            scanned_local=kwargs["scan_local"],
            generated_at="2026-07-17T00:00:00Z",
            legacy_write_blocker="",
            rows=[],
        )

    def fake_plan(action: str, **kwargs) -> AssetActionPlan:
        calls.append(("plan", (action, kwargs["kind"], kwargs["name"], kwargs["platform"])))
        return AssetActionPlan(
            operation_id="plan-1",
            action="download",
            resource_key="skill:demo",
            target_resource_key="skill:demo",
            kind="skill",
            name="demo",
            platform="cursor",
            local_instance_id="instance-1",
            local_locator="expected",
            remote_commit="abc123",
            remote_target_exists=True,
            remote_target_fingerprint="remote",
            local_source_fingerprint="local",
            target_path=None,
            target_exists=False,
            target_fingerprint="",
            target_managed=False,
        )

    def fake_apply(operation_id: str, **_kwargs) -> AssetActionResult:
        calls.append(("apply", operation_id))
        return AssetActionResult(
            operation_id=operation_id,
            action="download",
            status="succeeded",
            resource_key="skill:demo",
            target_resource_key="skill:demo",
            platform="cursor",
            message="done",
        )

    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(desktop_api, "build_asset_inventory", fake_inventory)
    monkeypatch.setattr(desktop_api, "build_asset_action_plan", fake_plan)
    monkeypatch.setattr(desktop_api, "apply_asset_action_plan", fake_apply)

    inventory = desktop_api.run_action(
        "asset_inventory",
        {"scan_local": True, "refresh_remote": False},
    )
    plan = desktop_api.run_action(
        "asset_action_plan",
        {
            "action": "download",
            "kind": "skill",
            "name": "demo",
            "platform": "cursor",
            "local_instance_id": "instance-1",
        },
    )
    applied = desktop_api.run_action(
        "asset_action_apply",
        {"operation_id": "plan-1"},
    )

    assert inventory["data"]["scanned_local"] is True
    assert "rows" not in inventory["data"]
    assert inventory["data"]["resources"] == []
    assert plan["data"]["resource_key"] == "skill:demo"
    assert applied["data"]["status"] == "succeeded"
    assert calls == [
        ("inventory", (True, False)),
        ("plan", ("download", "skill", "demo", "cursor")),
        ("apply", "plan-1"),
    ]


def test_desktop_asset_content_diff_forwards_selected_instance(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_diff(
        resource_key: str,
        local_instance_id: str,
        **_kwargs,
    ) -> AssetContentDiff:
        calls.append((resource_key, local_instance_id))
        return AssetContentDiff(
            resource_key=resource_key,
            local_instance_id=local_instance_id,
            platform="cursor",
            remote_commit="abc123",
            files=[
                AssetDiffFile(
                    path="SKILL.md",
                    status="modified",
                    diff="-remote\n+local",
                )
            ],
            added_files=0,
            deleted_files=0,
            modified_files=1,
            binary_files=0,
        )

    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(desktop_api, "build_asset_content_diff", fake_diff)

    result = desktop_api.run_action(
        "asset_content_diff",
        {
            "resource_key": "skill:demo",
            "local_instance_id": "instance-1",
        },
    )

    assert result["ok"] is True
    assert result["data"]["files"][0]["path"] == "SKILL.md"
    assert calls == [("skill:demo", "instance-1")]


def test_desktop_asset_batch_plan_and_apply(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_batch_plan(direction: str, **kwargs) -> AssetBatchPlan:
        calls.append(("plan", (direction, kwargs["resource_keys"], kwargs["target_platforms"])))
        return AssetBatchPlan(
            direction=direction,
            resource_keys=kwargs["resource_keys"],
            target_platforms=kwargs["target_platforms"],
            remote_commit="abc123",
            plan_hash="batch-hash",
            items=[],
            executable_count=0,
            blocked_count=0,
            skipped_count=0,
            checked_resources=[
                AssetBatchResourceCheck(
                    resource_key="skill:demo",
                    local_status="single",
                    remote_status="missing",
                    status="local-only",
                )
            ],
        )

    def fake_batch_apply(direction: str, **kwargs) -> AssetBatchResult:
        calls.append(("apply", (direction, kwargs["expected_plan_hash"])))
        return AssetBatchResult(status="succeeded", plan_hash="batch-hash", results=[])

    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(desktop_api, "build_asset_batch_plan", fake_batch_plan)
    monkeypatch.setattr(desktop_api, "apply_asset_batch_plan", fake_batch_apply)

    planned = desktop_api.run_action(
        "asset_batch_plan",
        {
            "direction": "download",
            "resource_keys": ["skill:demo"],
            "target_platforms": ["cursor"],
            "choices": [{"resource_key": "skill:demo", "platform": "cursor"}],
        },
    )
    applied = desktop_api.run_action(
        "asset_batch_apply",
        {
            "direction": "download",
            "resource_keys": ["skill:demo"],
            "target_platforms": ["cursor"],
            "plan_hash": "batch-hash",
        },
    )

    assert planned["data"]["plan_hash"] == "batch-hash"
    assert planned["data"]["checked_resources"] == [
        {
            "resource_key": "skill:demo",
            "local_status": "single",
                "remote_status": "missing",
                "status": "local-only",
                "local_instances": [],
            }
        ]
    assert applied["data"]["status"] == "succeeded"
    assert calls == [
        ("plan", ("download", ["skill:demo"], ["cursor"])),
        ("apply", ("download", "batch-hash")),
    ]


def test_desktop_batch_choice_parses_link_target_confirmation() -> None:
    choices = desktop_api._asset_batch_choices(
        [
            {
                "resource_key": "skill:demo",
                "local_instance_id": "linked-demo",
                "link_target_confirmed": True,
            }
        ]
    )

    assert len(choices) == 1
    assert choices[0].link_target_confirmed is True


def test_desktop_resource_commit_plan_serializes_resource_blockers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(
        desktop_api,
        "build_resource_commit_plan",
        lambda **_kwargs: ResourceCommitPlan(
            repo_path=tmp_path / "resources",
            changed_paths=["notes.txt"],
            managed_paths=[],
            resources=[
                ResourceCommitChange(
                    name="notes.txt",
                    kind="metadata",
                    action="added",
                    paths=["notes.txt"],
                )
            ],
            blocked_paths=[
                ResourceCommitIssue(
                    path="notes.txt",
                    reason="outside managed scope",
                )
            ],
            secret_findings=[],
            suggested_message="cc-port: update resource metadata",
        ),
    )

    result = desktop_api.run_action("resource_commit_plan")

    assert result["ok"] is True
    assert result["data"]["blocked"] is True
    assert result["data"]["blocked_paths"][0]["path"] == "notes.txt"


def test_desktop_operation_history_and_force_restore(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        desktop_api,
        "operation_history",
        lambda *, limit: [
            {
                "operation_id": "abc123",
                "kind": "resource-install",
                "status": "succeeded",
                "restorable": True,
                "limit": limit,
            }
        ],
    )

    def fake_restore(operation_id: str, *, force: bool, config: Config):
        assert isinstance(config, Config)
        calls.append((operation_id, force))
        return {"source_operation_id": operation_id, "status": "succeeded"}

    monkeypatch.setattr(desktop_api, "restore_operation", fake_restore)

    history = desktop_api.run_action("operation_history", {"limit": 20})
    restored = desktop_api.run_action(
        "operation_restore",
        {"operation_id": "abc123", "force": True},
    )

    assert history["ok"] is True
    assert history["data"]["operations"][0]["limit"] == 20
    assert restored["ok"] is True
    assert calls == [("abc123", True)]


def test_desktop_operation_page_detail_and_state_maintenance(monkeypatch) -> None:
    monkeypatch.setattr(
        desktop_api,
        "operation_history_page",
        lambda *, offset, limit: {
            "operations": [{"operation_id": "abc123"}],
            "offset": offset,
            "limit": limit,
            "total": 1,
            "has_more": False,
        },
    )
    monkeypatch.setattr(
        desktop_api,
        "operation_detail",
        lambda operation_id: {
            "operation_id": operation_id,
            "targets": [{"path": "/target"}],
        },
    )
    monkeypatch.setattr(
        desktop_api,
        "list_orphan_backups",
        lambda: [{"name": "lost", "size_bytes": 42}],
    )
    monkeypatch.setattr(
        desktop_api,
        "list_orphan_quarantines",
        lambda: [{"quarantine_id": "def456", "item_count": 1}],
    )
    monkeypatch.setattr(
        desktop_api,
        "list_maintenance_audits",
        lambda *, limit: [{"audit_id": "audit123", "limit": limit}],
    )
    monkeypatch.setattr(
        desktop_api,
        "load_maintenance_audit",
        lambda audit_id: {"audit_id": audit_id, "status": "succeeded"},
    )

    page = desktop_api.run_action(
        "operation_history_page",
        {"offset": 20, "limit": 20},
    )
    detail = desktop_api.run_action(
        "operation_detail",
        {"operation_id": "abc123"},
    )
    orphans = desktop_api.run_action("orphan_backups")
    quarantines = desktop_api.run_action("orphan_quarantines")
    audits = desktop_api.run_action("maintenance_audits", {"limit": 10})
    audit = desktop_api.run_action(
        "maintenance_audit",
        {"audit_id": "audit123"},
    )

    assert page["data"]["offset"] == 20
    assert detail["data"]["targets"][0]["path"] == "/target"
    assert orphans["data"]["orphans"][0]["name"] == "lost"
    assert quarantines["data"]["quarantines"][0]["quarantine_id"] == "def456"
    assert audits["data"]["audits"][0]["limit"] == 10
    assert audit["data"]["audit"]["audit_id"] == "audit123"


def test_desktop_state_retention_plan_and_prune(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_plan(**kwargs):
        calls.append(("plan", kwargs["retention_days"]))
        return {"candidate_count": 1, "candidates": [{"operation_id": "abc123"}]}

    def fake_prune(operation_ids: list[str], **kwargs):
        calls.append(("prune", (operation_ids, kwargs["max_backup_mb"])))
        return {"deleted_operation_ids": operation_ids, "reclaimed_bytes": 42}

    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(desktop_api, "build_state_retention_plan", fake_plan)
    monkeypatch.setattr(desktop_api, "prune_state", fake_prune)

    plan = desktop_api.run_action(
        "state_retention_plan",
        {
            "retention_days": 30,
            "keep_latest_operations": 5,
            "max_backup_mb": 100,
        },
    )
    pruned = desktop_api.run_action(
        "state_prune",
        {
            "operation_ids": ["abc123"],
            "retention_days": 30,
            "keep_latest_operations": 5,
            "max_backup_mb": 100,
        },
    )

    assert plan["ok"] is True
    assert plan["data"]["candidate_count"] == 1
    assert pruned["ok"] is True
    assert calls == [
        ("plan", 30),
        ("prune", (["abc123"], 100)),
    ]


def test_desktop_lists_and_cleans_stale_sync_worktrees(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        desktop_api,
        "list_stale_resource_sync_plans",
        lambda *, min_age_hours: [
            {
                "operation_id": "abc123",
                "worktree_path": tmp_path / "worktree",
                "age_hours": min_age_hours,
            }
        ],
    )
    monkeypatch.setattr(
        desktop_api,
        "cleanup_stale_resource_sync_plan",
        lambda operation_id, **_kwargs: {"operation_id": operation_id, "status": "abandoned"},
    )

    stale = desktop_api.run_action("resource_sync_stale", {"min_age_hours": 12})
    cleaned = desktop_api.run_action(
        "resource_sync_cleanup",
        {"operation_id": "abc123"},
    )

    assert stale["ok"] is True
    assert stale["data"]["plans"][0]["worktree_path"] == str(tmp_path / "worktree")
    assert stale["data"]["plans"][0]["age_hours"] == 12
    assert cleaned["data"]["status"] == "abandoned"


def test_config_bind_repo_exposes_binding_and_updated_settings(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_bind(repo_url: str, *, expected_current_repo_url: str):
        calls.append((repo_url, expected_current_repo_url))
        return {
            "repo_url": "https://github.com/example/resources.git",
            "read_verified": True,
            "write_verified": True,
        }

    monkeypatch.setattr(desktop_api, "bind_resource_repo", fake_bind)
    monkeypatch.setattr(
        desktop_api.git_ops,
        "require_git_credential_manager",
        lambda: git_ops.GitCredentialStatus.ready_for_use(),
    )
    monkeypatch.setattr(
        desktop_api,
        "_config_get",
        lambda _payload: {"config": {"resources": {"repo_name": "resources"}}},
    )

    result = desktop_api.run_action(
        "config_bind_repo",
        {
            "repo_url": "https://github.com/example/resources",
            "expected_current_repo_url": "",
        },
    )

    assert result["ok"] is True
    assert calls == [("https://github.com/example/resources", "")]
    assert result["data"]["binding"]["write_verified"] is True
    assert result["data"]["settings"]["config"]["resources"]["repo_name"] == "resources"


@pytest.mark.parametrize(
    "repo_url",
    [
        "https://github.com/Ling-ye",
        "https://github.com/Ling-ye/resources/tree/main",
        "https://user:secret@github.com/Ling-ye/resources",
        "git@github.com:Ling-ye/resources.git",
    ],
)
def test_desktop_binding_rejects_non_root_or_non_https_urls_before_git(
    repo_url: str,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        desktop_api,
        "bind_resource_repo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid desktop URL must not reach binding")
        ),
    )

    result = desktop_api.run_action(
        "config_bind_repo",
        {"repo_url": repo_url, "expected_current_repo_url": ""},
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "ValueError"


def test_git_credential_status_is_exposed_without_oauth_or_repo_admin_actions(
    monkeypatch,
) -> None:
    status = git_ops.GitCredentialStatus.ready_for_use()
    monkeypatch.setattr(desktop_api.git_ops, "git_credential_status", lambda: status)

    result = desktop_api.run_action("git_credential_status")

    assert result["ok"] is True
    assert result["data"]["state"] == "ready"
    assert result["data"]["ready"] is True
    for removed in (
        "github_auth_status",
        "github_auth_start",
        "github_auth_poll",
        "github_auth_cancel",
        "github_web_auth_start",
        "github_web_auth_poll",
        "github_web_auth_cancel",
        "github_token_reveal",
        "github_token_clear",
        "github_owner_set",
        "config_check",
        "config_branches",
        "config_save",
        "resource_init",
        "resource_use",
    ):
        assert removed not in desktop_api.ACTIONS


def test_desktop_config_get_does_not_expose_token_metadata(monkeypatch) -> None:
    monkeypatch.setattr(desktop_api, "load_raw_config", Config)

    result = desktop_api._config_get({})

    assert "token_source" not in result
    assert "token_preview" not in result
    assert "config_token_preview" not in result
    assert "env_token_active" not in result
    assert "github" not in result["config"]


def test_desktop_runtime_config_drops_cli_token(monkeypatch) -> None:
    cfg = Config(github=GithubConfig(token="cli-only-secret"))
    monkeypatch.setattr(desktop_api, "load_application_config", lambda: cfg)

    desktop_cfg = desktop_api.load_config()

    assert desktop_cfg.github.token == ""


def test_desktop_doctor_drops_cli_only_token_check(monkeypatch) -> None:
    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(
        desktop_api,
        "build_doctor_checks",
        lambda _config: [
            {"id": "git", "status": "ok"},
            {"id": "github_token", "status": "warning"},
        ],
    )

    result = desktop_api.run_action("doctor")

    assert result["ok"] is True
    assert result["data"]["checks"] == [{"id": "git", "status": "ok"}]
