/**
 * Question bank and round picker for the Asian pop culture formats (emoji, trivia, wyr,
 * city, plus the media formats drawn from snapshots: character, idol, opening, cityphoto,
 * vtuber, duel, scene, voice).
 *
 * The bank starts from the hand-checked items in core/quiz/assets/pop-bank.json
 * and grows with LLM-written items (Gemini first, OpenRouter as fallback).
 * Every generated batch goes through a second "fact-check" call and only the
 * items it confirms are kept, because a wrong answer in a quiz costs trust.
 *
 * Generated items and the used-item log live in MEMORY_DIR/pop-bank.json, so
 * each channel keeps its own bank and never repeats a question for 60 days.
 */
const path = require('path');
const fs = require('fs');
const { Logger } = require('../core/logger');

const logger = new Logger('PopQuiz');
const ROOT = path.join(__dirname, '..');
const SEED_FILE = path.join(ROOT, 'core', 'quiz', 'assets', 'pop-bank.json');
const BANK_FILE = path.resolve(ROOT, process.env.MEMORY_DIR || 'memory', 'pop-bank.json');
const REUSE_AFTER_DAYS = 60;
// snapshots from scripts/build-pop-media.py (AniList, Wikidata/Commons, Virtual YouTuber Wiki)
const MEDIA = {
  character: ['anime-characters.json', 'characters'],
  idol: ['kpop-idols.json', 'idols'],
  opening: ['anime-list.json', 'anime'],
  cityphoto: ['city-photos.json', 'cities'],
  vtuber: ['vtubers.json', 'vtubers'],
  scene: ['anime-list.json', 'anime'],
  voice: ['anime-characters.json', 'characters'],
  duel: ['anime-characters.json', 'characters'],
};
// clips can fail to download: spare candidates per level (hard shows fail most often: fewer uploads)
const SPARES = { opening: [1, 1, 1], scene: [3, 3, 2, 3, 1], voice: [3, 2, 3, 1, 2, 1] };
const RAMPS = { 3: [1, 2, 3], 4: [1, 2, 2, 3], 5: [1, 1, 2, 2, 3] };
// duel questions (mirrors core/quiz/render_pop.py): neutral ones fit any pair, the rest need both characters 16+
const DUEL_NEUTRAL = ['Who would you trust to protect you?', 'Who wins in a fight?', "Who's the better teacher?",
  'Who would you rather go on an adventure with?'];
const DUEL_ADULT = ['Who would you rather have as your roommate?'];
const DUEL_SKIP = new Set(['bakemonogatari']);  // fan-service heavy even where AniList does not tag it
// openings with fan-service shots, although AniList does not flag the show (the scene quiz plays video)
const SCENE_SKIP = ['Bakemonogatari', 'Kakegurui', "DON'T TOY WITH ME, MISS NAGATORO", 'The Pet Girl of Sakurasou', "Masamune-kun's Revenge",
  'Fire Force', 'Akame ga Kill!', 'Rascal Does Not Dream of Bunny Girl Senpai', 'Arifureta', 'The Misfit of Demon King Academy',
  "Miss Kobayashi's Dragon Maid", 'Cyberpunk: Edgerunners', 'The Quintessential Quintuplets', 'Nisekoi', 'Monogatari'];

