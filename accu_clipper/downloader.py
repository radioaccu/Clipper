"""Links (YouTube, SoundCloud, Mixcloud, Vimeo, Twitch, Dropbox, directe bestandslinks, …) downloaden met yt-dlp.

yt-dlp is een los, gratis programma. De app gebruikt het als extern hulpmiddel, net als FFmpeg.
Download alleen materiaal waarvoor je de rechten of toestemming hebt."""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .exporter import Cancelled
from .ffmpeg_tools import ToolError
from .platform_paths import (IS_MAC, IS_WIN, app_data_dir, bundled_bin_dirs, exe, no_window_kwargs,
                             reinstall_hint, system_bin_dirs)

TOOLS_DIR = app_data_dir() / "tools"
# Mac-route via Installeer.command: een Python-omgeving met yt-dlp[default] (incl. yt-dlp-ejs) in TOOLS_DIR.
# Installer-route (Mac en Windows): een losse yt-dlp in de app; bij eerste gebruik gekopieerd naar TOOLS_DIR,
# zodat hij daar zichzelf kan bijwerken (de programmamap zelf is vaak niet schrijfbaar).
TOOLS_VENV = TOOLS_DIR
VENV_BIN = TOOLS_VENV / ("Scripts" if IS_WIN else "bin")
STANDALONE = TOOLS_DIR / exe("yt-dlp")

QUALITIES: dict[str, tuple[str, list[str]]] = {
    "best": ("Beste beschikbare kwaliteit (tot 4K)",
             ["-f", "bv*+ba/b", "-S", "res:2160", "--merge-output-format", "mkv"]),
    "1440": ("Tot 1440p", ["-f", "bv*+ba/b", "-S", "res:1440", "--merge-output-format", "mkv"]),
    "1080": ("Tot 1080p", ["-f", "bv*+ba/b", "-S", "res:1080", "--merge-output-format", "mkv"]),
    "compat": ("Compatibel: H.264 + AAC, tot 1080p (.mp4)",
               ["-f", "bv*+ba/b", "-S", "vcodec:h264,res:1080,acodec:aac", "--merge-output-format", "mp4"]),
    "audio": ("Alleen audio (beste kwaliteit, geen omzetting)", ["-f", "ba/b"]),
}
BROWSERS = {"": "Geen (standaard)", **({"safari": "Safari"} if IS_MAC else {}), "firefox": "Firefox",
            "chrome": "Chrome", "edge": "Edge", "brave": "Brave"}


@dataclass
class LinkInfo:
    url: str
    title: str = ""
    uploader: str = ""
    duration: float = 0.0
    is_live: bool = False
    is_playlist: bool = False
    entries: list = field(default_factory=list)      # [{"url", "title", "duration"}]
    chapters: list = field(default_factory=list)     # [{"start", "end", "title"}]
    description: str = ""
    filesize: int = 0                                # schatting in bytes (0 = onbekend)
    extractor: str = ""


# ------------------------------------------------------------------------------------------- vinden
def _extra_path() -> list[str]:
    dirs = [str(VENV_BIN), str(TOOLS_DIR)] + [str(d) for d in bundled_bin_dirs() + system_bin_dirs()]
    dirs.append(str(Path.home() / ".deno" / "bin"))
    try:
        from .ffmpeg_tools import find_tool
        dirs.insert(0, os.path.dirname(find_tool("ffmpeg")))
    except ToolError:
        pass
    return dirs


