/**
 * Downloader module
 * web_embedded client works! (no bot block, only age-restricted videos fail)
 * Added --age-limit 99 + YOUTUBE_COOKIES fallback for age-restricted.
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

  // Build env with Node.js in PATH
  const env = { ...process.env };
  try { env.PATH = `${path.dirname(process.execPath)}:${env.PATH || ''}`; } catch {}

  // Write cookies from env if available
  const cookieFile = path.join(outputDir, 'cookies.txt');
  if (process.env.YOUTUBE_COOKIES && process.env.YOUTUBE_COOKIES.length > 100) {
    try { fs.writeFileSync(cookieFile, process.env.YOUTUBE_COOKIES); } catch {}
  }

  const PY = 'python3 -m yt_dlp';
  const fmt = '-f "best[height<=720]"';
  const base = `--download-sections "*0-${MAX_DURATION}" -o "${outputFile}" "${url}" --no-playlist --max-filesize 150M --socket-timeout 20 --retries 2 --user-agent "${UA}" --js-runtimes node --age-limit 99`;
  
  const cmds = [];
  
  // With cookies (best - handles everything)
  if (fs.existsSync(cookieFile) && fs.statSync(cookieFile).size > 100) {
    cmds.push(`${PY} --cookies "${cookieFile}" ${fmt} ${base}`);
  }
  
  // web_embedded: WORKS for non-age-restricted videos! No bot block.
  cmds.push(`${PY} --extractor-args "youtube:player_client=web_embedded" ${fmt} ${base}`);
  cmds.push(`${PY} --extractor-args "youtube:player_client=android_vr" ${fmt} ${base}`);
  cmds.push(`${PY} ${fmt} ${base}`);

  for (let i = 0; i < cmds.length; i++) {
    try {
      const client = cmds[i].match(/player_client=(\w+)/)?.[1] || cmds[i].includes('--cookies') ? 'cookies' : 'default';
      logger.info(`Try ${i+1}: ${client}`);
      
      execSync(cmds[i], { timeout: 120000, maxBuffer: 200*1024*1024, encoding: 'utf8', env });

      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000)
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        logger.success(`OK! ${files[0]} (${(fs.statSync(fp).size/1024/1024).toFixed(1)}MB)`);
        try { fs.unlinkSync(cookieFile); } catch {}
        return { path: fp, title, platform, sourceUrl: url };
      }
    } catch (e) {
      const err = (e.stderr || e.stdout || e.message || '').toString().substring(0, 100);
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
    const r = await downloadVideo(urls[i], outputDir);
    if (r) downloaded.push(r);
  }
  logger.success(`Downloaded ${downloaded.length}/3`);
  return downloaded;
}

module.exports = { downloadVideo, downloadVideos };
