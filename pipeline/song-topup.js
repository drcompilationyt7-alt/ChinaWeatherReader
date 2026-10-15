/**
 * Automatic monthly top-up of the stem edit's song list (pipeline/meme-pipeline.js SONGS), no manual approval.
 * A candidate is auto-approved only if EVERY gate passes; every verdict is logged to memory/song-topup-YYYY-MM.json.
 *
 *   1 niche        ONLY K-pop, Chinese (C-pop / Mandopop / viral / C-drama OST) or J-pop / anime, with an identified clip SOURCE:
 *                  an anime that has an AniList entry (core/quiz/assets/
 *                  anime-list.json) and is not NSFW-flagged, or a known group (curated list / KPOP_MEMBERS) -- the same
 *                  source rules the edit pipeline uses (nsfwSets)
 *   2 popular      the official upload (best of several queries: the title + artist match with the most views, official / Topic / VEVO /
 *                  label channels first) has >= TOPUP_MIN_VIEWS views (default 1M), and ANY trend signal: >= 1 acapella / edit / amv
 *                  upload in 90 days, or official views >= 20M (popular-evergreen), or >= 100M, or a release under 120 days old
 *   3 producible   clean, sharp, unsubtitled SOURCES: a search-only dry run (yt-dlp metadata) must find >= TOPUP_MIN_CLIPS (12)
 *                  weighted sources: 1080p+ counts 1, 720p counts 0.5 (below that 0); titles / descriptions with sub / lyrics / AMV /
 *                  mirrored / cropped / reaction... are dropped; creditless / NCOP / NCED / 4K / Blu-ray / official channels get
 *                  x1.25. Then a sample check of 3 of the best: a 10 s low-res preview through core/pools/source_probe.py (burned-in
 *                  subtitles via edit_fx.text_bands, static watermark, borders, sharpness); >= 2 must be clean. The song audio must
 *                  exist (official upload 60-420 s). (A demucs sanity check on a 20 s excerpt is not run.)
 *   4 not a dup    not (an alias of) a song already in the curated / approved / retired lists, never region-blocked or
 *                  taken down in our own history, not rejected before
 *   5 limits       at most TOPUP_MAX_PER_MONTH (10) new songs a month; each enters on PROBATION (pickSong gives it a small
 *                  penalty until it has 2 scored uploads, Thompson sampling then keeps or starves it); an auto-added song
 *                  with >= TOPUP_RETIRE_AFTER (4) scored uploads whose weighted mean is clearly below the channel's is RETIRED
 *
 * File: config/song-candidates.json { approved: [...], retired: [...], rejected: [{name, at, why}] }
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const { execFile } = require('child_process');

const ROOT = path.join(__dirname, '..');
const FILE = () => path.resolve(ROOT, process.env.SONG_CANDIDATES_FILE || 'config/song-candidates.json');
const MEM = () => path.resolve(ROOT, process.env.MEMORY_DIR || 'memory');
const num = (k, d) => (process.env[k] != null && process.env[k] !== '' ? Number(process.env[k]) : d);
const cfg = () => ({
  minViews: num('TOPUP_MIN_VIEWS', 1000000), evergreenViews: num('TOPUP_EVERGREEN_VIEWS', 100000000), minTrend: num('TOPUP_MIN_TREND', 1), trendDays: num('TOPUP_TREND_DAYS', 90), popularViews: num('TOPUP_POPULAR_VIEWS', 20000000),
  minClips: num('TOPUP_MIN_CLIPS', 12), maxPerMonth: num('TOPUP_MAX_PER_MONTH', 10), retireAfter: num('TOPUP_RETIRE_AFTER', 4),
  retireMargin: num('TOPUP_RETIRE_MARGIN', 0.5), minSustained: num('TOPUP_MIN_SUSTAINED', 6), sustainDays: num('TOPUP_SUSTAIN_DAYS', 180), newDays: num('TOPUP_NEW_DAYS', 120), llmAsk: num('TOPUP_ASK', 25),
});
const norm = s => String(s || '').toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9぀-ヿ一-鿿가-힯]+/g, ' ').trim();
const readJson = (f, d) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return d; } };
const loadFile = () => ({ approved: [], retired: [], rejected: [], ...readJson(FILE(), {}) });
const saveFile = f => { fs.mkdirSync(path.dirname(FILE()), { recursive: true }); fs.writeFileSync(FILE(), JSON.stringify(f, null, 1)); };

// distinct yt-dlp error lines (bot check, JS challenge, rate limit...), each printed once so a failing runner is diagnosable
const seenYtErr = new Set();
function logYtErrors(q, stderr) {
  for (const l of String(stderr || '').split('\n')) {
    const m = l.replace(/\[[^\]]*\]\s*[\w-]{11}:\s*/, '').trim();
    if (/ERROR|WARNING.*(JS|challenge|runtime|formats)|Sign in|bot|429/i.test(m) && !seenYtErr.has(m.slice(0, 90)) && seenYtErr.size < 12) { seenYtErr.add(m.slice(0, 90)); console.log(`  [yt-dlp] "${q.slice(0, 40)}": ${m.slice(0, 220)}`); }
  }
}

