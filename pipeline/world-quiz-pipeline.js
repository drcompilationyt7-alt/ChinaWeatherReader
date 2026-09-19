/**
 * World Quiz pipeline: original geography quiz shorts.
 *
 * Every short is rendered from open data (Wikidata CC0, Natural Earth, public
 * domain flags) by core/quiz/render_quiz.py, with a synthesised soundtrack,
 * so nothing is re-uploaded and there is nothing for Content ID to match.
 *
 *   1. pick a format (flag / shape / capital / bigger / crowd) by Thompson
 *      sampling, trained only on how our own quiz uploads performed
 *   2. pick a theme (world, or an Asia / Europe / Africa / Americas edition)
 *   3. render, avoiding answers used in recent quizzes
 *   4. metadata (core/metadata): the title style (keyword / speed / challenge / fans / casual /
 *      ramp / LLM-written, + styles the weekly step adds), hashtag set, emoji set and description
 *      hook are bandit arms learned across all formats; the template rotates inside the style
 *   5. description with the answers, hashtags, tags, translated titles
 *
 * Like the fastest-growing meme channels (one narrow format, repeated), a quiz
 * that clearly beats the channel gets a follow-up in the same format, theme
 * and title style ("double down on hits"), and a quick 3-round cut (~15 s)
 * is tested against the standard 5-round one (~25 s).
 *
 * memory/quiz-history.json keeps every quiz upload (never expires) so the
 * learners can join it with memory/performance-stats.json.
 */
const path = require('path');
const fs = require('fs');
const { execFile } = require('child_process');
const { Logger } = require('../core/logger');
const learn = require('../core/learning-models');
const { loadStats } = require('../core/performance-tracker');
const { buildPopPlan, markUsed, key: popKey } = require('./pop-quiz-content');
const { decay, appendDecision } = require('../core/experiment-log');
const pop = require('./pop-quiz-meta');
const metadata = require('../core/metadata');

const logger = new Logger('WorldQuiz');

const ROOT = path.join(__dirname, '..');
const RENDERER = path.join(ROOT, 'core', 'quiz', 'render_quiz.py');
const HISTORY_FILE = path.resolve(ROOT, process.env.MEMORY_DIR || 'memory', 'quiz-history.json');

const GEO_FORMATS = ['flag', 'shape', 'capital', 'bigger', 'crowd'];
const POP_FORMATS = ['emoji', 'trivia', 'wyr', 'city', 'character', 'idol', 'opening', 'cityphoto', 'vtuber', 'duel', 'scene', 'voice', 'song'];
// QUIZ_FORMATS picks the channel's formats (the quiz channel runs the pop set + Asia geography)
const FORMATS = (process.env.QUIZ_FORMATS || GEO_FORMATS.join(','))
  .split(',').map(f => f.trim()).filter(f => GEO_FORMATS.includes(f) || POP_FORMATS.includes(f));
const isPop = f => POP_FORMATS.includes(f);
// exploration weights; on the pop channel the geography formats are only a side dish. The new formats
// (song, voice, and emoji / wyr with their song and photo reveals) get a generous share while they have no data.
const FALLBACK_WEIGHTS = FORMATS.some(isPop)
  ? { emoji: 0.3, trivia: 0.15, wyr: 0.3, city: 0.08, character: 0.25, idol: 0.2, opening: 0.3, flag: 0.05, shape: 0.04,
    cityphoto: 0.15, vtuber: 0.18, duel: 0.22, scene: 0.28, voice: 0.3, song: 0.4 }
  : { flag: 0.3, shape: 0.2, capital: 0.15, bigger: 0.2, crowd: 0.15 };
const ROTATE_RECENT = 3;  // formats of the last 3 uploads are drawn less (and the last one never), so days rotate
const THEMES = [['world', 0.62], ['asia', 0.16], ['europe', 0.08], ['africa', 0.07], ['americas', 0.07]];
const THEMED_FORMATS = new Set(['flag', 'shape', 'capital']);

const PLAYLISTS = {
  flag: 'World Quiz: Guess the Flag',
  shape: 'World Quiz: Guess the Country by Shape',
  capital: 'World Quiz: Capital Cities',
  bigger: 'World Quiz: Which Country Is Bigger?',
  crowd: 'World Quiz: Which Country Has More People?',
};

const HASHTAGS = {
  flag: '#flagquiz #geography #quiz',
  shape: '#geography #countryquiz #maps',
  capital: '#capitals #geography #quiz',
  bigger: '#geography #maps #truesize',
  crowd: '#population #geography #quiz',
};

