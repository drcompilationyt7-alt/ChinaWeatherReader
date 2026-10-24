#!/usr/bin/env node
/**
 * Mainland Unpacked: hand a reviewed week to GitHub Actions.
 *
 *   node scripts/china/push-week.js <weekDir> [--start YYYY-MM-DD] [--long-day sat] [--dry]
 *
 * <weekDir>/week.json lists the items:
 *   { "items": [ { "type": "short" | "long", "file": "short-1.mp4", "title": "...",
 *                  "description": "...", "tags": [...], "thumbnail": "long.jpg"?,
 *                  "captions": "long.srt"?, "categoryId": "28"?,
 *                  "date": "YYYY-MM-DD"?, "publish_utc": "14:00"? } ] }
 *
 * When an item has no date:
 *   - the Shorts get one day each, in list order, starting from --start (default: tomorrow, UTC);
 *   - the long-form gets the first --long-day (default sat) in that 7-day window.
 *
 * The script checks every item (files, sizes, Shorts length and shape, YouTube metadata limits,
 * date clashes with weeks already queued). It then writes manifest.json and creates a DRAFT release
 * "china-week-<first date>" holding the manifest and the files. The daily workflow
 * (.github/workflows/china-channel.yml) uploads from there. --dry only checks and prints the plan.
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const titleStyle = require('../../core/metadata/title-style');

const REPO = process.env.CHINA_REPO || 'drcompilationyt7-alt/ChinaWeatherReader';
const MAX_ASSET = 2 * 1024 ** 3 - 1;
const DAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];

const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const dir = args.find((a, i) => !a.startsWith('--') && !['--start', '--long-day'].includes(args[i - 1]));
const DRY = args.includes('--dry');
if (!dir) { console.error('usage: node scripts/china/push-week.js <weekDir> [--start YYYY-MM-DD] [--long-day sat] [--dry]'); process.exit(2); }

const addDays = (d, n) => { const x = new Date(`${d}T00:00:00Z`); x.setUTCDate(x.getUTCDate() + n); return x.toISOString().slice(0, 10); };
const tomorrow = addDays(new Date().toISOString().slice(0, 10), 1);
const start = opt('--start', tomorrow);
const longDay = DAYS.indexOf(String(opt('--long-day', 'sat')).slice(0, 3).toLowerCase());
const gh = (...a) => execFileSync('gh', a, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });

function probe(file) {
  try {
    const out = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-select_streams', 'v:0', '-show_entries',
      'stream=width,height:format=duration', '-of', 'json', file], { encoding: 'utf8' }));
    return { w: out.streams[0].width, h: out.streams[0].height, dur: Number(out.format.duration) };
  } catch { return null; }
}

const errors = [];
const warn = [];
const week = JSON.parse(fs.readFileSync(path.join(dir, 'week.json'), 'utf8'));
const items = week.items || [];
if (start < tomorrow) errors.push(`--start ${start} is not in the future`);

// dates
let n = 0;
for (const it of items) if (it.type === 'short' && !it.date) it.date = addDays(start, n++);
for (const it of items) if (it.type === 'long' && !it.date) {
  for (let i = 0; i < 7; i++) if (new Date(`${addDays(start, i)}T00:00:00Z`).getUTCDay() === longDay) { it.date = addDays(start, i); break; }
}
const tag = `china-week-${items.map(i => i.date).sort()[0]}`;

// checks
for (const [i, it] of items.entries()) {
  const where = `item ${i + 1} (${it.file})`;
  it.id = `${tag.slice('china-week-'.length)}-${it.type}-${i + 1}`;
  if (!['short', 'long'].includes(it.type)) errors.push(`${where}: type must be short or long`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(it.date || '')) errors.push(`${where}: no date`);
  else if (it.date < tomorrow) errors.push(`${where}: date ${it.date} is not in the future`);
  if (it.publish_utc && !/^\d{2}:\d{2}$/.test(it.publish_utc)) errors.push(`${where}: publish_utc must be HH:MM`);
  if (!it.title || it.title.length > 100 || /[<>]/.test(it.title)) errors.push(`${where}: title must be 1-100 chars, no < >`);
  // Shorts title style (core/metadata/title-style.js, research 2026-10-24): <= 55 characters, China / the place in the
  // first 30, no #shorts, no hashtags, no all-caps. item.keyword may name the subject when it is not China itself.
  if (it.type === 'short' && it.title) {
    const tv = titleStyle.check(it.title, 'china', { front: it.keyword ? [it.keyword] : [] });
    if (!tv.ok) errors.push(`${where}: title "${it.title}" breaks the Shorts title style: ${tv.reasons.join('; ')}`);
  }
  if (Buffer.byteLength(it.description || '') > 5000 || /[<>]/.test(it.description || '')) errors.push(`${where}: description over 5000 bytes or has < >`);
  if ((it.tags || []).join(',').length > 480) errors.push(`${where}: tags too long (YouTube max ~500 chars)`);
  for (const f of [it.file, it.thumbnail, it.captions].filter(Boolean)) {
    if (!/^[A-Za-z0-9._-]+$/.test(f)) errors.push(`${where}: file name "${f}" must be letters, digits, . _ - only`);
    const p = path.join(dir, f);
    if (!fs.existsSync(p)) { errors.push(`${where}: ${f} not found`); continue; }
    if (fs.statSync(p).size > MAX_ASSET) errors.push(`${where}: ${f} is over 2 GB (GitHub release limit); re-encode at a lower bitrate`);
  }
  if (it.thumbnail && fs.existsSync(path.join(dir, it.thumbnail)) && fs.statSync(path.join(dir, it.thumbnail)).size > 2e6)
    errors.push(`${where}: thumbnail is over 2 MB (YouTube limit)`);
  const v = fs.existsSync(path.join(dir, it.file)) && probe(path.join(dir, it.file));
  if (!v) { warn.push(`${where}: couldn't probe (no ffprobe?); length and shape not checked`); continue; }
  if (it.type === 'short' && (v.dur > 180 || v.w > v.h)) errors.push(`${where}: a Short must be vertical or square and ≤ 3 min (is ${v.w}x${v.h}, ${v.dur.toFixed(0)} s)`);
  if (it.type === 'long' && v.dur > 15 * 60) warn.push(`${where}: ${(v.dur / 60).toFixed(0)} min; uploads over 15 min need a phone-verified channel`);
}
if (new Set(items.map(i => i.title)).size !== items.length) errors.push('two items share a title (the uploader dedupes by title)');

// date clashes with weeks already queued
let queued = [];
try {
  queued = JSON.parse(gh('api', `repos/${REPO}/releases?per_page=100`))   // includes drafts
    .filter(r => String(r.tag_name).startsWith('china-week-'));
} catch (e) { warn.push(`couldn't list releases: ${e.stderr || e.message}`); }
if (queued.some(r => r.tag_name === tag)) errors.push(`${tag} already exists; pick another --start`);
for (const r of queued) {
  const m = r.assets.find(x => x.name === 'manifest.json');
  try {
    const other = JSON.parse(gh('api', '-H', 'Accept: application/octet-stream', `repos/${REPO}/releases/assets/${m.id}`)).items;
    for (const it of items) if (other.some(o => o.type === it.type && o.date === it.date))
      errors.push(`${it.file}: ${it.date} already has a ${it.type} queued in ${r.tag_name}`);
  } catch { warn.push(`couldn't read the manifest of ${r.tag_name}`); }
}



console.log(`\n${tag} → ${REPO} (draft release)\n`);
for (const it of [...items].sort((a, b) => String(a.date).localeCompare(String(b.date))))
  console.log(`  ${it.date} ${it.publish_utc || (it.type === 'long' ? '15:00' : '14:00')} UTC  ${it.type.padEnd(5)}  ${it.file}  "${it.title}"`);
for (const w of warn) console.log(`  ⚠ ${w}`);
if (errors.length) { console.error('\nNot pushed:\n' + errors.map(e => `  ✗ ${e}`).join('\n')); process.exit(1); }
if (DRY) { console.log('\n--dry: checks passed, nothing pushed.'); process.exit(0); }

// access check, then push
const perm = JSON.parse(gh('api', `repos/${REPO}`, '--jq', '.permissions'));
if (!perm.push) { console.error(`\nThe active gh account can't push to ${REPO}. Run: gh auth switch -u drcompilationyt7-alt`); process.exit(1); }
const manifest = { tag, createdAt: new Date().toISOString(), items };
fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(manifest, null, 2));
const files = ['manifest.json', ...new Set(items.flatMap(i => [i.file, i.thumbnail, i.captions]).filter(Boolean))]
  .map(f => path.join(dir, f));
console.log(`\nUploading ${files.length} file(s) to a draft release (large files take a while)...`);
execFileSync('gh', ['release', 'create', tag, '--repo', REPO, '--draft', '--title', tag,
  '--notes', `Mainland Unpacked queue, ${items.length} item(s). Uploaded to YouTube one by one by china-channel.yml; deleted when done.`,
  ...files], { stdio: 'inherit' });
console.log(`\n✓ Queued. The daily workflow posts each item on its date.`);