// ─── YouTube search (yt-dlp, metadata only) ─────────────────────────────────────────────────────
function ytSearch(query, { n = 10, byDate = false } = {}) {
  const py = process.env.MEME_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  // same call style as core/meme/clip_finder.py (works on the runner): --force-ipv4, retries, a private cookie-jar copy per call
  // (yt-dlp rewrites the jar on exit; parallel calls on one file corrupt it), deno (default) or the JS runtime in YT_JS_RUNTIMES
  let jar = null;
  if (process.env.YT_COOKIES && fs.existsSync(process.env.YT_COOKIES)) {
    jar = path.join(os.tmpdir(), `topup_cookies_${process.pid}_${Math.random().toString(36).slice(2)}.txt`);
    fs.copyFileSync(process.env.YT_COOKIES, jar);
  }
  const args = ['-m', 'yt_dlp', '--dump-json', '--skip-download', '--ignore-errors', '--socket-timeout', '20', '--retries', '2', '--force-ipv4',
    ...(process.env.YT_JS_RUNTIMES ? ['--js-runtimes', process.env.YT_JS_RUNTIMES] : []),
    ...(jar ? ['--cookies', jar] : []),
    `${byDate ? 'ytsearchdate' : 'ytsearch'}${n}:${query}`];
  return new Promise(resolve => {
    execFile(py, args, { timeout: 240000, maxBuffer: 64 * 1024 * 1024, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }, (err, stdout, stderr) => {
      const out = [];
      logYtErrors(query, stderr);
      if (jar) { try { fs.unlinkSync(jar); } catch {} }
      for (const l of String(stdout || '').split('\n')) {
        try {
          const j = JSON.parse(l);
          out.push({ id: j.id, title: j.title || '', channel: j.channel || j.uploader || '', views: j.view_count || 0, duration: j.duration || 0,
            uploadDate: j.upload_date || null, height: Math.max(j.height || 0, ...(j.formats || []).map(x => x.height || 0)),   // best listed format (the default pick can be 360p)
            description: String(j.description || '').slice(0, 400),
            tags: (j.tags || []).slice(0, 15), verified: !!j.channel_is_verified });
        } catch {}
      }
      resolve(out);
    });
  });
}

