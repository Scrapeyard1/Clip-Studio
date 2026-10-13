# Apologetics Shorts Kit
For Elijah Johnson Apologetics and Clip Studio. Version 1.0, October 13, 2026.

## Start here

For Claude chat: attach CLAUDE_MASTER_PROMPT.md, plan.schema.json, and a timestamped transcript/source job. Ask for a plan matching the schema. Supply video observations or frames for visual decisions; a transcript alone is insufficient.

For Claude Code: provide this folder and the Clip-Studio repository, then paste IMPLEMENTATION_PROMPT.md. It explains which existing modules must change. No changes have been made to the GitHub repository by this deliverable.

## Included

- CLAUDE_MASTER_PROMPT.md: detailed selection, context, pacing, framing, captions, Scripture, audio, packaging, and evaluation instructions.
- RESEARCH_NOTES.md: linked examples, evidence categories, observations, source-pair limitations, and testing strategy.
- IMPLEMENTATION_PROMPT.md: coding brief for the existing app.
- plan.schema.json: model output contract, JSON Schema draft 2020-12.
- shorts_rules.py: standard-library CLI to normalize existing transcripts, build Claude requests, validate timing/evidence/assets, and compile a source/output timeline.
- example_job.json and example_plan.json: intentionally synthetic demonstration, not real footage or a completed clip.
- test_shorts_rules.py: 21 focused tests.

## Run the demonstration

Python 3.11+; no third-party packages or API keys required for these commands. Run from this folder:

```bash
python shorts_rules.py validate example_job.json example_plan.json
python shorts_rules.py compile example_job.json example_plan.json --out compiled_demo.json
python shorts_rules.py request example_job.json --out claude_request.md
python -m unittest discover -v
```

The demonstration compiles to 25 seconds. The source range 49–57 seconds begins at output second 17. A word starting at source second 50 therefore begins at output second 18. The example words are deliberately partial and do not form a real caption track.

## Use an existing Clip Studio transcript

```bash
python shorts_rules.py normalize /absolute/path/transcript.json --source-id live-2026-10-13 --out job.json
python shorts_rules.py request job.json --out claude_request.md
```

Give the resulting request to Claude. Save its JSON response as plan.json, then:

```bash
python shorts_rules.py validate job.json plan.json
python shorts_rules.py compile job.json plan.json --out compiled_plan.json
```

Invalid plans return a nonzero exit code. Successful compilation does not render a video. A renderer must implement the instructions in compiled_plan.json. Do not pass the new plan blindly into the old render_short.py: its expected fields and supported layouts differ.

## Enrich the job before advanced layouts

A region is an editor-confirmed normalized source rectangle:

```json
{"id":"host","confirmed":true,"box":[0.0,0.0,0.5,1.0]}
```

This rectangle is only an example, not a crop for Elijah's recordings. Detect/confirm regions for the actual source. V1 static regions require stable layout; future time-dependent camera changes need an extended model.

An audio asset requires explicit platform approval:

```json
{"id":"soft_reveal","kind":"sfx","approved":true,"duration_s":0.5,"platforms":["youtube","tiktok"]}
```

Approval metadata is supplied by the editor or asset manager; the validator does not verify licenses. An evidence asset needs kind=evidence, approved=true, exact text, reference, and provenance. Include translation/edition in the reference. An avatar requires kind=avatar and approved=true. Observations should include what was inspected, source times, confidence, and whether they describe audio, frames, or both. The current helper stores but does not verify observations.

## Boundaries and limitations

This is prompt + planning/validation code, not a complete automatic editor. No network/API calls occur in the helper. It does not identify faces, transcribe, listen, run Claude, mix audio, render media, enforce all JSON Schema keywords, or prove semantic accuracy. Validate the full schema in the app in addition to these checks. Model IDs, API pricing, and current SDK calls are deliberately not hardcoded.

The helper checks source identity, finite and in-range times, chronological non-overlapping pieces, cuts crossing provided words, retained evidence segments, layout coverage, required regions/assets, effect duration/approval, and an output cap. It cannot detect a misleading omission or establish that a title is warranted. ASR timestamps are estimates; inspect the actual audio before treating a boundary warning as definitive.

V1 assumes 1x speed and hard cuts; no reordering, replay, crossfades, frozen frames, or asset looping. Frame quantization belongs in the renderer and must retain rational source rates. Scores and numeric editing defaults are provisional, not empirically fitted success predictions. Final review is required for publishing, while automatic draft rendering remains the intended workflow.

Research limitations are explicit in RESEARCH_NOTES.md: the external longform/short pairs were not fully audiovisually aligned, and there is no verified sound-effects audit or private retention data. The supplied videos support visual style observations. One native TikTok caption track supports a timed structural analysis. Public metadata supports the remaining examples.
