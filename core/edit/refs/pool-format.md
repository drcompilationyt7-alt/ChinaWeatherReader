# Vibe pools for the sound edit and the cinematic slow-mo edit (format v1)

Status: v1, 2026-10-12. Owner of this file: the new-types agent (presets / pools). The infra agent builds the monthly
refresh workflow from it. Changes to the format are appended at the end ("Changelog"); fields are only ever added.

Tool: `core/edit/vibe_pool.py` (CLI below). Everything here is plain JSON + media files, so a pool directory can be
tarred, cached (actions/cache) or uploaded as a release asset as one unit.

## 1. Layout

```
$EDIT_POOL_DIR/                       default: <repo>/output/edit-pools  (NOT committed: media is GBs)
  index.json                          {"version": 1, "updated": ISO, "pools": {"sound/impact": {...counts}, ...}}
  <preset>/<vibe>/                    preset = sound | slowmo ; vibe = see section 4
    vibe.json                         the vibe definition (queries, library args, songs, minimums) - copied from
                                      core/edit/vibes.json at build time (the committed source of truth)
    clips.json                        clip manifest (section 2)
    clips/<clip_id>.mp4               the cut clip (h264 crf 16, source fps, <= 1080p, AAC audio kept for sound)
    sounds.json                       song / sound-bite manifest (section 3)
    sounds/<sound_id>.m4a             the audio file (optional: if absent it is fetched by `query` at run time)
    sheets/                           contact sheets of the last build (QA only, may be deleted)
```

Used ledger (small, COMMITTED by the daily job next to memory/meme-history.json):

```
memory/edit-pool-used.json
{"version": 1,
 "items": {"<preset>/<vibe>/clip/<clip_id>":  {"used_at": ISO, "video": "<out file name>", "role": "short|long"},
           "<preset>/<vibe>/sound/<sound_id>": {"used_at": ISO, "video": "...", "role": "short|long"}},
 "moments": {"<source_id>": [[start_s, end_s, ISO], ...]}}   # a source moment shown once is never shown again
                                                               # (any clip overlapping it within 1 s is unusable)
```

The ledger, not the manifest, is the truth for "used" on CI (the pool directory is restored from a cache and may be
older than the ledger). `vibe_pool.py` merges both: an item is unusable if the ledger has it OR its manifest status is
not `fresh`.

## 2. clips.json

```json
{"version": 1, "preset": "slowmo", "vibe": "summer_glow", "built": "2026-10-12T15:00:00Z",
 "items": [{
   "id": "c_<source_id>_<start_ms>",            // stable: same source + start -> same id
   "file": "clips/c_<source_id>_<start_ms>.mp4", // relative to the vibe dir
   "source_id": "YouTube id", "source_title": "...", "source_channel": "...", "source_url": "https://youtu.be/...",
   "start": 12.34, "end": 16.10,                  // seconds in the source
   "moment": "<source_id>#<scene_index>",          // a scene key: one moment is used at most once, ever
   "duration": 3.76, "fps": 23.976, "width": 1920, "height": 1080, "has_audio": true,
   "subject": "female|male|group|none", "character": "name or null", "adult": true|false|null,
   "mood": "bright|warm|dreamy|intense|...", "tags": ["water", "hair", "closeup"],
   "scores": {"aesthetic": 0.71, "sharpness": 812, "motion": 0.34, "appeal": 0.6, "taste": 0.74, "luma": 128,
              "sat": 96, "face_ok": true},       // face_ok: no grey / green / washed faces (colour gate)
   "talk": false,                                 // mouth moving (a talking mouth without its voice is rejected)
   "safety": {"gate": "pass", "nudenet": 0.02, "siglip_unsafe": 0.03, "checked": "per-shot"},
   "sfx": [{"t": 1.21, "cls": "snare", "score": 0.82}],   // sound pool only: usable hit events (seconds in the file)
   "status": "fresh|used|retired|rejected",
   "added": "2026-10-12", "used_at": null, "used_in": null
 }]}
```

Rules every item already satisfies when it is in a manifest with status `fresh`:
- passed library.py's gates (no burned-in text, no dialogue scene, sharpness / aesthetic floors) and the per-shot safety
  gate (NudeNet every kept frame + SigLIP; no nudity / underwear / sexual framing; strict for minors / unknown age);
