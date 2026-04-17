"""Validate a local skill directory and parse its SKILL.md frontmatter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import frontmatter

from .models import SKILL_NAME_RE


class SkillValidationError(ValueError):
    """Raised when a local skill directory does not meet SKILL.md requirements."""


@dataclass
class SkillMetadata:
    name: str
    description: str
    skill_md_path: Path


def find_skill_md(skill_dir: Path) -> Path:
    """Locate SKILL.md inside `skill_dir`.

    Accepts either `<skill_dir>/SKILL.md` or a directory containing exactly one
    nested SKILL.md (one level deep) for convenience.
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
    if not SKILL_NAME_RE.match(name):
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
