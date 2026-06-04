/**
 * Smart Cropper — Dynamic Virtual Camera Crop
 * 
 * The dynamic crop pipeline:
 *   Video → YOLO+ByteTrack @ 5 FPS → Group-center → Dead zone → EMA smoothing
 *   → Keyframe extraction → ONE FFmpeg crop expression (simple, no nested IFs)
 * 
 * NEVER zooms in/out — only repositions the 9:16 crop window.
 * Crops directly from original pixels, resizes once at the end.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');


const logger = new Logger('SmartCropper');

const SHORTS_W = 1080;
const SHORTS_H = 1920;
const TARGET_RATIO = SHORTS_W / SHORTS_H;

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
      if (fs.existsSync(framePath) && fs.statSync(framePath).size > 1000) frames.push(framePath);
    } catch {}
  }
  return frames;
}

// ────────────────────────────────────────────────────────────
// Dynamic Crop — Compact Expression Approach
// ────────────────────────────────────────────────────────────

/**
 * Compute 9:16 crop dimensions from original source dimensions.
 */
function computeCropDimensions(srcW, srcH) {
  const even = (n) => Math.max(2, Math.floor(n / 2) * 2);
  const ratio = srcW / srcH;
  const TARGET = SHORTS_W / SHORTS_H;
  if (Math.abs(ratio - TARGET) < 0.01) return null;
  if (ratio > TARGET) {
    const cropH = even(srcH);
    const cropW = even(cropH * TARGET);
    const maxCropX = Math.max(0, srcW - cropW);
    return { cropW, cropH, cropY: 0, maxCropX };
  } else {
    const cropW = even(srcW);
    const cropH = even(cropW / TARGET);
    const maxCropY = Math.max(0, srcH - cropH);
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
 * Extract keyframes from smoothed positions — picks ~1 keyframe per second
 * plus extra at points of significant change. This keeps the final expression small.
 */
function extractKeyframes(positions, tolerance = 10) {
  if (positions.length <= 2) return positions;
  const keyframes = [positions[0]];
  for (let i = 1; i < positions.length - 1; i++) {
    const prev = keyframes[keyframes.length - 1];
    const curr = positions[i];
    // Add keyframe if position changed more than tolerance, or time gap > 1.5s
    if (Math.abs(curr.cropX - prev.cropX) > tolerance || (curr.time - prev.time) > 1.5) {
      keyframes.push(curr);
    }
  }
  // Always include last point
  keyframes.push(positions[positions.length - 1]);
  return keyframes;
}

/**
 * Build an FFmpeg crop expression using nested if(lt(t,...)) with linear interpolation.
 * 
 * For keyframes [(t0,x0), (t1,x1), ..., (tn,xn)], generates:
 *   if(lt(t,t1), x0+(x1-x0)*(t-t0)/(t1-t0),
 *   if(lt(t,t2), x1+(x2-x1)*(t-t1)/(t2-t1),
 *   ...))
 * 
 * FFmpeg evaluates this per-frame, producing smooth continuous panning.
 */
function buildCropExpression(cropDims, keyframes, defaultCropY) {
  if (!keyframes || keyframes.length === 0) {
    const centerX = Math.round((cropDims.maxCropX || 876) / 4) * 2;
    return `crop=${cropDims.cropW}:${cropDims.cropH}:${Math.max(0, centerX)}:${cropDims.cropY || 0},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
  }

  const maxCropX = cropDims.maxCropX || cropDims.cropW;
  const cropY = defaultCropY || 0;

  // Build expression from last segment backwards (innermost)
  // Last segment: just returns its end value for t >= last_time
  let expr = `${keyframes[keyframes.length - 1].cropX}`;
  
  // Work backwards from second-to-last to first
  for (let i = keyframes.length - 2; i >= 0; i--) {
    const a = keyframes[i];
    const b = keyframes[i + 1];
    const tA = a.time;
    const tB = b.time;
    const xA = a.cropX;
    const xB = b.cropX;
    const dt = tB - tA;
    
    if (dt > 0) {
      expr = `if(lt(t,${tB.toFixed(3)}),${xA}+(${xB}-${xA})*(t-${tA.toFixed(3)})/${dt.toFixed(3)},${expr})`;
    } else {
      // Degenerate segment (same time), skip to next
      continue;
    }
  }
  
  // Log info about the crop range
  const allX = keyframes.map(k => k.cropX);
  const minX = Math.min(...allX);
  const maxX = Math.max(...allX);
  
  logger.info(`Crop range: ${minX}–${maxX}px (range: ${maxX-minX}px), keyframes: ${keyframes.length}`);

  const filterStr = `crop=${cropDims.cropW}:${cropDims.cropH}:${expr}:${cropY},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
  return filterStr;
}

/**
 * Full dynamic crop pipeline.
 */
function generateDynamicCropFilter(videoPath, startTime, duration, srcW, srcH, tmpDir) {
  try {
    const cropDims = computeCropDimensions(srcW, srcH);
    if (!cropDims) return `scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
    if (cropDims.maxCropX <= 0) return `crop=${cropDims.cropW}:${cropDims.cropH}:0:${cropDims.cropY},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;

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

    // Compute median cropY for vertical centering
    const cropYValues = rawPositions.filter(p => p.cropY >= 0).map(p => {
      const rawCropY = p.cropY - (cropDims.cropH / 2);
      return Math.max(0, Math.min(Math.round(rawCropY), srcH - cropDims.cropH));
    });
    let avgCropY = cropDims.cropY || 0;
    if (cropYValues.length > 0) {
      cropYValues.sort((a, b) => a - b);
      avgCropY = cropYValues[Math.floor(cropYValues.length / 2)];
      logger.info(`Vertical crop: median cropY=${avgCropY} from ${cropYValues.length} samples`);
    }

    // Convert to crop space X positions
    const rawCropPositions = rawPositions.map(p => ({
      time: p.time,
      cropX: Math.max(0, Math.min(Math.round(p.cropX - (cropDims.cropW / 2)), cropDims.maxCropX)),
    }));

    // EMA smoothing
    const smoothed = smoothCropPositions(rawCropPositions, cropDims.maxCropX, 8);

    // Extract compact keyframes (tolerance=10px, ~1 per second)
    const keyframes = extractKeyframes(smoothed, 10);

    // Build crop expression with nested if(lt()) lerp
    const filter = buildCropExpression(cropDims, keyframes, avgCropY);

    return filter;
  } catch (e) {
    logger.warn(`Dynamic crop generation failed: ${e.message.substring(0, 200)}`);
    try {
      const cropDims = computeCropDimensions(srcW, srcH);
      if (!cropDims) return `scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
      const centerX = Math.floor((srcW - cropDims.cropW) / 4) * 2;
      return `crop=${cropDims.cropW}:${cropDims.cropH}:${Math.max(0, centerX)}:${cropDims.cropY},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
    } catch { return `scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`; }
  }
}

module.exports = { 
  probeVideo, 
  extractFrames, 
  computeCropDimensions,
  generateDynamicCropFilter,
};