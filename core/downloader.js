/**
 * Downloader module
 * Priority: Bilibili -> RedNote -> Douyin -> TikTok -> YouTube
 * Uses each platform's direct API to get CDN URLs (bypasses yt-dlp extractors)
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('Downloader');

async function downloadFile(url, outputFile, timeout = 60000) {
  const resp = await fetch(url, {
    headers: { 'User-Agent': 'Mozilla/5.0' },
    redirect: 'follow',
    signal: AbortSignal.timeout(timeout),
  });
  if (!resp.ok) return null;
  const chunks = [];
  const reader = resp.body.getReader();
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    total += value.length;
    if (total > 100 * 1024 * 1024) { logger.warn('>100MB, stopping'); break; }
  }
  if (total < 50000) return null;
  fs.writeFileSync(outputFile, Buffer.concat(chunks));
  return outputFile;
}

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}.mp4`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // BILIBILI: Get direct CDN URL from their API
  if (platform === 'bilibili') {
    try {
      const bvMatch = url.match(/BV([a-zA-Z0-9]+)/i);
      if (bvMatch) {
        const bvid = `BV${bvMatch[1]}`;
        
        // Step 1: Get video info (cid)
        const infoResp = await fetch(`https://api.bilibili.com/x/web-interface/view?bvid=${bvid}`, {
          headers: { 'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/' }
        });
        if (infoResp.ok) {
          const info = await infoResp.json();
          const cid = info?.data?.cid;
          const title2 = info?.data?.title || title;
          
          if (cid) {
            // Step 2: Get play URL (CDN)
            const playResp = await fetch(`https://api.bilibili.com/x/player/playurl?bvid=${bvid}&cid=${cid}&qn=80&fnver=0&fnval=4048`, {
              headers: { 'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/' }
            });
            if (playResp.ok) {
              const playData = await playResp.json();
              const durl = playData?.data?.durl;
              const videoUrl = durl?.[0]?.url;
              const backupUrl = durl?.[0]?.backup_url?.[0];
              
              if (videoUrl) {
                logger.info('Downloading from Bilibili CDN...');
                const result = await downloadFile(videoUrl, outputFile, 120000);
                if (result) {
                  const sizeMB = fs.statSync(result).size / 1024 / 1024;
                  logger.success(`Bilibili CDN: ${(sizeMB).toFixed(1)}MB`);
                  return { path: result, title: title2, platform, sourceUrl: url };
                }
              }
              if (backupUrl) {
                logger.info('Trying Bilibili backup CDN...');
                const result = await downloadFile(backupUrl, outputFile, 120000);
                if (result) {
                  const sizeMB = fs.statSync(result).size / 1024 / 1024;
                  logger.success(`Bilibili backup: ${(sizeMB).toFixed(1)}MB`);
                  return { path: result, title: title2, platform, sourceUrl: url };
                }
              }
            }
          }
        }
      }
    } catch (e) {
      logger.warn(`Bilibili API: ${e.message.substring(0,80)}`);
    }
  }

  // DOUYIN: Try direct download via API
  if (platform === 'douyin') {
    try {
      // Try yt-dlp for Douyin (might work outside GitHub Actions)
      logger.info('Trying yt-dlp for Douyin...');
      const outTpl = path.join(outputDir, `dy_${Date.now()}_%(id)s.%(ext)s`);
      execSync(`yt-dlp -o "${outTpl}" "${url}" --no-playlist --max-filesize 100M 2>&1`, { timeout: 120000, maxBuffer: 50*1024*1024 });
      const files = fs.readdirSync(outputDir).filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 100000).sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      if (files.length > 0) {
        logger.success(`Douyin OK: ${files[0]}`);
        return { path: path.join(outputDir, files[0]), title, platform, sourceUrl: url };
      }
    } catch (e) {
      logger.warn(`Douyin: ${(e.stderr||e.message||'').substring(0,80)}`);
    }
  }

  // TIKTOK: Try yt-dlp directly
  if (platform === 'tiktok') {
    try {
      logger.info('Trying yt-dlp for TikTok...');
      const outTpl = path.join(outputDir, `tt_${Date.now()}_%(id)s.%(ext)s`);
      execSync(`yt-dlp -o "${outTpl}" "${url}" --no-playlist --max-filesize 100M 2>&1`, { timeout: 120000, maxBuffer: 50*1024*1024 });
      const files = fs.readdirSync(outputDir).filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 100000).sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      if (files.length > 0) {
        logger.success(`TikTok OK: ${files[0]}`);
        return { path: path.join(outputDir, files[0]), title, platform, sourceUrl: url };
      }
    } catch (e) {
      logger.warn(`TikTok: ${(e.stderr||e.message||'').substring(0,80)}`);
    }
  }

  // YOUTUBE: yt-dlp with all tricks (last resort)
  if (platform === 'youtube') {
    const outTpl = path.join(outputDir, `yt_${Date.now()}_%(id)s.%(ext)s`);
    const attempts = [
      `python3 -m yt_dlp --js-runtimes node --extractor-args "youtube:player_client=android" -f "best[height<=720]" -o "${outTpl}" "${url}" --no-playlist --max-filesize 100M`,
      `python3 -m yt_dlp --js-runtimes node -f "best[height<=720]" -o "${outTpl}" "${url}" --no-playlist --max-filesize 100M`,
    ];
    for (const cmd of attempts) {
      try {
        execSync(cmd, { timeout: 180000, maxBuffer: 50*1024*1024, encoding: 'utf8' });
        const files = fs.readdirSync(outputDir).filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 500000).sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
        if (files.length > 0) {
          logger.success(`YouTube OK: ${files[0]}`);
          return { path: path.join(outputDir, files[0]), title, platform, sourceUrl: url };
        }
      } catch {}
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
