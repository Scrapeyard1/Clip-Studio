"""Clip Studio: one command from a recorded live to review-ready drafts.

    python clipstudio.py process  "D:/Lives/2026-10-01.mkv"     # everything below, in order
    python clipstudio.py render-long  <live dir> <plan.json>      # re-render one conversation
    python clipstudio.py render-short <live dir> <clip.json>      # re-render one short

Steps for `process`:
  1. extract 16 kHz audio (streams the file; a 5 GB recording uses no extra RAM)
  2. transcribe locally with word timestamps (Parakeet via sherpa-onnx; no cloud)
  3. measure quiet stretches (so sound effects and laughter are never cut)
  4. plan: Claude reads the transcript -> conversations, cold opens, titles, shorts
  5. render every conversation (long-form) and every short into <live dir>/drafts
Each step skips itself if its output already exists, so a crash resumes where it stopped.
"""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(ROOT, "engine")
sys.path.insert(0, ENGINE)


_on_step = None


def step(name, out, fn):
    if os.path.exists(out):
        print(f"[skip] {name}")
        return
    print(f"[run ] {name}")
    if _on_step:
        _on_step(name)
    fn()


def process(video, threads=os.cpu_count(), workdir=None, on_step=None, config=None):
    global _on_step
    _on_step = on_step
    base = os.path.splitext(os.path.basename(video))[0]
    d = workdir or os.path.join(os.path.dirname(os.path.abspath(video)), base + ".clipstudio")
    os.makedirs(d, exist_ok=True)
    wav, tr, qj, plan = (os.path.join(d, x) for x in ("audio16k.wav", "transcript.json", "quiet.json", "plan.json"))

    step("extract audio", wav, lambda: subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", video, "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", wav], check=True))
    step("transcribe", tr, lambda: subprocess.run(
        [sys.executable, os.path.join(ENGINE, "transcribe.py"), wav, tr, "--threads", str(threads)], check=True))

    def quiet():
        from cuts import detect_quiet
        json.dump(detect_quiet(wav), open(qj, "w"))
    step("measure quiet stretches", qj, quiet)

    def make_plan():
        from moments import plan as claude_plan
        json.dump(claude_plan(json.load(open(tr))), open(plan, "w"), indent=1)
    step("plan with Claude", plan, make_plan)

    p = json.load(open(plan))
    drafts = os.path.join(d, "drafts")
    os.makedirs(drafts, exist_ok=True)
    for i, c in enumerate(p["conversations"], 1):
        c = dict(c, name=f"{i:02d}-long-{slug(c['guest'])}")
        f = os.path.join(d, c["name"] + ".json"); json.dump(c, open(f, "w"))
        step(f"long-form {c['name']}", os.path.join(drafts, c["name"] + ".mp4"), lambda f=f: subprocess.run(
            [sys.executable, os.path.join(ENGINE, "render_long.py"), "--video", video, "--transcript", tr,
             "--quiet", qj, "--plan", f, "--out", drafts, "--config", json.dumps(config or {})], check=True))

        def meta(c=c):
            from cuts import safe_cuts
            from metadata import write
            keep = safe_cuts(json.load(open(tr)), c["start"], c["end"], "long", c.get("protect", []),
                             json.load(open(qj))).keep
            json.dump(write(json.load(open(tr)), c, keep), open(os.path.join(drafts, c["name"] + ".metadata.json"), "w"),
                      indent=1)
        step(f"titles {c['name']}", os.path.join(drafts, c["name"] + ".metadata.json"), meta)
        if (config or {}).get("host_photos"):
            step(f"thumbnails {c['name']}", os.path.join(drafts, c["name"] + ".thumb1.jpg"),
                 lambda c=c: make_thumbnails(video, c, drafts, config["host_photos"]))
    for i, s in enumerate(p["shorts"], 1):
        s = dict(s, name=f"{i:02d}-short-{slug(s['category'])}")
        f = os.path.join(d, s["name"] + ".json"); json.dump(s, open(f, "w"))
        step(f"short {s['name']}", os.path.join(drafts, s["name"] + ".mp4"), lambda f=f: subprocess.run(
            [sys.executable, os.path.join(ENGINE, "render_short.py"), "--video", video, "--transcript", tr,
             "--quiet", qj, "--clip", f, "--out", drafts], check=True))
    print("drafts ready in", drafts)


def make_thumbnails(video, conv, drafts, photos_dir):
    """3 thumbnails from the titles step's briefs. Host photo is picked by expression from the photo
    folder (file names like pointing-1.jpg, shocked.png). Background: a verse screen from the
    conversation if there is one, else the dark studio gradient."""
    import cv2, numpy as np
    from PIL import Image
    import thumbnail as T
    briefs = json.load(open(os.path.join(drafts, conv["name"] + ".metadata.json")))["thumbnail"]
    photos = [os.path.join(photos_dir, f) for f in sorted(os.listdir(photos_dir))
              if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
    if not photos:
        return
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
        T.make(preset, host, lines, bg=bg, out=os.path.join(drafts, f"{conv['name']}.thumb{i}.jpg"))


def slug(s):
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in s).strip("-")[:40]


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "process":
        process(sys.argv[2])
    else:
        print(__doc__)
