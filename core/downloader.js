/**
 * Downloader module - 1080p quality + rotating proxies
 */
const { execSync, spawnSync } = require('child_process');
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

  // Gather proxy candidates
  const proxies = [];
  // 1. User's paid proxy (preferred)
  if (process.env.YT_PROXY && process.env.YT_PROXY.trim()) {
    proxies.push(process.env.YT_PROXY.trim());
    logger.info('Using YT_PROXY from env (paid proxy)');
  }
  // 2. Scraped free proxies - get 5
  for (let i = 0; i < 5; i++) {
    try {
      const p = await getWorkingProxy();
      if (p && !proxies.includes(p)) proxies.push(p);
    } catch {}
  }

  // Direct connection as fallback
  proxies.push(null);

  // Build commands
  const cmds = [];
  
  if (platform === 'youtube') {
    for (const proxy of proxies) {
      const proxyArg = proxy ? `--proxy "${proxy}"` : '';
      // 1080p: best[height<=1080]
      cmds.push(`python3 -m yt_dlp ${proxyArg} --js-runtimes node --extractor-args "youtube:player_client=web_safari" -f "best[height<=1080]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 200M --socket-timeout 20 --retries 2`);
      cmds.push(`python3 -m yt_dlp ${proxyArg} --js-runtimes node --extractor-args "youtube:player_client=tv" -f "best[height<=1080]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 200M --socket-timeout 20 --retries 2`);
      cmds.push(`python3 -m yt_dlp ${proxyArg} --js-runtimes node -f "best[height<=1080]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 200M --socket-timeout 20 --retries 2`);
    }
  } else {
    for (const proxy of proxies) {
      const proxyArg = proxy ? `--proxy "${proxy}"` : '';
      cmds.push(`python3 -m yt_dlp ${proxyArg} --add-header "Referer:https://www.bilibili.com/" -f "best[height<=1080]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 200M --socket-timeout 20 --retries 2`);
      cmds.push(`python3 -m yt_dlp ${proxyArg} -f "best[height<=1080]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 200M --socket-timeout 20 --retries 2`);
    }
  }

  for (let i = 0; i < cmds.length; i++) {
    try {
      const hasProxy = cmds[i].includes('--proxy');
      const client = cmds[i].match(/player_client=(\w+)/)?.[1] || 'default';
      logger.info(`Try ${i+1}/${cmds.length} ${client}${hasProxy ? ' (proxy)' : ' (direct)'}`);
      
      execSync(cmds[i], { timeout: 180000, maxBuffer: 100*1024*1024, encoding: 'utf8' });
      
      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 100000)
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        const sizeMB = (fs.statSync(fp).size/1024/1024).toFixed(1);
        logger.success(`OK: ${files[0]} (${sizeMB}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
    } catch (e) {
      const err = (e.stderr || e.stdout || e.message || '').toString().substring(0, 80);
      if (err.includes('Sign in') || err.includes('bot')) {
        // Skip known errors without cluttering logs
      } else {
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
  for (let i = 0; i < Math.min(urls.length, 5); i++) {
    logger.info(`--- Video ${i+1}/${Math.min(urls.length,5)} ---`);
    const result = await downloadVideo(urls[i], outputDir);
    if (result) downloaded.push(result);
  }
  logger.success(`Downloaded ${downloaded.length}/${Math.min(urls.length,5)}`);
  return downloaded;
}

module.exports = { downloadVideo, downloadVideos };
