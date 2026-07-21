"""Read-only discovery of local AI tools and their resource locations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..core.models import ItemKind
from ..core.tool_adapters import TOOL_ADAPTERS
from .mcp_installer import list_mcp_servers
from .publisher import _slug
from .resource_discovery import DiscoveredResource, discover_resources


@dataclass(frozen=True)
class ToolScanSpec:
    id: str
    name: str
    root: str
    config_files: tuple[str, ...] = ()
    resource_dirs: tuple[str, ...] = ()
    mcp_config_files: tuple[str, ...] = ()


@dataclass
class DiscoveredTool:
    id: str
    name: str
    root_path: Path
    detected: bool
    confidence: str
    config_paths: list[Path] = field(default_factory=list)
    resource_paths: list[Path] = field(default_factory=list)
    mcp_config_paths: list[Path] = field(default_factory=list)
    supports_kinds: list[ItemKind] = field(default_factory=list)


@dataclass
class DiscoveredMcpServer:
    id: str
    tool: str
    name: str
    config_path: Path
    config: dict[str, Any]
    secret_keys: list[str] = field(default_factory=list)


@dataclass
class EnvDiscoveryResult:
    tools: list[DiscoveredTool]
    resources: list[DiscoveredResource]
    mcp_servers: list[DiscoveredMcpServer]


TOOL_SPECS: tuple[ToolScanSpec, ...] = tuple(
    ToolScanSpec(
        id=adapter.id,
        name=adapter.name,
        root=adapter.discovery_root,
        config_files=adapter.config_files,
        resource_dirs=adapter.resource_dirs,
        mcp_config_files=adapter.mcp_config_files,
    )
    for adapter in TOOL_ADAPTERS
    if adapter.discovery_root
)

SECRET_KEY_RE = re.compile(
    r"(token|secret|api[_-]?key|auth|password|credential)",
    re.IGNORECASE,
)


def discover_environment(
    *,
    home: Path | None = None,
    registry_path_override: Path | None = None,
) -> EnvDiscoveryResult:
    """Discover local tools, logical resource candidates, and MCP entries without writing."""
    effective_home = home or Path.home()
    tools = [_discover_tool(spec, home=effective_home) for spec in TOOL_SPECS]
    resources = _discover_tool_resources(
        tools,
        registry_path_override=registry_path_override,
    )
    mcp_servers = _discover_mcp_servers(tools)
    return EnvDiscoveryResult(tools=tools, resources=resources, mcp_servers=mcp_servers)


def _discover_tool(spec: ToolScanSpec, *, home: Path) -> DiscoveredTool:
    root = _expand_home(spec.root, home=home)
    config_paths = _existing_paths(root, spec.config_files)
    resource_paths = _existing_paths(root, spec.resource_dirs, dirs_only=True)
    mcp_config_paths = _existing_paths(root, spec.mcp_config_files)
    detected = root.exists() or bool(config_paths or resource_paths or mcp_config_paths)
    supports = _supported_kinds(spec)
    confidence = (
        "high"
        if root.exists() and (config_paths or resource_paths or mcp_config_paths)
        else "medium"
        if detected
        else "none"
    )
    return DiscoveredTool(
        id=spec.id,
        name=spec.name,
        root_path=root,
        detected=detected,
        confidence=confidence,
        config_paths=config_paths,
        resource_paths=resource_paths,
        mcp_config_paths=mcp_config_paths,
        supports_kinds=supports,
    )


def _discover_tool_resources(
    tools: list[DiscoveredTool],
    *,
    registry_path_override: Path | None,
) -> list[DiscoveredResource]:
    resources: list[DiscoveredResource] = []
    seen: set[str] = set()
    for tool in tools:
        for path in [tool.root_path, *tool.resource_paths]:
            if not path.is_dir():
                continue
            for resource in discover_resources(
                scope="directory",
                root_path=path,
                registry_path=registry_path_override,
            ):
                if resource.kind == "mcp" or resource.id in seen:
                    continue
                resource.tool = tool.id
                seen.add(resource.id)
                resources.append(resource)
    return sorted(
        resources,
        key=lambda item: (item.tool, item.kind, item.name_hint, str(item.path)),
    )


def _discover_mcp_servers(tools: list[DiscoveredTool]) -> list[DiscoveredMcpServer]:
    servers: list[DiscoveredMcpServer] = []
    for tool in tools:
        for path in tool.mcp_config_paths:
            for name, raw_config in _read_mcp_servers(path).items():
                if not isinstance(raw_config, dict):
                    continue
                servers.append(
                    DiscoveredMcpServer(
                        id=f"{tool.id}:{path}:{name}",
                        tool=tool.id,
                        name=_slug(name),
                        config_path=path,
                        config=dict(raw_config),
                        secret_keys=_secret_env_keys(raw_config),
                    )
                )
    return sorted(servers, key=lambda item: (item.tool, item.name))


def _read_mcp_servers(path: Path) -> dict[str, Any]:
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
        if isinstance(data, dict):
            servers = data.get("mcpServers")
            return dict(servers) if isinstance(servers, dict) else data
        return {}
    try:
        return list_mcp_servers(path)
    except Exception:
        return {}


def _secret_env_keys(config: dict[str, Any]) -> list[str]:
    env = config.get("env")
    if not isinstance(env, dict):
        return []
    keys = {
        str(key)
        for key, value in env.items()
        if value not in ("", None)
        and (
            SECRET_KEY_RE.search(str(key))
            or (isinstance(value, str) and not _is_placeholder(value))
        )
    }
    return sorted(keys)


def _supported_kinds(spec: ToolScanSpec) -> list[ItemKind]:
    kinds: set[ItemKind] = set()
    directories = {item.lower() for item in spec.resource_dirs}
    if "skills" in directories:
        kinds.add("skill")
    if "rules" in directories:
        kinds.add("rule")
    if {"prompts", "commands"} & directories:
        kinds.add("prompt")
    if "plugins" in directories:
        kinds.add("plugin")
    if spec.mcp_config_files:
        kinds.add("mcp")
    return sorted(kinds)


def _existing_paths(
    root: Path,
    values: tuple[str, ...],
    *,
    dirs_only: bool = False,
) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        path = (root / value).expanduser().resolve()
        if (dirs_only and path.is_dir()) or (not dirs_only and path.exists()):
            paths.append(path)
    return paths


def _expand_home(value: str, *, home: Path) -> Path:
    if value == "~":
        return home
    if value.startswith(("~/", "~\\")):
        return (home / value[2:]).resolve()
    return Path(value).expanduser().resolve()


def _is_placeholder(value: str) -> bool:
    normalized = value.strip()
    return normalized.startswith("${") and normalized.endswith("}") and len(normalized) > 3
