Set-StrictMode -Version Latest

$script:LpmExpectedTarget = "x86_64-pc-windows-msvc"
$script:LpmWingetHelpUrl = "https://learn.microsoft.com/windows/package-manager/winget/"
$script:LpmJsonCacheSchemaVersion = 1

function Get-LpmRepoRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Get-LpmExpectedTarget {
    return $script:LpmExpectedTarget
}

function Get-LpmWingetHelpUrl {
    return $script:LpmWingetHelpUrl
}

function Write-LpmSection {
    param([Parameter(Mandatory = $true)][string]$Title)

    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
}

function Get-LpmOutputExcerpt {
    param(
        [AllowNull()][string]$Value,
        [int]$MaximumLength = 1200
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return "<empty>"
    }
    $clean = $Value.Trim()
    if ($clean.Length -le $MaximumLength) {
        return $clean
    }
    return $clean.Substring(0, $MaximumLength) + "...<truncated>"
}

function Invoke-LpmNative {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$WorkingDirectory = (Get-LpmRepoRoot),
        [switch]$Capture,
        [switch]$AllowFailure,
        [string]$Description = "external command"
    )

    if (-not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
        throw "Working directory does not exist: $WorkingDirectory"
    }

    $displayArguments = @($ArgumentList | ForEach-Object {
        if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
    })
    $displayCommand = (@($FilePath) + $displayArguments) -join " "
    if (-not $Capture) {
        Write-Host "  `$ $displayCommand"
    }

    $priorPreference = $ErrorActionPreference
    Push-Location -LiteralPath $WorkingDirectory
    try {
        # Windows PowerShell 5.1 promotes native stderr to NativeCommandError when
        # ErrorActionPreference is Stop. Native exit codes remain authoritative.
        $ErrorActionPreference = "Continue"
        if ($Capture) {
            $lines = @(& $FilePath @ArgumentList 2>&1)
            $exitCode = $LASTEXITCODE
            $output = ($lines | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
        } else {
            & $FilePath @ArgumentList 2>&1 | ForEach-Object { Write-Host $_.ToString() }
            $exitCode = $LASTEXITCODE
            $output = ""
        }
    } finally {
        $ErrorActionPreference = $priorPreference
        Pop-Location
    }

    $result = [pscustomobject]@{
        ExitCode = [int]$exitCode
        Output   = $output
        Command  = $displayCommand
    }
    if ($result.ExitCode -ne 0 -and -not $AllowFailure) {
        $detail = Get-LpmOutputExcerpt -Value $result.Output
        throw "$Description failed (exit $($result.ExitCode)): $detail"
    }
    return $result
}

function Resolve-LpmExecutable {
    [CmdletBinding()]
    param(
        [string[]]$Names = @(),
        [AllowEmptyCollection()][string[]]$Fallbacks = @()
    )

    foreach ($name in $Names) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($command) {
            $source = if ($command.Source) { $command.Source } else { $command.Path }
            if ($source -and (Test-Path -LiteralPath $source -PathType Leaf)) {
                return (Resolve-Path -LiteralPath $source).Path
            }
        }
    }
    foreach ($candidate in $Fallbacks) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

function Add-LpmPathDirectories {
    param([AllowEmptyCollection()][string[]]$Directories = @())

    $ordered = New-Object System.Collections.Generic.List[string]
    $seen = @{}
    foreach ($directory in @($Directories) + @($env:PATH -split ";")) {
        if ([string]::IsNullOrWhiteSpace($directory)) {
            continue
        }
        $key = $directory.Trim().TrimEnd("\").ToLowerInvariant()
        if (-not $seen.ContainsKey($key)) {
            $ordered.Add($directory.Trim())
            $seen[$key] = $true
        }
    }
    $env:PATH = $ordered -join ";"
}

function Update-LpmProcessPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $directories = @()
    $directories += @($env:PATH -split ";")
    $directories += @($user -split ";")
    $directories += @($machine -split ";")
    Add-LpmPathDirectories -Directories $directories
}

function Test-LpmPythonVersion {
    param([Parameter(Mandatory = $true)][version]$Version)

    return $Version.Major -eq 3 -and $Version.Minor -ge 10 -and $Version.Minor -le 12
}

