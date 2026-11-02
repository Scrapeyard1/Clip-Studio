"""Clip Studio pipeline, in two stages so a person reviews before anything heavy renders.

  analyze(project)          audio -> transcript -> quiet stretches -> Claude's plan (validated)
  render(project, ids)      only the conversations/shorts the reviewer selected

A project folder looks like:
  project.json      title, source, status (owned by the app)
  edits.json        everything a PERSON changed: boundaries, cold opens, restored pauses, framing,
                    titles, approvals, term corrections. Never overwritten by AI output.
  source/           an uploaded or downloaded recording (unique per project)
  work/             audio, transcripts, plan, step records
  drafts/           finished outputs, named by item id (c1191.mp4, s3496.mp4 ...)

A step only counts as finished when every output it promises exists AND passes a check
(plays, parses, isn't empty), and when the settings it was made from haven't changed since.
Outputs are written to temporary names and moved into place only after they pass.

Command line:  python clipstudio.py analyze <project dir>
               python clipstudio.py render  <project dir> [item ids...]
"""
import hashlib
import re
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(ROOT, "engine")
sys.path.insert(0, ENGINE)

import media  # noqa: E402
import paths  # noqa: E402

LONG_EDIT_KEYS = ("guest", "topic", "start", "end", "cold_open", "protect")
SHORT_EDIT_KEYS = ("hook", "category", "pieces", "framing", "protect", "guest")
# beat labels (setup / clash / turn) can be corrected too; they don't change the video, so they're kept
# out of render_inputs and never make a render stale
LONG_LABEL_KEYS = ("cold_open_beats",)
SHORT_LABEL_KEYS = ("beats",)


class Reporter:
    """How a job tells the app what it's doing. The queue passes one per job (no globals)."""
    def __init__(self, on_step=None, on_progress=None, cancel_event=None):
        self.on_step, self.on_progress, self.cancel = on_step, on_progress, cancel_event

    def step(self, name):
        self.check()
        if self.on_step:
            self.on_step(name)

    def progress(self, pct):
        if self.on_progress and pct != getattr(self, "_last", None):     # only write when the number changes
            self._last = pct
            self.on_progress(pct)

    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise media.Cancelled()

    def part(self, lo, hi):
        """A slice of the overall bar: part(6, 88)(0.5) reports 47%."""
        return lambda frac: self.progress(int(lo + (hi - lo) * max(0.0, min(1.0, frac))))


# Share of the analysis bar for each stage (transcription is most of the wait).
ANALYZE_STAGES = {"pictures": (0, 1), "audio": (1, 6), "transcript": (6, 88), "quiet": (88, 90), "plan": (90, 100)}


def transcriber_fraction(line):
    """The transcriber prints '  1234s / 9711s  speed 10.0x'. Returns 0.127 for that, or None."""
    m = re.match(r"\s*(\d+)s\s*/\s*(\d+)s", line or "")
    if not m or int(m.group(2)) <= 0:
        return None
    return min(1.0, int(m.group(1)) / int(m.group(2)))


def make_pictures(d, source):
    """A poster frame and a filmstrip of the recording, so there's something to see while it's analyzed."""
    w = os.path.join(d, "work")
    os.makedirs(w, exist_ok=True)
    dur = media.probe(source)["duration"] or 60
    poster = os.path.join(w, "poster.jpg")
    if not media.valid_output(poster):
        media.frame(source, min(dur * 0.1, 600), poster, 960)
    strip = os.path.join(w, "strip.jpg")
    if not media.valid_output(strip):
        media.filmstrip(source, dur, strip)
    return poster, strip


