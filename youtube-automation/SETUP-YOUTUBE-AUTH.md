# 🎬 Setting Up YouTube OAuth for Mr. WorldWideWebster

This guide will walk you through getting the `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, and `YOUTUBE_REFRESH_TOKEN` needed for the **free** YouTube Data API v3.

## Total time: ~15 minutes • Cost: FREE

---

## Step 1: Create a Google Cloud Project

1. Go to **https://console.cloud.google.com/**
2. Click the project dropdown at the top → **New Project**
3. Name it **"Mr. WorldWideWebster"** (or anything you like)
4. Click **Create**

## Step 2: Enable the YouTube Data API v3

1. From your project dashboard, go to **APIs & Services → Library**
2. Search for **"YouTube Data API v3"**
3. Click on it → **Enable**

## Step 3: Create OAuth Credentials

1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials** → **OAuth Client ID**
3. If you haven't set up the consent screen yet, click **Configure Consent Screen**
   - Choose **External** (anyone can authorize)
   - App name: **"Mr. WorldWideWebster"**
   - User support email: **your email**
   - Developer contact info: **your email**
   - Click **Save and Continue** through all pages (you don't need to add scopes)
   - Back on the Credentials page, click **+ Create Credentials** → **OAuth Client ID**
4. Application type: **Desktop app** (or **Web application**)
5. Name: **"YouTube Uploader"**
6. Under **Authorized redirect URIs**, add:
   ```
   http://localhost:8080/oauth2callback
   ```
7. Click **Create**
8. **IMPORTANT**: You'll see a popup with your **Client ID** and **Client Secret**.
   - Copy **both** values immediately
   - You won't see the secret again!

## Step 4: Run the Setup Script

1. Open a terminal in the `mr-worldwidewebster` directory
2. Run:
   ```bash
   npm install
   ```
3. Set up your environment (or just pass values directly):
   ```bash
   # On Windows (CMD):
   set YOUTUBE_CLIENT_ID=your-client-id.apps.googleusercontent.com
   set YOUTUBE_CLIENT_SECRET=your-client-secret
   set YOUTUBE_API_KEY=your-api-key
   node youtube-automation/setup-youtube.js
   
   # On Windows (PowerShell):
   $env:YOUTUBE_CLIENT_ID="your-client-id.apps.googleusercontent.com"
   $env:YOUTUBE_CLIENT_SECRET="your-client-secret"
   $env:YOUTUBE_API_KEY="your-api-key"
   node youtube-automation/setup-youtube.js
   
   # Or create a .env file with those values
   ```

4. The script will:
   - Open a browser window asking you to log in to your YouTube/Google account
   - **Use the same account that owns your YouTube channel**
   - Click **Continue** (you may see a warning about an unverified app — click "Advanced" → "Go to app")
   - Click **Allow** to grant permission

5. After authorizing, the browser will redirect to a local page.
   - The script will capture the authorization code
   - It will exchange it for a **Refresh Token**
   - **Save the refresh token!** You'll add it to GitHub Secrets

## Step 5: What You Get

After completing the setup, you'll have these 3 values:

| Secret Name | Where to Put It |
|------------|-----------------|
| `YOUTUBE_CLIENT_ID` | GitHub Secrets |
| `YOUTUBE_CLIENT_SECRET` | GitHub Secrets |
| `YOUTUBE_REFRESH_TOKEN` | GitHub Secrets |
| `YOUTUBE_API_KEY` | GitHub Secrets (optional but recommended) |

## Step 6: Add to GitHub Secrets

1. Go to your GitHub repository
2. **Settings → Secrets and variables → Actions**
3. Click **New repository secret**
4. Add each value:

   | Name | Value |
   |------|-------|
   | `YOUTUBE_CLIENT_ID` | `123456789-xxxx.apps.googleusercontent.com` |
   | `YOUTUBE_CLIENT_SECRET` | `GOCSPX-xxxxxxxxxxxx` |
   | `YOUTUBE_REFRESH_TOKEN` | `1//0gxxxxxxxxxxxxxxxxxxxx` |
   | `YOUTUBE_API_KEY` | `AIzaSyxxxxxxxxxxxxxxxxxxxx` |

5. Also add:
   - `OPENROUTER_API_KEY` — from https://openrouter.ai/
   - `DISCORD_BOT_TOKEN` — from https://discord.com/developers/applications
   - `GH_PAT` — from GitHub Settings → Developer settings → Personal access tokens

## How It Works

The YouTube Bridge uses OAuth 2.0 with a **refresh token**. This means:

1. The first time, you authorize the app (Step 4)
2. The app gets a short-lived access token + a long-lived refresh token
3. Every time a GitHub Action runs:
   - The bridge loads the refresh token from `YOUTUBE_REFRESH_TOKEN` secret
   - It automatically gets a fresh access token
   - It uploads the video
   - **You never need to re-authorize** (the refresh token lasts until you revoke it)

## Troubleshooting

**"Token has been expired or revoked"**
- The refresh token can expire if you haven't used it for 6+ months
- Just run the setup script again to get a new one

**"Access to this account has been temporarily limited"**
- This happens if you upload too many videos at once
- The system is set to max 5 uploads per day — stay within that

**"quotaExceeded"**
- YouTube Data API v3 has a daily quota of 10,000 units
- Each upload costs ~1,600 units
- You can upload about 6 videos per day for free
- To increase, go to Google Cloud → IAM & Admin → Quotas → Request more

**Browser doesn't open automatically**
- The script will print a URL in the console
- Copy and paste it into your browser manually

---

## Need Help?

If you get stuck:
1. Make sure you're using the **same Google account** that owns your YouTube channel
2. Check that the **redirect URI** exactly matches: `http://localhost:8080/oauth2callback`
3. Make sure the **YouTube Data API v3** is enabled (wait 5 minutes after enabling)