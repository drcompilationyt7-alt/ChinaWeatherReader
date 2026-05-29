/**
 * Gemini CLI Runner
 * 
 * Wraps the `gemini` CLI tool for visual/command tasks.
 * Used for: crop feedback loops, edit feedback loops, frame analysis.
 * 
 * Fixes:
 * - Uses temp file for prompt to avoid shell escaping issues (JSON { } chars)
 * - Only passes image paths as arguments
 * - Handles API key from environment (Gemini CLI reads GEMINI_API_KEY natively)
 */
const { execSync } = require('child_process');
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
      const result = execSync('npx @google/gemini-cli --version 2>&1 || gemini --version 2>&1', { timeout: 15000, encoding: 'utf8' }).trim();
      this.available = true;
      logger.info(`Gemini CLI available: ${result}`);
    } catch {
      this.available = false;
      logger.warn('Gemini CLI not available');
    }
  }

  isAvailable() {
    return this.available;
  }

  /**
   * Run Gemini CLI with a prompt and optional image files.
   * Uses temp file for prompt to fix shell escaping of { } characters.
   */
  async run(prompt, options = {}) {
    if (!this.available) {
      logger.warn('Gemini CLI not available — skipping');
      return null;
    }

    const timeout = options.timeout || 120000;
    const maxRetries = 3;
    const tmpDir = options.tmpDir || '/tmp';

    for (let attempt = 0; attempt < maxRetries; attempt++) {
      try {
        // Build the full prompt text
        let fullPrompt = prompt;

        // Load skill file if provided
        if (options.skillFile && fs.existsSync(options.skillFile)) {
          const skillContent = fs.readFileSync(options.skillFile, 'utf8');
          fullPrompt = `## SKILL INSTRUCTIONS\n${skillContent}\n\n## TASK\n${prompt}`;
        }

        // Write prompt to a temp file (fix for shell escaping JSON)
        const promptFile = path.join(tmpDir, `gemini_prompt_${Date.now()}_${Math.random().toString(36).slice(2, 8)}.txt`);
        fs.writeFileSync(promptFile, fullPrompt, 'utf8');

        // Build command with images
        let imageArgs = '';
        if (options.images && options.images.length > 0) {
          const existingImages = options.images.filter(f => fs.existsSync(f));
          if (existingImages.length > 0) {
            imageArgs = existingImages.map(f => `"${f}"`).join(' ');
          }
        }

        // Use npx if available, otherwise use global gemini
        const cli = fs.existsSync('/usr/local/bin/gemini') || fs.existsSync('/home/runner/.gemini/bin/gemini')
          ? 'gemini'
          : 'npx @google/gemini-cli';

        // Read prompt from file with --prompt-file or pipe the file content via -p
        // The safest approach: pass prompt via file using shell redirection
        const cmd = imageArgs
          ? `${cli} -p "$(cat '${promptFile}')" ${imageArgs} 2>&1`
          : `${cli} -p "$(cat '${promptFile}')" 2>&1`;

        logger.info(`Running Gemini CLI (attempt ${attempt + 1}, images: ${options.images?.length || 0})`);

        const result = execSync(cmd, {
          timeout,
          maxBuffer: 10 * 1024 * 1024,
          encoding: 'utf8',
          killSignal: 'SIGKILL',
        });

        // Cleanup temp file
        try { fs.unlinkSync(promptFile); } catch {}

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
          return null;
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
   * Rank a video using Gemini CLI with local file (downloads + uploads to File API)
   * This is the REAL "Gemini watches the video" approach via CLI.
   */
  async rankVideo(videoPath, country, skillFile, tmpDir) {
    if (!this.available || !fs.existsSync(videoPath)) return null;

    const prompt = `WATCH THIS VIDEO and rank it for reposting on YouTube Shorts channel "Mr. WorldWideWebster".
Target country: ${country}

Evaluate: 3-second hook, visual quality, watermark, country match, language independence.
Score 1-10. Return JSON: {"score": N, "hook_score": N, "verdict": "APPROVED/REJECTED", "reasoning": "..."}`;

    return await this.run(prompt, {
      images: [videoPath], // Pass video file as argument
      skillFile,
      tmpDir,
      timeout: 180000, // 3 min for video analysis
    });
  }

  async evaluateCrop(rawFrames, croppedFrames, question) {
    const images = [...rawFrames, ...croppedFrames];
    const prompt = `${question}\n\nRespond in JSON: {"verdict":"GOOD/BAD","issues":[],"suggested_adjustment":{"direction":"left/right/none","pixels":N,"reason":""}}`;
    const skillPath = path.join(__dirname, '..', 'skills', 'type1', 'viral-clip-curator.md');
    return await this.run(prompt, { images, skillFile: fs.existsSync(skillPath) ? skillPath : null, timeout: 60000 });
  }

  async evaluateEdit(frames, transcriptInfo) {
    const prompt = `Analyze for TikTok edits.\n${transcriptInfo || ''}\n\nRespond JSON: {"has_watermarks":bool,"needs_captions":bool,"caption_type":"","suggested_hook_text":""}`;
    const skillPath = path.join(__dirname, '..', 'skills', 'type1', 'viral-reposter-editor.md');
    return await this.run(prompt, { images: frames, skillFile: fs.existsSync(skillPath) ? skillPath : null, timeout: 60000 });
  }

  async qualityReview(frames) {
    const prompt = `QA review this Short. Score 1-10. JSON: {"quality_score":N,"crop_ok":bool,"subtitles_ok":bool,"recommendation":"APPROVE/RENDER_AGAIN"}`;
    return await this.run(prompt, { images: frames, timeout: 60000 });
  }
}

let instance = null;
function getGeminiCLI() { if (!instance) instance = new GeminiCLIRunner(); return instance; }
module.exports = { GeminiCLIRunner, getGeminiCLI };