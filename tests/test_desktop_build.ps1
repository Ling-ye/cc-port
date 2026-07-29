#!/usr/bin/env powershell
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Import-Module (Join-Path $RepoRoot "scripts\desktop-build.psm1") -Force -ErrorAction Stop

$originalPathExt = [Environment]::GetEnvironmentVariable("PATHEXT", "Process")
$nativeExecutableExtensions = @(".COM", ".EXE", ".BAT", ".CMD", ".VBS", ".VBE", ".JS", ".JSE", ".WSF", ".WSH", ".MSC", ".CPL")
if (@($env:PATHEXT -split ";" | ForEach-Object { $_.ToUpperInvariant() }) -notcontains ".EXE") {
    # WSL can launch Windows PowerShell with PATHEXT reduced to ".CPL". In that
    # host state, Windows PowerShell silently skips even absolute .exe invocations
    # and leaves LASTEXITCODE unset, invalidating every native-process fixture.
    $env:PATHEXT = $nativeExecutableExtensions -join ";"
}

$script:Passed = 0

function Assert-Equal {
    param(
        [AllowNull()]$Expected,
        [AllowNull()]$Actual,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if ($Expected -ne $Actual) {
        throw "$Message. expected=<$Expected> actual=<$Actual>"
    }
}

function Assert-True {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Assert-Throws {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [Parameter(Mandatory = $true)][string]$Pattern,
        [Parameter(Mandatory = $true)][string]$Message
    )

    try {
        & $Action
    } catch {
        if ($_.Exception.Message -notmatch $Pattern) {
            throw "$Message. Unexpected error: $($_.Exception.Message)"
        }
        return
    }
    throw "$Message. Expected an exception matching: $Pattern"
}

function Invoke-Test {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )

    & $Action
    $script:Passed++
    Write-Host "  PASS $Name"
}

# Load only the pure/reusable function definitions from the release entry point.
# Dot-sourcing the full file would execute a real release, so the AST is used to
# isolate the functions that need behavioral coverage.
$releaseScriptPath = Join-Path $RepoRoot "scripts\release-desktop.ps1"
$releaseTokens = $null
$releaseErrors = $null
$releaseAst = [System.Management.Automation.Language.Parser]::ParseFile(
    $releaseScriptPath,
    [ref]$releaseTokens,
    [ref]$releaseErrors
)
foreach ($functionName in @(
    "Add-ReleasePhase",
    "Start-ReleasePhaseRecovery",
    "Complete-ReleasePhaseRecovery",
    "Get-ReleasePytestWorkerCount",
    "Invoke-ParallelReleaseGates",
    "Enter-ReleaseLock",
    "Get-SidecarCacheStatus",
    "Remove-KnownTauriOutputs"
)) {
    $definition = $releaseAst.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $functionName
    }, $true)
    if ($null -eq $definition) {
        throw "Release function was not found for testing: $functionName"
    }
    Invoke-Expression $definition.Extent.Text
}
$setupTokens = $null
$setupErrors = $null
$setupAst = [System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $RepoRoot "scripts\setup.ps1"),
    [ref]$setupTokens,
    [ref]$setupErrors
)
$dependencyDecisionDefinition = $setupAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq "Get-DependencyCacheDecision"
}, $true)
if ($null -eq $dependencyDecisionDefinition) {
    throw "Setup function was not found for testing: Get-DependencyCacheDecision"
}
Invoke-Expression $dependencyDecisionDefinition.Extent.Text

