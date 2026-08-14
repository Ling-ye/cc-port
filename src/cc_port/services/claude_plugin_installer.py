"""Profile-bound Claude Code marketplace plugin installation.

This module intentionally delegates marketplace registration, cache layout,
dependency installation, and enabled-plugin state to Claude Code's CLI.  CC
Port never copies files into ``~/.claude/plugins`` itself.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..core.claude_plugins import CLAUDE_PLUGIN_NAME_RE
from ..core.models import PluginInstallation, PluginSpec
from ..core.platforms import PlatformProfile, current_environment_identity

MARKETPLACE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ClaudePluginInstallError(RuntimeError):
    """Raised when Claude's native plugin management cannot complete safely."""


@dataclass(frozen=True)
class ClaudePluginInstallResult:
    qualified_name: str
    scope: str
    marketplace_added: bool
    enabled: bool


@dataclass(frozen=True)
class ClaudeCliContext:
    command: tuple[str, ...]
    config_dir: Path


def claude_cli_context(profile: PlatformProfile) -> ClaudeCliContext | None:
    """Resolve a Claude CLI only for the profile's exact runtime identity."""
    if profile.effective_tool_id != "claude-code":
        return None
    configured_kind = profile.environment_kind.strip().lower()
    configured_name = profile.environment_name.strip()
    current_kind, current_name = current_environment_identity()
    if configured_kind and configured_kind != current_kind:
        return None
    if configured_kind == "wsl" and (
        not configured_name
        or not current_name
        or configured_name.casefold() != current_name.casefold()
    ):
        return None
    executable = shutil.which("claude")
    if not executable:
        return None
    settings_path = profile.settings_file()
    if settings_path is None:
        return None
    return ClaudeCliContext(
        command=(executable,),
        config_dir=settings_path.expanduser().absolute().parent,
    )


def install_marketplace_plugin(
    profile: PlatformProfile,
    spec: PluginSpec,
    installation: PluginInstallation,
    *,
    project_root: Path | None = None,
) -> ClaudePluginInstallResult:
    """Install one reference plugin through Claude's native CLI and verify it."""
    if spec.platform != "claude-code" or spec.track != "reference":
        raise ClaudePluginInstallError("Claude native install requires a reference plugin.")
    if spec.origin.type != "marketplace":
        raise ClaudePluginInstallError(
            "Claude native install currently requires a marketplace plugin origin."
        )
    if installation.scope == "managed":
        raise ClaudePluginInstallError("Managed Claude plugins are read-only.")
    if installation.scope in {"project", "local"} and project_root is None:
        raise ClaudePluginInstallError(
            f"Claude {installation.scope} plugin install requires an exact project mapping."
        )
    if not MARKETPLACE_NAME_RE.fullmatch(spec.origin.marketplace):
        raise ClaudePluginInstallError("Claude marketplace name is not a safe portable id.")
    if not CLAUDE_PLUGIN_NAME_RE.fullmatch(spec.plugin_id):
        raise ClaudePluginInstallError("Claude plugin name is not a safe kebab-case id.")

    context = claude_cli_context(profile)
    if context is None:
        raise ClaudePluginInstallError(
            "Claude CLI is unavailable for the selected runtime profile."
        )
    qualified = f"{spec.plugin_id}@{spec.origin.marketplace}"
    cwd = project_root if installation.scope in {"project", "local"} else None
    marketplace_added = False
    registration = _marketplace_registration(
        context,
        spec.origin.marketplace,
        cwd=cwd,
    )
    if registration is not None and not _registration_matches(registration, spec):
        raise ClaudePluginInstallError(
            "The registered Claude marketplace source or ref does not match the plan."
        )
    if registration is None:
        source = installable_marketplace_source(
            spec.origin.source,
            spec.origin.selector,
        )
        if not source:
            raise ClaudePluginInstallError(
                "The Claude marketplace is not registered and its portable source is unavailable."
            )
        _run_claude(
            context,
            ["plugin", "marketplace", "add", source, "--scope", installation.scope],
            cwd=cwd,
            timeout=120,
        )
        marketplace_added = True

    try:
        _run_claude(
            context,
            ["plugin", "install", qualified, "--scope", installation.scope],
            cwd=cwd,
            timeout=180,
        )
        if not installation.enabled:
            _run_claude(
                context,
                ["plugin", "disable", qualified, "--scope", installation.scope],
                cwd=cwd,
                timeout=60,
            )
        if not _plugin_state_matches(
            context,
            qualified,
            spec.plugin_id,
            installation.scope,
            installation.enabled,
            cwd=cwd,
        ):
            raise ClaudePluginInstallError(
                "Claude plugin install completed but native verification did not match."
            )
    except Exception:
        if marketplace_added:
            _best_effort_remove_marketplace(
                context,
                spec.origin.marketplace,
                installation.scope,
                cwd=cwd,
            )
        raise

    return ClaudePluginInstallResult(
        qualified_name=qualified,
        scope=installation.scope,
        marketplace_added=marketplace_added,
        enabled=installation.enabled,
    )


