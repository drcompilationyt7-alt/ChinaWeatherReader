/**
 * Clip Editor - YouTube Shorts Creator v5
 * Smart dimension handling with bitrate cap to avoid huge files.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const axios = require('axios');
const { Logger } = require('./logger');

const logger = new Logger('ClipEditor');
const SHORTS_W = 1080;
const SHORTS_H = 1920;
const TARGET_RATIO = SHORTS_W / SHORTS_H;
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
    const out = execSync(`ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "${videoPath}" 2>/dev/null`, { timeout: 10000, encoding: 'utf8' }).trim();
    const parts = out.split(',').map(s => parseInt(s.trim()));
    if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) return { width: parts[0], height: parts[1] };
  } catch {}
  return null;
}

function buildShortsFilter(srcW, srcH) {
  const ratio = srcW / srcH;
  const diff = Math.abs(ratio - TARGET_RATIO);

  // 9:16 or close — pad to exact target
  if (diff <= TOLERANCE) {
    logger.info(`~9:16 (${srcW}x${srcH}) — padding to ${SHORTS_W}x${SHORTS_H}`);
    return `scale='min(${SHORTS_W},iw)':'min(${SHORTS_H},ih)':force_original_aspect_ratio=decrease,pad=${SHORTS_W}:${SHORTS_H}:(ow-iw)/2:(oh-ih)/2:color=black`;
  }

  // Landscape — scale+center-crop, cap max scale
  logger.info(`Landscape ${srcW}x${srcH} — scaling+crop to ${SHORTS_W}x${SHORTS_H}`);
  const even = (n) => Math.max(2, Math.floor(n / 2) * 2);
  if (ratio >= TARGET_RATIO) {
    const sh = SHORTS_H;
    const sw = even(sh * ratio);
    return `scale=${sw}:${sh}:flags=lanczos,crop=${SHORTS_W}:${SHORTS_H}:${even((sw - SHORTS_W) / 2)}:0`;
  } else {
    const sw = SHORTS_W;
    const sh = even(sw / ratio);
    return `scale=${sw}:${sh}:flags=lanczos,crop=${SHORTS_W}:${SHORTS_H}:0:${even((sh - SHORTS_H) / 2)}`;
  }
}

function probeVideoDuration(videoPath) {
  try {
    const out = execSync(`ffprobe -i "${videoPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`, { timeout: 10000, encoding: 'utf8' }).trim();
    if (out) return parseFloat(out);
  } catch {}
  return null;
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
    const dims = probeVideoDimensions(videoPath);
    const srcW = dims ? dims.width : 720;
    const srcH = dims ? dims.height : 1280;
    logger.info(`Source: ${srcW}x${srcH}`);

    const vf = buildShortsFilter(srcW, srcH);

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

    // CRF for quality — lowered values for better output
    const pixelCount = srcW * srcH;
    const crf = pixelCount < 100000 ? 22 : pixelCount < 300000 ? 20 : 18;

    // Run upscaler if needed (only if source < 480p — truly bad quality)
    let processedVideo = videoPath;
    let upscaled = false;
    if (srcH < 480) {
      try {
        const { upscaleTo1080p } = require('./upscaler');
        const upscaledPath = outputPath.replace('.mp4', '_upscaled_temp.mp4');
        const result = await upscaleTo1080p(videoPath, upscaledPath);
        if (result && fs.existsSync(result) && fs.statSync(result).size > 100000) {
          processedVideo = result;
          upscaled = true;
          logger.info('Using upscaled video');
        }
      } catch (e) {
        logger.warn(`Upscale integration error: ${e.message.substring(0, 80)}`);
      }
    }

    // Re-probe dimensions after upscale
    const finalDims = upscaled ? probeVideoDimensions(processedVideo) : null;
    const finalW = finalDims ? finalDims.width : srcW;
    const finalH = finalDims ? finalDims.height : srcH;
    const finalVf = upscaled ? buildShortsFilter(finalW, finalH) : vf;

    // Check if video would be shorter than voiceover + 1s delay
    let skipVoiceover = false;
    if (voiceoverPath && fs.existsSync(voiceoverPath)) {
      let voDur = 4;
      try {
        const probeOut = execSync(`ffprobe -i "${voiceoverPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`, { timeout: 5000, encoding: 'utf8' }).trim();
        if (probeOut) voDur = Math.ceil(parseFloat(probeOut));
      } catch {}
      voDur = Math.min(voDur, 8);
      // If voiceover + 1s delay > trimmed video duration, skip voiceover but keep flag
      if (voDur + 1 > duration) {
        logger.info(`Voiceover (${voDur}s) + 1s delay exceeds video duration (${duration}s) — skipping voiceover, keeping flag`);
        skipVoiceover = true;
      }
    }

    if (voiceoverPath && fs.existsSync(voiceoverPath) && !skipVoiceover) {
      let voDur = 4;
      try {
        const probeOut = execSync(`ffprobe -i "${voiceoverPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`, { timeout: 5000, encoding: 'utf8' }).trim();
        if (probeOut) voDur = Math.ceil(parseFloat(probeOut));
      } catch {}
      voDur = Math.min(voDur, 8);

      let filterComplex;
      if (hasFlag) {
        filterComplex = `[0:v]${finalVf}[bg];[2:v]scale=100:-1[flag];[bg][flag]overlay=(W-w)/2:160:enable='between(t,0,2.5)'[v];` +
          `[0:a]volume=enable='between(t,1,${1+voDur})':volume=0.1[ad];[1:a]adelay=1000[av];[ad][av]amix=inputs=2:duration=first[a]`;
      } else {
        filterComplex = `[0:v]${finalVf}[v];[0:a]volume=enable='between(t,1,${1+voDur})':volume=0.1[ad];[1:a]adelay=1000[av];[ad][av]amix=inputs=2:duration=first[a]`;
      }
      const inputs = hasFlag ? `-i "${processedVideo}" -i "${voiceoverPath}" -i "${flagFile}"` : `-i "${processedVideo}" -i "${voiceoverPath}"`;
      execSync(`ffmpeg -y -ss ${startTime} ${inputs} -t ${duration} -filter_complex "${filterComplex}" -map "[v]" -map "[a]" -c:v libx264 -preset ultrafast -crf ${crf} -c:a aac -shortest "${outputPath}"`, { timeout: 120000, maxBuffer: 50*1024*1024 });
    } else if (hasFlag) {
      execSync(`ffmpeg -y -ss ${startTime} -i "${processedVideo}" -i "${flagFile}" -t ${duration} -filter_complex "[0:v]${finalVf}[bg];[1:v]scale=100:-1[flag];[bg][flag]overlay=(W-w)/2:160:enable='between(t,0,2.5)'[v]" -map "[v]" -map "[0:a]" -c:v libx264 -preset ultrafast -crf ${crf} -c:a aac -shortest "${outputPath}"`, { timeout: 120000 });
    } else {
      execSync(`ffmpeg -y -ss ${startTime} -i "${processedVideo}" -t ${duration} -vf "${finalVf}" -c:v libx264 -preset ultrafast -crf ${crf} -c:a aac -shortest "${outputPath}"`, { timeout: 120000 });
    }

    try { if (fs.existsSync(flagFile)) fs.unlinkSync(flagFile); } catch {}

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Short: ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB at ${SHORTS_W}x${SHORTS_H} (CRF ${crf})`);
      return outputPath;
    }

    // Fallback: trim copy
    logger.warn('Overlay failed, trim copy...');
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