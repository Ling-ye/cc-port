from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from cc_port.agent.contracts import AssetReconcileContextWire
from cc_port.core.config import Config
from cc_port.core.models import RegistryItem
from cc_port.core.platforms import PlatformProfile, PlatformsConfig
from cc_port.core.resource_detection import DetectedRemoteResource
from cc_port.interfaces import cli
from cc_port.services.ai_integration import (
    AiIntegrationPlan,
    AiIntegrationResult,
    AiIntegrationTarget,
    AiIntegrationVerification,
)
from cc_port.services.approval import ApprovalRequest, approve_approval_request
from cc_port.services.asset_reconcile import (
    AssetReconcileInvalidRequest,
    AssetReconcileStaleContext,
)
from cc_port.services.asset_sync import (
    AssetActionPlan,
    AssetActionResult,
    AssetBatchPlan,
    AssetBatchResult,
    AssetContentDiff,
    AssetDiffFile,
    AssetInventory,
)
from cc_port.services.resource_repo import ResourceRepoInfo

runner = CliRunner()


def _empty_asset_reconcile_context(
    *,
    page_size: int = 100,
    include_same: bool = False,
) -> AssetReconcileContextWire:
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
                "include_same": include_same,
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
                "page_size": page_size,
                "returned": 0,
                "total": 0,
                "has_more": False,
                "next_cursor": "",
            },
            "resources": [],
        }
    )


def _integration_target(*, actions: list[str] | None = None) -> AiIntegrationTarget:
    return AiIntegrationTarget(
        profile_id="codex.win",
        tool_id="codex",
        display_name="Codex Windows",
        environment_kind="windows",
        environment_name="",
        available=True,
        skill_path="C:/Users/test/.agents/skills/cc-port",
        mcp_config_path="C:/Users/test/.codex/config.toml",
        mcp_config_format="codex-toml",
        skill_status="missing",
        mcp_status="missing",
        actions=list(actions or ["install-skill", "register-mcp"]),
    )


def _integration_plan(action: str = "install") -> AiIntegrationPlan:
    return AiIntegrationPlan(
        operation_id="a" * 32,
        action=action,  # type: ignore[arg-type]
        profile_id="codex.win",
        command="C:/Program Files/CC Port/cc-port.exe",
        command_args=["mcp", "--stdio"],
        command_source="test",
        target=_integration_target(
            actions=(
                ["install-skill", "register-mcp"]
                if action == "install"
                else ["remove-skill", "remove-mcp"]
            )
        ),
        plan_hash="plan-hash",
        blocked=False,
        blockers=[],
        requires_approval=True,
        approval_id="b" * 32,
    )


def _approval(status: str = "pending") -> ApprovalRequest:
    return ApprovalRequest(
        approval_id="b" * 32,
        kind="ai-integration",
        operation_id="a" * 32,
        plan_hash="plan-hash",
        scope_hash="scope-hash",
        summary="Install CC Port AI integration for codex.win",
        status=status,  # type: ignore[arg-type]
        created_at="2026-08-11T00:00:00+00:00",
        expires_at="2026-08-11T01:00:00+00:00",
    )


def test_asset_reconcile_cli_forwards_strict_request_and_prints_one_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = object()
    captured: dict[str, object] = {}

    def fake_reconcile(**kwargs: object) -> AssetReconcileContextWire:
        captured.update(kwargs)
        return _empty_asset_reconcile_context(page_size=200, include_same=True)

    monkeypatch.setattr(cli, "_load", lambda: config)
    monkeypatch.setattr(cli, "build_asset_reconcile_context", fake_reconcile)

    result = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "asset",
            "reconcile",
            "--context-schema-version",
            "1",
            "--page-size",
            "200",
            "--include-same",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["contract_version"] == 1
    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert payload["data"]["context_schema_version"] == 1
    assert payload["data"]["page"]["page_size"] == 200
    assert payload["data"]["scope"]["include_same"] is True
    assert captured == {
        "config": config,
        "context_schema_version": 1,
        "cursor": "",
        "page_size": 200,
        "include_same": True,
    }
    assert "\x1b[" not in result.stdout
    document, end = json.JSONDecoder().raw_decode(result.stdout)
    assert document == payload
    assert result.stdout[end:].strip() == ""


def test_asset_reconcile_cli_rejects_non_decimal_page_size_with_one_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "build_asset_reconcile_context",
        lambda **_kwargs: pytest.fail("invalid CLI input must fail before the service"),
    )

    result = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "asset",
            "reconcile",
            "--page-size",
            "abc",
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["contract_version"] == 1
    assert payload["ok"] is False
    assert payload["status"] == "invalid-request"
    assert payload["data"] is None
    assert payload["error"]["code"] == "asset_reconcile_context_invalid"
    assert payload["error"]["message"] == "page_size must be an ASCII decimal integer."
    assert "\x1b[" not in result.stdout
    document, end = json.JSONDecoder().raw_decode(result.stdout)
    assert document == payload
    assert result.stdout[end:].strip() == ""


