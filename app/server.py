"""Clip Studio app: a local server + browser interface.

    python app/server.py            -> opens http://localhost:8765

Everything runs on this computer. Recordings are never uploaded anywhere; only transcript
text goes to Claude for planning and titles.

Flow per project:  add recording -> Analyze (queued) -> Review proposals -> Render selected
(queued) -> watch drafts, approve, export. One heavy job runs at a time; the rest wait in line.
"""
import collections
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
import webbrowser

import uvicorn
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "engine"))

import clipstudio as pipeline  # noqa: E402
import media  # noqa: E402
import paths  # noqa: E402

PROJECTS = paths.PROJECTS
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".flv"}
os.makedirs(PROJECTS, exist_ok=True)
SETTINGS = os.path.join(PROJECTS, "settings.json")
SETTING_KEYS = {"anthropic_api_key", "sting", "outro", "host_photos", "recordings_folder", "guest_audio_track"}
EDITABLE = {
    "long": {"guest", "topic", "start", "end", "cold_open", "protect", "selected",
             "titles", "chosen_title", "description", "chosen_thumbnail", "approved"},
    "short": {"hook", "category", "pieces", "framing", "protect", "selected", "approved", "guest"},
}

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app):
    start_worker()
    for c in [c for c in health()["checks"] if not c["ok"]]:
        print(f"[setup] {c['item']}: missing. {c['fix']}")
    yield


app = FastAPI(title="Clip Studio", lifespan=lifespan)
_lock = threading.RLock()


# ---------------- settings ----------------
def settings():
    s = json.load(open(SETTINGS, encoding="utf-8")) if os.path.exists(SETTINGS) else {}
    if s.get("anthropic_api_key"):
        os.environ["ANTHROPIC_API_KEY"] = s["anthropic_api_key"]
    return s


@app.get("/api/settings")
def get_settings():
    s = settings()
    k = s.get("anthropic_api_key", "")
    return {**{x: s.get(x, "") for x in SETTING_KEYS}, "anthropic_api_key": ("saved, ends in " + k[-4:]) if k else ""}


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    s = settings()
    for k, v in body.items():
        if k in SETTING_KEYS and not (k == "anthropic_api_key" and (not v or v.startswith("saved"))):
            v = str(v).strip().strip('"')
            if k in ("sting", "outro") and v and not media.valid_media(v):
                raise HTTPException(400, f"The {k} file wasn't found there, or it isn't a playable video.")
            if k == "guest_audio_track" and v and not v.isdigit():
                raise HTTPException(400, "The caller's audio track is a number: 1 for the first track, 2 for the second...")
            if k in ("host_photos", "recordings_folder") and v and not os.path.isdir(v):
                raise HTTPException(400, "That folder wasn't found.")
            s[k] = v
    media.write_json(SETTINGS, s)
    settings()
    return get_settings()


@app.get("/api/health")
def health():
    """What's missing for the app to work: FFmpeg, models, fonts, API key."""
    checks = paths.check(settings().get("anthropic_api_key"))
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


# ---------------- project files ----------------
def pdir(pid):
    d = os.path.join(PROJECTS, os.path.basename(pid))
    if not os.path.isfile(os.path.join(d, "project.json")):
        raise HTTPException(404, "No project with that name")
    return d


def load(pid):
    return json.load(open(os.path.join(pdir(pid), "project.json"), encoding="utf-8"))


def save(pid, proj):
    with _lock:
        media.write_json(os.path.join(pdir(pid), "project.json"), proj)


def set_status(pid, **kw):
    with _lock:
        p = load(pid)
        p["status"].update(kw)
        save(pid, p)


def load_edits(pid):
    return pipeline.edits(pdir(pid))


def save_edits(pid, e):
    with _lock:
        media.write_json(os.path.join(pdir(pid), "edits.json"), e)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48] or "live"


