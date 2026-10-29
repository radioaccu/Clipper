"""Rooktest met ECHTE Qt (PySide6), zonder scherm (offscreen). Draait op de Windows- en Mac-bouwmachines.

    python tests/real_qt_smoke.py <map met testmedia>

Controleert o.a.: venster opbouwen, elk gekoppeld instelveld, bestanden inlezen en analyseren, clips bewerken,
DJ-naam renderen, exporteren (1080×1350), links via de nagebootste yt-dlp, project opslaan/laden, afsluiten."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
MEDIA = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ACCU_TEST_MEDIA", "/tmp/t")).resolve()
os.environ["ACCU_YTDLP"] = str(HERE / "fake_ytdlp.py")
os.environ["FAKE_YTDLP_MEDIA"] = str(MEDIA)

from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

MESSAGES: list[str] = []
# modale vensters zouden zonder scherm eeuwig wachten: antwoord automatisch en onthoud de tekst
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
for _name in ("information", "warning", "critical"):
    setattr(QMessageBox, _name, staticmethod(lambda parent, title, text, *a, **k: MESSAGES.append(f"{title}: {text}")))
QMessageBox.exec = lambda self: 0
QDialog.exec = lambda self: 0

app = QApplication(sys.argv)

from accu_clipper.gui import mainwindow as mw          # noqa: E402  (na QApplication)
from accu_clipper.model import Clip, ExportSettings      # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  ({detail})" if detail and not cond else ""), flush=True)


def wait_until(cond, timeout=240, what="") -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(0.02)
    print(f"  … time-out bij wachten op: {what}", flush=True)
    return False


def ffprobe_size(path: str) -> tuple[int, int]:
    from accu_clipper.ffmpeg_tools import find_tool
    out = subprocess.run([find_tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_streams",
                          "-print_format", "json", path], capture_output=True, text=True).stdout
    s = json.loads(out)["streams"][0]
    return s["width"], s["height"]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="accu_realqt_"))
    mw.SUPPORT_DIR = tmp / "support"
    w = mw.MainWindow()
    w.show()
    w.project.out_dir = str(tmp / "uit")
    check("venster opgebouwd, FFmpeg gevonden", bool(w.ffmpeg), w.tool_error)

    # 1. inlezen + waveform-analyse
    w.add_files([str(MEDIA / "S_synthset.mp4"), str(MEDIA / "A.mp4")])
    ok = wait_until(lambda: len(w.project.sources) == 2 and all(s.info for s in w.project.sources)
                    and w.bg is None and not w.bg_queue, what="inlezen en analyse")
    check("twee bestanden ingelezen", ok)
    src = w.project.sources[0]
    w.src_list.setCurrentRow(0)
    wait_until(lambda: w.cur_src is src, 10)
    check("waveform-analyse klaar", src.path in w.feats)

    # 2. suggesties
    src.settings.clip_count = 3
    w.analyze()
    check("suggesties gemaakt", 1 <= len(src.clips) <= 3, str(len(src.clips)))

    # 3. elk gekoppeld veld schrijft echt naar de instellingen (dit vangt fouten zoals 'valueChanged bestaat niet')
    bad = []
    for widget, getter, attr, kind, scale in list(w._binds):
        obj = getter()
        if obj is None:
            continue
        try:
            if kind == "spin":
                target = widget.maximum()
                widget.setValue(target)
                expect = target / scale
                if isinstance(getattr(obj, attr), int) and not isinstance(getattr(obj, attr), bool):
                    expect = int(round(expect))
                okv = abs(float(getattr(obj, attr)) - float(expect)) < 1e-6
            elif kind == "check":
                widget.setChecked(not widget.isChecked())
                okv = getattr(obj, attr) == widget.isChecked()
            elif kind == "combo":
                if widget.count() < 2:
                    continue
                widget.setCurrentIndex(widget.count() - 1)
                okv = getattr(obj, attr) == widget.currentData()
            else:
                widget.setText("Test é")
                okv = getattr(obj, attr) == "Test é"
            if not okv:
                bad.append(f"{attr}={getattr(obj, attr)!r}")
        except Exception as e:
            bad.append(f"{attr}: {type(e).__name__}: {e}")
        app.processEvents()
    check(f"alle {len(w._binds)} gekoppelde velden werken", not bad, "; ".join(bad[:6]))

    # terug naar snelle, normale instellingen voor de export
    src.settings = ExportSettings(crf=24, preset="ultrafast", profile="custom")
    for c in src.clips:
        c.look = type(c.look)()
    w._reload_current()
    app.processEvents()

    # 4. profielkeuze via de echte keuzelijst
    w.cb_profile.setCurrentIndex(w.cb_profile.findData("compact"))
    check("profiel 'Compact' zet CRF 18", src.settings.crf == 18 and src.settings.preset == "medium")
    src.settings.crf, src.settings.preset = 24, "ultrafast"

    # 5. tijdvelden
    w.preview.player.setPosition(20_000)
    w.add_manual_clip()
    man = w.cur_clip
    w.ed_start.setText("0:40")
    w.ed_start.editingFinished.emit()
    w.ed_dur.setText("3")
    w.ed_dur.editingFinished.emit()
    check("start/duur via invoervelden", man is not None and abs(man.start - 40) < 0.01 and abs(man.duration - 3) < 0.01,
          f"{man.start if man else None}")

    # 6. DJ-naam echt renderen met Qt
    src.settings.dj_name = "DJ Ünïcode"
    png = w._text_png(src.settings)
    check("DJ-naam als afbeelding gerenderd", bool(png) and os.path.isfile(png))
    w._refresh_preview()

    # 7. export
    for c in src.clips:
        c.approved = c is man
    w.start_export([src])
    ok = wait_until(lambda: w.export_worker is None, 300, "export")
    mp4 = [p for p in Path(w.project.out_dir).glob("*.mp4")]
    check("export klaar", ok and len(mp4) == 1, str([p.name for p in mp4]))
    if mp4:
        check("export is 1080×1350", ffprobe_size(str(mp4[0])) == (1080, 1350))

    # 8. link via nagebootste yt-dlp, met tracklist
    n = len(w.project.sources)
    w.add_links(dict(urls=["https://www.youtube.com/watch?v=abc"], quality="best", cookies="", section=None,
                     tracks=True, dir=str(tmp / "downloads")))
    ok = wait_until(lambda: len(w.project.sources) == n + 1 and w.project.sources[-1].info is not None
                    and w.bg is None and not w.bg_queue, 300, "link")
    yt = w.project.sources[-1]
    check("link gedownload en ingelezen", ok and bool(yt.path))
    check("clips uit tracklist", [c.label for c in yt.clips] == ["Intro", "Artiest – Drop één", "Artiest – Drop twee"],
          str([c.label for c in yt.clips]))

    # 9. project opslaan en laden
    pf = tmp / "test.accuclip"
    w.project_path = str(pf)
    w.save_project(False)
    w.open_paths([str(pf)])
    wait_until(lambda: w.bg is None and not w.bg_queue, 120, "project laden")
    check("project opslaan en openen", len(w.project.sources) == n + 1 and pf.is_file())

    # 10. afspelen (kan op een bouwmachine zonder geluidskaart falen; alleen melden)
    w.src_list.setCurrentRow(0)
    app.processEvents()
    w.toggle_play()
    wait_until(lambda: w.preview.player.position() > 300, 8, "afspelen")
    print(f"  · afspeelpositie na enkele seconden: {w.preview.player.position()} ms (informatief)")
    w.toggle_play()

    w.close()
    app.processEvents()
    check("netjes afgesloten, sessie bewaard", (mw.SUPPORT_DIR / "last_session.accuclip").is_file())
    if MESSAGES:
        print("  meldingen tijdens de test:\n    " + "\n    ".join(m.replace("\n", " ")[:200] for m in MESSAGES))
    failed = [r for r in RESULTS if not r[1]]
    print(f"\nEchte-Qt-rooktest: {len(RESULTS) - len(failed)}/{len(RESULTS)} geslaagd")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
