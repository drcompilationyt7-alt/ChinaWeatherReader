/**
 * Downloader module
 * Uses yt-dlp with platform-specific strategies including TV/mobile clients
 * Creates temporary cookies for bot bypass
 */
const { spawnSync, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('Downloader');

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputTemplate = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Build a cookies file (generic, works for YouTube)
  const cookieFile = path.join(outputDir, 'cookies.txt');
  // Write some common cookies
  const cookies = `# Netscape HTTP Cookie File
.youtube.com	TRUE	/	TRUE	0	SOCS	CAI
.youtube.com	TRUE	/	TRUE	0	__Secure-3PSID	missing
.google.com	TRUE	/	TRUE	0	NID	missing`;
  try { fs.writeFileSync(cookieFile, cookies); } catch {}

  // Build try list per platform
  const tryCommands = [];

  if (platform === 'youtube') {
    // Try different player clients - TV is least restrictive
    tryCommands.push(
      `yt-dlp --extractor-args "youtube:player_client=tv" --cookies "${cookieFile}" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M`,
      `yt-dlp --extractor-args "youtube:player_client=android" --cookies "${cookieFile}" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M`,
      `yt-dlp --extractor-args "youtube:skip=webpage" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M`,
      // Try without any args (maybe deno fixes it)
      `yt-dlp -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M`,
      // Last resort: just get best format
      `python3 -m yt_dlp --extractor-args "youtube:player_client=tv" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M`,
    );
  } else if (platform === 'bilibili') {
    // Bilibili: use referer + try without headers
    tryCommands.push(
      `yt-dlp --add-header "Referer:https://www.bilibili.com/" --add-header "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36" --add-header "Origin:https://www.bilibili.com" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M`,
      `yt-dlp --user-agent "BiliApp/1.0.0 (Android 14; SDK 34)" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M`,
      // Try with cookies from common Bilibili session cookie
      `yt-dlp --add-header "Referer:https://www.bilibili.com/" --add-header "Cookie:buvid3=local" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M`,
      `python3 -m yt_dlp --add-header "Referer:https://www.bilibili.com/" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M`,
    );
  } else {
    tryCommands.push(
      `yt-dlp -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M`,
    );
  }

  // Build env with deno in PATH
  const env = { ...process.env };
  for (const p of ['/home/runner/.deno/bin', '/root/.deno/bin', '/usr/local/bin']) {
    if (fs.existsSync(p)) env.PATH = `${p}:${env.PATH || ''}`;
  }
  // Also try standard deno path
  try {
    const denoPath = execSync('which deno 2>/dev/null || echo ""', { timeout: 5000, encoding: 'utf8' }).trim();
    if (denoPath) {
      const dir = path.dirname(denoPath);
      env.PATH = `${dir}:${env.PATH || ''}`;
      logger.info(`Found deno at ${denoPath}`);
    }
  } catch {}

  for (let s = 0; s < tryCommands.length; s++) {
    try {
      const r = execSync(tryCommands[s], { timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8', env });

      // Find downloaded file
      const files = fs.readdirSync(outputDir)
        .filter(f => f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mkv'))
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        const sizeMB = fs.statSync(fp).size / 1024 / 1024;
        if (sizeMB > 1) {
          logger.success(`Downloaded: ${files[0]} (${sizeMB.toFixed(1)}MB)`);
          try { fs.unlinkSync(cookieFile); } catch {}
          return { path: fp, title, platform, sourceUrl: url };
        }
        if (sizeMB > 0.1) {
          logger.warn(`File small (${sizeMB.toFixed(1)}MB) but keeping`);
          try { fs.unlinkSync(cookieFile); } catch {}
          return { path: fp, title, platform, sourceUrl: url };
        }
      }
      
      // Check if the stdout mentions a file was written
      if (r.includes('Destination') && r.includes('.mp4')) {
        const match = r.match(/Destination: ([^\n]+\.mp4)/);
        if (match && fs.existsSync(match[1])) {
          const sizeMB = fs.statSync(match[1]).size / 1024 / 1024;
          if (sizeMB > 0.5) {
            logger.success(`Downloaded: ${path.basename(match[1])} (${sizeMB.toFixed(1)}MB)`);
            try { fs.unlinkSync(cookieFile); } catch {}
            return { path: match[1], title, platform, sourceUrl: url };
          }
        }
      }
    } catch (e) {
      const msg = e.message.substring(0, 150);
      logger.warn(`Try ${s+1} failed: ${msg}`);
      // Check if it actually succeeded despite error (some yt-dlp errors still download)
      const files = fs.readdirSync(outputDir)
        .filter(f => f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mkv'))
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        const sizeMB = fs.statSync(fp).size / 1024 / 1024;
        if (sizeMB > 0.1) {
          logger.success(`Actually downloaded despite error: ${files[0]} (${sizeMB.toFixed(1)}MB)`);
          try { fs.unlinkSync(cookieFile); } catch {}
          return { path: fp, title, platform, sourceUrl: url };
        }
      }
    }
  }

  try { fs.unlinkSync(cookieFile); } catch {}
  logger.warn(`All failed: ${url.substring(0,60)}`);
  return null;
}

async function downloadVideos(urls, outputDir) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const downloaded = [];
  for (let i = 0; i < Math.min(urls.length, 5); i++) {
    const result = await downloadVideo(urls[i], outputDir);
    if (result) downloaded.push(result);
  }
  logger.success(`Downloaded ${downloaded.length}/${Math.min(urls.length,5)}`);
  return downloaded;
}

module.exports = { downloadVideo, downloadVideos };
