"""The 'brain': read a live's transcript and plan every output, in two passes.

Pass 1 (the whole live): map the guest conversations: who, where each starts and ends, the topic,
how strong it is, titles, silences to protect, stream housekeeping.

Pass 2 (each conversation on its own, in parallel): find where the caller's argument defeats itself
(quoted word for word, with times), then build the cold open (one ~40 s stretch that ends on an
unanswered question, like Elijah's own previews) and the shorts in three beats:
  SETUP  the line that tells a stranger what's being debated
  CLASH  both sides in their own voices
  TURN   the caller's argument undercutting itself and Elijah catching it (or the decisive answer)
Each clip is labelled with its beat and who's speaking. plan_check then verifies the quotes against
the transcript and flags any short that's missing a beat.

Needs ANTHROPIC_API_KEY. A 2h40m live is ~60k input tokens in total: about $0.30-0.60 on Sonnet 5.5.
"""
import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor

CHANNEL = """You plan edits for the YouTube channel "Elijah Johnson Apologetics": Christian apologetics
lives where callers come up one at a time to challenge or question Elijah about the faith. The
transcript has no speaker labels: work out who is talking from what is said (Elijah is the host who
answers, reads Scripture and asks the follow-up questions; the caller raises the objection)."""

MAP_RECIPE = CHANNEL + """

From the timestamped transcript of the whole live, return ONLY a JSON object with these keys:

"conversations": one per guest conversation, in order, never overlapping. Each:
  "guest": how Elijah addresses them (e.g. "Black on YT"), "start", "end" (seconds).
     START at the first hello to THIS guest (not at an earlier caller he goes on to mention).
     END after Elijah's closing words once the guest leaves: he often answers the guest's last point or
     sums up to the audience for a few minutes. Include all of that; end only when he moves on (calls
     the next guest, reads chat or super chats, sorts out the stream). Never end at the goodbye.
     When a second guest joins the same discussion on stage, or the same guest drops and reconnects,
     it is ONE conversation that covers all of it. A new caller who reacts to the previous call is
     still a new conversation: it starts at his hello.
  "topic": the question being debated, as one plain sentence,
  "kind": "debate" (the caller argues against Elijah's position) | "question" (a sincere question) |
     "friendly" (chat, thanks, shout-outs),
  "video": true if this should be its own long-form video. Elijah's choices: yes for a real debate where
     the guest argues from outside the faith (Muslim, atheist, Hebrew Israelite, oneness, Quran-only,
     skeptic) or presses a challenge, with sustained back-and-forth, usually 10+ minutes; no for sincere
     questions and in-house Christian discussions (Catholic/Protestant questions, advice, pastoral),
     friendly chats, calls under about 8 minutes, and calls that never get going (bad audio, guest leaves
     early, mostly off topic).
  "video_reason": one short line on why or why not,
  "rank": "STRONG" | "MEDIUM" | "WEAK", "summary" (2 sentences),
  "titles": 3 options in the channel's style: who the guest is + the conflict + one or two CAPITALIZED
     words (e.g. "Jehovah's Witness Runs A NEW ARGUMENT", "PLEASE Do Not Be Like This Brother HERE").
     Never say a caller admitted, conceded or was destroyed unless his own words in the call show it.
  "protect": [start, end] ranges whose silence must not be cut (a stunned pause after a hard question).
"housekeeping": [{"start","end","what"}] stream business inside conversations to flag for removal:
  raid setup, mod instructions, super chat / donation reading, tech trouble.

Use only times that appear in the transcript. Seconds as numbers."""

