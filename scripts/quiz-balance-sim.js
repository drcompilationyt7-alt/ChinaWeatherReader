/**
 * Simulate the quiz balancer (pipeline/quiz-balancer.js through pickFormat) for N days at 2 uploads a day, starting from
 * the real quiz-channel history, stats and pools. Pools shrink as the simulated uploads use them (60-day reuse window),
 * so a format / topic whose stored pool runs low drops out like it would live. Nothing is written.
 *
 *   node scripts/quiz-balance-sim.js [days=60] [seed] [--grow] [--llm]
 *     --grow  add the nightly mining quotas (core/quiz/quiz_pool_grow.py QUOTA_REVIEWED: reviewed items per night) to the pools
 *     --llm   would-you-rather may still be written on demand (as live while an LLM key works)
 */
const os = require('os');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const argv = process.argv.slice(2).filter(a => !a.startsWith('--'));
const flags = new Set(process.argv.slice(2).filter(a => a.startsWith('--')));
const days = Number(argv[0] || 60);
let seed = Number(argv[1] || 7);
// reviewed items the nightly mining adds per night per (format, topic) (estimate: quota x the share that passes review)
const GROW = { 'trivia|kpop': 2, 'trivia|vtubers': 2, 'trivia|food': 2, 'trivia|cities': 2, 'trivia|anime': 1, 'trivia|cdrama': 1,
  'emoji|anime': 2, 'emoji|kpop song': 2, 'wyr|food': 1, 'wyr|cities': 1, 'wyr|kpop': 1, 'wyr|anime': 1, 'idol|kpop': 1,
  'vtuber|vtubers': 1, 'song|kpop': 1, 'cityphoto|asia': 1 };
const rng = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };

// the live channel's settings (.github/workflows/quiz-channel.yml); a throwaway copy of its memory
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'quiz-sim-'));
for (const f of ['quiz-history.json', 'pop-bank.json', 'performance-stats.json']) {
  const src = path.join(ROOT, 'memory', 'quiz-channel', f);
  if (fs.existsSync(src)) fs.copyFileSync(src, path.join(tmp, f));
}
Object.assign(process.env, {
  MEMORY_DIR: tmp, POP_NEW_FORMATS: '1', QUIZ_LLM: flags.has('--llm') ? 'on' : 'off', QUIZ_THEME: 'asia',
  ...(flags.has('--llm') ? { GEMINI_API_KEY: process.env.GEMINI_API_KEY || 'sim' } : {}),
  QUIZ_FORMATS: 'song,voice,character,idol,opening,scene,duel,cityphoto,vtuber,emoji,trivia,wyr,city,flag,shape,silhouette,eyes,outfit',
});
const wq = require('../pipeline/world-quiz-pipeline');
const { loadStats } = require('../core/performance-tracker');
const balancer = require('../pipeline/quiz-balancer');

const stats = loadStats();
const history = wq.loadHistory();
const realN = history.uploads.length;
const live = wq.poolAvailability();
const { topicFresh, loadBank } = require('../pipeline/pop-quiz-content');
const bank = loadBank();
const ROUNDS = 4;   // quick (3) and standard (5) cuts are both used: ~4 items a quiz
const used = [];    // {at, format, topic}
const t0 = Date.now();
const grown = (f, t, now) => (flags.has('--grow') ? (GROW[`${f}|${t}`] || 0) * Math.floor((now - t0) / 86400000) : 0);
const available = now => (f, t) => {
  if (f === 'wyr' && flags.has('--llm')) return true;
  const start = topicFresh(f, t, bank) + grown(f, t, now);
  if (!live(f, t) && start < 99 && !grown(f, t, now)) return false;
  if (!Number.isFinite(start)) return true;
  const spent = used.filter(u => u.format === f && u.topic === t && now - u.at < 60 * 86400000).length * ROUNDS;
  const need = (f === 'wyr' || f === 'duel' ? 4 : 5) + (f === 'trivia' ? 1 : 0);
  return start - spent >= need;
};

