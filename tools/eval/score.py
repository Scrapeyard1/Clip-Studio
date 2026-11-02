"""python score.py out_dir: compare a planner run against Elijah's answer key."""
import json, sys, os
import os.path as _p
truth = json.load(open(_p.join(_p.dirname(_p.abspath(__file__)), "answer_key.json")))
def hms(t): t = int(t); return f"{t//3600}:{t%3600//60:02d}:{t%60:02d}"
def ov(a, b): return max(0, min(a[1], b[1]) - max(a[0], b[0]))
out = sys.argv[1]; verbose = "-v" in sys.argv
tot = {"videos": 0, "found": 0, "picked": 0, "pred_videos": 0, "pred_correct": 0, "start_err": [], "end_err": [],
       "co_hit": 0, "co_near": 0, "co_len": [], "co_clips": [], "cost": 0}
for live, d in truth.items():
    f = f"{out}/{live}.json"
    if not os.path.exists(f): print(live, "missing"); continue
    r = json.load(open(f)); plan = r["plan"]; u = r.get("usage", {})
    tot["cost"] += u.get("in", 0) * 2e-6 + u.get("out", 0) * 10e-6
    convs = plan["conversations"]
    print(f"\n== {live}: planner found {len(convs)} conversations; you made {len(d['videos'])} videos")
    used = set()
    for v in d["videos"]:
        tot["videos"] += 1
        span = (v["start"], v["end"])
        hits = [c for c in convs if ov(span, (c["start"], c["end"])) > 0.5 * (c["end"] - c["start"])]
        if not hits:
            print(f"  MISSED {hms(span[0])}-{hms(span[1])} {v.get('title')}"); continue
        for c in hits: used.add(id(c))
        s, e = min(c["start"] for c in hits), max(c["end"] for c in hits)
        tot["found"] += 1
        tot["start_err"].append(abs(s - span[0])); tot["end_err"].append(abs(e - span[1]))
        pick = any(c.get("video", c.get("rank") == "STRONG") for c in hits)
        tot["picked"] += pick
        co = [x for c in hits for x in c.get("cold_open", [])]
        colen = sum(b - a for a, b in co)
        hit = sum(ov((a, b), tuple(t)) for a, b in co for t in v.get("cold_open", []))
        tlen = sum(b - a for a, b in v.get("cold_open", []))
        near = any(abs(a - t[0]) < 60 for a, b in co for t in v.get("cold_open", []))
        tot["co_hit"] += (hit / tlen) if tlen else 0; tot["co_near"] += near
        tot["co_len"].append(colen); tot["co_clips"].append(len(co))
        shape = len(co) in (1, 2) and 25 <= colen <= 50 and all(c.get("cold_open_ends_at") is not None for c in hits if c.get("video", True))
        tot.setdefault("shape", 0); tot["shape"] += shape
        print(f"  {'PICKED' if pick else 'not picked'} {hms(span[0])}-{hms(span[1])} {v.get('title')}")
        print(f"     as {len(hits)} conv(s): {hms(s)}-{hms(e)} (start off {s-span[0]:+.0f}s, end off {e-span[1]:+.0f}s) "
              f"rank {[c.get('rank') for c in hits]} video {[c.get('video') for c in hits]}")
        print(f"     cold open {len(co)} clip(s) {colen:.0f}s {[hms(a)+'-'+hms(b) for a,b in co]} vs yours {[hms(a)+'-'+hms(b) for a,b in v.get('cold_open',[])]}"
              f" -> {100*hit/tlen if tlen else 0:.0f}% of yours{' (near)' if near else ''}")
    for c in convs:
        p = c.get("video", c.get("rank") == "STRONG")
        tot["pred_videos"] += p; tot["pred_correct"] += p and id(c) in used
        if id(c) not in used:
            print(f"  (no video from you) {hms(c['start'])}-{hms(c['end'])} {(c['end']-c['start'])/60:.0f}min {c.get('guest')} rank {c.get('rank')} kind {c.get('kind')} video {c.get('video')}: {c.get('topic')}"
                  + (f"\n        why: {c.get('video_reason')}" if verbose and c.get('video_reason') else ""))
n = tot["found"] or 1
med = lambda xs: sorted(xs)[len(xs)//2] if xs else 0
print(f"\nSUMMARY  found {tot['found']}/{tot['videos']} of your videos; picked as video {tot['picked']}/{tot['videos']}; "
      f"of its own video picks {tot['pred_correct']}/{tot['pred_videos']} were yours")
print(f"  boundaries: median start off {med(tot['start_err']):.0f}s, median end off {med(tot['end_err']):.0f}s")
print(f"  cold open: avg {100*tot['co_hit']/n:.0f}% overlap with your preview; within a minute of yours {tot['co_near']}/{tot['found']}; "
      f"median length {med(tot['co_len']):.0f}s; clips {tot['co_clips']}; your shape (1-2 clips, 25-50 s, ends on a checked question) {tot.get('shape',0)}/{tot['found']}")
print(f"  cost ~${tot['cost']:.2f}")
