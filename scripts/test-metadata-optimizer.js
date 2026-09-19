#!/usr/bin/env node
/**
 * Tests for the metadata optimizer (core/metadata), on a throwaway MEMORY_DIR (the real memory is
 * never touched):
 *   - the packaging rules (lengths, keyword, no invented stats, no sad emoji, no emoji on video ...)
 *   - the learner with simulated rewards: converges to the better arm and keeps exploring,
 *     follows a change (older results decay)
 *   - the file-backed loop: choices + rewards -> the learner prefers the arm that earned more
 *   - the weekly step with a fake LLM: invalid proposals rejected, at most 3 added with a bounded
 *     prior, a clear loser retired, a second run in the same week changes nothing
 *   - example metadata for 5 meme songs and 5 quiz formats, without the LLM (and with it: --llm)
 *
 *   node scripts/test-metadata-optimizer.js [--llm] [--offline]
 */
const os = require('os');
const fs = require('fs');
const path = require('path');

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'metadata-test-'));
process.env.MEMORY_DIR = TMP;  // before anything reads it
const WITH_LLM = process.argv.includes('--llm');
if (process.argv.includes('--offline')) process.env.META_SUGGEST = 'off';

const ROOT = path.join(__dirname, '..');
const rules = require(path.join(ROOT, 'core/metadata/rules'));
const learner = require(path.join(ROOT, 'core/metadata/learner'));
const metadata = require(path.join(ROOT, 'core/metadata'));
const evolve = require(path.join(ROOT, 'core/metadata/evolve'));
const llm = require(path.join(ROOT, 'core/metadata/llm'));
const { SONGS } = require(path.join(ROOT, 'pipeline/meme-pipeline'));
const quiz = require(path.join(ROOT, 'pipeline/world-quiz-pipeline'));

let pass = 0;
let fail = 0;
function ok(cond, name, extra = '') {
  if (cond) pass++;
  else { fail++; console.log(`  FAIL ${name}${extra ? `: ${extra}` : ''}`); }
}
const section = s => console.log(`\n=== ${s} ===`);
const writeJson = (f, d) => fs.writeFileSync(path.join(TMP, f), JSON.stringify(d, null, 1));
const clearMem = () => { for (const f of fs.readdirSync(TMP)) fs.rmSync(path.join(TMP, f), { recursive: true, force: true }); };

// ─── 1. rules ────────────────────────────────────────────────────
function testRules() {
  section('rules');
  const must = [['kick back'], ['acapella', 'a cappella']];
  ok(rules.validateTitle('kick back acapella 🔥🎧', { must }).ok, 'plain keyword title passes');
  ok(rules.validateTitle('kick back acapella (chainsaw man edit) 🔥 #chainsawman', { must }).ok, 'title with one hashtag passes');
  ok(!rules.validateTitle('kick back acapella 😭', { must }).ok, 'sad emoji rejected');
  ok(!rules.validateTitle('99% of people fail this kick back acapella', { must }).ok, 'invented statistic rejected');
  ok(!rules.validateTitle('only 1 in 100 get this kick back acapella', { must }).ok, '"only 1 in 100" rejected');
  ok(!rules.validateTitle('Flag Quiz: Easy to IMPOSSIBLE 🔥', {}).ok, 'IMPOSSIBLE without a hard round rejected');
  ok(rules.validateTitle('Flag Quiz: Easy to IMPOSSIBLE 🔥', { allowImpossible: true }).ok, 'IMPOSSIBLE with a hard round allowed');
  ok(!rules.validateTitle('x'.repeat(71), {}).ok, '71 characters rejected');
  ok(!rules.validateTitle('kick back <acapella>', { must }).ok, '< > rejected');
  ok(!rules.validateTitle('chainsaw man edit 🔥', { must }).ok, 'missing core keyword rejected');
  ok(!rules.validateTitle('sexy kick back acapella', { must }).ok, 'sexual word rejected');
  ok(!rules.validateTitle('kick back acapella #a1 #b2 #c3 #d4', { must }).ok, '4 hashtags rejected');
  ok(!rules.validateTitle('kick back acapella #fyp', { must }).ok, 'generic hashtag rejected');
  ok(!rules.validateTitle('Guess the Flag: Japan edition', { forbidden: ['Japan'] }).ok, 'answer in the title rejected');
  ok(rules.validateOnVideo('kick back acapella').ok, 'on-video 18 chars passes');
  ok(!rules.validateOnVideo('kick back acapella 🔥').ok, 'emoji on video rejected (drawn as squares)');
  ok(!rules.validateOnVideo('bling bang bang born acapella').ok, 'on-video over 26 chars rejected');
  ok(!rules.validateOnVideo('推しの子 acapella').ok, 'CJK on video rejected (the font cannot draw it)');
  ok(rules.validateOnVideo('apt x rosé').ok, 'accented Latin on video passes');
  ok(rules.unsad('guess the vtuber 😭✌️') === 'guess the vtuber 🔥✌️', 'unsad swaps the crying face');
  ok(rules.fill('{song} acapella[[ ({source} edit)]] {e}', { song: 'idol', e: '🔥' }) === 'idol acapella 🔥', 'optional segment dropped');
  ok(rules.fill('{song} x {source}', { song: 'idol' }) === null, 'missing fact -> not eligible');
  ok(rules.slugTag('ROSÉ') === 'rose' && rules.slugTag('Steins;Gate') === 'steinsgate', 'hashtag slugs');
}