@pytest.mark.parametrize(
    ("error", "expected_exit", "expected_status", "expected_code"),
    [
        (
            AssetReconcileInvalidRequest(r"Invalid cursor at C:\Users\Alice\private"),
            2,
            "invalid-request",
            "asset_reconcile_context_invalid",
        ),
        (
            AssetReconcileStaleContext(r"Changed at C:\Users\Alice\private"),
            3,
            "stale-context",
            "asset_reconcile_context_stale",
        ),
    ],
)
def test_asset_reconcile_cli_returns_stable_safe_failure_envelope(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_exit: int,
    expected_status: str,
    expected_code: str,
) -> None:
    monkeypatch.setattr(cli, "_load", Config)
    monkeypatch.setattr(
        cli,
        "build_asset_reconcile_context",
        lambda **_kwargs: (_ for _ in ()).throw(error),
    )

    result = runner.invoke(
        cli.app,
        ["--non-interactive", "asset", "reconcile", "--cursor", "opaque", "--json"],
    )

    assert result.exit_code == expected_exit
    payload = json.loads(result.stdout)
    assert payload["contract_version"] == 1
    assert payload["ok"] is False
    assert payload["status"] == expected_status
    assert payload["error"]["code"] == expected_code
    assert "Alice" not in result.stdout
    assert "${PRIVATE_PATH}" in result.stdout
    document, end = json.JSONDecoder().raw_decode(result.stdout)
    assert document == payload
    assert result.stdout[end:].strip() == ""


def test_collect_cli_forwards_portable_mcp_config(monkeypatch) -> None:
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

    monkeypatch.setattr(cli, "_load", Config)
    monkeypatch.setattr(cli, "detect_remote_resource", lambda *_args, **_kwargs: detected)
    monkeypatch.setattr(cli.publisher, "add_external_skill", fake_add_external_skill)

    config = '{"command":"npx","args":["-y","@example/demo-mcp@1.0.0"]}'
    result = runner.invoke(
        cli.app,
        [
            "collect",
            detected.repo_url,
            "--type",
            "mcp",
            "--mcp-config",
            config,
            "--no-push",
        ],
    )

    assert result.exit_code == 0
    assert captured["repo"] == detected.repo_url
    assert captured["mcp_config"] == {
        "command": "npx",
        "args": ["-y", "@example/demo-mcp@1.0.0"],
    }


def test_asset_cli_list_plan_and_apply(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(
        cli,
        "console",
        Console(force_terminal=True, color_system="standard"),
    )
    monkeypatch.setattr(cli, "_load", Config)
    monkeypatch.setattr(
        cli,
        "build_asset_inventory",
        lambda **kwargs: AssetInventory(
            branch="main",
            remote_commit="abc123",
            repo_url="https://example.test/resources.git",
            remote_available=True,
            remote_warning="",
            scanned_local=kwargs["scan_local"],
            generated_at="2026-07-17T00:00:00Z",
            legacy_write_blocker="",
            rows=[],
        ),
    )

    def fake_plan(action: str, **kwargs) -> AssetActionPlan:
        calls.append(
            (
                "plan",
                (
                    action,
                    kwargs["kind"],
                    kwargs["name"],
                    kwargs["platform"],
                    kwargs["link_target_confirmed"],
                ),
            )
        )
        return AssetActionPlan(
            operation_id="plan-1",
            action="download",
            resource_key="skill:demo",
            target_resource_key="skill:demo",
            kind="skill",
            name="demo",
            platform="cursor",
            local_instance_id="",
            local_locator="expected",
            remote_commit="abc123",
            remote_target_exists=True,
            remote_target_fingerprint="remote",
            local_source_fingerprint="",
            target_path=None,
            target_exists=False,
            target_fingerprint="",
            target_managed=False,
            link_target_confirmed=kwargs["link_target_confirmed"],
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

    monkeypatch.setattr(cli, "build_asset_action_plan", fake_plan)
    monkeypatch.setattr(cli, "apply_asset_action_plan", fake_apply)
    monkeypatch.setattr(
        cli,
        "load_asset_action_plan",
        lambda *_args, **_kwargs: fake_plan(
            "download", kind="skill", name="demo", platform="cursor", link_target_confirmed=True
        ),
    )

    listed = runner.invoke(cli.app, ["asset", "list", "--cached-remote", "--json"])
    planned = runner.invoke(
        cli.app,
        [
            "asset",
            "plan",
            "download",
            "--kind",
            "skill",
            "--name",
            "demo",
            "--platform",
            "cursor",
            "--link-target-confirmed",
            "--json",
        ],
    )
    assert listed.exit_code == 0
    listed_payload = json.loads(listed.stdout)
    assert listed_payload["contract_version"] == 1
    assert listed_payload["ok"] is True
    assert listed_payload["status"] == "succeeded"
    assert listed_payload["data"]["branch"] == "main"
    assert "rows" not in listed_payload["data"]
    assert planned.exit_code == 0
    planned_payload = json.loads(planned.stdout)
    assert planned_payload["status"] == "planned"
    assert planned_payload["data"]["operation_id"] == "plan-1"
    assert len(planned_payload["data"]["plan_hash"]) == 64
    approval_id = planned_payload["data"]["approval_id"]
    approval_request = cli.load_approval_request(approval_id)
    assert approval_request.metadata["profile_id"] == "cursor"
    assert approval_request.metadata["link_target_confirmed"] is True
    assert "target_path" not in approval_request.metadata
    approve_approval_request(approval_id)
    applied = runner.invoke(
        cli.app,
        ["asset", "apply", "plan-1", "--approval-id", approval_id, "--json"],
    )
    assert applied.exit_code == 0
    applied_payload = json.loads(applied.stdout)
    assert applied_payload["status"] == "succeeded"
    assert applied_payload["data"]["status"] == "succeeded"
    for result in (listed, planned, applied):
        assert "\x1b[" not in result.stdout
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, dict)
        assert not _contains_desktop_message_ref(parsed)
    assert calls == [
        ("plan", ("download", "skill", "demo", "cursor", True)),
        ("plan", ("download", "skill", "demo", "cursor", True)),
        ("plan", ("download", "skill", "demo", "cursor", True)),
        ("apply", "plan-1"),
    ]


def test_asset_cli_diff_uses_machine_envelope(monkeypatch) -> None:
    config = object()
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "_load", lambda: config)

    def fake_diff(
        resource_key: str,
        local_instance_id: str,
        **kwargs: object,
    ) -> AssetContentDiff:
        captured.update(kwargs)
        return AssetContentDiff(
            resource_key=resource_key,
            local_instance_id=local_instance_id,
            platform="codex.win",
            remote_commit="abc123",
            files=[
                AssetDiffFile(
                    path="SKILL.md",
                    status="modified",
                    diff="@@ -1 +1 @@\n-old\n+token=ghp_1234567890abcdef",
                )
            ],
            added_files=0,
            deleted_files=0,
            modified_files=1,
            binary_files=0,
        )

    monkeypatch.setattr(
        cli,
        "build_asset_content_diff",
        fake_diff,
    )

    result = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "asset",
            "diff",
            "--resource",
            "skill:demo",
            "--local-instance-id",
            "codex.win:skill:demo",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["contract_version"] == 1
    assert payload["ok"] is True
    assert payload["data"]["files"][0]["status"] == "modified"
    assert payload["data"]["modified_files"] == 1
    assert "ghp_1234567890abcdef" not in result.stdout
    assert "${SECRET_VALUE}" in result.stdout
    assert captured == {"config": config, "enabled_profiles_only": True}


