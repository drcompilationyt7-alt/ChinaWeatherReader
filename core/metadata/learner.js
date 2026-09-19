/**
 * The metadata learner: one Thompson-sampling bandit per dimension (title style, on-video
 * style, hashtag set, emoji set, description hook), per pool (channel / content type).
 *
 * Observations: every upload's chosen arms (<MEMORY_DIR>/metadata-choices.jsonl, written by
 * recordMetadataChoice, plus the arm fields the pipelines log with appendDecision in
 * experiments.jsonl) joined with the video's reward from performance-stats.json
 * (core/experiment-log.js: views at 7 days, estimated from 48 h, + % viewed + subs per 1k views;
 * lscore before rewards exist). Rewards are standardized within the pool, so 0 = the pool's
 * average upload and an arm's mean says how much better or worse than average it does.
 *
 * Per arm: Gaussian posterior with a prior (mu0 = 0 for seeds; bounded optimistic for arms the
 * weekly step adds, n0 = 1 pseudo-observation), observations weighted by age (10% less per
 * week, the channel keeps changing) and 0.75 while the reward is still a 48 h estimate.
 * Pick: 25% of the time explore (least-tried arms more likely), else draw from every posterior
 * and take the best draw. State (arms added / retired by the weekly step) lives in
 * <MEMORY_DIR>/metadata-arms.json; seeds come from core/metadata/catalog.js.
 */
const fs = require('fs');
const path = require('path');
const { loadDecisions, decay } = require('../experiment-log');
const catalog = require('./catalog');

const DIMS = ['titleStyle', 'ovStyle', 'hashtagSet', 'emojiSet', 'descHook'];
const ROOT = path.join(__dirname, '..', '..');
const SIGMA = 1.0;          // sd of one standardized observation
const ESTIMATE_WEIGHT = 0.75;  // a reward still estimated from 48 h counts a bit less than a final 7-day one

const memDir = () => path.resolve(ROOT, process.env.MEMORY_DIR || 'memory');
const armsFile = () => path.join(memDir(), 'metadata-arms.json');
const choicesFile = () => path.join(memDir(), 'metadata-choices.jsonl');
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const exploreRate = () => clamp(Number(process.env.META_EXPLORE || 0.25), 0.05, 0.5);

function readJson(file, dflt) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return dflt; }
}

// ─── randomness (seedable for the tests) ─────────────────────────

