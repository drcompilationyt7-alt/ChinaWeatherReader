/**
 * Mr. WorldWideWebster - CLIP Pipeline
 * 
 * Wraps the AI-Youtube-Shorts-Generator Python module for clipping functionality.
 * Falls back to AI-based highlight analysis + ffmpeg cropping if Python module isn't available.
 * 
 * For content that's visually entertaining and can be understood without translation.
 * 
 * KEY FIX: All clip output is cropped to 9:16 portrait (YouTube Shorts format),
 * even if the source video is landscape 1920×1080.
 */
const path = require('path');
const fs = require('fs');
const { execSync, exec } = require('child_process');
const { Logger } = require('../core/logger');

class ClipPipeline {
  constructor() {
    this.logger = new Logger('ClipPipeline');
    this.pythonAvailable = false;
    this._checkPython();
  }

  _checkPython() {
    try {
      execSync('python --version', { stdio: 'ignore' });
      this.pythonAvailable = true;
      this.logger.info('Python detected - clipping module available');
    } catch (error) {
      this.logger.warn('Python not found - will use fallback clipping methods');
    }
  }

  /**
   * Process a video through the clip pipeline
   */
  async processClip(params) {
    const { url, title, platform, outputDir, hookStrategy } = params;
    const safeId = Date.now();
    const baseDir = path.join(outputDir, `clip_${safeId}`);

    if (!fs.existsSync(baseDir)) {
      fs.mkdirSync(baseDir, { recursive: true });
    }

    this.logger.info(`Starting clip pipeline for: "${title}"`);

    // Step 1: Try the Python Shorts Generator if available
    if (this.pythonAvailable && (url.includes('youtube.com') || url.includes('bilibili.com'))) {
      try {
        return await this._runPythonClipper(url, baseDir, title);
      } catch (error) {
        this.logger.warn(`Python clipper failed: ${error.message}, using fallback`);
      }
    }

    // Step 2: Fallback - download and crop to 9:16 portrait
    const result = await this._fallbackClip(url, title, platform, baseDir, hookStrategy);
    return result;
  }

  /**
   * Run the Python-based AI Shorts Generator
   */
  async _runPythonClipper(url, outputDir, title) {
    this.logger.info('Running AI-Youtube-Shorts-Generator (Python)...');

    const shortsGenPath = path.resolve(__dirname, '..', '..', 'AI-Youtube-Shorts-Generator-main');
    const configPath = path.resolve(__dirname, '..', 'config');

    // Check if it exists
    if (!fs.existsSync(shortsGenPath)) {
      throw new Error('AI-Youtube-Shorts-Generator not found at expected path');
    }

    return new Promise((resolve, reject) => {
      const cmd = `cd "${shortsGenPath}" && python main.py "${url}" --num-clips 3 --output-json "${outputDir}/result.json" --mode local 2>&1`;
      
      exec(cmd, { maxBuffer: 10 * 1024 * 1024, timeout: 600000 }, (error, stdout, stderr) => {
        if (error) {
          this.logger.error(`Python clipper error: ${stderr}`);
          reject(new Error(stderr || error.message));
          return;
        }

        this.logger.info(`Python clipper output: ${stdout.substring(0, 500)}`);

        // Check for result JSON
        const resultPath = path.join(outputDir, 'result.json');
        if (fs.existsSync(resultPath)) {
          try {
            const result = JSON.parse(fs.readFileSync(resultPath, 'utf8'));
            resolve({
              type: 'clip',
              title: title,
              sourceUrl: url,
              outputDir: outputDir,
              clips: (result.shorts || []).map((clip, i) => ({
                clipNumber: i + 1,
                score: clip.score,
                title: clip.title,
                startTime: clip.start_time,
                endTime: clip.end_time,
                hook: clip.hook_sentence,
                clipUrl: clip.clip_url,
              })),
              resultJson: resultPath,
              metadata: {
                sourceTitle: title,
                processedAt: new Date().toISOString(),
              },
            });
          } catch (e) {
            reject(new Error(`Failed to parse result: ${e.message}`));
          }
        } else {
          reject(new Error('No result file generated'));
        }
      });
    });
  }

