/**
 * Watermark Utility — Semi-transparent logo + @Mr.WorldWideWebster overlay
 * 
 * Overlays the channel profile image and text at the bottom-right corner
 * of any video. The watermark is semi-transparent (55%) so it doesn't
 * interfere with viewing but is visible enough to prevent content theft.
 */
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const { Logger } = require('./logger');

const logger = new Logger('Watermark');

const LOGO_SIZE = 50;
const MARGIN_RIGHT = 20;
const MARGIN_BOTTOM = 65;
const FONT_SIZE = 20;
const TEXT = '@Mr.WorldWideWebster';

const PROFILE_IMAGE = path.join(__dirname, 'assets', 'mrw-logo.png');
const OUTPUT_W = 1080;
const OUTPUT_H = 1920;

/**
 * Add watermark (logo + text) at bottom-right of the video.
 * 
 * @param {string} videoPath - Input video path
 * @param {string} outputPath - Output path for watermarked video
 * @returns {string|null} - Output path or null on failure
 */
async function addWatermark(videoPath, outputPath) {
  if (!fs.existsSync(videoPath)) {
    logger.warn(`Video not found: ${videoPath}`);
    return null;
  }

  if (!fs.existsSync(PROFILE_IMAGE)) {
    logger.warn(`Profile image missing at ${PROFILE_IMAGE} — skipping watermark, copying video`);
    try { fs.copyFileSync(videoPath, outputPath); return outputPath; } catch { return null; }
  }

  logger.info(`Adding watermark to ${path.basename(videoPath)}`);

  // Probe input video for audio stream
  let hasAudio = true;
  try {
    const probeOut = execSync(
      `ffprobe -v error -show_entries stream=codec_type -of csv=p=0 "${videoPath}"`,
      { timeout: 10000, encoding: 'utf8' }
    ).trim();
    const streamTypes = probeOut.split('\n').filter(Boolean);
    hasAudio = streamTypes.includes('audio');
  } catch (e) {
    logger.warn(`Failed to probe video for audio: ${(e.message || '').substring(0, 60)}`);
  }

  try {
    // Build FFmpeg command — conditionally map audio if present
    let cmd;
    if (hasAudio) {
      cmd = `ffmpeg -y -i "${videoPath}" -i "${PROFILE_IMAGE}" ` +
        `-filter_complex ` +
        `"[1:v]scale=${LOGO_SIZE}:${LOGO_SIZE}:flags=lanczos,format=rgba[logo];` +
        `[0:v][logo]overlay=W-w-${MARGIN_RIGHT}:H-h-${MARGIN_BOTTOM}:format=auto,` +
        `drawtext=text='${TEXT}':` +
        `fontcolor=white@0.55:` +
        `fontsize=${FONT_SIZE}:` +
        `x=W-tw-${MARGIN_RIGHT}:` +
        `y=H-th-${MARGIN_RIGHT-10}:` +
        `shadowcolor=black@0.55:shadowx=1:shadowy=1[out]" ` +
        `-map "[out]" -map 0:a -c:v libx264 -preset medium -crf 0 -c:a copy ` +
        `-pix_fmt yuv420p -shortest "${outputPath}"`;
    } else {
      cmd = `ffmpeg -y -i "${videoPath}" -i "${PROFILE_IMAGE}" ` +
        `-filter_complex ` +
        `"[1:v]scale=${LOGO_SIZE}:${LOGO_SIZE}:flags=lanczos,format=rgba[logo];` +
        `[0:v][logo]overlay=W-w-${MARGIN_RIGHT}:H-h-${MARGIN_BOTTOM}:format=auto,` +
        `drawtext=text='${TEXT}':` +
        `fontcolor=white@0.55:` +
        `fontsize=${FONT_SIZE}:` +
        `x=W-tw-${MARGIN_RIGHT}:` +
        `y=H-th-${MARGIN_RIGHT-10}:` +
        `shadowcolor=black@0.55:shadowx=1:shadowy=1[out]" ` +
        `-map "[out]" -c:v libx264 -preset medium -crf 0 ` +
        `-pix_fmt yuv420p -shortest "${outputPath}"`;
    }

    execSync(cmd, { timeout: 180000, maxBuffer: 500 * 1024 * 1024 });

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Watermark added: ${(fs.statSync(outputPath).size / 1024 / 1024).toFixed(1)}MB`);
      return outputPath;
    }
  } catch (e) {
    logger.warn(`Watermark failed: ${(e.message || '').substring(0, 80)}`);
  }

  try { fs.copyFileSync(videoPath, outputPath); return outputPath; } catch { return null; }
}

module.exports = { addWatermark };