"""Achtergrondtaken: de interface blijft reageren tijdens analyse en export."""
from __future__ import annotations
import threading
from typing import Callable

from PySide6.QtCore import QThread, Signal

from ..exporter import Cancelled
from ..ffmpeg_tools import ToolError
from .. import features as fe


class Worker(QThread):
    progress = Signal(float, str)   # 0..1, tekst
    done = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn: Callable[["Worker"], object], parent=None):
        super().__init__(parent)
        self._fn = fn
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def report(self, fraction: float, text: str = "") -> None:
        self.progress.emit(float(fraction), text)

    def run(self) -> None:
        try:
            result = self._fn(self)
        except Cancelled:
            self.cancelled.emit()
        except BaseException as e:  # noqa: BLE001
            if fe.is_cancel(e):
                self.cancelled.emit()
            elif isinstance(e, ToolError):
                self.failed.emit(str(e))
            else:
                self.failed.emit(f"Onverwachte fout: {type(e).__name__}: {e}")
        else:
            if self.cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.done.emit(result)
