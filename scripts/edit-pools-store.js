/**
 * Durable store of the sound / slowmo vibe pools (core/edit/refs/pool-format.md): a GitHub RELEASE, not git (the media
 * is GBs) and not an Actions cache (evictable). One tar per vibe (<preset>/<vibe>/: the manifests, every json, and only
 * the media files the manifests reference; "sheets" left out) + index.json. A vibe tar above PART_MB (1500 MB) is
 * uploaded as parts pool-<preset>-<vibe>.tar.part00, .part01 ... (each well under the 2 GiB per-file release limit);
 * pull concatenates them again.
 *
 *   node scripts/edit-pools-store.js pull [--dir DIR] [--manifests-only] [--preset P]
 *                                  download what changed since the last pull into DIR
 *                                  (--manifests-only: extract only clips/sounds/vibe .json, no media; for CI shards)
 *   node scripts/edit-pools-store.js push [--dir DIR] [--preset P]   upload every vibe dir of DIR (or only preset P), remove
 *                                  release assets of vibes (of that preset) that are gone and stale parts
 *   node scripts/edit-pools-store.js push ... --prune-local   after the upload, VERIFY every uploaded file on the release
 *                                  (size + sha256: the API digest, else a download-back) and only then delete the local
 *                                  media of that vibe that went into the tar (except a 3-clip sample; every .json stays),
 *                                  plus DIR/_src and _lib; manifests / index.json / the blocklist stay (owner storage rule 2026-10-13)
 *   node scripts/edit-pools-store.js list
 *
 * DIR = $EDIT_POOL_DIR (default output/edit-pools). Release tag = $EDIT_POOLS_TAG (default "edit-pools").
 * Auth: gh with GH_TOKEN (the workflows pass GH_PAT / the job token; nothing is written to disk). Locally:
 * GH_TOKEN=$(gh auth token -u <owner account>) node scripts/edit-pools-store.js push --preset sound --prune-local
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execFileSync } = require('child_process');

const ROOT = path.join(__dirname, '..');
const TAG = process.env.EDIT_POOLS_TAG || 'edit-pools';
const args = process.argv.slice(2);
const cmd = args[0];
const opt = k => (args.includes(k) ? args[args.indexOf(k) + 1] : null);
const dir = path.resolve(ROOT, opt('--dir') || process.env.EDIT_POOL_DIR || 'output/edit-pools');
const onlyPreset = opt('--preset');
const PART = Number(process.env.EDIT_POOLS_PART_MB || 1500) * 1048576;
const repoSlug = process.env.GITHUB_REPOSITORY || (() => {
  try {
    const u = execFileSync('git', ['remote', 'get-url', 'origin'], { cwd: ROOT, encoding: 'utf8' }).trim();
    const m = /github\.com[/:]([^/]+\/[^/.]+)/.exec(u);
    return m ? m[1] : null;
  } catch { return null; }
})();
const repo = repoSlug ? ['--repo', repoSlug] : [];
const env = { ...process.env, GH_TOKEN: process.env.GH_PAT || process.env.GH_TOKEN || process.env.GITHUB_TOKEN || '' };
if (!env.GH_TOKEN) delete env.GH_TOKEN;   // local: the gh login
const gh = (a, o = {}) => execFileSync('gh', a, { cwd: ROOT, env, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'], ...o });
// upload with retries: --clobber deletes the old asset first, so a one-off GitHub 5xx (2026-10-26) would leave the pool missing
function upload(file) {
  for (let k = 1; ; k++) {
    try { return gh(['release', 'upload', TAG, file, ...repo, '--clobber']); } catch (e) {
      if (k >= 4) throw e;
      console.warn(`upload ${path.basename(file)} failed (try ${k}/4): ${String(e.stderr || e.message).trim().split('\n').pop()}; retry in ${20 * k} s`);
      execFileSync(process.execPath, ['-e', `setTimeout(() => {}, ${20000 * k})`]);
    }
  }
}
const STAMP = path.join(dir, '.store-stamp.json');
const manifestsOnly = args.includes('--manifests-only');
const pruneLocal = args.includes('--prune-local');
const PRESETS = ['sound', 'slowmo', 'ranking'];   // ranking: one 'vibe' dir ranking/pool (clips.json + clips/), 2026-10-25
// Windows: the system bsdtar (Git's GNU tar reads "C:" in a path as a remote host)
const WIN_TAR = path.join(process.env.SystemRoot || 'C:/Windows', 'System32', 'tar.exe');
const TAR = process.platform === 'win32' && fs.existsSync(WIN_TAR) ? WIN_TAR : 'tar';
const ASSET_RE = /^pool-([a-z0-9]+)-(.+?)\.tar(?:\.part(\d+))?$/;

function assets() {
  try { return JSON.parse(gh(['release', 'view', TAG, ...repo, '--json', 'assets'])).assets || []; } catch { return null; }
}
/** release assets from the REST API (has the sha256 "digest" GitHub computes for every upload) */
function apiAssets() {
  try {
    const r = JSON.parse(gh(['api', `repos/${repoSlug}/releases/tags/${TAG}`]));
    return r.assets || [];
  } catch { return []; }
}
function vibes() {
  const out = [];
  for (const preset of PRESETS) {
    if (onlyPreset && preset !== onlyPreset) continue;
    const p = path.join(dir, preset);
    if (!fs.existsSync(p)) continue;
    for (const v of fs.readdirSync(p)) if (fs.statSync(path.join(p, v)).isDirectory()) out.push({ preset, vibe: v });
  }
  return out;
}
function sha256File(f) {
  const h = crypto.createHash('sha256');
  const fd = fs.openSync(f, 'r');
  const buf = Buffer.alloc(16 * 1048576);
  try { let n; while ((n = fs.readSync(fd, buf, 0, buf.length, null)) > 0) h.update(buf.subarray(0, n)); } finally { fs.closeSync(fd); }
  return h.digest('hex');
}
function concat(parts, out) {
  const fd = fs.openSync(out, 'w');
  const buf = Buffer.alloc(16 * 1048576);
  try {
    for (const p of parts) {
      const fi = fs.openSync(p, 'r');
      try { let n; while ((n = fs.readSync(fi, buf, 0, buf.length, null)) > 0) fs.writeSync(fd, buf, 0, n); } finally { fs.closeSync(fi); }
      fs.rmSync(p, { force: true });
    }
  } finally { fs.closeSync(fd); }
}
function split(file, size) {
  const total = fs.statSync(file).size;
  if (total <= size) return [file];
  const parts = [];
  const fi = fs.openSync(file, 'r');
  const buf = Buffer.alloc(16 * 1048576);
  try {
    for (let k = 0, off = 0; off < total; k++) {
      const pf = `${file}.part${String(k).padStart(2, '0')}`;
      const fo = fs.openSync(pf, 'w');
      let left = Math.min(size, total - off);
      try {
        while (left > 0) {
          const n = fs.readSync(fi, buf, 0, Math.min(buf.length, left), off);
          fs.writeSync(fo, buf, 0, n);
          off += n; left -= n;
        }
      } finally { fs.closeSync(fo); }
      parts.push(pf);
    }
  } finally { fs.closeSync(fi); }
  fs.rmSync(file, { force: true });
  return parts;
}
/** files of one vibe that go into its tar: every json + the media its manifests reference (no orphans, no sheets) */
function vibeFiles(preset, vibe) {
  const vd = path.join(dir, preset, vibe);
  const rel = f => `${preset}/${vibe}/${f}`.replace(/\\/g, '/');
  const out = new Set();
  const walk = (d, r) => {
    for (const f of fs.readdirSync(d)) {
      if (r === '' && f === 'sheets') continue;
      const p = path.join(d, f);
      if (fs.statSync(p).isDirectory()) walk(p, r + f + '/');
      else if (f.endsWith('.json')) out.add(rel(r + f));
    }
  };
  walk(vd, '');
  const missing = [];
  for (const kind of ['clips', 'sounds']) {
    let m = null;
    try { m = JSON.parse(fs.readFileSync(path.join(vd, `${kind}.json`), 'utf8')); } catch { continue; }
    for (const it of m.items || []) {
      let f = it.file ? String(it.file).replace(/\\/g, '/') : null;
      if (kind === 'sounds') {   // songs: the vibe's own sounds/<id>.* copy (the manifest 'file' is an owner-PC path)
        const sd = path.join(vd, 'sounds');
        const h = fs.existsSync(sd) ? fs.readdirSync(sd).find(x => x.startsWith(it.id + '.') && !x.endsWith('.json')) : null;
        f = h ? `sounds/${h}` : null;
      }
      if (!f || path.isAbsolute(f) || /^[A-Za-z]:/.test(f)) continue;
      if (it.status === 'cine_reject') continue;   // slow-mo: the planner's hard gates reject it (never shown): manifest row only
      if (fs.existsSync(path.join(vd, f))) out.add(rel(f)); else if (it.status !== 'rejected') missing.push(f);
    }
  }
  if (missing.length) console.warn(`${preset}/${vibe}: ${missing.length} referenced media file(s) missing locally (e.g. ${missing.slice(0, 3).join(', ')})`);
  return [...out].sort();
}

