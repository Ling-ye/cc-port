"""Manifest-aware resource copy and install planning helpers."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..core.agent_providers import AgentDetection, detect_agents
from ..core.models import ItemKind, RegistryItem
from ..core.platforms import PlatformProfile
from ..core.resource_files import is_resource_path_excluded, resource_copy_ignore
from ..core.tool_adapters import tool_adapter_by_id

MANIFEST_FILENAMES = ("lpm.resource.json", "lpm-resource.json")
RESOURCE_BUCKETS = ("skills", "agents", "commands", "hooks", "mcp", "rules", "prompts", "plugins")


@dataclass(frozen=True)
class ResourceManifest:
    path: Path | None = None
    buckets: dict[str, list[str]] = field(default_factory=dict)

    @property
    def has_entries(self) -> bool:
        return any(paths for paths in self.buckets.values())


@dataclass(frozen=True)
class InstallPlanTarget:
    platform: str
    kind: ItemKind
    install_mechanism: str
    path: Path
    auto_install: bool = True


@dataclass(frozen=True)
class InstallPlan:
    name: str
    kind: ItemKind
    source_path: Path
    manifest_path: Path | None
    files: list[Path]
    targets: list[InstallPlanTarget]
    warnings: list[str] = field(default_factory=list)
    detected_agents: list[AgentDetection] = field(default_factory=list)


def load_resource_manifest(root: Path) -> ResourceManifest:
    """Read an optional LPM resource manifest from a file or directory."""
    manifest_path: Path | None = None
    if root.is_file() and root.name in MANIFEST_FILENAMES:
        manifest_path = root
    elif root.is_dir():
        manifest_path = next((root / name for name in MANIFEST_FILENAMES if (root / name).is_file()), None)
    if manifest_path is None:
        return ResourceManifest()
    if manifest_path.is_symlink():
        raise ValueError(f"{manifest_path}: symbolic-link manifests are not allowed.")

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{manifest_path}: invalid JSON manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{manifest_path}: manifest root must be an object.")

    buckets: dict[str, list[str]] = {}
    for key in RESOURCE_BUCKETS:
        value = raw.get(key, [])
        if isinstance(value, str):
            paths = [value]
        elif isinstance(value, list):
            paths = [str(item) for item in value if str(item).strip()]
        else:
            raise ValueError(f"{manifest_path}: {key!r} must be a string or list of strings.")
        buckets[key] = [_normalize_manifest_path(item, manifest_path.parent) for item in paths]
    return ResourceManifest(path=manifest_path, buckets=buckets)


def copy_resource_tree(src: Path, dest: Path, *, manifest: ResourceManifest | None = None) -> None:
    """Copy a resource while excluding known redundant files."""
    src = src.expanduser().resolve()
    if dest.exists():
        _remove_path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    effective_manifest = manifest or load_resource_manifest(src)
    if src.is_file():
        if _is_excluded_path(src):
            raise ValueError(f"Resource file is excluded by policy: {src}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return
    if effective_manifest.has_entries:
        dest.mkdir(parents=True, exist_ok=True)
        for rel in _manifest_paths_for_copy(effective_manifest):
            source_candidate = src / rel
            if _is_excluded_path(source_candidate, root=src):
                continue
            source_item = source_candidate.resolve()
            _assert_inside(src, source_item)
            if not source_item.exists():
                continue
            target_item = dest / rel
            if source_item.is_dir():
                shutil.copytree(source_item, target_item, ignore=resource_copy_ignore)
            else:
                target_item.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_item, target_item)
        if effective_manifest.path and effective_manifest.path.parent.resolve() == src:
            shutil.copy2(effective_manifest.path, dest / effective_manifest.path.name)
        return

    shutil.copytree(src, dest, ignore=resource_copy_ignore)


def plan_install(
    entry: RegistryItem,
    source_path: Path,
    platforms: list[PlatformProfile],
    *,
    platform_filter: str | None = None,
    home: Path | None = None,
    path: str | None = None,
) -> InstallPlan:
    manifest = load_resource_manifest(source_path)
    files = list_resource_files(source_path, manifest=manifest)
    detected = detect_agents(home=home, path=path)
    targets: list[InstallPlanTarget] = []
    warnings: list[str] = []

    enabled = [
        platform
        for platform in platforms
        if platform.enabled and entry.supports_platform(platform.name)
    ]
    if platform_filter:
        enabled = [p for p in enabled if p.name == platform_filter]
        if not entry.supports_platform(platform_filter):
            warnings.append(
                f"Resource is not allowed on platform {platform_filter!r}."
            )

    detections_by_id = {item.provider.id: item for item in detected}
    for platform in enabled:
        target_path = platform.resolve_install_path(entry.kind, entry.install_target_name())
        if target_path is None:
            continue
        detection = detections_by_id.get(platform.name)
        auto_install = detection.auto_install if detection and detection.detected else True
        targets.append(
            InstallPlanTarget(
                platform=platform.name,
                kind=entry.kind,
                install_mechanism=_platform_install_mechanism(platform.name, entry.kind),
                path=target_path,
                auto_install=auto_install,
            )
        )

    if entry.kind == "plugin" and not targets:
        warnings.append("No enabled platform has a plugin target path.")
    if manifest.path is not None and not manifest.has_entries:
        warnings.append(f"Manifest {manifest.path} did not declare installable entries.")

    return InstallPlan(
        name=entry.name,
        kind=entry.kind,
        source_path=source_path,
        manifest_path=manifest.path,
        files=files,
        targets=targets,
        warnings=warnings,
        detected_agents=detected,
    )


def list_resource_files(src: Path, *, manifest: ResourceManifest | None = None) -> list[Path]:
    src = src.expanduser().resolve()
    effective_manifest = manifest or load_resource_manifest(src)
    if src.is_file():
        return [] if _is_excluded_path(src) else [src]
    if not src.exists():
        return []
    if effective_manifest.has_entries:
        files: list[Path] = []
        for rel in _manifest_paths_for_copy(effective_manifest):
            item = (src / rel).resolve()
            _assert_inside(src, item)
            if item.is_file() and not _is_excluded_path(item, root=src):
                files.append(item)
            elif item.is_dir():
                files.extend(
                    path
                    for path in item.rglob("*")
                    if path.is_file() and not _is_excluded_path(path, root=src)
                )
        return sorted(set(files))
    return sorted(
        path
        for path in src.rglob("*")
        if path.is_file() and not _is_excluded_path(path, root=src)
    )


def _platform_install_mechanism(platform_name: str, kind: ItemKind) -> str:
    adapter = tool_adapter_by_id(platform_name)
    if adapter is not None:
        return adapter.install_mechanism(kind)
    if kind == "mcp":
        return "json_mcp_servers_patch"
    if kind == "plugin":
        return "copy_plugin_dir"
    return "copy_directory"


def _manifest_paths_for_copy(manifest: ResourceManifest) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for paths in manifest.buckets.values():
        for item in paths:
            path = Path(item)
            key = path.as_posix().lower()
            if key not in seen:
                out.append(path)
                seen.add(key)
    return out


def _normalize_manifest_path(value: str, manifest_root: Path) -> str:
    text = value.strip().replace("\\", "/").strip("/")
    if not text or ".." in Path(text).parts or Path(text).is_absolute():
        raise ValueError(f"{manifest_root}: invalid manifest path {value!r}.")
    return text


def _is_excluded_path(path: Path, *, root: Path | None = None) -> bool:
    if path.is_symlink():
        return True
    candidate = path
    if root is not None:
        try:
            candidate = path.relative_to(root)
        except ValueError:
            return True
    return is_resource_path_excluded(candidate)


def _assert_inside(root: Path, target: Path) -> None:
    if target != root and root not in target.parents:
        raise ValueError(f"Refusing to copy path outside resource root: {target}")


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
