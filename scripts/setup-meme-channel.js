#!/usr/bin/env node
/**
 * Rebrand Mr. WorldWideWebster as the broke NEET who makes fan edits for his
 * hobby and his oshi: description, keywords, language and banner, applied
 * through the YouTube Data API with the channel's own token
 * (youtube-credentials.json, or YOUTUBE_CLIENT_ID / _SECRET / _REFRESH_TOKEN).
 *
 *   node scripts/setup-meme-channel.js           # shows what would change
 *   node scripts/setup-meme-channel.js --apply   # applies it (old branding saved first)
 *
 * The API cannot set the profile picture; the script prints it as the one
 * manual step (branding/meme-channel/avatar.png).
 */
const fs = require('fs');
const path = require('path');
const { google } = require('googleapis');

const ROOT = path.join(__dirname, '..');
const BANNER = path.join(ROOT, 'branding', 'meme-channel', 'banner.png');
const AVATAR = path.join(ROOT, 'branding', 'meme-channel', 'avatar.png');
const BACKUP = path.join(ROOT, 'branding', 'meme-channel', 'previous-branding.json');

const DESCRIPTION = `just an average NEET who spent every last yen on his hobby and his oshi 🥲

figures, concert tickets, gacha, the limited album (all 4 versions)... and now the wallet is empty 💸
so i started making fan edits to idolize my hobby and my oshi, and maybe earn enough for the next comeback.

every day:
🎤 an anime / k-pop song as an acapella, built layer by layer: vocals, bass, beatbox, harmony
🔥 then the real song drops, with an edit of the anime or the idols it belongs to

anime · k-pop · j-pop · c-pop
subscribe = one more cup of instant noodles for me 🍜

all clips belong to their original creators and studios. this is a fan channel, made with love (and no money).`;

const KEYWORDS = ['acapella', 'anime edit', 'amv', 'kpop edit', 'anime acapella', 'kpop acapella', 'anime opening',
  'oshi', 'otaku', 'fan edit', 'jpop', 'cpop', 'anime memes', 'kpop memes']
  .map(k => (k.includes(' ') ? `"${k}"` : k)).join(' ');

function credentials() {
  const file = path.join(ROOT, 'youtube-credentials.json');
  if (fs.existsSync(file)) return JSON.parse(fs.readFileSync(file, 'utf8'));
  const { YOUTUBE_CLIENT_ID: client_id, YOUTUBE_CLIENT_SECRET: client_secret, YOUTUBE_REFRESH_TOKEN: refresh_token } = process.env;
  if (client_id && client_secret && refresh_token) return { client_id, client_secret, refresh_token };
  throw new Error('No credentials for Mr. WorldWideWebster: youtube-credentials.json or YOUTUBE_* env vars');
}

(async () => {
  const apply = process.argv.includes('--apply');
  const c = credentials();
  const auth = new google.auth.OAuth2(c.client_id, c.client_secret);
  auth.setCredentials({ refresh_token: c.refresh_token });
  const yt = google.youtube({ version: 'v3', auth });

  const me = await yt.channels.list({ part: ['snippet', 'brandingSettings', 'statistics'], mine: true });
  const ch = me.data.items && me.data.items[0];
  if (!ch) throw new Error('This token has no YouTube channel');
  console.log(`Token belongs to: "${ch.snippet.title}" (${ch.snippet.customUrl || 'no handle'}, ${ch.statistics.videoCount} videos)`);
  if (!/worldwidewebster/i.test(`${ch.snippet.title} ${ch.snippet.customUrl}`)) {
    throw new Error('That is not the Mr. WorldWideWebster channel; stopping.');
  }

  const branding = ch.brandingSettings || {};
  const next = {
    ...branding,
    channel: { ...(branding.channel || {}), description: DESCRIPTION, keywords: KEYWORDS, defaultLanguage: 'en' },
  };
  console.log('\nOld description:\n' + ((branding.channel || {}).description || '(empty)'));
  console.log('\nNew description:\n' + DESCRIPTION + '\n\nKeywords: ' + KEYWORDS);
  if (!apply) {
    console.log('\nDry run. Re-run with --apply to update the channel.');
    return;
  }

  fs.writeFileSync(BACKUP, JSON.stringify({ savedAt: new Date().toISOString(), brandingSettings: branding }, null, 1));
  console.log(`\nOld branding saved to ${BACKUP}`);
  const banner = await yt.channelBanners.insert({ media: { mimeType: 'image/png', body: fs.createReadStream(BANNER) } });
  next.image = { ...(branding.image || {}), bannerExternalUrl: banner.data.url };
  await yt.channels.update({ part: ['brandingSettings'], requestBody: { id: ch.id, brandingSettings: next } });
  console.log('\n✅ Description, keywords, language and banner updated.');
  console.log(`\nStill to do by hand in YouTube Studio > Customization > Branding (the API cannot do it):`);
  console.log(`  Profile picture: ${AVATAR}`);
})().catch(e => { console.error('❌', e.message); process.exit(1); });