// Owner's hard resource limits for preview downloads / probes: ONE at a time, IDLE priority, 2 threads, and only started when
// free RAM >= PROBE_MIN_FREE_GB (4) and CPU < PROBE_MAX_CPU (60 %).
let probeChain = Promise.resolve();
function cpuBusy(ms = 1000) {
  const snap = () => os.cpus().reduce((a, c) => { const t = Object.values(c.times).reduce((x, y) => x + y, 0); return [a[0] + t, a[1] + c.times.idle]; }, [0, 0]);
  const [t0, i0] = snap();
  return new Promise(res => setTimeout(() => { const [t1, i1] = snap(); res(t1 > t0 ? 100 * (1 - (i1 - i0) / (t1 - t0)) : 0); }, ms));
}
async function waitForResources(maxWaitMs = num('PROBE_WAIT_MIN', 30) * 60000) {
  const minFree = num('PROBE_MIN_FREE_GB', 4) * 1024 ** 3, maxCpu = num('PROBE_MAX_CPU', 60);
  const t0 = Date.now();
  for (;;) {
    const cpu = await cpuBusy();
    if (os.freemem() >= minFree && cpu < maxCpu) return true;
    if (Date.now() - t0 > maxWaitMs) return false;
    await new Promise(r => setTimeout(r, 15000));
  }
}
function probeOnce(r) {
  const py = process.env.MEME_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  return new Promise(resolve => {
    const child = execFile(py, [path.join(ROOT, 'core', 'pools', 'source_probe.py'), '--id', r.id, '--start', String(Math.min(40, Math.floor((r.duration || 80) / 3)))],
      { timeout: 420000, env: { ...process.env, PYTHONIOENCODING: 'utf-8', OMP_NUM_THREADS: '2', OPENBLAS_NUM_THREADS: '2', MKL_NUM_THREADS: '2', PROBE_THREADS: '2' } }, (err, stdout, stderr) => {
        try { resolve(JSON.parse(String(stdout).trim().split(String.fromCharCode(10)).pop())); } catch {
          const why = String(stderr || (err && err.message) || '').trim().split(String.fromCharCode(10)).pop().slice(0, 120);   // e.g. ModuleNotFoundError: cv2
          resolve({ ok: false, clean: false, reasons: ['probe failed' + (why ? ': ' + why : '')] });
        }
      });
    try { os.setPriority(child.pid, os.constants.priority.PRIORITY_LOW); } catch {}   // idle class on Windows, nice 19 elsewhere (children inherit it)
  });
}
/** one 10 s low-res preview of a candidate source through core/pools/source_probe.py (serialised, idle priority, resource-gated) */
function probeSource(r) {
  const run = probeChain.then(async () => {
    if (!(await waitForResources())) return { ok: false, clean: false, reasons: ['no free resources (RAM < 4 GB or CPU >= 60 %): not probed'] };
    return probeOnce(r);
  });
  probeChain = run.catch(() => {});
  return run;
}

// ─── gates (pure given the deps) ───────────────────────────────────────────────────────────────
function knownSets() {
  const al = readJson(path.join(ROOT, 'core', 'quiz', 'assets', 'anime-list.json'), {});
  const list = al.anime || Object.values(al)[0] || [];
  const shows = new Map();
  for (const a of list) for (const t of [a.title, a.romaji]) if (norm(t)) shows.set(norm(t), { nsfw: !!a.nsfw });
  return shows;
}

// Live AniList lookup (GraphQL, search by title) for shows missing from the local snapshot; cached in memory/anilist-cache.json
const ANILIST_Q = 'query($s:String){Page(perPage:6){media(search:$s,type:ANIME,sort:SEARCH_MATCH){id title{romaji english native} synonyms isAdult genres}}}';
let aniCache = null;
const aniFile = () => path.join(MEM(), 'anilist-cache.json');
async function anilistLookup(title, fetchFn = globalThis.fetch) {
  const key = norm(title);
  if (!key) return null;
  if (!aniCache) aniCache = readJson(aniFile(), {});
  if (key in aniCache) return aniCache[key];
  let media;
  try {
    const r = await fetchFn('https://graphql.anilist.co', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify({ query: ANILIST_Q, variables: { s: String(title) } }) });
    if (!r.ok) return null;   // transient failure: not cached
    media = (((await r.json()).data || {}).Page || {}).media || [];
  } catch { return null; }
  const names = m => [m.title.romaji, m.title.english, m.title.native, ...(m.synonyms || [])].map(norm).filter(Boolean);
  const match = media.filter(m => names(m).some(n => n === key || (key.length >= 6 && (n.includes(key) || (n.length >= 6 && key.includes(n))))));
  const m = match[0];
  const hit = m ? { id: m.id, title: m.title.english || m.title.romaji, nsfw: match.some(x => x.isAdult || (x.genres || []).includes('Hentai')) } : null;
  aniCache[key] = hit;
  try { fs.mkdirSync(MEM(), { recursive: true }); fs.writeFileSync(aniFile(), JSON.stringify(aniCache, null, 1)); } catch {}
  return hit;
}

