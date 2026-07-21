# -*- coding: utf-8 -*-
"""Анализ вокала: длительность, тональность (key), темп (BPM), активные участки."""
import numpy as np
import soundfile as sf

x, sr = sf.read("vocal_src.wav")
if x.ndim > 1:
    x = x.mean(axis=1)
x = x.astype(np.float64)
dur = len(x) / sr
print(f"sr={sr}  duration={dur:.2f}s  samples={len(x)}")
peak = np.max(np.abs(x))
rms = np.sqrt(np.mean(x**2))
print(f"peak={peak:.4f}  rms={rms:.4f}  ({20*np.log10(rms+1e-9):.1f} dBFS)")

# ---------- STFT ----------
def stft(sig, n_fft=4096, hop=1024):
    win = np.hanning(n_fft)
    n = 1 + (len(sig) - n_fft) // hop
    S = np.empty((n_fft // 2 + 1, n), dtype=np.complex128)
    for i in range(n):
        frame = sig[i*hop:i*hop+n_fft] * win
        S[:, i] = np.fft.rfft(frame)
    return S, hop

S, hop = stft(x)
mag = np.abs(S)
freqs = np.fft.rfftfreq(4096, 1/sr)

# ---------- Тональность через хрому ----------
# энергия по нотам midi 24..96
chroma = np.zeros(12)
for b, f in enumerate(freqs):
    if f < 55 or f > 2000:
        continue
    midi = 69 + 12*np.log2(f/440.0)
    pc = int(round(midi)) % 12
    chroma[pc] += mag[b, :].sum()
chroma = chroma / (chroma.sum() + 1e-9)
names = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']

# профили Крумхансля для major/minor
maj = np.array([6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88])
minr= np.array([6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17])
maj=(maj-maj.mean())/maj.std(); minr=(minr-minr.mean())/minr.std()
c=(chroma-chroma.mean())/(chroma.std()+1e-9)
best=None
for tonic in range(12):
    for prof,mode in ((maj,'major'),(minr,'minor')):
        score=np.corrcoef(c, np.roll(prof,tonic))[0,1]
        if best is None or score>best[0]:
            best=(score,names[tonic],mode)
print(f"KEY guess: {best[1]} {best[2]}  (corr={best[0]:.3f})")
top3=np.argsort(chroma)[::-1][:4]
print("Top pitch classes:", [(names[i], round(float(chroma[i]),3)) for i in top3])

# ---------- Оценка средней высоты голоса (для гармоний) ----------
# грубый pitch по спектральному пику в 80-400 Гц на громких кадрах
frame_e = mag.sum(axis=0)
loud = frame_e > np.percentile(frame_e, 60)
lowband = (freqs>=80)&(freqs<=400)
pitches=[]
for i in np.where(loud)[0]:
    seg=mag[lowband,i]
    if seg.max()>0:
        pitches.append(freqs[lowband][np.argmax(seg)])
if pitches:
    med=np.median(pitches)
    midi=69+12*np.log2(med/440)
    print(f"Median vocal pitch ~ {med:.1f} Hz (midi {midi:.1f}, {names[int(round(midi))%12]})")

# ---------- BPM через автокорреляцию огибающей ----------
env = frame_e.copy()
env = np.maximum(0, np.diff(env))          # onset (положительная разность)
env = env - env.mean()
fps = sr / hop
ac = np.correlate(env, env, 'full')[len(env)-1:]
best_bpm=None
for bpm in np.arange(60, 180, 0.5):
    lag = fps * 60.0 / bpm
    l = int(round(lag))
    if 1 <= l < len(ac):
        if best_bpm is None or ac[l] > best_bpm[1]:
            best_bpm=(bpm, ac[l])
print(f"BPM guess: {best_bpm[0]:.1f}")

# ---------- Карта активности (где поёт) ----------
win=int(0.1*sr)
env2=np.array([np.sqrt(np.mean(x[i:i+win]**2)) for i in range(0,len(x)-win,win)])
thr=env2.max()*0.06
active=env2>thr
# сегменты
segs=[]; s=None
for i,a in enumerate(active):
    t=i*0.1
    if a and s is None: s=t
    if not a and s is not None: segs.append((s,t)); s=None
if s is not None: segs.append((s, len(active)*0.1))
print("Voiced segments (s):", [(round(a,1),round(b,1)) for a,b in segs][:20])
print(f"n_segments={len(segs)}  voiced_ratio={active.mean():.2f}")
