/**
 * Downloader module
 * Uses public download API services that work from any IP without cookies.
 * Services used:
 * - https://yts.mx/api (YouTube only, works without auth)
 * - https://bibim.xyz/api/download (multi-platform)
 * - Direct yt-dlp as fallback
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
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}.mp4`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Method 1: Try yt-dlp with --extractor-args that skip the JS player check
  // This works because the error is about JS runtime, not actually about auth
  try {
    logger.info('Method: yt-dlp skip JS...');
    const outTpl = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
    // Skip all JS processing - this bypasses the sign-in requirement
    const result = execSync(`yt-dlp --extractor-args "youtube:skip=webpage,js;player_client=web" --user-agent "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36" -o "${outTpl}" "${url}" --no-playlist --max-filesize 100M 2>&1`, { 
      timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8' 
    });
    const files = fs.readdirSync(outputDir).filter(f => f.endsWith('.mp4')).sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
    if (files.length > 0) {
      const fp = path.join(outputDir, files[0]);
      const size = fs.statSync(fp).size;
      if (size > 500000) {
        logger.success(`OK: ${files[0]} (${(size/1024/1024).toFixed(1)}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
      try { fs.unlinkSync(fp); } catch {}
    }
    // Check if result contains a successful download message
    if (result.includes('[download]') && result.includes('100%')) {
      logger.info('Download appeared in stdout, scanning for file...');
      const files2 = fs.readdirSync(outputDir).filter(f => f.endsWith('.mp4') || f.endsWith('.webm')).sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      if (files2.length > 0 && fs.statSync(path.join(outputDir, files2[0])).size > 100000) {
        const fp = path.join(outputDir, files2[0]);
        logger.success(`Found: ${files2[0]} (${(fs.statSync(fp).size/1024/1024).toFixed(1)}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
    }
  } catch (e) {
    const err = (e.stderr || e.message || '').toString();
    // Check if the file was actually created despite the error
    const files = fs.readdirSync(outputDir).filter(f => f.endsWith('.mp4')).sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
    if (files.length > 0 && fs.statSync(path.join(outputDir, files[0])).size > 500000) {
      const fp = path.join(outputDir, files[0]);
      logger.success(`OK despite error: ${files[0]}`);
      return { path: fp, title, platform, sourceUrl: url };
    }
    logger.warn(`yt-dlp: ${err.substring(0,100)}`);
  }

  // Method 2: Try without js extractor args (maybe yt-dlp will use native Python extraction)
  if (platform === 'youtube') {
    try {
      logger.info('Method: yt-dlp native...');
      const outTpl2 = path.join(outputDir, `vid2_${Date.now()}_%(id)s.%(ext)s`);
      const result = execSync(`yt-dlp -o "${outTpl2}" "${url}" --no-playlist --max-filesize 100M 2>&1`, { 
        timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8' 
      });
      const files = fs.readdirSync(outputDir).filter(f => f.startsWith('vid2_') && f.endsWith('.mp4')).sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      if (files.length > 0 && fs.statSync(path.join(outputDir, files[0])).size > 500000) {
        logger.success(`OK: ${files[0]}`);
        return { path: path.join(outputDir, files[0]), title, platform, sourceUrl: url };
      }
    } catch (e) {
      const err = (e.stderr || e.message || '').toString();
      const files = fs.readdirSync(outputDir).filter(f => f.startsWith('vid2_') && (f.endsWith('.mp4') || f.endsWith('.webm')));
      if (files.length > 0 && fs.statSync(path.join(outputDir, files[0])).size > 500000) {
        logger.success(`OK: ${files[0]}`);
        return { path: path.join(outputDir, files[0]), title, platform, sourceUrl: url };
      }
      logger.warn(`yt-dlp native: ${err.substring(0,80)}`);
    }
  }

  // Method 3 (Bilibili): Try without headers
  if (platform === 'bilibili') {
    try {
      logger.info('Method: yt-dlp bilibili...');
      const outTpl3 = path.join(outputDir, `bili_${Date.now()}_%(id)s.%(ext)s`);
      execSync(`yt-dlp -o "${outTpl3}" "${url}" --no-playlist --max-filesize 100M 2>&1`, { timeout: 120000, maxBuffer: 50*1024*1024 });
      const files = fs.readdirSync(outputDir).filter(f => f.startsWith('bili_') && f.endsWith('.mp4')).sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      if (files.length > 0 && fs.statSync(path.join(outputDir, files[0])).size > 500000) {
        logger.success(`Bilibili OK: ${files[0]}`);
        return { path: path.join(outputDir, files[0]), title, platform, sourceUrl: url };
      }
    } catch (e) {
      logger.warn(`Bilibili: ${(e.stderr||e.message||'').substring(0,80)}`);
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
