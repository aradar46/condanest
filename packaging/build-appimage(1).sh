#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPDIR="$ROOT/AppDir"

echo "[CondaNest] Preparing AppDir at $APPDIR"
rm -rf "$APPDIR"

# 1) Create an isolated venv inside the AppImage
python3 -m venv --system-site-packages "$APPDIR/venv"
source "$APPDIR/venv/bin/activate"
pip install --upgrade pip setuptools wheel
pip install "$ROOT"

# 2) Launcher that uses the bundled venv Python directly
mkdir -p "$APPDIR/usr/bin"
cat > "$APPDIR/usr/bin/condanest" << 'EOF'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "$0")")"
VENV="$HERE/../../venv"
exec "$VENV/bin/python" -m condanest.app "$@"
EOF
chmod +x "$APPDIR/usr/bin/condanest"

# 3) Desktop file + icon
mkdir -p "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps"

cat > "$APPDIR/usr/share/applications/condanest.desktop" << 'EOF'
[Desktop Entry]
Type=Application
Name=CondaNest
Exec=condanest
Icon=condanest
Categories=Development;
EOF

cp "$ROOT/condanest.png" \
   "$APPDIR/usr/share/icons/hicolor/256x256/apps/condanest.png"

# 4) Run linuxdeploy to turn AppDir into an AppImage
echo "[CondaNest] Running linuxdeploy..."
ARCH=x86_64 ~/bin/linuxdeploy-x86_64.AppImage \
  --appdir "$APPDIR" \
  --output appimage

echo "[CondaNest] Done. AppImage: $ROOT/CondaNest-x86_64.AppImage"
