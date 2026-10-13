/**
 * Pool-based edit types for the daily rotation: "sound" (sound edit) and "slowmo" (cinematic slow-mo).
 * Unlike the stem edit (a song picked live), these draw on vibe pools (good clips + good sounds per vibe, format in
 * core/edit/refs/pool-format.md, refreshed monthly by .github/workflows/edit-pool-refresh.yml). Rendering is the
 * edit-studio agent's CLI:
 *
 *   python core/edit/vibe_edit.py --type sound|slowmo --out <dir> [--landscape-long] [--pool $EDIT_POOL_DIR]
 *     -> <dir>/result.json {ok, short, long, preset, vibe, song, clips_used, sound_used, ...}; exit 2 = pool_low
 *   python core/edit/vibe_pool.py status --json     (which vibes are ready; a type is eligible when one is)
 *   python core/edit/vibe_pool.py mark-used --result <dir>/result.json    (the ledger memory/edit-pool-used.json)
 *
 * The pools are NOT in git: they live in the release "edit-pools" (scripts/edit-pools-store.js pull / push).
 *
 *   const r = await runNewType({ type, outputDir, wantLong });   // runMemePipeline-shaped result
 *   recordUpload(r, upload);   // memory/meme-history.json (contentType 'sound-edit' | 'slowmo-edit')
 */
const path = require('path');
const fs = require('fs');
const { execFile, execFileSync } = require('child_process');
const { Logger } = require('../core/logger');
const { appendDecision } = require('../core/experiment-log');
const { optimizeMetadata, recordMetadataChoice, buildDescription } = require('../core/metadata');

const logger = new Logger('NewTypes');
const ROOT = path.join(__dirname, '..');
const MEM = () => path.resolve(ROOT, process.env.MEMORY_DIR || 'memory');
const HISTORY_FILE = () => path.join(MEM(), 'meme-history.json');
const CT = { sound: 'sound-edit', slowmo: 'slowmo-edit' };
const PLAYLIST = { sound: 'sound edits', slowmo: 'slow motion edits' };
const VIBE_KEYWORD = { impact: 'anime sound design edit', crisp: 'anime sound design edit', summer_glow: 'anime summer slow motion edit',
  dreamy_bloom: 'anime sakura slow motion edit' };

const readJson = (f, d) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return d; } };
const python = () => process.env.MEME_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const poolRoot = () => path.resolve(ROOT, process.env.EDIT_POOL_DIR || 'output/edit-pools');
const CORE = f => path.join(ROOT, 'core', 'edit', f);

