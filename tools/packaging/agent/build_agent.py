"""Build the public ``cc-port`` CLI/MCP executable for the Tauri bundle."""

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
ENTRY_SCRIPT = Path(__file__).with_name("cc_port_agent_entry.py")
AGENT_NAME = "cc-port"
EXCLUDED_MODULES = [
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
    result = subprocess.run(
        [python, "-c", "import PyInstaller"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write('Install the desktop build dependencies with: pip install -e ".[desktop]"\n')
        raise SystemExit(1)


def build_pyinstaller_command(python: str, work_root: Path, *, clean: bool) -> list[str]:
    command = [python, "-m", "PyInstaller", "--onefile", "--noconfirm"]
    if clean:
        command.append("--clean")
    command.extend(
        [
            "--console",
            "--name",
            AGENT_NAME,
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
            "--collect-submodules",
            "fastmcp",
            "--collect-submodules",
            "mcp",
            "--copy-metadata",
            "fastmcp",
            "--copy-metadata",
            "mcp",
        ]
    )
    for module in EXCLUDED_MODULES:
        command.extend(["--exclude-module", module])
    command.append(str(ENTRY_SCRIPT))
    return command


def build(target_triple: str, out_dir: Path, *, clean: bool) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    work_root = ROOT / "build" / "agent"
    if clean and work_root.exists():
        shutil.rmtree(work_root, ignore_errors=True)
    python = sys.executable
    ensure_pyinstaller(python)
    completed = subprocess.run(
        build_pyinstaller_command(python, work_root, clean=clean),
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    exe_suffix = sysconfig.get_config_var("EXE") or ""
    source = work_root / "dist" / f"{AGENT_NAME}{exe_suffix}"
    if not source.is_file():
        raise SystemExit(f"PyInstaller did not produce {source}")
    destination = out_dir / f"{AGENT_NAME}-{target_triple}{exe_suffix}"
    shutil.copy2(source, destination)
    print(
        f"[build_agent] agent ready: {destination} "
        f"({destination.stat().st_size / 1024 / 1024:.1f} MB)"
    )
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--no-clean", action="store_true")
    args = parser.parse_args(argv)
    build(
        args.target or detect_target_triple(),
        args.out,
        clean=not args.no_clean,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
