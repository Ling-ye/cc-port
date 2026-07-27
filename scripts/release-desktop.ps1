#!/usr/bin/env powershell
<#
.SYNOPSIS
    Prepare, validate, build, and collect the Windows desktop release.

.EXAMPLE
    Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1
#>
[CmdletBinding()]
param(
    [switch]$NonInteractive,
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ModulePath = Join-Path $PSScriptRoot "desktop-build.psm1"
Import-Module $ModulePath -Force -ErrorAction Stop

$script:ReleaseStartedAtUtc = [DateTime]::UtcNow
$script:ReleaseRunId = [guid]::NewGuid().ToString("N").Substring(0, 8)
$script:ReleasePhases = New-Object System.Collections.Generic.List[object]
$script:ReleaseArtifacts = New-Object System.Collections.Generic.List[object]
$script:PendingReleaseArtifacts = @()
$script:ReleaseMetricsPath = $null
$script:ReleaseSucceeded = $false
$script:ReleaseError = $null
$script:ReleaseLock = $null
$script:HadPriorDependencyCacheStatus = Test-Path Env:CC_PORT_DEPENDENCY_CACHE_STATUS
$script:PriorDependencyCacheStatus = $env:CC_PORT_DEPENDENCY_CACHE_STATUS

function Add-ReleasePhase {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][DateTime]$StartedAtUtc,
        [Parameter(Mandatory = $true)][double]$DurationMs,
        [Parameter(Mandatory = $true)][int]$ExitCode,
        [AllowNull()][string]$CacheStatus,
        [AllowNull()][string]$LogPath
    )

    $script:ReleasePhases.Add([pscustomobject][ordered]@{
        name         = $Name
        startedAtUtc = $StartedAtUtc.ToString("o")
        durationMs   = [Math]::Round($DurationMs, 2)
        exitCode     = $ExitCode
        recovered    = $false
        recoveryAttempted = $false
        cacheStatus  = $CacheStatus
        logPath      = $LogPath
        detail       = $null
    })
}

function Start-ReleasePhaseRecovery {
    param(
        [Parameter(Mandatory = $true)][int]$PhaseIndex,
        [Parameter(Mandatory = $true)][string]$CacheStatus,
        [Parameter(Mandatory = $true)][string]$Detail
    )

    if ($PhaseIndex -lt 0 -or $PhaseIndex -ge $script:ReleasePhases.Count) {
        throw "Release phase index is out of range: $PhaseIndex"
    }
    $phase = $script:ReleasePhases[$PhaseIndex]
    $phase.recoveryAttempted = $true
    $phase.recovered = $false
    $phase.cacheStatus = $CacheStatus
    $phase.detail = $Detail
}

function Complete-ReleasePhaseRecovery {
    param([Parameter(Mandatory = $true)][int]$PhaseIndex)

    if ($PhaseIndex -lt 0 -or $PhaseIndex -ge $script:ReleasePhases.Count) {
        throw "Release phase index is out of range: $PhaseIndex"
    }
    if (-not $script:ReleasePhases[$PhaseIndex].recoveryAttempted) {
        throw "Release phase recovery was not started: $PhaseIndex"
    }
    $script:ReleasePhases[$PhaseIndex].recovered = $true
}

function Invoke-ReleaseAction {
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [AllowNull()][string]$CacheStatus
    )

    Write-CcPortSection $Description
    $startedAt = [DateTime]::UtcNow
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    try {
        & $Action | Out-Null
        $stopwatch.Stop()
        Add-ReleasePhase -Name $Description -StartedAtUtc $startedAt -DurationMs $stopwatch.Elapsed.TotalMilliseconds -ExitCode 0 -CacheStatus $CacheStatus -LogPath $null
    } catch {
        $stopwatch.Stop()
        Add-ReleasePhase -Name $Description -StartedAtUtc $startedAt -DurationMs $stopwatch.Elapsed.TotalMilliseconds -ExitCode 1 -CacheStatus $CacheStatus -LogPath $null
        throw
    }
}

function Invoke-ReleaseStep {
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [AllowNull()][string]$CacheStatus
    )

    Write-CcPortSection $Description
    $startedAt = [DateTime]::UtcNow
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    $recorded = $false
    try {
        $result = Invoke-CcPortNative -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory -AllowFailure -Description $Description
        $stopwatch.Stop()
        Add-ReleasePhase -Name $Description -StartedAtUtc $startedAt -DurationMs $stopwatch.Elapsed.TotalMilliseconds -ExitCode $result.ExitCode -CacheStatus $CacheStatus -LogPath $null
        $recorded = $true
        if ($result.ExitCode -ne 0) {
            throw "$Description failed (exit $($result.ExitCode))."
        }
    } catch {
        $stopwatch.Stop()
        if (-not $recorded) {
            Add-ReleasePhase -Name $Description -StartedAtUtc $startedAt -DurationMs $stopwatch.Elapsed.TotalMilliseconds -ExitCode 1 -CacheStatus $CacheStatus -LogPath $null
        }
        throw
    }
}

