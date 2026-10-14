import json, sys, subprocess, re
from PIL import Image, ImageDraw, ImageFont
W,H=1080,1920
VY=560; VH=729          # video band
SUB_Y=1450
FONT='assfonts/Montserrat_wght_800.ttf'
def wrap(text,font,maxw):
    words=text.split(); lines=[]; cur=''
    for w in words:
        t=(cur+' '+w).strip()
        if font.getlength(t)<=maxw: cur=t
        else: lines.append(cur); cur=w
    if cur: lines.append(cur)
    return lines
def overlay_png(hook,path):
    im=Image.new('RGBA',(W,H),(0,0,0,0)); d=ImageDraw.Draw(im)
    f=ImageFont.truetype(FONT,64)
    lines=[l for part in hook.split('\n') for l in wrap(part,f,940)]; lh=86
    y=VY-30-lh*len(lines)
    for ln in lines:
        tw=f.getlength(ln); x=(W-tw)/2; pad=26
        d.rounded_rectangle([x-pad,y-6,x+tw+pad,y+lh-10],radius=20,fill=(255,255,255,250))
        d.text((x,y+2),ln,font=f,fill=(12,12,12,255))
        y+=lh
    f2=ImageFont.truetype(FONT,30)
    t='оригинал: Домер (YouTube)'; tw=f2.getlength(t)
    d.text(((W-tw)/2,VY+VH+14),t,font=f2,fill=(255,255,255,170))
    im.save(path)
def ts(t):
    t=max(0,t); h=int(t//3600); m=int(t%3600//60); s=t%60
    return f"{h}:{m:02d}:{s:05.2f}"
def make_ass(chunks,parts,path):
    # map source time -> output time
    def mp(t):
        cum=0
        for a,b in parts:
            if t<a: return cum
            if t<=b: return cum+(t-a)
            cum+=b-a
        return cum
    total=sum(b-a for a,b in parts)
    head=f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: S,Montserrat ExtraBold,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,7,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev=[]
    for i,c in enumerate(chunks):
        st=mp(c[0])
        if len(c)>2: en=mp(c[2])
        else:
            nxt=mp(chunks[i+1][0]) if i+1<len(chunks) else total
            en=min(nxt, st+2.2)
        txt=c[1].upper()
        txt=re.sub(r'\*(.+?)\*', r'{\\c&H00E5FF&}\1{\\c&HFFFFFF&}', txt)
        ev.append(f"Dialogue: 0,{ts(st)},{ts(en)},S,,0,0,0,,{{\\an5\\pos({W//2},{SUB_Y})\\fscx82\\fscy82\\t(0,90,\\fscx100\\fscy100)}}{txt}")
    open(path,'w').write(head+'\n'.join(ev)+'\n')
def render(spec):
    name=spec['name']; parts=spec['parts']
    overlay_png(spec['hook'],f'out/{name}_ov.png')
    make_ass(spec['chunks'],parts,f'out/{name}.ass')
    args=['ffmpeg','-y','-loglevel','error','-nostats']
    for a,b in parts:
        args+=['-ss',str(a),'-t',str(b-a),'-i',spec['video']]
    for a,b in parts:
        args+=['-ss',str(a),'-t',str(b-a),'-i',spec['video']]
    args+=['-i',f'out/{name}_ov.png']
    n=len(parts); fc=[]
    for i,(a,b) in enumerate(parts):
        d=b-a
        fc.append(f"[{i}:v]fps=60000/1001,setpts=PTS-STARTPTS,format=yuv420p[v{i}]")
        fc.append(f"[{n+i}:a]asetpts=PTS-STARTPTS,aresample=48000,afade=t=in:d=0.04,afade=t=out:st={d-0.06:.3f}:d=0.06[a{i}]")
    fc.append(''.join(f'[v{i}][a{i}]' for i in range(n))+f'concat=n={n}:v=1:a=1[cv][ca]')
    fc.append('[cv]split[c1][c2]')
    fc.append(f'[c1]scale=-2:{H},crop={W}:{H},scale=270:480,boxblur=8:2,scale={W}:{H},eq=brightness=-0.12:saturation=1.1[bg]')
    fc.append(f'[c2]scale=1296:{VH},crop={W}:{VH},eq=saturation=1.1:contrast=1.04[fg]')
    fc.append(f'[bg][fg]overlay=0:{VY}[b1]')
    fc.append(f'[b1][{2*n}:v]overlay=0:0[b2]')
    fc.append(f"[b2]ass=out/{name}.ass:fontsdir=assfonts[vout]")
    fc.append('[ca]loudnorm=I=-14:TP=-1.5:LRA=11[aout]')
    args+=['-filter_complex',';'.join(fc),'-map','[vout]','-map','[aout]',
           '-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-r','60000/1001',
           '-c:a','aac','-b:a','192k','-ar','48000','-movflags','+faststart',f'out/{name}.mp4']
    subprocess.run(args,check=True)
    print('done',name, sum(b-a for a,b in parts))
if __name__=='__main__':
    specs=json.load(open(sys.argv[1]))
    for s in specs:
        if len(sys.argv)>2 and s['name'] not in sys.argv[2:]: continue
        render(s)
