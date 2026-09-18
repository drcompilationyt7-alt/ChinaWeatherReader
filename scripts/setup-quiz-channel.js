#!/usr/bin/env node
/**
 * One-time branding for the Asian Pop Quiz channel: description, keywords,
 * language and banner, applied through the YouTube Data API.
 *
 * Needs the quiz channel's token (from
 *   node youtube-automation/setup-youtube.js --out youtube-credentials-quiz.json)
 * or the QUIZ_YOUTUBE_CLIENT_ID / _SECRET / _REFRESH_TOKEN env vars.
 *
 *   node scripts/setup-quiz-channel.js           # shows what would change
 *   node scripts/setup-quiz-channel.js --apply   # applies it
 *
 * The API cannot set the channel name, handle or profile picture; the script
 * prints those as the remaining manual steps.
 */
const fs = require('fs');
const path = require('path');
const { google } = require('googleapis');

const ROOT = path.join(__dirname, '..');
const BANNER = path.join(ROOT, 'branding', 'quiz-channel', 'banner.png');
const AVATAR = path.join(ROOT, 'branding', 'quiz-channel', 'avatar.png');
const NAME = 'Asian Pop Quiz';
const HANDLE = '@asianpopquiz';

const DESCRIPTION = `🍡 Asian Pop Quiz: how well do you really know anime, K-pop and Asia?

A new quiz Short every day:
• Guess the anime or K-pop song from 4 emojis
• K-pop, anime and VTuber trivia, easy to impossible
• Would You Rather: anime, K-pop and Asian food editions
• Find the city on the map of China, Japan and Korea

Comment your score on every quiz 👇 and subscribe so you never miss a round.`;

const KEYWORDS = ['anime quiz', 'kpop quiz', 'guess the anime', 'guess the kpop song', 'would you rather',
  'vtuber', 'hololive', 'asian pop culture', 'trivia', 'quiz', 'emoji quiz', 'china', 'japan', 'korea']
  .map(k => (k.includes(' ') ? `"${k}"` : k)).join(' ');

function credentials() {
  const file = path.join(ROOT, 'youtube-credentials-quiz.json');
  if (fs.existsSync(file)) return JSON.parse(fs.readFileSync(file, 'utf8'));
  const { QUIZ_YOUTUBE_CLIENT_ID: client_id, QUIZ_YOUTUBE_CLIENT_SECRET: client_secret, QUIZ_YOUTUBE_REFRESH_TOKEN: refresh_token } = process.env;
  if (client_id && client_secret && refresh_token) return { client_id, client_secret, refresh_token };
  throw new Error('No quiz channel credentials: run `node youtube-automation/setup-youtube.js --out youtube-credentials-quiz.json` first');
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
  console.log(`Token belongs to: "${ch.snippet.title}" (${ch.snippet.customUrl || 'no handle yet'}, ${ch.statistics.videoCount} videos)`);
  if (/worldwidewebster/i.test(`${ch.snippet.title} ${ch.snippet.customUrl}`)) {
    throw new Error('That is the Mr. WorldWideWebster channel, not the new quiz channel. Re-run setup-youtube.js and pick the new channel.');
  }

  const branding = ch.brandingSettings || {};
  const next = {
    ...branding,
    channel: { ...(branding.channel || {}), description: DESCRIPTION, keywords: KEYWORDS, defaultLanguage: 'en' },
  };
  console.log('\nDescription:\n' + DESCRIPTION + '\n\nKeywords: ' + KEYWORDS);
  if (!apply) {
    console.log('\nDry run. Re-run with --apply to update the channel.');
    return;
  }

  const banner = await yt.channelBanners.insert({ media: { mimeType: 'image/png', body: fs.createReadStream(BANNER) } });
  next.image = { ...(branding.image || {}), bannerExternalUrl: banner.data.url };
  await yt.channels.update({ part: ['brandingSettings'], requestBody: { id: ch.id, brandingSettings: next } });
  console.log('\n✅ Description, keywords, language and banner updated.');
  console.log(`\nStill to do by hand in YouTube Studio > Customization (the API cannot do these):`);
  console.log(`  1. Name: ${NAME}`);
  console.log(`  2. Handle: ${HANDLE}`);
  console.log(`  3. Profile picture: ${AVATAR}`);
})().catch(e => { console.error('❌', e.message); process.exit(1); });
