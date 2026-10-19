#!/usr/bin/env bash
# Build a runnable Fabric Desktop .app on macOS (engine venv + Tauri bundle).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SIDECAR="$ROOT/sidecar"
TAURI="$ROOT/app/src-tauri"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script is for macOS only." >&2
  exit 1
fi

if ! command -v python3 >/dev/null; then
  echo "Install Python 3.11 or later (for example: brew install python@3.12)." >&2
  exit 1
fi

if ! command -v cargo >/dev/null; then
  echo "Installing Rust (rustup)…"
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
  # shellcheck disable=SC1091
  source "$HOME/.cargo/env"
fi

if ! command -v cargo >/dev/null; then
  echo "cargo not found after rustup; add \$HOME/.cargo/bin to PATH." >&2
  exit 1
fi

if ! cargo tauri --version >/dev/null 2>&1; then
  echo "Installing tauri-cli…"
  cargo install tauri-cli --locked
fi

COMMIT="$(git -C "$ROOT" rev-parse HEAD)"
DATA_ROOT="${HOME}/Library/Application Support/FabricDesktop"
ENGINE_DIR="${DATA_ROOT}/engine/${COMMIT}"

echo "Building local engine at ${ENGINE_DIR}…"
rm -rf "${ENGINE_DIR}"
python3 -m venv "${ENGINE_DIR}"
"${ENGINE_DIR}/bin/pip" install --disable-pip-version-check --upgrade pip
"${ENGINE_DIR}/bin/pip" install --disable-pip-version-check \
  -e "${SIDECAR}" -c "${SIDECAR}/constraints.txt"

PY_VER="$("${ENGINE_DIR}/bin/python3" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
cat > "${ENGINE_DIR}/engine.json" <<EOF
{"commit":"${COMMIT}","python":"${PY_VER}","client_id":"","telemetry_connection_string":""}
EOF

echo "Verifying engine starts…"
"${ENGINE_DIR}/bin/python3" -c "import fabric_desktop.server"

echo "Building Fabric Desktop.app…"
export FABRIC_RELEASE_COMMIT="${COMMIT}"
export FABRIC_CLIENT_ID=""
cd "${TAURI}"
cargo tauri build

bash "${ROOT}/.github/scripts/package-macos-app.sh" "${TAURI}"

APP="$(find "${TAURI}/target/release/bundle/macos" -maxdepth 1 -name '*.app' -print -quit)"
DMG="${TAURI}/fabric-desktop-macos.dmg"
if [[ -n "${APP}" ]]; then
  echo ""
  echo "Done. Open the app:"
  echo "  open \"${APP}\""
  if [[ -f "${DMG}" ]]; then
    echo "Or mount the disk image:"
    echo "  open \"${DMG}\""
  fi
  echo ""
  echo "If macOS says the app is damaged or can't be opened, run:"
  echo "  xattr -cr \"${APP}\""
  echo "Then open it again, or allow it under Privacy & Security."
else
  BIN="${TAURI}/target/release/fabric-desktop"
  if [[ -x "${BIN}" ]]; then
    echo ""
    echo "Done. Run the binary:"
    echo "  \"${BIN}\""
  else
    echo "Build finished; look under ${TAURI}/target/release/" >&2
  fi
fi
