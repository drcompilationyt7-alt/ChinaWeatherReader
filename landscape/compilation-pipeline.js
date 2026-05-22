/**
 * Mr. WorldWideWebster — Landscape Compilation Pipeline
 *
 * Creates cinematic 1920x1080 landscape videos from:
 * - Downloaded clips (from YouTube, Bilibili, Pexels, Pixabay, etc.)
 * - AI-generated or manually written scripts
 * - TTS voiceover (Edge-TTS)
 * - Background music (auto-selected by mood)
 * - Text overlays, transitions, intro/outro cards
 *
 * Video types supported:
 * - Compilation: "Best of [topic]" with multiple clips + music
 * - Versus: "US vs UK Music" with side-by-side or alternating clips
 * - List: "Top 10 [x] Around the World" with numbered segments
 * - Cinematic: Architecture/cityscape montage
 *
 * All processing is done via FFmpeg — no paid APIs.
 */
const path = require('path');
const fs = require('fs');
const { execSync, exec } = require('child_process');
const { promisify } = require('util');
const { Logger } = require('../core/logger');

const execAsync = promisify(exec);

class CompilationPipeline {
  constructor() {
    this.logger = new Logger('CompilationPipeline');
    this.outputBase = path.join(__dirname, '..', 'output', 'landscape');
    if (!fs.existsSync(this.outputBase)) {
      fs.mkdirSync(this.outputBase, { recursive: true });
    }
  }

  /**
   * MAIN ENTRY: Create a landscape compilation video
   *
   * @param {Object} params
   * @param {string} params.title - Video title
   * @param {string} params.description - Video description
   * @param {string} params.videoType - "compilation" | "versus" | "listicle" | "cinematic"
   * @param {Array<string>} params.clipPaths - Local paths to downloaded clips
   * @param {Object} params.script - { fullScript, segments, estimatedDuration }
   * @param {string} params.musicPath - Path to background music (optional)
   * @param {string} params.musicMood - Mood for music auto-select (optional)
   * @param {Array<string>} params.imagePaths - Still images to include (optional)
   * @returns {Promise<Object>} - { videoPath, duration, metadata }
   */
  async createVideo(params) {
    const safeId = Date.now();
    const baseDir = path.join(this.outputBase, `landscape_${safeId}`);
    const clipsDir = path.join(baseDir, 'clips');
    const assetsDir = path.join(baseDir, 'assets');

    for (const dir of [baseDir, clipsDir, assetsDir]) {
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    }

    const title = params.title || 'Mr. WorldWideWebster - Landscape Video';
    const videoType = params.videoType || 'compilation';

    this.logger.header(`LANDSCAPE VIDEO: "${title.substring(0, 60)}"`);
    this.logger.info(`Type: ${videoType} | Clips: ${params.clipPaths?.length || 0}`);

    // Step 1: Normalize all clips to consistent format
    this.logger.info('Step 1: Normalizing clips...');
    const normalizedClips = await this._normalizeClips(params.clipPaths || [], clipsDir);
    this.logger.success(`Normalized ${normalizedClips.length} clips`);

    // Step 2: Generate TTS voiceover from script
    let audioPath = null;
    if (params.script?.fullScript || params.script?.text) {
      this.logger.info('Step 2: Generating TTS voiceover...');
      audioPath = await this._generateVoiceover(params.script, baseDir);
      this.logger.success(`Voiceover: ${path.basename(audioPath)}`);
    }

    // Step 3: Get or generate background music
    let musicPath = params.musicPath || null;
    if (!musicPath) {
      this.logger.info('Step 3: Finding background music...');
      try {
        const { MusicFinder } = require('./music-finder');
        const finder = new MusicFinder();

        // Determine target duration (from script or clips)
        let targetDuration = 60;
        if (audioPath) {
          targetDuration = this._getAudioDuration(audioPath);
        }

        const musicResult = await finder.findMusic({
          mood: params.musicMood || 'chill',
          category: videoType,
          videoTitle: title,
          duration: targetDuration,
        });

        if (musicResult) {
          musicPath = musicResult.filePath;
          this.logger.success(`Music: "${musicResult.title}"`);
        }
      } catch (error) {
        this.logger.warn(`Music search failed: ${error.message}`);
      }
    }

    // Step 4: Create intro card
    this.logger.info('Step 4: Creating intro card...');
    const introPath = await this._createIntroCard(title, videoType, assetsDir);
    this.logger.success('Intro card created');

    // Step 5: Create outro card
    this.logger.info('Step 5: Creating outro card...');
    const outroPath = await this._createOutroCard(assetsDir);
    this.logger.success('Outro card created');

    // Step 6: Build the final video
    this.logger.info('Step 6: Assembling final video...');
    const finalVideo = await this._assembleVideo({
      introPath,
      clips: normalizedClips,
      outroPath,
      audioPath,
      musicPath,
      outputPath: path.join(baseDir, `${title.replace(/[^a-zA-Z0-9]/g, '_').substring(0, 50)}.mp4`),
      imagePaths: params.imagePaths || [],
      videoType,
    });
    this.logger.success(`Final video: ${path.basename(finalVideo)}`);

    // Step 7: Get metadata
    const duration = this._getVideoDuration(finalVideo);
    const fileSize = fs.statSync(finalVideo).size;

    return {
      videoPath: finalVideo,
      duration,
      fileSize,
      title,
      videoType,
      clipsUsed: normalizedClips.length,
      hasVoiceover: !!audioPath,
      hasMusic: !!musicPath,
      generatedAt: new Date().toISOString(),
      outputDir: baseDir,
    };
  }

