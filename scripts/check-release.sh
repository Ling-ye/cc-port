#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

cd "$ROOT"
"$PY" -m pytest -q -s
"$PY" -m ruff check src/lpm tests
(
  cd desktop
  npm run build
)
"$PY" tools/packaging/sidecar/build_sidecar.py
(
  cd desktop
  npm run tauri -- build
)

printf '
Release artifacts:
'
find "$ROOT/desktop/src-tauri/target/release/bundle" -maxdepth 3 -type f 2>/dev/null | sort || true
find "$ROOT/desktop/src-tauri/binaries" -maxdepth 1 -type f 2>/dev/null | sort || true