// the owner's niche: K-pop, Chinese (C-pop / Mandopop / viral / C-drama OST) and J-pop / anime. Nothing else.
const NICHE = ['anime', 'jpop', 'kpop', 'chinese'];
async function gateNiche(c, { shows, groups, skip }, deps = {}) {
  if (c.theme === 'cpop') c.theme = 'chinese';
  if (!NICHE.includes(c.theme)) return { pass: false, why: `theme "${c.theme}" is not K-pop / Chinese / J-pop / anime` };
  if (!c.source) return { pass: false, why: 'no clip source identified' };
  const src = norm(c.source);
  const live = async () => { const f = deps.anilist === undefined ? anilistLookup : deps.anilist; if (!f) return null; for (const a of [c.source, ...(c.aliases || [])].slice(0, 2)) { const h = await f(a); if (h) return h; } return null; };
  if (c.kind === 'anime' && c.theme === 'chinese' && !shows.get(src) && !(await live())) {   // donghua / Chinese animation not in AniList: same proof as a group
    return { pass: null, why: `Chinese animation source "${c.source}" (decided by the popularity and clip-quality gates)` };
  }
  if (c.kind === 'anime') {
    let hit = shows.get(src) || [...(c.aliases || []), c.source].map(norm).map(a => shows.get(a)).find(Boolean);
    let how = 'known';
    if (!hit) { hit = await live(); how = 'found live on AniList'; }
    if (!hit) return { pass: false, why: `"${c.source}" has no AniList entry (anime-list.json snapshot and live AniList search)` };
    if (hit.nsfw || skip.has(src)) return { pass: false, why: `"${c.source}" is NSFW / skip-listed` };
    return { pass: true, why: `anime source "${c.source}" ${how}, not NSFW` };
  }
  if (c.kind === 'group') {
    if (groups.has(src)) return { pass: true, why: `group "${c.source}" is in the known list` };
    return { pass: null, why: `group "${c.source}" not in the known list (decided by the popularity gate)` };
  }
  if (c.kind === 'drama') {   // C-drama / donghua-style source without an AniList entry: decided by the popularity + clip-quality gates
    return { pass: null, why: `drama source "${c.source}" (decided by the popularity and clip-quality gates)` };
  }
  return { pass: false, why: `unknown kind "${c.kind}"` };
}

const tokens = s => norm(s).split(' ').filter(w => w.length > 1);
const BAD_AUDIO = /\b(cover|reaction|live|remix|nightcore|lyrics?|piano|instrumental|karaoke|8d|slowed|sped|acapella|a cappella|edit|amv|tutorial|dance practice|teaser|trailer|shorts?)\b/i;
const OFFICIAL_CH = /official|topic|records|music|entertainment|\bvevo\b|sony|avex|universal|warner|hybe|jyp|smtown|yg|label|pony canyon|kadokawa|aniplex|lantis|king record|tencent|netease|emi/i;
/** the upload that matches song title AND artist (in title or channel); official / Topic / VEVO / label / artist channels first; the MOST views wins */
function findOfficial(c, results) {
  const want = tokens(c.name);
  const artToks = tokens(c.artist || '').filter(w => w.length > 2).slice(0, 3);
  const nativeHit = r => c.native && r.title.includes(c.native);
  const artistHit = r => !artToks.length || artToks.some(w => norm(r.title + ' ' + r.channel).includes(w));
  const ok = results.filter(r => r.duration >= 60 && r.duration <= 480 && (want.every(w => norm(r.title).includes(w)) || nativeHit(r)) && !BAD_AUDIO.test(r.title) && artistHit(r));
  const official = ok.filter(r => (artToks.length && norm(r.channel).includes(artToks[0])) || OFFICIAL_CH.test(r.channel) || r.verified);
  return (official.length ? official : ok).sort((x, y) => y.views - x.views)[0] || null;
}

