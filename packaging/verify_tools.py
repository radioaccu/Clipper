"""Controleert dat de meegeleverde hulpprogramma's echt werken. De bouw stopt als iets ontbreekt.

Gebruik:  python packaging/verify_tools.py <map met ffmpeg, ffprobe, yt-dlp, deno>"""
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


def run(cmd, **kw):
    cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, **kw)
    if cp.returncode != 0:
        raise SystemExit(f"MISLUKT: {' '.join(map(str, cmd))}\n{cp.stderr[-1500:]}")
    return cp.stdout + cp.stderr


def main() -> None:
    d = Path(sys.argv[1]).resolve()
    x = (lambda n: d / (n + ".exe")) if IS_WIN else (lambda n: d / n)
    ff = x("ffmpeg")
    encoders = run([ff, "-hide_banner", "-encoders"])
    filters = run([ff, "-hide_banner", "-filters"])
    need_enc = ["libx264", "libx265", "aac"] + (["h264_videotoolbox", "aac_at"] if IS_MAC else [])
    need_flt = ["zscale", "tonemap", "showwaves", "alimiter", "loudnorm", "colorchannelmixer", "overlay", "fade"]
    missing = [e for e in need_enc if f" {e} " not in encoders] + [f for f in need_flt if f" {f} " not in filters]
    if missing:
        raise SystemExit(f"MISLUKT: deze FFmpeg mist: {', '.join(missing)}")
    # echt coderen, inclusief HDR-omzetting, zodat ontbrekende bibliotheken hier al opvallen
    run([ff, "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=1", "-f", "lavfi",
         "-i", "sine=d=1", "-vf", "format=yuv420p10le,"
         "setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc:range=tv,"   # nagebootst HDR-beeld
         "zscale=tin=smpte2084:min=bt2020nc:pin=bt2020:rin=tv:t=linear:npl=100,"
         "format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p",
         "-c:v", "libx264", "-c:a", "aac", "-f", "null", "-"])
    run([x("ffprobe"), "-version"])
    print("yt-dlp", run([x("yt-dlp"), "--version"]).strip())
    print(run([x("deno"), "--version"]).splitlines()[0])
    print("Alle hulpprogramma's werken:", ", ".join(need_enc + need_flt))


if __name__ == "__main__":
    main()
