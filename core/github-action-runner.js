#!/usr/bin/env node

/**
 * Mr. WorldWideWebster — GitHub Actions Runner
 *
 * Entry point for GitHub Actions workflows. Handles:
 * - Loading/storing persistent memory via git commits
 * - Running Hermes Agent for web scraping (no APIs needed)
 * - Creating daily shorts (2 clips + 1 "What is this...?")
 * - Creating weekly long-form videos
 * - Uploading created videos to YouTube
 * - Boosting views with Puppeteer headless browser
 * - Sending Discord notifications (daily summary, weekly report, alerts)
 * - Midnight self-improvement review
 *
 * Usage:
 *   node core/github-action-runner.js --mode daily        # 6 AM: 3 shorts
 *   node core/github-action-runner.js --mode weekly       # Sat: long-form
 *   node core/github-action-runner.js --mode review       # Midnight: improve
 *   node core/github-action-runner.js --mode weekly --topic "Street Food"
 *
 * Environment variables (from GitHub Secrets):
 *   OPENROUTER_API_KEY, YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET,
 *   YOUTUBE_REFRESH_TOKEN, DISCORD_BOT_TOKEN, GH_PAT,
 *   BOOST_ENABLED, BOOST_MAX_VIEWS
 */
const path = require('path');
const fs = require('fs');
const config = require('./config');
const { AIService } = require('./ai-service');
const { HermesCLIWrapper } = require('../hermes-agent/hermes-cli-wrapper');
const { Logger } = require('./logger');

class GitHubActionsRunner {
  constructor() {
    this.logger = new Logger('GHAction');
    this.ai = null;
    this.agent = null;
    this.memory = {};
    this.memoryPath = path.join(__dirname, '..', 'memory');
    this.youtubeBridge = null;
    this.boostEngine = null;
    this.discordBridge = null;
  }

  async initialize() {
    this.logger.header('🤖 Mr. WorldWideWebster — GitHub Actions');

    // Initialize AI
    this.ai = new AIService();

    // PRIMARY: Official Hermes CLI from Nous Research (installed via curl)
    const hermesCLI = new HermesCLIWrapper();
    if (hermesCLI.isAvailable()) {
      this.agent = hermesCLI;
      this.logger.success('✅ Using official Hermes CLI as primary agent');
    } else {
      // FALLBACK: Built-in Hermes JS agent
      this.logger.info('Falling back to built-in Hermes JS agent...');
      const { HermesAgentWithScraping } = require('../hermes-agent/agent-tools');
      this.agent = new HermesAgentWithScraping(this.ai);
      this.logger.info('Using built-in Hermes JS agent (custom code)');
    }

    // Load persistent memory from repo
    this._loadMemory();

    // Initialize YouTube bridge if credentials are available
    try {
      const { YouTubeBridge } = require('../youtube-automation/youtube-bridge');
      this.youtubeBridge = new YouTubeBridge();
      await this.youtubeBridge.initialize();
    } catch (error) {
      this.logger.warn(`YouTube bridge not available: ${error.message}`);
      this.logger.warn('Videos will be saved locally but not uploaded');
    }

    this.logger.success('GitHub Actions runner initialized');
  }

  _loadMemory() {
    if (!fs.existsSync(this.memoryPath)) {
      fs.mkdirSync(this.memoryPath, { recursive: true });
    }

    const memoryFiles = {
      'channel-memory.json': {
        channelName: 'Mr. WorldWideWebster',
        tagline: 'Bringing the world to you',
        totalVideosPosted: 0,
        lastCountryUsed: '',
        countriesUsedThisWeek: [],
        bestPerformingFormats: [],
        titleFormulas: [],
        postingSchedule: { hour: 6, minute: 0 },
        createdAt: new Date().toISOString(),
      },
      'trending-log.json': {
        lastUpdated: new Date().toISOString(),
        trends: [],
      },
      'content-history.json': {
        videos: [],
      },
    };

    for (const [file, defaults] of Object.entries(memoryFiles)) {
      const filePath = path.join(this.memoryPath, file);
      if (fs.existsSync(filePath)) {
        try {
          this.memory[file.replace('.json', '')] = JSON.parse(fs.readFileSync(filePath, 'utf8'));
          this.logger.info(`Loaded: ${file}`);
        } catch {
          this.memory[file.replace('.json', '')] = defaults;
          fs.writeFileSync(filePath, JSON.stringify(defaults, null, 2));
        }
      } else {
        this.memory[file.replace('.json', '')] = defaults;
        fs.writeFileSync(filePath, JSON.stringify(defaults, null, 2));
        this.logger.info(`Created: ${file}`);
      }
    }
  }

