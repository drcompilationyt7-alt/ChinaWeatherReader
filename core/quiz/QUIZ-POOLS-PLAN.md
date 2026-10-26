# Quiz pool growth: plan + findings

## Status 2026-10-21 (built)
- Trivia reveal pictures + layout + natural titles: aeb390e. Balancer (family -> topic -> format): aafdd8b; `node scripts/quiz-balance-sim.js 60 7 --llm [--grow]`.
- Nightly mining: core/quiz/quiz_pool_grow.py + pipeline/quiz-pool-llm.js + .github/workflows/quiz-pool-mine.yml (03:30 UTC, QUIZ_POOL_MINE=off stops it).
- New formats nightcity / cuisine are wired (renderer, pipeline, meta) and stay off until 15 reviewed items each.
- Not done: would-you-rather showing photos on BOTH sides in the renderer (the mined food / travel pairs already carry both checked photos).
- Reuse rule (be8cfdf): items return after QUIZ_REUSE_GAP=8 uploads, LRU first, per exact picture; never one name twice in a video.
- First review 2026-10-22 (9f991e2): +13 city photos, +21 night skylines, +29 dishes, +18 idols -> nightcity + cuisine live.
- Known limits: the VTuber wiki answers 403 to GitHub runners (mine VTubers on the PC: `mine --shard people --cats vtuber`);
  Gemini free quota can be used up by late UTC (the 03:30 LLM categories then get little).
