"""Render one guest conversation as a finished long-form video.

  cold open (one 30-45 s stretch that ends on an open question, like Elijah's previews)
  -> title sequence, 10 s (your file, or a placeholder card; called "sting" in the code)
  -> the conversation with safe cuts only
  -> outro (your Patreon outro file, or a placeholder card)

Everything is cut from ONE Timeline (engine/timeline.py), frame-exact at the recording's own
frame rate, and the same Timeline writes the captions, the Resolve/Premiere XML and (later)
the chapters. Outputs, all validated before they're saved:
  <name>.mp4  <name>.srt  <name>.xml  <name>.timeline.json  <name>.cuts.json

Never loads the video into memory: each piece is decoded from a seek point.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

import export_timeline
import media
import paths
from cuts import safe_cuts
from timeline import Timeline, write_srt

CARD_SECONDS = {"sting": 10.0, "outro": 4.0}      # sting = the 10 s title sequence


_auto = None


def auto_encoder():
    """On a Mac, use the built-in hardware encoder (much faster on M1) when this FFmpeg has it."""
    global _auto
    if _auto is None:
        _auto = ""
        if sys.platform == "darwin":
            try:
                out = subprocess.run([paths.ffmpeg(), "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
                _auto = "videotoolbox" if "h264_videotoolbox" in out else ""
            except OSError:
                pass
    return _auto


def enc_args(cfg):
    enc = cfg.get("encoder") or auto_encoder()
    if enc == "videotoolbox":       # Apple Silicon Macs
        return ["-c:v", "h264_videotoolbox", "-b:v", cfg.get("bitrate", "12M")]
    if enc == "amf":                # AMD GPUs on Windows
        return ["-c:v", "h264_amf", "-quality", "quality", "-b:v", cfg.get("bitrate", "12M")]
    return ["-c:v", "libx264", "-preset", cfg.get("preset", "veryfast"), "-crf", str(cfg.get("crf", 20))]


GLITCH_SECONDS = 0.23     # Elijah's transitions: a quick white flash into a VHS-style colour split


def glitch(cfg):
    """A flash + VHS glitch over the first ~7 frames of a piece (timing untouched). "" when turned off
    with "transition": "cut" in settings."""
    if (cfg or {}).get("transition", "glitch") != "glitch":
        return ""
    g = GLITCH_SECONDS
    return (f",rgbashift=rh=-14:bh=14:enable='lt(t,{g})',noise=alls=55:allf=t:enable='lt(t,{g})',"
            f"eq=brightness=0.55:enable='lt(t,{g * 0.3:.3f})',eq=brightness=0.22:enable='between(t,{g * 0.3:.3f},{g * 0.6:.3f})'")


def norm(W, H, fps):
    return (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={media.fps_str(fps)},format=yuv420p")


def _secs(frames, fps):
    return f"{float(frames / fps):.6f}"


def card(path, text, sub, frames, W, H, fps, cfg):
    """Placeholder sting / outro card until the real file is set in Settings."""
    secs = float(frames / fps)
    font = paths.ffmpeg_filter_path(paths.CAPTION_FONT)
    fs = int(H / 12)
    vf = (f"drawtext=fontfile='{font}':text='{text}':fontcolor=white:fontsize={fs}:x=(w-tw)/2:y=(h-th)/2-{fs // 2},"
          f"drawtext=fontfile='{font}':text='{sub}':fontcolor=0xBBBBBB:fontsize={fs // 3}:x=(w-tw)/2:y=(h/2)+{fs},"
          f"fade=t=in:d=0.3,fade=t=out:st={secs - 0.3:.3f}:d=0.3" + glitch(cfg))
    media.run([paths.ffmpeg(), "-y", "-f", "lavfi", "-i", f"color=c=0x111111:s={W}x{H}:r={media.fps_str(fps)}",
               "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-vf", vf, "-frames:v", str(frames),
               "-t", f"{secs:.6f}", "-ac", "2", *enc_args(cfg), "-c:a", "pcm_s16le", path])


def file_piece(path, src, frames, W, H, fps, cfg):
    media.run([paths.ffmpeg(), "-y", "-i", src, "-vf", norm(W, H, fps) + glitch(cfg), "-af", "aresample=48000", "-ac", "2",
               "-frames:v", str(frames), "-t", _secs(frames, fps), *enc_args(cfg), "-c:a", "pcm_s16le", path])


def source_piece(path, video, segs, W, H, fps, cfg, workdir, each_clip=False):
    """Several stretches of the recording back to back. One decode pass when they're in order and
    close together (the conversation), one input per stretch otherwise (the cold open), so a preview
    clip from late in the call never means decoding everything in between.
    The transition glitch goes on the first stretch, or on every one with each_clip (the cold open).
    Frame-exact: every trim is given in whole source frames."""
    ordered = all(a["out_f"] <= b["in_f"] <= a["out_f"] + round(30 * fps) for a, b in zip(segs, segs[1:]))
    fx = glitch(cfg)
    inputs, fc, parts = [], [], []
    if ordered:
        pre_f = max(0, segs[0]["in_f"] - round(2 * fps))
        inputs = ["-ss", _secs(pre_f, fps), "-t", _secs(segs[-1]["out_f"] - pre_f + round(fps), fps), "-i", video]
        n = len(segs)
        fc.append(f"[0:v]{norm(W, H, fps)},split={n}" + "".join(f"[vs{i}]" for i in range(n)))
        fc.append(f"[0:a]aresample=48000,asplit={n}" + "".join(f"[as{i}]" for i in range(n)))
        for i, s in enumerate(segs):
            a, b = s["in_f"] - pre_f, s["out_f"] - pre_f
            g = fx if (i == 0 or each_clip) else ""
            fc.append(f"[vs{i}]trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS{g}[v{i}]")
            fc.append(_atrim(f"as{i}", a, b, fps, i))
            parts.append(f"[v{i}][a{i}]")
    else:
        for i, s in enumerate(segs):
            inputs += ["-ss", _secs(s["in_f"], fps), "-i", video]
            n_f = s["out_f"] - s["in_f"]
            g = fx if (i == 0 or each_clip) else ""
            fc.append(f"[{i}:v]{norm(W, H, fps)},trim=end_frame={n_f},setpts=PTS-STARTPTS{g}[v{i}]")
            fc.append(f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS,atrim=end={_secs(n_f, fps)},"
                      f"afade=t=in:d=0.12,afade=t=out:st={float(n_f / fps) - 0.12:.4f}:d=0.12[a{i}]")
            parts.append(f"[v{i}][a{i}]")
    fc.append(f"{''.join(parts)}concat=n={len(segs)}:v=1:a=1[v][a]")
    script = os.path.join(workdir, f"filter_{os.path.basename(path)}.txt")
    open(script, "w", encoding="utf-8").write(";\n".join(fc))
    media.run([paths.ffmpeg(), "-y", *inputs, "-filter_complex_script", script, "-map", "[v]", "-map", "[a]",
               "-ac", "2", *enc_args(cfg), "-c:a", "pcm_s16le", path])


def _atrim(label, a_f, b_f, fps, i):
    a, b = float(a_f / fps), float(b_f / fps)
    f = min(0.02, (b - a) / 4)
    return (f"[{label}]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={f:.4f},afade=t=out:st={b - a - f:.4f}:d={f:.4f}[a{i}]")


def outputs(out_dir, name):
    return {k: os.path.join(out_dir, f"{name}{ext}") for k, ext in
            (("video", ".mp4"), ("srt", ".srt"), ("xml", ".xml"), ("timeline", ".timeline.json"), ("cuts", ".cuts.json"))}


def plan_timeline(fps, transcript, item, cfg=None, quiet=None):
    """The finished video's exact sequence (cold open, sting, conversation with pauses trimmed, outro),
    without rendering anything. Used by render() and by the full preview, so both always agree."""
    cfg = cfg or {}
    rep = safe_cuts(transcript, item["start"], item["end"], "long", item.get("protect", []), quiet)
    sting = cfg.get("sting") if cfg.get("sting") and os.path.isfile(cfg["sting"]) else None
    outro = cfg.get("outro") if cfg.get("outro") and os.path.isfile(cfg["outro"]) else None
    tl = Timeline.for_longform(fps, item["cold_open"], rep.keep,
                               media.probe(sting)["duration"] if sting else CARD_SECONDS["sting"],
                               media.probe(outro)["duration"] if outro else CARD_SECONDS["outro"],
                               "sting" if sting else "card:sting", "outro" if outro else "card:outro")
    return tl, rep, sting, outro


def render(video, transcript, item, out_dir, cfg=None, quiet=None, timeline_only=False):
    """item: {name, start, end, cold_open[[a,b]], protect[[a,b]]}. Returns the outputs dict."""
    cfg = cfg or {}
    src = media.probe(video)
    W, H, fps = src["width"], src["height"], src["fps"]
    name = item["name"]
    out = outputs(out_dir, name)
    os.makedirs(out_dir, exist_ok=True)

    tl, rep, sting, outro = plan_timeline(fps, transcript, item, cfg, quiet)

    work = tempfile.mkdtemp(prefix="render-", dir=out_dir)
    try:
        if not timeline_only:
            pieces, groups = [], []
            for seg in tl.segments:          # group consecutive segments of the same kind
                if groups and groups[-1][0] == seg["kind"]:
                    groups[-1][1].append(seg)
                else:
                    groups.append((seg["kind"], [seg]))
            for i, (kind, segs) in enumerate(groups):
                p = os.path.join(work, f"{i:02d}-{kind}.mkv")
                frames = sum(s["out_f"] - s["in_f"] for s in segs)
                if segs[0]["src"] == "main":
                    source_piece(p, video, segs, W, H, fps, cfg, work, each_clip=(kind == "cold"))
                elif segs[0]["src"] == "sting":
                    file_piece(p, sting, frames, W, H, fps, cfg)
                elif segs[0]["src"] == "outro":
                    file_piece(p, outro, frames, W, H, fps, cfg)
                else:
                    label = "ELIJAH JOHNSON APOLOGETICS" if kind == "sting" else "PATREON OUTRO"
                    card(p, label, "placeholder - set your file in Settings", frames, W, H, fps, cfg)
                pieces.append(p)
            lst = os.path.join(work, "list.txt")
            open(lst, "w", encoding="utf-8").write("".join(f"file '{p}'\n" for p in pieces))
            tmp = media.tmp_name(out["video"])
            media.run([paths.ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c:v", "copy",
                       "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp])
            media.commit(tmp, out["video"])

        media.commit(write_srt(out["srt"], tl, transcript), out["srt"])
        files = {"main": video, "sting": sting, "outro": outro}
        media.commit(export_timeline.build(out["xml"], name, tl, {k: v for k, v in files.items() if v}), out["xml"])
        tl.save(out["timeline"])
        media.write_json(out["cuts"], {
            "source_minutes": round(rep.source_len / 60, 2), "output_minutes": round(tl.seconds / 60, 2),
            "removed": [{"from": x, "to": y, "seconds": round(y - x, 2), "reason": r} for x, y, r in rep.removed]})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True); ap.add_argument("--transcript", required=True)
    ap.add_argument("--plan", required=True, help="JSON: name, start, end, cold_open[[a,b]], protect[[a,b]]")
    ap.add_argument("--out", required=True); ap.add_argument("--config", default="{}")
    ap.add_argument("--quiet", help="quiet.json from cuts.detect_quiet (protects sound effects)")
    ap.add_argument("--timeline-only", action="store_true")
    a = ap.parse_args()
    quiet = json.load(open(a.quiet)) if a.quiet else None
    out = render(a.video, json.load(open(a.transcript)), json.load(open(a.plan)), a.out, json.loads(a.config),
                 quiet, a.timeline_only)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
