"""Load and persist the registry.yaml file."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

from .models import Registry

DEFAULT_REGISTRY_FILENAME = "registry.yaml"


def find_registry_path(start: Path | None = None) -> Path:
    """Walk upwards from `start` looking for registry.yaml.

    When `start` is omitted, the default registry lives in the configured
    private resource repository, not in the LPM tool repository.

    Falls back to `<cwd>/registry.yaml` if none is found, so a missing file
    can still be created in place.
    """
    if start is None:
        try:
            from .config import load_config

            return load_config().resources.local_path_value / DEFAULT_REGISTRY_FILENAME
        except Exception:
            pass

    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        p = candidate / DEFAULT_REGISTRY_FILENAME
        if p.is_file():
            return p
    return cur / DEFAULT_REGISTRY_FILENAME


CURRENT_REGISTRY_VERSION = 7


def _migrate_v1_to_v2(data: dict) -> dict:
    """Convert a v1 registry (with ``skills`` list) to v2 (with ``items`` list)."""
    items = []
    for entry in data.get("skills", []) or []:
        migrated = dict(entry)
        migrated.setdefault("kind", "skill")
        items.append(migrated)
    return {"version": 2, "items": items}


def _migrate_v2_to_v3(data: dict) -> dict:
    """v2 -> v3: new optional metadata fields; no structural change needed."""
    data = dict(data)
    data["version"] = 3
    return data


def _migrate_v3_to_v4(data: dict) -> dict:
    """v3 -> v4: local monorepo resources can carry a relative ``path``."""
    data = dict(data)
    data["version"] = 4
    return data


def _migrate_v4_to_v5(data: dict) -> dict:
    """v4 -> v5: items get explicit lifecycle tracking."""
    data = dict(data)
    items = data.get("items", []) or []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                item.setdefault("lifecycle", "active")
    data["version"] = 5
    return data


def _migrate_v5_to_v6(data: dict) -> dict:
    """v5 -> v6: identity becomes kind+name and install aliases may vary by platform."""
    data = dict(data)
    items = data.get("items", []) or []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                item.setdefault("kind", "skill")
                item.setdefault("platform_install_dirs", {})
    data["version"] = 6
    return data


def _migrate_v6_to_v7(data: dict) -> dict:
    """v6 -> v7: dual-track plugin metadata is opt-in for new entries."""
    data = dict(data)
    data["version"] = 7
    return data


def load_registry(path: Path | None = None) -> Registry:
    p = path or find_registry_path()
    if not p.is_file():
        return Registry(version=CURRENT_REGISTRY_VERSION)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Registry file {p} must contain a YAML mapping at the root.")

    version = data.get("version", 1)
    if version < 2:
        data = _migrate_v1_to_v2(data)
    if version < 3:
        data = _migrate_v2_to_v3(data)
    if version < 4:
        data = _migrate_v3_to_v4(data)
    if version < 5:
        data = _migrate_v4_to_v5(data)
    if version < 6:
        data = _migrate_v5_to_v6(data)
    if version < 7:
        data = _migrate_v6_to_v7(data)

    if "skills" in data and "items" not in data:
        data["items"] = data.pop("skills")

    return Registry.model_validate(data)


# Fields to omit from YAML output when empty/None
_OMIT_WHEN_EMPTY: set[str] = {
    "mcp_config", "last_checked", "reachable", "private",
    "version", "author", "tags", "category", "license", "path", "platforms",
    "removed_at", "removed_reason", "removed_effect", "platform_install_dirs", "plugin",
}


def save_registry(registry: Registry, path: Path | None = None) -> Path:
    """Atomically write the registry to disk (always as current version)."""
    p = path or find_registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    payload = registry.model_dump(mode="json")
    payload["version"] = CURRENT_REGISTRY_VERSION
    payload["items"] = sorted(
        payload.get("items", []),
        key=lambda item: (str(item.get("kind") or ""), str(item.get("name") or "")),
    )
    for item in payload.get("items", []):
        for key in _OMIT_WHEN_EMPTY:
            val = item.get(key)
            if val is None or val == "" or val == []:
                item.pop(key, None)
        plugin = item.get("plugin")
        if isinstance(plugin, dict):
            origin = plugin.get("origin")
            if isinstance(origin, dict):
                for key in list(origin):
                    if key != "type" and origin.get(key) in {None, ""}:
                        origin.pop(key, None)
            if not plugin.get("dependencies"):
                plugin.pop("dependencies", None)
            if not plugin.get("observed_version"):
                plugin.pop("observed_version", None)
            installations = plugin.get("installations", [])
            if isinstance(installations, list):
                for installation in installations:
                    if isinstance(installation, dict) and installation.get("project") is None:
                        installation.pop("project", None)

    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)

    fd, tmp_name = tempfile.mkstemp(prefix=".registry-", suffix=".yaml", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, p)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return p
