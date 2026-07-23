#!/usr/bin/env powershell
<#
.SYNOPSIS
    Configure secrets, test, and deploy the GitHub OAuth broker to workers.dev.

.DESCRIPTION
    LPM_GITHUB_OAUTH_CLIENT_ID and LPM_GITHUB_OAUTH_CLIENT_SECRET must be set
    in the current maintainer shell. Values are piped to Wrangler and are never
    placed in process arguments or written to repository files.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$workerRoot = Join-Path $repoRoot "services\github-oauth-worker"
$clientId = [Environment]::GetEnvironmentVariable("LPM_GITHUB_OAUTH_CLIENT_ID")
$clientSecret = [Environment]::GetEnvironmentVariable("LPM_GITHUB_OAUTH_CLIENT_SECRET")

if ([string]::IsNullOrWhiteSpace($clientId)) {
    throw "Set LPM_GITHUB_OAUTH_CLIENT_ID in this maintainer shell before deploying."
}
if ([string]::IsNullOrWhiteSpace($clientSecret)) {
    throw "Set LPM_GITHUB_OAUTH_CLIENT_SECRET in this maintainer shell before deploying."
}

$pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
$corepack = Get-Command corepack -ErrorAction SilentlyContinue
if ($pnpm) {
    $runner = $pnpm.Source
    $prefix = @()
} elseif ($corepack) {
    $runner = $corepack.Source
    $prefix = @("pnpm")
} else {
    throw "pnpm or corepack is required to deploy the OAuth Worker."
}

function Invoke-Pnpm {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

    & $runner @prefix @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "pnpm command failed (exit $LASTEXITCODE)."
    }
}

Push-Location $workerRoot
try {
    Invoke-Pnpm install --frozen-lockfile
    Invoke-Pnpm run typecheck
    Invoke-Pnpm test
    Invoke-Pnpm exec wrangler whoami

    $clientId | & $runner @prefix exec wrangler secret put GITHUB_OAUTH_CLIENT_ID
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to configure GITHUB_OAUTH_CLIENT_ID."
    }
    $clientSecret | & $runner @prefix exec wrangler secret put GITHUB_OAUTH_CLIENT_SECRET
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to configure GITHUB_OAUTH_CLIENT_SECRET."
    }

    $deployOutput = @(& $runner @prefix exec wrangler deploy 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Worker deployment failed (exit $LASTEXITCODE).`n$($deployOutput -join [Environment]::NewLine)"
    }
    $deployOutput | ForEach-Object { Write-Host $_ }
    $joined = $deployOutput -join [Environment]::NewLine
    $urlMatch = [regex]::Match($joined, 'https://[A-Za-z0-9.-]+\.workers\.dev')
    if (-not $urlMatch.Success) {
        throw "Worker deployed, but its workers.dev URL could not be detected from Wrangler output."
    }
    $brokerUrl = $urlMatch.Value.TrimEnd("/")
    Write-Host ""
    Write-Host "GitHub OAuth callback URL: $brokerUrl/oauth/callback"
    Write-Host "Desktop broker URL:        $brokerUrl"
    Write-Host ""
    Write-Host "Set the GitHub OAuth App callback URL, then copy the broker URL into"
    Write-Host "BUILTIN_GITHUB_OAUTH_BROKER_URL before running release-desktop.ps1."
} finally {
    Pop-Location
    $clientId = $null
    $clientSecret = $null
}
