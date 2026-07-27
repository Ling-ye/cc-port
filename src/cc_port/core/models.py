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
PluginTrack = Literal["content", "reference"]
PluginPlatform = Literal["codex", "claude-code", "opencode"]
PluginOriginType = Literal["marketplace", "npm", "git", "local"]
PluginScope = Literal["user", "project", "local", "managed"]


class PluginProjectIdentity(BaseModel):
    """Portable identity for a project-scoped plugin installation."""

    repo: str
    subdir: str = ""

    @field_validator("repo")
    @classmethod
    def _validate_repo(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError("plugin project repo is required.")
        if "://" in normalized and "@" in normalized.partition("://")[2].partition("/")[0]:
            raise ValueError("plugin project repo must not contain credentials.")
        return normalized

    @field_validator("subdir")
    @classmethod
    def _validate_subdir(cls, value: str) -> str:
        normalized = (value or "").strip().replace("\\", "/").strip("/")
        if ".." in normalized.split("/"):
            raise ValueError("plugin project subdir must not contain '..'.")
        return normalized


class PluginOrigin(BaseModel):
    """Installable source for one plugin distribution."""

    type: PluginOriginType
    marketplace: str = ""
    source: str = ""
    package: str = ""
    repo: str = ""
    selector: str = ""

    @model_validator(mode="after")
    def _validate_locator(self) -> PluginOrigin:
        if self.type == "marketplace" and not self.marketplace.strip():
            raise ValueError("marketplace plugin origins require marketplace.")
        if self.type == "npm" and not self.package.strip():
            raise ValueError("npm plugin origins require package.")
        if self.type == "git" and not self.repo.strip():
            raise ValueError("git plugin origins require repo.")
        if self.type != "marketplace":
            self.marketplace = ""
        if self.type != "npm":
            self.package = ""
        if self.type != "git":
            self.repo = ""
        if self.type not in {"marketplace", "local"}:
            self.source = ""
        return self


class PluginInstallation(BaseModel):
    """Desired installation state for one plugin scope."""

    scope: PluginScope = "user"
    enabled: bool = True
    project: PluginProjectIdentity | None = None

    @model_validator(mode="after")
    def _validate_project_scope(self) -> PluginInstallation:
        if self.scope in {"project", "local"} and self.project is None:
            raise ValueError(f"plugin scope {self.scope!r} requires a project identity.")
        if self.scope in {"user", "managed"} and self.project is not None:
            raise ValueError(f"plugin scope {self.scope!r} must not include a project identity.")
        return self


class PluginSpec(BaseModel):
    """Registry v7 dual-track plugin contract."""

    track: PluginTrack
    platform: PluginPlatform
    plugin_id: str
    origin: PluginOrigin
    observed_version: str = ""
    installations: list[PluginInstallation] = Field(default_factory=list)
    dependencies: dict[str, str] = Field(default_factory=dict)

    @field_validator("plugin_id")
    @classmethod
    def _validate_plugin_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("plugin_id is required.")
        return normalized

    @field_validator("dependencies")
    @classmethod
    def _normalize_dependencies(cls, values: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for name, selector in values.items():
            package = str(name).strip()
            version = str(selector).strip()
            if not package or not version:
                raise ValueError("plugin dependencies require non-empty package and selector.")
            normalized[package] = version
        return normalized

    @model_validator(mode="after")
    def _validate_track(self) -> PluginSpec:
        if self.track == "reference" and self.origin.type == "local":
            raise ValueError("reference plugins require a portable marketplace, npm, or git origin.")
        if self.track == "content" and self.origin.type not in {"local", "git"}:
            raise ValueError("content plugins require a local or git source origin.")
        if self.dependencies and not (
            self.track == "content" and self.platform == "opencode"
        ):
            raise ValueError("plugin dependencies are only supported for OpenCode content plugins.")
        identities: set[tuple[str, str, str]] = set()
        for installation in self.installations:
            project = installation.project
            identity = (
                installation.scope,
                project.repo if project else "",
                project.subdir if project else "",
            )
            if identity in identities:
                raise ValueError("plugin installations must be unique by scope and project.")
            identities.add(identity)
        return self


class ResourceKey(BaseModel):
    """Stable composite identity for a registry resource."""

    kind: ItemKind
    name: str

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not ITEM_NAME_RE.match(v):
            raise ValueError(
                f"Invalid name {v!r}: must be 1-64 chars of [a-z0-9-] starting with [a-z0-9]."
            )
        return v

    @property
    def value(self) -> str:
        return f"{self.kind}:{self.name}"

    @classmethod
    def parse(cls, value: str) -> ResourceKey:
        kind, separator, name = value.partition(":")
        if not separator:
            raise ValueError("Resource key must use the '<kind>:<name>' format.")
        return cls(kind=kind, name=name)  # type: ignore[arg-type]

    def __str__(self) -> str:
        return self.value


class AmbiguousResourceNameError(ValueError):
    """Raised when a legacy name-only lookup matches multiple resource kinds."""

    def __init__(self, name: str, matches: list[ResourceKey]):
        self.name = name
        self.matches = matches
        values = ", ".join(str(item) for item in matches)
        super().__init__(
            f"Resource name {name!r} is ambiguous ({values}). Pass the resource kind."
        )


class RegistryItem(BaseModel):
    """A skill, MCP config, rule, prompt, or plugin in registry.yaml."""

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
    platform_install_dirs: dict[str, str] = Field(
        default_factory=dict,
        description="Optional per-platform install directory overrides.",
    )
    description: str = Field(default="", description="Short description.")
    mcp_config: dict[str, Any] | None = Field(
        default=None,
        description="MCP server configuration (command/args/env). Only used when kind=mcp.",
    )
    plugin: PluginSpec | None = Field(
        default=None,
        description="Registry v7 dual-track plugin metadata. Omitted for legacy plugin entries.",
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

    @field_validator("install_dir")
    @classmethod
    def _validate_install_dir(cls, v: str) -> str:
        value = (v or "").strip()
        if value and not ITEM_NAME_RE.match(value):
            raise ValueError(
                "install_dir must be a safe single path segment using [a-z0-9-]."
            )
        return value

    @field_validator("platform_install_dirs")
    @classmethod
    def _validate_platform_install_dirs(cls, values: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for platform, install_name in values.items():
            platform_name = str(platform).strip()
            target_name = str(install_name).strip()
            if not platform_name:
                raise ValueError("platform_install_dirs must not contain an empty platform.")
            if not ITEM_NAME_RE.match(target_name):
                raise ValueError(
                    "platform_install_dirs values must be safe single path segments "
                    "using [a-z0-9-]."
                )
            normalized[platform_name] = target_name
        return normalized

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
        is_plugin_reference = bool(
            self.kind == "plugin" and self.plugin and self.plugin.track == "reference"
        )
        if self.source == "external" and not self.repo and not is_plugin_reference:
            raise ValueError("external items require a repo URL.")
        if self.source in {"local", "owned"} and not self.repo and not self.path:
            raise ValueError("local/owned items require either path or repo.")
        if self.kind == "mcp" and self.mcp_config is not None:
            has_command = bool(self.mcp_config.get("command"))
            has_url = bool(self.mcp_config.get("url"))
            if not has_command and not has_url:
                raise ValueError("mcp_config must contain either 'command' or 'url'.")
        if self.plugin is not None and self.kind != "plugin":
            raise ValueError("plugin metadata is only valid when kind='plugin'.")
        if self.plugin is not None:
            if self.plugin.track == "reference" and self.source != "external":
                raise ValueError("reference plugins must use source='external'.")
            if self.plugin.track == "content" and self.source == "external":
                raise ValueError("content plugins must use source='local' or source='owned'.")
        return self

    @property
    def resource_key(self) -> str:
        return f"{self.kind}:{self.name}"

    def key(self) -> ResourceKey:
        return ResourceKey(kind=self.kind, name=self.name)

    def install_target_name(self, platform_name: str | None = None) -> str:
        if platform_name:
            platform_name = platform_name.strip()
            if platform_name and platform_name in self.platform_install_dirs:
                return self.platform_install_dirs[platform_name]
        return self.install_dir or self.name

    def supports_platform(self, platform_name: str) -> bool:
        """Return whether this resource may be installed on *platform_name*."""
        return not self.platforms or platform_name in self.platforms


# Backward-compatible alias
SkillEntry = RegistryItem


class Registry(BaseModel):
    """Top-level registry document (supports v1 through v7 formats)."""

    version: int = 7
    items: list[RegistryItem] = Field(default_factory=list)

    def __init__(self, **data: Any) -> None:
        # Accept ``skills=`` kwarg as alias for ``items=`` for backward compat.
        if "skills" in data and "items" not in data:
            data["items"] = data.pop("skills")
        super().__init__(**data)

    @model_validator(mode="after")
    def _validate_plugin_distribution_identity(self) -> Registry:
        identities: dict[tuple[str, str, str, str], str] = {}
        for item in self.items:
            spec = item.plugin
            if item.kind != "plugin" or spec is None or item.lifecycle != "active":
                continue
            source_id = (
                spec.origin.marketplace
                if spec.origin.type == "marketplace"
                else spec.origin.package
                if spec.origin.type == "npm"
                else spec.origin.repo
                if spec.origin.type == "git"
                else spec.origin.source
            )
            identity = (spec.platform, spec.plugin_id, spec.origin.type, source_id)
            existing = identities.get(identity)
            if existing is not None and existing != item.resource_key:
                raise ValueError(
                    "active v7 plugins with the same platform and source must share one resource key."
                )
            identities[identity] = item.resource_key
        return self

    @property
    def skills(self) -> list[RegistryItem]:
        """Backward-compatible accessor: returns all items (regardless of kind)."""
        return self.items

    def get(self, name: str, kind: ItemKind | None = None) -> RegistryItem | None:
        matches = [
            item
            for item in self.items
            if item.name == name and (kind is None or item.kind == kind)
        ]
        if not matches:
            return None
        if kind is None and len(matches) > 1:
            raise AmbiguousResourceNameError(name, [item.key() for item in matches])
        return matches[0]

    def get_key(self, key: ResourceKey | str) -> RegistryItem | None:
        parsed = ResourceKey.parse(key) if isinstance(key, str) else key
        return self.get(parsed.name, parsed.kind)

    def upsert(self, entry: RegistryItem) -> None:
        for i, item in enumerate(self.items):
            if item.name == entry.name and item.kind == entry.kind:
                self.items[i] = entry
                return
        self.items.append(entry)
        self.items.sort(key=lambda item: (item.kind, item.name))

    def remove(self, name: str, kind: ItemKind | None = None) -> RegistryItem | None:
        entry = self.get(name, kind)
        if entry is None:
            return None
        for i, item in enumerate(self.items):
            if item.name == entry.name and item.kind == entry.kind:
                return self.items.pop(i)
        return None

    def filter_by_kind(self, kind: ItemKind) -> list[RegistryItem]:
        return [item for item in self.items if item.kind == kind]
