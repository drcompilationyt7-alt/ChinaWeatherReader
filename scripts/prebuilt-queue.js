#!/usr/bin/env node
/**
 * Upload today's pre-rendered video from memory/prebuilt-queue.json (one entry per date), scheduled for the channel's
 * daily slot (PUBLISH_HOUR_UTC, default 12:00 UTC). Records it like a normal upload (meme-history.json with postedAt = now, so
 * the daily job skips that day; posted-videos.json; slow-mo music pool + clip ledger). Marks the entry done right after the upload.
 *   --dry-run     print what would happen, change nothing
 *   FAKE_TODAY=YYYY-MM-DD  pretend it is that date (testing)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const { execFileSync } = require('child_process');
const ROOT = path.resolve(__dirname, '..');
const QFILE = path.join(ROOT, 'memory', 'prebuilt-queue.json');
const dry = process.argv.includes('--dry-run');
const today = process.env.FAKE_TODAY || new Date().toISOString().slice(0, 10);
const readJson = (f, d) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return d; } };

(async () => {
  const q = readJson(QFILE, { items: [] });
  const e = (q.items || []).find(x => x.date === today && !x.done);
  if (!e) { console.log(`prebuilt-queue: nothing due on ${today}`); return; }
  const posted = readJson(path.join(ROOT, 'memory', 'posted-videos.json'), { videos: [] });
  const hist = readJson(path.join(ROOT, 'memory', 'meme-history.json'), { uploads: [] });
  if ((posted.videos || []).some(v => v.title === e.title && String(v.postedAt || '').startsWith(today))) {
    console.log('prebuilt-queue: already posted (posted-videos); marking done'); if (!dry) { e.done = true; fs.writeFileSync(QFILE, JSON.stringify(q, null, 1)); } return;
  }
  const hour = String(process.env.PUBLISH_HOUR_UTC || '12').padStart(2, '0');
  const publishAt = `${today}T${hour}:00:00.000Z`;
  console.log(`prebuilt-queue: ${dry ? '[dry run] would upload' : 'uploading'} "${e.title}" (${e.asset}) publishAt ${publishAt}; ${e.clipIds.length} clips; song ${e.song}`);
  if (dry) return;

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'prebuilt-'));
  execFileSync('gh', ['release', 'download', 'edit-pools-staging', '-p', e.asset, '-D', dir, '--clobber'], { stdio: 'inherit' });
  const videoPath = path.join(dir, e.asset);
  if (!fs.existsSync(videoPath)) throw new Error('asset download failed');

  const { YouTubeBridge } = require('../youtube-automation/youtube-bridge');
  const yt = new YouTubeBridge();
  await yt.initialize();
  if (!yt.isAuthenticated()) throw new Error('YouTube not authenticated');
  const up = await yt.uploadVideo({ videoPath, title: e.title, description: e.description, tags: e.tags, categoryId: '1', publishAt });
  if (!up || !up.videoId) throw new Error('upload returned no video id');
  console.log(`UPLOADED videoId=${up.videoId} url=${up.url} publishAt=${publishAt}`);
  // never upload twice: mark done first
  e.done = true; e.videoId = up.videoId; e.url = up.url; e.publishAt = publishAt;
  fs.writeFileSync(QFILE, JSON.stringify(q, null, 1));

  try { await yt.addToPlaylist(up.videoId, 'slow motion edits', ''); } catch (x) { console.log('playlist: ' + x.message); }
  try { await yt.postComment(up.videoId, e.description); } catch (x) { console.log('comment: ' + x.message); }

  const resultJson = path.join(dir, 'result.json');
  fs.writeFileSync(resultJson, JSON.stringify({ ok: true, type: e.type, preset: e.type, vibe: e.vibe, short: e.asset,
    sound_used: e.musicPoolId, clips_used: e.clipIds }));
  const nt = require('../pipeline/newtype-pipeline');
  const result = { title: e.title, resultJson, longPath: null, country: 'Global', category: e.category,
    meme: { song: e.song, theme: 'anime', emoji: '✨', clipIds: e.clipIds, clipChannels: [], contentType: nt.CT[e.type],
      settings: { vibe: e.vibe, duration: e.duration, pool: e.type, prebuilt: true }, why: e.why || 'owner-picked', vibe: e.vibe,
      musicPoolId: e.musicPoolId, songSection: null } };
  nt.recordUpload(result, { ...up, publishAt });
  try {
    const { BoostEngine } = require('../boost/boost-engine');
    new BoostEngine().addPostedVideo(up.url, e.title, 'Global', { videoId: up.videoId, category: e.category, titleSource: e.song });
  } catch (x) { console.log('posted-videos: ' + x.message); }
  console.log('recorded');
})().catch(err => { console.error('prebuilt-queue FAILED: ' + (err.stack || err.message)); process.exit(1); });
