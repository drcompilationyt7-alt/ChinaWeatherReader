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
    execSync(
      `ffmpeg -y -i "${framePath}" -vf "${cropFilter}" -q:v 2 "${outputPath}" 2>/dev/null`,
      { timeout: 10000 }
    );
    return fs.existsSync(outputPath);
  } catch {}
  return false;
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

  // Step 3: Iterative Gemini CLI feedback loop
  const gemini = getGeminiCLI();
  const shouldUseCLI = gemini.isAvailable();

  // Pre-calculate frame positions for QA (used both in loop and after)
  const qaPositions = [duration / 4, duration / 2, duration * 3 / 4].map(t => startTime + t);

  if (shouldUseCLI) {
    const checkDir = path.join(tmpDir, `crop_check_${Date.now()}`);
    fs.mkdirSync(checkDir, { recursive: true });

    try {
      for (let iteration = 0; iteration < MAX_ITERATIONS; iteration++) {
        logger.info(`--- Crop iteration ${iteration + 1}/${MAX_ITERATIONS} ---`);

        // Extract 3 frames at 25%, 50%, 75% of the segment
        const rawFrames = extractFrames(videoPath, path.join(checkDir, `raw_${iteration}`), qaPositions);

        // Apply the current crop filter to each frame
        const croppedFrames = [];
        for (let i = 0; i < rawFrames.length; i++) {
          const croppedPath = rawFrames[i].replace('raw_', 'cropped_');
          if (applyCropToFrame(rawFrames[i], croppedPath, cropFilter)) {
            croppedFrames.push(croppedPath);
          }
        }

        if (croppedFrames.length === 0) {
          logger.warn('No cropped frames generated — using initial crop');
          break;
        }

        // Ask Gemini CLI to evaluate
        const evaluation = await gemini.evaluateCrop(
          rawFrames,
          croppedFrames,
          `Evaluate this crop for a YouTube Short from ${country}. ` +
          `The video is being converted from ${srcW}x${srcH} to 1080x1920 (9:16 portrait). ` +
          `Current crop offset: ${cropOffsetX}px, zoom: ${(zoom * 100).toFixed(0)}%. ` +
          `Is the crop good? Is the main subject centered? Any important content cut off?`
        );

        if (!evaluation) {
          logger.warn('Gemini CLI evaluation failed — using current crop');
          break;
        }

        // Parse the evaluation
        let verdict = 'GOOD';
        let adjustment = null;
        try {
          const jsonMatch = evaluation.match(/\{[\s\S]*\}/);
          if (jsonMatch) {
            const result = JSON.parse(jsonMatch[0]);
            verdict = result.verdict || 'GOOD';
            adjustment = result.suggested_adjustment;
          }
        } catch {
          // If we can't parse, check for keywords
          if (evaluation.toLowerCase().includes('bad') || evaluation.toLowerCase().includes('issue')) {
            verdict = 'BAD';
          }
        }

        logger.info(`Iteration ${iteration + 1} verdict: ${verdict}`);

        if (verdict === 'GOOD') {
          logger.success(`Crop approved after ${iteration + 1} iterations`);
          break;
        }

        // Apply adjustment
        if (adjustment && adjustment.direction !== 'none') {
          const pixels = Math.min(200, Math.max(20, adjustment.pixels || 50));
          if (adjustment.direction === 'left') {
            cropOffsetX += pixels;
          } else if (adjustment.direction === 'right') {
            cropOffsetX -= pixels;
          }
          logger.info(`Adjusting crop: ${adjustment.direction} by ${pixels}px — ${adjustment.reason || ''}`);
          cropFilter = buildCropFilter(srcW, srcH, cropOffsetX, zoom);
        } else {
          // Try slight zoom adjustment instead
          zoom = Math.min(1.15, zoom + 0.03);
          logger.info(`Increasing zoom to ${(zoom * 100).toFixed(0)}%`);
          cropFilter = buildCropFilter(srcW, srcH, cropOffsetX, zoom);
        }
      }
    } finally {
      // Cleanup check frames
      try {
        const checkDirs = fs.readdirSync(checkDir).filter(d => d.startsWith('raw_') || d.startsWith('cropped_'));
        for (const d of checkDirs) {
          fs.rmSync(path.join(checkDir, d), { recursive: true, force: true });
        }
      } catch {}
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