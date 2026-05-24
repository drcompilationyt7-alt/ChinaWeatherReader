/**
 * Downloader module
 * Uses yt-dlp with Node.js as JS runtime + correct flags to bypass bot detection.
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
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Build yt-dlp base command with Node.js as JS runtime
  // --js-runtimes node tells yt-dlp to use Node.js for JS extraction
  const ytBase = 'yt-dlp --js-runtimes node';

  // List of strategies to try
  const strategies = [];

  if (platform === 'youtube') {
    strategies.push(
      // Best strategy: Android client + Node.js runtime
      `${ytBase} --extractor-args "youtube:player_client=android" -f "best[height<=720]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`,
      // TV client (less restricted)
      `${ytBase} --extractor-args "youtube:player_client=tv" -f "best[height<=720]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`,
      // Web client with skip
      `${ytBase} --extractor-args "youtube:skip=webpage,js" -f "best[height<=720]" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`,
      // Just get the best format with js runtime
      `${ytBase} -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`,
    );
  } else if (platform === 'bilibili') {
    strategies.push(
      `${ytBase} --add-header "Referer:https://www.bilibili.com/" --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`,
      `${ytBase} -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`,
    );
  } else {
    strategies.push(
      `${ytBase} -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`,
    );
  }

  for (let s = 0; s < strategies.length; s++) {
    try {
      logger.info(`Strategy ${s+1}...`);
      const out = execSync(strategies[s], { timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8' });
      
      const files = fs.readdirSync(outputDir)
        .filter(f => f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mkv'))
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        const size = fs.statSync(fp).size;
        if (size > 500000) {
          logger.success(`Downloaded: ${files[0]} (${(size/1024/1024).toFixed(1)}MB)`);
          return { path: fp, title, platform, sourceUrl: url };
        }
        logger.warn(`Too small (${(size/1024).toFixed(0)}KB)`);
        try { fs.unlinkSync(fp); } catch {}
      } else if (out.includes('[download]') && out.includes('100%')) {
        // File may have been downloaded but with different extension check
        setTimeout(() => {}, 500); // give filesystem time
        const files2 = fs.readdirSync(outputDir).sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
        for (const f of files2.slice(0, 3)) {
          const fp = path.join(outputDir, f);
          try {
            const size = fs.statSync(fp).size;
            if (size > 500000 && (f.endsWith('.mp4') || f.endsWith('.webm'))) {
              logger.success(`Found: ${f} (${(size/1024/1024).toFixed(1)}MB)`);
              return { path: fp, title, platform, sourceUrl: url };
            }
          } catch {}
        }
      }
    } catch (e) {
      const err = (e.stderr || e.stdout || e.message || '').toString().substring(0, 100);
      logger.warn(`S${s+1}: ${err}`);
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