// ─── 2. quiz templates ───────────────────────────────────────────
function testTemplates() {
  section('quiz templates');
  const styles = new Set(['keyword', 'speed', 'challenge', 'fans', 'casual', 'ramp']);
  const all = Object.values(quiz.TITLE_TEMPLATES).flat();
  ok(all.length > 90, 'all templates present', String(all.length));
  ok(all.every(t => styles.has(t.style)), 'every template has a known style', all.filter(t => !styles.has(t.style)).map(t => t.id).join(','));
  const src = fs.readFileSync(path.join(ROOT, 'pipeline/world-quiz-pipeline.js'), 'utf8') + fs.readFileSync(path.join(ROOT, 'pipeline/pop-quiz-meta.js'), 'utf8');
  ok(!rules.hasSad(src), 'no sad / crying emoji in the quiz title code');
  ok(!rules.hasSad(fs.readFileSync(path.join(ROOT, 'core/metadata/catalog.js'), 'utf8')), 'no sad emoji in the metadata catalog');
}

// ─── 3. learner with simulated rewards ───────────────────────────
function simulate(seed, means, rounds, change = null) {
  const rng = learner.rngFrom(seed);
  const arms = means.map((m, i) => ({ id: `a${i}`, mu0: 0, n0: 1, status: 'active' }));
  const obs = [];
  const picks = [];
  for (let t = 0; t < rounds; t++) {
    const m = change && t >= change.at ? change.means : means;
    for (const o of obs) o.ageDays += 1;  // one upload a day
    const r = learner.choose(arms, obs, 'titleStyle', { rng, explore: 0.25 });
    const i = Number(r.arm.id.slice(1));
    picks.push(i);
    obs.push({ arms: { titleStyle: r.arm.id }, x: m[i] + learner.gauss(rng), ageDays: 0, final: true });
  }
  return { picks, post: learner.posteriors(arms, obs, 'titleStyle') };
}

function testLearner() {
  section('learner (simulated rewards)');
  const means = [0.6, 0.0, -0.2, -0.6];
  const seeds = 30;
  let bestLate = 0, minShare = 1, bestTop = 0;
  const shares = [0, 0, 0, 0];
  for (let s = 1; s <= seeds; s++) {
    const { picks, post } = simulate(s, means, 250);
    const late = picks.slice(-100);
    bestLate += late.filter(i => i === 0).length / late.length;
    for (let i = 0; i < 4; i++) {
      const sh = picks.filter(p => p === i).length / picks.length;
      shares[i] += sh / seeds;
      minShare = Math.min(minShare, sh);
    }
    const top = [...post.entries()].sort((a, b) => b[1].mean - a[1].mean)[0][0];
    if (top === 'a0') bestTop++;
  }
  bestLate /= seeds;
  console.log(`  true means ${means.join(' / ')}: share of picks ${shares.map(x => `${(100 * x).toFixed(0)}%`).join(' / ')}; `
    + `best arm in the last 100 rounds ${(100 * bestLate).toFixed(0)}%; lowest share of any arm ${(100 * minShare).toFixed(1)}%; `
    + `best arm ranked first ${bestTop}/${seeds}`);
  ok(bestLate > 0.6, 'converges to the better arm (>60% of late picks)', bestLate.toFixed(2));
  ok(minShare >= 0.04, 'keeps exploring every arm (>=4% of picks each)', minShare.toFixed(3));
  ok(bestTop >= seeds * 0.8, 'the best arm has the best posterior in >=80% of runs', String(bestTop));

  // the channel changes: after day 120 the second arm becomes the best; decay lets the learner follow
  let follow = 0;
  for (let s = 1; s <= seeds; s++) {
    const { picks } = simulate(100 + s, [0.5, 0, -0.3], 300, { at: 120, means: [-0.3, 0.6, -0.3] });
    follow += picks.slice(-60).filter(i => i === 1).length / 60 / seeds;
  }
  console.log(`  after a change on day 120, the new best arm gets ${(100 * follow).toFixed(0)}% of the last 60 picks`);
  ok(follow > 0.45, 'follows a change (decay + exploration)', follow.toFixed(2));

  // eligibility: an arm that does not fit the video is never picked
  const rng = learner.rngFrom(7);
  const arms = [{ id: 'x', status: 'active' }, { id: 'y', status: 'active' }, { id: 'z', status: 'retired' }];
  let bad = 0;
  for (let i = 0; i < 200; i++) { const r = learner.choose(arms, [], 'titleStyle', { rng, eligible: a => a.id !== 'x' }); if (r.arm.id !== 'y') bad++; }
  ok(bad === 0, 'ineligible and retired arms are never picked');
}

