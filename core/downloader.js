/**
 * Downloader module
 * Uses yt-dlp with rotating proxies from Proxifly (tested proxies).
 * Tries multiple proxies per video.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');
const { getWorkingProxy } = require('./proxy-scraper');

const logger = new Logger('Downloader');

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Gather multiple proxy candidates
  const proxies = [];
  
  // Env proxy first (user's paid proxy, preferred)
  if (process.env.YT_PROXY && process.env.YT_PROXY.trim()) {
    proxies.push(process.env.YT_PROXY.trim());
  }
  
  // Scrape free proxies (Proxifly returns tested ones)
  for (let i = 0; i < 3; i++) {
    try {
      const proxy = await getWorkingProxy();
      if (proxy && !proxies.includes(proxy)) proxies.push(proxy);
    } catch {}
  }

  // Try each proxy + direct connection
  const cmdTemplates = [];
  
  // With each proxy
  for (const proxy of proxies) {
    if (platform === 'youtube') {
      cmdTemplates.push(`yt-dlp --proxy "${proxy}" --extractor-args "youtube:player_client=android" -f "best[height<=480]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M --socket-timeout 10 --retries 1`);
      cmdTemplates.push(`yt-dlp --proxy "${proxy}" -f "best[height<=480]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M --socket-timeout 10 --retries 1`);
    } else {
      cmdTemplates.push(`yt-dlp --proxy "${proxy}" --add-header "Referer:https://www.bilibili.com/" -f "best" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M --socket-timeout 10 --retries 1`);
    }
  }
  
  // Direct connection (will fail for YouTube but try for others)
  if (platform !== 'youtube') {
    cmdTemplates.push(`yt-dlp -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M --socket-timeout 5`);
  }

  for (let i = 0; i < cmdTemplates.length; i++) {
    try {
      const hasProxy = cmdTemplates[i].includes('--proxy');
      logger.info(`Try ${i+1}/${cmdTemplates.length}${hasProxy ? ' (proxy)' : ' (direct)'}...`);
      
      const out = execSync(cmdTemplates[i], { timeout: 60000, maxBuffer: 50*1024*1024, encoding: 'utf8' });
      
      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 100000)
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        logger.success(`OK: ${files[0]} (${(fs.statSync(fp).size/1024/1024).toFixed(1)}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
    } catch (e) {
      const err = (e.stderr || e.stdout || e.message || '').toString().substring(0, 80);
      logger.warn(`Try ${i+1}: ${err}`);
    }
  }

  logger.warn(`Failed: ${url.substring(0,60)}`);
  return null;
}

async function downloadVideos(urls, outputDir) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const downloaded = [];
  for (let i = 0; i < Math.min(urls.length, 5); i++) {
    logger.info(`--- Video ${i+1}/${Math.min(urls.length,5)} ---`);
    const result = await downloadVideo(urls[i], outputDir);
    if (result) downloaded.push(result);
  }
  logger.success(`Downloaded ${downloaded.length}/${Math.min(urls.length,5)}`);
  return downloaded;
}

module.exports = { downloadVideo, downloadVideos };