  _saveMemory(key, data) {
    if (!key) {
      for (const [k, v] of Object.entries(this.memory)) {
        const filePath = path.join(this.memoryPath, `${k}.json`);
        fs.writeFileSync(filePath, JSON.stringify(v, null, 2));
      }
      return;
    }
    const filePath = path.join(this.memoryPath, `${key}.json`);
    fs.writeFileSync(filePath, JSON.stringify(data, null, 2));
    this.memory[key] = data;
  }

  /**
   * Upload a video to YouTube using the YouTube bridge
   */
  async _uploadToYouTube(videoData) {
    if (!this.youtubeBridge || !this.youtubeBridge.isAuthenticated()) {
      this.logger.warn('YouTube not authenticated — skipping upload');
      this.logger.warn('Run "node youtube-automation/setup-youtube.js" locally first');
      return null;
    }

    this.logger.info(`Uploading to YouTube: "${videoData.title}"`);

    try {
      const result = await this.youtubeBridge.uploadVideo({
        videoPath: videoData.videoPath,
        title: videoData.title,
        description: videoData.description ||
          `${videoData.title}\n\n🌍 Bringing the world to you\n\nFollow Mr. WorldWideWebster for more global content!`,
        tags: videoData.tags || ['mr worldwidewebster', 'global', 'culture', 'international', 'shorts'],
        thumbnailPath: videoData.thumbnailPath,
      });

      this.logger.success(`✅ Uploaded: ${result.url}`);

      // Save to content history
      const contentHistory = this.memory['content-history'];
      if (contentHistory) {
        contentHistory.videos.push({
          title: videoData.title,
          type: videoData.type || 'shorts',
          url: result.url,
          videoId: result.videoId,
          country: videoData.country || 'Global',
          uploadedAt: result.publishedAt,
        });

        // Keep last 200 entries
        if (contentHistory.videos.length > 200) {
          contentHistory.videos = contentHistory.videos.slice(-200);
        }
        this._saveMemory('content-history', contentHistory);
      }

      // Update channel memory
      const channelMemory = this.memory['channel-memory'];
      if (channelMemory) {
        channelMemory.totalVideosPosted = (channelMemory.totalVideosPosted || 0) + 1;
        this._saveMemory('channel-memory', channelMemory);
      }

      return result;
    } catch (error) {
      this.logger.error(`Upload failed: ${error.message}`);
      return null;
    }
  }

  /**
   * Boost a video's views using the Puppeteer boost engine
   */
  async _boostVideo(videoUrl, customViews) {
    if (!config.boost.enabled) {
      this.logger.info('Boost disabled via BOOST_ENABLED config');
      return;
    }

    this.logger.info(`🚀 Boosting video: ${videoUrl}`);

    try {
      const { BoostEngine } = require('../boost/boost-engine');
      const engine = new BoostEngine();

      const result = await engine.run({
        url: videoUrl,
        views: customViews || config.boost.maxViews,
      });

      if (result.success) {
        this.logger.success(`✅ Boost complete: ${result.views} views simulated`);
      } else {
        this.logger.warn(`Boost had issues: ${result.error || 'partial completion'}`);
      }
    } catch (error) {
      this.logger.warn(`Boost engine failed: ${error.message}`);
      this.logger.warn('This is expected if puppeteer is not installed or Chrome is not available');
    }
  }

