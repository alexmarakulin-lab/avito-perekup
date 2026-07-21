# -*- coding: utf-8 -*-
"""
Настоящий автотюн (TD-PSOLA) монофонического вокала.
Детекция F0 -> снап к гамме F minor -> пич-синхронный ресинтез (форманты сохраняются).
Вход: vocal_src.wav  ->  Выход: vocal_tuned.wav
"""
import numpy as np
from scipy.signal import medfilt, butter, sosfilt
import soundfile as sf

SR = 48000

# ---- гамма F minor (натуральный минор): F G Ab Bb C Db Eb ----
PC = {5,7,8,10,0,1,3}                       # pitch-classes (C=0)
ALLOWED = np.array([m for m in range(36, 79) if (m % 12) in PC])  # midi C2..F#5

def hz2midi(f): return 69 + 12*np.log2(np.maximum(f,1e-6)/440.0)
def midi2hz(m): return 440.0*2**((m-69)/12.0)

def snap_midi(m):
    i = np.argmin(np.abs(ALLOWED - m))
    return ALLOWED[i]

# ---------------- F0 detection (autocorrelation) ----------------
def detect_f0(x, hop=128, win=1536, fmin=80, fmax=300):
    n = len(x)
    lag_min = int(SR/fmax); lag_max = int(SR/fmin)
    frames = range(0, n-win, hop)
    f0 = np.zeros(len(list(frames)))
    voiced = np.zeros(len(f0), bool)
    idx = np.arange(0, n-win, hop)
    for j,i in enumerate(idx):
        seg = x[i:i+win] * np.hanning(win)
        e = np.sqrt(np.mean(seg**2))
        if e < 0.005:
            continue
        # автокорреляция через FFT
        s = seg - seg.mean()
        ac = np.fft.irfft(np.abs(np.fft.rfft(s, 2*win))**2)[:win]
        r0 = ac[0] + 1e-9
        region = ac[lag_min:lag_max]
        if len(region)==0: continue
        k = np.argmax(region) + lag_min
        # параболическая интерполяция пика
        if 1 <= k < win-1:
            a,b,c = ac[k-1],ac[k],ac[k+1]
            denom = (a-2*b+c)
            shift = 0.5*(a-c)/denom if denom!=0 else 0
        else:
            shift = 0
        lag = k + np.clip(shift,-1,1)
        conf = ac[k]/r0
        if conf > 0.30:
            f0[j] = SR/lag
            voiced[j] = True
    return f0, voiced, idx

def build_contour(f0, voiced, idx, n):
    """сгладить и интерполировать F0 на каждый сэмпл; вернуть f0_samp, voiced_samp."""
    f = f0.copy()
    # медианный фильтр по вокализованным (убрать октавные срывы)
    vals = f[voiced]
    if len(vals) >= 5:
        fv = medfilt(vals, 5)
        f[voiced] = fv
    # интерполяция по времени кадров -> сэмплы
    fr_t = idx + 768
    good = voiced & (f>0)
    if good.sum() < 2:
        return np.zeros(n), np.zeros(n, bool)
    f0_samp = np.interp(np.arange(n), fr_t[good], f[good],
                        left=f[good][0], right=f[good][-1])
    v_samp = np.interp(np.arange(n), fr_t, voiced.astype(float)) > 0.5
    return f0_samp, v_samp

# ---------------- pitch marks ----------------
def pitch_marks(x, f0_samp, v_samp):
    n = len(x); marks=[]; periods=[]
    # старт
    i = int(np.argmax(v_samp)) if v_samp.any() else 0
    i = max(i, 1)
    while i < n-1:
        f = f0_samp[i] if f0_samp[i]>0 else 150.0
        T = int(np.clip(SR/f, 60, 800))
        # притянуть метку к локальному пику |x| в окне ±T/4 (glottal sync)
        lo=max(0,i-T//4); hi=min(n,i+T//4)
        if hi>lo:
            i = lo + int(np.argmax(np.abs(x[lo:hi])))
        marks.append(i); periods.append(T)
        i += T
    return np.array(marks), np.array(periods)

# ---------------- PSOLA синтез ----------------
def psola(x, marks, periods, f0_samp, v_samp, strength=1.0):
    n=len(x); out=np.zeros(n)
    tgt = np.zeros(n)   # целевой период на сэмпл
    for i in range(n):
        if v_samp[i] and f0_samp[i]>0:
            m = hz2midi(f0_samp[i])
            sm = snap_midi(m)
            m2 = m + strength*(sm - m)      # сила автотюна
            tgt[i] = SR/midi2hz(m2)
        else:
            tgt[i] = 0
    o = float(marks[0])
    while o < n-1:
        oi=int(o)
        k = np.searchsorted(marks, oi)
        k = np.clip(k, 0, len(marks)-1)
        if k>0 and abs(marks[k-1]-oi)<abs(marks[k]-oi): k-=1
        m=marks[k]; T=periods[k]
        lo=m-T; hi=m+T
        a=max(0,lo); b=min(n,hi)
        seg=x[a:b]*np.hanning(hi-lo)[a-lo:b-lo]
        # положить центр сегмента в позицию o
        dst0=oi-T; da=max(0,dst0); db=min(n,dst0+len(seg))
        so=da-dst0
        out[da:db]+=seg[so:so+(db-da)]
        # шаг = целевой период (voiced) или входной (unvoiced -> копия)
        if v_samp[oi] and tgt[oi]>0:
            step=tgt[oi]
        else:
            step=T
        o+=max(40, step)
    return out

def autotune(x, strength=0.9):
    f0,voiced,idx = detect_f0(x)
    f0_samp,v_samp = build_contour(f0,voiced,idx,len(x))
    marks,periods = pitch_marks(x,f0_samp,v_samp)
    if len(marks)<3:
        return x, dict(note="no pitch found")
    y = psola(x,marks,periods,f0_samp,v_samp,strength)
    # нормализация к исходной громкости
    if np.max(np.abs(y))>0:
        y = y/np.max(np.abs(y))*np.max(np.abs(x))
    # диагностика: медианный F0 до/после
    med_in = np.median(f0[voiced]) if voiced.any() else 0
    return y, dict(med_f0_in=float(med_in), n_marks=int(len(marks)),
                   voiced_ratio=float(v_samp.mean()))

if __name__=="__main__":
    x,sr=sf.read("vocal_src.wav")
    if x.ndim>1: x=x.mean(1)
    x=x.astype(np.float64)
    assert sr==SR, sr
    y,info=autotune(x, strength=0.9)
    sf.write("vocal_tuned.wav", y.astype(np.float32), SR)
    print("autotune done:", info)
    # проверка: F0 выхода должен лежать ближе к нотам гаммы
    f0o,vo,_=detect_f0(y)
    if vo.any():
        moff=hz2midi(f0o[vo])
        err=np.abs(moff-np.round(moff))            # близость к полутонам
        # отклонение от НОТ ГАММЫ
        gerr=np.array([abs(m-ALLOWED[np.argmin(np.abs(ALLOWED-m))]) for m in moff])
        print(f"out median F0={np.median(f0o[vo]):.1f}Hz  mean |dist to scale note|={gerr.mean():.3f} semitone")
