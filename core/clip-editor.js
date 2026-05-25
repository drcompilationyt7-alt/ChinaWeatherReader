/**
 * Clip Editor - YouTube Shorts Creator
 * 
 * Adds Twemoji flag overlay at start for country context.
 * Downloads flag PNG from Twemoji CDN and overlays it via ffmpeg.
 * Falls back to no overlay if download fails.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const axios = require('axios');
const { Logger } = require('./logger');

const logger = new Logger('ClipEditor');
const SHORTS_W = 1080;
const SHORTS_H = 1920;

// Country code -> Unicode regional indicator hex codes so we can map to Twemoji filenames
function countryToFlagFile(country) {
  const isoMap = {
    'Nigeria':'NG','Japan':'JP','Germany':'DE',
    'Australia':'AU','France':'FR','Brazil':'BR',
    'Thailand':'TH','India':'IN','Mexico':'MX',
    'UK':'GB','South Korea':'KR','Egypt':'EG',
    'Italy':'IT','Spain':'ES','China':'CN',
    'Global':'UN','Indonesia':'ID','Vietnam':'VN'
  };
  const iso = isoMap[country];
  if (!iso) return null;
  // Convert ISO code to unicode regional indicator codepoints (A=1f1e6, B=1f1e7, etc)
  const cp1 = 0x1f1e6 + (iso.charCodeAt(0) - 65);
  const cp2 = 0x1f1e6 + (iso.charCodeAt(1) - 65);
  // Twemoji filename format: e.g. 1f1e7-1f1f7.png for Brazil (BR)
  return `${cp1.toString(16)}-${cp2.toString(16)}.png`;
}

async function createShort(videoPath, options) {
  const outputPath = options.outputPath || videoPath.replace(/\.\w+$/, '_shorts.mp4');
  const tmpDir = path.dirname(outputPath);
  const country = options.countryText || 'Global';
  const voiceoverPath = options.voiceoverPath || null;
  const startTime = Math.min(options.startTime || 5, 30);
  const duration = Math.min(options.duration || 28, 60);

  logger.info(`Creating short for ${country} (${duration}s)`);

  try {
    // Download Twemoji flag PNG if available
    const flagFile = path.join(tmpDir, `flag_${Date.now()}.png`);
    const flagFilename = countryToFlagFile(country);
    let hasFlag = false;

    if (flagFilename) {
      try {
        const url = `https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/${flagFilename}`;
        logger.info(`Downloading flag: ${url}`);
        const response = await axios({ method: 'GET', url, responseType: 'stream', timeout: 10000 });
        const writer = fs.createWriteStream(flagFile);
        response.data.pipe(writer);
        await new Promise((resolve, reject) => {
          writer.on('finish', resolve);
          writer.on('error', reject);
        });
        if (fs.existsSync(flagFile) && fs.statSync(flagFile).size > 100) {
          hasFlag = true;
          logger.success(`Flag downloaded: ${flagFilename}`);
        }
      } catch (e) {
        logger.warn(`Flag download failed: ${e.message.substring(0, 60)}`);
      }
    }

    const padFilter = `scale='min(${SHORTS_W},iw)':'min(${SHORTS_H},ih)':force_original_aspect_ratio=decrease,pad=${SHORTS_W}:${SHORTS_H}:(ow-iw)/2:(oh-ih)/2:color=black`;

    if (voiceoverPath && fs.existsSync(voiceoverPath)) {
      // Voiceover duration measurement
      let voDur = 4;
      try {
        const probeOut = execSync(
          `ffprobe -i "${voiceoverPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`,
          { timeout: 5000, encoding: 'utf8' }
        ).trim();
        if (probeOut) voDur = Math.ceil(parseFloat(probeOut));
      } catch {}
      voDur = Math.min(voDur, 8);

      // Build filter: pad + flag overlay for first 3s + voiceover mix
      let filterComplex;
      if (hasFlag) {
        flagFile.replace(/\\/g, '\\\\');
        filterComplex = `[0:v]${padFilter}[bg];` +
          `[2:v]scale=120:-1[flag];` +
          `[bg][flag]overlay=(W-w)/2:180:enable='between(t,0,2.5)'[v];` +
          `[0:a]volume=enable='between(t,1,${1+voDur})':volume=0.1[ad];` +
          `[1:a]adelay=1000[av];` +
          `[ad][av]amix=inputs=2:duration=first[a]`;
      } else {
        filterComplex = `[0:v]${padFilter}[v];` +
          `[0:a]volume=enable='between(t,1,${1+voDur})':volume=0.1[ad];` +
          `[1:a]adelay=1000[av];` +
          `[ad][av]amix=inputs=2:duration=first[a]`;
      }

      const inputs = hasFlag ? `-i "${videoPath}" -i "${voiceoverPath}" -i "${flagFile}"` : `-i "${videoPath}" -i "${voiceoverPath}"`;
      execSync(
        `ffmpeg -y -ss ${startTime} ${inputs} -t ${duration} ` +
        `-filter_complex "${filterComplex}" ` +
        `-map "[v]" -map "[a]" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${outputPath}"`,
        { timeout: 120000, maxBuffer: 50*1024*1024, encoding: 'utf8' }
      );
    } else {
      // No voiceover
      let filterComplex;
      if (hasFlag) {
        filterComplex = `[0:v]${padFilter}[bg];` +
          `[1:v]scale=120:-1[flag];` +
          `[bg][flag]overlay=(W-w)/2:180:enable='between(t,0,2.5)'[v]`;
      } else {
        filterComplex = padFilter;
      }

      execSync(
        `ffmpeg -y -ss ${startTime} -i "${videoPath}"${hasFlag ? ` -i "${flagFile}"` : ''} -t ${duration} ` +
        `-filter_complex "${filterComplex}" ` +
        `-map "[v]" -map "[0:a]" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -b:a 128k -shortest "${outputPath}"`,
        { timeout: 120000, maxBuffer: 50*1024*1024, encoding: 'utf8' }
      );
    }

    // Cleanup flag file
    try { if (fs.existsSync(flagFile)) fs.unlinkSync(flagFile); } catch {}

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Short: ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB`);
      return outputPath;
    }

    // Fallback: trim only
    logger.warn('Pad/overlay failed, trying trim-only...');
    execSync(
      `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} -c copy "${outputPath}"`,
      { timeout: 60000 }
    );
    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Short (trim only): ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB`);
      return outputPath;
    }

  } catch (error) {
    const errMsg = error.stderr || error.stdout || error.message || '';
    logger.warn(`FFMPEG ERROR: ${errMsg.toString().substring(0, 300)}`);
  }
  return null;
}

module.exports = { createShort };
