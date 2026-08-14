"""Stable wire contracts shared by machine-facing CC Port adapters.

This module deliberately has no CLI, desktop, MCP, Git, or filesystem side
effects.  Interfaces may serialize these models directly and then choose their
own transport-specific error mechanism.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..core.secret_scan import redact_secret_text

WIRE_CONTRACT_VERSION = 1

WIRE_EXIT_SUCCESS = 0
WIRE_EXIT_RUNTIME_FAILURE = 1
WIRE_EXIT_INVALID_REQUEST = 2
WIRE_EXIT_SAFE_NONCOMPLETION = 3

PUBLIC_PRIVATE_PATH_PLACEHOLDER = "${PRIVATE_PATH}"

_PUBLIC_PRIVATE_PATH_FIELDS = frozenset(
    {
        "backup_path",
        "config_path",
        "content_path",
        "home_dir",
        "install_target",
        "instructions_path",
        "link_target",
        "local_path",
        "mcp_config_path",
        "mcp_json",
        "memories_dir",
        "plugins_dir",
        "prompts_dir",
        "rules_dir",
        "settings_path",
        "skill_path",
        "skills_dir",
        "source_content_path",
        "source_link_target",
        "source_path",
        "target_path",
    }
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_PATH_IN_TEXT = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/][^\s\"'<>|]+|\\\\[^\s\"'<>|]+)"
)
_POSIX_USER_PATH_IN_TEXT = re.compile(
    r"(?<![A-Za-z0-9:])/(?:home|Users|root|mnt/[A-Za-z]/Users)(?:/[^\s\"'<>]+)+"
)

SUCCESS_STATUSES = frozenset({"planned", "ready", "succeeded", "unchanged"})
SAFE_NONCOMPLETION_STATUSES = frozenset(
    {
        "blocked",
        "cancelled",
        "needs-action",
        "needs-confirmation",
        "partial",
        "stale-plan",
    }
)

T = TypeVar("T")

ApprovalStatusWire = Literal[
    "not-required",
    "pending",
    "approved",
    "consumed",
    "rejected",
    "expired",
]


class WireError(BaseModel):
    """Stable structured error returned by machine-facing adapters."""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict[str, Any] | None = None


class WireEnvelope(BaseModel, Generic[T]):
    """Versioned machine response envelope.

    ``ok`` means the requested operation reached a successful terminal state.
    A safely rejected write therefore has ``ok=false`` while still carrying a
    structured plan or result in ``data``.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    contract_version: Literal[1] = WIRE_CONTRACT_VERSION
    ok: bool
    status: str = Field(min_length=1)
    data: T | None = None
    error: WireError | None = None

    @model_validator(mode="after")
    def _validate_outcome(self) -> WireEnvelope[T]:
        if self.ok and self.error is not None:
            raise ValueError("successful envelopes must not contain an error")
        if not self.ok and self.error is None:
            raise ValueError("failed envelopes require an error")
        return self


class AssetBatchChoiceWire(BaseModel):
    """Strict transport-neutral representation of one batch choice."""

    model_config = ConfigDict(extra="forbid", strict=True)

    resource_key: str = Field(min_length=1)
    platform: str = ""
    local_instance_id: str = ""
    resolution: Literal["overwrite", "rename"] = "overwrite"
    new_name: str = ""
    overwrite_unmanaged: bool = False
    plugin_track: Literal["", "content", "reference", "skip"] = ""
    ownership_confirmed: bool = False
    link_target_confirmed: bool = False
    reference_origin: dict[str, str] = Field(default_factory=dict)
    plugin_dependencies: dict[str, str] = Field(default_factory=dict)

    @field_validator("resource_key", mode="after")
    @classmethod
    def _validate_resource_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("resource_key must not be blank")
        return normalized

    @field_validator("platform", "local_instance_id", "new_name", "plugin_track", mode="after")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()


