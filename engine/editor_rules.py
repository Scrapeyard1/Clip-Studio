"""Editing rules Elijah teaches the chat editor ("start shorts on the question, not my greeting").

Saved once in <projects>/_style/rules.json and added to every future planning and titles request,
so a correction made on one live improves the next one. Elijah can read and delete them in Settings.
"""
import os
import time
import uuid

import media
import paths

SCOPES = {"shorts", "long_form", "both", "titles"}


def _file():
    return os.path.join(paths.PROJECTS, "_style", "rules.json")


def all_rules():
    return media.load_json(_file(), default={"rules": []}).get("rules", [])


def add(text, applies_to="both", source=None):
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("The rule is empty.")
    if len(text) > 400:
        raise ValueError("Keep a rule to one or two sentences.")
    if applies_to not in SCOPES:
        applies_to = "both"
    rules = all_rules()
    for r in rules:                       # the same rule twice adds nothing
        if r["text"].lower() == text.lower():
            return r
    r = {"id": uuid.uuid4().hex[:8], "text": text, "applies_to": applies_to,
         "created": time.strftime("%Y-%m-%d"), "source": source or ""}
    os.makedirs(os.path.dirname(_file()), exist_ok=True)
    media.write_json(_file(), {"rules": rules + [r]})
    return r


def delete(rule_id):
    rules = all_rules()
    keep = [r for r in rules if r["id"] != rule_id]
    if len(keep) == len(rules):
        return False
    media.write_json(_file(), {"rules": keep})
    return True


def prompt_block(purpose):
    """Rules as a section to append to a system prompt. purpose: 'planning' or 'titles'."""
    want = {"planning": {"shorts", "long_form", "both"}, "titles": {"titles", "both"}}[purpose]
    rules = [r for r in all_rules() if r["applies_to"] in want]
    if not rules:
        return ""
    label = {"shorts": "shorts", "long_form": "long-form", "both": "all clips", "titles": "titles"}
    lines = "\n".join(f"- ({label[r['applies_to']]}) {r['text']}" for r in rules)
    return ("\n\nRULES ELIJAH HAS TAUGHT YOU (from his own corrections; follow them unless the footage "
            f"makes one impossible):\n{lines}")
