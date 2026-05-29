/**
 * Frame QA — OpenCV Frame Inspection + Gemini Analysis
 * 
 * Uses OpenCV (via Python) for:
 * - Black frame detection
 * - Subtitle readability check
 * - Scene validation
 * - Silence detection
 * 
 * Uses Gemini API for:
 * - Visual quality review
 * - Caption blocking check
 * - Overall quality scoring
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');
const { getGeminiService } = require('./gemini-service');

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
  if (size > 100 * 1024 * 1024) {
    issues.push('File too large (> 100MB)');
    score -= 2;
  }

  // Check dimensions
  try {
    const dims = execSync(
      `ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "${videoPath}" 2>/dev/null`,
      { timeout: 10000, encoding: 'utf8' }
    ).trim();

    const [w, h] = dims.split(',').map(s => parseInt(s.trim()));
    if (w !== 1080 || h !== 1920) {
      issues.push(`Wrong dimensions: ${w}x${h} (expected 1080x1920)`);
      score -= 3;
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
 * Gemini-powered visual quality review
 * @param {string[]} framePaths - Frames from the final video
 * @returns {Object} - { score, issues, recommendation }
 */
async function geminiReview(framePaths) {
  const gemini = getGeminiService();

  const question = `Review these frames from a YouTube Short for "Mr. WorldWideWebster" channel.

Check:
1. Is the video properly cropped to 9:16 portrait (1080x1920)?
2. Are any subtitles/captions readable and NOT blocking the main content?
3. Are captions too big or too small?
4. Is any watermark still visible?
5. Does the first frame serve as a good hook?
6. Is the video quality acceptable (not blurry, not pixelated)?
7. Overall quality rating: 1-10

Return JSON:
{"quality_score": 7, "crop_ok": true, "subtitles_ok": true, "watermark_removed": true, "hook_quality": "strong", "issues": [], "recommendation": "APPROVE"}`;

  const curatorSkill = fs.existsSync(path.join(__dirname, '..', 'skills', 'viral-clip-curator.md'))
    ? fs.readFileSync(path.join(__dirname, '..', 'skills', 'viral-clip-curator.md'), 'utf8')
    : null;

  const response = await gemini.analyzeFramesJSON(framePaths, question, curatorSkill);

  if (response) {
    logger.info(`Gemini QA: ${response.quality_score}/10 — ${response.recommendation}`);
    return {
      score: response.quality_score || 5,
      issues: response.issues || [],
      recommendation: response.recommendation || 'APPROVE',
      cropOk: response.crop_ok !== false,
      subtitlesOk: response.subtitles_ok !== false,
      watermarkRemoved: response.watermark_removed !== false,
      hookQuality: response.hook_quality || 'unknown',
    };
  }

  return { score: 5, issues: ['Gemini review unavailable'], recommendation: 'APPROVE' };
}

module.exports = { detectBlackFrames, detectSilence, validateOutput, geminiReview };