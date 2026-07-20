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
    [switch]$NonInteractive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
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
    $probe = Invoke-LpmNative -FilePath $PythonPath -ArgumentList @(
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
    $imports = Invoke-LpmNative -FilePath $PythonPath -ArgumentList @(
        "-c", "import PIL, PyInstaller, lpm, pytest"
    ) -Capture -AllowFailure -Description "Python build dependency probe"
    if ($imports.ExitCode -ne 0) {
        return $false
    }
    $ruff = Invoke-LpmNative -FilePath $PythonPath -ArgumentList @("-m", "ruff", "--version") -Capture -AllowFailure -Description "Ruff probe"
    return $ruff.ExitCode -eq 0
}

function Test-NodeModulesReady {
    param([Parameter(Mandatory = $true)][string]$DesktopDirectory)

    $binDirectory = Join-Path $DesktopDirectory "node_modules\.bin"
    return (Test-Path -LiteralPath (Join-Path $binDirectory "vite.cmd") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $binDirectory "vitest.cmd") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $binDirectory "tauri.cmd") -PathType Leaf)
}

function Get-EnvironmentSnapshot {
    $python = Get-LpmPython
    $node = Get-LpmNode
    $npm = Get-LpmNpm -NodePath $(if ($node) { $node.Path } else { $null })
    $git = Get-LpmGit
    $rust = Get-LpmRustTools
    $visualStudio = Get-LpmVisualStudioPath
    return [pscustomobject]@{
        Python       = $python
        Node         = $node
        Npm          = $npm
        Git          = $git
        Rust         = $rust
        VisualStudio = $visualStudio
        Winget       = Get-LpmWinget
    }
}

