from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cc_port.interfaces import mcp_server
from cc_port.services.asset_sync import (
    AssetActionPlan,
    AssetActionResult,
    AssetBatchChoice,
    AssetBatchPlan,
    AssetBatchResult,
    AssetInventory,
    AssetLocalInstance,
    AssetRemoteState,
    AssetResourceRow,
)


def test_mcp_registers_structured_profile_aware_asset_tools() -> None:
    tools = {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}

    expected = {
        "asset_inventory",
        "asset_action_plan",
        "asset_action_apply",
        "asset_batch_plan",
        "asset_batch_apply",
    }
    assert expected <= tools.keys()
    assert {
        "action",
        "kind",
        "name",
        "platform",
        "local_instance_id",
        "new_install_name",
        "link_target_confirmed",
    } <= tools["asset_action_plan"].parameters["properties"].keys()
    assert {
        "direction",
        "resource_keys",
        "target_platforms",
        "choices",
        "plan_hash",
    } <= tools["asset_batch_apply"].parameters["properties"].keys()
    assert all(tools[name].output_schema is not None for name in expected)


def test_mcp_asset_inventory_forwards_scope_and_preserves_profile_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = object()
    captured: dict[str, object] = {}

    def fake_inventory(**kwargs: object) -> AssetInventory:
        captured.update(kwargs)
        return AssetInventory(
            branch="main",
            remote_commit="abc123",
            repo_url="https://example.test/resources.git",
            remote_available=True,
            remote_warning="",
            scanned_local=True,
            generated_at="2026-08-11T00:00:00Z",
            legacy_write_blocker="",
            rows=[],
            resources=[
                AssetResourceRow(
                    resource_key="memory:claude-memory-deadbeef",
                    kind="memory",
                    name="claude-memory-deadbeef",
                    description="Claude memory",
                    description_source="local",
                    local_status="single",
                    remote_status="missing",
                    status="local-only",
                    remote=AssetRemoteState(
                        exists=False,
                        status="missing",
                        writable=True,
                        read_only=False,
                        commit="abc123",
                        path=None,
                        description="",
                    ),
                    local_instances=[
                        AssetLocalInstance(
                            id="claude-wsl-ubuntu:memory:slot",
                            platform="claude-wsl-ubuntu",
                            install_name="-home-lingye-project",
                            path=Path("/home/lingye/.claude/projects/slot/memory"),
                            ownership="unmanaged",
                            fingerprint="local-fingerprint",
                            description="Claude memory",
                            status="local-only",
                            tool_id="claude-code",
                            environment_kind="wsl",
                            environment_name="Ubuntu",
                            display_name="Claude Code (Ubuntu)",
                            memory_layout="projects",
                        )
                    ],
                )
            ],
        )

    monkeypatch.setattr(mcp_server, "load_config", lambda: config)
    monkeypatch.setattr(mcp_server, "build_asset_inventory", fake_inventory)

    result = mcp_server.asset_inventory(
        scan_local=True,
        refresh_remote=False,
        scan_global=False,
        project_ids=["project-1"],
    )
    wire_result = asyncio.run(
        mcp_server.mcp.call_tool(
            "asset_inventory",
            {
                "scan_local": True,
                "refresh_remote": False,
                "scan_global": False,
                "project_ids": ["project-1"],
            },
        )
    )

    assert captured == {
        "config": config,
        "scan_local": True,
        "refresh_remote": False,
        "scan_global": False,
        "project_ids": ["project-1"],
    }
    assert "rows" not in result
    instance = result["resources"][0]["local_instances"][0]
    assert instance["platform"] == "claude-wsl-ubuntu"
    assert instance["tool_id"] == "claude-code"
    assert instance["environment_kind"] == "wsl"
    assert instance["environment_name"] == "Ubuntu"
    assert instance["path"].replace("\\", "/").endswith("projects/slot/memory")
    assert wire_result.structured_content == result


def test_mcp_single_asset_plan_and_apply_forward_revalidation_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = object()
    captured: dict[str, object] = {}

    def fake_plan(action: str, **kwargs: object) -> AssetActionPlan:
        captured["plan"] = (action, kwargs)
        return AssetActionPlan(
            operation_id="plan-1",
            action="set-platform-install-name",
            resource_key="memory:claude-memory-deadbeef",
            target_resource_key="memory:claude-memory-deadbeef",
            kind="memory",
            name="claude-memory-deadbeef",
            platform="claude-windows",
            local_instance_id="claude-windows:memory:slot",
            local_locator="discovered",
            remote_commit="abc123",
            remote_target_exists=True,
            remote_target_fingerprint="remote-fingerprint",
            local_source_fingerprint="local-fingerprint",
            target_path=Path("C:/Users/Lingye/.claude/projects/slot/memory"),
            target_exists=True,
            target_fingerprint="target-fingerprint",
            target_managed=True,
            new_install_name="-c-Users-Lingye-project",
            tool_id="claude-code",
            environment_kind="windows",
            environment_name="windows",
        )

    def fake_apply(operation_id: str, **kwargs: object) -> AssetActionResult:
        captured["apply"] = (operation_id, kwargs)
        return AssetActionResult(
            operation_id=operation_id,
            action="set-platform-install-name",
            status="succeeded",
            resource_key="memory:claude-memory-deadbeef",
            target_resource_key="memory:claude-memory-deadbeef",
            platform="claude-windows",
            message="done",
            local_path=Path("C:/Users/Lingye/.claude/projects/slot/memory"),
        )

    monkeypatch.setattr(mcp_server, "load_config", lambda: config)
    monkeypatch.setattr(mcp_server, "build_asset_action_plan", fake_plan)
    monkeypatch.setattr(mcp_server, "apply_asset_action_plan", fake_apply)

    planned = mcp_server.asset_action_plan(
        "set-platform-install-name",
        "memory",
        "claude-memory-deadbeef",
        "claude-windows",
        local_instance_id="claude-windows:memory:slot",
        new_install_name="-c-Users-Lingye-project",
        overwrite_unmanaged=True,
        link_target_confirmed=True,
    )
    applied = mcp_server.asset_action_apply("plan-1")

    action, kwargs = captured["plan"]
    assert action == "set-platform-install-name"
    assert kwargs == {
        "kind": "memory",
        "name": "claude-memory-deadbeef",
        "platform": "claude-windows",
        "local_instance_id": "claude-windows:memory:slot",
        "new_name": "",
        "new_install_name": "-c-Users-Lingye-project",
        "overwrite_unmanaged": True,
        "link_target_confirmed": True,
        "config": config,
    }
    assert captured["apply"] == ("plan-1", {"config": config})
    assert planned["operation_id"] == "plan-1"
    assert planned["platform"] == "claude-windows"
    assert planned["tool_id"] == "claude-code"
    assert planned["environment_kind"] == "windows"
    assert applied["local_path"].replace("\\", "/").endswith("projects/slot/memory")


