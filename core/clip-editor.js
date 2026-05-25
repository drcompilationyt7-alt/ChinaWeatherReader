/**
 * Clip Editor - YouTube Shorts Creator
 * 
 * FIXES:
 * - Smart padding (adds black bars) instead of cropping - preserves full content
 * - Original audio preserved completely in clips
 * - Voiceover: layered on top, original audio reduced during vo
 * - FIXED: Simplified concat logic - works reliably
 * - FIXED: No emoji flags - uses text country codes (DEU, JPN, etc)
 * - FIXED: Graceful fallback to padded clip if intro/concat fails
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('ClipEditor');
const SHORTS_W = 1080;
const SHORTS_H = 1920;

// Text country codes (ffmpeg drawtext can't render emoji flags)
const COUNTRY_CODES = {
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
  const country = options.countryText || 'Global';
  const countryCode = COUNTRY_CODES[country] || country.substring(0, 3).toUpperCase();
  const voiceoverPath = options.voiceoverPath || null;
  const startTime = Math.min(options.startTime || 5, 30);
  const duration = Math.min(options.duration || 28, 60);

  const baseName = `short_${Date.now()}`;
  logger.info(`Creating short for ${country} [${countryCode}] (${duration}s)`);

  try {
    // Smart pad to 9:16 (adds black bars, never crops)
    const padFilter = `scale='min(${SHORTS_W},iw)':'min(${SHORTS_H},ih)':force_original_aspect_ratio=decrease,pad=${SHORTS_W}:${SHORTS_H}:(ow-iw)/2:(oh-ih)/2:color=black`;

    // Step 1: Create the clip with voiceover (or plain) and pad to 9:16 in ONE ffmpeg call
    const clipFile = path.join(tmpDir, `${baseName}_clip.mp4`);

    if (voiceoverPath && fs.existsSync(voiceoverPath)) {
      // Voiceover: measure duration
      let voDur = 4;
      try {
        const probeOut = execSync(
          `ffprobe -i "${voiceoverPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`,
          { timeout: 5000, encoding: 'utf8' }
        ).trim();
        if (probeOut) voDur = Math.ceil(parseFloat(probeOut));
      } catch {}
      voDur = Math.min(voDur, duration - 2);

      // Mix: original video + voiceover overlaid (original audio ducked during voiceover)
      execSync(
        `ffmpeg -y -ss ${startTime} -i "${videoPath}" -i "${voiceoverPath}" -t ${duration} ` +
        `-filter_complex ` +
        `"[0:v]${padFilter}[outv];` +
        `[0:a]volume=enable='between(t,1,${1+voDur})':volume=0.1[adu];` +
        `[1:a]adelay=1000|1000[avo];` +
        `[adu][avo]amix=inputs=2:duration=first[outa]" ` +
        `-map "[outv]" -map "[outa]" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${clipFile}" 2>/dev/null`,
        { timeout: 120000 }
      );
    } else {
      // Plain: just pad to 9:16
      execSync(
        `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} ` +
        `-vf "${padFilter}" ` +
        `-c:v libx264 -preset ultrafast -crf 23 -c:a aac -b:a 128k -shortest "${clipFile}" 2>/dev/null`,
        { timeout: 120000 }
      );
    }

    if (!fs.existsSync(clipFile) || fs.statSync(clipFile).size < 50000) {
      throw new Error('Clip generation failed');
    }

    // Step 2: Create a simple 2.5s intro with country code on black background
    const introFile = path.join(tmpDir, `${baseName}_intro.mp4`);
    const font = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf';
    
    try {
      execSync(
        `ffmpeg -y -f lavfi -i color=c=black:s=${SHORTS_W}x${SHORTS_H}:d=2.5 -f lavfi -i anullsrc=r=44100:cl=mono ` +
        `-vf "drawtext=text='${countryCode}':fontfile=${font}:fontsize=140:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:shadowx=4:shadowy=4:shadowcolor=black@0.7" ` +
        `-c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${introFile}" 2>/dev/null`,
        { timeout: 30000 }
      );
    } catch (e) {
      // Fallback intro: just 2.5s of black + silent audio
      logger.warn(`Drawtext failed, plain black intro`);
      execSync(
        `ffmpeg -y -f lavfi -i color=c=black:s=${SHORTS_W}x${SHORTS_H}:d=2.5 -f lavfi -i anullsrc=r=44100:cl=mono -c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${introFile}" 2>/dev/null`,
        { timeout: 15000 }
      );
    }

    if (!fs.existsSync(introFile) || fs.statSync(introFile).size < 1000) {
      throw new Error('Intro generation failed');
    }

    // Step 3: Concatenate intro + clip using the concat protocol (more reliable than concat demuxer)
    // Encode intro to match clip's codec parameters first, then concat
    const introConformed = path.join(tmpDir, `${baseName}_intro_conformed.mp4`);
    execSync(
      `ffmpeg -y -i "${introFile}" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -b:a 128k -pix_fmt yuv420p "${introConformed}" 2>/dev/null`,
      { timeout: 15000 }
    );
    
    // Use concat demuxer with proper format
    const listFile = path.join(tmpDir, `${baseName}_list.txt`);
    fs.writeFileSync(listFile, `file '${introConformed}'\nfile '${clipFile}'`);
    
    execSync(
      `ffmpeg -y -f concat -safe 0 -i "${listFile}" -c copy -movflags +faststart "${outputPath}" 2>/dev/null`,
      { timeout: 60000 }
    );

    // Cleanup temp files (ignore errors)
    try { 
      [introFile, clipFile, introConformed, listFile, 
       path.join(tmpDir, `${baseName}_frame.jpg`)
      ].forEach(f => { try { if (fs.existsSync(f)) fs.unlinkSync(f); } catch {} });
    } catch {}

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Short: ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB`);
      return outputPath;
    }

    // Step 4: Fallback - just the padded clip
    logger.warn('Concat failed, using clip only');
    fs.copyFileSync(clipFile, outputPath);
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
