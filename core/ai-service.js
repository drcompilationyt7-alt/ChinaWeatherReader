/**
 * Mr. WorldWideWebster — Universal AI Service
 *
 * Supports:
 * 1. OpenRouter (primary)
 * 2. OpenAI (fallback)
 * 3. Edge-TTS (free voiceover)
 *
 * Features:
 * - chatWithVideo(): Send video files to vision models (Nemotron)
 * - webSearch(): Search platforms via HTTP (no browser needed)
 */
const config = require('./config');
const { Logger } = require('./logger');

class AIService {
  constructor() {
    this.logger = new Logger('AIService');
    this.llm = null;
    this.tts = null;
    this.imageGen = null;
    this._initPromise = this._initialize();
  }

  async waitForInit() {
    if (this._initPromise) await this._initPromise;
  }

  async _initialize() {
    if (config.openrouter?.apiKey) {
      const { OpenRouterProvider } = require('../providers/openrouter-provider');
      this.llm = new OpenRouterProvider(config);
      this.imageGen = this.llm;
      this.logger.info('Using OpenRouter provider (200+ models)');
    } else if (config.openai?.apiKey) {
      const OpenAI = require('openai');
      this.llm = new OpenAI({ apiKey: config.openai.apiKey });
      this.imageGen = this.llm;
      this.logger.info('Using OpenAI provider');
    } else {
      this.logger.warn('No AI API keys found — set OPENROUTER_API_KEY');
    }

    // Initialize TTS
    let ttsReady = false;
    try {
      const { EdgeTTSProvider } = require('../providers/edge-tts-provider');
      const ttsProvider = new EdgeTTSProvider();
      await ttsProvider._ensureAvailable();
      if (ttsProvider.isAvailable()) {
        this.tts = ttsProvider;
        ttsReady = true;
        this.logger.info('TTS: using Edge-TTS (free, no API key)');
      }
    } catch (ttsError) {
      this.logger.warn(`Edge-TTS init: ${ttsError.message}`);
    }
    if (!ttsReady && this.llm?.audio?.speech) {
      this.tts = this.llm;
      ttsReady = true;
    }
    if (!ttsReady) this.logger.warn('No TTS provider configured');
  }

  isAvailable() { return !!this.llm; }

  async chat(systemPrompt, userMessage, options = {}) {
    if (!this.llm) throw new Error('No AI provider.');
    if (this.llm.constructor.name === 'OpenRouterProvider') {
      return await this.llm.chat(systemPrompt, userMessage, options);
    }
    const response = await this.llm.chat.completions.create({
      model: options.useScriptModel ? config.openai.scriptModel : config.openai.model,
      messages: [{ role: 'system', content: systemPrompt }, { role: 'user', content: userMessage }],
      max_tokens: options.maxTokens || 2000,
      temperature: options.temperature || 0.7,
    });
    return response.choices[0].message.content;
  }

  async chatJSON(systemPrompt, userMessage, options = {}) {
    if (this.llm.constructor.name === 'OpenRouterProvider') {
      return await this.llm.chatJSON(systemPrompt, userMessage, options);
    }
    const result = await this.chat(
      systemPrompt + '\n\nRespond ONLY with valid JSON.', userMessage,
      { ...options, responseFormat: { type: 'json_object' } }
    );
    return JSON.parse(result.replace(/```json/g, '').replace(/```/g, '').trim());
  }

  /**
   * Send a video to a vision model (Nemotron) via OpenRouter
   */
  async chatWithVideo(systemPrompt, videoFilePath, textPrompt, options = {}) {
    if (!this.llm) throw new Error('No AI provider.');
    if (this.llm.constructor.name === 'OpenRouterProvider') {
      return await this.llm.chatWithVideo(systemPrompt, videoFilePath, textPrompt, options);
    }
    throw new Error('chatWithVideo requires OpenRouter');
  }

  /**
   * Search for video URLs across platforms via HTTP (no browser)
   */
  async webSearch(platforms, topicQuery, options = {}) {
    if (this.llm.constructor.name === 'OpenRouterProvider') {
      return await this.llm.webSearch(platforms, topicQuery, options);
    }
    return [];
  }

  async translate(text) {
    if (!this.llm) return text;
    return await this.chat(
      `Translate to natural English. Preserve slang, humor, and cultural context.`,
      text, { temperature: 0.3, useCheapModel: true }
    );
  }

  async textToSpeech(text, outputPath, options = {}) {
    if (this.tts && typeof this.tts.textToSpeech === 'function') {
      try { return await this.tts.textToSpeech(text, outputPath, options); } catch {}
    }
    if (this.tts?.audio?.speech) {
      const response = await this.tts.audio.speech.create({
        model: 'tts-1-hd', voice: options.voice || 'nova', input: text,
      });
      fs.writeFileSync(outputPath, Buffer.from(await response.arrayBuffer()));
      return outputPath;
    }
    return null;
  }

  async generateImage(prompt, outputPath, options = {}) {
    if (!this.imageGen) throw new Error('No image generation provider');
    return await this.imageGen.generateImage(prompt, outputPath, options);
  }

  async transcribe(audioFilePath, language = null) {
    if (!config.openai?.apiKey) throw new Error('Whisper requires OPENAI_API_KEY');
    const OpenAI = require('openai');
    const openai = new OpenAI({ apiKey: config.openai.apiKey });
    return await openai.audio.transcriptions.create({
      model: 'whisper-1', file: require('fs').createReadStream(audioFilePath),
      response_format: 'verbose_json', language,
    });
  }
}

module.exports = { AIService };