/**
 * Clip Editor - YouTube Shorts Creator
 * 
 * [0-3s] INTRO: Country flag + quick text like "Meme from Nigeria"
 * [3-30s] FULL CLIP: Original video, original audio
 * EXPLAINER: Voiceover with ducked audio
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
  'Italy':'\ud83c\uddee\ud83c\uddf9','Spain':'\ud83c\uddea\ud83c\uddf8','South Africa':'\ud83c\udfff\ud83c\udde6',
  'Argentina':'\ud83c\udde6\ud83c\uddf7','Turkey':'\ud83c\uddf9\ud83c\uddf7','Vietnam':'\ud83c\uddfb\ud83c\uddf3',
  'China':'\ud83c\udde8\ud83c\uddf3','Indonesia':'\ud83c\uddee\ud83c\udde9','Global':'\ud83c\udf0d'
};

async function createShort(videoPath, options) {
  const outputPath = options.outputPath || videoPath.replace(/\.\w+$/, '_shorts.mp4');
  const tmpDir = path.dirname(outputPath);
  const query = options.query || '';
  const country = options.countryText || 'Global';
  const flag = FLAGS[country] || '\ud83c\udf0d';
  const voiceoverPath = options.voiceoverPath || null;
  const explainerText = options.explainerText || '';
  
  // Determine intro text from query
  let introLine = `Clip from ${country}`;
  if (query.toLowerCase().includes('meme')) introLine = `Meme from ${country}`;
  else if (query.toLowerCase().includes('streamer')) introLine = `Streamer from ${country}`;
  else if (query.toLowerCase().includes('explain') || explainerText) introLine = explainerText ? `What is this?` : `Explainer from ${country}`;
  
  const startTime = options.startTime || 5;
  const duration = Math.min(options.duration || 25, 60);
  
  const baseName = `short_${Date.now()}`;
  logger.info(`Creating: "${introLine}" (${duration}s)`);
  
  try {
    // Extract frame
    const frameFile = path.join(tmpDir, `${baseName}_frame.jpg`);
    execSync(`ffmpeg -y -ss ${startTime} -i "${videoPath}" -vframes 1 -q:v 2 "${frameFile}" 2>/dev/null`, { timeout: 10000 });

    // Create intro (3s, blurred bg + flag + text)
    const introFile = path.join(tmpDir, `${baseName}_intro.mp4`);
    const font = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf';
    const safeIntro = introLine.replace(/'/g, "\\'");
    
    const filter = `[0:v]scale=${SHORTS_W}:${SHORTS_H}:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H},boxblur=30:8[b];` +
      `[b]drawtext=text='${flag}':fontfile=${font}:fontsize=120:fontcolor=white:x=(w-text_w)/2:y=h*0.3:shadowx=3:shadowy=3:shadowcolor=black@0.6,` +
      `drawtext=text='${safeIntro}':fontfile=${font}:fontsize=48:fontcolor=white:x=(w-text_w)/2:y=h*0.5:shadowx=2:shadowy=2:shadowcolor=black@0.5[out]`;
    
    execSync(`ffmpeg -y -loop 1 -i "${frameFile}" -t 3 -vf "${filter}" -c:v libx264 -preset ultrafast -crf 23 -pix_fmt yuv420p "${introFile}" 2>/dev/null`, { timeout: 30000 });

    // Create clip segment
    const clipFile = path.join(tmpDir, `${baseName}_clip.mp4`);
    
    if (voiceoverPath && fs.existsSync(voiceoverPath)) {
      // Explainer: voiceover + ducked original audio
      const mixFilter = `[1:a]volume=1[a0];[2:a]adelay=0|0[a1];[a0]volume=enable='between(t,1,${3+3})':volume=0.15[a_duck];[a_duck][a1]amix=inputs=2:duration=first[outa]`;
      execSync(`ffmpeg -y -ss ${startTime} -i "${videoPath}" -i "${videoPath}" -i "${voiceoverPath}" -t ${duration} ` +
        `-filter_complex "[0:v]scale=${SHORTS_W}:${SHORTS_H}:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H}[outv];${mixFilter}" ` +
        `-map "[outv]" -map "[outa]" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${clipFile}" 2>/dev/null`, { timeout: 120000 });
    } else {
      execSync(`ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} ` +
        `-vf "scale=${SHORTS_W}:${SHORTS_H}:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H}" ` +
        `-c:v libx264 -preset ultrafast -crf 23 -c:a aac -shortest "${clipFile}" 2>/dev/null`, { timeout: 120000 });
    }

    // Concatenate intro + clip
    if (fs.existsSync(introFile) && fs.existsSync(clipFile)) {
      const concatList = path.join(tmpDir, `${baseName}_list.txt`);
      const q = (s) => `'${s.replace(/'/g, "'\\''")}'`;
      fs.writeFileSync(concatList, `file ${q(introFile)}\nfile ${q(clipFile)}`);
      
      execSync(`ffmpeg -y -f concat -safe 0 -i "${concatList}" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -movflags +faststart "${outputPath}" 2>/dev/null`, { timeout: 120000 });
      
      [frameFile, introFile, clipFile, concatList].forEach(f => { try { fs.unlinkSync(f); } catch {} });
      
      if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
        logger.success(`Short: ${(fs.statSync(outputPath).size/1024/1024).toFixed(1)}MB`);
        return outputPath;
      }
    }

    // Fallback
    execSync(`ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} -vf "scale=${SHORTS_W}:${SHORTS_H}:force_original_aspect_ratio=increase,crop=${SHORTS_W}:${SHORTS_H}" -c:v libx264 -preset ultrafast -crf 28 -c:a aac -shortest "${outputPath}" 2>/dev/null`, { timeout: 60000 });
    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) return outputPath;
  } catch (error) {
    logger.warn(`Short: ${error.message.substring(0, 100)}`);
  }
  return null;
}

module.exports = { createShort };