async function gatePopular(c, deps, k) {
  const a = c.artist || '';
  const seenO = new Map();
  for (const q of [c.query, `${a} ${c.name} official MV`, `${a} ${c.name} official audio`, `${a} ${c.name} music video`, `${c.name} ${a}`].map(x => x.replace(/ +/g, ' ').trim())) {
    for (const r of (await deps.search(q, { n: 8 })) || []) if (r.id && !seenO.has(r.id)) seenO.set(r.id, r);
  }
  const off = findOfficial(c, [...seenO.values()]);
  if (off) console.log(`  [official] ${c.name} / ${a}: ${off.id} "${String(off.title).slice(0, 60)}" by ${off.channel} (${off.views} views) out of ${seenO.size} results`);
  if (!off) return { pass: false, why: 'no matching official upload found on YouTube', official: null };
  if (off.views < k.minViews) return { pass: false, why: `official upload has ${off.views} views < ${k.minViews}`, official: off };
  const day = n => new Date(deps.now().getTime() - n * 86400000).toISOString().slice(0, 10).replace(/-/g, '');
  const since = day(k.trendDays);
  // (yt-dlp's date-sorted search returns nothing: use relevance search and filter on the upload date)
  const seenT = new Map();
  for (const q of [`${c.name} ${c.artist || ''} edit`.replace(/ +/g, ' '), `${c.name} ${c.artist || ''} acapella`.replace(/ +/g, ' ')]) {
    for (const r of await deps.search(q, { n: 20 })) if (r.id && !seenT.has(r.id)) seenT.set(r.id, r);
  }
  const isEdit = r => r.uploadDate && (tokens(c.name).every(w => norm(r.title).includes(w)) || (c.native && r.title.includes(c.native))) && /acapella|a cappella|edit|amv/i.test(r.title);
  const hits = [...seenT.values()].filter(r => isEdit(r) && r.uploadDate >= since);
  const sustained = [...seenT.values()].filter(r => isEdit(r) && r.uploadDate >= day(k.sustainDays));
  const fresh = off.uploadDate && off.uploadDate >= day(k.newDays);
  const sig = hits.length >= k.minTrend ? `${hits.length} edit / acapella uploads in ${k.trendDays} days`
    : off.views >= k.evergreenViews ? `evergreen hit (${Math.round(off.views / 1e6)}M official views, popularity alone)`
    : off.views >= k.popularViews ? `popular-evergreen (${Math.round(off.views / 1e6)}M official views, on probation)`
    : fresh ? `new release (official upload ${off.uploadDate}, ${off.views} views)`
    : sustained.length >= k.minSustained ? `${sustained.length} edit / acapella uploads in ${k.sustainDays} days (sustained)` : null;
  const detail = { trend: hits.length, trend180: sustained.length, fresh: !!fresh };
  if (!sig) return { pass: false, why: `trend: ${hits.length} acapella / edit uploads in ${k.trendDays} days (< ${k.minTrend}), not a new release, under ${Math.round(k.popularViews / 1e6)}M views`, official: off, ...detail };
  return { pass: true, why: `official ${off.views} views (${off.id}); ${sig}`, official: off, ...detail };
}

