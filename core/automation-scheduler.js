#!/usr/bin/env node

/**
 * Mr. WorldWideWebster — 24/7 Automation Scheduler
 * 
 * Fully autonomous mode for running on a VPS.
 * No human intervention needed.
 * 
 * Schedule:
 * - Every 6 hours: Source content from all platforms
 * - Every hour: Process queued content
 * - Daily at 8AM: Upload videos to YouTube
 * - Daily at midnight: Hermes Agent runs strategy review
 * 
 * Run with: node core/automation-scheduler.js
 * Or with PM2: pm2 start core/automation-scheduler.js --name mww
 */
const cron = require('node-cron');
const config = require('./config');
const { AIService } = require('./ai-service');
const { DecisionEngine } = require('./decision-engine');
const { ContentRouter } = require('./content-router');
const { SourceController } = require('../sourcing/source-controller');
const { HermesAgent } = require('../hermes-agent/agent-core');
const { Logger } = require('./logger');
const fs = require('fs');
const path = require('path');

class AutomationScheduler {
  constructor() {
    this.logger = new Logger('AutoScheduler');
    this.ai = null;
    this.decisionEngine = null;
    this.router = null;
    this.sourceController = null;
    this.agent = null;
    this.youtubeBridge = null;  // ← NEW: real YouTube uploads
    this.initialized = false;
    this.dailyStats = { sourced: 0, processed: 0, uploaded: 0, errors: 0 };
    this.contentQueue = [];
    this.queuePath = path.join(__dirname, '..', 'content_queue.json');
  }

  async initialize() {
    this.logger.header('🤖 MR. WORLDWIDEWEBSTER — 24/7 AUTOMATION');
    this.logger.info('Channel:', config.channel.name);
    this.logger.info('Starting autonomous mode — no human intervention needed');
    this.logger.info('');

    // Load AI
    this.ai = new AIService();
    
    // Load engine
    this.decisionEngine = new DecisionEngine(this.ai);
    
    // Load router 
    this.router = new ContentRouter(this.ai, config);
    
    // Load sources
    this.sourceController = new SourceController(this.ai, config);

    // Load Hermes Agent
    this.agent = new HermesAgent(this.ai);

    // ─── Initialize YouTube Bridge ───────────────────────────────────
    this.logger.info('Initializing YouTube bridge...');
    this.youtubeBridge = new (require('../youtube-automation/youtube-bridge').YouTubeBridge)();
    if (await this.youtubeBridge.initialize()) {
      this.logger.success('✅ YouTube bridge connected — real uploads enabled');
    } else {
      this.logger.warn('⚠️ YouTube bridge not configured — set up with: node youtube-automation/setup-youtube-auth');
    }

    // Load any queued content
    this._loadQueue();

    this.initialized = true;
    this.logger.success('✅ Automation system initialized. Ready for 24/7 operation.');
    this.logger.info('');
  }

  _loadQueue() {
    if (fs.existsSync(this.queuePath)) {
      try {
        this.contentQueue = JSON.parse(fs.readFileSync(this.queuePath, 'utf8'));
        this.logger.info(`Loaded ${this.contentQueue.length} items from queue`);
      } catch {
        this.contentQueue = [];
      }
    }
  }

  _saveQueue() {
    fs.writeFileSync(this.queuePath, JSON.stringify(this.contentQueue, null, 2));
  }

  /**
   * Task 1: Source content from all platforms (every 6 hours)
   */
  async taskSourceContent() {
    this.logger.header('📡 TASK: SOURCE CONTENT');
    try {
      const sourced = await this.sourceController.discoverContent();
      const aiTopics = await this.sourceController.getTopicsForAICreate();
      
      const newItems = [...sourced, ...aiTopics];
      this.contentQueue.push(...newItems.map(item => ({
        ...item,
        queuedAt: new Date().toISOString(),
        status: 'pending',
      })));

      this._saveQueue();
      this.dailyStats.sourced += newItems.length;
      
      this.logger.success(`✅ Sourced ${newItems.length} new items (queue: ${this.contentQueue.length})`);
    } catch (error) {
      this.dailyStats.errors++;
      this.logger.error(`Source task failed: ${error.message}`);
    }
  }

