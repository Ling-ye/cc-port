"""Platform profiles for AI coding tools.

Each platform declares where skills, MCP configs, and rules should be
installed.  The user enables one or more platforms in their config.toml
under ``[platforms.<name>]`` sections.

Built-in presets cover popular tools; custom platforms can be added purely
via config.toml without changing code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import ItemKind


@dataclass
class PlatformProfile:
    """Describes one AI coding tool's directory conventions."""

    name: str
    enabled: bool = True
    skills_dir: str = ""
    mcp_json: str = ""
    rules_dir: str = ""

    def skills_path(self) -> Path | None:
        return Path(self.skills_dir).expanduser() if self.skills_dir else None

    def mcp_json_path(self) -> Path | None:
        return Path(self.mcp_json).expanduser() if self.mcp_json else None

    def rules_path(self) -> Path | None:
        return Path(self.rules_dir).expanduser() if self.rules_dir else None

    def resolve_install_path(self, kind: ItemKind, item_name: str) -> Path | None:
        """Return the target path for a given resource kind, or None if the
        platform does not support that kind."""
        if kind == "skill":
            base = self.skills_path()
            return base / item_name if base else None
        if kind == "rule":
            base = self.rules_path()
            return base / item_name if base else None
        if kind == "mcp":
            return self.mcp_json_path()
        return None


# ---- Built-in platform presets ---- #
# Users can add arbitrary platforms via config.toml without touching this dict.
# Example:
#   [platforms.windsurf]
#   enabled = true
#   skills_dir = "~/.windsurf/skills"
#   mcp_json = "~/.windsurf/mcp.json"

PLATFORM_PRESETS: dict[str, PlatformProfile] = {
    "cursor": PlatformProfile(
        name="cursor",
        enabled=True,
        skills_dir="~/.cursor/skills",
        mcp_json="~/.cursor/mcp.json",
        rules_dir="",
    ),
    "claude-code": PlatformProfile(
        name="claude-code",
        enabled=False,
        skills_dir="~/.claude/skills",
        mcp_json="~/.claude.json",
        rules_dir="",
    ),
    "windsurf": PlatformProfile(
        name="windsurf",
        enabled=False,
        skills_dir="~/.windsurf/skills",
        mcp_json="~/.windsurf/mcp.json",
        rules_dir="",
    ),
    "codex": PlatformProfile(
        name="codex",
        enabled=False,
        skills_dir="~/.codex/skills",
        mcp_json="",
        rules_dir="",
    ),
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
    return base


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


def load_platforms_from_dict(data: dict[str, Any]) -> PlatformsConfig:
    """Parse the ``[platforms]`` section of config.toml.

    If no ``[platforms]`` section exists, returns a default config with
    Cursor enabled.
    """
    platforms_data = data.get("platforms") or {}
    profiles: list[PlatformProfile] = []
    seen: set[str] = set()

    for name, section in platforms_data.items():
        if not isinstance(section, dict):
            continue
        profiles.append(build_platform(name, section))
        seen.add(name)

    # If user configured nothing, default to cursor-only
    if not profiles:
        profiles.append(build_platform("cursor", {"enabled": True}))

    return PlatformsConfig(profiles=profiles)
