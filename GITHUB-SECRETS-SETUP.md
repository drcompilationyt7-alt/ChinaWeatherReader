# 📋 GitHub Secrets Setup Guide

Go to your GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**

Add these **8 secrets** one by one:

---

## 1. `OPENROUTER_API_KEY` — AI Brain (Primary)

**How to get:** 
1. Go to https://openrouter.ai/keys
2. Sign in (Google/GitHub)
3. Click **"Create Key"**
4. Copy the key (starts with `sk-or-v1-`)

**Cost:** Free tier available ($1 credit)

---

## 1b. `OPENROUTER_API_KEY_2`, `OPENROUTER_API_KEY_3`, `OPENROUTER_API_KEY_4` — Backup Keys

**Up to 4 total keys** for automatic rotation when one runs out of credits or hits rate limits.

**How to get:** Same process as above. Open multiple OpenRouter accounts, or use different providers:
- Key 2: Another OpenRouter account
- Key 3: Another OpenRouter account
- Key 4: Another OpenRouter account

**How it works:**
1. The system tries `openrouter/owl-alpha` first with Key 1
2. If HTTP 402 (Payment Required) or 429 (Rate Limited), it auto-rotates to Key 2
3. If that also fails, it tries the **free model** `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`  
4. If still failing, rotates to Key 3, then Key 4
5. Each key/model combination is tried before giving up

**Cost:** Free (nemotron is always free) + free credits from each account

---

## 2. `YOUTUBE_CLIENT_ID` — YouTube Upload Access

**How to get:**
1. Go to https://console.cloud.google.com/
2. Create a project → **APIs & Services → Library**
3. Search "YouTube Data API v3" → **Enable**
4. Go to **Credentials → Create Credentials → OAuth Client ID**
5. App type: **Desktop app**
6. Name: "Mr. WorldWideWebster"
7. Copy the **Client ID** shown in the popup

**Cost:** Free

---

## 3. `YOUTUBE_CLIENT_SECRET` — YouTube Upload Password

**How to get:** Same popup as Client ID above. Copy the **Client Secret**.

**Cost:** Free

---

## 4. `YOUTUBE_REFRESH_TOKEN` — YouTube Upload Permission

**How to get:**
1. Run this on your computer ONE TIME:
   ```bash
   cd mr-worldwidewebster
   npm install
   ```
2. Then run:
   ```bash
   # Windows CMD:
   set YOUTUBE_CLIENT_ID=your-client-id
   set YOUTUBE_CLIENT_SECRET=your-client-secret
   node youtube-automation/setup-youtube.js
   ```
   (Replace with your actual Client ID and Secret)
3. A browser will open → Log into your YouTube channel's Google account → Click **Allow**
4. The script will print your **Refresh Token** → Copy it

**Cost:** Free (YouTube gives 10,000 quota/day for free)

---

## 5. `YOUTUBE_API_KEY` — YouTube Data Access

**How to get:**
1. Google Cloud Console → **APIs & Services → Credentials**
2. Click **Create Credentials → API Key**
3. Copy the key

**Cost:** Free

---

## 6. `DISCORD_BOT_TOKEN` — Daily Notifications

**How to get:**
1. Go to https://discord.com/developers/applications
2. Click **New Application** → Name: "Mr. WorldWideWebster"
3. Go to **Bot** tab (left sidebar)
4. Click **Reset Token** → Copy the token
5. Also enable: **Message Content Intent** (under Privileged Gateway Intents)

**Cost:** Free

---

## 7. `DISCORD_USER_ID` — Your Discord DM

**Required if using Installation flow.** The bot will DM you daily summaries.

**How to get:**
1. Discord → **User Settings → Advanced → Developer Mode** (ON)
2. Right-click your own name in any message or member list
3. Click **Copy User ID**
4. This is a long number like `123456789012345678`

---

## 8. `DISCORD_CHANNEL_ID` — Channel to Post In (Optional)

**Only if you want channel posts instead of DMs.** Overrides the User ID.

**How to get:**
1. Discord → **User Settings → Advanced → Developer Mode** (ON)
2. Right-click the channel you want the bot to post in
3. Click **Copy Channel ID**

---

## 9. `GH_PAT` — Git Push Permission

**How to get:**
1. GitHub → **Settings → Developer settings → Personal access tokens → Tokens (classic)**
2. Click **Generate new token (classic)**
3. Scopes: Check **repo** (full control), **workflow**
4. Click **Generate** → Copy the token

**Cost:** Free

---

## ✅ Summary Checklist

- [ ] `OPENROUTER_API_KEY` — https://openrouter.ai/keys
- [ ] `YOUTUBE_CLIENT_ID` — Google Cloud Console
- [ ] `YOUTUBE_CLIENT_SECRET` — Google Cloud Console
- [ ] `YOUTUBE_REFRESH_TOKEN` — Run setup script locally (1 time)
- [ ] `YOUTUBE_API_KEY` — Google Cloud Console
- [ ] `DISCORD_BOT_TOKEN` — https://discord.com/developers
- [ ] `DISCORD_USER_ID` — Right-click your name in Discord → Copy ID
- [ ] `DISCORD_CHANNEL_ID` — (optional) Right-click channel in Discord
- [ ] `GH_PAT` — GitHub Developer Settings

---

After adding all secrets, go to your repo **Actions** tab → **🌙 Midnight Review** → **Run workflow** (test Hermes Agent first, no YouTube needed).
Then → **🌅 Daily Shorts Creation** → **Run workflow** → Enable **debug** → **Run**
