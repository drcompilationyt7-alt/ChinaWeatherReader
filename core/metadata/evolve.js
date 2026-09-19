/**
 * Weekly self-improvement of the metadata arms (run by core/reflect/weekly-report.js on Sundays).
 *
 * For every pool (channel / content type) of the channel:
 *   1. score each arm: posterior mean +- sd of the pool-standardized reward, and its uses
 *   2. retire clear losers: >= 4 scored uses, mean below the pool average and well below the best
 *      arm (z > 2); at most 2 per pool a week, never below 2 active arms in a dimension and never
 *      the last template arm (the fallback when the LLM is down)
 *   3. OPRO-style proposals (optimization by prompting): the LLM sees every arm with its score, the
 *      best and worst titles and real autocomplete phrases, and proposes up to 3 new arms. Each is
 *      validated on the facts of recent uploads (length, keyword, no invented stats, no sad emoji,
 *      specific hashtags ...) and added with a bounded optimistic prior (+0.2..+0.5 from the LLM's
 *      confidence, worth one upload): the bandit tries it a few times, and a bad one sinks fast.
 *      The LLM's opinion is only that weak prior; the uploads decide.
 *   4. once per ISO week per pool (re-running the workflow changes nothing more); state in
 *      <MEMORY_DIR>/metadata-arms.json, the report gets a markdown section.
 */
const learner = require('./learner');
const catalog = require('./catalog');
const rules = require('./rules');
const llm = require('./llm');
const { suggestMany } = require('./suggest');
const { EXPERT_SYSTEM, _internal } = require('./index');
const { loadDecisions } = require('../experiment-log');

const { DIMS, loadState, saveState, armsFor, observations, posteriors, readChoices, decisionsFor } = learner;
const { specFor, channelKey, poolKey } = catalog;

const MIN_RETIRE_USES = 4;
const RETIRE_Z = 2;  // "clearly worse": the best arm leads by 2 standard errors
const MAX_RETIRE = 2;
const MAX_ADD = 3;
const MAX_ACTIVE = 8;
const MIN_OBS_FOR_PROPOSALS = () => Number(process.env.META_OPRO_MIN || 5);

const PLACEHOLDERS = {
  'meme/stem-edit': ['song', 'Song', 'source', 'Source', 'artist', 'kw', 'suggest', 'sourceTag', 'songTag', 'themeTag', 'editTag', 'e', 'e1'],
  'quiz/quiz': ['keyword', 'Keyword', 'n', 'topicLabel', 'topicEmoji', 'hook', 'cta', 'fmt1', 'fmt2', 'fmt3', 'e', 'e1'],
  edit: ['kw', 'subject', 'song', 'withSong', 'Song', 'artist', 'source', 'Source', 'character', 'group', 'member', 'suggest', 'subjectTag', 'sourceTag',
    'songTag', 'themeTag', 'editTag', 'e', 'e1'],
};
const NICHE_SEEDS = {
  'meme/stem-edit': ['acapella', 'anime acapella', 'anime edit', 'kpop edit'],
  'quiz/quiz': ['guess the kpop song', 'anime quiz', 'guess the anime', 'kpop quiz'],
  edit: ['anime edit', 'kpop edit', 'amv'],
};
const placeholdersOf = pool => PLACEHOLDERS[pool] || PLACEHOLDERS.edit;

function isoWeek(d = new Date()) {
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  const y0 = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  return `${t.getUTCFullYear()}-W${String(Math.ceil(((t - y0) / 86400000 + 1) / 7)).padStart(2, '0')}`;
}

const fmt = x => (Number.isFinite(x) ? (x >= 0 ? '+' : '') + x.toFixed(2) : '–');