def tool_env() -> dict:
    """Een vanuit Finder/Start gestarte app heeft een minimaal PATH; zonder deze aanvulling vindt yt-dlp Deno/FFmpeg niet."""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(_extra_path() + [env.get("PATH", "" if IS_WIN else "/usr/bin:/bin")])
    env["NO_COLOR"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _runnable(p: Path) -> bool:
    return p.is_file() and (IS_WIN or os.access(p, os.X_OK))


def _adopt_bundled(p: Path) -> Path:
    """Kopieert de meegeleverde yt-dlp naar de schrijfbare gebruikersmap (eenmalig)."""
    try:
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STANDALONE.with_name(STANDALONE.name + ".nieuw")
        shutil.copy2(p, tmp)
        if not IS_WIN:
            os.chmod(tmp, 0o755)
        os.replace(tmp, STANDALONE)
        return STANDALONE
    except OSError:
        return p          # kopiëren mislukt: dan de meegeleverde direct gebruiken (bijwerken kan dan niet)


def find_ytdlp() -> list[str]:
    """Geeft de opdracht (als lijst) om yt-dlp te starten, of gooit een begrijpelijke ToolError."""
    env = os.environ.get("ACCU_YTDLP")          # handig voor tests: pad naar programma of .py-script
    if env and os.path.isfile(env):
        return [sys.executable, env] if env.endswith(".py") else [env]
    name = exe("yt-dlp")
    if _runnable(VENV_BIN / name):
        return [str(VENV_BIN / name)]
    if _runnable(STANDALONE):
        return [str(STANDALONE)]
    for d in bundled_bin_dirs():
        if _runnable(d / name):
            return [str(_adopt_bundled(d / name))]
    here = Path(__file__).resolve().parent.parent
    for d in [here / ".venv" / ("Scripts" if IS_WIN else "bin"), *system_bin_dirs()]:
        if _runnable(d / name):
            return [str(d / name)]
    w = shutil.which("yt-dlp")
    if w:
        return [w]
    if not getattr(sys, "frozen", False):
        try:
            import yt_dlp  # noqa: F401
            return [sys.executable, "-m", "yt_dlp"]
        except ImportError:
            pass
    raise ToolError("yt-dlp is niet gevonden, dus links kunnen niet worden gedownload.\n"
                    f"{reinstall_hint()} Lokale video's werken gewoon.")


def deno_available() -> bool:
    return shutil.which("deno", path=tool_env()["PATH"]) is not None


def version() -> str:
    cp = subprocess.run(find_ytdlp() + ["--version"], capture_output=True, text=True, env=tool_env(), timeout=60,
                        encoding="utf-8", errors="replace", **no_window_kwargs())
    return cp.stdout.strip() or "?"


def update_command() -> list[str]:
    py = VENV_BIN / exe("python")
    if py.is_file():
        return [str(py), "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", "yt-dlp[default]"]
    cmd = find_ytdlp()
    if len(cmd) == 1 and os.path.abspath(cmd[0]) == os.path.abspath(STANDALONE):
        return cmd + ["-U"]          # losse yt-dlp in de gebruikersmap kan zichzelf bijwerken
    raise ToolError("Deze yt-dlp-installatie kan niet vanuit de app worden bijgewerkt. " + reinstall_hint())


def update() -> str:
    cp = subprocess.run(update_command(), capture_output=True, text=True, env=tool_env(), timeout=600,
                        encoding="utf-8", errors="replace", **no_window_kwargs())
    if cp.returncode != 0:
        raise ToolError("Bijwerken van yt-dlp mislukt:\n" + (cp.stderr or cp.stdout)[-400:])
    return version()


# ------------------------------------------------------------------------------------------- hulpfuncties
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


def extract_urls(text: str) -> list[str]:
    seen, out = set(), []
    for m in URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(").,;]")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _common(cookies: str) -> list[str]:
    args = ["--ignore-config", "--no-playlist", "--no-mtime"]
    if cookies:
        args += ["--cookies-from-browser", cookies]
    try:
        from .ffmpeg_tools import find_tool
        args += ["--ffmpeg-location", os.path.dirname(find_tool("ffmpeg"))]
    except ToolError:
        pass
    return args


def friendly_error(text: str) -> str:
    t = text.lower()
    errs = [l.strip() for l in text.splitlines() if l.strip().startswith("ERROR")]
    last = errs[-1] if errs else (text.strip().splitlines() or ["onbekende fout"])[-1]
    if "sign in to confirm" in t or "not a bot" in t:
        return ("YouTube vraagt om te bevestigen dat je geen bot bent. Kies bij 'Cookies uit browser' de browser "
                "waarin je bij YouTube bent ingelogd en probeer opnieuw.")
    if "cookies" in t and ("operation not permitted" in t or "permission" in t) and "safari" in t:
        return ("Safari-cookies kunnen niet worden gelezen. Geef de app 'Volledige schijftoegang' in "
                "Systeeminstellingen → Privacy en beveiliging, of kies Chrome/Firefox.")
    if ("could not copy" in t and "cookie" in t) or "dpapi" in t or "failed to decrypt" in t:
        return ("De cookies van deze browser konden niet worden gelezen. Sluit de browser helemaal af en probeer "
                "opnieuw, of kies Firefox.")
    if "private video" in t or "this video is private" in t:
        return "Deze video is privé. Gebruik cookies van een browser met een account dat toegang heeft."
    if "video unavailable" in t or ("not available" in t and "format" not in t):
        return "Deze video is niet (meer) beschikbaar of niet beschikbaar in jouw land."
    if "requested format is not available" in t:
        return "De gekozen kwaliteit is niet beschikbaar. Kies 'Beste beschikbare kwaliteit'."
    if "javascript runtime" in t or "js runtime" in t or ("ejs" in t and "challenge" in t):
        return ("yt-dlp mist een JavaScript-runtime (Deno), die YouTube tegenwoordig vereist. " + reinstall_hint())
    if "http error 403" in t or "unable to extract" in t or "signature" in t:
        return ("De site weigerde de download (vaak omdat yt-dlp verouderd is). Klik op 'yt-dlp bijwerken' en "
                "probeer opnieuw.")
    if "unsupported url" in t:
        return "Deze link wordt niet ondersteund. Plak de link van de videopagina zelf."
    if "no space left" in t:
        return "De schijf is vol. Kies een andere downloadmap of maak ruimte vrij."
    if "is live" in t or "live event" in t:
        return "Livestreams kunnen pas worden gedownload als de uitzending is afgelopen."
    return "Downloaden mislukt:\n" + last


# ------------------------------------------------------------------------------------------- info
def fetch_info(url: str, cookies: str = "", timeout: float = 120) -> LinkInfo:
    cmd = find_ytdlp() + _common(cookies)
    cmd.remove("--no-playlist")
    cmd += ["--flat-playlist", "-J", "--", url]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, env=tool_env(), timeout=timeout,
                            encoding="utf-8", errors="replace", **no_window_kwargs())
    except subprocess.TimeoutExpired:
        raise ToolError("Het ophalen van de gegevens duurde te lang. Controleer je internetverbinding.")
    if cp.returncode != 0 or not cp.stdout.strip():
        raise ToolError(friendly_error(cp.stderr or cp.stdout))
    try:
        d = json.loads(cp.stdout)
    except json.JSONDecodeError:
        raise ToolError("yt-dlp gaf onleesbare gegevens terug.")
    info = LinkInfo(url=url, title=d.get("title") or "", uploader=d.get("uploader") or d.get("channel") or "",
                    duration=float(d.get("duration") or 0), is_live=bool(d.get("is_live")),
                    description=d.get("description") or "", extractor=d.get("extractor_key") or "",
                    filesize=int(d.get("filesize") or d.get("filesize_approx") or 0))
    if d.get("_type") == "playlist":
        info.is_playlist = True
        for e in d.get("entries") or []:
            if not e:
                continue
            u = e.get("url") or e.get("webpage_url") or ""
            if u and not u.startswith("http") and e.get("ie_key") == "Youtube":
                u = f"https://www.youtube.com/watch?v={u}"
            if u:
                info.entries.append({"url": u, "title": e.get("title") or u, "duration": float(e.get("duration") or 0)})
    for ch in d.get("chapters") or []:
        try:
            info.chapters.append({"start": float(ch["start_time"]), "end": float(ch["end_time"]),
                                  "title": ch.get("title") or ""})
        except (KeyError, TypeError, ValueError):
            continue
    return info


