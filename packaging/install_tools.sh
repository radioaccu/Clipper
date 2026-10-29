#!/bin/bash
# Installeert of werkt bij wat nodig is voor links: yt-dlp (met yt-dlp-ejs) in een eigen map, en Deno.
# Wordt aangeroepen door Installeer.command en Start_zonder_installeren.command. Mislukt dit, dan werken
# lokale bestanden gewoon; alleen links downloaden niet.
TOOLS="$HOME/Library/Application Support/Radio ACCU Clipper/tools"
PY="${PYTHON:-python3}"

if command -v brew >/dev/null 2>&1; then
  if ! command -v deno >/dev/null 2>&1 && [ ! -x /opt/homebrew/bin/deno ]; then
    echo "Deno installeren (nodig voor YouTube-links)…"
    brew install deno || echo "Waarschuwing: Deno kon niet worden geïnstalleerd."
  fi
else
  echo "Waarschuwing: Homebrew ontbreekt, Deno wordt niet geïnstalleerd. YouTube-links werken dan mogelijk niet."
fi

mkdir -p "$(dirname "$TOOLS")"
# een eerder gemaakte map kan stuk zijn na een Python-update: dan opnieuw aanmaken
if [ -x "$TOOLS/bin/python" ] && ! "$TOOLS/bin/python" -c "pass" >/dev/null 2>&1; then
  rm -rf "$TOOLS"
fi
if [ ! -x "$TOOLS/bin/python" ]; then
  "$PY" -m venv "$TOOLS" || { echo "Waarschuwing: map voor yt-dlp kon niet worden gemaakt."; exit 1; }
fi
echo "yt-dlp installeren of bijwerken…"
"$TOOLS/bin/python" -m pip install --upgrade --disable-pip-version-check --quiet "yt-dlp[default]" \
  || { echo "Waarschuwing: yt-dlp kon niet worden geïnstalleerd."; exit 1; }
echo "yt-dlp $("$TOOLS/bin/yt-dlp" --version) staat klaar."