  /**
   * Normalize all clips to consistent 1920x1080, same framerate, same codec
   */
  async _normalizeClips(clipPaths, outputDir) {
    const normalized = [];

    for (let i = 0; i < clipPaths.length; i++) {
      const clip = clipPaths[i];
      if (!fs.existsSync(clip)) {
        this.logger.warn(`Clip not found: ${clip}`);
        continue;
      }

      const ext = path.extname(clip) || '.mp4';
      const outputFile = path.join(outputDir, `clip_${String(i).padStart(3, '0')}${ext}`);

      try {
        // Scale to 1920x1080, pad if needed, normalize framerate
        await execAsync(
          `ffmpeg -i "${clip}" ` +
          `-vf "scale=1920:1080:force_original_aspect_ratio=1,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30" ` +
          `-c:v libx264 -preset fast -crf 23 -c:a aac -ar 44100 ` +
          `-shortest "${outputFile}" -y 2>&1`,
          { timeout: 120000 }
        );

        if (fs.existsSync(outputFile)) {
          normalized.push(outputFile);
          this.logger.info(`  ✅ Clip ${i + 1}: ${path.basename(clip)}`);
        }
      } catch (error) {
        // If normalization fails, try just copying the stream
        this.logger.warn(`Normalization failed for clip ${i + 1}, trying direct copy...`);
        try {
          await execAsync(
            `ffmpeg -i "${clip}" -c copy -y "${outputFile}" 2>&1`,
            { timeout: 60000 }
          );
          if (fs.existsSync(outputFile)) normalized.push(outputFile);
        } catch {
          this.logger.warn(`Clip ${i + 1} could not be processed, skipping`);
        }
      }
    }

    return normalized;
  }

  /**
   * Generate TTS voiceover from script
   */
  async _generateVoiceover(script, outputDir) {
    const audioPath = path.join(outputDir, 'voiceover.mp3');
    const text = script.fullScript || script.text || '';

    if (!text.trim()) {
      throw new Error('Script text is empty');
    }

    // Use Edge-TTS if available, fallback to basic TTS
    try {
      const escapedText = text.replace(/"/g, '\\"').replace(/'/g, "\\'");
      const cmd = `edge-tts --voice "en-US-GuyNeural" --text "${escapedText}" --write-media "${audioPath}" 2>&1`;
      await execAsync(cmd, { timeout: 300000, maxBuffer: 50 * 1024 * 1024 });

      if (fs.existsSync(audioPath) && fs.statSync(audioPath).size > 1000) {
        return audioPath;
      }
    } catch {
      this.logger.warn('Edge-TTS failed, trying chunked approach...');
    }

    // Chunked fallback for long scripts
    return await this._chunkedTTS(text, audioPath);
  }

  /**
   * Chunked TTS for long scripts
   */
  async _chunkedTTS(text, outputPath) {
    const chunks = this._chunkText(text, 3000);
    const chunkFiles = [];

    for (let i = 0; i < chunks.length; i++) {
      const chunkPath = outputPath.replace('.mp3', `_chunk_${i}.mp3`);
      const escapedText = chunks[i].replace(/"/g, '\\"').replace(/'/g, "\\'");

      try {
        await execAsync(
          `edge-tts --voice "en-US-GuyNeural" --text "${escapedText}" --write-media "${chunkPath}" 2>&1`,
          { timeout: 120000 }
        );
        if (fs.existsSync(chunkPath)) chunkFiles.push(chunkPath);
      } catch {
        this.logger.warn(`Chunk ${i} TTS failed`);
      }
    }

    if (chunkFiles.length === 0) {
      throw new Error('All TTS chunks failed');
    }

    // Concatenate chunks
    const concatFile = outputPath.replace('.mp3', '_concat.txt');
    const concatContent = chunkFiles.map(f => `file '${f.replace(/'/g, "'\\''")}'`).join('\n');
    fs.writeFileSync(concatFile, concatContent);

    await execAsync(
      `ffmpeg -f concat -safe 0 -i "${concatFile}" -c copy "${outputPath}" -y 2>&1`,
      { timeout: 60000 }
    );

    // Cleanup
    for (const f of chunkFiles) {
      try { fs.unlinkSync(f); } catch {}
    }
    try { fs.unlinkSync(concatFile); } catch {}

    return outputPath;
  }

