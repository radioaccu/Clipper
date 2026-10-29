#!/bin/bash
# Dubbelklik dit bestand (eenmalig). Het bouwt de app en zet die in je map Programma's.
cd "$(dirname "$0")"
echo "=== Radio ACCU Clipper installeren ==="

# 1. Python
PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then PY="$c"; break; fi
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 of nieuwer ontbreekt. Installeer het gratis via https://www.python.org/downloads/macos/"
  echo "en dubbelklik daarna dit bestand opnieuw."; read -p "Druk op Enter om te sluiten…"; exit 1
fi
echo "Python: $PY"

# 2. FFmpeg (Homebrew-pad expliciet toevoegen: een dubbelgeklikt script kent het niet altijd)
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export HOMEBREW_NO_AUTO_UPDATE=1
HAVE_FFMPEG=""
for f in /opt/homebrew/bin/ffmpeg /usr/local/bin/ffmpeg vendor/ffmpeg; do
  if [ -x "$f" ]; then HAVE_FFMPEG="$f"; break; fi
done
if [ -n "$HAVE_FFMPEG" ]; then
  echo "FFmpeg: $HAVE_FFMPEG"
elif command -v brew >/dev/null 2>&1; then
  echo "FFmpeg installeren via Homebrew…"; brew install ffmpeg
else
  echo "FFmpeg ontbreekt. Installeer Homebrew (https://brew.sh), voer 'brew install ffmpeg' uit"
  echo "of zet een ffmpeg- en ffprobe-programma (arm64) in de map 'vendor' naast dit bestand."
  read -p "Druk op Enter om te sluiten…"; exit 1
fi

# 2b. Voor links: yt-dlp en Deno (niet verplicht; lokale bestanden werken ook zonder)
PYTHON="$PY" bash packaging/install_tools.sh || echo "Let op: links downloaden werkt pas als dit lukt (lokale bestanden werken wel)."

# 3. Bouwen
PYTHON="$PY" bash packaging/build_app.sh || { echo "Bouwen mislukt, zie de melding hierboven."; read -p "Enter…"; exit 1; }

# 4. Installeren
mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/Radio ACCU Clipper.app"
cp -R "dist/Radio ACCU Clipper.app" "$HOME/Applications/"
echo
echo "Klaar! Je vindt 'Radio ACCU Clipper' in $HOME/Applications. Sleep het naar het Dock."
echo "Eerste keer openen: houd Control ingedrukt, klik op de app en kies 'Open'."
open "$HOME/Applications"
read -p "Druk op Enter om te sluiten…"
