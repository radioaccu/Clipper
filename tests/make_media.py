"""Maakt het testmateriaal (vervangt make_media.sh; werkt ook op Windows zonder bash).

    python tests/make_media.py <map>
Gebruikt ffmpeg uit $ACCU_FFMPEG of uit het PATH."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FF = os.environ.get("ACCU_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def ff(*args):
    subprocess.run([FF, "-v", "error", "-y", *args], check=True)


def main(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    os.chdir(d)
    ff("-filter_complex", "color=red:s=960x1080:r=25:d=8[a];color=blue:s=960x1080:r=25:d=8[b];[a][b]hstack[v];"
       "sine=f=440:r=48000:d=8,volume=0.5,aformat=channel_layouts=stereo[a2]", "-map", "[v]", "-map", "[a2]",
       "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "A.mp4")
    ff("-display_rotation", "90", "-i", "A.mp4", "-c", "copy", "B_rot.mp4")
    ff("-i", "A.mp4", "-vf", "scale=1440:1080,setsar=4/3", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
       "-c:a", "copy", "C_sar.mp4")
    ff("-i", "A.mp4", "-vf", "scale=960:540,fps=60000/1001", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
       "-c:a", "copy", "D_60.mp4")
    ff("-filter_complex", "color=black:s=640x360:r=25:d=10[k];color=white:s=640x360:r=25:d=5[w];[k]trim=0:5[k5];"
       "[k5][w]concat=n=2:v=1:a=0[v];anullsrc=r=48000:cl=stereo,atrim=0:5[s];"
       "sine=f=1000:r=48000:d=5,aformat=channel_layouts=stereo[t];[s][t]concat=n=2:v=0:a=1[a]",
       "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
       "E_sync.mp4")
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=4", "-f", "lavfi", "-i", "sine=f=330:d=4",
       "-vf", "format=yuv420p10le", "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error",
       "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc", "-c:a", "aac", "F_hdr.mp4")
    ff("-i", "A.mp4", "-an", "-c:v", "copy", "G_noaudio.mp4")
    ff("-i", "A.mp4", "-af", "volume=1.8", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "H_loud.mp4")
    ff("-f", "lavfi", "-i", "color=gray:s=320x180:r=25:d=6", "-f", "lavfi",
       "-i", "anoisesrc=d=6:c=pink:r=48000:a=0.4,aformat=channel_layouts=stereo",
       "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "256k", "N_noise.mp4")
    shutil.copy2("A.mp4", "Mijn Set – Café #1 (zondag) [live].mp4")
    env = dict(os.environ, ACCU_FFMPEG=FF)
    subprocess.run([sys.executable, str(HERE / "make_synth_set.py"), str(d / "S_synthset.mp4")], check=True, env=env)
    Path("ch.txt").write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=3000\ntitle=Intro\n"
                              "[CHAPTER]\nTIMEBASE=1/1000\nSTART=3000\nEND=8000\ntitle=Track twee – é\n", "utf-8")
    ff("-i", "A.mp4", "-i", "ch.txt", "-map", "0", "-map_metadata", "1", "-map_chapters", "1", "-c", "copy", "Ch.mp4")
    os.remove("ch.txt")
    print(f"Testmedia klaar in {d}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/t").resolve())
