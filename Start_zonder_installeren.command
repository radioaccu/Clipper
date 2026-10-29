#!/bin/bash
# Alternatief: start de app rechtstreeks uit de broncode (zonder .app te bouwen).
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  PY=python3; for c in /opt/homebrew/bin/python3 /usr/local/bin/python3; do [ -x "$c" ] && PY="$c" && break; done
  "$PY" -m venv .venv && source .venv/bin/activate && python -m pip install -r requirements.txt
else source .venv/bin/activate; fi
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ ! -x "$HOME/Library/Application Support/Radio ACCU Clipper/tools/bin/yt-dlp" ]; then
  HOMEBREW_NO_AUTO_UPDATE=1 bash packaging/install_tools.sh || echo "Let op: links downloaden werkt nog niet."
fi
python run.py
