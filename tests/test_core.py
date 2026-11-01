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


# ---------- progress, pictures, chat editor ----------
def test_program_output_reaches_the_progress_bar(tmp_path):
    seen = []
    media.run([sys.executable, "-c", "import sys\nfor i in (10, 50, 90): print(f'  {i}s / 100s  speed 9.0x', flush=True)"],
              on_line=lambda l: seen.append(pipeline.transcriber_fraction(l)))
    time.sleep(0.2)
    assert seen == [0.1, 0.5, 0.9]
    assert pipeline.transcriber_fraction("done 12 segments") is None


def test_overall_bar_moves_through_stages():
    got = []
    rep = pipeline.Reporter(on_progress=got.append)
    lo, hi = pipeline.ANALYZE_STAGES["transcript"]
    rep.part(lo, hi)(0.5)
    rep.part(lo, hi)(2)                       # never past the stage's end
    assert got == [(lo + hi) // 2, hi]


def test_time_left_estimate(app_client):
    server, _ = app_client
    assert server.time_left(1000, 2, now=1100) is None          # too early to say
    assert server.time_left(1000, 25, now=1300) == 900          # 5 min for 25% -> 15 min left
    assert server.time_left(1000, 100, now=1300) is None


def test_pictures_of_a_new_recording(tmp_path):
    src = make_video(str(tmp_path / "live.mp4"), 6)
    poster, strip = pipeline.make_pictures(str(tmp_path), src)
    from PIL import Image
    assert Image.open(poster).width == 960
    assert Image.open(strip).width == 8 * 240


def test_upload_shows_a_picture_right_away(app_client, tmp_path):
    server, client = app_client
    v = make_video(str(tmp_path / "a.mp4"), 3)
    pid = client.post("/api/projects/upload", files={"file": ("live.mp4", open(v, "rb"), "video/mp4")}).json()["id"]
    assert client.get(f"/api/projects/{pid}").json()["pictures"]["poster"].startswith("work/poster.jpg")
    r = client.get(f"/api/projects/{pid}/frame", params={"t": 1.5})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


def test_rules_are_used_by_every_future_plan(tmp_path, monkeypatch):
    import editor_rules
    import moments
    monkeypatch.setattr(paths, "PROJECTS", str(tmp_path))
    r = editor_rules.add("Start shorts on the guest's question, not on Elijah's greeting.", "shorts", "Oct 1")
    editor_rules.add("start shorts on the guest's question, not on elijah's greeting.", "shorts")     # duplicate
    editor_rules.add("Never use the word DESTROYED in a title.", "titles")
    assert len(editor_rules.all_rules()) == 2
    assert "guest's question" in moments.system_prompt() and "DESTROYED" not in moments.system_prompt()
    assert "DESTROYED" in editor_rules.prompt_block("titles")
    assert editor_rules.delete(r["id"]) and "guest's question" not in moments.system_prompt()
    with pytest.raises(ValueError):
        editor_rules.add("   ")


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self, exclude_none=False):
        return {k: v for k, v in self.__dict__.items() if not (exclude_none and v is None)}


class _FakeClaude:
    """Plays back scripted answers in place of the real API, and records what it was sent."""
    def __init__(self, script):
        self.script, self.sent = list(script), []
        self.messages = self

    def stream(self, **kw):
        self.sent.append(kw)
        msg = self.script.pop(0)

        class Ctx:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def get_final_message(s): return msg
        return Ctx()


def _msg(stop, *blocks):
    return _Block(stop_reason=stop, content=list(blocks))


