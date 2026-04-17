"""Pydantic models for the registry and resource metadata."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ITEM_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Keep old name as alias for backward compatibility
SKILL_NAME_RE = ITEM_NAME_RE

ItemKind = Literal["skill", "mcp", "rule"]


class RegistryItem(BaseModel):
    """A single resource (skill, MCP server config, or rule) in registry.yaml."""

    name: str = Field(..., description="Lower-case, hyphenated unique identifier (<=64 chars).")
    kind: ItemKind = Field(default="skill", description="Resource type: skill | mcp | rule.")
    repo: str = Field(..., description="HTTPS GitHub URL of the repository.")
    source: Literal["owned", "external"] = Field(
        default="owned",
        description="`owned` = published by us; `external` = third-party.",
    )
    subdir: str = Field(
        default="",
        description="Path inside the repo where the resource lives. Empty = repo root.",
    )
    ref: str = Field(default="main", description="Branch, tag or commit to track.")
    install_dir: str = Field(
        default="",
        description="Optional override for the install directory name (defaults to `name`).",
    )
    description: str = Field(default="", description="Short description.")
    mcp_config: dict[str, Any] | None = Field(
        default=None,
        description="MCP server configuration (command/args/env). Only used when kind=mcp.",
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not ITEM_NAME_RE.match(v):
            raise ValueError(
                f"Invalid name {v!r}: must be 1-64 chars of [a-z0-9-] starting with [a-z0-9]."
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

    @model_validator(mode="after")
    def _validate_mcp_config(self) -> RegistryItem:
        if self.kind == "mcp" and self.mcp_config is not None:
            has_command = bool(self.mcp_config.get("command"))
            has_url = bool(self.mcp_config.get("url"))
            if not has_command and not has_url:
                raise ValueError("mcp_config must contain either 'command' or 'url'.")
        return self

    def install_target_name(self) -> str:
        return self.install_dir or self.name


# Backward-compatible alias
SkillEntry = RegistryItem


class Registry(BaseModel):
    """Top-level registry document (supports v1 and v2 formats)."""

    version: int = 2
    items: list[RegistryItem] = Field(default_factory=list)

    def __init__(self, **data: Any) -> None:
        # Accept ``skills=`` kwarg as alias for ``items=`` for backward compat.
        if "skills" in data and "items" not in data:
            data["items"] = data.pop("skills")
        super().__init__(**data)

    @property
    def skills(self) -> list[RegistryItem]:
        """Backward-compatible accessor: returns all items (regardless of kind)."""
        return self.items

    def get(self, name: str) -> RegistryItem | None:
        for item in self.items:
            if item.name == name:
                return item
        return None

    def upsert(self, entry: RegistryItem) -> None:
        for i, item in enumerate(self.items):
            if item.name == entry.name:
                self.items[i] = entry
                return
        self.items.append(entry)
        self.items.sort(key=lambda s: s.name)

    def remove(self, name: str) -> RegistryItem | None:
        for i, item in enumerate(self.items):
            if item.name == name:
                return self.items.pop(i)
        return None

    def filter_by_kind(self, kind: ItemKind) -> list[RegistryItem]:
        return [item for item in self.items if item.kind == kind]
