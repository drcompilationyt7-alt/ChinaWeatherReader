/**
 * AI Clipper  ESmart Video Clipping with Adaptive Multi-Subject Centering
 * 
 * Combines YOLO subject detection with intelligent cropping strategies:
 * - 1 subject: Center on that person
 * - 2-3 subjects: Center on group average
 * - 4+ subjects: Focus on center 3 (median + left + right neighbor)
 * 
 * Handles:
 * - Already 9:16 shorts (just extract segment)
 * - Landscape videos (smart crop with subject tracking)
 * - Compilations (detect and handle intros/outros)
 * - Dance videos with multiple performers
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('AIClipper');

const SHORTS_W = 1080;
const SHORTS_H = 1920;
const TARGET_RATIO = SHORTS_W / SHORTS_H; // 0.5625

/**
 * Parse JSON from YOLO output
 */
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
 * Check if video is already close to 9:16 aspect ratio
 */
function isAlreadyPortrait(srcW, srcH) {
  const ratio = srcW / srcH;
  return Math.abs(ratio - TARGET_RATIO) < 0.05;
}

/**
 * Detect subjects in a single frame using YOLO
 * Returns array of detections with center_x, center_y, bbox
 */
function detectSubjectsInFrame(framePath) {
  try {
    const yoloOut = execSync(
      `python3 "${path.join(__dirname, 'yolo-crop.py')}" "${framePath}"`,
      { timeout: 30000, encoding: 'utf8' }
    ).toString().trim();
    
    const result = parseJsonFromOutput(yoloOut);
    
    if (result.subject === 'none' || !result.center_x >= 0) {
      return [];
    }
    
    // Return detection with bbox info
    return [{
      center_x: result.center_x,
      center_y: result.center_y || 0,
      bbox: result.bbox || [0, 0, 0, 0],
      subject: result.subject,
      confidence: result.confidence || 0
    }];
  } catch (e) {
    logger.warn(`YOLO detection failed: ${(e.message || '').substring(0, 60)}`);
    return [];
  }
}

/**
 * Get focus subjects from detections
 * - 1 subject: return it
 * - 2-3 subjects: return all
 * - 4+ subjects: return center 3 (median + left + right neighbor)
 */
function getFocusSubjects(detections) {
  if (detections.length === 0) {
    return [];
  }
  
  if (detections.length <= 3) {
    return detections; // Use all if 3 or fewer
  }
  
  // Sort by horizontal position (left to right)
  const sorted = [...detections].sort((a, b) => a.center_x - b.center_x);
  
  // Find center index
  const centerIdx = Math.floor(sorted.length / 2);
  
  // Select center 3: center-1, center, center+1
  const focusIndices = [
    Math.max(0, centerIdx - 1),
    centerIdx,
    Math.min(sorted.length - 1, centerIdx + 1)
  ];
  
  return focusIndices.map(i => sorted[i]);
}

/**
 * Calculate crop parameters based on focus subjects
 */
function calculateCropParams(focusSubjects, srcW, srcH) {
  if (focusSubjects.length === 0) {
    // No subjects detected - use center crop
    return {
      centerX: srcW / 2,
      cropWidth: SHORTS_W,
      cropOffsetX: 0
    };
  }
  
  // Calculate average center of focus subjects
  const avgCenterX = focusSubjects.reduce((sum, s) => sum + s.center_x, 0) / focusSubjects.length;
  
  // For single subject, just center on them
  if (focusSubjects.length === 1) {
    return {
      centerX: avgCenterX,
      cropWidth: SHORTS_W,
      cropOffsetX: 0
    };
  }
  
  // For 2-3 subjects, center on group average
  return {
    centerX: avgCenterX,
    cropWidth: SHORTS_W,
    cropOffsetX: 0
  };
}

/**
 * Build the ffmpeg filter for cropping to 9:16
 */
function buildCropFilter(srcW, srcH, cropOffsetX = 0) {
  const ratio = srcW / srcH;
  const even = (n) => Math.max(2, Math.floor(n / 2) * 2);

  if (isAlreadyPortrait(srcW, srcH)) {
    // Already close to 9:16  Escale up to fill 1080x1920
    return `scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H}`;
  }

  if (ratio > TARGET_RATIO) {
    // Landscape  Escale height to match, then crop width
    const sh = SHORTS_H;
    const sw = even(sh * ratio);
    const cropX = even(Math.max(0, Math.min(sw - SHORTS_W, (sw - SHORTS_W) / 2 + cropOffsetX)));
    return `scale=${sw}:${sh}:flags=lanczos,crop=${SHORTS_W}:${SHORTS_H}:${cropX}:0`;
  } else {
    // Portrait but not 9:16  Escale width to match, then crop height
    const sw = SHORTS_W;
    const sh = even(sw / ratio);
    return `scale=${sw}:${sh}:flags=lanczos,crop=${SHORTS_W}:${SHORTS_H}:0:${even((sh - SHORTS_H) / 2)}`;
  }
}

