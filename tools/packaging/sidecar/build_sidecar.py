"""Build the ``cc-port-desktop-api`` sidecar binary for the Tauri desktop app.

This script wraps PyInstaller so the produced executable matches the naming
convention required by Tauri's ``bundle.externalBin`` field:

    desktop/src-tauri/binaries/cc-port-desktop-api-{target_triple}{exe_suffix}

Usage::

    # Use the active interpreter and auto-detect target triple
    python tools/packaging/sidecar/build_sidecar.py

    # Pin a specific triple (rarely needed)
    python tools/packaging/sidecar/build_sidecar.py --target x86_64-pc-windows-msvc

    # Override the output directory (used by some CI flows)
    python tools/packaging/sidecar/build_sidecar.py --out custom/path

The script requires ``pyinstaller`` to be available in the current Python
environment. The cleanest setup is::

    pip install -e ".[desktop]"
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT_DIR = ROOT / "desktop" / "src-tauri" / "binaries"
ENTRY_SCRIPT = Path(__file__).with_name("cc_port_desktop_api_entry.py")
SIDECAR_NAME = "cc-port-desktop-api"
EXCLUDED_MODULES = [
    # MCP server runtime is not used by the desktop API.
    "fastmcp",
    "mcp",
    # Developer, notebook, scientific, plotting, and GUI stacks are common in
    # Anaconda environments and can trigger huge or conflicting PyInstaller hooks.
    "IPython",
    "black",
    "docutils",
    "jedi",
    "matplotlib",
    "nbformat",
    "numpy",
    "PIL",
    "PyQt5",
    "PySide6",
    "pytest",
    "scipy",
    "sphinx",
    "tkinter",
    "yapf",
    "zmq",
]


def detect_target_triple() -> str:
    """Best-effort detection of Rust-style target triple for the current host.

    We avoid invoking ``rustc`` so this works even if Rust is not installed
    when only the Python sidecar is being built (e.g. in CI matrix jobs).
    """
    try:
        result = subprocess.run(
            ["rustc", "-vV"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.splitlines():
            if line.startswith("host:"):
                return line.split(":", 1)[1].strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    machine = platform.machine().lower()
    arch = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }.get(machine, machine)

    system = platform.system()
    if system == "Windows":
        return f"{arch}-pc-windows-msvc"
    if system == "Darwin":
        return f"{arch}-apple-darwin"
    if system == "Linux":
        return f"{arch}-unknown-linux-gnu"
    raise RuntimeError(f"Unsupported host platform: {system} {machine}")


def ensure_pyinstaller(python: str) -> None:
    code = subprocess.run(
        [python, "-c", "import PyInstaller"],
        capture_output=True,
        text=True,
    )
    if code.returncode != 0:
        sys.stderr.write(
            "PyInstaller is not installed in the current Python environment.\n"
            "Install it with one of:\n"
            '  pip install -e ".[desktop]"\n'
            "  pip install pyinstaller\n"
        )
        raise SystemExit(1)


def build_pyinstaller_command(python: str, work_root: Path, *, clean: bool) -> list[str]:
    """Return the deterministic PyInstaller command for a sidecar build."""
    command = [
        python,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconfirm",
    ]
    if clean:
        command.append("--clean")
    command.extend(
        [
            "--console",
            "--name",
            SIDECAR_NAME,
            "--distpath",
            str(work_root / "dist"),
            "--workpath",
            str(work_root / "work"),
            "--specpath",
            str(work_root),
            "--paths",
            str(ROOT),
            "--collect-submodules",
            "cc_port",
            "--collect-data",
            "cc_port",
        ]
    )
    for module in EXCLUDED_MODULES:
        command.extend(["--exclude-module", module])
    command.append(str(ENTRY_SCRIPT))
    return command


def build(target_triple: str, out_dir: Path, *, clean: bool) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)

    work_root = ROOT / "build" / "sidecar"
    dist_dir = work_root / "dist"

    if clean and work_root.exists():
        shutil.rmtree(work_root, ignore_errors=True)

    python = sys.executable
    ensure_pyinstaller(python)

    cmd = build_pyinstaller_command(python, work_root, clean=clean)

    print(f"[build_sidecar] python: {python}")
    print(f"[build_sidecar] target: {target_triple}")
    print(f"[build_sidecar] output: {out_dir}")
    print("[build_sidecar] running PyInstaller...")

    completed = subprocess.run(cmd, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)

    exe_suffix = sysconfig.get_config_var("EXE") or ""
    src = dist_dir / f"{SIDECAR_NAME}{exe_suffix}"
    if not src.is_file():
        raise SystemExit(f"PyInstaller did not produce {src}")

    dst = out_dir / f"{SIDECAR_NAME}-{target_triple}{exe_suffix}"
    shutil.copy2(src, dst)
    print(f"[build_sidecar] sidecar ready: {dst} ({dst.stat().st_size / 1024 / 1024:.1f} MB)")
    return dst


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        default=None,
        help="Rust-style target triple (auto-detected by default).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUT_DIR}).",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Reuse existing PyInstaller work directory (faster rebuilds).",
    )
    args = parser.parse_args(argv)

    target = args.target or detect_target_triple()
    build(target, args.out, clean=not args.no_clean)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