# ------------------------------------------------------------------------------------------- downloaden
class DownloadCancelled(Cancelled):
    """Subklasse van de export-annulering, zodat achtergrondtaken dit als 'geannuleerd' tonen."""


_PROG = re.compile(r"^ACCU\s+([\d.]+)%\|([^|]*)\|([^|]*)$")


def build_download_command(url: str, out_dir: str, tmp_dir: str, done_file: str, quality: str = "best",
                           cookies: str = "", section: tuple[float, float] | None = None) -> list[str]:
    if quality not in QUALITIES:
        raise ValueError(f"Onbekende kwaliteit: {quality}")
    cmd = find_ytdlp() + _common(cookies) + list(QUALITIES[quality][1])
    if section:
        a, b = section
        if b <= a:
            raise ToolError("Het einde van het fragment moet na het begin liggen.")
        cmd += ["--download-sections", f"*{a:.3f}-{b:.3f}"]
    cmd += ["-P", out_dir, "-P", f"temp:{tmp_dir}",
            "-o", "%(title).100B [%(id)s]" + (" (fragment)" if section else "") + ".%(ext)s",
            "--newline", "--progress",
            "--progress-template", "download:ACCU %(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
            "--print-to-file", "after_move:filepath", done_file,
            "--", url]
    return cmd