def test_chat_editor_changes_a_clip_and_learns_the_reason(app_client, tmp_path, monkeypatch):
    import editor_chat
    import editor_rules
    server, client = app_client
    monkeypatch.setattr(paths, "PROJECTS", str(tmp_path))
    pid, d = _project_with_plan(server, tmp_path)
    fake = _FakeClaude([
        _msg("tool_use", _Block(type="thinking", thinking="find the question", signature="sig"),
             _Block(type="tool_use", id="t1", name="update_item", input={"id": "c100", "changes": {"start": 950}}),
             _Block(type="tool_use", id="t2", name="update_item", input={"id": "c100", "changes": {"start": 130}}),
             _Block(type="tool_use", id="t3", name="remember_rule",
                    input={"rule": "Start conversations when the caller asks their first question.", "applies_to": "long_form"})),
        _msg("end_turn", _Block(type="text", text="Moved the start to 2:10 and I'll remember that.", citations=None)),
    ])
    out = editor_chat.ask(d, "Test live", "Start at his question because that's the hook", server.chat_actions(pid),
                          focus_id="c100", at=131.0, client=fake)
    assert out["changed"] == ["c100"]
    assert pipeline.edits(d)["items"]["c100"]["start"] == 130          # the valid change was saved
    convo = fake.sent[1]["messages"]
    tool_results = next(m["content"] for m in convo if m["role"] == "user" and isinstance(m["content"], list)
                        and m["content"][0].get("type") == "tool_result")
    assert tool_results[0]["is_error"] and "start before it ends" in tool_results[0]["content"]   # 950 > end
    assert [r["text"] for r in editor_rules.all_rules()] == ["Start conversations when the caller asks their first question."]
    assert any(a["kind"] == "rule" for a in out["actions"])
    first = next(m for m in convo if m["role"] == "user")["content"][0]["text"]
    assert "Preview player position: 2:11.0" in first and '"id": "c100"' in first
    assert [m["role"] for m in editor_chat.history(d)] == ["user", "assistant"]
    assert all("citations" not in b for m in convo if m["role"] == "assistant" for b in m["content"])   # no empty fields sent back


def test_chat_needs_a_key_and_a_message(app_client, tmp_path, monkeypatch):
    server, client = app_client
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    pid, _ = _project_with_plan(server, tmp_path)
    assert client.post(f"/api/projects/{pid}/chat", json={"message": ""}).status_code == 400
    r = client.post(f"/api/projects/{pid}/chat", json={"message": "hi"})
    assert r.status_code == 400 and "API key" in r.json()["detail"]


def test_render_bar_can_actually_hide():
    css = open(os.path.join(ROOT, "app", "static", "index.html"), encoding="utf-8").read()
    assert ".renderbar[hidden] { display: none; }" in css      # .renderbar's display:flex was beating [hidden]


# ---------- full preview ----------
def _cut_project(server, tmp_path):
    src = make_video(str(tmp_path / "live.mp4"), 120, rate="30")
    pid = server.new_project("Preview test", source=src)
    d = server.pdir(pid)
    os.makedirs(os.path.join(d, "work"), exist_ok=True)
    segs = [{"start": s, "end": s + 4, "text": "words here", "words": [{"w": "words", "s": s, "e": s + 1.5},
                                                                     {"w": "here", "s": s + 2, "e": s + 3.9}]}
            for s in range(0, 114, 6)]                       # 2.1 s pauses between lines get trimmed
    media.write_json(os.path.join(d, "work", "transcript.json"), {"duration": 120, "segments": segs})
    media.write_json(os.path.join(d, "work", "quiet.json"), [[s + 3.95, s + 5.95] for s in range(0, 114, 6)])
    media.write_json(os.path.join(d, "work", "plan.json"), {"conversations": [
        {"id": "c0", "guest": "Ana", "topic": "t", "start": 0, "end": 119, "cold_open": [[90, 94], [60, 64]]}],
        "shorts": [{"id": "s20", "hook": "h", "pieces": [[20, 54], [100, 109]], "framing": "auto"}]})
    return pid, d, src


