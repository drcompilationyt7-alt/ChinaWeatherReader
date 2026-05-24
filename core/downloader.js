/**
 * Downloader module
 * Uses yt-dlp with platform-specific strategies
 */
const { spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('Downloader');

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputTemplate = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Build strategy args for each platform
  const strategySets = [];

  if (platform === 'youtube') {
    strategySets.push([
      ['--extractor-args', 'youtube:player_client=android'],
      ['--user-agent', 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36'],
      ['-f', 'best[height<=720]'],
    ]);
    strategySets.push([
      ['--user-agent', 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36'],
      ['-f', 'best[height<=720]'],
    ]);
    strategySets.push([
      ['--extractor-args', 'youtube:player_client=android'],
    ]);
  } else if (platform === 'bilibili') {
    strategySets.push([
      ['--add-header', 'Referer:https://www.bilibili.com/'],
      ['--add-header', 'Origin:https://www.bilibili.com'],
      ['--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'],
    ]);
    strategySets.push([
      ['--add-header', 'Referer:https://www.bilibili.com/'],
    ]);
  }

  // If no platform-specific strategies, add a default
  if (strategySets.length === 0) strategySets.push([]);

  // Build environment with deno in PATH
  const env = { ...process.env };
  if (process.env.DENO_INSTALL) {
    env.PATH = `${process.env.DENO_INSTALL}/bin:${env.PATH || ''}`;
  }
  // Also try common deno paths
  const denoPaths = ['/home/runner/.deno/bin', '/root/.deno/bin', '/usr/local/bin'];
  for (const p of denoPaths) {
    if (fs.existsSync(p)) {
      env.PATH = `${p}:${env.PATH || ''}`;
    }
  }

  for (let s = 0; s < strategySets.length; s++) {
    const extraArgs = strategySets[s];
    const args = [
      ...extraArgs.flat(),
      '-o', outputTemplate,
      url,
      '--no-playlist',
      '--max-filesize', '100M',
    ];

    try {
      const r = spawnSync('yt-dlp', args, {
        timeout: 180000,
        maxBuffer: 50 * 1024 * 1024,
        encoding: 'utf8',
        env,
      });

      const stderr = (r.stderr || '').trim();
      const stdout = (r.stdout || '').trim();

      if (r.status === 0) {
        // Find downloaded file
        const files = fs.readdirSync(outputDir)
          .filter(f => f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mkv'))
          .sort((a, b) => fs.statSync(path.join(outputDir, b)).mtimeMs - fs.statSync(path.join(outputDir, a)).mtimeMs);
        
        if (files.length > 0) {
          const fp = path.join(outputDir, files[0]);
          const sizeMB = fs.statSync(fp).size / 1024 / 1024;
          if (sizeMB > 0.5) {
            logger.success(`Downloaded: ${files[0]} (${sizeMB.toFixed(1)}MB)`);
            return { path: fp, title, platform, sourceUrl: url };
          }
          logger.warn(`File too small (${sizeMB.toFixed(1)}MB), trying next`);
          try { fs.unlinkSync(fp); } catch {}
        } else {
          logger.warn(`No file found. stdout: ${(stdout).substring(0,200)}`);
        }
      } else {
        const errMsg = (stderr || stdout).substring(0, 250);
        logger.warn(`Strategy ${s+1} failed: ${errMsg}`);
      }
    } catch (e) {
      logger.warn(`Strategy ${s+1} error: ${e.message.substring(0,100)}`);
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
