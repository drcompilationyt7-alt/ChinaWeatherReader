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

// What the quiz is about, from format AND topic (2026-10-23: a K-pop song emoji quiz said "guess the anime").
const SUBJECT = { anime: 'anime', 'kpop song': 'K-pop song', kpop: 'K-pop', vtubers: 'VTuber', cdrama: 'Chinese drama',
  cities: 'Asian city', food: 'Asian dish', CN: 'Chinese', JP: 'Japanese', KR: 'Korean', mixed: 'Asian pop culture' };
function quizWhat(plan) {
  const t = SUBJECT[plan.topic] || null;
  const subj = t || 'pop culture';
  const who = plan.topic === 'vtubers' ? 'VTuber' : plan.topic === 'kpop' ? 'K-pop idol' : 'anime character';
  switch (plan.format) {
    case 'character': return `guess the ${who} from a blurred picture`;
    case 'silhouette': return `guess the ${who} from their silhouette`;
    case 'eyes': return `guess the ${who} from one eye`;
    case 'outfit': return `guess the ${who} from their outfit`;
    case 'duel': return `pick which of two ${who}s fans prefer`;
    case 'wyr': return `would you rather (${subj} edition)`;
    case 'trivia': return `${subj} trivia`;
    case 'emoji': return `guess the ${subj} from emojis`;
    case 'opening': return 'guess the anime from its opening song';
    case 'song': return `guess the ${plan.topic === 'anime' ? 'anime opening' : subj} song`;
    case 'scene': return 'guess the anime from one scene';
    case 'voice': return 'guess the anime character from their voice';
    case 'idol': return 'guess the K-pop idol';
    case 'vtuber': return 'guess the VTuber';
    case 'nightcity': return 'guess the Asian city from its night skyline';
    case 'cityphoto': return 'guess the Asian city from a photo';
    case 'cuisine': return 'guess the Asian dish';
    default: return `a ${subj} quiz`;
  }
}

// Topic words a spoken line may use; a line naming another topic is wrong for this quiz.
const TOPIC_WORDS = {
  anime: /\b(anime|manga|otaku)\b/i, kpop: /\b(k-?pop|idols?)\b/i, vtuber: /\bv-?tubers?\b/i,
  city: /\b(cit(y|ies)|skylines?|travel|trips?)\b/i, food: /\b(food|dish(es)?|eat|taste|cuisine|hungry)\b/i,
  song: /\b(songs?|tracks?|tunes?|music|openings?)\b/i, flag: /\b(flags?|countr(y|ies)|maps?)\b/i,
};
function allowedTopics(plan) {
  if (plan.topic === 'mixed') return new Set(Object.keys(TOPIC_WORDS));
  const a = new Set();
  const f = plan.format, t = plan.topic;
  if (t === 'anime' || ['character', 'silhouette', 'eyes', 'outfit', 'opening', 'scene', 'voice'].includes(f) && t !== 'kpop' && t !== 'vtubers') a.add('anime');
  if (t === 'kpop' || t === 'kpop song' || f === 'idol') a.add('kpop');
  if (t === 'vtubers' || f === 'vtuber') a.add('vtuber');
  if (t === 'cities' || ['nightcity', 'cityphoto', 'city'].includes(f) || (f === 'wyr' && /travel/i.test(plan.edition && plan.edition.id || ''))) a.add('city');
  if (t === 'food' || f === 'cuisine' || (f === 'wyr' && /food/i.test(plan.edition && plan.edition.id || ''))) a.add('food');
  if (t === 'kpop song' || ['song', 'opening'].includes(f) || (f === 'emoji' && /song/.test(t))) a.add('song');
  if (['flag', 'shape', 'capital', 'city'].includes(f)) a.add('flag');
  return a;
}
function wrongTopic(plan, line) {
  const ok = allowedTopics(plan);
  for (const [k, re] of Object.entries(TOPIC_WORDS)) if (!ok.has(k) && re.test(String(line || ''))) return `wrong topic (${k})`;
  return null;
}

function prompt(plan, edition, n) {
  const fmt = plan.format;
  const what = quizWhat(plan);
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
    const e1 = checkLine(slot, main, false) || wrongTopic(plan, main);
    const e2 = checkLine(slot, short, true) || wrongTopic(plan, short);
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

module.exports = { quizWhat, wrongTopic, allowedTopics, aiNarratorLines, checkLine, recordAiLines, BUDGET };