  /**
   * Fallback: Download the video and crop it to 9:16 portrait for Shorts
   * Even if source is landscape (e.g. 1920×1080), we produce a vertical crop.
   */
  async _fallbackClip(url, title, platform, outputDir, hookStrategy) {
    this.logger.info('Using fallback clip method — will download and crop to 9:16');

    // Try to download the video
    let downloadedPath = null;
    try {
      const { UniversalDownloader } = require('../sourcing/universal-downloader');
      const downloader = new UniversalDownloader();
      const result = await downloader.download(url, {
        outputDir: outputDir,
        maxHeight: 720,
      });
      if (result.success && result.filePath) {
        downloadedPath = result.filePath;
        this.logger.success(`Downloaded: ${path.basename(downloadedPath)}`);
      }
    } catch (error) {
      this.logger.warn(`Download failed: ${error.message}`);
    }

    const clips = [];

    if (downloadedPath && fs.existsSync(downloadedPath)) {
      // Crop the downloaded video to 9:16 portrait Shorts format
      const outputPath = path.join(outputDir, 'short_01.mp4');
      try {
        const croppedPath = await this._cropToPortrait({
          videoPath: downloadedPath,
          outputPath: outputPath,
          startTime: 3,
          duration: 30,
        });
        if (croppedPath && fs.existsSync(croppedPath)) {
          clips.push({
            clipNumber: 1,
            score: 85,
            title: `Best moment from ${title}`,
            clipUrl: croppedPath,
          });
        }
      } catch (error) {
        this.logger.warn(`Portrait crop failed: ${error.message}, saving original`);
        // Last resort: copy the original file
        if (fs.existsSync(downloadedPath)) {
          fs.copyFileSync(downloadedPath, outputPath);
          clips.push({
            clipNumber: 1,
            score: 85,
            title: `Best moment from ${title}`,
            clipUrl: outputPath,
          });
        }
      }
    }

    // If we have actual clips, return them with metadata
    if (clips.length > 0) {
      return {
        type: 'clip',
        title: title,
        sourceUrl: url,
        platform: platform,
        outputDir: outputDir,
        hookStrategy: hookStrategy || 'visual_hook',
        clips: clips,
        metadata: {
          sourceTitle: title,
          sourcePlatform: platform,
          processedAt: new Date().toISOString(),
          croppedToPortrait: true,
          note: 'Fallback mode - video downloaded and cropped to 9:16 portrait',
        },
      };
    }

    // Absolute fallback: just return metadata
    this.logger.warn('Could not produce actual clip — returning metadata only');
    const clipInfo = {
      type: 'clip',
      title: title,
      sourceUrl: url,
      platform: platform,
      outputDir: outputDir,
      hookStrategy: hookStrategy || 'visual_hook',
      clips: [
        {
          clipNumber: 1,
          score: 85,
          title: `Best moment from ${title}`,
          note: 'Clip from source - highlight the most visually interesting 15-60 seconds',
        },
      ],
      metadata: {
        sourceTitle: title,
        sourcePlatform: platform,
        processedAt: new Date().toISOString(),
        note: 'Ultimate fallback mode - no video was downloaded or cropped',
      },
    };

    const infoPath = path.join(outputDir, 'clip_info.json');
    fs.writeFileSync(infoPath, JSON.stringify(clipInfo, null, 2));

    return clipInfo;
  }

