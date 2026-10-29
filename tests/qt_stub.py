"""Minimale nep-PySide6 voor rooktests ZONDER Qt. Valideert alleen onze eigen logica (geen Qt-compatibiliteit!).
QThread.start() draait inline, zodat achtergrondtaken (met echt FFmpeg) direct doorlopen."""
import sys, types


class _Any:
    _cache: dict
    def __init__(self, *a, **k): self._cache = {}; self._cb = []
    def __getattr__(self, n):
        if n.startswith("__"): raise AttributeError(n)
        c = self.__dict__.setdefault("_cache", {})
        if n not in c: c[n] = _Any()
        return c[n]
    def __call__(self, *a, **k): return _Any()
    def __or__(self, o): return self
    __ror__ = __and__ = __rand__ = __or__
    def __iter__(self): return iter(())
    def __bool__(self): return True
    def __int__(self): return 0
    def __float__(self): return 0.0
    def __mul__(self, o): return self
    __rmul__ = __truediv__ = __add__ = __radd__ = __sub__ = __mul__
    def connect(self, f): self.__dict__.setdefault("_cb", []).append(f)
    def emit(self, *a):
        for f in list(self.__dict__.get("_cb", [])): f(*a)


class _Meta(type):
    def __getattr__(cls, n):
        if n.startswith("__"): raise AttributeError(n)
        v = _Any(); type.__setattr__(cls, n, v); return v


class Signal:
    def __init__(self, *a): pass
    def __set_name__(self, owner, name): self.name = "_sig_" + name
    def __get__(self, inst, owner):
        if inst is None: return self
        return inst.__dict__.setdefault(self.name, _Any())


class Base(metaclass=_Meta):
    def __init__(self, *a, **k): pass
    def __getattr__(self, n):
        if n.startswith("__"): raise AttributeError(n)
        v = _Any(); self.__dict__[n] = v; return v


class QThread(Base):
    def start(self): self.run(); self.finished.emit()


ALL_SIGNALS = {"valueChanged", "toggled", "currentIndexChanged", "textChanged"}


class _Strict:
    """Echte Qt-widgets hebben alleen hun eigen signalen; onbekende signaalnamen geven AttributeError."""
    allowed: set = set()
    def __getattr__(self, n):
        if n in ALL_SIGNALS and n not in self.allowed:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{n}'")
        if n.startswith("__"): raise AttributeError(n)
        v = _Any(); self.__dict__[n] = v; return v


class QComboBox(_Strict, Base):
    allowed = {"currentIndexChanged"}
    def __init__(self, *a, **k): self.items = []; self.idx = -1; self.currentIndexChanged = _Any()
    def addItem(self, text, data=None):
        self.items.append((text, data))
        if self.idx < 0: self.idx = 0
    def findData(self, d):
        for i, (_, x) in enumerate(self.items):
            if x == d: return i
        return -1
    def setCurrentIndex(self, i): self.idx = i
    def currentIndex(self): return self.idx
    def currentData(self): return self.items[self.idx][1] if 0 <= self.idx < len(self.items) else None
    def blockSignals(self, b): pass


class _Value(_Strict, Base):
    allowed = ALL_SIGNALS
    def __init__(self, *a, **k): self.v = 0; self.valueChanged = _Any(); self.toggled = _Any(); self.textChanged = _Any()
    def value(self): return self.v
    def setValue(self, v): self.v = v
    def isChecked(self): return bool(self.v)
    def setChecked(self, b): self.v = b
    def text(self): return self.v if isinstance(self.v, str) else ""
    def setText(self, t): self.v = t
    def clear(self): self.v = ""
    def blockSignals(self, b): pass
    def setEnabled(self, b): self.enabled = b


class QMediaPlayer(Base):
    class PlaybackState: PlayingState = "playing"; PausedState = "paused"
    def __init__(self, *a): self.p = 0
    def position(self): return self.p
    def setPosition(self, ms): self.p = ms
    def playbackState(self): return "paused"
    def pause(self): pass
    def stop(self): pass
    def setSource(self, u): pass


class QMessageBox(Base):
    log = []
    class StandardButton: Yes = "yes"; No = "no"
    @staticmethod
    def question(*a, **k): return "yes"
    @staticmethod
    def information(p, t, m, *a): QMessageBox.log.append(("info", m))
    warning = critical = information


class QFileDialog(Base):
    @staticmethod
    def getOpenFileNames(*a, **k): return ([], "")


class QSettings(Base):
    def value(self, k, default=None, **kw): return default
    def setValue(self, k, v): pass


class _Clipboard:
    def text(self): return ""


class QApplication(Base):
    @staticmethod
    def clipboard(): return _Clipboard()


class QTimer(Base):
    @staticmethod
    def singleShot(ms, fn): pass


class QListWidget(Base):
    def __init__(self, *a): self.rows = []; self.row = -1; self.currentRowChanged = _Any()
    def addItem(self, it): self.rows.append(it)
    def item(self, i): return self.rows[i]
    def takeItem(self, i): self.rows.pop(i)
    def currentRow(self): return self.row
    def setCurrentRow(self, i): self.row = i; self.currentRowChanged.emit(i)
    def clear(self): self.rows = []; self.row = -1
    def blockSignals(self, b): pass


class QPixmap(Base):
    def isNull(self): return False


class _Spin(_Value): allowed = {"valueChanged"}
class _IntSpin(_Spin):
    """Echte QSpinBox.setValue accepteert alleen int (PySide6 geeft TypeError bij float)."""
    def setValue(self, v):
        if not isinstance(v, int) or isinstance(v, bool):
            raise TypeError(f"QSpinBox.setValue(): verwacht int, kreeg {type(v).__name__}")
        self.v = v


class _Check(_Value): allowed = {"toggled"}
class _Line(_Value): allowed = {"textChanged"}


def install():
    mods = {}
    special = {"QThread": QThread, "QComboBox": QComboBox, "QSpinBox": _IntSpin, "QDoubleSpinBox": _Spin,
               "QCheckBox": _Check, "QLineEdit": _Line, "QMediaPlayer": QMediaPlayer, "QMessageBox": QMessageBox,
               "QFileDialog": QFileDialog, "QSettings": QSettings, "QTimer": QTimer, "Signal": Signal, "QPixmap": QPixmap, "QListWidget": QListWidget, "QApplication": QApplication}
    for name in ("QtCore", "QtGui", "QtWidgets", "QtMultimedia", "QtMultimediaWidgets"):
        m = types.ModuleType(f"PySide6.{name}")
        def ga(n, _m=m):
            if n.startswith("__"): raise AttributeError(n)
            cls = special.get(n) or _Meta(n, (Base,), {})
            setattr(_m, n, cls); return cls
        m.__getattr__ = ga
        mods[f"PySide6.{name}"] = m
    root = types.ModuleType("PySide6"); root.__path__ = []
    sys.modules["PySide6"] = root
    sys.modules.update(mods)
    for k, v in mods.items(): setattr(root, k.split(".")[1], v)
