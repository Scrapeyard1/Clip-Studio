"""Safe cuts: shorten silences, never touch speech or sound effects.

A stretch can only be cut when BOTH are true:
  1. nobody is talking (no transcribed speech there), and
  2. the audio is actually quiet (below `quiet_db`, measured by ffmpeg silencedetect).
Rule 2 protects soundboard effects, music stings and laughter, which the transcript can't see.

Then (long-form defaults, see PROFILES):
  * a quiet stretch longer than `min_gap` shrinks to `keep` seconds (half before, half after)
  * one right after a question keeps up to `dramatic` seconds (the pause IS the moment)
  * ranges listed in `protect` are never cut
"""
import subprocess

import paths
from dataclasses import dataclass

PROFILES = {
    "long":  {"min_gap": 1.5, "keep": 0.5,  "dramatic": 3.0},
    "short": {"min_gap": 0.6, "keep": 0.25, "dramatic": 1.2},
}
QUIET_DB = -26


@dataclass
class CutReport:
    keep: list            # [(start, end)] in source seconds
    removed: list         # [(start, end, reason)]
    source_len: float
    output_len: float


def detect_quiet(wav, db=QUIET_DB, min_len=0.4):
    """Quiet stretches of a whole recording. One fast pass over the 16 kHz audio."""
    r = subprocess.run([paths.ffmpeg(), "-v", "info", "-i", wav, "-af", f"silencedetect=noise={db}dB:d={min_len}",
                        "-f", "null", "-"], stderr=subprocess.PIPE, text=True)
    out, s = [], None
    for line in r.stderr.splitlines():
        if "silence_start:" in line:
            s = float(line.split("silence_start:")[1].split()[0])
        elif "silence_end:" in line and s is not None:
            out.append((s, float(line.split("silence_end:")[1].split()[0]))); s = None
    return out


def _speech(transcript, start, end):
    return [(max(s["start"], start), min(s["end"], end), s["text"])
            for s in transcript["segments"] if s["end"] > start and s["start"] < end]


def safe_cuts(transcript, start, end, profile="long", protect=(), quiet=None):
    p = PROFILES[profile]
    speech = _speech(transcript, start, end)
    # candidate gaps = between transcribed speech
    gaps, prev_end, prev_text = [], start, ""
    for a, b, text in speech + [(end, end, "")]:
        if a > prev_end:
            gaps.append((prev_end, a, prev_text))
        if b > prev_end:
            prev_end, prev_text = b, text
    # keep only the parts of each gap that are actually quiet
    pieces = []
    for ga, gb, before in gaps:
        if quiet is None:
            pieces.append((ga, gb, before))
            continue
        for qa, qb in quiet:
            a, b = max(ga, qa), min(gb, qb)
            if b - a > 0:
                pieces.append((a, b, before if a - ga < 0.6 else ""))
    keep, removed, cur = [], [], start
    for a, b, before in sorted(pieces):
        allowed = p["dramatic"] if before.rstrip().endswith("?") else p["keep"]
        if b - a <= max(p["min_gap"], allowed):
            continue
        if any(x < b and y > a for x, y in protect):
            continue
        half = allowed / 2
        ca = start if a <= start + 0.01 else a + half
        cb = end if b >= end - 0.01 else b - half
        if cb - ca < 0.05 or ca < cur:
            continue
        keep.append((round(cur, 3), round(ca, 3)))
        removed.append((round(ca, 3), round(cb, 3), "long pause after a question, trimmed" if allowed > p["keep"] else "silence"))
        cur = cb
    if end > cur:
        keep.append((round(cur, 3), round(end, 3)))
    keep = [(a, b) for a, b in keep if b - a > 0.04]
    return CutReport(keep, removed, end - start, sum(b - a for a, b in keep))


# Elijah's rule for previews (cold opens): no filler words, and no dead space longer than 0.23 s.
PREVIEW_MAX_GAP = 0.23
FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "ahh", "eh", "hmm", "hm", "mm", "mmm", "mhm"}


def _plain(w):
    return "".join(ch for ch in str(w).lower() if ch.isalpha())


