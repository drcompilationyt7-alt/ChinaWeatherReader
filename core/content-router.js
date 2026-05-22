/**
 * Mr. WorldWideWebster - Content Router
 * 
 * Routes content through the correct pipeline based on the decision engine's output.
 * Each path has its own processing pipeline.
 */
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

class ContentRouter {
  constructor(aiService, config) {
    this.ai = aiService;
    this.config = config;
    this.logger = new Logger('ContentRouter');

    // Ensure output directories exist
    this._ensureDirs();
  }

  _ensureDirs() {
    const dirs = [
      this.config.paths.clips,
      this.config.paths.voiceovers,
      this.config.paths.explainers,
      this.config.paths.aiCreated,
      this.config.paths.assets,
      this.config.paths.audio,
      this.config.paths.scripts,
    ];
    dirs.forEach(dir => {
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
    });
  }

  /**
   * Route content through the appropriate pipeline
   */
  async route(content, decision) {
    this.logger.header(`Processing: ${decision.path} — ${content.title?.substring(0, 50) || 'Untitled'}`);

    const result = {
      source: content,
      decision: decision,
      status: 'processing',
      output: null,
      error: null,
    };

    try {
      switch (decision.path) {
        case 'clip':
          result.output = await this._clipPipeline(content, decision);
          break;
        case 'voiceover':
          result.output = await this._voiceoverPipeline(content, decision);
          break;
        case 'explain':
          result.output = await this._explainPipeline(content, decision);
          break;
        case 'ai_create':
          result.output = await this._aiCreatePipeline(content, decision);
          break;
        default:
          throw new Error(`Unknown path: ${decision.path}`);
      }
      result.status = 'completed';
      this.logger.success(`${decision.path} pipeline completed successfully`);
    } catch (error) {
      result.status = 'failed';
      result.error = error.message;
      this.logger.error(`${decision.path} pipeline failed: ${error.message}`);
    }

    return result;
  }

  /**
   * CLIP Pipeline: Download video → find viral moments → crop to 9:16 → add captions
   */
  async _clipPipeline(content, decision) {
    this.logger.info('Starting CLIP pipeline...');

    // For now, this will download the video and prepare it
    // The actual clipping will be done by the clipping module
    const clippingModule = require('../clipping/clip-pipeline');
    const clipResult = await clippingModule.processClip({
      url: content.url,
      title: content.title,
      platform: content.platform,
      outputDir: this.config.paths.clips,
      hookStrategy: decision.hookStrategy,
    });

    return clipResult;
  }

  /**
   * VOICEOVER Pipeline: Download → transcribe → translate → generate TTS → replace audio → add captions
   */
  async _voiceoverPipeline(content, decision) {
    this.logger.info('Starting VOICEOVER pipeline...');

    const voiceoverModule = require('../voiceover/voiceover-pipeline');
    const voiceoverResult = await voiceoverModule.processVoiceover({
      url: content.url,
      title: content.title,
      platform: content.platform,
      outputDir: this.config.paths.voiceovers,
      ai: this.ai,
      languageDetected: content.languageDetected,
    });

    return voiceoverResult;
  }

  /**
   * EXPLAIN Pipeline: "What is this...?" format
   * Two voices: Curious asks "What is this?" → Explainer answers with details
   */
  async _explainPipeline(content, decision) {
    this.logger.info('Starting EXPLAIN pipeline...');

    const explainerModule = require('../explainer/explain-pipeline');
    const explainResult = await explainerModule.processExplain({
      sourceContent: content,
      explainThing: decision.explainThing || 'this content',
      explainCategory: decision.explainCategory || 'other',
      decision: decision,
      outputDir: this.config.paths.explainers,
      ai: this.ai,
      config: this.config,
    });

    return explainResult;
  }

  /**
   * AI CREATE Pipeline: Research → write script → generate visuals → TTS → compile video
   */
  async _aiCreatePipeline(content, decision) {
    this.logger.info('Starting AI CREATE pipeline...');

    const aiCreatorModule = require('../ai-creator/ai-create-pipeline');
    const createResult = await aiCreatorModule.processCreate({
      sourceContent: content,
      contentType: decision.contentType || 'explainer',
      decision: decision,
      outputDir: this.config.paths.aiCreated,
      ai: this.ai,
      config: this.config,
    });

    return createResult;
  }
}

module.exports = { ContentRouter };