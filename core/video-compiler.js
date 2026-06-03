/**
 * Video Compiler — FFmpeg Render Engine
 * 
 * Takes an Editor Manifest (JSON timeline) and produces the final .mp4.
 * 
 * Uses FFmpeg as core renderer (fast, reliable).
 * MoviePy as orchestrator for complex timelines.
 * 
 * Maximum quality: CRF 15, slow preset, 1080x1920.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('VideoCompiler');

const OUTPUT_W = 1080;
const OUTPUT_H = 1920;
const CRF = 0; // Lossless (0-51, lower = better)
const PRESET = 'slow';

/**
 * Generate ASS subtitle content from the manifest caption entries
 */
function generateAssSubtitles(timeline) {
  let assContent = `[Script Info]
ScriptType: v4.00+
Collisions: Normal
PlayResX: ${OUTPUT_W}
PlayResY: ${OUTPUT_H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BorderStyle, Outline, Shadow, Alignment, MarginV, MarginL, MarginR
Style: Caption,Impact,40,&H00FFFFFF,&H00000000,1,3,0,2,120,30,30
Style: Highlight,Impact,44,&H0000FFFF,&H00000000,1,3,0,2,120,30,30

[Events]
Format: Layer, Start, End, Style, Text
`;

  for (const segment of timeline) {
    for (const caption of (segment.captions || [])) {
      const startStr = formatAssTime(parseFloat(caption.start.replace(':', '.')));
      const endStr = formatAssTime(parseFloat(caption.end.replace(':', '.')));
      const style = caption.highlight ? 'Highlight' : 'Caption';
      const text = caption.text.replace(/"/g, '\\"');

      if (text && text.length > 0) {
        assContent += `Dialogue: 0,${startStr},${endStr},${style},${text}\n`;
      }
    }
  }

  return assContent;
}

function formatAssTime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const cs = Math.floor((s - Math.floor(s)) * 100);
  return `${h}:${String(m).padStart(2, '0')}:${String(Math.floor(s)).padStart(2, '0')}.${String(cs).padStart(2, '0')}`;
}

/**
 * Build a concat file for FFmpeg concatenation
 */
