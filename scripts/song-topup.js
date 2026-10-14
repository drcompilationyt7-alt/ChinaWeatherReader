/**
 * Monthly AUTOMATIC top-up of the stem edit's song list. Candidates come from three merged sources (pipeline/song-sources.js):
 * the LLM in small JSON batches (Gemini JSON mode, then the OpenRouter free models, tolerant parse, retries with patience), trending
 * YouTube acapella / edit uploads of the last 60 days, and a hand-listed seed list that works with no LLM at all. They are checked by
 * the quality gates of pipeline/song-topup.js; approved songs go to config/song-candidates.json on probation, the report to
 * memory/song-topup-YYYY-MM.json.
 *
 *   node scripts/song-topup.js [--dry-run]     (dry run: gates run, nothing is written)
 */
const top = require('../pipeline/song-topup');
const src = require('../pipeline/song-sources');

const dry = process.argv.includes('--dry-run');
const num = (k, d) => (process.env[k] != null && process.env[k] !== '' ? Number(process.env[k]) : d);
const race = (p, ms) => { let t; return Promise.race([Promise.resolve(p).catch(() => null), new Promise(r => { t = setTimeout(() => r(null), ms); })]).finally(() => clearTimeout(t)); };
let lastProvider = null;

/** one LLM call: Gemini in JSON mode (its own key / model rotation + backoff), then OpenRouter; returns raw text or null */
async function ask(system, user) {
  if (process.env.META_LLM === 'off') return null;
  const ms = num('TOPUP_LLM_CALL_MIN', 8) * 60000;
  if (Object.keys(process.env).some(k => /^GEMINI_API_KEY(_\d+)?$/.test(k) && process.env[k])) {
    try {
      const g = require('../core/gemini-service').getGeminiService();
      const t = await race(g.chat(system, user, { temperature: 0.7, maxTokens: 4000, json: true }), ms);
      if (t) { lastProvider = 'gemini'; return t; }
    } catch {}
  }
  if (Object.keys(process.env).some(k => /^OPENROUTER_API_KEY(_\d+)?$/.test(k) && process.env[k])) {
    try {
      const q = require('../core/openrouter-qa').getOpenRouterQA();
      const t = await race(q.chat(`${system}\n\nRespond ONLY with valid JSON.`, user, { temperature: 0.7, maxTokens: 2500, timeout: 60000, xTitle: 'song top-up' }), 4 * 60000);
      if (t) { lastProvider = 'openrouter'; return t; }
    } catch {}
  }
  return null;
}

async function propose(n, have) {
  const rows = await src.gatherCandidates({ ask, search: top.ytSearch, now: () => new Date(), shows: top.knownSets() }, have,
    { batch: 8, rounds: num('TOPUP_LLM_ROUNDS', 3), pauseMs: num('TOPUP_LLM_PAUSE_S', 90) * 1000, log: m => console.log(`  [candidates] ${m}`) });
  return rows.slice(0, num('TOPUP_MAX_CANDIDATES', 40));
}

(async () => {
  // canary: if YouTube is unreachable from here (bot check, cookies rejected) nothing can be judged: stop and leave everything unchanged
  const canary = await top.ytSearch('BABYMONSTER SHEESH official', { n: 2 });
  if (!canary.length || !canary.some(r => r.views > 0 && r.height > 0)) {
    console.log('infra blocked: YouTube search returned no usable metadata (bot check / cookies). The list and the rejections stay unchanged.');
    return;
  }
  const out = await top.runTopup({ propose, search: top.ytSearch, concurrency: num('TOPUP_CONCURRENCY', 2), probe: process.env.TOPUP_PROBE === 'off' ? null : top.probeSource, now: () => new Date() }, { write: !dry });
  console.log(`candidates ${out.candidates}, approved ${out.approved.length}, retired ${out.retired.length} (LLM: ${lastProvider || 'none'})${dry ? ' [dry run]' : ''}`);
  for (const r of out.report) console.log(`  ${r.verdict.padEnd(8)} ${r.name} | ${r.why}`);
  for (const a of out.approved) { const r = out.report.find(x => x.name.toLowerCase() === a); console.log(`APPROVED ${a} | ${r && r.artist} | ${r && r.theme} | ${r && r.source} | ${r && r.gates && r.gates.popular && r.gates.popular.why}`); }
})().catch(e => { console.error(`song top-up failed (the list stays as it is): ${e.stack || e.message}`); process.exit(0); });
