/**
 * Trend awareness, cheaply: YouTube's own search autocomplete (what people actually type),
 * cached for a few days in <MEMORY_DIR>/yt-suggest-cache.json. No API key, no quota.
 *
 *   await suggest('kick back acapella')
 *     -> ['kick back acapella', 'kick back acapella cover', 'kick back kenshi yonezu acapella', ...]
 *
 * META_SUGGEST=off skips the network (cached answers are still used).
 */
const fs = require('fs');
const path = require('path');
const { norm, contains, len } = require('./rules');

const ROOT = path.join(__dirname, '..', '..');
const cacheFile = () => path.resolve(ROOT, process.env.MEMORY_DIR || 'memory', 'yt-suggest-cache.json');
const TTL_DAYS = 3;
const KEEP_DAYS = 21;
const MAX_ENTRIES = 400;
// phrases that describe other kinds of videos (a lyric video, a reaction, a tutorial ...)
const JUNK = /\b(lyrics?|reaction|react|reacts|tutorial|live|karaoke|instrumental|remix|slowed|reverb|sped up|nightcore|download|mp3|piano|guitar|meaning|full|hour|hours|english|romaji|translation|tiktok|capcut|template|roblox|minecraft|fortnite|8d|bass boosted|ringtone|cover|dance practice|mirrored|behind)\b/i;

const caches = {};  // per cache file (MEMORY_DIR can differ between channels / tests)
function loadCache() {
  const f = cacheFile();
  if (!caches[f]) {
    try { caches[f] = JSON.parse(fs.readFileSync(f, 'utf8')); } catch { caches[f] = {}; }
  }
  return caches[f];
}

function saveCache() {
  const f = cacheFile();
  try {
    const now = Date.now();
    const entries = Object.entries(caches[f] || {})
      .filter(([, v]) => v && v.at && now - new Date(v.at).getTime() < KEEP_DAYS * 86400000)
      .sort((a, b) => new Date(b[1].at) - new Date(a[1].at)).slice(0, MAX_ENTRIES);
    caches[f] = Object.fromEntries(entries);
    fs.mkdirSync(path.dirname(f), { recursive: true });
    fs.writeFileSync(f, JSON.stringify(caches[f], null, 1));
  } catch {}
}

async function fetchSuggest(q, timeoutMs) {
  const url = 'https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&hl=en&ie=utf-8&oe=utf-8&q='
    + encodeURIComponent(q);
  const axios = require('axios');
  const r = await axios.get(url, { timeout: timeoutMs, responseType: 'text', transformResponse: x => x,
    headers: { 'User-Agent': 'Mozilla/5.0 (metadata optimizer)' } });
  const data = JSON.parse(r.data);
  return Array.isArray(data) && Array.isArray(data[1]) ? data[1].map(s => String(s).trim()).filter(Boolean).slice(0, 10) : [];
}

/** Autocomplete phrases for one query ([] when offline). */
async function suggest(query, { ttlDays = TTL_DAYS, timeoutMs = 6000 } = {}) {
  const q = norm(query);
  if (!q) return [];
  const c = loadCache();
  const hit = c[q];
  if (hit && Date.now() - new Date(hit.at).getTime() < ttlDays * 86400000) return hit.s || [];
  if (process.env.META_SUGGEST === 'off') return (hit && hit.s) || [];
  try {
    const s = await fetchSuggest(q, timeoutMs);
    loadCache()[q] = { at: new Date().toISOString(), s };
    saveCache();
    return s;
  } catch {
    return (hit && hit.s) || [];
  }
}

/** Autocomplete phrases for several queries, deduplicated, junk removed. */
async function suggestMany(queries, opts = {}) {
  const out = [];
  for (const q of [...new Set(queries.filter(Boolean).map(norm))].slice(0, 4)) {
    for (const s of await suggest(q, opts)) if (!JUNK.test(s) && !out.some(x => norm(x) === norm(s))) out.push(s);
  }
  return out;
}

/**
 * The best autocomplete phrase to use as a title: has every must-group, is more specific than the
 * bare keyword (e.g. "kick back kenshi yonezu acapella"), fits in `maxLen`.
 */
function bestSuggestTitle(phrases, must, keyword, maxLen = 48) {
  const ok = phrases.filter(p => len(p) <= maxLen && norm(p) !== norm(keyword) && !JUNK.test(p)
    && (must || []).every(g => [].concat(g).filter(Boolean).some(a => contains(p, a))));
  return ok.sort((a, b) => len(b) - len(a))[0] || null;
}

module.exports = { suggest, suggestMany, bestSuggestTitle, JUNK };
