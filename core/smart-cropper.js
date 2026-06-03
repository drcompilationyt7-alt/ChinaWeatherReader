/**
 * Smart Cropper — Gemini CLI Feedback Loop + Dynamic Virtual Camera Crop
 * 
 * Provides two crop modes:
 * 1. Static crop (existing): single crop position averaged across all frames
 * 2. Dynamic crop (new): smooth sliding crop window that follows people frame-by-frame
 * 
 * The dynamic crop:
 * - Analyzes 1 frame per second using YOLO person detection
 * - Computes optimal crop center to keep all people in frame
 * - Smooths positions with EMA to avoid jitter
 * - Builds an FFmpeg filter with time-conditional crop segments
 * - NEVER zooms in/out — only repositions the 9:16 crop window
 * - Crops directly from original pixels, resizes once at the end
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

// ────────────────────────────────────────────────────────────
// NEW: Dynamic Crop Position Functions
// ────────────────────────────────────────────────────────────

/**
 * Compute 9:16 crop dimensions from original source dimensions.
 * Crops directly from the original frame without intermediate scaling.
 * Returns { cropW, cropH, cropY, maxCropX } or null if source is already 9:16.
 */
function computeCropDimensions(srcW, srcH) {
  const even = (n) => Math.max(2, Math.floor(n / 2) * 2);
  const ratio = srcW / srcH;
  const TARGET = SHORTS_W / SHORTS_H; // 9:16 = 0.5625

  // If already very close to 9:16, no crop needed
  if (Math.abs(ratio - TARGET) < 0.01) {
    return null;
  }

  if (ratio > TARGET) {
    // Landscape: crop to full height, calculate width
    const cropH = even(srcH);
    const cropW = even(cropH * TARGET);
    const maxCropX = Math.max(0, srcW - cropW);
    return { cropW, cropH, cropY: 0, maxCropX };
  } else {
    // Portrait taller than 9:16: crop to full width, calculate height
    const cropW = even(srcW);
    const cropH = even(cropW / TARGET);
    const maxCropY = Math.max(0, srcH - cropH);
    return { cropW, cropH, cropY: Math.floor(maxCropY / 4) * 2, maxCropX: 0 };
  }
}

/**
 * Analyze a video clip and generate per-second crop positions.
 * Samples 1 frame per second, runs YOLO person detection, 
 * returns array of { time, cropX } positions.
 * 
 * @param {string} videoPath - Path to the video to analyze
 * @param {number} startTime - Start offset in seconds
 * @param {number} duration - Duration to analyze in seconds
 * @param {Object} cropDims - { cropW, cropH, cropY, maxCropX } from computeCropDimensions
 * @param {string} tmpDir - Temporary directory for frame extraction
 * @returns {Array<{time: number, cropX: number}>} Per-second crop positions
 */
