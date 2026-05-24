/**
 * Downloader module
 * Uses free API proxies to bypass CI IP blocks.
 * Platforms like YouTube and Bilibili block GitHub Actions IPs for yt-dlp,
 * but public download APIs work fine.
 * 
 * Strategy order:
 * 1. Cobalt.tools API (free, no key, works for YouTube & Bilibili)
 * 2. Invidious redirect (free YouTube proxy)
 * 3. yt-dlp with iOS client (fallback)
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('Downloader');

/**
 * Download a single video using free APIs that bypass CI IP blocks
 */
async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}.mp4`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Method 1: Try cobalt.tools API (free, no auth, handles YouTube, Bilibili, TikTok)
  try {
    logger.info('Method 1: Cobalt.tools API...');
    const cobaltUrl = 'https://api.cobalt.tools/api/json';
    const resp = await fetch(cobaltUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'User-Agent': 'Mozilla/5.0',
      },
      body: JSON.stringify({
        url: url,
        videoQuality: '720',
        filenamePattern: 'basic',
        isAudioOnly: false,
        disableMetadata: true,
      }),
    });
    
    if (resp.ok) {
      const data = await resp.json();
      if (data && data.url) {
        // Download the actual video file
        const videoResp = await fetch(data.url, { 
          headers: { 'User-Agent': 'Mozilla/5.0' },
          redirect: 'follow',
        });
        const buffer = Buffer.from(await videoResp.arrayBuffer());
        fs.writeFileSync(outputFile, buffer);
        const sizeMB = buffer.length / 1024 / 1024;
        if (sizeMB > 1) {
          logger.success(`Cobalt API: ${outputFile} (${sizeMB.toFixed(1)}MB)`);
          return { path: outputFile, title, platform, sourceUrl: url };
        }
        logger.warn(`Cobalt returned small file (${sizeMB.toFixed(1)}MB)`);
        try { fs.unlinkSync(outputFile); } catch {}
      }
    } else {
      logger.warn(`Cobalt API: HTTP ${resp.status}`);
    }
  } catch (e) {
    logger.warn(`Cobalt failed: ${e.message.substring(0,100)}`);
  }

  // Method 2: Try yt-dlp with iOS client (hardcoded API keys, less restricted)
  try {
    logger.info('Method 2: yt-dlp iOS client...');
    const cmd = `yt-dlp --extractor-args "youtube:player_client=ios" --user-agent "com.google.ios.youtube/19.45.3 (iPhone16,2; U; CPU iOS 18_3_2)" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M 2>&1`;
    execSync(cmd, { timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8' });
    if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 100000) {
      const sizeMB = fs.statSync(outputFile).size / 1024 / 1024;
      logger.success(`yt-dlp iOS: ${outputFile} (${sizeMB.toFixed(1)}MB)`);
      return { path: outputFile, title, platform, sourceUrl: url };
    }
  } catch (e) {
    logger.warn(`yt-dlp iOS failed: ${e.message.substring(0,100)}`);
  }

  // Method 3: Try invidious redirect (free YouTube proxy)
  if (platform === 'youtube') {
    try {
      logger.info('Method 3: Invidious proxy...');
      const videoId = url.match(/[?&]v=([a-zA-Z0-9_-]{11})/);
      if (videoId) {
        const apiUrl = `https://inv.nadeko.net/api/v1/videos/${videoId[1]}`;
        const resp = await fetch(apiUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } });
        if (resp.ok) {
          const data = await resp.json();
          const streams = data?.formatStreams || [];
          const adaptive = data?.adaptiveFormats || [];
          // Find best 720p stream
          const best = [...streams, ...adaptive]
            .filter(s => s.type?.startsWith('video/mp4'))
            .sort((a, b) => (b.height || 0) - (a.height || 0))
            .find(s => s.height <= 720) || streams[0];
          
          if (best?.url) {
            logger.info(`Downloading from invidious...`);
            const vResp = await fetch(best.url, { redirect: 'follow' });
            const buffer = Buffer.from(await vResp.arrayBuffer());
            fs.writeFileSync(outputFile, buffer);
            const sizeMB = buffer.length / 1024 / 1024;
            if (sizeMB > 0.5) {
              logger.success(`Invidious: ${outputFile} (${sizeMB.toFixed(1)}MB)`);
              return { path: outputFile, title, platform, sourceUrl: url };
            }
            try { fs.unlinkSync(outputFile); } catch {}
          }
        }
      }
    } catch (e) {
      logger.warn(`Invidious failed: ${e.message.substring(0,100)}`);
    }
  }

  // Method 4: yt-dlp with plain request (last resort for YouTube/Bilibili)
  try {
    logger.info('Method 4: yt-dlp default...');
    const cmd = `yt-dlp -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M 2>&1`;
    execSync(cmd, { timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8' });
    if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 100000) {
      const sizeMB = fs.statSync(outputFile).size / 1024 / 1024;
      logger.success(`yt-dlp default: ${outputFile} (${sizeMB.toFixed(1)}MB)`);
      return { path: outputFile, title, platform, sourceUrl: url };
    }
  } catch (e) {
    logger.warn(`yt-dlp default failed: ${e.message.substring(0,100)}`);
  }

  logger.warn(`All methods failed: ${url.substring(0,60)}`);
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
