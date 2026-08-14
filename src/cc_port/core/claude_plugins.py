"""Claude Code plugin package inspection.

Claude Code deliberately distinguishes a plain skill directory from a
skills-directory plugin: the latter must contain
``.claude-plugin/plugin.json``.  Keep that format knowledge in one place so
discovery, upload validation, and installation cannot drift apart.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frontmatter

CLAUDE_PLUGIN_MANIFEST = Path(".claude-plugin/plugin.json")
CLAUDE_PLUGIN_NAME_RE = re.compile(r"^(?=.{1,128}$)[a-z0-9]+(?:-[a-z0-9]+)*$")
CLAUDE_PLUGIN_PATH_FIELDS = {
    "agents",
    "commands",
    "hooks",
    "lspServers",
    "mcpServers",
    "outputStyles",
    "skills",
    "workflows",
}
CLAUDE_PLUGIN_DEFAULT_COMPONENTS = (
    "SKILL.md",
    "skills",
    "commands",
    "agents",
    "workflows",
    "output-styles",
    "themes",
    "hooks/hooks.json",
    ".mcp.json",
    ".lsp.json",
    "monitors/monitors.json",
    "bin",
    "settings.json",
)


class ClaudePluginFormatError(ValueError):
    """Raised when a Claude Code plugin package is structurally unsafe."""


@dataclass(frozen=True)
class ClaudePluginMetadata:
    name: str
    description: str
    version: str
    manifest_path: Path | None
    components: tuple[str, ...]

    @property
    def skills_dir_installable(self) -> bool:
        """Whether Claude can load this package directly from a skills dir."""
        return self.manifest_path is not None


@dataclass(frozen=True)
class ClaudeSkillMetadata:
    name: str
    description: str
    skill_md_path: Path


def inspect_claude_skill(path: Path) -> ClaudeSkillMetadata:
    """Inspect one native Claude skill without imposing cross-tool frontmatter."""
    expanded = path.expanduser().absolute()
    if expanded.is_symlink() or not expanded.is_dir():
        raise ClaudePluginFormatError(f"{path} must be a regular Claude skill directory.")
    if expanded.name.casefold() == "synced":
        raise ClaudePluginFormatError(
            "Claude's reserved synced skill directory is runtime-managed and not portable."
        )
    if (expanded / CLAUDE_PLUGIN_MANIFEST).exists():
        raise ClaudePluginFormatError(
            f"{expanded} is a Claude skills-directory plugin, not a plain skill."
        )
    name = expanded.name
    if not CLAUDE_PLUGIN_NAME_RE.fullmatch(name):
        raise ClaudePluginFormatError(
            f"Claude skill directory name {name!r} must be a safe kebab-case command name."
        )
    skill_md = expanded / "SKILL.md"
    if skill_md.is_symlink() or not skill_md.is_file():
        raise ClaudePluginFormatError(
            f"Claude skill {expanded} requires a regular root SKILL.md file."
        )
    try:
        post = frontmatter.load(skill_md)
    except Exception as exc:  # noqa: BLE001 - normalize parser failures
        raise ClaudePluginFormatError(
            f"Claude skill frontmatter is invalid in {skill_md}: {exc}"
        ) from exc
    if not isinstance(post.metadata, dict):
        raise ClaudePluginFormatError(f"Claude skill frontmatter in {skill_md} must be a mapping.")
    body = str(post.content or "").strip()
    if not body:
        raise ClaudePluginFormatError(f"Claude skill {skill_md} has no instructions.")
    description = str(post.metadata.get("description") or "").strip()
    if not description:
        description = body.split("\n\n", 1)[0].strip()
    return ClaudeSkillMetadata(
        name=name,
        description=description,
        skill_md_path=skill_md,
    )


def inspect_claude_plugin(
    path: Path,
    *,
    require_manifest: bool = False,
) -> ClaudePluginMetadata:
    """Inspect one Claude plugin source without executing any of its content."""
    expanded = path.expanduser().absolute()
    if expanded.is_symlink() or not expanded.is_dir():
        raise ClaudePluginFormatError(f"{path} must be a regular Claude plugin directory.")
    root = expanded.resolve()

    manifest_path = root / CLAUDE_PLUGIN_MANIFEST
    manifest: dict[str, Any] = {}
    if manifest_path.exists() or manifest_path.is_symlink():
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ClaudePluginFormatError(
                f"{manifest_path} must be a regular plugin manifest file."
            )
        manifest = _read_json_object(manifest_path, label="Claude plugin manifest")
    elif require_manifest:
        raise ClaudePluginFormatError(
            f"{root} is a plain skill or manifestless marketplace source, not a "
            "Claude skills-directory plugin."
        )

    misplaced = [
        name
        for name in (
            "skills",
            "commands",
            "agents",
            "hooks",
            "workflows",
            "output-styles",
            "themes",
            "monitors",
            "bin",
        )
        if (root / ".claude-plugin" / name).exists()
    ]
    if misplaced:
        raise ClaudePluginFormatError(
            "Claude plugin components must be at the plugin root, not inside "
            f".claude-plugin: {', '.join(sorted(misplaced))}."
        )

    if manifest and not isinstance(manifest.get("name"), str):
        raise ClaudePluginFormatError(
            f"Claude plugin manifest {manifest_path} requires a string `name` field."
        )
    name = str(manifest.get("name") or root.name).strip()
    if not CLAUDE_PLUGIN_NAME_RE.fullmatch(name):
        raise ClaudePluginFormatError(
            f"Claude plugin name {name!r} must be kebab-case lowercase letters and digits."
        )

    _validate_manifest_paths(root, manifest)
    _validate_component_json(root)
    _validate_markdown_frontmatter(root)
    components = tuple(
        relative for relative in CLAUDE_PLUGIN_DEFAULT_COMPONENTS if (root / relative).exists()
    )
    custom_components = tuple(
        sorted(field for field in CLAUDE_PLUGIN_PATH_FIELDS if field in manifest)
    )
    if not manifest and not components:
        raise ClaudePluginFormatError(
            f"{root} has neither a Claude plugin manifest nor a recognized plugin component."
        )

    return ClaudePluginMetadata(
        name=name,
        description=str(manifest.get("description") or "").strip(),
        version=str(manifest.get("version") or "").strip(),
        manifest_path=manifest_path if manifest else None,
        components=tuple(dict.fromkeys((*components, *custom_components))),
    )


def is_claude_skills_dir_plugin(path: Path) -> bool:
    """Return whether a skills-dir child is a manifest-backed Claude plugin."""
    manifest = path / CLAUDE_PLUGIN_MANIFEST
    return (
        path.is_dir() and not path.is_symlink() and manifest.is_file() and not manifest.is_symlink()
    )


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    payload = _read_json_value(path, label=label)
    if not isinstance(payload, dict):
        raise ClaudePluginFormatError(f"{label} {path} must contain a JSON object.")
    return payload


def _read_json_value(path: Path, *, label: str) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ClaudePluginFormatError(f"{label} {path} is invalid JSON: {exc}") from exc
    return payload


def _validate_manifest_paths(root: Path, manifest: dict[str, Any]) -> None:
    for field in CLAUDE_PLUGIN_PATH_FIELDS:
        if field not in manifest or isinstance(manifest[field], dict):
            continue
        raw_values = manifest[field]
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        if not all(isinstance(value, str) for value in values):
            raise ClaudePluginFormatError(
                f"Claude plugin manifest field {field!r} must use string paths or an object."
            )
        for value in values:
            _validate_component_path(root, field, value)

    experimental = manifest.get("experimental")
    if experimental is not None and not isinstance(experimental, dict):
        raise ClaudePluginFormatError("Claude plugin experimental settings must be an object.")
    if isinstance(experimental, dict):
        for field in ("themes", "monitors"):
            if field not in experimental:
                continue
            raw_values = experimental[field]
            if (
                field == "monitors"
                and isinstance(raw_values, list)
                and all(isinstance(value, dict) for value in raw_values)
            ):
                # Monitors may be declared inline as an array of monitor
                # objects instead of loading one or more path-based configs.
                continue
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            if not all(isinstance(value, str) for value in values):
                raise ClaudePluginFormatError(
                    f"Claude plugin experimental field {field!r} must use string paths."
                )
            for value in values:
                _validate_component_path(root, f"experimental.{field}", value)


def _validate_component_path(root: Path, field: str, value: str) -> None:
    normalized = value.strip().replace("\\", "/")
    if normalized in {".", "./"} and field == "skills":
        return
    normalized = normalized.rstrip("/")
    if (
        not normalized.startswith("./")
        or normalized.startswith("../")
        or any(part in {"", ".", ".."} for part in normalized[2:].split("/"))
    ):
        raise ClaudePluginFormatError(
            f"Claude plugin path {value!r} in {field!r} must be a safe './' relative path."
        )
    candidate = root.joinpath(*normalized[2:].split("/"))
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ClaudePluginFormatError(
            f"Claude plugin path {value!r} in {field!r} cannot be resolved safely."
        ) from exc
    if resolved != root and root not in resolved.parents:
        raise ClaudePluginFormatError(
            f"Claude plugin path {value!r} in {field!r} escapes the plugin root."
        )
    if candidate.is_symlink() or not candidate.exists():
        raise ClaudePluginFormatError(
            f"Claude plugin path {value!r} in {field!r} must resolve to existing "
            "non-linked content."
        )


def _validate_component_json(root: Path) -> None:
    for relative, label, expected_type in (
        ("hooks/hooks.json", "Claude plugin hooks", dict),
        (".mcp.json", "Claude plugin MCP configuration", dict),
        (".lsp.json", "Claude plugin LSP configuration", dict),
        ("monitors/monitors.json", "Claude plugin monitors", list),
        ("settings.json", "Claude plugin settings", dict),
    ):
        path = root / relative
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                raise ClaudePluginFormatError(f"{path} must be a regular JSON file.")
            payload = _read_json_value(path, label=label)
            if not isinstance(payload, expected_type):
                expected = "object" if expected_type is dict else "array"
                raise ClaudePluginFormatError(f"{label} {path} must contain a JSON {expected}.")


def _validate_markdown_frontmatter(root: Path) -> None:
    candidates: list[Path] = []
    root_skill = root / "SKILL.md"
    if root_skill.is_file():
        candidates.append(root_skill)
    for directory in ("skills", "commands", "agents", "output-styles"):
        component_root = root / directory
        if not component_root.is_dir():
            continue
        candidates.extend(
            path
            for path in component_root.rglob("*.md")
            if path.is_file() and not path.is_symlink()
        )
    for path in candidates:
        try:
            post = frontmatter.load(path)
        except Exception as exc:  # noqa: BLE001 - normalize parser failures
            raise ClaudePluginFormatError(
                f"Claude plugin Markdown frontmatter is invalid in {path}: {exc}"
            ) from exc
        if not isinstance(post.metadata, dict):
            raise ClaudePluginFormatError(
                f"Claude plugin Markdown frontmatter in {path} must be a mapping."
            )
