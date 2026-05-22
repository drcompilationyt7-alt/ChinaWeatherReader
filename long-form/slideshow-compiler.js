/**
 * Mr. WorldWideWebster — Long-Form Slideshow Compiler
 * 
 * Creates longer videos (3-60+ minutes) using:
 * - AI-generated scripts
 * - Free Edge-TTS voiceover
 * - Stock footage/images as B-roll
 * - Ken Burns effect for visual interest
 * - Background music
 * 
 * No paid APIs needed. Everything runs locally via FFmpeg.
 * 
 * Output formats:
 * - News summary (5-10 min)
 * - Deep dive explainer (10-30 min)
 * - Compilation (15-60 min)
 * - Documentary style (20-60 min)
 */
const path = require('path');
const fs = require('fs');
const { exec } = require('child_process');
const { promisify } = require('util');
const { Logger } = require('../core/logger');

const execAsync = promisify(exec);

class SlideshowCompiler {
  constructor() {
    this.logger = new Logger('SlideshowCompiler');
  }

  /**
   * Compile a long-form video from a script + images
   * @param {Object} params — { script, images, outputPath, title, backgroundMusic }
   * @returns {Promise<Object>} — { videoPath, duration, metadata }
   */
  async compile(params) {
    const { script, images, outputPath, title, backgroundMusic } = params;
    const baseDir = path.dirname(outputPath);

    if (!fs.existsSync(baseDir)) {
      fs.mkdirSync(baseDir, { recursive: true });
    }

    this.logger.info(`Compiling long-form video: "${title?.substring(0, 50) || 'Untitled'}"`);

    // Step 1: Generate TTS for the script
    const audioPath = await this._generateVoiceover(script, baseDir);
    this.logger.info(`Voiceover generated: ${path.basename(audioPath)}`);

    // Step 2: Get audio duration
    const audioDuration = await this._getAudioDuration(audioPath);
    this.logger.info(`Audio duration: ${audioDuration}s`);

    // Step 3: Create video segments with Ken Burns effect
    const videoPath = await this._createKenBurnsVideo(images, audioDuration, baseDir);
    this.logger.info(`Video segments created`);

    // Step 4: Combine audio + video
    const finalPath = await this._combineAudioVideo(videoPath, audioPath, backgroundMusic, outputPath);
    this.logger.info(`Final video compiled: ${path.basename(finalPath)}`);

    // Step 5: Clean up temp files
    await this._cleanup(baseDir);

    return {
      videoPath: finalPath,
      duration: audioDuration,
      title: title,
      segments: images?.length || 0,
      metadata: {
        generatedAt: new Date().toISOString(),
        audioSource: 'Edge-TTS',
        effect: 'Ken Burns',
      },
    };
  }

  /**
   * Generate voiceover audio from script using Edge-TTS
   */
  async _generateVoiceover(script, outputDir) {
    const audioPath = path.join(outputDir, 'voiceover.mp3');

    if (!script?.fullScript && !script?.text) {
      throw new Error('No script provided for voiceover');
    }

    const text = script.fullScript || script.text;

    try {
      const escapedText = text.replace(/"/g, '\\"').replace(/'/g, "\\'");
      const cmd = `edge-tts --voice "en-US-GuyNeural" --text "${escapedText}" --write-media "${audioPath}" 2>&1`;
      await execAsync(cmd, { timeout: 300000, maxBuffer: 50 * 1024 * 1024 });
      
      if (fs.existsSync(audioPath)) {
        return audioPath;
      }
      throw new Error('Audio file not created');
    } catch (error) {
      this.logger.warn(`Edge-TTS failed for long script, chunking...`);
      return await this._chunkedTTS(text, audioPath);
    }
  }

  /**
   * For very long scripts, chunk and concatenate
   */
  async _chunkedTTS(text, outputPath) {
    const chunks = this._chunkText(text, 3000); // 3000 char chunks
    const chunkFiles = [];

    for (let i = 0; i < chunks.length; i++) {
      const chunkPath = outputPath.replace('.mp3', `_chunk_${i}.mp3`);
      const escapedText = chunks[i].replace(/"/g, '\\"').replace(/'/g, "\\'");
      
      try {
        await execAsync(
          `edge-tts --voice "en-US-GuyNeural" --text "${escapedText}" --write-media "${chunkPath}" 2>&1`,
          { timeout: 120000 }
        );
        if (fs.existsSync(chunkPath)) {
          chunkFiles.push(chunkPath);
        }
      } catch (error) {
        this.logger.warn(`Chunk ${i} TTS failed: ${error.message}`);
      }
    }

    if (chunkFiles.length === 0) {
      throw new Error('All TTS chunks failed');
    }

    // Concatenate audio chunks using FFmpeg
    const concatFile = outputPath.replace('.mp3', '_concat.txt');
    const concatContent = chunkFiles.map(f => `file '${f.replace(/'/g, "'\\''")}'`).join('\n');
    fs.writeFileSync(concatFile, concatContent);

    await execAsync(
      `ffmpeg -f concat -safe 0 -i "${concatFile}" -c copy "${outputPath}" 2>&1`,
      { timeout: 60000 }
    );

    // Clean up chunks
    for (const f of chunkFiles) {
      try { fs.unlinkSync(f); } catch {}
    }
    try { fs.unlinkSync(concatFile); } catch {}

    return outputPath;
  }

