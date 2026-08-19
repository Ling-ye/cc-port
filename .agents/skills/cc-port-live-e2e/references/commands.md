# Native Windows command map

Use these templates only after the authority gate in the root Skill is satisfied. Replace angle
brackets from reviewed JSON fields; never paste credentials into a command. Run the UI and MCP
drivers with native Windows Node.js and the repository's Windows Python environment.

## Set reviewed local paths

```powershell
$Repo = "D:\path\to\cc-port"
$Skill = Join-Path $Repo ".agents\skills\cc-port-live-e2e"
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$Node = (Get-Command node.exe -ErrorAction Stop).Source
$Installer = "D:\path\to\cc-port_0.6.0_windows_x64_setup.exe"
$EvidenceRoot = Join-Path $Repo "build\live-e2e"
```

Do not continue if `$Python`, `$Node`, or `$Installer` is missing. Do not choose an existing
profile, configuration, state directory, or resource checkout.

## Preflight and repository initialization

```powershell
& $Python (Join-Path $Skill "scripts\preflight.py") `
  --repo-root $Repo `
  --installer $Installer `
  --evidence-root $EvidenceRoot
```

Read the printed output path, then set:

```powershell
$Evidence = "D:\path\to\cc-port\build\live-e2e\<run-id>"
$Preflight = Get-Content (Join-Path $Evidence "preflight.json") -Raw | ConvertFrom-Json
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Skill "scripts\create_private_repo.ps1") `
  -RepositoryName $Preflight.repository_name `
  -ReportPath (Join-Path $Evidence "github-repo.json")
$GitHub = Get-Content (Join-Path $Evidence "github-repo.json") -Raw | ConvertFrom-Json
```

If the source checkout is intentionally dirty, review every status entry before adding
`--allow-dirty`. Do not add `--allow-staged` unless the staged index is also explicitly accepted
as the baseline.

## Start the isolated installed session

Keep this command running in its own terminal until the stop signal is written:

```powershell
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Skill "scripts\session.ps1") `
  -InstallerPath $Installer `
  -ExpectedInstallerSha256 $Preflight.installer.sha256 `
  -RepositoryName $GitHub.repository `
  -RepositoryUrl $GitHub.cloneUrl `
  -Branch $GitHub.defaultBranch `
  -EvidenceDirectory $Evidence
```

Continue only after `session-context.json` exists and the session reports `READY`:

```powershell
$Context = Join-Path $Evidence "session-context.json"
$UploadState = Join-Path $Evidence "upload-plan-state.json"
$DownloadState = Join-Path $Evidence "download-plan-state.json"
```

## Enable AI integration and upload

```powershell
& $Node (Join-Path $Skill "scripts\ui_driver.mjs") enable $Context (Join-Path $Evidence "ui-enable.json")
& $Python (Join-Path $Skill "scripts\mcp_roundtrip.py") inventory $Context (Join-Path $Evidence "inventory-initial.json") $UploadState
& $Python (Join-Path $Skill "scripts\mcp_roundtrip.py") plan-upload $Context (Join-Path $Evidence "plan-upload.json") $UploadState
$UploadPlan = Get-Content (Join-Path $Evidence "upload-plan-state.json") -Raw | ConvertFrom-Json
& $Node (Join-Path $Skill "scripts\ui_driver.mjs") approve $Context (Join-Path $Evidence "ui-approve-upload.json") $UploadPlan.operationId
& $Python (Join-Path $Skill "scripts\mcp_roundtrip.py") apply-upload $Context (Join-Path $Evidence "apply-upload.json") $UploadState
```

Inspect each command's report before continuing. A nonzero exit, stale plan, blocked plan, warning,
operation mismatch, or approval mismatch ends the phase.

## Verify the real remote commit

```powershell
$Session = Get-Content $Context -Raw | ConvertFrom-Json
$Upload = Get-Content (Join-Path $Evidence "apply-upload.json") -Raw | ConvertFrom-Json
$UploadCommit = $Upload.result.apply.remoteCommit
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Skill "scripts\verify_remote.ps1") `
  -RepositoryUrl $GitHub.cloneUrl `
  -Branch $GitHub.defaultBranch `
  -ExpectedCommit $UploadCommit `
  -ExpectedSkillSha256 $Session.fixtureSkillSha256 `
  -ExpectedProofSha256 $Session.fixtureProofSha256 `
  -ReportPath (Join-Path $Evidence "remote-upload-verification.json")
```

## Download and install the same Skill

```powershell
& $Python (Join-Path $Skill "scripts\mcp_roundtrip.py") prepare-download $Context (Join-Path $Evidence "prepare-download.json") $DownloadState
& $Python (Join-Path $Skill "scripts\mcp_roundtrip.py") plan-download $Context (Join-Path $Evidence "plan-download.json") $DownloadState
$DownloadPlan = Get-Content (Join-Path $Evidence "download-plan-state.json") -Raw | ConvertFrom-Json
& $Node (Join-Path $Skill "scripts\ui_driver.mjs") approve $Context (Join-Path $Evidence "ui-approve-download.json") $DownloadPlan.operationId
& $Python (Join-Path $Skill "scripts\mcp_roundtrip.py") apply-download $Context (Join-Path $Evidence "apply-download.json") $DownloadState
& $Python (Join-Path $Skill "scripts\mcp_roundtrip.py") verify-downloaded-files $Context (Join-Path $Evidence "verify-download-files.json") $DownloadState
```

## Uninstall, stop, and validate

```powershell
& $Node (Join-Path $Skill "scripts\ui_driver.mjs") uninstall $Context (Join-Path $Evidence "ui-uninstall.json")
[IO.File]::WriteAllText((Join-Path $Evidence "session-stop.signal"), "stop", [Text.UTF8Encoding]::new($false))
```

Wait for the session terminal to finish and inspect `session-cleanup.json`. Then resolve the final
remote head through Windows Git and validate the evidence:

```powershell
$FinalRemoteHead = ((& git.exe ls-remote $GitHub.cloneUrl "refs/heads/main") -split "\s+")[0]
& $Python (Join-Path $Skill "scripts\validate_evidence.py") `
  --evidence-dir $Evidence `
  --repo-root $Repo `
  --final-remote-head $FinalRemoteHead `
  --output (Join-Path $Evidence "validated-summary.json")
```

Retain the private repository and evidence by default. Repository deletion is never part of these
commands.