/** Pools of a channel that have seeds, stored arms or logged uploads. */
function poolsOf(channel, state) {
  const ch = channelKey(channel);
  const pools = new Set([ch === 'quiz' ? 'quiz/quiz' : 'meme/stem-edit']);
  for (const p of Object.keys(state.pools || {})) if (p.startsWith(`${ch}/`)) pools.add(p);
  for (const c of readChoices()) if (c.pool && c.pool.startsWith(`${ch}/`)) pools.add(c.pool);
  for (const d of loadDecisions()) {
    if (!d.arm || !d.arm.titleStyle) continue;
    const p = poolKey(d.ch === 'quiz' ? 'quiz' : 'meme', d.arm.contentType || (d.ch === 'quiz' ? 'quiz' : 'stem-edit'));
    if (p.startsWith(`${ch}/`)) pools.add(p);
  }
  return [...pools];
}

/** Facts of recent uploads (to validate proposals on), else the catalog's samples. */
function samplesFor(pool) {
  const recent = decisionsFor(pool).filter(d => d.facts && Object.keys(d.facts).length).slice(-12).map(d => d.facts);
  return recent.length >= 3 ? recent : [...recent, ...(specFor(pool).samples || [])];
}

// ─── 2. retire ───────────────────────────────────────────────────

function retireLosers(arms, obs, maxRetire = MAX_RETIRE) {
  const out = [];
  for (const dim of DIMS) {
    const list = (arms[dim] || []).filter(a => a.status !== 'retired');
    if (list.length <= 2) continue;
    const post = posteriors(list, obs, dim);
    const tried = list.filter(a => post.get(a.id).uses >= 2);
    if (!tried.length) continue;
    const best = tried.reduce((b, a) => (post.get(a.id).mean > post.get(b.id).mean ? a : b));
    const pb = post.get(best.id);
    const cands = list.filter(a => a.id !== best.id)
      .map(a => ({ a, p: post.get(a.id) }))
      .filter(({ p }) => p.uses >= MIN_RETIRE_USES && p.mean < -0.25)
      .map(x => ({ ...x, z: (pb.mean - x.p.mean) / Math.sqrt(pb.sd ** 2 + x.p.sd ** 2) }))
      .filter(x => x.z > RETIRE_Z)
      .sort((x, y) => y.z - x.z);
    for (const c of cands) {
      if (out.length >= maxRetire) break;
      const active = list.filter(a => !out.some(o => o.dim === dim && o.id === a.id));
      if (active.length <= 2) break;
      if (!c.a.llm && active.filter(a => !a.llm).length <= 1) continue;  // keep a template fallback
      out.push({ dim, id: c.a.id, uses: c.p.uses, mean: Math.round(c.p.mean * 100) / 100, best: best.id, z: Math.round(c.z * 100) / 100 });
    }
  }
  return out.slice(0, maxRetire);
}

// ─── 3. propose ──────────────────────────────────────────────────

function armText(a) {
  if (a.llm) return `LLM writes in the style: "${a.guide || ''}"${a.fallback ? ` (template fallback: ${a.fallback})` : ''}`;
  if (a.fromTemplates) return 'picks one of the format\'s own title templates of this style';
  if (a.pool) return `emoji: ${a.pool.join(' | ')}`;
  if (a.keep) return "keeps the template's own emoji";
  if (a.title || a.desc) return `${a.title && a.title.length ? `title: ${a.title.map(t => `#${t}`).join(' ')}; ` : ''}description: ${(a.desc || []).map(t => `#${t}`).join(' ')}`;
  return `template: "${[].concat(a.template || a.templates || []).join('" / "')}"`;
}

