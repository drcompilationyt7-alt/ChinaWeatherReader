/**
 * Downloader module
 * Uses YouTube cookies for auth + Deno for JS challenge solving.
 * Cookies from YOUTUBE_COOKIES secret -> /tmp/yt_cookies.txt
 *
 * FIX: yt-dlp execSync can hang. Each strategy gets max 180s via execSync timeout.
 * If all strategies fail, we still move on — never freeze the pipeline.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('Downloader');
const MAX_DURATION = 480;
const STRATEGY_TIMEOUT = 180000; // 3 min per strategy
const MAX_STRATEGIES = 6; // 3 strategies x 2 executables
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36';
const COOKIE_FILE = '/tmp/yt_cookies.txt';

function getProxyArg() {
  const proxy = process.env.YT_PROXY || '';
  return proxy ? `--proxy "${proxy}"` : '';
}

async function downloadVideo(entry, outputDir) {
  const url = entry.shortsUrl || entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });

  // Only download shorts URLs
  if (!url.toLowerCase().includes('short') && !url.toLowerCase().includes('/shorts/')) {
    logger.warn(`Skipping non-shorts URL: ${url.substring(0, 80)}`);
    return null;
  }

  const outputFile = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  const env = { ...process.env };
  try { env.PATH = `${path.dirname(process.execPath)}:${env.PATH || ''}`; } catch {}

  const hasCookies = fs.existsSync(COOKIE_FILE) && fs.statSync(COOKIE_FILE).size > 100;
  const cookieArg = hasCookies ? `--cookies "${COOKIE_FILE}"` : '';
  const proxyArg = getProxyArg();
  const ejsArg = '--js-runtimes deno';

  if (hasCookies) logger.info('Using YouTube cookies');
  else logger.warn('No cookies found!');

  const executables = ['yt-dlp', 'python3 -m yt_dlp'];

  for (const exe of executables) {
    const strategies = [
      {
        name: 'web',
        args: `${ejsArg} --extractor-args "youtube:player_client=web"`,
        format: '-f "bestvideo+bestaudio/best" --merge-output-format mp4'
      },
      {
        name: 'default',
        args: ejsArg,
        format: '-f "bestvideo+bestaudio/best" --merge-output-format mp4'
      },
      {
        name: 'android',
        args: `${ejsArg} --extractor-args "youtube:player_client=android"`,
        format: '-f "best"'
      },
    ];

    for (const s of strategies) {
      try {
        const cmd = `${exe} ${proxyArg} ${cookieArg} ${s.args} ${s.format} --download-sections "*0-${MAX_DURATION}" -o "${outputFile}" "${url}" --no-playlist --max-filesize 150M --socket-timeout 30 --retries 3 --user-agent "${UA}" --force-ipv4`;

        logger.info(`Try: ${exe} ${s.name}`);
        
        // execSync with timeout — if yt-dlp hangs, this kills the process
        execSync(cmd, { timeout: STRATEGY_TIMEOUT, maxBuffer: 200*1024*1024, encoding: 'utf8', env, killSignal: 'SIGKILL' });

        const files = fs.readdirSync(outputDir)
          .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000)
          .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);

        if (files.length > 0) {
          const fp = path.join(outputDir, files[0]);
          logger.success(`OK! ${files[0]} (${(fs.statSync(fp).size/1024/1024).toFixed(1)}MB)`);
          return { path: fp, title, platform, sourceUrl: url };
        }
      } catch (e) {
        const err = (e.stderr || e.stdout || e.message || '').toString().substring(0, 60);
        // Timeout errors are expected — just move to next strategy
        if (e.signal === 'SIGKILL' || e.killed) {
          logger.warn(`yt-dlp ${s.name}: TIMED OUT after ${STRATEGY_TIMEOUT/1000}s — skipping`);
        }
      }
    }
  }

  // If all strategies failed, log and return null — don't hang, move on
  logger.warn(`All download strategies failed for ${url.substring(0,60)} — moving on`);
  return null;
}

async function downloadVideos(urls, outputDir, targetCount = 3) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const downloaded = [];
  for (let i = 0; i < Math.min(urls.length, targetCount + 2); i++) {
    logger.info(`--- Video ${i+1}/${Math.min(urls.length, targetCount + 2)} ---`);
    const r = await downloadVideo(urls[i], outputDir);
    if (r) downloaded.push(r);
    if (downloaded.length >= targetCount) break;
  }
  logger.success(`Downloaded ${downloaded.length}/${targetCount} (attempted ${Math.min(urls.length, targetCount + 2)})`);
  return downloaded;
}

module.exports = { downloadVideo, downloadVideos };
