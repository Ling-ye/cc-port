"""CC Port command-line interface (Typer + Rich)."""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from ..agent.contracts import (
    WIRE_EXIT_INVALID_REQUEST,
    WIRE_EXIT_RUNTIME_FAILURE,
    WIRE_EXIT_SAFE_NONCOMPLETION,
    AssetBatchChoiceWire,
    AssetBatchRequestWire,
    WireEnvelope,
    asset_action_approval_scope,
    asset_action_plan_hash,
    asset_batch_approval_scope,
    asset_batch_operation_id,
    parse_asset_batch_choices,
    parse_asset_batch_request,
    to_public_wire_value,
    to_wire_value,
    wire_exit_code,
    wire_failure,
    wire_result,
    wire_success,
)
from ..core.config import (
    CONFIG_ENV_VAR,
    Config,
    GithubConfig,
    InstallConfig,
    default_config_path,
    load_config,
    write_config,
)
from ..core.platforms import (
    PLATFORM_PRESETS,
    PlatformProfile,
    PlatformsConfig,
    build_platform,
    resolve_portable_resource_platforms,
)
from ..core.registry import find_registry_path, load_registry
from ..core.resource_detection import (
    ResourceDetectionError,
    detect_local_resource_type,
    detect_remote_resource,
)
from ..core.secret_scan import redact_secret_text
from ..infrastructure import git_ops
from ..services import publisher
from ..services.ai_integration import (
    AiIntegrationPlan,
    apply_ai_integration_plan,
    build_ai_integration_plan,
    load_ai_integration_plan,
    verify_ai_integration,
)
from ..services.approval import (
    ApprovalRequest,
    ApprovalRequiredError,
    approval_scope_hash,
    consume_approval,
    create_approval_request,
    invalidate_approval_request,
    load_approval_request,
)
from ..services.asset_reconcile import (
    AssetReconcileInvalidRequest,
    AssetReconcileStaleContext,
    build_asset_reconcile_context,
)
from ..services.asset_sync import (
    AssetBatchChoice,
    add_plugin_reference,
    apply_asset_action_plan,
    apply_asset_batch_plan,
    apply_plugin_delete_plan,
    build_asset_action_plan,
    build_asset_batch_plan,
    build_asset_content_diff,
    build_asset_inventory,
    build_plugin_delete_plan,
    load_asset_action_plan,
)
from ..services.doctor import build_doctor_checks, has_doctor_errors
from ..services.installer import (
    SyncAction,
    check_all,
    status_all,
    sync_all,
    sync_one,
    uninstall_one,
)
from ..services.local_resources import export_claude_plugin, import_local_resource
from ..services.operation_history import (
    operation_detail,
    operation_history_page,
    restore_operation,
)
from ..services.plugin_management import (
    add_plugin_project,
    list_plugin_projects,
    remove_plugin_project,
)
from ..services.registry_audit import (
    RegistryRepairChoice,
    RegistryRepairPlan,
    build_registry_repair_plan,
)
from ..services.resource_commit import build_resource_commit_plan
from ..services.resource_manager import resource_install_plan
from ..services.resource_repo import (
    init_resource_repo,
    inspect_resource_repo,
    pull_resource_repo,
    push_resource_repo,
    use_resource_repo,
)
from ..services.resource_sync import (
    apply_resource_sync_plan,
    build_resource_sync_plan,
    cancel_resource_sync_plan,
    cleanup_stale_resource_sync_plan,
    inspect_resource_sync,
    list_stale_resource_sync_plans,
    resolve_resource_sync_plan,
)
from ..services.state_maintenance import (
    delete_orphan_quarantine,
    export_orphan_backup,
    list_maintenance_audits,
    list_orphan_backups,
    list_orphan_quarantines,
    load_maintenance_audit,
    quarantine_orphan_backups,
)
from ..services.state_retention import (
    StateRetentionPlan,
    build_state_retention_plan,
    prune_state,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "CC Port: publish, register and sync skills, MCP servers, rules, prompts "
        "and plugins across AI coding platforms."
    ),
)
resource_app = typer.Typer(help="Manage a portable Git resource repository.")
asset_app = typer.Typer(
    help="Inspect and synchronize logical resources across local AI tools and the private repository."
)
operations_app = typer.Typer(help="Inspect and restore persisted local write operations.")
plugin_app = typer.Typer(help="Manage dual-track plugin references and project scan roots.")
plugin_project_app = typer.Typer(help="Manage explicit project roots used by plugin scans.")
plugin_reference_app = typer.Typer(help="Manage plugin references without uploading cache content.")
integration_app = typer.Typer(
    help="Install, verify, or remove CC Port's Skill and MCP registration for one exact profile."
)
app.add_typer(resource_app, name="resource")
app.add_typer(asset_app, name="asset")
app.add_typer(operations_app, name="operations")
app.add_typer(plugin_app, name="plugin")
app.add_typer(integration_app, name="integration")
plugin_app.add_typer(plugin_project_app, name="project")
plugin_app.add_typer(plugin_reference_app, name="reference")
console = Console()
_NON_INTERACTIVE = False
VALID_KINDS = {
    "skill",
    "mcp",
    "rule",
    "prompt",
    "plugin",
    "instruction",
    "memory",
}
DEPRECATED_SYNC_MESSAGE = (
    "Deprecated: use `cc-port asset list`, `cc-port asset plan`, and `cc-port asset apply`. "
    "Git workspace sync commands will be removed in the next release."
)


@app.callback()
def configure_cli(
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help=(
            "Never prompt; pair with a command's --json option for a structured "
            "non-completion result."
        ),
    ),
) -> None:
    """Configure process-wide CLI behavior before dispatching a command."""

    global _NON_INTERACTIVE
    _NON_INTERACTIVE = non_interactive


def _load() -> Config:
    cfg = load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    return cfg


def _portable_resource_platforms(
    cfg: Config,
    kind: str,
    values: list[str],
) -> list[str] | None:
    """Keep local profile ids out of portable repository metadata."""
    portable = resolve_portable_resource_platforms(cfg.platforms, kind, values)
    return portable or None


def _print_machine_json(data: object) -> None:
    """Write stable JSON without terminal styling or ANSI escape sequences."""
    typer.echo(
        json.dumps(
            _without_desktop_message_refs(data),
            default=str,
            ensure_ascii=False,
            indent=2,
        )
    )


def _print_wire_envelope(envelope: WireEnvelope[object]) -> None:
    """Write exactly one versioned JSON envelope to stdout."""

    typer.echo(json.dumps(to_public_wire_value(envelope), ensure_ascii=False, indent=2))


def _print_wire_success(data: object, *, status: str = "succeeded") -> None:
    _print_wire_envelope(wire_success(data, status=status))


def _print_wire_result(data: object, *, status: str, message: str) -> WireEnvelope[object]:
    envelope = wire_result(data, status=status, message=message)
    _print_wire_envelope(envelope)
    return envelope


def _exit_wire_error(
    *,
    json_output: bool,
    code: str,
    message: str,
    status: str = "failed",
    exit_code: int = WIRE_EXIT_RUNTIME_FAILURE,
    data: object | None = None,
) -> None:
    if json_output:
        _print_wire_envelope(
            wire_failure(code, redact_secret_text(message), status=status, data=data)
        )
    else:
        console.print(f"[red]{escape(message)}[/red]")
    raise typer.Exit(exit_code)


def _require_interactive_input(
    message: str,
    *,
    json_output: bool = False,
    code: str = "input_required",
) -> None:
    if not _NON_INTERACTIVE:
        return
    _exit_wire_error(
        json_output=json_output,
        code=code,
        message=message,
        status="needs-confirmation" if code == "confirmation_required" else "needs-action",
        exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
    )


def _wire_asset_batch_choices(
    choices: list[AssetBatchChoice],
) -> list[AssetBatchChoiceWire]:
    return [AssetBatchChoiceWire.model_validate(to_wire_value(choice)) for choice in choices]


def _asset_action_approval_metadata(plan: object) -> dict[str, object]:
    return {
        "action": str(plan.action),  # type: ignore[attr-defined]
        "resource_key": str(plan.resource_key),  # type: ignore[attr-defined]
        "target_resource_key": str(plan.target_resource_key),  # type: ignore[attr-defined]
        "profile_id": str(plan.platform),  # type: ignore[attr-defined]
        "local_instance_id": str(plan.local_instance_id),  # type: ignore[attr-defined]
        "new_name": str(plan.new_name),  # type: ignore[attr-defined]
        "new_install_name": str(plan.new_install_name),  # type: ignore[attr-defined]
        "overwrite_unmanaged": bool(plan.overwrite_unmanaged),  # type: ignore[attr-defined]
        "link_target_confirmed": bool(plan.link_target_confirmed),  # type: ignore[attr-defined]
        "remote_commit": str(plan.remote_commit),  # type: ignore[attr-defined]
    }


def _create_asset_action_approval(
    plan: object,
) -> tuple[str, ApprovalRequest | None]:
    plan_hash = asset_action_plan_hash(plan)
    if bool(getattr(plan, "blocked", False)):
        return plan_hash, None
    scope = asset_action_approval_scope(plan)
    request = create_approval_request(
        kind="asset-action",
        operation_id=str(plan.operation_id),  # type: ignore[attr-defined]
        plan_hash=plan_hash,
        scope=scope,
        summary=(
            f"{plan.action} {plan.resource_key} "  # type: ignore[attr-defined]
            f"for profile {plan.platform}"  # type: ignore[attr-defined]
        ),
        metadata=_asset_action_approval_metadata(plan),
    )
    return plan_hash, request


def _asset_action_plan_payload(
    plan: object,
    *,
    plan_hash: str,
    approval: ApprovalRequest | None,
) -> dict[str, object]:
    payload = to_wire_value(plan)
    if not isinstance(payload, dict):
        raise TypeError("Asset action plan must be an object.")
    payload.update(
        {
            "plan_hash": plan_hash,
            "requires_approval": approval is not None,
            "approval_id": approval.approval_id if approval else "",
            "approval_status": approval.status if approval else "not-required",
            "approval_scope_hash": approval.scope_hash if approval else "",
        }
    )
    return payload


def _rebuild_asset_action_plan(
    plan: object,
    *,
    config: Config,
    persist: bool,
) -> object:
    return build_asset_action_plan(
        plan.action,  # type: ignore[attr-defined]
        kind=plan.kind,  # type: ignore[attr-defined]
        name=plan.name,  # type: ignore[attr-defined]
        platform=plan.platform,  # type: ignore[attr-defined]
        local_instance_id=plan.local_instance_id,  # type: ignore[attr-defined]
        new_name=plan.new_name,  # type: ignore[attr-defined]
        new_install_name=plan.new_install_name,  # type: ignore[attr-defined]
        overwrite_unmanaged=plan.overwrite_unmanaged,  # type: ignore[attr-defined]
        link_target_confirmed=plan.link_target_confirmed,  # type: ignore[attr-defined]
        config=config,
        _persist=persist,
    )


def _asset_batch_approval_metadata(
    request: AssetBatchRequestWire,
) -> dict[str, object]:
    choices = [
        {
            "resource_key": choice.resource_key,
            "profile_id": choice.platform,
            "local_instance_id": choice.local_instance_id,
            "resolution": choice.resolution,
            "new_name": choice.new_name,
            "overwrite_unmanaged": choice.overwrite_unmanaged,
            "plugin_track": choice.plugin_track,
            "ownership_confirmed": choice.ownership_confirmed,
            "link_target_confirmed": choice.link_target_confirmed,
            "reference_origin_present": bool(choice.reference_origin),
            "plugin_dependency_count": len(choice.plugin_dependencies),
        }
        for choice in request.choices
    ]
    return {
        "direction": request.direction,
        "resource_keys": list(request.resource_keys),
        "target_platforms": list(request.target_platforms),
        "choices": choices,
    }


def _create_asset_batch_approval(
    plan: object,
    request: AssetBatchRequestWire,
) -> ApprovalRequest | None:
    if bool(getattr(plan, "blocked_count", 0)) or not int(getattr(plan, "executable_count", 0)):
        return None
    plan_hash = str(plan.plan_hash)  # type: ignore[attr-defined]
    scope = asset_batch_approval_scope(
        direction=request.direction,
        resource_keys=request.resource_keys,
        target_platforms=request.target_platforms,
        choices=request.choices,
        plan_hash=plan_hash,
    )
    return create_approval_request(
        kind="asset-batch",
        operation_id=asset_batch_operation_id(plan_hash),
        plan_hash=plan_hash,
        scope=scope,
        summary=(
            f"{request.direction.title()} {len(request.resource_keys)} CC Port asset resource(s)"
        ),
        metadata=_asset_batch_approval_metadata(request),
    )


def _asset_batch_plan_payload(
    plan: object,
    *,
    approval: ApprovalRequest | None,
) -> dict[str, object]:
    payload = to_wire_value(plan)
    if not isinstance(payload, dict):
        raise TypeError("Asset batch plan must be an object.")
    plan_hash = str(plan.plan_hash)  # type: ignore[attr-defined]
    payload.update(
        {
            "operation_id": asset_batch_operation_id(plan_hash),
            "requires_approval": approval is not None,
            "approval_id": approval.approval_id if approval else "",
            "approval_status": approval.status if approval else "not-required",
            "approval_scope_hash": approval.scope_hash if approval else "",
        }
    )
    return payload


def _consume_cli_approval(
    approval_id: str,
    *,
    kind: str,
    operation_id: str,
    plan_hash: str,
    scope: dict[str, object],
    summary: str,
    metadata: dict[str, object],
    json_output: bool,
    data: object,
) -> ApprovalRequest | None:
    selected = approval_id.strip()
    if not selected:
        if _NON_INTERACTIVE or json_output:
            _exit_wire_error(
                json_output=json_output,
                code="approval_id_required",
                message="Apply requires an explicit human-approved --approval-id.",
                status="invalid-request",
                exit_code=WIRE_EXIT_INVALID_REQUEST,
                data=data,
            )
        request = create_approval_request(
            kind=kind,
            operation_id=operation_id,
            plan_hash=plan_hash,
            scope=scope,
            summary=summary,
            metadata=metadata,
        )
    else:
        try:
            request = load_approval_request(selected)
        except Exception as exc:
            _exit_wire_error(
                json_output=json_output,
                code="approval_unavailable",
                message=f"The approval request is unavailable: {exc}",
                status="needs-confirmation",
                exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
                data=data,
            )
    if (
        request.kind != kind
        or request.operation_id != operation_id
        or request.plan_hash != plan_hash
        or request.scope_hash != approval_scope_hash(scope)
    ):
        _exit_wire_error(
            json_output=json_output,
            code="approval_mismatch",
            message="The approval request does not match this operation, plan, or scope.",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
            data=data,
        )
    if request.status == "pending":
        _exit_wire_error(
            json_output=json_output,
            code="approval_required",
            message=(
                f"Approval request {request.approval_id} is pending; "
                "review it in the Desktop client before apply."
            ),
            status="needs-confirmation",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data=data,
        )
    elif request.status != "approved":
        _exit_wire_error(
            json_output=json_output,
            code="approval_not_active",
            message=f"The approval request cannot be used in status {request.status}.",
            status="needs-action",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data=data,
        )
    try:
        return consume_approval(
            request.approval_id,
            kind=kind,
            operation_id=operation_id,
            plan_hash=plan_hash,
            scope=scope,
        )
    except ApprovalRequiredError as exc:
        _exit_wire_error(
            json_output=json_output,
            code="approval_required",
            message=str(exc),
            status="needs-confirmation",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data=data,
        )


def _invalidate_cli_approval(
    approval_id: str,
    *,
    kind: str,
    operation_id: str,
    plan_hash: str,
    scope: dict[str, object],
    json_output: bool,
    data: object,
) -> ApprovalRequest:
    selected = approval_id.strip()
    if not selected:
        _exit_wire_error(
            json_output=json_output,
            code="approval_id_required",
            message="The reviewed approval id is required before stale-plan replacement.",
            status="needs-confirmation",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data=data,
        )
    try:
        return invalidate_approval_request(
            selected,
            kind=kind,
            operation_id=operation_id,
            plan_hash=plan_hash,
            scope=scope,
        )
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="approval_invalidation_failed",
            message=f"The stale approval could not be invalidated safely: {exc}",
            status="needs-confirmation",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data=data,
        )


