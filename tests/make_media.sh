#!/bin/bash
# Genereert de testbronnen in ${1:-/tmp/t}. Vereist ffmpeg met libx264/libx265.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
D="${1:-/tmp/t}"; mkdir -p "$D"; cd "$D"
Q="-v error -y"
ffmpeg $Q -filter_complex "color=red:s=960x1080:r=25:d=8[a];color=blue:s=960x1080:r=25:d=8[b];[a][b]hstack[v];sine=f=440:r=48000:d=8,volume=0.5,aformat=channel_layouts=stereo[a2]" -map "[v]" -map "[a2]" -c:v libx264 -preset ultrafast -crf 23 -pix_fmt yuv420p -c:a aac -b:a 192k A.mp4
ffmpeg $Q -display_rotation 90 -i A.mp4 -c copy B_rot.mp4
ffmpeg $Q -i A.mp4 -vf "scale=1440:1080,setsar=4/3" -c:v libx264 -preset ultrafast -crf 23 -c:a copy C_sar.mp4
ffmpeg $Q -i A.mp4 -vf "scale=960:540,fps=60000/1001" -c:v libx264 -preset ultrafast -crf 23 -c:a copy D_60.mp4
ffmpeg $Q -filter_complex "color=black:s=640x360:r=25:d=10[k];color=white:s=640x360:r=25:d=5[w];[k]trim=0:5[k5];[k5][w]concat=n=2:v=1:a=0[v];anullsrc=r=48000:cl=stereo,atrim=0:5[s];sine=f=1000:r=48000:d=5,aformat=channel_layouts=stereo[t];[s][t]concat=n=2:v=0:a=1[a]" -map "[v]" -map "[a]" -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac E_sync.mp4
ffmpeg $Q -f lavfi -i "testsrc2=s=640x360:r=25:d=4" -f lavfi -i "sine=f=330:d=4" -vf format=yuv420p10le -c:v libx265 -preset ultrafast -x265-params "log-level=error" -color_primaries bt2020 -color_trc smpte2084 -colorspace bt2020nc -c:a aac F_hdr.mp4
ffmpeg $Q -i A.mp4 -an -c:v copy G_noaudio.mp4
ffmpeg $Q -i A.mp4 -af "volume=1.8" -c:v copy -c:a aac -b:a 256k H_loud.mp4
ffmpeg $Q -f lavfi -i "color=gray:s=320x180:r=25:d=6" -f lavfi -i "anoisesrc=d=6:c=pink:r=48000:a=0.4,aformat=channel_layouts=stereo" -c:v libx264 -preset ultrafast -c:a aac -b:a 256k N_noise.mp4
cp A.mp4 "Mijn Set – Café #1 (zondag) [live].mp4"
"${PYTHON:-python3}" "$HERE/make_synth_set.py" "$D/S_synthset.mp4"
printf ';FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=3000\ntitle=Intro\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=3000\nEND=8000\ntitle=Track twee – é\n' > ch.txt
ffmpeg $Q -i A.mp4 -i ch.txt -map 0 -map_metadata 1 -map_chapters 1 -c copy Ch.mp4 && rm ch.txt
echo "Testmedia klaar in $D"
