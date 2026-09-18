/**
 * What each upload tried and how it did, for the learners and the weekly report.
 *
 *   <MEMORY_DIR>/experiments.jsonl  one line per upload: the choices made (song, source,
 *                                   format, title template, settings) and why; append-only
 *   <MEMORY_DIR>/outcomes.json      per video: stat snapshots [hours since publish, views,
 *                                   likes, comments] from every daily sync, plus the latest
 *                                   analytics (engaged views, average % viewed, subs gained)
 *
 * reward(): one number per video on the channel's own scale, from fixed checkpoints so old
 * and new videos compare fairly (lscore favours whatever is newest):
 *   r = z(log1p(views at 7 days)) + 0.3 z(average % viewed) + 0.3 z(subs per 1k views)
 * Views are engaged views when the Analytics API gives them. Between 48 h and 7 days the
 * 7-day value is estimated from the 48 h one with the channel's own 48 h -> 7 d growth.
 * Videos from before the snapshots existed fall back to their lscore (also z-scored).
 */
const fs = require('fs');
const path = require('path');

const MEM = () => path.resolve(__dirname, '..', process.env.MEMORY_DIR || 'memory');
const EXPERIMENTS = () => path.join(MEM(), 'experiments.jsonl');
const OUTCOMES = () => path.join(MEM(), 'outcomes.json');
const MAX_SNAPS = 16;
const H48 = 48;
const H7D = 168;

function readJson(file, dflt) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return dflt; }
}

/** Log one upload's choices (called by recordUpload in each pipeline). */
function appendDecision(rec) {
  fs.mkdirSync(MEM(), { recursive: true });
  fs.appendFileSync(EXPERIMENTS(), JSON.stringify({ at: new Date().toISOString(), ...rec }) + '\n');
}

function loadDecisions() {
  try {
    return fs.readFileSync(EXPERIMENTS(), 'utf8').split('\n').filter(Boolean).map(l => { try { return JSON.parse(l); } catch { return null; } })
      .filter(Boolean);
  } catch { return []; }
}

/** Add today's stats to every video's snapshot list (videos: normalized, with publishedAt / analytics). */
function recordSnapshots(videos, now = Date.now()) {
  const out = readJson(OUTCOMES(), {});
  for (const v of videos) {
    if (!v.publishedAt || !v.videoId) continue;
    const h = (now - new Date(v.publishedAt).getTime()) / 3600000;
    if (h < 0) continue;
    const o = out[v.videoId] || (out[v.videoId] = { published: v.publishedAt, snaps: [] });
    const last = o.snaps[o.snaps.length - 1];
    if (last && h - last[0] < 6) o.snaps[o.snaps.length - 1] = [Math.round(h), v.views, v.likes, v.comments];
    else if (h <= 24 * 30 || !o.snaps.length) o.snaps.push([Math.round(h), v.views, v.likes, v.comments]);
    if (o.snaps.length > MAX_SNAPS) o.snaps.splice(1, o.snaps.length - MAX_SNAPS);  // keep the first and latest
    if (v.analytics) {
      o.ev = v.analytics.engagedViews || null;
      o.apv = v.analytics.avgViewPct || null;
      o.subs = v.analytics.subscribersGained || 0;
      o.shares = v.analytics.shares || 0;
      o.analyticsViews = v.analytics.views || null;
    }
  }
  fs.mkdirSync(MEM(), { recursive: true });
  fs.writeFileSync(OUTCOMES(), JSON.stringify(out));
  return out;
}

/** Views at `hours` after publishing, interpolated between snapshots (null if not bracketed / reached). */
function viewsAt(o, hours) {
  const s = (o && o.snaps) || [];
  if (!s.length || s[s.length - 1][0] < hours) return null;
  if (s[0][0] >= hours) return s[0][0] <= hours * 1.5 ? s[0][1] : null;  // first seen a bit late: close enough
  for (let i = 1; i < s.length; i++) {
    if (s[i][0] >= hours) {
      const [h0, v0] = s[i - 1], [h1, v1] = s[i];
      return v0 + (v1 - v0) * (hours - h0) / Math.max(1, h1 - h0);
    }
  }
  return null;
}

