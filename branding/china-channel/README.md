# Mainland Unpacked (channel 3)

- **Name:** Mainland Unpacked
- **Handle:** @mainlandunpacked (free on 2026-10-12). Backups: @insidechinanow, @redlinechina.
- **Avatar:** option B, an open box with Shanghai's Pearl Tower rising out of it ("China, unpacked"), on a red gradient.
  - The red background fills the circle so it holds up in both dark and light mode, and the mark reads at 40 px.
- **Banner:** a red sunset, the Pudong skyline (Jin Mao, SWFC, Shanghai Tower, Pearl Tower), the title, a schedule pill and topic chips.
  - Everything that matters sits inside the 1546×423 phone band; the text stays inside 1235×338.
- **Files:** `avatar.png` (800×800), `banner.png` (2560×1440), `watermark.png` (150×150, transparent; Studio → Customization → Branding → Video watermark).
- Regenerate with `python scripts/make-china-branding.py branding/china-channel`.

## Channel description
China, unpacked in 60 seconds: EVs, megabuildings, 3D chips, Huawei, and cities that grow overnight. Also the side the brochures skip: 996 work culture, gaokao pressure and student life.
Every week there's a full recap of *An Exciting Offer* (令人心动的offer), China's internship reality show where top grads fight for a real job.
New Shorts daily · Long-form recap weekly.

## Content plan
**Shorts (7 per week, one published each day).**
- Rotation: tech (EVs, towers, chips, Huawei, city growth), then society (996, gaokao, student pressure).
- Each one is about 75% motion graphics and about 25% B-roll, with a TTS voiceover and burned-in captions.

**Long form (1 per week, 20–30 min).**
- One episode of *An Exciting Offer* per video, starting with S1E1.
- The source episodes run 1.5–2 h. Pick the strongest 6–10 beats: the tasks, conflicts, evaluations and eliminations.
- Each beat gets a short clip, then narrated commentary over motion graphics: an intern scoreboard, the company/role explainer, a "who's winning" bar chart, and cultural context.
- Keep source clips short, always with commentary over or around them. The clips should be a minority of the runtime (Content ID / reused-content risk).

## Weekly routine (automated uploads)
1. Say "generate". Claude researches and fact-checks 7 topics and writes one script JSON per Short in `content/china/week-<date>/`.
   - Each script lists the scenes (hook, photo, counter, bars, vs, stack, quote, list, timeline, outro), the narration and Wikimedia photos.
   - `python core/china/build_week.py content/china/week-<date>` renders them to `output/china/week-<date>/` and writes `week.json`. Run it with the ttsvenv Python, KOKORO_DIR=%TEMP%/kokoro.
   - **Rendering is done with Remotion** (`remotion/`, pinned 4.0.529; free for individuals and teams up to 3).
     - `core/china/prep_short.py` makes the voice (Kokoro am_puck, the one channel voice for every video), the word timings (faster-whisper), the footage (YouTube Creative Commons, licence-checked, plus Pixabay / Pexels via core/china/stock.py), the photos (Wikimedia), the SFX (Kenney CC0 + Mixkit, fetched by `core/china/sfx.py`, never committed) and the music (Kevin MacLeod CC BY, credited).
     - Every animation and SFX is cued to a spoken word (`cue` / `cueEnd` in the script JSON).
     - Preview a Short live with `cd remotion && npm run studio`.
2. The owner reviews the samples.
3. `node scripts/china/push-week.js <folder> --dry` checks everything and prints the schedule.
4. The same command without `--dry` pushes the week as a DRAFT release `china-week-<date>` (gh account: drcompilationyt7-alt).
   - Drafts are invisible to the public, even though the repo is public.
   - Each file must be 2 GB or less.
5. `.github/workflows/china-channel.yml` runs daily at 03:37 UTC, with a backup run at 09:37.
   - It uploads that day's Short, scheduled for 14:00 UTC, and the long-form on its day (Saturday by default), scheduled for 15:00 UTC.
   - It records the uploads in `memory/china-channel/upload-history.json` and deletes the release once the week is done.

**Token:** run `gh auth switch -u drcompilationyt7-alt`, then `node scripts/china/setup-token.js`, and pick the Mainland Unpacked channel in the browser.

**Secrets:** `CHINA_YOUTUBE_CLIENT_ID`, `CHINA_YOUTUBE_CLIENT_SECRET`, `CHINA_YOUTUBE_REFRESH_TOKEN`.
- The client id and secret are the same OAuth client the other two channels use.
- **Repo variable:** `CHINA_CHANNEL_ID`. The uploader refuses to post if the token belongs to a different channel.

## YouTube API quota
- One Google Cloud project, shared with Zero Yen Otaku and Asian Pop Quiz: 10,000 units a day.
- An upload costs about 1,600 units; a thumbnail costs 50.
- The workflow uploads at most 2 videos per run. A normal day costs about 4,950 units across the three channels, and the long-form day about 6,600.
- **Videos over 15 min and custom thumbnails need a phone-verified channel** (youtube.com/verify).
