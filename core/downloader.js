/**
 * Downloader module
 * Uses Node.js JS runtime + web_embedded client (no PO Token needed).
 * Falls back to tv, safari clients. Uses Shadowsocks proxy.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');
const { getWorkingProxy } = require('./proxy-scraper');

const logger = new Logger('Downloader');

const MAX_DURATION = 480;

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Gather proxies
  const proxies = [];
  if (process.env.YT_PROXY && process.env.YT_PROXY.trim()) {
    proxies.push(process.env.YT_PROXY.trim());
    logger.info('Using Shadowsocks proxy');
  }
  for (let i = 0; i < 3; i++) {
    try { const p = await getWorkingProxy(); if (p && !proxies.includes(p)) proxies.push(p); } catch {}
  }
  proxies.push(null); // direct fallback

  // Build env with node in PATH for JS runtime
  const env = { ...process.env };
  try {
    const nodeDir = path.dirname(process.execPath);
    env.PATH = `${nodeDir}:${env.PATH || ''}`;
  } catch {}

  // Build commands
  const cmds = [];
  const base = `-f "best[height<=720]" --download-sections "*0-${MAX_DURATION}" --force-keyframes-at-cuts -o "${outputFile}" "${url}" --no-playlist --max-filesize 150M --socket-timeout 20 --retries 2 --no-part`;
  
  for (const proxy of proxies) {
    const p = proxy ? `--proxy "${proxy}"` : '';
    
    // Per yt-dlp docs: web_embedded = NO PO Token needed!
    // web_music = Only GVS PO Token needed (might work)
    cmds.push(`python3 -m yt_dlp ${p} --js-runtimes node --extractor-args "youtube:player_client=web_embedded" ${base}`);
    cmds.push(`python3 -m yt_dlp ${p} --js-runtimes node --extractor-args "youtube:player_client=web_safari" ${base}`);
    cmds.push(`python3 -m yt_dlp ${p} --js-runtimes node --extractor-args "youtube:player_client=tv" ${base}`);
    cmds.push(`python3 -m yt_dlp ${p} --js-runtimes node ${base}`);
  }

  for (let i = 0; i < cmds.length; i++) {
    try {
      const hasProxy = cmds[i].includes('--proxy');
      const client = cmds[i].match(/player_client=(\w+)/)?.[1] || 'default';
      logger.info(`Try ${i+1}/${cmds.length} ${client}${hasProxy ? ' (proxy)' : ' (direct)'}`);
      
      execSync(cmds[i], { timeout: 120000, maxBuffer: 100*1024*1024, encoding: 'utf8', env, cwd: outputDir });
      
      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000)
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        const sizeMB = (fs.statSync(fp).size/1024/1024).toFixed(1);
        const titleMatch = cmds[i].match(/--output "([^"]+)"/);
        logger.success(`OK: ${files[0]} (${sizeMB}MB) via ${client}`);
        return { path: fp, title, platform, sourceUrl: url };
      }
    } catch (e) {
      const err = (e.stderr || e.stdout || e.message || '').toString().substring(0, 80);
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
