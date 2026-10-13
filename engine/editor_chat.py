"""The chat editor: Elijah says what to change in plain language ("start this short at the question,
because the hook is his question") and Claude makes the change and, when the reason would help on
future lives, saves it as a rule (editor_rules) that every later plan follows.

The app supplies the actions (so every change goes through the same checks as a manual edit):
  items()                    -> list of the project's conversations and shorts
  transcript(start, end)     -> [{"start","end","text"}] corrected lines
  update(item_id, changes)   -> updated item, or raises ValueError with a plain-language reason
"""
import json
import os
import time

import editor_rules
import media
from claude_api import converse

SYSTEM = """You are the editor inside Clip Studio, working with Elijah Johnson (Elijah Johnson Apologetics,
Christian apologetics lives where callers come up one at a time) on one recorded live. He tells you, in
plain language, what to change about the proposed conversations (long-form videos) and shorts.

How to work:
- Tool times are seconds in the ORIGINAL recording. When you write to Elijah, use m:ss or h:mm:ss.
- Before moving a start or end, read the transcript around that point. Cuts go between lines, never
  inside a sentence: a start goes at the start of a line, an end at the end of a line. If his exact time
  would cut a word, nudge it to the nearest line boundary and say so.
- "Here", "this point" or "now" means the preview player position in the context, if one is given.
- Conversations have "start", "end" and "cold_open" (list of [start, end] clips from later in the call,
  about 30 s total). Shorts have "pieces" (list of [start, end] played in order) and "hook" (on-screen
  text, at most 8 words), "framing" (auto, host, full, split, guest).
- Change only what he asked about, with update_item. If it returns an error, explain it simply.
- When he gives a reason that would apply to future clips ("because the guest's question is the hook",
  "I never want the like-the-live ask in a short"), also call remember_rule with a short, general rule:
  no timestamps, no names from this live. Tell him it'll be used on future lives and can be removed in
  Settings. Don't save one-off preferences that only make sense for this clip.
- If he asks you to forget a rule, use forget_rule.
- Changing something already rendered marks it "changed since render": remind him to render it again.
- Reply in 1-3 short sentences, plain words, no headings or bullet lists."""

TOOLS = [
    {"name": "list_items", "description": "All proposed conversations and shorts in this live, with their current times.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_transcript", "description": "Transcript lines between two source times (at most 10 minutes).",
     "input_schema": {"type": "object", "properties": {"start": {"type": "number"}, "end": {"type": "number"}},
                      "required": ["start", "end"]}},
    {"name": "search_transcript", "description": "Find lines containing some words (case-insensitive). Up to 15 matches.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "update_item", "description": "Change a conversation or short. Allowed keys: conversations: start, end, "
     "cold_open, guest, topic, protect. Shorts: pieces, hook, framing, category, guest. Times in source seconds.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}, "changes": {"type": "object"}},
                      "required": ["id", "changes"]}},
    {"name": "remember_rule", "description": "Save a general editing rule for all future lives.",
     "input_schema": {"type": "object", "properties": {
         "rule": {"type": "string", "description": "One general sentence, no timestamps or names from this live."},
         "applies_to": {"type": "string", "enum": ["shorts", "long_form", "both", "titles"]}},
         "required": ["rule", "applies_to"]}},
    {"name": "forget_rule", "description": "Delete a saved rule by its id.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
]

CHAT_FILE = "chat.json"
HISTORY_TURNS = 16


def stamp(t):
    t = max(0.0, float(t))
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}"


def summary(item):
    if item["kind"] == "long":
        return {"id": item["id"], "kind": "conversation", "guest": item.get("guest"), "topic": item.get("topic"),
                "start": item["start"], "end": item["end"], "cold_open": item.get("cold_open", []),
                "render": item.get("render")}
    return {"id": item["id"], "kind": "short", "hook": item.get("hook"), "pieces": item.get("pieces", []),
            "framing": item.get("framing"), "category": item.get("category"), "render": item.get("render")}


def history(d):
    return media.load_json(os.path.join(d, CHAT_FILE), default={"messages": []}).get("messages", [])


def clear(d):
    media.write_json(os.path.join(d, CHAT_FILE), {"messages": []})


