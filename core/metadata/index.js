/**
 * Self-learning metadata optimizer for the Shorts channels: title, on-video title, emoji,
 * hashtags, description, tags and the first comment of every upload.
 *
 *   const meta = await optimizeMetadata({ channel: 'meme', contentType: 'stem-edit',
 *     facts: { song: 'kick back', source: 'Chainsaw Man', kind: 'anime', theme: 'anime' } });
 *   // meta: {title, onVideoTitle, emoji, hashtags, hashtagLine, descriptionHead, description,
 *   //        tags, comment, arms, modes, titleSource, choice, ...}
 *   ... upload ...
 *   appendDecision({ vid, ch, title: meta.title, arm: { ...yourArms, ...meta.arms } });   // experiments.jsonl
 *   recordMetadataChoice(meta, { videoId });                                             // metadata-choices.jsonl
 *
 * How it works
 *   1. arms: for each dimension (title style, on-video style, hashtag set, emoji set, description
 *      hook) the learner (learner.js) picks an arm by Thompson sampling on how our own uploads with
 *      that arm did, with a 25% exploration floor (catalog.js has the seeds; evolve.js adds / retires
 *      arms every week)
 *   2. trend awareness: YouTube autocomplete for the video's keywords (suggest.js), so titles and
 *      tags use the phrases people actually search
 *   3. generation: arms marked llm are written by an LLM briefed as a Shorts packaging expert (below),
 *      N candidates, validated (rules.js), the best kept by keyword position + real search phrasing +
 *      the LLM's own guess as a weak prior; template arms and every fallback need no LLM
 *   4. the arms actually used are returned in `arms` for the decision log, so the learner can
 *      credit them once the video's reward (core/experiment-log.js) comes in
 */
const learner = require('./learner');
const catalog = require('./catalog');
const rules = require('./rules');
const llm = require('./llm');
const { suggestMany, bestSuggestTitle } = require('./suggest');

const { DIMS, armsFor, observations, choose, appendChoice } = learner;
const { poolKey, specFor, varsFor } = catalog;
const { fill, clean, stripEmoji, validateTitle, validateOnVideo, validateLine, unsad, hasSad, len, norm, slugTag } = rules;

// ─── the expert ──────────────────────────────────────────────────

const EXPERT_SYSTEM = [
  'You are a YouTube Shorts packaging expert. You write the title, the on-screen text and the description line for one Short.',
  'How Shorts get picked up today:',
  '- Every new Short is first shown in the Shorts feed to a small seed audience, mostly people who already watch this niche. '
    + 'What is measured first is whether they stop and watch or swipe away (viewed vs swiped away), then how much of it they watch '
    + 'and whether it loops, then likes, comments, shares and subscribes. A good seed test earns bigger waves of similar viewers; '
    + 'a bad one ends it. So the packaging must pull the right viewers for THIS video, not everyone.',
  '- In the feed the title is small: the first second of the video and the on-screen text decide the swipe. The on-screen text must '
    + 'be readable in half a second (26 characters max), promise curiosity or hype, and be true for this video.',
  '- The title matters most for YouTube search, suggested videos, the channel page and after a tap, and it tells the system who the '
    + 'Short is for. Put the searchable keyword first, in the words people actually type ("<song> acapella", "<anime> edit", '
    + '"<character> edit", "guess the kpop song"). Lowercase meme voice is normal in anime and K-pop edit niches.',
  '- Hashtags: 1-3 specific ones (the anime, the group, the format); generic ones (#viral #fyp #trending) do nothing.',
  '- The description\'s first line shows in search results: say plainly what the video is, keyword first, no keyword stuffing.',
  'Hard rules: no clickbait the video does not deliver (viewers swipe and the Short dies); no invented numbers or statistics '
    + '("99% fail", "only 1% get this"); no fake claims ("official", "leaked", a "part 2" that does not exist); never sexual or '
    + 'suggestive, no comments on anyone\'s body, no mocking people; no sad or crying emoji. Do not put emoji or hashtags in the '
    + 'texts you write: they are added separately.',
  'Return only JSON.',
].join('\n');

// ─── helpers ─────────────────────────────────────────────────────

const pickOne = (list, rng) => list[Math.floor(rng() * list.length)];
const uniq = xs => [...new Set(xs)];
const tagStr = tags => tags.map(t => `#${t}`).join(' ');
const maxTitle = spec => spec.maxTitle || 70;

