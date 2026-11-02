# Clip Studio

Turns a recorded live into review-ready drafts: one long-form video per guest
conversation, vertical shorts, captions, titles, descriptions, chapters,
thumbnails, and a timeline your editor opens in DaVinci Resolve or Premiere Pro.

## Easiest setup: double-click

Download the folder (GitHub: Code > Download ZIP), unzip it somewhere permanent, then:

- **Mac:** double-click `Install Clip Studio (Mac).command` once (about 20 minutes), then
  `Open Clip Studio (Mac).command` whenever you want to use it.
- **Windows:** `Install Clip Studio (Windows).bat` once, then `Open Clip Studio (Windows).bat`.

`START HERE.txt` walks a non-technical person through it, including what to do when macOS blocks a
downloaded file. The installers install Homebrew/winget packages (Python 3.12, FFmpeg), make a private
Python folder (`.venv`) inside the Clip Studio folder, download the models, and run the setup check.
Each copy of Clip Studio keeps its own projects; copies don't sync with each other.

**Getting the newest version:** press **Refresh & update** (bottom left). It checks GitHub; if there's a
newer version it shows the latest change and **Update now** downloads it, keeps `projects/` (lives,
edits, renders, settings, API key), `models/`, `.venv/` and downloaded fonts, reinstalls Python packages
only if `requirements.txt` changed, restarts and reloads the page. Already up to date: it just refreshes
the page. It refuses while something is analyzing or rendering. A copy made with `git clone` updates
with `git pull --ff-only` instead. (Copies downloaded before this button existed need one more
download to get it.)

## Setup by hand (Mac or Windows, one time)

1. Install Python 3.11+ and FFmpeg.
   Mac: `brew install ffmpeg python`. Windows: `winget install ffmpeg Python.Python.3.12`.
2. `pip install -r requirements.txt`
3. `python scripts/get_models.py` downloads the models (~1.2 GB, including ~410 MB for caller-photo
   enhancement; add `--no-photo-models` to skip those) and any missing fonts, then
   prints a setup check. Every line should say `ok` except the Claude API key, which you add in the app.
4. `python app/server.py` opens http://localhost:8765. Add your Claude API key in Settings, plus
   the 10-second title sequence, the Patreon outro and your thumbnail photo folder.

The app repeats the setup check every time it starts, and the home screen shows anything missing
with the exact fix.

### Where things live

| Setting | Default | Change it with |
| --- | --- | --- |
| Models | `models/` in this folder | `CLIPSTUDIO_MODELS=/path/to/models` |
| Projects (recordings, drafts) | `projects/` in this folder | `CLIPSTUDIO_PROJECTS=/path/to/projects` |
| FFmpeg | whatever is on PATH | `CLIPSTUDIO_FFMPEG` / `CLIPSTUDIO_FFPROBE` |
| Video encoder | Mac: built-in hardware encoder; elsewhere libx264 | `"encoder": "amf"` (AMD GPU) or `"libx264"` in `projects/settings.json` |

## How a live goes through it

**Analyze → Review → Render selected → Approve and export**

1. **Analyze** (about 10 minutes per hour of video). Add a recording (drop it in, point to where it
   already sits so nothing is copied, or paste a YouTube link). Clip Studio transcribes it, finds the
   quiet stretches, and asks Claude for a plan: each guest conversation with its cold open, plus shorts.
   The plan is checked before you see it: times must be inside the recording, each conversation needs
   a cold open, each short needs pieces of a sensible length. Anything wrong is listed with what to fix.
2. **Review**. Nothing has been rendered yet. For every conversation you can change the guest name,
   topic, start and end, and the cold-open clips, and press **Hear it** to play any stretch with
   10 seconds of context around it. For shorts: hear each piece, and pick the framing:
   *Auto* (follows the face, switches to the screen when Scripture is shared), *Host only*,
   *Full frame* (for Scripture and screen shares), *Split screen*, or *Caller photo* for audio-only
   guests: you (or the shared screen) in a gold-edged box up top, captions in the middle, and the
   caller's photo in a circle with a ring of bars that moves with the audio and their @handle.
   Add a caller's photo and handle once on any short; it's reused on every live they join. Photos are
   enhanced locally (4x upscale with Real-ESRGAN, face restored with GFPGAN, centred on the face). Under **Names and terms**, fix what
   the transcriber gets wrong (`McKills a day => Melchizedek`); corrections go into captions, titles
   and descriptions without disturbing timing.