DEAD_DB_BELOW_SPEECH = 12.0   # a gap counts as dead air when it's this much quieter than the voices


def _frame_db(wav, a, b, frame=0.05):
    """Loudness (dB) of each 50 ms frame of a stretch of the 16 kHz mono analysis audio."""
    import wave
    import numpy as np
    with wave.open(wav) as w:
        sr = w.getframerate()
        w.setpos(max(0, min(w.getnframes(), int(a * sr))))
        x = np.frombuffer(w.readframes(max(0, int((b - a) * sr))), dtype=np.int16).astype(np.float32) / 32768
    n = int(frame * sr)
    if len(x) < n:
        return np.array([20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)]) if len(x) else np.array([-120.0])
    x = x[: len(x) // n * n].reshape(-1, n)
    return 20 * np.log10(np.sqrt((x ** 2).mean(axis=1)) + 1e-9)


def tighten_preview(transcript, start, end, max_gap=PREVIEW_MAX_GAP, audio=None):
    """A preview clip with every filler word cut out and every silence longer than max_gap shrunk
    to max_gap (half kept after the last word, half before the next). Works from word timings, so it
    catches the short pauses the silence measurement can't. With the analysis audio, a gap between
    words is only cut when it's really dead air (well below the voices in this clip): a gap with sound
    in it (words the transcriber missed, laughter, a sound effect) stays. Returns a CutReport."""
    words = [w for s in transcript.get("segments", []) for w in s.get("words", [])
             if w["e"] > start and w["s"] < end]
    words.sort(key=lambda w: w["s"])
    spoken = [(max(start, w["s"]), min(end, w["e"])) for w in words if _plain(w["w"]) not in FILLERS]
    fillers = [w for w in words if _plain(w["w"]) in FILLERS]
    if not spoken:
        return CutReport([(start, end)], [], end - start, end - start)
    half = max_gap / 2
    dead = lambda a, b: True                             # no audio to check: trust the word timings
    if audio:
        import os
        import numpy as np
        if os.path.exists(audio):
            speech = float(np.percentile(_frame_db(audio, start, end), 80))
            dead = lambda a, b: b - a < 0.06 or float(np.percentile(_frame_db(audio, a, b), 90)) < speech - DEAD_DB_BELOW_SPEECH
    keep, removed = [], []
    cur = start if spoken[0][0] - start <= max_gap or not dead(start, spoken[0][0]) else spoken[0][0] - half
    if cur > start:
        removed.append((round(start, 3), round(cur, 3), "pause before the first word"))
    for (a, b), (c, _) in zip(spoken, spoken[1:]):
        between = [f for f in fillers if b - 0.01 <= f["s"] < c]
        if c - b > max_gap and (between or dead(b, c)):   # a pause, or a filler word, between two real words
            cut_a, cut_b = b + half, c - half
            if between:                                  # none of the filler survives in the padding
                cut_a, cut_b = min(cut_a, between[0]["s"]), max(cut_b, min(c, between[-1]["e"]))
            keep.append((round(cur, 3), round(cut_a, 3)))
            had_filler = bool(between)
            removed.append((round(cut_a, 3), round(cut_b, 3), "filler word" if had_filler else "pause"))
            cur = cut_b
        elif between:                                    # filler squeezed between words with no gap
            keep.append((round(cur, 3), round(b, 3)))
            removed.append((round(b, 3), round(c, 3), "filler word"))
            cur = c
    last = spoken[-1][1]
    stop = end if end - last <= max_gap or not dead(last, end) else last + half
    keep.append((round(cur, 3), round(stop, 3)))
    if stop < end:
        removed.append((round(stop, 3), round(end, 3), "pause after the last word"))
    keep = [(a, b) for a, b in keep if b - a > 0.04]
    return CutReport(keep, removed, end - start, sum(b - a for a, b in keep))


def remap(t, keep):
    """Source time -> output time on the cut timeline (None if t was cut)."""
    acc = 0.0
    for a, b in keep:
        if t < a:
            return None
        if t <= b:
            return acc + (t - a)
        acc += b - a
    return None
