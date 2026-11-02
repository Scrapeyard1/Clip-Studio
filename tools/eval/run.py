"""python tools/eval/run.py <out_dir> [lives...]: plan each live with the current engine and save it for score.py.
Needs tools/eval/transcripts/transcript-<live>.json (see README) and a Claude key in projects/settings.json
or ANTHROPIC_API_KEY. About $0.30-0.60 per live."""
import json, os, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
out, lives = sys.argv[1], sys.argv[2:] or ["10-11", "10-13", "10-15", "10-31"]
if not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = json.load(open(os.path.join(ROOT, "projects", "settings.json")))["anthropic_api_key"]
os.environ["CLIPSTUDIO_PROJECTS"] = tempfile.mkdtemp()          # no saved editor rules: score the planner itself
sys.path.insert(0, os.path.join(ROOT, "engine"))
import claude_api, moments
usage = {"in": 0, "out": 0}
_ask = claude_api.ask
def ask(*a, **k):
    text, u = _ask(*a, **k)
    usage["in"] += u.get("input", 0); usage["out"] += u.get("output", 0)
    return text, u
claude_api.ask = ask
os.makedirs(out, exist_ok=True)
for live in lives:
    t0 = time.time()
    tr = json.load(open(os.path.join(HERE, "transcripts", f"transcript-{live}.json")))
    clean, problems = moments.plan(tr)
    json.dump({"plan": clean, "problems": problems, "usage": dict(usage)}, open(os.path.join(out, f"{live}.json"), "w"), indent=1)
    print(live, "planned in", int(time.time() - t0), "s")
