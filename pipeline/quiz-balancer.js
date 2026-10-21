/**
 * Two-level balancer for the Asian Pop Quiz (owner 2026-10-20): the TYPE family first (quiz / would-you-rather / duel),
 * then the TOPIC bucket (anime / K-pop / VTuber / geo / culture+food), then the FORMAT.
 *
 *   - each level boosts what is furthest below its target share over the last WINDOW uploads, and a topic already at
 *     CAP (40 %) of the window is not picked while another topic can be;
 *   - the format rules stay: no format repeat within NO_REPEAT_DAYS days, geography (incl. nightcity) at most
 *     1 in GEO_EVERY uploads, and the caller's view data steers only part of the format picks;
 *   - never-post-empty: a (format, topic) is only a candidate when `available(format, popTopic)` says its pool has
 *     enough fresh real items (the pipeline passes pool-backed counts; new formats with small pools stay off).
 *
 * Pure functions (no I/O): the pipeline and scripts/quiz-balance-sim.js both use them.
 */
const FAMILY_TARGET = { quiz: 0.74, wyr: 0.16, duel: 0.10 };
const TOPIC_TARGET = { anime: 0.30, kpop: 0.22, vtuber: 0.14, geo: 0.16, culture: 0.18 };
const WINDOW = 14;
const CAP = 0.4;

const GEO_FORMATS = ['flag', 'shape', 'capital', 'bigger', 'crowd', 'city', 'cityphoto', 'nightcity'];   // the geo topic bucket
const GEO_CAPPED = ['flag', 'shape', 'capital', 'bigger', 'crowd', 'city', 'nightcity'];   // at most 1 in GEO_EVERY uploads (as before; + nightcity)
// which topic buckets a format can serve, and the pop topic it is built with for each bucket
const SERVES = {
  character: { anime: 'anime' }, silhouette: { anime: 'anime' }, eyes: { anime: 'anime' }, outfit: { anime: 'anime' },
  opening: { anime: 'anime' }, scene: { anime: 'anime' }, voice: { anime: 'anime' }, duel: { anime: 'anime' },
  idol: { kpop: 'kpop' }, vtuber: { vtuber: 'vtubers' },
  city: { geo: null }, cityphoto: { geo: 'asia' }, nightcity: { geo: 'asia' }, flag: { geo: null }, shape: { geo: null },
  capital: { geo: null }, bigger: { geo: null }, crowd: { geo: null },
  cuisine: { culture: 'food' },
  emoji: { anime: 'anime', kpop: 'kpop song' },
  song: { anime: 'anime', kpop: 'kpop' },
  trivia: { anime: 'anime', kpop: 'kpop', vtuber: 'vtubers', geo: 'cities', culture: ['food', 'cdrama'] },
  wyr: { anime: 'anime', kpop: 'kpop', culture: 'food', geo: 'cities' },
};
const familyOf = f => (f === 'wyr' ? 'wyr' : f === 'duel' ? 'duel' : 'quiz');

/** The topic bucket of a past upload (format + the topic / theme it was built with). */
function bucketOf(format, theme) {
  const t = String(theme || '').toLowerCase();
  if (GEO_FORMATS.includes(format)) return 'geo';
  if (format === 'idol') return 'kpop';
  if (format === 'vtuber') return 'vtuber';
  if (format === 'cuisine') return 'culture';
  if (['trivia', 'emoji', 'song', 'wyr'].includes(format)) {
    if (/kpop|k-pop|mixed/.test(t)) return 'kpop';
    if (/vtuber/.test(t)) return 'vtuber';
    if (/cities|asia|travel/.test(t)) return 'geo';
    if (/food|cdrama|culture/.test(t)) return 'culture';
  }
  return 'anime';
}

function shares(list, keys) {
  const out = Object.fromEntries(keys.map(k => [k, 0]));
  for (const x of list) if (x in out) out[x] += 1;
  const n = Math.max(1, list.length);
  for (const k of keys) out[k] /= n;
  return out;
}

/** Deficit weights: target x (1 + 4 x (target - share)), never below 10 % of the target; at the cap: 0. */
function deficitWeights(target, share, keys, cap = 1) {
  return keys.map(k => [k, share[k] >= cap ? 0 : target[k] * Math.max(0.1, 1 + 4 * (target[k] - share[k]))]);
}

