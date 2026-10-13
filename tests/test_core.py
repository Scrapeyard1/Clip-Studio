"""Regression tests for the problems found in review. Run:  python -m pytest -q tests

Each test builds its own tiny media with FFmpeg, so they run in seconds and need no models.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from fractions import Fraction

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "engine")]

import clipstudio as pipeline  # noqa: E402
import media  # noqa: E402
import paths  # noqa: E402
from timeline import Timeline, write_srt  # noqa: E402


def make_video(path, seconds=6, rate="30000/1001", size="320x180"):
    subprocess.run([paths.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", path], check=True)
    return path


# ---------- 1. setup paths ----------
def test_no_machine_specific_paths_in_code():
    bad = []
    for folder in ("engine", "app"):
        for f in os.listdir(os.path.join(ROOT, folder)):
            if f.endswith((".py", ".html")):
                text = open(os.path.join(ROOT, folder, f), encoding="utf-8").read()
                if "/home/claude" in text or "/usr/share/fonts" in text:
                    bad.append(f)
    assert not bad, f"hard-coded machine paths in {bad}"


def test_health_check_reports_missing_models(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "MODEL_FILES", {"speech recognition": [str(tmp_path / "nope.onnx")]})
    items = {c["item"]: c for c in paths.check(api_key=None)}
    assert not items["Model: speech recognition"]["ok"]
    assert "get_models.py" in items["Model: speech recognition"]["fix"]
    assert not items["Claude API key"]["ok"]


# ---------- 2. safe retries ----------
def test_empty_output_is_not_treated_as_done(tmp_path):
    d = str(tmp_path)
    out = str(tmp_path / "drafts" / "x.mp4")
    os.makedirs(os.path.dirname(out))
    open(out, "wb").close()                      # a crash left an empty file behind
    ran = []

    def fn():
        ran.append(1)
        make_video(out, 2)
    pipeline.run_step(d, "render-x", "Render x", [out], fn, pipeline.Reporter())
    assert ran, "an empty MP4 must not count as finished"
    ran.clear()
    pipeline.run_step(d, "render-x", "Render x", [out], fn, pipeline.Reporter())
    assert not ran, "a valid, recorded output should be skipped"


def test_step_fails_loudly_when_output_is_broken(tmp_path):
    out = str(tmp_path / "x.mp4")
    with pytest.raises(RuntimeError, match="didn't come out right"):
        pipeline.run_step(str(tmp_path), "k", "Render x", [out], lambda: open(out, "wb").write(b"junk" * 500),
                          pipeline.Reporter())
    assert not os.path.exists(os.path.join(tmp_path, "work", "steps", "k.json"))


def test_changed_settings_make_a_step_stale(tmp_path):
    out = make_video(str(tmp_path / "x.mp4"), 2)
    pipeline.run_step(str(tmp_path), "k", "x", [out], lambda: None, pipeline.Reporter(), inputs={"cold": [1, 2]})
    assert pipeline.step_state(str(tmp_path), "k", [out], {"cold": [1, 2]}) == "done"
    assert pipeline.step_state(str(tmp_path), "k", [out], {"cold": [1, 3]}) == "stale"


def test_commit_refuses_broken_files(tmp_path):
    tmp, final = str(tmp_path / "a.partial.mp4"), str(tmp_path / "a.mp4")
    open(tmp, "wb").write(b"\0" * 4096)
    with pytest.raises(RuntimeError):
        media.commit(tmp, final)
    assert not os.path.exists(final)


# ---------- 4. timing ----------
def test_fractional_frame_rate_survives_export(tmp_path):
    import export_timeline
    src = make_video(str(tmp_path / "ntsc.mp4"), 3)
    info = media.probe(src)
    assert info["fps"] == Fraction(30000, 1001)
    t = Timeline(info["fps"])
    t.add_source("body", 0, 3600)
    real = media.probe
    media.probe = lambda p: dict(real(p), duration=3600.0)
    try:
        tmp = export_timeline.build(str(tmp_path / "t.xml"), "t", t, {"main": src})
    finally:
        media.probe = real
    import xml.etree.ElementTree as ET
    frames = int(ET.parse(tmp).getroot().find("sequence/duration").text)
    seconds = frames * 1001 / 30000
    assert abs(seconds - 3600) < 1 / 29.97, seconds          # was 3603.6 before the fix
    seq_rate = ET.parse(tmp).getroot().find("sequence/rate")
    assert seq_rate.find("ntsc").text == "TRUE"


def test_chapters_and_captions_use_the_real_sting_length():
    from metadata import clean_chapters, final_timeline_transcript
    tr = {"duration": 100, "segments": [{"start": 50.0, "end": 52.0, "text": "Body begins",
                                         "words": [{"w": "Body", "s": 50.0, "e": 50.5}, {"w": "begins", "s": 50.5, "e": 51.0}]}]}
    t = Timeline.for_longform(Fraction(30000, 1001), [[80, 90]], [[50, 95]], sting_seconds=7.25, outro_seconds=4)
    body0 = t.body_start()
    assert abs(body0 - (10 + 7.25)) < 0.05                    # cold open 10 s + a 7.25 s sting, not 3 s
    assert final_timeline_transcript(tr, t).startswith("[00:17]")
    words = t.words_on_output(tr)
    assert abs(words[0]["s"] - body0) < 0.05
    ch = clean_chapters([["00:00", "Cold open"], ["00:20", "Talk"], ["99:00", "Too late"]], t)
    assert ch[0][0] == "00:00" and all(c[1] != "Too late" for c in ch)


def test_rendered_video_matches_its_timeline(tmp_path):
    import render_long
    src = make_video(str(tmp_path / "live.mp4"), 40)
    tr = {"duration": 40, "segments": [{"start": s, "end": s + 3, "text": "words here", "words": [
        {"w": "words", "s": s, "e": s + 1}, {"w": "here", "s": s + 1, "e": s + 2}]} for s in (0, 8, 16, 24, 32)]}
    item = {"name": "c0", "start": 0, "end": 40, "cold_open": [[24, 27], [9, 11]]}
    sting = make_video(str(tmp_path / "sting.mp4"), 2.5, rate="25")
    out = render_long.render(src, tr, item, str(tmp_path / "drafts"), {"preset": "ultrafast", "sting": sting}, quiet=[])
    t = Timeline.load(out["timeline"])
    assert abs(media.probe(out["video"])["duration"] - t.seconds) < 0.15
    assert abs(t.body_start() - (5 + 2.5)) < 0.05
    import xml.etree.ElementTree as ET
    assert int(ET.parse(out["xml"]).getroot().find("sequence/duration").text) == t.frames
    assert "-->" in open(out["srt"], encoding="utf-8").read()


# ---------- 6. plan validation ----------
def test_plan_validation_catches_bad_items():
    from plan_check import PlanError, validate
    clean, problems = validate({
        "conversations": [{"guest": "A", "start": 100, "end": 900, "cold_open": [[800, 812]]},
                          {"guest": "B", "start": "soon", "end": 50},
                          {"guest": "C", "start": 1000, "end": 99999}],
        "shorts": [{"hook": "x", "pieces": []}, {"hook": "y", "pieces": [[200, 230]]}]}, duration=2000)
    assert [c["guest"] for c in clean["conversations"]] == ["A"]
    assert [s["hook"] for s in clean["shorts"]] == ["y"]
    assert len(problems) == 3
    with pytest.raises(PlanError):
        validate({"conversations": [], "shorts": []}, duration=100)
    with pytest.raises(PlanError):
        validate("not json", duration=100)


# ---------- 3 + 5. app: uploads, edits, queue ----------
@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPSTUDIO_PROJECTS", str(tmp_path))
    monkeypatch.setattr(paths, "PROJECTS", str(tmp_path))
    for m in [m for m in sys.modules if m in ("server",)]:
        del sys.modules[m]
    sys.path.insert(0, os.path.join(ROOT, "app"))
    import server
    server.PROJECTS = str(tmp_path)
    server.SETTINGS = str(tmp_path / "settings.json")
    server.queue = server.Queue()                 # a fresh queue with no worker: jobs stay put
    from fastapi.testclient import TestClient
    return server, TestClient(server.app)  # not used as a context manager, so the real worker never starts


def test_same_file_name_uploads_never_overwrite(app_client, tmp_path):
    server, client = app_client
    v1 = make_video(str(tmp_path / "a.mp4"), 2)
    v2 = make_video(str(tmp_path / "b.mp4"), 3)
    r1 = client.post("/api/projects/upload", files={"file": ("live.mp4", open(v1, "rb"), "video/mp4")}).json()
    r2 = client.post("/api/projects/upload", files={"file": ("live.mp4", open(v2, "rb"), "video/mp4")}).json()
    assert r1["source"] != r2["source"]
    assert abs(media.probe(r1["source"])["duration"] - 2) < 0.2      # first upload untouched
    assert abs(media.probe(r2["source"])["duration"] - 3) < 0.2


def _project_with_plan(server, tmp_path, titles=("AI title",)):
    pid = server.new_project("Test live", source=make_video(str(tmp_path / "src.mp4"), 4))
    d = server.pdir(pid)
    os.makedirs(os.path.join(d, "work"), exist_ok=True)
    media.write_json(os.path.join(d, "work", "transcript.json"), {"duration": 4000, "segments": []})
    media.write_json(os.path.join(d, "work", "plan.json"), {"conversations": [
        {"id": "c100", "guest": "Guest", "topic": "t", "start": 100, "end": 900, "cold_open": [[800, 812]],
         "titles": list(titles), "protect": []}], "shorts": []})
    return pid, d


def test_edits_survive_a_new_plan(app_client, tmp_path):
    server, client = app_client
    pid, d = _project_with_plan(server, tmp_path)
    r = client.patch(f"/api/projects/{pid}/items/c100", json={"titles": ["My title"], "chosen_title": 0,
                                                              "selected": False, "approved": True, "start": 120})
    assert r.status_code == 200, r.text
    # Claude writes a brand-new plan (same conversation, new wording)
    media.write_json(os.path.join(d, "work", "plan.json"), {"conversations": [
        {"id": "c100", "guest": "Guest", "topic": "new", "start": 100, "end": 900, "cold_open": [[800, 812]],
         "titles": ["Different AI title"], "protect": []}], "shorts": []})
    item = client.get(f"/api/projects/{pid}").json()["items"][0]
    assert item["titles"] == ["My title"] and item["ai_titles"] == ["Different AI title"]
    assert item["selected"] is False and item["approved"] is True and item["start"] == 120


def test_bad_edits_are_rejected(app_client, tmp_path):
    server, client = app_client
    pid, _ = _project_with_plan(server, tmp_path)
    assert client.patch(f"/api/projects/{pid}/items/c100", json={"cold_open": [[900, 800]]}).status_code == 400
    assert client.patch(f"/api/projects/{pid}/items/c100", json={"rank": "STRONG"}).status_code == 400


def test_queue_blocks_duplicates_and_cancels(app_client, tmp_path):
    server, client = app_client
    pid, _ = _project_with_plan(server, tmp_path)
    server.queue.add(pid, "analyze")
    assert client.post(f"/api/projects/{pid}/retry").status_code == 409          # no duplicate job
    assert client.post(f"/api/projects/{pid}/cancel").json()["result"] == "removed from the line"
    assert server.load(pid)["status"]["state"] == "cancelled"


def test_progress_goes_to_the_right_project(app_client, tmp_path, monkeypatch):
    server, client = app_client
    a, _ = _project_with_plan(server, tmp_path)
    b = server.new_project("Other live", source=make_video(str(tmp_path / "o.mp4"), 2))

    def fake_analyze(d, source, rep, threads=None):
        rep.step(f"working on {os.path.basename(d)}")
    monkeypatch.setattr(server.pipeline, "analyze", fake_analyze)
    for pid in (a, b):
        server.run_job(pid, "analyze", [], threading.Event())
    assert server.load(a)["status"]["steps"] == [f"working on {a}"]
    assert server.load(b)["status"]["steps"] == [f"working on {b}"]


def test_interrupted_jobs_are_recovered_after_restart(app_client, tmp_path):
    server, _ = app_client
    pid, _ = _project_with_plan(server, tmp_path)
    server.set_status(pid, state="running", job="render", ids=["c100"])
    server.queue.recover()
    assert server.queue.position(pid) == 1
    assert server.load(pid)["status"]["state"] == "queued"


def test_cancel_stops_a_running_ffmpeg(tmp_path):
    ev = threading.Event()
    media.cancel_event.ev = ev
    threading.Timer(0.6, ev.set).start()
    t0 = time.time()
    with pytest.raises(media.Cancelled):
        media.run([paths.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
                   "-t", "600", "-c:v", "libx264", "-preset", "slow", str(tmp_path / "long.mp4")])
    media.cancel_event.ev = None
    assert time.time() - t0 < 5


def test_ffmpeg_reports_progress(tmp_path):
    seen = []
    media.progress.done, media.progress.fn = 0.0, seen.append
    try:
        media.run([paths.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30",
                   "-t", "4", "-c:v", "libx264", "-preset", "ultrafast", str(tmp_path / "p.mp4")])
    finally:
        media.progress.fn = None
    assert seen and 3.5 < max(seen) <= 4.1


def test_failed_step_leaves_no_partial_file(tmp_path):
    out = str(tmp_path / "x.mp4")

    def fn():
        open(media.tmp_name(out), "wb").write(b"half")
        raise media.Cancelled()
    with pytest.raises(media.Cancelled):
        pipeline.run_step(str(tmp_path), "k", "x", [out], fn, pipeline.Reporter())
    assert not os.path.exists(media.tmp_name(out))


# ---------- caller framing ----------
needs_models = pytest.mark.skipif(not os.path.isfile(paths.YUNET), reason="run scripts/get_models.py first")


def test_guest_library_keeps_handles_and_photos(tmp_path, monkeypatch):
    import cv2
    import numpy as np
    import guests
    monkeypatch.setattr(paths, "PROJECTS", str(tmp_path))
    g = guests.set_info("Black on YT (Mr. Clean)", "blackonyt")
    assert g["handle"] == "@blackonyt" and g["slug"] == "black-on-yt-mr-clean" and g["photo"] is None
    img = np.full((90, 120, 3), 120, np.uint8)
    ok, png = cv2.imencode(".png", img)
    g = guests.save_photo("Black on YT (Mr. Clean)", png.tobytes(), "png")
    out = cv2.imread(g["photo"])
    assert out.shape == (guests.SIZE, guests.SIZE, 3) and g["handle"] == "@blackonyt"
    with pytest.raises(ValueError):
        guests.save_photo("X", b"not an image", "jpg")
    assert [x["name"] for x in guests.all_guests()] == ["Black on YT (Mr. Clean)"]


def test_wave_follows_the_audio():
    import numpy as np
    import caller
    sr, fps = caller.SR, 30
    t = np.arange(sr * 2) / sr
    sound = np.concatenate([np.zeros(sr), 0.3 * np.sin(2 * np.pi * 300 * t[:sr])]).astype(np.float32)
    L = caller.bands(sound, 60, fps)
    assert L[:25].max() < 0.05            # silence: bars rest
    assert L[40:].max() > 0.5             # voice: bars move


def test_new_caller_photo_or_handle_makes_the_short_stale(tmp_path, monkeypatch):
    import guests
    monkeypatch.setattr(paths, "PROJECTS", str(tmp_path))
    d = str(tmp_path / "live")
    os.makedirs(os.path.join(d, "work"))
    media.write_json(os.path.join(d, "work", "plan.json"), {"conversations": [
        {"id": "c100", "guest": "Ana", "start": 100, "end": 900, "cold_open": [[800, 812]]}],
        "shorts": [{"id": "s200", "hook": "h", "pieces": [[200, 230]], "framing": "guest"}]})
    short = pipeline.effective_items(d)[1][0]
    assert pipeline.short_guest(d, short) == "Ana"                 # found from the conversation
    before = pipeline.render_inputs(d, short, {})
    guests.set_info("Ana", "@ana")
    assert pipeline.render_inputs(d, short, {}) != before


@needs_models
def test_caller_short_renders_with_badge(tmp_path):
    import render_short
    src = make_video(str(tmp_path / "live.mp4"), 12, rate="30")
    tr = {"duration": 12, "segments": [{"start": 1, "end": 3, "text": "hello there", "words": [
        {"w": "hello", "s": 1.0, "e": 1.5}, {"w": "there", "s": 1.6, "e": 2.2}]}]}
    out = render_short.render(src, tr, {"name": "s1", "hook": "Hook", "pieces": [[0.5, 4], [6, 9]],
                                        "framing": "guest"}, str(tmp_path), quiet=[],
                              guest={"name": "Ana Lee", "handle": "@ana", "photo": None})
    info = media.probe(out["video"])
    assert (info["width"], info["height"]) == (1080, 1920)
    assert abs(info["duration"] - out["seconds"]) < 0.2
    assert not [f for f in os.listdir(tmp_path) if f.startswith("short-")]      # work folder cleaned up


def test_edits_follow_a_clip_when_a_new_plan_shifts_it(tmp_path):
    d = str(tmp_path)
    media.write_json(os.path.join(d, "edits.json"), {"items": {"s1276": {"framing": "guest"},
                                                               "s9000": {"approved": True}}})
    old = {"conversations": [], "shorts": [{"id": "s1276", "pieces": [[1276.6, 1281.9], [3496.8, 3550.8]]},
                                           {"id": "s9000", "pieces": [[9000, 9030]]}]}
    new = {"conversations": [], "shorts": [{"id": "s1279", "pieces": [[1279.0, 1283.0], [3497.0, 3553.0]]}]}
    assert pipeline.carry_edits(d, old, new) == {"s1276": "s1279"}
    e = pipeline.edits(d)["items"]
    assert e["s1279"]["framing"] == "guest" and "s1276" not in e
    assert e["s9000"] == {"approved": True}        # no overlapping clip: kept, not thrown away


def test_edits_dont_jump_to_a_clip_between_far_apart_pieces(tmp_path):
    d = str(tmp_path)
    media.write_json(os.path.join(d, "edits.json"), {"items": {"s1276": {"framing": "guest"}}})
    old = {"shorts": [{"id": "s1276", "pieces": [[1276.6, 1281.9], [3496.8, 3550.8]]}]}
    new = {"shorts": [{"id": "s1960", "pieces": [[1960, 2005]]},             # sits between the pieces
                      {"id": "s1279", "pieces": [[1279.0, 1283.0], [3497.0, 3553.0]]}]}
    assert pipeline.carry_edits(d, old, new) == {"s1276": "s1279"}