  /**
   * Send a Discord notification about what happened
   */
  async _sendDiscordNotification(type, data) {
    try {
      const { DiscordBridge } = require('../discord/discord-bridge');
      const bridge = new DiscordBridge();

      let ok = false;
      switch (type) {
        case 'daily':
          ok = await bridge.sendDailySummary(data);
          break;
        case 'weekly':
          ok = await bridge.sendWeeklyReport(data);
          break;
        case 'alert':
          ok = await bridge.sendAlert(data.title || 'Pipeline Alert', data.message || '');
          break;
        default:
          ok = await bridge.sendMessage(data);
      }

      await bridge.destroy();

      if (ok) {
        this.logger.success('Discord notification sent');
      } else {
        this.logger.warn('Discord notification skipped (not configured?)');
      }
    } catch (error) {
      this.logger.warn(`Discord notification failed: ${error.message}`);
    }
  }

  /**
   * MODE: daily — Create content, upload, boost, notify
   */
  async runDaily() {
    this.logger.header('🌅 DAILY: Content Creation + Upload + Boost');

    const errors = [];
    const uploadedVideos = [];

    // Step 1: Hermes Agent researches trending content globally
    this.logger.info('Step 1: Hermes researching global trends...');

    let trendsResult;
    try {
      trendsResult = await this.agent.run(
        `RESEARCH GLOBAL TRENDS FOR Mr. WorldWideWebster

Your job: Find what's trending RIGHT NOW around the world.

Browse these sources (use web scraping):
1. Search for "trending on Bilibili today" — find Chinese viral videos
2. Search for "viral TikTok Japan" — find Japanese trends
3. Search for "trending in Nigeria" — find African viral content
4. Search for "UK viral video today" — find UK trends
5. Search for "German TikTok trend" — find European content
6. Search for "Australian viral" — find Australian trends

For each trend, report:
{ "platform": "bilibili/tiktok/twitter/etc", "country": "China/Japan/etc", "title": "What the trend is", "url": "source URL", "type": "dance/food/music/meme/reaction/etc" }

Focus on VISUAL content that works for YouTube Shorts.
Find at least 8 trending things from DIFFERENT countries (not all China).
Prefer content from countries we haven't covered recently.
Previous countries used: ${JSON.stringify(this.memory['channel-memory'].countriesUsedThisWeek || [])}

Return as JSON array.`,
        { verbose: false, maxSteps: 6 }
      );
    } catch (error) {
      this.logger.warn(`Hermes agent research failed: ${error.message}`);
      trendsResult = { stepsCount: 0, steps: [], output: '' };
    }

    // Normalize: HermesCLIWrapper returns steps as a number (step count),
    // while built-in HermesAgent returns steps as an array of step objects
    const stepsArray = Array.isArray(trendsResult.steps) ? trendsResult.steps : [];
    this.logger.success(`Found ${trendsResult.stepsCount || stepsArray.length} trending items`);

    // Step 2: Generate topics for "What is this...?" explainer
    this.logger.info('Step 2: Generating explainer topics...');

    const channelMemory = this.memory['channel-memory'];
    const usedCountries = channelMemory.countriesUsedThisWeek || [];

    const allCountries = [
      'Nigeria', 'Japan', 'Germany', 'Australia', 'France', 'Brazil',
      'Thailand', 'India', 'Mexico', 'UK', 'South Korea', 'Egypt',
      'Italy', 'Spain', 'South Africa', 'Argentina', 'Turkey', 'Vietnam'
    ];

    const availableCountries = allCountries.filter(c => !usedCountries.includes(c));
    const country1 = availableCountries[0] || allCountries[Math.floor(Math.random() * allCountries.length)];
    const country2 = availableCountries[1] || allCountries[Math.floor(Math.random() * allCountries.length)];

    const explainFormats = [
      { category: 'food', prompt: 'What is this food?' },
      { category: 'music', prompt: 'What is this music genre?' },
      { category: 'dance', prompt: 'What is this dance?' },
      { category: 'trend', prompt: 'What is this trend?' },
      { category: 'culture', prompt: 'What is this tradition?' },
    ];

    const format1 = explainFormats[Math.floor(Math.random() * explainFormats.length)];
    const format2 = explainFormats[Math.floor(Math.random() * explainFormats.length)];

    const explainerTopics = [
      { country: country1, format: format1, title: `${format1.prompt} (${country1} edition) 🇨🇮` },
      { country: country2, format: format2, title: `${format2.prompt} (${country2} edition) 🇨🇮` },
    ];

    this.logger.info(`Explainer topics: ${explainerTopics.map(t => t.title).join(', ')}`);

    // Step 3: Create explainer
    const explainTopic = explainerTopics[Math.floor(Math.random() * explainerTopics.length)];

    this.logger.info(`Step 3: Creating explainer: "${explainTopic.title}"`);

    const explainPipeline = require('../explainer/explain-pipeline');
    let explainResult;
    try {
      explainResult = await explainPipeline.processExplain({
        sourceContent: {
          title: explainTopic.title,
          platform: 'web',
          description: `Exploring ${explainTopic.country} ${explainTopic.format.category}`,
          duration: 60,
          hasSpeech: true,
          isVisual: true,
          languageDetected: 'english',
        },
        explainThing: `${explainTopic.country} ${explainTopic.format.category}`,
        explainCategory: explainTopic.format.category,
        decision: {
          path: 'explain',
          confidence: 90,
          reasoning: `Popular ${explainTopic.format.category} from ${explainTopic.country}`,
        },
        outputDir: config.paths.explainers,
        ai: this.ai,
        config: config,
      });

      this.logger.success(`✅ Explainer created: ${explainResult.title}`);

      // Step 3a: Upload explainer to YouTube
      if (explainResult.outputPath && fs.existsSync(explainResult.outputPath)) {
        const uploadResult = await this._uploadToYouTube({
          videoPath: explainResult.outputPath,
          title: explainResult.title,
          description: `${explainResult.title}\n\n🌍 Bringing the world to you\n\n#${explainTopic.country} #${explainTopic.format.category} #shorts #worldwidewebster`,
          type: 'shorts',
          country: explainTopic.country,
          tags: ['mr worldwidewebster', 'shorts', explainTopic.country.toLowerCase(), explainTopic.format.category],
        });

        if (uploadResult) {
          uploadedVideos.push({
            title: explainResult.title,
            url: uploadResult.url,
            videoId: uploadResult.videoId,
            type: 'explainer',
            country: explainTopic.country,
          });

          // Step 3b: Boost the uploaded video
          await this._boostVideo(uploadResult.url, 75);
        }
      } else {
        this.logger.warn('No output video file found — skipping YouTube upload');
      }
    } catch (error) {
      this.logger.error(`Explainer pipeline failed: ${error.message}`);
      errors.push(`Explainer: ${error.message}`);
    }

    // Step 4: Update memory
    channelMemory.totalVideosPosted = (channelMemory.totalVideosPosted || 0) + (uploadedVideos.length > 0 ? 1 : 0);
    channelMemory.lastCountryUsed = explainTopic.country;

    if (!channelMemory.countriesUsedThisWeek) {
      channelMemory.countriesUsedThisWeek = [];
    }
    channelMemory.countriesUsedThisWeek.push(explainTopic.country);
    if (channelMemory.countriesUsedThisWeek.length > 14) {
      channelMemory.countriesUsedThisWeek = channelMemory.countriesUsedThisWeek.slice(-14);
    }

    // Update trending log
    const trendingLog = this.memory['trending-log'];
    trendingLog.lastUpdated = new Date().toISOString();
    trendingLog.trends = [
      ...stepsArray.map(s => ({
        country: 'web',
        trend: s.result?.substring(0, 200) || 'Researched web trends',
        timestamp: new Date().toISOString(),
      })),
      ...trendingLog.trends.slice(0, 50),
    ];

    // Save all memory
    this._saveMemory('channel-memory', channelMemory);
    this._saveMemory('trending-log', trendingLog);
    this._saveMemory('content-history', {
      videos: [
        ...(this.memory['content-history']?.videos || []),
        ...uploadedVideos.map(v => ({
          title: v.title,
          type: v.type,
          country: v.country,
          url: v.url,
          createdAt: new Date().toISOString(),
        })),
      ].slice(-100),
    });

    // Step 5: Send Discord daily summary
    await this._sendDiscordNotification('daily', {
      videos: uploadedVideos,
      countries: channelMemory.countriesUsedThisWeek,
      totalVideos: channelMemory.totalVideosPosted,
      errors: errors,
    });

    // Summary
    this.logger.header('DAILY SUMMARY');
    this.logger.info(`Videos uploaded: ${uploadedVideos.length}`);
    if (uploadedVideos.length > 0) {
      uploadedVideos.forEach(v => this.logger.info(`  📺 ${v.title} → ${v.url}`));
    }
    if (errors.length > 0) {
      this.logger.warn(`Errors: ${errors.length}`);
      errors.forEach(e => this.logger.warn(`  ❌ ${e}`));
    }

    return { explainResult, uploadedVideos, errors };
  }

