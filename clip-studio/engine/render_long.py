"""Render one guest conversation as a finished long-form video.

  cold open (~30 s of clips from later in the talk)
  -> sting (your file, or a placeholder card)
  -> the conversation with safe cuts only
  -> outro (your Patreon outro file, or a placeholder card)

Also writes:
  * <name>.srt  captions on the final timeline (upload to YouTube as a caption track)
  * <name>.edl  CMX3600 edit list of the body cuts, opens in Premiere / DaVinci Resolve
  * <name>.cuts.json  every removed stretch with its reason, for the review queue

Never loads the video into memory: each piece is decoded from a seek point,
so a 5 GB source costs no more RAM than a 500 MB one.
"""
import argparse, json, os, subprocess, tempfile
from cuts import safe_cuts, remap
import export_timeline

FONT = "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"


def probe(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                   "stream=width,height,r_frame_rate", "-of", "json", path])
    s = json.loads(out)["streams"][0]
    n, d = map(int, s["r_frame_rate"].split("/"))
    return s["width"], s["height"], n / d


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def enc_args(cfg):
    if cfg.get("encoder") == "videotoolbox":       # Apple Silicon Macs
        return ["-c:v", "h264_videotoolbox", "-b:v", cfg.get("bitrate", "12M")]
    if cfg.get("encoder") == "amf":                # AMD GPUs on Windows
        return ["-c:v", "h264_amf", "-quality", "quality", "-b:v", cfg.get("bitrate", "12M")]
    return ["-c:v", "libx264", "-preset", cfg.get("preset", "veryfast"), "-crf", str(cfg.get("crf", 20))]


def norm(W, H, fps):
    return f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p"


def card(path, text, sub, secs, W, H, fps, cfg, riser=False):
    """Placeholder sting / outro card until the real file is supplied."""
    fs = int(H / 12)
    vf = (f"drawtext=fontfile={FONT}:text='{text}':fontcolor=white:fontsize={fs}:x=(w-tw)/2:y=(h-th)/2-{fs//2},"
          f"drawtext=fontfile={FONT}:text='{sub}':fontcolor=0xBBBBBB:fontsize={fs//3}:x=(w-tw)/2:y=(h/2)+{fs},"
          f"fade=t=in:d=0.3,fade=t=out:st={secs-0.3}:d=0.3")
    a = (f"aevalsrc='0.12*sin(2*PI*(200+600*t/{secs})*t)*min(1,t*4)*min(1,({secs}-t)*3)':s=48000:d={secs}"
         if riser else f"anullsrc=r=48000:cl=stereo:d={secs}")
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x111111:s={W}x{H}:r={fps}:d={secs}", "-f", "lavfi", "-i", a,
         "-vf", vf, "-ac", "2", *enc_args(cfg), "-c:a", "pcm_s16le", "-shortest", path])


def file_piece(path, src, W, H, fps, cfg):
    run(["ffmpeg", "-y", "-i", src, "-vf", norm(W, H, fps), "-af", "aresample=48000", "-ac", "2",
         *enc_args(cfg), "-c:a", "pcm_s16le", path])


def clips_piece(path, video, clips, W, H, fps, cfg):
    """Several short clips back to back, each with 0.12 s audio fades so joins never click."""
    cmd, parts = ["ffmpeg", "-y"], []
    for a, b in clips:
        cmd += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", video]
    fc = []
    for i, (a, b) in enumerate(clips):
        d = b - a
        fc.append(f"[{i}:v]{norm(W, H, fps)},setpts=PTS-STARTPTS[v{i}]")
        fc.append(f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS,afade=t=in:d=0.12,afade=t=out:st={d-0.12:.3f}:d=0.12[a{i}]")
        parts.append(f"[v{i}][a{i}]")
    fc.append(f"{''.join(parts)}concat=n={len(clips)}:v=1:a=1[v][a]")
    run(cmd + ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]", "-ac", "2",
               *enc_args(cfg), "-c:a", "pcm_s16le", path])


