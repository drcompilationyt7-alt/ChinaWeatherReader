#!/usr/bin/env node
/**
 * Mainland Unpacked: one-time YouTube login for the uploader.
 *
 *   node scripts/china/setup-token.js
 *
 * 1. Reuses the OAuth client (id + secret) the other two channels use, from youtube-credentials.json,
 *    so nothing is typed in.
 * 2. Opens the Google sign-in page. Pick the account, then the MAINLAND UNPACKED channel when asked.
 * 3. Checks the token is for that channel. It refuses Zero Yen Otaku and Asian Pop Quiz.
 * 4. Saves youtube-credentials-china.json (gitignored) and stores the GitHub secrets
 *    CHINA_YOUTUBE_CLIENT_ID / _CLIENT_SECRET / _REFRESH_TOKEN plus the repo variable CHINA_CHANNEL_ID.
 *    Values go to gh over stdin and are never printed.
 */
const fs = require('fs');
const path = require('path');
const http = require('http');
const { execFileSync, exec } = require('child_process');
const { google } = require('googleapis');

const ROOT = path.join(__dirname, '..', '..');
const REPO = 'drcompilationyt7-alt/ChinaWeatherReader';
const PORT = 8080;
const REDIRECT = `http://localhost:${PORT}/oauth2callback`;
const SCOPES = [
  'https://www.googleapis.com/auth/youtube.upload',
  'https://www.googleapis.com/auth/youtube',
  'https://www.googleapis.com/auth/youtube.force-ssl', // comments: list + reply
  'https://www.googleapis.com/auth/yt-analytics.readonly',
];
const OTHER_CHANNELS = { UC2_aNJgemODOCsOLsYwaelg: 'Zero Yen Otaku' };

function client() {
  const c = JSON.parse(fs.readFileSync(path.join(ROOT, 'youtube-credentials.json'), 'utf8'));
  if (!c.client_id || !c.client_secret) throw new Error('youtube-credentials.json has no client_id / client_secret');
  return c;
}

function ghCan() {
  try {
    const p = JSON.parse(execFileSync('gh', ['api', `repos/${REPO}`, '--jq', '.permissions'], { encoding: 'utf8' }));
    return !!p.push;
  } catch { return false; }
}

function setSecret(name, value, kind = 'secret') {
  execFileSync('gh', [kind, 'set', name, '--repo', REPO], { input: value, stdio: ['pipe', 'ignore', 'inherit'] });
  console.log(`  ✓ ${kind} ${name}`);
}

(async () => {
  if (!ghCan()) {
    console.error(`The active GitHub account can't write secrets to ${REPO}.\nRun:  gh auth switch -u drcompilationyt7-alt   then run this again.`);
    process.exit(1);
  }

  const { client_id, client_secret } = client();
  const oauth = new google.auth.OAuth2(client_id, client_secret, REDIRECT);
  const url = oauth.generateAuthUrl({ access_type: 'offline', prompt: 'consent', scope: SCOPES });

  const code = await new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const u = new URL(req.url, `http://localhost:${PORT}`);
      if (u.pathname !== '/oauth2callback') { res.end(); return; }
      const err = u.searchParams.get('error');
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(err ? `<h2>Sign-in failed: ${err}</h2>` : '<h2>Done. You can close this tab and go back to the terminal.</h2>');
      server.close();
      err ? reject(new Error(err)) : resolve(u.searchParams.get('code'));
    });
    server.listen(PORT, () => {
      console.log('\nOpening Google sign-in. If no browser opens, paste this link into one:\n');
      console.log(url + '\n');
      console.log('➡  Choose your Google account, then pick the "Mainland Unpacked" channel, then Allow.\n');
      exec(`start "" "${url}"`);
    });
  });

  const { tokens } = await oauth.getToken(code);
  if (!tokens.refresh_token) throw new Error('Google returned no refresh token. Run this again (it forces the consent screen).');
  oauth.setCredentials(tokens);
  const ch = (await google.youtube({ version: 'v3', auth: oauth }).channels.list({ part: ['snippet'], mine: true })).data.items?.[0];
  if (!ch) throw new Error('That Google account has no YouTube channel selected. Run again and pick the Mainland Unpacked channel.');
  console.log(`Token belongs to: "${ch.snippet.title}" (${ch.snippet.customUrl || 'no handle yet'}, ${ch.id})`);
  const other = OTHER_CHANNELS[ch.id] || (/asian ?pop ?quiz|zero ?yen|worldwidewebster/i.test(`${ch.snippet.title} ${ch.snippet.customUrl}`) && ch.snippet.title);
  if (other) throw new Error(`That's ${other}, not Mainland Unpacked. Run again and pick the new channel.`);

  fs.writeFileSync(path.join(ROOT, 'youtube-credentials-china.json'), JSON.stringify({
    client_id, client_secret, refresh_token: tokens.refresh_token,
    channel: { id: ch.id, title: ch.snippet.title, handle: ch.snippet.customUrl || null },
    generated_at: new Date().toISOString(),
  }, null, 2));
  console.log('  ✓ saved youtube-credentials-china.json (gitignored)');
  setSecret('CHINA_YOUTUBE_CLIENT_ID', client_id);
  setSecret('CHINA_YOUTUBE_CLIENT_SECRET', client_secret);
  setSecret('CHINA_YOUTUBE_REFRESH_TOKEN', tokens.refresh_token);
  setSecret('CHINA_CHANNEL_ID', ch.id, 'variable');
  console.log('\nAll set. The daily upload workflow can now post to this channel.');
})().catch(e => { console.error('\n✗ ' + e.message); process.exit(1); });