def download(url: str, out_dir: str, quality: str = "best", cookies: str = "",
             section: tuple[float, float] | None = None, expected_bytes: int = 0,
             progress: Callable[[float, str], None] | None = None,
             cancel: threading.Event | None = None) -> str:
    """Downloadt één link naar out_dir. Geeft het pad van het eindbestand terug."""
    os.makedirs(out_dir, exist_ok=True)
    free = shutil.disk_usage(out_dir).free
    need = int(expected_bytes * 1.15) + 500 * 1024 * 1024
    if free < need:
        raise ToolError(f"Te weinig schijfruimte in {out_dir}: {free/1e9:.1f} GB vrij, ongeveer "
                        f"{need/1e9:.1f} GB nodig.")
    tmp = tempfile.mkdtemp(prefix="accu_dl_")
    done_file = os.path.join(tmp, "klaar.txt")
    cmd = build_download_command(url, out_dir, os.path.join(tmp, "parts"), done_file, quality, cookies, section)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=tool_env(),
                            encoding="utf-8", errors="replace", bufsize=1, **no_window_kwargs())
    tail: deque[str] = deque(maxlen=80)
    stage, last_pct = 1, 0.0
    stop = threading.Event()

    def watch():
        while not stop.is_set() and proc.poll() is None:
            if cancel is not None and cancel.wait(0.2):
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                return
            if cancel is None:
                stop.wait(0.2)
    threading.Thread(target=watch, daemon=True).start()
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip()
            m = _PROG.match(line.strip())
            if m:
                pct = float(m.group(1))
                if pct + 20 < last_pct:          # nieuw deel (bijv. eerst beeld, dan geluid)
                    stage += 1
                last_pct = pct
                speed, eta = m.group(2).strip(), m.group(3).strip()
                txt = f"Downloaden{' (deel ' + str(stage) + ')' if stage > 1 else ''}: {pct:.0f}%"
                if speed and speed not in ("Unknown", "NA", "N/A"):
                    txt += f" · {speed}"
                if eta and eta not in ("Unknown", "NA", "N/A"):
                    txt += f" · nog {eta}"
                if progress:
                    progress(min(0.99, pct / 100.0), txt)
            elif line:
                tail.append(line)
                if progress and ("[Merger]" in line or "[ExtractAudio]" in line or "[FixupM3u8]" in line):
                    progress(0.99, "Samenvoegen van beeld en geluid…")
        proc.wait()
    finally:
        stop.set()
        try:
            proc.stdout.close()  # type: ignore[union-attr]
        except Exception:
            pass
        if proc.poll() is None:
            proc.kill()
    try:
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled()
        if proc.returncode != 0:
            raise ToolError(friendly_error("\n".join(tail)))
        try:
            with open(done_file, encoding="utf-8") as f:
                paths = [p.strip() for p in f if p.strip()]
        except OSError:
            paths = []
        final = paths[-1] if paths else ""
        if not final or not os.path.isfile(final):
            raise ToolError("De download leek te lukken, maar het bestand is niet gevonden.\n" + "\n".join(list(tail)[-3:]))
        return os.path.abspath(final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)   # halve downloads (.part) staan hier en worden altijd opgeruimd
