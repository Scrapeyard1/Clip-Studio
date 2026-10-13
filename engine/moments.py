"""The 'brain': read a live's transcript and plan every output.

Sends the timestamped transcript to Claude with the channel's editing recipe and
gets back a JSON plan: the guest conversations, a cold open for each, safe-cut
protections, stream housekeeping to flag, and ranked short candidates.

Needs ANTHROPIC_API_KEY. Cost: a 3-hour live is ~40k input tokens, about $0.10-0.50.
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
    out = []
    for s in transcript["segments"]:
        out.append(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}")
    return "\n".join(out)


def _ask(client, model, messages):
    msg = client.messages.create(model=model, max_tokens=16000, system=RECIPE, messages=messages)
    return "".join(b.text for b in msg.content if b.type == "text")


def plan(transcript, model="claude-sonnet-5-5"):
    """Ask Claude for a plan, check it, and ask once more with the problems if it can't be used."""
    import anthropic
    from plan_check import PlanError, validate
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": condensed(transcript)}]
    text = _ask(client, model, messages)
    for attempt in (1, 2):
        try:
            raw = json.loads(text[text.index("{"): text.rindex("}") + 1])
            clean, problems = validate(raw, transcript["duration"])
            return clean, problems
        except (ValueError, PlanError) as e:
            if attempt == 2:
                raise PlanError(f"Claude's plan couldn't be used twice in a row ({e}). Press Retry to ask again.")
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"That plan can't be used: {e} Return the corrected JSON only."}]
            text = _ask(client, model, messages)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript"); ap.add_argument("out")
    a = ap.parse_args()
    clean, problems = plan(json.load(open(a.transcript)))
    json.dump(clean, open(a.out, "w"), indent=1)
    print("\n".join(problems))
