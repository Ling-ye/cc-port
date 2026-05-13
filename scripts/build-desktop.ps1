#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Build the LPM Desktop app for distribution (executable + installers).

.DESCRIPTION
    Steps:
      1. Generate placeholder icons if missing.
      2. Build the lpm-desktop-api sidecar binary via PyInstaller.
      3. Run `npm run tauri build` which compiles the Rust shell, packages the
         frontend, and produces NSIS / MSI installers on Windows (or the
         platform-equivalent bundles on macOS / Linux).

    Run scripts/setup.ps1 once before invoking this.

.EXAMPLE
    pwsh scripts/build-desktop.ps1
    pwsh scripts/build-desktop.ps1 -SkipSidecar     # reuse existing sidecar exe
#>
[CmdletBinding()]
param(
    [switch]$SkipSidecar,
    [switch]$SkipIcons
)

# Use Continue (not Stop): external commands writing to stderr (PyInstaller
# WARNING lines, npm progress, ...) would otherwise be treated as terminating
# errors by PowerShell 5. We check $LASTEXITCODE manually instead.
$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path "$PSScriptRoot/..").Path

function Write-Section([string]$Title) {
    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
}

# Run an external command and merge stderr into stdout so PowerShell 5 doesn't
# render stderr lines as red NativeCommandError records. Throws on non-zero
# exit code.
function Invoke-Step {
    param(
        [Parameter(Mandatory)][string]$Description,
        [Parameter(Mandatory)][scriptblock]$Action
    )
    & $Action 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) {
        throw "${Description} failed (exit $LASTEXITCODE)"
    }
}

function Get-TargetTriple {
    $hostLine = (& rustc -vV 2>$null | Where-Object { $_ -like "host:*" } | Select-Object -First 1)
    if ($hostLine) {
        return (($hostLine -split ":", 2)[1]).Trim()
    }
    return "unknown-target"
}

if (-not (Get-Command "cargo" -ErrorAction SilentlyContinue)) {
    $cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
    if (Test-Path (Join-Path $cargoBin "cargo.exe")) {
        $env:Path = "$cargoBin;$env:Path"
    } else {
        throw "cargo not found. Install Rust via 'winget install Rustlang.Rustup' first."
    }
}

if (-not $SkipIcons) {
    Write-Section "Ensuring desktop icons exist"
    $iconFile = Join-Path $RepoRoot "desktop/src-tauri/icons/icon.ico"
    if (-not (Test-Path $iconFile)) {
        Invoke-Step "generate_icons.py" {
            & python "$RepoRoot/tools/packaging/icons/generate_icons.py"
        }
    } else {
        Write-Host "  icons already present"
    }
}

if (-not $SkipSidecar) {
    Write-Section "Building lpm-desktop-api sidecar"
    Invoke-Step "sidecar build" {
        & python "$RepoRoot/tools/packaging/sidecar/build_sidecar.py"
    }
}

Write-Section "Building Tauri app (release)"
Push-Location (Join-Path $RepoRoot "desktop")
try {
    Invoke-Step "tauri build" { & npm run tauri build }
} finally {
    Pop-Location
}

Write-Section "Build complete"
$releaseDir = Join-Path $RepoRoot "desktop/src-tauri/target/release"
$bundleDir  = Join-Path $releaseDir "bundle"
$targetTriple = Get-TargetTriple
$artifactDir = Join-Path $RepoRoot "dist/desktop/$targetTriple"
New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null

Write-Host "  Executable : $releaseDir\lpm-desktop.exe"
$exePath = Join-Path $releaseDir "lpm-desktop.exe"
if (Test-Path $exePath) {
    Copy-Item -LiteralPath $exePath -Destination $artifactDir -Force
}
if (Test-Path $bundleDir) {
    Copy-Item -Path (Join-Path $bundleDir "*") -Destination $artifactDir -Recurse -Force
    Get-ChildItem $bundleDir -Recurse -File | ForEach-Object {
        Write-Host ("  Bundle     : " + $_.FullName)
    }
}
Write-Host "  Collected  : $artifactDir"
