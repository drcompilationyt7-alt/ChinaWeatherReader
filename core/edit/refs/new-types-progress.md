# New types: SOUND EDIT + CINEMATIC SLOW-MO, production-ready (2026-10-12, owner deadline end of day)

Scope: make `sound_hit_sync` (sound edit) and `cinematic_slowmo` production-ready for @zeroyenotaku: 9:16 shorts 30-50 s,
a 16:9 landscape long (1.5-3 min, same vibe + song, more clips) posted a week later, CPU-only render on GitHub Actions,
vibe pools with used tracking, watermark, samples for the owner in Desktop/review-shorts/edits/new-types/.
Rules: presets stay type-specific (sound edit != others), per-shot safety gate stays, no lyrics in files, nothing
committed / pushed, block-system work on video A is paused (not touched). Pool format: refs/pool-format.md (infra agent
builds the monthly refresh from it).

## Layout of the work
- `core/edit/vibes.json` (new): vibe definitions (queries, library args, songs, licence note).
- `core/edit/vibe_pool.py` (new): pool build (library.py runs -> colour / moment gates -> pool), status, ledger,
  validate / promote / prune, mark-used.
- `core/edit/vibe_edit.py` (new): the production entry point (short + optional landscape long, watermark, result.json).
- sound preset: `core/edit/sound_edit.py` (render_vibe: 30-50 s short, 1.5-3 min long, CPU).
- slow-mo preset: `core/edit/cine_vibe.py` (new) around `planners.plan_cinematic_slowmo` + `engine.py` (CPU flow interp).

## Log
- 13:xx pool-format.md v1 written; vibes.json + vibe_pool.py written; songs registered (blue downloaded to
  output/edit-pools/_sounds); 4 background refresh runs started (one per vibe, 330 min budget, logs in
  output/edit-pools/_logs/).
- 11:30 vibe_pool fixes: library 'talk' / 'mouth_flap' are 0-1 floats (the first gate treated them as booleans and
  rejected 80 %); talking = mouth_flap >= .2 or talk / talk_mouth >= .45 or speech_ratio >= .3. Subject from the
  library's rules.female / male / group. Colour gate recalibrated on 40 beauty twixtor shots: hard reject only green
  casts (skin a* < -8), blown / grey frames (sat < 30, luma outside 50-235, black point > 90); hazy pastel shots are
  kept with scores.milky = True and MUST get the renderer's per-clip levels fix. Same moment never in two vibes of a
  preset (import checks the sibling vibes too).
- Pool yield is the bottleneck: the network library runs keep ~1-5 shots per query per ~20 min (downloads paced 15 s
  across 4 processes; gates reject most windows: other_edit / watermark sources, no_sfx, dark, gore, talking).
  Seeded from the local caches (library --pool-cache / --local --local-only, no network): slowmo 160 kept -> split
  by title / luma into the two vibes; sound 11 from the 24 local sound sources.
  Status 11:35: sound/impact 35 fresh (56 hit events), sound/crisp 10 (15), slowmo/summer_glow 55 (38 female),
  slowmo/dreamy_bloom 27 (15 female). None reaches the production minimum yet (70); the 4 network refreshes continue.


## Infra (daily rotation, long-form funnel, monthly refresh) - infra agent, 2026-10-12

Nothing pushed or committed. All of it is inert until the pools exist: with no ready pool the daily job posts the stem edit exactly as before.

### 1. Daily rotation
- `pipeline/daily-rotation.js` `dailyOrder()`: weighted random over stem / sound / slowmo (`DAILY_TYPE_WEIGHTS`, default `stem:1,sound:1,slowmo:1`; 0 disables; `DAILY_ROTATION=off` = stem only). Never the type of the last upload when another is eligible. A new type is eligible only when `vibe_pool.py status --json` has a ready vibe of that preset. The result is an ordered list; stem is always the last fallback.
- `core/github-action-runner.js` `runMeme()`: tries the order, new type via `pipeline/newtype-pipeline.js` (calls `core/edit/vibe_edit.py --type X --out DIR [--landscape-long]`, reads `result.json`), any failure (pool_low, render error, timeout 30 min) falls through to the next type and finally to the unchanged stem pipeline. Same 12:00 UTC `PUBLISH_AT`, same one-a-day guard (`memory/meme-history.json` `postedAt`).
- History/learners: new uploads are written to `memory/meme-history.json` with `contentType` `sound-edit` / `slowmo-edit` (+ `vibe`, `settings.pool`), logged with `appendDecision` and `recordMetadataChoice`; `pickEditType()` / the weekly report read `contentType` already. Stem rows have no field (read as `stem-edit`).
- Metadata optimiser (`core/metadata/catalog.js`): new pools `meme/slowmo-edit`, `meme/sound-long`, `meme/slowmo-long` (long = `long: true`, 100-char titles, description links the short); `meme/sound-edit` now works without a source anime ("anime sound design edit"). Aliases `slowmo`, `slow motion`. Keyword per vibe is set in `newtype-pipeline.js` `VIBE_KEYWORD`.
- Workflow `daily-create.yml`: env `DAILY_ROTATION`, `DAILY_TYPE_WEIGHTS` (repo variables override), step "Fetch the sound / slow-mo pools" (`scripts/edit-pools-store.js pull`, failure-tolerant), `memory/edit-pool-used.json` and `memory/longform-queue.json` added to "Save memory".

