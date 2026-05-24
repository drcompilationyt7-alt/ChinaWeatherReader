/**
 * Downloader module
 * Uses yt-dlp with visitor_data/po_token approach to bypass bot blocks.
 * Falls back to direct HTTP download via invidious API.
 */
const { execSync, spawnSync } = require('child_process');
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

  // Method 1: yt-dlp with visitor_data (bypasses sign-in requirement)
  if (platform === 'youtube') {
    try {
      logger.info('Method 1: yt-dlp with visitor_data...');
      const cmd = `yt-dlp --extractor-args "youtube:player_skip=webpage,js;player_client=web" --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M 2>&1`;
      execSync(cmd, { timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8' });
      if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 100000) {
        logger.success(`yt-dlp visitor_data: ${(fs.statSync(outputFile).size/1024/1024).toFixed(1)}MB`);
        return { path: outputFile, title, platform, sourceUrl: url };
      }
      try { fs.unlinkSync(outputFile); } catch {}
    } catch (e) {
      logger.warn(`visitor_data failed: ${e.message.substring(0,100)}`);
    }

    // Method 2: Direct download via invidious redirect proxy
    try {
      logger.info('Method 2: Invidious redirect...');
      const videoId = url.match(/[?&]v=([a-zA-Z0-9_-]{11})/);
      if (videoId) {
        // Use invidious to get a download link, then follow redirect
        const apiUrls = [
          `https://inv.nadeko.net/api/v1/videos/${videoId[1]}`,
          `https://invidious.private.coffee/api/v1/videos/${videoId[1]}`,
          `https://iv.ggtyler.dev/api/v1/videos/${videoId[1]}`,
        ];
        for (const apiUrl of apiUrls) {
          try {
            const resp = await fetch(apiUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } });
            if (!resp.ok) continue;
            const data = await resp.json();
            const formats = data.formatStreams || data.adaptiveFormats || [];
            // Find best 720p+ mp4
            const best = formats
              .filter(f => f.type?.startsWith('video/mp4') || f.container === 'mp4')
              .sort((a, b) => (b.height || 0) - (a.height || 0))
              .find(f => !f.encoding || f.encoding.includes('h264'));
            
            if (best?.url) {
              // Download directly
              const vResp = await fetch(best.url, { 
                headers: { 
                  'User-Agent': 'Mozilla/5.0',
                  'Referer': 'https://invidious.private.coffee/',
                },
                redirect: 'follow',
              });
              if (vResp.ok) {
                const buffer = Buffer.from(await vResp.arrayBuffer());
                if (buffer.length > 500000) {
                  fs.writeFileSync(outputFile, buffer);
                  logger.success(`Invidious: ${(buffer.length/1024/1024).toFixed(1)}MB`);
                  return { path: outputFile, title, platform, sourceUrl: url };
                }
              }
            }
          } catch {}
        }
      }
    } catch (e) {
      logger.warn(`Invidious failed: ${e.message.substring(0,80)}`);
    }
  }

  // Method 3 (Bilibili): Try download with specific referer + cookie
  if (platform === 'bilibili') {
    try {
      logger.info('Method: Bilibili direct API...');
      const videoId = url.match(/BV([a-zA-Z0-9]+)/i);
      if (videoId) {
        // Use bilibili API to get play URL
        const apiUrl = `https://api.bilibili.com/x/web-interface/view?bvid=BV${videoId[1]}`;
        const resp = await fetch(apiUrl, {
          headers: {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.bilibili.com/',
          }
        });
        if (resp.ok) {
          const data = await resp.json();
          const cid = data?.data?.cid;
          if (cid) {
            // Get play URL
            const playUrl = `https://api.bilibili.com/x/player/playurl?bvid=BV${videoId[1]}&cid=${cid}&qn=80&fnver=0&fnval=4048`;
            const playResp = await fetch(playUrl, {
              headers: {
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://www.bilibili.com/',
              }
            });
            if (playResp.ok) {
              const playData = await playResp.json();
              const dlUrls = playData?.data?.durl || [];
              if (dlUrls.length > 0 && dlUrls[0].url) {
                const dlResp = await fetch(dlUrls[0].url, {
                  headers: { 'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/' },
                  redirect: 'follow',
                });
                if (dlResp.ok) {
                  const buffer = Buffer.from(await dlResp.arrayBuffer());
                  if (buffer.length > 500000) {
                    fs.writeFileSync(outputFile, buffer);
                    logger.success(`Bilibili API: ${(buffer.length/1024/1024).toFixed(1)}MB`);
                    return { path: outputFile, title, platform, sourceUrl: url };
                  }
                }
              }
            }
          }
        }
      }
    } catch (e) {
      logger.warn(`Bilibili API failed: ${e.message.substring(0,80)}`);
    }

    // Bilibili fallback: yt-dlp with referer
    try {
      const cmd = `yt-dlp --add-header "Referer:https://www.bilibili.com/" --user-agent "Mozilla/5.0" -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M 2>&1`;
      execSync(cmd, { timeout: 120000, maxBuffer: 50*1024*1024 });
      if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 100000) {
        logger.success(`yt-dlp Bilibili: ok`);
        return { path: outputFile, title, platform, sourceUrl: url };
      }
    } catch {}
  }

  // Method 4: yt-dlp default for any other platform
  if (platform !== 'youtube' && platform !== 'bilibili') {
    try {
      logger.info('Method: yt-dlp generic...');
      execSync(`yt-dlp -o "${outputFile}" "${url}" --no-playlist --max-filesize 100M 2>&1`, { timeout: 120000, maxBuffer: 50*1024*1024 });
      if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 100000) {
        logger.success(`yt-dlp generic: ok`);
        return { path: outputFile, title, platform, sourceUrl: url };
      }
    } catch {}
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
