Set-StrictMode -Version Latest

$script:LpmExpectedTarget = "x86_64-pc-windows-msvc"
$script:LpmWingetHelpUrl = "https://learn.microsoft.com/windows/package-manager/winget/"

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

function Enable-LpmVisualStudioEnvironment {
    param([Parameter(Mandatory = $true)][string]$InstallationPath)

    $vsDevCmd = Join-Path $InstallationPath "Common7\Tools\VsDevCmd.bat"
    if (-not (Test-Path -LiteralPath $vsDevCmd -PathType Leaf)) {
        throw "VsDevCmd.bat was not found under: $InstallationPath"
    }
    $commandProcessor = if ($env:ComSpec) { $env:ComSpec } else { Join-Path $env:SystemRoot "System32\cmd.exe" }
    $command = '"' + $vsDevCmd + '" -no_logo -arch=x64 -host_arch=x64 >nul && set'
    $result = Invoke-LpmNative -FilePath $commandProcessor -ArgumentList @("/d", "/s", "/c", $command) -Capture -Description "Visual Studio developer environment"
    foreach ($line in $result.Output -split "`r?`n") {
        if ($line -notmatch '^([^=]+)=(.*)$') {
            continue
        }
        $name = $Matches[1]
        if ($name.StartsWith("=")) {
            continue
        }
        [Environment]::SetEnvironmentVariable($name, $Matches[2], "Process")
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
        [Parameter(Mandatory = $true)][string]$ReleaseRoot
    )

    $staging = Assert-LpmDirectChild -Path $StagingDirectory -Parent $ReleaseRoot
    $final = Assert-LpmDirectChild -Path $FinalDirectory -Parent $ReleaseRoot
    $backup = Join-Path $ReleaseRoot ("." + [IO.Path]::GetFileName($final) + ".backup-" + [guid]::NewGuid().ToString("N"))
    Assert-LpmDirectChild -Path $backup -Parent $ReleaseRoot | Out-Null
    $movedOld = $false
    try {
        if (Test-Path -LiteralPath $final) {
            Move-Item -LiteralPath $final -Destination $backup -ErrorAction Stop
            $movedOld = $true
        }
        Move-Item -LiteralPath $staging -Destination $final -ErrorAction Stop
    } catch {
        if ($movedOld -and (Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $final)) {
            Move-Item -LiteralPath $backup -Destination $final -ErrorAction Stop
        }
        throw
    }
    if (Test-Path -LiteralPath $backup) {
        Remove-LpmSafePath -Path $backup -Parent $ReleaseRoot
    }
}

Export-ModuleMember -Function @(
    "Add-LpmPathDirectories",
    "Assert-LpmDirectChild",
    "Enable-LpmVisualStudioEnvironment",
    "Get-LpmExpectedTarget",
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
    "Get-LpmVisualStudioPath",
    "Get-LpmWinget",
    "Get-LpmWingetHelpUrl",
    "Get-LpmWindowsPackageArtifacts",
    "Install-LpmWingetPackage",
    "Invoke-LpmNative",
    "Publish-LpmStagingDirectory",
    "Remove-LpmSafePath",
    "Resolve-LpmExecutable",
    "Test-LpmNodeVersion",
    "Test-LpmPythonVersion",
    "Update-LpmProcessPath",
    "Write-LpmSection"
)
