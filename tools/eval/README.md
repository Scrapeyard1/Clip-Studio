# Planner evaluation against Elijah's own picks

`answer_key.json` is what Elijah actually published from four October 2026 lives (10-11, 10-13, 10-15,
10-31): each long-form video's start and end in the live, and the clips of its preview (cold open), from
his edited transcripts and "October 2026 Preview Scripts". Use it to check any planner change.

The transcripts aren't in the repo. Put the four YouTube-style transcripts (`[h:mm:ss] text` lines) in
`tools/eval/transcripts/` named `10-11.txt`, `10-13.txt`, `10-15.txt`, `10-31.txt` (the folder is
gitignored), then:

```
python tools/eval/convert.py            # -> transcripts/transcript-<live>.json, the app's format
python tools/eval/run.py out_new        # plans all four with the current engine (~$1.70 in total)
python tools/eval/score.py out_new      # add -v for the reason behind each video / shorts-only call
```

The scorer reports: how many of his videos the planner found and picked as videos, how many of its
own picks he actually made, how far the start and end are off, and for previews: overlap with his,
length, number of clips, and whether it has his shape (1-2 clips, 25-50 s, ends on a checked question).
Exact overlap with his preview is noisy run to run (several moments in a call make good previews);
the shape is the steadier measure. 10-11 and 10-13 supply the two preview examples in the prompt, so
judge generalisation on 10-15 and 10-31.