function Test-LpmNodeVersion {
    param([Parameter(Mandatory = $true)][string]$Value)

    $match = [regex]::Match($Value.Trim(), '^v?(\d+)\.(\d+)\.(\d+)$')
    if (-not $match.Success) {
        return $false
    }
    $major = [int]$match.Groups[1].Value
    $minor = [int]$match.Groups[2].Value
    return ($major -eq 20 -and $minor -ge 19) -or
        ($major -eq 22 -and $minor -ge 12) -or
        $major -gt 22
}

function Get-LpmPython {
    $local = $env:LOCALAPPDATA
    $programFiles = $env:ProgramFiles
    $fallbacks = @(
        $(if ($local) { Join-Path $local "Programs\Python\Python312\python.exe" }),
        $(if ($programFiles) { Join-Path $programFiles "Python312\python.exe" }),
        "C:\Python312\python.exe"
    ) | Where-Object { $_ }

    $candidates = New-Object System.Collections.Generic.List[string]
    $launcher = Resolve-LpmExecutable -Names @("py.exe", "py")
    if ($launcher) {
        $result = Invoke-LpmNative -FilePath $launcher -ArgumentList @(
            "-3.12", "-c", "import sys; print(sys.executable)"
        ) -Capture -AllowFailure -Description "Python launcher"
        if ($result.ExitCode -eq 0 -and (Test-Path -LiteralPath $result.Output.Trim() -PathType Leaf)) {
            $candidates.Add((Resolve-Path -LiteralPath $result.Output.Trim()).Path)
        }
    }
    foreach ($candidate in $fallbacks) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $candidates.Add((Resolve-Path -LiteralPath $candidate).Path)
        }
    }
    $pathPython = Resolve-LpmExecutable -Names @("python.exe", "python")
    if ($pathPython) {
        $candidates.Add($pathPython)
    }

    $seen = @{}
    foreach ($candidate in $candidates) {
        $key = $candidate.ToLowerInvariant()
        if ($seen.ContainsKey($key)) {
            continue
        }
        $seen[$key] = $true
        $probe = Invoke-LpmNative -FilePath $candidate -ArgumentList @(
            "-c",
            "import platform,sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{platform.architecture()[0]}')"
        ) -Capture -AllowFailure -Description "Python version probe"
        if ($probe.ExitCode -ne 0 -or $probe.Output.Trim() -notmatch '^(\d+\.\d+\.\d+)\|(64bit)$') {
            continue
        }
        $version = [version]$Matches[1]
        if (Test-LpmPythonVersion -Version $version) {
            return [pscustomobject]@{ Path = $candidate; Version = $version }
        }
    }
    return $null
}

function Get-LpmNode {
    $local = $env:LOCALAPPDATA
    $programFiles = $env:ProgramFiles
    $fallbacks = @(
        $(if ($programFiles) { Join-Path $programFiles "nodejs\node.exe" }),
        $(if ($local) { Join-Path $local "Programs\nodejs\node.exe" })
    ) | Where-Object { $_ }
    $nodePath = Resolve-LpmExecutable -Names @("node.exe", "node") -Fallbacks $fallbacks
    if (-not $nodePath) {
        return $null
    }
    $probe = Invoke-LpmNative -FilePath $nodePath -ArgumentList @("--version") -Capture -AllowFailure -Description "Node.js version probe"
    if ($probe.ExitCode -ne 0 -or -not (Test-LpmNodeVersion -Value $probe.Output)) {
        return $null
    }
    return [pscustomobject]@{ Path = $nodePath; Version = $probe.Output.Trim() }
}

function Get-LpmNpm {
    param([AllowNull()][string]$NodePath)

    $fallbacks = @()
    if ($NodePath) {
        $fallbacks += Join-Path (Split-Path -Parent $NodePath) "npm.cmd"
    }
    if ($env:ProgramFiles) {
        $fallbacks += Join-Path $env:ProgramFiles "nodejs\npm.cmd"
    }
    if ($env:LOCALAPPDATA) {
        $fallbacks += Join-Path $env:LOCALAPPDATA "Programs\nodejs\npm.cmd"
    }
    return Resolve-LpmExecutable -Names @("npm.cmd", "npm") -Fallbacks $fallbacks
}

