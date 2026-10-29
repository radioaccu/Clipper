"""Integratietests: echte FFmpeg-exports controleren op resolutie, duur, fps, audio, crop, branding en sync.
Draaien met:  python3 -m unittest discover -s tests -v
Testbronnen worden gegenereerd door tests/make_media.sh (zie README)."""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from copy import deepcopy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image  # alleen voor tests

from accu_clipper.ffmpeg_tools import find_tool, probe, ToolError
from accu_clipper.model import Source, Clip, Look, ExportSettings
from accu_clipper import exporter as ex

MEDIA = os.environ.get("ACCU_TEST_MEDIA", "/tmp/t")
FFMPEG, FFPROBE = find_tool("ffmpeg"), find_tool("ffprobe")


def fast_settings(**kw) -> ExportSettings:
    st = ExportSettings(crf=23, preset="ultrafast", profile="custom")
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def make_source(name: str, st: ExportSettings | None = None) -> Source:
    s = Source(path=os.path.join(MEDIA, name), settings=st or fast_settings())
    s.info = probe(s.path)
    return s


def ffprobe_json(path: str) -> dict:
    out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
                         capture_output=True, text=True).stdout
    return json.loads(out)


def frame_at(path: str, t: float, out_png: str) -> Image.Image:
    subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{t}", "-i", path, "-frames:v", "1", out_png], check=True)
    return Image.open(out_png).convert("RGB")


def is_color(px, name, tol=60):
    ref = {"red": (255, 0, 0), "blue": (0, 0, 255), "black": (0, 0, 0), "white": (255, 255, 255),
           "green": (0, 255, 0)}[name]
    return all(abs(a - b) <= tol for a, b in zip(px, ref))


class ExportBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="accu_test_")

    def export(self, src: Source, clip: Clip, **kw) -> str:
        return ex.export_clip(FFMPEG, src, clip, self.tmp, 1, **kw)

    def vstream(self, path):
        return next(s for s in ffprobe_json(path)["streams"] if s["codec_type"] == "video")

    def astream(self, path):
        return next((s for s in ffprobe_json(path)["streams"] if s["codec_type"] == "audio"), None)