def test_mcp_batch_plan_and_apply_preserve_hash_profiles_and_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = object()
    captured: dict[str, object] = {}

    def fake_plan(direction: str, **kwargs: object) -> AssetBatchPlan:
        captured["plan"] = (direction, kwargs)
        return AssetBatchPlan(
            direction=direction,
            resource_keys=list(kwargs["resource_keys"]),
            target_platforms=list(kwargs["target_platforms"]),
            remote_commit="abc123",
            plan_hash="batch-hash",
            items=[],
            executable_count=1,
            blocked_count=0,
            skipped_count=0,
        )

    def fake_apply(direction: str, **kwargs: object) -> AssetBatchResult:
        captured["apply"] = (direction, kwargs)
        stale_plan = AssetBatchPlan(
            direction=direction,
            resource_keys=list(kwargs["resource_keys"]),
            target_platforms=list(kwargs["target_platforms"]),
            remote_commit="def456",
            plan_hash="new-hash",
            items=[],
            executable_count=0,
            blocked_count=1,
            skipped_count=0,
        )
        return AssetBatchResult(
            status="stale-plan",
            plan_hash="new-hash",
            results=[],
            stale_plan=stale_plan,
        )

    monkeypatch.setattr(mcp_server, "load_config", lambda: config)
    monkeypatch.setattr(mcp_server, "build_asset_batch_plan", fake_plan)
    monkeypatch.setattr(mcp_server, "apply_asset_batch_plan", fake_apply)
    choices = [
        {
            "resource_key": "memory:claude-memory-deadbeef",
            "platform": "claude-wsl-ubuntu",
            "local_instance_id": "claude-wsl-ubuntu:memory:slot",
            "resolution": "rename",
            "new_name": "claude-memory-copy",
            "overwrite_unmanaged": True,
            "plugin_track": "reference",
            "ownership_confirmed": True,
            "link_target_confirmed": True,
            "reference_origin": {"origin_type": "git", "repo": "owner/repo"},
            "plugin_dependencies": {"demo": "1.0.0"},
        }
    ]
    resource_keys = ["memory:claude-memory-deadbeef"]
    target_platforms = ["claude-windows", "claude-wsl-ubuntu"]

    planned = mcp_server.asset_batch_plan(
        "download",
        resource_keys,
        target_platforms=target_platforms,
        choices=choices,
    )
    applied = mcp_server.asset_batch_apply(
        "download",
        resource_keys,
        "batch-hash",
        target_platforms=target_platforms,
        choices=choices,
    )

    _, plan_kwargs = captured["plan"]
    _, apply_kwargs = captured["apply"]
    plan_choice = plan_kwargs["choices"][0]
    apply_choice = apply_kwargs["choices"][0]
    assert isinstance(plan_choice, AssetBatchChoice)
    assert isinstance(apply_choice, AssetBatchChoice)
    assert plan_choice.platform == "claude-wsl-ubuntu"
    assert plan_choice.local_instance_id == "claude-wsl-ubuntu:memory:slot"
    assert plan_choice.link_target_confirmed is True
    assert plan_choice.reference_origin == {"origin_type": "git", "repo": "owner/repo"}
    assert apply_choice == plan_choice
    assert plan_kwargs["target_platforms"] == target_platforms
    assert apply_kwargs["target_platforms"] == target_platforms
    assert apply_kwargs["expected_plan_hash"] == "batch-hash"
    assert plan_kwargs["config"] is config
    assert apply_kwargs["config"] is config
    assert planned["plan_hash"] == "batch-hash"
    assert applied["status"] == "stale-plan"
    assert applied["plan_hash"] == "new-hash"
    assert applied["stale_plan"]["target_platforms"] == target_platforms


def test_mcp_batch_choice_rejects_missing_resource_identity() -> None:
    with pytest.raises(ValueError, match="requires resource_key"):
        mcp_server._asset_batch_choices([{"resolution": "skip"}])


def test_mcp_batch_choice_does_not_coerce_confirmation_strings() -> None:
    with pytest.raises(ValueError, match="overwrite_unmanaged must be a boolean"):
        mcp_server._asset_batch_choices(
            [
                {
                    "resource_key": "memory:claude-memory-deadbeef",
                    "overwrite_unmanaged": "false",
                }
            ]
        )