function Get-LpmGit {
    $fallbacks = @()
    if ($env:ProgramFiles) {
        $fallbacks += Join-Path $env:ProgramFiles "Git\cmd\git.exe"
    }
    if ($env:LOCALAPPDATA) {
        $fallbacks += Join-Path $env:LOCALAPPDATA "Programs\Git\cmd\git.exe"
    }
    $root = Get-LpmRepoRoot
    if ([IO.Path]::GetPathRoot($root)) {
        $fallbacks += Join-Path ([IO.Path]::GetPathRoot($root)) "Git\cmd\git.exe"
    }
    return Resolve-LpmExecutable -Names @("git.exe", "git") -Fallbacks $fallbacks
}

function Remove-LpmTerminalSequences {
    param([AllowNull()][string]$Value)

    if ($null -eq $Value) {
        return ""
    }
    $escape = [regex]::Escape([string][char]27)
    $withoutAnsi = [regex]::Replace($Value, $escape + '\[[0-?]*[ -/]*[@-~]', '')
    return $withoutAnsi.Replace([string][char]0xFEFF, "")
}

function Get-LpmRustHostFromTuple {
    param([Parameter(Mandatory = $true)][string]$Output)

    $clean = Remove-LpmTerminalSequences -Value $Output
    foreach ($line in $clean -split "`r?`n") {
        $candidate = $line.Trim()
        if ($candidate -match '^[A-Za-z0-9_]+(?:-[A-Za-z0-9_.]+)+$') {
            return $candidate
        }
    }
    throw "rustc --print host-tuple did not return a valid target triple."
}

function Get-LpmRustHostFromVerbose {
    param([Parameter(Mandatory = $true)][string]$Output)

    $clean = Remove-LpmTerminalSequences -Value $Output
    foreach ($line in $clean -split "`r?`n") {
        $match = [regex]::Match($line, '^\s*host\s*:\s*(?<target>[A-Za-z0-9_]+(?:-[A-Za-z0-9_.]+)+)\s*$', 'IgnoreCase')
        if ($match.Success) {
            return $match.Groups['target'].Value
        }
    }
    throw "rustc -vV did not return a valid host target triple."
}

function Get-LpmRustTarget {
    param([Parameter(Mandatory = $true)][string]$RustcPath)

    $tuple = Invoke-LpmNative -FilePath $RustcPath -ArgumentList @("--print", "host-tuple") -Capture -AllowFailure -Description "rustc host tuple"
    if ($tuple.ExitCode -eq 0) {
        try {
            return Get-LpmRustHostFromTuple -Output $tuple.Output
        } catch {
            # Older wrappers sometimes accept the option but format it incorrectly.
        }
    }

    $verbose = Invoke-LpmNative -FilePath $RustcPath -ArgumentList @("-vV") -Capture -AllowFailure -Description "rustc verbose version"
    if ($verbose.ExitCode -eq 0) {
        try {
            return Get-LpmRustHostFromVerbose -Output $verbose.Output
        } catch {
            # The diagnostic below includes both attempts.
        }
    }

    $tupleOutput = Get-LpmOutputExcerpt -Value $tuple.Output
    $verboseOutput = Get-LpmOutputExcerpt -Value $verbose.Output
    throw "rustc did not report a valid Windows host target. rustc=$RustcPath; --print host-tuple exit=$($tuple.ExitCode), output=$tupleOutput; -vV exit=$($verbose.ExitCode), output=$verboseOutput"
}

