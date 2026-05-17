"""Shared health check construction for CLI and desktop surfaces."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Literal

from ..core.config import CONFIG_ENV_VAR, Config, default_config_path, load_config
from ..core.platforms import PlatformProfile
from .resource_repo import ResourceRepoInfo, inspect_resource_repo

DoctorStatus = Literal["ok", "warning", "error", "skipped"]
DoctorCheck = dict[str, Any]


def build_doctor_checks(config: Config | None = None) -> list[DoctorCheck]:
    """Build non-mutating environment health checks.

    Optional features that are not configured are reported as ``skipped`` so
    a fresh installation does not look broken before a resource repo is set up.
    """

    cfg = config or load_config()
    git_path = shutil.which("git")
    resource = inspect_resource_repo(cfg)
    resource_configured = _resource_repo_configured(cfg, resource)

    checks: list[DoctorCheck] = [
        _check(
            "git",
            "Git",
            "ok" if git_path else "error",
            _git_version(git_path) if git_path else "git not found on PATH",
        ),
        _config_check(cfg),
        _github_token_check(cfg, resource_configured),
        _resource_repo_check(cfg, resource, resource_configured),
        _install_target_check(cfg),
    ]

    checks.extend(_platform_check(profile) for profile in cfg.platforms.profiles)
    return checks


def has_doctor_errors(checks: list[DoctorCheck]) -> bool:
    return any(check.get("status") == "error" for check in checks)


def _check(
    check_id: str,
    label: str,
    status: DoctorStatus,
    detail: str,
    **extra: Any,
) -> DoctorCheck:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "ok": status != "error",
        "detail": detail,
        **extra,
    }


def _config_check(cfg: Config) -> DoctorCheck:
    if cfg.source_path:
        return _check("config", "Config", "ok", str(cfg.source_path))
    return _check(
        "config",
        "Config",
        "skipped",
        f"No config file; using defaults at {default_config_path()}",
    )


def _github_token_check(cfg: Config, resource_configured: bool) -> DoctorCheck:
    if cfg.github.token:
        return _check(
            "github_token",
            "GitHub token",
            "ok",
            f"Configured via {CONFIG_ENV_VAR} or config",
        )
    if resource_configured:
        return _check(
            "github_token",
            "GitHub token",
            "warning",
            f"Not configured; private GitHub resource repo actions need {CONFIG_ENV_VAR} or config",
        )
    return _check(
        "github_token",
        "GitHub token",
        "skipped",
        "Not configured; needed only for GitHub and private resource repo actions",
    )


def _resource_repo_check(
    cfg: Config,
    resource: ResourceRepoInfo,
    resource_configured: bool,
) -> DoctorCheck:
    if not resource_configured:
        return _check(
            "resource_repo",
            "Resource repo",
            "skipped",
            f"Not configured; expected local path would be {resource.local_path}",
        )
    if not resource.exists:
        return _check(
            "resource_repo",
            "Resource repo",
            "warning",
            f"Configured but local path does not exist: {resource.local_path}",
        )
    if not resource.is_git_repo:
        return _check(
            "resource_repo",
            "Resource repo",
            "error",
            f"Configured path exists but is not a git repository: {resource.local_path}",
        )

    detail_parts = [f"Git repository at {resource.local_path}"]
    if resource.remote_url:
        detail_parts.append(f"remote {resource.remote_url}")
    elif cfg.resources.repo_url:
        detail_parts.append("no git remote configured")

    status: DoctorStatus = "ok"
    if cfg.resources.repo_url:
        if not resource.remote_url:
            status = "warning"
            detail_parts.append(f"configured URL is {cfg.resources.repo_url}")
        else:
            expected = _normalize_git_url(cfg.resources.repo_url)
            actual = _normalize_git_url(resource.remote_url)
            if expected != actual:
                status = "warning"
                detail_parts.append(f"configured URL is {cfg.resources.repo_url}")
    if resource.dirty:
        status = "warning"
        detail_parts.append("has local changes")

    return _check("resource_repo", "Resource repo", status, "; ".join(detail_parts))


def _install_target_check(cfg: Config) -> DoctorCheck:
    target = cfg.install.target_path
    if not target.exists():
        return _check(
            "install_target",
            "Install target",
            "skipped",
            f"Not created yet; installs will use {target}",
        )
    if not target.is_dir():
        return _check(
            "install_target",
            "Install target",
            "error",
            f"Configured install target is not a directory: {target}",
        )
    if not os.access(target, os.W_OK):
        return _check(
            "install_target",
            "Install target",
            "error",
            f"Directory is not writable: {target}",
        )
    return _check("install_target", "Install target", "ok", f"Writable directory: {target}")


def _platform_check(profile: PlatformProfile) -> DoctorCheck:
    if not profile.enabled:
        return _check(
            f"platform:{profile.name}",
            f"Platform: {profile.name}",
            "skipped",
            "disabled",
            enabled=False,
            profile=profile,
        )

    problems = _platform_path_problems(profile)
    if problems:
        return _check(
            f"platform:{profile.name}",
            f"Platform: {profile.name}",
            "error",
            "; ".join(problems),
            enabled=True,
            profile=profile,
        )

    details = _platform_details(profile)
    return _check(
        f"platform:{profile.name}",
        f"Platform: {profile.name}",
        "ok",
        "; ".join(details) if details else "enabled",
        enabled=True,
        profile=profile,
    )


def _platform_path_problems(profile: PlatformProfile) -> list[str]:
    problems: list[str] = []
    for label, path in (
        ("skills_dir", profile.skills_path()),
        ("rules_dir", profile.rules_path()),
    ):
        if path is None or not path.exists():
            continue
        if not path.is_dir():
            problems.append(f"{label} is not a directory: {path}")
        elif not os.access(path, os.W_OK):
            problems.append(f"{label} is not writable: {path}")

    mcp_json = profile.mcp_json_path()
    if mcp_json and mcp_json.exists() and mcp_json.is_dir():
        problems.append(f"mcp_json is a directory: {mcp_json}")
    elif mcp_json and mcp_json.parent.exists() and not os.access(mcp_json.parent, os.W_OK):
        problems.append(f"mcp_json parent is not writable: {mcp_json.parent}")
    return problems


def _platform_details(profile: PlatformProfile) -> list[str]:
    details = ["enabled"]
    if profile.skills_dir:
        details.append(f"skills_dir {profile.skills_path()}")
    if profile.mcp_json:
        details.append(f"mcp_json {profile.mcp_json_path()}")
    if profile.rules_dir:
        details.append(f"rules_dir {profile.rules_path()}")
    return details


def _resource_repo_configured(cfg: Config, resource: ResourceRepoInfo) -> bool:
    return bool(
        cfg.resources.repo_url.strip()
        or cfg.resources.local_path.strip()
        or resource.exists
    )


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


def _normalize_git_url(value: str) -> str:
    return value.strip().removesuffix(".git").rstrip("/")
