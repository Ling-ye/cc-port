from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.tools import ToolResult
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from cc_port.agent.contracts import (
    PUBLIC_PRIVATE_PATH_PLACEHOLDER,
    asset_action_plan_hash,
    to_public_wire_value,
    to_wire_value,
)
from cc_port.interfaces import mcp_server
from cc_port.services.approval import (
    approve_approval_request,
    load_approval_request,
)
from cc_port.services.asset_sync import (
    AssetActionPlan,
    AssetBatchPlan,
    AssetContentDiff,
    AssetDiffFile,
)
from cc_port.services.operation_history import OperationHistoryEntry
from cc_port.services.operation_state import OperationTarget
from cc_port.services.registry_audit import (
    RegistryAuditIssue,
    RegistryRepairPlan,
    RegistryRepairResult,
)

CORE_TOOLS = {
    "cc_port_status",
    "cc_port_doctor",
    "asset_inventory",
    "asset_reconcile_context",
    "asset_content_diff",
    "asset_action_plan",
    "asset_action_apply",
    "asset_batch_plan",
    "asset_batch_apply",
    "registry_repair_plan",
    "registry_repair_apply",
    "operation_detail",
}


def _tools() -> dict[str, object]:
    return {
        tool.name: tool
        for tool in asyncio.run(mcp_server.mcp.list_tools())
    }


def test_public_path_projection_is_separate_from_internal_wire_identity() -> None:
    raw = {
        "path": "skills/demo",
        "target_path": r"C:\Users\Alice\.codex\skills\demo",
        "content_path": "/home/alice/.claude/projects/demo/memory",
        "link_target": "../private-target",
        "backup_path": "backups/private-target",
        "nested": {
            "path": r"\\server\share\Users\Alice\demo",
            "message": (
                r"Cannot read C:\Users\Alice\private\demo or "
                "/home/alice/private/demo"
            ),
        },
    }

    internal = to_wire_value(raw)
    public = to_public_wire_value(raw)

    assert internal["target_path"] == raw["target_path"]
    assert internal["content_path"] == raw["content_path"]
    assert internal["link_target"] == raw["link_target"]
    assert public["path"] == "skills/demo"
    assert public["target_path"] == PUBLIC_PRIVATE_PATH_PLACEHOLDER
    assert public["content_path"] == PUBLIC_PRIVATE_PATH_PLACEHOLDER
    assert public["link_target"] == PUBLIC_PRIVATE_PATH_PLACEHOLDER
    assert public["backup_path"] == PUBLIC_PRIVATE_PATH_PLACEHOLDER
    assert public["nested"]["path"] == PUBLIC_PRIVATE_PATH_PLACEHOLDER
    assert "Alice" not in public["nested"]["message"]
    assert public["nested"]["message"].count(PUBLIC_PRIVATE_PATH_PLACEHOLDER) == 2
    other_identity = {**raw, "target_path": r"C:\Users\Bob\.codex\skills\demo"}
    assert asset_action_plan_hash(raw) != asset_action_plan_hash(other_identity)
    assert (
        to_public_wire_value(raw)["target_path"]
        == to_public_wire_value(other_identity)["target_path"]
        == PUBLIC_PRIVATE_PATH_PLACEHOLDER
    )


def test_doctor_projects_private_paths_from_public_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "load_config", lambda: object())
    monkeypatch.setattr(
        mcp_server,
        "build_doctor_checks",
        lambda _config: [
            {
                "id": "profile-path",
                "label": "Profile path",
                "status": "warning",
                "ok": False,
                "detail": r"Cannot read C:\Users\Alice\.codex\config.toml",
                "profile": SimpleNamespace(name="codex-windows"),
            }
        ],
    )

    result = mcp_server.cc_port_doctor()

    assert result.data is not None
    assert result.data.status == "warning"
    assert result.data.checks[0].detail == (
        f"Cannot read {PUBLIC_PRIVATE_PATH_PLACEHOLDER}"
    )