function oproPrompt(pool, spec, arms, obs, samples, niche) {
  const lines = [`Channel: ${spec.channelName}`, `Content type: ${pool.split('/')[1]}. Example video: ${spec.describe(samples[0] || {})}`,
    `Scored uploads in this pool: ${obs.length}. Scores are standardized within the pool: 0 = average upload, +1 = one standard deviation better.`, '',
    'Current arms (id | status | scored uses | mean score +- uncertainty | definition):'];
  for (const dim of DIMS) {
    if (!arms[dim]) continue;
    const post = posteriors(arms[dim], obs, dim);
    lines.push(`${dim}:`);
    for (const a of arms[dim]) {
      const p = post.get(a.id);
      lines.push(`  ${a.id} | ${a.status} | ${p.uses} | ${fmt(p.mean)} +- ${p.sd.toFixed(2)} | ${armText(a)}`);
    }
  }
  const sorted = obs.filter(o => o.title).sort((a, b) => b.x - a.x);
  const row = o => `  "${o.title}"${o.onVideoTitle ? ` [on video: "${o.onVideoTitle}"]` : ''} score ${fmt(o.x)}, ${o.views} views, arms ${JSON.stringify(o.arms)}`;
  if (sorted.length) lines.push('', 'Best uploads:', ...sorted.slice(0, 5).map(row), 'Worst uploads:', ...sorted.slice(-4).reverse().map(row));
  if (niche.length) lines.push('', `What people type in YouTube search in this niche (autocomplete): ${niche.slice(0, 16).join(' | ')}`);
  const ph = placeholdersOf(pool).map(p => `{${p}}`).join(' ');
  const kw = spec.keyword(samples[0] || {});
  lines.push('',
    `Placeholders for templates: ${ph} ({e} = the emoji set, put at the end if you leave it out; {e1} = its first emoji).`,
    `Rules: titles at most ${spec.maxTitle || 70} characters with the emoji and must contain the main keyword (e.g. "${kw}"); on-video texts (ovStyle)`
      + ' at most 26 characters, Latin letters only, no emoji; hashtag sets 1-3 specific tags (lowercase letters and digits, or tag'
      + ' placeholders), at most 1 in the title, no generic tags (#viral #fyp #trending ...); emoji sets energetic only, from: '
      + `${rules.ENERGETIC_EMOJI.join(' ')}; description hooks one line with the keyword; no invented statistics, no fake claims, never sexual.`,
    'Propose up to 3 NEW arms most likely to beat the current best, each building on a pattern in the scores above (say which).'
      + (pool.startsWith('quiz/') ? ' This pool has no ovStyle dimension.' : ''),
    'JSON: {"proposals": [{"dimension": "titleStyle|ovStyle|hashtagSet|emojiSet|descHook", "id": "short-kebab-id",'
      + ' "template": "titleStyle / descHook", "templates": ["ovStyle text", "shorter fallback"], "guide": "optional, titleStyle / descHook / ovStyle:'
      + ' a style the LLM writes each video in (then the template is its example)", "title": ["{sourceTag}"], "desc": ["acapella"],'
      + ' "pool": ["🔥,🎧"], "why": "the data point it builds on", "confidence": 1-10}]}');
  return lines.join('\n');
}

const firstTemplateArm = list => (list || []).find(a => !a.llm && a.status !== 'retired');