class TestFormat(ExportBase):
    def test_always_1080x1350_for_all_source_types(self):
        for name in ["A.mp4", "B_rot.mp4", "C_sar.mp4", "D_60.mp4"]:
            for mode in ["fill", "fit"]:
                src = make_source(name)
                out = self.export(src, Clip(start=1, end=3, look=Look(crop_mode=mode)))
                v = self.vstream(out)
                self.assertEqual((v["width"], v["height"]), (1080, 1350), f"{name} {mode}")
                self.assertEqual(v["pix_fmt"], "yuv420p")
                self.assertEqual(v["codec_name"], "h264")
                self.assertAlmostEqual(float(ffprobe_json(out)["format"]["duration"]), 2.0, delta=0.1)

    def test_fill_crop_positions(self):
        src = make_source("A.mp4")  # links rood, rechts blauw
        for fx, left, right in [(0.0, "red", "red"), (1.0, "blue", "blue"), (0.5, "red", "blue")]:
            out = self.export(src, Clip(start=1, end=2, look=Look(fx=fx)))
            im = frame_at(out, 0.4, os.path.join(self.tmp, "f.png"))
            self.assertTrue(is_color(im.getpixel((100, 675)), left), f"fx={fx} links {im.getpixel((100,675))}")
            self.assertTrue(is_color(im.getpixel((980, 675)), right), f"fx={fx} rechts {im.getpixel((980,675))}")

    def test_fit_has_black_bars_and_full_image(self):
        src = make_source("A.mp4")
        out = self.export(src, Clip(start=1, end=2, look=Look(crop_mode="fit")))
        im = frame_at(out, 0.4, os.path.join(self.tmp, "f.png"))
        self.assertTrue(is_color(im.getpixel((540, 50)), "black", 30))
        self.assertTrue(is_color(im.getpixel((540, 1300)), "black", 30))
        self.assertTrue(is_color(im.getpixel((100, 675)), "red"))     # volledig beeld: links rood...
        self.assertTrue(is_color(im.getpixel((980, 675)), "blue"))    # ...rechts blauw
        # beeldhoogte ≈ 1080*1080/1920 = 607 → balk boven ≈ 372 px
        col = [im.getpixel((100, y)) for y in range(1350)]
        first = next(y for y, p in enumerate(col) if not is_color(p, "black", 30))
        last = max(y for y, p in enumerate(col) if not is_color(p, "black", 30))
        self.assertAlmostEqual(last - first + 1, 607, delta=4)

    def test_rotation_metadata_is_honoured(self):
        # B_rot toont 1080x1920 (portret). Fill: hoogte past exact; links rood / rechts blauw bestaat na rotatie niet meer
        # zoals bij A: na 90° is rood boven of onder. We controleren dat fit een breed-passend beeld geeft (geen zwarte zijkanten).
        src = make_source("B_rot.mp4")
        self.assertEqual((src.info.display_w, src.info.display_h), (1080, 1920))
        out = self.export(src, Clip(start=1, end=2, look=Look(crop_mode="fit")))
        im = frame_at(out, 0.4, os.path.join(self.tmp, "f.png"))
        # portret 9:16 in 4:5 → zwarte balken links/rechts, volledige hoogte gevuld
        self.assertTrue(is_color(im.getpixel((5, 675)), "black", 30))
        self.assertFalse(is_color(im.getpixel((540, 5)), "black", 30))
        self.assertFalse(is_color(im.getpixel((540, 1344)), "black", 30))

    def test_pixel_aspect_ratio_anamorphic(self):
        # 1440x1080 met SAR 4:3 = 16:9. Fit moet daarom ~607 hoog worden (niet 810 zoals bij SAR 1:1).
        src = make_source("C_sar.mp4")
        out = self.export(src, Clip(start=1, end=2, look=Look(crop_mode="fit")))
        im = frame_at(out, 0.4, os.path.join(self.tmp, "f.png"))
        col = [im.getpixel((100, y)) for y in range(1350)]
        first = next(y for y, p in enumerate(col) if not is_color(p, "black", 30))
        last = max(y for y, p in enumerate(col) if not is_color(p, "black", 30))
        self.assertAlmostEqual(last - first + 1, 607, delta=4)

    def test_zoom_and_offset(self):
        src = make_source("A.mp4")
        # zoom 2 + fx=1: rechtsonder het beeld → blauw over de volle breedte
        out = self.export(src, Clip(start=1, end=2, look=Look(zoom=2.0, fx=1.0)))
        im = frame_at(out, 0.4, os.path.join(self.tmp, "f.png"))
        self.assertTrue(is_color(im.getpixel((10, 675)), "blue"))


class TestFps(ExportBase):
    def test_fps_options(self):
        src = make_source("A.mp4")
        for opt, expect in [("source", 25.0), ("24", 24.0), ("30", 30.0), ("50", 50.0), ("60", 60.0)]:
            src.settings.fps = opt
            out = self.export(src, Clip(start=1, end=2))
            r = self.vstream(out)["r_frame_rate"]
            n, d = r.split("/")
            self.assertAlmostEqual(int(n) / int(d), expect, delta=0.01, msg=opt)

    def test_source_fps_5994_preserved(self):
        src = make_source("D_60.mp4")
        out = self.export(src, Clip(start=1, end=2))
        n, d = self.vstream(out)["r_frame_rate"].split("/")
        self.assertAlmostEqual(int(n) / int(d), 60000 / 1001, delta=0.01)


