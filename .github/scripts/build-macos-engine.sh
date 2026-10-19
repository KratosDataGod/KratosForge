#!/usr/bin/env bash
# Build fabric-desktop-engine-macos.zip for CI (venv layout: bin/python3 at zip root).
set -euo pipefail

ROOT="${GITHUB_WORKSPACE:-$(cd "$(dirname "$0")/../.." && pwd)}"
SIDECAR="${ROOT}/sidecar"
ENGINE_DIR="${ENGINE_DIR:-${RUNNER_TEMP:-/tmp}/fabric-desktop-engine}"
OUT_DIR="${OUT_DIR:-${GITHUB_WORKSPACE:-$ROOT}}"
COMMIT="${GITHUB_SHA:-$(git -C "$ROOT" rev-parse HEAD)}"
ZIP="${OUT_DIR}/fabric-desktop-engine-macos.zip"

rm -rf "${ENGINE_DIR}"
python3 -m venv "${ENGINE_DIR}"
"${ENGINE_DIR}/bin/pip" install --disable-pip-version-check --upgrade pip
"${ENGINE_DIR}/bin/pip" install --disable-pip-version-check \
  -e "${SIDECAR}" -c "${SIDECAR}/constraints.txt"

PY_VER="$("${ENGINE_DIR}/bin/python3" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
cat > "${ENGINE_DIR}/engine.json" <<EOF
{"commit":"${COMMIT}","python":"${PY_VER}","client_id":"${FABRIC_CLIENT_ID:-}","telemetry_connection_string":""}
EOF

"${ENGINE_DIR}/bin/python3" -c "import fabric_desktop.server"

rm -f "${ZIP}"
(cd "${ENGINE_DIR}" && zip -r -q "${ZIP}" .)
echo "Wrote ${ZIP}"
