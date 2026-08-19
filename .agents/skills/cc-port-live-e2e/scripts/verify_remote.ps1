[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RepositoryUrl,
    [Parameter(Mandatory = $true)][string]$Branch,
    [Parameter(Mandatory = $true)][string]$ExpectedCommit,
    [Parameter(Mandatory = $true)][string]$ExpectedSkillSha256,
    [Parameter(Mandatory = $true)][string]$ExpectedProofSha256,
    [Parameter(Mandatory = $true)][string]$ReportPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-EvidenceDirectory {
    param([string]$Path)
    $directory = [IO.DirectoryInfo]::new([IO.Path]::GetFullPath($Path).TrimEnd("\"))
    if ($directory.Name -notmatch '^e2e-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$' -or
        $null -eq $directory.Parent -or $directory.Parent.Name -ne "live-e2e" -or
        $null -eq $directory.Parent.Parent -or $directory.Parent.Parent.Name -ne "build") {
        throw "Report must stay in a generated build/live-e2e run directory."
    }
}

if ($RepositoryUrl -notmatch '^https://github\.com/[^/]+/cc-port-e2e-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}(?:\.git)?$') {
    throw "RepositoryUrl is not a generated CC Port E2E repository."
}
if ($Branch -ne "main") {
    throw "Live E2E verification currently requires the main branch."
}
if ($ExpectedCommit -notmatch '^[0-9a-fA-F]{40,64}$' -or
    $ExpectedSkillSha256 -notmatch '^[0-9a-fA-F]{64}$' -or
    $ExpectedProofSha256 -notmatch '^[0-9a-fA-F]{64}$') {
    throw "Expected commit or content hash is malformed."
}
$ReportPath = [IO.Path]::GetFullPath($ReportPath)
if ([IO.Path]::GetFileName($ReportPath) -ne "remote-upload-verification.json") {
    throw "Remote verification report must be named remote-upload-verification.json."
}
if (Test-Path -LiteralPath $ReportPath) {
    throw "Refusing to overwrite an existing remote-verification report."
}
$reportParent = Split-Path -Parent $ReportPath
Assert-EvidenceDirectory -Path $reportParent
if (-not (Test-Path -LiteralPath $reportParent -PathType Container)) {
    throw "Report directory does not exist."
}
if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
    throw "Git for Windows is not available."
}

$tempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\")
$clone = Join-Path $tempParent ("cc-port-remote-verify-" + [guid]::NewGuid().ToString("N"))
try {
    & git -c core.autocrlf=false clone --quiet --depth 2 --branch $Branch --single-branch $RepositoryUrl $clone
    if ($LASTEXITCODE -ne 0) {
        throw "Native Windows depth-2 clone failed."
    }
    $head = (& git -C $clone rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $head -ne $ExpectedCommit) {
        throw "Cloned HEAD did not match the expected upload commit."
    }
    $status = @(& git -C $clone status --porcelain)
    if ($LASTEXITCODE -ne 0 -or $status.Count -ne 0) {
        throw "Verification clone is not clean."
    }
    $changedPaths = @(& git -C $clone diff-tree --no-commit-id --name-only -r HEAD | Where-Object { $_ })
    $expectedPaths = @(
        "registry.yaml",
        "skills/cc-port-e2e-skill/SKILL.md",
        "skills/cc-port-e2e-skill/references/proof.md"
    )
    if (@(Compare-Object -ReferenceObject $expectedPaths -DifferenceObject $changedPaths).Count -ne 0) {
        throw "The upload commit changed paths outside the expected Registry and Skill scope."
    }

    $registryText = [IO.File]::ReadAllText((Join-Path $clone "registry.yaml"), [Text.Encoding]::UTF8)
    if ($registryText -notmatch "(?m)^version:\s*1\s*$" -or
        $registryText -notmatch "(?m)^\s*-\s+kind:\s*skill\s*$" -or
        $registryText -notmatch "(?m)^\s+name:\s*cc-port-e2e-skill\s*$" -or
        $registryText -notmatch "(?m)^\s+path:\s*skills/cc-port-e2e-skill\s*$" -or
        [regex]::Matches($registryText, "(?m)^\s*-\s+kind:").Count -ne 1) {
        throw "Registry v1 does not contain exactly the expected test Skill entry."
    }

    $skillHash = [string](Get-FileHash -LiteralPath (Join-Path $clone "skills\cc-port-e2e-skill\SKILL.md") -Algorithm SHA256).Hash
    $proofHash = [string](Get-FileHash -LiteralPath (Join-Path $clone "skills\cc-port-e2e-skill\references\proof.md") -Algorithm SHA256).Hash
    if (-not $skillHash.Equals($ExpectedSkillSha256, [StringComparison]::OrdinalIgnoreCase) -or
        -not $proofHash.Equals($ExpectedProofSha256, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Remote Skill bytes do not match the original fixture."
    }

    $report = [ordered]@{
        verifiedAtUtc = [DateTime]::UtcNow.ToString("o")
        interface = "Git for Windows depth-2 clone with core.autocrlf=false"
        repositoryUrl = $RepositoryUrl
        branch = $Branch
        head = $head
        clean = $true
        changedPaths = $changedPaths
        registryVersion = 1
        registryResourceCount = 1
        registryResourceKey = "skill:cc-port-e2e-skill"
        registryPath = "skills/cc-port-e2e-skill"
        skillSha256 = $skillHash
        proofSha256 = $proofHash
        bytesMatchOriginalFixture = $true
    }
    [IO.File]::WriteAllText($ReportPath, ($report | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    Write-Output ($report | ConvertTo-Json -Compress)
} finally {
    if (Test-Path -LiteralPath $clone) {
        $fullClone = [IO.Path]::GetFullPath($clone).TrimEnd("\")
        if (-not $fullClone.StartsWith($tempParent + "\", [StringComparison]::OrdinalIgnoreCase)) {
            throw "Verification clone escaped the Windows temporary directory."
        }
        Remove-Item -LiteralPath $clone -Recurse -Force
    }
}
