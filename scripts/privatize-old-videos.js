#!/usr/bin/env node
/**
 * Make the old Mr. WorldWideWebster re-uploads private on Zero Yen Otaku (channel
 * UC2_aNJgemODOCsOLsYwaelg): every public / unlisted upload published before the
 * cut-off (the first Zero Yen Otaku edit went out 2026-09-19). Private, not deleted:
 * it is reversible, and nothing is lost.
 *
 *   node scripts/privatize-old-videos.js              # lists what would change
 *   node scripts/privatize-old-videos.js --apply      # makes them private
 *   node scripts/privatize-old-videos.js --apply --before 2026-09-19T00:00:00Z
 *
 * Each videos.update costs 50 quota units (10,000 a day for the whole project), so a
 * run stops cleanly on a quota error; run it again the next day to finish. Progress is
 * kept in branding/meme-channel/privatized.json (gitignored).
 */
const fs = require('fs');
const path = require('path');
const { google } = require('googleapis');

const ROOT = path.join(__dirname, '..');
const CHANNEL_ID = 'UC2_aNJgemODOCsOLsYwaelg';
const LOG = path.join(ROOT, 'branding', 'meme-channel', 'privatized.json');

function credentials() {
  const file = path.join(ROOT, 'youtube-credentials.json');
  if (fs.existsSync(file)) return JSON.parse(fs.readFileSync(file, 'utf8'));
  const { YOUTUBE_CLIENT_ID: client_id, YOUTUBE_CLIENT_SECRET: client_secret, YOUTUBE_REFRESH_TOKEN: refresh_token } = process.env;
  if (client_id && client_secret && refresh_token) return { client_id, client_secret, refresh_token };
  throw new Error('No credentials for Zero Yen Otaku');
}

(async () => {
  const apply = process.argv.includes('--apply');
  const bi = process.argv.indexOf('--before');
  const cutoff = new Date(bi > 0 ? process.argv[bi + 1] : '2026-09-19T00:00:00Z');
  const c = credentials();
  const auth = new google.auth.OAuth2(c.client_id, c.client_secret);
  auth.setCredentials({ refresh_token: c.refresh_token });
  const yt = google.youtube({ version: 'v3', auth });

  const ch = await yt.channels.list({ part: ['contentDetails'], mine: true });
  if (!ch.data.items || ch.data.items[0].id !== CHANNEL_ID) throw new Error('That token is not the Zero Yen Otaku channel; stopping.');
  const uploads = ch.data.items[0].contentDetails.relatedPlaylists.uploads;
  const ids = [];
  let pageToken;
  do {
    const r = await yt.playlistItems.list({ part: ['contentDetails'], playlistId: uploads, maxResults: 50, pageToken });
    ids.push(...r.data.items.map(i => i.contentDetails.videoId));
    pageToken = r.data.nextPageToken;
  } while (pageToken);

  const videos = [];
  for (let i = 0; i < ids.length; i += 50) {
    const r = await yt.videos.list({ part: ['snippet', 'status'], id: ids.slice(i, i + 50).join(','), maxResults: 50 });
    videos.push(...r.data.items);
  }
  const old = videos.filter(v => new Date(v.snippet.publishedAt) < cutoff && ['public', 'unlisted'].includes(v.status.privacyStatus));
  const keep = videos.filter(v => new Date(v.snippet.publishedAt) >= cutoff);
  console.log(`${videos.length} uploads; ${old.length} old public/unlisted to make private; keeping ${keep.length} new: `
    + keep.map(v => `"${v.snippet.title}"`).join(', '));
  if (!apply) {
    old.slice(0, 5).forEach(v => console.log(`  would hide ${v.id} ${v.snippet.publishedAt.slice(0, 10)} ${v.snippet.title.slice(0, 60)}`));
    console.log('Dry run. Re-run with --apply.');
    return;
  }

  const done = (() => { try { return JSON.parse(fs.readFileSync(LOG, 'utf8')); } catch { return {}; } })();
  let n = 0;
  for (const v of old) {
    // send the whole status back so only the privacy changes (videos.update replaces the part)
    const st = v.status;
    const status = { privacyStatus: 'private', embeddable: st.embeddable, license: st.license,
      publicStatsViewable: st.publicStatsViewable, selfDeclaredMadeForKids: st.madeForKids || false };
    try {
      await yt.videos.update({ part: ['status'], requestBody: { id: v.id, status } });
      done[v.id] = { title: v.snippet.title, published: v.snippet.publishedAt, hiddenAt: new Date().toISOString(), was: st.privacyStatus };
      n++;
      if (n % 20 === 0) console.log(`  ${n}/${old.length} private`);
    } catch (e) {
      const msg = (e.errors && e.errors[0] && e.errors[0].reason) || e.message;
      if (/quota/i.test(msg)) { console.log(`Quota used up after ${n}; run again tomorrow to finish (${old.length - n} left).`); break; }
      console.log(`  ${v.id} failed: ${String(msg).slice(0, 120)}`);
    }
  }
  fs.mkdirSync(path.dirname(LOG), { recursive: true });
  fs.writeFileSync(LOG, JSON.stringify(done, null, 1));
  console.log(`Done: ${n} made private this run (${Object.keys(done).length} total). Log: ${path.relative(ROOT, LOG)}`);
})().catch(e => { console.error('❌', e.message); process.exit(1); });