def _label(name, args, out, is_err, items_by_id):
    """What Elijah sees under the reply for each action taken."""
    if is_err:
        return None
    if name == "update_item":
        it = items_by_id.get(args.get("id"), {})
        who = it.get("guest") or it.get("hook") or args.get("id")
        parts = []
        for k, v in (args.get("changes") or {}).items():
            if k in ("start", "end"):
                parts.append(f"{k} to {stamp(v)}")
            elif k in ("pieces", "cold_open"):
                parts.append(f"{'clips' if k == 'pieces' else 'cold open'} to " +
                             ", ".join(f"{stamp(a)}-{stamp(b)}" for a, b in v))
            else:
                parts.append(f"{k} to \"{v}\"")
        return {"kind": "change", "id": args.get("id"), "text": f"Changed {who}: " + "; ".join(parts)}
    if name == "remember_rule":
        return {"kind": "rule", "text": f"Remembered for future lives: {args.get('rule')}"}
    if name == "forget_rule":
        return {"kind": "rule", "text": "Forgot a saved rule."}
    return None


def ask(d, title, message, actions, focus_id=None, at=None, client=None):
    """One chat turn. Returns the saved assistant message {role, text, actions, changed}."""
    items = actions["items"]()
    by_id = {i["id"]: i for i in items}
    rules = editor_rules.all_rules()

    def run_tool(name, args):
        if name == "list_items":
            return json.dumps([summary(i) for i in actions["items"]()]), False
        if name == "read_transcript":
            a, b = float(args["start"]), float(args["end"])
            b = min(b, a + 600)
            lines = actions["transcript"](a, b)
            return ("\n".join(f"[{l['start']:.1f}-{l['end']:.1f}] {l['text']}" for l in lines)
                    or "(no speech there)"), False
        if name == "search_transcript":
            q = str(args.get("text", "")).lower().strip()
            hits = [l for l in actions["transcript"](0, 10 ** 9) if q and q in l["text"].lower()][:15]
            return ("\n".join(f"[{l['start']:.1f}-{l['end']:.1f}] {l['text']}" for l in hits)
                    or "No line contains that."), False
        if name == "update_item":
            try:
                new = actions["update"](str(args.get("id")), dict(args.get("changes") or {}))
            except ValueError as e:
                return f"Not changed: {e}", True
            return json.dumps(summary(new)), False
        if name == "remember_rule":
            try:
                r = editor_rules.add(args.get("rule"), args.get("applies_to", "both"), source=title)
            except ValueError as e:
                return f"Not saved: {e}", True
            return f"Saved rule {r['id']}.", False
        if name == "forget_rule":
            ok = editor_rules.delete(str(args.get("id")))
            return ("Deleted." if ok else "No rule with that id."), not ok
        return f"Unknown tool {name}", True

    ctx = [f"Live: \"{title}\"."]
    if focus_id and focus_id in by_id:
        ctx.append("Elijah is looking at: " + json.dumps(summary(by_id[focus_id])))
    else:
        ctx.append("Items: " + json.dumps([summary(i) for i in items]))
    if at is not None:
        ctx.append(f"Preview player position: {stamp(at)} (source seconds {float(at):.1f}).")
    else:
        ctx.append("The preview player is closed.")
    ctx.append("Saved rules: " + (json.dumps([{"id": r["id"], "rule": r["text"], "applies_to": r["applies_to"]}
                                                for r in rules]) if rules else "none yet."))

    past = history(d)
    messages = []
    for m in past[-HISTORY_TURNS:]:
        if m.get("text"):
            messages.append({"role": m["role"], "content": m["text"]})
    while messages and messages[0]["role"] != "user":          # the API needs a user turn first
        messages.pop(0)
    messages.append({"role": "user", "content": [{"type": "text", "text": "[Context]\n" + "\n".join(ctx)},
                                                 {"type": "text", "text": message}]})
    reply, used = converse(SYSTEM, messages, TOOLS, run_tool, client=client)
    labels = [x for x in (_label(n, a, o, e, by_id) for n, a, o, e in used) if x]
    changed = sorted({a.get("id") for n, a, o, e in used if n == "update_item" and not e})
    now = time.time()
    out = {"role": "assistant", "text": reply or "Done.", "actions": labels, "changed": changed, "at": now}
    media.write_json(os.path.join(d, CHAT_FILE), {"messages": past + [
        {"role": "user", "text": message, "focus": focus_id, "at": now}, out]})
    return out
