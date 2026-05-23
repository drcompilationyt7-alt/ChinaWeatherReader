# Cookie Authentication Fix Summary

## Problem
YouTube, Bilibili, Douyin, TikTok, and other platforms were blocking downloads with "Sign in to confirm you're not a bot" errors because:
1. Camofox browser WebSocket connection was failing (`/json/version` endpoint not responding)
2. Cookie extraction wasn't working properly
3. yt-dlp needed proper cookie authentication

## Fixes Applied

### 1. Hermes CLI Wrapper (`hermes-agent/hermes-cli-wrapper.js`)
- Added default fallback values for environment variables (PATH, HOME, USER, PWD)
- Added `HERMES_ENV` variable pointing to `~/.hermes/.env`
- Ensured Hermes config paths work even when env vars are undefined in GitHub Actions

### 2. Universal Downloader (`sourcing/universal-downloader.js`)

#### Enhanced Cookie Extraction from Camofox:
- **Multiple WebSocket endpoints**: Try `/json/version`, `/json`, `/json/list` instead of just one
- **Additional WebSocket URL patterns**: Added `ws://localhost:9222/devtools/browser` fallback
- **Better error handling**: Graceful fallback if Puppeteer isn't available
- **Multiple cookie extraction methods**:
  1. Domain-specific cookies via `page.cookies(https://domain)`
  2. All cookies with filtering
  3. JavaScript evaluation (`document.cookie`) as last resort
- **Longer wait times**: Increased cookie settling time from 2s to 3s
- **Added referer**: Navigate with Google referer for more legitimate appearance

#### Enhanced yt-dlp Cookie Support:
- Changed from `--cookies-from-browser "chrome"` to `--cookies-from-browser "chromium:profile-directory=Default"` (Linux-compatible)
- Added browser cookie extraction for Bilibili, Douyin, TikTok, Instagram platforms
- Cookies are now extracted and saved in Netscape format before yt-dlp runs

### 3. GitHub Workflow (`.github/workflows/daily-create.yml`)
- Added verification for `/json/list` endpoint (required by Puppeteer)
- Exported `HERMES_CONFIG` and `HERMES_ENV` to `$GITHUB_ENV` for child processes
- Removed premature `unset` of API keys during Hermes installation (was breaking config detection)

## How It Works Now

1. **Before downloading**, the system attempts to extract cookies from Camofox browser
2. **Camofox connection** tries multiple methods:
   - HTTP API (`/api/cookies?domain=...`)
   - WebSocket via `/json/version`, `/json`, `/json/list`
   - Direct WebSocket URLs including port 9222
3. **Once connected**, it navigates to the target domain to establish a legitimate session
4. **Cookies are extracted** using three fallback methods and saved in Netscape format
5. **yt-dlp uses these cookies** via `--cookies` flag to bypass bot detection
6. **Fallback to browser cookies** via `--cookies-from-browser "chromium"` if file-based cookies fail

## Supported Platforms
All platforms now have enhanced cookie authentication:
- ✅ YouTube
- ✅ Bilibili  
- ✅ Douyin
- ✅ TikTok
- ✅ Instagram
- ✅ Twitter/X
- ✅ Facebook
- ✅ Xiaohongshu (Rednote)
- ✅ Weibo

## Testing
Run the workflow manually with debug mode to verify:
```yaml
workflow_dispatch:
  inputs:
    debug: true
```

Check logs for:
- "✅ Connected to Camofox" message
- "Found X cookies for [domain]" message
- "Using cookies from: [path]" message
- Successful download without "Sign in to confirm you're not a bot" error
