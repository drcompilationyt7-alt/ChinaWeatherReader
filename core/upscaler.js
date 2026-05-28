/**
 * Real-ESRGAN Upscaler
 * Only upscales if input video height < 480p (truly bad quality).
 * Limited to 30 frames max, with timeout.
 * If upscale fails, returns null — pipeline continues with original video.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('Upscaler');
const MAX_FRAMES = 30;
const MAX_DURATION = 180; // only upscale first 3 minutes max
const UPSCALE_TIMEOUT = 180000; // 3 min timeout for Real-ESRGAN

function probeVideoDimensions(videoPath) {
  try {
    const out = execSync(
      `ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "${videoPath}" 2>/dev/null`,
      { timeout: 10000, encoding: 'utf8' }
    ).trim();
    const parts = out.split(',').map(s => parseInt(s.trim()));
    if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
      return { width: parts[0], height: parts[1] };
    }
  } catch {}
  return null;
}

async function upscaleTo1080p(inputPath, outputPath) {
  // Step 1: Check if already >= 480p — only upscale truly bad quality
  const dims = probeVideoDimensions(inputPath);
  if (!dims) {
    logger.warn('Could not probe input dimensions, skipping upscale');
    return null;
  }

  logger.info(`Input: ${dims.width}x${dims.height}`);

  if (dims.height >= 480 || dims.width >= 480) {
    logger.success('Already >= 480p — no upscale needed');
    return null;
  }

  // Step 2: Locate Real-ESRGAN
  const realesrganPath = process.env.REALESRGAN_PATH || '/opt/Real-ESRGAN';
  const inferenceScript = path.join(realesrganPath, 'inference_realesrgan.py');
  
  if (!fs.existsSync(inferenceScript)) {
    logger.warn(`Real-ESRGAN not found at ${inferenceScript}, skipping upscale`);
    return null;
  }

  const tmpDir = path.join(path.dirname(outputPath), `upscale_tmp_${Date.now()}`);
  const framesDir = path.join(tmpDir, 'frames');
  const upscaledDir = path.join(tmpDir, 'upscaled');
  fs.mkdirSync(framesDir, { recursive: true });
  fs.mkdirSync(upscaledDir, { recursive: true });

  try {
    // Step 3: Get video duration and extract frames at 1 fps (max 30 frames)
    let duration = 30;
    try {
      const durOut = execSync(
        `ffprobe -v error -show_entries format=duration -of csv=p=0 "${inputPath}" 2>/dev/null`,
        { timeout: 10000, encoding: 'utf8' }
      ).trim();
      if (durOut) duration = Math.min(parseFloat(durOut), MAX_DURATION);
    } catch {}

    const fps = Math.max(1, duration / MAX_FRAMES);
    logger.info(`Extracting ~${Math.min(Math.floor(duration/fps), MAX_FRAMES)} frames (1 per ${fps.toFixed(1)}s)`);

    execSync(
      `ffmpeg -y -i "${inputPath}" -vf "fps=${fps}" -qscale:v 2 -pix_fmt rgb24 "${path.join(framesDir, 'frame_%06d.png')}" 2>/dev/null`,
      { timeout: 120000 }
    );
    
    const frameFiles = fs.readdirSync(framesDir).filter(f => f.startsWith('frame_') && f.endsWith('.png'));
    logger.success(`Extracted ${frameFiles.length} frames (max ${MAX_FRAMES})`);

    if (frameFiles.length === 0) {
      throw new Error('No frames extracted');
    }

    // Step 4: Upscale frames with Real-ESRGAN (with timeout)
    logger.info('Upscaling frames with Real-ESRGAN (x4)...');
    execSync(
      `cd "${realesrganPath}" && python3 inference_realesrgan.py -n RealESRGAN_x4plus -i "${framesDir}" -o "${upscaledDir}" --outscale 4 --fp32 2>&1`,
      { timeout: UPSCALE_TIMEOUT }
    );

    const upscaledFiles = fs.readdirSync(upscaledDir).filter(f => f.startsWith('frame_') && (f.endsWith('.png') || f.endsWith('.jpg')));
    logger.success(`Upscaled ${upscaledFiles.length} frames`);

    if (upscaledFiles.length === 0) {
      throw new Error('No frames upscaled');
    }

    // Step 5: Rebuild video at 720p (480p upscaled x4 = 1920p, but we just need decent)
    logger.info('Rebuilding video...');
    const fpsStr = fps.toFixed(4);
    
    // Try common output naming patterns
    const outPattern = path.join(upscaledDir, 'frame_%06d_out.png');
    const globPattern = path.join(upscaledDir, '*.png');
    
    execSync(
      `ffmpeg -y -framerate ${fpsStr} -i "${outPattern}" -i "${inputPath}" -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p -c:a aac -b:a 128k -shortest "${outputPath}" 2>/dev/null`,
      { timeout: 300000 }
    );

    if (!fs.existsSync(outputPath) || fs.statSync(outputPath).size < 100000) {
      // Try glob pattern
      execSync(
        `ffmpeg -y -framerate ${fpsStr} -pattern_type glob -i "${globPattern}" -i "${inputPath}" -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p -c:a aac -b:a 128k -shortest "${outputPath}" 2>/dev/null`,
        { timeout: 300000 }
      );
    }

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      const sizeMB = (fs.statSync(outputPath).size / 1024 / 1024).toFixed(1);
      logger.success(`Upscaled video: ${sizeMB}MB`);
      return outputPath;
    }

    // Fallback: return null — pipeline will use original
    logger.warn('Upscale rebuild produced no output — skipping');
    return null;

  } catch (e) {
    logger.warn(`Upscale error: ${e.message.substring(0, 100)} — skipping, using original`);
    return null;
  } finally {
    // Cleanup temp files
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}
  }
}

module.exports = { upscaleTo1080p, probeVideoDimensions };