"""Haalt de hulpprogramma's op die in de installer meegaan: FFmpeg/ffprobe, yt-dlp en Deno.

Gebruik:  python packaging/fetch_tools.py <win-x64|mac-arm64|mac-x64> <doelmap>

Windows: FFmpeg (gedeelde GPL-build van BtbN), yt-dlp.exe, deno.exe — allemaal van GitHub-releases.
macOS:   FFmpeg via Homebrew, met al zijn bibliotheken meegenomen door dylibbundler (vereist Homebrew;
         op de GitHub-bouwmachines is dat aanwezig), plus yt-dlp_macos en Deno van GitHub."""
from __future__ import annotations
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

GH = "https://github.com"
URLS = {
    "ffmpeg-win": f"{GH}/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip",
    "ytdlp-win": f"{GH}/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
    "ytdlp-mac": f"{GH}/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos",
    "deno-win": f"{GH}/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
    "deno-mac-arm64": f"{GH}/denoland/deno/releases/latest/download/deno-aarch64-apple-darwin.zip",
    "deno-mac-x64": f"{GH}/denoland/deno/releases/latest/download/deno-x86_64-apple-darwin.zip",
}


def download(url: str, dest: Path) -> Path:
    print(f"  ↓ {url}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "radio-accu-clipper-build"})
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1024 * 1024)
    return dest


def make_exec(p: Path) -> None:
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def unzip_member(zip_path: Path, member_suffix: str, dest: Path) -> Path:
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.endswith(member_suffix))
        with z.open(name) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
    return dest


def fetch_windows(out: Path, tmp: Path) -> None:
    z = download(URLS["ffmpeg-win"], tmp / "ffmpeg.zip")
    with zipfile.ZipFile(z) as zf:
        for n in zf.namelist():
            base = os.path.basename(n)
            # alleen ffmpeg/ffprobe en hun DLL's; ffplay is niet nodig (scheelt ~25 MB)
            if "/bin/" in n and base and base != "ffplay.exe" and (base.endswith(".dll") or base in ("ffmpeg.exe", "ffprobe.exe")):
                with zf.open(n) as src, open(out / base, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            elif n.endswith("LICENSE.txt") and "/" in n and n.count("/") == 1:
                with zf.open(n) as src, open(out / "FFMPEG-LICENSE.txt", "wb") as dst:
                    shutil.copyfileobj(src, dst)
    download(URLS["ytdlp-win"], out / "yt-dlp.exe")
    unzip_member(download(URLS["deno-win"], tmp / "deno.zip"), "deno.exe", out / "deno.exe")


def fetch_mac(out: Path, tmp: Path, arch: str) -> None:
    brew = shutil.which("brew") or "/opt/homebrew/bin/brew"
    env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_INSTALL_CLEANUP="1")
    subprocess.run([brew, "install", "ffmpeg", "dylibbundler"], check=True, env=env)
    prefix = subprocess.run([brew, "--prefix"], check=True, capture_output=True, text=True).stdout.strip()
    for tool in ("ffmpeg", "ffprobe"):
        dst = out / tool
        shutil.copy2(os.path.realpath(f"{prefix}/bin/{tool}"), dst)
        dst.chmod(0o755)
    # Alle Homebrew-bibliotheken mee naar bin/libs en de verwijzingen daarheen omzetten.
    # -s: zoekpad voor @rpath-bibliotheken; stdin leeg + tijdslimiet zodat een vraag nooit eindeloos blijft wachten
    subprocess.run(["dylibbundler", "-cd", "-of", "-b", "-x", str(out / "ffmpeg"), "-x", str(out / "ffprobe"),
                    "-d", str(out / "libs"), "-p", "@executable_path/libs/", "-s", f"{prefix}/lib"],
                   check=True, stdin=subprocess.DEVNULL, timeout=1200)
    # Na het aanpassen zijn de handtekeningen ongeldig; op Apple Silicon start dan niets meer → opnieuw (ad hoc) tekenen.
    for p in list((out / "libs").glob("*")) + [out / "ffmpeg", out / "ffprobe"]:
        subprocess.run(["codesign", "--force", "--sign", "-", str(p)], check=True)
    download(URLS["ytdlp-mac"], out / "yt-dlp")
    make_exec(out / "yt-dlp")
    unzip_member(download(URLS[f"deno-mac-{arch}"], tmp / "deno.zip"), "deno", out / "deno")
    make_exec(out / "deno")
    lic = Path(prefix) / "opt" / "ffmpeg" / "LICENSE.md"
    if lic.is_file():
        shutil.copy2(lic, out / "FFMPEG-LICENSE.txt")


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("win-x64", "mac-arm64", "mac-x64"):
        print(__doc__)
        return 2
    target, out = sys.argv[1], Path(sys.argv[2]).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    tmp = Path(tempfile.mkdtemp(prefix="accu_tools_"))
    try:
        if target == "win-x64":
            fetch_windows(out, tmp)
        else:
            fetch_mac(out, tmp, target.split("-")[1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"Hulpprogramma's klaar in {out} ({total / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