// ─── 4. the file-backed loop ─────────────────────────────────────
async function testLoop() {
  section('file-backed loop (choices + rewards -> picks)');
  clearMem();
  process.env.META_LLM = 'off';
  process.env.META_SUGGEST = 'off';
  const rng = learner.rngFrom(42);
  const videos = [];
  const styles = ['kw-emoji', 'kw-source', 'kw-emoji', 'kw-source', 'kw-emoji', 'kw-source'];
  for (let i = 0; i < 36; i++) {
    const style = styles[i % styles.length];
    const vid = `vid${String(i).padStart(8, '0')}`.slice(0, 11);
    metadata.recordMetadataChoice({ pool: 'meme/stem-edit', arms: { contentType: 'stem-edit', titleStyle: style, hashtagSet: i % 2 ? 'niche3' : 'minimal',
      emojiSet: 'fire', descHook: 'layers', ovStyle: 'ov-kw' }, title: `song${i} acapella 🔥`, facts: { song: `song${i}`, source: 'Chainsaw Man', theme: 'anime' } },
    { videoId: vid });
    // kw-source earns clearly more
    const reward = (style === 'kw-source' ? 1.0 : 0) + (learner.gauss(rng) * 0.5);
    videos.push({ videoId: vid, views: 500, ageDays: 3 + (35 - i), lscore: 3, reward, rewardFinal: true });
  }
  writeJson('performance-stats.json', { videos });
  const pool = metadata.describePool('meme', 'stem-edit');
  const ts = Object.fromEntries(pool.arms.titleStyle.map(a => [a.id, a]));
  console.log(`  ${pool.n} scored uploads; titleStyle means: ${pool.arms.titleStyle.map(a => `${a.id} ${a.mean.toFixed(2)} (${a.uses})`).join(', ')}`);
  ok(ts['kw-source'].mean > ts['kw-emoji'].mean + 0.5, 'posterior prefers the arm that earned more');
  const counts = {};
  for (let i = 0; i < 200; i++) {
    const m = await metadata.optimizeMetadata({ channel: 'meme', contentType: 'stem-edit', rng,
      facts: { song: 'kick back', source: 'Chainsaw Man', kind: 'anime', theme: 'anime' } });
    counts[m.arms.titleStyle] = (counts[m.arms.titleStyle] || 0) + 1;
    if (i === 0) ok(m.choice && m.choice.pool === 'meme/stem-edit', 'the choice record is ready for recordMetadataChoice');
  }
  console.log(`  200 picks (LLM and autocomplete off, so only these two title arms fit): ${JSON.stringify(counts)}`);
  ok((counts['kw-source'] || 0) > 100, 'the learner picks the better title style most of the time');
  ok((counts['kw-emoji'] || 0) >= 10, 'and still explores the other (>= 5%)');
}

