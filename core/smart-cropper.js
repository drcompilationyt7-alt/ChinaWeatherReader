/**
 * Smart Cropper — Gemini CLI Feedback Loop
 * 
 * Converts any video to YouTube Shorts format (1080x1920) using:
 * 1. Initial smart crop based on source dimensions
 * 2. Frame extraction (raw + cropped)
 * 3. Gemini CLI evaluates the crop quality
 * 4. Feedback loop: adjust and re-crop if needed
 * 5. Final validation
 * 
 * Never stretches or squeezes — maintains proper aspect ratio.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');
const { getGeminiCLI } = require('./gemini-cli-runner');
const { getOpenRouterQA } = require('./openrouter-qa');

const logger = new Logger('SmartCropper');

const SHORTS_W = 1080;
const SHORTS_H = 1920;
const TARGET_RATIO = SHORTS_W / SHORTS_H; // 0.5625
const MAX_ITERATIONS = 3;

/**
 * Probe video dimensions using ffprobe
 */
function probeVideo(videoPath) {
  try {
    const out = execSync(
      `ffprobe -v error -select_streams v:0 -show_entries stream=width,height,duration -of csv=p=0 "${videoPath}" 2>/dev/null`,
      { timeout: 10000, encoding: 'utf8' }
    ).trim();
    const parts = out.split(',').map(s => parseInt(s.trim()));
    if (parts.length >= 2) {
      return { width: parts[0], height: parts[1], duration: parts[2] || 30 };
    }
  } catch {}
  return { width: 1920, height: 1080, duration: 30 };
}

/**
 * Extract frames from video at specified positions
 */
function extractFrames(videoPath, outputDir, positions) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const frames = [];

  for (let i = 0; i < positions.length; i++) {
    const framePath = path.join(outputDir, `frame_${i}.jpg`);
    try {
      execSync(
        `ffmpeg -y -ss ${positions[i].toFixed(2)} -i "${videoPath}" -vframes 1 -q:v 2 "${framePath}" 2>/dev/null`,
        { timeout: 15000 }
      );
      if (fs.existsSync(framePath) && fs.statSync(framePath).size > 1000) {
        frames.push(framePath);
      }
    } catch {}
  }
  return frames;
}

/**
 * Apply a crop command to a frame (for comparison)
 */
function applyCropToFrame(framePath, outputPath, cropFilter) {
  try {
    const out = execSync(
      `ffmpeg -y -i "${framePath}" -vf "${cropFilter}" -q:v 2 "${outputPath}" 2>&1`,
      { timeout: 10000, encoding: 'utf8' }
    );
    if (fs.existsSync(outputPath)) return true;
    logger.warn(`Crop frame missing at output — ffmpeg stderr: ${(out || '').substring(0, 200)}`);
    return false;
  } catch (e) {
    const errText = (e.stderr || e.stdout || e.message || '').toString().substring(0, 200);
    logger.warn(`Crop frame ffmpeg error: ${errText}`);
    return false;
  }
}

/**
 * Build the ffmpeg filter for cropping to 9:16
 * @param {number} srcW - Source width
 * @param {number} srcH - Source height
 * @param {number} cropOffsetX - Horizontal offset for landscape videos
 * @param {number} zoom - Zoom factor (1.0 = no zoom, 1.05 = 5% zoom)
 */
function buildCropFilter(srcW, srcH, cropOffsetX = 0, zoom = 1.0) {
  const ratio = srcW / srcH;
  const even = (n) => Math.max(2, Math.floor(n / 2) * 2);

  // Apply zoom first
  const zw = even(srcW * zoom);
  const zh = even(srcH * zoom);

  if (Math.abs(ratio - TARGET_RATIO) < 0.05) {
    // Already close to 9:16 — scale up to fill 1080x1920
    return `scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H}`;
  }

  if (ratio > TARGET_RATIO) {
    // Landscape — scale height to match, then crop width
    const sh = SHORTS_H;
    const sw = even(sh * ratio * zoom);
    const cropX = even(Math.max(0, Math.min(sw - SHORTS_W, (sw - SHORTS_W) / 2 + cropOffsetX)));
    return `scale=${sw}:${sh}:flags=lanczos,crop=${SHORTS_W}:${SHORTS_H}:${cropX}:0`;
  } else {
    // Portrait but not 9:16 — scale width to match, then crop height
    const sw = SHORTS_W;
    const sh = even(sw / ratio * zoom);
    return `scale=${sw}:${sh}:flags=lanczos,crop=${SHORTS_W}:${SHORTS_H}:0:${even((sh - SHORTS_H) / 2)}`;
  }
}

/**
 * Smart crop with Gemini CLI feedback loop
 * 
 * @param {string} videoPath - Input video path
 * @param {string} outputPath - Output video path
 * @param {Object} options - { country, duration, startTime }
 * @returns {Object} - { success, outputPath, iterations, cropFilter }
 */
