/**
 * Downloader module
 * Uses free proxies scraped from public lists to bypass CI IP blocks.
 * Routes yt-dlp through HTTP proxies for YouTube, Bilibili, etc.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');
const { getWorkingProxy } = require('./proxy-scraper');

const logger = new Logger('Downloader');

let cachedProxy = null;

async function getProxy() {
  if (cachedProxy) return cachedProxy;
  cachedProxy = await getWorkingProxy();
  return cachedProxy;
}

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Get a working proxy
  const proxy = await getProxy();
  const proxyArg = proxy ? `--proxy "${proxy}"` : '';

  // Try yt-dlp with proxy first, then without
  const cmds = [];

  if (platform === 'youtube') {
    if (proxyArg) {
      cmds.push(`yt-dlp ${proxyArg} --js-runtimes node --extractor-args "youtube:player_client=android" -f "best[height<=720]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`);
      cmds.push(`yt-dlp ${proxyArg} -f "best[height<=720]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`);
    }
    cmds.push(`yt-dlp --js-runtimes node --extractor-args "youtube:player_client=android" -f "best[height<=720]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`);
    cmds.push(`yt-dlp -f "best[height<=720]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`);
  } else {
    if (proxyArg) {
      cmds.push(`yt-dlp ${proxyArg} --add-header "Referer:https://www.bilibili.com/" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`);
      cmds.push(`yt-dlp ${proxyArg} -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`);
    }
    cmds.push(`yt-dlp -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`);
  }

  for (let i = 0; i < cmds.length; i++) {
    try {
      logger.info(`Cmd ${i+1}${i === 0 && proxyArg ? ' (with proxy)' : ''}...`);
      execSync(cmds[i], { timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8' });

      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mkv')) && fs.statSync(path.join(outputDir, f)).size > 500000)
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        logger.success(`OK: ${files[0]} (${(fs.statSync(fp).size/1024/1024).toFixed(1)}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
    } catch (e) {
      const err = (e.stderr || e.stdout || e.message || '').toString().substring(0, 100);
      logger.warn(`Cmd ${i+1}: ${err}`);
    }
  }

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
