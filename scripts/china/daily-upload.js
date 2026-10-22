#!/usr/bin/env node
/**
 * Mainland Unpacked: the daily uploader (run by .github/workflows/china-channel.yml).
 *
 * The week's videos are rendered locally, reviewed, then pushed as a DRAFT GitHub release
 * "china-week-YYYY-MM-DD" (scripts/china/push-week.js). The release holds the videos and a
 * manifest.json, which gives each item a date. Drafts are only visible to accounts with push
 * access, so nothing leaks from the public repo before it airs.
 *
 * On each run this:
 *   - uploads what is due today (the long-form on its day, plus that day's Short), scheduled
 *     with publishAt for the item's publish time;
 *   - catches up overdue items (published right away), at most CHINA_MAX_PER_RUN per run
 *     (the API quota is shared with the other two channels);
 *   - records each upload in memory/china-channel/upload-history.json (the workflow commits it);
 *   - deletes a release once every item in it is uploaded.
 *
 * Guards:
 *   - Refuses to upload if the token's channel isn't CHINA_CHANNEL_ID (when that is set).
 *   - Skips an item whose title is already among the channel's recent uploads, so a lost
 *     response can't double-post.
 */
const fs = require('fs');
const path = require('path');
const { pipeline } = require('stream/promises');
const { Readable } = require('stream');
const { google } = require('googleapis');
const { stripLinks } = require('../../core/link-guard');  // no external links (policy warning 2026-10-22)

const REPO = process.env.GITHUB_REPOSITORY;
const GH_TOKEN = process.env.GH_TOKEN;
const MEMORY_DIR = process.env.MEMORY_DIR || 'memory/china-channel';
const HISTORY = path.join(MEMORY_DIR, 'upload-history.json');
const WORK = process.env.CHINA_WORK_DIR || path.join(process.env.RUNNER_TEMP || '/tmp', 'china-upload');
const MAX_PER_RUN = Number(process.env.CHINA_MAX_PER_RUN || 2);
const DRY = process.env.DRY_RUN === 'true';
const TAG_PREFIX = 'china-week-';
const DEFAULT_TIME = { short: '14:00', long: '15:00' };     // UTC: 10-11 AM US East, evening in Europe
const DEFAULT_CATEGORY = { short: '28', long: '24' };       // Science & Technology / Entertainment

const log = (...a) => console.log(...a);
const summary = [];