// other people's edits, subtitled / lyric uploads, mirrored or cropped re-uploads, shorts: not usable as source material
const BAD_SRC = /\b(eng(lish)?[ .-]?subs?|subs?|subbed|hardsub(bed)?|subtitles?|sub[ .-]?indo|lyrics?|lyric video|romaji|karaoke|amv|mmv|mmd|edit|edits|fmv|fanmade|fan[ .-]?made|reaction|mirror(ed)?|flipped|cropped|zoomed|vertical|reupload|re-upload|tiktok|shorts?|nightcore|slowed|sped[ .-]?up|compilation)\b|字幕|歌詞|자막|가사/i;
const PREFERRED = /\b(creditless|non[ .-]?credit(ed)?|nc[ .-]?(op|ed)|ncop|nced|4k|2160p|blu[ .-]?ray|bd|remaster(ed)?|official)\b|crunchyroll|aniplex|toho|funimation|netflix|muse asia|hybe|jyp|smtown|yg entertainment|1thek|mnet|studio/i;
function sourceWeight(r) {
  const text = [r.title, r.description, (r.tags || []).join(' ')].join(' ');
  if (BAD_SRC.test(text)) return { w: 0, why: 'edit / subtitled / lyric / mirrored upload' };
  if (r.duration <= 0 || r.duration > 600) return { w: 0, why: 'duration' };
  const pref = PREFERRED.test(r.title + ' ' + r.channel) || r.verified;
  if (r.views < (pref ? 5000 : 30000)) return { w: 0, why: 'views' };
  const base = r.height >= 1080 ? 1 : r.height >= 720 ? 0.5 : 0;
  return base ? { w: base * (pref ? 1.25 : 1), pref, why: '' } : { w: 0, why: `${r.height || '?'}p` };
}

async function gateProducible(c, official, deps, k) {
  if (!official) return { pass: false, why: 'song audio unavailable (no official upload)' };
  const queries = c.kind === 'drama'
    ? [`${c.source} 4K`, `${c.source} official clips 1080p`, `${c.source} OST MV official 4K`, `${c.source} drama scenes 1080p`]
    : c.kind === 'anime'
    ? [`${c.source} 4K`, `${c.source} creditless NCOP NCED 4K`, `${c.source} anime scenes 1080p`, `${c.source} official trailer PV 1080p`]
    : [`${c.source} stage 4K`, `${c.source} MV 4K`, `${c.source} live performance 1080p`, `${c.source} variety 1080p`];
  // SOURCE level (owner): footage of the anime / drama / group in general counts, not only of this song
  const seen = new Map();
  for (const q of queries) for (const r of await deps.search(q, { n: 15 })) if (r.id && !seen.has(r.id)) seen.set(r.id, r);
  const scored = [...seen.values()].map(r => ({ r, ...sourceWeight(r) })).filter(x => x.w > 0).sort((a, b) => b.w - a.w);
  const weight = scored.reduce((a, x) => a + x.w, 0);
  const hd = scored.filter(x => x.r.height >= 1080).length;
  if (weight < k.minClips) return { pass: false, why: `source quality: weighted ${weight.toFixed(1)} < ${k.minClips} (${hd} at 1080p+, ${scored.length - hd} at 720p, of ${seen.size} found)` };
  // light sample check on the best three: preview -> subtitles / watermark / borders / sharpness
  const sample = scored.slice(0, 3).map(x => x.r);
  const probes = deps.probe ? await Promise.all(sample.map(r => deps.probe(r).catch(e => ({ ok: false, clean: false, reasons: [String(e.message || e).slice(0, 80)] })))) : null;
  if (probes) {
    const clean = probes.filter(p => p && p.clean).length;
    const need = Math.min(2, sample.length);
    if (clean < need) {
      return { pass: false, why: `sample check: ${clean}/${sample.length} clean previews (${probes.map((p, i) => `${sample[i].id}: ${(p && p.reasons || ['?']).join(', ') || 'clean'}`).join('; ')})`, probes };
    }
  }
  return { pass: true, why: `weighted sources ${weight.toFixed(1)} (${hd} at 1080p+), audio ${official.id} (${official.duration}s)${probes ? `, ${probes.filter(p => p && p.clean).length}/${sample.length} sample previews clean` : ', no sample check'}`, probes };
}

function gateDuplicate(c, { have, aliasOwner, blocked, rejected }) {
  const n = norm(c.name);
  if (have.has(n)) return { pass: false, why: 'already in the song list' };
  const owner = aliasOwner.get(n);
  if (owner) return { pass: false, why: `alias of "${owner}"` };
  if (blocked.has(n)) return { pass: false, why: 'region-blocked / taken down before' };
  if (rejected.has(n)) return { pass: false, why: 'rejected in an earlier month' };
  return { pass: true, why: 'new' };
}

