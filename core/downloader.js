/**
 * Downloader module - debug mode - shows all errors
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

  // Build env with Node.js and deno in PATH
  const env = { ...process.env };
  try {
    const nodeDir = path.dirname(process.execPath);
    env.PATH = `${nodeDir}:${env.PATH || ''}`;
  } catch {}
  // Also try to find deno
  for (const d of ['/home/runner/.deno/bin', '/root/.deno/bin', '/usr/local/bin']) {
    if (fs.existsSync(d)) env.PATH = `${d}:${env.PATH || ''}`;
  }

  const PY = 'python3 -m yt_dlp';
  const fmt = '-f "best[height<=720]"';
  const base = `--download-sections "*0-${MAX_DURATION}" -o "${outputFile}" "${url}" --no-playlist --max-filesize 150M --socket-timeout 20 --retries 2 --user-agent "${UA}" --js-runtimes node`;

  const strategies = [
    { name: 'web_embedded', args: '--extractor-args "youtube:player_client=web_embedded"' },
    { name: 'android_vr', args: '--extractor-args "youtube:player_client=android_vr"' },
    { name: 'tv', args: '--extractor-args "youtube:player_client=tv"' },
    { name: 'default', args: '' },
  ];

  for (const s of strategies) {
    try {
      logger.info(`Try: ${s.name}`);
      const stdout = execSync(`${PY} ${s.args} ${fmt} ${base}`, { timeout: 120000, maxBuffer: 200*1024*1024, encoding: 'utf8', env });
      
      // Find any downloaded file
      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000)
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        const mb = (fs.statSync(fp).size/1024/1024).toFixed(1);
        logger.success(`OK! ${files[0]} (${mb}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
      logger.warn(`No file. stdout: ${(stdout||'').substring(0,200)}`);
    } catch (e) {
      // Show ALL errors for debugging
      const err = (e.stderr || e.stdout || e.message || '').toString();
      logger.warn(`${s.name} error: ${err.substring(0, 200)}`);
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
    const r = await downloadVideo(urls[i], outputDir);
    if (r) downloaded.push(r);
  }
  logger.success(`Downloaded ${downloaded.length}/3`);
  return downloaded;
}

module.exports = { downloadVideo, downloadVideos };
