from __future__ import annotations

from typer.testing import CliRunner

from lpm.core.config import Config
from lpm.interfaces import cli
from lpm.services.asset_sync import AssetActionPlan, AssetActionResult, AssetInventory
from lpm.services.resource_repo import ResourceRepoInfo

runner = CliRunner()


def test_asset_cli_list_plan_and_apply(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
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
    assert planned.exit_code == 0
    assert '"operation_id": "plan-1"' in planned.stdout
    assert applied.exit_code == 0
    assert '"status": "succeeded"' in applied.stdout
    assert calls == [
        ("plan", ("download", "skill", "demo", "cursor")),
        ("apply", "plan-1"),
    ]


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