def body_piece(path, video, start, end, keep, W, H, fps, cfg, workdir):
    """The conversation with silences removed: one decode pass, trim + concat per kept stretch."""
    pre = max(0.0, start - 2.0)
    fc = [f"[0:v]{norm(W, H, fps)},split={len(keep)}" + "".join(f"[vs{i}]" for i in range(len(keep))),
          "[0:a]aresample=48000,asplit=" + str(len(keep)) + "".join(f"[as{i}]" for i in range(len(keep)))]
    parts = []
    for i, (a, b) in enumerate(keep):
        a0, b0 = a - pre, b - pre
        f = min(0.02, (b - a) / 4)
        fc.append(f"[vs{i}]trim=start={a0:.3f}:end={b0:.3f},setpts=PTS-STARTPTS[v{i}]")
        fc.append(f"[as{i}]atrim=start={a0:.3f}:end={b0:.3f},asetpts=PTS-STARTPTS,"
                  f"afade=t=in:d={f:.3f},afade=t=out:st={b-a-f:.3f}:d={f:.3f}[a{i}]")
        parts.append(f"[v{i}][a{i}]")
    fc.append(f"{''.join(parts)}concat=n={len(keep)}:v=1:a=1[v][a]")
    script = os.path.join(workdir, "body_filter.txt")
    open(script, "w").write(";\n".join(fc))
    run(["ffmpeg", "-y", "-ss", f"{pre:.3f}", "-t", f"{end - pre + 1:.3f}", "-i", video,
         "-filter_complex_script", script, "-map", "[v]", "-map", "[a]", "-ac", "2",
         *enc_args(cfg), "-c:a", "pcm_s16le", path])


def ts(t):
    h, r = divmod(t, 3600); m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")


def tc(t, fps):
    f = int(round(t * fps)); fr = int(round(fps))
    return f"{f // (3600*fr):02d}:{f // (60*fr) % 60:02d}:{f // fr % 60:02d}:{f % fr:02d}"


def write_srt(path, transcript, pieces):
    """pieces: [(out_offset, [(src_a, src_b)], ...)] -> caption lines on the final timeline."""
    words = [w for s in transcript["segments"] for w in s["words"]]
    lines, n = [], 0
    for off, keep in pieces:
        lo, hi = keep[0][0], keep[-1][1]
        chunk = []
        for w in words:
            if w["s"] < lo or w["s"] > hi:
                continue
            t = remap(w["s"], keep)
            if t is None:
                continue
            chunk.append((off + t, off + (remap(min(w["e"], hi), keep) or t + 0.3), w["w"]))
        line = []
        for i, (a, b, w) in enumerate(chunk):
            line.append((a, b, w))
            brk = w.endswith((".", "?", "!")) or len(" ".join(x[2] for x in line)) > 38 or \
                  (i + 1 < len(chunk) and chunk[i + 1][0] - b > 0.8)
            if brk or i == len(chunk) - 1:
                n += 1
                lines.append(f"{n}\n{ts(line[0][0])} --> {ts(max(line[-1][1], line[0][0] + 0.6))}\n"
                             f"{' '.join(x[2] for x in line)}\n")
                line = []
    open(path, "w").write("\n".join(lines))


def write_edl(path, title, keep, fps, rec_start):
    out = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    rec = rec_start
    for i, (a, b) in enumerate(keep, 1):
        d = b - a
        out.append(f"{i:03d}  AX       AA/V  C        {tc(a, fps)} {tc(b, fps)} {tc(rec, fps)} {tc(rec + d, fps)}")
        out.append("* FROM CLIP NAME: SOURCE")
        rec += d
    open(path, "w").write("\n".join(out) + "\n")


def media_len(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "csv=p=0", path]))


