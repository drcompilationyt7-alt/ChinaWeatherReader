/**
 * Downloader module
 * Uses piped.video API (free YouTube proxy) to download without cookies.
 * Piped API provides direct video stream URLs that work from any IP.
 * Falls back to direct yt-dlp + invidious.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('Downloader');

// Piped instances (free YouTube proxies)
const PIPED_INSTANCES = [
  'https://pipedapi.nadeko.net',
  'https://pipedapi.kavin.rocks',
  'https://api.piped.privacydev.net',
];

async function fetchWithFallback(urls, options = {}) {
  for (const url of urls) {
    try {
      const resp = await fetch(url, { 
        ...options,
        signal: AbortSignal.timeout(10000)
      });
      if (resp.ok) return { ok: true, data: await resp.json(), url };
    } catch {}
  }
  return { ok: false };
}

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}.mp4`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Method 1: piped.video API (YouTube only, free, no cookies)
  if (platform === 'youtube') {
    try {
      const videoId = url.match(/[?&]v=([a-zA-Z0-9_-]{11})/);
      if (!videoId) {
        logger.warn('Could not extract video ID');
      } else {
        const vid = videoId[1];
        
        // Get video info from piped API
        const instances = PIPED_INSTANCES.map(i => `${i}/streams/${vid}`);
        const result = await fetchWithFallback(instances);
        
        if (result.ok && result.data) {
          const data = result.data;
          // Find best video stream (mp4, <=720p, with audio)
          const streams = data.videoStreams || [];
          
          // Sort: prefer mp4 with 720p max, then smaller
          const sorted = streams
            .filter(s => s.format === 'MP4' || s.mimeType?.includes('mp4'))
            .sort((a, b) => {
              const aH = a.height || 0;
              const bH = b.height || 0;
              // Prefer 720p, then 480p, etc.
              if (aH <= 720 && bH <= 720) return bH - aH;
              if (aH <= 720) return -1;
              if (bH <= 720) return 1;
              return aH - bH;
            });
          
          const bestStream = sorted[0] || data.videoStreams?.[0];
          
          if (bestStream?.url) {
            logger.info(`Downloading via piped API: ${bestStream.height || '?'}p`);
            const dlResp = await fetch(bestStream.url, { 
              redirect: 'follow',
              headers: { 'User-Agent': 'Mozilla/5.0' },
              signal: AbortSignal.timeout(120000),
            });
            
            if (dlResp.ok) {
              const chunks = [];
              const reader = dlResp.body.getReader();
              let totalSize = 0;
              while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                chunks.push(value);
                totalSize += value.length;
              }
              const buffer = Buffer.concat(chunks);
              
              if (totalSize > 500000) {
                fs.writeFileSync(outputFile, buffer);
                logger.success(`Piped API: ${(totalSize/1024/1024).toFixed(1)}MB`);
                return { path: outputFile, title, platform, sourceUrl: url };
              }
              logger.warn(`Piped stream too small: ${(totalSize/1024).toFixed(0)}KB`);
            }
          }
          
          // Try audio + video streams as fallback
          const audioStream = data.audioStreams?.[0];
          const videoOnlyStream = (data.videoStreams || [])
            .filter(s => !s.format?.includes('mp4'))
            .sort((a, b) => (b.height||0) - (a.height||0))
            .find(s => s.height <= 720);
          
          if (videoOnlyStream?.url && audioStream?.url) {
            // Download separately and merge (requires ffmpeg)
            logger.info('Downloading video+audio streams separately...');
            const vFile = outputFile.replace('.mp4', '_v.mp4');
            const aFile = outputFile.replace('.mp4', '_a.webm');
            
            const vResp = await fetch(videoOnlyStream.url, { headers: { 'User-Agent': 'Mozilla/5.0' }, signal: AbortSignal.timeout(120000) });
            const aResp = await fetch(audioStream.url, { headers: { 'User-Agent': 'Mozilla/5.0' }, signal: AbortSignal.timeout(120000) });
            
            if (vResp.ok && aResp.ok) {
              const vBuf = Buffer.from(await vResp.arrayBuffer());
              const aBuf = Buffer.from(await aResp.arrayBuffer());
              fs.writeFileSync(vFile, vBuf);
              fs.writeFileSync(aFile, aBuf);
              
              // Merge with ffmpeg
              execSync(`ffmpeg -y -i "${vFile}" -i "${aFile}" -c:v copy -c:a aac -shortest "${outputFile}" 2>/dev/null`, { timeout: 60000 });
              
              if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 500000) {
                logger.success(`Merged: ${(fs.statSync(outputFile).size/1024/1024).toFixed(1)}MB`);
                try { fs.unlinkSync(vFile); } catch {}
                try { fs.unlinkSync(aFile); } catch {}
                return { path: outputFile, title, platform, sourceUrl: url };
              }
            }
            try { fs.unlinkSync(vFile); } catch {}
            try { fs.unlinkSync(aFile); } catch {}
          }
        } else {
          logger.warn('Piped API returned no data');
        }
      }
    } catch (e) {
      logger.warn(`Piped API failed: ${e.message.substring(0,80)}`);
    }
  }

  // Method 2: yt-dlp with throttled rate (no cookies, just standard request)
  try {
    logger.info('Fallback: yt-dlp throttled...');
    const outputTpl = outputFile.replace('.mp4', '_%(id)s.%(ext)s');
    execSync(`yt-dlp --throttled-rate 100K -o "${outputTpl}" "${url}" --no-playlist --max-filesize 100M 2>&1`, { timeout: 180000, maxBuffer: 50*1024*1024 });
    const files = fs.readdirSync(outputDir).filter(f => f.endsWith('.mp4') || f.endsWith('.webm')).sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
    if (files.length > 0) {
      const fp = path.join(outputDir, files[0]);
      const size = fs.statSync(fp).size;
      if (size > 500000) {
        logger.success(`yt-dlp: ${files[0]} (${(size/1024/1024).toFixed(1)}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
    }
  } catch (e) {
    logger.warn(`yt-dlp: ${e.message.substring(0,80)}`);
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
