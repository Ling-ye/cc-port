"""Discover local AI coding resources from common tool directories."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from ..core.claude_plugins import CLAUDE_PLUGIN_MANIFEST, inspect_claude_skill
from ..core.models import ItemKind
from ..core.registry import load_registry
from ..core.validator import RULE_FILE_NAMES, RULE_FILE_SUFFIXES, parse_skill, validate_item
from .install_planner import MANIFEST_FILENAMES, load_resource_manifest
from .local_path_probe import (
    LocalPathProbe,
    is_known_canonical_link_target,
    probe_local_path,
    resource_tree_issues,
)
from .publisher import _slug

DiscoveryScope = str

DEFAULT_MAX_DEPTH = 4
PREVIEW_MAX_CHARS = 20_000
TEXT_SAMPLE_BYTES = 64_000
CLAUDE_MEMORY_STARTUP_MAX_BYTES = 25 * 1024
CLAUDE_MEMORY_STARTUP_MAX_LINES = 200

EXCLUDED_DIR_NAMES = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".system",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "cache",
    "coverage",
    "dist",
    "env",
    "node_modules",
    "out",
    "target",
    "temp",
    "tmp",
    "venv",
}
ALLOWED_HIDDEN_DIR_NAMES = {".claude", ".codex", ".cursor"}
PROMPT_DIR_NAMES = {"commands", "prompts"}
RULE_DIR_NAMES = {"rules"}
INSTRUCTION_FILE_NAMES = {
    "agents.md",
    "agents.override.md",
    "claude.md",
    "claude.local.md",
}


@dataclass
class DiscoveredResource:
    id: str
    tool: str
    source: str
    kind: ItemKind
    name_hint: str
    path: Path
    content_path: Path | None = None
    path_kind: str = "regular"
    link_health: str = "ready"
    link_target: str = ""
    reparse_tag: str = ""
    link_target_trusted: bool = True
    description: str = ""
    size: int = 0
    mtime: float = 0
    exists_in_registry: bool = False
    status: str = "ready"
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    tool_id: str = ""
    environment_kind: str = ""
    environment_name: str = ""
    display_name: str = ""
    install_name_hint: str = ""


@dataclass
class DiscoveryReadResult:
    id: str
    path: Path
    text: str
    truncated: bool = False
    warning: str = ""


def discover_resources(
    *,
    scope: DiscoveryScope = "global",
    root_path: Path | str | None = None,
    registry_path: Path | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    file_kind_hint: ItemKind | None = None,
    tool_hint: str = "",
) -> list[DiscoveredResource]:
    """Discover resource candidates without modifying any source directory."""
    roots = _roots_for_scope(scope=scope, root_path=root_path)
    registry_keys = (
        {
            (item.kind, item.name)
            for item in load_registry(registry_path).items
        }
        if registry_path is not None
        else set()
    )
    candidates: list[DiscoveredResource] = []
    seen: set[tuple[str, str]] = set()

    for tool, root in roots:
        root_probe = probe_local_path(root)
        if not root_probe.ready:
            continue
        effective_tool = tool_hint or _infer_tool(root, default=tool)
        candidates.extend(
            _scan_root(
                root.absolute(),
                tool=effective_tool,
                source=scope,
                max_depth=max_depth,
                seen=seen,
                file_kind_hint=file_kind_hint,
            )
        )

    _mark_conflicts(candidates, registry_keys)
    return sorted(candidates, key=lambda item: (item.tool, item.kind, item.name_hint, str(item.path)))


def discover_exact_resource(
    path: Path | str,
    *,
    tool: str,
    kind: ItemKind,
    name_hint: str,
    source: str = "configured",
    warnings: list[str] | None = None,
) -> DiscoveredResource | None:
    """Discover one explicitly scoped file/directory without scanning its parents."""
    logical = Path(path).expanduser().absolute()
    probe = probe_local_path(logical)
    if probe.health == "missing":
        return None
    if not probe.ready or probe.content_path is None:
        blocked = _blocked_resource_from_probe(
            logical,
            probe,
            tool=tool,
            source=source,
            kind_hint=kind,
        )
        if blocked is not None:
            blocked.name_hint = _slug(name_hint)
        return blocked
    content_path = probe.content_path
    validation_error = ""
    try:
        validate_item(content_path, kind)
    except Exception as exc:  # noqa: BLE001 - discovery surfaces validation as a warning
        validation_error = str(exc)
        validation_warnings = list(warnings or [])
    else:
        validation_warnings = list(warnings or [])
    if kind == "memory" and content_path.is_dir():
        validation_warnings.extend(_memory_entrypoint_warnings(content_path))
    resource = _resource(
        path=logical,
        content_path=content_path,
        probe=probe,
        marker=content_path,
        tool=tool,
        source=source,
        kind=kind,
        name_hint=_slug(name_hint),
        description=_file_description(content_path) if content_path.is_file() else "",
        warnings=validation_warnings,
    )
    if validation_error:
        resource.blockers.append(validation_error)
        resource.status = "blocked"
    return resource


def _memory_entrypoint_warnings(memory_path: Path) -> list[str]:
    """Warn about Claude's bounded startup load without modifying the payload."""
    entrypoint = memory_path / "MEMORY.md"
    try:
        over_limit = _claude_memory_startup_over_limit(entrypoint)
    except (OSError, UnicodeError):
        return []
    if not over_limit:
        return []
    return [
        "Claude Code only loads the first 200 lines or 25 KiB of MEMORY.md "
        "at startup; CC Port preserves the complete file during migration."
    ]