def write_timeline(a, cfg, plan, rep, name):
    """Editable timeline for DaVinci Resolve / Premiere Pro, cut against the original recording."""
    segs = [{"src": "main", "in": x, "out": y, "label": f"Cold open {i + 1}"} for i, (x, y) in enumerate(plan["cold_open"])]
    extra = {}
    if cfg.get("sting"):
        extra["sting"] = cfg["sting"]
        segs.append({"src": "sting", "in": 0, "out": media_len(cfg["sting"]), "label": "Sting"})
    segs += [{"src": "main", "in": x, "out": y, "label": name} for x, y in rep.keep]
    if cfg.get("outro"):
        extra["outro"] = cfg["outro"]
        segs.append({"src": "outro", "in": 0, "out": media_len(cfg["outro"]), "label": "Patreon outro"})
    cold = sum(y - x for x, y in plan["cold_open"])
    body0 = cold + (media_len(cfg["sting"]) if cfg.get("sting") else 0)
    marks = [(0, "Cold open"), (body0, "Conversation starts")]
    for x, y, why in rep.removed:
        t = remap(x - 0.001, rep.keep)
        if t is not None:
            marks.append((body0 + t, f"Trimmed {y - x:.1f}s ({why}) - drag edge to restore"))
    export_timeline.build(os.path.join(a.out, f"{name}.xml"), name, a.video, segs, extra, marks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True); ap.add_argument("--transcript", required=True)
    ap.add_argument("--plan", required=True, help="JSON: name, start, end, cold_open[[a,b]], protect[[a,b]]")
    ap.add_argument("--out", required=True); ap.add_argument("--config", default="{}")
    ap.add_argument("--quiet", help="quiet.json from cuts.detect_quiet (protects sound effects)")
    ap.add_argument("--timeline-only", action="store_true", help="write the Resolve/Premiere timeline, skip rendering")
    a = ap.parse_args()
    cfg = json.loads(a.config)
    plan = json.load(open(a.plan))
    tr = json.load(open(a.transcript))
    W, H, fps = probe(a.video)
    fps = round(fps)
    os.makedirs(a.out, exist_ok=True)
    work = tempfile.mkdtemp(dir=a.out)
    name = plan["name"]

    quiet = json.load(open(a.quiet)) if a.quiet else None
    rep = safe_cuts(tr, plan["start"], plan["end"], "long", plan.get("protect", []), quiet)
    print(f"body {rep.source_len/60:.1f} min -> {rep.output_len/60:.1f} min, {len(rep.removed)} cuts")

    write_timeline(a, cfg, plan, rep, name)
    if a.timeline_only:
        return

    p_cold, p_sting = os.path.join(work, "1.mkv"), os.path.join(work, "2.mkv")
    p_body, p_outro = os.path.join(work, "3.mkv"), os.path.join(work, "4.mkv")
    clips_piece(p_cold, a.video, plan["cold_open"], W, H, fps, cfg)
    if cfg.get("sting"):
        file_piece(p_sting, cfg["sting"], W, H, fps, cfg)
    else:
        card(p_sting, "ELIJAH JOHNSON APOLOGETICS", "placeholder sting - swap in your file", 3, W, H, fps, cfg, riser=True)
    body_piece(p_body, a.video, plan["start"], plan["end"], rep.keep, W, H, fps, cfg, work)
    if cfg.get("outro"):
        file_piece(p_outro, cfg["outro"], W, H, fps, cfg)
    else:
        card(p_outro, "PATREON OUTRO", "placeholder - your outro file plays here", 4, W, H, fps, cfg)

    lst = os.path.join(work, "list.txt")
    open(lst, "w").write("".join(f"file '{p}'\n" for p in (p_cold, p_sting, p_body, p_outro)))
    final = os.path.join(a.out, f"{name}.mp4")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", final])

    cold_len = sum(b - x for x, b in plan["cold_open"])
    sting_len = media_len(cfg["sting"]) if cfg.get("sting") else 3.0
    write_srt(os.path.join(a.out, f"{name}.srt"), tr,
              [(sum(b - x for x, b in plan["cold_open"][:i]), [tuple(c)]) for i, c in enumerate(plan["cold_open"])] +
              [(cold_len + sting_len, rep.keep)])
    write_edl(os.path.join(a.out, f"{name}.edl"), name, rep.keep, fps, 3600.0)
    json.dump({"source_minutes": round(rep.source_len / 60, 2), "output_minutes": round(rep.output_len / 60, 2),
               "removed": [{"from": x, "to": y, "seconds": round(y - x, 2), "reason": r} for x, y, r in rep.removed]},
              open(os.path.join(a.out, f"{name}.cuts.json"), "w"), indent=1)
    for p in (p_cold, p_sting, p_body, p_outro, lst):
        os.remove(p)
    print("wrote", final)


if __name__ == "__main__":
    main()