function Get-ReleasePytestWorkerCount {
    param([int]$ProcessorCount = [Environment]::ProcessorCount)

    return [Math]::Max(1, [Math]::Min(4, $ProcessorCount))
}

function Invoke-ParallelReleaseGates {
    param(
        [Parameter(Mandatory = $true)][object[]]$Gates,
        [Parameter(Mandatory = $true)][string]$LogDirectory,
        [int]$MaximumConcurrency = 4,
        [int]$GateTimeoutSeconds = 900
    )

    if (-not (Test-Path -LiteralPath $LogDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
    }
    $limit = [Math]::Max(1, [Math]::Min($MaximumConcurrency, [Environment]::ProcessorCount))
    $pending = New-Object 'System.Collections.Generic.Queue[object]'
    foreach ($gate in $Gates) {
        $pending.Enqueue($gate)
    }
    $active = @{}
    $failures = New-Object System.Collections.Generic.List[string]
    $worker = {
        param([string]$Payload)

        $spec = $Payload | ConvertFrom-Json
        $startedAt = [DateTime]::UtcNow
        $stopwatch = [Diagnostics.Stopwatch]::StartNew()
        $exitCode = 1
        $output = ""
        try {
            Set-Location -LiteralPath ([string]$spec.workingDirectory) -ErrorAction Stop
            $arguments = @($spec.argumentList | ForEach-Object { [string]$_ })
            $priorPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                $lines = @(& ([string]$spec.filePath) @arguments 2>&1)
                $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
                $output = ($lines | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
            } finally {
                $ErrorActionPreference = $priorPreference
            }
        } catch {
            $exitCode = 1
            $output = $_.Exception.ToString()
        } finally {
            $stopwatch.Stop()
        }
        [pscustomobject]@{
            startedAtUtc = $startedAt.ToString("o")
            durationMs   = $stopwatch.Elapsed.TotalMilliseconds
            exitCode     = $exitCode
            output       = $output
        }
    }

    Write-CcPortSection "Running release quality gates (max $limit concurrent)"
    try {
        while ($pending.Count -gt 0 -or $active.Count -gt 0) {
            while ($pending.Count -gt 0 -and $active.Count -lt $limit) {
                $gate = $pending.Dequeue()
                $payload = [ordered]@{
                    filePath         = $gate.FilePath
                    argumentList     = @($gate.ArgumentList)
                    workingDirectory = $gate.WorkingDirectory
                } | ConvertTo-Json -Depth 4 -Compress
                $job = Start-Job -ScriptBlock $worker -ArgumentList $payload
                $active[$job.Id] = [pscustomobject]@{
                    Job = $job
                    Gate = $gate
                    StartedAtUtc = [DateTime]::UtcNow
                }
                Write-Host "  START $($gate.Name)"
            }

            $finished = Wait-Job -Job @($active.Values | ForEach-Object { $_.Job }) -Any -Timeout 1
            if ($null -eq $finished) {
                $now = [DateTime]::UtcNow
                $expired = @($active.Values | Where-Object {
                    ($now - $_.StartedAtUtc).TotalSeconds -ge $GateTimeoutSeconds
                })
                foreach ($timedOut in $expired) {
                    Stop-Job -Job $timedOut.Job -ErrorAction SilentlyContinue
                    $gate = $timedOut.Gate
                    $safeName = [regex]::Replace($gate.Name.ToLowerInvariant(), '[^a-z0-9.-]+', '-')
                    $logPath = Join-Path $LogDirectory ($safeName.Trim('-') + ".log")
                    $message = "Gate exceeded the $GateTimeoutSeconds second release timeout."
                    [IO.File]::WriteAllText($logPath, $message, [Text.UTF8Encoding]::new($false))
                    $durationMs = ($now - $timedOut.StartedAtUtc).TotalMilliseconds
                    Add-ReleasePhase -Name $gate.Name -StartedAtUtc $timedOut.StartedAtUtc -DurationMs $durationMs -ExitCode 124 -CacheStatus $null -LogPath $logPath
                    $failures.Add("$($gate.Name) (timeout, log $logPath)")
                    Write-Host "  TIMEOUT $($gate.Name) -> $logPath" -ForegroundColor Red
                    Remove-Job -Job $timedOut.Job -Force -ErrorAction SilentlyContinue
                    $active.Remove($timedOut.Job.Id)
                }
                continue
            }
            $entry = $active[$finished.Id]
            $gate = $entry.Gate
            $received = @(Receive-Job -Job $finished)
            $result = if ($received.Count -gt 0) { $received[-1] } else { $null }
            $safeName = [regex]::Replace($gate.Name.ToLowerInvariant(), '[^a-z0-9.-]+', '-')
            $logPath = Join-Path $LogDirectory ($safeName.Trim('-') + ".log")
            if ($null -eq $result) {
                $result = [pscustomobject]@{
                    startedAtUtc = [DateTime]::UtcNow.ToString("o")
                    durationMs   = 0
                    exitCode     = 1
                    output       = "Gate worker returned no result. state=$($finished.State)"
                }
            }
            [IO.File]::WriteAllText($logPath, [string]$result.output, [Text.UTF8Encoding]::new($false))
            $startedAt = [DateTime]::Parse([string]$result.startedAtUtc).ToUniversalTime()
            Add-ReleasePhase -Name $gate.Name -StartedAtUtc $startedAt -DurationMs ([double]$result.durationMs) -ExitCode ([int]$result.exitCode) -CacheStatus $null -LogPath $logPath
            if ([int]$result.exitCode -eq 0) {
                Write-Host ("  PASS  {0} ({1:N2}s) -> {2}" -f $gate.Name, ([double]$result.durationMs / 1000), $logPath) -ForegroundColor Green
            } else {
                $failures.Add("$($gate.Name) (exit $($result.exitCode), log $logPath)")
                Write-Host ("  FAIL  {0} ({1:N2}s) -> {2}" -f $gate.Name, ([double]$result.durationMs / 1000), $logPath) -ForegroundColor Red
                Write-Host (Get-CcPortOutputExcerpt -Value ([string]$result.output))
            }
            Remove-Job -Job $finished -Force
            $active.Remove($finished.Id)
        }
    } finally {
        foreach ($entry in @($active.Values)) {
            Stop-Job -Job $entry.Job -ErrorAction SilentlyContinue
            Remove-Job -Job $entry.Job -Force -ErrorAction SilentlyContinue
        }
    }
    if ($failures.Count -gt 0) {
        throw "Release quality gates failed: $($failures -join '; ')"
    }
}

function Save-ReleaseMetrics {
    if ([string]::IsNullOrWhiteSpace($script:ReleaseMetricsPath)) {
        return
    }
    $payload = [ordered]@{
        runId        = $script:ReleaseRunId
        startedAtUtc = $script:ReleaseStartedAtUtc.ToString("o")
        finishedAtUtc = [DateTime]::UtcNow.ToString("o")
        durationMs   = [Math]::Round(([DateTime]::UtcNow - $script:ReleaseStartedAtUtc).TotalMilliseconds, 2)
        mode         = $(if ($Clean) { "clean" } else { "reuse" })
        success      = $script:ReleaseSucceeded
        error        = $script:ReleaseError
        phases       = $script:ReleasePhases.ToArray()
        artifacts    = $script:ReleaseArtifacts.ToArray()
    }
    Write-CcPortJsonCacheAtomically -Path $script:ReleaseMetricsPath -Value $payload
}

function Write-ReleaseSummary {
    Write-CcPortSection "Release timing summary"
    foreach ($phase in $script:ReleasePhases) {
        $status = if ($phase.recovered) {
            "RCVR"
        } elseif ([int]$phase.exitCode -eq 0) {
            "PASS"
        } else {
            "FAIL"
        }
        $cache = if ([string]::IsNullOrWhiteSpace([string]$phase.cacheStatus)) {
            ""
        } else {
            " cache=$($phase.cacheStatus)"
        }
        $log = if ([string]::IsNullOrWhiteSpace([string]$phase.logPath)) {
            ""
        } else {
            " log=$($phase.logPath)"
        }
        Write-Host ("  {0,-4} {1,8:N2}s  {2}{3}{4}" -f $status, ([double]$phase.durationMs / 1000), $phase.name, $cache, $log)
    }
    $totalSeconds = ([DateTime]::UtcNow - $script:ReleaseStartedAtUtc).TotalSeconds
    Write-Host ("  TOTAL {0:N2}s  mode={1} success={2}" -f $totalSeconds, $(if ($Clean) { "clean" } else { "reuse" }), $script:ReleaseSucceeded)
    if (-not [string]::IsNullOrWhiteSpace($script:ReleaseError)) {
        Write-Host "  error: $($script:ReleaseError)" -ForegroundColor Red
    }
}

function Enter-ReleaseLock {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [IO.Path]::GetFullPath($Path)
    $parent = [IO.Path]::GetDirectoryName($fullPath)
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    try {
        $stream = [IO.File]::Open(
            $fullPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
    } catch [IO.IOException] {
        throw "Another desktop release is already using this repository. lock=$fullPath"
    }
    try {
        $stream.SetLength(0)
        $value = "pid=$PID startedAtUtc=$([DateTime]::UtcNow.ToString('o'))"
        $bytes = [Text.Encoding]::UTF8.GetBytes($value)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush()
        return $stream
    } catch {
        $stream.Dispose()
        throw
    }
}

function Get-SidecarInputFingerprint {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$Target
    )

    $sourceFiles = @(
        Get-ChildItem -LiteralPath (Join-Path $RepoRoot "src\cc_port") -Recurse -File -Force | Where-Object {
            $_.FullName -notmatch '\\__pycache__\\' -and $_.Extension -notin @(".pyc", ".pyo")
        }
        Get-ChildItem -LiteralPath (Join-Path $RepoRoot "tools\packaging\sidecar") -File -Filter "*.py" -Force
        Get-Item -LiteralPath (Join-Path $RepoRoot "pyproject.toml")
    ) | Sort-Object FullName -Unique
    $contentFingerprint = Get-CcPortContentFingerprint -Paths @($sourceFiles | ForEach-Object { $_.FullName })
    $metadataCode = @'
import importlib.metadata as metadata
import json
import platform
import sys
import sysconfig
import PyInstaller

packages = sorted(
    ((dist.metadata.get('Name') or '').lower(), dist.version)
    for dist in metadata.distributions()
)
print(json.dumps({
    'executable': sys.executable,
    'python': platform.python_version(),
    'implementation': platform.python_implementation(),
    'abi': sysconfig.get_config_var('SOABI'),
    'platform': platform.platform(),
    'pyinstaller': PyInstaller.__version__,
    'packages': packages,
}, sort_keys=True, separators=(',', ':')))
'@
    $metadata = Invoke-CcPortNative -FilePath $PythonPath -ArgumentList @("-c", $metadataCode) -WorkingDirectory $RepoRoot -Capture -Description "sidecar build environment fingerprint"
    $payload = [ordered]@{
        content = $contentFingerprint
        environment = $metadata.Output.Trim()
        target = $Target
    } | ConvertTo-Json -Depth 5 -Compress
    return Get-CcPortStringHash -Value $payload
}

function Get-SidecarCacheStatus {
    param(
        [Parameter(Mandatory = $true)][string]$CachePath,
        [Parameter(Mandatory = $true)][string]$Fingerprint,
        [Parameter(Mandatory = $true)][string]$ArtifactPath,
        [switch]$ForceClean
    )

    if ($ForceClean) {
        return [pscustomobject]@{ Hit = $false; Reason = "forced"; Record = $null }
    }
    $record = Read-CcPortJsonCache -Path $CachePath
    if ($null -eq $record) {
        return [pscustomobject]@{ Hit = $false; Reason = "missing-or-invalid-record"; Record = $null }
    }
    $fingerprintProperty = $record.PSObject.Properties["fingerprint"]
    $hashProperty = $record.PSObject.Properties["artifactSha256"]
    if ($null -eq $fingerprintProperty -or $null -eq $hashProperty -or
        [string]::IsNullOrWhiteSpace([string]$fingerprintProperty.Value) -or
        [string]::IsNullOrWhiteSpace([string]$hashProperty.Value)) {
        return [pscustomobject]@{ Hit = $false; Reason = "missing-or-invalid-record"; Record = $record }
    }
    if ([string]$fingerprintProperty.Value -ne $Fingerprint) {
        return [pscustomobject]@{ Hit = $false; Reason = "input-fingerprint-changed"; Record = $record }
    }
    if (-not (Test-Path -LiteralPath $ArtifactPath -PathType Leaf)) {
        return [pscustomobject]@{ Hit = $false; Reason = "artifact-missing"; Record = $record }
    }
    $actualHash = (Get-FileHash -LiteralPath $ArtifactPath -Algorithm SHA256).Hash
    if ([string]$hashProperty.Value -ne $actualHash) {
        return [pscustomobject]@{ Hit = $false; Reason = "artifact-hash-changed"; Record = $record }
    }
    return [pscustomobject]@{ Hit = $true; Reason = "verified"; Record = $record }
}

function Write-SidecarCacheRecord {
    param(
        [Parameter(Mandatory = $true)][string]$CachePath,
        [Parameter(Mandatory = $true)][string]$Fingerprint,
        [Parameter(Mandatory = $true)][string]$ArtifactPath,
        [Parameter(Mandatory = $true)][string]$Target
    )

    $artifact = Get-Item -LiteralPath $ArtifactPath
    $record = [ordered]@{
        fingerprint = $Fingerprint
        target = $Target
        artifactPath = $artifact.FullName
        artifactSha256 = (Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256).Hash
        artifactBytes = $artifact.Length
        verifiedAtUtc = [DateTime]::UtcNow.ToString("o")
    }
    Write-CcPortJsonCacheAtomically -Path $CachePath -Value $record
}

function Assert-SidecarHashesMatch {
    param(
        [Parameter(Mandatory = $true)][string]$ExpectedPath,
        [Parameter(Mandatory = $true)][string]$ActualPath
    )

    foreach ($path in @($ExpectedPath, $ActualPath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required sidecar artifact is missing: $path"
        }
    }
    $expectedHash = (Get-FileHash -LiteralPath $ExpectedPath -Algorithm SHA256).Hash
    $actualHash = (Get-FileHash -LiteralPath $ActualPath -Algorithm SHA256).Hash
    if ($expectedHash -ne $actualHash) {
        throw "Tauri sidecar hash mismatch. source=$ExpectedPath ($expectedHash) target=$ActualPath ($actualHash)"
    }
}

function Remove-KnownTauriOutputs {
    param(
        [Parameter(Mandatory = $true)][string]$TargetReleaseDirectory,
        [Parameter(Mandatory = $true)][string]$DesktopName,
        [Parameter(Mandatory = $true)][string]$SidecarName,
        [switch]$Clean
    )

    if (-not (Test-Path -LiteralPath $TargetReleaseDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $TargetReleaseDirectory -Force | Out-Null
    }
    $bundleDirectory = Join-Path $TargetReleaseDirectory "bundle"
    Remove-CcPortSafePath -Path $bundleDirectory -Parent $TargetReleaseDirectory
    $names = @("$SidecarName.exe")
    if ($Clean) {
        $names += "$DesktopName.exe"
    }
    foreach ($name in $names) {
        $path = Join-Path $TargetReleaseDirectory $name
        Assert-CcPortDirectChild -Path $path -Parent $TargetReleaseDirectory | Out-Null
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
    Get-CcPortWindowsPackageArtifacts -ReleaseDirectory $StagingDirectory | Out-Null
}

function Invoke-SidecarSmokeTest {
    param(
        [Parameter(Mandatory = $true)][string]$SidecarPath,
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )

    $tempRoot = [IO.Path]::GetTempPath().TrimEnd("\")
    $stateHome = Join-Path $tempRoot ("cc-port-release-smoke-" + [guid]::NewGuid().ToString("N"))
    Assert-CcPortDirectChild -Path $stateHome -Parent $tempRoot | Out-Null
    New-Item -ItemType Directory -Path $stateHome | Out-Null
    $hadPriorStateHome = Test-Path Env:CC_PORT_STATE_HOME
    $priorStateHome = $env:CC_PORT_STATE_HOME
    try {
        $env:CC_PORT_STATE_HOME = $stateHome
        $result = Invoke-CcPortNative -FilePath $SidecarPath -ArgumentList @("operation_history_page") -WorkingDirectory $RepoRoot -Capture -Description "packaged sidecar smoke test"
        try {
            $response = $result.Output | ConvertFrom-Json -ErrorAction Stop
        } catch {
            throw "Packaged sidecar returned invalid JSON: $(Get-CcPortOutputExcerpt -Value $result.Output)"
        }
        if ($null -eq $response -or -not $response.ok) {
            throw "Packaged sidecar returned an error: $(Get-CcPortOutputExcerpt -Value $result.Output)"
        }
        Write-Host "  sidecar response: ok"
    } finally {
        if ($hadPriorStateHome) {
            $env:CC_PORT_STATE_HOME = $priorStateHome
        } else {
            Remove-Item Env:CC_PORT_STATE_HOME -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $stateHome) {
            Remove-CcPortSafePath -Path $stateHome -Parent $tempRoot
        }
    }
}

try {
    $repoRoot = Get-CcPortRepoRoot
    $metricsDirectory = Join-Path $repoRoot "build\metrics"
    $metricsStem = "release-$($script:ReleaseStartedAtUtc.ToString('yyyyMMdd-HHmmss'))-$($script:ReleaseRunId)"
    $script:ReleaseMetricsPath = Join-Path $metricsDirectory "$metricsStem.json"
    $gateLogDirectory = Join-Path $metricsDirectory "$metricsStem.logs"
    $script:ReleaseLock = Enter-ReleaseLock -Path (Join-Path $repoRoot "build\cache\release.lock")

    if ($env:OS -ne "Windows_NT" -or -not [Environment]::Is64BitOperatingSystem) {
        throw "Desktop release supports Windows x64 only."
    }

    $desktopDirectory = Join-Path $repoRoot "desktop"
    $tauriDirectory = Join-Path $desktopDirectory "src-tauri"
    $targetReleaseDirectory = Join-Path $tauriDirectory "target\release"
    $releaseRoot = Join-Path $repoRoot "release\desktop"
    $desktopName = "cc-port-desktop"
    $sidecarName = "cc-port-desktop-api"
    $expectedTarget = Get-CcPortExpectedTarget
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    $sidecarCachePath = Join-Path $repoRoot "build\cache\sidecar.json"
    $sourceSidecar = Join-Path $tauriDirectory "binaries\$sidecarName-$expectedTarget.exe"
    $targetSidecar = Join-Path $targetReleaseDirectory "$sidecarName.exe"

    $env:CC_PORT_DEPENDENCY_CACHE_STATUS = $null
    $setupPhaseIndex = $script:ReleasePhases.Count
    try {
        Invoke-ReleaseAction -Description "Preparing build environment" -CacheStatus $(if ($Clean) { "forced" } else { $null }) -Action {
            if ($Clean) {
                & (Join-Path $PSScriptRoot "setup.ps1") -NonInteractive -ForceSync
            } else {
                & (Join-Path $PSScriptRoot "setup.ps1") -NonInteractive
            }
            if ($LASTEXITCODE -ne 0) {
                throw "Environment preparation failed (exit $LASTEXITCODE)."
            }
        }
    } finally {
        if ($script:ReleasePhases.Count -gt $setupPhaseIndex -and $env:CC_PORT_DEPENDENCY_CACHE_STATUS) {
            $script:ReleasePhases[$setupPhaseIndex].cacheStatus = $env:CC_PORT_DEPENDENCY_CACHE_STATUS
        }
    }

    $resolveStartedAt = [DateTime]::UtcNow
    $resolveStopwatch = [Diagnostics.Stopwatch]::StartNew()
    try {
        Update-CcPortProcessPath
        $node = Get-CcPortNode
        $npm = Get-CcPortNpm -NodePath $(if ($node) { $node.Path } else { $null })
        $git = Get-CcPortGit
        $rust = Get-CcPortRustTools
        $visualStudio = Get-CcPortVisualStudioPath
        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf) -or -not $node -or
            -not $npm -or -not $git -or -not $rust.Cargo -or -not $rust.Rustc -or
            $rust.Target -ne $expectedTarget -or -not $visualStudio) {
            throw "Resolved environment is incomplete after setup. Run scripts/setup.ps1 -CheckOnly for details."
        }
        Add-CcPortPathDirectories -Directories @(
            (Split-Path -Parent $venvPython),
            (Split-Path -Parent $node.Path),
            (Split-Path -Parent $npm),
            (Split-Path -Parent $git),
            (Split-Path -Parent $rust.Cargo),
            (Split-Path -Parent $rust.Rustc)
        )
        $env:RUSTUP_TOOLCHAIN = "stable-$expectedTarget"
        Enable-CcPortVisualStudioEnvironment -InstallationPath $visualStudio
        $resolveStopwatch.Stop()
        Add-ReleasePhase -Name "Resolving build tools" -StartedAtUtc $resolveStartedAt -DurationMs $resolveStopwatch.Elapsed.TotalMilliseconds -ExitCode 0 -CacheStatus $null -LogPath $null
    } catch {
        $resolveStopwatch.Stop()
        Add-ReleasePhase -Name "Resolving build tools" -StartedAtUtc $resolveStartedAt -DurationMs $resolveStopwatch.Elapsed.TotalMilliseconds -ExitCode 1 -CacheStatus $null -LogPath $null
        throw
    }

    Write-CcPortSection "Resolved build tools"
    Write-Host "  python : $venvPython"
    Write-Host "  node   : $($node.Path)"
    Write-Host "  npm    : $npm"
    Write-Host "  git    : $git"
    Write-Host "  cargo  : $($rust.Cargo)"
    Write-Host "  rustc  : $($rust.Rustc)"
    Write-Host "  target : $($rust.Target)"
    Write-Host "  msvc   : $visualStudio"
    Write-Host "  mode   : $(if ($Clean) { 'clean' } else { 'verified cache reuse' })"
    Write-Host "  runtime: external Git for Windows with Git Credential Manager required at runtime"

    $windowsPowerShell = Join-Path $PSHOME "powershell.exe"
    if (-not (Test-Path -LiteralPath $windowsPowerShell -PathType Leaf)) {
        throw "Windows PowerShell executable was not found: $windowsPowerShell"
    }
    $pytestWorkerCount = Get-ReleasePytestWorkerCount
    Write-Host "  pytest workers: $pytestWorkerCount"
    $gates = @(
        [pscustomobject]@{
            Name = "PowerShell build self-tests"
            FilePath = $windowsPowerShell
            ArgumentList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $repoRoot "tests\test_desktop_build.ps1"))
            WorkingDirectory = $repoRoot
        },
        [pscustomobject]@{
            Name = "Python tests"
            FilePath = $venvPython
            ArgumentList = @("-m", "pytest", "-q", "-s", "-n", [string]$pytestWorkerCount, "--dist", "load")
            WorkingDirectory = $repoRoot
        },
        [pscustomobject]@{
            Name = "Ruff"
            FilePath = $venvPython
            ArgumentList = @("-m", "ruff", "check", "src/cc_port", "tests", "tools/packaging/sidecar", "tools/packaging/icons")
            WorkingDirectory = $repoRoot
        },
        [pscustomobject]@{
            Name = "Frontend tests"
            FilePath = $npm
            ArgumentList = @("test")
            WorkingDirectory = $desktopDirectory
        },
        [pscustomobject]@{
            Name = "Locked frontend dependency audit"
            FilePath = $npm
            ArgumentList = @("audit", "--package-lock-only", "--audit-level=moderate")
            WorkingDirectory = $desktopDirectory
        }
    )
    Invoke-ParallelReleaseGates -Gates $gates -LogDirectory $gateLogDirectory -MaximumConcurrency 4

    $iconPng = Join-Path $tauriDirectory "icons\icon.png"
    $iconIco = Join-Path $tauriDirectory "icons\icon.ico"
    if (-not (Test-Path -LiteralPath $iconPng -PathType Leaf) -or -not (Test-Path -LiteralPath $iconIco -PathType Leaf)) {
        Invoke-ReleaseStep -Description "Generating desktop icons" -FilePath $venvPython -ArgumentList @(
            (Join-Path $repoRoot "tools\packaging\icons\generate_icons.py")
        ) -WorkingDirectory $repoRoot
    } else {
        Add-ReleasePhase -Name "Generating desktop icons" -StartedAtUtc ([DateTime]::UtcNow) -DurationMs 0 -ExitCode 0 -CacheStatus "hit" -LogPath $null
        Write-CcPortSection "Desktop icons already exist"
    }

    $sidecarFingerprint = $null
    Invoke-ReleaseAction -Description "Fingerprinting desktop API sidecar" -CacheStatus $null -Action {
        $script:ComputedSidecarFingerprint = Get-SidecarInputFingerprint -RepoRoot $repoRoot -PythonPath $venvPython -Target $expectedTarget
    }
    $sidecarFingerprint = $script:ComputedSidecarFingerprint
    $sidecarStatus = Get-SidecarCacheStatus -CachePath $sidecarCachePath -Fingerprint $sidecarFingerprint -ArtifactPath $sourceSidecar -ForceClean:$Clean
    $sidecarCacheHit = [bool]$sidecarStatus.Hit
    $cachedSidecarValidationPhaseIndex = $null
    if ($sidecarCacheHit) {
        $validationPhaseIndex = $script:ReleasePhases.Count
        try {
            Invoke-ReleaseAction -Description "Validating cached desktop API sidecar" -CacheStatus "hit" -Action {
                Invoke-SidecarSmokeTest -SidecarPath $sourceSidecar -RepoRoot $repoRoot
            }
            Add-ReleasePhase -Name "Building desktop API sidecar" -StartedAtUtc ([DateTime]::UtcNow) -DurationMs 0 -ExitCode 0 -CacheStatus "hit" -LogPath $null
        } catch {
            Write-Host "  cached sidecar failed smoke test; rebuilding clean: $($_.Exception.Message)" -ForegroundColor Yellow
            if ($script:ReleasePhases.Count -gt $validationPhaseIndex) {
                $cachedSidecarValidationPhaseIndex = $validationPhaseIndex
                Start-ReleasePhaseRecovery -PhaseIndex $validationPhaseIndex -CacheStatus "invalidated:smoke-failed" -Detail $_.Exception.Message
            }
            $sidecarCacheHit = $false
            $sidecarStatus = [pscustomobject]@{ Reason = "cached-smoke-failed" }
        }
    }
    if (-not $sidecarCacheHit) {
        Invoke-ReleaseStep -Description "Building desktop API sidecar" -FilePath $venvPython -ArgumentList @(
            (Join-Path $repoRoot "tools\packaging\sidecar\build_sidecar.py"),
            "--target", $expectedTarget
        ) -WorkingDirectory $repoRoot -CacheStatus "miss:$($sidecarStatus.Reason)"
        Invoke-ReleaseAction -Description "Smoke testing rebuilt desktop API sidecar" -CacheStatus "miss:$($sidecarStatus.Reason)" -Action {
            Invoke-SidecarSmokeTest -SidecarPath $sourceSidecar -RepoRoot $repoRoot
        }
        Invoke-ReleaseAction -Description "Updating desktop API sidecar cache" -CacheStatus "refresh" -Action {
            Write-SidecarCacheRecord -CachePath $sidecarCachePath -Fingerprint $sidecarFingerprint -ArtifactPath $sourceSidecar -Target $expectedTarget
        }
        if ($null -ne $cachedSidecarValidationPhaseIndex) {
            Complete-ReleasePhaseRecovery -PhaseIndex $cachedSidecarValidationPhaseIndex
        }
    }

    Invoke-ReleaseAction -Description "Cleaning known Tauri outputs" -CacheStatus $(if ($Clean) { "forced" } else { "reuse-cargo" }) -Action {
        Remove-KnownTauriOutputs -TargetReleaseDirectory $targetReleaseDirectory -DesktopName $desktopName -SidecarName $sidecarName -Clean:$Clean
    }
    Invoke-ReleaseAction -Description "Staging verified Tauri sidecar" -CacheStatus $(if ($sidecarCacheHit) { "hit" } else { "rebuilt" }) -Action {
        Copy-Item -LiteralPath $sourceSidecar -Destination $targetSidecar -Force
        Assert-SidecarHashesMatch -ExpectedPath $sourceSidecar -ActualPath $targetSidecar
    }
    Invoke-ReleaseStep -Description "Building Tauri MSI and NSIS bundles" -FilePath $npm -ArgumentList @(
        "run", "tauri", "--", "build"
    ) -WorkingDirectory $desktopDirectory -CacheStatus $(if ($Clean) { "forced-relink" } else { "cargo-managed" })
    Invoke-ReleaseAction -Description "Verifying Tauri sidecar content" -CacheStatus $null -Action {
        Assert-SidecarHashesMatch -ExpectedPath $sourceSidecar -ActualPath $targetSidecar
    }

    if (-not (Test-Path -LiteralPath $releaseRoot -PathType Container)) {
        New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
    }
    $finalDirectory = Join-Path $releaseRoot $expectedTarget
    $stagingDirectory = Join-Path $releaseRoot ("." + $expectedTarget + ".staging-" + [guid]::NewGuid().ToString("N"))
    Assert-CcPortDirectChild -Path $finalDirectory -Parent $releaseRoot | Out-Null
    Assert-CcPortDirectChild -Path $stagingDirectory -Parent $releaseRoot | Out-Null
    New-Item -ItemType Directory -Path $stagingDirectory | Out-Null
    try {
        Invoke-ReleaseAction -Description "Collecting release artifacts into staging" -CacheStatus $null -Action {
            Copy-TauriOutputs -TargetReleaseDirectory $targetReleaseDirectory -StagingDirectory $stagingDirectory -DesktopName $desktopName -SidecarName $sidecarName
        }
        $stagedSidecar = Join-Path $stagingDirectory "$sidecarName.exe"
        Invoke-ReleaseAction -Description "Smoke testing packaged sidecar" -CacheStatus $null -Action {
            Invoke-SidecarSmokeTest -SidecarPath $stagedSidecar -RepoRoot $repoRoot
        }
        $stagedArtifacts = @(
            (Get-Item -LiteralPath (Join-Path $stagingDirectory "$desktopName.exe")),
            (Get-Item -LiteralPath (Join-Path $stagingDirectory "$sidecarName.exe"))
        ) + @(Get-CcPortWindowsPackageArtifacts -ReleaseDirectory $stagingDirectory)
        Invoke-ReleaseAction -Description "Hashing verified release artifacts" -CacheStatus $null -Action {
            $verifiedArtifacts = New-Object System.Collections.Generic.List[object]
            $stagingPrefix = $stagingDirectory.TrimEnd("\") + "\"
            foreach ($artifact in $stagedArtifacts | Sort-Object FullName -Unique) {
                if (-not $artifact.FullName.StartsWith($stagingPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                    throw "Staged artifact escaped the staging directory: $($artifact.FullName)"
                }
                $relativePath = $artifact.FullName.Substring($stagingPrefix.Length)
                $publishedPath = Join-Path $finalDirectory $relativePath
                $hash = Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256
                Write-Host "  $publishedPath ($($artifact.Length) bytes)"
                Write-Host "    SHA-256 $($hash.Hash)"
                $verifiedArtifacts.Add([pscustomobject][ordered]@{
                    path = $publishedPath
                    bytes = $artifact.Length
                    sha256 = $hash.Hash
                })
            }
            $script:PendingReleaseArtifacts = $verifiedArtifacts.ToArray()
        }
        Invoke-ReleaseAction -Description "Publishing verified release" -CacheStatus $null -Action {
            Publish-CcPortStagingDirectory -StagingDirectory $stagingDirectory -FinalDirectory $finalDirectory -ReleaseRoot $releaseRoot
        }
        foreach ($verifiedArtifact in $script:PendingReleaseArtifacts) {
            $script:ReleaseArtifacts.Add($verifiedArtifact)
        }
    } finally {
        if (Test-Path -LiteralPath $stagingDirectory) {
            Remove-CcPortSafePath -Path $stagingDirectory -Parent $releaseRoot
        }
    }
    $script:ReleaseSucceeded = $true
    Write-Host ""
    Write-Host "Release complete: $finalDirectory" -ForegroundColor Green
} catch {
    $script:ReleaseSucceeded = $false
    $script:ReleaseError = $_.Exception.Message
    Write-Host ""
    Write-Host "Release failed: $($script:ReleaseError)" -ForegroundColor Red
} finally {
    try {
        Write-ReleaseSummary
    } catch {
        Write-Host "Unable to write release timing summary: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    try {
        try {
            Save-ReleaseMetrics
            if ($script:ReleaseMetricsPath) {
                Write-Host "Release metrics: $($script:ReleaseMetricsPath)"
            }
        } catch {
            Write-Host "Unable to write release metrics: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    } finally {
        try {
            if ($null -ne $script:ReleaseLock) {
                $script:ReleaseLock.Dispose()
            }
        } finally {
            if ($script:HadPriorDependencyCacheStatus) {
                $env:CC_PORT_DEPENDENCY_CACHE_STATUS = $script:PriorDependencyCacheStatus
            } else {
                Remove-Item Env:CC_PORT_DEPENDENCY_CACHE_STATUS -ErrorAction SilentlyContinue
            }
        }
    }
}

if ($script:ReleaseSucceeded) {
    exit 0
}
exit 1