class AssetBatchRequestWire(BaseModel):
    """Inputs that must remain identical between batch plan and apply."""

    model_config = ConfigDict(extra="forbid", strict=True)

    direction: Literal["upload", "download"]
    resource_keys: list[str] = Field(min_length=1)
    target_platforms: list[str] = Field(default_factory=list)
    choices: list[AssetBatchChoiceWire] = Field(default_factory=list)

    @field_validator("resource_keys", "target_platforms", mode="after")
    @classmethod
    def _normalize_unique_strings(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if not item:
                raise ValueError("values must not contain empty strings")
            if item not in normalized:
                normalized.append(item)
        return normalized

    @model_validator(mode="after")
    def _validate_direction_inputs(self) -> AssetBatchRequestWire:
        if self.direction == "download" and not self.target_platforms:
            raise ValueError("download requests require at least one target platform")
        return self


class StrictWireModel(BaseModel):
    """Base for closed, machine-discoverable payload schemas."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ProfileSummaryWire(StrictWireModel):
    profile_id: str
    tool_id: str
    environment_kind: str
    environment_name: str
    display_name: str
    enabled: bool


class ServerStatusWire(StrictWireModel):
    server: Literal["cc-port"] = "cc-port"
    version: str
    transport: Literal["stdio"] = "stdio"
    automation_policy: Literal["plan-apply-verify"] = "plan-apply-verify"
    approval_mode: Literal["desktop-only"] = "desktop-only"
    approval_tools_exposed: Literal[False] = False
    recommended_tools: list[str]
    legacy_direct_write_tools: list[str]
    profiles: list[ProfileSummaryWire]


class DoctorCheckWire(StrictWireModel):
    id: str
    label: str
    status: Literal["ok", "warning", "error", "skipped"]
    ok: bool
    detail: str
    profile_id: str = ""


class DoctorReportWire(StrictWireModel):
    status: Literal["ok", "warning", "error"]
    ok_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    checks: list[DoctorCheckWire]


class RegistryHealthWire(StrictWireModel):
    status: str
    checked_commit: str
    issue_count: int = Field(ge=0)
    repairable_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    message: str


class AssetRemoteStateWire(StrictWireModel):
    exists: bool
    status: str
    writable: bool
    read_only: bool
    commit: str
    path: str | None
    description: str


class AssetLocalInstanceWire(StrictWireModel):
    id: str
    platform: str
    install_name: str
    path: str | None
    ownership: str
    fingerprint: str
    description: str
    status: str
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    track: str = ""
    scope: str = ""
    project_id: str = ""
    source_kind: str = ""
    source_id: str = ""
    selector: str = ""
    observed_version: str = ""
    enabled: bool | None = None
    writable: bool = True
    content_path: str | None = None
    path_kind: str = "regular"
    link_health: str = "ready"
    link_target: str = ""
    reparse_tag: str = ""
    link_target_trusted: bool = True
    tool_id: str = ""
    environment_kind: str = ""
    environment_name: str = ""
    display_name: str = ""
    memory_layout: str = ""


class AssetResourceWire(StrictWireModel):
    resource_key: str
    kind: str
    name: str
    description: str
    description_source: str
    local_status: str
    remote_status: str
    status: str
    remote: AssetRemoteStateWire
    local_instances: list[AssetLocalInstanceWire]
    metadata_differences: list[str] = Field(default_factory=list)
    diff_summary: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    available_actions: list[str] = Field(default_factory=list)
    plugin_track: str = ""
    plugin_platform: str = ""
    plugin_id: str = ""
    plugin_source_kind: str = ""
    plugin_source_id: str = ""
    plugin_marketplace: str = ""
    plugin_marketplace_source: str = ""
    plugin_selector: str = ""
    plugin_observed_version: str = ""


class AssetInventoryWire(StrictWireModel):
    branch: str
    remote_commit: str
    repo_url: str
    remote_available: bool
    remote_warning: str
    scanned_local: bool
    generated_at: str
    legacy_write_blocker: str
    resources: list[AssetResourceWire]
    registry_health: RegistryHealthWire | None = None


class AssetDiffFileWire(StrictWireModel):
    path: str
    status: Literal["added", "deleted", "modified"]
    diff: str
    binary: bool = False
    truncated: bool = False


class AssetContentDiffWire(StrictWireModel):
    resource_key: str
    local_instance_id: str
    platform: str
    remote_commit: str
    files: list[AssetDiffFileWire]
    added_files: int = Field(ge=0)
    deleted_files: int = Field(ge=0)
    modified_files: int = Field(ge=0)
    binary_files: int = Field(ge=0)
    truncated: bool = False


class AssetActionPlanWire(StrictWireModel):
    operation_id: str
    plan_hash: str = ""
    requires_approval: bool = False
    approval_id: str = ""
    approval_status: ApprovalStatusWire = "not-required"
    approval_scope_hash: str = ""
    action: str
    resource_key: str
    target_resource_key: str
    kind: str
    name: str
    platform: str
    local_instance_id: str
    local_locator: str
    remote_commit: str
    remote_repo_hash: str = ""
    remote_branch: str = ""
    remote_target_exists: bool
    remote_target_fingerprint: str
    local_source_fingerprint: str
    target_path: str | None
    target_exists: bool
    target_fingerprint: str
    target_managed: bool
    source_path: str | None = None
    source_content_path: str | None = None
    source_path_kind: str = "regular"
    source_link_health: str = "ready"
    source_link_target: str = ""
    source_reparse_tag: str = ""
    link_target_confirmed: bool = False
    overwrite_unmanaged: bool = False
    new_name: str = ""
    new_install_name: str = ""
    tool_id: str = ""
    environment_kind: str = ""
    environment_name: str = ""
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    blocked: bool = False
    created_at: str = ""
    plugin_data: dict[str, Any] = Field(default_factory=dict)
    schema_version: int


class AssetActionResultWire(StrictWireModel):
    operation_id: str
    plan_hash: str = ""
    approval_id: str = ""
    approval_status: ApprovalStatusWire = "not-required"
    action: str
    status: str
    resource_key: str
    target_resource_key: str
    platform: str
    message: str
    remote_commit: str = ""
    local_path: str | None = None
    replayed_on_latest: bool = False
    push_retry_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    operation_status: str = ""
    stale_plan: AssetActionPlanWire | None = None


class AssetBatchPlanItemWire(StrictWireModel):
    id: str
    resource_key: str
    platform: str
    local_instance_id: str
    action: str
    disposition: str
    target_resource_key: str
    reason: str = ""
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    plan: AssetActionPlanWire | None = None


class AssetBatchResourceCheckWire(StrictWireModel):
    resource_key: str
    local_status: str
    remote_status: str
    status: str
    local_instances: list[AssetLocalInstanceWire] = Field(default_factory=list)


class AssetBatchPlanWire(StrictWireModel):
    operation_id: str = ""
    requires_approval: bool = False
    approval_id: str = ""
    approval_status: ApprovalStatusWire = "not-required"
    approval_scope_hash: str = ""
    direction: Literal["upload", "download"]
    resource_keys: list[str]
    target_platforms: list[str]
    remote_commit: str
    remote_repo_hash: str = ""
    remote_branch: str = ""
    plan_hash: str
    items: list[AssetBatchPlanItemWire]
    executable_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    status: str = "planned"
    checked_resources: list[AssetBatchResourceCheckWire] = Field(default_factory=list)


class AssetBatchResultWire(StrictWireModel):
    operation_id: str = ""
    approval_id: str = ""
    approval_status: ApprovalStatusWire = "not-required"
    status: str
    plan_hash: str
    results: list[AssetActionResultWire]
    stale_plan: AssetBatchPlanWire | None = None


class RegistryRepairChoiceWire(StrictWireModel):
    issue_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    name: str = ""


class RegistryAuditIssueWire(StrictWireModel):
    id: str
    code: str
    severity: str
    message: str
    resource_key: str = ""
    kind: str = ""
    name: str = ""
    path: str = ""
    default_action: str = "keep"
    actions: list[str] = Field(default_factory=list)
    blocking: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class RegistryRepairPlanWire(StrictWireModel):
    operation_id: str = ""
    requires_approval: bool = False
    approval_id: str = ""
    approval_status: ApprovalStatusWire = "not-required"
    approval_scope_hash: str = ""
    remote_commit: str
    repo_url: str
    branch: str
    registry_status: str
    issues: list[RegistryAuditIssueWire]
    choices: list[RegistryRepairChoiceWire]
    registry_diff: str
    plan_hash: str
    executable_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    repairable: bool
    original_registry_hash: str = ""
    candidate_fingerprints: dict[str, str] = Field(default_factory=dict)
    resulting_registry_text: str = ""
    legacy_item_count: int = Field(default=0, ge=0)
    rebuilt_item_count: int = Field(default=0, ge=0)
    dropped_item_count: int = Field(default=0, ge=0)


class RegistryRepairResultWire(StrictWireModel):
    operation_id: str = ""
    approval_id: str = ""
    approval_status: ApprovalStatusWire = "not-required"
    status: str
    plan_hash: str
    remote_commit: str = ""
    message: str = ""
    stale_plan: RegistryRepairPlanWire | None = None


class OperationTargetWire(StrictWireModel):
    path: str
    action: str
    change_action: str = ""
    backup_path: str = ""
    resource: str = ""
    platform: str = ""
    before_hash: str = ""
    after_hash: str = ""
    verified: bool = False


class OperationDetailWire(StrictWireModel):
    operation_id: str
    kind: str
    status: str
    started_at: str
    finished_at: str
    message: str
    rolled_back: bool
    target_count: int = Field(ge=0)
    changed_target_count: int = Field(ge=0)
    restorable: bool
    metadata: dict[str, Any]
    targets: list[OperationTargetWire]


def parse_asset_batch_choices(value: object) -> list[AssetBatchChoiceWire]:
    """Parse a strict list of batch choices without coercing booleans."""

    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Asset batch choices must be a list.")
    try:
        return [AssetBatchChoiceWire.model_validate(item) for item in value]
    except ValidationError as exc:
        raise ValueError(_validation_message("Invalid asset batch choices", exc)) from exc


def parse_asset_batch_request(value: object) -> AssetBatchRequestWire:
    """Parse a complete strict request shared by batch plan and apply."""

    try:
        return AssetBatchRequestWire.model_validate(value)
    except ValidationError as exc:
        raise ValueError(_validation_message("Invalid asset batch request", exc)) from exc


def wire_success(data: T | None = None, *, status: str = "succeeded") -> WireEnvelope[T]:
    """Build a successful response envelope."""

    return WireEnvelope[T](ok=True, status=status, data=data, error=None)


def wire_failure(
    code: str,
    message: str,
    *,
    status: str = "failed",
    data: T | None = None,
    details: dict[str, Any] | None = None,
) -> WireEnvelope[T]:
    """Build a failed or safely non-completing response envelope."""

    return WireEnvelope[T](
        ok=False,
        status=status,
        data=data,
        error=WireError(code=code, message=message, details=details),
    )


def wire_result(
    data: T,
    *,
    status: str,
    message: str = "Operation did not complete successfully.",
) -> WireEnvelope[T]:
    """Map a service result status onto the stable envelope contract."""

    if status in SUCCESS_STATUSES:
        return wire_success(data, status=status)
    code = status.replace("-", "_")
    return wire_failure(code, message, status=status, data=data)


def wire_exit_code(envelope: WireEnvelope[Any]) -> int:
    """Return the stable process exit code for an envelope."""

    if envelope.ok:
        return WIRE_EXIT_SUCCESS
    if envelope.status == "invalid-request":
        return WIRE_EXIT_INVALID_REQUEST
    if envelope.status in SAFE_NONCOMPLETION_STATUSES:
        return WIRE_EXIT_SAFE_NONCOMPLETION
    return WIRE_EXIT_RUNTIME_FAILURE


def to_wire_value(value: Any) -> Any:
    """Convert service values to a stable JSON-compatible internal wire shape.

    Desktop-only localization references are intentionally removed from the
    machine contract. Absolute-path privacy projection is deliberately handled
    later by :func:`to_public_wire_value` so plan and approval hashes retain the
    full internal identity.
    """

    if value is None or isinstance(value, int | float | bool):
        return value
    if isinstance(value, str):
        return redact_secret_text(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return to_wire_value(value.model_dump(mode="python"))
    if is_dataclass(value):
        return to_wire_value(asdict(value))
    if isinstance(value, dict):
        return {
            str(key): to_wire_value(item)
            for key, item in value.items()
            if not (isinstance(key, str) and key.endswith(("_ref", "_refs")))
        }
    if isinstance(value, list | tuple | set):
        return [to_wire_value(item) for item in value]
    return str(value)


def to_public_wire_value(value: Any) -> Any:
    """Project a service value into the privacy-minimized public MCP shape.

    This projection is intentionally separate from :func:`to_wire_value`.
    Plan hashes, approval scopes, stale checks, and persisted service identity
    must continue to use the unprojected value; only an adapter's outward-facing
    payload may use this helper.
    """

    return _project_public_wire_paths(to_wire_value(value))


def _project_public_wire_paths(value: Any, *, field_name: str = "") -> Any:
    if isinstance(value, str):
        if value and field_name in _PUBLIC_PRIVATE_PATH_FIELDS:
            return PUBLIC_PRIVATE_PATH_PLACEHOLDER
        if value and field_name == "path" and _is_absolute_local_path(value):
            return PUBLIC_PRIVATE_PATH_PLACEHOLDER
        return _redact_embedded_private_paths(value)
    if isinstance(value, dict):
        return {
            str(key): _project_public_wire_paths(item, field_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_project_public_wire_paths(item, field_name=field_name) for item in value]
    return value


def _is_absolute_local_path(value: str) -> bool:
    stripped = value.strip()
    return bool(
        stripped.startswith(("/", "\\", "//", "file://")) or _WINDOWS_ABSOLUTE_PATH.match(stripped)
    )


def _redact_embedded_private_paths(value: str) -> str:
    redacted = _WINDOWS_PATH_IN_TEXT.sub(
        PUBLIC_PRIVATE_PATH_PLACEHOLDER,
        value,
    )
    return _POSIX_USER_PATH_IN_TEXT.sub(
        PUBLIC_PRIVATE_PATH_PLACEHOLDER,
        redacted,
    )


def wire_canonical_hash(value: Any) -> str:
    """Hash one normalized machine contract without transport formatting drift."""

    payload = json.dumps(
        to_wire_value(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def asset_action_plan_hash(value: Any) -> str:
    """Hash the immutable action-plan identity, excluding transport bindings."""

    payload = to_wire_value(value)
    if not isinstance(payload, dict):
        raise ValueError("Asset action plan must be an object.")
    identity = dict(payload)
    for key in (
        "operation_id",
        "created_at",
        "plan_hash",
        "requires_approval",
        "approval_id",
        "approval_status",
        "approval_scope_hash",
    ):
        identity.pop(key, None)
    return wire_canonical_hash(identity)


def asset_action_approval_scope(value: Any) -> dict[str, Any]:
    """Return the exact human-approval scope for one asset action plan."""

    payload = to_wire_value(value)
    if not isinstance(payload, dict):
        raise ValueError("Asset action plan must be an object.")
    return {
        "action": str(payload.get("action") or ""),
        "resource_key": str(payload.get("resource_key") or ""),
        "target_resource_key": str(payload.get("target_resource_key") or ""),
        "platform": str(payload.get("platform") or ""),
        "local_instance_id": str(payload.get("local_instance_id") or ""),
        "new_name": str(payload.get("new_name") or ""),
        "new_install_name": str(payload.get("new_install_name") or ""),
        "overwrite_unmanaged": bool(payload.get("overwrite_unmanaged")),
        "link_target_confirmed": bool(payload.get("link_target_confirmed")),
        "remote_commit": str(payload.get("remote_commit") or ""),
        "remote_repo_hash": str(payload.get("remote_repo_hash") or ""),
        "remote_branch": str(payload.get("remote_branch") or ""),
    }


def asset_batch_operation_id(plan_hash: str) -> str:
    return _bound_operation_id("asset-batch", plan_hash)


def asset_batch_approval_scope(
    *,
    direction: str,
    resource_keys: list[str],
    target_platforms: list[str] | None,
    choices: list[AssetBatchChoiceWire],
    plan_hash: str,
) -> dict[str, Any]:
    """Bind a batch approval to the complete normalized plan/apply request."""

    return {
        "direction": str(direction).strip().lower(),
        "resource_keys": _normalized_unique_strings(resource_keys),
        "target_platforms": _normalized_unique_strings(target_platforms or []),
        "choices": [item.model_dump(mode="json") for item in choices],
        "plan_hash": str(plan_hash).strip(),
    }


def registry_repair_operation_id(plan_hash: str) -> str:
    return _bound_operation_id("registry-repair", plan_hash)


def registry_repair_approval_scope(
    *,
    branch: str,
    choices: list[RegistryRepairChoiceWire],
    plan_hash: str,
) -> dict[str, Any]:
    """Bind Registry repair approval to selected issue resolutions and snapshot."""

    return {
        "branch": str(branch).strip(),
        "choices": [item.model_dump(mode="json") for item in choices],
        "plan_hash": str(plan_hash).strip(),
    }


def _bound_operation_id(kind: str, plan_hash: str) -> str:
    normalized_hash = str(plan_hash).strip().lower()
    if len(normalized_hash) != 64 or any(
        character not in "0123456789abcdef" for character in normalized_hash
    ):
        raise ValueError("Plan hash must be a complete SHA-256 digest.")
    return f"{kind}:{normalized_hash}"


def _normalized_unique_strings(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _validation_message(prefix: str, exc: ValidationError) -> str:
    details: list[str] = []
    for error in exc.errors(include_url=False, include_context=False):
        location = ".".join(str(item) for item in error.get("loc", ())) or "request"
        details.append(f"{location}: {error.get('msg', 'invalid value')}")
    return f"{prefix}: {'; '.join(details)}"
