"""Corrections for names, theological terms and Scripture references the transcriber gets wrong.

Rules are "wrong => right" lines. Matching ignores case and punctuation, works across several
words ("McKills a day" => "Melchizedek"), and keeps each word's timing, so captions stay in sync.
The raw transcript is never changed; a corrected copy is made for captions, titles and plans.
"""
import copy
import re

DEFAULTS = """McKills of that => Melchizedek
McKills a day => Melchizedek
Michel's deck => Melchizedek
Mckedc => Melchizedek
Mackilad => Melchizedek
Ibn Kadir => Ibn Kathir
Iban Omar => Ibn Umar
Ananas => Ananias
Sephira => Sapphira
Safhira => Sapphira
Catalyzem => Catholicism
Potestania => Protestantism
"""


def parse(text):
    rules = []
    for line in (text or "").splitlines():
        if "=>" in line:
            wrong, right = (x.strip() for x in line.split("=>", 1))
            if wrong and right:
                rules.append((wrong, right))
    return rules


def _norm(w):
    return re.sub(r"[^\w']", "", w.lower())


def apply(transcript, rules_text):
    """Returns (corrected transcript, {rule: count})."""
    rules = [(r[0].split(), r[1]) for r in parse(rules_text)]
    rules.sort(key=lambda r: -len(r[0]))               # longest phrases first
    out = copy.deepcopy(transcript)
    counts = {}
    for seg in out["segments"]:
        words = seg["words"]
        i, new = 0, []
        while i < len(words):
            hit = None
            for wrong, right in rules:
                n = len(wrong)
                if [_norm(w["w"]) for w in words[i:i + n]] == [_norm(x) for x in wrong]:
                    hit = (n, right, " => ".join((" ".join(wrong), right))); break
            if hit:
                n, right, key = hit
                last = words[i + n - 1]["w"]
                tail = re.search(r"[.,?!;:]+$", last)
                new.append({"w": right + (tail.group(0) if tail else ""), "s": words[i]["s"], "e": words[i + n - 1]["e"]})
                counts[key] = counts.get(key, 0) + 1
                i += n
            else:
                new.append(words[i]); i += 1
        seg["words"] = new
        seg["text"] = " ".join(w["w"] for w in new) if new else seg["text"]
    return out, counts
