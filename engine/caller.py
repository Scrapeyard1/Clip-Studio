"""The caller badge for audio-only guests: their photo in a gold ring, a ring of bars that moves
with the audio, and their @handle on a tag. Rendered as a transparent video that render_short
lays over the short.

The bars follow the audio's frequency bands frame by frame (not a canned animation). With a
recording that has the caller on their own audio track (OBS: desktop audio), pass that track
and the bars move only when the caller talks; on a mixed track they move with every voice.
"""
import math
import os
import subprocess

import cv2
import numpy as np

import media
import paths

BADGE = 760                 # badge canvas, square
CX, CY, R = 380, 370, 220   # circle centre and photo radius inside the canvas
NBARS, NBANDS = 72, 24
GOLD = (58, 200, 255)       # BGR of #FFC83A
NIGHT = (32, 20, 13)        # BGR of #0D1420
SR = 16000


def audio_for(video, timeline, track=0, workdir=None):
    """Mono 16 kHz samples of exactly what the short plays, piece by piece."""
    parts = []
    for seg in timeline.segments:
        a, b = timeline.src_seconds(seg)
        n = int(round((b - a) * SR))
        raw = os.path.join(workdir or _tmpdir(), f"seg{len(parts)}.raw")
        media.run([paths.ffmpeg(), "-v", "error", "-y", "-ss", f"{a:.6f}", "-t", f"{b - a:.6f}", "-i", video,
                   "-map", f"0:a:{track}", "-ac", "1", "-ar", str(SR), "-f", "s16le", raw])
        x = np.fromfile(raw, np.int16).astype(np.float32) / 32768.0
        os.remove(raw)
        parts.append(np.pad(x, (0, max(0, n - len(x))))[:n])
    return np.concatenate(parts) if parts else np.zeros(1, np.float32)


_TMP = None


def _tmpdir():
    global _TMP
    if _TMP is None or not os.path.isdir(_TMP):
        import tempfile
        _TMP = tempfile.mkdtemp(prefix="caller-")
    return _TMP