def test_non_interactive_asset_apply_never_approves_pending_request(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "_load", Config)
    plan = AssetActionPlan(
        operation_id="1" * 32,
        action="download",
        resource_key="skill:demo",
        target_resource_key="skill:demo",
        kind="skill",
        name="demo",
        platform="codex.win",
        local_instance_id="codex.win:skill:demo",
        local_locator="expected",
        remote_commit="abc123",
        remote_target_exists=True,
        remote_target_fingerprint="remote",
        local_source_fingerprint="",
        target_path=None,
        target_exists=False,
        target_fingerprint="",
        target_managed=False,
    )
    _plan_hash, pending = cli._create_asset_action_approval(plan)
    assert pending is not None
    monkeypatch.setattr(cli, "load_asset_action_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(
        cli,
        "build_asset_action_plan",
        lambda *_args, **_kwargs: plan,
    )
    monkeypatch.setattr(
        cli.typer,
        "confirm",
        lambda *_args, **_kwargs: pytest.fail("machine mode must not prompt"),
    )
    monkeypatch.setattr(
        cli,
        "apply_asset_action_plan",
        lambda *_args, **_kwargs: pytest.fail("pending approval must not apply"),
    )

    result = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "asset",
            "apply",
            plan.operation_id,
            "--approval-id",
            pending.approval_id,
            "--yes",
            "--json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["status"] == "needs-confirmation"
    assert payload["error"]["code"] == "approval_required"
    assert cli.load_approval_request(pending.approval_id).status == "pending"


