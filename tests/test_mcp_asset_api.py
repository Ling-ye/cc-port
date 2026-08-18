from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastmcp.tools import ToolResult

from cc_port.agent.contracts import AssetReconcileContextWire
from cc_port.interfaces import mcp_server
from cc_port.services.approval import (
    approve_approval_request,
    load_approval_request,
)
from cc_port.services.asset_reconcile import (
    AssetReconcileInvalidRequest,
    AssetReconcileStaleContext,
)
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
        "asset_reconcile_context",
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


def _empty_reconcile_context() -> AssetReconcileContextWire:
    return AssetReconcileContextWire.model_validate(
        {
            "context_schema_version": 1,
            "context_id": "a" * 64,
            "generated_at": "2026-08-17T00:00:00Z",
            "completeness": "complete",
            "scope": {
                "mode": "configured-enabled",
                "arbitrary_filesystem_scan": False,
                "includes_saved_projects": True,
                "saved_project_count": 0,
                "scanned_saved_project_count": 0,
                "unavailable_saved_project_count": 0,
                "include_same": False,
            },
            "remote": {
                "configured": False,
                "freshness": "unavailable",
                "available": False,
                "branch": "",
                "commit": "",
                "registry_status": "unavailable",
                "issues": [],
            },
            "coverage": [],
            "summary": {
                "logical_resource_count": 0,
                "comparison_count": 0,
                "profile_count": 0,
                "kind_counts": {},
                "status_counts": {},
                "same_count": 0,
                "local_only_count": 0,
                "remote_only_count": 0,
                "review_count": 0,
                "blocked_count": 0,
            },
            "page": {
                "offset": 0,
                "page_size": 100,
                "returned": 0,
                "total": 0,
                "has_more": False,
                "next_cursor": "",
            },
            "resources": [],
        }
    )


def test_mcp_reconcile_context_is_read_only_and_forwards_strict_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = object()
    captured: dict[str, object] = {}

    def fake_reconcile(**kwargs: object) -> AssetReconcileContextWire:
        captured.update(kwargs)
        return _empty_reconcile_context()

    monkeypatch.setattr(mcp_server, "load_config", lambda: config)
    monkeypatch.setattr(
        mcp_server,
        "build_asset_reconcile_context",
        fake_reconcile,
    )

    result = mcp_server.asset_reconcile_context(
        context_schema_version=1,
        cursor="",
        page_size=100,
        include_same=False,
    )
    called = asyncio.run(
        mcp_server.mcp.call_tool(
            "asset_reconcile_context",
            {
                "context_schema_version": 1,
                "cursor": "",
                "page_size": 100,
                "include_same": False,
            },
        )
    )

    assert captured == {
        "config": config,
        "context_schema_version": 1,
        "cursor": "",
        "page_size": 100,
        "include_same": False,
    }
    assert result.ok is True
    assert result.status == "ready"
    assert result.data == _empty_reconcile_context()
    assert called.structured_content == result.model_dump(mode="json")
    tool = {
        item.name: item for item in asyncio.run(mcp_server.mcp.list_tools())
    }["asset_reconcile_context"]
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.idempotentHint is True
    assert tool.annotations.openWorldHint is True


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (
            AssetReconcileInvalidRequest(r"Invalid cursor at C:\Users\Alice\private"),
            "invalid-request",
            "asset_reconcile_context_invalid",
        ),
        (
            AssetReconcileStaleContext(r"Changed at C:\Users\Alice\private"),
            "stale-context",
            "asset_reconcile_context_stale",
        ),
    ],
)
def test_mcp_reconcile_context_returns_safe_structured_failures(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_status: str,
    expected_code: str,
) -> None:
    monkeypatch.setattr(mcp_server, "load_config", object)
    monkeypatch.setattr(
        mcp_server,
        "build_asset_reconcile_context",
        lambda **_kwargs: (_ for _ in ()).throw(error),
    )

    result = mcp_server.asset_reconcile_context(cursor="opaque")

    assert result.ok is False
    assert result.status == expected_status
    assert result.error is not None
    assert result.error.code == expected_code
    encoded = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    assert "Alice" not in encoded
    assert "${PRIVATE_PATH}" in encoded


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
                        path=Path("memories/claude-memory-deadbeef"),
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
                            content_path=Path("/home/lingye/.claude/projects/slot/memory"),
                            link_target="/home/lingye/private/memory",
                            tool_id="claude-code",
                            environment_kind="wsl",
                            environment_name="Ubuntu",
                            display_name="Claude Code (Ubuntu)",
                            memory_layout="projects",
                        )
                    ],
                    plugin_marketplace="team-tools",
                    plugin_marketplace_source="acme/claude-plugins",
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
    assert result.ok is True
    assert result.data is not None
    instance = result.data.resources[0].local_instances[0]
    assert instance.platform == "claude-wsl-ubuntu"
    assert instance.tool_id == "claude-code"
    assert instance.environment_kind == "wsl"
    assert instance.environment_name == "Ubuntu"
    assert instance.path == "${PRIVATE_PATH}"
    assert instance.content_path == "${PRIVATE_PATH}"
    assert instance.link_target == "${PRIVATE_PATH}"
    assert result.data.resources[0].plugin_marketplace == "team-tools"
    assert result.data.resources[0].plugin_marketplace_source == "acme/claude-plugins"
    remote_path = result.data.resources[0].remote.path
    assert remote_path is not None
    assert remote_path.replace("\\", "/") == "memories/claude-memory-deadbeef"
    assert wire_result.structured_content == result.model_dump(mode="json")


