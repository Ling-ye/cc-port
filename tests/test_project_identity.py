from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

import cc_port
from cc_port.core.config import (
    CONFIG_ENV_VAR,
    CONFIG_PATH_ENV_VAR,
    DEFAULT_CONFIG_RELATIVE,
    GIT_EXECUTABLE_ENV_VAR,
    RESOURCE_HOME_ENV_VAR,
    STATE_HOME_ENV_VAR,
)
from cc_port.core.ownership import MANAGED_MARKER
from cc_port.core.resource_detection import MANIFEST_FILENAMES
from cc_port.services.install_planner import MANIFEST_FILENAMES as PLANNER_MANIFEST_FILENAMES
from cc_port.services.linker import CC_PORT_LINK_MARKER, CC_PORT_RULE_FILENAME

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.5.4"
HISTORICAL_IDENTITY_ALLOWLIST = {
    "docs/adr/0003-rename-project-identity-to-cc-port.md",
    "docs/specs/project-identity-rename.md",
}


def test_distribution_package_commands_and_version_share_one_identity() -> None:
    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = manifest["project"]

    assert project["name"] == "cc-port"
    assert project["version"] == EXPECTED_VERSION
    assert cc_port.__version__ == EXPECTED_VERSION
    assert project["scripts"] == {
        "cc-port": "cc_port.interfaces.cli:app",
        "cc-port-mcp": "cc_port.interfaces.mcp_server:main",
        "cc-port-desktop-api": "cc_port.interfaces.desktop_api:main",
    }
    assert manifest["tool"]["setuptools"]["packages"]["find"]["include"] == ["cc_port*"]
    assert project["urls"] == {
        "Homepage": "https://github.com/Ling-ye/cc-port",
        "Issues": "https://github.com/Ling-ye/cc-port/issues",
    }


def test_desktop_and_skill_manifests_share_one_identity() -> None:
    desktop_manifest = json.loads((ROOT / "desktop/package.json").read_text(encoding="utf-8"))
    desktop_lock = json.loads((ROOT / "desktop/package-lock.json").read_text(encoding="utf-8"))
    cargo_manifest = tomllib.loads(
        (ROOT / "desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    )
    cargo_lock = tomllib.loads(
        (ROOT / "desktop/src-tauri/Cargo.lock").read_text(encoding="utf-8")
    )
    tauri_manifest = json.loads(
        (ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
    )
    skill_frontmatter = yaml.safe_load(
        (ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
    )

    assert desktop_manifest["name"] == "cc-port-desktop"
    assert desktop_manifest["version"] == EXPECTED_VERSION
    assert desktop_lock["name"] == desktop_manifest["name"]
    assert desktop_lock["version"] == EXPECTED_VERSION
    assert desktop_lock["packages"][""]["name"] == desktop_manifest["name"]
    assert desktop_lock["packages"][""]["version"] == EXPECTED_VERSION

    assert cargo_manifest["package"]["name"] == "cc-port-desktop"
    assert cargo_manifest["package"]["version"] == EXPECTED_VERSION
    assert cargo_manifest["lib"]["name"] == "cc_port_desktop_lib"
    cargo_package = next(
        package
        for package in cargo_lock["package"]
        if package["name"] == cargo_manifest["package"]["name"]
    )
    assert cargo_package["version"] == EXPECTED_VERSION

    assert tauri_manifest["productName"] == "CC Port"
    assert tauri_manifest["version"] == EXPECTED_VERSION
    assert tauri_manifest["identifier"] == "com.lingye.cc-port.desktop"
    assert tauri_manifest["app"]["windows"][0]["title"] == "CC Port"
    assert tauri_manifest["bundle"]["externalBin"] == [
        "binaries/cc-port-desktop-api",
        "binaries/cc-port",
    ]

    assert skill_frontmatter["name"] == "cc-port"
    assert set(skill_frontmatter) == {"name", "description"}
    assert (ROOT / "desktop/src/types/cc-port.ts").is_file()
    assert (ROOT / "tools/packaging/sidecar/cc_port_desktop_api_entry.py").is_file()
    assert (ROOT / "tools/packaging/agent/cc_port_agent_entry.py").is_file()
    assert not (ROOT / "package-lock.json").exists()


def test_runtime_state_and_ownership_names_use_the_new_identity() -> None:
    assert CONFIG_ENV_VAR == "CC_PORT_GITHUB_TOKEN"
    assert CONFIG_PATH_ENV_VAR == "CC_PORT_CONFIG"
    assert RESOURCE_HOME_ENV_VAR == "CC_PORT_RESOURCE_HOME"
    assert STATE_HOME_ENV_VAR == "CC_PORT_STATE_HOME"
    assert GIT_EXECUTABLE_ENV_VAR == "CC_PORT_GIT_EXECUTABLE"
    assert DEFAULT_CONFIG_RELATIVE == Path(".config/cc-port/config.toml")
    assert MANAGED_MARKER == ".cc-port-managed.json"
    assert MANIFEST_FILENAMES == {"cc-port.resource.json", "cc-port-resource.json"}
    assert set(PLANNER_MANIFEST_FILENAMES) == MANIFEST_FILENAMES
    assert CC_PORT_RULE_FILENAME == "cc-port-skills.md"
    assert CC_PORT_LINK_MARKER == ".cc-port-linked"


def test_repository_source_text_contains_no_obsolete_tokens() -> None:
    obsolete_tokens = {
        "".join(("l", "p", "m")),
        "".join(("lingye", "plugin", "marketplace")),
        "".join(("lingye", "-", "plugin", "-", "marketplace")),
        "".join(("lingye", "_", "plugin", "_", "marketplace")),
        " ".join(("lingye", "plugin", "marketplace")),
    }
    git_paths = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout

    offenders: list[str] = []
    for raw_relative_path in git_paths.split(b"\0"):
        if not raw_relative_path:
            continue
        relative_path = raw_relative_path.decode("utf-8")
        normalized_relative_path = relative_path.casefold()
        if any(token in normalized_relative_path for token in obsolete_tokens):
            offenders.append(f"{relative_path} (path)")

        if relative_path in HISTORICAL_IDENTITY_ALLOWLIST:
            continue
        path = ROOT / relative_path
        if not path.is_file():
            continue
        contents = path.read_bytes()
        if b"\0" in contents:
            continue
        try:
            normalized_contents = contents.decode("utf-8").casefold()
        except UnicodeDecodeError:
            continue
        if any(token in normalized_contents for token in obsolete_tokens):
            offenders.append(f"{relative_path} (contents)")

    assert offenders == []
    assert not (ROOT / "src" / "".join(("l", "p", "m"))).exists()
