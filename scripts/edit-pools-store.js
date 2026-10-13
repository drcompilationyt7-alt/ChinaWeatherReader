/**
 * Durable store of the sound / slowmo vibe pools (core/edit/refs/pool-format.md): a GitHub RELEASE, not git (the media
 * is GBs) and not an Actions cache (evictable). One tar per vibe (<preset>/<vibe>/, "sheets" left out) + index.json,
 * each asset well under the 2 GiB per-file limit.
 *
 *   node scripts/edit-pools-store.js pull [--dir DIR] [--manifests-only]   download what changed since the last pull into DIR
 *                                  (--manifests-only: extract only clips/sounds/vibe .json, no media; GNU tar, for CI shards)
 *   node scripts/edit-pools-store.js push [--dir DIR]   upload every vibe dir of DIR, remove assets of vibes that are gone
 *   node scripts/edit-pools-store.js list
 *
 * DIR = $EDIT_POOL_DIR (default output/edit-pools). Release tag = $EDIT_POOLS_TAG (default "edit-pools").
 * Auth: gh with GH_TOKEN (the workflows pass GH_PAT / the job token; nothing is written to disk). Run `push` locally
 * (gh auth login) after a local refresh when YouTube blocks the runner.
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.join(__dirname, '..');
const TAG = process.env.EDIT_POOLS_TAG || 'edit-pools';
const args = process.argv.slice(2);
const cmd = args[0];
const dir = path.resolve(ROOT, args.includes('--dir') ? args[args.indexOf('--dir') + 1] : (process.env.EDIT_POOL_DIR || 'output/edit-pools'));
const repo = process.env.GITHUB_REPOSITORY ? ['--repo', process.env.GITHUB_REPOSITORY] : [];
const env = { ...process.env, GH_TOKEN: process.env.GH_PAT || process.env.GH_TOKEN || process.env.GITHUB_TOKEN || '' };
const gh = (a, o = {}) => execFileSync('gh', a, { env, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'], ...o });
const STAMP = path.join(dir, '.store-stamp.json');
const manifestsOnly = args.includes('--manifests-only');
const assetName = (preset, vibe) => `pool-${preset}-${vibe}.tar`;

function assets() {
  try { return JSON.parse(gh(['release', 'view', TAG, ...repo, '--json', 'assets'])).assets || []; } catch { return null; }
}
function vibes() {
  const out = [];
  for (const preset of ['sound', 'slowmo']) {
    const p = path.join(dir, preset);
    if (!fs.existsSync(p)) continue;
    for (const v of fs.readdirSync(p)) if (fs.statSync(path.join(p, v)).isDirectory()) out.push({ preset, vibe: v });
  }
  return out;
}

function pull() {
  const list = assets();
  if (!list) { console.log(`no release "${TAG}" yet: no pools (the daily job falls back to the stem edit)`); return; }
  fs.mkdirSync(dir, { recursive: true });
  let stamp = {};
  try { stamp = JSON.parse(fs.readFileSync(STAMP, 'utf8')); } catch {}
  const next = {};
  for (const a of list) {
    const key = `${a.size}:${a.updatedAt}`;
    next[a.name] = key;
    if (stamp[a.name] === key && (a.name === 'index.json' ? fs.existsSync(path.join(dir, 'index.json')) : true)) continue;
    console.log(`pull ${a.name} (${(a.size / 1048576).toFixed(0)} MB)`);
    const tmp = path.join(dir, '.dl');
    fs.rmSync(tmp, { recursive: true, force: true });
    fs.mkdirSync(tmp, { recursive: true });
    gh(['release', 'download', TAG, ...repo, '--pattern', a.name, '--dir', tmp, '--clobber']);
    if (a.name.endsWith('.tar')) {
      const m = /^pool-(\w+)-(.+)\.tar$/.exec(a.name);
      if (m) fs.rmSync(path.join(dir, m[1], m[2]), { recursive: true, force: true });   // the tar replaces the vibe dir
      execFileSync('tar', manifestsOnly
        ? ['-xf', path.join(tmp, a.name), '-C', dir, '--wildcards', '*/*/clips.json', '*/*/sounds.json', '*/*/vibe.json']
        : ['-xf', path.join(tmp, a.name), '-C', dir]);
    } else fs.copyFileSync(path.join(tmp, a.name), path.join(dir, a.name));
    fs.rmSync(tmp, { recursive: true, force: true });
  }
  if (!manifestsOnly) fs.writeFileSync(STAMP, JSON.stringify(next));
  console.log(`pools in ${dir}: ${vibes().map(v => `${v.preset}/${v.vibe}`).join(', ') || 'none'}`);
}

function push() {
  const vs = vibes();
  if (!vs.length) throw new Error(`no pools in ${dir}`);
  try { gh(['release', 'view', TAG, ...repo]); } catch {
    gh(['release', 'create', TAG, ...repo, '--prerelease', '--title', 'Sound / slow-mo vibe pools (media, not in git)',
      '--notes', 'Pool media for the daily rotation (core/edit/refs/pool-format.md). Managed by scripts/edit-pools-store.js. Do not delete.']);
  }
  const keep = new Set(['index.json']);
  const tmp = path.join(dir, '.up');
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.mkdirSync(tmp, { recursive: true });
  for (const { preset, vibe } of vs) {
    const name = assetName(preset, vibe);
    keep.add(name);
    const tar = path.join(tmp, name);
    execFileSync('tar', ['-cf', tar, '-C', dir, '--exclude', `${preset}/${vibe}/sheets`, `${preset}/${vibe}`]);
    const mb = fs.statSync(tar).size / 1048576;
    if (mb > 1900) throw new Error(`${name} is ${mb.toFixed(0)} MB (> 2 GiB release asset limit): split the vibe or trim clips`);
    console.log(`push ${name} (${mb.toFixed(0)} MB)`);
    gh(['release', 'upload', TAG, tar, ...repo, '--clobber']);
    fs.rmSync(tar);
  }
  if (fs.existsSync(path.join(dir, 'index.json'))) gh(['release', 'upload', TAG, path.join(dir, 'index.json'), ...repo, '--clobber']);
  for (const a of assets() || []) if (!keep.has(a.name)) { console.log(`delete stale ${a.name}`); gh(['release', 'delete-asset', TAG, a.name, ...repo, '--yes']); }
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.rmSync(STAMP, { force: true });   // next pull re-checks sizes / dates
}

try {
  if (cmd === 'pull') pull();
  else if (cmd === 'push') push();
  else if (cmd === 'list') for (const a of assets() || []) console.log(a.name, a.size);
  else { console.error('usage: edit-pools-store.js pull|push|list [--dir DIR]'); process.exit(2); }
} catch (e) {
  console.error(`edit-pools-store ${cmd}: ${(e.stderr || e.message || '').toString().slice(0, 400)}`);
  process.exit(cmd === 'pull' ? 0 : 1);   // a failed pull must never stop the daily short (the rotation falls back to stem)
}
