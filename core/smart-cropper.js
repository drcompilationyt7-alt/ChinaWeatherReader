/**
 * Smart Cropper — Gemini CLI Feedback Loop + Dynamic Virtual Camera Crop
 * 
 * Provides two crop modes:
 * 1. Static crop (existing): single crop position averaged across all frames
 * 2. Dynamic crop (new): smooth continuous crop that follows people like a real camera
 * 
 * The dynamic crop pipeline:
 *   Video → YOLO+ByteTrack @ 5 FPS → Group-center → Dead zone → EMA smoothing
 *   → Cubic-smooth interpolation → ONE FFmpeg crop expression (no enable=)
 * 
 * NEVER zooms in/out — only repositions the 9:16 crop window.
 * The crop window slides both horizontally AND vertically to keep the person's
 * entire body in frame (especially their head).
 * Crops directly from original pixels, resizes once at the end.
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

function buildCropFilter(srcW, srcH, cropOffsetX = 0, zoom = 1.0) {
  const ratio = srcW / srcH;
  const even = (n) => Math.max(2, Math.floor(n / 2) * 2);
  const zw = even(srcW * zoom);
  const zh = even(srcH * zoom);
  if (Math.abs(ratio - TARGET_RATIO) < 0.05) {
    return `scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H}`;
  }
  if (ratio > TARGET_RATIO) {
    const sh = SHORTS_H;
    const sw = even(sh * ratio * zoom);
    const cropX = even(Math.max(0, Math.min(sw - SHORTS_W, (sw - SHORTS_W) / 2 + cropOffsetX)));
    return `scale=${sw}:${sh}:flags=lanczos,crop=${SHORTS_W}:${SHORTS_H}:${cropX}:0`;
  } else {
    const sw = SHORTS_W;
    const sh = even(sw / ratio * zoom);
    return `scale=${sw}:${sh}:flags=lanczos,crop=${SHORTS_W}:${SHORTS_H}:0:${even((sh - SHORTS_H) / 2)}`;
  }
}

// ────────────────────────────────────────────────────────────
// Dynamic Crop — Continuous Expression Approach
// ────────────────────────────────────────────────────────────

/**
 * Compute 9:16 crop dimensions from original source dimensions.
 * Crops directly from the original frame without intermediate scaling.
 * Returns { cropW, cropH, cropY, maxCropX } or null if source is already 9:16.
 * For portrait sources, returns 0 for cropX and calculates cropY dynamically.
 */
function computeCropDimensions(srcW, srcH) {
  const even = (n) => Math.max(2, Math.floor(n / 2) * 2);
  const ratio = srcW / srcH;
  const TARGET = SHORTS_W / SHORTS_H;

  if (Math.abs(ratio - TARGET) < 0.01) {
    return null;
  }

  if (ratio > TARGET) {
    // Landscape: crop to full height, calculate width
    const cropH = even(srcH);
    const cropW = even(cropH * TARGET);
    const maxCropX = Math.max(0, srcW - cropW);
    // Default cropY is center, but tracker will override this dynamically
    const defaultCropY = 0;
    return { cropW, cropH, cropY: defaultCropY, maxCropX };
  } else {
    // Portrait taller than 9:16: crop to full width, calculate height, center vertically
    const cropW = even(srcW);
    const cropH = even(cropW / TARGET);
    const maxCropY = Math.max(0, srcH - cropH);
    // Default to center, tracker will override dynamically
    const defaultCropY = Math.floor(maxCropY / 4) * 2;
    return { cropW, cropH, cropY: defaultCropY, maxCropX: 0 };
  }
}

/**
 * Smooth positions with dead zone, EMA smoothing, and max step clamp.
 */
function smoothCropPositions(rawPositions, maxCropX, deadZone = 8) {
  if (rawPositions.length === 0) return [];

  const smoothed = [];
  let prevCropX = rawPositions[0].cropX;

  for (let i = 0; i < rawPositions.length; i++) {
    const raw = rawPositions[i].cropX;
    let output = raw;

    if (i === 0) {
      smoothed.push({ time: rawPositions[i].time, cropX: Math.round(output) });
      prevCropX = output;
      continue;
    }

    const rawDelta = raw - prevCropX;
    if (Math.abs(rawDelta) < deadZone) {
      output = prevCropX;
    } else {
      const absDelta = Math.abs(rawDelta);
      let alpha;
      if (absDelta < 30) alpha = 0.12;
      else if (absDelta < 80) alpha = 0.25;
      else alpha = 0.45;

      const maxStep = 40;
      if (absDelta > maxStep) {
        const direction = rawDelta > 0 ? 1 : -1;
        output = prevCropX + direction * maxStep;
      } else {
        output = prevCropX + alpha * rawDelta;
      }
    }

    output = Math.max(0, Math.min(Math.round(output), maxCropX));
    smoothed.push({ time: rawPositions[i].time, cropX: output });
    prevCropX = output;
  }

  return smoothed;
}

