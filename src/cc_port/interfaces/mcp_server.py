"""CC Port MCP server.

Exposes the same operations as the CLI as MCP tools, so an AI coding agent can
publish, register, and sync skills, MCP servers, rules, prompts, plugins,
instructions, and memories.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, Literal, ParamSpec, TypeVar, cast

from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import TextContent
from pydantic import Field

from .. import __version__
from ..agent.contracts import (
    AssetActionPlanWire,
    AssetActionResultWire,
    AssetBatchChoiceWire,
    AssetBatchPlanWire,
    AssetBatchResultWire,
    AssetContentDiffWire,
    AssetInventoryWire,
    DoctorCheckWire,
    DoctorReportWire,
    OperationDetailWire,
    ProfileSummaryWire,
    RegistryRepairChoiceWire,
    RegistryRepairPlanWire,
    RegistryRepairResultWire,
    ServerStatusWire,
    WireEnvelope,
    asset_action_approval_scope,
    asset_action_plan_hash,
    asset_batch_approval_scope,
    asset_batch_operation_id,
    parse_asset_batch_choices,
    registry_repair_approval_scope,
    registry_repair_operation_id,
    to_public_wire_value,
    to_wire_value,
    wire_failure,
    wire_result,
    wire_success,
)
from ..core.config import load_config
from ..core.models import ItemKind
from ..core.platforms import resolve_portable_resource_platforms
from ..core.registry import find_registry_path, load_registry
from ..core.secret_scan import redact_secret_text
from ..core.secrets import redact_item_dump
from ..services import publisher
from ..services.approval import (
    ApprovalRequiredError,
    consume_approval,
    create_approval_request,
    invalidate_approval_request,
    load_approval_request,
)
from ..services.asset_sync import (
    AssetActionPlan,
    AssetBatchChoice,
    AssetBatchPlan,
    apply_asset_action_plan,
    apply_asset_batch_plan,
    build_asset_action_plan,
    build_asset_batch_plan,
    build_asset_content_diff,
    build_asset_inventory,
    load_asset_action_plan,
)
from ..services.doctor import build_doctor_checks
from ..services.installer import check_all, status_all, sync_all, sync_one, uninstall_one
from ..services.local_resources import export_claude_plugin, import_local_resource
from ..services.operation_history import operation_detail as load_operation_detail
from ..services.registry_audit import (
    RegistryRepairChoice,
    RegistryRepairPlan,
    apply_registry_repair,
    build_registry_repair_plan,
)
from ..services.resource_manager import resource_install_plan

_SERVER_INSTRUCTIONS = """\
Use the safe CC Port workflow for automation: call cc_port_status, then
asset_inventory with scan_local=true. Inspect asset_content_diff when content
differs, create an asset_action_plan or asset_batch_plan, review blockers and
warnings, and only then call the matching apply tool with the unchanged
operation_id, plan_hash, and approval_id after the desktop marks that exact
approval approved. MCP exposes no approve or reject tool. Treat platform values
as exact profile ids. A stale result is a new proposal with a new approval,
never approval to continue an old write. Verify every write with asset_inventory
or operation_detail. Use registry_repair_plan before registry_repair_apply.
Never set overwrite, ownership, link-target, visibility, or other confirmation
fields merely to bypass a blocker; obtain explicit user authorization first.
Tools whose title starts with "Legacy" are compatibility surfaces and are not
the default automation path.
"""

mcp = FastMCP(
    "CC Port",
    instructions=_SERVER_INSTRUCTIONS,
    version=__version__,
    strict_input_validation=True,
)

_READ_LOCAL = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
_READ_REMOTE = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_PLAN_REMOTE = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
_APPLY_LOCAL = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
}
_APPLY_REMOTE = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}

_RECOMMENDED_TOOLS = [
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
]
_LEGACY_DIRECT_WRITE_TOOLS = [
    "publish_local_skill",
    "set_skill_visibility",
    "add_external_skill",
    "collect_resource",
    "import_local_resource_tool",
    "export_plugin",
    "add_mcp_server",
    "check_items",
    "remove_skill",
    "sync_skills",
    "update_skill",
]

_P = ParamSpec("_P")
_R = TypeVar("_R")
_NonEmptyString = Annotated[str, Field(min_length=1)]
_PlanHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_ApprovalId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
_AssetBatchOperationId = Annotated[
    str,
    Field(pattern=r"^asset-batch:[0-9a-f]{64}$"),
]
_RegistryRepairOperationId = Annotated[
    str,
    Field(pattern=r"^registry-repair:[0-9a-f]{64}$"),
]
_AssetKind = Literal[
    "skill",
    "mcp",
    "rule",
    "prompt",
    "plugin",
    "instruction",
    "memory",
]
_AssetPlanAction = Literal[
    "download",
    "upload",
    "copy-to-local",
    "copy-to-remote",
    "set-platform-install-name",
]

_ASSET_KINDS = {
    "skill",
    "mcp",
    "rule",
    "prompt",
    "plugin",
    "instruction",
    "memory",
}


def _execution_error(
    tool_name: str,
    exc: Exception,
    *,
    retryable: bool = False,
) -> ToolResult:
    """Return a machine-readable MCP execution error without hiding ``isError``."""
    message = to_public_wire_value(
        redact_secret_text(str(exc) or exc.__class__.__name__)
    )
    if not isinstance(message, str):  # pragma: no cover - strings remain strings
        message = exc.__class__.__name__
    if isinstance(exc, ApprovalRequiredError):
        details: dict[str, Any] = {
            "retryable": False,
            "tool": tool_name,
        }
        if exc.approval_id:
            details["approval_id"] = exc.approval_id
        envelope = wire_failure(
            "approval_required",
            message,
            status="needs-confirmation",
            details=details,
        )
    else:
        envelope = wire_failure(
            f"{tool_name}.failed",
            message,
            details={"retryable": retryable, "tool": tool_name},
        )
    payload = envelope.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=payload,
        is_error=True,
    )


def _tool_boundary(
    tool_name: str,
    *,
    retryable: bool = False,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R | ToolResult]]:
    """Convert service exceptions into MCP error results with a stable envelope."""

    def decorate(function: Callable[_P, _R]) -> Callable[_P, _R | ToolResult]:
        @wraps(function)
        def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R | ToolResult:
            try:
                return function(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - the MCP boundary owns errors
                return _execution_error(tool_name, exc, retryable=retryable)

        return guarded

    return decorate


def _profile_summary(profile: Any) -> dict[str, Any]:
    return {
        "profile_id": profile.name,
        "tool_id": profile.effective_tool_id,
        "environment_kind": profile.environment_kind,
        "environment_name": profile.environment_name,
        "display_name": profile.effective_display_name,
        "enabled": profile.enabled,
    }


def _asset_kind(value: str) -> ItemKind:
    kind = value.strip()
    if kind not in _ASSET_KINDS:
        raise ValueError(f"Unsupported resource kind: {kind}")
    return cast(ItemKind, kind)


def _asset_choice_flag(item: dict[str, Any], key: str, *, index: int) -> bool:
    value = item.get(key, False)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError(f"Asset batch choice {index} field {key} must be a boolean.")
    return value


def _asset_batch_choices(
    values: list[AssetBatchChoiceWire] | None,
) -> list[AssetBatchChoice]:
    parsed_values = _asset_batch_choice_wires(values)
    choices: list[AssetBatchChoice] = []
    for index, parsed in enumerate(parsed_values):
        value = parsed.model_dump(mode="python")
        reference_origin = value.get("reference_origin")
        plugin_dependencies = value.get("plugin_dependencies")
        choices.append(
            AssetBatchChoice(
                resource_key=parsed.resource_key,
                platform=parsed.platform,
                local_instance_id=parsed.local_instance_id,
                resolution=parsed.resolution,
                new_name=parsed.new_name,
                overwrite_unmanaged=_asset_choice_flag(
                    value,
                    "overwrite_unmanaged",
                    index=index,
                ),
                plugin_track=parsed.plugin_track,
                ownership_confirmed=_asset_choice_flag(
                    value,
                    "ownership_confirmed",
                    index=index,
                ),
                link_target_confirmed=_asset_choice_flag(
                    value,
                    "link_target_confirmed",
                    index=index,
                ),
                reference_origin={
                    str(key): str(value) for key, value in reference_origin.items()
                }
                if isinstance(reference_origin, dict)
                else {},
                plugin_dependencies={
                    str(key): str(value) for key, value in plugin_dependencies.items()
                }
                if isinstance(plugin_dependencies, dict)
                else {},
            )
        )
    return choices


def _asset_batch_choice_wires(
    values: list[AssetBatchChoiceWire] | None,
) -> list[AssetBatchChoiceWire]:
    for index, item in enumerate(values or []):
        if isinstance(item, dict):
            resource_key = str(item.get("resource_key") or "").strip()
            if not resource_key:
                raise ValueError(f"Asset batch choice {index} requires resource_key.")
            for key in (
                "overwrite_unmanaged",
                "ownership_confirmed",
                "link_target_confirmed",
            ):
                _asset_choice_flag(item, key, index=index)
    return parse_asset_batch_choices(values)


def _registry_repair_choices(
    values: list[RegistryRepairChoiceWire] | None,
) -> list[RegistryRepairChoice]:
    return [
        RegistryRepairChoice(
            issue_id=item.issue_id,
            action=item.action,
            name=item.name,
        )
        for item in _registry_repair_choice_wires(values)
    ]


def _registry_repair_choice_wires(
    values: list[RegistryRepairChoiceWire] | None,
) -> list[RegistryRepairChoiceWire]:
    return [
        item
        if isinstance(item, RegistryRepairChoiceWire)
        else RegistryRepairChoiceWire.model_validate(item)
        for item in values or []
    ]


def _approval_review_metadata(
    *,
    kind: str,
    operation_id: str,
    plan_hash: str,
    scope: dict[str, Any],
    planned_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a complete, redacted desktop review payload without local paths."""

    payload = to_wire_value(
        {
            "kind": kind,
            "operation_id": operation_id,
            "plan_hash": plan_hash,
            "scope": scope,
            "planned_items": planned_items or [],
        }
    )
    if not isinstance(payload, dict):
        raise TypeError("Approval review metadata must be an object.")
    return cast(dict[str, Any], _redact_private_approval_paths(payload))


