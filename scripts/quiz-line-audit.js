#!/usr/bin/env node
/**
 * Voice-line audit of the quiz channel (owner 2026-10-23: "make sure all voice lines are correct").
 *
 * For every format x topic x themed edition x length the rotation can produce, builds real plans (buildPopPlan with the real
 * pools, no LLM generation; geography plans from core/quiz/plan.py), runs them through the renderer's line picking without
 * rendering (core/quiz/line_check.py --dry-run) and reports:
 *   - lines the topic / format check had to drop (fixed-pool lines here = a pool bug; injected AI lines = expected),
 *   - problems left in the lines that would be spoken / shown (must be 0).
 * With --ai, every plan also carries a set of AI lines (some deliberately wrong: another topic, another edition, a wrong count)
 * to prove the check swaps them. Nothing is written to the bank or the history.
 *
 *   MEMORY_DIR=memory/quiz-channel node scripts/quiz-line-audit.js [--seeds 6] [--ai] [--out file.json]
 */
process.env.POP_NEW_FORMATS = '1';
process.env.POP_LLM_ONDEMAND = 'none';   // never generate items here
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const content = require('../pipeline/pop-quiz-content');
const learn = require('../core/learning-models');

const ROOT = path.join(__dirname, '..');
const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const SEEDS = Number(arg('--seeds', 6));
const WITH_AI = process.argv.includes('--ai');
const OUT = arg('--out', path.join(os.tmpdir(), 'quiz-line-audit.json'));

const POP = ['emoji', 'trivia', 'wyr', 'city', 'character', 'idol', 'opening', 'cityphoto', 'vtuber', 'duel', 'scene', 'voice', 'song',
  'nightcity', 'cuisine', 'silhouette', 'eyes', 'outfit'];
const GEO = ['flag', 'shape', 'capital', 'bigger', 'crowd'];
const GEO_THEMES = ['world', 'asia', 'europe', 'africa', 'americas'];
const themes = content.loadThemes().themes;

// AI lines a model could write: a correct one and wrong ones per slot (the check must keep only fitting ones)
const AI_SETS = [
  { intro: ['Guess the anime from these emojis!', 'Ready?'], outro: ['Drop your score below!', 'Score below!'], reveal: ['There it is, {a}!', '{a}!'] },
  { intro: ['Guess the K-pop song from these emojis!', 'Name that song!'], outro: ['How many idols did you get?', 'Score?'], reveal: ['Easy song, {a}!', '{a}!'] },
  { intro: ['Halloween night, guess who is in costume!', 'Spooky time!'], outro: ['Five out of five? Comment below!', 'Comment below!'], reveal: ['That city is {a}!', '{a}!'] },
  { intro: ['Real VTuber fans get these instantly!', 'Chat, ready?'], outro: ['Comment your score, foodies!', 'Comment!'], reveal: ['Nailed it, {a}!', '{a}!'] },
];

async function plansFor(fmt) {
  const out = [];
  const topics = content.TOPICS[fmt] || [null];
  const editions = [null, ...Object.keys(themes).filter(t => (themes[t].formats || []).includes(fmt))];
  const lengths = ['wyr', 'duel'].includes(fmt) ? [3, 4] : [3, 4, 5];
  for (const ed of editions) {
    for (const topic of ed ? [null] : topics) {
      for (const n of lengths) {
        for (let s = 0; s < SEEDS; s++) {
          let built;
          try { built = await content.buildPopPlan(fmt, { topic, rounds: n, edition: ed }); } catch (e) { out.push({ skip: `${fmt}/${topic}/${ed}: ${e.message}` }); break; }
          let plan = built.plan;
          // themed emoji / trivia / wyr items are written on demand in CI: lines do not depend on the items, so a themed plan
          // without stored items borrows the plain anime items (the edition, topic and round count are what the lines depend on)
          if (ed && (plan.rounds || []).length < Math.min(3, n) && ['emoji', 'trivia', 'wyr'].includes(fmt)) {
            const plain = (await content.buildPopPlan(fmt, { topic: 'anime', rounds: n })).plan;
            plan = { ...plain, edition: { ...themes[ed], id: ed }, synthetic: true };
          }
          if ((plan.rounds || []).length < Math.min(3, n)) { out.push({ skip: `${fmt}/${topic || plan.topic}/${ed || '-'}: only ${plan.rounds.length} items` }); break; }
          if (plan.want) plan.rounds = plan.rounds.slice(0, plan.want);   // the renderer keeps `want` of the candidates
          plan.seed = 1000 + s * 7919 + n;
          if (WITH_AI) {
            const a = AI_SETS[s % AI_SETS.length];
            plan.ai_lines = { intro: a.intro, outro: a.outro, reveal: plan.rounds.map(() => a.reveal) };
          }
          out.push({ id: `${fmt}|${plan.topic}|${ed || '-'}|${n}|s${s}`, plan });
        }
      }
    }
  }
  return out;
}

