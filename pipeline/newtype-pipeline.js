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
 * (core/quiz/qa_guard.py). The quality judges (judge_slowmo / judge_sound) run in judgeRender, inside the repair loop.
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
  if (type === 'sound' && rep.clip_rotation && rep.clip_rotation.reuse_share != null && !(rep.fresh_override && process.env.DRY_RUN === 'true')) {
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
  return bad;
}

/**
 * The quality judge of the rendered file + what to change (core/edit/judge_repair.py). Slow-mo: judge_slowmo (every criterion
 * >= 8, overall >= 8.5; lyrics per word, waves / sync / holds / groove). Sound: judge_sound against the FB15 reference
 * (cached measures core/edit/refs/sound-judge-ref-FB15TNFAHVU.json: the reference video is not on the runner), the owner's
 * build-up pre-roll (JUDGE_PRE_HI_MS 800), strict PASS (every criterion >= 8) AND mean >= SOUND_JUDGE_MIN (9.0).
 * NEWTYPE_JUDGE=off skips it (tests only). -> {pass, overall, failed, action, repairPath, jd, why}
 */
function judgeRender(type, rep, videoPath, work, tag) {
  if (process.env.NEWTYPE_JUDGE === 'off') return { pass: true, overall: null, action: 'none', skipped: true };
  const jd = path.join(work, 'judge');
  const planPath = rep.plan_short ? (path.isAbsolute(rep.plan_short) ? rep.plan_short : path.resolve(work, rep.plan_short)) : null;
  const env = { ...process.env, PYTHONIOENCODING: 'utf-8' };
  try {
    if (type === 'slowmo') {
      execFileSync(python(), [CORE('judge_slowmo.py'), videoPath, jd, '--label', path.basename(videoPath), ...(planPath ? ['--plan', planPath] : [])],
        { timeout: 45 * 60000, encoding: 'utf8', env, maxBuffer: 64 * 1024 * 1024 });
    } else {
      execFileSync(python(), [CORE('judge_sound.py'), videoPath, path.join(ROOT, 'edit-refs', 'sound', 'FB15TNFAHVU.mp4'), jd,
        ...(planPath ? ['--plan', planPath] : []), '--label', tag || 'sound',
        '--ref-cache', path.join(ROOT, 'core', 'edit', 'refs', 'sound-judge-ref-FB15TNFAHVU.json'), '--per-sec', '8'],
      { timeout: 45 * 60000, encoding: 'utf8', env: { ...env, JUDGE_PRE_HI_MS: process.env.JUDGE_PRE_HI_MS || '800' }, maxBuffer: 64 * 1024 * 1024 });
    }
  } catch (e) { logger.warn(`judge (${type}): ${(e.message || '').slice(0, 300)}`); }
  const report = path.join(jd, 'report.json');
  const repairPath = path.join(work, 'repair.json');
  try {
    execFileSync(python(), [CORE('judge_repair.py'), '--type', type, '--report', report, '--out', repairPath],
      { timeout: 120000, encoding: 'utf8', env });
  } catch (e) { logger.warn(`judge_repair: ${(e.message || '').slice(0, 200)}`); }
  const r = readJson(repairPath, null) || { pass: false, action: 'reseed', why: ['no repair verdict'] };
  r.repairPath = repairPath;
  r.jd = jd;
  logger.info(`judge ${type} ${tag || ''}: ${r.pass ? 'PASS' : 'NOT PASS'} overall ${r.overall} failed ${JSON.stringify(r.failed || {})} -> ${r.action}`);
  return r;
}

/** time left for another attempt: NEWTYPE_BUDGET_MIN from the start of the loop, and (scheduled runs) a UTC clock deadline that
 * leaves the stem fallback + the upload well before PUBLISH_HOUR_UTC */
function timeLeft(t0, lastMin) {
  const budget = Number(process.env.NEWTYPE_BUDGET_MIN || 210);
  const used = (Date.now() - t0) / 60000;
  if (used + lastMin * 1.15 > budget) return false;
  const dl = String(process.env.NEWTYPE_DEADLINE_UTC || '08:30');
  if (process.env.DRY_RUN !== 'true' && /^\d{1,2}:\d{2}$/.test(dl)) {
    const [h, m] = dl.split(':').map(Number);
    const now = new Date();
    const end = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), h, m);
    if (Date.now() + lastMin * 1.15 * 60000 > end) return false;
  }
  return true;
}

