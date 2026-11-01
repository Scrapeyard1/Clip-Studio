"""Checks an AI plan before anything expensive runs.

validate(plan, duration, transcript=None) returns (clean_plan, problems). Problems are plain
sentences the review screen shows next to the item, so a person can fix the times or drop the item.
A plan with no usable conversation AND no usable short raises PlanError.

With the transcript it also:
  * moves any cut that lands inside a word to that word's edge,
  * verifies every quoted self-defeat against the words actually said near that time (correcting the
    time if it's a little off) and drops any it can't find, so nothing is claimed without evidence.

structure_notes(item) says which of the three beats (setup, clash, turn) a short or cold open is
missing. It's worked out live from the item, so it stays right after edits.
"""
import bisect
import math
import re

MAX_COLD_OPEN = 45.0      # seconds; the target is ~30
EXTEND = 6.0              # a quoted self-defeat this close outside a clip pulls the clip edge out to it
SHORT_RANGE = (8.0, 180.0)
CATEGORIES = {"Self-Defeat", "Roast", "Argument Win", "Explainer", "Testimony", "Pastoral"}
DEBATE = {"Self-Defeat", "Roast", "Argument Win"}
BEATS = {"setup", "clash", "turn", "payoff"}
SPEAKERS = {"caller", "elijah", "both", "unsure"}
DEFEAT_KINDS = {"contradiction", "admission", "double standard", "undercuts itself"}


class PlanError(Exception):
    pass


def _num(x):
    try:
        x = float(x)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _span(v, duration):
    """A [start, end] pair inside the recording, or None."""
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    a, b = _num(v[0]), _num(v[1])
    if a is None or b is None or not (0 <= a < b <= duration + 0.5):
        return None
    return [round(a, 3), round(min(b, duration), 3)]


def _clip(v, duration):
    """A clip given as [a, b] or {"start", "end", "beat", "speaker"} -> (span, {"beat", "speaker"})."""
    if isinstance(v, dict):
        span = _span([v.get("start"), v.get("end")], duration)
        beat = str(v.get("beat") or "").lower()
        who = str(v.get("speaker") or "").lower()
        meta = {"beat": beat if beat in BEATS else None,
                "speaker": who if who in SPEAKERS else ("elijah" if who in ("host", "elijah johnson") else
                                                        "caller" if who in ("guest", "opponent") else None)}
        return span, meta
    return _span(v, duration), {"beat": None, "speaker": None}


# ---------------- transcript helpers ----------------
def _words(transcript):
    if not transcript:
        return []
    return sorted((w for s in transcript.get("segments", []) for w in s.get("words", [])), key=lambda w: w["s"])


def _snap(span, words, starts):
    """Never cut inside a word: a start inside a word moves to its beginning, an end to its end."""
    if not words:
        return span
    a, b = span
    i = bisect.bisect_right(starts, a) - 1
    if 0 <= i < len(words) and words[i]["s"] < a < words[i]["e"]:
        a = words[i]["s"]
    j = bisect.bisect_right(starts, b) - 1
    if 0 <= j < len(words) and words[j]["s"] < b < words[j]["e"]:
        b = words[j]["e"]
    return [round(a, 3), round(b, 3)]


def _norm(w):
    return re.sub(r"[^a-z0-9']", "", str(w).lower())


def find_quote(quote, at, words, starts, before=12.0, after=25.0, need=0.6):
    """Where these words were actually said near `at`: the start time of the best match, or None."""
    q = [t for t in (_norm(x) for x in str(quote or "").split()) if t]
    if not q or not words or at is None:
        return None
    lo = bisect.bisect_left(starts, at - before)
    hi = bisect.bisect_right(starts, at + after)
    win = [(_norm(w["w"]), w["s"]) for w in words[lo:hi]]
    best, best_t = 0.0, None
    for i, (tok, t) in enumerate(win):
        if tok not in q:
            continue
        k, got = q.index(tok), 0
        j = i
        for want in q[k:]:                       # greedy in-order match from here
            while j < len(win) and j - i <= len(q) * 2 + 4 and win[j][0] != want:
                j += 1
            if j < len(win) and win[j][0] == want:
                got += 1
                j += 1
        score = got / len(q)
        if score > best:
            best, best_t = score, t
    min_words = min(3, len(q))
    return best_t if best >= need and best * len(q) >= min_words else None


