/**
 * Clip Editor - YouTube Shorts Creator v5
 * 
 * Smart dimension handling:
 * - If source is 9:16 portrait (within 5% tolerance): stream copy, no re-encode
 * - If landscape (16:9, 4:3, etc): center-crop 9:16 slice, scale to 1080x1920
 * - Always adds flag overlay + voiceover if provided
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const axios = require('axios');
const { Logger } = require('./logger');

const logger = new Logger('ClipEditor');
const SHORTS_W = 1080;
const SHORTS_H = 1920;
const TARGET_RATIO = SHORTS_W / SHORTS_H; // 0.5625
const TOLERANCE = 0.05;

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
  const cp1 = 0x1f1e6 + (iso.charCodeAt(0) - 65);
  const cp2 = 0x1f1e6 + (iso.charCodeAt(1) - 65);
  return `${cp1.toString(16)}-${cp2.toString(16)}.png`;
}

function probeVideoDimensions(videoPath) {
  try {
    const out = execSync(
      `ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "${videoPath}" 2>/dev/null`,
      { timeout: 10000, encoding: 'utf8' }
    ).trim();
    const parts = out.split(',').map(s => parseInt(s.trim()));
    if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
      return { width: parts[0], height: parts[1] };
    }
  } catch {}
  return null;
}

/**
 * Build ffmpeg video filter to make any video fit 1080x1920 shorts format.
 * - 9:16 portrait: just pad to exact dimensions (no crop, no rescale)
 * - Landscape/other: scale+center-crop to fill shorts frame
 */
