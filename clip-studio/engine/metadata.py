"""Titles, description, chapters, tags and the thumbnail brief for one finished long-form video.

The transcript is sent with timestamps ON THE FINAL CUT (cold open + sting included), so the
chapters Claude writes line up with the uploaded video, not the raw live.

Needs ANTHROPIC_API_KEY.
"""
import json
from cuts import remap

STYLE = """You write YouTube packaging for "Elijah Johnson Apologetics" (Christian apologetics; callers
challenge Elijah live: Muslims, Jehovah's Witnesses, atheists, Hebrew Israelites, Catholics, Oneness, etc.).

What has worked on this channel (titles):
  "PLEASE Do Not Be Like This Brother HERE", "Jehovah's Witness Runs A NEW ARGUMENT",
  "I Showed This Muslim His Quran Was Wrong", "Atheist Asked Me "Why GOD Allows Suffering"",
  "He Questioned Jesus' Resurrection"
  Formula: who the guest is + the conflict + one or two CAPITALIZED words. 40-65 characters.
  Curiosity without lying: never claim a conversion, concession or "destroyed" that didn't happen.

What has worked (thumbnail text): COOKED, EXPOSED, EYES OPENED, THIS WAS BAD, PROVE YOUR CLAIM,
  HUMBLED, IS TRINITY LOGICAL?, I'M SO CONFUSED, WAS JESUS CREATED. 1-3 words, never repeats the title.

Return ONLY JSON:
{"titles": [3 options, best first],
 "thumbnail": [3 options: {"preset": "lightning"|"arrow"|"banner"|"bubble"|"solo",
                           "lines": [[["WORD", "white"|"yellow"|"red"], ...], ...],
                           "host_expression": "smirk"|"pointing"|"confused"|"shocked"|"side-eye"|"laughing"|"praying",
                           "guest_image": short description of who should stand opposite Elijah (or null if solo),
                           "background": short description of a themed background}],
 "description": 2 short paragraphs (hook, then what's covered and the key verses), then a blank line and
                "Support the channel: https://www.patreon.com/..." placeholder line,
 "chapters": [["MM:SS", "label"], ...] starting at "00:00", 5-10 entries, labels <= 40 chars,
 "tags": 10-15 search tags,
 "pinned_comment": one question that invites debate in the comments}"""


def final_timeline_transcript(transcript, cold_open, sting_len, keep):
    """Transcript lines stamped with times on the finished video."""
    lines, off = [], 0.0
    for a, b in cold_open:
        off += b - a
    body0 = off + sting_len
    for s in transcript["segments"]:
        t = remap(s["start"], keep)
        if t is None:
            continue
        T = body0 + t
        lines.append(f"[{int(T // 60):02d}:{int(T % 60):02d}] {s['text']}")
    return "\n".join(lines)


def write(transcript, plan, keep, sting_len=3.0, model="claude-sonnet-5-5"):
    import anthropic
    text = final_timeline_transcript(transcript, plan["cold_open"], sting_len, keep)
    msg = anthropic.Anthropic().messages.create(
        model=model, max_tokens=4000, system=STYLE,
        messages=[{"role": "user", "content": f"Guest: {plan.get('guest', 'caller')}\n\n{text}"}])
    out = "".join(b.text for b in msg.content if b.type == "text")
    return json.loads(out[out.index("{"): out.rindex("}") + 1])
