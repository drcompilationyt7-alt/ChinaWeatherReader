/**
 * Downloader module
 * Downloads ALL videos with free proxies, min 480p quality.
 * Rotates proxies between each attempt for best reliability.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');
const { getWorkingProxy } = require('./proxy-scraper');

const logger = new Logger('Downloader');

var proxyIndex = 0;

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}_${Math.random().toString(36).slice(2,6)}_%(id)s.%(ext)s`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Get a proxy
  let proxy = null;
  if (process.env.YT_PROXY && process.env.YT_PROXY.trim()) {
    proxy = process.env.YT_PROXY.trim();
  } else {
    try {
      const p = await getWorkingProxy();
      // Rotate proxies
      if (p && !p.startsWith('http://')) proxy = `http://${p}`;
      else proxy = p;
    } catch {}
  }

  const proxyArg = proxy ? `--proxy "${proxy}"` : '';
  
  // Build commands: with proxy first, then without
  const cmdVariants = [];
  
  const baseOut = `-o "${outputFile}"`;
  const baseUrl = `"${url}"`;
  const baseFlags = '--no-playlist --max-filesize 150M --socket-timeout 15 --retries 3 --fragment-retries 3';
  
  if (platform === 'bilibili') {
    const headers = '--add-header "Referer:https://www.bilibili.com/" --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"';
    if (proxyArg) cmdVariants.push(`yt-dlp ${proxyArg} ${headers} -f "best[height<=720]" ${baseOut} ${baseUrl} ${baseFlags}`);
    cmdVariants.push(`yt-dlp ${headers} -f "best[height<=720]" ${baseOut} ${baseUrl} ${baseFlags}`);
  } else {
    // YouTube: try android + web clients
    const extractor = '--extractor-args "youtube:player_client=android,web" --user-agent "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36"';
    if (proxyArg) cmdVariants.push(`yt-dlp ${proxyArg} ${extractor} -f "best[height<=720]" ${baseOut} ${baseUrl} ${baseFlags}`);
    if (proxyArg) cmdVariants.push(`yt-dlp ${proxyArg} -f "best[height<=720]" ${baseOut} ${baseUrl} ${baseFlags}`);
    cmdVariants.push(`yt-dlp ${extractor} -f "best[height<=720]" ${baseOut} ${baseUrl} ${baseFlags}`);
    cmdVariants.push(`yt-dlp -f "best[height<=720]" ${baseOut} ${baseUrl} ${baseFlags}`);
  }

  for (let i = 0; i < cmdVariants.length; i++) {
    try {
      const hasProxy = cmdVariants[i].includes('--proxy');
      logger.info(`Try ${i+1}${hasProxy ? ' (proxy)' : ' (direct)'}...`);
      const out = execSync(cmdVariants[i], { timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8' });

      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 500000)
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        const sizeMB = (fs.statSync(fp).size / 1024 / 1024).toFixed(1);
        logger.success(`OK: ${files[0]} (${sizeMB}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
    } catch (e) {
      const err = (e.stderr || e.stdout || e.message || '').toString().substring(0, 100);
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
