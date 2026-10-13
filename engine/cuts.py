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
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", wav, "-af", f"silencedetect=noise={db}dB:d={min_len}",
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