function titleRules(spec, f) {
  return { maxLen: maxTitle(spec), must: spec.must(f), allowImpossible: !!f.hard, forbidden: f.forbidden || [] };
}

/** Clean an LLM-written text: no quotes, emoji, hashtags or < >. */
function cleanLLM(s) {
  return stripEmoji(clean(String(s || '').replace(/#[\p{L}\p{N}_]+/gu, ' '))).replace(/^["'“”‘’]+|["'“”‘’]+$/g, '').trim();
}

/** Quiz template id -> style family (for templates that do not say). */
function quizStyleOf(id) {
  const s = String(id || '');
  if (/-llm$/.test(s)) return 'llm';
  if (/-casual/.test(s)) return 'casual';
  if (/-ramp$/.test(s)) return 'ramp';
  if (/-(3sec|4sec|timer|blur)$/.test(s)) return 'speed';
  if (/-(expert|otaku|fans|lookalike|trap|holo|travel)$/.test(s)) return 'fans';
  if (/-(all5|know|name|find|howwell|harder|hard|agree|maplies)$/.test(s)) return 'challenge';
  return 'keyword';
}

function templatesOf(f) {
  return (f.templates || []).map(t => ({ ...t, style: t.style || quizStyleOf(t.id) }));
}

function renderEmoji(arm, vars, rng) {
  if (!arm) return { list: [], keep: false };
  if (arm.keep) return { list: vars.topicEmoji ? [vars.topicEmoji] : [], keep: true };
  const opts = (arm.pool || []).map(p => fill(p, vars)).filter(x => x !== null);
  const pick = opts.length ? pickOne(opts, rng) : '';
  return { list: String(pick).split(',').map(s => s.trim()).filter(e => e && !hasSad(e)), keep: false };
}

/** {title: [...], desc: [...]} (without #), max 3 in all; null when the set does not apply. */
function renderHashtags(arm, vars) {
  if (!arm) return null;
  const ok = t => t && /^[a-z0-9]{2,30}$/.test(t) && !rules.GENERIC_TAGS.has(t);
  const title = (arm.title || []).map(t => fill(t, vars));
  if (title.some(t => !t)) return null;
  const tt = uniq(title.map(t => t.replace(/^#/, '').toLowerCase())).filter(ok).slice(0, 1);
  if ((arm.title || []).length && !tt.length) return null;
  const desc = uniq((arm.desc || []).map(t => fill(t, vars)).filter(Boolean).map(t => t.replace(/^#/, '').toLowerCase()))
    .filter(t => ok(t) && !tt.includes(t)).slice(0, 3 - tt.length);
  if (!tt.length && !desc.length) return null;
  return { title: tt, desc };
}

/** A template-style title (or null if it does not fit this video). */
function renderTitleArm(arm, ctx, eTitle, tags) {
  if (!arm || arm.llm) return null;
  let text = null;
  let templateId = null;
  if (arm.fromTemplates) {
    const list = templatesOf(ctx.f).filter(t => t.style === arm.id);
    const fresh = list.filter(x => !(ctx.f.recentTemplateIds || []).includes(x.id));
    const t = list.find(x => x.id === ctx.f.templateId) || (list.length ? pickOne(fresh.length ? fresh : list, ctx.rng || Math.random) : null);
    if (!t) return null;
    templateId = t.id;
    text = ctx.emojiKeep ? unsad(t.text) : `${stripEmoji(t.text)} ${eTitle}`.trim();
  } else {
    const tpl = [].concat(arm.template || arm.templates || [])[0];
    text = fill(tpl, { ...ctx.vars, e: eTitle });
    if (text && tpl && !tpl.includes('{e}') && eTitle) text = `${text} ${eTitle}`;
    if (text && ctx.f.format) templateId = `${ctx.f.format}-${arm.id}`;
  }
  if (!text) return null;
  const full = tags ? `${text} ${tags}` : text;
  // the pipeline's own templates are trusted for keywords (e.g. "Only Real Otakus Get 5/5"), not for the rest
  const v = validateTitle(full, arm.fromTemplates ? { ...ctx.rules, must: [] } : ctx.rules);
  return v.ok ? { text: full, templateId } : null;
}

function renderOnVideoArm(arm, ctx) {
  if (!arm || arm.llm) return null;
  for (const tpl of [].concat(arm.templates || arm.template || [])) {
    let t = fill(tpl, ctx.vars);
    if (!t) continue;
    if (ctx.spec.lowerOnVideo) t = t.toLowerCase();
    if (validateOnVideo(t, ctx.ovRules).ok) return t;
  }
  return null;
}

function renderDescArm(arm, ctx, e1) {
  if (!arm || arm.llm) return null;
  const t = fill(arm.template, { ...ctx.vars, e1, e: e1 });
  return t && validateLine(t, { maxLen: 240, allowImpossible: !!ctx.f.hard, forbidden: ctx.f.forbidden }).ok ? t : null;
}

/** Can this arm be used for this video? */
function eligibleFor(dim, arm, ctx) {
  if (arm.notFor && ctx.f.format && arm.notFor.includes(ctx.f.format)) return false;
  if (arm.llm) return !!ctx.llmOk;
  switch (dim) {
    case 'titleStyle': return !!renderTitleArm(arm, { ...ctx, rng: () => 0 }, '🔥🔥', '');
    case 'ovStyle': return !!renderOnVideoArm(arm, ctx);
    case 'hashtagSet': return !!renderHashtags(arm, ctx.vars);
    case 'emojiSet': return !!arm.keep || (arm.pool || []).some(p => fill(p, ctx.vars) !== null);
    case 'descHook': return !!renderDescArm(arm, ctx, '🔥');
    default: return true;
  }
}

function makeCtx(spec, f, { llmOk = false, rng = Math.random } = {}) {
  const vars = varsFor(spec, f);
  return { spec, f, vars, llmOk, rng, rules: titleRules(spec, f), ovRules: { maxLen: 26, allowImpossible: !!f.hard, forbidden: f.forbidden || [] } };
}

/** Pick one arm per dimension. force: {dim: armId} (a hit's arms for a remake). */
function pickArms(pool, ctx, force = {}, obs = null) {
  const arms = armsFor(pool);
  const o = obs || observations(pool);
  const chosen = {};
  const modes = {};
  for (const dim of DIMS) {
    const list = arms[dim];
    if (!list) continue;
    const forced = force && force[dim] && list.find(a => a.id === force[dim]);
    if (forced && eligibleFor(dim, forced, ctx)) { chosen[dim] = forced; modes[dim] = 'forced'; continue; }
    const r = choose(list, o, dim, { rng: ctx.rng, eligible: a => eligibleFor(dim, a, ctx) });
    if (r) { chosen[dim] = r.arm; modes[dim] = r.mode; }
  }
  return { chosen, modes, arms };
}

/** One arm of one dimension (e.g. the quiz title style before the template is chosen). */
function pickArm({ channel, contentType, dim, eligible, rng = Math.random }) {
  const pool = poolKey(channel, contentType);
  const r = choose((armsFor(pool)[dim]) || [], observations(pool), dim, { rng, eligible });
  return r ? { id: r.arm.id, arm: r.arm, mode: r.mode } : null;
}

// ─── the LLM writer ──────────────────────────────────────────────

function examplesText(obs) {
  const withTitle = obs.filter(o => o.title);
  if (withTitle.length < 2) return 'Our scored titles so far: none yet (the format is new).';
  const sorted = [...withTitle].sort((a, b) => b.x - a.x);
  const f = o => `"${o.title}" (${o.views} views, score ${o.x >= 0 ? '+' : ''}${o.x.toFixed(1)})`;
  const best = sorted.filter(o => o.x > 0).slice(0, 4);
  const worst = sorted.filter(o => o.x < 0).slice(-3).reverse();
  return [best.length ? `Our best titles so far (score: views, % viewed and subs vs our average upload, 0 = average):\n${best.map(f).join('\n')}` : '',
    worst.length ? `Our weakest titles so far:\n${worst.map(f).join('\n')}` : ''].filter(Boolean).join('\n');
}

async function generate(ctx, chosen, { keyword, suggestions, obs, eTitle, tagsTitle, n }) {
  const { spec, f, vars } = ctx;
  const need = {
    title: chosen.titleStyle && chosen.titleStyle.llm,
    ov: chosen.ovStyle && chosen.ovStyle.llm,
    desc: chosen.descHook && chosen.descHook.llm,
  };
  if (!need.title && !need.ov && !need.desc) return null;
  const room = Math.min(60, maxTitle(spec) - (eTitle ? len(eTitle) + 1 : 0) - (tagsTitle ? len(tagsTitle) + 1 : 0));
  const ex = arm => (arm.example ? fill(arm.example, { ...vars, e: '' }) : null);
  const must = spec.must(f).map(g => [].concat(g).filter(Boolean)).filter(g => g.length);
  const lines = [
    `Channel: ${spec.channelName}`,
    `The video: ${spec.describe(f)}`,
    `Main search keyword: "${keyword}" (every title must contain it, ideally first)`,
    must.length ? `Every title must contain: ${must.map(g => g.map(x => `"${x}"`).join(' or ')).join(', and ')}` : '',
    suggestions.length ? `Real YouTube autocomplete phrases that match this video (how people search): ${suggestions.slice(0, 10).join(' | ')}` : '',
    examplesText(obs),
    (f.forbidden || []).length ? `Never reveal these answers: ${f.forbidden.slice(0, 12).join(', ')}` : '',
    f.format && !f.hard ? 'Do not say "impossible" (no round in this quiz is very hard).' : '',
    '',
    'Write this JSON:',
  ];
  const shape = {};
  if (need.title) {
    lines.push(`"titles": ${n} different titles in this style: ${chosen.titleStyle.guide || 'keyword first'}.`
      + `${ex(chosen.titleStyle) ? ` Style example: "${ex(chosen.titleStyle)}".` : ''} At most ${room} characters each, no emoji, no hashtags.`);
    shape.titles = [{ text: '...', pull: 7 }];
  }
  if (need.ov) {
    lines.push(`"onVideo": ${n} on-screen texts in this style: ${chosen.ovStyle.guide || 'short and punchy'}.`
      + `${ex(chosen.ovStyle) ? ` Style example: "${ex(chosen.ovStyle)}".` : ''} At most 24 characters, Latin letters only, no emoji, no hashtags.`);
    shape.onVideo = [{ text: '...', pull: 6 }];
  }
  if (need.desc) {
    lines.push(`"descriptionLine": one sentence in this style: ${chosen.descHook.guide || 'keyword first'}. At most 150 characters, no emoji, no hashtags.`);
    shape.descriptionLine = '...';
  }
  lines.push('"comment": one short question for the channel\'s first comment that viewers will want to answer, at most 80 characters, no emoji.');
  shape.comment = '...';
  lines.push('"pull": your honest 1-10 guess of how strongly each text makes the seed audience stop and watch (a weak hint; our data decides).');
  lines.push(`JSON shape: ${JSON.stringify(shape)}`);
  const r = await llm.chatJSON(EXPERT_SYSTEM, lines.filter(x => x !== '').join('\n'),
    { timeoutMs: Number(process.env.META_LLM_TIMEOUT_MS || 45000) });
  if (!r || typeof r !== 'object') return null;
  const items = xs => (Array.isArray(xs) ? xs : []).map(x => (typeof x === 'string' ? { text: x } : x))
    .filter(x => x && typeof x.text === 'string').map(x => ({ text: x.text, pull: Number(x.pull) }));
  return { titles: items(r.titles), onVideo: items(r.onVideo), descriptionLine: typeof r.descriptionLine === 'string' ? r.descriptionLine : '',
    comment: typeof r.comment === 'string' ? r.comment : '', provider: llm.provider() };
}

/** Candidate score: keyword first, real search phrasing, sweet-spot length, not a repeat; the LLM's guess only nudges. */
function scoreTitle(text, { keyword, suggestions, recent, pull }) {
  const t = norm(stripEmoji(text).replace(/#[\p{L}\p{N}_]+/gu, ''));
  const k = norm(keyword);
  let s = 0;
  if (k && t.startsWith(k)) s += 1;
  else if (k && t.includes(k) && t.indexOf(k) < 20) s += 0.5;
  if (suggestions.some(p => norm(p) !== k && t.includes(norm(p)))) s += 0.3;
  const L = len(text);
  if (L >= 25 && L <= 60) s += 0.2;
  if ((recent || []).some(r => norm(stripEmoji(r)) === t)) s -= 0.6;
  if (Number.isFinite(pull)) s += 0.15 * (Math.max(1, Math.min(10, pull)) - 5.5) / 4.5;
  return s;
}

// words an autocomplete phrase may add on top of the video's own facts
const LINK_WORDS = ['a', 'an', 'the', 'of', 'by', 'x', 'ft', 'feat', 'and', 'in', 'from', 'with', 'to', 'acapella', 'cappella', 'acappella',
  'edit', 'edits', 'amv', 'quiz', 'guess', 'vocals', 'vocal'];  // not 'song': "<anime> edit song" is a search for the music

/** Every word the video's facts back up (song, artist, anime / group and its aliases, quiz keyword ...). */
function factVocab(f) {
  const words = new Set(LINK_WORDS);
  const add = s => { for (const w of norm(s).split(' ')) if (w) words.add(w); };
  for (const k of ['song', 'artist', 'source', 'character', 'group', 'member', 'keyword', 'Keyword', 'topicLabel', 'format', 'hook']) add(f[k]);
  // the song's search query minus its boilerplate ("official audio" must never reach a title)
  for (const w of norm(f.query).split(' ')) if (w && !/^(official|audio|video|mv|music|lyrics?|hd|full|version)$/.test(w)) words.add(w);
  for (const a of [].concat(f.aliases || [], f.vocab || [])) add(a);
  return words;
}

/**
 * An autocomplete phrase describes THIS video only when all its words are backed by the facts
 * ("idol acapella bts" is another song, "... boy next door acapella" another version: never use those).
 */
function factual(phrase, vocab) {
  const ws = norm(phrase).split(' ').filter(Boolean);
  return ws.length > 0 && ws.every(w => vocab.has(w));
}

function buildTags(spec, f, suggestions) {
  const list = [...spec.tags(f), ...suggestions.slice(0, 4), ...(f.baseTags || []), ...(f.extraTags || [])];
  const out = [];
  let total = 0;
  for (const t0 of list) {
    const t = clean(String(t0 || '')).replace(/[,#]/g, '').trim();
    if (!t || hasSad(t) || out.some(x => x.toLowerCase() === t.toLowerCase()) || total + t.length + 3 > 480) continue;
    out.push(t);
    total += t.length + 3;
  }
  return out;
}

const FACT_KEYS = ['song', 'artist', 'source', 'kind', 'theme', 'character', 'group', 'member', 'keyword', 'Keyword', 'any', 'format',
  'topic', 'topicLabel', 'topicEmoji', 'n', 'hard', 'formatTags', 'hook', 'cta'];
function compactFacts(f) {
  const out = {};
  for (const k of FACT_KEYS) {
    const v = f[k];
    if (v === undefined || v === null || v === '') continue;
    out[k] = typeof v === 'string' ? v.slice(0, 160) : v;
  }
  return out;
}

// ─── the API ─────────────────────────────────────────────────────

/**
 * Title, on-video title, emoji, hashtags, description, tags and comment for one video.
 *
 * @param {object} o
 * @param {string} o.channel      'meme' (Zero Yen Otaku) or 'quiz' (Asian Pop Quiz)
 * @param {string} o.contentType  meme: 'stem-edit' | 'song-edit' | 'character-edit' | 'dance-edit' | 'kpop-edit' |
 *                                'sound-edit' | 'long-edit' (unknown types behave like song-edit keyed on facts.keyword);
 *                                quiz: anything (all formats share one pool; pass facts.format)
 * @param {object} o.facts        what is in the video: song, artist, source (anime / group), kind, theme
 *                                ('anime' | 'kpop' | 'jpop'), character, group, member, keyword (override),
 *                                extraTags, recentTitles; quiz: format, keyword, Keyword, any, n, hard, topic,
 *                                topicLabel, topicEmoji, formatTags, hook, cta, forbidden (answers), templates
 *                                ([{id, text, style}]), templateId, baseTags, comment
 * @param {object} [o.style]      force arms by id, e.g. a hit's arms for its remake: {titleStyle: 'kw-source'}
 * @param {boolean} [o.llm=true]  false: templates only
 * @param {number} [o.n=4]        LLM candidates per text
 * @returns {Promise<object>} {title, onVideoTitle, emoji ('🔥,🎧' for the renderer, or 'none'), emojiList, hashtags,
 *   titleHashtags, hashtagLine, descriptionHead, description, tags, comment, arms (log these), modes, titleSource,
 *   templateId, keyword, suggestions, candidates, choice (pass to recordMetadataChoice)}
 */
async function optimizeMetadata({ channel = 'meme', contentType = 'stem-edit', facts = {}, style = {}, llm: useLLM = true, n = 4,
  rng = Math.random } = {}) {
  const pool = poolKey(channel, contentType);
  const spec = specFor(pool);
  const f = { ...facts };
  const keyword = spec.keyword(f) || '';

  // trend awareness: the phrases people actually type (cached; [] offline), kept only when the
  // video's facts back every word of them
  let suggestionsAll = [];
  try { suggestionsAll = await suggestMany(spec.searchSeeds(f)); } catch {}
  const vocab = factVocab(f);
  const suggestions = suggestionsAll.filter(s => factual(s, vocab));
  if (!f.suggestTitle) f.suggestTitle = bestSuggestTitle(suggestions, spec.suggestMust(f), keyword);

  const llmOk = useLLM !== false && llm.available();
  const ctx = makeCtx(spec, f, { llmOk, rng });
  const obs = observations(pool);
  const { chosen, modes, arms } = pickArms(pool, ctx, style || {}, obs);
  const byId = (dim, id) => (arms[dim] || []).find(a => a.id === id);
  // the template arm an LLM arm falls back to (its named one, else the first active one that fits)
  const fallbackOf = (dim, arm) => {
    const named = byId(dim, arm.fallback);
    if (named && named.status !== 'retired' && eligibleFor(dim, named, ctx)) return named;
    return (arms[dim] || []).find(a => !a.llm && a.status !== 'retired' && eligibleFor(dim, a, ctx)) || named || null;
  };
  const used = {};

  // emoji (energetic families; drawn as images next to the on-video title)
  const em = renderEmoji(chosen.emojiSet, ctx.vars, rng);
  if (chosen.emojiSet) used.emojiSet = chosen.emojiSet.id;
  ctx.emojiKeep = em.keep;
  const eTitle = em.list.join('');

  // hashtags
  let tags = chosen.hashtagSet && renderHashtags(chosen.hashtagSet, ctx.vars);
  if (tags) used.hashtagSet = chosen.hashtagSet.id;
  else tags = { title: [], desc: [slugTag(keyword)].filter(Boolean) };
  const moveTitleTags = () => {
    tags = { title: [], desc: uniq([...tags.title, ...tags.desc]).slice(0, 3) };
    if (chosen.hashtagSet) used.hashtagSet = chosen.hashtagSet.fallback || chosen.hashtagSet.id;
  };

  const gen = llmOk ? await generate(ctx, chosen, { keyword, suggestions, obs, eTitle, tagsTitle: tagStr(tags.title), n }) : null;
  const recent = f.recentTitles || obs.map(o => o.title).filter(Boolean);

  // title
  let title = null;
  let titleSource = 'template';
  let templateId = null;
  let candidates = [];
  const ts = chosen.titleStyle;
  if (ts && ts.llm && gen && gen.titles.length) {
    for (const withTags of [true, false]) {
      if (!withTags && !tags.title.length) break;
      candidates = gen.titles.map(c => {
        const text = cleanLLM(c.text);
        const full = [text, eTitle, withTags ? tagStr(tags.title) : ''].filter(Boolean).join(' ');
        const v = validateTitle(full, ctx.rules);
        return { text: full, pull: c.pull, ok: v.ok, why: v.reasons, score: v.ok ? scoreTitle(full, { keyword, suggestions, recent, pull: c.pull }) : null };
      });
      const ok = candidates.filter(c => c.ok).sort((a, b) => b.score - a.score);
      if (ok.length) {
        title = ok[0].text;
        titleSource = 'llm';
        used.titleStyle = ts.id;
        if (!withTags) moveTitleTags();
        break;
      }
    }
  }
  if (!title) {
    const firstTemplate = (arms.titleStyle || []).find(a => !a.llm && a.status !== 'retired' && eligibleFor('titleStyle', a, ctx));
    const chain = [ts && (ts.llm ? fallbackOf('titleStyle', ts) : ts), firstTemplate].filter(Boolean);
    for (const arm of chain) {
      let r = renderTitleArm(arm, ctx, eTitle, tagStr(tags.title));
      if (!r && tags.title.length) { r = renderTitleArm(arm, ctx, eTitle, ''); if (r) moveTitleTags(); }
      if (r) { title = r.text; templateId = r.templateId; used.titleStyle = arm.id; break; }
    }
  }
  if (!title) {
    title = clean(`${keyword} ${eTitle}`);
    used.titleStyle = 'keyword-only';
  }
  title = unsad(clean(title));

  // on-video title (no emoji: the video font draws them as squares; they are pasted as images instead)
  let onVideoTitle = null;
  let ovCandidates = [];
  const ov = chosen.ovStyle;
  if (ov && ov.llm && gen && gen.onVideo.length) {
    ovCandidates = gen.onVideo.map(c => {
      let t = cleanLLM(c.text);
      if (spec.lowerOnVideo) t = t.toLowerCase();
      const v = validateOnVideo(t, ctx.ovRules);
      const subj = [ctx.vars.song, ctx.vars.subject].filter(Boolean);
      const score = v.ok ? (subj.some(s => rules.contains(t, s)) ? 0.3 : 0) + (len(t) <= 22 ? 0.1 : 0)
        + (Number.isFinite(c.pull) ? 0.15 * (Math.max(1, Math.min(10, c.pull)) - 5.5) / 4.5 : 0) : null;
      return { text: t, ok: v.ok, why: v.reasons, score };
    });
    const ok = ovCandidates.filter(c => c.ok).sort((a, b) => b.score - a.score);
    if (ok.length) { onVideoTitle = ok[0].text; used.ovStyle = ov.id; }
  }
  if (!onVideoTitle && ov) {
    const arm = ov.llm ? fallbackOf('ovStyle', ov) : ov;
    onVideoTitle = renderOnVideoArm(arm, ctx);
    if (onVideoTitle) used.ovStyle = arm.id;
  }
  if (!onVideoTitle) {
    for (const t0 of [keyword, ctx.vars.song, ctx.vars.subject]) {
      const t = t0 && (spec.lowerOnVideo ? t0.toLowerCase() : t0);
      if (t && validateOnVideo(t, ctx.ovRules).ok) { onVideoTitle = t; break; }
    }
  }

  // description: the first line shows in search (keyword first), then the question, then hashtags
  const e1 = em.list[0] || '🔥';
  let head = null;
  const dh = chosen.descHook;
  if (dh && dh.llm && gen && gen.descriptionLine) {
    const line = cleanLLM(gen.descriptionLine);
    const v = validateLine(line, { maxLen: 200, must: spec.must(f), allowImpossible: !!f.hard, forbidden: f.forbidden });
    if (v.ok) { head = `${line} ${e1}`; used.descHook = dh.id; }
  }
  if (!head && dh) {
    const arm = dh.llm ? fallbackOf('descHook', dh) : dh;
    head = renderDescArm(arm, ctx, e1);
    if (head) used.descHook = arm.id;
  }
  if (!head) head = `${keyword} ${e1}`.trim();
  // the question line after the hook, unless the hook already has it ({cta} in its template, noCta, or a question)
  const usedHook = used.descHook && byId('descHook', used.descHook);
  const hasCta = !!usedHook && (usedHook.noCta || /\{cta\}/.test(String(usedHook.template || '')));
  const cta = spec.cta ? spec.cta(f) : '';
  const descriptionHead = !hasCta && cta && !head.includes('?') ? `${head}\n\n${cta}` : head;
  const hashtagLine = tagStr(tags.desc);
  const description = [descriptionHead.replace(/[<>]/g, ''), f.descriptionExtra, hashtagLine].filter(Boolean).join('\n\n');

  // first comment: a question viewers want to answer (comments are a signal too)
  let comment = f.comment || null;
  if (!comment && gen && gen.comment) {
    const c = cleanLLM(gen.comment);
    if (validateLine(c, { maxLen: 100 }).ok && /\?/.test(c)) comment = `${c} ${e1}👇`;
  }
  if (!comment) comment = pickOne(spec.comments || ['what did you think? 👇'], rng);
  comment = unsad(comment);

  const armsUsed = { contentType: pool.split('/')[1], ...used };
  const choice = { pool, arms: armsUsed, modes, title, onVideoTitle, titleSource, templateId, facts: compactFacts(f) };
  return {
    title, onVideoTitle,
    emoji: em.list.length ? em.list.join(',') : 'none', emojiList: em.list,
    hashtags: [...tags.title, ...tags.desc].map(t => `#${t}`), titleHashtags: tags.title, hashtagLine,
    descriptionHead, description, tags: buildTags(spec, f, suggestions), comment,
    arms: armsUsed, modes, titleSource, templateId, pool, keyword, suggestions, suggestionsAll,
    // which LLM wrote the LLM arms' texts: 'off' (unavailable), 'unused' (only template arms picked), 'failed' (fell back)
    llm: gen ? gen.provider || 'llm' : !llmOk ? 'off' : ['titleStyle', 'ovStyle', 'descHook'].some(d => chosen[d] && chosen[d].llm) ? 'failed' : 'unused',
    candidates: { titles: candidates, onVideo: ovCandidates },
    choice,
  };
}

/** Description with extra lines (credits) between the hook and the hashtags. */
function buildDescription(meta, ...extra) {
  return [meta.descriptionHead, ...extra.map(x => String(x || '').trim()), meta.hashtagLine].filter(Boolean).join('\n\n');
}

/**
 * Log what an upload used, so the learner can credit its arms when the reward comes in.
 * Writes <MEMORY_DIR>/metadata-choices.jsonl. (Also spread meta.arms into your appendDecision arm.)
 */
function recordMetadataChoice(metaOrChoice, { videoId } = {}) {
  const c = metaOrChoice && metaOrChoice.choice ? metaOrChoice.choice : metaOrChoice;
  if (!c || !c.pool || !videoId) return false;
  appendChoice({ at: new Date().toISOString(), vid: videoId, ...c });
  return true;
}

/**
 * Quiz: the title template for a plan. The style (keyword / speed / challenge / fans / casual /
 * ramp / llm / styles the weekly step added) is learned across all formats; inside the style the
 * template rotates (never one of the format's last two).
 * options: the format's templates already filtered for this plan ([{id, text(plan), style}]).
 */
function pickQuizTitle({ plan, options, llmAvailable, recentIds = [], facts = {}, rng = Math.random }) {
  const spec = specFor('quiz/quiz');
  const templates = options.map(t => ({ id: t.id, style: t.style || quizStyleOf(t.id), text: t.text(plan) }));
  const ctx = makeCtx(spec, { ...facts, templates, recentTemplateIds: recentIds }, { llmOk: llmAvailable && llm.available(), rng });
  const r = choose(armsFor('quiz/quiz').titleStyle || [], observations('quiz/quiz'), 'titleStyle',
    { rng, eligible: a => eligibleFor('titleStyle', a, ctx) });
  if (!r) return null;
  const arm = r.arm;
  if (arm.llm) return { id: `${plan.format}-llm`, llm: true, style: arm.id, mode: r.mode };
  if (arm.fromTemplates) {
    const inStyle = options.filter(t => (t.style || quizStyleOf(t.id)) === arm.id);
    const fresh = inStyle.filter(t => !recentIds.includes(t.id));
    const list = fresh.length ? fresh : inStyle;
    return { ...pickOne(list, rng), style: arm.id, mode: r.mode };
  }
  // a style the weekly step added: its own template
  const tpl = arm.template;
  return { id: `${plan.format}-${arm.id}`, style: arm.id, mode: r.mode, text: () => fill(tpl, { ...ctx.vars, e: ctx.vars.topicEmoji || '' }) };
}

/** Arms of a pool with their posteriors (for reports and debugging). */
function describePool(channel, contentType) {
  const pool = poolKey(channel, contentType);
  const arms = armsFor(pool);
  const obs = observations(pool);
  const out = {};
  for (const dim of DIMS) {
    if (!arms[dim]) continue;
    const post = learner.posteriors(arms[dim], obs, dim);
    out[dim] = arms[dim].map(a => ({ id: a.id, status: a.status, source: a.source, ...post.get(a.id) }));
  }
  return { pool, n: obs.length, arms: out };
}

module.exports = {
  optimizeMetadata, recordMetadataChoice, buildDescription, pickQuizTitle, pickArm, describePool, quizStyleOf,
  EXPERT_SYSTEM, poolKey, scoreTitle,
  // internals for the tests and the weekly step
  _internal: { renderTitleArm, renderOnVideoArm, renderDescArm, renderHashtags, renderEmoji, eligibleFor, makeCtx, pickArms, generate, cleanLLM },
};