function Get-LpmRustTools {
    $cargoFallbacks = @()
    if ($env:USERPROFILE) {
        $cargoFallbacks += Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe"
    }
    $cargo = Resolve-LpmExecutable -Names @("cargo.exe", "cargo") -Fallbacks $cargoFallbacks
    if (-not $cargo) {
        return [pscustomobject]@{ Cargo = $null; Rustc = $null; Rustup = $null; Target = $null; Error = "Cargo was not found." }
    }

    $cargoDirectory = Split-Path -Parent $cargo
    $rustup = Resolve-LpmExecutable -Names @("rustup.exe", "rustup") -Fallbacks @(
        (Join-Path $cargoDirectory "rustup.exe"),
        $(if ($env:USERPROFILE) { Join-Path $env:USERPROFILE ".cargo\bin\rustup.exe" })
    )

    # A rustc proxy next to the selected Cargo is coherent with that Cargo and
    # must win over unrelated PATH entries such as Conda shims.
    $rustc = Resolve-LpmExecutable -Fallbacks @((Join-Path $cargoDirectory "rustc.exe"))
    if (-not $rustc -and $rustup) {
        $which = Invoke-LpmNative -FilePath $rustup -ArgumentList @("which", "rustc") -Capture -AllowFailure -Description "rustup which rustc"
        if ($which.ExitCode -eq 0 -and (Test-Path -LiteralPath $which.Output.Trim() -PathType Leaf)) {
            $rustc = (Resolve-Path -LiteralPath $which.Output.Trim()).Path
        }
    }
    if (-not $rustc) {
        $rustc = Resolve-LpmExecutable -Names @("rustc.exe", "rustc") -Fallbacks @(
            $(if ($env:USERPROFILE) { Join-Path $env:USERPROFILE ".cargo\bin\rustc.exe" })
        )
    }
    if (-not $rustc) {
        return [pscustomobject]@{ Cargo = $cargo; Rustc = $null; Rustup = $rustup; Target = $null; Error = "rustc was not found." }
    }

    try {
        $target = Get-LpmRustTarget -RustcPath $rustc
        $errorText = $null
    } catch {
        $target = $null
        $errorText = $_.Exception.Message
    }
    return [pscustomobject]@{
        Cargo  = $cargo
        Rustc  = $rustc
        Rustup = $rustup
        Target = $target
        Error  = $errorText
    }
}

function Get-LpmWinget {
    return Resolve-LpmExecutable -Names @("winget.exe", "winget") -Fallbacks @(
        $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\winget.exe" })
    )
}

function Get-LpmVsWhere {
    $fallbacks = @()
    if (${env:ProgramFiles(x86)}) {
        $fallbacks += Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    }
    return Resolve-LpmExecutable -Names @("vswhere.exe", "vswhere") -Fallbacks $fallbacks
}

function Get-LpmVisualStudioPath {
    $vswhere = Get-LpmVsWhere
    if (-not $vswhere) {
        return $null
    }
    $probe = Invoke-LpmNative -FilePath $vswhere -ArgumentList @(
        "-latest",
        "-products", "*",
        "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
        "-property", "installationPath"
    ) -Capture -AllowFailure -Description "Visual Studio Build Tools probe"
    if ($probe.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($probe.Output)) {
        return $null
    }
    $path = $probe.Output.Trim()
    if (Test-Path -LiteralPath $path -PathType Container) {
        return (Resolve-Path -LiteralPath $path).Path
    }
    return $null
}

function Get-LpmMinimalWindowsPath {
    $systemRoot = if ($env:SystemRoot) { $env:SystemRoot } else { [Environment]::SystemDirectory }
    if (-not $systemRoot) {
        $systemRoot = "C:\Windows"
    } elseif ($systemRoot -match '\\System32$') {
        $systemRoot = Split-Path -Parent $systemRoot
    }
    return @(
        (Join-Path $systemRoot "System32"),
        (Join-Path $systemRoot "System32\Wbem"),
        (Join-Path $systemRoot "System32\WindowsPowerShell\v1.0"),
        $systemRoot
    ) -join ";"
}