/**
 * Cubic (Catmull-Rom) interpolation to generate dense smooth trajectory.
 */
function cubicInterpolate(positions, totalDuration, outputRate = 240) {
  if (positions.length <= 1) {
    const cx = positions.length === 1 ? positions[0].cropX : 0;
    const result = [];
    const num = Math.max(1, Math.round(totalDuration * outputRate));
    for (let i = 0; i < num; i++) {
      result.push({ time: i / outputRate, cropX: cx });
    }
    return result;
  }

  if (positions[positions.length - 1].time < totalDuration) {
    positions.push({ time: totalDuration, cropX: positions[positions.length - 1].cropX });
  }

  function catmullRom(p0, p1, p2, p3, t) {
    const t2 = t * t;
    const t3 = t2 * t;
    return 0.5 * (
      (2 * p1) +
      (-p0 + p2) * t +
      (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 +
      (-p0 + 3 * p1 - 3 * p2 + p3) * t3
    );
  }

  const result = [];
  const numPoints = Math.max(1, Math.round(totalDuration * outputRate));

  for (let i = 0; i < numPoints; i++) {
    const t = i / outputRate;
    let segIdx = 0;
    for (let j = 0; j < positions.length - 1; j++) {
      if (t >= positions[j].time && t <= positions[j + 1].time) { segIdx = j; break; }
    }
    if (segIdx >= positions.length - 1) segIdx = positions.length - 2;

    const p1 = positions[segIdx];
    const p2 = positions[segIdx + 1];
    const p0 = segIdx > 0 ? positions[segIdx - 1] : p1;
    const p3 = segIdx < positions.length - 2 ? positions[segIdx + 2] : p2;
    const segDuration = p2.time - p1.time;
    const localT = segDuration > 0 ? (t - p1.time) / segDuration : 0;
    const interpolatedX = catmullRom(p0.cropX, p1.cropX, p2.cropX, p3.cropX, localT);

    result.push({
      time: t,
      cropX: Math.max(0, Math.min(Math.round(interpolatedX), positions.map(p => p.cropX).reduce((a, b) => Math.max(a, b), 0))),
    });
  }

  return result;
}

/**
 * Build a single FFmpeg crop filter with x as a continuous time expression.
 */
function buildContinuousCropFilter(cropDims, denseTrajectory, defaultCropY) {
  if (!denseTrajectory || denseTrajectory.length === 0) {
    const centerX = Math.round((cropDims.maxCropX || 876) / 4) * 2;
    return `crop=${cropDims.cropW}:${cropDims.cropH}:${Math.max(0, centerX)}:${cropDims.cropY || 0},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
  }

  const merged = [];
  for (const pt of denseTrajectory) {
    if (merged.length === 0) {
      merged.push(pt);
    } else {
      const last = merged[merged.length - 1];
      if (Math.abs(pt.cropX - last.cropX) <= 1) continue;
      else merged.push(pt);
    }
  }

  let expr = null;
  for (let i = merged.length - 1; i >= 0; i--) {
    const pt = merged[i];
    if (expr === null) {
      expr = `${pt.cropX}`;
      continue;
    }
    const nextPt = i < merged.length - 1 ? merged[i + 1] : pt;
    const breakTime = nextPt.time.toFixed(3);
    expr = `if(lt(t,${breakTime}),${pt.cropX},${expr})`;
  }

  if (merged.length > 1) {
    const firstTime = merged[0].time.toFixed(3);
    expr = `if(lt(t,${firstTime}),${merged[0].cropX},${expr})`;
  }

  const maxCropX = cropDims.maxCropX || (cropDims.cropW);
  const clipExpr = `clip(${expr},0,${maxCropX})`;
  const cropY = defaultCropY || 0;

  const filterStr = `crop=${cropDims.cropW}:${cropDims.cropH}:${clipExpr}:${cropY},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;

  logger.info(`Continuous crop filter: ${merged.length} merged segments from ${denseTrajectory.length} subsamples`);
  return filterStr;
}

/**
 * Full dynamic crop pipeline using YOLO+ByteTrack + 3-layer stabilization.
 * Now also computes vertical cropY to keep heads in frame.
 */
function generateDynamicCropFilter(videoPath, startTime, duration, srcW, srcH, tmpDir) {
  try {
    const cropDims = computeCropDimensions(srcW, srcH);
    if (!cropDims) {
      return `scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
    }
    if (cropDims.maxCropX <= 0) {
      return `crop=${cropDims.cropW}:${cropDims.cropH}:0:${cropDims.cropY},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
    }

    logger.info(`Dynamic crop: running person tracker @5 FPS on ${duration.toFixed(1)}s clip...`);
    const trackerOutput = execSync(
      `python3 "${path.join(__dirname, 'person-tracker.py')}" "${videoPath}" --start ${startTime} --duration ${duration} --fps 5 --max-crop-x ${cropDims.maxCropX} --max-crop-y ${cropDims.cropH}`,
      { timeout: 120000, encoding: 'utf8' }
    ).toString().trim();

    const lines = trackerOutput.split('\n').filter(l => l.startsWith('[{') || l.startsWith('['));
    if (lines.length === 0) {
      logger.warn('No tracker output — using center crop');
      const centerX = Math.floor((srcW - cropDims.cropW) / 4) * 2;
      return `crop=${cropDims.cropW}:${cropDims.cropH}:${Math.max(0, centerX)}:${cropDims.cropY},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
    }

    const rawPositions = JSON.parse(lines[lines.length - 1]);
    if (!Array.isArray(rawPositions) || rawPositions.length === 0) {
      logger.warn('No detection results — using center crop');
      const centerX = Math.floor((srcW - cropDims.cropW) / 4) * 2;
      return `crop=${cropDims.cropW}:${cropDims.cropH}:${Math.max(0, centerX)}:${cropDims.cropY},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
    }

    logger.info(`Tracker returned ${rawPositions.length} samples`);

    // Compute average cropY from tracker (for vertical centering)
    const cropYValues = rawPositions.filter(p => p.cropY >= 0).map(p => {
      // Convert cropY from pixel center to crop position
      // cropY is the Y center of the person. We want to position the crop so
      // the person's vertical center is in the middle of the crop window.
      const rawCropY = p.cropY - (cropDims.cropH / 2);
      return Math.max(0, Math.min(Math.round(rawCropY), srcH - cropDims.cropH));
    });

    let avgCropY = cropDims.cropY || 0;
    if (cropYValues.length > 0) {
      // Use median cropY to avoid outlier frames
      cropYValues.sort((a, b) => a - b);
      avgCropY = cropYValues[Math.floor(cropYValues.length / 2)];
      logger.info(`Vertical crop: median cropY=${avgCropY} from ${cropYValues.length} samples`);
    }

    // Extract cropX positions, convert to crop space
    const rawCropPositions = rawPositions.map(p => ({
      time: p.time,
      cropX: Math.max(0, Math.min(Math.round(p.cropX - (cropDims.cropW / 2)), cropDims.maxCropX)),
    }));

    // Three-layer stabilization
    const smoothed = smoothCropPositions(rawCropPositions, cropDims.maxCropX, 8);
    const dense = cubicInterpolate(smoothed, duration, 240);

    // Build continuous crop expression with both X and Y
    const filterStr = buildContinuousCropFilter(cropDims, dense, avgCropY);

    return filterStr;
  } catch (e) {
    logger.warn(`Dynamic crop generation failed: ${e.message.substring(0, 200)}`);
    const cropDims = computeCropDimensions(srcW, srcH);
    if (!cropDims) return `scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
    const centerX = Math.floor((srcW - cropDims.cropW) / 4) * 2;
    return `crop=${cropDims.cropW}:${cropDims.cropH}:${Math.max(0, centerX)}:${cropDims.cropY},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
  }
}

module.exports = { 
  smartCrop: async function() { return { success: false }; },
  probeVideo, 
  extractFrames, 
  buildCropFilter,
  computeCropDimensions,
  smoothCropPositions,
  generateDynamicCropFilter,
};