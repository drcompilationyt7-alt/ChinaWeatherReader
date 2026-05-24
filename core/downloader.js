/**
 * Downloader module
 * Minimal approach: no custom extractor args, proper browser emulation.
 * Uses Shadowsocks proxy + cookies from env + Node.js runtime.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

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

  // Get proxy from Shadowsocks
  const proxy = process.env.YT_PROXY || null;
  const proxyArg = proxy ? `--proxy "${proxy}"` : '';

  // Write cookies.txt from env if set
  const cookieFile = path.join(outputDir, 'cookies.txt');
  if (process.env.YOUTUBE_COOKIES && process.env.YOUTUBE_COOKIES.length > 100) {
    try { fs.writeFileSync(cookieFile, process.env.YOUTUBE_COOKIES, 'utf8'); } catch {}
  }
  const cookieArg = fs.existsSync(cookieFile) && fs.statSync(cookieFile).size > 100 
    ? `--cookies "${cookieFile}"` : '';

  // Build env with Node.js in PATH
  const env = { ...process.env };
  try {
    const nodeDir = path.dirname(process.execPath);
    env.PATH = `${nodeDir}:${env.PATH || ''}`;
  } catch {}

  // Minimal command: just a proper browser UA, geo-bypass, and runtime
  const cmd = `python3 -m yt_dlp ${proxyArg} ${cookieArg} --js-runtimes node --user-agent "${UA}" --geo-bypass -f "best[height<=720]" --download-sections "*0-${MAX_DURATION}" --force-keyframes-at-cuts -o "${outputFile}" "${url}" --no-playlist --max-filesize 150M --socket-timeout 30 --retries 3`;

  try {
    logger.info(`Downloading...`);
    execSync(cmd, { timeout: 180000, maxBuffer: 100*1024*1024, encoding: 'utf8', env });

    const files = fs.readdirSync(outputDir)
      .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000)
      .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
    
    if (files.length > 0) {
      const fp = path.join(outputDir, files[0]);
      logger.success(`OK: ${files[0]} (${(fs.statSync(fp).size/1024/1024).toFixed(1)}MB)`);
      try { fs.unlinkSync(cookieFile); } catch {}
      return { path: fp, title, platform, sourceUrl: url };
    }
    
    throw new Error('No output file found');
  } catch (e) {
    const err = (e.stderr || e.stdout || e.message || '').toString();
    // Try without proxy as fallback
    if (proxy && !err.includes('HTTP Error 403')) {
      logger.warn(`With proxy failed, trying direct...`);
      const cmd2 = `python3 -m yt_dlp ${cookieArg} --js-runtimes node --user-agent "${UA}" --geo-bypass -f "best[height<=720]" --download-sections "*0-${MAX_DURATION}" -o "${outputFile}" "${url}" --no-playlist --max-filesize 150M --socket-timeout 15 --retries 1`;
      try {
        execSync(cmd2, { timeout: 120000, maxBuffer: 100*1024*1024, encoding: 'utf8', env });
        const files = fs.readdirSync(outputDir).filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000).sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
        if (files.length > 0) {
          const fp = path.join(outputDir, files[0]);
          logger.success(`OK (direct): ${files[0]}`);
          try { fs.unlinkSync(cookieFile); } catch {}
          return { path: fp, title, platform, sourceUrl: url };
        }
      } catch {}
    }
    
    const shortErr = err.substring(0, 150);
    if (shortErr.includes('Sign in')) {
      logger.warn('YouTube: sign-in required - set YOUTUBE_COOKIES secret');
    } else if (shortErr.includes('HTTP Error 403')) {
      logger.warn('YouTube: 403 - needs PO Token');
    } else {
      logger.warn(`Failed: ${shortErr}`);
    }
  }

  try { fs.unlinkSync(cookieFile); } catch {}
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
