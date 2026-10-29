"""Nagebootste yt-dlp voor tests (geen internet nodig). Bootst de CLI-uitvoer na die de app gebruikt.
Gedrag hangt af van de URL:  'playlist' → afspeellijst, 'bot' → botcontrole, 'unsupported' → niet ondersteund,
'fail' → fout halverwege, 'slow' → trage download (voor annuleren). Bronmateriaal: $FAKE_YTDLP_MEDIA/S_synthset.mp4"""
import json
import os
import shutil
import subprocess
import sys
import time

args = sys.argv[1:]
MEDIA = os.environ["FAKE_YTDLP_MEDIA"]
SRC = os.path.join(MEDIA, "S_synthset.mp4")
TITLE = "Live Set – Café 2026 / Ü"


def opt(name, multi=False):
    vals = [args[i + 1] for i, a in enumerate(args[:-1]) if a == name]
    return vals if multi else (vals[-1] if vals else None)


if "--version" in args:
    print("2026.10.01")
    sys.exit(0)
assert "--ignore-config" in args, "app moet --ignore-config meegeven"
assert "--" in args, "URL moet na '--' staan"
url = args[args.index("--") + 1]
vid = "abc123XYZ_-"

if "bot" in url:
    print("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser", file=sys.stderr)
    sys.exit(1)
if "unsupported" in url:
    print(f"ERROR: Unsupported URL: {url}", file=sys.stderr)
    sys.exit(1)

if "-J" in args:
    assert "--flat-playlist" in args
    if "playlist" in url:
        print(json.dumps({"_type": "playlist", "title": "Mijn sets", "entries": [
            {"ie_key": "Youtube", "url": "aaaaaaaaaaa", "title": "Set A", "duration": 360},
            {"ie_key": "Youtube", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb", "title": "Set B"},
            None]}))
    else:
        print(json.dumps({"_type": "video", "id": vid, "title": TITLE, "uploader": "Radio ACCU", "duration": 360,
                          "is_live": False, "extractor_key": "Youtube", "filesize_approx": 5_000_000,
                          "description": "Tracklist:\n00:00 Intro\n01:30 Artiest – Drop één\n03:00 Artiest – Drop twee",
                          "chapters": [{"start_time": 0, "end_time": 90, "title": "Intro"},
                                       {"start_time": 90, "end_time": 360, "title": "Hoofddeel"}]}))
    sys.exit(0)

# ---- downloaden
paths = opt("-P", multi=True)
out_dir = next(p for p in paths if not p.startswith("temp:"))
tmp_dir = next(p[5:] for p in paths if p.startswith("temp:"))
template = opt("-o")
done = args[args.index("--print-to-file") + 2]
assert args[args.index("--print-to-file") + 1] == "after_move:filepath"
assert "--newline" in args and opt("--progress-template", True)
audio = opt("-f") == "ba/b"
ext = "m4a" if audio else (opt("--merge-output-format") or "webm")
name = (template.replace("%(title).100B", TITLE.replace("/", "⧸")).replace("%(id)s", vid)
        .replace("%(ext)s", ext))
os.makedirs(out_dir, exist_ok=True)
os.makedirs(tmp_dir, exist_ok=True)
final = os.path.join(out_dir, name)

if os.path.exists(final):
    print(f"[download] {final} has already been downloaded")
else:
    stages = 1 if audio else 2
    delay = 0.4 if "slow" in url else 0.01
    part = os.path.join(tmp_dir, name + ".part")
    for st in range(stages):
        for pct in range(0, 101, 10):
            print(f"ACCU {pct:5.1f}%|  2.50MiB/s|00:{(100 - pct) // 10:02d}", flush=True)
            with open(part, "ab") as f:
                f.write(b"x" * 1000)
            if "fail" in url and pct == 50:
                print("ERROR: unable to download video data: HTTP Error 403: Forbidden", flush=True)
                sys.exit(1)
            time.sleep(delay)
    print(f'[Merger] Merging formats into "{final}"', flush=True)
    sec = opt("--download-sections")
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if sec:
        a, b = sec.lstrip("*").split("-")
        cmd += ["-ss", a, "-to", b]
    cmd += ["-i", SRC]
    cmd += (["-vn", "-c:a", "aac"] if audio else ["-c", "copy"])
    tmp_out = os.path.join(tmp_dir, "merged." + ext)
    subprocess.run(cmd + [tmp_out], check=True)
    os.remove(part)
    shutil.move(tmp_out, final)
with open(done, "a", encoding="utf-8") as f:
    f.write(final + "\n")