def marketplace_install_ready(
    profile: PlatformProfile,
    spec: PluginSpec,
    *,
    project_root: Path | None = None,
) -> bool:
    """Check that an exact marketplace registration can be used or safely added."""
    if spec.platform != "claude-code" or spec.origin.type != "marketplace":
        return False
    context = claude_cli_context(profile)
    if context is None:
        return False
    try:
        registration = _marketplace_registration(
            context,
            spec.origin.marketplace,
            cwd=project_root,
        )
    except ClaudePluginInstallError:
        return False
    if registration is not None:
        return _registration_matches(registration, spec)
    return bool(installable_marketplace_source(spec.origin.source, spec.origin.selector))


def set_marketplace_plugin_enabled(
    profile: PlatformProfile,
    spec: PluginSpec,
    installation: PluginInstallation,
    *,
    project_root: Path | None = None,
) -> None:
    """Change installed state through the exact profile's native Claude CLI."""
    if spec.platform != "claude-code" or spec.origin.type != "marketplace":
        raise ClaudePluginInstallError(
            "Claude native state alignment requires a marketplace plugin."
        )
    if installation.scope == "managed":
        raise ClaudePluginInstallError("Managed Claude plugins are read-only.")
    if installation.scope in {"project", "local"} and project_root is None:
        raise ClaudePluginInstallError(
            f"Claude {installation.scope} plugin state requires an exact project mapping."
        )
    if not MARKETPLACE_NAME_RE.fullmatch(spec.origin.marketplace):
        raise ClaudePluginInstallError("Claude marketplace name is not a safe portable id.")
    if not CLAUDE_PLUGIN_NAME_RE.fullmatch(spec.plugin_id):
        raise ClaudePluginInstallError("Claude plugin name is not a safe kebab-case id.")

    context = claude_cli_context(profile)
    if context is None:
        raise ClaudePluginInstallError(
            "Claude CLI is unavailable for the selected runtime profile."
        )
    qualified = f"{spec.plugin_id}@{spec.origin.marketplace}"
    cwd = project_root if installation.scope in {"project", "local"} else None
    _run_claude(
        context,
        [
            "plugin",
            "enable" if installation.enabled else "disable",
            qualified,
            "--scope",
            installation.scope,
        ],
        cwd=cwd,
        timeout=60,
    )
    if not _plugin_state_matches(
        context,
        qualified,
        spec.plugin_id,
        installation.scope,
        installation.enabled,
        cwd=cwd,
    ):
        raise ClaudePluginInstallError(
            "Claude plugin state command completed but native verification did not match."
        )


