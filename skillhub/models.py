"""Pydantic models for the registry and skill metadata."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class SkillEntry(BaseModel):
    """A single skill recorded in registry.yaml."""

    name: str = Field(..., description="Lower-case, hyphenated unique identifier (<=64 chars).")
    repo: str = Field(..., description="HTTPS GitHub URL of the skill repository.")
    source: Literal["owned", "external"] = Field(
        default="owned",
        description="`owned` = published by us via `skillhub publish`; `external` = third-party.",
    )
    subdir: str = Field(
        default="",
        description="Path inside the repo where SKILL.md lives. Empty = repo root.",
    )
    ref: str = Field(default="main", description="Branch, tag or commit to track.")
    install_dir: str = Field(
        default="",
        description="Optional override for the install directory name (defaults to `name`).",
    )
    description: str = Field(default="", description="Short description, mirrored from SKILL.md.")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not SKILL_NAME_RE.match(v):
            raise ValueError(
                f"Invalid skill name {v!r}: must be 1-64 chars of [a-z0-9-] starting with [a-z0-9]."
            )
        return v

    @field_validator("repo")
    @classmethod
    def _validate_repo(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not (v.startswith("https://github.com/") or v.startswith("git@github.com:")):
            raise ValueError(f"Repo URL must be a GitHub HTTPS or SSH URL, got {v!r}.")
        return v

    @field_validator("subdir")
    @classmethod
    def _validate_subdir(cls, v: str) -> str:
        v = (v or "").strip().strip("/")
        if ".." in v.split("/"):
            raise ValueError("subdir must not contain '..' segments.")
        return v

    def install_target_name(self) -> str:
        return self.install_dir or self.name


class Registry(BaseModel):
    """Top-level registry document."""

    version: int = 1
    skills: list[SkillEntry] = Field(default_factory=list)

    def get(self, name: str) -> SkillEntry | None:
        for s in self.skills:
            if s.name == name:
                return s
        return None

    def upsert(self, entry: SkillEntry) -> None:
        for i, s in enumerate(self.skills):
            if s.name == entry.name:
                self.skills[i] = entry
                return
        self.skills.append(entry)
        self.skills.sort(key=lambda s: s.name)

    def remove(self, name: str) -> SkillEntry | None:
        for i, s in enumerate(self.skills):
            if s.name == name:
                return self.skills.pop(i)
        return None
