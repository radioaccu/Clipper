"""Platformgedrag (Windows/macOS) met nagebootste platformkeuze. Bewijst de logica, niet dat het op Windows draait."""
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from accu_clipper import platform_paths as pp, ffmpeg_tools as ft, downloader as dl, exporter as ex


class TestPaths(unittest.TestCase):
    def test_exe_suffix(self):
        with mock.patch.object(pp, "IS_WIN", True):
            self.assertEqual(pp.exe("ffmpeg"), "ffmpeg.exe")
            self.assertEqual(pp.exe("yt-dlp.EXE"), "yt-dlp.EXE")
        with mock.patch.object(pp, "IS_WIN", False):
            self.assertEqual(pp.exe("ffmpeg"), "ffmpeg")

    def test_windows_dirs(self):
        with mock.patch.object(pp, "IS_WIN", True), mock.patch.object(pp, "IS_MAC", False), \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": "/C/Users/Andy/AppData/Local"}):
            self.assertEqual(pp.app_data_dir(), Path("/C/Users/Andy/AppData/Local/Radio ACCU Clipper"))
            self.assertEqual(pp.cache_dir(), Path("/C/Users/Andy/AppData/Local/Radio ACCU Clipper/Cache"))
            self.assertEqual(pp.file_manager_name(), "Verkenner")
            self.assertEqual(pp.system_bin_dirs(), [])

    def test_mac_dirs(self):
        with mock.patch.object(pp, "IS_WIN", False), mock.patch.object(pp, "IS_MAC", True):
            self.assertEqual(pp.app_data_dir(), Path.home() / "Library" / "Application Support" / "Radio ACCU Clipper")
            self.assertEqual(pp.cache_dir(), Path.home() / "Library" / "Caches" / "Radio ACCU Clipper")
            self.assertEqual(pp.file_manager_name(), "Finder")
            self.assertEqual(pp.videos_dir(), Path.home() / "Movies")

    def test_no_console_window_on_windows(self):
        with mock.patch.object(pp, "IS_WIN", True):
            self.assertEqual(pp.no_window_kwargs(), {"creationflags": 0x08000000})
        with mock.patch.object(pp, "IS_WIN", False):
            self.assertEqual(pp.no_window_kwargs(), {})

    def test_reserved_windows_names(self):
        self.assertEqual(ex.safe_stem("CON.mp4"), "_CON")
        self.assertEqual(ex.safe_stem("nul"), "_nul")
        self.assertEqual(ex.safe_stem("com1.set.mp4"), "_com1.set")
        self.assertEqual(ex.safe_stem("Console set.mp4"), "Console set")
        self.assertEqual(ex.safe_stem('a:b*c?"d<e>f|g.mp4'), "a_b_c__d_e_f_g")

    def test_find_tool_uses_exe_on_windows(self):
        d = Path(tempfile.mkdtemp())
        (d / "ffmpeg.exe").write_bytes(b"MZ")       # op Windows telt geen uitvoerbit
        with mock.patch.object(pp, "IS_WIN", True), mock.patch.object(ft, "IS_WIN", True), \
                mock.patch.object(ft, "bundled_bin_dirs", lambda: [d]), \
                mock.patch.object(ft, "system_bin_dirs", lambda: []), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ACCU_FFMPEG", None)
            self.assertEqual(ft.find_tool("ffmpeg"), str(d / "ffmpeg.exe"))


@unittest.skipIf(sys.platform.startswith("win"), "gebruikt een shellscript als nep-yt-dlp; logica wordt op macOS/Linux getest")
class TestBundledYtdlp(unittest.TestCase):
    def setUp(self):
        self.tools = Path(tempfile.mkdtemp()) / "tools"
        self.bundle = Path(tempfile.mkdtemp())
        exe = self.bundle / "yt-dlp"
        exe.write_text("#!/bin/sh\necho 2026.10.01\n")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        self.patches = [mock.patch.object(dl, "TOOLS_DIR", self.tools),
                        mock.patch.object(dl, "VENV_BIN", self.tools / "bin"),
                        mock.patch.object(dl, "STANDALONE", self.tools / "yt-dlp"),
                        mock.patch.object(dl, "bundled_bin_dirs", lambda: [self.bundle]),
                        mock.patch.object(dl, "system_bin_dirs", lambda: []),
                        mock.patch.dict(os.environ, {"PATH": "/nonexistent"})]
        for p in self.patches:
            p.start()
        os.environ.pop("ACCU_YTDLP", None)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_bundled_copy_is_adopted_and_updatable(self):
        cmd = dl.find_ytdlp()
        self.assertEqual(cmd, [str(self.tools / "yt-dlp")])
        self.assertTrue(os.access(cmd[0], os.X_OK))
        self.assertEqual(dl.update_command(), [str(self.tools / "yt-dlp"), "-U"])
        self.assertEqual(dl.version(), "2026.10.01")
        # tweede keer: de kopie in de gebruikersmap wordt gebruikt
        (self.bundle / "yt-dlp").unlink()
        self.assertEqual(dl.find_ytdlp(), [str(self.tools / "yt-dlp")])

    def test_copy_failure_falls_back_to_bundled(self):
        with mock.patch.object(dl.shutil, "copy2", side_effect=OSError("alleen-lezen")):
            self.assertEqual(dl.find_ytdlp(), [str(self.bundle / "yt-dlp")])
        with mock.patch.object(dl.shutil, "copy2", side_effect=OSError("alleen-lezen")):
            with self.assertRaises(ft.ToolError):
                dl.update_command()      # bijwerken kan dan niet: nette melding i.p.v. een crash

    def test_not_found_message(self):
        (self.bundle / "yt-dlp").unlink()
        with mock.patch.object(dl.shutil, "which", return_value=None), \
                mock.patch.dict(sys.modules, {"yt_dlp": None}):
            with self.assertRaises(ft.ToolError) as cm:
                dl.find_ytdlp()
        self.assertIn("Lokale video's werken gewoon", str(cm.exception))


class TestBrowserCookieErrors(unittest.TestCase):
    def test_windows_chrome_cookie_error(self):
        msg = dl.friendly_error("ERROR: Could not copy Chrome cookie database. See …")
        self.assertIn("Firefox", msg)
        self.assertIn("Firefox", dl.friendly_error("ERROR: Failed to decrypt with DPAPI"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
