# Clip Studio

Turns a recorded live into review-ready drafts: one long-form video per guest
conversation, vertical shorts, captions, titles, descriptions, chapters,
thumbnails, and a timeline your editor opens in DaVinci Resolve or Premiere Pro.

## Setup (Mac or Windows, one time)

1. Install Python 3.11+ and FFmpeg.
   Mac: `brew install ffmpeg python`. Windows: `winget install ffmpeg Python.Python.3.12`.
2. `pip install -r requirements.txt`
3. `python scripts/get_models.py` downloads the models (~830 MB) and any missing fonts, then
   prints a setup check. Every line should say `ok` except the Claude API key, which you add in the app.
4. `python app/server.py` opens http://localhost:8765. Add your Claude API key in Settings, plus
   the sting, the Patreon outro and your thumbnail photo folder.

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
   *Full frame* (for Scripture and screen shares), or *Split screen*. Under **Names and terms**, fix what
   the transcriber gets wrong (`McKills a day => Melchizedek`); corrections go into captions, titles
   and descriptions without disturbing timing.
3. **Render selected**. Tick what you want and press Render. One render runs at a time; other
   lives wait in line with their place shown. Stop works mid-render.
4. **Approve and export**. Each rendered conversation shows its video, chapters, every trimmed pause
   (Hear it / Restore), titles, thumbnails and description. Restoring a pause or editing a time marks
   just that clip "changed since render"; re-rendering redoes only that clip.
   Exports: **Resolve / Premiere** (`.xml`; Resolve: File > Import > Timeline, Premiere: File > Import),
   the video, and `.srt` captions (CapCut: import the video, then the captions).

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
  real frame rate (29.97 stays 30000/1001) and the real length of your sting and outro.
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
| `engine/render_long.py` | Cold open → sting → conversation with safe cuts → Patreon outro |
| `engine/render_short.py` | 1080x1920 short: framing modes, word-by-word captions, hook text |
| `engine/export_timeline.py` | FCP7 XML for Resolve and Premiere: cuts against the original file, a marker at every trimmed pause |
| `engine/metadata.py` | Titles, description, chapters (on the final cut), tags |
| `engine/thumbnail.py` | 1280x720 thumbnails in the channel's style |
| `engine/fetch_youtube.py` | Gets your video from a YouTube link (keep current: `pip install -U yt-dlp`) |
| `app/` | The local app: `server.py` (queue, API) and `static/index.html` (the interface) |

## Known limits

- **Guest-only framing** isn't offered: most guests call in audio-only, so there's no guest face to
  frame. Video guests appear in *Full frame* and *Split screen*.
- Speaker labels per OBS audio track aren't used yet; on one mixed track captions don't say who's talking.
- The sting and outro are marked as gaps in the editor timeline until their files are set in Settings.
- No one-click installer yet.
