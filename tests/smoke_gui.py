"""Rooktest van MainWindow-logica met nep-Qt + echt FFmpeg. Bewijst NIET dat de Qt-interface op macOS werkt."""
import os, sys, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_stub; qt_stub.install()
os.environ["ACCU_YTDLP"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_ytdlp.py")
os.environ.setdefault("FAKE_YTDLP_MEDIA", os.environ.get("ACCU_TEST_MEDIA", "/tmp/t"))
from accu_clipper.gui import mainwindow as mw
from accu_clipper.gui.mainwindow import MainWindow
from accu_clipper.model import QUALITY_PROFILES, Clip
import subprocess, json

MEDIA = os.environ.get("ACCU_TEST_MEDIA", "/tmp/t")
w = MainWindow()
assert w.ffmpeg, w.tool_error
w.project.out_dir = tempfile.mkdtemp(prefix="accu_smoke_")
mw.SUPPORT_DIR = type(mw.SUPPORT_DIR)(tempfile.mkdtemp())

# 1. toevoegen → probe + waveform-extractie (inline workers)
w.add_files([os.path.join(MEDIA, "S_synthset.mp4"), os.path.join(MEDIA, "Mijn Set – Café #1 (zondag) [live].mp4"),
             "/tmp/niet_bestaand.mp4"])
assert len(w.project.sources) == 2, [s.name for s in w.project.sources]
src = w.project.sources[0]
assert src.info and src.info.duration > 300 and src.path in w.feats, "probe/extract"
w.on_select_source(0)
assert w.cur_src is src

# 2. analyse
src.settings.clip_count = 4
w.analyze()
assert 1 <= len(src.clips) <= 4 and w.cur_clip is not None, src.clips
n_auto = len(src.clips)
print("suggesties:", [(round(c.start), c.weak) for c in src.clips])

# 3. handmatig, duplicate, velden, set_point, delete
w.preview.player.setPosition(10_000)
w.add_manual_clip(); man = w.cur_clip
assert man.origin == "manual" and abs(man.start - 10) < 0.01 and abs(man.duration - 30) < 0.01
w.ed_start.setText("0:20"); w.apply_field("start")
assert abs(man.start - 20) < 0.01 and man.end > man.start
w.ed_dur.setText("15,5"); w.apply_field("dur"); assert abs(man.duration - 15.5) < 0.01
w.ed_end.setText("00:45"); w.apply_field("end"); assert abs(man.end - 45) < 0.01
w.ed_start.setText("kapot"); w.apply_field("start"); assert abs(man.start - 20) < 0.01   # ongeldige invoer wordt genegeerd
w.duplicate_clip(); assert len(src.clips) == n_auto + 2
w.delete_clip(); assert len(src.clips) == n_auto + 1
w.select_clip(man)
w.preview.player.setPosition(25_000); w.set_point("start"); assert abs(man.start - 25) < 0.01

# 4. instellingen: profiel, binding, enablement
w.cb_profile.setCurrentIndex(w.cb_profile.findData("compact")); w.on_profile()
assert src.settings.crf == 18 and src.settings.preset == "medium"
w.sp_crf.setValue(22); w.sp_crf.valueChanged.emit(22)       # via bind()
assert src.settings.crf == 22 and src.settings.profile == "custom"
src.settings.preset = "ultrafast"; src.settings.crf = 24
src.settings.dj_name = ""   # tekstrenderer bestaat alleen in echte Qt
man.look.crop_mode = "fit"; w.refresh_binds(); w._update_enablement()

# 5. export van goedgekeurde clips (echt FFmpeg)
for c in src.clips: c.approved = False
man.approved = True
sel = next(c for c in src.clips if c is not man); sel.approved = True; sel.end = sel.start + 3; man.end = man.start + 3
w.start_export([src])
files = sorted(f for f in os.listdir(w.project.out_dir))
mp4 = [f for f in files if f.endswith(".mp4")]
assert len(mp4) == 2 and any(f.endswith(".txt") for f in files) and not any(f.endswith(".part") for f in files), files
for f in mp4:
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-print_format", "json",
                                   os.path.join(w.project.out_dir, f)], capture_output=True, text=True).stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1350), f