async function gh(pathname, opts = {}) {
  const res = await fetch(`https://api.github.com${pathname}`, {
    ...opts,
    headers: {
      Authorization: `Bearer ${GH_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) throw new Error(`GitHub ${opts.method || 'GET'} ${pathname}: ${res.status} ${await res.text()}`);
  return res;
}

// Asset downloads redirect to a signed storage URL; follow it by hand, without our token.
async function downloadAsset(asset, dest) {
  const first = await fetch(`https://api.github.com/repos/${REPO}/releases/assets/${asset.id}`, {
    headers: { Authorization: `Bearer ${GH_TOKEN}`, Accept: 'application/octet-stream' },
    redirect: 'manual',
  });
  const res = first.status >= 300 && first.status < 400 ? await fetch(first.headers.get('location')) : first;
  if (!res.ok) throw new Error(`download ${asset.name}: ${res.status}`);
  await pipeline(Readable.fromWeb(res.body), fs.createWriteStream(dest));
  return dest;
}

function loadHistory() {
  try { return JSON.parse(fs.readFileSync(HISTORY, 'utf8')); } catch { return { done: {} }; }
}
function saveHistory(h) {
  fs.mkdirSync(MEMORY_DIR, { recursive: true });
  fs.writeFileSync(HISTORY, JSON.stringify(h, null, 2) + '\n');
}

async function main() {
  if (!REPO || !GH_TOKEN) throw new Error('GITHUB_REPOSITORY and GH_TOKEN are required');
  fs.mkdirSync(WORK, { recursive: true });
  const history = loadHistory();
  history.done = history.done || {};
  const now = new Date();
  const today = now.toISOString().slice(0, 10);

  // 1. collect due items from every pending week release
  const releases = (await (await gh(`/repos/${REPO}/releases?per_page=100`)).json())
    .filter(r => String(r.tag_name || r.name || '').startsWith(TAG_PREFIX));
  const due = [];
  for (const rel of releases) {
    const mAsset = rel.assets.find(a => a.name === 'manifest.json');
    if (!mAsset) { log(`! ${rel.tag_name}: no manifest.json, skipped`); continue; }
    const manifest = JSON.parse(fs.readFileSync(await downloadAsset(mAsset, path.join(WORK, `${rel.id}-manifest.json`)), 'utf8'));
    rel.manifest = manifest;
    for (const item of manifest.items) {
      if (!history.done[item.id] && item.date <= today) due.push({ rel, item });
    }
  }
  // today's items first (long-form before the Short), then the oldest overdue
  due.sort((a, b) =>
    (b.item.date === today) - (a.item.date === today) ||
    a.item.date.localeCompare(b.item.date) ||
    (a.item.type === 'long' ? -1 : 1));
  log(`${releases.length} pending week release(s), ${due.length} item(s) due`);
  if (!due.length) { summary.push('Nothing due today.'); return finish(releases, history); }

  // 2. YouTube client + channel guard + recent titles (dedupe), 2 quota units
  const auth = new google.auth.OAuth2(process.env.YOUTUBE_CLIENT_ID, process.env.YOUTUBE_CLIENT_SECRET);
  auth.setCredentials({ refresh_token: process.env.YOUTUBE_REFRESH_TOKEN });
  const yt = google.youtube({ version: 'v3', auth });
  const ch = (await yt.channels.list({ part: ['snippet', 'contentDetails'], mine: true })).data.items?.[0];
  if (!ch) throw new Error('token has no channel');
  const want = process.env.CHINA_CHANNEL_ID;
  if (want && ch.id !== want) throw new Error(`token belongs to "${ch.snippet.title}" (${ch.id}), not CHINA_CHANNEL_ID ${want}; refusing to upload`);
  if (!want) log(`! CHINA_CHANNEL_ID is not set; uploading to "${ch.snippet.title}" (${ch.id})`);
  const uploads = ch.contentDetails.relatedPlaylists.uploads;
  const recent = (await yt.playlistItems.list({ part: ['snippet'], playlistId: uploads, maxResults: 50 }).catch(() => ({ data: {} }))).data.items || [];
  const recentByTitle = new Map(recent.map(p => [p.snippet.title, p.snippet.resourceId.videoId]));

  // 3. upload
  for (const { rel, item } of due.slice(0, MAX_PER_RUN)) {
    const when = new Date(`${item.date}T${item.publish_utc || DEFAULT_TIME[item.type] || '14:00'}:00Z`);
    const scheduled = when.getTime() > now.getTime() + 20 * 60 * 1000;
    const label = `${item.id} (${item.type}, ${item.date})`;

    if (recentByTitle.has(item.title)) {
      const videoId = recentByTitle.get(item.title);
      log(`= ${label}: already on the channel as ${videoId}; marking done`);
      history.done[item.id] = { videoId, title: item.title, date: item.date, release: rel.tag_name, note: 'found existing', at: now.toISOString() };
      saveHistory(history);
      continue;
    }
    if (DRY) { summary.push(`DRY RUN: would upload ${label}${scheduled ? `, publishing ${when.toISOString()}` : ', public now'}`); continue; }

    const asset = rel.assets.find(a => a.name === item.file);
    if (!asset) { log(`! ${label}: asset ${item.file} missing from ${rel.tag_name}`); summary.push(`❌ ${label}: file missing`); continue; }
    log(`↓ ${item.file} (${(asset.size / 1e6).toFixed(0)} MB)`);
    const video = await downloadAsset(asset, path.join(WORK, item.file));

    const body = {
      snippet: {
        title: item.title,
        description: stripLinks(item.description || ''),
        tags: item.tags || [],
        categoryId: item.categoryId || DEFAULT_CATEGORY[item.type] || '22',
        defaultLanguage: 'en',
        defaultAudioLanguage: 'en',
      },
      status: { privacyStatus: scheduled ? 'private' : 'public', selfDeclaredMadeForKids: false },
    };
    if (scheduled) body.status.publishAt = when.toISOString();
    if (item.containsSyntheticMedia != null) body.status.containsSyntheticMedia = !!item.containsSyntheticMedia;

    log(`↑ ${label}: "${item.title}"`);
    const res = await yt.videos.insert({
      part: ['snippet', 'status'],
      requestBody: body,
      media: { mimeType: 'video/mp4', body: fs.createReadStream(video) },
    });
    const videoId = res.data.id;
    history.done[item.id] = {
      videoId, title: item.title, type: item.type, date: item.date, release: rel.tag_name,
      publishAt: scheduled ? when.toISOString() : null, at: new Date().toISOString(),
    };
    saveHistory(history);   // saved straight away: a later failure must not cause a re-upload
    summary.push(`✅ ${label}: https://youtu.be/${videoId}${scheduled ? ` (goes public ${when.toISOString()})` : ' (public now)'}`);

    for (const [kind, file] of [['thumbnail', item.thumbnail], ['captions', item.captions]]) {
      const a = file && rel.assets.find(x => x.name === file);
      if (!a) continue;
      try {
        const p = await downloadAsset(a, path.join(WORK, file));
        if (kind === 'thumbnail') await yt.thumbnails.set({ videoId, media: { body: fs.createReadStream(p) } });
        else await yt.captions.insert({
          part: ['snippet'],
          requestBody: { snippet: { videoId, language: 'en', name: 'English', isDraft: false } },
          media: { body: fs.createReadStream(p) },
        });
        log(`  + ${kind}`);
      } catch (e) {
        log(`  ! ${kind} failed: ${e.message}`);   // e.g. custom thumbnails need a verified channel
      }
    }
    fs.rmSync(video, { force: true });
  }
  if (due.length > MAX_PER_RUN) summary.push(`${due.length - MAX_PER_RUN} more item(s) overdue; they go in the next runs.`);
  return finish(releases, history);
}

// a release whose items are all uploaded is deleted (keeps the video files from piling up)
async function finish(releases, history) {
  for (const rel of releases) {
    if (!rel.manifest || DRY) continue;
    if (rel.manifest.items.every(i => history.done[i.id])) {
      await gh(`/repos/${REPO}/releases/${rel.id}`, { method: 'DELETE' });
      if (!rel.draft) await gh(`/repos/${REPO}/git/refs/tags/${rel.tag_name}`, { method: 'DELETE' }).catch(() => {});
      summary.push(`🧹 ${rel.tag_name}: every item uploaded, release deleted`);
    }
  }
  const text = summary.join('\n');
  log(text);
  if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, `### Mainland Unpacked\n${text}\n`);
}

main().catch(e => {
  console.error(e.response?.data ? JSON.stringify(e.response.data) : e.stack || e.message);
  process.exit(1);
});
