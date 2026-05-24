/**
 * Mr. WorldWideWebster — Universal AI Service
 * 
 * Supports multiple backends automatically:
 * 1. OpenRouter (primary) — 200+ models, cheap, no download needed
 * 2. OpenAI (fallback) — if no OpenRouter key is set
 * 3. Edge-TTS — completely free voiceover (Windows)
 * 
 * NEW: chatWithVideo() sends video files to vision models (Nemotron)
 * NEW: browserSearch() controls Playwright via AI (owl-alpha)
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
      this.logger.warn('No AI API keys found — set OPENROUTER_API_KEY or OPENAI_API_KEY');
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
      this.logger.info('TTS: using OpenAI TTS');
    }
    if (!ttsReady) this.logger.warn('No TTS provider configured');
  }

  isAvailable() { return !!this.llm; }

  // ════════════════════════════════════════
  //  LLM Methods
  // ════════════════════════════════════════

  async chat(systemPrompt, userMessage, options = {}) {
    if (!this.llm) throw new Error('No AI provider configured.');
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
    const response = await this.chat(
      systemPrompt + '\n\nRespond ONLY with valid JSON.', userMessage,
      { ...options, responseFormat: { type: 'json_object' } }
    );
    const cleaned = response.replace(/```json/g, '').replace(/```/g, '').trim();
    return JSON.parse(cleaned);
  }

  /**
   * Send a video file to a vision model for analysis (e.g. Nemotron)
   * Uses the OpenRouter provider's chatWithVideo method.
   */
  async chatWithVideo(systemPrompt, videoFilePath, textPrompt, options = {}) {
    if (!this.llm) throw new Error('No AI provider configured.');
    if (this.llm.constructor.name === 'OpenRouterProvider') {
      return await this.llm.chatWithVideo(systemPrompt, videoFilePath, textPrompt, options);
    }
    throw new Error('chatWithVideo requires OpenRouter provider');
  }

  /**
   * Use browser-based search via Playwright + AI (owl-alpha)
   * Searches specified platforms for video content matching a topic.
   */
  async browserSearch(platforms, topicQuery, options = {}) {
    if (this.llm.constructor.name === 'OpenRouterProvider') {
      return await this.llm.browserSearch(platforms, topicQuery, options);
    }
    throw new Error('browserSearch requires OpenRouter provider');
  }

  async translate(text, sourceLanguage = 'auto') {
    if (!this.llm) return text;
    return await this.chat(
      `You are a professional translator for Mr. WorldWideWebster.
Rules:
- Translate to natural, fluent English
- Preserve slang, humor, and cultural context — add brief [explanations] in brackets if needed
- Keep the tone and energy of the original`,
      text, { temperature: 0.3, useCheapModel: true }
    );
  }

  // ════════════════════════════════════════
  //  TTS Methods
  // ════════════════════════════════════════

  async textToSpeech(text, outputPath, options = {}) {
    if (this.tts && typeof this.tts.textToSpeech === 'function') {
      try { return await this.tts.textToSpeech(text, outputPath, options); }
      catch (e) { this.logger.warn(`TTS failed: ${e.message}`); }
    }
    if (this.tts?.audio?.speech) {
      const response = await this.tts.audio.speech.create({
        model: 'tts-1-hd', voice: options.voice || 'nova', input: text,
      });
      const buffer = Buffer.from(await response.arrayBuffer());
      require('fs').writeFileSync(outputPath, buffer);
      return outputPath;
    }
    require('fs').writeFileSync(outputPath + '.txt', text);
    return outputPath + '.txt';
  }

  async generateDialogAudio(scenes, outputDir) {
    if (this.tts?.constructor?.name === 'EdgeTTSProvider') {
      return await this.tts.generateDialogAudio(scenes, outputDir);
    }
    const files = [];
    for (const scene of scenes) {
      const outputFile = require('path').join(outputDir, `scene_${scene.sceneNumber}.mp3`);
      await this.textToSpeech(scene.dialogue, outputFile, { voice: 'nova' });
      files.push({ scene: scene.sceneNumber, file: outputFile, dialogue: scene.dialogue });
    }
    return files;
  }

  // ════════════════════════════════════════
  //  Image Generation / Transcription
  // ════════════════════════════════════════

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

  async listAvailableModels() {
    if (this.llm?.constructor?.name === 'OpenRouterProvider') {
      return await this.llm.listAvailableModels();
    }
    return [];
  }
}

module.exports = { AIService };