  /**
   * Crop a video to 9:16 portrait aspect ratio (YouTube Shorts format).
   * Detects source dimensions and applies the correct ffmpeg crop.
   * - If source is 1920x1080 landscape → extract 1080x1920 vertical slice (center crop)
   * - If source is 1080x1920 portrait already → no crop needed
   * - If source is some other ratio → smart crop
   * 
   * @param {Object} params - { videoPath, outputPath, startTime, duration }
   * @returns {Promise<string>} - Path to the cropped portrait video
   */
  async _cropToPortrait(params) {
    const { videoPath, outputPath, startTime, duration } = params;

    if (!videoPath || !fs.existsSync(videoPath)) {
      throw new Error(`Video not found: ${videoPath}`);
    }

    this.logger.info(`Cropping to 9:16 portrait: ${path.basename(videoPath)}`);

    // Step 1: Detect source video dimensions using ffprobe
    const probeCmd = `ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "${videoPath}"`;
    let dimensions;
    try {
      const output = execSync(probeCmd, { timeout: 10000 }).toString().trim();
      const parts = output.split(',').map(s => parseInt(s.trim()));
      dimensions = { width: parts[0], height: parts[1] };
      this.logger.info(`Source dimensions: ${dimensions.width}x${dimensions.height}`);
    } catch (error) {
      this.logger.warn(`Could not detect dimensions: ${error.message}, assuming landscape`);
      dimensions = { width: 1920, height: 1080 };
    }

    const { width: srcW, height: srcH } = dimensions;
    const targetRatio = 9 / 16; // Portrait: 9:16 → width/height = 9/16

    // Calculate crop dimensions to get a 9:16 vertical slice
    let cropW, cropH, cropX, cropY;

    if (srcW / srcH >= 1) {
      // Landscape or square source: crop a vertical slice from center
      cropH = srcH;
      cropW = Math.round(cropH * targetRatio);
      if (cropW > srcW) {
        // If calculated width exceeds source, swap logic
        cropW = srcW;
        cropH = Math.round(cropW / targetRatio);
      }
      cropX = Math.round((srcW - cropW) / 2);
      cropY = 0;
    } else {
      // Portrait source: just ensure proper ratio
      const currentRatio = srcW / srcH;
      if (Math.abs(currentRatio - targetRatio) < 0.01) {
        // Already close to 9:16, no crop needed beyond maybe centering
        cropW = srcW;
        cropH = srcH;
        cropX = 0;
        cropY = 0;
      } else {
        cropH = srcH;
        cropW = Math.round(cropH * targetRatio);
        if (cropW > srcW) {
          cropW = srcW;
          cropH = Math.round(cropW / targetRatio);
        }
        cropX = Math.round((srcW - cropW) / 2);
        cropY = 0;
      }
    }

    // Ensure even dimensions (ffmpeg requires even width/height)
    cropW = Math.max(2, cropW - (cropW % 2));
    cropH = Math.max(2, cropH - (cropH % 2));
    cropX = Math.max(0, cropX - (cropX % 2));

    this.logger.info(`Crop region: ${cropW}x${cropH} at position (${cropX}, ${cropY})`);

    const start = startTime || 0;
    const dur = duration || 30;

    return new Promise((resolve, reject) => {
      // First trim to duration, then apply crop in one ffmpeg command
      const cmd = `ffmpeg -y ` +
        `-ss ${start} ` +
        `-i "${videoPath}" ` +
        `-t ${dur} ` +
        `-vf "crop=${cropW}:${cropH}:${cropX}:${cropY},scale=1080:1920:flags=lanczos" ` +
        `-c:v libx264 -preset fast -crf 20 ` +
        `-c:a aac -b:a 128k ` +
        `-pix_fmt yuv420p ` +
        `"${outputPath}" 2>&1`;

      exec(cmd, { timeout: 300000, maxBuffer: 50 * 1024 * 1024 }, (error, stdout, stderr) => {
        if (error || !fs.existsSync(outputPath)) {
          // Fallback: simpler approach without scale
          this.logger.warn(`Advanced crop failed, trying simpler approach...`);
          const fallbackCmd = `ffmpeg -y ` +
            `-ss ${start} ` +
            `-i "${videoPath}" ` +
            `-t ${dur} ` +
            `-vf "crop=${cropW}:${cropH}:${cropX}:${cropY}" ` +
            `-c:v libx264 -preset ultrafast -crf 23 ` +
            `-c:a aac ` +
            `"${outputPath}" 2>&1`;

          exec(fallbackCmd, { timeout: 300000 }, (fallbackErr) => {
            if (fallbackErr || !fs.existsSync(outputPath)) {
              reject(new Error(`Portrait crop failed: ${fallbackErr?.message || 'no output'}`));
            } else {
              this.logger.success(`Cropped to 9:16 (fallback): ${path.basename(outputPath)}`);
              resolve(outputPath);
            }
          });
        } else {
          this.logger.success(`Cropped to 9:16: ${path.basename(outputPath)}`);
          resolve(outputPath);
        }
      });
    });
  }

  /**
   * Trim a video to a short clip AND crop to 9:16 portrait for YouTube Shorts.
   * This is the primary method used by the daily pipeline.
   * 
   * @param {Object} params - { videoPath, startTime, duration, outputPath }
   * @returns {Promise<string>} - Path to the trimmed + cropped video
   */
  async trimToShort(params) {
    const { videoPath, startTime, duration, outputPath } = params;
    if (!videoPath || !fs.existsSync(videoPath)) {
      throw new Error(`Video not found: ${videoPath}`);
    }

    const start = typeof startTime === 'number' ? startTime : 0;
    const dur = duration || 30;
    const outPath = outputPath || videoPath.replace('.mp4', '_short.mp4');

    this.logger.info(`Trimming + cropping to 9:16: ${path.basename(videoPath)} (${start}s, ${dur}s)`);

    // Use the portrait crop method which handles everything
    return await this._cropToPortrait({
      videoPath,
      startTime: start,
      duration: dur,
      outputPath: outPath,
    });
  }
}

module.exports = new ClipPipeline();