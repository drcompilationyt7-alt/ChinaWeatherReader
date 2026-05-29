/**
 * Gemini CLI Runner
 * 
 * Wraps the `gemini` CLI tool for visual/command tasks.
 * Used for: crop feedback loops, edit feedback loops, frame analysis.
 * 
 * The Gemini CLI can:
 * - Analyze images (frames)
 * - Generate ffmpeg commands
 * - Execute shell commands directly
 * - Provide feedback on visual output
 * 
 * This runs via execSync in GitHub Actions where the gemini CLI is installed.
 */
const { execSync, exec } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('GeminiCLI');

class GeminiCLIRunner {
  constructor() {
    this.available = false;
    this.keyIndex = 0;
    this._checkAvailability();
  }

  _checkAvailability() {
    try {
      const result = execSync('gemini --version 2>&1', { timeout: 10000, encoding: 'utf8' }).trim();
      this.available = true;
      logger.info(`Gemini CLI available: ${result}`);
    } catch {
      this.available = false;
      logger.warn('Gemini CLI not available — install from https://github.com/google-gemini/gemini-cli');
    }
  }

  isAvailable() {
    return this.available;
  }

  /**
   * Get API key for CLI calls (rotates through 8 keys)
   */
  _getApiKey() {
    for (let i = 1; i <= 8; i++) {
      const suffix = i === 1 ? '' : `_${i}`;
      if (i - 1 === this.keyIndex % 8) {
        return process.env[`GEMINI_API_KEY${suffix}`] || null;
      }
    }
    return process.env.GEMINI_API_KEY || null;
  }

  _rotateKey() {
    this.keyIndex++;
  }