const TOPICS = {
  emoji: ['anime', 'kpop song'],
  trivia: ['kpop', 'anime', 'vtubers', 'cdrama', 'cities'],
  wyr: ['anime', 'kpop', 'food', 'cities'],
  city: ['CN', 'JP', 'KR'],
  character: ['anime'], idol: ['kpop'], opening: ['anime'],
  cityphoto: ['asia'], vtuber: ['vtubers'], scene: ['anime'], voice: ['anime'], duel: ['anime'],
};
const TOPIC_WEIGHTS = {
  character: { anime: 1 }, idol: { kpop: 1 }, opening: { anime: 1 },
  cityphoto: { asia: 1 }, vtuber: { vtubers: 1 }, scene: { anime: 1 }, voice: { anime: 1 }, duel: { anime: 1 },
  emoji: { anime: 0.6, 'kpop song': 0.4 },
  trivia: { kpop: 0.3, anime: 0.3, vtubers: 0.15, cdrama: 0.1, cities: 0.15 },
  wyr: { anime: 0.4, kpop: 0.25, food: 0.2, cities: 0.15 },
  city: { CN: 0.45, JP: 0.35, KR: 0.2 },
};
const TOPIC_DESC = {
  anime: 'anime (hugely popular series from the last 30 years)',
  'kpop song': 'K-pop songs (answer as "Title (Artist)")',
  kpop: 'K-pop groups, idols, fandom names and hit songs',
  vtubers: 'VTubers (Hololive, Nijisanji, indie VTubers)',
  cdrama: 'Chinese dramas, Chinese movie stars and Hong Kong action films',
  cities: 'cities of China, Japan and South Korea (landmarks, food, nicknames)',
  food: 'Asian food (Japanese, Korean, Chinese, Thai, Vietnamese)',
};

function key(fmt, it) {
  if (fmt === 'emoji') return `emoji:${String(it.answer).toLowerCase()}`;
  if (fmt === 'trivia') return `trivia:${String(it.question).toLowerCase()}`;
  if (fmt === 'wyr') return `wyr:${String(it.a).toLowerCase()}|${String(it.b).toLowerCase()}`;
  if (['character', 'idol', 'vtuber', 'voice'].includes(fmt)) return `${fmt}:${String(it.name).toLowerCase()}`;
  if (fmt === 'opening' || fmt === 'scene') return `${fmt}:${it.id}`;
  if (fmt === 'duel') return duelKey(it.a, it.b);
  if (fmt === 'cityphoto') return `cityphoto:${it.iso2}:${String(it.name).toLowerCase()}`;
  return `city:${it.iso2}:${String(it.name).toLowerCase()}`;
}

const duelKey = (a, b) => `duel:${Math.min(a.id, b.id)}-${Math.max(a.id, b.id)}`;

/** 'Attack on Titan Final Season' -> 'attack on': one entry per series in a quiz (mirrors render_pop.franchise). */
function franchise(title) {
  const t = String(title || '').toLowerCase();
  const head = t.split(/[:\-–]/)[0];
  return ((head.trim().length >= 4 ? head : t).match(/[a-z0-9]+/g) || []).slice(0, 2).join(' ');
}

const isSequel = t => /\b(season|part|cour)\s*\d|final season|\b(ii|iii|iv)\b|\d+(st|nd|rd|th) season|\barc\b|√a|:re\b|\bmovie\b|\?$/i
  .test(String(t || ''));

/** AniList age strings ('17-', '15-16 (series)', '40 Days') -> first number, or null. */
function ageOf(a) {
  const s = String(a || '');
  if (!s || /day|week|month/i.test(s)) return null;
  const m = s.match(/\d+/);
  return m ? Number(m[0]) : null;
}

/** 'noragami' / 'noragami aragoto': one franchise key is the start of the other. */
function sameSeries(a, b) {
  const x = franchise(a).split(' '), y = franchise(b).split(' ');
  const k = Math.min(x.length, y.length);
  return k > 0 && x.slice(0, k).join(' ') === y.slice(0, k).join(' ');
}

/** What each media format draws from: nothing flagged nsfw (Ecchi / sexual-content tags), one entry per series for scenes. */
function mediaPool(fmt, items) {
  if (fmt === 'scene') {
    const keep = [];
    for (const a of [...items].sort((p, q) => (q.popularity || 0) - (p.popularity || 0))) {
      if (!a.nsfw && !isSequel(a.title) && !SCENE_SKIP.some(s => sameSeries(a.title, s)) && !keep.some(k => sameSeries(a.title, k.title))) keep.push(a);
    }
    return keep;
  }
  // voice-line videos exist for the well-known characters only
  if (fmt === 'voice') return items.slice(0, 150).map((c, k) => ({ ...c, difficulty: k < 25 ? 1 : k < 70 ? 2 : 3 })).filter(c => !c.nsfw);
  return items;
}

