#!/usr/bin/env node

/**
 * Mr. WorldWideWebster — YouTube OAuth Setup
 *
 * Run this ONCE on your local computer to get a YOUTUBE_REFRESH_TOKEN.
 * This token lets GitHub Actions upload videos to your channel permanently.
 *
 * Usage:
 *   npm install
 *   node youtube-automation/setup-youtube.js
 *
 * Environment variables needed (or you'll be prompted):
 *   YOUTUBE_CLIENT_ID
 *   YOUTUBE_CLIENT_SECRET
 *   YOUTUBE_API_KEY (optional but recommended)
 *
 * After getting the refresh token, store it in GitHub Secrets:
 *   https://github.com/YOUR_USERNAME/YOUR_REPO/settings/secrets/actions
 */
const { google } = require('googleapis');
const http = require('http');
const url = require('url');
const { promises: fs } = require('fs');
const path = require('path');
const crypto = require('crypto');

// ─── Config ───────────────────────────────────────────────────────────────

const PORT = 8080;
const REDIRECT_URI = `http://localhost:${PORT}/oauth2callback`;
const SCOPES = [
  'https://www.googleapis.com/auth/youtube.upload',
  'https://www.googleapis.com/auth/youtube',
  'https://www.googleapis.com/auth/youtubepartner',
];

// ─── Get Credentials ─────────────────────────────────────────────────────

function getCredentials() {
  // Try environment variables first
  let clientId = process.env.YOUTUBE_CLIENT_ID;
  let clientSecret = process.env.YOUTUBE_CLIENT_SECRET;

  // If not set, prompt user
  if (!clientId || !clientSecret) {
    const readline = require('readline').createInterface({
      input: process.stdin,
      output: process.stdout,
    });

    return new Promise((resolve) => {
      console.log('\n📋 YouTube OAuth Setup - Step 1: Enter Credentials\n');

      const ask = (question) => new Promise((r) => readline.question(question, r));

      (async () => {
        if (!clientId) {
          clientId = await ask('Enter your YOUTUBE_CLIENT_ID: ');
        }
        if (!clientSecret) {
          clientSecret = await ask('Enter your YOUTUBE_CLIENT_SECRET: ');
        }
        readline.close();
        resolve({ clientId: clientId.trim(), clientSecret: clientSecret.trim() });
      })();
    });
  }

  return Promise.resolve({ clientId, clientSecret });
}

// ─── Start OAuth Flow ────────────────────────────────────────────────────

