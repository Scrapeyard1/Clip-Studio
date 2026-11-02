"""The one timeline every output agrees on: rendered video, captions, chapters, and the
Resolve/Premiere XML.

Every edit point is snapped to a whole frame of the SOURCE at its exact frame rate
(29.97 stays 30000/1001), and every position on the finished video is counted in those
frames. Seconds are only derived at the edges (frame / fps), so nothing drifts: a one-hour
29.97 fps source exports as exactly one hour.

A timeline is saved next to each long-form video as <name>.timeline.json and loaded by the
captions, chapters and XML writers, so they can't disagree with the video.
"""
import json
from fractions import Fraction


class Timeline:
    def __init__(self, fps, segments=None):
        self.fps = Fraction(fps)
        self.segments = []   # each: kind, src, in_f, out_f (source frames), at_f (output frame)
        self.frames = 0
        for s in segments or []:
            self._append(**s)

    # ---- building ----
    def _append(self, kind, src, in_f, out_f, label="", **_):
        if out_f <= in_f:
            return
        self.segments.append({"kind": kind, "src": src, "in_f": in_f, "out_f": out_f,
                              "at_f": self.frames, "label": label})
        self.frames += out_f - in_f

    def add_source(self, kind, start, end, label=""):
        """A stretch of the main recording, snapped to whole source frames."""
        self._append(kind, "main", round(Fraction(start) * self.fps), round(Fraction(end) * self.fps), label)

    def add_file(self, kind, src, seconds, label=""):
        """A separate file (sting, outro, placeholder card) lasting `seconds`, counted in timeline frames."""
        self._append(kind, src, 0, max(1, round(Fraction(seconds) * self.fps)), label)

    @classmethod
    def for_longform(cls, fps, cold_open, keep, sting_seconds, outro_seconds, sting_src="card:sting",
                     outro_src="card:outro"):
        t = cls(fps)
        for i, c in enumerate(cold_open):          # [a, b], or [a, b, n]: a tightened piece of clip n
            t.add_source("cold", c[0], c[1], f"Cold open {(c[2] if len(c) > 2 else i) + 1}")
        if sting_seconds:
            t.add_file("sting", sting_src, sting_seconds, "Title sequence")
        for a, b in keep:
            t.add_source("body", a, b)
        if outro_seconds:
            t.add_file("outro", outro_src, outro_seconds, "Patreon outro")
        return t

    # ---- reading ----
    def sec(self, frames):
        return float(Fraction(frames) / self.fps)

    @property
    def seconds(self):
        return self.sec(self.frames)

    def body_start(self):
        return self.sec(next((s["at_f"] for s in self.segments if s["kind"] == "body"), 0))

    def src_seconds(self, seg):
        return self.sec(seg["in_f"]), self.sec(seg["out_f"])

    def to_output(self, t, kinds=("cold", "body")):
        """Source time -> seconds on the finished video, or None if that moment was cut.
        A moment used in the cold open AND the body maps to the body (where chapters belong)."""
        f = Fraction(t) * self.fps
        hit = None
        for s in self.segments:
            # half a frame of slack: cut points are snapped to frames, a line starting right on one stays in
            if s["src"] == "main" and s["kind"] in kinds and s["in_f"] - Fraction(1, 2) <= f < s["out_f"]:
                hit = s["at_f"] + max(Fraction(0), f - s["in_f"])
                if s["kind"] == "body":
                    break
        return None if hit is None else float(hit / self.fps)

    def words_on_output(self, transcript):
        """Every transcript word placed on the finished video, cold-open repeats included, in order."""
        words = [w for seg in transcript["segments"] for w in seg["words"]]
        out = []
        for s in self.segments:
            if s["src"] != "main":
                continue
            a, b = self.src_seconds(s)
            base = self.sec(s["at_f"])
            slack = 0.5 / float(self.fps)
            for w in words:
                if a - slack <= w["s"] < b:
                    out.append({"w": w["w"], "s": base + max(0.0, w["s"] - a), "e": base + min(w["e"], b) - a})
        return out

    # ---- persistence ----
    def to_json(self):
        return {"fps": f"{self.fps.numerator}/{self.fps.denominator}", "frames": self.frames,
                "seconds": self.seconds, "segments": self.segments}

    def save(self, path):
        import media
        media.write_json(path, self.to_json())

    @classmethod
    def load(cls, path):
        d = json.load(open(path, encoding="utf-8"))
        t = cls(Fraction(d["fps"]))
        for s in d["segments"]:
            t._append(s["kind"], s["src"], s["in_f"], s["out_f"], s.get("label", ""))
        return t


def srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path, timeline, transcript):
    """Captions on the finished video, from the same timeline the video was cut with."""
    words = timeline.words_on_output(transcript)
    lines, line, n = [], [], 0
    for i, w in enumerate(words):
        line.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        text = " ".join(x["w"] for x in line)
        if not nxt or w["w"].endswith((".", "?", "!")) or len(text) > 38 or nxt["s"] - w["e"] > 0.8:
            n += 1
            end = max(line[-1]["e"], line[0]["s"] + 0.6)
            if nxt:
                end = min(end, nxt["s"])
            lines.append(f"{n}\n{srt_time(line[0]['s'])} --> {srt_time(end)}\n{text}\n")
            line = []
    import media
    tmp = media.tmp_name(path)
    open(tmp, "w", encoding="utf-8").write("\n".join(lines))
    return tmp


def chapter_stamp(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60:02d}:{t % 60:02d}"