print("export-bestanden:", mp4)

# 6. project opslaan en weer laden
pf = os.path.join(tempfile.mkdtemp(), "t.accuclip")
w.project_path = pf; w.save_project(False); assert os.path.isfile(pf)
w.load_project(pf)
assert len(w.project.sources) == 2 and w.project.sources[0].settings.crf == 24
assert any(c.origin == "manual" for c in w.project.sources[0].clips)

# 7. exact frame + sluiten
w.on_select_source(0)
w.select_clip(w.cur_src.clips[0]); w.show_exact()
assert os.path.isfile(os.path.join(w._tmp, "exact.png"))
w.remove_source(); assert len(w.project.sources) == 1

# 8. links (nagebootste yt-dlp): tracklist uit beschrijving, audio-only, afspeellijst, fout
from accu_clipper.gui.mainwindow import QMessageBox as MB
dl_dir = tempfile.mkdtemp(prefix="accu_smoke_dl_")
n0 = len(w.project.sources)
w.add_links(dict(urls=["https://www.youtube.com/watch?v=abc"], quality="best", cookies="", section=None,
                 tracks=True, dir=dl_dir))
yt = w.project.sources[-1]
assert len(w.project.sources) == n0 + 1 and yt.path and yt.info, "download → inlezen mislukt"
assert yt.title == "Live Set – Café 2026 / Ü" and yt.url.endswith("abc")
assert [c.label for c in yt.clips] == ["Intro", "Artiest – Drop één", "Artiest – Drop twee"], [c.label for c in yt.clips]
assert [round(c.start) for c in yt.clips] == [0, 90, 180] and all(c.origin == "tracklist" for c in yt.clips)
assert [c["title"] for c in yt.info.chapters] == ["Intro", "Hoofddeel"]
w.add_links(dict(urls=["https://soundcloud.com/radio/set"], quality="audio", cookies="", section=None,
                 tracks=False, dir=dl_dir))
sc = w.project.sources[-1]
assert sc.info.audio_only and sc.path.endswith(".m4a")
n1 = len(w.project.sources)
w.add_links(dict(urls=["https://www.youtube.com/playlist?list=x"], quality="best", cookies="", section=None,
                 tracks=False, dir=dl_dir))
assert len(w.project.sources) == n1, "afspeellijst met al aanwezige video's mag geen dubbele sets maken"
MB.log.clear()
w.add_links(dict(urls=["https://youtube.com/watch?v=bot"], quality="best", cookies="", section=None,
                 tracks=False, dir=dl_dir))
assert len(w.project.sources) == n1 and all(x.path for x in w.project.sources), "mislukte link blijft hangen"
assert any("Cookies uit browser" in m for _, m in MB.log), MB.log
# audio-only set exporteren
w.on_select_source(w.project.sources.index(sc))
sc.settings.preset, sc.settings.crf, sc.settings.dj_name = "ultrafast", 24, ""
sc.settings.caption_enabled = sc.settings.export_cover = True
sc.clips = [Clip(start=30, end=33, label="Test")]
before = set(os.listdir(w.project.out_dir))
w.start_export([sc])
new = sorted(set(os.listdir(w.project.out_dir)) - before)
assert any(f.endswith(".mp4") for f in new) and any(f.endswith("_cover.jpg") for f in new) \
    and any(f.endswith("_bijschrift.txt") for f in new), new
print("links + audio-only via interfacelogica:", [f for f in new if not f.startswith("Radio_ACCU")])
ev = type("E", (), {"accept": lambda s: None, "ignore": lambda s: None})()
w.closeEvent(ev)
print("GUI-rooktest (nep-Qt): OK")