  /**
   * Task 2: Process queued content (every hour)
   */
  async taskProcessQueue() {
    this.logger.header('⚡ TASK: PROCESS QUEUE');
    
    const pending = this.contentQueue.filter(item => item.status === 'pending');
    if (pending.length === 0) {
      this.logger.info('No pending items to process');
      return;
    }

    // Process top 3 items max per run
    const batch = pending.slice(0, 3);
    this.logger.info(`Processing ${batch.length} items...`);

    for (const item of batch) {
      try {
        // AI decides what to do
        const decision = await this.decisionEngine.decidePath(item);
        
        // Generate a title
        const title = await this.decisionEngine.generateTitle(item, decision);
        
        // Process through the pipeline
        const result = await this.router.route({ ...item, title }, decision);
        
        // Mark as processed
        item.status = result.status === 'completed' ? 'completed' : 'failed';
        item.processedAt = new Date().toISOString();
        item.result = result.output?.title || title;
        item.outputPath = result.output?.scriptPath || result.output?.outputPath;

        if (result.status === 'completed') {
          this.dailyStats.processed++;
          this.logger.success(`✅ [${decision.path}] ${title.substring(0, 60)}`);
        } else {
          this.dailyStats.errors++;
          this.logger.error(`❌ [${decision.path}] ${title.substring(0, 60)} — ${result.error}`);
        }
      } catch (error) {
        item.status = 'failed';
        item.error = error.message;
        this.dailyStats.errors++;
        this.logger.error(`Failed to process: ${error.message}`);
      }
    }

    this._saveQueue();
    this.logger.info(`Queue status: ${this.contentQueue.filter(i => i.status === 'pending').length} pending, ${this.contentQueue.filter(i => i.status === 'completed').length} completed`);
  }

  /**
   * Task 3: Hermes Agent strategy review (daily at midnight)
   * The agent analyzes performance and creates new skills
   */
  async taskAgentStrategyReview() {
    this.logger.header('🧠 TASK: HERMES AGENT STRATEGY REVIEW');
    
    try {
      const stats = {
        totalSourced: this.dailyStats.sourced,
        totalProcessed: this.dailyStats.processed,
        totalErrors: this.dailyStats.errors,
        queueSize: this.contentQueue.length,
        completedCount: this.contentQueue.filter(i => i.status === 'completed').length,
      };

      this.logger.info(`Daily stats: ${JSON.stringify(stats)}`);

      // Have the agent think about what to improve
      const result = await this.agent.run(
        `Review the past 24 hours of operation for Mr. WorldWideWebster channel.
        
        Production stats: ${JSON.stringify(stats)}
        
        Recently processed content:
        ${this.contentQueue.filter(i => i.status === 'completed').slice(-5).map(i => `- ${i.title} (${i.platform}) → ${i.result}`).join('\n')}
        
        Tasks:
        1. Analyze what types of content performed best
        2. Suggest new content strategies
        3. Identify any patterns in failed items
        4. Create new skills to automate repetitive tasks
        
        Create at least 2 reusable skills that would help automate this channel better.`,
        { verbose: true }
      );

      this.logger.success(`✅ Strategy review complete: ${result.stepsCount} steps`);
      
      // Reset daily stats
      this.dailyStats = { sourced: 0, processed: 0, uploaded: 0, errors: 0 };
    } catch (error) {
      this.logger.error(`Strategy review failed: ${error.message}`);
    }
  }

  /**
   * Task 4: Content creation — generate new topics
   * The agent proactively searches for trending topics
   */
  async taskAgentCreateContent() {
    this.logger.header('🔥 TASK: AGENT DISCOVER TRENDS');
    
    try {
      // Use the agent to find what's trending
      const result = await this.agent.run(
        `You are running 24/7 on a VPS for Mr. WorldWideWebster. Your goal is to find NEW trending content ideas.

        1. Search the web for what's trending in these categories:
           - Chinese internet trends (Bilibili, Douyin, Weibo)
           - African viral content
           - UK vs US cultural comparisons
           - Japanese/Korean pop culture
           - Global news that would interest an international audience
           
        2. For each trend found, create a content plan:
           - Title for the video
           - What type of content (clip/voiceover/explain/comparison)
           - Why it would appeal to Mr. WorldWideWebster's audience
           
        3. Add them to our content queue by creating a skill that automatically 
           discovers these trends daily.
           
        Focus on finding at least 5 new content ideas that are actually trending NOW.`,
        { verbose: false, maxSteps: 8 }
      );

      this.logger.success(`✅ Agent created ${result.stepsCount} new content ideas`);
    } catch (error) {
      this.logger.error(`Agent trend discovery failed: ${error.message}`);
    }
  }

