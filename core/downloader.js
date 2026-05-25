/**
 * Downloader module
 * Uses cookies + Shadowsocks proxy for YouTube downloads.
 * Cookies written by workflow from YOUTUBE_COOKIES secret.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('Downloader');
const MAX_DURATION = 480;
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36';
const COOKIE_FILE = '/tmp/yt_cookies.txt';

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  // Build env with Node.js in PATH
  const env = { ...process.env };
  try { env.PATH = `${path.dirname(process.execPath)}:${env.PATH || ''}`; } catch {}

  // socks5 -> socks5h for DNS through proxy
  let proxy = (process.env.YT_PROXY || '').replace(/^socks5:\/\//, 'socks5h://');
  const proxyArg = proxy ? `--proxy "${proxy}"` : '';
  
  // Check cookies
  const hasCookies = fs.existsSync(COOKIE_FILE) && fs.statSync(COOKIE_FILE).size > 100;
  const cookieArg = hasCookies ? `--cookies "${COOKIE_FILE}"` : '';
  
  if (hasCookies) logger.info('Using YouTube cookies from YOUTUBE_COOKIES secret');
  else logger.warn('No YouTube cookies found - download may fail');

  // Try pybundled yt-dlp first, then python3 -m
  const executables = ['yt-dlp', 'python3 -m yt_dlp'];
  
  for (const exe of executables) {
    const strategies = [
      // With cookies + proxy (best chance)
      { name: 'cookies+proxy', args: `${cookieArg} ${proxyArg} --extractor-args "youtube:player_client=web"` },
      // Cookies only
      { name: 'cookies', args: `${cookieArg} --extractor-args "youtube:player_client=web"` },
      // Cookies + embedded client
      { name: 'cookies+embed', args: `${cookieArg} --extractor-args "youtube:player_client=web_embedded"` },
      // No cookies with proxy
      { name: 'proxy', args: `${proxyArg}` },
    ];
    
    for (const s of strategies) {
      try {
        const cmd = `${exe} ${s.args} -f "best[height<=720]" --download-sections "*0-${MAX_DURATION}" -o "${outputFile}" "${url}" --no-playlist --max-filesize 150M --socket-timeout 30 --retries 3 --user-agent "${UA}" --force-ipv4 --throttled-rate 200K 2>&1 | tail -3`;
        
        logger.info(`Try: ${exe} ${s.name}`);
        execSync(cmd, { timeout: 180000, maxBuffer: 200*1024*1024, encoding: 'utf8', env });
        
        const files = fs.readdirSync(outputDir)
          .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000)
          .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
        
        if (files.length > 0) {
          const fp = path.join(outputDir, files[0]);
          logger.success(`OK! ${files[0]} (${(fs.statSync(fp).size/1024/1024).toFixed(1)}MB)`);
          return { path: fp, title, platform, sourceUrl: url };
        }
      } catch (e) {
        const err = (e.stderr || e.stdout || e.message || '').toString().substring(0, 80);
      }
    }
  }

  logger.warn(`Failed: ${url.substring(0,60)}`);
  return null;
}

async function downloadVideos(urls, outputDir) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const downloaded = [];
  for (let i = 0; i < Math.min(urls.length, 3); i++) {
    logger.info(`--- Video ${i+1}/3 ---`);
    const r = await downloadVideo(urls[i], outputDir);
    if (r) downloaded.push(r);
  }
  logger.success(`Downloaded ${downloaded.length}/3`);
  return downloaded;
}

module.exports = { downloadVideo, downloadVideos };