function pull() {
  const list = assets();
  if (!list) { console.log(`no release "${TAG}" yet: no pools (the daily job falls back to the stem edit)`); return; }
  fs.mkdirSync(dir, { recursive: true });
  let stamp = {};
  try { stamp = JSON.parse(fs.readFileSync(STAMP, 'utf8')); } catch {}
  const next = {};
  const groups = {};
  for (const a of list) {
    const m = ASSET_RE.exec(a.name);
    if (m && onlyPreset && m[1] !== onlyPreset) continue;
    const g = m ? `pool-${m[1]}-${m[2]}.tar` : a.name;
    (groups[g] = groups[g] || { preset: m && m[1], vibe: m && m[2], items: [] }).items.push(a);
  }
  const tmp = path.join(dir, '.dl');
  for (const [g, { preset, vibe, items }] of Object.entries(groups)) {
    items.sort((x, y) => x.name.localeCompare(y.name));
    const key = items.map(a => `${a.name}:${a.size}:${a.updatedAt}`).join('|');
    next[g] = key;
    const have = preset ? fs.existsSync(path.join(dir, preset, vibe)) : fs.existsSync(path.join(dir, g));
    if (stamp[g] === key && have) continue;
    const mb = items.reduce((s, a) => s + a.size, 0) / 1048576;
    console.log(`pull ${g} (${mb.toFixed(0)} MB, ${items.length} file${items.length > 1 ? 's' : ''})`);
    fs.rmSync(tmp, { recursive: true, force: true });
    fs.mkdirSync(tmp, { recursive: true });
    for (const a of items) gh(['release', 'download', TAG, ...repo, '--pattern', a.name, '--dir', tmp, '--clobber']);
    if (!preset) { fs.copyFileSync(path.join(tmp, g), path.join(dir, g)); continue; }
    const tar = path.join(tmp, g);
    if (!(items.length === 1 && items[0].name === g)) concat(items.map(a => path.join(tmp, a.name)), tar);
    fs.rmSync(path.join(dir, preset, vibe), { recursive: true, force: true });   // the tar replaces the vibe dir
    if (manifestsOnly) {
      const names = execFileSync(TAR, ['-tf', tar], { encoding: 'utf8', maxBuffer: 64 * 1048576 }).split(/\r?\n/)
        .filter(n => /^[^/]+\/[^/]+\/(clips|sounds|vibe)\.json$/.test(n));
      if (names.length) execFileSync(TAR, ['-xf', tar, '-C', dir, ...names]);
    } else execFileSync(TAR, ['-xf', tar, '-C', dir]);
  }
  fs.rmSync(tmp, { recursive: true, force: true });
  if (!manifestsOnly) fs.writeFileSync(STAMP, JSON.stringify({ ...stamp, ...next }));
  console.log(`pools in ${dir}: ${vibes().map(v => `${v.preset}/${v.vibe}`).join(', ') || 'none'}`);
}

