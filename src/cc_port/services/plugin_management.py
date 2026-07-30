"""Dual-track discovery and local project mapping for plugins."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - py310 fallback
    import tomli as tomllib

from ..core.config import (
    Config,
    PluginProjectConfig,
    default_config_path,
    load_raw_config,
    write_config,
)
from ..infrastructure import git_ops
from .publisher import _slug

PLUGIN_PLATFORMS = {"codex", "claude-code", "opencode"}
PLUGIN_SCOPES = {"user", "project", "local", "managed"}


@dataclass
class DiscoveredPlugin:
    id: str
    platform: str
    plugin_id: str
    track: str
    origin_type: str
    scope: str
    enabled: bool | None
    writable: bool
    path: Path | None = None
    state_path: Path | None = None
    marketplace: str = ""
    origin_source: str = ""
    package: str = ""
    repo: str = ""
    selector: str = ""
    selector_known: bool = True
    observed_version: str = ""
    project_id: str = ""
    project_repo: str = ""
    project_subdir: str = ""
    description: str = ""
    dependencies: dict[str, str] = field(default_factory=dict)
    complete: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def source_id(self) -> str:
        if self.origin_type == "marketplace":
            return f"{self.plugin_id}@{self.marketplace}"
        if self.origin_type == "npm":
            return self.package
        if self.origin_type == "git":
            return self.repo
        return self.origin_source or self.plugin_id

    @property
    def resource_name(self) -> str:
        return plugin_resource_name(self.platform, self.origin_type, self.source_id)


@dataclass(frozen=True)
class PluginProjectSummary:
    id: str
    path: Path
    repo: str
    subdir: str
    portable: bool
    exists: bool


def normalize_git_identity(value: str) -> str:
    """Normalize a Git remote into a credential-free portable project identity."""
    remote = value.strip().rstrip("/")
    if not remote:
        return ""
    if (
        remote.startswith(("/", "\\", "./", "../"))
        or re.match(r"^[A-Za-z]:[\\/]", remote)
        or remote.lower().startswith("file://")
    ):
        return ""
    ssh = re.match(r"^(?:[^@]+@)?([^:]+):(.+)$", remote)
    if ssh and "://" not in remote:
        host, path = ssh.groups()
        normalized = f"{host.lower()}/{path}"
    else:
        parsed = urlparse(remote)
        if parsed.scheme and parsed.hostname:
            normalized = f"{parsed.hostname.lower()}/{parsed.path.lstrip('/')}"
        else:
            normalized = remote.replace("\\", "/")
    return normalized.removesuffix(".git").rstrip("/")


def plugin_project_id(repo: str, subdir: str, *, local_path: str = "") -> str:
    portable = f"{normalize_git_identity(repo)}\0{subdir.strip('/')}"
    material = portable if normalize_git_identity(repo) else f"local\0{Path(local_path).resolve()}"
    return "project-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def inspect_plugin_project(path: Path | str, *, config: Config | None = None) -> PluginProjectConfig:
    cfg = config or load_raw_config()
    git_ops.configure_git_executable(cfg.git.executable)
    selected = Path(path).expanduser().resolve()
    if not selected.is_dir():
        raise ValueError(f"Plugin project path is not a directory: {selected}")
    root = git_ops.repository_root(selected)
    repo = normalize_git_identity(git_ops.current_remote_url(root) or "") if root else ""
    subdir = selected.relative_to(root).as_posix() if root and selected != root else ""
    return PluginProjectConfig(
        id=plugin_project_id(repo, subdir, local_path=str(selected)),
        path=str(selected),
        repo=repo,
        subdir=subdir,
    )


def list_plugin_projects(config: Config | None = None) -> list[PluginProjectSummary]:
    cfg = config or load_raw_config()
    return [
        PluginProjectSummary(
            id=item.id,
            path=item.path_value,
            repo=item.repo,
            subdir=item.subdir,
            portable=bool(item.repo),
            exists=item.path_value.is_dir(),
        )
        for item in cfg.plugin_projects
    ]


def add_plugin_project(
    path: Path | str,
    *,
    config_path: Path | None = None,
) -> PluginProjectSummary:
    cfg = load_raw_config(config_path)
    project = inspect_plugin_project(path, config=cfg)
    cfg.plugin_projects = [item for item in cfg.plugin_projects if item.id != project.id]
    cfg.plugin_projects.append(project)
    cfg.plugin_projects.sort(key=lambda item: (item.repo, item.subdir, item.path.lower()))
    write_config(cfg, config_path or cfg.source_path or default_config_path())
    return list_plugin_projects(cfg)[next(
        index for index, item in enumerate(cfg.plugin_projects) if item.id == project.id
    )]


def remove_plugin_project(
    project_id: str,
    *,
    config_path: Path | None = None,
) -> PluginProjectSummary:
    cfg = load_raw_config(config_path)
    before = list_plugin_projects(cfg)
    target = next((item for item in before if item.id == project_id), None)
    if target is None:
        raise ValueError(f"Unknown plugin project: {project_id}")
    cfg.plugin_projects = [item for item in cfg.plugin_projects if item.id != project_id]
    write_config(cfg, config_path or cfg.source_path or default_config_path())
    return target


def plugin_resource_name(platform: str, origin_type: str, source_id: str) -> str:
    raw = _slug(f"{platform}-{origin_type}-{source_id}") or "plugin"
    if len(raw) <= 64:
        return raw
    digest = hashlib.sha256(f"{platform}\0{origin_type}\0{source_id}".encode()).hexdigest()[:8]
    return f"{raw[:55].rstrip('-')}-{digest}"


def discover_plugins(
    config: Config,
    *,
    home: Path | None = None,
    scan_global: bool = True,
    project_ids: list[str] | None = None,
) -> list[DiscoveredPlugin]:
    effective_home = home or Path.home()
    selected = config.plugin_projects
    if project_ids is not None:
        allowed = set(project_ids)
        selected = [item for item in selected if item.id in allowed]

    found: list[DiscoveredPlugin] = []
    if scan_global:
        found.extend(_discover_codex(effective_home))
        found.extend(_discover_claude(effective_home))
        found.extend(_discover_opencode(effective_home))
        found.extend(_discover_configured_content_plugins(config, home=effective_home))
    for project in selected:
        root = project.path_value
        if not root.is_dir():
            continue
        found.extend(_discover_codex(effective_home, project=project))
        found.extend(_discover_claude(effective_home, project=project))
        found.extend(_discover_opencode(effective_home, project=project))
    return _dedupe_plugins(found)


def _discover_configured_content_plugins(
    config: Config,
    *,
    home: Path,
) -> list[DiscoveredPlugin]:
    manifest_by_platform = {
        "codex": ".codex-plugin/plugin.json",
        "claude-code": ".claude-plugin/plugin.json",
    }
    found: list[DiscoveredPlugin] = []
    for profile in config.platforms.enabled():
        manifest_rel = manifest_by_platform.get(profile.name)
        if manifest_rel is None or not profile.plugins_dir:
            continue
        plugins_root = _expand_home(profile.plugins_dir, home=home)
        found.extend(
            _content_directories(
                plugins_root,
                profile.name,
                "user",
                manifest_rel,
            )
        )
    return found


def _discover_codex(home: Path, project: PluginProjectConfig | None = None) -> list[DiscoveredPlugin]:
    codex_home = home / ".codex"
    config_path = (project.path_value / ".codex" / "config.toml") if project else codex_home / "config.toml"
    scope = "project" if project else "user"
    cache_root = codex_home / "plugins" / "cache"
    config = _read_toml(config_path)
    marketplaces = config.get("marketplaces", {}) if isinstance(config, dict) else {}
    plugin_settings = config.get("plugins", {}) if isinstance(config, dict) else {}
    out: list[DiscoveredPlugin] = []
    if isinstance(plugin_settings, dict):
        for qualified, raw in plugin_settings.items():
            plugin_id, marketplace = _split_qualified(str(qualified))
            if not plugin_id or not marketplace:
                continue
            settings = raw if isinstance(raw, dict) else {}
            version_path, version, description = _installed_cache_entry(
                cache_root / marketplace / plugin_id,
                ".codex-plugin/plugin.json",
            )
            marketplace_data = marketplaces.get(marketplace, {}) if isinstance(marketplaces, dict) else {}
            source = str(marketplace_data.get("source", "") or "") if isinstance(marketplace_data, dict) else ""
            out.append(
                _plugin(
                    platform="codex",
                    plugin_id=plugin_id,
                    track="reference",
                    origin_type="marketplace",
                    scope="managed" if bool(settings.get("managed", False)) else scope,
                    enabled=bool(settings.get("enabled", True)),
                    writable=not bool(settings.get("managed", False)),
                    path=version_path,
                    state_path=config_path,
                    marketplace=marketplace,
                    origin_source=_portable_marketplace_source(source, marketplace),
                    selector_known=False,
                    observed_version=version,
                    description=description,
                    project=project,
                )
            )

    if project is None:
        out.extend(_content_directories(codex_home / "plugins", "codex", "user", ".codex-plugin/plugin.json"))
    else:
        out.extend(_project_codex_content(project))
        out.extend(
            _content_directories(
                project.path_value.parent,
                "codex",
                "project",
                ".codex-plugin/plugin.json",
                project,
                only=project.path_value,
            )
        )
    return out


def _discover_claude(home: Path, project: PluginProjectConfig | None = None) -> list[DiscoveredPlugin]:
    claude_home = home / ".claude"
    scope = "user" if project is None else "project"
    project_root = project.path_value if project else None
    settings_paths: list[tuple[Path, str]] = []
    if project is None:
        settings_paths.append((claude_home / "settings.json", "user"))
    else:
        settings_paths.extend(
            [
                (project_root / ".claude" / "settings.json", "project"),
                (project_root / ".claude" / "settings.local.json", "local"),
            ]
        )
    cache_root = claude_home / "plugins" / "cache"
    known = _read_json(claude_home / "plugins" / "known_marketplaces.json")
    out: list[DiscoveredPlugin] = []

    cli_items = _claude_cli_plugins(project_root)
    for item in cli_items:
        plugin_id, marketplace = _split_qualified(str(item.get("id") or item.get("name") or ""))
        if not plugin_id:
            continue
        marketplace = str(item.get("marketplace") or marketplace or "")
        item_scope = str(item.get("scope") or scope)
        if item_scope not in PLUGIN_SCOPES:
            item_scope = scope
        state_path = next(
            (path for path, configured_scope in settings_paths if configured_scope == item_scope),
            None,
        )
        source = _marketplace_source(known, marketplace)
        version_path, cached_version, description = _installed_cache_entry(
            cache_root / marketplace / plugin_id,
            ".claude-plugin/plugin.json",
        ) if marketplace else (None, "", "")
        out.append(
            _plugin(
                platform="claude-code",
                plugin_id=plugin_id,
                track="reference",
                origin_type="marketplace",
                scope=item_scope,
                enabled=_optional_bool(item.get("enabled"), default=True),
                writable=item_scope != "managed",
                path=version_path,
                state_path=state_path,
                marketplace=marketplace,
                origin_source=_portable_marketplace_source(
                    str(item.get("source") or source or ""),
                    marketplace,
                ),
                selector=str(item.get("selector") or ""),
                selector_known="selector" in item,
                observed_version=str(item.get("version") or cached_version),
                description=str(item.get("description") or description),
                project=project if item_scope in {"project", "local"} else None,
            )
        )

    for settings_path, item_scope in settings_paths:
        settings = _read_json(settings_path)
        enabled_plugins = settings.get("enabledPlugins", {}) if isinstance(settings, dict) else {}
        if not isinstance(enabled_plugins, dict):
            continue
        for qualified, enabled in enabled_plugins.items():
            plugin_id, marketplace = _split_qualified(str(qualified))
            if not plugin_id or not marketplace:
                continue
            version_path, version, description = _installed_cache_entry(
                cache_root / marketplace / plugin_id,
                ".claude-plugin/plugin.json",
            )
            out.append(
                _plugin(
                    platform="claude-code",
                    plugin_id=plugin_id,
                    track="reference",
                    origin_type="marketplace",
                    scope=item_scope,
                    enabled=bool(enabled),
                    writable=True,
                    path=version_path,
                    state_path=settings_path,
                    marketplace=marketplace,
                    origin_source=_portable_marketplace_source(
                        _marketplace_source(known, marketplace),
                        marketplace,
                    ),
                    selector_known=False,
                    observed_version=version,
                    description=description,
                    project=project if item_scope in {"project", "local"} else None,
                )
            )

    if not out and cache_root.is_dir():
        out.extend(_restricted_claude_cache(cache_root, known))

    if project_root is not None:
        out.extend(
            _content_directories(
                project_root.parent,
                "claude-code",
                scope,
                ".claude-plugin/plugin.json",
                project,
                only=project_root,
            )
        )
        out.extend(
            _content_directories(
                project_root / ".claude" / "plugins",
                "claude-code",
                scope,
                ".claude-plugin/plugin.json",
                project,
            )
        )
    else:
        out.extend(
            _content_directories(
                claude_home / "plugins",
                "claude-code",
                scope,
                ".claude-plugin/plugin.json",
            )
        )
    return out


def _discover_opencode(home: Path, project: PluginProjectConfig | None = None) -> list[DiscoveredPlugin]:
    base = home / ".config" / "opencode" if project is None else project.path_value
    config_path = base / "opencode.json"
    plugins_dir = base / "plugins" if project is None else base / ".opencode" / "plugins"
    scope = "user" if project is None else "project"
    config = _read_json(config_path)
    declared = config.get("plugin", []) if isinstance(config, dict) else []
    out: list[DiscoveredPlugin] = []
    if isinstance(declared, list):
        for raw in declared:
            package, selector = _split_npm_selector(str(raw))
            if not package:
                continue
            out.append(
                _plugin(
                    platform="opencode",
                    plugin_id=package,
                    track="reference",
                    origin_type="npm",
                    scope=scope,
                    enabled=True,
                    writable=True,
                    path=config_path,
                    state_path=config_path,
                    package=package,
                    selector=selector,
                    selector_known=True,
                    project=project,
                )
            )
    if plugins_dir.is_dir():
        for path in sorted(plugins_dir.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_file() or path.suffix.lower() not in {".js", ".ts"} or path.is_symlink():
                continue
            out.append(
                _plugin(
                    platform="opencode",
                    plugin_id=path.stem,
                    track="content",
                    origin_type="local",
                    scope=scope,
                    enabled=True,
                    writable=True,
                    path=path,
                    origin_source=path.name,
                    project=project,
                )
            )
    return out


def _project_codex_content(project: PluginProjectConfig) -> list[DiscoveredPlugin]:
    root = project.path_value
    marketplace_path = root / ".agents" / "plugins" / "marketplace.json"
    marketplace = _read_json(marketplace_path)
    out: list[DiscoveredPlugin] = []
    entries = marketplace.get("plugins", []) if isinstance(marketplace, dict) else []
    if not isinstance(entries, list):
        return out
    for item in entries:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        local_path = ""
        if isinstance(source, str):
            local_path = source
        elif isinstance(source, dict) and source.get("source") == "local":
            local_path = str(source.get("path") or "")
        if not local_path.startswith("./"):
            continue
        candidate = (root / local_path).resolve()
        if root.resolve() not in candidate.parents and candidate != root.resolve():
            continue
        if not (candidate / ".codex-plugin" / "plugin.json").is_file():
            continue
        out.extend(_content_directories(candidate.parent, "codex", "project", ".codex-plugin/plugin.json", project, only=candidate))
    return out


def _content_directories(
    root: Path,
    platform: str,
    scope: str,
    manifest_rel: str,
    project: PluginProjectConfig | None = None,
    *,
    only: Path | None = None,
) -> list[DiscoveredPlugin]:
    if not root.is_dir():
        return []
    children = [only] if only else sorted(root.iterdir(), key=lambda item: item.name.lower())
    out: list[DiscoveredPlugin] = []
    for path in children:
        if path is None or not path.is_dir() or path.is_symlink():
            continue
        if path.name.lower() == "cache" or path.name.startswith("."):
            continue
        manifest = path / Path(manifest_rel)
        if not manifest.is_file() or manifest.is_symlink():
            continue
        metadata = _read_json(manifest)
        plugin_id = str(metadata.get("name") or path.name)
        out.append(
            _plugin(
                platform=platform,
                plugin_id=plugin_id,
                track="content",
                origin_type="local",
                scope=scope,
                enabled=True,
                writable=True,
                path=path,
                origin_source=path.name,
                observed_version=str(metadata.get("version") or ""),
                description=str(metadata.get("description") or ""),
                project=project,
            )
        )
    return out


def _restricted_claude_cache(root: Path, known: dict[str, Any]) -> list[DiscoveredPlugin]:
    out: list[DiscoveredPlugin] = []
    for marketplace_dir in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not marketplace_dir.is_dir():
            continue
        for plugin_dir in sorted(marketplace_dir.iterdir(), key=lambda item: item.name.lower()):
            if not plugin_dir.is_dir():
                continue
            path, version, description = _installed_cache_entry(plugin_dir, ".claude-plugin/plugin.json")
            if path is None:
                continue
            out.append(
                _plugin(
                    platform="claude-code",
                    plugin_id=plugin_dir.name,
                    track="reference",
                    origin_type="marketplace",
                    scope="user",
                    enabled=None,
                    writable=False,
                    path=path,
                    marketplace=marketplace_dir.name,
                    origin_source=_portable_marketplace_source(
                        _marketplace_source(known, marketplace_dir.name),
                        marketplace_dir.name,
                    ),
                    observed_version=version,
                    selector_known=False,
                    description=description,
                    complete=False,
                    warnings=["Claude CLI/settings did not provide enablement; cache is observation-only."],
                )
            )
    return out


def _claude_cli_plugins(cwd: Path | None) -> list[dict[str, Any]]:
    executable = shutil.which("claude")
    if not executable:
        return []
    try:
        result = subprocess.run(
            [executable, "plugin", "list", "--json"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        values = payload.get("plugins", payload.get("installed", []))
        return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []
    return []


def _plugin(**kwargs: Any) -> DiscoveredPlugin:
    project = kwargs.pop("project", None)
    project_id = project.id if project else ""
    project_repo = project.repo if project else ""
    project_subdir = project.subdir if project else ""
    if project_repo and kwargs.get("origin_type") == "local" and kwargs.get("path") is not None:
        try:
            relative_path = Path(kwargs["path"]).resolve().relative_to(project.path_value.resolve())
        except (OSError, ValueError):
            relative_path = Path(str(kwargs.get("origin_source") or kwargs.get("plugin_id") or "plugin"))
        repo_path = Path(project_subdir) / relative_path if project_subdir else relative_path
        kwargs["origin_source"] = f"{project_repo}#{repo_path.as_posix()}"
    identity = "\0".join(
        [
            str(kwargs.get("platform", "")),
            str(kwargs.get("origin_type", "")),
            str(kwargs.get("marketplace") or kwargs.get("package") or kwargs.get("repo") or kwargs.get("origin_source") or ""),
            str(kwargs.get("plugin_id", "")),
            str(kwargs.get("scope", "")),
            project_id,
        ]
    )
    return DiscoveredPlugin(
        id=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        project_id=project_id,
        project_repo=project_repo,
        project_subdir=project_subdir,
        **kwargs,
    )


def _dedupe_plugins(items: list[DiscoveredPlugin]) -> list[DiscoveredPlugin]:
    by_id: dict[str, DiscoveredPlugin] = {}
    for item in items:
        existing = by_id.get(item.id)
        if existing is None or (not existing.complete and item.complete):
            by_id[item.id] = item
    return sorted(
        by_id.values(),
        key=lambda item: (item.platform, item.resource_name, item.scope, item.project_id),
    )


def _installed_cache_entry(root: Path, manifest_rel: str) -> tuple[Path | None, str, str]:
    if not root.is_dir():
        return None, "", ""
    candidates = [path for path in root.iterdir() if path.is_dir()]
    candidates = [path for path in candidates if (path / Path(manifest_rel)).is_file()]
    if not candidates:
        return None, "", ""
    selected = max(candidates, key=lambda path: path.stat().st_mtime)
    metadata = _read_json(selected / Path(manifest_rel))
    return (
        selected,
        str(metadata.get("version") or selected.name),
        str(metadata.get("description") or ""),
    )


def _marketplace_source(known: dict[str, Any], marketplace: str) -> str:
    raw = known.get(marketplace, {}) if isinstance(known, dict) else {}
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, dict):
        return ""
    source = raw.get("source", raw)
    if isinstance(source, str):
        return source
    if isinstance(source, dict):
        return str(source.get("repo") or source.get("url") or source.get("path") or "")
    return ""


def _portable_marketplace_source(value: str, fallback: str) -> str:
    text = value.strip()
    if not text:
        return fallback
    if text.startswith(("/", "\\", "./", "../")) or re.match(r"^[A-Za-z]:[\\/]", text):
        return fallback
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https", "ssh", "git"} and parsed.hostname:
        return normalize_git_identity(text)
    if re.match(r"^(?:[^@]+@)?[^:]+:.+$", text) and "://" not in text:
        return normalize_git_identity(text)
    if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", text):
        return text.removesuffix(".git")
    return fallback


def _split_qualified(value: str) -> tuple[str, str]:
    plugin_id, separator, marketplace = value.rpartition("@")
    return (plugin_id, marketplace) if separator and plugin_id and marketplace else (value, "")


def _split_npm_selector(value: str) -> tuple[str, str]:
    text = value.strip()
    if not text:
        return "", ""
    if text.startswith("@"):
        slash = text.find("/")
        selector_at = text.find("@", slash + 1) if slash >= 0 else -1
    else:
        selector_at = text.rfind("@")
    if selector_at > 0:
        return text[:selector_at], text[selector_at + 1 :]
    return text, ""


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _expand_home(value: str, *, home: Path) -> Path:
    if value == "~":
        return home
    if value.startswith(("~/", "~\\")):
        return (home / value[2:]).resolve()
    return Path(value).expanduser().resolve()


def _optional_bool(value: object, *, default: bool) -> bool:
    return value if isinstance(value, bool) else default