def verify_defeat(d, words, starts, span, duration):
    """A self-defeat with both quotes found in the transcript (times corrected), or None."""
    if not isinstance(d, dict):
        return None
    claim, against = str(d.get("claim") or "").strip(), str(d.get("against") or "").strip()
    ca, aa = _num(d.get("claim_at")), _num(d.get("against_at"))
    if not claim or not against or ca is None or aa is None:
        return None
    if words:
        ca2, aa2 = find_quote(claim, ca, words, starts), find_quote(against, aa, words, starts)
        if ca2 is None or aa2 is None:
            return None
        ca, aa = ca2, aa2
    if span and not (span[0] - 30 <= ca <= span[1] and span[0] - 30 <= aa <= span[1]):
        return None
    caught = _num(d.get("caught_at"))
    kind = str(d.get("kind") or "").lower()
    return {"claim": claim[:300], "claim_at": round(ca, 2), "against": against[:300], "against_at": round(aa, 2),
            "kind": kind if kind in DEFEAT_KINDS else "contradiction",
            "caught_at": round(caught, 2) if caught is not None and 0 <= caught <= duration else None,
            "explain": str(d.get("explain") or "")[:300]}


# ---------------- the three beats ----------------
def _aligned(spans, beats):
    return isinstance(beats, list) and len(beats) == len(spans) and all(isinstance(b, dict) for b in beats)


def _inside(t, spans, slack=0.5):
    return t is not None and any(a - slack <= t <= b + slack for a, b in spans)


def structure_notes(item):
    """Which beats are missing, in plain words. [] when the structure is complete or unknown."""
    notes = []
    if item.get("kind") == "long" or "cold_open" in item and "pieces" not in item:
        spans, beats = item.get("cold_open") or [], item.get("cold_open_beats")
        if not spans or not _aligned(spans, beats):
            return []
        if beats[0].get("beat") != "setup":
            notes.append("Cold open doesn't start with the setup (his claim or the question), so viewers "
                         "won't know what the fight is about.")
        if item.get("self_defeats") and not any(b.get("beat") == "turn" for b in beats):
            notes.append("He contradicts himself in this call, but the cold open doesn't tease it.")
        return notes
    spans, beats = item.get("pieces") or [], item.get("beats")
    cat = item.get("category")
    sd = item.get("self_defeat")
    if cat == "Self-Defeat" and not sd:
        notes.append("Marked Self-Defeat, but there's no checked contradiction in it. Change the category "
                     "or tell the editor which moment it is.")
    if sd and not (_inside(sd.get("claim_at"), spans) and _inside(sd.get("against_at"), spans)):
        notes.append(f"The contradiction isn't fully in the clip: it has to play both “{sd['claim'][:60]}” "
                     f"and “{sd['against'][:60]}”.")
    if not spans or not _aligned(spans, beats):
        return notes
    kinds = [b.get("beat") for b in beats]
    if kinds[0] != "setup":
        notes.append("No setup: it doesn't open with the question or claim, so viewers won't know what's "
                     "being debated.")
    if cat in DEBATE:
        who = {b.get("speaker") for b in beats}
        if "both" not in who and not ({"caller", "elijah"} <= who):
            side = "Elijah" if "elijah" in who else "the caller" if "caller" in who else None
            if side:
                notes.append(f"Only {side} speaks. The clash needs both sides in their own voices.")
        if "turn" not in kinds:
            notes.append("No turn: it doesn't end on his argument undercutting itself or on the decisive answer.")
    elif "turn" not in kinds and "payoff" not in kinds:
        notes.append("No payoff: it doesn't end on the answer or the point.")
    return notes