/** size + sha256 of every uploaded file against the release (API digest; without one, a download-back) */
function verifyUploaded(files) {
  const api = apiAssets();
  for (const f of files) {
    const name = path.basename(f);
    const a = api.find(x => x.name === name);
    const size = fs.statSync(f).size;
    const sha = sha256File(f);
    if (!a || Number(a.size) !== size) { console.error(`verify: ${name} size ${a && a.size} != ${size}`); return false; }
    if (a.digest) {
      if (String(a.digest).replace(/^sha256:/, '') !== sha) { console.error(`verify: ${name} digest mismatch`); return false; }
    } else {
      const back = path.join(dir, '.verify');
      fs.rmSync(back, { recursive: true, force: true });
      fs.mkdirSync(back, { recursive: true });
      gh(['release', 'download', TAG, ...repo, '--pattern', name, '--dir', back, '--clobber']);
      const ok = sha256File(path.join(back, name)) === sha;
      fs.rmSync(back, { recursive: true, force: true });
      if (!ok) { console.error(`verify: ${name} downloaded copy differs`); return false; }
    }
    console.log(`verified ${name} (${(size / 1048576).toFixed(0)} MB, sha256 ${sha.slice(0, 12)}${a.digest ? ', API digest' : ', download-back'})`);
  }
  return true;
}