def test_full_preview_is_exactly_what_renders(app_client, tmp_path):
    import render_long
    from timeline import Timeline
    server, client = app_client
    pid, d, src = _cut_project(server, tmp_path)
    long_cut = client.get(f"/api/projects/{pid}/items/c0/cut").json()
    item = next(i for i in pipeline.effective_items(d)[0] if i["id"] == "c0")
    tl, rep, _, _ = render_long.plan_timeline(media.probe(src)["fps"], pipeline.corrected_transcript(d), item, {},
                                              pipeline.load(os.path.join(d, "work"), "quiet.json"))
    assert rep.removed                                                             # pauses really were trimmed
    assert abs(long_cut["total"] - tl.seconds) < 1e-3                             # same to the millisecond
    ch = long_cut["chunks"]
    assert [c["kind"] for c in ch[:3]] == ["cold", "cold", "sting"] and ch[-1]["kind"] == "outro"
    assert ch[0]["parts"] == [[90.0, 94.0]] and ch[1]["parts"] == [[60.0, 64.0]]   # cold open order kept
    assert abs(sum(c["seconds"] for c in ch) - long_cut["total"]) < 0.02          # rounding only
    for a, b in zip(ch, ch[1:]):                                                   # back to back, no gaps
        assert abs(a["at"] + a["seconds"] - b["at"]) < 0.02
    assert all(c["seconds"] <= pipeline.PREVIEW_JOINED + 1e-6 for c in ch if c.get("parts") and len(c["parts"]) > 1)
    short_cut = client.get(f"/api/projects/{pid}/items/s20/cut").json()
    joined = [p for c in short_cut["chunks"] for p in c["parts"]]
    assert joined[0][0] == 20 and joined[-1][1] == 109 and len(joined) > 2       # pauses tightened inside
    assert not any(a < 100 and b > 54 for a, b in joined)                        # the gap between pieces stays cut


def test_preview_clip_is_the_length_it_says(app_client, tmp_path):
    server, client = app_client
    pid, d, src = _cut_project(server, tmp_path)
    r = client.get(f"/api/projects/{pid}/cutclip", params={"p": "10-14,20-23.5"})
    assert r.status_code == 200
    f = str(tmp_path / "c.mp4"); open(f, "wb").write(r.content)
    assert abs(media.probe(f)["duration"] - 7.5) < 0.15
    r = client.get(f"/api/projects/{pid}/preview", params={"start": 30, "end": 33.8})
    f2 = str(tmp_path / "p.mp4"); open(f2, "wb").write(r.content)
    assert abs(media.probe(f2)["duration"] - 3.8) < 0.15                        # exactly the clip, no padding
    for bad in ("20-10", "10-14,12-16", "0-300", "x-y"):
        assert client.get(f"/api/projects/{pid}/cutclip", params={"p": bad}).status_code == 400


# ---------- debate structure: setup -> clash -> turn, verified self-defeats ----------
def _talk(lines):
    """A transcript from [(start, "words ...")], one word every 0.4 s."""
    segs = []
    for start, text in lines:
        ws = [{"w": w, "s": round(start + k * 0.4, 2), "e": round(start + k * 0.4 + 0.3, 2)} for k, w in enumerate(text.split())]
        segs.append({"start": start, "end": ws[-1]["e"], "text": text, "words": ws})
    return {"duration": 600, "segments": segs}


TALK = _talk([(100, "Do you believe the Son is God"), (104, "Yes I believe that there's two gods"),
              (110, "Do you believe the scriptures say there's only one God"),
              (116, "Correct there is only one God but there's two"),
              (122, "You just said only one God and two gods"), (130, "Well it's hard to explain")])


def test_cuts_never_split_words():
    from plan_check import validate
    clean, _ = validate({"shorts": [{"hook": "h", "pieces": [[104.5, 106.5]]}]}, 600, TALK)
    a, b = clean["shorts"][0]["pieces"][0]
    assert a == 104.4 and b == 106.7          # 104.5 was inside "I"; 106.5 inside "gods"


def test_self_defeats_need_the_callers_real_words():
    from plan_check import validate
    defeats = [{"claim": "there is only one God", "claim_at": 119, "against": "but there's two", "against_at": 118,
                "kind": "contradiction", "caught_at": 122, "explain": "One God and two gods."},
               {"claim": "Jesus is a prophet only", "claim_at": 125, "against": "he is not God", "against_at": 126}]
    plan = {"conversations": [{"guest": "Black on YT", "start": 90, "end": 200, "cold_open": [[104, 107]],
                               "self_defeats": defeats}],
            "shorts": [{"hook": "One God, then TWO", "category": "Self-Defeat", "self_defeat": 0, "_defeats": defeats,
                        "pieces": [{"start": 110, "end": 125, "beat": "setup", "speaker": "elijah"}]},
                       {"hook": "Made up", "category": "Self-Defeat", "self_defeat": 1, "_defeats": defeats,
                        "pieces": [[100, 120]]}]}
    clean, problems = validate(plan, 600, TALK)
    conv = clean["conversations"][0]
    assert [d["claim"] for d in conv["self_defeats"]] == ["there is only one God"]       # the invented one is gone
    assert abs(conv["self_defeats"][0]["claim_at"] - 116.4) < 0.01                      # time corrected to where "there" is said
    assert any("couldn't be found" in p for p in problems)
    real, fake = clean["shorts"]
    assert real["category"] == "Self-Defeat" and real["self_defeat"]["against"] == "but there's two"
    assert fake["category"] == "Argument Win" and fake["self_defeat"] is None            # never labelled without proof


