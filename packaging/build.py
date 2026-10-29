"""Bouwt de zelfstandige app en de installer voor het platform waarop dit script draait.

    python packaging/build.py --tools build/tools --target win-x64|mac-arm64|mac-x64 [--dry-run]

Windows → Radio-ACCU-Clipper-<versie>-Windows-Setup.exe (Inno Setup) + …-Windows-portable.zip
macOS   → Radio-ACCU-Clipper-<versie>-Mac-AppleSilicon.dmg of …-Mac-Intel.dmg

Optioneel op de Mac (anders lokaal 'ad hoc' ondertekend):
    MAC_SIGN_IDENTITY   bijv. "Developer ID Application: Naam (TEAMID)"
    APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD   → notariseren bij Apple
"""
from __future__ import annotations
import argparse
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
APP = "Radio ACCU Clipper"
FILE_STEM = "Radio-ACCU-Clipper"
IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
MIN_MACOS = "15.0"      # de meegeleverde FFmpeg (Homebrew) is voor macOS 15 gebouwd
QT_EXCLUDES = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
               "PySide6.QtWebChannel", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
               "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
               "PySide6.QtBluetooth", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "tkinter"]
DRY = False


def version() -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "accu_clipper" / "__init__.py").read_text("utf-8"))
    return m.group(1)


def run(cmd: list, **kw) -> None:
    print("▶", " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd), flush=True)
    if not DRY:
        subprocess.run([str(c) for c in cmd], check=True, **kw)


# ------------------------------------------------------------------------------------------- iconen
def make_icons() -> Path:
    from PIL import Image
    src = Image.open(ROOT / "packaging" / "icon.png").convert("RGBA")
    BUILD.mkdir(exist_ok=True)
    if IS_MAC:
        iconset = BUILD / "AppIcon.iconset"
        shutil.rmtree(iconset, ignore_errors=True)
        iconset.mkdir()
        for s in (16, 32, 128, 256, 512):
            src.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
            src.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
        icns = BUILD / "AppIcon.icns"
        run(["iconutil", "-c", "icns", iconset, "-o", icns])
        return icns
    ico = BUILD / "AppIcon.ico"
    src.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return ico


# ------------------------------------------------------------------------------------------- app
def pyinstaller(icon: Path, target: str) -> Path:
    sep = os.pathsep            # ';' op Windows, ':' elders
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", APP,
           "--icon", icon, "--add-data", f"{ROOT / 'packaging' / 'icon.png'}{sep}.",
           "--distpath", BUILD / "dist", "--workpath", BUILD / "work", "--specpath", BUILD]
    for m in QT_EXCLUDES:
        cmd += ["--exclude-module", m]
    if IS_MAC:
        cmd += ["--osx-bundle-identifier", "nl.radioaccu.clipper",
                "--target-architecture", "arm64" if target == "mac-arm64" else "x86_64"]
    cmd.append(ROOT / "run.py")
    run(cmd)
    return BUILD / "dist" / (f"{APP}.app" if IS_MAC else APP)


def add_tools(app: Path, tools: Path) -> Path:
    """Hulpprogramma's naast de app zetten: daar zoekt platform_paths.bundled_bin_dirs() ze."""
    dest = app / "Contents" / "Resources" / "bin" if IS_MAC else app / "bin"
    print(f"Hulpprogramma's → {dest}")
    if not DRY:
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(tools, dest, symlinks=True)
        shutil.copy2(ROOT / "packaging" / "THIRD_PARTY_LICENSES.txt", dest / "THIRD_PARTY_LICENSES.txt")
    return dest


# ------------------------------------------------------------------------------------------- macOS
def mac_info_plist(app: Path, ver: str) -> None:
    p = app / "Contents" / "Info.plist"
    if DRY:
        return
    with open(p, "rb") as f:
        info = plistlib.load(f)
    info.update({"CFBundleShortVersionString": ver, "CFBundleVersion": ver, "LSMinimumSystemVersion": MIN_MACOS,
                 "CFBundleDisplayName": APP, "NSHighResolutionCapable": True,
                 "NSHumanReadableCopyright": "Radio ACCU",
                 # .accuclip-projecten openen met een dubbelklik in Finder
                 "CFBundleDocumentTypes": [{"CFBundleTypeName": "Radio ACCU-project",
                                            "CFBundleTypeExtensions": ["accuclip"],
                                            "CFBundleTypeRole": "Editor", "LSHandlerRank": "Owner"}]})
    with open(p, "wb") as f:
        plistlib.dump(info, f)


