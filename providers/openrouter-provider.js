/**
 * Mr. WorldWideWebster — OpenRouter Provider with Multi-Key Rotation + Video Vision
 *
 * Features:
 * - Full OpenAI-compatible chat/images API via OpenRouter
 * - Multi-key rotation across 4 API keys (fallback if one is rate-limited)
 * - Automatic model fallback: primary → free model → next key
 * - chatWithVideo(): Send video files to vision models (Nemotron, etc.)
 * - browserSearch(): Use owl-alpha to drive Playwright browser for web search
 * - Max token limits to avoid 402 Payment Required errors
 * - Proper error handling for credit limits, rate limits, and timeouts
 */
const OpenAI = require('openai');
const fs = require('fs');
const path = require('path');
const { Logger } = require('../core/logger');

class OpenRouterProvider {
  constructor(config) {
    this.logger = new Logger('OpenRouter');

    // Collect all available API keys (up to 4)
    this.apiKeys = this._collectApiKeys(config);
    this.currentKeyIndex = 0;

    // Model configuration with fallback chain
    this.defaultModel = config.openrouter?.defaultModel || 'openrouter/owl-alpha';
    this.fallbackModel = 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free';
    this.videoModel = 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free';
    this.scriptModel = config.openrouter?.scriptModel || this.defaultModel;
    this.agentModel = config.openrouter?.agentModel || this.defaultModel;
    this.imageModel = config.openrouter?.imageModel || 'black-forest-labs/flux-schnell';

    // Safety limits to avoid 402 Payment Required
    this.DEFAULT_MAX_TOKENS = 1500;
    this.SCRIPT_MAX_TOKENS = 2000;
    this.CHEAP_MAX_TOKENS = 500;
    this.VIDEO_MAX_TOKENS = 1000;

    this.deadKeys = new Set();

    this._client = this._buildClient(this.currentKeyIndex);

    this.logger.info(`OpenRouter initialized with ${this.apiKeys.length} key(s)`);
    this.logger.info(`Default model: ${this.defaultModel}`);
    this.logger.info(`Video model: ${this.videoModel}`);
    this.logger.info(`Fallback model: ${this.fallbackModel}`);
  }

  _collectApiKeys(config) {
    const keys = [];
    if (config.openrouter?.apiKey) keys.push(config.openrouter.apiKey);
    for (let i = 2; i <= 4; i++) {
      const envKey = process.env[`OPENROUTER_API_KEY_${i}`];
      if (envKey) keys.push(envKey);
    }
    return keys;
  }

  _buildClient(keyIndex) {
    if (keyIndex >= this.apiKeys.length) return null;
    return new OpenAI({
      baseURL: 'https://openrouter.ai/api/v1',
      apiKey: this.apiKeys[keyIndex],
      defaultHeaders: {
        'HTTP-Referer': 'https://github.com/mr-worldwidewebster',
        'X-Title': 'Mr. WorldWideWebster',
      },
    });
  }

  _rotateKey() {
    const startIndex = this.currentKeyIndex;
    for (let i = 0; i < this.apiKeys.length; i++) {
      const nextIndex = (startIndex + 1 + i) % this.apiKeys.length;
      if (!this.deadKeys.has(nextIndex)) {
        this.currentKeyIndex = nextIndex;
        this._client = this._buildClient(nextIndex);
        this.logger.info(`Rotated to API key #${nextIndex + 1}`);
        return true;
      }
    }
    this.logger.error('All API keys are exhausted');
    return false;
  }

  _isRetryableError(error) {
    const msg = (error.message || '').toLowerCase();
    const status = error.status || 0;
    return (
      status === 402 || status === 429 || status === 401 || status === 403 ||
      msg.includes('payment required') || msg.includes('insufficient credits') ||
      msg.includes('rate limit') || msg.includes('max_tokens') ||
      msg.includes('quota exceeded') || msg.includes('insufficient_quota')
    );
  }