def test_mcp_single_asset_plan_and_apply_forward_revalidation_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = object()
    captured: dict[str, object] = {}
    service_plan = AssetActionPlan(
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
        overwrite_unmanaged=True,
        link_target_confirmed=True,
        tool_id="claude-code",
        environment_kind="windows",
        environment_name="windows",
    )

    def fake_plan(action: str, **kwargs: object) -> AssetActionPlan:
        captured.setdefault("plans", []).append((action, kwargs))
        return service_plan

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
    monkeypatch.setattr(
        mcp_server,
        "load_asset_action_plan",
        lambda operation_id, **_kwargs: service_plan,
    )

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
    assert planned.data is not None
    assert planned.data.requires_approval is True
    assert planned.data.approval_status == "pending"
    pending_apply = mcp_server.asset_action_apply(
        planned.data.operation_id,
        planned.data.plan_hash,
        planned.data.approval_id,
    )
    pending_called = asyncio.run(
        mcp_server.mcp.call_tool(
            "asset_action_apply",
            {
                "operation_id": planned.data.operation_id,
                "plan_hash": planned.data.plan_hash,
                "approval_id": planned.data.approval_id,
            },
        )
    )
    assert isinstance(pending_apply, ToolResult)
    assert pending_apply.is_error is True
    assert pending_apply.structured_content is not None
    assert pending_apply.structured_content["status"] == "needs-confirmation"
    assert pending_apply.structured_content["error"]["code"] == "approval_required"
    assert (
        pending_apply.structured_content["error"]["details"]["approval_id"]
        == planned.data.approval_id
    )
    assert pending_called.is_error is True
    assert pending_called.structured_content == pending_apply.structured_content
    approve_approval_request(planned.data.approval_id)
    applied = mcp_server.asset_action_apply(
        planned.data.operation_id,
        planned.data.plan_hash,
        planned.data.approval_id,
    )

    action, kwargs = captured["plans"][0]
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
    assert planned.data.operation_id == "plan-1"
    assert len(planned.data.plan_hash) == 64
    assert planned.data.platform == "claude-windows"
    assert planned.data.tool_id == "claude-code"
    assert planned.data.environment_kind == "windows"
    assert planned.data.target_path == "${PRIVATE_PATH}"
    approval = load_approval_request(planned.data.approval_id)
    assert approval.metadata["operation_id"] == planned.data.operation_id
    assert approval.metadata["plan_hash"] == planned.data.plan_hash
    assert approval.metadata["scope"] == {
        "action": "set-platform-install-name",
        "resource_key": "memory:claude-memory-deadbeef",
        "target_resource_key": "memory:claude-memory-deadbeef",
        "platform": "claude-windows",
        "local_instance_id": "claude-windows:memory:slot",
        "new_name": "",
        "new_install_name": "-c-Users-Lingye-project",
        "overwrite_unmanaged": True,
        "link_target_confirmed": True,
        "remote_commit": "abc123",
        "remote_repo_hash": "",
        "remote_branch": "",
    }
    assert "C:/Users/Lingye" not in str(approval.metadata)
    assert applied.data is not None
    assert applied.data.approval_status == "consumed"
    assert load_approval_request(planned.data.approval_id).status == "consumed"
    assert applied.data.local_path == "${PRIVATE_PATH}"