def mac_sign(app: Path, bindir: Path) -> None:
    ident = os.environ.get("MAC_SIGN_IDENTITY", "").strip()
    if not ident:
        # Lokaal ('ad hoc') ondertekenen: vereist op Apple Silicon. Geen Apple-verificatie (zie README).
        run(["codesign", "--force", "--deep", "--sign", "-", app])
        return
    ent = ROOT / "packaging" / "mac" / "entitlements.plist"
    files = sorted([p for p in bindir.rglob("*") if p.is_file() and p.suffix not in (".txt", ".md")],
                   key=lambda p: -len(p.parts))
    for f in files:          # eerst alles binnenin, dan de app zelf
        run(["codesign", "--force", "--timestamp", "--options", "runtime", "--entitlements", ent, "--sign", ident, f])
    run(["codesign", "--force", "--deep", "--timestamp", "--options", "runtime", "--entitlements", ent,
         "--sign", ident, app])


def mac_dmg(app: Path, out: Path) -> Path:
    stage = BUILD / "dmg"
    if not DRY:
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir()
        shutil.copytree(app, stage / app.name, symlinks=True)
        os.symlink("/Applications", stage / "Applications")
        shutil.copy2(ROOT / "packaging" / "mac" / "Eerst lezen.txt", stage / "Eerst lezen.txt")
        if out.exists():
            out.unlink()
    run(["hdiutil", "create", "-volname", APP, "-srcfolder", stage, "-ov", "-format", "UDZO", out])
    if all(os.environ.get(k) for k in ("MAC_SIGN_IDENTITY", "APPLE_ID", "APPLE_TEAM_ID", "APPLE_APP_PASSWORD")):
        run(["codesign", "--force", "--timestamp", "--sign", os.environ["MAC_SIGN_IDENTITY"], out])
        run(["xcrun", "notarytool", "submit", out, "--apple-id", os.environ["APPLE_ID"],
             "--team-id", os.environ["APPLE_TEAM_ID"], "--password", os.environ["APPLE_APP_PASSWORD"], "--wait"])
        run(["xcrun", "stapler", "staple", out])
    return out


# ------------------------------------------------------------------------------------------- Windows
def find_iscc() -> str:
    for c in [shutil.which("iscc"), shutil.which("ISCC"),
              r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe"]:
        if c and os.path.isfile(c):
            return c
    if DRY:
        return "ISCC.exe"
    raise SystemExit("Inno Setup (ISCC.exe) niet gevonden. Installeer het via https://jrsoftware.org/isdl.php "
                     "of 'choco install innosetup'.")


def win_installer(app: Path, icon: Path, ver: str, outdir: Path) -> list[Path]:
    run([find_iscc(), f"/DAppVersion={ver}", f"/DSourceDir={app}", f"/DOutputDir={outdir}", f"/DIconFile={icon}",
         f"/DOutputName={FILE_STEM}-{ver}-Windows-Setup", ROOT / "packaging" / "windows" / "installer.iss"])
    portable = outdir / f"{FILE_STEM}-{ver}-Windows-portable"
    print(f"Losse zip → {portable}.zip")
    if not DRY:
        shutil.make_archive(str(portable), "zip", root_dir=app.parent, base_dir=app.name)
    return [outdir / f"{FILE_STEM}-{ver}-Windows-Setup.exe", Path(f"{portable}.zip")]


# ------------------------------------------------------------------------------------------- main
def main() -> int:
    global DRY
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", required=True, help="map met ffmpeg, ffprobe, yt-dlp, deno (zie fetch_tools.py)")
    ap.add_argument("--target", required=True, choices=["win-x64", "mac-arm64", "mac-x64"])
    ap.add_argument("--dry-run", action="store_true", help="alleen tonen wat er zou gebeuren")
    a = ap.parse_args()
    DRY = a.dry_run
    if a.target.startswith("win") != IS_WIN and not DRY:
        raise SystemExit("Een Windows-installer kan alleen op Windows gebouwd worden, een Mac-installer alleen op een Mac.")
    ver = version()
    tools = Path(a.tools).resolve()
    outdir = BUILD / "installers"
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"=== {APP} {ver} – {a.target} ===")
    icon = make_icons()
    app = pyinstaller(icon, a.target)
    bindir = add_tools(app, tools)
    if a.target.startswith("mac"):
        mac_info_plist(app, ver)
        mac_sign(app, bindir)
        kind = "AppleSilicon" if a.target == "mac-arm64" else "Intel"
        made = [mac_dmg(app, outdir / f"{FILE_STEM}-{ver}-Mac-{kind}.dmg")]
    else:
        made = win_installer(app, icon, ver, outdir)
    for m in made:
        size = f"{m.stat().st_size / 1e6:.0f} MB" if m.exists() else "(dry-run)"
        print(f"✓ {m.name}  {size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