// what people search for each geography format (the metadata optimizer's title keyword)
const GEO_KEYWORD = {
  flag: { keyword: 'flag quiz', Keyword: 'Flag Quiz', any: ['flag'], emoji: '🚩' },
  shape: { keyword: 'guess the country', Keyword: 'Guess the Country', any: ['country', 'countries', 'shape'], emoji: '🗺️' },
  capital: { keyword: 'capital quiz', Keyword: 'Capital Cities Quiz', any: ['capital'], emoji: '🏛️' },
  bigger: { keyword: 'which country is bigger', Keyword: 'Which Country Is Bigger', any: ['bigger', 'size'], emoji: '🌍' },
  crowd: { keyword: 'population quiz', Keyword: 'Population Quiz', any: ['people', 'population'], emoji: '👥' },
};

const TAGS = {
  base: ['geography quiz', 'world quiz', 'geography', 'quiz', 'trivia', 'country quiz', 'general knowledge', 'guess the country'],
  flag: ['flag quiz', 'guess the flag', 'flags of the world', 'world flags', 'vexillology'],
  shape: ['country shape quiz', 'guess the country by shape', 'map quiz', 'maps', 'country outlines'],
  capital: ['capital cities quiz', 'capitals quiz', 'guess the capital', 'world capitals'],
  bigger: ['which country is bigger', 'true size of countries', 'map projection', 'country size comparison', 'mercator'],
  crowd: ['population quiz', 'country population', 'which country has more people', 'population comparison'],
};

const THEME_WORD = { asia: 'Asian', europe: 'European', africa: 'African', americas: 'Americas' };

// Title templates. `when` limits a template to plans it makes sense for.
const TITLE_TEMPLATES = {
  flag: [
    { id: 'flag-3sec', style: 'speed', text: () => 'Guess the Flag in 3 Seconds 🌍', when: p => p.theme === 'world' },
    { id: 'flag-all5', style: 'challenge', text: p => `Can You Name All ${p.rounds.length} Flags? 🚩` },
    { id: 'flag-ramp', style: 'ramp', text: () => 'Flag Quiz: Easy to IMPOSSIBLE 🔥', when: p => p.theme === 'world' },
    { id: 'flag-expert', style: 'fans', text: p => `Only Flag Experts Get ${p.rounds.length}/${p.rounds.length} 🌍` },
    { id: 'flag-casual', style: 'casual', text: () => 'this flag quiz gets hard fast 🔥✌️' },
    { id: 'flag-casual-last', style: 'casual', text: () => 'nobody gets the last flag 👀🔥', when: p => p.rounds.some(r => r.decoy) },
    { id: 'flag-timer', style: 'speed', text: () => 'Name These Flags Before Time Runs Out ⏱️' },
    { id: 'flag-theme', style: 'challenge', text: p => `${THEME_WORD[p.theme]} Flag Quiz: Can You Get ${p.rounds.length}/${p.rounds.length}? 🌏`, when: p => p.theme in THEME_WORD },
    { id: 'flag-theme-3sec', style: 'speed', text: p => p.theme === 'americas' ? 'Flags of the Americas in 3 Seconds 🌎' : `Guess the ${THEME_WORD[p.theme]} Flag in 3 Seconds 🌏`, when: p => p.theme in THEME_WORD },
    { id: 'flag-lookalike', style: 'fans', text: () => 'Most People Get the Last Flag Wrong 😅', when: p => p.rounds.some(r => r.decoy) },
  ],
  shape: [
    { id: 'shape-guess', style: 'keyword', text: () => 'Guess the Country by Its Shape 🗺️' },
    { id: 'shape-all5', style: 'challenge', text: p => `Can You Recognize These ${p.rounds.length} Countries? 🗺️` },
    { id: 'shape-casual', style: 'casual', text: () => 'guess the country by its shape 👀🗺️' },
    { id: 'shape-ramp', style: 'ramp', text: () => 'Country Shape Quiz: Easy to IMPOSSIBLE 🔥' },
    { id: 'shape-outline', style: 'keyword', text: () => 'Name the Country From Its Outline 🌍' },
    { id: 'shape-theme', style: 'challenge', text: p => `${THEME_WORD[p.theme]} Countries by Shape: How Many Do You Know? 🌏`, when: p => p.theme in THEME_WORD && p.theme !== 'americas' },
  ],
  capital: [
    { id: 'cap-3sec', style: 'speed', text: () => 'Name the Capital in 3 Seconds 🏛️' },
    { id: 'cap-trap', style: 'fans', text: p => `Most People Get #${p.rounds.findIndex(r => r.decoy) + 1} Wrong 😅 Capital Quiz`, when: p => p.rounds.some(r => r.decoy) },
    { id: 'cap-ramp', style: 'ramp', text: () => 'Capital City Quiz: Easy to IMPOSSIBLE 🔥' },
    { id: 'cap-know', style: 'challenge', text: () => 'Do You Know These Capitals? 🌍' },
    { id: 'cap-casual', style: 'casual', text: () => 'most people fail this capital quiz 🔥✌️' },
    { id: 'cap-theme', style: 'challenge', text: p => p.theme === 'americas' ? 'Capitals of the Americas Quiz 🌎' : `${THEME_WORD[p.theme]} Capitals Quiz: Can You Get ${p.rounds.length}/${p.rounds.length}? 🏛️`, when: p => p.theme in THEME_WORD },
  ],
  bigger: [
    { id: 'big-actually', style: 'keyword', text: () => 'Which Country Is Actually Bigger? 🤯' },
    { id: 'big-vs', style: 'keyword', text: p => `${p.rounds[0].left.name} vs ${p.rounds[0].right.name}: Which Is Bigger? 🌍`, when: p => (p.rounds[0].left.name + p.rounds[0].right.name).length <= 34 },
    { id: 'big-maplies', style: 'challenge', text: () => 'The Map Lies! Which Country Is Bigger? 🗺️' },
    { id: 'big-realsize', style: 'keyword', text: () => 'Real Size Quiz: Which Country Is Bigger? 📏' },
    { id: 'big-casual', style: 'casual', text: () => 'the map has been lying to you 🤯🗺️' },
  ],
  crowd: [
    { id: 'crowd-more', style: 'keyword', text: () => 'Which Country Has More People? 👥' },
    { id: 'crowd-vs', style: 'keyword', text: p => `${p.rounds[0].left.name} or ${p.rounds[0].right.name}: Who Has More People? 🤔`, when: p => (p.rounds[0].left.name + p.rounds[0].right.name).length <= 30 },
    { id: 'crowd-harder', style: 'challenge', text: () => 'Population Quiz: Harder Than It Looks 🤯' },
    { id: 'crowd-guess', style: 'keyword', text: () => 'Guess Which Country Has More People 🌍' },
    { id: 'crowd-casual', style: 'casual', text: () => 'i did not expect #1 🤯👥' },
  ],
  ...pop.POP_TITLE_TEMPLATES,
};

