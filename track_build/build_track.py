# -*- coding: utf-8 -*-
"""
Сборка трека в стиле Айсгергера (мелодичный автотюн-трэп) из чистого вокала.
Всё синтезируется на numpy/scipy, сведение и мастеринг тоже.
Тональность: F minor. Темп: 125 BPM (half-time). Прогрессия: Fm - Db - Ab - Eb (i-VI-III-VII).
"""
import numpy as np
from scipy.signal import fftconvolve, butter, sosfilt
import soundfile as sf

SR = 48000
BPM = 125.0
BEAT = 60.0 / BPM          # 0.48 c
BAR = 4 * BEAT             # 1.92 c
rng = np.random.default_rng(7)

# ---------------- utils ----------------
def midi2f(m):
    return 440.0 * 2 ** ((m - 69) / 12.0)

def env_ad(n, a, d, curve=2.5):
    """Attack-Decay огибающая (в сэмплах a,d)."""
    e = np.zeros(n)
    a = max(1, int(a)); d = max(1, int(d))
    e[:a] = np.linspace(0, 1, a)
    dd = min(d, n - a)
    e[a:a+dd] = np.exp(-np.linspace(0, curve, dd))
    return e

def adsr(n, a, dcy, s, r):
    a,dcy,r = int(a),int(dcy),int(r)
    e = np.zeros(n)
    a=max(1,a); dcy=max(1,dcy); r=max(1,r)
    sus = max(0, n - a - dcy - r)
    e[:a] = np.linspace(0,1,a)
    e[a:a+dcy] = np.linspace(1,s,dcy)
    e[a+dcy:a+dcy+sus] = s
    e[a+dcy+sus:a+dcy+sus+r] = np.linspace(s,0,r)
    return e[:n]

def lp(x, fc, order=4):
    sos = butter(order, fc/(SR/2), 'low', output='sos');  return sosfilt(sos, x)
def hp(x, fc, order=4):
    sos = butter(order, fc/(SR/2), 'high', output='sos'); return sosfilt(sos, x)
def bp(x, f1, f2, order=4):
    sos = butter(order, [f1/(SR/2), f2/(SR/2)], 'band', output='sos'); return sosfilt(sos, x)

def soft(x, drive=1.0):
    return np.tanh(x*drive)

def place(buf, sig, t):
    i = int(round(t*SR))
    j = min(len(buf), i+len(sig))
    if i < len(buf):
        buf[i:j] += sig[:j-i]

# ---------------- reverb (алгоритмический IR) ----------------
def make_ir(length_s, decay, seed):
    r = np.random.default_rng(seed)
    n = int(length_s*SR)
    ir = r.standard_normal(n) * np.exp(-np.linspace(0, decay, n))
    ir = lp(ir, 7000); ir = hp(ir, 180)
    ir[:int(0.008*SR)] = 0                 # предзадержка
    ir /= np.sqrt(np.sum(ir**2))+1e-9
    return ir

IR_L = make_ir(2.4, 5.5, 11)
IR_R = make_ir(2.4, 5.5, 22)

def reverb_stereo(mono, mix=0.3):
    wetL = fftconvolve(mono, IR_L)[:len(mono)]
    wetR = fftconvolve(mono, IR_R)[:len(mono)]
    return mono*(1-mix)+wetL*mix, mono*(1-mix)+wetR*mix

# ---------------- delay ----------------
def delay(x, t, fb=0.35, mix=0.35, n_rep=6):
    d = int(t*SR)
    out = x.copy()
    tap = x.copy()
    for _ in range(n_rep):
        tap = np.concatenate([np.zeros(d), tap])[:len(x)] * fb
        out = out + tap*mix
    return out

# ---------------- инструменты ----------------
def kick(dur=0.5):
    n=int(dur*SR); t=np.arange(n)/SR
    f=np.linspace(140,45,n)
    ph=2*np.pi*np.cumsum(f)/SR
    body=np.sin(ph)*env_ad(n,0.001*SR,0.28*SR,4)
    click=hp(rng.standard_normal(n),2500)*env_ad(n,0.0005*SR,0.01*SR,6)*0.5
    return soft(body*1.2+click,1.1)*0.9

def eight08(freqs, times, dur):
    """808 с глайдом между нотами. freqs: список (t,f). times не исп."""
    n=int(dur*SR); out=np.zeros(n)
    return out

