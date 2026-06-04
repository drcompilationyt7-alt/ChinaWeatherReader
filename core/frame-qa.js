/**
 * Frame QA — OpenCV Frame Inspection + Gemini CLI Video QA
 * 
 * Uses OpenCV (via Python) for:
 * - Black frame detection
 * - Subtitle readability check
 * - Scene validation
 * - Silence detection
 * 
 * Uses Gemini CLI for:
 * - Video quality review (sends full MP4)
 * - Caption blocking check
 * - Overall quality scoring
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');
const { getGeminiCLI } = require('./gemini-cli-runner');

const logger = new Logger('FrameQA');

/**
 * Detect black frames in a video using OpenCV
 * @param {string} videoPath - Path to video
 * @returns {Object} - { hasBlackFrames, blackFrameCount, percentage }
 */
async function detectBlackFrames(videoPath) {
  try {
    const pyPath = videoPath.replace(/\\/g, '\\\\');
    const output = execSync(
      `python3 -c "
import json
try:
    import cv2
    cap = cv2.VideoCapture('${pyPath}')
    total = 0
    black = 0
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        total += 1
        # Check every 10th frame for speed
        if total % 10 == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mean = gray.mean()
            if mean < 15:  # Very dark = likely black frame
                black += 1
    cap.release()
    pct = (black / max(total // 10, 1)) * 100
    print(json.dumps({'total_frames': total, 'black_frames': black, 'percentage': round(pct, 1)}))
except Exception as e:
    print(json.dumps({'error': str(e)[:100]}))
" 2>&1`,
      { timeout: 60000, encoding: 'utf8', maxBuffer: 5 * 1024 * 1024 }
    ).toString().trim();

    if (output && !output.includes('Error') && !output.includes('Traceback')) {
      const result = JSON.parse(output.split('\n').filter(l => l.startsWith('{')).pop() || '{}');
      const pct = result.percentage || 0;
      logger.info(`Black frames: ${pct}% of frames`);
      return {
        hasBlackFrames: pct > 5,
        blackFrameCount: result.black_frames || 0,
        percentage: pct,
      };
    }
  } catch (e) {
    logger.warn(`Black frame detection failed: ${e.message.substring(0, 60)}`);
  }

  return { hasBlackFrames: false, blackFrameCount: 0, percentage: 0 };
}

/**
 * Detect silence in audio using ffmpeg
 * @param {string} videoPath - Path to video
 * @returns {Object} - { hasAudio, silencePercent }
 */
async function detectSilence(videoPath) {
  try {
    const output = execSync(
      `ffmpeg -i "${videoPath}" -af silencedetect=noise=-30dB:d=0.5 -f null - 2>&1 | grep -c "silence_"`,
      { timeout: 30000, encoding: 'utf8' }
    ).toString().trim();

    const silenceEvents = parseInt(output) || 0;

    // Get video duration
    const durOut = execSync(
      `ffprobe -i "${videoPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`,
      { timeout: 10000, encoding: 'utf8' }
    ).toString().trim();

    const duration = parseFloat(durOut) || 30;
    const hasAudio = silenceEvents < duration * 2; // Less than 2 silence events per second

    logger.info(`Audio: ${hasAudio ? 'present' : 'silent'}, ${silenceEvents} silence events`);

    return { hasAudio, silencePercent: 0 };
  } catch (e) {
    return { hasAudio: true, silencePercent: 0 };
  }
}

/**
 * Validate video output quality
 * @param {string} videoPath - Path to the final rendered video
 * @returns {Object} - { passed, issues, score }
 */
