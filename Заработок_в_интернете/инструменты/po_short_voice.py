import os, sys, wave, glob, subprocess, array
from PIL import Image, ImageDraw, ImageFont
from piper import PiperVoice
from piper.config import SynthesisConfig
P = os.path.dirname(os.path.abspath(__file__)); os.chdir(P)
W, H, FPS = 1080, 1920, 30
F = P + '/../domer/assfonts/Montserrat_wght_800.ttf'
REC, REC3 = P + '/../po_promo/rec', P + '/../po_promo/rec3'
os.makedirs('tts', exist_ok=True); os.makedirs('seg', exist_ok=True)
def run(a): subprocess.run(a, check=True)
def dur(f): return float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f]))
def font(s): return ImageFont.truetype(F, s)
# (spoken text, subtitle text, visual, pill)
SEG = [
 ("Этот ролик про Майнкрафт набрал десять тысяч просмотров. И я его даже не снимал.",
  "Этот ролик про Майнкрафт набрал 10 000 просмотров. И я его даже не снимал.", ('band', '../domer2/out/b2_voprosy.mp4', 0), ('10 000 ПРОСМОТРОВ', 'y')),
 ("Я просто нарезал стрим Домера. А Домер сам платит за такие нарезки.",
  "Я просто нарезал стрим Домера. А Домер сам платит за такие нарезки.", ('band', '../domer2/out/b2_voprosy.mp4', 7.0), ('НАРЕЗКИ СТРИМОВ', 'w')),
 ("Двадцать долларов, если нарезка наберёт сто тысяч просмотров, и девяносто — за миллион.",
  "$20, если нарезка наберёт 100 000 просмотров, и $90 — за миллион.", ('rec', REC, 'c_domer', 0.3), ('$20 ЗА 100 000', 'y')),
 ("А на площадке Прайм Оракулс есть задания, где платят уже с первой тысячи просмотров.",
  "А на площадке Prime Oracles есть задания, где платят уже с первой 1000 просмотров.", ('rec', REC3, 'b_rewards', 0.5), ('ПЛАТЯТ С 1 000 ПРОСМОТРОВ', 'y')),
 ("Выбираешь задание, нарезаешь, выкладываешь и отправляешь ссылку. Выплаты в долларах.",
  "Выбираешь задание, нарезаешь, выкладываешь и отправляешь ссылку. Выплаты в долларах.", ('rec', REC, 'd_zeny', 0.3), ('4 ШАГА', 'w')),
 ("Ищи Прайм Оракулс. А я через неделю покажу, сколько заработал.",
  "Ищи Prime Oracles. А я через неделю покажу, сколько заработал.", ('rec', REC, 'a_home', 0.0), ('ИЩИ: PRIME ORACLES', 'y')),
]
voice = PiperVoice.load(P + '/../tts/ru_RU-dmitri-medium.onnx'); cfg = SynthesisConfig(length_scale=0.95)
GAP = 0.25; t = 0.0; items = []
for i, (sp, sub, vis, pl) in enumerate(SEG):
    f = f'tts/s{i}.wav'
    if not os.path.exists(f):
        with wave.open(f, 'wb') as wf: voice.synthesize_wav(sp, wf, syn_config=cfg)
    d = dur(f); items.append((t, d)); t += d + GAP
TOTAL = t + 0.4
with wave.open('tts/s0.wav') as w0: sr = w0.getframerate()
buf = array.array('h', [0] * int((TOTAL + 1) * sr))
for i, (st, d) in enumerate(items):
    with wave.open(f'tts/s{i}.wav') as w: a = array.array('h', w.readframes(w.getnframes()))
    o = int(st * sr); buf[o:o + len(a)] = a
