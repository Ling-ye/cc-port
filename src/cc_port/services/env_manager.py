"""Read-only discovery of local AI tools and their resource locations."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from ..core.config import Config
from ..core.models import ItemKind
from ..core.platforms import (
    PlatformProfile,
    PlatformsConfig,
    build_platform,
    current_environment_identity,
    is_cross_platform_absolute_path,
    select_instruction_path,
)
from ..core.tool_adapters import TOOL_ADAPTERS, tool_adapter_by_id
from .local_path_probe import probe_local_path
from .mcp_installer import list_mcp_servers
from .plugin_management import DiscoveredPlugin, discover_plugins
from .publisher import _slug
from .resource_discovery import (
    DiscoveredResource,
    discover_exact_resource,
    discover_resources,
)


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
    tool_id: str = ""
    environment_kind: str = ""
    environment_name: str = ""
    display_name: str = ""
    instruction_path: Path | None = None
    memories_path: Path | None = None
    memory_layout: str = "projects"
    resource_path_kinds: dict[str, ItemKind] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    memory_blocker: str = ""
    memory_install_names: dict[str, str] = field(default_factory=dict)


@dataclass
class DiscoveredMcpServer:
    id: str
    tool: str
    name: str
    config_path: Path
    config: dict[str, Any]
    secret_keys: list[str] = field(default_factory=list)
    tool_id: str = ""
    environment_kind: str = ""
    environment_name: str = ""


@dataclass
class EnvDiscoveryResult:
    tools: list[DiscoveredTool]
    resources: list[DiscoveredResource]
    mcp_servers: list[DiscoveredMcpServer]
    plugins: list[DiscoveredPlugin] = field(default_factory=list)


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
    config: Config | None = None,
    scan_global: bool = True,
    project_ids: list[str] | None = None,
) -> EnvDiscoveryResult:
    """Discover local tools, logical resource candidates, and MCP entries without writing."""
    effective_home = home or Path.home()
    if config is None:
        profiles = [
            build_platform(spec.id, {"enabled": True})
            for spec in TOOL_SPECS
        ]
    else:
        profiles = list(config.platforms.enabled())
    tools: list[DiscoveredTool] = []
    available_profiles: list[PlatformProfile] = []
    for profile in profiles:
        profile_available, profile_problem = _profile_scan_available(
            profile,
            runtime_home=effective_home,
        )
        tools.append(
            _discover_profile_tool(profile, home=effective_home)
            if profile_available
            else _unavailable_profile_tool(
                profile,
                home=effective_home,
                problem=profile_problem,
            )
        )
        if profile_available:
            available_profiles.append(profile)
    resources = (
        _discover_tool_resources(
            tools,
            registry_path_override=registry_path_override,
        )
        if scan_global
        else []
    )
    # Plugin candidates are owned by platform adapters so cache installations can
    # never be mistaken for uploadable source content by the generic scanner.
    resources = [resource for resource in resources if resource.kind != "plugin"]
    mcp_servers = (
        _discover_mcp_servers(
            tools,
        )
        if scan_global
        else []
    )
    plugin_config = (
        replace(
            config,
            platforms=PlatformsConfig(profiles=available_profiles),
        )
        if config is not None
        else Config()
    )
    plugins = discover_plugins(
        plugin_config,
        home=effective_home,
        scan_global=scan_global,
        project_ids=project_ids,
    )
    profiles_by_name = {profile.name: profile for profile in profiles}
    for plugin in plugins:
        profile = profiles_by_name.get(plugin.platform)
        if profile is None:
            continue
        plugin.tool_id = profile.effective_tool_id
        plugin.environment_kind = profile.environment_kind
        plugin.environment_name = profile.environment_name
    return EnvDiscoveryResult(
        tools=tools,
        resources=resources,
        mcp_servers=mcp_servers,
        plugins=plugins,
    )


def _discover_profile_tool(profile: PlatformProfile, *, home: Path) -> DiscoveredTool:
    adapter = tool_adapter_by_id(profile.effective_tool_id)
    profile_home = _profile_home(profile, runtime_home=home)
    root = _profile_root(profile, home=profile_home)
    config_candidates = [
        _expand_home(profile.settings_path, home=profile_home)
        if profile.settings_path
        else None
    ]
    if adapter is not None and root is not None:
        config_candidates.extend(root / value for value in adapter.config_files)
    config_paths = _unique_existing_paths(config_candidates)
    resource_path_kinds: dict[str, ItemKind] = {}
    for kind, raw_path in (
        ("skill", profile.skills_dir),
        ("rule", profile.rules_dir),
        ("prompt", profile.prompts_dir),
    ):
        if not raw_path:
            continue
        path = _expand_home(raw_path, home=profile_home)
        if path.is_dir() or (kind == "rule" and path.is_symlink()):
            resource_path_kinds[str(path)] = kind  # type: ignore[assignment]
    # Discovery-only adapters (for example Gemini CLI) intentionally have no
    # install preset. Preserve their read-only resource discovery from the
    # adapter's narrowly declared subdirectories without scanning the whole root.
    if not resource_path_kinds and adapter is not None and root is not None:
        for relative in adapter.resource_dirs:
            kind = _adapter_resource_kind(adapter.supports_kinds, relative)
            candidate = root / relative
            if kind is not None and candidate.is_dir():
                resource_path_kinds[str(candidate)] = kind
    resource_paths = [Path(value) for value in resource_path_kinds]
    mcp_config_paths = []
    if profile.mcp_json:
        mcp = _expand_home(profile.mcp_json, home=profile_home)
        if mcp.is_file():
            mcp_config_paths.append(mcp)
    instruction_path = (
        _effective_instruction_path(profile, home=profile_home)
        if profile.instructions_path
        else None
    )
    memories_path = (
        _expand_home(profile.memories_dir, home=profile_home)
        if profile.memories_dir
        else None
    )
    memory_layout = profile.memory_layout
    warnings: list[str] = []
    memory_blocker = ""
    if instruction_path is not None:
        instruction_problem = _unsafe_path_component_problem(
            instruction_path,
            include_leaf=False,
        )
        if instruction_problem:
            warnings.append(
                "Claude/Codex instruction target has an unsafe ancestor: "
                + instruction_problem
            )
    if profile.effective_tool_id == "claude-code":
        override, override_warning, memory_settings_trusted = _claude_auto_memory_override(
            profile,
            home=profile_home,
        )
        if not memory_settings_trusted:
            memories_path = None
            memory_blocker = override_warning
        elif override is not None:
            memories_path = override
            memory_layout = "direct"
        if override_warning:
            warnings.append(override_warning)
        memory_root_present = False
        if memories_path is not None:
            ancestor_problem = _unsafe_path_component_problem(
                memories_path,
                include_leaf=True,
            )
            if ancestor_problem:
                memory_blocker = (
                    "Claude memory path cannot be accessed safely because it has an "
                    f"unsafe ancestor; automatic memory migration is blocked: {ancestor_problem}"
                )
                warnings.append(memory_blocker)
                memories_path = None
        if memories_path is not None:
            try:
                memory_root_present = memories_path.exists() or memories_path.is_symlink()
            except OSError:
                memory_blocker = (
                    "Claude memory root cannot be accessed from this runtime; "
                    "automatic memory migration is blocked."
                )
                warnings.append(memory_blocker)
                memories_path = None
        if memories_path is not None and memory_root_present:
            memory_probe = probe_local_path(memories_path)
            if memory_probe.is_link or not memory_probe.ready:
                memory_blocker = (
                    "Claude memory root uses a link or cannot be read safely; "
                    "automatic memory migration is blocked."
                )
                warnings.append(memory_blocker)
                memories_path = None
    detected_paths = [
        *config_paths,
        *resource_paths,
        *mcp_config_paths,
        *(path for path in (instruction_path, memories_path) if path is not None and path.exists()),
    ]
    detected = bool((root is not None and root.exists()) or detected_paths)
    supports = list(adapter.supports_kinds) if adapter is not None else []
    confidence = (
        "high"
        if root is not None and root.exists() and detected_paths
        else "medium"
        if detected
        else "none"
    )
    return DiscoveredTool(
        id=profile.name,
        name=profile.effective_display_name,
        root_path=root or home,
        detected=detected,
        confidence=confidence,
        config_paths=config_paths,
        resource_paths=resource_paths,
        mcp_config_paths=mcp_config_paths,
        supports_kinds=supports,
        tool_id=profile.effective_tool_id,
        environment_kind=profile.environment_kind,
        environment_name=profile.environment_name,
        display_name=profile.effective_display_name,
        instruction_path=instruction_path,
        memories_path=memories_path,
        memory_layout=memory_layout,
        resource_path_kinds=resource_path_kinds,
        warnings=warnings,
        memory_blocker=memory_blocker,
        memory_install_names=dict(profile.memory_install_names),
    )


def _profile_scan_available(
    profile: PlatformProfile,
    *,
    runtime_home: Path,
) -> tuple[bool, str]:
    if profile.home_dir and profile.home_dir != "~":
        if not is_cross_platform_absolute_path(profile.home_dir):
            return (
                False,
                "The configured runtime home_dir is not absolute; scanning is blocked.",
            )
        configured_home = _expand_home(profile.home_dir, home=runtime_home)
        unsafe_home = _unsafe_path_component_problem(
            configured_home,
            include_leaf=True,
        )
        if not unsafe_home and configured_home.is_dir():
            return True, ""
        return (
            False,
            (
                f"The configured {profile.environment_kind or 'runtime'} home is unsafe: "
                f"{unsafe_home}"
                if unsafe_home
                else f"The configured {profile.environment_kind or 'runtime'} home is "
                f"unavailable: {configured_home}"
            ),
        )
    configured_kind = profile.environment_kind.strip().lower()
    if not configured_kind:
        return True, ""
    current_kind, current_name = current_environment_identity()
    if configured_kind != current_kind:
        return (
            False,
            "The configured runtime identity differs from the current process; "
            "set an explicit accessible home_dir before scanning it.",
        )
    if configured_kind == "wsl" and (
        not profile.environment_name
        or not current_name
        or profile.environment_name.casefold() != current_name.casefold()
    ):
        return (
            False,
            "The configured WSL distro identity cannot be confirmed from the current process.",
        )
    return True, ""


def _unavailable_profile_tool(
    profile: PlatformProfile,
    *,
    home: Path,
    problem: str,
) -> DiscoveredTool:
    adapter = tool_adapter_by_id(profile.effective_tool_id)
    profile_home = _profile_home(profile, runtime_home=home)
    return DiscoveredTool(
        id=profile.name,
        name=profile.effective_display_name,
        root_path=profile_home,
        detected=False,
        confidence="none",
        supports_kinds=list(adapter.supports_kinds) if adapter is not None else [],
        tool_id=profile.effective_tool_id,
        environment_kind=profile.environment_kind,
        environment_name=profile.environment_name,
        display_name=profile.effective_display_name,
        warnings=[problem],
        memory_blocker=problem if profile.effective_tool_id == "claude-code" else "",
        memory_layout=profile.memory_layout,
        memory_install_names=dict(profile.memory_install_names),
    )


def _adapter_resource_kind(
    supported: tuple[ItemKind, ...],
    relative: str,
) -> ItemKind | None:
    name = Path(relative).name.casefold()
    preferred: ItemKind | None = (
        "skill"
        if name == "skills"
        else "rule"
        if name == "rules"
        else "prompt"
        if name in {"commands", "prompts"}
        else "plugin"
        if name == "plugins"
        else None
    )
    return preferred if preferred in supported else None


def _discover_tool_resources(
    tools: list[DiscoveredTool],
    *,
    registry_path_override: Path | None,
) -> list[DiscoveredResource]:
    resources: list[DiscoveredResource] = []
    seen: set[tuple[str, ItemKind, str]] = set()

    def add_from_path(
        *,
        tool: DiscoveredTool,
        path: Path,
        file_kind_hint: ItemKind | None = None,
    ) -> None:
        path_probe = probe_local_path(path)
        if (
            not path_probe.ready
            or path_probe.content_path is None
            or not path_probe.content_path.is_dir()
        ):
            return
        for resource in discover_resources(
            scope="directory",
            root_path=path,
            registry_path=registry_path_override,
            max_depth=32 if file_kind_hint == "rule" else 4,
            file_kind_hint=file_kind_hint,
        ):
            identity = (
                tool.id,
                resource.kind,
                str(resource.path.expanduser().absolute()).casefold(),
            )
            if resource.kind == "mcp" or identity in seen:
                continue
            _annotate_resource(resource, tool)
            seen.add(identity)
            resources.append(resource)

    def add_rules_from_path(*, tool: DiscoveredTool, path: Path) -> None:
        """Discover every Markdown rule without applying generic cache/depth filters.

        Claude Code documents ``~/.claude/rules/**/*.md`` as recursive.  The
        generic resource scanner deliberately excludes cache/build/hidden
        directories and caps recursion, so using it here would silently omit
        valid Claude rules.
        """
        root = path.expanduser().absolute()
        ancestor_problem = _unsafe_path_component_problem(root, include_leaf=False)
        root_probe = probe_local_path(root)
        if (
            ancestor_problem
            or
            root_probe.is_link
            or
            not root_probe.ready
            or root_probe.content_path is None
            or not root_probe.content_path.is_dir()
        ):
            if root_probe.health != "missing":
                blocked = _blocked_scoped_resource(
                    tool,
                    root,
                    kind="rule",
                    name_hint="claude-rules-"
                    + hashlib.sha256(
                        f"{tool.id}\0{root}".encode()
                    ).hexdigest()[:12],
                    problem=(
                        "Claude user rules root has a linked or unreadable ancestor: "
                        + ancestor_problem
                        if ancestor_problem
                        else root_probe.problem
                        or "Claude user rules root is linked or unreadable."
                    ),
                )
                _append_exact_resource(resources, seen, blocked, tool)
            tool.warnings.append(
                "Claude user rules root is linked or unreadable; rules below it were not followed."
            )
            return
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                children = sorted(current.iterdir(), key=lambda item: item.name.casefold())
            except OSError:
                tool.warnings.append(
                    f"Claude user rules directory cannot be read safely: {current}"
                )
                continue
            for child in reversed(children):
                logical = child.absolute()
                child_probe = probe_local_path(logical)
                if child_probe.is_link or (
                    child_probe.health not in {"ready", "missing"}
                ):
                    try:
                        unsafe_relative = logical.relative_to(root).as_posix()
                    except ValueError:
                        unsafe_relative = child.name
                    blocked = _blocked_scoped_resource(
                        tool,
                        logical,
                        kind="rule",
                        name_hint="claude-rule-"
                        + hashlib.sha256(unsafe_relative.encode("utf-8")).hexdigest()[:12],
                        problem=(
                            child_probe.problem
                            or "Linked or unreadable Claude rule paths are not followed."
                        ),
                    )
                    _append_exact_resource(resources, seen, blocked, tool)
                    continue
                if child_probe.ready and child_probe.content_path is not None and child_probe.content_path.is_dir():
                    stack.append(child)
                    continue
                if child.suffix.casefold() != ".md":
                    continue
                try:
                    relative = logical.relative_to(root)
                except ValueError:
                    continue
                relative_stem = relative.with_suffix("").as_posix()
                name_hint = (
                    relative_stem
                    if len(relative.parts) == 1
                    else "claude-rule-"
                    + hashlib.sha256(relative_stem.encode("utf-8")).hexdigest()[:12]
                )
                rule = discover_exact_resource(
                    logical,
                    tool=tool.id,
                    kind="rule",
                    name_hint=name_hint,
                )
                if rule is None:
                    continue
                if len(relative.parts) > 1:
                    rule.blockers.append(
                        "Nested Claude rule hierarchy cannot yet be restored losslessly; "
                        "choose an explicit portable layout before uploading this rule."
                    )
                    rule.status = "blocked"
                _append_exact_resource(resources, seen, rule, tool)

    for tool in tools:
        for path in tool.resource_paths:
            hint = tool.resource_path_kinds.get(str(path))
            if hint == "rule":
                add_rules_from_path(tool=tool, path=path)
            else:
                add_from_path(tool=tool, path=path, file_kind_hint=hint)
        if tool.instruction_path is not None:
            ancestor_problem = _unsafe_path_component_problem(
                tool.instruction_path,
                include_leaf=False,
            )
            instruction = (
                _blocked_scoped_resource(
                    tool,
                    tool.instruction_path,
                    kind="instruction",
                    name_hint=f"{tool.tool_id}-user-instructions",
                    problem=(
                        "Instruction path has a linked or unreadable ancestor and was not followed: "
                        + ancestor_problem
                    ),
                )
                if ancestor_problem
                else discover_exact_resource(
                    tool.instruction_path,
                    tool=tool.id,
                    kind="instruction",
                    name_hint=f"{tool.tool_id}-user-instructions",
                    warnings=_instruction_import_warnings(tool.instruction_path),
                )
                if _instruction_is_discoverable(
                    tool.instruction_path,
                    tool_id=tool.tool_id,
                )
                else None
            )
            if instruction is not None:
                _append_exact_resource(resources, seen, instruction, tool)
        if tool.tool_id != "claude-code" or tool.memories_path is None:
            continue
        if tool.memory_layout == "direct":
            memory = discover_exact_resource(
                tool.memories_path,
                tool=tool.id,
                kind="memory",
                name_hint=_portable_direct_memory_name(tool, tool.memories_path),
            )
            if memory is not None:
                _append_exact_resource(resources, seen, memory, tool)
            continue
        if tool.memory_layout != "projects" or not tool.memories_path.is_dir():
            continue
        try:
            projects = sorted(tool.memories_path.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            projects = []
        for project in projects:
            memory_path = project / "memory"
            name_hint = _project_memory_name(tool, project.name)
            project_probe = probe_local_path(project)
            if project_probe.is_link or not project_probe.ready:
                blocked = _blocked_project_memory_resource(
                    tool,
                    project,
                    memory_path,
                    project_probe,
                    name_hint=name_hint,
                )
                _append_exact_resource(resources, seen, blocked, tool)
                continue
            memory = discover_exact_resource(
                memory_path,
                tool=tool.id,
                kind="memory",
                name_hint=name_hint,
            )
            if memory is not None:
                memory.install_name_hint = project.name
                _append_exact_resource(resources, seen, memory, tool)

    return sorted(
        resources,
        key=lambda item: (item.tool, item.kind, item.name_hint, str(item.path)),
    )


def _discover_mcp_servers(
    tools: list[DiscoveredTool],
) -> list[DiscoveredMcpServer]:
    servers: list[DiscoveredMcpServer] = []
    seen: set[tuple[str, str, str]] = set()

    def add_from_path(*, tool: DiscoveredTool, path: Path) -> None:
        try:
            normalized_path = str(path.resolve()).casefold()
        except OSError:
            normalized_path = str(path.absolute()).casefold()
        for name, raw_config in _read_mcp_servers(path).items():
            if not isinstance(raw_config, dict):
                continue
            normalized_name = _slug(name)
            identity = (tool.id, normalized_path, normalized_name)
            if identity in seen:
                continue
            seen.add(identity)
            servers.append(
                DiscoveredMcpServer(
                    id=f"{tool.id}:{path}:{name}",
                    tool=tool.id,
                    name=normalized_name,
                    config_path=path,
                    config=dict(raw_config),
                    secret_keys=_secret_env_keys(raw_config),
                    tool_id=tool.tool_id,
                    environment_kind=tool.environment_kind,
                    environment_name=tool.environment_name,
                )
            )

    for tool in tools:
        for path in tool.mcp_config_paths:
            add_from_path(tool=tool, path=path)

    return sorted(servers, key=lambda item: (item.tool, item.name))


def _profile_root(profile: PlatformProfile, *, home: Path) -> Path | None:
    for value in (profile.settings_path, profile.instructions_path):
        if value:
            return _expand_home(value, home=home).parent
    adapter = tool_adapter_by_id(profile.effective_tool_id)
    if adapter is not None and adapter.discovery_root:
        return _expand_home(adapter.discovery_root, home=home)
    for value in (profile.skills_dir, profile.rules_dir, profile.prompts_dir):
        if value:
            return _expand_home(value, home=home).parent
    return None


def _profile_home(profile: PlatformProfile, *, runtime_home: Path) -> Path:
    if profile.home_dir and profile.home_dir != "~":
        return _expand_home(profile.home_dir, home=runtime_home)
    raw_settings = profile.settings_path.strip()
    if raw_settings and _is_cross_platform_absolute(raw_settings):
        settings = Path(raw_settings)
        if settings.name == "settings.json" and settings.parent.name == ".claude":
            return settings.parent.parent
    if profile.home_dir == "~":
        return runtime_home
    return runtime_home


def _effective_instruction_path(profile: PlatformProfile, *, home: Path) -> Path:
    return select_instruction_path(
        profile.effective_tool_id,
        _expand_home(profile.instructions_path, home=home),
    )


def _is_cross_platform_absolute(value: str) -> bool:
    return bool(
        Path(value).is_absolute()
        or re.match(r"^[A-Za-z]:[\\/]", value)
        or value.startswith("/")
        or value.startswith(("\\\\", "//"))
    )


def _unique_existing_paths(values: list[Path | None]) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        path = value.expanduser().absolute()
        key = str(path).casefold()
        if path.exists() and key not in seen:
            paths.append(path)
            seen.add(key)
    return paths


def _claude_auto_memory_override(
    profile: PlatformProfile,
    *,
    home: Path,
) -> tuple[Path | None, str, bool]:
    if not profile.settings_path:
        return None, "", True
    settings_path = _expand_home(profile.settings_path, home=home)
    settings_problem = _unsafe_path_component_problem(
        settings_path,
        include_leaf=True,
    )
    if settings_problem:
        return (
            None,
            "Claude settings has a linked or unreadable path component; automatic "
            f"memory migration is blocked: {settings_problem}",
            False,
        )
    if settings_path.is_symlink():
        return (
            None,
            "Claude settings is a symbolic link; automatic memory migration is blocked.",
            False,
        )
    if not settings_path.exists():
        return None, "", True
    if not settings_path.is_file():
        return (
            None,
            "Claude settings is not a regular file; automatic memory migration is blocked.",
            False,
        )
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return (
            None,
            "Claude settings could not be parsed safely; automatic memory migration is blocked.",
            False,
        )
    if not isinstance(raw, dict):
        return (
            None,
            "Claude settings root is not an object; automatic memory migration is blocked.",
            False,
        )
    if "autoMemoryDirectory" not in raw:
        return None, "", True
    value = str(raw.get("autoMemoryDirectory") or "").strip()
    if not value:
        return (
            None,
            "Claude autoMemoryDirectory is empty; automatic memory migration is blocked.",
            False,
        )
    candidate, path_problem = _claude_memory_override_path(
        value,
        profile=profile,
        home=home,
    )
    if candidate is None:
        return (
            None,
            path_problem,
            False,
        )
    return candidate, "", True


def _claude_memory_override_path(
    value: str,
    *,
    profile: PlatformProfile,
    home: Path,
) -> tuple[Path | None, str]:
    """Map a trusted absolute auto-memory path into the profile runtime.

    The only cross-runtime conversion with an unambiguous native target is a
    POSIX WSL path viewed from Windows when the distro identity is explicit.
    Other host/guest absolute path combinations fail closed instead of being
    interpreted relative to the CC Port process working directory.
    """
    if value.startswith(("~/", "~\\")):
        return (home / value[2:]).absolute(), ""
    if not _is_cross_platform_absolute(value):
        return (
            None,
            "Claude autoMemoryDirectory is not absolute; automatic memory migration is blocked.",
        )

    is_windows_absolute = bool(
        re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith(("\\\\", "//"))
    )
    is_posix_absolute = value.startswith("/") and not value.startswith("//")

    if _host_is_windows():
        wsl_unc = None
        if is_windows_absolute:
            normalized_windows = value.replace("/", "\\")
            wsl_unc = re.match(
                r"^\\\\(?:wsl\.localhost|wsl\$)\\([^\\]+)(?:\\|$)",
                normalized_windows,
                flags=re.IGNORECASE,
            )
        if is_posix_absolute:
            distro = profile.environment_name.strip()
            if profile.environment_kind != "wsl" or not distro:
                return (
                    None,
                    "A POSIX autoMemoryDirectory requires an explicit WSL distro identity "
                    "when CC Port runs on Windows; automatic memory migration is blocked.",
                )
            if not re.fullmatch(r"[A-Za-z0-9._-]+", distro):
                return (
                    None,
                    "The configured WSL distro identity is unsafe; automatic memory migration is blocked.",
                )
            relative = value.lstrip("/").replace("/", "\\")
            return Path(f"\\\\wsl.localhost\\{distro}\\{relative}"), ""
        if is_windows_absolute and profile.environment_kind == "wsl":
            distro = profile.environment_name.strip()
            if (
                wsl_unc is None
                or not distro
                or wsl_unc.group(1).casefold() != distro.casefold()
            ):
                return (
                    None,
                    "A Windows autoMemoryDirectory for a WSL profile must use the "
                    "matching named distro UNC root; automatic memory migration is blocked.",
                )
            return Path(value), ""
        if is_windows_absolute and wsl_unc is not None:
            return (
                None,
                "A WSL UNC autoMemoryDirectory cannot be used by a native Windows profile.",
            )
        if is_windows_absolute:
            return Path(value), ""
    else:
        if is_posix_absolute and profile.environment_kind != "windows":
            return Path(value).absolute(), ""

    return (
        None,
        "Claude autoMemoryDirectory uses an absolute path from another runtime; "
        "configure a path accessible to this environment before migrating memory.",
    )


def _host_is_windows() -> bool:
    return os.name == "nt"


def _append_exact_resource(
    resources: list[DiscoveredResource],
    seen: set[tuple[str, ItemKind, str]],
    resource: DiscoveredResource,
    tool: DiscoveredTool,
) -> None:
    identity = (
        tool.id,
        resource.kind,
        str(resource.path.expanduser().absolute()).casefold(),
    )
    if identity in seen:
        return
    _annotate_resource(resource, tool)
    seen.add(identity)
    resources.append(resource)


def _annotate_resource(resource: DiscoveredResource, tool: DiscoveredTool) -> None:
    resource.tool = tool.id
    resource.tool_id = tool.tool_id
    resource.environment_kind = tool.environment_kind
    resource.environment_name = tool.environment_name
    resource.display_name = tool.display_name


def _portable_memory_name(tool: DiscoveredTool, project_key: str) -> str:
    digest = hashlib.sha256(
        f"{tool.id}\0{project_key}".encode()
    ).hexdigest()[:12]
    return f"claude-memory-{digest}"


def _project_memory_name(tool: DiscoveredTool, project_slot: str) -> str:
    mapped_names = sorted(
        resource_name
        for resource_name, slot in tool.memory_install_names.items()
        if slot == project_slot
    )
    return (
        mapped_names[0]
        if len(mapped_names) == 1
        else _portable_memory_name(tool, project_slot)
    )


def _blocked_scoped_resource(
    tool: DiscoveredTool,
    path: Path,
    *,
    kind: ItemKind,
    name_hint: str,
    problem: str,
) -> DiscoveredResource:
    """Report an unsafe exact path without reading through its link target."""
    logical = path.expanduser().absolute()
    probe = probe_local_path(logical)
    try:
        path_stat = logical.lstat()
        size = path_stat.st_size
        mtime = path_stat.st_mtime
    except OSError:
        size = 0
        mtime = 0
    digest = hashlib.sha256(
        f"{tool.id}\0{kind}\0{logical}".encode()
    ).hexdigest()[:16]
    return DiscoveredResource(
        id=f"{tool.id}:{kind}:unsafe-{digest}",
        tool=tool.id,
        source="configured",
        kind=kind,
        name_hint=_slug(name_hint),
        path=logical,
        content_path=None,
        path_kind=probe.path_kind,
        link_health=probe.health,
        link_target=probe.raw_target,
        reparse_tag=probe.reparse_tag_hex,
        link_target_trusted=False,
        size=size,
        mtime=mtime,
        status="blocked",
        blockers=[problem],
    )


def _blocked_project_memory_resource(
    tool: DiscoveredTool,
    project: Path,
    memory_path: Path,
    probe: Any,
    *,
    name_hint: str,
) -> DiscoveredResource:
    """Represent an unsafe project-slot ancestor without dereferencing it."""
    try:
        stat = project.lstat()
        size = stat.st_size
        mtime = stat.st_mtime
    except OSError:
        size = 0
        mtime = 0
    digest = hashlib.sha256(
        f"{tool.id}\0{memory_path.absolute()}".encode()
    ).hexdigest()[:16]
    return DiscoveredResource(
        id=f"{tool.id}:memory:unsafe-{digest}",
        tool=tool.id,
        source="configured",
        kind="memory",
        name_hint=name_hint,
        path=memory_path.absolute(),
        content_path=None,
        path_kind=probe.path_kind,
        link_health=probe.health,
        link_target=probe.raw_target,
        reparse_tag=probe.reparse_tag_hex,
        link_target_trusted=False,
        size=size,
        mtime=mtime,
        status="blocked",
        blockers=[
            "Claude project slot is a link or cannot be read safely; its memory subtree was not followed."
        ],
        install_name_hint=project.name,
    )


def _portable_direct_memory_name(tool: DiscoveredTool, path: Path) -> str:
    normalized_path = os.path.normcase(str(path.absolute()))
    local_identity = f"{tool.id}\0{normalized_path}"
    digest = hashlib.sha256(local_identity.encode("utf-8")).hexdigest()[:12]
    return f"claude-memory-{digest}"


def _instruction_is_discoverable(path: Path, *, tool_id: str) -> bool:
    """Codex ignores empty instruction files; unsafe files remain observable."""
    if tool_id != "codex" or path.is_symlink() or not path.is_file():
        return True
    try:
        with path.open("r", encoding="utf-8") as handle:
            while chunk := handle.read(8192):
                if chunk.strip():
                    return True
        return False
    except (OSError, UnicodeError):
        return True


def _instruction_import_warnings(path: Path) -> list[str]:
    if not path.is_file() or path.is_symlink():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    in_fence = False
    import_pattern = re.compile(r"(?:^|[\s(])@(?:~[/\\]|\.?\.?[/\\]|[A-Za-z0-9_.-]+)")
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence and import_pattern.search(line):
            return [
                "Instruction imports are not followed or bundled; migrate referenced files separately."
            ]
    return []


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
        return home.absolute()
    if value.startswith(("~/", "~\\")):
        return (home / value[2:]).absolute()
    return Path(value).expanduser().absolute()


def _unsafe_path_component_problem(
    path: Path,
    *,
    include_leaf: bool,
) -> str:
    """Inspect existing path components without resolving through links."""
    logical = path.expanduser().absolute()
    components = list(reversed(logical.parents))
    if include_leaf:
        components.append(logical)
    for component in components:
        probe = probe_local_path(component)
        if probe.health == "missing":
            continue
        if probe.is_link or not probe.ready:
            return probe.problem or f"Unsafe path component: {component}"
    return ""


def _is_placeholder(value: str) -> bool:
    normalized = value.strip()
    return normalized.startswith("${") and normalized.endswith("}") and len(normalized) > 3
