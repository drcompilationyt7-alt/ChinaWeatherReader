/**
 * Gemini Service — Free AI Brain for Mr. WorldWideWebster
 * Uses Google AI Studio (free tier) with 8-key rotation.
 * Free tier: 15 RPM, 1M tokens/day per key, 8 keys = 120 RPM
 * 
 * Supports:
 * - YouTube URL video analysis (file_data.file_uri)
 * - Video File API upload for fallback
 * - Model fallback chain: gemini-3.5-flash → gemini-2.5-flash
 * - Exponential backoff on 503 (capped at 30s)
 * - Key rotation first, model rotation only after all keys exhausted
 */
const axios = require('axios');
const { Logger } = require('./logger');
const path = require('path');
const fs = require('fs');

const logger = new Logger('GeminiService');
const GEMINI_BASE = 'https://generativelanguage.googleapis.com/v1beta';
const GEMINI_UPLOAD = 'https://generativelanguage.googleapis.com/upload/v1beta';
const MIN_DELAY = 2000;

const MODEL_CHAIN = [
  'gemini-3.5-flash',
  'gemini-2.5-flash',
];

class GeminiService {
  constructor() {
    this.keys = [];
    this.currentKeyIndex = 0;
    this.currentModelIndex = 0;
    this.requestCount = 0;
    this.lastResetTime = Date.now();
    this._loadKeys();
  }

  get model() { return MODEL_CHAIN[this.currentModelIndex % MODEL_CHAIN.length]; }

  _loadKeys() {
    for (let i = 1; i <= 8; i++) {
      const key = process.env[`GEMINI_API_KEY${i === 1 ? '' : `_${i}`}`];
      if (key) this.keys.push(key);
    }
    if (this.keys.length === 0 && process.env.GEMINI_API_KEY) this.keys.push(process.env.GEMINI_API_KEY);
    logger.info(`Loaded ${this.keys.length} Gemini API keys, models: ${MODEL_CHAIN.join(' → ')}`);
  }

  _getKey() { return this.keys.length ? this.keys[this.currentKeyIndex % this.keys.length] : null; }

  _rotateKey() {
    if (this.keys.length <= 1) return;
    const prev = this.currentKeyIndex;
    this.currentKeyIndex = (this.currentKeyIndex + 1) % this.keys.length;
    // If we wrapped around to 0, all keys were tried — rotate model
    if (this.currentKeyIndex === 0 && prev !== 0) {
      this._rotateModel();
    }
  }

  _rotateModel() {
    const prev = MODEL_CHAIN[this.currentModelIndex % MODEL_CHAIN.length];
    this.currentModelIndex = (this.currentModelIndex + 1) % MODEL_CHAIN.length;
    const next = this.model;
    if (prev !== next) {
      logger.info(`All keys exhausted for ${prev} — switching model to: ${next}`);
    }
  }

  async _callAPI(contents, options = {}) {
    // Total attempts = (keys × models) + 1 extra
    const maxRetries = (this.keys.length || 1) * MODEL_CHAIN.length + 1;
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
          logger.warn(`Gemini empty candidates (model: ${this.model}, block: ${blockReason || 'unknown'}, key: ${this.currentKeyIndex + 1})`);
          this._rotateKey();
          await new Promise(r => setTimeout(r, MIN_DELAY));
          continue;
        }