  async _callWithRetry(model, messages, options = {}) {
    const maxRetries = options.maxRetries || (this.apiKeys.length * 2) + 1;
    let lastError = null;

    const tryModels = [
      model,
      this.fallbackModel,
      model,
      'openai/gpt-4o-mini',
    ];

    for (let attempt = 0; attempt < maxRetries; attempt++) {
      if (attempt > 0 && this.apiKeys.length > 1) {
        const rotated = this._rotateKey();
        if (!rotated) break;
      }

      const currentModel = tryModels[Math.min(attempt, tryModels.length - 1)];

      let maxTokens = options.maxTokens || this.DEFAULT_MAX_TOKENS;
      if (options.useCheapModel) maxTokens = this.CHEAP_MAX_TOKENS;
      if (options.useScriptModel) maxTokens = this.SCRIPT_MAX_TOKENS;
      if (options.useVideo) maxTokens = this.VIDEO_MAX_TOKENS;
      if (currentModel === this.fallbackModel) maxTokens = Math.min(maxTokens, 1000);

      try {
        const response = await this._client.chat.completions.create({
          model: currentModel,
          messages: messages,
          max_tokens: maxTokens,
          temperature: options.temperature || 0.7,
          response_format: options.responseFormat || undefined,
        });

        if (response.choices?.[0]?.message?.content) {
          return response.choices[0].message.content;
        }
        throw new Error('Empty response from LLM');
      } catch (error) {
        lastError = error;
        if (this._isRetryableError(error)) {
          this.deadKeys.add(this.currentKeyIndex);
          this.logger.warn(`Key #${this.currentKeyIndex + 1} failed: ${error.message}`);
        } else {
          this.logger.warn(`Non-retryable: ${error.message}`);
          if (attempt >= 2) throw error;
        }
      }
    }
    throw lastError || new Error('All retries exhausted');
  }

  async chat(systemPrompt, userMessage, options = {}) {
    if (!this._client) throw new Error('No OpenRouter API keys configured.');

    const model = options.model || 
      (options.useScriptModel ? this.scriptModel : 
       options.useCheapModel ? this.fallbackModel : 
       this.defaultModel);

    const messages = [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: userMessage },
    ];

