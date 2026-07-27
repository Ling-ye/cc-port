from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "tools" / "packaging" / "sidecar" / "build_sidecar.py"
)
MODULE_SPEC = importlib.util.spec_from_file_location("cc_port_build_sidecar", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
build_sidecar = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(build_sidecar)


def test_build_pyinstaller_command_clean_has_stable_parameters(tmp_path: Path) -> None:
    work_root = tmp_path / "sidecar-cache"
    python = "python-for-sidecar"

    command = build_sidecar.build_pyinstaller_command(python, work_root, clean=True)

    expected = [
        python,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconfirm",
        "--clean",
        "--console",
        "--name",
        build_sidecar.SIDECAR_NAME,
        "--distpath",
        str(work_root / "dist"),
        "--workpath",
        str(work_root / "work"),
        "--specpath",
        str(work_root),
        "--paths",
        str(build_sidecar.ROOT),
        "--collect-submodules",
        "cc_port",
    ]
    for module in build_sidecar.EXCLUDED_MODULES:
        expected.extend(["--exclude-module", module])
    expected.append(str(build_sidecar.ENTRY_SCRIPT))

    assert command == expected


def test_build_pyinstaller_command_no_clean_only_omits_clean(tmp_path: Path) -> None:
    work_root = tmp_path / "sidecar-cache"

    clean_command = build_sidecar.build_pyinstaller_command(
        "python-for-sidecar", work_root, clean=True
    )
    incremental_command = build_sidecar.build_pyinstaller_command(
        "python-for-sidecar", work_root, clean=False
    )

    assert "--clean" not in incremental_command
    assert [argument for argument in clean_command if argument != "--clean"] == incremental_command


@pytest.mark.parametrize(
    ("clean", "sentinel_should_survive"),
    [(True, False), (False, True)],
)
def test_build_applies_clean_policy_and_copies_target_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean: bool,
    sentinel_should_survive: bool,
) -> None:
    root = tmp_path / "repo"
    work_root = root / "build" / "sidecar"
    work_root.mkdir(parents=True)
    sentinel = work_root / "keep-me.txt"
    sentinel.write_text("cached analysis", encoding="utf-8")
    out_dir = tmp_path / "tauri-binaries"
    commands: list[list[str]] = []

    monkeypatch.setattr(build_sidecar, "ROOT", root)
    monkeypatch.setattr(build_sidecar.sys, "executable", "python-for-sidecar")
    monkeypatch.setattr(build_sidecar.sysconfig, "get_config_var", lambda _name: ".exe")
    monkeypatch.setattr(build_sidecar, "ensure_pyinstaller", lambda _python: None)

    def fake_run(command: list[str], *, check: bool) -> SimpleNamespace:
        assert check is False
        commands.append(command)
        dist_dir = Path(command[command.index("--distpath") + 1])
        dist_dir.mkdir(parents=True, exist_ok=True)
        (dist_dir / f"{build_sidecar.SIDECAR_NAME}.exe").write_bytes(b"sidecar")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(build_sidecar.subprocess, "run", fake_run)

    result = build_sidecar.build("x86_64-pc-windows-msvc", out_dir, clean=clean)

    assert sentinel.exists() is sentinel_should_survive
    assert ("--clean" in commands[0]) is clean
    assert result == out_dir / "cc-port-desktop-api-x86_64-pc-windows-msvc.exe"
    assert result.read_bytes() == b"sidecar"
