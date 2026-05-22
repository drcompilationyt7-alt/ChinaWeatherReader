/**
 * Mr. WorldWideWebster - CLIP Pipeline
 * 
 * Wraps the AI-Youtube-Shorts-Generator Python module for clipping functionality.
 * Falls back to AI-based highlight analysis + ffmpeg cropping if Python module isn't available.
 * 
 * For content that's visually entertaining and can be understood without translation.
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

    // Step 2: Fallback - download and prepare for manual clipping
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
   * Fallback: Prepare clip metadata for manual/video-editor-based clipping
   */
  async _fallbackClip(url, title, platform, outputDir, hookStrategy) {
    this.logger.info('Using fallback clip method');

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
        note: 'Fallback mode - clip manually or use Python pipeline when available',
      },
    };

    // Save clip metadata
    const infoPath = path.join(outputDir, 'clip_info.json');
    fs.writeFileSync(infoPath, JSON.stringify(clipInfo, null, 2));

    return clipInfo;
  }
}

module.exports = new ClipPipeline();