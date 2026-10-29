"""Startpunt van de desktopapp."""
from __future__ import annotations
import os
import sys

from PySide6.QtCore import QEvent
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .. import APP_NAME
from .mainwindow import MainWindow


def _icon_path() -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(getattr(sys, "_MEIPASS", ""), "icon.png"),
              os.path.join(here, "..", "..", "packaging", "icon.png")):
        if os.path.isfile(p):
            return p
    return None


def _windows_taskbar_identity() -> None:
    """Zonder eigen AppUserModelID toont Windows het Python-icoon in de taakbalk."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RadioACCU.Clipper")
        except Exception:
            pass


class ClipperApp(QApplication):
    """Vangt op de Mac 'open dit bestand' van Finder op (dubbelklik op een project of 'Open met')."""

    def __init__(self, argv):
        super().__init__(argv)
        self.window: MainWindow | None = None
        self.pending: list[str] = []

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.FileOpen:
            path = e.file()
            if self.window is not None:
                self.window.open_paths([path])
            else:
                self.pending.append(path)
            return True
        return super().event(e)


def main() -> int:
    _windows_taskbar_identity()
    app = ClipperApp(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("RadioACCU")
    ip = _icon_path()
    if ip:
        app.setWindowIcon(QIcon(ip))
    win = MainWindow()
    app.window = win
    win.show()
    # bestanden die via "Open met…", een dubbelklik (Windows) of als argument worden meegegeven
    paths = [a for a in sys.argv[1:] if os.path.exists(a)] + app.pending
    if paths:
        win.open_paths(paths)
    return app.exec()
