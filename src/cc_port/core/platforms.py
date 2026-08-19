"""Platform profiles for AI coding tools.

Each platform declares where skills, MCP configs, rules, prompts, and plugins
should be installed. The user enables one or more platforms in their
config.toml under ``[platforms.<name>]`` sections.

Built-in presets cover popular tools; custom platforms can be added purely
via config.toml without changing code.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import (
    ITEM_NAME_RE,
    MEMORY_SOURCE_TOOL_IDS,
    SAFE_INSTALL_SEGMENT_RE,
    ItemKind,
)
from .tool_adapters import TOOL_ADAPTERS

PORTABLE_TOOL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def validate_portable_tool_id(value: str) -> str:
    """Return one repository-safe tool id or reject local/path-like values."""
    normalized = str(value).strip()
    if not PORTABLE_TOOL_ID_RE.fullmatch(normalized):
        raise ValueError(
            "portable tool ids must use lowercase letters, digits, dots, underscores, "
            "or hyphens and must not contain paths or control characters."
        )
    return normalized


def is_cross_platform_absolute_path(value: str) -> bool:
    """Recognize native, Windows drive, and UNC absolute paths on any host."""
    normalized = str(value).strip()
    return bool(
        Path(normalized).is_absolute()
        or re.match(r"^[A-Za-z]:[\\/]", normalized)
        or normalized.startswith(("\\\\", "//"))
    )


def validate_profile_id(value: str) -> str:
    """Return one stable local profile id safe for plans and TOML keys."""
    normalized = str(value).strip()
    if not PROFILE_ID_RE.fullmatch(normalized):
        raise ValueError(
            "profile ids must use lowercase letters, digits, dots, underscores, "
            "or hyphens and must not contain paths or control characters."
        )
    return normalized


@dataclass
class PlatformProfile:
    """Describes one AI coding tool's directory conventions."""

    name: str
    tool_id: str = ""
    environment_kind: str = ""
    environment_name: str = ""
    display_name: str = ""
    home_dir: str = ""
    enabled: bool = True
    skills_dir: str = ""
    mcp_json: str = ""
    rules_dir: str = ""
    prompts_dir: str = ""
    plugins_dir: str = ""
    instructions_path: str = ""
    memories_dir: str = ""
    memory_layout: str = "projects"
    settings_path: str = ""
    memory_install_names: dict[str, str] = field(default_factory=dict)

    @property
    def effective_tool_id(self) -> str:
        return self.tool_id or self.name

    @property
    def runtime_namespace(self) -> str:
        kind = self.environment_kind or "unknown"
        suffix = f":{self.environment_name.casefold()}" if self.environment_name else ""
        return f"{self.effective_tool_id}@{kind}{suffix}"

    @property
    def effective_display_name(self) -> str:
        return self.display_name or self.name

    def skills_path(self) -> Path | None:
        return self.expand_profile_path(self.skills_dir) if self.skills_dir else None

    def mcp_json_path(self) -> Path | None:
        return self.expand_profile_path(self.mcp_json) if self.mcp_json else None

    def rules_path(self) -> Path | None:
        return self.expand_profile_path(self.rules_dir) if self.rules_dir else None

    def prompts_path(self) -> Path | None:
        return self.expand_profile_path(self.prompts_dir) if self.prompts_dir else None

    def plugins_path(self) -> Path | None:
        return self.expand_profile_path(self.plugins_dir) if self.plugins_dir else None

    def instructions_file(self) -> Path | None:
        if not self.instructions_path:
            return None
        return select_instruction_path(
            self.effective_tool_id,
            self.expand_profile_path(self.instructions_path),
        )

    def memories_path(self) -> Path | None:
        return self.expand_profile_path(self.memories_dir) if self.memories_dir else None

    def settings_file(self) -> Path | None:
        return self.expand_profile_path(self.settings_path) if self.settings_path else None

    def home_path(self) -> Path:
        if not self.home_dir or self.home_dir == "~":
            return Path.home()
        return Path(self.home_dir).expanduser()

    def expand_profile_path(self, value: str) -> Path:
        if value == "~":
            return self.home_path()
        if value.startswith(("~/", "~\\")):
            return self.home_path() / value[2:]
        return Path(value).expanduser()

    def supports_resource_platforms(self, platforms: list[str]) -> bool:
        """Match repository metadata only against the portable tool identity."""
        return not platforms or self.effective_tool_id in platforms

    def supports_resource(self, kind: ItemKind, platforms: list[str]) -> bool:
        """Apply kind-specific safety policy before matching a runtime profile."""
        if kind == "instruction" and not platforms:
            return False
        if kind == "memory" and (
            not platforms or self.effective_tool_id not in MEMORY_SOURCE_TOOL_IDS
        ):
            return False
        return self.supports_resource_platforms(platforms)

    def resolve_install_path(self, kind: ItemKind, item_name: str) -> Path | None:
        """Return the target path for a given resource kind, or None if the
        platform does not support that kind."""
        if kind == "skill":
            base = self.skills_path()
            return base / item_name if base else None
        if kind == "rule":
            base = self.rules_path()
            return base / item_name if base else None
        if kind == "prompt":
            base = self.prompts_path()
            if base:
                return base / f"{item_name}.md"
            legacy_base = self.rules_path()
            return legacy_base / item_name if legacy_base else None
        if kind == "mcp":
            return self.mcp_json_path()
        if kind == "plugin":
            base = (
                self.skills_path()
                if self.effective_tool_id == "claude-code"
                else self.plugins_path()
            )
            return base / item_name if base else None
        if kind == "instruction":
            return self.instructions_file()
        if kind == "memory":
            base = self.memories_path()
            if base is None:
                return None
            if self.memory_layout == "direct":
                return base
            if self.memory_layout == "projects":
                if len(set(self.memory_install_names.values())) != len(
                    self.memory_install_names
                ):
                    raise ValueError(
                        "memory_install_names values must be unique within a profile."
                    )
                slot = self.memory_install_names.get(item_name) or item_name
                if (
                    slot in {".", ".."}
                    or not SAFE_INSTALL_SEGMENT_RE.fullmatch(slot)
                ):
                    raise ValueError("memory project slot must be one safe path segment.")
                return base / slot / "memory"
            return None
        return None


