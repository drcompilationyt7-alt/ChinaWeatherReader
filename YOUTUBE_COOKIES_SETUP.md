# 🍪 Setting Up YouTube Cookies for yt-dlp

## Why You Need This

YouTube frequently blocks automated downloads from GitHub Actions, cloud VMs, and shared IPs with the error:
```
ERROR: [youtube] VIDEO_ID: Sign in to confirm you're not a bot
```

To bypass this, you need to provide YouTube cookies from a real logged-in browser session.

## How to Export YouTube Cookies

### Option 1: Using a Browser Extension (Easiest)

1. **Install the "Get cookies.txt LOCALLY" extension:**
   - Chrome: https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc
   - Firefox: https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/

2. **Go to YouTube and make sure you're logged in**

3. **Click the extension icon** → Download cookies.txt

4. **Open the downloaded file** and copy all contents

5. **Add to GitHub Secrets:**
   - Go to your repo → Settings → Secrets and variables → Actions
   - Click "New repository secret"
   - Name: `YOUTUBE_COOKIES`
   - Value: Paste the entire contents of cookies.txt
   - Click "Add secret"

### Option 2: Using command-line tools (Advanced)

If you have `curl` and are comfortable with command line:

```bash
# Install cookies extractor
pip install cookiecutter

# Extract cookies from your browser
# For Chrome on Linux:
python3 -m http.cookiejar.MozillaCookieJar | grep youtube.com

# Or use the get-cookies python package:
pip install get-cookies
python -c "from get_cookies import get_cookies; print(get_cookies('chrome', 'youtube.com'))"
```

### Option 3: Manual extraction from browser dev tools

1. Open YouTube in your browser (logged in)
2. Press F12 to open DevTools
3. Go to Application tab → Cookies → https://www.youtube.com
4. Copy each cookie name and value
5. Format as Netscape cookie format:
   ```
   .youtube.comTRUE/TRUEEXPIRE_TIMECONSENTVALUE
   .youtube.comTRUE/TRUEEXPIRE_TIMELOGIN_INFOVALUE
   ...
   ```

## Verifying It Works

After adding the secret, the next GitHub Actions run will:
1. Write cookies to `/tmp/youtube_cookies.txt`
2. Pass them to yt-dlp with `--cookies` flag
3. Use Android client emulator to further bypass detection

You should see in the logs:
```
✅ YouTube cookies loaded for yt-dlp
Using cookies from: /tmp/youtube_cookies.txt
```

## Troubleshooting

### Cookies expire
Cookies typically last 2 weeks to several months. If downloads start failing again:
1. Re-export fresh cookies from your browser
2. Update the `YOUTUBE_COOKIES` secret
3. Trigger a new workflow run

### Still getting bot errors
Try these additional steps:
1. Make sure you're logged into YouTube in the browser you export from
2. Watch a few videos manually before exporting (makes account look more legitimate)
3. Use a Google account that has normal viewing history
4. Consider using multiple accounts and rotating cookies

### Cookie file too large
If your cookies.txt is very large (>10KB):
1. Open the file in a text editor
2. Keep only cookies for `.youtube.com`, `.google.com`, and `.youtu.be`
3. Delete cookies for other domains
4. Save and update the secret

## Security Notes

- ✅ Cookies are stored encrypted in GitHub Secrets
- ✅ Only accessible by your workflows
- ⚠️ Don't commit cookies.txt to the repository
- ⚠️ Rotate cookies every few weeks as a best practice
- ⚠️ Use a dedicated Google account if concerned about security

## Alternative: Skip YouTube Downloads

If you can't get cookies to work, the system has fallbacks:
- OneForAllDownloader.com (browser automation)
- Hermes Puppeteer direct scraping
- Other video platforms (Bilibili, TikTok, etc.)

But cookies provide the most reliable YouTube downloads.
