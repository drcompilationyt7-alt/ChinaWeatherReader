# Quiz new formats (silhouette / eyes / outfit): handoff

## 2026-10-11 strict list update (owner review, via coordinator)
Tatsumaki and Fenrys REMOVED from assets/appearance-overrides.json (petite adults: full adult rules). Strict list now: Senko, Rory Mercury,
Charmy Pappitson, Milim Nava, Yachiru Kusajishi (normal art only). Items already built for those two under the strict rules stay as they are;
their adult-rule art (swimwear / outfit) appears when the models / topup phases next process them (clear cache so:<id>:v2 / theme:*:<id> to force).

## SESSION 2026-10-11 (continue) - LOCAL BUILD CHAIN + NEW SOURCES + ANIMATED BACKGROUNDS
Scratchpad = ...\claude\c--Users-Home-Desktop-ChinaWeatherReader\c0dd8c36-cb0d-48fb-93cb-4f90a6450d45\scratchpad. NO Kaggle / runner; nothing committed.
CHAIN (started 13:50, all mine, acapvenv, POP_MIN_FREE_GB=2 auto-pause): `chain3.sh` = beach2.sh (rebuild, resumes) -> finish_theme beach ->
 glasses (selector, 60 min) -> finish_theme glasses -> run1010b.sh halloween (-> models -> finish + villains + theme merges + regate).
 `chain4.sh` waits for "== models batch 1:" in run1010.log, sets HOLD, waits for that batch, runs `--phase topup --minutes 120`
 (official promo / game art second pass, see below), then removes HOLD so models continue. Check: `grep -E "^==|BEACH|GLASSES|topup" run1010.log | tail`.
 Samples queue: `samples_q.sh` (waits for >= 3.6 GB free RAM; log samples_q.log): anim-regular (trivia seed 5) + anim-beach (duel, beach, seed 22),
 both `--ci-voice --anim-bg`, into review-shorts/quiz-new/sample-anim-*.mp4 (+ qs/<name>.check / .asr).
NEW IMAGE SOURCES (owner 2026-10-10 evening #2; NO figurines): core/quiz/pop_fanart.py
 - Danbooru `<tag> official_art` (PROMO) and `<tag> game_cg` (GAME: gacha / event versions) posts, OFFICIAL_MIN_SCORE 5 (promo art is rarely voted);
   every other filter unchanged (colour, solo, one character tag, resolution >= 1000, age tags, safety tags, adult-only swimwear, on-model, WD14 gate).
   `_clean` stores srctype PROMO / GAME / FAN ART (from the post tags); `_licence()` words the credit per type.
 - `candidates(official=True)` puts official / game art before fan art (all new base builds use it); `theme_candidates` also queries official_art / game_cg
   (kept only when the post carries a theme tag). Cache keys dbofficial1_<tag>:... (existing fan caches untouched).
 - `pop_extras.build_topup` / `--phase topup [--scopes base,beach,halloween]`: second pass over characters whose silhouette / outfit / themed art was
   DROPPED, official + game art only, replaces the dropped entry, then finish_v2 / finish_theme. Keys topup1:<scope>:<fmt>:<id>. Thin pools first
   (adult women first, popular first).
 - scripts/pop-gate-sheets.py labels the source: PROMO / GAME / FAN ART / WIKI / ANILIST (AI when AI art exists).
ANIMATED BACKGROUNDS (owner 2026-10-10 evening #1): core/quiz/bg_anim.py (new), hooks in render_pop.py (gradient_bg(dots_on), edition_deco(animated),
 PopRenderer.render -> bg_loop), `render_quiz.py --anim-bg` (env POP_ANIM_BG=1; OFF by default so the live channel is unchanged).
 - Regular: spinning globe of LED dots (orthographic, 18 deg tilt, 2273 land dots at 20 px pitch from countries-50m.json, limb fade), 360 deg per 24 s,
   frame N == frame 0 (720 frames at 30 fps); same accent / strength as the old map (0.14). Themes add periodic motion (integer cycles per 24 s):
   beach waves + glints + breathing sun glow, halloween fog + bats (moon / pumpkins static), winter snow, festival lanterns + far fireworks,
   hearts (maid / valentine) + sparkles, gym pulsing stripes (6 s), villains smoke (needs themes.json deco "smoke"), glasses lens glints (deco "glints").
 - Pre-rendered ONCE per (palette, deco) as mp4 (crf 12, 5-8 MB, 60-120 s on CPU) in ~/.cache/pop_quiz_bg (POP_BG_CACHE); the renderer streams it
   with ffmpeg (sequential reads; jumps restart ffmpeg). Bump bg_anim.VERSION after any look change. CI: cache that dir with actions/cache or accept ~1-2 min.

## PARKED 2026-10-10 14:40 (owner leaving) - EXACT STATE + RESTART (read this first)
All my builds STOPPED (only my PIDs); every cache kept (~/.cache/pop_quiz_models/extras-cache.json, assets/pop2, scratchpad/imgcache).
Nothing committed / pushed; no remote branch or workflow run exists. Formats / themes / AI lines stay behind flags.
POOLS (character-extras.json, unchanged): eyes 955, silhouette 46, outfit 42; beach 18 / 7 / 2 (OLD merge; the rebuild below is not merged yet);
 halloween character 2 (282 / 2172 cached); villains character 72 / silhouette 3 / eyes 32; glasses not built.
BEACH REBUILD (loosened filters, approved appearance rules): 159 characters processed of 540, art for 24 (character 24, silhouette 8, outfit 2 so
 far; cache theme:beach:*, funnel in cache funnel:beach). Funnel so far (159 chars): 108 have a Danbooru tag; candidates after post filters:
 character 66 / silhouette 30 / outfit 21 chars; biggest image-level drops: on-model ("closer to a castmate", "no clear face to verify
 the design", hair / eye colour) ~60-70 %, then not distinctive (silhouette), gate (sensitive / underwear tags), pose not full standing.
 => the on-model check, not score / framing, is the main limit. Ideas for the owner: AI-generated art (stricter on-model), more reference
 pictures per character (wiki gallery) so "no clear face" drops fall.
RESTART (one model process at a time; acapvenv; the driver does halloween -> models -> finish -> villains -> theme merges -> regate):
 1. Beach: `nohup bash <scratchpad>/beach2.sh &` (resumes; scratchpad = ...\claude\c--Users-Home-Desktop-ChinaWeatherReader\c0dd8c36-cb0d-48fb-93cb-4f90a6450d45\scratchpad).
    When it prints BEACH2 DONE: `python -c "import sys; sys.path.insert(0,'core/quiz'); import pop_extras as pe; pe.finish_theme('beach')"`.
 2. `rm <scratchpad>/HOLD; nohup bash <scratchpad>/run1010b.sh halloween &` (HOLD file exists: remove it) ; later `--phase theme --theme glasses`.
 3. Then `python scripts/pop-gate-sheets.py` (POP_IMG_CACHE=<scratchpad>/imgcache), samples with `%TEMP%/ttsvenv/Scripts/python.exe core/quiz/render_quiz.py --ci-voice ...`.
 Back off below ~2 GB free RAM (POP_MIN_FREE_GB=2 pauses by itself while the edit render runs).
SHARD SUPPORT (kept, unused): `build-pop-media.py --newformats2 --shard i/N` (POP_SHARD), POP_DB_GAP, scripts/pop-merge-shards.py,
 scripts/pop-pool-sizes.py, .github/workflows/pop-pool-build.yml (uncommitted; branch quiz-build deleted).
TODAY'S DECISIONS (owner via coordinator, 2026-10-10):
 - Strict (child-bodied) list APPROVED: Senko, Rory Mercury, Charmy Pappitson, Tatsumaki, Milim Nava, Yachiru Kusajishi, Fenrys. Nobody is
   flagged automatically; a confirmed adult gets the full adult rules (the 36 auto flags are removed). Frieren stays adult-modest.
 - Canonical petite / flat_chest: allowed on adult swimwear posts when the OFFICIAL art (WD14 on AniList portrait + wiki infobox, cache
   canon:<id>) shows it too; rejected only when the fan art draws them flatter / smaller than canon. loli / shota / child / aged_down /
   younger stay rejected for everyone (posts and pictures).
 - AI-generated art ALLOWED if GOOD and VERY close to the character: Danbooru ai-generated tag no longer a reject (NOT IMPLEMENTED yet),
   stricter on-model threshold for AI posts + WD14 artefact tags (bad_hands, extra_fingers ...). Own generation PILOT (Illustrious / NoobAI-XL
   class, licence check, RTX 3060 only while no edit render runs, 10-20 images, adults only for swimwear, marked AI-GENERATED, owner judges
   on a sheet) = PENDING, not started.
 - GitHub runner plan (branch quiz-build) CANCELLED by the owner: do not push. Build locally.
 - Beach Day loosening (quality / framing only) and funnel instrumentation are in the code (see the session notes below).
 - Theme priorities: 1) Glasses + Villains (villains built, glasses queued), 2) HALLOWEEN before Oct 15: trivia / wyr / emoji / duel first
   (themed LLM questions, duel cards normal art + Halloween deco), character / silhouette art where the pool allows, one Halloween trivia or
   duel sample, 3) then Summer Festival, Maid Cafe, Gym, Winter pools in batches.
 - Still owed: gate sheets + strict list sheet, Halloween character sample, Beach Day outfit sample (needs >= 5 outfit items; else report
   funnel), outfit / silhouette sample re-renders after the bigger pools are merged.


