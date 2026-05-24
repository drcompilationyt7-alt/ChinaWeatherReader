/**
 * Downloader module
 * Uses browser cookies from env var + yt-dlp.
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
  const outputFinal = outputFile.replace('%(id)s.%(ext)s', 'final.mp4');
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Write cookies from env
  const cookieFile = path.join(outputDir, 'cookies.txt');
  if (process.env.YOUTUBE_COOKIES && process.env.YOUTUBE_COOKIES.length > 50) {
    try {
      fs.writeFileSync(cookieFile, process.env.YOUTUBE_COOKIES, 'utf8');
      logger.info(`Cookies written: ${(process.env.YOUTUBE_COOKIES.length / 1024).toFixed(1)}KB`);
    } catch (e) {
      logger.warn(`Failed to write cookies: ${e.message}`);
    }
  } else {
    logger.warn('No YOUTUBE_COOKIES env var set');
  }

  // Build command list per platform
  const commands = [];
  
  if (fs.existsSync(cookieFile) && fs.statSync(cookieFile).size > 100) {
    commands.push(`yt-dlp --cookies "${cookieFile}" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`);
  }
  
  commands.push(`yt-dlp -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M`);

  for (let c = 0; c < commands.length; c++) {
    try {
      logger.info(`Trying command ${c + 1}...`);
      const stdout = execSync(commands[c], { timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8', cwd: outputDir });
      
      // Find what was downloaded
      const files = fs.readdirSync(outputDir)
        .filter(f => f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mkv'))
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        const size = fs.statSync(fp).size;
        if (size > 500000) {
          logger.success(`Downloaded: ${files[0]} (${(size/1024/1024).toFixed(1)}MB)`);
          try { fs.unlinkSync(cookieFile); } catch {}
          return { path: fp, title, platform, sourceUrl: url };
        }
        logger.warn(`File too small (${(size/1024).toFixed(0)}KB), removing`);
        try { fs.unlinkSync(fp); } catch {}
      } else {
        logger.warn(`No output file found. Stdout last 200: ${(stdout||'').slice(-200)}`);
      }
    } catch (e) {
      const stderr = e.stderr?.toString().trim() || e.message;
      logger.warn(`Cmd ${c+1} failed: ${stderr.substring(0, 300)}`);
      // Check if file was actually downloaded despite error
      const files = fs.readdirSync(outputDir)
        .filter(f => f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mkv'))
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        const size = fs.statSync(fp).size;
        if (size > 500000) {
          logger.success(`Downloaded despite error: ${files[0]} (${(size/1024/1024).toFixed(1)}MB)`);
          try { fs.unlinkSync(cookieFile); } catch {}
          return { path: fp, title, platform, sourceUrl: url };
        }
      }
    }
  }

  try { fs.unlinkSync(cookieFile); } catch {}
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
