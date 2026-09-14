/**
 * Learning models — Node side of the three free third-party learners that
 * run on the GitHub Actions runner (Python, CPU only):
 *
 *   MABWiser  core/learning/bandit_pick.py   contextual Thompson sampling over
 *             content categories (context: region, weekday, hour) — the
 *             principled exploration/exploitation part of the clip pick
 *   River     core/learning/river_views.py   online regressor (adaptive random
 *             forest) predicting log views/day from country, category, channel,
 *             title features, eggs, weekday, hour — learns one video at a time
 *             after every stats sync, predicts for clip candidates and titles
 *   DSPy      core/learning/dspy_title.py    title writer whose few-shot demos
 *             are compiled against our own performance proxy, re-optimized
 *             weekly; its titles join Gemini's as candidates for the ranker
 *
 * Every call degrades gracefully: missing package, missing model, no API key
 * or a crash just returns null and the pipeline continues as before.
 */
const path = require('path');
const fs = require('fs');
const { execFileSync } = require('child_process');
const { Logger } = require('./logger');
const { loadStats, loadInsights, loadPostedIndex, TITLE_FEATURES } = require('./performance-tracker');

const logger = new Logger('Learning');
const DIR = path.join(__dirname, 'learning');
const MEM = path.join(__dirname, '..', 'memory');
const DSPY_STATUS = path.join(MEM, 'dspy-title-status.json');

let _py;
function pythonCommand() {
  if (_py !== undefined) return _py;
  _py = null;
  for (const cmd of [process.env.PYTHON_BIN, 'python3', 'python'].filter(Boolean)) {
    try { execFileSync(cmd, ['-c', 'import sys'], { stdio: 'ignore', timeout: 10000 }); _py = cmd; break; } catch {}
  }
  return _py;
}

function runPy(script, args, input, timeout = 120000) {
  const py = pythonCommand();
  const file = path.join(DIR, script);
  if (!py || !fs.existsSync(file)) return null;
  try {
    const out = execFileSync(py, [file, ...args], {
      input: JSON.stringify(input || {}), encoding: 'utf8', timeout, maxBuffer: 32 * 1024 * 1024,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    });
    const line = out.trim().split('\n').filter(Boolean).pop();
    return line ? JSON.parse(line) : null;
  } catch (e) {
    logger.warn(`${script} ${args[0] || ''} failed: ${(e.message || '').substring(0, 100)}`);
    return null;
  }
}

// ─── features ──────────────────────────────────────────────────────

const REGION = {
  east: ['china', 'japan', 'south korea', 'korea', 'taiwan', 'hong kong'],
  sea: ['thailand', 'vietnam', 'indonesia', 'philippines', 'malaysia', 'singapore', 'cambodia', 'laos', 'myanmar'],
  south: ['india', 'pakistan', 'bangladesh', 'sri lanka', 'nepal'],
};
function regionOf(country) {
  const c = String(country || '').toLowerCase();
  for (const [name, list] of Object.entries(REGION)) if (list.some(x => c === x || c.includes(x))) return name;
  return c && !['world', 'asia', 'global'].includes(c) ? 'other' : 'asia';
}

/**
 * Feature dict for River. `title` may be null at clip-pick time (typical
 * values are used then); `date` defaults to now.
 */
function featuresFor({ country, category, channel, date, eggs, duration, title } = {}) {
  const d = date ? new Date(date) : new Date();
  const f = {
    country: String(country || 'unknown').toLowerCase(),
    region: regionOf(country),
    category: String(category || 'other').toLowerCase(),
    channel: String(channel || 'unknown').replace(/^@/, '').toLowerCase(),
    weekday: d.getUTCDay(),
    hour: d.getUTCHours(),
    eggs: Number.isFinite(eggs) ? eggs : 1,
    duration: Number.isFinite(duration) ? duration : 30,
  };
  if (title) {
    f.title_len = title.length;
    f.title_emoji = TITLE_FEATURES['has emoji'](title) ? 1 : 0;
    f.title_question = TITLE_FEATURES['question'](title) ? 1 : 0;
    f.title_number = TITLE_FEATURES['has number'](title) ? 1 : 0;
    f.title_caps = TITLE_FEATURES['ALL CAPS word'](title) ? 1 : 0;
    f.title_place = TITLE_FEATURES['mentions place'](title) ? 1 : 0;
  } else {
    Object.assign(f, { title_len: 32, title_emoji: 1, title_question: 0, title_number: 0, title_caps: 0, title_place: 1 });
  }
  return f;
}

// ─── MABWiser bandit ───────────────────────────────────────────────

function banditHistory(stats) {
  const vids = (stats && stats.videos) || [];
  const scored = vids.filter(v => typeof v.lscore === 'number');
  const sorted = [...scored].sort((a, b) => a.lscore - b.lscore);
  const rank = new Map(sorted.map((v, i) => [v.videoId, sorted.length > 1 ? i / (sorted.length - 1) : 0.5]));
  return scored.map(v => {
    const d = v.publishedAt ? new Date(v.publishedAt) : new Date();
    return { arm: v.category || 'other', context: { country: v.country, weekday: d.getUTCDay(), hour: d.getUTCHours() }, reward: rank.get(v.videoId) };
  });
}

/**
 * Sampled expected reward (0..1) per candidate from the contextual bandit.
 * @param {Array<{id, arm, context}>} candidates
 * @returns {Map<string, number>|null}
 */