def view(pid):
    """The project as the interface sees it: AI plan + the person's edits + what's been rendered."""
    d = pdir(pid)
    p = load(pid)
    e = load_edits(pid)
    cfg = render_cfg()
    plan = pipeline.load(os.path.join(d, "work"), "plan.json") or {}
    convs, shorts = pipeline.effective_items(d) if plan else ([], [])
    items = []
    for it in convs + shorts:
        mine = e["items"].get(it["id"], {})
        outs = pipeline.outputs_for(d, it)
        key = f"render-{it['id']}"
        state = pipeline.step_state(d, key, list(outs.values()) if it["kind"] == "long" else [outs["video"]],
                                    pipeline.render_inputs(d, it, cfg))
        v = {**it, "selected": mine.get("selected", it["kind"] == "long" and it.get("rank") != "WEAK"),
             "approved": mine.get("approved", False), "render": state,
             "problems": pipeline.render_problems(it)}
        rel = lambda f: os.path.relpath(f, d).replace("\\", "/")
        if os.path.exists(outs["video"]):
            v["files"] = {k: rel(f) for k, f in outs.items() if os.path.exists(f)}
        if it["kind"] == "short":
            import guests
            gname = pipeline.short_guest(d, it) or ""
            g = guests.get(gname) if gname else None
            v["guest_name"] = gname
            v["guest_info"] = {"handle": g["handle"], "photo": f"/guest-photo/{g['slug']}.png?v={g['version']}"
                               if g["photo"] else None} if g else {"handle": "", "photo": None}
        if it["kind"] == "long":
            cuts = pipeline.load(os.path.join(d, "drafts"), it["id"] + ".cuts.json")
            if cuts:
                v.update(minutes_in=cuts["source_minutes"], minutes_out=cuts["output_minutes"], cuts=cuts["removed"])
            meta = pipeline.load(os.path.join(d, "drafts"), it["id"] + ".metadata.json") or {}
            ai_titles = meta.get("titles") or it.get("titles") or []
            v["titles"] = mine.get("titles", ai_titles)       # the person's wording wins
            v["ai_titles"] = ai_titles
            v["chosen_title"] = mine.get("chosen_title", 0)
            v["description"] = mine.get("description", meta.get("description", ""))
            v["chapters"] = meta.get("chapters", [])
            v["tags"] = meta.get("tags", [])
            v["chosen_thumbnail"] = mine.get("chosen_thumbnail", 0)
            v["thumbnails"] = [rel(f) for f in (os.path.join(d, "drafts", f"{it['id']}.thumb{k}.jpg") for k in (1, 2, 3))
                               if os.path.exists(f)]
        items.append(v)
    tr = pipeline.load(os.path.join(d, "work"), "transcript.json")
    return {**p, "duration": tr["duration"] if tr else p.get("duration"),
            "items": items, "problems": plan.get("problems", []),
            "glossary": pipeline.glossary_text(d), "queue_position": queue.position(pid)}


# ---------------- the job queue ----------------
class Queue:
    """One heavy job at a time (FFmpeg and transcription already use every core).
    Jobs live in each project's status, so they survive an app restart."""
    def __init__(self):
        self.q = collections.deque()
        self.cv = threading.Condition()
        self.current = None              # (pid, cancel_event)

    def position(self, pid):
        with self.cv:
            if self.current and self.current[0] == pid:
                return 0
            ids = [j[0] for j in self.q]
            return ids.index(pid) + 1 if pid in ids else None

    def add(self, pid, job, ids=None):
        with self.cv:
            if (self.current and self.current[0] == pid) or any(j[0] == pid for j in self.q):
                raise HTTPException(409, "This live already has a job waiting or running.")
            set_status(pid, state="queued", queued_at=time.time(), job=job, ids=ids or [], error=None, steps=[], step=None, progress=None)
            self.q.append((pid, job, ids or []))
            self.cv.notify()

    def cancel(self, pid):
        with self.cv:
            for j in list(self.q):
                if j[0] == pid:
                    self.q.remove(j)
                    set_status(pid, state="cancelled", step=None)
                    return "removed from the line"
            if self.current and self.current[0] == pid:
                self.current[1].set()
                return "stopping"
        raise HTTPException(409, "Nothing is running for this live.")

    def worker(self):
        while True:
            with self.cv:
                while not self.q:
                    self.cv.wait()
                pid, job, ids = self.q.popleft()
                ev = threading.Event()
                self.current = (pid, ev)
            try:
                run_job(pid, job, ids, ev)
            finally:
                with self.cv:
                    self.current = None

    def recover(self):
        """After a restart, put interrupted and waiting jobs back in line (oldest first)."""
        found = []
        for name in os.listdir(PROJECTS):
            f = os.path.join(PROJECTS, name, "project.json")
            if os.path.isfile(f):
                p = json.load(open(f, encoding="utf-8"))
                st = p.get("status", {})
                if st.get("state") in ("queued", "running"):
                    found.append((st.get("queued_at", 0), p["id"], st.get("job", "analyze"), st.get("ids", [])))
        for _, pid, job, ids in sorted(found):
            with self.cv:
                set_status(pid, state="queued", note="Picked up again after the app restarted.")
                self.q.append((pid, job, ids))
                self.cv.notify()


