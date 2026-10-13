"""Titles, description, chapters, tags and the thumbnail brief for one finished long-form video.

The transcript is sent with timestamps ON THE FINAL CUT (cold open + sting included), so the
chapters Claude writes line up with the uploaded video, not the raw live.

Needs ANTHROPIC_API_KEY.
"""
import json
from timeline import chapter_stamp

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


def final_timeline_transcript(transcript, timeline):
    """Transcript lines stamped with their time on the finished video (from the saved timeline)."""
    lines = []
    for s in transcript["segments"]:
        t = timeline.to_output(s["start"], kinds=("body",))
        if t is not None:
            lines.append(f"[{chapter_stamp(t)}] {s['text']}")
    return "\n".join(lines)


def _secs(stamp):
    parts = [int(p) for p in str(stamp).split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def clean_chapters(chapters, timeline):
    """YouTube's rules: first chapter at 0:00, increasing, at least 10 s apart, inside the video."""
    out, last = [], -10
    body0 = timeline.body_start()
    for c in chapters or []:
        try:
            t, label = _secs(c[0]), str(c[1])[:60]
        except (ValueError, TypeError, IndexError):
            continue
        if t >= timeline.seconds - 5 or t - last < 10:
            continue
        out.append([chapter_stamp(t), label]); last = t
    if not out or out[0][0] not in ("00:00", "0:00:00"):
        out.insert(0, ["00:00", "Cold open"])
    if body0 and all(abs(_secs(c[0]) - body0) > 10 for c in out):
        out.insert(1, [chapter_stamp(body0), "The conversation"])
    return out


def write(transcript, item, timeline, model=None):
    from claude_api import ask, extract_json
    text = final_timeline_transcript(transcript, timeline)
    import editor_rules
    out, _ = ask(STYLE + editor_rules.prompt_block("titles"), [{"role": "user", "content": f"Guest: {item.get('guest', 'caller')}\n"
                          f"The video is {chapter_stamp(timeline.seconds)} long; the cold open runs until "
                          f"{chapter_stamp(timeline.body_start())}.\n\n{text}"}], max_tokens=16000, model=model)
    data = extract_json(out)
    data["chapters"] = clean_chapters(data.get("chapters"), timeline)
    data["titles"] = [str(t)[:100] for t in data.get("titles", [])][:3]
    return data