// ─── 5. the weekly step ──────────────────────────────────────────
async function testWeekly() {
  section('weekly step (fake LLM)');
  clearMem();
  process.env.META_SUGGEST = 'off';
  delete process.env.META_LLM;
  const rng = learner.rngFrom(9);
  const videos = [];
  for (let i = 0; i < 24; i++) {
    const hook = ['layers', 'drop', 'question', 'stems'][i % 4];
    const vid = `wk${String(i).padStart(9, '0')}`;
    // every hook appears with both title styles (as with independent picks), so only the hook differs
    metadata.recordMetadataChoice({ pool: 'meme/stem-edit', arms: { contentType: 'stem-edit', titleStyle: Math.floor(i / 4) % 2 ? 'kw-emoji' : 'kw-source',
      hashtagSet: 'niche3', emojiSet: 'fire', descHook: hook, ovStyle: 'ov-kw' }, title: `song${i} acapella 🔥`,
    facts: { song: ['kick back', 'idol', 'specialz', 'gurenge'][i % 4], source: ['Chainsaw Man', 'Oshi no Ko', 'Jujutsu Kaisen', 'Demon Slayer'][i % 4], theme: 'anime', kind: 'anime' } },
    { videoId: vid });
    // the 'question' hook does clearly worse
    videos.push({ videoId: vid, views: 300, ageDays: 3 + (24 - i) / 2, lscore: 3, reward: (hook === 'question' ? -2 : 0.3) + learner.gauss(rng) * 0.3, rewardFinal: true });
  }
  writeJson('performance-stats.json', { videos });
  let prompt = '';
  llm.setLLM(async (sys, user) => {
    prompt = user;
    return { proposals: [
      { dimension: 'titleStyle', id: 'stems-reveal', template: '{song} acapella but you hear every stem {e}', why: 'keyword-first titles lead', confidence: 8 },
      { dimension: 'titleStyle', id: 'sad', template: '{song} acapella 😭', confidence: 9 },
      { dimension: 'titleStyle', id: 'fake-stat', template: '99% cannot handle this {song} acapella {e}', confidence: 9 },
      { dimension: 'titleStyle', id: 'no-keyword', template: 'wait for it {e}', confidence: 9 },
      { dimension: 'hashtagSet', id: 'too-many', desc: ['acapella', '{sourceTag}', 'anime', 'amv'], confidence: 5 },
      { dimension: 'hashtagSet', id: 'generic', desc: ['viral', '{sourceTag}'], confidence: 5 },
      { dimension: 'ovStyle', id: 'too-long', templates: ['this {song} acapella is way too clean'], confidence: 6 },
      { dimension: 'emojiSet', id: 'crying', pool: ['😭,🔥'], confidence: 6 },
      { dimension: 'hashtagSet', id: 'source-amv', desc: ['{sourceTag}', 'amv', 'acapella'], why: 'edit-community did well', confidence: 4 },
      { dimension: 'emojiSet', id: 'headphones', pool: ['🎧', '🎧,🔥'], confidence: 6 },
      { dimension: 'descHook', id: 'one-more', template: '{song} acapella, stem by stem {e1}', confidence: 7 },
    ] };
  });
  const week = '2026-W38';
  const r1 = await evolve.weeklyMetadataStep({ channel: 'meme', week });
  const ch = r1.changes[0] || { added: [], retired: [], rejected: [] };
  console.log(`  added: ${ch.added.map(a => `${a.dim}/${a.id} (prior ${a.mu0})`).join(', ') || 'none'}`);
  console.log(`  retired: ${ch.retired.map(a => `${a.dim}/${a.id} (${a.uses} uses, mean ${a.mean})`).join(', ') || 'none'}`);
  console.log(`  rejected: ${ch.rejected.map(a => `${a.id}: ${a.why}`).join(' | ')}`);
  ok(prompt.includes('Current arms') && prompt.includes('question'), 'the proposer sees the scored arms');
  ok(ch.added.length === 3, 'at most 3 arms added a week', String(ch.added.length));
  ok(ch.added.every(a => a.mu0 >= 0.2 && a.mu0 <= 0.5), 'new arms get a bounded optimistic prior');
  const rej = ch.rejected.map(r => r.id);
  ok(['sad', 'fake-stat', 'no-keyword', 'too-many', 'generic', 'too-long', 'crying'].every(id => rej.includes(id)), 'invalid proposals rejected', rej.join(','));
  ok(ch.retired.some(r => r.dim === 'descHook' && r.id === 'question'), 'the clear loser is retired');
  const arms = learner.armsFor('meme/stem-edit');
  ok(arms.titleStyle.some(a => a.id === 'op-stems-reveal' && a.status === 'active'), 'the new title style is an active arm');
  ok(arms.descHook.find(a => a.id === 'question').status === 'retired', 'the retired arm stays retired');
  const r2 = await evolve.weeklyMetadataStep({ channel: 'meme', week });
  ok(r2.changes.length === 0, 'a second run in the same week changes nothing');
  ok(r1.lines.join('\n').includes('| titleStyle |'), 'the report gets a metadata table');
  // the new arm renders on a real video
  const m = await metadata.optimizeMetadata({ channel: 'meme', contentType: 'stem-edit', llm: false, style: { titleStyle: 'op-stems-reveal', hashtagSet: 'op-source-amv' },
    facts: { song: 'kick back', source: 'Chainsaw Man', theme: 'anime', kind: 'anime' } });
  ok(m.arms.titleStyle === 'op-stems-reveal' && /every stem/.test(m.title), 'a new arm is used for a video', m.title);
  console.log(`  -> "${m.title}" | ${m.hashtagLine}`);

  // the LLM fails (429 / timeout): an LLM arm falls back to its template arm, and the log names the arm actually used
  llm.setLLM(async () => null);
  const facts = { song: 'kick back', source: 'Chainsaw Man', theme: 'anime', kind: 'anime' };
  const f1 = await metadata.optimizeMetadata({ channel: 'meme', contentType: 'stem-edit', facts,
    style: { titleStyle: 'llm-hook', ovStyle: 'ov-llm', descHook: 'llm-line' } });
  ok(f1.titleSource === 'template' && f1.arms.titleStyle === 'kw-emoji', 'LLM down: title from the fallback template, logged as it', f1.arms.titleStyle);
  ok(f1.arms.ovStyle === 'ov-kw' && f1.arms.descHook === 'layers', 'LLM down: on-video and description fall back too');
  // a retired fallback is skipped for the first active template arm
  const st = learner.loadState();
  st.pools['meme/stem-edit'].arms.titleStyle = { ...(st.pools['meme/stem-edit'].arms.titleStyle || {}), 'kw-emoji': { status: 'retired' } };
  learner.saveState(st);
  const f2 = await metadata.optimizeMetadata({ channel: 'meme', contentType: 'stem-edit', facts, style: { titleStyle: 'llm-hook' } });
  ok(f2.arms.titleStyle !== 'kw-emoji' && f2.titleSource === 'template', 'a retired fallback is never used', f2.arms.titleStyle);
  llm.setLLM(null);
}

