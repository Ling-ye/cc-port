#!/usr/bin/env bash
# One-shot environment setup for LPM (Python + desktop tooling).
#
# Usage:
#   bash scripts/setup.sh
#   bash scripts/setup.sh --skip-desktop    # CLI/MCP only

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

SKIP_DESKTOP=0
for arg in "$@"; do
    case "${arg}" in
        --skip-desktop) SKIP_DESKTOP=1 ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *) echo "Unknown argument: ${arg}" >&2; exit 2 ;;
    esac
done

section() { printf "\n==> %s\n" "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

section "Checking prerequisites"
if ! have python3 && ! have python; then
    echo "python not found on PATH. Install Python 3.10+ first." >&2
    exit 1
fi
PY="$(command -v python3 || command -v python)"
echo "  python : $("${PY}" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"

if [[ "${SKIP_DESKTOP}" -eq 0 ]]; then
    if ! have node; then echo "node not found. Install Node.js 18+ (or rerun with --skip-desktop)." >&2; exit 1; fi
    echo "  node   : $(node --version)"
    if ! have npm; then echo "npm not found." >&2; exit 1; fi
    echo "  npm    : $(npm --version)"
    if ! have cargo; then
        if [[ -x "${HOME}/.cargo/bin/cargo" ]]; then
            export PATH="${HOME}/.cargo/bin:${PATH}"
            echo "  (added ~/.cargo/bin to PATH for this session)"
        else
            echo "cargo not found. Install Rust from https://rustup.rs/ first." >&2
            exit 1
        fi
    fi
    echo "  cargo  : $(cargo --version)"
    echo "  rustc  : $(rustc --version)"
fi

section "Installing LPM Python package"
if [[ "${SKIP_DESKTOP}" -eq 0 ]]; then
    "${PY}" -m pip install -e ".[dev,desktop]"
else
    "${PY}" -m pip install -e ".[dev]"
fi

if [[ "${SKIP_DESKTOP}" -eq 0 ]]; then
    section "Ensuring desktop icons exist"
    if [[ -f "desktop/src-tauri/icons/icon.ico" ]]; then
        echo "  icons already present at desktop/src-tauri/icons/"
    else
        "${PY}" "packaging/icons/generate_icons.py"
    fi

    section "Installing desktop npm dependencies"
    (cd desktop && npm install)
fi

section "Setup complete"
if [[ "${SKIP_DESKTOP}" -eq 0 ]]; then
    echo "Next steps:"
    echo "  bash scripts/dev.sh            # run desktop in dev mode"
    echo "  bash scripts/build-desktop.sh  # produce installer + exe"
else
    echo "Next steps:"
    echo "  lpm doctor         # verify CLI"
    echo "  lpm platforms      # list configured platforms"
fi
