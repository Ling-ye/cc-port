"""Validate, build, collect, and verify the LPM desktop release.

This is the authoritative cross-platform release orchestrator. It runs on
Windows, macOS, and Linux, but it only builds bundles for the current host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_DIR = ROOT / "desktop"
TAURI_DIR = DESKTOP_DIR / "src-tauri"
TAURI_RELEASE_DIR = TAURI_DIR / "target" / "release"
RELEASE_ROOT = ROOT / "release" / "desktop"
SIDECAR_NAME = "lpm-desktop-api"
DESKTOP_NAME = "lpm-desktop"
TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ReleaseError(RuntimeError):
    """A release gate or build step failed."""


@dataclass(frozen=True)
class Toolchain:
    python: Path
    node: Path
    npm: Path
    git: Path
    cargo: Path
    rustc: Path
    target: str
    env: dict[str, str]


def parse_rust_host(output: str) -> str:
    """Extract and validate the host target from ``rustc -vV`` output."""
    for line in output.splitlines():
        if line.startswith("host:"):
            target = line.split(":", 1)[1].strip()
            if target and TARGET_RE.fullmatch(target):
                return target
            break
    raise ReleaseError("rustc did not report a valid host target triple.")


def node_version_supported(value: str) -> bool:
    """Return whether a Node.js version satisfies Vite's minimum version."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    if not match:
        return False
    major, minor, _patch = (int(part) for part in match.groups())
    return (major == 20 and minor >= 19) or (major == 22 and minor >= 12) or major > 22


