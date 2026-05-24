/**
 * Downloader module
 * Uses python3 yt-dlp v2026.3.17 with PO Token plugins.
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
  
  // Build env with Node.js in PATH
  const env = { ...process.env };
  try { env.PATH = `${path.dirname(process.execPath)}:${env.PATH || ''}`; } catch {}

  // Find bgutil plugin location
  let pluginDir = '';
  try {
    const bgPath = execSync('python3 -c "import bgutil_ytdlp_pot_provider; print(bgutil_ytdlp_pot_provider.__path__[0])"', { timeout: 5000, encoding: 'utf8' }).trim();
    if (bgPath) pluginDir = `--plugin-dirs "${path.dirname(bgPath)}"`;
  } catch {}

  // Strategies: try multiple clients with PO Token
  const PY = 'python3 -m yt_dlp';
  const fmt = '-f "best[height<=720]"';
  const opts = `--download-sections "*0-${MAX_DURATION}" --force-keyframes-at-cuts -o "${outputFile}" "${url}" --no-playlist --max-filesize 150M --socket-timeout 20 --retries 2 --user-agent "${UA}" --geo-bypass`;
  
  const cmds = [];
  
  // With bgutil PO Token plugin (autonomous token generation)
  const plug = pluginDir || '';
  
  // Try each client with PO Token support
  for (const client of ['web', 'mweb', 'web_safari', 'tv', 'android']) {
    cmds.push(`${PY} ${proxy} ${plug} --js-runtimes node --extractor-args "youtube:po_token=${client}.gvs+;player_client=${client}" ${fmt} ${opts}`);
  }
  // Without PO Token (fallback)
  cmds.push(`${PY} ${proxy} ${plug} --js-runtimes node --extractor-args "youtube:skip=webpage" ${fmt} ${opts}`);
  cmds.push(`${PY} ${proxy} ${plug} --js-runtimes node ${fmt} ${opts}`);
  
  for (let i = 0; i < cmds.length; i++) {
    try {
      const client = cmds[i].match(/player_client=(\w+)/)?.[1] || 'none';
      const hasPOToken = cmds[i].includes('po_token');
      logger.info(`Cmd ${i+1}: ${client}${hasPOToken ? ' (PO)' : ''}`);
      
      execSync(cmds[i], { timeout: 120000, maxBuffer: 200*1024*1024, encoding: 'utf8', env });
      
      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000)
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        logger.success(`OK: ${files[0]} (${(fs.statSync(fp).size/1024/1024).toFixed(1)}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
    } catch (e) {
      const err = (e.stderr || e.stdout || e.message || '').toString().substring(0, 80);
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
