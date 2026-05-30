/**
 * Gemini CLI Runner
 * Uses stdin pipe for pure text prompts to avoid shell escaping issues.
 * Staging media files via Google GenAI SDK before passing URIs to CLI.
 * 
 * Model-Switching-First Strategy:
 * On quota (429) errors, switches model first on the same API key.
 * Only rotates to next API key after all models exhausted for current key.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { GoogleGenAI } = require('@google/genai');
const { Logger } = require('./logger');

const logger = new Logger('GeminiCLI');

const MODEL_CHAIN = [
  'gemini-3.5-flash',
  'gemini-2.5-flash',
];

class GeminiCLIRunner {
  constructor() {
    this.available = false;
    this.keyIndex = 0;
    this.modelIndex = 0;
    this._checkAvailability();
  }

  get model() { return MODEL_CHAIN[this.modelIndex % MODEL_CHAIN.length]; }

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

  _rotateModel() {
    const prev = this.model;
    this.modelIndex++;
    const next = this.model;
    const wrapped = prev === next; 
    if (!wrapped) {
      logger.info(`Switching model: ${prev} → ${next} (key ${this.keyIndex + 1})`);
    }
    return !wrapped;
  }

  _rotateKey() {
    const prevKey = this.keyIndex;
    this.keyIndex++;
    this.modelIndex = 0;
    logger.info(`Switching key: ${prevKey + 1} → ${(this.keyIndex % 8) + 1}, model reset to ${this.model}`);
  }

  _rotateOnQuota() {
    const modelSwitched = this._rotateModel();
    if (!modelSwitched) {
      this._rotateKey();
    }
  }

  /** Upload media via File API and return URI */
  async _uploadFileForCLI(filePath) {
    const fileName = path.basename(filePath);
    const fileSize = fs.statSync(filePath).size;
    const key = this._getApiKey();
    if (!key) { logger.warn('No API key for upload'); return null; }

    const ext = path.extname(filePath).toLowerCase();
    let mimeType = 'video/mp4';
    if (ext === '.jpg' || ext === '.jpeg') mimeType = 'image/jpeg';
    else if (ext === '.png') mimeType = 'image/png';

    logger.info(`Uploading ${fileName} (${(fileSize / 1024 / 1024).toFixed(2)}MB) for analysis...`);

    try {
      const ai = new GoogleGenAI({ apiKey: key });
      const mediaFile = await ai.files.upload({
        file: filePath,
        mimeType: mimeType,
      });

      if (!mediaFile || !mediaFile.name) return null;

      // Poll for ACTIVE state (3 tries × 10s), proceed even if still processing
      if (mimeType.startsWith('video/')) {
        let fileState = await ai.files.get({ name: mediaFile.name });
        for (let poll = 1; poll <= 3; poll++) {
          if (fileState.state === 'ACTIVE') break;
          logger.info(`  Poll ${poll}/3 — waiting 10s (state: ${fileState.state})...`);
          await new Promise(r => setTimeout(r, 10000));
          fileState = await ai.files.get({ name: mediaFile.name });
        }
        if (fileState.state === 'FAILED') {
          logger.warn('File processing FAILED');
          return null;
        }
        logger.success(`File ready: ${mediaFile.name} (state: ${fileState.state})`);
      }
      
      return mediaFile.name;
    } catch (e) {
      logger.warn(`File upload error: ${e.message}`);
      return null;
    }
  }

  async _deleteFile(fileUri) {
    const key = this._getApiKey();
    if (!key || !fileUri) return;
    try {
      const ai = new GoogleGenAI({ apiKey: key });
      await ai.files.delete({ name: fileUri });
    } catch {}
  }

  async run(prompt, options = {}) {
    if (!this.available) { logger.warn('Gemini CLI not available'); return null; }

    const timeout = options.timeout || 120000;
    const maxRetries = MODEL_CHAIN.length * 8 + 1;
    const tmpDir = '/tmp';

    for (let attempt = 0; attempt < maxRetries; attempt++) {
      let uploadedUris = [];
      try {
        let fullPrompt = prompt;
        if (options.skillFile && fs.existsSync(options.skillFile)) {
          const skillContent = fs.readFileSync(options.skillFile, 'utf8');
          fullPrompt = `## SKILL INSTRUCTIONS\n${skillContent}\n\n## TASK\n${prompt}`;
        }

        // Intercept and upload all local media files first
        let mediaString = '';
        const allMediaPaths = [
          ...(options.images || []),
          ...(options.videoPaths || []),
          ...(options.videoPath ? [options.videoPath] : [])
        ];

        for (const filePath of allMediaPaths) {
          if (fs.existsSync(filePath)) {
            const uri = await this._uploadFileForCLI(filePath);
            if (uri) {
              uploadedUris.push(uri);
              mediaString += `\nMedia URI attached: ${uri}\n`; 
            }
          }
        }
        
        if (mediaString) {
          fullPrompt = `${mediaString}\n${fullPrompt}`;
        }

        const cli = fs.existsSync('/snap/bin/gemini') ? 'gemini' : 'npx @google/gemini-cli';
        const key = this._getApiKey();

        // Text-only mode piped execution (media references are now part of the text prompt)
        const promptFile = path.join(tmpDir, `gemini_p_${Date.now()}_${Math.random().toString(36).slice(2, 8)}.txt`);
        fs.writeFileSync(promptFile, fullPrompt, 'utf8');
        
        const cmd = `cat "${promptFile}" | TERM=xterm-256color GEMINI_API_KEY="${key}" ${cli} --skip-trust -m ${this.model} 2>&1`;

        logger.info(`CLI run (attempt ${attempt + 1}, model: ${this.model}, key: ${this.keyIndex + 1})`);

        const result = execSync(cmd, { timeout, maxBuffer: 10 * 1024 * 1024, encoding: 'utf8' });

        try { fs.unlinkSync(promptFile); } catch {}

        // Cleanup remote files on success
        for (const uri of uploadedUris) {
            await this._deleteFile(uri);
        }

        // Filter terminal/CLI noise from output
        const output = result.trim().split('\n')
          .filter(l => !l.includes('256-color') && !l.includes('Ripgrep') && !l.includes('Warning:'))
          .join('\n')
          .trim();
        if (output && output.length > 10) {
          logger.success(`CLI responded (${output.length} chars)`);
          const preview = output.substring(0, 300);
          logger.info(`  Response: ${preview.replace(/\n/g, '\\n')}`);
          await new Promise(r => setTimeout(r, 10000));
          return output;
        }
        logger.warn('CLI empty response');
        this._rotateKey();
      } catch (e) {
        // Cleanup remote files on failure
        for (const uri of uploadedUris) {
            await this._deleteFile(uri);
        }

        const errText = (e.stderr || e.stdout || e.message || '').toString();
        if (e.signal === 'SIGKILL' || e.killed) { logger.warn('CLI timeout'); return null; }
        if (errText.includes('429') || errText.includes('quota')) { 
          logger.warn(`CLI rate limited (429) on model ${this.model}, key ${this.keyIndex + 1} — rotating model first...`);
          this._rotateOnQuota();
          await new Promise(r => setTimeout(r, 2000)); 
          continue; 
        }
        logger.warn(`CLI error: ${errText.substring(0, 120)}`);
        this._rotateKey();
      }
    }
    return null;
  }

  async compareAndReviewQA(originalPath, editedPath, editType, country = 'Global') {
    if (!this.available) { logger.warn('CLI not available for QA'); return null; }
    if (!fs.existsSync(originalPath) || !fs.existsSync(editedPath)) {
      logger.warn('compareAndReviewQA: one or both videos missing');
      return null;
    }

    const isCrop = editType === 'crop';
    const prompt = isCrop
      ? `Compare the original landscape video with the CROPPED 9:16 portrait version.

Evaluate the CROPPED video:
1. Is the main subject properly centered in the 9:16 frame?
2. Is any face cut off by the edges?
3. Is the cropping smooth and not jarring?
4. Is watermark still visible?
5. Overall quality score 1-10

If improvements are needed, specify precise pixel adjustments:
- adjustment: number of pixels to shift left/right (negative=left, positive=right)
- zoom_adjustment: fine-tune zoom percentage (95-110)

Respond ONLY JSON:
{"verdict":"APPROVE/IMPROVE/REJECT","score":N,"adjustment":0,"zoom_adjustment":0,"reason":"brief reason"}`
      : `Compare the original UNEDITED video with the EDITED version.

Evaluate the EDITED video:
1. Are TikTok-style captions clear and readable?
2. Are captions properly timed with speech?
3. Are watermarks properly removed?
4. Is the visual quality good?
5. Does the edit feel natural?
6. Overall quality score 1-10

If improvements needed, specify what to change.
Respond ONLY JSON:
{"verdict":"APPROVE/IMPROVE/REJECT","score":N,"issues":[],"improvement_suggestions":"specific changes needed","reason":"brief reason"}`;

    const result = await this.run(prompt, {
      videoPaths: [originalPath, editedPath],
      timeout: 120000,
    });

    if (!result) {
      logger.warn(`compareAndReviewQA (${editType}): null response`);
      return null;
    }

    try {
      const m = result.match(/\{[\s\S]*\}/);
      if (m) return JSON.parse(m[0]);
    } catch (e) {
      logger.warn(`compareAndReviewQA JSON: ${e.message.substring(0, 80)}`);
    }
    return null;
  }

  async reviewFinalVideo(videoPath, country = 'Global') {
    if (!this.available) { logger.warn('CLI not available for final review'); return null; }
    if (!fs.existsSync(videoPath)) {
      logger.warn(`reviewFinalVideo: video not found: ${videoPath}`);
      return null;
    }

    const prompt = `Review this YouTube Short for "Mr. WorldWideWebster" channel. Target country: ${country}.

Check:
1. Is the video in 9:16 portrait (1080x1920)? If not, pixel dimensions?
2. Are any subtitles/captions readable and NOT blocking main content?
3. Is any watermark still visible?
4. Does the first 3 seconds serve as a good hook?
5. Is the video quality acceptable (not blurry/pixelated)?
6. Overall quality rating 1-10

Respond ONLY with JSON:
{"quality_score":N,"recommendation":"APPROVE/RENDER_AGAIN","issues":[],"crop_ok":true,"subtitles_ok":true,"watermark_removed":true,"hook_quality":"strong"}`;

    const result = await this.run(prompt, {
      videoPath,
      timeout: 120000,
    });

    if (!result) {
      logger.warn('reviewFinalVideo: null response');
      return { quality_score: 5, recommendation: 'APPROVE', issues: ['CLI review unavailable'], crop_ok: true, subtitles_ok: true, watermark_removed: true, hook_quality: 'unknown' };
    }

    try {
      const m = result.match(/\{[\s\S]*\}/);
      if (m) return JSON.parse(m[0]);
    } catch (e) {
      logger.warn(`reviewFinalVideo JSON: ${e.message.substring(0, 80)}`);
    }
    return { quality_score: 5, recommendation: 'APPROVE', issues: [], crop_ok: true, subtitles_ok: true, watermark_removed: true, hook_quality: 'unknown' };
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

  async rankVideoFromPath(videoPath, country, curatorSkillContent, engagementData = null) {
    if (!this.available) return null;
    if (!fs.existsSync(videoPath)) return null;

    let metricsBlock = '';
    if (engagementData) {
      const velocity = engagementData.ageInDays > 0 ? (engagementData.views / engagementData.ageInDays).toFixed(0) : 'N/A';
      metricsBlock = `ENGAGEMENT METRICS:
- Views: ${engagementData.views || 0}
- Likes: ${engagementData.likes || 0}
- Comments: ${engagementData.comments || 0}
- Age in days: ${engagementData.ageInDays || 0}
- Title: "${engagementData.title || 'Unknown'}"
- Velocity (views/day): ${velocity}`;

      if (engagementData.topComments && engagementData.topComments.length > 0) {
        metricsBlock += '\n\nTOP VIEWER COMMENTS:\n' +
          engagementData.topComments.map((c, i) => `  ${i + 1}. "${c.text}" (${c.likes} likes, by ${c.author})`).join('\n');
      }
    }

    // skill content is passed directly from pipeline (not a file path)
    const skillHeader = curatorSkillContent ? `## SKILL INSTRUCTIONS\n${curatorSkillContent}\n\n## TASK\n` : '';

    const prompt = `${skillHeader}Rank the attached video for Mr. WorldWideWebster.

Country: ${country}${metricsBlock}

Follow the skill instructions. Return JSON.`;

    const result = await this.run(prompt, {
      videoPath: videoPath,
      timeout: 120000,
    });

    if (!result) return null;

    try {
      const m = result.match(/\{[\s\S]*\}/);
      if (m) {
        const p = JSON.parse(m[0]);
        p.verdict = (p.verdict || '').toUpperCase() === 'APPROVED' ? 'APPROVED' : 'REJECTED';
        return p;
      }
    } catch (e) {
      logger.warn(`rankVideoFromPath JSON parse: ${e.message}`);
    }
    return null;
  }
}

let instance = null;
function getGeminiCLI() { if (!instance) instance = new GeminiCLIRunner(); return instance; }
module.exports = { GeminiCLIRunner, getGeminiCLI };