function Write-EnvironmentSnapshot {
    param([Parameter(Mandatory = $true)]$Snapshot)

    Write-LpmSection "Detected build environment"
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
    $repoRoot = Get-LpmRepoRoot
    $desktopDirectory = Join-Path $repoRoot "desktop"
    $venvDirectory = Join-Path $repoRoot ".venv"
    $venvPython = Join-Path $venvDirectory "Scripts\python.exe"
    $expectedTarget = Get-LpmExpectedTarget

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

    Write-LpmSection "Required actions"
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
    if ($CheckOnly) {
        if ($venvReady) {
            Write-Host "  Repository .venv is ready"
        }
        if ($nodeModulesReady) {
            Write-Host "  Frontend node_modules is ready"
        } else {
            Write-Host "  Install frontend dependencies from desktop/package-lock.json"
        }
    } else {
        Write-Host "  Synchronize Python dependencies from pyproject.toml"
        Write-Host "  Synchronize frontend dependencies from desktop/package-lock.json"
    }

    if ($CheckOnly) {
        $ready = $packages.Count -eq 0 -and
            -not $needsRustToolchain -and
            $venvReady -and
            $nodeModulesReady
        if (-not $ready) {
            if ($packages.Count -gt 0 -and -not $snapshot.Winget) {
                Write-Host ""
                Write-Host "WinGet is required to install missing system tools." -ForegroundColor Red
                Write-Host "Install or repair App Installer, then rerun this command: $(Get-LpmWingetHelpUrl)"
            }
            throw "Build environment is not ready. Check-only mode made no changes."
        }
        Write-Host ""
        Write-Host "Environment check passed. Check-only mode made no changes." -ForegroundColor Green
        exit 0
    }

    if ($packages.Count -gt 0 -and -not $snapshot.Winget) {
        throw "WinGet is required to install missing system tools. Install or repair App Installer from $(Get-LpmWingetHelpUrl), then rerun the same command."
    }

    if (-not $NonInteractive) {
        $answer = Read-Host "Continue with these actions? [y/N]"
        if ($answer -notmatch '^(?i:y|yes)$') {
            throw "Environment setup cancelled by the user."
        }
    }

    foreach ($packageId in $packages.Keys) {
        Write-LpmSection "Installing $($packages[$packageId])"
        $override = $null
        if ($packageId -eq "Microsoft.VisualStudio.2022.BuildTools") {
            $override = "--wait --passive --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
        }
        Install-LpmWingetPackage -WingetPath $snapshot.Winget -PackageId $packageId -Override $override -NonInteractive:$NonInteractive
    }

    Update-LpmProcessPath
    $snapshot = Get-EnvironmentSnapshot
    if (-not $snapshot.Python -or -not $snapshot.Node -or -not $snapshot.Npm -or
        -not $snapshot.Git -or -not $snapshot.Rust.Rustup -or -not $snapshot.VisualStudio) {
        Write-EnvironmentSnapshot -Snapshot $snapshot
        throw "One or more installed tools are still unavailable. A package may require a Windows restart; restart if requested, then rerun the same command."
    }

    Add-LpmPathDirectories -Directories @(
        (Split-Path -Parent $snapshot.Python.Path),
        (Split-Path -Parent $snapshot.Node.Path),
        (Split-Path -Parent $snapshot.Npm),
        (Split-Path -Parent $snapshot.Git),
        (Split-Path -Parent $snapshot.Rust.Cargo),
        (Split-Path -Parent $snapshot.Rust.Rustup)
    )

    Write-LpmSection "Ensuring Rust MSVC toolchain"
    $env:RUSTUP_TOOLCHAIN = "stable-$expectedTarget"
    $rust = Get-LpmRustTools
    if ($rust.Target -ne $expectedTarget) {
        Invoke-LpmNative -FilePath $snapshot.Rust.Rustup -ArgumentList @(
            "toolchain", "install", "stable-$expectedTarget", "--profile", "minimal"
        ) -Description "Rust MSVC toolchain installation" | Out-Null
        $rust = Get-LpmRustTools
    }
    if ($rust.Target -ne $expectedTarget) {
        throw "Rust target mismatch after toolchain setup. expected=$expectedTarget actual=$($rust.Target); detail=$($rust.Error)"
    }

    Write-LpmSection "Loading Visual Studio C++ environment"
    Enable-LpmVisualStudioEnvironment -InstallationPath $snapshot.VisualStudio
    $link = Resolve-LpmExecutable -Names @("link.exe")
    if (-not $link) {
        throw "Visual Studio C++ linker link.exe was not found after loading VsDevCmd.bat."
    }
    Write-Host "  linker: $link"

    if ((Test-Path -LiteralPath $venvDirectory -PathType Container) -and -not (Test-VenvInterpreterReady -PythonPath $venvPython)) {
        $backupName = ".venv.backup-" + (Get-Date -Format "yyyyMMdd-HHmmss")
        $backupPath = Join-Path $repoRoot $backupName
        Assert-LpmDirectChild -Path $venvDirectory -Parent $repoRoot | Out-Null
        Assert-LpmDirectChild -Path $backupPath -Parent $repoRoot | Out-Null
        Write-LpmSection "Backing up incompatible virtual environment"
        Move-Item -LiteralPath $venvDirectory -Destination $backupPath
        Write-Host "  backup: $backupPath"
    }
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        Write-LpmSection "Creating repository virtual environment"
        Invoke-LpmNative -FilePath $snapshot.Python.Path -ArgumentList @("-m", "venv", $venvDirectory) -Description "Python virtual environment creation" | Out-Null
    }

    Write-LpmSection "Installing Python build dependencies"
    Invoke-LpmNative -FilePath $venvPython -ArgumentList @(
        "-m", "pip", "install", "--disable-pip-version-check", "-e", ".[dev,desktop]"
    ) -WorkingDirectory $repoRoot -Description "Python dependency installation" | Out-Null

    Write-LpmSection "Installing locked frontend dependencies"
    Invoke-LpmNative -FilePath $snapshot.Npm -ArgumentList @(
        "ci", "--ignore-scripts", "--no-audit", "--no-fund"
    ) -WorkingDirectory $desktopDirectory -Description "frontend dependency installation" | Out-Null

    if (-not (Test-VenvReady -PythonPath $venvPython) -or -not (Test-NodeModulesReady -DesktopDirectory $desktopDirectory)) {
        throw "Project dependency verification failed after installation."
    }

    Write-LpmSection "Environment ready"
    Write-Host "  python : $venvPython"
    Write-Host "  node   : $($snapshot.Node.Path)"
    Write-Host "  npm    : $($snapshot.Npm)"
    Write-Host "  git    : $($snapshot.Git)"
    Write-Host "  cargo  : $($rust.Cargo)"
    Write-Host "  rustc  : $($rust.Rustc)"
    Write-Host "  target : $($rust.Target)"
    Write-Host "  msvc   : $($snapshot.VisualStudio)"
    exit 0
} catch {
    Write-Host ""
    Write-Host "Environment setup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
