"""Manage resources stored directly inside the private LPM resource repository."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import ItemKind, RegistryItem
from .publisher import _slug
from .registry import find_registry_path, load_registry, save_registry
from .secrets import sanitize_mcp_config_for_storage
from .validator import parse_skill, validate_item

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
    registry_path: Path | None = None,
    overwrite: bool = False,
    mcp_config: dict[str, Any] | None = None,
) -> ImportLocalResult:
    """Copy a local resource into the resource repository and record it in registry.yaml."""
    src = Path(source).expanduser().resolve()
    reg_path = registry_path or find_registry_path()
    root = repo_root_for_registry(reg_path)

    item_name = _infer_local_name(src, kind, name)
    item_description = _infer_description(src, kind, description)
    relative_parent = _resource_parent(kind, category)
    relative_path = relative_parent / item_name
    dest = root / relative_path

    if src == dest or dest in src.parents:
        raise ValueError("Source path cannot be the destination resource path.")
    if dest.exists():
        if not overwrite:
            raise FileExistsError(f"{dest} already exists. Pass --force to overwrite.")
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()

    validate_item(src, kind, mcp_config=mcp_config)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    else:
        shutil.copy2(src, dest)

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
    )

    registry = load_registry(reg_path)
    registry.upsert(entry)
    save_registry(registry, reg_path)
    return ImportLocalResult(entry=entry, source_path=src, stored_path=dest)


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
        if item.kind != "skill" or not item.path:
            continue
        skill_path = root / item.path
        if (skill_path / "SKILL.md").is_file():
            skills.append(f"./{item.path}")

    payload = {
        "name": plugin_name or root.name,
        "skills": sorted(skills),
    }
    out = root / ".claude-plugin" / "plugin.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


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
