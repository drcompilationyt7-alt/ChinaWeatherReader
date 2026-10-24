#!/usr/bin/env node
/**
 * Shorts title style selftest (core/metadata/title-style.js): generates titles for real plans of each channel with the
 * template paths (no LLM, no autocomplete calls) and checks every one against the channel style.
 *
 *   node scripts/title-style-selftest.js [--show N] [--json out.json]
 *
 *   Zero Yen Otaku   50 generations from the real uploads in memory/meme-history.json (cycled, different seeds)
 *   Asian Pop Quiz   the real uploads in memory/quiz-channel/quiz-history.json + themed editions, up to 50
 *   Mainland Unpacked the real week scripts in content/china/week-* (old titles, expected to fail) + their rewrites
 * Exit code 1 when a generated (new) title breaks the style.
 */
process.env.META_LLM = 'off';
process.env.META_SUGGEST = 'off';
process.env.POP_NEW_FORMATS = process.env.POP_NEW_FORMATS || '1';   // eyes / outfit / silhouette + editions
const fs = require('fs');
const path = require('path');
const ROOT = path.resolve(__dirname, '..');
const ts = require('../core/metadata/title-style');
const metadata = require('../core/metadata');
const readJson = (f, d) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return d; } };
const args = process.argv.slice(2);
const SHOW = Number((args[args.indexOf('--show') + 1]) || 10);
const OUT = args.includes('--json') ? args[args.indexOf('--json') + 1] : null;
const rngFrom = seed => () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
const L = s => [...String(s || '')].length;

const results = { meme: [], quiz: [], china: [] };

async function meme() {
  const ups = (readJson(path.join(ROOT, 'memory', 'meme-history.json'), { uploads: [] }).uploads || []).filter(u => u.title && u.song);
  const KW = { impact: 'anime sound design edit', crisp: 'anime sound design edit', summer_glow: 'anime summer slow motion edit',
    dreamy_bloom: 'anime sakura slow motion edit' };
  for (let i = 0; i < 50; i++) {
    const u = ups[(ups.length - 1 - i % ups.length)];
    const ct = u.contentType || 'stem-edit';
    const facts = ct === 'stem-edit'
      ? { song: String(u.song).toLowerCase(), source: u.clipSource || u.source || null, theme: u.theme || 'anime' }
      : { keyword: KW[u.vibe] || null, vibe: u.vibe || null, song: u.song, source: null, theme: 'anime' };
    const m = await metadata.optimizeMetadata({ channel: 'meme', contentType: ct, facts, llm: false, rng: rngFrom(1000 + i) });
    const sn = require('../core/metadata/catalog').songName(facts);
    const v = ts.check(m.title, 'meme', { front: [sn], require: [sn] });   // the song name is always in the title
    results.meme.push({ plan: `${ct} ${u.song}`, old: u.title, title: m.title, how: m.arms.titleStyle, ok: v.ok, why: v.reasons });
  }
}

async function quiz() {
  const wq = require('../pipeline/world-quiz-pipeline');
  const ups = (readJson(path.join(ROOT, 'memory', 'quiz-channel', 'quiz-history.json'), { uploads: [] }).uploads || []).filter(u => u.format);
  const themes = readJson(path.join(ROOT, 'core', 'quiz', 'themes.json'), { themes: {} }).themes;
  const round = (j, fmt) => ({ answer: `Answer${j}`, name: `Name ${j}`, title: `Song ${j}`, artist: 'Artist', difficulty: j === 2 ? 4 : 2,
    question: `Question ${j}?`, options: ['a', 'b'], a: { name: ['Gojo', 'Luffy', 'Levi'][j % 3] }, b: { name: ['Sukuna', 'Zoro', 'Eren'][j % 3] },
    left: fmt === 'bigger' || fmt === 'crowd' ? { name: 'Japan' } : undefined, right: fmt === 'bigger' || fmt === 'crowd' ? { name: 'Spain' } : undefined });
  const plans = ups.map(u => ({ plan: { format: u.format, theme: u.theme, topic: u.theme, rounds: Array.from({ length: (u.answers || []).length || 3 }, (_, j) => round(j, u.format)) }, old: u.title }));
  const edFormats = ['eyes', 'character', 'outfit', 'silhouette', 'scene', 'character', 'eyes', 'outfit', 'voice', 'opening'];
  const edKeys = Object.keys(themes);
  for (let i = 0; plans.length < 50 && i < edFormats.length; i++) {
    const ed = themes[edKeys[i % edKeys.length]];
    plans.push({ plan: { format: edFormats[i], theme: 'anime', topic: 'anime', edition: { ...ed, key: edKeys[i % edKeys.length] },
      rounds: [0, 1, 2].map(j => round(j, edFormats[i])) }, old: null });
  }
  for (const [i, { plan, old }] of plans.slice(0, 50).entries()) {
    let title = null;
    let how = '';
    try {
      const options = wq.titleOptions(plan).filter(t => { try { return !!t.text(plan); } catch { return false; } });
      const facts = wq.quizFacts(plan);
      const tpl = options[Math.floor(rngFrom(7 + i)() * options.length)];
      const meta = await metadata.optimizeMetadata({ channel: 'quiz', contentType: plan.format, llm: false, rng: rngFrom(500 + i),
        facts: { ...facts, templates: options.map(t => ({ id: t.id, style: t.style || metadata.quizStyleOf(t.id), text: t.text(plan) })), templateId: tpl.id },
        style: { titleStyle: tpl.style || metadata.quizStyleOf(tpl.id) } });
      title = wq.fitQuizTitle(meta.title, plan);
      how = `${tpl.id}${plan.edition ? ` + ${plan.edition.key} edition` : ''}`;
    } catch (e) { how = `error: ${e.message}`; }
    const f = (() => { try { return wq.quizFacts(plan); } catch { return {}; } })();
    const front = [].concat(f.any || []).filter(a => !/^(otakus?|fans?)$/i.test(a));
    const v = title ? ts.check(title, 'quiz', { front }) : { ok: false, reasons: [how] };
    results.quiz.push({ plan: `${plan.format}/${plan.topic}${plan.edition ? ` (${plan.edition.key})` : ''}`, old, title, how, ok: v.ok, why: v.reasons });
  }
}

