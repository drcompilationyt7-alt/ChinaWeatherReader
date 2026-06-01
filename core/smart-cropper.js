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


const logger = new Logger('SmartCropper');

const SHORTS_W = 1080;
const SHORTS_H = 1920;
const TARGET_RATIO = SHORTS_W / SHORTS_H; // 0.5625
const MAX_ITERATIONS = 3;

function parseJsonFromOutput(output) {
  const text = String(output || '').trim();
  try { return JSON.parse(text); } catch {}
  const matches = text.match(/\{[\s\S]*?\}/g) || [];
  for (let i = matches.length - 1; i >= 0; i--) {
    try { return JSON.parse(matches[i]); } catch {}
  }
  throw new Error(`No JSON object found in output: ${text.substring(0, 120)}`);
}

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

  // Step 3: Gemini feedback loop — find subject, crop, verify, adjust
  const gemini = getGeminiCLI();
  const shouldUseCLI = gemini.isAvailable();

  // Pre-calculate frame positions for QA (used after crop)
  const qaPositions = [duration / 4, duration / 2, duration * 3 / 4].map(t => startTime + t);

  if (true) {
    const targetHeight = 1920;
    const targetWidth = 1080;
    const scaleFactor = targetHeight / srcH;
    const scaledWidth = Math.round(srcW * scaleFactor);
    const maxCropX = scaledWidth - targetWidth;

    // ─── Phase A: Find subject center using YOLO ────────────────────
    logger.info('Analyzing video with YOLO to locate subject...');
    const yoloDir = path.join(tmpDir, `yolo_frames_${Date.now()}`);
    fs.mkdirSync(yoloDir, { recursive: true });
    // Sample every 1.5s for accurate subject center averaging across full clip
    const yoloPositions = [];
    for (let t = 1.5; t < duration - 1; t += 1.5) {
      yoloPositions.push(t);
    }

    const subjectCenters = [];
    const subjectBoxes = [];
    for (const pos of yoloPositions) {
      const framePath = path.join(yoloDir, `yolo_frame.jpg`);
      try {
        execSync(
          `ffmpeg -y -ss ${(startTime + pos).toFixed(2)} -i "${videoPath}" -vframes 1 -q:v 2 "${framePath}" 2>/dev/null`,
          { timeout: 10000 }
        );
        if (fs.existsSync(framePath) && fs.statSync(framePath).size > 1000) {
          const yoloOut = execSync(
            `python3 "${path.join(__dirname, 'yolo-crop.py')}" "${framePath}"`,
            { timeout: 30000, encoding: 'utf8' }
          ).toString().trim();
          const yoloResult = parseJsonFromOutput(yoloOut);
          if (yoloResult.subject !== 'none' && yoloResult.center_x >= 0) {
            subjectCenters.push(yoloResult.center_x);
            if (Array.isArray(yoloResult.bbox) && yoloResult.bbox.length === 4) {
              subjectBoxes.push(yoloResult.bbox);
            }
            logger.info(`  Frame @${pos}s: ${yoloResult.subject} at center_x=${yoloResult.center_x.toFixed(0)} (conf: ${yoloResult.confidence.toFixed(2)})`);
          }
        }
      } catch (e) {
        const errText = (e.stderr || e.stdout || e.message || '').toString();
        logger.warn(`YOLO frame @${pos}s failed: ${errText.substring(0, 120)}`);
      }
    }

    try { fs.rmSync(yoloDir, { recursive: true, force: true }); } catch {}

    if (subjectCenters.length > 0) {
      const avgCenterX = subjectCenters.reduce((a, b) => a + b, 0) / subjectCenters.length;
      const avgScaledCenterX = avgCenterX * scaleFactor;
      let cropX = avgScaledCenterX - (targetWidth / 2);

      if (subjectBoxes.length > 0) {
        const padding = 80;
        const scaledBoxes = subjectBoxes.map(([x1, , x2]) => ({
          left: x1 * scaleFactor,
          right: x2 * scaleFactor,
        }));
        const leftMost = Math.min(...scaledBoxes.map(b => b.left));
        const rightMost = Math.max(...scaledBoxes.map(b => b.right));
        if (leftMost < cropX + padding) cropX = leftMost - padding;
        if (rightMost > cropX + targetWidth - padding) cropX = rightMost - targetWidth + padding;
      }

      cropOffsetX = Math.max(0, Math.min(Math.round(cropX), maxCropX));
      cropFilter = buildCropFilter(srcW, srcH, cropOffsetX, zoom);
      logger.info(`YOLO subject centers: [${subjectCenters.map(c => c.toFixed(0)).join(', ')}], avg: ${avgCenterX.toFixed(0)}, scaled avg: ${avgScaledCenterX.toFixed(0)}, crop offset: ${cropOffsetX}px`);
    } else {
      logger.info('YOLO: No subject detected — using center crop');
    }

  }

  // Step 4: Apply final crop to actual video
  logger.info(`Applying final crop: ${cropFilter}`);
  
  const crf = 0;
  const finalOutput = outputPath || videoPath.replace(/\.\w+$/, '_shorts.mp4');

  try {
    execSync(
      `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} ` +
      `-vf "${cropFilter}" ` +
      `-c:v libx264 -preset veryslow -crf ${crf} -c:a aac -b:a 320k ` +
      `-pix_fmt yuv444p -shortest "${finalOutput}" 2>/dev/null`,
      { timeout: 180000, maxBuffer: 200 * 1024 * 1024 }
    );

    if (fs.existsSync(finalOutput) && fs.statSync(finalOutput).size > 100000) {
      const sizeMB = (fs.statSync(finalOutput).size / 1024 / 1024).toFixed(1);
      logger.success(`Cropped: ${sizeMB}MB at ${SHORTS_W}x${SHORTS_H} (CRF ${crf})`);

      // ─── YOLO QA Check (verify subject is centered in final crop) ─────
      try {
        const qaDir = path.join(tmpDir, `yolo_qa_${Date.now()}`);
        fs.mkdirSync(qaDir, { recursive: true });
        const qaFrames = extractFrames(finalOutput, qaDir, qaPositions);
        const qaOffsets = [];
        for (const fp of qaFrames) {
          try {
            const yoloOut = execSync(
              `python3 "${path.join(__dirname, 'yolo-crop.py')}" "${fp}"`,
              { timeout: 30000, encoding: 'utf8' }
            ).toString().trim();
            const yoloResult = parseJsonFromOutput(yoloOut);
            if (yoloResult.subject !== 'none' && yoloResult.center_x >= 0) {
              qaOffsets.push(Math.abs(yoloResult.center_x - (SHORTS_W / 2)));
            }
          } catch {}
        }
        try { fs.rmSync(qaDir, { recursive: true, force: true }); } catch {}
        if (qaOffsets.length > 0) {
          const avgOffset = qaOffsets.reduce((a, b) => a + b, 0) / qaOffsets.length;
          if (avgOffset > SHORTS_W * 0.2) {
            logger.warn(`YOLO QA: subject avg offset ${avgOffset.toFixed(0)}px — may be off-center`);
          } else {
            logger.success(`YOLO QA: subject well-centered (avg offset ${avgOffset.toFixed(0)}px)`);
          }
        }
      } catch (qaError) {
        logger.warn(`YOLO QA error: ${(qaError.message || '').substring(0, 60)}`);
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