3. **Render selected**. Tick what you want and press Render. One render runs at a time; other
   lives wait in line with their place shown. Stop works mid-render.
4. **Approve and export**. Each rendered conversation shows its video, chapters, every trimmed pause
   (Hear it / Restore), titles, thumbnails and description. Restoring a pause or editing a time marks
   just that clip "changed since render"; re-rendering redoes only that clip.
   Exports: **Resolve / Premiere** (`.xml`; Resolve: File > Import > Timeline, Premiere: File > Import),
   the video, and `.srt` captions (CapCut: import the video, then the captions).

## How clips are chosen

Claude plans in two passes. First it maps the live: every conversation, the caller, the topic, how
strong it is, and whether it should be its own video. Then it reads each conversation on its own and:

- finds every place the caller's argument **defeats itself** (he contradicts himself, admits the point,
  leans on a source or standard he rejects), quoting both statements word for word with their times;
- builds the **cold open** the way Elijah builds his previews: one continuous 30-45 s stretch of the
  real conversation (two clips at most) that tells a stranger what's being debated, has real tension,
  and ends on an unanswered question or challenge, just before the answer;
- builds the **shorts** in three beats: **Setup** (the question or claim that tells a stranger what's
  being debated), **Clash** (both sides in their own voices), **Turn** (his argument undercutting itself
  and Elijah catching it, or the decisive answer).

**Learned from Elijah's own videos.** These rules come from four October lives where Elijah supplied
what he actually published (`tools/eval/`). His long-forms are debates where the guest argues from
outside the faith (Muslim, atheist, Hebrew Israelite, oneness); sincere questions and in-house
Christian discussions aren't made into videos. A good debate of 15 minutes or less is shorts only,
unless two of them are combined into one video (his rule): the planner pairs them in the order they
happened, and an odd one out stays shorts only. Each conversation shows "video pick", "shorts only" or
"in a combined video" (hover for why), and videos come ticked for rendering.

**Combined videos.** One cold open (from the stronger of the two calls), the title sequence, then both
conversations back to back with a glitch transition between them, then the outro. The combined card
lists both calls; change their times on each call's own card and the combined video follows. "Split
apart" undoes it; "Make one video with" on any conversation of 15 minutes or less pairs it by hand. A video
runs from the first hello to the guest through Elijah's closing words after the guest leaves. Against
those four lives the planner now picks all 11 of his videos (10 before) and builds every preview in his
shape; it also suggests about one extra debate per live that he didn't make.