function zscorer(values) {
  const xs = values.filter(Number.isFinite);
  if (xs.length < 3) return () => 0;
  const m = xs.reduce((a, b) => a + b, 0) / xs.length;
  const sd = Math.sqrt(xs.reduce((a, b) => a + (b - m) ** 2, 0) / xs.length) || 1;
  return x => (Number.isFinite(x) ? (x - m) / sd : 0);
}

/**
 * Rewards for all videos in `videos` (performance-stats entries: videoId, lscore, ageDays, views).
 * Returns {videoId: {r, final, v7}} on the channel's own scale (last 60 days as the reference).
 */
function computeRewards(videos, outcomes = readJson(OUTCOMES(), {}), now = Date.now()) {
  const recent = videos.filter(v => (v.ageDays || 0) <= 60);
  // the channel's own 48 h -> 7 d growth (default 2x until there is data)
  const ratios = [];
  for (const v of recent) {
    const o = outcomes[v.videoId];
    const a = viewsAt(o, H48), b = viewsAt(o, H7D);
    if (a > 20 && b) ratios.push(b / a);
  }
  ratios.sort((x, y) => x - y);
  const growth = ratios.length >= 3 ? ratios[Math.floor(ratios.length / 2)] : 2.0;

  const rows = videos.map(v => {
    const o = outcomes[v.videoId] || {};
    const engagedShare = o.ev && o.analyticsViews ? Math.min(1, o.ev / o.analyticsViews) : null;
    const v7raw = viewsAt(o, H7D);
    const v48 = viewsAt(o, H48);
    let v7 = null, final = false;
    if (v7raw != null) { v7 = v7raw; final = true; } else if (v48 != null) v7 = v48 * growth;
    if (v7 != null && engagedShare != null) v7 *= engagedShare;  // engaged views when known
    const subsPer1k = o.subs != null && v.views > 0 ? (1000 * o.subs) / v.views : null;
    return { id: v.videoId, lv7: v7 != null ? Math.log1p(v7) : null, apv: o.apv || null, spk: subsPer1k, lscore: v.lscore, final };
  });
  const ref = rows.filter(r => videos.find(v => v.videoId === r.id && (v.ageDays || 0) <= 60));
  const zv = zscorer(ref.map(r => r.lv7));
  const za = zscorer(ref.map(r => r.apv));
  const zs = zscorer(ref.map(r => r.spk));
  const zl = zscorer(ref.map(r => r.lscore));
  const out = {};
  for (const r of rows) {
    let score;
    if (r.lv7 != null) score = zv(r.lv7) + (r.apv != null ? 0.3 * za(r.apv) : 0) + (r.spk != null ? 0.3 * zs(r.spk) : 0);
    else if (Number.isFinite(r.lscore)) score = zl(r.lscore);  // no checkpoint snapshots (older videos)
    else continue;
    out[r.id] = { r: Math.round(score * 1000) / 1000, final: r.final, v7: r.lv7 != null ? Math.round(Math.expm1(r.lv7)) : null };
  }
  return out;
}

/** Weight of an observation `ageDays` old: results lose 10% of their say per week (the channel changes). */
function decay(ageDays) {
  return Math.pow(0.9, Math.max(0, ageDays || 0) / 7);
}

/**
 * Discounted Thompson draw for one option from its scored uploads.
 * obs: [{r, ageDays}]; prior: channel mean; noise: sd of one observation.
 */
function thompsonDraw(obs, prior = 0, noise = 0.8, gauss = null) {
  const g = gauss || (() => {
    let u = 0, v = 0;
    while (!u) u = Math.random();
    while (!v) v = Math.random();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  });
  const w = obs.map(o => decay(o.ageDays));
  const n = w.reduce((a, b) => a + b, 0);
  const mean = (obs.reduce((a, o, i) => a + w[i] * o.r, 0) + prior) / (n + 1);
  return { mean, n, draw: mean + g() * noise / Math.sqrt(n + 1) };
}

module.exports = { appendDecision, loadDecisions, recordSnapshots, computeRewards, viewsAt, decay, thompsonDraw };
