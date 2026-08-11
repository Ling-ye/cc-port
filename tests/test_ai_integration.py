from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from cc_port.core.config import Config, InstallConfig, ResourcesConfig
from cc_port.core.platforms import PlatformProfile, PlatformsConfig
from cc_port.services import ai_integration
from cc_port.services.ai_integration import (
    AgentCommand,
    apply_ai_integration_plan,
    approve_ai_integration_plan,
    build_ai_integration_plan,
    verify_ai_integration,
)
from cc_port.services.approval import ApprovalRequiredError, load_approval_request
from cc_port.services.local_transaction import ChangeTarget, LocalChangeTransaction
from cc_port.services.state_lock import acquire_target_locks


@pytest.fixture
def integration_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(tmp_path / "state"))
    executable = tmp_path / "cc-port.exe"
    executable.write_bytes(b"test executable")
    command = AgentCommand(str(executable), ["mcp", "--stdio"], "test")
    monkeypatch.setattr(
        ai_integration,
        "_verify_stdio",
        lambda _command, *, timeout_seconds: ["cc_port_status", "asset_inventory"],
    )
    return tmp_path, command


def _config(tmp_path: Path, profile: PlatformProfile) -> Config:
    return Config(
        resources=ResourcesConfig(local_path=str(tmp_path / "resource-repo")),
        platforms=PlatformsConfig(profiles=[profile]),
    )


def test_frozen_target_named_sidecar_resolves_the_matching_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sidecar = tmp_path / "cc-port-desktop-api-x86_64-pc-windows-msvc.exe"
    agent = tmp_path / "cc-port-x86_64-pc-windows-msvc.exe"
    sidecar.write_bytes(b"sidecar")
    agent.write_bytes(b"agent")
    monkeypatch.setattr(ai_integration.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ai_integration.sys, "executable", str(sidecar))

    command = ai_integration.resolve_agent_command()

    assert command == AgentCommand(
        str(agent),
        ["mcp", "--stdio"],
        "bundled-sibling",
    )


def test_frozen_installed_sidecar_resolves_the_plain_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sidecar = tmp_path / "cc-port-desktop-api.exe"
    agent = tmp_path / "cc-port.exe"
    sidecar.write_bytes(b"sidecar")
    agent.write_bytes(b"agent")
    monkeypatch.setattr(ai_integration.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ai_integration.sys, "executable", str(sidecar))

    command = ai_integration.resolve_agent_command()

    assert command.command == str(agent)
    assert command.source == "bundled-sibling"


