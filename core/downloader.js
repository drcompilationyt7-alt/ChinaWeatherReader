/**
 * Downloader module
 * Uses YT_PROXY (set by Shadowsocks) or scraped proxies.
 * Limits downloads to first 8 minutes to save bandwidth.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');
const { getWorkingProxy } = require('./proxy-scraper');

const logger = new Logger('Downloader');

// Download only first 8 minutes to save bandwidth
const MAX_DURATION = 480; // 8 minutes in seconds

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Get proxy - YT_PROXY first (set by Shadowsocks), then scraped
  let proxy = process.env.YT_PROXY || null;
  if (!proxy) {
    try { proxy = await getWorkingProxy(); } catch {}
  }

  // Build commands
  const cmds = [];
  
  // Common args: limit to 720p, 8min max, 150MB max
  const baseArgs = `-f "best[height<=720]" --download-sections "*0-${MAX_DURATION}" --force-keyframes-at-cuts`;
  const fileArgs = `-o "${outputFile}" "${url}" --no-playlist --max-filesize 150M --socket-timeout 20 --retries 2 --no-part`;
  
  if (proxy) {
    const proxyArg = `--proxy "${proxy}"`;
    if (platform === 'youtube') {
      cmds.push(`python3 -m yt_dlp ${proxyArg} --js-runtimes node --extractor-args "youtube:player_client=web_safari" ${baseArgs} ${fileArgs}`);
      cmds.push(`python3 -m yt_dlp ${proxyArg} --js-runtimes node ${baseArgs} ${fileArgs}`);
    } else {
      cmds.push(`python3 -m yt_dlp ${proxyArg} --add-header "Referer:https://www.bilibili.com/" ${baseArgs} ${fileArgs}`);
    }
  }
  
  // Direct as last resort (won't work for YouTube)
  if (platform !== 'bilibili') {
    cmds.push(`python3 -m yt_dlp ${baseArgs} ${fileArgs}`);
  }

  for (let i = 0; i < cmds.length; i++) {
    try {
      const hasProxy = cmds[i].includes('--proxy');
      logger.info(`Try ${i+1}/${cmds.length}${hasProxy ? ' (proxy)' : ' (direct)'}`);
      
      execSync(cmds[i], { timeout: 180000, maxBuffer: 100*1024*1024, encoding: 'utf8' });
      
      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000)
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        const sizeMB = (fs.statSync(fp).size/1024/1024).toFixed(1);
        logger.success(`OK: ${files[0]} (${sizeMB}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
    } catch (e) {
      const err = (e.stderr || e.stdout || e.message || '').toString().substring(0, 80);
      if (!err.includes('Sign in') && !err.includes('bot') && !err.includes('412')) {
        logger.warn(`Try ${i+1}: ${err}`);
      }
    }
  }

  logger.warn(`Failed: ${url.substring(0,60)}`);
  return null;
}

async function downloadVideos(urls, outputDir) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const downloaded = [];
  for (let i = 0; i < Math.min(urls.length, 3); i++) {
    logger.info(`--- Video ${i+1}/3 ---`);
    const result = await downloadVideo(urls[i], outputDir);
    if (result) downloaded.push(result);
  }
  logger.success(`Downloaded ${downloaded.length}/3`);
  return downloaded;
}

module.exports = { downloadVideo, downloadVideos };
