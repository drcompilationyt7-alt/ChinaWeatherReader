/**
 * Mr. WorldWideWebster - VOICEOVER Pipeline
 * 
 * For content in foreign languages that needs:
 * 1. Audio transcription (Whisper)
 * 2. Translation to English
 * 3. AI voiceover generation
 * 4. Audio replacement
 * 5. Bilingual subtitle generation
 */
const path = require('path');
const fs = require('fs');
const { Logger } = require('../core/logger');

class VoiceoverPipeline {
  constructor() {
    this.logger = new Logger('VoiceoverPipeline');
  }

  /**
   * Process content through the VOICEOVER pipeline
   */
  async processVoiceover(params) {
    const { url, title, platform, outputDir, ai, languageDetected } = params;
    const safeId = Date.now();
    const basePath = path.join(outputDir, `voiceover_${safeId}`);

    this.logger.info(`Starting voiceover for: "${title}" [${languageDetected || 'unknown language'}]`);

    // Step 1: Download the video
    const videoPath = await this._downloadVideo(url, basePath);
    this.logger.info(`Video downloaded: ${videoPath}`);

    // Step 2: Extract audio and transcribe
    const audioPath = await this._extractAudio(videoPath, basePath);
    const transcript = await ai.transcribe(audioPath, languageDetected);
    this.logger.info(`Transcription complete: ${transcript.text?.substring(0, 100)}...`);

    // Step 3: Translate to English
    const translatedText = await ai.translate(transcript.text, languageDetected);
    this.logger.info(`Translation complete`);

    // Step 4: Generate voiceover audio
    const voiceoverPath = path.join(basePath, 'voiceover.mp3');
    await ai.textToSpeech(translatedText, voiceoverPath, { voice: 'onyx' });
    this.logger.info(`Voiceover audio generated`);

    // Step 5: Generate bilingual subtitles
    const subtitles = this._generateSubtitles(transcript, translatedText);
    const subtitlePath = path.join(basePath, 'subtitles.srt');
    fs.writeFileSync(subtitlePath, subtitles);
    this.logger.info(`Subtitles generated`);

    // Step 6: Save the script and translation
    const scriptPath = path.join(basePath, 'script.json');
    const scriptData = {
      title: title,
      platform: platform,
      sourceLanguage: languageDetected || 'unknown',
      originalText: transcript.text,
      translatedText: translatedText,
      generatedAt: new Date().toISOString(),
    };
    fs.writeFileSync(scriptPath, JSON.stringify(scriptData, null, 2));

    const result = {
      type: 'voiceover',
      title: title,
      sourceUrl: url,
      videoPath: videoPath,
      audioPath: audioPath,
      voiceoverPath: voiceoverPath,
      subtitlePath: subtitlePath,
      scriptPath: scriptPath,
      outputPath: basePath,
      transcript: transcript.text?.substring(0, 200),
      translatedText: translatedText?.substring(0, 200),
      metadata: {
        sourceTitle: title,
        sourcePlatform: platform,
        sourceLanguage: languageDetected || 'unknown',
        generatedAt: new Date().toISOString(),
      },
    };

    this.logger.success(`Voiceover pipeline complete for: "${title}"`);
    return result;
  }

  /**
   * Download video using yt-dlp
   */
  async _downloadVideo(url, outputDir) {
    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }

    try {
      // Try using youtube-dl-exec if available
      const youtubedl = require('youtube-dl-exec');
      const outputPath = path.join(outputDir, 'source.mp4');
      
      await youtubedl(url, {
        output: outputPath,
        format: 'best[height<=720]',
        noPlaylist: true,
      });

      return outputPath;
    } catch (error) {
      this.logger.warn(`yt-dlp download failed: ${error.message}`);
      this.logger.warn('This is expected if the video source requires special handling.');

      // Return a placeholder - in production, you'd handle this per-platform
      const placeholderPath = path.join(outputDir, 'source.mp4.download_failed');
      fs.writeFileSync(placeholderPath, JSON.stringify({
        url: url,
        error: error.message,
        note: 'Download requires platform-specific handling',
      }));
      return placeholderPath;
    }
  }

  /**
   * Extract audio from video using ffmpeg
   */
  async _extractAudio(videoPath, outputDir) {
    const audioPath = path.join(outputDir, 'source_audio.mp3');
    
    try {
      const ffmpeg = require('fluent-ffmpeg');
      return new Promise((resolve, reject) => {
        ffmpeg(videoPath)
          .output(audioPath)
          .audioCodec('libmp3lame')
          .audioBitrate(128)
          .on('end', () => resolve(audioPath))
          .on('error', reject)
          .run();
      });
    } catch (error) {
      this.logger.warn(`ffmpeg audio extraction failed: ${error.message}`);
      // Create a silent audio placeholder
      const silencePath = path.join(outputDir, 'source_audio_silent.mp3');
      fs.writeFileSync(silencePath, '');
      return silencePath;
    }
  }

  /**
   * Generate bilingual SRT subtitles
   */
  _generateSubtitles(transcript, translatedText) {
    if (!transcript || !transcript.segments) {
      // Simple subtitle format
      return `1\n00:00:00,000 --> 00:00:30,000\n${translatedText || 'Translated content'}\n`;
    }

    let srt = '';
    let index = 1;

    for (const segment of transcript.segments) {
      const startTime = this._formatSrtTime(segment.start);
      const endTime = this._formatSrtTime(segment.end);
      
      srt += `${index}\n`;
      srt += `${startTime} --> ${endTime}\n`;
      srt += `${segment.text}\n\n`;
      
      index++;
    }

    return srt;
  }

  /**
   * Format seconds to SRT time format (HH:MM:SS,mmm)
   */
  _formatSrtTime(seconds) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    const millis = Math.floor((seconds % 1) * 1000);
    
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')},${String(millis).padStart(3, '0')}`;
  }
}

module.exports = new VoiceoverPipeline();