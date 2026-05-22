/**
 * Mr. WorldWideWebster — OpenRouter Provider (Official SDK)
 *
 * Uses the official @openrouter/sdk for all AI interactions.
 * This is the brain of the Hermes agent — powers decisions,
 * script writing, translations, and all LLM operations.
 *
 * The SDK provides:
 * - Streaming support (real-time responses)
 * - Official OpenRouter API compatibility
 * - Automatic model selection and routing
 * - Built-in error handling
 *
 * Installation:
 *   npm install @openrouter/sdk
 *
 * Docs: https://openrouter.ai/docs
 */
const { OpenRouter } = require('@openrouter/sdk');
const axios = require('axios');
const { Logger } = require('../core/logger');

class OpenRouterProvider {
  constructor(config) {
    this.logger = new Logger('OpenRouter');
    this.config = config;
    this.client = null;
    this.apiKey = config.openrouter?.apiKey || process.env.OPENROUTER_API_KEY;

    if (this.apiKey) {
      this.client = new OpenRouter({
        apiKey: this.apiKey,
        // Identify the app to OpenRouter for analytics
        defaultHeaders: {
          'HTTP-Referer': 'https://github.com/mr-worldwidewebster',
          'X-Title': 'Mr. WorldWideWebster',
        },
      });
      this.logger.info('OpenRouter SDK initialized');
    } else {
      this.logger.warn('No OpenRouter API key — set OPENROUTER_API_KEY in .env');
    }
  }

  isAvailable() {
    return !!this.client;
  }

  /**
   * Selects the best model for the task
   */
  _getDefaultModel(options) {
    if (options.useScriptModel) {
      return this.config.openrouter?.scriptModel || 'openrouter/owl-alpha';
    }
    if (options.useCheapModel) {
      return 'openrouter/owl-alpha';
    }
    if (options.useAgentModel) {
      return this.config.openrouter?.agentModel || 'openrouter/owl-alpha';
    }
    return this.config.openrouter?.defaultModel || 'openrouter/owl-alpha';
  }

  /**
   * Chat completion via OpenRouter SDK
   * Uses the official streaming API under the hood
   *
   * @param {string} systemPrompt
   * @param {string} userMessage
   * @param {Object} options — { model, temperature, maxTokens }
   * @returns {Promise<string>}
   */
  async chat(systemPrompt, userMessage, options = {}) {
    const model = options.model || this._getDefaultModel(options);

    try {
      const response = await this.client.chat.send({
        model: model,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userMessage }
        ],
        max_tokens: options.maxTokens || 2000,
        temperature: options.temperature ?? 0.7,
      });

      // Handle streaming and non-streaming responses
      if (response && response.choices) {
        return response.choices[0]?.message?.content || '';
      }

      // If it's a stream, collect all chunks
      if (response && response[Symbol.asyncIterator]) {
        let fullContent = '';
        for await (const chunk of response) {
          const content = chunk.choices?.[0]?.delta?.content;
          if (content) fullContent += content;
        }
        return fullContent;
      }

      // Fallback: handle raw response
      if (typeof response === 'string') return response;
      return JSON.stringify(response);

    } catch (error) {
      this.logger.error(`OpenRouter SDK chat failed (${model}): ${error.message}`);
      throw error;
    }
  }

  /**
   * Stream a chat response (for real-time display)
   */
  async chatStream(systemPrompt, userMessage, onChunk, options = {}) {
    const model = options.model || this._getDefaultModel(options);

    try {
      const stream = await this.client.chat.send({
        model: model,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userMessage }
        ],
        max_tokens: options.maxTokens || 2000,
        temperature: options.temperature ?? 0.7,
        stream: true,
      });

      let fullContent = '';
      for await (const chunk of stream) {
        const content = chunk.choices?.[0]?.delta?.content;
        if (content) {
          fullContent += content;
          if (onChunk) onChunk(content);
        }
      }
      return fullContent;
    } catch (error) {
      this.logger.error(`OpenRouter SDK stream failed (${model}): ${error.message}`);
      throw error;
    }
  }

  /**
   * Get JSON response from OpenRouter SDK
   * Forces JSON mode for structured data extraction
   */
  async chatJSON(systemPrompt, userMessage, options = {}) {
    const model = options.model || this._getDefaultModel(options);
    const enhancedPrompt = systemPrompt + '\n\nRespond ONLY with valid JSON. No markdown, no explanation.';

    try {
      const response = await this.client.chat.send({
        model: model,
        messages: [
          { role: 'system', content: enhancedPrompt },
          { role: 'user', content: userMessage }
        ],
        max_tokens: options.maxTokens || 2000,
        temperature: options.temperature ?? 0.4,
      });

      // Extract content from response
      let raw = '';
      if (response?.choices) {
        raw = response.choices[0]?.message?.content || '';
      } else if (response[Symbol.asyncIterator]) {
        for await (const chunk of response) {
          const content = chunk.choices?.[0]?.delta?.content;
          if (content) raw += content;
        }
      } else {
        raw = String(response);
      }

      // Clean potential markdown fences
      const cleaned = raw.replace(/```json/g, '').replace(/```/g, '').trim();
      return JSON.parse(cleaned);
    } catch (error) {
      this.logger.error(`OpenRouter SDK JSON failed (${model}): ${error.message}`);
      throw error;
    }
  }

  /**
   * Generate images via OpenRouter compatible models
   * OpenRouter supports: Flux Pro, Stable Diffusion 3
   */
  async generateImage(prompt, outputPath, options = {}) {
    const model = options.model || 'black-forest-labs/flux-pro';
    const size = options.size || '1024x1024';

    try {
      const response = await this.client.images?.generate?.({
        model: model,
        prompt: prompt,
        n: 1,
        size: size,
      }) || await this._fallbackGenerateImage(prompt, outputPath, model, size);

      const imageUrl = response.data?.[0]?.url || response.url;
      if (!imageUrl) {
        throw new Error('No image URL in response');
      }

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
   * Fallback image generation using raw OpenRouter API
   */
  async _fallbackGenerateImage(prompt, outputPath, model, size) {
    const response = await axios.post(
      'https://openrouter.ai/api/v1/images/generations',
      { model, prompt, n: 1, size },
      { headers: { 'Authorization': `Bearer ${this.apiKey}`, 'Content-Type': 'application/json' } }
    );
    return response.data;
  }

  /**
   * List available models from OpenRouter
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