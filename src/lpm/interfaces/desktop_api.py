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
from urllib.parse import urlparse

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - py310 fallback
    import tomli as tomllib

from .. import __version__
from ..core.config import (
    CONFIG_ENV_VAR,
    DEFAULT_INSTALL_TARGET,
    DEFAULT_KEEP_LATEST_OPERATIONS,
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    DEFAULT_MAX_BACKUP_MB,
    DEFAULT_REPO_PREFIX,
    DEFAULT_RESOURCE_BRANCH,
    DEFAULT_RESOURCE_CREDENTIAL_MODE,
    DEFAULT_RESOURCE_REPO_NAME,
    DEFAULT_RETENTION_DAYS,
    Config,
    GitConfig,
    GithubConfig,
    InstallConfig,
    ResourcesConfig,
    StateConfig,
    default_config_path,
    load_config,
    load_raw_config,
    new_default_config,
    resource_repo_auth_token,
    write_config,
)
from ..core.models import ItemKind, RegistryItem
from ..core.platforms import PLATFORM_PRESETS, PlatformProfile, PlatformsConfig, build_platform
from ..core.registry import find_registry_path, load_registry
from ..core.resource_detection import detect_local_resource_type, detect_remote_resource
from ..infrastructure import git_ops
from ..infrastructure.github_client import GithubAuthError, GithubClient
from ..services import github_oauth, publisher
from ..services.asset_sync import (
    AssetBatchChoice,
    add_plugin_reference,
    apply_asset_action_plan,
    apply_asset_batch_plan,
    apply_plugin_delete_plan,
    build_asset_action_plan,
    build_asset_batch_plan,
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
from ..services.resource_binding import bind_resource_repo, configured_github_owner
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
    init_resource_repo,
    inspect_resource_repo,
    prepare_local_resource_repo,
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
DESKTOP_PAYLOAD_ENV_VAR = "LPM_DESKTOP_API_PAYLOAD"
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
    return {"checks": build_doctor_checks(load_config())}


def _collect(payload: JsonDict) -> JsonDict:
    github_url = _required_str(payload, "github_url")
    cfg = load_config()
    detected = detect_remote_resource(
        github_url,
        explicit_type=_optional_str(payload.get("kind")),
        token=cfg.github.token or None,
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
        token=cfg.github.token or None,
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
        try:
            result = import_local_resource(
                candidate.path,
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
        github_oauth.require_authorization("remote_delete")
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
            message=f"lpm: delete resource {deleted.name}",
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


def _resource_init(payload: JsonDict) -> Any:
    return init_resource_repo(name=_optional_str(payload.get("name")), config=load_config())


def _resource_use(payload: JsonDict) -> Any:
    return use_resource_repo(_required_str(payload, "target"), config=load_config())


def _resource_pull(_: JsonDict) -> Any:
    return pull_resource_repo(load_config())


def _resource_push(payload: JsonDict) -> Any:
    return push_resource_repo(
        message=_optional_str(payload.get("message")) or "lpm: update resources",
        config=load_config(),
    )


def _resource_commit_plan(_: JsonDict) -> Any:
    return build_resource_commit_plan(config=load_config())


def _resource_commit_push(payload: JsonDict) -> Any:
    return push_resource_repo(
        message=_optional_str(payload.get("message")) or "lpm: update resources",
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
    effective_cfg = load_config()
    env_token = os.environ.get(CONFIG_ENV_VAR, "").strip()
    return {
        "path": str(raw_cfg.source_path or default_config_path()),
        "exists": bool(raw_cfg.source_path),
        "token_source": "env" if env_token else ("config" if raw_cfg.github.token else "none"),
        "token_preview": _mask_token(effective_cfg.github.token),
        "config_token_preview": _mask_token(raw_cfg.github.token),
        "env_token_active": bool(env_token),
        "config": _editable_config(raw_cfg),
    }


def _config_check(payload: JsonDict) -> JsonDict:
    raw_cfg = load_raw_config()
    cfg = _config_from_draft(payload, raw_cfg)
    cfg.github.token = _effective_token(cfg.github.token)
    return _check_resource_target(cfg)


def _config_branches(payload: JsonDict) -> JsonDict:
    raw_cfg = load_raw_config()
    cfg = _config_from_draft(payload, raw_cfg)
    cfg.github.token = _effective_token(cfg.github.token)

    selected_branch = cfg.resources.branch.strip() or DEFAULT_RESOURCE_BRANCH
    default_branch = DEFAULT_RESOURCE_BRANCH

    parsed = _parse_github_repo(cfg.resources.repo_url)
    if cfg.resources.repo_url and parsed is None:
        return _branch_options_response(
            selected_branch=selected_branch,
            default_branch=default_branch,
            warning="Only github.com repositories can be checked from Settings.",
        )

    native_credentials = cfg.resources.credential_mode == "native"
    if cfg.github.token.strip() and not native_credentials:
        try:
            client = GithubClient(cfg.github.token)
            owner, name = _target_repo_owner_name(cfg, client)
            result = client.list_repo_branches(owner, name)
            if result is not None:
                default_branch = result.default_branch or DEFAULT_RESOURCE_BRANCH
                return {
                    "branches": _branch_options(
                        result.branches,
                        selected_branch,
                        default_branch,
                    ),
                    "default_branch": default_branch,
                    "selected_branch": selected_branch,
                    "warning": "",
                }
            label = "/".join(part for part in (owner, name) if part) or cfg.resources.repo_url
            api_warning = f"Repository {label} is not accessible or does not exist."
        except GithubAuthError as exc:
            api_warning = str(exc)
        except Exception as exc:  # noqa: BLE001 - branch loading is optional in Settings
            api_warning = f"GitHub API branch lookup failed: {exc}"
    elif not native_credentials:
        api_warning = f"No API token is configured in config or {CONFIG_ENV_VAR}."
    else:
        api_warning = ""

    if cfg.resources.repo_url:
        try:
            git_default, git_branches = git_ops.remote_branches(
                cfg.resources.repo_url,
                token=resource_repo_auth_token(cfg),
            )
            default_branch = git_default or DEFAULT_RESOURCE_BRANCH
            return {
                "branches": _branch_options(
                    git_branches,
                    selected_branch,
                    default_branch,
                ),
                "default_branch": default_branch,
                "selected_branch": selected_branch,
                "warning": (
                    f"{api_warning} Branches were loaded using local Git/SSH credentials."
                    if api_warning
                    else ""
                ),
            }
        except (git_ops.GitError, ValueError) as exc:
            return _branch_options_response(
                selected_branch=selected_branch,
                default_branch=default_branch,
                warning=f"{api_warning} Git fallback also failed: {exc}",
            )

    return _branch_options_response(
        selected_branch=selected_branch,
        default_branch=default_branch,
        warning=api_warning,
    )


def _config_bind_repo(payload: JsonDict) -> JsonDict:
    result = bind_resource_repo(
        _required_str(payload, "repo_url"),
        expected_current_repo_url=str(payload.get("expected_current_repo_url") or ""),
    )
    return {
        "settings": _config_get({}),
        "binding": result,
    }


def _github_auth_status(_: JsonDict) -> JsonDict:
    return github_oauth.auth_status()


def _github_auth_start(payload: JsonDict) -> JsonDict:
    return github_oauth.start_authorization(_required_str(payload, "purpose"))


def _github_auth_poll(payload: JsonDict) -> JsonDict:
    return github_oauth.poll_authorization(_required_str(payload, "session_id"))


def _github_auth_cancel(payload: JsonDict) -> JsonDict:
    return github_oauth.cancel_authorization(_required_str(payload, "session_id"))


def _github_web_auth_start(payload: JsonDict) -> JsonDict:
    return github_oauth.start_web_authorization(_required_str(payload, "purpose"))


def _github_web_auth_poll(payload: JsonDict) -> JsonDict:
    return github_oauth.poll_web_authorization(
        _required_str(payload, "session_id"),
        immediate=bool(payload.get("immediate", False)),
    )


def _github_web_auth_cancel(payload: JsonDict) -> JsonDict:
    return github_oauth.cancel_web_authorization(_required_str(payload, "session_id"))


def _github_token_reveal(_: JsonDict) -> JsonDict:
    return github_oauth.reveal_config_token()


def _github_token_clear(_: JsonDict) -> JsonDict:
    return github_oauth.clear_config_token()


def _github_owner_set(payload: JsonDict) -> JsonDict:
    owner = github_oauth.set_github_owner(_required_str(payload, "owner"))
    return {**owner, "settings": _config_get({})}


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


def _config_save(payload: JsonDict) -> JsonDict:
    raw_cfg = load_raw_config()
    cfg = _config_from_draft(payload, raw_cfg)
    config_path = raw_cfg.source_path or default_config_path()
    if cfg.resources.credential_mode == "token" and not _effective_token(cfg.github.token):
        raise ValueError(
            "Resource repository credential mode is token, but no GitHub token is configured."
        )

    resource_result = None
    if bool(payload.get("prepare_resource_repo", False)):
        resource_result = _prepare_resource_target(cfg, _effective_token(cfg.github.token))

    written = write_config(cfg, config_path)
    data = _config_get({})
    data["saved"] = True
    data["path"] = str(written)
    if resource_result is not None:
        data["resource_repo"] = resource_result
    return data


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
        "github": {
            "owner": cfg.github.owner,
            "repo_prefix": cfg.github.repo_prefix,
            "default_private": cfg.github.default_private,
        },
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
        "plugins_dir": profile.plugins_dir,
    }


def _config_from_draft(payload: JsonDict, base: Config) -> Config:
    draft = payload.get("draft")
    if not isinstance(draft, dict):
        raise ValueError("Missing required field: draft")

    github_data = _dict_field(draft, "github")
    git_data = _dict_field(draft, "git")
    install_data = _dict_field(draft, "install")
    resources_data = _dict_field(draft, "resources")
    state_data = _dict_field(draft, "state")

    return Config(
        github=GithubConfig(
            token=_token_for_write(base.github.token, payload),
            owner=_field_str(github_data, "owner", base.github.owner),
            repo_prefix=_field_str(
                github_data, "repo_prefix", base.github.repo_prefix or DEFAULT_REPO_PREFIX
            ),
            default_private=_field_bool(
                github_data, "default_private", base.github.default_private
            ),
        ),
        install=InstallConfig(
            target=_field_str(
                install_data, "target", base.install.target or DEFAULT_INSTALL_TARGET
            ),
        ),
        resources=ResourcesConfig(
            repo_name=_field_str(
                resources_data,
                "repo_name",
                base.resources.repo_name or DEFAULT_RESOURCE_REPO_NAME,
            )
            or DEFAULT_RESOURCE_REPO_NAME,
            repo_url=_field_str(resources_data, "repo_url", base.resources.repo_url),
            local_path=_field_str(resources_data, "local_path", base.resources.local_path),
            branch=_field_str(
                resources_data,
                "branch",
                base.resources.branch or DEFAULT_RESOURCE_BRANCH,
            )
            or DEFAULT_RESOURCE_BRANCH,
            credential_mode=_field_choice(
                resources_data,
                "credential_mode",
                base.resources.credential_mode or DEFAULT_RESOURCE_CREDENTIAL_MODE,
                {"auto", "native", "token"},
            ),
        ),
        git=GitConfig(
            executable=_field_str(git_data, "executable", base.git.executable),
        ),
        state=StateConfig(
            lock_timeout_seconds=_field_positive_float(
                state_data,
                "lock_timeout_seconds",
                base.state.lock_timeout_seconds or DEFAULT_LOCK_TIMEOUT_SECONDS,
            ),
            retention_days=_field_non_negative_int(
                state_data,
                "retention_days",
                base.state.retention_days
                if base.state.retention_days >= 0
                else DEFAULT_RETENTION_DAYS,
            ),
            keep_latest_operations=_field_non_negative_int(
                state_data,
                "keep_latest_operations",
                base.state.keep_latest_operations
                if base.state.keep_latest_operations >= 0
                else DEFAULT_KEEP_LATEST_OPERATIONS,
            ),
            max_backup_mb=_field_non_negative_int(
                state_data,
                "max_backup_mb",
                base.state.max_backup_mb
                if base.state.max_backup_mb >= 0
                else DEFAULT_MAX_BACKUP_MB,
            ),
        ),
        platforms=PlatformsConfig(
            profiles=_platforms_from_payload(draft.get("platforms"), base.platforms.profiles),
        ),
        source_path=base.source_path,
    )


def _token_for_write(current: str, payload: JsonDict) -> str:
    action = str(payload.get("token_action") or "preserve").strip().lower()
    if action == "clear":
        return ""
    if action == "replace":
        token = str(payload.get("new_token") or "").strip()
        return token or current
    return current


def _effective_token(config_token: str) -> str:
    return os.environ.get(CONFIG_ENV_VAR, "").strip() or config_token


def _platforms_from_payload(value: Any, existing: list[PlatformProfile]) -> list[PlatformProfile]:
    if not isinstance(value, list):
        return _platforms_with_presets(existing)

    out: list[PlatformProfile] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        out.append(
            PlatformProfile(
                name=name,
                enabled=bool(item.get("enabled", False)),
                skills_dir=str(item.get("skills_dir") or ""),
                mcp_json=str(item.get("mcp_json") or ""),
                rules_dir=str(item.get("rules_dir") or ""),
                plugins_dir=str(item.get("plugins_dir") or ""),
            )
        )
        seen.add(name)
    return out or _platforms_with_presets(existing)


def _dict_field(data: JsonDict, key: str) -> JsonDict:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _field_str(data: JsonDict, key: str, default: str = "") -> str:
    if key not in data:
        return default
    return str(data.get(key) or "").strip()


def _field_bool(data: JsonDict, key: str, default: bool = False) -> bool:
    if key not in data:
        return default
    value = data.get(key)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _field_choice(data: JsonDict, key: str, default: str, choices: set[str]) -> str:
    value = _field_str(data, key, default).lower()
    if value not in choices:
        allowed = ", ".join(sorted(choices))
        raise ValueError(f"{key} must be one of: {allowed}.")
    return value


def _field_non_negative_int(data: JsonDict, key: str, default: int) -> int:
    if key not in data:
        return default
    try:
        value = int(data[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a non-negative integer.") from exc
    if value < 0:
        raise ValueError(f"{key} must be a non-negative integer.")
    return value


def _field_positive_float(data: JsonDict, key: str, default: float) -> float:
    if key not in data:
        return default
    try:
        value = float(data[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be greater than zero.") from exc
    if value <= 0:
        raise ValueError(f"{key} must be greater than zero.")
    return value


def _mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}{'*' * max(4, len(token) - 8)}{token[-4:]}"


def _check_resource_target(cfg: Config) -> JsonDict:
    missing: list[JsonDict] = []
    warnings: list[JsonDict] = []
    token = cfg.github.token.strip()
    local_path = cfg.resources.local_path_value.expanduser()
    local_exists = local_path.exists()
    local_is_git = git_ops.is_repo(local_path) if local_exists else False
    local_remote_url = git_ops.current_remote_url(local_path) if local_is_git else None
    local_dirty = bool(git_ops.status_short(local_path)) if local_is_git else False

    if not local_exists:
        missing.append(
            {
                "id": "local_path",
                "label": "Local resource directory",
                "detail": f"{local_path} does not exist.",
            }
        )
    elif not local_is_git:
        missing.append(
            {
                "id": "local_git",
                "label": "Local git repository",
                "detail": f"{local_path} exists but is not a git repository.",
            }
        )
    elif cfg.resources.repo_url.strip():
        expected = _normalize_git_url(cfg.resources.repo_url)
        actual = _normalize_git_url(local_remote_url or "")
        if not local_remote_url:
            missing.append(
                {
                    "id": "local_remote",
                    "label": "Local git remote",
                    "detail": f"{local_path} is a git repository but has no origin remote.",
                }
            )
        elif expected and actual != expected:
            missing.append(
                {
                    "id": "local_remote_mismatch",
                    "label": "Local git remote",
                    "detail": f"{local_path} points to {local_remote_url}, not {cfg.resources.repo_url}.",
                }
            )

    if local_dirty:
        warnings.append(
            {
                "id": "local_dirty",
                "label": "Local resource directory",
                "detail": f"{local_path} has local changes. Commit or clean them before pulling remote data.",
            }
        )

    remote = _check_remote_repo(cfg, token, missing, warnings)
    has_blocking_warning = any(
        item["id"] in {"github_token", "remote_unsupported"} for item in warnings
    )
    return {
        "missing": missing,
        "warnings": warnings,
        "can_prepare": bool(missing) and not has_blocking_warning,
        "local": {
            "path": str(local_path),
            "exists": local_exists,
            "is_git_repo": local_is_git,
        },
        "remote": remote,
    }


def _check_remote_repo(
    cfg: Config,
    token: str,
    missing: list[JsonDict],
    warnings: list[JsonDict],
) -> JsonDict:
    parsed = _parse_github_repo(cfg.resources.repo_url)
    if cfg.resources.repo_url and parsed is None:
        warnings.append(
            {
                "id": "remote_unsupported",
                "label": "Remote repository",
                "detail": "Only github.com repositories can be checked or created from Settings.",
            }
        )
        return {"checked": False, "exists": False, "repo": cfg.resources.repo_url}

    if not token:
        warnings.append(
            {
                "id": "github_token",
                "label": "GitHub token",
                "detail": f"Set a token in config or {CONFIG_ENV_VAR} before checking private repositories.",
            }
        )
        return {"checked": False, "exists": False, "repo": cfg.resources.repo_url}

    try:
        client = GithubClient(token)
        owner, name = _target_repo_owner_name(cfg, client)
        repo = client.get_repo(owner, name)
    except Exception as exc:  # noqa: BLE001 - surfaced as a desktop warning
        warnings.append(
            {
                "id": "remote_check",
                "label": "Remote repository",
                "detail": str(exc),
            }
        )
        return {"checked": False, "exists": False, "repo": cfg.resources.repo_url}

    label = f"{owner}/{name}"
    if repo is None:
        missing.append(
            {
                "id": "remote_repo",
                "label": "GitHub repository",
                "detail": f"{label} is not accessible or does not exist.",
            }
        )
        return {"checked": True, "exists": False, "repo": label}

    return {"checked": True, "exists": True, "repo": label}


def _prepare_resource_target(cfg: Config, token: str) -> JsonDict:
    if not token:
        raise ValueError(
            f"Set a GitHub token in config or {CONFIG_ENV_VAR} before creating a resource repository."
        )
    if cfg.resources.repo_url and _parse_github_repo(cfg.resources.repo_url) is None:
        raise ValueError(
            "Only github.com resource repositories can be created or connected from Settings."
        )

    client = GithubClient(token)
    owner, name = _target_repo_owner_name(cfg, client)
    repo, created = client.ensure_repo(
        owner=owner,
        name=name,
        description="Private LPM AI resources repository.",
        private=True,
    )
    branch = cfg.resources.branch or DEFAULT_RESOURCE_BRANCH
    local_path = cfg.resources.local_path_value.expanduser().resolve()

    prepare_local_resource_repo(
        local_path,
        repo_url=repo.https_url,
        branch=branch,
        token=token,
        config=cfg,
    )

    cfg.resources.repo_name = name
    cfg.resources.repo_url = repo.https_url
    cfg.resources.local_path = str(local_path)
    cfg.resources.branch = branch

    return {
        "created": created,
        "repo_url": repo.https_url,
        "local_path": str(local_path),
        "info": inspect_resource_repo(cfg),
    }


def _target_repo_owner_name(cfg: Config, client: GithubClient) -> tuple[str, str]:
    parsed = _parse_github_repo(cfg.resources.repo_url)
    if parsed is not None:
        return parsed
    owner = configured_github_owner(cfg) or client.authenticated_login()
    name = cfg.resources.repo_name.strip() or DEFAULT_RESOURCE_REPO_NAME
    return owner, name


def _parse_github_repo(value: str) -> tuple[str, str] | None:
    raw = value.strip().rstrip("/")
    if not raw:
        return None
    if raw.startswith("git@github.com:"):
        path = raw.split(":", 1)[1]
    else:
        parsed = urlparse(raw)
        if parsed.netloc.lower() != "github.com":
            return None
        path = parsed.path.lstrip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None
    return parts[0], parts[1].removesuffix(".git")


def _normalize_git_url(value: str) -> str:
    return value.strip().removesuffix(".git").rstrip("/")


def _branch_options_response(
    *,
    selected_branch: str,
    default_branch: str,
    warning: str,
) -> JsonDict:
    return {
        "branches": _branch_options([], selected_branch, default_branch),
        "default_branch": default_branch,
        "selected_branch": selected_branch,
        "warning": warning,
    }


def _branch_options(branches: list[str], selected_branch: str, default_branch: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for branch in [DEFAULT_RESOURCE_BRANCH, default_branch, selected_branch, *branches]:
        value = branch.strip()
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out


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
        "github": {
            "token_configured": bool(cfg.github.token),
            "owner": cfg.github.owner,
            "repo_prefix": cfg.github.repo_prefix,
            "default_private": cfg.github.default_private,
        },
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
        "OAuthConfigurationError": "api.github.oauth_not_configured",
        "GithubApiError": "api.github.request_failed",
        "OAuthSessionError": "api.github.session_failed",
        "GithubAuthError": "api.github.authentication_failed",
        "GithubOwnerScopeRequired": "api.github.owner_scope_required",
        "GithubDeleteScopeRequired": "api.github.delete_scope_required",
    }
    code = message_codes.get(exc.__class__.__name__)
    if code is None:
        return None
    return ui_message(
        code,
        str(exc),
        **({"detail": str(exc)} if code == "api.github.authentication_failed" else {}),
    )


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
                out.append({"id": resource_id, "name": _optional_str(item.get("name")) or ""})
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
    "resource_init": _resource_init,
    "resource_use": _resource_use,
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
    "config_check": _config_check,
    "config_branches": _config_branches,
    "config_bind_repo": _config_bind_repo,
    "config_save": _config_save,
    "github_auth_status": _github_auth_status,
    "github_auth_start": _github_auth_start,
    "github_auth_poll": _github_auth_poll,
    "github_auth_cancel": _github_auth_cancel,
    "github_web_auth_start": _github_web_auth_start,
    "github_web_auth_poll": _github_web_auth_poll,
    "github_web_auth_cancel": _github_web_auth_cancel,
    "github_token_reveal": _github_token_reveal,
    "github_token_clear": _github_token_clear,
    "github_owner_set": _github_owner_set,
    "platform_set_enabled": _platform_set_enabled,
    "write_default_config": _write_default_config,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lpm-desktop-api")
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
