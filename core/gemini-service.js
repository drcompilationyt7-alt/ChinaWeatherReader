/**
 * Gemini Service — Free AI Brain for Mr. WorldWideWebster
 * Uses Google AI Studio (free tier) with 8-key rotation.
 * Free tier: 15 RPM, 1M tokens/day per key, 8 keys = 120 RPM
 */
const axios = require('axios');
const { Logger } = require('./logger');
const path = require('path');
const fs = require('fs');

const logger = new Logger('GeminiService');
const GEMINI_API_BASE = 'https://generativelanguage.googleapis.com/v1beta';

class GeminiService {
  constructor() {
    this.keys = [];
    this.currentKeyIndex = 0;
    this.model = 'gemini-2.5-flash';
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
    logger.info(`Loaded ${this.keys.length} Gemini API keys`);
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

        const resp = await axios.post(
          `${GEMINI_API_BASE}/models/${this.model}:generateContent?key=${key}`,
          body,
          { headers: { 'Content-Type': 'application/json' }, timeout: options.timeout || 60000 }
        );

        this.requestCount++;
        const candidates = resp.data?.candidates;

        if (!candidates || candidates.length === 0) {
          // Blocked by safety filters or empty response
          const blockReason = resp.data?.promptFeedback?.blockReason;
          logger.warn(`Gemini returned empty candidates (block: ${blockReason || 'unknown'})`);
          this._rotateKey();
          await new Promise(r => setTimeout(r, 3000));
          continue;
        }

        const parts = candidates[0].content?.parts;
        if (parts && parts.length > 0) return parts.map(p => p.text || '').join('');
        return null;
      } catch (error) {
        lastError = error;
        const status = error.response?.status;
        const errText = error.response?.data?.error?.message || error.message;

        if (status === 429 || status === 403 || errText?.includes('quota') || errText?.includes('RESOURCE_EXHAUSTED')) {
          logger.warn(`Key ${this.currentKeyIndex + 1} rate limited — rotating`);
          this._rotateKey();
          await new Promise(r => setTimeout(r, 3000));
          continue;
        }
        if (errText?.includes('high demand') || errText?.includes('temporarily') || errText?.includes('spikes')) {
          logger.warn(`Key ${this.currentKeyIndex + 1} transient error — rotating`);
          this._rotateKey();
          await new Promise(r => setTimeout(r, 3000));
          continue;
        }
        logger.error(`Gemini API error: ${errText?.substring(0, 120)}`);
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
    } catch (e) { logger.warn(`JSON parse: ${e.message.substring(0, 60)}`); logger.warn(`Raw: ${r.substring(0, 200)}`); }
    return null;
  }

  async rankVideo(url, country, curatorSkill) {
    const msg = `Rank this YouTube video for reposting on "Mr. WorldWideWebster".

Video URL: ${url}
Target country: ${country}

Evaluate:
1. 3-Second Hook
2. Language independence
3. Visual quality
4. Watermark presence
5. Country match: ${country}?
6. YouTube Shorts potential?

Respond ONLY with valid JSON:
{"score": 1-10, "country": "detected country", "hook_score": 1-10, "language_independent": true/false, "has_watermark": true/false, "verdict": "APPROVED/REJECTED", "reasoning": "brief"}`;

    const resp = await this.chat(curatorSkill || '', msg, { temperature: 0.3, maxTokens: 1024 });
    if (!resp) { logger.warn(`rankVideo: null for "${url.substring(0, 50)}"`); return null; }

    try {
      const m = resp.match(/\{[\s\S]*\}/);
      if (m) {
        const p = JSON.parse(m[0]);
        p.verdict = (p.verdict || '').toUpperCase() === 'APPROVED' ? 'APPROVED' : 'REJECTED';
        return p;
      }
    } catch (e) { logger.warn(`rankVideo JSON: ${e.message.substring(0, 80)}`); logger.warn(`Raw: ${resp.substring(0, 200)}`); }
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