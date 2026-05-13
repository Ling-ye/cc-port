#!/usr/bin/env bash
# Run the LPM Desktop app in development mode.
#
# Builds the lpm-desktop-api sidecar binary first, then launches `npm run tauri dev`.
#
# Usage:
#   bash scripts/dev.sh
#   bash scripts/dev.sh --skip-sidecar

set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

SKIP_SIDECAR=0
for arg in "$@"; do
    case "${arg}" in
        --skip-sidecar) SKIP_SIDECAR=1 ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "Unknown argument: ${arg}" >&2; exit 2 ;;
    esac
done

if ! command -v cargo >/dev/null 2>&1 && [[ -x "${HOME}/.cargo/bin/cargo" ]]; then
    export PATH="${HOME}/.cargo/bin:${PATH}"
fi

PY="$(command -v python3 || command -v python)"

if [[ "${SKIP_SIDECAR}" -eq 0 ]]; then
    echo "==> Building lpm-desktop-api sidecar"
    "${PY}" "${REPO_ROOT}/tools/packaging/sidecar/build_sidecar.py"
fi

echo
echo "==> Starting Tauri dev shell"
cd "${REPO_ROOT}/desktop"
npm run tauri dev