def current_environment_identity() -> tuple[str, str]:
    """Return a local-only runtime identity for one configured installation."""
    if sys.platform == "win32":
        return "windows", ""
    if sys.platform == "darwin":
        return "macos", ""
    distro = os.environ.get("WSL_DISTRO_NAME", "").strip()
    if distro:
        return "wsl", distro
    if sys.platform.startswith("linux"):
        try:
            release = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8")
        except OSError:
            release = ""
        if re.search(r"microsoft|wsl", release, re.IGNORECASE):
            return "wsl", ""
        return "linux", ""
    return "unknown", ""


def select_instruction_path(tool_id: str, configured: Path) -> Path:
    """Resolve native instruction precedence without hiding unsafe overrides."""
    if tool_id != "codex" or configured.name != "AGENTS.md":
        return configured
    override = configured.with_name("AGENTS.override.md")
    if not (override.exists() or override.is_symlink()):
        return configured
    if override.is_file() and not override.is_symlink():
        try:
            with override.open("r", encoding="utf-8") as handle:
                while chunk := handle.read(8192):
                    if chunk.strip():
                        return override
            return configured
        except (OSError, UnicodeError):
            return override
    return override


# ---- Built-in platform presets ---- #
# Users can add arbitrary platforms via config.toml without touching this dict.
# Example:
#   [platforms.windsurf]
#   enabled = true
#   skills_dir = "~/.windsurf/skills"
#   mcp_json = "~/.windsurf/mcp.json"

_DEFAULT_ENVIRONMENT_KIND, _DEFAULT_ENVIRONMENT_NAME = current_environment_identity()


PLATFORM_PRESETS: dict[str, PlatformProfile] = {
    adapter.id: PlatformProfile(
        name=adapter.id,
        tool_id=adapter.id,
        environment_kind=_DEFAULT_ENVIRONMENT_KIND,
        environment_name=_DEFAULT_ENVIRONMENT_NAME,
        display_name=adapter.name,
        home_dir="~",
        enabled=adapter.default_enabled,
        skills_dir=adapter.skills_dir,
        mcp_json=adapter.mcp_json,
        rules_dir=adapter.rules_dir,
        prompts_dir=adapter.prompts_dir,
        plugins_dir=adapter.plugins_dir,
        instructions_path=adapter.instructions_path,
        memories_dir=adapter.memories_dir,
        memory_layout=adapter.memory_layout,
        settings_path=adapter.settings_path,
        memory_install_names={},
    )
    for adapter in TOOL_ADAPTERS
    if adapter.expose_platform_preset
}


