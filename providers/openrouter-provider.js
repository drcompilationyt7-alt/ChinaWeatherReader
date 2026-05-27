/**
 * Mr. WorldWideWebster — OpenRouter Provider with Multi-Key Rotation (up to 8 keys)
 */
const OpenAI = require('openai');
const fs = require('fs');
const path = require('path');
const { Logger } = require('../core/logger');

class OpenRouterProvider {
  constructor(config) {
    this.logger = new Logger('OpenRouter');

    this.apiKeys = this._collectApiKeys(config);
    this.currentKeyIndex = 0;

    this.defaultModel = config.openrouter?.defaultModel || 'openrouter/owl-alpha';
    this.fallbackModel = 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free';
    this.videoModel = 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free';
    this.scriptModel = config.openrouter?.scriptModel || this.defaultModel;
    this.agentModel = config.openrouter?.agentModel || this.defaultModel;
    this.imageModel = config.openrouter?.imageModel || 'black-forest-labs/flux-schnell';

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
    // Use highest keys first (8,7,6...) since lower keys are shared across projects
    for (let i = 8; i >= 2; i--) {
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
        // Map internal index to original env key number for logging
        const keyNum = nextIndex === 0 ? 1 : (8 - nextIndex + 2);
        this.logger.info(`Rotated to API key #${keyNum}`);
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
    const tryModels = [model, this.fallbackModel, model, 'openai/gpt-4o-mini'];

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
       options.useCheapModel ? this.fallbackModel : this.defaultModel);
    const messages = [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: userMessage },
    ];
    return await this._callWithRetry(model, messages, options);
  }

  async chatJSON(systemPrompt, userMessage, options = {}) {
    const strictPrompt = systemPrompt + '\n\nRespond ONLY with valid JSON. No markdown, no explanation, no code blocks.';
    const result = await this.chat(strictPrompt, userMessage, {
      ...options, responseFormat: { type: 'json_object' },
    });
    const cleaned = result.replace(/```json\s*/gi, '').replace(/```\s*/g, '').trim();
    try { return JSON.parse(cleaned); } catch {
      const jsonMatch = cleaned.match(/\{[\s\S]*\}/);
      if (jsonMatch) return JSON.parse(jsonMatch[0]);
      throw new Error(`Failed to parse JSON: ${cleaned.substring(0, 200)}`);
    }
  }

  async chatWithVideo(systemPrompt, videoFilePath, textPrompt, options = {}) {
    if (!this._client) throw new Error('No OpenRouter API keys configured.');
    if (!fs.existsSync(videoFilePath)) throw new Error(`Video file not found: ${videoFilePath}`);
    const model = options.model || this.videoModel;
    const ext = path.extname(videoFilePath).toLowerCase().replace('.', '');
    const mimeType = ext === 'mp4' ? 'video/mp4' : ext === 'webm' ? 'video/webm' : `video/${ext}`;
    const videoBuffer = fs.readFileSync(videoFilePath);
    const dataUri = `data:${mimeType};base64,${videoBuffer.toString('base64')}`;
    this.logger.info(`Sending video to ${model}: ${path.basename(videoFilePath)} (${(videoBuffer.length / 1024 / 1024).toFixed(1)}MB)`);
    const messages = [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: [{ type: 'text', text: textPrompt }, { type: 'image_url', image_url: { url: dataUri, detail: 'auto' } }] },
    ];
    return await this._callWithRetry(model, messages, { ...options, useVideo: true });
  }

  async webSearch(platforms, topicQuery, options = {}) {
    const maxUrls = options.maxUrls || 5;
    this.logger.info(`Web search: ${platforms.join(', ')} for "${topicQuery}"`);
    const axios = require('axios');
    const foundUrls = [];
    const headers = {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'Accept-Language': 'en-US,en;q=0.5',
    };
    for (const platform of platforms) {
      if (foundUrls.length >= maxUrls) break;
      try {
        switch (platform) {
          case 'youtube': {
            const html = (await axios.get(`https://www.youtube.com/results?search_query=${encodeURIComponent(topicQuery)}`, { headers, timeout: 15000 })).data;
            for (const id of [...html.matchAll(/"videoId":\s*"([a-zA-Z0-9_-]{11})"/g)].map(m => m[1]).slice(0, 3)) {
              const url = `https://www.youtube.com/watch?v=${id}`;
              if (!foundUrls.includes(url)) foundUrls.push(url);
            }
            break;
          }
          case 'bilibili': {
            const html = (await axios.get(`https://search.bilibili.com/all?keyword=${encodeURIComponent(topicQuery)}`, { headers, timeout: 15000 })).data;
            for (const bv of [...html.matchAll(/"bvid":\s*"(BV[a-zA-Z0-9]+)"/g)].map(m => m[1]).slice(0, 2)) {
              const url = `https://www.bilibili.com/video/${bv}`;
              if (!foundUrls.includes(url)) foundUrls.push(url);
            }
            break;
          }
          case 'tiktok': {
            const html = (await axios.get(`https://www.tiktok.com/search?q=${encodeURIComponent(topicQuery)}`, { headers, timeout: 15000 })).data;
            for (const url of [...html.matchAll(/https?:\/\/[^"'\s]+tiktok[^"'\s]*\/video\/\d+/gi)].map(m => m[0]).slice(0, 2)) {
              if (!foundUrls.includes(url)) foundUrls.push(url);
            }
            break;
          }
        }
      } catch {}
    }
    return foundUrls;
  }

  async generateImage(prompt, outputPath, options = {}) {
    if (!this._client) throw new Error('No OpenRouter API keys configured');
    const maxAttempts = this.apiKeys.length + 1;
    let lastError = null;
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      if (attempt > 0 && this.apiKeys.length > 1) {
        if (!this._rotateKey()) break;
      }
      try {
        const response = await this._client.images.generate({ model: this.imageModel, prompt, n: 1, size: options.size || '1792x1024' });
        const imageUrl = response.data?.[0]?.url;
        if (!imageUrl) throw new Error('No image URL');
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
        if (this._isRetryableError(error)) this.deadKeys.add(this.currentKeyIndex);
        else throw error;
      }
    }
    throw lastError || new Error('Image gen failed');
  }
}

module.exports = { OpenRouterProvider };