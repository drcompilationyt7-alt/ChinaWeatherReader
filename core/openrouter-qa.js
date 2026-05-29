/**
 * OpenRouter Nano QA — Lightweight Second-Opinion Checker
 * 
 * After Gemini CLI produces a crop or edit, this module sends frames to
 * OpenRouter nano to ask simple non-directional questions:
 * - "Are you sure this is good?"
 * - "Can you spot any issues?"
 * - "Is the caption blocking content?"
 * 
 * IMPORTANT: This module does NOT give directions or generate commands.
 * It only flags potential problems. Gemini CLI handles the actual fixes.
 * 
 * Uses existing OPENROUTER_API_KEY through _8 (8 keys for rotation).
 */
const axios = require('axios');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('OpenRouterQA');

const QA_MODEL = 'openrouter/owl-alpha';  // Cheap, fast, good enough for QA questions

class OpenRouterQA {
  constructor() {
    this.keys = [];
    this.currentKeyIndex = 0;
    this._loadKeys();
  }

  _loadKeys() {
    for (let i = 1; i <= 8; i++) {
      const suffix = i === 1 ? '' : `_${i}`;
      const key = process.env[`OPENROUTER_API_KEY${suffix}`];
      if (key) this.keys.push(key);
    }
    if (this.keys.length === 0 && process.env.OPENROUTER_API_KEY) {
      this.keys.push(process.env.OPENROUTER_API_KEY);
    }
    logger.info(`Loaded ${this.keys.length} OpenRouter QA keys`);
  }

  _getKey() {
    if (this.keys.length === 0) return null;
    return this.keys[this.currentKeyIndex % this.keys.length];
  }

  _rotateKey() {
    if (this.keys.length <= 1) return;
    this.currentKeyIndex++;
  }

  /**
   * Encode an image file to base64 data URI
   */
  _encodeImage(imagePath) {
    try {
      const buffer = fs.readFileSync(imagePath);
      const ext = path.extname(imagePath).toLowerCase();
      const mime = ext === '.png' ? 'image/png' : 'image/jpeg';
      return `data:${mime};base64,${buffer.toString('base64')}`;
    } catch (e) {
      logger.warn(`Failed to encode image ${imagePath}: ${e.message.substring(0, 40)}`);
      return null;
    }
  }

  /**
   * Send QA check to OpenRouter nano vision model
   * @param {string[]} framePaths - Paths to frames to analyze
   * @param {string} question - The QA question (non-directional)
   * @returns {Object|null} - { yes: boolean, issues: string[], confidence: number }
   */
  async ask(framePaths, question) {
    const maxRetries = this.keys.length + 1;
    const maxTokens = 150;  // Keep it short — we just need yes/no + brief issues

    for (let attempt = 0; attempt < maxRetries; attempt++) {
      const key = this._getKey();
      if (!key) {
        logger.warn('No OpenRouter QA keys available');
        return null;
      }

      try {
        // Build message content: text question + image frames
        const content = [{ type: 'text', text: question }];

        for (const fp of framePaths) {
          const imageUrl = this._encodeImage(fp);
          if (imageUrl) {
            content.push({
              type: 'image_url',
              image_url: { url: imageUrl, detail: 'low' }
            });
          }
        }

        const response = await axios.post(
          'https://openrouter.ai/api/v1/chat/completions',
          {
            model: QA_MODEL,
            messages: [
              {
                role: 'system',
                content: 'You are a quality checker for video clips. Look at the frames and answer a simple question. Respond ONLY with valid JSON: {"yes": true/false, "issues": ["brief issue or empty array"], "confidence": 1-10, "notes": "optional note"}'
              },
              { role: 'user', content }
            ],
            max_tokens: maxTokens,
            temperature: 0.2,
          },
          {
            headers: {
              'Authorization': `Bearer ${key}`,
              'Content-Type': 'application/json',
              'HTTP-Referer': 'https://github.com/mr-worldwidewebster',
              'X-Title': 'Mr. WorldWideWebster QA',
            },
            timeout: 15000,
          }
        );

        if (response.data?.choices?.[0]?.message?.content) {
          const text = response.data.choices[0].message.content;
          const jsonMatch = text.match(/\{[\s\S]*\}/);
          if (jsonMatch) {
            return JSON.parse(jsonMatch[0]);
          }
        }
      } catch (error) {
        const status = error.response?.status;
        const errText = error.response?.data?.error?.message || error.message;

        if (status === 429 || errText?.includes('quota') || errText?.includes('rate')) {
          logger.warn(`QA key ${this.currentKeyIndex + 1} rate limited — rotating`);
          this._rotateKey();
          await new Promise(r => setTimeout(r, 1000));
          continue;
        }

        if (status === 402) {
          // Insufficient credits — rotate
          logger.warn(`QA key ${this.currentKeyIndex + 1} out of credits — rotating`);
          this._rotateKey();
          continue;
        }

        logger.warn(`QA API error: ${errText?.substring(0, 80)}`);
        this._rotateKey();
      }
    }

    return null;
  }

