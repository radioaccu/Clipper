"""Tests voor audio-extractie en highlightdetectie (synthetische set met bekende structuur)."""
import os, sys, threading, unittest, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from accu_clipper import features as fe, highlights as hl
from accu_clipper.ffmpeg_tools import find_tool, probe, ToolError
from accu_clipper.model import Project, Source, Clip, Look, ExportSettings, apply_profile
from accu_clipper.timecode import parse_time, format_time

MEDIA = os.environ.get("ACCU_TEST_MEDIA", "/tmp/t")
SET = os.path.join(MEDIA, "S_synthset.mp4")     # drop 1 @ 90 s, breakdown @ 150 s, drop 2 @ 180 s
FF = find_tool("ffmpeg")


class TestAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.info = probe(SET)
        cls.f = fe.extract(FF, SET, cls.info.duration, use_cache=False)

    def test_feature_shape_and_duration(self):
        self.assertAlmostEqual(self.f.duration, 360, delta=1)
        self.assertEqual(self.f.data.shape[1], 7)
        self.assertAlmostEqual(self.f.n / fe.FPS, 360, delta=1)

    def test_drops_found_near_known_drops(self):
        s, note = hl.suggest(self.f, 30, 6, "drops")
        strong = [g for g in s if not g.weak]
        events = [g.start + g.event_offset for g in strong]
        for known in (90, 180):
            self.assertTrue(any(abs(e - known) <= 4 for e in events), f"drop {known}s niet gevonden: {events}")

    def test_transitions_find_breakdown(self):
        s, _ = hl.suggest(self.f, 30, 6, "transitions")
        events = [g.start + g.event_offset for g in s]
        self.assertTrue(any(abs(e - 150) <= 6 for e in events), events)   # begin breakdown
        self.assertTrue(any(abs(e - 180) <= 6 for e in events), events)   # herstart

    def test_no_overlap_in_any_mode(self):
        for mode in hl.MODES:
            for dur in (15, 30, 60):
                s, _ = hl.suggest(self.f, dur, 15, mode)
                for a, b in zip(s, s[1:]):
                    self.assertGreaterEqual(b.start, a.start + a.duration, f"{mode} {dur}")
                for g in s:
                    self.assertGreaterEqual(g.start, 0)
                    self.assertLessEqual(g.start + g.duration, self.f.duration + 0.01)

    def test_honest_when_few_moments(self):
        s, note = hl.suggest(self.f, 60, 15, "balanced")      # 360 s kan geen 15 clips van 60 s bevatten
        self.assertLess(len(s), 15)
        self.assertIn("gevraagd", note)
        self.assertIn("geen AI", note)

    def test_weak_flagged(self):
        s, note = hl.suggest(self.f, 15, 15, "drops")
        self.assertTrue(any(g.weak for g in s))
        self.assertIn("zwak", note)
        self.assertTrue(all("ZWAK" in g.reason for g in s if g.weak))

    def test_avoid_existing_clips(self):
        s, _ = hl.suggest(self.f, 30, 6, "balanced", avoid=[(80, 130)])
        for g in s:
            self.assertFalse(g.start < 130 and g.start + g.duration > 80)

    def test_too_short(self):
        s, note = hl.suggest(self.f, 600, 3, "balanced")
        self.assertEqual(s, [])

    def test_cancel(self):
        ev = threading.Event(); ev.set()
        with self.assertRaises(BaseException) as cm:
            fe.extract(FF, SET, 360, cancel=ev, use_cache=False)
        self.assertTrue(fe.is_cancel(cm.exception))

    def test_cache_roundtrip(self):
        fe.extract(FF, SET, 360, use_cache=True)
        c = fe.load_cached(SET)
        self.assertIsNotNone(c)
        np.testing.assert_allclose(c.data, self.f.data, rtol=1e-4, atol=1e-4)

    def test_no_audio_gives_friendly_error(self):
        with self.assertRaises(ToolError) as cm:
            fe.extract(FF, os.path.join(MEDIA, "G_noaudio.mp4"), 8, use_cache=False)
        self.assertIn("geen audio", str(cm.exception).lower())


class TestTimecodeAndProject(unittest.TestCase):
    def test_timecodes(self):
        self.assertEqual(parse_time("90"), 90)
        self.assertEqual(parse_time("1:30"), 90)
        self.assertEqual(parse_time("01:01:30"), 3690)
        self.assertEqual(parse_time("1:30,5"), 90.5)
        self.assertEqual(format_time(3690), "01:01:30")
        self.assertEqual(format_time(90), "01:30")
        for bad in ("", "x", "1:99", "1:2:3:4", "-5"):
            with self.assertRaises(ValueError):
                parse_time(bad)

    def test_project_roundtrip(self):
        p = Project(out_dir="/tmp/uit")
        s = Source(path="/x/Set – é.mp4", info=probe(SET))
        apply_profile(s.settings, "high")
        s.settings.dj_name = "DJ Ünïcode"
        s.clips.append(Clip(start=10, end=40, label="a", look=Look(zoom=1.5, fx=0.2, mute=True)))
        p.sources.append(s)
        d = tempfile.mkdtemp(); fn = os.path.join(d, "p.accu.json")
        p.save(fn)
        q = Project.load(fn)
        self.assertEqual(q.sources[0].path, s.path)
        self.assertEqual(q.sources[0].settings.crf, 14)
        self.assertEqual(q.sources[0].settings.dj_name, "DJ Ünïcode")
        self.assertEqual(q.sources[0].clips[0].look.zoom, 1.5)
        self.assertTrue(q.sources[0].clips[0].look.mute)
        self.assertEqual(q.sources[0].info.width, 320)

    def test_project_ignores_unknown_fields(self):
        d = tempfile.mkdtemp(); fn = os.path.join(d, "p.json")
        json.dump({"sources": [{"path": "/a.mp4", "clips": [{"start": 1, "end": 2, "toekomst": 1}],
                                "settings": {"crf": 12, "nieuw": 3}}], "extra": 1}, open(fn, "w"))
        q = Project.load(fn)
        self.assertEqual(q.sources[0].settings.crf, 12)


if __name__ == "__main__":
    unittest.main(verbosity=2)