def _claude_memory_startup_over_limit(path: Path) -> bool:
    """Stream Claude's effective startup text with a bounded visible buffer."""
    visible = ""
    in_comment = False
    with path.open("r", encoding="utf-8") as handle:
        first = handle.readline()
        if first.strip() == "---":
            candidate, candidate_in_comment = _append_visible_memory_text(
                "",
                first,
                in_comment=False,
            )
            candidate_over_limit = _visible_memory_over_limit(candidate)
            closed_frontmatter = False
            for line in handle:
                if line.strip() in {"---", "..."}:
                    closed_frontmatter = True
                    break
                if not candidate_over_limit:
                    candidate, candidate_in_comment = _append_visible_memory_text(
                        candidate,
                        line,
                        in_comment=candidate_in_comment,
                    )
                    candidate_over_limit = _visible_memory_over_limit(candidate)
            if not closed_frontmatter:
                return candidate_over_limit
        else:
            visible, in_comment = _append_visible_memory_text(
                visible,
                first,
                in_comment=in_comment,
            )
        if _visible_memory_over_limit(visible):
            return True
        for line in handle:
            visible, in_comment = _append_visible_memory_text(
                visible,
                line,
                in_comment=in_comment,
            )
            if _visible_memory_over_limit(visible):
                return True
    return False


def _append_visible_memory_text(
    current: str,
    text: str,
    *,
    in_comment: bool,
) -> tuple[str, bool]:
    output: list[str] = []
    cursor = 0
    while cursor < len(text):
        if in_comment:
            closing = text.find("-->", cursor)
            if closing < 0:
                return _bounded_visible_text(current + "".join(output)), True
            cursor = closing + 3
            in_comment = False
            continue
        opening = text.find("<!--", cursor)
        if opening < 0:
            output.append(text[cursor:])
            break
        output.append(text[cursor:opening])
        cursor = opening + 4
        in_comment = True
    return _bounded_visible_text(current + "".join(output)), in_comment


def _bounded_visible_text(text: str) -> str:
    max_chars = CLAUDE_MEMORY_STARTUP_MAX_BYTES + CLAUDE_MEMORY_STARTUP_MAX_LINES + 1
    return text[:max_chars]


def _visible_memory_over_limit(text: str) -> bool:
    return (
        len(text.encode("utf-8")) > CLAUDE_MEMORY_STARTUP_MAX_BYTES
        or len(text.splitlines()) > CLAUDE_MEMORY_STARTUP_MAX_LINES
    )


def read_discovered_resource(
    resource_id: str,
    *,
    scope: DiscoveryScope = "global",
    root_path: Path | str | None = None,
    max_chars: int = PREVIEW_MAX_CHARS,
) -> DiscoveryReadResult:
    """Read a bounded text preview for a discovered candidate."""
    candidate = _find_candidate(resource_id, scope=scope, root_path=root_path)
    preview_path = _preview_path(candidate)
    text, truncated, warning = _read_text_preview(preview_path, max_chars=max_chars)
    return DiscoveryReadResult(
        id=candidate.id,
        path=preview_path,
        text=text,
        truncated=truncated,
        warning=warning,
    )


