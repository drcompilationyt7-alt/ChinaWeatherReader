/**
 * Mr. WorldWideWebster — OpenRouter Provider
 * 
 * Gives access to 200+ models through a single API.
 * Uses the same OpenAI-compatible format, just with a different baseURL.
 * 
 * Key models for Mr. WorldWideWebster:
 * - nousresearch/hermes-3-70b (agent/tool use) — ~$0.90/1M tokens
 * - qwen/qwen-2.5-72b (Chinese→English translation) — ~$0.35/1M tokens
 * - mistralai/mistral-7b (fast, cheap scripts) — ~$0.07/1M tokens
 * - deepseek/deepseek-chat (cheap GPT-4 competitor) — ~$0.50/1M tokens
 * - meta-llama/llama-3.1-8b (free tier) — $0
 * - black-forest-labs/flux-pro (image gen) — ~$0.05/image
 * - stability-ai/stable-diffusion-3 (image gen) — ~$0.002/image
 */
const OpenAI = require('openai');
const axios = require('axios');
const { Logger } = require('../core/logger');

class OpenRouterProvider {
  constructor(config) {
    this.logger = new Logger('OpenRouter');
    this.config = config;
    this.client = null;
    this.apiKey = config.openrouter?.apiKey || process.env.OPENROUTER_API_KEY;
    
    if (this.apiKey) {
      this.client = new OpenAI({
        baseURL: 'https://openrouter.ai/api/v1',
        apiKey: this.apiKey,
        defaultHeaders: {
          'HTTP-Referer': 'https://github.com/mr-worldwidewebster',
          'X-Title': 'Mr. WorldWideWebster',
        },
      });
      this.logger.info('OpenRouter initialized');
    } else {
      this.logger.warn('No OpenRouter API key — set OPENROUTER_API_KEY in .env');
    }
  }

  isAvailable() {
    return !!this.client;
  }

  /**
   * Chat completion via OpenRouter
   * @param {string} systemPrompt
   * @param {string} userMessage
   * @param {Object} options — { model, temperature, maxTokens, responseFormat }
   */
  async chat(systemPrompt, userMessage, options = {}) {
    const model = options.model || this._getDefaultModel(options);

    try {
      const response = await this.client.chat.completions.create({
        model: model,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userMessage }
        ],
        max_tokens: options.maxTokens || 2000,
        temperature: options.temperature ?? 0.7,
      });

      return response.choices[0].message.content;
    } catch (error) {
      this.logger.error(`OpenRouter chat failed (${model}): ${error.message}`);
      throw error;
    }
  }

  /**
   * Get JSON response from OpenRouter
   */
  async chatJSON(systemPrompt, userMessage, options = {}) {
    const model = options.model || this._getDefaultModel(options);
    const enhancedPrompt = systemPrompt + '\n\nRespond ONLY with valid JSON. No markdown, no explanation.';

    try {
      const response = await this.client.chat.completions.create({
        model: model,
        messages: [
          { role: 'system', content: enhancedPrompt },
          { role: 'user', content: userMessage }
        ],
        max_tokens: options.maxTokens || 2000,
        temperature: options.temperature ?? 0.4,
      });

      const raw = response.choices[0].message.content;
      // Clean potential markdown fences
      const cleaned = raw.replace(/```json/g, '').replace(/```/g, '').trim();
      return JSON.parse(cleaned);
    } catch (error) {
      this.logger.error(`OpenRouter JSON failed (${model}): ${error.message}`);
      throw error;
    }
  }

  /**
   * Selects the best model for the task
   */
  _getDefaultModel(options) {
    // User-specified model override
    if (options.useScriptModel) {
      return this.config.openrouter?.scriptModel || 'openrouter/owl-alpha';
    }
    if (options.useCheapModel) {
      return 'openrouter/owl-alpha';
    }
    if (options.useAgentModel) {
      return this.config.openrouter?.agentModel || 'openrouter/owl-alpha';
    }
    // Default: openrouter/owl-alpha is FREE
    return this.config.openrouter?.defaultModel || 'openrouter/owl-alpha';
  }

  /**
   * Generate images via OpenRouter compatible models
   * OpenRouter supports: Flux Pro, Stable Diffusion 3
   */
  async generateImage(prompt, outputPath, options = {}) {
    const model = options.model || 'black-forest-labs/flux-pro';
    const size = options.size || '1024x1024';

    try {
      const response = await this.client.images.generate({
        model: model,
        prompt: prompt,
        n: 1,
        size: size,
      });

      const imageUrl = response.data[0].url;
      const fs = require('fs');
      const imgResponse = await axios({ method: 'GET', url: imageUrl, responseType: 'stream' });
      const writer = fs.createWriteStream(outputPath);
      imgResponse.data.pipe(writer);

      return new Promise((resolve, reject) => {
        writer.on('finish', () => resolve(outputPath));
        writer.on('error', reject);
      });
    } catch (error) {
      this.logger.error(`Image generation failed (${model}): ${error.message}`);
      throw error;
    }
  }

  /**
   * List available free/cheap models from OpenRouter
   */
  async listAvailableModels() {
    try {
      const response = await axios.get('https://openrouter.ai/api/v1/models', {
        headers: { 'Authorization': `Bearer ${this.apiKey}` }
      });
      return response.data.data
        .filter(m => m.pricing?.prompt === '0' || parseFloat(m.pricing?.prompt || '999') < 0.001)
        .map(m => ({ id: m.id, name: m.name, cost: m.pricing }));
    } catch (error) {
      this.logger.error(`Failed to list models: ${error.message}`);
      return [];
    }
  }
}

module.exports = { OpenRouterProvider };