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
const TIMEOUT = 3 * 60 * 60 * 1000; // 3 hours

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
    // Cap rotation so we don't cycle forever with 1 model
    const models = this.models || FALLBACK_MODELS;
    if (models.length > 1 && prev !== next) {
      logger.info(`Ollama: rotating model ${prev} → ${next}`);
      return true;
    }
    return false;
  }

  /**
   * Reset back to the first model (e.g. after a successful call or timeout recovery)
   */
  _resetModelIndex() {
    this.currentModelIndex = 0;
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
    let consecutiveTimeout = 0;

    for (let attempt = 0; attempt < maxRetries; attempt++) {
      const currentModel = this.model;
      // First attempt uses full 3hr timeout; subsequent retries use TIMEOUT minus 20min per attempt
      const attemptTimeout = TIMEOUT - (attempt * 20 * 60 * 1000);

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

        logger.info(`Ollama ${currentModel}: generating response (attempt ${attempt + 1}/${maxRetries}, timeout ${attemptTimeout}ms)...`);
        const resp = await axios.post(`${OLLAMA_BASE}/api/chat`, body, {
          timeout: attemptTimeout,
          headers: { 'Content-Type': 'application/json' },
        });

        const content = resp.data?.message?.content;
        if (content && content.trim().length > 0) {
          logger.success(`Ollama ${currentModel}: generated ${content.length} chars`);
          // Reset model index on success so next call starts with primary model
          this._resetModelIndex();
          return content.trim();
        }

        logger.warn(`Ollama ${currentModel}: empty response`);
        lastError = new Error('Empty response');
        consecutiveTimeout = 0;
      } catch (e) {
        lastError = e;
        const errText = e.response?.data?.error || e.message || '';
        const isTimeout = errText.includes('timeout') || errText.includes('TIMEOUT') || errText.includes('ETIMEDOUT') || errText.includes('ECONNABORTED');
        const isOOM = errText.includes('out of memory') || errText.includes('OOM') || errText.includes('CUDA out of memory');

        if (isTimeout) {
          consecutiveTimeout++;
          logger.warn(`Ollama ${currentModel}: timeout (${consecutiveTimeout}x in a row)`);
        } else if (isOOM) {
          logger.warn(`Ollama ${currentModel}: out of memory — marking unavailable`);
          this.available = false;
          logger.error(`Ollama: ${currentModel} OOM — disabling Ollama fallback`);
          return null;
        } else {
          logger.warn(`Ollama ${currentModel} error: ${errText.substring(0, 100)}`);
          consecutiveTimeout = 0;
        }

        // Determine if we need to rotate model
        let shouldRotate = true;

        // If we have multiple timeouts on the same model, skip to next model
        if (consecutiveTimeout >= 2) {
          logger.warn(`Ollama ${currentModel}: ${consecutiveTimeout} consecutive timeouts — rotating model`);
          consecutiveTimeout = 0;
        } else if (isTimeout) {
          // Single timeout — don't rotate, just retry with same model
          shouldRotate = false;
        }

        // Try next model/attempt
        if (attempt < maxRetries - 1) {
          if (shouldRotate) {
            if (this._rotateModel()) {
              consecutiveTimeout = 0; // Reset timeout counter on model change
            }
          }
          const cooldown = isTimeout ? 10000 : 2000; // Longer cooldown after timeout
          logger.info(`Ollama: retrying with ${this.model}... (cooldown ${cooldown}ms)`);
          await new Promise(r => setTimeout(r, cooldown));
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