def make_808(note_hz, dur, glide_from=None):
    n=int(dur*SR); t=np.arange(n)/SR
    if glide_from:
        gl=int(min(n,0.05*SR))
        f=np.concatenate([np.linspace(glide_from,note_hz,gl), np.full(n-gl,note_hz)])
    else:
        f=np.full(n,note_hz)
    ph=2*np.pi*np.cumsum(f)/SR
    e=adsr(n,0.004*SR,0.02*SR,0.9,0.12*SR)
    sub=np.sin(ph)*e
    sub=soft(sub*1.4,1.3)                    # насыщение 808
    sub=sub+0.15*np.sin(2*ph)*e             # немного 2й гармоники для слышимости
    return sub*0.9

def clap():
    n=int(0.32*SR)
    base=bp(rng.standard_normal(n),1200,4500)
    e=np.zeros(n)
    for off in [0,0.009,0.018,0.028]:
        i=int(off*SR); e[i:]+= np.exp(-np.linspace(0,30,n-i))
    body=base*e
    tail=bp(rng.standard_normal(n),1500,3500)*np.exp(-np.linspace(0,8,n))*0.4
    return (body*0.9+tail)*0.7

def hat(open_=False):
    d=0.14 if open_ else 0.045
    n=int(d*SR)
    h=hp(rng.standard_normal(n),7000)
    e=np.exp(-np.linspace(0, 6 if open_ else 22, n))
    return h*e*0.35

def pad_chord(midis, dur):
    n=int(dur*SR); out=np.zeros(n); t=np.arange(n)/SR
    for m in midis:
        for det in (-0.12,0.0,0.12):          # детюн для ширины
            f=midi2f(m)*2**(det/12)
            # пилообразная через сумму гармоник
            s=np.zeros(n)
            for k in range(1,7):
                s+=np.sin(2*np.pi*f*k*t + rng.random()*6.28)/k
            out+=s
    out=lp(out, 2600)
    e=adsr(n,0.12*SR,0.2*SR,0.8,0.6*SR)
    out*=e
    return out/np.max(np.abs(out)+1e-9)*0.5

def pluck(midi, dur):
    n=int(dur*SR); t=np.arange(n)/SR
    f=midi2f(midi)
    s=(np.sin(2*np.pi*f*t)+0.5*np.sin(4*np.pi*f*t)+0.3*np.sin(6*np.pi*f*t))
    s=lp(s,4500)
    e=env_ad(n,0.003*SR,dur*0.9*SR,3.5)
    return s*e*0.5

def bell(midi, dur):
    """Тёмный колокольчик/лид (FM-ish) для dark-trap верха."""
    n=int(dur*SR); t=np.arange(n)/SR
    f=midi2f(midi)
    mod=np.sin(2*np.pi*f*2*t)*3.0*np.exp(-t*6)
    s=np.sin(2*np.pi*f*t + mod) + 0.35*np.sin(2*np.pi*f*2*t)
    s=hp(s,300)
    e=env_ad(n,0.002*SR,dur*0.95*SR,3.0)
    return s*e*0.5

# ---------------- аранжировка ----------------
# грид — раздельные шины для контроля баланса
N_BARS = 24
total = N_BARS*BAR + 2.5
N = int(total*SR)
kicks=np.zeros(N); perc=np.zeros(N); hats=np.zeros(N)
sub=np.zeros(N); pads=np.zeros(N); lead=np.zeros(N); bells=np.zeros(N)

# прогрессия аккордов (корень 808, midi для пэда/лида) по 1 аккорду на такт, цикл 4
# Fm, Db, Ab, Eb
prog = [
    dict(root=midi2f(41),               pad=[53,56,60,65], name='Fm'),   # F2 root
    dict(root=midi2f(37),               pad=[49,53,56,61], name='Db'),   # Db2
    dict(root=midi2f(44),               pad=[56,60,63,68], name='Ab'),   # Ab2
    dict(root=midi2f(39),               pad=[51,55,58,63], name='Eb'),   # Eb2
]

# пентатоника/натуральный минор F для лид-мотива (midi, октава 4-5)
lead_scale=[65,67,68,70,72,73,75,77]   # F4 G Ab Bb C5 Db Eb F5

# мотив лида на такт (в 1/8), индексы в lead_scale, None=пауза
lead_motifs=[
    [4,None,3,4,None,2,3,None],
    [3,None,2,3,None,0,2,None],
    [5,None,4,5,None,3,4,None],
    [2,None,3,2,None,0,None,None],
]
# зловещий колокольчик — реже, высокий регистр (октава выше)
bell_scale=[77,79,80,82,84,85]   # F5 G Ab Bb C6 Db
bell_motifs=[
    [(0.0,4),(1.5,3),(3.0,2)],
    [(0.0,3),(2.0,1)],
    [(0.0,5),(1.0,4),(2.5,3)],
    [(0.5,2),(2.0,0)],
]

