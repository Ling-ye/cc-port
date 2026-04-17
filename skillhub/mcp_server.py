"""SkillHub MCP server.

Exposes the same operations as the CLI as MCP tools, so a Cursor agent can
publish, register, and sync skills directly from chat.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from . import publisher
from .config import load_config
from .installer import status_all, sync_all, sync_one, uninstall_one
from .registry import find_registry_path, load_registry

mcp = FastMCP("SkillHub")


@mcp.tool()
def list_skills() -> dict[str, Any]:
    """List all skills currently registered in registry.yaml."""
    cfg = load_config()
    registry = load_registry()
    install_root = cfg.install.target_path
    return {
        "registry_path": str(find_registry_path()),
        "install_target": str(install_root),
        "skills": [
            {
                **s.model_dump(),
                "installed": (install_root / s.install_target_name()).exists(),
            }
            for s in registry.skills
        ],
    }


@mcp.tool()
def publish_local_skill(
    path: str,
    name: str | None = None,
    description: str | None = None,
    private: bool | None = None,
    update_visibility: bool = False,
) -> dict[str, Any]:
    """Validate a local skill directory, create a dedicated GitHub repository
    for it under the configured owner, push the contents, and record it in
    registry.yaml.

    Args:
        path: Absolute or user-relative path to the skill directory containing SKILL.md.
        name: Optional override for the skill name (defaults to SKILL.md frontmatter).
        description: Optional override for the description.
        private: Repo visibility. True = private, False = public, None = use the
            user's configured default. ALWAYS confirm this with the user before
            calling unless they have explicitly stated their preference.
        update_visibility: If the GitHub repo already exists with a different
            visibility than `private`, set this to True to flip it. Defaults to
            False, which raises an error on mismatch (so we never silently
            change visibility without consent).
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
        )
    except publisher.VisibilityMismatchError as exc:
        return {
            "error": "visibility_mismatch",
            "message": str(exc),
            "full_name": exc.full_name,
            "current_private": exc.current_private,
            "requested_private": exc.requested_private,
            "hint": "Re-run with update_visibility=True to change it, or pass private= matching the current state.",
        }
    return {
        "name": result.name,
        "repo_url": result.repo_url,
        "full_name": result.full_name,
        "created_repo": result.created,
        "pushed": result.pushed,
        "private": result.private,
        "visibility_changed": result.visibility_changed,
        "entry": result.entry.model_dump(),
    }


@mcp.tool()
def set_skill_visibility(name: str, private: bool) -> dict[str, Any]:
    """Change the GitHub visibility of an `owned` skill repository.

    Args:
        name: Name of an `owned` skill in the registry.
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
) -> dict[str, Any]:
    """Register a third-party skill repository in registry.yaml.

    Args:
        github_url: HTTPS or SSH URL of the upstream repo.
        name: Optional explicit name (defaults inferred from URL or subdir).
        subdir: Optional path inside the repo where SKILL.md lives.
        ref: Branch/tag/commit to track.
        description: Optional human description.
    """
    entry = publisher.add_external_skill(
        github_url,
        name=name,
        subdir=subdir,
        ref=ref,
        description=description,
    )
    return entry.model_dump()


@mcp.tool()
def remove_skill(name: str, uninstall: bool = False) -> dict[str, Any]:
    """Remove a skill from the registry. Optionally also delete its local installation."""
    cfg = load_config()
    registry = load_registry()
    entry = registry.get(name)
    removed = publisher.remove_skill(name)
    detail = {"removed": removed.model_dump() if removed else None, "uninstalled": False}
    if uninstall and entry is not None:
        detail["uninstalled"] = uninstall_one(entry, config=cfg)
    return detail


@mcp.tool()
def sync_skills(only: list[str] | None = None) -> dict[str, Any]:
    """Install or update every skill in the registry under the configured target.

    Args:
        only: Optional list of skill names to restrict the sync to.
    """
    cfg = load_config()
    results = sync_all(config=cfg, only=only)
    return {
        "install_target": str(cfg.install.target_path),
        "results": [
            {
                "name": r.name,
                "action": r.action.value,
                "install_path": str(r.install_path),
                "detail": r.detail,
            }
            for r in results
        ],
    }


@mcp.tool()
def update_skill(name: str) -> dict[str, Any]:
    """Force-sync a single skill by name."""
    cfg = load_config()
    registry = load_registry()
    entry = registry.get(name)
    if entry is None:
        return {"error": f"No skill named {name!r} in registry."}
    result = sync_one(entry, config=cfg)
    return {
        "name": result.name,
        "action": result.action.value,
        "install_path": str(result.install_path),
        "detail": result.detail,
    }


@mcp.tool()
def skill_status() -> dict[str, Any]:
    """Report local vs remote commit status for every registered skill."""
    cfg = load_config()
    rows = status_all(config=cfg)
    return {
        "install_target": str(cfg.install.target_path),
        "skills": [
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