def build_platform(name: str, overrides: dict[str, Any] | None = None) -> PlatformProfile:
    """Create a PlatformProfile, starting from a preset if one exists.

    Any ``name`` not in PLATFORM_PRESETS creates a blank profile, allowing
    users to add custom platforms purely via config.toml.
    """
    name = validate_profile_id(name)
    requested_tool_id = validate_portable_tool_id(
        str((overrides or {}).get("tool_id") or name)
    )
    preset = PLATFORM_PRESETS.get(requested_tool_id)
    base = PlatformProfile(
        name=name,
        tool_id=requested_tool_id,
    ) if preset is None else PlatformProfile(
        name=name,
        tool_id=preset.tool_id,
        environment_kind=preset.environment_kind,
        environment_name=preset.environment_name,
        display_name=preset.display_name,
        home_dir=preset.home_dir,
        enabled=preset.enabled,
        skills_dir=preset.skills_dir,
        mcp_json=preset.mcp_json,
        rules_dir=preset.rules_dir,
        prompts_dir=preset.prompts_dir,
        plugins_dir=preset.plugins_dir,
        instructions_path=preset.instructions_path,
        memories_dir=preset.memories_dir,
        memory_layout=preset.memory_layout,
        settings_path=preset.settings_path,
        memory_install_names=dict(preset.memory_install_names),
    )
    if overrides:
        if "tool_id" in overrides:
            base.tool_id = validate_portable_tool_id(
                str(overrides["tool_id"] or name)
            )
        if "environment_kind" in overrides:
            base.environment_kind = str(overrides["environment_kind"] or "").strip().lower()
        if "environment_name" in overrides:
            base.environment_name = str(overrides["environment_name"] or "").strip()
        if "display_name" in overrides:
            base.display_name = str(overrides["display_name"] or "").strip()
        if "home_dir" in overrides:
            home_dir = str(overrides["home_dir"] or "").strip()
            if home_dir and home_dir != "~" and not is_cross_platform_absolute_path(
                home_dir
            ):
                raise ValueError(
                    "platform home_dir must be '~' or an absolute native/UNC path."
                )
            base.home_dir = home_dir
        if "enabled" in overrides:
            base.enabled = bool(overrides["enabled"])
        if "skills_dir" in overrides:
            base.skills_dir = str(overrides["skills_dir"] or "")
        if "mcp_json" in overrides:
            base.mcp_json = str(overrides["mcp_json"] or "")
        if "rules_dir" in overrides:
            base.rules_dir = str(overrides["rules_dir"] or "")
        if "prompts_dir" in overrides:
            base.prompts_dir = str(overrides["prompts_dir"] or "")
        if "plugins_dir" in overrides:
            base.plugins_dir = str(overrides["plugins_dir"] or "")
        if "instructions_path" in overrides:
            base.instructions_path = str(overrides["instructions_path"] or "")
        if "memories_dir" in overrides:
            base.memories_dir = str(overrides["memories_dir"] or "")
        if "memory_layout" in overrides:
            layout = str(overrides["memory_layout"] or "projects").strip().lower()
            if layout not in {"projects", "direct"}:
                raise ValueError("platform memory_layout must be 'projects' or 'direct'.")
            base.memory_layout = layout
        if "settings_path" in overrides:
            base.settings_path = str(overrides["settings_path"] or "")
        if "memory_install_names" in overrides:
            raw_names = overrides["memory_install_names"] or {}
            if not isinstance(raw_names, dict):
                raise ValueError("platform memory_install_names must be a mapping.")
            normalized_names: dict[str, str] = {}
            for resource, slot in raw_names.items():
                resource_name = str(resource).strip()
                slot_name = str(slot).strip()
                if not ITEM_NAME_RE.fullmatch(resource_name):
                    raise ValueError(
                        "platform memory_install_names keys must be valid resource names."
                    )
                if (
                    slot_name in {".", ".."}
                    or not SAFE_INSTALL_SEGMENT_RE.fullmatch(slot_name)
                ):
                    raise ValueError(
                        "platform memory_install_names values must be safe path segments."
                    )
                normalized_names[resource_name] = slot_name
            if len(set(normalized_names.values())) != len(normalized_names):
                raise ValueError(
                    "platform memory_install_names values must be unique within a profile."
                )
            base.memory_install_names = normalized_names
    return base