### 2. Long-form funnel
- Every sound/slowmo short is rendered with `--landscape-long` when the queue has room (`LONG_QUEUE_MAX=6`), in the same run. Storage = GitHub release asset (tag `longform-queue`): not in git, no 90-day artifact expiry, 2 GiB per file; deleted after upload/expiry. Queue = `memory/longform-queue.json`.
- `node core/github-action-runner.js --mode longform` (daily workflow step, every run, idempotent) uploads at most ONE long per run: due = short publish + 7 days at 16:00 UTC, scheduled via `publishAt` (YouTube treats 16:9 as a normal video), playlist "full edits", description links the short; then the short's description gets "Full-length version (date): URL" via `videos.update`. If several are due, the short with the best `lscore` goes first. Cadence caps: `LONG_MIN_GAP_DAYS=3`, `LONG_MAX_PER_WEEK=3`, expiry after `LONG_MAX_WAIT_DAYS=21`. `LONG_ENABLED=off` disables everything.
- Recommended cadence: one long every 3 days (about 2.3 a week, hard cap 3). Owner's 1-3 days is a valid range; set `LONG_MIN_GAP_DAYS=1`/`2` and `LONG_MAX_PER_WEEK` accordingly (repo variables).
- Sources: Metricool analysis of ~800k videos: per-video performance peaks at 2-4 long-form posts a week (https://metricool.com/youtube-trends/); Metricool guide: channels with a few Shorts feeding long-form got about 46% more views per long video, no gain above ~35 Shorts/month (https://metricool.com/how-to-grow-on-youtube/); vidIQ: 3+ videos a week gives stronger sustained growth, consistency matters most (https://vidiq.com/blog/post/How-Often-Post-on-Youtube); small channels: one quality Short a day beats flooding (https://subscribr.ai/p/how-often-to-post-youtube-shorts). Anime/AMV example: Future AMV's about 3.25 uploads a week (VidIQ snapshot, https://vidiq.com/youtube-stats/channel/UCToaMhG2077evtP92WaLE3g). Mostly marketing blogs and estimate tools: treat as heuristics, check Studio analytics after a month.

### 3. Monthly refresh (written, DISABLED)
- `.github/workflows/edit-pool-refresh.yml`: runs only when repo variable `EDIT_POOL_REFRESH=on`; first Tuesday of the month 05:00 UTC; no Kaggle (CPU). Steps: pull live pools -> `core/pools/pool_refresh.py run` (`vibe_pool.py refresh --staging` -> baseline check -> `vibe_pool.py validate` -> `promote`) -> push to the release only on success. Report artifact either way.
- `core/pools/pool_refresh.py`: owner-approved quality BASELINE (`core/edit/assets/pools-baseline.json`, created by `python core/pools/pool_refresh.py baseline --approve` once the owner approves the samples; no baseline = nothing ever promotes). `check` drops staged items below the baseline p10 (aesthetic / sharpness / motion / appeal / taste), failed safety gate, colour gate, duplicate ids, missing files; fails when batch medians are >15% below the baseline, fewer than 20 new items, or new + unused live clips < minimum or < 80% of the live pool. promote (vibe_pool.py) = new + FRESH live items; used retire.
- Pool storage: `scripts/edit-pools-store.js pull|push` (release `edit-pools`, one tar per vibe, index.json). The first upload must be done by the owner/agent after the first local build: `node scripts/edit-pools-store.js push` (gh login).
- Stem-edit song top-up, FULLY AUTOMATIC (owner change): `.github/workflows/song-topup.yml` is ENABLED (off only with repo variable `SONG_TOPUP=off`), first Tuesday 05:30 UTC, CPU, no Kaggle. `scripts/song-topup.js` -> `pipeline/song-topup.js`: the LLM proposes ~25 candidates; each is auto-approved only if ALL gates pass, otherwise rejected with the reason:
  1. niche: ONLY K-pop, Chinese (C-pop / Mandopop / viral / C-drama OST; theme `chinese`, source = the artist's official channel, a C-drama or a donghua) and J-pop / anime. An anime source needs an AniList entry (`core/quiz/assets/anime-list.json`) and must not be NSFW-flagged; groups / dramas / Chinese animation not in the lists must prove themselves through gate 2 (official channel + views). Everything else is rejected;
  2. popular: official upload found via yt-dlp metadata search with >= `TOPUP_MIN_VIEWS` (1M) views AND a trend signal: >= `TOPUP_MIN_TREND` (2) acapella / edit / amv uploads titled with the song in 60 days, or >= `TOPUP_MIN_SUSTAINED` (6) in 180 days, or an official upload < `TOPUP_NEW_DAYS` (120) days old (relevance search + date filter: yt-dlp's date-sorted search returns nothing);
  3. clip QUALITY (search-only metadata dry run, then a light sample): weighted sources >= `TOPUP_MIN_CLIPS` (12) where 1080p+ counts 1, 720p counts 0.5, below that 0, x1.25 for creditless / NCOP / NCED / 4K / Blu-ray / official or verified channels; titles, descriptions and tags with sub / eng sub / lyrics / AMV / edit / mirrored / cropped / zoomed / reaction / shorts / nightcore etc. are dropped. Then 10 s low-res previews (`core/pools/source_probe.py`, yt-dlp `--download-sections`) of the best 3: burned-in subtitles (`core/meme/edit_fx.text_bands`), static watermark / logo, borders, sharpness (p85 block Laplacian, library.local_sharpness copied because library.py has heavy imports); >= 2 of 3 must be clean, a preview that cannot be downloaded counts as not clean. The song audio must exist (official upload 60-420 s). The optional 20 s demucs check is NOT implemented;
  4. not a duplicate: not a name or alias of a curated / approved / retired song, never region-blocked (>= 30 countries) or listed in `config/song-blocklist.json`, not rejected before;
  5. limits: `TOPUP_MAX_PER_MONTH` (10) per month (also across reruns). Approved songs go to `config/song-candidates.json` `approved` with `probation: true`; `meme-pipeline.js` loads them into SONGS and `pickSong` gives probation songs a -0.15 handicap until they have 2 scored uploads (Thompson sampling does the rest). Auto-added songs with >= 4 scored uploads whose mean lscore is 0.5 below the channel mean are RETIRED (moved to `retired`, no longer picked). Curated songs are never retired.
  Every verdict (all gates, with the reason) is written to `memory/song-topup-YYYY-MM.json`; both files are committed by the workflow. Thresholds are env-tunable (`TOPUP_*`); the 1M view bar is a first guess, tune it against the existing list.

### 4. Tests (all local, all pass)
- `node scripts/longform-selftest.js`: rotation (weights, no repeat, empty pool -> stem, off -> stem, stem last), 90-day cadence simulation (gap >= 3 d, <= 3/week, queue <= 6, 2.2 longs/week), enqueue + upload + two-way link + asset delete with a local store and fake YouTube.
- `python scripts/edit-pool-refresh-selftest.py`: no baseline -> no promote; drop rules; weak batch fails; too few new; ordered validate/promote on success; live pool byte-identical after failures.
- `python scripts/source-probe-selftest.py`: the probe's detectors on synthetic frames and a generated subtitled mp4 (sharp, soft, borders, logo, burned-in text, end to end).
- `node scripts/song-topup-selftest.js` (offline, fake YouTube search and previews): every gate rejects what it should (low views, no trend, few HD sources, 480p-only, subtitled / edit sources, dirty previews, Chinese pass, non-niche theme, NSFW show, no AniList entry, duplicate, alias, blocked, wrong theme), a good song is approved on probation, the report has a reason per candidate, the 10 a month limit holds across reruns, only a real underperformer with enough uploads is retired.
- Fake-CLI integration of `runNewType` / `recordUpload` / `enqueue` done once; the real `vibe_edit.py` path is untested end-to-end until it exists (contract = section 5 of pool-format.md; adapt `newtype-pipeline.js` if `result.json` keys differ).

### 5. To go live
1. Pools: finish the local builds, then `node scripts/edit-pools-store.js push` (creates the release `edit-pools`). Until then the rotation posts stem only.
2. The CI python step must install what `vibe_edit.py` needs (nudenet/onnxruntime/siglip etc.; the refresh workflow lists a guess) and the render must fit the CPU runner (30 min short, 75 min with long).
3. Owner approves samples -> `python core/pools/pool_refresh.py baseline --approve` -> commit `pools-baseline.json`; then set repo variable `EDIT_POOL_REFRESH=on` (and `SONG_TOPUP=on` if wanted).
4. Push the code (nothing is pushed yet). Songs in the sound/slowmo pools are commercial recordings (Content ID claims expected, see pool-format.md): owner call.
5. Optional repo variables: `DAILY_TYPE_WEIGHTS`, `DAILY_ROTATION`, `LONG_*`, `TOPUP_*`, `SONG_TOPUP=off` (the song top-up is on by default; it also needs the Gemini / OpenRouter secrets, already used by the daily job).

## Sound edit (sound agent)
- 10:40 `core/edit/sound_edit.py` extended (nothing else touched): `render_vibe(song, shots, out, *, aspect, length, seed,
  workdir, theme, title, threads=None)`; `plan_sound_edit(..., length_range=(lo, hi), lufs=None, rate=None)` = range
  mode: one continuous whole-bar section of the song (8- / 4-bar phrase lengths, prefers a section start), the riff
  pattern re-found every 4 bars (`select_hits_span`), quiet stretches > 0.75 s filled with beat-grid 'song only' shots
  (`fill_silent`, clip audio muted, no voice clips there; the refs have 46 % song-only cuts), per-source caps scaled
  with length, pool-fit (`fit_hits_to_pool`: drop whole bars to the range minimum, then thin weak off-pattern hits,
  else TooFewEvents -> 'pool_low'), no loop-end clip (it would show a scene twice), master at the references'
  loudness (`reference_lufs`: -7.3 LUFS ref median, capped at the song section + 0.5 LU, TP <= -1.2 after AAC).
- Renderer: >16 shots render in groups of 16 to lossless x264 qp0 intermediates (split at hard cuts only), then ONE
  final libx264 encode (+ rotation / letterbox + audio); `colour_fix` = mild per-clip black point / saturation repair.
- Song-side test (5 songs, 36 s / 110 s sections): hits on the window onsets (median 0.3-7 ms), gaps <= 0.73 s,
  2.0-2.4 hits/s. Pool demand: ~2.2 distinct scenes per second of video (Short ~80, long 90 s ~200).
- Test clips: pools empty at 10:35, so library.py --local-only (full gates) runs on the 22 cached sound sources
  (edit-cache/sound_src) into scratchpad/sound/lib_local.

## Cinematic slow-mo (cine agent)

2026-10-12 10:25 start. Entry point `core/edit/cine_vibe.py` `render_vibe(song, shots, out, *, aspect, length, seed, workdir,
song_speed, title)`: slowed song (song_fx.make_version, pitch follows) -> music.py analysis (cached) ->
planners.plan_cinematic_slowmo with `cine_body_s` / `cine_intro_s` / `cine_loose` -> engine.py in a subprocess.
No lyrics: the planner runs with no song name (offline), the intro words are the `title` words (lyric_text.attach with
explicit non-lyric words); no title = no intro text. Watermark off (vibe_edit adds it). Music only.
- planners.py (cinematic functions only): body length generalised (`cine_body_s`, was a fixed ~10 s); new
  `cine_body_layout` for bodies > 12 pulses: phrases [hero hold 4 / 3] + single pulses (move / still m s m m s), phrase
  starts on the song's section starts, the loudest section's hero = the LIFT (bigger bloom bump), loose 2-pulse shots
  in the quiet sections of the 16:9 long, last shot 2-3 pulses into the song fade; long-body cuts re-anchor on the
  tracked beats (no drift); the per-slot reference targets (luma / sat) map hero / move / still / last onto the
  reference's six slots; show variety by recency (no show in the last 4 shots) instead of "one show per video".
- first plan on the test pools: 42 min on CPU, almost all of it first-time per-clip QA caches (SigLIP portrait QA,
  mood, mouth, interp, people). The test pools hold the same clip in several folders: dedupe on source_id + start.
- 11:45 first full short (test pools, 'blue' @0.95, seed 1, 9:16 rot90 like _sX2, 30 fps): engine 7.0 min on 4 cores with
  EDIT_GPU=0 (29.8 s video, 2.2 fps render). Full-fps QA (scratch cine/qa_cine.py): 0 duplicate frames and 0 judder
  frames in 773 body frames (DIS optical-flow in-betweens, footage.py); motion p50 / p75 / p90 0.120 / 0.191 / 0.389
  %W/frame, near-still 9 %; cuts on the song's accents (engine: p50 53 ms before the hit, 100 % within 150 ms);
  -9.3 LUFS / -2.3 dBTP vs the reference -6.9 LUFS. Problems found: only 14 of 108 test clips pass the gates (the
  soft fallback had put talking / soft / gore / phone takes on screen); a Kaguya O. frame (villain 0.30, fierce 0.59);
  milky shots (sat 29-35); the title word was re-triggered (stutter).
- fixes: `CINE_HARD` rejects (talking, lipflap, gore, fierce, phone, soft, stepped, ghost, roll, twoshot, gag, male lead,
  panels, group, eye macro, wide + grey sat < 25 + library rules villain >= 0.28 / gloomy or creepy >= 0.3 + cine mood
  fierce >= 0.5) are never shown; cine_vibe probes the pool first (`cine_probe`: clean count), fits the length to it
  and merges single pulses into 2-pulse shots (`cine_max_shots`) before shortening; pool_low below 12 clean (short) /
  24 (long). golden_soft guard sat_ref .26 -> .28, sat_hi 1.55 -> 1.9 (milky clips); master -7.5 LUFS / ceiling -1.8
  (`cine_lufs`, `cine_ceiling`); title words hold to the next word (no max_hold re-trigger).

### Song top-up preview (2026-10-12)
Real local run with the full gates: `C:/Users/Home/Desktop/review-shorts/edits/new-types/song-suggestions.md` (26 candidates, 2 passed: sheesh, iris out). Not written to the live list. The local Gemini quota was exhausted and the OpenRouter free models were busy, so the candidates were hand-listed in the LLM's output shape; needed secret for the monthly run: GEMINI_API_KEY (or OPENROUTER_API_KEY), already set as repo secrets.
- 11:50 sound agent, round 2 (all in sound_edit.py):
  - HPSS hits: a hit whose raw level never drops 12 dB (it sits on the clip's BGM) is re-measured on its percussive
    layer; if that is a clean transient (attack <= 110 ms, decay < 700 ms, rise >= 9 dB) the hit is mixed from that
    layer (`perc`). Usable events on the library's own sound clips 4/20 -> 12/20; the measured percussive hits match
    the refs' anatomy (rise 11-22 dB, decay 75-380 ms).
  - 'silent_only' events (not_crisp / impact_off: visible action, muddy sound) fill only the song-only shots;
    pool-fit mutes weak audible hits when crisp scenes run short (>= 55 % audible). `nothing_moves` relaxed for
    continuous action (prominence >= 1.0 and mean frame diff >= 2.5).
  - `is_talking`: library writes talk / mouth_flap as 0-1 scores; vibe_pool's import turns them into a bool that is
    True for ~every clip (bool(0.006)). render_vibe reads the raw scores (mouth_flap >= 0.3, talk >= 0.45,
    rules.talk_mouth >= 0.7). Suggest vibe_pool store talk = score >= 0.45.
  - Loudness: refs are -5.8..-9.9 LUFS but clip (+1.5..+4.8 dBTP). Sweep at TP <= -1: hit over music / song gain
    change at cuts = +6.0 / 0.76 dB at -14 (mix +10), +4.2 / 1.4 at -12 (reached -13.7), +1.2 / 1.9 at -7.8 (pumping).
    Vibe master = -13.5 LUFS, hit target +9.5 dB -> hits +5.5..6.1 dB (refs 3/5/7), 20 ms RMS p95-p50 8-9 dB (FB15
    7.7-9.3). AAC overshoot fixed locally (10 ms windows) instead of turning the whole video down (a 90 s long had
    lost 3.6 dB).
  - Small pool (lead): short falls back to >= 19.5 s (ok True, reason 'pool_low_shortened: ...'); long targets the
    top of its range and shrinks to the pool, floor 45 s.
  - CPU (EDIT_GPU=0, libx264, ffmpeg -threads 4): 35 s short / 83 shots 338 s wall; 90 s long / 190 shots 598 s
    (plan 131, render 404 of which final encode 270). Real 20 s shorts 145-220 s.
  - Real-clip samples (pool impact+crisp + local library clips): scratchpad/sound/real_shape_of_you.mp4,
    real_fake.mp4 (20 s, pool-limited). Pool clip c_rYXih2N84QE_61200 shows blood (gore gate miss).
- 12:00 real vibe pools (slowmo/summer_glow + dreamy_bloom, 90 fresh clips): only 6 passed the hard gates. Contact sheet
  of the pool with the gate numbers (scratch cine/pool_a.jpg / pool_b.jpg): the 'soft' gate (lap 450 / lap_r 700, tuned
  on 4K twixtor packs) rejected crisp flat-shaded 1080p clips (Frieren flower crown lap_r 590). Hard 'soft' is now
  lap_r < 260 (or < 400 with lap < 120); the old thresholds stay a soft penalty. villain >= 0.35, gloomy / creepy >= 0.42.
  Added hard: face_h < 0.2, sat > 165 (neon / paint frames), frozen (portrait drift < 0.006 or native flow < 0.015),
  too_fast (native flow > 0.6, full-body action). Long bodies enter where the take's own motion starts (a clip held
  one frame for 1.3 s: 23 repeated frames in a still slot).
- 12:20 short on the real pools (s4, 36.6 s, 23 shots, 'blue' @0.95): engine 13.4 min on 4 cores (other jobs were
  running), plan 1.7 min warm; colour sat median 86 (was 60), p2 median 31; -8.5 LUFS / -1.5 dBTP (target raised to
  -6.8 so the master lands near the reference's -6.9); 33 duplicate frames found in 2 shots -> the frozen fixes above.
- 13:10 first 16:9 long (L1: Masquerade @0.9, test + vibe pools, 43 clean of 211, 91.0 s, 45 shots): engine 36.2 min on
  4 cores (EDIT_GPU=0), 51 min with the cold per-clip QA of ~100 new clips. Found: (1) the window ran into the song's
  quiet outro (14 s at -40..-50 dB under the picture) -> cine_window scores out windows whose body has quiet beats
  (beat_loud_db < median - 12) and long bodies may start on any 8th beat; (2) AAC true peak +1.5 dBTP from a wav at
  -1.8 dBFS: the song window starts at full level on frame 0 (encoder overshoot on the onset) -> 30 ms fade-in,
  ceiling -2.3, and `_tp_guard` re-encodes the audio (video copied) if the delivered true peak is over -1.0;
  (3) an unchecked clip filled the last slot when clean clips ran out -> the layout shrinks the body to the clean count
  (render_vibe returns pool_low when the video would end under the minimum length). Dup 5 / judder 1 in 2612 frames.
- 13:30 short s5 on the vibe pools only (18 clean of 96, 30.8 s, 18 shots): 0 duplicate / 0 judder frames, motion
  p50 / p75 / p90 0.106 / 0.207 / 0.404, sat median 66 (ref body ~70), p2 median 28, -8.3 LUFS / -1.4 dBTP; engine
  13.1 min on 4 cores (the 4 pool-building processes share the CPU), plan 8 min (cold QA of the newly imported clips).
- intro audio: no lyric timing = no gate; the intro now plays 7 dB under the body with a shallow 4 dB / 0.12 s breath and
  +3 dB on the hero hit (song_fx gap_drop with gate -4 dB, no riser).
- `precompute(shots)` (sidecars .flow30 / .portrait / .cinemood / .mouth / .interp / .people .json) and
  `gate(shots)` (per-clip hard rejects, pool-independent) added to cine_vibe.py for the pool build.
- 13:50 sample (s5: 'blue' @0.95, summer_glow + dreamy_bloom pools, 30.8 s, 18 shots, 9:16 rot90 like _sX2; rendered
  before the reviewed-only pool filter, the intro duck and the 2.2x entry cap; a re-render with all fixes follows).
SAMPLE READY: C:/Users/Home/Desktop/review-shorts/edits/new-types/slowmo_1_summer-glow_blue_31s.mp4

### Infra state at PAUSE (2026-10-12, coordinator pause: owner's PC struggling; no local downloads / probes / python until told to resume)
- DONE and self-tested (node scripts/longform-selftest.js, node scripts/song-topup-selftest.js, python scripts/edit-pool-refresh-selftest.py, python scripts/source-probe-selftest.py all passed before the pause): rotation, long-form queue, pool refresh wrapper (disabled), song top-up with the owner's changes:
  - gate 3 now counts clip quality at SOURCE level (4 queries per source: "<anime> 4K", creditless/NCOP/NCED, scenes, PV; "<group> stage 4K", "MV 4K", live performance, variety; drama equivalents), not only footage of the song;
  - gate 2 also passes an evergreen hit: official upload >= 100M views (`TOPUP_EVERGREEN_VIEWS`) on popularity alone (still probation + Thompson retirement);
  - resource limits for previews (`pipeline/song-topup.js probeSource`): one probe at a time (promise chain), IDLE priority (os.setPriority LOW; children inherit), 2 threads (OMP/PROBE_THREADS, cv2.setNumThreads, ffmpeg -threads 2), started only when free RAM >= 4 GB and CPU < 60 % (`PROBE_MIN_FREE_GB`, `PROBE_MAX_CPU`, waits up to `PROBE_WAIT_MIN`=30 min, then the song counts as "not probed" = not clean).
- NOT DONE: the re-run of the 26-candidate preview with these changes (its results would update `C:/Users/Home/Desktop/review-shorts/edits/new-types/song-suggestions.md`, which still shows the previous run: 2 pass of 26). The run was killed at the pause; no output was kept. To resume: `ONLY` unset, `node <scratchpad>/real-run2.js <out.json>` (driver is in the session scratchpad, candidates = the same 26 hand-listed songs; LLM free tiers were rate-limited), then regenerate the md with mkmd.js. About 30-60 min, one probe at a time. Do not run it until the coordinator says resume.

## Round 3 (new-types agent, 2026-10-12 afternoon; owner feedback: vertical sound short, fewer voices, no repeats, lyrics on slow-mo, pool growth)
- Gore clip c_rYXih2N84QE_61200: rejected in both sound pools, media deleted. NEW committed blocklist
  `core/edit/assets/pool-blocklist.json` (39 safety-rejected source moments, 27 sources): import + fresh_clips skip any
  overlapping moment forever (survives promote); `vibe_pool.py block-sync`, and gore-check --apply syncs it.
- Voice ratio (owner: too much voice). Same metric both sides (harmonic share 30-180 ms after the hit, HPSS 150-4000 Hz,
  output/newtypes/ref_voice.py): refs' clip-sound layer 10 % of hits >= 0.5 (6.5 % >= 0.6; Time Back excluded, other
  master); our 13:20 sample 38 % / 25 %. sound_edit.vibe_events now keeps harmonic hits audible only within a budget
  (SOUND_VOICED_SHARE 0.05 of the audible hits: 4 in a short), the rest become picture-only (action) or are dropped
  (voice). Re-render s3: 12 % / 8 % (refs 10 / 6.5).
- No repeats: one shot per pool clip (scene key = moment key), clips showing the same picture (7-frame 32x18 grey
  signature corr > 0.9, e.g. two uploads of one scene) or overlapping source ranges share a key; scene-repeat penalty
  1.5 -> 6.0 in assign / repair (a repeat now costs more than a tight fit).
- Vertical: core/edit/reframe.py (16:9 master -> 9:16 full-height crop per shot, face / action tracked, no bars) now
  caps pan speed at 0.12 W/s (REFRAME_PAN_MAX): short shots are almost static crops (dance refs: static 9:16 crop),
  longer ones drift; cv2 threads capped.
- Pool: picture-only fill (SOUND_PICTURE_FILL): a clean clip with no usable hit gives one song-only shot (refs: 46 %
  song-only cuts); SILENT_OK += nothing_moves, no_room_after (quality, not safety). Long official sources tried
  (creditless OP/ED, trailers, fight compilations; scratch build_batch.sh): yield ~0-1 shot per OP (fast cuts < 2 s,
  watermark / broadcast logo) and fight compilations / official clips carry burned-in subtitles (source dropped):
  not a viable route today.
- Slow-mo text = the SONG'S LYRICS (owner): cine_vibe fetches lyrics at render time (title cleaned of "(slowed)"),
  planner online (vocal-gap window), lyric_text 'cinematic_slowmo' words-on-black intro (the _sX2 style), no lyrics
  or no sung words in the window = re-plan with NO text. LYRICS_NO_STORE=1 (lyrics.py / lyric_check.py): no lyric
  records / ASR words / word timings read from or written to disk; the plan.json on disk is redacted after the engine
  ran. Sound edit refs show no text (sound.md) -> no lyrics there.
- 16:55 SOUND SAMPLE DELIVERED: new-types/sound_2_impact_shape-of-you_vertical_30s.mp4 (output/newtypes/s8, seed 41,
  Shape of You). 30 s 9:16, 49 shots, 1.6 cuts/s, voiced 1/24 audible (4 %), repeats 0, -13.5 LUFS / -1.4 dBTP,
  render 416 s + watermark 55 s on 4 threads (IDLE). Changes since round-3 notes: voiced budget 0.03 (2 per short);
  fit_hits_to_pool mutes again after thinning + audible floor 0.5 (SOUND_AUDIBLE_MIN; refs 54 %) so the pool's 29
  crisp scenes fill a full 30 s (was 20 s); repair() 40 rounds + a HARD no-repeat pass (cheapest unused scene, a
  tight fit holds a few frames). Safety review of all 185 impact clips (pool_review sheets): 10 more rejected
  (blood / possible blood: Levi, Asuna, Choso x3, Rengoku, Eren, Castlevania, Hashirama, Mitsuri) -> blocklist.
- 17:50 SOUND_3 DELIVERED (owner: sound_2 lost the hits - muting voiced slots was wrong; clip sound must run
  through the whole video): output/newtypes/s10 -> new-types/sound_3_impact_shape-of-you_vertical_30s.mp4.
  Voiced hits now costed (VOICED_COST 0.9, cap 35 % of hits) not muted; non-hit shots play their own audio as a bed
  (CLIP_BED_DB -3 under the music); impact_off / nothing_moves events audible (AUDIBLE_RELAXED); audible floor 0.9.
  New plan field sound_edit.clip_layer (present %, longest clip silence, hit gaps). Refs residual measure
  (scratch snd/ref_gaps.py): clip layer present 87-100 %, strong hits 0.65-2.58/s, gap max 1.07-4.17 s.
  Ours: 57/63 audible, 1.87/s, present 85 %, longest silence 0.8 s, gap max 1.5 s, onset |6| ms (79 % < 40 ms),
  peak +5.0 dB, voiced 29 % (refs 10 %; pool-limited: 43 clean action moments). Pool +26 clips from 21 local action
  sources (library --local, min-sharp 250, aesthetic 0.5), 14 rejected on review -> impact 219 fresh.
- 18:15 SLOW-MO memory: the planning (lyrics Demucs / wav2vec / faster-whisper medium + SigLIP / face QA) holds
  ~2.9 GB and the engine 2.0-3.4 GB, so plan + engine in one process tree broke the 3 GB budget (3 killed renders).
  cine_vibe.render_vibe_isolated (vibe_edit uses it): planning in a child process that exits, then run_engine() in
  the parent; engine.py EDIT_PREFETCH (shots decoded ahead, default 2; 0 locally). Engine alone still peaks ~3.4 GB
  on a 9:16 slow-mo (work frames 1920x1080 per take): OPEN ITEM. Sample render with a 3.7 GB cap (free RAM > 6 GB).
  Lyrics: blue (yung kai) found, 7 sung words on the words-on-black intro (lyric_text cinematic_slowmo = the _sX2
  style: magenta neon, smear-in, wiggle). Pool: 11 of 59 summer_glow clips pass the hard gates -> 29.8 s.
- 18:45 SLOW-MO LYRICS SAMPLE DELIVERED: new-types/slowmo_2_summer-glow_blue_lyrics_30s.mp4 (output/newtypes/c7, seed 7).
  Intro words = sung lyrics (UNIVERSE / WILL / ALWAYS / BRING / ME TO / YOU), _sX2 neon style, rot90 like the ref.
  -9.0 LUFS / -1.8 dBTP. Engine memory: whole 60 fps takes decoded at 1080p -> 3.4 GB peak on the last shots even at
  1 CPU / EDIT_PREFETCH=0; rendered via engine --start/--end segments (0-9.4, 9.4-19.1, 19.1-25.9, 25.9-29.83, hard
  cuts) + concat + full audio.wav mux + _tp_guard + watermark. OPEN: decode-window cap in footage.py (or decode
  60 fps takes at 30) so one process stays < 3 GB; GH runners have the RAM, so CI is not blocked by it.
- 19:10 LONG DELIVERED: new-types/sound_long_impact_shape-of-you_16x9_90s.mp4 (output/newtypes/L2): 90 s 16:9, 144
  hits, 86 audible (+ beds), audible floor 0.6 for longs, beds spread (no mute next to a mute): hit gap max 18.8 ->
  5.0 s, clip silence max 1.7 s, onset |6| ms, +4.5 dB, -13.6 LUFS. Render 492 s + watermark 163 s (4 threads).
  Pool songs copied into each vibe's sounds/ (they ship in the edit-pools release; vibes.json 'local' paths are
  owner-PC only and are skipped on CI).
## READINESS (19:10)
- Commit (core/edit is untracked; what these presets import): core/edit/{vibe_edit,vibe_pool,pool_review,sound_edit,
  reframe,cine_vibe,watermark,engine,planners,footage,library,lyrics,lyric_text,lyric_check,text,text_fx,text_sound,
  styles,layouts,transitions,music,song_fx,sfx,portrait,matte,interp_qa,clip_audio,clip_rules,clip_taste,camera3d,
  flow_s2rz,plates_s2rz,fxsets,gpu,handover,hype_i4f,photo_phone,presets}.py, core/edit/vibes.json,
  core/edit/assets/** (fonts, brand, pool-blocklist.json), core/edit/refs/{sound_timing.py,sound.json,fx.json,
  edit-roster.json,exemplars.json,exemplars.npz,attract_head.npz,blur_flow_s2rz_template.json,motion3d-av.json,
  hype_i4f_template.json,cine_ghost.json,pool-format.md,new-types-progress.md}. core/meme deps already tracked.
- OPEN: engine memory on 9:16 slow-mo (3.4 GB; local renders in segments), voiced share 29 % (pool-limited),
  slow-mo pools 11 clean (summer_glow) / 7 (dreamy_bloom) -> not enough for a week of distinct slow-mo posts,
  sound/crisp 18 clips (not ready), monthly refresh on GitHub untested end to end (YouTube bot blocks, subtitles).

## OWNER UPDATE 2026-10-12 (evening): BLOOD / GORE ALLOWED in the sound + slow-mo edits (sound agent, sound_4 round)
- library.py: `GORE_OK_PRESETS = {sound_hit_sync, cinematic_slowmo}` -> `ALLOW_GORE` (set from --preset; env
  EDIT_ALLOW_GORE=1 forces it): the 'gore' shot reject is skipped, the gore score is still measured / logged.
- planners.py: `CINE_ALLOW_GORE = True` -> the slow-mo 'gore' hard reject is off (the 'fierce' mood gate and the soft
  gore score penalty in the ballad ranking stay).
- vibe_pool.py: UNSAFE_WORDS no longer has blood / gore / injury / wound (no auto-blocklisting); `gore-check` only flags
  (`safety.gore_flag`), never rejects (GORE_IS_SAFETY False); new blocklist rows carry a category.
- core/edit/assets/pool-blocklist.json -> v2: `moments` rows are [start, end, reason, category]; 55 gore-only rows moved to
  `released` (kept for the record, not read); 3 kept: QG-lpeQC9k4 41.1 (shirtless: sexual_content), 7HMp8KC9dq8 99.2
  (object in mouth: sexual_content), 7HMp8KC9dq8 13.9 (Vinland Saga: wrong_show). Backup of v1: %TEMP%/pool-blocklist.before-gore-release.json.
- Pool: 58 clips had been rejected for blood only; 15 re-cut from the edit-cache sources (status fresh, `released` note,
  gore_flag) -> sound/impact 221 -> 236 fresh; the other 43 have no cached source: the next refresh may import them again.
- Other gates unchanged: nudity / sexual (NudeNet + SigLIP), minors strict, hentai sources, subtitles / burned-in text.

## SOUND_4: VISIBLE SOURCE (sound agent, 2026-10-12 evening; owner: "I need to know WHAT IS MAKING THE SOUND")
- Python for this work: %TEMP%/acapvenv/Scripts/python.exe (cv2 / torch / librosa / transformers); the system pythons lack cv2.
- NEW core/edit/sound_visible.py: measure(path, t, box, t_min, t_max) -> residual motion (Farneback, camera pan removed
  + frame diff; holds = still), local brightening (sparks / muzzle flashes), event frame within +-2 f@24 of the transient
  (onset over the 3 frames before / contact / spark / shot-relative), wind-up start, action bbox + area, best 9:16
  window (focus_x, fit), FX-flash flag; tags() = SigLIP (clip_judge model, CPU) source classes gun / reload / sword /
  fist / kick / clap / object / impact / glass / mask / crack / chain / metal vs face / eyes / scenery / fx; gate() and
  score(). Cached per (file, t, range) in %TEMP%/sound-visible.
- NEW core/edit/refs/sound_visible_study.py (ref or our render + plan): per hit measures + tags + gate, strips with the
  hit frame (red), action box (green), 9:16 window (blue).
- FB15 by eye: 8 / 10 hits show their source (crystal crushed in a fist, earring shatters, bullet hits a can, match
  strike, clawed hand flexes, cracker bite, muzzle flash (wide), blade swing (medium)); 2 do not (#11 FX flash over a
  mouth, #16 face reacting). Motion gate v5: 9 / 10 pass (#11 rejected, #16 passes on motion -> the face tag must reject it).
- sound_edit.py: visible_pass() in vibe_events (SOUND_VISIBLE=1, SOUND_VIS_TAGS=1): failing hits -> picture-only
  (vis_reject); passing hits get preroll >= (hit - wind-up) + 2 f@24 (SOUND_VIS_LEAD) so the cut shows the cause, score
  += 0.12 x visible score; voiced hits may pass on a face (shout) but stay budgeted. Plan shots carry focus_x / visible / vis_src.
- reframe.py: hit shots with focus_x are held centred on the sound source (if the face/action track is > 0.6 half-window off).

## PUSH PACKAGE (prepared 2026-10-12 ~20:15 by the sound agent; NOTHING committed / pushed; coordinator runs dry run + push after the owner approves sound_4)
Files (git add exactly these; the quiz / pop files in the working tree belong to other agents and are NOT part of it):
- core/edit/{vibe_edit,vibe_pool,pool_review,sound_edit,sound_visible,reframe,cine_vibe,watermark,engine,planners,footage,
  library,lyrics,lyric_text,lyric_check,text,text_fx,text_sound,styles,layouts,transitions,music,song_fx,sfx,portrait,matte,
  interp_qa,clip_audio,clip_rules,clip_taste,camera3d,flow_s2rz,plates_s2rz,fxsets,gpu,handover,hype_i4f,photo_phone,presets}.py
  (import closure checked: no other core/edit module is imported by these)
- core/edit/vibes.json, core/edit/assets/** (brand, fonts, pool-blocklist.json v2; 4.7 MB)
- core/edit/refs/{sound_timing.py,sound.json,fx.json,edit-roster.json,exemplars.json,exemplars.npz,attract_head.npz,
  blur_flow_s2rz_template.json,motion3d-av.json,hype_i4f_template.json,cine_ghost.json,pool-format.md,new-types-progress.md,
  sound-visible-source.md,sound_visible_study.py}
- infra: .github/workflows/daily-create.yml, .github/workflows/edit-pool-refresh.yml, .github/workflows/song-topup.yml,
  core/github-action-runner.js, core/metadata/catalog.js, core/pools/**, pipeline/{daily-rotation,edit-pipeline,longform-queue,
  newtype-pipeline,song-topup}.js, pipeline/meme-pipeline.js (probation songs), scripts/{edit-pools-store.js,song-topup.js,
  longform-selftest.js,song-topup-selftest.js,edit-pool-refresh-selftest.py,source-probe-selftest.py}
- NOT needed: core/meme/edit_fx.py working-tree change (+1185 lines, another agent): the sound path only uses
  edit_fx.text_bands, which is in HEAD. pop-pool-refresh.yml / pop-* / kaggle / quiz assets: quiz agent's.
CI deps (daily-create.yml, meme mode): torch + torchaudio from the CPU index (demucs needs a matching torchaudio),
  + scipy, onnxruntime (slow-mo / matte later); SigLIP (transformers + sentencepiece) already there; model cache key
  meme-models-v1 -> v2 so the SigLIP weights (~800 MB HF cache) and the anime-face onnx (~/.cache/clip_finder) get saved.
Rotation: DAILY_TYPE_WEIGHTS default 'stem:1,sound:1,slowmo:0' (workflow AND pipeline/daily-rotation.js default);
  longform-selftest passes.
Pool upload (ready, NOT run): `node scripts/edit-pools-store.js push` (needs gh login; creates/updates release edit-pools
  with one tar per vibe + index.json from output/edit-pools).
Sound path runtime on CI adds the visible-source pass: ~0.3 s / event motion + SigLIP tags (cached per clip in
  %TEMP%/sound-visible; on CI cold every run: ~160 events x ~0.5 s = ~1.5 min). SOUND_VIS_TAGS=0 skips tags if needed.

### OWNER 2026-10-12 evening: SLOW-MO short format (queued: start AFTER the owner approves the sound edit)
- The slow-mo SHORT must NOT be the rotated landscape video (no rot90; nothing sideways).
- The short is the same edit as the long, but upright: clips and words the right way up, filling the 9:16 frame, with a subject-tracking crop that keeps only the part of each clip holding the main subject (the same reframe approach as the sound edit, eased pans, no bars).
- The LONG version keeps the full landscape clips (16:9).
- The text / lyrics are laid out for 9:16 (upright, safe margins) in the short.
- 20:18 FIRST sound_4 render (output/newtypes/s11, seed 41, Shape of You, SOUND_AUDIBLE_MIN=0.5): 30 s, 56 shots, 28 audible.
  BY EYE on the final 9:16: only ~7 / 28 hits clearly show the source (~6 borderline, ~15 not: fire / water / energy FX,
  faces, dark smears). NOT delivered. Causes: (1) the voiced-hit face exemption let faces through (removed); (2) a weak
  SigLIP margin (0-1.6) passed FX / faces -> gate needs margin >= 1.9 AND source logit >= -3.8 ('source_unclear'),
  measured on the 28 hits (all 6 clear sources pass, 13 of 17 bad ones fail); (3) dark regions ('dark': region luma
  < 0.16 without a spark, or frame luma < 0.12); (4) 5 hits had 0-30 ms pre-roll (cut ON the hit: the cause is never
  seen) -> plan mutes audible hits with pre-roll < 55 ms (SOUND_VIS_PRE_MIN).
  Motion false negatives: continuous fight motion (Sword of the Stranger, Greed vs Wrath clashes, prom 1.1-1.7) -> a
  STRONG tag (margin >= 4, source logit >= -2.5) lowers the motion floor to 1.0.
  Pool after the stricter gate: 28 visible hit events in 23 clips (of 253) -> far too few (a short needs ~29 audible at
  floor 0.5; a week ~200). Pool growth (grow.py driver in session scratch, 10 new queries: Lycoris Recoil, Black Lagoon,
  Sword of the Stranger, glass shatter, Bleach mask, Cowboy Bebop, Spy x Family, FMA:B, Trigun Stampede, Demon Slayer
  mask) running 20:40-21:55 (log output/newtypes/grow_vis.log); the full refresh was stopped (it re-ran old queries first).