def test_missing_beats_are_named():
    from plan_check import structure_notes
    sd = {"claim": "there is only one God", "claim_at": 117.2, "against": "but there's two", "against_at": 119.6}
    lonely = {"kind": "short", "category": "Roast", "pieces": [[104, 108]], "beats": [{"beat": "clash", "speaker": "caller"}]}
    notes = structure_notes(lonely)
    assert any(n.startswith("No setup") for n in notes) and any("Only the caller speaks" in n for n in notes)
    assert any(n.startswith("No turn") for n in notes)
    whole = {"kind": "short", "category": "Self-Defeat", "self_defeat": sd, "pieces": [[110, 113], [116, 121], [122, 125]],
             "beats": [{"beat": "setup", "speaker": "elijah"}, {"beat": "clash", "speaker": "caller"},
                       {"beat": "turn", "speaker": "elijah"}]}
    assert structure_notes(whole) == []
    cut_short = dict(whole, pieces=[[110, 113], [116, 118], [122, 125]])
    assert any("isn't fully in the clip" in n for n in structure_notes(cut_short))
    cold = {"kind": "long", "cold_open": [[122, 125], [104, 107]], "self_defeats": [sd],
            "cold_open_beats": [{"beat": "clash", "speaker": "both"}, {"beat": "clash", "speaker": "caller"}]}
    assert len(structure_notes(cold)) == 2                     # no setup first, and the contradiction isn't teased
    assert structure_notes({"kind": "short", "category": "Roast", "pieces": [[1, 2]]}) == []   # old plans: no labels, no nagging


def test_two_pass_planner_builds_the_structure(monkeypatch):
    import claude_api
    import moments
    calls = []

    def fake_ask(system, messages, **kw):
        calls.append(system)
        if system.startswith(moments.CHANNEL) and "of the whole live" in system:
            return json.dumps({"conversations": [
                {"guest": "Black on YT", "start": 95, "end": 200, "topic": "Is the Trinity two gods?", "kind": "debate",
                 "rank": "STRONG", "titles": ["He Said ONE God, Then TWO"]},
                {"guest": "Broken", "start": 300, "end": 420, "rank": "WEAK"}], "housekeeping": []}), {}
        if "Caller: Broken" in messages[0]["content"]:
            raise claude_api.ClaudeError("overloaded")
        return json.dumps({
            "self_defeats": [{"claim": "there is only one God", "claim_at": 117, "against": "but there's two",
                              "against_at": 119, "kind": "contradiction", "caught_at": 122, "explain": "x"}],
            "cold_open": [{"start": 104, "end": 107, "beat": "setup", "speaker": "caller"},
                          {"start": 116, "end": 121, "beat": "turn", "speaker": "caller"}],
            "shorts": [{"category": "Argument Win", "hook": "Two gods?", "strength": 3, "self_defeat": None,
                        "pieces": [{"start": 100, "end": 108, "beat": "setup", "speaker": "both"}]},
                       {"category": "Self-Defeat", "hook": "One God, then TWO", "strength": 3, "self_defeat": 0,
                        "topic": "Is the Trinity two gods?",
                        "pieces": [{"start": 110, "end": 113, "beat": "setup", "speaker": "elijah"},
                                   {"start": 116, "end": 121, "beat": "clash", "speaker": "caller"},
                                   {"start": 122, "end": 125, "beat": "turn", "speaker": "elijah"}]}]}), {}
    monkeypatch.setattr(claude_api, "ask", fake_ask)
    clean, problems = moments.plan(TALK, workers=2)
    assert len(calls) == 3                                     # one map + one per conversation
    assert "SETUP" in calls[1] and "self_defeats" in calls[1]
    conv = next(c for c in clean["conversations"] if c["guest"] == "Black on YT")
    assert [b["beat"] for b in conv["cold_open_beats"]] == ["setup", "turn"]
    assert conv["self_defeats"][0]["against"] == "but there's two"
    assert clean["shorts"][0]["category"] == "Self-Defeat"     # a proven self-defeat ranks first
    assert clean["shorts"][0]["beats"][2] == {"beat": "turn", "speaker": "elijah"}
    assert any("Broken" in p and "couldn't build its clips" in p for p in problems)   # one failure doesn't sink the rest