class TestAudioAndSync(ExportBase):
    def test_audio_aac_320k(self):
        # De ingebouwde FFmpeg-AAC-encoder gebruikt 320k als doel en blijft bij simpele signalen eronder;
        # met ruisachtig (muziekachtig) materiaal verwachten we ruim boven 250 kbps.
        src = make_source("N_noise.mp4", fast_settings(audio_bitrate_k=320))
        out = self.export(src, Clip(start=0, end=5))
        a = self.astream(out)
        self.assertEqual(a["codec_name"], "aac")
        self.assertGreater(int(a["bit_rate"]), 250000)
        self.assertLessEqual(int(a["bit_rate"]), 330000)
        self.assertEqual(int(a["sample_rate"]), 48000)
        cmd = ex.build_command(FFMPEG, src.info, 0, 2, Look(), src.settings, [], "/tmp/x.mp4")
        self.assertIn("320k", cmd)

    def test_no_audio_source(self):
        out = self.export(make_source("G_noaudio.mp4"), Clip(start=1, end=2))
        self.assertIsNone(self.astream(out))

    def test_mute_keeps_silent_track(self):
        out = self.export(make_source("A.mp4"), Clip(start=1, end=3, look=Look(mute=True)))
        self.assertLess(float(self.max_volume(out)), -80)

    def max_volume(self, path):
        cp = subprocess.run([FFMPEG, "-hide_banner", "-i", path, "-af", "volumedetect", "-vn", "-f", "null", "-"],
                            capture_output=True, text=True)
        return re.search(r"max_volume: (-?[\d.]+) dB", cp.stderr).group(1)

    def test_gain_does_not_clip(self):
        src = make_source("H_loud.mp4")
        ref = float(self.max_volume(src.path))
        out = self.export(src, Clip(start=1, end=4, look=Look(audio_gain_db=9.0)))
        self.assertLessEqual(float(self.max_volume(out)), -0.3)   # limiter houdt pieken onder ~-1 dBFS
        out0 = self.export(src, Clip(start=1, end=4))             # zonder gain: mix ongewijzigd (geen limiter)
        self.assertAlmostEqual(float(self.max_volume(out0)), ref, delta=1.0)

    def test_normalize_peak(self):
        src = make_source("A.mp4", fast_settings(normalize="peak", peak_target=-3.0))
        out = self.export(src, Clip(start=1, end=4))
        self.assertAlmostEqual(float(self.max_volume(out)), -3.0, delta=0.8)

    def test_normalize_loudness(self):
        src = make_source("A.mp4", fast_settings(normalize="loudness", loudness_target=-16.0))
        out = self.export(src, Clip(start=1, end=6))
        cp = subprocess.run([FFMPEG, "-hide_banner", "-i", out, "-af", "loudnorm=print_format=json", "-vn", "-f", "null", "-"],
                            capture_output=True, text=True)
        data = json.loads(re.search(r"\{[^{}]*input_i[^{}]*\}", cp.stderr, re.S).group(0))
        self.assertAlmostEqual(float(data["input_i"]), -16.0, delta=1.5)

    def test_av_sync_and_duration(self):
        # E_sync: zwart+stilte tot 5.0 s, daarna wit+1 kHz. Clip van 3.0 → verwacht omslag op 2.0 s in beide sporen.
        src = make_source("E_sync.mp4")
        out = self.export(src, Clip(start=3.0, end=9.0))
        j = ffprobe_json(out)
        vd = float(next(s for s in j["streams"] if s["codec_type"] == "video")["duration"])
        ad = float(next(s for s in j["streams"] if s["codec_type"] == "audio")["duration"])
        self.assertAlmostEqual(vd, 6.0, delta=0.08)
        self.assertAlmostEqual(vd, ad, delta=0.06)
        # videoomslag
        dark = frame_at(out, 1.8, os.path.join(self.tmp, "d.png")).getpixel((540, 675))
        bright = frame_at(out, 2.3, os.path.join(self.tmp, "b.png")).getpixel((540, 675))
        self.assertTrue(is_color(dark, "black", 40), dark)
        self.assertTrue(is_color(bright, "white", 40), bright)
        # audio-omslag via silencedetect
        cp = subprocess.run([FFMPEG, "-hide_banner", "-i", out, "-af", "silencedetect=n=-50dB:d=0.1", "-vn", "-f", "null", "-"],
                            capture_output=True, text=True)
        end = float(re.search(r"silence_end: ([\d.]+)", cp.stderr).group(1))
        self.assertAlmostEqual(end, 2.0, delta=0.06)

    def test_fades_apply(self):
        src = make_source("A.mp4")
        out = self.export(src, Clip(start=1, end=4, look=Look(video_fade_in=1.0, video_fade_out=1.0,
                                                               audio_fade_in=1.0, audio_fade_out=1.0)))
        first = frame_at(out, 0.0, os.path.join(self.tmp, "f0.png")).getpixel((540, 675))
        mid = frame_at(out, 1.5, os.path.join(self.tmp, "f1.png")).getpixel((100, 675))
        self.assertTrue(is_color(first, "black", 30), first)
        self.assertTrue(is_color(mid, "red"), mid)


