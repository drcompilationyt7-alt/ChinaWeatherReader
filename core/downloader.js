/**
 * Downloader module
 * Uses proxy from YT_PROXY env var or scraped free proxies.
 * Limits downloads to 1 video max per run to save bandwidth.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');
const { getWorkingProxy } = require('./proxy-scraper');

const logger = new Logger('Downloader');
let cachedProxy = null;

async function getProxy() {
  // First check env var (user-set proxy, preferred)
  if (process.env.YT_PROXY) {
    const proxy = process.env.YT_PROXY.trim();
    if (proxy) {
      logger.info(`Using YT_PROXY env var (5s timeout per request)`);
      return proxy;
    }
  }
  // Fallback: try scraping free proxies (slow but works)
  if (!cachedProxy) {
    cachedProxy = await getWorkingProxy();
  }
  return cachedProxy;
}

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Try proxy first, then direct
  const proxy = await getProxy();
  
  // Build commands: [with proxy, without proxy]
  const cmds = [];
  
  // With proxy (if available)
  if (proxy) {
    const proxyArg = `--proxy "${proxy}"`;
    if (platform === 'bilibili') {
      cmds.push(`yt-dlp ${proxyArg} --add-header "Referer:https://www.bilibili.com/" --user-agent "Mozilla/5.0" -f "best[height<=480]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 50M --socket-timeout 10`);
    } else {
      cmds.push(`yt-dlp ${proxyArg} --extractor-args "youtube:player_client=android" -f "best[height<=480]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 50M --socket-timeout 10`);
    }
  }
  
  // Without proxy (sometimes works for certain sites)
  if (platform === 'bilibili') {
    cmds.push(`yt-dlp --add-header "Referer:https://www.bilibili.com/" -f "best[height<=480]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 50M`);
  } else if (platform !== 'youtube') {
    cmds.push(`yt-dlp -f "best[height<=480]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 50M`);
  }
  
  // Without proxy for YouTube (will likely fail, but try)
  if (platform === 'youtube') {
    cmds.push(`yt-dlp --extractor-args "youtube:player_client=android" -f "best[height<=480]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 50M`);
  }

  for (let i = 0; i < cmds.length; i++) {
    try {
      const isProxy = cmds[i].includes('--proxy');
      logger.info(`Cmd ${i+1}${isProxy ? ' (proxy)' : ' (direct)'}...`);
      execSync(cmds[i], { timeout: 120000, maxBuffer: 50*1024*1024, encoding: 'utf8' });

      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 100000)
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        const sizeMB = (fs.statSync(fp).size / 1024 / 1024).toFixed(1);
        logger.success(`OK: ${files[0]} (${sizeMB}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
    } catch (e) {
      const err = (e.stderr || e.stdout || e.message || '').toString().substring(0, 80);
      logger.warn(`Cmd ${i+1}: ${err}`);
    }
  }

  logger.warn(`All failed: ${url.substring(0,60)}`);
  return null;
}

async function downloadVideos(urls, outputDir) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const downloaded = [];
  // Only try first URL to save bandwidth
  if (urls.length > 0) {
    const result = await downloadVideo(urls[0], outputDir);
    if (result) downloaded.push(result);
  }
  logger.success(`Downloaded ${downloaded.length} video(s)`);
  return downloaded;
}

module.exports = { downloadVideo, downloadVideos };