/** the attempts of a new-type day: job summary (always), memory/newtype-repairs.jsonl (always), and on the last-resort stem
 * fallback memory/newtype-fallback.json + a GitHub issue with the failing judge verdicts (the coordinator fixes the cause) */
async function reportAttempts(type, attempts, ok, reason) {
  const lines = [`### ${type}: ${ok ? 'PASS' : 'FAILED -> stem fallback'} after ${attempts.length} attempt(s)${reason ? ` (${reason})` : ''}`, '',
    '| # | song | seed | input | minutes | result | overall | failed criteria | next |', '|---|---|---|---|---|---|---|---|---|'];
  for (const a of attempts) {
    const failed = Object.entries(a.failed || {}).map(([k, v]) => `${k} ${v}`).join(', ') || '-';
    lines.push(`| ${a.n} | ${a.song || '-'} | ${a.seed ?? '-'} | ${a.input || 'first'} | ${a.min ?? '-'} | ${a.result || '-'} | ${a.overall ?? '-'} | ${failed} | ${a.next || '-'} |`);
  }
  const md = lines.join('\n') + '\n';
  if (process.env.GITHUB_STEP_SUMMARY) { try { fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, md + '\n'); } catch {} }
  logger.info(md);
  const rec = { at: new Date().toISOString(), type, ok, reason: reason || null, dryRun: process.env.DRY_RUN === 'true', attempts };
  try { fs.mkdirSync(MEM(), { recursive: true }); fs.appendFileSync(path.join(MEM(), 'newtype-repairs.jsonl'), JSON.stringify(rec) + '\n'); } catch {}
  if (ok) return;
  try { fs.writeFileSync(path.join(MEM(), 'newtype-fallback.json'), JSON.stringify(rec, null, 1)); } catch {}
  const repo = process.env.GITHUB_REPOSITORY;
  const token = process.env.GITHUB_TOKEN;
  if (!repo || !token || process.env.DRY_RUN === 'true' || typeof fetch !== 'function') return;
  const run = process.env.GITHUB_RUN_ID ? `https://github.com/${repo}/actions/runs/${process.env.GITHUB_RUN_ID}` : '';
  try {
    const r = await fetch(`https://api.github.com/repos/${repo}/issues`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: `${type} edit fell back to stem on ${rec.at.slice(0, 10)}`,
        body: `The daily ${type} edit failed the judge after ${attempts.length} attempt(s); the day posted a stem edit.\n\nRun: ${run}\n\n${md}\n`
          + 'Judge reports: the run artifact "renders" (output/meme/work-*/judge). Fix the root cause next session.' }),
    });
    logger.info(`fallback issue: HTTP ${r.status}`);
  } catch (e) { logger.warn(`fallback issue: ${(e.message || '').slice(0, 160)}`); }
}

const stripArgs = (args, names) => {
  const out = [];
  for (let i = 0; i < args.length; i++) {
    if (names.includes(args[i])) { i++; continue; }
    out.push(args[i]);
  }
  return out;
};
const nextSeed = (s, k) => (Number.isFinite(Number(s)) ? Number(s) : 7) + k;

/**
 * One new-type video, repaired until the judge passes (owner 2026-10-23: "if the github when creating the slow mo edit is not
 * good we have to fix it, we must publish a video every day"). Attempt 1 renders as before; a NOT PASS feeds the judge's
 * measured failures back (core/edit/judge_repair.py): 'retime' (lyric words re-timed) / 'recut' (switches moved / dropped /
 * added) keep the song + seed and re-render only the changed parts (CINE_PART_CACHE); 'reseed' = another clip order;
 * 'new_song' = another song (SLOWMO_EXCLUDE_SONGS). At most NEWTYPE_MAX_ATTEMPTS (5) within NEWTYPE_BUDGET_MIN (210 min) and
 * before NEWTYPE_DEADLINE_UTC (08:30, scheduled runs); a pool that is too small stops at once. Throws when nothing passed ->
 * the runner posts the stem edit (last resort) and reportAttempts files the issue. Every attempt renders with --no-mark: only
 * the posted video marks its clips / song (recordUpload).
 */