class TestBranding(ExportBase):
    def test_logo_overlay_position_and_alpha(self):
        logo = os.path.join(self.tmp, "logo.png")
        Image.new("RGBA", (200, 200), (0, 255, 0, 255)).save(logo)
        half = os.path.join(self.tmp, "half.png")
        im = Image.new("RGBA", (200, 200), (0, 255, 0, 255)); im.putalpha(128); im.save(half)
        st = fast_settings(logo_path=logo, logo_cx=0.5, logo_cy=0.5, logo_width=0.25, logo_opacity=1.0)
        out = self.export(make_source("A.mp4", st), Clip(start=1, end=2, look=Look(fx=0.0)))
        px = frame_at(out, 0.4, os.path.join(self.tmp, "l.png"))
        self.assertTrue(is_color(px.getpixel((540, 675)), "green", 50), px.getpixel((540, 675)))   # logo in het midden
        self.assertTrue(is_color(px.getpixel((100, 675)), "red"))                                  # daarbuiten ongemoeid
        # logo 270 px breed → x 405..675; net erbuiten is rood
        self.assertTrue(is_color(px.getpixel((395, 675)), "red"))
        st2 = fast_settings(logo_path=half, logo_cx=0.5, logo_cy=0.5, logo_width=0.25)
        out2 = self.export(make_source("A.mp4", st2), Clip(start=1, end=2, look=Look(fx=0.0)))
        p2 = frame_at(out2, 0.4, os.path.join(self.tmp, "l2.png")).getpixel((540, 675))
        self.assertTrue(p2[0] > 60 and p2[1] > 100, f"transparantie niet zichtbaar: {p2}")      # rood+groen gemengd

    def test_text_overlay_via_renderer(self):
        def renderer(st, tmpdir):
            p = os.path.join(tmpdir, "t.png")
            Image.new("RGBA", (400, 100), (255, 255, 255, 255)).save(p)
            return p
        st = fast_settings(dj_name="DJ Test", dj_cx=0.5, dj_cy=0.9, dj_opacity=1.0)
        out = self.export(make_source("A.mp4", st), Clip(start=1, end=2, look=Look(fx=0.0)), text_renderer=renderer)
        px = frame_at(out, 0.4, os.path.join(self.tmp, "t.png")).getpixel((540, 1215))
        self.assertTrue(is_color(px, "white", 40), px)


class TestHdr(ExportBase):
    def test_hdr_detected_and_tonemapped_to_sdr(self):
        src = make_source("F_hdr.mp4")
        self.assertEqual(src.info.hdr, "PQ")
        out = self.export(src, Clip(start=0, end=2))
        v = self.vstream(out)
        self.assertEqual((v["width"], v["height"]), (1080, 1350))
        self.assertEqual(v["pix_fmt"], "yuv420p")
        self.assertEqual(v.get("color_transfer"), "bt709")
        self.assertEqual(v.get("color_primaries"), "bt709")

    def test_hdr_keep_gives_hevc_10bit(self):
        src = make_source("F_hdr.mp4", fast_settings(hdr_mode="keep"))
        out = self.export(src, Clip(start=0, end=2))
        v = self.vstream(out)
        self.assertEqual(v["codec_name"], "hevc")
        self.assertEqual(v["pix_fmt"], "yuv420p10le")
        self.assertEqual(v.get("color_transfer"), "smpte2084")


