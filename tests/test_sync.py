"""Tests voor beeld/geluid-sync (meten en corrigeren) en de export van de volledige set (video, MP3/WAV/FLAC).
Testbron V_sync360.mp4: het beeld loopt 0,36 s achter op het geluid (gemaakt door tests/make_sync_set.py)."""
from __future__ import annotations
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from accu_clipper.ffmpeg_tools import find_tool, probe, ToolError
from accu_clipper.model import Source, Clip, ExportSettings, Project
from accu_clipper import avsync, fullset, exporter as ex

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA = os.environ.get("ACCU_TEST_MEDIA", "/tmp/t")
FFMPEG, FFPROBE = find_tool("ffmpeg"), find_tool("ffprobe")
SYNC = os.path.join(MEDIA, "V_sync360.mp4")
LAG = 0.36
# Een lamp die halverwege een frame aangaat, is pas in het volgende frame te zien: gemiddeld een half frame
# (25 fps → 20 ms) later. Zo ziet een kijker het ook, dus de meting hoort ±20 ms boven LAG uit te komen.
TOL = 0.045


def src(path: str, **kw) -> Source:
    st = ExportSettings(crf=26, preset="ultrafast", profile="custom", out_format="16x9")
    for k, v in kw.items():
        setattr(st, k, v)
    s = Source(path=path, settings=st)
    s.info = probe(path)
    return s


def pcm(path: str, start: float = 0.0, dur: float | None = None, sr: int = 8000) -> np.ndarray:
    cmd = [FFMPEG, "-v", "error", "-ss", f"{start}", "-i", path] + (["-t", f"{dur}"] if dur else []) + \
          ["-map", "0:a:0", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, np.float32)


def best_lag(a: np.ndarray, b: np.ndarray, max_lag: int) -> int:
    """Verschuiving k waarbij b[t] ≈ a[t + k] het best past."""
    n = min(len(a), len(b)) - 2 * max_lag
    seg = b[max_lag:max_lag + n]
    scores = [float(np.dot(a[max_lag + k:max_lag + k + n], seg)) for k in range(-max_lag, max_lag + 1)]
    return int(np.argmax(scores)) - max_lag


class SyncBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(SYNC):
            subprocess.run([sys.executable, os.path.join(HERE, "make_sync_set.py"), SYNC, "--lag", str(LAG),
                            "--dur", "150"], check=True)
        cls.tmp = tempfile.mkdtemp(prefix="accu_sync_")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestDetect(SyncBase):
    def test_detects_known_lag(self):
        r = avsync.detect(FFMPEG, probe(SYNC))
        self.assertAlmostEqual(r.offset, LAG + 0.02, delta=TOL)
        self.assertEqual(r.level, "hoog", r)
        self.assertEqual(r.drift, 0.0)
        self.assertAlmostEqual(r.candidates[0][0], r.offset, delta=0.001)
        self.assertIn("achter", avsync.describe_offset(r.offset))

    def test_negative_lag_and_progress_and_cancel(self):
        p = os.path.join(self.tmp, "ahead.mp4")
        subprocess.run([sys.executable, os.path.join(HERE, "make_sync_set.py"), p, "--lag", "-0.2", "--dur", "120",
                        "--bpm", "128", "--fps", "30", "--seed", "3"], check=True)
        seen = []
        r = avsync.detect(FFMPEG, probe(p), progress=lambda f, t: seen.append(f))
        self.assertAlmostEqual(r.offset, -0.2 + 0.017, delta=TOL)
        self.assertTrue(seen and all(0 <= f <= 1 for f in seen))
        import threading
        ev = threading.Event(); ev.set()
        with self.assertRaises(avsync.SyncCancelled):
            avsync.detect(FFMPEG, probe(p), cancel=ev)

    def test_drift_is_found(self):
        p = os.path.join(self.tmp, "drift.mp4")
        subprocess.run([sys.executable, os.path.join(HERE, "make_sync_set.py"), p, "--lag", "0.2",
                        "--drift-ms-per-hour", "2000", "--dur", "480", "--seed", "5"], check=True)
        r = avsync.detect(FFMPEG, probe(p))
        self.assertAlmostEqual(r.drift * 3600, 2.0, delta=0.4)
        self.assertAlmostEqual(r.offset_at(240), 0.2 + 2.0 * 240 / 3600 + 0.02, delta=TOL)
        # volledige export met drift-correctie → gemeten verschil overal ≈ 0
        s = src(p, av_offset=r.offset, av_drift=r.drift, master_mode="off")
        out = fullset.export_video(FFMPEG, s, self.tmp)
        r2 = avsync.detect(FFMPEG, probe(out))
        self.assertLess(abs(r2.offset), TOL)
        self.assertEqual(r2.drift, 0.0)

    def test_needs_audio_and_video(self):
        with self.assertRaises(ToolError):
            avsync.detect(FFMPEG, probe(os.path.join(MEDIA, "G_noaudio.mp4")))

    def test_comb_cancels_periodic_part(self):
        # zoals in detect(): de curve is breder dan het deel dat wordt beoordeeld (randen vallen buiten beschouwing)
        c = np.zeros(801); P = 47.0
        for k in range(-8, 8):
            c[int(430 + k * P)] += 1.0               # beat-kam rond +30
        c[430] += 0.5                                # eenmalige samenhang op +30
        f = avsync.comb(c, P)[200:601]
        self.assertEqual(int(np.argmax(f)) + 200, 430)
        # zonder filter zijn alle beat-pieken (bijna) even hoog; na het filter steekt de echte er ver bovenuit
        rest = np.delete(f, range(225, 236))
        self.assertGreater(f[230], 5 * rest.max())

    def test_window_starts(self):
        self.assertEqual(avsync.window_starts(30, 10, 40), [0.0])
        w = avsync.window_starts(7200, 10, 40)
        self.assertEqual(len(w), 10)
        self.assertTrue(all(0 <= t <= 7200 - 40 for t in w))