def test_interactive_asset_apply_yes_still_requires_desktop_approval(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "_load", Config)
    plan = AssetActionPlan(
        operation_id="4" * 32,
        action="download",
        resource_key="skill:demo",
        target_resource_key="skill:demo",
        kind="skill",
        name="demo",
        platform="codex.win",
        local_instance_id="codex.win:skill:demo",
        local_locator="expected",
        remote_commit="abc123",
        remote_target_exists=True,
        remote_target_fingerprint="remote",
        local_source_fingerprint="",
        target_path=None,
        target_exists=False,
        target_fingerprint="",
        target_managed=False,
    )
    _plan_hash, pending = cli._create_asset_action_approval(plan)
    assert pending is not None
    monkeypatch.setattr(cli, "load_asset_action_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(cli, "build_asset_action_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(
        cli.typer,
        "confirm",
        lambda *_args, **_kwargs: pytest.fail("asset apply must not offer CLI approval"),
    )
    monkeypatch.setattr(
        cli,
        "apply_asset_action_plan",
        lambda *_args, **_kwargs: pytest.fail("pending approval must not apply"),
    )

    result = runner.invoke(
        cli.app,
        [
            "asset",
            "apply",
            plan.operation_id,
            "--approval-id",
            pending.approval_id,
            "--yes",
        ],
    )

    assert result.exit_code == 3
    assert "Desktop" in result.stdout
    assert cli.load_approval_request(pending.approval_id).status == "pending"


def test_stale_asset_apply_returns_new_pending_approval(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "_load", Config)
    original = AssetActionPlan(
        operation_id="2" * 32,
        action="download",
        resource_key="skill:demo",
        target_resource_key="skill:demo",
        kind="skill",
        name="demo",
        platform="codex.win",
        local_instance_id="codex.win:skill:demo",
        local_locator="expected",
        remote_commit="old-commit",
        remote_target_exists=True,
        remote_target_fingerprint="old-remote",
        local_source_fingerprint="",
        target_path=None,
        target_exists=False,
        target_fingerprint="",
        target_managed=False,
    )
    replacement = AssetActionPlan(
        **{
            **original.__dict__,
            "operation_id": "3" * 32,
            "remote_commit": "new-commit",
            "remote_target_fingerprint": "new-remote",
        }
    )
    _old_hash, old_approval = cli._create_asset_action_approval(original)
    assert old_approval is not None
    approve_approval_request(old_approval.approval_id)
    monkeypatch.setattr(cli, "load_asset_action_plan", lambda *_args, **_kwargs: original)
    monkeypatch.setattr(
        cli,
        "apply_asset_action_plan",
        lambda *_args, **_kwargs: AssetActionResult(
            operation_id=original.operation_id,
            action="download",
            status="stale-target",
            resource_key="skill:demo",
            target_resource_key="skill:demo",
            platform="codex.win",
            message="target changed",
        ),
    )
    monkeypatch.setattr(
        cli,
        "build_asset_action_plan",
        lambda *_args, **_kwargs: replacement,
    )

    result = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "asset",
            "apply",
            original.operation_id,
            "--approval-id",
            old_approval.approval_id,
            "--json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["status"] == "stale-plan"
    assert payload["data"]["approval_status"] == "rejected"
    stale_plan = payload["data"]["stale_plan"]
    assert stale_plan["operation_id"] == replacement.operation_id
    assert stale_plan["approval_status"] == "pending"
    assert stale_plan["approval_id"] != old_approval.approval_id
    assert cli.load_approval_request(old_approval.approval_id).status == "rejected"


def _contains_desktop_message_ref(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            key.endswith(("_ref", "_refs")) or _contains_desktop_message_ref(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_desktop_message_ref(item) for item in value)
    return False


def test_legacy_resource_pull_warns_but_keeps_behavior(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_load", Config)
    monkeypatch.setattr(
        cli,
        "pull_resource_repo",
        lambda _config: ResourceRepoInfo(
            repo_name="resources",
            local_path="C:/resources",
            registry_path="C:/resources/registry.yaml",
            repo_url="",
            remote_url="",
            branch="main",
            current_branch="main",
            exists=True,
            is_git_repo=True,
            dirty=False,
        ),
    )

    result = runner.invoke(cli.app, ["resource", "pull"])

    assert result.exit_code == 0
    assert "Deprecated: use `cc-port asset list`" in result.stdout
    assert "resources" in result.stdout


def test_asset_batch_cli_and_removed_environment_command(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "_load", Config)
    batch_hash = "c" * 64

    def fake_plan(direction: str, **kwargs) -> AssetBatchPlan:
        calls.append(("plan", (direction, kwargs["resource_keys"], kwargs["target_platforms"])))
        return AssetBatchPlan(
            direction=direction,
            resource_keys=kwargs["resource_keys"],
            target_platforms=kwargs["target_platforms"],
            remote_commit="abc123",
            plan_hash=batch_hash,
            items=[],
            executable_count=1,
            blocked_count=0,
            skipped_count=0,
        )

    def fake_apply(direction: str, **kwargs) -> AssetBatchResult:
        calls.append(("apply", (direction, kwargs["expected_plan_hash"])))
        return AssetBatchResult(status="succeeded", plan_hash=batch_hash, results=[])

    monkeypatch.setattr(cli, "build_asset_batch_plan", fake_plan)
    monkeypatch.setattr(cli, "apply_asset_batch_plan", fake_apply)

    planned = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "asset",
            "download",
            "--resource",
            "skill:demo",
            "--platform",
            "cursor",
            "--dry-run",
            "--json",
        ],
    )
    assert planned.exit_code == 0
    approval_id = json.loads(planned.stdout)["data"]["approval_id"]
    approve_approval_request(approval_id)
    downloaded = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "asset",
            "download",
            "--resource",
            "skill:demo",
            "--platform",
            "cursor",
            "--approval-id",
            approval_id,
            "--json",
        ],
    )
    environment = runner.invoke(cli.app, ["env", "discover"])

    assert downloaded.exit_code == 0
    downloaded_payload = json.loads(downloaded.stdout)
    assert downloaded_payload["contract_version"] == 1
    assert downloaded_payload["status"] == "succeeded"
    assert downloaded_payload["data"]["plan_hash"] == batch_hash
    assert environment.exit_code != 0
    assert calls == [
        ("plan", ("download", ["skill:demo"], ["cursor"])),
        ("plan", ("download", ["skill:demo"], ["cursor"])),
        ("apply", ("download", batch_hash)),
    ]


def test_asset_batch_choices_allow_multiple_sources_for_one_resource(tmp_path: Path) -> None:
    choices_path = tmp_path / "choices.yaml"
    choices_path.write_text(
        """items:
  - resource_key: skill:demo
    local_instance_id: cursor:skill:demo
    resolution: rename
    new_name: demo-cursor
    link_target_confirmed: true
  - resource_key: skill:demo
    local_instance_id: codex:skill:demo
    resolution: rename
    new_name: demo-codex
""",
        encoding="utf-8",
    )

    choices = cli._load_asset_batch_choices(choices_path)

    assert [choice.local_instance_id for choice in choices] == [
        "cursor:skill:demo",
        "codex:skill:demo",
    ]
    assert [choice.new_name for choice in choices] == ["demo-cursor", "demo-codex"]
    assert choices[0].link_target_confirmed is True


