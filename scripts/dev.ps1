#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Run the LPM Desktop app in development mode.

.DESCRIPTION
    Builds the lpm-desktop-api sidecar binary first (so the bundled app works
    end-to-end), then uses `npm run tauri dev` as an internal step to start
    Vite, compile the Tauri shell with Cargo, and open the desktop window.

.EXAMPLE
    pwsh scripts/dev.ps1
    pwsh scripts/dev.ps1 -SkipSidecar    # only re-run if sidecar already built
#>
[CmdletBinding()]
param(
    [switch]$SkipSidecar
)

# Use Continue (not Stop): external commands writing to stderr (e.g.
# PyInstaller WARNING lines) would otherwise be treated as terminating
# errors by PowerShell 5. We check $LASTEXITCODE manually instead.
$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path "$PSScriptRoot/..").Path
$PreviousBrowser = $env:BROWSER
$env:BROWSER = "none"

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

if (-not (Get-Command "cargo" -ErrorAction SilentlyContinue)) {
    $cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
    if (Test-Path (Join-Path $cargoBin "cargo.exe")) {
        $env:Path = "$cargoBin;$env:Path"
    }
}

if (-not $SkipSidecar) {
    Write-Host "==> Building lpm-desktop-api sidecar" -ForegroundColor Cyan
    Invoke-Step "sidecar build" {
        & python "$RepoRoot/tools/packaging/sidecar/build_sidecar.py"
    }
}

Write-Host ""
Write-Host "==> Starting Tauri dev shell" -ForegroundColor Cyan
Push-Location (Join-Path $RepoRoot "desktop")
try {
    Invoke-Step "tauri dev" { & npm run tauri dev }
} finally {
    Pop-Location
    if ($null -eq $PreviousBrowser) {
        Remove-Item Env:\BROWSER -ErrorAction SilentlyContinue
    } else {
        $env:BROWSER = $PreviousBrowser
    }
}