CLIP_RECIPE = CHANNEL + """

You get the transcript of ONE conversation. Return ONLY a JSON object with "self_defeats",
"cold_open", "cold_open_ends_on", "cold_open_why" and "shorts".

THE STRUCTURE. Every short is built in three beats, played in this order (the cold open has its
own rules below):
  1. SETUP: the line that tells a stranger what is being debated, usually the caller's question or
     claim, or Elijah's question that frames it ("Do you believe the Son is God?"). 2-10 s. Without it
     nobody knows what the fight is about. It may come from earlier in the call than the rest.
  2. CLASH: both sides in their own voices: the caller's position, then Elijah's challenge, or the
     back-and-forth. Never only one person talking.
  3. TURN: the moment the caller's argument undercuts itself, then Elijah catching it. If there is no
     self-defeat, the turn is the decisive answer or the payoff.
For Explainer, Testimony and Pastoral shorts the clash may be skipped: setup = the question that
prompted it, turn = the payoff.

"self_defeats": every place where the CALLER's argument defeats itself:
  - he contradicts something he said ("there is only one God" ... "there's two gods"),
  - he admits the point he is arguing against,
  - he relies on a source or standard he rejects, or holds Elijah to a standard he won't meet himself,
  - his objection, if true, would undercut his own position.
  Each: {"claim": his exact words, "claim_at": seconds, "against": his exact words that undercut it,
  "against_at": seconds, "kind": "contradiction" | "admission" | "double standard" | "undercuts itself",
  "caught_at": seconds where Elijah points it out (or null), "explain": one plain sentence}.
  Copy the words exactly as they appear in the transcript. Being confused, silent, rude or losing is NOT
  a self-defeat. Return [] if there is none; never invent one.

"cold_open": the preview that plays before the title sequence, built the way Elijah builds his:
  - ONE continuous stretch of the real conversation, 30-45 s (aim for about 40; never over 45). Never stitch short
    fragments together. Use two clips only when that stretch doesn't make the topic clear on its own:
    first the caller's opening claim or question (8-20 s), then the stretch.
  - On its own it tells a stranger what is being debated: the claim, question or challenge is heard in it.
  - It has real tension: a direct challenge, a pointed question, a dilemma, a heated back-and-forth, a
    caller about to leave.
  - It ENDS on an unanswered question or challenge, or just as the answer begins, before anything is
    resolved, so viewers stay to hear the answer. Never end on the resolution or Elijah's full explanation.
    Its "end" is AFTER the last word of that question: the transcript only shows when each line starts,
    so if the question is in a line, end at the start of the next line.
  - It can come from anywhere in the call: most often the second half, sometimes the opening exchange
    when that states the debate best.
  - Never misrepresent a position or imply an admission that isn't in the words.
  Each clip: {"start", "end", "beat": "setup"|"clash"|"turn", "speaker": "caller"|"elijah"|"both"}.
  Two of Elijah's real previews, as transcribed:
    "...Then I will dwell in your midst, and you will know that Yahweh of Hosts has sent me to you. So you
    have Yahweh being sent by Yahweh of hosts. Very clear. Yahweh is sent by Yahweh of hosts. So who are
    these two persons? Who sent who?" (44 s; ends before the caller answers)
    "...then by the Quran's own standard, I'm not supposed to support that prophet that came with that
    message. / Okay. Can you give me one example? / Yeah." (39 s; ends as the challenge is taken up)
"cold_open_ends_on": the exact words of the question or challenge the preview ends on.
"cold_open_why": one line on why this preview makes people keep watching.

"shorts": 0-3 of the best moments (only real ones; quality over count), each 20-90 s:
  {"category": "Self-Defeat" | "Roast" | "Argument Win" | "Explainer" | "Testimony" | "Pastoral",
   "hook": on-screen text, at most 8 words, that states the debate without lying about it,
   "topic": the question being debated, one plain sentence,
   "why": one line on why it works,
   "strength": 1-5,
   "pieces": 1-5 clips played in order, each {"start", "end", "beat", "speaker"} as above,
   "self_defeat": the index in self_defeats this short shows, or null}.
  A Self-Defeat short must play BOTH the claim and the words that undercut it.

Rules for every clip: start at the beginning of a sentence and end at the end of one; use only times
that appear in the transcript; seconds as numbers."""


def map_prompt():
    import editor_rules
    return MAP_RECIPE + editor_rules.prompt_block("titles")


def clip_prompt():
    """The structure plus every editing rule Elijah has taught the chat editor."""
    import editor_rules
    return CLIP_RECIPE + editor_rules.prompt_block("planning")


def system_prompt():                      # kept for anything that shows "what Claude is told"
    return clip_prompt()


def condensed(transcript, start=None, end=None):
    """One line per transcript segment, stamped with its start (each line ends where the next begins)."""
    segs = [s for s in transcript["segments"]
            if (start is None or s["end"] > start) and (end is None or s["start"] < end)]
    out = [f"[{s['start']:.1f}] {s['text']}" for s in segs]
    last = end if end is not None else transcript["duration"]
    out.append(f"[{last:.1f}] (end)")
    return "\n".join(out)


def _ask_json(system, user, model, on_chars=None, check=None):
    """Ask, parse the JSON, and ask once more with the problem if it can't be used."""
    from claude_api import ask, extract_json
    messages = [{"role": "user", "content": user}]
    text, _ = ask(system, messages, model=model, on_chars=on_chars)
    for attempt in (1, 2):
        try:
            data = extract_json(text)
            if check:
                check(data)
            return data
        except ValueError as e:
            if attempt == 2:
                raise
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"That can't be used: {e} Return the corrected JSON only."}]
            text, _ = ask(system, messages, model=model)