let now = Date.now();
const sim = [];
for (let k = 0; k < days * 2; k++) {
  now += (k % 2 === 0 ? 15 : 9) * 3600000;   // 12:00 and 21:00 UTC
  const p = wq.pickFormat(stats, history, null, { available: available(now), rng, now });
  const u = { format: p.format, theme: p.topic || 'asia', postedAt: new Date(now).toISOString(), publishAt: new Date(now).toISOString(), sim: true };
  history.uploads.push(u);
  used.push({ at: now, format: p.format, topic: p.topic });
  sim.push({ ...u, bucket: balancer.bucketOf(p.format, p.topic), family: balancer.familyOf(p.format) });
}

const count = key => sim.reduce((m, u) => ({ ...m, [u[key]]: (m[u[key]] || 0) + 1 }), {});
const pct = m => Object.entries(m).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v} (${Math.round(100 * v / sim.length)}%)`).join(', ');
console.log(`${sim.length} simulated uploads (${days} days x 2), after ${realN} real ones${flags.size ? ` [${[...flags].join(' ')}]` : ''}`);
const half = sim.slice(sim.length / 2);
const pctOf = (list, key) => Object.entries(list.reduce((m, u) => ({ ...m, [u[key]]: (m[u[key]] || 0) + 1 }), {})).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${Math.round(100 * v / list.length)}%`).join(', ');
console.log(`topics, days ${days / 2 + 1}-${days}: ${pctOf(half, 'bucket')}`);
console.log(`families: ${pct(count('family'))}`);
console.log(`topics:   ${pct(count('bucket'))}`);
console.log(`formats:  ${pct(count('format'))}`);
const topicOf = sim.map(u => `${u.format}/${u.theme}`);
console.log(`format/topic: ${pct(topicOf.reduce((m, k) => ({ ...m, [k]: (m[k] || 0) + 1 }), {}))}`);
// checks: rolling 14-upload topic max, format repeats within 3 days, geography spacing
let maxShare = 0, maxAt = '';
for (let i = 14; i <= sim.length; i++) {
  const w = sim.slice(i - 14, i);
  for (const b of Object.keys(balancer.TOPIC_TARGET)) {
    const s = w.filter(u => u.bucket === b).length / 14;
    if (s > maxShare) { maxShare = s; maxAt = `${b} at upload ${i}`; }
  }
}
let repeats = 0, geoClose = 0;
for (let i = 0; i < sim.length; i++) {
  const t = new Date(sim[i].postedAt).getTime();
  if (sim.slice(0, i).some(u => u.format === sim[i].format && t - new Date(u.postedAt).getTime() < 3 * 86400000 - 3600000)) repeats++;
  if (balancer.GEO_CAPPED.includes(sim[i].format) && sim.slice(Math.max(0, i - 6), i).some(u => balancer.GEO_CAPPED.includes(u.format))) geoClose++;
}
const gaps = f => { const idx = sim.map((u, i) => (u.format === f ? i : -1)).filter(i => i >= 0); return idx.slice(1).map((v, i) => (v - idx[i]) / 2); };
const avg = a => (a.length ? (a.reduce((x, y) => x + y, 0) / a.length).toFixed(1) : '-');
console.log(`max topic share in any 14-upload window: ${Math.round(maxShare * 100)}% (${maxAt})`);
console.log(`format repeats within 3 days: ${repeats}; geography within 7 uploads of another: ${geoClose}`);
const kpop = sim.filter(u => u.bucket === 'kpop').length;
console.log(`idol: ${sim.filter(u => u.format === 'idol').length} uploads, every ${avg(gaps('idol'))} days on average; K-pop topic: ${kpop} uploads (one every ${(days / Math.max(1, kpop)).toFixed(1)} days)`);
console.log(`vtuber topic: ${sim.filter(u => u.bucket === 'vtuber').length}; first 14 picks: ${sim.slice(0, 14).map(u => `${u.format}/${u.theme}`).join(' ')}`);
fs.rmSync(tmp, { recursive: true, force: true });