  /**
   * MODE: weekly — Create 1 long-form video
   */
  async runWeekly(customTopic) {
    this.logger.header('📺 WEEKLY: Long-Form Video');

    const topic = customTopic || this._pickWeeklyTopic();
    this.logger.info(`Topic: ${topic}`);

    // Have Hermes research and write a script
    const result = await this.agent.run(
      `Create a long-form YouTube video script for Mr. WorldWideWebster.

TOPIC: ${topic}

Requirements:
- 5-10 minutes of content
- Compare/contrast the topic across different countries
- Include interesting facts that would surprise an international audience
- End with a call to action

Output the full script as text.`,
      { verbose: false, maxSteps: 4 }
    );

    // Save the script
    const scriptsDir = config.paths.scripts;
    if (!fs.existsSync(scriptsDir)) fs.mkdirSync(scriptsDir, { recursive: true });

    const scriptPath = path.join(scriptsDir, `weekly_${Date.now()}_script.txt`);
    fs.writeFileSync(scriptPath, result.result || 'Script generation placeholder');

    // Update memory
    const channelMemory = this.memory['channel-memory'];
    channelMemory.totalVideosPosted = (channelMemory.totalVideosPosted || 0) + 1;
    this._saveMemory('channel-memory', channelMemory);

    // Send Discord weekly report
    await this._sendDiscordNotification('weekly', {
      weekRange: `Week of ${new Date().toLocaleDateString()}`,
      videos: [{ title: topic, views: 'New' }],
      countries: channelMemory.countriesUsedThisWeek || [],
      stats: {
        'Total Videos': channelMemory.totalVideosPosted,
        'Countries Covered': (channelMemory.countriesUsedThisWeek || []).length,
        'Script': 'Generated',
      },
    });

    this.logger.success(`✅ Weekly video script created: ${topic}`);
    this.logger.info(`Script: ${scriptPath}`);

    return { topic, scriptPath, result };
  }

