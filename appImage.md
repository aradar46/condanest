### AppImage: how to build it (the “right way” for CondaNest)

Here’s what you effectively have now, written up cleanly so you can put it into `plan.md` or a `packaging` section in the README.

---

### 1. One‑time setup: linuxdeploy

```bash
mkdir -p ~/bin
cd ~/bin

wget https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage
chmod +x linuxdeploy-x86_64.AppImage
```

(You don’t need the python/conda plugins anymore.)

---

### 2. Build script to create the AppImage

From the project root `~/Syncthing/Projects/cobra`, create `packaging/build-appimage.sh`:

```bash
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

# 2) Launcher that activates the AppDir venv and runs CondaNest
mkdir -p "$APPDIR/usr/bin"
cat > "$APPDIR/usr/bin/condanest" << 'EOF'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "$0")")"
VENV="$HERE/../../venv"
source "$VENV/bin/activate"
exec python -m condanest.app "$@"
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
```

Make it executable:

```bash
chmod +x packaging/build-appimage.sh
```

---

### 3. Building the AppImage

From the project root:

```bash
./packaging/build-appimage.sh
```

This produces:

```text
CondaNest-x86_64.AppImage
```

in the project root.

---

### 4. How users run it

Document this in the README’s “Binary download” section:

```bash
chmod +x CondaNest-x86_64.AppImage
./CondaNest-x86_64.AppImage
```

Requirements on the user’s system:

- Linux x86_64
- GTK4 + Libadwaita + PyGObject (same system packages you already list in the README)

This flow is reproducible, doesn’t rely on the linuxdeploy conda/python plugins or Anaconda ToS, and bundles your own CondaNest install in an internal venv.