def resolve_discovered_resources(
    resource_ids: list[str],
    *,
    scope: DiscoveryScope = "global",
    root_path: Path | str | None = None,
) -> list[DiscoveredResource]:
    """Resolve candidate ids by rescanning their declared discovery scope."""
    by_id = {item.id: item for item in discover_resources(scope=scope, root_path=root_path)}
    missing = [resource_id for resource_id in resource_ids if resource_id not in by_id]
    if missing:
        raise ValueError(f"Discovered resource id(s) no longer available: {', '.join(missing)}")
    return [by_id[resource_id] for resource_id in resource_ids]


def _roots_for_scope(
    *,
    scope: DiscoveryScope,
    root_path: Path | str | None,
) -> list[tuple[str, Path]]:
    if scope == "global":
        home = Path.home()
        return [
            ("codex", home / ".codex"),
            # Claude's config root also contains credentials, transcripts,
            # history, caches, and per-project state.  Only scan the documented
            # portable roots; user instructions are handled as one exact file.
            ("claude-code", home / ".claude" / "skills"),
            ("claude-code", home / ".claude" / "rules"),
            ("cursor", home / ".cursor"),
            ("windsurf", home / ".windsurf"),
            ("opencode", home / ".config" / "opencode"),
            ("gemini", home / ".gemini"),
        ]
    if scope == "directory":
        if root_path is None or str(root_path).strip() == "":
            raise ValueError("root_path is required when scope='directory'.")
        root = Path(root_path).expanduser()
        if not root.is_dir():
            raise ValueError(f"{root} is not a directory.")
        return [(_infer_tool(root, default="directory"), root)]
    raise ValueError("scope must be 'global' or 'directory'.")


