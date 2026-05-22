#!/usr/bin/env node

/**
 * ███╗   ███╗██████╗     ██╗    ██╗ ██████╗ ██████╗ ██╗     ██████╗ ██╗    ██╗██╗██████╗ ███████╗██████╗ ██╗███████╗████████╗███████╗██████╗ 
 * ████╗ ████║██╔══██╗    ██║    ██║██╔═══██╗██╔══██╗██║     ██╔══██╗██║    ██║██║██╔══██╗██╔════╝██╔══██╗██║██╔════╝╚══██╔══╝██╔════╝██╔══██╗
 * ██╔████╔██║██████╔╝    ██║ █╗ ██║██║   ██║██████╔╝██║     ██║  ██║██║ █╗ ██║██║██████╔╝█████╗  ██████╔╝██║█████╗     ██║   █████╗  ██████╔╝
 * ██║╚██╔╝██║██╔══██╗    ██║███╗██║██║   ██║██╔══██╗██║     ██║  ██║██║███╗██║██║██╔══██╗██╔══╝  ██╔══██╗██║██╔══╝     ██║   ██╔══╝  ██╔══██╗
 * ██║ ╚═╝ ██║██║  ██║    ╚███╔███╔╝╚██████╔╝██║  ██║███████╗██████╔╝╚███╔███╔╝██║██████╔╝███████╗██████╔╝██║███████╗   ██║   ███████╗██║  ██║
 * ╚═╝     ╚═╝╚═╝  ╚═╝     ╚══╝╚══╝  ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═════╝  ╚══╝╚══╝ ╚═╝╚═════╝ ╚══════╝╚═════╝ ╚═╝╚══════╝   ╚═╝   ╚══════╝╚═╝  ╚═╝
 * 
 * Mr. WorldWideWebster - AI YouTube Channel Orchestrator
 * "Bringing the world to you"
 * 
 * This is the main entry point. It orchestrates:
 * 1. Content Discovery (sourcing from global platforms)
 * 2. Decision Engine (AI decides what to do with each piece)
 * 3. Content Routing (sends to correct pipeline)
 * 4. YouTube Publishing (uploads processed content)
 * 5. Daily Scheduling (fully automated)
 * 
 * Usage:
 *   node core/index.js              # Full automation mode
 *   node core/index.js --source     # Source content only
 *   node core/index.js --process    # Process already-sourced items
 *   node core/index.js --topic "UK Drill"  # Create AI content about a topic
 */

const config = require('./config');
const { AIService } = require('./ai-service');
const { DecisionEngine } = require('./decision-engine');
const { ContentRouter } = require('./content-router');
const { SourceController } = require('../sourcing/source-controller');
const { Logger } = require('./logger');

// ─── Core App ───────────────────────────────────────────────────────────────

class MrWorldWideWebster {
  constructor() {
    this.logger = new Logger('MWW');
    this.ai = null;
    this.decisionEngine = null;
    this.router = null;
    this.sourceController = null;
    this.initialized = false;
    this.contentQueue = [];
    this.processedCount = 0;
  }

  async initialize() {
    this.logger.header(`MR. WORLDWIDEWEBSTER v1.0`);
    this.logger.info(`Channel: ${config.channel.name}`);
    this.logger.info(`Tagline: ${config.channel.tagline}`);
    this.logger.info('');

    // Initialize AI
    this.logger.info('Initializing AI service...');
    this.ai = new AIService();

    // Initialize decision engine
    this.logger.info('Initializing decision engine...');
    this.decisionEngine = new DecisionEngine(this.ai);

    // Initialize content router
    this.logger.info('Initializing content router...');
    this.router = new ContentRouter(this.ai, config);

    // Initialize source controller
    this.logger.info('Initializing source controller...');
    this.sourceController = new SourceController(this.ai, config);

    this.initialized = true;
    this.logger.success('Mr. WorldWideWebster initialized!');
    this.logger.info('');
  }

