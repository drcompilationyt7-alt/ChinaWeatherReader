/**
 * Clip Editor - YouTube Shorts Creator
 * 
 * FIXES:
 * - Smart padding (adds black bars) instead of cropping - preserves full content
 * - Original audio preserved completely in clips
 * - Explainer: voiceover layered on top, original audio reduced during vo
 * - Concatenation preserves audio properly
 * - FIXED: Intro now has silent audio track so concat with clip audio works properly
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('ClipEditor');
const SHORTS_W = 1080;
const SHORTS_H = 1920;

const FLAGS = {
  'Nigeria':'\ud83c\uddf3\ud83c\uddec','Japan':'\ud83c\uddef\ud83c\uddf5','Germany':'\ud83c\udde9\ud83c\uddea',
  'Australia':'\ud83c\udde6\ud83c\uddfa','France':'\ud83c\uddeb\ud83c\uddf7','Brazil':'\ud83c\udde7\ud83c\uddf7',
  'Thailand':'\ud83c\uddf9\ud83c\udded','India':'\ud83c\uddee\ud83c\uddf3','Mexico':'\ud83c\uddf2\ud83c\uddfd',
  'UK':'\ud83c\uddec\ud83c\udde7','South Korea':'\ud83c\uddf0\ud83c\uddf7','Egypt':'\ud83c\uddea\ud83c\uddec',
  'Italy':'\ud83c\uddee\ud83c\uddf9','Spain':'\ud83c\uddea\ud83c\uddf8','China':'\ud83c\udde8\ud83c\uddf3',
  'Global':'\ud83c\udf0d'
};

async function createShort(videoPath, options) {
  const outputPath = options.outputPath || videoPath.replace(/\.\w+$/, '_shorts.mp4');
  const tmpDir = path.dirname(outputPath);
  const query = options.query || '';
  const country = options.countryText || 'Global';
  const flag = FLAGS[country] || '\ud83c\udf0d';
  const voiceoverPath = options.voiceoverPath || null;
  const startTime = Math.min(options.startTime || 5, 30);
  const duration = Math.min(options.duration || 28, 60);

  // Determine intro text
  let introLine = `Clip from ${country}`;
  if (query.toLowerCase().includes('meme')) introLine = `Meme from ${country}`;
  else if (query.toLowerCase().includes('streamer')) introLine = `Streamer from ${country}`;
  else if (options.explainerText || query.toLowerCase().includes('explain')) introLine = `What is this?`;
  // Check for douyin/shorts
  if (query.toLowerCase().includes('douyin')) introLine = `Douyin from ${country}`;
  else if (query.toLowerCase().includes('short')) introLine = `Short from ${country}`;

  const baseName = `short_${Date.now()}`;
  logger.info(`Creating: "${introLine}" (${duration}s)`);

  try {
    // Get video dimensions first
    let videoW = 0, videoH = 0;
    try {
      const probe = execSync(`ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "${videoPath}" 2>/dev/null`, { timeout: 5000, encoding: 'utf8' }).trim();
      const parts = probe.split(',');
      videoW = parseInt(parts[0]);
      videoH = parseInt(parts[1]);
    } catch {}

    // Smart pad to 9:16 (maintains original frame completely, adds black bars if needed)
    const padFilter = `scale='min(${SHORTS_W},iw)':'min(${SHORTS_H},ih)':force_original_aspect_ratio=decrease,pad=${SHORTS_W}:${SHORTS_H}:(ow-iw)/2:(oh-ih)/2:color=black`;

    // Extract a frame for the intro background
    const frameFile = path.join(tmpDir, `${baseName}_frame.jpg`);
    execSync(`ffmpeg -y -ss ${startTime} -i "${videoPath}" -vframes 1 -q:v 2 "${frameFile}" 2>/dev/null`, { timeout: 10000 });

    // Create intro (3s): blurred background image + flag + text
    // FIXED: Added anullsrc to generate silent audio so concat works properly with audio streams
    const introFile = path.join(tmpDir, `${baseName}_intro.mp4`);
    const font = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf';
    const safeText = introLine.replace(/'/g, "'\\''");

    // Blurred + scaled to full 9:16 + text overlay + silent audio track
    execSync(
      `ffmpeg -y -loop 1 -i "${frameFile}" -t 3 -f lavfi -i anullsrc=r=44100:cl=mono ` +
      `-filter_complex ` +
      `"[0:v]scale=${SHORTS_W}:${SHORTS_H}:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H},boxblur=25:5[b];` +
      `[b]drawtext=text='${flag}':fontfile=${font}:fontsize=110:fontcolor=white:x=(w-text_w)/2:y=h*0.28:shadowx=3:shadowy=3:shadowcolor=black@0.6,` +
      `drawtext=text='${safeText}':fontfile=${font}:fontsize=52:fontcolor=white:x=(w-text_w)/2:y=h*0.45:shadowx=2:shadowy=2:shadowcolor=black@0.5[outv]" ` +
      `-map "[outv]" -map "1:a" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -pix_fmt yuv420p -shortest "${introFile}" 2>/dev/null`,
      { timeout: 30000 }
    );

    // Create clip segment (properly padded to 9:16, NO cropping, audio preserved)
    const clipFile = path.join(tmpDir, `${baseName}_clip.mp4`);

    if (voiceoverPath && fs.existsSync(voiceoverPath)) {
      // EXPLAINER: clip with voiceover + lowered original audio
      const voDur = 4; // voiceover duration in seconds
      execSync(
        `ffmpeg -y -ss ${startTime} -i "${videoPath}" -i "${voiceoverPath}" -t ${duration} ` +
        `-filter_complex ` +
        `"[0:v]${padFilter}[outv];` +
        `[0:a]asplit=2[a_orig][a_bg];` +
        `[a_bg]volume=enable='between(t,2,${2+voDur})':volume=0.15[a_duck];` +
        `[1:a]adelay=2000|2000[a_vo];` +
        `[a_duck][a_vo]amix=inputs=2:duration=first:dropout_transition=2[outa]" ` +
        `-map "[outv]" -map "[outa]" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${clipFile}" 2>/dev/null`,
        { timeout: 120000 }
      );
    } else {
      // CLIP/STREAMER: just pad to 9:16, keep original audio (FIXED: added -c:a aac copy to ensure audio preserved)
      execSync(
        `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} ` +
        `-vf "${padFilter}" ` +
        `-c:v libx264 -preset ultrafast -crf 23 -c:a aac -b:a 128k -shortest "${clipFile}" 2>/dev/null`,
        { timeout: 120000 }
      );
    }

    // Concatenate intro + clip with proper audio handling
    if (fs.existsSync(introFile) && fs.existsSync(clipFile)) {
      const listFile = path.join(tmpDir, `${baseName}_list.txt`);
      fs.writeFileSync(listFile, `file '${introFile.replace(/'/g, "'\\''")}'\nfile '${clipFile.replace(/'/g, "'\\''")}'`);

      // Both files now have audio tracks, concat works properly
      execSync(
        `ffmpeg -y -f concat -safe 0 -i "${listFile}" ` +
        `-c:v libx264 -preset ultrafast -crf 23 -c:a aac -b:a 128k -movflags +faststart "${outputPath}" 2>/dev/null`,
        { timeout: 120000 }
      );

      // Cleanup
      [frameFile, introFile, clipFile, listFile].forEach(f => { try { fs.unlinkSync(f); } catch {} });

      if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
        logger.success(`Short: ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB`);
        return outputPath;
      }
    }

    // Fallback: just pad the clip
    logger.warn('Intro failed, using padded clip only');
    execSync(
      `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} -vf "${padFilter}" -c:v libx264 -preset ultrafast -crf 28 -c:a aac -shortest "${outputPath}" 2>/dev/null`,
      { timeout: 60000 }
    );
    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) return outputPath;

  } catch (error) {
    logger.warn(`Short failed: ${error.message.substring(0, 100)}`);
  }
  return null;
}

module.exports = { createShort };