def _without_desktop_message_refs(data: object) -> object:
    """Keep desktop-only localization metadata out of stable CLI JSON."""

    if isinstance(data, dict):
        return {
            key: _without_desktop_message_refs(value)
            for key, value in data.items()
            if not (isinstance(key, str) and key.endswith(("_ref", "_refs")))
        }
    if isinstance(data, list):
        return [_without_desktop_message_refs(value) for value in data]
    return data


@app.command("mcp")
def cmd_mcp(
    stdio: bool = typer.Option(
        False,
        "--stdio",
        help="Run the CC Port MCP server over stdio without terminal prose.",
    ),
) -> None:
    """Run CC Port as a discoverable MCP server for AI clients."""

    if not stdio:
        _exit_wire_error(
            json_output=False,
            code="transport_required",
            message="Select the MCP transport explicitly with --stdio.",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    _run_mcp_stdio()


def _run_mcp_stdio() -> None:
    """Import the MCP adapter lazily so ordinary CLI startup remains lightweight."""

    from .mcp_server import main

    main()


@integration_app.command("status")
def cmd_integration_status(
    profile_id: str = typer.Option(
        "",
        "--profile",
        help="Exact profile id. Omit to inspect every configured profile.",
    ),
    verify_transport: bool = typer.Option(
        False,
        "--verify-transport/--no-verify-transport",
        help="Also start the configured MCP command and request its tool list.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Inspect CC Port's Skill and MCP entry for every configured profile."""

    try:
        cfg = _load()
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="integration_status_failed",
            message=f"AI integration status failed: {exc}",
        )
    selected = profile_id.strip()
    if selected:
        profile = cfg.platforms.get(selected)
        if profile is None:
            _exit_wire_error(
                json_output=json_output,
                code="unknown_profile",
                message="Unknown platform profile id.",
                status="invalid-request",
                exit_code=WIRE_EXIT_INVALID_REQUEST,
            )
        profiles = [profile]
    else:
        profiles = list(cfg.platforms.profiles)
    results: list[object] = []
    failed = False
    for profile in profiles:
        try:
            results.append(
                verify_ai_integration(
                    profile.name,
                    config=cfg,
                    verify_transport=verify_transport,
                )
            )
        except Exception as exc:
            failed = True
            results.append(
                {
                    "profile_id": profile.name,
                    "installed": False,
                    "managed": False,
                    "skill_ready": False,
                    "mcp_registered": False,
                    "transport_verified": False,
                    "configured": False,
                    "transport_status": "failed" if verify_transport else "unknown",
                    "skill_managed": False,
                    "mcp_managed": False,
                    "managed_actions_available": [],
                    "tool_count": 0,
                    "tools": [],
                    "problems": [redact_secret_text(str(exc) or exc.__class__.__name__)],
                }
            )
    payload = {"profiles": results}
    if json_output:
        if failed:
            _exit_wire_error(
                json_output=True,
                code="integration_status_incomplete",
                message="One or more integration profiles could not be inspected.",
                status="partial",
                exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
                data=payload,
            )
        _print_wire_success(payload, status="ready")
        return
    _print_ai_integration_status(results)
    if failed:
        raise typer.Exit(WIRE_EXIT_SAFE_NONCOMPLETION)


@integration_app.command("plan-install")
def cmd_integration_plan_install(
    profile_id: str = typer.Option("", "--profile", help="Exact target profile id."),
    overwrite_unmanaged: bool = typer.Option(
        False,
        "--overwrite-unmanaged",
        help="Plan explicit takeover of an unmanaged Skill or MCP entry.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Plan Skill installation and MCP registration for one exact profile."""

    _run_ai_integration_plan_command(
        "install",
        profile_id=profile_id,
        overwrite_unmanaged=overwrite_unmanaged,
        json_output=json_output,
    )


@integration_app.command("plan-uninstall")
def cmd_integration_plan_uninstall(
    profile_id: str = typer.Option("", "--profile", help="Exact target profile id."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Plan removal of only CC Port-owned Skill and MCP integration state."""

    _run_ai_integration_plan_command(
        "uninstall",
        profile_id=profile_id,
        overwrite_unmanaged=False,
        json_output=json_output,
    )


def _run_ai_integration_plan_command(
    action: str,
    *,
    profile_id: str,
    overwrite_unmanaged: bool,
    json_output: bool,
) -> None:
    selected = profile_id.strip()
    if not selected:
        _exit_wire_error(
            json_output=json_output,
            code="profile_required",
            message="An exact --profile id is required.",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    try:
        plan = build_ai_integration_plan(
            selected,
            action=action,  # type: ignore[arg-type]
            overwrite_unmanaged=overwrite_unmanaged,
            config=_load(),
        )
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="integration_plan_failed",
            message=f"AI integration planning failed: {exc}",
        )
    if json_output:
        if plan.blocked:
            _exit_wire_error(
                json_output=True,
                code="plan_blocked",
                message="The AI integration plan is blocked.",
                status="blocked",
                exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
                data=plan,
            )
        _print_wire_success(plan, status="planned")
        return
    _print_ai_integration_plan(plan)


@integration_app.command("apply-install")
def cmd_integration_apply_install(
    operation_id: str = typer.Option("", "--operation-id", help="Id returned by plan-install."),
    plan_hash: str = typer.Option("", "--plan-hash", help="Hash returned by plan-install."),
    approval_id: str = typer.Option(
        "",
        "--approval-id",
        help="Approval id returned by plan-install and approved by a human surface.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Deprecated compatibility flag; it does not approve pending requests.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Apply an installation plan approved through the Desktop client."""

    _ = yes
    _run_ai_integration_apply_command(
        "install",
        operation_id=operation_id,
        plan_hash=plan_hash,
        approval_id=approval_id,
        json_output=json_output,
    )


@integration_app.command("apply-uninstall")
def cmd_integration_apply_uninstall(
    operation_id: str = typer.Option("", "--operation-id", help="Id returned by plan-uninstall."),
    plan_hash: str = typer.Option("", "--plan-hash", help="Hash returned by plan-uninstall."),
    approval_id: str = typer.Option(
        "",
        "--approval-id",
        help="Approval id returned by plan-uninstall and approved by a human surface.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Deprecated compatibility flag; it does not approve pending requests.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Apply an ownership-safe uninstall plan approved through Desktop."""

    _ = yes
    _run_ai_integration_apply_command(
        "uninstall",
        operation_id=operation_id,
        plan_hash=plan_hash,
        approval_id=approval_id,
        json_output=json_output,
    )


def _run_ai_integration_apply_command(
    action: str,
    *,
    operation_id: str,
    plan_hash: str,
    approval_id: str,
    json_output: bool,
) -> None:
    identifiers = {
        "operation_id": operation_id.strip(),
        "plan_hash": plan_hash.strip(),
        "approval_id": approval_id.strip(),
    }
    missing = [name for name, value in identifiers.items() if not value]
    if missing:
        _exit_wire_error(
            json_output=json_output,
            code="integration_apply_identifiers_required",
            message="Apply requires --operation-id, --plan-hash, and --approval-id.",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
            data={"missing": missing},
        )
    try:
        plan = load_ai_integration_plan(identifiers["operation_id"])
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="integration_plan_unavailable",
            message=f"The stored AI integration plan is unavailable: {exc}",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    if plan.action != action:
        _exit_wire_error(
            json_output=json_output,
            code="integration_action_mismatch",
            message=f"This command applies {action} plans, but the stored plan is {plan.action}.",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    if plan.plan_hash != identifiers["plan_hash"]:
        _exit_wire_error(
            json_output=json_output,
            code="integration_plan_hash_mismatch",
            message="The supplied plan hash does not match the stored integration plan.",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    try:
        approval = load_approval_request(identifiers["approval_id"])
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="approval_unavailable",
            message=f"The approval request is unavailable: {exc}",
            status="needs-confirmation",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data=plan,
        )
    if (
        approval.kind != "ai-integration"
        or approval.operation_id != plan.operation_id
        or approval.plan_hash != plan.plan_hash
    ):
        _exit_wire_error(
            json_output=json_output,
            code="approval_mismatch",
            message="The approval request does not match this integration plan.",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    if approval.status == "pending":
        if not json_output:
            _print_ai_integration_plan(plan)
        _exit_wire_error(
            json_output=json_output,
            code="approval_required",
            message=(
                f"Approval request {approval.approval_id} is pending; "
                "review it in the Desktop client before apply."
            ),
            status="needs-confirmation",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data={"plan": plan, "approval": approval},
        )
    elif approval.status != "approved":
        _exit_wire_error(
            json_output=json_output,
            code="approval_not_active",
            message=f"The approval request cannot be used in status {approval.status}.",
            status="needs-action",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data={"plan": plan, "approval": approval},
        )
    try:
        result = apply_ai_integration_plan(
            plan.operation_id,
            plan.plan_hash,
            approval.approval_id,
            config=_load(),
        )
    except ApprovalRequiredError as exc:
        _exit_wire_error(
            json_output=json_output,
            code="approval_required",
            message=str(exc),
            status="needs-confirmation",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data=plan,
        )
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="integration_apply_failed",
            message=f"AI integration apply failed: {exc}",
        )
    if json_output:
        envelope = _print_wire_result(
            result,
            status=result.status,
            message=result.message or "AI integration apply did not complete successfully.",
        )
        if not envelope.ok:
            raise typer.Exit(wire_exit_code(envelope))
        return
    console.print(f"[bold]{result.status}[/bold] {result.profile_id}: {result.message}")
    if result.status not in {"succeeded", "unchanged"}:
        raise typer.Exit(wire_exit_code(wire_result(result, status=result.status)))


@integration_app.command("verify")
def cmd_integration_verify(
    profile_id: str = typer.Option("", "--profile", help="Exact target profile id."),
    expect: str = typer.Option(
        "installed",
        "--expect",
        help="Expected state: installed | absent | any.",
    ),
    verify_transport: bool = typer.Option(
        True,
        "--verify-transport/--no-verify-transport",
        help="Start the configured MCP command and request its tool list.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Verify the observed Skill, MCP registration, and optional stdio transport."""

    selected = profile_id.strip()
    expected = expect.strip().lower()
    if not selected:
        _exit_wire_error(
            json_output=json_output,
            code="profile_required",
            message="An exact --profile id is required.",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    if expected not in {"installed", "absent", "any"}:
        _exit_wire_error(
            json_output=json_output,
            code="invalid_expected_state",
            message="--expect must be installed, absent, or any.",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    try:
        verification = verify_ai_integration(
            selected,
            config=_load(),
            verify_transport=verify_transport,
        )
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="integration_verify_failed",
            message=f"AI integration verification failed: {exc}",
        )
    matches = (
        expected == "any"
        or expected == "installed"
        and verification.installed
        or expected == "absent"
        and not verification.skill_ready
        and not verification.mcp_registered
    )
    if json_output:
        if not matches:
            _exit_wire_error(
                json_output=True,
                code="verification_mismatch",
                message=f"Observed integration state does not match expected state: {expected}.",
                status="needs-action",
                exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
                data=verification,
            )
        _print_wire_success(verification, status="ready")
        return
    _print_ai_integration_status([verification])
    if not matches:
        raise typer.Exit(WIRE_EXIT_SAFE_NONCOMPLETION)


def _print_ai_integration_status(results: list[object]) -> None:
    table = Table(title="CC Port AI integration")
    table.add_column("Profile", style="bold")
    table.add_column("Installed")
    table.add_column("Managed")
    table.add_column("Skill")
    table.add_column("MCP")
    table.add_column("Transport")
    table.add_column("Problems")
    for result in results:
        value = to_wire_value(result)
        if not isinstance(value, dict):
            continue
        table.add_row(
            str(value.get("profile_id") or ""),
            str(bool(value.get("installed"))).lower(),
            str(bool(value.get("managed"))).lower(),
            str(bool(value.get("skill_ready"))).lower(),
            str(bool(value.get("mcp_registered"))).lower(),
            str(bool(value.get("transport_verified"))).lower(),
            "; ".join(str(item) for item in value.get("problems", [])) or "-",
        )
    console.print(table)


def _print_ai_integration_plan(plan: AiIntegrationPlan) -> None:
    table = Table(title=f"CC Port AI integration {plan.action}")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for label, value in (
        ("Operation", plan.operation_id),
        ("Profile", plan.profile_id),
        ("Skill target", plan.target.skill_path),
        ("MCP config", plan.target.mcp_config_path),
        ("Skill status", plan.target.skill_status),
        ("MCP status", plan.target.mcp_status),
        ("Actions", ", ".join(plan.target.actions) or "none"),
        ("Plan hash", plan.plan_hash),
        ("Approval", plan.approval_id or "not required"),
        ("Blocked", str(plan.blocked).lower()),
    ):
        table.add_row(label, str(value))
    console.print(table)
    for blocker in plan.blockers:
        console.print(f"[red]Blocked:[/red] {escape(blocker)}")


def _print_sync_deprecation() -> None:
    console.print(f"[yellow]{DEPRECATED_SYNC_MESSAGE}[/yellow]")


# ---- init ---- #


@app.command("init")
def cmd_init(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing config."),
    claude_code: bool = typer.Option(
        False,
        "--claude-code",
        help="Deprecated compatibility flag; all complete platform presets are enabled.",
    ),
) -> None:
    """Generate the CC Port config file with sensible defaults.

    Usage:
        cc-port init                # generate config (all complete platform presets)

    Then edit ~/.config/cc-port/config.toml to fill in your token and owner.
    Or set the CC_PORT_GITHUB_TOKEN environment variable instead.
    """
    path = default_config_path()
    if path.exists() and not force:
        console.print(f"[yellow]Config already exists at[/yellow] {path}")
        console.print(f"  Use [bold]--force[/bold] to overwrite, or edit it directly: {path}")
        raise typer.Exit(0)

    profiles: list[PlatformProfile] = [
        build_platform(name, {"enabled": True}) for name in PLATFORM_PRESETS
    ]

    cfg = Config(
        github=GithubConfig(),
        install=InstallConfig(),
        platforms=PlatformsConfig(profiles=profiles),
    )
    written = write_config(cfg)
    console.print(f"[green]Config generated at[/green] {written}")
    console.print()
    console.print("Next steps:")
    console.print(
        f"  1. Edit [bold]{written}[/bold] to fill in your [bold]token[/bold] and [bold]owner[/bold]"
    )
    console.print(f'     Or set env var: [bold]$env:{CONFIG_ENV_VAR} = "ghp_xxx"[/bold]')
    console.print(
        "  2. Run [bold]cc-port resource init[/bold] to create/connect your private resource repo"
    )
    console.print("  3. Run [bold]cc-port doctor[/bold] to verify")


# ---- resource repo ---- #


@resource_app.command("init")
def cmd_resource_init(
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Private GitHub resource repo name. Defaults to config or cc-port-resources.",
    ),
) -> None:
    """Create/connect the private resource repo and generate its structure."""
    cfg = _load()
    repo_name = name
    if repo_name is None and not cfg.resources.repo_url:
        _require_interactive_input("Resource repository name is required; pass --name.")
        repo_name = typer.prompt("Resource repository name", default=cfg.resources.repo_name)
    try:
        info = init_resource_repo(name=repo_name, config=cfg)
    except Exception as exc:
        console.print(f"[red]Resource init failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_info(info)


@resource_app.command("use")
def cmd_resource_use(
    target: str = typer.Argument(..., help="Existing local path or Git URL for the resource repo."),
) -> None:
    """Bind CC Port to an existing portable resource repository."""
    try:
        info = use_resource_repo(target, config=_load())
    except Exception as exc:
        console.print(f"[red]Resource use failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_info(info)


@resource_app.command("status")
def cmd_resource_status() -> None:
    """Show resource repository configuration and Git state."""
    _print_resource_info(inspect_resource_repo(_load()))


@resource_app.command("registry-check")
def cmd_resource_registry_check(
    json_output: bool = typer.Option(False, "--json", help="Print the full plan as JSON."),
) -> None:
    """Audit registry.yaml against the current remote commit without writing."""
    try:
        plan = build_registry_repair_plan(config=_load())
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="registry_check_failed",
            message=f"Registry check failed: {exc}",
        )
    if json_output:
        _print_wire_success(plan)
        return
    _print_registry_repair_plan(plan)


@resource_app.command("registry-repair")
def cmd_resource_registry_repair(
    dry_run: bool = typer.Option(False, "--dry-run", help="Build and print the plan only."),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Deprecated compatibility flag; CLI registry repair never applies a plan.",
    ),
    choices_path: Path | None = typer.Option(
        None,
        "--choices",
        help="YAML file containing explicit issue choices.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the plan or result as JSON."),
) -> None:
    """Build a Registry repair plan; apply is restricted to approved interfaces."""
    _ = yes

    try:
        choices = _load_registry_repair_choices(choices_path)
        plan = build_registry_repair_plan(config=_load(), choices=choices)
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="registry_repair_plan_failed",
            message=f"Registry repair planning failed: {exc}",
        )
    if dry_run:
        if json_output:
            _print_wire_success(plan, status="planned")
        else:
            _print_registry_repair_plan(plan)
        return
    if not json_output:
        _print_registry_repair_plan(plan)
    _exit_wire_error(
        json_output=json_output,
        code="registry_repair_apply_unavailable",
        message=(
            "CLI registry repair apply is disabled; review and apply the plan "
            "through Desktop or the approval-gated MCP workflow."
        ),
        status="needs-confirmation",
        exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
        data=plan,
    )


def _load_registry_repair_choices(path: Path | None) -> list[RegistryRepairChoice]:
    if path is None:
        return []
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise typer.BadParameter(f"Cannot parse choices YAML: {exc}") from exc
    if isinstance(payload, dict):
        payload = payload.get("choices", [])
    if not isinstance(payload, list):
        raise typer.BadParameter("Choices YAML must be a list or contain a choices list.")
    choices: list[RegistryRepairChoice] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise typer.BadParameter(f"Choice {index} must be a mapping.")
        issue_id = str(item.get("issue_id") or "").strip()
        action = str(item.get("action") or "").strip()
        if not issue_id or not action:
            raise typer.BadParameter(f"Choice {index} requires issue_id and action.")
        choices.append(
            RegistryRepairChoice(
                issue_id=issue_id,
                action=action,
                name=str(item.get("name") or "").strip(),
            )
        )
    return choices


def _print_registry_repair_plan(plan: RegistryRepairPlan) -> None:
    console.print(
        f"Registry: [bold]{plan.registry_status}[/bold]  "
        f"commit={plan.remote_commit or '-'}  repairable={str(plan.repairable).lower()}"
    )
    table = Table(title="Registry audit issues")
    table.add_column("Issue")
    table.add_column("Resource")
    table.add_column("Path")
    table.add_column("Action")
    table.add_column("Message")
    choice_by_id = {choice.issue_id: choice for choice in plan.choices}
    for issue in plan.issues:
        choice = choice_by_id.get(issue.id)
        table.add_row(
            issue.code,
            issue.resource_key or "-",
            issue.path or "-",
            choice.action if choice else issue.default_action,
            issue.message,
        )
    console.print(table)
    if plan.registry_diff:
        console.print("[bold]registry.yaml diff[/bold]")
        console.print(plan.registry_diff, markup=False)
    console.print(
        f"Executable: {plan.executable_count}; blocked: {plan.blocked_count}; "
        f"plan_hash: {plan.plan_hash}"
    )


@resource_app.command("pull")
def cmd_resource_pull() -> None:
    """Pull the resource repository after checking it is clean."""
    _print_sync_deprecation()
    try:
        info = pull_resource_repo(_load())
    except Exception as exc:
        console.print(f"[red]Resource pull failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_info(info)


@resource_app.command("push")
def cmd_resource_push(
    message: str = typer.Option("cc-port: update resources", "--message", "-m"),
) -> None:
    """Commit local resource changes if needed and push the private repo."""
    _print_sync_deprecation()
    try:
        info = push_resource_repo(message=message, config=_load())
    except Exception as exc:
        console.print(f"[red]Resource push failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_info(info)


@resource_app.command("commit-plan")
def cmd_resource_commit_plan() -> None:
    """Preview resource-level changes and safety blockers before committing."""
    try:
        plan = build_resource_commit_plan(config=_load())
    except Exception as exc:
        console.print(f"[red]Resource commit planning failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    table = Table(title="CC Port resource commit plan")
    table.add_column("Resource")
    table.add_column("Kind")
    table.add_column("Action")
    table.add_column("Paths")
    for item in plan.resources:
        table.add_row(item.name, item.kind, item.action, ", ".join(item.paths))
    console.print(table)
    if plan.blocked_paths or plan.secret_findings:
        blocked = Table(title="Blocked resource changes")
        blocked.add_column("Path")
        blocked.add_column("Reason")
        for item in [*plan.blocked_paths, *plan.secret_findings]:
            blocked.add_row(item.path, item.reason)
        console.print(blocked)
    console.print(f"Suggested message: {plan.suggested_message}")


@resource_app.command("sync-status")
def cmd_resource_sync_status(
    fetch: bool = typer.Option(False, "--fetch", help="Fetch remote refs before reporting."),
) -> None:
    """Show ahead/behind/diverged state without changing the working tree."""
    _print_sync_deprecation()
    try:
        plan = inspect_resource_sync(config=_load(), fetch=fetch)
    except Exception as exc:
        console.print(f"[red]Resource sync status failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_sync_plan(plan)


@resource_app.command("sync-plan")
def cmd_resource_sync_plan() -> None:
    """Fetch and build a safe fast-forward or three-way merge plan."""
    _print_sync_deprecation()
    try:
        plan = build_resource_sync_plan(config=_load())
    except Exception as exc:
        console.print(f"[red]Resource sync planning failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_sync_plan(plan)


@resource_app.command("sync-resolve")
def cmd_resource_sync_resolve(
    operation_id: str = typer.Argument(..., help="Operation id returned by sync-plan."),
    choices: Path = typer.Option(
        ...,
        "--choices",
        exists=True,
        dir_okay=False,
        readable=True,
        help="YAML file mapping conflict ids to local or incoming.",
    ),
) -> None:
    """Resolve a persisted three-way merge plan."""
    _print_sync_deprecation()
    raw = yaml.safe_load(choices.read_text(encoding="utf-8")) or {}
    values = raw.get("items", raw) if isinstance(raw, dict) else {}
    if not isinstance(values, dict):
        console.print("[red]Choices must be a YAML mapping.[/red]")
        raise typer.Exit(2)
    try:
        plan = resolve_resource_sync_plan(
            operation_id,
            {str(key): str(value) for key, value in values.items()},
            config=_load(),
        )
    except Exception as exc:
        console.print(f"[red]Resource sync resolution failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_sync_plan(plan)


@resource_app.command("sync-apply")
def cmd_resource_sync_apply(
    operation_id: str = typer.Argument(..., help="Operation id returned by sync-plan."),
) -> None:
    """Apply a ready sync plan to the resource repository."""
    _print_sync_deprecation()
    try:
        plan = apply_resource_sync_plan(operation_id, config=_load())
    except Exception as exc:
        console.print(f"[red]Resource sync apply failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_sync_plan(plan)


@resource_app.command("sync-cancel")
def cmd_resource_sync_cancel(
    operation_id: str = typer.Argument(..., help="Operation id returned by sync-plan."),
) -> None:
    """Cancel a pending sync plan and remove its temporary worktree."""
    _print_sync_deprecation()
    try:
        plan = cancel_resource_sync_plan(operation_id, config=_load())
    except Exception as exc:
        console.print(f"[red]Resource sync cancellation failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_sync_plan(plan)


@resource_app.command("sync-stale")
def cmd_resource_sync_stale(
    min_age_hours: float = typer.Option(
        24,
        "--min-age-hours",
        min=0,
        help="Only show pending worktrees at least this old.",
    ),
) -> None:
    """List abandoned-looking merge worktrees without modifying them."""
    _print_sync_deprecation()
    plans = list_stale_resource_sync_plans(min_age_hours=min_age_hours)
    table = Table(title="Stale resource sync worktrees")
    table.add_column("Operation")
    table.add_column("Status")
    table.add_column("Age hours")
    table.add_column("Worktree")
    for plan in plans:
        table.add_row(
            plan.operation_id,
            plan.status,
            str(plan.age_hours),
            str(plan.worktree_path),
        )
    console.print(table)


@resource_app.command("sync-cleanup")
def cmd_resource_sync_cleanup(
    operation_id: str = typer.Argument(...),
    force: bool = typer.Option(
        False,
        "--force",
        help="Abandon a newer pending plan instead of requiring stale age.",
    ),
) -> None:
    """Explicitly abandon a pending sync plan and remove its worktree."""
    _print_sync_deprecation()
    try:
        plan = cleanup_stale_resource_sync_plan(
            operation_id,
            force=force,
            config=_load(),
        )
    except Exception as exc:
        console.print(f"[red]Resource sync cleanup failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_sync_plan(plan)


# ---- asset-level sync ---- #


@plugin_project_app.command("list")
def cmd_plugin_project_list(
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """List the explicit project roots available to plugin scans."""
    projects = list_plugin_projects(_load())
    if json_output:
        _print_machine_json([asdict(item) for item in projects])
        return
    table = Table(title="CC Port plugin projects")
    table.add_column("ID", style="bold")
    table.add_column("Path")
    table.add_column("Git identity")
    table.add_column("Mode")
    for item in projects:
        table.add_row(
            item.id,
            str(item.path),
            f"{item.repo}{('/' + item.subdir) if item.subdir else ''}" or "-",
            "portable" if item.portable else "observe-only",
        )
    console.print(table)


@plugin_project_app.command("add")
def cmd_plugin_project_add(
    path: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Add one explicit project root; no home-directory recursion is performed."""
    project = add_plugin_project(path)
    if json_output:
        _print_machine_json(asdict(project))
    else:
        console.print(
            f"[bold]{project.id}[/bold] {project.path} "
            f"({'portable' if project.portable else 'observe-only: no Git remote'})"
        )


@plugin_project_app.command("remove")
def cmd_plugin_project_remove(
    project_id: str = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Remove a project scan mapping without touching the project directory."""
    project = remove_plugin_project(project_id)
    if json_output:
        _print_machine_json(asdict(project))
    else:
        console.print(f"Removed project mapping [bold]{project.id}[/bold]; files were not changed.")


@plugin_reference_app.command("add")
def cmd_plugin_reference_add(
    platform: str = typer.Option(..., "--platform", help="codex | claude-code | opencode"),
    plugin_id: str = typer.Option(..., "--plugin-id"),
    origin_type: str = typer.Option(..., "--origin", help="marketplace | npm | git"),
    scope: str = typer.Option("user", "--scope", help="user | project | local | managed"),
    marketplace: str = typer.Option("", "--marketplace"),
    source: str = typer.Option("", "--source"),
    package: str = typer.Option("", "--package"),
    repo: str = typer.Option("", "--repo"),
    selector: str = typer.Option("", "--selector"),
    observed_version: str = typer.Option("", "--observed-version"),
    project_id: str = typer.Option("", "--project"),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
    name: str = typer.Option("", "--name"),
    description: str = typer.Option("", "--description"),
    push: bool = typer.Option(True, "--push/--no-push"),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Store desired plugin source/state without uploading installed cache content."""
    try:
        result = add_plugin_reference(
            platform=platform,
            plugin_id=plugin_id,
            origin_type=origin_type,
            scope=scope,
            enabled=enabled,
            marketplace=marketplace,
            source=source,
            package=package,
            repo=repo,
            selector=selector,
            observed_version=observed_version,
            project_id=project_id,
            name=name,
            description=description,
            push=push,
            config=_load(),
        )
    except Exception as exc:
        console.print(f"[red]Plugin reference add failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    if json_output:
        _print_machine_json(asdict(result))
    else:
        console.print(f"[bold]{result.status}[/bold] {result.resource_key}")


@plugin_app.command("delete")
def cmd_plugin_delete(
    resource_key: str = typer.Argument(..., help="Composite plugin resource key."),
    instance: list[str] = typer.Option([], "--instance", help="Instance id; repeatable."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the uninstall plan only."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm selected instance removal."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Uninstall selected instances before marking their remote desired state removed."""
    try:
        plan = build_plugin_delete_plan(
            resource_key,
            selected_instance_ids=instance or None,
            config=_load(),
        )
    except Exception as exc:
        console.print(f"[red]Plugin delete planning failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    if json_output and dry_run:
        _print_machine_json(asdict(plan))
        return
    if not json_output:
        table = Table(title=f"Plugin delete {resource_key}")
        table.add_column("Instance", style="bold")
        table.add_column("Scope")
        table.add_column("Method")
        table.add_column("Selectable")
        table.add_column("Detail")
        for item in plan.instances:
            table.add_row(
                item.id, item.scope, item.method, str(item.selectable).lower(), item.detail
            )
        console.print(table)
    if dry_run:
        return
    if plan.blocked:
        console.print("[red]" + "; ".join(plan.blockers) + "[/red]")
        raise typer.Exit(1)
    if not yes:
        _require_interactive_input(
            "Plugin deletion requires confirmation; pass --yes.",
            json_output=json_output,
            code="confirmation_required",
        )
    if not yes and not typer.confirm(
        f"Uninstall {len(plan.selected_instance_ids)} plugin instance(s)?",
        default=False,
    ):
        console.print("[yellow]Plugin delete cancelled.[/yellow]")
        return
    result = apply_plugin_delete_plan(
        resource_key,
        selected_instance_ids=plan.selected_instance_ids,
        expected_plan_hash=plan.plan_hash,
        config=_load(),
    )
    if json_output:
        _print_machine_json(asdict(result))
    else:
        console.print(f"[bold]{result.status}[/bold] {resource_key}")
        for item in result.results:
            console.print(f"{item.status}: {item.message}")
    if result.status != "succeeded":
        raise typer.Exit(1)


@asset_app.command("list")
def cmd_asset_list(
    scan_local: bool = typer.Option(
        False,
        "--scan-local",
        help="Scan configured and detected platforms for unregistered assets and extra instances.",
    ),
    refresh_remote: bool = typer.Option(
        True,
        "--refresh-remote/--cached-remote",
        help="Fetch the configured branch before building the inventory.",
    ),
    scan_global: bool = typer.Option(
        True,
        "--global/--no-global",
        help="Include or exclude global plugin locations when --scan-local is used.",
    ),
    project: list[str] = typer.Option(
        [],
        "--project",
        help="Saved plugin project id. Repeatable; an empty list scans every saved project.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """List one logical resource row with nested local tool instances."""
    try:
        inventory = build_asset_inventory(
            config=_load(),
            scan_local=scan_local,
            refresh_remote=refresh_remote,
            scan_global=scan_global,
            project_ids=project or None,
        )
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_inventory_failed",
            message=f"Asset inventory failed: {exc}",
        )
    if json_output:
        payload = asdict(inventory)
        payload.pop("rows", None)
        _print_wire_success(payload)
        return

    table = Table(title=f"CC Port assets ({inventory.branch or 'unconfigured branch'})")
    table.add_column("Resource", style="bold")
    table.add_column("Description")
    table.add_column("Local")
    table.add_column("Remote")
    table.add_column("Status")
    table.add_column("Actions")
    for row in inventory.resources:
        table.add_row(
            row.resource_key,
            row.description or "-",
            row.local_status,
            row.remote_status,
            row.status,
            ", ".join(row.available_actions) or "-",
        )
    console.print(table)
    if inventory.remote_warning:
        console.print(f"[yellow]{inventory.remote_warning}[/yellow]")
    if inventory.legacy_write_blocker:
        console.print(f"[red]Remote writes blocked:[/red] {inventory.legacy_write_blocker}")


@asset_app.command("reconcile")
def cmd_asset_reconcile(
    context_schema_version: str = typer.Option(
        "1",
        "--context-schema-version",
        help="Structured reconciliation context schema version.",
    ),
    cursor: str = typer.Option("", "--cursor", help="Opaque continuation cursor."),
    page_size: str = typer.Option(
        "100",
        "--page-size",
        help="Resources per page (1-200).",
    ),
    include_same: bool = typer.Option(
        False,
        "--include-same",
        help="Include resources whose local and remote content already match.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Build fresh read-only local/remote context for an advisor agent."""

    try:
        if (
            not context_schema_version
            or len(context_schema_version) > 10
            or not context_schema_version.isascii()
            or not context_schema_version.isdecimal()
        ):
            raise AssetReconcileInvalidRequest(
                "context_schema_version must be an ASCII decimal integer."
            )
        if (
            not page_size
            or len(page_size) > 10
            or not page_size.isascii()
            or not page_size.isdecimal()
        ):
            raise AssetReconcileInvalidRequest("page_size must be an ASCII decimal integer.")
        context = build_asset_reconcile_context(
            config=_load(),
            context_schema_version=int(context_schema_version),
            cursor=cursor,
            page_size=int(page_size),
            include_same=include_same,
        )
    except AssetReconcileInvalidRequest as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_reconcile_context_invalid",
            message=str(exc),
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    except AssetReconcileStaleContext as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_reconcile_context_stale",
            message=str(exc),
            status="stale-context",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
        )
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_reconcile_context_failed",
            message=f"Asset reconciliation failed: {exc}",
        )

    if json_output:
        _print_wire_success(context, status="ready")
        return

    table = Table(title="CC Port asset reconciliation")
    table.add_column("Resource", style="bold")
    table.add_column("Status")
    table.add_column("Comparisons")
    table.add_column("Blocked")
    for resource in context.resources:
        table.add_row(
            resource.resource_key,
            resource.resource_status,
            str(len(resource.comparisons)),
            "yes"
            if any(
                check.state == "blocked"
                for comparison in resource.comparisons
                for check in comparison.action_checks
            )
            else "no",
        )
    console.print(table)
    console.print(
        f"Context {context.context_id[:12]}: "
        f"{context.page.returned}/{context.page.total} resources, "
        f"completeness={context.completeness}"
    )
    if context.page.has_more:
        console.print("More resources are available; rerun with --cursor and the returned token.")


@asset_app.command("diff")
def cmd_asset_diff(
    resource_key: str = typer.Option(..., "--resource", "-r", help="Logical resource key."),
    local_instance_id: str = typer.Option(
        ...,
        "--local-instance-id",
        help="Exact local instance id returned by asset list --scan-local.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Build a bounded, read-only diff between one local instance and the remote asset."""

    try:
        content_diff = build_asset_content_diff(
            resource_key,
            local_instance_id,
            config=_load(),
            enabled_profiles_only=_NON_INTERACTIVE,
        )
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_diff_failed",
            message=f"Asset diff failed: {exc}",
        )
    if json_output:
        _print_wire_success(content_diff)
        return
    summary = Table(title=f"CC Port asset diff {content_diff.resource_key}")
    summary.add_column("Added")
    summary.add_column("Deleted")
    summary.add_column("Modified")
    summary.add_column("Binary")
    summary.add_column("Truncated")
    summary.add_row(
        str(content_diff.added_files),
        str(content_diff.deleted_files),
        str(content_diff.modified_files),
        str(content_diff.binary_files),
        str(content_diff.truncated).lower(),
    )
    console.print(summary)
    for item in content_diff.files:
        console.print(f"[bold]{escape(item.status)}[/bold] {escape(item.path)}")
        if item.diff:
            console.print(item.diff, markup=False)


@asset_app.command("plan")
def cmd_asset_plan(
    action: str = typer.Argument(
        "",
        help="download | upload | copy-to-local | copy-to-remote | set-platform-install-name",
    ),
    kind: str = typer.Option("", "--kind", "-k", help="Asset kind."),
    name: str = typer.Option("", "--name", "-n", help="Asset name."),
    platform: str = typer.Option("", "--platform", "-p", help="Platform id."),
    local_instance_id: str = typer.Option(
        "",
        "--local-instance-id",
        help="Required when a platform has multiple local instances.",
    ),
    new_name: str = typer.Option(
        "",
        "--new-name",
        help="New asset name for copy-to-local or copy-to-remote.",
    ),
    new_install_name: str = typer.Option(
        "",
        "--new-install-name",
        help="Platform install alias for set-platform-install-name.",
    ),
    overwrite_unmanaged: bool = typer.Option(
        False,
        "--overwrite-unmanaged",
        help="Explicitly allow replacing an unmanaged local target.",
    ),
    link_target_confirmed: bool = typer.Option(
        False,
        "--link-target-confirmed",
        help="Explicitly confirm an untrusted non-standard root link target.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Persist one revalidatable asset action plan."""
    normalized = {
        "action": action.strip(),
        "kind": kind.strip(),
        "name": name.strip(),
        "platform": platform.strip(),
    }
    missing = [field for field, value in normalized.items() if not value]
    if missing:
        _exit_wire_error(
            json_output=json_output,
            code="asset_plan_inputs_required",
            message="Asset plan requires action, --kind, --name, and --platform.",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
            data={"missing": missing},
        )
    if normalized["kind"] not in VALID_KINDS:
        _exit_wire_error(
            json_output=json_output,
            code="invalid_resource_kind",
            message=f"Unsupported resource kind: {normalized['kind']}",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    try:
        plan = build_asset_action_plan(
            normalized["action"],
            kind=normalized["kind"],  # type: ignore[arg-type]
            name=normalized["name"],
            platform=normalized["platform"],
            local_instance_id=local_instance_id,
            new_name=new_name,
            new_install_name=new_install_name,
            overwrite_unmanaged=overwrite_unmanaged,
            link_target_confirmed=link_target_confirmed,
            config=_load(),
        )
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_plan_failed",
            message=f"Asset planning failed: {exc}",
        )
    try:
        plan_hash, approval = _create_asset_action_approval(plan)
        payload = _asset_action_plan_payload(
            plan,
            plan_hash=plan_hash,
            approval=approval,
        )
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_approval_request_failed",
            message=f"Asset approval request failed: {exc}",
        )
    if json_output:
        if plan.blocked:
            _exit_wire_error(
                json_output=True,
                code="plan_blocked",
                message="The asset action plan is blocked.",
                status="blocked",
                exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
                data=payload,
            )
        _print_wire_success(payload, status="planned")
        return
    _print_asset_action_plan(
        plan,
        plan_hash=plan_hash,
        approval_id=approval.approval_id if approval else "",
        approval_status=approval.status if approval else "not-required",
    )


@asset_app.command("apply")
def cmd_asset_apply(
    operation_id: str = typer.Argument("", help="Operation id returned by asset plan."),
    approval_id: str = typer.Option(
        "",
        "--approval-id",
        help="Approval id returned by asset plan and approved by a human surface.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Deprecated compatibility flag; it does not approve pending requests.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Revalidate and apply one persisted asset action plan."""
    _ = yes
    selected_operation_id = operation_id.strip()
    if not selected_operation_id:
        _exit_wire_error(
            json_output=json_output,
            code="operation_id_required",
            message="Asset apply requires an operation id returned by asset plan.",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
            data={"missing": ["operation_id"]},
        )
    selected_approval_id = approval_id.strip()
    if not selected_approval_id:
        _exit_wire_error(
            json_output=json_output,
            code="approval_id_required",
            message="Asset apply requires --approval-id from the reviewed plan.",
            status="needs-confirmation",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data={"missing": ["approval_id"]},
        )

    try:
        cfg = _load()
        plan = load_asset_action_plan(selected_operation_id, config=cfg)
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_plan_unavailable",
            message=f"The stored asset action plan is unavailable: {exc}",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    plan_hash = asset_action_plan_hash(plan)
    plan_payload = _asset_action_plan_payload(plan, plan_hash=plan_hash, approval=None)
    if plan.blocked:
        _exit_wire_error(
            json_output=json_output,
            code="plan_blocked",
            message="The stored asset action plan is blocked.",
            status="blocked",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data=plan_payload,
        )
    try:
        current = _rebuild_asset_action_plan(plan, config=cfg, persist=False)
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_revalidation_failed",
            message=f"Asset action revalidation failed: {exc}",
        )
    if asset_action_plan_hash(current) != plan_hash:
        invalidated = _invalidate_cli_approval(
            selected_approval_id,
            kind="asset-action",
            operation_id=plan.operation_id,
            plan_hash=plan_hash,
            scope=asset_action_approval_scope(plan),
            json_output=json_output,
            data=plan_payload,
        )
        try:
            replacement = _rebuild_asset_action_plan(plan, config=cfg, persist=True)
            replacement_hash, replacement_approval = _create_asset_action_approval(replacement)
            stale_plan_payload = _asset_action_plan_payload(
                replacement,
                plan_hash=replacement_hash,
                approval=replacement_approval,
            )
        except Exception as exc:
            stale_plan_payload = None
            replan_error = redact_secret_text(str(exc))
        else:
            replan_error = ""
        if not json_output and stale_plan_payload is not None:
            _print_asset_action_plan(
                replacement,
                plan_hash=replacement_hash,
                approval_id=(replacement_approval.approval_id if replacement_approval else ""),
                approval_status=(
                    replacement_approval.status if replacement_approval else "not-required"
                ),
            )
        _exit_wire_error(
            json_output=json_output,
            code="stale_plan",
            message="The asset action plan is stale; review the returned replacement plan.",
            status="stale-plan",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data={
                "operation_id": plan.operation_id,
                "plan_hash": plan_hash,
                "approval_id": invalidated.approval_id,
                "approval_status": invalidated.status,
                "action": plan.action,
                "status": "stale-plan",
                "resource_key": plan.resource_key,
                "target_resource_key": plan.target_resource_key,
                "platform": plan.platform,
                "message": "The action state changed before approval consumption.",
                "stale_plan": stale_plan_payload,
                "replan_error": replan_error,
            },
        )
    if not _NON_INTERACTIVE and not json_output:
        _print_asset_action_plan(
            plan,
            plan_hash=plan_hash,
            approval_id=selected_approval_id,
            approval_status="pending or approved",
        )
    approval = _consume_cli_approval(
        selected_approval_id,
        kind="asset-action",
        operation_id=plan.operation_id,
        plan_hash=plan_hash,
        scope=asset_action_approval_scope(current),
        summary=f"{plan.action} {plan.resource_key} for profile {plan.platform}",
        metadata=_asset_action_approval_metadata(plan),
        json_output=json_output,
        data=plan_payload,
    )
    if approval is None:
        return
    try:
        result = apply_asset_action_plan(selected_operation_id, config=cfg)
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_apply_failed",
            message=f"Asset apply failed: {exc}",
        )
    result_payload = to_wire_value(result)
    if not isinstance(result_payload, dict):
        raise TypeError("Asset action result must be an object.")
    result_payload.update(
        {
            "plan_hash": plan_hash,
            "approval_id": approval.approval_id,
            "approval_status": approval.status,
        }
    )
    if result.status.startswith("stale-"):
        try:
            replacement = _rebuild_asset_action_plan(plan, config=cfg, persist=True)
            replacement_hash, replacement_approval = _create_asset_action_approval(replacement)
            result_payload["stale_plan"] = _asset_action_plan_payload(
                replacement,
                plan_hash=replacement_hash,
                approval=replacement_approval,
            )
            if not json_output:
                _print_asset_action_plan(
                    replacement,
                    plan_hash=replacement_hash,
                    approval_id=(replacement_approval.approval_id if replacement_approval else ""),
                    approval_status=(
                        replacement_approval.status if replacement_approval else "not-required"
                    ),
                )
        except Exception as exc:
            result_payload["stale_plan"] = None
            result_payload["replan_error"] = redact_secret_text(str(exc))
        _exit_wire_error(
            json_output=json_output,
            code="stale_plan",
            message="The asset action plan became stale; review the returned replacement plan.",
            status="stale-plan",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data=result_payload,
        )
    if json_output:
        envelope = _print_wire_result(
            result_payload,
            status=result.status,
            message=result.message or "Asset apply did not complete successfully.",
        )
        if not envelope.ok:
            raise typer.Exit(wire_exit_code(envelope))
    else:
        console.print(
            f"[bold]{result.status}[/bold] {result.target_resource_key} "
            f"on {result.platform}: {result.message}"
        )
        for warning in result.warnings:
            console.print(f"[yellow]Warning:[/yellow] {warning}")
    if not json_output and result.status not in {"succeeded", "unchanged"}:
        raise typer.Exit(1)


@asset_app.command("upload")
def cmd_asset_upload(
    resource: list[str] = typer.Option(
        [], "--resource", "-r", help="Logical resource key. Repeatable."
    ),
    all_resources: bool = typer.Option(
        False, "--all", help="Upload every scanned logical resource."
    ),
    choices: Path | None = typer.Option(None, "--choices", help="YAML batch choices file."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the current plan without writing."
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Deprecated compatibility flag; it does not approve pending requests.",
    ),
    approval_id: str = typer.Option(
        "",
        "--approval-id",
        help="Approval id returned by an earlier plan and approved by a human surface.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Upload selected local resources in one remote commit."""
    _run_asset_batch_command(
        "upload",
        resource_keys=resource,
        all_resources=all_resources,
        platforms=[],
        choices_path=choices,
        dry_run=dry_run,
        yes=yes,
        approval_id=approval_id,
        json_output=json_output,
    )


@asset_app.command("download")
def cmd_asset_download(
    resource: list[str] = typer.Option(
        [], "--resource", "-r", help="Logical resource key. Repeatable."
    ),
    all_resources: bool = typer.Option(
        False, "--all", help="Download every remote logical resource."
    ),
    platform: list[str] = typer.Option(
        [], "--platform", "-p", help="Enabled target AI tool. Repeatable."
    ),
    choices: Path | None = typer.Option(None, "--choices", help="YAML batch choices file."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the current plan without writing."
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Deprecated compatibility flag; it does not approve pending requests.",
    ),
    approval_id: str = typer.Option(
        "",
        "--approval-id",
        help="Approval id returned by an earlier plan and approved by a human surface.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Download selected remote resources to one or more enabled AI tools."""
    if not platform:
        _exit_wire_error(
            json_output=json_output,
            code="target_platform_required",
            message="Select at least one target with --platform.",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    _run_asset_batch_command(
        "download",
        resource_keys=resource,
        all_resources=all_resources,
        platforms=platform,
        choices_path=choices,
        dry_run=dry_run,
        yes=yes,
        approval_id=approval_id,
        json_output=json_output,
    )


@asset_app.command("batch-plan")
def cmd_asset_batch_plan(
    request_source: str = typer.Option(
        "",
        "--request",
        help="JSON request file, or '-' to read the request from stdin.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Build a stateless batch plan from one strict reusable JSON request."""
    selected_request = request_source.strip()
    if not selected_request:
        _exit_wire_error(
            json_output=json_output,
            code="batch_request_required",
            message="Asset batch plan requires --request with a JSON file or '-'.",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
            data={"missing": ["request"]},
        )

    try:
        request = _load_asset_batch_request(selected_request)
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="invalid_batch_request",
            message=str(exc),
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    try:
        plan = build_asset_batch_plan(
            request.direction,
            resource_keys=request.resource_keys,
            target_platforms=request.target_platforms,
            choices=_service_asset_batch_choices(request.choices),
            config=_load(),
        )
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_batch_plan_failed",
            message=f"Asset batch planning failed: {exc}",
        )
    try:
        approval = _create_asset_batch_approval(plan, request)
        payload = _asset_batch_plan_payload(plan, approval=approval)
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_approval_request_failed",
            message=f"Asset batch approval request failed: {exc}",
        )
    if json_output:
        if plan.blocked_count:
            _exit_wire_error(
                json_output=True,
                code="plan_blocked",
                message="The asset batch plan contains blocked items.",
                status="blocked",
                exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
                data=payload,
            )
        _print_wire_success(payload, status="planned")
        return
    _print_asset_batch_plan(plan, request=request)
    if approval:
        console.print(f"Approval: {approval.approval_id} ({approval.status})")


@asset_app.command("batch-apply")
def cmd_asset_batch_apply(
    request_source: str = typer.Option(
        "",
        "--request",
        help="The exact JSON request file used for batch-plan, or '-' for stdin.",
    ),
    plan_hash: str = typer.Option(
        "",
        "--plan-hash",
        help="The exact plan_hash returned by batch-plan.",
    ),
    approval_id: str = typer.Option(
        "",
        "--approval-id",
        help="Approval id returned by batch-plan and approved by a human surface.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Deprecated compatibility flag; it does not approve pending requests.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Apply a batch only when the same request still produces the reviewed hash."""
    _ = yes
    selected_request = request_source.strip()
    selected_plan_hash = plan_hash.strip()
    selected_approval_id = approval_id.strip()
    missing = [
        field
        for field, value in (
            ("request", selected_request),
            ("plan_hash", selected_plan_hash),
            ("approval_id", selected_approval_id),
        )
        if not value
    ]
    if missing:
        _exit_wire_error(
            json_output=json_output,
            code="batch_apply_inputs_required",
            message="Asset batch apply requires --request, --plan-hash, and --approval-id.",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
            data={"missing": missing},
        )
    try:
        request = _load_asset_batch_request(selected_request)
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="invalid_batch_request",
            message=str(exc),
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    try:
        cfg = _load()
        current = build_asset_batch_plan(
            request.direction,
            resource_keys=request.resource_keys,
            target_platforms=request.target_platforms,
            choices=_service_asset_batch_choices(request.choices),
            config=cfg,
        )
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_batch_plan_failed",
            message=f"Asset batch revalidation failed: {exc}",
        )
    if current.plan_hash != selected_plan_hash:
        old_operation_id = asset_batch_operation_id(selected_plan_hash)
        invalidated = _invalidate_cli_approval(
            selected_approval_id,
            kind="asset-batch",
            operation_id=old_operation_id,
            plan_hash=selected_plan_hash,
            scope=asset_batch_approval_scope(
                direction=request.direction,
                resource_keys=request.resource_keys,
                target_platforms=request.target_platforms,
                choices=request.choices,
                plan_hash=selected_plan_hash,
            ),
            json_output=json_output,
            data={"plan_hash": selected_plan_hash},
        )
        replacement_approval = _create_asset_batch_approval(current, request)
        stale_payload = {
            "operation_id": old_operation_id,
            "approval_id": invalidated.approval_id,
            "approval_status": invalidated.status,
            "status": "stale-plan",
            "plan_hash": current.plan_hash,
            "results": [],
            "stale_plan": _asset_batch_plan_payload(
                current,
                approval=replacement_approval,
            ),
        }
        if not json_output:
            _print_asset_batch_plan(current, request=request)
            if replacement_approval:
                console.print(
                    f"Approval: {replacement_approval.approval_id} ({replacement_approval.status})"
                )
        _exit_wire_error(
            json_output=json_output,
            code="stale_plan",
            message="The asset batch plan is stale; review the returned replacement plan.",
            status="stale-plan",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data=stale_payload,
        )
    current_payload = _asset_batch_plan_payload(current, approval=None)
    if current.blocked_count:
        _exit_wire_error(
            json_output=json_output,
            code="plan_blocked",
            message="The current asset batch plan contains blocked items.",
            status="blocked",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data=current_payload,
        )
    if not _NON_INTERACTIVE and not json_output:
        _print_asset_batch_plan(current, request=request)
        console.print(f"Approval: {approval_id.strip() or '(create or select during apply)'}")
    consumed: ApprovalRequest | None = None
    if current.executable_count:
        consumed = _consume_cli_approval(
            selected_approval_id,
            kind="asset-batch",
            operation_id=asset_batch_operation_id(current.plan_hash),
            plan_hash=current.plan_hash,
            scope=asset_batch_approval_scope(
                direction=request.direction,
                resource_keys=request.resource_keys,
                target_platforms=request.target_platforms,
                choices=request.choices,
                plan_hash=current.plan_hash,
            ),
            summary=(
                f"{request.direction.title()} {len(request.resource_keys)} "
                "CC Port asset resource(s)"
            ),
            metadata=_asset_batch_approval_metadata(request),
            json_output=json_output,
            data=current_payload,
        )
        if consumed is None:
            return
    try:
        result = apply_asset_batch_plan(
            request.direction,
            resource_keys=request.resource_keys,
            target_platforms=request.target_platforms,
            choices=_service_asset_batch_choices(request.choices),
            expected_plan_hash=selected_plan_hash,
            config=cfg,
        )
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_batch_apply_failed",
            message=f"Asset batch apply failed: {exc}",
        )
    result_payload = to_wire_value(result)
    if not isinstance(result_payload, dict):
        raise TypeError("Asset batch result must be an object.")
    result_payload.update(
        {
            "operation_id": asset_batch_operation_id(current.plan_hash),
            "approval_id": consumed.approval_id if consumed else "",
            "approval_status": consumed.status if consumed else "not-required",
        }
    )
    if result.status == "stale-plan" and result.stale_plan is not None:
        replacement_approval = _create_asset_batch_approval(result.stale_plan, request)
        result_payload["stale_plan"] = _asset_batch_plan_payload(
            result.stale_plan,
            approval=replacement_approval,
        )
        if not json_output:
            _print_asset_batch_plan(result.stale_plan, request=request)
            if replacement_approval:
                console.print(
                    f"Approval: {replacement_approval.approval_id} ({replacement_approval.status})"
                )
    if json_output:
        envelope = _print_wire_result(
            result_payload,
            status=result.status,
            message=_asset_batch_result_message(result.status),
        )
        if not envelope.ok:
            raise typer.Exit(wire_exit_code(envelope))
        return
    console.print(f"[bold]{result.status}[/bold]")
    for item in result.results:
        console.print(
            f"{item.status}: {item.target_resource_key}"
            f"{f' on {item.platform}' if item.platform else ''} - {item.message}"
        )
    if result.status != "succeeded":
        raise typer.Exit(
            WIRE_EXIT_SAFE_NONCOMPLETION
            if result.status in {"stale-plan", "partial", "needs-action"}
            else WIRE_EXIT_RUNTIME_FAILURE
        )


def _run_asset_batch_command(
    direction: str,
    *,
    resource_keys: list[str],
    all_resources: bool,
    platforms: list[str],
    choices_path: Path | None,
    dry_run: bool,
    yes: bool,
    approval_id: str,
    json_output: bool,
) -> None:
    _ = yes

    try:
        cfg = _load()
        keys = list(dict.fromkeys(item.strip() for item in resource_keys if item.strip()))
        if all_resources:
            inventory = build_asset_inventory(config=cfg, scan_local=True, refresh_remote=True)
            keys = [
                item.resource_key
                for item in inventory.resources
                if direction == "upload" or item.remote.exists
            ]
        batch_choices = _load_asset_batch_choices(choices_path)
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="invalid_batch_request",
            message=f"Invalid asset batch request: {exc}",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    if not keys:
        _exit_wire_error(
            json_output=json_output,
            code="resource_selection_required",
            message="Select at least one resource with --resource or --all.",
            status="invalid-request",
            exit_code=WIRE_EXIT_INVALID_REQUEST,
        )
    try:
        plan = build_asset_batch_plan(
            direction,
            resource_keys=keys,
            target_platforms=platforms,
            choices=batch_choices,
            config=cfg,
        )
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_batch_plan_failed",
            message=f"Asset batch planning failed: {exc}",
        )
    request = AssetBatchRequestWire(
        direction=direction,  # type: ignore[arg-type]
        resource_keys=keys,
        target_platforms=platforms,
        choices=_wire_asset_batch_choices(batch_choices),
    )
    selected_approval_id = approval_id.strip()
    if selected_approval_id:
        try:
            reviewed_approval = load_approval_request(selected_approval_id)
        except Exception as exc:
            _exit_wire_error(
                json_output=json_output,
                code="approval_unavailable",
                message=f"The approval request is unavailable: {exc}",
                status="needs-confirmation",
                exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            )
        if reviewed_approval.kind != "asset-batch":
            _exit_wire_error(
                json_output=json_output,
                code="approval_mismatch",
                message="The approval request does not match an asset batch operation.",
                status="invalid-request",
                exit_code=WIRE_EXIT_INVALID_REQUEST,
            )
        if reviewed_approval.plan_hash != plan.plan_hash:
            invalidated = _invalidate_cli_approval(
                selected_approval_id,
                kind="asset-batch",
                operation_id=asset_batch_operation_id(reviewed_approval.plan_hash),
                plan_hash=reviewed_approval.plan_hash,
                scope=asset_batch_approval_scope(
                    direction=request.direction,
                    resource_keys=request.resource_keys,
                    target_platforms=request.target_platforms,
                    choices=request.choices,
                    plan_hash=reviewed_approval.plan_hash,
                ),
                json_output=json_output,
                data={"plan_hash": reviewed_approval.plan_hash},
            )
            replacement_approval = _create_asset_batch_approval(plan, request)
            stale_payload = {
                "operation_id": reviewed_approval.operation_id,
                "approval_id": invalidated.approval_id,
                "approval_status": invalidated.status,
                "status": "stale-plan",
                "plan_hash": plan.plan_hash,
                "results": [],
                "stale_plan": _asset_batch_plan_payload(
                    plan,
                    approval=replacement_approval,
                ),
            }
            if not json_output:
                _print_asset_batch_plan(plan, request=request)
                if replacement_approval:
                    console.print(
                        f"Approval: {replacement_approval.approval_id} "
                        f"({replacement_approval.status})"
                    )
            _exit_wire_error(
                json_output=json_output,
                code="stale_plan",
                message="The asset batch plan is stale; review the returned replacement plan.",
                status="stale-plan",
                exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
                data=stale_payload,
            )
    try:
        planned_approval = (
            _create_asset_batch_approval(plan, request) if not selected_approval_id else None
        )
        plan_payload = _asset_batch_plan_payload(plan, approval=planned_approval)
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_approval_request_failed",
            message=f"Asset batch approval request failed: {exc}",
        )
    if json_output and dry_run:
        if plan.blocked_count:
            _exit_wire_error(
                json_output=True,
                code="plan_blocked",
                message="The asset batch plan contains blocked items.",
                status="blocked",
                exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
                data=plan_payload,
            )
        _print_wire_success(plan_payload, status="planned")
        return
    if not json_output:
        _print_asset_batch_plan(plan, request=request)
        if planned_approval:
            console.print(f"Approval: {planned_approval.approval_id} ({planned_approval.status})")
    if dry_run:
        return
    has_manual = any(item.disposition == "manual" for item in plan.items)
    if plan.executable_count == 0 and not has_manual:
        _exit_wire_error(
            json_output=json_output,
            code="no_executable_items",
            message="The plan has no executable items.",
            status="blocked",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data=plan_payload,
        )
    if plan.blocked_count:
        _exit_wire_error(
            json_output=json_output,
            code="plan_blocked",
            message="Resolve or remove blocked items before applying.",
            status="blocked",
            exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
            data=plan_payload,
        )
    consumed: ApprovalRequest | None = None
    if plan.executable_count:
        consumed = _consume_cli_approval(
            selected_approval_id,
            kind="asset-batch",
            operation_id=asset_batch_operation_id(plan.plan_hash),
            plan_hash=plan.plan_hash,
            scope=asset_batch_approval_scope(
                direction=request.direction,
                resource_keys=request.resource_keys,
                target_platforms=request.target_platforms,
                choices=request.choices,
                plan_hash=plan.plan_hash,
            ),
            summary=f"{direction.title()} {len(keys)} CC Port asset resource(s)",
            metadata=_asset_batch_approval_metadata(request),
            json_output=json_output,
            data=plan_payload,
        )
        if consumed is None:
            return
    try:
        result = apply_asset_batch_plan(
            direction,
            resource_keys=keys,
            target_platforms=platforms,
            choices=batch_choices,
            expected_plan_hash=plan.plan_hash,
            config=cfg,
        )
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="asset_batch_apply_failed",
            message=f"Asset batch apply failed: {exc}",
        )
    result_payload = to_wire_value(result)
    if not isinstance(result_payload, dict):
        raise TypeError("Asset batch result must be an object.")
    result_payload.update(
        {
            "operation_id": asset_batch_operation_id(plan.plan_hash),
            "approval_id": consumed.approval_id if consumed else "",
            "approval_status": consumed.status if consumed else "not-required",
        }
    )
    if result.status == "stale-plan" and result.stale_plan is not None:
        replacement_approval = _create_asset_batch_approval(result.stale_plan, request)
        result_payload["stale_plan"] = _asset_batch_plan_payload(
            result.stale_plan,
            approval=replacement_approval,
        )
        if not json_output:
            _print_asset_batch_plan(result.stale_plan, request=request)
            if replacement_approval:
                console.print(
                    f"Approval: {replacement_approval.approval_id} ({replacement_approval.status})"
                )
    if json_output:
        envelope = _print_wire_result(
            result_payload,
            status=result.status,
            message=_asset_batch_result_message(result.status),
        )
        if not envelope.ok:
            raise typer.Exit(wire_exit_code(envelope))
    else:
        console.print(f"[bold]{result.status}[/bold]")
        for item in result.results:
            console.print(
                f"{item.status}: {item.target_resource_key}"
                f"{f' on {item.platform}' if item.platform else ''} - {item.message}"
            )
    if not json_output and result.status not in {"succeeded"}:
        raise typer.Exit(1)


def _load_asset_batch_choices(path: Path | None) -> list[AssetBatchChoice]:
    if path is None:
        return []
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_items = payload.get("items", payload) if isinstance(payload, dict) else payload
    if isinstance(raw_items, dict):
        iterable = [
            {"resource_key": key, **(value if isinstance(value, dict) else {"resolution": value})}
            for key, value in raw_items.items()
        ]
    elif isinstance(raw_items, list):
        iterable = raw_items
    else:
        raise ValueError("Batch choices must be a mapping or list.")
    return _service_asset_batch_choices(parse_asset_batch_choices(iterable))


def _load_asset_batch_request(source: str) -> AssetBatchRequestWire:
    """Read and strictly validate one reusable batch request."""

    if source == "-":
        if sys.stdin.isatty():
            raise ValueError("--request - requires JSON on stdin.")
        raw = sys.stdin.read()
    else:
        raw = Path(source).expanduser().read_text(encoding="utf-8")
    if not raw.strip():
        raise ValueError("The batch request is empty.")
    try:
        payload = json.loads(raw.lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid batch request JSON at line {exc.lineno}, column {exc.colno}."
        ) from exc
    return parse_asset_batch_request(payload)


def _service_asset_batch_choices(
    choices: list[AssetBatchChoiceWire],
) -> list[AssetBatchChoice]:
    """Convert validated wire choices into the shared service input type."""

    return [AssetBatchChoice(**choice.model_dump(mode="python")) for choice in choices]


def _asset_batch_result_message(status: str) -> str:
    return {
        "stale-plan": "The batch plan is stale; review the returned fresh plan.",
        "partial": "The batch completed only partially.",
        "needs-action": "The batch requires additional user action.",
        "failed": "The batch failed.",
    }.get(status, f"The batch did not complete successfully: {status}.")


def _print_asset_action_plan(
    plan: object,
    *,
    plan_hash: str,
    approval_id: str,
    approval_status: str,
) -> None:
    table = Table(title="CC Port asset action plan")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for label, value in (
        ("Operation", plan.operation_id),  # type: ignore[attr-defined]
        ("Action", plan.action),  # type: ignore[attr-defined]
        ("Source", plan.resource_key),  # type: ignore[attr-defined]
        ("Target", plan.target_resource_key),  # type: ignore[attr-defined]
        ("Platform", plan.platform),  # type: ignore[attr-defined]
        ("Local instance", plan.local_instance_id or "-"),  # type: ignore[attr-defined]
        ("New resource name", plan.new_name or "-"),  # type: ignore[attr-defined]
        ("New install name", plan.new_install_name or "-"),  # type: ignore[attr-defined]
        (
            "Overwrite unmanaged",
            str(plan.overwrite_unmanaged).lower(),  # type: ignore[attr-defined]
        ),
        (
            "Link target confirmed",
            str(plan.link_target_confirmed).lower(),  # type: ignore[attr-defined]
        ),
        ("Remote commit", plan.remote_commit),  # type: ignore[attr-defined]
        ("Plan hash", plan_hash),
        (
            "Approval",
            f"{approval_id or '(create or select during apply)'} ({approval_status})",
        ),
        ("Blocked", str(plan.blocked).lower()),  # type: ignore[attr-defined]
    ):
        table.add_row(label, str(value))
    console.print(table)
    for warning in plan.warnings:  # type: ignore[attr-defined]
        console.print(f"[yellow]Warning:[/yellow] {escape(warning)}")
    for blocker in plan.blockers:  # type: ignore[attr-defined]
        console.print(f"[red]Blocked:[/red] {escape(blocker)}")


def _print_asset_batch_plan(
    plan: object,
    *,
    request: AssetBatchRequestWire | None = None,
) -> None:
    table = Table(title=f"CC Port asset batch {getattr(plan, 'direction', '')}")
    table.add_column("Resource", style="bold")
    table.add_column("Platform")
    table.add_column("Action")
    table.add_column("Plan")
    table.add_column("Reason")
    for item in getattr(plan, "items", []):
        table.add_row(
            getattr(item, "resource_key", ""),
            getattr(item, "platform", "") or "-",
            getattr(item, "action", ""),
            getattr(item, "disposition", ""),
            getattr(item, "reason", "") or "-",
        )
    console.print(table)
    console.print(
        f"Executable: {getattr(plan, 'executable_count', 0)}; "
        f"blocked: {getattr(plan, 'blocked_count', 0)}; "
        f"skipped: {getattr(plan, 'skipped_count', 0)}"
        f"; manual: {sum(item.disposition == 'manual' for item in getattr(plan, 'items', []))}"
    )
    console.print(f"Plan hash: {getattr(plan, 'plan_hash', '')}")
    if request is None:
        return
    console.print(
        f"Resources: {', '.join(request.resource_keys)}; "
        f"target profiles: {', '.join(request.target_platforms) or '-'}"
    )
    if not request.choices:
        return
    choices = Table(title="Explicit batch choices")
    choices.add_column("Resource", style="bold")
    choices.add_column("Profile")
    choices.add_column("Local instance")
    choices.add_column("Resolution / name")
    choices.add_column("Overwrite")
    choices.add_column("Ownership")
    choices.add_column("Link target")
    choices.add_column("Plugin track")
    for choice in request.choices:
        choices.add_row(
            choice.resource_key,
            choice.platform or "-",
            choice.local_instance_id or "-",
            f"{choice.resolution} / {choice.new_name or '-'}",
            str(choice.overwrite_unmanaged).lower(),
            str(choice.ownership_confirmed).lower(),
            str(choice.link_target_confirmed).lower(),
            choice.plugin_track or "-",
        )
    console.print(choices)


def _print_resource_info(info: object) -> None:
    table = Table(title="CC Port resource repository")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for field in (
        "repo_name",
        "local_path",
        "registry_path",
        "repo_url",
        "remote_url",
        "branch",
        "current_branch",
        "exists",
        "is_git_repo",
        "dirty",
    ):
        value = getattr(info, field)
        table.add_row(field, str(value))
    console.print(table)


def _print_resource_sync_plan(plan: object) -> None:
    table = Table(title="CC Port resource Git synchronization")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for field in (
        "operation_id",
        "status",
        "branch",
        "local_commit",
        "remote_commit",
        "merge_base",
        "ahead",
        "behind",
        "merge_commit",
        "detail",
    ):
        table.add_row(field, str(getattr(plan, field)))
    console.print(table)
    conflicts = plan.conflicts
    if conflicts:
        conflict_table = Table(title="Conflicts")
        conflict_table.add_column("Id")
        conflict_table.add_column("Path")
        conflict_table.add_column("Reason")
        for conflict in conflicts:
            conflict_table.add_row(conflict.id, conflict.path, conflict.reason)
        console.print(conflict_table)


def _maybe_push_resource_repo(cfg: Config, *, push: bool, no_push: bool) -> None:
    if push and no_push:
        console.print("[red]Choose only one of --push or --no-push.[/red]")
        raise typer.Exit(2)
    should_push = push
    if not push and not no_push:
        _require_interactive_input(
            "Push confirmation is required; pass --push or --no-push.",
            code="confirmation_required",
        )
        should_push = typer.confirm(
            "Push changes to your private resource repo now?", default=False
        )
    if not should_push:
        console.print("[yellow]Not pushed.[/yellow] Run `cc-port resource push` when ready.")
        return
    try:
        info = push_resource_repo(config=cfg)
    except Exception as exc:
        console.print(f"[red]Resource push failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Pushed[/green] {info.local_path}")


# ---- persisted operations ---- #


@operations_app.command("list")
def cmd_operations_list(
    limit: int = typer.Option(20, "--limit", min=1, max=100),
    offset: int = typer.Option(0, "--offset", min=0),
) -> None:
    """List local write operations in reverse chronological order."""
    page = operation_history_page(offset=offset, limit=limit)
    table = Table(title="CC Port operation history")
    table.add_column("Operation")
    table.add_column("Kind")
    table.add_column("Status")
    table.add_column("Changed")
    table.add_column("Started")
    table.add_column("Restorable")
    for item in page.operations:
        table.add_row(
            item.operation_id,
            item.kind,
            item.status,
            str(item.changed_target_count),
            item.started_at,
            "yes" if item.restorable else "no",
        )
    console.print(table)
    console.print(
        f"Showing {len(page.operations)} of {page.total} operation(s) from offset {page.offset}."
    )


@operations_app.command("show")
def cmd_operations_show(
    operation_id: str = typer.Argument(...),
) -> None:
    """Show one operation including metadata and target details."""
    try:
        detail = operation_detail(operation_id)
    except Exception as exc:
        console.print(f"[red]Operation detail failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_machine_json(asdict(detail))


@operations_app.command("restore")
def cmd_operations_restore(
    operation_id: str = typer.Argument(...),
    force: bool = typer.Option(
        False,
        "--force",
        help="Restore even when targets changed after the original operation.",
    ),
) -> None:
    """Restore a successful operation to its before-state."""
    try:
        result = restore_operation(operation_id, force=force)
    except Exception as exc:
        console.print(f"[red]Operation restore failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Restored[/green] {result.source_operation_id} "
        f"with operation {result.operation.operation_id}"
    )


@operations_app.command("retention-plan")
def cmd_operations_retention_plan(
    retention_days: int | None = typer.Option(None, "--retention-days", min=0),
    keep_latest: int | None = typer.Option(None, "--keep-latest", min=0),
    max_backup_mb: int | None = typer.Option(None, "--max-backup-mb", min=0),
) -> None:
    """Preview operation and backup cleanup without deleting anything."""
    plan = build_state_retention_plan(
        config=_load(),
        retention_days=retention_days,
        keep_latest_operations=keep_latest,
        max_backup_mb=max_backup_mb,
    )
    _print_retention_plan(plan)


@operations_app.command("prune")
def cmd_operations_prune(
    operation_id: list[str] = typer.Option(
        [],
        "--operation-id",
        help="Delete only this eligible operation. Repeatable; defaults to all candidates.",
    ),
    retention_days: int | None = typer.Option(None, "--retention-days", min=0),
    keep_latest: int | None = typer.Option(None, "--keep-latest", min=0),
    max_backup_mb: int | None = typer.Option(None, "--max-backup-mb", min=0),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip cleanup confirmation."),
) -> None:
    """Explicitly delete operations selected by a fresh retention plan."""
    cfg = _load()
    plan = build_state_retention_plan(
        config=cfg,
        retention_days=retention_days,
        keep_latest_operations=keep_latest,
        max_backup_mb=max_backup_mb,
    )
    selected = operation_id or [item.operation_id for item in plan.candidates]
    _print_retention_plan(plan)
    if not selected:
        console.print("[yellow]No operations are eligible for cleanup.[/yellow]")
        return
    if not yes:
        _require_interactive_input(
            "State cleanup requires confirmation; pass --yes.",
            code="confirmation_required",
        )
    if not yes and not typer.confirm(
        f"Delete {len(selected)} operation record(s) and their backups?",
        default=False,
    ):
        console.print("[yellow]Cleanup cancelled.[/yellow]")
        return
    try:
        result = prune_state(
            selected,
            config=cfg,
            retention_days=retention_days,
            keep_latest_operations=keep_latest,
            max_backup_mb=max_backup_mb,
        )
    except Exception as exc:
        console.print(f"[red]State cleanup failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Deleted[/green] {len(result.deleted_operation_ids)} operation(s); "
        f"reclaimed {result.reclaimed_bytes} bytes."
    )
    if result.failed:
        for failure in result.failed:
            console.print(f"[red]{failure.operation_id}:[/red] {failure.error}")
    console.print(f"Audit: {result.audit_path}")


def _print_retention_plan(plan: StateRetentionPlan) -> None:
    summary = Table(title="CC Port state retention plan")
    summary.add_column("Field")
    summary.add_column("Value")
    for field in (
        "operation_count",
        "running_operation_count",
        "protected_operation_count",
        "operation_record_bytes",
        "backup_bytes",
        "orphan_backup_count",
        "orphan_backup_bytes",
        "candidate_count",
        "reclaimable_bytes",
        "projected_backup_bytes",
    ):
        summary.add_row(field, str(getattr(plan, field)))
    console.print(summary)
    if plan.candidates:
        candidates = Table(title="Cleanup candidates")
        candidates.add_column("Operation")
        candidates.add_column("Kind")
        candidates.add_column("Age days")
        candidates.add_column("Bytes")
        candidates.add_column("Reasons")
        for item in plan.candidates:
            candidates.add_row(
                item.operation_id,
                item.kind,
                str(item.age_days),
                str(item.reclaimable_bytes),
                ", ".join(item.reasons),
            )
        console.print(candidates)


@operations_app.command("orphans")
def cmd_operations_orphans() -> None:
    """List backup entries that have no valid operation record."""
    table = Table(title="Orphan backups")
    table.add_column("Name")
    table.add_column("Kind")
    table.add_column("Bytes")
    table.add_column("Modified")
    for item in list_orphan_backups():
        table.add_row(
            item.name,
            item.kind,
            str(item.size_bytes),
            item.modified_at,
        )
    console.print(table)


@operations_app.command("orphan-export")
def cmd_operations_orphan_export(
    name: str = typer.Argument(...),
    out: Path | None = typer.Option(None, "--out", "-o"),
) -> None:
    """Export one orphan backup to a ZIP without following symlinks."""
    try:
        result = export_orphan_backup(name, output_path=out, config=_load())
    except Exception as exc:
        console.print(f"[red]Orphan export failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Exported[/green] {result.name} to {result.output_path}")


@operations_app.command("orphan-quarantine")
def cmd_operations_orphan_quarantine(
    name: list[str] = typer.Option(
        [],
        "--name",
        help="Orphan backup name. Repeat for multiple items.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Move explicitly selected orphan backups into quarantine."""
    if not name:
        console.print("[red]Select at least one orphan with --name.[/red]")
        raise typer.Exit(2)
    if not yes:
        _require_interactive_input(
            "Orphan quarantine requires confirmation; pass --yes.",
            code="confirmation_required",
        )
    if not yes and not typer.confirm(
        f"Quarantine {len(set(name))} orphan backup(s)?",
        default=False,
    ):
        console.print("[yellow]Quarantine cancelled.[/yellow]")
        return
    try:
        result = quarantine_orphan_backups(name, config=_load())
    except Exception as exc:
        console.print(f"[red]Orphan quarantine failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Quarantined[/green] {result.quarantine.item_count} item(s) "
        f"as {result.quarantine.quarantine_id}"
    )
    console.print(f"Audit: {result.audit_path}")


@operations_app.command("quarantines")
def cmd_operations_quarantines() -> None:
    """List orphan backup quarantine batches."""
    table = Table(title="Orphan backup quarantines")
    table.add_column("Quarantine")
    table.add_column("Items")
    table.add_column("Bytes")
    table.add_column("Created")
    for item in list_orphan_quarantines():
        table.add_row(
            item.quarantine_id,
            str(item.item_count),
            str(item.size_bytes),
            item.created_at,
        )
    console.print(table)


@operations_app.command("quarantine-delete")
def cmd_operations_quarantine_delete(
    quarantine_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Permanently delete one orphan backup quarantine batch."""
    if not yes:
        _require_interactive_input(
            "Quarantine deletion requires confirmation; pass --yes.",
            code="confirmation_required",
        )
    if not yes and not typer.confirm(
        f"Permanently delete quarantine {quarantine_id}?",
        default=False,
    ):
        console.print("[yellow]Delete cancelled.[/yellow]")
        return
    result = delete_orphan_quarantine(quarantine_id, config=_load())
    if not result.deleted:
        console.print(f"[red]Quarantine delete failed:[/red] {result.error}")
        raise typer.Exit(1)
    console.print(
        f"[green]Deleted[/green] {quarantine_id}; reclaimed {result.reclaimed_bytes} bytes."
    )
    console.print(f"Audit: {result.audit_path}")


@operations_app.command("audits")
def cmd_operations_audits(
    limit: int = typer.Option(50, "--limit", min=1, max=500),
) -> None:
    """List state maintenance audit records."""
    table = Table(title="State maintenance audits")
    table.add_column("Audit")
    table.add_column("Action")
    table.add_column("Status")
    table.add_column("Items")
    table.add_column("Reclaimed")
    table.add_column("Created")
    for item in list_maintenance_audits(limit=limit):
        table.add_row(
            item.audit_id,
            item.action,
            item.status,
            str(item.item_count),
            str(item.reclaimed_bytes),
            item.created_at,
        )
    console.print(table)


@operations_app.command("audit")
def cmd_operations_audit(
    audit_id: str = typer.Argument(...),
) -> None:
    """Show one state maintenance audit record."""
    try:
        payload = load_maintenance_audit(audit_id)
    except Exception as exc:
        console.print(f"[red]Maintenance audit failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_machine_json(payload)


# ---- publish ---- #


@app.command("publish")
def cmd_publish(
    path: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    name: str | None = typer.Option(None, "--name", help="Override the item name."),
    description: str | None = typer.Option(None, "--description"),
    kind: str = typer.Option(
        "skill",
        "--kind",
        "-k",
        help="Dedicated-repository type: skill | mcp | rule | prompt | plugin.",
    ),
    private: bool | None = typer.Option(
        None,
        "--private/--public",
        help="Repo visibility. If omitted, you'll be prompted.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the interactive visibility prompt.",
    ),
    update_visibility: bool = typer.Option(
        False,
        "--update-visibility",
        help="If the GitHub repo already exists with a different visibility, change it.",
    ),
    mcp_config_json: str | None = typer.Option(
        None,
        "--mcp-config",
        help='MCP server config as JSON string (for --kind mcp), e.g. \'{"command":"npx","args":["-y","@mcp/test"]}\'.',
    ),
    tags: list[str] = typer.Option([], "--tag", "-t", help="Tags for discovery (repeatable)."),
    category: str = typer.Option("", "--category", "-c", help="Category, e.g. 'software-dev'."),
    platforms: list[str] = typer.Option(
        [],
        "--platform",
        "-p",
        help="Restrict installation to these platforms (repeatable).",
    ),
    version: str = typer.Option("", "--version", "-v", help="Semantic version, e.g. '1.0.0'."),
    author: str = typer.Option("", "--author", help="Author name."),
    item_license: str = typer.Option("", "--license", help="SPDX license id, e.g. 'MIT'."),
) -> None:
    """Publish a local directory to a new GitHub repository."""
    cfg = _load()

    if kind not in VALID_KINDS:
        console.print(
            f"[red]Invalid kind {kind!r}.[/red] Expected: "
            "skill, mcp, rule, prompt, plugin, instruction, memory."
        )
        raise typer.Exit(2)
    if kind in {"instruction", "memory"}:
        console.print(
            "[red]Personal instructions and memories must use `cc-port upload` "
            "or the asset upload workflow.[/red]"
        )
        raise typer.Exit(2)

    mcp_config = None
    if mcp_config_json:
        try:
            mcp_config = json.loads(mcp_config_json)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Invalid --mcp-config JSON:[/red] {exc}")
            raise typer.Exit(2) from exc

    if private is None and not yes:
        _require_interactive_input(
            "Repository visibility is required; pass --private, --public, or --yes to use the configured default."
        )
        default = cfg.github.default_private
        choice = (
            typer.prompt(
                "Repository visibility? [public/private]",
                default="private" if default else "public",
            )
            .strip()
            .lower()
        )
        if choice in {"private", "priv", "p"}:
            private = True
        elif choice in {"public", "pub"}:
            private = False
        else:
            console.print(f"[red]Invalid choice {choice!r}.[/red] Expected 'public' or 'private'.")
            raise typer.Exit(2)

    try:
        result = publisher.publish_local_skill(
            path,
            config=cfg,
            name=name,
            description=description,
            private=private,
            update_visibility=update_visibility,
            kind=kind,
            mcp_config=mcp_config,
            tags=tags or None,
            category=category,
            platforms=_portable_resource_platforms(cfg, kind, platforms),
            version=version,
            author=author,
            item_license=item_license,
        )
    except (ValueError, publisher.VisibilityMismatchError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    visibility = "[red]private[/red]" if result.private else "[green]public[/green]"
    state = "created" if result.created else "updated"
    msg = f"[green]Published[/green] {result.name} ({kind}) -> {result.repo_url} ({visibility}, {state})"
    if result.visibility_changed:
        msg += " [yellow](visibility changed)[/yellow]"
    console.print(msg)


# ---- add ---- #


@app.command("add")
def cmd_add(
    github_url: str = typer.Argument(..., help="HTTPS or SSH GitHub URL of the repo."),
    name: str | None = typer.Option(None, "--name"),
    subdir: str | None = typer.Option(None, "--subdir", help="Path inside the repo to install."),
    ref: str = typer.Option("main", "--ref"),
    description: str = typer.Option("", "--description"),
    kind: str = typer.Option(
        "skill",
        "--kind",
        "-k",
        help="Resource type: skill | mcp | rule | prompt | plugin | instruction | memory.",
    ),
    no_verify: bool = typer.Option(
        False,
        "--no-verify",
        help=(
            "Allow an already complete commit SHA without an online probe; "
            "branch and tag refs are still resolved."
        ),
    ),
    mcp_config_json: str | None = typer.Option(
        None,
        "--mcp-config",
        help="MCP server config as JSON string (for --kind mcp).",
    ),
    tags: list[str] = typer.Option([], "--tag", "-t", help="Tags for discovery (repeatable)."),
    category: str = typer.Option("", "--category", "-c", help="Category, e.g. 'productivity'."),
    platforms: list[str] = typer.Option(
        [],
        "--platform",
        "-p",
        help="Restrict installation to these platforms (repeatable).",
    ),
) -> None:
    """Register an external (third-party) resource in the registry."""
    cfg = _load()
    if kind not in VALID_KINDS:
        console.print(
            f"[red]Invalid kind {kind!r}.[/red] Expected: "
            "skill, mcp, rule, prompt, plugin, instruction, memory."
        )
        raise typer.Exit(2)

    mcp_config = None
    if mcp_config_json:
        try:
            mcp_config = json.loads(mcp_config_json)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Invalid --mcp-config JSON:[/red] {exc}")
            raise typer.Exit(2) from exc

    try:
        entry = publisher.add_external_skill(
            github_url,
            name=name,
            subdir=subdir,
            ref=ref,
            description=description,
            kind=kind,
            mcp_config=mcp_config,
            skip_verify=no_verify,
            token=cfg.github.token or None,
            tags=tags or None,
            category=category,
            platforms=_portable_resource_platforms(cfg, kind, platforms),
        )
    except (ValueError, publisher.RepoUnreachableError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except publisher.UnsafeMcpConfigError as exc:
        console.print(f"[red]Unsafe MCP config:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Added[/green] {entry.name} ({entry.kind}) ({entry.repo}@{entry.ref})")


@app.command("collect")
def cmd_collect(
    github_url: str = typer.Argument(..., help="HTTPS or SSH GitHub URL of the repo."),
    resource_type: str | None = typer.Option(
        None,
        "--type",
        help="Override detected type: skill, mcp, rule, prompt, plugin, instruction, memory.",
    ),
    name: str | None = typer.Option(None, "--name", help="Override the resource name."),
    mcp_config_json: str | None = typer.Option(
        None,
        "--mcp-config",
        help="Portable MCP server config as JSON (required when the collected type is mcp).",
    ),
    platforms: list[str] = typer.Option(
        [],
        "--platform",
        "-p",
        help="Restrict installation to these platforms (repeatable).",
    ),
    push: bool = typer.Option(False, "--push", help="Push private resource repo without asking."),
    no_push: bool = typer.Option(False, "--no-push", help="Do not push private resource repo."),
) -> None:
    """Collect a third-party resource as an immutable upstream reference."""
    cfg = _load()
    try:
        mcp_config = json.loads(mcp_config_json) if mcp_config_json else None
        if mcp_config is not None and not isinstance(mcp_config, dict):
            raise ValueError("--mcp-config must decode to a JSON object.")
        detected = detect_remote_resource(
            github_url,
            explicit_type=resource_type,
            token=cfg.github.token or None,
        )
        if detected.kind == "mcp" and not mcp_config:
            raise ValueError(
                "GitHub MCP references require --mcp-config with a portable command or url."
            )
        if detected.kind != "mcp" and mcp_config is not None:
            raise ValueError("--mcp-config is only valid when the collected type is mcp.")
        entry = publisher.add_external_skill(
            detected.repo_url,
            name=name or detected.name_hint,
            subdir=detected.subdir,
            ref=detected.ref,
            kind=detected.kind,
            mcp_config=mcp_config,
            skip_verify=False,
            token=cfg.github.token or None,
            tags=detected.tags,
            platforms=_portable_resource_platforms(cfg, detected.kind, platforms),
        )
    except (ValueError, ResourceDetectionError, publisher.RepoUnreachableError) as exc:
        console.print(f"[red]Collect failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(
        f"[green]Collected[/green] {entry.name} ({entry.kind}) -> {entry.repo}"
        f"{f'/{entry.subdir}' if entry.subdir else ''}"
    )
    _maybe_push_resource_repo(cfg, push=push, no_push=no_push)


@app.command("upload")
def cmd_upload(
    path: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=True),
    resource_type: str | None = typer.Option(
        None,
        "--type",
        help="Override detected type: skill, mcp, rule, prompt, plugin, instruction, memory.",
    ),
    name: str | None = typer.Option(None, "--name", help="Override the resource name."),
    platforms: list[str] = typer.Option(
        [],
        "--platform",
        "-p",
        help="Restrict installation to these platforms (repeatable).",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite an existing local resource."
    ),
    push: bool = typer.Option(False, "--push", help="Push private resource repo without asking."),
    no_push: bool = typer.Option(False, "--no-push", help="Do not push private resource repo."),
) -> None:
    """Upload a local resource into the private resource repo."""
    cfg = _load()
    try:
        kind = detect_local_resource_type(path, explicit_type=resource_type)
        result = import_local_resource(
            path,
            kind=kind,
            name=name,
            platforms=_portable_resource_platforms(cfg, kind, platforms),
            overwrite=force,
        )
    except Exception as exc:
        console.print(f"[red]Upload failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(
        f"[green]Uploaded[/green] {result.entry.name} ({result.entry.kind}) -> {result.entry.path}"
    )
    _maybe_push_resource_repo(cfg, push=push, no_push=no_push)


@app.command("import-local")
def cmd_import_local(
    path: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=True),
    name: str | None = typer.Option(None, "--name", help="Override the item name."),
    description: str | None = typer.Option(None, "--description"),
    kind: str = typer.Option(
        "skill",
        "--kind",
        "-k",
        help="Resource type: skill | mcp | rule | prompt | plugin | instruction | memory.",
    ),
    category: str = typer.Option(
        "", "--category", "-c", help="Stored under <kind>/<category>/<name>."
    ),
    tags: list[str] = typer.Option([], "--tag", "-t", help="Tags for discovery (repeatable)."),
    platforms: list[str] = typer.Option(
        [],
        "--platform",
        "-p",
        help="Restrict installation to these platforms (repeatable).",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite an existing local resource."
    ),
    mcp_config_json: str | None = typer.Option(
        None, "--mcp-config", help="MCP server config JSON."
    ),
) -> None:
    """Copy a local resource into this repository and register it."""
    cfg = _load()
    if kind not in VALID_KINDS:
        console.print(
            f"[red]Invalid kind {kind!r}.[/red] Expected: "
            "skill, mcp, rule, prompt, plugin, instruction, memory."
        )
        raise typer.Exit(2)

    mcp_config = None
    if mcp_config_json:
        try:
            mcp_config = json.loads(mcp_config_json)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Invalid --mcp-config JSON:[/red] {exc}")
            raise typer.Exit(2) from exc

    try:
        result = import_local_resource(
            path,
            kind=kind,
            name=name,
            description=description,
            category=category,
            tags=tags or None,
            platforms=_portable_resource_platforms(cfg, kind, platforms),
            overwrite=force,
            mcp_config=mcp_config,
        )
    except Exception as exc:
        console.print(f"[red]Import failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Imported[/green] {result.entry.name} ({result.entry.kind}) -> {result.entry.path}"
    )


@app.command("export-plugin")
def cmd_export_plugin(
    name: str | None = typer.Option(
        None,
        "--name",
        help="Plugin name (defaults to a kebab-case form of the repo folder name).",
    ),
) -> None:
    """Generate .claude-plugin/plugin.json for local skills in this repository."""
    path = export_claude_plugin(plugin_name=name)
    console.print(f"[green]Generated[/green] {path}")


# ---- remove ---- #


@app.command("remove")
def cmd_remove(
    name: str = typer.Argument(...),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Resource type."),
    uninstall: bool = typer.Option(
        False, "--uninstall", help="Also delete the local installation."
    ),
) -> None:
    """Remove an item from the registry."""
    cfg = _load()
    registry = load_registry()
    entry = registry.get(name, kind)
    removed = publisher.remove_skill(name, kind=kind)
    if removed is None:
        console.print(f"[yellow]No item named[/yellow] {name}")
        raise typer.Exit(1)
    if uninstall and entry is not None:
        uninstall_one(entry, config=cfg)
    console.print(f"[green]Removed[/green] {name}")


# ---- list ---- #


@app.command("list")
def cmd_list(
    kind: str | None = typer.Option(None, "--kind", "-k", help="Filter by resource type."),
) -> None:
    """List items in the registry along with their local installation state."""
    cfg = _load()
    registry = load_registry()
    items = registry.items
    if kind:
        items = [i for i in items if i.kind == kind]
    if not items:
        console.print("[yellow]Registry is empty.[/yellow] Use `cc-port publish` or `cc-port add`.")
        return
    table = Table(title=f"CC Port registry ({find_registry_path()})")
    table.add_column("Name", style="bold")
    table.add_column("Kind")
    table.add_column("Source")
    table.add_column("Repo")
    table.add_column("Ref")
    table.add_column("Subdir")
    table.add_column("Visibility")
    table.add_column("Installed")
    table.add_column("Reachable")
    install_root = cfg.install.target_path
    for s in items:
        installed = (install_root / s.install_target_name()).exists()
        if s.private is True:
            visibility_cell = "[red]private[/red]"
        elif s.private is False:
            visibility_cell = "[green]public[/green]"
        else:
            visibility_cell = "-"
        if s.reachable is True:
            reachable_cell = "[green]yes[/green]"
        elif s.reachable is False:
            reachable_cell = "[red]no[/red]"
        else:
            reachable_cell = "[dim]-[/dim]"
        kind_style = {"skill": "cyan", "mcp": "magenta", "rule": "yellow"}.get(s.kind, "")
        table.add_row(
            s.name,
            f"[{kind_style}]{s.kind}[/{kind_style}]" if kind_style else s.kind,
            s.source,
            s.repo,
            s.ref,
            s.subdir or "-",
            visibility_cell,
            "yes" if installed else "no",
            reachable_cell,
        )
    console.print(table)


# ---- search ---- #


@app.command("search")
def cmd_search(
    query: str = typer.Argument("", help="Search query (matches name, description, tags)."),
    tags_filter: list[str] = typer.Option([], "--tag", "-t", help="Filter by tag (repeatable)."),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Filter by resource type."),
    category: str | None = typer.Option(None, "--category", "-c", help="Filter by category."),
    remote: bool = typer.Option(
        False, "--remote", "-r", help="Also search GitHub for SKILL.md repos."
    ),
) -> None:
    """Search the local registry (and optionally GitHub) for resources.

    Examples:
        cc-port search python
        cc-port search --tag testing --kind skill
        cc-port search fastapi --remote
    """
    registry = load_registry()
    items = registry.items

    if kind:
        items = [i for i in items if i.kind == kind]
    if category:
        items = [i for i in items if i.category and category.lower() in i.category.lower()]
    if tags_filter:
        tag_set = {t.lower() for t in tags_filter}
        items = [i for i in items if tag_set & {t.lower() for t in i.tags}]
    if query:
        q = query.lower()
        items = [
            i
            for i in items
            if q in i.name.lower()
            or q in i.description.lower()
            or any(q in t.lower() for t in i.tags)
        ]

    if items:
        table = Table(title="Local results")
        table.add_column("Name", style="bold")
        table.add_column("Kind")
        table.add_column("Description")
        table.add_column("Tags")
        table.add_column("Category")
        for s in items:
            kind_style = {"skill": "cyan", "mcp": "magenta", "rule": "yellow"}.get(s.kind, "")
            table.add_row(
                s.name,
                f"[{kind_style}]{s.kind}[/{kind_style}]" if kind_style else s.kind,
                (s.description[:60] + "...") if len(s.description) > 60 else s.description or "-",
                ", ".join(s.tags) if s.tags else "-",
                s.category or "-",
            )
        console.print(table)
    else:
        console.print("[yellow]No local matches.[/yellow]")

    if remote:
        _search_github(query or "SKILL.md")


def _search_github(query: str) -> None:
    """Search GitHub for repos containing SKILL.md (best-effort)."""
    try:
        import urllib.parse
        import urllib.request

        search_q = urllib.parse.quote(f"{query} filename:SKILL.md")
        url = f"https://api.github.com/search/repositories?q={search_q}&per_page=10&sort=stars"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            import json as _json

            data = _json.loads(resp.read())

        repos = data.get("items", [])
        if not repos:
            console.print("[yellow]No remote results on GitHub.[/yellow]")
            return

        table = Table(title="GitHub results (add with `cc-port add <url>`)")
        table.add_column("Repository", style="bold")
        table.add_column("Stars")
        table.add_column("Description")
        for r in repos:
            table.add_row(
                r.get("html_url", ""),
                str(r.get("stargazers_count", 0)),
                (r.get("description") or "-")[:70],
            )
        console.print(table)
    except Exception as exc:
        console.print(f"[yellow]GitHub search failed:[/yellow] {exc}")


# ---- sync ---- #


@app.command("sync")
def cmd_sync(
    only: list[str] = typer.Option(None, "--only", help="Restrict to one or more item names."),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Only sync items of this type."),
    tags_filter: list[str] = typer.Option(
        None, "--tag", "-t", help="Only sync items with these tags."
    ),
    include_mcp: bool = typer.Option(False, "--include-mcp", help="Also sync MCP configs."),
    include_rule: bool = typer.Option(False, "--include-rule", help="Also sync rules."),
    include_prompt: bool = typer.Option(False, "--include-prompt", help="Also sync prompts."),
    include_plugin: bool = typer.Option(False, "--include-plugin", help="Also sync plugins."),
    all_kinds: bool = typer.Option(False, "--all-kinds", help="Sync every resource kind."),
    platform: str | None = typer.Option(
        None, "--platform", "-p", help="Only sync to this platform."
    ),
) -> None:
    """Install or update registry items.

    By default this syncs skills only. MCP/rule/prompt/plugin resources are
    opt-in because they can modify tool configuration or agent behavior.
    """
    cfg = _load()
    include_kinds = set()
    if include_mcp:
        include_kinds.add("mcp")
    if include_rule:
        include_kinds.add("rule")
    if include_prompt:
        include_kinds.add("prompt")
    if include_plugin:
        include_kinds.add("plugin")
    results = sync_all(
        config=cfg,
        only=only or None,
        kind=kind,
        tags=tags_filter or None,
        include_optional=all_kinds,
        include_kinds=include_kinds,
        platform_filter=platform,
    )
    if not results:
        console.print("[yellow]Nothing to sync.[/yellow]")
        return
    table = Table(title=f"Sync -> {cfg.install.target_path}")
    table.add_column("Name")
    table.add_column("Action")
    table.add_column("Path")
    table.add_column("Platforms")
    table.add_column("Operation")
    table.add_column("Detail")
    style = {
        SyncAction.INSTALLED: "green",
        SyncAction.UPDATED: "cyan",
        SyncAction.UNCHANGED: "dim",
        SyncAction.SKIPPED: "yellow",
        SyncAction.FAILED: "red",
        SyncAction.REPO_GONE: "red bold",
    }
    failures = 0
    repo_gone = 0
    for r in results:
        if r.action is SyncAction.FAILED:
            failures += 1
        elif r.action is SyncAction.REPO_GONE:
            repo_gone += 1
        table.add_row(
            r.name,
            f"[{style[r.action]}]{r.action.value}[/{style[r.action]}]",
            str(r.install_path),
            ", ".join(r.platforms_installed) or "-",
            r.operation_id or "-",
            r.detail,
        )
    console.print(table)
    if repo_gone:
        console.print(
            f"\n[yellow]{repo_gone} repo(s) appear to have been deleted.[/yellow] "
            "Run [bold]cc-port check --prune[/bold] to clean up."
        )
    if failures or repo_gone:
        raise typer.Exit(1)


@app.command("plan-install")
def cmd_plan_install(
    name: str = typer.Argument(..., help="Registered resource name."),
    platform: str | None = typer.Option(
        None, "--platform", "-p", help="Only plan for this platform."
    ),
) -> None:
    """Build an install plan without writing local files."""
    try:
        plan = resource_install_plan(name, config=_load(), platform_filter=platform)
    except Exception as exc:
        console.print(f"[red]Install plan failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[bold]{plan.name}[/bold] ({plan.kind})")
    console.print(f"Source: {plan.source_path}")
    if plan.manifest_path:
        console.print(f"Manifest: {plan.manifest_path}")
    if plan.warnings:
        for warning in plan.warnings:
            console.print(f"[yellow]Warning:[/yellow] {warning}")

    target_table = Table(title="Install targets")
    target_table.add_column("Platform")
    target_table.add_column("Mechanism")
    target_table.add_column("Auto")
    target_table.add_column("Path")
    for target in plan.targets:
        target_table.add_row(
            target.platform,
            target.install_mechanism,
            "yes" if target.auto_install else "manual",
            str(target.path),
        )
    console.print(target_table)
    console.print(f"Files after filtering: {len(plan.files)}")


# ---- status ---- #


@app.command("status")
def cmd_status(
    kind: str | None = typer.Option(None, "--kind", "-k", help="Filter by resource type."),
) -> None:
    """Show local vs remote commit status for each item."""
    cfg = _load()
    rows = status_all(config=cfg, kind=kind)
    if not rows:
        console.print("[yellow]Registry is empty.[/yellow]")
        return
    table = Table(title="CC Port status")
    table.add_column("Name")
    table.add_column("Installed")
    table.add_column("Local")
    table.add_column("Remote")
    table.add_column("Update?")
    for s in rows:
        table.add_row(
            s.name,
            "yes" if s.installed else "no",
            (s.local_commit or "-")[:10],
            (s.remote_commit or "-")[:10],
            "[red]yes[/red]" if s.has_update else "no",
        )
    console.print(table)


# ---- check ---- #


@app.command("check")
def cmd_check(
    kind: str | None = typer.Option(None, "--kind", "-k", help="Filter by resource type."),
    prune: bool = typer.Option(
        False, "--prune", help="Remove unreachable items from the registry."
    ),
    uninstall: bool = typer.Option(
        False, "--uninstall", help="Also delete local files when pruning."
    ),
) -> None:
    """Check reachability of all registered repositories.

    Reports which items point to repositories that no longer exist or are
    inaccessible.  Use ``--prune`` to automatically remove dead entries.
    """
    cfg = _load()
    results, pruned = check_all(config=cfg, kind=kind, prune=prune, uninstall=uninstall)
    if not results:
        console.print("[yellow]Registry is empty.[/yellow]")
        return

    table = Table(title="CC Port Health Check")
    table.add_column("Name", style="bold")
    table.add_column("Kind")
    table.add_column("Repo")
    table.add_column("Status")
    unreachable = 0
    for r in results:
        if r.reachable:
            status = "[green]reachable[/green]"
        else:
            unreachable += 1
            status = "[red]NOT FOUND[/red]"
        kind_style = {"skill": "cyan", "mcp": "magenta", "rule": "yellow"}.get(r.kind, "")
        table.add_row(
            r.name,
            f"[{kind_style}]{r.kind}[/{kind_style}]" if kind_style else r.kind,
            r.repo,
            status,
        )
    console.print(table)

    if pruned:
        console.print(
            f"\n[green]Pruned {len(pruned)} unreachable item(s):[/green] {', '.join(pruned)}"
        )
    elif unreachable:
        console.print(
            f"\n[yellow]{unreachable} unreachable item(s) found.[/yellow] "
            "Run [bold]cc-port check --prune[/bold] to remove them."
        )

    if unreachable and not prune:
        raise typer.Exit(1)


# ---- doctor ---- #


@app.command("doctor")
def cmd_doctor(
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Check that the environment is ready (git, token, permissions, platforms)."""
    try:
        cfg = _load()
        checks = build_doctor_checks(cfg)
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="doctor_failed",
            message=f"Environment checks failed: {exc}",
        )
    if json_output:
        statuses = [str(check.get("status") or "error") for check in checks]
        report = {
            "status": (
                "error" if "error" in statuses else "warning" if "warning" in statuses else "ok"
            ),
            "ok_count": statuses.count("ok"),
            "warning_count": statuses.count("warning"),
            "error_count": statuses.count("error"),
            "skipped_count": statuses.count("skipped"),
            "checks": [
                {
                    "id": str(check.get("id") or ""),
                    "label": str(check.get("label") or ""),
                    "status": str(check.get("status") or "error"),
                    "ok": bool(check.get("ok")),
                    "detail": str(check.get("detail") or ""),
                    "profile_id": (
                        check["profile"].name
                        if isinstance(check.get("profile"), PlatformProfile)
                        else ""
                    ),
                }
                for check in checks
            ],
        }
        if has_doctor_errors(checks):
            _exit_wire_error(
                json_output=True,
                code="doctor_errors",
                message="Environment checks reported errors.",
                status="blocked",
                exit_code=WIRE_EXIT_SAFE_NONCOMPLETION,
                data=report,
            )
        _print_wire_success(report, status="ready")
        return
    general_checks = [
        check for check in checks if not str(check.get("id", "")).startswith("platform:")
    ]
    platform_checks = [
        check for check in checks if str(check.get("id", "")).startswith("platform:")
    ]

    for check in general_checks:
        _print_doctor_check(check)
    console.print(f"[green]Registry:[/green] {escape(str(find_registry_path()))}")
    console.print("\n[bold]Platforms[/bold]")
    for check in platform_checks:
        _print_doctor_check(check, indent="  ")

    if has_doctor_errors(checks):
        raise typer.Exit(1)


def _print_doctor_check(check: dict, *, indent: str = "") -> None:
    status = str(check.get("status") or ("ok" if check.get("ok") else "error"))
    style = {
        "ok": "green",
        "warning": "yellow",
        "error": "red",
        "skipped": "dim",
    }.get(status, "white")
    label = escape(str(check.get("label") or check.get("id") or "Check"))
    detail = escape(str(check.get("detail") or ""))
    console.print(f"{indent}[{style}]{label}:[/{style}] {detail}")


# ---- uninstall ---- #


@app.command("uninstall")
def cmd_uninstall(
    name: str = typer.Argument(...),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Resource type."),
) -> None:
    """Remove an item's local files (without touching the registry)."""
    cfg = _load()
    registry = load_registry()
    entry = registry.get(name, kind)
    if entry is None:
        console.print(f"[yellow]No item named[/yellow] {name}")
        raise typer.Exit(1)
    removed = uninstall_one(entry, config=cfg)
    msg = "Uninstalled" if removed else "Nothing to remove"
    console.print(f"[green]{msg}[/green] {name}")


# ---- set-visibility ---- #


@app.command("set-visibility")
def cmd_set_visibility(
    name: str = typer.Argument(..., help="Name of an `owned` item in the registry."),
    visibility: str = typer.Argument(..., help="`public` or `private`."),
) -> None:
    """Change the GitHub visibility of an owned repository (public <-> private)."""
    cfg = _load()
    v = visibility.strip().lower()
    if v not in {"public", "private"}:
        console.print(
            f"[red]Invalid visibility {visibility!r}.[/red] Expected 'public' or 'private'."
        )
        raise typer.Exit(2)
    private = v == "private"
    try:
        result = publisher.set_skill_visibility(name, config=cfg, private=private)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    label = "[red]private[/red]" if result["private"] else "[green]public[/green]"
    console.print(f"[green]Updated[/green] {result['full_name']} -> {label}")


# ---- install-self ---- #


@app.command("install-self")
def cmd_install_self(
    target: Path | None = typer.Option(
        None,
        "--target",
        help="Override install root (defaults to all enabled platform skill dirs).",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing files."),
) -> None:
    """Install CC Port's own SKILL.md to all enabled platforms.

    Copies the project's SKILL.md (and any companion .md files at repo root)
    into each platform's skills directory under a ``cc-port/`` subdirectory.
    """
    cfg = _load()
    project_root = _find_project_root()
    skill_md = project_root / "SKILL.md"
    if not skill_md.is_file():
        console.print(f"[red]SKILL.md not found at[/red] {skill_md}")
        raise typer.Exit(1)

    candidates = [skill_md]
    for extra in ("reference.md", "examples.md"):
        p = project_root / extra
        if p.is_file():
            candidates.append(p)

    if target:
        target_dirs = [target.expanduser() / "cc-port"]
    else:
        target_dirs = []
        for plat in cfg.platforms.enabled():
            sp = plat.skills_path()
            if sp:
                target_dirs.append(sp / "cc-port")
        if not target_dirs:
            target_dirs = [cfg.install.target_path / "cc-port"]

    total_copied: list[str] = []
    for dest in target_dirs:
        dest.mkdir(parents=True, exist_ok=True)
        for src in candidates:
            out = dest / src.name
            if out.exists() and not force:
                console.print(f"[yellow]skip[/yellow] {out} (use --force to overwrite)")
                continue
            shutil.copy2(src, out)
            total_copied.append(str(out))

    if total_copied:
        console.print("[green]Installed CC Port skill files:[/green]")
        for p in total_copied:
            console.print(f"  - {p}")
        console.print(
            "\nNext: register the MCP server in your platform's MCP config. Example for Cursor:\n"
            '  ~/.cursor/mcp.json -> {"mcpServers": {"cc-port": {"command": "cc-port-mcp"}}}\n'
            "Example for Claude Code:\n"
            "  claude mcp add cc-port -- cc-port-mcp\n"
            "Then restart your IDE."
        )
    else:
        console.print(
            "[yellow]Nothing copied. SKILL.md already installed on all platforms.[/yellow]"
        )


# ---- platforms ---- #


@app.command("platforms")
def cmd_platforms(
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Show configured platforms and their directories."""
    try:
        cfg = _load()
    except Exception as exc:
        _exit_wire_error(
            json_output=json_output,
            code="platform_list_failed",
            message=f"Platform discovery failed: {exc}",
        )
    if json_output:
        _print_wire_success(
            {
                "profiles": [
                    {
                        "profile_id": plat.name,
                        "tool_id": plat.effective_tool_id,
                        "environment_kind": plat.environment_kind,
                        "environment_name": plat.environment_name,
                        "display_name": plat.effective_display_name,
                        "enabled": plat.enabled,
                    }
                    for plat in cfg.platforms.profiles
                ]
            },
            status="ready",
        )
        return
    table = Table(title="CC Port platforms")
    table.add_column("Platform", style="bold")
    table.add_column("Enabled")
    table.add_column("Skills Dir")
    table.add_column("MCP Config")
    table.add_column("Rules Dir")
    table.add_column("Prompts Dir")
    table.add_column("Plugins Dir")
    for plat in cfg.platforms.profiles:
        table.add_row(
            plat.name,
            "[green]yes[/green]" if plat.enabled else "[dim]no[/dim]",
            plat.skills_dir or "-",
            plat.mcp_json or "-",
            plat.rules_dir or "-",
            plat.prompts_dir or "-",
            plat.plugins_dir or "-",
        )
    console.print(table)


# ---- update ---- #


@app.command("update")
def cmd_update(
    name: str = typer.Argument(...),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Resource type."),
) -> None:
    """Force-sync a single item."""
    cfg = _load()
    registry = load_registry()
    entry = registry.get(name, kind)
    if entry is None:
        console.print(f"[yellow]No item named[/yellow] {name}")
        raise typer.Exit(1)
    result = sync_one(entry, config=cfg)
    color = "green" if result.action is not SyncAction.FAILED else "red"
    console.print(f"[{color}]{result.action.value}[/{color}] {name} -> {result.install_path}")
    if result.platforms_installed:
        console.print(f"  Platforms: {', '.join(result.platforms_installed)}")
    if result.detail:
        console.print(result.detail)


# ---- link / unlink ---- #


@app.command("link")
def cmd_link(
    project: Path = typer.Option(
        ".",
        "--project",
        "-p",
        help="Project root directory (defaults to CWD).",
    ),
    only: list[str] = typer.Option(None, "--only", help="Only link specific items."),
    tags_filter: list[str] = typer.Option(
        None, "--tag", "-t", help="Only link items with these tags."
    ),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Only link items of this type."),
) -> None:
    """Link registry skills into a project for AI auto-discovery.

    Creates .cursor/rules/cc-port-skills.md (Cursor Rule index) and symlinks
    in .cursor/skills/ pointing to globally-installed skill directories.
    AI agents reading the rule file will automatically know which skills
    are available and when to use them.
    """
    from ..services.linker import link

    cfg = _load()
    project_path = project.resolve()

    linked, rule_path = link(
        project_path, cfg, only=only or None, tags=tags_filter or None, kind=kind
    )

    console.print(f"[green]Rule index:[/green] {rule_path}")
    if linked:
        console.print(f"[green]Linked {len(linked)} skill(s):[/green] {', '.join(linked)}")
    else:
        console.print(
            "[yellow]No skill symlinks created (skills may not be installed yet).[/yellow]"
        )
    console.print("\nAI agents in this project will now auto-discover linked skills.")


@app.command("unlink")
def cmd_unlink(
    project: Path = typer.Option(
        ".",
        "--project",
        "-p",
        help="Project root directory (defaults to CWD).",
    ),
) -> None:
    """Remove all CC Port links and the skill index from a project."""
    from ..services.linker import unlink

    project_path = project.resolve()
    removed, rule_removed = unlink(project_path)

    if removed:
        console.print(f"[green]Removed {len(removed)} symlink(s):[/green] {', '.join(removed)}")
    if rule_removed:
        console.print("[green]Removed[/green] cc-port-skills.md rule file")
    if not removed and not rule_removed:
        console.print("[yellow]Nothing to unlink.[/yellow]")


def _find_project_root() -> Path:
    """Locate the CC Port project root by walking up from this file."""
    here = Path(__file__).resolve()
    for candidate in [here.parent, *here.parents]:
        if (candidate / "SKILL.md").is_file() and (candidate / "pyproject.toml").is_file():
            return candidate
    return here.parent.parent


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    app()