function Get-LpmVisualStudioTransientVariableNames {
    # Variables that make a second VsDevCmd.bat entry expand past cmd.exe's
    # 8191-character limit when INCLUDE/EXTERNAL_INCLUDE/LIB* are re-prepended,
    # or that pin an old fat PATH via __VSCMD_PREINIT_PATH.
    $names = New-Object System.Collections.Generic.List[string]
    $seen = @{}
    foreach ($entry in @(Get-ChildItem Env: -ErrorAction SilentlyContinue)) {
        $name = [string]$entry.Name
        if ($name -like "__VSCMD_*" -or $name -like "VSCMD_*") {
            $key = $name.ToLowerInvariant()
            if (-not $seen.ContainsKey($key)) {
                $names.Add($name)
                $seen[$key] = $true
            }
        }
    }
    foreach ($name in @(
            "INCLUDE",
            "LIB",
            "LIBPATH",
            "EXTERNAL_INCLUDE",
            "VSINSTALLDIR",
            "VCINSTALLDIR",
            "VS170COMNTOOLS",
            "DevEnvDir",
            "WindowsSdkDir",
            "WindowsSDKVersion",
            "WindowsSdkBinPath",
            "WindowsSdkVerBinPath",
            "WindowsSDK_ExecutablePath_x86",
            "WindowsSDK_ExecutablePath_x64",
            "UniversalCRTSdkDir",
            "WindowsLibPath",
            "UCRTVersion",
            "NETFXSDKDir",
            "VCToolsInstallDir",
            "VCToolsRedistDir",
            "VCToolsVersion",
            "VisualStudioVersion",
            "FSHARPINSTALLDIR",
            "FrameworkDir",
            "FrameworkDir64",
            "FrameworkVersion",
            "FrameworkVersion64",
            "Framework40Version",
            "IFCPATH",
            "ExtensionSdkDir",
            "HTMLHelpDir",
            "VCIDEInstallDir"
        )) {
        $key = $name.ToLowerInvariant()
        if ($seen.ContainsKey($key)) {
            continue
        }
        if ($null -ne [Environment]::GetEnvironmentVariable($name, "Process")) {
            $names.Add($name)
            $seen[$key] = $true
        }
    }
    return @($names)
}

function Enable-LpmVisualStudioEnvironment {
    param([Parameter(Mandatory = $true)][string]$InstallationPath)

    $vsDevCmd = Join-Path $InstallationPath "Common7\Tools\VsDevCmd.bat"
    if (-not (Test-Path -LiteralPath $vsDevCmd -PathType Leaf)) {
        throw "VsDevCmd.bat was not found under: $InstallationPath"
    }
    $commandProcessor = if ($env:ComSpec) { $env:ComSpec } else { Join-Path $env:SystemRoot "System32\cmd.exe" }
    $command = '"' + $vsDevCmd + '" -no_logo -arch=x64 -host_arch=x64 >nul && set'

    # VsDevCmd.bat expands PATH/INCLUDE/EXTERNAL_INCLUDE inside cmd.exe. A Conda
    # bloated PATH, or leftover VS vars from a previous import in the same shell
    # (especially __VSCMD_PREINIT_PATH / EXTERNAL_INCLUDE), crosses the 8191
    # character limit ("输入行太长"). Scrub those vars, run with a minimal PATH,
    # then merge MSVC directories back onto the caller's PATH.
    $savedPath = $env:PATH
    $savedTransient = @{}
    foreach ($name in Get-LpmVisualStudioTransientVariableNames) {
        $savedTransient[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, $null, "Process")
    }
    $env:PATH = Get-LpmMinimalWindowsPath
    try {
        $result = Invoke-LpmNative -FilePath $commandProcessor -ArgumentList @("/d", "/s", "/c", $command) -Capture -Description "Visual Studio developer environment"
        $vsPath = $null
        foreach ($line in $result.Output -split "`r?`n") {
            if ($line -notmatch '^([^=]+)=(.*)$') {
                continue
            }
            $name = $Matches[1]
            $value = $Matches[2]
            if ($name.StartsWith("=")) {
                continue
            }
            # Never persist VsDevCmd internal bookmarks into the parent process;
            # they poison the next import by restoring an old fat PATH/INCLUDE.
            if ($name -like "__VSCMD_*" -or $name -like "VSCMD_*") {
                continue
            }
            if ($name -ieq "Path") {
                $vsPath = $value
                continue
            }
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
        $env:PATH = $savedPath
        if ($vsPath) {
            Add-LpmPathDirectories -Directories @($vsPath -split ";")
        }
    } catch {
        $env:PATH = $savedPath
        foreach ($name in $savedTransient.Keys) {
            [Environment]::SetEnvironmentVariable($name, $savedTransient[$name], "Process")
        }
        throw
    }

    # Some Build Tools installations select the legacy winv6.3 placeholder
    # even though a Windows 10/11 SDK is installed. Repair the process-only
    # SDK paths so release linking cannot pass setup and then miss kernel32.lib.
    $hasKernel32 = @($env:LIB -split ";") | Where-Object {
        $_ -and (Test-Path -LiteralPath (Join-Path $_ "kernel32.lib") -PathType Leaf)
    }
    if (-not $hasKernel32) {
        $programFilesX86 = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFilesX86)
        $kitRoot = Join-Path $programFilesX86 "Windows Kits\10"
        $libRoot = Join-Path $kitRoot "Lib"
        $sdk = @(Get-ChildItem -LiteralPath $libRoot -Directory -ErrorAction SilentlyContinue | Where-Object {
            $_.Name -match '^\d+\.\d+\.\d+\.\d+$' -and
            (Test-Path -LiteralPath (Join-Path $_.FullName "um\x64\kernel32.lib") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $_.FullName "ucrt\x64\ucrt.lib") -PathType Leaf)
        } | Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1)
        if ($sdk.Count -eq 0) {
            throw "A Windows 10/11 x64 SDK with kernel32.lib and ucrt.lib is required."
        }

        $sdkVersion = $sdk[0].Name
        $sdkLibs = @(
            (Join-Path $sdk[0].FullName "um\x64"),
            (Join-Path $sdk[0].FullName "ucrt\x64")
        )
        $includeRoot = Join-Path (Join-Path $kitRoot "Include") $sdkVersion
        $sdkIncludes = @("ucrt", "shared", "um", "winrt", "cppwinrt") | ForEach-Object {
            Join-Path $includeRoot $_
        } | Where-Object { Test-Path -LiteralPath $_ -PathType Container }
        $env:LIB = (@($env:LIB) + $sdkLibs | Where-Object { $_ }) -join ";"
        $env:INCLUDE = (@($env:INCLUDE) + $sdkIncludes | Where-Object { $_ }) -join ";"
        $env:WindowsSdkDir = $kitRoot + "\"
        $env:WindowsSDKVersion = $sdkVersion + "\"
        $env:UniversalCRTSdkDir = $kitRoot + "\"
        Add-LpmPathDirectories -Directories @(
            (Join-Path (Join-Path (Join-Path $kitRoot "bin") $sdkVersion) "x64")
        )
    }
}

