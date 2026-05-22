/**
 * Mr. WorldWideWebster — Universal AI Service
 * 
 * Supports multiple backends automatically:
 * 1. OpenRouter (primary) — 200+ models, cheap, no download needed
 * 2. OpenAI (fallback) — if no OpenRouter key is set
 * 3. Edge-TTS — completely free voiceover (Windows)
 * 
 * The system auto-selects the best available provider.
 */
const config = require('./config');
const { Logger } = require('./logger');

class AIService {
  constructor() {
    this.logger = new Logger('AIService');
    this.llm = null;      // Language model provider
    this.tts = null;      // Text-to-speech provider
    this.imageGen = null; // Image generation provider
    this._initialize();
  }

  async _initialize() {
    // ─── Step 1: Initialize LLM ───────────────────────────────────────
    if (config.openrouter?.apiKey) {
      const { OpenRouterProvider } = require('../providers/openrouter-provider');
      this.llm = new OpenRouterProvider(config);
      this.imageGen = this.llm; // OpenRouter also handles images
      this.logger.info('Using OpenRouter provider (200+ models)');
    } else if (config.openai?.apiKey) {
      const OpenAI = require('openai');
      this.llm = new OpenAI({ apiKey: config.openai.apiKey });
      this.imageGen = this.llm;
      this.logger.info('Using OpenAI provider');
    } else {
      this.logger.warn('No AI API keys found — set OPENROUTER_API_KEY or OPENAI_API_KEY');
    }

    // ─── Step 2: Initialize TTS ──────────────────────────────────────
    if (config.tts.provider === 'edge' || !config.tts.provider) {
      const { EdgeTTSProvider } = require('../providers/edge-tts-provider');
      this.tts = new EdgeTTSProvider();
      this.logger.info('Using Edge-TTS (free)');
    } else {
      // Fallback to OpenAI TTS
      if (this.llm?.audio?.speech) {
        this.tts = this.llm;
        this.logger.info('Using OpenAI TTS');
      }
    }
  }

  isAvailable() {
    return !!this.llm;
  }

  // ═══════════════════════════════════════════════════════
  //  LLM Methods
  // ═══════════════════════════════════════════════════════

  /**
   * Send a chat completion
   */
  async chat(systemPrompt, userMessage, options = {}) {
    if (!this.llm) {
      throw new Error('No AI provider configured. Set OPENROUTER_API_KEY in .env');
    }

    if (this.llm.constructor.name === 'OpenRouterProvider') {
      return await this.llm.chat(systemPrompt, userMessage, options);
    }

    // OpenAI fallback
    const response = await this.llm.chat.completions.create({
      model: options.useScriptModel ? config.openai.scriptModel : config.openai.model,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userMessage }
      ],
      max_tokens: options.maxTokens || 2000,
      temperature: options.temperature || 0.7,
    });
    return response.choices[0].message.content;
  }

  /**
   * Get JSON response
   */
  async chatJSON(systemPrompt, userMessage, options = {}) {
    if (!this.llm) {
      throw new Error('No AI provider configured. Set OPENROUTER_API_KEY in .env');
    }

    if (this.llm.constructor.name === 'OpenRouterProvider') {
      return await this.llm.chatJSON(systemPrompt, userMessage, options);
    }

    // OpenAI fallback
    const response = await this.chat(
      systemPrompt + '\n\nRespond ONLY with valid JSON. No markdown, no explanation.',
      userMessage,
      { ...options, responseFormat: { type: 'json_object' } }
    );
    const cleaned = response.replace(/```json/g, '').replace(/```/g, '').trim();
    return JSON.parse(cleaned);
  }

  /**
   * Translate text
   */
  async translate(text, sourceLanguage = 'auto') {
    if (!this.llm) return text;

    const systemPrompt = `You are a professional translator for Mr. WorldWideWebster.
Rules:
- Translate to natural, fluent English
- Preserve slang, humor, and cultural context — add brief [explanations] in brackets if needed
- Keep the tone and energy of the original`;

    return await this.chat(systemPrompt, text, { 
      temperature: 0.3,
      useCheapModel: true, 
    });
  }

  // ═══════════════════════════════════════════════════════
  //  TTS Methods
  // ═══════════════════════════════════════════════════════

  /**
   * Generate speech from text
   */
  async textToSpeech(text, outputPath, options = {}) {
    if (this.tts?.constructor?.name === 'EdgeTTSProvider') {
      return await this.tts.textToSpeech(text, outputPath, options);
    }
    // OpenAI fallback
    if (this.tts?.audio?.speech) {
      const response = await this.tts.audio.speech.create({
        model: 'tts-1-hd',
        voice: options.voice || 'nova',
        input: text,
      });
      const buffer = Buffer.from(await response.arrayBuffer());
      require('fs').writeFileSync(outputPath, buffer);
      return outputPath;
    }
    throw new Error('No TTS provider available');
  }

  /**
   * Generate two-voice dialog (for "What is this?" format)
   */
  async generateDialogAudio(scenes, outputDir) {
    if (this.tts?.constructor?.name === 'EdgeTTSProvider') {
      return await this.tts.generateDialogAudio(scenes, outputDir);
    }
    // Fallback to single voice
    const files = [];
    for (const scene of scenes) {
      const outputFile = require('path').join(outputDir, `scene_${scene.sceneNumber}.mp3`);
      await this.textToSpeech(scene.dialogue, outputFile, { voice: 'nova' });
      files.push({ scene: scene.sceneNumber, file: outputFile, dialogue: scene.dialogue });
    }
    return files;
  }

  // ═══════════════════════════════════════════════════════
  //  Image Generation
  // ═══════════════════════════════════════════════════════

  /**
   * Generate an image
   */
  async generateImage(prompt, outputPath, options = {}) {
    if (!this.imageGen) {
      throw new Error('No image generation provider available');
    }
    if (this.imageGen.constructor.name === 'OpenRouterProvider') {
      return await this.imageGen.generateImage(prompt, outputPath, options);
    }
    // DALL-E fallback
    const response = await this.imageGen.images.generate({
      model: 'dall-e-3',
      prompt: prompt,
      n: 1,
      size: options.size || '1792x1024',
    });
    const axios = require('axios');
    const fs = require('fs');
    const imgResponse = await axios({ method: 'GET', url: response.data[0].url, responseType: 'stream' });
    const writer = fs.createWriteStream(outputPath);
    imgResponse.data.pipe(writer);
    return new Promise((resolve, reject) => {
      writer.on('finish', () => resolve(outputPath));
      writer.on('error', reject);
    });
  }

  /**
   * Transcribe audio (only OpenAI Whisper supported)
   */
  async transcribe(audioFilePath, language = null) {
    if (!config.openai?.apiKey) {
      throw new Error('Whisper transcription requires OPENAI_API_KEY');
    }
    const OpenAI = require('openai');
    const openai = new OpenAI({ apiKey: config.openai.apiKey });
    const options = { model: 'whisper-1', file: require('fs').createReadStream(audioFilePath), response_format: 'verbose_json' };
    if (language) options.language = language;
    return await openai.audio.transcriptions.create(options);
  }

  /**
   * List models from provider
   */
  async listAvailableModels() {
    if (this.llm?.constructor?.name === 'OpenRouterProvider') {
      return await this.llm.listAvailableModels();
    }
    return [];
  }
}

module.exports = { AIService };