def test_json_profile_install_requires_approval_and_verifies(integration_env) -> None:
    tmp_path, command = integration_env
    profile = PlatformProfile(
        name="cursor-windows",
        tool_id="cursor",
        display_name="Cursor Windows",
        skills_dir=str(tmp_path / "cursor" / "skills"),
        mcp_json=str(tmp_path / "cursor" / "mcp.json"),
    )
    config = _config(tmp_path, profile)

    plan = build_ai_integration_plan(profile.name, config=config, command=command)

    assert plan.blocked is False
    assert plan.target.actions == ["install-skill", "register-mcp"]
    assert plan.approval_id
    with pytest.raises(ApprovalRequiredError):
        apply_ai_integration_plan(
            plan.operation_id,
            plan.plan_hash,
            plan.approval_id,
            config=config,
        )

    approve_ai_integration_plan(plan.approval_id)
    result = apply_ai_integration_plan(
        plan.operation_id,
        plan.plan_hash,
        plan.approval_id,
        config=config,
    )

    assert result.status == "succeeded"
    assert result.verified is True
    skill = Path(profile.skills_dir) / "cc-port"
    assert (skill / "SKILL.md").is_file()
    marker = json.loads((skill / ".cc-port-managed.json").read_text(encoding="utf-8"))
    assert marker["owner"] == "cc-port-ai-integration"
    mcp = json.loads(Path(profile.mcp_json).read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["cc-port"] == {
        "command": command.command,
        "args": command.args,
    }
    verified = verify_ai_integration(
        profile.name,
        config=config,
        command=command,
        verify_transport=True,
    )
    assert verified.installed is True
    assert verified.tool_count == 2


def test_changed_target_returns_stale_plan_without_writing(integration_env) -> None:
    tmp_path, command = integration_env
    profile = PlatformProfile(
        name="claude-windows",
        tool_id="claude-code",
        skills_dir=str(tmp_path / "claude" / "skills"),
        mcp_json=str(tmp_path / "claude.json"),
    )
    config = _config(tmp_path, profile)
    plan = build_ai_integration_plan(profile.name, config=config, command=command)
    approve_ai_integration_plan(plan.approval_id)
    mcp_path = Path(profile.mcp_json)
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text('{"mcpServers": {}}\n', encoding="utf-8")

    result = apply_ai_integration_plan(
        plan.operation_id,
        plan.plan_hash,
        plan.approval_id,
        config=config,
    )

    assert result.status == "stale-plan"
    assert result.stale_plan is not None
    assert result.stale_plan.plan_hash != plan.plan_hash
    assert result.stale_plan.approval_id != plan.approval_id
    assert load_approval_request(plan.approval_id).status == "rejected"
    assert load_approval_request(result.stale_plan.approval_id).status == "pending"
    assert not (Path(profile.skills_dir) / "cc-port").exists()
    mcp_path.unlink()
    with pytest.raises(ValueError, match="plan hash"):
        apply_ai_integration_plan(
            plan.operation_id,
            plan.plan_hash,
            plan.approval_id,
            config=config,
        )
    assert load_approval_request(plan.approval_id).status == "rejected"


def test_uninstall_removes_only_owned_content(integration_env) -> None:
    tmp_path, command = integration_env
    profile = PlatformProfile(
        name="cursor-windows",
        tool_id="cursor",
        skills_dir=str(tmp_path / "cursor" / "skills"),
        mcp_json=str(tmp_path / "cursor" / "mcp.json"),
    )
    config = _config(tmp_path, profile)
    install = build_ai_integration_plan(profile.name, config=config, command=command)
    approve_ai_integration_plan(install.approval_id)
    apply_ai_integration_plan(
        install.operation_id,
        install.plan_hash,
        install.approval_id,
        config=config,
    )
    mcp_path = Path(profile.mcp_json)
    data = json.loads(mcp_path.read_text(encoding="utf-8"))
    data["mcpServers"]["unrelated"] = {"command": "other"}
    mcp_path.write_text(json.dumps(data), encoding="utf-8")

    uninstall = build_ai_integration_plan(
        profile.name,
        action="uninstall",
        config=config,
        command=command,
    )
    approve_ai_integration_plan(uninstall.approval_id)
    result = apply_ai_integration_plan(
        uninstall.operation_id,
        uninstall.plan_hash,
        uninstall.approval_id,
        config=config,
    )

    assert result.status == "succeeded"
    assert not (Path(profile.skills_dir) / "cc-port").exists()
    remaining = json.loads(mcp_path.read_text(encoding="utf-8"))["mcpServers"]
    assert remaining == {"unrelated": {"command": "other"}}


def test_codex_toml_managed_block_preserves_existing_settings(integration_env) -> None:
    tmp_path, command = integration_env
    config_path = tmp_path / "codex" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('model = "gpt-test"\n', encoding="utf-8")
    profile = PlatformProfile(
        name="codex-windows",
        tool_id="codex",
        skills_dir=str(tmp_path / "codex" / "skills"),
        settings_path=str(config_path),
    )
    config = _config(tmp_path, profile)

    plan = build_ai_integration_plan(profile.name, config=config, command=command)
    approve_ai_integration_plan(plan.approval_id)
    apply_ai_integration_plan(
        plan.operation_id,
        plan.plan_hash,
        plan.approval_id,
        config=config,
    )

    text = config_path.read_text(encoding="utf-8")
    assert 'model = "gpt-test"' in text
    assert ai_integration.CODEX_BLOCK_BEGIN in text
    assert "[mcp_servers.cc-port]" in text


def test_unmanaged_conflict_is_blocked_without_explicit_takeover(integration_env) -> None:
    tmp_path, command = integration_env
    mcp_path = tmp_path / "cursor" / "mcp.json"
    mcp_path.parent.mkdir(parents=True)
    mcp_path.write_text(
        '{"mcpServers":{"cc-port":{"command":"different"}}}\n',
        encoding="utf-8",
    )
    profile = PlatformProfile(
        name="cursor-windows",
        tool_id="cursor",
        skills_dir=str(tmp_path / "cursor" / "skills"),
        mcp_json=str(mcp_path),
    )
    config = _config(tmp_path, profile)

    blocked = build_ai_integration_plan(profile.name, config=config, command=command)
    allowed = build_ai_integration_plan(
        profile.name,
        config=config,
        command=command,
        overwrite_unmanaged=True,
    )

    assert blocked.blocked is True
    assert blocked.approval_id == ""
    assert any("unmanaged" in item for item in blocked.blockers)
    assert allowed.blocked is False
    assert "register-mcp" in allowed.target.actions


def test_wsl_profile_is_blocked_and_transport_is_not_started(
    integration_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path, command = integration_env
    home = tmp_path / "wsl-home"
    home.mkdir()
    profile = PlatformProfile(
        name="codex-wsl",
        tool_id="codex",
        environment_kind="wsl",
        environment_name="Ubuntu",
        home_dir=str(home),
        skills_dir=str(home / ".agents" / "skills"),
        settings_path=str(home / ".codex" / "config.toml"),
    )
    config = _config(tmp_path, profile)
    calls: list[AgentCommand] = []
    monkeypatch.setattr(
        ai_integration,
        "_verify_stdio",
        lambda observed, *, timeout_seconds: calls.append(observed),
    )

    plan = build_ai_integration_plan(profile.name, config=config, command=command)
    verified = verify_ai_integration(
        profile.name,
        config=config,
        command=command,
        verify_transport=True,
    )

    assert plan.blocked is True
    assert "WSL AI integration is unavailable in schema version 1." in plan.blockers
    assert verified.configured is False
    assert verified.transport_status == "unknown"
    assert verified.transport_verified is False
    assert calls == []


def test_disabled_profile_blocks_install_but_allows_owned_uninstall(integration_env) -> None:
    tmp_path, command = integration_env
    profile = PlatformProfile(
        name="cursor-disabled",
        tool_id="cursor",
        skills_dir=str(tmp_path / "cursor-disabled" / "skills"),
        mcp_json=str(tmp_path / "cursor-disabled" / "mcp.json"),
    )
    config = _config(tmp_path, profile)
    install = build_ai_integration_plan(profile.name, config=config, command=command)
    approve_ai_integration_plan(install.approval_id)
    apply_ai_integration_plan(
        install.operation_id,
        install.plan_hash,
        install.approval_id,
        config=config,
    )
    profile.enabled = False

    blocked_install = build_ai_integration_plan(
        profile.name,
        config=config,
        command=command,
    )
    status = verify_ai_integration(
        profile.name,
        config=config,
        command=command,
        verify_transport=False,
    )
    uninstall = build_ai_integration_plan(
        profile.name,
        action="uninstall",
        config=config,
        command=command,
    )

    assert blocked_install.blocked is True
    assert status.configured is True
    assert status.transport_status == "unknown"
    assert status.skill_managed is True
    assert status.mcp_managed is True
    assert status.managed_actions_available == ["uninstall"]
    assert uninstall.blocked is False
    assert uninstall.target.actions == ["remove-skill", "remove-mcp"]
    approve_ai_integration_plan(uninstall.approval_id)
    result = apply_ai_integration_plan(
        uninstall.operation_id,
        uninstall.plan_hash,
        uninstall.approval_id,
        config=config,
    )
    assert result.status == "succeeded"


def test_configured_state_is_independent_from_failed_transport(
    integration_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path, command = integration_env
    profile = PlatformProfile(
        name="cursor-transport-failure",
        tool_id="cursor",
        skills_dir=str(tmp_path / "transport-failure" / "skills"),
        mcp_json=str(tmp_path / "transport-failure" / "mcp.json"),
    )
    config = _config(tmp_path, profile)
    install = build_ai_integration_plan(profile.name, config=config, command=command)
    approve_ai_integration_plan(install.approval_id)
    apply_ai_integration_plan(
        install.operation_id,
        install.plan_hash,
        install.approval_id,
        config=config,
    )

    def fail_probe(*_args, **_kwargs):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(
        ai_integration,
        "_verify_stdio",
        fail_probe,
    )

    status = verify_ai_integration(
        profile.name,
        config=config,
        command=command,
        verify_transport=True,
    )

    assert status.configured is True
    assert status.installed is False
    assert status.transport_status == "failed"
    assert status.transport_verified is False


def test_integration_targets_fail_closed_at_every_private_boundary(
    integration_env,
) -> None:
    tmp_path, command = integration_env
    state_profile = PlatformProfile(
        name="state-boundary",
        tool_id="cursor",
        skills_dir=str(tmp_path / "state"),
        mcp_json=str(tmp_path / "targets" / "state.json"),
    )
    state_config = _config(tmp_path, state_profile)

    config_file = tmp_path / "targets" / "config.toml"
    config_profile = PlatformProfile(
        name="config-boundary",
        tool_id="codex",
        skills_dir=str(tmp_path / "targets" / "config-skills"),
        settings_path=str(config_file),
    )
    config_boundary = _config(tmp_path, config_profile)
    config_boundary.source_path = config_file

    repo_root = tmp_path / "repo-target" / "cc-port"
    repo_profile = PlatformProfile(
        name="repo-boundary",
        tool_id="cursor",
        skills_dir=str(repo_root.parent),
        mcp_json=str(tmp_path / "targets" / "repo.json"),
    )
    repo_config = Config(
        resources=ResourcesConfig(local_path=str(repo_root)),
        platforms=PlatformsConfig(profiles=[repo_profile]),
    )

    legacy_root = tmp_path / "legacy-target"
    legacy_profile = PlatformProfile(
        name="legacy-boundary",
        tool_id="cursor",
        skills_dir=str(legacy_root),
        mcp_json=str(tmp_path / "targets" / "legacy.json"),
    )
    legacy_config = Config(
        install=InstallConfig(target=str(legacy_root)),
        resources=ResourcesConfig(local_path=str(tmp_path / "resource-repo")),
        platforms=PlatformsConfig(profiles=[legacy_profile]),
    )

    mutual_root = tmp_path / "mutual"
    mutual_profile = PlatformProfile(
        name="mutual-boundary",
        tool_id="cursor",
        skills_dir=str(mutual_root),
        mcp_json=str(mutual_root / "cc-port" / "mcp.json"),
    )
    mutual_config = _config(tmp_path, mutual_profile)

    shared_root = tmp_path / "other-profile-root"
    selected_profile = PlatformProfile(
        name="selected-profile",
        tool_id="cursor",
        skills_dir=str(shared_root / "nested"),
        mcp_json=str(tmp_path / "targets" / "selected.json"),
    )
    other_profile = PlatformProfile(
        name="other-profile",
        tool_id="claude-code",
        rules_dir=str(shared_root),
    )
    cross_profile_config = Config(
        resources=ResourcesConfig(local_path=str(tmp_path / "resource-repo")),
        platforms=PlatformsConfig(profiles=[selected_profile, other_profile]),
    )

    cases = [
        (state_profile, state_config, "state boundary"),
        (config_profile, config_boundary, "configuration boundary"),
        (repo_profile, repo_config, "resource repository boundary"),
        (legacy_profile, legacy_config, "legacy install boundary"),
        (mutual_profile, mutual_config, "overlap each other"),
        (selected_profile, cross_profile_config, "configured profile boundary"),
    ]
    for profile, config, expected in cases:
        plan = build_ai_integration_plan(profile.name, config=config, command=command)
        rendered = " ".join(plan.blockers)
        assert plan.blocked is True
        assert expected in rendered
        assert str(tmp_path) not in rendered


def test_packaged_skill_rejects_nested_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "packaged-skill"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: cc-port\n---\n", encoding="utf-8")
    external = tmp_path / "external.md"
    external.write_text("external", encoding="utf-8")
    try:
        (source / "linked.md").symlink_to(external)
    except OSError:
        pytest.skip("This Windows host does not allow test symlink creation.")
    monkeypatch.setattr(ai_integration, "_packaged_skill_root", lambda: source)

    with pytest.raises(ValueError, match="unsafe or unreadable"):
        ai_integration._skill_source()


def test_packaged_skill_rejects_reported_reparse_or_unreadable_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "packaged-skill"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: cc-port\n---\n", encoding="utf-8")
    monkeypatch.setattr(ai_integration, "_packaged_skill_root", lambda: source)
    monkeypatch.setattr(
        ai_integration,
        "resource_tree_issues",
        lambda _source: [SimpleNamespace(code="unsupported-reparse")],
    )

    with pytest.raises(ValueError, match="unsafe or unreadable"):
        ai_integration._skill_source()


@pytest.mark.parametrize("mutation", ["extra", "duplicate"])
def test_codex_managed_block_rejects_noncanonical_or_ambiguous_toml(
    integration_env,
    mutation: str,
) -> None:
    tmp_path, command = integration_env
    config_path = tmp_path / f"codex-{mutation}" / "config.toml"
    profile = PlatformProfile(
        name=f"codex-{mutation}",
        tool_id="codex",
        skills_dir=str(tmp_path / f"codex-{mutation}" / "skills"),
        settings_path=str(config_path),
    )
    config = _config(tmp_path, profile)
    install = build_ai_integration_plan(profile.name, config=config, command=command)
    approve_ai_integration_plan(install.approval_id)
    apply_ai_integration_plan(
        install.operation_id,
        install.plan_hash,
        install.approval_id,
        config=config,
    )
    text = config_path.read_text(encoding="utf-8")
    if mutation == "extra":
        text = text.replace(
            ai_integration.CODEX_BLOCK_END,
            'env = { API_TOKEN = "super-secret-value" }\n'
            + ai_integration.CODEX_BLOCK_END,
        )
    else:
        text += ai_integration.CODEX_BLOCK_BEGIN + "\n"
    config_path.write_text(text, encoding="utf-8")

    plan = build_ai_integration_plan(profile.name, config=config, command=command)

    assert plan.blocked is True
    rendered = " ".join(plan.blockers)
    assert "cannot be parsed safely" in rendered
    assert "super-secret-value" not in rendered
    assert str(config_path) not in rendered


def test_codex_canonical_block_without_sidecar_is_not_owned(integration_env) -> None:
    tmp_path, command = integration_env
    config_path = tmp_path / "codex-sidecar" / "config.toml"
    profile = PlatformProfile(
        name="codex-sidecar",
        tool_id="codex",
        skills_dir=str(tmp_path / "codex-sidecar" / "skills"),
        settings_path=str(config_path),
    )
    config = _config(tmp_path, profile)
    install = build_ai_integration_plan(profile.name, config=config, command=command)
    approve_ai_integration_plan(install.approval_id)
    apply_ai_integration_plan(
        install.operation_id,
        install.plan_hash,
        install.approval_id,
        config=config,
    )
    ai_integration._ownership_path(profile.name).unlink()

    status = verify_ai_integration(
        profile.name,
        config=config,
        command=command,
        verify_transport=False,
    )
    uninstall = build_ai_integration_plan(
        profile.name,
        action="uninstall",
        config=config,
        command=command,
    )

    assert status.mcp_registered is True
    assert status.mcp_managed is False
    assert uninstall.target.actions == ["remove-skill"]
    assert ai_integration.CODEX_BLOCK_BEGIN in config_path.read_text(encoding="utf-8")


def test_codex_sidecar_without_canonical_block_is_not_owned(integration_env) -> None:
    tmp_path, command = integration_env
    config_path = tmp_path / "codex-marker" / "config.toml"
    profile = PlatformProfile(
        name="codex-marker",
        tool_id="codex",
        skills_dir=str(tmp_path / "codex-marker" / "skills"),
        settings_path=str(config_path),
    )
    config = _config(tmp_path, profile)
    install = build_ai_integration_plan(profile.name, config=config, command=command)
    approve_ai_integration_plan(install.approval_id)
    apply_ai_integration_plan(
        install.operation_id,
        install.plan_hash,
        install.approval_id,
        config=config,
    )
    text = config_path.read_text(encoding="utf-8")
    text = text.replace(ai_integration.CODEX_BLOCK_BEGIN + "\n", "")
    text = text.replace(ai_integration.CODEX_BLOCK_END + "\n", "")
    config_path.write_text(text, encoding="utf-8")

    status = verify_ai_integration(
        profile.name,
        config=config,
        command=command,
        verify_transport=False,
    )
    uninstall = build_ai_integration_plan(
        profile.name,
        action="uninstall",
        config=config,
        command=command,
    )

    assert status.mcp_managed is False
    assert uninstall.target.actions == ["remove-skill"]
    assert "[mcp_servers.cc-port]" in config_path.read_text(encoding="utf-8")


def test_stdio_probe_uses_minimal_environment_and_checks_status_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CC_PORT_GITHUB_TOKEN", "secret-token-value")
    monkeypatch.setenv("UNRELATED_VALUE", "must-not-cross-boundary")
    captured: dict[str, object] = {}
    names = sorted(ai_integration.MCP_REQUIRED_CORE_TOOLS)
    responses = {
        1: {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}},
        2: {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"tools": [{"name": name} for name in names]},
        },
        3: {
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "structuredContent": {
                    "contract_version": 1,
                    "ok": True,
                    "status": "ready",
                    "data": {
                        "automation_policy": "plan-apply-verify",
                        "approval_mode": "desktop-only",
                        "approval_tools_exposed": False,
                        "recommended_tools": names,
                    },
                }
            },
        },
    }

    def fake_exchange(_observed, *, payload, **_kwargs):
        captured["input"] = payload
        captured["env"] = ai_integration._stdio_probe_environment()
        return responses, "", 0

    monkeypatch.setattr(ai_integration, "_stdio_probe_exchange", fake_exchange)
    command = AgentCommand(str(tmp_path / "cc-port.exe"), ["mcp", "--stdio"], "test")

    assert ai_integration._verify_stdio(command, timeout_seconds=1) == names
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert "CC_PORT_STATE_HOME" in environment
    assert "CC_PORT_GITHUB_TOKEN" not in environment
    assert "UNRELATED_VALUE" not in environment
    assert '"name":"cc_port_status"' in str(captured["input"])


