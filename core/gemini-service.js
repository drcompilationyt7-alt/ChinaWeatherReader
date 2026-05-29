/**
 * Gemini Service — Free AI Brain for Mr. WorldWideWebster
 * Uses Google AI Studio (free tier) with 8-key rotation.
 * Free tier: 15 RPM, 1M tokens/day per key, 8 keys = 120 RPM
 * 
 * Supports:
 * - YouTube URL video analysis (file_data.file_uri)
 * - Video File API upload for fallback
 */
const axios = require('axios');
const { Logger } = require('./logger');
const path = require('path');
const fs = require('fs');

const logger = new Logger('GeminiService');
const GEMINI_BASE = 'https://generativelanguage.googleapis.com/v1beta';
const GEMINI_UPLOAD = 'https://generativelanguage.googleapis.com/upload/v1beta';
const MIN_DELAY = 3000;

class GeminiService {
  constructor() {
    this.keys = [];
    this.currentKeyIndex = 0;
    this.model = 'gemini-3.5-flash';
    this.requestCount = 0;
    this.lastResetTime = Date.now();
    this._loadKeys();
  }

  _loadKeys() {
    for (let i = 1; i <= 8; i++) {
      const key = process.env[`GEMINI_API_KEY${i === 1 ? '' : `_${i}`}`];
      if (key) this.keys.push(key);
    }
    if (this.keys.length === 0 && process.env.GEMINI_API_KEY) this.keys.push(process.env.GEMINI_API_KEY);
    logger.info(`Loaded ${this.keys.length} Gemini API keys (model: ${this.model})`);
  }

  _getKey() { return this.keys.length ? this.keys[this.currentKeyIndex % this.keys.length] : null; }

  _rotateKey() {
    if (this.keys.length <= 1) return;
    this.currentKeyIndex = (this.currentKeyIndex + 1) % this.keys.length;
  }

  async _callAPI(contents, options = {}) {
    const maxRetries = this.keys.length + 1;
    let lastError = null;

    for (let attempt = 0; attempt < maxRetries; attempt++) {
      const key = this._getKey();
      if (!key) { logger.warn('No API keys available'); return null; }

      try {
        const body = {
          contents,
          generationConfig: {
            temperature: options.temperature || 0.7,
            maxOutputTokens: options.maxTokens || 2048,
            topP: options.topP || 0.9,
          },
        };
        if (options.systemInstruction) {
          body.systemInstruction = { parts: [{ text: options.systemInstruction }] };
        }

        const url = `${GEMINI_BASE}/models/${this.model}:generateContent?key=${key}`;
        const resp = await axios.post(url, body, {
          headers: { 'Content-Type': 'application/json' },
          timeout: options.timeout || 120000,
        });

        this.requestCount++;
        const candidates = resp.data?.candidates;

        if (!candidates || candidates.length === 0) {
          const blockReason = resp.data?.promptFeedback?.blockReason;
          logger.warn(`Gemini empty candidates (block: ${blockReason || 'unknown'}, key: ${this.currentKeyIndex + 1})`);
          this._rotateKey();
          await new Promise(r => setTimeout(r, MIN_DELAY * (attempt + 1)));
          continue;
        }

        const parts = candidates[0].content?.parts;
        if (parts && parts.length > 0) return parts.map(p => p.text || '').join('');
        return null;
      } catch (error) {
        lastError = error;
        const status = error.response?.status;
        const errText = error.response?.data?.error?.message || error.message;
        const respBody = JSON.stringify(error.response?.data || {}).substring(0, 200);

        if (status === 400) {
          const field = error.response?.data?.error?.details?.[0]?.fieldViolations?.[0]?.field || 'unknown';
          logger.warn(`Gemini 400 error (${field}): ${respBody}`);
        }

        if (status === 429 || status === 403 || errText?.includes('quota') || errText?.includes('RESOURCE_EXHAUSTED')) {
          logger.warn(`Key ${this.currentKeyIndex + 1} rate limited — rotating, waiting ${MIN_DELAY * (attempt + 1)}ms`);
          this._rotateKey();
          await new Promise(r => setTimeout(r, MIN_DELAY * (attempt + 1)));
          continue;
        }
        if (errText?.includes('high demand') || errText?.includes('temporarily') || errText?.includes('spikes')) {
          logger.warn(`Key ${this.currentKeyIndex + 1} transient error — rotating`);
          this._rotateKey();
          await new Promise(r => setTimeout(r, MIN_DELAY * (attempt + 1)));
          continue;
        }
        logger.error(`Gemini API error (${status}): ${errText?.substring(0, 150)}`);
        return null;
      }
    }
    logger.error(`All keys exhausted. Last: ${lastError?.message?.substring(0, 80)}`);
    return null;
  }

