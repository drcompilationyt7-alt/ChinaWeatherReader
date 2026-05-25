/**
 * Clip Editor - YouTube Shorts Creator
 * 
 * SIMPLIFIED: No intro, no text overlay.
 * Just pads to 9:16 and adds voiceover on top of original audio.
 * The voiceover (via edge-tts) already says what country it's from.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('ClipEditor');
const SHORTS_W = 1080;
const SHORTS_H = 1920;

async function createShort(videoPath, options) {
  const outputPath = options.outputPath || videoPath.replace(/\.\w+$/, '_shorts.mp4');
  const tmpDir = path.dirname(outputPath);
  const voiceoverPath = options.voiceoverPath || null;
  const startTime = Math.min(options.startTime || 5, 30);
  const duration = Math.min(options.duration || 28, 60);

  logger.info(`Creating short (${duration}s)`);

  try {
    // Step 1: Create the padded clip (9:16) with optional voiceover
    // Just a single ffmpeg call to avoid any complex concat issues
    const padFilter = `scale='min(${SHORTS_W},iw)':'min(${SHORTS_H},ih)':force_original_aspect_ratio=decrease,pad=${SHORTS_W}:${SHORTS_H}:(ow-iw)/2:(oh-ih)/2:color=black`;

    if (voiceoverPath && fs.existsSync(voiceoverPath)) {
      // Measure voiceover duration
      let voDur = 4;
      try {
        const probeOut = execSync(
          `ffprobe -i "${voiceoverPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`,
          { timeout: 5000, encoding: 'utf8' }
        ).trim();
        if (probeOut) voDur = Math.ceil(parseFloat(probeOut));
      } catch {}
      voDur = Math.min(voDur, 8);

      // SIMPLE: pad video, mix original audio + voiceover (duck original during vo)
      // [0:a] original audio, [1:a] voiceover
      // amix blends them together, adelay shifts voiceover to start after 1s
      execSync(
        `ffmpeg -y -ss ${startTime} -i "${videoPath}" -i "${voiceoverPath}" -t ${duration} ` +
        `-filter_complex ` +
        `"[0:v]${padFilter}[v];` +
        `[0:a]asplit=2[a1][a2];` +
        `[a1]volume=0.1[a_duck];` +
        `[1:a]adelay=1000[a_vo];` +
        `[a_duck][a_vo]amix=inputs=2:duration=first[a]" ` +
        `-map "[v]" -map "[a]" ` +
        `-c:v libx264 -preset ultrafast -crf 23 -c:a aac ` +
        `-shortest "${outputPath}" 2>/dev/null`,
        { timeout: 120000 }
      );
    } else {
      // No voiceover: just pad, keep original audio
      execSync(
        `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} ` +
        `-vf "${padFilter}" ` +
        `-c:v libx264 -preset ultrafast -crf 23 -c:a aac -b:a 128k ` +
        `-shortest "${outputPath}" 2>/dev/null`,
        { timeout: 120000 }
      );
    }

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Short: ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB`);
      return outputPath;
    }

    // Fallback: try without any filter (just trim and copy)
    logger.warn('Filter failed, trying trim-only...');
    execSync(
      `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} -c copy "${outputPath}" 2>/dev/null`,
      { timeout: 60000 }
    );
    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Short (copy): ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB`);
      return outputPath;
    }

  } catch (error) {
    logger.warn(`Short failed: ${error.message.substring(0, 200)}`);
  }
  return null;
}

module.exports = { createShort };
