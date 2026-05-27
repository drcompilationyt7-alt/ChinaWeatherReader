/**
 * Real-ESRGAN Upscaler
 * Only upscales if input video height < 1080p.
 * Pipeline: extract frames → Real-ESRGAN → rebuild 1080p video
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('Upscaler');

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
  // Step 1: Check if already >= 1080p
  const dims = probeVideoDimensions(inputPath);
  if (!dims) {
    logger.warn('Could not probe input dimensions, skipping upscale');
    return null;
  }

  logger.info(`Input: ${dims.width}x${dims.height}`);

  if (dims.height >= 1080 && dims.width >= 1080) {
    logger.success('Already >= 1080p — no upscale needed');
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
    // Step 3: Extract frames at original framerate
    logger.info('Extracting frames...');
    const probeFps = execSync(
      `ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0 "${inputPath}" 2>/dev/null`,
      { timeout: 10000, encoding: 'utf8' }
    ).trim();
    
    // Parse frame rate (may be "30000/1001" style fraction)
    let fps = 30;
    if (probeFps) {
      const parts = probeFps.split('/');
      if (parts.length === 2) {
        const num = parseFloat(parts[0]);
        const den = parseFloat(parts[1]);
        if (num && den) fps = num / den;
      } else {
        fps = parseFloat(probeFps) || 30;
      }
    }
    logger.info(`Detected framerate: ${fps.toFixed(2)} fps`);

    execSync(
      `ffmpeg -y -i "${inputPath}" -qscale:v 1 -qmin 1 -qmax 1 -pix_fmt rgb24 "${path.join(framesDir, 'frame_%06d.png')}" 2>/dev/null`,
      { timeout: 300000 }
    );
    const frameFiles = fs.readdirSync(framesDir).filter(f => f.startsWith('frame_') && f.endsWith('.png'));
    logger.success(`Extracted ${frameFiles.length} frames`);

    if (frameFiles.length === 0) {
      throw new Error('No frames extracted');
    }

    // Step 4: Upscale frames with Real-ESRGAN
    logger.info('Upscaling frames with Real-ESRGAN (x4)...');
    const modelName = dims.height * 4 >= 1080 ? 'RealESRGAN_x4plus' : 'RealESRGAN_x4plus';
    
    execSync(
      `cd "${realesrganPath}" && python3 inference_realesrgan.py -n ${modelName} -i "${framesDir}" -o "${upscaledDir}" --outscale 4 --fp32 2>&1`,
      { timeout: 600000 }
    );

    const upscaledFiles = fs.readdirSync(upscaledDir).filter(f => f.startsWith('frame_') && (f.endsWith('.png') || f.endsWith('.jpg')));
    logger.success(`Upscaled ${upscaledFiles.length} frames`);

    if (upscaledFiles.length === 0) {
      throw new Error('No frames upscaled');
    }

    // Step 5: Rebuild video at 1080p
    logger.info('Rebuilding video at 1080p...');
    execSync(
      `ffmpeg -y -framerate ${fps.toFixed(4)} -i "${path.join(upscaledDir, 'frame_%06d_out.png')}" -i "${inputPath}" -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -c:a aac -b:a 192k -shortest -vf "scale=1080:1920:flags=lanczos:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black" "${outputPath}" 2>/dev/null`,
      { timeout: 300000 }
    );

    if (!fs.existsSync(outputPath) || fs.statSync(outputPath).size < 100000) {
      // Try alternate output naming (some Real-ESRGAN versions use different output names)
      execSync(
        `ffmpeg -y -framerate ${fps.toFixed(4)} -pattern_type glob -i "${upscaledDir}/*.png" -i "${inputPath}" -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -c:a aac -b:a 192k -shortest -vf "scale=1080:1920:flags=lanczos:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black" "${outputPath}" 2>/dev/null`,
        { timeout: 300000 }
      );
    }

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      const sizeMB = (fs.statSync(outputPath).size / 1024 / 1024).toFixed(1);
      logger.success(`Upscaled to 1080p: ${sizeMB}MB`);
      return outputPath;
    }

    // Step 6: Fallback — copy input if upscale failed
    logger.warn('Upscale rebuild failed, copying input as-is');
    fs.copyFileSync(inputPath, outputPath);
    return outputPath;

  } catch (e) {
    logger.warn(`Upscale error: ${e.message.substring(0, 100)}`);
    // Fallback: copy input
    try {
      fs.copyFileSync(inputPath, outputPath);
      return outputPath;
    } catch {}
    return null;
  } finally {
    // Cleanup temp files
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}
  }
}

module.exports = { upscaleTo1080p, probeVideoDimensions };