function buildShortsFilter(srcW, srcH) {
  const ratio = srcW / srcH;
  const diff = Math.abs(ratio - TARGET_RATIO);

  // Already 9:16 or close — just pad to exact target
  if (diff <= TOLERANCE) {
    logger.info(`Already ~9:16 (${srcW}x${srcH}) — padding to ${SHORTS_W}x${SHORTS_H}`);
    return `scale='min(${SHORTS_W},iw)':'min(${SHORTS_H},ih)':force_original_aspect_ratio=decrease,pad=${SHORTS_W}:${SHORTS_H}:(ow-iw)/2:(oh-ih)/2:color=black`;
  }

  // Landscape or other — scale to fill then center-crop
  logger.info(`Landscape/other ${srcW}x${srcH} — scaling+center-crop to ${SHORTS_W}x${SHORTS_H}`);
  const even = (n) => Math.max(2, Math.floor(n / 2) * 2);
  let sw, sh, cw, ch;
  if (ratio >= TARGET_RATIO) {
    // Wider than 9:16: scale height to fill, crop width
    sh = SHORTS_H;
    sw = even(sh * ratio);
    cw = SHORTS_W;
    ch = SHORTS_H;
    return `scale=${sw}:${sh}:flags=lanczos,crop=${cw}:${ch}:${even((sw - cw) / 2)}:0`;
  } else {
    // Taller than 9:16: scale width to fill, crop height
    sw = SHORTS_W;
    sh = even(sw / ratio);
    cw = SHORTS_W;
    ch = SHORTS_H;
    return `scale=${sw}:${sh}:flags=lanczos,crop=${cw}:${ch}:0:${even((sh - ch) / 2)}`;
  }
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
    // Probe dimensions
    const dims = probeVideoDimensions(videoPath);
    const srcW = dims ? dims.width : 720;
    const srcH = dims ? dims.height : 1280;
    logger.info(`Source: ${srcW}x${srcH} (ratio: ${(srcW/srcH).toFixed(3)})`);

    // Build the filter
    const vf = buildShortsFilter(srcW, srcH);
    logger.info(`Filter: ${vf}`);

    // Download flag
    const flagFile = path.join(tmpDir, `flag_${Date.now()}.png`);
    const flagFilename = countryToFlagFile(country);
    let hasFlag = false;

    if (flagFilename) {
      try {
        const url = `https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/${flagFilename}`;
        const response = await axios({ method: 'GET', url, responseType: 'stream', timeout: 10000 });
        const writer = fs.createWriteStream(flagFile);
        response.data.pipe(writer);
        await new Promise((resolve, reject) => { writer.on('finish', resolve); writer.on('error', reject); });
        if (fs.existsSync(flagFile) && fs.statSync(flagFile).size > 100) hasFlag = true;
      } catch {}
    }

    // For portrait 9:16 with no overlay/voiceover, just stream copy
    const ratio = srcW / srcH;
    const isPortraitShort = Math.abs(ratio - TARGET_RATIO) <= TOLERANCE;

    if (isPortraitShort && !hasFlag && !voiceoverPath) {
      // Already a proper short — stream copy (fastest)
      logger.info('Already proper short, no flag/voiceover — stream copy');
      execSync(`ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} -c copy "${outputPath}"`, { timeout: 60000 });
      if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
        logger.success(`Short (stream copy): ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB`);
        return outputPath;
      }
    }

    if (voiceoverPath && fs.existsSync(voiceoverPath)) {
      let voDur = 4;
      try {
        const probeOut = execSync(`ffprobe -i "${voiceoverPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`, { timeout: 5000, encoding: 'utf8' }).trim();
        if (probeOut) voDur = Math.ceil(parseFloat(probeOut));
      } catch {}
      voDur = Math.min(voDur, 8);

      let filterComplex;
      if (hasFlag) {
        filterComplex = `[0:v]${vf}[bg];[2:v]scale=120:-1[flag];[bg][flag]overlay=(W-w)/2:180:enable='between(t,0,2.5)'[v];` +
          `[0:a]volume=enable='between(t,1,${1+voDur})':volume=0.1[ad];[1:a]adelay=1000[av];[ad][av]amix=inputs=2:duration=first[a]`;
      } else {
        filterComplex = `[0:v]${vf}[v];[0:a]volume=enable='between(t,1,${1+voDur})':volume=0.1[ad];[1:a]adelay=1000[av];[ad][av]amix=inputs=2:duration=first[a]`;
      }

      const inputs = hasFlag ? `-i "${videoPath}" -i "${voiceoverPath}" -i "${flagFile}"` : `-i "${videoPath}" -i "${voiceoverPath}"`;
      execSync(`ffmpeg -y -ss ${startTime} ${inputs} -t ${duration} -filter_complex "${filterComplex}" -map "[v]" -map "[a]" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${outputPath}"`, { timeout: 120000, maxBuffer: 50*1024*1024 });
    } else if (hasFlag) {
      execSync(`ffmpeg -y -ss ${startTime} -i "${videoPath}" -i "${flagFile}" -t ${duration} -filter_complex "[0:v]${vf}[bg];[1:v]scale=120:-1[flag];[bg][flag]overlay=(W-w)/2:180:enable='between(t,0,2.5)'[v]" -map "[v]" -map "[0:a]" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${outputPath}"`, { timeout: 120000 });
    } else {
      // Needs scaling but no overlay/voiceover
      execSync(`ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} -vf "${vf}" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${outputPath}"`, { timeout: 120000 });
    }

    // Cleanup
    try { if (fs.existsSync(flagFile)) fs.unlinkSync(flagFile); } catch {}

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Short: ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB at ${SHORTS_W}x${SHORTS_H}`);
      return outputPath;
    }

    // Fallback: trim only
    logger.warn('Scaling failed, trying trim copy...');
    execSync(`ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} -c copy "${outputPath}"`, { timeout: 60000 });
    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Short (trim): ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB`);
      return outputPath;
    }

  } catch (error) {
    const errMsg = error.stderr || error.stdout || error.message || '';
    logger.warn(`FFMPEG ERROR: ${errMsg.toString().substring(0, 300)}`);
  }
  return null;
}

module.exports = { createShort };
