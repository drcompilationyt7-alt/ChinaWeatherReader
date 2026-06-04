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
 * Build the ffmpeg filter for cropping to 9:16 (static/legacy)
 */
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
// NEW: Dynamic Crop — Continuous Expression Approach
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

  if (Math.abs(ratio - TARGET) < 0.01) {
    return null;
  }

  if (ratio > TARGET) {
    const cropH = even(srcH);
    const cropW = even(cropH * TARGET);
    const maxCropX = Math.max(0, srcW - cropW);
    return { cropW, cropH, cropY: 0, maxCropX };
  } else {
    const cropW = even(srcW);
    const cropH = even(cropW / TARGET);
    const maxCropY = Math.max(0, srcH - cropH);
    return { cropW, cropH, cropY: Math.floor(maxCropY / 4) * 2, maxCropX: 0 };
  }
}

/**
 * Smooth crop positions with three layers of stabilization:
 * 1. Dead zone — ignore movements smaller than threshold
 * 2. EMA smoothing — adaptive alpha based on movement size
 * 3. Max step clamp — prevent instantaneous jumps
 * 
 * @param {Array<{time: number, cropX: number}>} rawPositions
 * @param {number} maxCropX
 * @param {number} deadZone - Pixels of tolerance (default 8)
 * @returns {Array<{time: number, cropX: number}>}
 */