def test_asset_batch_choices_reject_coerced_boolean_and_unknown_choice(
    tmp_path: Path,
) -> None:
    choices_path = tmp_path / "choices.yaml"
    choices_path.write_text(
        """items:
  - resource_key: skill:demo
    ownership_confirmed: "false"
    unexpected: true
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid asset batch choices"):
        cli._load_asset_batch_choices(choices_path)


def test_asset_batch_plan_and_apply_reuse_strict_request_and_hash(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "_load", Config)
    reviewed_hash = "d" * 64
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "direction": "upload",
                "resource_keys": ["skill:demo"],
                "target_platforms": [],
                "choices": [
                    {
                        "resource_key": "skill:demo",
                        "local_instance_id": "codex.win:skill:demo",
                        "resolution": "rename",
                        "new_name": "demo-copy",
                        "ownership_confirmed": True,
                        "link_target_confirmed": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def fake_plan(direction: str, **kwargs) -> AssetBatchPlan:
        choice = kwargs["choices"][0]
        calls.append(
            (
                "plan",
                (
                    direction,
                    kwargs["resource_keys"],
                    choice.local_instance_id,
                    choice.link_target_confirmed,
                ),
            )
        )
        return AssetBatchPlan(
            direction=direction,
            resource_keys=kwargs["resource_keys"],
            target_platforms=kwargs["target_platforms"],
            remote_commit="abc123",
            plan_hash=reviewed_hash,
            items=[],
            executable_count=1,
            blocked_count=0,
            skipped_count=0,
        )

    def fake_apply(direction: str, **kwargs) -> AssetBatchResult:
        choice = kwargs["choices"][0]
        calls.append(
            (
                "apply",
                (
                    direction,
                    kwargs["resource_keys"],
                    kwargs["expected_plan_hash"],
                    choice.local_instance_id,
                    choice.link_target_confirmed,
                ),
            )
        )
        return AssetBatchResult(status="succeeded", plan_hash=reviewed_hash, results=[])

    monkeypatch.setattr(cli, "build_asset_batch_plan", fake_plan)
    monkeypatch.setattr(cli, "apply_asset_batch_plan", fake_apply)

    planned = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "asset",
            "batch-plan",
            "--request",
            str(request_path),
            "--json",
        ],
    )
    assert planned.exit_code == 0
    planned_payload = json.loads(planned.stdout)
    approval_id = planned_payload["data"]["approval_id"]
    approval_request = cli.load_approval_request(approval_id)
    assert approval_request.metadata["target_platforms"] == []
    assert approval_request.metadata["choices"][0]["local_instance_id"] == ("codex.win:skill:demo")
    assert approval_request.metadata["choices"][0]["link_target_confirmed"] is True
    assert "reference_origin" not in approval_request.metadata["choices"][0]
    approve_approval_request(approval_id)
    applied = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "asset",
            "batch-apply",
            "--request",
            str(request_path),
            "--plan-hash",
            reviewed_hash,
            "--approval-id",
            approval_id,
            "--json",
        ],
    )

    assert planned_payload["data"]["plan_hash"] == reviewed_hash
    assert applied.exit_code == 0
    assert json.loads(applied.stdout)["status"] == "succeeded"
    assert calls == [
        ("plan", ("upload", ["skill:demo"], "codex.win:skill:demo", True)),
        ("plan", ("upload", ["skill:demo"], "codex.win:skill:demo", True)),
        (
            "apply",
            (
                "upload",
                ["skill:demo"],
                reviewed_hash,
                "codex.win:skill:demo",
                True,
            ),
        ),
    ]


def test_asset_batch_plan_rejects_string_boolean_with_json_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        cli,
        "build_asset_batch_plan",
        lambda *_args, **_kwargs: pytest.fail("service must not receive invalid request"),
    )
    request_path = tmp_path / "invalid.json"
    request_path.write_text(
        json.dumps(
            {
                "direction": "upload",
                "resource_keys": ["skill:demo"],
                "choices": [
                    {
                        "resource_key": "skill:demo",
                        "link_target_confirmed": "false",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "asset",
            "batch-plan",
            "--request",
            str(request_path),
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["status"] == "invalid-request"
    assert payload["error"]["code"] == "invalid_batch_request"


@pytest.mark.parametrize(
    ("command", "error_code", "missing"),
    [
        (
            ["asset", "plan", "--json"],
            "asset_plan_inputs_required",
            ["action", "kind", "name", "platform"],
        ),
        (
            ["asset", "apply", "--json"],
            "operation_id_required",
            ["operation_id"],
        ),
        (
            ["asset", "batch-plan", "--json"],
            "batch_request_required",
            ["request"],
        ),
        (
            ["asset", "batch-apply", "--json"],
            "batch_apply_inputs_required",
            ["request", "plan_hash", "approval_id"],
        ),
    ],
)
def test_recommended_machine_asset_commands_report_missing_inputs_as_one_envelope(
    command: list[str],
    error_code: str,
    missing: list[str],
) -> None:
    result = runner.invoke(cli.app, ["--non-interactive", *command])

    assert result.exit_code == 2
    assert "Usage:" not in result.output
    assert "\x1b[" not in result.output
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["contract_version"] == 1
    assert payload["ok"] is False
    assert payload["status"] == "invalid-request"
    assert payload["error"]["code"] == error_code
    assert payload["data"]["missing"] == missing


def test_machine_asset_apply_requires_reviewed_approval_before_loading_plan(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "load_asset_action_plan",
        lambda *_args, **_kwargs: pytest.fail("missing approval must fail before plan loading"),
    )

    result = runner.invoke(
        cli.app,
        ["--non-interactive", "asset", "apply", "operation-1", "--json"],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["status"] == "needs-confirmation"
    assert payload["error"]["code"] == "approval_id_required"
    assert payload["data"]["missing"] == ["approval_id"]


def test_machine_wire_envelope_redacts_private_local_paths(capsys) -> None:
    private_windows_path = r"C:\Users\Alice\AppData\Local\cc-port\backup"
    private_posix_path = "/home/alice/.config/cc-port/config.toml"

    cli._print_wire_success(
        {
            "target_path": private_windows_path,
            "message": f"Unable to read {private_posix_path}",
            "portable_path": "skills/demo",
        },
        status="ready",
    )

    rendered = capsys.readouterr().out
    payload = json.loads(rendered)
    assert private_windows_path not in rendered
    assert private_posix_path not in rendered
    assert payload["data"]["target_path"] == "${PRIVATE_PATH}"
    assert payload["data"]["message"] == "Unable to read ${PRIVATE_PATH}"
    assert payload["data"]["portable_path"] == "skills/demo"


def test_asset_batch_apply_reports_stale_plan_with_safe_exit_code(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "_load", Config)
    fresh_hash = "f" * 64
    reviewed_hash = "e" * 64
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "direction": "download",
                "resource_keys": ["skill:demo"],
                "target_platforms": ["codex.win"],
            }
        ),
        encoding="utf-8",
    )
    fresh_plan = AssetBatchPlan(
        direction="download",
        resource_keys=["skill:demo"],
        target_platforms=["codex.win"],
        remote_commit="new-commit",
        plan_hash=fresh_hash,
        items=[],
        executable_count=1,
        blocked_count=0,
        skipped_count=0,
    )
    reviewed_plan = AssetBatchPlan(
        direction="download",
        resource_keys=["skill:demo"],
        target_platforms=["codex.win"],
        remote_commit="old-commit",
        plan_hash=reviewed_hash,
        items=[],
        executable_count=1,
        blocked_count=0,
        skipped_count=0,
    )
    request = cli.AssetBatchRequestWire(
        direction="download",
        resource_keys=["skill:demo"],
        target_platforms=["codex.win"],
    )
    reviewed_approval = cli._create_asset_batch_approval(reviewed_plan, request)
    assert reviewed_approval is not None
    approve_approval_request(reviewed_approval.approval_id)
    monkeypatch.setattr(
        cli,
        "build_asset_batch_plan",
        lambda *_args, **_kwargs: fresh_plan,
    )
    monkeypatch.setattr(
        cli,
        "apply_asset_batch_plan",
        lambda *_args, **_kwargs: pytest.fail("stale plans must not reach apply"),
    )

    result = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "asset",
            "batch-apply",
            "--request",
            str(request_path),
            "--plan-hash",
            reviewed_hash,
            "--approval-id",
            reviewed_approval.approval_id,
            "--json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["status"] == "stale-plan"
    assert payload["error"]["code"] == "stale_plan"
    assert payload["data"]["approval_id"] == reviewed_approval.approval_id
    assert payload["data"]["approval_status"] == "rejected"
    assert payload["data"]["stale_plan"]["plan_hash"] == fresh_hash
    assert payload["data"]["stale_plan"]["approval_status"] == "pending"
    assert cli.load_approval_request(reviewed_approval.approval_id).status == "rejected"


def test_non_interactive_batch_never_prompts_and_requires_confirmation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "_load", Config)
    batch_hash = "9" * 64
    plan = AssetBatchPlan(
        direction="download",
        resource_keys=["skill:demo"],
        target_platforms=["codex.win"],
        remote_commit="abc123",
        plan_hash=batch_hash,
        items=[],
        executable_count=1,
        blocked_count=0,
        skipped_count=0,
    )
    request = cli.AssetBatchRequestWire(
        direction="download",
        resource_keys=["skill:demo"],
        target_platforms=["codex.win"],
    )
    pending = cli._create_asset_batch_approval(plan, request)
    assert pending is not None
    monkeypatch.setattr(
        cli,
        "build_asset_batch_plan",
        lambda _direction, **_kwargs: plan,
    )
    monkeypatch.setattr(
        cli.typer,
        "confirm",
        lambda *_args, **_kwargs: pytest.fail("non-interactive mode must never prompt"),
    )
    monkeypatch.setattr(
        cli,
        "apply_asset_batch_plan",
        lambda *_args, **_kwargs: pytest.fail("unconfirmed batch must not apply"),
    )

    result = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "asset",
            "download",
            "--resource",
            "skill:demo",
            "--platform",
            "codex.win",
            "--approval-id",
            pending.approval_id,
            "--yes",
            "--json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["status"] == "needs-confirmation"
    assert payload["error"]["code"] == "approval_required"


def test_interactive_batch_apply_yes_still_requires_desktop_approval(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "_load", Config)
    batch_hash = "8" * 64
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "direction": "download",
                "resource_keys": ["skill:demo"],
                "target_platforms": ["codex.win"],
            }
        ),
        encoding="utf-8",
    )
    request = cli.AssetBatchRequestWire(
        direction="download",
        resource_keys=["skill:demo"],
        target_platforms=["codex.win"],
    )
    plan = AssetBatchPlan(
        direction="download",
        resource_keys=list(request.resource_keys),
        target_platforms=list(request.target_platforms),
        remote_commit="abc123",
        plan_hash=batch_hash,
        items=[],
        executable_count=1,
        blocked_count=0,
        skipped_count=0,
    )
    pending = cli._create_asset_batch_approval(plan, request)
    assert pending is not None
    monkeypatch.setattr(cli, "build_asset_batch_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(
        cli.typer,
        "confirm",
        lambda *_args, **_kwargs: pytest.fail("batch apply must not offer CLI approval"),
    )
    monkeypatch.setattr(
        cli,
        "apply_asset_batch_plan",
        lambda *_args, **_kwargs: pytest.fail("pending approval must not apply"),
    )

    result = runner.invoke(
        cli.app,
        [
            "asset",
            "batch-apply",
            "--request",
            str(request_path),
            "--plan-hash",
            batch_hash,
            "--approval-id",
            pending.approval_id,
            "--yes",
        ],
    )

    assert result.exit_code == 3
    assert "Desktop" in result.stdout
    assert cli.load_approval_request(pending.approval_id).status == "pending"


@pytest.mark.parametrize(
    ("direction", "command_args", "target_platforms"),
    [
        ("upload", ["--resource", "skill:demo"], []),
        (
            "download",
            ["--resource", "skill:demo", "--platform", "codex.win"],
            ["codex.win"],
        ),
    ],
)
def test_interactive_legacy_asset_yes_still_requires_desktop_approval(
    monkeypatch,
    tmp_path: Path,
    direction: str,
    command_args: list[str],
    target_platforms: list[str],
) -> None:
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "_load", Config)
    batch_hash = ("6" if direction == "upload" else "7") * 64
    request = cli.AssetBatchRequestWire(
        direction=direction,  # type: ignore[arg-type]
        resource_keys=["skill:demo"],
        target_platforms=target_platforms,
    )
    plan = AssetBatchPlan(
        direction=direction,
        resource_keys=list(request.resource_keys),
        target_platforms=list(request.target_platforms),
        remote_commit="abc123",
        plan_hash=batch_hash,
        items=[],
        executable_count=1,
        blocked_count=0,
        skipped_count=0,
    )
    pending = cli._create_asset_batch_approval(plan, request)
    assert pending is not None
    monkeypatch.setattr(cli, "build_asset_batch_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(
        cli.typer,
        "confirm",
        lambda *_args, **_kwargs: pytest.fail("legacy asset apply must not offer CLI approval"),
    )
    monkeypatch.setattr(
        cli,
        "apply_asset_batch_plan",
        lambda *_args, **_kwargs: pytest.fail("pending approval must not apply"),
    )

    result = runner.invoke(
        cli.app,
        [
            "asset",
            direction,
            *command_args,
            "--approval-id",
            pending.approval_id,
            "--yes",
        ],
    )

    assert result.exit_code == 3
    assert "Desktop" in result.stdout
    assert cli.load_approval_request(pending.approval_id).status == "pending"


def test_legacy_asset_apply_invalidates_a_stale_reviewed_approval(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "_load", Config)
    request = cli.AssetBatchRequestWire(
        direction="download",
        resource_keys=["skill:demo"],
        target_platforms=["codex.win"],
    )
    reviewed_plan = AssetBatchPlan(
        direction="download",
        resource_keys=list(request.resource_keys),
        target_platforms=list(request.target_platforms),
        remote_commit="old-commit",
        plan_hash="4" * 64,
        items=[],
        executable_count=1,
        blocked_count=0,
        skipped_count=0,
    )
    current_plan = AssetBatchPlan(
        direction="download",
        resource_keys=list(request.resource_keys),
        target_platforms=list(request.target_platforms),
        remote_commit="new-commit",
        plan_hash="5" * 64,
        items=[],
        executable_count=1,
        blocked_count=0,
        skipped_count=0,
    )
    reviewed_approval = cli._create_asset_batch_approval(reviewed_plan, request)
    assert reviewed_approval is not None
    approve_approval_request(reviewed_approval.approval_id)
    monkeypatch.setattr(cli, "build_asset_batch_plan", lambda *_args, **_kwargs: current_plan)
    monkeypatch.setattr(
        cli,
        "apply_asset_batch_plan",
        lambda *_args, **_kwargs: pytest.fail("stale approval must not reach apply"),
    )

    result = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "asset",
            "download",
            "--resource",
            "skill:demo",
            "--platform",
            "codex.win",
            "--approval-id",
            reviewed_approval.approval_id,
            "--json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["status"] == "stale-plan"
    assert payload["data"]["approval_status"] == "rejected"
    assert payload["data"]["stale_plan"]["plan_hash"] == current_plan.plan_hash
    assert payload["data"]["stale_plan"]["approval_status"] == "pending"
    assert cli.load_approval_request(reviewed_approval.approval_id).status == "rejected"


def test_doctor_and_platforms_have_stable_json_envelopes(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_load", Config)
    monkeypatch.setattr(
        cli,
        "build_doctor_checks",
        lambda _cfg: [
            {
                "id": "git",
                "label": "Git",
                "status": "ok",
                "ok": True,
                "detail": "ready",
            },
            {
                "id": "resource_repo",
                "label": "Resource repo",
                "status": "warning",
                "ok": True,
                "detail": "not configured",
            },
        ],
    )

    doctor = runner.invoke(cli.app, ["--non-interactive", "doctor", "--json"])
    platforms = runner.invoke(cli.app, ["--non-interactive", "platforms", "--json"])

    assert doctor.exit_code == 0
    doctor_payload = json.loads(doctor.stdout)
    assert doctor_payload["status"] == "ready"
    assert doctor_payload["data"]["status"] == "warning"
    assert doctor_payload["data"]["warning_count"] == 1
    assert platforms.exit_code == 0
    platform_payload = json.loads(platforms.stdout)
    assert platform_payload["status"] == "ready"
    assert isinstance(platform_payload["data"]["profiles"], list)


def test_mcp_stdio_command_delegates_without_terminal_output(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "_run_mcp_stdio", lambda: calls.append("stdio"))

    result = runner.invoke(cli.app, ["--non-interactive", "mcp", "--stdio"])

    assert result.exit_code == 0
    assert result.stdout == ""
    assert calls == ["stdio"]


def test_integration_status_lists_all_configured_profiles_with_failure_fallback(
    monkeypatch,
) -> None:
    cfg = Config(
        platforms=PlatformsConfig(
            profiles=[
                PlatformProfile(name="codex.win", enabled=True),
                PlatformProfile(name="claude.wsl", enabled=False),
            ]
        )
    )
    seen: list[str] = []
    monkeypatch.setattr(cli, "_load", lambda: cfg)

    def fake_verify(profile_id: str, **_kwargs) -> AiIntegrationVerification:
        seen.append(profile_id)
        if profile_id == "claude.wsl":
            raise RuntimeError("profile unavailable")
        return AiIntegrationVerification(
            profile_id=profile_id,
            installed=True,
            managed=True,
            skill_ready=True,
            mcp_registered=True,
            transport_verified=False,
            tool_count=0,
            tools=[],
            problems=[],
            configured=True,
            transport_status="unknown",
            skill_managed=True,
            mcp_managed=True,
            managed_actions_available=["uninstall"],
        )

    monkeypatch.setattr(cli, "verify_ai_integration", fake_verify)

    result = runner.invoke(cli.app, ["integration", "status", "--json"])

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["status"] == "partial"
    assert seen == ["codex.win", "claude.wsl"]
    profiles = {item["profile_id"]: item for item in payload["data"]["profiles"]}
    assert profiles["codex.win"]["configured"] is True
    assert profiles["codex.win"]["skill_managed"] is True
    assert profiles["codex.win"]["mcp_managed"] is True
    assert profiles["codex.win"]["managed_actions_available"] == ["uninstall"]
    assert profiles["claude.wsl"]["configured"] is False
    assert profiles["claude.wsl"]["transport_status"] == "unknown"
    assert profiles["claude.wsl"]["skill_managed"] is False
    assert profiles["claude.wsl"]["mcp_managed"] is False
    assert profiles["claude.wsl"]["managed_actions_available"] == []


def test_integration_plan_install_returns_approval_binding(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "_load", Config)

    def fake_plan(profile_id: str, **kwargs) -> AiIntegrationPlan:
        captured["profile_id"] = profile_id
        captured.update(kwargs)
        return _integration_plan()

    monkeypatch.setattr(cli, "build_ai_integration_plan", fake_plan)

    result = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "integration",
            "plan-install",
            "--profile",
            "codex.win",
            "--overwrite-unmanaged",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "planned"
    assert payload["data"]["operation_id"] == "a" * 32
    assert payload["data"]["plan_hash"] == "plan-hash"
    assert payload["data"]["approval_id"] == "b" * 32
    assert captured["profile_id"] == "codex.win"
    assert captured["action"] == "install"
    assert captured["overwrite_unmanaged"] is True


def test_non_interactive_integration_apply_never_approves_pending_request(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "load_ai_integration_plan", lambda _operation_id: _integration_plan())
    monkeypatch.setattr(cli, "load_approval_request", lambda _approval_id: _approval())
    monkeypatch.setattr(
        cli,
        "apply_ai_integration_plan",
        lambda *_args, **_kwargs: pytest.fail("pending approval must not apply"),
    )

    result = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "integration",
            "apply-install",
            "--operation-id",
            "a" * 32,
            "--plan-hash",
            "plan-hash",
            "--approval-id",
            "b" * 32,
            "--yes",
            "--json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["status"] == "needs-confirmation"
    assert payload["error"]["code"] == "approval_required"
    assert payload["data"]["approval"]["status"] == "pending"


def test_non_interactive_integration_apply_consumes_preapproved_request(
    monkeypatch,
) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(cli, "_load", Config)
    monkeypatch.setattr(cli, "load_ai_integration_plan", lambda _operation_id: _integration_plan())
    monkeypatch.setattr(
        cli,
        "load_approval_request",
        lambda _approval_id: _approval("approved"),
    )

    def fake_apply(operation_id: str, plan_hash: str, approval_id: str, **_kwargs):
        calls.append(("apply", (operation_id, plan_hash, approval_id)))
        return AiIntegrationResult(
            status="succeeded",
            operation_id=operation_id,
            plan_hash=plan_hash,
            profile_id="codex.win",
            changed=True,
            verified=True,
            approval_id=approval_id,
            message="installed",
        )

    monkeypatch.setattr(cli, "apply_ai_integration_plan", fake_apply)

    result = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "integration",
            "apply-install",
            "--operation-id",
            "a" * 32,
            "--plan-hash",
            "plan-hash",
            "--approval-id",
            "b" * 32,
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    assert payload["data"]["verified"] is True
    assert calls == [("apply", ("a" * 32, "plan-hash", "b" * 32))]


def test_interactive_integration_apply_yes_still_requires_desktop_approval(
    monkeypatch,
) -> None:
    pending = _approval()
    monkeypatch.setattr(cli, "load_ai_integration_plan", lambda _operation_id: _integration_plan())
    monkeypatch.setattr(cli, "load_approval_request", lambda _approval_id: pending)
    monkeypatch.setattr(
        cli.typer,
        "confirm",
        lambda *_args, **_kwargs: pytest.fail("integration apply must not offer CLI approval"),
    )
    monkeypatch.setattr(
        cli,
        "apply_ai_integration_plan",
        lambda *_args, **_kwargs: pytest.fail("pending approval must not apply"),
    )

    result = runner.invoke(
        cli.app,
        [
            "integration",
            "apply-install",
            "--operation-id",
            "a" * 32,
            "--plan-hash",
            "plan-hash",
            "--approval-id",
            "b" * 32,
            "--yes",
        ],
    )

    assert result.exit_code == 3
    assert "Desktop" in result.stdout
    assert pending.status == "pending"


def test_integration_verify_mismatch_has_safe_machine_exit(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_load", Config)
    monkeypatch.setattr(
        cli,
        "verify_ai_integration",
        lambda *_args, **_kwargs: AiIntegrationVerification(
            profile_id="codex.win",
            installed=False,
            managed=False,
            skill_ready=True,
            mcp_registered=False,
            transport_verified=False,
            tool_count=0,
            tools=[],
            problems=["MCP entry missing"],
        ),
    )

    result = runner.invoke(
        cli.app,
        [
            "--non-interactive",
            "integration",
            "verify",
            "--profile",
            "codex.win",
            "--expect",
            "installed",
            "--json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["status"] == "needs-action"
    assert payload["error"]["code"] == "verification_mismatch"
    assert payload["data"]["skill_ready"] is True
