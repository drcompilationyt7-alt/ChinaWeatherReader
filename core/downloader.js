/**
 * Downloader module
 * Uses yt-dlp with multiple strategies to bypass CI IP blocks
 * Falls back to Python + aiohttp if yt-dlp fails
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
  
  const outputTemplate = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Try all strategies in order
  const strategies = [];

  if (platform === 'youtube') {
    strategies.push(
      // Android client (best for CI)
      `yt-dlp --extractor-args "youtube:player_client=android" -f "best[height<=720]" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M 2>&1`,
      // Web with Android UA
      `yt-dlp --user-agent "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36" -f "best[height<=720]" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M 2>&1`,
      // Best format no filter
      `yt-dlp --extractor-args "youtube:player_client=android" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M 2>&1`
    );
  } else if (platform === 'bilibili') {
    strategies.push(
      // With proper referer
      `yt-dlp --add-header "Referer:https://www.bilibili.com/" --add-header "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" --add-header "Origin:https://www.bilibili.com" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M 2>&1`,
      // TV app headers
      `yt-dlp --add-header "Referer:https://www.bilibili.com/" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M 2>&1`
    );
  } else {
    strategies.push(
      `yt-dlp -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M 2>&1`
    );
  }

  for (let s = 0; s < strategies.length; s++) {
    try {
      const output = execSync(strategies[s], { timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8' });

      // Find downloaded file
      const files = fs.readdirSync(outputDir)
        .filter(f => f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mkv'))
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        const sizeMB = fs.statSync(fp).size / 1024 / 1024;
        if (sizeMB > 1) {
          logger.success(`Downloaded: ${files[0]} (${sizeMB.toFixed(1)}MB)`);
          return { path: fp, title, platform, sourceUrl: url };
        }
        // Too small, likely an error file
        logger.warn(`File too small (${sizeMB.toFixed(1)}MB)`);
        try { fs.unlinkSync(fp); } catch {}
      }
    } catch (e) {
      const msg = e.message.substring(0, 150);
      logger.warn(`Strategy ${s+1} failed: ${msg}`);
    }
  }

  // Final fallback: try with python yt-dlp directly
  logger.info('Trying Python yt-dlp as final fallback...');
  try {
    const pyOut = execSync(
      `python3 -m yt_dlp -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M 2>&1`,
      { timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8' }
    );
    const files = fs.readdirSync(outputDir)
      .filter(f => f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mkv'))
      .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
    if (files.length > 0) {
      const fp = path.join(outputDir, files[0]);
      const sizeMB = fs.statSync(fp).size / 1024 / 1024;
      if (sizeMB > 1) {
        logger.success(`Downloaded via Python: ${files[0]} (${sizeMB.toFixed(1)}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
    }
  } catch (e) {
    logger.warn(`Python fallback failed: ${e.message.substring(0,100)}`);
  }

  logger.warn(`All download strategies failed for ${url.substring(0,60)}`);
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