        const parts = candidates[0].content?.parts;
        if (parts && parts.length > 0) return parts.map(p => p.text || '').join('');
        return null;
      } catch (error) {
        lastError = error;
        const status = error.response?.status;
        const errText = error.response?.data?.error?.message || error.message;

        logger.warn(`Key ${this.currentKeyIndex + 1} model ${this.model} error (status ${status}): ${(errText || '').substring(0, 120)}`);

        if (status === 403 || errText?.includes('forbidden') || errText?.includes('not allowed') || errText?.includes('permission') || errText?.includes('private video')) {
          logger.warn('Video access denied (private/restricted) — not retrying other keys/models');
          return null;
        }

        if (status === 429 || errText?.includes('quota') || errText?.includes('RESOURCE_EXHAUSTED')) {
          const delay = Math.min(MIN_DELAY * (attempt + 1), 30000);
          logger.warn(`Key ${this.currentKeyIndex + 1} rate limited — rotating, waiting ${delay}ms`);
          this._rotateKey();
          await new Promise(r => setTimeout(r, delay));
          continue;
        }

        if (status === 503 || errText?.includes('high demand') || errText?.includes('temporarily') || errText?.includes('spikes') || errText?.includes('unavailable') || errText?.includes('deadline') || errText?.includes('write EPIPE')) {
          const backoff = Math.min(Math.pow(2, attempt) * 1000 + Math.random() * 1000, 30000);
          logger.warn(`Model ${this.model} overloaded — backoff ${Math.round(backoff)}ms, then rotating key`);
          this._rotateKey();
          await new Promise(r => setTimeout(r, backoff));
          continue;
        }

        if (status === 400) {
          logger.warn(`Key ${this.currentKeyIndex + 1} model ${this.model} 400 error — rotating`);
          this._rotateKey();
          await new Promise(r => setTimeout(r, MIN_DELAY));
          continue;
        }

        this._rotateKey();
        await new Promise(r => setTimeout(r, MIN_DELAY));
      }
    }
    logger.error(`All keys + models exhausted. Last: ${(lastError?.message || '').substring(0, 80)}`);
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

  async rankVideo(url, country, curatorSkill, engagementData = null) {
    let metricsBlock = '';
    if (engagementData) {
      const velocity = engagementData.ageInDays > 0 ? (engagementData.views / engagementData.ageInDays).toFixed(0) : 'N/A';
      const likeRatio = engagementData.views > 0 ? ((engagementData.likes / engagementData.views) * 100).toFixed(2) : 'N/A';
      const commentDensity = engagementData.views > 0 ? ((engagementData.comments / engagementData.views) * 100).toFixed(3) : 'N/A';

      metricsBlock = `
ENGAGEMENT METRICS:
- Views: ${engagementData.views || 0}
- Likes: ${engagementData.likes || 0}
- Comments: ${engagementData.comments || 0}
- Age in days: ${engagementData.ageInDays || 0}
- Title: "${engagementData.title || 'Unknown'}"

HARD METRIC COMPUTATION:
- Velocity (views/day): ${velocity}
- Like Ratio (likes/views×100): ${likeRatio}%  [Benchmark: >3% good, <1.5% bad]
- Comment Density (comments/views×100): ${commentDensity}%  [Benchmark: >0.2% = high engagement]`;

      if (engagementData.topComments && engagementData.topComments.length > 0) {
        const commentLines = engagementData.topComments
          .map((c, i) => `  ${i + 1}. "${c.text}" (${c.likes} likes, by ${c.author})`)
          .join('\n');
        metricsBlock += `\n\nTOP VIEWER COMMENTS:\n${commentLines}`;
      }
    }

    const prompt = `WATCH this YouTube video and rank it for reposting on "Mr. WorldWideWebster" channel.

Target country: ${country}${metricsBlock}

HYBRID EVALUATION FRAMEWORK:
1. Hard Metrics (40% weight): Evaluate velocity, like ratio, and comment density against benchmarks
2. Multimodal Visual (60% weight): 3-Second Hook, language independence, production cleanliness

Carefully evaluate the actual video content:
1. 3-Second Hook — does it grab attention in the first 3 seconds?
2. Language independence — can it be understood without translation?
3. Visual quality and entertainment value
4. Watermark presence — can it be cropped out?
5. Does the content match country ${country}?
6. Would this perform well as a YouTube Short?

Respond ONLY with valid JSON (no markdown):
{"score": 1-10, "country": "detected country", "hook_score": 1-10, "velocity_score": 1-10, "engagement_score": 1-10, "language_independent": true/false, "has_watermark": true/false, "watermark_type": "type or null", "verdict": "APPROVED/REJECTED", "reasoning": "brief hybrid evaluation — metrics + visual quality + hook"}`;

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

  async rankVideoFile(videoPath, country, curatorSkill, engagementData = null) {
    if (!fs.existsSync(videoPath)) {
      logger.warn(`rankVideoFile: video not found: ${videoPath}`);
      return null;
    }

    const fileSize = fs.statSync(videoPath).size;
    const fileName = path.basename(videoPath);
    const fileBuffer = fs.readFileSync(videoPath);

    // Helper: upload video with the current key and return the uploaded file object
    async function uploadWithCurrentKey(displayName, buffer, size) {
      logger.info(`Uploading ${displayName} (${(size / 1024 / 1024).toFixed(1)}MB) to Gemini File API...`);
      const currentKey = this._getKey();
      if (!currentKey) { logger.warn('No API keys for upload'); return null; }

      try {
        const startResp = await axios.post(
          `${GEMINI_UPLOAD}/files?key=${currentKey}`,
          { file: { displayName } },
          {
            headers: {
              'X-Goog-Upload-Protocol': 'resumable',
              'X-Goog-Upload-Command': 'start',
              'X-Goog-Upload-Header-Content-Length': size.toString(),
              'X-Goog-Upload-Header-Content-Type': 'video/mp4',
              'Content-Type': 'application/json',
            },
            timeout: 30000,
          }
        );

        const uploadUrl = startResp.headers['x-goog-upload-url'];
        if (!uploadUrl) { logger.warn('No upload URL'); return null; }

        const uploadResp = await axios.put(
          uploadUrl, buffer,
          {
            headers: {
              'Content-Type': 'video/mp4',
              'Content-Length': size.toString(),
              'X-Goog-Upload-Command': 'upload, finalize',
              'X-Goog-Upload-Offset': '0',
            },
            timeout: 300000,
            maxContentLength: Infinity,
            maxBodyLength: Infinity,
            transformRequest: [(data) => data],
            transformResponse: [(data) => data],
          }
        );

        // Parse raw buffer response to JSON
        let respData = uploadResp.data;
        try {
          if (typeof respData === 'string' || Buffer.isBuffer(respData)) {
            respData = JSON.parse(respData.toString());
          }
        } catch (e) {}

        let uf = respData?.file || respData;
        if (!uf || !uf.name) {
          const fileUri = uploadResp.headers['x-goog-upload-file-uri'];
          if (fileUri) uf = { name: fileUri, state: 'PROCESSING' };
          else { logger.warn('No file name in response'); return null; }
        }

        // Wait for metadata propagation before polling
        logger.info(`File uploaded: ${uf.name} (state: ${uf.state}) — waiting 3s for metadata propagation...`);
        await new Promise(r => setTimeout(r, 3000));
        let waitCount = 0;
        while (uf.state === 'PROCESSING' || uf.state === 'UPLOADING' || uf.state === 'QUEUED') {
          waitCount++;
          if (waitCount > 25) { logger.warn('File processing timed out (~19 min)'); break; }
          logger.info(`  Poll ${waitCount}/25 — waiting 45s (state: ${uf.state})...`);
          await new Promise(r => setTimeout(r, 45000));
          try {
            const pollUrl = `${GEMINI_BASE}/files/${uf.name}?key=${currentKey}`;
            logger.info(`  Polling URL: ${GEMINI_BASE}/files/${uf.name}`);
            const statusResp = await axios.get(pollUrl, { timeout: 15000 });
            uf = statusResp.data?.file || statusResp.data;
          } catch (e) {
            const errBody = e.response?.data ? JSON.stringify(e.response.data).substring(0, 200) : '';
            logger.warn(`File status check failed (status ${e.response?.status || 'none'}): ${errBody || e.message}`);
            logger.warn(`  File: ${uf.name} | Key used: ${currentKey}`);
            await new Promise(r => setTimeout(r, 30000));
          }
        }

        if (uf.state === 'FAILED') { logger.warn(`File failed: ${uf.error?.message || ''}`); return null; }
        if (uf.state !== 'ACTIVE') { logger.warn(`File not ACTIVE (state: ${uf.state}) — skipping`); return null; }
        logger.success(`File ready: ${uf.name} (${uf.state})`);
        return { file: uf, key: currentKey };
      } catch (e) {
        logger.warn(`File upload error: ${e.message.substring(0, 100)}`);
        if (e.response?.data) logger.warn(`Upload response: ${JSON.stringify(e.response.data).substring(0, 200)}`);
        return null;
      }
    }

    // Check if key changed — helper to compare current key vs upload key
    const uploaded = await uploadWithCurrentKey.call(this, fileName, fileBuffer, fileSize);
    if (!uploaded) return null;

    let currentKey = uploaded.key;
    let uploadedFile = uploaded.file;

    let metricsBlock = '';
    if (engagementData) {
      const velocity = engagementData.ageInDays > 0 ? (engagementData.views / engagementData.ageInDays).toFixed(0) : 'N/A';
      const likeRatio = engagementData.views > 0 ? ((engagementData.likes / engagementData.views) * 100).toFixed(2) : 'N/A';
      const commentDensity = engagementData.views > 0 ? ((engagementData.comments / engagementData.views) * 100).toFixed(3) : 'N/A';

      metricsBlock = `
ENGAGEMENT METRICS:
- Views: ${engagementData.views || 0}
- Likes: ${engagementData.likes || 0}
- Comments: ${engagementData.comments || 0}
- Age in days: ${engagementData.ageInDays || 0}
- Title: "${engagementData.title || 'Unknown'}"

HARD METRIC COMPUTATION:
- Velocity (views/day): ${velocity}
- Like Ratio (likes/views×100): ${likeRatio}%  [Benchmark: >3% good, <1.5% bad]
- Comment Density (comments/views×100): ${commentDensity}%  [Benchmark: >0.2% = high engagement]`;

      if (engagementData.topComments && engagementData.topComments.length > 0) {
        const commentLines = engagementData.topComments
          .map((c, i) => `  ${i + 1}. "${c.text}" (${c.likes} likes, by ${c.author})`)
          .join('\n');
        metricsBlock += `\n\nTOP VIEWER COMMENTS:\n${commentLines}`;
      }
    }

    const prompt = `WATCH this video and rank it for reposting on "Mr. WorldWideWebster".

Target country: ${country}${metricsBlock}

HYBRID EVALUATION FRAMEWORK:
1. Hard Metrics (40% weight): Evaluate velocity, like ratio, and comment density against benchmarks
2. Multimodal Visual (60% weight): 3-Second Hook, language independence, production cleanliness

WATCH the video carefully and evaluate:
1. 3-Second Hook
2. Language independence
3. Visual quality
4. Watermark presence
5. Country match: ${country}?
6. YouTube Shorts potential?

Respond ONLY with valid JSON:
{"score": 1-10, "country": "detected country", "hook_score": 1-10, "velocity_score": 1-10, "engagement_score": 1-10, "language_independent": true/false, "has_watermark": true/false, "watermark_type": "type or null", "verdict": "APPROVED/REJECTED", "reasoning": "brief hybrid evaluation — metrics + visual quality + hook"}`;

    const contents = [{
      role: 'user',
      parts: [
        { file_data: { mimeType: 'video/mp4', file_uri: uploadedFile.uri || uploadedFile.name } },
        { text: prompt }
      ]
    }];

    let response = await this._callAPI(contents, {
      systemInstruction: curatorSkill,
      temperature: 0.3,
      maxTokens: 1024,
      timeout: 120000,
    });

    // If query failed (null), check if key rotated — re-upload and retry once
    if (!response) {
      const newKey = this._getKey();
      if (newKey !== currentKey) {
        logger.warn(`Key rotated (${currentKey} → ${newKey}) — re-uploading video and retrying query`);
        // Delete old file
        try { await axios.delete(`${GEMINI_BASE}/files/${uploadedFile.name}?key=${currentKey}`, { timeout: 10000 }); } catch {}
        // Re-upload with new key
        const reUploaded = await uploadWithCurrentKey.call(this, fileName, fileBuffer, fileSize);
        if (reUploaded) {
          currentKey = reUploaded.key;
          uploadedFile = reUploaded.file;
          contents[0].parts[0].file_data.file_uri = uploadedFile.uri || uploadedFile.name;
          response = await this._callAPI(contents, {
            systemInstruction: curatorSkill,
            temperature: 0.3,
            maxTokens: 1024,
            timeout: 120000,
          });
        }
      }
    }

    // Cleanup
    try { await axios.delete(`${GEMINI_BASE}/files/${uploadedFile.name}?key=${currentKey}`, { timeout: 10000 }); } catch {}

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

  async generateQueries(country, tKeys, count = 20) {
    // Load query generator skill for rich, specific queries
    const skillPath = path.join(__dirname, '..', 'skills', 'type1', 'query-generator.md');
    let skillContent = '';
    try {
      if (fs.existsSync(skillPath)) {
        skillContent = fs.readFileSync(skillPath, 'utf8');
      }
    } catch (e) {}
    
    const tl = tKeys?.length ? `\nRelevant trend keywords for ${country}: ${tKeys.join(', ')}` : '';
    const userMessage = `Generate ${count} search queries for finding viral clips from ${country}.${tl}\n\nReturn ONLY a JSON array of strings, no markdown.`;
    
    return this.chatJSON(skillContent || 'You generate YouTube Shorts search queries.', userMessage, { temperature: 0.9 });
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