queue = Queue()


def render_cfg():
    s = settings()
    cfg = {k: s[k] for k in ("sting", "outro", "host_photos", "encoder") if s.get(k)}
    if str(s.get("guest_audio_track", "")).isdigit() and int(s["guest_audio_track"]) > 0:
        cfg["guest_audio_track"] = int(s["guest_audio_track"]) - 1      # people count tracks from 1
    return cfg


def run_job(pid, job, ids, ev):
    media.cancel_event.ev = ev
    media.progress.fn = None          # never report into a previous job's progress bar

    def on_step(name):
        with _lock:
            p = load(pid)
            st = p["status"]
            st["step"] = name
            if not st.get("steps") or st["steps"][-1] != name:
                st.setdefault("steps", []).append(name)
            save(pid, p)

    rep = pipeline.Reporter(on_step=on_step, on_progress=lambda pct: set_status(pid, progress=pct), cancel_event=ev)
    set_status(pid, state="running", started_at=time.time())
    try:
        settings()
        p = load(pid)
        d = pdir(pid)
        if job == "fetch":
            fetch(pid, rep)
            p = load(pid)
            job = "analyze"
        if job == "analyze":
            if not media.valid_media(p["source"], need_video=True, need_audio=True):
                raise RuntimeError("The recording can't be read. Check the file plays, then Retry.")
            pipeline.analyze(d, p["source"], rep)
            set_status(pid, state="review", step=None, progress=None)
        elif job == "render":
            pipeline.render(d, p["source"], ids, render_cfg(), rep)
            set_status(pid, state="review", step=None, progress=None, rendered_at=time.time())
    except media.Cancelled:
        set_status(pid, state="cancelled", step=None, note="Stopped. Finished steps are kept; Retry continues.")
    except Exception as e:
        msg = str(e)
        if "ANTHROPIC_API_KEY" in msg or "api_key" in msg.lower() or "authentication" in msg.lower():
            msg = "Finding conversations needs your Claude API key. Add it in Settings, then press Retry."
        set_status(pid, state="error", error=msg[:1200], trace=traceback.format_exc()[-3000:])
    finally:
        media.cancel_event.ev = None


def fetch(pid, rep):
    import fetch_youtube as yt
    p = load(pid)
    rep.step("Get the video from YouTube")
    meta = yt.info(p["link"])
    with _lock:
        p = load(pid)
        p["title"] = meta["title"]
        if meta.get("date"):
            p["date"] = f"{meta['date'][:4]}-{meta['date'][4:6]}-{meta['date'][6:]}"
        save(pid, p)
    path = yt.download(p["link"], os.path.join(pdir(pid), "source"), rep.progress)
    with _lock:
        p = load(pid)
        p["source"] = path
        save(pid, p)


# ---------------- adding recordings ----------------
def new_project(title, source=None, link=None):
    base = time.strftime("%Y-%m-%d-") + slug(title)
    pid, n = base, 2
    while os.path.exists(os.path.join(PROJECTS, pid)):
        pid, n = f"{base}-{n}", n + 1
    d = os.path.join(PROJECTS, pid)
    os.makedirs(os.path.join(d, "source"))
    media.write_json(os.path.join(d, "project.json"), {
        "id": pid, "title": title, "date": time.strftime("%Y-%m-%d"), "source": source, "link": link,
        "status": {"state": "new", "queued_at": time.time()}})
    return pid


@app.post("/api/projects/from-path")
def add_from_path(body: dict = Body(...)):
    """Use a recording where it already sits (OBS folder): nothing is copied."""
    path = os.path.abspath(str(body.get("path", "")).strip().strip('"'))
    if not os.path.isfile(path):
        raise HTTPException(400, "That file wasn't found. Check the path and try again.")
    if os.path.splitext(path)[1].lower() not in VIDEO_EXT:
        raise HTTPException(400, "Use a video file: MP4, MOV or MKV.")
    pid = new_project(body.get("title") or os.path.splitext(os.path.basename(path))[0], source=path)
    queue.add(pid, "analyze")
    return load(pid)


