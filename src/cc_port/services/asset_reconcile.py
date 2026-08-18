"""Read-only, privacy-minimized asset reconciliation context.

The reconciliation surface deliberately projects the existing asset inventory
instead of duplicating discovery or planning.  It never creates a plan,
approval, operation, or write transaction.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from ..agent.contracts import (
    AssetManifestEntryWire,
    AssetManifestWire,
    AssetReconcileActionCheckWire,
    AssetReconcileBaselineWire,
    AssetReconcileComparisonWire,
    AssetReconcileContextWire,
    AssetReconcileIssueWire,
    AssetReconcileLocalInstanceWire,
    AssetReconcilePageWire,
    AssetReconcilePluginWire,
    AssetReconcileProfileCoverageWire,
    AssetReconcileRemoteContextWire,
    AssetReconcileRemoteWire,
    AssetReconcileResourceWire,
    AssetReconcileScopeWire,
    AssetReconcileSummaryWire,
    to_public_wire_value,
    wire_canonical_hash,
)
from ..core.config import Config, load_config
from ..core.resource_files import is_resource_path_excluded
from ..core.secret_scan import find_secret_text, redact_secret_text
from ..core.secrets import sanitize_mcp_config_for_storage
from .asset_sync import (
    ASSET_DIFF_MAX_FILE_BYTES,
    ASSET_DIFF_MAX_FILES,
    AssetInventory,
    AssetPlatformRow,
    AssetResourceRow,
    _configured_plugin_project_scan_scope,
    _download_plan_blocker_refs,
    _plugin_remote_content_source,
    _profile_environment_state,
    _prompt_payload_path,
    _read_mcp_server,
    _upload_plan_blocker_refs,
    build_asset_inventory,
)
from .local_path_probe import probe_local_path
from .ui_messages import UiMessageRef

ASSET_RECONCILE_CONTEXT_SCHEMA_VERSION = 1
ASSET_RECONCILE_DEFAULT_PAGE_SIZE = 100
ASSET_RECONCILE_MAX_PAGE_SIZE = 200
ASSET_RECONCILE_MAX_ISSUES = 32
ASSET_RECONCILE_MAX_MESSAGE_CHARS = 512
_CURSOR_MAX_CHARS = 2048
_CONFIRMATION_CODES = {
    "asset.blocker.unmanaged_target": "overwrite-unmanaged",
    "asset.blocker.link_target_confirmation_required": "confirm-link-target",
}
_GENERIC_POSIX_PATH_IN_TEXT = re.compile(
    r"(?<![A-Za-z0-9:])/(?!/)[^\s\"'<>|,;]+"
)


class AssetReconcileError(RuntimeError):
    """Base error for the read-only reconciliation projection."""


class AssetReconcileInvalidRequest(ValueError):
    """Raised when a schema, page, or cursor argument is invalid."""


class AssetReconcileStaleContext(AssetReconcileError):
    """Raised when a continuation cursor no longer matches current facts."""


def build_asset_reconcile_context(
    *,
    config: Config | None = None,
    context_schema_version: int = ASSET_RECONCILE_CONTEXT_SCHEMA_VERSION,
    cursor: str = "",
    page_size: int = ASSET_RECONCILE_DEFAULT_PAGE_SIZE,
    include_same: bool = False,
) -> AssetReconcileContextWire:
    """Build one fresh, bounded page of reconciliation facts.

    Every call reuses the canonical inventory scanner with the complete
    configured scope.  A continuation cursor is accepted only when those
    freshly observed facts still produce the same context identity.
    """

    _validate_request(
        context_schema_version=context_schema_version,
        cursor=cursor,
        page_size=page_size,
        include_same=include_same,
    )
    cursor_data = _decode_cursor(cursor) if cursor else None
    if cursor_data is not None and (
        cursor_data["page_size"] != page_size
        or cursor_data["include_same"] is not include_same
    ):
        raise AssetReconcileInvalidRequest(
            "Continuation requests must retain the cursor page_size and include_same scope."
        )
    cfg = config or load_config()
    project_scope = _configured_plugin_project_scan_scope(cfg)
    inventory = build_asset_inventory(
        config=cfg,
        scan_local=True,
        refresh_remote=True,
        scan_global=True,
        project_ids=None,
        cleanup_expired_plans=False,
        enabled_profiles_only=True,
    )
    coverage = _build_coverage(cfg, inventory)
    resources = _build_resources(cfg, inventory)
    scope = AssetReconcileScopeWire(
        include_same=include_same,
        saved_project_count=project_scope.total_count,
        scanned_saved_project_count=project_scope.scanned_count,
        unavailable_saved_project_count=project_scope.unavailable_count,
    )
    remote = _build_remote_context(cfg, inventory)
    summary = _build_summary(resources, coverage)
    completeness = _completeness(inventory, coverage, resources, scope)

    # Only public, privacy-safe facts may bind the cursor. In particular, do
    # not add raw inventory fingerprints for secret-withheld or truncated
    # files: a deterministic public commitment would become an offline
    # confirmation oracle. Such hidden changes therefore do not carry a strong
    # stale guarantee until CC Port has a protected, cross-process HMAC key.
    identity = {
        "context_schema_version": ASSET_RECONCILE_CONTEXT_SCHEMA_VERSION,
        "scope": scope,
        "remote": remote,
        "coverage": coverage,
        "summary": summary,
        "completeness": completeness,
        "resources": resources,
    }
    context_id = wire_canonical_hash(identity)

    if cursor_data is not None:
        if cursor_data["context_id"] != context_id:
            raise AssetReconcileStaleContext(
                "The local or remote asset context changed; restart reconciliation from the first page."
            )
        offset = cursor_data["offset"]
    else:
        offset = 0

    visible = [item for item in resources if include_same or item.resource_status != "same"]
    if cursor_data is not None and (
        offset <= 0 or offset >= len(visible) or offset % page_size != 0
    ):
        raise AssetReconcileInvalidRequest(
            "The reconciliation cursor offset is not a valid page boundary."
        )
    if offset > len(visible):
        raise AssetReconcileInvalidRequest("The reconciliation cursor offset is invalid.")
    page_resources = visible[offset : offset + page_size]
    next_offset = offset + len(page_resources)
    has_more = next_offset < len(visible)
    next_cursor = (
        _encode_cursor(
            context_id=context_id,
            offset=next_offset,
            page_size=page_size,
            include_same=include_same,
        )
        if has_more
        else ""
    )
    return AssetReconcileContextWire(
        context_id=context_id,
        generated_at=inventory.generated_at,
        completeness=completeness,
        scope=scope,
        remote=remote,
        coverage=coverage,
        summary=summary,
        page=AssetReconcilePageWire(
            offset=offset,
            page_size=page_size,
            returned=len(page_resources),
            total=len(visible),
            has_more=has_more,
            next_cursor=next_cursor,
        ),
        resources=page_resources,
    )


def _validate_request(
    *,
    context_schema_version: int,
    cursor: str,
    page_size: int,
    include_same: bool,
) -> None:
    if (
        isinstance(context_schema_version, bool)
        or not isinstance(context_schema_version, int)
        or context_schema_version != 1
    ):
        raise AssetReconcileInvalidRequest("Only asset reconciliation context schema v1 is supported.")
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= ASSET_RECONCILE_MAX_PAGE_SIZE
    ):
        raise AssetReconcileInvalidRequest("page_size must be between 1 and 200.")
    if not isinstance(include_same, bool):
        raise AssetReconcileInvalidRequest("include_same must be a boolean.")
    if not isinstance(cursor, str) or len(cursor) > _CURSOR_MAX_CHARS:
        raise AssetReconcileInvalidRequest("The reconciliation cursor is invalid.")


def _encode_cursor(
    *,
    context_id: str,
    offset: int,
    page_size: int,
    include_same: bool,
) -> str:
    # This unkeyed checksum detects accidental corruption only. It does not
    # authenticate a caller or prove that pages were consumed in order;
    # clients must treat the returned cursor as opaque, and no write workflow
    # may treat it as authorization.
    value: dict[str, Any] = {
        "v": ASSET_RECONCILE_CONTEXT_SCHEMA_VERSION,
        "c": context_id,
        "o": offset,
        "p": page_size,
        "s": include_same,
    }
    value["x"] = _cursor_checksum(value)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != cursor:
            raise ValueError("non-canonical cursor encoding")
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetReconcileInvalidRequest("The reconciliation cursor is invalid.") from exc
    if not isinstance(value, dict) or set(value) != {"v", "c", "o", "p", "s", "x"}:
        raise AssetReconcileInvalidRequest("The reconciliation cursor is invalid.")
    checksum = value.get("x")
    if not isinstance(checksum, str) or checksum != _cursor_checksum(
        {key: value[key] for key in ("v", "c", "o", "p", "s")}
    ):
        raise AssetReconcileInvalidRequest("The reconciliation cursor checksum is invalid.")
    if value.get("v") != ASSET_RECONCILE_CONTEXT_SCHEMA_VERSION:
        raise AssetReconcileInvalidRequest("The reconciliation cursor schema is unsupported.")
    context_id = value.get("c")
    offset = value.get("o")
    stored_page_size = value.get("p")
    stored_include_same = value.get("s")
    if (
        not isinstance(context_id, str)
        or len(context_id) != 64
        or any(character not in "0123456789abcdef" for character in context_id)
        or isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset < 0
        or isinstance(stored_page_size, bool)
        or not isinstance(stored_page_size, int)
        or not isinstance(stored_include_same, bool)
    ):
        raise AssetReconcileInvalidRequest("The reconciliation cursor is invalid.")
    return {
        "context_id": context_id,
        "offset": offset,
        "page_size": stored_page_size,
        "include_same": stored_include_same,
    }


def _cursor_checksum(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _build_coverage(
    cfg: Config,
    inventory: AssetInventory,
) -> list[AssetReconcileProfileCoverageWire]:
    rows_by_profile: dict[str, list[AssetPlatformRow]] = {}
    for row in inventory.rows:
        rows_by_profile.setdefault(row.platform, []).append(row)

    result: list[AssetReconcileProfileCoverageWire] = []
    for profile in sorted(cfg.platforms.profiles, key=lambda item: item.name):
        issues: list[AssetReconcileIssueWire] = []
        if not profile.enabled:
            scan_state: Literal[
                "complete", "partial", "unavailable", "not-scanned-disabled"
            ] = "not-scanned-disabled"
        else:
            try:
                available, problem = _profile_environment_state(profile)
            except Exception as exc:  # fail closed for an adapter-specific probe
                available, problem = False, str(exc)
            if not available:
                scan_state = "unavailable"
                issues.append(
                    _issue(
                        "asset.blocker.environment_unavailable",
                        "blocker",
                        "scan",
                        problem or "The configured runtime environment is unavailable.",
                    )
                )
            else:
                profile_rows = rows_by_profile.get(profile.name, [])
                row_scan_blockers = [
                    ref
                    for row in profile_rows
                    for ref in row.blocker_refs
                    if ref.code in {
                        "asset.blocker.environment_unavailable",
                        "asset.blocker.local_path_unsafe",
                        "asset.blocker.nested_link_unsafe",
                        "asset.blocker.wsl_link_unsupported",
                        "asset.blocker.memory_settings_untrusted",
                        "asset.blocker.memory_target_mapping_required",
                    }
                ]
                path_issues = _profile_path_coverage_issues(profile)
                if row_scan_blockers or path_issues:
                    scan_state = "partial"
                    issues.extend(_issues_from_refs(row_scan_blockers, "blocker", "scan"))
                    issues.extend(path_issues)
                else:
                    scan_state = "complete"
        result.append(
            AssetReconcileProfileCoverageWire(
                profile_id=profile.name,
                tool_id=_safe_text(profile.effective_tool_id),
                environment_kind=_safe_text(profile.environment_kind),
                environment_name=_safe_text(profile.environment_name),
                display_name=_safe_text(profile.effective_display_name),
                configuration_state="enabled" if profile.enabled else "disabled",
                scan_state=scan_state,
                issues=_bounded_issues(issues),
            )
        )
    return result


def _profile_path_coverage_issues(profile: Any) -> list[AssetReconcileIssueWire]:
    try:
        candidates = [
            profile.skills_path(),
            profile.mcp_json_path(),
            profile.rules_path(),
            profile.prompts_path(),
            profile.plugins_path(),
            profile.instructions_file(),
            profile.memories_path(),
            profile.settings_file(),
        ]
    except (OSError, ValueError) as exc:
        return [
            _issue(
                "asset.reconcile.profile_path_invalid",
                "blocker",
                "scan",
                str(exc) or "A configured profile path is invalid.",
            )
        ]
    issues: list[AssetReconcileIssueWire] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        identity = os.path.normcase(str(candidate))
        if identity in seen:
            continue
        seen.add(identity)
        probe = probe_local_path(candidate)
        if probe.health == "missing":
            continue
        if not probe.ready or probe.content_path is None:
            issues.append(
                _issue(
                    "asset.reconcile.profile_path_unavailable",
                    "blocker",
                    "scan",
                    probe.problem or "A configured profile path cannot be scanned safely.",
                )
            )
            continue
        try:
            if probe.content_path.is_dir():
                with os.scandir(probe.content_path) as entries:
                    next(entries, None)
            elif probe.content_path.is_file():
                with probe.content_path.open("rb") as handle:
                    handle.read(1)
        except OSError as exc:
            issues.append(
                _issue(
                    "asset.reconcile.profile_path_unreadable",
                    "blocker",
                    "scan",
                    str(exc) or "A configured profile path cannot be read safely.",
                )
            )
    return _bounded_issues(issues)


def _build_remote_context(
    cfg: Config,
    inventory: AssetInventory,
) -> AssetReconcileRemoteContextWire:
    configured = bool(inventory.repo_url)
    if inventory.remote_available and not inventory.remote_warning:
        freshness: Literal["fresh", "cached", "local-fallback", "unavailable"] = "fresh"
    elif inventory.remote_available:
        freshness = "cached"
    elif inventory.remote_commit:
        freshness = "local-fallback"
    else:
        freshness = "unavailable"
    issues: list[AssetReconcileIssueWire] = []
    if inventory.remote_warning_ref is not None:
        issues.extend(_issues_from_refs([inventory.remote_warning_ref], "warning", "scan"))
    elif inventory.remote_warning:
        issues.append(
            _issue("asset.reconcile.remote_warning", "warning", "scan", inventory.remote_warning)
        )
    registry_status = (
        inventory.registry_health.status if inventory.registry_health is not None else "unavailable"
    )
    if inventory.registry_health is not None and inventory.registry_health.message:
        severity: Literal["warning", "blocker"] = (
            "warning" if registry_status in {"healthy", "issues"} else "blocker"
        )
        issues.append(
            _issue(
                f"asset.reconcile.registry.{registry_status}",
                severity,
                "scan",
                inventory.registry_health.message,
            )
        )
    return AssetReconcileRemoteContextWire(
        configured=configured,
        freshness=freshness,
        available=inventory.remote_available,
        branch=_safe_text(inventory.branch),
        commit=_safe_text(inventory.remote_commit),
        registry_status=registry_status,
        issues=_bounded_issues(issues),
    )


def _build_resources(cfg: Config, inventory: AssetInventory) -> list[AssetReconcileResourceWire]:
    enabled_profiles = {profile.name for profile in cfg.platforms.profiles if profile.enabled}
    legacy_write_blocker_ref = inventory.legacy_write_blocker_ref
    if legacy_write_blocker_ref is None and inventory.legacy_write_blocker:
        legacy_write_blocker_ref = UiMessageRef(
            code="asset.blocker.legacy_write_state",
            fallback=inventory.legacy_write_blocker,
        )
    rows_by_resource: dict[str, list[AssetPlatformRow]] = {}
    for row in inventory.rows:
        if row.platform in enabled_profiles:
            rows_by_resource.setdefault(row.resource_key, []).append(row)

    resources: list[AssetReconcileResourceWire] = []
    for resource in inventory.resources:
        rows = sorted(
            rows_by_resource.get(resource.resource_key, []),
            key=lambda item: (item.platform, item.local_instance_id, item.local_locator),
        )
        if not resource.remote.exists and not rows:
            continue
        resources.append(
            _resource_wire(
                resource,
                rows,
                legacy_write_blocker_ref=legacy_write_blocker_ref,
            )
        )
    return sorted(resources, key=lambda item: (item.kind, item.name))


def _resource_wire(
    resource: AssetResourceRow,
    rows: list[AssetPlatformRow],
    legacy_write_blocker_ref: UiMessageRef | None,
) -> AssetReconcileResourceWire:
    manifest_issues: list[AssetReconcileIssueWire] = []
    remote_row = next((row for row in rows if row.remote_exists), None)
    if resource.kind not in {
        "skill",
        "mcp",
        "rule",
        "prompt",
        "plugin",
        "instruction",
        "memory",
    }:
        remote_manifest, remote_manifest_issues = (
            _empty_manifest("reference-metadata", complete=True),
            [],
        )
    else:
        remote_manifest, remote_manifest_issues = _manifest_for_side(
            remote_row,
            "remote",
            fallback_path=resource.remote.path,
            kind=resource.kind,
            plugin_track=resource.plugin_track,
        )
    manifest_issues.extend(remote_manifest_issues)

    local_rows = [row for row in rows if row.local_exists]
    local_descriptions = {item.id: item.description for item in resource.local_instances}
    variant_groups = _variant_groups(local_rows)
    local_instances: list[AssetReconcileLocalInstanceWire] = []
    for row in local_rows:
        manifest, issues = _manifest_for_side(row, "local", kind=row.kind, plugin_track=row.plugin_track)
        local_issues = _bounded_issues(
            _issues_from_refs(row.warning_refs, "warning", "manifest")
            + _issues_from_refs(row.blocker_refs, "blocker", "manifest")
            + issues
        )
        ownership = row.ownership if row.ownership in {"managed", "unmanaged", "unknown"} else "unknown"
        local_instances.append(
            AssetReconcileLocalInstanceWire(
                local_instance_id=row.local_instance_id,
                profile_id=row.platform,
                tool_id=_safe_text(row.tool_id),
                environment_kind=_safe_text(row.environment_kind),
                environment_name=_safe_text(row.environment_name),
                display_name=_safe_text(row.display_name),
                description=_safe_text(local_descriptions.get(row.local_instance_id, ""))[:512],
                origin=(
                    "plugin-expected"
                    if row.local_locator == "plugin-expected"
                    else "plugin-discovered"
                    if row.local_locator.startswith("plugin-")
                    else "expected-target"
                    if row.local_locator == "expected"
                    else "discovered-local"
                ),
                ownership=ownership,
                variant_group=variant_groups.get(row.local_instance_id, ""),
                path_kind=row.path_kind,
                link_health=row.link_health,
                link_target_trusted=row.link_target_trusted,
                status=row.status,
                manifest=manifest,
                issues=local_issues,
            )
        )

    comparisons = [
        _comparison_wire(row, legacy_write_blocker_ref=legacy_write_blocker_ref)
        for row in rows
    ]
    resource_issues = (
        _issues_from_refs(resource.warning_refs, "warning", "comparison")
        + _issues_from_refs(resource.blocker_refs, "blocker", "comparison")
        + manifest_issues
    )
    all_issue_count = len(resource_issues)
    plugin = (
        AssetReconcilePluginWire(
            track=resource.plugin_track,
            platform=resource.plugin_platform,
            plugin_id=resource.plugin_id,
        )
        if resource.kind == "plugin"
        else None
    )
    return AssetReconcileResourceWire(
        resource_key=resource.resource_key,
        kind=resource.kind,
        name=resource.name,
        resource_status=resource.status,
        local_multiplicity=resource.local_status,
        remote=AssetReconcileRemoteWire(
            exists=resource.remote.exists,
            status=resource.remote.status,
            writable=resource.remote.writable,
            read_only=resource.remote.read_only,
            commit=resource.remote.commit,
            description=_safe_text(resource.remote.description)[:512],
            manifest=remote_manifest,
        ),
        local_instances=local_instances,
        comparisons=comparisons,
        plugin=plugin,
        issues=_bounded_issues(resource_issues),
        issues_truncated=all_issue_count > ASSET_RECONCILE_MAX_ISSUES,
    )


def _comparison_wire(
    row: AssetPlatformRow,
    *,
    legacy_write_blocker_ref: UiMessageRef | None,
) -> AssetReconcileComparisonWire:
    action_checks = [
        _action_check(
            row,
            "upload",
            legacy_write_blocker_ref=legacy_write_blocker_ref,
        ),
        _action_check(
            row,
            "download",
            legacy_write_blocker_ref=legacy_write_blocker_ref,
        ),
    ]
    issues = _bounded_issues(
        _issues_from_refs(row.warning_refs, "warning", "comparison")
        + _issues_from_refs(row.blocker_refs, "blocker", "comparison")
    )
    return AssetReconcileComparisonWire(
        profile_id=row.platform,
        local_instance_id=row.local_instance_id if row.local_exists else "",
        comparison_status=row.status,
        metadata_differences=[_safe_text(value) for value in row.metadata_differences],
        diff_available=bool(
            row.remote_exists
            and row.local_exists
            and row.status in {"content-different", "metadata-only"}
            and row.local_instance_id
            and row.local_fingerprint
            and row.remote_content_fingerprint
        ),
        action_checks=action_checks,
        baseline=AssetReconcileBaselineWire(),
        issues=issues,
    )


def _action_check(
    row: AssetPlatformRow,
    action: Literal["upload", "download"],
    *,
    legacy_write_blocker_ref: UiMessageRef | None,
) -> AssetReconcileActionCheckWire:
    if action == "upload" and not row.local_exists:
        return AssetReconcileActionCheckWire(action=action, state="not-applicable", issues=[])
    if action == "download" and not row.remote_exists:
        return AssetReconcileActionCheckWire(action=action, state="not-applicable", issues=[])

    plan_refs = (
        _upload_plan_blocker_refs(row, False)
        if action == "upload"
        else _download_plan_blocker_refs(row, False)
    )
    refs = _unique_refs([*row.blocker_refs, *plan_refs])
    if action == "upload" and legacy_write_blocker_ref is not None:
        refs = _unique_refs([*refs, legacy_write_blocker_ref])
    confirmations = [
        _CONFIRMATION_CODES[ref.code] for ref in refs if ref.code in _CONFIRMATION_CODES
    ]
    hard_refs = [ref for ref in refs if ref.code not in _CONFIRMATION_CODES]
    if action == "upload" and row.kind in {"instruction", "memory"} and not row.tool_id:
        hard_refs.append(
            UiMessageRef(
                code="asset.blocker.portable_tool_identity_missing",
                fallback="The local source tool identity is unavailable.",
            )
        )
    if action == "upload" and row.kind == "memory" and row.tool_id != "claude-code":
        hard_refs.append(
            UiMessageRef(
                code="asset.blocker.memory_source_tool_invalid",
                fallback="Memory resources can only originate from Claude Code.",
            )
        )

    issues = _issues_from_refs(
        refs + [ref for ref in hard_refs if ref not in refs],
        "blocker",
        action,
    )
    if hard_refs:
        state: Literal["not-applicable", "eligible", "needs-confirmation", "blocked"] = "blocked"
    elif confirmations:
        state = "needs-confirmation"
    elif action in row.available_actions:
        state = "eligible"
    else:
        state = "blocked"
        issues.append(
            _issue(
                f"asset.reconcile.{action}_unavailable",
                "blocker",
                action,
                f"The resource is not eligible for {action} in this profile.",
            )
        )
    return AssetReconcileActionCheckWire(
        action=action,
        state=state,
        required_confirmations=list(dict.fromkeys(confirmations)),
        issues=_bounded_issues(issues),
    )


def _manifest_for_side(
    row: AssetPlatformRow | None,
    side: Literal["local", "remote"],
    *,
    fallback_path: Path | None = None,
    kind: str = "",
    plugin_track: str = "",
) -> tuple[AssetManifestWire, list[AssetReconcileIssueWire]]:
    if plugin_track == "reference":
        return _empty_manifest("reference-metadata", complete=True), []
    if row is None:
        if fallback_path is None:
            return _empty_manifest("none", complete=True), []
        return _manifest_from_path(fallback_path, include_excluded=kind == "memory")

    if side == "local" and not row.local_fingerprint:
        return _empty_manifest("files", complete=False), [
            _issue(
                "asset.reconcile.manifest_local_identity_unavailable",
                "blocker",
                "manifest",
                "The local asset cannot be fingerprinted safely, so its files were not read.",
            )
        ]

    if row.kind == "mcp":
        try:
            if side == "remote":
                value = (
                    sanitize_mcp_config_for_storage(row.entry.mcp_config)
                    if row.entry is not None and row.entry.mcp_config is not None
                    else None
                )
            else:
                source = row.local_content_path or row.local_path
                value = sanitize_mcp_config_for_storage(
                    _read_mcp_server(source, row.install_name) if source is not None else None
                )
            if value is None:
                return _empty_manifest("normalized-config", complete=False), [
                    _issue(
                        "asset.reconcile.manifest_unavailable",
                        "blocker",
                        "manifest",
                        "The normalized MCP configuration is unavailable.",
                    )
                ]
            data = (json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n").encode("utf-8")
            return _manifest_from_blobs({"mcp.json": (data, len(data), False)}, "normalized-config")
        except Exception as exc:
            return _manifest_failure(exc)

    source = (
        row.local_content_path or row.local_path
        if side == "local"
        else row.remote_path
    )
    if side == "remote" and row.kind == "plugin" and row.entry is not None and row.entry.plugin:
        source = _plugin_remote_content_source(source, row.entry.plugin)
    if side == "remote" and row.kind == "prompt" and source is not None:
        payload, problem = _prompt_payload_path(source)
        if payload is None:
            return _empty_manifest("files", complete=False), [
                _issue(
                    "asset.reconcile.manifest_unavailable",
                    "blocker",
                    "manifest",
                    problem or "The Prompt payload is unavailable.",
                )
            ]
        source = payload
    if source is None:
        return _empty_manifest("none", complete=True), []
    return _manifest_from_path(source, include_excluded=row.kind == "memory")


def _manifest_from_path(
    root: Path,
    *,
    include_excluded: bool = False,
) -> tuple[AssetManifestWire, list[AssetReconcileIssueWire]]:
    root_probe = probe_local_path(root)
    if root_probe.health == "missing":
        return _empty_manifest("none", complete=True), []
    if (
        not root_probe.ready
        or root_probe.content_path is None
        or root_probe.path_kind != "regular"
    ):
        return _empty_manifest("files", complete=False), [
            _issue(
                "asset.reconcile.manifest_link_unsafe",
                "blocker",
                "manifest",
                "Link, reparse-point, or unreadable content cannot be summarized safely.",
            )
        ]
    root = root_probe.content_path
    try:
        if root.is_file():
            data, size, truncated = _read_manifest_file(root)
            return _manifest_from_blobs({root.name: (data, size, truncated)}, "files")

        blobs: dict[str, tuple[bytes, int, bool]] = {}
        total_count = 0
        total_bytes = 0
        link_seen = False
        unreadable_seen = False
        walk_errors: list[OSError] = []
        for directory, dirnames, filenames in os.walk(
            root,
            followlinks=False,
            onerror=walk_errors.append,
        ):
            current = Path(directory)
            safe_dirs: list[str] = []
            for name in sorted(dirnames):
                item = current / name
                relative = item.relative_to(root)
                item_probe = probe_local_path(item)
                if (
                    not item_probe.ready
                    or item_probe.content_path is None
                    or item_probe.path_kind != "regular"
                ):
                    link_seen = True
                    continue
                if not include_excluded and is_resource_path_excluded(relative):
                    continue
                safe_dirs.append(name)
            dirnames[:] = safe_dirs
            for name in sorted(filenames):
                item = current / name
                relative = item.relative_to(root)
                item_probe = probe_local_path(item)
                if (
                    not item_probe.ready
                    or item_probe.content_path is None
                    or item_probe.path_kind != "regular"
                    or not item_probe.content_path.is_file()
                ):
                    link_seen = True
                    continue
                if not include_excluded and is_resource_path_excluded(relative):
                    continue
                try:
                    size = item.stat().st_size
                    total_count += 1
                    total_bytes += size
                    if len(blobs) < ASSET_DIFF_MAX_FILES:
                        data, _size, truncated = _read_manifest_file(item)
                        blobs[relative.as_posix()] = (data, size, truncated)
                except OSError:
                    unreadable_seen = True
        unreadable_seen = unreadable_seen or bool(walk_errors)
        manifest, issues = _manifest_from_blobs(
            blobs,
            "files",
            source_truncated=total_count > ASSET_DIFF_MAX_FILES or link_seen or unreadable_seen,
            total_count=total_count,
            total_bytes=total_bytes,
        )
        if link_seen:
            issues.append(
                _issue(
                    "asset.reconcile.manifest_nested_link",
                    "blocker",
                    "manifest",
                    "Nested link content was excluded from the manifest.",
                )
            )
        if unreadable_seen:
            issues.append(
                _issue(
                    "asset.reconcile.manifest_unreadable",
                    "blocker",
                    "manifest",
                    "One or more files could not be read safely.",
                )
            )
        return manifest, issues
    except (OSError, ValueError) as exc:
        return _manifest_failure(exc)


def _read_manifest_file(path: Path) -> tuple[bytes, int, bool]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        data = handle.read(ASSET_DIFF_MAX_FILE_BYTES + 1)
    truncated = len(data) > ASSET_DIFF_MAX_FILE_BYTES
    return data[:ASSET_DIFF_MAX_FILE_BYTES], size, truncated


def _manifest_from_blobs(
    blobs: dict[str, tuple[bytes, int, bool]],
    mode: Literal["files", "normalized-config"],
    *,
    source_truncated: bool = False,
    total_count: int | None = None,
    total_bytes: int | None = None,
) -> tuple[AssetManifestWire, list[AssetReconcileIssueWire]]:
    entries: list[AssetManifestEntryWire] = []
    issues: list[AssetReconcileIssueWire] = []
    complete = not source_truncated
    tree_values: list[tuple[str, int, str]] = []
    for relative_path, (data, size, truncated) in sorted(blobs.items()):
        binary = _is_binary(data)
        secret = None
        if not binary and not truncated:
            try:
                secret = find_secret_text(data.decode("utf-8"))
            except UnicodeDecodeError:
                binary = True
        if truncated:
            hash_status = "unsupported"
            digest = ""
            complete = False
        elif secret is not None:
            hash_status = "withheld-secret"
            digest = ""
            complete = False
            issues.append(
                _issue(
                    "asset.reconcile.manifest_secret_withheld",
                    "blocker",
                    "manifest",
                    f"A secret-like value was detected in {relative_path}; its hash was withheld.",
                )
            )
        else:
            hash_status = "available"
            digest = hashlib.sha256(data).hexdigest()
            tree_values.append((relative_path, size, digest))
        entries.append(
            AssetManifestEntryWire(
                relative_path=_safe_relative_path(relative_path),
                size_bytes=size,
                sha256=digest,
                content_kind="binary" if binary else "text",
                hash_status=hash_status,
            )
        )
    entry_count = len(blobs) if total_count is None else total_count
    byte_count = sum(value[1] for value in blobs.values()) if total_bytes is None else total_bytes
    entries_truncated = source_truncated or entry_count > len(entries)
    if entries_truncated:
        complete = False
    tree_sha = ""
    if complete and len(tree_values) == entry_count:
        tree_sha = hashlib.sha256(
            json.dumps(tree_values, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    return (
        AssetManifestWire(
            mode=mode,
            complete=complete,
            entry_count=entry_count,
            total_bytes=byte_count,
            entries_truncated=entries_truncated,
            tree_sha256=tree_sha,
            entries=entries,
        ),
        issues,
    )


def _empty_manifest(
    mode: Literal["files", "normalized-config", "reference-metadata", "none"],
    *,
    complete: bool,
) -> AssetManifestWire:
    return AssetManifestWire(
        mode=mode,
        complete=complete,
        entry_count=0,
        total_bytes=0,
        entries_truncated=False,
        tree_sha256=hashlib.sha256(b"[]").hexdigest() if complete and mode != "none" else "",
        entries=[],
    )


def _manifest_failure(
    exc: Exception,
) -> tuple[AssetManifestWire, list[AssetReconcileIssueWire]]:
    return _empty_manifest("files", complete=False), [
        _issue(
            "asset.reconcile.manifest_unavailable",
            "blocker",
            "manifest",
            "The asset manifest is unavailable or could not be read safely.",
        )
    ]


def _variant_groups(rows: list[AssetPlatformRow]) -> dict[str, str]:
    fingerprints = sorted({row.local_fingerprint for row in rows if row.local_fingerprint})
    groups = {fingerprint: f"variant-{index + 1}" for index, fingerprint in enumerate(fingerprints)}
    return {
        row.local_instance_id: groups.get(row.local_fingerprint, "variant-unknown")
        for row in rows
    }


def _build_summary(
    resources: list[AssetReconcileResourceWire],
    coverage: list[AssetReconcileProfileCoverageWire],
) -> AssetReconcileSummaryWire:
    kind_counts = Counter(item.kind for item in resources)
    status_counts = Counter(item.resource_status for item in resources)
    review_statuses = {"content-different", "metadata-only", "target-conflict", "uncomparable"}
    blocked_count = sum(
        resource.remote.read_only
        or not resource.comparisons
        or any(
            check.state == "blocked"
            for comparison in resource.comparisons
            for check in comparison.action_checks
        )
        or any(issue.severity == "blocker" for issue in resource.issues)
        for resource in resources
    )
    return AssetReconcileSummaryWire(
        logical_resource_count=len(resources),
        comparison_count=sum(len(item.comparisons) for item in resources),
        profile_count=len(coverage),
        kind_counts=dict(sorted(kind_counts.items())),
        status_counts=dict(sorted(status_counts.items())),
        same_count=status_counts.get("same", 0),
        local_only_count=status_counts.get("local-only", 0),
        remote_only_count=status_counts.get("remote-only", 0),
        review_count=sum(status_counts.get(status, 0) for status in review_statuses),
        blocked_count=blocked_count,
    )


def _completeness(
    inventory: AssetInventory,
    coverage: list[AssetReconcileProfileCoverageWire],
    resources: list[AssetReconcileResourceWire],
    scope: AssetReconcileScopeWire,
) -> Literal["complete", "partial", "blocked"]:
    registry_status = inventory.registry_health.status if inventory.registry_health else "unavailable"
    if not inventory.remote_available or registry_status not in {"healthy", "issues"}:
        return "blocked"
    if any(item.scan_state in {"partial", "unavailable"} for item in coverage):
        return "partial"
    if scope.unavailable_saved_project_count:
        return "partial"
    if any(
        not resource.remote.manifest.complete
        or any(not local.manifest.complete for local in resource.local_instances)
        for resource in resources
    ):
        return "partial"
    return "complete"


def _issues_from_refs(
    refs: list[UiMessageRef],
    severity: Literal["warning", "blocker"],
    scope: Literal["scan", "comparison", "upload", "download", "manifest", "diff"],
) -> list[AssetReconcileIssueWire]:
    return [_issue(ref.code, severity, scope, ref.fallback) for ref in _unique_refs(refs)]


def _issue(
    code: str,
    severity: Literal["warning", "blocker"],
    scope: Literal["scan", "comparison", "upload", "download", "manifest", "diff"],
    message: str,
) -> AssetReconcileIssueWire:
    return AssetReconcileIssueWire(
        code=code or "asset.reconcile.issue",
        severity=severity,
        scope=scope,
        message=_safe_text(message)[:ASSET_RECONCILE_MAX_MESSAGE_CHARS]
        or "Asset reconciliation could not complete this check.",
    )


def _bounded_issues(issues: list[AssetReconcileIssueWire]) -> list[AssetReconcileIssueWire]:
    unique: list[AssetReconcileIssueWire] = []
    seen: set[tuple[str, str, str, str]] = set()
    for issue in issues:
        key = (issue.code, issue.severity, issue.scope, issue.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
        if len(unique) >= ASSET_RECONCILE_MAX_ISSUES:
            break
    return unique


def _unique_refs(refs: list[UiMessageRef]) -> list[UiMessageRef]:
    unique: list[UiMessageRef] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        key = (ref.code, ref.fallback)
        if key not in seen:
            seen.add(key)
            unique.append(ref)
    return unique


def _safe_text(value: object) -> str:
    redacted = redact_secret_text(str(value or ""))
    redacted = _GENERIC_POSIX_PATH_IN_TEXT.sub("${PRIVATE_PATH}", redacted)
    public = to_public_wire_value(redacted)
    return public if isinstance(public, str) else ""


def _safe_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip("/")
    if not normalized or normalized.startswith("../") or "/../" in f"/{normalized}/":
        return "${PRIVATE_PATH}"
    return _safe_text(normalized)


def _is_binary(data: bytes) -> bool:
    if b"\0" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False
