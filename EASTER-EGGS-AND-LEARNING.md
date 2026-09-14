# Easter Eggs + Self-Learning Loop

Both daily workflows (`daily-create.yml`, `daily-temp-explainer.yml`) run
`node core/github-action-runner.js --mode daily`, which is the Type 1 clip
pipeline in `pipeline/type1-clip-pipeline.js`. Two things changed there.

## 1. Country flag / voice line removed

The rendered short no longer gets the country flag at the top or the
"Enjoy this Asian edit from {country}" voice-over. The country is still
detected and used for selection, the title and the description.

## 2. Easter egg overlays

Tiny semi-transparent cartoon characters (green-screen clips) pop into a
bottom corner at random moments and leave again.

| Egg | What happens |
| --- | --- |
| spidey-popcorn | chubby Spidey eats popcorn, then turns to look at you |
| gwen-popout | Gwen runs up from the distance and pops out at you |
| gwen-stick / spidey-stick | walk in from the edge and poke the video with a stick |
| flash-chase / spidey-chase | chase a robber across the corner, lose him, stand and watch |
| gwen-peek / gwen-tap | Gwen peeks in and waves / taps the screen |

Rules (see `core/easter-eggs.js`):

- 1 egg on clips under 20 s, 1 or 2 on longer clips, never more than 2
- eggs are at least 6 s (or a quarter of the clip) apart, never in the first
  1.5 s or the last second
- 25 to 36 percent of the frame width, 82 percent opacity, bottom corner,
  kept 360 px above the frame bottom so YouTube's title overlay does not
  cover them
- random side (mirrored when needed), quick fade or slide out
- no audio from the eggs

Env overrides: `EASTER_EGGS_ENABLED=false`, `EASTER_EGG_OPACITY`,
`EASTER_EGG_SIZE` (multiplier), `EASTER_EGG_SAFE_BOTTOM`, `EASTER_EGG_MAX`.

Assets live in `core/easter-eggs/` as stacked mp4s (colour on top, alpha
matte below); the pipeline splits them with ffmpeg `alphamerge`, so no
chroma keying happens at render time. To rebuild them from the raw
green-screen clips:

```
python scripts/prepare-easter-eggs.py --src "path/to/raw/clips"
```

## 3. Self-learning from the channel's own stats

`core/performance-tracker.js` runs at the start of every daily run:

1. pulls all uploads + public stats (views, likes, comments) through the
   YouTube Data API using the existing OAuth secrets
2. scores each short by views per day over its first two weeks
   (log-scaled so one viral clip does not dominate)
3. joins them with `memory/posted-videos.json` (country, source channel,
   easter eggs used, how the title was chosen)
4. writes `memory/performance-stats.json` and
   `memory/performance-insights.json` (both committed by the workflow)

The insights are used in three places:

- **Title prompt**: `gemini-service.generateTitle` adds the best and worst
  titles, the title patterns that lift or hurt performance and the best
  countries to the prompt, and asks Gemini for a main title plus two
  alternatives.
- **Title pick**: the alternatives are ranked by a k-nearest-neighbour
  prediction over past titles (free `all-MiniLM-L6-v2` sentence
  embeddings via `core/title-similarity.py`, falling back to character
  n-grams if the model is missing) blended with the learned patterns.
- **Clip pick**: country rotation still dominates, but learned country and
  source-channel weights break ties and can promote a country that clearly
  performs better.

Weights only apply once a country or channel has 3 or more shorts, and
patterns once 5 or more; below 8 scored shorts nothing is applied.

Check the current state any time:

```
node core/performance-tracker.js --report
node core/performance-tracker.js --sync     # needs YouTube credentials in env
```

Optional upgrade: enable the *YouTube Analytics API* in the Google Cloud
project and re-run `node youtube-automation/setup-youtube.js` (the
analytics scope is now in the list). The tracker then also uses watch time
and average view percentage.
