"""Structured JSON API used by the desktop application.

The CLI is optimized for humans and Rich tables.  This module exposes the
same core operations as stable JSON so desktop shells do not need to parse
terminal output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - py310 fallback
    import tomli as tomllib

from .. import __version__
from ..core.config import (
    Config,
    default_config_path,
    load_raw_config,
    new_default_config,
    write_config,
)
from ..core.config import (
    load_config as load_application_config,
)
from ..core.models import ItemKind, RegistryItem
from ..core.platforms import PLATFORM_PRESETS, PlatformProfile, PlatformsConfig, build_platform
from ..core.registry import find_registry_path, load_registry
from ..core.resource_detection import detect_local_resource_type, detect_remote_resource
from ..infrastructure import git_ops
from ..services import publisher
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
)
from ..services.doctor import build_doctor_checks
from ..services.installer import check_all, preview_sync_all, status_all, sync_all, uninstall_one
from ..services.local_resources import import_local_resource
from ..services.operation_history import (
    operation_detail,
    operation_history,
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
    apply_registry_repair,
    build_registry_repair_plan,
)
from ..services.resource_binding import bind_resource_repo, parse_github_repo_url
from ..services.resource_commit import build_resource_commit_plan
from ..services.resource_discovery import (
    discover_resources,
    read_discovered_resource,
    resolve_discovered_resources,
)
from ..services.resource_manager import (
    build_resource_inventory,
    delete_resource,
    install_resource,
    preview_resource,
    resource_delete_requires_remote_scope,
    resource_install_plan,
    resource_open_path,
    uninstall_resource,
)
from ..services.resource_repo import (
    inspect_resource_repo,
    pull_resource_repo,
    push_resource_repo,
)
from ..services.resource_sync import (
    apply_resource_sync_plan,
    build_resource_sync_plan,
    cancel_resource_sync_plan,
    cleanup_stale_resource_sync_plan,
    inspect_resource_sync,
    list_stale_resource_sync_plans,
    push_resource_sync,
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
from ..services.state_retention import build_state_retention_plan, prune_state
from ..services.ui_messages import UiMessageRef, ui_message

JsonDict = dict[str, Any]
Handler = Callable[[JsonDict], Any]
DESKTOP_PAYLOAD_ENV_VAR = "CC_PORT_DESKTOP_API_PAYLOAD"


class DesktopRemoteRepositoryMutationError(RuntimeError):
    """Raised when a desktop action would mutate a GitHub repository container."""


def load_config() -> Config:
    """Load a desktop-safe config copy that cannot use application-managed tokens."""
    cfg = load_application_config()
    cfg.github.token = ""
    return cfg


ITEM_KINDS = {"skill", "mcp", "rule", "prompt", "plugin"}
DEPRECATED_ACTIONS = {
    "resource_commit_plan",
    "resource_commit_push",
    "resource_sync_status",
    "resource_sync_plan",
    "resource_sync_resolve",
    "resource_sync_apply",
    "resource_sync_cancel",
    "resource_sync_push",
    "resource_sync_stale",
    "resource_sync_cleanup",
}
DEPRECATED_SYNC_MESSAGE = (
    "Deprecated: use asset_inventory, asset_action_plan, and asset_action_apply. "
    "The Git workspace sync API will be removed in the next release."
)


def run_action(action: str, payload: JsonDict | None = None) -> JsonDict:
    """Run one desktop API action and return a JSON-serializable envelope."""
    data = payload or {}
    runtime_config = load_config()
    git_ops.configure_git_executable(runtime_config.git.executable)
    try:
        handler = ACTIONS[action]
    except KeyError:
        message_ref = ui_message(
            "api.unknown_action",
            f"Unknown UI API action: {action}",
            action=action,
        )
        return _error("unknown_action", message_ref.fallback, message_ref)

    try:
        result = {"ok": True, "data": _to_jsonable(handler(data))}
        if action in DEPRECATED_ACTIONS:
            result["deprecated"] = True
            result["warnings"] = [DEPRECATED_SYNC_MESSAGE]
        return result
    except Exception as exc:  # noqa: BLE001 - desktop needs a structured error boundary
        return _error(
            exc.__class__.__name__,
            str(exc),
            _exception_message_ref(exc),
        )


def _summary(_: JsonDict) -> JsonDict:
    cfg = load_config()
    raw_cfg = load_raw_config()
    registry_path = find_registry_path()
    registry = load_registry(registry_path)
    resource = inspect_resource_repo(cfg)
    statuses = status_all(config=cfg, registry=registry, registry_path=registry_path)
    return {
        "version": __version__,
        "config": _config_summary(cfg),
        "registry_path": str(registry_path),
        "resource_repo": resource,
        "resource_repo_display_name": _configured_resource_repo_name(raw_cfg),
        "counts": _registry_counts(registry.items),
        "updates": sum(1 for item in statuses if item.has_update),
        "installed": sum(1 for item in statuses if item.installed),
    }


def _list_items(payload: JsonDict) -> JsonDict:
    cfg = load_config()
    kind = _optional_str(payload.get("kind"))
    registry_path = find_registry_path()
    registry = load_registry(registry_path)
    items = [item for item in registry.items if not kind or item.kind == kind]
    statuses = {
        s.resource_key: s
        for s in status_all(config=cfg, registry=registry, registry_path=registry_path)
    }
    return {
        "registry_path": str(registry_path),
        "items": [
            {
                **item.model_dump(mode="json"),
                "status": _to_jsonable(statuses.get(item.resource_key)),
            }
            for item in items
        ],
    }


def _resource_status(_: JsonDict) -> Any:
    return inspect_resource_repo(load_config())


def _platforms(_: JsonDict) -> JsonDict:
    cfg = load_config()
    return {"platforms": cfg.platforms.profiles}


def _doctor(_: JsonDict) -> JsonDict:
    checks = build_doctor_checks(load_config())
    return {"checks": [check for check in checks if check.get("id") != "github_token"]}


def _collect(payload: JsonDict) -> JsonDict:
    github_url = _required_str(payload, "github_url")
    kind = _required_kind(payload)
    cfg = load_config()
    detected = detect_remote_resource(
        github_url,
        explicit_type=kind,
        token=None,
    )
    raw_mcp_config = payload.get("mcp_config")
    if raw_mcp_config is not None and not isinstance(raw_mcp_config, dict):
        raise ValueError("mcp_config must be a mapping.")
    mcp_config = raw_mcp_config if isinstance(raw_mcp_config, dict) else None
    if detected.kind == "mcp" and not mcp_config:
        raise ValueError(
            "GitHub MCP references require a portable mcp_config with command or url."
        )
    if detected.kind != "mcp" and mcp_config is not None:
        raise ValueError("mcp_config is only valid when the collected resource kind is mcp.")
    entry = publisher.add_external_skill(
        detected.repo_url,
        name=_optional_str(payload.get("name")) or detected.name_hint,
        subdir=detected.subdir,
        ref=detected.ref,
        kind=detected.kind,
        mcp_config=mcp_config,
        skip_verify=bool(payload.get("skip_verify", False)),
        token=None,
        tags=detected.tags,
        platforms=_str_list(payload.get("platforms")),
    )
    push_result = _maybe_push(cfg, payload)
    return {"entry": entry, "detected": detected, "push": push_result}


def _upload(payload: JsonDict) -> JsonDict:
    source = Path(_required_str(payload, "path")).expanduser()
    kind = detect_local_resource_type(source, explicit_type=_optional_str(payload.get("kind")))
    result = import_local_resource(
        source,
        kind=kind,
        name=_optional_str(payload.get("name")),
        platforms=_str_list(payload.get("platforms")),
        overwrite=bool(payload.get("overwrite", False)),
    )
    push_result = _push_after_upload(load_config(), payload)
    return {
        "entry": result.entry,
        "source_path": result.source_path,
        "stored_path": result.stored_path,
        "push": push_result,
    }


def _discover_resources(payload: JsonDict) -> JsonDict:
    scope = _optional_str(payload.get("scope")) or "global"
    root_path = _optional_str(payload.get("root_path"))
    items = discover_resources(scope=scope, root_path=root_path)
    return {"scope": scope, "root_path": root_path or "", "items": items}


def _read_discovered_resource(payload: JsonDict) -> Any:
    return read_discovered_resource(
        _required_str(payload, "id"),
        scope=_optional_str(payload.get("scope")) or "global",
        root_path=_optional_str(payload.get("root_path")),
    )


def _upload_discovered_resources(payload: JsonDict) -> JsonDict:
    selections = _discovery_selections(payload.get("items"))
    if not selections:
        raise ValueError("Missing required field: items")

    candidates = resolve_discovered_resources(
        [item["id"] for item in selections],
        scope=_optional_str(payload.get("scope")) or "global",
        root_path=_optional_str(payload.get("root_path")),
    )
    overwrite = bool(payload.get("overwrite", False))
    results: list[JsonDict] = []
    imported = 0

    for selection, candidate in zip(selections, candidates, strict=True):
        name = _optional_str(selection.get("name")) or candidate.name_hint
        if candidate.blockers or candidate.content_path is None:
            results.append(
                {
                    "id": candidate.id,
                    "name": name,
                    "kind": candidate.kind,
                    "path": candidate.path,
                    "ok": False,
                    "error": "; ".join(candidate.blockers)
                    or "The local resource path cannot be read safely.",
                }
            )
            continue
        if (
            candidate.path_kind in {"symlink", "junction"}
            and not candidate.link_target_trusted
            and not bool(selection.get("link_target_confirmed", False))
        ):
            results.append(
                {
                    "id": candidate.id,
                    "name": name,
                    "kind": candidate.kind,
                    "path": candidate.path,
                    "ok": False,
                    "error": (
                        "Confirm this external link target before uploading its contents: "
                        f"{candidate.content_path}"
                    ),
                }
            )
            continue
        try:
            result = import_local_resource(
                candidate.content_path,
                kind=candidate.kind,
                name=name,
                overwrite=overwrite,
            )
        except Exception as exc:  # noqa: BLE001 - batch uploads report per-item failures
            results.append(
                {
                    "id": candidate.id,
                    "name": name,
                    "kind": candidate.kind,
                    "path": candidate.path,
                    "ok": False,
                    "error": str(exc),
                }
            )
            continue

        imported += 1
        results.append(
            {
                "id": candidate.id,
                "name": result.entry.name,
                "kind": result.entry.kind,
                "path": candidate.path,
                "ok": True,
                "entry": result.entry,
                "source_path": result.source_path,
                "stored_path": result.stored_path,
            }
        )

    push_result = _push_after_upload(load_config(), payload) if imported else None
    return {
        "results": results,
        "imported": imported,
        "failed": len(results) - imported,
        "push": push_result,
    }


def _sync(payload: JsonDict) -> JsonDict:
    include_kinds = set(_str_list(payload.get("include_kinds")))
    results = sync_all(
        config=load_config(),
        only=_str_list(payload.get("only")) or None,
        kind=_optional_str(payload.get("kind")),
        tags=_str_list(payload.get("tags")) or None,
        include_optional=bool(payload.get("all_kinds", False)),
        include_kinds=include_kinds or None,
        platform_filter=_optional_str(payload.get("platform")),
    )
    return {"results": results}


def _sync_preview(payload: JsonDict) -> Any:
    include_kinds = set(_str_list(payload.get("include_kinds")))
    return preview_sync_all(
        config=load_config(),
        only=_str_list(payload.get("only")) or None,
        kind=_optional_str(payload.get("kind")),
        tags=_str_list(payload.get("tags")) or None,
        include_optional=bool(payload.get("all_kinds", False)),
        include_kinds=include_kinds or None,
        platform_filter=_optional_str(payload.get("platform")),
    )


def _resource_inventory(payload: JsonDict) -> Any:
    return build_resource_inventory(
        config=load_config(),
        kind=_optional_str(payload.get("kind")),
    )


def _asset_inventory(payload: JsonDict) -> Any:
    inventory = build_asset_inventory(
        config=load_config(),
        scan_local=bool(payload.get("scan_local", False)),
        refresh_remote=bool(payload.get("refresh_remote", True)),
        scan_global=bool(payload.get("scan_global", True)),
        project_ids=_str_list(payload.get("project_ids")) if "project_ids" in payload else None,
    )
    response = asdict(inventory)
    response.pop("rows", None)
    return response


def _registry_repair_plan(payload: JsonDict) -> Any:
    return build_registry_repair_plan(
        config=load_config(),
        choices=_registry_repair_choices(payload.get("choices")),
    )


def _registry_repair_apply(payload: JsonDict) -> Any:
    return apply_registry_repair(
        expected_plan_hash=_required_str(payload, "plan_hash"),
        config=load_config(),
        choices=_registry_repair_choices(payload.get("choices")),
    )


def _asset_content_diff(payload: JsonDict) -> Any:
    return build_asset_content_diff(
        _required_str(payload, "resource_key"),
        _required_str(payload, "local_instance_id"),
        config=load_config(),
    )


def _plugin_projects_list(_: JsonDict) -> Any:
    return {"projects": list_plugin_projects(load_raw_config())}


def _plugin_projects_add(payload: JsonDict) -> Any:
    return add_plugin_project(_required_str(payload, "path"))


def _plugin_projects_remove(payload: JsonDict) -> Any:
    return remove_plugin_project(_required_str(payload, "project_id"))


def _plugin_reference_add(payload: JsonDict) -> Any:
    return add_plugin_reference(
        platform=_required_str(payload, "platform"),
        plugin_id=_required_str(payload, "plugin_id"),
        origin_type=_required_str(payload, "origin_type"),
        scope=_optional_str(payload.get("scope")) or "user",
        enabled=bool(payload.get("enabled", True)),
        marketplace=_optional_str(payload.get("marketplace")) or "",
        source=_optional_str(payload.get("source")) or "",
        package=_optional_str(payload.get("package")) or "",
        repo=_optional_str(payload.get("repo")) or "",
        selector=_optional_str(payload.get("selector")) or "",
        observed_version=_optional_str(payload.get("observed_version")) or "",
        project_id=_optional_str(payload.get("project_id")) or "",
        name=_optional_str(payload.get("name")) or "",
        description=_optional_str(payload.get("description")) or "",
        push=bool(payload.get("push", True)),
        config=load_config(),
    )


def _plugin_delete_plan(payload: JsonDict) -> Any:
    return build_plugin_delete_plan(
        _required_str(payload, "resource_key"),
        selected_instance_ids=_str_list(payload.get("instance_ids")) or None,
        config=load_config(),
    )


def _plugin_delete_apply(payload: JsonDict) -> Any:
    return apply_plugin_delete_plan(
        _required_str(payload, "resource_key"),
        selected_instance_ids=_str_list(payload.get("instance_ids")),
        expected_plan_hash=_required_str(payload, "plan_hash"),
        config=load_config(),
    )


def _asset_action_plan(payload: JsonDict) -> Any:
    return build_asset_action_plan(
        _required_str(payload, "action"),
        kind=_required_kind(payload),
        name=_required_str(payload, "name"),
        platform=_required_str(payload, "platform"),
        local_instance_id=_optional_str(payload.get("local_instance_id")) or "",
        new_name=_optional_str(payload.get("new_name")) or "",
        new_install_name=_optional_str(payload.get("new_install_name")) or "",
        overwrite_unmanaged=bool(payload.get("overwrite_unmanaged", False)),
        link_target_confirmed=bool(payload.get("link_target_confirmed", False)),
        config=load_config(),
    )


def _asset_action_apply(payload: JsonDict) -> Any:
    return apply_asset_action_plan(
        _required_str(payload, "operation_id"),
        config=load_config(),
    )


def _asset_batch_plan(payload: JsonDict) -> Any:
    return build_asset_batch_plan(
        _required_str(payload, "direction"),
        resource_keys=_str_list(payload.get("resource_keys")),
        target_platforms=_str_list(payload.get("target_platforms")),
        choices=_asset_batch_choices(payload.get("choices")),
        config=load_config(),
    )


def _asset_batch_apply(payload: JsonDict) -> Any:
    return apply_asset_batch_plan(
        _required_str(payload, "direction"),
        resource_keys=_str_list(payload.get("resource_keys")),
        target_platforms=_str_list(payload.get("target_platforms")),
        choices=_asset_batch_choices(payload.get("choices")),
        expected_plan_hash=_required_str(payload, "plan_hash"),
        config=load_config(),
    )


def _resource_install(payload: JsonDict) -> Any:
    return install_resource(
        _required_str(payload, "name"),
        config=load_config(),
        platform_filter=_optional_str(payload.get("platform")),
        kind=_optional_str(payload.get("kind")),
    )


def _resource_install_plan(payload: JsonDict) -> Any:
    return resource_install_plan(
        _required_str(payload, "name"),
        config=load_config(),
        platform_filter=_optional_str(payload.get("platform")),
        kind=_optional_str(payload.get("kind")),
    )


def _resource_uninstall(payload: JsonDict) -> Any:
    return uninstall_resource(
        _required_str(payload, "name"),
        config=load_config(),
        platform_filter=_optional_str(payload.get("platform")),
        kind=_optional_str(payload.get("kind")),
    )


def _resource_preview(payload: JsonDict) -> Any:
    return preview_resource(
        _required_str(payload, "name"),
        config=load_config(),
        platform_filter=_optional_str(payload.get("platform")),
        kind=_optional_str(payload.get("kind")),
    )


def _resource_open_path(payload: JsonDict) -> JsonDict:
    path = resource_open_path(
        _required_str(payload, "name"),
        config=load_config(),
        platform_filter=_optional_str(payload.get("platform")),
        kind=_optional_str(payload.get("kind")),
    )
    return {"path": path}


def _resource_delete(payload: JsonDict) -> Any:
    cfg = load_config()
    name = _required_str(payload, "name")
    kind = _optional_str(payload.get("kind"))
    if resource_delete_requires_remote_scope(name, kind=kind):
        raise DesktopRemoteRepositoryMutationError(
            "CC Port does not delete GitHub repositories. "
            "Delete the repository on GitHub, or remove only its local/indexed resource."
        )
    deleted = delete_resource(
        name,
        config=cfg,
        confirm_name=_optional_str(payload.get("confirm_name")),
        reason=_optional_str(payload.get("reason")) or "",
        kind=kind,
    )
    return {
        **asdict(deleted),
        "push": push_resource_repo(
            message=f"cc-port: delete resource {deleted.name}",
            config=cfg,
        ),
    }


def _check(payload: JsonDict) -> JsonDict:
    results, pruned = check_all(
        config=load_config(),
        kind=_optional_str(payload.get("kind")),
        prune=bool(payload.get("prune", False)),
        uninstall=bool(payload.get("uninstall", False)),
    )
    return {"results": results, "pruned": pruned}


def _remove(payload: JsonDict) -> JsonDict:
    name = _required_str(payload, "name")
    kind = _optional_str(payload.get("kind"))
    cfg = load_config()
    if resource_delete_requires_remote_scope(name, kind=kind):
        raise DesktopRemoteRepositoryMutationError(
            "CC Port does not delete GitHub repositories. "
            "Delete the repository on GitHub, or remove only its local/indexed resource."
        )
    registry = load_registry()
    entry = registry.get(name, kind)
    uninstalled = False
    if entry is not None and bool(payload.get("uninstall", False)):
        uninstalled = uninstall_one(entry, config=cfg)
    removed = delete_resource(
        name,
        kind=kind,
        config=cfg,
        confirm_name=_optional_str(payload.get("confirm_name")),
    )
    return {"removed": removed.entry, "delete": removed, "uninstalled": uninstalled}


def _resource_pull(_: JsonDict) -> Any:
    return pull_resource_repo(load_config())


def _resource_push(payload: JsonDict) -> Any:
    return push_resource_repo(
        message=_optional_str(payload.get("message")) or "cc-port: update resources",
        config=load_config(),
    )


def _resource_commit_plan(_: JsonDict) -> Any:
    return build_resource_commit_plan(config=load_config())


def _resource_commit_push(payload: JsonDict) -> Any:
    return push_resource_repo(
        message=_optional_str(payload.get("message")) or "cc-port: update resources",
        config=load_config(),
    )


def _resource_sync_status(payload: JsonDict) -> Any:
    return inspect_resource_sync(
        config=load_config(),
        fetch=bool(payload.get("fetch", False)),
    )


def _resource_sync_plan(_: JsonDict) -> Any:
    return build_resource_sync_plan(config=load_config())


def _resource_sync_resolve(payload: JsonDict) -> Any:
    return resolve_resource_sync_plan(
        _required_str(payload, "operation_id"),
        _choices_payload(payload) or {},
        config=load_config(),
    )


def _resource_sync_apply(payload: JsonDict) -> Any:
    return apply_resource_sync_plan(
        _required_str(payload, "operation_id"),
        config=load_config(),
    )


def _resource_sync_cancel(payload: JsonDict) -> Any:
    return cancel_resource_sync_plan(
        _required_str(payload, "operation_id"),
        config=load_config(),
    )


def _resource_sync_push(_: JsonDict) -> Any:
    return push_resource_sync(config=load_config())


def _operation_history(payload: JsonDict) -> JsonDict:
    limit = int(payload.get("limit", 100))
    return {"operations": operation_history(limit=max(1, min(limit, 500)))}


def _operation_history_page(payload: JsonDict) -> Any:
    return operation_history_page(
        offset=max(0, int(payload.get("offset", 0))),
        limit=max(1, min(int(payload.get("limit", 20)), 100)),
    )


def _operation_detail(payload: JsonDict) -> Any:
    return operation_detail(_required_str(payload, "operation_id"))


def _operation_restore(payload: JsonDict) -> Any:
    return restore_operation(
        _required_str(payload, "operation_id"),
        force=bool(payload.get("force", False)),
        config=load_config(),
    )


def _state_retention_plan(payload: JsonDict) -> Any:
    return build_state_retention_plan(
        config=load_config(),
        retention_days=_optional_non_negative_int(payload.get("retention_days")),
        keep_latest_operations=_optional_non_negative_int(payload.get("keep_latest_operations")),
        max_backup_mb=_optional_non_negative_int(payload.get("max_backup_mb")),
    )


def _state_prune(payload: JsonDict) -> Any:
    return prune_state(
        _str_list(payload.get("operation_ids")),
        config=load_config(),
        retention_days=_optional_non_negative_int(payload.get("retention_days")),
        keep_latest_operations=_optional_non_negative_int(payload.get("keep_latest_operations")),
        max_backup_mb=_optional_non_negative_int(payload.get("max_backup_mb")),
    )


def _orphan_backups(_: JsonDict) -> JsonDict:
    return {"orphans": list_orphan_backups()}


def _orphan_export(payload: JsonDict) -> Any:
    output = _optional_str(payload.get("output_path"))
    return export_orphan_backup(
        _required_str(payload, "name"),
        output_path=Path(output) if output else None,
        config=load_config(),
    )


def _orphan_quarantine(payload: JsonDict) -> Any:
    return quarantine_orphan_backups(
        _str_list(payload.get("names")),
        config=load_config(),
    )


def _orphan_quarantines(_: JsonDict) -> JsonDict:
    return {"quarantines": list_orphan_quarantines()}


def _orphan_quarantine_delete(payload: JsonDict) -> Any:
    return delete_orphan_quarantine(
        _required_str(payload, "quarantine_id"),
        config=load_config(),
    )


def _maintenance_audits(payload: JsonDict) -> JsonDict:
    limit = max(1, min(int(payload.get("limit", 50)), 500))
    return {"audits": list_maintenance_audits(limit=limit)}


def _maintenance_audit(payload: JsonDict) -> JsonDict:
    return {"audit": load_maintenance_audit(_required_str(payload, "audit_id"))}


def _resource_sync_stale(payload: JsonDict) -> JsonDict:
    minimum = float(payload.get("min_age_hours", 24))
    return {
        "plans": list_stale_resource_sync_plans(
            min_age_hours=max(0, minimum),
        )
    }


def _resource_sync_cleanup(payload: JsonDict) -> Any:
    return cleanup_stale_resource_sync_plan(
        _required_str(payload, "operation_id"),
        min_age_hours=max(0, float(payload.get("min_age_hours", 24))),
        force=bool(payload.get("force", False)),
        config=load_config(),
    )


def _config_get(_: JsonDict) -> JsonDict:
    raw_cfg = load_raw_config()
    return {
        "path": str(raw_cfg.source_path or default_config_path()),
        "exists": bool(raw_cfg.source_path),
        "config": _editable_config(raw_cfg),
    }


def _config_bind_repo(payload: JsonDict) -> JsonDict:
    repo_url = _required_str(payload, "repo_url")
    parsed = parse_github_repo_url(repo_url)
    if parsed.transport != "https":
        raise ValueError(
            "CC Port only accepts a complete HTTPS GitHub repository URL, "
            "for example https://github.com/owner/repository."
        )
    git_ops.require_git_credential_manager()
    result = bind_resource_repo(
        repo_url,
        expected_current_repo_url=str(payload.get("expected_current_repo_url") or ""),
    )
    return {
        "settings": _config_get({}),
        "binding": result,
    }


def _git_credential_status(_: JsonDict) -> Any:
    return git_ops.git_credential_status()


def _platform_set_enabled(payload: JsonDict) -> JsonDict:
    name = _required_str(payload, "name")
    if name not in PLATFORM_PRESETS:
        raise ValueError(f"Unsupported platform preset: {name}")
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean.")

    cfg = load_raw_config()
    profiles = _platforms_with_presets(cfg.platforms.profiles)
    for profile in profiles:
        if profile.name == name:
            profile.enabled = enabled
            break
    cfg.platforms = PlatformsConfig(profiles=profiles)
    write_config(cfg, cfg.source_path or default_config_path())
    return _config_get({})


def _write_default_config(payload: JsonDict) -> JsonDict:
    force = bool(payload.get("force", False))
    path = default_config_path()
    if path.exists() and not force:
        return {"written": False, "path": path, "reason": "exists"}
    cfg = new_default_config()
    written = write_config(cfg)
    return {"written": True, "path": written}


def _editable_config(cfg: Config) -> JsonDict:
    return {
        "install": {"target": cfg.install.target},
        "git": {"executable": cfg.git.executable},
        "resources": {
            "repo_name": cfg.resources.repo_name,
            "repo_url": cfg.resources.repo_url,
            "local_path": cfg.resources.local_path,
            "branch": cfg.resources.branch,
            "credential_mode": cfg.resources.credential_mode,
        },
        "state": {
            "lock_timeout_seconds": cfg.state.lock_timeout_seconds,
            "retention_days": cfg.state.retention_days,
            "keep_latest_operations": cfg.state.keep_latest_operations,
            "max_backup_mb": cfg.state.max_backup_mb,
        },
        "platforms": [
            _platform_to_json(p) for p in _platforms_with_presets(cfg.platforms.profiles)
        ],
    }


def _platforms_with_presets(profiles: list[PlatformProfile]) -> list[PlatformProfile]:
    out = [
        PlatformProfile(
            name=p.name,
            enabled=p.enabled,
            skills_dir=p.skills_dir,
            mcp_json=p.mcp_json,
            rules_dir=p.rules_dir,
            prompts_dir=p.prompts_dir,
            plugins_dir=p.plugins_dir,
        )
        for p in profiles
    ]
    seen = {p.name for p in out}
    for name in PLATFORM_PRESETS:
        if name in seen:
            continue
        preset = build_platform(name)
        preset.enabled = False
        out.append(preset)
    return out


def _platform_to_json(profile: PlatformProfile) -> JsonDict:
    return {
        "name": profile.name,
        "enabled": profile.enabled,
        "skills_dir": profile.skills_dir,
        "mcp_json": profile.mcp_json,
        "rules_dir": profile.rules_dir,
        "prompts_dir": profile.prompts_dir,
        "plugins_dir": profile.plugins_dir,
    }


def _maybe_push(cfg: Config, payload: JsonDict) -> Any:
    if not bool(payload.get("push", False)):
        return None
    return push_resource_repo(config=cfg)


def _push_after_upload(cfg: Config, payload: JsonDict) -> Any:
    if bool(payload.get("no_push", False)):
        return None
    return push_resource_repo(config=cfg)


def _configured_resource_repo_name(raw_cfg: Config) -> str:
    if raw_cfg.source_path is None:
        return ""
    try:
        with raw_cfg.source_path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return ""

    resources = data.get("resources", {})
    if not isinstance(resources, dict):
        return ""
    return str(resources.get("repo_name") or "").strip()


def _config_summary(cfg: Config) -> JsonDict:
    return {
        "path": str(cfg.source_path or default_config_path()),
        "exists": bool(cfg.source_path),
        "resources": cfg.resources,
        "git": cfg.git,
        "install": cfg.install,
        "state": cfg.state,
    }


def _registry_counts(items: list[RegistryItem]) -> JsonDict:
    counts: JsonDict = {"total": len(items), "by_kind": {}, "by_source": {}}
    for item in items:
        counts["by_kind"][item.kind] = counts["by_kind"].get(item.kind, 0) + 1
        counts["by_source"][item.source] = counts["by_source"].get(item.source, 0) + 1
    return counts


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_to_jsonable(item) for item in value]
    return str(value)


def _error(
    code: str,
    message: str,
    message_ref: UiMessageRef | None = None,
) -> JsonDict:
    error: JsonDict = {"code": code, "message": message}
    if message_ref is not None:
        error["message_ref"] = _to_jsonable(message_ref)
    return {"ok": False, "error": error}


def _exception_message_ref(exc: Exception) -> UiMessageRef | None:
    message_codes = {
        "GitMissingError": "api.git.missing",
        "GitCredentialManagerMissingError": "api.git.gcm_missing",
        "GitCredentialManagerNotConfiguredError": "api.git.gcm_not_configured",
        "GitCredentialInteractionCancelled": "api.git.login_cancelled",
        "GitAuthenticationRequired": "api.git.login_required",
        "GitWriteAccessDenied": "api.git.write_denied",
        "GitOperationTimeout": "api.git.timeout",
        "DesktopRemoteRepositoryMutationError": "api.github.desktop_repo_admin_forbidden",
    }
    code = message_codes.get(exc.__class__.__name__)
    if code is None:
        return None
    return ui_message(code, str(exc))


def _write_json_response(result: JsonDict) -> None:
    text = json.dumps(_to_jsonable(result), ensure_ascii=True)
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8") + b"\n")
        buffer.flush()
        return
    print(text)


def _required_str(payload: JsonDict, key: str) -> str:
    value = payload.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"Missing required field: {key}")
    return str(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _choices_payload(payload: JsonDict) -> dict[str, str] | None:
    value = payload.get("choices")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("choices must be a mapping of diff item id to local/incoming.")
    out: dict[str, str] = {}
    for key, raw_choice in value.items():
        choice = str(raw_choice).strip()
        if choice not in {"local", "incoming"}:
            raise ValueError(f"Invalid choice for {key}: {choice}")
        out[str(key)] = choice
    return out


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    raise ValueError("Expected a string list.")


def _optional_non_negative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Expected a non-negative integer.") from exc
    if result < 0:
        raise ValueError("Expected a non-negative integer.")
    return result


def _required_kind(payload: JsonDict) -> ItemKind:
    kind = _required_str(payload, "kind")
    if kind not in ITEM_KINDS:
        raise ValueError(f"Unsupported resource kind: {kind}")
    return kind  # type: ignore[return-value]


def _asset_batch_choices(value: Any) -> list[AssetBatchChoice]:
    if not isinstance(value, list):
        return []
    choices: list[AssetBatchChoice] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        resource_key = str(item.get("resource_key") or "").strip()
        if not resource_key:
            continue
        choices.append(
            AssetBatchChoice(
                resource_key=resource_key,
                platform=str(item.get("platform") or "").strip(),
                local_instance_id=str(item.get("local_instance_id") or "").strip(),
                resolution=str(item.get("resolution") or "overwrite").strip(),
                new_name=str(item.get("new_name") or "").strip(),
                overwrite_unmanaged=bool(item.get("overwrite_unmanaged", False)),
                plugin_track=str(item.get("plugin_track") or "").strip(),
                ownership_confirmed=bool(item.get("ownership_confirmed", False)),
                link_target_confirmed=bool(item.get("link_target_confirmed", False)),
                reference_origin={
                    str(key): str(value)
                    for key, value in (item.get("reference_origin") or {}).items()
                }
                if isinstance(item.get("reference_origin"), dict)
                else {},
                plugin_dependencies={
                    str(key): str(value)
                    for key, value in (item.get("plugin_dependencies") or {}).items()
                }
                if isinstance(item.get("plugin_dependencies"), dict)
                else {},
            )
        )
    return choices


def _registry_repair_choices(value: Any) -> list[RegistryRepairChoice]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Registry repair choices must be a list.")
    choices: list[RegistryRepairChoice] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"Registry repair choice {index} must be an object.")
        issue_id = str(item.get("issue_id") or "").strip()
        action = str(item.get("action") or "").strip()
        if not issue_id or not action:
            raise ValueError(
                f"Registry repair choice {index} requires issue_id and action."
            )
        choices.append(
            RegistryRepairChoice(
                issue_id=issue_id,
                action=action,
                name=str(item.get("name") or "").strip(),
            )
        )
    return choices


def _discovery_selections(value: Any) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    out: list[JsonDict] = []
    for item in value:
        if isinstance(item, str):
            if item.strip():
                out.append({"id": item.strip()})
        elif isinstance(item, dict):
            resource_id = str(item.get("id") or "").strip()
            if resource_id:
                out.append(
                    {
                        "id": resource_id,
                        "name": _optional_str(item.get("name")) or "",
                        "link_target_confirmed": bool(
                            item.get("link_target_confirmed", False)
                        ),
                    }
                )
    return out


ACTIONS: dict[str, Handler] = {
    "summary": _summary,
    "list_items": _list_items,
    "resource_status": _resource_status,
    "platforms": _platforms,
    "doctor": _doctor,
    "collect": _collect,
    "upload": _upload,
    "discover_resources": _discover_resources,
    "read_discovered_resource": _read_discovered_resource,
    "upload_discovered_resources": _upload_discovered_resources,
    "sync_preview": _sync_preview,
    "sync": _sync,
    "resource_inventory": _resource_inventory,
    "asset_inventory": _asset_inventory,
    "registry_repair_plan": _registry_repair_plan,
    "registry_repair_apply": _registry_repair_apply,
    "asset_content_diff": _asset_content_diff,
    "asset_action_plan": _asset_action_plan,
    "asset_action_apply": _asset_action_apply,
    "asset_batch_plan": _asset_batch_plan,
    "asset_batch_apply": _asset_batch_apply,
    "plugin_projects_list": _plugin_projects_list,
    "plugin_projects_add": _plugin_projects_add,
    "plugin_projects_remove": _plugin_projects_remove,
    "plugin_reference_add": _plugin_reference_add,
    "plugin_delete_plan": _plugin_delete_plan,
    "plugin_delete_apply": _plugin_delete_apply,
    "resource_install": _resource_install,
    "resource_install_plan": _resource_install_plan,
    "resource_uninstall": _resource_uninstall,
    "resource_preview": _resource_preview,
    "resource_open_path": _resource_open_path,
    "resource_delete": _resource_delete,
    "check": _check,
    "remove": _remove,
    "resource_pull": _resource_pull,
    "resource_push": _resource_push,
    "resource_commit_plan": _resource_commit_plan,
    "resource_commit_push": _resource_commit_push,
    "resource_sync_status": _resource_sync_status,
    "resource_sync_plan": _resource_sync_plan,
    "resource_sync_resolve": _resource_sync_resolve,
    "resource_sync_apply": _resource_sync_apply,
    "resource_sync_cancel": _resource_sync_cancel,
    "resource_sync_push": _resource_sync_push,
    "resource_sync_stale": _resource_sync_stale,
    "resource_sync_cleanup": _resource_sync_cleanup,
    "operation_history": _operation_history,
    "operation_history_page": _operation_history_page,
    "operation_detail": _operation_detail,
    "operation_restore": _operation_restore,
    "state_retention_plan": _state_retention_plan,
    "state_prune": _state_prune,
    "orphan_backups": _orphan_backups,
    "orphan_export": _orphan_export,
    "orphan_quarantine": _orphan_quarantine,
    "orphan_quarantines": _orphan_quarantines,
    "orphan_quarantine_delete": _orphan_quarantine_delete,
    "maintenance_audits": _maintenance_audits,
    "maintenance_audit": _maintenance_audit,
    "config_get": _config_get,
    "config_bind_repo": _config_bind_repo,
    "git_credential_status": _git_credential_status,
    "platform_set_enabled": _platform_set_enabled,
    "write_default_config": _write_default_config,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cc-port-desktop-api")
    parser.add_argument("action", choices=sorted(ACTIONS))
    parser.add_argument("payload", nargs="?")
    args = parser.parse_args(argv)

    try:
        env_payload = os.environ.pop(DESKTOP_PAYLOAD_ENV_VAR, "")
        if args.payload is not None:
            raw_payload = args.payload
        elif env_payload:
            raw_payload = env_payload
        elif sys.stdin is not None and not sys.stdin.isatty():
            raw_payload = sys.stdin.read() or "{}"
        else:
            raw_payload = "{}"
        raw_payload = raw_payload.lstrip("\ufeff")
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        detail = str(exc)
        result = _error(
            "invalid_json",
            detail,
            ui_message("api.invalid_json", detail, detail=detail),
        )
    else:
        if not isinstance(payload, dict):
            message_ref = ui_message(
                "api.invalid_payload",
                "Payload must be a JSON object.",
            )
            result = _error("invalid_payload", message_ref.fallback, message_ref)
        else:
            result = run_action(args.action, payload)

    _write_json_response(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