def _run_claude(
    context: ClaudeCliContext,
    arguments: list[str],
    *,
    cwd: Path | None,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(context.config_dir)
    try:
        result = subprocess.run(
            [*context.command, *arguments],
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClaudePluginInstallError(f"Claude plugin command could not run: {exc}") from exc
    if result.returncode != 0:
        detail = _safe_cli_error(result.stderr or result.stdout)
        raise ClaudePluginInstallError(
            f"Claude plugin command failed with exit code {result.returncode}: {detail}"
        )
    return result


def _marketplace_registration(
    context: ClaudeCliContext,
    marketplace: str,
    *,
    cwd: Path | None,
) -> dict[str, Any] | None:
    result = _run_claude(
        context,
        ["plugin", "marketplace", "list", "--json"],
        cwd=cwd,
        timeout=30,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudePluginInstallError(
            "Claude marketplace list did not return valid JSON."
        ) from exc
    values: Any = payload
    if isinstance(payload, dict) and "marketplaces" in payload:
        values = payload["marketplaces"]
    if isinstance(values, dict):
        values = [
            {"name": name, **(value if isinstance(value, dict) else {})}
            for name, value in values.items()
        ]
    if not isinstance(values, list):
        raise ClaudePluginInstallError(
            "Claude marketplace list returned an unsupported JSON shape."
        )
    matches = [
        item
        for item in values
        if isinstance(item, dict) and str(item.get("name") or "") == marketplace
    ]
    if len(matches) > 1:
        raise ClaudePluginInstallError(
            "Claude marketplace list returned duplicate marketplace identities."
        )
    return matches[0] if matches else None


def _registration_matches(registration: dict[str, Any], spec: PluginSpec) -> bool:
    expected_source = _normalized_marketplace_source(spec.origin.source)
    actual_source = _normalized_marketplace_source(_registration_source(registration))
    if expected_source and expected_source != actual_source:
        return False
    expected_ref = spec.origin.selector.strip()
    actual_ref = str(registration.get("ref") or "").strip()
    return not expected_ref or expected_ref == actual_ref


def _registration_source(registration: dict[str, Any]) -> str:
    for field in ("repo", "url", "path"):
        value = registration.get(field)
        if isinstance(value, str) and value.strip():
            return value
    source = registration.get("source")
    if isinstance(source, str):
        return source
    if isinstance(source, dict):
        return str(source.get("repo") or source.get("url") or source.get("path") or "")
    return ""


def _plugin_state_matches(
    context: ClaudeCliContext,
    qualified: str,
    plugin_id: str,
    scope: str,
    enabled: bool,
    *,
    cwd: Path | None,
) -> bool:
    try:
        result = _run_claude(
            context,
            ["plugin", "list", "--json"],
            cwd=cwd,
            timeout=30,
        )
        payload = json.loads(result.stdout)
    except (ClaudePluginInstallError, json.JSONDecodeError):
        return False
    values = (
        payload
        if isinstance(payload, list)
        else payload.get("plugins", payload.get("installed", []))
        if isinstance(payload, dict)
        else []
    )
    if not isinstance(values, list):
        return False
    for item in values:
        if not isinstance(item, dict):
            continue
        identity = str(item.get("id") or item.get("name") or "")
        item_scope = str(item.get("scope") or "user")
        if identity in {qualified, plugin_id} and item_scope == scope:
            return bool(item.get("enabled", True)) is enabled
    return False


def installable_marketplace_source(value: str, selector: str = "") -> str:
    source = value.strip()
    ref = selector.strip()
    if ref and not _safe_git_ref(ref):
        return ""
    if not source or source.startswith(("/", "\\", "./", "../")):
        return ""
    if re.match(r"^[A-Za-z]:[\\/]", source):
        return ""
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source):
        normalized = source.removesuffix(".git")
        return f"{normalized}@{ref}" if ref else normalized
    if (
        source.startswith(("https://", "ssh://", "git://"))
        and "@" not in source.partition("://")[2].partition("/")[0]
    ):
        if ref and source.lower().split("?", 1)[0].endswith(".json"):
            return ""
        return f"{source}#{ref}" if ref else source
    return ""


def _normalized_marketplace_source(value: str) -> str:
    source = value.strip().rstrip("/")
    if not source:
        return ""
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", source):
        return source.removesuffix(".git").lower()
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https", "ssh", "git"} and parsed.hostname:
        path = parsed.path.lstrip("/").removesuffix(".git").rstrip("/")
        if parsed.hostname.lower() == "github.com":
            return path.lower()
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{port}/{path}"
    return source


def _safe_git_ref(value: str) -> bool:
    return bool(
        re.fullmatch(r"[A-Za-z0-9._/-]{1,255}", value)
        and ".." not in value
        and "//" not in value
        and not value.startswith(("/", "."))
        and not value.endswith(("/", "."))
    )


def _best_effort_remove_marketplace(
    context: ClaudeCliContext,
    marketplace: str,
    scope: str,
    *,
    cwd: Path | None,
) -> None:
    try:
        _run_claude(
            context,
            ["plugin", "marketplace", "remove", marketplace, "--scope", scope],
            cwd=cwd,
            timeout=60,
        )
    except ClaudePluginInstallError:
        pass


def _safe_cli_error(value: str) -> str:
    collapsed = " ".join(value.split())
    return collapsed[:500] if collapsed else "no diagnostic output"