def _scan_root(
    root: Path,
    *,
    tool: str,
    source: str,
    max_depth: int,
    seen: set[tuple[str, str]],
    file_kind_hint: ItemKind | None,
) -> list[DiscoveredResource]:
    out: list[DiscoveredResource] = []
    stack: list[tuple[Path, int]] = [(root, 0)]

    while stack:
        current, depth = stack.pop()
        probe = probe_local_path(current)
        if not probe.ready:
            blocked = _blocked_resource_from_probe(
                current,
                probe,
                tool=tool,
                source=source,
                kind_hint=file_kind_hint,
            )
            if blocked is not None:
                _add_candidate(out, blocked, seen)
            continue
        content_path = probe.content_path
        if content_path is None:
            continue
        try:
            content_is_dir = content_path.is_dir()
            content_is_file = content_path.is_file()
        except OSError:
            continue
        if content_is_dir:
            if _is_excluded_dir(current, root=root):
                continue
            candidate = _candidate_from_directory(
                current,
                content_path=content_path,
                probe=probe,
                tool=tool,
                source=source,
            )
            if candidate is not None:
                _add_candidate(out, candidate, seen)
                continue
            if current != root and probe.is_link:
                continue
            if depth >= max_depth:
                continue
            try:
                children = sorted(content_path.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                continue
            for child in reversed(children):
                logical_child = current / child.name
                child_probe = probe_local_path(logical_child)
                if not child_probe.ready:
                    blocked = _blocked_resource_from_probe(
                        logical_child,
                        child_probe,
                        tool=_infer_tool(logical_child, default=tool),
                        source=source,
                        kind_hint=file_kind_hint,
                    )
                    if blocked is not None:
                        _add_candidate(out, blocked, seen)
                    continue
                child_content = child_probe.content_path
                if child_content is None:
                    continue
                try:
                    child_is_dir = child_content.is_dir()
                    child_is_file = child_content.is_file()
                except OSError:
                    continue
                if child_probe.is_link and child_is_dir:
                    candidate = _candidate_from_directory(
                        logical_child,
                        content_path=child_content,
                        probe=child_probe,
                        tool=_infer_tool(logical_child, default=tool),
                        source=source,
                    )
                    if candidate is not None:
                        _add_candidate(out, candidate, seen)
                    continue
                if child_probe.is_link and child_is_file:
                    candidate = _candidate_from_file(
                        logical_child,
                        content_path=child_content,
                        probe=child_probe,
                        tool=_infer_tool(logical_child, default=tool),
                        source=source,
                        kind_hint=file_kind_hint,
                    )
                    if candidate is not None:
                        _add_candidate(out, candidate, seen)
                    continue
                if child_is_dir:
                    stack.append((logical_child, depth + 1))
                elif child_is_file:
                    candidate = _candidate_from_file(
                        logical_child,
                        content_path=child_content,
                        probe=child_probe,
                        tool=_infer_tool(logical_child, default=tool),
                        source=source,
                        kind_hint=file_kind_hint,
                    )
                    if candidate is not None:
                        _add_candidate(out, candidate, seen)
        elif content_is_file:
            candidate = _candidate_from_file(
                current,
                content_path=content_path,
                probe=probe,
                tool=_infer_tool(current, default=tool),
                source=source,
                kind_hint=file_kind_hint,
            )
            if candidate is not None:
                _add_candidate(out, candidate, seen)
    return out


def _candidate_from_directory(
    path: Path,
    *,
    content_path: Path,
    probe: LocalPathProbe,
    tool: str,
    source: str,
) -> DiscoveredResource | None:
    manifest_candidate = _candidate_from_manifest(
        path,
        content_path=content_path,
        probe=probe,
        tool=tool,
        source=source,
    )
    if manifest_candidate is not None:
        return manifest_candidate

    # Claude skills directories contain two different native formats.  A
    # manifest-backed child is a plugin even when it also has a root SKILL.md;
    # a child with only SKILL.md remains a plain skill.
    if tool == "claude-code":
        plugin_manifest = content_path / CLAUDE_PLUGIN_MANIFEST
        plugin_probe = probe_local_path(plugin_manifest)
        if plugin_probe.health != "missing":
            return _resource(
                path=path,
                content_path=content_path,
                probe=probe,
                marker=(
                    plugin_probe.content_path
                    if plugin_probe.ready and plugin_probe.content_path is not None
                    else content_path
                ),
                tool=tool,
                source=source,
                kind="plugin",
                name_hint=_slug(path.name),
                description="",
                warnings=[],
            )

    skill_md = content_path / "SKILL.md"
    skill_probe = probe_local_path(skill_md)
    if skill_probe.health != "missing" and (
        not skill_probe.ready or skill_probe.path_kind != "regular"
    ):
        return _resource(
            path=path,
            content_path=content_path,
            probe=probe,
            marker=content_path,
            tool=tool,
            source=source,
            kind="skill",
            name_hint=_slug(path.name),
            description="",
            warnings=[],
        )
    if (
        skill_probe.ready
        and skill_probe.content_path is not None
        and skill_probe.content_path.is_file()
    ):
        warnings: list[str] = []
        name_hint = _slug(path.name)
        description = ""
        try:
            meta = (
                inspect_claude_skill(content_path)
                if tool == "claude-code"
                else parse_skill(content_path)
            )
            name_hint = _slug(meta.name)
            description = meta.description
        except Exception as exc:  # noqa: BLE001 - discovery reports invalid metadata as a warning
            warnings.append(str(exc))

        return _resource(
            path=path,
            content_path=content_path,
            probe=probe,
            marker=skill_md,
            tool=tool,
            source=source,
            kind="skill",
            name_hint=name_hint,
            description=description,
            warnings=warnings,
        )

    for marker in (
        content_path / ".claude-plugin" / "plugin.json",
        content_path / ".codex-plugin" / "plugin.json",
    ):
        marker_probe = probe_local_path(marker)
        if marker_probe.health == "missing":
            continue
        if not marker_probe.ready or marker_probe.path_kind != "regular":
            return _resource(
                path=path,
                content_path=content_path,
                probe=probe,
                marker=content_path,
                tool=tool,
                source=source,
                kind="plugin",
                name_hint=_slug(path.name),
                description="",
                warnings=[],
            )
        if marker_probe.content_path is not None and marker_probe.content_path.is_file():
            return _resource(
                path=path,
                content_path=content_path,
                probe=probe,
                marker=marker,
                tool=tool,
                source=source,
                kind="plugin",
                name_hint=_slug(path.name),
                description="",
                warnings=[],
            )
    return None


def _candidate_from_file(
    path: Path,
    *,
    content_path: Path,
    probe: LocalPathProbe,
    tool: str,
    source: str,
    kind_hint: ItemKind | None = None,
) -> DiscoveredResource | None:
    if source == "global" and _is_global_user_instruction_location(path, tool=tool):
        # User instructions are profile-bound resources.  The generic scanner
        # has no configured profile/home identity, so exposing them here would
        # mislabel Windows content as WSL (or vice versa).  Environment-aware
        # discovery owns this resource type.
        return None
    kind = _file_kind(
        path,
        kind_hint=kind_hint,
        allow_instruction=_is_scoped_instruction_path(
            path,
            tool=tool,
            source=source,
        ),
    )
    if kind is None:
        return None
    if kind == "instruction" and tool == "codex" and not _has_non_whitespace_text(
        content_path
    ):
        return None
    name_hint = _slug(path.stem if path.stem else path.name.lstrip("."))
    description = _file_description(content_path)
    resource = _resource(
        path=path,
        content_path=content_path,
        probe=probe,
        marker=content_path,
        tool=tool,
        source=source,
        kind=kind,
        name_hint=name_hint,
        description=description,
        warnings=[],
    )
    if kind == "instruction" and source == "directory":
        resource.blockers.append(
            "Project instruction files are observation-only until a project-specific "
            "target identity is configured; they cannot be migrated as user instructions."
        )
        resource.status = "blocked"
    if (
        kind == "rule"
        and source == "directory"
        and tool == "claude-code"
        and _has_parent_named(path, {".claude"})
    ):
        resource.blockers.append(
            "Project Claude rules are observation-only until a project-specific "
            "target identity is configured; they cannot be promoted to user rules."
        )
        resource.status = "blocked"
    return resource


def _resource(
    *,
    path: Path,
    content_path: Path,
    probe: LocalPathProbe,
    marker: Path,
    tool: str,
    source: str,
    kind: ItemKind,
    name_hint: str,
    description: str,
    warnings: list[str],
) -> DiscoveredResource:
    stat = marker.stat()
    logical_path = path.expanduser().absolute()
    tree_issues = resource_tree_issues(content_path) if content_path.is_dir() else []
    blockers = [
        f"Nested link or unreadable entry at {item.relative_path}: {item.detail}"
        for item in tree_issues
    ]
    status = "blocked" if blockers else "warning" if warnings else "ready"
    return DiscoveredResource(
        id=_candidate_id(tool=tool, source=source, kind=kind, path=logical_path),
        tool=tool,
        source=source,
        kind=kind,
        name_hint=name_hint,
        path=logical_path,
        content_path=content_path,
        path_kind=probe.path_kind,
        link_health=probe.health,
        link_target=probe.raw_target,
        reparse_tag=probe.reparse_tag_hex,
        link_target_trusted=not probe.is_link or is_known_canonical_link_target(probe),
        description=description,
        size=stat.st_size,
        mtime=stat.st_mtime,
        status=status,
        warnings=warnings,
        blockers=blockers,
    )


def _file_kind(
    path: Path,
    *,
    kind_hint: ItemKind | None = None,
    allow_instruction: bool = False,
) -> ItemKind | None:
    lower = path.name.lower()
    suffix = path.suffix.lower()
    if kind_hint == "prompt" and suffix == ".md":
        return "prompt"
    if kind_hint == "rule" and suffix in RULE_FILE_SUFFIXES:
        return "rule"
    if suffix == ".md" and _has_parent_named(path, RULE_DIR_NAMES):
        return "rule"
    if allow_instruction and lower in INSTRUCTION_FILE_NAMES:
        return "instruction"
    if lower in {"mcp.json", "mcp.yaml", "mcp.yml"}:
        return "mcp"
    if lower in RULE_FILE_NAMES:
        return "rule"
    if suffix == ".mdc":
        return "rule"
    if suffix != ".md":
        return None
    if _has_parent_named(path, PROMPT_DIR_NAMES):
        return "prompt"
    if _has_parent_named(path, RULE_DIR_NAMES):
        return "rule"
    return None


def _is_scoped_instruction_path(path: Path, *, tool: str, source: str) -> bool:
    lower = path.name.casefold()
    if lower not in INSTRUCTION_FILE_NAMES:
        return False
    return source == "directory"


def _is_global_user_instruction_location(path: Path, *, tool: str) -> bool:
    lower = path.name.casefold()
    parent = path.parent.name.casefold()
    if tool == "claude-code":
        return lower == "claude.md" and parent == ".claude"
    return (
        tool == "codex"
        and parent == ".codex"
        and lower in {"agents.md", "agents.override.md"}
    )


def _has_non_whitespace_text(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as handle:
            while chunk := handle.read(8192):
                if chunk.strip():
                    return True
    except (OSError, UnicodeError):
        # Keep unreadable candidates visible so their path safety problem is not
        # silently converted into absence.
        return True
    return False


def _file_description(path: Path) -> str:
    text, _, warning = _read_text_preview(path, max_chars=TEXT_SAMPLE_BYTES)
    if warning:
        return ""
    try:
        post = frontmatter.loads(text)
    except Exception:
        post = None
    if post is not None:
        description = str(post.get("description") or "").strip()
        if description:
            return _shorten(description)
        text = post.content

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "---":
            continue
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        return _shorten(line)
    return ""


def _read_text_preview(path: Path, *, max_chars: int) -> tuple[str, bool, str]:
    try:
        size = path.stat().st_size
        max_bytes = max(4096, max_chars * 4 + 4)
        with path.open("rb") as f:
            raw = f.read(max_bytes)
    except OSError as exc:
        return "", False, str(exc)
    if b"\x00" in raw[: min(len(raw), 4096)]:
        return "", False, f"{path} does not look like a text file."
    text = ""
    for end in range(len(raw), max(-1, len(raw) - 5), -1):
        try:
            text = raw[:end].decode("utf-8")
            break
        except UnicodeDecodeError:
            continue
    if not text and raw:
        return "", False, f"{path} is not valid UTF-8 text."
    truncated = size > len(raw) or len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    return text, truncated, ""


def _preview_path(candidate: DiscoveredResource) -> Path:
    content_path = candidate.content_path or candidate.path
    if candidate.kind == "skill" and content_path.is_dir():
        return content_path / "SKILL.md"
    if content_path.is_file():
        return content_path
    files = [
        p
        for p in content_path.iterdir()
        if p.is_file() and (p.name.lower() in RULE_FILE_NAMES or p.suffix.lower() in RULE_FILE_SUFFIXES)
    ]
    if not files:
        raise ValueError(f"No previewable file found in {candidate.path}.")
    return sorted(files, key=lambda p: p.name.lower())[0]


def _find_candidate(
    resource_id: str,
    *,
    scope: DiscoveryScope,
    root_path: Path | str | None,
) -> DiscoveredResource:
    for candidate in discover_resources(scope=scope, root_path=root_path):
        if candidate.id == resource_id:
            return candidate
    raise ValueError(f"Discovered resource id is no longer available: {resource_id}")


def _add_candidate(
    out: list[DiscoveredResource],
    candidate: DiscoveredResource,
    seen: set[tuple[str, str]],
) -> None:
    key = (candidate.kind, str(candidate.path).lower())
    if key in seen:
        return
    seen.add(key)
    out.append(candidate)


def _mark_conflicts(
    candidates: list[DiscoveredResource],
    registry_keys: set[tuple[ItemKind, str]],
) -> None:
    counts: dict[tuple[ItemKind, str], int] = {}
    for candidate in candidates:
        key = (candidate.kind, candidate.name_hint)
        counts[key] = counts.get(key, 0) + 1

    for candidate in candidates:
        key = (candidate.kind, candidate.name_hint)
        if key in registry_keys:
            candidate.exists_in_registry = True
            candidate.warnings.append("Kind and name already exist in registry.")
        if counts.get(key, 0) > 1:
            candidate.warnings.append(
                "Another discovered resource has the same inferred kind and name."
            )
        if candidate.blockers:
            candidate.status = "blocked"
        elif candidate.warnings:
            candidate.status = (
                "conflict"
                if key in registry_keys or counts[key] > 1
                else "warning"
            )


def _candidate_id(*, tool: str, source: str, kind: ItemKind, path: Path) -> str:
    key = f"{source}\0{tool}\0{kind}\0{str(path).lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _is_excluded_dir(path: Path, *, root: Path) -> bool:
    if path == root:
        return False
    name = path.name.lower()
    if name in EXCLUDED_DIR_NAMES:
        return True
    if name.startswith(".") and name not in ALLOWED_HIDDEN_DIR_NAMES:
        return True
    return False


def _has_parent_named(path: Path, names: set[str]) -> bool:
    return any(parent.name.lower() in names for parent in path.parents)


def _infer_tool(path: Path, *, default: str) -> str:
    lowered = {part.lower() for part in path.parts}
    if ".codex" in lowered:
        return "codex"
    if ".claude" in lowered:
        return "claude-code"
    if ".cursor" in lowered:
        return "cursor"
    if ".windsurf" in lowered:
        return "windsurf"
    if "opencode" in lowered:
        return "opencode"
    if ".gemini" in lowered:
        return "gemini"
    return default


def _candidate_from_manifest(
    path: Path,
    *,
    content_path: Path,
    probe: LocalPathProbe,
    tool: str,
    source: str,
) -> DiscoveredResource | None:
    for manifest_name in MANIFEST_FILENAMES:
        manifest_path = content_path / manifest_name
        manifest_probe = probe_local_path(manifest_path)
        if manifest_probe.health == "missing":
            continue
        if not manifest_probe.ready or manifest_probe.path_kind != "regular":
            return _resource(
                path=path,
                content_path=content_path,
                probe=probe,
                marker=content_path,
                tool=tool,
                source=source,
                kind="plugin",
                name_hint=_slug(path.name),
                description="",
                warnings=[],
            )
    try:
        manifest = load_resource_manifest(content_path)
    except ValueError as exc:
        return _resource(
            path=path,
            content_path=content_path,
            probe=probe,
            marker=content_path,
            tool=tool,
            source=source,
            kind="plugin",
            name_hint=_slug(path.name),
            description="",
            warnings=[str(exc)],
        )
    if manifest.path is None:
        return None
    kind = _kind_from_manifest_buckets(manifest.buckets)
    if kind is None:
        return None
    return _resource(
        path=path,
        content_path=content_path,
        probe=probe,
        marker=manifest.path,
        tool=tool,
        source=source,
        kind=kind,
        name_hint=_slug(path.name),
        description="",
        warnings=[],
    )


def _blocked_resource_from_probe(
    path: Path,
    probe: LocalPathProbe,
    *,
    tool: str,
    source: str,
    kind_hint: ItemKind | None,
) -> DiscoveredResource | None:
    kind = kind_hint or _kind_from_parent(path)
    if kind is None:
        return None
    logical_path = path.expanduser().absolute()
    problem = probe.problem or f"The local resource path cannot be read safely: {logical_path}"
    return DiscoveredResource(
        id=_candidate_id(tool=tool, source=source, kind=kind, path=logical_path),
        tool=tool,
        source=source,
        kind=kind,
        name_hint=_slug(logical_path.stem or logical_path.name),
        path=logical_path,
        content_path=None,
        path_kind=probe.path_kind,
        link_health=probe.health,
        link_target=probe.raw_target,
        reparse_tag=probe.reparse_tag_hex,
        link_target_trusted=False,
        status="blocked",
        blockers=[problem],
    )


def _kind_from_parent(path: Path) -> ItemKind | None:
    parent = path.parent.name.lower()
    if parent == "skills":
        return "skill"
    if parent == "rules":
        return "rule"
    if parent in PROMPT_DIR_NAMES:
        return "prompt"
    if parent == "plugins":
        return "plugin"
    return None


def _kind_from_manifest_buckets(buckets: dict[str, list[str]]) -> ItemKind | None:
    if buckets.get("skills"):
        return "skill"
    if buckets.get("mcp"):
        return "mcp"
    if buckets.get("rules"):
        return "rule"
    if buckets.get("prompts") or buckets.get("commands"):
        return "prompt"
    if buckets.get("plugins") or buckets.get("agents") or buckets.get("hooks"):
        return "plugin"
    return None


def _shorten(text: str, limit: int = 180) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."
