"""Discover local AI coding resources from common tool directories."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from ..core.models import ItemKind
from ..core.registry import load_registry
from ..core.validator import RULE_FILE_NAMES, RULE_FILE_SUFFIXES, parse_skill
from .publisher import _slug

DiscoveryScope = str

DEFAULT_MAX_DEPTH = 4
PREVIEW_MAX_CHARS = 20_000
TEXT_SAMPLE_BYTES = 64_000

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


@dataclass
class DiscoveredResource:
    id: str
    tool: str
    source: str
    kind: ItemKind
    name_hint: str
    path: Path
    description: str = ""
    size: int = 0
    mtime: float = 0
    status: str = "ready"
    warnings: list[str] = field(default_factory=list)


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
) -> list[DiscoveredResource]:
    """Discover resource candidates without modifying any source directory."""
    roots = _roots_for_scope(scope=scope, root_path=root_path)
    registry_names = {item.name for item in load_registry(registry_path).items}
    candidates: list[DiscoveredResource] = []
    seen: set[tuple[str, str]] = set()

    for tool, root in roots:
        if not root.exists():
            continue
        effective_tool = _infer_tool(root, default=tool)
        candidates.extend(
            _scan_root(
                root.resolve(),
                tool=effective_tool,
                source=scope,
                max_depth=max_depth,
                seen=seen,
            )
        )

    _mark_conflicts(candidates, registry_names)
    return sorted(candidates, key=lambda item: (item.tool, item.kind, item.name_hint, str(item.path)))


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
            ("claude-code", home / ".claude"),
            ("cursor", home / ".cursor"),
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
) -> list[DiscoveredResource]:
    out: list[DiscoveredResource] = []
    stack: list[tuple[Path, int]] = [(root, 0)]

    while stack:
        current, depth = stack.pop()
        if current.is_dir():
            if _is_excluded_dir(current, root=root):
                continue
            candidate = _candidate_from_directory(current, tool=tool, source=source)
            if candidate is not None:
                _add_candidate(out, candidate, seen)
                continue
            if depth >= max_depth:
                continue
            try:
                children = sorted(current.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                continue
            for child in reversed(children):
                if child.is_dir():
                    stack.append((child, depth + 1))
                elif child.is_file():
                    candidate = _candidate_from_file(child, tool=_infer_tool(child, default=tool), source=source)
                    if candidate is not None:
                        _add_candidate(out, candidate, seen)
    return out


def _candidate_from_directory(path: Path, *, tool: str, source: str) -> DiscoveredResource | None:
    skill_md = path / "SKILL.md"
    if not skill_md.is_file():
        return None

    warnings: list[str] = []
    name_hint = _slug(path.name)
    description = ""
    try:
        meta = parse_skill(path)
        name_hint = _slug(meta.name)
        description = meta.description
    except Exception as exc:  # noqa: BLE001 - discovery reports invalid metadata as a warning
        warnings.append(str(exc))

    return _resource(
        path=path,
        marker=skill_md,
        tool=tool,
        source=source,
        kind="skill",
        name_hint=name_hint,
        description=description,
        warnings=warnings,
    )


def _candidate_from_file(path: Path, *, tool: str, source: str) -> DiscoveredResource | None:
    kind = _file_kind(path)
    if kind is None:
        return None
    name_hint = _slug(path.stem if path.stem else path.name.lstrip("."))
    description = _file_description(path)
    return _resource(
        path=path,
        marker=path,
        tool=tool,
        source=source,
        kind=kind,
        name_hint=name_hint,
        description=description,
        warnings=[],
    )


def _resource(
    *,
    path: Path,
    marker: Path,
    tool: str,
    source: str,
    kind: ItemKind,
    name_hint: str,
    description: str,
    warnings: list[str],
) -> DiscoveredResource:
    stat = marker.stat()
    resolved = path.resolve()
    status = "warning" if warnings else "ready"
    return DiscoveredResource(
        id=_candidate_id(tool=tool, source=source, kind=kind, path=resolved),
        tool=tool,
        source=source,
        kind=kind,
        name_hint=name_hint,
        path=resolved,
        description=description,
        size=stat.st_size,
        mtime=stat.st_mtime,
        status=status,
        warnings=warnings,
    )


def _file_kind(path: Path) -> ItemKind | None:
    lower = path.name.lower()
    suffix = path.suffix.lower()
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
    if candidate.kind == "skill" and candidate.path.is_dir():
        return candidate.path / "SKILL.md"
    if candidate.path.is_file():
        return candidate.path
    files = [
        p
        for p in candidate.path.iterdir()
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


def _mark_conflicts(candidates: list[DiscoveredResource], registry_names: set[str]) -> None:
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.name_hint] = counts.get(candidate.name_hint, 0) + 1

    for candidate in candidates:
        if candidate.name_hint in registry_names:
            candidate.warnings.append("Name already exists in registry.")
        if counts.get(candidate.name_hint, 0) > 1:
            candidate.warnings.append("Another discovered resource has the same inferred name.")
        if candidate.warnings:
            candidate.status = "conflict" if candidate.name_hint in registry_names or counts[candidate.name_hint] > 1 else "warning"


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
    return default


def _shorten(text: str, limit: int = 180) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."