function pickWeighted(pairs, rng) {
  const live = pairs.filter(([, w]) => w > 0);
  const list = live.length ? live : pairs;
  const tot = list.reduce((a, [, w]) => a + w, 0);
  if (!(tot > 0)) return list[Math.floor(rng() * list.length)][0];
  let x = rng() * tot;
  for (const [k, w] of list) { x -= w; if (x <= 0) return k; }
  return list[list.length - 1][0];
}

const timeOf = u => new Date(u.publishAt || u.postedAt || 0).getTime();

/** Formats blocked right now: posted within NO_REPEAT_DAYS days, and all geography when one aired in the last GEO_EVERY - 1 uploads. */
function blockedFormats(uploads, { now = Date.now(), noRepeatDays = 3, geoEvery = 7, geoCapOn = true } = {}) {
  const out = new Set();
  const cutoff = now - noRepeatDays * 86400000 + 3600000;   // an hour of slack: "3 days ago" at the same slot is allowed
  for (const u of uploads) if (timeOf(u) >= cutoff) out.add(u.format);
  for (const u of uploads.slice(-3)) out.add(u.format);    // and never one of the last 3 uploads (a burst of backup runs)
  if (geoCapOn && uploads.slice(-(geoEvery - 1)).some(u => GEO_CAPPED.includes(u.format))) for (const f of GEO_CAPPED) out.add(f);
  return out;
}

/**
 * Pick family -> topic -> candidates. Returns {family, bucket, candidates: [{format, topic}], shares, why}; the caller
 * chooses the format among the candidates (exploration weights or Thompson on views).
 *   formats      the channel's formats
 *   uploads      history (oldest first; excluded uploads already removed)
 *   available    (format, popTopic) => bool
 */
function plan({ formats, uploads, available, rng = Math.random, now = Date.now(), noRepeatDays = 3, geoEvery = 7 }) {
  const recent = uploads.slice(-WINDOW);
  const famShare = shares(recent.map(u => familyOf(u.format)), Object.keys(FAMILY_TARGET));
  const topShare = shares(recent.map(u => bucketOf(u.format, u.theme)), Object.keys(TOPIC_TARGET));
  const blocked = blockedFormats(uploads, { now, noRepeatDays, geoEvery, geoCapOn: true });
  // every (format, bucket) pair that can run now
  const options = [];
  for (const f of formats) {
    if (blocked.has(f) || !SERVES[f]) continue;
    for (const [bucket, t] of Object.entries(SERVES[f])) {
      const topics = Array.isArray(t) ? t.filter(x => available(f, x)) : (available(f, t) ? [t] : []);
      if (topics.length) options.push({ format: f, bucket, family: familyOf(f), topic: topics[Math.floor(rng() * topics.length)] });
    }
  }
  if (!options.length) return { family: null, bucket: null, candidates: [], famShare, topShare, why: 'nothing available' };
  const fams = Object.keys(FAMILY_TARGET).filter(k => options.some(o => o.family === k));
  // a family that could only serve topics already at the cap (duel = anime only) is held back while another family can run
  const openFam = k => options.some(o => o.family === k && topShare[o.bucket] < CAP);
  const anyOpen = fams.some(openFam);
  const family = pickWeighted(deficitWeights(FAMILY_TARGET, famShare, fams).map(([k, w]) => [k, anyOpen && !openFam(k) ? 0 : w]), rng);
  const inFam = options.filter(o => o.family === family);
  const buckets = Object.keys(TOPIC_TARGET).filter(k => inFam.some(o => o.bucket === k));
  const bucket = pickWeighted(deficitWeights(TOPIC_TARGET, topShare, buckets, CAP), rng);
  const candidates = inFam.filter(o => o.bucket === bucket);
  const pct = o => Object.entries(o).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(' ');
  return { family, bucket, candidates, famShare, topShare,
    why: `balance ${family}/${bucket} (last ${recent.length}: ${pct(famShare)}; ${pct(topShare)})` };
}

module.exports = { plan, bucketOf, familyOf, blockedFormats, SERVES, FAMILY_TARGET, TOPIC_TARGET, GEO_FORMATS, GEO_CAPPED, WINDOW, CAP };
