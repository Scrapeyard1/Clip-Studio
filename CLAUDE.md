# Clip Studio: handoff notes for Claude Code

Read this first, then README.md (setup, architecture, file map). This file carries the context from
the earlier Claude session that built the app, so you can continue without re-asking.

## Who and what

- Owner: Elijah (Ajamn) of the **Elijah Johnson Apologetics** YouTube/TikTok channel. Christian
  apologetics lives: callers come up one at a time (VDO Ninja, TikTok Live Studio; many are
  audio-only) to challenge or ask about the faith. Recorded in OBS (MKV/MP4), or downloaded from YouTube.
- Goal: drop in a 2-3 hour live and get, with review before anything renders:
  - one long-form video per guest conversation: ~30 s cold open (clips from later in the call) ->
    sting -> conversation with safe pause cuts -> Patreon outro. Uploaded uncut otherwise.
  - vertical shorts with word-by-word captions and framing (auto / host / full frame for
    Scripture / split / caller photo).
  - titles (channel style: who + conflict + 1-2 CAPITALIZED words), description, chapters, tags,
    thumbnails.
  - editable timelines: FCP7 XML for DaVinci Resolve (his editor, in another state) and Premiere
    (Elijah). CapCut gets MP4 + SRT.
- Machines: Elijah's MacBook (M1) and a Windows mini PC (AMD Ryzen 9); the editor uses a Mac.
  Everything must work on Mac and Windows. Posting to platforms is a later phase.

## Running it

See README "Setup". Short version (Mac):
```
brew install python@3.12 ffmpeg
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/get_models.py        # ~1.2 GB into ./models (gitignored)
python app/server.py                # http://localhost:8765
python -m pytest -q tests           # 59 tests, ~1 min
```
The Claude API key goes in the app's Settings (stored in projects/settings.json, gitignored).
Never commit it, print it, or paste it into files. Elijah has a working key with credit.

## Status (as of 2026-10-13)

Done and tested on Linux (not yet on a Mac or Windows; first Mac run is the next real test):
- Review-first flow: Analyze -> Review -> Render selected -> Approve/export.
- Checked steps (temp file -> validate -> commit), one shared frame-exact Timeline (Fraction fps),
  job queue (one render at a time, cancel, restart recovery), plan validation (plan_check.py),
  glossary corrections, edits kept in edits.json separate from AI output.
- Edits follow their clip across re-plans by actual piece overlap (clipstudio.carry_edits).
- Caller photo framing (engine/guests.py, engine/caller.py): guest library in projects/_guests,
  photo enhancement (Real-ESRGAN + GFPGAN, optional models), audio-driven wave ring.
- Shorts render piece by piece (flat memory; the old one-pass render was OOM-killed at 6 GB).
- Claude calls go through engine/claude_api.py: streaming, adaptive thinking, effort "medium".
  Sonnet 5.5 thinks by default; a low max_tokens returns NO text. Keep that helper.
- On main: chat editor (engine/editor_chat.py, tool loop in
  claude_api.converse) that edits clips via server.apply_edit and saves rules (engine/editor_rules.py,
  injected into planner + titles prompts); upload % and analysis % with time left; poster + filmstrip
  on upload; greyed "Upcoming" previews for unrendered items.
- Full preview before rendering: clipstudio.cut_plan (same timeline code as the renderers) +
  /cutclip joins nearby parts at 360p; the UI player plays parts back to back. "Hear it" is exact.
- Debate structure (Elijah's spec, Oct 31): two-pass planner in engine/moments.py (map the live, then
  each conversation in parallel) -> self_defeats with verbatim quotes, cold opens and shorts labelled
  setup/clash/turn + speaker. plan_check verifies quotes against transcript words (find_quote), snaps
  cuts to word edges, stretches clips to a nearby quote; structure_notes() names missing beats live.
  Beat labels are editable (beats / cold_open_beats) and never stale a render. On Oct 1 it found the
  "two gods" vs "only one God" contradiction that the old one-pass planner missed.
- Refresh & update (Nov 1): engine/updater.py + GET/POST /api/update + rail button. Version comes from
  engine/version.txt, stamped with the commit by GitHub's ZIP (export-subst in .gitattributes); git
  copies use git. Update swaps files in place, keeping projects/ models/ .venv/ fonts; restart is exit
  code 3, which both Open launchers loop on (the Windows loop is one block so the .bat can be replaced
  while running); started by hand it re-execs. CLIPSTUDIO_UPDATE_BRANCH tests an update from a branch.
  Tested end to end on Linux through the Mac launcher + a browser.
