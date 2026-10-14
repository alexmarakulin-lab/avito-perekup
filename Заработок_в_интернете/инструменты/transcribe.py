import sys, json, time
from faster_whisper import WhisperModel
src, out = sys.argv[1], sys.argv[2]
t0=time.time()
m = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=4)
segs, info = m.transcribe(src, language="ru", vad_filter=True, word_timestamps=True, beam_size=1)
res=[]
for s in segs:
    res.append({"start":s.start,"end":s.end,"text":s.text.strip(),"words":[{"s":w.start,"e":w.end,"w":w.word} for w in (s.words or [])]})
    if len(res)%50==0:
        json.dump(res,open(out,'w'),ensure_ascii=False); print(len(res), round(s.end), 'sec', round(time.time()-t0),'s elapsed', flush=True)
json.dump(res,open(out,'w'),ensure_ascii=False)
print('DONE', len(res), round(time.time()-t0), flush=True)
