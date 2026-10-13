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
const ALL = ['stem', ...NEW_TYPES];

function parseWeights(str = process.env.DAILY_TYPE_WEIGHTS) {
  const w = { stem: 1, sound: 0, slowmo: 0 };
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
  const last = lastType(history);
  const eligible = ALL.filter(t => weights[t] > 0 && (t === 'stem' || (poolSizes[t] || 0) >= poolMin));
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

module.exports = { dailyOrder, parseWeights, lastType, typeOf, NEW_TYPES, ALL };