def default_platform_profiles() -> list[PlatformProfile]:
    """Return every complete, user-configurable preset enabled for new users."""
    return [build_platform(name, {"enabled": True}) for name in PLATFORM_PRESETS]


@dataclass
class PlatformsConfig:
    """Collection of all configured platforms."""

    profiles: list[PlatformProfile] = field(default_factory=list)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for profile in self.profiles:
            profile.name = validate_profile_id(profile.name)
            if profile.name in seen:
                raise ValueError(f"Duplicate platform profile id: {profile.name}.")
            seen.add(profile.name)

    def enabled(self) -> list[PlatformProfile]:
        return [p for p in self.profiles if p.enabled]

    def get(self, name: str) -> PlatformProfile | None:
        for p in self.profiles:
            if p.name == name:
                return p
        return None

    def primary_skills_path(self) -> Path | None:
        """Return the skills_dir of the first enabled platform, for backward
        compat with the old single ``[install].target`` path."""
        for p in self.enabled():
            sp = p.skills_path()
            if sp:
                return sp
        return None


def resolve_portable_resource_platforms(
    profiles: PlatformsConfig,
    kind: ItemKind | str,
    values: list[str] | None,
    *,
    source_tool_id: str = "",
) -> list[str]:
    """Normalize local runtime profile ids to safe portable tool ids.

    Unknown raw strings are rejected so a typo or local namespace can never be
    persisted into portable repository metadata.  Explicitly configured custom
    tool ids and every built-in adapter id remain valid portable bindings.
    """
    requested = [source_tool_id] if source_tool_id else list(values or [])
    configured_tool_ids = {
        validate_portable_tool_id(profile.effective_tool_id)
        for profile in profiles.profiles
    }
    known_tool_ids = configured_tool_ids | {adapter.id for adapter in TOOL_ADAPTERS}
    portable: list[str] = []
    for raw_value in requested:
        value = str(raw_value).strip()
        if not value:
            continue
        profile = profiles.get(value)
        if profile is not None:
            tool_id = validate_portable_tool_id(profile.effective_tool_id)
        elif value in known_tool_ids:
            tool_id = validate_portable_tool_id(value)
        else:
            raise ValueError(
                f"Unknown platform or portable tool id: {value!r}. "
                "Configure the runtime profile before using it in portable metadata."
            )
        if tool_id not in portable:
            portable.append(tool_id)

    if kind == "instruction":
        if not portable:
            raise ValueError(
                "Instruction uploads require an explicit portable tool binding. "
                "Use a configured profile id or a tool id such as 'claude-code' or 'codex'."
            )
        if len(portable) != 1:
            raise ValueError(
                "Instruction resources must be bound to exactly one portable source tool."
            )
    if kind == "memory":
        if not portable:
            raise ValueError(
                "Memory uploads require an explicit portable tool binding. "
                "Use a configured profile id or 'claude-code' or 'codex'."
            )
        if len(portable) != 1 or portable[0] not in MEMORY_SOURCE_TOOL_IDS:
            raise ValueError(
                "Memory resources must be bound to exactly one supported source tool: "
                "Claude Code or Codex."
            )
    return portable


def load_platforms_from_dict(
    data: dict[str, Any],
    *,
    new_config: bool = False,
) -> PlatformsConfig:
    """Parse the ``[platforms]`` section of config.toml.

    New files enable all complete presets. Existing files without a platform
    section retain the historical Cursor-only fallback.
    """
    platforms_data = data.get("platforms") or {}
    profiles: list[PlatformProfile] = []
    seen: set[str] = set()

    for name, section in platforms_data.items():
        if not isinstance(section, dict):
            continue
        profiles.append(build_platform(name, section))
        seen.add(name)

    # Existing files retain the historical Cursor-only fallback. A genuinely
    # new configuration enables every preset that exposes complete paths.
    if not profiles:
        profiles.extend(
            default_platform_profiles()
            if new_config
            else [build_platform("cursor", {"enabled": True})]
        )

    return PlatformsConfig(profiles=profiles)