/** how many vibes of a type are ready (0 = not ready: the rotation skips the type). Never throws. */
function readyVibes(type) {
  try {
    if (!fs.existsSync(path.join(poolRoot(), 'index.json')) || !fs.existsSync(CORE('vibe_pool.py'))) return 0;
    const out = execFileSync(python(), [CORE('vibe_pool.py'), 'status', '--json'],
      { timeout: 90000, encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8', EDIT_POOL_DIR: poolRoot() } });
    const i = Math.min(...['{', '['].map(c => (out.indexOf(c) < 0 ? 1e9 : out.indexOf(c))));
    const j = JSON.parse(out.slice(i));
    let n = 0;
    const walk = (node, key) => {
      if (Array.isArray(node)) return node.forEach(x => walk(x, key));
      if (node && typeof node === 'object') {
        const name = String(node.pool || node.name || key || '');
        if (typeof node.ready === 'boolean') { if (node.ready && (node.preset === type || name.startsWith(type + '/') || name === type)) n++; return; }
        for (const [k, v] of Object.entries(node)) walk(v, k);
      }
    };
    walk(j, '');
    return n;
  } catch (e) { logger.warn(`pool status (${type}): ${(e.message || '').slice(0, 120)}`); return 0; }
}
function poolSizes() { return { sound: readyVibes('sound'), slowmo: readyVibes('slowmo') }; }
const poolMin = () => Number(process.env.NEWTYPE_POOL_MIN || 1);

function runCli(args, timeoutMin) {
  return new Promise(resolve => {
    execFile(python(), args, { timeout: timeoutMin * 60000, maxBuffer: 128 * 1024 * 1024,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', EDIT_POOL_DIR: poolRoot() } }, (err, stdout, stderr) => {
      if (stderr) process.stderr.write(String(stderr).slice(-3000));
      resolve({ err, stdout: String(stdout || '') });
    });
  });
}

async function runNewType({ type, outputDir, wantLong = false } = {}) {
  if (!CT[type]) throw new Error(`unknown new type "${type}"`);
  const work = path.join(outputDir, `work-${type}-${Date.now()}`);
  fs.mkdirSync(work, { recursive: true });
  const cli = CORE('vibe_edit.py');
  if (!fs.existsSync(cli)) throw new Error(`render CLI missing: ${cli}`);
  const args = [cli, '--type', type, '--out', work, ...(wantLong ? ['--landscape-long'] : []),
    ...(process.env.NEWTYPE_CLI_ARGS ? process.env.NEWTYPE_CLI_ARGS.split(' ').filter(Boolean) : [])];
  const t0 = Date.now();
  const { err } = await runCli(args, Number(process.env.NEWTYPE_TIMEOUT_MIN || (wantLong ? 75 : 30)));
  const rep = readJson(path.join(work, 'result.json'), null);
  if (!rep || rep.ok === false || err) {
    throw new Error(`${type} render failed: ${rep && rep.reason ? rep.reason : ((err && err.message) || 'no result').slice(0, 200)}`);
  }
  const abs = f => (f ? (path.isAbsolute(f) ? f : path.resolve(work, f)) : null);
  const videoPath = abs(rep.short);
  if (!videoPath || !fs.existsSync(videoPath)) throw new Error('the render CLI wrote no short');
  let longPath = abs(rep.long);
  if (longPath && !fs.existsSync(longPath)) longPath = null;
  logger.success(`${type}: rendered ${path.basename(videoPath)}${longPath ? ' + long' : ''} in ${Math.round((Date.now() - t0) / 1000)} s (vibe ${rep.vibe})`);

  const song = rep.song_title ? String(rep.song_title) : (rep.song && !/^s_/.test(String(rep.song)) ? String(rep.song) : null);
  const facts = { keyword: VIBE_KEYWORD[rep.vibe] || null, vibe: rep.vibe || null, song, source: null, theme: 'anime' };
  const meta = await optimizeMetadata({ channel: 'meme', contentType: CT[type], facts });
  logger.info(`Metadata: "${meta.title}"`);
  return {
    success: true, videoPath, longPath, resultJson: path.join(work, 'result.json'), title: meta.title,
    description: buildDescription(meta), tags: meta.tags,
    categoryId: '1', playlistTitle: PLAYLIST[type], comment: meta.comment, country: 'Global',
    category: `edit-${type}`, editType: 'edit', long: false, facts,
    meme: { song: song || rep.vibe || type, theme: 'anime', emoji: meta.emoji, clipIds: rep.clips_used || [], clipChannels: [],
      settings: { vibe: rep.vibe, duration: rep.duration_short, pool: type, render_s: rep.render_s }, onVideoTitle: meta.onVideoTitle,
      meta: meta.arms, metaChoice: meta.choice, contentType: CT[type], why: `rotation ${type}`, long: false, vibe: rep.vibe },
    workDir: work,
  };
}

/** idempotent: the render already marks its items; this repeats it from result.json (memory/edit-pool-used.json) */
function markUsed(result) {
  try {
    if (!result.resultJson || !fs.existsSync(CORE('vibe_pool.py'))) return;
    execFileSync(python(), [CORE('vibe_pool.py'), 'mark-used', '--result', result.resultJson],
      { timeout: 60000, env: { ...process.env, EDIT_POOL_DIR: poolRoot() } });
  } catch (e) { logger.warn(`mark-used: ${(e.message || '').slice(0, 120)}`); }
}

/** history (the daily guard + the rotation read it), pool ledger, decision log for the learners */
function recordUpload(result, upload) {
  const m = result.meme;
  const h = readJson(HISTORY_FILE(), { uploads: [] });
  h.uploads.push({
    videoId: upload.videoId, url: upload.url, postedAt: new Date().toISOString(), publishAt: upload.publishAt || null,
    contentType: m.contentType, song: m.song, theme: m.theme, emoji: m.emoji, title: result.title, clipIds: m.clipIds,
    clipChannels: m.clipChannels, settings: m.settings, onVideoTitle: m.onVideoTitle, meta: m.meta,
    vibe: m.vibe, longQueued: !!result.longPath || undefined,
  });
  fs.mkdirSync(MEM(), { recursive: true });
  fs.writeFileSync(HISTORY_FILE(), JSON.stringify(h, null, 1));
  markUsed(result);
  appendDecision({ vid: upload.videoId, ch: 'meme', title: result.title,
    arm: { contentType: m.contentType, song: m.song, source: null, theme: m.theme, vibe: m.vibe || null, ...(m.settings || {}), ...(m.meta || {}) },
    mode: m.why });
  if (m.metaChoice) recordMetadataChoice(m.metaChoice, { videoId: upload.videoId });
}

module.exports = { runNewType, recordUpload, readyVibes, poolSizes, poolMin, poolRoot, markUsed, CT };
