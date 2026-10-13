/**
 * Long-form funnel for the sound / slowmo shorts: every such short also gets a landscape (16:9) long version,
 * rendered in the same run, parked in a GitHub RELEASE ASSET and uploaded as a normal (non-Shorts) video about
 * a week after its short, with links both ways.
 *
 * Storage: release tag LONG_RELEASE_TAG ("longform-queue", a pre-release), one asset per long video. Release
 * assets are not part of the repo (no git bloat), have no expiry (an Actions artifact would die after <= 90 days
 * and is tied to a run), allow 2 GiB per file, and are deleted here once the long is uploaded or expired.
 * Queue state: memory/longform-queue.json (small, committed by the daily job's "Save memory" step).
 *
 * Cadence (env, defaults = the recommendation in core/edit/refs/new-types-progress.md):
 *   LONG_ENABLED=on (default OFF)  LONG_DELAY_DAYS=7        days between the short's publish and its long
 *   LONG_MIN_GAP_DAYS=3      at least this many days between two longs (about 2-3 a week)
 *   LONG_MAX_PER_WEEK=3      hard cap per rolling 7 days
 *   LONG_PUBLISH_HOUR_UTC=16 the long goes out at this hour (the short is at 12:00, so they never collide)
 *   LONG_QUEUE_MAX=6         no new long is rendered while this many wait (over-supply is dropped, not stacked)
 *   LONG_MAX_WAIT_DAYS=21    a long that could not get a slot within this many days after its due date expires
 *
 *   enqueue(result, upload)            after a sound / slowmo short went up (result.longPath set)
 *   uploadDue({ youtubeBridge })       once a day: uploads at most ONE long whose slot is within 24 h
 */
const path = require('path');
const fs = require('fs');
const os = require('os');
const { execFile } = require('child_process');
const { Logger } = require('../core/logger');
const { optimizeMetadata, recordMetadataChoice, buildDescription } = require('../core/metadata');

const logger = new Logger('LongForm');
const ROOT = path.join(__dirname, '..');
const MEM = () => path.resolve(ROOT, process.env.MEMORY_DIR || 'memory');
const QUEUE_FILE = () => path.join(MEM(), 'longform-queue.json');
const DAY = 86400000;

const cfg = () => ({
  enabled: process.env.LONG_ENABLED === 'on',   // OFF by default (owner 2026-10-12: only stem edits post for now)
  delayDays: Number(process.env.LONG_DELAY_DAYS || 7),
  minGapDays: Number(process.env.LONG_MIN_GAP_DAYS || 3),
  maxPerWeek: Number(process.env.LONG_MAX_PER_WEEK || 3),
  hour: Number(process.env.LONG_PUBLISH_HOUR_UTC || 16),
  queueMax: Number(process.env.LONG_QUEUE_MAX || 6),
  maxWaitDays: Number(process.env.LONG_MAX_WAIT_DAYS || 21),
  tag: process.env.LONG_RELEASE_TAG || 'longform-queue',
});

function loadQueue() {
  try { return JSON.parse(fs.readFileSync(QUEUE_FILE(), 'utf8')); } catch { return { items: [] }; }
}
function saveQueue(q) {
  fs.mkdirSync(MEM(), { recursive: true });
  fs.writeFileSync(QUEUE_FILE(), JSON.stringify(q, null, 1));
}

// ─── storage (GitHub release assets; LONG_STORE=local:<dir> for tests / dry runs) ──────────────
function sh(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    execFile(cmd, args, { env: { ...process.env, GH_TOKEN: process.env.GH_PAT || process.env.GITHUB_TOKEN || process.env.GH_TOKEN || '' },
      maxBuffer: 16 * 1024 * 1024, timeout: 30 * 60000, ...opts }, (err, out, errOut) => {
      if (err) return reject(new Error(`${cmd} ${args.slice(0, 3).join(' ')}: ${(errOut || err.message || '').toString().slice(0, 300)}`));
      resolve(String(out));
    });
  });
}
function store() {
  const local = /^local:(.+)$/.exec(process.env.LONG_STORE || '');
  if (local) {
    const dir = path.resolve(local[1]);
    return {
      name: `local:${dir}`,
      put: async (file, name) => { fs.mkdirSync(dir, { recursive: true }); fs.copyFileSync(file, path.join(dir, name)); },
      get: async (name, dest) => { fs.copyFileSync(path.join(dir, name), dest); },
      del: async name => { try { fs.unlinkSync(path.join(dir, name)); } catch {} },
    };
  }
  const tag = cfg().tag;
  const repo = process.env.GITHUB_REPOSITORY ? ['--repo', process.env.GITHUB_REPOSITORY] : [];
  return {
    name: `release:${tag}`,
    put: async (file, name) => {
      try { await sh('gh', ['release', 'view', tag, ...repo]); } catch {
        await sh('gh', ['release', 'create', tag, ...repo, '--prerelease', '--title', 'Long-form queue (temporary renders)',
          '--notes', 'Rendered long versions waiting for their upload date; assets are deleted after upload. Do not delete.']);
      }
      const tmp = path.join(path.dirname(file), name);   // gh names the asset after the file
      if (tmp !== file) fs.copyFileSync(file, tmp);
      try { await sh('gh', ['release', 'upload', tag, tmp, ...repo, '--clobber']); } finally { if (tmp !== file) fs.rmSync(tmp, { force: true }); }
    },
    get: async (name, dest) => {
      const dir = path.dirname(dest);
      await sh('gh', ['release', 'download', tag, ...repo, '--pattern', name, '--dir', dir, '--clobber']);
      if (path.join(dir, name) !== dest) fs.renameSync(path.join(dir, name), dest);
    },
    del: async name => { try { await sh('gh', ['release', 'delete-asset', tag, name, ...repo, '--yes']); } catch (e) { logger.warn(e.message); } },
  };
}