def test_core_tools_publish_closed_envelope_schemas_and_annotations() -> None:
    tools = _tools()

    assert CORE_TOOLS <= tools.keys()
    assert all(tool.annotations is not None for tool in tools.values())
    for name in CORE_TOOLS:
        tool = tools[name]
        assert tool.output_schema is not None
        assert set(tool.output_schema["properties"]) == {
            "contract_version",
            "ok",
            "status",
            "data",
            "error",
        }
        assert tool.output_schema.get("additionalProperties") is False
        assert tool.annotations is not None
        assert tool.annotations.destructiveHint is not None
        assert tool.annotations.idempotentHint is not None
        assert tool.annotations.openWorldHint is not None
        assert tool.annotations.readOnlyHint is not None

    for name in ("asset_action_plan", "asset_batch_plan", "registry_repair_plan"):
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.destructiveHint is False

    action_schema = tools["asset_action_plan"].parameters
    assert action_schema["properties"]["action"]["enum"] == [
        "download",
        "upload",
        "copy-to-local",
        "copy-to-remote",
        "set-platform-install-name",
    ]
    assert action_schema["properties"]["platform"]["minLength"] == 1
    batch_schema = tools["asset_batch_apply"].parameters
    choice_schema = batch_schema["properties"]["choices"]["anyOf"][0]["items"]
    assert choice_schema["additionalProperties"] is False
    assert set(choice_schema["properties"]) == {
        "resource_key",
        "platform",
        "local_instance_id",
        "resolution",
        "new_name",
        "overwrite_unmanaged",
        "plugin_track",
        "ownership_confirmed",
        "link_target_confirmed",
        "reference_origin",
        "plugin_dependencies",
    }
    assert batch_schema["properties"]["plan_hash"]["pattern"] == "^[0-9a-f]{64}$"
    assert batch_schema["properties"]["operation_id"]["pattern"] == (
        "^asset-batch:[0-9a-f]{64}$"
    )
    assert {"plan_hash", "operation_id", "approval_id"} <= set(
        batch_schema["required"]
    )
    registry_apply_schema = tools["registry_repair_apply"].parameters
    assert registry_apply_schema["properties"]["operation_id"]["pattern"] == (
        "^registry-repair:[0-9a-f]{64}$"
    )
    assert registry_apply_schema["properties"]["approval_id"]["pattern"] == (
        "^[0-9a-f]{32}$"
    )
    assert not any("approve" in name or "reject" in name for name in tools)


def test_status_declares_safe_workflow_and_legacy_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = SimpleNamespace(
        profiles=[
            SimpleNamespace(
                name="claude-wsl-ubuntu",
                effective_tool_id="claude-code",
                environment_kind="wsl",
                environment_name="Ubuntu",
                effective_display_name="Claude Code (Ubuntu)",
                enabled=True,
            )
        ]
    )
    monkeypatch.setattr(
        mcp_server,
        "load_config",
        lambda: SimpleNamespace(platforms=profiles),
    )

    result = mcp_server.cc_port_status()

    assert result.ok is True
    assert result.data is not None
    assert result.data.automation_policy == "plan-apply-verify"
    assert result.data.approval_mode == "desktop-only"
    assert result.data.approval_tools_exposed is False
    assert result.data.transport == "stdio"
    assert result.data.profiles[0].profile_id == "claude-wsl-ubuntu"
    assert "asset_action_plan" in result.data.recommended_tools
    assert "publish_local_skill" in result.data.legacy_direct_write_tools

    tools = _tools()
    for name in result.data.legacy_direct_write_tools:
        assert tools[name].title.startswith("Legacy")
        assert tools[name].meta["cc_port"]["preferred"] is False