  /**
   * Full pipeline: Discover → Decide → Route → Output
   */
  async runFullPipeline(options = { maxItems: 10 }) {
    if (!this.initialized) await this.initialize();

    this.logger.header('FULL PIPELINE RUN');
    this.logger.info(`Max items to process: ${options.maxItems}`);

    // ─── Step 1: Discover Content ──────────────────────────────────────
    this.logger.info('Step 1: Discovering content...');
    const sourcedContent = await this.sourceController.discoverContent();
    
    // ─── Step 2: Get AI-Generated Topics ─────────────────────────────
    this.logger.info('Step 2: Generating AI topic ideas...');
    const aiTopics = await this.sourceController.getTopicsForAICreate();
    
    const allContent = [...sourcedContent, ...aiTopics];
    this.logger.info(`Total content available: ${allContent.length} items`);

    if (allContent.length === 0) {
      this.logger.warn('No content to process');
      return [];
    }

    // ─── Step 3: Rank by Decision Engine ────────────────────────────────
    this.logger.info('Step 3: AI decision engine ranking...');
    const rankedContent = await this.decisionEngine.rankContent(allContent);
    
    // Take top N
    const toProcess = rankedContent.slice(0, options.maxItems);
    this.logger.info(`Selected top ${toProcess.length} items to process`);

    // ─── Step 4: Route Through Pipelines ──────────────────────────────
    this.logger.info('Step 4: Processing through pipelines...');
    const results = [];

    for (const item of toProcess) {
      this.logger.info('');

      // Generate a catchy title
      const title = await this.decisionEngine.generateTitle(item, item.decision);

      // Route through the correct pipeline
      const result = await this.router.route({ ...item, title }, item.decision);
      results.push(result);

      if (result.status === 'completed') {
        this.processedCount++;
      }
    }

    // ─── Step 5: Summary ─────────────────────────────────────────────────
    this.logger.header('PIPELINE SUMMARY');
    
    const completed = results.filter(r => r.status === 'completed').length;
    const failed = results.filter(r => r.status === 'failed').length;

    this.logger.info(`Processed: ${results.length} items`);
    this.logger.info(`Completed: ${completed}`);
    this.logger.info(`Failed: ${failed}`);
    this.logger.info('');

    // Print each result
    for (const result of results) {
      const icon = result.status === 'completed' ? '✅' : '❌';
      const pathIcon = {
        'clip': '🎬',
        'voiceover': '🎙️',
        'explain': '🤔',
        'ai_create': '🤖',
      }[result.decision?.path] || '📦';

      this.logger.info(`${icon} ${pathIcon} [${result.decision?.path?.toUpperCase() || '?'}] ${result.source?.title?.substring(0, 60) || 'Untitled'}`);
      if (result.decision?.reasoning) {
        this.logger.info(`   📝 ${result.decision.reasoning.substring(0, 100)}`);
      }
      if (result.output?.title) {
        this.logger.info(`   📺 ${result.output.title}`);
      }
      if (result.output?.scriptPath) {
        this.logger.info(`   📄 Script: ${result.output.scriptPath}`);
      }
    }

    this.logger.info('');
    this.logger.success(`Done! ${completed} videos ready for review.`);
    this.logger.info(`Output directory: ${config.paths.output}`);

    return results;
  }

  /**
   * Source-only mode
   */
  async runSourceOnly() {
    if (!this.initialized) await this.initialize();
    this.logger.header('CONTENT SOURCING ONLY');
    const content = await this.sourceController.discoverContent();
    const topics = await this.sourceController.getTopicsForAICreate();
    
    this.logger.info(`\nDiscovered ${content.length} items + ${topics.length} AI topics`);
    
    // Save to queue
    const fs = require('fs');
    const queuePath = './content_queue.json';
    const queue = [...content, ...topics];
    fs.writeFileSync(queuePath, JSON.stringify(queue, null, 2));
    this.logger.info(`Queue saved to ${queuePath}`);
    
    return queue;
  }

  /**
   * Process a specific topic through AI Create
   */
  async createTopic(topic) {
    if (!this.initialized) await this.initialize();
    
    this.logger.header(`AI CREATE: "${topic}"`);
    
    const content = {
      url: null,
      title: topic,
      platform: 'ai_topic',
      description: `Exploring: ${topic}`,
      duration: 60,
      hasSpeech: false,
      isVisual: false,
      languageDetected: 'english',
      thumbnailUrl: null,
      isAITopic: true,
    };

    // AI decides what type of content this should be
    const decision = await this.decisionEngine.decidePath(content);
    this.logger.info(`Decision: ${decision.path} (${decision.confidence}%)`);

    // Generate title
    const title = await this.decisionEngine.generateTitle(content, decision);
    
    // Process
    const result = await this.router.route({ ...content, title }, decision);
    
    if (result.status === 'completed') {
      this.logger.success(`Created: "${result.output?.title || topic}"`);
      this.logger.info(`Script: ${result.output?.scriptPath}`);
      this.logger.info(`Output: ${result.output?.outputPath}`);
    } else {
      this.logger.error(`Failed: ${result.error}`);
    }

    return result;
  }
}

// ─── CLI Entry Point ────────────────────────────────────────────────────────

async function main() {
  const args = process.argv.slice(2);
  const app = new MrWorldWideWebster();

  if (args.includes('--help') || args.includes('-h')) {
    console.log(`
  Usage: node core/index.js [options]

  Options:
    (no args)   Run full pipeline (source + decide + create)
    --source    Source content only (save to queue)
    --topic     Create AI content about a specific topic
      Example: node core/index.js --topic "UK Drill vs US Trap"
    --help      Show this help

  Environment:
    Create a .env file based on .env.example
    Make sure OPENAI_API_KEY is set
    `);
    return;
  }

  if (args.includes('--topic')) {
    const topicIndex = args.indexOf('--topic') + 1;
    const topic = args[topicIndex];
    if (!topic) {
      console.error('Error: --topic requires a topic string');
      process.exit(1);
    }
    await app.createTopic(topic);
    return;
  }

  if (args.includes('--source')) {
    await app.runSourceOnly();
    return;
  }

  // Default: full pipeline
  await app.runFullPipeline({ maxItems: 8 });
}

main().catch(error => {
  console.error('\n❌ Fatal error:', error.message);
  process.exit(1);
});