const LOCALES = ['es', 'pt', 'fr', 'de', 'it', 'hi', 'id', 'ar', 'ru', 'ja', 'ko', 'tr', 'vi', 'pl'];

// ─── history ─────────────────────────────────────────────────────

function loadHistory() {
  try { return JSON.parse(fs.readFileSync(HISTORY_FILE, 'utf8')); } catch { return { uploads: [] }; }
}

function saveHistory(h) {
  fs.mkdirSync(path.dirname(HISTORY_FILE), { recursive: true });
  fs.writeFileSync(HISTORY_FILE, JSON.stringify(h, null, 1));
}

/** Store a finished upload so later runs can learn from it. */
function recordUpload(result, upload) {
  const h = loadHistory();
  h.uploads.push({
    videoId: upload.videoId, url: upload.url, postedAt: new Date().toISOString(), publishAt: upload.publishAt || null,
    format: result.quiz.format, theme: result.quiz.theme, seed: result.quiz.seed, length: result.quiz.length,
    followUpOf: result.quiz.followUpOf || undefined,
    titleTemplate: result.quiz.titleTemplate, title: result.title, answers: result.quiz.iso2s,
    meta: result.quiz.meta || undefined,
  });
  saveHistory(h);
  if (result.quiz.keys) markUsed(result.quiz.keys);
  // the metadata arms (titleStyle, hashtagSet, emojiSet, descHook) go in the decision log for the learner
  appendDecision({ vid: upload.videoId, ch: 'quiz', title: result.title,
    arm: { format: result.quiz.format, topic: result.quiz.topic || result.quiz.theme, length: result.quiz.length, title: result.quiz.titleTemplate,
      ...(result.quiz.meta || {}) },
    mode: result.quiz.followUpOf ? 'followup' : (result.quiz.why || 'pick') });
  if (result.quiz.metaChoice) metadata.recordMetadataChoice(result.quiz.metaChoice, { videoId: upload.videoId });
}

function recentIso(history, format, days) {
  const cutoff = Date.now() - days * 86400000;
  const out = new Set();
  for (const u of history.uploads) {
    if (new Date(u.postedAt).getTime() < cutoff) continue;
    if (format && u.format !== format) continue;
    for (const iso of u.answers || []) out.add(iso);
  }
  return out;
}

// ─── learning ────────────────────────────────────────────────────

