#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Run the CC Port desktop app in development mode.

.DESCRIPTION
    Builds the cc-port-desktop-api sidecar and the public cc-port CLI/MCP agent
    binaries first (so the bundled app works end-to-end), then uses
    `npm run tauri dev` as an internal step to start Vite, compile the Tauri
    shell with Cargo, and open the desktop window.

.EXAMPLE
    pwsh scripts/dev.ps1
    pwsh scripts/dev.ps1 -SkipSidecar    # reuse an existing sidecar
    pwsh scripts/dev.ps1 -SkipAgent      # reuse an existing CLI/MCP agent
#>
[CmdletBinding()]
param(
    [switch]$SkipSidecar,
    [switch]$SkipAgent
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

function Stop-ExistingDesktopDevServer {
    $DesktopRoot = (Join-Path $RepoRoot "desktop")
    $Listeners = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort 1420 -State Listen -ErrorAction SilentlyContinue
    foreach ($Listener in $Listeners) {
        $ProcessInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $($Listener.OwningProcess)" -ErrorAction SilentlyContinue
        if ($null -eq $ProcessInfo) {
            continue
        }

        $CommandLine = [string]$ProcessInfo.CommandLine
        $IsThisRepoVite =
            $CommandLine.Contains($DesktopRoot) -and
            $CommandLine.Contains("vite") -and
            $CommandLine.Contains("--host 127.0.0.1")

        if (-not $IsThisRepoVite) {
            throw "Port 1420 is already in use by PID $($Listener.OwningProcess): $CommandLine"
        }

        Write-Host "Stopping existing CC Port Vite dev server on port 1420 (PID $($Listener.OwningProcess))" -ForegroundColor Yellow
        Stop-Process -Id $Listener.OwningProcess -Force
        Start-Sleep -Milliseconds 500
    }
}

if (-not (Get-Command "cargo" -ErrorAction SilentlyContinue)) {
    $cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
    if (Test-Path (Join-Path $cargoBin "cargo.exe")) {
        $env:Path = "$cargoBin;$env:Path"
    }
}

if (-not $SkipSidecar) {
    Write-Host "==> Building cc-port-desktop-api sidecar" -ForegroundColor Cyan
    Invoke-Step "sidecar build" {
        & python "$RepoRoot/tools/packaging/sidecar/build_sidecar.py"
    }
}

if (-not $SkipAgent) {
    Write-Host "==> Building public cc-port CLI/MCP agent" -ForegroundColor Cyan
    Invoke-Step "agent build" {
        & python "$RepoRoot/tools/packaging/agent/build_agent.py"
    }
}

Write-Host ""
Write-Host "==> Starting Tauri dev shell" -ForegroundColor Cyan
Stop-ExistingDesktopDevServer
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
