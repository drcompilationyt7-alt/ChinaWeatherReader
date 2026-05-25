/**
 * Clip Editor - YouTube Shorts Creator
 * 
 * FIXES:
 * - Smart padding (adds black bars) instead of cropping - preserves full content
 * - Original audio preserved completely in clips
 * - Voiceover: layered on top, original audio reduced during vo
 * - Concatenation preserves audio properly
 * - FIXED: Intro now has silent audio track so concat with clip audio works properly
 * - FIXED: Uses text flag codes instead of emoji (ffmpeg drawtext can't render emoji flags)
 * - FIXED: Intro fallback works even if drawtext fails
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('ClipEditor');
const SHORTS_W = 1080;
const SHORTS_H = 1920;

// Text-based flags (ffmpeg drawtext can't render emoji flags)
// Using bold text with country code
const FLAGS_TEXT = {
  'Nigeria':'NGA','Japan':'JPN','Germany':'DEU',
  'Australia':'AUS','France':'FRA','Brazil':'BRA',
  'Thailand':'THA','India':'IND','Mexico':'MEX',
  'UK':'GBR','South Korea':'KOR','Egypt':'EGY',
  'Italy':'ITA','Spain':'ESP','China':'CHN',
  'Global':'GLB', 'Indonesia':'IDN', 'Vietnam':'VNM'
};

async function createShort(videoPath, options) {
  const outputPath = options.outputPath || videoPath.replace(/\.\w+$/, '_shorts.mp4');
  const tmpDir = path.dirname(outputPath);
  const query = options.query || '';
  const country = options.countryText || 'Global';
  const countryText = FLAGS_TEXT[country] || country.substring(0, 3).toUpperCase();
  const voiceoverPath = options.voiceoverPath || null;
  const startTime = Math.min(options.startTime || 5, 30);
  const duration = Math.min(options.duration || 28, 60);

  const baseName = `short_${Date.now()}`;
  logger.info(`Creating short for ${country} [${countryText}] (${duration}s)`);

  try {
    // Smart pad to 9:16 (maintains original frame completely, adds black bars if needed)
    const padFilter = `scale='min(${SHORTS_W},iw)':'min(${SHORTS_H},ih)':force_original_aspect_ratio=decrease,pad=${SHORTS_W}:${SHORTS_H}:(ow-iw)/2:(oh-ih)/2:color=black`;

    // Extract a frame for the intro background
    const frameFile = path.join(tmpDir, `${baseName}_frame.jpg`);
    execSync(`ffmpeg -y -ss ${startTime} -i "${videoPath}" -vframes 1 -q:v 2 "${frameFile}" 2>/dev/null`, { timeout: 10000 });

    // Create intro (3s): blurred background + country code text + silent audio
    const introFile = path.join(tmpDir, `${baseName}_intro.mp4`);
    const font = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf';

    try {
      execSync(
        `ffmpeg -y -loop 1 -i "${frameFile}" -t 3 -f lavfi -i anullsrc=r=44100:cl=mono ` +
        `-filter_complex ` +
        `"[0:v]scale=${SHORTS_W}:${SHORTS_H}:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H},boxblur=25:5[b];` +
        `[b]drawtext=text='${countryText}':fontfile=${font}:fontsize=120:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:shadowx=4:shadowy=4:shadowcolor=black@0.7[outv]" ` +
        `-map "[outv]" -map "1:a" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -pix_fmt yuv420p -shortest "${introFile}" 2>/dev/null`,
        { timeout: 30000 }
      );
      
      if (!fs.existsSync(introFile) || fs.statSync(introFile).size < 1000) {
        logger.warn('Intro file too small, creating simple intro...');
        throw new Error('Intro file invalid');
      }
    } catch (introError) {
      // Fallback: simple black background with text
      logger.warn(`Drawtext failed, trying simple intro: ${introError.message.substring(0, 60)}`);
      execSync(
        `ffmpeg -y -f lavfi -i color=c=black:s=${SHORTS_W}x${SHORTS_H}:d=3 -f lavfi -i anullsrc=r=44100:cl=mono ` +
        `-vf "drawtext=text='${countryText}':fontfile=${font}:fontsize=120:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:shadowx=4:shadowy=4:shadowcolor=black@0.7" ` +
        `-c:v libx264 -preset ultrafast -crf 23 -c:a aac -pix_fmt yuv420p -shortest "${introFile}" 2>/dev/null`,
        { timeout: 30000 }
      );
      if (!fs.existsSync(introFile) || fs.statSync(introFile).size < 1000) {
        // Last resort: copy a piece of the video with audio
        logger.warn('Simple intro also failed, using video segment as intro...');
        execSync(
          `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t 3 -c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${introFile}" 2>/dev/null`,
          { timeout: 30000 }
        );
      }
    }

    // Create clip segment (padded to 9:16, with voiceover or plain)
    const clipFile = path.join(tmpDir, `${baseName}_clip.mp4`);

    if (voiceoverPath && fs.existsSync(voiceoverPath)) {
      // CLIP WITH VOICEOVER: clip + voiceover ducking original audio
      let voDur = 4;
      try {
        const probeOut = execSync(
          `ffprobe -i "${voiceoverPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`,
          { timeout: 5000, encoding: 'utf8' }
        ).trim();
        if (probeOut) {
          voDur = Math.ceil(parseFloat(probeOut));
        }
      } catch {}
      voDur = Math.min(voDur, duration - 2);

      execSync(
        `ffmpeg -y -ss ${startTime} -i "${videoPath}" -i "${voiceoverPath}" -t ${duration} ` +
        `-filter_complex ` +
        `"[0:v]${padFilter}[outv];` +
        `[0:a]asplit=2[a_orig][a_bg];` +
        `[a_bg]volume=enable='between(t,1,${1+voDur})':volume=0.1[a_duck];` +
        `[1:a]adelay=1000|1000[a_vo];` +
        `[a_duck][a_vo]amix=inputs=2:duration=first:dropout_transition=2[outa]" ` +
        `-map "[outv]" -map "[outa]" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${clipFile}" 2>/dev/null`,
        { timeout: 120000 }
      );
    } else {
      // PLAIN CLIP: pad to 9:16, keep original audio
      execSync(
        `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} ` +
        `-vf "${padFilter}" ` +
        `-c:v libx264 -preset ultrafast -crf 23 -c:a aac -b:a 128k -shortest "${clipFile}" 2>/dev/null`,
        { timeout: 120000 }
      );
    }

    // Concatenate intro + clip
    if (fs.existsSync(introFile) && fs.existsSync(clipFile)) {
      const listFile = path.join(tmpDir, `${baseName}_list.txt`);
      fs.writeFileSync(listFile, `file '${introFile.replace(/'/g, "'\\''")}'\nfile '${clipFile.replace(/'/g, "'\\''")}'`);

      execSync(
        `ffmpeg -y -f concat -safe 0 -i "${listFile}" ` +
        `-c:v libx264 -preset ultrafast -crf 23 -c:a aac -b:a 128k -movflags +faststart "${outputPath}" 2>/dev/null`,
        { timeout: 120000 }
      );

      // Cleanup
      [frameFile, introFile, clipFile, listFile].forEach(f => { try { fs.unlinkSync(f); } catch {} });

      if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
        logger.success(`Short created: ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB`);
        return outputPath;
      }
    }

    // Fallback: just padded clip
    logger.warn('Intro+concat failed, using padded clip only');
    execSync(
      `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} -vf "${padFilter}" -c:v libx264 -preset ultrafast -crf 28 -c:a aac -shortest "${outputPath}" 2>/dev/null`,
      { timeout: 60000 }
    );
    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Short (no intro): ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB`);
      return outputPath;
    }

  } catch (error) {
    logger.warn(`Short failed: ${error.message.substring(0, 150)}`);
  }
  return null;
}

module.exports = { createShort };