function banditScores(candidates, stats = loadStats()) {
  if (!candidates || !candidates.length) return null;
  const res = runPy('bandit_pick.py', [], { history: banditHistory(stats), candidates }, 120000);
  if (!res || !res.available || !Array.isArray(res.scores)) { if (res && res.reason) logger.info(`Bandit: ${res.reason}`); return null; }
  if (res.policy === 'none') { logger.info(`Bandit: ${res.reason || 'not enough history'}`); return null; }
  logger.info(`Bandit (${res.policy}, ${res.n} plays): ${res.scores.map(s => `${s.arm}=${s.score}`).join(', ')}`);
  return new Map(res.scores.map(s => [String(s.id), s.score]));
}

// ─── River online regressor ────────────────────────────────────────

/** Stream every scored video the model has not seen yet. */
function riverUpdate(stats = loadStats()) {
  const vids = (stats && stats.videos) || [];
  const posted = loadPostedIndex();
  const videos = vids.filter(v => typeof v.lscore === 'number').map(v => {
    const p = posted[v.videoId] || {};
    return {
      id: v.videoId, publishedAt: v.publishedAt, target: v.lscore,
      features: featuresFor({ country: v.country || p.country, category: v.category || p.category, channel: v.sourceChannel || p.sourceChannel,
        date: v.publishedAt, eggs: Array.isArray(v.eggs) ? v.eggs.length : (Array.isArray(p.eggs) ? p.eggs.length : 0), duration: v.durationSec, title: v.title }),
    };
  });
  if (!videos.length) return null;
  const res = runPy('river_views.py', ['--update'], { videos }, 300000);
  if (res && res.available) logger.info(`River: learned ${res.learned} new, ${res.seen} total${res.rebuilt ? ' (model rebuilt)' : ''}${res.mae != null ? `, MAE ${res.mae}` : ''}`);
  else if (res && res.reason) logger.info(`River: ${res.reason}`);
  return res;
}

/**
 * Predicted log views/day per item.
 * @param {Array<{id, features}>} items
 * @returns {Map<string, number>|null}
 */
function riverPredict(items) {
  if (!items || !items.length) return null;
  const res = runPy('river_views.py', ['--predict'], { items }, 120000);
  if (!res || !res.available || !Array.isArray(res.predictions)) return null;
  const m = new Map();
  for (const p of res.predictions) if (typeof p.predicted === 'number') m.set(String(p.id), p.predicted);
  if (!m.size) { if (res.reason) logger.info(`River: ${res.reason}`); return null; }
  return m;
}

/** min-max normalise a Map of numbers to 0..1 (0.5 when flat). */
function normalizeMap(m) {
  if (!m || !m.size) return null;
  const vals = [...m.values()];
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const out = new Map();
  for (const [k, v] of m) out.set(k, hi > lo ? (v - lo) / (hi - lo) : 0.5);
  return out;
}

// ─── DSPy title writer ─────────────────────────────────────────────

function dspyStatus() { return runPy('dspy_title.py', ['--status'], {}, 60000); }

/** Titles from the (compiled) DSPy program; [] when unavailable. */
function dspyGenerateTitles(context, insightsText = '') {
  if ((process.env.DSPY_TITLES || 'on') === 'off') return [];
  const res = runPy('dspy_title.py', ['--generate'], { context, insights: insightsText }, 180000);
  if (!res || !res.available) { if (res && res.reason) logger.info(`DSPy: ${res.reason}`); return []; }
  if (res.error) logger.warn(`DSPy generate: ${res.error}`);
  if (res.titles && res.titles.length) logger.info(`DSPy titles (${res.compiled ? 'compiled' : 'base'} program): ${res.titles.map(t => `"${t}"`).join(' | ')}`);
  return res.titles || [];
}

/**
 * Re-compile the title program against our stats when due (weekly, or
 * forced with DSPY_OPTIMIZE=force; DSPY_OPTIMIZE=off disables).
 */
function dspyOptimizeIfDue({ stats = loadStats(), insights = loadInsights(), force = false } = {}) {
  const mode = process.env.DSPY_OPTIMIZE || 'auto';
  if (mode === 'off') return null;
  let status = {};
  try { status = JSON.parse(fs.readFileSync(DSPY_STATUS, 'utf8')); } catch {}
  const ageDays = status.optimizedAt ? (Date.now() - new Date(status.optimizedAt).getTime()) / 86400000 : Infinity;
  const due = force || mode === 'force' || ageDays >= 7;
  if (!due) { logger.info(`DSPy: program is ${ageDays.toFixed(1)} days old — no re-optimization needed`); return null; }

  const vids = ((stats && stats.videos) || []).filter(v => typeof v.lscore === 'number' && v.title);
  const posted = loadPostedIndex();
  const trainset = vids.map(v => {
    const p = posted[v.videoId] || {};
    return { title: v.title, score: v.lscore, context: { country: v.country || p.country || 'unknown', category: v.category || p.category || 'other', summary: p.summary || '', source_title: p.sourceTitle || '' } };
  });
  if (trainset.length < 8) { logger.info(`DSPy: only ${trainset.length} scored titles — skipping optimization`); return null; }
  logger.info(`DSPy: optimizing title program on ${Math.min(24, trainset.length)} examples...`);
  const res = runPy('dspy_title.py', ['--optimize'], {
    trainset, history: vids.map(v => ({ title: v.title, score: v.lscore })),
    insights: (insights && insights.promptSummary) || '', maxExamples: 24,
  }, 15 * 60000);
  if (res && res.ok) logger.success(`DSPy: program compiled (${res.examples} examples, metric ${res.metricMean})`);
  else logger.warn(`DSPy: optimization skipped (${(res && res.reason) || 'no result'})`);
  return res;
}

module.exports = { featuresFor, regionOf, banditScores, banditHistory, riverUpdate, riverPredict, normalizeMap, dspyStatus, dspyGenerateTitles, dspyOptimizeIfDue, pythonCommand };