function buildConcatFile(clips, dir) {
  const concatPath = path.join(dir, 'concat_list.txt');
  let content = '';

  for (const clip of clips) {
    if (clip.videoPath && fs.existsSync(clip.videoPath)) {
      const escapedPath = clip.videoPath.replace(/\\/g, '/').replace(/'/g, "'\\''");
      content += `file '${escapedPath}'\n`;
    }
  }

  fs.writeFileSync(concatPath, content, 'utf8');
  return concatPath;
}

/**
 * Render the final video from an editor manifest
 * 
 * @param {Object} manifest - Editor manifest with global_settings + timeline
 * @param {string} assetsDir - Directory containing sourced video clips
 * @param {string} ttsDir - Directory containing TTS audio files
 * @param {string} outputPath - Final output .mp4 path
 * @returns {string|null} - Path to final video
 */
async function render(manifest, assetsDir, ttsDir, outputPath) {
  const tmpDir = path.join(path.dirname(outputPath), `render_tmp_${Date.now()}`);
  if (!fs.existsSync(tmpDir)) fs.mkdirSync(tmpDir, { recursive: true });

  const timeline = manifest.timeline || [];
  const globalSettings = manifest.global_settings || {};
  const bgmVolume = globalSettings.bgm_volume || 0.15;

  logger.info(`Rendering ${timeline.length} timeline segments (CRF ${CRF}, ${PRESET})`);

  // ─── Step 1: Verify all assets exist ──────────────────────────────
  const verifiedSegments = [];

  for (const segment of timeline) {
    const clipId = segment.video?.source || 'unknown';
    const sourcePath = path.join(assetsDir, clipId);

    if (!fs.existsSync(sourcePath)) {
      logger.warn(`Missing video: ${clipId} — checking assets dir...`);
      // Search assetsDir for any file containing this clip ID
      const files = fs.readdirSync(assetsDir).filter(f => f.includes(clipId.replace('.mp4', '')));
      if (files.length > 0) {
        const found = path.join(assetsDir, files[0]);
        logger.info(`Found alternate: ${found.split(/[\\/]/).pop()}`);
        segment.video._resolvedPath = found;
      } else {
        logger.error(`Cannot find video for clip "${clipId}" — skipping segment`);
        continue;
      }
    } else {
      segment.video._resolvedPath = sourcePath;
    }

    // Verify TTS audio
    const ttsId = segment.audio?.source || '';
    const ttsPath = path.join(ttsDir, ttsId);
    if (ttsId && !fs.existsSync(ttsPath)) {
      logger.warn(`Missing TTS: ${ttsId}`);
    }

    verifiedSegments.push(segment);
  }

  if (verifiedSegments.length === 0) {
    logger.error('No valid segments to render');
    return null;
  }

  // ─── Step 2: Process each segment ────────────────────────────────
  const processedClips = [];

  for (let i = 0; i < verifiedSegments.length; i++) {
    const segment = verifiedSegments[i];
    const clipPath = segment.video._resolvedPath;
    const action = segment.video?.action || '';
    const ttsPath = segment.audio?.source
      ? path.join(ttsDir, segment.audio.source)
      : null;

    const segmentOutput = path.join(tmpDir, `segment_${String(i).padStart(2, '0')}.mp4`);

    // Build per-segment processing
    let videoFilters = [];

    // Scale + crop to 1080x1920
    videoFilters.push(`scale=${OUTPUT_W}:${OUTPUT_H}:flags=lanczos:force_original_aspect_ratio=increase,crop=${OUTPUT_W}:${OUTPUT_H}`);

    // Zoom effect if specified
    if (segment.effects?.includes('zoom')) {
      videoFilters.push('zoompan=z=zoom+0.01:d=125');
    }

    // Build audio filter
    let audioFilter = '';
    if (ttsPath && fs.existsSync(ttsPath)) {
      // Mix original audio (low) + TTS (high)
      audioFilter = `-i "${ttsPath}" -filter_complex "[0:a]volume=0.1[bg];[1:a]adelay=${0.1 * 1000}[vo];[bg][vo]amix=inputs=2:duration=first"`;
    }

    const vf = videoFilters.join(',');

    try {
      // If we need to handle the segment timing, use trim
      const duration = segment.end_time 
        ? `-t ${(parseFloat(segment.end_time) - parseFloat(segment.start_time || 0)).toFixed(1)}`
        : '';

      const audioInput = ttsPath && fs.existsSync(ttsPath) ? `-i "${ttsPath}"` : '';
      const filterComplex = ttsPath && fs.existsSync(ttsPath)
        ? `-filter_complex "[0:a]volume=0.1[bg];[1:a]adelay=100[vo];[bg][vo]amix=inputs=2:duration=first" -map 0:v -map "[a]"`
        : '';

      const cmd = `ffmpeg -y -i "${clipPath}" ${audioInput} ${duration} ` +
        `-vf "${vf}" ${filterComplex} ` +
        `-c:v ffv1 -level 3 -coder rice -slices 24 -slices-crc 32 ` +
        `-pix_fmt yuv444p10le -c:a flac -ar 48000 -shortest "${segmentOutput}"`;

      execSync(cmd, { timeout: 180000, maxBuffer: 50 * 1024 * 1024 });

      if (fs.existsSync(segmentOutput) && fs.statSync(segmentOutput).size > 50000) {
        processedClips.push(segmentOutput);
        logger.success(`  Segment ${i + 1}/${verifiedSegments.length}: ${(fs.statSync(segmentOutput).size / 1024 / 1024).toFixed(1)}MB`);
      } else {
        logger.warn(`  Segment ${i + 1}: output too small, using source directly`);
        processedClips.push(clipPath);
      }
    } catch (e) {
      logger.warn(`  Segment ${i + 1} encoding failed: ${e.message.substring(0, 60)}`);
      processedClips.push(clipPath); // Use raw source as fallback
    }
  }

  if (processedClips.length === 0) {
    logger.error('No processed clips to concatenate');
    return null;
  }

  // ─── Step 3: Concatenate all segments ────────────────────────────
  logger.info(`Concatenating ${processedClips.length} clips...`);

  const concatFile = buildConcatFile(processedClips.map(p => ({ videoPath: p })), tmpDir);

  try {
    // Use FFmpeg concat demuxer for lossless concatenation when possible
    const firstUseConcat = processedClips.every(c => c.endsWith('.mp4'));
    let concatCmd;

    if (firstUseConcat) {
      concatCmd = `ffmpeg -y -f concat -safe 0 -i "${concatFile}" ` +
        `-c:v ffv1 -level 3 -coder rice -slices 24 -slices-crc 32 ` +
        `-pix_fmt yuv444p10le -c:a flac -ar 48000 "${outputPath}"`;
    } else {
      // Fallback: use concat filter
      const inputs = processedClips.map(c => `-i "${c}"`).join(' ');
      const streams = processedClips.map((_, i) => `[${i}:v][${i}:a]`).join('');
      concatCmd = `ffmpeg -y ${inputs} ` +
        `-filter_complex "${streams}concat=n=${processedClips.length}:v=1:a=1[vo][ao]" ` +
        `-map "[vo]" -map "[ao]" -c:v ffv1 -level 3 -coder rice -slices 24 -slices-crc 32 ` +
        `-pix_fmt yuv444p10le -c:a flac -ar 48000 "${outputPath}"`;
    }

    execSync(concatCmd, { timeout: 300000, maxBuffer: 100 * 1024 * 1024 });

    // ─── Step 4: Burn subtitles ─────────────────────────────────────
    if (timeline.some(s => (s.captions || []).length > 0)) {
      const assContent = generateAssSubtitles(timeline);
      const assPath = path.join(tmpDir, 'captions.ass');
      fs.writeFileSync(assPath, assContent, 'utf8');

      const subbedPath = outputPath.replace('.mp4', '_captioned.mp4');

      try {
        execSync(
          `ffmpeg -y -i "${outputPath}" -vf "ass='${assPath.replace(/\\/g, '/').replace(/'/g, "'\\\\''")}'" ` +
          `-c:v ffv1 -level 3 -coder rice -slices 24 -slices-crc 32 ` +
          `-pix_fmt yuv444p10le -c:a flac -ar 48000 "${subbedPath}" 2>/dev/null`,
          { timeout: 120000 }
        );

        if (fs.existsSync(subbedPath) && fs.statSync(subbedPath).size > 50000) {
          // Replace output with captioned version
          try { fs.unlinkSync(outputPath); } catch {}
          fs.renameSync(subbedPath, outputPath);
          logger.success(`Subtitles burned in (${assPath.split(/[\\/]/).pop()})`);
        }
      } catch (e) {
        logger.warn(`Subtitle burning failed: ${e.message.substring(0, 60)}`);
        try { fs.unlinkSync(subbedPath); } catch {}
      }
    }

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      const sizeMB = (fs.statSync(outputPath).size / 1024 / 1024).toFixed(1);
      logger.success(`Final video: ${sizeMB}MB at ${OUTPUT_W}x${OUTPUT_H}`);

      // Save the manifest alongside the video for review
      const manifestPath = outputPath.replace('.mp4', '_manifest.json');
      fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
      logger.info(`Manifest saved: ${manifestPath}`);

      // Cleanup tmp
      try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}

      return outputPath;
    }
  } catch (e) {
    logger.error(`Concatenation failed: ${e.message.substring(0, 100)}`);
  }

  // Cleanup tmp on failure
  try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}

  return null;
}

module.exports = { render };