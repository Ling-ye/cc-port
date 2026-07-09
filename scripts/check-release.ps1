$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
  $Py = Join-Path $Root ".venv/bin/python"
}
if (-not (Test-Path $Py)) {
  $Py = "python"
}

Push-Location $Root
try {
  & $Py -m pytest -q -s
  & $Py -m ruff check src/lpm tests
  Push-Location (Join-Path $Root "desktop")
  try {
    npm run build
  } finally {
    Pop-Location
  }
  & $Py tools/packaging/sidecar/build_sidecar.py
  Push-Location (Join-Path $Root "desktop")
  try {
    npm run tauri -- build
  } finally {
    Pop-Location
  }

  Write-Host "`nRelease artifacts:"
  $Bundle = Join-Path $Root "desktop/src-tauri/target/release/bundle"
  if (Test-Path $Bundle) {
    Get-ChildItem $Bundle -Recurse -File | Sort-Object FullName | ForEach-Object { $_.FullName }
  }
  $Binaries = Join-Path $Root "desktop/src-tauri/binaries"
  if (Test-Path $Binaries) {
    Get-ChildItem $Binaries -File | Sort-Object FullName | ForEach-Object { $_.FullName }
  }
} finally {
  Pop-Location
}
