# Ranking / list compilation from other creators' edits: study, prototype, risk (2026-10-24)

New edit type for Zero Yen Otaku. It is in Phase 1 (study + prototype): **NOT wired into any rotation, nothing uploaded.**
The owner decides after seeing the prototype and the risks.

- **Owner request:** a "list" short made from other people's edits, mimicking `v1tnk8GwWD8`, for genres AND rankings (strongest JJK character, strongest character, best waifu ...).
  - Source edits must be good, with no watermark.
  - We pick the part of each edit to use.
  - Each entry must show an edit OF THAT character / anime ("#3 Rimuru" shows a Rimuru edit).

**Files:**
- Study tool: `core/edit/refs/ranking_ref_study.py`. It does cuts, OCR per 0.5 s, audio RMS / novelty, a 1 fps index and per-second random 20-60 frame sheets.
- Builder: `core/edit/ranking_compilation.py`.
- Raw data, not committed: `Desktop/edit-refs/ranking/` (videos, `study/*.json`, sheets).

## 1. The four references (all by the same channel)

| | v1tnk8GwWD8 (owner's main ref) | XSHZhNzRovw | XQRx0mqFxbc ("Tierlist") | BnsZETrlnFA |
|---|---|---|---|---|
| uploaded | 2026-02-19 | 2026-08-04 | 2026-06-27 | 2026-06-22 |
| views / likes | 1.24 M / 69 k | 343 k / 16.7 k | 312 k / 21.8 k | 191 k / 11.1 k |
| canvas | 1080x1440 (3:4) 60 fps | 1080x1440 30 fps | 1080x1440 30 fps | 1080x1440 30 fps |
| length | 60 s | 59 s | 59.5 s | 59.5 s |
| entries actually shown | 3 (#5, #4, #3) | 3 (#5, #4, #3 cut off) | 3 (#5, #4, #3) | **2** (#5, #4) |
| entry starts (s) | 0 / 21.5 / 41.5 | 0 / 29 / 52.5 | 0 / 25 / 44 | 0 / 35 |
| segment lengths (s) | 21 / 20 / 18.5 | 29 / 23.5 / 6.5 | 25 / 19 / 15.5 | 35 / 24.5 |
| on-screen credit | none | none | none | none |
| description | empty | hashtags + `Credits: @p3ytoneditz @kawaiiloop_0 @knight_editz_01 @nexofx17` | hashtags only | hashtags only |

**Channel:** `the_snow00` (@the_snow0000) has 31.9 k subscribers and 80 shorts, nearly all in this one format ("all these Romance animes are peak").
- 22.6 M total views; the median short has 142 k views, the best 2.6 M.
- The oldest of the four references is 8 months old.
- It is a young, small, single-format channel, not a long-running or big one. Its survival so far tells us little about the risk.

### Common pattern (all 4)

**Layout:**
- 3:4 canvas.
- The source edit is a 16:9 band filling the full width at y 0.41-0.835. On a 1440-pixel-high frame that is 590 px to 1200 px.
- A heavily blurred, scaled-up copy of the same clip fills the rest of the frame (top and bottom).
- No colour treatment of our own.

**Title:**
- Two lines, centred at the top: "PEAK" / "ROMANCE ANIME".
- White, rounded geometric sans (Nunito / Montserrat-like, ExtraBold), soft dark drop shadow, about 5 % of the height per line.
- Static for the whole video. No animation.

**List:**
- Numbered slots "1." to "5." in a left-aligned column under the title, at x ≈ 0.17 and y 0.18-0.40.
- Names are white uppercase bold, smaller than the numbers.
- An entry's name appears in its slot on the frame its edit starts: a hard pop with no animation.
- Earlier entries stay listed.

**Order:**
- A countdown: #5 first, then #4, then #3.
- **The video ends before #2 / #1 are ever revealed.** The empty "1." / "2." slots are the hook: the comments argue about what is missing ("Bro forgot ...", "Where is Blue Box"). Some titles say "Part 2".

**Entries and segments:**
- 2-3 entries per 60 s, so each segment is 15-35 s (median ≈ 21 s).
- That is **close to a whole short edit**: typical fan edits run 15-40 s.
- In BnsZETrlnFA the source editor's end card ("[SWAG]" + logo, 33.5-35 s) and a small editor tag ("NUE", lower-left at 1.5 s and 22.5 s) are left in. The reference **does not remove watermarks or end cards**; it re-posts the edits nearly whole.

**Transitions:**
- The outgoing edit dips to black for ~0.2-1 s (black at 21.1-21.3, 24.5, 33.5-35, 42.5-43.5).
- The next edit starts with its own fade-in. These are mostly the edits' own heads and tails.
- No transition effects or SFX added.

**Audio:**
- Each entry keeps **its source edit's own audio** (its own song).
- There is no common track: a novelty peak marks every entry change.
- Within an entry the song plays as the editor cut it, including the editor's silent "pause before the drop" gaps (v1 2.0-3.0 s, Bns 11-20 s).
- Levels sit at roughly -10 to -18 dB RMS per entry, unnormalised.
- No voice-over. No added SFX.

**Intro / outro:** none. The first edit starts at 0.0 s with the list already showing, and the video ends mid-entry at about 60 s.

**Lyric text:** the source edits carry their editors' own kinetic lyric typography, which is part of the edits. One source edit (Kimi ni Todoke, v1 41-60 s) also has burned-in English subtitles.

**Which part of each edit is used:** from the start of the edit. There is no "best moment" picking: the reference takes the edit from its beginning for as long as the slot lasts.

### Where they differ (vary these between our videos)
- Entry count shown: 2 or 3.
- Segment length: 6.5-35 s. The last entry is often cut by the 60 s limit.
- Title wording: "Peak Romance Anime", "Romance Anime Tierlist", "all these Romance animes are peak", "you need to watch these".
- Frame rate: 60 or 30.
- Credits: in the description in 1 of 4, otherwise none.

## 2. Our version (the prototype), and why it deviates

We copy the reference, pacing included (**owner decision 2026-10-24: "I think longer so they can get the feel of the edit"**):
- 3:4 canvas, blurred fill, 16:9 band, static 2-line title.
- The numbered 5-slot list fills in as a countdown.
- **2-3 entries revealed in ~60 s.** #5, #4 and usually #3; #2 / #1 stay blank, as the comment hook.
- Segments of 15-35 s: `SEG_3` / `SEG_2`, about 21 / 20 / rest.
- Each edit plays with its own audio, with a short dip to black between entries.

What we still do differently:

1. **Never the whole edit.** At most 85 % of a source edit (`MAX_SHARE`). The editor's intro (first 8 %) and end card / logo (last 10 %) are never used. The window is centred on the edit's drop / peak (see below), not simply its first 20 s.
2. **Watermark-free sources only.**
   - A source edit with a handle, a logo or a persistent / semi-transparent watermark (`mark_scan`) is rejected.
   - Seconds that show the editor's own name are never inside the excerpt.
   - Nothing is ever blurred or cropped out.
   - The reference keeps other editors' watermarks; we do not use such edits at all.
3. **Credit in the description** (owner, 2026-10-24):
   - one plain-text line per entry, "#4 Yuji — edit by @handle" (`describe()`);
   - no URLs (rule [[no-external-links]]; `describe()` raises if a link slips in);
   - the editor handle is stored per source (`report.json` entries: `handle`, `channel`, `source`).
   - **On-screen credit is off by default, as in the references.** `--onscreen-credit` adds a small "edit: @handle" under the clip; it is a cheap risk reducer (section 4).
4. **Every entry shows its own character / anime** (owner, 2026-10-24). This is checked visually per segment; see below.

### Segment picker (`pick_windows`, superseded 2026-10-25 by the phrase windows in section 7)
1. It works on the downloaded edit's audio (librosa RMS + onset + beats) and picture (frame difference at 10 fps, cuts).
2. The **drop** is the beat where the next 3 s are loudest relative to the previous 3 s (dB jump + onset strength).
3. Candidate windows are of the target length:
   - starting at, or 0-1 s before, each of the top 8 drops;
   - plus a 1 s sliding grid;
   - skipping the editor's intro (first 8 %) and end card (last 10 %).
4. Window score:
   - 0.35 drop + 0.2 loudness + 0.2 motion + 0.15 cut rate + 0.05 "starts / ends on a cut"
   - minus 1.5 × on-screen text area (heavy lyric typography) minus 1.0 × subtitle frames.
5. Up to 6 distinct windows (at least half a window apart) go to the gates in order.

### Character check (owner: "the edits must follow")
- Targets are the entry's AniList characters from `core/quiz/assets/anime-characters.json`. Genre lists use the anime's two leads.
- The other side of the comparison:
  - the other list entries' characters;
  - the 6 most-favourited other characters of the same show (Gojo vs Sukuna vs Yuji vs Megumi ...).
- Per frame (2 fps inside the window), only frames with an anime face vote:
  - SigLIP zero-shot "an anime screenshot of <name> from <anime>" over all candidates (0.6);
  - SigLIP similarity of the anime-face crops to each candidate's AniList art crop (0.4, normalised).
- **Confidence** = share of the voting frames the target wins.
  - A window passes at ≥ 0.5 with ≥ 4 votes, and only if no other single character wins more frames.
  - A source with no passing window is rejected.
- The title must also name the entry (alias regex) before anything is downloaded.

### Quality / safety gates
- **Search:** title names the entry; 12-150 s (longer uploads are full-fight AMVs); ≥ 20 k views; no "vs / full fight / compilation / top N / tier list" (those are not "an edit of X"); no explicit words (`library.EXPLICIT_RE`, hentai blocked).
- **Info:** landscape only (the band layout needs 16:9, as the references); ≥ 720p; like / view ≥ 1.5 %.
- **Watermark gate (`text_gate`)**, run on every downloaded edit:
  - **Temporal-mean scan (`mark_scan`).**
    - Average the gradient and the frame over the whole edit (end card excluded). Moving content washes out; a mark that never moves stays crisp.
    - OCR that average, then keep a hit only if its pattern is present (NCC ≥ 0.35) in ≥ 50 % of frames, or ≥ 25 % for a short tag-like word.
    - This catches the semi-transparent tags per-frame OCR misses ("SANCHEZ", "nsnlxd"), and long lyric / caption lines do not trigger it.
  - RapidOCR every 0.5 s.
  - Reject on any handle-like text: "@", tiktok, instagram, "edit by", ".com", "subscribe".
  - Reject on any text box that persists over ≥ 30 % of frames across ≥ 3 shots, or repeats the same text across ≥ 3 shots.
  - Reject on a non-text logo: edge pixels present in ≥ 75 % of shots, logo-sized, inside the picture, with moving surroundings. A repeated still is not a logo.
  - Editor-name seconds: OCR text matching the channel name / handle (e.g. a "MOLOB" name card). Those seconds are excluded from the windows.
- **Subtitles:** bottom-band centred sentence lines; windows with > 15 % subtitle frames are rejected.
- **Safety per window:**
  - NudeNet (clip_finder) on every 2 fps frame.
  - SigLIP UNSAFE vs SAFE prompts; the window is rejected if > 15 % of frames are flagged.
  - Gore is allowed (owner 2026-10-12). The per-shot rules for minors stay as in library.py.

### Prototype results
See the "Prototype run" section at the end (filled in from the run's `.report.json`).

## 3. Strongest-list rankings
`TOPICS` in the builder hard-codes the order with a note on the source.

**JJK (rank 1 → 5): Sukuna, Gojo, Yuta, Yuji, Toji.**
- This is the post-manga-ending (Sept 2024) community power-scaling consensus.
- Checked against canon feats: Sukuna beat Gojo; Yuta fought in Gojo's body; Yuji's Shinjuku feats; Toji is the strongest non-sorcerer.
- Kenjaku / Yuki / Mahoraga are arguable for #4-5, but have few edits.

**For new lists, use an LLM proposal + sanity checks:**
- every name must exist in the AniList pool;
- the canon winner of a head-to-head must be ranked above the loser;
- each entry needs ≥ 2 watermark-free landscape edits.

"Best waifu / husbando" lists are opinion. Order them by AniList favourites (already in the quiz pool) and say "ranked by fans" in the title.

## 4. Risk assessment (honest version; the owner decides)

**What this format is:** re-posting other people's fan edits. Those edits are themselves unlicensed derivatives of copyrighted anime footage and copyrighted songs. That stacks three rights layers we do not own:
- the anime studio / licensor (Crunchyroll, Aniplex, Toho ...);
- the record label;
- the editor, whose selection / timing / effects are their creative work.

| risk | likelihood | consequence | notes |
|---|---|---|---|
| **Content ID claims by labels** (the songs) | very high, most videos | revenue goes to the label or the video is blocked in some countries; **no strike** | Same as our current edits. The music is the edit's own; we cannot swap it without breaking the edit. |
| **Content ID / manual claims by anime licensors** | medium | claim, block, or a **copyright takedown = strike** | Crunchyroll / Toho do send manual takedowns on popular clips; short excerpts lower but do not remove this. |
| **Manual takedown by an editor** | **medium-high, the biggest new risk** | each one is a **copyright strike**: 3 strikes = channel termination, and linked channels can follow | Fan editors watch for re-uploads of their edits, and the edit community is hostile to "edit compilation" channels. An editor whose edit is used without permission can file a takedown even though they don't own the anime. YouTube generally honours these as the editor's own creative work. |
| **YPP "reused content" / "inauthentic content" (formerly repetitious)** | **high** if monetised | channel-level demonetisation (whole channel, not one video), not a strike | The YPP policy names "compilations of clips from other creators with little or no narrative / commentary" as reused content. A list overlay is thin transformation. 80 near-identical "all these romance animes are peak" shorts is the inauthentic / mass-produced pattern. |
| **Community Guidelines** | low for the content itself | warning → strike | The channel group had its first warning on 2026-10-22 (danbooru link), so a second guideline violation within 90 days = a strike. Copyright strikes are separate from guideline strikes but also count toward termination. |

**How the reference handles credit:**
- Nothing on screen.
- The description lists editor handles in 1 of 4 videos; the other three have only hashtags.
- It even leaves other editors' watermarks and end cards in.
- Commenters treat the edits as the channel's own ("why does bro have to make an amv for every single one with the same songs").

So the reference is **not** a model of safe practice; it survives because it is small and young (8 months, 31.9 k subs), not because the format is safe.

**What makes compilations safer** (in order of effect):
1. **Permission from the editors.** A simple DM / comment ask, keeping a list of editors who said yes (many editors welcome credited reposts). This is the only thing that removes the editor-takedown risk. Everything else only lowers it.
2. **Short excerpts:**
   - ≤ 10-14 s and ≤ 40 % of any edit;
   - the edit is never re-uploaded whole or used from its start;
   - never more than one excerpt from the same editor per video.
3. **Clear on-screen credit** for every entry, plus the plain-text description credit.
4. **Real transformation:** the ranking itself (our own order, reasons per entry), a short on-screen reason or stat per entry ("Domain: Malevolent Shrine", "beat Gojo"), and a new structure (countdown to #1 with our reveal), so the video is a ranking with clips as illustration, not a playlist of other people's work.
5. **Honour takedown requests within hours:** delete the video and blacklist the editor in the builder (`used` / blocklist).
6. **Only use watermark-free edits.** Never remove a watermark: removing one is itself a DMCA §1202 issue. The builder already rejects watermarked edits rather than cropping them.
7. **Low volume:** not daily. Keep it to a side format, not the channel's whole identity (the inauthentic-content signal).

**Recommendation (safest workable version):**
- **Use the format only with editor permission.** Start a small allow-list of editors who said yes.
- Until the owner has that list, keep this a prototype and post nothing.
- **The owner chose the reference pacing (15-35 s per entry).** That is most of a typical 20-40 s fan edit, which makes an editor takedown more likely and weakens any "short excerpt" argument. It is the main risk lever, and the owner has taken it knowingly.
- If the owner wants to go without permission anyway, post on **a channel whose loss would not hurt the others**, never on the main channels:
  - the reference pacing (owner decision), never the whole edit / its end card;
  - watermark-free edits only;
  - @handle credit in the description (on-screen credit recommended too);
  - our ranking text;
  - at most 2-3 a week, never monetised as the bulk of the channel;
  - a same-day takedown routine.
- One editor takedown = one strike, and the group is already on a warning.
- The lowest-risk alternative uses the same layout with **our own edits** (the edit studio already makes character edits) in the band. Viewers mostly cannot tell, and it removes the editor risk entirely. The label / anime claims are then the same as for our current edits.

## 5. Production design: pool / backlog (coordinator 2026-10-24; not wired)

The daily video never downloads. It works like the other edit pools:
1. **Stage (mining).**
   - `--mode stage --topic X` runs in the nightly GitHub rotation or the local idle miner: one job at a time, the spare account, paced.
   - It pauses while `pool-mine-nightly` runs. `--offline` uses the cache only.
   - It searches, downloads and gates the needed ranks (#5, #4, #3 + spare #2), then cuts the best ≤ 35 s window of each passing edit into `$EDIT_POOL_DIR/_staged/ranking/<topic>/`.
   - The `manifest.json` per item records: source id / title / channel, **editor @handle**, window, char confidence + votes, safety / watermark status.
2. **Review.** Claude looks at the staged clips by eye: per-second random 20-60 frame sheets plus the sound. Then `--mode import --ids a,b --reject c` copies the passing ones into `$EDIT_POOL_DIR/ranking/<topic>/` with status `reviewed`.
3. **Assemble (daily).** `--mode assemble --topic X --out f.mp4` uses the reviewed pool only.
   - A topic is eligible (`--mode eligible`) only when every revealed rank has a usable reviewed edit.
   - An edit is not reused within `REUSE_GAP` = 8 ranking uploads, so it is never used back-to-back. This uses the ledger key `ranking_recent` in `memory/edit-pool-used.json`.
   - The description comes from `describe()`: plain-text @handles, no links.

## 6. Source rules, final (owner 2026-10-24, after hearing the risk)
Owner: "instead lets do we credit them and prioritise old ones like 1 year old". This replaces the permission-only idea.

**What is allowed:**
- Any edit that passes the quality bar may be used, **with credit**:
  - a plain `@handle` per entry in the description, no links (`describe()`);
  - **No on-screen credit.** The owner reviewed PREVIEW2 ("no credit at the side, just in description is fine"). `--onscreen-credit` exists but is off.

**Priority** (`rights()` → `priority`):
- CC / free-to-use / allow-listed editor: +3.
- Upload ≥ 1 year old: +2.
- Channel idle ≥ 6 months: +1. The newest upload comes from one cached channel request.
- Then popularity.

**Hard skips:**
- The editor says "don't repost / reupload / use" (`VETO_RE`) in the description, the pinned comment or the channel about.
- The editor is on the takedown blocklist.

**Proof:** every checked source gets a snapshot: description, about, pinned comment, licence, date, the reason. It is stored in `$EDIT_POOL_DIR/ranking/_permissions/<id>.json`.

**Takedowns:**
- Add the editor to `core/edit/refs/ranking-editor-blocklist.json`. Search, staging and the daily assembly all skip them from then on.
- **Remove the video the same day on any complaint.**

**Allow-list:** `core/edit/refs/ranking-editor-allowlist.json` is for editors who granted permission directly.

**Permission survey** (cached JJK sources, 17 popular edits): 0 permitted.
- 13 have only a "no copyright intended / fair use" disclaimer. That is not a grant.
- 1 sells project files. A paid project is not a repost licence.
- 1 says "do not reupload unless transformative". That is a veto.
- 2 say nothing.

**Project files** (owner asked):
- Popular editors sell their AE projects (Payhip / Boosty, $5-20).
- `aep_replay.py` can replay an AE project with our footage and free effect implementations. Fidelity is limited: Magic Bullet Looks, Twixtor, RSMB and Curves / LUT are no-ops, and the block-system remakes have not reached the 9/10 bar.
- The licence of a bought project normally lets the buyer make their own edits, not repost the editor's render.
- So feasible only as "our edit built from their template" (a different product). It is not a way to legally reuse their exact render.

### Character check, revised
The SigLIP + AniList-art vote was too weak in the preview:
- a Toji window passed at 0.5 with Yuta at 7 of 16 votes;
- a Yuji window passed with Nobara at 6 of 14.

**Now:**
- **WD ViT tagger v3** (danbooru character tags, ONNX, CPU) is used for the characters it knows: Gojo, Yuji, Sukuna, Megumi, Frieren, Kurisu, Robin, Yor, C.C., Marin, Makima, Zero Two. It also gives a rating check.
- SigLIP + art is the fallback for characters it does not know: Toji, Yuta, most romance leads.
- **Pass:** the entry's character wins ≥ 60 % of the character-voting frames and at least twice any other character.
- The Gojo window scored 0.86 by tagger. The two marginal ones are now rejected.
- Toji / Yuta need either more source candidates or a topic list built from taggable characters.

### Owner review of PREVIEW2 (2026-10-24)
- "the format is good": the layout / countdown / band / blur / dip-to-black are approved. Keep them exactly.
- "yuji edit can be better":
  - Yuji gets more queries (black flash, Shinjuku).
  - Every entry now evaluates up to `MAX_CLEAN` = 4 clean candidates.
  - It keeps the strongest: highest character share under the strict rule, then source priority. It no longer takes the first that passes.
- Segment length: now 18-24 s per entry, cut on the song's phrase ends and balanced across entries (section 7).

### Low-memory mode (owner: "can u do the last step even if the little ram we have")
- `RANKING_MIN_FREE_GB=1.5`: pause when free RAM falls below it, resume when it recovers.
- One heavy process, ≤ 4 threads, IDLE priority, models loaded once and lazily, 2 fps frames at 448 px.

## 7. Eventful + song scoring, phrase windows, pool survey (2026-10-25)

Owner feedback on PREVIEW2: Toji (@noctryx7) and Gojo (@XenozEdit) GOOD, Toji a bit long; Yuji (@Dr1zzy.aep.)
"not eventful enough". Code: `core/edit/ranking_score.py` (scores + phrase windows), wired into
`ranking_compilation.evaluate()`.

### Scores (0-10 each)
- **Eventful** = 0.25 cuts/s + 0.15 beat-synced cuts (cut within 70 ms of a beat / onset) + 0.2 motion
  (frame difference) + 0.15 flashes/s (luma spikes) + 0.15 shakes/s (camera-shift reversals, phase correlation)
  + 0.1 zooms/s (radial optical-flow bursts). Every frame up to 30 fps at 160x90, cached per source (`cache/ev`).
- **Song** = 0.25 drop (dB jump on a beat in the first 60 % of the window; silence floored at -40 dB so an
  editor's pause is not a 60 dB "drop") + 0.25 loudness + 0.3 pulse clarity + 0.1 punch + 0.1 bass.
- **Popularity** (0-1): the same song used by other editors in the search cache (trending edit song) + the
  edit's views. Song name from the description's MUSIC block / "Song:" / the title.
- **Quality** = 0.6 eventful + 0.25 song + 1.5 popularity - 15 x on-screen text cover.
- **Rank score** = quality + source bonus (>= 1 year old +1, idle channel +0.5, CC / free-to-use +1.5,
  `--prefer` owner-approved +3).

### Calibration (window as used in PREVIEW2 / the reference segments)
| | eventful | song |
|---|---|---|
| Toji lynJABr10N4 (owner: good) | **8.1** | 5.7 |
| Gojo thMXb5r792s (owner: good) | **9.7** | 6.7 |
| Yuji IryZxcTZgzE (owner: not eventful) | **2.8** | 4.6 |
| romance refs, 10 segments (composited 3:4, so motion is inflated and shakes are hidden) | 2.6-8.2, median ~4 | 2.7-6.3 |

Gates: eventful >= 6.0 for action topics (JJK), >= 3.5 for calm ones (romance, waifu: the romance refs
themselves score ~4); song >= 4.0.

### Phrase windows
Bars from the beat track (downbeat = the strongest onset phase); a phrase-end strength per bar boundary
(MFCC + chroma novelty over 2 bars, an energy dip right before it, an editor cut on it, the 4-bar grid from the
strongest bar). Windows start and end on those boundaries, 18-24 s (target 20), plus up to 2 length
variants per region. `balance()` then picks one window per entry: best summed quality minus the length spread,
total <= 63 s. 2-entry videos use 24-34 s.

### Character check, revised again
- **Bug fixed:** the topic used "Toji Fushiguro"; AniList has "Touji Fushiguro", so Toji had no art reference
  at all. Also fixed: Shota Kazehaya, Kyoko Hori, Rintaro Tsumugi. Same-show distractors now match across
  seasons ("JUJUTSU KAISEN Season 2").
- Characters the WD tagger does not know (Toji, Yuta, most romance leads): `match_open()`. Frames with a
  tagger-known distractor of the same show (Gojo, Megumi, Yuji, Nobara ...) vote for it, other-gender-only
  frames vote "other_gender", the remaining person frames vote by **face crop vs AniList art similarity with
  a 0.02 margin** (ambiguous frames abstain). SigLIP name prompts are no longer used there: they called
  almost every JJK frame "Yuta". Same pass rule (>= 60 %, >= 2x any other).
- Toji lynJABr10N4 now passes at 0.71 (it failed at 0.38 with the old SigLIP vote).
- **Limit:** one AniList picture per character is a weak reference. Yuta edits (white uniform / Gojo-body
  scenes) and both romance leads of Horimiya / Blue Box mostly fail. These entries cannot be verified yet.

### Safety change
NudeNet misfired on a Gojo eye close-up (WD rating "general" 0.95) and vetoed the owner-approved Starboy
window. A NudeNet hit now counts only when the WD tagger confirms that frame (questionable + explicit >= 0.35).
The SigLIP unsafe share and the tagger rating gate stay.

### Pool survey (the final source rules; full gates on a sample)
Rights level: the top 10 search hits per entry that pass title / length / views, then info gates and the
rights check (proof snapshots in `$EDIT_POOL_DIR/ranking/_permissions/`). Full gates: the first 4-5 usable
candidates per entry downloaded (old edits first) and run through watermark, subtitles, eventful + song,
safety and character (`--mode deep-survey`, json + review sheet `deep-<topic>-sheet.jpg` in
`Desktop/edit-refs/ranking/survey/`).

| topic | entry | usable (rights) | >= 1 y | downloaded | watermark-free | **pass all** |
|---|---|---|---|---|---|---|
| jjk_strongest | #5 Toji | 8 | 7 | 4 (+1 owner-approved) | 4 | **1 (+1)** |
| | #4 Yuji | 7 | 2 | 4 | 1 | **0** |
| | #3 Yuta | 6 | 2 | 4 | 3 | **0** (character check) |
| | #2 Gojo | 8 | 6 | 4 | 3 | **1** |
| best_waifu | #5 C.C. | 7 | 7 | 4 | 4 | **2** |
| | #4 Yor | 10 | 10 | 4 | 3 | **1** |
| | #3 Nico Robin | 9 | 8 | 4 | 3 | **0** |
| | #2 Kurisu | 4 | 4 | 4 | 1 | **1** |
| romance_peak | #5 Fragrant Flower | 10 | 8 | 5 | 4 | **2** |
| | #4 Dress-Up Darling | 10 | 8 | 5 | 3 | **2** |
| | #3 Horimiya | 10 | 10 | 4 | 4 | **0** (character check) |
| | #2 Blue Box | 10 | 8 | 5 | 4 | **0** (character check) |

- 0 vetoes outside JJK Yuji (2 editors say no reuploads); 4 CC / free-to-use edits (C.C. 2, Yor 1, Horimiya 1).
- About 30 % of downloaded usable edits pass every gate; the watermark gate and the character check are the
  main losses.
- **Buildable now:** JJK 3 entries only with the spare #2 (Gojo) standing in for Yuta (as PREVIEW2); waifu
  3 entries the same way (C.C., Yor, Kurisu as #2); romance only 2 entries (#5, #4), the 2-entry format of
  the reference BnsZETrlnFA.
- **Cadence verdict: weekly, not daily.** Each entry has 0-2 passing edits, and an edit may not return
  within 8 ranking uploads, so a topic gives ~1-2 videos before it needs new edits. Daily would need
  ~30 topics with >= 3 verifiable entries each. One video a week, rotating topics, keeps the pool ahead and
  matches the risk advice (section 4: not daily, never the channel's main identity). Raising it needs a
  better character reference for tagger-unknown characters (several art images per character) and more
  candidates per entry (deeper search).

## Prototype run

**v3 strict sample (2026-10-25, never uploaded):** `review-shorts/edits/new-types/ranking_jjk_strongest_v3.mp4`
(+ `-sheets/`, `.description.txt`, `.report.json`), 58.2 s, no label, no on-screen credit.
Built with `--show 3 --seed 7 --prefer lynJABr10N4,thMXb5r792s,mY7mgdslbdc`.

| slot | entry | source | editor | window | length | eventful | song | character |
|---|---|---|---|---|---|---|---|---|
| #5 | Toji | lynJABr10N4 (owner: good) | @noctryx7 | 42.4-61.7 | 19.3 s (was 20.9) | 8.0 | 5.9 | 0.71 (open-set) |
| #4 | Yuji | mY7mgdslbdc, 911 days old | @DivineHotdog | 106.1-125.3 | 19.3 s | 8.6 | 6.3 | 0.75 (tagger) |
| #2 | Gojo | thMXb5r792s (owner: good) | @XenozEdit | 27.6-47.2 | 19.6 s | 10.0 | 6.5 | 0.79 (tagger) |

- #3 Yuta: no edit passes the character check (see section 7), so the spare #2 shows, as in PREVIEW2.
- The new Yuji was the only one of 12 downloaded Yuji edits that passed every gate (wide hunt, 3-4 min AMVs
  allowed for Yuji). It is a 3-panel manga-animation edit, and its description credits other creators' manga
  animations and TikTok edits that it reuses. The credit goes to @DivineHotdog only. **The owner should check
  this one.**


## 8. Production (2026-10-25: owner approved v3, "start setting this type ready")

Owner decisions (final): the v3 / PREVIEW2 format; others' edits credited ONLY in the description (plain @handles, no
links, nothing on screen); >= 1-year-old edits and idle channels first; hard-skip "no reupload" editors and the takedown
blocklist; the entry must show THAT character; eventful edits + strong songs; segments end on phrase ends, 18-24 s,
balanced; cadence WEEKLY with rotating topics. Owner 2026-10-25: Rimuru is #1 in any POWER list that includes him
("if he is mentioned as power level he should be 1"), every ranking must make sense, no "best husbando" list.

### Code
| file | what |
|---|---|
| `core/edit/refs/ranking-topics.json` | the topic library (owner-editable, rank order = the file order) |
| `core/edit/ranking_topics.py` | `check` (sanity), `refresh` (AniList favourites / scores / main characters, wiki check), `show` |
| `core/edit/char_refs.py` | the multi-picture character reference bank |
| `core/edit/ranking_pool.py` | plan / mine / publish / pull-staged / review / import / push-state / eligible / assemble / mark / status |
| `core/edit/ranking_compilation.py` | the gates, the models, the renderer (+ the one-off `build`, survey, deep-survey) |
| `.github/workflows/ranking-pool-mine.yml` | the weekly mining job (called by pool-mine-nightly.yml) |
| `pipeline/newtype-pipeline.js` `runRanking` + `pipeline/daily-rotation.js` | the weekly daily-job slot + the pre-upload guard |
| `core/edit/refs/ranking_charcheck_eval.py` | the character-check evaluation (old vs new) on the survey candidates |

### Topic library and its sanity rules
- `order: power`: community power-scaling consensus; every `above` pair (canon head-to-head / feat) must hold; `pin`s hold.
  A power list with Rimuru pins him #1 and uses `reveal: top` (a 3-slot list counting down #3, #2, #1, all shown), so he
  is both the highest-ranked and on screen, and no hidden slot claims someone stronger. This is the cleaner of the two
  options: in the countdown format he would either sit in a hidden slot or below two blank "stronger" slots.
- `order: fans`: AniList favourites, high to low ("ranked by fans" in the description).
- `order: score`: genre lists, AniList average score, high to low; the entries are ANIME and any character of the show
  counts in the character check.
- `python core/edit/ranking_topics.py check` fails on a broken order (an inverted `above` pair, a pin elsewhere, a fan /
  score list out of order unless `order_locked`), missing names, too few entries.

**The library (2026-10-25, 22 topics: 11 power, 3 fan / waifu, 8 genre lists; `ranking_topics.py show`):**
```
jjk_strongest [power, countdown]: #1 SUKUNA > #2 GOJO > #3 YUTA* > #4 YUJI* > #5 TOJI*
op_strongest [power, top]: #1 RIMURU* > #2 ANOS* > #3 AINZ* > #4 SHADOW
strongest_overall [power, countdown]: #1 SAITAMA > #2 GOKU > #3 SUNG JINWOO* > #4 MADARA* > #5 GOJO*
demon_slayer_strongest [power, countdown]: #1 YORIICHI > #2 MUZAN > #3 KOKUSHIBO* > #4 GYOMEI* > #5 AKAZA*
one_piece_strongest [power, countdown]: #1 SHANKS > #2 LUFFY > #3 KAIDO* > #4 MIHAWK* > #5 ZORO*
naruto_strongest [power, countdown]: #1 NARUTO > #2 SASUKE > #3 MADARA* > #4 PAIN* > #5 ITACHI*
bleach_strongest [power, countdown]: #1 ICHIGO > #2 YHWACH > #3 AIZEN* > #4 YAMAMOTO* > #5 KENPACHI*
solo_leveling_strongest [power, countdown]: #1 SUNG JINWOO > #2 BERU > #3 THOMAS ANDRE* > #4 IGRIS* > #5 CHA HAE-IN*
dragon_ball_strongest [power, countdown]: #1 BEERUS > #2 GOKU > #3 BROLY* > #4 VEGETA* > #5 GOHAN*
aot_strongest [power, countdown]: #1 EREN > #2 LEVI > #3 MIKASA* > #4 ZEKE* > #5 REINER*
mha_strongest [power, countdown]: #1 DEKU > #2 SHIGARAKI > #3 ALL MIGHT* > #4 ENDEAVOR* > #5 BAKUGO*
chainsaw_man_strongest [power, countdown]: #1 DENJI > #2 MAKIMA > #3 REZE* > #4 POWER* > #5 AKI*
best_waifu [fans, countdown]: #1 MIKASA > #2 MAKIMA > #3 MARIN* > #4 REM* > #5 ZERO TWO*
best_waifu_2 [fans, countdown]: #1 FRIEREN > #2 KURISU > #3 NICO ROBIN* > #4 NEZUKO* > #5 YOR*
best_waifu_3 [fans, countdown]: #1 EMILIA > #2 MAI SAKURAJIMA > #3 VIOLET* > #4 ZERO TWO* > #5 C.C.*
romance_peak [score, countdown]: #1 KAGUYA-SAMA > #2 THE FRAGRANT FLOWER > #3 HORIMIYA* > #4 BLUE BOX* > #5 MY DRESS-UP DARLING* > #6 KIMI NI TODOKE
action_peak [score, countdown]: #1 ATTACK ON TITAN > #2 VINLAND SAGA > #3 JUJUTSU KAISEN* > #4 DEMON SLAYER* > #5 CHAINSAW MAN* > #6 SOLO LEVELING
isekai_peak [score, countdown]: #1 RE:ZERO > #2 MUSHOKU TENSEI > #3 THE EMINENCE IN SHADOW* > #4 REINCARNATED AS A SLIME* > #5 KONOSUBA* > #6 OVERLORD
sad_peak [score, countdown]: #1 A SILENT VOICE > #2 VIOLET EVERGARDEN > #3 YOUR LIE IN APRIL* > #4 I WANT TO EAT YOUR PANCREAS* > #5 ANOHANA*
comedy_peak [score, countdown]: #1 BOCCHI THE ROCK > #2 GINTAMA > #3 SPY x FAMILY* > #4 GRAND BLUE* > #5 KONOSUBA*
sports_peak [score, countdown]: #1 HAIKYU!! > #2 HAJIME NO IPPO > #3 SLAM DUNK* > #4 KUROKO'S BASKETBALL* > #5 BLUE LOCK*
horror_peak [score, countdown]: #1 THE PROMISED NEVERLAND > #2 PARASYTE > #3 DEVILMAN CRYBABY* > #4 TOKYO GHOUL* > #5 ANOTHER*
(* = shown on screen)
```
No "best husbando" list (owner 2026-10-25).

### Character check v2 (multi-picture bank)
Per character: the AniList art, our quiz-pool pictures of that AniList id (eyes portrait, outfit, theme art), and the
show's Fandom wiki page + /Gallery files whose NAME is only about this character (a file naming anyone else, "Everyone
...", "Maki and Yuta ...", is skipped). One dominant face per picture (group shots dropped), the WD tagger vetoes a
picture where it sees another known character, a consistency filter drops outliers. A frame votes for the candidate
with the best mean of its top-3 reference similarities, only with a 0.03 margin; tagger-known characters keep the WD
tagger vote. Pass rule: >= 60 % of the voting frames, >= 2x any other character, and >= 6 voting frames (a 4-vote bank
pass called a Horimiya edit "Fragrant Flower"; every true bank pass had >= 6). Genre entries: the other-gender vote is off
(the target is a whole cast), any character of the show counts.

**Evaluation** (`ranking_charcheck_eval.py`, the 42 downloaded watermark-free survey candidates, 2 x 20 s chunks each,
every chunk checked for its own entry AND for every other entry of the list):

| | own entry passes (candidates) | wrong-character passes (chunks) |
|---|---|---|
| v1: one AniList picture (same pipeline otherwise) | 25 / 42 (JJK 8, waifu 7, romance 10) | **7**: Toji on 2 Yuta edits; Blue Box / Fragrant Flower on Dress-Up / Horimiya / Blue Box edits |
| v2: the reference bank (margin 0.03, >= 6 votes) | 21 / 42 (JJK 8, waifu 7, romance 6) | **0** |
| v2 with margin 0.02 (tested, not used) | +3 (Dress-Up x2, Blue Box) | 1 (Blue Box edit passed as Dress-Up) |

(Both versions also flag GOJO in MTFXS_Tna2w, a "Toji" edit that is half Gojo footage: a true detection, not counted.)
- Precision first: every wrong-character pass is gone. The 4 romance passes v1 had and v2 lacks rested on 2-4 votes.
- Toji now has a 23-picture bank (the wiki is "Toji", AniList "Touji": the bank searches both spellings).
- Still 0 of 3 Yuta candidates pass: the Yuta edits are mostly Geto / Rika / masked or far shots (few clear Yuta faces).
  Yuta needs more candidates (mining searches deeper) before jjk_strongest can show its #3.

### Watermark gate additions (the PJUNKIE miss)
`xlPSek4600U` (@PJUNKIE, Yuta) passed the watermark gate: it ends on an "OVO EDITORS" group card and the editor's logo on
black (29.5-33 s of 33.2 s), and the 10 % end-card margin kept out only the last 3.3 s. Now (`editor_tags`):
- tag words anywhere (editors / edits / amv / fx / prod / presents / thanks for watching / follow / channel / subscribe);
- the editor's name matched fuzzily (OCR "10FLMZY" = Flimzys, "MAZT" = maztrae, "ZYPER" = ZyperAmv);
- a logo card on black (no OCR text, a small bright centred element) in the first 10 % / last 20 %;
- a mark in the last 40 % ends the usable part there; in the first 25 % the usable part starts after it;
- the editor's name at two moments >= 5 s apart, or a SMALL name (< 1 % of the picture) mid-edit = a recurring / faint
  watermark: the whole source is rejected (teRmyO3US4A "SANCHEZ", Blubzz, Floby, Zeno, ZARKX, KieNai).

### Pool path
1. **Candidates** (metadata only): per entry, the search results that pass the title / length / views gates, cached 14
   days in `ranking-state.json`, with every checked source's verdict (gate_reject / staged / rejected / reviewed), so
   nothing is downloaded twice.
2. **Mining** (`ranking_pool.py mine`): entries in `plan()` order, i.e. the entries that block whole topics first (an
   entry that alone blocks a topic scores 1, two missing entries 0.5 each ...). Per entry <= 3 downloads (old / idle
   editors first, vetoes and the blocklist skipped), every gate, the best phrase window; ONLY that window + 1.5 s margin
   is stored (`clips/<id>.mp4`), the source edit is deleted. Each staged item has its review sheet (20 random frames), the
   proof snapshot (description / about / pinned comment), the editor handle and the upload date.
   - GitHub: one job a week, Wednesday (`RANKING_MINE_WEEKDAY`), in pool-mine-nightly's rotation; `RANKING_MINE=off` stops it.
   - PC: `scripts/local-mine.py`, lowest priority, one small batch a day after >= 3 other batches (`LOCAL_MINE_RANKING=0`
     stops it); the same guards (spare cookies, no YouTube while a GitHub mining run is queued / running, bot-check back-off).
3. **Review + import** (owner PC) and 4. **assembly** (daily job, weekly slot): see the runbook.

Pooling is per ENTRY (name + show), so one reviewed Gojo segment serves every list with Gojo.

## 9. Runbook

**Cadence.** One ranking video a week. The daily job's slot is Saturday (`RANKING_WEEKDAY=6`, UTC): that day the
ranking video REPLACES the day's type (never a second post) when `DAILY_TYPE_WEIGHTS` contains `ranking:1`, a topic is
eligible and the last ranking upload is >= 6 days old. **It stays OFF (ranking:0) until the owner approves the first
CI-built video.** Topics rotate: an eligible topic not used in the last 4 ranking uploads, least recently used first; an
edit is not reused within 8 ranking uploads; never two excerpts of one editor in a video. Mining runs Wednesday night.

**Review routine (weekly, after Wednesday's mining):**
```
python core/edit/ranking_pool.py pull-staged          # the release's ranking-cand tars -> output/edit-pools/_staged/ranking
python core/edit/ranking_pool.py review               # one line per staged item + its review sheet path
#   check each sheet (and play the clip): THAT character, no watermark / editor tag / end card, no subtitles, safe, eventful
python core/edit/ranking_pool.py import --ids r_a,r_b --reject r_c --by owner
python core/edit/ranking_pool.py push-state           # the decisions -> ranking-state.json on edit-pools-staging
GH_TOKEN=$(gh auth token -u drcompilationyt7-alt) node scripts/edit-pools-store.js push --preset ranking   # the live pool
python core/edit/ranking_pool.py status               # per topic: reviewed / staged per revealed entry, ELIGIBLE
```

**Daily job.** `pipeline/newtype-pipeline.js runRanking` -> `ranking_pool.py assemble --topic auto`. Pre-upload guard:
the file, 40-75 s and within 3 s of the segments' total, every revealed entry present with a reviewed segment of THAT
entry whose character check passed (>= 0.6), distinct editors, a credit line per entry, no URL in the description,
frames not blank / static (qa_guard), the title style (`"<keyword> 🔥 #<hashtag>"`, keyword first). Any failure -> the
stem edit that day (a video every day) and an issue with the reason. A test without upload: Actions -> Daily Shorts
Creation -> type `ranking`, dry_run `true` (optionally `ranking_topic`).

**Takedown / complaint (same day):**
1. Delete (or private) the video on YouTube at once.
2. Add the editor to `core/edit/refs/ranking-editor-blocklist.json` (`"@handle": {"added", "why", "video"}`), commit,
   push. Mining, staging and the daily assembly skip that editor from then on.
3. Optionally reject their pool items (`ranking_pool.py import --reject <ids>`) and push the pool.

**Adding / changing topics.** Edit `core/edit/refs/ranking-topics.json` (entries in rank order; `chars` = AniList names;
`wiki` = the show's Fandom wiki for the reference pictures; power lists need `above` pairs), then
`python core/edit/ranking_topics.py refresh` (AniList numbers; fan / score lists re-sort unless `order_locked`) and
`python core/edit/ranking_topics.py check` (must print 0 errors). `ranking_pool.py plan` shows what mining does next.

## 10. STATE when saved (2026-10-26 ~01:10 UTC, owner left) + resume steps

**Owner decisions (all final, 2026-10-24/25):**
- Format = v3 / PREVIEW2 (3:4, band, blur, static title, 5-slot countdown, dip to black, each edit's own audio).
- Others' edits, credit ONLY in the description (plain @handles, no links, nothing on screen); >= 1-year-old edits and
  idle channels first; hard-skip "no reupload" editors + the takedown blocklist; remove a video the same day on complaint.
- The entry must show THAT character; eventful edits + strong songs; phrase-end segments 18-24 s, balanced.
- Cadence WEEKLY, rotating topics; the type replaces that day's stem, never a 2nd post; OFF until the owner approves the
  first CI-built video.
- LISTS FIRST: a big topic library (strongest overall / per show, waifu, romance / action / isekai / sad / comedy / sports
  / horror; genre entries = ANIME, character entries = CHARACTERS); per entry a cheap candidate list (metadata), then
  download + gates + only the chosen segment; mining fills the entries that block whole topics first.
- NO "best husbando" list.
- An OP list "strongest in general" (Rimuru, Ainz, Anos + the usual OP picks) = `op_strongest`.
- Rimuru: not in every list, but in any POWER list that has him he is #1 AND shown -> `pin: 1` + `reveal: top`
  (a 3-slot list #3 -> #1; no hidden slot claims someone stronger).
- Every ranking must make sense: power lists = community consensus + canon `above` pairs; fan lists = AniList
  favourites; genre lists = AniList score; owner overrides in the topic file (`order_locked`); `ranking_topics.py check`.

**Done (pushed: 0e2d10a, 0c8740a, a7f62e0 + this save):** everything in sections 8-9 (library 22 topics, character bank,
editor-tag + full-resolution window watermark gates, the pool path, weekly mining Wed, PC miner lowest priority, the daily
weekly slot Sat with guards + stem fallback, ranking:0 = OFF). Tested: pool eligibility / reuse gap / distinct editors /
topic rotation (synthetic), a local assemble of 3 reviewed segments (63.4 s, layout as v3) + `guardRanking` (passes; a
broken copy fails on unreviewed / char 0.4 / same editor / link), titles pass `title-style` ("strongest jjk characters 🔥
#jujutsukaisen"), longform + title selftests pass.

**Pool now** (live, release `edit-pools` asset `pool-ranking-pool.tar`, 4 items):
| item | entry | editor | reviewed by |
|---|---|---|---|
| r_lynJABr10N4_40660 | TOJI | @noctryx7 | owner (v3) |
| r_thMXb5r792s_27610 | GOJO | @XenozEdit | owner (v3) |
| r_mY7mgdslbdc_29000 | YUJI | @DivineHotdog | Claude (owner approved this source in v3, another window) - owner to confirm |
| r_OaYRHPJZiXg_1790 | HORIMIYA | @karma7892 | Claude - owner to confirm (calm, plain footage) |
Rejected in review: r_lze2HJlbwEE (Gojo, "FL!CK" name logo), r_QyRzyUmEt7M (Yor, tiny "a.editorx" tag),
r_FDIFn1mnZb0 (C.C., key-icon logo); the last two are now caught by `window_marks`. Staging state pushed
(`ranking-state.json` on `edit-pools-staging`, 46 sources).

**No topic is eligible yet** (jjk needs YUTA; strongest_overall needs MADARA + SUNG JINWOO; waifu_2 needs NEZUKO + NICO
ROBIN; romance needs BLUE BOX + DRESS-UP). So the CI dry run (task step 5) is NOT done yet.

**In flight:** GitHub run 40247085474 (ranking-pool-mine, dispatched 23:58 UTC, queued behind the nightly sound mining;
staging only): entries MADARA, SUNG JINWOO, NEZUKO, NICO ROBIN, YUTA, PAIN, ITACHI, BLUE BOX, MY DRESS-UP DARLING.

**Resume steps:**
1. When run 40247085474 is done: `python core/edit/ranking_pool.py pull-staged`, `review`, look at every sheet (and the
   clip): THAT character, no tag / logo / end card / subtitles, safe, eventful. Owner reviews (Claude may pre-screen).
2. `import --ids ... --reject ... --by owner`, `push-state`, then
   `GH_TOKEN=$(gh auth token -u drcompilationyt7-alt) node scripts/edit-pools-store.js push --preset ranking`.
3. `python core/edit/ranking_pool.py status` -> a topic shows ELIGIBLE (likely strongest_overall: Jinwoo / Madara / Gojo).
4. CI dry run: Actions -> Daily Shorts Creation -> type `ranking`, dry_run `true`, ranking_topic `<topic>`. Download the
   `renders` artifact -> `Desktop/review-shorts/edits/new-types/ranking_ci_dryrun_<topic>.mp4` + `.title.txt` /
   `.description.txt` (written next to the mp4 in `output/meme/work-ranking-*`).
5. Owner approves the CI video -> repo variable `DAILY_TYPE_WEIGHTS` add `ranking:1` (Saturday slot).
6. Open: Yuta still has 0 passing edits (needs deeper search); Toji / Yuji windows sum to 63 s with Gojo (cap the total at
   ~60 s in `pick_items` if the owner wants it shorter); the CI path of `assemble` has not run on GitHub yet.