  async chat(systemPrompt, userMessage, opts = {}) {
    return this._callAPI([{ role: 'user', parts: [{ text: userMessage }] }], { ...opts, systemInstruction: systemPrompt });
  }

  async chatJSON(systemPrompt, userMessage, opts = {}) {
    const r = await this.chat(systemPrompt + '\n\nIMPORTANT: Respond ONLY with valid JSON.', userMessage, opts);
    if (!r) return null;
    try {
      const m = r.match(/\{[\s\S]*\}/);
      if (m) return JSON.parse(m[0]);
      const a = r.match(/\[[\s\S]*\]/);
      if (a) return JSON.parse(a[0]);
    } catch (e) { logger.warn(`JSON parse: ${e.message.substring(0, 60)}`); }
    return null;
  }

  /**
   * PRIMARY: Rank a YouTube video by URL.
   * Gemini watches the video natively via file_data (no download needed).
   * Uses snake_case for REST API compatibility.
   */
  async rankVideo(url, country, curatorSkill) {
    const prompt = `WATCH this YouTube video and rank it for reposting on "Mr. WorldWideWebster" channel.

Target country: ${country}

Carefully evaluate the actual video content:
1. 3-Second Hook — does it grab attention in the first 3 seconds?
2. Language independence — can it be understood without translation?
3. Visual quality and entertainment value
4. Watermark presence — can it be cropped out?
5. Does the content match country ${country}?
6. Would this perform well as a YouTube Short?

Respond ONLY with valid JSON (no markdown):
{"score": 1-10, "country": "detected country", "hook_score": 1-10, "language_independent": true/false, "has_watermark": true/false, "watermark_type": "type or null", "verdict": "APPROVED/REJECTED", "reasoning": "brief explanation of visual quality and hook"}`;

    // REST API uses snake_case: file_data.file_uri
    const contents = [{
      role: 'user',
      parts: [
        { file_data: { file_uri: url } },
        { text: prompt }
      ]
    }];

    const response = await this._callAPI(contents, {
      systemInstruction: curatorSkill,
      temperature: 0.3,
      maxTokens: 1024,
      timeout: 120000,
    });

    if (!response) {
      logger.warn(`rankVideo: null for "${url.substring(0, 50)}"`);
      return null;
    }

    try {
      const m = response.match(/\{[\s\S]*\}/);
      if (m) {
        const p = JSON.parse(m[0]);
        p.verdict = (p.verdict || '').toUpperCase() === 'APPROVED' ? 'APPROVED' : 'REJECTED';
        return p;
      }
    } catch (e) {
      logger.warn(`rankVideo JSON: ${e.message.substring(0, 80)}`);
      logger.warn(`Raw: ${response.substring(0, 200)}`);
    }
    return null;
  }

