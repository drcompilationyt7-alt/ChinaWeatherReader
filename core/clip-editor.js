/**
 * Clip Editor - YouTube Shorts Creator
 * 
 * Proper YT Shorts (9:16, 1080x1920):
 * [0-3s] INTRO: Country flag + quick text "Meme from Nigeria" / "Streamer from Japan"
 * [3-30s] FULL CLIP: Original video, original audio
 *
 * NO captions for now (user request - placement was wrong)
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('ClipEditor');

const SHORTS_W = 1080;
const SHORTS_H = 1920;

// Country flag emojis
const COUNTRY_FLAGS = {
  'Nigeria': '\ud83c\uddf3\ud83c\uddec', 'Japan': '\ud83c\uddef\ud83c\uddf5', 'Germany': '\ud83c\udde9\ud83c\uddea',
  'Australia': '\ud83c\udde6\ud83c\uddfa', 'France': '\ud83c\uddeb\ud83c\uddf7', 'Brazil': '\ud83c\udde7\ud83c\uddf7',
  'Thailand': '\ud83c\uddf9\ud83c\udded', 'India': '\ud83c\uddee\ud83c\uddf3', 'Mexico': '\ud83c\uddf2\ud83c\uddfd',
  'UK': '\ud83c\uddec\ud83c\udde7', 'South Korea': '\ud83c\uddf0\ud83c\uddf7', 'Egypt': '\ud83c\uddea\ud83c\uddec',
  'Italy': '\ud83c\uddee\ud83c\uddf9', 'Spain': '\ud83c\uddea\ud83c\uddf8', 'South Africa': '\ud83c\udfff\ud83c\udde6',
  'Argentina': '\ud83c\udde6\ud83c\uddf7', 'Turkey': '\ud83c\uddf9\ud83c\uddf7', 'Vietnam': '\ud83c\uddfb\ud83c\uddf3',
  'China': '\ud83c\udde8\ud83c\uddf3', 'Indonesia': '\ud83c\uddee\ud83c\udde9',
};

function getFlag(country) {
  return COUNTRY_FLAGS[country] || '\ud83c\udf0d';
}

/**
 * Create a YouTube Short from source video
 */
async function createShort(videoPath, options) {
  const type = options.type || 'clip';
  const outputPath = options.outputPath || videoPath.replace(/\.\w+$/, '_shorts.mp4');
  const tmpDir = path.dirname(outputPath);
  
  const query = options.query || ''; // Original search query like "Nigeria viral meme"
  const country = options.countryText || 'Global';
  const flag = getFlag(country);
  
  // Extract content type from query
  const contentType = query.includes('meme') ? 'Meme' : 
                      query.includes('streamer') ? 'Streamer' :
                      query.includes('explain') ? 'Explainer' : 'Clip';
  
  const startTime = options.startTime || 5;
  const duration = Math.min(options.duration || 25, 60);
  const voiceoverPath = options.voiceoverPath || null;
  
  const baseName = `short_${Date.now()}`;
  logger.info(`Creating Short: "${contentType} from ${country}" (${duration}s)`);
  
  try {
    // Step 1: Extract a frame for the intro background
    const frameFile = path.join(tmpDir, `${baseName}_frame.jpg`);
    execSync(`ffmpeg -y -ss ${startTime} -i "${videoPath}" -vframes 1 -q:v 2 "${frameFile}" 2>/dev/null`, { timeout: 10000 });

    // Step 2: Create intro video (3s, blurred background + flag + text)
    const introFile = path.join(tmpDir, `${baseName}_intro.mp4`);
    
    // Font setup
    const fontBold = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf';
    
    const introFilter = `
      [0:v]scale=${SHORTS_W}:${SHORTS_H}:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H},boxblur=30:8[b];
      [b]drawtext=text='${flag}':
        fontfile=${fontBold}:
        fontsize=120:fontcolor=white:
        x=(w-text_w)/2:y=h*0.3:
        shadowx=3:shadowy=3:shadowcolor=black@0.6,
      drawtext=text='${contentType} from ${country}':
        fontfile=${fontBold}:
        fontsize=52:fontcolor=white:
        x=(w-text_w)/2:y=h*0.52:
        shadowx=2:shadowy=2:shadowcolor=black@0.5[out]
    `.replace(/\s+/g, ' ').trim();
    
    execSync(`ffmpeg -y -loop 1 -i "${frameFile}" -t 3 -vf "${introFilter}" -c:v libx264 -preset ultrafast -crf 23 -pix_fmt yuv420p "${introFile}" 2>/dev/null`, { timeout: 30000 });

    // Step 3: Extract and fit the clip segment to 9:16 (NO captions per user request)
    const clipFile = path.join(tmpDir, `${baseName}_clip.mp4`);
    
    if (voiceoverPath && fs.existsSync(voiceoverPath)) {
      // EXPLAINER: voiceover with ducked audio
      const audioMix = `[1:a]volume=enable='between(t,1,4)':volume=0.2[a_d];[2:a][a_d]amix=inputs=2:duration=first[outa]`;
      execSync(
        `ffmpeg -y -ss ${startTime} -i "${videoPath}" -i "${videoPath}" -i "${voiceoverPath}" -t ${duration} ` +
        `-filter_complex "[0:v]scale=${SHORTS_W}:${SHORTS_H}:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H}[outv]; ${audioMix}" ` +
        `-map "[outv]" -map "[outa]" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${clipFile}" 2>/dev/null`,
        { timeout: 120000 }
      );
    } else {
      // CLIP/STREAMER: just fit to 9:16, keep original audio
      execSync(
        `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} ` +
        `-vf "scale=${SHORTS_W}:${SHORTS_H}:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H}" ` +
        `-c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${clipFile}" 2>/dev/null`,
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
        logger.success(`Short: ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB`);
        return outputPath;
      }
    }

    // Fallback: simple crop
    execSync(`ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} -vf "scale=${SHORTS_W}:${SHORTS_H}:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H}" -c:v libx264 -preset ultrafast -crf 28 -c:a aac -shortest "${outputPath}" 2>/dev/null`, { timeout: 60000 });
    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) return outputPath;
  } catch (error) {
    logger.warn(`Short creation: ${error.message.substring(0, 100)}`);
  }
  return null;
}

async function generateVoiceover(text, outputPath) {
  try {
    const safeText = text.replace(/"/g, '\\"');
    execSync(`edge-tts --voice "en-US-JennyNeural" --text "${safeText}" --write-media "${outputPath}" 2>/dev/null`, { timeout: 30000 });
    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 1000) return outputPath;
  } catch (error) {
    logger.warn(`Voiceover: ${error.message.substring(0, 80)}`);
  }
  return null;
}

module.exports = { createShort, generateVoiceover };