def test_mcp_batch_plan_and_apply_preserve_hash_profiles_and_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = object()
    captured: dict[str, object] = {}
    plan_hash = "a" * 64
    replacement_hash = "b" * 64

    def fake_plan(direction: str, **kwargs: object) -> AssetBatchPlan:
        captured.setdefault("plans", []).append((direction, kwargs))
        return AssetBatchPlan(
            direction=direction,
            resource_keys=list(kwargs["resource_keys"]),
            target_platforms=list(kwargs["target_platforms"]),
            remote_commit="abc123",
            plan_hash=plan_hash,
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
            plan_hash=replacement_hash,
            items=[],
            executable_count=1,
            blocked_count=0,
            skipped_count=0,
        )
        return AssetBatchResult(
            status="stale-plan",
            plan_hash=replacement_hash,
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
            "reference_origin": {
                "origin_type": "git",
                "repo": "owner/repo",
                "url": "https://user:supersecretvalue@example.test/repo",
                "local_path": "C:/Users/Lingye/private/repo",
            },
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
        "download", resource_keys, plan_hash, "not-yet", "0" * 32
    )
    assert isinstance(applied, ToolResult)
    assert planned.data is not None
    assert planned.data.requires_approval is True
    assert planned.data.approval_status == "pending"
    approve_approval_request(planned.data.approval_id)
    applied = mcp_server.asset_batch_apply(
        "download",
        resource_keys,
        plan_hash,
        planned.data.operation_id,
        planned.data.approval_id,
        target_platforms=target_platforms,
        choices=choices,
    )

    _, plan_kwargs = captured["plans"][0]
    _, apply_kwargs = captured["apply"]
    plan_choice = plan_kwargs["choices"][0]
    apply_choice = apply_kwargs["choices"][0]
    assert isinstance(plan_choice, AssetBatchChoice)
    assert isinstance(apply_choice, AssetBatchChoice)
    assert plan_choice.platform == "claude-wsl-ubuntu"
    assert plan_choice.local_instance_id == "claude-wsl-ubuntu:memory:slot"
    assert plan_choice.link_target_confirmed is True
    assert plan_choice.reference_origin == {
        "origin_type": "git",
        "repo": "owner/repo",
        "url": "https://user:supersecretvalue@example.test/repo",
        "local_path": "C:/Users/Lingye/private/repo",
    }
    assert apply_choice == plan_choice
    assert plan_kwargs["target_platforms"] == target_platforms
    assert apply_kwargs["target_platforms"] == target_platforms
    assert apply_kwargs["expected_plan_hash"] == plan_hash
    assert plan_kwargs["config"] is config
    assert apply_kwargs["config"] is config
    assert planned.data.plan_hash == plan_hash
    approval = load_approval_request(planned.data.approval_id)
    assert approval.metadata["operation_id"] == planned.data.operation_id
    assert approval.metadata["plan_hash"] == plan_hash
    assert approval.metadata["scope"]["direction"] == "download"
    assert approval.metadata["scope"]["resource_keys"] == resource_keys
    assert approval.metadata["scope"]["target_platforms"] == target_platforms
    review_choice = approval.metadata["scope"]["choices"][0]
    assert review_choice["platform"] == "claude-wsl-ubuntu"
    assert review_choice["local_instance_id"] == "claude-wsl-ubuntu:memory:slot"
    assert "supersecretvalue" not in json.dumps(approval.metadata)
    assert "${SECRET_VALUE}" in review_choice["reference_origin"]["url"]
    assert review_choice["reference_origin"]["local_path"] == "${PRIVATE_PATH}"
    assert applied.status == "stale-plan"
    assert applied.data is not None
    assert applied.data.approval_status == "consumed"
    assert applied.data.plan_hash == replacement_hash
    assert applied.data.stale_plan is not None
    assert applied.data.stale_plan.target_platforms == target_platforms
    assert applied.data.stale_plan.approval_status == "pending"
    assert applied.data.stale_plan.approval_id != planned.data.approval_id
    assert load_approval_request(planned.data.approval_id).status == "consumed"


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