- Session routine: `python core/quiz/quiz_pool_grow.py pull-staged` -> look at output/quiz-pool-review/review/*.jpg + index.txt ->
  `import-staged --reject N,N --delete-remote` -> commit the changed core/quiz/assets files (+ pop2/ for theme art) -> `push-manifests`.


## Findings so far
- **Formats:** POP_FORMATS (pipeline/world-quiz-pipeline.js:45) and the quiz-channel.yml QUIZ_FORMATS list.
- **Weights:** FALLBACK_WEIGHTS on line 55. Picks use exploration on half the picks (EXPLORE_SHARE), otherwise Thompson on percentile rewards.
- **Rotation rules:** blockedFormats() enforces no repeat within 3 uploads and geography (GEO_SET = flag/shape/capital/bigger/crowd + city) at most 1 in 7.
- **Guards:** freshCount() / topUpEdition() / themeFits() in pipeline/pop-quiz-content.js. Editions are picked by pickEdition() (POP_THEMES, rate 1 in 4, seasonal 0.34).
  - LLM formats (emoji / trivia / wyr) still generate on demand in buildPopPlan() when the pool is low.
  - Plain emoji / trivia / wyr / duel freshCount returns Infinity. The new work should store pre-generated, reviewed items instead.
- **Why the K-pop idol rounds disappeared:**
  - Not blocked, and the pool is not empty: kpop-idols.json has 62 idols, 6 used.
  - Idol aired 10-07 (1312 views, 49 likes: one of the best) and 10-10 (281 views).
  - **Cause 1:** from 10-04 to 10-15, shape took every other day (5 uploads) before the 1-in-7 geography cap landed on 10-15.
  - **Cause 2:** POP_NEW_FORMATS added silhouette / eyes / outfit (0.25 / 0.25 / 0.2) plus editions, which diluted idol's 0.2 weight among about 18 formats (about 5 % a day).
  - **Fix to make:**
    - raise the idol weight (about 0.35);
    - add a "not seen in N days" boost in pickFormat for formats with a strong past reward;
    - grow kpop-idols.json.
- **Patterns to copy:**
  - core/pools/slowmo_grow.py: nightly_queries with a KEEP_DAYS rotation, selftest, vid_shard, publish / pull-staged / import-staged / push-manifests, contact sheets.
  - .github/workflows/slowmo-pool-mine.yml: a plan → mine (matrix shards) → publish job chain to the edit-pools-staging release.
- **Existing workflow:** the local untracked .github/workflows/pop-pool-refresh.yml is the monthly theme refresh (Kaggle / CPU, POP_POOL_REFRESH=on, first Wednesday). Theme building is done by scripts/build-pop-media.py --newformats2 --phase theme --theme T.
- **Uncommitted change:** core/quiz/assets/character-extras.json has local edits that are not yet checked. Diff them before any commit.

## Task list
1. **Nightly job:** .github/workflows/quiz-pool-mine.yml at about 03:30 UTC.
   - Turned off by the repo variable QUIZ_POOL_MINE=off.
   - Staging only (release edit-pools-staging, assets quiz-cand-*). Nothing goes live without Claude's review.
   - Script: core/quiz/quiz_pool_grow.py with the subcommands mine / publish / need / selftest / pull-staged / import-staged --reject / push-manifests, and per-category contact sheets.
   - Each category has a rotating query list, a per-night quota, and dedupe against the live pools plus rejects.
   - Categories:
     - a) **Anime theme art:** beach, halloween, festival, glasses, villains, maid, winter, valentine, gym.
       - Same sources and filters as the build-pop-media theme phase: official / wiki / game / promo art and on-model fan art (Danbooru g/s plus WD14), NO figurines.
       - Keep the per-image safety and on-model gates.
     - b) **IRL geo** (Commons CC, licence + attribution like city-photos.json): countries and cities (landmarks, skylines); China tier 1 and new tier 1 cities; Korean and Japanese cities.
     - c) **Night city views** (CN / KR / JP skylines) → NEW format `nightcity` (the cityphoto renderer with a night variant; counts as geography for the 1-in-7 cap).
     - d) **Chinese cuisine** (Commons CC; dish name + region) → NEW format `cuisine` ("Guess the dish").
     - e) **K-pop songs:**
       - grow songs.json (type kpop) for the song format, with the same safety and licence handling as opening;
       - K-pop song emoji items;
       - NEVER write lyrics.
     - f) **K-pop idols:** grow kpop-idols.json (Commons, licensed photos only) and apply the weight fix above.
     - g) **VTubers:** Hololive / Nijisanji official art → grow vtubers.json and add a character-format "vtuber edition".
       - Check the COVER derivative-work guidelines first.
     - h) **Emoji pre-generation:** the LLM writes candidates nightly (anime / K-pop / other topics), then fact-check → stored → reviewed. Stop relying on on-demand generation at render time (the cause of the 10-19 empty video).
     - i) **Would-you-rather editions with real photos on both sides:**
       - FOOD ("eat X or Y": Asian dishes; shares the cuisine pool);
       - TRAVEL ("go to X or Y": Asian cities / landmarks; shares the geo / night-city pools).
       - Edition gating counts only stored, reviewed pairs with images.
2. **Rotation:**
   - add nightcity, cuisine, kpop-song topic weight, the vtuber edition and the wyr food / travel editions with modest weights;
   - keep: no repeat within 3, geography (incl. nightcity) at most 1 in 7, views steer half;
   - freshCount / edition gating counts only real reviewed items, so each new format stays off automatically until its pool has enough.
3. **Content rules (hard):**
   - Adults may have swimsuits or suggestive art.
   - Always blocked: nudity, underwear, explicit content, censored images.
   - Strict list (Senko, Yachiru, Charmy, Milim, Rory) and minors / unknown age: the strict gate.
   - IRL photos: no identifiable private people as the subject.
   - No credentials, no lyrics.
4. **Tests:**
   - selftests: query rotation lasts 60 nights; shards are disjoint;
   - a plan-only dry run per new format with small fake pools (the guards must refuse);
   - node --check / py_compile;
   - then ONE workflow_dispatch (staging only).
5. **Push:**
   - only the needed files; grep for secrets; pull --rebase --autostash;
   - push as drcompilationyt7-alt (gh auth token -u, extraheader);
   - check rate_limit first;
   - don't touch daily-create.yml, core/edit/* or newtype-pipeline.js.
6. **Session review routine:** quiz_pool_grow.py pull-staged → look at the sheets → import-staged --reject … → push-manifests. Report counts only.

## Owner 2026-10-20: topic BALANCE
Recent quizzes were almost all anime. The owner wants a balance, not all anime, not all geo, not all K-pop, not all VTuber.
- Grow the IRL pools: geography, culture, food.
- K-pop and VTuber have been lacking lately: boost them until they catch up.
- Implement it as a topic-level balancer above the format picker. Topic buckets: anime / kpop / vtuber / geo / culture+food. Target shares (roughly even, tuned by views). Over the last ~10 uploads, boost the bucket furthest below its target and cap any bucket above ~40%. Keep the no-repeat-3-days and the format rules.

## Owner 2026-10-20: balance FORMAT TYPES as well as topics
- Balance the video types: quiz vs would-you-rather (vs duel, etc.).
- Inside quiz, rotate across its many types: guess from an anime frame (scene), emoji, guess the character, silhouette, eyes, outfit, guess the song / opening, K-pop song, idol, flag / shape / capital / city / night-city, dish, trivia.
- Two-level balancer: pick the TYPE family first (quiz / wyr / duel ...) toward target shares, then the TOPIC bucket (anime / K-pop / VTuber / geo / culture+food), then the specific FORMAT. Each level boosts what is furthest below target over the last ~10–14 uploads. No format repeats within 3 days; the geo cap stays.
- Views steer only part of the choice; exploration stays.

## Owner 2026-10-21: multiple-choice trivia (e.g. "asia city quiz: ice festival capital bund", 10-21 07:53)
1. The layout is a bit off: review it frame by frame against the other formats, and fix the spacing / sizes / timing.
2. Text-only is boring. On the ANSWER REVEAL, show an image or a short video clip of the actual thing (the city, dish, landmark, idol, anime...). Use the reviewed geo / city / food / idol pools; footage must be CC / licensed.
   - The guard: a trivia round without a reveal visual is not allowed (fall back to another question).
3. Also the title "asia city quiz: ice festival capital bund" reads like a keyword dump. Titles must read naturally.

## 2026-10-26: AI costume art (Kaggle, Illustrious-XL) - tuned
- **Why the 10-12 run passed 8 of 256 (3 %):** looked at the 86 characters' pictures by eye. About 40 % were usable; the gate threw them away.
  - The AI-strict on-model gate (castmate margin >= 0.10, hair >= 0.55, eye >= 0.35) sits far above what a costume picture scores.
    The eye-approved tune pictures scored margin -0.02..+0.19, hair 0.17..0.80, eye 0.07..0.41.
  - The prompts: the theme words came before the character; 'adult, mature female' and the 'flat chest, petite' negatives pulled every face toward one generic busty look.
    Male characters were drawn as girls (Kurapika, Light).
  - Small faces in full-body pictures: the eye detector found no face ('no clear face', 23 rejects).
- **Fixes (pop_gen.py):**
  - make_prompt: name + series first and weighted; male negatives (1girl, breasts); modest negatives for anyone not a confirmed adult ('adult-modest' gets modest variants); theme part + pose rotate per k, so each character gets distinct pictures.
  - plan_v2: explicit job list, 832x1216.
  - score_stage: costume-mode gate COSTUME = margin >= -0.03, hair >= 0.15, eye >= 0.065. A small face passes on margin >= 0.03 + hair >= 0.30.
    The WD14 safety gate, the artefact tags, the theme tag, the strict minor gate and the near-duplicate check are unchanged.
  - score_stage stages a quiz-cand folder (file_id <id>_ai<k>) for pull-staged / review / import-staged.
- **IP-Adapter (plus-face SDXL, from the AniList portrait) was tested and is OFF.**
  - Auto pass: 6 of 20, against 13 of 20 for the plain v2 prompt.
  - By eye: 11 of 20 usable, against 19 of 20.
  - It washes the style toward the screenshot and drifts hair colours.
  - img2img was not tried: the official art is a head shot, so it would keep that framing instead of a full-body costume.
  - The kernel keeps the option (job field ipa > 0).
- **Tune batch (40 pictures, about 21 GPU min):**
  - Automatic pass: 19 of 40 overall; 13 of 20 for the plain v2 prompt.
  - Old gate: 0 of 40.
  - Most of the remaining losses are the strict minor WD14 gate ('rating sensitive' on modest costumes). That gate is kept on purpose.
