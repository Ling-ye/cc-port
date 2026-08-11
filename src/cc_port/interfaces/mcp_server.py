"""CC Port MCP server.

Exposes the same operations as the CLI as MCP tools, so an AI coding agent can
publish, register, and sync skills, MCP servers, rules, prompts, plugins,
instructions, and memories.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

from fastmcp import FastMCP

from ..core.config import load_config
from ..core.models import ItemKind
from ..core.platforms import resolve_portable_resource_platforms
from ..core.registry import find_registry_path, load_registry
from ..core.secrets import redact_item_dump
from ..services import publisher
from ..services.asset_sync import (
    AssetBatchChoice,
    apply_asset_action_plan,
    apply_asset_batch_plan,
    build_asset_action_plan,
    build_asset_batch_plan,
    build_asset_inventory,
)
from ..services.installer import check_all, status_all, sync_all, sync_one, uninstall_one
from ..services.local_resources import export_claude_plugin, import_local_resource
from ..services.resource_manager import resource_install_plan

mcp = FastMCP("CC Port")

_ASSET_KINDS = {
    "skill",
    "mcp",
    "rule",
    "prompt",
    "plugin",
    "instruction",
    "memory",
}


def _jsonable(value: Any) -> Any:
    """Convert service dataclasses and paths to MCP structured-content values."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(item) for item in value]
    return str(value)


def _structured_asset_result(value: Any) -> dict[str, Any]:
    payload = _jsonable(value)
    if not isinstance(payload, dict):
        raise TypeError("Asset service returned a non-object result.")
    return payload


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
    values: list[dict[str, Any]] | None,
) -> list[AssetBatchChoice]:
    choices: list[AssetBatchChoice] = []
    for index, item in enumerate(values or []):
        resource_key = str(item.get("resource_key") or "").strip()
        if not resource_key:
            raise ValueError(f"Asset batch choice {index} requires resource_key.")
        reference_origin = item.get("reference_origin")
        plugin_dependencies = item.get("plugin_dependencies")
        choices.append(
            AssetBatchChoice(
                resource_key=resource_key,
                platform=str(item.get("platform") or "").strip(),
                local_instance_id=str(item.get("local_instance_id") or "").strip(),
                resolution=str(item.get("resolution") or "overwrite").strip(),
                new_name=str(item.get("new_name") or "").strip(),
                overwrite_unmanaged=_asset_choice_flag(
                    item,
                    "overwrite_unmanaged",
                    index=index,
                ),
                plugin_track=str(item.get("plugin_track") or "").strip(),
                ownership_confirmed=_asset_choice_flag(
                    item,
                    "ownership_confirmed",
                    index=index,
                ),
                link_target_confirmed=_asset_choice_flag(
                    item,
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


def _portable_resource_platforms(
    kind: str,
    platforms: list[str] | None,
) -> list[str] | None:
    """Resolve local profile ids to tool ids before writing portable metadata."""
    cfg = load_config()
    portable = resolve_portable_resource_platforms(cfg.platforms, kind, platforms)
    return portable or None


@mcp.tool()
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


@mcp.tool()
def list_skills() -> dict[str, Any]:
    """List all skills currently registered in registry.yaml (backward-compatible alias)."""
    return list_items(kind="skill")


@mcp.tool()
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


@mcp.tool()
def asset_inventory(
    scan_local: bool = False,
    refresh_remote: bool = True,
    scan_global: bool = True,
    project_ids: list[str] | None = None,
) -> dict[str, Any]:
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
    payload = _structured_asset_result(inventory)
    payload.pop("rows", None)
    return payload


@mcp.tool()
def asset_action_plan(
    action: str,
    kind: str,
    name: str,
    platform: str,
    local_instance_id: str = "",
    new_name: str = "",
    new_install_name: str = "",
    overwrite_unmanaged: bool = False,
    link_target_confirmed: bool = False,
) -> dict[str, Any]:
    """Persist one revalidatable asset action plan without applying it.

    ``platform`` is the stable profile id, not a tool id. Use ``list_platforms``
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
    return _structured_asset_result(plan)


@mcp.tool()
def asset_action_apply(operation_id: str) -> dict[str, Any]:
    """Revalidate and apply one persisted asset action plan by operation id."""
    result = apply_asset_action_plan(operation_id, config=load_config())
    return _structured_asset_result(result)


@mcp.tool()
def asset_batch_plan(
    direction: str,
    resource_keys: list[str],
    target_platforms: list[str] | None = None,
    choices: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a stateless upload or download plan for logical assets.

    Target platforms are exact profile ids. Choices may select a profile and
    local instance, request overwrite/rename/skip, confirm unmanaged or linked
    targets, and carry plugin reference decisions. The returned ``plan_hash``
    binds these inputs to the freshly scanned local and remote identities.
    """
    plan = build_asset_batch_plan(
        direction,
        resource_keys=resource_keys,
        target_platforms=target_platforms,
        choices=_asset_batch_choices(choices),
        config=load_config(),
    )
    return _structured_asset_result(plan)


@mcp.tool()
def asset_batch_apply(
    direction: str,
    resource_keys: list[str],
    plan_hash: str,
    target_platforms: list[str] | None = None,
    choices: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rebuild and apply a batch plan only if its plan hash is still current.

    Pass the same direction, resource keys, exact profile ids, and choices used
    for ``asset_batch_plan``. A changed identity or state returns ``stale-plan``
    and the newly structured plan without applying the old plan.
    """
    result = apply_asset_batch_plan(
        direction,
        resource_keys=resource_keys,
        target_platforms=target_platforms,
        choices=_asset_batch_choices(choices),
        expected_plan_hash=plan_hash,
        config=load_config(),
    )
    return _structured_asset_result(result)


@mcp.tool()
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
    """Validate a local directory, create a dedicated GitHub repository for it
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
def export_plugin(name: str | None = None) -> dict[str, Any]:
    """Generate .claude-plugin/plugin.json for local skills in this repository."""
    try:
        path = export_claude_plugin(plugin_name=name)
    except Exception as exc:  # noqa: BLE001 - MCP tools return errors as data
        return {"error": str(exc)}
    return {"path": str(path)}


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