/**
 * Duel pairs: two popular characters (same series, else same gender among the top 80), a question
 * each, and the real AniList favourites as the fans' pick. Difficulty = how close the vote is.
 */
function duelRounds(chars, n, fresh) {
  const top = chars.slice(0, 150).filter(c => !c.nsfw && !DUEL_SKIP.has(franchise(c.anime)));
  const rank = new Map(top.map((c, k) => [c.id, k]));
  const same = [], cross = [];
  for (let x = 0; x < top.length; x++) {
    for (let y = x + 1; y < top.length; y++) {
      const a = top[x], b = top[y];
      if (franchise(a.anime) === franchise(b.anime)) same.push([a, b]);
      else if (rank.get(a.id) < 80 && rank.get(b.id) < 80 && a.gender === b.gender && ['Male', 'Female'].includes(a.gender)) cross.push([a, b]);
    }
  }
  const level = ([a, b]) => {
    const r = Math.max(a.favourites, b.favourites) / Math.max(1, Math.min(a.favourites, b.favourites));
    return r >= 1.6 ? 1 : r >= 1.2 ? 2 : 3;
  };
  const pickOne = arr => arr[Math.floor(Math.random() * arr.length)];
  const rounds = [], used = new Set(), asked = new Set(), shows = new Set();
  for (const d of RAMPS[n] || RAMPS[4]) {
    let pick = null;
    for (const pool of (Math.random() < 0.75 ? [same, cross] : [cross, same])) {
      let cand = pool.filter(p => level(p) === d && !used.has(p[0].id) && !used.has(p[1].id) && fresh(duelKey(p[0], p[1])));
      const varied = cand.filter(p => !shows.has(franchise(p[0].anime)) && !shows.has(franchise(p[1].anime)));  // vary the shows
      if (varied.length) cand = varied;
      if (cand.length) { pick = pickOne(cand); shows.add(franchise(pick[0].anime)); shows.add(franchise(pick[1].anime)); break; }
    }
    if (!pick) continue;
    const [a, b] = Math.random() < 0.5 ? pick : [pick[1], pick[0]];
    used.add(a.id); used.add(b.id);
    const grown = [a, b].every(c => (ageOf(c.age) ?? 0) >= 16);
    const qs = [...DUEL_NEUTRAL, ...(grown ? DUEL_ADULT : [])].filter(q => !asked.has(q));
    const special = !grown ? null : a.gender === 'Female' && b.gender === 'Female' ? 'Best girl?' : a.gender === 'Male' && b.gender === 'Male' ? 'Best boy?' : null;
    const question = special && !asked.has(special) && Math.random() < 0.5 ? special : pickOne(qs.length ? qs : DUEL_NEUTRAL);
    asked.add(question);
    const keep = c => ({ id: c.id, name: c.name, anime: c.anime, image: c.image, favourites: c.favourites });
    const winner = a.favourites >= b.favourites ? 'a' : 'b';
    rounds.push({ a: keep(a), b: keep(b), question, difficulty: d, winner, answer: (winner === 'a' ? a : b).name });
  }
  return rounds;
}