class TestReliability(ExportBase):
    def test_filename_with_spaces_accents_specials(self):
        src = make_source("Mijn Set – Café #1 (zondag) [live].mp4")
        out = self.export(src, Clip(start=1, end=2))
        self.assertTrue(os.path.isfile(out))
        self.assertIn("Café", os.path.basename(out))
        self.assertEqual(self.vstream(out)["width"], 1080)

    def test_never_overwrites(self):
        src = make_source("A.mp4")
        a = ex.export_clip(FFMPEG, src, Clip(start=1, end=2), self.tmp, 7)
        b = ex.export_clip(FFMPEG, src, Clip(start=1, end=2), self.tmp, 7)
        self.assertNotEqual(a, b)
        self.assertTrue(os.path.isfile(a) and os.path.isfile(b))

    def test_cancel_leaves_no_files(self):
        d = tempfile.mkdtemp(prefix="accu_cancel_")
        src = make_source("A.mp4", fast_settings(preset="veryslow", crf=5))
        cancel = threading.Event()
        threading.Timer(0.6, cancel.set).start()
        with self.assertRaises(ex.Cancelled):
            ex.export_clip(FFMPEG, src, Clip(start=0, end=8, look=Look(zoom=2)), d, 1, cancel=cancel)
        self.assertEqual(os.listdir(d), [])

    def test_one_failing_clip_does_not_stop_queue(self):
        d = tempfile.mkdtemp(prefix="accu_q_")
        good = make_source("A.mp4")
        bad = make_source("A.mp4")
        bad.info.path = "/nonexistent/weg.mp4"
        jobs = [(good, Clip(start=1, end=2), 1), (bad, Clip(start=1, end=2), 1), (good, Clip(start=2, end=3), 2)]
        res = ex.run_queue(FFMPEG, jobs, d)
        self.assertEqual([r.ok for r in res], [True, False, True])
        self.assertIn("niet gevonden", res[1].error)
        txt = ex.write_summary(d, jobs, res)
        self.assertTrue(os.path.isfile(txt) and os.path.isfile(txt[:-4] + ".json"))
        with open(txt, encoding="utf-8") as fh:
            self.assertIn("MISLUKT", fh.read())

    def test_source_unchanged(self):
        src = make_source("A.mp4")
        before = os.stat(src.path)
        self.export(src, Clip(start=1, end=2))
        after = os.stat(src.path)
        self.assertEqual((before.st_size, before.st_mtime), (after.st_size, after.st_mtime))

    def test_disk_space_check(self):
        with self.assertRaises(ToolError):
            ex.check_disk_space(self.tmp, 10 ** 15)

    def test_progress_reports(self):
        seen = []
        self.export(make_source("A.mp4"), Clip(start=0, end=4), progress=seen.append)
        self.assertTrue(seen and max(seen) > 0.8)

    def test_encoder_variants_build(self):
        # Alleen commando-opbouw (VideoToolbox bestaat niet onder Linux) en lossless echt uitvoeren.
        src = make_source("A.mp4", fast_settings(encoder="h264_videotoolbox", vt_quality=70))
        cmd = ex.build_command(FFMPEG, src.info, 0, 2, Look(), src.settings, [], "/tmp/x.mp4")
        self.assertIn("h264_videotoolbox", cmd)
        src2 = make_source("A.mp4", fast_settings(lossless=True, preset="ultrafast"))
        out = self.export(src2, Clip(start=1, end=2))
        self.assertEqual(self.vstream(out)["codec_name"], "h264")
        self.assertEqual(self.vstream(out)["width"], 1080)


class TestStill(ExportBase):
    def test_still_uses_same_pipeline(self):
        src = make_source("A.mp4")
        png = os.path.join(self.tmp, "still.png")
        cmd = ex.build_still_command(FFMPEG, src.info, 2.0, Look(fx=0.5), src.settings, [], png)
        subprocess.run(cmd, check=True, capture_output=True)
        with Image.open(png) as im:
            self.assertEqual(im.size, (1080, 1350))


if __name__ == "__main__":
    unittest.main(verbosity=2)