  /**
   * Start all scheduled tasks
   */
  async start() {
    await this.initialize();

    this.logger.header('⏰ SCHEDULED TASKS');

    // ─── Every 6 hours: Source content ────────────────────────────────
    cron.schedule('0 */6 * * *', async () => {
      await this.taskSourceContent();
    });
    this.logger.info('📡 Content sourcing: Every 6 hours');

    // ─── Every hour: Process queue ────────────────────────────────────
    cron.schedule('0 * * * *', async () => {
      await this.taskProcessQueue();
    });
    this.logger.info('⚡ Queue processing: Every hour');

    // ─── Daily at midnight: Strategy review ───────────────────────────
    cron.schedule('0 0 * * *', async () => {
      await this.taskAgentStrategyReview();
    });
    this.logger.info('🧠 Strategy review: Daily at midnight');

    // ─── Daily at 6 AM: Agent trend discovery ─────────────────────────
    cron.schedule('0 6 * * *', async () => {
      await this.taskAgentCreateContent();
    });
    this.logger.info('🔥 Trend discovery: Daily at 6 AM');

    // ─── Daily at 9 AM: Upload to YouTube ─────────────────────────────
    cron.schedule('0 9 * * *', async () => {
      await this.taskUploadToYouTube();
    });
    this.logger.info('📤 YouTube upload: Daily at 9 AM');

    // ─── Also run initial tasks on startup ────────────────────────────
    this.logger.info('');
    this.logger.info('🚀 Running initial tasks on startup...');
    
    await this.taskSourceContent();
    await this.taskProcessQueue();

    this.logger.header('✅ SYSTEM RUNNING 24/7');
    this.logger.info('PID:', process.pid);
    this.logger.info('To monitor: pm2 logs mww');
    this.logger.info('To stop: pm2 stop mww');
    this.logger.info('');

    // Keep alive
    setInterval(() => {
      const now = new Date().toISOString();
      const stats = {
        uptime: Math.floor(process.uptime() / 3600) + 'h',
        queue: this.contentQueue.length,
        pending: this.contentQueue.filter(i => i.status === 'pending').length,
        completed: this.contentQueue.filter(i => i.status === 'completed').length,
        memory: Math.round(process.memoryUsage().heapUsed / 1024 / 1024) + 'MB',
      };
      this.logger.info(`💓 Heartbeat: ${JSON.stringify(stats)}`);
    }, 3600000); // Every hour
  }

  /**
   * Task: Upload to YouTube — uses the real YouTube bridge which
   * loads credentials from youtube-automation-agent-master/config/
   */
  async taskUploadToYouTube() {
    this.logger.header('📤 TASK: UPLOAD TO YOUTUBE');

    if (!this.youtubeBridge?.isAuthenticated()) {
      this.logger.warn('YouTube not authenticated. Run: node youtube-automation/setup-youtube-auth');
      this.logger.warn('Then set up YouTube API creds in youtube-automation-agent-master/config/');
      return;
    }

    const completed = this.contentQueue.filter(i => i.status === 'completed' && !i.uploaded);
    
    if (completed.length === 0) {
      this.logger.info('No new content to upload');
      return;
    }

    const maxUpload = config.youtube.maxUploadsPerDay || 5;
    const batch = completed.slice(0, maxUpload);

    for (const item of batch) {
      try {
        // Build the video path — check multiple possible locations
        let videoPath = null;
        if (item.outputPath) {
          // Check for mp4 in output dir
          const dir = path.dirname(item.outputPath);
          if (fs.existsSync(dir)) {
            const files = fs.readdirSync(dir).filter(f => f.endsWith('.mp4') || f.endsWith('.mp3'));
            if (files.length > 0) {
              videoPath = path.join(dir, files[0]);
            }
          }
        }

        this.logger.info(`📤 Uploading: "${item.result || item.title}"`);

        // Upload to YouTube via the bridge
        const result = await this.youtubeBridge.uploadVideo({
          videoPath: videoPath || item.outputPath || item.scriptPath,
          title: item.result || item.title,
          description: `${item.title}\n\n🌍 Bringing the world to you\n\nFollow Mr. WorldWideWebster for more global content!\n\n#MrWorldWideWebster #Global #Culture`,
          tags: ['MrWorldWideWebster', 'global', 'culture', item.platform || 'international'],
        });

        // Mark as uploaded
        item.uploaded = true;
        item.uploadedAt = new Date().toISOString();
        item.youtubeId = result.videoId;
        item.youtubeUrl = result.url;
        this.dailyStats.uploaded++;

        this.logger.success(`✅ Uploaded: ${result.url}`);
      } catch (error) {
        item.uploadError = error.message;
        this.dailyStats.errors++;
        this.logger.error(`Upload failed for "${item.title}": ${error.message}`);
      }
    }

    this._saveQueue();
    this.logger.success(`✅ Uploaded ${batch.length} videos to YouTube`);
  }

  /**
   * Get system status
   */
  getStatus() {
    return {
      uptime: process.uptime(),
      pid: process.pid,
      memory: process.memoryUsage().heapUsed,
      queue: {
        total: this.contentQueue.length,
        pending: this.contentQueue.filter(i => i.status === 'pending').length,
        completed: this.contentQueue.filter(i => i.status === 'completed').length,
        failed: this.contentQueue.filter(i => i.status === 'failed').length,
        uploaded: this.contentQueue.filter(i => i.uploaded).length,
      },
      daily: this.dailyStats,
      skills: this.agent?.skills ? Object.keys(this.agent.skills).length : 0,
      channels: [config.channel.name],
    };
  }
}

// ─── Start if run directly ──────────────────────────────────────────────

if (require.main === module) {
  const scheduler = new AutomationScheduler();
  scheduler.start().catch(error => {
    console.error('❌ Fatal error:', error);
    process.exit(1);
  });
}

module.exports = { AutomationScheduler };