"""Genereert een synthetische 'DJ-set' (6 min) met bekende structuur voor de analysetests."""
import os, sys, wave, subprocess
import numpy as np

def make(path_wav, sr=44100):
    rng = np.random.default_rng(1)
    T = 360
    t = np.arange(T * sr) / sr
    x = np.zeros_like(t)
    def seg(a, b): return (t >= a) & (t < b)
    # 0-60 rustige pad
    m = seg(0, 60); x[m] += 0.04 * np.sin(2*np.pi*220*t[m]) + 0.01 * rng.standard_normal(m.sum())
    # 60-90 opbouw: ruis + stijgende toon
    m = seg(60, 90); ramp = (t[m]-60)/30
    x[m] += (0.02 + 0.15*ramp) * rng.standard_normal(m.sum()) * 0.5 + 0.1*ramp*np.sin(2*np.pi*(300+600*ramp)*t[m])
    def kick_groove(a, b, amp):
        m = seg(a, b); tt = t[m] - a
        beat = (tt * 2) % 1.0      # 120 bpm
        env = np.exp(-beat * 9)
        kick = amp * env * np.sin(2*np.pi*(50 + 60*np.exp(-beat*30))*tt)
        sub = 0.5*amp*np.sin(2*np.pi*55*tt) * (0.6 + 0.4*np.sin(2*np.pi*0.5*tt))
        hat_env = np.exp(-(((tt*4) % 1.0))*25)
        hat = 0.25*amp*hat_env*rng.standard_normal(m.sum())
        lead = 0.2*amp*np.sin(2*np.pi*880*tt)*(np.sin(2*np.pi*2*tt) > 0)
        x[m] += kick + sub + hat + lead
    kick_groove(90, 150, 0.8)                         # DROP 1
    m = seg(150, 180); x[m] += 0.03*np.sin(2*np.pi*330*t[m])   # breakdown
    kick_groove(180, 240, 0.9)                        # DROP 2
    kick_groove(240, 300, 0.45)                       # rustiger groove
    kick_groove(300, 360, 0.95)                       # high energy
    x = np.clip(x, -1, 1)
    pcm = (x * 32767).astype("<i2")
    with wave.open(path_wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm.tobytes())

if __name__ == "__main__":
    out = sys.argv[1]
    make(out + ".wav")
    subprocess.run([os.environ.get("ACCU_FFMPEG", "ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=320x180:r=5:d=360",
                    "-i", out + ".wav", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "256k",
                    "-shortest", out], check=True)
