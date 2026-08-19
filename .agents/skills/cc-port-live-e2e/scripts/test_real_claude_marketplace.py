"""Exercise CC Port's Claude Marketplace adapter through a real WSL Claude CLI.

The test uses an isolated ``CLAUDE_CONFIG_DIR`` under ``/tmp``.  It never starts
Claude or a plugin, and it removes the installed plugin and marketplace before
returning.  The caller owns removal of the generated temporary directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cc_port.core.models import PluginInstallation, PluginOrigin, PluginSpec
from cc_port.core.platforms import PlatformProfile
from cc_port.services.claude_plugin_installer import (
    claude_cli_context,
    install_marketplace_plugin,
    marketplace_install_ready,
    set_marketplace_plugin_enabled,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _protected_snapshot(config_dir: Path) -> dict[str, str | None]:
    return {
        relative: _sha256(config_dir / relative)
        for relative in (
            "settings.json",
            "plugins/installed_plugins.json",
            "plugins/known_marketplaces.json",
        )
    }


def _run(command: tuple[str, ...], config_dir: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    result = subprocess.run(
        [*command, *arguments],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout).split())[:500]
        raise RuntimeError(f"Claude CLI exited {result.returncode}: {detail}")
    return result


def _plugin_state(command: tuple[str, ...], config_dir: Path, qualified: str) -> dict[str, Any] | None:
    payload = json.loads(_run(command, config_dir, "plugin", "list", "--json").stdout)
    values = payload if isinstance(payload, list) else payload.get("installed", [])
    for item in values:
        if isinstance(item, dict) and str(item.get("id") or item.get("name") or "") == qualified:
            return item
    return None


def _marketplaces(command: tuple[str, ...], config_dir: Path) -> list[dict[str, Any]]:
    payload = json.loads(
        _run(command, config_dir, "plugin", "marketplace", "list", "--json").stdout
    )
    if not isinstance(payload, list):
        raise RuntimeError("Claude marketplace list returned a non-list payload.")
    return [item for item in payload if isinstance(item, dict)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plugin-id", default="agent-sdk-dev")
    parser.add_argument("--marketplace", default="claude-plugins-official")
    parser.add_argument("--source", default="anthropics/claude-plugins-official")
    parser.add_argument("--protected-config-dir", type=Path, default=Path.home() / ".claude")
    args = parser.parse_args()

    config_dir = args.config_dir.expanduser().absolute()
    output = args.output.expanduser().absolute()
    protected = args.protected_config_dir.expanduser().absolute()
    temp_root = Path("/tmp").resolve()
    config_parent = config_dir.parent.resolve()
    if config_parent.parent != temp_root or not config_parent.name.startswith("cc-port-claude-e2e-"):
        raise SystemExit("--config-dir must be inside /tmp/cc-port-claude-e2e-*/")
    if config_dir == protected or config_dir in protected.parents or protected in config_dir.parents:
        raise SystemExit("The isolated and protected Claude config directories overlap.")
    if os.environ.get("WSL_DISTRO_NAME", "").strip() == "":
        raise SystemExit("This live test must run inside one exact WSL distribution.")
    if output.exists():
        raise SystemExit("Refusing to overwrite an existing report.")
    output.parent.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    protected_before = _protected_snapshot(protected)
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "startedAtUtc": _utc_now(),
        "success": False,
        "distro": os.environ["WSL_DISTRO_NAME"],
        "configDir": str(config_dir),
        "protectedConfigDir": str(protected),
        "plugin": f"{args.plugin_id}@{args.marketplace}",
        "source": args.source,
        "steps": [],
    }
    qualified = f"{args.plugin_id}@{args.marketplace}"
    command: tuple[str, ...] | None = None
    cleanup_errors: list[str] = []

    profile = PlatformProfile(
        name="claude-wsl-e2e",
        tool_id="claude-code",
        environment_kind="wsl",
        environment_name=os.environ["WSL_DISTRO_NAME"],
        display_name="CC Port isolated WSL Claude E2E",
        home_dir=str(config_dir.parent),
        enabled=True,
        skills_dir=str(config_dir / "skills"),
        mcp_json="",
        rules_dir="",
        prompts_dir="",
        plugins_dir="",
        instructions_path="",
        memories_dir="",
        settings_path=str(config_dir / "settings.json"),
    )
    installation = PluginInstallation(scope="user", enabled=False)
    spec = PluginSpec(
        track="reference",
        platform="claude-code",
        plugin_id=args.plugin_id,
        origin=PluginOrigin(
            type="marketplace",
            marketplace=args.marketplace,
            source=args.source,
        ),
        installations=[installation],
    )

    try:
        context = claude_cli_context(profile)
        if context is None:
            raise RuntimeError("CC Port did not resolve the real Claude CLI for this WSL profile.")
        command = context.command
        version = _run(command, config_dir, "--version").stdout.strip()
        report["claudeCommand"] = list(command)
        report["claudeVersion"] = version
        report["steps"].append({"name": "resolve-exact-wsl-claude-cli", "ok": True})

        if _marketplaces(command, config_dir):
            raise RuntimeError("The isolated Claude config was not empty before the test.")
        if not marketplace_install_ready(profile, spec):
            raise RuntimeError("CC Port rejected the portable Marketplace source before installation.")
        report["steps"].append({"name": "marketplace-install-ready", "ok": True})

        installed = install_marketplace_plugin(profile, spec, installation)
        state = _plugin_state(command, config_dir, qualified)
        if not installed.marketplace_added or installed.enabled or not state or state.get("enabled") is not False:
            raise RuntimeError("Native Marketplace install/disabled state did not match the plan.")
        report["steps"].append(
            {
                "name": "cc-port-native-install-and-disable",
                "ok": True,
                "marketplaceAdded": installed.marketplace_added,
                "scope": installed.scope,
                "enabled": installed.enabled,
            }
        )

        enabled_installation = installation.model_copy(update={"enabled": True})
        set_marketplace_plugin_enabled(profile, spec, enabled_installation)
        state = _plugin_state(command, config_dir, qualified)
        if not state or state.get("enabled") is not True:
            raise RuntimeError("Native Marketplace enable state did not match the plan.")
        report["steps"].append({"name": "cc-port-native-enable", "ok": True})
        report["installedState"] = {
            "id": str(state.get("id") or state.get("name") or ""),
            "scope": str(state.get("scope") or ""),
            "enabled": bool(state.get("enabled")),
            "version": str(state.get("version") or ""),
        }
        report["success"] = True
    except Exception as exc:  # preserve a concise diagnostic without CLI secrets
        report["failure"] = f"{type(exc).__name__}: {exc}"
    finally:
        if command is not None:
            try:
                if _plugin_state(command, config_dir, qualified) is not None:
                    _run(command, config_dir, "plugin", "uninstall", qualified, "--scope", "user", "-y")
            except Exception as exc:
                cleanup_errors.append(f"plugin-uninstall: {type(exc).__name__}: {exc}")
            try:
                if any(str(item.get("name") or "") == args.marketplace for item in _marketplaces(command, config_dir)):
                    _run(
                        command,
                        config_dir,
                        "plugin",
                        "marketplace",
                        "remove",
                        args.marketplace,
                        "--scope",
                        "user",
                    )
            except Exception as exc:
                cleanup_errors.append(f"marketplace-remove: {type(exc).__name__}: {exc}")
        protected_after = _protected_snapshot(protected)
        report["protectedConfigUnchanged"] = protected_before == protected_after
        report["cleanupErrors"] = cleanup_errors
        if command is not None:
            try:
                report["remainingPlugin"] = _plugin_state(command, config_dir, qualified) is not None
                report["remainingMarketplace"] = any(
                    str(item.get("name") or "") == args.marketplace
                    for item in _marketplaces(command, config_dir)
                )
            except Exception as exc:
                report["postCleanupProbeFailure"] = f"{type(exc).__name__}: {exc}"
        report["finishedAtUtc"] = _utc_now()
        if (
            cleanup_errors
            or not report["protectedConfigUnchanged"]
            or report.get("remainingPlugin")
            or report.get("remainingMarketplace")
        ):
            report["success"] = False
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