function getDynamicCropPositions(videoPath, startTime, duration, cropDims, tmpDir) {
  logger.info('Dynamic crop: analyzing video at 1 FPS for subject tracking...');
  const yoloDir = path.join(tmpDir, `dyn_crop_${Date.now()}`);
  fs.mkdirSync(yoloDir, { recursive: true });

  const { maxCropX } = cropDims;
  const positions = [];

  // Sample at 1 FPS
  const fps = 1;
  const sampleCount = Math.max(1, Math.floor(duration * fps));

  for (let i = 0; i < sampleCount; i++) {
    const t = i / fps;
    if (t >= duration) break;
    const framePath = path.join(yoloDir, `frame_${i}.jpg`);
    try {
      execSync(
        `ffmpeg -y -ss ${(startTime + t).toFixed(2)} -i "${videoPath}" -vframes 1 -q:v 2 "${framePath}" 2>/dev/null`,
        { timeout: 10000 }
      );
      if (fs.existsSync(framePath) && fs.statSync(framePath).size > 1000) {
        const detectorOut = execSync(
          `python3 "${path.join(__dirname, 'person-detector.py')}" "${framePath}"`,
          { timeout: 30000, encoding: 'utf8' }
        ).toString().trim();
        const result = parseJsonFromOutput(detectorOut);

        if (result.center_x >= 0 && result.person_count > 0) {
          // Scale center_x from original pixel space to crop space
          // center_x is in original frame coordinates
          // We need: what would the crop X be to center the subject?
          let rawCropX = result.center_x - (cropDims.cropW / 2);
          
          // If multiple people, adjust to keep group in frame
          if (result.person_count > 1 && Array.isArray(result.bboxes) && result.bboxes.length > 0) {
            // Find leftmost and rightmost edges of all people in crop space
            // This prevents people at the edges from being cut off
            const leftEdge = Math.min(...result.bboxes.map(b => b[0]));
            const rightEdge = Math.max(...result.bboxes.map(b => b[2]));
            const padding = 40; // pixels of padding on each side

            // If group is wider than crop window, favor the group center
            if ((rightEdge - leftEdge) > cropDims.cropW) {
              const groupCropX = ((leftEdge + rightEdge) / 2) - (cropDims.cropW / 2);
              rawCropX = groupCropX;
            } else {
              // Adjust rawCropX so both edges fit with padding
              const minAllowed = leftEdge - padding;
              const maxAllowed = rightEdge - cropDims.cropW + padding;
              rawCropX = Math.max(minAllowed, Math.min(rawCropX, maxAllowed));
            }
          }

          const cropX = Math.max(0, Math.min(Math.round(rawCropX), maxCropX));
          positions.push({ time: t, cropX, personCount: result.person_count });
          
          if (i % 5 === 0 || i === sampleCount - 1) {
            logger.info(`  Dynamic crop @${t.toFixed(1)}s: ${result.person_count} person(s), body=${result.body_type}, cropX=${cropX}/${maxCropX}`);
          }
        } else {
          // No people detected — use center of frame
          const centerCropX = Math.max(0, Math.min(Math.round((result.frame_width - cropDims.cropW) / 2), maxCropX));
          positions.push({ time: t, cropX: centerCropX, personCount: 0 });
        }
      }
    } catch (e) {
      // On error, use interpolated position (filled in later)
      positions.push({ time: t, cropX: null, personCount: 0 });
    }
  }

  // Cleanup frames
  try { fs.rmSync(yoloDir, { recursive: true, force: true }); } catch {}

  logger.info(`Dynamic crop: ${positions.length} samples collected (${positions.filter(p => p.cropX !== null).length} valid)`);
  return positions;
}

/**
 * Smooth crop positions using Exponential Moving Average (EMA).
 * Fills gaps (null positions) via linear interpolation from neighbors.
 * Adaptive alpha: 
 *   - Small movement (<20px): α=0.15 (smooth, slow)
 *   - Medium movement (20-60px): α=0.35 (moderate)
 *   - Large movement (>60px): α=0.55 (responsive, subject change)
 * Clamps all positions to valid range [0, maxCropX].
 * 
 * @param {Array<{time: number, cropX: number|null, personCount: number}>} positions
 * @param {number} maxCropX - Maximum allowed crop X value
 * @returns {Array<{time: number, cropX: number}>} Smoothed positions
 */