## SESSION 2026-10-10 afternoon (coordinator messages) - STATE + QUEUE
Scratchpad = ...\claude\c--Users-Home-Desktop-ChinaWeatherReader\c0dd8c36-cb0d-48fb-93cb-4f90a6450d45\scratchpad (run1010.log).
RUNNING (background, started by me): beach2.sh (Beach Day rebuild with the loosened filters, 30-min batches, pauses < 2 GB free RAM
because the edit render uses most of it), then chain2.sh waits for "BEACH2 DONE", removes scratchpad/HOLD so run1010b.sh continues
halloween -> models (1992 chars, silhouette / outfit) -> finish (merge, villains, theme merges, regate) -> then chain2 builds
glasses (WD14 selector). Check `grep -E "^theme|^==" run1010.log | tail`.
BEFORE the Beach Day rebuild: character 18 / silhouette 7 / outfit 2 (backups scratchpad/bak1010/*before-beach2*).
Beach Day loosening (quality / framing ONLY, no safety / age rule touched; themes.json art + pop_fanart.theme_candidates):
 min_score 10 (was 20; >= 50 / >= 20 still sorted first), 10 query tags (swimsuit, beach, bikini, one-piece_swimsuit, sarong, innertube,
 beach_umbrella, summer, swim_trunks, pool) + full_body scan, 200 posts each, up to 16 candidates per adult, official swimsuit /
 summer costume character tags (costume_tags, cache dbalt:), loose_framing (character: any framing; silhouette: full_body or a
 standing figure; outfit: full_body or only the feet cut off), WD14 theme confirm also swim_trunks / swim_briefs / hawaiian_shirt.
 Adult men are in (age rule only) for character / silhouette; outfit stays adult women. Cache keys dbtheme4_10:.
FUNNEL: pop_fanart.STATS counts every post drop by reason (rating, artist / paywall, low-quality tag, score, resolution, not solo,
 other / several characters, age tag, not-them, safety tag, framing) and build_theme stores per theme cache['funnel:<tid>']
 {chars, with_tag, with_candidates{fmt}, passed{fmt}, images{fmt:{reason:n}}, posts{reason:n}}: print it after the build.
APPEARANCE RULE CHANGE (owner 2026-10-10): the 36 auto "looks young" strict flags and their heuristic (Danbooru loli / shota share,
 rule_class) are REMOVED. A confirmed adult gets the full adult rules. ONLY exception = the explicit hand list in
 assets/appearance-overrides.json: Senko, Rory Mercury, Charmy Pappitson (owner) + Tatsumaki, Milim Nava, Yachiru Kusajishi, Fenrys
 (all 7 APPROVED by the owner; strict-flags-sheet-N.jpg = this list). Frieren stays adult-modest.
 Kept: per-picture WD14 loli / shota / child / aged_down >= 0.15 -> strict for that picture; Danbooru post rejects (loli, shota, child,
 aged_down, younger ...); petite / flat_chest on adults' swimwear posts ONLY when the character's OFFICIAL art (WD14 on AniList portrait + wiki infobox, cache canon:<id>) does not show it too (owner: canonical build is fine, reject only fan art that alters the character; pop_extras.canon_young / pf.CANON_FN; caches keyed with a 'c' suffix when canonical tags exist). Cache entries of the freed characters and the
 age-rule skips were cleared (re-built by the running builds). Villains theme built: character 72 / silhouette 3 / eyes 32.
QUEUE (coordinator, not started yet): (1) Beach Day OUTFIT sample with narrator + AI theme lines -> review-shorts/quiz-new/
 sample-beach-outfit.mp4 (needs >= 5 outfit items; otherwise report the funnel); (2) glasses (queued), villains done;
 (3) HALLOWEEN window opens Oct 15: trivia / wyr / emoji / duel first (themed LLM questions, duel cards normal art + Halloween deco),
 character / silhouette art where the pool allows, one Halloween trivia / duel sample; then festival / maid / gym / winter pools;
 (4) AI art pilot: Danbooru ai-generated tag allowed with a STRICTER on-model threshold + WD14 artefact tags (bad_hands,
 extra_fingers ...); own generation (Illustrious / NoobAI-XL class model, licence check, RTX 3060 only while no edit render runs,
 10-20 images, adults only for swimwear, child-bodied never, marked AI-GENERATED with settings, owner judges on a sheet first).
 (5) Gate sheets + strict list sheet + Halloween character sample (--ci-voice) + outfit / silhouette re-renders after the merge.


## PARKED 2026-10-10 ~12:35 (coordinator: save credits) - exact state
All my processes stopped (driver run1010b.sh + the halloween batch); caches kept (extras-cache.json 20151 keys, scratchpad/imgcache,
backup of caches / pools in scratchpad/bak1010). Nothing committed / pushed; flags unchanged. selftest 13/13, node --check OK.
POOLS NOW (character-extras.json): eyes 955 (DONE, every character), silhouette 46, outfit 42; beach character 18 / silhouette 7 /
outfit 2 (DONE, all 548 adults); halloween IN PROGRESS: 282 of 2172 cache entries done, 2 with character art not yet merged
(pool shows 0 until finish_theme), yield very low (Halloween fan art of a character passing score / res / solo / on-model is rare).
Samples done (review-shorts/quiz-new): sample-eyes.mp4 (re-render, OK), sample-beach-duel.mp4 (OK), sample-beach-character-ai-lines.mp4
(OK). Not done: Halloween character sample (pool empty so far), Beach Day outfit sample (pool 2 < 5: the duel replaces it).
NOT DONE: gate sheets in quiz-new not regenerated yet (script ready, tested into scratchpad/sheets_test incl. strict-flags-sheet-1..3);
models phase (1992 characters, ~8-10 h, silhouette only for non-adult-women); villains / glasses selector themes; regate; Kaggle
measurement SKIPPED (~/.kaggle exists but has no access_token).
RESUME: `nohup bash <scratchpad>/run1010b.sh halloween &` (prep_halloween.done exists, so it continues where it stopped; then models,
finish, villains, theme merges, regate). Then `python scripts/pop-gate-sheets.py` (POP_IMG_CACHE=<scratchpad>/imgcache), a Halloween
character sample if the pool reaches >= 5 (`bash <scratchpad>/qs/sample.sh halloween-character --format character --edition halloween --seed N`).
Owner review notes: Yachiru Kusajishi (age_class adult from the role "Lieutenant") and Fenrys look like children: keep strict.
Open: eyes-format music bed ~3 dB louder than in the character format (voice ~2 dB over the unducked bed; ASR hears every line).

## SESSION 2026-10-10 (in progress)
- Driver: scratchpad/run1010b.sh (eyes -> beach -> halloween -> models -> finish + villains + theme merges + regate), acapvenv,
  POP_THREADS=2, POP_MIN_FREE_GB=2, POP_IMG_CACHE=scratchpad/imgcache (pictures downloaded once, reused across formats);
  log scratchpad/run1010.log; `touch scratchpad/HOLD` pauses between batches (sample renders), `touch scratchpad/STOP2` ends it.
  Restart at a phase: `nohup bash <scratchpad>/run1010b.sh <eyes|beach|halloween|models|finish> &` (waits for a running build).
  scratchpad/prep_theme.py <beach|halloween> clears theme cache entries before the rebuild (only while no build runs).
  Order note: themes run before the long models tail (1992 non-adult-women characters, silhouette only, ~8-10 h) because the
  samples need the Beach Day / Halloween pools and Beach Day for adults is the owner's priority.
- Beach Day yield analysis (before): 532 adults processed, 160 adult women; 99 women had 0 clean themed posts, 171 of 532 had
  no Danbooru tag, and the 100 newest posts per query were mostly too low-scored / not full-body.
  Changes (pop_fanart.py): theme queries add `score:>=20` (Danbooru does not count score: / rating: in the 2-tag limit; checked),
  200 posts per query, the 60 best kept (cache key dbtheme3:); `cowboy_shot` is now recorded as framing; themes.json `art.query_tags`
  (beach 2, halloween 3 = halloween, halloween_costume, witch_hat) and `art.fan_limit_adult` (beach 12 candidates for adults,
  default 6). find_tag: 5 autocomplete matches instead of 3, then qualified tags `<name>_(<show>)` and the given name alone
  (e.g. tsunade_(naruto), fubuki_(one-punch_man) were missed); empty tags are retried once (dbtag2: marker); the show check on
  the tag's posts is unchanged. No gate / age rule changed.
- EYES phase finished 10:52: every usable character processed, eyes pool 721 -> 955 (official art only). finish also gave
  silhouette 46 / outfit 42 (the models batch from 2026-10-09 evening had not been merged).
- sample-eyes.mp4 RE-RENDERED (seed 13, --ci-voice): chatterbox 7/7, -14 LUFS, 36.3 s; rounds Itachi / Rintaro Okabe / Fern /
  Kenzo Tenma / Hinata Shintani (level 5). Frames (24 random / s) clean: header, eye card, timer, reveal pair + name pill +
  show; qcheck "out of frame / Shorts zone" hits are only the portrait card's 0.3 s slide-in from the right edge (rests at
  x <= 987). ASR hears every line on time ("Fern!" heard as "Burn", Japanese names approximated by base.en). Narrator is
  ~2 dB over the UNDUCKED music bed in the gaps; the music ducks ~8 dB under the voice (sound.mix), same mix as the live channel.
- BEACH DAY rebuilt (deeper queries + wider tag search + a `<tag> full_body rating:s score:>=20` scan for silhouette / outfit,
  themes.json art.framing_query): character 18, silhouette 7, outfit 2 (was 19 / 5 / 1; Hifumi's group poster now rejected).
  The limit is the data, not the filters: e.g. Kurisu has 80 full-body rating:s posts with score >= 20, 3 of them swimwear
  (1 not solo, 1 soles, 1 < 1000 px); most adult women's swimsuit art is cowboy-shot / upper-body (fine for character / duel
  cards, not for silhouette / outfit), and the on-model check drops many. Base pools hold only 2 outfit / 5 silhouette swimwear
  items. Beach Day outfit stays < 5 -> the sample is the Beach Day duel (character pool).
- sample-beach-duel.mp4 (seed 22, --ci-voice): chatterbox 10/10, -14 LUFS, 41.4 s, all 10 lines heard on time; beach palette +
  sun / waves deco, themed questions (surfer / sandcastle / volleyball), themed art on the cards with faces in frame.
  qcheck flags = the existing (live) duel layout: timer badge between the cards, right card edge at x ~1035 (into the right
  margin / Shorts button column at y 1150-1290); not changed (live format). qcheck.py fixed (crashed when the outro ends past
  the last audio sample).
- sample-beach-character-ai-lines.mp4 (Beach Day character, seed 31 plan + AI lines from pipeline/pop-narrator-ai.js via
  scratchpad/qs/ai_plan.js, Gemini, nothing recorded as used; rendered with --plan-file --ci-voice): chatterbox 7/7, -13.9 LUFS,
  32.7 s. aiLines: intro + outro AI main ("Beach day edition! Can you spot who is catching waves?" / "How did you do? Drop your
  score below!"), reveals 1 / 2 / 4 AI short ("It is {a}!", "Say hello to {a}!", "Definitely {a}!"), reveals 3 / 5 fell back to
  the fixed pool on the timing budget. 0 rejected by validation. All lines heard on time (re-checked round 3 on its own window);
  blur reveal + beach deco clean in the frame sheets.
- Themed character art now needs ONE figure (WD14 solo, no multiple_* / 2girls ...): a wiki "Hifumi, Aoba and ..." swimsuit poster
  had got into Beach Day. regate_all (--phase regate) now also re-checks theme pools (theme age rule, one figure, gate).
- scripts/pop-gate-sheets.py: labels are cut to the tile width (they ran into the next tile);
  also writes strict-flags-sheet-N.jpg: every AUTO strict appearance flag, numbered, AniList portrait +
  wiki infobox art, age class + evidence, Danbooru loli / shota share, pools it is in, a decision line for the owner.

## PARKED 2026-10-09 ~21:15 (owner left) - state + next steps
All processes stopped; every cache kept (~/.cache/pop_quiz_models/extras-cache.json, assets/pop2, pop2/theme, appearance
overrides). Nothing committed / pushed. Formats + themes + AI lines all OFF behind flags (POP_NEW_FORMATS=1, POP_THEMES,
POP_AI_LINES=1); the refresh workflow is off (needs repo var POP_POOL_REFRESH=on). Repo compiles (node --check, py_compile),
metadata test 81/81, `python scripts/pool-refresh-selftest.py` 13/13.
Pools now (character-extras.json): silhouette 27, eyes 721, outfit 23; theme beach: character 19, silhouette 5, outfit 1;
theme halloween 0 (old query method; rebuild with the tag queries); villains / others not built.
Still to process: eyes 584 characters, models (silhouette / outfit) 1992 characters (adult women first is done).
Samples in C:/Users/Home/Desktop/review-shorts/quiz-new/: sample-character / -silhouette / -outfit (OK, narrator 7/7, layout
clean); sample-eyes.mp4 = the seed-12 re-render that was stopped mid-check: RE-RENDER it (seed 11 take had a TTS artefact
"Go, easy" on reveal 1 and an obscure name "Hiling"). Gate sheets there are from mid-build: regenerate.
NEXT (in order, one model process at a time, acapvenv, POP_MIN_FREE_GB=2, POP_THREADS=2):
 1. scratchpad/theme_run3.sh did: halloween (tag queries) -> scratchpad/redo_beach.py + beach redo (framing fix) -> villains
    -> pool_run.sh (models first, then eyes). Restart: `nohup bash <scratchpad>/theme_run3.sh &` (it waits for no other build).
    Or by hand: `--phase theme --theme halloween|beach|villains --minutes N`, then `--phase models` / `--phase eyes` batches,
    `--phase finish`, `--phase regate`.
 2. Samples with --ci-voice via scratchpad/qs/qrender.py + qcheck.py + asr.py: eyes (re-render), Beach Day outfit
    (`--edition beach`; the beach outfit pool is 1, so Beach Day character / silhouette if outfit stays < 5), Halloween character
    (`--format character --edition halloween`), a themed duel (`--format duel --edition beach`), one themed render with AI
    lines (plan with ai_lines from pipeline/pop-narrator-ai.js, rendered via --plan-file).
 3. Gate sheets: `python scripts/pop-gate-sheets.py` (base + all theme pools; labels age class, override, source, fan-art
    score / resolution / on-model scores, SWIMWEAR / BARE FEET / BARE LEGS / THEME).
 4. Measure compute per 100 characters offline (prefetch 100, then models with POP_OFFLINE=1) to size the Kaggle sessions.
 5. Report: list every appearance-override character (owner 6 + auto 29, below) for the owner to review.

## OWNER RULES (binding, collected 2026-10-09)
- Content / age: confirmed adults (age_class adult): swimsuit / bikini / bare legs / feet / suggestive official art allowed and
  preferred for outfit / silhouette. Always rejected: WD14 questionable (> 0.20) / explicit (> 0.10), nudity, underwear /
  lingerie / pantyshot, see-through, sex acts, isAdult / hentai sources (+ topless_male, no_pants, cameltoe, covered_nipples,
  foot_focus / soles, reverse bunnysuit). Minors / unknown: strict (general only, no swimwear / suggestive). Never change age_class.
- Appearance overrides = explicit list assets/appearance-overrides.json (no hidden body heuristics). Owner: Kobeni + Shinobu
  adult, Frieren adult-modest (swimwear + bare legs / feet, no suggestive poses), Senko / Rory Mercury / Charmy strict (still in
  silhouette / eyes / character, no outfit). Auto entries (Danbooru loli / shota share >= 1%) = strict, PENDING owner review:
  Tatsumaki, Milim Nava, Akane Kinoshita, Alice Lendrott, Erika Furudo, Stephanie Dola, Tamayo, Fenrys, Yachiru Kusajishi,
  Celia Claire, Mirellia Q Melromarc, Shino Kuribayashi, Gran Mamare, Arataka Reigen, Kakashi Hatake, Loid Forger, Naofumi
  Iwatani, Zeke, Yamato, Tatsuhiro Sato, Kai Chisaki, Tadaomi Karasuma, Felix Argyle, King Bradley, Biscuit Krueger, Rokuro
  Okajima, Gouhin, Gaku Yashiro, Nyanta (many are adult men with canonical child versions: likely to be flipped to adult).
  Per picture: WD14 loli / shota / child / aged_down >= 0.15 -> that picture is gated strict.
- Colour art only (monochrome / greyscale / lineart / comic rejected; colourfulness gate).
- Fan art (Danbooru, approved): rating:g / rating:s queries only (s only for adult rules), never q / e; solo, exactly one
  character tag, no cosplay / genderswap / crossover / alternate hair or eye colour / chibi / parody / multiple views / text;
  costume = the character's own (alternate costume only as adults' real swimwear, or when the costume IS the theme); quality:
  score >= 20 (>= 50 first), short side >= 1000 px, no sketch / lineart / unfinished / lowres / jpeg_artifacts /
  traditional_media / 3d / photorealistic / ai-generated; skip banned_artist / paywalled sources; apparent-age tags rejected
  (loli, shota, child, aged_down, younger, kindergarten, randoseru, toddler; school uniforms for adults); 2 requests / s.
  Recognisable = on-model vs the ANIME look (pop_onmodel.py: margin over castmates >= 0.05, hair >= 0.35, eye >= 0.20, face
  required; when unsure official art wins). Credit "N. Art: <artist> <post>" per round in the description.
- EYES = official art only (AniList portrait / wiki), never fan art.
- Themes (core/quiz/themes.json, all formats incl. existing ones): rotation seasonal first in its window, else ~1 in 4, never
  the same theme twice in a row; behind POP_NEW_FORMATS + POP_THEMES; titles lead with the edition label.
- AI narrator lines (POP_AI_LINES=1): flavour only, never names / facts; {a} templates; validated; timed against the slots;
  Gemini -> OpenRouter -> fixed pools; 60-day no-repeat memory.
- Monthly refresh: first Wednesday (cron every Wednesday + day <= 7 check), Kaggle Mon-Thu only (never Fri-Sun: the other
  project's weekend), <= ~3 GPU h / month, sessions <= 120 min, resumable, CPU fallback; token only from secret
  KAGGLE_API_TOKEN (never in files / logs); safe swap (stage -> validate -> promote new + unused old, used retire; failure keeps
  the old pool; LRU reuse >= 14 days as the worst case); quality baseline set only when the owner approves
  (`pool_refresh.py baseline --approve`), until then nothing promotes. Write but don't enable / push.

Nothing is committed. All new formats are off unless `POP_NEW_FORMATS=1` (pipeline/world-quiz-pipeline.js `POP_FORMATS`,
pipeline/pop-quiz-content.js `NEW_FORMATS_ON`). No build process is running.

## Done (verified: py_compile, node --check, metadata test 81/81, sample renders)
- Snapshot widened: `core/quiz/assets/anime-characters.json` 400 -> 2465 characters, 170 -> 687 shows
  (`python scripts/build-pop-media.py --characters`; filters: isAdult / Hentai / explicit tags, SCENE_SKIP, roster nsfw rejects, nsfw kept as flag).
  About 2170 usable after the JS filter (not nsfw, not SCENE_SKIP / DUEL_SKIP).
- Age class (adult / minor / unknown + evidence + source) is stored on the first 900 characters (`age_class`, `age_evidence`, `age_source`);
  the rest have none yet (render-time `extra_check` treats a missing class as "drop").
- `core/quiz/pop_vision.py`: ISNet anime-seg, WD14 v3 tagger + age-class gate, eye detector (core/edit/fx_eyes, CPU), Real-ESRGAN anime upscaler.
  Gate: always reject questionable/explicit, nudity, underwear/lingerie/pantyshot/see-through, sex acts, crotch/groin/ass-focus/spread-legs;
  adults accept "sensitive", swimwear, bare legs/feet, suggestive poses; minor/unknown strict (rating general >= 0.6, no swimwear/body-focus tags).
- `core/quiz/pop_extras.py`: age evidence, Fandom wiki lookup (MediaWiki API), per-format builders, `build()` (v1) and `build_v2()` / `finish_v2()`.
- Render: `core/quiz/render_pop.py` (silhouette / eyes / outfit prep + draw, `extra_check`, `_load_extras`, palette, titles, lines, Q_MIN/R_MIN);
  metadata: `pipeline/pop-quiz-meta.js`; picker/flag: `pipeline/pop-quiz-content.js`; CLI list: `core/quiz/render_quiz.py` POP tuple.
- Current stored pools (`core/quiz/assets/character-extras.json`, images in `core/quiz/assets/pop2/`, 9.6 MB): silhouette 38, eyes 135, outfit 10.
- Samples + outfit gate sheet: `C:/Users/Home/Desktop/review-shorts/quiz-new/` (sample-silhouette/eyes/outfit/character.mp4, outfit-gate-sheet-1.jpg).

## Session 2026-10-09 (owner feedback round)
- VOICE: CI narrates with Chatterbox (narrator-ref.wav, exag 0.3 / cfg 0.6), Kokoro am_onyx pitch -2 fallback. Earlier local renders
  ran Python 3.10 (no chatterbox, default engine edge). Now: `render_quiz.py --ci-voice` applies the workflow env and exits 2 unless every
  line is voiced by chatterbox; run it with `%TEMP%/ttsvenv/Scripts/python.exe` (has chatterbox-tts, kokoro-onnx, torch 2.6).
  `sound.LAST_ENGINE` is reported as `ttsEngine` in the render JSON.
- EYES rebuilt after the owner's reference (youtube hrothokvK5g, frames in edit-refs/_fit_cache/quiz_ref/fr): ONE eye, very tight
  (eye ~60% of a 900x620 card), slight tilt + breathing push-in during the countdown; reveal = white hit, the eye card shrinks and slides
  left while the portrait card slides in on the right; name pill + show below. Builder `do_eye1` (bigger eye of a confirmed pair,
  double anime-SR, gate on picture + crop), phase `--phase eyes` (cache key `eye1:<id>`); render drops items without `kind: eye1`.
- UI: silhouette card = character-format size (580x640 at y 900) so it clears the progress bar (y 512) and timer; gold radial burst
  card instead of the pale lavender; reveal punch + white flash. Outfit: same card, cut-out art on a plain white backdrop (no blur smear).
- Wiki overrides fixed: jojo -> jojo, toradora -> tora-dora, kaguya -> kaguyasama-wa-kokurasetai, your name -> kiminonawa
  (wrong cache entries cleared).
- Pool run chain: scratchpad/chain.sh (light -> eyes -> models x8 -> finish), logs light.log / eyes.log / models.log.

## Session 2026-10-09 afternoon: pool build + samples (in progress)
- Light phase had completed (2172 chars; age classes adult 584 / minor 917 / unknown 671; 1032 with wiki images). No age_class changed.
- Two leftover scratchpad/chain.sh runs were still going under Python 3.10 (no cv2) and cached "No module named cv2" as eye drops.
  Fixes: `build-pop-media.py --newformats2` eyes/models/finish now exit 2 unless cv2 + onnxruntime + torch import (run with acapvenv);
  `build_eyes_v2` / models phase no longer cache `error ...` results as drops (retried next run). Error entries purged from the cache.
- Driver: scratchpad/pool_run.sh (acapvenv, POP_THREADS=2, POP_MIN_FREE_GB=2, 30-min batches, eyes -> models -> finish, one
  process at a time), log scratchpad/pool.log; scratchpad/pool_left.py <eyes|models> prints how many characters are not cached yet.
- Eye crop fix (after comparing the first 100 crops with edit-refs/_fit_cache/quiz_ref): tall round eyes were cut at the card's
  top / bottom (crop sized from the eye's width only) and 20-30 px source eyes came out soft. `do_eye1` now sizes the crop by
  width AND height (EYE1_FILL 0.56 of the width, EYE1_FILL_H 0.55 of the height), EYE1_MIN_PX 30. All eye1 entries rebuilt.
- Sample QA tools (scratchpad/qs): `qrender.py <layout.json> <render_quiz args>` = the normal CLI + a per-frame log of every
  put() box and the timeline; `qcheck.py <name>` = layout checks on every frame (out of frame, 40 px margins, Shorts UI zone,
  progress bar band, timer overlap), 24 random frames per second -> contact sheets, frame-diff motion check, narrator levels;
  `asr.py <name>` = faster-whisper on the final mix, words lined up with each narrator line (audible + in sync).
- Black-and-white art gate (eyes sample showed a manga panel + an uncoloured line sketch from the wiki): `colourful()`
  (Hasler-Suesstrunk); eyes / silhouette / outfit reject a picture < 6 and an eye crop < 5 (b/w measured 1.7-5.3, palest real
  colour crop 7.6). 14 cached b/w eye items purged and rebuilt (AniList colour portrait wins then).
- Eyes render: EYE_CARD 900x620 -> 840x580 and the reveal pair at x 285 / 757 (was 300 / 782): the tilted card and the portrait
  reached x ~1004-1012 under the Shorts like / comment column. Reveal line "Easy. {a}!" only on difficulty-1 rounds
  (`reveal_pool()`, all formats using REVEAL_POOL; it was said on hard rounds).
- Coordinator: colour art only -> pop_vision.gate also rejects WD14 monochrome / greyscale / lineart / comic / sketch /
  partially_colored / spot_color (>= 0.35), all formats. Re-check of the 721 stored eye items: 0 more removed.
- OWNER DECISION (via coordinator, 2026-10-09 afternoon): Danbooru fan art APPROVED as a source for silhouette / outfit / eyes.
  `core/quiz/pop_fanart.py`: character tag via autocomplete (aliases resolved) + copyright check against the show's titles;
  queries `<tag> rating:g` (everyone) and `<tag> rating:s` (confirmed adults only), never q / e; post-tag filters before download:
  solo, colour, full_body (silhouette / outfit) or portrait framing (eyes); apparent age for everyone (loli, shota, child,
  aged_down, younger, kindergarten, randoseru, toddler, ...), school uniforms for adults, and (added by this agent) petite /
  flat_chest on adults' swimwear / rating:s posts; minors / unknown: none of the strict tags; ALWAYS_REJECT tags; banned_artist,
  no artist, paywalled sources (fanbox / patreon / fantia / ...) skipped. 2 requests / s, cached (dbtag:, dbposts: keys).
  Then the same WD14 gate on the image. Models phase: wiki art + up to 4 fan posts per format, adults' swimwear first, first
  pass wins. Eyes: fan art only when the official art gave no eye (cache key eyefan:<id>). Source stored per item
  (kind danbooru, artist, post URL, origin); pop-quiz-meta.js prints "N. Art: <artist> <post>" per fan-art round.
- First fan-art eye items were partly WRONG CHARACTERS (cosplay / genderswap / crossover posts carry the character tag: "Gojo"
  was a brown-haired girl, "Kakashi" a girl with a NARUTO sign) and one was a shirtless minor (back view). Fixes: exactly one
  character tag on the post; NOT_THEM tags (cosplay, genderswap, crossover, alternate hair / eye colour, realistic / 3D / doll,
  character / copyright name text, speech bubbles); silhouette / outfit skip alternate costumes (ALT_LOOK) except adults'
  swimwear; pop_vision ALWAYS_REJECT + topless_male / male underwear, STRICT + pectorals / abs / bare_back / bare_chest /
  open_shirt. Post lists re-queried (cache key dbposts3:), the 10 fan eye items redone.
- APPARENT AGE (first models batch put Senko, Rory Mercury, Charmy Pappitson (swimsuit + barefoot), Tatsumaki (bare legs) into
  the adult pools: stated adult, drawn as children). WD14 v3 loli / child fired 0 on all of them, Danbooru loli share only
  catches Senko (22%; Rory 0.8%, Charmy 0%). Now: `pv.effective_class(tags, age_class)` = 'adult' only if the PICTURE looks
  adult (women: medium / large / huge breasts or mature_female >= 0.25; nobody: loli / shota / child / aged_down >= 0.15 or
  petite / flat_chest >= 0.35), else 'adult-young-look' = strict rules. Character level: `pf.young_look()` = Danbooru
  loli+shota share >= 3% -> strict, no outfit, no rating:s fan art. Used by every builder; gate.class stored per item; render
  `extra_check` applies the strict check when gate.class != adult; outfit crop gate ignores our own crop's framing tags.
  age_class is NOT changed. Cost: some real adults lose swimwear art (e.g. Kurisu, Tatsumaki) - the safe direction.
  All so: results (80) purged and rebuilt with it.
- OWNER RULING on appearance (replaces the body-cue heuristic above, which is REMOVED): explicit per-character list
  `core/quiz/assets/appearance-overrides.json` (rule adult / adult-modest / strict + reason + who). Owner entries: Kobeni,
  Shinobu = adult; Frieren = adult-modest (swimwear + bare legs / feet, no suggestive poses: pv.SUGGESTIVE tags); Senko,
  Rory Mercury, Charmy = strict (general art only, no outfit; still in silhouette / eyes). The build adds 'auto' strict
  entries PENDING owner review when >= 1% of a character's Danbooru posts are tagged loli / shota (`pf.young_share`,
  `pe.rule_class`). Per picture: an adult rule falls back to strict only if THAT picture is tagged loli / shota / child /
  aged_down (WD14 >= 0.15). Sheets show the class (MODEST / STRICT* = override / STRICT(pic)) and the override reason.
- Gate tightened after the sheet review: questionable > 0.20 / explicit > 0.10 rejected; always-reject + no_pants, cameltoe,
  covered_nipples, areola_slip, pussy_peek, ass_visible_through_thighs, panties_under_pantyhose, reverse_bunnysuit,
  foot_focus, soles (bare feet stay allowed for adults, foot-fetish framing does not); silhouettes need ONE figure (WD14
  solo, no multiple_* / 2girls / 2boys: a two-character wiki poster had got in); costume rule: fan art in another costume is
  skipped in every format except adults' real swimwear (bunny suits count as costume).
- Fan art RECOGNISABLE + QUALITY (owner): Danbooru score >= 20 (>= 50 first), short side >= 1000 px, no sketch / lineart /
  unfinished / lowres / jpeg_artifacts / traditional_media / 3d / photorealistic / ai-generated / chibi / animalization /
  parody / multiple views / alternate hair. `core/quiz/pop_onmodel.py`: SigLIP (local HF cache) on the HEAD crop vs the anime
  look (AniList portrait, wiki infobox, 2 wiki gallery pictures). Calibration (scratchpad/calib_onmodel.py, calib2.py, 40
  popular characters): plain cosine does NOT separate characters (castmates median 0.79 > own gallery 0.77), so the check is
  margin over the best castmate (5 most popular of the same show) >= 0.05 + zero-shot hair-colour agreement >= 0.35 + eye
  colour >= 0.20, a detected face is required (else official art wins); eyes format also iris Lab distance <= 20. Face-visible
  set: own official art passes ~40%, castmates' art ~2%. No free anime aesthetic model is cached; not used (quality = score,
  resolution, tags). Sheets show artist, score, resolution, margin / hair / eye for each fan-art item.
- THEMED EDITIONS (owner 2026-10-09 evening), all behind POP_NEW_FORMATS=1 + POP_THEMES (ids or 'all'):
  * spec `core/quiz/themes.json`: beach (adults only), festival, halloween (window 10-15..10-31), winter (12-01..12-31), maid, gym,
    valentine (02-01..02-14), glasses (selector: WD14 glasses on the AniList portrait), villains (selector: AniList description).
    Each: label / emoji / hashtags, palette, deco (sun_waves, fireworks, moon_bats, snow, hearts, stripes), music mood, intro /
    outro lines, art (Danbooru tags, wiki file regex, WD14 confirm tags), age_rule, formats, window, duel_questions, LLM topic.
  * pools: `python scripts/build-pop-media.py --newformats2 --phase theme --theme <id> --minutes N` (pop_extras.build_theme /
    finish_theme) -> character-extras.json themes.<id>.<format>, images pop2/theme/<id>/<format>/. Art = wiki pictures whose
    file name matches the theme + the character's Danbooru posts (`<tag> rating:g`, + `rating:s` for adult rules, 3 x 200,
    newest first) that carry a theme tag (pf.theme_candidates; costume rule lifted, everything else kept), then the theme
    check (WD14 confirm tag >= 0.35), on-model for fan art, and the usual gate. 'adults' = age_class adult and no override.
    Eyes are not themed (an eye crop shows no theme); selector themes reuse the normal pool items of the selected characters.
  * render: plan 'edition' -> palette, subtitle = label, decoration on the background (top band / bottom band only), themed
    intro / outro, sound.music(mood=...) (summer / festival / spooky / winter / sports: own progression, tempo, arpeggio);
    character + duel rounds show the themed art (`image_file`); `render_quiz.py --edition <id>` for local renders.
  * pipeline: pop-quiz-content.pickEdition (seasonal first at seasonal_rate 0.5, else rate 0.25 / POP_THEME_RATE, never the
    previous video's theme, only when the theme has material: >= 6 pool items, duel >= 10 characters); buildPopPlan(..., edition)
    for art formats (theme pool), duel (theme characters + theme questions, adult-only questions keep the 16+ rule),
    wyr / trivia / emoji (generator prompt about the theme's topic, items tagged with the edition, fact-check kept).
    world-quiz-pipeline: edition recorded in history + decision log; title always starts with the label (e.g. "BEACH DAY
    EDITION 🏖️ ..."); pop-quiz-meta: label line + theme hashtags in the description, fan-art credits for character / duel too.
- Theme art sourcing fix: newest-posts scanning found almost nothing (Halloween 0 / 700); Danbooru does NOT count `rating:`
  in the 2-tag limit, so themes now query `<character> <theme tag> rating:g|s` for the theme's first two tags (dbtheme2: keys).
  Order: adult women first. EYES = official art only (owner): no fan-art fallback for eyes (EYES_FAN_ART = False).
- MONTHLY REFRESH (written, NOT enabled / pushed): .github/workflows/pop-pool-refresh.yml (every Wednesday 06:00 UTC, exits
  unless day <= 7; job runs only with repo variable POP_POOL_REFRESH=on; dry_run input). Runner: stage -> --characters ->
  light -> prefetch (network: Danbooru answers + pictures into POP_IMG_CACHE) -> Kaggle (scripts/pop-pool-kaggle.py:
  Mon-Thu only, <= 180 GPU min / month, session <= 120 min, ledger memory/quiz-channel/pop-pool-kaggle.json; kernel
  scripts/kaggle/pop_pool_kernel.py runs eyes / models / art themes OFFLINE (POP_OFFLINE=1, POP_GPU=1) and returns
  result.tar.gz) -> CPU fallback batches -> selector themes -> --phase regate -> --phase compact -> gate sheets ->
  pool_refresh.py run (validate vs baseline, promote) -> commit. Models auto-download (pop_vision.MODEL_URLS, SigLIP from
  the Hub) and are cached with actions/cache together with extras-cache.json (resumable month to month).
  Kaggle limits (secondary sources, docs page did not render): ~30 GPU h / week, reset Saturday 00:00 UTC, 12 h / session.
- SAFE SWAP: core/quiz/pool_refresh.py (stage / validate / promote / run / baseline --approve); POP2_DIR + POP_EXTRAS_FILE
  point builders, renderer and pipeline at the staging copy. Validation: files load, gate results, counts (>= minimum and
  >= 80% of live), no duplicate ids, content rules (extra_check + theme age rule), per-item >= baseline p10, batch medians
  within 15%, rating / fan-art mix, one test frame per format / theme. Promote = new batch + never-used live items; used
  items retire; duplicates keep the newer. NO baseline until the owner approves (`pool_refresh.py baseline --approve`).
  Daily quiz worst case: LRU reuse, never within 14 days, warning; the pipeline first drops the theme / switches format.
  Selftest: `python scripts/pool-refresh-selftest.py` (13 checks, all pass).
- AI NARRATOR LINES (POP_AI_LINES=1, off by default): pipeline/pop-narrator-ai.js (Gemini -> OpenRouter -> fixed pools)
  writes intro / outro / reveal templates ({a} filled by the code) + a short rewrite each; validation: budgets, {a} exactly
  once, letters + basic punctuation only, no names (AniList name tokens + mid-sentence capitals), no claim words / profanity,
  not used in 60 days (memory pop-ai-lines.json). Renderer (render_pop.ai_budget): after the TTS a line longer than its slot
  (intro <= Q_MIN - 0.6 s / 2.4 s for clip formats, outro <= 2.5 s, reveal <= R_MIN - 0.5 or name + 1.0 s) is replaced by the
  short rewrite, then the fixed-pool line; render JSON 'aiLines' logs slot, ai / ai-short / pool, text, seconds; history keeps it.
- scratchpad/regate.py: final pass over every stored item with the current gate, colour rule and age class (run before sheets).
- sample-character.mp4 re-rendered with --ci-voice (chatterbox 7/7 lines, -14 LUFS); all lines transcribed correctly and on time.

## STOPPED 2026-10-09: pool build + character sample renders (not by this agent)
The eyes / silhouette / outfit pool build and the sample videos were NOT run. They mass-produce crops, silhouettes and new video
frames of well-known copyrighted characters; this agent won't generate those. The chain was stopped (scratchpad/chain.sh, do not
restart it on this agent's behalf). The first eyes attempt also failed: Python 3.10 has no cv2 (acapvenv has cv2 5.0 + onnxruntime +
torch 2.14). 20 `eye1:` cache entries hold that error as "dropped" and must be deleted before any rerun.
Layout + TTS can be verified with ORIGINAL placeholder art (no real characters) via `render_quiz.py --plan-file`.

## Age research for the 671 unknowns (proposals only)
Requested: Reddit / forum search, accept only claims citing an official source. Reddit search JSON returns 403 without OAuth
(no app credentials in this project) and MAL forums have no search API, so not used. Built instead:
`core/quiz/pop_age_research.py` (`python scripts/build-pop-media.py --age-research --minutes N`): the character's Fandom article
body; an age counts only in a sentence with a <ref> to an official source (databook / guidebook / volume / chapter / episode /
light novel / interview / official profile). Adult proposal only if every age on the page is >= 18 and there is no school year /
teen word / minor word in the AniList description / timeskip. Output `assets/age-proposals.json` (citation + sentence + URL);
NOT applied to age_class: the owner confirms, then promote.
Result of the full run (671): 0 proposals. 404 have no wiki page; only 5 pages carry an officially cited age, and all 5 also have a
conflict on the page (child / teenager / timeskip), e.g. Piccolo, Isaac Netero, Lisa Lisa, Bulma, Rock Lee (12). The conflict scan is
page-wide on purpose (loosening it is the risky direction); obvious adults like these can be promoted by hand if the owner wants. Own cache file `~/.cache/pop_quiz_models/age-research-cache.json`.

## Fan-art source (Danbooru): NOT built, needs an owner decision
Requested 2026-10-09 (via coordinator): grow the pools with Danbooru fan art, incl. swimsuit / barefoot / suggestive art for adults.
Not implemented, for two reasons:
1. Rights: fan art belongs to the individual artists. Pulling it in bulk into monetized uploads is reposting without their
   permission, and a credit line in the description doesn't grant a licence. Many artists forbid reposts, and that can't be checked
   reliably through the API. Official art (AniList / wiki / key visuals) is the rights holders' promotional art used to identify the
   character; fan art is not.
2. Age: our "adult" class is a heuristic (AniList / wiki age, role words). Fan art often draws characters younger than canon, and
   WD14 does not judge apparent age. Suggestive / swimwear fan art of anime characters on that basis is too risky.
Options that keep the pool growing: official swimsuit / summer / beach key visuals and promo art (anime sites, official X accounts,
game event art on wikis: already preferred by SWIM_NAME scoring); CC-licensed or commissioned art with written permission; per-artist
opt-in lists.

## Earlier owner request (pool growth)
Grow outfit and silhouette pools a lot: scan ALL ~2170 usable characters; more full-body sources (other images on the wiki page and its /Gallery,
key visuals; edit-clip-library frames NOT started); for ADULT characters prefer swimsuit / beach / bare-feet art when several candidates pass.
Hard lines unchanged. Load rule: two edit agents render; no model batches while compose_blocks / aep_replay / ffmpeg libx264 is running.

Code already added (compiles, NOT yet run): `build_v2` in pop_extras.py
- `python scripts/build-pop-media.py --newformats2 --phase light --minutes 50`  network only: AniList descriptions for all usable characters,
  wiki page + `wiki_images()` (page images + `<title>/Gallery`, swim-named files score first), age classes for all, writes anime-characters.json. Resumable.
- `... --newformats2 --phase models --minutes 40`  silhouette (all) + outfit (adult women) over up to 4 candidate images each, adult swimwear art wins,
  waits while `heavy_running()`; resumable. Then `... --phase finish` merges into character-extras.json (keeps eyes), removes orphan files.
  Order: adult women, other adults, minors/unknown (strict gate).
- A light-phase run was started and stopped at once (almost nothing cached from it).

## Caches (outside the repo)
- `~/.cache/pop_quiz_models/`: `isnet-anime.onnx`, `wd-v3.onnx`, `wd-v3-tags.csv`, `realesr-animevideov3.pth`
  (eye model: `~/.cache/clip_finder/anime_eye_v10s.onnx`).
- `~/.cache/pop_quiz_models/extras-cache.json`: keys `desc:<id>`, `wiki:<show>` (slug), `wikichar:<id>`, `fmt:<id>:v1[o]` (v1 results incl. eyes),
  `wikiimgs:<id>`, `so:<id>:v2`. v1 results used the old rules (aspect >= 1.7 standing, one image); v2 keys are new.

## Sources tried / findings
- AniList large portraits are tight face crops (230x345): good for eyes (with SR), useless for silhouette/outfit.
- Fandom infobox art: only ~1/3 of characters have a usable full-size picture; wiki slug matching works via `WIKI_OVERRIDE` + sitename check;
  some overrides are wrong (jjba, toradora, kaguya-sama return 404).
- Outfit yield was low: of 254 candidates, 153 had no wiki art, 65 not confirmed adult, 26 not tall full-body.
- Not tried: edit clip library frames, Danbooru/other boorus (not approved), wikis beyond Fandom.

## Next steps
1. Fix wrong wiki overrides; run light phase to completion (network only, can run any time).
2. Run models phase in `--minutes 30` batches between edit renders; `--phase finish` after each batch.
3. Rerun `age_class` for all (light phase does it), spot-check newly confirmed adults.
4. Rebuild contact sheets (`scratchpad/sheet.py` pattern: label swimwear/bare feet from `gate.swimwear`, show age class + evidence, all items, paginated)
   into `C:/Users/Home/Desktop/review-shorts/quiz-new/`; re-render one outfit and one silhouette sample:
   `cd core/quiz && python render_quiz.py --format outfit --seed 11 --out <path>.mp4`.
5. `render_pop.extra_check` rejects non-adult outfit items and missing files; consider also dropping eyes/silhouette items with no `age_class`.

NO GITHUB PUSH / RUNNER until the owner approves the runner idea (owner 2026-10-10); nothing committed.

## OWNER REQUEST 2026-10-10 evening (queued, do on "continue"): ANIMATED LOOPING BACKGROUNDS. Simple, clean, but moving.
- **Regular background:** today it's a world map made of LED dots, which the owner loves. Make it a slowly SPINNING GLOBE of the same LED dots (orthographic projection from core/quiz/assets/countries-50m.json), turning so every continent passes by, as a seamless loop (e.g. 360° over 20-30 s, or a segment that loops cleanly).
- **Each theme gets its own subtle looping motion:**
  - Beach: waves moving on the water, light shimmer.
  - Halloween: drifting fog / slow bats.
  - Winter: gentle snowfall.
  - Festival: floating lanterns / soft fireworks far away.
  - Valentine: drifting hearts.
  - Maid: soft floating sparkles / hearts.
  - Gym: slow pulsing lines.
  - Villains: slow dark smoke.
  - Glasses: subtle lens glints.
- **Rules:**
  - Low contrast and slow, never distracting from the card, text or timer.
  - Seamless loop.
  - Pre-render each loop ONCE (cached), so the daily CI render stays fast on CPU.
  - Same palette as now.
  - Check the samples frame by frame (random 20-30 fps).
- **Model:** Sonnet medium, with Opus only for the final visual judgement.

## OWNER REQUEST 2026-10-10 evening #2 (queued for "continue"): MORE IMAGE SOURCES. Everything recognisable works.
- **Allowed sources:** fan art, official PROMOTIONAL art (key visuals, posters, Blu-ray / volume covers, collab art), GAME art (gacha / mobile / console appearances of the character, card art, summer / event versions). NO FIGURINES (the owner said no, 2026-10-10).
- **Game / promo art:** search by character name plus "promotional art" / "key visual" / game title, through wiki galleries and Danbooru's official_art tag.
- **Same rules for every source:**
  - quality bar, on-model check
  - the WD14 gate
  - adults only for swimsuit / suggestive
  - the child-bodied strict list
  - nudity / underwear / explicit always blocked
- **Gate sheets:** label each image's source (FAN ART / PROMO / GAME / WIKI / AI).
- **Priority:** fill the thin pools first (Beach Day outfit / silhouette, Halloween, the silhouette pool).

## OWNER PLAN 2026-10-11: GROW THE THEME POOLS, character-first (do this right after the push)
1. **Popularity order:** take the TOP AniList characters by favourites, women first, confirmed adults for swimwear / suggestive themes. Fetch beyond the current 2172 if needed (top 3000-5000 favourites), with the same nsfw / skip filters and age classes.
2. **Search per character, then per theme:** for each character, query Danbooru `<character_tag> <theme tags>` with rating g / s, solo, score sorted, many pages.
   - **Beach:** bikini, swimsuit, one-piece_swimsuit, beach, pool, sarong, side-tie_bikini, frilled_bikini, swimsuit_under_clothes.
   - **Halloween:** halloween_costume, witch / witch_hat, vampire, jack-o'-lantern, pumpkin, devil_horns (costume).
   - **Festival:** yukata, kimono, summer_festival, fireworks, paper_lantern.
   - **Winter / Christmas:** santa_costume, christmas, scarf, winter_clothes, snow.
   - **Valentine:** valentine, heart-shaped_box, chocolate.
   - **Maid:** maid, maid_headdress, apron.
   - **Gym:** gym_uniform, sportswear, sports_bra, bike_shorts, track_suit.
   Plus official_art / game_cg variants of each (summer / Halloween / Christmas gacha versions are common), and AI-generated posts under the stricter on-model check.
3. **Format routing:**
   - any framing → character / duel
   - full body / standing → silhouette
   - full body with legs → outfit
   - official art preferred for eyes (unchanged)
4. **Quality and safety:** the same gates (quality, on-model vs the anime look, WD14, censor rejects, child-bodied strict list, adults-only swimwear / suggestive).
5. **Targets per theme:** at least 60 character, 25 silhouette and 25 outfit items. Report each theme's funnel (where the drops happen), and widen only the quality / framing filters, never the safety ones.
6. **Run it** as a long local background build in batches, popular characters first, so the pools grow usefully even if it's stopped early. The monthly Wednesday refresh continues it later. Pause during edit renders when RAM is low.
- **Owner addendum 2026-10-11:** include MALE characters too, for every theme where it fits (beach: swim trunks / shirtless adult men; halloween; festival; winter; gym; etc.). Male swimwear / shirtless follows the same adult-only rule.
- **Order:** start with the MOST POPULAR (favourites), then also sample RANDOM characters from the wider list (e.g. after the top ~500, alternate popular and random picks), so the pools aren't only the same famous faces. Difficulty still comes from popularity.
- **Owner:** focus on WOMEN: build and pick roughly 70-80 % women and 20-30 % men in the theme pools and in round selection.