function loadBank() {
  const seed = JSON.parse(fs.readFileSync(SEED_FILE, 'utf8'));
  let mem = {};
  try { mem = JSON.parse(fs.readFileSync(BANK_FILE, 'utf8')); } catch {}
  const bank = { used: mem.used || {} };
  for (const fmt of ['emoji', 'trivia', 'wyr']) {
    const seen = new Set();
    bank[fmt] = [];
    for (const it of [...(seed[fmt] || []), ...((mem.items && mem.items[fmt]) || [])]) {
      const k = key(fmt, it);
      if (!seen.has(k)) { seen.add(k); bank[fmt].push(it); }
    }
  }
  bank.city = (seed.cities || []).map(c => ({ ...c, topic: c.iso2 }));
  for (const [fmt, [file, field]] of Object.entries(MEDIA)) {
    try {
      const data = JSON.parse(fs.readFileSync(path.join(ROOT, 'core', 'quiz', 'assets', file), 'utf8'))[field] || [];
      bank[fmt] = mediaPool(fmt, data).map(x => ({ ...x, topic: TOPICS[fmt][0] }));
    } catch { bank[fmt] = []; }
  }
  bank.generated = (mem.items) || { emoji: [], trivia: [], wyr: [] };
  return bank;
}

function saveBank(bank) {
  fs.mkdirSync(path.dirname(BANK_FILE), { recursive: true });
  fs.writeFileSync(BANK_FILE, JSON.stringify({ used: bank.used, items: bank.generated }, null, 1));
}

function isFresh(bank, fmt, it) {
  const at = bank.used[key(fmt, it)];
  return !at || (Date.now() - new Date(at).getTime()) > REUSE_AFTER_DAYS * 86400000;
}

function pickWeighted(weights) {
  const e = Object.entries(weights);
  let x = Math.random() * e.reduce((a, [, w]) => a + w, 0);
  for (const [k, w] of e) { x -= w; if (x <= 0) return k; }
  return e[e.length - 1][0];
}

// ─── LLM ─────────────────────────────────────────────────────────

let llmDown = false;

async function withTimeout(p, ms) {
  let t;
  return Promise.race([p.catch(() => null), new Promise(r => { t = setTimeout(() => r(null), ms); })]).finally(() => clearTimeout(t));
}

async function llmJSON(system, user) {
  if (llmDown || process.env.QUIZ_LLM === 'off') return null;
  try {
    const { getGeminiService } = require('../core/gemini-service');
    const g = getGeminiService();
    if (g && g.getStats().keysLoaded > 0) {
      const r = await withTimeout(g.chatJSON(system, user), 90000);
      if (r) return r;
    }
  } catch {}
  try {
    const { OpenRouterProvider } = require('../providers/openrouter-provider');
    const or = new OpenRouterProvider(require('../core/config'));
    const r = await withTimeout(or.chatJSON(system, user, { maxTokens: 3000 }), 120000);
    if (r) return r;
  } catch {}
  llmDown = true;
  return null;
}

const SAFETY = 'Only use well-documented, neutral facts. Never mention scandals, dating rumours, politics, '
  + 'religion, ages or private details of real people. English only.';

function genPrompt(fmt, topic, n, avoid) {
  const avoidLine = avoid.length ? `Do NOT reuse any of these: ${avoid.slice(0, 60).join('; ')}.` : '';
  if (fmt === 'emoji') {
    return [`You write "guess it from the emojis" puzzles for a YouTube Shorts quiz channel about Asian pop culture. ${SAFETY}`,
      `Write ${n} puzzles about ${TOPIC_DESC[topic]}. Each: "answer" (official English name), "emojis" (exactly 4 emojis `
      + 'that hint at famous visual details; never letters, digits or flags that spell the answer), "difficulty" 1-3 '
      + '(1 = everyone knows it), "fact" (true fun fact, max 50 characters). Mix difficulties. ' + avoidLine
      + '\nReturn JSON: {"items": [{"answer": "", "emojis": ["", "", "", ""], "difficulty": 1, "fact": ""}]}'];
  }
  if (fmt === 'trivia') {
    return [`You write multiple-choice trivia for a YouTube Shorts quiz channel about Asian pop culture. ${SAFETY}`,
      `Write ${n} questions about ${TOPIC_DESC[topic]}. Each: "question" (max 60 characters), "options" (4 short options, `
      + 'max 22 characters each, exactly one correct, the others clearly wrong), "answer" (index 0-3 of the correct option, '
      + 'vary the position), "difficulty" 1-3, "emoji" (one emoji that fits the question). Facts must be stable, not things '
      + 'that change month to month. ' + avoidLine
      + '\nReturn JSON: {"items": [{"question": "", "options": ["", "", "", ""], "answer": 0, "difficulty": 1, "emoji": ""}]}'];
  }
  return [`You write "Would you rather" dilemmas for a YouTube Shorts channel about Asian pop culture. Fun, clean, for teens and adults. ${SAFETY}`,
    `Write ${n} dilemmas about ${TOPIC_DESC[topic]}. Each: "a" and "b" (the two choices, max 34 characters each, start with a verb, `
    + 'both tempting), "emojiA", "emojiB" (one emoji each), "twistA", "twistB" (optional funny catch starting with "but", max 34 '
    + 'characters, leave "" for most; at most one per dilemma). ' + avoidLine
    + '\nReturn JSON: {"items": [{"a": "", "b": "", "emojiA": "", "emojiB": "", "twistA": "", "twistB": ""}]}'];
}

