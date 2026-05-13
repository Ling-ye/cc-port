"""Structured JSON API used by the desktop application.

The CLI is optimized for humans and Rich tables.  This module exposes the
same core operations as stable JSON so desktop shells do not need to parse
terminal output.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from . import __version__, publisher
from .config import CONFIG_ENV_VAR, Config, default_config_path, load_config, write_config
from .installer import check_all, status_all, sync_all, uninstall_one
from .local_resources import import_local_resource
from .models import RegistryItem
from .publisher import remove_skill
from .registry import find_registry_path, load_registry
from .resource_detection import detect_local_resource_type, detect_remote_resource
from .resource_repo import (
    init_resource_repo,
    inspect_resource_repo,
    pull_resource_repo,
    push_resource_repo,
    use_resource_repo,
)

JsonDict = dict[str, Any]
Handler = Callable[[JsonDict], Any]


def run_action(action: str, payload: JsonDict | None = None) -> JsonDict:
    """Run one desktop API action and return a JSON-serializable envelope."""
    data = payload or {}
    try:
        handler = ACTIONS[action]
    except KeyError:
        return _error("unknown_action", f"Unknown UI API action: {action}")

    try:
        return {"ok": True, "data": _to_jsonable(handler(data))}
    except Exception as exc:  # noqa: BLE001 - desktop needs a structured error boundary
        return _error(exc.__class__.__name__, str(exc))


def _summary(_: JsonDict) -> JsonDict:
    cfg = load_config()
    registry_path = find_registry_path()
    registry = load_registry(registry_path)
    resource = inspect_resource_repo(cfg)
    statuses = status_all(config=cfg, registry=registry, registry_path=registry_path)
    return {
        "version": __version__,
        "config": _config_summary(cfg),
        "registry_path": str(registry_path),
        "resource_repo": resource,
        "counts": _registry_counts(registry.items),
        "updates": sum(1 for item in statuses if item.has_update),
        "installed": sum(1 for item in statuses if item.installed),
    }


def _list_items(payload: JsonDict) -> JsonDict:
    cfg = load_config()
    kind = _optional_str(payload.get("kind"))
    registry_path = find_registry_path()
    registry = load_registry(registry_path)
    items = [item for item in registry.items if not kind or item.kind == kind]
    statuses = {s.name: s for s in status_all(config=cfg, registry=registry, registry_path=registry_path)}
    return {
        "registry_path": str(registry_path),
        "items": [
            {
                **item.model_dump(mode="json"),
                "status": _to_jsonable(statuses.get(item.name)),
            }
            for item in items
        ],
    }


def _resource_status(_: JsonDict) -> Any:
    return inspect_resource_repo(load_config())


def _platforms(_: JsonDict) -> JsonDict:
    cfg = load_config()
    return {"platforms": cfg.platforms.profiles}


def _doctor(_: JsonDict) -> JsonDict:
    cfg = load_config()
    git_path = shutil.which("git")
    resource = inspect_resource_repo(cfg)
    checks: list[JsonDict] = [
        {
            "id": "git",
            "label": "Git",
            "ok": bool(git_path),
            "detail": _git_version(git_path) if git_path else "git not found on PATH",
        },
        {
            "id": "config",
            "label": "Config",
            "ok": bool(cfg.source_path),
            "detail": str(cfg.source_path or default_config_path()),
        },
        {
            "id": "github_token",
            "label": "GitHub token",
            "ok": bool(cfg.github.token),
            "detail": f"Configured via {CONFIG_ENV_VAR} or config" if cfg.github.token else "Not configured",
        },
        {
            "id": "resource_repo",
            "label": "Resource repo",
            "ok": resource.exists and resource.is_git_repo,
            "detail": str(resource.local_path),
        },
    ]
    for profile in cfg.platforms.profiles:
        checks.append(
            {
                "id": f"platform:{profile.name}",
                "label": f"Platform: {profile.name}",
                "ok": True,
                "detail": "enabled" if profile.enabled else "disabled",
                "enabled": profile.enabled,
                "profile": profile,
            }
        )
    return {"checks": checks}


def _collect(payload: JsonDict) -> JsonDict:
    github_url = _required_str(payload, "github_url")
    cfg = load_config()
    detected = detect_remote_resource(
        github_url,
        explicit_type=_optional_str(payload.get("kind")),
        token=cfg.github.token or None,
    )
    entry = publisher.add_external_skill(
        detected.repo_url,
        name=_optional_str(payload.get("name")) or detected.name_hint,
        subdir=detected.subdir,
        ref=detected.ref,
        kind=detected.kind,
        skip_verify=bool(payload.get("skip_verify", False)),
        token=cfg.github.token or None,
        tags=detected.tags,
    )
    push_result = _maybe_push(cfg, payload)
    return {"entry": entry, "detected": detected, "push": push_result}


def _upload(payload: JsonDict) -> JsonDict:
    source = Path(_required_str(payload, "path")).expanduser()
    kind = detect_local_resource_type(source, explicit_type=_optional_str(payload.get("kind")))
    result = import_local_resource(
        source,
        kind=kind,
        name=_optional_str(payload.get("name")),
        overwrite=bool(payload.get("overwrite", False)),
    )
    push_result = _maybe_push(load_config(), payload)
    return {"entry": result.entry, "source_path": result.source_path, "stored_path": result.stored_path, "push": push_result}


def _sync(payload: JsonDict) -> JsonDict:
    include_kinds = set(_str_list(payload.get("include_kinds")))
    results = sync_all(
        config=load_config(),
        only=_str_list(payload.get("only")) or None,
        kind=_optional_str(payload.get("kind")),
        tags=_str_list(payload.get("tags")) or None,
        include_optional=bool(payload.get("all_kinds", False)),
        include_kinds=include_kinds or None,
        platform_filter=_optional_str(payload.get("platform")),
    )
    return {"results": results}


def _check(payload: JsonDict) -> JsonDict:
    results, pruned = check_all(
        config=load_config(),
        kind=_optional_str(payload.get("kind")),
        prune=bool(payload.get("prune", False)),
        uninstall=bool(payload.get("uninstall", False)),
    )
    return {"results": results, "pruned": pruned}


def _remove(payload: JsonDict) -> JsonDict:
    name = _required_str(payload, "name")
    cfg = load_config()
    registry = load_registry()
    entry = registry.get(name)
    removed = remove_skill(name)
    uninstalled = False
    if removed is not None and entry is not None and bool(payload.get("uninstall", False)):
        uninstalled = uninstall_one(entry, config=cfg)
    return {"removed": removed, "uninstalled": uninstalled}


def _resource_init(payload: JsonDict) -> Any:
    return init_resource_repo(name=_optional_str(payload.get("name")), config=load_config())


def _resource_use(payload: JsonDict) -> Any:
    return use_resource_repo(_required_str(payload, "target"), config=load_config())


def _resource_pull(_: JsonDict) -> Any:
    return pull_resource_repo(load_config())


def _resource_push(payload: JsonDict) -> Any:
    return push_resource_repo(
        message=_optional_str(payload.get("message")) or "lpm: update resources",
        config=load_config(),
    )


def _write_default_config(payload: JsonDict) -> JsonDict:
    force = bool(payload.get("force", False))
    path = default_config_path()
    if path.exists() and not force:
        return {"written": False, "path": path, "reason": "exists"}
    cfg = Config()
    written = write_config(cfg)
    return {"written": True, "path": written}


def _maybe_push(cfg: Config, payload: JsonDict) -> Any:
    if not bool(payload.get("push", False)):
        return None
    return push_resource_repo(config=cfg)


def _config_summary(cfg: Config) -> JsonDict:
    return {
        "path": str(cfg.source_path or default_config_path()),
        "exists": bool(cfg.source_path),
        "github": {
            "token_configured": bool(cfg.github.token),
            "owner": cfg.github.owner,
            "repo_prefix": cfg.github.repo_prefix,
            "default_private": cfg.github.default_private,
        },
        "resources": cfg.resources,
        "install": cfg.install,
    }


def _registry_counts(items: list[RegistryItem]) -> JsonDict:
    counts: JsonDict = {"total": len(items), "by_kind": {}, "by_source": {}}
    for item in items:
        counts["by_kind"][item.kind] = counts["by_kind"].get(item.kind, 0) + 1
        counts["by_source"][item.source] = counts["by_source"].get(item.source, 0) + 1
    return counts


def _git_version(git_path: str | None) -> str:
    if not git_path:
        return ""
    completed = subprocess.run(
        [git_path, "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() or git_path


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_to_jsonable(item) for item in value]
    return str(value)


def _error(code: str, message: str) -> JsonDict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _required_str(payload: JsonDict, key: str) -> str:
    value = payload.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"Missing required field: {key}")
    return str(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    raise ValueError("Expected a string list.")


ACTIONS: dict[str, Handler] = {
    "summary": _summary,
    "list_items": _list_items,
    "resource_status": _resource_status,
    "platforms": _platforms,
    "doctor": _doctor,
    "collect": _collect,
    "upload": _upload,
    "sync": _sync,
    "check": _check,
    "remove": _remove,
    "resource_init": _resource_init,
    "resource_use": _resource_use,
    "resource_pull": _resource_pull,
    "resource_push": _resource_push,
    "write_default_config": _write_default_config,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lpm-ui-api")
    parser.add_argument("action", choices=sorted(ACTIONS))
    parser.add_argument("payload", nargs="?", default="{}")
    args = parser.parse_args(argv)

    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError as exc:
        result = _error("invalid_json", str(exc))
    else:
        if not isinstance(payload, dict):
            result = _error("invalid_payload", "Payload must be a JSON object.")
        else:
            result = run_action(args.action, payload)

    print(json.dumps(_to_jsonable(result), ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
