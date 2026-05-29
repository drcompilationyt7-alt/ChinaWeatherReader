/**
 * Gemini CLI Runner
 * 
 * Uses stdin pipe for prompts (fixes `-p "..."` shell escaping issues).
 * The new Gemini CLI v0.44 does NOT support `-p` with positional image args.
 * 
 * Fix: cat prompt_file | GEMINI_API_KEY="x" gemini image1 image2
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
      const result = execSync('npx @google/gemini-cli --version 2>&1', { timeout: 15000, encoding: 'utf8' }).trim();
      if (result) {
        this.available = true;
        logger.info(`Gemini CLI available: ${result}`);
        return;
      }
    } catch {}
    try {
      const result = execSync('gemini --version 2>&1', { timeout: 10000, encoding: 'utf8' }).trim();
      if (result) {
        this.available = true;
        logger.info(`Gemini CLI available (global): ${result}`);
        return;
      }
    } catch {}
    this.available = false;
    logger.warn('Gemini CLI not available');
  }

  isAvailable() { return this.available; }

  _getApiKey() {
    for (let i = 1; i <= 8; i++) {
      const k = process.env[`GEMINI_API_KEY${i === 1 ? '' : `_${i}`}`];
      if (k && (i - 1 === this.keyIndex % 8)) return k;
    }
    return process.env.GEMINI_API_KEY || null;
  }

  _rotateKey() { this.keyIndex++; }

  async run(prompt, options = {}) {
    if (!this.available) { logger.warn('Gemini CLI not available'); return null; }

    const timeout = options.timeout || 120000;
    const maxRetries = 3;
    const tmpDir = '/tmp';

    for (let attempt = 0; attempt < maxRetries; attempt++) {
      try {
        let fullPrompt = prompt;
        if (options.skillFile && fs.existsSync(options.skillFile)) {
          const skillContent = fs.readFileSync(options.skillFile, 'utf8');
          fullPrompt = `## SKILL INSTRUCTIONS\n${skillContent}\n\n## TASK\n${prompt}`;
        }

        // Write prompt to temp file (avoids shell escaping issues with -p)
        // For video analysis, prepend @video.mp4 to the prompt text
        let promptContent = fullPrompt;
        if (options.videoPath && fs.existsSync(options.videoPath)) {
          promptContent = `@${options.videoPath} ${fullPrompt}`;
        }

        const promptFile = path.join(tmpDir, `gemini_p_${Date.now()}_${Math.random().toString(36).slice(2, 8)}.txt`);
        fs.writeFileSync(promptFile, promptContent, 'utf8');

        const cli = fs.existsSync('/snap/bin/gemini') ? 'gemini' : 'npx @google/gemini-cli';
        const key = this._getApiKey();
        let cmd;

        if (options.images && options.images.length > 0) {
          const existingImages = options.images.filter(f => fs.existsSync(f));
          if (existingImages.length > 0) {
            const imageArgs = existingImages.map(f => `"${f}"`).join(' ');
            cmd = `cat "${promptFile}" | GEMINI_API_KEY="${key}" ${cli} -m gemini-2.5-flash ${imageArgs} 2>&1`;
          } else {
            cmd = `cat "${promptFile}" | GEMINI_API_KEY="${key}" ${cli} -m gemini-2.5-flash 2>&1`;
          }
        } else {
          cmd = `cat "${promptFile}" | GEMINI_API_KEY="${key}" ${cli} -m gemini-2.5-flash 2>&1`;
        }

        logger.info(`CLI run (attempt ${attempt + 1})`);

        const result = execSync(cmd, { timeout, maxBuffer: 10 * 1024 * 1024, encoding: 'utf8' });

        // Cleanup temp file
        try { fs.unlinkSync(promptFile); } catch {}

        const output = result.trim();
        if (output && output.length > 10) {
          logger.success(`CLI responded (${output.length} chars)`);
          return output;
        }
        logger.warn('CLI empty response');
        this._rotateKey();
      } catch (e) {
        const errText = (e.stderr || e.stdout || e.message || '').toString();
        if (e.signal === 'SIGKILL' || e.killed) { logger.warn('CLI timeout'); return null; }
        if (errText.includes('429') || errText.includes('quota')) { this._rotateKey(); await new Promise(r => setTimeout(r, 2000)); continue; }
        logger.warn(`CLI error: ${errText.substring(0, 120)}`);
        this._rotateKey();
      }
    }
    return null;
  }

  async evaluateCropFromVideo(videoPath, country, skillFilePath) {
    const prompt = `Evaluate this video for a YouTube Short from ${country}. The video needs to be cropped from landscape to 9:16 portrait (1080x1920). Locate the primary subject and return the calculated center percentage.`;
    return this.run(prompt, {
      videoPath,
      skillFile: skillFilePath || path.join(__dirname, '..', 'skills', 'type1', 'smart-crop-skill.md'),
      timeout: 60000,
    });
  }

  async evaluateCropQuality(croppedVideoPath, skillFilePath) {
    const prompt = `Analyze this 9:16 vertical cropped video. Is the main subject properly centered and fully visible? Are any faces cut off by the edges? Return PASS or REJECT with pixel adjustment.`;
    return this.run(prompt, {
      videoPath: croppedVideoPath,
      skillFile: skillFilePath || path.join(__dirname, '..', 'skills', 'type1', 'crop-evaluator-skill.md'),
      timeout: 60000,
    });
  }

  async evaluateCrop(rawFrames, croppedFrames, question) {
    const images = [...rawFrames, ...croppedFrames];
    const prompt = `${question}\n\nRespond JSON: {"verdict":"GOOD/BAD","suggested_adjustment":{"direction":"left/right","pixels":N}}`;
    return this.run(prompt, { images, skillFile: path.join(__dirname, '..', 'skills', 'type1', 'viral-clip-curator.md'), timeout: 60000 });
  }

  async evaluateEdit(frames, transcriptInfo) {
    const prompt = `Analyze for TikTok edits.\n${transcriptInfo}\n\nRespond JSON: {"has_watermarks":bool,"needs_captions":bool,"caption_type":"","suggested_hook_text":""}`;
    return this.run(prompt, { images: frames, skillFile: path.join(__dirname, '..', 'skills', 'type1', 'viral-reposter-editor.md'), timeout: 60000 });
  }

  async qualityReview(frames) {
    return this.run('QA review this Short. Score 1-10. JSON: {"quality_score":N,"recommendation":"APPROVE/RENDER_AGAIN"}', { images: frames, timeout: 60000 });
  }
}

let instance = null;
function getGeminiCLI() { if (!instance) instance = new GeminiCLIRunner(); return instance; }
module.exports = { GeminiCLIRunner, getGeminiCLI };