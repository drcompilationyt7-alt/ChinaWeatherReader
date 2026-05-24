/**
 * Mr. WorldWideWebster — Clip Editor
 * 
 * Handles all video editing operations:
 * - Trimming videos to short form (15-30s)
 * - Audio ducking (lower original audio during voiceover, restore after)
 * - Text overlay for translations
 * - Meme/streamer clips: minimal editing, keep original audio
 * 
 * Audio Mix Strategy:
 * - MEME: Original audio 100%, optional text overlay for translation
 * - STREAMER: Original audio 100%, no changes
 * - EXPLAINER: Voiceover segment: original ducked to 15% → voiceover plays
 *              → then original audio restored to 100%
 */
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const { Logger } = require('./logger');

class ClipEditor {
  constructor() {
    this.logger = new Logger('ClipEditor');
  }

  /**
   * Edit a video based on its type
   */
  async editVideo(videoPath, options) {
    const type = options.type || 'clip'; // 'clip', 'streamer', 'explainer'
    const outputPath = options.outputPath || videoPath.replace(/\.\w+$/, '_edited.mp4');
    
    this.logger.info(`Editing video type=${type}: ${path.basename(videoPath)}`);
    
    switch (type) {
      case 'clip':
      case 'streamer':
        return await this._editClip(videoPath, outputPath, options);
      case 'explainer':
        return await this._editExplainer(videoPath, outputPath, options);
      default:
        return await this._editClip(videoPath, outputPath, options);
    }
  }

  /**
   * Edit a clip/meme video:
   * - Trim to duration (default 15-30s starting from offset)
   * - Add text overlay for translation (if provided)
   * - Keep original audio at 100%
   */
  async _editClip(videoPath, outputPath, options) {
    const startTime = options.startTime || 3;
    const duration = options.duration || 25;
    const textOverlay = options.textOverlay || ''; // Translation if any
    
    try {
      if (textOverlay && textOverlay.length > 0) {
        // Trim + text overlay
        const textFile = outputPath + '_text.txt';
        fs.writeFileSync(textFile, textOverlay, 'utf8');
        
        const cmd = `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} ` +
          `-vf "drawtext=textfile='${textFile.replace(/\\/g, '/')}':` +
          `fontsize=48:fontcolor=white:x=(w-text_w)/2:y=h-150:font=Arial:box=1:boxcolor=black@0.5" ` +
          `-c:v libx264 -preset ultrafast -crf 28 -c:a aac -b:a 128k "${outputPath}"`;
        
        execSync(cmd, { timeout: 60000, maxBuffer: 50 * 1024 * 1024 });
        try { fs.unlinkSync(textFile); } catch {}
      } else {
        // Just trim with original audio
        const cmd = `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${duration} ` +
          `-c:v libx264 -preset ultrafast -crf 28 -c:a aac -b:a 128k "${outputPath}"`;
        execSync(cmd, { timeout: 60000, maxBuffer: 50 * 1024 * 1024 });
      }
      
      if (fs.existsSync(outputPath)) {
        this.logger.success(`Clip edited: ${outputPath}`);
        return outputPath;
      }
    } catch (error) {
      this.logger.warn(`Clip editing failed: ${error.message}`);
    }
    return null;
  }

