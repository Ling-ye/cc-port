"""Load and persist the tool-neutral registry v1 and CC Port overlay."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import frontmatter
import yaml

from .models import CcPortSettings, Registry
from .secrets import sanitize_mcp_config_for_storage

DEFAULT_REGISTRY_FILENAME = "registry.yaml"
DEFAULT_CC_PORT_FILENAME = "cc-port.yaml"
CURRENT_REGISTRY_VERSION = 1


class RegistryFormatError(ValueError):
    """Raised when registry.yaml is syntactically valid but not registry v1."""


class UnsupportedRegistryVersionError(RegistryFormatError):
    """Raised when a legacy or future registry version is encountered."""

    def __init__(self, version: object) -> None:
        self.version = version
        super().__init__(f"Unsupported registry version: {version!r}; expected version 1.")


def find_registry_path(start: Path | None = None) -> Path:
    """Walk upwards from *start* looking for registry.yaml."""
    if start is None:
        try:
            from .config import load_config

            return load_config().resources.local_path_value / DEFAULT_REGISTRY_FILENAME
        except Exception:
            pass

    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        path = candidate / DEFAULT_REGISTRY_FILENAME
        if path.is_file():
            return path
    return cur / DEFAULT_REGISTRY_FILENAME


def parse_registry_data(data: Any) -> Registry:
    """Validate already-parsed YAML as canonical registry v1."""
    if not isinstance(data, dict):
        raise RegistryFormatError("registry.yaml must contain a YAML mapping at the root.")
    version = data.get("version")
    if version != CURRENT_REGISTRY_VERSION:
        raise UnsupportedRegistryVersionError(version)
    if "resources" not in data or not isinstance(data.get("resources"), list):
        raise RegistryFormatError("registry.yaml version 1 requires a resources list.")
    unknown = set(data) - {"version", "resources"}
    if unknown:
        raise RegistryFormatError(
            "registry.yaml contains unsupported top-level fields: "
            + ", ".join(sorted(str(item) for item in unknown))
        )
    return Registry.model_validate(data)


def load_registry(path: Path | None = None) -> Registry:
    registry_path = path or find_registry_path()
    if not registry_path.is_file():
        return Registry()
    with registry_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    registry = parse_registry_data(data)
    try:
        registry.cc_port = load_cc_port_settings(
            registry_path.parent / DEFAULT_CC_PORT_FILENAME
        )
    except (OSError, UnicodeError, yaml.YAMLError, ValueError):
        registry.cc_port = CcPortSettings()
    registry.reset_resolved()
    resolve_registry_content(registry, registry_path.parent)
    return registry


def resolve_registry_content(registry: Registry, root: Path) -> Registry:
    """Populate transient ResolvedResource fields from safe current content."""
    for entry in registry.items:
        if not entry.path or entry.kind not in {"skill", "mcp", "rule", "prompt", "plugin"}:
            continue
        content = _safe_content_path(root, entry.path)
        if content is None or not content.exists():
            continue
        if entry.kind == "mcp":
            entry.mcp_config = _read_mcp_content(content)
            continue
        metadata = _read_content_metadata(content, entry.kind)
        for field_name in ("description", "version", "author", "license"):
            value = metadata.get(field_name)
            if value not in (None, ""):
                setattr(entry, field_name, str(value))
    return registry


def _safe_content_path(root: Path, relative: str) -> Path | None:
    root = root.absolute()
    current = root
    for part in relative.split("/"):
        if part in {"", ".", ".."}:
            return None
        current /= part
        if current.is_symlink():
            return None
    try:
        resolved_root = root.resolve(strict=False)
        resolved = current.resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    if resolved != resolved_root and resolved_root not in resolved.parents:
        return None
    return current


def _read_mcp_content(path: Path) -> dict[str, Any] | None:
    candidates = [path] if path.is_file() else [
        path / "mcp.json",
        path / "mcp.yaml",
        path / "mcp.yml",
    ]
    for candidate in candidates:
        if not candidate.is_file() or candidate.is_symlink():
            continue
        try:
            payload = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("mcp_config"), dict):
            payload = payload["mcp_config"]
        if isinstance(payload, dict) and isinstance(payload.get("mcpServers"), dict):
            servers = payload["mcpServers"]
            if len(servers) == 1:
                payload = next(iter(servers.values()))
        if not isinstance(payload, dict):
            continue
        sanitized = sanitize_mcp_config_for_storage(payload)
        if sanitized:
            return sanitized
    return None


def _read_content_metadata(path: Path, kind: str) -> dict[str, Any]:
    if kind == "plugin":
        candidates = [path] if path.is_file() else [
            path / "package.json",
            path / ".codex-plugin" / "plugin.json",
            path / ".claude-plugin" / "plugin.json",
            path / "plugin.json",
        ]
        for candidate in candidates:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            author = payload.get("author")
            license_value = payload.get("license")
            return {
                "description": payload.get("description"),
                "version": payload.get("version"),
                "author": author.get("name") if isinstance(author, dict) else author,
                "license": (
                    license_value.get("type")
                    if isinstance(license_value, dict)
                    else license_value
                ),
            }
        return {}
    candidates = [path] if path.is_file() else []
    if kind == "skill":
        candidates = [path / "SKILL.md"]
    elif kind in {"rule", "prompt"} and path.is_dir():
        candidates = sorted(
            item
            for item in path.rglob("*")
            if item.is_file() and item.suffix.lower() in {".md", ".mdc"}
        )
    for candidate in candidates:
        if not candidate.is_file() or candidate.is_symlink():
            continue
        try:
            post = frontmatter.load(candidate)
        except Exception:
            continue
        return {
            "description": post.get("description"),
            "version": post.get("version"),
            "author": post.get("author"),
            "license": post.get("license"),
        }
    return {}


def load_cc_port_settings(path: Path) -> CcPortSettings:
    if not path.is_file():
        return CcPortSettings()
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"CC Port settings file {path} must contain a YAML mapping.")
    return CcPortSettings.model_validate(data)


def canonical_registry_payload(registry: Registry) -> dict[str, Any]:
    resources: list[dict[str, Any]] = []
    for resource in sorted(registry.resources, key=lambda item: (item.kind, item.name)):
        item: dict[str, Any] = {"kind": resource.kind, "name": resource.name}
        if resource.path:
            item["path"] = resource.path
        elif resource.source is not None:
            source: dict[str, Any] = {
                "type": resource.source.type,
                "locator": resource.source.locator,
            }
            if resource.source.revision:
                source["revision"] = resource.source.revision
            if resource.source.subpath:
                source["subpath"] = resource.source.subpath
            item["source"] = source
        for key, value in sorted((resource.model_extra or {}).items()):
            item[key] = _canonical_extra(value)
        resources.append(item)
    return {"version": CURRENT_REGISTRY_VERSION, "resources": resources}


def _canonical_extra(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonical_extra(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        return [_canonical_extra(item) for item in value]
    return value


def canonical_registry_text(registry: Registry) -> str:
    return yaml.safe_dump(
        canonical_registry_payload(registry),
        sort_keys=False,
        allow_unicode=True,
    )


def save_registry(
    registry: Registry,
    path: Path | None = None,
    *,
    save_cc_port_overlay: bool = True,
) -> Path:
    """Atomically write canonical registry v1 and, when present, cc-port.yaml."""
    registry_path = path or find_registry_path()
    _atomic_write_text(registry_path, canonical_registry_text(registry))
    if save_cc_port_overlay and registry.cc_port.resources:
        settings_payload = registry.cc_port.model_dump(mode="json", exclude_none=True)
        settings_payload["resources"] = {
            key: settings_payload["resources"][key]
            for key in sorted(settings_payload.get("resources", {}))
        }
        settings_text = yaml.safe_dump(
            settings_payload,
            sort_keys=False,
            allow_unicode=True,
        )
        _atomic_write_text(registry_path.parent / DEFAULT_CC_PORT_FILENAME, settings_text)
    return registry_path


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}-",
        suffix=path.suffix,
        dir=str(path.parent),
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
