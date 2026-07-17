#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Compatibility wrapper for the cross-platform Python release orchestrator.

.EXAMPLE
    Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1
#>
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (Test-Path -LiteralPath $VenvPython) {
    $Python = (Resolve-Path -LiteralPath $VenvPython).Path
} else {
    $PythonCommand = Get-Command "python.exe", "python" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $PythonCommand -or -not $PythonCommand.Source) {
        throw "Python 3.10+ was not found. Run the release with an activated Python environment."
    }
    $Python = $PythonCommand.Source
}

& $Python (Join-Path $PSScriptRoot "release_desktop.py") @args
exit $LASTEXITCODE
