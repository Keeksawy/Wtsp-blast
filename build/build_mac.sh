#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
#  Build WA Outreach.app + WA Outreach.dmg for macOS
#  Run from the project root:   bash build/build_mac.sh
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
APP_NAME="WA Outreach"
DIST="$ROOT/dist"
DMG="$DIST/${APP_NAME}.dmg"

echo "=== WA Outreach — macOS build ==="
echo "Project: $ROOT"
echo ""

# ── 1. Virtual env & deps ─────────────────────────────────────────────────
if [ ! -f "$VENV/bin/python3" ]; then
  echo "[1/5] Creating venv…"
  python3 -m venv "$VENV"
fi

echo "[1/5] Installing / updating dependencies…"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r requirements.txt
"$VENV/bin/pip" install -q "pywebview>=4.3" pyinstaller

# ── 2. Icons ──────────────────────────────────────────────────────────────
echo "[2/5] Generating icons…"
"$VENV/bin/python3" assets/make_icon.py

# ── 3. PyInstaller build ──────────────────────────────────────────────────
echo "[3/5] Running PyInstaller…"
"$VENV/bin/pyinstaller" build/WA_Outreach_mac.spec --clean --noconfirm

echo ""
echo "  Built: $DIST/${APP_NAME}.app"

# ── 4. DMG (optional — requires: brew install create-dmg) ─────────────────
echo "[4/5] Creating DMG…"
if command -v create-dmg &>/dev/null; then
  rm -f "$DMG"
  create-dmg \
    --volname "$APP_NAME" \
    --volicon "$ROOT/assets/icon.icns" \
    --window-pos 200 150 \
    --window-size 620 340 \
    --icon-size 120 \
    --icon "${APP_NAME}.app" 160 150 \
    --hide-extension "${APP_NAME}.app" \
    --app-drop-link 460 150 \
    --no-internet-enable \
    "$DMG" \
    "$DIST/${APP_NAME}.app"
  echo "  DMG:   $DMG"
else
  echo "  create-dmg not found — skipping DMG."
  echo "  Install with:  brew install create-dmg"
  echo "  Then re-run this script, or drag $DIST/${APP_NAME}.app to a folder."
fi

# ── 5. Summary ────────────────────────────────────────────────────────────
echo ""
echo "[5/5] Done."
echo ""
echo "  App:  $DIST/${APP_NAME}.app"
[ -f "$DMG" ] && echo "  DMG:  $DMG"
echo ""
echo "First-time install note:"
echo "  Distribute the .dmg (or zip the .app)."
echo "  On first launch the app downloads Playwright's Chromium (~150 MB)."
echo "  User data is stored in a 'WA Outreach Data' folder beside the .app."
