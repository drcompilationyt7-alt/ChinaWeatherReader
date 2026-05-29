/**
 * Gemini Service — Free AI Brain for Mr. WorldWideWebster
 * 
 * Uses Google AI Studio (free tier) with 8-key rotation.
 * Handles: text analysis, video URL analysis, ranking, query generation.
 * 
 * Free tier: 15 RPM, 1M tokens/day per key
 * 8 keys = 120 RPM, 8M tokens/day total
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
      const suffix = i === 1 ? '' : `_${i}`;
      const key = process.env[`GEMINI_API_KEY${suffix}`];
      if (key) this.keys.push(key);
    }
    if (this.keys.length === 0 && process.env.GEMINI_API_KEY) {
      this.keys.push(process.env.GEMINI_API_KEY);
    }
    logger.info(`Loaded ${this.keys.length} Gemini API keys`);
    if (this.keys.length === 0) {
      logger.warn('No GEMINI_API_KEY found — AI features will be disabled');
    }
  }

  _getKey() {
    if (this.keys.length === 0) return null;
    return this.keys[this.currentKeyIndex % this.keys.length];
  }

  _rotateKey() {
    if (this.keys.length <= 1) return;
    const oldIndex = this.currentKeyIndex;
    this.currentKeyIndex = (this.currentKeyIndex + 1) % this.keys.length;
    logger.info(`Rotated API key: ${oldIndex + 1} → ${this.currentKeyIndex + 1}`);
  }

  /**
   * Core API call with automatic key rotation on rate limits AND transient errors
   */
  async _callAPI(contents, options = {}) {
    const maxRetries = this.keys.length + 1;
    let lastError = null;

    for (let attempt = 0; attempt < maxRetries; attempt++) {
      const key = this._getKey();
      if (!key) {
        logger.warn('No API keys available');
        return null;
      }

      try {
        const requestBody = {
          contents: contents,
          generationConfig: {
            temperature: options.temperature || 0.7,
            maxOutputTokens: options.maxTokens || 2048,
            topP: options.topP || 0.9,
          },
        };

        if (options.systemInstruction) {
          requestBody.systemInstruction = {
            parts: [{ text: options.systemInstruction }]
          };
        }

        const url = `${GEMINI_API_BASE}/models/${this.model}:generateContent?key=${key}`;
        const response = await axios.post(url, requestBody, {
          headers: { 'Content-Type': 'application/json' },
          timeout: options.timeout || 60000,
        });

        this.requestCount++;

        const candidates = response.data?.candidates;
        if (candidates && candidates.length > 0) {
          const parts = candidates[0].content?.parts;
          if (parts && parts.length > 0) {
            return parts.map(p => p.text || '').join('');
          }
        }

        return null;
      } catch (error) {
        lastError = error;
        const status = error.response?.status;
        const errorText = error.response?.data?.error?.message || error.message;

        // Rate limit or quota exhausted — rotate key, retry
        if (status === 429 || status === 403 || errorText?.includes('quota') || errorText?.includes('RESOURCE_EXHAUSTED')) {
          logger.warn(`Key ${this.currentKeyIndex + 1} rate limited (${status}): ${errorText?.substring(0, 80)}`);
          this._rotateKey();
          await new Promise(r => setTimeout(r, 3000));
          continue;
        }

        // Transient "high demand" errors — rotate key, retry
        if (errorText?.includes('high demand') || errorText?.includes('temporarily') || errorText?.includes('try again later') || errorText?.includes('spikes in demand')) {
          logger.warn(`Key ${this.currentKeyIndex + 1} transient error: ${errorText?.substring(0, 80)}`);
          this._rotateKey();
          await new Promise(r => setTimeout(r, 3000));
          continue;
        }

        // Other errors — don't retry
        logger.error(`Gemini API error: ${errorText?.substring(0, 120)}`);
        return null;
      }
    }

    logger.error(`All ${this.keys.length} API keys exhausted. Last error: ${lastError?.message?.substring(0, 80)}`);
    return null;
  }

  async chat(systemPrompt, userMessage, options = {}) {
    const contents = [{ role: 'user', parts: [{ text: userMessage }] }];
    return await this._callAPI(contents, { ...options, systemInstruction: systemPrompt });
  }

  async chatJSON(systemPrompt, userMessage, options = {}) {
    const response = await this.chat(
      systemPrompt + '\n\nIMPORTANT: Respond ONLY with valid JSON. No markdown, no explanation, just the JSON object.',
      userMessage,
      options
    );
    if (!response) return null;
    try {
      const jsonMatch = response.match(/\{[\s\S]*\}/);
      if (jsonMatch) return JSON.parse(jsonMatch[0]);
      const arrayMatch = response.match(/\[[\s\S]*\]/);
      if (arrayMatch) return JSON.parse(arrayMatch[0]);
    } catch (e) {
      logger.warn(`JSON parse failed: ${e.message.substring(0, 60)}`);
      logger.warn(`Raw response: ${response.substring(0, 200)}`);
    }
    return null;
  }

  async analyzeYouTubeUrl(url, skillPrompt, userQuestion) {
    const userContent = `Analyze this YouTube video: ${url}\n\n${userQuestion || 'Evaluate this video for viral potential, confirm the country of origin, and rate it 1-10.'}`;
    const contents = [{ role: 'user', parts: [{ text: userContent }] }];
    return await this._callAPI(contents, { systemInstruction: skillPrompt, temperature: 0.3, maxTokens: 1024 });
  }

  async analyzeFrames(framePaths, question, skillPrompt) {
    const parts = [{ text: question }];
    for (const framePath of framePaths) {
      try {
        if (fs.existsSync(framePath)) {
          const imageBuffer = fs.readFileSync(framePath);
          const base64Image = imageBuffer.toString('base64');
          const mimeType = framePath.endsWith('.png') ? 'image/png' : 'image/jpeg';
          parts.push({ inlineData: { mimeType, data: base64Image } });
        }
      } catch (e) {
        logger.warn(`Failed to load frame ${framePath}: ${e.message.substring(0, 40)}`);
      }
    }
    if (parts.length <= 1) {
      logger.warn('No frames loaded for analysis');
      return null;
    }
    const contents = [{ role: 'user', parts }];
    return await this._callAPI(contents, { systemInstruction: skillPrompt, temperature: 0.3, maxTokens: 1024 });
  }

  async analyzeFramesJSON(framePaths, question, skillPrompt) {
    const response = await this.analyzeFrames(framePaths, question + '\n\nRespond ONLY with valid JSON.', skillPrompt);
    if (!response) return null;
    try {
      const jsonMatch = response.match(/\{[\s\S]*\}/);
      if (jsonMatch) return JSON.parse(jsonMatch[0]);
      const arrayMatch = response.match(/\[[\s\S]*\]/);
      if (arrayMatch) return JSON.parse(arrayMatch[0]);
    } catch (e) {
      logger.warn(`JSON parse failed from frame analysis: ${e.message.substring(0, 60)}`);
    }
    return null;
  }

  async generateQueries(country, trendKeywords, count = 3) {
    const systemPrompt = `You are a YouTube Shorts content strategist for a channel called "Mr. WorldWideWebster" that posts viral clips from around the world.
    
Rules for queries:
- Every query MUST end with: #shorts #tiktok #reels
- For China queries, ALSO add #douyin
- Queries should find viral clips, memes, dances, trends — NOT talking head videos
- Use native language keywords when possible (e.g., Chinese characters for China)
- Focus on visually engaging content that needs no translation
- Include trending song/audio names when relevant`;
    const trendList = trendKeywords?.length > 0 ? `\nCurrent trending keywords for ${country}: ${trendKeywords.join(', ')}` : '';
    const userMessage = `Generate ${count} YouTube Shorts search queries for ${country}.${trendList}\n\nFind viral clips, memes, dances, and trends. Return JSON array of strings.\nExample format: ["query 1 #shorts #tiktok #reels", "query 2 #shorts #tiktok #reels"]`;
    return await this.chatJSON(systemPrompt, userMessage, { temperature: 0.9 });
  }

  async rankVideo(url, country, curatorSkill) {
    const userMessage = `Rank this YouTube video for reposting on our "Mr. WorldWideWebster" channel (viral clips from around the world).

Video URL: ${url}
Target country: ${country}

Evaluate:
1. 3-Second Hook (does it grab attention immediately?)
2. Language independence (can it be understood without translation?)
3. Visual quality and entertainment value
4. Watermark presence and removal feasibility
5. Does it match the country ${country}?
6. Would it perform well as a YouTube Short?

Respond ONLY with valid JSON. No markdown, no explanation.
Return JSON:
{"score": 1-10, "country": "detected country", "hook_score": 1-10, "language_independent": true/false, "has_watermark": true/false, "watermark_type": "type or null", "verdict": "APPROVED/REJECTED", "reasoning": "brief explanation"}`;

    const response = await this.chat(curatorSkill || 'You are a viral content curator. Respond ONLY with valid JSON.', userMessage, {
      temperature: 0.3,
      maxTokens: 2048,
    });

    if (!response) {
      logger.warn(`rankVideo: Gemini returned null for "${url.substring(0, 50)}"`);
      return null;
    }

    try {
      const jsonMatch = response.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        const parsed = JSON.parse(jsonMatch[0]);
        const verdict = (parsed.verdict || 'REJECTED').toUpperCase().trim();
        parsed.verdict = verdict === 'APPROVED' || verdict === 'CONDITIONAL' ? 'APPROVED' : 'REJECTED';
        return parsed;
      }
    } catch (e) {
      logger.warn(`rankVideo: JSON parse failed — ${e.message.substring(0, 80)}`);
      logger.warn(`rankVideo: Raw response (first 300 chars): ${response.substring(0, 300)}`);
    }
    return null;
  }

  async generateTitle(country, transcriptText, originalTitle) {
    const systemPrompt = `You are a YouTube Shorts title writer for "Mr. WorldWideWebster" channel.
Rules:
- Title: catchy, max 70 characters, English, emoji-heavy, no hashtags in title
- Description: 2-3 sentences in English summarizing the clip, ending with "Subscribe for more global trends!"
- Tags: 5-7 targeted tags`;
    const userMessage = `Generate title + description for a YouTube Short.\nCountry: ${country}\n${transcriptText ? `Transcript: "${transcriptText.substring(0, 300)}"` : ''}\n${originalTitle ? `Original title: "${originalTitle}"` : ''}\n\nReturn JSON: {"title": "...", "description": "...", "tags": ["tag1", "tag2"]}`;
    return await this.chatJSON(systemPrompt, userMessage, { temperature: 0.8 });
  }

  async translate(text) {
    if (!text || text.length < 3) return text;
    return await this.chat('Translate to natural English. Preserve slang, humor, and cultural context. Return ONLY the translation.', text, { temperature: 0.3, maxTokens: 512 });
  }

  hasProfanity(text) {
    if (!text) return false;
    const banned = ['fuck', 'shit', 'bitch', 'asshole', 'motherfucker', 'dick', 'cunt', 'pussy', 'bastard', 'whore', 'slut', 'nigger', 'nigga', 'faggot', 'retard', 'chink'];
    const lower = text.toLowerCase();
    return banned.some(w => new RegExp(`\\b${w}\\b`, 'i').test(lower));
  }

  isSilentVideo(wordCount) {
    return wordCount <= 5;
  }

  getStats() {
    const elapsed = Date.now() - this.lastResetTime;
    return { keysLoaded: this.keys.length, currentKey: this.currentKeyIndex + 1, requestsThisSession: this.requestCount, uptimeMs: elapsed };
  }
}

let instance = null;
function getGeminiService() {
  if (!instance) instance = new GeminiService();
  return instance;
}

module.exports = { GeminiService, getGeminiService };