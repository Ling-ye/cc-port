#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Validate, package, collect, and verify the Windows desktop release.

.DESCRIPTION
    This is the single entry point to run after updating code. It installs the
    locked frontend dependencies, runs Python and frontend checks, performs a
    full sidecar/Tauri build, verifies that the collected artifacts are fresh,
    and prints their SHA-256 hashes.

.EXAMPLE
    Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ReleaseStartedAt = Get-Date

function Write-Section([string]$Title) {
    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
}

function Resolve-Executable {
    param(
        [Parameter(Mandatory)][string[]]$Names,
        [string[]]$Fallbacks = @()
    )

    foreach ($name in $Names) {
        $command = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command -and $command.Source) {
            return $command.Source
        }
    }
    foreach ($candidate in $Fallbacks) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Required command not found: $($Names -join ', ')"
}

function Add-ExecutableDirectoryToPath([string]$Executable) {
    $directory = Split-Path -Parent $Executable
    $normalizedDirectory = $directory.TrimEnd("\")
    $pathEntries = $env:Path -split ";" | Where-Object {
        $_ -and $_.TrimEnd("\") -ne $normalizedDirectory
    }
    $env:Path = (@($directory) + $pathEntries) -join ";"
}

function Invoke-NativeStep {
    param(
        [Parameter(Mandatory)][string]$Description,
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $RepoRoot
    )

    Write-Section $Description
    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Description failed (exit $LASTEXITCODE)"
        }
    } finally {
        Pop-Location
    }
}

function Get-TargetTriple([string]$Rustc) {
    $versionOutput = & $Rustc -vV
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read the Rust target triple."
    }
    $hostLine = $versionOutput | Where-Object { $_ -like "host:*" } | Select-Object -First 1
    if (-not $hostLine) {
        throw "Rust did not report a host target triple."
    }
    return (($hostLine -split ":", 2)[1]).Trim()
}

function Get-FreshArtifact {
    param(
        [Parameter(Mandatory)][string]$Description,
        [Parameter(Mandatory)][string]$Path,
        [string]$Filter = "*"
    )

    $artifact = Get-ChildItem -LiteralPath $Path -Filter $Filter -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $artifact) {
        throw "$Description was not generated in $Path"
    }
    if ($artifact.LastWriteTime -lt $ReleaseStartedAt.AddSeconds(-2)) {
        throw "$Description is stale: $($artifact.FullName)"
    }
    return $artifact
}

Set-Location $RepoRoot

