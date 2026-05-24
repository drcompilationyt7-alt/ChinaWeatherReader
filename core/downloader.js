/**
 * Downloader module
 * Uses yt-dlp with proper clients to avoid PO Token requirement:
 * - web_safari: Provides HLS formats (no PO Token needed)
 * - tv: No PO Token required
 * Uses deno for EJS challenge solving + Proxifly tested proxies.
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

  // Get proxy candidates (env var first, then scraped)
  const proxies = [];
  if (process.env.YT_PROXY && process.env.YT_PROXY.trim()) {
    proxies.push(process.env.YT_PROXY.trim());
  }
  for (let i = 0; i < 3; i++) {
    try {
      const p = await getWorkingProxy();
      if (p) proxies.push(p);
    } catch {}
  }
  proxies.push(null); // direct connection fallback

  // Build command templates per platform
  const commands = [];
  
  if (platform === 'youtube') {
    for (const proxy of proxies) {
      const proxyArg = proxy ? `--proxy "${proxy}"` : '';
      
      // web_safari client: provides HLS formats (no PO Token needed)
      commands.push(`python3 -m yt_dlp ${proxyArg} --js-runtimes deno --extractor-args "youtube:player_client=web_safari" -f "best[height<=720]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M --socket-timeout 15 --retries 2`);
      
      // tv client: no PO Token required
      commands.push(`python3 -m yt_dlp ${proxyArg} --js-runtimes deno --extractor-args "youtube:player_client=tv" -f "best[height<=720]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M --socket-timeout 15 --retries 2`);
      
      // mweb client with PO Token fallback
      commands.push(`python3 -m yt_dlp ${proxyArg} --js-runtimes deno --extractor-args "youtube:player_client=mweb" -f "best[height<=720]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M --socket-timeout 15 --retries 2`);
      
      // No format filter (yt-dlp auto-selects)
      commands.push(`python3 -m yt_dlp ${proxyArg} --js-runtimes deno -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M --socket-timeout 15 --retries 2`);
    }
  } else {
    // Bilibili/other
    for (const proxy of proxies) {
      const proxyArg = proxy ? `--proxy "${proxy}"` : '';
      commands.push(`python3 -m yt_dlp ${proxyArg} --add-header "Referer:https://www.bilibili.com/" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M --socket-timeout 15 --retries 2`);
      commands.push(`python3 -m yt_dlp ${proxyArg} -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M --socket-timeout 15 --retries 2`);
    }
  }

  for (let i = 0; i < commands.length; i++) {
    try {
      const hasProxy = commands[i].includes('--proxy');
      const clientMatch = commands[i].match(/player_client=(\w+)/);
      const client = clientMatch ? clientMatch[1] : 'default';
      logger.info(`Try ${i+1}: ${client}${hasProxy ? ' (proxy)' : ' (direct)'}`);
      
      execSync(commands[i], { timeout: 120000, maxBuffer: 50*1024*1024, encoding: 'utf8' });
      
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
      if (err.includes('Sign in') || err.includes('bot')) {
        logger.warn(`Try ${i+1}: bot blocked (expected without proxy)`);
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
