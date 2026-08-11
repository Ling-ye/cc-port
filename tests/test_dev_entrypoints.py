from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL_DEV = ROOT / "scripts" / "dev.ps1"
BASH_DEV = ROOT / "scripts" / "dev.sh"


def test_powershell_dev_builds_both_external_binaries_before_tauri() -> None:
    source = POWERSHELL_DEV.read_text(encoding="utf-8")

    sidecar_build = source.index("tools/packaging/sidecar/build_sidecar.py")
    agent_build = source.index("tools/packaging/agent/build_agent.py")
    tauri_dev = source.rindex('Invoke-Step "tauri dev"')

    assert sidecar_build < tauri_dev
    assert agent_build < tauri_dev
    assert "[switch]$SkipSidecar" in source
    assert "[switch]$SkipAgent" in source
    assert "if (-not $SkipSidecar)" in source
    assert "if (-not $SkipAgent)" in source


def test_bash_dev_builds_both_external_binaries_before_tauri() -> None:
    source = BASH_DEV.read_text(encoding="utf-8")

    sidecar_build = source.index("tools/packaging/sidecar/build_sidecar.py")
    agent_build = source.index("tools/packaging/agent/build_agent.py")
    tauri_dev = source.rindex("npm run tauri dev")

    assert sidecar_build < tauri_dev
    assert agent_build < tauri_dev
    assert "--skip-sidecar) SKIP_SIDECAR=1" in source
    assert "--skip-agent) SKIP_AGENT=1" in source
    assert '[[ "${SKIP_SIDECAR}" -eq 0 ]]' in source
    assert '[[ "${SKIP_AGENT}" -eq 0 ]]' in source
