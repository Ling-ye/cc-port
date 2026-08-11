#!/usr/bin/env bash
# Run the CC Port desktop app in development mode.
#
# Builds the cc-port-desktop-api sidecar and public cc-port CLI/MCP agent
# binaries first, then uses `npm run tauri dev` as the internal Tauri
# development step.
#
# Usage:
#   bash scripts/dev.sh
#   bash scripts/dev.sh --skip-sidecar
#   bash scripts/dev.sh --skip-agent

set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

SKIP_SIDECAR=0
SKIP_AGENT=0
for arg in "$@"; do
    case "${arg}" in
        --skip-sidecar) SKIP_SIDECAR=1 ;;
        --skip-agent) SKIP_AGENT=1 ;;
        -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
        *) echo "Unknown argument: ${arg}" >&2; exit 2 ;;
    esac
done

if ! command -v cargo >/dev/null 2>&1 && [[ -x "${HOME}/.cargo/bin/cargo" ]]; then
    export PATH="${HOME}/.cargo/bin:${PATH}"
fi

PY="$(command -v python3 || command -v python)"

if [[ "${SKIP_SIDECAR}" -eq 0 ]]; then
    echo "==> Building cc-port-desktop-api sidecar"
    "${PY}" "${REPO_ROOT}/tools/packaging/sidecar/build_sidecar.py"
fi

if [[ "${SKIP_AGENT}" -eq 0 ]]; then
    echo "==> Building public cc-port CLI/MCP agent"
    "${PY}" "${REPO_ROOT}/tools/packaging/agent/build_agent.py"
fi

echo
echo "==> Starting Tauri dev shell"
cd "${REPO_ROOT}/desktop"
npm run tauri dev
