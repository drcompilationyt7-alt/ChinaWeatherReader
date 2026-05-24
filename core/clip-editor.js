/**
 * Clip Editor - YouTube Shorts Creator
 * 
 * Creates proper YT Shorts (9:16, 1080x1920):
 * [0-4s] INTRO: Blurred background of clip + text overlay
 *   "Here we present you..."
 *   "[Content/Channel Name]"
 *   "From [Country] 🌍"
 * [4-30s] FULL CLIP: Original video, original audio, optional captions
 *
 * For MEME: Show meme name, fit to 9:16, keep original audio
 * For EXPLAINER: Same intro but voiceover plays during clip with ducked audio
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('ClipEditor');

const SHORTS_W = 1080;
const SHORTS_H = 1920;

/**
 * Create a YouTube Short from source video
 */
async function createShort(videoPath, options) {
  const type = options.type || 'clip'; // 'clip', 'streamer', 'explainer'
  const outputPath = options.outputPath || videoPath.replace(/\.\w+$/, '_shorts.mp4');
  const tmpDir = path.dirname(outputPath);
  
  const introText = options.introText || 'Here we present you';
  const titleText = options.titleText || 'This content';
  const countryText = options.countryText ? `From ${options.countryText}` : 'Global';
  const captionText = options.textOverlay || '';
  
  const startTime = options.startTime || 5;
  const duration = Math.min(options.duration || 25, 60);
  const voiceoverPath = options.voiceoverPath || null;
  
  const baseName = `short_${Date.now()}`;
  logger.info(`Creating Short: "${titleText}" (${duration}s)`);
  
  try {
    // Step 1: Extract a frame for the intro background
    const frameFile = path.join(tmpDir, `${baseName}_frame.jpg`);
    execSync(`ffmpeg -y -ss ${startTime} -i "${videoPath}" -vframes 1 -q:v 2 "${frameFile}" 2>/dev/null`, { timeout: 10000 });

    // Step 2: Create intro video (blurred background + text)
    const introFile = path.join(tmpDir, `${baseName}_intro.mp4`);
    const introDuration = 4;
    
    // Create intro with ffmpeg: blurred image + text overlays
    // Text styling: centered, bold font, nice font family
    const introFilter = `
      [0:v]scale=${SHORTS_W}:${SHORTS_H}:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H},boxblur=20:5[b];
      [b]drawtext=text='${introText.replace(/'/g, "\\'")}':
        fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:
        fontsize=48:fontcolor=white:
        x=(w-text_w)/2:y=h*0.3:
        shadowx=2:shadowy=2:shadowcolor=black@0.5,
      drawtext=text='${titleText.replace(/'/g, "\\'")}':
        fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:
        fontsize=64:fontcolor=#FFD700:
        x=(w-text_w)/2:y=h*0.45:
        shadowx=2:shadowy=2:shadowcolor=black@0.5,
      drawtext=text='${countryText.replace(/'/g, "\\'")}':
        fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:
        fontsize=36:fontcolor=white:
        x=(w-text_w)/2:y=h*0.55:
        shadowx=2:shadowy=2:shadowcolor=black@0.5[out]
    `.replace(/\s+/g, ' ').trim();
    
    execSync(`ffmpeg -y -loop 1 -i "${frameFile}" -t ${introDuration} -vf "${introFilter}" -c:v libx264 -preset ultrafast -crf 23 -pix_fmt yuv420p "${introFile}" 2>/dev/null`, { timeout: 30000 });

    // Step 3: Extract and fit the clip segment to 9:16
    const clipFile = path.join(tmpDir, `${baseName}_clip.mp4`);
    
    if (voiceoverPath && fs.existsSync(voiceoverPath)) {
      // EXPLAINER: clip with voiceover + ducked original audio
      const audioMixFilter = `
        [1:a]volume=1[a_orig];
        [2:a]adelay=0|0[a_vo];
        [a_orig]volume=enable='between(t,1,${introDuration + 1})':volume=0.15[a_ducked];
        [a_ducked][a_vo]amix=inputs=2:duration=first[outa]
      `.replace(/\s+/g, ' ').trim();
      
      execSync(
        `ffmpeg -y -ss ${startTime} -i "${videoPath}" -i "${videoPath}" -i "${voiceoverPath}" -t ${duration} ` +
        `-filter_complex "[0:v]scale=${SHORTS_W}:${SHORTS_H}:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H}[outv]; ${audioMixFilter}" ` +
        `-map "[outv]" -map "[outa]" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${clipFile}" 2>/dev/null`,
        { timeout: 120000 }
      );
    } else {
      // CLIP/STREAMER: just fit and add captions if needed
      let clipFilter = `[0:v]scale=${SHORTS_W}:${SHORTS_H}:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H}[outv]`;
      
      if (captionText && captionText.length > 0) {
        // Add subtitle/caption overlay at bottom
        clipFilter = `[0:v]scale=${SHORTS_W}:${SHORTS_H}:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H},` +
          `drawtext=text='${captionText.replace(/'/g, "\\'")}':` +
          `fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:` +
          `fontsize=42:fontcolor=white:` +
          `x=(w-text_w)/2:y=h-text_h-80:` +
          `box=1:boxcolor=black@0.6:boxborderw=10` +
          `[outv]`;
      }
      
      execSync(
        `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} ` +
        `-filter_complex "${clipFilter}" ` +
        `-map "[outv]" -map 0:a? -c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${clipFile}" 2>/dev/null`,
        { timeout: 120000 }
      );
    }

    // Step 4: Concatenate intro + clip
    if (fs.existsSync(introFile) && fs.existsSync(clipFile)) {
      const concatFile = path.join(tmpDir, `${baseName}_concat.txt`);
      fs.writeFileSync(concatFile, `file '${introFile.replace(/'/g, "'\\''")}'\nfile '${clipFile.replace(/'/g, "'\\''")}'`);
      
      execSync(
        `ffmpeg -y -f concat -safe 0 -i "${concatFile}" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -movflags +faststart "${outputPath}" 2>/dev/null`,
        { timeout: 120000 }
      );

      // Cleanup
      try { fs.unlinkSync(frameFile); } catch {}
      try { fs.unlinkSync(introFile); } catch {}
      try { fs.unlinkSync(clipFile); } catch {}
      try { fs.unlinkSync(concatFile); } catch {}

      if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
        logger.success(`Short created: ${path.basename(outputPath)} (${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB)`);
        return outputPath;
      }
    }

    // Fallback: just crop to 9:16
    logger.warn('Intro+clip failed, trying simple crop...');
    const fallbackCmd = `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} ` +
      `-vf "scale=${SHORTS_W}:${SHORTS_H}:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H}" ` +
      `-c:v libx264 -preset ultrafast -crf 28 -c:a aac -shortest "${outputPath}" 2>/dev/null`;
    execSync(fallbackCmd, { timeout: 60000 });
    
    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Fallback short: ${path.basename(outputPath)}`);
      return outputPath;
    }
  } catch (error) {
    logger.warn(`Short creation failed: ${error.message.substring(0, 100)}`);
  }

  return null;
}

/**
 * Generate a simple voiceover using edge-tts
 */
async function generateVoiceover(text, outputPath) {
  try {
    const safeText = text.replace(/"/g, '\\"');
    execSync(`edge-tts --voice "en-US-JennyNeural" --text "${safeText}" --write-media "${outputPath}" 2>/dev/null`, { timeout: 30000 });
    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 1000) {
      logger.info(`Voiceover: ${path.basename(outputPath)}`);
      return outputPath;
    }
  } catch (error) {
    logger.warn(`Voiceover: ${error.message.substring(0, 80)}`);
  }
  return null;
}

module.exports = { createShort, generateVoiceover };
