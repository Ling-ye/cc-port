from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_SCRIPT = ROOT / "scripts" / "release-desktop.ps1"


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
    assert "Remove-Item Env:LPM_DEPENDENCY_CACHE_STATUS" in source


def test_artifacts_are_hashed_before_the_verified_release_is_published() -> None:
    source = _source()

    hash_position = source.index('Description "Hashing verified release artifacts"')
    publish_position = source.index('Description "Publishing verified release"')
    assert hash_position < publish_position


def test_release_treats_oauth_broker_as_an_optional_runtime_feature() -> None:
    source = _source()

    assert "Get-GithubOAuthBrokerBuildStatus" in source
    assert "Assert-GithubOAuthBrokerConfigured" not in source
    assert "BUILTIN_GITHUB_OAUTH_BROKER_URL" in source
    assert 'Description "Checking optional GitHub OAuth broker"' in source
    assert "Packaging will continue" in source
    assert "disabled by default; runtime override supported" in source