def test_beat_labels_can_be_fixed_without_making_a_render_stale(app_client, tmp_path):
    server, client = app_client
    pid, d = _project_with_plan(server, tmp_path)
    plan = media.load_json(os.path.join(d, "work", "plan.json"))
    plan["shorts"] = [{"id": "s200", "hook": "h", "pieces": [[200, 210], [220, 230]], "framing": "auto",
                       "category": "Roast", "beats": [{"beat": "clash", "speaker": "caller"}, {"beat": "turn", "speaker": "elijah"}]}]
    media.write_json(os.path.join(d, "work", "plan.json"), plan)
    item = next(i for i in client.get(f"/api/projects/{pid}").json()["items"] if i["id"] == "s200")
    assert any(n.startswith("No setup") for n in item["structure"])
    before = pipeline.render_inputs(d, next(i for i in pipeline.effective_items(d)[1] if i["id"] == "s200"), {})
    fixed = [{"beat": "setup", "speaker": "caller"}, {"beat": "turn", "speaker": "elijah"}]
    r = client.patch(f"/api/projects/{pid}/items/s200", json={"beats": fixed})
    assert r.status_code == 200 and r.json()["beats"] == fixed
    assert not any(n.startswith("No setup") for n in r.json()["structure"])
    after = pipeline.render_inputs(d, next(i for i in pipeline.effective_items(d)[1] if i["id"] == "s200"), {})
    assert before == after                                     # labels never force a re-render
    assert client.patch(f"/api/projects/{pid}/items/s200", json={"beats": fixed[:1]}).status_code == 400


def test_a_quote_just_outside_its_clip_is_pulled_in():
    from plan_check import validate, structure_notes
    defeats = [{"claim": "there is only one God", "claim_at": 117, "against": "but there's two", "against_at": 119}]
    plan = {"shorts": [{"hook": "h", "category": "Self-Defeat", "self_defeat": 0, "_defeats": defeats,
                        "pieces": [{"start": 118.0, "end": 121, "beat": "clash", "speaker": "caller"}]}]}
    s = validate(plan, 600, TALK)[0]["shorts"][0]
    assert s["pieces"][0][0] <= 116.4                         # stretched back to where "there is only one God" starts
    assert not any("isn't fully in the clip" in n for n in structure_notes(dict(s, kind="short")))


def test_long_cold_opens_are_sent_back(monkeypatch):
    import claude_api
    import moments
    answers = [json.dumps({"self_defeats": [], "shorts": [],
                           "cold_open": [{"start": 100, "end": 130}, {"start": 140, "end": 170}]}),
               json.dumps({"self_defeats": [], "shorts": [], "cold_open": [{"start": 104, "end": 107, "beat": "setup"}]})]
    asked = []

    def fake_ask(system, messages, **kw):
        asked.append(messages[-1]["content"])
        return answers.pop(0), {}
    monkeypatch.setattr(claude_api, "ask", fake_ask)
    out = moments._clips_for(TALK, {"guest": "A", "start": 95, "end": 200}, None)
    assert "60 s" in asked[1] and out["cold_open"][0]["start"] == 104


