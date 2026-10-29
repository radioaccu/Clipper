#!/bin/bash
# Bouwt "Radio ACCU Clipper.app" (Apple Silicon). Draai dit op de Mac; wordt aangeroepen door Installeer.command.
set -euo pipefail
cd "$(dirname "$0")/.."
APP="Radio ACCU Clipper"
PY="${PYTHON:-python3}"

if [ ! -d .venv ]; then "$PY" -m venv .venv; fi
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt pyinstaller

# Icoon (.icns) maken met macOS-hulpprogramma's
rm -rf packaging/AppIcon.iconset && mkdir packaging/AppIcon.iconset
for s in 16 32 128 256 512; do
  sips -z $s $s packaging/icon.png --out packaging/AppIcon.iconset/icon_${s}x${s}.png >/dev/null
  d=$((s*2)); sips -z $d $d packaging/icon.png --out packaging/AppIcon.iconset/icon_${s}x${s}@2x.png >/dev/null
done
iconutil -c icns packaging/AppIcon.iconset -o packaging/AppIcon.icns

EXTRA=()
if [ -x vendor/ffmpeg ] && [ -x vendor/ffprobe ]; then
  echo "→ FFmpeg uit ./vendor wordt in de app ingebouwd"
  EXTRA+=(--add-binary "vendor/ffmpeg:bin" --add-binary "vendor/ffprobe:bin")
fi

rm -rf build dist
pyinstaller --noconfirm --windowed --name "$APP" --icon packaging/AppIcon.icns \
  --osx-bundle-identifier nl.radioaccu.clipper \
  --add-data "packaging/icon.png:." \
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets \
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml \
  --exclude-module tkinter \
  ${EXTRA[@]+"${EXTRA[@]}"} run.py

# Lokale (ad-hoc) handtekening: geen Apple Developer-account nodig, wel nodig op Apple Silicon.
codesign --force --deep --sign - "dist/$APP.app" || echo "Waarschuwing: ondertekenen mislukt"
xattr -cr "dist/$APP.app" || true
echo "Klaar: dist/$APP.app"