function Install-LpmWingetPackage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$WingetPath,
        [Parameter(Mandatory = $true)][string]$PackageId,
        [AllowNull()][string]$Override,
        [switch]$NonInteractive
    )

    $common = @("--id", $PackageId, "--exact", "--source", "winget", "--accept-source-agreements")
    Invoke-LpmNative -FilePath $WingetPath -ArgumentList (@("show") + $common) -Description "WinGet package verification ($PackageId)" | Out-Null

    $arguments = @("install") + $common + @("--accept-package-agreements")
    if ($NonInteractive) {
        $arguments += @("--silent", "--disable-interactivity")
    }
    if ($Override) {
        $arguments += @("--override", $Override)
    }
    Invoke-LpmNative -FilePath $WingetPath -ArgumentList $arguments -Description "WinGet install ($PackageId)" | Out-Null
}

function Assert-LpmDirectChild {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Parent
    )

    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd("\")
    $fullParent = [IO.Path]::GetFullPath($Parent).TrimEnd("\")
    $actualParent = [IO.Path]::GetDirectoryName($fullPath).TrimEnd("\")
    if (-not $actualParent.Equals($fullParent, [StringComparison]::OrdinalIgnoreCase) -or
        [string]::IsNullOrWhiteSpace([IO.Path]::GetFileName($fullPath))) {
        throw "Refusing to modify a path outside the expected parent. path=$fullPath parent=$fullParent"
    }
    return $fullPath
}

function Remove-LpmSafePath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Parent
    )

    $safePath = Assert-LpmDirectChild -Path $Path -Parent $Parent
    if (Test-Path -LiteralPath $safePath) {
        Remove-Item -LiteralPath $safePath -Recurse -Force -ErrorAction Stop
    }
}

function Get-LpmStringHash {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [AllowNull()]
        [string]$Value
    )

    $bytes = [Text.Encoding]::UTF8.GetBytes([string]$Value)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = $algorithm.ComputeHash($bytes)
    } finally {
        $algorithm.Dispose()
    }
    return ([BitConverter]::ToString($hash).Replace("-", "").ToLowerInvariant())
}

function ConvertTo-LpmFingerprintPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "Fingerprint path must not be empty."
    }
    return [IO.Path]::GetFullPath($Path).Replace("\", "/").ToLowerInvariant()
}

