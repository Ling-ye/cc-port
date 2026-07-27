"""Project-level skill linking.

``cc-port link`` creates symlinks and a Cursor Rule index file in the current
project so that AI agents can automatically discover and use installed skills.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from ..core.config import Config
from ..core.models import RegistryItem
from ..core.registry import load_registry

CC_PORT_RULE_FILENAME = "cc-port-skills.md"
CC_PORT_LINK_MARKER = ".cc-port-linked"


def _project_cursor_dir(project: Path) -> Path:
    return project / ".cursor"


def _rules_dir(project: Path) -> Path:
    return _project_cursor_dir(project) / "rules"


def _skills_dir(project: Path) -> Path:
    return _project_cursor_dir(project) / "skills"


def _global_skill_path(config: Config, entry: RegistryItem) -> Path | None:
    """Return the globally-installed skill directory for *entry*, or None."""
    for plat in config.platforms.enabled():
        sp = plat.skills_path()
        if sp:
            candidate = sp / entry.install_target_name(plat.name)
            if candidate.exists():
                return candidate
    fallback = config.install.target_path / entry.install_target_name()
    return fallback if fallback.exists() else None


def _create_symlink(source: Path, link: Path) -> bool:
    """Create a symlink (or directory junction on Windows)."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink():
        return False
    try:
        link.symlink_to(source, target_is_directory=True)
    except OSError:
        if os.name == "nt":
            import subprocess
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(source)],
                check=True, capture_output=True,
            )
        else:
            raise
    return True


def _generate_rule_content(items: list[RegistryItem], config: Config) -> str:
    """Generate the cc-port-skills.md rule file content."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "---",
        "description: CC Port managed skill index. AI should read referenced SKILL.md files when matching trigger keywords.",
        "globs: \"**/*\"",
        "---",
        "",
        f"# CC Port Skill Index (auto-generated {now})",
        "",
        "The following Agent Skills are available in this project, managed by",
        "[CC Port](https://github.com/Ling-ye/cc-port).",
        "When the user's request matches a skill's trigger keywords, read the",
        "corresponding SKILL.md to learn how to use it.",
        "",
        "| Name | Kind | Description | Tags | Skill Path |",
        "|------|------|-------------|------|------------|",
    ]

    for item in sorted(items, key=lambda i: i.name):
        tags_str = ", ".join(item.tags) if item.tags else "-"
        skill_path = _global_skill_path(config, item)
        path_str = f"`{skill_path}`" if skill_path else "-"
        desc = item.description.replace("|", "/") if item.description else "-"
        lines.append(f"| {item.name} | {item.kind} | {desc} | {tags_str} | {path_str} |")

    lines.append("")
    return "\n".join(lines)


def link(
    project: Path,
    config: Config,
    *,
    only: list[str] | None = None,
    tags: list[str] | None = None,
    kind: str | None = None,
) -> tuple[list[str], Path]:
    """Link registry items into a project.

    Creates:
    1. ``.cursor/rules/cc-port-skills.md`` -- Cursor Rule for auto-discovery
    2. ``.cursor/skills/<name>`` -- symlinks to globally-installed skills

    Returns ``(linked_names, rule_path)``.
    """
    registry = load_registry()
    items = registry.items

    if only:
        items = [i for i in items if i.name in only]
    if tags:
        tag_set = set(tags)
        items = [i for i in items if tag_set & set(i.tags)]
    if kind:
        items = [i for i in items if i.kind == kind]

    linked: list[str] = []

    skills_dir = _skills_dir(project)
    for item in items:
        if item.kind != "skill":
            continue
        source = _global_skill_path(config, item)
        if source is None:
            continue
        link_path = skills_dir / item.install_target_name()
        _create_symlink(source, link_path)
        linked.append(item.name)

    rule_path = _rules_dir(project) / CC_PORT_RULE_FILENAME
    rule_path.parent.mkdir(parents=True, exist_ok=True)
    rule_path.write_text(_generate_rule_content(items, config), encoding="utf-8")

    marker = _project_cursor_dir(project) / CC_PORT_LINK_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        datetime.now(timezone.utc).isoformat(timespec="seconds"), encoding="utf-8"
    )

    return linked, rule_path


def unlink(project: Path) -> tuple[list[str], bool]:
    """Remove all CC Port-created links and the rule file from a project.

    Returns ``(removed_names, rule_removed)``.
    """
    removed: list[str] = []

    skills_dir = _skills_dir(project)
    if skills_dir.exists():
        for child in skills_dir.iterdir():
            if child.is_symlink():
                child.unlink()
                removed.append(child.name)
            elif child.is_junction() if hasattr(child, "is_junction") else False:
                child.unlink()
                removed.append(child.name)

    rule_path = _rules_dir(project) / CC_PORT_RULE_FILENAME
    rule_removed = False
    if rule_path.exists():
        rule_path.unlink()
        rule_removed = True

    marker = _project_cursor_dir(project) / CC_PORT_LINK_MARKER
    if marker.exists():
        marker.unlink()

    return removed, rule_removed
