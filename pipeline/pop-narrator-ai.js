/**
 * AI-written narrator lines for the pop quiz (owner 2026-10-09), with the fixed pools in core/quiz/render_pop.py as the fallback.
 *
 * Gemini (then OpenRouter, the same chain as the other generators) writes, per video, flavour / hype lines matched to the format
 * and the themed edition: an intro, an outro and reveal TEMPLATES with {a} where the code inserts the exact answer name. The
 * model never writes a name and never states a fact. Every line comes as a main version + a shorter rewrite.
 *
 * Validation (any failure -> that slot falls back to the fixed pool): word / character budgets per slot; {a} exactly once in a
 * reveal template and nowhere else; letters and . , ! ? ' - only (no digits, emoji, quotes); no character / show names (every
 * name token in the AniList snapshot); no claim words (best, first, most, famous, won, season, episode, ...); no profanity;
 * nothing used in the last 60 days (MEMORY_DIR/pop-ai-lines.json).
 * Timing is checked after the TTS in the renderer (render_pop.ai_line_fits): a line too long for its slot is replaced by the
 * shorter rewrite, then by the fixed-pool line, so the video's timing never gets looser than with the fixed lines.
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const MEMORY = path.resolve(ROOT, process.env.MEMORY_DIR || 'memory', 'pop-ai-lines.json');
const REUSE_DAYS = 60;
const BUDGET = {   // words / characters per slot (main, short)
  intro: { words: [12, 7], chars: [70, 42] },
  outro: { words: [10, 6], chars: [60, 36] },
  reveal: { words: [6, 3], chars: [40, 22] },   // the template's own words, {a} not counted
};
const CLAIMS = /\b(best|first|last|most|least|only|never|always|famous|popular|won|wins|award|season|episode|chapter|volume|creator|author|studio|born|years?|old|age|actually|fact|facts|true|real|official|canon|record|million|percent|everyone|nobody|fans|voted|ranked|number)\b/i;
const PROFANITY = /\b(damn|hell|crap|shit|fuck|bitch|ass|bastard|sexy|hot|thicc|waifu|daddy|kill|die|dead|blood)\b/i;
const ALLOWED = /^[A-Za-z .,!?'{}-]+$/;

let NAME_TOKENS = null;
function nameTokens() {
  if (NAME_TOKENS) return NAME_TOKENS;
  const common = new Set(['the', 'and', 'you', 'who', 'one', 'day', 'beach', 'summer', 'night', 'love', 'king', 'queen', 'star', 'girl',
    'boy', 'man', 'lady', 'black', 'white', 'red', 'blue', 'little', 'big', 'sun', 'moon', 'sky', 'snow', 'rose', 'hope', 'joy', 'grace']);
  NAME_TOKENS = new Set();
  try {
    const chars = JSON.parse(fs.readFileSync(path.join(ROOT, 'core', 'quiz', 'assets', 'anime-characters.json'), 'utf8')).characters;
    for (const c of chars) {
      for (const w of String(c.name).toLowerCase().split(/[^a-z]+/)) if (w.length >= 3 && !common.has(w)) NAME_TOKENS.add(w);
    }
  } catch {}
  return NAME_TOKENS;
}

function memory() {
  try { return JSON.parse(fs.readFileSync(MEMORY, 'utf8')); } catch { return { used: {} }; }
}

function recentlyUsed(line) {
  const u = memory().used[String(line).toLowerCase()];
  return !!u && (Date.now() - new Date(u).getTime()) < REUSE_DAYS * 86400000;
}

/** Record the AI lines a published video used (call after the upload). */
function recordAiLines(lines) {
  if (!lines || !lines.length) return;
  const m = memory();
  const now = new Date().toISOString();
  for (const l of lines) m.used[String(l).toLowerCase()] = now;
  // keep the file small: drop entries older than the reuse window
  for (const [k, v] of Object.entries(m.used)) if ((Date.now() - new Date(v).getTime()) > REUSE_DAYS * 86400000) delete m.used[k];
  fs.mkdirSync(path.dirname(MEMORY), { recursive: true });
  fs.writeFileSync(MEMORY, JSON.stringify(m, null, 1));
}

