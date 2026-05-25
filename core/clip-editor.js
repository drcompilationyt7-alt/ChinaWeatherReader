/**
 * Clip Editor - YouTube Shorts Creator
 * 
 * SIMPLIFIED: No intro, no text overlay.
 * Just pads to 9:16 and adds voiceover on top of original audio.
 * SHOWS FULL FFMPEG ERRORS so we can debug.
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
  const voiceoverPath = options.voiceoverPath || null;
  const startTime = Math.min(options.startTime || 5, 30);
  const duration = Math.min(options.duration || 28, 60);

  logger.info(`Creating short (${duration}s, voiceover: ${voiceoverPath ? 'yes' : 'no'})`);

  try {
    // Check video has audio stream
    try {
      const hasAudio = execSync(
        `ffprobe -i "${videoPath}" -show_streams -select_streams a -loglevel error 2>/dev/null | head -1`,
        { timeout: 5000, encoding: 'utf8' }
      ).trim();
      logger.info(`Audio streams: ${hasAudio ? 'yes' : 'no'}`);
    } catch {}

    const padFilter = `scale='min(${SHORTS_W},iw)':'min(${SHORTS_H},ih)':force_original_aspect_ratio=decrease,pad=${SHORTS_W}:${SHORTS_H}:(ow-iw)/2:(oh-ih)/2:color=black`;

    if (voiceoverPath && fs.existsSync(voiceoverPath)) {
      // SIMPLE: trim + pad video, mix original audio with voiceover
      // Voiceover plays after 1s, original audio is reduced during that time
      let voDur = 4;
      try {
        const probeOut = execSync(
          `ffprobe -i "${voiceoverPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`,
          { timeout: 5000, encoding: 'utf8' }
        ).trim();
        if (probeOut) voDur = Math.ceil(parseFloat(probeOut));
      } catch {}
      voDur = Math.min(voDur, 8);

      logger.info(`Voiceover duration: ${voDur}s`);

      // Build command WITHOUT 2>/dev/null so we see errors
      const cmd = [
        `ffmpeg -y -ss ${startTime} -i "${videoPath}" -i "${voiceoverPath}" -t ${duration}`,
        `-filter_complex "[0:v]${padFilter}[v];[0:a]volume=enable='between(t,1,${1+voDur})':volume=0.1[ad];[1:a]adelay=1000[av];[ad][av]amix=inputs=2:duration=first[a]"`,
        `-map "[v]" -map "[a]" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${outputPath}"`
      ].join(' ');

      logger.info(`Running: ffmpeg -y -ss ${startTime} -i ... -i ... -t ${duration} -filter_complex ...`);
      execSync(cmd, { timeout: 120000, maxBuffer: 50*1024*1024, encoding: 'utf8' });
    } else {
      // No voiceover: just pad
      const cmd = `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} -vf "${padFilter}" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -b:a 128k -shortest "${outputPath}"`;
      execSync(cmd, { timeout: 120000, maxBuffer: 50*1024*1024, encoding: 'utf8' });
    }

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Short: ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB`);
      return outputPath;
    }

    // Fallback: trim only, no filters
    logger.warn('Pad/voiceover failed, trying simple trim...');
    execSync(
      `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} -c copy "${outputPath}"`,
      { timeout: 60000 }
    );
    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Short (trim only): ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB`);
      return outputPath;
    }

  } catch (error) {
    // Show the FULL ffmpeg error
    const errMsg = error.stderr || error.stdout || error.message || '';
    logger.warn(`FFMPEG ERROR (first 500 chars): ${errMsg.toString().substring(0, 500)}`);
    logger.warn(`Short failed`);
  }
  return null;
}

module.exports = { createShort };