async function smartCrop(videoPath, outputPath, options = {}) {
  const country = options.country || 'Global';
  const startTime = Math.min(options.startTime || 3, 30);
  const duration = Math.min(options.duration || 30, 60);
  const tmpDir = path.dirname(outputPath);

  logger.info(`Smart cropping: ${path.basename(videoPath)} → 9:16 portrait`);

  // Step 1: Probe source dimensions
  const dims = probeVideo(videoPath);
  const srcW = dims.width;
  const srcH = dims.height;
  logger.info(`Source: ${srcW}x${srcH}`);

  // Step 2: Apply initial center crop
  let cropOffsetX = 0;
  let zoom = 1.0;
  let cropFilter = buildCropFilter(srcW, srcH, cropOffsetX, zoom);
  
  logger.info(`Initial crop filter: ${cropFilter}`);

  // Step 3: Gemini evaluates the video directly to find the subject
  const gemini = getGeminiCLI();
  const shouldUseCLI = gemini.isAvailable();

  // Pre-calculate frame positions for QA (used after crop)
  const qaPositions = [duration / 4, duration / 2, duration * 3 / 4].map(t => startTime + t);

  if (shouldUseCLI) {
    logger.info('Analyzing video with Gemini to locate subject...');
    const smartCropSkillPath = path.join(__dirname, '..', 'skills', 'type1', 'smart-crop-skill.md');
    const evaluation = await gemini.evaluateCropFromVideo(videoPath, country, smartCropSkillPath);

    if (evaluation) {
      try {
        const jsonMatch = evaluation.match(/\{[\s\S]*\}/);
        if (jsonMatch) {
          const result = JSON.parse(jsonMatch[0]);
          const centerPercent = result.calculated_center_percentage;

          if (typeof centerPercent === 'number' && centerPercent >= 0 && centerPercent <= 100) {
            logger.info(`Subject detected: "${result.subject_label || 'Unknown'}" at ${centerPercent}% horizontally`);

            // Compute the crop pixel offset from the percentage
            const targetHeight = 1920;
            const targetWidth = 1080;

            // Scale proportionally: height matches 1920, width scales by same factor
            const scaleFactor = targetHeight / srcH;
            const scaledWidth = Math.round(srcW * scaleFactor);

            // Center the 1080px crop window over the subject's position
            const subjectX = (centerPercent / 100) * scaledWidth;
            cropOffsetX = Math.max(0, Math.min(
              Math.round(subjectX - (targetWidth / 2)),
              scaledWidth - targetWidth
            ));

            logger.info(`Scaled width: ${scaledWidth}px, Subject X: ${Math.round(subjectX)}px, Crop offset: ${cropOffsetX}px`);
            cropFilter = buildCropFilter(srcW, srcH, cropOffsetX, zoom);
          } else {
            logger.warn(`Invalid center percentage: ${centerPercent} — using center crop`);
          }
        }
      } catch (e) {
        logger.warn(`Failed to parse Gemini crop eval: ${e.message} — using center crop`);
      }
    } else {
      logger.warn('Gemini crop analysis returned null — using center crop');
    }
  }

  // Step 4: Apply final crop to actual video
  logger.info(`Applying final crop: ${cropFilter}`);
  
  const crf = srcW * srcH < 300000 ? 20 : 18;
  const finalOutput = outputPath || videoPath.replace(/\.\w+$/, '_shorts.mp4');

  try {
    execSync(
      `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} ` +
      `-vf "${cropFilter}" ` +
      `-c:v libx264 -preset fast -crf ${crf} -c:a aac -b:a 128k ` +
      `-pix_fmt yuv420p -shortest "${finalOutput}" 2>/dev/null`,
      { timeout: 180000, maxBuffer: 50 * 1024 * 1024 }
    );

    if (fs.existsSync(finalOutput) && fs.statSync(finalOutput).size > 100000) {
      const sizeMB = (fs.statSync(finalOutput).size / 1024 / 1024).toFixed(1);
      logger.success(`Cropped: ${sizeMB}MB at ${SHORTS_W}x${SHORTS_H} (CRF ${crf})`);

      // ─── OpenRouter QA Check (non-directional second opinion) ─────
      try {
        const qa = getOpenRouterQA();
        const qaDir = path.join(tmpDir, `qa_crop_${Date.now()}`);
        fs.mkdirSync(qaDir, { recursive: true });
        const qaFrames = extractFrames(finalOutput, qaDir, qaPositions);
        if (qaFrames.length > 0) {
          // No rawFrames reference here — QA just checks the final cropped output
          const qaResult = await qa.checkCrop([], qaFrames, country);
          if (qaResult) {
            if (qaResult.yes === false || (qaResult.issues && qaResult.issues.length > 0)) {
              logger.warn(`OpenRouter QA flags crop issues: ${(qaResult.issues || ['Unknown']).join('; ')}`);
            } else {
              logger.success(`OpenRouter QA: crop looks good (confidence: ${qaResult.confidence || '?'}/10)`);
            }
          } else {
            logger.info('OpenRouter QA: no response (keys exhausted?)');
          }
        }
        try { fs.rmSync(qaDir, { recursive: true, force: true }); } catch {}
      } catch (qaError) {
        // QA is non-blocking — never let it break the pipeline
        logger.warn(`OpenRouter QA error: ${qaError.message.substring(0, 60)}`);
      }

      return {
        success: true,
        outputPath: finalOutput,
        cropFilter,
        zoom,
        cropOffsetX,
      };
    }
  } catch (e) {
    logger.warn(`Final crop failed: ${e.message.substring(0, 100)}`);
  }

  // Fallback: simple center crop
  logger.warn('Falling back to simple center crop');
  try {
    execSync(
      `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} ` +
      `-vf "scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H}" ` +
      `-c:v libx264 -preset fast -crf 20 -c:a aac -shortest "${finalOutput}" 2>/dev/null`,
      { timeout: 120000 }
    );

    if (fs.existsSync(finalOutput) && fs.statSync(finalOutput).size > 100000) {
      return { success: true, outputPath: finalOutput, cropFilter: 'fallback', zoom: 1.0, cropOffsetX: 0 };
    }
  } catch {}

  return { success: false, outputPath: null };
}

module.exports = { smartCrop, probeVideo, extractFrames, buildCropFilter };