$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Python = if (Test-Path -LiteralPath $venvPython) {
    (Resolve-Path -LiteralPath $venvPython).Path
} else {
    Resolve-Executable -Names @("python.exe", "python")
}
$Npm = Resolve-Executable -Names @("npm.cmd", "npm") -Fallbacks @(
    (Join-Path $env:LOCALAPPDATA "Programs\nodejs\npm.cmd"),
    (Join-Path $env:ProgramFiles "nodejs\npm.cmd")
)
$Cargo = Resolve-Executable -Names @("cargo.exe", "cargo") -Fallbacks @(
    (Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe")
)
$Git = Resolve-Executable -Names @("git.exe", "git") -Fallbacks @(
    (Join-Path $env:ProgramFiles "Git\cmd\git.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Git\cmd\git.exe"),
    (Join-Path (Split-Path $RepoRoot -Qualifier) "Git\cmd\git.exe")
)

Add-ExecutableDirectoryToPath $Python
Add-ExecutableDirectoryToPath $Npm
Add-ExecutableDirectoryToPath $Cargo
Add-ExecutableDirectoryToPath $Git
$Rustc = Resolve-Executable -Names @("rustc.exe", "rustc") -Fallbacks @(
    (Join-Path $env:USERPROFILE ".cargo\bin\rustc.exe")
)
$TargetTriple = Get-TargetTriple $Rustc

Write-Section "Resolved build tools"
Write-Host "  python : $Python"
Write-Host "  npm    : $Npm"
Write-Host "  git    : $Git"
Write-Host "  cargo  : $Cargo"
Write-Host "  rustc  : $Rustc"
Write-Host "  target : $TargetTriple"

Invoke-NativeStep -Description "Installing Python release dependencies" `
    -FilePath $Python -Arguments @("-m", "pip", "install", "--disable-pip-version-check", "--quiet", "-e", ".[dev,desktop]")
Invoke-NativeStep -Description "Installing locked frontend dependencies" `
    -FilePath $Npm -Arguments @(
        "ci",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund"
    ) -WorkingDirectory (Join-Path $RepoRoot "desktop")
Invoke-NativeStep -Description "Running Python tests" `
    -FilePath $Python -Arguments @("-m", "pytest", "-q", "-s")
Invoke-NativeStep -Description "Running Ruff" `
    -FilePath $Python -Arguments @("-m", "ruff", "check", "src/lpm", "tests")
Invoke-NativeStep -Description "Running frontend tests" `
    -FilePath $Npm -Arguments @("test") -WorkingDirectory (Join-Path $RepoRoot "desktop")
Invoke-NativeStep -Description "Auditing production frontend dependencies" `
    -FilePath $Npm -Arguments @("audit", "--omit=dev") -WorkingDirectory (Join-Path $RepoRoot "desktop")

Write-Section "Building and collecting desktop release"
& (Join-Path $RepoRoot "scripts\build-desktop.ps1")
if (-not $?) {
    throw "Desktop release build failed."
}

$ReleaseDir = Join-Path $RepoRoot "release\desktop\$TargetTriple"
if (-not (Test-Path -LiteralPath $ReleaseDir)) {
    throw "Release directory was not created: $ReleaseDir"
}

$Artifacts = @(
    Get-FreshArtifact -Description "Desktop executable" -Path $ReleaseDir -Filter "lpm-desktop.exe"
    Get-FreshArtifact -Description "Desktop API sidecar" -Path $ReleaseDir -Filter "lpm-desktop-api.exe"
    Get-FreshArtifact -Description "MSI installer" -Path (Join-Path $ReleaseDir "msi") -Filter "*.msi"
    Get-FreshArtifact -Description "NSIS installer" -Path (Join-Path $ReleaseDir "nsis") -Filter "*-setup.exe"
)

Write-Section "Verified release artifacts"
$Artifacts | Select-Object FullName, Length, LastWriteTime | Format-Table -AutoSize

Write-Section "SHA-256"
$Artifacts | Get-FileHash -Algorithm SHA256 | Format-Table Path, Hash -AutoSize

Write-Section "Smoke testing packaged sidecar"
$SidecarArtifact = $Artifacts | Where-Object { $_.Name -eq "lpm-desktop-api.exe" } | Select-Object -First 1
if (-not $SidecarArtifact) {
    throw "Collected sidecar artifact was not found."
}
$PreviousStateHome = $env:LPM_STATE_HOME
$SmokeState = Join-Path $env:TEMP "lpm-release-smoke-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $SmokeState | Out-Null
try {
    $env:LPM_STATE_HOME = $SmokeState
    $SmokeOutput = & $SidecarArtifact.FullName "operation_history_page"
    if ($LASTEXITCODE -ne 0) {
        throw "Packaged sidecar smoke test failed (exit $LASTEXITCODE): $SmokeOutput"
    }
    $SmokeResponse = ($SmokeOutput -join [Environment]::NewLine) | ConvertFrom-Json
    if (-not $SmokeResponse.ok) {
        throw "Packaged sidecar returned an error: $SmokeOutput"
    }
    Write-Host "  operation_history_page: ok" -ForegroundColor Green
} finally {
    $env:LPM_STATE_HOME = $PreviousStateHome
    Remove-Item -LiteralPath $SmokeState -Force -ErrorAction SilentlyContinue
}

Write-Section "Release complete"
Write-Host "  Output: $ReleaseDir" -ForegroundColor Green