def test_diff_registry_and_operation_tools_reuse_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = object()
    captured: dict[str, object] = {}
    repair_hash = "c" * 64
    monkeypatch.setattr(mcp_server, "load_config", lambda: config)

    def fake_diff(resource_key: str, local_instance_id: str, **kwargs: object):
        captured["diff"] = (resource_key, local_instance_id, kwargs)
        return AssetContentDiff(
            resource_key=resource_key,
            local_instance_id=local_instance_id,
            platform="claude-wsl-ubuntu",
            remote_commit="abc123",
            files=[
                AssetDiffFile(
                    path="MEMORY.md",
                    status="modified",
                    diff="-before\n+after",
                )
            ],
            added_files=0,
            deleted_files=0,
            modified_files=1,
            binary_files=0,
        )

    issue = RegistryAuditIssue(
        id="issue-1",
        code="missing-entry",
        severity="warning",
        message="Missing entry",
        actions=["add"],
    )
    repair_plan = RegistryRepairPlan(
        remote_commit="abc123",
        repo_url="https://example.test/resources.git",
        branch="main",
        registry_status="issues",
        issues=[issue],
        choices=[],
        registry_diff="+ resource",
        plan_hash=repair_hash,
        executable_count=1,
        blocked_count=0,
        repairable=True,
    )

    def fake_repair_plan(**kwargs: object):
        captured["repair_plan"] = kwargs
        return repair_plan

    def fake_repair_apply(**kwargs: object):
        captured["repair_apply"] = kwargs
        return RegistryRepairResult(
            status="succeeded",
            plan_hash=repair_hash,
            remote_commit="def456",
            message="done",
        )

    def fake_operation(operation_id: str):
        captured["operation"] = operation_id
        return OperationHistoryEntry(
            operation_id=operation_id,
            kind="asset-download",
            status="succeeded",
            started_at="2026-08-11T00:00:00Z",
            finished_at="2026-08-11T00:00:01Z",
            message="done",
            rolled_back=False,
            target_count=1,
            changed_target_count=1,
            restorable=False,
            metadata={"resource_key": "memory:demo"},
            targets=[
                OperationTarget(
                    path="C:/Users/test/.claude/projects/demo/memory",
                    action="write",
                    backup_path="C:/Users/test/AppData/Local/cc-port/backups/demo",
                    verified=True,
                )
            ],
        )

    monkeypatch.setattr(mcp_server, "build_asset_content_diff", fake_diff)
    monkeypatch.setattr(mcp_server, "build_registry_repair_plan", fake_repair_plan)
    monkeypatch.setattr(mcp_server, "apply_registry_repair", fake_repair_apply)
    monkeypatch.setattr(mcp_server, "load_operation_detail", fake_operation)

    diff = mcp_server.asset_content_diff("memory:demo", "claude:memory:demo")
    planned = mcp_server.registry_repair_plan()
    assert planned.data is not None
    assert planned.data.requires_approval is True
    assert planned.data.approval_status == "pending"
    approve_approval_request(planned.data.approval_id)
    applied = mcp_server.registry_repair_apply(
        planned.data.plan_hash,
        planned.data.operation_id,
        planned.data.approval_id,
    )
    detail = mcp_server.operation_detail("operation-1")

    assert diff.data is not None
    assert diff.data.files[0].status == "modified"
    assert captured["diff"] == (
        "memory:demo",
        "claude:memory:demo",
        {"config": config, "enabled_profiles_only": True},
    )
    assert planned.data.plan_hash == repair_hash
    approval = load_approval_request(planned.data.approval_id)
    assert approval.metadata["kind"] == "registry-repair"
    assert approval.metadata["operation_id"] == planned.data.operation_id
    assert approval.metadata["plan_hash"] == repair_hash
    assert approval.metadata["scope"] == {
        "branch": "main",
        "choices": [],
        "plan_hash": repair_hash,
    }
    assert approval.metadata["planned_items"] == [
        {
            "issue_id": "issue-1",
            "resource_key": "",
            "kind": "",
            "name": "",
            "default_action": "keep",
        }
    ]
    assert captured["repair_plan"] == {"config": config, "choices": []}
    assert applied.ok is True
    assert captured["repair_apply"] == {
        "expected_plan_hash": repair_hash,
        "config": config,
        "choices": [],
    }
    assert applied.data is not None
    assert applied.data.approval_status == "consumed"
    assert load_approval_request(planned.data.approval_id).status == "consumed"
    assert detail.data is not None
    assert detail.data.targets[0].verified is True
    assert detail.data.targets[0].path == PUBLIC_PRIVATE_PATH_PLACEHOLDER
    assert detail.data.targets[0].backup_path == PUBLIC_PRIVATE_PATH_PLACEHOLDER
    assert captured["operation"] == "operation-1"


