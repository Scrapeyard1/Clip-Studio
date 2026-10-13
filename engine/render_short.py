"""Render a vertical short (1080x1920) from one or more pieces of a live.

  * tighter safe cuts than long-form (pauses over 0.6 s shrink to 0.25 s)
  * auto-framing: finds your face across the clip and crops 9:16 around it,
    below the stream's title banner and clear of the chat / donation overlays
  * animated word-by-word captions (current word highlighted), Poppins Bold
  * hook text pinned at the top for the whole clip
"""
import argparse, json, os, statistics, subprocess, tempfile
import cv2
from cuts import safe_cuts, remap

OW, OH, FPS = 1080, 1920, 30
FONT_DIR = "/usr/share/fonts/truetype/google-fonts"


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])


YUNET = "/home/claude/clipstudio/models/yunet.onnx"


def layout_runs(video, pieces, step=0.4):
    """Split kept stretches wherever the picture switches between camera and screen share.
    Returns [(start, end, face_x or None)]: face_x is the host's face centre (fraction of width),
    None means screen share (show the whole frame)."""
    cap = cv2.VideoCapture(video)
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    det = cv2.FaceDetectorYN.create(YUNET, "", (W, H), 0.7)
    runs, hits = [], 0
    for a, b in pieces:
        samples, t = [], a + 0.05
        while t < b:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, fr = cap.read()
            if ok:
                _, faces = det.detect(fr)
                x = None
                if faces is not None and len(faces):
                    fx, fy, fw, fh = max(faces[:, :4], key=lambda f: f[2] * f[3])   # biggest face = host
                    if fw > W * 0.08:
                        x = (fx + fw / 2) / W
                if x is None:
                    # no face: a screen share is mostly bright page; otherwise he just turned or covered
                    # his face, so keep the camera framing (marked "keep" and filled in below)
                    g = cv2.cvtColor(fr[int(H * .12):, :int(W * .8)], cv2.COLOR_BGR2GRAY)
                    x = None if (g > 190).mean() > 0.25 else "keep"
                samples.append((t, x))
            t += step
        # "keep" = camera without a visible face: borrow the nearest real face position
        last = next((x for _, x in samples if isinstance(x, float)), None)
        for i, (t, x) in enumerate(samples):
            if x == "keep":
                samples[i] = (t, last if last is not None else 0.5)
            elif x is not None:
                last = x
        # smooth: a single screen-looking sample between two camera samples is still camera
        for i in range(1, len(samples) - 1):
            if samples[i][1] is None and samples[i - 1][1] is not None and samples[i + 1][1] is not None:
                samples[i] = (samples[i][0], samples[i - 1][1])
        # group consecutive samples by layout; switch only after 2+ samples agree
        cur_start, cur_kind, xs, pending = a, None, [], []
        for t, x in samples:
            kind = "face" if x is not None else "screen"
            if cur_kind is None:
                cur_kind = kind
            if kind == cur_kind:
                xs.extend(v for _, v in pending if v is not None); pending = []
                if x is not None:
                    xs.append(x)
            else:
                pending.append((t, x))
                if len(pending) >= 2:
                    cut = pending[0][0] - step / 2
                    runs.append((cur_start, cut, statistics.median(xs) if cur_kind == "face" and xs else None))
                    hits += len(xs)
                    cur_start, cur_kind = cut, kind
                    xs = [v for _, v in pending if v is not None]; pending = []
        if cur_kind == "face" and not xs:
            cur_kind = "screen"
        runs.append((cur_start, b, statistics.median(xs) if cur_kind == "face" else None))
        hits += len(xs)
    return [(round(x, 3), round(y, 3), c) for x, y, c in runs if y - x > 0.05], hits


def ass_time(t):
    h, r = divmod(max(t, 0), 3600); m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(path, words, hook, total):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {OW}
