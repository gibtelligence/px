#!/usr/bin/env bash
# Compila PX y arma el .app a mano (sin Xcode: solo Command Line Tools + SPM).
#
# El scratch de SPM va a disco LOCAL a proposito: las fuentes viven en el NAS
# (NFS, sin file-locks) y SPM necesita locks para su cache de build.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRATCH="${PX_BUILD_DIR:-$HOME/.cache/px-build}"
CONF="${1:-release}"
APP="${PX_APP_DEST:-$HOME/Applications}/PX.app"

echo "  compilando ($CONF)…"
cd "$HERE"
swift build -c "$CONF" --scratch-path "$SCRATCH" 2>&1 | grep -Ev '^\[[0-9]+/[0-9]+\] (Compiling|Emitting)' || true
BIN="$(swift build -c "$CONF" --scratch-path "$SCRATCH" --show-bin-path)/PX"
[ -x "$BIN" ] || { echo "  no se genero el binario"; exit 1; }

echo "  armando $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/PX"
cp "$HERE/Resources/Info.plist" "$APP/Contents/Info.plist"
[ -f "$HERE/Resources/AppIcon.icns" ] && cp "$HERE/Resources/AppIcon.icns" "$APP/Contents/Resources/"
# firma ad-hoc: sin ella macOS mata la app al abrir ventanas
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "  (aviso: no se pudo firmar ad-hoc)"
echo "  listo: $APP"