/** names whose own uploads were blocked in many countries or are listed in config/song-blocklist.json */
function blockedSongs(stats, history) {
  const out = new Set();
  const byId = new Map(((stats && stats.videos) || []).map(v => [v.videoId, v]));
  for (const u of history.uploads || []) {
    const v = byId.get(u.videoId);
    if (v && ((v.regionBlocked || 0) >= 30 || v.takedown || v.copyrightStrike)) out.add(norm(u.song));
  }
  for (const n of readJson(path.join(ROOT, 'config', 'song-blocklist.json'), [])) out.add(norm(n));
  return out;
}

/**
 * Run the gates over candidates. deps: { search(q, opts), now() }.
 * @returns {{approved: object[], report: object[]}}
 */
async function evaluate(candidates, deps, ctx) {
  const k = ctx.k || cfg();
  const room = Math.max(0, k.maxPerMonth - ctx.approvedThisMonth);
  const judge = async c => {
    const row = { name: c.name, artist: c.artist || null, theme: c.theme, source: c.source, query: c.query, gates: {}, verdict: 'rejected' };
    const g1 = await gateNiche(c, ctx, deps);
    row.gates.niche = g1;
    const g4 = gateDuplicate(c, ctx);
    row.gates.duplicate = g4;
    if (g1.pass === false || !g4.pass) { row.why = (g1.pass === false ? g1 : g4).why; return row; }
    const g2 = await gatePopular(c, deps, k);
    row.gates.popular = g2;
    if (!g2.pass) { row.why = g2.why; return row; }
    if (g1.pass === null) row.gates.niche = { pass: true, why: `${g1.why}; official channel + ${g2.official.views} views accepted` };
    const g3 = await gateProducible(c, g2.official, deps, k);
    row.gates.producible = g3;
    if (!g3.pass) { row.why = g3.why; return row; }
    row.verdict = 'approved';
    row.why = 'all gates passed';
    return row;
  };
  // candidates are judged in parallel (deps.concurrency, default 1); the monthly limit is applied afterwards, in candidate order
  const report = new Array(candidates.length);
  let next = 0, passed = 0;
  const worker = async () => {
    while (next < candidates.length) {
      const i = next++;
      if (passed >= room) { report[i] = { name: candidates[i].name, source: candidates[i].source, gates: {}, verdict: 'deferred', why: `monthly limit (${k.maxPerMonth}) already filled` }; continue; }
      try { report[i] = await judge(candidates[i]); if (report[i].verdict === 'approved') passed++; } catch (e) {
        report[i] = { name: candidates[i].name, source: candidates[i].source, gates: {}, verdict: 'rejected', why: `error: ${String(e.message || e).slice(0, 100)}` };
      }
    }
  };
  await Promise.all(Array.from({ length: Math.max(1, deps.concurrency || 1) }, worker));
  const approved = [];
  candidates.forEach((c, i) => {
    const row = report[i];
    if (row.verdict !== 'approved') return;
    if (approved.length >= room) { row.verdict = 'deferred'; row.why = `monthly limit (${k.maxPerMonth}) reached`; return; }
    const off = row.gates.popular.official;
    approved.push({ name: String(c.name).toLowerCase(), query: c.query, artist: c.artist || undefined, theme: c.theme, source: c.source,
      kind: c.kind === 'drama' ? 'group' : c.kind, aliases: (c.aliases || []).map(a => String(a).toLowerCase()).slice(0, 10), officialId: off.id,
      probation: true, approvedAt: deps.now().toISOString().slice(0, 10) });
  });
  return { approved, report };
}

