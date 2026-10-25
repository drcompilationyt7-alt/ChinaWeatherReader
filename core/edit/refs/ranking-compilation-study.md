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

### Segment picker (`pick_windows`)
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
- Segment length stays ~21 s per entry until the owner says whether "long" means longer.

### Low-memory mode (owner: "can u do the last step even if the little ram we have")
- `RANKING_MIN_FREE_GB=1.5`: pause when free RAM falls below it, resume when it recovers.
- One heavy process, ≤ 4 threads, IDLE priority, models loaded once and lazily, 2 fps frames at 448 px.

## Prototype run
(appended after the run)
