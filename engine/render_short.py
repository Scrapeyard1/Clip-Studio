"""Render a vertical short (1080x1920) from one or more pieces of a live.

  * tighter safe cuts than long-form (pauses over 0.6 s shrink to 0.25 s)
  * auto-framing: finds your face across the clip and crops 9:16 around it,
    below the stream's title banner and clear of the chat / donation overlays
  * animated word-by-word captions (current word highlighted), Poppins Bold
  * hook text pinned at the top for the whole clip
  * framing per short: "auto" (host crop, switching to the full frame for screen shares),
    "host" (always the host crop), "full" (whole frame: Scripture on screen), "split"
    (whole frame on top, host crop underneath), "guest" (for audio-only callers: host or the shared
    screen in a gold-edged box up top, captions in the middle, the caller's photo in a circle with
    an audio-driven ring and their @handle at the bottom, over a blurred backdrop of the live)
"""
import argparse, json, os, shutil, statistics, tempfile
import cv2
import media
import paths
from cuts import safe_cuts
from timeline import Timeline

OW, OH, FPS = 1080, 1920, 30
FRAMINGS = ("auto", "host", "full", "split", "guest")
# caller layout (1080x1920)
PANEL_W, PANEL_H, PANEL_Y = 960, 720, 330
BADGE_X, BADGE_Y = (OW - 760) // 2, 1170


def layout_runs(video, pieces, step=0.4, detect_screens=True):
    """Split kept stretches wherever the picture switches between camera and screen share.
    Returns [(start, end, face_x or None)]: face_x is the host's face centre (fraction of width),
    None means screen share (show the whole frame)."""
    cap = cv2.VideoCapture(video)
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    det = cv2.FaceDetectorYN.create(paths.YUNET, "", (W, H), 0.7)
    runs, hits = [], 0
    for a, b in pieces:
        samples, t = [], a + 0.05
        while t < b:
            ev = getattr(media.cancel_event, "ev", None)
            if ev is not None and ev.is_set():          # Stop works during face scanning too
                cap.release()
                raise media.Cancelled()
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
                    x = None if detect_screens and (g > 190).mean() > 0.25 else "keep"
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


def build_ass(path, words, hook, total, layout="full"):
    cap = "Style: Cap,Poppins,150,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,10,4,2,70,70,600,1"
    hk = "Style: Hook,Poppins,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H28000000,1,0,0,0,100,100,0,0,3,26,0,8,80,80,200,1"
    if layout == "guest":     # captions between the host box and the caller badge; hook above the box
        cap = "Style: Cap,Poppins,100,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,8,3,2,60,60,712,1"
        hk = "Style: Hook,Poppins,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H28000000,1,0,0,0,100,100,0,0,3,22,0,8,70,70,96,1"
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {OW}
PlayResY: {OH}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{cap}
{hk}

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
    open(path, "w", encoding="utf-8").write(head + "\n".join(ev) + "\n")