# ---------------- project files ----------------
def load(d, name, default=None):
    p = os.path.join(d, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default


def edits(d):
    e = load(d, "edits.json", {}) or {}
    e.setdefault("items", {})
    e.setdefault("glossary", None)
    return e


def _spans(item):
    return item["pieces"] if "pieces" in item else [[item["start"], item["end"]]]


def _shared(a, b):
    """How much of the smaller item the two items share, from their actual pieces (0..1)."""
    both = sum(max(0.0, min(y0, y1) - max(x0, x1)) for x0, y0 in _spans(a) for x1, y1 in _spans(b))
    la, lb = (sum(y - x for x, y in _spans(i)) for i in (a, b))
    return both / max(1e-6, min(la, lb))


def carry_edits(d, old_plan, new_plan):
    """A new plan can shift a clip by a few seconds, which changes its id. Move the person's edits
    from each vanished id onto the new item that covers the same stretch (at least half of it), so
    a re-plan never silently drops a framing choice, title or approval. Returns {old_id: new_id}."""
    e = edits(d)
    old = {i["id"]: i for k in ("conversations", "shorts") for i in (old_plan or {}).get(k, [])}
    new_by_kind = {"conversations": (new_plan or {}).get("conversations", []),
                   "shorts": (new_plan or {}).get("shorts", [])}
    new_ids = {i["id"] for v in new_by_kind.values() for i in v}
    moved = {}
    for oid in [k for k in e["items"] if k not in new_ids and k in old]:
        kind = "shorts" if oid.startswith("s") else "conversations"
        best, best_f = None, 0.5
        for it in new_by_kind[kind]:
            f = _shared(old[oid], it)
            if f > best_f and it["id"] not in e["items"]:
                best, best_f = it["id"], f
        if best:
            e["items"][best] = dict(e["items"].pop(oid), moved_from=oid)
            moved[oid] = best
    if moved:                                   # combined videos follow their conversations to the new ids
        old_pairs = e["combos"] if "combos" in e else [c["ids"] for c in (old_plan or {}).get("combos", [])]
        for p in old_pairs:
            new = [moved.get(i, i) for i in p]
            if new != list(p) and combo_id(*p) in e["items"]:
                e["items"][combo_id(*new)] = e["items"].pop(combo_id(*p))
        if "combos" in e:
            e["combos"] = [[moved.get(i, i) for i in p] for p in e["combos"]]
    if moved:
        media.write_json(os.path.join(d, "edits.json"), e)
    return moved


def glossary_text(d):
    import glossary
    g = edits(d)["glossary"]
    return glossary.DEFAULTS if g is None else g


COMBO_EDIT_KEYS = ("guest", "topic", "cold_open", "protect")


def combo_id(a, b):
    """Stable id of the video combining conversations a and b (same whichever order they're given)."""
    x, y = sorted((a, b))
    return f"m-{x}-{y}"


def combo_pairs(d, plan=None):
    """Which conversations are combined into one video: the person's choice once they've made one,
    otherwise the plan's suggestion. [[id_a, id_b], ...]"""
    e = edits(d)
    if "combos" in e:
        return [list(p) for p in e["combos"]]
    plan = plan if plan is not None else load(os.path.join(d, "work"), "plan.json", {})
    return [list(c["ids"]) for c in (plan or {}).get("combos", [])]


def effective_items(d):
    """AI plan with the person's edits laid over it, item by item (matched by stable id).
    Two short conversations combined into one video appear as one more long item with "parts"
    (its times always follow the two conversations' own, edited times)."""
    plan = load(os.path.join(d, "work"), "plan.json", {"conversations": [], "shorts": []})
    e = edits(d)["items"]
    convs, shorts = [], []
    for c in plan.get("conversations", []):
        convs.append({**c, **{k: v for k, v in e.get(c["id"], {}).items() if k in LONG_EDIT_KEYS + LONG_LABEL_KEYS},
                      "kind": "long"})
    by_id = {c["id"]: c for c in convs}
    suggested = {frozenset(c["ids"]): c for c in plan.get("combos", [])}
    import plan_check
    for ids in combo_pairs(d, plan):
        if len(ids) != 2 or ids[0] == ids[1] or any(i not in by_id for i in ids):
            continue
        a, b = sorted((by_id[i] for i in ids), key=lambda c: c["start"])
        cid = combo_id(a["id"], b["id"])
        base = suggested.get(frozenset(ids)) or {k: plan_check.stronger(a, b).get(k) for k in plan_check.COMBO_COPY}
        item = {"id": cid, "kind": "long", "combo": True, "members": [a["id"], b["id"]],
                "parts": [[a["start"], a["end"]], [b["start"], b["end"]]], "start": a["start"], "end": b["end"],
                "guest": f"{a['guest']} + {b['guest']}", "topic": f"{a.get('topic', '')} / {b.get('topic', '')}",
                "rank": plan_check.stronger(a, b).get("rank"), "video": True,
                "video_reason": base.get("reason") or f"You combined {a['guest']} and {b['guest']} into one video.",
                "protect": list(a.get("protect") or []) + list(b.get("protect") or []),
                "self_defeats": list(a.get("self_defeats") or []) + list(b.get("self_defeats") or []),
                "summary": " ".join(x for x in (a.get("summary"), b.get("summary")) if x), "issues": [],
                **{k: base.get(k) for k in plan_check.COMBO_COPY}}
        item.update({k: v for k, v in e.get(cid, {}).items() if k in COMBO_EDIT_KEYS + LONG_LABEL_KEYS})
        for m in (a, b):
            m["in_combo"] = cid
        convs.append(item)
    convs.sort(key=lambda c: (c["start"], 0 if c.get("combo") else 1))
    for s in plan.get("shorts", []):
        shorts.append({**s, **{k: v for k, v in e.get(s["id"], {}).items() if k in SHORT_EDIT_KEYS + SHORT_LABEL_KEYS},
                       "kind": "short"})
    return convs, shorts


def corrected_transcript(d):
    import glossary
    raw = load(os.path.join(d, "work"), "transcript.json")
    fixed, _ = glossary.apply(raw, glossary_text(d))
    return fixed


# ---------------- checked steps ----------------
def _fingerprint(inputs):
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()[:16]


def step_state(d, key, outputs, inputs=None):
    """'done' only if the record exists, every output is still there with the recorded size and
    passes its check, and the inputs fingerprint matches. Otherwise 'stale' or 'missing'."""
    rec = load(os.path.join(d, "work", "steps"), key + ".json")
    if not rec:
        return "missing"
    if inputs is not None and rec.get("inputs") != _fingerprint(inputs):
        return "stale"
    for p in outputs:
        if not os.path.exists(p) or os.path.getsize(p) != rec["sizes"].get(os.path.basename(p)):
            return "missing"
    return "done"


def run_step(d, key, label, outputs, fn, rep, inputs=None):
    if step_state(d, key, outputs, inputs) == "done" and all(media.valid_output(p) for p in outputs):
        return False
    rep.step(label)
    try:
        fn()
    except BaseException:
        for o in outputs:                       # a stopped or failed step leaves no half-written files
            try:
                os.remove(media.tmp_name(o))
            except OSError:
                pass
        raise
    bad = [os.path.basename(p) for p in outputs if not media.valid_output(p)]
    if bad:
        raise RuntimeError(f"{label}: {', '.join(bad)} didn't come out right. Press Retry to redo it.")
    os.makedirs(os.path.join(d, "work", "steps"), exist_ok=True)
    media.write_json(os.path.join(d, "work", "steps", key + ".json"),
                     {"inputs": _fingerprint(inputs) if inputs is not None else None,
                      "sizes": {os.path.basename(p): os.path.getsize(p) for p in outputs}})
    return True


# ---------------- stage 1: analyze ----------------
def analyze(d, source, rep=None, threads=None):
    rep = rep or Reporter()
    w = os.path.join(d, "work")
    os.makedirs(w, exist_ok=True)
    wav, tr, qj, pj = (os.path.join(w, x) for x in ("audio16k.wav", "transcript.json", "quiet.json", "plan.json"))

    S = ANALYZE_STAGES
    try:
        make_pictures(d, source)
    except Exception:
        pass                                   # pictures are nice to have, never a reason to stop
    rep.part(*S["pictures"])(1)

    def extract():
        dur = media.probe(source)["duration"] or 1
        media.progress.done, media.progress.fn = 0.0, lambda sec: rep.part(*S["audio"])(sec / dur)
        tmp = media.tmp_name(wav)
        media.run([paths.ffmpeg(), "-v", "error", "-y", "-i", source, "-map", "0:a:0", "-vn", "-ac", "1",
                   "-ar", "16000", "-c:a", "pcm_s16le", tmp])
        media.progress.fn = None
        media.commit(tmp, wav)
    run_step(d, "audio", "Extract audio", [wav], extract, rep, inputs=[source, os.path.getsize(source)])
    rep.part(*S["audio"])(1)

    def on_line(line):
        f = transcriber_fraction(line)
        if f is not None:
            rep.part(*S["transcript"])(f)
    run_step(d, "transcript", "Transcribe", [tr], lambda: media.run(
        [sys.executable, os.path.join(ENGINE, "transcribe.py"), wav, tr, "--threads", str(threads or os.cpu_count())],
        on_line=on_line), rep, inputs=[os.path.getsize(wav)])
    rep.part(*S["transcript"])(1)

    def quiet():
        from cuts import detect_quiet
        media.write_json(qj, detect_quiet(wav))
    run_step(d, "quiet", "Measure quiet stretches", [qj], quiet, rep, inputs=[os.path.getsize(wav)])
    rep.part(*S["quiet"])(1)

    def plan():
        from moments import plan as claude_plan
        previous = load(w, "plan.json") or load(w, "plan.previous.json")
        # Claude's answer is usually 20-40k characters including its thinking: move the bar as it streams
        clean, problems = claude_plan(corrected_transcript(d),
                                      on_chars=lambda n: rep.part(*S["plan"])(min(0.95, n / 35000)))
        moved = carry_edits(d, previous, clean)
        clean["problems"] = problems + [f"Your edits on {a} moved to {b}, the same stretch in the new plan."
                                        for a, b in moved.items()]
        media.write_json(pj, clean)
    run_step(d, "plan", "Find conversations and shorts", [pj], plan, rep, inputs=[os.path.getsize(tr)])


# ---------------- full preview (before rendering) ----------------
PREVIEW_CHUNK = 90          # long stretches are split so playback starts fast and the next part is ready
PREVIEW_SPAN = 150          # parts are joined into one file only when they sit this close in the live
PREVIEW_JOINED = 30         # ...and up to this long, so each joined file is ready within a few seconds


def cut_plan(d, item, cfg, source=None):
    """Exactly what the finished video will play, in order, from the same code the renderer uses.
    Returns {"total", "chunks": [{"kind", "parts": [[a, b] source seconds] (or "placeholder" for the
    sting/outro cards), "seconds", "at" (time in the finished video), "label"}]}."""
    from timeline import Timeline
    tr = corrected_transcript(d)
    quiet = load(os.path.join(d, "work"), "quiet.json")
    fps = media.probe(source)["fps"] if source else 30
    if item["kind"] == "long":
        import render_long
        tl, _, _, _ = render_long.plan_timeline(fps, tr, item, cfg, quiet)
    else:
        import render_short
        tl = Timeline(fps)
        for a, b in render_short.keep_ranges(tr, item, quiet):
            tl.add_source("body", a, b)
    chunks, cold_n = [], 0
    for seg in tl.segments:
        at = tl.sec(seg["at_f"])
        if seg["src"] != "main":
            name = {"sting": "Title sequence", "outro": "Patreon outro"}.get(seg["kind"], seg["kind"].title())
            chunks.append({"kind": seg["kind"], "seconds": round(tl.sec(seg["out_f"] - seg["in_f"]), 3),
                           "at": round(at, 3), "label": name, "placeholder": seg["src"].startswith("card:")})
            continue
        a, b = tl.src_seconds(seg)
        if seg["kind"] == "cold":
            cold_n += 1
        label = f"Cold open, clip {cold_n}" if seg["kind"] == "cold" else (
            "The conversation" if item["kind"] == "long" else "The short")
        x = a
        while x < b - 1e-6:
            y = min(b, x + PREVIEW_CHUNK)
            if b - y < 5:
                y = b
            last = chunks[-1] if chunks else None
            # nearby parts (pauses tightened between them) play as one smooth file; far-apart ones don't
            if (last and last.get("parts") and last["kind"] == seg["kind"] and seg["kind"] != "cold"
                    and x >= last["parts"][-1][1] and y - last["parts"][0][0] <= PREVIEW_SPAN
                    and last["seconds"] + (y - x) <= PREVIEW_JOINED):
                last["parts"].append([round(x, 3), round(y, 3)])
                last["seconds"] = round(last["seconds"] + y - x, 3)
            else:
                chunks.append({"kind": seg["kind"], "parts": [[round(x, 3), round(y, 3)]], "seconds": round(y - x, 3),
                               "at": round(at + x - a, 3), "label": label})
            x = y
    return {"total": round(tl.seconds, 3), "chunks": chunks}


# ---------------- stage 2: render selected ----------------
def outputs_for(d, item):
    dr = os.path.join(d, "drafts")
    if item["kind"] == "long":
        import render_long
        return render_long.outputs(dr, item["id"])
    return {"video": os.path.join(dr, item["id"] + ".mp4")}


def short_guest(d, item):
    """Who a short's caller is: the person's choice, else the conversation the short comes from."""
    if item.get("guest"):
        return item["guest"]
    convs, _ = effective_items(d)
    a = item["pieces"][0][0] if item.get("pieces") else 0
    hit = next((c for c in convs if c["start"] <= a <= c["end"]), None)
    return hit.get("guest") if hit else ""


def guest_for_render(d, item, cfg):
    import guests
    g = guests.get(short_guest(d, item) or "Guest")
    return dict(g, track=cfg.get("guest_audio_track", 0))


def render_inputs(d, item, cfg):
    """Everything a render depends on; if any of it changes, the render is stale."""
    keys = LONG_EDIT_KEYS if item["kind"] == "long" else SHORT_EDIT_KEYS
    out = {"item": {k: item.get(k) for k in keys + (("parts",) if item.get("parts") else ())}, "glossary": glossary_text(d),
           "sting": cfg.get("sting"), "outro": cfg.get("outro"), "v": 2}
    if item["kind"] == "short" and item.get("framing") == "guest":      # new photo or handle -> re-render
        g = guest_for_render(d, item, cfg)
        out["guest"] = {k: g.get(k) for k in ("name", "handle", "version", "track")}
    return out


def render_problems(item):
    """Why an item can't be rendered yet, in words for the review screen ([] = ready)."""
    out = []
    if item["kind"] == "long":
        if not item.get("cold_open"):
            out.append("Pick at least one cold-open clip.")
        if not (item.get("end", 0) - item.get("start", 0) >= 60):
            out.append("The conversation needs to be at least a minute long.")
        parts = item.get("parts") or [[item.get("start", 0), item.get("end", 0)]]
        if any(not any(pa - 1 <= a < b <= pb + 1 for pa, pb in parts) for a, b in item.get("cold_open", [])):
            out.append("Every cold-open clip must come from inside the conversation"
                       + (" (one of the two combined)." if item.get("parts") else "."))
    else:
        if not item.get("pieces"):
            out.append("Add at least one clip.")
        if any(b <= a for a, b in item.get("pieces", [])):
            out.append("Each clip must end after it starts.")
    return out


def render(d, source, ids, cfg=None, rep=None):
    cfg = cfg or {}
    rep = rep or Reporter()
    w = os.path.join(d, "work")
    quiet = load(w, "quiet.json")
    tr = corrected_transcript(d)
    convs, shorts = effective_items(d)
    by_id = {i["id"]: i for i in convs + shorts}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise RuntimeError(f"These items aren't in the plan anymore: {', '.join(missing)}")
    blocked = [f"{by_id[i].get('guest') or by_id[i].get('hook')}: {' '.join(render_problems(by_id[i]))}"
               for i in ids if render_problems(by_id[i])]
    if blocked:
        raise RuntimeError("Fix these before rendering. " + " | ".join(blocked))
    def expected_seconds(it):
        if it["kind"] == "long":
            parts = it.get("parts") or [[it["start"], it["end"]]]
            return sum(b - a for a, b in parts) + sum(b - a for a, b in it.get("cold_open", [])) + 14
        return sum(b - a for a, b in it["pieces"])
    for n, iid in enumerate(ids, 1):
        item = by_id[iid]
        rep.progress(int(100 * (n - 1) / len(ids)))
        # inside one item, move the bar with FFmpeg's position
        total = 2 * expected_seconds(item)     # every render passes over the footage twice: pieces, then the join
        media.progress.done = 0.0
        media.progress.fn = lambda sec, n=n, total=total: rep.progress(
            int(100 * (n - 1 + min(0.99, sec / max(total, 1))) / len(ids)))
        outs = outputs_for(d, item)
        inputs = render_inputs(d, item, cfg)
        label = item.get("guest") or item.get("hook") or iid
        if item["kind"] == "long":
            import render_long
            run_step(d, f"render-{iid}", f"Render: {label}", list(outs.values()),
                     lambda item=item: render_long.render(source, tr, dict(item, name=iid),
                                                          os.path.join(d, "drafts"), cfg, quiet), rep, inputs)
            if os.environ.get("ANTHROPIC_API_KEY"):
                mj = os.path.join(d, "drafts", iid + ".metadata.json")

                def meta(item=item, mj=mj):
                    from metadata import write
                    from timeline import Timeline
                    media.write_json(mj, write(tr, item, Timeline.load(outs["timeline"])))
                run_step(d, f"titles-{iid}", f"Titles and description: {label}", [mj], meta, rep, inputs)
            if cfg.get("host_photos") and os.path.exists(os.path.join(d, "drafts", iid + ".metadata.json")):
                thumbs = [os.path.join(d, "drafts", f"{iid}.thumb{k}.jpg") for k in (1, 2, 3)]
                run_step(d, f"thumbs-{iid}", f"Thumbnails: {label}", thumbs,
                         lambda item=item: make_thumbnails(source, item, d, cfg["host_photos"]), rep,
                         dict(inputs, photos=sorted(os.listdir(cfg["host_photos"]))))
        else:
            import render_short
            run_step(d, f"render-{iid}", f"Render short: {label}", [outs["video"]],
                     lambda item=item: render_short.render(
                         source, tr, dict(item, name=iid), os.path.join(d, "drafts"), quiet,
                         guest=guest_for_render(d, item, cfg) if item.get("framing") == "guest" else None),
                     rep, inputs)
    media.progress.fn = None
    rep.progress(100)


def make_thumbnails(video, conv, d, photos_dir):
    """3 thumbnails from the titles step's briefs. Host photo is picked by expression from the photo
    folder (file names like pointing-1.jpg, shocked.png). Background: a verse screen from the
    conversation if there is one, else the dark studio gradient."""
    import cv2
    import numpy as np
    from PIL import Image
    import thumbnail as T
    drafts = os.path.join(d, "drafts")
    briefs = load(drafts, conv["id"] + ".metadata.json")["thumbnail"]
    photos = [os.path.join(photos_dir, f) for f in sorted(os.listdir(photos_dir))
              if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
    if not photos:
        raise RuntimeError("The thumbnail photo folder has no photos in it.")
    cap, bg = cv2.VideoCapture(video), None
    for t in np.linspace(conv["start"], conv["end"], 40):
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, fr = cap.read()
        if ok and (cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY) > 190).mean() > 0.3:
            bg = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)); break
    colors = {"white": T.WHITE, "yellow": T.YELLOW, "red": T.RED, "black": T.BLACK}
    cache = {}
    for i, b in enumerate(briefs[:3], 1):
        want = (b.get("host_expression") or "").lower()
        photo = next((p for p in photos if want and want in os.path.basename(p).lower()), photos[(i - 1) % len(photos)])
        host = cache.setdefault(photo, T.cutout(Image.open(photo)))
        lines = [[(w, colors.get(c, T.WHITE)) for w, c in line] for line in b["lines"]]
        preset = b["preset"] if b["preset"] in ("solo", "banner", "bubble") else "solo"   # guest images: next phase
        if preset == "bubble":
            lines = [x for line in lines for x in line]
        final = os.path.join(drafts, f"{conv['id']}.thumb{i}.jpg")
        tmp = media.tmp_name(final)
        T.make(preset, host, lines, bg=bg, out=tmp)
        media.commit(tmp, final)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] in ("analyze", "render"):
        proj = os.path.abspath(sys.argv[2])
        meta = load(proj, "project.json")
        if sys.argv[1] == "analyze":
            analyze(proj, meta["source"], Reporter(on_step=print))
        else:
            c, s = effective_items(proj)
            ids = sys.argv[3:] or [i["id"] for i in c + s]
            render(proj, meta["source"], ids, rep=Reporter(on_step=print))
    else:
        print(__doc__)