# ---------------- the whole plan ----------------
def validate(plan, duration, transcript=None):
    if not isinstance(plan, dict):
        raise PlanError("The plan wasn't a JSON object.")
    words = _words(transcript)
    starts = [w["s"] for w in words]
    verified = {}                                       # id(raw self-defeat) -> checked one (or None)

    def check_defeat(d, span):
        if id(d) not in verified:
            verified[id(d)] = verify_defeat(d, words, starts, span, duration)
        return verified[id(d)]

    problems, convs, shorts = [], [], []
    for i, c in enumerate(plan.get("conversations") or [], 1):
        if not isinstance(c, dict):
            problems.append(f"Conversation {i} isn't readable; skipped."); continue
        who = str(c.get("guest") or f"Guest {i}").strip()[:60]
        span = _span([c.get("start"), c.get("end")], duration)
        if not span:
            problems.append(f"{who}: start/end times are missing or outside the recording; skipped."); continue
        if span[1] - span[0] < 60:
            problems.append(f"{who}: shorter than a minute; skipped."); continue
        issues = []
        cold, cold_beats = [], []
        for x in c.get("cold_open") or []:
            s, meta = _clip(x, duration)
            if s and span[0] <= s[0] and s[1] <= span[1] + 1:
                cold.append(_snap(s, words, starts)); cold_beats.append(meta)
        if not cold:
            issues.append("No usable cold-open clips; pick them in review.")
        elif sum(b - a for a, b in cold) > MAX_COLD_OPEN:
            issues.append(f"Cold open is {sum(b - a for a, b in cold):.0f} s; trim it to about 30.")
        raw_defeats = [d for d in (c.get("self_defeats") or []) if isinstance(d, dict)]
        defeats = [v for v in (check_defeat(d, span) for d in raw_defeats) if v]
        dropped = len(raw_defeats) - len(defeats)
        if dropped:
            problems.append(f"{who}: {dropped} claimed contradiction{'s' if dropped > 1 else ''} couldn't be "
                            "found in the transcript, so I left them out.")
        titles = [str(t)[:100] for t in (c.get("titles") or []) if str(t).strip()]
        protect = [s for s in (_span(x, duration) for x in (c.get("protect") or [])) if s]
        rank = c.get("rank") if c.get("rank") in ("STRONG", "MEDIUM", "WEAK") else None
        kind = c.get("kind") if c.get("kind") in ("debate", "question", "friendly") else None
        convs.append({"id": f"c{int(span[0])}", "guest": who, "topic": str(c.get("topic") or "")[:160],
                      "start": span[0], "end": span[1], "rank": rank, "kind_of_call": kind,
                      "summary": str(c.get("summary") or "")[:600], "cold_open": cold,
                      "cold_open_beats": cold_beats if any(m["beat"] for m in cold_beats) else None,
                      "self_defeats": defeats, "titles": titles[:3], "protect": protect, "issues": issues})
        problems += [f"{who}: {x}" for x in issues]
    convs.sort(key=lambda c: c["start"])
    for a, b in zip(convs, convs[1:]):
        if b["start"] < a["end"]:
            msg = f"overlaps {a['guest']}; check where one ends and the next begins."
            b["issues"].append(msg); problems.append(f"{b['guest']}: {msg}")
    for i, s in enumerate(plan.get("shorts") or [], 1):
        if not isinstance(s, dict):
            continue
        pieces, beats = [], []
        for x in s.get("pieces") or []:
            span, meta = _clip(x, duration)
            if span:
                pieces.append(_snap(span, words, starts)); beats.append(meta)
        hook = str(s.get("hook") or "").strip()[:80]
        label = hook or f"Short {i}"
        if not pieces:
            problems.append(f"Short “{label}”: no valid clip times; skipped."); continue
        length = sum(b - a for a, b in pieces)
        issues = []
        if not SHORT_RANGE[0] <= length <= SHORT_RANGE[1]:
            issues.append(f"{length:.0f} s long; shorts work best between 15 and 90 s.")
        cat = s.get("category") if s.get("category") in CATEGORIES else "Argument Win"
        sd = None
        idx = s.get("self_defeat")
        pool = s.get("_defeats") or []
        if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < len(pool):
            sd = check_defeat(pool[idx], None)
        elif isinstance(idx, dict):
            sd = check_defeat(idx, None)
        if sd:                                          # a quote just outside a clip: stretch the clip to it
            for t in (sd["claim_at"], sd["against_at"]):
                if _inside(t, pieces):
                    continue
                k = min(range(len(pieces)), key=lambda k: min(abs(t - pieces[k][0]), abs(t - pieces[k][1])))
                a, b = pieces[k]
                if a - EXTEND <= t < a:
                    pieces[k] = _snap([max(0.0, t - 0.2), b], words, starts)
                elif b < t <= b + EXTEND:
                    pieces[k] = _snap([a, min(duration, t + 4.0)], words, starts)
        if cat == "Self-Defeat" and not sd:
            cat = "Argument Win"                        # never label a contradiction we couldn't check
            issues.append("Claude called this a self-defeat, but the quoted words weren't found, so it's "
                          "labelled Argument Win.")
        shorts.append({"id": f"s{int(pieces[0][0])}", "category": cat, "hook": hook or "Add a hook",
                       "topic": str(s.get("topic") or "")[:160], "pieces": pieces,
                       "beats": beats if any(m["beat"] for m in beats) else None, "self_defeat": sd,
                       "why": str(s.get("why") or "")[:200], "framing": "auto", "issues": issues})
        problems += [f"Short “{label}”: {x}" for x in issues]
    if not convs and not shorts:
        raise PlanError("The plan had no usable conversations or shorts. " + " ".join(problems[:5]))
    seen = set()                                        # unique ids even if two items start in the same second
    for item in convs + shorts:
        base, n = item["id"], 2
        while item["id"] in seen:
            item["id"] = f"{base}-{n}"; n += 1
        seen.add(item["id"])
    housekeeping = [h for h in (plan.get("housekeeping") or []) if isinstance(h, dict) and _span([h.get("start"), h.get("end")], duration)]
    return {"conversations": convs, "shorts": shorts, "housekeeping": housekeeping}, problems