/** Validate one proposal on real facts; returns {ok, arm} or {ok: false, why}. */
function validateProposal(pool, spec, p, arms, samples) {
  const dim = p && p.dimension;
  if (!DIMS.includes(dim) || !arms[dim]) return { ok: false, why: `no dimension "${dim}" in this pool` };
  const base = String(p.id || '').toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 22);
  if (base.length < 2) return { ok: false, why: 'bad id' };
  const id = base.startsWith('op-') ? base : `op-${base}`;
  if (arms[dim].some(a => a.id === id)) return { ok: false, why: `id ${id} exists` };
  if (arms[dim].filter(a => a.status !== 'retired').length >= MAX_ACTIVE) return { ok: false, why: `${dim} already has ${MAX_ACTIVE} active arms` };
  const allowed = new Set(placeholdersOf(pool));
  const badPh = t => rules.placeholders(t).find(k => !allowed.has(k));
  const ctxOf = s => _internal.makeCtx(spec, { ...s }, { llmOk: false, rng: () => 0 });
  const def = {};
  const guide = typeof p.guide === 'string' && p.guide.trim() ? rules.clean(p.guide).slice(0, 300) : null;
  if (guide && (rules.hasSad(guide) || /\b(sexy|nsfw|clickbait)\b/i.test(guide))) return { ok: false, why: 'guide breaks the rules' };

  const applicable = (render) => {
    let n = 0, okN = 0;
    for (const s of samples) {
      const r = render(ctxOf(s), s);
      if (r === null) continue;  // a fact the template needs is missing for this sample
      n++;
      if (r) okN++;
    }
    return { n, okN };
  };

  if (dim === 'titleStyle' || dim === 'descHook') {
    const tpl = typeof p.template === 'string' ? p.template.trim() : '';
    if (!tpl) return { ok: false, why: 'needs a template' };
    if (badPh(tpl)) return { ok: false, why: `unknown placeholder {${badPh(tpl)}}` };
    const armT = { id, template: tpl };
    const res = applicable((ctx, s) => {
      if (!rules.placeholders(tpl).every(k => k === 'e1' || (ctx.vars[k] !== null && ctx.vars[k] !== undefined))) return null;
      if (dim === 'titleStyle') return !!_internal.renderTitleArm(armT, ctx, '🔥🎧', '');
      const line = _internal.renderDescArm(armT, ctx, '🔥');
      const kw = spec.keyword(s);
      return !!line && (rules.contains(line, kw) || (ctx.vars.hook && rules.contains(line, ctx.vars.hook)));
    });
    if (!res.n || res.okN < res.n || res.n < Math.ceil(samples.length / 2)) {
      // the validator's reason on the first video, for the report
      const ctx0 = ctxOf(samples[0] || {});
      const text = rules.fill(tpl, { ...ctx0.vars, e: '🔥🎧', e1: '🔥' });
      const reasons = text === null ? ['needs facts most videos lack']
        : (dim === 'titleStyle' ? rules.validateTitle(text, ctx0.rules) : rules.validateLine(text, { maxLen: 240 })).reasons;
      return { ok: false, why: `fails on ${res.n - res.okN}/${res.n} recent videos: ${reasons.join(', ') || 'no keyword'}` };
    }
    def.template = tpl;
    if (guide) {
      const fb = firstTemplateArm(arms[dim]);
      if (!fb) return { ok: false, why: 'no template fallback in this dimension' };
      Object.assign(def, { llm: true, guide, example: tpl, fallback: fb.id });
      delete def.template;
    }
  } else if (dim === 'ovStyle') {
    const tpls = [].concat(p.templates || p.template || []).filter(t => typeof t === 'string' && t.trim()).map(t => t.trim()).slice(0, 3);
    if (!tpls.length && !guide) return { ok: false, why: 'needs templates' };
    if (tpls.some(badPh)) return { ok: false, why: 'unknown placeholder' };
    if (tpls.length) {
      const res = applicable(ctx => _internal.renderOnVideoArm({ id, templates: tpls }, ctx) ? true : false);
      if (res.okN < res.n) return { ok: false, why: `on-video text invalid on ${res.n - res.okN}/${res.n} videos (26 chars, no emoji)` };
    }
    if (guide) {
      const fb = firstTemplateArm(arms[dim]);
      if (!fb) return { ok: false, why: 'no template fallback' };
      Object.assign(def, { llm: true, guide, example: tpls[0] || '', fallback: fb.id });
    } else def.templates = tpls;
  } else if (dim === 'hashtagSet') {
    const norm = xs => (Array.isArray(xs) ? xs : []).map(t => String(t).trim().replace(/^#/, '')).filter(Boolean);
    const title = norm(p.title).slice(0, 2);
    const desc = norm(p.desc);
    const all = [...title, ...desc];
    if (!all.length || all.length > 3 || title.length > 1) return { ok: false, why: '1-3 hashtags, at most 1 in the title' };
    for (const t of all) {
      const ph = t.match(/^\{(\w+)\}$/);
      if (ph ? !allowed.has(ph[1]) || !/Tag$|^fmt\d$/.test(ph[1]) : !rules.validTag(t)) return { ok: false, why: `bad hashtag "${t}"` };
    }
    const res = applicable(ctx => !!_internal.renderHashtags({ title, desc }, ctx.vars));
    if (res.okN < Math.ceil(res.n / 2)) return { ok: false, why: 'the set does not apply to most videos' };
    Object.assign(def, { title, desc });
  } else if (dim === 'emojiSet') {
    const pool0 = (Array.isArray(p.pool) ? p.pool : []).map(x => String(x).replace(/\s+/g, '')).filter(Boolean).slice(0, 4);
    if (!pool0.length) return { ok: false, why: 'empty emoji set' };
    for (const combo of pool0) {
      const es = combo.split(',').filter(Boolean);
      if (!es.length || es.length > 2 || !es.every(rules.validEmoji)) return { ok: false, why: `emoji "${combo}" not in the energetic list` };
    }
    def.pool = pool0;
  }
  const conf = Math.max(1, Math.min(10, Number(p.confidence) || 5));
  return { ok: true, arm: { dim, id, def, mu0: Math.round((0.2 + 0.3 * (conf - 1) / 9) * 100) / 100, n0: 1,
    why: rules.clean(String(p.why || '')).slice(0, 200) } };
}

async function propose(pool, spec, arms, obs, maxAdd = MAX_ADD) {
  if (!llm.available()) return { accepted: [], rejected: [], note: 'LLM unavailable: no proposals this week' };
  const samples = samplesFor(pool);
  let niche = [];
  try { niche = await suggestMany(NICHE_SEEDS[pool] || NICHE_SEEDS.edit); } catch {}
  const sys = `${EXPERT_SYSTEM}\nYou also run this channel's packaging experiments: a bandit tries metadata "arms" on every upload and scores them `
    + 'by how the videos did. Propose new arms that could beat the current best, learning from what scored well and badly. Be specific to '
    + 'this niche and do not repeat an existing arm.';
  const r = await llm.chatJSON(sys, oproPrompt(pool, spec, arms, obs, samples, niche), { timeoutMs: 90000, temperature: 1.0, maxTokens: 3000 });
  const props = r && Array.isArray(r.proposals) ? r.proposals : [];
  if (!props.length) return { accepted: [], rejected: [], note: r ? 'the LLM proposed nothing' : 'LLM call failed: no proposals this week' };
  const accepted = [];
  const rejected = [];
  for (const p of props.slice(0, 10)) {
    if (accepted.length >= maxAdd) break;
    const v = validateProposal(pool, spec, p, arms, samples);
    if (v.ok) {
      accepted.push(v.arm);
      arms[v.arm.dim].push({ id: v.arm.id, status: 'active', ...v.arm.def });  // later proposals see it (no duplicates)
    } else rejected.push({ dim: p && p.dimension, id: p && p.id, why: v.why });
  }
  return { accepted, rejected, note: null };
}

// ─── the weekly step ─────────────────────────────────────────────

/**
 * Run the weekly step for a channel ('meme' or 'quiz'); returns {lines (markdown), tablesText, changes}.
 * opts: {week, dryRun, maxAdd, maxRetire}
 */
async function weeklyMetadataStep({ channel, week = isoWeek(), dryRun = false, maxAdd = MAX_ADD, maxRetire = MAX_RETIRE } = {}) {
  const state = loadState();
  state.log = state.log || [];
  const lines = ['## Metadata: title styles, on-video text, hashtags, emoji, description hooks', '',
    'Each dimension is a bandit (core/metadata): an arm\'s score is its uploads\' reward standardized within the pool '
    + '(0 = average upload), with an uncertainty; 25% of picks explore. Weekly: clear losers (>= 4 uses) retire, the LLM '
    + 'proposes up to 3 new arms (validated, small optimistic prior).', ''];
  let tablesText = '';
  const changes = [];
  for (const pool of poolsOf(channel, state)) {
    const spec = specFor(pool);
    const arms = armsFor(pool, state);
    const obs = observations(pool);
    const done = state.log.some(l => l.week === week && l.pool === pool);
    let retired = [];
    let prop = { accepted: [], rejected: [], note: null };
    if (!done && !dryRun) {
      retired = retireLosers(arms, obs, maxRetire);
      // proposals must not lean on an arm retired a moment ago (e.g. as their template fallback)
      for (const r of retired) { const a = (arms[r.dim] || []).find(x => x.id === r.id); if (a) a.status = 'retired'; }
      prop = obs.length >= MIN_OBS_FOR_PROPOSALS()
        ? await propose(pool, spec, arms, obs, maxAdd)
        : { accepted: [], rejected: [], note: `${obs.length} scored uploads: proposals start at ${MIN_OBS_FOR_PROPOSALS()}` };
      const ps = state.pools[pool] || (state.pools[pool] = { arms: {} });
      ps.arms = ps.arms || {};
      for (const r of retired) {
        const d = ps.arms[r.dim] || (ps.arms[r.dim] = {});
        d[r.id] = { ...(d[r.id] || {}), status: 'retired', retiredAt: week, why: `mean ${fmt(r.mean)} after ${r.uses} uses vs ${r.best} (z ${r.z})` };
      }
      for (const a of prop.accepted) {
        const d = ps.arms[a.dim] || (ps.arms[a.dim] = {});
        d[a.id] = { status: 'active', source: `opro-${week}`, added: week, mu0: a.mu0, n0: a.n0, def: a.def, why: a.why };
      }
      state.log.push({ week, pool, at: new Date().toISOString(), n: obs.length, retired, added: prop.accepted.map(a => ({ dim: a.dim, id: a.id, mu0: a.mu0 })),
        rejected: prop.rejected.slice(0, 6), note: prop.note || undefined });
      changes.push({ pool, retired, added: prop.accepted, rejected: prop.rejected });
    }
    // table (after this week's changes)
    const now = armsFor(pool, state);
    const t = [`### ${pool} (${obs.length} scored uploads)`, '', '| dimension | arm | uses | mean | ± | status |', '|---|---|---|---|---|---|'];
    const snap = {};
    for (const dim of DIMS) {
      if (!now[dim]) continue;
      const post = posteriors(now[dim], obs, dim);
      snap[dim] = {};
      for (const a of [...now[dim]].sort((x, y) => post.get(y.id).mean - post.get(x.id).mean)) {
        const p = post.get(a.id);
        snap[dim][a.id] = { uses: p.uses, mean: Math.round(p.mean * 100) / 100, sd: Math.round(p.sd * 100) / 100 };
        t.push(`| ${dim} | ${a.id}${a.source && a.source !== 'seed' ? ' (new)' : ''} | ${p.uses} | ${fmt(p.mean)} | ${p.sd.toFixed(2)} | ${a.status} |`);
      }
    }
    lines.push(...t, '');
    tablesText += t.join('\n') + '\n';
    const entry = state.log.filter(l => l.pool === pool && l.week === week).pop();
    if (entry) {
      const bits = [];
      for (const r of entry.retired || []) bits.push(`retired \`${r.id}\` (${r.dim}, ${r.uses} uses, mean ${fmt(r.mean)})`);
      for (const a of entry.added || []) {
        const full = ((state.pools[pool].arms || {})[a.dim] || {})[a.id] || {};
        bits.push(`added \`${a.id}\` (${a.dim}, prior ${fmt(a.mu0)}): ${armText({ ...full.def })}${full.why ? ` — ${full.why}` : ''}`);
      }
      lines.push(`Changes this week: ${bits.length ? bits.join('; ') : 'none'}${entry.note ? ` (${entry.note})` : ''}.`);
      if ((entry.rejected || []).length) lines.push(`Rejected proposals: ${entry.rejected.map(r => `${r.id || '?'} (${r.why})`).join('; ')}.`);
      lines.push('');
    }
    if (!dryRun) {
      const ps = state.pools[pool] || (state.pools[pool] = { arms: {} });
      ps.posterior = { week, n: obs.length, dims: snap };
    }
  }
  if (state.log.length > 80) state.log = state.log.slice(-80);
  if (!dryRun) saveState(state);
  return { lines, tablesText, changes };
}

if (require.main === module) {
  const channel = (process.argv.find(a => a.startsWith('--channel=')) || '').split('=')[1] || process.argv[process.argv.indexOf('--channel') + 1] || 'meme';
  weeklyMetadataStep({ channel, dryRun: process.argv.includes('--dry-run') })
    .then(r => { console.log(r.lines.join('\n')); process.exit(0); })
    .catch(e => { console.error(e); process.exit(1); });
}

module.exports = { weeklyMetadataStep, retireLosers, validateProposal, oproPrompt, poolsOf, isoWeek, MAX_ADD, MAX_RETIRE };