// ─── pure scheduling logic (tested by scripts/longform-selftest.js) ──────────────────────────────
function atHour(date, hour) {
  const d = new Date(date);
  d.setUTCHours(hour, 0, 0, 0);
  return d;
}
/** the planned slot of a long: the day of its short's publish + delay, at the long hour */
function dueAt(shortPublishAt, c = cfg()) {
  return atHour(new Date(new Date(shortPublishAt).getTime() + c.delayDays * DAY), c.hour);
}
/**
 * First legal slot for a long planned at `planned`, given the slots of the longs already out / scheduled:
 * >= minGapDays after the previous one and < maxPerWeek inside any rolling 7 days.
 */
function planSlot(planned, taken, c = cfg()) {
  const slots = taken.map(t => new Date(t).getTime()).filter(Number.isFinite).sort((a, b) => a - b);
  let cand = new Date(planned).getTime();
  for (let guard = 0; guard < 120; guard++) {
    const gapOk = slots.every(s => Math.abs(cand - s) >= c.minGapDays * DAY - 60000);
    const weekN = slots.filter(s => s > cand - 7 * DAY && s <= cand).length;
    const weekAfter = slots.filter(s => s > cand && s < cand + 7 * DAY).length;
    if (gapOk && weekN < c.maxPerWeek && weekN + weekAfter < c.maxPerWeek + 1) return new Date(cand);
    cand += DAY;
  }
  return new Date(cand);
}
const taken = q => q.items.filter(i => i.status === 'uploaded' || i.status === 'scheduled').map(i => i.slotAt || i.dueAt);

/** whether the daily run should spend time rendering a long for the short it just made */
function shouldRender(q = loadQueue(), c = cfg()) {
  return c.enabled && q.items.filter(i => i.status === 'queued').length < c.queueMax;
}

/**
 * The long to upload now (at most one): among the queued items whose slot is within `horizonH` hours (the slot is
 * the later of its due date and the cadence rules), the one whose short did best (perf(item) -> number, optional),
 * else the oldest. Items past LONG_MAX_WAIT_DAYS are returned in `expired`.
 */
function pickDue(q, now = new Date(), { horizonH = 24, perf = null, c = cfg() } = {}) {
  const t = taken(q);
  const expired = [], ready = [];
  for (const it of q.items.filter(i => i.status === 'queued').sort((a, b) => new Date(a.dueAt) - new Date(b.dueAt))) {
    if (now - new Date(it.dueAt) > c.maxWaitDays * DAY) { expired.push(it); continue; }
    const slot = planSlot(it.dueAt, t, c);
    if (slot.getTime() - now.getTime() <= horizonH * 3600000) ready.push({ it, slot });
  }
  if (!ready.length) return { item: null, slot: null, expired };
  ready.sort((a, b) => (perf ? (perf(b.it) || 0) - (perf(a.it) || 0) : 0) || new Date(a.it.dueAt) - new Date(b.it.dueAt));
  return { item: ready[0].it, slot: ready[0].slot, expired };
}

// ─── queueing a long ─────────────────────────────────────────────────────────────────────────────
async function enqueue(result, upload) {
  const c = cfg();
  if (!c.enabled || !result.longPath || !fs.existsSync(result.longPath)) return null;
  const m = result.meme;
  const type = m.contentType.replace('-edit', '');
  const shortUrl = upload.url || (upload.videoId ? `https://www.youtube.com/watch?v=${upload.videoId}` : '');
  const shortPublishAt = upload.publishAt || new Date().toISOString();
  // metadata of the long: its own pool (sound-long / slowmo-long), the link to the short in the description
  const meta = await optimizeMetadata({ channel: 'meme', contentType: `${type}-long`, facts: { ...(result.facts || {}), shortUrl } });
  const name = `${type}-${(upload.videoId || Date.now())}.mp4`;
  const st = store();
  await st.put(result.longPath, name);
  const item = {
    id: name.replace(/\.mp4$/, ''), type, status: 'queued', createdAt: new Date().toISOString(), shortVideoId: upload.videoId || null, shortUrl,
    shortTitle: result.title, shortPublishAt, dueAt: dueAt(shortPublishAt, c).toISOString(), asset: { store: st.name, name },
    title: meta.title,
    description: buildDescription(meta, `Short version: ${shortUrl}`, 'More edits every day on this channel.'),
    tags: meta.tags, categoryId: result.categoryId, playlistTitle: 'full edits', metaChoice: meta.choice || null,
  };
  const q = loadQueue();
  q.items.push(item);
  saveQueue(q);
  logger.success(`Long queued: ${name} due ${item.dueAt} (${st.name})`);
  return item;
}

