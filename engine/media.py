"""Media helpers shared by every step: probing with exact frame rates, validating outputs,
atomic writes, and running FFmpeg so a job can be cancelled."""
import json
import os
import subprocess
import threading
from fractions import Fraction

import paths


class Cancelled(Exception):
    pass


# set by the job queue for the job currently running; checked while subprocesses run
cancel_event = threading.local()


progress = threading.local()   # progress.fn(seconds): media seconds written so far in the current step


def run(cmd, timeout=None, on_line=None):
    """Run a command, killing it promptly if the current job is cancelled. Raises with FFmpeg's error.
    FFmpeg commands also report how far they've got to progress.fn, if one is set. Other programs can
    report through their output: on_line(text) gets each line they print."""
    ev = getattr(cancel_event, "ev", None)
    fn = getattr(progress, "fn", None)
    is_ffmpeg = os.path.basename(cmd[0]).lower().startswith("ffmpeg")
    if fn and is_ffmpeg:
        cmd = [cmd[0], "-progress", "pipe:1", "-nostats", *cmd[1:]]
    else:
        fn = None
    piped = bool(fn or on_line)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE if piped else subprocess.DEVNULL,
                         stderr=subprocess.PIPE, text=True)
    err, base, last = [], getattr(progress, "done", 0.0), [0.0]
    t = threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True)
    t.start()

    def read_progress():
        for line in p.stdout:
            if on_line:
                try:
                    on_line(line.rstrip("\n"))
                except Exception:
                    pass
                continue
            if line.startswith("out_time_us=") and line.strip()[12:].isdigit():
                last[0] = int(line.strip()[12:]) / 1e6
                try:
                    fn(base + last[0])
                except Exception:
                    pass
    if p.stdout:
        threading.Thread(target=read_progress, daemon=True).start()
    while True:
        try:
            p.wait(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if ev is not None and ev.is_set():
                p.kill(); p.wait()
                raise Cancelled()
    t.join(timeout=2)
    if p.returncode:
        raise RuntimeError(describe_failure(cmd[0], p.returncode, "".join(err)))
    if fn:
        progress.done = base + last[0]


def describe_failure(prog, code, stderr):
    """FFmpeg's real complaint, not its banner: keep the lines that say what went wrong."""
    name = os.path.basename(prog)
    if code < 0 or code in (137, 139, -9, -11):
        return (f"{name} was stopped by the system (code {code}). This usually means the computer ran out of "
                "memory; close other apps and press Retry.")
    lines = [l for l in stderr.splitlines() if l.strip()]
    keys = ("error", "invalid", "cannot", "could not", "no such", "failed", "not found", "unable", "mismatch")
    bad = [l for l in lines if any(k in l.lower() for k in keys)]
    return f"{name} failed (code {code}): " + " | ".join((bad or lines)[-6:])[-1500:]


def probe(path):
    """Exact stream facts. fps is a Fraction (30000/1001 stays 30000/1001, never 30)."""
    out = json.loads(subprocess.check_output(
        [paths.ffprobe(), "-v", "error", "-show_entries",
         "stream=index,codec_type,width,height,r_frame_rate,avg_frame_rate,channels,sample_rate:format=duration",
         "-of", "json", path]))
    vs = [s for s in out["streams"] if s["codec_type"] == "video"]
    aus = [s for s in out["streams"] if s["codec_type"] == "audio"]
    info = {"duration": float(out.get("format", {}).get("duration") or 0),
            "audio": [{"channels": s.get("channels", 2), "rate": int(s.get("sample_rate", 48000))} for s in aus]}
    if vs:
        v = vs[0]
        fr = v.get("r_frame_rate") or v.get("avg_frame_rate") or "30/1"
        n, d = map(int, fr.split("/"))
        info.update(width=v["width"], height=v["height"], fps=Fraction(n, d or 1))
    return info


def fps_str(fps):
    return f"{fps.numerator}/{fps.denominator}"


def valid_media(path, need_video=True, need_audio=True, min_seconds=0.5):
    """True if the file exists, decodes far enough to report a duration, and has the needed streams."""
    if not os.path.isfile(path) or os.path.getsize(path) < 1024:
        return False
    try:
        i = probe(path)
    except Exception:
        return False
    if i["duration"] < min_seconds:
        return False
    if need_video and "fps" not in i:
        return False
    if need_audio and not i["audio"]:
        return False
    return True


def valid_output(path):
    """Validation by file type, used to decide whether a step really finished."""
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return False
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".mp4", ".mkv", ".mov"):
            return valid_media(path)
        if ext == ".wav":
            return valid_media(path, need_video=False)
        if ext == ".json":
            json.load(open(path, encoding="utf-8"))
            return True
        if ext == ".xml":
            import xml.etree.ElementTree as ET
            return ET.parse(path).getroot().tag == "xmeml"
        if ext in (".srt", ".edl"):
            return "-->" in open(path, encoding="utf-8").read(4096) if ext == ".srt" else True
        if ext in (".jpg", ".png"):
            from PIL import Image
            Image.open(path).verify()
            return True
    except Exception:
        return False
    return True


def tmp_name(path):
    """Temp file next to the final one, same extension (FFmpeg picks the format from it)."""
    base, ext = os.path.splitext(path)
    return f"{base}.partial{ext}"


def commit(tmp, final):
    """Validate a finished temp file, then move it into place in one atomic step."""
    if not valid_output(tmp):
        raise RuntimeError(f"{os.path.basename(final)} came out broken or empty; it was not saved.")
    os.replace(tmp, final)


def write_json(path, data):
    tmp = tmp_name(path)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def frame(src, t, out, width=640):
    """One still of the recording at t seconds, as a JPEG (fast: seeks before decoding)."""
    tmp = tmp_name(out)
    run([paths.ffmpeg(), "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", src, "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "4", tmp])
    commit(tmp, out)
    return out


def filmstrip(src, duration, out, count=8, width=240):
    """`count` stills spread across the recording, side by side in one JPEG."""
    from PIL import Image
    import tempfile
    tiles = []
    with tempfile.TemporaryDirectory() as td:
        for k in range(count):
            t = duration * (k + 0.5) / count
            f = os.path.join(td, f"{k}.jpg")
            try:
                frame(src, t, f, width)
                tiles.append(Image.open(f).convert("RGB"))
            except Exception:
                continue
        if not tiles:
            raise RuntimeError("Couldn't read any pictures from the recording.")
        h = tiles[0].height
        strip = Image.new("RGB", (width * len(tiles), h))
        for k, im in enumerate(tiles):
            strip.paste(im.resize((width, h)), (k * width, 0))
        tmp = tmp_name(out)
        strip.save(tmp, "JPEG", quality=82)
    commit(tmp, out)
    return out