/** Quiz uploads joined with their latest stats (lscore = log views/day, engagement-weighted). */
function scoredQuizUploads(stats, history) {
  const byId = new Map(((stats && stats.videos) || []).map(v => [v.videoId, v]));
  const out = history.uploads
    .map(u => ({ ...u, stat: byId.get(u.videoId) }))
    .filter(u => u.stat && typeof u.stat.lscore === 'number' && (u.stat.ageDays || 0) >= 1.5);
  // learn from the fixed-checkpoint reward (core/experiment-log.js) once every upload has one;
  // w: older results count less (10% per week), the channel keeps changing
  const useReward = out.length && out.every(u => typeof u.stat.reward === 'number');
  return out.map(u => ({ ...u, learn: useReward ? u.stat.reward : u.stat.lscore, w: decay(u.stat.ageDays) }));
}

/** Weighted Thompson draw: (sum w*x + prior) / (sum w + 1) plus noise shrinking with the evidence. */
function weightedDraw(obs, prior, noise) {
  const n = obs.reduce((a, u) => a + u.w, 0);
  const mean = (obs.reduce((a, u) => a + u.w * u.x, 0) + prior) / (n + 1);
  return { n, draw: mean + gaussian() * noise / Math.sqrt(n + 1) };
}

function percentileRewards(items) {
  const sorted = [...items].sort((a, b) => a.learn - b.learn);
  const rank = new Map(sorted.map((u, i) => [u.videoId, sorted.length > 1 ? i / (sorted.length - 1) : 0.5]));
  return items.map(u => ({ ...u, reward: rank.get(u.videoId) }));
}

function pickWeighted(pairs) {
  const tot = pairs.reduce((a, [, w]) => a + w, 0);
  let x = Math.random() * tot;
  for (const [k, w] of pairs) { x -= w; if (x <= 0) return k; }
  return pairs[pairs.length - 1][0];
}

/**
 * Gaussian Thompson sampling over formats on percentile rewards (how each quiz
 * ranked among our quizzes). 15% of picks explore. Consecutive days rotate: the
 * last upload's format is never picked again right away, and the formats of the
 * last ROTATE_RECENT uploads are drawn less (exploration weight x0.3, Thompson -0.15).
 */
function pickFormat(stats, history, forced) {
  if (forced && FORMATS.includes(forced)) return { format: forced, why: 'forced' };
  const recent = history.uploads.slice(-ROTATE_RECENT).map(u => u.format);
  const last = recent[recent.length - 1];
  const allowed = FORMATS.length > 1 ? FORMATS.filter(f => f !== last) : FORMATS;
  const scored = percentileRewards(scoredQuizUploads(stats, history));
  if (Math.random() < 0.15 || scored.length < 6) {
    const f = pickWeighted(allowed.map(f => [f, (FALLBACK_WEIGHTS[f] || 0.1) * (recent.includes(f) ? 0.3 : 1)]));
    return { format: f, why: scored.length < 6 ? `exploring (${scored.length} scored quizzes so far)` : 'exploration draw' };
  }
  let best = null;
  const draws = [];
  for (const f of allowed) {
    const { n, draw: d } = weightedDraw(scored.filter(u => u.format === f).map(u => ({ x: u.reward, w: u.w })), 0.5, 0.25);
    const draw = d - (recent.includes(f) ? 0.15 : 0);
    draws.push(`${f}=${draw.toFixed(2)}(${n.toFixed(1)})`);
    if (!best || draw > best.draw) best = { f, draw };
  }
  return { format: best.f, why: `thompson ${draws.join(' ')}` };
}

function pickTheme(format, forced) {
  if (forced) return forced;
  return THEMED_FORMATS.has(format) ? pickWeighted(THEMES) : 'world';
}

const LENGTHS = { quick: 3, standard: null };  // null = the format's default (5, or 4 for pairs)

/** Thompson sampling between the quick (3-round) and standard cut. */
function pickLength(stats, history) {
  const scored = scoredQuizUploads(stats, history);
  const prior = scored.length ? scored.reduce((a, u) => a + u.learn, 0) / scored.length : 0;
  let best = null;
  for (const len of Object.keys(LENGTHS)) {
    const { draw } = weightedDraw(scored.filter(u => (u.length || 'standard') === len).map(u => ({ x: u.learn, w: u.w })), prior, 0.6);
    if (!best || draw > best.draw) best = { len, draw };
  }
  return best.len;
}

/**
 * A recent quiz that clearly beat the channel (>= 3x the typical quiz's
 * views/day and at least 300 views): repeat its format, theme, length and
 * title style, the way meme channels keep remaking the one that popped.
 */
function findHit(stats, history) {
  const scored = scoredQuizUploads(stats, history);
  if (scored.length < 4) return null;
  const ls = scored.map(u => u.stat.lscore).sort((a, b) => a - b);
  const median = ls[Math.floor(ls.length / 2)];
  const cutoff = Date.now() - 12 * 86400000;
  const hits = scored.filter(u => new Date(u.postedAt).getTime() >= cutoff && u.stat.views >= 300
    && Math.expm1(u.stat.lscore) >= 3 * Math.max(0.5, Math.expm1(median)));
  if (!hits.length) return null;
  const hit = hits.sort((a, b) => b.stat.lscore - a.stat.lscore)[0];
  const followUps = history.uploads.filter(u => u.followUpOf === hit.videoId).length;
  return followUps < 3 ? hit : null;
}