function Get-LpmDependencyInputFingerprint {
    param(
        [Parameter(Mandatory = $true)][string]$ContentFingerprint,
        [Parameter(Mandatory = $true)][string]$PythonVersion,
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$NodeVersion,
        [Parameter(Mandatory = $true)][string]$NodePath,
        [Parameter(Mandatory = $true)][string]$NpmVersion,
        [Parameter(Mandatory = $true)][string]$NpmPath,
        [Parameter(Mandatory = $true)][string]$Platform
    )

    $descriptor = @(
        "dependencyPolicyVersion=1",
        "content=$($ContentFingerprint.Trim())",
        "pythonVersion=$($PythonVersion.Trim())",
        "pythonPath=$(ConvertTo-LpmFingerprintPath -Path $PythonPath)",
        "nodeVersion=$($NodeVersion.Trim())",
        "nodePath=$(ConvertTo-LpmFingerprintPath -Path $NodePath)",
        "npmVersion=$($NpmVersion.Trim())",
        "npmPath=$(ConvertTo-LpmFingerprintPath -Path $NpmPath)",
        "platform=$($Platform.Trim())"
    ) -join "`n"
    return Get-LpmStringHash -Value $descriptor
}

function Get-LpmFileHashValue {
    param([Parameter(Mandatory = $true)][string]$Path)

    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = $algorithm.ComputeHash($stream)
    } finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
    return ([BitConverter]::ToString($hash).Replace("-", "").ToLowerInvariant())
}

function Get-LpmContentFingerprint {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string[]]$Paths
    )

    $entriesByPath = @{}
    foreach ($path in $Paths) {
        if ([string]::IsNullOrWhiteSpace($path)) {
            throw "Fingerprint path must not be empty."
        }
        $fullPath = [IO.Path]::GetFullPath($path)
        if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
            throw "Fingerprint input file does not exist: $fullPath"
        }
        $normalizedPath = ConvertTo-LpmFingerprintPath -Path $fullPath
        $entriesByPath[$normalizedPath] = Get-LpmFileHashValue -Path $fullPath
    }

    $entries = New-Object System.Collections.Generic.List[string]
    foreach ($normalizedPath in $entriesByPath.Keys) {
        $entries.Add("$normalizedPath`0$($entriesByPath[$normalizedPath])")
    }
    $entries.Sort([StringComparer]::Ordinal)
    return Get-LpmStringHash -Value ($entries -join "`n")
}

function Read-LpmJsonCache {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        return $null
    }
    try {
        $raw = [IO.File]::ReadAllText($fullPath)
        $document = $raw | ConvertFrom-Json -ErrorAction Stop
        if ($null -eq $document) {
            return $null
        }
        $schemaProperty = $document.PSObject.Properties["schemaVersion"]
        $valueProperty = $document.PSObject.Properties["value"]
        if ($null -eq $schemaProperty -or $null -eq $valueProperty -or
            [int]$schemaProperty.Value -ne $script:LpmJsonCacheSchemaVersion) {
            return $null
        }
        return $valueProperty.Value
    } catch {
        return $null
    }
}

