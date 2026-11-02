"""YouTube transcript lines -> Clip Studio transcript.json (segments with word times), like the app's own."""
import re, json, sys
TS = re.compile(r"^\[(\d+):(\d\d):(\d\d)\]\s*(.*)$")
def convert(path):
    cues = []
    for l in open(path, encoding="utf-8"):
        m = TS.match(l.strip())
        if not m: continue
        t = int(m[1])*3600+int(m[2])*60+int(m[3])
        text = re.sub(r"\[[^\]]*\]", " ", m[4]).replace(">>", " ")   # no tags or speaker marks: the app has neither
        cues.append((t, " ".join(text.split())))
    duration = cues[-1][0] + 3.0
    words = []
    for i, (t, text) in enumerate(cues):
        nxt = cues[i+1][0] if i+1 < len(cues) else t + 3
        ws = text.split()
        if not ws: continue
        step = max(0.2, (nxt - t)) / len(ws)
        for j, w in enumerate(ws):
            words.append({"w": w, "s": round(t + j*step, 2), "e": round(t + (j+1)*step, 2)})
    segs, cur = [], []
    for w in words:
        cur.append(w)
        dur = cur[-1]["e"] - cur[0]["s"]
        if (w["w"][-1] in ".?!" and dur >= 4) or dur >= 14:
            segs.append(cur); cur = []
    if cur: segs.append(cur)
    return {"duration": duration, "segments": [{"start": s[0]["s"], "end": s[-1]["e"], "text": " ".join(x["w"] for x in s), "words": s} for s in segs]}
if __name__ == "__main__":
    # python tools/eval/convert.py <transcripts dir>: each "<live>.txt" (YouTube "[h:mm:ss] text" lines) -> transcript-<live>.json
    import glob, os
    src = sys.argv[1] if len(sys.argv) > 1 else "tools/eval/transcripts"
    for f in sorted(glob.glob(os.path.join(src, "*.txt"))):
        live = os.path.splitext(os.path.basename(f))[0]
        t = convert(f)
        json.dump(t, open(os.path.join(src, f"transcript-{live}.json"), "w"))
        print(live, len(t["segments"]), "segments")
