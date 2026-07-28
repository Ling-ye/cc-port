"""Platform profiles for AI coding tools.

Each platform declares where skills, MCP configs, rules, prompts, and plugins
should be installed. The user enables one or more platforms in their
config.toml under ``[platforms.<name>]`` sections.

Built-in presets cover popular tools; custom platforms can be added purely
via config.toml without changing code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import ItemKind
from .tool_adapters import TOOL_ADAPTERS


@dataclass
class PlatformProfile:
    """Describes one AI coding tool's directory conventions."""

    name: str
    enabled: bool = True
    skills_dir: str = ""
    mcp_json: str = ""
    rules_dir: str = ""
    prompts_dir: str = ""
    plugins_dir: str = ""

    def skills_path(self) -> Path | None:
        return Path(self.skills_dir).expanduser() if self.skills_dir else None

    def mcp_json_path(self) -> Path | None:
        return Path(self.mcp_json).expanduser() if self.mcp_json else None

    def rules_path(self) -> Path | None:
        return Path(self.rules_dir).expanduser() if self.rules_dir else None

    def prompts_path(self) -> Path | None:
        return Path(self.prompts_dir).expanduser() if self.prompts_dir else None

    def plugins_path(self) -> Path | None:
        return Path(self.plugins_dir).expanduser() if self.plugins_dir else None

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
            base = self.plugins_path()
            return base / item_name if base else None
        return None


# ---- Built-in platform presets ---- #
# Users can add arbitrary platforms via config.toml without touching this dict.
# Example:
#   [platforms.windsurf]
#   enabled = true
#   skills_dir = "~/.windsurf/skills"
#   mcp_json = "~/.windsurf/mcp.json"

PLATFORM_PRESETS: dict[str, PlatformProfile] = {
    adapter.id: PlatformProfile(
        name=adapter.id,
        enabled=adapter.default_enabled,
        skills_dir=adapter.skills_dir,
        mcp_json=adapter.mcp_json,
        rules_dir=adapter.rules_dir,
        prompts_dir=adapter.prompts_dir,
        plugins_dir=adapter.plugins_dir,
    )
    for adapter in TOOL_ADAPTERS
    if adapter.expose_platform_preset
}


def build_platform(name: str, overrides: dict[str, Any] | None = None) -> PlatformProfile:
    """Create a PlatformProfile, starting from a preset if one exists.

    Any ``name`` not in PLATFORM_PRESETS creates a blank profile, allowing
    users to add custom platforms purely via config.toml.
    """
    preset = PLATFORM_PRESETS.get(name)
    base = PlatformProfile(name=name) if preset is None else PlatformProfile(
        name=preset.name,
        enabled=preset.enabled,
        skills_dir=preset.skills_dir,
        mcp_json=preset.mcp_json,
        rules_dir=preset.rules_dir,
        prompts_dir=preset.prompts_dir,
        plugins_dir=preset.plugins_dir,
    )
    if overrides:
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
    return base


def default_platform_profiles() -> list[PlatformProfile]:
    """Return every complete, user-configurable preset enabled for new users."""
    return [build_platform(name, {"enabled": True}) for name in PLATFORM_PRESETS]


@dataclass
class PlatformsConfig:
    """Collection of all configured platforms."""

    profiles: list[PlatformProfile] = field(default_factory=list)

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
