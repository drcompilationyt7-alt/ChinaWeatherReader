/**
 * Simulation of "learn why it hit, exploit it a bit more, keep randomness" (core/reflect/edit-insights.js + pickSong + title
 * mimicry) on the REAL upload history and stats (read only; nothing is written).
 *
 *   node scripts/edit-insights-sim.js [--runs 40] [--days 60]
 *
 * Checks over every simulated 60-day run:
 *   - no song repeats within 7 uploads
 *   - hit songs come back ~1.5-2x as often as without the insights (same Thompson sampler, insights off)
 *   - the share of picks similar to a winner (positive artist / vibe / anime bonus) goes up
 *   - >= 30% of picks are exploration (insights ignored)
 *   - titles: no duplicates, most follow a winning pattern, >= 25% explore
 */
process.env.META_LLM = 'off';
process.env.META_SUGGEST = 'off';
const fs = require('fs');
const path = require('path');
const { rngFrom } = require('../core/metadata/learner');
const I = require('../core/reflect/edit-insights');
const meme = require('../pipeline/meme-pipeline');
const { optimizeMetadata } = require('../core/metadata');

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? Number(process.argv[i + 1]) : d; };
const RUNS = arg('--runs', 40), DAYS = arg('--days', 60);
const MEM = path.join(__dirname, '..', 'memory');
const stats = JSON.parse(fs.readFileSync(path.join(MEM, 'performance-stats.json'), 'utf8'));
const history0 = JSON.parse(fs.readFileSync(path.join(MEM, 'meme-history.json'), 'utf8'));
const ins = I.computeInsights({ stats, history: history0, write: false });
const SONGS = meme.SONGS;
const byName = new Map(SONGS.map(s => [s.name, s]));
const hitSongs = new Set((ins.rows || []).filter(r => r.resid >= 0.3 && r.views >= 500 && byName.has(r.song)).map(r => r.song));
const similar = s => !hitSongs.has(s.name) && I.songBonus(ins, s) > 0.1;

let fails = 0;
const check = (ok, msg) => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`); if (!ok) fails++; };

function simulate(exploit, seed) {
  const rng = rngFrom(seed);
  const h = { uploads: [...history0.uploads] };
  const picks = [];
  for (let d = 0; d < DAYS; d++) {
    const s = meme.pickSong(stats, h, [], { rng, insights: exploit ? ins : null, exploit });
    picks.push(s);
    h.uploads.push({ videoId: `sim${seed}_${d}`, song: s.name, contentType: 'stem-edit', clipSource: s.source, followUpOf: s.followUpOf, title: s.name });
  }
  return { picks, uploads: h.uploads.slice(-DAYS - 7) };
}

const agg = on => ({ n: 0, hit: 0, sim: 0, explore: 0, repeat: 0, insight: 0, gapFail: 0 });
const A = agg(), B = agg();
for (let r = 0; r < RUNS; r++) {
  for (const [exploit, acc] of [[true, A], [false, B]]) {
    const { picks, uploads } = simulate(exploit, 1000 + r);
    for (const p of picks) {
      acc.n++;
      if (hitSongs.has(p.name)) acc.hit++;
      if (similar(p)) acc.sim++;
      if (p.whyMode === 'explore') acc.explore++;
      if (p.whyMode === 'hit-spaced-repeat') acc.repeat++;
      if (p.whyMode === 'insight-driven') acc.insight++;
    }
    // no song twice within 7 uploads (the real tail + the simulated days)
    for (let i = 7; i < uploads.length; i++) if (uploads.slice(i - 7, i).some(u => u.song === uploads[i].song) && uploads[i].videoId.startsWith('sim')) acc.gapFail++;
  }
}
const pct = x => `${(100 * x).toFixed(1)}%`;
console.log(`insights: ${I.summaryLine(ins)}`);
console.log(`hit songs: ${[...hitSongs].join(', ')}`);
console.log(`with insights: hit songs ${pct(A.hit / A.n)}, similar ${pct(A.sim / A.n)}, explore ${pct(A.explore / A.n)}, spaced repeats ${pct(A.repeat / A.n)}, insight-driven ${pct(A.insight / A.n)}`);
console.log(`without      : hit songs ${pct(B.hit / B.n)}, similar ${pct(B.sim / B.n)}`);
const ratio = (A.hit / A.n) / Math.max(1e-9, B.hit / B.n);
check(A.gapFail === 0 && B.gapFail === 0, `no song repeats within 7 uploads (${A.gapFail + B.gapFail} violations in ${RUNS * 2} runs x ${DAYS} days)`);
check(ratio >= 1.4 && ratio <= 2.2, `hit songs ${ratio.toFixed(2)}x the base rate (target ~1.5-2x)`);
check(A.sim / A.n > B.sim / B.n * 1.1, `similar-artist / vibe / anime share up: ${pct(B.sim / B.n)} -> ${pct(A.sim / A.n)}`);
check(A.explore / A.n >= 0.3, `exploration share ${pct(A.explore / A.n)} (>= 30%)`);

(async () => {
  // titles over one simulated 60-day run: varied, mostly the winning patterns
  const { picks } = simulate(true, 7);
  const titles = [];
  const modes = { mimic: 0, explore: 0, other: 0 };
  const rng = rngFrom(99);
  const winners = new Set(I.mimicTemplates(ins, 'meme/stem-edit').map(p => p.pattern));
  let followsWinner = 0;
  for (const s of picks) {
    const m = await optimizeMetadata({ channel: 'meme', contentType: 'stem-edit', llm: false, rng,
      facts: { song: s.name, artist: s.artist, source: s.source, kind: s.kind, theme: s.theme, insights: ins,
        recentTitles: [...history0.uploads.map(u => u.title), ...titles] } });
    titles.push(m.title);
    modes[m.arms.titleMode === 'mimic' ? 'mimic' : m.arms.titleMode === 'explore' ? 'explore' : 'other']++;
    if (winners.has(m.arms.titlePattern)) followsWinner++;
  }
  const all = [...history0.uploads.map(u => u.title), ...titles].map(t => t.toLowerCase());
  const dups = titles.filter(t => all.filter(x => x === t.toLowerCase()).length > 1);
  console.log('sample titles:\n  ' + titles.slice(0, 8).join('\n  '));
  check(dups.length === 0, `no duplicate titles (${titles.length} new, ${dups.length} duplicates${dups.length ? `: ${dups.slice(0, 3).join(' | ')}` : ''})`);
  check(followsWinner / titles.length >= 0.55, `titles following a winning pattern: ${pct(followsWinner / titles.length)} (mimic ${modes.mimic}, explore ${modes.explore}, other ${modes.other})`);
  check(modes.explore / titles.length >= 0.2, `title exploration ${pct(modes.explore / titles.length)} (target ~30%)`);
  // sound edit titles mimic too
  const snd = await optimizeMetadata({ channel: 'meme', contentType: 'sound-edit', llm: false, rng: rngFrom(3),
    facts: { keyword: 'anime sound design edit', song: 'Thunder', theme: 'anime', insights: ins } });
  console.log(`sound title: "${snd.title}" (${snd.arms.titleMode || 'arms'}, pattern ${snd.arms.titlePattern})`);
  console.log(fails ? `\n${fails} check(s) failed` : '\nall passed');
  process.exit(fails ? 1 : 0);
})();
