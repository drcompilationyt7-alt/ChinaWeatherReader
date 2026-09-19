#!/usr/bin/env node
/**
 * Weekly reflection for one channel: what each choice has earned so far, in
 * plain tables computed from the data, plus one short LLM read of those
 * tables (it may only interpret the numbers it is given).
 *
 *   MEMORY_DIR=memory              node core/reflect/weekly-report.js --channel meme
 *   MEMORY_DIR=memory/quiz-channel node core/reflect/weekly-report.js --channel quiz
 *
 * Reads <MEMORY_DIR>/{meme-history|quiz-history}.json and performance-stats.json
 * (rewards from core/experiment-log.js). Writes reports/weekly/<channel>/<YYYY-Www>.md
 * and <MEMORY_DIR>/lessons.json (the latest effects, for people and future tools).
 *
 * Metadata section: core/metadata/evolve.js scores every metadata arm (title style, on-video
 * text, hashtag set, emoji set, description hook), retires clear losers and adds up to 3 new
 * LLM-proposed arms (validated, bounded prior) to <MEMORY_DIR>/metadata-arms.json, once a week.
 */
const fs = require('fs');
const path = require('path');
const { decay } = require('../experiment-log');

const ROOT = path.join(__dirname, '..', '..');
const MEM = path.resolve(ROOT, process.env.MEMORY_DIR || 'memory');

function readJson(file, dflt) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return dflt; }
}

function isoWeek(d = new Date()) {
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  const y0 = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  return `${t.getUTCFullYear()}-W${String(Math.ceil(((t - y0) / 86400000 + 1) / 7)).padStart(2, '0')}`;
}

/** Uploads joined with their stats; `learn` is the reward (or lscore before rewards exist). */
function joined(channel) {
  const hist = readJson(path.join(MEM, channel === 'meme' ? 'meme-history.json' : 'quiz-history.json'), { uploads: [] });
  const stats = readJson(path.join(MEM, 'performance-stats.json'), { videos: [] });
  const byId = new Map((stats.videos || []).map(v => [v.videoId, v]));
  const rows = hist.uploads.map(u => ({ ...u, stat: byId.get(u.videoId) })).filter(u => u.stat && (u.stat.ageDays || 0) >= 1.5);
  const useReward = rows.length && rows.every(u => typeof u.stat.reward === 'number');
  return { rows: rows.map(u => ({ ...u, learn: useReward ? u.stat.reward : u.stat.lscore, w: decay(u.stat.ageDays) })), useReward, stats };
}

const FACTORS = {
  meme: {
    'anime / group': u => u.clipSource || '(theme clips)',
    'clip theme': u => u.theme,
    'song': u => u.song,
    'drop length (s)': u => u.settings && u.settings.drop,
    'edit shots': u => u.settings && u.settings.edit,
    'hashtag in title': u => u.settings && (u.settings.hashtag ? 'yes' : 'no'),
    'description hook': u => u.settings && u.settings.hook,
    'colour look': u => u.settings && u.settings.look,
  },
  quiz: {
    'format': u => u.format,
    'length': u => u.length || 'standard',
    'title template': u => u.titleTemplate,
  },
};

/** Per value of a factor: weighted n, weighted mean, lift vs the channel mean. */
function effects(rows, fn) {
  const all = rows.reduce((a, u) => a + u.w * u.learn, 0) / (rows.reduce((a, u) => a + u.w, 0) || 1);
  const groups = new Map();
  for (const u of rows) {
    const k = fn(u);
    if (k === undefined || k === null) continue;
    const g = groups.get(k) || { n: 0, sw: 0, s: 0 };
    g.n += 1; g.sw += u.w; g.s += u.w * u.learn;
    groups.set(k, g);
  }
  return [...groups.entries()].map(([k, g]) => ({ value: String(k), n: g.n, mean: g.s / g.sw, lift: g.s / g.sw - all }))
    .sort((a, b) => b.mean - a.mean);
}

const fmt = x => (Number.isFinite(x) ? (x >= 0 ? '+' : '') + x.toFixed(2) : '–');

