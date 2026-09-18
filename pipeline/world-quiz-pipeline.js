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
 *   4. title: Thompson sampling over title templates (+ an LLM-written arm)
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
const pop = require('./pop-quiz-meta');

const logger = new Logger('WorldQuiz');

const ROOT = path.join(__dirname, '..');
const RENDERER = path.join(ROOT, 'core', 'quiz', 'render_quiz.py');
const HISTORY_FILE = path.resolve(ROOT, process.env.MEMORY_DIR || 'memory', 'quiz-history.json');

const GEO_FORMATS = ['flag', 'shape', 'capital', 'bigger', 'crowd'];
const POP_FORMATS = ['emoji', 'trivia', 'wyr', 'city', 'character', 'idol', 'opening'];
// QUIZ_FORMATS picks the channel's formats (the quiz channel runs the pop set + Asia geography)
const FORMATS = (process.env.QUIZ_FORMATS || GEO_FORMATS.join(','))
  .split(',').map(f => f.trim()).filter(f => GEO_FORMATS.includes(f) || POP_FORMATS.includes(f));
const isPop = f => POP_FORMATS.includes(f);
// exploration weights; on the pop channel the geography formats are only a side dish
const FALLBACK_WEIGHTS = FORMATS.some(isPop)
  ? { emoji: 0.25, trivia: 0.2, wyr: 0.2, city: 0.1, character: 0.3, idol: 0.2, opening: 0.3, flag: 0.06, shape: 0.05 }
  : { flag: 0.3, shape: 0.2, capital: 0.15, bigger: 0.2, crowd: 0.15 };
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
    { id: 'flag-3sec', text: () => 'Guess the Flag in 3 Seconds 🌍', when: p => p.theme === 'world' },
    { id: 'flag-all5', text: p => `Can You Name All ${p.rounds.length} Flags? 🚩` },
    { id: 'flag-ramp', text: () => 'Flag Quiz: Easy to IMPOSSIBLE 🔥', when: p => p.theme === 'world' },
    { id: 'flag-expert', text: p => `Only Flag Experts Get ${p.rounds.length}/${p.rounds.length} 🌍` },
    { id: 'flag-casual', text: () => 'this flag quiz gets hard fast 😭✌️' },
    { id: 'flag-casual-last', text: () => 'nobody gets the last flag 😭😭', when: p => p.rounds.some(r => r.decoy) },
    { id: 'flag-timer', text: () => 'Name These Flags Before Time Runs Out ⏱️' },
    { id: 'flag-theme', text: p => `${THEME_WORD[p.theme]} Flag Quiz: Can You Get ${p.rounds.length}/${p.rounds.length}? 🌏`, when: p => p.theme in THEME_WORD },
    { id: 'flag-theme-3sec', text: p => p.theme === 'americas' ? 'Flags of the Americas in 3 Seconds 🌎' : `Guess the ${THEME_WORD[p.theme]} Flag in 3 Seconds 🌏`, when: p => p.theme in THEME_WORD },
    { id: 'flag-lookalike', text: () => 'Most People Get the Last Flag Wrong 😅', when: p => p.rounds.some(r => r.decoy) },
  ],
  shape: [
    { id: 'shape-guess', text: () => 'Guess the Country by Its Shape 🗺️' },
    { id: 'shape-all5', text: p => `Can You Recognize These ${p.rounds.length} Countries? 🗺️` },
    { id: 'shape-casual', text: () => 'guess the country by its shape 😭🗺️' },
    { id: 'shape-ramp', text: () => 'Country Shape Quiz: Easy to IMPOSSIBLE 🔥' },
    { id: 'shape-outline', text: () => 'Name the Country From Its Outline 🌍' },
    { id: 'shape-theme', text: p => `${THEME_WORD[p.theme]} Countries by Shape: How Many Do You Know? 🌏`, when: p => p.theme in THEME_WORD && p.theme !== 'americas' },
  ],
  capital: [
    { id: 'cap-3sec', text: () => 'Name the Capital in 3 Seconds 🏛️' },
    { id: 'cap-trap', text: p => `Most People Get #${p.rounds.findIndex(r => r.decoy) + 1} Wrong 😅 Capital Quiz`, when: p => p.rounds.some(r => r.decoy) },
    { id: 'cap-ramp', text: () => 'Capital City Quiz: Easy to IMPOSSIBLE 🔥' },
    { id: 'cap-know', text: () => 'Do You Know These Capitals? 🌍' },
    { id: 'cap-casual', text: () => 'most people fail this capital quiz 😭✌️' },
    { id: 'cap-theme', text: p => p.theme === 'americas' ? 'Capitals of the Americas Quiz 🌎' : `${THEME_WORD[p.theme]} Capitals Quiz: Can You Get ${p.rounds.length}/${p.rounds.length}? 🏛️`, when: p => p.theme in THEME_WORD },
  ],
  bigger: [
    { id: 'big-actually', text: () => 'Which Country Is Actually Bigger? 🤯' },
    { id: 'big-vs', text: p => `${p.rounds[0].left.name} vs ${p.rounds[0].right.name}: Which Is Bigger? 🌍`, when: p => (p.rounds[0].left.name + p.rounds[0].right.name).length <= 34 },
    { id: 'big-maplies', text: () => 'The Map Lies! Which Country Is Bigger? 🗺️' },
    { id: 'big-realsize', text: () => 'Real Size Quiz: Which Country Is Bigger? 📏' },
    { id: 'big-casual', text: () => 'the map has been lying to you 😭🗺️' },
  ],
  crowd: [
    { id: 'crowd-more', text: () => 'Which Country Has More People? 👥' },
    { id: 'crowd-vs', text: p => `${p.rounds[0].left.name} or ${p.rounds[0].right.name}: Who Has More People? 🤔`, when: p => (p.rounds[0].left.name + p.rounds[0].right.name).length <= 30 },
    { id: 'crowd-harder', text: () => 'Population Quiz: Harder Than It Looks 🤯' },
    { id: 'crowd-guess', text: () => 'Guess Which Country Has More People 🌍' },
    { id: 'crowd-casual', text: () => 'i did not expect #1 😭👥' },
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
  });
  saveHistory(h);
  if (result.quiz.keys) markUsed(result.quiz.keys);
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
  return history.uploads
    .map(u => ({ ...u, stat: byId.get(u.videoId) }))
    .filter(u => u.stat && typeof u.stat.lscore === 'number' && (u.stat.ageDays || 0) >= 1.5);
}