def bar_time(b): return b*BAR

for b in range(N_BARS):
    ch = prog[b % 4]
    t0 = bar_time(b)
    intro = b < 2
    outro = b >= N_BARS-2

    # ---- 808 (корневая нота, держится такт, с глайдом от предыдущего) ----
    prev_root = prog[(b-1) % 4]['root'] if b>0 else None
    s808 = make_808(ch['root'], BAR*0.98, glide_from=prev_root if b>0 else None)
    place(sub, s808*(0.7 if intro else 1.0), t0)
    if not intro:
        # синкопированные повторы 808 для трэп-драйва
        s808b = make_808(ch['root'], BEAT*1.1, glide_from=None)
        place(sub, s808b*0.85, t0+2.5*BEAT)
        place(sub, make_808(ch['root'], BEAT*0.7)*0.7, t0+3.75*BEAT)

    # ---- пэд ----
    pc = pad_chord(ch['pad'], BAR)
    place(pads, pc*(0.9 if not intro else 1.0), t0)

    # ---- лид/плак ----
    if not intro or b==1:
        motif=lead_motifs[b%4]
        for i,idx in enumerate(motif):
            if idx is None: continue
            place(lead, pluck(lead_scale[idx], BEAT*0.9), t0 + i*(BEAT/2))

    # ---- зловещий колокольчик (dark-trap верх) ----
    if not intro:
        for bt, idx in bell_motifs[b%4]:
            place(bells, bell(bell_scale[idx], BEAT*1.4), t0 + bt*BEAT)

    if outro:  # аутро — только хвосты, барабаны стихают
        continue

    # ---- барабаны (half-time) ----
    if not intro:
        place(kicks, kick(), t0)                         # kick доля 1
        place(kicks, kick()*0.7, t0+2.5*BEAT)            # доп кик
        place(perc, clap(), t0+2*BEAT)                   # clap доля 3
        # хэты 1/8 с роллами 1/16
        for i in range(8):
            th=t0+i*(BEAT/2)
            place(hats, hat(open_=(i==7)), th)
            if i in (3,5):                               # роллы 1/16
                place(hats, hat()*0.75, th+BEAT/4)
        # быстрый ролл 1/32 в конце 4-го такта фразы
        if b%4==3:
            for k in range(8):
                place(hats, hat()*(0.4+0.08*k), t0+3.5*BEAT+k*(BEAT/8))

# ---------------- вокал ----------------
import os
VOCAL_FILE = "vocal_tuned.wav" if os.path.exists("vocal_tuned.wav") else "vocal_src.wav"
print("vocal source:", VOCAL_FILE)
vx, vsr = sf.read(VOCAL_FILE)
if vx.ndim>1: vx=vx.mean(axis=1)
vx=vx.astype(np.float64)
assert vsr==SR

# 1) чистка: highpass + мягкий гейт от шума пауз
vx=hp(vx,95)
# 2) presence EQ (буст ~4кГц), лёгкий воздух ~10к
pres=bp(vx,3000,6000)*0.6
air=hp(vx,9000)*0.45
vlead=vx+pres+air
# 3) простая компрессия (peak-follow)
def compress(x, thr=0.25, ratio=4.0, atk=0.005, rel=0.12):
    env=np.zeros_like(x); a=np.exp(-1/(atk*SR)); r=np.exp(-1/(rel*SR)); e=0
    ax=np.abs(x)
    for i in range(len(x)):
        e = a*e+(1-a)*ax[i] if ax[i]>e else r*e+(1-r)*ax[i]
        env[i]=e
    g=np.ones_like(x)
    over=env>thr
    g[over]=(thr+(env[over]-thr)/ratio)/(env[over]+1e-9)
    return x*g
vlead=compress(vlead)
vlead/=np.max(np.abs(vlead)+1e-9)
vlead*=0.9

# 4) дабл/хорус для ширины (два расстроенных слоя L/R с микро-задержкой)
def chorus_layer(x, delay_ms, depth_ms, rate):
    d=int(delay_ms/1000*SR)
    t=np.arange(len(x))/SR
    mod=(depth_ms/1000*SR)*np.sin(2*np.pi*rate*t)
    idx=np.arange(len(x))-d-mod
    idx=np.clip(idx,0,len(x)-1)
    i0=idx.astype(int); frac=idx-i0
    return x[i0]*(1-frac)+x[np.clip(i0+1,0,len(x)-1)]*frac

vL = vlead + 0.55*chorus_layer(vlead, 14, 3.0, 0.7)
vR = vlead + 0.55*chorus_layer(vlead, 19, 3.2, 0.9)

