#!/usr/bin/env powershell
<#
.SYNOPSIS
    Prepare, validate, build, and collect the Windows desktop release.

.EXAMPLE
    Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1
#>
[CmdletBinding()]
param(
    [switch]$NonInteractive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ModulePath = Join-Path $PSScriptRoot "desktop-build.psm1"
Import-Module $ModulePath -Force -ErrorAction Stop

function Invoke-ReleaseStep {
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )

    Write-LpmSection $Description
    Invoke-LpmNative -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory -Description $Description | Out-Null
}

function Remove-KnownTauriOutputs {
    param(
        [Parameter(Mandatory = $true)][string]$TargetReleaseDirectory,
        [Parameter(Mandatory = $true)][string]$DesktopName,
        [Parameter(Mandatory = $true)][string]$SidecarName
    )

    if (-not (Test-Path -LiteralPath $TargetReleaseDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $TargetReleaseDirectory -Force | Out-Null
    }
    $bundleDirectory = Join-Path $TargetReleaseDirectory "bundle"
    Remove-LpmSafePath -Path $bundleDirectory -Parent $TargetReleaseDirectory
    foreach ($name in @("$DesktopName.exe", "$SidecarName.exe")) {
        $path = Join-Path $TargetReleaseDirectory $name
        Assert-LpmDirectChild -Path $path -Parent $TargetReleaseDirectory | Out-Null
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force
        }
    }
}

function Copy-TauriOutputs {
    param(
        [Parameter(Mandatory = $true)][string]$TargetReleaseDirectory,
        [Parameter(Mandatory = $true)][string]$StagingDirectory,
        [Parameter(Mandatory = $true)][string]$DesktopName,
        [Parameter(Mandatory = $true)][string]$SidecarName
    )

    $required = @(
        (Join-Path $TargetReleaseDirectory "$DesktopName.exe"),
        (Join-Path $TargetReleaseDirectory "$SidecarName.exe")
    )
    foreach ($source in $required) {
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Tauri did not produce required artifact: $source"
        }
        Copy-Item -LiteralPath $source -Destination (Join-Path $StagingDirectory ([IO.Path]::GetFileName($source)))
    }

    $bundleDirectory = Join-Path $TargetReleaseDirectory "bundle"
    if (-not (Test-Path -LiteralPath $bundleDirectory -PathType Container)) {
        throw "Tauri bundle directory was not created: $bundleDirectory"
    }
    foreach ($source in Get-ChildItem -LiteralPath $bundleDirectory -Force) {
        Copy-Item -LiteralPath $source.FullName -Destination (Join-Path $StagingDirectory $source.Name) -Recurse
    }
    Get-LpmWindowsPackageArtifacts -ReleaseDirectory $StagingDirectory | Out-Null
}

