"""Tests voor links (via nagebootste yt-dlp), tracklists, audio-only-bronnen, omslagfoto en bijschrift."""
import glob
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
MEDIA = os.environ.get("ACCU_TEST_MEDIA", "/tmp/t")
os.environ["ACCU_YTDLP"] = os.path.join(HERE, "fake_ytdlp.py")
os.environ["FAKE_YTDLP_MEDIA"] = MEDIA

from PIL import Image
from accu_clipper import downloader as dl, exporter as ex
from accu_clipper.ffmpeg_tools import ToolError, find_tool, probe
from accu_clipper.model import Clip, ExportSettings, Look, Source
from accu_clipper.tracklist import parse_tracklist, tracks_from_chapters, tracks_to_ranges

FF = find_tool("ffmpeg")


def leftovers() -> set:
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "accu_dl_*")))


class TestLinkInfo(unittest.TestCase):
    def test_single_video(self):
        i = dl.fetch_info("https://www.youtube.com/watch?v=abc")
        self.assertEqual(i.title, "Live Set – Café 2026 / Ü")
        self.assertEqual(i.duration, 360)
        self.assertFalse(i.is_playlist)
        self.assertEqual([c["title"] for c in i.chapters], ["Intro", "Hoofddeel"])
        self.assertIn("Drop twee", i.description)

    def test_playlist(self):
        i = dl.fetch_info("https://www.youtube.com/playlist?list=xyz")
        self.assertTrue(i.is_playlist)
        self.assertEqual([e["url"] for e in i.entries], ["https://www.youtube.com/watch?v=aaaaaaaaaaa",
                                                         "https://www.youtube.com/watch?v=bbbbbbbbbbb"])

    def test_bot_check_gives_cookie_advice(self):
        with self.assertRaises(ToolError) as cm:
            dl.fetch_info("https://youtube.com/watch?v=bot")
        self.assertIn("Cookies uit browser", str(cm.exception))

    def test_unsupported(self):
        with self.assertRaises(ToolError) as cm:
            dl.fetch_info("https://example.com/unsupported")
        self.assertIn("niet ondersteund", str(cm.exception))

    def test_extract_urls(self):
        self.assertEqual(dl.extract_urls("a https://youtu.be/x, b (https://soundcloud.com/y/z). https://youtu.be/x"),
                         ["https://youtu.be/x", "https://soundcloud.com/y/z"])

    def test_command_is_safe(self):
        cmd = dl.build_download_command("-rf /", "/uit map", "/tmp/x", "/tmp/k.txt", "compat", "safari", (60, 120))
        self.assertEqual(cmd[-2:], ["--", "-rf /"])            # URL kan nooit als optie worden gelezen
        self.assertIn("--ignore-config", cmd)
        self.assertIn("--no-playlist", cmd)
        self.assertEqual(cmd[cmd.index("--cookies-from-browser") + 1], "safari")
        self.assertEqual(cmd[cmd.index("--download-sections") + 1], "*60.000-120.000")
        self.assertIn("vcodec:h264,res:1080,acodec:aac", cmd)


class TestDownload(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="accu_dlout_")
        self.before = leftovers()

    def tearDown(self):
        self.assertEqual(leftovers() - self.before, set(), "tijdelijke downloadmap niet opgeruimd")

    def test_video_download_with_progress(self):
        seen = []
        p = dl.download("https://youtu.be/abc", self.out, "best", progress=lambda f, t: seen.append((f, t)))
        self.assertTrue(os.path.isfile(p))
        self.assertTrue(p.endswith(".mkv"))
        self.assertIn("Café", os.path.basename(p))
        self.assertEqual(os.path.dirname(p), os.path.abspath(self.out))
        self.assertTrue(any("deel 2" in t for _, t in seen), "tweede deel (geluid) niet gemeld")
        self.assertTrue(any("2.50MiB/s" in t for _, t in seen))
        self.assertGreater(max(f for f, _ in seen), 0.9)
        info = probe(p)
        self.assertAlmostEqual(info.duration, 360, delta=1)
        self.assertFalse(info.audio_only)
        self.assertEqual(os.listdir(self.out), [os.path.basename(p)])   # geen .part of rommel

    def test_second_download_reuses_file(self):
        a = dl.download("https://youtu.be/abc", self.out, "best")
        b = dl.download("https://youtu.be/abc", self.out, "best")
        self.assertEqual(a, b)

    def test_audio_only_download(self):
        p = dl.download("https://soundcloud.com/radio/set", self.out, "audio")
        self.assertTrue(p.endswith(".m4a"))
        self.assertTrue(probe(p).audio_only)

    def test_section(self):
        p = dl.download("https://youtu.be/abc", self.out, "compat", section=(60, 90))
        self.assertIn("(fragment)", p)
        self.assertAlmostEqual(probe(p).duration, 30, delta=1.5)

    def test_cancel_cleans_up(self):
        ev = threading.Event()
        threading.Timer(0.8, ev.set).start()
        with self.assertRaises(dl.DownloadCancelled):
            dl.download("https://youtu.be/slow", self.out, "best", cancel=ev)
        self.assertEqual(os.listdir(self.out), [])

    def test_failure_midway(self):
        with self.assertRaises(ToolError) as cm:
            dl.download("https://youtu.be/fail", self.out, "best")
        self.assertIn("bijwerken", str(cm.exception))
        self.assertEqual(os.listdir(self.out), [])

    def test_disk_space(self):
        with self.assertRaises(ToolError):
            dl.download("https://youtu.be/abc", self.out, "best", expected_bytes=10 ** 16)


