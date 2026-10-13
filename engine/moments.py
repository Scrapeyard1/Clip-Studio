"""The 'brain': read a live's transcript and plan every output.

Sends the timestamped transcript to Claude with the channel's editing recipe and
gets back a JSON plan: the guest conversations, a cold open for each, safe-cut
protections, stream housekeeping to flag, and ranked short candidates.

Needs ANTHROPIC_API_KEY. A 2h40m live is ~50k input tokens: about $0.20-0.50 on Sonnet 5.5.
"""
import argparse, json, os

RECIPE = """You plan edits for the YouTube channel "Elijah Johnson Apologetics" (Christian apologetics
lives: callers come up one at a time to challenge or ask Elijah about the faith).

From the timestamped transcript, return ONLY a JSON object with these keys:

"conversations": one per guest, in order. Each:
  "guest": how Elijah addresses them (e.g. "Black on YT"), "start", "end" (seconds; start at the first
  hello to the guest, end after Elijah's reaction once they leave, before the next guest is called),
  "topic", "rank": "STRONG" | "MEDIUM" | "WEAK", "summary" (2 sentences),
  "cold_open": 2-4 [start, end] clips from LATER in the conversation, total 20-30 s: the guest's core
     claim first, then the most heated or funniest exchange. Each clip starts and ends on a sentence
     boundary. Never give away a twist the title teases.
  "titles": 3 options in the channel's style: who the guest is + the conflict + one or two CAPITALIZED
     words (e.g. "Jehovah's Witness Runs A NEW ARGUMENT", "PLEASE Do Not Be Like This Brother HERE").
  "protect": [start, end] ranges whose silence must not be cut (a stunned pause after a hard question).
"housekeeping": [{"start","end","what"}] stream business inside conversations to flag for removal:
  raid setup, mod instructions, super chat / donation reading, tech trouble.
"shorts": up to 8, best first. Each: "category": Roast | Argument Win | Explainer | Testimony | Pastoral,
  "pieces": 1-3 [start, end] ranges played in order (30-90 s total), "hook": <= 8 words shown on screen,
  "why": one line. RULE for Roast: the guest's actual claim must play before Elijah's response.

Use only times that appear in the transcript. Seconds as numbers."""


def condensed(transcript):
    """One line per transcript segment, stamped with its start (each line ends where the next begins)."""
    out = [f"[{s['start']:.1f}] {s['text']}" for s in transcript["segments"]]
    out.append(f"[{transcript['duration']:.1f}] (end of recording)")
    return "\n".join(out)


def plan(transcript, model=None):
    """Ask Claude for a plan, check it, and ask once more with the problems if it can't be used."""
    from claude_api import ask, extract_json
    from plan_check import PlanError, validate
    messages = [{"role": "user", "content": condensed(transcript)}]
    text, _ = ask(RECIPE, messages, model=model)
    for attempt in (1, 2):
        try:
            raw = extract_json(text)
            clean, problems = validate(raw, transcript["duration"])
            return clean, problems
        except (ValueError, PlanError) as e:
            if attempt == 2:
                raise PlanError(f"Claude's plan couldn't be used twice in a row ({e}). Press Retry to ask again.")
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"That plan can't be used: {e} Return the corrected JSON only."}]
            text, _ = ask(RECIPE, messages, model=model)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript"); ap.add_argument("out")
    a = ap.parse_args()
    clean, problems = plan(json.load(open(a.transcript)))
    json.dump(clean, open(a.out, "w"), indent=1)
    print("\n".join(problems))