/** Auto-added songs that stayed clearly below the channel average over enough scored uploads. */
function findLosers(file, stats, history, k = cfg()) {
  const byId = new Map(((stats && stats.videos) || []).map(v => [v.videoId, v]));
  const rows = (history.uploads || []).map(u => ({ song: u.song, s: byId.get(u.videoId) })).filter(x => x.s && typeof x.s.lscore === 'number' && (x.s.ageDays || 0) >= 7);
  if (!rows.length) return [];
  const mean = a => a.reduce((x, y) => x + y, 0) / a.length;
  const channel = mean(rows.map(r => r.s.lscore));
  const out = [];
  for (const s of file.approved) {
    const mine = rows.filter(r => r.song === s.name);
    if (mine.length >= k.retireAfter && mean(mine.map(r => r.s.lscore)) < channel - k.retireMargin) {
      out.push({ name: s.name, n: mine.length, mean: +mean(mine.map(r => r.s.lscore)).toFixed(2), channel: +channel.toFixed(2) });
    }
  }
  return out;
}

/** One monthly run. deps.propose() -> [candidate]. Writes config/song-candidates.json and memory/song-topup-YYYY-MM.json. */
async function runTopup(deps, { write = true } = {}) {
  const k = cfg();
  const now = deps.now();
  const month = now.toISOString().slice(0, 7);
  const file = loadFile();
  const { SONGS } = require('./meme-pipeline');
  let extra = [];
  try { extra = require('./edit-pipeline').EXTRA_SONGS || []; } catch {}
  const history = readJson(path.join(MEM(), 'meme-history.json'), { uploads: [] });
  let stats = deps.stats || null;
  if (!stats) { try { stats = require('../core/performance-tracker').loadStats(); } catch {} }

  // retire first, so a retired song stops being picked and its slot opens up
  const losers = findLosers(file, stats, history, k);
  for (const l of losers) {
    const s = file.approved.find(x => x.name === l.name);
    file.approved = file.approved.filter(x => x.name !== l.name);
    file.retired.push({ ...s, retiredAt: now.toISOString().slice(0, 10), why: `${l.n} uploads, mean ${l.mean} vs channel ${l.channel}` });
  }
  // after a day on probation with 2+ scored uploads the flag stays informational (pickSong ignores it then)

  const all = [...SONGS, ...extra, ...file.approved, ...file.retired];
  const have = new Set(all.map(s => norm(s.name)));
  const aliasOwner = new Map();
  for (const s of all) for (const a of s.aliases || []) if (!have.has(norm(a))) aliasOwner.set(norm(a), s.name);
  const groups = new Set(all.filter(s => s.kind === 'group').map(s => norm(s.source)));
  for (const g of Object.keys(require('./edit-pipeline').KPOP_MEMBERS || {})) groups.add(norm(g));
  const ctx = {
    k, shows: knownSets(), groups, skip: new Set(), have, aliasOwner, blocked: blockedSongs(stats, history),
    rejected: new Set(file.rejected.map(r => norm(r.name))),
    approvedThisMonth: file.approved.filter(s => String(s.approvedAt || '').startsWith(month)).length,
  };
  const candidates = (await deps.propose(k.llmAsk, [...have])) || [];
  const { approved, report } = await evaluate(candidates, deps, ctx);
  file.approved.push(...approved);
  // infrastructure failures (YouTube blocked: nothing found, nothing downloadable) say nothing about the song: not remembered
  const infra = r => /weighted 0.0 <|no matching official upload|^error:|preview unavailable|probe failed|no free resources/.test(r.why || '');
  for (const r of report) if (r.verdict === 'rejected' && !infra(r)) file.rejected.push({ name: r.name, at: now.toISOString().slice(0, 10), why: r.why });
  file.rejected = file.rejected.slice(-300);
  const out = { month, at: now.toISOString(), limits: k, candidates: candidates.length, approved: approved.map(a => a.name),
    retired: losers, approvedThisMonthBefore: ctx.approvedThisMonth, report };
  if (write) {
    saveFile(file);
    fs.mkdirSync(MEM(), { recursive: true });
    fs.writeFileSync(path.join(MEM(), `song-topup-${month}.json`), JSON.stringify(out, null, 1));
  }
  return out;
}

module.exports = { anilistLookup, knownSets, sourceWeight, probeSource, runTopup, evaluate, findLosers, gateNiche, gateDuplicate, gatePopular, gateProducible, findOfficial, blockedSongs, ytSearch, cfg, norm, loadFile, FILE };
