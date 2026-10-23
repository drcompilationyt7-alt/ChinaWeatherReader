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

/**
 * The owner-approved SOUND EDIT = sound_11b (2026-10-17, output/newtypes/s48; core/edit/refs/new-types-progress.md):
 * beat remake (the clip sounds rebuild the song's drum onsets), eye-approved pinned hits only, the references' rotated
 * 9:16 layout (+ rotate-phone hint, burned by watermark.py), no shake (vibe_edit default), zero-freeze remake defaults
 * (SOUND_REMAKE_STILL_*, TAIL_MAX, SUB_ORDER are the sound_edit.py defaults), allow / hit-block / remake-exclude lists
 * in core/edit/assets. A variable already set in the environment (repo variable, test) wins.
 */
const SOUND_ENV = {
  SOUND_BEAT_REMAKE: '1', SOUND_SRC_CAP: '2', SOUND_PREROLL_MAX: '1.1', SOUND_EXTRA_VIBES: 'crisp', SOUND_ROT_CAS: '0.7',
  SOUND_RATE_MIN: '1.1', SOUND_GAP_MAX: '1.35', SOUND_RATE_SHORT: '1.3', SOUND_VERTICAL_NATIVE: 'rot90', SOUND_VIS_DROP: '1',
  SOUND_PICTURE_FILL: '0', SOUND_AUDIBLE_MIN: '1.0', SOUND_VIS_LEAD_MIN: '0.4', SOUND_ALLOW_ONLY: '1',
  SOUND_MUSIC_POOL: '1', YT_JS_RUNTIMES: 'node',   // song + section from core/edit/music_pool.py (falls back to the vibe songs)
  EDIT_NO_SHAKE: '1', EDIT_ROTATE_HINT: '1', EDIT_POOL_REVIEWED_ONLY: '1', SOUND_COOLDOWN_VIDEOS: '3', SOUND_COOLDOWN_RELAX: '1',
  EDIT_GPU: '0',
};
/**
 * The SLOW-MO edit (cinematic_slowmo; core/edit/cine_vibe.py + core/edit/judge_slowmo.py; new-types-progress.md "Slow-mo go-live"):
 * the references' rotated 9:16 layout (the full 16:9 master turned 90 deg, the planner's 'rot90' frame) + the upright rotate-phone
 * corner hint (watermark.py), no shake, the song's sung lyrics (fetched + aligned at render time, never stored), the song SLOWED
 * from the slow-mo music pool (core/edit/slowmo_music.py: emotional / chill famous songs, hook section, 30-day cooldown), eye-reviewed
 * clips only, clips reused across videos after SLOWMO_COOLDOWN_VIDEOS. CINE_SEGMENT_S renders the engine in ~12 s parts (one process
 * each: the whole-short engine peaks ~3.4 GB resident; parts stay ~2 GB) - the GitHub runner (16 GB) fits either way.
 */
const SLOWMO_ENV = {
  SLOWMO_MUSIC_POOL: '1', YT_JS_RUNTIMES: 'node', LYRICS_NO_STORE: '1', CINE_LYRICS: '1',
  EDIT_NO_SHAKE: '1', EDIT_ROTATE_HINT: '1', EDIT_POOL_REVIEWED_ONLY: '1', SLOWMO_COOLDOWN_VIDEOS: '1',
  CINE_SEGMENT_S: '12', EDIT_PREFETCH: '0', EDIT_GPU: '0',
  LYRICS_WHISPER: 'medium',   // 2026-10-23: the lyric check / head-line pins need the judge's model (base misheard blue's intro line)
};
const typeEnv = type => {
  const base = type === 'sound' ? SOUND_ENV : (type === 'slowmo' ? SLOWMO_ENV : { EDIT_NO_SHAKE: '1', EDIT_GPU: '0' });
  const out = {};
  for (const [k, v] of Object.entries(base)) out[k] = process.env[k] !== undefined && process.env[k] !== '' ? process.env[k] : v;
  return out;
};

function runCli(args, timeoutMin, extraEnv = {}) {
  return new Promise(resolve => {
    execFile(python(), args, { timeout: timeoutMin * 60000, maxBuffer: 128 * 1024 * 1024,
      env: { ...process.env, ...extraEnv, PYTHONIOENCODING: 'utf-8', EDIT_POOL_DIR: poolRoot() } }, (err, stdout, stderr) => {
      if (stderr) process.stderr.write(String(stderr).slice(process.env.DRY_RUN === 'true' ? -60000 : -3000));   // a dry run keeps the whole story
      resolve({ err, stdout: String(stdout || '') });
    });
  });
}

/**
 * Pre-upload guard (owner 2026-10-20 after the empty quiz short: never post an empty / broken video). Returns the problems
 * (empty = ok). Every new type: the file, its length, the shot count of the plan, sampled frames not blank / static
 * (core/quiz/qa_guard.py). Slow-mo also needs a judge_slowmo PASS (cut timing incl. the wave / no-SFX / hold checks, beauty,
 * lyrics, mix, layout) - NEWTYPE_JUDGE=off skips the judge (tests only). runNewType throws on a problem -> the runner posts stem.
 */
