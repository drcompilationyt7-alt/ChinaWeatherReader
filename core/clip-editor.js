/**
 * Clip Editor - YouTube Shorts Creator v6
 * Smart crop (no black bars), AI crop optimization, credits detection
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

function probeVideoDuration(videoPath) {
  try {
    const out = execSync(`ffprobe -i "${videoPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`, { timeout: 10000, encoding: 'utf8' }).trim();
    if (out) return parseFloat(out);
  } catch {}
  return null;
}

/**
 * Builds ffmpeg filter string for shorts (no black bars).
 * For 9:16: scale UP to fill, then crop (no padding).
 * For landscape: center crop.
 * Accepts optional cropX override for smart crop.
 */
function buildShortsFilter(srcW, srcH, cropOffsetX) {
  const ratio = srcW / srcH;
  const diff = Math.abs(ratio - TARGET_RATIO);
  const even = (n) => Math.max(2, Math.floor(n / 2) * 2);

  // 9:16 or close — scale up to fill, then crop (no black bars)
  if (diff <= TOLERANCE) {
    logger.info(`~9:16 (${srcW}x${srcH}) — scale+crop to ${SHORTS_W}x${SHORTS_H} (no padding)`);
    // Use force_original_aspect_ratio=increase to scale UP filling 1080x1920, then center crop
    return `scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H}`;
  }

  // Landscape — scale+center-crop
  logger.info(`Landscape ${srcW}x${srcH} — scaling+crop to ${SHORTS_W}x${SHORTS_H}`);
  if (ratio >= TARGET_RATIO) {
    const sh = SHORTS_H;
    const sw = even(sh * ratio);
    // Use cropOffsetX if provided (smart crop), otherwise center
    const cropX = typeof cropOffsetX === 'number' ? even(Math.max(0, Math.min(sw - SHORTS_W, cropOffsetX))) : even((sw - SHORTS_W) / 2);
    return `scale=${sw}:${sh}:flags=lanczos,crop=${SHORTS_W}:${SHORTS_H}:${cropX}:0`;
  } else {
    const sw = SHORTS_W;
    const sh = even(sw / ratio);
    return `scale=${sw}:${sh}:flags=lanczos,crop=${SHORTS_W}:${SHORTS_H}:0:${even((sh - SHORTS_H) / 2)}`;
  }
}

/**
 * AI Smart Crop Optimization
 * Extracts 3 frames, applies the crop, and asks OpenRouter if the crop is good.
 * Returns adjusted cropX offset in pixels, or null if no adjustment needed.
 */
async function optimizeCrop(videoPath, srcW, srcH, startTime, duration) {
  // Only optimize landscape videos
  const ratio = srcW / srcH;
  if (ratio <= TARGET_RATIO) return null; // not landscape, no horizontal crop to optimize

  const tmpDir = path.dirname(videoPath);
  const cropDir = path.join(tmpDir, `crop_check_${Date.now()}`);
  fs.mkdirSync(cropDir, { recursive: true });

  try {
    // Extract 3 frames evenly spaced
    const interval = duration / 4; // at 25%, 50%, 75%
    const cropPositions = [];
    
    for (let i = 1; i <= 3; i++) {
      const timePos = startTime + interval * i;
      const rawFrame = path.join(cropDir, `frame_${i}_raw.jpg`);
      const croppedFrame = path.join(cropDir, `frame_${i}_cropped.jpg`);
      
      // Extract raw frame
      execSync(`ffmpeg -y -ss ${timePos.toFixed(1)} -i "${videoPath}" -vframes 1 -q:v 3 "${rawFrame}" 2>/dev/null`, { timeout: 30000 });
      if (!fs.existsSync(rawFrame)) continue;
      
      // Calculate center crop for this aspect ratio
      const even = (n) => Math.max(2, Math.floor(n / 2) * 2);
      const sh = SHORTS_H;
      const sw = even(sh * ratio);
      const cropX = even((sw - SHORTS_W) / 2);
      
      // Scale and apply the crop to see what it looks like
      execSync(
        `ffmpeg -y -i "${rawFrame}" -vf "scale=${sw}:${sh}:flags=lanczos,crop=${SHORTS_W}:${SHORTS_H}:${cropX}:0" -q:v 3 "${croppedFrame}" 2>/dev/null`,
        { timeout: 30000 }
      );
      
      if (fs.existsSync(croppedFrame)) {
        cropPositions.push({ rawFrame, croppedFrame, cropX, timePos });
      }
    }

    if (cropPositions.length === 0) return null;

    // Send to OpenRouter owl-alpha for analysis (text only — describe the frames)
    const description = cropPositions.map((cp, i) => {
      const dims = probeVideoDimensions(cp.rawFrame);
      return `Frame ${i+1} at ${cp.timePos.toFixed(1)}s: Original ${dims ? dims.width+'x'+dims.height : 'unknown'}, crop centered horizontally at x=${cp.cropX}px.`;
    }).join('\n');

    try {
      const response = await axios.post(
        'https://openrouter.ai/api/v1/chat/completions',
        {
          model: 'openrouter/owl-alpha',
          messages: [
            { role: 'system', content: 'You analyze video crop positions. Respond ONLY with valid JSON.' },
            { role: 'user', content: `A landscape video (${srcW}x${srcH}) is being center-cropped to 1080x1920 portrait for YouTube Shorts.\n\n${description}\n\nIs the subject well-framed in the crop? Should the crop shift left or right? Consider that people/faces/action should be centered.\n\nReply ONLY with JSON:\n{"shift":"left"/"right"/"none","pixels":N,"reason":"brief reason"}\nWhere pixels is how many pixels to shift (50-200 range).` }
          ],
          max_tokens: 100,
          temperature: 0.3,
        },
        {
          headers: {
            'Authorization': `Bearer ${process.env.OPENROUTER_API_KEY || ''}`,
            'Content-Type': 'application/json',
          },
          timeout: 15000,
        }
      );

      if (response.data?.choices?.[0]?.message?.content) {
        const text = response.data.choices[0].message.content;
        const jsonMatch = text.match(/\{[\s\S]*\}/);
        if (jsonMatch) {
          const result = JSON.parse(jsonMatch[0]);
          if (result.shift === 'left') {
            const pixels = Math.min(200, Math.max(20, result.pixels || 50));
            logger.info(`AI smart crop: shift LEFT by ${pixels}px — ${result.reason || ''}`);
            return pixels; // positive = shift left
          } else if (result.shift === 'right') {
            const pixels = Math.min(200, Math.max(20, result.pixels || 50));
            logger.info(`AI smart crop: shift RIGHT by ${pixels}px — ${result.reason || ''}`);
            return -pixels; // negative = shift right
          } else {
            logger.info(`AI smart crop: center is fine — ${result.reason || ''}`);
            return 0;
          }
        }
      }
    } catch (e) {
      logger.warn(`AI crop optimization error: ${e.message.substring(0, 60)}`);
    }
    return null;
  } finally {
    try { fs.rmSync(cropDir, { recursive: true, force: true }); } catch {}
  }
}