class TestCorrection(SyncBase):
    def test_clip_audio_is_shifted(self):
        s = src(SYNC, av_offset=0.5)
        out = ex.export_clip(FFMPEG, s, Clip(start=40, end=60), self.tmp, 1)
        # geluid van de clip moet gelijk zijn aan het bron-geluid vanaf 40 − 0,5 s
        a = pcm(SYNC, 39.0, 22.0); b = pcm(out, 0.0, 18.0)
        k = best_lag(a, b, 8000)                      # b[t] ≈ a[t + k]  →  k = 0,5 s · 8000
        self.assertAlmostEqual(k / 8000, 0.5, delta=0.01)

    def test_clip_at_start_gets_silence(self):
        s = src(SYNC, av_offset=0.5)
        out = ex.export_clip(FFMPEG, s, Clip(start=0.2, end=5), self.tmp, 2)
        b = pcm(out)
        self.assertLess(np.abs(b[: int(0.25 * 8000)]).max(), 0.02)   # 0,3 s stilte vooraan (min. marge)
        self.assertGreater(np.abs(b[int(0.4 * 8000):]).max(), 0.1)
        d = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                                 capture_output=True, text=True).stdout)
        self.assertAlmostEqual(d, 4.8, delta=0.1)

    def test_no_offset_means_single_input(self):
        s = src(SYNC)
        cmd = ex.build_command(FFMPEG, s.info, 10, 5, Clip().look, s.settings, [], "/tmp/x.mp4")
        self.assertEqual(cmd.count("-i"), 1)
        s.settings.av_offset = 0.2
        cmd = ex.build_command(FFMPEG, s.info, 10, 5, Clip().look, s.settings, [], "/tmp/x.mp4")
        self.assertEqual(cmd.count("-i"), 2)
        self.assertEqual(cmd[cmd.index("-i", cmd.index("-i") + 1) - 1], "9.800000")

    def test_full_video_fixes_sync_and_overcorrection_shows_negative(self):
        r = avsync.detect(FFMPEG, probe(SYNC))
        s = src(SYNC, av_offset=r.offset, master_mode="off")
        out = fullset.export_video(FFMPEG, s, self.tmp)
        self.assertTrue(out.endswith("_volledig_sync.mp4"))
        self.assertLess(abs(avsync.detect(FFMPEG, probe(out)).offset), TOL)
        info = probe(out)
        self.assertAlmostEqual(info.duration, s.info.duration, delta=0.1)
        self.assertEqual(info.codec, s.info.codec)                      # beeld gekopieerd
        # te veel corrigeren → beeld loopt vóór (negatief), het pad met atrim/geen stilte
        s2 = src(SYNC, av_offset=r.offset + 0.5, master_mode="off")
        out2 = fullset.export_video(FFMPEG, s2, self.tmp)
        self.assertAlmostEqual(avsync.detect(FFMPEG, probe(out2)).offset, -0.5, delta=TOL)

    def test_full_video_trim_reencode(self):
        s = src(SYNC, av_offset=0.38, master_mode="off", full_start=30, full_end=100, full_video_mode="reencode")
        out = fullset.export_video(FFMPEG, s, self.tmp)
        info = probe(out)
        self.assertAlmostEqual(info.duration, 70, delta=0.1)
        self.assertEqual(info.codec, "h264")
        a = pcm(SYNC, 29.0, 30.0); b = pcm(out, 0.0, 25.0)
        self.assertAlmostEqual(best_lag(a, b, 8000) / 8000, 1.0 - 0.38, delta=0.01)

    def test_range_validation(self):
        s = src(SYNC, full_start=100, full_end=100.2)
        with self.assertRaises(ToolError):
            fullset.full_range(s.info, s.settings)
        s.settings.full_start, s.settings.full_end = 10, 0
        self.assertEqual(fullset.full_range(s.info, s.settings), (10, s.info.duration - 10))

    def test_settings_saved_in_project(self):
        p = Project(); s = src(SYNC, av_offset=0.123, av_drift=1e-5, master_lufs=-9.0, audio_format="flac")
        p.sources.append(s)
        f = os.path.join(self.tmp, "p.accuclip"); p.save(f)
        st = Project.load(f).sources[0].settings
        self.assertEqual((st.av_offset, st.av_drift, st.master_lufs, st.audio_format), (0.123, 1e-5, -9.0, "flac"))


