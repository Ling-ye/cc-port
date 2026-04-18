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

    Falls back to `<cwd>/registry.yaml` if none is found, so a missing file
    can still be created in place.
    """
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        p = candidate / DEFAULT_REGISTRY_FILENAME
        if p.is_file():
            return p
    return cur / DEFAULT_REGISTRY_FILENAME


CURRENT_REGISTRY_VERSION = 3


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

    if "skills" in data and "items" not in data:
        data["items"] = data.pop("skills")

    return Registry.model_validate(data)


# Fields to omit from YAML output when empty/None
_OMIT_WHEN_EMPTY: set[str] = {
    "mcp_config", "last_checked", "reachable", "private",
    "version", "author", "tags", "category", "license",
}


def save_registry(registry: Registry, path: Path | None = None) -> Path:
    """Atomically write the registry to disk (always as current version)."""
    p = path or find_registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    payload = registry.model_dump(mode="json")
    payload["version"] = CURRENT_REGISTRY_VERSION
    for item in payload.get("items", []):
        for key in _OMIT_WHEN_EMPTY:
            val = item.get(key)
            if val is None or val == "" or val == []:
                item.pop(key, None)

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
