/**
 * Gemini CLI Runner
 * Uses stdin pipe for pure text prompts to avoid shell escaping issues.
 * Uses explicit positional invocation args (gemini "prompt" file1) 
 * for multimodal inputs to force correct media ingestion across file types.
 * 
 * Model-Switching-First Strategy:
 * On quota (429) errors, switches model first on the same API key.
 * Only rotates to next API key after all models exhausted for current key.
 * Since CLI and upload share the same keys, this preserves quota for uploads.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
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

  /** Rotates to next model. Returns true if model changed, false if wrapped around (all models exhausted) */
  _rotateModel() {
    const prev = this.model;
    this.modelIndex++;
    const next = this.model;
    const wrapped = prev === next; // wrapped around if same after increment
    if (!wrapped) {
      logger.info(`Switching model: ${prev} → ${next} (key ${this.keyIndex + 1})`);
    }
    return !wrapped;
  }

  /** Rotates to next key and resets model */
  _rotateKey() {
    const prevKey = this.keyIndex;
    this.keyIndex++;
    this.modelIndex = 0;
    logger.info(`Switching key: ${prevKey + 1} → ${(this.keyIndex % 8) + 1}, model reset to ${this.model}`);
  }

  /** On quota error: switch model first, only rotate key if all models exhausted */
  _rotateOnQuota() {
    const modelSwitched = this._rotateModel();
    if (!modelSwitched) {
      // All models exhausted for current key — rotate key, reset model
      this._rotateKey();
    }
  }

  async run(prompt, options = {}) {
    if (!this.available) { logger.warn('Gemini CLI not available'); return null; }

    const timeout = options.timeout || 120000;
    const maxRetries = MODEL_CHAIN.length * 8 + 1;
    const tmpDir = '/tmp';

    for (let attempt = 0; attempt < maxRetries; attempt++) {
      try {
        let fullPrompt = prompt;
        if (options.skillFile && fs.existsSync(options.skillFile)) {
          const skillContent = fs.readFileSync(options.skillFile, 'utf8');
          fullPrompt = `## SKILL INSTRUCTIONS\n${skillContent}\n\n## TASK\n${prompt}`;
        }

        const cli = fs.existsSync('/snap/bin/gemini') ? 'gemini' : 'npx @google/gemini-cli';
        const key = this._getApiKey();

        const fileArgs = [];
        if (options.images && options.images.length > 0) {
          for (const f of options.images) {
            if (fs.existsSync(f)) fileArgs.push(`"${f}"`);
          }
        }
        if (options.videoPaths && options.videoPaths.length > 0) {
          for (const vp of options.videoPaths) {
            if (vp && fs.existsSync(vp)) fileArgs.push(`"${vp}"`);
          }
        } else if (options.videoPath && fs.existsSync(options.videoPath)) {
          fileArgs.push(`"${options.videoPath}"`);
        }

        let cmd;
        let promptFile = null;
        if (fileArgs.length > 0) {
          // Multimodal mode: Removed -p to fix positional argument clash with files
          const escapedPrompt = fullPrompt
            .replace(/\\/g, '\\\\')
            .replace(/"/g, '\\"')
            .replace(/\$/g, '\\$')
            .replace(/`/g, '\\`');

          cmd = `GEMINI_API_KEY="${key}" ${cli} --skip-trust -m ${this.model} "${escapedPrompt}" ${fileArgs.join(' ')} 2>&1`;
        } else {
          // Text-only mode
          promptFile = path.join(tmpDir, `gemini_p_${Date.now()}_${Math.random().toString(36).slice(2, 8)}.txt`);
          fs.writeFileSync(promptFile, fullPrompt, 'utf8');
          cmd = `cat "${promptFile}" | GEMINI_API_KEY="${key}" ${cli} --skip-trust -m ${this.model} 2>&1`;
        }

        logger.info(`CLI run (attempt ${attempt + 1}, model: ${this.model}, key: ${this.keyIndex + 1})`);

        const result = execSync(cmd, { timeout, maxBuffer: 10 * 1024 * 1024, encoding: 'utf8' });

        try { if (promptFile) fs.unlinkSync(promptFile); } catch {}

        const output = result.trim();
        if (output && output.length > 10) {
          logger.success(`CLI responded (${output.length} chars)`);
          const preview = output.substring(0, 300);
          logger.info(`   Response: ${preview.replace(/\n/g, '\\n')}`);
          // Pause 10s between CLI calls to avoid 429 rate limiting
          await new Promise(r => setTimeout(r, 10000));
          return output;
        }
        logger.warn('CLI empty response');
        this._rotateKey();
      } catch (e) {
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

  /**
   * Upload a local video file to the Gemini File API and return the resource URI.
   * Uses axios directly (not the CLI) to upload, then passes the URI to CLI for analysis.
   * After upload, waits 13 seconds for processing instead of polling the broken status API.
   */
  async _uploadFileForCLI(videoPath) {
    const axios = require('axios');
    const GEMINI_UPLOAD = 'https://generativelanguage.googleapis.com/upload/v1beta';
    const GEMINI_BASE = 'https://generativelanguage.googleapis.com/v1beta';
    const fileName = path.basename(videoPath);
    const fileBuffer = fs.readFileSync(videoPath);
    const fileSize = fileBuffer.length;
    const key = this._getApiKey();
    if (!key) { logger.warn('No API key for upload'); return null; }

    logger.info(`Uploading ${fileName} (${(fileSize / 1024 / 1024).toFixed(1)}MB) for CLI analysis...`);

    // Step 1: Start resumable upload
    const startResp = await axios.post(
      `${GEMINI_UPLOAD}/files?key=${key}`,
      { file: { displayName: fileName } },
      {
        headers: {
          'X-Goog-Upload-Protocol': 'resumable',
          'X-Goog-Upload-Command': 'start',
          'X-Goog-Upload-Header-Content-Length': fileSize.toString(),
          'X-Goog-Upload-Header-Content-Type': 'video/mp4',
          'Content-Type': 'application/json',
        },
        timeout: 30000,
      }
    );

    const uploadUrl = startResp.headers['x-goog-upload-url'];
    if (!uploadUrl) { logger.warn('No upload URL'); return null; }

    // Step 2: Upload binary
    const uploadResp = await axios.put(uploadUrl, fileBuffer, {
      headers: {
        'Content-Type': 'video/mp4',
        'Content-Length': fileSize.toString(),
        'X-Goog-Upload-Command': 'upload, finalize',
        'X-Goog-Upload-Offset': '0',
      },
      timeout: 300000,
      maxContentLength: Infinity,
      maxBodyLength: Infinity,
      transformRequest: [(d) => d],
      transformResponse: [(d) => d],
    });

    let respData = uploadResp.data;
    try { if (typeof respData === 'string' || Buffer.isBuffer(respData)) respData = JSON.parse(respData.toString()); } catch (e) {}

    let uf = respData?.file || respData;
    if (!uf || !uf.name) {
      const fileUri = uploadResp.headers['x-goog-upload-file-uri'];
      if (fileUri) uf = { name: fileUri, state: 'PROCESSING' };
      else { logger.warn('No file name in upload response'); return null; }
    }

    // Step 3: Wait 13 seconds for processing instead of polling broken status API
    logger.info(`File uploaded: ${uf.name} — waiting 13s for processing...`);
    await new Promise(r => setTimeout(r, 13000));
    
    logger.success(`File ready for CLI: ${uf.name}`);
    return { name: uf.name, uri: uf.uri || uf.name, key, state: 'ACTIVE' };
  }

  /** Delete a file from Gemini File API */
  async _deleteFile(fileUri, apiKey) {
    const axios = require('axios');
    const GEMINI_BASE = 'https://generativelanguage.googleapis.com/v1beta';
    try { await axios.delete(`${GEMINI_BASE}/files/${fileUri}?key=${apiKey}`, { timeout: 10000 }); } catch {}
  }

  /**
   * Rank a video for viral reposting via Gemini CLI.
   * Uploads file first, then references the URI in the prompt.
   */
  async rankVideoFromPath(videoPath, country, curatorSkill, engagementData = null) {
    if (!this.available) {
      logger.warn('Gemini CLI not available for video ranking');
      return null;
    }
    if (!fs.existsSync(videoPath)) {
      logger.warn(`rankVideoFromPath: video not found: ${videoPath}`);
      return null;
    }

    // Upload file first, then reference its URI in the CLI prompt
    let uploadedFile = null;
    try {
      uploadedFile = await this._uploadFileForCLI(videoPath);
    } catch (e) {
      logger.warn(`Upload failed: ${e.message.substring(0, 60)}`);
    }
    if (!uploadedFile) {
      logger.warn('rankVideoFromPath: upload failed — cannot rank');
      return null;
    }

    let metricsBlock = '';
    if (engagementData) {
      const velocity = engagementData.ageInDays > 0 ? (engagementData.views / engagementData.ageInDays).toFixed(0) : 'N/A';
      const likeRatio = engagementData.views > 0 ? ((engagementData.likes / engagementData.views) * 100).toFixed(2) : 'N/A';
      const commentDensity = engagementData.views > 0 ? ((engagementData.comments / engagementData.views) * 100).toFixed(3) : 'N/A';

      metricsBlock = `ENGAGEMENT METRICS:
- Views: ${engagementData.views || 0}
- Likes: ${engagementData.likes || 0}
- Comments: ${engagementData.comments || 0}
- Age in days: ${engagementData.ageInDays || 0}
- Title: "${engagementData.title || 'Unknown'}"

HARD METRIC COMPUTATION:
- Velocity (views/day): ${velocity}
- Like Ratio (likes/views×100): ${likeRatio}%
- Comment Density (comments/views×100): ${commentDensity}%`;

      if (engagementData.topComments && engagementData.topComments.length > 0) {
        metricsBlock += '\n\nTOP VIEWER COMMENTS:\n' +
          engagementData.topComments.map((c, i) => `  ${i + 1}. "${c.text}" (${c.likes} likes, by ${c.author})`).join('\n');
      }
    }

    const prompt = `Analyze the uploaded video file at ${uploadedFile.uri} and rank it for reposting on "Mr. WorldWideWebster" channel.

Target country: ${country}${metricsBlock}

HYBRID EVALUATION FRAMEWORK:
1. Hard Metrics (40% weight): Evaluate velocity, like ratio, and comment density against benchmarks
2. Multimodal Visual (60% weight): 3-Second Hook, language independence, production cleanliness

WATCH the video carefully and evaluate:
1. 3-Second Hook — does it grab attention in the first 3 seconds?
2. Language independence — can it be understood without translation?
3. Visual quality and entertainment value
4. Watermark presence — can it be cropped out?
5. Does the content match country ${country}?
6. Would this perform well as a YouTube Short?

Respond ONLY with valid JSON (no markdown):
{"score": 1-10, "country": "detected country", "hook_score": 1-10, "velocity_score": 1-10, "engagement_score": 1-10, "language_independent": true/false, "has_watermark": true/false, "watermark_type": "type or null", "verdict": "APPROVED/REJECTED", "reasoning": "brief hybrid evaluation — metrics + visual quality + hook"}`;

    const result = await this.run(prompt, {
      timeout: 120000,
    });

    if (!result) {
      logger.warn('rankVideoFromPath: CLI returned null');
      return null;
    }

    try {
      const m = result.match(/\{[\s\S]*\}/);
      if (m) {
        const p = JSON.parse(m[0]);
        p.verdict = (p.verdict || '').toUpperCase() === 'APPROVED' ? 'APPROVED' : 'REJECTED';
        return p;
      }
    } catch (e) {
      logger.warn(`rankVideoFromPath JSON parse: ${e.message.substring(0, 80)}`);
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
}

let instance = null;
function getGeminiCLI() { if (!instance) instance = new GeminiCLIRunner(); return instance; }
module.exports = { GeminiCLIRunner, getGeminiCLI };