Before you see anything, the app checks every quoted self-defeat against the words actually said near
that time (correcting the time if it's slightly off) and drops any it can't find, so nothing is
claimed without evidence. Cuts that land inside a word move to its edge, and a quote just outside a
clip pulls the clip edge out to it. In review each clip shows its beat and who's speaking, a red box
shows any self-defeat with both quotes ("Hear it" on each), and a note names any missing beat
("No setup: viewers won't know what's being debated"). For a cold open it shows the question it ends
on (checked against the transcript; a clip that stops mid-question is stretched to finish it) and notes
anything off: too many clips, too short or long, or no open question at the end. The chat editor can
fix those for you.

## The chat editor

"Ask the editor" (left rail) or "Tell the editor" on any clip opens a chat. Say what to change in your
own words: "This short starts too late, start it on my question, because the question is the setup."
It reads the transcript, moves the cut to the nearest line boundary, and makes the change through the
same checks as a manual edit. If the preview player is open, "here" means where it's paused.
When you give a reason that applies beyond this clip, it saves it as a rule, and every future plan and
title follows it. Settings lists everything it has learned, with Forget buttons.
Rules live in `projects/_style/rules.json`; chats in each project's `chat.json`.

## Progress and previews

Uploads show a percentage, size and time left. Right after a recording is added you get a picture of
it and a filmstrip across the whole live, with an overall percentage and time left while it's analyzed
(transcription is most of the bar). Anything not rendered yet is shown greyed with an "Upcoming" tag.
Finished renders play in full color.

**Preview** (on every conversation and short, or click its "Upcoming" picture) plays the whole video as
it will be cut, before you render: the cold open, the title sequence, the conversation with every trimmed pause
already removed, then the outro; for shorts, every piece in order with pauses tightened. It's built
from the same code as the render, so the length matches to the frame. The bar under it shows each part
and a red tick at every trimmed pause; click anywhere to jump. Parts are made on the fly at 360p a few
seconds ahead of where you're watching. Framing, captions and titles come with the real render.
**Hear it** plays exactly that clip; "Add 10 s around it" plays the context too.

## Your edits are kept separate

Everything you change in Review is saved in `edits.json` in the project, apart from Claude's output.
Re-planning or regenerating titles never overwrites your titles, times, selections or approvals; the
app shows Claude's new suggestions next to yours.

## Project folder

```
projects/<live>/
  project.json      name, source, job status
  edits.json        your changes (never overwritten by the AI)
  source/           uploaded recording (each upload gets its own project, so same names never clash)
  work/             audio, transcript, quiet stretches, plan, step records, preview clips
  drafts/<id>.*     .mp4, .srt, .xml, .timeline.json, .cuts.json, titles, thumbnails
```

## Reliability

- **Retries are safe.** Every file is written as `name.partial.ext`, checked (plays, has audio and
  video, parses), then moved into place. A step only counts as done when all its files pass that
  check, including captions and editor exports, and when its inputs haven't changed since.
  A crash mid-render leaves nothing that looks finished; Retry redoes it.
- **One timeline for everything.** The video, captions, chapters and the Resolve/Premiere timeline
  are all built from the same frame-exact plan (`drafts/<id>.timeline.json`), using the recording's
  real frame rate (29.97 stays 30000/1001) and the real length of your title sequence and outro.
- **Transitions.** Each preview clip, the title sequence, the conversation and the outro start with a
  quick flash and VHS glitch (about a quarter second, timing untouched). Pause trims inside the
  conversation stay invisible cuts. `"transition": "cut"` in `projects/settings.json` turns it off.
  The Resolve/Premiere timeline has plain cuts so the editor can add his own.
- **Restart-proof.** If the app closes mid-job, it picks the job back up on the next start.

## Command line

```
python clipstudio.py analyze projects/<live>
python clipstudio.py render  projects/<live> [ids...]
python -m pytest -q tests          # regression tests, ~10 seconds, no models needed
```

## What's here

| File | Does |
| --- | --- |
| `clipstudio.py` | The pipeline: analyze, render, checked steps, edits overlay |
| `engine/paths.py` | Every file location, environment overrides, the setup check |
| `engine/media.py` | FFmpeg runner (cancellable), probing, output validation, safe file commits |
| `engine/timeline.py` | Frame-exact timeline shared by video, captions, chapters and XML |
| `engine/transcribe.py` | Local speech-to-text with word timestamps (Parakeet TDT 0.6B via sherpa-onnx) |
| `engine/cuts.py` | Safe cuts: only where nobody is talking and the audio is quiet |
| `engine/moments.py` + `plan_check.py` | Claude's plan, then validation (one automatic repair attempt) |
| `engine/glossary.py` | Names and terms corrections that keep word timing |
| `engine/render_long.py` | Cold open → 10 s title sequence → conversation with safe cuts → Patreon outro, glitch transitions |
| `tools/eval/` | Elijah's published picks from four lives and a scorer for checking planner changes |
| `engine/render_short.py` | 1080x1920 short: framing modes, word-by-word captions, hook text. Rendered piece by piece, so memory stays flat for any length |
| `engine/guests.py` + `caller.py` | Guest library (photo, @handle) with photo enhancement, and the audio-driven caller badge |
| `engine/export_timeline.py` | FCP7 XML for Resolve and Premiere: cuts against the original file, a marker at every trimmed pause |
| `engine/metadata.py` | Titles, description, chapters (on the final cut), tags |
| `engine/thumbnail.py` | 1280x720 thumbnails in the channel's style |
| `engine/updater.py` | Refresh & update: version check against GitHub, safe in-place update that keeps your work |
| `engine/fetch_youtube.py` | Gets your video from a YouTube link (keep current: `pip install -U yt-dlp`) |
| `app/` | The local app: `server.py` (queue, API) and `static/index.html` (the interface) |

## Known limits

- The wave around a caller's photo follows the whole mix unless OBS records callers on their own
  audio track: set its number in Settings ("Caller's audio track") and it moves only when they talk.
  YouTube downloads have one mixed track.
- Speaker labels per OBS audio track aren't used yet; on one mixed track captions don't say who's talking.
- The title sequence and outro are placeholder cards (10 s and 4 s) until their files are set in Settings.