function smoothCropPositions(positions, maxCropX) {
  if (positions.length === 0) return [];

  // Step 1: Fill null values by linear interpolation
  const filled = [...positions];
  let lastValid = null;
  let lastValidIdx = -1;

  for (let i = 0; i < filled.length; i++) {
    if (filled[i].cropX !== null) {
      if (lastValid !== null && i - lastValidIdx > 1) {
        // Interpolate between lastValid and filled[i]
        const gap = i - lastValidIdx;
        const startVal = lastValid;
        const endVal = filled[i].cropX;
        for (let j = 1; j < gap; j++) {
          const frac = j / gap;
          filled[lastValidIdx + j].cropX = Math.round(startVal + (endVal - startVal) * frac);
        }
      }
      lastValid = filled[i].cropX;
      lastValidIdx = i;
    }
  }

  // If leading nulls, fill with first valid value
  if (lastValidIdx > 0 && filled[0].cropX === null) {
    const firstValid = filled[lastValidIdx].cropX;
    for (let i = 0; i < lastValidIdx; i++) {
      filled[i].cropX = firstValid;
    }
  }

  // If trailing nulls, fill with last valid
  let lastValidBack = null;
  let lastValidBackIdx = -1;
  for (let i = filled.length - 1; i >= 0; i--) {
    if (filled[i].cropX !== null) {
      lastValidBack = filled[i].cropX;
      lastValidBackIdx = i;
      break;
    }
  }
  if (lastValidBackIdx >= 0 && lastValidBackIdx < filled.length - 1) {
    for (let i = lastValidBackIdx + 1; i < filled.length; i++) {
      filled[i].cropX = lastValidBack;
    }
  }

  // If everything is null, use center of frame
  if (filled[0].cropX === null) {
    const center = Math.round(maxCropX / 2);
    for (let i = 0; i < filled.length; i++) {
      filled[i].cropX = center;
    }
  }

  // Step 2: Apply EMA smoothing with adaptive alpha
  const smoothed = [];
  let prevCropX = filled[0].cropX;

  for (let i = 0; i < filled.length; i++) {
    const raw = filled[i].cropX;
    const diff = Math.abs(raw - prevCropX);

    // Adaptive alpha: larger movement = less smoothing (more responsive)
    let alpha;
    if (diff < 20) {
      alpha = 0.15;    // Smooth small movements
    } else if (diff < 60) {
      alpha = 0.35;    // Moderate
    } else if (diff < 150) {
      alpha = 0.55;    // Faster transition for subject changes
    } else {
      alpha = 0.75;    // Very fast for abrupt scene changes
    }

    // Clamp max step to prevent jarring jumps (max 50px per second)
    const maxStep = 50;
    let smoothVal = raw;
    if (diff > maxStep) {
      const direction = raw > prevCropX ? 1 : -1;
      smoothVal = prevCropX + direction * maxStep;
    } else {
      smoothVal = prevCropX + alpha * (raw - prevCropX);
    }

    smoothVal = Math.max(0, Math.min(Math.round(smoothVal), maxCropX));
    smoothed.push({ time: filled[i].time, cropX: smoothVal });
    prevCropX = smoothVal;
  }

  return smoothed;
}

/**
 * Build an FFmpeg filter string with time-conditional crop segments.
 * Adjacent positions within 2px are merged to reduce filter segment count.
 * Ends with a single scale to 1920x1080.
 * 
 * @param {number} srcW - Source width
 * @param {number} srcH - Source height
 * @param {Object} cropDims - { cropW, cropH, cropY } from computeCropDimensions
 * @param {Array<{time: number, cropX: number}>} smoothedPositions - Smoothed positions
 * @returns {string} FFmpeg filter_complex string
 */
