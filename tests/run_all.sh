#!/bin/bash
# Draait alle tests. Vereist: ffmpeg, python3, numpy, Pillow (alleen voor tests).
cd "$(dirname "$0")/.."
export ACCU_TEST_MEDIA="${ACCU_TEST_MEDIA:-/tmp/t}"
[ -f "$ACCU_TEST_MEDIA/A.mp4" ] || bash tests/make_media.sh "$ACCU_TEST_MEDIA"
python3 -W ignore -m unittest tests.test_analysis tests.test_export tests.test_links tests.test_platform -v 2>&1 | tail -70
python3 -W ignore tests/smoke_gui.py