def bands(samples, frames, fps=30):
    """frames x NBANDS levels in 0..1, smoothed like a real meter (fast rise, slower fall)."""
    win = 1024
    hann = np.hanning(win).astype(np.float32)
    freqs = np.fft.rfftfreq(win, 1 / SR)
    edges = np.geomspace(90, 5000, NBANDS + 1)
    idx = [np.where((freqs >= edges[k]) & (freqs < edges[k + 1]))[0] for k in range(NBANDS)]
    idx = [i if len(i) else np.array([np.argmin(abs(freqs - edges[k]))]) for k, i in enumerate(idx)]
    padded = np.pad(samples, (win, win))
    E = np.zeros((frames, NBANDS), np.float32)
    rms = np.zeros(frames, np.float32)
    for f in range(frames):
        c = int(f / fps * SR) + win
        w = padded[c - win // 2:c + win // 2] * hann
        spec = np.abs(np.fft.rfft(w))
        E[f] = [20 * np.log10(spec[i].mean() + 1e-6) for i in idx]
        rms[f] = np.sqrt(np.mean(w ** 2))
    top = np.percentile(E, 97) if frames else 0
    L = np.clip((E - (top - 42)) / 42, 0, 1) ** 1.6
    gate = np.clip((20 * np.log10(rms + 1e-6) + 52) / 12, 0, 1)      # silence -> bars rest
    L *= gate[:, None]
    out = np.zeros_like(L)
    prev = np.zeros(NBANDS, np.float32)
    for f in range(frames):
        cur = L[f]
        prev = np.where(cur > prev, prev + (cur - prev) * 0.7, prev * 0.82 + cur * 0.18)
        out[f] = prev
    return out


def _circle_photo(photo_path, name, workdir=None):
    """The static top layer: photo in a circle, gold ring, soft shadow, name tag. BGRA float, premultiplied."""
    if photo_path and os.path.isfile(photo_path):
        img = cv2.imread(photo_path, cv2.IMREAD_COLOR)
    else:
        import guests
        ph = os.path.join(workdir or _tmpdir(), "placeholder.png")
        img = cv2.imread(guests.placeholder(name, ph), cv2.IMREAD_COLOR)
    d = 2 * R
    img = cv2.resize(img, (d, d), interpolation=cv2.INTER_AREA)
    ss = 4                                                     # anti-aliased circle mask
    m = np.zeros((d * ss, d * ss), np.uint8)
    cv2.circle(m, (d * ss // 2, d * ss // 2), d * ss // 2 - ss, 255, -1, cv2.LINE_AA)
    m = cv2.resize(m, (d, d), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    layer = np.zeros((BADGE, BADGE, 4), np.float32)
    # shadow
    sh = np.zeros((BADGE, BADGE), np.float32)
    cv2.circle(sh, (CX, CY + 10), R + 14, 1.0, -1, cv2.LINE_AA)
    sh = cv2.GaussianBlur(sh, (0, 0), 16) * 0.55
    layer[..., 3] = sh
    # photo
    y0, x0 = CY - R, CX - R
    a = m[..., None]
    layer[y0:y0 + d, x0:x0 + d, :3] = img.astype(np.float32) / 255 * a + layer[y0:y0 + d, x0:x0 + d, :3] * (1 - a)
    layer[y0:y0 + d, x0:x0 + d, 3] = m + layer[y0:y0 + d, x0:x0 + d, 3] * (1 - m)
    # gold ring
    ring = np.zeros((BADGE, BADGE, 4), np.float32)
    cv2.circle(ring, (CX, CY), R + 4, (*[c / 255 for c in GOLD], 1.0), 8, cv2.LINE_AA)
    return ring + layer * (1 - ring[..., 3:4])                 # everything here is premultiplied


def _tag(layer, handle):
    """@handle on a dark pill with a gold edge, overlapping the bottom of the circle."""
    if not handle:
        return layer
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(os.path.join(paths.FONTS, "Poppins-Bold.ttf"), 36)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    bx = probe.textbbox((0, 0), handle, font=font)
    tw, th = bx[2] - bx[0], bx[3] - bx[1]
    pw, ph = min(tw + 56, BADGE - 20), 64
    pill = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    dr = ImageDraw.Draw(pill)
    dr.rounded_rectangle((1, 1, pw - 2, ph - 2), radius=ph // 2, fill=(13, 20, 32, 240), outline=(255, 200, 58, 255),
                         width=3)
    dr.text(((pw - tw) / 2 - bx[0], (ph - th) / 2 - bx[1]), handle, font=font, fill=(255, 255, 255, 255))
    p = np.asarray(pill).astype(np.float32) / 255
    p = np.dstack([p[..., 2::-1] * p[..., 3:4], p[..., 3]])      # RGBA -> premultiplied BGRA
    x0, y0 = CX - pw // 2, CY + R - 26
    reg = layer[y0:y0 + ph, x0:x0 + pw]
    layer[y0:y0 + ph, x0:x0 + pw] = p + reg * (1 - p[..., 3:4])
    return layer


def render_badge(out_path, photo, name, handle, levels, fps=30):
    workdir = os.path.dirname(os.path.abspath(out_path))
    """Write the badge as a transparent .mov (QuickTime Animation). levels: frames x NBANDS."""
    top = _tag(_circle_photo(photo, name, workdir), handle)
    frames = len(levels)
    angles = [(-90 + k * 360 / NBARS) * math.pi / 180 for k in range(NBARS)]
    # each bar reads a band: mirrored so the ring is symmetric, low voices at the bottom
    pos = [min(k, NBARS - k) / (NBARS / 2) for k in range(NBARS)]
    band_of = [int(round((1 - p) * (NBANDS - 1))) for p in pos]
    jitter = np.random.default_rng(7).uniform(0.75, 1.0, NBARS)
    r0 = R + 18
    cmd = [paths.ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{BADGE}x{BADGE}",
           "-r", str(fps), "-i", "pipe:0", "-c:v", "qtrle", "-pix_fmt", "argb", out_path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    ev = getattr(media.cancel_event, "ev", None)
    gold = (*[c / 255 for c in GOLD], 1.0)
    try:
        for f in range(frames):
            if ev is not None and ev.is_set():
                p.kill()
                raise media.Cancelled()
            bars = np.zeros((BADGE, BADGE, 4), np.float32)
            glow = np.zeros((BADGE // 2, BADGE // 2, 4), np.float32)
            lv = levels[f]
            for k, ang in enumerate(angles):
                ln = 6 + 84 * float(lv[band_of[k]]) * jitter[k]
                c, s = math.cos(ang), math.sin(ang)
                p1 = (int(CX + c * r0), int(CY + s * r0))
                p2 = (int(CX + c * (r0 + ln)), int(CY + s * (r0 + ln)))
                cv2.line(bars, p1, p2, gold, 9, cv2.LINE_AA)
                cv2.line(glow, (p1[0] // 2, p1[1] // 2), (p2[0] // 2, p2[1] // 2), gold, 7, cv2.LINE_AA)
            glow = cv2.resize(cv2.GaussianBlur(glow, (0, 0), 5), (BADGE, BADGE)) * (0.35 + 0.5 * float(lv.mean()))
            base = bars + glow * (1 - bars[..., 3:4])
            out = top + base * (1 - top[..., 3:4])
            a = np.clip(out[..., 3:4], 1e-4, 1)
            straight = np.dstack([np.clip(out[..., :3] / a, 0, 1), np.clip(out[..., 3], 0, 1)])
            p.stdin.write((straight * 255).round().astype(np.uint8).tobytes())
        p.stdin.close()
        p.wait()
    except BrokenPipeError:
        p.wait()
    if p.returncode:
        raise RuntimeError("Caller badge failed: " + p.stderr.read().decode(errors="replace")[-800:])
    return out_path
