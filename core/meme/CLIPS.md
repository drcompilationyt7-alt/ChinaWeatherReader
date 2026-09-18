# Meme clip finder

`clip_finder.py` sources the clips for the "<song> acapella" Shorts. A short
has 4 tiles, one per acapella layer. Each tile loops one clip of a vibe:

| layer | vibe | what it looks for |
|---|---|---|
| vocals | `dance` | K-pop dance practice, anime dance scenes, Douyin / Japanese dance trends |
| bass | `cool` | aura, walk-ins, stage presence, street skills |
| beatbox / drums | `fight` | anime fights, kung fu, karate, taekwondo, K-drama action (staged or sport only) |
| harmony | `cute` | idols being cute, pets, pandas, cute anime moments |

Every clip is a 4-6 s continuous moment (one shot when possible) around its
strongest hit. All clips come from different videos, and other channels are
preferred. Everything is Asia-related and matches `--theme`:

`funny`, `cute`, `anime`, `kpop`, `chinese`, `japanese` (not anime), or `mixed`.

## Workflow step (ubuntu-latest)

```yaml
- run: python3 -m pip install yt-dlp yt-dlp-ejs opencv-python-headless numpy --break-system-packages
- run: curl -fsSL https://deno.land/install.sh | sh && echo "$HOME/.deno/bin" >> $GITHUB_PATH
- uses: actions/cache@v4          # optional: skips the 12 MB model download
  with: { path: ~/.cache/clip_finder, key: clip-finder-models-v1 }
- run: python3 core/meme/clip_finder.py --out-dir work/clips --theme kpop
       --vibes dance:2,cool:2,fight:2,cute:2
       --used memory/meme-used.json --block memory/meme-blocked.json --cookies /tmp/yt_cookies.txt
```

The last stdout line is
`{"ok": true, "count": 8, "manifest": ".../clips.json", "vibes": {"dance": 2, ...}, "stats": {...}}`.
`ok` is false when some vibe got no clip. The exit code is 1 only when there
are no clips at all. It stops starting new work around `--budget` seconds
(default 330, which is under 6 min on 2 cores). The budget is shared across
the vibes.

## clips.json

A list grouped by vibe in `--vibes` order, best first within each vibe (the
first clip of a vibe is the one to use, the second is the spare).

| field | meaning |
|---|---|
| `path` | absolute path of the mp4 (h264, no audio, source aspect, at most 720p) |
| `vibe`, `theme` | which tile it is for, and the theme it matched |
| `duration` | 4.0-6.0 s |
| `peak` | seconds into the clip of its strongest hit: the punch for fight, the sharpest move for dance, the reaction for cute and cool. Put it on a beat. |
| `hits` | seconds of every motion impact in the clip (up to 12), for beat alignment |
| `hook` | 0-1: strength of the first 0.5 s (motion, face, contrast) |
| `score` | 0-1 ranking: vibe fit, hook, loop seam, views and view velocity |
| `loop` | 0-1: how closely the last frame matches the first (seamless loop) |
| `cuts` | shot changes inside the clip (0 is one continuous shot) |
| `has_face`, `has_caption`, `motion` | face in most frames, burned-in caption text, mean motion 0-1 |
| `width`, `height`, `content_box` | clip size; `[x0, y0, x1, y1]` (0-1) picture area without static bars |
| `source_id`, `source_channel`, `source_channel_id`, `source_title`, `views`, `start`, `end` | where it came from. Record `source_id` in `--used` and `source_channel_id` in `--block` |

## What gets skipped

- **Safety (unchanged from v1):**
  - Sexual or suggestive words in title, tags or description (EN/ZH/JA/KO).
  - Fanservice shows, Japanese game shows and idol groups.
  - Age-restricted videos.
  - A NudeNet check on frames: 3 or more hits drop the source, and windows near a single hit are dropped. It is tuned to over-reject.
- **Stricter additions in v2:**
  - "hot guy(s)/boy(s)", "crotch" and "groin" in titles.
  - Channel names are screened for NSFW terms.
- **Stricter per vibe:**
  - `dance` rejects kids, students, waist or body-roll moves and "sexy/hot dance".
  - `fight` rejects street fights ("real footage"), bullying, MMA/UFC, weapons and blood.
  - `cool` and `fight` also reject family uploads of school kids (sports days, elementary school).
- **Relevance:**
  - Dance, fight and cute sources must say so in their title, tags or description.
  - Every source must show a theme signal: show, group or place names, CJK text, or a native-script title from a theme search (kana for anime, Hangul for kpop).
  - Off-theme: South Asian or Arabic titles, and Western studios and cartoons (Kung Fu Panda, Disney, DreamWorks, SpongeBob...).
- **Other:** used ids, blocked channels, and videos that are live, over 4 min, under 30k views or low resolution.
- **Windows:**
  - dark or mostly black canvas
  - logo and title cards
  - still screenshots with a small moving inset
  - text covering more than 45 % of the frame (captions are fine)
  - blurry