function buildDynamicCropFilter(srcW, srcH, cropDims, smoothedPositions) {
  if (!smoothedPositions || smoothedPositions.length === 0) {
    // Fallback: static center crop
    const centerX = Math.floor((srcW - cropDims.cropW) / 4) * 2;
    return `crop=${cropDims.cropW}:${cropDims.cropH}:${Math.max(0, centerX)}:${cropDims.cropY},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
  }

  // Step 1: Merge adjacent positions that are similar (within 2px)
  const merged = [];
  let segStart = smoothedPositions[0].time;
  let segCropX = smoothedPositions[0].cropX;

  for (let i = 1; i < smoothedPositions.length; i++) {
    const pos = smoothedPositions[i];
    const prev = smoothedPositions[i - 1];
    const timeGap = pos.time - prev.time;

    if (Math.abs(pos.cropX - segCropX) <= 2 && timeGap <= 1.5) {
      // Similar enough to merge — extend segment
      continue;
    } else {
      // End current segment
      merged.push({ start: segStart, end: prev.time + 0.5, cropX: segCropX });
      segStart = pos.time;
      segCropX = pos.cropX;
    }
  }
  // Final segment
  const lastTime = smoothedPositions[smoothedPositions.length - 1].time;
  merged.push({ start: segStart, end: lastTime + 0.5, cropX: segCropX });

  // Step 2: Build filter string
  // Only merge if it reduces segment count
  const segments = merged.length < smoothedPositions.length ? merged : 
    smoothedPositions.map((p, i) => ({
      start: p.time,
      end: i < smoothedPositions.length - 1 ? (p.time + smoothedPositions[i + 1].time) / 2 : p.time + 0.5,
      cropX: p.cropX,
    }));

  const even = (n) => Math.max(2, Math.floor(n / 2) * 2);

  // For very long segments, we can batch more aggressively
  const finalSegments = [];
  for (const seg of segments) {
    const cropX = even(seg.cropX);
    finalSegments.push(`crop=${cropDims.cropW}:${cropDims.cropH}:${cropX}:${cropDims.cropY}:enable='between(t,${seg.start.toFixed(1)},${seg.end.toFixed(1)})'`);
  }

  // Join all crop segments — they are mutually exclusive (only one is active at a time)
  const cropChain = finalSegments.join(',');

  // Add final scale to 1920x1080
  const filterStr = `${cropChain},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;

  logger.info(`Dynamic crop filter: ${finalSegments.length} time segments (merged from ${smoothedPositions.length} samples)`);
  return filterStr;
}

/**
 * Full dynamic crop pipeline: detect, smooth, and build filter.
 * 
 * @param {string} videoPath - Path to the video
 * @param {number} startTime - Start time in seconds
 * @param {number} duration - Duration to crop in seconds
 * @param {number} srcW - Source width
 * @param {number} srcH - Source height
 * @param {string} tmpDir - Temp directory for frame extraction
 * @returns {string|null} FFmpeg filter string, or null on failure
 */
function generateDynamicCropFilter(videoPath, startTime, duration, srcW, srcH, tmpDir) {
  try {
    const cropDims = computeCropDimensions(srcW, srcH);
    if (!cropDims) {
      // Already 9:16 — just scale
      return `scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
    }
    if (cropDims.maxCropX <= 0) {
      // Portrait taller than 9:16 — static vertical centering, no horizontal movement
      return `crop=${cropDims.cropW}:${cropDims.cropH}:0:${cropDims.cropY},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
    }

    const rawPositions = getDynamicCropPositions(videoPath, startTime, duration, cropDims, tmpDir);
    if (rawPositions.length === 0) {
      const centerX = Math.floor((srcW - cropDims.cropW) / 4) * 2;
      return `crop=${cropDims.cropW}:${cropDims.cropH}:${Math.max(0, centerX)}:${cropDims.cropY},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
    }

    const smoothed = smoothCropPositions(rawPositions, cropDims.maxCropX);
    const filterStr = buildDynamicCropFilter(srcW, srcH, cropDims, smoothed);
    return filterStr;
  } catch (e) {
    logger.warn(`Dynamic crop generation failed: ${e.message.substring(0, 100)}`);
    return null;
  }
}

// ────────────────────────────────────────────────────────────
// END: Dynamic Crop Functions
// ────────────────────────────────────────────────────────────

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

module.exports = { 
  smartCrop, 
  probeVideo, 
  extractFrames, 
  buildCropFilter,
  // New exports
  computeCropDimensions,
  getDynamicCropPositions,
  smoothCropPositions,
  buildDynamicCropFilter,
  generateDynamicCropFilter,
};