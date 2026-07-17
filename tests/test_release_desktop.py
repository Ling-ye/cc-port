from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "release_desktop.py"
SPEC = importlib.util.spec_from_file_location("lpm_release_desktop", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
release_desktop = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_desktop
SPEC.loader.exec_module(release_desktop)


def test_parse_rust_host() -> None:
    output = "rustc 1.88.0\nbinary: rustc\nhost: x86_64-pc-windows-msvc\n"

    assert release_desktop.parse_rust_host(output) == "x86_64-pc-windows-msvc"

    with pytest.raises(release_desktop.ReleaseError, match="host target"):
        release_desktop.parse_rust_host("rustc 1.88.0\n")


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        ("v20.18.1", False),
        ("v20.19.0", True),
        ("v22.11.0", False),
        ("v22.12.0", True),
        ("v24.1.0", True),
        ("invalid", False),
    ],
)
def test_node_version_gate(version: str, supported: bool) -> None:
    assert release_desktop.node_version_supported(version) is supported


def test_validate_windows_packages_requires_msi_and_nsis(tmp_path: Path) -> None:
    msi = tmp_path / "msi" / "LPM Desktop_0.1.0_x64_en-US.msi"
    msi.parent.mkdir()
    msi.write_bytes(b"msi")

    with pytest.raises(release_desktop.ReleaseError, match="NSIS"):
        release_desktop.platform_package_artifacts(tmp_path, "Windows")

    nsis = tmp_path / "nsis" / "LPM Desktop_0.1.0_x64-setup.exe"
    nsis.parent.mkdir()
    nsis.write_bytes(b"nsis")

    assert release_desktop.platform_package_artifacts(tmp_path, "Windows") == [msi, nsis]


@pytest.mark.parametrize(
    ("system", "filename"),
    [
        ("Darwin", "LPM Desktop_0.1.0_aarch64.dmg"),
        ("Linux", "lpm-desktop_0.1.0_amd64.AppImage"),
    ],
)
def test_validate_non_windows_package(tmp_path: Path, system: str, filename: str) -> None:
    artifact = tmp_path / filename
    artifact.write_bytes(b"bundle")

    assert release_desktop.platform_package_artifacts(tmp_path, system) == [artifact]


def test_publish_staging_replaces_prior_verified_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(release_desktop, "RELEASE_ROOT", tmp_path)
    final_dir = tmp_path / "x86_64-test"
    staging = tmp_path / ".x86_64-test.staging"
    final_dir.mkdir()
    staging.mkdir()
    (final_dir / "artifact.txt").write_text("old", encoding="utf-8")
    (staging / "artifact.txt").write_text("new", encoding="utf-8")

    release_desktop._publish_staging(staging, final_dir)

    assert (final_dir / "artifact.txt").read_text(encoding="utf-8") == "new"
    assert not staging.exists()
    assert not list(tmp_path.glob(".*.backup-*"))


def test_publish_staging_restores_prior_release_when_swap_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(release_desktop, "RELEASE_ROOT", tmp_path)
    final_dir = tmp_path / "x86_64-test"
    staging = tmp_path / ".x86_64-test.staging"
    final_dir.mkdir()
    staging.mkdir()
    (final_dir / "artifact.txt").write_text("old", encoding="utf-8")
    (staging / "artifact.txt").write_text("new", encoding="utf-8")
    original_rename = Path.rename

    def fail_staging_rename(path: Path, target: Path) -> Path:
        if path == staging:
            raise OSError("simulated publish failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_staging_rename)

    with pytest.raises(OSError, match="simulated publish failure"):
        release_desktop._publish_staging(staging, final_dir)

    assert (final_dir / "artifact.txt").read_text(encoding="utf-8") == "old"
    assert (staging / "artifact.txt").read_text(encoding="utf-8") == "new"
