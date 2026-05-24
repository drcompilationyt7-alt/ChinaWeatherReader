/**
 * Downloader module
 * Uses Shadowsocks proxy + proper User-Agent + --geo-bypass.
 * Tries web_embedded -> safari -> android_vr clients (no PO Token).
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');
const { getWorkingProxy } = require('./proxy-scraper');

const logger = new Logger('Downloader');
const MAX_DURATION = 480;
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36';

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Get proxy
  const proxy = process.env.YT_PROXY || null;
  const proxyArg = proxy ? `--proxy "${proxy}"` : '';

  // Build env with Node.js PATH for JS runtime
  const env = { ...process.env };
  try {
    const nodeDir = path.dirname(process.execPath);
    env.PATH = `${nodeDir}:${env.PATH || ''}`;
  } catch {}

  // Only try 4 key strategies
  const base = `-f "best[height<=720]" --download-sections "*0-${MAX_DURATION}" --force-keyframes-at-cuts -o "${outputFile}" "${url}" --no-playlist --max-filesize 150M --socket-timeout 20 --retries 2 --no-part --geo-bypass --user-agent "${UA}"`;
  
  const cmds = [
    `python3 -m yt_dlp ${proxyArg} --js-runtimes node --extractor-args "youtube:player_client=web_embedded" ${base}`,
    `python3 -m yt_dlp ${proxyArg} --js-runtimes node --extractor-args "youtube:player_client=web_safari" ${base}`,
    `python3 -m yt_dlp ${proxyArg} --js-runtimes node --extractor-args "youtube:player_client=android_vr" ${base}`,
    `python3 -m yt_dlp ${proxyArg} --js-runtimes node ${base}`,
  ];

  for (let i = 0; i < cmds.length; i++) {
    try {
      const client = cmds[i].match(/player_client=(\w+)/)?.[1] || 'default';
      const hasProxy = cmds[i].includes('--proxy');
      logger.info(`Try ${i+1}: ${client}${hasProxy ? ' (proxy)' : ' (direct)'}`);
      
      const stdout = execSync(cmds[i], { timeout: 120000, maxBuffer: 100*1024*1024, encoding: 'utf8', env });

      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000)
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        logger.success(`OK: ${files[0]} (${(fs.statSync(fp).size/1024/1024).toFixed(1)}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
    } catch (e) {
      const err = (e.stderr || e.stdout || e.message || '').toString();
      if (err.includes('HTTP Error 403')) {
        logger.warn(`Try ${i+1}: 403 (needs PO Token)`);
      } else if (err.includes('Sign in')) {
        logger.warn(`Try ${i+1}: blocked`);
      } else {
        logger.warn(`Try ${i+1}: ${err.substring(0,60)}`);
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