# 5) дилей в темпе (dotted 1/8 = 0.36c) + slapback
d_time = BEAT*0.75            # dotted eighth
vL = delay(vL, d_time, fb=0.33, mix=0.28)
vR = delay(vR, d_time*1.01, fb=0.33, mix=0.28)

# 6) реверб (plate), подмешиваем
rvL,_ = reverb_stereo(vlead, mix=1.0)
_,rvR = reverb_stereo(vlead, mix=1.0)
vL = vL + 0.22*rvL
vR = vR + 0.22*rvR

# уложить вокал в микс с нужной длиной
Ln=N
def fit(x):
    y=np.zeros(Ln); m=min(Ln,len(x)); y[:m]=x[:m]; return y
vL=fit(vL); vR=fit(vR)

# ---------------- сведение ----------------
def db(x): return 10**(x/20.0)

def norm1(x):
    return x/(np.max(np.abs(x))+1e-9)

def to_stereo(mono, pan=0.0, rev=0.0, rev_only_wet=False):
    """равномощностная панорама + опциональный реверб-подмес."""
    lg=np.cos((pan+1)/2*np.pi/2); rg=np.sin((pan+1)/2*np.pi/2)
    L=mono*lg; R=mono*rg
    if rev>0:
        wl,_=reverb_stereo(mono,1.0); _,wr=reverb_stereo(mono,1.0)
        L=L+rev*wl; R=R+rev*wr
    return L,R

# нормализуем каждую шину к пику 1, затем задаём баланс в дБ (вокал = 0 dB ref)
kicks=norm1(kicks); perc=norm1(perc); hats=norm1(hats)
sub=norm1(sub); pads=norm1(pads); lead=norm1(lead); bells=norm1(bells)

BAL = dict(voc=0.0, sub=-4.5, kick=-3.0, clap=-6.0, hat=-7.0,
           pad=-15.0, lead=-12.0, bell=-13.0)

kl,kr = to_stereo(kicks, 0.0, 0.03)
cl,cr = to_stereo(perc, 0.0, 0.10)
hl,hr = to_stereo(hats, -0.1, 0.05)
sl,sr_ = to_stereo(sub, 0.0, 0.0)
pl,pr = to_stereo(pads, 0.0, 0.30)
ll,lr = to_stereo(lead, 0.18, 0.35)
bl,br = to_stereo(bells, -0.22, 0.45)     # bell шире и с длинным ревербом

# инструментал (без вокала) — его будем дакать под кик
instL = (fit(kl)*db(BAL['kick']) + fit(cl)*db(BAL['clap']) + fit(hl)*db(BAL['hat'])
         + fit(sl)*db(BAL['sub']) + fit(pl)*db(BAL['pad']) + fit(ll)*db(BAL['lead'])
         + fit(bl)*db(BAL['bell']))
instR = (fit(kr)*db(BAL['kick']) + fit(cr)*db(BAL['clap']) + fit(hr)*db(BAL['hat'])
         + fit(sr_)*db(BAL['sub']) + fit(pr)*db(BAL['pad']) + fit(lr)*db(BAL['lead'])
         + fit(br)*db(BAL['bell']))

# сайдчейн-«пампинг» инструментала под кик
kick_env=np.zeros(N)
seg=int(0.30*SR)
for b in range(2, N_BARS-2):
    i=int(bar_time(b)*SR)
    kick_env[i:i+seg] = np.maximum(kick_env[i:i+seg], np.linspace(1,0,seg)**2)
duck=1-0.32*kick_env
instL=instL*duck; instR=instR*duck

# вокал вперёд (лёгкий дак только 15%, чтобы дышал с китом)
vduck=1-0.15*kick_env
L = instL + vL*db(BAL['voc'])*vduck
R = instR + vR*db(BAL['voc'])*vduck

# мастер-шина: мягкий клиппер + лимитер + нормализация к -0.8 dB
def master(x):
    x=soft(x*1.02,1.0)
    peak=np.max(np.abs(x))
    if peak>db(-0.8): x=x/peak*db(-0.8)
    return x
L=master(L); R=master(R)

stereo=np.stack([L,R],axis=1).astype(np.float32)
sf.write("final_mix.wav", stereo, SR)
try:
    sf.write("final_mix.mp3", stereo, SR)
    print("MP3 written")
except Exception as e:
    print("mp3 fail:", e)
print(f"Done. length={len(L)/SR:.2f}s  peakL={np.max(np.abs(L)):.3f} peakR={np.max(np.abs(R)):.3f}")