const GUARD = { sound: { len: [15, 75], shots: 15 }, slowmo: { len: [25, 50], shots: 12 } };
function guardRender(type, rep, videoPath, work) {
  const g = GUARD[type] || { len: [10, 90], shots: 5 };
  const bad = [];
  let size = 0;
  try { size = fs.statSync(videoPath).size; } catch {}
  if (size < 200 * 1024) bad.push(`video file ${size} bytes`);
  const plan = rep.plan_short ? readJson(path.isAbsolute(rep.plan_short) ? rep.plan_short : path.resolve(work, rep.plan_short), null) : null;
  const nShots = plan && Array.isArray(plan.shots) ? plan.shots.length - (type === 'slowmo' ? 1 : 0) : 0;   // slow-mo: minus the words-on-black intro
  if (nShots < g.shots) bad.push(`${nShots} shots (< ${g.shots})`);
  if ((rep.clips_short || []).length < Math.ceil(g.shots / 2)) bad.push(`${(rep.clips_short || []).length} distinct clips`);
  if (rep.fresh_override) {
    // vibe_edit --fresh-override: a slow-mo TIMING TEST that may repeat the last videos' clips; a dry run shows it, nothing posts it
    if (process.env.DRY_RUN === 'true') logger.warn(`${type}: fresh-clip override (timing test, dry run only)`);
    else bad.push('fresh-clip override (timing test only, never posted)');
  }
  if (type === 'sound' && rep.clip_rotation && rep.clip_rotation.reuse_share != null) {
    // owner 2026-10-22 "no variety and good match need both": <= SOUND_REUSE_CAP (30 %) of the shots from the last 5 sound videos
    const cap = Number(process.env.SOUND_REUSE_CAP || 0.30);
    if (rep.clip_rotation.reuse_share > cap + 1e-6) bad.push(`pool too small: reuse ${Math.round(rep.clip_rotation.reuse_share * 100)} % > ${Math.round(cap * 100)} %`);
  }
  try {
    const out = execFileSync(python(), [path.join(ROOT, 'core', 'quiz', 'qa_guard.py'), '--video', videoPath, '--samples', '16'],
      { timeout: 300000, encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    const v = JSON.parse(out.trim().split('\n').pop()).video || {};
    if (!(v.duration >= g.len[0] && v.duration <= g.len[1])) bad.push(`length ${v.duration} s (expected ${g.len[0]}-${g.len[1]})`);
    if ((v.sampled || 0) < 12) bad.push(`only ${v.sampled || 0} frames decoded`);
    if ((v.blankShare || 0) > 0.2) bad.push(`blank frames ${v.blankShare}`);
    if ((v.staticShare || 0) > 0.4) bad.push(`static frames ${v.staticShare}`);
  } catch (e) { bad.push(`video check failed: ${(e.message || '').slice(0, 120)}`); }
  if (type === 'slowmo' && process.env.NEWTYPE_JUDGE !== 'off') {
    const jd = path.join(work, 'judge');
    try {
      const args = [CORE('judge_slowmo.py'), videoPath, jd, '--label', path.basename(videoPath), ...(plan ? ['--plan', path.resolve(work, rep.plan_short)] : [])];
      execFileSync(python(), args, { timeout: 40 * 60000, encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' }, maxBuffer: 64 * 1024 * 1024 });
    } catch (e) { logger.warn(`judge_slowmo: ${(e.message || '').slice(0, 200)}`); }
    const jr = readJson(path.join(jd, 'report.json'), null);
    if (!jr) bad.push('judge_slowmo wrote no report');
    else {
      logger.info(`judge_slowmo: overall ${jr.overall} ${jr.pass ? 'PASS' : 'NOT PASS'} ${JSON.stringify(jr.scores || {})}`);
      if (!jr.pass) bad.push(`judge_slowmo NOT PASS (overall ${jr.overall})`);
    }
  }
  return bad;
}

async function runNewType({ type, outputDir, wantLong = false } = {}) {
  if (!CT[type]) throw new Error(`unknown new type "${type}"`);
  if (type === 'sound') musicPoolFeedback();
  if (type === 'slowmo') musicPoolFeedback('slowmo');
  const work = path.join(outputDir, `work-${type}-${Date.now()}`);
  fs.mkdirSync(work, { recursive: true });
  const cli = CORE('vibe_edit.py');
  if (!fs.existsSync(cli)) throw new Error(`render CLI missing: ${cli}`);
  const args = [cli, '--type', type, '--out', work, ...(wantLong ? ['--landscape-long'] : []),
    ...(process.env.DRY_RUN === 'true' ? ['--no-mark'] : []),   // a dry run never touches the pool ledger
    ...(process.env.NEWTYPE_CLI_ARGS ? process.env.NEWTYPE_CLI_ARGS.split(' ').filter(Boolean) : [])];
  const t0 = Date.now();
  const tenv = typeEnv(type);
  logger.info(`${type} render env: ${Object.entries(tenv).map(([k, v]) => `${k}=${v}`).join(' ')}`);
  const { err } = await runCli(args, Number(process.env.NEWTYPE_TIMEOUT_MIN || (wantLong ? 90 : 45)), tenv);
  const rep = readJson(path.join(work, 'result.json'), null);
  if (rep && rep.ok === false && /^pool_too_small/.test(String(rep.reason || ''))) {
    // the pool cannot give variety (reuse cap) AND good matches today: no sound video, the runner posts stem
    logger.warn(`${type}: pool too small -> stem fallback (${String(rep.reason).slice(0, 200)})`);
    throw new Error(`${type} pool too small: ${String(rep.reason).slice(0, 300)}`);
  }
  if (!rep || rep.ok === false || err) {
    throw new Error(`${type} render failed: ${rep && rep.reason ? rep.reason : ((err && err.message) || 'no result').slice(0, 200)}`);
  }
  const abs = f => (f ? (path.isAbsolute(f) ? f : path.resolve(work, f)) : null);
  const videoPath = abs(rep.short);
  if (!videoPath || !fs.existsSync(videoPath)) throw new Error('the render CLI wrote no short');
  const problems = guardRender(type, rep, videoPath, work);
  if (problems.length) throw new Error(`${type} pre-upload guard: ${problems.join('; ')}`);
  logger.success(`${type}: pre-upload guard ok`);
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
      meta: meta.arms, metaChoice: meta.choice, contentType: CT[type], why: `rotation ${type}`, long: false, vibe: rep.vibe,
      musicPoolId: rep.music_pool ? rep.song : null, songSection: rep.song_section || null },
    workDir: work,
  };
}

/** a small python call into core/edit/music_pool.py (sound) or core/edit/slowmo_music.py (slow-mo), as MP (never throws; the pool
 * file is committed by the workflow) */
function musicPy(code, what, type = 'sound') {
  const mod = type === 'slowmo' ? 'slowmo_music' : 'music_pool';
  try {
    execFileSync(python(), ['-c', `import sys; sys.path.insert(0, ${JSON.stringify(path.join(ROOT, 'core', 'edit'))}); import ${mod} as MP; ${code}`],
      { timeout: 60000, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    return true;
  } catch (e) { logger.warn(`${what}: ${(e.message || '').slice(0, 160)}`); return false; }
}

/**
 * Music-pool learning: every sound upload whose stats are >= 7 days old feeds its lscore to music_pool.record_performance once
 * (2 scores end a song's probation); then retire_underperformers(pool, channel mean) retires auto-added songs that stay below.
 */
function musicPoolFeedback(type = 'sound') {
  try {
    const h = readJson(HISTORY_FILE(), { uploads: [] });
    const stats = readJson(path.join(MEM(), 'performance-stats.json'), { videos: [] });
    const byId = new Map((stats.videos || []).map(v => [v.videoId, v]));
    const scored = (h.uploads || []).map(u => ({ u, s: byId.get(u.videoId) })).filter(x => x.s && typeof x.s.lscore === 'number' && (x.s.ageDays || 0) >= 7);
    const todo = scored.filter(x => x.u.musicPoolId && x.u.musicPerf === undefined && x.u.contentType === CT[type]);
    if (!todo.length) return;
    const mean = scored.reduce((a, x) => a + x.s.lscore, 0) / scored.length;
    const calls = todo.map(x => `MP.record_performance(${JSON.stringify(x.u.musicPoolId)}, ${Number(x.s.lscore)})`).join('; ');
    if (musicPy(`${calls}; p = MP.load_pool(); n = MP.retire_underperformers(p, ${mean}); MP.save_pool(p); print(n)`, 'music pool feedback', type)) {
      for (const x of todo) x.u.musicPerf = x.s.lscore;
      fs.writeFileSync(HISTORY_FILE(), JSON.stringify(h, null, 1));
      logger.info(`music pool: ${todo.length} upload score(s) recorded`);
    }
  } catch (e) { logger.warn(`music pool feedback: ${(e.message || '').slice(0, 160)}`); }
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
    musicPoolId: m.musicPoolId || undefined, songSection: m.songSection || undefined,
  });
  fs.mkdirSync(MEM(), { recursive: true });
  fs.writeFileSync(HISTORY_FILE(), JSON.stringify(h, null, 1));
  markUsed(result);
  if (m.musicPoolId) musicPy(`MP.mark_used(${JSON.stringify(m.musicPoolId)})`, 'music pool mark_used', m.contentType === CT.slowmo ? 'slowmo' : 'sound');
  appendDecision({ vid: upload.videoId, ch: 'meme', title: result.title,
    arm: { contentType: m.contentType, song: m.song, source: null, theme: m.theme, vibe: m.vibe || null, ...(m.settings || {}), ...(m.meta || {}) },
    mode: m.why });
  if (m.metaChoice) recordMetadataChoice(m.metaChoice, { videoId: upload.videoId });
}

module.exports = { guardRender, runNewType, recordUpload, readyVibes, poolSizes, poolMin, poolRoot, markUsed, musicPoolFeedback, typeEnv, SOUND_ENV, SLOWMO_ENV, CT };
