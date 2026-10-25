/**
 * Zero Yen Otaku daily rotation: the ONE short a day is a stem edit, a sound edit or a slow-mo edit.
 *
 *   DAILY_TYPE_WEIGHTS  "stem:1,sound:0,slowmo:0" (default: stem only until the owner switches the new types on). 0 disables a type. A type with no weight is never picked.
 *   DAILY_ROTATION      must be "on" for anything but stem (default off = always stem, the old behaviour)
 *
 * Rules: weighted random; never the type of the last upload when another is possible; a new type whose pool is
 * empty (pipeline/newtype-pipeline.js poolSize < NEWTYPE_POOL_MIN) is not eligible; stem is the fallback of the
 * fallback order, so a day is never missed (the runner tries each type in order() until one renders).
 */
const NEW_TYPES = ['sound', 'slowmo'];
// 2026-10-25 ranking / list compilation: WEEKLY, not in the weighted draw. On RANKING_WEEKDAY (UTC, 0 = Sunday ... 6 =
// Saturday; default 6) it REPLACES that day's type (never a second post) when its weight is > 0 (DAILY_TYPE_WEIGHTS
// "ranking:1"; default 0 = OFF until the owner approves the first CI-built one), a topic is eligible (poolSizes.ranking =
// eligible topics) and the last ranking upload is >= RANKING_MIN_GAP_DAYS (6) old. Stem stays the fallback.
const WEEKLY = ['ranking'];
const ALL = ['stem', ...NEW_TYPES, ...WEEKLY];

function parseWeights(str = process.env.DAILY_TYPE_WEIGHTS) {
  const w = { stem: 1, sound: 0, slowmo: 0, ranking: 0 };
  if (str) {
    for (const part of String(str).split(',')) {
      const [k, v] = part.split(':').map(s => s.trim());
      if (ALL.includes(k) && Number.isFinite(Number(v))) w[k] = Math.max(0, Number(v));
    }
  }
  return w;
}

/** content type of an upload record (stem edits predate the field) */
const typeOf = u => String((u && u.contentType) || 'stem-edit').replace(/-edit$/, '').replace('stem-edit', 'stem');
/** the weekly ranking slot is today: weight > 0, the weekday, a topic is eligible, the last ranking upload >= gap days ago */
function rankingDue({ history = { uploads: [] }, weights = parseWeights(), poolSizes = {}, now = new Date() } = {}) {
  if (!(weights.ranking > 0) || !((poolSizes.ranking || 0) >= 1)) return false;
  if (now.getUTCDay() !== Number(process.env.RANKING_WEEKDAY ?? 6)) return false;
  const gap = Number(process.env.RANKING_MIN_GAP_DAYS || 6) * 86400000;
  const last = ((history && history.uploads) || []).filter(u => typeOf(u) === 'ranking').map(u => Date.parse(u.postedAt || 0)).sort().pop();
  return !last || now.getTime() - last >= gap;
}
const lastType = history => {
  const ups = ((history && history.uploads) || []).filter(u => !u.long);
  return ups.length ? typeOf(ups[ups.length - 1]).replace('stem-edit', 'stem') : null;
};

/**
 * @returns {string[]} the types to try, in order: a weighted random first pick, then the remaining eligible new
 * types, and 'stem' always last (the fallback).
 */
function dailyOrder({ history = { uploads: [] }, weights = parseWeights(), poolSizes = {}, poolMin = 1, rng = Math.random, enabled = process.env.DAILY_ROTATION === 'on' } = {}) {
  if (!enabled) return ['stem'];
  if (rankingDue({ history, weights, poolSizes })) return ['ranking', 'stem'];      // the weekly slot replaces the day's type
  const last = lastType(history);
  const eligible = ['stem', ...NEW_TYPES].filter(t => weights[t] > 0 && (t === 'stem' || (poolSizes[t] || 0) >= poolMin));
  if (!eligible.length) return ['stem'];
  let cand = eligible.filter(t => t !== last);
  if (!cand.length) cand = eligible;  // only one type left: a repeat is better than no short
  const pick = [];
  const left = [...cand];
  while (left.length) {
    const total = left.reduce((a, t) => a + weights[t], 0);
    let r = rng() * total, i = 0;
    for (; i < left.length - 1; i++) { r -= weights[left[i]]; if (r < 0) break; }
    pick.push(left.splice(i, 1)[0]);
  }
  // the type we avoided (if eligible) and stem come after as fallbacks
  for (const t of eligible) if (!pick.includes(t)) pick.push(t);
  if (!pick.includes('stem')) pick.push('stem');
  // stem is the final fallback unless it was the weighted first pick
  const first = pick[0];
  return [first, ...pick.slice(1).filter(t => t !== 'stem'), ...(first === 'stem' ? [] : ['stem'])];
}

module.exports = { dailyOrder, parseWeights, lastType, typeOf, rankingDue, NEW_TYPES, WEEKLY, ALL };