// Gaussian Thompson sampling over title templates of one format.
function gaussian() {
  let u = 0, v = 0;
  while (!u) u = Math.random();
  while (!v) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

/** Does the video have a hard round? (no difficulty data: the geography formats, assume yes) */
function hasHardRound(plan) {
  const levels = (plan.rounds || []).map(r => Number(r.difficulty)).filter(Number.isFinite);
  return !(levels.length && Math.max(...levels) < 3);
}

/** The title templates that fit this plan: "Easy to IMPOSSIBLE" only when a hard round made it into the video. */
function titleOptions(plan) {
  const noHard = !hasHardRound(plan);
  return TITLE_TEMPLATES[plan.format].filter(t => (!t.when || t.when(plan)) && !(noHard && /-ramp$/.test(t.id)));
}

/** What the metadata optimizer needs to know about a quiz (no answers in the title, keyword, hook, ...). */
function quizFacts(plan) {
  const format = plan.format;
  const k = isPop(format) ? pop.popKeyword(plan) : GEO_KEYWORD[format];
  const answers = plan.rounds.map(r => (typeof r.answer === 'string' ? r.answer.split(' (')[0] : '')).filter(a => a.length > 3);
  const topicLabel = isPop(format) ? (pop.TOPIC_LABEL[plan.topic] || 'Asian Pop') : (THEME_WORD[plan.theme] || 'World');
  return {
    format, topic: plan.topic || plan.theme, topicLabel, keyword: k.keyword, Keyword: k.Keyword, any: k.any, topicEmoji: k.emoji,
    n: plan.rounds.length, hard: hasHardRound(plan),
    forbidden: plan.rounds[0] && plan.rounds[0].left ? [] : answers,  // "X vs Y" titles may name both sides
    hook: isPop(format) ? pop.popHook(plan) : hookLine(plan),
    cta: isPop(format) ? pop.popCta(plan) : 'Comment your score 👇',
    formatTags: isPop(format) ? pop.popHashtags(plan) : HASHTAGS[format],
    roundsText: plan.rounds.map(r => (r.left ? `${r.left.name} vs ${r.right.name}` : r.question || (r.a ? `${r.a.name || r.a} or ${r.b.name || r.b}` : 'hidden answer')))
      .join('; ').slice(0, 400),
  };
}

/**
 * The title template. The style (keyword / speed / challenge / fans / casual / ramp / llm, + styles
 * the weekly step adds) is a bandit arm learned across ALL formats by core/metadata (one upload a day
 * is too little to learn per format and template); inside the style the template rotates, never one
 * of the format's last two. Falls back to a random fresh template.
 */
function pickTitleTemplate(plan, stats, history, llmAvailable) {
  const options = titleOptions(plan);
  const recentIds = history.uploads.filter(u => u.format === plan.format).slice(-2).map(u => u.titleTemplate);
  try {
    const t = metadata.pickQuizTitle({ plan, options, llmAvailable, recentIds, facts: quizFacts(plan) });
    if (t) return t;
  } catch (e) {
    logger.warn(`Title style pick failed (${(e.message || '').slice(0, 80)}), using a random template`);
  }
  const fresh = options.filter(t => !recentIds.includes(t.id));
  const list = fresh.length ? fresh : options;
  const t = list[Math.floor(Math.random() * list.length)];
  return { ...t, style: t.style || metadata.quizStyleOf(t.id) };
}

/**
 * Title, hashtags, description head, tags and comment from core/metadata. tpl: the template
 * pickTitleTemplate chose (or a hit's); hitMeta: the hit's arms for a follow-up.
 */
async function quizMetadata(plan, tpl, { hitMeta = null, baseTags = [], comment = null } = {}) {
  const facts = quizFacts(plan);
  const options = titleOptions(plan);
  const templates = options.map(t => ({ id: t.id, style: t.style || metadata.quizStyleOf(t.id), text: t.text(plan) }));
  if (!tpl.llm && !templates.some(t => t.id === tpl.id) && typeof tpl.text === 'function') templates.push({ id: tpl.id, style: tpl.style, text: tpl.text(plan) });
  const meta = await metadata.optimizeMetadata({
    channel: 'quiz', contentType: plan.format,
    facts: { ...facts, templates, templateId: tpl.llm ? null : tpl.id, baseTags, comment,
      recentTemplateIds: loadHistory().uploads.filter(u => u.format === plan.format).slice(-2).map(u => u.titleTemplate) },
    style: { ...(hitMeta || {}), titleStyle: tpl.style || metadata.quizStyleOf(tpl.id) },
    llm: process.env.QUIZ_LLM !== 'off',
  });
  if (tpl.mode && meta.modes) meta.modes.titleStyle = tpl.mode;  // how pickTitleTemplate chose the style (thompson / explore)
  return meta;
}

// Once a Gemini call fails or times out (quota, outage), skip it for the rest
// of the run: its own retry loop can otherwise sleep minutes per call.
let geminiDown = false;

async function withTimeout(promise, ms) {
  let timer;
  const r = await Promise.race([promise.catch(() => null), new Promise(res => { timer = setTimeout(() => res(null), ms); })])
    .finally(() => clearTimeout(timer));
  if (r == null) geminiDown = true;
  return r;
}

function getGemini() {
  if (geminiDown || process.env.QUIZ_LLM === 'off') return null;
  try {
    const { getGeminiService } = require('../core/gemini-service');
    const g = getGeminiService();
    return g && g.getStats().keysLoaded > 0 ? g : null;
  } catch { return null; }
}

// (LLM-written titles: core/metadata writes them with its Shorts-packaging brief, validated: no answers,
// the format's keyword, no invented statistics)

async function translateMeta(gemini, title, hook) {
  const sys = 'Translate YouTube Shorts metadata naturally for native speakers. Keep emojis, keep it short and punchy.';
  const msg = `Title: ${title}\nLine: ${hook}\nLanguages: ${LOCALES.join(', ')}\n`
    + 'Return JSON: {"<lang>": {"title": "...", "line": "..."}, ...} with every language code as a key.';
  const r = await withTimeout(gemini.chatJSON(sys, msg), 90000);
  if (!r || typeof r !== 'object') return null;
  const out = {};
  for (const lang of LOCALES) {
    const v = r[lang];
    if (v && typeof v.title === 'string' && v.title.trim() && v.title.length <= 100) out[lang] = { title: v.title.trim(), line: String(v.line || '').trim() };
  }
  return Object.keys(out).length ? out : null;
}

// ─── metadata ────────────────────────────────────────────────────

function hookLine(plan) {
  const n = plan.rounds.length;
  switch (plan.format) {
    case 'flag': return `How many of these ${n} flags can you name before the timer runs out?`;
    case 'shape': return `Can you name all ${n} countries just from their shape?`;
    case 'capital': return `Name all ${n} capitals in 3 seconds each.`;
    case 'bigger': return 'Flat maps make some countries look huge. Which one is really bigger?';
    default: return 'Some of these population match-ups are harder than they look.';
  }
}

function answersBlock(plan) {
  return plan.rounds.map((r, i) => {
    if (r.left) {
      const detail = plan.format === 'bigger' ? `${Math.round(r.ratio * 10) / 10}x bigger` : `${Math.round(r.ratio * 10) / 10}x more people`;
      return `${i + 1}. ${r.left.name} vs ${r.right.name}: ${r.answer} (${detail})`;
    }
    return `${i + 1}. ${plan.format === 'capital' ? `${r.name}: ${r.answer}` : r.answer}`;
  }).join('\n');
}

/** opts.head / opts.hashtags: the metadata optimizer's first line and hashtag set (else the defaults). */
function buildDescription(plan, hook, opts = {}) {
  return `${opts.head || `${hook} Comment your score 👇`}\n\n`
    + '🌍 A new world quiz every day: flags, country shapes, capitals and the real size of countries.\n\n'
    + `Answers (no peeking!):\n${answersBlock(plan)}\n\n`
    + 'Data: Wikidata (CC0), Natural Earth, flagcdn.com\n'
    + `${opts.hashtags || HASHTAGS[plan.format]}`;
}

function buildTags(plan) {
  const names = plan.rounds.flatMap(r => (r.left ? [r.left.name, r.right.name] : [r.name]));
  const tags = [...TAGS[plan.format], ...TAGS.base, ...names];
  const out = [];
  let len = 0;
  for (const t of tags) {
    if (out.includes(t) || len + t.length + 3 > 480) continue;
    out.push(t);
    len += t.length + 3;
  }
  return out;
}

// ─── render ──────────────────────────────────────────────────────

function renderQuiz({ format, theme, seed, avoid, outPath, rounds, planFile }) {
  const py = learn.pythonCommand();
  if (!py) return Promise.reject(new Error('python not found'));
  const args = [RENDERER, '--format', format, '--theme', theme, '--seed', String(seed), '--out', outPath];
  if (planFile) args.push('--plan-file', planFile);
  if (avoid && avoid.length) args.push('--avoid', avoid.join(','));
  if (rounds) args.push('--rounds', String(rounds));
  return new Promise((resolve, reject) => {
    execFile(py, args, { timeout: 15 * 60000, maxBuffer: 16 * 1024 * 1024, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }, (err, stdout, stderr) => {
      const line = String(stdout || '').trim().split('\n').filter(Boolean).pop();
      let res = null;
      try { res = line ? JSON.parse(line) : null; } catch {}
      if (err || !res || !res.ok) return reject(new Error((res && res.reason) || (stderr || err?.message || 'render failed').toString().slice(-300)));
      // rounds whose clip or photo could not be fetched (a spare took their place)
      for (const l of String(stderr || '').split(/\r?\n/).filter(x => /skipped:|audio check:/.test(x)).slice(0, 12)) logger.warn(l.slice(0, 200));
      resolve(res);
    });
  });
}

/**
 * @param {{outputDir: string, format?: string, theme?: string}} opts
 * @returns {Promise<Object>} upload-ready result
 */
async function runWorldQuizPipeline(opts = {}) {
  const outputDir = opts.outputDir || path.join(ROOT, 'output', 'quiz');
  fs.mkdirSync(outputDir, { recursive: true });
  const stats = loadStats();
  const history = loadHistory();

  const forcedFormat = opts.format || process.env.QUIZ_FORMAT;
  const lastFormat = (history.uploads[history.uploads.length - 1] || {}).format;
  let hit = !forcedFormat && Math.random() < 0.5 ? findHit(stats, history) : null;
  if (hit && hit.format === lastFormat && FORMATS.length > 1) hit = null;  // a follow-up waits a day: consecutive days rotate
  let format, why, theme, length;
  if (hit && FORMATS.includes(hit.format)) {
    ({ format, theme } = hit);
    length = hit.length || 'standard';
    why = `follow-up to hit "${hit.title}" (${hit.stat.views} views)`;
  } else {
    ({ format, why } = pickFormat(stats, history, forcedFormat));
    theme = pickTheme(format, opts.theme || process.env.QUIZ_THEME);
    length = opts.length || process.env.QUIZ_LENGTH || pickLength(stats, history);
  }
  logger.info(`Format: ${format} (${why}), theme: ${theme}, length: ${length}`);

  // never repeat an answer of the same format within 30 days, or any answer within 4 days
  const avoid = [...new Set([...recentIso(history, format, 30), ...recentIso(history, null, 4)])];
  const seed = Math.floor(Math.random() * 1e9);
  const outPath = path.join(outputDir, `quiz-${format}-${Date.now()}.mp4`);
  const t0 = Date.now();
  let planFile = null;
  let popKeys = null;
  if (isPop(format)) {
    const built = await buildPopPlan(format, { topic: hit ? hit.theme : (opts.topic || null), rounds: LENGTHS[length] || null });
    theme = built.plan.topic;
    popKeys = built.keys;
    planFile = path.join(outputDir, `plan-${Date.now()}.json`);
    fs.writeFileSync(planFile, JSON.stringify({ ...built.plan, seed }, null, 1));
    logger.info(`Topic: ${theme}, ${built.plan.rounds.length} rounds`);
  }
  const res = await renderQuiz({ format, theme, seed, avoid, outPath, rounds: LENGTHS[length], planFile });
  if (planFile) { try { fs.unlinkSync(planFile); } catch {} }
  const plan = { ...res.plan, theme };
  // the renderer may drop candidates (openings that fail to download): only mark what was shown
  if (isPop(format)) popKeys = plan.rounds.map(r => popKey(format, r));
  const label = r => (typeof r.answer === 'string' ? r.answer : r.question ? r.options[r.answer] : r.a ? `${r.a} / ${r.b}` : r.name || r.title || '');
  const loud = res.lufs != null ? `, ${res.lufs} LUFS / ${res.truePeak} dBTP` : '';
  logger.success(`Rendered ${res.duration}s in ${Math.round((Date.now() - t0) / 1000)}s (voice ${res.voice}/${res.voiceLines}${loud}): ${plan.rounds.map(label).join(', ')}`);
  if (res.voice === 0) logger.warn('No voice lines were generated (edge-tts unreachable) — the short uses music and SFX only');

  const gemini = getGemini();
  // a follow-up to a hit keeps the hit's title template (or its LLM-written style) and its other metadata arms
  const hitTpl = hit && (TITLE_TEMPLATES[format].find(t => t.id === hit.titleTemplate && (!t.when || t.when(plan)))
    || (hit.meta && hit.meta.titleStyle === 'llm' && process.env.QUIZ_LLM !== 'off' ? { id: `${format}-llm`, llm: true, style: 'llm' } : null));
  const tpl = hitTpl || pickTitleTemplate(plan, stats, history, process.env.QUIZ_LLM !== 'off');
  const comment = isPop(format) ? pop.popComment(plan) : format === 'bigger' || format === 'crowd'
    ? 'Which one surprised you the most? 🤯 Tell me your score 👇'
    : `What did you score out of ${plan.rounds.length}? 🏆 Which one got you? 👇`;
  const baseTags = isPop(format) ? pop.popTags(plan) : buildTags(plan);
  // title style, hashtag set, emoji set and description hook: core/metadata (learned across formats)
  let meta = null;
  try {
    meta = await quizMetadata(plan, tpl, { hitMeta: hit && hit.meta, baseTags, comment });
  } catch (e) {
    logger.warn(`Metadata optimizer failed (${(e.message || '').slice(0, 100)}), using the template as is`);
  }
  let title;
  let titleTemplate;
  if (meta) {
    title = meta.title;
    titleTemplate = meta.titleSource === 'llm' ? `${format}-llm` : (meta.templateId || tpl.id);
  } else {
    const t = tpl.llm || typeof tpl.text !== 'function' ? titleOptions(plan)[0] : tpl;
    title = t.text(plan);
    titleTemplate = t.id;
  }
  title = pop.clean(title).substring(0, 100);  // YouTube rejects < and > in titles and descriptions
  logger.info(`Title [${titleTemplate}${meta ? `; ${Object.entries(meta.arms).filter(([k]) => k !== 'contentType')
    .map(([k, v]) => `${k} ${v}${meta.modes[k] ? `/${meta.modes[k]}` : ''}`).join(', ')}; llm ${meta.llm}` : ''}]: ${title}`);

  const hook = isPop(format) ? pop.popHook(plan) : hookLine(plan);
  const hashtags = (meta && meta.hashtagLine) || (isPop(format) ? pop.popHashtags(plan) : HASHTAGS[format]);
  const head = meta ? meta.descriptionHead : null;
  const description = pop.clean(isPop(format) ? pop.popDescription(plan, { head, hashtags }) : buildDescription(plan, hook, { head, hashtags }));
  if (res.audioCheck) {
    logger.info(`Audio: narrator over clip ${res.audioCheck.overlapSec}s, all at ${res.audioCheck.clipGainUnderVoiceDb ?? '-'} dB; `
      + `unducked overlap ${res.audioCheck.overlapUnduckedSec}s; fetch ${JSON.stringify(res.fetch || {})}`);
  }
  const answersText = isPop(format) ? pop.popAnswers(plan) : answersBlock(plan);
  let localizations = null;
  if (gemini && !geminiDown && process.env.QUIZ_TRANSLATE !== 'off') {
    const tr = await translateMeta(gemini, title, hook).catch(() => null);
    if (tr) {
      localizations = {};
      for (const [lang, v] of Object.entries(tr)) {
        localizations[lang] = { title: v.title, description: `${v.line || hook} 👇\n\n${answersText}\n\n${hashtags}` };
      }
      logger.info(`Localized titles: ${Object.keys(localizations).join(', ')}`);
    }
  }

  return {
    success: true,
    videoPath: res.path,
    title,
    description,
    tags: (meta && meta.tags) || baseTags,
    categoryId: isPop(format) ? '24' : '27',
    localizations,
    playlistTitle: isPop(format) ? pop.popPlaylist(plan) : PLAYLISTS[format],
    comment: (meta && meta.comment) || comment,
    country: 'Global',
    // what the renderer measured: loudness, fetch results, and every narrator / clip placement with the overlap check
    renderInfo: { duration: res.duration, renderSec: res.renderSec, totalSec: Math.round((Date.now() - t0) / 1000), lufs: res.lufs,
      truePeak: res.truePeak, fetch: res.fetch, audioCheck: res.audioCheck, placements: res.placements },
    category: `quiz-${format}`,
    editType: 'quiz',
    geminiScore: null,
    sourceChannel: null,
    quiz: {
      format, theme, seed: plan.seed, titleTemplate, length, followUpOf: hit ? hit.videoId : null, why: String(why || '').slice(0, 80),
      topic: plan.topic || null,
      iso2s: popKeys || plan.rounds.flatMap(r => (r.left ? [r.left.iso2, r.right.iso2] : [r.iso2])),
      keys: popKeys,
      answers: plan.rounds.map(label),
      duration: res.duration,
      meta: meta ? meta.arms : undefined, metaChoice: meta ? meta.choice : undefined,
    },
  };
}

module.exports = { runWorldQuizPipeline, recordUpload, loadHistory, pickFormat, pickLength, findHit, pickTitleTemplate, titleOptions, quizFacts,
  quizMetadata, buildDescription, buildTags, TITLE_TEMPLATES, PLAYLISTS };