(async () => {
  const items = [];
  const skipped = [];
  for (const f of POP) for (const x of await plansFor(f)) (x.skip ? skipped.push(x.skip) : items.push(x));
  // geography: core/quiz/plan.py plans
  const py = learn.pythonCommand();
  for (const f of GEO) {
    for (const th of GEO_THEMES) {
      for (const n of [3, 4, 5]) {
        for (let s = 0; s < Math.min(SEEDS, 3); s++) {
          try {
            const code = `import json,sys; sys.path.insert(0, r'${path.join(ROOT, 'core', 'quiz')}'); import plan as p; print(json.dumps(p.make_plan('${f}', seed=${s + 11}, theme='${th}', rounds=${n})))`;
            const plan = JSON.parse(execFileSync(py, ['-c', code], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }).trim().split('\n').pop());
            if (plan.rounds.length >= 3) items.push({ id: `${f}|${th}|-|${n}|s${s}`, plan });
          } catch (e) { skipped.push(`${f}/${th}: ${(e.message || '').slice(0, 80)}`); }
        }
      }
    }
  }
  const inFile = path.join(os.tmpdir(), `quiz-line-plans-${process.pid}.json`);
  fs.writeFileSync(inFile, JSON.stringify(items));
  const summary = execFileSync(py, [path.join(ROOT, 'core', 'quiz', 'line_check.py'), '--dry-run', inFile, OUT],
    { encoding: 'utf8', cwd: path.join(ROOT, 'core', 'quiz'), env: { ...process.env, PYTHONIOENCODING: 'utf-8' }, maxBuffer: 64 * 1024 * 1024 });
  fs.unlinkSync(inFile);
  const res = JSON.parse(fs.readFileSync(OUT, 'utf8'));
  const combos = new Set(res.map(r => r.id.split('|').slice(0, 4).join('|')));
  // the JS rule set (AI validator + pre-upload guard) must agree with the Python one on the final lines
  const lineCheck = require('../pipeline/quiz-line-check');
  const byId = Object.fromEntries(items.map(x => [x.id, x.plan]));
  let jsLeft = 0;
  for (const r of res) {
    const p = lineCheck.checkSpoken(byId[r.id], r.spoken, r.screen);
    if (p.length) { jsLeft += p.length; console.log(`JS ${r.id}: ${p.join(' | ')}`); }
  }
  const poolSwaps = res.flatMap(r => r.swaps.filter(s => !/\(ai\)$/.test(s.slot)).map(s => `${r.id}: ${s.slot} "${s.line}": ${s.why.join('; ')}`));
  const aiSwaps = res.reduce((a, r) => a + r.swaps.filter(s => /\(ai\)$/.test(s.slot)).length, 0);
  const left = res.flatMap(r => r.left.map(l => `${r.id}: ${l}`));
  console.log(`plans ${res.length} (${combos.size} format x topic x edition x length combinations), skipped ${skipped.length}`);
  for (const s of [...new Set(skipped)]) console.log(`  skipped: ${s}`);
  console.log(`pool / template lines dropped by the check: ${poolSwaps.length}`);
  for (const s of [...new Set(poolSwaps)].slice(0, 60)) console.log(`  ${s}`);
  if (WITH_AI) console.log(`injected AI lines dropped by the check: ${aiSwaps}`);
  console.log(`mismatches left in spoken / on-screen lines: python ${left.length}, js ${jsLeft}`);
  for (const l of left.slice(0, 60)) console.log(`  ${l}`);
  console.log(`details: ${OUT} (${summary.trim()})`);
  process.exit(left.length || jsLeft ? 1 : 0);
})();
