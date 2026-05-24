/**
 * Downloader module
 * Uses Node.js http.get to download Bilibili videos via their CDN.
 * Bypasses yt-dlp/Cloudflare issues by getting CDN url from Bilibili API
 * then downloading directly with proper headers.
 */
const https = require('https');
const http = require('http');
const urlMod = require('url');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('Downloader');

function httpGet(url, outputPath, timeout = 120000) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const mod = parsed.protocol === 'https:' ? https : http;
    const options = {
      hostname: parsed.hostname,
      port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
      path: parsed.pathname + parsed.search,
      method: 'GET',
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Referer': 'https://www.bilibili.com/',
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
      },
      timeout,
    };

    const req = mod.request(options, (res) => {
      // Follow redirects
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        const redirectUrl = new URL(res.headers.location, url).href;
        logger.info(`Redirecting to ${redirectUrl.substring(0, 60)}...`);
        return httpGet(redirectUrl, outputPath, timeout).then(resolve).catch(reject);
      }

      if (res.statusCode !== 200) {
        reject(new Error(`HTTP ${res.statusCode}`));
        return;
      }

      let contentLength = parseInt(res.headers['content-length'] || '0');
      logger.info(`Content-Length: ${(contentLength / 1024 / 1024).toFixed(1)}MB`);

      if (contentLength < 100000 && contentLength > 0) {
        reject(new Error('File too small'));
        return;
      }

      const file = fs.createWriteStream(outputPath);
      let downloaded = 0;

      res.on('data', (chunk) => {
        file.write(chunk);
        downloaded += chunk.length;
      });

      res.on('end', () => {
        file.end();
        if (downloaded > 100000) {
          resolve(outputPath);
        } else {
          reject(new Error(`Too small: ${(downloaded/1024).toFixed(0)}KB`));
        }
      });

      res.on('error', (err) => {
        file.close();
        reject(err);
      });
    });

    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Timeout')); });
    req.end();
  });
}

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}.mp4`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // For Bilibili: Get CDN URL from API, download directly
  if (platform === 'bilibili') {
    try {
      const bvMatch = url.match(/BV([a-zA-Z0-9]+)/i);
      if (bvMatch) {
        const bvid = `BV${bvMatch[1]}`;
        logger.info(`Getting Bilibili CDN URL for ${bvid}...`);

        // Get video info
        const infoUrl = `https://api.bilibili.com/x/web-interface/view?bvid=${bvid}`;
        
        const info = await new Promise((resolve, reject) => {
          https.get(infoUrl, {
            headers: {
              'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
              'Referer': 'https://www.bilibili.com/',
            },
            timeout: 15000,
          }, (res) => {
            let data = '';
            res.on('data', c => data += c);
            res.on('end', () => {
              try { resolve(JSON.parse(data)); }
              catch { reject(new Error('Parse failed')); }
            });
          }).on('error', reject);
        });

        if (!info?.data?.cid) {
          logger.warn('Could not get Bilibili video info (412 or blocked)');
        } else {
          const cid = info.data.cid;
          const videoTitle = info.data.title || title;
          
          // Get CDN play URL
          const playUrl = `https://api.bilibili.com/x/player/playurl?bvid=${bvid}&cid=${cid}&qn=80&fnver=0&fnval=4048`;
          
          const playJson = await new Promise((resolve, reject) => {
            https.get(playUrl, {
              headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://www.bilibili.com/',
              },
              timeout: 15000,
            }, (res) => {
              let data = '';
              res.on('data', c => data += c);
              res.on('end', () => {
                try { resolve(JSON.parse(data)); }
                catch { reject(new Error('Parse failed')); }
              });
            }).on('error', reject);
          });

          const durl = playJson?.data?.durl;
          const cdnUrl = durl?.[0]?.url;
          const backupUrl = durl?.[0]?.backup_url?.[0];

          if (cdnUrl) {
            logger.info(`CDN URL obtained. Downloading...`);
            try {
              await httpGet(cdnUrl, outputFile, 180000);
              const size = fs.statSync(outputFile).size / 1024 / 1024;
              logger.success(`Downloaded: ${(size).toFixed(1)}MB`);
              return { path: outputFile, title: videoTitle, platform, sourceUrl: url };
            } catch (e) {
              logger.warn(`CDN error: ${e.message}`);
              if (backupUrl) {
                logger.info('Trying backup CDN...');
                await httpGet(backupUrl, outputFile, 180000);
                const size = fs.statSync(outputFile).size / 1024 / 1024;
                logger.success(`Backup CDN: ${(size).toFixed(1)}MB`);
                return { path: outputFile, title: videoTitle, platform, sourceUrl: url };
              }
            }
          }
        }
      }
    } catch (e) {
      logger.warn(`Bilibili: ${e.message.substring(0, 80)}`);
    }
  }

  // For YouTube and other: try yt-dlp with proxy from env directly
  if (platform === 'youtube' || platform === 'other') {
    const proxyArg = process.env.YT_PROXY ? `--proxy "${process.env.YT_PROXY}"` : '';
    try {
      const outTpl = outputFile.replace('.mp4', '_%(id)s.%(ext)s');
      const cmd = `yt-dlp ${proxyArg} -f "best[height<=720]" -o "${outTpl}" "${url}" --no-playlist --max-filesize 100M --socket-timeout 10 2>&1 | head -5`;
      execSync(cmd, { timeout: 60000, maxBuffer: 10*1024*1024, encoding: 'utf8' });
      const files = fs.readdirSync(outputDir).filter(f => f.endsWith('.mp4') || f.endsWith('.webm')).sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      if (files.length > 0 && fs.statSync(path.join(outputDir, files[0])).size > 500000) {
        logger.success(`yt-dlp OK: ${files[0]}`);
        return { path: path.join(outputDir, files[0]), title, platform, sourceUrl: url };
      }
    } catch {}
  }

  logger.warn(`Failed: ${url.substring(0,60)}`);
  return null;
}

async function downloadVideos(urls, outputDir) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const downloaded = [];
  for (let i = 0; i < Math.min(urls.length, 5); i++) {
    logger.info(`--- Video ${i+1}/${Math.min(urls.length,5)} ---`);
    const result = await downloadVideo(urls[i], outputDir);
    if (result) downloaded.push(result);
  }
  logger.success(`Downloaded ${downloaded.length}/${Math.min(urls.length,5)}`);
  return downloaded;
}

module.exports = { downloadVideo, downloadVideos };