// ─── 6. examples ─────────────────────────────────────────────────
const QUIZ_PLANS = [
  { format: 'flag', theme: 'world', rounds: [{ name: 'Japan', answer: 'Japan', iso2: 'JP' }, { answer: 'Brazil', iso2: 'BR' }, { answer: 'Kenya', iso2: 'KE' },
    { answer: 'Nepal', iso2: 'NP' }, { answer: 'Chad', iso2: 'TD', decoy: true }] },
  { format: 'bigger', theme: 'world', rounds: [{ left: { name: 'Greenland', iso2: 'GL' }, right: { name: 'Congo', iso2: 'CD' }, answer: 'Congo', ratio: 1.1 },
    { left: { name: 'Japan', iso2: 'JP' }, right: { name: 'Germany', iso2: 'DE' }, answer: 'Japan', ratio: 1.06 }] },
  { format: 'song', topic: 'kpop', rounds: [{ title: 'Super Shy', artist: 'NewJeans', answer: 'Super Shy', difficulty: 1 },
    { title: 'Magnetic', artist: 'ILLIT', answer: 'Magnetic', difficulty: 2 }, { title: 'Supernova', artist: 'aespa', answer: 'Supernova', difficulty: 3 }] },
  { format: 'character', topic: 'anime', rounds: [{ name: 'Gojo Satoru', anime: 'Jujutsu Kaisen', answer: 'Gojo Satoru', difficulty: 1 },
    { name: 'Anya Forger', anime: 'Spy x Family', answer: 'Anya Forger', difficulty: 2 }, { name: 'Maomao', anime: 'The Apothecary Diaries', answer: 'Maomao', difficulty: 2 }] },
  { format: 'wyr', topic: 'anime', rounds: [{ a: "Gojo's Infinity", b: 'the Sharingan', pick: 'a' }, { a: 'Live in Konoha', b: 'Live in the Soul Society', pick: 'b' }] },
];
const MEME_SONGS = ['kick back', 'bling bang bang born', 'apt', 'hana ni natte', 'specialz'];

