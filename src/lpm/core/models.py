"""Pydantic models for the registry and resource metadata."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ITEM_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Keep old name as alias for backward compatibility
SKILL_NAME_RE = ITEM_NAME_RE

ItemKind = Literal["skill", "mcp", "rule", "prompt", "plugin"]
ItemLifecycle = Literal["active", "removed"]
RemovedEffect = Literal["", "index_only", "local_files_deleted", "remote_repo_deleted"]


class RegistryItem(BaseModel):
    """A single resource (skill, MCP server config, or rule) in registry.yaml."""

    name: str = Field(..., description="Lower-case, hyphenated unique identifier (<=64 chars).")
    kind: ItemKind = Field(
        default="skill",
        description="Resource type: skill | mcp | rule | prompt | plugin.",
    )
    repo: str = Field(default="", description="HTTPS GitHub URL of the repository.")
    source: Literal["owned", "external", "local"] = Field(
        default="owned",
        description="`owned`/`local` = stored in this resource repo; `external` = third-party.",
    )
    path: str = Field(
        default="",
        description="Relative path inside this resource repo for local/owned items.",
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

    # --- Rich metadata (v3) ---
    version: str = Field(default="", description="Semantic version, e.g. '1.2.0'.")
    author: str = Field(default="", description="Author or maintainer name.")
    tags: list[str] = Field(default_factory=list, description="Search tags, e.g. ['python', 'testing'].")
    category: str = Field(default="", description="Category, e.g. 'software-dev', 'productivity'.")
    license: str = Field(default="", description="SPDX license identifier, e.g. 'MIT'.")
    platforms: list[str] = Field(
        default_factory=list,
        description="Optional platform allowlist. Empty means every enabled platform.",
    )
    private: bool | None = Field(
        default=None,
        description="Cached GitHub repo visibility. True=private, False=public.",
    )

    # --- Health-check metadata ---
    last_checked: str | None = Field(
        default=None,
        description="ISO-8601 timestamp of last reachability check.",
    )
    reachable: bool | None = Field(
        default=None,
        description="Whether the repo was reachable at last check.",
    )

    # --- Lifecycle metadata (v5) ---
    lifecycle: ItemLifecycle = Field(
        default="active",
        description="Whether this item is active or kept only as a removed tracking record.",
    )
    removed_at: str | None = Field(default=None, description="ISO-8601 removal timestamp.")
    removed_reason: str = Field(default="", description="Human-readable removal reason.")
    removed_effect: RemovedEffect = Field(
        default="",
        description="What deletion action was applied when the item was removed.",
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
        if not v:
            return ""
        if not (v.startswith("https://github.com/") or v.startswith("git@github.com:")):
            raise ValueError(f"Repo URL must be a GitHub HTTPS or SSH URL, got {v!r}.")
        return v

    @field_validator("path")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        v = (v or "").strip().replace("\\", "/").strip("/")
        if not v:
            return ""
        if v.startswith("/") or ".." in v.split("/"):
            raise ValueError("path must be a relative path without '..' segments.")
        return v

    @field_validator("subdir")
    @classmethod
    def _validate_subdir(cls, v: str) -> str:
        v = (v or "").strip().strip("/")
        if ".." in v.split("/"):
            raise ValueError("subdir must not contain '..' segments.")
        return v

    @field_validator("platforms")
    @classmethod
    def _validate_platforms(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            name = str(value).strip()
            if not name:
                raise ValueError("platforms must not contain empty names.")
            if name not in seen:
                normalized.append(name)
                seen.add(name)
        return normalized

    @model_validator(mode="after")
    def _validate_mcp_config(self) -> RegistryItem:
        if self.source == "external" and not self.repo:
            raise ValueError("external items require a repo URL.")
        if self.source in {"local", "owned"} and not self.repo and not self.path:
            raise ValueError("local/owned items require either path or repo.")
        if self.kind == "mcp" and self.mcp_config is not None:
            has_command = bool(self.mcp_config.get("command"))
            has_url = bool(self.mcp_config.get("url"))
            if not has_command and not has_url:
                raise ValueError("mcp_config must contain either 'command' or 'url'.")
        return self

    def install_target_name(self) -> str:
        return self.install_dir or self.name

    def supports_platform(self, platform_name: str) -> bool:
        """Return whether this resource may be installed on *platform_name*."""
        return not self.platforms or platform_name in self.platforms


# Backward-compatible alias
SkillEntry = RegistryItem


class Registry(BaseModel):
    """Top-level registry document (supports v1 through v5 formats)."""

    version: int = 5
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