  /**
   * Split text into chunks at sentence boundaries
   */
  _chunkText(text, maxChars) {
    const sentences = text.match(/[^.!?\n]+[.!?\n]+/g) || [text];
    const chunks = [];
    let current = '';

    for (const sentence of sentences) {
      if ((current + sentence).length > maxChars && current.length > 0) {
        chunks.push(current.trim());
        current = sentence;
      } else {
        current += sentence;
      }
    }
    if (current.trim()) chunks.push(current.trim());

    return chunks;
  }

  /**
   * Get audio duration using FFprobe
   */
  async _getAudioDuration(audioPath) {
    try {
      const { stdout } = await execAsync(
        `ffprobe -i "${audioPath}" -show_entries format=duration -v quiet -of csv="p=0"`,
        { timeout: 10000 }
      );
      return Math.ceil(parseFloat(stdout.trim()) || 60);
    } catch {
      return 60;
    }
  }

  /**
   * Create video with Ken Burns effect from images
   * Each image gets ~8 seconds with smooth zoom/pan
   */
  async _createKenBurnsVideo(images, duration, outputDir) {
    const videoPath = path.join(outputDir, 'slideshow.mp4');
    
    // If no images, create a gradient background
    if (!images || images.length === 0) {
      await execAsync(
        `ffmpeg -f lavfi -i "color=c=#1a1a2e:s=1920x1080:d=${duration}:r=30" -c:v libx264 -preset fast "${videoPath}" 2>&1`,
        { timeout: 120000 }
      );
      return videoPath;
    }

    // Create image list with durations
    const imageDuration = Math.max(6, duration / images.length);
    const filterComplex = [];
    const inputs = [];

    for (let i = 0; i < images.length; i++) {
      if (fs.existsSync(images[i])) {
        inputs.push(`-i "${images[i]}"`);
        // Ken Burns effect: zoom from 1.0 to 1.15, pan slightly
        const zoomStart = 1.0;
        const zoomEnd = 1.15;
        const xStart = 0;
        const xEnd = Math.random() * 0.05;
        const yStart = 0;
        const yEnd = Math.random() * 0.05;

        filterComplex.push(
          `[${i}:v]format=yuv420p,scale=1920:1080:force_original_aspect_ratio=1,` +
          `zoompan=z='if(eq(on,1),${zoomStart},${zoomStart}+(${zoomEnd}-${zoomStart})*(on-1)/${imageDuration * 30})':` +
          `x='if(eq(on,1),${xStart},${xStart}+(${xEnd}-${xStart})*(on-1)/${imageDuration * 30})':` +
          `y='if(eq(on,1),${yStart},${yStart}+(${yEnd}-${yStart})*(on-1)/${imageDuration * 30})':` +
          `d=${imageDuration * 30}:s=1920x1080[v${i}]`
        );
      }
    }

    if (inputs.length === 0) {
      // All images failed, use color background
      await execAsync(
        `ffmpeg -f lavfi -i "color=c=#1a1a2e:s=1920x1080:d=${duration}:r=30" -c:v libx264 -preset fast "${videoPath}" 2>&1`,
        { timeout: 120000 }
      );
      return videoPath;
    }

    const concatFilter = filterComplex.map((_, i) => `[v${i}]`).join('');
    const filterStr = `${filterComplex.join(';')};${concatFilter}concat=n=${inputs.length}:v=1:a=0[v]`;

    const cmd = `ffmpeg ${inputs.join(' ')} -filter_complex "${filterStr}" -map "[v]" -c:v libx264 -preset fast -r 30 "${videoPath}" 2>&1`;

    try {
      await execAsync(cmd, { timeout: 300000 });
    } catch (error) {
      this.logger.warn(`Ken Burns failed: ${error.message}, using fallback`);
      // Fallback: simple image slideshow
      const fallbackCmd = `ffmpeg ${inputs.join(' ')} -filter_complex "${inputs.map((_, i) => `[${i}:v]`).join('')}concat=n=${inputs.length}:v=1:a=0" -c:v libx264 -preset fast "${videoPath}" 2>&1`;
      await execAsync(fallbackCmd, { timeout: 300000 });
    }

    return videoPath;
  }

  /**
   * Combine audio track with video
   */
  async _combineAudioVideo(videoPath, audioPath, backgroundMusic, outputPath) {
    const finalPath = outputPath;

    if (backgroundMusic && fs.existsSync(backgroundMusic)) {
      // Mix voiceover + background music (voiceover at 0dB, music at -20dB)
      const mixedAudio = path.join(path.dirname(outputPath), 'mixed_audio.mp3');
      await execAsync(
        `ffmpeg -i "${audioPath}" -i "${backgroundMusic}" -filter_complex ` +
        `"[1:a]volume=-20dB[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2" ` +
        `-c:a libmp3lame "${mixedAudio}" 2>&1`,
        { timeout: 120000 }
      );
      
      await execAsync(
        `ffmpeg -i "${videoPath}" -i "${mixedAudio}" -c:v copy -c:a aac -shortest "${finalPath}" 2>&1`,
        { timeout: 120000 }
      );
    } else {
      await execAsync(
        `ffmpeg -i "${videoPath}" -i "${audioPath}" -c:v copy -c:a aac -shortest "${finalPath}" 2>&1`,
        { timeout: 120000 }
      );
    }

    return finalPath;
  }

  /**
   * Clean up temporary files
   */
  async _cleanup(dir) {
    try {
      const files = fs.readdirSync(dir);
      for (const file of files) {
        if (file.startsWith('_') || file.includes('_chunk_') || file.includes('_concat')) {
          fs.unlinkSync(path.join(dir, file));
        }
      }
    } catch {}
  }
}

module.exports = { SlideshowCompiler };