def test_service_exception_is_structured_mcp_execution_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_server,
        "build_asset_content_diff",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError(r"no local instance at C:\Users\Alice\private\memory")
        ),
    )

    direct = mcp_server.asset_content_diff("memory:demo", "missing-instance")
    called = asyncio.run(
        mcp_server.mcp.call_tool(
            "asset_content_diff",
            {
                "resource_key": "memory:demo",
                "local_instance_id": "missing-instance",
            },
        )
    )

    assert isinstance(direct, ToolResult)
    assert direct.is_error is True
    assert direct.structured_content is not None
    assert direct.structured_content["ok"] is False
    assert direct.structured_content["error"]["code"] == "asset_content_diff.failed"
    assert "Alice" not in direct.structured_content["error"]["message"]
    assert (
        PUBLIC_PRIVATE_PATH_PLACEHOLDER
        in direct.structured_content["error"]["message"]
    )
    assert called.is_error is True
    assert called.structured_content == direct.structured_content
    assert json.loads(called.content[0].text) == direct.structured_content


def test_stale_batch_gets_new_approval_and_old_grant_cannot_authorize_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = object()
    old_hash = "d" * 64
    new_hash = "e" * 64
    calls = 0

    def plan(plan_hash: str) -> AssetBatchPlan:
        return AssetBatchPlan(
            direction="upload",
            resource_keys=["skill:demo"],
            target_platforms=[],
            remote_commit="abc123" if plan_hash == old_hash else "def456",
            plan_hash=plan_hash,
            items=[],
            executable_count=1,
            blocked_count=0,
            skipped_count=0,
        )

    def fake_plan(*_args: object, **_kwargs: object) -> AssetBatchPlan:
        nonlocal calls
        calls += 1
        return plan(old_hash if calls == 1 else new_hash)

    monkeypatch.setattr(mcp_server, "load_config", lambda: config)
    monkeypatch.setattr(mcp_server, "build_asset_batch_plan", fake_plan)
    monkeypatch.setattr(
        mcp_server,
        "apply_asset_batch_plan",
        lambda *_args, **_kwargs: pytest.fail("stale plans must not reach apply"),
    )

    planned = mcp_server.asset_batch_plan("upload", ["skill:demo"])
    assert planned.data is not None
    approve_approval_request(planned.data.approval_id)

    stale = mcp_server.asset_batch_apply(
        "upload",
        ["skill:demo"],
        planned.data.plan_hash,
        planned.data.operation_id,
        planned.data.approval_id,
    )

    assert stale.status == "stale-plan"
    assert stale.data is not None
    assert stale.data.stale_plan is not None
    replacement = stale.data.stale_plan
    assert replacement.plan_hash == new_hash
    assert replacement.approval_status == "pending"
    assert replacement.approval_id != planned.data.approval_id
    assert load_approval_request(planned.data.approval_id).status == "rejected"

    denied = mcp_server.asset_batch_apply(
        "upload",
        ["skill:demo"],
        replacement.plan_hash,
        replacement.operation_id,
        planned.data.approval_id,
    )
    assert isinstance(denied, ToolResult)
    assert denied.is_error is True
    assert denied.structured_content is not None
    assert denied.structured_content["status"] == "needs-confirmation"
    assert denied.structured_content["error"]["code"] == "approval_required"
    assert load_approval_request(planned.data.approval_id).status == "rejected"