/**
 * Main smart clip and crop function
 * 
 * @param {string} sourcePath - Input video path
 * @param {string} outputPath - Output video path
 * @param {Object} options - { startTime, endTime, country }
 * @returns {Object} - { success, outputPath, cropFilter, detections }
 */
async function smartClipAndCrop(sourcePath, outputPath, options = {}) {
  const startTime = options.startTime || 0;
  const endTime = options.endTime;
  const duration = endTime ? endTime - startTime : 30;
  const tmpDir = path.dirname(outputPath);
  
  logger.info(`Smart clip and crop: ${path.basename(sourcePath)} ↁE9:16 portrait`);
  
  // Step 1: Probe source dimensions
  const dims = probeVideo(sourcePath);
  const srcW = dims.width;
  const srcH = dims.height;
  logger.info(`Source: ${srcW}x${srcH}, duration: ${dims.duration}s`);
  
  // Step 2: Check if already 9:16
  if (isAlreadyPortrait(srcW, srcH) && srcW >= SHORTS_W && srcH >= SHORTS_H) {
    logger.info('Source is already 9:16 at sufficient resolution  Ejust extracting segment');
    
    // Just extract the time segment without re-encoding
    const clipFilter = endTime ? `-to ${duration}` : '';
    try {
      execSync(
        `ffmpeg -y -ss ${startTime} -i "${sourcePath}" ${clipFilter} ` +
        `-c copy "${outputPath}" 2>/dev/null`,
        { timeout: 60000 }
      );
      
      if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
        const sizeMB = (fs.statSync(outputPath).size / 1024 / 1024).toFixed(1);
        logger.success(`Extracted segment: ${sizeMB}MB (no re-encode)`);
        return {
          success: true,
          outputPath,
          cropFilter: 'none (already 9:16)',
          detections: []
        };
      }
    } catch (e) {
      logger.warn(`Direct copy failed, falling back to re-encode: ${(e.message || '').substring(0, 60)}`);
    }
    
    // Fallback: re-encode if direct copy fails
    try {
      execSync(
        `ffmpeg -y -ss ${startTime} -i "${sourcePath}" ${clipFilter} ` +
        `-c:v ffv1 -level 3 -coder 1 -context 1 -g 1 -slices 16 -slicecrc 1 ` +
        `-pix_fmt yuv444p10le -c:a flac -ar 48000 -shortest "${outputPath}" 2>/dev/null`,
        { timeout: 120000 }
      );
      
      if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
        const sizeMB = (fs.statSync(outputPath).size / 1024 / 1024).toFixed(1);
        logger.success(`Extracted segment: ${sizeMB}MB (re-encoded)`);
        return {
          success: true,
          outputPath,
          cropFilter: 'none (already 9:16)',
          detections: []
        };
      }
    } catch (e) {
      logger.error(`Segment extraction failed: ${(e.message || '').substring(0, 100)}`);
    }
    
    return { success: false, outputPath: null };
  }
  
  // Step 3: For landscape/non-9:16 videos, detect subjects and calculate crop
  logger.info('Analyzing video for subject detection...');
  
  // Sample frames for subject detection
  const samplePositions = [];
  const sampleInterval = Math.max(1, duration / 6); // Sample every ~5 seconds or 6 samples
  for (let t = sampleInterval; t < duration - 1; t += sampleInterval) {
    samplePositions.push(startTime + t);
  }
  
  // Detect subjects in each sample frame
  const allDetections = [];
  const sampleDir = path.join(tmpDir, `sample_frames_${Date.now()}`);
  fs.mkdirSync(sampleDir, { recursive: true });
  
  for (const pos of samplePositions) {
    const framePath = path.join(sampleDir, `frame_${pos.toFixed(1)}.jpg`);
    try {
      execSync(
        `ffmpeg -y -ss ${pos.toFixed(2)} -i "${sourcePath}" -vframes 1 -q:v 2 "${framePath}" 2>/dev/null`,
        { timeout: 10000 }
      );
      
      if (fs.existsSync(framePath) && fs.statSync(framePath).size > 1000) {
        const detections = detectSubjectsInFrame(framePath);
        if (detections.length > 0) {
          allDetections.push(...detections);
          logger.info(`  Frame @${(pos - startTime).toFixed(1)}s: ${detections.length} subject(s) detected`);
        }
      }
    } catch (e) {
      logger.warn(`Frame extraction @${pos.toFixed(1)}s failed: ${(e.message || '').substring(0, 40)}`);
    }
  }
  
  try { fs.rmSync(sampleDir, { recursive: true, force: true }); } catch {}
  
  // Step 4: Get focus subjects and calculate crop parameters
  let cropOffsetX = 0;
  
  if (allDetections.length > 0) {
    // Get the focus subjects (max 3, centered)
    const focusSubjects = getFocusSubjects(allDetections);
    const avgCenterX = focusSubjects.reduce((sum, s) => sum + s.center_x, 0) / focusSubjects.length;
    
    logger.info(`Detected ${allDetections.length} total subjects, focusing on ${focusSubjects.length} (avg center: ${avgCenterX.toFixed(0)})`);
    
    // Calculate crop offset for landscape videos
    if (srcW / srcH > TARGET_RATIO) {
      // Landscape: need to calculate horizontal crop position
      const targetHeight = SHORTS_H;
      const scaleFactor = targetHeight / srcH;
      const scaledWidth = Math.round(srcW * scaleFactor);
      const maxCropX = scaledWidth - SHORTS_W;
      
      const scaledCenterX = avgCenterX * scaleFactor;
      let cropX = scaledCenterX - (SHORTS_W / 2);
      cropOffsetX = Math.max(0, Math.min(Math.round(cropX), maxCropX));
      
      logger.info(`Crop offset: ${cropOffsetX}px (scaled center: ${scaledCenterX.toFixed(0)}, max: ${maxCropX})`);
    }
  } else {
    logger.info('No subjects detected  Eusing center crop');
  }
  
  // Step 5: Build crop filter and apply
  const cropFilter = buildCropFilter(srcW, srcH, cropOffsetX);
  logger.info(`Applying crop filter: ${cropFilter}`);
  
  const clipFilter = endTime ? `-to ${duration}` : `-t ${Math.min(duration, dims.duration)}`;
  
  try {
    execSync(
      `ffmpeg -y -ss ${startTime} -i "${sourcePath}" ${clipFilter} ` +
      `-vf "${cropFilter}" ` +
      `-c:v ffv1 -level 3 -coder 1 -context 1 -g 1 -slices 16 -slicecrc 1 ` +
      `-pix_fmt yuv444p10le -c:a flac -ar 48000 -shortest "${outputPath}" 2>/dev/null`,
      { timeout: 300000, maxBuffer: 500 * 1024 * 1024 }
    );
    
    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      const sizeMB = (fs.statSync(outputPath).size / 1024 / 1024).toFixed(1);
      logger.success(`Cropped and clipped: ${sizeMB}MB at ${SHORTS_W}x${SHORTS_H}`);
      
      return {
        success: true,
        outputPath,
        cropFilter,
        detections: allDetections
      };
    }
  } catch (e) {
    logger.error(`Crop and clip failed: ${(e.message || '').substring(0, 100)}`);
  }
  
  // Fallback: simple center crop
  logger.warn('Falling back to simple center crop');
  try {
    const fallbackFilter = isAlreadyPortrait(srcW, srcH)
      ? `scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H}`
      : buildCropFilter(srcW, srcH, 0);
    
    execSync(
      `ffmpeg -y -ss ${startTime} -i "${sourcePath}" ${clipFilter} ` +
      `-vf "${fallbackFilter}" ` +
      `-c:v ffv1 -level 3 -coder 1 -context 1 -g 1 -slices 16 -slicecrc 1 ` +
      `-pix_fmt yuv444p10le -c:a flac -ar 48000 -shortest "${outputPath}" 2>/dev/null`,
      { timeout: 180000 }
    );
    
    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      return {
        success: true,
        outputPath,
        cropFilter: fallbackFilter,
        detections: []
      };
    }
  } catch (e) {
    logger.error(`Fallback crop failed: ${(e.message || '').substring(0, 100)}`);
  }
  
  return { success: false, outputPath: null };
}

module.exports = { 
  smartClipAndCrop, 
  probeVideo, 
  isAlreadyPortrait, 
  getFocusSubjects, 
  calculateCropParams,
  buildCropFilter
};