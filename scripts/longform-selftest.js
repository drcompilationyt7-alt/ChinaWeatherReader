/**
 * Selftest (no network, no render): the daily rotation picker and the long-form queue.   node scripts/longform-selftest.js
 *   1. rotation: weights, no repeat, empty pool -> stem, off -> stem, fallback order ends in stem
 *   2. 90-day simulation of the funnel (2 of 3 days make a long): gap >= LONG_MIN_GAP_DAYS, <= LONG_MAX_PER_WEEK per
 *      7 days, the queue never exceeds LONG_QUEUE_MAX, every long goes out >= 7 days after its short
 *   3. enqueue + uploadDue with a local store and a fake YouTube bridge: asset stored, uploaded, linked both ways, asset deleted
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const T = fs.mkdtempSync(path.join(os.tmpdir(), 'longform-test-'));
process.env.MEMORY_DIR = path.relative(path.join(__dirname, '..'), path.join(T, 'memory'));
process.env.LONG_STORE = `local:${path.join(T, 'store')}`;
process.env.META_LLM = 'off';
process.env.LONG_ENABLED = 'on';   // the funnel is off by default in production
const rot = require('../pipeline/daily-rotation');
const lf = require('../pipeline/longform-queue');
let fails = 0;
const check = (c, w) => { console.log((c ? 'PASS  ' : 'FAIL  ') + w); if (!c) fails++; };
const DAY = 86400000;
const ALL1 = { stem: 1, sound: 1, slowmo: 1 };

// 1. rotation
{
  const counts = {}; let prev = null, repeats = 0, badTail = 0;
  for (let i = 0; i < 3000; i++) {
    const o = rot.dailyOrder({ history: { uploads: prev ? [{ contentType: `${prev}-edit` }] : [] }, poolSizes: { sound: 40, slowmo: 40 }, enabled: true, weights: ALL1 });
    counts[o[0]] = (counts[o[0]] || 0) + 1; if (o[0] === prev) repeats++; if (o[0] !== 'stem' && o[o.length - 1] !== 'stem') badTail++; prev = o[0];
  }
  check(repeats === 0, 'rotation: never the same type twice in a row');
  check(Object.values(counts).every(n => n > 800), `rotation: roughly even ${JSON.stringify(counts)}`);
  check(badTail === 0, 'rotation: stem is always the last fallback');
  check(rot.dailyOrder({ poolSizes: {}, enabled: true, weights: ALL1 }).join() === 'stem', 'rotation: empty pools -> stem only');
  check(rot.dailyOrder({ enabled: false, poolSizes: { sound: 40 } }).join() === 'stem', 'rotation: DAILY_ROTATION=off -> stem');
  check(rot.dailyOrder({ poolSizes: { sound: 40, slowmo: 40 }, history: { uploads: [] } }).join() === 'stem', 'rotation: default (DAILY_ROTATION unset) = stem only');
  check(rot.parseWeights().sound === 0 && rot.parseWeights().slowmo === 0, 'rotation: default weights = stem only');
  const w = rot.parseWeights('stem:0,sound:3,slowmo:1');
  check(w.stem === 0 && w.sound === 3, 'rotation: DAILY_TYPE_WEIGHTS parsed');
  const c2 = { sound: 0, slowmo: 0 };
  for (let i = 0; i < 2000; i++) { const o = rot.dailyOrder({ weights: w, poolSizes: { sound: 9, slowmo: 9 }, history: { uploads: [] }, enabled: true }); c2[o[0]]++; }
  check(c2.sound > c2.slowmo * 2 && c2.sound + c2.slowmo === 2000, `rotation: weights respected ${JSON.stringify(c2)}`);
}

// 2. cadence simulation
{
  const c = lf.cfg(); const q = { items: [] }; let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const out = []; let maxQ = 0, skipped = 0, earlyOut = 0;
  const start = new Date('2026-11-01T00:00:00Z');
  for (let d = 0; d < 90; d++) {
    const now = new Date(start.getTime() + d * DAY + 3 * 3600000);
    const { item, slot } = lf.pickDue(q, now);
    if (item) { item.status = 'uploaded'; item.slotAt = slot.toISOString(); out.push(slot); if (slot < new Date(item.dueAt)) earlyOut++; }
    if (rnd() < 2 / 3) {
      if (lf.shouldRender(q)) {
        const pub = new Date(start.getTime() + d * DAY + 12 * 3600000).toISOString();
        q.items.push({ id: 'x' + d, status: 'queued', shortPublishAt: pub, dueAt: lf.dueAt(pub).toISOString() });
      } else skipped++;
    }
    for (const e of lf.pickDue(q, now).expired) e.status = 'expired';
    maxQ = Math.max(maxQ, q.items.filter(i => i.status === 'queued').length);
  }
  const gaps = out.slice(1).map((s, i) => (s - out[i]) / DAY);
  check(Math.min(...gaps) >= c.minGapDays, `cadence: min gap ${Math.min(...gaps).toFixed(1)} d >= ${c.minGapDays}`);
  let worst = 0; for (const s of out) worst = Math.max(worst, out.filter(x => x > new Date(s - 7 * DAY) && x <= s).length);
  check(worst <= c.maxPerWeek, `cadence: max ${worst} per 7 days <= ${c.maxPerWeek}`);
  check(maxQ <= c.queueMax, `cadence: queue peak ${maxQ} <= ${c.queueMax} (${skipped} longs not rendered because the queue was full)`);
  check(earlyOut === 0, 'cadence: no long before its due date (short + 7 d)');
  console.log(`      ${out.length} longs in 90 days = ${(out.length / 90 * 7).toFixed(1)} per week`);
}

// 3. enqueue + upload with fakes
(async () => {
  const f = path.join(T, 'long.mp4'); fs.writeFileSync(f, 'fake');
  const published = [], updated = [];
  const bridge = {
    isAuthenticated: () => true,
    uploadVideo: async p => { published.push(p); return { videoId: 'LONG1', url: 'https://www.youtube.com/watch?v=LONG1', publishAt: p.publishAt }; },
    addToPlaylist: async () => {},
    youtube: { videos: {
      list: async () => ({ data: { items: [{ snippet: { title: 'short', description: 'short desc', categoryId: '1', tags: ['a'] } }] } }),
      update: async r => { updated.push(r); } } },
  };
  const short = new Date(Date.now() - 6.9 * DAY).toISOString();   // its long is due about now
  const item = await lf.enqueue({ longPath: f, title: 'T', categoryId: '1', facts: { source: 'Frieren', vibe: 'calm', theme: 'anime' },
    meme: { contentType: 'slowmo-edit' } }, { videoId: 'SHORT1', url: 'https://www.youtube.com/watch?v=SHORT1', publishAt: short });
  check(item && item.status === 'queued' && fs.existsSync(path.join(T, 'store', 'slowmo-SHORT1.mp4')), 'queue: long stored, item queued');
  check(item.description.includes('SHORT1') && /slow motion/i.test(item.title + item.description), 'queue: long metadata links the short');
  const r = await lf.uploadDue({ youtubeBridge: bridge, now: new Date(new Date(item.dueAt).getTime() - 3600000) });
  check(r && r.up.videoId === 'LONG1' && published.length === 1, 'upload: the long went up');
  check(updated.length === 1 && updated[0].requestBody.snippet.description.includes('LONG1'), 'upload: the short links to the long');
  check(!fs.existsSync(path.join(T, 'store', 'slowmo-SHORT1.mp4')), 'upload: the stored asset was deleted');
  const again = await lf.uploadDue({ youtubeBridge: bridge, now: new Date() });
  check(again === null && published.length === 1, 'upload: nothing twice');
  console.log(fails ? `\n${fails} FAILED` : '\nall passed');
  fs.rmSync(T, { recursive: true, force: true });
  process.exit(fails ? 1 : 0);
})();
