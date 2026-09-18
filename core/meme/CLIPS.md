# Meme clip finder

`clip_finder.py` sources the funny clips for the "<song> acapella" Shorts.
It searches YouTube, downloads a few videos, and cuts 0.6-2.5 s single-shot
segments with no audio, keeping the source aspect ratio.

## Workflow step (ubuntu-latest)

```yaml
- run: python3 -m pip install yt-dlp yt-dlp-ejs opencv-python-headless numpy --break-system-packages
- run: curl -fsSL https://deno.land/install.sh | sh && echo "$HOME/.deno/bin" >> $GITHUB_PATH
- uses: actions/cache@v4          # optional: skips the 12 MB model download
  with: { path: ~/.cache/clip_finder, key: clip-finder-models-v1 }
- run: python3 core/meme/clip_finder.py --out-dir work/clips --count 16 --theme mixed
       --used memory/meme-used.json --block memory/meme-blocked.json --cookies /tmp/yt_cookies.txt
```

The last stdout line is `{"ok": true, "count": N, "manifest": ".../clips.json", "stats": {...}}`.
The exit code is 1 when no clips were produced. It stops starting new work
around `--budget` seconds (default 330, so under 6 min on 2 cores).

## clips.json

A list in playback order (sources interleaved). Each entry has:

| field | meaning |
|---|---|
| `path` | absolute path of the mp4 (h264, no audio) |
| `source_id`, `source_channel`, `source_channel_id`, `source_title` | where it came from; record `source_id` in `--used` and `source_channel_id` in `--block` |
| `start`, `end` | seconds in the source video |
| `peak` | seconds from the clip start to its biggest motion/loudness hit: put the beat drop here |
| `score` | 0-1 ranking score (motion, spike, loud peak, face, most-replayed heatmap) |
| `has_face`, `motion` | face in most sampled frames; mean motion 0-1 |
| `width`, `height` | clip size (source aspect, at most 720p) |
| `content_box` | `[x0, y0, x1, y1]` (0-1) active picture, without static black or flat bars |
| `theme` | `asian` or `anime` |

## What gets skipped

- Title, description or tag hits for sexual or suggestive words (EN/ZH/JA/KO),
  known fanservice shows, Japanese game shows and idol groups, tragedy and
  politics, and non-clip formats (ASMR, prank calls, dance, AMVs).
- Age-restricted, live, over 4 min, under 20k views, used ids and blocked
  channels. Off-theme: South Asian or Arabic titles, and `asian` candidates
  with no China/Korea/Japan signal (keyword or CJK text in title, tags,
  channel or description).
- Frames: NudeNet flags exposed or swimwear-level body parts. 3 or more hits
  drop the whole source, and windows near a single hit are dropped. It is tuned
  to over-reject.
- Segments that are dark or mostly black canvas, blank, logo or title cards
  (plain background), static, covered in captions (more than 10 % text),
  blurry, or shorter than 0.6 s.