def test_stale_action_invalidates_old_approval_before_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def action_plan(*, commit: str, operation_id: str) -> AssetActionPlan:
        return AssetActionPlan(
            operation_id=operation_id,
            action="download",
            resource_key="skill:demo",
            target_resource_key="skill:demo",
            kind="skill",
            name="demo",
            platform="codex-windows",
            local_instance_id="",
            local_locator="",
            remote_commit=commit,
            remote_target_exists=True,
            remote_target_fingerprint=f"remote-{commit}",
            local_source_fingerprint="",
            target_path=Path(r"C:\Users\Alice\.codex\skills\demo"),
            target_exists=False,
            target_fingerprint="",
            target_managed=False,
            remote_repo_hash="repo-hash",
            remote_branch="main",
        )

    stored = action_plan(commit="old", operation_id="action-old")
    replacement = action_plan(commit="new", operation_id="action-new")
    monkeypatch.setattr(mcp_server, "load_config", lambda: object())
    monkeypatch.setattr(
        mcp_server,
        "build_asset_action_plan",
        lambda *_args, **_kwargs: stored,
    )
    monkeypatch.setattr(
        mcp_server,
        "load_asset_action_plan",
        lambda *_args, **_kwargs: stored,
    )
    monkeypatch.setattr(
        mcp_server,
        "_rebuild_action_plan",
        lambda *_args, **_kwargs: replacement,
    )
    monkeypatch.setattr(
        mcp_server,
        "apply_asset_action_plan",
        lambda *_args, **_kwargs: pytest.fail("stale action must not reach apply"),
    )

    planned = mcp_server.asset_action_plan(
        "download",
        "skill",
        "demo",
        "codex-windows",
    )
    assert planned.data is not None
    approve_approval_request(planned.data.approval_id)

    stale = mcp_server.asset_action_apply(
        planned.data.operation_id,
        planned.data.plan_hash,
        planned.data.approval_id,
    )

    assert stale.status == "stale-plan"
    assert stale.data is not None
    assert stale.data.stale_plan is not None
    assert stale.data.stale_plan.approval_status == "pending"
    assert stale.data.stale_plan.approval_id != planned.data.approval_id
    assert load_approval_request(planned.data.approval_id).status == "rejected"


def test_stale_registry_invalidates_old_approval_before_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_hash = "2" * 64
    new_hash = "3" * 64
    calls = 0

    def repair_plan(*, plan_hash: str, branch: str) -> RegistryRepairPlan:
        return RegistryRepairPlan(
            remote_commit="old" if plan_hash == old_hash else "new",
            repo_url="https://example.test/resources.git",
            branch=branch,
            registry_status="issues",
            issues=[],
            choices=[],
            registry_diff="+ resource",
            plan_hash=plan_hash,
            executable_count=1,
            blocked_count=0,
            repairable=True,
        )

    def fake_plan(**_kwargs: object) -> RegistryRepairPlan:
        nonlocal calls
        calls += 1
        if calls == 1:
            return repair_plan(plan_hash=old_hash, branch="main")
        return repair_plan(plan_hash=new_hash, branch="release")

    monkeypatch.setattr(mcp_server, "load_config", lambda: object())
    monkeypatch.setattr(mcp_server, "build_registry_repair_plan", fake_plan)
    monkeypatch.setattr(
        mcp_server,
        "apply_registry_repair",
        lambda **_kwargs: pytest.fail("stale Registry plan must not reach apply"),
    )

    planned = mcp_server.registry_repair_plan()
    assert planned.data is not None
    approve_approval_request(planned.data.approval_id)

    stale = mcp_server.registry_repair_apply(
        planned.data.plan_hash,
        planned.data.operation_id,
        planned.data.approval_id,
    )

    assert stale.status == "stale-plan"
    assert stale.data is not None
    assert stale.data.stale_plan is not None
    assert stale.data.stale_plan.branch == "release"
    assert stale.data.stale_plan.approval_status == "pending"
    assert stale.data.stale_plan.approval_id != planned.data.approval_id
    assert load_approval_request(planned.data.approval_id).status == "rejected"


