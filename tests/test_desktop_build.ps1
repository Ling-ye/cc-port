#!/usr/bin/env powershell
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Import-Module (Join-Path $RepoRoot "scripts\desktop-build.psm1") -Force -ErrorAction Stop

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

$tempParent = [IO.Path]::GetTempPath().TrimEnd("\")
$tempRoot = Join-Path $tempParent ("lpm-desktop-build-tests-" + [guid]::NewGuid().ToString("N"))
Assert-LpmDirectChild -Path $tempRoot -Parent $tempParent | Out-Null
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
        Assert-True -Condition (Test-LpmPythonVersion -Version ([version]"3.10.0")) -Message "Python 3.10 should be accepted"
        Assert-True -Condition (Test-LpmPythonVersion -Version ([version]"3.12.9")) -Message "Python 3.12 should be accepted"
        Assert-True -Condition (-not (Test-LpmPythonVersion -Version ([version]"3.9.19"))) -Message "Python 3.9 should be rejected"
        Assert-True -Condition (-not (Test-LpmPythonVersion -Version ([version]"3.13.0"))) -Message "Python 3.13 should be rejected"
    }

    Invoke-Test "Node.js version policy" {
        Assert-True -Condition (-not (Test-LpmNodeVersion -Value "v20.18.1")) -Message "Node 20.18 should be rejected"
        Assert-True -Condition (Test-LpmNodeVersion -Value "v20.19.0") -Message "Node 20.19 should be accepted"
        Assert-True -Condition (-not (Test-LpmNodeVersion -Value "v22.11.0")) -Message "Node 22.11 should be rejected"
        Assert-True -Condition (Test-LpmNodeVersion -Value "v22.12.0") -Message "Node 22.12 should be accepted"
        Assert-True -Condition (Test-LpmNodeVersion -Value "v24.1.0") -Message "Node 24 should be accepted"
    }

    Invoke-Test "Rust tuple parsing" {
        $escape = [string][char]27
        $bom = [string][char]0xFEFF
        $output = $bom + $escape + "[32m  x86_64-pc-windows-msvc  " + $escape + "[0m"
        Assert-Equal -Expected "x86_64-pc-windows-msvc" -Actual (Get-LpmRustHostFromTuple -Output $output) -Message "Rust tuple parser"
    }

    Invoke-Test "Rust verbose parsing tolerates decorated output" {
        $escape = [string][char]27
        $bom = [string][char]0xFEFF
        $output = "rustc 1.90.0`r`n" + $bom + $escape + "[36m   HOST : x86_64-pc-windows-msvc   " + $escape + "[0m`r`n"
        Assert-Equal -Expected "x86_64-pc-windows-msvc" -Actual (Get-LpmRustHostFromVerbose -Output $output) -Message "Rust verbose parser"
    }

    Invoke-Test "Explicit executable fallback" {
        $fake = Join-Path $tempRoot "fallback-tool.exe"
        [IO.File]::WriteAllText($fake, "fixture")
        $resolved = Resolve-LpmExecutable -Names @("lpm-command-that-does-not-exist.exe") -Fallbacks @($fake)
        Assert-Equal -Expected ([IO.Path]::GetFullPath($fake)) -Actual $resolved -Message "Executable fallback"
    }

    Invoke-Test "Safe path guard" {
        $direct = Join-Path $tempRoot "direct-child"
        Assert-Equal -Expected ([IO.Path]::GetFullPath($direct)) -Actual (Assert-LpmDirectChild -Path $direct -Parent $tempRoot) -Message "Direct child should pass"
        $nested = Join-Path (Join-Path $tempRoot "nested") "child"
        Assert-Throws -Action { Assert-LpmDirectChild -Path $nested -Parent $tempRoot | Out-Null } -Pattern "outside the expected parent" -Message "Nested path should be rejected"
    }

    Invoke-Test "Windows package detection" {
        $packages = Join-Path $tempRoot "packages"
        $msiDirectory = Join-Path $packages "msi"
        $nsisDirectory = Join-Path $packages "nsis"
        New-Item -ItemType Directory -Path $msiDirectory, $nsisDirectory | Out-Null
        [IO.File]::WriteAllText((Join-Path $msiDirectory "LPM.msi"), "msi")
        Assert-Throws -Action { Get-LpmWindowsPackageArtifacts -ReleaseDirectory $packages | Out-Null } -Pattern "NSIS" -Message "Missing NSIS should fail"
        [IO.File]::WriteAllText((Join-Path $nsisDirectory "LPM-setup.exe"), "nsis")
        Assert-Equal -Expected 2 -Actual @(Get-LpmWindowsPackageArtifacts -ReleaseDirectory $packages).Count -Message "MSI and NSIS should pass"
    }

    Invoke-Test "Verified release replacement" {
        $publishRoot = Join-Path $tempRoot "publish-success"
        $final = Join-Path $publishRoot "x86_64-pc-windows-msvc"
        $staging = Join-Path $publishRoot ".x86_64-pc-windows-msvc.staging"
        New-Item -ItemType Directory -Path $final, $staging | Out-Null
        [IO.File]::WriteAllText((Join-Path $final "artifact.txt"), "old")
        [IO.File]::WriteAllText((Join-Path $staging "artifact.txt"), "new")
        Publish-LpmStagingDirectory -StagingDirectory $staging -FinalDirectory $final -ReleaseRoot $publishRoot
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
            Publish-LpmStagingDirectory -StagingDirectory $missingStaging -FinalDirectory $final -ReleaseRoot $publishRoot
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
            Publish-LpmStagingDirectory -StagingDirectory $staging -FinalDirectory $final -ReleaseRoot $publishRoot -MoveDirectory $moveDirectory
        } -Pattern "^Simulated staging publish failure\." -Message "Replacement failure should propagate"
        Assert-Equal -Expected "old" -Actual ([IO.File]::ReadAllText((Join-Path $final "artifact.txt"))) -Message "Prior release restoration"
        Assert-Equal -Expected "new" -Actual ([IO.File]::ReadAllText((Join-Path $staging "artifact.txt"))) -Message "Failed staging preservation"
        Assert-Equal -Expected 0 -Actual @(Get-ChildItem -LiteralPath $publishRoot -Force | Where-Object Name -like ".*.backup-*").Count -Message "Rollback backup cleanup"
    }

    Invoke-Test "Setup exposes cancel and missing WinGet paths" {
        $source = [IO.File]::ReadAllText((Join-Path $RepoRoot "scripts\setup.ps1"))
        Assert-True -Condition ($source -match 'Read-Host\s+"Continue with these actions') -Message "Interactive confirmation is missing"
        Assert-True -Condition ($source -match 'WinGet is required to install missing system tools') -Message "Missing WinGet guidance is missing"
    }

    Invoke-Test "Release forwards non-interactive setup explicitly" {
        $source = [IO.File]::ReadAllText((Join-Path $RepoRoot "scripts\release-desktop.ps1"))
        Assert-True -Condition ($source -match 'setup\.ps1"\) -NonInteractive') -Message "Release must bind the setup switch explicitly"
        Assert-True -Condition ($source -notmatch '@setupArguments') -Message "Array splatting must not be used for named setup switches"
    }

    Invoke-Test "Minimal Windows PATH stays short" {
        $minimal = Get-LpmMinimalWindowsPath
        Assert-True -Condition ($minimal.Length -lt 512) -Message "Minimal PATH must stay well under the cmd.exe limit"
        Assert-True -Condition ($minimal -match 'System32') -Message "Minimal PATH must include System32"
    }

    $visualStudioForPathTest = Get-LpmVisualStudioPath
    if ($visualStudioForPathTest) {
        Invoke-Test "VsDevCmd import tolerates oversized PATH" {
            $savedPath = $env:PATH
            $savedInclude = $env:INCLUDE
            $savedLib = $env:LIB
            try {
                $pad = (1..80 | ForEach-Object {
                    "C:\LpmPathPad\very\long\directory\name\segment$_\Scripts"
                }) -join ";"
                $env:PATH = $savedPath + ";" + $pad
                Assert-True -Condition ($env:PATH.Length -gt 7000) -Message "Test PATH must exceed the historical failure threshold"
                Enable-LpmVisualStudioEnvironment -InstallationPath $visualStudioForPathTest
                $link = Resolve-LpmExecutable -Names @("link.exe")
                Assert-True -Condition ($null -ne $link) -Message "link.exe must resolve after fat-PATH VsDevCmd import"
                Assert-True -Condition ($env:PATH -like "*LpmPathPad*") -Message "Caller PATH entries must be preserved"
            } finally {
                $env:PATH = $savedPath
                if ($null -eq $savedInclude) { Remove-Item Env:INCLUDE -ErrorAction SilentlyContinue } else { $env:INCLUDE = $savedInclude }
                if ($null -eq $savedLib) { Remove-Item Env:LIB -ErrorAction SilentlyContinue } else { $env:LIB = $savedLib }
            }
        }
    } else {
        Write-Host "  SKIP VsDevCmd import tolerates oversized PATH (Visual Studio not installed)"
    }
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-LpmSafePath -Path $tempRoot -Parent $tempParent
    }
}

Write-Host ""
Write-Host "PowerShell build self-tests passed: $script:Passed" -ForegroundColor Green
exit 0
