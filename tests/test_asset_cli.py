from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

from lpm.core.config import Config
from lpm.core.models import RegistryItem
from lpm.core.resource_detection import DetectedRemoteResource
from lpm.interfaces import cli
from lpm.services.asset_sync import (
    AssetActionPlan,
    AssetActionResult,
    AssetBatchPlan,
    AssetBatchResult,
    AssetInventory,
)
from lpm.services.resource_repo import ResourceRepoInfo

runner = CliRunner()


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


def test_asset_cli_list_plan_and_apply(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
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
        calls.append(("plan", (action, kwargs["kind"], kwargs["name"], kwargs["platform"])))
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
            "--json",
        ],
    )
    applied = runner.invoke(cli.app, ["asset", "apply", "plan-1", "--json"])

    assert listed.exit_code == 0
    assert '"branch": "main"' in listed.stdout
    assert "rows" not in json.loads(listed.stdout)
    assert planned.exit_code == 0
    assert '"operation_id": "plan-1"' in planned.stdout
    assert applied.exit_code == 0
    assert '"status": "succeeded"' in applied.stdout
    for result in (listed, planned, applied):
        assert "\x1b[" not in result.stdout
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, dict)
        assert not _contains_desktop_message_ref(parsed)
    assert calls == [
        ("plan", ("download", "skill", "demo", "cursor")),
        ("apply", "plan-1"),
    ]


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
    assert "Deprecated: use `lpm asset list`" in result.stdout
    assert "resources" in result.stdout


def test_asset_batch_cli_and_removed_environment_command(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(cli, "_load", Config)

    def fake_plan(direction: str, **kwargs) -> AssetBatchPlan:
        calls.append(("plan", (direction, kwargs["resource_keys"], kwargs["target_platforms"])))
        return AssetBatchPlan(
            direction=direction,
            resource_keys=kwargs["resource_keys"],
            target_platforms=kwargs["target_platforms"],
            remote_commit="abc123",
            plan_hash="batch-hash",
            items=[],
            executable_count=1,
            blocked_count=0,
            skipped_count=0,
        )

    def fake_apply(direction: str, **kwargs) -> AssetBatchResult:
        calls.append(("apply", (direction, kwargs["expected_plan_hash"])))
        return AssetBatchResult(status="succeeded", plan_hash="batch-hash", results=[])

    monkeypatch.setattr(cli, "build_asset_batch_plan", fake_plan)
    monkeypatch.setattr(cli, "apply_asset_batch_plan", fake_apply)

    downloaded = runner.invoke(
        cli.app,
        [
            "asset",
            "download",
            "--resource",
            "skill:demo",
            "--platform",
            "cursor",
            "--yes",
            "--json",
        ],
    )
    environment = runner.invoke(cli.app, ["env", "discover"])

    assert downloaded.exit_code == 0
    assert json.loads(downloaded.stdout)["status"] == "succeeded"
    assert environment.exit_code != 0
    assert calls == [
        ("plan", ("download", ["skill:demo"], ["cursor"])),
        ("apply", ("download", "batch-hash")),
    ]


def test_asset_batch_choices_allow_multiple_sources_for_one_resource(tmp_path: Path) -> None:
    choices_path = tmp_path / "choices.yaml"
    choices_path.write_text(
        """items:
  - resource_key: skill:demo
    local_instance_id: cursor:skill:demo
    resolution: rename
    new_name: demo-cursor
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
