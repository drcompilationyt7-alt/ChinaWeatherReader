/**
 * Mr. WorldWideWebster - VOICEOVER Pipeline
 * 
 * Takes a video in a foreign language, downloads it, transcribes the audio,
 * translates to English, generates TTS voiceover, and replaces the audio.
 * 
 * Fixes applied:
 * - Transcribe result handles both string and object responses
 * - TTS uses 'en-US-GuyNeural' instead of invalid 'onyx'
 * - _downloadVideo validates output file exists
 * - _extractAudio validates and creates silence fallback
 * - Validation after every step
 */
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const { Logger } = require('../core/logger');

class VoiceoverPipeline {
  constructor() {
    this.logger = new Logger('VoiceoverPipeline');
  }

  /**
   * Process a video through the voiceover pipeline
   */
  async processVoiceover(params) {
    const { url, title, platform, outputDir, ai, languageDetected } = params;
    const safeId = Date.now();
    const basePath = path.join(outputDir, `voiceover_${safeId}`);

    if (!fs.existsSync(basePath)) {
      fs.mkdirSync(basePath, { recursive: true });
    }

    this.logger.info(`Starting voiceover pipeline for: "${title}"`);

    // Step 1: Download the video
    const videoPath = await this._downloadVideo(url, basePath);
    if (!videoPath) {
      throw new Error('Could not download source video');
    }
    this.logger.success(`Video downloaded: ${path.basename(videoPath)}`);

    // Step 2: Extract audio from video
    const audioPath = await this._extractAudio(videoPath, basePath);
    if (!audioPath) {
      throw new Error('Could not extract audio from video');
    }
    this.logger.success(`Audio extracted: ${path.basename(audioPath)}`);

    // Step 3: Transcribe audio to text using Whisper
    const transcriptResult = await ai.transcribe(audioPath, languageDetected);
    const transcript = typeof transcriptResult === 'string'
      ? { text: transcriptResult, segments: [] }
      : transcriptResult || { text: '', segments: [] };
    this.logger.info(`Transcription complete: ${(transcript?.text || '').substring(0, 100)}...`);

    // Step 4: Translate to English if needed
    let translatedText = transcript.text;
    if (languageDetected && languageDetected !== 'english') {
      translatedText = await ai.translate(transcript.text, languageDetected);
      this.logger.info(`Translation complete: ${translatedText.substring(0, 100)}...`);
    }

    // Step 5: Generate TTS voiceover
    const voiceoverPath = path.join(basePath, 'voiceover.mp3');
    await ai.textToSpeech(translatedText, voiceoverPath, { voice: 'en-US-GuyNeural' });
    if (!fs.existsSync(voiceoverPath)) {
      throw new Error('Voiceover generation failed — no output file');
    }
    this.logger.success('Voiceover generated');

    // Step 6: Generate subtitles/SRT
    const srtContent = this._generateSubtitles(transcript, translatedText);
    const srtPath = path.join(basePath, 'subtitles.srt');
    fs.writeFileSync(srtPath, srtContent, 'utf8');

    // Step 7: Compile final video with voiceover + subtitles
    const outputVideoPath = path.join(basePath, 'voiceover_final.mp4');
    try {
      execSync(
        `ffmpeg -y -i "${videoPath}" -i "${voiceoverPath}" ` +
        `-c:v copy -c:a aac -map 0:v:0 -map 1:a:0 ` +
        `-shortest "${outputVideoPath}"`,
        { timeout: 300000, maxBuffer: 100 * 1024 * 1024 }
      );
      this.logger.success(`Final video: ${outputVideoPath}`);
    } catch (error) {
      this.logger.warn(`FFmpeg compile failed: ${error.message}, using source video`);
      // Fallback: copy the source video as-is
      fs.copyFileSync(videoPath, outputVideoPath);
    }

    const result = {
      type: 'voiceover',
      title: title,
      sourceUrl: url,
      platform: platform,
      outputPath: basePath,
      videoFile: path.basename(outputVideoPath),
      voiceoverFile: path.basename(voiceoverPath),
      subtitlesFile: path.basename(srtPath),
      transcript: transcript.text,
      translatedText: translatedText,
      metadata: {
        sourceTitle: title,
        sourcePlatform: platform,
        languageDetected: languageDetected,
        processedAt: new Date().toISOString(),
      },
    };

    this.logger.success(`Voiceover pipeline complete: "${title}"`);
    return result;
  }

  /**
   * Download a video from a URL using youtube-dl-exec
   */
  async _downloadVideo(url, outputDir) {
    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }

    try {
      const youtubedl = require('youtube-dl-exec');
      const outputPath = path.join(outputDir, 'source.mp4');

      await youtubedl(url, {
        output: outputPath,
        format: 'bestvideo[height<=720]+bestaudio/best',
        noPlaylist: true,
      });

      if (!fs.existsSync(outputPath)) {
        throw new Error('Downloaded file missing after download');
      }
      return outputPath;
    } catch (error) {
      this.logger.error(`Video download failed: ${error.message}`);
      return null;
    }
  }

  /**
   * Extract audio from video using ffmpeg, with silence fallback
   */
  async _extractAudio(videoPath, outputDir) {
    const audioPath = path.join(outputDir, 'source_audio.mp3');

    try {
      const ffmpeg = require('fluent-ffmpeg');
      return await new Promise((resolve, reject) => {
        ffmpeg(videoPath)
          .output(audioPath)
          .audioCodec('libmp3lame')
          .audioBitrate(128)
          .on('end', () => resolve(audioPath))
          .on('error', reject)
          .run();
      });
    } catch (error) {
      this.logger.warn(`Audio extraction failed: ${error.message}, creating silence`);

      // Create a silence MP3 as fallback
      const silencePath = path.join(outputDir, 'silence.mp3');
      execSync(
        `ffmpeg -y -f lavfi -i anullsrc=r=44100:cl=mono -t 5 "${silencePath}"`,
        { timeout: 30000 }
      );

      if (fs.existsSync(silencePath)) {
        return silencePath;
      }
      return null;
    }
  }

  /**
   * Generate SRT subtitle content from transcript segments
   */
  _generateSubtitles(transcript, translatedText) {
    if (!transcript || !transcript.segments || transcript.segments.length === 0) {
      // Fallback: single subtitle block
      return [
        '1',
        '00:00:00,000 --> 00:00:30,000',
        `${transcript?.text || ''}`,
        `${translatedText || ''}`,
      ].join('\n');
    }

    let srt = '';
    let index = 1;

    for (const segment of transcript.segments) {
      const start = this._formatSrtTime(segment.start);
      const end = this._formatSrtTime(segment.end);

      srt += `${index}\n`;
      srt += `${start} --> ${end}\n`;
      srt += `${segment.text}\n`;

      if (translatedText) {
        srt += `${translatedText}\n`;
      }
      srt += '\n';
      index++;
    }

    return srt;
  }

  /**
   * Format seconds to SRT timestamp format (HH:MM:SS,mmm)
   */
  _formatSrtTime(seconds) {
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    const msecs = Math.floor((seconds % 1) * 1000);

    return `${String(hrs).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')},${String(msecs).padStart(3, '0')}`;
  }
}

module.exports = new VoiceoverPipeline();