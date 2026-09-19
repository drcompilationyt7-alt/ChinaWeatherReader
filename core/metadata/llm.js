/**
 * LLM access for the metadata optimizer: Gemini (core/gemini-service.js) first, the free
 * OpenRouter models (core/openrouter-qa.js) second, each behind a hard timeout. Gemini's own
 * retry loop can sleep minutes on a 429; once a provider fails or times out it is skipped for
 * the rest of the run and the optimizer falls back to its templates.
 *
 * META_LLM=off disables it; setLLM(fn) injects a fake (async (system, user) => object|null) for tests.
 */
let injected = null;
const down = { gemini: false, openrouter: false };
let lastProvider = null;

function hasGeminiKeys() {
  return Object.keys(process.env).some(k => /^GEMINI_API_KEY(_\d+)?$/.test(k) && process.env[k]);
}
function hasOpenRouterKeys() {
  return Object.keys(process.env).some(k => /^OPENROUTER_API_KEY(_\d+)?$/.test(k) && process.env[k]);
}

function available() {
  if (injected) return true;
  if (process.env.META_LLM === 'off') return false;
  return (!down.gemini && hasGeminiKeys()) || (!down.openrouter && hasOpenRouterKeys());
}

function race(promise, ms) {
  let timer;
  return Promise.race([
    Promise.resolve(promise).catch(() => null),
    new Promise(res => { timer = setTimeout(() => res(null), ms); if (timer.unref) timer.unref(); }),
  ]).finally(() => clearTimeout(timer));
}

function parseJSON(text) {
  if (!text) return null;
  if (typeof text === 'object') return text;
  const s = String(text).replace(/```json/gi, '').replace(/```/g, '');
  for (const re of [/\{[\s\S]*\}/, /\[[\s\S]*\]/]) {
    const m = s.match(re);
    if (m) { try { return JSON.parse(m[0]); } catch {} }
  }
  return null;
}

/** JSON from the first provider that answers in time, or null. */
async function chatJSON(system, user, { timeoutMs = 45000, temperature = 0.9, maxTokens = 2048 } = {}) {
  if (injected) {
    const r = await race(injected(system, user), timeoutMs);
    lastProvider = 'injected';
    return parseJSON(r);
  }
  if (process.env.META_LLM === 'off') return null;
  if (!down.gemini && hasGeminiKeys()) {
    try {
      const { getGeminiService } = require('../gemini-service');
      const g = getGeminiService();
      if (g && g.getStats().keysLoaded > 0) {
        const r = await race(g.chatJSON(system, user, { temperature, maxTokens }), timeoutMs);
        if (r && typeof r === 'object') { lastProvider = 'gemini'; return r; }
      }
    } catch {}
    down.gemini = true;
  }
  if (!down.openrouter && hasOpenRouterKeys()) {
    try {
      const { getOpenRouterQA } = require('../openrouter-qa');
      const q = getOpenRouterQA();
      const text = await race(q.chat(`${system}\n\nRespond ONLY with valid JSON.`, user,
        { temperature, maxTokens: Math.min(maxTokens, 1500), timeout: Math.min(30000, timeoutMs), xTitle: 'metadata optimizer' }), timeoutMs);
      const r = parseJSON(text);
      if (r && typeof r === 'object') { lastProvider = 'openrouter'; return r; }
    } catch {}
    down.openrouter = true;
  }
  return null;
}

module.exports = {
  chatJSON, available, parseJSON,
  setLLM: fn => { injected = fn || null; },
  reset: () => { down.gemini = false; down.openrouter = false; },
  provider: () => lastProvider,
};