  /**
   * Split text into chunks
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
   * Create intro card with title text on animated background
   * Drawtext filter strings need careful escaping for cross-platform
   */
  async _createIntroCard(title, videoType, outputDir) {
    const outputPath = path.join(outputDir, 'intro.mp4');
    const duration = 4;

    try {
      // Use simpler approach: write text to a subtitle/ASS file for clean rendering
      const assPath = path.join(outputDir, 'intro.ass');
      const safeTitle = title.replace(/['"]/g, '').replace(/[:]/g, ' ');
      const assContent = `[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,2,10,10,40,1
Style: Subtitle,Arial,28,&H00FFD700,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,2,10,10,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:04.00,Title,,0,0,0,,${safeTitle}
Dialogue: 0,0:00:01.50,0:00:04.00,Subtitle,,0,0,0,,Mr. WorldWideWebster 🌍`;

      fs.writeFileSync(assPath, assContent);

      await execAsync(
        `ffmpeg -f lavfi -i "color=c=#1a1a2e:s=1920x1080:d=${duration}:r=30" ` +
        `-vf "ass='${assPath.replace(/'/g, "'\\''")}'" ` +
        `-c:v libx264 -preset fast -pix_fmt yuv420p "${outputPath}" -y 2>&1`,
        { timeout: 30000 }
      );

      if (fs.existsSync(outputPath)) return outputPath;
    } catch (error) {
      this.logger.warn(`Intro card failed: ${error.message}`);
    }

    // Ultra simple fallback
    await execAsync(
      `ffmpeg -f lavfi -i "color=c=#1a1a2e:s=1920x1080:d=${duration}:r=30" -c:v libx264 -preset fast "${outputPath}" -y 2>&1`,
      { timeout: 30000 }
    );

    return outputPath;
  }

  /**
   * Create outro card with subscribe CTA
   */
  async _createOutroCard(outputDir) {
    const outputPath = path.join(outputDir, 'outro.mp4');
    const duration = 3;

    try {
      const assPath = path.join(outputDir, 'outro.ass');
      const assContent = `[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Arial,56,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,2,10,10,40,1
Style: Subtitle,Arial,28,&H00FFD700,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,2,10,10,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:03.00,Title,,0,0,0,,Like & Subscribe
Dialogue: 0,0:00:01.00,0:00:03.00,Subtitle,,0,0,0,,Follow Mr. WorldWideWebster 🌍`;

      fs.writeFileSync(assPath, assContent);

      await execAsync(
        `ffmpeg -f lavfi -i "color=c=#0f3460:s=1920x1080:d=${duration}:r=30" ` +
        `-vf "ass='${assPath.replace(/'/g, "'\\''")}'" ` +
        `-c:v libx264 -preset fast -pix_fmt yuv420p "${outputPath}" -y 2>&1`,
        { timeout: 30000 }
      );

      if (fs.existsSync(outputPath)) return outputPath;
    } catch {
      // Ultra simple fallback
      await execAsync(
        `ffmpeg -f lavfi -i "color=c=#0f3460:s=1920x1080:d=${duration}:r=30" -c:v libx264 -preset fast "${outputPath}" -y 2>&1`,
        { timeout: 30000 }
      );
    }

    return outputPath;
  }

  /**
   * Assemble the final video: intro → clips → outro + audio + music
   */
  async _assembleVideo(params) {
    const { introPath, clips, outroPath, audioPath, musicPath, outputPath, imagePaths, videoType } = params;

    // Build a concat file for all video segments
    const concatFilePath = path.join(path.dirname(outputPath), 'concat_list.txt');
    const segments = [];

    // Add intro
    if (fs.existsSync(introPath)) segments.push(introPath);

    // Add clips
    for (const clip of clips) {
      if (fs.existsSync(clip)) segments.push(clip);
    }

    // Add images as video segments (3 seconds each)
    if (imagePaths) {
      for (const img of imagePaths) {
        if (fs.existsSync(img)) {
          const imgVideo = img.replace(path.extname(img), '_vid.mp4');
          try {
            await execAsync(
              `ffmpeg -loop 1 -i "${img}" -c:v libx264 -t 3 -pix_fmt yuv420p -vf "scale=1920:1080:force_original_aspect_ratio=1,pad=1920:1080:(ow-iw)/2:(oh-ih)/2" "${imgVideo}" -y 2>&1`,
              { timeout: 30000 }
            );
            if (fs.existsSync(imgVideo)) segments.push(imgVideo);
          } catch {}
        }
      }
    }

    // Add outro
    if (fs.existsSync(outroPath)) segments.push(outroPath);

    // Write concat file
    const concatContent = segments.map(s => `file '${s.replace(/'/g, "'\\''")}'`).join('\n');
    fs.writeFileSync(concatFilePath, concatContent);

    // Step A: Concatenate all video segments
    const concatVideo = path.join(path.dirname(outputPath), 'concat_video.mp4');
    try {
      await execAsync(
        `ffmpeg -f concat -safe 0 -i "${concatFilePath}" -c copy "${concatVideo}" -y 2>&1`,
        { timeout: 300000 }
      );
    } catch (error) {
      // If concat with copy fails, re-encode
      this.logger.warn('Fast concat failed, re-encoding...');
      await execAsync(
        `ffmpeg -f concat -safe 0 -i "${concatFilePath}" -c:v libx264 -preset fast -crf 23 -c:a aac "${concatVideo}" -y 2>&1`,
        { timeout: 300000 }
      );
    }

    // Step B: Mix audio tracks
    if (audioPath && fs.existsSync(audioPath)) {
      if (musicPath && fs.existsSync(musicPath)) {
        // Voiceover + background music
        const mixedAudio = path.join(path.dirname(outputPath), 'mixed_audio.mp3');
        await execAsync(
          `ffmpeg -i "${audioPath}" -i "${musicPath}" -filter_complex ` +
          `"[1:a]volume=-18dB[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2" ` +
          `-c:a libmp3lame "${mixedAudio}" -y 2>&1`,
          { timeout: 120000 }
        );

        // Combine mixed audio with video
        await execAsync(
          `ffmpeg -i "${concatVideo}" -i "${mixedAudio}" -c:v copy -c:a aac -shortest "${outputPath}" -y 2>&1`,
          { timeout: 120000 }
        );
      } else {
        // Voiceover only
        await execAsync(
          `ffmpeg -i "${concatVideo}" -i "${audioPath}" -c:v copy -c:a aac -shortest "${outputPath}" -y 2>&1`,
          { timeout: 120000 }
        );
      }
    } else if (musicPath && fs.existsSync(musicPath)) {
      // Background music only (no voiceover)
      await execAsync(
        `ffmpeg -i "${concatVideo}" -i "${musicPath}" -c:v copy -c:a aac -shortest "${outputPath}" -y 2>&1`,
        { timeout: 120000 }
      );
    } else {
      // No audio at all
      if (fs.existsSync(concatVideo)) {
        fs.copyFileSync(concatVideo, outputPath);
      }
    }

    return outputPath;
  }

  /**
   * Get audio duration using ffprobe
   */
  _getAudioDuration(audioPath) {
    try {
      const output = execSync(
        `ffprobe -i "${audioPath}" -show_entries format=duration -v quiet -of csv="p=0"`,
        { timeout: 10000 }
      ).toString().trim();
      return Math.ceil(parseFloat(output) || 0);
    } catch {
      return 0;
    }
  }

  /**
   * Get video duration using ffprobe
   */
  _getVideoDuration(videoPath) {
    try {
      const output = execSync(
        `ffprobe -i "${videoPath}" -show_entries format=duration -v quiet -of csv="p=0"`,
        { timeout: 10000 }
      ).toString().trim();
      return Math.ceil(parseFloat(output) || 0);
    } catch {
      return 0;
    }
  }

  /**
   * Concat for versus-style videos (side-by-side)
   * Creates a video with two clips playing simultaneously
   */
  async _createVersusVideo(clipAPath, clipBPath, outputPath, labelA, labelB) {
    this.logger.info('Creating versus-style video...');

    try {
      await execAsync(
        `ffmpeg -i "${clipAPath}" -i "${clipBPath}" ` +
        `-filter_complex ` +
        `"[0:v]scale=960:1080:force_original_aspect_ratio=1,pad=960:1080:(ow-iw)/2:(oh-ih)/2,drawtext=text='${labelA || 'A'}':x=10:y=10:fontsize=24:fontcolor=white[v0];` +
        `[1:v]scale=960:1080:force_original_aspect_ratio=1,pad=960:1080:(ow-iw)/2:(oh-ih)/2,drawtext=text='${labelB || 'B'}':x=10:y=10:fontsize=24:fontcolor=white[v1];` +
        `[v0][v1]hstack=inputs=2[v]" ` +
        `-map "[v]" -c:v libx264 -preset fast "${outputPath}" -y 2>&1`,
        { timeout: 120000 }
      );

      return outputPath;
    } catch (error) {
      this.logger.warn(`Versus video failed: ${error.message}`);
      // Fallback to alternating clips
      return null;
    }
  }
}

module.exports = { CompilationPipeline };