function show(label, m, extra = '') {
  console.log(`\n  [${label}]${extra}`);
  console.log(`    title:     ${m.title}  (${[...m.title].length} chars, ${m.titleSource})`);
  if (m.onVideoTitle !== undefined) console.log(`    on video:  ${m.onVideoTitle}  + emoji images ${m.emoji}`);
  console.log(`    hashtags:  ${m.hashtags.join(' ')}${m.titleHashtags.length ? ' (first in the title)' : ''}`);
  console.log(`    desc:      ${m.descriptionHead.replace(/\n+/g, ' / ')}`);
  console.log(`    comment:   ${m.comment}`);
  console.log(`    tags:      ${m.tags.slice(0, 8).join(', ')}${m.tags.length > 8 ? ', ...' : ''}`);
  console.log(`    arms:      ${Object.entries(m.arms).filter(([k]) => k !== 'contentType').map(([k, v]) => `${k}=${v}${m.modes[k] ? `/${m.modes[k]}` : ''}`).join(' ')}  llm: ${m.llm}`);
  const bad = (m.candidates.titles || []).filter(c => !c.ok);
  if (bad.length) console.log(`    rejected LLM titles: ${bad.map(c => `"${c.text}" (${c.why.join(', ')})`).join('; ')}`);
}

async function examples(useLLM) {
  section(`examples ${useLLM ? 'WITH the LLM (LLM arms forced so every example uses it)' : 'without the LLM (templates)'}`);
  clearMem();
  if (useLLM) { delete process.env.META_LLM; llm.reset(); } else process.env.META_LLM = 'off';
  if (!process.argv.includes('--offline')) delete process.env.META_SUGGEST;
  let i = 0;
  for (const name of MEME_SONGS) {
    const s = SONGS.find(x => x.name === name);
    const style = useLLM ? [{ titleStyle: 'llm-hook', ovStyle: 'ov-llm', descHook: 'llm-line' }, { titleStyle: 'llm-free', ovStyle: 'ov-llm' }][i++ % 2] : {};
    const m = await metadata.optimizeMetadata({ channel: 'meme', contentType: 'stem-edit', style,
      facts: { song: s.name, source: s.source, kind: s.kind, theme: s.theme, query: s.query, aliases: s.aliases } });
    show(`meme ${s.name} (${s.source})`, m);
    ok(rules.validateTitle(m.title, { must: [[s.name], ['acapella']] }).ok, `meme title valid: ${m.title}`);
    ok(!m.onVideoTitle || rules.validateOnVideo(m.onVideoTitle).ok, `on-video title valid: ${m.onVideoTitle}`);
    ok(!rules.hasSad(m.title + m.description + m.comment + m.emoji), 'no sad emoji anywhere');
    ok(m.hashtags.length >= 1 && m.hashtags.length <= 3, '1-3 hashtags');
  }
  // the API the new edit pipelines will call
  const ce = await metadata.optimizeMetadata({ channel: 'meme', contentType: 'character-edit',
    style: useLLM ? { titleStyle: 'llm-hook', ovStyle: 'ov-llm' } : {},
    facts: { character: 'Gojo', source: 'Jujutsu Kaisen', song: 'specialz', artist: 'King Gnu', theme: 'anime' } });
  show('character-edit Gojo (new edit type, same API)', ce);
  ok(/gojo edit/i.test(ce.title), 'character edit title has "<character> edit"');

  for (const plan of QUIZ_PLANS) {
    const tpl = useLLM ? { id: `${plan.format}-llm`, llm: true, style: 'llm' } : quiz.pickTitleTemplate(plan, null, { uploads: [] }, false);
    const m = await quiz.quizMetadata(plan, tpl, { baseTags: [] });
    show(`quiz ${plan.format}${plan.topic ? ` (${plan.topic})` : ''}`, m, `  template ${m.templateId || tpl.id}`);
    const f = quiz.quizFacts(plan);
    ok(rules.validateTitle(m.title, { allowImpossible: f.hard, forbidden: f.forbidden, maxLen: 70 }).ok, `quiz title valid: ${m.title}`);
    ok(!rules.hasSad(m.title + m.descriptionHead), 'no sad emoji in quiz metadata');
    if (!f.hard) ok(!/impossible/i.test(m.title), 'no IMPOSSIBLE without a hard round');
  }
}

(async () => {
  try {
    testRules();
    testTemplates();
    testLearner();
    await testLoop();
    await testWeekly();
    await examples(false);
    if (WITH_LLM) await examples(true);
  } catch (e) {
    fail++;
    console.error(e);
  }
  console.log(`\n${pass} passed, ${fail} failed (memory dir ${TMP})`);
  try { fs.rmSync(TMP, { recursive: true, force: true }); } catch {}
  process.exit(fail ? 1 : 0);
})();