  /**
   * Edit an explainer video with voiceover:
   * - First segment (3-5s): voiceover plays, original audio ducked to 15%
   * - Then: original audio restored to 100% for rest of clip
   * - Total duration: 15-30s
   * 
   * FFmpeg audio mixing:
   * ┬──────────────┬───────────────┬─────────────────┐
   * │ Original │ voiceover    │ voice ends,       │
   * │ audio    │ starts      │ orig audio back   │
   * │ 100%     │ orig->15%   │ to 100%          │
   * └──────────────┴───────────────┴─────────────────◘
   *              ^-- voiceover      ^-- audio restore
   *              lasts ~5 seconds
   */
  async _editExplainer(videoPath, outputPath, options) {
    const startTime = options.startTime || 3;
    const totalDuration = options.duration || 25;
    const voiceoverPath = options.voiceoverPath; // Path to voiceover audio file
    const voiceoverDuration = options.voiceoverDuration || 5; // How long the voiceover takes
    const voiceoverText = options.textOverlay || '';
    
    try {
      const tmpDir = path.dirname(outputPath);
      const baseName = path.basename(outputPath, path.extname(outputPath));
      
      // Step 1: Trim the original video
      const trimmedPath = path.join(tmpDir, `${baseName}_trimmed.mp4`);
      let trimCmd = `ffmpeg -y -ss ${startTime} -i "${videoPath}" -t ${totalDuration} ` +
        `-c:v libx264 -preset ultrafast -crf 28 -c:a aac -b:a 128k "${trimmedPath}"`;
      execSync(trimCmd, { timeout: 60000, maxBuffer: 50 * 1024 * 1024 });
      
      if (!fs.existsSync(trimmedPath)) {
        throw new Error('Trim produced no output');
      }
      
      // Step 2: Extract original audio
      const origAudioPath = path.join(tmpDir, `${baseName}_orig_audio.aac`);
      execSync(`ffmpeg -y -i "${trimmedPath}" -vn -c:a copy "${origAudioPath}"`, 
        { timeout: 30000, maxBuffer: 50 * 1024 * 1024 });
      
      if (voiceoverPath && fs.existsSync(voiceoverPath)) {
        // Step 3: Create ducked audio
        // We split original audio into 3 parts:
        // - Part A: Before voiceover (full volume)
        // - Part B: During voiceover (ducked to 15%)
        // - Part C: After voiceover (full volume)
        
        const vDuration = voiceoverDuration;
        const vStart = 1; // Voiceover starts 1 second in (brief intro silence)
        
        // Create the mixed audio
        const mixedAudioPath = path.join(tmpDir, `${baseName}_mixed.aac`);
        
        // Use ffmpeg audio filter:
        // Stream 0: original audio
        // Stream 1: voiceover
        // Mix them with volume envelope on original during voiceover segment
        const audioFilter = 
          `[0:a]volume=1[A];` + // Full volume original
          `[0:a]volume=enable='between(t,${vStart},${vStart + vDuration})':volume=0.15[B];` + // Duck to 15% during voiceover
          `[B][1:a]amix=inputs=2:duration=first:dropout_transition=2[C];` + // Mix ducked original + voiceover
          `[A][C]acrossfade=d=0.1[out]`; // Crossfade to avoid clicks
        
        const mixCmd = `ffmpeg -y -i "${trimmedPath}" -i "${voiceoverPath}" ` +
          `-filter_complex "${audioFilter}" -map "[out]" -c:a aac -b:a 128k "${mixedAudioPath}"`;
        
        execSync(mixCmd, { timeout: 30000, maxBuffer: 50 * 1024 * 1024 });
        
        // Step 4: Replace audio in video
        if (fs.existsSync(mixedAudioPath)) {
          execSync(`ffmpeg -y -i "${trimmedPath}" -i "${mixedAudioPath}" -c:v copy -c:a aac -map 0:v:0 -map 1:a:0 -shortest "${outputPath}"`,
            { timeout: 60000, maxBuffer: 50 * 1024 * 1024 });
        }
        
        // Cleanup mixed audio
        try { fs.unlinkSync(mixedAudioPath); } catch {}
      } else {
        // No voiceover - just add text overlay if needed
        if (voiceoverText) {
          const textFile = outputPath + '_text.txt';
          fs.writeFileSync(textFile, voiceoverText, 'utf8');
          const cmd = `ffmpeg -y -i "${trimmedPath}" ` +
            `-vf "drawtext=textfile='${textFile.replace(/\\/g, '/')}':` +
            `fontsize=48:fontcolor=white:x=(w-text_w)/2:y=h-150:font=Arial:box=1:boxcolor=black@0.5" ` +
            `-c:v libx264 -preset ultrafast -crf 28 -c:a aac -b:a 128k "${outputPath}"`;
          execSync(cmd, { timeout: 60000, maxBuffer: 50 * 1024 * 1024 });
          try { fs.unlinkSync(textFile); } catch {}
        } else {
          // No edits needed, just copy trimmed
          fs.copyFileSync(trimmedPath, outputPath);
        }
      }
      
      // Cleanup temp files
      try { fs.unlinkSync(trimmedPath); } catch {}
      try { fs.unlinkSync(origAudioPath); } catch {}
      
      if (fs.existsSync(outputPath)) {
        this.logger.success(`Explainer edited: ${outputPath}`);
        return outputPath;
      }
    } catch (error) {
      this.logger.warn(`Explainer editing failed: ${error.message}`);
    }
    return null;
  }

  /**
   * Generate a simple voiceover audio using edge-tts
   */
  async generateVoiceover(text, outputPath, voice = 'en-US-JennyNeural') {
    try {
      const cmd = `edge-tts --voice "${voice}" --text "${text.replace(/"/g, '\\"')}" --write-media "${outputPath}"`;
      execSync(cmd, { timeout: 30000 });
      if (fs.existsSync(outputPath)) {
        this.logger.info(`Voiceover generated: ${outputPath}`);
        return outputPath;
      }
    } catch (error) {
      this.logger.warn(`Voiceover generation failed: ${error.message}`);
    }
    return null;
  }

  /**
   * Get video duration in seconds using ffprobe
   */
  getDuration(videoPath) {
    try {
      const cmd = `ffprobe -v error -show_entries format=duration -of csv=p=0 "${videoPath}"`;
      const output = execSync(cmd, { timeout: 10000 }).toString().trim();
      return parseFloat(output) || 0;
    } catch {
      return 0;
    }
  }
}

module.exports = { ClipEditor };