def _redact_private_approval_paths(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        is_windows_absolute = (
            len(stripped) >= 3
            and stripped[0].isalpha()
            and stripped[1] == ":"
            and stripped[2] in {"/", "\\"}
        )
        if stripped.startswith(("/", "\\\\", "file://")) or is_windows_absolute:
            return "${PRIVATE_PATH}"
        return value
    if isinstance(value, dict):
        return {
            str(key): _redact_private_approval_paths(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_private_approval_paths(item) for item in value]
    return value


def _action_plan_wire(
    plan: AssetActionPlan,
    *,
    request_approval: bool,
) -> AssetActionPlanWire:
    payload = to_wire_value(plan)
    if not isinstance(payload, dict):
        raise TypeError("Asset action plan service returned a non-object result.")
    plan_hash = asset_action_plan_hash(payload)
    requires_approval = not plan.blocked
    payload.update(
        {
            "plan_hash": plan_hash,
            "requires_approval": requires_approval,
            "approval_id": "",
            "approval_status": "not-required",
            "approval_scope_hash": "",
        }
    )
    if request_approval and requires_approval:
        scope = asset_action_approval_scope(payload)
        request = create_approval_request(
            kind="asset-action",
            operation_id=plan.operation_id,
            plan_hash=plan_hash,
            scope=scope,
            summary=redact_secret_text(
                f"{plan.action} {plan.resource_key} for {plan.platform}"
            ),
            metadata=_approval_review_metadata(
                kind="asset-action",
                operation_id=plan.operation_id,
                plan_hash=plan_hash,
                scope=scope,
            ),
        )
        payload.update(
            {
                "approval_id": request.approval_id,
                "approval_status": request.status,
                "approval_scope_hash": request.scope_hash,
            }
        )
    return AssetActionPlanWire.model_validate(to_public_wire_value(payload))


def _batch_plan_wire(
    plan: AssetBatchPlan,
    *,
    choices: list[AssetBatchChoiceWire],
    request_approval: bool,
) -> AssetBatchPlanWire:
    payload = to_wire_value(plan)
    if not isinstance(payload, dict):
        raise TypeError("Asset batch plan service returned a non-object result.")
    operation_id = asset_batch_operation_id(plan.plan_hash)
    requires_approval = plan.executable_count > 0 and plan.blocked_count == 0
    payload.update(
        {
            "operation_id": operation_id,
            "requires_approval": requires_approval,
            "approval_id": "",
            "approval_status": "not-required",
            "approval_scope_hash": "",
        }
    )
    if request_approval and requires_approval:
        scope = asset_batch_approval_scope(
            direction=plan.direction,
            resource_keys=plan.resource_keys,
            target_platforms=plan.target_platforms,
            choices=choices,
            plan_hash=plan.plan_hash,
        )
        request = create_approval_request(
            kind="asset-batch",
            operation_id=operation_id,
            plan_hash=plan.plan_hash,
            scope=scope,
            summary=redact_secret_text(
                f"{plan.direction} {plan.executable_count} CC Port assets"
            ),
            metadata=_approval_review_metadata(
                kind="asset-batch",
                operation_id=operation_id,
                plan_hash=plan.plan_hash,
                scope=scope,
                planned_items=[
                    {
                        "action": item.action,
                        "resource_key": item.resource_key,
                        "target_resource_key": item.target_resource_key,
                        "profile_id": item.platform,
                        "local_instance_id": item.local_instance_id,
                        "disposition": item.disposition,
                    }
                    for item in plan.items
                ],
            ),
        )
        payload.update(
            {
                "approval_id": request.approval_id,
                "approval_status": request.status,
                "approval_scope_hash": request.scope_hash,
            }
        )
    return AssetBatchPlanWire.model_validate(to_public_wire_value(payload))


def _registry_plan_wire(
    plan: RegistryRepairPlan,
    *,
    choices: list[RegistryRepairChoiceWire],
    request_approval: bool,
) -> RegistryRepairPlanWire:
    payload = to_wire_value(plan)
    if not isinstance(payload, dict):
        raise TypeError("Registry repair plan service returned a non-object result.")
    operation_id = registry_repair_operation_id(plan.plan_hash)
    requires_approval = (
        plan.repairable
        and plan.executable_count > 0
        and plan.blocked_count == 0
        and bool(plan.registry_diff)
    )
    payload.update(
        {
            "operation_id": operation_id,
            "requires_approval": requires_approval,
            "approval_id": "",
            "approval_status": "not-required",
            "approval_scope_hash": "",
        }
    )
    if request_approval and requires_approval:
        scope = registry_repair_approval_scope(
            branch=plan.branch,
            choices=choices,
            plan_hash=plan.plan_hash,
        )
        request = create_approval_request(
            kind="registry-repair",
            operation_id=operation_id,
            plan_hash=plan.plan_hash,
            scope=scope,
            summary=redact_secret_text(f"Repair registry.yaml on {plan.branch}"),
            metadata=_approval_review_metadata(
                kind="registry-repair",
                operation_id=operation_id,
                plan_hash=plan.plan_hash,
                scope=scope,
                planned_items=[
                    {
                        "issue_id": item.id,
                        "resource_key": item.resource_key,
                        "kind": item.kind,
                        "name": item.name,
                        "default_action": item.default_action,
                    }
                    for item in plan.issues
                ],
            ),
        )
        payload.update(
            {
                "approval_id": request.approval_id,
                "approval_status": request.status,
                "approval_scope_hash": request.scope_hash,
            }
        )
    return RegistryRepairPlanWire.model_validate(to_public_wire_value(payload))


def _rebuild_action_plan(
    stored: AssetActionPlan,
    *,
    config: Any,
    persist: bool,
) -> AssetActionPlan:
    return build_asset_action_plan(
        stored.action,
        kind=stored.kind,
        name=stored.name,
        platform=stored.platform,
        local_instance_id=stored.local_instance_id,
        new_name=stored.new_name,
        new_install_name=stored.new_install_name,
        overwrite_unmanaged=stored.overwrite_unmanaged,
        link_target_confirmed=stored.link_target_confirmed,
        config=config,
        _persist=persist,
    )


def _is_stale_status(status: str) -> bool:
    return status == "stale" or status.startswith("stale-")


def _portable_resource_platforms(
    kind: str,
    platforms: list[str] | None,
) -> list[str] | None:
    """Resolve local profile ids to tool ids before writing portable metadata."""
    cfg = load_config()
    portable = resolve_portable_resource_platforms(cfg.platforms, kind, platforms)
    return portable or None


@mcp.tool(
    title="CC Port automation status",
    annotations=_READ_LOCAL,
    meta={"cc_port": {"preferred": True, "phase": "discover"}},
)
@_tool_boundary("cc_port_status")
def cc_port_status() -> WireEnvelope[ServerStatusWire]:
    """Discover the safe automation workflow and configured profile identities."""
    config = load_config()
    data = ServerStatusWire(
        version=__version__,
        recommended_tools=list(_RECOMMENDED_TOOLS),
        legacy_direct_write_tools=list(_LEGACY_DIRECT_WRITE_TOOLS),
        profiles=[
            ProfileSummaryWire.model_validate(_profile_summary(profile))
            for profile in config.platforms.profiles
        ],
    )
    return wire_success(data, status="ready")


@mcp.tool(
    title="CC Port doctor",
    annotations=_READ_LOCAL,
    meta={"cc_port": {"preferred": True, "phase": "diagnose"}},
)
@_tool_boundary("cc_port_doctor")
def cc_port_doctor() -> WireEnvelope[DoctorReportWire]:
    """Run non-mutating environment checks and return a typed health report."""
    checks = build_doctor_checks(load_config())
    wire_checks: list[DoctorCheckWire] = []
    counts = {"ok": 0, "warning": 0, "error": 0, "skipped": 0}
    for check in checks:
        status = str(check.get("status") or "error")
        if status not in counts:
            status = "error"
        counts[status] += 1
        profile = check.get("profile")
        public_check = to_public_wire_value(
            {
                "id": str(check.get("id") or "unknown"),
                "label": str(check.get("label") or "Unknown check"),
                "status": status,
                "ok": bool(check.get("ok")),
                "detail": str(check.get("detail") or ""),
                "profile_id": str(getattr(profile, "name", "")),
            }
        )
        if not isinstance(public_check, dict):  # pragma: no cover - fixed mapping
            raise TypeError("Doctor check projection must be an object.")
        wire_checks.append(
            DoctorCheckWire.model_validate(public_check)
        )
    report_status: Literal["ok", "warning", "error"] = (
        "error"
        if counts["error"]
        else "warning"
        if counts["warning"]
        else "ok"
    )
    data = DoctorReportWire(
        status=report_status,
        ok_count=counts["ok"],
        warning_count=counts["warning"],
        error_count=counts["error"],
        skipped_count=counts["skipped"],
        checks=wire_checks,
    )
    return wire_success(data, status="ready")


@mcp.tool(
    title="List registered resources (compatibility read)",
    annotations=_READ_LOCAL,
    meta={"cc_port": {"preferred": False, "stability": "compatibility"}},
)
def list_items(kind: str | None = None) -> dict[str, Any]:
    """List all items registered in registry.yaml.

    Args:
        kind: Optional filter: "skill", "mcp", "rule", "prompt", "plugin",
            "instruction", or "memory".
    """
    cfg = load_config()
    registry = load_registry()
    install_root = cfg.install.target_path
    items = registry.items
    if kind:
        items = [i for i in items if i.kind == kind]
    return {
        "registry_path": str(find_registry_path()),
        "install_target": str(install_root),
        "platforms": [
            {
                "name": p.name,
                "tool_id": p.effective_tool_id,
                "environment_kind": p.environment_kind,
                "environment_name": p.environment_name,
                "display_name": p.effective_display_name,
                "home_dir": p.home_dir,
                "enabled": p.enabled,
                "skills_dir": p.skills_dir,
                "mcp_json": p.mcp_json,
                "rules_dir": p.rules_dir,
                "prompts_dir": p.prompts_dir,
                "plugins_dir": p.plugins_dir,
                "instructions_path": p.instructions_path,
                "memories_dir": p.memories_dir,
                "memory_layout": p.memory_layout,
                "settings_path": p.settings_path,
            }
            for p in cfg.platforms.profiles
        ],
        "items": [
            redact_item_dump(
                {
                    **s.model_dump(),
                    "installed": (install_root / s.install_target_name()).exists(),
                }
            )
            for s in items
        ],
    }


@mcp.tool(
    title="List skills (compatibility alias)",
    annotations=_READ_LOCAL,
    meta={"cc_port": {"preferred": False, "stability": "compatibility"}},
)
def list_skills() -> dict[str, Any]:
    """List all skills currently registered in registry.yaml (backward-compatible alias)."""
    return list_items(kind="skill")


@mcp.tool(
    title="List exact CC Port profiles (compatibility read)",
    annotations=_READ_LOCAL,
    meta={"cc_port": {"preferred": False, "stability": "compatibility"}},
)
def list_platforms() -> dict[str, Any]:
    """Show all configured platforms and their installation directories."""
    cfg = load_config()
    return {
        "platforms": [
            {
                "name": p.name,
                "tool_id": p.effective_tool_id,
                "environment_kind": p.environment_kind,
                "environment_name": p.environment_name,
                "display_name": p.effective_display_name,
                "home_dir": p.home_dir,
                "enabled": p.enabled,
                "skills_dir": p.skills_dir,
                "mcp_json": p.mcp_json,
                "rules_dir": p.rules_dir,
                "prompts_dir": p.prompts_dir,
                "plugins_dir": p.plugins_dir,
                "instructions_path": p.instructions_path,
                "memories_dir": p.memories_dir,
                "memory_layout": p.memory_layout,
                "settings_path": p.settings_path,
            }
            for p in cfg.platforms.profiles
        ],
    }


@mcp.tool(
    title="Build profile-aware asset inventory",
    annotations=_READ_REMOTE,
    meta={"cc_port": {"preferred": True, "phase": "discover"}},
)
@_tool_boundary("asset_inventory", retryable=True)
def asset_inventory(
    scan_local: bool = False,
    refresh_remote: bool = True,
    scan_global: bool = True,
    project_ids: list[str] | None = None,
) -> WireEnvelope[AssetInventoryWire]:
    """Build the logical asset inventory used by the CLI and desktop app.

    Local instances retain their exact configured profile id in ``platform``;
    tool and Windows/WSL environment identities are returned as separate fields.
    Internal platform-comparison rows are intentionally omitted from this stable
    interface.

    Args:
        scan_local: Discover resources in configured and detected tool profiles.
        refresh_remote: Fetch the configured resource-repository branch first.
        scan_global: Include global locations when local discovery is enabled.
        project_ids: Optional saved project ids to include in local discovery.
    """
    inventory = build_asset_inventory(
        config=load_config(),
        scan_local=scan_local,
        refresh_remote=refresh_remote,
        scan_global=scan_global,
        project_ids=project_ids,
    )
    payload = to_public_wire_value(inventory)
    if not isinstance(payload, dict):
        raise TypeError("Asset inventory service returned a non-object result.")
    payload.pop("rows", None)
    return wire_success(AssetInventoryWire.model_validate(payload), status="ready")


@mcp.tool(
    title="Compare remote and one local asset instance",
    annotations=_READ_LOCAL,
    meta={"cc_port": {"preferred": True, "phase": "inspect"}},
)
@_tool_boundary("asset_content_diff")
def asset_content_diff(
    resource_key: _NonEmptyString,
    local_instance_id: _NonEmptyString,
) -> WireEnvelope[AssetContentDiffWire]:
    """Build a bounded read-only diff for one exact inventory instance."""
    value = build_asset_content_diff(
        resource_key,
        local_instance_id,
        config=load_config(),
    )
    data = AssetContentDiffWire.model_validate(to_public_wire_value(value))
    return wire_success(data, status="ready")


@mcp.tool(
    title="Plan one revalidated asset action",
    annotations=_PLAN_REMOTE,
    meta={"cc_port": {"preferred": True, "phase": "plan"}},
)
@_tool_boundary("asset_action_plan", retryable=True)
def asset_action_plan(
    action: _AssetPlanAction,
    kind: _AssetKind,
    name: _NonEmptyString,
    platform: _NonEmptyString,
    local_instance_id: str = "",
    new_name: str = "",
    new_install_name: str = "",
    overwrite_unmanaged: bool = False,
    link_target_confirmed: bool = False,
) -> WireEnvelope[AssetActionPlanWire]:
    """Persist one revalidatable asset action plan without applying it.

    ``platform`` is the stable profile id, not a tool id. Use ``cc_port_status``
    or ``asset_inventory`` to select it. The returned ``operation_id`` must be
    passed unchanged to ``asset_action_apply``.

    Args:
        action: download, upload, copy-to-local, copy-to-remote, or
            set-platform-install-name.
        kind: Resource kind.
        name: Logical resource name.
        platform: Exact target/source profile id, including Windows or WSL identity.
        local_instance_id: Exact inventory instance id when multiple sources exist.
        new_name: New logical name for a copy action.
        new_install_name: Profile-local alias for set-platform-install-name.
        overwrite_unmanaged: Explicit confirmation to replace an unmanaged target.
        link_target_confirmed: Explicit confirmation for a non-standard root link target.
    """
    plan = build_asset_action_plan(
        action,
        kind=_asset_kind(kind),
        name=name,
        platform=platform,
        local_instance_id=local_instance_id,
        new_name=new_name,
        new_install_name=new_install_name,
        overwrite_unmanaged=overwrite_unmanaged,
        link_target_confirmed=link_target_confirmed,
        config=load_config(),
    )
    data = _action_plan_wire(plan, request_approval=True)
    if data.blocked:
        message = "; ".join(data.blockers) or "The asset action plan is blocked."
        return wire_failure("blocked", message, status="blocked", data=data)
    return wire_success(data, status="planned")


@mcp.tool(
    title="Apply one persisted asset action",
    annotations=_APPLY_REMOTE,
    meta={"cc_port": {"preferred": True, "phase": "apply"}},
)
@_tool_boundary("asset_action_apply")
def asset_action_apply(
    operation_id: _NonEmptyString,
    plan_hash: _PlanHash,
    approval_id: _ApprovalId,
) -> WireEnvelope[AssetActionResultWire]:
    """Consume an exact human approval, then revalidate and apply one action."""
    config = load_config()
    stored = load_asset_action_plan(operation_id, config=config)
    stored_hash = asset_action_plan_hash(stored)
    if stored_hash != plan_hash:
        raise ValueError("The supplied plan hash does not match the stored action plan.")

    current = _rebuild_action_plan(stored, config=config, persist=False)
    if asset_action_plan_hash(current) != plan_hash:
        invalidate_approval_request(
            approval_id,
            kind="asset-action",
            operation_id=operation_id,
            plan_hash=plan_hash,
            scope=asset_action_approval_scope(stored),
        )
        replacement = _rebuild_action_plan(stored, config=config, persist=True)
        replacement_wire = _action_plan_wire(replacement, request_approval=True)
        data = AssetActionResultWire(
            operation_id=operation_id,
            plan_hash=plan_hash,
            action=stored.action,
            status="stale-plan",
            resource_key=stored.resource_key,
            target_resource_key=stored.target_resource_key,
            platform=stored.platform,
            message="The action state changed; review and approve the replacement plan.",
            stale_plan=replacement_wire,
        )
        return wire_failure(
            "stale_plan",
            data.message,
            status="stale-plan",
            data=data,
        )

    consume_approval(
        approval_id,
        kind="asset-action",
        operation_id=operation_id,
        plan_hash=plan_hash,
        scope=asset_action_approval_scope(current),
    )
    result = apply_asset_action_plan(operation_id, config=config)
    payload = to_public_wire_value(result)
    if not isinstance(payload, dict):
        raise TypeError("Asset action apply service returned a non-object result.")
    payload.update(
        {
            "plan_hash": plan_hash,
            "approval_id": approval_id,
            "approval_status": "consumed",
        }
    )
    if _is_stale_status(result.status):
        replacement = _rebuild_action_plan(stored, config=config, persist=True)
        payload["stale_plan"] = _action_plan_wire(
            replacement,
            request_approval=True,
        ).model_dump(mode="json")
        payload["status"] = "stale-plan"
    data = AssetActionResultWire.model_validate(payload)
    status = "stale-plan" if _is_stale_status(data.status) else data.status
    return wire_result(
        data,
        status=status,
        message=data.message or "The asset action did not complete.",
    )


@mcp.tool(
    title="Plan a revalidated asset batch",
    annotations=_PLAN_REMOTE,
    meta={"cc_port": {"preferred": True, "phase": "plan"}},
)
@_tool_boundary("asset_batch_plan", retryable=True)
def asset_batch_plan(
    direction: Literal["upload", "download"],
    resource_keys: list[_NonEmptyString],
    target_platforms: list[str] | None = None,
    choices: list[AssetBatchChoiceWire] | None = None,
) -> WireEnvelope[AssetBatchPlanWire]:
    """Build a stateless upload or download plan for logical assets.

    Target platforms are exact profile ids. Choices may select a profile and
    local instance, request overwrite/rename, confirm unmanaged or linked
    targets, and carry plugin reference decisions. The returned ``plan_hash``
    binds these inputs to the freshly scanned local and remote identities.
    """
    wire_choices = _asset_batch_choice_wires(choices)
    plan = build_asset_batch_plan(
        direction,
        resource_keys=resource_keys,
        target_platforms=target_platforms,
        choices=_asset_batch_choices(wire_choices),
        config=load_config(),
    )
    data = _batch_plan_wire(
        plan,
        choices=wire_choices,
        request_approval=True,
    )
    return wire_success(data, status="planned")


@mcp.tool(
    title="Apply a hash-bound asset batch",
    annotations=_APPLY_REMOTE,
    meta={"cc_port": {"preferred": True, "phase": "apply"}},
)
@_tool_boundary("asset_batch_apply")
def asset_batch_apply(
    direction: Literal["upload", "download"],
    resource_keys: list[_NonEmptyString],
    plan_hash: _PlanHash,
    operation_id: _AssetBatchOperationId,
    approval_id: _ApprovalId,
    target_platforms: list[str] | None = None,
    choices: list[AssetBatchChoiceWire] | None = None,
) -> WireEnvelope[AssetBatchResultWire]:
    """Rebuild and apply a batch plan only if its plan hash is still current.

    Pass the same direction, resource keys, exact profile ids, and choices used
    for ``asset_batch_plan``. A changed identity or state returns ``stale-plan``
    and the newly structured plan without applying the old plan.
    """
    expected_operation_id = asset_batch_operation_id(plan_hash)
    if operation_id != expected_operation_id:
        raise ValueError("The operation id does not match the supplied batch plan hash.")
    config = load_config()
    wire_choices = _asset_batch_choice_wires(choices)
    service_choices = _asset_batch_choices(wire_choices)
    current = build_asset_batch_plan(
        direction,
        resource_keys=resource_keys,
        target_platforms=target_platforms,
        choices=service_choices,
        config=config,
    )
    if current.plan_hash != plan_hash:
        invalidate_approval_request(
            approval_id,
            kind="asset-batch",
            operation_id=operation_id,
            plan_hash=plan_hash,
            scope=asset_batch_approval_scope(
                direction=direction,
                resource_keys=resource_keys,
                target_platforms=target_platforms,
                choices=wire_choices,
                plan_hash=plan_hash,
            ),
        )
        replacement = _batch_plan_wire(
            current,
            choices=wire_choices,
            request_approval=True,
        )
        data = AssetBatchResultWire(
            operation_id=operation_id,
            status="stale-plan",
            plan_hash=current.plan_hash,
            results=[],
            stale_plan=replacement,
        )
        return wire_failure(
            "stale_plan",
            "The batch state changed; review and approve the replacement plan.",
            status="stale-plan",
            data=data,
        )
    if current.blocked_count:
        data = AssetBatchResultWire(
            operation_id=operation_id,
            status="blocked",
            plan_hash=current.plan_hash,
            results=[],
        )
        return wire_failure(
            "blocked",
            "The batch plan has blocked items and cannot be applied.",
            status="blocked",
            data=data,
        )
    if current.executable_count == 0:
        return wire_success(
            AssetBatchResultWire(
                operation_id=operation_id,
                status="unchanged",
                plan_hash=current.plan_hash,
                results=[],
            ),
            status="unchanged",
        )

    scope = asset_batch_approval_scope(
        direction=current.direction,
        resource_keys=current.resource_keys,
        target_platforms=current.target_platforms,
        choices=wire_choices,
        plan_hash=current.plan_hash,
    )
    consume_approval(
        approval_id,
        kind="asset-batch",
        operation_id=operation_id,
        plan_hash=plan_hash,
        scope=scope,
    )
    result = apply_asset_batch_plan(
        direction,
        resource_keys=resource_keys,
        target_platforms=target_platforms,
        choices=service_choices,
        expected_plan_hash=plan_hash,
        config=config,
    )
    payload = to_public_wire_value(result)
    if not isinstance(payload, dict):
        raise TypeError("Asset batch apply service returned a non-object result.")
    payload.update(
        {
            "operation_id": operation_id,
            "approval_id": approval_id,
            "approval_status": "consumed",
        }
    )
    if result.stale_plan is not None:
        payload["stale_plan"] = _batch_plan_wire(
            result.stale_plan,
            choices=wire_choices,
            request_approval=True,
        ).model_dump(mode="json")
    data = AssetBatchResultWire.model_validate(payload)
    status = "stale-plan" if data.status == "stale" else data.status
    message = (
        "The batch plan is stale; review the returned replacement plan."
        if status == "stale-plan"
        else "The batch operation did not complete."
    )
    return wire_result(data, status=status, message=message)


@mcp.tool(
    title="Plan registry.yaml repair",
    annotations=_PLAN_REMOTE,
    meta={"cc_port": {"preferred": True, "phase": "plan"}},
)
@_tool_boundary("registry_repair_plan", retryable=True)
def registry_repair_plan(
    choices: list[RegistryRepairChoiceWire] | None = None,
) -> WireEnvelope[RegistryRepairPlanWire]:
    """Fetch, audit, and plan a registry-only repair without modifying remote state."""
    wire_choices = _registry_repair_choice_wires(choices)
    value = build_registry_repair_plan(
        config=load_config(),
        choices=_registry_repair_choices(wire_choices),
    )
    data = _registry_plan_wire(
        value,
        choices=wire_choices,
        request_approval=True,
    )
    return wire_success(data, status="planned")


@mcp.tool(
    title="Apply hash-bound registry.yaml repair",
    annotations=_APPLY_REMOTE,
    meta={"cc_port": {"preferred": True, "phase": "apply"}},
)
@_tool_boundary("registry_repair_apply")
def registry_repair_apply(
    plan_hash: _PlanHash,
    operation_id: _RegistryRepairOperationId,
    approval_id: _ApprovalId,
    choices: list[RegistryRepairChoiceWire] | None = None,
) -> WireEnvelope[RegistryRepairResultWire]:
    """Consume an exact human approval, then apply a current Registry repair."""
    expected_operation_id = registry_repair_operation_id(plan_hash)
    if operation_id != expected_operation_id:
        raise ValueError("The operation id does not match the Registry plan hash.")
    config = load_config()
    wire_choices = _registry_repair_choice_wires(choices)
    service_choices = _registry_repair_choices(wire_choices)
    current = build_registry_repair_plan(
        config=config,
        choices=service_choices,
    )
    if current.plan_hash != plan_hash:
        reviewed = load_approval_request(approval_id)
        reviewed_scope = reviewed.metadata.get("scope")
        reviewed_branch = (
            reviewed_scope.get("branch")
            if isinstance(reviewed_scope, dict)
            else None
        )
        if not isinstance(reviewed_branch, str) or not reviewed_branch.strip():
            raise ValueError("The Registry approval has no reviewed branch binding.")
        invalidate_approval_request(
            approval_id,
            kind="registry-repair",
            operation_id=operation_id,
            plan_hash=plan_hash,
            scope=registry_repair_approval_scope(
                branch=reviewed_branch,
                choices=wire_choices,
                plan_hash=plan_hash,
            ),
        )
        replacement = _registry_plan_wire(
            current,
            choices=wire_choices,
            request_approval=True,
        )
        data = RegistryRepairResultWire(
            operation_id=operation_id,
            status="stale-plan",
            plan_hash=current.plan_hash,
            message="The Registry state changed; review the replacement plan.",
            stale_plan=replacement,
        )
        return wire_failure(
            "stale_plan",
            data.message,
            status="stale-plan",
            data=data,
        )
    if not current.repairable or current.blocked_count:
        data = RegistryRepairResultWire(
            operation_id=operation_id,
            status="blocked",
            plan_hash=current.plan_hash,
            message="The Registry repair plan is blocked.",
        )
        return wire_failure(
            "blocked",
            data.message,
            status="blocked",
            data=data,
        )
    if current.executable_count == 0 or not current.registry_diff:
        return wire_success(
            RegistryRepairResultWire(
                operation_id=operation_id,
                status="unchanged",
                plan_hash=current.plan_hash,
                remote_commit=current.remote_commit,
                message="registry.yaml already matches the selected state.",
            ),
            status="unchanged",
        )

    scope = registry_repair_approval_scope(
        branch=current.branch,
        choices=wire_choices,
        plan_hash=current.plan_hash,
    )
    consume_approval(
        approval_id,
        kind="registry-repair",
        operation_id=operation_id,
        plan_hash=plan_hash,
        scope=scope,
    )
    value = apply_registry_repair(
        expected_plan_hash=plan_hash,
        config=config,
        choices=service_choices,
    )
    payload = to_public_wire_value(value)
    if not isinstance(payload, dict):
        raise TypeError("Registry repair apply service returned a non-object result.")
    payload.update(
        {
            "operation_id": operation_id,
            "approval_id": approval_id,
            "approval_status": "consumed",
        }
    )
    if value.stale_plan is not None:
        payload["stale_plan"] = _registry_plan_wire(
            value.stale_plan,
            choices=wire_choices,
            request_approval=True,
        ).model_dump(mode="json")
    data = RegistryRepairResultWire.model_validate(payload)
    status = "stale-plan" if data.status == "stale" else data.status
    return wire_result(
        data,
        status=status,
        message=data.message or "The registry repair did not complete.",
    )


@mcp.tool(
    title="Read operation detail",
    annotations=_READ_LOCAL,
    meta={"cc_port": {"preferred": True, "phase": "verify"}},
)
@_tool_boundary("operation_detail")
def operation_detail(
    operation_id: _NonEmptyString,
) -> WireEnvelope[OperationDetailWire]:
    """Read one persisted operation and its verification targets."""
    value = load_operation_detail(operation_id)
    data = OperationDetailWire.model_validate(to_public_wire_value(value))
    return wire_success(data, status="ready")


@mcp.tool(
    title="Legacy direct write: publish dedicated repository",
    annotations=_APPLY_REMOTE,
    meta={
        "cc_port": {
            "preferred": False,
            "stability": "legacy-direct-write",
            "replacement": "asset_action_plan -> asset_action_apply",
        }
    },
)
def publish_local_skill(
    path: str,
    name: str | None = None,
    description: str | None = None,
    private: bool | None = None,
    update_visibility: bool = False,
    kind: str = "skill",
    mcp_config: dict[str, Any] | None = None,
    platforms: list[str] | None = None,
) -> dict[str, Any]:
    """Legacy direct-write compatibility tool.

    Validate a local directory, create a dedicated GitHub repository for it
    under the configured owner, push the contents, and record it in registry.yaml.

    Args:
        path: Absolute or user-relative path to the directory.
        name: Optional override for the name (defaults to SKILL.md frontmatter for skills).
        description: Optional override for the description.
        private: Repo visibility. True = private, False = public, None = use the
            user's configured default. ALWAYS confirm this with the user before
            calling unless they have explicitly stated their preference.
        update_visibility: If the GitHub repo already exists with a different
            visibility, set this to True to flip it.
        kind: Dedicated-repository resource type: "skill", "mcp", "rule",
            "prompt", or "plugin". Personal instructions and memories use the
            private resource-repository upload workflow.
        mcp_config: MCP server configuration dict (required when kind="mcp").
        platforms: Optional installation platform allowlist.
    """
    cfg = load_config()
    try:
        result = publisher.publish_local_skill(
            Path(path),
            config=cfg,
            name=name,
            description=description,
            private=private,
            update_visibility=update_visibility,
            kind=kind,
            mcp_config=mcp_config,
            platforms=_portable_resource_platforms(kind, platforms),
        )
    except publisher.VisibilityMismatchError as exc:
        return {
            "error": "visibility_mismatch",
            "message": str(exc),
            "full_name": exc.full_name,
            "current_private": exc.current_private,
            "requested_private": exc.requested_private,
            "hint": "Re-run with update_visibility=True to change it.",
        }
    except ValueError as exc:
        return {"error": str(exc)}
    return {
        "name": result.name,
        "kind": kind,
        "repo_url": result.repo_url,
        "full_name": result.full_name,
        "created_repo": result.created,
        "pushed": result.pushed,
        "private": result.private,
        "visibility_changed": result.visibility_changed,
        "entry": redact_item_dump(result.entry.model_dump()),
    }


@mcp.tool(
    title="Legacy direct write: set repository visibility",
    annotations=_APPLY_REMOTE,
    meta={"cc_port": {"preferred": False, "stability": "legacy-direct-write"}},
)
def set_skill_visibility(name: str, private: bool) -> dict[str, Any]:
    """Change the GitHub visibility of an ``owned`` repository.

    Args:
        name: Name of an ``owned`` item in the registry.
        private: True = make the repo private, False = make it public.
    """
    cfg = load_config()
    try:
        return publisher.set_skill_visibility(name, config=cfg, private=private)
    except ValueError as exc:
        return {"error": str(exc)}


@mcp.tool(
    title="Legacy direct write: register external resource",
    annotations=_APPLY_REMOTE,
    meta={
        "cc_port": {
            "preferred": False,
            "stability": "legacy-direct-write",
            "replacement": "asset_action_plan -> asset_action_apply",
        }
    },
)
def add_external_skill(
    github_url: str,
    name: str | None = None,
    subdir: str | None = None,
    ref: str = "main",
    description: str = "",
    kind: str = "skill",
    mcp_config: dict[str, Any] | None = None,
    skip_verify: bool = False,
    tags: list[str] | None = None,
    category: str = "",
    platforms: list[str] | None = None,
) -> dict[str, Any]:
    """Register a third-party resource in registry.yaml.

    Branch and tag refs are always resolved to a complete commit SHA before
    writing. ``skip_verify=True`` only allows an already complete SHA to be
    recorded without an online probe.

    Args:
        github_url: HTTPS or SSH URL of the upstream repo.
        name: Optional explicit name (defaults inferred from URL or subdir).
        subdir: Optional path inside the repo where the resource lives.
        ref: Branch/tag/commit to track.
        description: Optional human description.
        kind: Resource type: "skill", "mcp", "rule", "prompt", "plugin",
            "instruction", or "memory".
        mcp_config: MCP server configuration dict (for kind="mcp").
        skip_verify: Allow an already complete SHA without an online probe.
        tags: Optional tags for selective sync and discovery.
        category: Optional category label.
        platforms: Optional installation platform allowlist.
    """
    cfg = load_config()
    try:
        entry = publisher.add_external_skill(
            github_url,
            name=name,
            subdir=subdir,
            ref=ref,
            description=description,
            kind=kind,
            mcp_config=mcp_config,
            skip_verify=skip_verify,
            token=cfg.github.token or None,
            tags=tags,
            category=category,
            platforms=_portable_resource_platforms(kind, platforms),
        )
    except publisher.RepoUnreachableError as exc:
        return {"error": "repo_unreachable", "message": str(exc)}
    except publisher.UnsafeMcpConfigError as exc:
        return {"error": "unsafe_mcp_config", "message": str(exc)}
    except ValueError as exc:
        return {"error": str(exc)}
    return redact_item_dump(entry.model_dump())


@mcp.tool(
    title="Legacy direct write: collect external resource",
    annotations=_APPLY_REMOTE,
    meta={"cc_port": {"preferred": False, "stability": "legacy-direct-write"}},
)
def collect_resource(
    github_url: str,
    name: str | None = None,
    subdir: str | None = None,
    ref: str = "main",
    description: str = "",
    kind: str = "skill",
    mcp_config: dict[str, Any] | None = None,
    skip_verify: bool = False,
    tags: list[str] | None = None,
    category: str = "",
    platforms: list[str] | None = None,
) -> dict[str, Any]:
    """Collect a third-party resource as an immutable upstream reference."""
    return add_external_skill(
        github_url=github_url,
        name=name,
        subdir=subdir,
        ref=ref,
        description=description,
        kind=kind,
        mcp_config=mcp_config,
        skip_verify=skip_verify,
        tags=tags,
        category=category,
        platforms=platforms,
    )


@mcp.tool(
    title="Legacy direct write: import local resource",
    annotations=_APPLY_LOCAL,
    meta={
        "cc_port": {
            "preferred": False,
            "stability": "legacy-direct-write",
            "replacement": "asset_action_plan -> asset_action_apply",
        }
    },
)
def import_local_resource_tool(
    path: str,
    name: str | None = None,
    description: str | None = None,
    kind: str = "skill",
    category: str = "",
    tags: list[str] | None = None,
    platforms: list[str] | None = None,
    overwrite: bool = False,
    mcp_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy a local resource into this CC Port repository and register it."""
    try:
        portable_platforms = _portable_resource_platforms(kind, platforms)
        result = import_local_resource(
            Path(path),
            kind=kind,
            name=name,
            description=description,
            category=category,
            tags=tags,
            platforms=portable_platforms,
            overwrite=overwrite,
            mcp_config=mcp_config,
        )
    except Exception as exc:  # noqa: BLE001 - MCP tools return errors as data
        return {"error": str(exc)}
    return {
        "entry": redact_item_dump(result.entry.model_dump()),
        "source_path": str(result.source_path),
        "stored_path": str(result.stored_path),
    }


@mcp.tool(
    title="Legacy direct write: export Claude plugin manifest",
    annotations=_APPLY_LOCAL,
    meta={"cc_port": {"preferred": False, "stability": "legacy-direct-write"}},
)
def export_plugin(name: str | None = None) -> dict[str, Any]:
    """Generate .claude-plugin/plugin.json for local skills in this repository."""
    try:
        path = export_claude_plugin(plugin_name=name)
    except Exception as exc:  # noqa: BLE001 - MCP tools return errors as data
        return {"error": str(exc)}
    return {"path": str(path)}


@mcp.tool(
    title="Legacy direct write: register MCP server",
    annotations=_APPLY_REMOTE,
    meta={"cc_port": {"preferred": False, "stability": "legacy-direct-write"}},
)
def add_mcp_server(
    name: str,
    github_url: str,
    command: str | None = None,
    args: list[str] | None = None,
    url: str | None = None,
    env: dict[str, str] | None = None,
    subdir: str | None = None,
    ref: str = "main",
    description: str = "",
    skip_verify: bool = False,
) -> dict[str, Any]:
    """Register an MCP server in the registry (convenience wrapper).

    Either ``command`` (stdio) or ``url`` (http) must be provided.

    Args:
        name: Name for the MCP server entry.
        github_url: GitHub repository containing the MCP server.
        command: Command to start the server (stdio transport).
        args: Arguments for the command.
        url: HTTP URL for the server (http transport).
        env: Environment variables for the server.
        subdir: Subdirectory in the repo.
        ref: Branch/tag to track.
        description: Human description.
        skip_verify: Allow an already complete SHA without an online probe.
    """
    cfg = load_config()
    mcp_config: dict[str, Any] = {}
    if command:
        mcp_config["command"] = command
        if args:
            mcp_config["args"] = args
    elif url:
        mcp_config["type"] = "http"
        mcp_config["url"] = url
    else:
        return {"error": "Either 'command' or 'url' must be provided."}

    if env:
        mcp_config["env"] = env

    try:
        entry = publisher.add_external_skill(
            github_url,
            name=name,
            subdir=subdir,
            ref=ref,
            description=description,
            kind="mcp",
            mcp_config=mcp_config,
            skip_verify=skip_verify,
            token=cfg.github.token or None,
        )
    except publisher.RepoUnreachableError as exc:
        return {"error": "repo_unreachable", "message": str(exc)}
    return redact_item_dump(entry.model_dump())


@mcp.tool(
    title="Legacy mixed check/prune operation",
    annotations=_APPLY_REMOTE,
    meta={"cc_port": {"preferred": False, "stability": "legacy-direct-write"}},
)
def check_items(
    kind: str | None = None,
    prune: bool = False,
    uninstall: bool = False,
) -> dict[str, Any]:
    """Check reachability of all registered repositories.

    Reports which items point to repos that no longer exist.
    Set ``prune=True`` to automatically remove dead entries.

    Args:
        kind: Optional filter by resource type ("skill", "mcp", "rule").
        prune: Remove unreachable items from the registry.
        uninstall: Also delete local files when pruning.
    """
    cfg = load_config()
    results, pruned = check_all(
        config=cfg, kind=kind, prune=prune, uninstall=uninstall,
    )
    return {
        "items": [
            {"name": r.name, "kind": r.kind, "repo": r.repo, "reachable": r.reachable}
            for r in results
        ],
        "pruned": pruned,
    }


@mcp.tool(
    title="Legacy direct write: remove registered resource",
    annotations=_APPLY_LOCAL,
    meta={"cc_port": {"preferred": False, "stability": "legacy-direct-write"}},
)
def remove_skill(
    name: str,
    uninstall: bool = False,
    kind: str | None = None,
) -> dict[str, Any]:
    """Remove an item from the registry. Optionally also delete its local installation."""
    cfg = load_config()
    registry = load_registry()
    entry = registry.get(name, kind)
    removed = publisher.remove_skill(name, kind=kind)
    detail: dict[str, Any] = {
        "removed": redact_item_dump(removed.model_dump()) if removed else None,
        "uninstalled": False,
    }
    if uninstall and entry is not None:
        detail["uninstalled"] = uninstall_one(entry, config=cfg)
    return detail


@mcp.tool(
    title="Legacy direct write: sync registered resources",
    annotations=_APPLY_REMOTE,
    meta={
        "cc_port": {
            "preferred": False,
            "stability": "legacy-direct-write",
            "replacement": "asset_batch_plan -> asset_batch_apply",
        }
    },
)
def sync_skills(
    only: list[str] | None = None,
    kind: str | None = None,
    tags: list[str] | None = None,
    include_optional: bool = False,
    include_kinds: list[str] | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    """Install or update items in the registry across all enabled platforms.

    Args:
        only: Optional list of item names to restrict the sync to.
        kind: Optional filter by resource type ("skill", "mcp", "rule").
        tags: Optional tag filter for selective restore.
        include_optional: Sync all optional kinds too.
        include_kinds: Optional resource kinds to sync in addition to skills.
        platform: Optional: only sync to this specific platform.
    """
    cfg = load_config()
    results = sync_all(
        config=cfg,
        only=only,
        kind=kind,
        tags=tags,
        include_optional=include_optional,
        include_kinds=set(include_kinds or []),
        platform_filter=platform,
    )
    return {
        "install_target": str(cfg.install.target_path),
        "results": [
            {
                "name": r.name,
                "action": r.action.value,
                "install_path": str(r.install_path),
                "platforms": r.platforms_installed,
                "detail": r.detail,
            }
            for r in results
        ],
    }


@mcp.tool(
    title="Legacy resource install plan",
    annotations=_READ_LOCAL,
    meta={"cc_port": {"preferred": False, "stability": "compatibility"}},
)
def plan_resource_install(
    name: str,
    platform: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Build an install plan for one registered resource without writing files."""
    try:
        plan = resource_install_plan(
            name,
            kind=kind,
            config=load_config(),
            platform_filter=platform,
        )
    except Exception as exc:  # noqa: BLE001 - MCP tools return errors as data
        return {"error": str(exc)}
    return {
        "name": plan.name,
        "kind": plan.kind,
        "source_path": str(plan.source_path),
        "manifest_path": str(plan.manifest_path) if plan.manifest_path else "",
        "files": [str(path) for path in plan.files],
        "targets": [
            {
                "platform": target.platform,
                "kind": target.kind,
                "install_mechanism": target.install_mechanism,
                "path": str(target.path),
                "auto_install": target.auto_install,
            }
            for target in plan.targets
        ],
        "warnings": plan.warnings,
        "detected_agents": [
            {
                "id": item.provider.id,
                "name": item.provider.name,
                "detected": item.detected,
                "auto_install": item.auto_install,
                "matched_signals": [
                    {"kind": signal.kind, "value": signal.value, "soft": signal.soft}
                    for signal in item.matched_signals
                ],
                "notes": item.notes,
            }
            for item in plan.detected_agents
        ],
    }


@mcp.tool(
    title="Legacy direct write: update one resource",
    annotations=_APPLY_REMOTE,
    meta={
        "cc_port": {
            "preferred": False,
            "stability": "legacy-direct-write",
            "replacement": "asset_action_plan -> asset_action_apply",
        }
    },
)
def update_skill(name: str, kind: str | None = None) -> dict[str, Any]:
    """Force-sync a single item by name."""
    cfg = load_config()
    registry = load_registry()
    entry = registry.get(name, kind)
    if entry is None:
        return {"error": f"No item named {name!r} in registry."}
    result = sync_one(entry, config=cfg)
    return {
        "name": result.name,
        "action": result.action.value,
        "install_path": str(result.install_path),
        "platforms": result.platforms_installed,
        "detail": result.detail,
    }


@mcp.tool(
    title="Legacy registered resource status",
    annotations=_READ_REMOTE,
    meta={"cc_port": {"preferred": False, "stability": "compatibility"}},
)
def skill_status(kind: str | None = None) -> dict[str, Any]:
    """Report local vs remote commit status for registered items.

    Args:
        kind: Optional filter by resource type ("skill", "mcp", "rule").
    """
    cfg = load_config()
    rows = status_all(config=cfg, kind=kind)
    return {
        "install_target": str(cfg.install.target_path),
        "items": [
            {
                "name": s.name,
                "installed": s.installed,
                "install_path": str(s.install_path),
                "local_commit": s.local_commit,
                "remote_commit": s.remote_commit,
                "has_update": s.has_update,
            }
            for s in rows
        ],
    }


def main() -> None:  # pragma: no cover - entry point
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":  # pragma: no cover
    main()