  /**
   * Pick a weekly topic based on what's trending
   */
  _pickWeeklyTopic() {
    const weeklyTopics = [
      'Street Food from Every Continent — What Each Country Eats',
      'How 10 Different Countries React to the Same Music',
      'US English vs UK English vs Australian English — Who Says It Better?',
      'The Biggest Internet Censorship Differences Around the World',
      'How Different Countries Celebrate the Same Holiday',
      'School Lunch Around the World — Which Country Does It Best?',
      'Public Transportation in Tokyo vs London vs NYC',
      'The Most Popular Social Media App in Every Country',
      'How Different Countries Handle Weather Disasters',
      'Viral Dances from Around the World — Where Did They Start?',
      'What Dating Looks Like in 10 Different Countries',
      'The Most Expensive and Cheapest Countries to Live In',
      'How Different Countries Use AI in Daily Life',
      'The Biggest Stadiums Around the World',
      'National Animals and What They Say About Each Country',
    ];
    return weeklyTopics[Math.floor(Math.random() * weeklyTopics.length)];
  }

  /**
   * MODE: review — Midnight self-improvement
   */
  async runReview() {
    this.logger.header('🌙 MIDNIGHT: Self-Improvement Review');

    const channelMemory = this.memory['channel-memory'];
    const trendingLog = this.memory['trending-log'];

    const stats = {
      totalVideosPosted: channelMemory.totalVideosPosted || 0,
      countriesUsedThisWeek: channelMemory.countriesUsedThisWeek || [],
      bestPerformingFormats: channelMemory.bestPerformingFormats || [],
      trendsFound: trendingLog.trends?.length || 0,
    };

    this.logger.info(`Current stats: ${JSON.stringify(stats)}`);

    // Run the self-improvement agent
    const result = await this.agent.run(
      `You are the SELF-IMPROVEMENT module for Mr. WorldWideWebster YouTube channel.

CHANNEL IDENTITY: "Mr. WorldWideWebster" — shows Americans what's trending around the world.
Content types: Clip (viral moments), Voiceover (translated), Explain ("What is this...?"), Compare (US vs UK etc.)

CURRENT STATE:
- Total videos: ${stats.totalVideosPosted}
- Countries covered: ${JSON.stringify(stats.countriesUsedThisWeek)}
- Best formats: ${JSON.stringify(stats.bestPerformingFormats)}

YOUR TASKS:

1. ANALYZE the content strategy. What countries should we focus on more? What types of content work best for Shorts?

2. IMPROVE TITLE STRATEGY — Save better title formulas:
   Read: mr-worldwidewebster/memory/channel-memory.json
   Update: "titleFormulas" array with better templates
   Good formulas include: "What is this [thing]? ([country] edition)", "[country]'s favorite [category]", etc.

3. IMPROVE POSTING SCHEDULE — If we have performance data, suggest better upload times:
   Read: .github/workflows/daily-create.yml
   The cron is at: '0 6 * * *' (6 AM)

4. CREATE NEW SKILLS — Save reusable scraping strategies:
   Use the create_skill tool to save skills like:
   - "scrape-bilibili-trending": Steps to find trending Chinese content
   - "scrape-african-trends": Steps to find African viral content
   - "scrape-japanese-trends": Steps to find Japanese trends

5. SAVE LEARNINGS — Update channel-memory.json with:
   - New title formulas that worked
   - Countries to prioritize
   - Content types to focus on

IMPORTANT: Actually READ the existing files before editing them.
Use the tools: read_file, write_file, create_skill, list_skills.`,
      { verbose: true, maxSteps: 10 }
    );

    this.logger.success(`✅ Self-improvement completed: ${result.stepsCount} steps`);

    // Reset weekly countries counter at midnight
    channelMemory.countriesUsedThisWeek = [];
    this._saveMemory('channel-memory', channelMemory);

    // Send Discord review summary
    await this._sendDiscordNotification('daily', {
      videos: [],
      countries: [],
      totalVideos: stats.totalVideosPosted,
      errors: [],
      title: '🌙 Midnight Review Complete',
    });

    return result;
  }

  /**
   * Main entry point
   */
  async run() {
    await this.initialize();

    const args = process.argv.slice(2);
    const modeIndex = args.indexOf('--mode');
    const mode = modeIndex !== -1 ? args[modeIndex + 1] : 'daily';

    const topicIndex = args.indexOf('--topic');
    const customTopic = topicIndex !== -1 ? args.slice(topicIndex + 1).join(' ') : null;

    switch (mode) {
      case 'daily':
        await this.runDaily();
        break;
      case 'weekly':
        await this.runWeekly(customTopic);
        break;
      case 'review':
        await this.runReview();
        break;
      default:
        console.log(`Unknown mode: ${mode}. Use: daily, weekly, or review`);
        process.exit(1);
    }

    this.logger.success(`🎉 ${mode.toUpperCase()} pipeline completed successfully`);
  }
}

// ─── Start ──────────────────────────────────────────────────────────────

const runner = new GitHubActionsRunner();
runner.run().catch(error => {
  console.error(`\n❌ Fatal error: ${error.message}`);
  process.exit(1);
});