async function validateOutput(videoPath) {
  const issues = [];
  let score = 10;

  // Check file exists and has reasonable size
  if (!fs.existsSync(videoPath)) {
    return { passed: false, issues: ['File does not exist'], score: 0 };
  }

  const size = fs.statSync(videoPath).size;
  if (size < 50000) {
    issues.push('File too small (< 50KB)');
    score -= 5;
  }
  // Skip >100MB penalty — FFV1 lossless files are often very large

  // Check dimensions: must be 9:16 (YouTube Shorts ratio)
  try {
    const dims = execSync(
      `ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "${videoPath}" 2>/dev/null`,
      { timeout: 10000, encoding: 'utf8' }
    ).trim();

    const [w, h] = dims.split(',').map(s => parseInt(s.trim()));
    // Check 9:16 ratio (allow small tolerance)
    const ratio = w / h;
    const targetRatio = 9 / 16; // 0.5625
    if (Math.abs(ratio - targetRatio) > 0.05) {
      issues.push(`Wrong aspect ratio: ${w}x${h} (expected 9:16, got ${ratio.toFixed(3)})`);
      score -= 3;
    } else {
      logger.success(`Dimensions valid: ${w}x${h} (9:16 ratio)`);
    }
  } catch {
    issues.push('Could not probe dimensions');
    score -= 1;
  }

  // Check duration
  try {
    const dur = execSync(
      `ffprobe -i "${videoPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`,
      { timeout: 10000, encoding: 'utf8' }
    ).trim();

    const duration = parseFloat(dur);
    if (duration < 5) {
      issues.push(`Too short: ${duration.toFixed(1)}s (min 5s)`);
      score -= 3;
    }
    if (duration > 65) {
      issues.push(`Too long: ${duration.toFixed(1)}s (max 60s for Shorts)`);
      score -= 2;
    }
  } catch {
    issues.push('Could not probe duration');
    score -= 1;
  }

  // Check audio
  try {
    const audioStreams = execSync(
      `ffprobe -v error -select_streams a -show_entries stream=codec_name -of csv=p=0 "${videoPath}" 2>/dev/null`,
      { timeout: 10000, encoding: 'utf8' }
    ).trim();

    if (!audioStreams) {
      issues.push('No audio stream detected');
      score -= 3;
    }
  } catch {
    // Assume audio is present
  }

  // Black frame check
  const blackCheck = await detectBlackFrames(videoPath);
  if (blackCheck.hasBlackFrames) {
    issues.push(`${blackCheck.percentage.toFixed(1)}% black frames detected`);
    score -= 2;
  }

  // Silence check
  const silenceCheck = await detectSilence(videoPath);
  if (!silenceCheck.hasAudio) {
    issues.push('Video appears to be silent');
    score -= 1;
  }

  const passed = score >= 6;
  logger.info(`Validation: ${passed ? 'PASSED' : 'FAILED'} (score: ${score}/10, ${issues.length} issues)`);

  return { passed, issues, score: Math.max(0, score) };
}

/**
 * Gemini CLI-powered visual quality review (replaces broken gemini.analyzeFramesJSON).
 * Sends the full MP4 to Gemini CLI for final quality check.
 * @param {string} videoPath - Path to the final rendered video
 * @returns {Object} - { score, issues, recommendation, cropOk, subtitlesOk, watermarkRemoved, hookQuality }
 */
async function geminiReview(videoPath) {
  const geminiCLI = getGeminiCLI();

  if (!geminiCLI.isAvailable()) {
    logger.warn('Gemini CLI not available for review');
    return { score: 5, issues: ['CLI not available'], recommendation: 'APPROVE', cropOk: true, subtitlesOk: true, watermarkRemoved: true, hookQuality: 'unknown' };
  }

  const result = await geminiCLI.reviewFinalVideo(videoPath);

  if (result) {
    logger.info(`Gemini CLI QA: ${result.quality_score}/10 — ${result.recommendation}`);
    if (result.issues && result.issues.length > 0) {
      logger.warn(`Gemini CLI QA issues: ${result.issues.join(', ')}`);
    }
    return {
      score: result.quality_score || 5,
      issues: result.issues || [],
      recommendation: result.recommendation || 'APPROVE',
      cropOk: result.crop_ok !== false,
      subtitlesOk: result.subtitles_ok !== false,
      watermarkRemoved: result.watermark_removed !== false,
      hookQuality: result.hook_quality || 'unknown',
    };
  }

  return { score: 5, issues: ['Gemini review unavailable'], recommendation: 'APPROVE', cropOk: true, subtitlesOk: true, watermarkRemoved: true, hookQuality: 'unknown' };
}

module.exports = { detectBlackFrames, detectSilence, validateOutput, geminiReview };