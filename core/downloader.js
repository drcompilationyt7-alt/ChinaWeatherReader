/**
 * Downloader module
 * Uses browser cookies from env var + yt-dlp with throttling to avoid bot detection.
 * Set YOUTUBE_COOKIES secret in GitHub with Netscape cookie format.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('Downloader');

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}.mp4`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Write cookies from env if available (set via GitHub Secret YOUTUBE_COOKIES)
  const cookieFile = path.join(outputDir, 'yt_cookies.txt');
  if (process.env.YOUTUBE_COOKIES) {
    try { fs.writeFileSync(cookieFile, process.env.YOUTUBE_COOKIES); } catch {}
  }

  // Try strategies with cookies first, then without
  const strategies = [];

  // Strategy 1: cookies + throttle (most likely to work)
  if (fs.existsSync(cookieFile) && fs.statSync(cookieFile).size > 50) {
    strategies.push(
      `yt-dlp --cookies "${cookieFile}" --throttled-rate 100K -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M 2>&1`
    );
  }

  // Strategy 2: visitor_data approach (for YouTube)
  if (platform === 'youtube') {
    strategies.push(
      `yt-dlp --extractor-args "youtube:player_client=web;player_skip=webpage,js" --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36" --throttled-rate 50K -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M 2>&1`,
      `yt-dlp --throttled-rate 50K -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M 2>&1`,
    );
  }

  // Strategy 3: generic fallback
  strategies.push(
    `yt-dlp -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M 2>&1`
  );

  for (let s = 0; s < strategies.length; s++) {
    try {
      execSync(strategies[s], { timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8' });
      if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 500000) {
        logger.success(`Strategy ${s+1}: ${(fs.statSync(outputFile).size/1024/1024).toFixed(1)}MB`);
        try { fs.unlinkSync(cookieFile); } catch {}
        return { path: outputFile, title, platform, sourceUrl: url };
      }
    } catch (e) {
      logger.warn(`Strategy ${s+1}: ${e.message.substring(0,100)}`);
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
