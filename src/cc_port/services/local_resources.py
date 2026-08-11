"""Manage content stored directly inside a portable resource repository."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..core.models import ItemKind, RegistryItem
from ..core.registry import find_registry_path, load_registry, save_registry
from ..core.secret_scan import find_secret_text
from ..core.secrets import sanitize_mcp_config_for_storage
from ..core.validator import parse_skill, validate_item
from .install_planner import copy_resource_tree
from .local_path_probe import probe_local_path, resource_tree_issues
from .publisher import _slug

LOCAL_SOURCE = "local"


@dataclass
class ImportLocalResult:
    entry: RegistryItem
    source_path: Path
    stored_path: Path


def repo_root_for_registry(registry_path: Path | None = None) -> Path:
    return (registry_path or find_registry_path()).resolve().parent


def import_local_resource(
    source: Path | str,
    *,
    kind: ItemKind = "skill",
    name: str | None = None,
    description: str | None = None,
    category: str = "",
    tags: list[str] | None = None,
    platforms: list[str] | None = None,
    registry_path: Path | None = None,
    overwrite: bool = False,
    mcp_config: dict[str, Any] | None = None,
) -> ImportLocalResult:
    """Copy a local resource into the resource repository and record it in registry.yaml."""
    logical_source = Path(source).expanduser().absolute()
    _assert_regular_source_chain(logical_source)
    source_probe = probe_local_path(logical_source)
    if source_probe.path_kind != "regular" or not source_probe.ready:
        raise ValueError(
            "Direct resource import requires a regular source path. "
            "Use the asset upload workflow to review a linked source."
        )
    assert source_probe.content_path is not None
    src = source_probe.content_path
    reg_path = registry_path or find_registry_path()
    root = repo_root_for_registry(reg_path)

    item_name = _infer_local_name(src, kind, name)
    item_description = _infer_description(src, kind, description)
    relative_parent = _resource_parent(kind, category)
    relative_path = relative_parent / item_name
    dest = root / relative_path
    _assert_regular_destination_chain(root, dest)

    if src == dest or dest in src.parents or src in dest.parents:
        raise ValueError("Source path cannot be the destination resource path.")
    destination_exists = dest.exists() or dest.is_symlink()
    if destination_exists and not overwrite:
        raise FileExistsError(f"{dest} already exists. Pass --force to overwrite.")

    if kind in {"instruction", "memory"}:
        _reject_unsafe_personal_resource_tree(src)
    validate_item(src, kind, mcp_config=mcp_config)
    if kind in {"instruction", "memory"}:
        _reject_secret_content(src)
    if destination_exists:
        if dest.is_dir() and not dest.is_symlink():
            shutil.rmtree(dest)
        else:
            dest.unlink()

    dest.parent.mkdir(parents=True, exist_ok=True)
    _assert_regular_destination_chain(root, dest)
    if src.is_file() and kind in {"prompt", "rule", "instruction"}:
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest / src.name)
    elif src.is_dir():
        if kind == "memory":
            # Claude memory accepts Markdown topics under directories whose
            # names overlap generic build/cache exclusions. Validation above
            # defines the complete safe tree, so preserve it exactly.
            shutil.copytree(src, dest, symlinks=True)
        else:
            copy_resource_tree(src, dest)
    else:
        shutil.copy2(src, dest)

    if kind in {"instruction", "memory"}:
        try:
            validate_item(dest, kind)
            _reject_secret_content(dest)
        except Exception:
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            elif dest.exists() or dest.is_symlink():
                dest.unlink()
            raise

    effective_mcp_config = mcp_config
    if kind == "mcp" and effective_mcp_config is None:
        effective_mcp_config = _read_mcp_config(dest)
    effective_mcp_config = sanitize_mcp_config_for_storage(effective_mcp_config)

    entry = RegistryItem(
        name=item_name,
        kind=kind,
        source=LOCAL_SOURCE,
        path=relative_path.as_posix(),
        repo="",
        subdir="",
        ref="",
        install_dir="",
        description=item_description,
        mcp_config=effective_mcp_config,
        tags=tags or [],
        category=category,
        platforms=platforms or [],
    )

    registry = load_registry(reg_path)
    registry.upsert(entry)
    save_registry(registry, reg_path)
    return ImportLocalResult(entry=entry, source_path=src, stored_path=dest)


def _reject_secret_content(source: Path) -> None:
    """Fail before copying a personal instruction or memory snapshot."""
    candidates = [source] if source.is_file() else sorted(source.rglob("*.md"))
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError("Personal resource content cannot be read safely.") from exc
        finding = find_secret_text(text)
        if finding is None:
            continue
        relative = Path(candidate.name) if source.is_file() else candidate.relative_to(source)
        raise ValueError(
            f"Secret-like content in {relative.as_posix()}: {finding.reason}. "
            "Replace credentials with environment placeholders before importing."
        )


def _reject_unsafe_personal_resource_tree(source: Path) -> None:
    """Reject nested links/reparse points before a direct personal-resource import."""
    issues = resource_tree_issues(source)
    if not issues:
        return
    preview = ", ".join(
        f"{issue.relative_path} ({issue.code})" for issue in issues[:3]
    )
    remaining = len(issues) - 3
    if remaining > 0:
        preview += f", and {remaining} more"
    raise ValueError(
        "Personal resource tree contains unsafe linked, reparse-point, cyclic, "
        f"or unreadable entries: {preview}."
    )


def _assert_regular_destination_chain(root: Path, destination: Path) -> None:
    """Reject links/reparse points anywhere below the repository root."""
    root = root.absolute()
    destination = destination.absolute()
    try:
        relative = destination.relative_to(root)
    except ValueError as exc:
        raise ValueError("Destination resource path escapes the repository root.") from exc
    current = root
    parts = relative.parts
    for index, part in enumerate(parts):
        current /= part
        probe = probe_local_path(current)
        if probe.health == "missing":
            return
        if probe.path_kind != "regular" or not probe.ready:
            raise ValueError(
                "Destination resource path contains a link or unsupported reparse point."
            )
        if index < len(parts) - 1 and (
            probe.content_path is None or not probe.content_path.is_dir()
        ):
            raise ValueError("Destination resource path has a non-directory ancestor.")


def _assert_regular_source_chain(source: Path) -> None:
    """Reject linked/reparse ancestors that direct import cannot confirm safely."""
    logical = source.expanduser().absolute()
    for ancestor in reversed(logical.parents):
        probe = probe_local_path(ancestor)
        if probe.path_kind != "regular" or not probe.ready:
            raise ValueError(
                "Direct resource import requires regular, non-linked source ancestors. "
                "Use the asset upload workflow to review a linked source."
            )
        if probe.content_path is None or not probe.content_path.is_dir():
            raise ValueError("Direct resource import source has a non-directory ancestor.")


def export_claude_plugin(
    *,
    registry_path: Path | None = None,
    plugin_name: str | None = None,
) -> Path:
    """Generate .claude-plugin/plugin.json from local skills in registry.yaml."""
    reg_path = registry_path or find_registry_path()
    root = repo_root_for_registry(reg_path)
    registry = load_registry(reg_path)

    skills: list[str] = []
    for item in registry.items:
        if (
            item.kind != "skill"
            or item.lifecycle != "active"
            or not item.path
            or not item.supports_platform("claude-code")
        ):
            continue
        skill_path = root / item.path
        if (skill_path / "SKILL.md").is_file():
            skills.append(f"./{item.path}")

    payload = {
        "name": _plugin_slug(plugin_name or root.name),
        "skills": sorted(skills),
    }
    out = root / ".claude-plugin" / "plugin.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def _plugin_slug(value: str) -> str:
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", value)
    words = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "-", words)
    return _slug(words)


def _infer_local_name(src: Path, kind: ItemKind, name: str | None) -> str:
    if name:
        return _slug(name)
    if kind == "skill" and src.is_dir():
        return _slug(parse_skill(src).name)
    return _slug(src.stem if src.is_file() else src.name)


def _infer_description(src: Path, kind: ItemKind, description: str | None) -> str:
    if description is not None:
        return description.strip()
    if kind == "skill" and src.is_dir():
        return parse_skill(src).description
    return ""


def _resource_parent(kind: ItemKind, category: str) -> Path:
    base = {
        "skill": "skills",
        "rule": "rules",
        "mcp": "mcp",
        "prompt": "prompts",
        "plugin": "plugins",
        "instruction": "instructions",
        "memory": "memories",
    }[kind]
    clean_category = _slug(category) if category else ""
    return Path(base) / clean_category if clean_category else Path(base)


def _read_mcp_config(path: Path) -> dict[str, Any] | None:
    config_file = path
    if path.is_dir():
        candidates = [path / "mcp.yaml", path / "mcp.yml", path / "mcp.json"]
        config_file = next((p for p in candidates if p.is_file()), path)
    if not config_file.is_file():
        return None
    text = config_file.read_text(encoding="utf-8")
    if config_file.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{config_file} must contain a mapping.")
    if "mcp_config" in data and isinstance(data["mcp_config"], dict):
        return data["mcp_config"]
    return data