/**
 * Detect credits/watermarks at the end of the video.
 * Uses PaddleOCR on last frames, then AI to classify text.
 * Returns suggested end time in seconds, or null if no credits.
 */
async function detectCredits(videoPath, totalDuration, startTime) {
  const tmpDir = path.dirname(videoPath);
  const creditDir = path.join(tmpDir, `credit_check_${Date.now()}`);
  fs.mkdirSync(creditDir, { recursive: true });

  try {
    const segStart = startTime || 0;
    const segEnd = segStart + Math.min(totalDuration - segStart, 35);
    
    // Extract last 3 seconds worth of frames (3 frames)
    const framePaths = [];
    for (let i = 0; i < 3; i++) {
      const timePos = Math.max(segStart, segEnd - 3 + i);
      const fp = path.join(creditDir, `end_frame_${i}.jpg`);
      execSync(`ffmpeg -y -ss ${timePos.toFixed(1)} -i "${videoPath}" -vframes 1 -q:v 2 "${fp}" 2>/dev/null`, { timeout: 30000 });
      if (fs.existsSync(fp) && fs.statSync(fp).size > 1000) framePaths.push(fp);
    }

    if (framePaths.length === 0) return null;

    // Run PaddleOCR on the frames (fast, no API call)
    let ocrTexts = [];
    try {
      for (const fp of framePaths) {
        const pyPath = fp.replace(/\\/g, '\\\\');
        const output = execSync(
          `python3 -c "
import json
try:
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False, use_gpu=False)
    result = ocr.ocr('${pyPath}', cls=True)
    texts = []
    if result and result[0]:
        for line in result[0]:
            texts.append(line[1][0])
    print(json.dumps({'texts': texts}))
except Exception as e:
    print(json.dumps({'texts': []}))
" 2>&1`,
          { timeout: 30000, encoding: 'utf8', maxBuffer: 5*1024*1024 }
        ).toString().trim();
        
        const lines = output.split('\n').filter(l => l.startsWith('{'));
        if (lines.length > 0) {
          const parsed = JSON.parse(lines[lines.length - 1]);
          if (parsed.texts && parsed.texts.length > 0) ocrTexts.push(...parsed.texts);
        }
      }
    } catch {}

    if (ocrTexts.length === 0) return null;

    const combinedText = [...new Set(ocrTexts)].join(', ');
    logger.info(`OCR detected in end frames: "${combinedText.substring(0, 80)}"`);

    // Ask OpenRouter owl-alpha if this is credits/watermark
    try {
      const response = await axios.post(
        'https://openrouter.ai/api/v1/chat/completions',
        {
          model: 'openrouter/owl-alpha',
          messages: [
            { role: 'system', content: 'You detect video credits/watermarks. Respond ONLY with valid JSON.' },
            { role: 'user', content: `Text detected in last frames of a video: "${combinedText.substring(0, 200)}"\n\nIs this a credit, watermark, app name, or subscribe prompt? (e.g. "TikTok", "CapCut", "Subscribe", "@username", "Follow me")\n\nReply ONLY with JSON:\n{"is_credit":true/false,"reason":"short reason"}` }
          ],
          max_tokens: 50,
          temperature: 0.1,
        },
        {
          headers: {
            'Authorization': `Bearer ${process.env.OPENROUTER_API_KEY || ''}`,
            'Content-Type': 'application/json',
          },
          timeout: 10000,
        }
      );

      if (response.data?.choices?.[0]?.message?.content) {
        const text = response.data.choices[0].message.content;
        const jsonMatch = text.match(/\{[\s\S]*\}/);
        if (jsonMatch) {
          const result = JSON.parse(jsonMatch[0]);
          if (result.is_credit) {
            // Credits detected — find where they start by scanning backward
            logger.info(`Credits detected: "${combinedText.substring(0, 50)}" — ${result.reason || ''}`);
            
            // Scan backward in 1s increments from end to find credit start
            let creditStart = segEnd - 3;
            for (let t = segEnd - 4; t >= segStart; t -= 1) {
              try {
                const checkFrame = path.join(creditDir, `check_${t}.jpg`);
                execSync(`ffmpeg -y -ss ${t.toFixed(1)} -i "${videoPath}" -vframes 1 -q:v 2 "${checkFrame}" 2>/dev/null`, { timeout: 10000 });
                if (!fs.existsSync(checkFrame)) continue;
                
                const pyPath2 = checkFrame.replace(/\\/g, '\\\\');
                const checkOutput = execSync(
                  `python3 -c "
import json
try:
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False, use_gpu=False)
    result = ocr.ocr('${pyPath2}', cls=True)
    texts = []
    if result and result[0]:
        for line in result[0]:
            texts.append(line[1][0])
    print(json.dumps({'has_text': len(texts) > 0}))
except:
    print(json.dumps({'has_text': False}))
" 2>&1`,
                  { timeout: 15000, encoding: 'utf8', maxBuffer: 2*1024*1024 }
                ).toString().trim();
                
                const checkLines = checkOutput.split('\n').filter(l => l.startsWith('{'));
                if (checkLines.length > 0) {
                  const check = JSON.parse(checkLines[checkLines.length - 1]);
                  if (!check.has_text) {
                    creditStart = t + 1;
                    break;
                  }
                }
              } catch {}
            }
            
            const adjustedDuration = Math.max(5, creditStart - segStart);
            logger.info(`Credits start at ~${creditStart.toFixed(1)}s — trimming to ${adjustedDuration.toFixed(1)}s`);
            return adjustedDuration;
          }
        }
      }
    } catch {}
    
    return null;
  } finally {
    try { fs.rmSync(creditDir, { recursive: true, force: true }); } catch {}
  }
}

