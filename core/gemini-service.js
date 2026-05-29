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
    // Load up to 8 Gemini API keys
    for (let i = 1; i <= 8; i++) {
      const suffix = i === 1 ? '' : `_${i}`;
      const key = process.env[`GEMINI_API_KEY${suffix}`];
      if (key) this.keys.push(key);
    }
    
    // Also check the numbered format
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
   * Core API call with automatic key rotation on rate limits
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

        // Add system instruction if provided
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
        
        // Extract text from response
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
        
        if (status === 429 || status === 403 || errorText?.includes('quota') || errorText?.includes('RESOURCE_EXHAUSTED')) {
          logger.warn(`Key ${this.currentKeyIndex + 1} rate limited (${status}): ${errorText?.substring(0, 80)}`);
          this._rotateKey();
          // Small delay before retry
          await new Promise(r => setTimeout(r, 1000));
          continue;
        }
        
        // Non-rate-limit error — don't rotate, just fail
        logger.error(`Gemini API error: ${errorText?.substring(0, 120)}`);
        return null;
      }
    }

    logger.error(`All ${this.keys.length} API keys exhausted. Last error: ${lastError?.message?.substring(0, 80)}`);
    return null;
  }

  /**
   * Simple text chat with Gemini
   */
  async chat(systemPrompt, userMessage, options = {}) {
    const contents = [{
      role: 'user',
      parts: [{ text: userMessage }]
    }];

    return await this._callAPI(contents, {
      ...options,
      systemInstruction: systemPrompt,
    });
  }

  /**
   * Chat that returns parsed JSON
   */
  async chatJSON(systemPrompt, userMessage, options = {}) {
    const response = await this.chat(
      systemPrompt + '\n\nIMPORTANT: Respond ONLY with valid JSON. No markdown, no explanation, just the JSON object.',
      userMessage,
      options
    );

    if (!response) return null;

    try {
      // Try to extract JSON from the response
      const jsonMatch = response.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        return JSON.parse(jsonMatch[0]);
      }
      // Try array format
      const arrayMatch = response.match(/\[[\s\S]*\]/);
      if (arrayMatch) {
        return JSON.parse(arrayMatch[0]);
      }
    } catch (e) {
      logger.warn(`JSON parse failed: ${e.message.substring(0, 60)}`);
      logger.warn(`Raw response: ${response.substring(0, 200)}`);
    }
    return null;
  }

  /**
   * Analyze a YouTube URL directly (Gemini can watch YouTube videos natively)
   * Uses the curator skill as system prompt
   */
  async analyzeYouTubeUrl(url, skillPrompt, userQuestion) {
    const userContent = `Analyze this YouTube video: ${url}\n\n${userQuestion || 'Evaluate this video for viral potential, confirm the country of origin, and rate it 1-10.'}`;

    const contents = [{
      role: 'user',
      parts: [{ text: userContent }]
    }];

    return await this._callAPI(contents, {
      systemInstruction: skillPrompt,
      temperature: 0.3,
      maxTokens: 1024,
    });
  }

  /**
   * Analyze multiple frames (images) for crop/edit decisions
   * @param {string[]} framePaths - Array of frame file paths
   * @param {string} question - What to analyze
   * @param {string} skillPrompt - Optional skill prompt
   */
  async analyzeFrames(framePaths, question, skillPrompt) {
    const parts = [];

    // Add the text question first
    parts.push({ text: question });

    // Add each frame as inline image
    for (const framePath of framePaths) {
      try {
        if (fs.existsSync(framePath)) {
          const imageBuffer = fs.readFileSync(framePath);
          const base64Image = imageBuffer.toString('base64');
          const mimeType = framePath.endsWith('.png') ? 'image/png' : 'image/jpeg';
          
          parts.push({
            inlineData: {
              mimeType: mimeType,
              data: base64Image,
            }
          });
        }
      } catch (e) {
        logger.warn(`Failed to load frame ${framePath}: ${e.message.substring(0, 40)}`);
      }
    }

    if (parts.length <= 1) {
      logger.warn('No frames loaded for analysis');
      return null;
    }

    const contents = [{
      role: 'user',
      parts: parts
    }];

    return await this._callAPI(contents, {
      systemInstruction: skillPrompt,
      temperature: 0.3,
      maxTokens: 1024,
    });
  }

  /**
   * Analyze frames and return parsed JSON
   */
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

  /**
   * Generate search queries for a country
   */
  async generateQueries(country, trendKeywords, count = 3) {
    const systemPrompt = `You are a YouTube Shorts content strategist for a channel called "Mr. WorldWideWebster" that posts viral clips from around the world.
    
Rules for queries:
- Every query MUST end with: #shorts #tiktok #reels
- For China queries, ALSO add #douyin
- Queries should find viral clips, memes, dances, trends — NOT talking head videos
- Use native language keywords when possible (e.g., Chinese characters for China)
- Focus on visually engaging content that needs no translation
- Include trending song/audio names when relevant`;

    const trendList = trendKeywords?.length > 0 
      ? `\nCurrent trending keywords for ${country}: ${trendKeywords.join(', ')}`
      : '';

    const userMessage = `Generate ${count} YouTube Shorts search queries for ${country}.${trendList}

Find viral clips, memes, dances, and trends. Return JSON array of strings.
Example format: ["query 1 #shorts #tiktok #reels", "query 2 #shorts #tiktok #reels"]`;

    return await this.chatJSON(systemPrompt, userMessage, { temperature: 0.9 });
  }

  /**
   * Rank a video for viral potential
   */
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
        // Normalize verdict field
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

  /**
   * Generate title + description for a short
   */
  async generateTitle(country, transcriptText, originalTitle) {
    const systemPrompt = `You are a YouTube Shorts title writer for "Mr. WorldWideWebster" channel.
Rules:
- Title: catchy, max 70 characters, English, emoji-heavy, no hashtags in title
- Description: 2-3 sentences in English summarizing the clip, ending with "Subscribe for more global trends!"
- Tags: 5-7 targeted tags`;

    const userMessage = `Generate title + description for a YouTube Short.
Country: ${country}
${transcriptText ? `Transcript: "${transcriptText.substring(0, 300)}"` : ''}
${originalTitle ? `Original title: "${originalTitle}"` : ''}

Return JSON: {"title": "...", "description": "...", "tags": ["tag1", "tag2"]}`;

    return await this.chatJSON(systemPrompt, userMessage, { temperature: 0.8 });
  }

  /**
   * Translate text to English
   */
  async translate(text) {
    if (!text || text.length < 3) return text;
    return await this.chat(
      'Translate to natural English. Preserve slang, humor, and cultural context. Return ONLY the translation.',
      text,
      { temperature: 0.3, maxTokens: 512 }
    );
  }

  /**
   * Check if a list of words contains profanity
   */
  hasProfanity(text) {
    if (!text) return false;
    const banned = ['fuck', 'shit', 'bitch', 'asshole', 'motherfucker', 'dick', 'cunt', 'pussy', 'bastard', 'whore', 'slut', 'nigger', 'nigga', 'faggot', 'retard', 'chink'];
    const lower = text.toLowerCase();
    return banned.some(w => new RegExp(`\\b${w}\\b`, 'i').test(lower));
  }

  /**
   * Check if a word count is too low (video likely has no meaningful speech)
   */
  isSilentVideo(wordCount) {
    return wordCount <= 5;
  }

  /**
   * Get usage stats
   */
  getStats() {
    const elapsed = Date.now() - this.lastResetTime;
    return {
      keysLoaded: this.keys.length,
      currentKey: this.currentKeyIndex + 1,
      requestsThisSession: this.requestCount,
      uptimeMs: elapsed,
    };
  }
}

// Singleton
let instance = null;

function getGeminiService() {
  if (!instance) {
    instance = new GeminiService();
  }
  return instance;
}

module.exports = { GeminiService, getGeminiService };