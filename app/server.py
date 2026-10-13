"""Clip Studio app: a local server + browser interface.

    python app/server.py            -> opens http://localhost:8765

Everything runs on this computer. Recordings are never uploaded anywhere;
only the transcript text goes to Claude for planning and titles.
"""
import json, os, re, shutil, sys, threading, time, traceback, webbrowser
from fastapi import FastAPI, HTTPException, UploadFile, File, Body
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS = os.environ.get("CLIPSTUDIO_PROJECTS", os.path.join(ROOT, "projects"))
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".flv"}
os.makedirs(PROJECTS, exist_ok=True)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "engine"))

SETTINGS = os.path.join(PROJECTS, "settings.json")
SETTING_KEYS = {"anthropic_api_key", "sting", "outro", "host_photos", "recordings_folder"}

app = FastAPI(title="Clip Studio")


def settings():
    s = json.load(open(SETTINGS)) if os.path.exists(SETTINGS) else {}
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
            v = v.strip().strip('"')
            if k in ("sting", "outro") and v and not os.path.isfile(v):
                raise HTTPException(400, f"The {k} file wasn't found at that location.")
            if k in ("host_photos", "recordings_folder") and v and not os.path.isdir(v):
                raise HTTPException(400, "That folder wasn't found.")
            s[k] = v
    json.dump(s, open(SETTINGS, "w"), indent=1)
    settings()
    return get_settings()
_lock = threading.Lock()


def pdir(pid):
    d = os.path.join(PROJECTS, os.path.basename(pid))
    if not os.path.isdir(d):
        raise HTTPException(404, "No project with that name")
    return d


def load(pid):
    return json.load(open(os.path.join(pdir(pid), "project.json")))


def save(pid, proj):
    with _lock:
        tmp = os.path.join(pdir(pid), "project.json.tmp")
        json.dump(proj, open(tmp, "w"), indent=1)
        os.replace(tmp, os.path.join(pdir(pid), "project.json"))


@app.get("/api/projects")
def projects():
    out = []
    for d in sorted(os.listdir(PROJECTS), reverse=True):
        f = os.path.join(PROJECTS, d, "project.json")
        if os.path.exists(f):
            p = json.load(open(f))
            out.append({"id": p["id"], "title": p["title"], "date": p.get("date"), "step": p["status"]["step"],
                        "rendered": sum(c.get("rendered", False) for c in p["conversations"]),
                        "conversations": len(p["conversations"]), "shorts": len(p.get("shorts", []))})
    return out


@app.get("/api/projects/{pid}")
def project(pid: str):
    return load(pid)


@app.patch("/api/projects/{pid}/items/{name}")
def edit_item(pid: str, name: str, changes: dict = Body(...)):
    """Save edits from the review screen: chosen title, edited titles, description, thumbnail, approval."""
    allowed = {"titles", "chosen_title", "description", "chosen_thumbnail", "approved", "hook", "tags", "pinned_comment"}
    proj = load(pid)
    for item in proj["conversations"] + proj.get("shorts", []):
        if item["name"] == name:
            item.update({k: v for k, v in changes.items() if k in allowed})
            save(pid, proj)
            return item
    raise HTTPException(404, "No video with that name in this project")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48] or "live"


def new_project(src_path, title, link=None):
    base = time.strftime("%Y-%m-%d-") + slug(title)
    pid, n = base, 2
    while os.path.exists(os.path.join(PROJECTS, pid)):
        pid, n = f"{base}-{n}", n + 1
    d = os.path.join(PROJECTS, pid)
    os.makedirs(d, exist_ok=True)
    proj = {"id": pid, "title": title, "date": time.strftime("%Y-%m-%d"), "source": src_path, "link": link,
            "status": {"step": "queued", "steps": [], "error": None}, "conversations": [], "shorts": []}
    json.dump(proj, open(os.path.join(d, "project.json"), "w"), indent=1)
    threading.Thread(target=fetch_then_run if link else run_pipeline, args=(pid,), daemon=True).start()
    return proj


@app.post("/api/projects/from-link")
def add_from_link(body: dict = Body(...)):
    """A YouTube link (your own video or live replay): downloaded on this computer, then processed."""
    url = body.get("url", "").strip()
    if not re.match(r"^https?://\S+$", url):
        raise HTTPException(400, "Paste the full link, starting with https://")
    return new_project(None, "YouTube video", link=url)


def fetch_then_run(pid):
    import fetch_youtube as yt
    p = load(pid)
    p["status"].update(step="Getting the video from YouTube", steps=["Getting the video from YouTube"], progress=0)
    save(pid, p)
    try:
        meta = yt.info(p["link"])
        p = load(pid)
        p["title"] = meta["title"]
        if meta.get("date"):
            p["date"] = f"{meta['date'][:4]}-{meta['date'][4:6]}-{meta['date'][6:]}"
        save(pid, p)

        def prog(pct):
            q = load(pid); q["status"]["progress"] = pct; save(pid, q)
        path = yt.download(p["link"], os.path.join(PROJECTS, "_recordings"), prog)
        p = load(pid)
        p["source"] = path
        p["status"].pop("progress", None)
        save(pid, p)
    except Exception as e:
        p = load(pid)
        p["status"].update(step="error", error=str(e) if isinstance(e, yt.FetchError) else f"Download failed: {e}")
        save(pid, p)
        return
    run_pipeline(pid)