- Learned from Elijah's own picks (Nov 1): he supplied 4 lives (10-11, 10-13, 10-15, 10-31) with full
  transcripts, edited transcripts of what became videos, and his preview scripts. Answer key + scorer in
  tools/eval/ (transcripts not committed: public repo). His patterns, now in moments.py/plan_check.py:
  * Preview = ONE continuous 36-44 s stretch (two clips once: caller's opening line + payoff), states the
    debate, ends on an unanswered question/challenge BEFORE the answer. Anywhere in the call (often 2nd
    half). Then a 10 s branded title sequence (the "sting" slot, placeholder now 10 s), then the whole
    conversation. Transitions are quick flash/VHS glitches (render_long.glitch).
  * Videos = debates with someone arguing from outside the faith, 12-60 min; not in-house Christian Q&A,
    pastoral, friendly, or calls < 8 min (plan_check.MIN_VIDEO forces "shorts only"). Map returns
    video/video_reason; video picks are pre-ticked for rendering.
  * Boundaries: first hello to THIS caller -> through Elijah's closing words after the caller leaves.
  * cold_open_ends_on is verified (find_quote) to start in the last 15 s; a clip that stops mid-question
    is stretched to finish it (YouTube transcripts and segment-start-only prompts caused this).
  Scores (tools/eval/score.py): baseline found 11/11, picked 10/11, previews 6% overlap, 0/11 in his
  shape (3-9 stitched fragments). Tuned: picked 11/11 (11 of 14 own picks were his), previews in his
  shape (1 clip ~40 s ending on a question) for most; exact-moment overlap is noisy run to run.
  Prompt examples come from 10-11/10-13 only; 10-15/10-31 are the held-out check.
- Combined videos (Nov 1, his rule): plan_check.pair_short_debates pairs good debates of 8-15 min
  (COMBINE_MAX) in time order into plan["combos"]; members get video=False. clipstudio.effective_items
  builds the combo item (id m-<a>-<b>, "parts" from the members' EDITED times, combo=True); edits.json
  "combos" overrides the plan's pairs once he combines/splits (POST /combine, /split). render_long
  plan_timeline cuts each part; body groups split at >30 s gaps so each part decodes on its own and
  gets a glitch. The map's "video" now means quality only; length rules live in plan_check.
- FFmpeg 8 (Nov 1): 8.0 removed -filter_complex_script ("Option not found" on Windows/Homebrew
  renders). media.filter_script() asks FFmpeg once whether "-/filter_complex <file>" works (7+) and
  falls back for 6.x. Test suite passes on 6.1 and on an FFmpeg 8/master build (CLIPSTUDIO_FFMPEG).
- Tight previews (Nov 1, his rule): cuts.tighten_preview removes filler words and shrinks dead air
  over 0.23 s to 0.23 s inside cold-open clips, from word timings plus a loudness check on
  work/audio16k.wav (gap 90th-pct frame dB must be 12 dB under the clip's 80th-pct speech level).
  Cold clips become several timeline pieces labelled "Cold open N"; each clip renders as its own
  group (one glitch per clip). render_inputs v3 marks older renders as changed.
- First real run on the Oct 1 live ("What is ILLOGICAL about the Trinity?", 2:41:51) found 11
  conversations and 8 shorts; titles/description/chapters generated for "Black on YT".

## Open questions for Elijah (ask before deciding)

1. Answered (Nov 1): debates of 15 min or less are shorts, unless two are stitched into one video.
   Implemented as combos (see Status). Unconfirmed: a combined video gets ONE preview (from the
   stronger call); ask if he'd rather have a preview per call.
2. Real Patreon link for descriptions (currently a placeholder in metadata STYLE).
3. Title sequence (10 s) and Patreon outro video files, thumbnail photo folder: still not supplied.
4. Whether OBS records callers on their own audio track (Settings "Caller's audio track").
5. The editor still needs to confirm the .xml opens correctly in Resolve.

## Next: the shorts kit (docs/shorts-kit/)

Elijah supplied an editorial spec + validator from another assistant. Assessment from the earlier
session: the editorial rules are good (no claimed concessions/"admitted" without explicit evidence,
viewer_question/payoff, 7 scores with reasons, rejected list, presets clean_argument /
debate_exchange / humorous_reversal / pastoral, Scripture only from verified text, restrained
effects, analytics log). Its implementation prompt is partly outdated: most of "First: reliable
pipeline" is already done (it was written against old commit 4eee7f8). Known issues with its helper:
`normalize` rejects real transcripts (13 zero-length words out of 24,001 fail s < e), and its
request embeds every word timing (~330k tokens for one live; text-only is ~31k).

Agreed plan, phased (work on a branch, show Elijah before merging):
1. (Mostly done: see Status) Editorial brain: rewrite engine/moments.py around CLAUDE_MASTER_PROMPT.md (map the live, then
   candidates, then score), chunked text with segment IDs; fold the kit's semantic checks into
   plan_check.py (snap cuts that split a word to word edges instead of rejecting); show scores,
   reasons and rejected moments in the review UI; add the "no claimed admissions" rule to titles.
2. Shorts styles: stacked two-speaker layout, Scripture cards from verified text (a bundled
   public-domain Bible such as KJV/WEB; ESV/NKJV are copyrighted), clean_phrase vs punchy_word
   captions, phone safe-zone preview, WebVTT for previews.
3. Editor handoff: portable project package with relative paths + relink, approved SFX library with
   mute/gain, draft / needs revision / approved status, analytics log.
Skip two-person live sync for now.

## Conventions

- Plain-language UI text; Elijah is not a developer. Explain choices in short, concrete terms.
- Every bug fix gets a regression test in tests/test_core.py.
- Write outputs via media.tmp_name -> media.commit; never mark a step done without valid outputs.
- Keep source time vs output time separate; map through timeline.Timeline only.
- Don't push to main or publish anything without Elijah's go-ahead.
