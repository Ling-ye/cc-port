#!/usr/bin/env powershell
<#
.SYNOPSIS
    Check and prepare the complete Windows desktop build environment.

.EXAMPLE
    Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1

.EXAMPLE
    & .\scripts\setup.ps1 -CheckOnly
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$NonInteractive,
    [switch]$ForceSync
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:CC_PORT_DEPENDENCY_CACHE_STATUS = "miss:bootstrap"
$ModulePath = Join-Path $PSScriptRoot "desktop-build.psm1"
Import-Module $ModulePath -Force -ErrorAction Stop

function Write-ToolValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [AllowNull()]$Value
    )

    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        Write-Host ("  {0,-12}: MISSING" -f $Name) -ForegroundColor Yellow
    } else {
        Write-Host ("  {0,-12}: {1}" -f $Name, $Value)
    }
}

function Test-VenvInterpreterReady {
    param([Parameter(Mandatory = $true)][string]$PythonPath)

    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return $false
    }
    $probe = Invoke-CcPortNative -FilePath $PythonPath -ArgumentList @(
        "-c",
        "import platform,sys; ok=(sys.version_info[:2] >= (3,10) and sys.version_info[:2] <= (3,12) and platform.architecture()[0] == '64bit'); raise SystemExit(0 if ok else 1)"
    ) -Capture -AllowFailure -Description "virtual environment probe"
    if ($probe.ExitCode -ne 0) {
        return $false
    }
    return $true
}

function Test-VenvReady {
    param([Parameter(Mandatory = $true)][string]$PythonPath)

    if (-not (Test-VenvInterpreterReady -PythonPath $PythonPath)) {
        return $false
    }
    $imports = Invoke-CcPortNative -FilePath $PythonPath -ArgumentList @(
        "-c", "import PIL, PyInstaller, cc_port, pytest, xdist"
    ) -Capture -AllowFailure -Description "Python build dependency probe"
    if ($imports.ExitCode -ne 0) {
        return $false
    }
    $ruff = Invoke-CcPortNative -FilePath $PythonPath -ArgumentList @("-m", "ruff", "--version") -Capture -AllowFailure -Description "Ruff probe"
    return $ruff.ExitCode -eq 0
}

function Test-NodeModulesReady {
    param([Parameter(Mandatory = $true)][string]$DesktopDirectory)

    $binDirectory = Join-Path $DesktopDirectory "node_modules\.bin"
    return (Test-Path -LiteralPath (Join-Path $binDirectory "vite.cmd") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $binDirectory "vitest.cmd") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $binDirectory "tauri.cmd") -PathType Leaf)
}