function percentileRewards(items) {
  const sorted = [...items].sort((a, b) => a.stat.lscore - b.stat.lscore);
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
 * ranked among our quizzes). 15% of picks explore; a format may repeat, but
 * never three times in a row.
 */
function pickFormat(stats, history, forced) {
  if (forced && FORMATS.includes(forced)) return { format: forced, why: 'forced' };
  const lastTwo = history.uploads.slice(-2).map(u => u.format);
  const allowed = FORMATS.filter(f => !(lastTwo.length === 2 && lastTwo[0] === f && lastTwo[1] === f));
  const scored = percentileRewards(scoredQuizUploads(stats, history));
  if (Math.random() < 0.15 || scored.length < 6) {
    const f = pickWeighted(allowed.map(f => [f, FALLBACK_WEIGHTS[f]]));
    return { format: f, why: scored.length < 6 ? `exploring (${scored.length} scored quizzes so far)` : 'exploration draw' };
  }
  let best = null;
  const draws = [];
  for (const f of allowed) {
    const mine = scored.filter(u => u.format === f).map(u => u.reward);
    const n = mine.length;
    const draw = (mine.reduce((a, b) => a + b, 0) + 0.5) / (n + 1) + gaussian() * 0.25 / Math.sqrt(n + 1);
    draws.push(`${f}=${draw.toFixed(2)}(${n})`);
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
  const prior = scored.length ? scored.reduce((a, u) => a + u.stat.lscore, 0) / scored.length : 0;
  let best = null;
  for (const len of Object.keys(LENGTHS)) {
    const mine = scored.filter(u => (u.length || 'standard') === len).map(u => u.stat.lscore);
    const n = mine.length;
    const mean = n ? (mine.reduce((a, b) => a + b, 0) + prior) / (n + 1) : prior;
    const draw = mean + gaussian() * 0.6 / Math.sqrt(n + 1);
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

function pickTitleTemplate(plan, stats, history, llmAvailable) {
  const options = TITLE_TEMPLATES[plan.format].filter(t => !t.when || t.when(plan));
  if (llmAvailable) options.push({ id: `${plan.format}-llm`, llm: true });
  const scored = scoredQuizUploads(stats, history).filter(u => u.format === plan.format);
  const prior = scored.length ? scored.reduce((a, u) => a + u.stat.lscore, 0) / scored.length : 0;
  const recent = history.uploads.filter(u => u.format === plan.format).slice(-2).map(u => u.titleTemplate);
  let best = null;
  for (const t of options) {
    const mine = scored.filter(u => u.titleTemplate === t.id).map(u => u.stat.lscore);
    const n = mine.length;
    const mean = n ? (mine.reduce((a, b) => a + b, 0) + prior) / (n + 1) : prior;
    let draw = mean + gaussian() * 0.6 / Math.sqrt(n + 1);
    if (recent.includes(t.id)) draw -= 0.35;  // variety between consecutive uploads
    if (!best || draw > best.draw) best = { t, draw };
  }
  return best.t;
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

async function llmTitle(gemini, plan, stats, history) {
  const top = scoredQuizUploads(stats, history).sort((a, b) => b.stat.lscore - a.stat.lscore).slice(0, 5).map(u => `"${u.title}" (${u.stat.views} views)`);
  const sys = 'You write titles for a geography quiz YouTube Shorts channel. Titles are short (max 60 characters), '
    + 'a question or a challenge, honest (no false claims, no invented statistics), never reveal answers, and use at most one emoji.';
  const rounds = plan.rounds.map((r, i) => `${i + 1}. ${r.left ? `${r.left.name} vs ${r.right.name}` : r.question || (r.a ? `${r.a} OR ${r.b}` : r.levelLabel || 'hidden answer')}`).join('\n');
  const msg = `Quiz format: ${plan.format} (${plan.topic ? `topic: ${plan.topic}` : `theme: ${plan.theme}`})\nRounds:\n${rounds}\n`
    + (top.length ? `Our best performing quiz titles so far:\n${top.join('\n')}\n` : '')
    + 'Return JSON: {"title": "..."}';
  const r = await withTimeout(gemini.chatJSON(sys, msg), 60000);
  const t = r && typeof r.title === 'string' ? r.title.trim().replace(/^["']|["']$/g, '') : '';
  const answers = plan.rounds.map(r => (typeof r.answer === 'string' ? r.answer.split(' (')[0] : '').toLowerCase()).filter(Boolean);
  if (!t || t.length > 80 || answers.some(a => a.length > 3 && t.toLowerCase().includes(a) && !plan.rounds[0].left)) return null;
  return t;
}

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

function buildDescription(plan, hook) {
  return `${hook} Comment your score 👇\n\n`
    + '🌍 A new world quiz every day: flags, country shapes, capitals and the real size of countries.\n\n'
    + `Answers (no peeking!):\n${answersBlock(plan)}\n\n`
    + 'Data: Wikidata (CC0), Natural Earth, flagcdn.com\n'
    + `${HASHTAGS[plan.format]}`;
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
  const hit = !forcedFormat && Math.random() < 0.5 ? findHit(stats, history) : null;
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
  logger.success(`Rendered ${res.duration}s in ${Math.round((Date.now() - t0) / 1000)}s (voice ${res.voice}/${res.voiceLines}): ${plan.rounds.map(label).join(', ')}`);
  if (res.voice === 0) logger.warn('No voice lines were generated (edge-tts unreachable) — the short uses music and SFX only');

  const gemini = getGemini();
  const hitTpl = hit && TITLE_TEMPLATES[format].find(t => t.id === hit.titleTemplate && (!t.when || t.when(plan)));
  const tpl = hitTpl || pickTitleTemplate(plan, stats, history, !!gemini);
  let title = null;
  let titleTemplate = tpl.id;
  if (tpl.llm) {
    title = await llmTitle(gemini, plan, stats, history).catch(() => null);
    if (!title) {
      const fallback = TITLE_TEMPLATES[format].filter(t => !t.when || t.when(plan));
      const f = fallback[Math.floor(Math.random() * fallback.length)];
      title = f.text(plan);
      titleTemplate = f.id;
    }
  } else {
    title = tpl.text(plan);
  }
  title = title.substring(0, 100);
  logger.info(`Title [${titleTemplate}]: ${title}`);

  const hook = isPop(format) ? pop.popHook(plan) : hookLine(plan);
  const description = isPop(format) ? pop.popDescription(plan) : buildDescription(plan, hook);
  const answersText = isPop(format) ? pop.popAnswers(plan) : answersBlock(plan);
  const hashtags = isPop(format) ? pop.popHashtags(plan) : HASHTAGS[format];
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
    tags: isPop(format) ? pop.popTags(plan) : buildTags(plan),
    categoryId: isPop(format) ? '24' : '27',
    localizations,
    playlistTitle: isPop(format) ? pop.popPlaylist(plan) : PLAYLISTS[format],
    comment: isPop(format) ? pop.popComment(plan) : format === 'bigger' || format === 'crowd'
      ? 'Which one surprised you the most? 🤯 Tell me your score 👇'
      : `What did you score out of ${plan.rounds.length}? 🏆 Which one got you? 👇`,
    country: 'Global',
    category: `quiz-${format}`,
    editType: 'quiz',
    geminiScore: null,
    sourceChannel: null,
    quiz: {
      format, theme, seed: plan.seed, titleTemplate, length, followUpOf: hit ? hit.videoId : null,
      iso2s: popKeys || plan.rounds.flatMap(r => (r.left ? [r.left.iso2, r.right.iso2] : [r.iso2])),
      keys: popKeys,
      answers: plan.rounds.map(label),
      duration: res.duration,
    },
  };
}

module.exports = { runWorldQuizPipeline, recordUpload, loadHistory, pickFormat, pickLength, findHit, pickTitleTemplate, buildDescription, buildTags, TITLE_TEMPLATES, PLAYLISTS };