$tempParent = [IO.Path]::GetTempPath().TrimEnd("\")
$tempRoot = Join-Path $tempParent ("cc-port-desktop-build-tests-" + [guid]::NewGuid().ToString("N"))
Assert-CcPortDirectChild -Path $tempRoot -Parent $tempParent | Out-Null
New-Item -ItemType Directory -Path $tempRoot | Out-Null

try {
    Invoke-Test "PowerShell files parse on Windows PowerShell" {
        $files = @(
            (Join-Path $RepoRoot "scripts\desktop-build.psm1"),
            (Join-Path $RepoRoot "scripts\setup.ps1"),
            (Join-Path $RepoRoot "scripts\release-desktop.ps1"),
            (Join-Path $RepoRoot "tests\test_desktop_build.ps1")
        )
        foreach ($file in $files) {
            $tokens = $null
            $errors = $null
            [System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors) | Out-Null
            Assert-Equal -Expected 0 -Actual @($errors).Count -Message "Parser errors in $file"
        }
    }

    Invoke-Test "Python version policy" {
        Assert-True -Condition (Test-CcPortPythonVersion -Version ([version]"3.10.0")) -Message "Python 3.10 should be accepted"
        Assert-True -Condition (Test-CcPortPythonVersion -Version ([version]"3.12.9")) -Message "Python 3.12 should be accepted"
        Assert-True -Condition (-not (Test-CcPortPythonVersion -Version ([version]"3.9.19"))) -Message "Python 3.9 should be rejected"
        Assert-True -Condition (-not (Test-CcPortPythonVersion -Version ([version]"3.13.0"))) -Message "Python 3.13 should be rejected"
    }

    Invoke-Test "Node.js version policy" {
        Assert-True -Condition (-not (Test-CcPortNodeVersion -Value "v20.18.1")) -Message "Node 20.18 should be rejected"
        Assert-True -Condition (Test-CcPortNodeVersion -Value "v20.19.0") -Message "Node 20.19 should be accepted"
        Assert-True -Condition (-not (Test-CcPortNodeVersion -Value "v22.11.0")) -Message "Node 22.11 should be rejected"
        Assert-True -Condition (Test-CcPortNodeVersion -Value "v22.12.0") -Message "Node 22.12 should be accepted"
        Assert-True -Condition (Test-CcPortNodeVersion -Value "v24.1.0") -Message "Node 24 should be accepted"
    }

    Invoke-Test "Stable string and content fingerprints" {
        $fingerprintRoot = Join-Path $tempRoot "fingerprints-stable"
        New-Item -ItemType Directory -Path $fingerprintRoot | Out-Null
        $firstPath = Join-Path $fingerprintRoot "first.txt"
        $secondPath = Join-Path $fingerprintRoot "second.txt"
        [IO.File]::WriteAllText($firstPath, "alpha")
        [IO.File]::WriteAllText($secondPath, "beta")

        $stringHash = Get-CcPortStringHash -Value "stable value"
        Assert-Equal -Expected $stringHash -Actual (Get-CcPortStringHash -Value "stable value") -Message "String hash must be stable"
        Assert-True -Condition ($stringHash -match '^[0-9a-f]{64}$') -Message "String hash must be lowercase SHA-256"

        $ordered = Get-CcPortContentFingerprint -Paths @($firstPath, $secondPath)
        $reversed = Get-CcPortContentFingerprint -Paths @($secondPath, $firstPath)
        Assert-Equal -Expected $ordered -Actual $reversed -Message "Content fingerprint must not depend on input order"
        Assert-True -Condition (
            (Get-CcPortContentFingerprint -Paths @($firstPath)) -ne
            (Get-CcPortContentFingerprint -Paths @($secondPath))
        ) -Message "Content fingerprint must include file identity as well as content"
    }

    Invoke-Test "Content fingerprint invalidates on file change" {
        $path = Join-Path $tempRoot "fingerprint-change.txt"
        [IO.File]::WriteAllText($path, "before")
        $before = Get-CcPortContentFingerprint -Paths @($path)
        [IO.File]::WriteAllText($path, "after")
        $after = Get-CcPortContentFingerprint -Paths @($path)
        Assert-True -Condition ($before -ne $after) -Message "Changed file content must invalidate the fingerprint"
    }

    Invoke-Test "Dependency fingerprint includes normalized tool paths" {
        $pythonPath = Join-Path $tempRoot "tools\python.exe"
        $nodePath = Join-Path $tempRoot "tools\node.exe"
        $npmPath = Join-Path $tempRoot "tools\npm.cmd"
        $base = Get-CcPortDependencyInputFingerprint -ContentFingerprint "content" `
            -PythonVersion "3.12.10" -PythonPath $pythonPath `
            -NodeVersion "v22.23.1" -NodePath $nodePath `
            -NpmVersion "10.9.8" -NpmPath $npmPath -Platform "windows-x64"
        $same = Get-CcPortDependencyInputFingerprint -ContentFingerprint "content" `
            -PythonVersion "3.12.10" -PythonPath ($pythonPath.ToUpperInvariant()) `
            -NodeVersion "v22.23.1" -NodePath ($nodePath.ToUpperInvariant()) `
            -NpmVersion "10.9.8" -NpmPath ($npmPath.ToUpperInvariant()) -Platform "windows-x64"
        Assert-Equal -Expected $base -Actual $same -Message "Windows path casing must not perturb dependency fingerprints"

        $changedPython = Get-CcPortDependencyInputFingerprint -ContentFingerprint "content" `
            -PythonVersion "3.12.10" -PythonPath (Join-Path $tempRoot "alternate\python.exe") `
            -NodeVersion "v22.23.1" -NodePath $nodePath `
            -NpmVersion "10.9.8" -NpmPath $npmPath -Platform "windows-x64"
        $changedNode = Get-CcPortDependencyInputFingerprint -ContentFingerprint "content" `
            -PythonVersion "3.12.10" -PythonPath $pythonPath `
            -NodeVersion "v22.23.1" -NodePath (Join-Path $tempRoot "alternate\node.exe") `
            -NpmVersion "10.9.8" -NpmPath $npmPath -Platform "windows-x64"
        $changedNpm = Get-CcPortDependencyInputFingerprint -ContentFingerprint "content" `
            -PythonVersion "3.12.10" -PythonPath $pythonPath `
            -NodeVersion "v22.23.1" -NodePath $nodePath `
            -NpmVersion "10.9.8" -NpmPath (Join-Path $tempRoot "alternate\npm.cmd") -Platform "windows-x64"
        Assert-True -Condition ($base -ne $changedPython) -Message "Changing Python path must invalidate dependency fingerprint"
        Assert-True -Condition ($base -ne $changedNode) -Message "Changing Node path must invalidate dependency fingerprint"
        Assert-True -Condition ($base -ne $changedNpm) -Message "Changing npm path must invalidate dependency fingerprint"
        $changedContent = Get-CcPortDependencyInputFingerprint -ContentFingerprint "changed-content" `
            -PythonVersion "3.12.10" -PythonPath $pythonPath `
            -NodeVersion "v22.23.1" -NodePath $nodePath `
            -NpmVersion "10.9.8" -NpmPath $npmPath -Platform "windows-x64"
        $changedVersions = Get-CcPortDependencyInputFingerprint -ContentFingerprint "content" `
            -PythonVersion "3.12.11" -PythonPath $pythonPath `
            -NodeVersion "v22.23.2" -NodePath $nodePath `
            -NpmVersion "10.9.9" -NpmPath $npmPath -Platform "windows-x64"
        Assert-True -Condition ($base -ne $changedContent) -Message "Changing a manifest or lock content hash must invalidate the dependency fingerprint"
        Assert-True -Condition ($base -ne $changedVersions) -Message "Changing tool versions must invalidate the dependency fingerprint"
    }

    Invoke-Test "Versioned JSON cache round trip and corruption handling" {
        $cachePath = Join-Path $tempRoot "cache-round-trip.json"
        $value = [pscustomobject]@{
            InputFingerprint = "input-a"
            ManifestHash = "manifest-a"
        }
        Write-CcPortJsonCacheAtomically -Path $cachePath -Value $value
        $loaded = Read-CcPortJsonCache -Path $cachePath
        Assert-True -Condition ($null -ne $loaded) -Message "Written cache must be readable"
        Assert-Equal -Expected "input-a" -Actual $loaded.InputFingerprint -Message "Cache payload input fingerprint"
        Assert-Equal -Expected "manifest-a" -Actual $loaded.ManifestHash -Message "Cache payload manifest hash"

        Write-CcPortJsonCacheAtomically -Path $cachePath -Value ([pscustomobject]@{
            InputFingerprint = "input-b"
            ManifestHash = "manifest-b"
        })
        $replaced = Read-CcPortJsonCache -Path $cachePath
        Assert-Equal -Expected "input-b" -Actual $replaced.InputFingerprint -Message "Existing cache must be atomically replaced"
        $replacementArtifacts = @(Get-ChildItem -LiteralPath $tempRoot -Force | Where-Object {
            $_.Name -like ".cache-round-trip.json.tmp-*" -or $_.Name -like ".cache-round-trip.json.backup-*"
        })
        Assert-Equal -Expected 0 -Actual $replacementArtifacts.Count -Message "Successful replacement must clean temporary and backup files"

        [IO.File]::WriteAllText($cachePath, '{not-json')
        Assert-True -Condition ($null -eq (Read-CcPortJsonCache -Path $cachePath)) -Message "Corrupt cache must be treated as a miss"

        [IO.File]::WriteAllText($cachePath, '{"schemaVersion":999,"value":{"InputFingerprint":"stale"}}')
        Assert-True -Condition ($null -eq (Read-CcPortJsonCache -Path $cachePath)) -Message "Unknown cache schema must be treated as a miss"
    }

    Invoke-Test "JSON cache write is atomic on replacement failure" {
        $cachePath = Join-Path $tempRoot "cache-atomic.json"
        Write-CcPortJsonCacheAtomically -Path $cachePath -Value ([pscustomobject]@{ Marker = "old" })
        $lock = [IO.File]::Open($cachePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        try {
            Assert-Throws -Action {
                Write-CcPortJsonCacheAtomically -Path $cachePath -Value ([pscustomobject]@{ Marker = "new" })
            } -Pattern ".+" -Message "Locked destination must reject atomic replacement"
        } finally {
            $lock.Dispose()
        }
        $loaded = Read-CcPortJsonCache -Path $cachePath
        Assert-Equal -Expected "old" -Actual $loaded.Marker -Message "Failed replacement must preserve the prior cache"
        $temporary = @(Get-ChildItem -LiteralPath $tempRoot -Force | Where-Object {
            $_.Name -like ".cache-atomic.json.tmp-*" -or $_.Name -like ".cache-atomic.json.backup-*"
        })
        Assert-Equal -Expected 0 -Actual $temporary.Count -Message "Failed atomic writes must clean temporary and backup files"
    }

    Invoke-Test "Release lock is exclusive and reusable" {
        $lockPath = Join-Path $tempRoot "release-lock\release.lock"
        $first = Enter-ReleaseLock -Path $lockPath
        try {
            Assert-Throws -Action {
                $second = Enter-ReleaseLock -Path $lockPath
                $second.Dispose()
            } -Pattern "Another desktop release" -Message "A concurrent release must be rejected"
        } finally {
            $first.Dispose()
        }
        $afterRelease = Enter-ReleaseLock -Path $lockPath
        $afterRelease.Dispose()
    }

    Invoke-Test "Sidecar cache rejects incomplete, changed, and forced records" {
        $cachePath = Join-Path $tempRoot "sidecar-cache\sidecar.json"
        $artifactPath = Join-Path $tempRoot "sidecar-cache\sidecar.exe"
        New-Item -ItemType Directory -Path (Split-Path -Parent $artifactPath) -Force | Out-Null
        [IO.File]::WriteAllText($artifactPath, "sidecar-v1")

        Write-CcPortJsonCacheAtomically -Path $cachePath -Value ([pscustomobject]@{})
        $incomplete = Get-SidecarCacheStatus -CachePath $cachePath -Fingerprint "input-a" -ArtifactPath $artifactPath
        Assert-True -Condition (-not $incomplete.Hit) -Message "An incomplete sidecar record must miss"
        Assert-Equal -Expected "missing-or-invalid-record" -Actual $incomplete.Reason -Message "Incomplete record reason"

        Write-CcPortJsonCacheAtomically -Path $cachePath -Value ([pscustomobject]@{
            fingerprint = "input-a"
            artifactSha256 = "not-the-artifact-hash"
        })
        $changed = Get-SidecarCacheStatus -CachePath $cachePath -Fingerprint "input-a" -ArtifactPath $artifactPath
        Assert-Equal -Expected "artifact-hash-changed" -Actual $changed.Reason -Message "Changed artifact reason"

        $actualHash = (Get-FileHash -LiteralPath $artifactPath -Algorithm SHA256).Hash
        Write-CcPortJsonCacheAtomically -Path $cachePath -Value ([pscustomobject]@{
            fingerprint = "input-a"
            artifactSha256 = $actualHash
        })
        $hit = Get-SidecarCacheStatus -CachePath $cachePath -Fingerprint "input-a" -ArtifactPath $artifactPath
        Assert-True -Condition $hit.Hit -Message "Matching sidecar inputs and hash must hit"
        $forced = Get-SidecarCacheStatus -CachePath $cachePath -Fingerprint "input-a" -ArtifactPath $artifactPath -ForceClean
        Assert-Equal -Expected "forced" -Actual $forced.Reason -Message "Clean mode must force a miss"
    }

    Invoke-Test "Sidecar recovery is complete only after an explicit success transition" {
        $script:ReleasePhases = New-Object System.Collections.Generic.List[object]
        Add-ReleasePhase -Name "Cached sidecar smoke" -StartedAtUtc ([DateTime]::UtcNow) `
            -DurationMs 1 -ExitCode 1 -CacheStatus "hit" -LogPath $null

        Start-ReleasePhaseRecovery -PhaseIndex 0 -CacheStatus "invalidated:smoke-failed" -Detail "invalid JSON"
        Assert-True -Condition $script:ReleasePhases[0].recoveryAttempted -Message "Failed cache smoke must record a recovery attempt"
        Assert-True -Condition (-not $script:ReleasePhases[0].recovered) -Message "Starting a rebuild must not claim recovery"
        Assert-Equal -Expected "invalid JSON" -Actual $script:ReleasePhases[0].detail -Message "Original cache smoke failure must remain visible"

        Complete-ReleasePhaseRecovery -PhaseIndex 0
        Assert-True -Condition $script:ReleasePhases[0].recovered -Message "Successful rebuild path must complete recovery"
    }

    Invoke-Test "Warm cleanup preserves the Cargo app and Clean forces its relink" {
        $targetRelease = Join-Path $tempRoot "tauri-cleanup\release"
        $bundle = Join-Path $targetRelease "bundle"
        New-Item -ItemType Directory -Path $bundle -Force | Out-Null
        $app = Join-Path $targetRelease "cc-port-desktop.exe"
        $sidecar = Join-Path $targetRelease "cc-port-desktop-api.exe"
        [IO.File]::WriteAllText($app, "cargo-app")
        [IO.File]::WriteAllText($sidecar, "sidecar")
        [IO.File]::WriteAllText((Join-Path $bundle "old-installer.msi"), "old")

        Remove-KnownTauriOutputs -TargetReleaseDirectory $targetRelease `
            -DesktopName "cc-port-desktop" -SidecarName "cc-port-desktop-api"
        Assert-True -Condition (Test-Path -LiteralPath $app -PathType Leaf) -Message "Warm cleanup must preserve the Cargo-managed app"
        Assert-True -Condition (-not (Test-Path -LiteralPath $sidecar)) -Message "Warm cleanup must remove the staged sidecar"
        Assert-True -Condition (-not (Test-Path -LiteralPath $bundle)) -Message "Warm cleanup must remove prior bundles"

        New-Item -ItemType Directory -Path $bundle -Force | Out-Null
        [IO.File]::WriteAllText($sidecar, "sidecar")
        Remove-KnownTauriOutputs -TargetReleaseDirectory $targetRelease `
            -DesktopName "cc-port-desktop" -SidecarName "cc-port-desktop-api" -Clean
        Assert-True -Condition (-not (Test-Path -LiteralPath $app)) -Message "Clean must remove the app to force Cargo relinking"
    }

    Invoke-Test "Dependency cache honors forced, failed, changed, and passing probes" {
        $cachePath = Join-Path $tempRoot "dependency-decision\dependencies.json"
        $desktopPath = Join-Path $tempRoot "dependency-decision\desktop"
        New-Item -ItemType Directory -Path $desktopPath -Force | Out-Null
        $inputState = [pscustomobject]@{ Fingerprint = "dependency-input-a" }
        Write-CcPortJsonCacheAtomically -Path $cachePath -Value ([pscustomobject]@{
            fingerprint = $inputState.Fingerprint
            pythonManifestHash = "python-a"
            npmTreeHash = "npm-a"
            nodeModulesLockHash = "lock-a"
        })

        $forced = Get-DependencyCacheDecision -CachePath $cachePath -InputState $inputState `
            -VenvPython "python.exe" -NpmPath "npm.cmd" -DesktopDirectory $desktopPath -ForceSync
        Assert-Equal -Expected "forced" -Actual $forced.Status -Message "ForceSync must bypass an otherwise valid cache"

        $script:DependencyProbeCalls = 0
        function Get-DependencyProbeState {
            param([string]$VenvPython, [string]$NpmPath, [string]$DesktopDirectory)
            $script:DependencyProbeCalls++
            throw "Deferred dependency decision must not run environment probes"
        }
        $candidate = Get-DependencyCacheDecision -CachePath $cachePath -InputState $inputState `
            -VenvPython "python.exe" -NpmPath "npm.cmd" -DesktopDirectory $desktopPath -DeferProbe
        Assert-Equal -Expected "candidate:inputs-match" -Actual $candidate.Status -Message "Matching inputs must remain a reuse candidate"
        Assert-Equal -Expected 0 -Actual $script:DependencyProbeCalls -Message "Preflight must defer expensive dependency probes"

        $script:DependencyProbeFixture = [pscustomobject]@{ Ready = $false; Reason = "pip-check" }
        function Get-DependencyProbeState {
            param([string]$VenvPython, [string]$NpmPath, [string]$DesktopDirectory)
            return $script:DependencyProbeFixture
        }
        $probeFailure = Get-DependencyCacheDecision -CachePath $cachePath -InputState $inputState `
            -VenvPython "python.exe" -NpmPath "npm.cmd" -DesktopDirectory $desktopPath
        Assert-Equal -Expected "miss:pip-check" -Actual $probeFailure.Status -Message "A failed environment probe must force synchronization"

        $script:DependencyProbeFixture = [pscustomobject]@{
            Ready = $true
            Reason = $null
            PythonManifestHash = "python-a"
            NpmTreeHash = "npm-a"
            NodeModulesLockHash = "lock-b"
        }
        $manifestChanged = Get-DependencyCacheDecision -CachePath $cachePath -InputState $inputState `
            -VenvPython "python.exe" -NpmPath "npm.cmd" -DesktopDirectory $desktopPath
        Assert-Equal -Expected "miss:node-modules-lock-changed" -Actual $manifestChanged.Status -Message "A changed installed lock manifest must miss"

        $script:DependencyProbeFixture.NodeModulesLockHash = "lock-a"
        $hit = Get-DependencyCacheDecision -CachePath $cachePath -InputState $inputState `
            -VenvPython "python.exe" -NpmPath "npm.cmd" -DesktopDirectory $desktopPath
        Assert-True -Condition $hit.Hit -Message "Matching dependency inputs and probes must hit"
    }

    Invoke-Test "Parallel gates preserve every exit code and independent log" {
        $script:ReleasePhases = New-Object System.Collections.Generic.List[object]
        $gateLogs = Join-Path $tempRoot "parallel-gate-logs"
        $windowsPowerShell = Join-Path $PSHOME "powershell.exe"
        $gates = @(
            [pscustomobject]@{
                Name = "Gate exit three"
                FilePath = $windowsPowerShell
                ArgumentList = @("-NoProfile", "-Command", "exit 3")
                WorkingDirectory = $RepoRoot
            },
            [pscustomobject]@{
                Name = "Gate exit seven"
                FilePath = $windowsPowerShell
                ArgumentList = @("-NoProfile", "-Command", "exit 7")
                WorkingDirectory = $RepoRoot
            }
        )
        Assert-Throws -Action {
            Invoke-ParallelReleaseGates -Gates $gates -LogDirectory $gateLogs -MaximumConcurrency 2 -GateTimeoutSeconds 30
        } -Pattern "Release quality gates failed" -Message "Failed gates must be reported after all workers finish"
        $exitCodes = @($script:ReleasePhases | ForEach-Object { $_.exitCode } | Sort-Object)
        Assert-Equal -Expected "3 7" -Actual ($exitCodes -join " ") -Message "Native gate exit codes must be preserved"
        Assert-Equal -Expected 2 -Actual @(Get-ChildItem -LiteralPath $gateLogs -Filter "*.log").Count -Message "Each gate must have an independent log"
    }

    Invoke-Test "Pytest worker count is bounded by the host and four-worker cap" {
        Assert-Equal -Expected 1 -Actual (Get-ReleasePytestWorkerCount -ProcessorCount 0) -Message "Zero processors must still use one worker"
        Assert-Equal -Expected 1 -Actual (Get-ReleasePytestWorkerCount -ProcessorCount 1) -Message "One processor must use one worker"
        Assert-Equal -Expected 2 -Actual (Get-ReleasePytestWorkerCount -ProcessorCount 2) -Message "Two processors must use two workers"
        Assert-Equal -Expected 4 -Actual (Get-ReleasePytestWorkerCount -ProcessorCount 4) -Message "Four processors must use four workers"
        Assert-Equal -Expected 4 -Actual (Get-ReleasePytestWorkerCount -ProcessorCount 16) -Message "Large hosts must remain capped at four workers"
    }

    Invoke-Test "Parallel gate timeout is enforced per worker" {
        $script:ReleasePhases = New-Object System.Collections.Generic.List[object]
        $gateLogs = Join-Path $tempRoot "timeout-gate-logs"
        $windowsPowerShell = Join-Path $PSHOME "powershell.exe"
        $gate = [pscustomobject]@{
            Name = "Slow gate"
            FilePath = $windowsPowerShell
            ArgumentList = @("-NoProfile", "-Command", "Start-Sleep -Seconds 3; exit 0")
            WorkingDirectory = $RepoRoot
        }
        Assert-Throws -Action {
            Invoke-ParallelReleaseGates -Gates @($gate) -LogDirectory $gateLogs -MaximumConcurrency 1 -GateTimeoutSeconds 1
        } -Pattern "Release quality gates failed" -Message "A timed out gate must fail the gate group"
        Assert-Equal -Expected 124 -Actual $script:ReleasePhases[0].exitCode -Message "Timeout exit code"
        Assert-True -Condition ($script:ReleasePhases[0].durationMs -ge 1000 -and $script:ReleasePhases[0].durationMs -lt 2500) -Message "Timeout duration must be calculated from the individual worker start"
        Assert-True -Condition (Test-Path -LiteralPath $script:ReleasePhases[0].logPath -PathType Leaf) -Message "Timeout must have its own log"
        $timeoutLog = [IO.File]::ReadAllText($script:ReleasePhases[0].logPath)
        Assert-True -Condition ($timeoutLog -match "exceeded the 1 second") -Message "Timeout log must identify the configured limit"
    }

    Invoke-Test "Rust tuple parsing" {
        $escape = [string][char]27
        $bom = [string][char]0xFEFF
        $output = $bom + $escape + "[32m  x86_64-pc-windows-msvc  " + $escape + "[0m"
        Assert-Equal -Expected "x86_64-pc-windows-msvc" -Actual (Get-CcPortRustHostFromTuple -Output $output) -Message "Rust tuple parser"
    }

    Invoke-Test "Rust verbose parsing tolerates decorated output" {
        $escape = [string][char]27
        $bom = [string][char]0xFEFF
        $output = "rustc 1.90.0`r`n" + $bom + $escape + "[36m   HOST : x86_64-pc-windows-msvc   " + $escape + "[0m`r`n"
        Assert-Equal -Expected "x86_64-pc-windows-msvc" -Actual (Get-CcPortRustHostFromVerbose -Output $output) -Message "Rust verbose parser"
    }

    Invoke-Test "Explicit executable fallback" {
        $fake = Join-Path $tempRoot "fallback-tool.exe"
        [IO.File]::WriteAllText($fake, "fixture")
        $resolved = Resolve-CcPortExecutable -Names @("cc-port-command-that-does-not-exist.exe") -Fallbacks @($fake)
        Assert-Equal -Expected ([IO.Path]::GetFullPath($fake)) -Actual $resolved -Message "Executable fallback"
    }

    Invoke-Test "Safe path guard" {
        $direct = Join-Path $tempRoot "direct-child"
        Assert-Equal -Expected ([IO.Path]::GetFullPath($direct)) -Actual (Assert-CcPortDirectChild -Path $direct -Parent $tempRoot) -Message "Direct child should pass"
        $nested = Join-Path (Join-Path $tempRoot "nested") "child"
        Assert-Throws -Action { Assert-CcPortDirectChild -Path $nested -Parent $tempRoot | Out-Null } -Pattern "outside the expected parent" -Message "Nested path should be rejected"
    }

    Invoke-Test "Windows package detection" {
        $packages = Join-Path $tempRoot "packages"
        $msiDirectory = Join-Path $packages "msi"
        $nsisDirectory = Join-Path $packages "nsis"
        New-Item -ItemType Directory -Path $msiDirectory, $nsisDirectory | Out-Null
        [IO.File]::WriteAllText((Join-Path $msiDirectory "CC Port.msi"), "msi")
        Assert-Throws -Action { Get-CcPortWindowsPackageArtifacts -ReleaseDirectory $packages | Out-Null } -Pattern "NSIS" -Message "Missing NSIS should fail"
        [IO.File]::WriteAllText((Join-Path $nsisDirectory "CC Port-setup.exe"), "nsis")
        Assert-Equal -Expected 2 -Actual @(Get-CcPortWindowsPackageArtifacts -ReleaseDirectory $packages).Count -Message "MSI and NSIS should pass"
    }

    Invoke-Test "Rust release paths are remapped and restored" {
        $testRepo = Join-Path $tempRoot "repo with spaces"
        $hadEncoded = Test-Path Env:CARGO_ENCODED_RUSTFLAGS
        $priorEncoded = $env:CARGO_ENCODED_RUSTFLAGS
        $hadRustFlags = Test-Path Env:RUSTFLAGS
        $priorRustFlags = $env:RUSTFLAGS
        Remove-Item Env:CARGO_ENCODED_RUSTFLAGS -ErrorAction SilentlyContinue
        Remove-Item Env:RUSTFLAGS -ErrorAction SilentlyContinue
        $state = $null
        try {
            $state = Enter-CcPortRustPathRemapping -RepoRoot $testRepo
            $separator = [string][char]0x1F
            $flags = @($env:CARGO_ENCODED_RUSTFLAGS -split $separator)
            $fullRepo = [IO.Path]::GetFullPath($testRepo).TrimEnd("\")
            Assert-True -Condition ($flags -contains "--remap-path-prefix=$fullRepo=cc-port") -Message "Repository path remap"
            Assert-True -Condition ($flags -contains "--remap-path-prefix=$($fullRepo.Replace('\', '/'))=cc-port") -Message "Slash-normalized repository path remap"
            Assert-True -Condition (-not (Test-Path Env:RUSTFLAGS)) -Message "Encoded flags should preserve spaces without RUSTFLAGS"
        } finally {
            if ($null -ne $state) {
                Exit-CcPortRustPathRemapping -State $state
            }
            if ($hadEncoded) {
                $env:CARGO_ENCODED_RUSTFLAGS = $priorEncoded
            } else {
                Remove-Item Env:CARGO_ENCODED_RUSTFLAGS -ErrorAction SilentlyContinue
            }
            if ($hadRustFlags) {
                $env:RUSTFLAGS = $priorRustFlags
            } else {
                Remove-Item Env:RUSTFLAGS -ErrorAction SilentlyContinue
            }
        }
    }

    Invoke-Test "Packaged binary host paths are rejected in common encodings" {
        $sensitivePath = "C:\Users\builder-name\source"
        $cleanBinary = Join-Path $tempRoot "clean-binary.exe"
        $utf8Binary = Join-Path $tempRoot "utf8-path.exe"
        $utf16Binary = Join-Path $tempRoot "utf16-path.exe"
        [IO.File]::WriteAllBytes($cleanBinary, [Text.Encoding]::UTF8.GetBytes("release artifact"))
        [IO.File]::WriteAllBytes($utf8Binary, [Text.Encoding]::UTF8.GetBytes("prefix $sensitivePath suffix"))
        [IO.File]::WriteAllBytes($utf16Binary, [Text.Encoding]::Unicode.GetBytes("prefix $sensitivePath suffix"))

        Assert-CcPortBinaryOmitsHostPaths -Path $cleanBinary -SensitivePaths @($sensitivePath)
        Assert-Throws -Action {
            Assert-CcPortBinaryOmitsHostPaths -Path $utf8Binary -SensitivePaths @($sensitivePath)
        } -Pattern "contains a build host path" -Message "UTF-8 host path should fail"
        Assert-Throws -Action {
            Assert-CcPortBinaryOmitsHostPaths -Path $utf16Binary -SensitivePaths @($sensitivePath)
        } -Pattern "contains a build host path" -Message "UTF-16 host path should fail"
    }

    Invoke-Test "Release manifest versions agree" {
        Assert-Equal -Expected "0.5.3" -Actual (Get-CcPortReleaseVersion -RepoRoot $RepoRoot) -Message "Release version"
    }

    Invoke-Test "Release manifest mismatch is rejected" {
        $versionRoot = Join-Path $tempRoot "version-mismatch"
        $versionFiles = @(
            "pyproject.toml",
            "src\cc_port\__init__.py",
            "desktop\package.json",
            "desktop\package-lock.json",
            "desktop\src-tauri\Cargo.toml",
            "desktop\src-tauri\Cargo.lock",
            "desktop\src-tauri\tauri.conf.json",
            "SKILL.md",
            "docs\releases\v0.5.3.md",
            "docs\releases\v0.5.3.en.md"
        )
        foreach ($relativePath in $versionFiles) {
            $destination = Join-Path $versionRoot $relativePath
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            Copy-Item -LiteralPath (Join-Path $RepoRoot $relativePath) -Destination $destination
        }
        [IO.File]::WriteAllText(
            (Join-Path $versionRoot "src\cc_port\__init__.py"),
            '__version__ = "9.9.9"'
        )

        Assert-Throws -Action {
            Get-CcPortReleaseVersion -RepoRoot $versionRoot | Out-Null
        } -Pattern "versions do not agree" -Message "Version mismatch should block release"
    }

    Invoke-Test "Public release bundle contains only installer and checksum" {
        $verified = Join-Path $tempRoot "verified-public-source"
        $msiDirectory = Join-Path $verified "msi"
        $nsisDirectory = Join-Path $verified "nsis"
        $publishRoot = Join-Path $tempRoot "public-output"
        New-Item -ItemType Directory -Path $msiDirectory, $nsisDirectory | Out-Null
        [IO.File]::WriteAllText((Join-Path $msiDirectory "CC Port_0.5.3_x64_en-US.msi"), "msi")
        $sourceInstaller = Join-Path $nsisDirectory "CC Port_0.5.3_x64-setup.exe"
        [IO.File]::WriteAllText($sourceInstaller, "verified installer")

        $artifacts = @(Publish-CcPortPublicReleaseBundle `
            -VerifiedReleaseDirectory $verified `
            -PublishRoot $publishRoot `
            -Version "0.5.3")
        $final = Join-Path $publishRoot "v0.5.3"
        $installerName = "cc-port_0.5.3_windows_x64_setup.exe"
        $installer = Join-Path $final $installerName
        $checksum = Join-Path $final "SHA256SUMS.txt"
        $files = @(Get-ChildItem -LiteralPath $final -File | Sort-Object Name)
        $expectedHash = (Get-FileHash -LiteralPath $sourceInstaller -Algorithm SHA256).Hash.ToLowerInvariant()

        Assert-Equal -Expected 2 -Actual $files.Count -Message "Public artifact count"
        Assert-Equal -Expected $installerName -Actual $files[0].Name -Message "Public installer name"
        Assert-Equal -Expected "SHA256SUMS.txt" -Actual $files[1].Name -Message "Public checksum name"
        Assert-Equal -Expected "verified installer" -Actual ([IO.File]::ReadAllText($installer)) -Message "Public installer content"
        Assert-Equal -Expected "$expectedHash  $installerName`n" -Actual ([IO.File]::ReadAllText($checksum)) -Message "Checksum content"
        Assert-Equal -Expected 2 -Actual $artifacts.Count -Message "Returned public artifact count"
    }

    Invoke-Test "Verified release replacement" {
        $publishRoot = Join-Path $tempRoot "publish-success"
        $final = Join-Path $publishRoot "x86_64-pc-windows-msvc"
        $staging = Join-Path $publishRoot ".x86_64-pc-windows-msvc.staging"
        New-Item -ItemType Directory -Path $final, $staging | Out-Null
        [IO.File]::WriteAllText((Join-Path $final "artifact.txt"), "old")
        [IO.File]::WriteAllText((Join-Path $staging "artifact.txt"), "new")
        Publish-CcPortStagingDirectory -StagingDirectory $staging -FinalDirectory $final -ReleaseRoot $publishRoot
        Assert-Equal -Expected "new" -Actual ([IO.File]::ReadAllText((Join-Path $final "artifact.txt"))) -Message "Published artifact"
        Assert-Equal -Expected 0 -Actual @(Get-ChildItem -LiteralPath $publishRoot -Force | Where-Object Name -like ".*.backup-*").Count -Message "Backup cleanup"
    }

    Invoke-Test "Missing staging leaves prior output untouched" {
        $publishRoot = Join-Path $tempRoot "publish-missing-staging"
        $final = Join-Path $publishRoot "x86_64-pc-windows-msvc"
        $missingStaging = Join-Path $publishRoot ".missing-staging"
        New-Item -ItemType Directory -Path $final | Out-Null
        [IO.File]::WriteAllText((Join-Path $final "artifact.txt"), "old")
        Assert-Throws -Action {
            Publish-CcPortStagingDirectory -StagingDirectory $missingStaging -FinalDirectory $final -ReleaseRoot $publishRoot
        } -Pattern "^Staging directory does not exist or is not a directory:" -Message "Missing staging should fail"
        Assert-Equal -Expected "old" -Actual ([IO.File]::ReadAllText((Join-Path $final "artifact.txt"))) -Message "Prior release preservation"
        Assert-Equal -Expected 0 -Actual @(Get-ChildItem -LiteralPath $publishRoot -Force | Where-Object Name -like ".*.backup-*").Count -Message "Missing staging must not create a backup"
    }

    Invoke-Test "Failed release replacement restores prior output" {
        $publishRoot = Join-Path $tempRoot "publish-failure"
        $final = Join-Path $publishRoot "x86_64-pc-windows-msvc"
        $staging = Join-Path $publishRoot ".x86_64-pc-windows-msvc.staging"
        New-Item -ItemType Directory -Path $final, $staging | Out-Null
        [IO.File]::WriteAllText((Join-Path $final "artifact.txt"), "old")
        [IO.File]::WriteAllText((Join-Path $staging "artifact.txt"), "new")
        $stagingToFail = [IO.Path]::GetFullPath($staging)
        $moveDirectory = {
            param([string]$Source, [string]$Destination)
            if ([IO.Path]::GetFullPath($Source) -eq $stagingToFail) {
                throw "Simulated staging publish failure."
            }
            Move-Item -LiteralPath $Source -Destination $Destination -ErrorAction Stop
        }.GetNewClosure()
        Assert-Throws -Action {
            Publish-CcPortStagingDirectory -StagingDirectory $staging -FinalDirectory $final -ReleaseRoot $publishRoot -MoveDirectory $moveDirectory
        } -Pattern "moving the verified staging directory into place.*Simulated staging publish failure\." -Message "Replacement failure should identify the failed stage"
        Assert-Equal -Expected "old" -Actual ([IO.File]::ReadAllText((Join-Path $final "artifact.txt"))) -Message "Prior release restoration"
        Assert-Equal -Expected "new" -Actual ([IO.File]::ReadAllText((Join-Path $staging "artifact.txt"))) -Message "Failed staging preservation"
        Assert-Equal -Expected 0 -Actual @(Get-ChildItem -LiteralPath $publishRoot -Force | Where-Object Name -like ".*.backup-*").Count -Message "Rollback backup cleanup"
    }

    Invoke-Test "Setup exposes cancel and missing WinGet paths" {
        $source = [IO.File]::ReadAllText((Join-Path $RepoRoot "scripts\setup.ps1"))
        Assert-True -Condition ($source -match 'Read-Host\s+"Continue with these actions') -Message "Interactive confirmation is missing"
        Assert-True -Condition ($source -match 'WinGet is required to install missing system tools') -Message "Missing WinGet guidance is missing"
    }

    Invoke-Test "Setup exposes dependency cache and ForceSync bypass" {
        $source = [IO.File]::ReadAllText((Join-Path $RepoRoot "scripts\setup.ps1"))
        Assert-True -Condition ($source -match '\[switch\]\$ForceSync') -Message "Setup must expose the ForceSync switch"
        Assert-True -Condition ($source -match 'build[\\/]cache') -Message "Setup must store dependency cache below build/cache"
        Assert-True -Condition ($source -match 'dependencies\.json') -Message "Setup must use the dependencies.json cache"
        Assert-True -Condition ($source -match 'if\s*\(\$ForceSync\)') -Message "ForceSync must explicitly bypass cache reuse"
        Assert-True -Condition ($source -match 'pip.+check') -Message "Dependency cache validation must run pip check"
        Assert-True -Condition ($source -match 'pip.+list.+--format=json') -Message "Dependency cache validation must fingerprint pip list JSON"
        Assert-True -Condition ($source -match 'npm.+ls.+--all') -Message "Dependency cache validation must probe the complete npm tree"
        Assert-True -Condition ($source -match '\.package-lock\.json') -Message "Dependency cache validation must fingerprint node_modules/.package-lock.json"
        Assert-True -Condition (($source | Select-String -Pattern 'Get-DependencyCacheDecision' -AllMatches).Matches.Count -ge 2) -Message "Setup must revalidate dependencies immediately before reuse"
        Assert-True -Condition ($source -match 'invalidatedAtUtc') -Message "Setup must invalidate the prior cache before synchronizing dependencies"
        $moduleSource = Get-Content -LiteralPath (Join-Path $repoRoot "scripts\desktop-build.psm1") -Raw
        Assert-True -Condition ($moduleSource -match 'dependencyPolicyVersion=\d+') -Message "Dependency fingerprint must include a policy version"
    }

    Invoke-Test "Release always forwards non-interactive setup explicitly" {
        $source = [IO.File]::ReadAllText((Join-Path $RepoRoot "scripts\release-desktop.ps1"))
        $setupCalls = [regex]::Matches(
            $source,
            '&\s+\(Join-Path\s+\$PSScriptRoot\s+"setup\.ps1"\)(?<arguments>[^\r\n]*)'
        )
        Assert-Equal -Expected 2 -Actual $setupCalls.Count -Message "Release setup call count"
        foreach ($setupCall in $setupCalls) {
            Assert-True `
                -Condition ($setupCall.Groups["arguments"].Value -match '(^|\s)-NonInteractive(\s|$)') `
                -Message "Every release setup call must bind the non-interactive switch explicitly"
        }
        Assert-True -Condition ($source -notmatch '@setupArguments') -Message "Array splatting must not be used for named setup switches"
        $lastExitInitialization = $source.IndexOf('$global:LASTEXITCODE = 0')
        $firstSetupCall = $source.IndexOf('& (Join-Path $PSScriptRoot "setup.ps1")')
        Assert-True `
            -Condition ($lastExitInitialization -ge 0 -and $lastExitInitialization -lt $firstSetupCall) `
            -Message "Release must initialize LASTEXITCODE before invoking setup from a fresh strict-mode session"
    }

    Invoke-Test "Minimal Windows PATH stays short" {
        $minimal = Get-CcPortMinimalWindowsPath
        Assert-True -Condition ($minimal.Length -lt 512) -Message "Minimal PATH must stay well under the cmd.exe limit"
        Assert-True -Condition ($minimal -match 'System32') -Message "Minimal PATH must include System32"
    }

    $visualStudioForPathTest = Get-CcPortVisualStudioPath
    if ($visualStudioForPathTest) {
        Invoke-Test "VsDevCmd import tolerates oversized PATH" {
            $savedPath = $env:PATH
            $savedTransient = @{}
            foreach ($name in Get-CcPortVisualStudioTransientVariableNames) {
                $savedTransient[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
            }
            try {
                $pad = (1..160 | ForEach-Object {
                    "C:\CcPortPathPad\very\long\directory\name\segment$_\Scripts"
                }) -join ";"
                $env:PATH = $savedPath + ";" + $pad
                Assert-True -Condition ($env:PATH.Length -gt 7000) -Message "Test PATH must exceed the historical failure threshold"
                Enable-CcPortVisualStudioEnvironment -InstallationPath $visualStudioForPathTest
                $link = Resolve-CcPortExecutable -Names @("link.exe")
                Assert-True -Condition ($null -ne $link) -Message "link.exe must resolve after fat-PATH VsDevCmd import"
                Assert-True -Condition ($env:PATH -like "*CcPortPathPad*") -Message "Caller PATH entries must be preserved"
                Assert-True -Condition ([string]::IsNullOrEmpty($env:__VSCMD_PREINIT_PATH)) -Message "__VSCMD_PREINIT_PATH must not pollute the parent process"
            } finally {
                $env:PATH = $savedPath
                foreach ($name in @(Get-CcPortVisualStudioTransientVariableNames) + @($savedTransient.Keys)) {
                    if ($savedTransient.ContainsKey($name)) {
                        [Environment]::SetEnvironmentVariable($name, $savedTransient[$name], "Process")
                    } else {
                        [Environment]::SetEnvironmentVariable($name, $null, "Process")
                    }
                }
            }
        }

        Invoke-Test "VsDevCmd re-entry survives leftover EXTERNAL_INCLUDE" {
            $savedPath = $env:PATH
            $savedTransient = @{}
            foreach ($name in Get-CcPortVisualStudioTransientVariableNames) {
                $savedTransient[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
            }
            try {
                Enable-CcPortVisualStudioEnvironment -InstallationPath $visualStudioForPathTest
                # Poison the process the way a previous broken import did: keep a
                # huge EXTERNAL_INCLUDE and __VSCMD_PREINIT_PATH, then re-enter.
                $env:EXTERNAL_INCLUDE = ((1..120 | ForEach-Object {
                    "C:\CcPortExtInclude\very\long\include\path\segment$_"
                }) -join ";")
                $env:__VSCMD_PREINIT_PATH = $env:PATH
                $env:LIBPATH = ((1..80 | ForEach-Object {
                    "C:\CcPortLibPath\very\long\lib\path\segment$_"
                }) -join ";")
                Assert-True -Condition ($env:EXTERNAL_INCLUDE.Length -gt 4000) -Message "EXTERNAL_INCLUDE poison must be large"
                Enable-CcPortVisualStudioEnvironment -InstallationPath $visualStudioForPathTest
                $link = Resolve-CcPortExecutable -Names @("link.exe")
                Assert-True -Condition ($null -ne $link) -Message "link.exe must resolve after poisoned re-entry"
                Assert-True -Condition ([string]::IsNullOrEmpty($env:__VSCMD_PREINIT_PATH)) -Message "re-entry must not leave __VSCMD_PREINIT_PATH"
            } finally {
                $env:PATH = $savedPath
                foreach ($name in @(Get-CcPortVisualStudioTransientVariableNames) + @($savedTransient.Keys) + @("EXTERNAL_INCLUDE", "__VSCMD_PREINIT_PATH", "LIBPATH")) {
                    if ($savedTransient.ContainsKey($name)) {
                        [Environment]::SetEnvironmentVariable($name, $savedTransient[$name], "Process")
                    } else {
                        [Environment]::SetEnvironmentVariable($name, $null, "Process")
                    }
                }
            }
        }
    } else {
        Write-Host "  SKIP VsDevCmd import tolerates oversized PATH (Visual Studio not installed)"
        Write-Host "  SKIP VsDevCmd re-entry survives leftover EXTERNAL_INCLUDE (Visual Studio not installed)"
    }
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-CcPortSafePath -Path $tempRoot -Parent $tempParent
    }
    [Environment]::SetEnvironmentVariable("PATHEXT", $originalPathExt, "Process")
}

Write-Host ""
Write-Host "PowerShell build self-tests passed: $script:Passed" -ForegroundColor Green
exit 0