/** null when the line is fine for the slot, else why not. */
function checkLine(slot, line, short) {
  const b = BUDGET[slot];
  const s = String(line || '').trim();
  if (!s) return 'empty';
  if (!ALLOWED.test(s)) return 'characters';
  const holes = (s.match(/\{a\}/g) || []).length;
  if (slot === 'reveal' ? holes !== 1 : holes !== 0) return 'placeholder';
  if (/[{}]/.test(s.replace(/\{a\}/g, ''))) return 'braces';
  const words = s.replace(/\{a\}/g, '').split(/\s+/).filter(w => /[A-Za-z]/.test(w));
  if (words.length > b.words[short ? 1 : 0]) return 'too many words';
  if (s.replace(/\{a\}/g, '').length > b.chars[short ? 1 : 0]) return 'too long';
  if (CLAIMS.test(s)) return 'claim word';
  if (PROFANITY.test(s)) return 'profanity';
  const toks = nameTokens();
  if (s.replace(/\{a\}/g, '').toLowerCase().split(/[^a-z]+/).some(w => toks.has(w))) return 'a name';
  // capitalised words mid-sentence are names (except I)
  const mid = s.replace(/\{a\}/g, '').split(/(?<=[.!?])\s+/).flatMap(sent => sent.split(/\s+/).slice(1));
  if (mid.some(w => /^[A-Z]/.test(w) && w !== 'I' && !/^I'/.test(w))) return 'capitalised word';
  if (recentlyUsed(s)) return 'used in the last 60 days';
  return null;
}

function prompt(plan, edition, n) {
  const fmt = plan.format;
  const what = { character: 'guess the anime character from a blurred picture', silhouette: 'guess the anime character from their silhouette',
    eyes: 'guess the anime character from one eye', outfit: 'guess the anime character from their outfit', duel: 'pick which of two characters fans prefer',
    wyr: 'would you rather', trivia: 'anime trivia', emoji: 'guess the anime from emojis', opening: 'guess the anime opening song' }[fmt] || 'a pop culture quiz';
  const theme = edition ? `This is the "${edition.name}" themed edition (${edition.label}); match its mood (for example: "${(edition.intro || [])[0] || ''}").` : '';
  return [
    'You write short spoken lines for the narrator of a YouTube Shorts quiz. Calm, cool, upbeat. Pure flavour and hype: '
    + 'NEVER state a fact about any character, show, person or event, NEVER write a name, number, statistic or claim, '
    + 'nothing about bodies or looks, no slang, no profanity, plain English, easy for text-to-speech (short words, natural rhythm). English only.',
    `Quiz: ${what}. ${theme}\nWrite:\n`
    + '- "intro": the opening line (max 12 words), plus "intro_short" (max 7 words);\n'
    + '- "outro": the closing line asking viewers to comment their score (max 10 words), plus "outro_short" (max 6 words);\n'
    + `- "reveals": ${n} reveal TEMPLATES said right after the answer appears. Each contains {a} exactly once where the code puts the `
    + 'answer\'s name, at most 6 other words (e.g. "There it is, {a}!", "{a}. Nailed it?"), each with a "short" version (max 3 other words, '
    + 'e.g. "{a}!"). Never write a name yourself. Vary them.\n'
    + 'Return JSON: {"intro": "", "intro_short": "", "outro": "", "outro_short": "", "reveals": [{"main": "", "short": ""}]}',
  ];
}

/**
 * Lines for one plan: {intro: [main, short] | null, outro: [...] | null, reveal: [[main, short] | null per round], source, rejected}.
 * Slots that fail validation are null (the renderer uses the fixed pool there).
 */
async function aiNarratorLines(plan, edition, llmJSON) {
  if (process.env.POP_AI_LINES !== '1') return null;
  const n = plan.rounds.length;
  let r = null;
  try { r = await llmJSON(...prompt(plan, edition, n)); } catch {}
  if (!r) return null;
  const rejected = [];
  const pair = (slot, main, short) => {
    const e1 = checkLine(slot, main, false);
    const e2 = checkLine(slot, short, true);
    if (e1) rejected.push(`${slot}: "${String(main).slice(0, 50)}" (${e1})`);
    if (e2) rejected.push(`${slot} short: "${String(short).slice(0, 50)}" (${e2})`);
    if (e1 && e2) return null;
    return [e1 ? String(short).trim() : String(main).trim(), e2 ? null : String(short).trim()];
  };
  const reveals = (Array.isArray(r.reveals) ? r.reveals : []).slice(0, n).map(x => pair('reveal', x && x.main, x && x.short));
  while (reveals.length < n) reveals.push(null);
  return {
    intro: pair('intro', r.intro, r.intro_short), outro: pair('outro', r.outro, r.outro_short), reveal: reveals,
    source: 'llm', rejected,
  };
}

module.exports = { aiNarratorLines, checkLine, recordAiLines, BUDGET };