- passed the vibe's mood / subject rules (beauty vibes: female first, bright, never gloomy; aura vibes: any gender);
- `talk` false unless the clip's own audio is used under it (sound pool: a voice hit is allowed, max 1 per video);
- colour gate (`scores.face_ok`): skin not grey / green, not milky (black point and saturation floors per vibe).

## 3. sounds.json (songs and sound bites)

```json
{"version": 1, "items": [{
   "id": "s_shape_of_you",
   "title": "Shape of You", "artist": "Ed Sheeran",
   "query": "Ed Sheeran Shape of You official audio",        // how to fetch it (yt-dlp search) when the file is absent
   "source_id": "YouTube id or null", "file": "sounds/s_shape_of_you.m4a or null",
   "kind": "song|bite",                                     // bite = a short sound / effect bed
   "section": {"start": 0.27, "length": 36.0} | null,       // the part the vibe uses (null = the planner picks)
   "treatment": {"speed": 1.0, "pitch": 0},                 // slowmo songs may be "slowed": speed 0.95
   "bpm": 96, "fits": ["sound/crisp"],
   "licence": "commercial recording, not licensed to us: YouTube Content ID will claim it (ads revenue to the rights
               holder; a block in some regions is possible). Same practice as the reference edits.",
   "reuse": "song",                                         // song = a song may be reused after `cooldown_days`
   "cooldown_days": 21,
   "status": "fresh|used|retired", "used_at": null
 }]}
```

No lyrics are ever written to any pool file. Songs are NOT retired after one use (there are few that fit each vibe):
they go on a cooldown (`cooldown_days`, ledger `used_at`); clips ARE retired after one use.

## 4. Vibes (v1)

| pool | what | subject | songs (from what the presets already use) |
|---|---|---|---|
| `sound/impact` | gun cock / gunshot, sword clash / sheathe, punch / kick impacts, stomps (aura, crisp transient) | any (aura: male + female) | Shape of You, Fake (Tech Thieves), Wellerman (sea-shanty edit) |
| `sound/crisp` | claps, finger snaps, clicks (pen, chess, Go stone, lighter, coin), knocks, door / table slams | any | HandClap (Fitz & The Tantrums), Shape of You, Sempero / Time Back (phonk edit staples) |
| `slowmo/summer_glow` | bright water / beach / pool / sunlight, hair in the wind, warm golden light | female first (adult framing rules) | blue (yung kai, slowed 0.95), Masquerade (Siouxxie, slowed) |
| `slowmo/dreamy_bloom` | sakura / petals / night lights / snow, soft pastel close-ups, a turn, a smile | female first | blue (slowed), Masquerade (slowed) |

`core/edit/vibes.json` holds the exact queries and library arguments per vibe (committed).

## 5. How a run uses a pool (what the daily job does)

1. ENTRY POINT (implemented): `python core/edit/vibe_edit.py --type sound|slowmo --out <dir> [--landscape-long]
   [--vibe V] [--song SOUND_ID] [--seed N] [--no-mark] [--threads N]` (pool dir from `EDIT_POOL_DIR`; CPU only:
   it sets EDIT_GPU=0 unless the caller set it). A node wrapper (`node pipeline/edit-pipeline.js --type ... --out ...`)
   is the infra agent's call: it should just spawn this command (MEME_PYTHON) and read `<dir>/result.json`.
   picks a vibe with enough fresh items, a song not in cooldown, renders the 9:16 short (30-50 s) and, with
   `--landscape-long`, the 16:9 long (1.5-3 min, same vibe + song, more clips) and writes `<dir>/result.json`:
   `{"ok", "short": path, "long": path|null, "preset", "vibe", "song", "clips_used": [...ids], "sound_used": id,
     "duration_short", "duration_long", "render_s"}`.
2. The run marks every clip it put in either video as used in `memory/edit-pool-used.json` (role short / long) and the
   song's `used_at` (cooldown). Marking happens at render time (the long posts a week later but its clips are reserved
   now). `python core/edit/vibe_pool.py mark-used --result <dir>/result.json` does the same thing explicitly
   (idempotent) if the job wants to mark only after a successful upload.