@pytest.mark.parametrize(
    ("executable_count", "blocked_count"),
    [(0, 0), (1, 1)],
)
def test_batch_plan_without_unblocked_writes_creates_no_approval(
    monkeypatch: pytest.MonkeyPatch,
    executable_count: int,
    blocked_count: int,
) -> None:
    plan_hash = "f" * 64
    monkeypatch.setattr(mcp_server, "load_config", lambda: object())
    monkeypatch.setattr(
        mcp_server,
        "build_asset_batch_plan",
        lambda *_args, **_kwargs: AssetBatchPlan(
            direction="upload",
            resource_keys=["skill:demo"],
            target_platforms=[],
            remote_commit="abc123",
            plan_hash=plan_hash,
            items=[],
            executable_count=executable_count,
            blocked_count=blocked_count,
            skipped_count=0,
        ),
    )

    result = mcp_server.asset_batch_plan("upload", ["skill:demo"])

    assert result.data is not None
    assert result.data.requires_approval is False
    assert result.data.approval_id == ""
    assert result.data.approval_status == "not-required"


def test_write_failure_after_consume_is_fail_closed_and_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_hash = "1" * 64
    apply_calls = 0
    stable = AssetBatchPlan(
        direction="upload",
        resource_keys=["skill:demo"],
        target_platforms=[],
        remote_commit="abc123",
        plan_hash=plan_hash,
        items=[],
        executable_count=1,
        blocked_count=0,
        skipped_count=0,
    )
    monkeypatch.setattr(mcp_server, "load_config", lambda: object())
    monkeypatch.setattr(
        mcp_server,
        "build_asset_batch_plan",
        lambda *_args, **_kwargs: stable,
    )

    def fail_apply(*_args: object, **_kwargs: object):
        nonlocal apply_calls
        apply_calls += 1
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(mcp_server, "apply_asset_batch_plan", fail_apply)
    planned = mcp_server.asset_batch_plan("upload", ["skill:demo"])
    assert planned.data is not None
    approve_approval_request(planned.data.approval_id)

    failed = mcp_server.asset_batch_apply(
        "upload",
        ["skill:demo"],
        planned.data.plan_hash,
        planned.data.operation_id,
        planned.data.approval_id,
    )
    retried = mcp_server.asset_batch_apply(
        "upload",
        ["skill:demo"],
        planned.data.plan_hash,
        planned.data.operation_id,
        planned.data.approval_id,
    )

    assert isinstance(failed, ToolResult)
    assert failed.is_error is True
    assert failed.structured_content is not None
    assert failed.structured_content["error"]["details"]["retryable"] is False
    assert isinstance(retried, ToolResult)
    assert retried.is_error is True
    assert apply_calls == 1
    assert load_approval_request(planned.data.approval_id).status == "consumed"


def test_real_stdio_initialize_list_and_call_smoke() -> None:
    async def run_smoke() -> tuple[str | None, set[str], object]:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "cc_port.interfaces.mcp_server"],
            cwd=Path.cwd(),
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                called = await session.call_tool("cc_port_status", {})
                return (
                    initialized.instructions,
                    {tool.name for tool in listed.tools},
                    called,
                )

    instructions, names, called = asyncio.run(run_smoke())

    assert instructions is not None
    assert "plan_hash" in instructions
    assert CORE_TOOLS <= names
    assert called.isError is False
    assert called.structuredContent["status"] == "ready"
    assert called.structuredContent["data"]["transport"] == "stdio"
