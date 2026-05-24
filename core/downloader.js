/**
 * Downloader module
 * Uses Python yt-dlp via subprocess for more reliable handling
 * Implements platform-specific download strategies to bypass bot detection
 */
const { spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('Downloader');

/**
 * Download a single video with yt-dlp
 * Returns { path, title, platform, sourceUrl } or null
 */
async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  const outputTemplate = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
  
  logger.info(`Downloading ${platform}: ${url.substring(0, 80)}`);

  // Strategy 1: Try with platform-specific args
  let strategies = [];

  if (platform === 'youtube') {
    // YouTube: try multiple player clients
    strategies = [
      // Android - best for CI
      `python3 -m yt_dlp --extractor-args "youtube:player_client=android" --user-agent "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M`,
      // Web with mobile UA
      `python3 -m yt_dlp --extractor-args "youtube:skip=webpage" --user-agent "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M`,
      // TV client 
      `python3 -m yt_dlp --extractor-args "youtube:player_client=android_tv" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M`,
    ];
  } else if (platform === 'bilibili') {
    // Bilibili: need proper CF headers
    strategies = [
      // With proper user-agent and accept headers
      `python3 -m yt_dlp --add-header "Referer:https://www.bilibili.com/" --add-header "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36" --add-header "Origin:https://www.bilibili.com" -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M`,
      // Without headers
      `python3 -m yt_dlp -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M 2>&1`,
    ];
  } else {
    strategies = [
      `python3 -m yt_dlp -o "${outputTemplate}" "${url}" --no-playlist --max-filesize 100M`,
    ];
  }

  for (let s = 0; s < strategies.length; s++) {
    try {
      const r = spawnSync('bash', ['-c', strategies[s]], { 
        timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8',
        env: { ...process.env, HOME: process.env.HOME || '/root' }
      });
      
      const stderr = (r.stderr || '').trim();
      const stdout = (r.stdout || '').trim();
      
      // Check for downloaded file
      const files = fs.readdirSync(outputDir)
        .filter(f => f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mkv'))
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        const sizeMB = fs.statSync(fp).size / 1024 / 1024;
        if (sizeMB > 1) {
          logger.success(`Downloaded: ${files[0]} (${sizeMB.toFixed(1)}MB) via strategy ${s+1}`);
          return { path: fp, title, platform, sourceUrl: url };
        } else {
          logger.warn(`File too small (${sizeMB.toFixed(1)}MB), trying next strategy`);
          try { fs.unlinkSync(fp); } catch {}
        }
      } else {
        // Check the output for actual errors
        if (r.status !== 0) {
          const errMsg = (stderr || stdout).substring(0, 300);
          logger.warn(`Strategy ${s+1} failed: ${errMsg}`);
        } else {
          logger.warn(`Strategy ${s+1} returned 0 but no file found`);
        }
      }
    } catch (e) {
      logger.warn(`Strategy ${s+1} error: ${e.message.substring(0,100)}`);
    }
  }
  
  logger.warn(`All strategies failed for ${url.substring(0, 60)}`);
  return null;
}

/**
 * Download multiple videos
 */
async function downloadVideos(urls, outputDir) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const downloaded = [];
  
  for (let i = 0; i < Math.min(urls.length, 5); i++) {
    const result = await downloadVideo(urls[i], outputDir);
    if (result) downloaded.push(result);
  }
  
  logger.success(`Downloaded ${downloaded.length}/${Math.min(urls.length, 5)} videos`);
  return downloaded;
}

module.exports = { downloadVideo, downloadVideos };