  /**
   * QA check for crop quality
   * Non-directional: just asks "does this look correct?"
   */
  async checkCrop(rawFrames, croppedFrames, country) {
    // Use cropped frames to check — that's what matters
    const frames = croppedFrames.filter(f => fs.existsSync(f));
    if (frames.length === 0) return null;

    const question = `Look at these frames from a video being cropped to YouTube Shorts format (1080x1920 portrait). The original video is from ${country}.

Questions (answer with yes/no):
1. Does the crop look correct? Is the main subject visible and centered?
2. Is there any important content being cut off at the edges?
3. Does the video look properly framed for a portrait YouTube Short?
4. Any visible quality issues (blurriness, stretching, black bars)?

Rate overall quality 1-10. Respond with JSON only.`;

    return await this.ask(frames, question);
  }

  /**
   * QA check for edits (captions, watermark removal, visual quality)
   * Non-directional: just asks "does this look right?"
   */
  async checkEdit(frames, editType, country) {
    if (frames.length === 0) return null;

    const captionCheck = editType === 'tiktok_captions' || editType === 'translation'
      ? `3. Are the captions/subtitles readable and NOT blocking any important content?
4. Is the caption text too big or too small?`
      : '3. No captions expected — is that appropriate for this content?';

    const question = `Look at these frames from an edited YouTube Short for "Mr. WorldWideWebster" (content from ${country}).

Edit type applied: ${editType || 'none'}

Questions (answer with yes/no):
1. Is the video framed correctly (9:16 portrait)?
2. Is the visual quality good (not blurry, not pixelated)?
${captionCheck}
5. Is there any visible watermark or unwanted overlay?
6. Does the first frame look like a good hook for viewers?
7. Would you say this is ready to upload?

Overall quality rating 1-10. Respond with JSON only.`;

    return await this.ask(frames, question);
  }

  /**
   * Generic QA check — "are you sure this is good?"
   * Asks OpenRouter to point out ANY small issues it can spot
   */
  async finalReview(frames, country) {
    if (frames.length === 0) return null;

    const question = `FINAL REVIEW: Look at these frames from a YouTube Short about content from ${country}.

Just answer honestly:
1. Are you sure this is ready to upload?
2. Can you spot ANY small issues or things that could be improved?
3. Is the crop right? Captions readable? Quality acceptable?
4. Would this look good on mobile YouTube Shorts?
5. Overall score 1-10.

Important: Do NOT give edit instructions — just flag issues if any.
If it looks good, just say so. Respond with JSON:
{"ready": true/false, "issues": ["list or empty"], "score": 1-10, "notes": "any concerns"}`;

    return await this.ask(frames, question);
  }
}

// Singleton
let instance = null;

function getOpenRouterQA() {
  if (!instance) {
    instance = new OpenRouterQA();
  }
  return instance;
}

module.exports = { OpenRouterQA, getOpenRouterQA };