@app.post("/api/projects/upload")
async def add_upload(file: UploadFile = File(...)):
    """Drag and drop: streamed to disk in 8 MB pieces into this project's own folder,
    so two recordings with the same file name never overwrite each other."""
    name = os.path.basename(file.filename or "recording.mp4")
    if os.path.splitext(name)[1].lower() not in VIDEO_EXT:
        raise HTTPException(400, "Use a video file: MP4, MOV or MKV.")
    pid = new_project(os.path.splitext(name)[0])
    dest = os.path.join(pdir(pid), "source", name)
    tmp = dest + ".partial"
    with open(tmp, "wb") as f:
        while chunk := await file.read(8 * 1024 * 1024):
            f.write(chunk)
    os.replace(tmp, dest)
    with _lock:
        p = load(pid); p["source"] = dest; save(pid, p)
    queue.add(pid, "analyze")
    return load(pid)


@app.post("/api/projects/from-link")
def add_from_link(body: dict = Body(...)):
    url = str(body.get("url", "")).strip()
    if not re.match(r"^https?://\S+$", url):
        raise HTTPException(400, "Paste the full link, starting with https://")
    pid = new_project("YouTube video", link=url)
    queue.add(pid, "fetch")
    return load(pid)


# ---------------- projects ----------------
@app.get("/api/projects")
def projects():
    out = []
    for name in sorted(os.listdir(PROJECTS), reverse=True):
        f = os.path.join(PROJECTS, name, "project.json")
        if os.path.isfile(f):
            p = json.load(open(f, encoding="utf-8"))
            out.append({"id": p["id"], "title": p["title"], "date": p.get("date"), "state": p["status"].get("state"),
                        "step": p["status"].get("step")})
    return out


@app.get("/api/projects/{pid}")
def project(pid: str):
    return view(pid)


@app.patch("/api/projects/{pid}/items/{iid}")
def edit_item(pid: str, iid: str, changes: dict = Body(...)):
    """Save a person's change. Stored in edits.json, never in the AI's files, so regenerating
    plans, titles or renders can't overwrite it."""
    d = pdir(pid)
    convs, shorts = pipeline.effective_items(d)
    item = next((i for i in convs + shorts if i["id"] == iid), None)
    if not item:
        raise HTTPException(404, "No conversation or short with that id")
    bad = set(changes) - EDITABLE[item["kind"]]
    if bad:
        raise HTTPException(400, f"Can't change: {', '.join(sorted(bad))}")
    dur = (pipeline.load(os.path.join(d, "work"), "transcript.json") or {}).get("duration", 1e9)
    for k in ("cold_open", "pieces", "protect"):
        if k in changes:
            spans = changes[k]
            if not isinstance(spans, list) or any(not (isinstance(s, list) and len(s) == 2 and 0 <= s[0] < s[1] <= dur + 1)
                                                  for s in spans):
                raise HTTPException(400, "Each clip needs a start before its end, inside the recording.")
    if "framing" in changes:
        import render_short
        if changes["framing"] not in render_short.FRAMINGS:
            raise HTTPException(400, "Unknown framing")
    if "guest" in changes and not (isinstance(changes["guest"], str) and len(changes["guest"]) <= 60):
        raise HTTPException(400, "Guest name is too long")
    for k in ("start", "end"):
        if k in changes and not (isinstance(changes[k], (int, float)) and 0 <= changes[k] <= dur + 1):
            raise HTTPException(400, "That time is outside the recording.")
    with _lock:
        e = load_edits(pid)
        e["items"].setdefault(iid, {}).update(changes)
        save_edits(pid, e)
    return next(i for i in view(pid)["items"] if i["id"] == iid)


@app.put("/api/projects/{pid}/glossary")
def put_glossary(pid: str, body: dict = Body(...)):
    import glossary
    text = str(body.get("text", ""))
    with _lock:
        e = load_edits(pid)
        e["glossary"] = text
        save_edits(pid, e)
    tr = pipeline.load(os.path.join(pdir(pid), "work"), "transcript.json")
    counts = glossary.apply(tr, text)[1] if tr else {}
    return {"text": text, "matches": counts}


@app.post("/api/projects/{pid}/render")
def render(pid: str, body: dict = Body(...)):
    ids = [str(i) for i in body.get("ids", [])]
    if not ids:
        raise HTTPException(400, "Select at least one conversation or short.")
    v = view(pid)
    by_id = {i["id"]: i for i in v["items"]}
    blocked = [f"{by_id[i].get('guest') or by_id[i].get('hook')}: {' '.join(by_id[i]['problems'])}"
               for i in ids if i in by_id and by_id[i]["problems"]]
    if blocked:
        raise HTTPException(400, "Fix these first. " + " | ".join(blocked))
    queue.add(pid, "render", ids)
    return {"queued": ids}