function Invoke-SidecarSmokeTest {
    param(
        [Parameter(Mandatory = $true)][string]$SidecarPath,
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )

    Write-LpmSection "Smoke testing packaged sidecar"
    $tempRoot = [IO.Path]::GetTempPath().TrimEnd("\")
    $stateHome = Join-Path $tempRoot ("lpm-release-smoke-" + [guid]::NewGuid().ToString("N"))
    Assert-LpmDirectChild -Path $stateHome -Parent $tempRoot | Out-Null
    New-Item -ItemType Directory -Path $stateHome | Out-Null
    $hadPriorStateHome = Test-Path Env:LPM_STATE_HOME
    $priorStateHome = $env:LPM_STATE_HOME
    try {
        $env:LPM_STATE_HOME = $stateHome
        $result = Invoke-LpmNative -FilePath $SidecarPath -ArgumentList @("operation_history_page") -WorkingDirectory $RepoRoot -Capture -Description "packaged sidecar smoke test"
        try {
            $response = $result.Output | ConvertFrom-Json -ErrorAction Stop
        } catch {
            throw "Packaged sidecar returned invalid JSON: $(Get-LpmOutputExcerpt -Value $result.Output)"
        }
        if ($null -eq $response -or -not $response.ok) {
            throw "Packaged sidecar returned an error: $(Get-LpmOutputExcerpt -Value $result.Output)"
        }
        Write-Host "  sidecar response: ok"
    } finally {
        if ($hadPriorStateHome) {
            $env:LPM_STATE_HOME = $priorStateHome
        } else {
            Remove-Item Env:LPM_STATE_HOME -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $stateHome) {
            Remove-LpmSafePath -Path $stateHome -Parent $tempRoot
        }
    }
}

try {
    if ($env:OS -ne "Windows_NT" -or -not [Environment]::Is64BitOperatingSystem) {
        throw "Desktop release supports Windows x64 only."
    }

    $repoRoot = Get-LpmRepoRoot
    $desktopDirectory = Join-Path $repoRoot "desktop"
    $tauriDirectory = Join-Path $desktopDirectory "src-tauri"
    $targetReleaseDirectory = Join-Path $tauriDirectory "target\release"
    $releaseRoot = Join-Path $repoRoot "release\desktop"
    $desktopName = "lpm-desktop"
    $sidecarName = "lpm-desktop-api"
    $expectedTarget = Get-LpmExpectedTarget
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

    Write-LpmSection "Preparing build environment"
    if ($NonInteractive) {
        & (Join-Path $PSScriptRoot "setup.ps1") -NonInteractive
    } else {
        & (Join-Path $PSScriptRoot "setup.ps1")
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Environment preparation failed (exit $LASTEXITCODE)."
    }

    Update-LpmProcessPath
    $node = Get-LpmNode
    $npm = Get-LpmNpm -NodePath $(if ($node) { $node.Path } else { $null })
    $git = Get-LpmGit
    $rust = Get-LpmRustTools
    $visualStudio = Get-LpmVisualStudioPath
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf) -or -not $node -or
        -not $npm -or -not $git -or -not $rust.Cargo -or -not $rust.Rustc -or
        $rust.Target -ne $expectedTarget -or -not $visualStudio) {
        throw "Resolved environment is incomplete after setup. Run scripts/setup.ps1 -CheckOnly for details."
    }

    Add-LpmPathDirectories -Directories @(
        (Split-Path -Parent $venvPython),
        (Split-Path -Parent $node.Path),
        (Split-Path -Parent $npm),
        (Split-Path -Parent $git),
        (Split-Path -Parent $rust.Cargo),
        (Split-Path -Parent $rust.Rustc)
    )
    $env:RUSTUP_TOOLCHAIN = "stable-$expectedTarget"
    Enable-LpmVisualStudioEnvironment -InstallationPath $visualStudio

    Write-LpmSection "Resolved build tools"
    Write-Host "  python : $venvPython"
    Write-Host "  node   : $($node.Path)"
    Write-Host "  npm    : $npm"
    Write-Host "  git    : $git"
    Write-Host "  cargo  : $($rust.Cargo)"
    Write-Host "  rustc  : $($rust.Rustc)"
    Write-Host "  target : $($rust.Target)"
    Write-Host "  msvc   : $visualStudio"

    $windowsPowerShell = Join-Path $PSHOME "powershell.exe"
    if (-not (Test-Path -LiteralPath $windowsPowerShell -PathType Leaf)) {
        throw "Windows PowerShell executable was not found: $windowsPowerShell"
    }
    Invoke-ReleaseStep -Description "Running PowerShell build self-tests" -FilePath $windowsPowerShell -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $repoRoot "tests\test_desktop_build.ps1")
    ) -WorkingDirectory $repoRoot
    Invoke-ReleaseStep -Description "Running Python tests" -FilePath $venvPython -ArgumentList @("-m", "pytest", "-q", "-s") -WorkingDirectory $repoRoot
    Invoke-ReleaseStep -Description "Running Ruff" -FilePath $venvPython -ArgumentList @(
        "-m", "ruff", "check", "src/lpm", "tests", "tools/packaging/sidecar", "tools/packaging/icons"
    ) -WorkingDirectory $repoRoot
    Invoke-ReleaseStep -Description "Running frontend tests" -FilePath $npm -ArgumentList @("test") -WorkingDirectory $desktopDirectory
    Invoke-ReleaseStep -Description "Auditing locked frontend dependencies" -FilePath $npm -ArgumentList @(
        "audit", "--package-lock-only", "--audit-level=moderate"
    ) -WorkingDirectory $desktopDirectory
    Invoke-ReleaseStep -Description "Building frontend" -FilePath $npm -ArgumentList @("run", "build") -WorkingDirectory $desktopDirectory

    $iconPng = Join-Path $tauriDirectory "icons\icon.png"
    $iconIco = Join-Path $tauriDirectory "icons\icon.ico"
    if (-not (Test-Path -LiteralPath $iconPng -PathType Leaf) -or -not (Test-Path -LiteralPath $iconIco -PathType Leaf)) {
        Invoke-ReleaseStep -Description "Generating desktop icons" -FilePath $venvPython -ArgumentList @(
            (Join-Path $repoRoot "tools\packaging\icons\generate_icons.py")
        ) -WorkingDirectory $repoRoot
    } else {
        Write-LpmSection "Desktop icons already exist"
    }

    Invoke-ReleaseStep -Description "Building desktop API sidecar" -FilePath $venvPython -ArgumentList @(
        (Join-Path $repoRoot "tools\packaging\sidecar\build_sidecar.py"),
        "--target", $expectedTarget
    ) -WorkingDirectory $repoRoot

    Write-LpmSection "Cleaning known Tauri outputs"
    Remove-KnownTauriOutputs -TargetReleaseDirectory $targetReleaseDirectory -DesktopName $desktopName -SidecarName $sidecarName
    Invoke-ReleaseStep -Description "Building Tauri MSI and NSIS bundles" -FilePath $npm -ArgumentList @(
        "run", "tauri", "--", "build"
    ) -WorkingDirectory $desktopDirectory

    if (-not (Test-Path -LiteralPath $releaseRoot -PathType Container)) {
        New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
    }
    $finalDirectory = Join-Path $releaseRoot $expectedTarget
    $stagingDirectory = Join-Path $releaseRoot ("." + $expectedTarget + ".staging-" + [guid]::NewGuid().ToString("N"))
    Assert-LpmDirectChild -Path $finalDirectory -Parent $releaseRoot | Out-Null
    Assert-LpmDirectChild -Path $stagingDirectory -Parent $releaseRoot | Out-Null
    New-Item -ItemType Directory -Path $stagingDirectory | Out-Null
    try {
        Write-LpmSection "Collecting release artifacts into staging"
        Copy-TauriOutputs -TargetReleaseDirectory $targetReleaseDirectory -StagingDirectory $stagingDirectory -DesktopName $desktopName -SidecarName $sidecarName
        $stagedSidecar = Join-Path $stagingDirectory "$sidecarName.exe"
        Invoke-SidecarSmokeTest -SidecarPath $stagedSidecar -RepoRoot $repoRoot

        Write-LpmSection "Publishing verified release"
        Publish-LpmStagingDirectory -StagingDirectory $stagingDirectory -FinalDirectory $finalDirectory -ReleaseRoot $releaseRoot
    } finally {
        if (Test-Path -LiteralPath $stagingDirectory) {
            Remove-LpmSafePath -Path $stagingDirectory -Parent $releaseRoot
        }
    }

    $artifacts = @(
        (Get-Item -LiteralPath (Join-Path $finalDirectory "$desktopName.exe")),
        (Get-Item -LiteralPath (Join-Path $finalDirectory "$sidecarName.exe"))
    ) + @(Get-LpmWindowsPackageArtifacts -ReleaseDirectory $finalDirectory)
    Write-LpmSection "Verified release artifacts"
    foreach ($artifact in $artifacts | Sort-Object FullName -Unique) {
        $hash = Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256
        Write-Host "  $($artifact.FullName) ($($artifact.Length) bytes)"
        Write-Host "    SHA-256 $($hash.Hash)"
    }
    Write-Host ""
    Write-Host "Release complete: $finalDirectory" -ForegroundColor Green
    exit 0
} catch {
    Write-Host ""
    Write-Host "Release failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