function validItem(fmt, it) {
  const s = v => typeof v === 'string' && v.trim().length > 0;
  if (fmt === 'emoji') return s(it.answer) && Array.isArray(it.emojis) && it.emojis.length >= 3 && it.emojis.every(s)
    && !it.emojis.some(e => /[a-z0-9]/i.test(e)) && it.answer.length <= 40;
  if (fmt === 'trivia') return s(it.question) && it.question.length <= 80 && Array.isArray(it.options) && it.options.length === 4
    && it.options.every(o => s(o) && o.length <= 26) && Number.isInteger(it.answer) && it.answer >= 0 && it.answer < 4
    && new Set(it.options.map(o => o.toLowerCase())).size === 4;
  return s(it.a) && s(it.b) && it.a.length <= 40 && it.b.length <= 40 && s(it.emojiA) && s(it.emojiB)
    && String(it.twistA || '').length <= 40 && String(it.twistB || '').length <= 40;
}

/** Second opinion: keep only items the checker confirms. */
async function verify(fmt, items) {
  if (fmt === 'wyr') return items;  // opinions, nothing to fact-check
  const list = items.map((it, i) => (fmt === 'emoji'
    ? `${i}. answer "${it.answer}", emojis ${it.emojis.join(' ')}, fact "${it.fact}"`
    : `${i}. Q "${it.question}" options ${JSON.stringify(it.options)} correct=${JSON.stringify(it.options[it.answer])}`)).join('\n');
  const r = await llmJSON('You are a strict fact-checker for a quiz channel. When unsure, reject.',
    (fmt === 'emoji'
      ? 'For each puzzle: is the answer a real, correctly spelled title, do the emojis clearly fit it, and is the fact true?'
      : 'For each question: is the marked option correct, are the other three definitely wrong, and is the question unambiguous?')
    + `\n${list}\nReturn JSON: {"ok": [indexes of items that pass every check]}`);
  if (!r || !Array.isArray(r.ok)) return [];
  const ok = new Set(r.ok.map(Number));
  return items.filter((_, i) => ok.has(i));
}

async function generate(bank, fmt, topic, n = 12) {
  const existing = bank[fmt].filter(i => i.topic === topic).map(i => (fmt === 'emoji' ? i.answer : fmt === 'trivia' ? i.question : `${i.a} / ${i.b}`));
  const [sys, msg] = genPrompt(fmt, topic, n, existing);
  const r = await llmJSON(sys, msg);
  const raw = (r && Array.isArray(r.items) ? r.items : []).map(it => ({ ...it, topic }));
  const seen = new Set(bank[fmt].map(i => key(fmt, i)));
  const candidates = raw.filter(it => validItem(fmt, it) && !seen.has(key(fmt, it)));
  const kept = candidates.length ? await verify(fmt, candidates) : [];
  if (kept.length) {
    bank.generated[fmt] = [...(bank.generated[fmt] || []), ...kept];
    bank[fmt].push(...kept);
    saveBank(bank);
  }
  logger.info(`Generated ${fmt}/${topic}: ${raw.length} written, ${candidates.length} valid, ${kept.length} passed the fact-check`);
  return kept.length;
}

