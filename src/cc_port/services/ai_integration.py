"""Plan, approve, apply, verify, and uninstall the local AI integration.

The desktop and CLI call this module; host-specific configuration writes must
not be reimplemented in either adapter.  Plans bind the packaged Skill,
profile identity, exact target fingerprints, and MCP launch command.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - py310 fallback
    import tomli as tomllib

from ..core.config import (
    Config,
    default_config_path,
    default_state_dir,
    load_config,
    resource_repo_private_path_conflicts,
)
from ..core.platforms import PlatformProfile
from ..core.secret_scan import redact_secret_text
from .approval import (
    ApprovalRequest,
    ApprovalRequiredError,
    approve_approval_request,
    consume_approval,
    create_approval_request,
    invalidate_approval_request,
)
from .env_manager import _profile_scan_available
from .local_path_probe import probe_local_path, resource_tree_issues
from .local_transaction import (
    ChangeTarget,
    LocalChangeTransaction,
    hash_path,
    remove_path_if_exists,
    resource_hash_path,
)
from .state_lock import acquire_target_locks

AI_INTEGRATION_SCHEMA_VERSION = 1
AI_SERVER_NAME = "cc-port"
AI_SKILL_NAME = "cc-port"
AI_OWNER = "cc-port-ai-integration"
AI_AGENT_BIN_ENV_VAR = "CC_PORT_AGENT_BIN"
AI_PLAN_DIR = "ai-integration/plans"
AI_OWNERSHIP_DIR = "ai-integration/ownership"
AI_MARKER_NAME = ".cc-port-managed.json"
CODEX_BLOCK_BEGIN = "# BEGIN CC PORT MANAGED MCP: cc-port"
CODEX_BLOCK_END = "# END CC PORT MANAGED MCP: cc-port"
MCP_CONTRACT_VERSION = 1
MCP_PROBE_DETAIL_LIMIT = 512
MCP_PROBE_STDERR_CAPTURE_LIMIT = 4096
MCP_REQUIRED_CORE_TOOLS = frozenset(
    {
        "cc_port_status",
        "cc_port_doctor",
        "asset_inventory",
        "asset_content_diff",
        "asset_action_plan",
        "asset_action_apply",
        "asset_batch_plan",
        "asset_batch_apply",
        "registry_repair_plan",
        "registry_repair_apply",
        "operation_detail",
    }
)

IntegrationAction = Literal["install", "uninstall"]


@dataclass(frozen=True)
class AgentCommand:
    command: str
    args: list[str]
    source: str


@dataclass
class AiIntegrationTarget:
    profile_id: str
    tool_id: str
    display_name: str
    environment_kind: str
    environment_name: str
    available: bool
    skill_path: str
    mcp_config_path: str
    mcp_config_format: str
    skill_status: str
    mcp_status: str
    actions: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    current_skill_hash: str = ""
    current_config_hash: str = ""
    desired_skill_hash: str = ""
    desired_entry_hash: str = ""
    current_skill_cas: str = ""
    current_mcp_cas: str = ""
    current_ownership_hash: str = ""


@dataclass
class AiIntegrationPlan:
    operation_id: str
    action: IntegrationAction
    profile_id: str
    command: str
    command_args: list[str]
    command_source: str
    target: AiIntegrationTarget
    plan_hash: str
    blocked: bool
    blockers: list[str]
    requires_approval: bool
    approval_id: str = ""
    overwrite_unmanaged: bool = False
    schema_version: int = AI_INTEGRATION_SCHEMA_VERSION


@dataclass
class AiIntegrationResult:
    status: str
    operation_id: str
    plan_hash: str
    profile_id: str
    changed: bool
    verified: bool
    apply_operation_id: str = ""
    approval_id: str = ""
    stale_plan: AiIntegrationPlan | None = None
    blockers: list[str] = field(default_factory=list)
    message: str = ""


@dataclass
class AiIntegrationVerification:
    profile_id: str
    installed: bool
    managed: bool
    skill_ready: bool
    mcp_registered: bool
    transport_verified: bool
    tool_count: int
    tools: list[str]
    problems: list[str]
    configured: bool = False
    transport_status: Literal["unknown", "verified", "failed"] = "unknown"
    skill_managed: bool = False
    mcp_managed: bool = False
    managed_actions_available: list[str] = field(default_factory=list)


class AiIntegrationCasMismatch(RuntimeError):
    """Raised when a component changed after approval consumption."""


def build_ai_integration_plan(
    profile_id: str,
    *,
    action: IntegrationAction = "install",
    overwrite_unmanaged: bool = False,
    config: Config | None = None,
    command: AgentCommand | None = None,
) -> AiIntegrationPlan:
    """Build and persist a plan for one exact runtime profile."""
    return _build_plan(
        profile_id,
        action=action,
        overwrite_unmanaged=overwrite_unmanaged,
        config=config,
        command=command,
        operation_id=uuid4().hex,
        persist=True,
        request_approval=True,
    )


def approve_ai_integration_plan(approval_id: str) -> ApprovalRequest:
    """Approve an integration plan from a trusted human-facing adapter."""
    return approve_approval_request(approval_id)


def apply_ai_integration_plan(
    operation_id: str,
    expected_plan_hash: str,
    approval_id: str,
    *,
    config: Config | None = None,
) -> AiIntegrationResult:
    """Lock, rebuild, snapshot, consume approval, and apply one exact plan."""
    stored = load_ai_integration_plan(operation_id)
    if stored.plan_hash != expected_plan_hash:
        raise ValueError("The supplied plan hash does not match the stored integration plan.")
    cfg = config or load_config()
    command = AgentCommand(
        command=stored.command,
        args=list(stored.command_args),
        source=stored.command_source,
    )
    stored_paths = _change_paths(stored)
    locks = acquire_target_locks(
        stored_paths,
        timeout_seconds=cfg.state.lock_timeout_seconds,
    )
    transaction: LocalChangeTransaction | None = None
    try:
        current = _build_plan(
            stored.profile_id,
            action=stored.action,
            overwrite_unmanaged=stored.overwrite_unmanaged,
            config=cfg,
            command=command,
            operation_id=stored.operation_id,
            persist=False,
            request_approval=False,
        )
        current_paths = _change_paths(current)
        if (
            current.plan_hash != expected_plan_hash
            or _path_keys(current_paths) != _path_keys(stored_paths)
        ):
            _invalidate_plan_approval(stored, approval_id)
            return _replacement_stale_result(
                stored,
                expected_plan_hash=expected_plan_hash,
                replacement=current,
            )
        if current.blocked:
            return AiIntegrationResult(
                status="blocked",
                operation_id=current.operation_id,
                plan_hash=current.plan_hash,
                profile_id=current.profile_id,
                changed=False,
                verified=False,
                blockers=list(current.blockers),
                message="Integration plan is blocked.",
            )
        if not current.target.actions:
            verification = verify_ai_integration(
                current.profile_id,
                config=cfg,
                verify_transport=False,
                command=command,
            )
            return AiIntegrationResult(
                status="unchanged",
                operation_id=current.operation_id,
                plan_hash=current.plan_hash,
                profile_id=current.profile_id,
                changed=False,
                verified=(
                    verification.installed
                    if current.action == "install"
                    else not verification.skill_managed and not verification.mcp_managed
                ),
                message="Integration already matches the requested state.",
            )
        profile = _required_profile(cfg, current.profile_id)
        transaction = LocalChangeTransaction.begin(
            "ai-integration",
            [
                ChangeTarget(
                    path=path,
                    change_action=current.action,
                    resource=(
                        "skill:cc-port"
                        if path == Path(current.target.skill_path).expanduser().absolute()
                        else "mcp:cc-port"
                    ),
                    platform=current.profile_id,
                )
                for path in current_paths
            ],
            metadata={
                "plan_operation_id": current.operation_id,
                "plan_hash": current.plan_hash,
                "profile_id": current.profile_id,
                "action": current.action,
                "approval_id": approval_id,
            },
            lock_timeout_seconds=cfg.state.lock_timeout_seconds,
            preheld_locks=locks,
        )
        locks = None
        try:
            consume_approval(
                approval_id,
                kind="ai-integration",
                operation_id=current.operation_id,
                plan_hash=current.plan_hash,
                scope=_approval_scope(current),
            )
        except Exception:
            transaction.abort("AI integration approval was not consumable.")
            transaction = None
            raise
        post_consume = _build_plan(
            stored.profile_id,
            action=stored.action,
            overwrite_unmanaged=stored.overwrite_unmanaged,
            config=cfg,
            command=command,
            operation_id=stored.operation_id,
            persist=False,
            request_approval=False,
        )
        if (
            post_consume.plan_hash != expected_plan_hash
            or _path_keys(_change_paths(post_consume)) != _path_keys(current_paths)
        ):
            transaction.abort("AI integration targets changed after approval consumption.")
            transaction = None
            return _replacement_stale_result(
                stored,
                expected_plan_hash=expected_plan_hash,
                replacement=post_consume,
            )
        if current.action == "install":
            _apply_install(current, profile, cfg, transaction)
        else:
            _apply_uninstall(current, profile, cfg, transaction)
        verification = verify_ai_integration(
            current.profile_id,
            config=cfg,
            verify_transport=current.action == "install",
            command=AgentCommand(
                current.command,
                list(current.command_args),
                current.command_source,
            ),
        )
        expected = (
            verification.installed
            if current.action == "install"
            else _uninstall_actions_verified(current, verification)
        )
        if not expected:
            raise RuntimeError("AI integration verification did not match the requested state.")
        record = transaction.complete(message=f"AI integration {current.action} succeeded.")
        transaction = None
        return AiIntegrationResult(
            status="succeeded",
            operation_id=current.operation_id,
            plan_hash=current.plan_hash,
            profile_id=current.profile_id,
            changed=True,
            verified=True,
            apply_operation_id=record.operation_id,
            approval_id=approval_id,
            message=f"AI integration {current.action} succeeded.",
        )
    except AiIntegrationCasMismatch as exc:
        if transaction is None:
            raise
        errors = transaction.rollback("AI integration component CAS failed.")
        transaction = None
        if errors:
            raise RuntimeError(
                "AI integration targets changed and rollback did not complete."
            ) from exc
        replacement = _build_plan(
            stored.profile_id,
            action=stored.action,
            overwrite_unmanaged=stored.overwrite_unmanaged,
            config=cfg,
            command=command,
            operation_id=stored.operation_id,
            persist=False,
            request_approval=False,
        )
        return _replacement_stale_result(
            stored,
            expected_plan_hash=expected_plan_hash,
            replacement=replacement,
        )
    except Exception as exc:
        if transaction is None:
            raise
        errors = transaction.rollback(redact_secret_text(str(exc)))
        transaction = None
        suffix = "" if not errors else " Rollback encountered one or more errors."
        raise RuntimeError(
            f"AI integration {stored.action} failed: "
            f"{_sanitize_probe_detail(str(exc), command)}.{suffix}"
        ) from exc
    finally:
        if locks is not None:
            locks.release()


def load_ai_integration_plan(operation_id: str) -> AiIntegrationPlan:
    safe_id = _operation_id(operation_id)
    path = _plan_root() / f"{safe_id}.json"
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(f"Unknown AI integration operation: {safe_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != AI_INTEGRATION_SCHEMA_VERSION:
        raise ValueError("Unsupported AI integration plan schema.")
    target = raw.get("target")
    if not isinstance(target, dict):
        raise ValueError("AI integration plan target is invalid.")
    raw["target"] = AiIntegrationTarget(**target)
    return AiIntegrationPlan(**raw)


def verify_ai_integration(
    profile_id: str,
    *,
    config: Config | None = None,
    verify_transport: bool = True,
    timeout_seconds: float = 20.0,
    command: AgentCommand | None = None,
) -> AiIntegrationVerification:
    cfg = config or load_config()
    profile = _required_profile(cfg, profile_id)
    resolved_command = command or resolve_agent_command()
    target = _inspect_target(
        profile,
        action="install",
        overwrite_unmanaged=False,
        command=resolved_command,
        config=cfg,
    )
    problems = list(target.blockers)
    skill_ready = target.skill_status in {"installed", "compatible"}
    mcp_registered = target.mcp_status in {"installed", "compatible"}
    skill_managed = target.skill_status in {"installed", "update"}
    mcp_managed = target.mcp_status in {"installed", "update"}
    transport_status: Literal["unknown", "verified", "failed"] = "unknown"
    tools: list[str] = []
    if verify_transport and mcp_registered and not target.blockers:
        try:
            tools = _verify_stdio(resolved_command, timeout_seconds=timeout_seconds)
            transport_status = "verified"
        except Exception as exc:
            transport_status = "failed"
            problems.append(
                "MCP stdio verification failed: "
                f"{_sanitize_probe_detail(str(exc), resolved_command)}"
            )
    elif verify_transport and mcp_registered:
        problems.append("MCP transport was not started because the profile is blocked.")
    uninstall_target = _inspect_target(
        profile,
        action="uninstall",
        overwrite_unmanaged=False,
        command=resolved_command,
        config=cfg,
    )
    managed_actions = (
        ["uninstall"]
        if not uninstall_target.blockers and bool(uninstall_target.actions)
        else []
    )
    installed = skill_ready and mcp_registered and (
        not verify_transport or transport_status == "verified"
    )
    return AiIntegrationVerification(
        profile_id=profile_id,
        installed=installed,
        managed=skill_managed and mcp_managed,
        skill_ready=skill_ready,
        mcp_registered=mcp_registered,
        transport_verified=transport_status == "verified",
        tool_count=len(tools),
        tools=tools,
        problems=problems,
        configured=skill_ready and mcp_registered,
        transport_status=transport_status,
        skill_managed=skill_managed,
        mcp_managed=mcp_managed,
        managed_actions_available=managed_actions,
    )


def resolve_agent_command() -> AgentCommand:
    """Resolve an absolute, reproducible command for MCP host configuration."""
    override = os.environ.get(AI_AGENT_BIN_ENV_VAR, "").strip()
    if override:
        candidate = Path(override).expanduser().absolute()
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError("Configured CC Port agent binary is unavailable or unsafe.")
        return AgentCommand(str(candidate), ["mcp", "--stdio"], "environment")
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).absolute()
        sibling_names = _bundled_agent_sibling_names(executable)
        for name in sibling_names:
            sibling = executable.with_name(name)
            if sibling.is_file() and not sibling.is_symlink():
                return AgentCommand(str(sibling), ["mcp", "--stdio"], "bundled-sibling")
        if executable.name.casefold().startswith("cc-port"):
            return AgentCommand(str(executable), ["mcp", "--stdio"], "bundled")
    installed = shutil.which("cc-port")
    if installed:
        candidate = Path(installed).absolute()
        if candidate.is_file() and not candidate.is_symlink():
            return AgentCommand(str(candidate), ["mcp", "--stdio"], "path")
    python = Path(sys.executable).absolute()
    if not python.is_file() or python.is_symlink():
        raise ValueError("Python executable for the CC Port MCP server is unavailable or unsafe.")
    if importlib.util.find_spec("cc_port.interfaces.mcp_server") is None:
        raise ValueError("CC Port MCP module is unavailable.")
    return AgentCommand(
        str(python),
        ["-m", "cc_port.interfaces.mcp_server"],
        "python-module",
    )


def _bundled_agent_sibling_names(executable: Path) -> list[str]:
    prefix = "cc-port-desktop-api"
    name = executable.name
    if not name.casefold().startswith(prefix):
        return []
    suffix = name[len(prefix) :]
    candidates = [f"cc-port{suffix}"] if suffix else []
    candidates.extend(["cc-port.exe", "cc-port"])
    return list(dict.fromkeys(candidates))


def _build_plan(
    profile_id: str,
    *,
    action: IntegrationAction,
    overwrite_unmanaged: bool,
    config: Config | None,
    command: AgentCommand | None,
    operation_id: str,
    persist: bool,
    request_approval: bool,
) -> AiIntegrationPlan:
    if action not in {"install", "uninstall"}:
        raise ValueError("AI integration action must be install or uninstall.")
    cfg = config or load_config()
    profile = _required_profile(cfg, profile_id)
    resolved_command = command or resolve_agent_command()
    target = _inspect_target(
        profile,
        action=action,
        overwrite_unmanaged=overwrite_unmanaged,
        command=resolved_command,
        config=cfg,
    )
    blockers = list(target.blockers)
    if resource_repo_private_path_conflicts(cfg):
        blockers.append("The resource repository overlaps a machine-local integration target.")
    payload = {
        "schema_version": AI_INTEGRATION_SCHEMA_VERSION,
        "action": action,
        "profile_id": profile.name,
        "profile_identity": {
            "tool_id": profile.effective_tool_id,
            "environment_kind": profile.environment_kind,
            "environment_name": profile.environment_name,
        },
        "command": resolved_command.command,
        "command_args": list(resolved_command.args),
        "command_source": resolved_command.source,
        "overwrite_unmanaged": bool(overwrite_unmanaged),
        "target": asdict(target),
        "blockers": list(blockers),
    }
    plan = AiIntegrationPlan(
        operation_id=_operation_id(operation_id),
        action=action,
        profile_id=profile.name,
        command=resolved_command.command,
        command_args=list(resolved_command.args),
        command_source=resolved_command.source,
        target=target,
        plan_hash=_stable_hash(payload),
        blocked=bool(blockers),
        blockers=blockers,
        requires_approval=bool(target.actions and not blockers),
        overwrite_unmanaged=bool(overwrite_unmanaged),
    )
    if request_approval:
        plan = _attach_approval(plan)
    if persist:
        _save_plan(plan)
    return plan


def _attach_approval(plan: AiIntegrationPlan) -> AiIntegrationPlan:
    if plan.blocked or not plan.target.actions:
        return plan
    request = create_approval_request(
        kind="ai-integration",
        operation_id=plan.operation_id,
        plan_hash=plan.plan_hash,
        scope=_approval_scope(plan),
        summary=f"{plan.action.title()} CC Port AI integration for {plan.profile_id}",
        metadata={
            "profile_id": plan.profile_id,
            "action": plan.action,
            "tool_id": plan.target.tool_id,
            "actions": list(plan.target.actions),
        },
    )
    plan.approval_id = request.approval_id
    return plan


def _invalidate_plan_approval(
    plan: AiIntegrationPlan,
    supplied_approval_id: str,
) -> None:
    if not plan.approval_id:
        return
    if supplied_approval_id != plan.approval_id:
        raise ApprovalRequiredError(
            "The supplied approval id does not match the stored integration plan.",
            approval_id=supplied_approval_id,
        )
    invalidate_approval_request(
        supplied_approval_id,
        kind="ai-integration",
        operation_id=plan.operation_id,
        plan_hash=plan.plan_hash,
        scope=_approval_scope(plan),
    )


def _replacement_stale_result(
    stored: AiIntegrationPlan,
    *,
    expected_plan_hash: str,
    replacement: AiIntegrationPlan,
) -> AiIntegrationResult:
    replacement = _attach_approval(replacement)
    _save_plan(replacement)
    return AiIntegrationResult(
        status="stale-plan",
        operation_id=stored.operation_id,
        plan_hash=expected_plan_hash,
        profile_id=stored.profile_id,
        changed=False,
        verified=False,
        stale_plan=replacement,
        blockers=list(replacement.blockers),
        message="Integration targets changed; review and approve the replacement plan.",
    )


def _inspect_target(
    profile: PlatformProfile,
    *,
    action: IntegrationAction,
    overwrite_unmanaged: bool,
    command: AgentCommand,
    config: Config,
) -> AiIntegrationTarget:
    blockers: list[str] = []
    available, _ = _profile_scan_available(profile, runtime_home=Path.home())
    if not profile.enabled and action == "install":
        blockers.append("The selected profile is disabled.")
    if profile.environment_kind.strip().lower() == "wsl":
        blockers.append("WSL AI integration is unavailable in schema version 1.")
    if not available:
        blockers.append("The selected runtime profile is unavailable.")
    skill_root = profile.skills_path()
    skill_path = skill_root / AI_SKILL_NAME if skill_root is not None else None
    config_path, config_format = _mcp_config_target(profile)
    initial_skill_cas = _stable_hash(_skill_component_snapshot(skill_path))
    initial_mcp_cas = _stable_hash(
        _mcp_component_snapshot(
            profile,
            config_path=config_path,
            config_format=config_format,
        )
    )
    if skill_path is None:
        blockers.append("The selected profile has no configured skills directory.")
    if config_path is None:
        blockers.append("The selected profile has no supported MCP configuration target.")
    blockers.extend(
        _integration_target_boundary_blockers(
            config,
            profile,
            skill_path=skill_path,
            config_path=config_path,
        )
    )
    source = _skill_source()
    desired_skill_hash = resource_hash_path(source)
    desired_entry = _desired_entry(command)
    desired_entry_hash = _stable_hash(desired_entry)
    skill_status = "unsupported"
    current_skill_hash = ""
    if skill_path is not None:
        path_problem = _unsafe_write_path_problem(skill_path)
        if path_problem:
            blockers.append("The Skill target has an unsafe path component.")
            skill_status = "blocked"
        elif skill_path.exists() or skill_path.is_symlink():
            probe = probe_local_path(skill_path)
            if probe.path_kind != "regular" or not probe.ready or not skill_path.is_dir():
                blockers.append("The Skill target is a link, reparse point, or non-directory.")
                skill_status = "blocked"
            else:
                current_skill_hash = resource_hash_path(skill_path)
                managed = _skill_is_managed(skill_path)
                if current_skill_hash == desired_skill_hash:
                    skill_status = "installed" if managed else "compatible"
                elif managed:
                    skill_status = "update"
                elif overwrite_unmanaged and action == "install":
                    skill_status = "replace-unmanaged"
                elif action == "uninstall":
                    skill_status = "unmanaged"
                else:
                    skill_status = "unmanaged"
                    blockers.append("An unmanaged CC Port Skill already exists at the target.")
        else:
            skill_status = "missing"
    mcp_status = "unsupported"
    current_config_hash = ""
    if config_path is not None:
        path_problem = _unsafe_write_path_problem(config_path)
        if path_problem:
            blockers.append("The MCP configuration target has an unsafe path component.")
            mcp_status = "blocked"
        elif config_path.exists() and not config_path.is_file():
            blockers.append("The MCP configuration target is not a regular file.")
            mcp_status = "blocked"
        else:
            current_config_hash = hash_path(config_path)
            try:
                existing_entry, canonical_block = _read_mcp_entry(
                    config_path,
                    config_format,
                )
            except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError):
                blockers.append("The MCP configuration cannot be parsed safely.")
                mcp_status = "blocked"
            else:
                owned = _mcp_is_owned(profile.name, config_path, existing_entry) and (
                    config_format != "codex-toml" or canonical_block
                )
                if existing_entry is None:
                    mcp_status = "missing"
                elif _entry_compatible(existing_entry, desired_entry):
                    mcp_status = "installed" if owned else "compatible"
                elif owned:
                    mcp_status = "update"
                elif overwrite_unmanaged and action == "install":
                    if config_format == "codex-toml":
                        mcp_status = "unmanaged"
                        blockers.append(
                            "An unmanaged Codex cc-port MCP table cannot be replaced safely."
                        )
                    else:
                        mcp_status = "replace-unmanaged"
                elif action == "uninstall":
                    mcp_status = "unmanaged"
                else:
                    mcp_status = "unmanaged"
                    blockers.append("A different unmanaged cc-port MCP entry already exists.")
    actions: list[str] = []
    if not blockers:
        if action == "install":
            if skill_status in {"missing", "update", "replace-unmanaged"}:
                if skill_status != "installed":
                    actions.append("install-skill")
            if mcp_status in {"missing", "update", "replace-unmanaged"}:
                actions.append("register-mcp")
            if mcp_status == "compatible":
                actions.append("record-compatible-mcp")
        else:
            if skill_status == "installed":
                actions.append("remove-skill")
            if mcp_status == "installed":
                actions.append("remove-mcp")
            if skill_status == "unmanaged" or mcp_status in {"unmanaged", "compatible"}:
                # Uninstall is ownership-sensitive: leave compatible/unmanaged
                # host content intact instead of turning it into a plan blocker.
                pass
    current_skill_cas = _stable_hash(_skill_component_snapshot(skill_path))
    current_mcp_cas = _stable_hash(
        _mcp_component_snapshot(
            profile,
            config_path=config_path,
            config_format=config_format,
        )
    )
    current_ownership_hash = _safe_hash_path(_ownership_path(profile.name))
    if initial_skill_cas != current_skill_cas:
        blockers.append("The Skill target changed during integration inspection.")
    if initial_mcp_cas != current_mcp_cas:
        blockers.append("The MCP target changed during integration inspection.")
    if initial_skill_cas != current_skill_cas or initial_mcp_cas != current_mcp_cas:
        actions.clear()
    blockers = list(dict.fromkeys(blockers))
    return AiIntegrationTarget(
        profile_id=profile.name,
        tool_id=profile.effective_tool_id,
        display_name=profile.effective_display_name,
        environment_kind=profile.environment_kind,
        environment_name=profile.environment_name,
        available=available,
        skill_path=str(skill_path or ""),
        mcp_config_path=str(config_path or ""),
        mcp_config_format=config_format,
        skill_status=skill_status,
        mcp_status=mcp_status,
        actions=actions,
        blockers=blockers,
        current_skill_hash=current_skill_hash,
        current_config_hash=current_config_hash,
        desired_skill_hash=desired_skill_hash,
        desired_entry_hash=desired_entry_hash,
        current_skill_cas=current_skill_cas,
        current_mcp_cas=current_mcp_cas,
        current_ownership_hash=current_ownership_hash,
    )


def _apply_install(
    plan: AiIntegrationPlan,
    profile: PlatformProfile,
    config: Config,
    transaction: LocalChangeTransaction,
) -> None:
    source = _skill_source()
    skill_path = Path(plan.target.skill_path)
    config_path = Path(plan.target.mcp_config_path)
    _assert_integration_targets_safe(config, profile, skill_path, config_path)
    if "install-skill" in plan.target.actions:
        manifest = _validated_skill_tree(source)
        _assert_skill_component_cas(plan)
        transaction.mark_attempted([skill_path])
        remove_path_if_exists(skill_path)
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        _copy_validated_skill_tree(source, skill_path, manifest=manifest)
        marker = {
            "schema_version": 1,
            "owner": AI_OWNER,
            "resource": "skill:cc-port",
            "source_hash": plan.target.desired_skill_hash,
        }
        _write_json_atomic(skill_path / AI_MARKER_NAME, marker)
    desired = _desired_entry(
        AgentCommand(plan.command, list(plan.command_args), plan.command_source)
    )
    if "register-mcp" in plan.target.actions:
        _assert_mcp_component_cas(plan, profile)
        transaction.mark_attempted([config_path])
        _write_mcp_entry(config_path, plan.target.mcp_config_format, desired)
    if "register-mcp" in plan.target.actions or "record-compatible-mcp" in plan.target.actions:
        if "register-mcp" not in plan.target.actions:
            _assert_mcp_component_cas(plan, profile)
        _assert_ownership_component_cas(plan)
        transaction.mark_attempted([_ownership_path(profile.name)])
        _write_ownership(
            profile,
            config_path=config_path,
            entry=desired,
            owns_entry="record-compatible-mcp" not in plan.target.actions,
        )


def _apply_uninstall(
    plan: AiIntegrationPlan,
    profile: PlatformProfile,
    config: Config,
    transaction: LocalChangeTransaction,
) -> None:
    skill_path = Path(plan.target.skill_path)
    config_path = Path(plan.target.mcp_config_path)
    _assert_integration_targets_safe(config, profile, skill_path, config_path)
    if "remove-skill" in plan.target.actions:
        _assert_skill_component_cas(plan)
        if not _skill_is_managed(skill_path):
            raise RuntimeError("Skill ownership changed before uninstall.")
        transaction.mark_attempted([skill_path])
        remove_path_if_exists(skill_path)
    if "remove-mcp" in plan.target.actions:
        _assert_mcp_component_cas(plan, profile)
        existing, canonical_block = _read_mcp_entry(
            config_path,
            plan.target.mcp_config_format,
        )
        if not _mcp_is_owned(profile.name, config_path, existing) or (
            plan.target.mcp_config_format == "codex-toml" and not canonical_block
        ):
            raise RuntimeError("MCP entry ownership changed before uninstall.")
        transaction.mark_attempted([config_path])
        _remove_mcp_entry(config_path, plan.target.mcp_config_format)
    if "remove-mcp" in plan.target.actions:
        ownership = _ownership_path(profile.name)
        _assert_ownership_component_cas(plan)
        if ownership.is_file() and not ownership.is_symlink():
            transaction.mark_attempted([ownership])
            ownership.unlink()


def _change_paths(plan: AiIntegrationPlan) -> list[Path]:
    paths: list[Path] = []
    if any(action.endswith("skill") for action in plan.target.actions):
        paths.append(Path(plan.target.skill_path))
    if any(action.endswith("mcp") for action in plan.target.actions):
        paths.append(Path(plan.target.mcp_config_path))
    if plan.target.actions:
        paths.append(_ownership_path(plan.profile_id))
    unique: dict[str, Path] = {}
    for path in paths:
        absolute = path.expanduser().absolute()
        unique.setdefault(os.path.normcase(str(absolute)), absolute)
    return list(unique.values())


def _uninstall_actions_verified(
    plan: AiIntegrationPlan,
    verification: AiIntegrationVerification,
) -> bool:
    if "remove-skill" in plan.target.actions and verification.skill_managed:
        return False
    if "remove-mcp" in plan.target.actions and verification.mcp_managed:
        return False
    return True


def _skill_component_snapshot(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"configured": False}
    exists = path.exists() or path.is_symlink()
    if not exists:
        return {"configured": True, "exists": False}
    try:
        probe = probe_local_path(path)
    except OSError:
        return {"configured": True, "exists": True, "state": "unreadable"}
    snapshot: dict[str, Any] = {
        "configured": True,
        "exists": True,
        "path_kind": probe.path_kind,
        "health": probe.health,
        "directory": path.is_dir(),
    }
    if probe.path_kind != "regular" or not probe.ready or not path.is_dir():
        return snapshot
    try:
        snapshot.update(
            {
                "content_hash": resource_hash_path(path),
                "marker_hash": hash_path(path / AI_MARKER_NAME),
                "managed": _skill_is_managed(path),
            }
        )
    except OSError:
        snapshot["state"] = "unreadable"
    return snapshot


def _safe_hash_path(path: Path) -> str:
    try:
        return hash_path(path)
    except OSError:
        return "unreadable"


def _mcp_component_snapshot(
    profile: PlatformProfile,
    *,
    config_path: Path | None,
    config_format: str,
) -> dict[str, Any]:
    if config_path is None:
        return {"configured": False}
    exists = config_path.exists() or config_path.is_symlink()
    ownership_hash = _safe_hash_path(_ownership_path(profile.name))
    snapshot: dict[str, Any] = {
        "configured": True,
        "exists": exists,
        "format": config_format,
        "ownership_hash": ownership_hash,
    }
    if not exists:
        return snapshot
    try:
        probe = probe_local_path(config_path)
        snapshot.update(
            {
                "path_kind": probe.path_kind,
                "health": probe.health,
                "file": config_path.is_file(),
                "config_hash": hash_path(config_path),
            }
        )
        if probe.path_kind != "regular" or not probe.ready or not config_path.is_file():
            return snapshot
        entry, canonical_block = _read_mcp_entry(config_path, config_format)
        snapshot.update(
            {
                "entry_hash": _stable_hash(entry) if entry is not None else "",
                "canonical_block": canonical_block,
                "owned": _mcp_is_owned(profile.name, config_path, entry),
            }
        )
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        snapshot["state"] = "unreadable-or-invalid"
    return snapshot


def _assert_skill_component_cas(plan: AiIntegrationPlan) -> None:
    first = _stable_hash(_skill_component_snapshot(Path(plan.target.skill_path)))
    second = _stable_hash(_skill_component_snapshot(Path(plan.target.skill_path)))
    if (
        not plan.target.current_skill_cas
        or first != second
        or second != plan.target.current_skill_cas
    ):
        raise AiIntegrationCasMismatch("The Skill target changed after plan validation.")


def _assert_mcp_component_cas(
    plan: AiIntegrationPlan,
    profile: PlatformProfile,
) -> None:
    first = _stable_hash(
        _mcp_component_snapshot(
            profile,
            config_path=Path(plan.target.mcp_config_path),
            config_format=plan.target.mcp_config_format,
        )
    )
    second = _stable_hash(
        _mcp_component_snapshot(
            profile,
            config_path=Path(plan.target.mcp_config_path),
            config_format=plan.target.mcp_config_format,
        )
    )
    if (
        not plan.target.current_mcp_cas
        or first != second
        or second != plan.target.current_mcp_cas
    ):
        raise AiIntegrationCasMismatch("The MCP target changed after plan validation.")


def _assert_ownership_component_cas(plan: AiIntegrationPlan) -> None:
    path = _ownership_path(plan.profile_id)
    first = _safe_hash_path(path)
    second = _safe_hash_path(path)
    if first != second or second != plan.target.current_ownership_hash:
        raise AiIntegrationCasMismatch("The MCP ownership target changed after plan validation.")


def _approval_scope(plan: AiIntegrationPlan) -> dict[str, Any]:
    return {
        "profile_id": plan.profile_id,
        "tool_id": plan.target.tool_id,
        "environment_kind": plan.target.environment_kind,
        "environment_name": plan.target.environment_name,
        "action": plan.action,
        "changes": sorted(plan.target.actions),
        "skill_target_hash": _stable_hash(plan.target.skill_path),
        "mcp_target_hash": _stable_hash(plan.target.mcp_config_path),
    }


def _required_profile(config: Config, profile_id: str) -> PlatformProfile:
    profile = config.platforms.get(str(profile_id).strip())
    if profile is None:
        raise ValueError("Unknown platform profile id.")
    return profile


def _mcp_config_target(profile: PlatformProfile) -> tuple[Path | None, str]:
    if profile.mcp_json:
        return profile.mcp_json_path(), "json"
    if profile.effective_tool_id == "codex" and profile.settings_path:
        return profile.settings_file(), "codex-toml"
    return None, ""


def _integration_target_boundary_blockers(
    config: Config,
    profile: PlatformProfile,
    *,
    skill_path: Path | None,
    config_path: Path | None,
) -> list[str]:
    """Return path-class blockers without exposing machine-local path values."""
    selected = [path for path in (skill_path, config_path) if path is not None]
    blockers: list[str] = []
    protected = (
        (
            "The AI integration target overlaps the machine-local state boundary.",
            default_state_dir(),
        ),
        (
            "The AI integration target overlaps the CC Port configuration boundary.",
            config.source_path or default_config_path(),
        ),
        (
            "The AI integration target overlaps the resource repository boundary.",
            config.resources.local_path_value,
        ),
        (
            "The AI integration target overlaps the legacy install boundary.",
            config.install.target_path,
        ),
    )
    for target in selected:
        for message, private_path in protected:
            if _paths_overlap(target, private_path):
                blockers.append(message)
    if skill_path is not None and config_path is not None and _paths_overlap(
        skill_path,
        config_path,
    ):
        blockers.append("The Skill and MCP integration targets overlap each other.")
    for other in config.platforms.profiles:
        if other.name == profile.name:
            continue
        other_targets = (
            other.skills_path(),
            other.mcp_json_path(),
            other.rules_path(),
            other.prompts_path(),
            other.plugins_path(),
            other.instructions_file(),
            other.memories_path(),
            other.settings_file(),
        )
        for target in selected:
            if any(
                candidate is not None and _paths_overlap(target, candidate)
                for candidate in other_targets
            ):
                blockers.append(
                    "The AI integration target overlaps another configured profile boundary."
                )
    return list(dict.fromkeys(blockers))


def _assert_integration_targets_safe(
    config: Config,
    profile: PlatformProfile,
    skill_path: Path,
    config_path: Path,
) -> None:
    expected_skill_root = profile.skills_path()
    expected_skill = (
        expected_skill_root / AI_SKILL_NAME
        if expected_skill_root is not None
        else None
    )
    expected_config, _ = _mcp_config_target(profile)
    if (
        expected_skill is None
        or expected_config is None
        or _path_keys([skill_path, config_path])
        != _path_keys([expected_skill, expected_config])
    ):
        raise RuntimeError("AI integration profile targets changed before apply.")
    blockers = _integration_target_boundary_blockers(
        config,
        profile,
        skill_path=skill_path,
        config_path=config_path,
    )
    if blockers:
        raise RuntimeError("AI integration target boundaries changed before apply.")
    if _unsafe_write_path_problem(skill_path) or _unsafe_write_path_problem(config_path):
        raise RuntimeError("AI integration target safety changed before apply.")


def _canonical_boundary_path(path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = candidate.absolute()
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return candidate


def _paths_overlap(first: Path, second: Path) -> bool:
    left = os.path.normcase(str(_canonical_boundary_path(first)))
    right = os.path.normcase(str(_canonical_boundary_path(second)))
    try:
        common = os.path.commonpath((left, right))
    except ValueError:
        return False
    return common == left or common == right


def _path_keys(paths: list[Path]) -> frozenset[str]:
    return frozenset(
        os.path.normcase(str(path.expanduser().absolute()))
        for path in paths
    )


def _packaged_skill_root() -> Path:
    return Path(__file__).absolute().parents[1] / "assets" / "ai" / AI_SKILL_NAME


def _skill_source() -> Path:
    source = _packaged_skill_root()
    marker = source / "SKILL.md"
    source_probe = probe_local_path(source)
    if (
        source_probe.path_kind != "regular"
        or not source_probe.ready
        or not source.is_dir()
        or _unsafe_write_path_problem(source)
    ):
        raise FileNotFoundError("Packaged CC Port AI Skill is unavailable or unsafe.")
    marker_probe = probe_local_path(marker)
    if (
        marker_probe.path_kind != "regular"
        or not marker_probe.ready
        or not marker.is_file()
    ):
        raise FileNotFoundError("Packaged CC Port AI Skill is unavailable.")
    if resource_tree_issues(source):
        raise ValueError("Packaged CC Port AI Skill contains an unsafe or unreadable entry.")
    return source


def _validated_skill_tree(source: Path) -> tuple[list[Path], list[Path]]:
    """Return relative ordinary directories/files for a fail-closed copy."""
    if source != _skill_source():
        raise ValueError("Packaged CC Port AI Skill source identity changed.")
    directories: list[Path] = []
    files: list[Path] = []

    def on_error(_exc: OSError) -> None:
        raise ValueError("Packaged CC Port AI Skill cannot be read safely.")

    for directory, dirnames, filenames in os.walk(
        source,
        topdown=True,
        followlinks=False,
        onerror=on_error,
    ):
        current = Path(directory)
        retained: list[str] = []
        for name in sorted(dirnames):
            candidate = current / name
            probe = probe_local_path(candidate)
            if probe.path_kind != "regular" or not probe.ready or not candidate.is_dir():
                raise ValueError(
                    "Packaged CC Port AI Skill contains an unsafe or unreadable entry."
                )
            relative = candidate.relative_to(source)
            directories.append(relative)
            retained.append(name)
        dirnames[:] = retained
        for name in sorted(filenames):
            candidate = current / name
            probe = probe_local_path(candidate)
            if probe.path_kind != "regular" or not probe.ready or not candidate.is_file():
                raise ValueError(
                    "Packaged CC Port AI Skill contains an unsafe or unreadable entry."
                )
            files.append(candidate.relative_to(source))
    if Path("SKILL.md") not in files:
        raise ValueError("Packaged CC Port AI Skill is missing its entry document.")
    return directories, files


def _copy_validated_skill_tree(
    source: Path,
    destination: Path,
    *,
    manifest: tuple[list[Path], list[Path]] | None = None,
) -> None:
    current_manifest = _validated_skill_tree(source)
    if manifest is not None and current_manifest != manifest:
        raise ValueError("Packaged CC Port AI Skill changed during installation.")
    directories, files = current_manifest
    destination.mkdir(parents=True, exist_ok=False)
    for relative in directories:
        (destination / relative).mkdir(parents=True, exist_ok=False)
    for relative in files:
        source_file = source / relative
        before = source_file.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(
                "Packaged CC Port AI Skill changed to an unsafe entry during installation."
            )
        descriptor = os.open(source_file, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise ValueError(
                    "Packaged CC Port AI Skill changed during installation."
                )
            with os.fdopen(descriptor, "rb", closefd=False) as source_handle:
                with (destination / relative).open("xb") as destination_handle:
                    shutil.copyfileobj(source_handle, destination_handle)
            os.chmod(destination / relative, stat.S_IMODE(before.st_mode))
        finally:
            os.close(descriptor)


def _skill_is_managed(path: Path) -> bool:
    marker = path / AI_MARKER_NAME
    if not marker.is_file() or marker.is_symlink():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(data, dict)
        and data.get("schema_version") == 1
        and data.get("owner") == AI_OWNER
        and data.get("resource") == "skill:cc-port"
    )


def _desired_entry(command: AgentCommand) -> dict[str, Any]:
    return {"command": command.command, "args": list(command.args)}


def _entry_compatible(existing: dict[str, Any], desired: dict[str, Any]) -> bool:
    return (
        str(existing.get("command") or "") == desired["command"]
        and [str(item) for item in existing.get("args", [])] == desired["args"]
    )


def _read_mcp_entry(path: Path, format_name: str) -> tuple[dict[str, Any] | None, bool]:
    if not path.is_file():
        return None, False
    text = path.read_text(encoding="utf-8")
    if format_name == "json":
        data = json.loads(text or "{}")
        if not isinstance(data, dict):
            raise ValueError("MCP JSON root must be an object.")
        servers = data.get("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("mcpServers must be an object.")
        entry = servers.get(AI_SERVER_NAME)
        if entry is None:
            return None, False
        if not isinstance(entry, dict):
            raise ValueError("cc-port MCP entry must be an object.")
        return dict(entry), False
    if format_name == "codex-toml":
        data = tomllib.loads(text) if text.strip() else {}
        servers = data.get("mcp_servers", {})
        if not isinstance(servers, dict):
            raise ValueError("mcp_servers must be a TOML table.")
        entry = servers.get(AI_SERVER_NAME)
        if entry is not None and not isinstance(entry, dict):
            raise ValueError("cc-port MCP entry must be a TOML table.")
        parsed_entry = dict(entry) if isinstance(entry, dict) else None
        managed = _validated_codex_block_span(text, parsed_entry) is not None
        return parsed_entry, managed
    raise ValueError("Unsupported MCP configuration format.")


def _write_mcp_entry(path: Path, format_name: str, entry: dict[str, Any]) -> None:
    if format_name == "json":
        data: dict[str, Any] = {}
        if path.is_file():
            parsed = json.loads(path.read_text(encoding="utf-8") or "{}")
            if not isinstance(parsed, dict):
                raise ValueError("MCP JSON root must be an object.")
            data = parsed
        servers = data.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("mcpServers must be an object.")
        servers[AI_SERVER_NAME] = entry
        _write_json_atomic(path, data)
        return
    if format_name == "codex-toml":
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if re.search(r"(?m)^\s*mcp_servers\s*=", text):
            raise ValueError("Inline mcp_servers TOML cannot be modified safely.")
        existing, managed = _read_mcp_entry(path, format_name) if path.is_file() else (None, False)
        if existing is not None and not managed:
            raise ValueError("An unmanaged Codex cc-port MCP table already exists.")
        text = _remove_codex_managed_block(text)
        block = _render_codex_managed_block(entry)
        combined = text.rstrip() + ("\n\n" if text.strip() else "") + block
        tomllib.loads(combined)
        _write_text_atomic(path, combined)
        return
    raise ValueError("Unsupported MCP configuration format.")


def _remove_mcp_entry(path: Path, format_name: str) -> None:
    if format_name == "json":
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("MCP JSON root must be an object.")
        servers = data.get("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("mcpServers must be an object.")
        servers.pop(AI_SERVER_NAME, None)
        _write_json_atomic(path, data)
        return
    if format_name == "codex-toml":
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        updated = _remove_codex_managed_block(text)
        if updated.strip():
            tomllib.loads(updated)
        _write_text_atomic(path, updated)
        return
    raise ValueError("Unsupported MCP configuration format.")


def _remove_codex_managed_block(text: str) -> str:
    if CODEX_BLOCK_BEGIN not in text and CODEX_BLOCK_END not in text:
        return text
    data = tomllib.loads(text) if text.strip() else {}
    servers = data.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcp_servers must be a TOML table.")
    entry = servers.get(AI_SERVER_NAME)
    parsed_entry = dict(entry) if isinstance(entry, dict) else None
    span = _validated_codex_block_span(text, parsed_entry)
    if span is None:
        raise ValueError("Codex managed MCP block markers are incomplete or ambiguous.")
    updated = text[: span[0]] + text[span[1] :]
    return updated.rstrip() + ("\n" if updated.strip() else "")


def _render_codex_managed_block(entry: dict[str, Any]) -> str:
    if set(entry) != {"command", "args"}:
        raise ValueError("Codex managed MCP entry has unexpected configuration.")
    command = entry.get("command")
    args = entry.get("args")
    if not isinstance(command, str) or not isinstance(args, list) or not all(
        isinstance(item, str) for item in args
    ):
        raise ValueError("Codex managed MCP entry has an invalid command contract.")
    return (
        f"{CODEX_BLOCK_BEGIN}\n"
        "[mcp_servers.cc-port]\n"
        f"command = {json.dumps(command, ensure_ascii=True)}\n"
        f"args = {json.dumps(args, ensure_ascii=True)}\n"
        f"{CODEX_BLOCK_END}\n"
    )


def _validated_codex_block_span(
    text: str,
    entry: dict[str, Any] | None,
) -> tuple[int, int] | None:
    begin_count = text.count(CODEX_BLOCK_BEGIN)
    end_count = text.count(CODEX_BLOCK_END)
    if begin_count == 0 and end_count == 0:
        return None
    if begin_count != 1 or end_count != 1 or entry is None:
        raise ValueError("Codex managed MCP block markers are incomplete or ambiguous.")
    begin = re.search(rf"(?m)^{re.escape(CODEX_BLOCK_BEGIN)}\n", text)
    end = re.search(rf"(?m)^{re.escape(CODEX_BLOCK_END)}(?:\n|$)", text)
    if begin is None or end is None or begin.start() >= end.start():
        raise ValueError("Codex managed MCP block markers are incomplete or ambiguous.")
    actual = text[begin.start() : end.end()]
    if not actual.endswith("\n"):
        actual += "\n"
    if actual != _render_codex_managed_block(entry):
        raise ValueError("Codex managed MCP block is not canonical.")
    return begin.start(), end.end()


def _ownership_path(profile_id: str) -> Path:
    return default_state_dir() / AI_OWNERSHIP_DIR / f"{profile_id}.json"


def _write_ownership(
    profile: PlatformProfile,
    *,
    config_path: Path,
    entry: dict[str, Any],
    owns_entry: bool,
) -> None:
    payload = {
        "schema_version": 1,
        "owner": AI_OWNER,
        "profile_id": profile.name,
        "tool_id": profile.effective_tool_id,
        "config_path_hash": _stable_hash(str(config_path.expanduser().absolute())),
        "entry_hash": _stable_hash(entry),
        "owns_entry": owns_entry,
    }
    _write_json_atomic(_ownership_path(profile.name), payload)


def _mcp_is_owned(
    profile_id: str,
    config_path: Path,
    entry: dict[str, Any] | None,
) -> bool:
    if entry is None:
        return False
    path = _ownership_path(profile_id)
    if not path.is_file() or path.is_symlink():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(data, dict)
        and set(data)
        == {
            "schema_version",
            "owner",
            "profile_id",
            "tool_id",
            "config_path_hash",
            "entry_hash",
            "owns_entry",
        }
        and data.get("schema_version") == 1
        and data.get("owner") == AI_OWNER
        and data.get("profile_id") == profile_id
        and data.get("owns_entry") is True
        and data.get("config_path_hash")
        == _stable_hash(str(config_path.expanduser().absolute()))
        and data.get("entry_hash") == _stable_hash(entry)
    )


def _unsafe_write_path_problem(path: Path) -> str:
    candidate = path.expanduser().absolute()
    existing = candidate
    while not existing.exists() and not existing.is_symlink() and existing != existing.parent:
        existing = existing.parent
    chain = [existing, *existing.parents]
    for component in chain:
        try:
            probe = probe_local_path(component)
        except OSError:
            return "unreadable"
        if probe.path_kind != "regular" or not probe.ready:
            return probe.health
    if candidate.exists() or candidate.is_symlink():
        probe = probe_local_path(candidate)
        if probe.path_kind != "regular" or not probe.ready:
            return probe.health
    return ""


def _verify_stdio(command: AgentCommand, *, timeout_seconds: float) -> list[str]:
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "cc-port-verifier", "version": "1"},
        },
    }
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    list_tools = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    status = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "cc_port_status", "arguments": {}},
    }
    payload = "\n".join(
        json.dumps(item, separators=(",", ":"))
        for item in (initialize, initialized, list_tools, status)
    ) + "\n"
    try:
        responses, stderr, returncode = _stdio_probe_exchange(
            command,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("MCP stdio verification timed out.") from exc
    if 1 not in responses or "result" not in responses[1]:
        raise RuntimeError(
            _probe_failure_detail(
                "MCP initialize did not return a result.",
                stderr,
                command,
            )
        )
    tools_result = responses.get(2, {}).get("result", {})
    tools = tools_result.get("tools", []) if isinstance(tools_result, dict) else []
    if not isinstance(tools, list):
        raise RuntimeError("MCP tools/list returned an invalid result.")
    names = [str(item.get("name")) for item in tools if isinstance(item, dict) and item.get("name")]
    if not names:
        raise RuntimeError(
            _probe_failure_detail(
                "MCP tools/list returned no tools.",
                stderr,
                command,
            )
        )
    missing_tools = MCP_REQUIRED_CORE_TOOLS.difference(names)
    if missing_tools:
        raise RuntimeError("MCP tools/list omitted one or more required core tools.")
    status_envelope = _mcp_status_envelope(responses.get(3, {}).get("result"))
    if status_envelope.get("contract_version") != MCP_CONTRACT_VERSION:
        raise RuntimeError("cc_port_status returned an unsupported contract version.")
    if status_envelope.get("ok") is not True:
        raise RuntimeError("cc_port_status did not return a successful envelope.")
    if not isinstance(status_envelope.get("status"), str) or not status_envelope[
        "status"
    ].strip():
        raise RuntimeError("cc_port_status returned an invalid status contract.")
    data = status_envelope.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("cc_port_status returned an invalid data contract.")
    if (
        data.get("automation_policy") != "plan-apply-verify"
        or data.get("approval_mode") != "desktop-only"
        or data.get("approval_tools_exposed") is not False
    ):
        raise RuntimeError("cc_port_status returned an unsafe automation policy contract.")
    recommended = data.get("recommended_tools")
    if not isinstance(recommended, list) or not all(
        isinstance(item, str) for item in recommended
    ):
        raise RuntimeError("cc_port_status returned an invalid recommended tool contract.")
    if not MCP_REQUIRED_CORE_TOOLS.issubset(recommended):
        raise RuntimeError("cc_port_status omitted one or more recommended core tools.")
    if returncode != 0:
        raise RuntimeError(
            _probe_failure_detail(
                "MCP stdio process exited unsuccessfully.",
                stderr,
                command,
            )
        )
    return names


def _stdio_probe_exchange(
    command: AgentCommand,
    *,
    payload: str,
    timeout_seconds: float,
) -> tuple[dict[int, dict[str, Any]], str, int]:
    process = subprocess.Popen(
        [command.command, *command.args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=_stdio_probe_environment(),
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        process.wait()
        raise RuntimeError("MCP stdio pipes could not be established.")
    stdout_queue: queue.Queue[str | None] = queue.Queue()
    stderr_parts: list[str] = []
    stderr_length = 0
    stderr_lock = threading.Lock()

    def read_stdout() -> None:
        try:
            for line in process.stdout:
                stdout_queue.put(line)
        finally:
            stdout_queue.put(None)

    def read_stderr() -> None:
        nonlocal stderr_length
        while chunk := process.stderr.read(1024):
            with stderr_lock:
                remaining = MCP_PROBE_STDERR_CAPTURE_LIMIT - stderr_length
                if remaining > 0:
                    captured = chunk[:remaining]
                    stderr_parts.append(captured)
                    stderr_length += len(captured)

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    responses: dict[int, dict[str, Any]] = {}
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    try:
        process.stdin.write(payload)
        process.stdin.flush()
        while not {1, 2, 3}.issubset(responses):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                line = stdout_queue.get(timeout=remaining)
            except queue.Empty:
                timed_out = True
                break
            if line is None:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and isinstance(message.get("id"), int):
                responses[message["id"]] = message
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=min(2.0, remaining))
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        stdout_thread.join(timeout=1.0)
        stderr_thread.join(timeout=1.0)
    if timed_out:
        raise subprocess.TimeoutExpired([command.command, *command.args], timeout_seconds)
    with stderr_lock:
        stderr = "".join(stderr_parts)
    return responses, stderr, int(process.returncode or 0)


def _stdio_probe_environment() -> dict[str, str]:
    allowed = {
        "APPDATA",
        "CC_PORT_CONFIG",
        "CC_PORT_GIT_EXECUTABLE",
        "CC_PORT_RESOURCE_HOME",
        "CC_PORT_STATE_HOME",
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "WINDIR",
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in allowed and value
    }
    environment.update(
        {
            "FASTMCP_SHOW_BANNER": "false",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


def _mcp_status_envelope(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise RuntimeError("cc_port_status did not return a tool result.")
    structured = result.get("structuredContent")
    if structured is None:
        structured = result.get("structured_content")
    if isinstance(structured, dict):
        candidate = structured.get("result", structured)
        if isinstance(candidate, dict):
            return candidate
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                continue
            try:
                candidate = json.loads(item["text"])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                return candidate
    raise RuntimeError("cc_port_status returned no structured envelope.")


def _probe_failure_detail(message: str, stderr: str, command: AgentCommand) -> str:
    detail = _sanitize_probe_detail(stderr, command)
    return message + (f" {detail}" if detail else "")


def _sanitize_probe_detail(text: str, command: AgentCommand) -> str:
    redacted = redact_secret_text(str(text or ""))
    sensitive_values = [
        value
        for key, value in os.environ.items()
        if value
        and (
            any(
                token in key.upper()
                for token in (
                    "TOKEN",
                    "SECRET",
                    "PASSWORD",
                    "API_KEY",
                    "PRIVATE_KEY",
                    "CREDENTIAL",
                    "AUTH",
                )
            )
            or key.upper()
            in {
                "APPDATA",
                "CC_PORT_CONFIG",
                "CC_PORT_RESOURCE_HOME",
                "CC_PORT_STATE_HOME",
                "HOME",
                "LOCALAPPDATA",
                "USERPROFILE",
                "XDG_CONFIG_HOME",
                "XDG_STATE_HOME",
            }
        )
    ]
    sensitive_values.append(command.command)
    for value in sorted(set(sensitive_values), key=len, reverse=True):
        if len(value) >= 4:
            redacted = redacted.replace(value, "<redacted>")
    redacted = re.sub(
        r"(?<![\w.])(?:[A-Za-z]:[\\/]|\\\\|//|/)[^\s\"']+",
        "<redacted-path>",
        redacted,
    )
    compact = " ".join(redacted.split())
    if len(compact) > MCP_PROBE_DETAIL_LIMIT:
        compact = compact[: MCP_PROBE_DETAIL_LIMIT - 3].rstrip() + "..."
    return compact


def _save_plan(plan: AiIntegrationPlan) -> Path:
    root = _plan_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_operation_id(plan.operation_id)}.json"
    _write_json_atomic(path, asdict(plan))
    return path


def _plan_root() -> Path:
    return default_state_dir() / AI_PLAN_DIR


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    _write_text_atomic(path, text)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _operation_id(value: str) -> str:
    safe = str(value).strip().lower()
    if len(safe) != 32 or any(char not in "0123456789abcdef" for char in safe):
        raise ValueError("Invalid AI integration operation id.")
    return safe


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
