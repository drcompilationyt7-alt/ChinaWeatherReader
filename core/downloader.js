/**
 * Downloader module
 * Uses PO Token plugins + Shadowsocks + proper clients for YouTube downloads.
 * No cookies needed - fully autonomous via bgutil + wpc plugins.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('Downloader');
const MAX_DURATION = 480;
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36';

async function downloadVideo(entry, outputDir) {
  const url = entry.url;
  const title = entry.title || 'video';
  const platform = entry.platform || 'unknown';
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  
  const outputFile = path.join(outputDir, `vid_${Date.now()}_%(id)s.%(ext)s`);
  logger.info(`Downloading ${platform}: ${url.substring(0,80)}`);

  const proxy = process.env.YT_PROXY ? `--proxy "${process.env.YT_PROXY}"` : '';
  
  // Build env with Node.js PATH
  const env = { ...process.env };
  try { env.PATH = `${path.dirname(process.execPath)}:${env.PATH || ''}`; } catch {}

  // Multiple strategies with PO Token plugins + different clients
  const cmds = [];
  const fmt = '-f "best[height<=720]"';
  const opts = `--download-sections "*0-${MAX_DURATION}" --force-keyframes-at-cuts -o "${outputFile}" "${url}" --no-playlist --max-filesize 150M --socket-timeout 30 --retries 3 --no-part --user-agent "${UA}" --geo-bypass`;
  const yt = 'yt-dlp'; // Use direct yt-dlp command (not python3 -m)
  
  // PO Token plugins auto-load when installed via pip
  // Plugin: bgutil-ytdlp-pot-provider (po_token=web.gvs+XXX)
  // Plugin: yt-dlp-getpot-wpc (fallback)
  
  cmds.push(`${yt} ${proxy} --js-runtimes node --extractor-args "youtube:po_token=web.gvs+;player_client=web" ${fmt} ${opts}`);
  cmds.push(`${yt} ${proxy} --js-runtimes node --extractor-args "youtube:po_token=mweb.gvs+;player_client=mweb" ${fmt} ${opts}`);
  cmds.push(`${yt} ${proxy} --js-runtimes node --extractor-args "youtube:player_client=web_safari" ${fmt} ${opts}`);
  cmds.push(`${yt} ${proxy} --js-runtimes node --extractor-args "youtube:player_client=tv" ${fmt} ${opts}`);
  cmds.push(`${yt} ${proxy} --js-runtimes node ${fmt} ${opts}`);
  cmds.push(`python3 -m yt_dlp ${proxy} --js-runtimes node --extractor-args "youtube:po_token=web.gvs+;player_client=web" ${fmt} ${opts}`);
  cmds.push(`python3 -m yt_dlp ${proxy} --js-runtimes node ${fmt} ${opts}`);
  
  for (let i = 0; i < cmds.length; i++) {
    try {
      const hasProxy = cmds[i].includes('--proxy');
      const hasPOToken = cmds[i].includes('po_token');
      const using = cmds[i].startsWith('yt-dlp') ? 'binary' : 'pip';
      logger.info(`Cmd ${i+1}: ${using}${hasPOToken?' (PO)':''}${hasProxy?' (p)':' (d)'}`);
      
      execSync(cmds[i], { timeout: 180000, maxBuffer: 200*1024*1024, encoding: 'utf8', env });
      
      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000)
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        logger.success(`OK: ${files[0]} (${(fs.statSync(fp).size/1024/1024).toFixed(1)}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
    } catch (e) {
      const err = (e.stderr || e.stdout || e.message || '').toString().substring(0, 100);
      if (!err.includes('Sign in') && !err.includes('HTTP Error 403')) {
        logger.warn(`Cmd ${i+1}: ${err}`);
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
    const result = await downloadVideo(urls[i], outputDir);
    if (result) downloaded.push(result);
  }
  logger.success(`Downloaded ${downloaded.length}/3`);
  return downloaded;
}

module.exports = { downloadVideo, downloadVideos };