  /**
   * FALLBACK: Upload a video file to Gemini File API, then rank visually.
   * Used when URL-based ranking fails (private videos, rate limits, etc.)
   */
  async rankVideoFile(videoPath, country, curatorSkill) {
    if (!fs.existsSync(videoPath)) {
      logger.warn(`rankVideoFile: video not found: ${videoPath}`);
      return null;
    }

    const fileSize = fs.statSync(videoPath).size;
    const fileName = path.basename(videoPath);
    logger.info(`Uploading ${fileName} (${(fileSize / 1024 / 1024).toFixed(1)}MB) to Gemini File API...`);

    const key = this._getKey();
    if (!key) { logger.warn('No API keys for upload'); return null; }

    let uploadedFile;
    try {
      const fileBuffer = fs.readFileSync(videoPath);
      const uploadResp = await axios.post(
        `${GEMINI_UPLOAD}/files?key=${key}`,
        fileBuffer,
        {
          headers: {
            'Content-Type': 'video/mp4',
            'X-Goog-Upload-Protocol': 'resumable',
            'X-Goog-Upload-Command': 'start, upload, finalize',
            'X-Goog-Upload-Header-Content-Length': fileSize.toString(),
            'X-Goog-Upload-Header-Content-Type': 'video/mp4',
          },
          timeout: 300000,
          maxContentLength: 100 * 1024 * 1024,
        }
      );

      uploadedFile = uploadResp.data?.file || uploadResp.data;
      if (!uploadedFile || !uploadedFile.name) {
        logger.warn('File upload response missing file name');
        return null;
      }
      logger.info(`File uploaded: ${uploadedFile.name} (state: ${uploadedFile.state})`);

      // Wait for processing
      let waitCount = 0;
      while (uploadedFile.state === 'PROCESSING' || uploadedFile.state === 'UPLOADING' || uploadedFile.state === 'QUEUED') {
        waitCount++;
        if (waitCount > 60) { logger.warn('File processing timed out'); break; }
        await new Promise(r => setTimeout(r, 5000));
        try {
          const statusResp = await axios.get(`${GEMINI_BASE}/files/${uploadedFile.name}?key=${key}`, { timeout: 15000 });
          uploadedFile = statusResp.data?.file || statusResp.data;
        } catch { logger.warn('File status check failed'); break; }
      }

      if (uploadedFile.state === 'FAILED') {
        logger.warn(`File processing failed: ${uploadedFile.error?.message || ''}`);
        return null;
      }
      logger.success(`File ready: ${uploadedFile.name} (${uploadedFile.state})`);
    } catch (e) {
      logger.warn(`File upload error: ${e.message.substring(0, 100)}`);
      // Log detailed response body
      if (e.response?.data) logger.warn(`Upload response: ${JSON.stringify(e.response.data).substring(0, 200)}`);
      return null;
    }

    if (!uploadedFile || !uploadedFile.name) return null;

    const prompt = `WATCH this video and rank it for reposting on "Mr. WorldWideWebster".

Target country: ${country}

WATCH the video carefully and evaluate:
1. 3-Second Hook
2. Language independence
3. Visual quality
4. Watermark presence
5. Country match: ${country}?
6. YouTube Shorts potential?

Respond ONLY with valid JSON:
{"score": 1-10, "country": "detected country", "hook_score": 1-10, "language_independent": true/false, "has_watermark": true/false, "watermark_type": "type or null", "verdict": "APPROVED/REJECTED", "reasoning": "brief explanation"}`;

    const contents = [{
      role: 'user',
      parts: [
        { file_data: { mimeType: 'video/mp4', file_uri: uploadedFile.uri || uploadedFile.name } },
        { text: prompt }
      ]
    }];

    const response = await this._callAPI(contents, {
      systemInstruction: curatorSkill,
      temperature: 0.3,
      maxTokens: 1024,
      timeout: 120000,
    });

    // Cleanup uploaded file
    try { await axios.delete(`${GEMINI_BASE}/files/${uploadedFile.name}?key=${key}`, { timeout: 10000 }); } catch {}

    if (!response) { logger.warn('rankVideoFile: returned null'); return null; }

    try {
      const m = response.match(/\{[\s\S]*\}/);
      if (m) {
        const p = JSON.parse(m[0]);
        p.verdict = (p.verdict || '').toUpperCase() === 'APPROVED' ? 'APPROVED' : 'REJECTED';
        return p;
      }
    } catch (e) { logger.warn(`rankVideoFile JSON: ${e.message.substring(0, 80)}`); }
    return null;
  }

  async analyzeFrames(framePaths, question, skillPrompt) {
    const parts = [{ text: question }];
    for (const fp of framePaths) {
      try {
        if (fs.existsSync(fp)) {
          const buf = fs.readFileSync(fp);
          parts.push({ inlineData: { mimeType: fp.endsWith('.png') ? 'image/png' : 'image/jpeg', data: buf.toString('base64') } });
        }
      } catch {}
    }
    if (parts.length <= 1) return null;
    return this._callAPI([{ role: 'user', parts }], { systemInstruction: skillPrompt, temperature: 0.3, maxTokens: 1024 });
  }

  async generateQueries(country, tKeys, count = 3) {
    const sp = `You generate YouTube Shorts search queries. Every query ends with #shorts #tiktok #reels (add #douyin for China).`;
    const tl = tKeys?.length ? `\nTrends: ${tKeys.join(', ')}` : '';
    return this.chatJSON(sp, `Generate ${count} queries for ${country}.${tl} Return JSON array.`, { temperature: 0.9 });
  }

  async generateTitle(country, transcript, origTitle) {
    return this.chatJSON(
      'You write YouTube Shorts titles. Max 70 chars, emoji-heavy, English. Return JSON: {"title":"...","description":"...","tags":[...]}',
      `Country: ${country}\n${transcript ? `Transcript: "${transcript.substring(0, 300)}"` : ''}\n${origTitle ? `Original: "${origTitle}"` : ''}`,
      { temperature: 0.8 }
    );
  }

  async translate(text) {
    if (!text || text.length < 3) return text;
    return this.chat('Translate to English. Return ONLY translation.', text, { temperature: 0.3, maxTokens: 512 });
  }

  hasProfanity(text) {
    if (!text) return false;
    return ['fuck', 'shit', 'bitch', 'asshole', 'motherfucker', 'dick', 'cunt', 'pussy', 'bastard', 'whore', 'slut', 'nigger', 'nigga', 'faggot', 'retard', 'chink']
      .some(w => new RegExp(`\\b${w}\\b`, 'i').test(text.toLowerCase()));
  }

  getStats() {
    return { keysLoaded: this.keys.length, currentKey: this.currentKeyIndex + 1, requestsThisSession: this.requestCount };
  }
}

let instance = null;
function getGeminiService() { if (!instance) instance = new GeminiService(); return instance; }
module.exports = { GeminiService, getGeminiService };