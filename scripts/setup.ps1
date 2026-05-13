#!/usr/bin/env pwsh
<#
.SYNOPSIS
    One-shot environment setup for LPM (Python + desktop tooling).

.DESCRIPTION
    Verifies Python / Node / Cargo are available, installs the Python package
    in editable mode (with desktop extras), generates placeholder icons if
    missing, and installs npm dependencies for the desktop app.

    Run this once after cloning the repo. To rebuild the desktop app, use
    scripts/build-desktop.ps1. To run the dev shell, use scripts/dev.ps1.

.EXAMPLE
    pwsh scripts/setup.ps1
    pwsh scripts/setup.ps1 -SkipDesktop      # CLI/MCP only, no Tauri tooling
#>
[CmdletBinding()]
param(
    [switch]$SkipDesktop
)

# Use Continue (not Stop) so external commands writing to stderr don't trip
# PowerShell 5's NativeCommandError. We check $LASTEXITCODE explicitly below.
$ErrorActionPreference = "Continue"

$RepoRoot = (Resolve-Path "$PSScriptRoot/..").Path
Set-Location $RepoRoot

# Run an external command and merge stderr into stdout so PowerShell 5 doesn't
# format stderr lines as red NativeCommandError records. Throws on non-zero
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

function Write-Section([string]$Title) {
    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
}

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# ------------------------------------------------------------------ checks
Write-Section "Checking prerequisites"

if (-not (Test-Command "python")) {
    throw "python not found on PATH. Install Python 3.10+ first."
}
$pyVersion = & python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
Write-Host "  python : $pyVersion"

if (-not $SkipDesktop) {
    if (-not (Test-Command "node")) {
        throw "node not found on PATH. Install Node.js 18+ first (or rerun with -SkipDesktop)."
    }
    Write-Host "  node   : $(node --version)"
    if (-not (Test-Command "npm")) {
        throw "npm not found on PATH."
    }
    Write-Host "  npm    : $(npm --version)"

    if (-not (Test-Command "cargo")) {
        $cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
        if (Test-Path (Join-Path $cargoBin "cargo.exe")) {
            $env:Path = "$cargoBin;$env:Path"
            Write-Host "  (added $cargoBin to PATH for this session)"
        } else {
            throw "cargo not found on PATH. Install Rust via 'winget install Rustlang.Rustup' first."
        }
    }
    Write-Host "  cargo  : $(cargo --version)"
    Write-Host "  rustc  : $(rustc --version)"
}

# ----------------------------------------------------------- python install
Write-Section "Installing LPM Python package"

$extras = if ($SkipDesktop) { "[dev]" } else { "[dev,desktop]" }
Invoke-Step "pip install" { & python -m pip install -e ".$extras" }

# --------------------------------------------------------------- icons
if (-not $SkipDesktop) {
    Write-Section "Ensuring desktop icons exist"
    $iconFile = Join-Path $RepoRoot "desktop/src-tauri/icons/icon.ico"
    if (Test-Path $iconFile) {
        Write-Host "  icons already present at desktop/src-tauri/icons/"
    } else {
        Invoke-Step "generate_icons.py" {
            & python "$RepoRoot/packaging/icons/generate_icons.py"
        }
    }

    Write-Section "Installing desktop npm dependencies"
    Push-Location (Join-Path $RepoRoot "desktop")
    try {
        Invoke-Step "npm install" { & npm install }
    } finally {
        Pop-Location
    }
}

Write-Section "Setup complete"
Write-Host "Next steps:"
if ($SkipDesktop) {
    Write-Host "  lpm doctor         # verify CLI"
    Write-Host "  lpm platforms      # list configured platforms"
} else {
    Write-Host "  pwsh scripts/dev.ps1            # run desktop in dev mode"
    Write-Host "  pwsh scripts/build-desktop.ps1  # produce installer + exe"
}