function push() {
  const vs = vibes();
  if (!vs.length) throw new Error(`no pools in ${dir}${onlyPreset ? ` for preset ${onlyPreset}` : ''}`);
  try { gh(['release', 'view', TAG, ...repo]); } catch {
    gh(['release', 'create', TAG, ...repo, '--prerelease', '--title', 'Sound / slow-mo vibe pools (media, not in git)',
      '--notes', 'Pool media for the daily rotation (core/edit/refs/pool-format.md). Managed by scripts/edit-pools-store.js. Do not delete.']);
  }
  const keep = new Set(['index.json']);
  const verified = [];
  const tmp = path.join(dir, '.up');
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.mkdirSync(tmp, { recursive: true });
  let total = 0;
  for (const { preset, vibe } of vs) {
    const name = `pool-${preset}-${vibe}.tar`;
    const files = vibeFiles(preset, vibe);
    const listFile = path.join(tmp, `${name}.list`);
    fs.writeFileSync(listFile, files.join('\n') + '\n');
    const tar = path.join(tmp, name);
    execFileSync(TAR, ['-cf', tar, '-C', dir, '-T', listFile]);
    fs.rmSync(listFile);
    const bytes = fs.statSync(tar).size;
    total += bytes;
    const parts = split(tar, PART);
    for (const p of parts) keep.add(path.basename(p));
    console.log(`push ${name}: ${files.length} files, ${(bytes / 1048576).toFixed(0)} MB in ${parts.length} asset(s)`);
    for (const p of parts) upload(p);
    if (pruneLocal) {
      if (verifyUploaded(parts)) verified.push({ preset, vibe, files });
      else console.error(`prune: ${name} NOT verified on the release: local copy kept`);
    }
    for (const p of parts) fs.rmSync(p, { force: true });
  }
  // index.json is the sound / slow-mo vibe index: a ranking-only push never replaces it (2026-10-25)
  if (onlyPreset !== 'ranking' && fs.existsSync(path.join(dir, 'index.json'))) upload(path.join(dir, 'index.json'));
  const pushedVibes = new Set(vs.map(v => `${v.preset}/${v.vibe}`));
  for (const a of assets() || []) {
    if (keep.has(a.name)) continue;
    const m = ASSET_RE.exec(a.name);
    // stale = an old part / single tar of a vibe pushed now, or a vibe of the pushed preset(s) that no longer exists locally
    const stale = m && (pushedVibes.has(`${m[1]}/${m[2]}`) || (!onlyPreset || m[1] === onlyPreset) && !fs.existsSync(path.join(dir, m[1], m[2])));
    if (stale) { console.log(`delete stale ${a.name}`); gh(['release', 'delete-asset', TAG, a.name, ...repo, '--yes']); }
  }
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.rmSync(STAMP, { force: true });   // next pull re-checks sizes / dates
  console.log(`pushed ${(total / 1048576).toFixed(0)} MB for ${vs.length} vibe(s)`);
  if (pruneLocal) prune(verified);
}

function prune(verified) {
  let freed = 0;
  const sizeOf = f => { try { return fs.statSync(f).size; } catch { return 0; } };
  for (const { files } of verified) {
    const media = files.filter(f => !f.endsWith('.json'));
    const sample = new Set(media.filter(f => /\/clips\//.test(f)).slice(0, 3));
    for (const f of media) {
      if (sample.has(f)) continue;
      const p = path.join(dir, f);
      freed += sizeOf(p);
      fs.rmSync(p, { force: true });
    }
  }
  if (verified.length) {
    for (const x of ['_src', '_lib']) {
      const d = path.join(dir, x);
      if (!fs.existsSync(d)) continue;
      let n = 0;
      const walk = q => { for (const f of fs.readdirSync(q)) { const p = path.join(q, f); if (fs.statSync(p).isDirectory()) walk(p); else n += sizeOf(p); } };
      try { walk(d); } catch {}
      fs.rmSync(d, { recursive: true, force: true });
      freed += n;
      console.log(`prune: removed ${x} (${(n / 1048576).toFixed(0)} MB)`);
    }
  }
  console.log(`prune-local: ${verified.length} vibe(s) verified on the release, ${(freed / 1048576).toFixed(0)} MB freed (manifests kept; node scripts/edit-pools-store.js pull restores)`);
}

try {
  if (cmd === 'pull') pull();
  else if (cmd === 'push') push();
  else if (cmd === 'list') for (const a of assets() || []) console.log(a.name, a.size);
  else { console.error('usage: edit-pools-store.js pull|push|list [--dir DIR] [--preset sound|slowmo|ranking] [--manifests-only] [--prune-local]'); process.exit(2); }
} catch (e) {
  console.error(`edit-pools-store ${cmd}: ${(e.stderr || e.message || '').toString().slice(0, 400)}`);
  process.exit(cmd === 'pull' ? 0 : 1);   // a failed pull must never stop the daily short (the rotation falls back to stem)
}
