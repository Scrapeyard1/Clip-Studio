"""Checks an AI plan before anything expensive runs.

validate(plan, duration) returns (clean_plan, problems). Problems are plain sentences the
review screen shows next to the item, so a person can fix the times or drop the item.
A plan with no usable conversation AND no usable short raises PlanError.
"""
import math

MAX_COLD_OPEN = 45.0      # seconds; the target is ~30
SHORT_RANGE = (8.0, 180.0)
CATEGORIES = {"Roast", "Argument Win", "Explainer", "Testimony", "Pastoral"}


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


def validate(plan, duration):
    if not isinstance(plan, dict):
        raise PlanError("The plan wasn't a JSON object.")
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
        cold = [s for s in (_span(x, duration) for x in (c.get("cold_open") or [])) if s]
        cold = [s for s in cold if span[0] <= s[0] and s[1] <= span[1] + 1]
        if not cold:
            issues.append("No usable cold-open clips; pick them in review.")
        elif sum(b - a for a, b in cold) > MAX_COLD_OPEN:
            issues.append(f"Cold open is {sum(b - a for a, b in cold):.0f} s; trim it to about 30.")
        titles = [str(t)[:100] for t in (c.get("titles") or []) if str(t).strip()]
        protect = [s for s in (_span(x, duration) for x in (c.get("protect") or [])) if s]
        rank = c.get("rank") if c.get("rank") in ("STRONG", "MEDIUM", "WEAK") else None
        convs.append({"id": f"c{int(span[0])}", "guest": who, "topic": str(c.get("topic") or "")[:120],
                      "start": span[0], "end": span[1], "rank": rank, "summary": str(c.get("summary") or "")[:600],
                      "cold_open": cold, "titles": titles[:3], "protect": protect, "issues": issues})
        problems += [f"{who}: {x}" for x in issues]
    convs.sort(key=lambda c: c["start"])
    for a, b in zip(convs, convs[1:]):
        if b["start"] < a["end"]:
            msg = f"overlaps {a['guest']}; check where one ends and the next begins."
            b["issues"].append(msg); problems.append(f"{b['guest']}: {msg}")
    for i, s in enumerate(plan.get("shorts") or [], 1):
        if not isinstance(s, dict):
            continue
        pieces = [p for p in (_span(x, duration) for x in (s.get("pieces") or [])) if p]
        hook = str(s.get("hook") or "").strip()[:80]
        label = hook or f"Short {i}"
        if not pieces:
            problems.append(f"Short “{label}”: no valid clip times; skipped."); continue
        length = sum(b - a for a, b in pieces)
        issues = []
        if not SHORT_RANGE[0] <= length <= SHORT_RANGE[1]:
            issues.append(f"{length:.0f} s long; shorts work best between 15 and 90 s.")
        cat = s.get("category") if s.get("category") in CATEGORIES else "Argument Win"
        shorts.append({"id": f"s{int(pieces[0][0])}", "category": cat, "hook": hook or "Add a hook",
                       "pieces": pieces, "why": str(s.get("why") or "")[:200], "framing": "auto", "issues": issues})
        problems += [f"Short “{label}”: {x}" for x in issues]
    if not convs and not shorts:
        raise PlanError("The plan had no usable conversations or shorts. " + " ".join(problems[:5]))
    # unique ids even if two items start in the same second
    seen = set()
    for item in convs + shorts:
        base, n = item["id"], 2
        while item["id"] in seen:
            item["id"] = f"{base}-{n}"; n += 1
        seen.add(item["id"])
    housekeeping = [h for h in (plan.get("housekeeping") or []) if isinstance(h, dict) and _span([h.get("start"), h.get("end")], duration)]
    return {"conversations": convs, "shorts": shorts, "housekeeping": housekeeping}, problems