# ---------- refresh & update ----------
def _fake_release(files):
    """A ZIP shaped like GitHub's download: everything inside one top folder."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr("Clip-Studio-main/" + name, text)
    return buf.getvalue()


def _old_copy(root):
    files = {"app/server.py": "old server", "engine/old_module.py": "gone in the new version",
             "engine/version.txt": "aaa 2026-10-01T00:00:00Z", "requirements.txt": "fastapi\n",
             "Open Clip Studio (Mac).command": "launcher", "projects/oct1/project.json": "my live",
             "projects/settings.json": '{"anthropic_api_key": "kept"}', "models/m.onnx": "model",
             ".venv/bin/python": "python", "install-log.txt": "log", "assets/fonts/Downloaded.ttf": "font"}
    for name, text in files.items():
        os.makedirs(os.path.dirname(os.path.join(root, name)), exist_ok=True)
        open(os.path.join(root, name), "w").write(text)


def test_update_keeps_lives_models_and_settings(tmp_path):
    import updater
    _old_copy(str(tmp_path))
    release = _fake_release({"app/server.py": "new server", "engine/version.txt": "bbb 2026-11-01T00:00:00Z",
                             "requirements.txt": "fastapi\n", "Open Clip Studio (Mac).command": "launcher",
                             "projects/settings.json": "{}",            # must never overwrite the real one
                             "engine/new_module.py": "new"})
    pip_runs = []
    out = updater.apply(str(tmp_path), download=lambda: release, pip=lambda: pip_runs.append(1))
    read = lambda n: open(tmp_path / n).read()
    assert read("app/server.py") == "new server" and read("engine/new_module.py") == "new"
    assert not (tmp_path / "engine/old_module.py").exists()               # deleted upstream, deleted here
    assert read("projects/oct1/project.json") == "my live"
    assert "kept" in read("projects/settings.json")
    assert read("models/m.onnx") == "model" and read(".venv/bin/python") == "python"
    assert read("install-log.txt") == "log" and read("assets/fonts/Downloaded.ttf") == "font"
    assert "Open Clip Studio (Mac).command" not in out["changed"]          # identical launcher left alone
    assert pip_runs == []                                                  # same requirements: no reinstall
    assert out["from"]["sha"] == "aaa" and out["to"]["sha"] == "bbb"


def test_update_reinstalls_when_requirements_change(tmp_path):
    import updater
    _old_copy(str(tmp_path))
    release = _fake_release({"app/server.py": "new", "requirements.txt": "fastapi\nnewthing\n"})
    pip_runs = []
    updater.apply(str(tmp_path), download=lambda: release, pip=lambda: pip_runs.append(1))
    assert pip_runs == [1]


def test_update_refuses_a_bad_download(tmp_path):
    import io
    import zipfile
    import updater
    _old_copy(str(tmp_path))
    with pytest.raises(updater.UpdateError):                               # not a Clip Studio folder
        updater.apply(str(tmp_path), download=lambda: _fake_release({"README.md": "x"}))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../escape.py", "x")
        z.writestr("app/server.py", "x")
    with pytest.raises(updater.UpdateError):                               # path outside the folder
        updater.apply(str(tmp_path), download=lambda: buf.getvalue())
    assert open(tmp_path / "app/server.py").read() == "old server"         # nothing changed either time
    assert not (tmp_path.parent / "escape.py").exists()


def test_version_comes_from_the_download_stamp(tmp_path):
    import updater
    os.makedirs(tmp_path / "engine")
    (tmp_path / "engine/version.txt").write_text("$Format:%H %cI$\n")      # unstamped: unknown, so offer update
    assert updater.local_version(str(tmp_path))["sha"] is None
    (tmp_path / "engine/version.txt").write_text("abc123 2026-11-01T10:00:00Z\n")
    assert updater.local_version(str(tmp_path)) == {"sha": "abc123", "date": "2026-11-01T10:00:00Z", "git": False}


def test_update_waits_for_running_jobs(app_client, tmp_path, monkeypatch):
    server, client = app_client
    import updater
    pid, _ = _project_with_plan(server, tmp_path)
    server.queue.add(pid, "analyze")
    monkeypatch.setattr(updater, "apply", lambda *a, **k: pytest.fail("must not update mid-job"))
    r = client.post("/api/update")
    assert r.status_code == 409 and "finished" in r.json()["detail"]


def test_update_check_without_internet_says_so(app_client, monkeypatch):
    server, client = app_client
    import updater

    def offline(url):
        raise updater.UpdateError("Couldn't reach GitHub to check for updates (offline).")
    monkeypatch.setattr(updater, "_get", offline)
    r = client.get("/api/update")
    assert r.status_code == 503 and "Couldn't reach GitHub" in r.json()["detail"]
