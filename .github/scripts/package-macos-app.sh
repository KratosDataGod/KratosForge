#!/usr/bin/env bash
# Ad-hoc sign the .app and create fabric-desktop-macos.dmg (unsigned; Gatekeeper-friendly locally).
set -euo pipefail

TAURI_DIR="${1:-app/src-tauri}"
APP="$(find "${TAURI_DIR}/target/release/bundle/macos" -maxdepth 1 -name '*.app' -print -quit)"
if [[ -z "${APP}" ]]; then
  echo "No .app under ${TAURI_DIR}/target/release/bundle/macos" >&2
  exit 1
fi

echo "Signing ${APP} (ad hoc)…"
codesign --sign - --force --deep --options runtime "${APP}"

DMG="${TAURI_DIR}/fabric-desktop-macos.dmg"
rm -f "${DMG}"
hdiutil create -volname "Fabric Desktop" -srcfolder "${APP}" -ov -format UDZO "${DMG}"
echo "Wrote ${DMG}"