class TestMaster(SyncBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.hot = os.path.join(cls.tmp, "hot.mov")       # te heet opgenomen: +24 dB, hard afgetopt (16-bit PCM)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", os.path.join(MEDIA, "N_noise.mp4"), "-af",
                        "volume=24dB,aformat=sample_fmts=s16", "-c:v", "copy", "-c:a", "pcm_s16le", cls.hot],
                       check=True)
        cls.quiet = os.path.join(MEDIA, "S_synthset.mp4")

    def check(self, res, lufs=None, ceiling=-1.0):
        self.assertTrue(os.path.isfile(res.path) and ".part" not in res.path)
        self.assertLessEqual(res.after.true_peak, ceiling + 0.15, res.summary())
        if lufs is not None:
            self.assertAlmostEqual(res.after.integrated, lufs, delta=0.6, msg=res.summary())
        # onafhankelijke controle: geen enkele sample (per kanaal, zonder omrekenen) op of boven 0 dBFS
        raw = subprocess.run([FFMPEG, "-v", "error", "-i", res.path, "-map", "0:a:0", "-f", "f32le", "-"],
                             capture_output=True, check=True).stdout
        self.assertLess(np.abs(np.frombuffer(raw, np.float32)).max(), 0.999)

    def test_mp3_streaming_level(self):
        for p in (self.hot, self.quiet):
            res = fullset.export_audio(FFMPEG, src(p), self.tmp)
            self.assertTrue(res.path.endswith("_master_-14LUFS.mp3"))
            self.check(res, -14.0)
            j = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_name,sample_rate,bit_rate",
                                "-of", "csv=p=0", res.path], capture_output=True, text=True).stdout.strip()
            self.assertEqual(j, "mp3,44100,320000")

    def test_hot_source_is_flagged(self):
        res = fullset.export_audio(FFMPEG, src(self.hot, master_lufs=-9.0), self.tmp)
        self.assertTrue(res.before.source_clipped)
        self.assertTrue(any("afgetopt" in w for w in res.warnings), res.warnings)
        self.check(res, -9.0)
        # schone bron (sinus op −6 dBFS) wordt niet als afgetopt gemeld
        self.assertFalse(fullset.export_audio(FFMPEG, src(os.path.join(MEDIA, "A.mp4"), master_mode="peak"),
                                              self.tmp).before.source_clipped)

    def test_peak_mode_and_formats(self):
        res = fullset.export_audio(FFMPEG, src(self.quiet, master_mode="peak", audio_format="wav24",
                                               master_ceiling=-2.0), self.tmp)
        self.check(res, ceiling=-2.0)
        self.assertAlmostEqual(res.after.true_peak, -2.0, delta=0.3)
        self.assertAlmostEqual(res.after.integrated - res.before.integrated, res.gain_db, delta=0.3)
        out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                              res.path], capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "pcm_s24le")
        res = fullset.export_audio(FFMPEG, src(self.hot, audio_format="flac", master_lufs=-16.0), self.tmp)
        self.check(res, -16.0)
        self.assertTrue(res.path.endswith(".flac"))

    def test_clip_scan(self):
        self.assertGreaterEqual(fullset.clip_scan(FFMPEG, self.hot, 0, 6)[0], 1)
        hits, n = fullset.clip_scan(FFMPEG, self.quiet, 0, probe(self.quiet).duration)
        self.assertEqual(hits, 0); self.assertGreater(n, 5)

    def test_off_leaves_audio_untouched(self):
        res = fullset.export_audio(FFMPEG, src(self.quiet, master_mode="off", audio_format="wav24"), self.tmp)
        self.assertAlmostEqual(res.after.integrated, res.before.integrated, delta=0.15)
        self.assertEqual(res.gain_db, 0.0)

    def test_tags_and_range(self):
        s = src(self.quiet, dj_name="DJ Test", full_start=60, full_end=120)
        s.title = "Zondag – Café"
        res = fullset.export_audio(FFMPEG, s, self.tmp)
        tags = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration:format_tags=title,artist,album",
                               "-of", "default=nw=1", res.path], capture_output=True, text=True).stdout
        self.assertIn("title=Zondag – Café", tags); self.assertIn("artist=DJ Test", tags)
        self.assertIn("album=Radio ACCU", tags)
        self.assertAlmostEqual(probe(res.path).duration, 60, delta=0.15)

    def test_video_audio_level_with_aac(self):
        res = fullset.export_video(FFMPEG, src(self.hot), self.tmp)
        self.check(res, -14.0)

    def test_parse_levels(self):
        lines = ["[Parsed_ebur128_0 @ 0x1] Summary:", "", "  Integrated loudness:", "    I:         -13.4 LUFS",
                 "    Threshold: -23.5 LUFS", "", "  Loudness range:", "    LRA:         4.1 LU", "",
                 "  True peak:", "    Peak:        1.2 dBFS", "[Parsed_astats_1 @ 0x2] Overall",
                 "Peak level dB: -0.000000", "Peak count: 345"]
        lv = fullset.parse_levels(lines)
        self.assertEqual((lv.integrated, lv.lra, lv.true_peak, lv.clip_events), (-13.4, 4.1, 1.2, 345))
        self.assertTrue(lv.source_clipped)

    def test_no_audio_source(self):
        with self.assertRaises(ToolError):
            fullset.export_audio(FFMPEG, src(os.path.join(MEDIA, "G_noaudio.mp4")), self.tmp)


if __name__ == "__main__":
    unittest.main()