// ─── the daily upload ───────────────────────────────────────────────────────────────────────────
async function appendLinkToShort(youtube, item, longUrl, publishAt) {
  if (!youtube || !item.shortVideoId) return false;
  const r = await youtube.videos.list({ part: ['snippet'], id: [item.shortVideoId] });
  const v = r.data.items && r.data.items[0];
  if (!v) return false;
  const sn = v.snippet;
  if ((sn.description || '').includes(longUrl)) return true;
  const when = publishAt ? ` (${new Date(publishAt).toISOString().slice(0, 10)})` : '';
  sn.description = `${sn.description || ''}\n\nFull-length version${when}: ${longUrl}`.trim();
  await youtube.videos.update({ part: ['snippet'], requestBody: { id: item.shortVideoId, snippet: { title: sn.title, description: sn.description,
    categoryId: sn.categoryId, tags: sn.tags, defaultLanguage: sn.defaultLanguage } } });
  return true;
}

async function uploadDue({ youtubeBridge, now = new Date(), perf = null, dryRun = process.env.DRY_RUN === 'true' } = {}) {
  const c = cfg();
  const q = loadQueue();
  const st = store();
  const { item, slot, expired } = pickDue(q, now, { perf, c });
  for (const e of expired) {
    e.status = 'expired';
    if (!dryRun) await st.del(e.asset.name);
    logger.warn(`Long expired (no slot in ${c.maxWaitDays} days): ${e.id}`);
  }
  if (!item) { if (expired.length && !dryRun) saveQueue(q); logger.info('Long-form: nothing due'); return null; }
  logger.info(`Long-form due: ${item.id}, slot ${slot.toISOString()}`);
  if (dryRun) { logger.info('[dry run] would upload the long and link both videos'); return { item, slot, dryRun: true }; }
  if (!youtubeBridge || !youtubeBridge.isAuthenticated()) { logger.warn('YouTube not authenticated; long stays queued'); return null; }
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'long-'));
  const file = path.join(dir, item.asset.name);
  try {
    await st.get(item.asset.name, file);
    const up = await youtubeBridge.uploadVideo({ videoPath: file, title: item.title, description: item.description, tags: item.tags,
      categoryId: item.categoryId, publishAt: slot.toISOString() });
    item.status = up.publishAt ? 'scheduled' : 'uploaded';
    item.slotAt = slot.toISOString();
    item.longVideoId = up.videoId;
    item.longUrl = up.url;
    item.uploadedAt = new Date().toISOString();
    saveQueue(q);   // saved first: the upload exists even if a later step fails
    try { if (item.playlistTitle) await youtubeBridge.addToPlaylist(up.videoId, item.playlistTitle, 'Full-length versions of the daily edits.'); } catch (e) { logger.warn(`playlist: ${e.message}`); }
    try { await appendLinkToShort(youtubeBridge.youtube, item, up.url, up.publishAt); } catch (e) { logger.warn(`link on the short failed: ${(e.message || '').slice(0, 120)}`); }
    if (item.metaChoice) { try { recordMetadataChoice(item.metaChoice, { videoId: up.videoId }); } catch {} }
    await st.del(item.asset.name);
    logger.success(`Long ${up.publishAt ? 'scheduled for ' + up.publishAt : 'uploaded'}: ${up.url}`);
    return { item, slot, up };
  } catch (e) {
    item.tries = (item.tries || 0) + 1;
    item.lastError = (e.message || '').slice(0, 200);
    if (item.tries >= 4) item.status = 'failed';
    saveQueue(q);
    logger.warn(`Long upload failed (try ${item.tries}): ${item.lastError}`);
    return null;
  } finally { try { fs.rmSync(dir, { recursive: true, force: true }); } catch {} }
}

/** Entries that are fully done for more than 60 days are dropped from the file to keep it small. */
function prune(q = loadQueue(), now = new Date()) {
  q.items = q.items.filter(i => i.status === 'queued' || now - new Date(i.uploadedAt || i.createdAt) < 60 * DAY);
  return q;
}

module.exports = { enqueue, uploadDue, shouldRender, pickDue, planSlot, dueAt, loadQueue, saveQueue, prune, cfg, store, QUEUE_FILE };