def test_stdio_probe_redacts_and_limits_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "top-secret-value"
    private_path = str(tmp_path / "Users" / "private" / "config.toml")
    monkeypatch.setenv("SERVICE_API_KEY", secret)
    monkeypatch.setattr(
        ai_integration,
        "_stdio_probe_exchange",
        lambda *_args, **_kwargs: (
            {},
            f"api key {secret} at {private_path} " + "x" * 2000,
            1,
        ),
    )
    command = AgentCommand(str(tmp_path / "private" / "cc-port.exe"), [], "test")

    with pytest.raises(RuntimeError) as caught:
        ai_integration._verify_stdio(command, timeout_seconds=1)

    message = str(caught.value)
    assert secret not in message
    assert private_path not in message
    assert len(message) < 650


def test_stdio_probe_rejects_missing_recommended_core_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = sorted(ai_integration.MCP_REQUIRED_CORE_TOOLS)
    recommended = [name for name in names if name != "asset_action_apply"]
    responses = {
        1: {"jsonrpc": "2.0", "id": 1, "result": {}},
        2: {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"tools": [{"name": name} for name in names]},
        },
        3: {
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "structuredContent": {
                    "contract_version": 1,
                    "ok": True,
                    "status": "ready",
                    "data": {
                        "automation_policy": "plan-apply-verify",
                        "approval_mode": "desktop-only",
                        "approval_tools_exposed": False,
                        "recommended_tools": recommended,
                    },
                }
            },
        },
    }
    monkeypatch.setattr(
        ai_integration,
        "_stdio_probe_exchange",
        lambda *_args, **_kwargs: (responses, "", 0),
    )

    with pytest.raises(RuntimeError, match="recommended core tools"):
        ai_integration._verify_stdio(
            AgentCommand(str(tmp_path / "cc-port.exe"), [], "test"),
            timeout_seconds=1,
        )


