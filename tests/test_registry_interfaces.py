from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cc_port.core.config import Config
from cc_port.interfaces import cli, desktop_api
from cc_port.services.registry_audit import (
    RegistryRepairChoice,
    RegistryRepairPlan,
    RegistryRepairResult,
)

runner = CliRunner()


def _plan(*, choices: list[RegistryRepairChoice] | None = None) -> RegistryRepairPlan:
    return RegistryRepairPlan(
        remote_commit="abc123",
        repo_url="https://example.invalid/resources.git",
        branch="main",
        registry_status="healthy",
        issues=[],
        choices=list(choices or []),
        registry_diff="",
        plan_hash="plan-hash",
        executable_count=0,
        blocked_count=0,
        repairable=True,
    )


def test_registry_cli_check_and_dry_run_are_read_only(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "_load", Config)
    monkeypatch.setattr(
        cli,
        "build_registry_repair_plan",
        lambda **_kwargs: calls.append("plan") or _plan(),
    )
    monkeypatch.setattr(
        cli,
        "apply_registry_repair",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("apply must not run")),
    )

    checked = runner.invoke(cli.app, ["resource", "registry-check", "--json"])
    dry_run = runner.invoke(
        cli.app,
        ["resource", "registry-repair", "--dry-run", "--json"],
    )
    unconfirmed = runner.invoke(
        cli.app,
        ["resource", "registry-repair", "--json"],
    )

    assert checked.exit_code == 0
    assert json.loads(checked.stdout)["registry_status"] == "healthy"
    assert dry_run.exit_code == 0
    assert json.loads(dry_run.stdout)["plan_hash"] == "plan-hash"
    assert unconfirmed.exit_code == 2
    assert "pass --yes" in unconfirmed.stdout
    assert calls == ["plan", "plan", "plan"]


def test_registry_cli_apply_requires_yes_and_forwards_choices(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    choices_path = tmp_path / "choices.yaml"
    choices_path.write_text(
        "choices:\n- issue_id: issue-1\n  action: remove\n",
        encoding="utf-8",
    )

    def fake_plan(**kwargs) -> RegistryRepairPlan:
        captured["planned_choices"] = kwargs["choices"]
        return _plan(choices=kwargs["choices"])

    def fake_apply(**kwargs) -> RegistryRepairResult:
        captured.update(kwargs)
        return RegistryRepairResult(
            status="succeeded",
            plan_hash=kwargs["expected_plan_hash"],
            remote_commit="def456",
            message="done",
        )

    monkeypatch.setattr(cli, "_load", Config)
    monkeypatch.setattr(cli, "build_registry_repair_plan", fake_plan)
    monkeypatch.setattr(cli, "apply_registry_repair", fake_apply)

    result = runner.invoke(
        cli.app,
        [
            "resource",
            "registry-repair",
            "--yes",
            "--choices",
            str(choices_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "succeeded"
    assert captured["expected_plan_hash"] == "plan-hash"
    assert captured["choices"] == [
        RegistryRepairChoice(issue_id="issue-1", action="remove")
    ]


def test_desktop_registry_plan_and_apply_validate_and_forward_choices(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_plan(**kwargs) -> RegistryRepairPlan:
        captured["planned"] = kwargs["choices"]
        return _plan(choices=kwargs["choices"])

    def fake_apply(**kwargs) -> RegistryRepairResult:
        captured.update(kwargs)
        return RegistryRepairResult(status="unchanged", plan_hash="plan-hash")

    monkeypatch.setattr(desktop_api, "load_config", Config)
    monkeypatch.setattr(desktop_api, "build_registry_repair_plan", fake_plan)
    monkeypatch.setattr(desktop_api, "apply_registry_repair", fake_apply)
    payload = {
        "choices": [
            {"issue_id": "issue-1", "action": "add", "name": "renamed"}
        ]
    }

    planned = desktop_api.run_action("registry_repair_plan", payload)
    applied = desktop_api.run_action(
        "registry_repair_apply",
        {"plan_hash": "plan-hash", **payload},
    )
    invalid = desktop_api.run_action(
        "registry_repair_plan",
        {"choices": [{"issue_id": "missing-action"}]},
    )

    expected = [
        RegistryRepairChoice(issue_id="issue-1", action="add", name="renamed")
    ]
    assert planned["ok"] is True
    assert applied["ok"] is True
    assert captured["planned"] == expected
    assert captured["expected_plan_hash"] == "plan-hash"
    assert captured["choices"] == expected
    assert invalid["ok"] is False
    assert "requires issue_id and action" in invalid["error"]["message"]
