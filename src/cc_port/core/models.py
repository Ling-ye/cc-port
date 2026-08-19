"""Pydantic models for the registry and resource metadata."""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

ITEM_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
RESOURCE_KIND_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SAFE_INSTALL_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")

# Keep old name as alias for backward compatibility
SKILL_NAME_RE = ITEM_NAME_RE

ItemKind = Literal[
    "skill",
    "mcp",
    "rule",
    "prompt",
    "plugin",
    "instruction",
    "memory",
]

MEMORY_SOURCE_TOOL_IDS = frozenset({"claude-code", "codex"})

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
    """Resolved CC Port plugin behavior; never persisted in registry.yaml."""

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


class ExternalSource(BaseModel):
    """Portable source reference stored in registry v1."""

    model_config = ConfigDict(extra="forbid")

    type: str
    locator: str
    revision: str = ""
    subpath: str = ""

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not RESOURCE_KIND_RE.fullmatch(normalized):
            raise ValueError(
                "source type must use 1-64 lower-case letters, digits, '.', '_' or '-'."
            )
        return normalized

    @field_validator("locator")
    @classmethod
    def _validate_locator(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("source locator is required.")
        parsed = urlparse(normalized)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("source locator must not contain credentials.")
        return normalized.rstrip("/")

    @field_validator("revision")
    @classmethod
    def _normalize_revision(cls, value: str) -> str:
        return (value or "").strip()

    @field_validator("subpath")
    @classmethod
    def _validate_subpath(cls, value: str) -> str:
        raw = (value or "").strip()
        if (
            "\\" in raw
            or raw.startswith("/")
            or raw.endswith("/")
            or re.match(r"^[a-zA-Z]:", raw)
        ):
            raise ValueError("source subpath must use POSIX relative path syntax.")
        normalized = raw
        if normalized and any(part in {"", ".", ".."} for part in normalized.split("/")):
            raise ValueError("source subpath must be a safe relative POSIX path.")
        return normalized


class RegistryResource(BaseModel):
    """One tool-neutral resource declaration in registry v1."""

    model_config = ConfigDict(extra="allow")

    kind: str
    name: str
    path: str = ""
    source: ExternalSource | None = None

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not RESOURCE_KIND_RE.fullmatch(normalized):
            raise ValueError(
                "resource kind must use 1-64 lower-case letters, digits, '.', '_' or '-'."
            )
        return normalized

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not ITEM_NAME_RE.fullmatch(normalized):
            raise ValueError(
                f"Invalid name {value!r}: must be 1-64 chars of [a-z0-9-] "
                "starting with [a-z0-9]."
            )
        return normalized

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return ""
        if "\\" in raw or raw.startswith("/") or raw.endswith("/"):
            raise ValueError("path must be a repository-relative POSIX path.")
        if re.match(r"^[a-zA-Z]:", raw) or any(ord(char) < 32 for char in raw):
            raise ValueError("path must be a portable repository-relative path.")
        parts = raw.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("path must not contain empty, '.' or '..' segments.")
        return raw

    @model_validator(mode="after")
    def _validate_location(self) -> RegistryResource:
        if bool(self.path) == bool(self.source):
            raise ValueError("resource must contain exactly one of path or source.")
        if self.kind in {
            "skill",
            "mcp",
            "rule",
            "prompt",
            "plugin",
            "instruction",
            "memory",
        } and self.model_extra:
            raise ValueError(
                f"known resource kind {self.kind!r} contains unsupported fields: "
                + ", ".join(sorted(self.model_extra))
            )
        return self

    @property
    def resource_key(self) -> str:
        return f"{self.kind}:{self.name}"


class ResourceKey(BaseModel):
    """Stable composite identity for a registry resource."""

    kind: str
    name: str

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not ITEM_NAME_RE.match(v):
            raise ValueError(
                f"Invalid name {v!r}: must be 1-64 chars of [a-z0-9-] starting with [a-z0-9]."
            )
        return v

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not RESOURCE_KIND_RE.fullmatch(normalized):
            raise ValueError("Invalid resource kind.")
        return normalized

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


class ResolvedResource(BaseModel):
    """Runtime resource resolved from registry, content, and cc-port.yaml."""

    name: str = Field(..., description="Lower-case, hyphenated unique identifier (<=64 chars).")
    kind: str = Field(
        default="skill",
        description=(
            "Resource type: skill | mcp | rule | prompt | plugin | instruction | memory."
        ),
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
        description="CC Port plugin behavior resolved from the optional overlay.",
    )
    external_source: ExternalSource | None = Field(
        default=None,
        exclude=True,
        description="Portable registry v1 source used by the resolved runtime view.",
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

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not RESOURCE_KIND_RE.fullmatch(normalized):
            raise ValueError("Invalid resource kind.")
        return normalized

    @field_validator("repo")
    @classmethod
    def _validate_repo(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v:
            return ""
        parsed = urlparse(v)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Repo URL must not contain credentials.")
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
            if not RESOURCE_KIND_RE.fullmatch(platform_name):
                raise ValueError(
                    "platform_install_dirs keys must be portable tool ids using "
                    "lowercase letters, digits, '.', '_' or '-'."
                )
            if (
                target_name in {".", ".."}
                or not SAFE_INSTALL_SEGMENT_RE.fullmatch(target_name)
            ):
                raise ValueError(
                    "platform_install_dirs values must be safe single path segments "
                    "using letters, digits, '.', '_' or '-'."
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
            if not RESOURCE_KIND_RE.fullmatch(name):
                raise ValueError(
                    "platforms must contain only portable tool ids using lowercase "
                    "letters, digits, '.', '_' or '-'."
                )
            if name not in seen:
                normalized.append(name)
                seen.add(name)
        return normalized

    @model_validator(mode="after")
    def _validate_mcp_config(self) -> ResolvedResource:
        is_plugin_reference = bool(
            self.kind == "plugin" and self.plugin and self.plugin.track == "reference"
        )
        if (
            self.source == "external"
            and not self.repo
            and self.external_source is None
            and not is_plugin_reference
        ):
            raise ValueError("external items require a portable source reference.")
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


# Internal compatibility aliases while consumers migrate to ResolvedResource.
RegistryItem = ResolvedResource
SkillEntry = ResolvedResource


class CcPortPluginSettings(BaseModel):
    """CC Port-only plugin installation intent stored outside registry.yaml."""

    model_config = ConfigDict(extra="forbid")

    platform: PluginPlatform
    plugin_id: str
    marketplace: str = ""
    installations: list[PluginInstallation] = Field(default_factory=list)
    dependencies: dict[str, str] = Field(default_factory=dict)


class CcPortResourceSettings(BaseModel):
    """Optional CC Port behavior for a portable registry resource."""

    model_config = ConfigDict(extra="forbid")

    platforms: list[str] = Field(default_factory=list)
    install_name: str = ""
    install_names: dict[str, str] = Field(default_factory=dict)
    plugin: CcPortPluginSettings | None = None

    @field_validator("platforms")
    @classmethod
    def _validate_platforms(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            tool_id = str(value).strip()
            if not RESOURCE_KIND_RE.fullmatch(tool_id):
                raise ValueError(
                    "platforms must contain only portable tool ids using lowercase "
                    "letters, digits, '.', '_' or '-'."
                )
            if tool_id not in seen:
                normalized.append(tool_id)
                seen.add(tool_id)
        return normalized

    @field_validator("install_name")
    @classmethod
    def _validate_install_name(cls, value: str) -> str:
        normalized = (value or "").strip()
        if normalized and not ITEM_NAME_RE.fullmatch(normalized):
            raise ValueError("install_name must be a safe single path segment.")
        return normalized

    @field_validator("install_names")
    @classmethod
    def _validate_install_names(cls, values: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for platform, name in values.items():
            platform_name = str(platform).strip()
            install_name = str(name).strip()
            if (
                not RESOURCE_KIND_RE.fullmatch(platform_name)
                or install_name in {".", ".."}
                or not SAFE_INSTALL_SEGMENT_RE.fullmatch(install_name)
            ):
                raise ValueError(
                    "install_names requires portable tool ids and safe install names."
                )
            normalized[platform_name] = install_name
        return normalized


class CcPortSettings(BaseModel):
    """Optional cc-port.yaml overlay."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    resources: dict[str, CcPortResourceSettings] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_version_and_keys(self) -> CcPortSettings:
        if self.version != 1:
            raise ValueError(f"Unsupported cc-port.yaml version: {self.version}.")
        for key, settings in self.resources.items():
            resource_key = ResourceKey.parse(key)
            if resource_key.kind == "instruction" and len(settings.platforms) != 1:
                raise ValueError(
                    "Instruction resources in cc-port.yaml must be bound to exactly "
                    "one portable source tool."
                )
            if resource_key.kind == "memory" and (
                settings.install_name or settings.install_names
            ):
                raise ValueError(
                    "Memory install names are machine-local and cannot be stored "
                    "in cc-port.yaml; configure platform memory_install_names instead."
                )
            if resource_key.kind == "memory" and (
                len(settings.platforms) != 1
                or settings.platforms[0] not in MEMORY_SOURCE_TOOL_IDS
            ):
                raise ValueError(
                    "Memory resources in cc-port.yaml must be bound to exactly one "
                    "supported source tool: Claude Code or Codex."
                )
        return self


class RegistryCatalog(BaseModel):
    """Tool-neutral registry v1 catalog plus a non-persisted CC Port overlay."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    version: int = 1
    resources: list[RegistryResource] = Field(default_factory=list)
    cc_port: CcPortSettings = Field(default_factory=CcPortSettings, exclude=True)
    _resolved_items: dict[str, RegistryItem] = PrivateAttr(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        runtime_items = data.pop("items", data.pop("skills", None))
        super().__init__(**data)
        if runtime_items is not None:
            self.resources = []
            for raw in runtime_items:
                entry = raw if isinstance(raw, RegistryItem) else RegistryItem.model_validate(raw)
                self.upsert(entry)

    @model_validator(mode="after")
    def _validate_plugin_distribution_identity(self) -> RegistryCatalog:
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
                    "Plugins with the same platform and source must share one resource key."
                )
            identities[identity] = item.resource_key
        return self

    @model_validator(mode="after")
    def _validate_portable_identity(self) -> RegistryCatalog:
        if self.version != 1:
            raise ValueError(f"Unsupported registry version: {self.version}.")
        keys: set[str] = set()
        paths: dict[str, str] = {}
        for resource in self.resources:
            if resource.resource_key in keys:
                raise ValueError(f"Duplicate resource identity: {resource.resource_key}.")
            keys.add(resource.resource_key)
            if resource.path:
                existing = paths.get(resource.path)
                if existing is not None:
                    raise ValueError(
                        f"Resources {existing} and {resource.resource_key} share path "
                        f"{resource.path!r}."
                    )
                paths[resource.path] = resource.resource_key
        return self

    @property
    def items(self) -> list[RegistryItem]:
        active_keys = {resource.resource_key for resource in self.resources}
        for key in list(self._resolved_items):
            if key not in active_keys:
                self._resolved_items.pop(key, None)
        resolved: list[RegistryItem] = []
        for resource in sorted(self.resources, key=lambda item: (item.kind, item.name)):
            entry = self._resolved_items.get(resource.resource_key)
            if entry is None:
                entry = self._resolved_item(resource)
                self._resolved_items[resource.resource_key] = entry
            resolved.append(entry)
        return resolved

    @property
    def skills(self) -> list[RegistryItem]:
        """Resolved resources for the legacy internal service surface."""
        return self.items

    def reset_resolved(self) -> None:
        """Rebuild runtime views after replacing the CC Port overlay."""
        self._resolved_items.clear()

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
        resource = self._portable_resource(entry)
        settings = self._portable_settings(entry)
        entry.external_source = resource.source
        for index, current in enumerate(self.resources):
            if current.resource_key == resource.resource_key:
                self.resources[index] = resource
                break
        else:
            self.resources.append(resource)
        self.resources.sort(key=lambda item: (item.kind, item.name))
        if settings is None:
            self.cc_port.resources.pop(entry.resource_key, None)
        else:
            self.cc_port.resources[entry.resource_key] = settings
        self._resolved_items[entry.resource_key] = entry

    def remove(self, name: str, kind: ItemKind | None = None) -> RegistryItem | None:
        entry = self.get(name, kind)
        if entry is None:
            return None
        for index, resource in enumerate(self.resources):
            if resource.resource_key == entry.resource_key:
                self.resources.pop(index)
                self.cc_port.resources.pop(entry.resource_key, None)
                self._resolved_items.pop(entry.resource_key, None)
                return entry
        return None

    def filter_by_kind(self, kind: ItemKind) -> list[RegistryItem]:
        return [item for item in self.items if item.kind == kind]

    def _resolved_item(self, resource: RegistryResource) -> RegistryItem:
        settings = self.cc_port.resources.get(resource.resource_key, CcPortResourceSettings())
        source = resource.source
        plugin: PluginSpec | None = None
        if settings.plugin is not None:
            origin_type: PluginOriginType = (
                source.type
                if source is not None and source.type in {"marketplace", "npm", "git", "local"}
                else "local"
            )  # type: ignore[assignment]
            origin = PluginOrigin(
                type=origin_type,
                marketplace=(
                    settings.plugin.marketplace
                    if settings.plugin.marketplace
                    else source.locator.split("/", 1)[0]
                    if source and origin_type == "marketplace"
                    else ""
                ),
                source=(
                    source.locator if source and origin_type in {"marketplace", "local"} else ""
                ),
                package=(source.locator if source and origin_type == "npm" else ""),
                repo=(source.locator if source and origin_type == "git" else ""),
                selector=(source.revision if source else ""),
            )
            plugin = PluginSpec(
                track="content" if resource.path else "reference",
                platform=settings.plugin.platform,
                plugin_id=settings.plugin.plugin_id,
                origin=origin,
                installations=list(settings.plugin.installations),
                dependencies=dict(settings.plugin.dependencies),
            )
        repo = source.locator if source is not None and source.type == "git" else ""
        return RegistryItem(
            name=resource.name,
            kind=resource.kind,
            repo=repo,
            source="local" if resource.path else "external",
            path=resource.path,
            subdir=source.subpath if source else "",
            ref=source.revision if source else "",
            install_dir=settings.install_name,
            platform_install_dirs=dict(settings.install_names),
            platforms=list(settings.platforms),
            plugin=plugin,
            external_source=source,
        )

    @staticmethod
    def _portable_resource(entry: RegistryItem) -> RegistryResource:
        if entry.path:
            return RegistryResource(kind=entry.kind, name=entry.name, path=entry.path)
        source = entry.external_source
        if source is None and entry.plugin is not None:
            origin = entry.plugin.origin
            locator = (
                origin.source or origin.marketplace
                if origin.type == "marketplace"
                else origin.package
                if origin.type == "npm"
                else origin.repo
                if origin.type == "git"
                else origin.source
            )
            source = ExternalSource(
                type=origin.type,
                locator=locator,
                revision=origin.selector,
            )
        if source is None and entry.repo:
            source = ExternalSource(
                type="git",
                locator=entry.repo,
                revision=entry.ref,
                subpath=entry.subdir,
            )
        if source is None:
            raise ValueError(f"Resource {entry.resource_key} has neither path nor source.")
        return RegistryResource(kind=entry.kind, name=entry.name, source=source)

    @staticmethod
    def _portable_settings(entry: RegistryItem) -> CcPortResourceSettings | None:
        if entry.kind == "memory" and (
            entry.install_dir or entry.platform_install_dirs
        ):
            raise ValueError(
                "Memory install names are machine-local and cannot be stored "
                "in cc-port.yaml; configure platform memory_install_names instead."
            )
        plugin_settings = None
        if entry.plugin is not None:
            plugin_settings = CcPortPluginSettings(
                platform=entry.plugin.platform,
                plugin_id=entry.plugin.plugin_id,
                marketplace=entry.plugin.origin.marketplace,
                installations=list(entry.plugin.installations),
                dependencies=dict(entry.plugin.dependencies),
            )
        if not (
            entry.platforms
            or entry.install_dir
            or entry.platform_install_dirs
            or plugin_settings is not None
        ):
            return None
        return CcPortResourceSettings(
            platforms=list(entry.platforms),
            install_name=entry.install_dir,
            install_names=dict(entry.platform_install_dirs),
            plugin=plugin_settings,
        )


# Internal compatibility alias while consumers migrate to RegistryCatalog.
Registry = RegistryCatalog
