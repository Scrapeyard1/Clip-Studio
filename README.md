# Clip Studio

Turns a recorded live into review-ready drafts: one long-form video per guest
conversation, vertical shorts, captions, titles, descriptions, chapters,
thumbnails, and a timeline your editor opens in DaVinci Resolve or Premiere Pro.

## Run the app

```
python app/server.py
```

Opens http://localhost:8765. Add a recording (drop it in, paste where it already sits so
nothing is copied, or paste a YouTube link to one of your own videos), then review: the live map shows every conversation found; each cut
conversation has its video, three titles, three thumbnails, description and chapters.
"Open in DaVinci Resolve or Premiere" downloads an `.xml` timeline:
Resolve: File > Import > Timeline. Premiere: File > Import. CapCut: download the video and
import the `.srt` captions.

## What's here

| File | Does |
| --- | --- |
| `clipstudio.py` | One command: `python clipstudio.py process <recording>` runs every step and resumes if interrupted |
| `engine/transcribe.py` | Local speech-to-text with word timestamps (Parakeet TDT 0.6B via sherpa-onnx). No cloud, ~10x real time on 2 CPU cores |
| `engine/cuts.py` | Safe cuts. Only cuts where nobody is talking AND the audio is quiet, so sound effects and laughter survive |
| `engine/moments.py` | The planning step: sends the transcript to Claude with the channel's editing recipe, gets back conversations, cold opens, titles, shorts |
| `engine/render_long.py` | Cold open -> sting -> conversation with safe cuts -> Patreon outro. Also writes `.srt` captions and a CMX3600 `.edl` |
| `engine/render_short.py` | 1080x1920 short: face-tracked crop, screen-share layout, animated word-by-word captions, hook text |
| `engine/export_timeline.py` | Editable timeline (FCP7 XML) for Resolve and Premiere: every cut against the original file, one audio track per OBS track, a marker at every trimmed pause |
| `engine/metadata.py` | Titles, description, chapters (timed to the final cut), tags, thumbnail briefs |
| `engine/thumbnail.py` | 1280x720 thumbnails in the channel's style: cutouts, outline glow, Anton headline, arrow / lightning / banner / bubble |
| `engine/fetch_youtube.py` | Gets a video from a YouTube link (yt-dlp, best video + audio merged to MP4). Keep it current with `pip install -U yt-dlp` |
| `app/` | The local app: `server.py` (runs the pipeline, serves files) and `static/index.html` (the interface) |

## Setup (Mac or Windows)

1. Install Python 3.11+ and FFmpeg (Mac: `brew install ffmpeg python`; Windows: `winget install ffmpeg Python.Python.3.12`).
2. `pip install -r requirements.txt`
3. Download models: `python scripts/get_models.py` (one time, ~830 MB). It fetches:
   - `sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8` and `silero_vad.onnx` from the sherpa-onnx `asr-models` GitHub release
   - `yunet.onnx` (face detection) from the opencv_zoo repo
   - `u2net_human_seg.onnx` (thumbnail cutouts) from the rembg GitHub release
   Fonts (Anton, Archivo, Poppins) are in `assets/fonts`.
4. Start the app and add your Claude API key in Settings (also: sting, Patreon outro, thumbnail photo folder).

## Config for real recordings

Pass `--config` to `render_long.py` as JSON:

```json
{"sting": "assets/sting.mp4", "outro": "assets/patreon_outro.mp4",
 "encoder": "videotoolbox"}
```

`encoder`: `videotoolbox` on the MacBooks, `amf` on the Ryzen mini PC (if its GPU supports it), default `libx264` everywhere.

## Large files

Nothing loads the video into memory. Audio is extracted once (~110 MB per hour);
every render decodes only the stretch it needs from a seek point. A 5 GB, 3-hour
OBS recording needs about 1 GB of free disk for working files.

## Known limits (Phase 1)

- Speaker labels come from separate OBS audio tracks (Phase 2). On a single mixed track, captions don't say who's talking.
- Sting and outro are placeholder cards until their files are set in Settings.
- Thumbnails: guest images (AI-generated stand-ins for audio-only callers) need an image-generation service; until then thumbnails use the solo, banner and bubble layouts.
- No one-click installer yet: Phase 4 packages this as a Mac app and a Windows installer.
- Caption spelling follows the transcription; the custom name list (Melchizedek, Tacitus...) is Phase 2.