function Write-LpmJsonCacheAtomically {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowNull()]$Value
    )

    $fullPath = [IO.Path]::GetFullPath($Path)
    $parent = [IO.Path]::GetDirectoryName($fullPath)
    if ([string]::IsNullOrWhiteSpace($parent)) {
        throw "JSON cache path must have a parent directory: $fullPath"
    }
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    $temporaryPath = Join-Path $parent ("." + [IO.Path]::GetFileName($fullPath) + ".tmp-" + [guid]::NewGuid().ToString("N"))
    $backupPath = Join-Path $parent ("." + [IO.Path]::GetFileName($fullPath) + ".backup-" + [guid]::NewGuid().ToString("N"))
    $envelope = [ordered]@{
        schemaVersion = $script:LpmJsonCacheSchemaVersion
        value = $Value
    }
    $json = ($envelope | ConvertTo-Json -Depth 32) + [Environment]::NewLine
    $encoding = New-Object Text.UTF8Encoding($false)
    try {
        [IO.File]::WriteAllText($temporaryPath, $json, $encoding)
        if (Test-Path -LiteralPath $fullPath -PathType Leaf) {
            [IO.File]::Replace($temporaryPath, $fullPath, $backupPath)
        } else {
            [IO.File]::Move($temporaryPath, $fullPath)
        }
    } finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $backupPath -PathType Leaf) {
            Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-LpmWindowsPackageArtifacts {
    param([Parameter(Mandatory = $true)][string]$ReleaseDirectory)

    $files = @(Get-ChildItem -LiteralPath $ReleaseDirectory -Recurse -File | Sort-Object FullName)
    $msi = @($files | Where-Object { $_.Extension -ieq ".msi" })
    $nsis = @($files | Where-Object { $_.Name -ilike "*-setup.exe" })
    if ($msi.Count -eq 0) {
        throw "Windows release did not produce an MSI installer."
    }
    if ($nsis.Count -eq 0) {
        throw "Windows release did not produce an NSIS installer."
    }
    return @($msi + $nsis)
}

function Publish-LpmStagingDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$StagingDirectory,
        [Parameter(Mandatory = $true)][string]$FinalDirectory,
        [Parameter(Mandatory = $true)][string]$ReleaseRoot,
        [AllowNull()][scriptblock]$MoveDirectory = $null
    )

    $staging = Assert-LpmDirectChild -Path $StagingDirectory -Parent $ReleaseRoot
    $final = Assert-LpmDirectChild -Path $FinalDirectory -Parent $ReleaseRoot
    if (-not (Test-Path -LiteralPath $staging -PathType Container)) {
        throw "Staging directory does not exist or is not a directory: $staging"
    }
    if ($null -eq $MoveDirectory) {
        $MoveDirectory = {
            param([string]$Source, [string]$Destination)
            Move-Item -LiteralPath $Source -Destination $Destination -ErrorAction Stop
        }
    }
    $backup = Join-Path $ReleaseRoot ("." + [IO.Path]::GetFileName($final) + ".backup-" + [guid]::NewGuid().ToString("N"))
    Assert-LpmDirectChild -Path $backup -Parent $ReleaseRoot | Out-Null
    $movedOld = $false
    $stage = "moving the previous release to its backup"
    try {
        if (Test-Path -LiteralPath $final) {
            & $MoveDirectory $final $backup | Out-Null
            $movedOld = $true
        }
        $stage = "moving the verified staging directory into place"
        & $MoveDirectory $staging $final | Out-Null
    } catch {
        $publishError = $_.Exception.Message
        if ($movedOld -and (Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $final)) {
            try {
                & $MoveDirectory $backup $final | Out-Null
            } catch {
                throw "Verified release switch failed during ${stage}: $publishError; rollback also failed: $($_.Exception.Message); backup=$backup"
            }
        }
        throw "Verified release switch failed during ${stage}: $publishError"
    }
    if (Test-Path -LiteralPath $backup) {
        try {
            Remove-LpmSafePath -Path $backup -Parent $ReleaseRoot
        } catch {
            Write-Warning "Verified release is active, but the previous release backup could not be removed: $backup ($($_.Exception.Message))"
        }
    }
}

Export-ModuleMember -Function @(
    "Add-LpmPathDirectories",
    "Assert-LpmDirectChild",
    "Enable-LpmVisualStudioEnvironment",
    "Get-LpmExpectedTarget",
    "Get-LpmContentFingerprint",
    "Get-LpmDependencyInputFingerprint",
    "Get-LpmGit",
    "Get-LpmNode",
    "Get-LpmNpm",
    "Get-LpmOutputExcerpt",
    "Get-LpmPython",
    "Get-LpmRepoRoot",
    "Get-LpmRustHostFromTuple",
    "Get-LpmRustHostFromVerbose",
    "Get-LpmRustTarget",
    "Get-LpmRustTools",
    "Get-LpmStringHash",
    "Get-LpmVisualStudioPath",
    "Get-LpmMinimalWindowsPath",
    "Get-LpmVisualStudioTransientVariableNames",
    "Get-LpmWinget",
    "Get-LpmWingetHelpUrl",
    "Get-LpmWindowsPackageArtifacts",
    "Install-LpmWingetPackage",
    "Invoke-LpmNative",
    "Publish-LpmStagingDirectory",
    "Read-LpmJsonCache",
    "Remove-LpmSafePath",
    "Resolve-LpmExecutable",
    "Test-LpmNodeVersion",
    "Test-LpmPythonVersion",
    "Update-LpmProcessPath",
    "Write-LpmJsonCacheAtomically",
    "Write-LpmSection"
)