async function runNewType({ type, outputDir, wantLong = false } = {}) {
  if (!CT[type]) throw new Error(`unknown new type "${type}"`);
  if (type === 'sound') musicPoolFeedback();
  if (type === 'slowmo') musicPoolFeedback('slowmo');
  const cli = CORE('vibe_edit.py');
  if (!fs.existsSync(cli)) throw new Error(`render CLI missing: ${cli}`);
  const maxAttempts = Number(process.env.NEWTYPE_MAX_ATTEMPTS || 5);
  const userArgs = process.env.NEWTYPE_CLI_ARGS ? process.env.NEWTYPE_CLI_ARGS.split(' ').filter(Boolean) : [];
  const partCache = path.join(outputDir, `partcache-${type}`);
  const tenv = typeEnv(type);
  logger.info(`${type} render env: ${Object.entries(tenv).map(([k, v]) => `${k}=${v}`).join(' ')}`);
  const tLoop = Date.now();
  const attempts = [];
  const excluded = [];
  let song = null;
  let seed = null;
  let repair = null;          // {moves, avoid, need, lyric_t0}: the next attempt on the same song + seed
  let input = 'first';
  let sameSong = 0;
  let lastMin = 30;
  let stopReason = null;
  for (let n = 1; n <= maxAttempts; n++) {
    if (n > 1 && !timeLeft(tLoop, lastMin)) { stopReason = 'time budget'; break; }
    const t0 = Date.now();
    const work = path.join(outputDir, `work-${type}-${Date.now()}`);
    fs.mkdirSync(work, { recursive: true });
    let extra = userArgs;
    if (song) extra = stripArgs(extra, ['--song']);
    if (seed != null) extra = stripArgs(extra, ['--seed']);
    const args = [cli, '--type', type, '--out', work, ...(wantLong ? ['--landscape-long'] : []), '--no-mark', ...extra,
      ...(song ? ['--song', String(song)] : []), ...(seed != null ? ['--seed', String(seed)] : [])];
    const env = { ...tenv, CINE_PART_CACHE: partCache, SLOWMO_EXCLUDE_SONGS: excluded.join(','), SOUND_EXCLUDE_SONGS: excluded.join(',') };
    if (repair && type === 'slowmo') {
      const rp = path.join(work, 'repair-in.json');
      fs.writeFileSync(rp, JSON.stringify(repair));
      env.SLOWMO_REPAIR = rp;
    }
    const att = { n, song, seed, input, work: path.basename(work) };
    attempts.push(att);
    logger.info(`${type} attempt ${n}/${maxAttempts}: ${input}${song ? ` song ${song}` : ''}${seed != null ? ` seed ${seed}` : ''}`);
    const { err } = await runCli(args, Number(process.env.NEWTYPE_TIMEOUT_MIN || (wantLong ? 90 : 45)), env);
    const rep = readJson(path.join(work, 'result.json'), null);
    att.min = Math.round((Date.now() - t0) / 6000) / 10;
    if (rep) { att.song = rep.song || song; att.seed = rep.seed ?? seed; }
    if (rep && rep.ok === false && /^pool_too_small/.test(String(rep.reason || ''))) {
      // the pool cannot give variety (reuse cap) AND good matches today: no repair can fix it -> stem now
      att.result = 'pool too small';
      stopReason = `pool too small: ${String(rep.reason).slice(0, 160)}`;
      break;
    }
    if (!rep || rep.ok === false || err) {
      const why = rep && rep.reason ? String(rep.reason) : ((err && err.message) || 'no result');
      att.result = `render failed: ${why.slice(0, 120)}`;
      lastMin = Math.max(lastMin, att.min);
      if (/^pool_low/.test(why) || (/^no_song/.test(why) && !att.song)) { stopReason = why.slice(0, 160); break; }
      // a song-level failure (no lyrics / no audio / no layout for its waves) -> another song; anything else -> another seed
      if (/no_lyrics|no audio|no_song|no wave|bad plan|pool_low|only \d+ usable/.test(why) && att.song) {
        excluded.push(String(att.song).split('@')[0]); song = null; sameSong = 0; seed = nextSeed(att.seed, 211); att.next = 'new_song';
      } else { song = att.song || song; seed = nextSeed(att.seed, 101); sameSong++; att.next = 'reseed'; }
      repair = null; input = att.next;
      continue;
    }
    const abs = f => (f ? (path.isAbsolute(f) ? f : path.resolve(work, f)) : null);
    const videoPath = abs(rep.short);
    if (!videoPath || !fs.existsSync(videoPath)) {
      att.result = 'no short written'; song = att.song; seed = nextSeed(att.seed, 101); repair = null; input = att.next = 'reseed'; continue;
    }
    const problems = guardRender(type, rep, videoPath, work);
    if (problems.some(p => /fresh-clip override/.test(p))) { att.result = problems.join('; '); stopReason = 'fresh-clip override outside a dry run'; break; }
    if (problems.some(p => /pool too small/.test(p))) { att.result = problems.join('; '); stopReason = 'pool too small (reuse)'; break; }
    if (problems.length) {
      att.result = `guard: ${problems.join('; ').slice(0, 160)}`;
      song = att.song; seed = nextSeed(att.seed, 101); repair = null; input = att.next = 'reseed'; sameSong++;
      lastMin = Math.max(lastMin, att.min);
      continue;
    }
    const J = judgeRender(type, rep, videoPath, work, `${type}-a${n}`);
    att.min = Math.round((Date.now() - t0) / 6000) / 10;
    att.overall = J.overall; att.failed = J.failed; att.result = J.pass ? 'PASS' : 'NOT PASS';
    lastMin = input === 'retime' ? Math.max(10, att.min) : Math.max(15, att.min);
    if (J.pass) {
      await reportAttempts(type, attempts, true);
      logger.success(`${type}: judge PASS on attempt ${n} (overall ${J.overall})`);
      return finishNewType(type, rep, work, videoPath, abs, t0, attempts);
    }
    // what next: the judge's own repair first (same song + seed), then another clip order, then another song
    song = att.song; seed = att.seed;
    let action = J.action || 'reseed';
    sameSong++;
    if ((action === 'retime' || action === 'recut') && (sameSong >= 3 || type !== 'slowmo')) action = 'reseed';
    if (action === 'reseed' && sameSong >= 3) action = 'new_song';
    if (action === 'retime' || action === 'recut') {
      const prev = repair || {};
      const jr = readJson(J.repairPath, {}) || {};
      repair = { moves: [...(prev.moves || []), ...(jr.moves || [])], avoid: [...(prev.avoid || []), ...(jr.avoid || [])],
        need: [...(prev.need || []), ...(jr.need || [])], lyric_t0: { ...(prev.lyric_t0 || {}), ...(jr.lyric_t0 || {}) } };
    } else if (action === 'reseed') {
      seed = nextSeed(seed, 101); repair = null;
    } else {
      if (song) excluded.push(String(song).split('@')[0]);
      song = null; seed = nextSeed(seed, 211); repair = null; sameSong = 0;
    }
    att.next = action; input = action;
  }
  await reportAttempts(type, attempts, false, stopReason || 'attempts used up');
  throw new Error(`${type}: no attempt passed the judge (${attempts.map(a => `${a.n}: ${a.result}`).join(' | ').slice(0, 400)})`);
}

async function finishNewType(type, rep, work, videoPath, abs, t0, attempts) {
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
      settings: { vibe: rep.vibe, duration: rep.duration_short, pool: type, render_s: rep.render_s, attempts: attempts.length },
      onVideoTitle: meta.onVideoTitle, meta: meta.arms, metaChoice: meta.choice, contentType: CT[type], why: `rotation ${type}`,
      long: false, vibe: rep.vibe, musicPoolId: rep.music_pool ? rep.song : null, songSection: rep.song_section || null },
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
