/**
 * Ollama Provider — Local LLM Fallback for Mr. WorldWideWebster
 * 
 * Runs Gemma 4 (or any Ollama model) locally via Ollama's REST API.
 * Used as a LAST-RESORT fallback for metadata generation (title/description)
 * when Gemini API and OpenRouter both fail.
 * 
 * Ollama runs on localhost:11434 by default.
 * Model: gemma4:latest (or fallback to llama3.2, phi4, etc.)
 * 
 * No API key needed — runs entirely local.
 */
const axios = require('axios');
const { Logger } = require('../core/logger');

const logger = new Logger('OllamaProvider');

const OLLAMA_BASE = process.env.OLLAMA_BASE_URL || 'http://127.0.0.1:11434';
const FALLBACK_MODELS = ['gemma4:latest', 'gemma4', 'llama3.2:latest', 'llama3.2', 'phi4:latest', 'phi4', 'mistral:latest', 'mistral'];
const TIMEOUT = 120000;

class OllamaProvider {
  constructor() {
    this.available = false;
    this.currentModelIndex = 0;
    this.models = null;
  }

  get model() {
    const models = this.models || FALLBACK_MODELS;
    return models[this.currentModelIndex % models.length];
  }

  _rotateModel() {
    const prev = this.model;
    this.currentModelIndex++;
    const next = this.model;
    if (prev !== next) {
      logger.info(`Ollama: rotating model ${prev} → ${next}`);
    }
    return prev !== next;
  }

  /**
   * Check if Ollama is running and has compatible models.
   * Lists available models and picks the best one.
   */
  async checkAvailability() {
    try {
      const resp = await axios.get(`${OLLAMA_BASE}/api/tags`, { timeout: 5000 });
      const models = resp.data?.models || [];
      if (models.length === 0) {
        logger.warn('Ollama running but no models found — pull gemma4 or llama3.2');
        this.available = false;
        return false;
      }

      // Prefer models in order: gemma4, llama3.2, phi4, mistral, or any available
      const modelNames = models.map(m => m.name);
      logger.info(`Ollama available with models: ${modelNames.join(', ')}`);

      // Reorder available models to prioritize best for metadata
      const prioritized = [];
      for (const preferred of FALLBACK_MODELS) {
        const found = modelNames.find(n => n.startsWith(preferred));
        if (found) prioritized.push(found);
      }
      // Add any remaining models not already prioritized
      for (const n of modelNames) {
        if (!prioritized.includes(n)) prioritized.push(n);
      }

      this.models = prioritized.length > 0 ? prioritized : FALLBACK_MODELS;
      this.available = true;
      logger.success(`Ollama provider ready: primary model "${this.model}"`);
      return true;
    } catch (e) {
      logger.warn(`Ollama not available: ${e.message}`);
      this.available = false;
      return false;
    }
  }

  isAvailable() { return this.available; }

  /**
   * Simple text generation (no vision).
   * Used for metadata generation: titles, descriptions, tags.
   */
  async generate(systemPrompt, userMessage, options = {}) {
    if (!this.available) return null;

    const maxRetries = Math.min(this.models?.length || 1, 3) + 1;
    let lastError = null;

    for (let attempt = 0; attempt < maxRetries; attempt++) {
      const currentModel = this.model;
      try {
        const body = {
          model: currentModel,
          messages: [
            { role: 'system', content: systemPrompt },
            { role: 'user', content: userMessage },
          ],
          stream: false,
          options: {
            temperature: options.temperature || 0.7,
            num_predict: options.maxTokens || 1024,
            top_p: options.topP || 0.9,
          },
        };

        logger.info(`Ollama ${currentModel}: generating response...`);
        const resp = await axios.post(`${OLLAMA_BASE}/api/chat`, body, {
          timeout: TIMEOUT,
          headers: { 'Content-Type': 'application/json' },
        });

        const content = resp.data?.message?.content;
        if (content && content.trim().length > 0) {
          logger.success(`Ollama ${currentModel}: generated ${content.length} chars`);
          return content.trim();
        }

        logger.warn(`Ollama ${currentModel}: empty response`);
        lastError = new Error('Empty response');
      } catch (e) {
        lastError = e;
        const errText = e.response?.data?.error || e.message || '';
        logger.warn(`Ollama ${currentModel} error: ${errText.substring(0, 100)}`);

        // Try next model
        if (attempt < maxRetries - 1) {
          this._rotateModel();
          logger.info(`Ollama: retrying with ${this.model}...`);
          await new Promise(r => setTimeout(r, 2000));
        }
      }
    }

    logger.error(`Ollama: all models exhausted — ${(lastError?.message || '').substring(0, 80)}`);
    return null;
  }

  /**
   * Generate JSON response (for title/description/tags metadata).
   * Tells the model to respond with valid JSON.
   */
  async generateJSON(systemPrompt, userMessage, options = {}) {
    const strictPrompt = systemPrompt + '\n\nIMPORTANT: Respond ONLY with valid JSON. No markdown, no code fences, no explanation.';
    const result = await this.generate(strictPrompt, userMessage, options);
    if (!result) return null;

    try {
      // Try direct parse first
      return JSON.parse(result);
    } catch {
      // Try to extract JSON from markdown/backticks
      const m = result.match(/\{[\s\S]*\}/);
      if (m) {
        try { return JSON.parse(m[0]); } catch {}
      }
      const a = result.match(/\[[\s\S]*\]/);
      if (a) {
        try { return JSON.parse(a[0]); } catch {}
      }
      logger.warn(`Ollama JSON parse failed: ${result.substring(0, 120)}`);
      return null;
    }
  }
}

let instance = null;
function getOllamaProvider() {
  if (!instance) {
    instance = new OllamaProvider();
    // Async init — caller should await checkAvailability() before using
  }
  return instance;
}

module.exports = { OllamaProvider, getOllamaProvider };