class TestTracklist(unittest.TestCase):
    TEXT = ("Tracklist:\n00:00 Intro\n1. [03:12] Artiest Eén – Titel (Extended Mix)\n2) 7:45 - Artiest Twee - Titel\n"
            "Artiest Drie - Derde (12:30)\n01:02:03 | DJ X - Laatste\ngeen tijd\n99:99 kapot\n03:12 dubbel\n")

    def test_parse(self):
        t = parse_tracklist(self.TEXT, total=4000)
        self.assertEqual([x.start for x in t], [0, 192, 465, 750, 3723])
        self.assertEqual(t[1].title, "Artiest Eén – Titel (Extended Mix)")
        self.assertEqual(t[3].title, "Artiest Drie - Derde")
        self.assertEqual(t[4].title, "DJ X - Laatste")
        self.assertEqual(t[0].end, 192)

    def test_beyond_end_dropped(self):
        self.assertEqual([x.start for x in parse_tracklist(self.TEXT, total=500)], [0, 192, 465])

    def test_ranges(self):
        t = parse_tracklist("00:00 A\n00:20 B\n05:00 C", total=400)
        r = tracks_to_ranges(t, 30, 0, 400, stop_at_next=True)
        self.assertEqual([(a, b) for a, b, _ in r], [(0, 20), (20, 50), (300, 330)])
        r2 = tracks_to_ranges(t, 30, 25, 400)       # offset voorbij track A (20 s) → overgeslagen
        self.assertEqual([x[2] for x in r2], ["B", "C"])

    def test_chapters(self):
        t = tracks_from_chapters([{"start": 90, "end": 360, "title": "Hoofd"}, {"start": 0, "end": 90, "title": ""}], 360)
        self.assertEqual([(x.start, x.title) for x in t], [(0, "Hoofdstuk"), (90, "Hoofd")])


class TestAudioOnlyAndExtras(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mp3 = os.path.join(tempfile.mkdtemp(), "Set zonder beeld – é.mp3")
        subprocess.run([FF, "-v", "error", "-y", "-i", os.path.join(MEDIA, "S_synthset.mp4"), "-vn",
                        "-c:a", "libmp3lame", "-b:a", "192k", cls.mp3], check=True)

    def src(self, **kw):
        st = ExportSettings(crf=23, preset="ultrafast", profile="custom")
        for k, v in kw.items():
            setattr(st, k, v)
        s = Source(path=self.mp3, settings=st, title="Mijn Set", url="https://soundcloud.com/x")
        s.info = probe(self.mp3)
        return s

    def stream(self, path, kind):
        import json
        j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-print_format", "json", path],
                                      capture_output=True, text=True).stdout)
        return next(s for s in j["streams"] if s["codec_type"] == kind)

    def test_waveform_video(self):
        s = self.src()
        self.assertTrue(s.info.audio_only)
        out = ex.export_clip(FF, s, Clip(start=100, end=104), tempfile.mkdtemp(), 1)
        v, a = self.stream(out, "video"), self.stream(out, "audio")
        self.assertEqual((v["width"], v["height"]), (1080, 1350))
        self.assertAlmostEqual(float(v["duration"]), float(a["duration"]), delta=0.06)
        png = os.path.join(tempfile.mkdtemp(), "f.png")
        subprocess.run([FF, "-v", "error", "-ss", "2", "-i", out, "-frames:v", "1", png], check=True)
        with Image.open(png) as im:
            im = im.convert("RGB")
            mid = sum(1 for x in range(0, 1080, 6) for y in range(470, 880, 6) if sum(im.getpixel((x, y))) > 120)
            top = sum(1 for x in range(0, 1080, 6) for y in range(0, 300, 6) if sum(im.getpixel((x, y))) > 120)
        self.assertGreater(mid, 100)
        self.assertEqual(top, 0)

    def test_black_video(self):
        out = ex.export_clip(FF, self.src(audio_visual="black"), Clip(start=100, end=102), tempfile.mkdtemp(), 1)
        self.assertEqual(self.stream(out, "video")["width"], 1080)

    def test_still_frame(self):
        s = self.src()
        png = os.path.join(tempfile.mkdtemp(), "s.png")
        cp = subprocess.run(ex.build_still_command(FF, s.info, 100, Look(), s.settings, [], png), capture_output=True)
        self.assertEqual(cp.returncode, 0, cp.stderr[-300:])
        with Image.open(png) as im:
            self.assertEqual(im.size, (1080, 1350))

    def test_cover_and_caption(self):
        d = tempfile.mkdtemp()
        s = self.src(export_cover=True, caption_enabled=True,
                     caption_template="{title} – {label}\n{dj} @ {start} ({duration}s) {url} {onbekend}")
        out = ex.export_clip(FF, s, Clip(start=100, end=103, label="Drop één"), d, 4)
        stem = os.path.splitext(out)[0]
        with Image.open(stem + "_cover.jpg") as im:
            self.assertEqual(im.size, (1080, 1350))
        with open(stem + "_bijschrift.txt", encoding="utf-8") as f:
            txt = f.read()
        self.assertEqual(txt, "Mijn Set – Drop één\n@ 00:01:40 (3s) https://soundcloud.com/x {onbekend}\n")

    def test_chapters_from_file(self):
        p = os.path.join(MEDIA, "Ch.mp4")
        if not os.path.isfile(p):
            self.skipTest("Ch.mp4 ontbreekt (draai tests/make_media.sh)")
        self.assertEqual([c["title"] for c in probe(p).chapters], ["Intro", "Track twee – é"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
