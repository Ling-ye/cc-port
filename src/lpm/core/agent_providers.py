"""Agent/provider detection matrix for local AI coding tools."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .models import ItemKind

DetectionSignalKind = Literal[
    "command",
    "config_file",
    "extension_dir",
    "known_skills_dir",
    "native_cli_probe",
]


@dataclass(frozen=True)
class DetectionSignal:
    kind: DetectionSignalKind
    value: str
    soft: bool = False


@dataclass(frozen=True)
class AgentProvider:
    id: str
    name: str
    install_mechanism: str
    supports_kinds: tuple[ItemKind, ...] = ("skill",)
    signals: tuple[DetectionSignal, ...] = ()
    soft: bool = False


@dataclass(frozen=True)
class AgentDetection:
    provider: AgentProvider
    detected: bool
    auto_install: bool
    matched_signals: tuple[DetectionSignal, ...] = ()
    missing_signals: tuple[DetectionSignal, ...] = ()
    notes: tuple[str, ...] = ()


PROVIDERS: tuple[AgentProvider, ...] = (
    AgentProvider(
        id="codex",
        name="Codex",
        install_mechanism="copy_skills_dir",
        supports_kinds=("skill",),
        signals=(
            DetectionSignal("known_skills_dir", "~/.codex/skills"),
            DetectionSignal("config_file", "~/.codex/config.toml", soft=True),
        ),
    ),
    AgentProvider(
        id="claude-code",
        name="Claude Code",
        install_mechanism="claude_plugin_or_copy",
        supports_kinds=("skill", "mcp", "plugin"),
        signals=(
            DetectionSignal("command", "claude"),
            DetectionSignal("config_file", "~/.claude.json", soft=True),
            DetectionSignal("known_skills_dir", "~/.claude/skills", soft=True),
        ),
    ),
    AgentProvider(
        id="cursor",
        name="Cursor",
        install_mechanism="skills_cli_or_copy",
        supports_kinds=("skill", "mcp", "rule", "prompt"),
        signals=(
            DetectionSignal("known_skills_dir", "~/.cursor/skills"),
            DetectionSignal("config_file", "~/.cursor/mcp.json", soft=True),
        ),
    ),
    AgentProvider(
        id="windsurf",
        name="Windsurf",
        install_mechanism="skills_cli_or_copy",
        supports_kinds=("skill", "mcp", "rule", "prompt"),
        signals=(
            DetectionSignal("known_skills_dir", "~/.windsurf/skills"),
            DetectionSignal("config_file", "~/.windsurf/mcp.json", soft=True),
        ),
    ),
    AgentProvider(
        id="cline",
        name="Cline",
        install_mechanism="skills_cli_or_copy",
        supports_kinds=("skill", "mcp", "rule", "prompt"),
        signals=(
            DetectionSignal("extension_dir", "~/.vscode/extensions", soft=True),
            DetectionSignal("extension_dir", "~/.cursor/extensions", soft=True),
        ),
        soft=True,
    ),
    AgentProvider(
        id="opencode",
        name="opencode",
        install_mechanism="native_plugin_commands_agents",
        supports_kinds=("skill", "mcp", "rule", "prompt", "plugin"),
        signals=(
            DetectionSignal("command", "opencode"),
            DetectionSignal("config_file", "~/.config/opencode/opencode.json", soft=True),
            DetectionSignal("known_skills_dir", "~/.config/opencode/skills", soft=True),
        ),
    ),
    AgentProvider(
        id="gemini",
        name="Gemini CLI",
        install_mechanism="copy_gemini_commands",
        supports_kinds=("rule", "prompt"),
        signals=(
            DetectionSignal("command", "gemini"),
            DetectionSignal("config_file", "~/.gemini/settings.json", soft=True),
        ),
    ),
)


def provider_by_id(provider_id: str) -> AgentProvider | None:
    for provider in PROVIDERS:
        if provider.id == provider_id:
            return provider
    return None


def detect_agents(*, home: Path | None = None, path: str | None = None) -> list[AgentDetection]:
    """Detect known local AI coding tools.

    Strong signals enable auto-install. Providers or matches that only have
    soft signals are returned for user selection, but ``auto_install`` is false.
    """
    return [detect_agent(provider, home=home, path=path) for provider in PROVIDERS]


def detect_agent(
    provider: AgentProvider,
    *,
    home: Path | None = None,
    path: str | None = None,
) -> AgentDetection:
    matched: list[DetectionSignal] = []
    missing: list[DetectionSignal] = []
    notes: list[str] = []
    for signal in provider.signals:
        if _signal_exists(signal, home=home, path=path):
            matched.append(signal)
        else:
            missing.append(signal)

    detected = bool(matched)
    strong_match = any(not signal.soft for signal in matched)
    auto_install = detected and strong_match and not provider.soft
    if detected and not auto_install:
        notes.append("Detected only by soft signals; explicit user selection is required.")
    return AgentDetection(
        provider=provider,
        detected=detected,
        auto_install=auto_install,
        matched_signals=tuple(matched),
        missing_signals=tuple(missing),
        notes=tuple(notes),
    )


def _signal_exists(signal: DetectionSignal, *, home: Path | None, path: str | None) -> bool:
    if signal.kind in {"command", "native_cli_probe"}:
        return shutil.which(signal.value, path=path) is not None
    target = _expand_home(signal.value, home=home)
    if signal.kind in {"config_file"}:
        return target.is_file()
    if signal.kind in {"extension_dir", "known_skills_dir"}:
        return target.is_dir()
    return False


def _expand_home(value: str, *, home: Path | None) -> Path:
    if home is None:
        return Path(value).expanduser()
    if value == "~":
        return home
    if value.startswith("~/") or value.startswith("~\\"):
        return home / value[2:]
    return Path(value).expanduser()
