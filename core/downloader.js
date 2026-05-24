/**
 * Downloader module
 * Uses python3 yt-dlp (newer version) with Node.js as JS runtime.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('Downloader');

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
  const outputFinal = path.join(outputDir, `vid_final_${Date.now()}.mp4`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Use python3 yt-dlp (v2026.3.17, has --js-runtimes support)
  const PY = 'python3 -m yt_dlp';
  
  const attempts = [];

  if (platform === 'youtube') {
    attempts.push(
      // Android client with Node.js runtime (latest yt-dlp)
      `${PY} --js-runtimes node --extractor-args "youtube:player_client=android" -f "best[height<=720]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`,
      `${PY} --js-runtimes node --extractor-args "youtube:player_client=tv" -f "best[height<=720]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`,
      `${PY} --js-runtimes node -f "best[height<=720]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`,
      `${PY} -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`,
    );
  } else if (platform === 'bilibili') {
    attempts.push(
      `${PY} --add-header "Referer:https://www.bilibili.com/" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`,
      `${PY} -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`,
    );
  } else {
    attempts.push(
      `${PY} -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`,
    );
  }

  // Try each
  for (let a = 0; a < attempts.length; a++) {
    try {
      logger.info(`Attempt ${a+1}...`);
      const stdout = execSync(attempts[a], { 
        timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8',
        env: { ...process.env, PATH: process.env.PATH || '' }
      });

      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mkv')) && fs.statSync(path.join(outputDir, f)).size > 500000)
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);

      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        logger.success(`OK: ${files[0]} (${(fs.statSync(fp).size/1024/1024).toFixed(1)}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
      
      // Check stdout for filename
      if (stdout.includes('[download]') && stdout.includes('100%')) {
        // Wait a moment and check again
        const files2 = fs.readdirSync(outputDir).filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 100000).sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
        if (files2.length > 0) {
          logger.success(`Found: ${files2[0]} (${(fs.statSync(path.join(outputDir, files2[0])).size/1024/1024).toFixed(1)}MB)`);
          return { path: path.join(outputDir, files2[0]), title, platform, sourceUrl: url };
        }
      }
    } catch (e) {
      const err = (e.stderr || e.stdout || e.message || '').toString().substring(0, 120);
      logger.warn(`A${a+1}: ${err}`);
    }
  }

  logger.warn(`All failed: ${url.substring(0,60)}`);
  return null;
}

async function downloadVideos(urls, outputDir) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const downloaded = [];
  for (let i = 0; i < Math.min(urls.length, 5); i++) {
    const result = await downloadVideo(urls[i], outputDir);
    if (result) downloaded.push(result);
  }
  logger.success(`Downloaded ${downloaded.length}/${Math.min(urls.length,5)}`);
  return downloaded;
}

module.exports = { downloadVideo, downloadVideos };
