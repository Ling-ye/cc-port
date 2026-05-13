#!/usr/bin/env bash
# Build the LPM Desktop app for distribution (executable + installers).
#
# Steps:
#   1. Generate placeholder icons if missing.
#   2. Build the lpm-desktop-api sidecar via PyInstaller.
#   3. Run `npm run tauri build` to produce platform installers.
#
# Run scripts/setup.sh once before invoking this.

set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

SKIP_SIDECAR=0
SKIP_ICONS=0
for arg in "$@"; do
    case "${arg}" in
        --skip-sidecar) SKIP_SIDECAR=1 ;;
        --skip-icons)   SKIP_ICONS=1 ;;
        -h|--help)      sed -n '2,11p' "$0"; exit 0 ;;
        *) echo "Unknown argument: ${arg}" >&2; exit 2 ;;
    esac
done

section() { printf "\n==> %s\n" "$1"; }

if ! command -v cargo >/dev/null 2>&1; then
    if [[ -x "${HOME}/.cargo/bin/cargo" ]]; then
        export PATH="${HOME}/.cargo/bin:${PATH}"
    else
        echo "cargo not found. Install Rust from https://rustup.rs/ first." >&2
        exit 1
    fi
fi

PY="$(command -v python3 || command -v python)"

if [[ "${SKIP_ICONS}" -eq 0 ]]; then
    section "Ensuring desktop icons exist"
    if [[ ! -f "${REPO_ROOT}/desktop/src-tauri/icons/icon.ico" ]]; then
        "${PY}" "${REPO_ROOT}/tools/packaging/icons/generate_icons.py"
    else
        echo "  icons already present"
    fi
fi

if [[ "${SKIP_SIDECAR}" -eq 0 ]]; then
    section "Building lpm-desktop-api sidecar"
    "${PY}" "${REPO_ROOT}/tools/packaging/sidecar/build_sidecar.py"
fi

section "Building Tauri app (release)"
cd "${REPO_ROOT}/desktop"
npm run tauri build

section "Build complete"
RELEASE_DIR="${REPO_ROOT}/desktop/src-tauri/target/release"
TARGET_TRIPLE="$(rustc -vV 2>/dev/null | awk '/^host:/ {print $2; exit}')"
TARGET_TRIPLE="${TARGET_TRIPLE:-unknown-target}"
ARTIFACT_DIR="${REPO_ROOT}/dist/desktop/${TARGET_TRIPLE}"
mkdir -p "${ARTIFACT_DIR}"
echo "  Executable : ${RELEASE_DIR}/lpm-desktop"
if [[ -f "${RELEASE_DIR}/lpm-desktop" ]]; then
    cp -f "${RELEASE_DIR}/lpm-desktop" "${ARTIFACT_DIR}/"
elif [[ -f "${RELEASE_DIR}/lpm-desktop.exe" ]]; then
    cp -f "${RELEASE_DIR}/lpm-desktop.exe" "${ARTIFACT_DIR}/"
fi
if [[ -f "${RELEASE_DIR}/lpm-desktop-api" ]]; then
    cp -f "${RELEASE_DIR}/lpm-desktop-api" "${ARTIFACT_DIR}/"
elif [[ -f "${RELEASE_DIR}/lpm-desktop-api.exe" ]]; then
    cp -f "${RELEASE_DIR}/lpm-desktop-api.exe" "${ARTIFACT_DIR}/"
fi
if [[ -d "${RELEASE_DIR}/bundle" ]]; then
    cp -R "${RELEASE_DIR}/bundle/." "${ARTIFACT_DIR}/"
    find "${RELEASE_DIR}/bundle" -type f -print | sed 's/^/  Bundle     : /'
fi
echo "  Collected  : ${ARTIFACT_DIR}"