def test_apply_revalidates_after_waiting_for_target_locks(
    integration_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path, command = integration_env
    profile = PlatformProfile(
        name="cursor-race",
        tool_id="cursor",
        skills_dir=str(tmp_path / "cursor-race" / "skills"),
        mcp_json=str(tmp_path / "cursor-race" / "mcp.json"),
    )
    config = _config(tmp_path, profile)
    plan = build_ai_integration_plan(profile.name, config=config, command=command)
    approve_ai_integration_plan(plan.approval_id)
    paths = ai_integration._change_paths(plan)
    held = acquire_target_locks(paths, timeout_seconds=1)
    attempted = threading.Event()
    original_acquire = ai_integration.acquire_target_locks

    def notifying_acquire(targets, *, timeout_seconds):
        attempted.set()
        return original_acquire(targets, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(ai_integration, "acquire_target_locks", notifying_acquire)
    observed: dict[str, object] = {}

    def run_apply() -> None:
        try:
            observed["result"] = apply_ai_integration_plan(
                plan.operation_id,
                plan.plan_hash,
                plan.approval_id,
                config=config,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            observed["error"] = exc

    worker = threading.Thread(target=run_apply)
    worker.start()
    assert attempted.wait(timeout=2)
    mcp_path = Path(profile.mcp_json)
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text('{"mcpServers": {}}\n', encoding="utf-8")
    held.release()
    worker.join(timeout=5)

    assert worker.is_alive() is False
    assert "error" not in observed
    result = observed["result"]
    assert result.status == "stale-plan"
    assert load_approval_request(plan.approval_id).status == "rejected"
    assert not (Path(profile.skills_dir) / "cc-port").exists()


def test_preheld_transaction_lock_mismatch_releases_lock(
    integration_env,
) -> None:
    tmp_path, _ = integration_env
    first = tmp_path / "first-target"
    second = tmp_path / "second-target"
    held = acquire_target_locks([first], timeout_seconds=1)

    with pytest.raises(ValueError, match="do not match"):
        LocalChangeTransaction.begin(
            "test-preheld-lock",
            [ChangeTarget(path=second, change_action="write")],
            lock_timeout_seconds=1,
            preheld_locks=held,
        )

    reacquired = acquire_target_locks([first], timeout_seconds=1)
    reacquired.release()


def test_snapshot_failure_does_not_consume_approved_request(
    integration_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path, command = integration_env
    profile = PlatformProfile(
        name="cursor-snapshot-failure",
        tool_id="cursor",
        skills_dir=str(tmp_path / "snapshot-failure" / "skills"),
        mcp_json=str(tmp_path / "snapshot-failure" / "mcp.json"),
    )
    config = _config(tmp_path, profile)
    plan = build_ai_integration_plan(profile.name, config=config, command=command)
    approve_ai_integration_plan(plan.approval_id)

    def fail_snapshot(_cls, *_args, **_kwargs):
        raise OSError("snapshot failed")

    monkeypatch.setattr(
        ai_integration.LocalChangeTransaction,
        "begin",
        classmethod(fail_snapshot),
    )

    with pytest.raises(OSError, match="snapshot failed"):
        apply_ai_integration_plan(
            plan.operation_id,
            plan.plan_hash,
            plan.approval_id,
            config=config,
        )

    assert load_approval_request(plan.approval_id).status == "approved"


@pytest.mark.parametrize("component", ["skill", "mcp"])
def test_post_consume_revalidation_never_overwrites_new_unmanaged_component(
    integration_env,
    monkeypatch: pytest.MonkeyPatch,
    component: str,
) -> None:
    tmp_path, command = integration_env
    profile = PlatformProfile(
        name=f"cursor-consume-race-{component}",
        tool_id="cursor",
        skills_dir=str(tmp_path / f"consume-race-{component}" / "skills"),
        mcp_json=str(tmp_path / f"consume-race-{component}" / "mcp.json"),
    )
    config = _config(tmp_path, profile)
    plan = build_ai_integration_plan(
        profile.name,
        config=config,
        command=command,
        overwrite_unmanaged=True,
    )
    approve_ai_integration_plan(plan.approval_id)
    original_consume = ai_integration.consume_approval
    skill_path = Path(profile.skills_dir) / "cc-port"
    mcp_path = Path(profile.mcp_json)

    def consume_then_create(*args, **kwargs):
        request = original_consume(*args, **kwargs)
        if component == "skill":
            skill_path.mkdir(parents=True)
            (skill_path / "SKILL.md").write_text(
                "unmanaged skill created after consume\n",
                encoding="utf-8",
            )
        else:
            mcp_path.parent.mkdir(parents=True)
            mcp_path.write_text(
                '{"mcpServers":{"cc-port":{"command":"external"}}}\n',
                encoding="utf-8",
            )
        return request

    monkeypatch.setattr(ai_integration, "consume_approval", consume_then_create)

    result = apply_ai_integration_plan(
        plan.operation_id,
        plan.plan_hash,
        plan.approval_id,
        config=config,
    )

    assert result.status == "stale-plan"
    assert result.stale_plan is not None
    assert result.stale_plan.approval_id != plan.approval_id
    assert load_approval_request(plan.approval_id).status == "consumed"
    assert load_approval_request(result.stale_plan.approval_id).status == "pending"
    if component == "skill":
        assert (skill_path / "SKILL.md").read_text(encoding="utf-8") == (
            "unmanaged skill created after consume\n"
        )
        assert not mcp_path.exists()
    else:
        assert json.loads(mcp_path.read_text(encoding="utf-8"))["mcpServers"][
            "cc-port"
        ]["command"] == "external"
        assert not skill_path.exists()


def test_component_cas_rolls_back_own_skill_and_preserves_external_mcp(
    integration_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path, command = integration_env
    profile = PlatformProfile(
        name="cursor-component-cas",
        tool_id="cursor",
        skills_dir=str(tmp_path / "component-cas" / "skills"),
        mcp_json=str(tmp_path / "component-cas" / "mcp.json"),
    )
    config = _config(tmp_path, profile)
    plan = build_ai_integration_plan(
        profile.name,
        config=config,
        command=command,
        overwrite_unmanaged=True,
    )
    approve_ai_integration_plan(plan.approval_id)
    original_copy = ai_integration._copy_validated_skill_tree
    mcp_path = Path(profile.mcp_json)

    def copy_then_create_mcp(source, destination, *, manifest=None):
        original_copy(source, destination, manifest=manifest)
        mcp_path.parent.mkdir(parents=True, exist_ok=True)
        mcp_path.write_text(
            '{"mcpServers":{"cc-port":{"command":"external-race"}}}\n',
            encoding="utf-8",
        )

    monkeypatch.setattr(
        ai_integration,
        "_copy_validated_skill_tree",
        copy_then_create_mcp,
    )

    result = apply_ai_integration_plan(
        plan.operation_id,
        plan.plan_hash,
        plan.approval_id,
        config=config,
    )

    assert result.status == "stale-plan"
    assert result.stale_plan is not None
    assert load_approval_request(plan.approval_id).status == "consumed"
    assert load_approval_request(result.stale_plan.approval_id).status == "pending"
    assert not (Path(profile.skills_dir) / "cc-port").exists()
    assert json.loads(mcp_path.read_text(encoding="utf-8"))["mcpServers"][
        "cc-port"
    ]["command"] == "external-race"


def test_partial_uninstall_removes_only_still_safe_managed_component(
    integration_env,
) -> None:
    tmp_path, command = integration_env
    profile = PlatformProfile(
        name="cursor-partial",
        tool_id="cursor",
        skills_dir=str(tmp_path / "cursor-partial" / "skills"),
        mcp_json=str(tmp_path / "cursor-partial" / "mcp.json"),
    )
    config = _config(tmp_path, profile)
    install = build_ai_integration_plan(profile.name, config=config, command=command)
    approve_ai_integration_plan(install.approval_id)
    apply_ai_integration_plan(
        install.operation_id,
        install.plan_hash,
        install.approval_id,
        config=config,
    )
    skill_path = Path(profile.skills_dir) / "cc-port"
    entry = skill_path / "SKILL.md"
    entry.write_text(entry.read_text(encoding="utf-8") + "\nuser edit\n", encoding="utf-8")

    uninstall = build_ai_integration_plan(
        profile.name,
        action="uninstall",
        config=config,
        command=command,
    )
    assert uninstall.target.skill_status == "update"
    assert uninstall.target.actions == ["remove-mcp"]
    approve_ai_integration_plan(uninstall.approval_id)
    result = apply_ai_integration_plan(
        uninstall.operation_id,
        uninstall.plan_hash,
        uninstall.approval_id,
        config=config,
    )

    assert result.status == "succeeded"
    assert skill_path.is_dir()
    assert "user edit" in entry.read_text(encoding="utf-8")
    mcp_data = json.loads(Path(profile.mcp_json).read_text(encoding="utf-8"))
    assert "cc-port" not in mcp_data["mcpServers"]
    assert not ai_integration._ownership_path(profile.name).exists()


def test_partial_uninstall_preserves_mcp_ownership_record_when_mcp_is_retained(
    integration_env,
) -> None:
    tmp_path, command = integration_env
    profile = PlatformProfile(
        name="cursor-partial-mcp",
        tool_id="cursor",
        skills_dir=str(tmp_path / "cursor-partial-mcp" / "skills"),
        mcp_json=str(tmp_path / "cursor-partial-mcp" / "mcp.json"),
    )
    config = _config(tmp_path, profile)
    install = build_ai_integration_plan(profile.name, config=config, command=command)
    approve_ai_integration_plan(install.approval_id)
    apply_ai_integration_plan(
        install.operation_id,
        install.plan_hash,
        install.approval_id,
        config=config,
    )
    mcp_path = Path(profile.mcp_json)
    mcp_data = json.loads(mcp_path.read_text(encoding="utf-8"))
    mcp_data["mcpServers"]["cc-port"]["args"] = ["externally-modified"]
    mcp_path.write_text(json.dumps(mcp_data), encoding="utf-8")
    ownership = ai_integration._ownership_path(profile.name)

    uninstall = build_ai_integration_plan(
        profile.name,
        action="uninstall",
        config=config,
        command=command,
    )
    assert uninstall.target.actions == ["remove-skill"]
    approve_ai_integration_plan(uninstall.approval_id)
    result = apply_ai_integration_plan(
        uninstall.operation_id,
        uninstall.plan_hash,
        uninstall.approval_id,
        config=config,
    )

    assert result.status == "succeeded"
    assert ownership.is_file()
    assert json.loads(mcp_path.read_text(encoding="utf-8")) == mcp_data