function Get-DependencyInputState {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$DesktopDirectory,
        [Parameter(Mandatory = $true)][string]$VenvPython,
        [Parameter(Mandatory = $true)][string]$NodeVersion,
        [Parameter(Mandatory = $true)][string]$NodePath,
        [Parameter(Mandatory = $true)][string]$NpmPath,
        [Parameter(Mandatory = $true)][string]$ExpectedTarget
    )

    $python = Invoke-CcPortNative -FilePath $VenvPython -ArgumentList @(
        "-c", "import platform,sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{platform.architecture()[0]}')"
    ) -Capture -AllowFailure -Description "dependency cache Python version probe"
    if ($python.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($python.Output)) {
        throw "Repository Python version could not be determined for dependency caching."
    }

    $npm = Invoke-CcPortNative -FilePath $NpmPath -ArgumentList @("--version") -WorkingDirectory $DesktopDirectory -Capture -AllowFailure -Description "npm version probe"
    if ($npm.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($npm.Output)) {
        throw "npm version could not be determined for dependency caching."
    }

    $contentFingerprint = Get-CcPortContentFingerprint -Paths @(
        (Join-Path $RepoRoot "pyproject.toml"),
        (Join-Path $DesktopDirectory "package.json"),
        (Join-Path $DesktopDirectory "package-lock.json")
    )
    $platform = @(
        "os=$($env:OS)",
        "osVersion=$([Environment]::OSVersion.VersionString)",
        "is64Bit=$([Environment]::Is64BitOperatingSystem)",
        "target=$ExpectedTarget"
    ) -join ";"
    $pythonVersion = $python.Output.Trim()
    $normalizedNodeVersion = $NodeVersion.Trim()
    $npmVersion = $npm.Output.Trim()
    $fingerprint = Get-CcPortDependencyInputFingerprint -ContentFingerprint $contentFingerprint `
        -PythonVersion $pythonVersion -PythonPath $VenvPython `
        -NodeVersion $normalizedNodeVersion -NodePath $NodePath `
        -NpmVersion $npmVersion -NpmPath $NpmPath -Platform $platform

    return [pscustomobject]@{
        Fingerprint = $fingerprint
        ContentFingerprint = $contentFingerprint
        PythonVersion = $pythonVersion
        PythonPath = [IO.Path]::GetFullPath($VenvPython)
        NodeVersion = $normalizedNodeVersion
        NodePath = [IO.Path]::GetFullPath($NodePath)
        NpmVersion = $npmVersion
        NpmPath = [IO.Path]::GetFullPath($NpmPath)
        Platform = $platform
    }
}

function Get-DependencyProbeState {
    param(
        [Parameter(Mandatory = $true)][string]$VenvPython,
        [Parameter(Mandatory = $true)][string]$NpmPath,
        [Parameter(Mandatory = $true)][string]$DesktopDirectory
    )

    if (-not (Test-VenvReady -PythonPath $VenvPython)) {
        return [pscustomobject]@{ Ready = $false; Reason = "python-build-probe" }
    }
    if (-not (Test-NodeModulesReady -DesktopDirectory $DesktopDirectory)) {
        return [pscustomobject]@{ Ready = $false; Reason = "node-modules-tools" }
    }

    $pipCheck = Invoke-CcPortNative -FilePath $VenvPython -ArgumentList @("-m", "pip", "check") -Capture -AllowFailure -Description "pip dependency check"
    if ($pipCheck.ExitCode -ne 0) {
        return [pscustomobject]@{ Ready = $false; Reason = "pip-check" }
    }
    $pipList = Invoke-CcPortNative -FilePath $VenvPython -ArgumentList @("-m", "pip", "--disable-pip-version-check", "list", "--format=json") -Capture -AllowFailure -Description "pip dependency manifest"
    if ($pipList.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($pipList.Output)) {
        return [pscustomobject]@{ Ready = $false; Reason = "pip-list" }
    }

    $npmTree = Invoke-CcPortNative -FilePath $NpmPath -ArgumentList @("ls", "--all", "--json") -WorkingDirectory $DesktopDirectory -Capture -AllowFailure -Description "npm dependency tree"
    if ($npmTree.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($npmTree.Output)) {
        return [pscustomobject]@{ Ready = $false; Reason = "npm-tree" }
    }
    $nodeModulesLock = Join-Path $DesktopDirectory "node_modules\.package-lock.json"
    if (-not (Test-Path -LiteralPath $nodeModulesLock -PathType Leaf)) {
        return [pscustomobject]@{ Ready = $false; Reason = "node-modules-lock" }
    }

    return [pscustomobject]@{
        Ready = $true
        Reason = $null
        PythonManifestHash = Get-CcPortStringHash -Value $pipList.Output.Trim()
        NpmTreeHash = Get-CcPortStringHash -Value $npmTree.Output.Trim()
        NodeModulesLockHash = Get-CcPortContentFingerprint -Paths @($nodeModulesLock)
    }
}

function Get-DependencyCacheDecision {
    param(
        [Parameter(Mandatory = $true)][string]$CachePath,
        [AllowNull()]$InputState,
        [AllowNull()][string]$VenvPython,
        [AllowNull()][string]$NpmPath,
        [Parameter(Mandatory = $true)][string]$DesktopDirectory,
        [switch]$ForceSync,
        [switch]$DeferProbe
    )

    if ($ForceSync) {
        return [pscustomobject]@{ Hit = $false; Status = "forced"; Probe = $null; Cache = $null }
    }
    if ($null -eq $InputState) {
        return [pscustomobject]@{ Hit = $false; Status = "miss:environment"; Probe = $null; Cache = $null }
    }
    if (-not (Test-Path -LiteralPath $CachePath -PathType Leaf)) {
        return [pscustomobject]@{ Hit = $false; Status = "miss:cache-missing"; Probe = $null; Cache = $null }
    }

    $cache = Read-CcPortJsonCache -Path $CachePath
    if ($null -eq $cache) {
        return [pscustomobject]@{ Hit = $false; Status = "miss:cache-invalid"; Probe = $null; Cache = $null }
    }
    $fingerprintProperty = $cache.PSObject.Properties["fingerprint"]
    if ($null -eq $fingerprintProperty -or [string]$fingerprintProperty.Value -ne $InputState.Fingerprint) {
        return [pscustomobject]@{ Hit = $false; Status = "miss:inputs-changed"; Probe = $null; Cache = $cache }
    }
    if ($DeferProbe) {
        return [pscustomobject]@{ Hit = $true; Status = "candidate:inputs-match"; Probe = $null; Cache = $cache }
    }

    $probe = Get-DependencyProbeState -VenvPython $VenvPython -NpmPath $NpmPath -DesktopDirectory $DesktopDirectory
    if (-not $probe.Ready) {
        return [pscustomobject]@{ Hit = $false; Status = "miss:$($probe.Reason)"; Probe = $probe; Cache = $cache }
    }
    $comparisons = @(
        [pscustomobject]@{ CacheName = "pythonManifestHash"; ProbeName = "PythonManifestHash"; Reason = "python-manifest-changed" },
        [pscustomobject]@{ CacheName = "npmTreeHash"; ProbeName = "NpmTreeHash"; Reason = "npm-tree-changed" },
        [pscustomobject]@{ CacheName = "nodeModulesLockHash"; ProbeName = "NodeModulesLockHash"; Reason = "node-modules-lock-changed" }
    )
    foreach ($comparison in $comparisons) {
        $cachedProperty = $cache.PSObject.Properties[$comparison.CacheName]
        $actualProperty = $probe.PSObject.Properties[$comparison.ProbeName]
        if ($null -eq $cachedProperty -or $null -eq $actualProperty -or
            [string]$cachedProperty.Value -ne [string]$actualProperty.Value) {
            return [pscustomobject]@{ Hit = $false; Status = "miss:$($comparison.Reason)"; Probe = $probe; Cache = $cache }
        }
    }
    return [pscustomobject]@{ Hit = $true; Status = "hit"; Probe = $probe; Cache = $cache }
}

function Get-EnvironmentSnapshot {
    $python = Get-CcPortPython
    $node = Get-CcPortNode
    $npm = Get-CcPortNpm -NodePath $(if ($node) { $node.Path } else { $null })
    $git = Get-CcPortGit
    $rust = Get-CcPortRustTools
    $visualStudio = Get-CcPortVisualStudioPath
    return [pscustomobject]@{
        Python       = $python
        Node         = $node
        Npm          = $npm
        Git          = $git
        Rust         = $rust
        VisualStudio = $visualStudio
        Winget       = Get-CcPortWinget
    }
}

function Write-EnvironmentSnapshot {
    param([Parameter(Mandatory = $true)]$Snapshot)

    Write-CcPortSection "Detected build environment"
    Write-ToolValue -Name "PowerShell" -Value $PSVersionTable.PSVersion
    Write-ToolValue -Name "Python" -Value $(if ($Snapshot.Python) { "$($Snapshot.Python.Version) ($($Snapshot.Python.Path))" })
    Write-ToolValue -Name "Node.js" -Value $(if ($Snapshot.Node) { "$($Snapshot.Node.Version) ($($Snapshot.Node.Path))" })
    Write-ToolValue -Name "npm" -Value $Snapshot.Npm
    Write-ToolValue -Name "Git" -Value $Snapshot.Git
    Write-ToolValue -Name "Cargo" -Value $Snapshot.Rust.Cargo
    Write-ToolValue -Name "rustc" -Value $Snapshot.Rust.Rustc
    Write-ToolValue -Name "Rust target" -Value $Snapshot.Rust.Target
    Write-ToolValue -Name "VS BuildTools" -Value $Snapshot.VisualStudio
    Write-ToolValue -Name "WinGet" -Value $Snapshot.Winget
    if ($Snapshot.Rust.Error) {
        Write-Host "  Rust detail : $($Snapshot.Rust.Error)" -ForegroundColor Yellow
    }
}

try {
    $repoRoot = Get-CcPortRepoRoot
    $desktopDirectory = Join-Path $repoRoot "desktop"
    $venvDirectory = Join-Path $repoRoot ".venv"
    $venvPython = Join-Path $venvDirectory "Scripts\python.exe"
    $expectedTarget = Get-CcPortExpectedTarget
    $dependencyCachePath = Join-Path $repoRoot "build\cache\dependencies.json"

    if ($env:OS -ne "Windows_NT") {
        throw "Desktop release setup supports Windows only."
    }
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "Desktop release setup supports Windows x64 only."
    }
    if ($PSVersionTable.PSVersion -lt [version]"5.1") {
        throw "Windows PowerShell 5.1 or newer is required."
    }

    $snapshot = Get-EnvironmentSnapshot
    Write-EnvironmentSnapshot -Snapshot $snapshot

    $packages = [ordered]@{}
    if (-not $snapshot.Python) {
        $packages["Python.Python.3.12"] = "Python 3.12 x64"
    }
    if (-not $snapshot.Node -or -not $snapshot.Npm) {
        $packages["OpenJS.NodeJS.LTS"] = "Node.js LTS and npm"
    }
    if (-not $snapshot.Git) {
        $packages["Git.Git"] = "Git"
    }
    if (-not $snapshot.Rust.Cargo -or -not $snapshot.Rust.Rustc -or -not $snapshot.Rust.Rustup) {
        $packages["Rustlang.Rustup"] = "Rustup, Cargo, and rustc"
    }
    if (-not $snapshot.VisualStudio) {
        $packages["Microsoft.VisualStudio.2022.BuildTools"] = "Visual Studio 2022 C++ Build Tools"
    }

    $needsRustToolchain = -not $snapshot.Rust.Rustup -or $snapshot.Rust.Target -ne $expectedTarget
    $venvReady = Test-VenvReady -PythonPath $venvPython
    $nodeModulesReady = Test-NodeModulesReady -DesktopDirectory $desktopDirectory
    $dependencyInputState = $null
    if ($snapshot.Node -and $snapshot.Npm -and $venvReady) {
        try {
            $dependencyInputState = Get-DependencyInputState -RepoRoot $repoRoot -DesktopDirectory $desktopDirectory `
                -VenvPython $venvPython -NodeVersion $snapshot.Node.Version -NodePath $snapshot.Node.Path -NpmPath $snapshot.Npm `
                -ExpectedTarget $expectedTarget
        } catch {
            $dependencyInputState = $null
        }
    }
    $dependencyDecision = Get-DependencyCacheDecision -CachePath $dependencyCachePath `
        -InputState $dependencyInputState -VenvPython $venvPython -NpmPath $snapshot.Npm `
        -DesktopDirectory $desktopDirectory -ForceSync:$ForceSync -DeferProbe:(-not $CheckOnly)
    $env:CC_PORT_DEPENDENCY_CACHE_STATUS = $dependencyDecision.Status

    Write-CcPortSection "Required actions"
    if ($packages.Count -eq 0) {
        Write-Host "  System tools are installed."
    } else {
        foreach ($packageId in $packages.Keys) {
            Write-Host "  Install $($packages[$packageId]) [$packageId]"
        }
    }
    if ($needsRustToolchain) {
        Write-Host "  Install/select Rust toolchain stable-$expectedTarget"
    }
    if (-not $venvReady) {
        Write-Host "  Create or repair repository .venv"
    }
    if ($dependencyDecision.Status -eq "candidate:inputs-match") {
        Write-Host "  Dependency inputs match cache; complete probes run immediately before reuse"
    } elseif ($dependencyDecision.Hit) {
        Write-Host "  Reuse verified Python and frontend dependencies"
    } elseif ($CheckOnly) {
        if ($venvReady) {
            Write-Host "  Repository .venv is ready"
        }
        if ($nodeModulesReady) {
            Write-Host "  Frontend node_modules is ready"
        } else {
            Write-Host "  Install frontend dependencies from desktop/package-lock.json"
        }
        Write-Host "  Dependency cache status: $($dependencyDecision.Status)"
    } else {
        Write-Host "  Synchronize Python dependencies from pyproject.toml"
        Write-Host "  Synchronize frontend dependencies from desktop/package-lock.json"
        Write-Host "  Dependency cache status: $($dependencyDecision.Status)"
    }

    if ($CheckOnly) {
        $ready = $packages.Count -eq 0 -and
            -not $needsRustToolchain -and
            $venvReady -and
            $nodeModulesReady -and
            $dependencyDecision.Hit
        if (-not $ready) {
            if ($packages.Count -gt 0 -and -not $snapshot.Winget) {
                Write-Host ""
                Write-Host "WinGet is required to install missing system tools." -ForegroundColor Red
                Write-Host "Install or repair App Installer, then rerun this command: $(Get-CcPortWingetHelpUrl)"
            }
            throw "Build environment is not ready. Check-only mode made no changes."
        }
        Write-Host ""
        Write-Host "Environment check passed. Check-only mode made no changes." -ForegroundColor Green
        exit 0
    }

    if ($packages.Count -gt 0 -and -not $snapshot.Winget) {
        throw "WinGet is required to install missing system tools. Install or repair App Installer from $(Get-CcPortWingetHelpUrl), then rerun the same command."
    }

    foreach ($packageId in $packages.Keys) {
        Write-CcPortSection "Installing $($packages[$packageId])"
        $override = $null
        if ($packageId -eq "Microsoft.VisualStudio.2022.BuildTools") {
            $override = "--wait --passive --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
        }
        Install-CcPortWingetPackage -WingetPath $snapshot.Winget -PackageId $packageId -Override $override -NonInteractive:$NonInteractive
    }

    Update-CcPortProcessPath
    $snapshot = Get-EnvironmentSnapshot
    if (-not $snapshot.Python -or -not $snapshot.Node -or -not $snapshot.Npm -or
        -not $snapshot.Git -or -not $snapshot.Rust.Rustup -or -not $snapshot.VisualStudio) {
        Write-EnvironmentSnapshot -Snapshot $snapshot
        throw "One or more installed tools are still unavailable. A package may require a Windows restart; restart if requested, then rerun the same command."
    }

    Add-CcPortPathDirectories -Directories @(
        (Split-Path -Parent $snapshot.Python.Path),
        (Split-Path -Parent $snapshot.Node.Path),
        (Split-Path -Parent $snapshot.Npm),
        (Split-Path -Parent $snapshot.Git),
        (Split-Path -Parent $snapshot.Rust.Cargo),
        (Split-Path -Parent $snapshot.Rust.Rustup)
    )

    Write-CcPortSection "Ensuring Rust MSVC toolchain"
    $env:RUSTUP_TOOLCHAIN = "stable-$expectedTarget"
    $rust = Get-CcPortRustTools
    if ($rust.Target -ne $expectedTarget) {
        Invoke-CcPortNative -FilePath $snapshot.Rust.Rustup -ArgumentList @(
            "toolchain", "install", "stable-$expectedTarget", "--profile", "minimal"
        ) -Description "Rust MSVC toolchain installation" | Out-Null
        $rust = Get-CcPortRustTools
    }
    if ($rust.Target -ne $expectedTarget) {
        throw "Rust target mismatch after toolchain setup. expected=$expectedTarget actual=$($rust.Target); detail=$($rust.Error)"
    }

    Write-CcPortSection "Loading Visual Studio C++ environment"
    Enable-CcPortVisualStudioEnvironment -InstallationPath $snapshot.VisualStudio
    $link = Resolve-CcPortExecutable -Names @("link.exe")
    if (-not $link) {
        throw "Visual Studio C++ linker link.exe was not found after loading VsDevCmd.bat."
    }
    Write-Host "  linker: $link"

    if ((Test-Path -LiteralPath $venvDirectory -PathType Container) -and -not (Test-VenvInterpreterReady -PythonPath $venvPython)) {
        $backupName = ".venv.backup-" + (Get-Date -Format "yyyyMMdd-HHmmss")
        $backupPath = Join-Path $repoRoot $backupName
        Assert-CcPortDirectChild -Path $venvDirectory -Parent $repoRoot | Out-Null
        Assert-CcPortDirectChild -Path $backupPath -Parent $repoRoot | Out-Null
        Write-CcPortSection "Backing up incompatible virtual environment"
        Move-Item -LiteralPath $venvDirectory -Destination $backupPath
        Write-Host "  backup: $backupPath"
    }
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        Write-CcPortSection "Creating repository virtual environment"
        Invoke-CcPortNative -FilePath $snapshot.Python.Path -ArgumentList @("-m", "venv", $venvDirectory) -Description "Python virtual environment creation" | Out-Null
    }

    $currentDependencyInput = Get-DependencyInputState -RepoRoot $repoRoot -DesktopDirectory $desktopDirectory `
        -VenvPython $venvPython -NodeVersion $snapshot.Node.Version -NodePath $snapshot.Node.Path -NpmPath $snapshot.Npm `
        -ExpectedTarget $expectedTarget
    # Run the complete decision immediately before reuse. The earlier non-check-only
    # decision intentionally validates only immutable inputs, because interactive
    # pauses and tool setup can change either dependency tree before this point.
    $dependencyDecision = Get-DependencyCacheDecision -CachePath $dependencyCachePath `
        -InputState $currentDependencyInput -VenvPython $venvPython -NpmPath $snapshot.Npm `
        -DesktopDirectory $desktopDirectory -ForceSync:$ForceSync
    $env:CC_PORT_DEPENDENCY_CACHE_STATUS = $dependencyDecision.Status

    if ($dependencyDecision.Hit) {
        Write-CcPortSection "Reusing verified dependency environment"
        Write-Host "  cache: $dependencyCachePath"
    } else {
        # Invalidate the previous record before either package manager mutates the
        # environment. A failed repair must never leave a stale record eligible
        # for reuse on the next invocation.
        Write-CcPortJsonCacheAtomically -Path $dependencyCachePath -Value ([pscustomobject]@{
            invalidatedAtUtc = [DateTime]::UtcNow.ToString("o")
            reason = $dependencyDecision.Status
        })

        Write-CcPortSection "Installing Python build dependencies"
        Invoke-CcPortNative -FilePath $venvPython -ArgumentList @(
            "-m", "pip", "install", "--disable-pip-version-check", "-e", ".[dev,desktop]"
        ) -WorkingDirectory $repoRoot -Description "Python dependency installation" | Out-Null

        Write-CcPortSection "Installing locked frontend dependencies"
        Invoke-CcPortNative -FilePath $snapshot.Npm -ArgumentList @(
            "ci", "--ignore-scripts", "--no-audit", "--no-fund"
        ) -WorkingDirectory $desktopDirectory -Description "frontend dependency installation" | Out-Null

        $probe = Get-DependencyProbeState -VenvPython $venvPython -NpmPath $snapshot.Npm -DesktopDirectory $desktopDirectory
        if (-not $probe.Ready) {
            throw "Project dependency verification failed after installation: $($probe.Reason)"
        }
        $cacheValue = [pscustomobject]@{
            fingerprint = $currentDependencyInput.Fingerprint
            inputs = [pscustomobject]@{
                contentFingerprint = $currentDependencyInput.ContentFingerprint
                pythonVersion = $currentDependencyInput.PythonVersion
                pythonPath = $currentDependencyInput.PythonPath
                nodeVersion = $currentDependencyInput.NodeVersion
                nodePath = $currentDependencyInput.NodePath
                npmVersion = $currentDependencyInput.NpmVersion
                npmPath = $currentDependencyInput.NpmPath
                platform = $currentDependencyInput.Platform
            }
            pythonManifestHash = $probe.PythonManifestHash
            npmTreeHash = $probe.NpmTreeHash
            nodeModulesLockHash = $probe.NodeModulesLockHash
            writtenAtUtc = [DateTime]::UtcNow.ToString("o")
        }
        Write-CcPortJsonCacheAtomically -Path $dependencyCachePath -Value $cacheValue
        Write-Host "  dependency cache: $dependencyCachePath"
    }

    Write-CcPortSection "Environment ready"
    Write-Host "  python : $venvPython"
    Write-Host "  node   : $($snapshot.Node.Path)"
    Write-Host "  npm    : $($snapshot.Npm)"
    Write-Host "  git    : $($snapshot.Git)"
    Write-Host "  cargo  : $($rust.Cargo)"
    Write-Host "  rustc  : $($rust.Rustc)"
    Write-Host "  target : $($rust.Target)"
    Write-Host "  msvc   : $($snapshot.VisualStudio)"
    Write-Host "  cache  : $($env:CC_PORT_DEPENDENCY_CACHE_STATUS)"
    exit 0
} catch {
    if ([string]::IsNullOrWhiteSpace($env:CC_PORT_DEPENDENCY_CACHE_STATUS)) {
        $env:CC_PORT_DEPENDENCY_CACHE_STATUS = "miss:setup-error"
    }
    Write-Host ""
    Write-Host "Environment setup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