def _piece_filter(i, x0, mode, cw, ch, top, W, H):
    if mode == "guest":         # blurred live behind; host (or the shared screen) in a gold-edged box
        bg = (f"[{i}:v]split[bg{i}][fg{i}];[bg{i}]scale={OW}:{OH}:force_original_aspect_ratio=increase,"
              f"crop={OW}:{OH},boxblur=28:2,eq=brightness=-0.28:saturation=1.15[bb{i}];")
        if x0 is not None:
            ph = int(ch * 0.8) // 2 * 2                    # a little closer: keeps chat/donation edges out
            pw = min(int(ph * 4 / 3) // 2 * 2, W)
            px = int(min(max(x0 + cw / 2 - pw / 2, 0), W - pw)) // 2 * 2
            panel = f"[fg{i}]crop={pw}:{ph}:{px}:{top},scale={PANEL_W}:{PANEL_H}:flags=lanczos"
            py = PANEL_Y
        else:
            sh = int(PANEL_W * H / W) // 2 * 2
            panel = f"[fg{i}]scale={PANEL_W}:{sh}:flags=lanczos"
            py = PANEL_Y + max(0, (PANEL_H - sh) // 2)
        return (bg + panel + f",pad=iw+12:ih+12:6:6:color=0xFFC83A[pp{i}];"
                f"[bb{i}][pp{i}]overlay=(W-w)/2:{py - 6},setsar=1,setpts=PTS-STARTPTS[v{i}]")
    if mode == "split":         # whole frame on top, host underneath
        fh = int(OW * H / W) // 2 * 2
        bh = OH - fh
        bw = int(ch * OW / bh) // 2 * 2
        bx = int(min(max((x0 if x0 is not None else (W - bw) / 2) + cw / 2 - bw / 2, 0), W - bw)) // 2 * 2
        return (f"[{i}:v]split[t{i}][b{i}];[t{i}]scale={OW}:{fh}:flags=lanczos[tt{i}];"
                f"[b{i}]crop={min(bw, W)}:{ch}:{bx}:{top},scale={OW}:{bh}:flags=lanczos[bb{i}];"
                f"[tt{i}][bb{i}]vstack,setsar=1,setpts=PTS-STARTPTS[v{i}]")
    if x0 is not None:          # host crop, 9:16
        return (f"[{i}:v]crop={cw}:{ch}:{x0}:{top},scale={OW}:{OH}:flags=lanczos,setsar=1,"
                f"setpts=PTS-STARTPTS[v{i}]")
    # whole frame, readable, over a blurred fill (screen share / Scripture)
    return (f"[{i}:v]split[bg{i}][fg{i}];[bg{i}]scale=-2:{OH},crop={OW}:{OH},boxblur=24:2,"
            f"eq=brightness=-0.15[bb{i}];[fg{i}]scale={OW}:-2:flags=lanczos[ff{i}];"
            f"[bb{i}][ff{i}]overlay=(W-w)/2:(H-h)/2-120,setsar=1,setpts=PTS-STARTPTS[v{i}]")


def keep_ranges(transcript, clip, quiet=None):
    """What the short actually plays: each piece in order, with its pauses tightened. Used by render()
    and by the full preview, so both always agree."""
    keep = []
    for s, e in clip["pieces"]:
        keep += safe_cuts(transcript, s, e, "short", clip.get("protect", []), quiet).keep
    return keep


def render(video, transcript, clip, out_dir, quiet=None, crop_top=0.125, guest=None):
    """clip: {name, hook, pieces[[a,b]], framing}. Returns {"video": path, ...stats}.
    guest (for the "guest" framing): {name, handle, photo, track} - photo may be None (initials are used)."""
    os.makedirs(out_dir, exist_ok=True)
    mode = clip.get("framing", "auto")
    if mode not in FRAMINGS:
        mode = "auto"
    src = media.probe(video)
    W, H = src["width"], src["height"]
    keep = keep_ranges(transcript, clip, quiet)
    if mode == "full":
        runs, nfaces = [(a, b, None) for a, b in keep], 0
    else:
        runs, nfaces = layout_runs(video, keep, detect_screens=(mode in ("auto", "guest")))
    top = int(H * crop_top) // 2 * 2
    ch = (H - top) // 2 * 2
    cw = int(ch * 9 / 16) // 2 * 2
    x0s = [None if c is None else int(min(max(c * W - cw / 2, 0), W - cw)) // 2 * 2 for _, _, c in runs]
    for i in range(1, len(x0s)):          # ignore small drifts: only reframe when he really moves
        if x0s[i] is not None and x0s[i - 1] is not None and abs(x0s[i] - x0s[i - 1]) < cw * 0.15:
            x0s[i] = x0s[i - 1]

    tl = Timeline(src["fps"])
    for a, b, _ in runs:
        tl.add_source("body", a, b)
    total = tl.seconds
    work = tempfile.mkdtemp(prefix="short-", dir=out_dir)
    try:
        ass = os.path.join(work, "caps.ass")
        build_ass(ass, tl.words_on_output(transcript), clip["hook"], total, "guest" if mode == "guest" else "full")
        # 1) each piece on its own, so memory stays flat however long the short is
        listing = []
        for i, seg in enumerate(tl.segments):
            a, b = tl.src_seconds(seg)
            f = min(0.015, (b - a) / 4)
            fc = [_piece_filter(0, x0s[i], mode, cw, ch, top, W, H).replace("[v0]", "[v]"),
                  f"[0:a]aresample=48000,asetpts=PTS-STARTPTS,afade=t=in:d={f:.3f},"
                  f"afade=t=out:st={b - a - f:.3f}:d={f:.3f}[a]"]
            script = os.path.join(work, f"piece{i}.txt")
            open(script, "w", encoding="utf-8").write(";\n".join(fc))
            piece = os.path.join(work, f"piece{i:03d}.mkv")
            media.run([paths.ffmpeg(), "-y", "-ss", f"{a:.6f}", "-t", f"{b - a:.6f}", "-i", video,
                       "-filter_complex_script", script, "-map", "[v]", "-map", "[a]",
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p",
                       "-c:a", "pcm_s16le", "-ac", "2", "-ar", "48000", piece])
            listing.append("file '" + piece.replace("\\", "/").replace("'", "'\\''") + "'")
        lst = os.path.join(work, "pieces.txt")
        open(lst, "w", encoding="utf-8").write("\n".join(listing) + "\n")

        # 2) join them, add the caller badge (if any), captions and loudness in one final pass
        cmd = [paths.ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", lst]
        fc = []
        if mode == "guest":
            import caller
            g = guest or {}
            track = int(g.get("track") or 0)
            if track >= len(src["audio"]):
                track = 0
            levels = caller.bands(caller.audio_for(video, tl, track, work), int(round(total * FPS)) + 1, FPS)
            badge = caller.render_badge(os.path.join(work, "badge.mov"), g.get("photo"), g.get("name") or "",
                                        g.get("handle") or "", levels, FPS)
            cmd += ["-i", badge]
            fc.append(f"[0:v][1:v]overlay={BADGE_X}:{BADGE_Y}:eof_action=pass:format=auto[cvb]")
            vin = "cvb"
        else:
            vin = "0:v"
        fc.append(f"[{vin}]fps={FPS},ass='{paths.ffmpeg_filter_path(ass)}':fontsdir='{paths.ffmpeg_filter_path(paths.FONTS)}',"
                  f"format=yuv420p[v]")
        fc.append("[0:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]")
        script = os.path.join(work, "filter.txt")
        open(script, "w", encoding="utf-8").write(";\n".join(fc))
        final = os.path.join(out_dir, f"{clip['name']}.mp4")
        tmp = media.tmp_name(final)
        media.run(cmd + ["-filter_complex_script", script, "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
                         "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                         "-movflags", "+faststart", tmp])
        media.commit(tmp, final)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"video": final, "seconds": round(total, 1), "pieces": len(tl.segments), "face_hits": nfaces,
            "framing": mode, "screen_share_pieces": sum(x is None for x in x0s)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True); ap.add_argument("--transcript", required=True)
    ap.add_argument("--clip", required=True, help="JSON: name, hook, pieces[[a,b]], framing")
    ap.add_argument("--out", required=True)
    ap.add_argument("--quiet", help="quiet.json (protects sound effects)")
    ap.add_argument("--crop-top", type=float, default=0.125, help="fraction of height to skip (title banner)")
    a = ap.parse_args()
    quiet = json.load(open(a.quiet)) if a.quiet else None
    print(json.dumps(render(a.video, json.load(open(a.transcript)), json.load(open(a.clip)), a.out, quiet, a.crop_top)))


if __name__ == "__main__":
    main()