function china() {
  // the rewrites follow content/china/TOPICS.md (title rules); old = what was posted
  const NEW = {
    '1-byd-flash-charge': ['China\'s 5-Minute EV Charge Is Real (Almost)'],
    '2-huajiang-bridge': ['China Just Opened the World\'s Highest Bridge'],
    '3-996-work-culture': ['996: China\'s Brutal 72-Hour Work Week'],
    '4-huawei-mate-60': ['Huawei Is Now Building Chips in 3D', 'Huawei'],
    '5-mini-sky-city': ['China Built a 57-Floor Tower in 19 Days'],
    '6-gaokao': ['Gaokao: The Exam That Stops All of China', 'gaokao'],
    '7-shenzhen': ['Shenzhen: Border Town to 18 Million People'],
  };
  const dirs = fs.readdirSync(path.join(ROOT, 'content', 'china')).filter(d => d.startsWith('week-'));
  for (const d of dirs) {
    for (const f of fs.readdirSync(path.join(ROOT, 'content', 'china', d)).filter(x => x.endsWith('.json'))) {
      const spec = readJson(path.join(ROOT, 'content', 'china', d, f), {});
      if (!spec.title) continue;
      const slug = f.replace(/\.json$/, '');
      const oldV = ts.check(spec.title, 'china', { front: spec.keyword ? [spec.keyword] : [] });
      const [title, keyword] = NEW[slug] || [ts.dropShortsTag(spec.title)];
      const v = ts.check(title, 'china', { front: keyword ? [keyword] : [] });
      results.china.push({ plan: slug, old: spec.title, oldOk: oldV.ok, oldWhy: oldV.reasons, title, keyword: keyword || null, ok: v.ok, why: v.reasons });
    }
  }
}

(async () => {
  await meme();
  await quiz();
  china();
  let bad = 0;
  for (const [ch, rows] of Object.entries(results)) {
    const ok = rows.filter(r => r.ok).length;
    const lens = rows.filter(r => r.title).map(r => L(r.title)).sort((a, b) => a - b);
    bad += rows.length - ok;
    console.log(`\n== ${ch}: ${ok}/${rows.length} pass; title length median ${lens[Math.floor(lens.length / 2)]}, max ${lens[lens.length - 1]}`);
    for (const r of rows.filter(x => !x.ok)) console.log(`   FAIL ${r.plan}: "${r.title}" (${r.why.join('; ')})`);
    for (const r of rows.slice(0, SHOW)) console.log(`   ${r.plan.padEnd(30).slice(0, 30)} ${String(L(r.old)).padStart(3)} "${r.old}"\n   ${''.padEnd(30)} ${String(L(r.title)).padStart(3)} "${r.title}"  [${r.how || r.keyword || ''}]`);
  }
  if (OUT) fs.writeFileSync(OUT, JSON.stringify(results, null, 1));
  console.log(`\n${bad ? `${bad} title(s) break the style` : 'all generated titles pass the style'}`);
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