with wave.open('voice.wav', 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(buf.tobytes())
# subtitles
def chunks(text, maxc=24):
    out, cur = [], ''
    for w in text.split():
        x = (cur + ' ' + w).strip()
        if len(x) <= maxc: cur = x
        else: out.append(cur); cur = w
    if cur: out.append(cur)
    return out
def ts(x): return f'{int(x//3600)}:{int(x%3600//60):02d}:{x%60:05.2f}'
ass = [f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: S,Montserrat ExtraBold,76,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,7,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""]
for (st, d), (sp, sub, vis, pl) in zip(items, SEG):
    cs = chunks(sub); tot = sum(len(c) for c in cs); x = st
    for c in cs:
        dd = d * len(c) / tot
        ass.append(f'Dialogue: 0,{ts(x)},{ts(x+dd)},S,,0,0,0,,{{\\an5\\pos({W//2},1500)\\fscx85\\fscy85\\t(0,90,\\fscx100\\fscy100)}}{c.upper()}')
        x += dd
open('subs.ass', 'w').write('\n'.join(ass) + '\n')
def pill(text, kind, path):
    im = Image.new('RGBA', (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im); fs = 70
    while font(fs).getlength(text) > 960: fs -= 2
    f = font(fs); tw = f.getlength(text); x = (W - tw) / 2; y = 330
    d.rounded_rectangle([x - 30, y - 10, x + tw + 30, y + 92], radius=24, fill=(255, 204, 0, 250) if kind == 'y' else (255, 255, 255, 250))
    d.text((x, y + 4), text, font=f, fill=(12, 12, 12, 255))
    f2 = font(30); t2 = 'Реклама • Prime Oracles'
    d.text((W - f2.getlength(t2) - 28, 40), t2, font=f2, fill=(255, 255, 255, 210), stroke_width=2, stroke_fill=(0, 0, 0, 200))
    im.save(path)
lst = open('seg/list.txt', 'w')
for i, ((st, d), (sp, sub, vis, pl)) in enumerate(zip(items, SEG)):
    sd = d + (GAP if i < len(SEG) - 1 else 0.4); out = f'seg/v{i}.mp4'; pill(pl[0], pl[1], f'seg/p{i}.png')
    a = ['ffmpeg', '-y', '-loglevel', 'error']
    if vis[0] == 'band':
        a += ['-ss', str(vis[2]), '-i', P + '/' + vis[1]]
        v = (f'[0:v]fps={FPS},crop=1080:729:0:560,split[x][y];[x]scale=-2:{H},crop={W}:{H},boxblur=20:3,eq=brightness=-0.2[bg];'
             f'[bg][y]overlay=0:560,trim=0:{sd:.2f},setpts=PTS-STARTPTS[b]')
    else:
        src = glob.glob(f'{vis[1]}/{vis[2]}/*.webm')[0]; s0 = float(open(f'{vis[1]}/{vis[2]}/start.txt').read()) + vis[3]
        a += ['-ss', f'{s0:.2f}', '-i', src]
        v = f'[0:v]fps={FPS},crop=540:960:0:0,scale={W}:{H}:flags=lanczos,tpad=stop_mode=clone:stop_duration={sd:.2f},trim=0:{sd:.2f},setpts=PTS-STARTPTS[b]'
    a += ['-loop', '1', '-t', f'{sd:.2f}', '-i', f'seg/p{i}.png',
          '-filter_complex', v + ';[1:v]format=rgba,fade=t=in:d=0.2:alpha=1[p];[b][p]overlay=0:0:shortest=1,format=yuv420p[o]',
          '-map', '[o]', '-t', f'{sd:.2f}', '-r', str(FPS), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-an', out]
    run(a); lst.write(f"file '{os.path.abspath(out)}'\n")
lst.close()
run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', 'seg/list.txt', '-c', 'copy', 'raw.mp4'])
run(['ffmpeg', '-y', '-loglevel', 'error', '-i', 'raw.mp4', '-i', 'voice.wav', '-stream_loop', '-1', '-i', P + '/../vids/music/m5.mp3',
     '-filter_complex', f"[0:v]ass=subs.ass:fontsdir={P}/../domer/assfonts[v];[1:a]aresample=48000,loudnorm=I=-14:TP=-1.5:LRA=7[vo];"
                        f"[2:a]aresample=48000,volume=0.10,atrim=0:{TOTAL:.2f},afade=t=out:st={TOTAL-1:.2f}:d=1[mu];[vo][mu]amix=inputs=2:duration=first:normalize=0[a]",
     '-map', '[v]', '-map', '[a]', '-t', f'{TOTAL:.2f}', '-c:v', 'libx264', '-preset', 'medium', '-crf', '21', '-pix_fmt', 'yuv420p',
     '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', 'po_short2.mp4'])
print('DONE', round(TOTAL, 1))