@app.post("/api/projects/{pid}/cancel")
def cancel(pid: str):
    return {"result": queue.cancel(pid)}


@app.post("/api/projects/{pid}/retry")
def retry(pid: str):
    p = load(pid)
    st = p["status"]
    job = st.get("job") or ("fetch" if p.get("link") and not p.get("source") else "analyze")
    queue.add(pid, job, st.get("ids"))
    return load(pid)


@app.post("/api/projects/{pid}/replan")
def replan(pid: str):
    """Ask Claude for a fresh plan. The person's edits are kept and re-applied to items whose id survives."""
    d = pdir(pid)
    for f in (os.path.join(d, "work", "plan.json"), os.path.join(d, "work", "steps", "plan.json")):
        if os.path.exists(f):
            os.remove(f)
    queue.add(pid, "analyze")
    return load(pid)


# ---------------- review helpers ----------------
@app.get("/api/projects/{pid}/preview")
def preview(pid: str, start: float = Query(...), end: float = Query(...)):
    """A small playable clip of the recording, cut on demand (works for MKV sources too)."""
    d = pdir(pid)
    src = load(pid)["source"]
    start, end = max(0.0, start), max(start + 0.5, end)
    if end - start > 600:
        raise HTTPException(400, "Previews are limited to 10 minutes.")
    cache = os.path.join(d, "work", "previews")
    os.makedirs(cache, exist_ok=True)
    out = os.path.join(cache, f"{start:.2f}-{end:.2f}.mp4")
    if not media.valid_media(out, min_seconds=0.3):
        tmp = media.tmp_name(out) + f".{uuid.uuid4().hex[:6]}.mp4"
        media.run([paths.ffmpeg(), "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", src,
                   "-vf", "scale=-2:360", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                   "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", tmp])
        os.replace(tmp, out)
    return FileResponse(out, media_type="video/mp4")


@app.get("/api/projects/{pid}/transcript")
def transcript(pid: str, start: float = Query(...), end: float = Query(...)):
    """The words around a moment, corrected, for reading context next to a preview."""
    d = pdir(pid)
    tr = pipeline.corrected_transcript(d)
    return [{"start": s["start"], "end": s["end"], "text": s["text"]}
            for s in tr["segments"] if s["end"] > start and s["start"] < end]


# ---------------- guest library (photos and handles, shared by every live) ----------------
@app.get("/api/guests")
def list_guests():
    import guests
    return [{"name": g["name"], "handle": g["handle"],
             "photo": f"/guest-photo/{g['slug']}.png?v={g['version']}" if g["photo"] else None}
            for g in guests.all_guests()]


@app.put("/api/guests")
def put_guest(body: dict = Body(...)):
    import guests
    name = str(body.get("name") or "").strip()
    if not name or len(name) > 60:
        raise HTTPException(400, "Give the guest a name first.")
    guests.set_info(name, str(body.get("handle") or ""))
    return list_guests()


@app.post("/api/guests/photo")
def guest_photo(name: str = Form(...), file: UploadFile = File(...)):
    """Saves the original and builds the enhanced photo (upscaled, face restored, centred). ~10 s."""
    import guests
    name = name.strip()
    if not name:
        raise HTTPException(400, "Give the guest a name first.")
    data = file.file.read(25 * 1024 * 1024 + 1)
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(400, "That photo is over 25 MB.")
    try:
        guests.save_photo(name, data, os.path.splitext(file.filename or "")[1])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return list_guests()


@app.get("/guest-photo/{slug}.png")
def guest_photo_file(slug: str):
    import guests
    f = os.path.join(guests.root(), re.sub(r"[^a-z0-9-]", "", slug), "photo.png")
    if not os.path.isfile(f):
        raise HTTPException(404, "No photo yet")
    return FileResponse(f)


@app.get("/media/{pid}/{path:path}")
def media_file(pid: str, path: str):
    base = os.path.realpath(pdir(pid))
    f = os.path.realpath(os.path.join(base, path))
    if not f.startswith(base + os.sep):
        raise HTTPException(403, "Outside the project folder")
    if not os.path.isfile(f):
        raise HTTPException(404, "File not found")
    name = os.path.basename(f)
    return FileResponse(f, filename=name if name.endswith((".xml", ".srt")) else None)


app.mount("/fonts", StaticFiles(directory=paths.FONTS), name="fonts")
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="ui")


def start_worker():
    queue.recover()
    threading.Thread(target=queue.worker, daemon=True, name="clipstudio-worker").start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
