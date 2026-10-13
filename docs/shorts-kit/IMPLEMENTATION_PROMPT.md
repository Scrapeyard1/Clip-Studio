# Paste into Claude Code with the Clip-Studio repository and this kit

Implement this editorial specification in Clip-Studio. Read CLAUDE_MASTER_PROMPT.md, RESEARCH_NOTES.md, plan.schema.json, and shorts_rules.py first. Inspect the current checkout and any AGENTS.md before changing code. The earlier review used commit 4eee7f8; verify the current code rather than assuming it is unchanged. Work on a branch. Do not publish videos or push/merge without the user's applicable authorization.

Goal: import apologetics lives, automatically create editable 15–70 minute longform conversation drafts and short-form drafts, then let Elijah and his editor adjust them. Preserve context and source media. Build functioning vertical slices, not mocked success states.

## First: reliable pipeline

Fix portable model/font paths, source filename collisions, atomic output completion, per-project progress, concurrency limits, restart recovery, and preserving manual edits on retry. Validate media and all required sidecar files before calling a render complete. Hash or version dependencies so transcript, cut, asset, and settings changes invalidate the right outputs. Use source frame-rate rationals throughout; fix chapter sting duration and source/output time inconsistencies. Expose intelligible failures and keep original files immutable.

## Second: editable plan contract

Keep the current longform plan separate from the new short-form schema. Add source_id and immutable segment IDs during transcription. Preserve the current transcript words while adding explicit uncertainty; their existing word-end estimates are not ground-truth alignment. Feed source windows, context, known speaker regions, approved assets, and inspected audio/visual observations to Claude.

Use the master prompt to discover and rank moments. Validate the response against the JSON schema and the semantic checks before scheduling a render. On failure, give Claude the specific validation errors and request a bounded correction without inventing missing evidence. After a small configurable retry limit, expose the issue to the editor. Never silently accept malformed output.

The included Python helper is an offline reference, not a replacement rendering engine. Its v1 contract supports only chronological 1x hard cuts. It validates several structural and timing constraints, not the truth of captions or a title. Add schema validation in the application; the dependency-free helper is not a generic JSON Schema implementation.

## Third: rendering support

Extend engine/render_short.py to consume compiled timelines and explicit layout intervals. Implement source_fit, confirmed host/guest closeups, stacked speakers, approved caller avatars, and evidence cards. Do not silently downgrade unsupported instructions. Keep one crop per participant and allow time-dependent regions when camera layouts change. Preview actual safe zones, line breaks, and evidence legibility. Use bundled fonts with configurable size and style.

Preserve separate dialogue and added audio tracks. Approved SFX/music must resolve by asset ID, not arbitrary paths from the model. Validate assets at render time. Add cue gain and placement controls, ducking, and an option to disable effects. Do not add effects where audio evidence is unavailable without flagging review.

Use one timeline mapping for rendered frames, word captions, source references, and editor export. Generate WebVTT for browser previews and SRT for interchange. Maintain word timestamps after cuts; align or flag dubious boundaries. Keep fractions/rational frame rates until converting at the final export boundary.

## Fourth: review UI and editor handoff

Show candidates and finished drafts progressively, not only after the entire job completes. Include transcript navigation, source context playback, trim controls, caption correction, layout choice, crop adjustment, evidence replacement, effect mute/gain, and timestamped revision notes. Save user overrides separately from regenerated AI suggestions. Rerender only changed outputs. Track draft, needs revision, and approved status without resetting approvals on unrelated work.

Package a portable project manifest with relative media/asset references, checksums, schema version, and relink support. A local server plus JSON does not provide two-person remote sync. Implement explicit project handoff first; if shared editing is required, design authenticated storage, revisions, and conflict handling rather than sharing a mutable JSON file blindly.

## Acceptance criteria

1. A known short can be rendered from a real source using approved cuts and stable captions.
2. Two projects cannot mix progress or overwrite recordings; an interrupted render retries correctly.
3. Re-rendering preserves manual titles, boundaries, captions, and approval state unless the changed media requires explicit re-review.
4. A word, caption, overlay, and sound cue retain their intended timing across a cut; verify fractional-rate footage.
5. All three main styles have an actual phone-sized visual preview; no fake success placeholder.
6. Scripture/evidence text comes from verified supplied assets and fits the layout.
7. The editor can disable every added effect and restore a cut.
8. Source context is one click away for each draft.
9. Exported editable projects relink correctly on a second machine.
10. Run the included 21 tests plus targeted integration tests for the real renderer. Report what was actually run, any unavailable API/model/media dependencies, and unresolved limitations.

Do not claim the system guarantees views. The scoring model is an editorial ranking heuristic. Keep an analytics log for later tuning using the channel's own performance.
