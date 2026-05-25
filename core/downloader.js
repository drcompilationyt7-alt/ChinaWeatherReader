/**
 * Downloader module
 * Uses Shadowsocks + bgutil PO Token plugin for autonomous YouTube downloads.
 * Tests proxy, shows IP, retries across multiple clients.
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

  const env = { ...process.env };
  try { env.PATH = `${path.dirname(process.execPath)}:${env.PATH || ''}`; } catch {}

  // Fix socks5 -> socks5h for DNS through proxy
  let proxy = (process.env.YT_PROXY || '').replace(/^socks5:\/\//, 'socks5h://');
  
  // Test proxy
  if (proxy) {
    try {
      const ip = execSync(`curl -s --connect-timeout 5 --proxy "${proxy}" https://ifconfig.me 2>/dev/null`, { timeout: 10000, encoding: 'utf8' }).trim();
      logger.info(`Proxy IP: ${ip || 'unknown'}`);
    } catch {
      logger.warn('Proxy unreachable, trying without');
      proxy = '';
    }
  }

  const proxyArg = proxy ? `--proxy "${proxy}"` : '';
  
  // Try each client with bgutil PO Token plugin + without
  const strategies = [
    // With PO Token plugin + web client (bgutil auto-generates tokens)
    { name: 'web+PO', args: `--extractor-args "youtube:po_token=web.gvs+;player_client=web"` },
    // With PO Token + mweb client
    { name: 'mweb+PO', args: `--extractor-args "youtube:po_token=mweb.gvs+;player_client=mweb"` },
    // web_embedded (historically no PO needed)
    { name: 'embedded', args: '--extractor-args "youtube:player_client=web_embedded"' },
    // android_vr
    { name: 'android_vr', args: '--extractor-args "youtube:player_client=android_vr"' },
    // Default
    { name: 'default', args: '' },
  ];

  const PY = 'python3 -m yt_dlp';
  const base = `${proxyArg} -f "best[height<=720]" --download-sections "*0-${MAX_DURATION}" -o "${outputFile}" "${url}" --no-playlist --max-filesize 150M --socket-timeout 30 --retries 3 --user-agent "${UA}" --js-runtimes node --force-ipv4`;

  for (const s of strategies) {
    try {
      logger.info(`Try: ${s.name}`);
      execSync(`${PY} ${s.args} ${base}`, { timeout: 180000, maxBuffer: 200*1024*1024, encoding: 'utf8', env });
      
      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000)
        .sort((a,b) => fs.statSync(path.join(outputDir,b)).mtimeMs - fs.statSync(path.join(outputDir,a)).mtimeMs);
      
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        logger.success(`OK! ${files[0]} (${(fs.statSync(fp).size/1024/1024).toFixed(1)}MB)`);
        return { path: fp, title, platform, sourceUrl: url };
      }
    } catch (e) {
      const err = (e.stderr || e.stdout || e.message || '').toString().substring(0, 100);
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