async function startOAuthFlow(credentials) {
  const { clientId, clientSecret } = credentials;

  // Create OAuth2 client
  const oauth2Client = new google.auth.OAuth2(clientId, clientSecret, REDIRECT_URI);

  // Generate auth URL
  const authUrl = oauth2Client.generateAuthUrl({
    access_type: 'offline',
    scope: SCOPES,
    prompt: 'consent', // Force refresh token to be returned
  });

  console.log('\n' + '═'.repeat(60));
  console.log('🔑 YouTube OAuth Setup - Step 2: Authorize the App\n');
  console.log('A browser window will open shortly.');
  console.log('If it doesn\'t, copy and paste this URL into your browser:\n');
  console.log(authUrl);
  console.log('\n' + '═'.repeat(60) + '\n');

  // Open browser automatically
  const { exec } = require('child_process');
  const platform = process.platform;
  try {
    if (platform === 'win32') {
      exec(`start "" "${authUrl}"`);
    } else if (platform === 'darwin') {
      exec(`open "${authUrl}"`);
    } else {
      exec(`xdg-open "${authUrl}"`);
    }
  } catch {
    console.log('(Could not open browser automatically)');
  }

  // Start local server to receive the callback
  return new Promise((resolve, reject) => {
    const server = http.createServer(async (req, res) => {
      const query = url.parse(req.url, true).query;

      if (query.code) {
        // Authorization code received
        res.writeHead(200, { 'Content-Type': 'text/html' });
        res.end(`
          <html><body style="font-family: sans-serif; padding: 40px; text-align: center;">
            <h1 style="color: #4CAF50;">✅ Authorization Successful!</h1>
            <p>You can close this window and return to the terminal.</p>
            <p>The refresh token is being generated...</p>
          </body></html>
        `);

        server.close();

        try {
          // Exchange code for tokens
          const { tokens } = await oauth2Client.getToken(query.code);
          oauth2Client.setCredentials(tokens);

          // Verify by calling YouTube API
          const youtube = google.youtube({ version: 'v3', auth: oauth2Client });
          const channelResponse = await youtube.channels.list({
            part: ['snippet'],
            mine: true,
          });

          const channelName = channelResponse.data.items?.[0]?.snippet?.title || 'Your Channel';

          console.log('\n' + '✅'.repeat(30));
          console.log('\n🎉 AUTHORIZATION SUCCESSFUL!\n');
          console.log(`   Channel: ${channelName}\n`);

          // Display the refresh token
          console.log('═'.repeat(60));
          console.log('📋 COPY THIS REFRESH TOKEN:\n');
          console.log(tokens.refresh_token);
          console.log('\n');
          console.log('═'.repeat(60));
          console.log('\n📌 Store it in GitHub Secrets as: YOUTUBE_REFRESH_TOKEN\n');
          console.log('📌 Also store these (you already have them):');
          console.log('   YOUTUBE_CLIENT_ID = ' + clientId);
          console.log('   YOUTUBE_CLIENT_SECRET = ' + clientSecret);
          console.log('\n   (Optional) YOUTUBE_API_KEY = from Google Cloud Console\n');

          // Save to a local file as backup
          const outputPath = path.join(__dirname, '..', 'youtube-credentials.json');
          const creds = {
            client_id: clientId,
            client_secret: clientSecret,
            refresh_token: tokens.refresh_token,
            channel: channelName,
            generated_at: new Date().toISOString(),
          };
          await fs.writeFile(outputPath, JSON.stringify(creds, null, 2));
          console.log(`💾 Backup saved to: ${outputPath}\n`);

          resolve({
            refreshToken: tokens.refresh_token,
            channelName,
          });
        } catch (error) {
          console.error('\n❌ Failed to get refresh token:', error.message);
          reject(error);
        }
      } else if (query.error) {
        res.writeHead(400, { 'Content-Type': 'text/html' });
        res.end(`<html><body><h1>❌ Authorization Failed</h1><p>${query.error}</p></body></html>`);
        server.close();
        reject(new Error(`Authorization failed: ${query.error}`));
      } else {
        res.writeHead(200, { 'Content-Type': 'text/html' });
        res.end('<html><body><h1>Waiting for authorization...</h1></body></html>');
      }
    });

    server.listen(PORT, () => {
      console.log(`\n🌐 Local server listening on http://localhost:${PORT}`);
      console.log('Waiting for authorization...\n');
    });

    server.on('error', (error) => {
      if (error.code === 'EADDRINUSE') {
        console.error(`\n❌ Port ${PORT} is already in use. Close the other process and try again.`);
      } else {
        console.error(`\n❌ Server error: ${error.message}`);
      }
      reject(error);
    });
  });
}

// ─── Main ────────────────────────────────────────────────────────────────

async function main() {
  console.log('\n' + '🎬'.repeat(15));
  console.log('\n🎬 Mr. WorldWideWebster - YouTube OAuth Setup\n');
  console.log('This will get a refresh token that lets GitHub Actions');
  console.log('upload videos to your YouTube channel automatically.\n');

  const credentials = await getCredentials();
  const result = await startOAuthFlow(credentials);

  console.log('✅ YouTube OAuth setup complete!');
  console.log(`📺 Channel: ${result.channelName}`);
  console.log('\n📋 Next steps:');
  console.log('1. Go to your GitHub repo → Settings → Secrets → Actions');
  console.log('2. Add these secrets:');
  console.log('   - YOUTUBE_REFRESH_TOKEN = (the token above)');
  console.log('   - YOUTUBE_CLIENT_ID = ' + credentials.clientId);
  console.log('   - YOUTUBE_CLIENT_SECRET = ' + credentials.clientSecret);
  console.log('3. Run your first workflow from the Actions tab!\n');
}

main().catch((error) => {
  console.error('\n❌ Setup failed:', error.message);
  process.exit(1);
});