PlayResY: {OH}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Poppins,150,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,10,4,2,70,70,600,1
Style: Hook,Poppins,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H28000000,1,0,0,0,100,100,0,0,3,26,0,8,80,80,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = [f"Dialogue: 1,{ass_time(0)},{ass_time(total)},Hook,,0,0,0,,{hook}"]
    # group into pages of <= 3 words; break on punctuation or a pause
    pages, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        chars = sum(len(x["w"]) + 1 for x in cur) + (len(nxt["w"]) if nxt else 0)
        if len(cur) == 3 or chars > 15 or w["w"].endswith((".", "?", "!", ",")) or not nxt or nxt["s"] - w["e"] > 0.35:
            pages.append(cur); cur = []
    for n, pg in enumerate(pages):
        nxt_start = pages[n + 1][0]["s"] if n + 1 < len(pages) else total
        for j, w in enumerate(pg):
            a = w["s"]
            # last word stays up until the next page (max 0.6 s of hold), never overlapping it
            b = pg[j + 1]["s"] if j + 1 < len(pg) else min(nxt_start, max(w["e"], w["s"] + 0.25) + 0.6)
            txt = []
            for k, x in enumerate(pg):
                word = x["w"].upper().strip(",")
                if k == j:
                    txt.append(r"{\c&H00E6FF&\fscx108\fscy108}" + word + r"{\c&HFFFFFF&\fscx100\fscy100}")
                else:
                    txt.append(word)
            ev.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Cap,,0,0,0,,{' '.join(txt)}")
    open(path, "w").write(head + "\n".join(ev) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True); ap.add_argument("--transcript", required=True)
    ap.add_argument("--clip", required=True, help="JSON: name, hook, pieces[[a,b]]")
    ap.add_argument("--out", required=True)
    ap.add_argument("--quiet", help="quiet.json (protects sound effects)")
    ap.add_argument("--crop-top", type=float, default=0.125, help="fraction of height to skip (title banner)")
    a = ap.parse_args()
    clip, tr = json.load(open(a.clip)), json.load(open(a.transcript))
    os.makedirs(a.out, exist_ok=True)
    work = tempfile.mkdtemp(dir=a.out)

    quiet = json.load(open(a.quiet)) if a.quiet else None
    keep = []
    for s, e in clip["pieces"]:
        keep += safe_cuts(tr, s, e, "short", quiet=quiet).keep
    total = sum(b - x for x, b in keep)

    cap = cv2.VideoCapture(a.video)
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    runs, nfaces = layout_runs(a.video, keep)
    keep = [(x, y) for x, y, _ in runs]
    centers = [c for _, _, c in runs]
    top = int(H * a.crop_top) // 2 * 2
    ch = (H - top) // 2 * 2
    cw = int(ch * 9 / 16) // 2 * 2
    x0s = [None if c is None else int(min(max(c * W - cw / 2, 0), W - cw)) // 2 * 2 for c in centers]
    for i in range(1, len(x0s)):          # ignore small drifts: only reframe when he really moves
        if x0s[i] is not None and x0s[i - 1] is not None and abs(x0s[i] - x0s[i - 1]) < cw * 0.15:
            x0s[i] = x0s[i - 1]

    # words on the output timeline
    words, off = [], 0.0
    for ka, kb in keep:
        for s in tr["segments"]:
            for w in s["words"]:
                if ka <= w["s"] < kb:
                    words.append({"w": w["w"], "s": off + w["s"] - ka, "e": off + min(w["e"], kb) - ka})
        off += kb - ka
    ass = os.path.join(work, "caps.ass")
    build_ass(ass, words, clip["hook"], total)

    cmd, fc, parts = ["ffmpeg", "-y"], [], []
    for i, (s, e) in enumerate(keep):
        cmd += ["-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", a.video]
        f = min(0.015, (e - s) / 4)
        if x0s[i] is not None:      # face layout: 9:16 crop around the host
            fc.append(f"[{i}:v]crop={cw}:{ch}:{x0s[i]}:{top},scale={OW}:{OH}:flags=lanczos,setsar=1,"
                      f"setpts=PTS-STARTPTS[v{i}]")
        else:                       # screen-share layout: whole frame, readable, over a blurred fill
            fc.append(f"[{i}:v]split[bg{i}][fg{i}];[bg{i}]scale=-2:{OH},crop={OW}:{OH},boxblur=24:2,"
                      f"eq=brightness=-0.15[bb{i}];[fg{i}]scale={OW}:-2:flags=lanczos[ff{i}];"
                      f"[bb{i}][ff{i}]overlay=(W-w)/2:(H-h)/2-120,setsar=1,setpts=PTS-STARTPTS[v{i}]")
        fc.append(f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS,afade=t=in:d={f:.3f},afade=t=out:st={e-s-f:.3f}:d={f:.3f}[a{i}]")
        parts.append(f"[v{i}][a{i}]")
    fc.append(f"{''.join(parts)}concat=n={len(keep)}:v=1:a=1[cv][ca]")
    fc.append(f"[cv]fps={FPS},"
              f"ass={ass}:fontsdir={FONT_DIR},format=yuv420p[v]")
    fc.append("[ca]loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    final = os.path.join(a.out, f"{clip['name']}.mp4")
    run(cmd + ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
               "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", final])
    print(json.dumps({"out": final, "seconds": round(total, 1), "pieces": len(keep), "face_hits": nfaces,
                      "crop": [cw, ch, top], "x_positions": sorted({x for x in x0s if x is not None}),
                      "screen_share_pieces": sum(x is None for x in x0s)}))


if __name__ == "__main__":
    main()
