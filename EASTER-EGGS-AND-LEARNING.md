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
- 27 to 46 percent of the frame width, 82 percent opacity, along the bottom
- characters move like they belong: the chases run in from one screen edge
  and out the other (the box travels so the robber leaves through the far
  edge), the stick characters walk in to the bottom middle and poke up at
  the content, Gwen's tap rises at the bottom middle and points up
- characters whose body is cropped by their own clip (close-ups, popcorn
  Spidey, the pop-out) sit on the frame's bottom edge so the crop lands on
  the screen edge; standing characters float 260 px above it
- random side (mirrored when needed), quick fade / slide / sink out
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

## 2b. Watermark: cover the source's mark, or the usual corner

`core/watermark-cover.js` runs as a second lossless pass after the render:

1. `core/watermark-detector.py` (OpenCV) samples 32 frames and keeps the
   edges that stay put in nearly every frame while the content moves;
   small dense clusters near the border are proposed as static overlays
   (channel handles, platform logos, "AI generated" tags). Static tripod
   shots are recognised and skipped.
2. Gemini looks at two crops of each proposal and confirms it is a
   watermark and not a caption, sign or object. Only proposals that are
   present in more than 85 percent of frames are trusted without Gemini.
3. Confirmed spots get `delogo`, a dark translucent badge and the channel
   logo + handle on top, sized to cover the mark. If nothing is confirmed
   the small 40 percent watermark goes bottom-right as before.

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
- **Clip pick** (Phase 3 of the pipeline):
  1. *Niche gate*: Gemini now also returns a one-line summary, a category
     and whether the clip fits the Asian niche (Asian setting, people,
     culture or media such as k-pop / anime). Anything marked non-Asian is
     dropped; if nothing fits, nothing is posted that day.
  2. *Never the same thing twice*: exact repeats are blocked by the used
     video ids; near-duplicates are caught by embedding similarity of the
     clip summary / source title against everything already posted
     (cosine above 0.9 is skipped).
  3. *Weighted pick*: each remaining candidate gets a score from country
     rotation, learned country / source-channel / category weights and how
     similar its summary is to the channel's best performers. The winner is
     sampled from a softmax over those scores (temperature 0.35), so
     content like what already works is likely but never guaranteed and
     other clips still get their turn.

Weights only apply once a country, channel or category has 3 or more
shorts, and patterns once 5 or more; below 8 scored shorts nothing is
applied. Each post stores its summary, category, source title, eggs and how
the title was chosen in `memory/posted-videos.json`, so the loop gets
sharper with every upload.

Check the current state any time:

```
node core/performance-tracker.js --report
node core/performance-tracker.js --sync     # needs YouTube credentials in env
```

Optional upgrade: enable the *YouTube Analytics API* in the Google Cloud
project and re-run `node youtube-automation/setup-youtube.js` (the
analytics scope is now in the list). The tracker then also uses watch time
and average view percentage.
