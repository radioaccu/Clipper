"""Alle verschillen tussen macOS, Windows (en Linux voor tests) op één plek."""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
APP_DIR_NAME = "Radio ACCU Clipper"


def exe(name: str) -> str:
    """'ffmpeg' → 'ffmpeg.exe' op Windows."""
    return name + ".exe" if IS_WIN and not name.lower().endswith(".exe") else name


def app_data_dir() -> Path:
    """Schrijfbare map voor instellingen, sessie en hulpprogramma's (yt-dlp)."""
    if IS_MAC:
        p = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    elif IS_WIN:
        p = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / APP_DIR_NAME
    else:
        p = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "radio-accu-clipper"
    return p


def cache_dir() -> Path:
    if IS_MAC:
        return Path.home() / "Library" / "Caches" / APP_DIR_NAME
    if IS_WIN:
        return app_data_dir() / "Cache"
    return Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "radio-accu-clipper"


def videos_dir() -> Path:
    """~/Movies op de Mac, ~/Videos op Windows."""
    for name in (("Movies",) if IS_MAC else ("Videos", "Movies")):
        p = Path.home() / name
        if p.is_dir():
            return p
    return Path.home() / ("Movies" if IS_MAC else "Videos")


def file_manager_name() -> str:
    return "Finder" if IS_MAC else ("Verkenner" if IS_WIN else "bestandsbeheer")


def bundled_bin_dirs() -> list[Path]:
    """Mappen met meegeleverde programma's (ffmpeg, yt-dlp, deno) in de geïnstalleerde app."""
    dirs: list[Path] = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        dirs += [Path(base) / "bin", Path(base)]
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        dirs += [exe_dir / "bin", exe_dir.parent / "Resources" / "bin", exe_dir.parent / "Frameworks" / "bin"]
    here = Path(__file__).resolve().parent.parent
    dirs += [here / "vendor", here / "bin"]
    return dirs


def system_bin_dirs() -> list[Path]:
    if IS_WIN:
        return []
    return [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/opt/local/bin"), Path("/usr/bin")]


def no_window_kwargs() -> dict:
    """Op Windows opent elke consoleprogramma-aanroep anders een zwart venster."""
    if IS_WIN:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def reinstall_hint() -> str:
    if getattr(sys, "frozen", False):
        return "Installeer de nieuwste versie van de app opnieuw met de installer."
    if IS_MAC:
        return "Dubbelklik opnieuw op Installeer.command."
    return "Installeer yt-dlp en Deno (zie README_NL.md)."
