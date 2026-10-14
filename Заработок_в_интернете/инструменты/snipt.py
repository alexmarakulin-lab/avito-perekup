import sys, json, glob, os
from faster_whisper import WhisperModel
m = WhisperModel("medium", device="cpu", compute_type="int8", cpu_threads=4)
out={}
for f in sorted(glob.glob(os.path.join(sys.argv[1],'s_*.wav'))):
    off=float(os.path.basename(f)[2:-4])
    segs,_=m.transcribe(f, language="ru", word_timestamps=True, beam_size=5, vad_filter=False)
    out[off]=[{"s":round(w.start+off,2),"e":round(w.end+off,2),"w":w.word} for s in segs for w in s.words]
    print(off, ''.join(w['w'] for w in out[off]), flush=True)
json.dump(out,open(sys.argv[2],'w'),ensure_ascii=False)
print('DONE',flush=True)