/**
 * Pick the rounds for one short, generating new items first when the fresh
 * pool for the topic is running low.
 * @returns {Promise<{format, topic, rounds, keys}>}
 */
async function buildPopPlan(fmt, { topic = null, rounds = null } = {}) {
  const bank = loadBank();
  topic = topic || pickWeighted(TOPIC_WEIGHTS[fmt]);
  const n = rounds || (fmt === 'wyr' || fmt === 'duel' ? 4 : 5);
  if (fmt === 'duel') {
    let picked = duelRounds(bank.duel, n, k => !bank.used[k] || (Date.now() - new Date(bank.used[k]).getTime()) > REUSE_AFTER_DAYS * 86400000);
    if (picked.length < Math.min(3, n)) picked = duelRounds(bank.duel, n, () => true);  // every pair used lately: allow repeats
    return { plan: { format: 'duel', topic: 'anime', rounds: picked }, keys: picked.map(r => key('duel', r)) };
  }
  const fresh = () => bank[fmt].filter(i => i.topic === topic && isFresh(bank, fmt, i));
  if (!['city', ...Object.keys(MEDIA)].includes(fmt) && fresh().length < n + 4) {
    try { await generate(bank, fmt, topic); } catch (e) { logger.warn(`Generation failed: ${(e.message || '').slice(0, 100)}`); }
  }
  let pool = fresh();
  if (pool.length < n) {  // bank exhausted: reuse the least recently used items
    pool = bank[fmt].filter(i => i.topic === topic)
      .sort((a, b) => String(bank.used[key(fmt, a)] || '').localeCompare(String(bank.used[key(fmt, b)] || '')));
  }
  const chosen = [];
  // clips can fail to download: plan spare candidates, the renderer keeps n of them (a spare replaces a
  // failed round of the same difficulty, so the easy-to-hard ramp holds)
  const spareLevels = SPARES[fmt] || [];
  if (fmt === 'wyr') {
    const shuffled = [...pool].sort(() => Math.random() - 0.5);
    chosen.push(...shuffled.slice(0, n));
  } else {
    const ramp = [...(RAMPS[n] || RAMPS[5]), ...spareLevels];
    for (const d of ramp) {
      // scene: never two seasons of one show in a quiz
      const any = pool.filter(i => !chosen.includes(i) && (fmt !== 'scene' || !chosen.some(c => sameSeries(i.title, c.title))));
      const c = any.filter(i => (i.difficulty || 2) === d);
      const src = c.length ? c : any;
      if (!src.length) break;
      chosen.push(src[Math.floor(Math.random() * src.length)]);
    }
  }
  const byName = ['city', 'character', 'idol', 'cityphoto', 'vtuber', 'voice'];
  const plan = {
    format: fmt, topic, want: spareLevels.length ? n : undefined,
    rounds: chosen.map(i => (byName.includes(fmt) ? { ...i, answer: i.name }
      : fmt === 'opening' || fmt === 'scene' ? { ...i, answer: i.title } : fmt === 'trivia' ? { ...i, answerText: i.options[i.answer] } : i)),
  };
  return { plan, keys: chosen.map(i => key(fmt, i)) };
}

/** Mark a published short's items as used. */
function markUsed(keys) {
  if (!keys || !keys.length) return;
  const bank = loadBank();
  const now = new Date().toISOString();
  for (const k of keys) bank.used[k] = now;
  saveBank(bank);
}

module.exports = { buildPopPlan, markUsed, loadBank, TOPICS, generate, key, franchise, duelRounds };