async function llmRead(channel, tablesText) {
  try {
    const { getGeminiService } = require('../gemini-service');
    const g = getGeminiService();
    if (!g || g.getStats().keysLoaded === 0) return null;
    const sys = 'You review a YouTube Shorts channel\'s weekly numbers. Use ONLY the numbers given; never invent data. '
      + 'Small samples (n < 3) are anecdotes: say so. Output 3-6 short bullet points: what seems to work, what to try more, '
      + 'what to try less, and one concrete experiment for next week. No fluff.';
    const out = await Promise.race([g.chat(sys, `Channel: ${channel}\n\n${tablesText}`), new Promise(r => setTimeout(() => r(null), 60000))]);
    return typeof out === 'string' ? out.trim() : (out && out.text) || null;
  } catch { return null; }
}

async function main() {
  const channel = (process.argv.find(a => a.startsWith('--channel=')) || '').split('=')[1]
    || process.argv[process.argv.indexOf('--channel') + 1] || 'quiz';
  const { rows, useReward, stats } = joined(channel);
  const week = isoWeek();
  const lines = [`# ${channel === 'meme' ? 'Zero Yen Otaku' : 'Asian Pop Quiz'}: week ${week}`, ''];
  const now = Date.now();
  const lastWeek = rows.filter(u => now - new Date(u.postedAt).getTime() < 7 * 86400000);
  const ch = stats.channel || {};
  lines.push(`Subscribers ${ch.subscribers ?? '?'}, total views ${ch.totalViews ?? '?'}. Scored uploads: ${rows.length} `
    + `(${lastWeek.length} posted in the last 7 days). Score: ${useReward ? 'reward (z-score vs the last 60 days: views at 7 days, % viewed, subs per 1k views)' : 'lscore (log views/day), until every upload has a reward'}; `
    + 'older results weigh less (10% per week).', '');

  const bySc = [...rows].sort((a, b) => b.learn - a.learn);
  const line = u => `| ${(u.title || u.song || u.format || '').replace(/\|/g, '/').slice(0, 60)} | ${u.stat.views} | ${fmt(u.learn)} | ${u.stat.v7 ?? '–'} |`;
  lines.push('## Best 5', '', '| title | views | score | views @7d |', '|---|---|---|---|', ...bySc.slice(0, 5).map(line), '');
  lines.push('## Worst 5', '', '| title | views | score | views @7d |', '|---|---|---|---|', ...bySc.slice(-5).reverse().map(line), '');

  const lessons = {};
  let tables = '';
  for (const [name, fn] of Object.entries(FACTORS[channel] || {})) {
    const eff = effects(rows, fn);
    if (!eff.length) continue;
    lessons[name] = eff;
    const t = [`## ${name}`, '', '| value | uploads | mean score | vs channel |', '|---|---|---|---|',
      ...eff.slice(0, 15).map(e => `| ${e.value.slice(0, 40)} | ${e.n} | ${fmt(e.mean)} | ${fmt(e.lift)} |`), ''];
    lines.push(...t);
    tables += t.join('\n') + '\n';
  }
  // metadata: what each title style / on-video text / hashtag set / emoji set / description hook earned,
  // then the weekly self-improvement step (retire clear losers, add up to 3 validated new arms)
  try {
    const { weeklyMetadataStep } = require('../metadata/evolve');
    const md = await weeklyMetadataStep({ channel, week });
    lines.push(...md.lines);
    tables += md.tablesText;
  } catch (e) {
    lines.push(`_Metadata step failed: ${String((e && e.message) || e).slice(0, 200)}_`, '');
  }
  const read = rows.length >= 3 ? await llmRead(channel, tables) : null;
  if (read) lines.push('## Read (LLM, from the tables above only)', '', read, '');
  else if (rows.length < 3) lines.push('_Not enough scored uploads yet for a read._', '');

  const dir = path.join(ROOT, 'reports', 'weekly', channel);
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${week}.md`);
  fs.writeFileSync(file, lines.join('\n'));
  fs.writeFileSync(path.join(MEM, 'lessons.json'), JSON.stringify({ week, useReward, n: rows.length, effects: lessons }, null, 1));
  console.log(`Weekly report: ${path.relative(ROOT, file)} (${rows.length} scored uploads)`);
}

// exit when done: a timed-out LLM call can leave retry timers running (all files are written synchronously)
if (require.main === module) main().then(() => process.exit(0)).catch(e => { console.error(e); process.exit(1); });
module.exports = { effects, isoWeek };
