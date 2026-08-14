"""Internal tool adapter registry.

The registry is deliberately internal for V1. It centralizes detection,
capabilities, default paths, discovery layout, and install mechanisms without
promising a third-party plugin API before the contract is stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .models import ItemKind

ToolStability = Literal["stable", "experimental"]
SignalKind = Literal[
    "command",
    "config_file",
    "extension_dir",
    "known_skills_dir",
    "native_cli_probe",
]


@dataclass(frozen=True)
class ToolSignal:
    kind: SignalKind
    value: str
    soft: bool = False


@dataclass(frozen=True)
class ToolAdapter:
    id: str
    name: str
    stability: ToolStability
    supports_kinds: tuple[ItemKind, ...]
    install_mechanisms: dict[ItemKind, str] = field(default_factory=dict)
    signals: tuple[ToolSignal, ...] = ()
    soft_detection: bool = False
    discovery_root: str = ""
    config_files: tuple[str, ...] = ()
    resource_dirs: tuple[str, ...] = ()
    mcp_config_files: tuple[str, ...] = ()
    expose_platform_preset: bool = True
    default_enabled: bool = False
    skills_dir: str = ""
    mcp_json: str = ""
    rules_dir: str = ""
    prompts_dir: str = ""
    plugins_dir: str = ""
    instructions_path: str = ""
    memories_dir: str = ""
    memory_layout: str = "projects"
    settings_path: str = ""

    def install_mechanism(self, kind: ItemKind) -> str:
        return self.install_mechanisms.get(kind, "copy_directory")


TOOL_ADAPTERS: tuple[ToolAdapter, ...] = (
    ToolAdapter(
        id="codex",
        name="Codex",
        stability="stable",
        supports_kinds=("skill", "instruction"),
        install_mechanisms={
            "skill": "copy_skills_dir",
            "instruction": "copy_instruction_file",
        },
        signals=(
            ToolSignal("known_skills_dir", "~/.codex/skills"),
            ToolSignal("config_file", "~/.codex/config.toml", soft=True),
        ),
        discovery_root="~/.codex",
        config_files=("config.toml",),
        resource_dirs=("skills", "prompts", "rules", "plugins"),
        skills_dir="~/.codex/skills",
        plugins_dir="~/.codex/plugins",
        instructions_path="~/.codex/AGENTS.md",
        settings_path="~/.codex/config.toml",
    ),
    ToolAdapter(
        id="claude-code",
        name="Claude Code",
        stability="stable",
        supports_kinds=("skill", "mcp", "rule", "plugin", "instruction", "memory"),
        install_mechanisms={
            "skill": "claude_plugin_or_copy",
            "mcp": "json_mcp_servers_patch",
            "plugin": "claude_skills_dir_or_marketplace",
            "instruction": "copy_instruction_file",
            "memory": "copy_auto_memory_directory",
        },
        signals=(
            ToolSignal("command", "claude"),
            ToolSignal("config_file", "~/.claude.json", soft=True),
            ToolSignal("known_skills_dir", "~/.claude/skills", soft=True),
        ),
        discovery_root="~/.claude",
        config_files=("settings.json", "../.claude.json"),
        resource_dirs=("skills", "commands", "prompts", "rules", "plugins"),
        mcp_config_files=("../.claude.json",),
        skills_dir="~/.claude/skills",
        mcp_json="~/.claude.json",
        rules_dir="~/.claude/rules",
        # ~/.claude/plugins is Claude's marketplace/cache runtime root, not a
        # native content-install directory.  Manifest-backed local plugins live
        # under skills_dir; optional source libraries must be configured
        # explicitly with a different plugins_dir.
        plugins_dir="",
        instructions_path="~/.claude/CLAUDE.md",
        memories_dir="~/.claude/projects",
        memory_layout="projects",
        settings_path="~/.claude/settings.json",
    ),
    ToolAdapter(
        id="cursor",
        name="Cursor",
        stability="stable",
        supports_kinds=("skill", "mcp", "rule", "prompt"),
        install_mechanisms={
            "skill": "skills_cli_or_copy",
            "mcp": "json_mcp_servers_patch",
            "rule": "skills_cli_or_copy",
            "prompt": "skills_cli_or_copy",
        },
        signals=(
            ToolSignal("known_skills_dir", "~/.cursor/skills"),
            ToolSignal("config_file", "~/.cursor/mcp.json", soft=True),
        ),
        discovery_root="~/.cursor",
        config_files=("mcp.json",),
        resource_dirs=("skills", "commands", "rules", "prompts", "plugins"),
        mcp_config_files=("mcp.json",),
        default_enabled=True,
        skills_dir="~/.cursor/skills",
        mcp_json="~/.cursor/mcp.json",
        prompts_dir="~/.cursor/commands",
    ),
    ToolAdapter(
        id="windsurf",
        name="Windsurf",
        stability="experimental",
        supports_kinds=("skill", "mcp", "rule", "prompt"),
        install_mechanisms={
            "skill": "skills_cli_or_copy",
            "mcp": "json_mcp_servers_patch",
            "rule": "skills_cli_or_copy",
            "prompt": "skills_cli_or_copy",
        },
        signals=(
            ToolSignal("known_skills_dir", "~/.windsurf/skills"),
            ToolSignal("config_file", "~/.windsurf/mcp.json", soft=True),
        ),
        discovery_root="~/.windsurf",
        config_files=("mcp.json",),
        resource_dirs=("skills", "rules", "prompts", "plugins"),
        mcp_config_files=("mcp.json",),
        skills_dir="~/.windsurf/skills",
        mcp_json="~/.windsurf/mcp.json",
    ),
    ToolAdapter(
        id="cline",
        name="Cline",
        stability="experimental",
        supports_kinds=("skill", "mcp", "rule", "prompt"),
        install_mechanisms={
            "skill": "skills_cli_or_copy",
            "mcp": "json_mcp_servers_patch",
            "rule": "skills_cli_or_copy",
            "prompt": "skills_cli_or_copy",
        },
        signals=(
            ToolSignal("extension_dir", "~/.vscode/extensions", soft=True),
            ToolSignal("extension_dir", "~/.cursor/extensions", soft=True),
        ),
        soft_detection=True,
        expose_platform_preset=False,
    ),
    ToolAdapter(
        id="opencode",
        name="opencode",
        stability="experimental",
        supports_kinds=("skill", "mcp", "rule", "prompt", "plugin"),
        install_mechanisms={
            "skill": "native_plugin_commands_agents",
            "mcp": "json_mcp_servers_patch",
            "rule": "native_plugin_commands_agents",
            "prompt": "native_plugin_commands_agents",
            "plugin": "native_plugin_commands_agents",
        },
        signals=(
            ToolSignal("command", "opencode"),
            ToolSignal("config_file", "~/.config/opencode/opencode.json", soft=True),
            ToolSignal("known_skills_dir", "~/.config/opencode/skills", soft=True),
        ),
        discovery_root="~/.config/opencode",
        config_files=("opencode.json",),
        resource_dirs=("skills", "rules", "prompts", "commands", "plugins"),
        mcp_config_files=("opencode.json",),
        skills_dir="~/.config/opencode/skills",
        mcp_json="~/.config/opencode/opencode.json",
        rules_dir="~/.config/opencode/rules",
        plugins_dir="~/.config/opencode/plugins",
    ),
    ToolAdapter(
        id="gemini",
        name="Gemini CLI",
        stability="experimental",
        supports_kinds=("rule", "prompt"),
        install_mechanisms={
            "rule": "copy_gemini_commands",
            "prompt": "copy_gemini_commands",
        },
        signals=(
            ToolSignal("command", "gemini"),
            ToolSignal("config_file", "~/.gemini/settings.json", soft=True),
        ),
        discovery_root="~/.gemini",
        config_files=("settings.json",),
        resource_dirs=("commands", "prompts", "rules"),
        expose_platform_preset=False,
    ),
)


def tool_adapter_by_id(tool_id: str) -> ToolAdapter | None:
    for adapter in TOOL_ADAPTERS:
        if adapter.id == tool_id:
            return adapter
    return None


def stable_tool_adapters() -> tuple[ToolAdapter, ...]:
    return tuple(adapter for adapter in TOOL_ADAPTERS if adapter.stability == "stable")