@app.post("/api/projects/from-path")
def add_from_path(body: dict = Body(...)):
    """Use a recording where it already sits (OBS folder): nothing is copied, so 5 GB files cost nothing."""
    path = body.get("path", "").strip().strip('"')
    if not os.path.isfile(path):
        raise HTTPException(400, "That file wasn't found. Check the path and try again.")
    if os.path.splitext(path)[1].lower() not in VIDEO_EXT:
        raise HTTPException(400, "Use a video file: MP4, MOV or MKV.")
    return new_project(path, body.get("title") or os.path.splitext(os.path.basename(path))[0])


@app.post("/api/projects/upload")
async def add_upload(file: UploadFile = File(...)):
    """Drag and drop: the file is streamed to disk in 8 MB pieces, never held in memory."""
    if os.path.splitext(file.filename)[1].lower() not in VIDEO_EXT:
        raise HTTPException(400, "Use a video file: MP4, MOV or MKV.")
    inbox = os.path.join(PROJECTS, "_recordings")
    os.makedirs(inbox, exist_ok=True)
    dest = os.path.join(inbox, os.path.basename(file.filename))
    with open(dest, "wb") as f:
        while chunk := await file.read(8 * 1024 * 1024):
            f.write(chunk)
    return new_project(dest, os.path.splitext(file.filename)[0])


@app.post("/api/projects/{pid}/retry")
def retry(pid: str):
    """Start again from the step that failed; finished steps are skipped."""
    p = load(pid)
    p["status"].update(step="queued", error=None)
    save(pid, p)
    need_download = p.get("link") and not (p.get("source") and os.path.exists(p["source"]))
    threading.Thread(target=fetch_then_run if need_download else run_pipeline, args=(pid,), daemon=True).start()
    return p


def run_pipeline(pid):
    """Runs clipstudio.process and mirrors its progress into project.json for the interface."""
    import clipstudio
    settings()          # puts the saved API key in place for the planning step
    proj = load(pid)

    def step(name):
        p = load(pid)
        name = name[0].upper() + name[1:]
        p["status"]["step"] = name
        if not p["status"]["steps"] or p["status"]["steps"][-1] != name:
            p["status"]["steps"].append(name)
        save(pid, p)
    try:
        cfg = {k: v for k, v in settings().items() if k in ("sting", "outro", "host_photos") and v}
        clipstudio.process(proj["source"], workdir=pdir(pid), on_step=step, config=cfg)
        p = load(pid)
        sync_manifest(p)
        p["status"]["step"] = "done"
        save(pid, p)
    except Exception as e:
        p = load(pid)
        msg = str(e)
        if "ANTHROPIC_API_KEY" in msg or "api_key" in msg.lower():
            msg = "Planning needs your Anthropic API key. Add it in Settings, then press Retry."
        p["status"].update(step="error", error=msg, trace=traceback.format_exc()[-3000:])
        save(pid, p)


def sync_manifest(p):
    """Fill the project's conversation and short lists from the plan and the rendered drafts."""
    d = pdir(p["id"])
    plan = json.load(open(os.path.join(d, "plan.json")))
    p["duration"] = json.load(open(os.path.join(d, "transcript.json")))["duration"]
    convs = []
    for i, c in enumerate(plan["conversations"], 1):
        name = f"{i:02d}-long-{clipstudio_slug(c['guest'])}"
        base = os.path.join("drafts", name)
        item = {"name": name, "guest": c["guest"], "topic": c.get("topic", ""), "start": c["start"], "end": c["end"],
                "rank": c.get("rank"), "rendered": os.path.exists(os.path.join(d, base + ".mp4")),
                "video": base + ".mp4", "srt": base + ".srt", "xml": base + ".xml", "edl": base + ".edl",
                "cold_open": c.get("cold_open", []), "chosen_title": 0, "chosen_thumbnail": 0,
                "thumbnails": [f"{base}.thumb{i}.jpg" for i in (1, 2, 3) if os.path.exists(os.path.join(d, f"{base}.thumb{i}.jpg"))]}
        cj = os.path.join(d, base + ".cuts.json")
        if os.path.exists(cj):
            cuts = json.load(open(cj))
            item.update(minutes_in=cuts["source_minutes"], minutes_out=cuts["output_minutes"],
                        cuts=[[x["from"], x["to"]] for x in cuts["removed"]])
        mj = os.path.join(d, base + ".metadata.json")
        if os.path.exists(mj):
            m = json.load(open(mj))
            item.update(titles=m["titles"], description=m["description"], chapters=m["chapters"],
                        tags=m["tags"], pinned_comment=m.get("pinned_comment", ""))
        else:
            item.update(titles=c.get("titles", []), description="", chapters=[], tags=[])
        convs.append(item)
    p["conversations"] = convs
    p["shorts"] = [{"name": f"{i:02d}-short-{clipstudio_slug(s['category'])}", "category": s["category"],
                    "hook": s["hook"], "video": f"drafts/{i:02d}-short-{clipstudio_slug(s['category'])}.mp4",
                    "approved": False} for i, s in enumerate(plan.get("shorts", []), 1)]


def clipstudio_slug(s):
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in s).strip("-")[:40]


@app.get("/media/{pid}/{path:path}")
def media(pid: str, path: str):
    base = os.path.realpath(pdir(pid))
    f = os.path.realpath(os.path.join(base, path))
    if not f.startswith(base + os.sep) and not os.path.islink(os.path.join(base, path)):
        raise HTTPException(403, "Outside the project folder")
    if not os.path.exists(f):
        raise HTTPException(404, "File not found")
    name = os.path.basename(path)
    return FileResponse(f, filename=name if name.endswith((".xml", ".srt", ".edl")) else None)


app.mount("/fonts", StaticFiles(directory=os.path.join(ROOT, "assets", "fonts")), name="fonts")
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="ui")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
