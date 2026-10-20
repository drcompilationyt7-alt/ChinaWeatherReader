/**
 * Candidate sources for the song top-up (pipeline/song-topup.js): tolerant LLM JSON in small batches, a hand-listed seed list that
 * always works, and trending YouTube scrapes. Pure given the injected ask() / search(); no network of its own.
 */
const norm = s => String(s || '').toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9぀-ヿ一-鿿가-힯]+/g, ' ').trim();

/** Parse model output that may be fenced, truncated or have trailing commas. Returns the array of song objects (possibly partial). */
function parseSongs(text) {
  if (!text) return [];
  if (typeof text === 'object') return Array.isArray(text) ? text : (Array.isArray(text.songs) ? text.songs : []);
  const s = String(text).replace(/```(json)?/gi, '');
  const clean = t => t.replace(/,\s*([}\]])/g, '$1');
  for (const m of [s.match(/\{[\s\S]*\}/), s.match(/\[[\s\S]*\]/)]) {
    if (!m) continue;
    for (const t of [m[0], clean(m[0])]) {
      try { const j = JSON.parse(t); const a = Array.isArray(j) ? j : j && j.songs; if (Array.isArray(a)) return a; } catch {}
    }
  }
  // salvage: every complete {...} object inside the songs array; a truncated last one is cut off
  const from = s.search(/"songs"\s*:\s*\[/);
  let i = from >= 0 ? s.indexOf('[', from) + 1 : s.indexOf('[') + 1;
  const out = [];
  let depth = 0, start = -1, str = false, esc = false;
  for (; i < s.length; i++) {
    const ch = s[i];
    if (str) { if (esc) esc = false; else if (ch === '\\') esc = true; else if (ch === '"') str = false; continue; }
    if (ch === '"') str = true;
    else if (ch === '{') { if (depth++ === 0) start = i; }
    else if (ch === '}' && depth > 0 && --depth === 0) { try { out.push(JSON.parse(clean(s.slice(start, i + 1)))); } catch {} }
  }
  return out;
}
// drop template echoes and malformed rows
const validSong = s => s && typeof s === 'object' && s.name && s.query && !/[|]|short lowercase title|Artist Title/i.test(`${s.name} ${s.query} ${s.source}`);

const FOCI = [
  'anime openings / endings / inserts that are trending for edits right now',
  'K-pop songs (girl groups and boy groups) trending for edits right now',
  'J-pop / Vocaloid / Japanese rock songs trending for edits right now (not necessarily anime)',
  'Chinese songs (C-pop / Mandopop / C-drama OST / donghua) trending for edits right now',
];
const SYSTEM = 'You curate songs for an anime / J-pop / K-pop edit channel. Only real, existing songs; Japanese anime openings / endings / inserts, J-pop, K-pop and Chinese (C-pop / Mandopop / C-drama OST / donghua) only. Nothing else.';
const SHAPE = 'Return JSON {"songs":[{"name":"short lowercase title","query":"Artist Title official audio","artist":"Artist","native":"title in its native script for Chinese / Japanese / Korean songs, else omit","theme":"anime|jpop|kpop|chinese",'
  + '"source":"exact anime title (English or romaji), or the group / artist (official channel) for J-pop, K-pop and C-pop, or the C-drama / donghua title for a drama OST","kind":"anime|group|drama",'
  + '"aliases":["lowercase character / member names and native-script title that clip titles would mention"]}]}';

/**
 * Ask for SMALL batches (batch songs per call, one call per focus), tolerant parse, one "valid JSON only" retry per call, and
 * several rounds with a pause while a provider is overloaded. ask(system, user) -> text | object | null (may throw).
 */
/** "similar to hit X" foci from core/reflect/edit-insights.js similarSeeds(): [{feature, value, hit}] -> [{text, tag}] */
function similarFoci(similar = []) {
  return similar.map(x => ({
    tag: `similar to hit "${x.hit || x.value}" (${x.feature} ${x.value})`,
    text: x.feature === 'artist'
      ? `other songs by ${x.value} (our "${x.hit}" edit was a hit), or songs by artists with a very similar sound and fanbase`
      : `other songs from ${x.value} (openings, endings, inserts; our "${x.hit}" edit was a hit), or anime songs with the same vibe`,
  }));
}

async function askBatches(ask, have, { batch = 8, rounds = 3, pauseMs = 60000, log = () => {}, sleep = ms => new Promise(r => setTimeout(r, ms)), similar = [] } = {}) {
  const got = new Map();
  const done = new Set();
  // "similar to a hit" foci first (same niche rule, same gates), then the standing ones
  const foci = [...similarFoci(similar), ...FOCI.map(text => ({ text, tag: null }))];
  for (let round = 0; round < rounds && done.size < foci.length; round++) {
    if (round > 0) { log(`LLM round ${round + 1}: ${foci.length - done.size} call(s) still empty, waiting ${pauseMs / 1000}s`); await sleep(pauseMs); }
    for (let i = 0; i < foci.length; i++) {
      if (done.has(i)) continue;
      const known = [...have, ...got.keys()].slice(0, 120).join(', ');
      const user = `Name ${batch} songs: ${foci[i].text} (this month and last). Not these (already used): ${known}.\n${SHAPE}`;
      let rows = [];
      for (const extra of ['', '\n\nYour last answer was not valid JSON. Return valid JSON only, no commentary, no markdown.']) {
        try { rows = parseSongs(await ask(SYSTEM, user + extra)).filter(validSong); } catch (e) { log(`LLM call failed: ${String(e.message || e).slice(0, 80)}`); rows = []; }
        if (rows.length) break;
      }
      if (rows.length) { done.add(i); for (const r of rows) if (!got.has(norm(r.name))) got.set(norm(r.name), foci[i].tag ? { ...r, similarTo: foci[i].tag } : r); }
    }
  }
  return [...got.values()];
}

/** The 26 candidates hand-listed in the first preview (2026-10-12): a source that always works. The gates still decide. */
const SEEDS = (() => {
  const mk = (theme, name, artist, o = {}) => ({ name, artist, query: `${artist} ${name} official audio`, theme, source: artist, kind: 'group', aliases: [], ...o });
  const k = (n, a) => mk('kpop', n, a), j = (n, a, o) => mk('jpop', n, a, o), c = (n, a, o) => mk('chinese', n, a, o);
  return [
    k('sheesh', 'BABYMONSTER'), j('iris out', 'Kenshi Yonezu', { theme: 'anime', source: 'Chainsaw Man', kind: 'anime', aliases: ['denji', 'reze'] }),
    k('drip', 'BABYMONSTER'), k('armageddon', 'aespa'), k('crazy', 'LE SSERAFIM'), k('heya', 'IVE'), k('baddie', 'IVE'), k('rockstar', 'LISA'),
    k('gabriela', 'KATSEYE'), k('plot twist', 'TWS'), k('klaxon', '(G)I-DLE'), k('how sweet', 'NewJeans'), k('jump', 'BLACKPINK'), k('cherish', 'ILLIT'),
    c('qing tian', 'Jay Chou'), c('mojito', 'Jay Chou'), c('wu ji', 'Wang Yibo', { source: 'The Untamed', kind: 'drama' }), c('qing hua ci', 'Jay Chou'),
    j('show', 'Ado', { theme: 'anime', source: 'One Piece', kind: 'anime', aliases: ['uta'] }), j('usseewa', 'Ado'), j('pretender', 'Official HIGE DANdism'), j('lemon', 'Kenshi Yonezu'),
    j('kaiju no hanauta', 'Vaundy'), j('hakujitsu', 'King Gnu'),
    j('brave shine', 'Aimer', { theme: 'anime', source: 'Fate/stay night: Unlimited Blade Works', kind: 'anime', aliases: ['emiya', 'archer'] }), j('first love', 'Hikaru Utada'),
  ];
})();

const TREND_QUERIES = [
  ['kpop', 'kpop acapella edit'], ['kpop', 'kpop girl group acapella'], ['kpop', 'kpop boy group acapella edit'],
  ['anime', 'anime opening acapella edit'], ['anime', 'anime ending acapella'], ['jpop', 'jpop acapella edit'],
  ['chinese', 'cpop acapella edit'], ['chinese', 'chinese song acapella edit'],
];
/** "Artist - Song (acapella edit) #tags" -> {artist, song} or null */
function parseUploadTitle(title) {
  let t = String(title || '').replace(/#\S+/g, ' ').replace(/[\[(【「『][^\])】」』]*[\])】」』]/g, ' ');
  t = t.replace(/\b(a ?cappella|acapella|vocals? only|isolated vocals?|edit|amv|stems?|official|audio|mv|m\/v|lyrics?|slowed|reverb|sped up|tiktok|version)\b/gi, ' ').replace(/\s+/g, ' ').trim();
  const m = t.match(/^(.{2,40}?)\s+[-–—|:]\s+(.{2,50})$/);
  if (!m) return null;
  const artist = m[1].trim(), song = m[2].replace(/\s+[-–—|:].*$/, '').trim();
  if (!/[A-Za-z0-9぀-ヿ一-鿿가-힯]/.test(song) || song.split(' ').length > 6 || song.length < 2) return null;
  return { artist, song };
}
/** Trending scrape: acapella / edit uploads of the last `days` days -> candidate songs, most-uploaded first. search(q, {n}) -> results. */
async function trendingCandidates(search, { days = 60, now = () => new Date(), max = 20, shows = new Map(), log = () => {} } = {}) {
  const since = new Date(now().getTime() - days * 86400000).toISOString().slice(0, 10).replace(/-/g, '');
  const found = new Map();
  for (const [theme, q] of TREND_QUERIES) {
    let res = [];
    try { res = (await search(q, { n: 25 })) || []; } catch (e) { log(`trend search failed: ${String(e.message || e).slice(0, 60)}`); }
    for (const r of res) {
      if (!r.uploadDate || r.uploadDate < since) continue;
      const p = parseUploadTitle(r.title);
      const key = p && norm(p.song);
      if (!key) continue;
      const e = found.get(key) || { name: p.song.toLowerCase(), artist: p.artist, theme, n: 0, text: ' ' };
      e.n++; e.text += norm(r.title) + ' ';
      found.set(key, e);
    }
  }
  const out = [];
  for (const e of [...found.values()].sort((a, b) => b.n - a.n).slice(0, max)) {
    // an AniList show named in the upload titles makes it an anime source, otherwise the artist is the (group) source
    let show = null;
    if (e.theme === 'anime' || e.theme === 'jpop') for (const t of shows.keys()) if (t.length >= 6 && e.text.includes(` ${t} `)) { show = t; break; }
    const base = { name: e.name, artist: e.artist, query: `${e.artist} ${e.name} official audio`, aliases: [], trending: e.n };
    out.push(show ? { ...base, theme: 'anime', source: show, kind: 'anime' } : { ...base, theme: e.theme, source: e.artist, kind: 'group' });
  }
  return out;
}

/** LLM batches + trending scrape + seed list, deduped (LLM first, then trending, then seeds), minus songs already in the list. */
async function gatherCandidates({ ask, search, now, shows }, have, o = {}) {
  const log = o.log || (() => {});
  const haveSet = new Set(have.map(norm));
  let llmRows = [], trend = [];
  try { llmRows = ask ? await askBatches(ask, have, o) : []; } catch (e) { log(`LLM path failed: ${e.message}`); }
  try { trend = await trendingCandidates(search, { now, shows, log }); } catch (e) { log(`trending scrape failed: ${e.message}`); }
  const out = [], seen = new Set();
  const n = { llm: 0, trending: 0, seed: 0 };
  for (const [kind, rows] of [['llm', llmRows], ['trending', trend], ['seed', SEEDS]]) {
    for (const r of rows) {
      const k = norm(r.name);
      if (!k || seen.has(k) || haveSet.has(k)) continue;
      seen.add(k); out.push(r); n[kind]++;
    }
  }
  log(`candidates: ${n.llm} from the LLM, ${n.trending} trending, ${n.seed} seed (${out.length} after dedupe)`);
  return out;
}

module.exports = { parseSongs, validSong, askBatches, similarFoci, SEEDS, parseUploadTitle, trendingCandidates, gatherCandidates };
