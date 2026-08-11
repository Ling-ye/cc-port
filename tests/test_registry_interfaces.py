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
    assert not hasattr(cli, "apply_registry_repair")

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
    checked_payload = json.loads(checked.stdout)
    assert checked_payload["contract_version"] == 1
    assert checked_payload["ok"] is True
    assert checked_payload["status"] == "succeeded"
    assert checked_payload["error"] is None
    assert checked_payload["data"]["registry_status"] == "healthy"
    assert dry_run.exit_code == 0
    dry_run_payload = json.loads(dry_run.stdout)
    assert dry_run_payload["status"] == "planned"
    assert dry_run_payload["data"]["plan_hash"] == "plan-hash"
    assert unconfirmed.exit_code == 3
    unconfirmed_payload = json.loads(unconfirmed.stdout)
    assert unconfirmed_payload["status"] == "needs-confirmation"
    assert unconfirmed_payload["error"]["code"] == "registry_repair_apply_unavailable"
    assert unconfirmed_payload["data"]["plan_hash"] == "plan-hash"
    assert calls == ["plan", "plan", "plan"]


def test_registry_cli_check_json_failure_is_one_envelope(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_load", Config)

    def fail_plan(**_kwargs) -> RegistryRepairPlan:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(cli, "build_registry_repair_plan", fail_plan)

    result = runner.invoke(
        cli.app,
        ["--non-interactive", "resource", "registry-check", "--json"],
    )

    assert result.exit_code == 1
    assert result.stderr == ""
    assert "Usage:" not in result.output
    assert "\x1b[" not in result.output
    payload = json.loads(result.stdout)
    assert payload["contract_version"] == 1
    assert payload["ok"] is False
    assert payload["status"] == "failed"
    assert payload["data"] is None
    assert payload["error"] == {
        "code": "registry_check_failed",
        "message": "Registry check failed: audit unavailable",
        "details": None,
    }


def test_registry_cli_yes_never_applies_and_forwards_choices_only_to_plan(
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

    monkeypatch.setattr(cli, "_load", Config)
    monkeypatch.setattr(cli, "build_registry_repair_plan", fake_plan)
    assert not hasattr(cli, "apply_registry_repair")

    machine = runner.invoke(
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
    human = runner.invoke(
        cli.app,
        [
            "resource",
            "registry-repair",
            "--yes",
            "--choices",
            str(choices_path),
        ],
    )

    assert machine.exit_code == 3
    payload = json.loads(machine.stdout)
    assert payload["status"] == "needs-confirmation"
    assert payload["error"]["code"] == "registry_repair_apply_unavailable"
    assert payload["data"]["plan_hash"] == "plan-hash"
    assert human.exit_code == 3
    assert "Desktop" in human.stdout
    assert "MCP" in human.stdout
    assert captured["planned_choices"] == [
        RegistryRepairChoice(issue_id="issue-1", action="remove")
    ]
    assert "expected_plan_hash" not in captured


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
    monkeypatch.setattr(desktop_api, "_trusted_desktop_parent", lambda: True)
    payload = {"choices": [{"issue_id": "issue-1", "action": "add", "name": "renamed"}]}

    planned = desktop_api.run_action("registry_repair_plan", payload)
    monkeypatch.setenv(
        desktop_api.TRUSTED_DESKTOP_ACTION_ENV_VAR,
        "registry_repair_apply",
    )
    applied = desktop_api.run_action(
        "registry_repair_apply",
        {"plan_hash": "plan-hash", **payload},
    )
    invalid = desktop_api.run_action(
        "registry_repair_plan",
        {"choices": [{"issue_id": "missing-action"}]},
    )

    expected = [RegistryRepairChoice(issue_id="issue-1", action="add", name="renamed")]
    assert planned["ok"] is True
    assert applied["ok"] is True
    assert captured["planned"] == expected
    assert captured["expected_plan_hash"] == "plan-hash"
    assert captured["choices"] == expected
    assert invalid["ok"] is False
    assert "requires issue_id and action" in invalid["error"]["message"]