def _clips_for(transcript, conv, model, on_chars=None):
    lead = 20                                                  # a little context before the hello
    user = (f"Caller: {conv.get('guest')}\nTopic: {conv.get('topic')}\n"
            f"Conversation runs {conv['start']:.1f} to {conv['end']:.1f} s.\n\n"
            + condensed(transcript, max(0, conv["start"] - lead), conv["end"]))

    def check(d):
        if not isinstance(d, dict):
            raise ValueError("expected a JSON object with self_defeats, cold_open and shorts")
        if isinstance(d.get("cold_open"), dict):            # one clip sent on its own, not in a list
            d["cold_open"] = [d["cold_open"]]
        cold = [x for x in (d.get("cold_open") or []) if isinstance(x, dict)]
        try:
            spans = [(float(x["start"]), float(x["end"])) for x in cold]
        except (KeyError, TypeError, ValueError):
            spans = None
        if conv.get("video") is False:                       # not a video pick: a missing preview is fine
            if not spans or len(spans) > 2 or not 15 <= sum(b - a for a, b in spans) <= 60:
                d["cold_open"] = []
            return
        if not cold:
            raise ValueError("cold_open needs at least one clip: a list of {start, end, beat, speaker}")
        if spans is None:
            raise ValueError("every cold_open clip needs a numeric start and end")
        total = sum(b - a for a, b in spans)
        if len(spans) > 2:
            raise ValueError(f"the cold open has {len(spans)} clips; use one continuous stretch (two at most)")
        if not 15 <= total <= 60:                        # a little off is flagged in review, not refused
            raise ValueError(f"the cold open is {total:.0f} s; make it one stretch of 30-45 s")
        outside = [a for a, b in spans if a < conv["start"] or b > conv["end"] + 1]
        if outside:
            raise ValueError(f"cold_open clips must come from inside this conversation "
                             f"({conv['start']:.1f}-{conv['end']:.1f} s)")
    return _ask_json(clip_prompt(), user, model, on_chars, check)


def plan(transcript, model=None, on_chars=None, workers=4):
    """Pass 1 maps the conversations; pass 2 builds each one's self-defeats, cold open and shorts.
    Returns (clean plan, problems) after plan_check has verified everything against the transcript."""
    from plan_check import PlanError, validate
    progress = {"map": 0, "clips": {}}
    lock = threading.Lock()

    def report():
        if on_chars:
            on_chars(progress["map"] + sum(progress["clips"].values()))

    def map_chars(n):
        progress["map"] = n
        report()

    def check_map(d):
        if not isinstance(d, dict) or not isinstance(d.get("conversations"), list) or not d["conversations"]:
            raise ValueError("expected a JSON object with a non-empty conversations list")
    try:
        mapped = _ask_json(map_prompt(), condensed(transcript), model, map_chars, check_map)
    except ValueError as e:
        raise PlanError(f"Claude's map of the live couldn't be used ({e}). Press Retry to ask again.")

    usable = []
    for c in mapped["conversations"]:
        try:
            c["start"], c["end"] = float(c["start"]), float(c["end"])
            if c["end"] - c["start"] >= 60:
                usable.append(c)
        except (KeyError, TypeError, ValueError):
            continue

    def one(i_c):
        i, c = i_c

        def chars(n):
            with lock:
                progress["clips"][i] = n
            report()
        try:
            return i, _clips_for(transcript, c, model, chars), None
        except Exception as e:                       # one bad conversation never sinks the whole plan
            return i, None, str(e)

    failures = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for i, data, err in pool.map(one, list(enumerate(usable))):
            c = usable[i]
            if err or not data:
                failures.append(f"{c.get('guest', 'A conversation')}: couldn't build its clips ({err}). "
                                "Pick the cold open by hand or press Retry.")
                c["cold_open"], c["self_defeats"], c["shorts"] = [], [], []
                continue
            c["cold_open"] = data.get("cold_open") or []
            c["cold_open_ends_on"] = data.get("cold_open_ends_on")
            c["cold_open_why"] = data.get("cold_open_why")
            c["self_defeats"] = data.get("self_defeats") or []
            c["shorts"] = []
            for s in data.get("shorts") or []:
                if isinstance(s, dict):
                    s["_defeats"] = c["self_defeats"]        # plan_check resolves the one it shows
                    c["shorts"].append(s)

    rank_w = {"STRONG": 3, "MEDIUM": 2, "WEAK": 1}
    shorts = []
    for c in usable:
        for s in c.pop("shorts", []):
            try:
                strength = float(s.get("strength") or 0)
            except (TypeError, ValueError):
                strength = 0
            s["_order"] = -(strength + rank_w.get(c.get("rank"), 0) + (1.5 if s.get("self_defeat") is not None else 0))
            shorts.append(s)
    shorts.sort(key=lambda s: s["_order"])
    raw = {"conversations": usable, "shorts": shorts[:12], "housekeeping": mapped.get("housekeeping") or []}
    clean, problems = validate(raw, transcript["duration"], transcript)
    return clean, failures + problems


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript"); ap.add_argument("out")
    a = ap.parse_args()
    clean, problems = plan(json.load(open(a.transcript)))
    json.dump(clean, open(a.out, "w"), indent=1)
    print("\n".join(problems))