  /**
   * Run Gemini CLI with a prompt and optional image files
   * @param {string} prompt - The prompt to send
   * @param {Object} options - { images: [], timeout: 120000, skillFile: null }
   * @returns {string|null} - Gemini's response text
   */
  async run(prompt, options = {}) {
    if (!this.available) {
      logger.warn('Gemini CLI not available — skipping');
      return null;
    }

    const timeout = options.timeout || 120000;
    const maxRetries = 3;

    for (let attempt = 0; attempt < maxRetries; attempt++) {
      const apiKey = this._getApiKey();
      if (!apiKey) {
        logger.warn('No Gemini API key available for CLI');
        return null;
      }

      try {
        // Build the full prompt
        let fullPrompt = prompt;

        // Load skill file if provided
        if (options.skillFile && fs.existsSync(options.skillFile)) {
          const skillContent = fs.readFileSync(options.skillFile, 'utf8');
          fullPrompt = `## SKILL INSTRUCTIONS\n${skillContent}\n\n## TASK\n${prompt}`;
        }

        // Escape the prompt for shell
        const escapedPrompt = fullPrompt.replace(/"/g, '\\"').replace(/\n/g, '\\n');

        // Build command with images
        let imageArgs = '';
        if (options.images && options.images.length > 0) {
          const existingImages = options.images.filter(f => fs.existsSync(f));
          if (existingImages.length > 0) {
            imageArgs = existingImages.map(f => `"${f}"`).join(' ');
          }
        }

        const cmd = imageArgs
          ? `GEMINI_API_KEY="${apiKey}" gemini -p "${escapedPrompt}" ${imageArgs} 2>&1`
          : `GEMINI_API_KEY="${apiKey}" gemini -p "${escapedPrompt}" 2>&1`;

        logger.info(`Running Gemini CLI (attempt ${attempt + 1}, images: ${options.images?.length || 0})`);
        
        const result = execSync(cmd, {
          timeout,
          maxBuffer: 10 * 1024 * 1024,
          encoding: 'utf8',
          killSignal: 'SIGKILL',
        });

        const output = result.trim();
        if (output && output.length > 10) {
          logger.success(`Gemini CLI responded (${output.length} chars)`);
          return output;
        }
        
        logger.warn('Gemini CLI returned empty response');
        this._rotateKey();
      } catch (e) {
        const errText = (e.stderr || e.stdout || e.message || '').toString();
        
        if (e.signal === 'SIGKILL' || e.killed) {
          logger.warn(`Gemini CLI timed out after ${timeout}ms`);
          return null; // Don't retry on timeout
        }
        
        if (errText.includes('429') || errText.includes('quota') || errText.includes('RESOURCE_EXHAUSTED')) {
          logger.warn(`Gemini CLI rate limited — rotating key`);
          this._rotateKey();
          await new Promise(r => setTimeout(r, 2000));
          continue;
        }
        
        logger.warn(`Gemini CLI error: ${errText.substring(0, 120)}`);
        this._rotateKey();
      }
    }

    logger.error('All Gemini CLI attempts failed');
    return null;
  }

  /**
   * Analyze frames for crop quality
   * Sends raw and cropped frames to Gemini for comparison
   */
  async evaluateCrop(rawFrames, croppedFrames, question) {
    const images = [...rawFrames, ...croppedFrames];
    const frameDescription = rawFrames.map((f, i) => 
      `Frame ${i + 1} (raw): ${path.basename(f)}\nFrame ${i + 1} (cropped): ${croppedFrames[i] ? path.basename(croppedFrames[i]) : 'missing'}`
    ).join('\n');

    const prompt = `${question}

Frame pairs (raw vs cropped):
${frameDescription}

Analyze each frame pair. Is the crop:
1. Keeping the main subject/action in frame?
2. Not cutting off important content?
3. Not creating black bars or distortion?
4. Properly centered on the action?

Respond in JSON:
{"verdict": "GOOD" or "BAD", "issues": ["issue1", "issue2"], "suggested_adjustment": {"direction": "left/right/none", "pixels": N, "reason": "why"}, "confidence": 1-10}`;

    const skillPath = path.join(__dirname, '..', 'skills', 'viral-clip-curator.md');
    return await this.run(prompt, { 
      images, 
      skillFile: fs.existsSync(skillPath) ? skillPath : null,
      timeout: 60000 
    });
  }

  /**
   * Generate and execute an ffmpeg crop command
   * Gemini analyzes the video and produces the exact ffmpeg command
   */
  async generateCropCommand(videoPath, outputPath, sourceWidth, sourceHeight) {
    const prompt = `I need to convert a video to YouTube Shorts format (1080x1920, 9:16 portrait).

Source video: ${videoPath}
Source dimensions: ${sourceWidth}x${sourceHeight}
Output: ${outputPath}

Generate the EXACT ffmpeg command to:
1. Crop the video to 9:16 aspect ratio (1080x1920)
2. Keep the most interesting/important part of the frame (center on people/action)
3. Scale properly without stretching or squeezing
4. Use high quality encoding (libx264, CRF 18-20)
5. Preserve audio

IMPORTANT: The output MUST be exactly 1080x1920. No black bars. No padding.

Return ONLY the ffmpeg command, nothing else. The command should start with "ffmpeg"`;

    const result = await this.run(prompt, { timeout: 30000 });
    return result;
  }

  /**
   * Analyze editing needs for a video
   * Sends frames to determine what TikTok-style edits are needed
   */
  async evaluateEdit(frames, transcriptInfo) {
    const prompt = `Analyze these video frames to determine what TikTok-style edits are needed.

Transcript info: ${transcriptInfo || 'No transcript available'}

For each frame, check:
1. Are there watermarks visible? (TikTok, Douyin, platform logos)
2. Is there text that needs captions/translation?
3. What's the overall energy/vibe?
4. Are there any visual hooks in the first 3 seconds?

Respond in JSON:
{
  "has_watermarks": true/false,
  "watermark_positions": [{"x": N, "y": N, "type": "tiktok/douyin/other"}],
  "needs_captions": true/false,
  "caption_type": "none/tiktok_style/translation",
  "suggested_hook_text": "text for first 3 seconds",
  "edit_energy": "high/medium/low",
  "visual_transform": {"zoom": 105, "mirror": false}
}`;

    const skillPath = path.join(__dirname, '..', 'skills', 'viral-reposter-editor.md');
    return await this.run(prompt, {
      images: frames,
      skillFile: fs.existsSync(skillPath) ? skillPath : null,
      timeout: 60000,
    });
  }

  /**
   * Generate ffmpeg edit command based on analysis
   */
  async generateEditCommand(videoPath, outputPath, editPlan) {
    const prompt = `Generate an ffmpeg command to apply these edits to a YouTube Short.

Input: ${videoPath}
Output: ${outputPath}

Edit plan: ${JSON.stringify(editPlan)}

The command should:
1. Apply zoom (105-110%) to crop out watermarks if present
2. Add slight contrast/saturation bump for uniqueness
3. Apply TikTok-style ASS subtitles if captions needed
4. Handle audio properly
5. Use libx264, CRF 20, preset fast

Return ONLY the ffmpeg command.`;

    return await this.run(prompt, { timeout: 30000 });
  }

  /**
   * QA review: Check if the final output looks good
   * Sends representative frames for final quality check
   */
  async qualityReview(frames) {
    const prompt = `QA Review this YouTube Short (represented by these frames extracted at different points).

Check:
1. Is the video properly cropped to 9:16 (portrait)? Any black bars?
2. Are subtitles/captions readable and not blocking important content?
3. Are captions too big or too small?
4. Is the watermark (if any) successfully removed/cropped?
5. Does the first frame look like a good hook?
6. Overall quality rating: 1-10

Respond in JSON:
{
  "quality_score": 1-10,
  "crop_ok": true/false,
  "subtitles_ok": true/false,
  "watermark_removed": true/false,
  "hook_quality": "strong/medium/weak",
  "issues": ["issue1", "issue2"],
  "recommendation": "APPROVE/RENDER_AGAIN"
}`;

    return await this.run(prompt, {
      images: frames,
      timeout: 60000,
    });
  }
}

// Singleton
let instance = null;

function getGeminiCLI() {
  if (!instance) {
    instance = new GeminiCLIRunner();
  }
  return instance;
}

module.exports = { GeminiCLIRunner, getGeminiCLI };