function smoothCropPositions(rawPositions, maxCropX, deadZone = 8) {
  if (rawPositions.length === 0) return [];

  const smoothed = [];
  let prevCropX = rawPositions[0].cropX;

  for (let i = 0; i < rawPositions.length; i++) {
    const raw = rawPositions[i].cropX;
    let output = raw;

    if (i === 0) {
      // First frame: use raw value
      smoothed.push({ time: rawPositions[i].time, cropX: Math.round(output) });
      prevCropX = output;
      continue;
    }

    // 1. Dead zone: if delta is tiny, don't move at all
    const rawDelta = raw - prevCropX;
    if (Math.abs(rawDelta) < deadZone) {
      output = prevCropX;
    } else {
      // 2. Adaptive EMA
      const absDelta = Math.abs(rawDelta);
      let alpha;
      if (absDelta < 30) {
        alpha = 0.12;     // Very smooth for small movements
      } else if (absDelta < 80) {
        alpha = 0.25;     // Moderate
      } else {
        alpha = 0.45;     // Responsive for large movements (subject changes)
      }

      // 3. Max step clamp: max 40px per 0.2s (5 FPS)
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
 * Generate cubic-smooth sub-samples between keyframe positions.
 * Uses Catmull-Rom cubic interpolation for smooth curves.
 * Outputs 240 points/second for seamless FFmpeg expression.
 * 
 * @param {Array<{time: number, cropX: number}>} positions - Already smoothed keyframes
 * @param {number} totalDuration - Clip duration in seconds
 * @param {number} outputRate - Sub-samples per second (default 240)
 * @returns {Array<{time: number, cropX: number}>} Dense trajectory
 */
function cubicInterpolate(positions, totalDuration, outputRate = 240) {
  if (positions.length <= 1) {
    // Single point — constant
    const cx = positions.length === 1 ? positions[0].cropX : Math.round(876 / 2);
    const result = [];
    const num = Math.max(1, Math.round(totalDuration * outputRate));
    for (let i = 0; i < num; i++) {
      result.push({ time: i / outputRate, cropX: cx });
    }
    return result;
  }

  // Ensure positions cover the full duration
  if (positions[positions.length - 1].time < totalDuration) {
    positions.push({ time: totalDuration, cropX: positions[positions.length - 1].cropX });
  }

  // Catmull-Rom cubic interpolation
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

    // Find the segment containing t
    let segIdx = 0;
    for (let j = 0; j < positions.length - 1; j++) {
      if (t >= positions[j].time && t <= positions[j + 1].time) {
        segIdx = j;
        break;
      }
    }
    // Clamp to last segment
    if (segIdx >= positions.length - 1) segIdx = positions.length - 2;

    const p1 = positions[segIdx];
    const p2 = positions[segIdx + 1];
    const p0 = segIdx > 0 ? positions[segIdx - 1] : p1;
    const p3 = segIdx < positions.length - 2 ? positions[segIdx + 2] : p2;

    // Normalize t within the segment
    const segDuration = p2.time - p1.time;
    const localT = segDuration > 0 ? (t - p1.time) / segDuration : 0;

    const interpolatedX = catmullRom(p0.cropX, p1.cropX, p2.cropX, p3.cropX, localT);

    result.push({
      time: t,
      cropX: Math.max(0, Math.min(Math.round(interpolatedX), positions.map(p => p.cropX).reduce((a, b) => Math.max(a, b)))),
    });
  }

  return result;
}

/**
 * Build a single FFmpeg crop filter with x as a continuous time expression.
 * Uses piecewise linear interpolation between dense (240/sec) sub-samples.
 * The expression handles up to ~10000 sub-samples efficiently via nested if().
 * 
 * @param {Object} cropDims - { cropW, cropH, cropY } from computeCropDimensions
 * @param {Array<{time: number, cropX: number}>} denseTrajectory - Cubic-interpolated sub-samples
 * @returns {string} Single FFmpeg filter string with one crop + scale
 */
function buildContinuousCropFilter(cropDims, denseTrajectory) {
  if (!denseTrajectory || denseTrajectory.length === 0) {
    // Fallback: center crop
    const centerX = Math.round((cropDims.maxCropX || 876) / 4) * 2;
    return `crop=${cropDims.cropW}:${cropDims.cropH}:${Math.max(0, centerX)}:${cropDims.cropY},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
  }

  // We generate a piecewise linear expression using nested if(lt(t, time), value, ...)
  // For very dense trajectories (240 pts/sec), we merge segments within 1px to reduce
  // expression size. FFmpeg can handle fairly large expressions.
  
  // Merge adjacent segments within 1px
  const merged = [];
  for (const pt of denseTrajectory) {
    if (merged.length === 0) {
      merged.push(pt);
    } else {
      const last = merged[merged.length - 1];
      if (Math.abs(pt.cropX - last.cropX) <= 1) {
        // Extend the last segment's end time but keep value
        continue;
      } else {
        merged.push(pt);
      }
    }
  }

  // Build nested expression from right to left (innermost is last segment)
  // Expression: if(lt(t,T1), V1, if(lt(t,T2), V2, Vn))
  // This avoids needing eval() and ffmpeg evaluates lt/if natively
  
  let expr = null;
  for (let i = merged.length - 1; i >= 0; i--) {
    const pt = merged[i];
    if (expr === null) {
      // Last segment: just its value
      const lastTime = pt.time;
      if (i < merged.length - 1) {
        // Extend to end
        const prev = merged[i + 1];
        expr = `${pt.cropX}`;
      } else {
        expr = `${pt.cropX}`;
      }
      continue;
    }
    const nextPt = i < merged.length - 1 ? merged[i + 1] : pt;
    const breakTime = nextPt.time.toFixed(3);
    expr = `if(lt(t,${breakTime}),${pt.cropX},${expr})`;
  }

  // Handle leading time (t < first point)
  if (merged.length > 1) {
    const firstTime = merged[0].time.toFixed(3);
    expr = `if(lt(t,${firstTime}),${merged[0].cropX},${expr})`;
  }

  // Wrap with clip for safety
  const maxCropX = cropDims.maxCropX || (cropDims.cropW);
  const clipExpr = `clip(${expr},0,${maxCropX})`;

  const filterStr = `crop=${cropDims.cropW}:${cropDims.cropH}:${clipExpr}:${cropDims.cropY},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
  
  logger.info(`Continuous crop filter: ${merged.length} merged segments from ${denseTrajectory.length} subsamples`);
  return filterStr;
}

/**
 * Full dynamic crop pipeline using YOLO+ByteTrack + 3-layer stabilization.
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
      return `scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
    }
    if (cropDims.maxCropX <= 0) {
      return `crop=${cropDims.cropW}:${cropDims.cropH}:0:${cropDims.cropY},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
    }

    // Step 1: Run YOLO+ByteTrack at 5 FPS
    logger.info(`Dynamic crop: running person tracker @5 FPS on ${duration.toFixed(1)}s clip...`);
    const trackerOutput = execSync(
      `python3 "${path.join(__dirname, 'person-tracker.py')}" "${videoPath}" --start ${startTime} --duration ${duration} --fps 5 --max-crop-x ${cropDims.maxCropX}`,
      { timeout: 120000, encoding: 'utf8' }
    ).toString().trim();

    // The tracker outputs JSON to stdout (stderr is debug logs)
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

    // Step 2: Extract cropX from tracker output and convert to crop space
    const rawCropPositions = rawPositions.map(p => ({
      time: p.time,
      cropX: Math.max(0, Math.min(Math.round(p.cropX - (cropDims.cropW / 2)), cropDims.maxCropX)),
    }));

    // Step 3: Three-layer stabilization
    // Layer A: Dead zone + EMA smoothing
    const smoothed = smoothCropPositions(rawCropPositions, cropDims.maxCropX, 8);

    // Layer B: Cubic interpolation for continuous trajectory (240 sub-samples/sec)
    const dense = cubicInterpolate(smoothed, duration, 240);

    // Step 4: Build continuous crop expression for FFmpeg
    const filterStr = buildContinuousCropFilter(cropDims, dense);

    return filterStr;
  } catch (e) {
    logger.warn(`Dynamic crop generation failed: ${e.message.substring(0, 200)}`);
    // Fallback: center crop
    const cropDims = computeCropDimensions(srcW, srcH);
    if (!cropDims) {
      return `scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
    }
    const centerX = Math.floor((srcW - cropDims.cropW) / 4) * 2;
    return `crop=${cropDims.cropW}:${cropDims.cropH}:${Math.max(0, centerX)}:${cropDims.cropY},scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos`;
  }
}

// ────────────────────────────────────────────────────────────
// END: Dynamic Crop Functions
// ────────────────────────────────────────────────────────────

/**
 * Smart crop with Gemini CLI feedback loop (legacy/static)
 */
async function smartCrop(videoPath, outputPath, options = {}) {
  const country = options.country || 'Global';
  const startTime = Math.min(options.startTime || 3, 30);
  const duration = Math.min(options.duration || 30, 60);
  const tmpDir = path.dirname(outputPath);

  logger.info(`Smart cropping: ${path.basename(videoPath)} → 9:16 portrait`);

  const dims = probeVideo(videoPath);
  const srcW = dims.width;
  const srcH = dims.height;
  logger.info(`Source: ${srcW}x${srcH}`);

  let cropOffsetX = 0;
  let zoom = 1.0;
  let cropFilter = buildCropFilter(srcW, srcH, cropOffsetX, zoom);
  
  logger.info(`Initial crop filter: ${cropFilter}`);

  const gemini = getGeminiCLI();
  const qaPositions = [duration / 4, duration / 2, duration * 3 / 4].map(t => startTime + t);

  if (true) {
    const targetHeight = 1920;
    const targetWidth = 1080;
    const scaleFactor = targetHeight / srcH;
    const scaledWidth = Math.round(srcW * scaleFactor);
    const maxCropX = scaledWidth - targetWidth;

    logger.info('Analyzing video with YOLO to locate subject...');
    const yoloDir = path.join(tmpDir, `yolo_frames_${Date.now()}`);
    fs.mkdirSync(yoloDir, { recursive: true });
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

      return { success: true, outputPath: finalOutput, cropFilter, zoom, cropOffsetX };
    }
  } catch (e) {
    logger.warn(`Final crop failed: ${e.message.substring(0, 100)}`);
  }

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
  computeCropDimensions,
  smoothCropPositions,
  generateDynamicCropFilter,
};