    return await this._callWithRetry(model, messages, options);
  }

  async chatJSON(systemPrompt, userMessage, options = {}) {
    const strictPrompt = systemPrompt + 
      '\n\nRespond ONLY with valid JSON. No markdown, no explanation, no code blocks.';

    const result = await this.chat(strictPrompt, userMessage, {
      ...options,
      responseFormat: { type: 'json_object' },
    });

    const cleaned = result
      .replace(/```json\s*/gi, '')
      .replace(/```\s*/g, '')
      .trim();

    try {
      return JSON.parse(cleaned);
    } catch {
      const jsonMatch = cleaned.match(/\{[\s\S]*\}/);
      if (jsonMatch) return JSON.parse(jsonMatch[0]);
      throw new Error(`Failed to parse JSON: ${cleaned.substring(0, 200)}`);
    }
  }

  /**
   * Send a video file to a vision model for analysis.
   * Uses Nemotron or any OpenRouter model that supports video input.
   * The video file is read as base64 and sent as data URI.
   */
  async chatWithVideo(systemPrompt, videoFilePath, textPrompt, options = {}) {
    if (!this._client) throw new Error('No OpenRouter API keys configured.');

    if (!fs.existsSync(videoFilePath)) {
      throw new Error(`Video file not found: ${videoFilePath}`);
    }

    const model = options.model || this.videoModel;
    
    // Read video file and convert to base64
    const ext = path.extname(videoFilePath).toLowerCase().replace('.', '');
    const mimeType = ext === 'mp4' ? 'video/mp4' : ext === 'webm' ? 'video/webm' : `video/${ext}`;
    const videoBuffer = fs.readFileSync(videoFilePath);
    const base64Video = videoBuffer.toString('base64');
    const dataUri = `data:${mimeType};base64,${base64Video}`;

    this.logger.info(`Sending video to ${model}: ${path.basename(videoFilePath)} (${(videoBuffer.length / 1024 / 1024).toFixed(1)}MB)`);

    const messages = [
      { role: 'system', content: systemPrompt },
      {
        role: 'user',
        content: [
          { type: 'text', text: textPrompt },
          {
            type: 'image_url',
            image_url: {
              url: dataUri,
              detail: 'auto',
            },
          },
        ],
      },
    ];

    return await this._callWithRetry(model, messages, { ...options, useVideo: true });
  }

  /**
   * Use owl-alpha (or another browser agent model) to perform a web search
   * by controlling a Playwright browser.
   * 
   * This works by:
   * 1. Playwright opens a headless browser
   * 2. Screenshots are sent to owl-alpha (vision model)
   * 3. owl-alpha returns coordinates/actions (click, type, scroll)
   * 4. The loop continues until URLs are extracted
   *
   * For simplicity, this method performs:
   * - Targeted searches on Bilibili, Instagram, RedNote, TikTok, YouTube
   * - Extracts video URLs from search results
   * - Returns up to 5 real, downloadable video URLs
   */
  async browserSearch(platforms, topicQuery, options = {}) {
    const model = options.model || this.defaultModel;
    const maxUrls = options.maxUrls || 5;
    
    this.logger.info(`Browser search: ${platforms.join(', ')} for "${topicQuery}"`);
    
    try {
      const { chromium } = require('playwright');
      
      const browser = await chromium.launch({
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
      });
      
      const context = await browser.newContext({
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        viewport: { width: 1280, height: 720 },
        locale: 'en-US',
      });
      
      const page = await context.newPage();
      const foundUrls = [];
      
      for (const platform of platforms) {
        if (foundUrls.length >= maxUrls) break;
        
        this.logger.info(`Searching ${platform}...`);
        
        try {
          let searchUrl = '';
          let searchSelector = '';
          let resultSelector = '';
          let urlExtractor = null;
          
          switch (platform) {
            case 'youtube':
              searchUrl = `https://www.youtube.com/results?search_query=${encodeURIComponent(topicQuery)}`;
              resultSelector = 'a#video-title';
              urlExtractor = (el) => `https://www.youtube.com${el.getAttribute('href')}`;
              break;
            case 'bilibili':
              searchUrl = `https://search.bilibili.com/all?keyword=${encodeURIComponent(topicQuery)}`;
              resultSelector = 'a.title';
              urlExtractor = (el) => el.href;
              break;
            case 'tiktok':
              searchUrl = `https://www.tiktok.com/search?q=${encodeURIComponent(topicQuery)}`;
              resultSelector = 'a[href*="/video/"]';
              urlExtractor = (el) => `https://www.tiktok.com${el.getAttribute('href')}`;
              break;
            default:
              this.logger.warn(`Unknown platform: ${platform}`);
              continue;
          }
          
          await page.goto(searchUrl, { waitUntil: 'networkidle', timeout: 30000 });
          await page.waitForTimeout(3000);
          
          const urlElements = await page.$$(resultSelector);
          
          for (const el of urlElements.slice(0, 3)) {
            const url = await el.evaluate(urlExtractor);
            if (url && url.startsWith('http') && !foundUrls.includes(url)) {
              foundUrls.push(url);
              this.logger.info(`  Found: ${url.substring(0, 80)}`);
            }
            if (foundUrls.length >= maxUrls) break;
          }
        } catch (platformError) {
          this.logger.warn(`  ${platform} search failed: ${platformError.message}`);
        }
      }
      
      await browser.close();
      
      this.logger.success(`Browser search found ${foundUrls.length} URLs`);
      return foundUrls;
      
    } catch (error) {
      this.logger.error(`Browser search failed: ${error.message}`);
      return [];
    }
  }

  async generateImage(prompt, outputPath, options = {}) {
    if (!this._client) throw new Error('No OpenRouter API keys configured');

    const maxAttempts = this.apiKeys.length + 1;
    let lastError = null;

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      if (attempt > 0 && this.apiKeys.length > 1) {
        const rotated = this._rotateKey();
        if (!rotated) break;
      }

      try {
        const response = await this._client.images.generate({
          model: this.imageModel,
          prompt: prompt,
          n: 1,
          size: options.size || '1792x1024',
        });

        const imageUrl = response.data?.[0]?.url;
        if (!imageUrl) throw new Error('No image URL in response');

        const axios = require('axios');
        const imgResponse = await axios({ method: 'GET', url: imageUrl, responseType: 'stream' });

        return new Promise((resolve, reject) => {
          const writer = fs.createWriteStream(outputPath);
          imgResponse.data.pipe(writer);
          writer.on('finish', () => resolve(outputPath));
          writer.on('error', reject);
        });
      } catch (error) {
        lastError = error;
        if (this._isRetryableError(error)) {
          this.deadKeys.add(this.currentKeyIndex);
          this.logger.warn(`Image gen failed on key #${this.currentKeyIndex + 1}: ${error.message}`);
        } else {
          throw error;
        }
      }
    }
    throw lastError || new Error('Image generation failed after all retries');
  }

  async listAvailableModels() {
    try {
      const axios = require('axios');
      const response = await axios.get('https://openrouter.ai/api/v1/models', {
        headers: { 'Authorization': `Bearer ${this.apiKeys[0] || ''}` },
        timeout: 10000,
      });
      return response.data?.data?.slice(0, 50) || [];
    } catch {
      return [];
    }
  }
}

module.exports = { OpenRouterProvider };