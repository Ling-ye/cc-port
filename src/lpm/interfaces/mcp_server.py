"""LPM (LingyePluginMarketplace) MCP server.

Exposes the same operations as the CLI as MCP tools, so an AI coding agent
can publish, register, and sync skills, MCP servers, and rules directly from chat.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from ..core.config import load_config
from ..core.registry import find_registry_path, load_registry
from ..core.secrets import redact_item_dump
from ..services import publisher
from ..services.installer import check_all, status_all, sync_all, sync_one, uninstall_one
from ..services.local_resources import export_claude_plugin, import_local_resource

mcp = FastMCP("LPM")


@mcp.tool()
def list_items(kind: str | None = None) -> dict[str, Any]:
    """List all items (skills, MCP servers, rules) registered in registry.yaml.

    Args:
        kind: Optional filter by resource type: "skill", "mcp", or "rule".
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
            {"name": p.name, "enabled": p.enabled, "skills_dir": p.skills_dir, "mcp_json": p.mcp_json}
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
                "enabled": p.enabled,
                "skills_dir": p.skills_dir,
                "mcp_json": p.mcp_json,
                "rules_dir": p.rules_dir,
            }
            for p in cfg.platforms.profiles
        ],
    }


@mcp.tool()
def publish_local_skill(
    path: str,
    name: str | None = None,
    description: str | None = None,
    private: bool | None = None,
    update_visibility: bool = False,
    kind: str = "skill",
    mcp_config: dict[str, Any] | None = None,
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
        kind: Resource type: "skill", "mcp", or "rule".
        mcp_config: MCP server configuration dict (required when kind="mcp").
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
) -> dict[str, Any]:
    """Register a third-party resource in registry.yaml.

    The remote repository is verified to be reachable before adding.
    Set ``skip_verify=True`` to skip this check (e.g. for offline use).

    Args:
        github_url: HTTPS or SSH URL of the upstream repo.
        name: Optional explicit name (defaults inferred from URL or subdir).
        subdir: Optional path inside the repo where the resource lives.
        ref: Branch/tag/commit to track.
        description: Optional human description.
        kind: Resource type: "skill", "mcp", "rule", "prompt", or "plugin".
        mcp_config: MCP server configuration dict (for kind="mcp").
        skip_verify: Skip remote repository reachability check.
        tags: Optional tags for selective sync and discovery.
        category: Optional category label.
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
        )
    except publisher.RepoUnreachableError as exc:
        return {"error": "repo_unreachable", "message": str(exc)}
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
) -> dict[str, Any]:
    """Collect a third-party resource by recording its upstream URL only."""
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
    )


@mcp.tool()
def import_local_resource_tool(
    path: str,
    name: str | None = None,
    description: str | None = None,
    kind: str = "skill",
    category: str = "",
    tags: list[str] | None = None,
    overwrite: bool = False,
    mcp_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy a local resource into this LPM repository and register it."""
    try:
        result = import_local_resource(
            Path(path),
            kind=kind,
            name=name,
            description=description,
            category=category,
            tags=tags,
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
        skip_verify: Skip remote repository reachability check.
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
def remove_skill(name: str, uninstall: bool = False) -> dict[str, Any]:
    """Remove an item from the registry. Optionally also delete its local installation."""
    cfg = load_config()
    registry = load_registry()
    entry = registry.get(name)
    removed = publisher.remove_skill(name)
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
def update_skill(name: str) -> dict[str, Any]:
    """Force-sync a single item by name."""
    cfg = load_config()
    registry = load_registry()
    entry = registry.get(name)
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
