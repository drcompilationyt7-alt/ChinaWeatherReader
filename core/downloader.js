/**
 * Downloader module
 * web_embedded FIRST (bypasses bot detection but fails age check)
 * android_vr second (no PO Token needed)
 * tv third (no PO Token)
 * Default last
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('Downloader');
const MAX_DURATION = 480;
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36';

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Build env with Node.js in PATH
  const env = { ...process.env };
  try { env.PATH = `${path.dirname(process.execPath)}:${env.PATH || ''}`; } catch {}

  const PY = 'python3 -m yt_dlp';
  const fmt = '-f "best[height<=720]"';
  const base = `--download-sections "*0-${MAX_DURATION}" -o "${outputFile}" "${url}" --no-playlist --max-filesize 150M --socket-timeout 25 --retries 2 --user-agent "${UA}" --js-runtimes node --age-limit 99`;

  // Strategy: web_embedded first (passes bot detection), then fallbacks
  const strategies = [
    { name: 'web_embedded', args: '--extractor-args "youtube:player_client=web_embedded"' },
    { name: 'android_vr', args: '--extractor-args "youtube:player_client=android_vr"' },
    { name: 'tv', args: '--extractor-args "youtube:player_client=tv"' },
    { name: 'default', args: '' },
  ];

  for (const s of strategies) {
    try {
      logger.info(`Try: ${s.name}`);
      execSync(`${PY} ${s.args} ${fmt} ${base}`, { timeout: 120000, maxBuffer: 200*1024*1024, encoding: 'utf8', env });

      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000)
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        logger.success(`OK! ${files[0]} (${(fs.statSync(fp).size/1024/1024).toFixed(1)}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
    } catch (e) {
      const err = (e.stderr || e.stdout || e.message || '').toString();
      if (err.includes('Sign in to confirm your age')) {
        logger.warn(`Age-restricted`);
      } else if (err.includes('Sign in') || err.includes('bot')) {
        // Bot block - expected for some clients
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
    const r = await downloadVideo(urls[i], outputDir);
    if (r) downloaded.push(r);
  }
  logger.success(`Downloaded ${downloaded.length}/3`);
  return downloaded;
}

module.exports = { downloadVideo, downloadVideos };
