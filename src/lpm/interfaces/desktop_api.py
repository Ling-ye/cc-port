"""Structured JSON API used by the desktop application.

The CLI is optimized for humans and Rich tables.  This module exposes the
same core operations as stable JSON so desktop shells do not need to parse
terminal output.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .. import __version__
from ..core.config import (
    CONFIG_ENV_VAR,
    DEFAULT_INSTALL_TARGET,
    DEFAULT_REPO_PREFIX,
    DEFAULT_RESOURCE_BRANCH,
    DEFAULT_RESOURCE_REPO_NAME,
    Config,
    GithubConfig,
    InstallConfig,
    ResourcesConfig,
    default_config_path,
    load_config,
    load_raw_config,
    write_config,
)
from ..core.models import RegistryItem
from ..core.platforms import PLATFORM_PRESETS, PlatformProfile, PlatformsConfig, build_platform
from ..core.registry import find_registry_path, load_registry
from ..core.resource_detection import detect_local_resource_type, detect_remote_resource
from ..infrastructure import git_ops
from ..infrastructure.github_client import GithubClient
from ..services import publisher
from ..services.installer import check_all, status_all, sync_all, uninstall_one
from ..services.local_resources import import_local_resource
from ..services.publisher import remove_skill
from ..services.resource_discovery import (
    discover_resources,
    read_discovered_resource,
    resolve_discovered_resources,
)
from ..services.resource_repo import (
    ensure_structure,
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


def _discover_resources(payload: JsonDict) -> JsonDict:
    scope = _optional_str(payload.get("scope")) or "global"
    root_path = _optional_str(payload.get("root_path"))
    items = discover_resources(scope=scope, root_path=root_path)
    return {"scope": scope, "root_path": root_path or "", "items": items}


def _read_discovered_resource(payload: JsonDict) -> Any:
    return read_discovered_resource(
        _required_str(payload, "id"),
        scope=_optional_str(payload.get("scope")) or "global",
        root_path=_optional_str(payload.get("root_path")),
    )


def _upload_discovered_resources(payload: JsonDict) -> JsonDict:
    selections = _discovery_selections(payload.get("items"))
    if not selections:
        raise ValueError("Missing required field: items")

    candidates = resolve_discovered_resources(
        [item["id"] for item in selections],
        scope=_optional_str(payload.get("scope")) or "global",
        root_path=_optional_str(payload.get("root_path")),
    )
    overwrite = bool(payload.get("overwrite", False))
    results: list[JsonDict] = []
    imported = 0

    for selection, candidate in zip(selections, candidates, strict=True):
        name = _optional_str(selection.get("name")) or candidate.name_hint
        try:
            result = import_local_resource(
                candidate.path,
                kind=candidate.kind,
                name=name,
                overwrite=overwrite,
            )
        except Exception as exc:  # noqa: BLE001 - batch uploads report per-item failures
            results.append(
                {
                    "id": candidate.id,
                    "name": name,
                    "kind": candidate.kind,
                    "path": candidate.path,
                    "ok": False,
                    "error": str(exc),
                }
            )
            continue

        imported += 1
        results.append(
            {
                "id": candidate.id,
                "name": result.entry.name,
                "kind": result.entry.kind,
                "path": candidate.path,
                "ok": True,
                "entry": result.entry,
                "source_path": result.source_path,
                "stored_path": result.stored_path,
            }
        )

    push_result = _maybe_push(load_config(), payload) if imported else None
    return {
        "results": results,
        "imported": imported,
        "failed": len(results) - imported,
        "push": push_result,
    }


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


def _config_get(_: JsonDict) -> JsonDict:
    raw_cfg = load_raw_config()
    effective_cfg = load_config()
    env_token = os.environ.get(CONFIG_ENV_VAR, "").strip()
    return {
        "path": str(raw_cfg.source_path or default_config_path()),
        "exists": bool(raw_cfg.source_path),
        "token_source": "env" if env_token else ("config" if raw_cfg.github.token else "none"),
        "token_preview": _mask_token(effective_cfg.github.token),
        "config_token_preview": _mask_token(raw_cfg.github.token),
        "env_token_active": bool(env_token),
        "config": _editable_config(raw_cfg),
    }


def _config_check(payload: JsonDict) -> JsonDict:
    raw_cfg = load_raw_config()
    cfg = _config_from_draft(payload, raw_cfg)
    cfg.github.token = _effective_token(cfg.github.token)
    return _check_resource_target(cfg)


def _config_save(payload: JsonDict) -> JsonDict:
    raw_cfg = load_raw_config()
    cfg = _config_from_draft(payload, raw_cfg)
    config_path = raw_cfg.source_path or default_config_path()

    resource_result = None
    if bool(payload.get("prepare_resource_repo", False)):
        resource_result = _prepare_resource_target(cfg, _effective_token(cfg.github.token))

    written = write_config(cfg, config_path)
    data = _config_get({})
    data["saved"] = True
    data["path"] = str(written)
    if resource_result is not None:
        data["resource_repo"] = resource_result
    return data


def _write_default_config(payload: JsonDict) -> JsonDict:
    force = bool(payload.get("force", False))
    path = default_config_path()
    if path.exists() and not force:
        return {"written": False, "path": path, "reason": "exists"}
    cfg = Config()
    written = write_config(cfg)
    return {"written": True, "path": written}


def _editable_config(cfg: Config) -> JsonDict:
    return {
        "github": {
            "owner": cfg.github.owner,
            "repo_prefix": cfg.github.repo_prefix,
            "default_private": cfg.github.default_private,
        },
        "install": {"target": cfg.install.target},
        "resources": {
            "repo_name": cfg.resources.repo_name,
            "repo_url": cfg.resources.repo_url,
            "local_path": cfg.resources.local_path,
            "branch": cfg.resources.branch,
        },
        "platforms": [_platform_to_json(p) for p in _platforms_with_presets(cfg.platforms.profiles)],
    }


def _platforms_with_presets(profiles: list[PlatformProfile]) -> list[PlatformProfile]:
    out = [
        PlatformProfile(
            name=p.name,
            enabled=p.enabled,
            skills_dir=p.skills_dir,
            mcp_json=p.mcp_json,
            rules_dir=p.rules_dir,
        )
        for p in profiles
    ]
    seen = {p.name for p in out}
    for name in PLATFORM_PRESETS:
        if name in seen:
            continue
        preset = build_platform(name)
        preset.enabled = False
        out.append(preset)
    return out


def _platform_to_json(profile: PlatformProfile) -> JsonDict:
    return {
        "name": profile.name,
        "enabled": profile.enabled,
        "skills_dir": profile.skills_dir,
        "mcp_json": profile.mcp_json,
        "rules_dir": profile.rules_dir,
    }


def _config_from_draft(payload: JsonDict, base: Config) -> Config:
    draft = payload.get("draft")
    if not isinstance(draft, dict):
        raise ValueError("Missing required field: draft")

    github_data = _dict_field(draft, "github")
    install_data = _dict_field(draft, "install")
    resources_data = _dict_field(draft, "resources")

    return Config(
        github=GithubConfig(
            token=_token_for_write(base.github.token, payload),
            owner=_field_str(github_data, "owner", base.github.owner),
            repo_prefix=_field_str(github_data, "repo_prefix", base.github.repo_prefix or DEFAULT_REPO_PREFIX),
            default_private=_field_bool(github_data, "default_private", base.github.default_private),
        ),
        install=InstallConfig(
            target=_field_str(install_data, "target", base.install.target or DEFAULT_INSTALL_TARGET),
        ),
        resources=ResourcesConfig(
            repo_name=_field_str(
                resources_data,
                "repo_name",
                base.resources.repo_name or DEFAULT_RESOURCE_REPO_NAME,
            )
            or DEFAULT_RESOURCE_REPO_NAME,
            repo_url=_field_str(resources_data, "repo_url", base.resources.repo_url),
            local_path=_field_str(resources_data, "local_path", base.resources.local_path),
            branch=_field_str(
                resources_data,
                "branch",
                base.resources.branch or DEFAULT_RESOURCE_BRANCH,
            )
            or DEFAULT_RESOURCE_BRANCH,
        ),
        platforms=PlatformsConfig(
            profiles=_platforms_from_payload(draft.get("platforms"), base.platforms.profiles),
        ),
        source_path=base.source_path,
    )


def _token_for_write(current: str, payload: JsonDict) -> str:
    action = str(payload.get("token_action") or "preserve").strip().lower()
    if action == "clear":
        return ""
    if action == "replace":
        token = str(payload.get("new_token") or "").strip()
        return token or current
    return current


def _effective_token(config_token: str) -> str:
    return os.environ.get(CONFIG_ENV_VAR, "").strip() or config_token


def _platforms_from_payload(value: Any, existing: list[PlatformProfile]) -> list[PlatformProfile]:
    if not isinstance(value, list):
        return _platforms_with_presets(existing)

    out: list[PlatformProfile] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        out.append(
            PlatformProfile(
                name=name,
                enabled=bool(item.get("enabled", False)),
                skills_dir=str(item.get("skills_dir") or ""),
                mcp_json=str(item.get("mcp_json") or ""),
                rules_dir=str(item.get("rules_dir") or ""),
            )
        )
        seen.add(name)
    return out or _platforms_with_presets(existing)


def _dict_field(data: JsonDict, key: str) -> JsonDict:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _field_str(data: JsonDict, key: str, default: str = "") -> str:
    if key not in data:
        return default
    return str(data.get(key) or "").strip()


def _field_bool(data: JsonDict, key: str, default: bool = False) -> bool:
    if key not in data:
        return default
    value = data.get(key)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}{'*' * max(4, len(token) - 8)}{token[-4:]}"


def _check_resource_target(cfg: Config) -> JsonDict:
    missing: list[JsonDict] = []
    warnings: list[JsonDict] = []
    token = cfg.github.token.strip()
    local_path = cfg.resources.local_path_value.expanduser()
    local_exists = local_path.exists()
    local_is_git = git_ops.is_repo(local_path) if local_exists else False

    if not local_exists:
        missing.append(
            {
                "id": "local_path",
                "label": "Local resource directory",
                "detail": f"{local_path} does not exist.",
            }
        )
    elif not local_is_git:
        missing.append(
            {
                "id": "local_git",
                "label": "Local git repository",
                "detail": f"{local_path} exists but is not a git repository.",
            }
        )

    remote = _check_remote_repo(cfg, token, missing, warnings)
    has_blocking_warning = any(item["id"] in {"github_token", "remote_unsupported"} for item in warnings)
    return {
        "missing": missing,
        "warnings": warnings,
        "can_prepare": bool(missing) and not has_blocking_warning,
        "local": {
            "path": str(local_path),
            "exists": local_exists,
            "is_git_repo": local_is_git,
        },
        "remote": remote,
    }


def _check_remote_repo(
    cfg: Config,
    token: str,
    missing: list[JsonDict],
    warnings: list[JsonDict],
) -> JsonDict:
    parsed = _parse_github_repo(cfg.resources.repo_url)
    if cfg.resources.repo_url and parsed is None:
        warnings.append(
            {
                "id": "remote_unsupported",
                "label": "Remote repository",
                "detail": "Only github.com repositories can be checked or created from Settings.",
            }
        )
        return {"checked": False, "exists": False, "repo": cfg.resources.repo_url}

    if not token:
        warnings.append(
            {
                "id": "github_token",
                "label": "GitHub token",
                "detail": f"Set a token in config or {CONFIG_ENV_VAR} before checking private repositories.",
            }
        )
        return {"checked": False, "exists": False, "repo": cfg.resources.repo_url}

    try:
        client = GithubClient(token)
        owner, name = _target_repo_owner_name(cfg, client)
        repo = client.get_repo(owner, name)
    except Exception as exc:  # noqa: BLE001 - surfaced as a desktop warning
        warnings.append(
            {
                "id": "remote_check",
                "label": "Remote repository",
                "detail": str(exc),
            }
        )
        return {"checked": False, "exists": False, "repo": cfg.resources.repo_url}

    label = f"{owner}/{name}"
    if repo is None:
        missing.append(
            {
                "id": "remote_repo",
                "label": "GitHub repository",
                "detail": f"{label} is not accessible or does not exist.",
            }
        )
        return {"checked": True, "exists": False, "repo": label}

    return {"checked": True, "exists": True, "repo": label}


def _prepare_resource_target(cfg: Config, token: str) -> JsonDict:
    if not token:
        raise ValueError(f"Set a GitHub token in config or {CONFIG_ENV_VAR} before creating a resource repository.")
    if cfg.resources.repo_url and _parse_github_repo(cfg.resources.repo_url) is None:
        raise ValueError("Only github.com resource repositories can be created or connected from Settings.")

    client = GithubClient(token)
    owner, name = _target_repo_owner_name(cfg, client)
    repo, created = client.ensure_repo(
        owner=owner,
        name=name,
        description="Private LPM AI resources repository.",
        private=True,
    )
    branch = cfg.resources.branch or DEFAULT_RESOURCE_BRANCH
    local_path = cfg.resources.local_path_value.expanduser().resolve()

    if not local_path.exists():
        git_ops.clone(repo.https_url, local_path, token=token)
    else:
        local_path.mkdir(parents=True, exist_ok=True)
        if not git_ops.is_repo(local_path):
            git_ops.init_repo(local_path, default_branch=branch)
        git_ops.set_remote(local_path, "origin", repo.https_url)

    git_ops.checkout_branch(local_path, branch)
    ensure_structure(local_path)
    if git_ops.status_short(local_path):
        git_ops.add_all(local_path)
        git_ops.commit(local_path, message="lpm: initialize resource repository")
    git_ops.push(local_path, branch=branch, token=token)

    cfg.resources.repo_name = name
    cfg.resources.repo_url = repo.https_url
    cfg.resources.local_path = str(local_path)
    cfg.resources.branch = branch

    return {
        "created": created,
        "repo_url": repo.https_url,
        "local_path": str(local_path),
        "info": inspect_resource_repo(cfg),
    }


def _target_repo_owner_name(cfg: Config, client: GithubClient) -> tuple[str, str]:
    parsed = _parse_github_repo(cfg.resources.repo_url)
    if parsed is not None:
        return parsed
    owner = cfg.github.owner.strip() or client.authenticated_login()
    name = cfg.resources.repo_name.strip() or DEFAULT_RESOURCE_REPO_NAME
    return owner, name


def _parse_github_repo(value: str) -> tuple[str, str] | None:
    raw = value.strip().rstrip("/")
    if not raw:
        return None
    if raw.startswith("git@github.com:"):
        path = raw.split(":", 1)[1]
    else:
        parsed = urlparse(raw)
        if parsed.netloc.lower() != "github.com":
            return None
        path = parsed.path.lstrip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None
    return parts[0], parts[1].removesuffix(".git")


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


def _write_json_response(result: JsonDict) -> None:
    text = json.dumps(_to_jsonable(result), ensure_ascii=True)
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8") + b"\n")
        buffer.flush()
        return
    print(text)


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


def _discovery_selections(value: Any) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    out: list[JsonDict] = []
    for item in value:
        if isinstance(item, str):
            if item.strip():
                out.append({"id": item.strip()})
        elif isinstance(item, dict):
            resource_id = str(item.get("id") or "").strip()
            if resource_id:
                out.append({"id": resource_id, "name": _optional_str(item.get("name")) or ""})
    return out


ACTIONS: dict[str, Handler] = {
    "summary": _summary,
    "list_items": _list_items,
    "resource_status": _resource_status,
    "platforms": _platforms,
    "doctor": _doctor,
    "collect": _collect,
    "upload": _upload,
    "discover_resources": _discover_resources,
    "read_discovered_resource": _read_discovered_resource,
    "upload_discovered_resources": _upload_discovered_resources,
    "sync": _sync,
    "check": _check,
    "remove": _remove,
    "resource_init": _resource_init,
    "resource_use": _resource_use,
    "resource_pull": _resource_pull,
    "resource_push": _resource_push,
    "config_get": _config_get,
    "config_check": _config_check,
    "config_save": _config_save,
    "write_default_config": _write_default_config,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lpm-desktop-api")
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

    _write_json_response(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