function rngFrom(seed) {
  let a = (seed >>> 0) || 1;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gauss(rng = Math.random) {
  let u = 0, v = 0;
  while (!u) u = rng();
  while (!v) v = rng();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

// ─── state ───────────────────────────────────────────────────────

function loadState() {
  const s = readJson(armsFile(), null);
  if (s && typeof s === 'object' && s.pools) return { version: 1, log: [], ...s };
  return { version: 1, pools: {}, log: [] };
}

function saveState(state) {
  fs.mkdirSync(memDir(), { recursive: true });
  state.updatedAt = new Date().toISOString();
  fs.writeFileSync(armsFile(), JSON.stringify(state, null, 1));
}

/**
 * Arms of a pool: the catalog's seeds merged with what the weekly step stored (status, added arms).
 * Returns {dim: [arm]}; arm = {id, status, mu0, n0, source, ...definition}.
 */
function armsFor(pool, state = loadState()) {
  const seeds = catalog.specFor(pool).seeds || {};
  const stored = ((state.pools || {})[pool] || {}).arms || {};
  const out = {};
  for (const dim of DIMS) {
    const st = stored[dim] || {};
    const list = (seeds[dim] || []).map(a => ({ mu0: 0, n0: 1, source: 'seed', ...a, status: (st[a.id] && st[a.id].status) || 'active' }));
    for (const [id, rec] of Object.entries(st)) {
      if (list.some(a => a.id === id) || !rec || !rec.def) continue;
      list.push({ ...rec.def, id, status: rec.status || 'active', source: rec.source || 'added',
        mu0: Number.isFinite(rec.mu0) ? rec.mu0 : 0, n0: Number.isFinite(rec.n0) ? rec.n0 : 1, why: rec.why });
    }
    if (list.length) out[dim] = list;
  }
  return out;
}

// ─── observations ────────────────────────────────────────────────

function readChoices() {
  try {
    return fs.readFileSync(choicesFile(), 'utf8').split('\n').filter(Boolean)
      .map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
  } catch { return []; }
}

function appendChoice(rec) {
  fs.mkdirSync(memDir(), { recursive: true });
  fs.appendFileSync(choicesFile(), JSON.stringify(rec) + '\n');
  // keep the file small: the learner only needs the last few months (older results barely count)
  try {
    const lines = fs.readFileSync(choicesFile(), 'utf8').split('\n').filter(Boolean);
    if (lines.length > 3000) fs.writeFileSync(choicesFile(), lines.slice(-2000).join('\n') + '\n');
  } catch {}
}

const pickDims = a => Object.fromEntries(DIMS.filter(d => a && a[d]).map(d => [d, a[d]]));

/** Every upload of the pool with the arms it used: {vid, at, title, arms, facts?}. */
function decisionsFor(pool) {
  const byVid = new Map();
  for (const d of loadDecisions()) {
    const a = d.arm || {};
    if (!d.vid || !a.titleStyle) continue;
    const dct = a.contentType || (d.ch === 'quiz' ? 'quiz' : 'stem-edit');
    if (catalog.poolKey(d.ch === 'quiz' ? 'quiz' : 'meme', dct) !== pool) continue;
    byVid.set(d.vid, { vid: d.vid, at: d.at, title: d.title, arms: pickDims(a) });
  }
  for (const c of readChoices()) {
    if (c.pool !== pool || !c.vid) continue;
    byVid.set(c.vid, { ...(byVid.get(c.vid) || {}), vid: c.vid, at: c.at, title: c.title || (byVid.get(c.vid) || {}).title,
      arms: pickDims(c.arms), facts: c.facts, onVideoTitle: c.onVideoTitle, modes: c.modes });
  }
  return [...byVid.values()];
}

/** Standardize within the pool: 0 = average upload, clipped to +-2.5 so one outlier cannot run the show. */
function standardize(values) {
  const xs = values.filter(Number.isFinite);
  if (!xs.length) return () => 0;
  const m = xs.reduce((a, b) => a + b, 0) / xs.length;
  const sd = xs.length >= 3 ? Math.max(0.25, Math.sqrt(xs.reduce((a, b) => a + (b - m) ** 2, 0) / xs.length)) : 1;
  return x => clamp((x - m) / sd, -2.5, 2.5);
}

/**
 * Scored observations of a pool: [{vid, arms, title, r, x, ageDays, final, views}].
 * Needs 2 days of data (the 48 h checkpoint the reward estimates from).
 */
function observations(pool, { stats = null, decisions = null } = {}) {
  const st = stats || readJson(path.join(memDir(), 'performance-stats.json'), { videos: [] });
  const byId = new Map(((st && st.videos) || []).map(v => [v.videoId, v]));
  const rows = (decisions || decisionsFor(pool)).map(d => ({ ...d, stat: byId.get(d.vid) }))
    .filter(d => d.stat && (d.stat.ageDays || 0) >= 2);
  const useReward = rows.length && rows.every(d => typeof d.stat.reward === 'number');
  const val = d => (useReward ? d.stat.reward : d.stat.lscore);
  const scored = rows.filter(d => Number.isFinite(val(d)));
  const z = standardize(scored.map(val));
  return scored.map(d => ({ vid: d.vid, arms: d.arms || {}, title: d.title, onVideoTitle: d.onVideoTitle, facts: d.facts,
    r: val(d), x: z(val(d)), ageDays: d.stat.ageDays || 0, final: d.stat.rewardFinal !== false || !useReward, views: d.stat.views || 0 }));
}

// ─── the bandit ──────────────────────────────────────────────────

/** Posterior of one arm from its observations ([{x, ageDays, final}]). */
function posterior(arm, obs) {
  let n = 0, s = 0;
  for (const o of obs) {
    const w = decay(o.ageDays) * (o.final === false ? ESTIMATE_WEIGHT : 1);
    n += w;
    s += w * o.x;
  }
  const n0 = Number.isFinite(arm.n0) ? arm.n0 : 1;
  const mu0 = Number.isFinite(arm.mu0) ? arm.mu0 : 0;
  return { uses: obs.length, n: Math.round(n * 100) / 100, mean: (s + n0 * mu0) / (n + n0), sd: SIGMA / Math.sqrt(n + n0) };
}

function posteriors(arms, obs, dim) {
  return new Map(arms.map(a => [a.id, posterior(a, obs.filter(o => o.arms && o.arms[dim] === a.id))]));
}

/**
 * Choose one arm of a dimension. opts: {rng, explore, eligible(arm) -> bool}.
 * Returns {arm, mode: 'explore'|'thompson'|'only', post} or null when nothing is eligible.
 */
function choose(arms, obs, dim, opts = {}) {
  const rng = opts.rng || Math.random;
  const explore = opts.explore ?? exploreRate();
  const act = arms.filter(a => a.status !== 'retired' && (!opts.eligible || opts.eligible(a)));
  if (!act.length) return null;
  const post = posteriors(act, obs, dim);
  if (act.length === 1) return { arm: act[0], mode: 'only', post };
  if (rng() < explore) {
    // exploration floor: every arm keeps getting tried, the least-tried ones more
    const w = act.map(a => 1 / Math.sqrt(1 + post.get(a.id).uses));
    let x = rng() * w.reduce((a, b) => a + b, 0);
    for (let i = 0; i < act.length; i++) { x -= w[i]; if (x <= 0) return { arm: act[i], mode: 'explore', post }; }
    return { arm: act[act.length - 1], mode: 'explore', post };
  }
  let best = null;
  for (const a of act) {
    const p = post.get(a.id);
    const d = p.mean + p.sd * gauss(rng);
    if (!best || d > best.d) best = { a, d };
  }
  return { arm: best.a, mode: 'thompson', post };
}

module.exports = {
  DIMS, SIGMA, memDir, armsFile, choicesFile, rngFrom, gauss, clamp, readJson,
  loadState, saveState, armsFor, readChoices, appendChoice, decisionsFor, standardize, observations,
  posterior, posteriors, choose, exploreRate,
};
