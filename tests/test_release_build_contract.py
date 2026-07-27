from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
RELEASE_SCRIPT = ROOT / "scripts" / "release-desktop.ps1"
SETUP_SCRIPT = ROOT / "scripts" / "setup.ps1"
PYPROJECT = ROOT / "pyproject.toml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _source() -> str:
    return RELEASE_SCRIPT.read_text(encoding="utf-8")


def test_release_exposes_verified_cache_and_clean_contract() -> None:
    source = _source()

    assert "[switch]$Clean" in source
    assert "[switch]$NonInteractive" in source
    setup_calls = re.findall(
        r'&\s+\(Join-Path\s+\$PSScriptRoot\s+"setup\.ps1"\)([^\r\n]*)',
        source,
    )
    assert setup_calls == [" -NonInteractive -ForceSync", " -NonInteractive"]
    assert "build\\cache\\sidecar.json" in source
    assert "build\\metrics" in source


def test_frontend_production_build_has_one_owner() -> None:
    source = _source()

    assert 'Description "Building frontend"' not in source
    assert '"run", "tauri", "--", "build"' in source


def test_release_python_environment_pins_and_probes_xdist() -> None:
    manifest = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    setup_source = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert "pytest-xdist==3.8.0" in manifest["project"]["optional-dependencies"]["dev"]
    assert "import PIL, PyInstaller, cc_port, pytest, xdist" in setup_source


def test_release_runs_pytest_with_dynamic_xdist_workers() -> None:
    source = _source()

    assert "$pytestWorkerCount = Get-ReleasePytestWorkerCount" in source
    assert (
        '"-m", "pytest", "-q", "-s", "-n", [string]$pytestWorkerCount, '
        '"--dist", "load"'
    ) in source


def test_verified_sidecar_is_staged_even_when_tauri_cache_skips_its_copy() -> None:
    source = _source()

    stage_position = source.index('Description "Staging verified Tauri sidecar"')
    tauri_position = source.index('Description "Building Tauri MSI and NSIS bundles"')
    assert stage_position < tauri_position
    assert "Copy-Item -LiteralPath $sourceSidecar -Destination $targetSidecar" in source


def test_warm_release_preserves_cargo_binary_and_validates_sidecar_hash() -> None:
    source = _source()

    assert "Remove-KnownTauriOutputs" in source
    assert "-Clean:$Clean" in source
    assert "Assert-SidecarHashesMatch" in source
    assert "Get-FileHash" in source


def test_release_records_parallel_gate_logs_and_final_metrics() -> None:
    source = _source()

    assert "Invoke-ParallelReleaseGates" in source
    assert "Start-Job" in source
    assert "MaximumConcurrency" in source
    assert "Save-ReleaseMetrics" in source
    assert "finally" in source
    assert ".ToArray()" in source


def test_release_serializes_publishers_and_bounds_gate_waits() -> None:
    source = _source()

    assert "Enter-ReleaseLock" in source
    assert "[IO.FileShare]::None" in source
    assert "$script:ReleaseLock.Dispose()" in source
    assert "GateTimeoutSeconds" in source
    assert "-ExitCode 124" in source


def test_incomplete_sidecar_cache_is_a_miss_and_environment_is_restored() -> None:
    source = _source()

    assert '$record.PSObject.Properties["fingerprint"]' in source
    assert '$record.PSObject.Properties["artifactSha256"]' in source
    assert "missing-or-invalid-record" in source
    assert "$script:HadPriorDependencyCacheStatus" in source
    assert "Remove-Item Env:CC_PORT_DEPENDENCY_CACHE_STATUS" in source


def test_artifacts_are_hashed_before_the_verified_release_is_published() -> None:
    source = _source()

    hash_position = source.index('Description "Hashing verified release artifacts"')
    publish_position = source.index('Description "Publishing verified release"')
    assert hash_position < publish_position


def test_release_generates_a_two_file_public_upload_bundle() -> None:
    source = _source()

    assert "Get-CcPortReleaseVersion" in source
    assert "Publish-CcPortPublicReleaseBundle" in source
    assert 'Join-Path $repoRoot "release\\publish"' in source
    assert "cc-port_${Version}_windows_x64_setup.exe" in (
        ROOT / "scripts" / "desktop-build.psm1"
    ).read_text(encoding="utf-8")
    assert "SHA256SUMS.txt" in source
    assert "Uploading GitHub Release" not in source
    assert "signtool" not in source.lower()


def test_release_remaps_rust_build_paths_only_around_tauri_build() -> None:
    source = _source()

    enter_position = source.index("Enter-CcPortRustPathRemapping")
    tauri_position = source.index('Description "Building Tauri MSI and NSIS bundles"')
    exit_position = source.index("Exit-CcPortRustPathRemapping")
    privacy_position = source.index('Description "Checking packaged binaries for host paths"')

    assert enter_position < tauri_position < exit_position < privacy_position
    assert "Assert-CcPortBinaryOmitsHostPaths" in source
    assert "CARGO_ENCODED_RUSTFLAGS" in (
        ROOT / "scripts" / "desktop-build.psm1"
    ).read_text(encoding="utf-8")
    assert "--remap-path-prefix=" in (
        ROOT / "scripts" / "desktop-build.psm1"
    ).read_text(encoding="utf-8")


def test_release_declares_external_git_and_has_no_oauth_broker_gate() -> None:
    source = _source()

    assert "Git Credential Manager required at runtime" in source
    assert "Get-GithubOAuthBrokerBuildStatus" not in source
    assert "BUILTIN_GITHUB_OAUTH_BROKER_URL" not in source
    assert "GitHub OAuth broker" not in source


def test_ci_scans_full_git_history_and_markdown_links() -> None:
    source = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "fetch-depth: 0" in source
    assert "GITLEAKS_VERSION: \"8.30.1\"" in source
    assert (
        "gitleaks git . --config .gitleaks.toml --redact --log-opts=--all"
        in source
    )
    assert "Test (includes Markdown link validation)" in source
    assert "Verify acknowledged glib advisory is absent from Windows target" in source
    assert "--target x86_64-pc-windows-msvc" in source
    assert "glib v0.18.5" in source


def test_tauri_ci_stages_external_bin_placeholder_before_cargo_check() -> None:
    source = CI_WORKFLOW.read_text(encoding="utf-8")
    placeholder_step = "Stage Tauri sidecar placeholder"
    cargo_check = "cargo check --manifest-path desktop/src-tauri/Cargo.toml"
    placeholder_start = source.index(placeholder_step)
    cargo_check_start = source.index(cargo_check)
    placeholder_block = source[placeholder_start:cargo_check_start]

    assert (
        '$sidecarPath = "desktop/src-tauri/binaries/'
        'cc-port-desktop-api-x86_64-pc-windows-msvc.exe"'
        in placeholder_block
    )
    assert (
        "New-Item -ItemType Directory -Path "
        "(Split-Path -Parent $sidecarPath) -Force | Out-Null"
        in placeholder_block
    )
    assert (
        "New-Item -ItemType File -Path $sidecarPath -Force | Out-Null"
        in placeholder_block
    )