def platform_package_artifacts(release_dir: Path, system: str) -> list[Path]:
    """Find and validate the required installer artifacts for *system*."""
    files = sorted(
        (path for path in release_dir.rglob("*") if path.is_file()),
        key=lambda path: path.as_posix().casefold(),
    )
    if system == "Windows":
        msi = [path for path in files if path.suffix.lower() == ".msi"]
        nsis = [path for path in files if path.name.lower().endswith("-setup.exe")]
        if not msi:
            raise ReleaseError("Windows release did not produce an MSI installer.")
        if not nsis:
            raise ReleaseError("Windows release did not produce an NSIS installer.")
        return msi + nsis
    if system == "Darwin":
        dmg = [path for path in files if path.suffix.lower() == ".dmg"]
        if not dmg:
            raise ReleaseError("macOS release did not produce a DMG installer.")
        return dmg
    if system == "Linux":
        packages = [
            path
            for path in files
            if path.name.lower().endswith((".deb", ".appimage", ".rpm"))
        ]
        if not packages:
            raise ReleaseError("Linux release did not produce a DEB, AppImage, or RPM bundle.")
        return packages
    raise ReleaseError(f"Unsupported release host: {system}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _existing_path(value: str | Path | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_file():
        return path.resolve()
    return None


def resolve_executable(
    description: str,
    names: Sequence[str],
    fallbacks: Iterable[str | Path | None] = (),
) -> Path:
    """Resolve a build tool from PATH, then explicit fallback paths."""
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return Path(resolved).resolve()
    for candidate in fallbacks:
        resolved = _existing_path(candidate)
        if resolved:
            return resolved
    raise ReleaseError(f"Required build tool was not found: {description}")


def _windows_tool_fallbacks() -> dict[str, list[Path | None]]:
    local = Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else None
    program_files = Path(os.environ["ProgramFiles"]) if os.environ.get("ProgramFiles") else None
    repo_drive = Path(f"{ROOT.drive}\\") if ROOT.drive else None
    home = Path.home()
    return {
        "node": [
            local / "Programs/nodejs/node.exe" if local else None,
            program_files / "nodejs/node.exe" if program_files else None,
        ],
        "npm": [
            local / "Programs/nodejs/npm.cmd" if local else None,
            program_files / "nodejs/npm.cmd" if program_files else None,
        ],
        "git": [
            _existing_path(os.environ.get("LPM_GIT_EXECUTABLE")),
            program_files / "Git/cmd/git.exe" if program_files else None,
            local / "Programs/Git/cmd/git.exe" if local else None,
            repo_drive / "Git/cmd/git.exe" if repo_drive else None,
        ],
        "cargo": [home / ".cargo/bin/cargo.exe"],
        "rustc": [home / ".cargo/bin/rustc.exe"],
    }


def _prepend_path(env: dict[str, str], executables: Iterable[Path]) -> None:
    directories: list[str] = []
    seen: set[str] = set()
    for executable in executables:
        directory = str(executable.parent)
        key = os.path.normcase(directory)
        if key not in seen:
            directories.append(directory)
            seen.add(key)
    current = env.get("PATH", "")
    env["PATH"] = os.pathsep.join(directories + ([current] if current else []))


def _capture(command: Sequence[str | Path], *, env: dict[str, str]) -> str:
    result = subprocess.run(
        [str(item) for item in command],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReleaseError(f"Command failed ({result.returncode}): {detail}")
    return result.stdout.strip()


def resolve_toolchain() -> Toolchain:
    """Resolve all native tools before mutating dependencies or outputs."""
    windows = platform.system() == "Windows"
    fallbacks = _windows_tool_fallbacks() if windows else {}
    python = Path(sys.executable).resolve()
    node = resolve_executable("Node.js", ["node.exe", "node"], fallbacks.get("node", []))
    npm = resolve_executable("npm", ["npm.cmd", "npm"], fallbacks.get("npm", []))
    git = resolve_executable("Git", ["git.exe", "git"], fallbacks.get("git", []))
    cargo = resolve_executable("Cargo", ["cargo.exe", "cargo"], fallbacks.get("cargo", []))
    rustc = resolve_executable("rustc", ["rustc.exe", "rustc"], fallbacks.get("rustc", []))
    env = os.environ.copy()
    _prepend_path(env, [python, node, npm, git, cargo, rustc])

    node_version = _capture([node, "--version"], env=env)
    if not node_version_supported(node_version):
        raise ReleaseError(
            f"Unsupported Node.js {node_version!r}; use Node.js 20.19+ or 22.12+."
        )
    target = parse_rust_host(_capture([rustc, "-vV"], env=env))
    return Toolchain(
        python=python,
        node=node,
        npm=npm,
        git=git,
        cargo=cargo,
        rustc=rustc,
        target=target,
        env=env,
    )


def _display_command(command: Sequence[str | Path]) -> str:
    return " ".join(
        f'"{item}"' if any(char.isspace() for char in str(item)) else str(item)
        for item in command
    )


def run_step(
    description: str,
    command: Sequence[str | Path],
    *,
    cwd: Path,
    env: dict[str, str],
) -> None:
    print(f"\n==> {description}", flush=True)
    print(f"  $ {_display_command(command)}", flush=True)
    result = subprocess.run(
        [str(item) for item in command],
        cwd=cwd,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseError(f"{description} failed (exit {result.returncode}).")


def _safe_remove_tree(path: Path, *, parent: Path) -> None:
    resolved = path.resolve(strict=False)
    expected_parent = parent.resolve(strict=False)
    if resolved.parent != expected_parent or not resolved.name:
        raise ReleaseError(f"Refusing to remove unexpected directory: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _prepare_tauri_outputs() -> None:
    """Remove known outputs while preserving Cargo compilation caches."""
    TAURI_RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    _safe_remove_tree(TAURI_RELEASE_DIR / "bundle", parent=TAURI_RELEASE_DIR)
    suffix = ".exe" if platform.system() == "Windows" else ""
    for name in (f"{DESKTOP_NAME}{suffix}", f"{SIDECAR_NAME}{suffix}"):
        artifact = TAURI_RELEASE_DIR / name
        if artifact.is_file() or artifact.is_symlink():
            artifact.unlink()


def _copy_tauri_outputs(staging: Path, *, system: str) -> list[Path]:
    suffix = ".exe" if system == "Windows" else ""
    required_sources = [
        TAURI_RELEASE_DIR / f"{DESKTOP_NAME}{suffix}",
        TAURI_RELEASE_DIR / f"{SIDECAR_NAME}{suffix}",
    ]
    for source in required_sources:
        if not source.is_file():
            raise ReleaseError(f"Tauri did not produce required artifact: {source}")
        shutil.copy2(source, staging / source.name)

    bundle_dir = TAURI_RELEASE_DIR / "bundle"
    if not bundle_dir.is_dir():
        raise ReleaseError(f"Tauri bundle directory was not created: {bundle_dir}")
    for source in bundle_dir.iterdir():
        destination = staging / source.name
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)

    packages = platform_package_artifacts(staging, system)
    return [staging / source.name for source in required_sources] + packages


def _smoke_sidecar(sidecar: Path, *, env: dict[str, str]) -> None:
    print("\n==> Smoke testing packaged sidecar", flush=True)
    with tempfile.TemporaryDirectory(prefix="lpm-release-smoke-") as state_home:
        smoke_env = env.copy()
        smoke_env["LPM_STATE_HOME"] = state_home
        result = subprocess.run(
            [str(sidecar), "operation_history_page"],
            cwd=ROOT,
            env=smoke_env,
            text=True,
            capture_output=True,
            check=False,
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReleaseError(f"Packaged sidecar smoke test failed: {detail}")
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseError("Packaged sidecar returned invalid JSON.") from exc
    if not isinstance(response, dict) or not response.get("ok"):
        raise ReleaseError(f"Packaged sidecar returned an error: {result.stdout.strip()}")


def _publish_staging(staging: Path, final_dir: Path) -> None:
    """Replace the prior verified release, restoring it if replacement fails."""
    if staging.parent.resolve() != RELEASE_ROOT.resolve():
        raise ReleaseError(f"Unexpected staging directory: {staging}")
    if final_dir.parent.resolve() != RELEASE_ROOT.resolve():
        raise ReleaseError(f"Unexpected final release directory: {final_dir}")
    backup = RELEASE_ROOT / f".{final_dir.name}.backup-{uuid.uuid4().hex}"
    moved_old = False
    try:
        if final_dir.exists():
            final_dir.rename(backup)
            moved_old = True
        staging.rename(final_dir)
    except Exception:
        if moved_old and backup.exists() and not final_dir.exists():
            backup.rename(final_dir)
        raise
    if backup.exists():
        _safe_remove_tree(backup, parent=RELEASE_ROOT)


def build_release(toolchain: Toolchain) -> Path:
    python = toolchain.python
    npm = toolchain.npm
    env = toolchain.env

    run_step(
        "Installing Python release dependencies",
        [python, "-m", "pip", "install", "--disable-pip-version-check", "--quiet", "-e", ".[dev,desktop]"],
        cwd=ROOT,
        env=env,
    )
    run_step(
        "Installing locked frontend dependencies",
        [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=DESKTOP_DIR,
        env=env,
    )
    run_step("Running Python tests", [python, "-m", "pytest", "-q", "-s"], cwd=ROOT, env=env)
    run_step(
        "Running Ruff",
        [python, "-m", "ruff", "check", "src/lpm", "tests", "scripts/release_desktop.py"],
        cwd=ROOT,
        env=env,
    )
    run_step("Running frontend tests", [npm, "test"], cwd=DESKTOP_DIR, env=env)
    run_step(
        "Auditing frontend dependency tree",
        [npm, "audit", "--audit-level=moderate"],
        cwd=DESKTOP_DIR,
        env=env,
    )
    run_step("Building frontend", [npm, "run", "build"], cwd=DESKTOP_DIR, env=env)

    icon_dir = TAURI_DIR / "icons"
    if not (icon_dir / "icon.png").is_file() or not (icon_dir / "icon.ico").is_file():
        run_step(
            "Generating desktop icons",
            [python, ROOT / "tools/packaging/icons/generate_icons.py"],
            cwd=ROOT,
            env=env,
        )
    else:
        print("\n==> Desktop icons already exist", flush=True)

    run_step(
        "Building desktop API sidecar",
        [
            python,
            ROOT / "tools/packaging/sidecar/build_sidecar.py",
            "--target",
            toolchain.target,
        ],
        cwd=ROOT,
        env=env,
    )
    _prepare_tauri_outputs()
    run_step(
        "Building Tauri release bundles",
        [npm, "run", "tauri", "--", "build"],
        cwd=DESKTOP_DIR,
        env=env,
    )

    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)
    final_dir = RELEASE_ROOT / toolchain.target
    staging = RELEASE_ROOT / f".{toolchain.target}.staging-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        staged_artifacts = _copy_tauri_outputs(staging, system=platform.system())
        sidecar = next(path for path in staged_artifacts if path.name.startswith(SIDECAR_NAME))
        _smoke_sidecar(sidecar, env=env)
        relative_artifacts = [path.relative_to(staging) for path in staged_artifacts]
        _publish_staging(staging, final_dir)
    finally:
        if staging.exists():
            _safe_remove_tree(staging, parent=RELEASE_ROOT)

    artifacts = [final_dir / path for path in relative_artifacts]
    print("\n==> Verified release artifacts", flush=True)
    for artifact in artifacts:
        print(
            f"  {artifact} ({artifact.stat().st_size} bytes)\n"
            f"    SHA-256 {sha256_file(artifact)}",
            flush=True,
        )
    return final_dir


def _print_toolchain(toolchain: Toolchain) -> None:
    print("==> Resolved build tools")
    print(f"  python : {toolchain.python}")
    print(f"  node   : {toolchain.node}")
    print(f"  npm    : {toolchain.npm}")
    print(f"  git    : {toolchain.git}")
    print(f"  cargo  : {toolchain.cargo}")
    print(f"  rustc  : {toolchain.rustc}")
    print(f"  target : {toolchain.target}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        toolchain = resolve_toolchain()
        _print_toolchain(toolchain)
        output = build_release(toolchain)
    except ReleaseError as exc:
        print(f"\nRelease failed: {exc}", file=sys.stderr, flush=True)
        return 1
    except OSError as exc:
        print(f"\nRelease failed: {exc}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print("\nRelease interrupted.", file=sys.stderr, flush=True)
        return 130
    print(f"\nRelease complete: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
