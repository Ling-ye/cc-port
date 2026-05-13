"""Validate a local skill directory and parse its SKILL.md frontmatter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frontmatter
import yaml

from .models import ITEM_NAME_RE, ItemKind

# Keep old name for backward compat
SKILL_NAME_RE = ITEM_NAME_RE


class SkillValidationError(ValueError):
    """Raised when a local skill directory does not meet requirements."""


@dataclass
class SkillMetadata:
    name: str
    description: str
    skill_md_path: Path


def find_skill_md(skill_dir: Path) -> Path:
    """Locate SKILL.md inside ``skill_dir``.

    Accepts either ``<skill_dir>/SKILL.md`` or a directory containing exactly
    one nested SKILL.md (one level deep) for convenience.
    """
    direct = skill_dir / "SKILL.md"
    if direct.is_file():
        return direct
    nested = list(skill_dir.glob("*/SKILL.md"))
    if len(nested) == 1:
        return nested[0]
    raise SkillValidationError(
        f"Could not find SKILL.md in {skill_dir}. Expected SKILL.md at the root."
    )


def parse_skill(skill_dir: Path) -> SkillMetadata:
    """Validate frontmatter and return parsed metadata."""
    skill_dir = skill_dir.expanduser().resolve()
    if not skill_dir.is_dir():
        raise SkillValidationError(f"{skill_dir} is not a directory.")

    skill_md = find_skill_md(skill_dir)
    post = frontmatter.load(skill_md)

    name = (post.get("name") or "").strip()
    description = (post.get("description") or "").strip()

    if not name:
        raise SkillValidationError(f"{skill_md}: frontmatter is missing required field `name`.")
    if not ITEM_NAME_RE.match(name):
        raise SkillValidationError(
            f"{skill_md}: name {name!r} must be lowercase letters/digits/hyphens, max 64 chars."
        )
    if not description:
        raise SkillValidationError(
            f"{skill_md}: frontmatter is missing required field `description`."
        )
    if len(description) > 1024:
        raise SkillValidationError(
            f"{skill_md}: description must be <= 1024 chars (got {len(description)})."
        )

    return SkillMetadata(name=name, description=description, skill_md_path=skill_md)


def validate_mcp_config(mcp_config: dict[str, Any] | None) -> None:
    """Validate an MCP server configuration dict."""
    if mcp_config is None:
        raise SkillValidationError("MCP items require an mcp_config dict.")
    if not mcp_config.get("command") and not mcp_config.get("url"):
        raise SkillValidationError(
            "mcp_config must contain either 'command' (stdio) or 'url' (http)."
        )


def validate_mcp_path(path: Path) -> None:
    """Validate an MCP config file or directory containing mcp.yaml/json."""
    path = path.expanduser().resolve()
    config_file = path
    if path.is_dir():
        candidates = [path / "mcp.yaml", path / "mcp.yml", path / "mcp.json"]
        config_file = next((p for p in candidates if p.is_file()), path)
    if not config_file.is_file():
        raise SkillValidationError(
            f"{path} must be an MCP config file or contain mcp.yaml/mcp.yml/mcp.json."
        )
    try:
        text = config_file.read_text(encoding="utf-8")
        data = json.loads(text) if config_file.suffix.lower() == ".json" else yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 - surface parser errors as validation errors
        raise SkillValidationError(f"{config_file}: invalid MCP config: {exc}") from exc
    if isinstance(data, dict) and isinstance(data.get("mcp_config"), dict):
        data = data["mcp_config"]
    if not isinstance(data, dict):
        raise SkillValidationError(f"{config_file}: MCP config must be a mapping.")
    validate_mcp_config(data)


def validate_rule_dir(rule_dir: Path) -> None:
    """Validate that a rule directory contains at least one .md file."""
    rule_dir = rule_dir.expanduser().resolve()
    if not rule_dir.is_dir():
        raise SkillValidationError(f"{rule_dir} is not a directory.")
    md_files = list(rule_dir.glob("*.md"))
    if not md_files:
        raise SkillValidationError(f"No .md files found in {rule_dir}.")


def validate_prompt_path(prompt_path: Path) -> None:
    """Validate that a prompt resource is a markdown file or directory."""
    prompt_path = prompt_path.expanduser().resolve()
    if prompt_path.is_file():
        if prompt_path.suffix.lower() != ".md":
            raise SkillValidationError(f"{prompt_path} must be a .md prompt file.")
        return
    validate_rule_dir(prompt_path)


def validate_item(path: Path, kind: ItemKind, mcp_config: dict[str, Any] | None = None) -> None:
    """Dispatch validation based on item kind."""
    if kind == "skill":
        parse_skill(path)
    elif kind == "mcp":
        if mcp_config is not None:
            validate_mcp_config(mcp_config)
        else:
            validate_mcp_path(path)
    elif kind == "rule":
        validate_rule_dir(path)
    elif kind == "prompt":
        validate_prompt_path(path)
    elif kind == "plugin":
        if not path.expanduser().resolve().is_dir():
            raise SkillValidationError(f"{path} is not a directory.")