3. The job commits `memory/edit-pool-used.json` with the other memory files.

## 6. Minimums (a vibe is "ready" when it has at least)

| pool | fresh clips (short + long in one run) | fresh sfx events | songs |
|---|---|---|---|
| sound/* | 70 | 160 usable hit events | 2 not in cooldown |
| slowmo/* | 70 | - | 1 not in cooldown |

`python core/edit/vibe_pool.py status [--json]` prints per pool: fresh / used / retired counts, runs left (fresh clips
/ clips per run), songs available, and `ready` true / false. A run fails fast (exit 2, `result.json` ok false,
reason "pool_low") when no vibe of the type is ready.

## 7. Refresh (what the monthly workflow should call)

```
python core/edit/vibe_pool.py refresh --preset sound|slowmo|all [--vibe V] [--budget-min 300] [--staging DIR]
python core/edit/vibe_pool.py validate --dir DIR          # exit 0 = safe to promote
python core/edit/vibe_pool.py promote --staging DIR       # staging -> live: new items + the live pool's FRESH items;
                                                          # used / retired items and their files are dropped
python core/edit/vibe_pool.py prune                        # delete files of used / retired items in the live pool
```

- `refresh` runs `core/edit/library.py` per vibe query (its own download pacing, cache `$EDIT_CACHE`), the per-shot safety
  gate, the colour / mood / subject gates, de-duplicates against the ledger's `moments` and the live pool, and writes a
  staging copy of the vibe dirs. It is resumable (re-running continues where it stopped) and time-boxed by `--budget-min`.
- CPU only is fine (SigLIP / NudeNet on CPU: ~30 s per source). Network: YouTube via yt-dlp (cookies optional,
  `YT_COOKIES`). A GitHub-hosted runner may be bot-blocked by YouTube; then refresh locally and upload the pool. The monthly workflow
  (`.github/workflows/edit-pool-refresh.yml`: plan -> 4 build shards -> swap) does this on the runner, see changelog v1.2.
- `validate` checks: every file exists and decodes, every item has safety.gate == pass, no moment twice, no item in the
  ledger, counts >= the section 6 minimums.

## Changelog
- v1 2026-10-12: first version.
- v1.1 2026-10-12: ledger `moments` is {source_id: [[start, end, ISO]]} (overlap test), entry point = core/edit/vibe_edit.py
  (exit 0 ok / 2 pool_low or no song / 1 failure); result.json keys: ok, type, preset, vibe, song, song_title, song_artist,
  short, long, duration_short, duration_long, clips_short, clips_long, clips_used, sound_used, render_s, licence, reason.
- v1.2 2026-10-12 (pool growth + GitHub refresh): sound clips carry an additive `vis` stamp written by
  `core/pools/pool_visible.py stamp` (it runs the production gate `sound_edit.vibe_events` = HPSS / transient events +
  `sound_visible.py` once per clip): `{"v": 1, "hit": bool, "hit_t", "cls", "sound_type": gun|blade|punch|break|object|other,
  "src": SigLIP source tag, "score": 0-1 visible-source score, "offset_f24", "prom", "area", "fit", "kind", "event_t",
  "windup_t", "voiced", "reason" (why a non-hit clip is a BED clip), "n_events", "sig" (5 x 16x9 grey picture signature,
  base64; same picture in two clips = duplicate), "checked"}`. HIT clip = `vis.hit`; BED clip = picture-only cuts (song-only
  shots). `vibe_pool.py status` reports `hits`. `vibe_pool.py refresh --shard K/N` takes every N-th query (CI matrix).
  `core/pools/pool_refresh.py` gains `shard` (refresh + stamp + pack), `merge`, `stamp`, `preflight`, `propose-baseline`; its
  `check` requires the stamp for new sound clips, drops same-picture duplicates, and fails a batch with too few new HIT clips
  (`POOL_MIN_NEW_HITS` 40), a hit share / median vis score more than 15 % under the baseline, or > 40 % voiced hits.
  The baseline (`core/edit/assets/pools-baseline.json`) also stores `hit_share`, `hits`, `voiced_share` and the `vis_score` stats.