async function createShort(videoPath, options) {
  const outputPath = options.outputPath || videoPath.replace(/\.\w+$/, '_shorts.mp4');
  const tmpDir = path.dirname(outputPath);
  const country = options.countryText || 'Global';
  const voiceoverPath = options.voiceoverPath || null;
  const startTime = Math.min(options.startTime || 5, 30);
  let duration = Math.min(options.duration || 28, 60);

  logger.info(`Creating short for ${country} (${duration}s)`);

  try {
    const dims = probeVideoDimensions(videoPath);
    const srcW = dims ? dims.width : 720;
    const srcH = dims ? dims.height : 1280;
    logger.info(`Source: ${srcW}x${srcH}`);

    // Check for credits at end of video
    try {
      const creditResult = await detectCredits(videoPath, duration + startTime, startTime);
      if (creditResult !== null && creditResult < duration) {
        logger.info(`Credits detected — reducing duration from ${duration}s to ${creditResult.toFixed(1)}s`);
        duration = Math.max(5, Math.floor(creditResult));
      }
    } catch (e) {
      logger.warn(`Credit check error: ${e.message.substring(0, 60)}`);
    }

    // AI smart crop optimization
    let cropOffsetX = 0;
    try {
      const cropResult = await optimizeCrop(videoPath, srcW, srcH, startTime, duration);
      if (cropResult !== null) cropOffsetX = cropResult;
    } catch (e) {
      logger.warn(`Crop optimization error: ${e.message.substring(0, 60)}`);
    }

    const vf = buildShortsFilter(srcW, srcH, cropOffsetX);

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

    // CRF for quality
    const pixelCount = srcW * srcH;
    const crf = pixelCount < 100000 ? 22 : pixelCount < 300000 ? 20 : 18;

    // Run upscaler if needed (only if source < 480p)
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
    const finalVf = upscaled ? buildShortsFilter(finalW, finalH, cropOffsetX) : vf;

    // Check if video shorter than voiceover + 1s delay
    let skipVoiceover = false;
    if (voiceoverPath && fs.existsSync(voiceoverPath)) {
      let voDur = 4;
      try {
        const probeOut = execSync(`ffprobe -i "${voiceoverPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`, { timeout: 5000, encoding: 'utf8' }).trim();
        if (probeOut) voDur = Math.ceil(parseFloat(probeOut));
      } catch {}
      voDur = Math.min(voDur, 8);
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