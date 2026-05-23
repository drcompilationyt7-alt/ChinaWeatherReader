#!/usr/bin/env node

/**
 * Mr. WorldWideWebster — GitHub Actions Runner
 *
 * Entry point for GitHub Actions workflows. Handles:
 * - Loading/storing persistent memory via git commits
 * - Running Hermes Agent for web scraping (no APIs needed)
 * - Creating daily shorts (download+trim clips, explainers, landscapes)
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

    // Initialize AI — await async initialization (TTS provider, etc.)
    this.ai = new AIService();
    await this.ai.waitForInit();

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
        lastContentType: '',
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
   * Download a trending video, trim to short, and upload to YouTube
   * This content type uses the video's original audio — no TTS needed
   *
   * AI chooses the best platform based on the trend type and region:
   * - Bilibili/Douyin/Rednote for Chinese trends
   * - TikTok for global short-form trends
   * - Instagram Reels for lifestyle/fashion/travel
   * - YouTube for longer form or specific searches
   */
  async _createClipShort() {
    const { execSync } = require('child_process');
    const { TrendingVideoFinder } = require('../sourcing/trending-video-finder');

    // Let AI decide which platform to search based on current trends
    this.logger.info('Asking AI which platform to search for trending content...');

    let platformChoice;
    let searchQuery;

    try {
      // Ask AI to recommend platform and search query based on what's trending globally
      // Note: Hermes CLI with Ollama may produce imperfect JSON, so we add robust parsing
      const aiRecommendation = await this.agent.run(
        `Mr. WorldWideWebster needs to find a viral video RIGHT NOW for a YouTube Short.
        
        Current date: ${new Date().toISOString()}
        Channel mission: "Burst your bubble. Escape the algorithm. See the internet the world sees."
        
        Based on what's trending globally TODAY, recommend:
        1. Which platform to search (choose ONE): bilibili, douyin, tiktok, instagram, rednote, youtube
           - For Chinese trends: bilibili, douyin, or rednote
           - For global Gen-Z trends: tiktok
           - For lifestyle/travel/fashion: instagram
           - For specific searches: youtube
        2. What search query to use (in the language of that platform)

        CRITICAL: Return ONLY valid JSON in this exact format:
        {"platform": "tiktok", "query": "viral dance challenge 2026", "reason": "trending in Japan right now"}

        Do NOT include any other text, explanations, or markdown formatting.`,
        { verbose: false, maxSteps: 2 }
      );

      // Parse AI recommendation with enhanced error handling
      let parsed;
      try {
        const output = aiRecommendation.output || '';
        
        // Try to extract JSON from the output (Hermes/Ollama may wrap it in text)
        const jsonMatch = output.match(/\{[\s\S]*\}/);
        if (jsonMatch) {
          parsed = JSON.parse(jsonMatch[0]);
        } else {
          // Fallback: try parsing the entire output as JSON
          parsed = JSON.parse(output);
        }
      } catch (parseError) {
        this.logger.warn(`JSON parse failed: ${parseError.message}`);
        throw new Error('AI output was not valid JSON');
      }

      if (parsed && parsed.platform && parsed.query) {
        platformChoice = parsed.platform.toLowerCase();
        searchQuery = parsed.query;
        this.logger.info(`AI recommends: ${platformChoice} — "${searchQuery}" (${parsed.reason || 'no reason given'})`);
      } else {
        throw new Error('AI did not return valid platform/query');
      }
    } catch (error) {
      this.logger.warn(`AI recommendation failed: ${error.message}, using random selection`);
      // Fallback to random selection
      const platforms = ['youtube', 'bilibili', 'tiktok', 'instagram', 'douyin', 'rednote'];
      platformChoice = platforms[Math.floor(Math.random() * platforms.length)];

      const queriesByPlatform = {
        youtube: ['funny fail compilation 2026 short', 'beautiful nature drone 4k', 'satisfying video no music'],
        bilibili: ['热门视频', '搞笑合集', '美食制作'],
        tiktok: ['viral dance 2026', 'funny moments', 'cooking hacks'],
        instagram: ['reels viral', 'travel reels', 'food reels'],
        douyin: ['热门', '搞笑', '美食'],
        rednote: ['热门笔记', '旅行分享', '美食推荐'],
      };
      searchQuery = queriesByPlatform[platformChoice][Math.floor(Math.random() * queriesByPlatform[platformChoice].length)];
    }

    try {
      this.logger.info(`Searching ${platformChoice} for: "${searchQuery}"`);

      let videoTitle = searchQuery;
      let videoUrl = null;
      
      // Use direct Puppeteer scraping for all platforms (more reliable than Hermes for this task)
      const finder = new TrendingVideoFinder();
      const results = await finder.findTrendingVideos(platformChoice, searchQuery);
      await finder.destroy();
      
      if (results.length === 0) {
        throw new Error(`No videos found on ${platformChoice}`);
      }
      
      // Pick a random video from results
      const randomVideo = results[Math.floor(Math.random() * results.length)];
      videoUrl = randomVideo.url;
      videoTitle = randomVideo.title || searchQuery;
      this.logger.info(`Found video: ${videoTitle.substring(0, 60)}...`);

      if (!videoUrl || !videoUrl.startsWith('http')) throw new Error('Invalid video URL');

      this.logger.info(`Downloading: "${videoTitle}"`);
      
      const { UniversalDownloader } = require('../sourcing/universal-downloader');
      const downloader = new UniversalDownloader();
      
      const downloadResult = await downloader.download(videoUrl, {
        outputDir: config.paths.clips,
        maxHeight: 720,
      });

      if (!downloadResult.success || !downloadResult.filePath) throw new Error('Download failed');

      const clipPipeline = require('../clipping/clip-pipeline');
      const shortPath = path.join(config.paths.clips, `clip_upload_${Date.now()}.mp4`);
      
      const trimmedPath = await clipPipeline.trimToShort({
        videoPath: downloadResult.filePath,
        startTime: 3,
        duration: 30,
        outputPath: shortPath,
      });

      if (!trimmedPath || !fs.existsSync(trimmedPath)) throw new Error('Trim produced no output');

      const uploadResult = await this._uploadToYouTube({
        videoPath: trimmedPath,
        title: (downloadResult.title || videoTitle).substring(0, 100),
        description: `🔥 ${downloadResult.title || videoTitle}\n\n🌍 Bringing the world to you`,
        type: 'shorts',
        tags: ['mr worldwidewebster', 'shorts', 'trending', 'viral', platformChoice],
      });

      if (uploadResult) {
        await this._boostVideo(uploadResult.url, 50);
        return {
          title: (downloadResult.title || videoTitle).substring(0, 100),
          url: uploadResult.url,
          type: 'clip',
          platform: platformChoice,
        };
      }
    } catch (error) {
      this.logger.warn(`Clip creation failed: ${error.message}`);
    }
    return null;
  }

  /**
   * MODE: daily — Create content, upload, boost, notify
   * Produces multiple content types: clip, landscape, explainer
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

    // Normalize steps (HermesCLI returns number, HermesAgent returns array)
    const stepsArray = Array.isArray(trendsResult.steps) ? trendsResult.steps : [];
    this.logger.success(`Found ${trendsResult.stepsCount || stepsArray.length} trending items`);

    // Step 2: Download + upload a trending clip (original audio, no TTS needed)
    this.logger.info('Step 2: Downloading trending clip...');
    const clipResult = await this._createClipShort();
    if (clipResult) {
      uploadedVideos.push(clipResult);
      this.logger.success(`✅ Clip uploaded: ${clipResult.title}`);
    } else {
      errors.push('Clip: No video uploaded');
    }

    // Step 3: Generate and upload an explainer video (AI script + optional TTS)
    this.logger.info('Step 3: Creating explainer video...');

    const channelMemory = this.memory['channel-memory'];
    const usedCountries = channelMemory.countriesUsedThisWeek || [];

    const allCountries = [
      'Nigeria', 'Japan', 'Germany', 'Australia', 'France', 'Brazil',
      'Thailand', 'India', 'Mexico', 'UK', 'South Korea', 'Egypt',
      'Italy', 'Spain', 'South Africa', 'Argentina', 'Turkey', 'Vietnam'
    ];

    const availableCountries = allCountries.filter(c => !usedCountries.includes(c));
    const country = availableCountries[Math.floor(Math.random() * availableCountries.length)] || 
                    allCountries[Math.floor(Math.random() * allCountries.length)];

    const explainFormats = [
      { category: 'food', prompt: 'What is this food?' },
      { category: 'music', prompt: 'What is this music genre?' },
      { category: 'dance', prompt: 'What is this dance?' },
      { category: 'trend', prompt: 'What is this trend?' },
      { category: 'culture', prompt: 'What is this tradition?' },
    ];

    const format = explainFormats[Math.floor(Math.random() * explainFormats.length)];
    const explainTopic = { country, format, title: `${format.prompt} (${country} edition) 🌍` };

    this.logger.info(`Creating explainer: "${explainTopic.title}"`);

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

      // Upload explainer to YouTube if video file exists
      const uploadVideoPath = explainResult.videoFile;
      if (uploadVideoPath && fs.existsSync(uploadVideoPath)) {
        const uploadResult = await this._uploadToYouTube({
          videoPath: uploadVideoPath,
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

          await this._boostVideo(uploadResult.url, 75);
        }
      } else {
        this.logger.warn('No output video file found — skipping explainer upload');
      }
    } catch (error) {
      this.logger.error(`Explainer pipeline failed: ${error.message}`);
      errors.push(`Explainer: ${error.message}`);
    }

    // Step 4: Update memory
    channelMemory.totalVideosPosted = (channelMemory.totalVideosPosted || 0) + uploadedVideos.length;
    channelMemory.lastCountryUsed = explainTopic?.country || 'Global';
    channelMemory.lastContentType = uploadedVideos.length > 0 ? uploadedVideos[0].type : 'none';

    if (!channelMemory.countriesUsedThisWeek) {
      channelMemory.countriesUsedThisWeek = [];
    }
    if (explainTopic?.country && !channelMemory.countriesUsedThisWeek.includes(explainTopic.country)) {
      channelMemory.countriesUsedThisWeek.push(explainTopic.country);
    }
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
          type: v.type || 'shorts',
          country: v.country || 'Global',
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
      uploadedVideos.forEach(v => this.logger.info(`  📺 [${v.type}] ${v.title} → ${v.url}`));
    }
    if (errors.length > 0) {
      this.logger.warn(`Errors: ${errors.length}`);
      errors.forEach(e => this.logger.warn(`  ❌ ${e}`));
    }

    return { explainResult, uploadedVideos, errors };
  }

  /**
   * MODE: weekly — Create 2 landscape compilation videos
   * Delegates to the landscape/weekly-runner.js pipeline which produces
   * actual 1920×1080 video files from clips + TTS + music.
   * This replaces the old long-form slideshow approach.
   */
  async runWeekly(customTopic) {
    this.logger.header('🎬 WEEKLY: Landscape Compilation Videos');

    try {
      const { WeeklyRunner } = require('../landscape/weekly-runner');
      const runner = new WeeklyRunner();

      const options = {
        count: '2',
        type: 'auto',
        'skip-research': 'false',
      };

      // If a custom topic was passed, override the type to compilation with that topic
      if (customTopic) {
        options.type = 'compilation';
        // Store the topic for the runner to use (passed via environment or other means)
        process.env.MWW_CUSTOM_TOPIC = customTopic;
        this.logger.info(`Custom topic: ${customTopic}`);
      }

      const result = await runner.run(options);

      // Upload created videos to YouTube
      const uploadedVideos = [];
      if (result.results && result.results.length > 0) {
        for (const videoResult of result.results) {
          if (videoResult.videoPath && fs.existsSync(videoResult.videoPath)) {
            const uploadResult = await this._uploadToYouTube({
              videoPath: videoResult.videoPath,
              title: videoResult.title || customTopic || 'Weekly Landscape Video',
              description: `${videoResult.title}\n\n🌍 Bringing the world to you.\n\nFollow Mr. WorldWideWebster for more global content!\n\n#global #travel #culture #landscape`,
              type: 'landscape',
              tags: ['mr worldwidewebster', 'global', 'travel', 'culture', 'landscape'],
            });
            if (uploadResult) {
              uploadedVideos.push({
                title: videoResult.title,
                url: uploadResult.url,
                type: 'landscape',
              });
            }
          }
        }
      }

      // Update memory
      const channelMemory = this.memory['channel-memory'];
      channelMemory.totalVideosPosted = (channelMemory.totalVideosPosted || 0) + uploadedVideos.length;
      this._saveMemory('channel-memory', channelMemory);

      // Send Discord weekly report
      await this._sendDiscordNotification('weekly', {
        weekRange: `Week of ${new Date().toLocaleDateString()}`,
        videos: uploadedVideos.map(v => ({ title: v.title, views: 'New' })),
        countries: channelMemory.countriesUsedThisWeek || [],
        stats: {
          'Total Videos': channelMemory.totalVideosPosted,
          'Countries Covered': (channelMemory.countriesUsedThisWeek || []).length,
          'Videos Created': result.succeeded || 0,
        },
      });

      this.logger.success(`✅ Weekly landscape pipeline finished: ${result.succeeded} videos`);
      return { topic: customTopic || 'auto-generated', videos: uploadedVideos, result };
    } catch (error) {
      this.logger.error(`Weekly pipeline failed: ${error.message}`);
      // Fallback: just create a script like before
      this.logger.info('Falling back to script-only generation...');
      const topic = customTopic || this._pickWeeklyTopic();
      const scriptsDir = config.paths.scripts;
      if (!fs.existsSync(scriptsDir)) fs.mkdirSync(scriptsDir, { recursive: true });
      const scriptPath = path.join(scriptsDir, `weekly_${Date.now()}_script.txt`);
      fs.writeFileSync(scriptPath, `Weekly topic: ${topic}\nLandscape pipeline was unavailable.`);
      return { topic, scriptPath, error: error.message };
    }
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
   * MODE: review — Midnight self-improvement with video-before-commit loop
   *
   * Flow:
   * 1. Performance Analysis — analyze YouTube analytics + channel memory
   * 2. Strategy Design — Hermes agent reads code + performance, proposes changes
   * 3. Brand Validation — CodeEvolver + BrandGuardian validate proposals
   * 4. Apply Code Edits — changes saved to disk (not committed yet)
   * 5. Create & Post Video — test video using NEW strategy, uploaded to YouTube
   * 6. Commit Everything — git add + commit + push (code + video metadata)
   */
  async runReview() {
    this.logger.header('🌙 MIDNIGHT: Self-Improvement Review + Video-Before-Commit');

    const channelMemory = this.memory['channel-memory'];
    const trendingLog = this.memory['trending-log'];
    const { CodeEvolver } = require('../hermes-agent/code-evolver');
    const evolver = new CodeEvolver({ repoRoot: path.resolve(__dirname, '..') });

    const stats = {
      totalVideosPosted: channelMemory.totalVideosPosted || 0,
      countriesUsedThisWeek: channelMemory.countriesUsedThisWeek || [],
      bestPerformingFormats: channelMemory.bestPerformingFormats || [],
      trendsFound: trendingLog.trends?.length || 0,
    };

    this.logger.info(`Current stats: ${JSON.stringify(stats)}`);

    // ──────────────── STEP 1: Performance Analysis ────────────────
    this.logger.header('STEP 1: Performance Analysis');
    this.logger.info(`Total videos: ${stats.totalVideosPosted}, Countries this week: ${stats.countriesUsedThisWeek.length}`);

    // Read performance metrics
    let perfData = { totalVideosTracked: 0, recommendations: ['No performance data yet'] };
    try {
      const perfPath = path.join(this.memoryPath, 'performance-metrics.json');
      if (fs.existsSync(perfPath)) {
        perfData = JSON.parse(fs.readFileSync(perfPath, 'utf8'));
      }
    } catch {
      this.logger.warn('Could not read performance metrics');
    }

    // ──────────────── STEP 2: Strategy Design via Hermes Agent ────────────────
    this.logger.header('STEP 2: Hermes Agent Strategy Design (with code editing tools)');

    const brandGuidelines = evolver.brandGuardian.getGuidelines();

    // Run the Hermes agent with SELF-IMPROVEMENT tools that can edit code + create videos
    const result = await this.agent.run(
      `You are the SELF-IMPROVEMENT module for Mr. WorldWideWebster YouTube channel.

CHANNEL IDENTITY: "Mr. WorldWideWebster" — shows people what's trending around the world.
Content types: Clip (viral moments), Voiceover (translated), Explain ("What is this...?"), AI Create (comparisons/original content)

CURRENT STATE:
- Total videos posted: ${stats.totalVideosPosted}
- Countries covered this week: ${JSON.stringify(stats.countriesUsedThisWeek)}
- Best performing formats: ${JSON.stringify(stats.bestPerformingFormats)}
- Performance recommendations: ${JSON.stringify(perfData.recommendations || [])}
- Trends tracked: ${stats.trendsFound}

BRAND GUIDELINES (read these carefully):
- Allowed content types: ${brandGuidelines.allowedContentTypes.join(', ')}
- Preferred title formulas: ${JSON.stringify(brandGuidelines.titleRules.preferredFormulas)}
- Max title length: ${brandGuidelines.titleRules.maxLength} chars
- Must avoid: ${JSON.stringify(brandGuidelines.titleRules.forbiddenPatterns)}
- Ethical bounds: ${JSON.stringify(brandGuidelines.ethicalBounds)}
- Voice tone: ${brandGuidelines.voice.tone}

YOUR WORKFLOW (follow this order):

PHASE 1: ANALYZE
1. Call analyze_performance to see current channel stats
2. Call get_brand_guidelines to understand brand rules
3. Read memory/channel-memory.json to see full state
4. Read memory/performance-metrics.json to see what's working

PHASE 2: IMPROVE CODE
5. Call edit_source_code (dryRun:true first!) to preview changes:
   a) Update titleFormulas in memory/channel-memory.json with better templates
   b) Improve priority countries in config/brand-guidelines.json if needed
   c) Optimize posting schedule in memory/channel-memory.json
   d) Update decision-engine.js confidence thresholds if needed
   e) Create new skills with create_skill tool
6. After dry-run looks good, call edit_source_code (dryRun:false) to apply

PHASE 3: CREATE TEST VIDEO
7. Call create_and_post_video with a topic that uses the NEW strategy
   - Pick a country not covered recently
   - Use one of the new title formulas
   - Content type should be one of: explain, ai_create, clip
   - This uploads to YouTube to PROVE the improvement works

PHASE 4: COMMIT
8. Call commit_improvements with descriptive message
9. Report what was accomplished

IMPORTANT: Follow the phases in order. Don't skip phase 3 — the video must be created and posted before committing!`,
      { verbose: true, maxSteps: 15 }
    );

    this.logger.success(`✅ Hermes agent completed: ${result.stepsCount} steps`);

    // ──────────────── POST-AGENT: Commit any uncommitted changes ────────────────
    // If the agent didn't call commit_improvements, do it now
    const changeLog = evolver.getChangeLog();
    if (changeLog.length > 0) {
      this.logger.info(`Agent left ${changeLog.length} uncommitted changes — committing now`);
      const commitResult = evolver.commitChanges('🌙 Midnight self-improvements (auto-commit)');
      this.logger.info(`Commit result: ${commitResult.success ? 'Success' : 'Failed'}`);
    } else {
      this.logger.info('No pending changes to commit');
    }

    // Reset weekly countries counter at midnight
    channelMemory.countriesUsedThisWeek = [];
    this._saveMemory('channel-memory', channelMemory);

    // Update performance metrics with any video we created
    try {
      const videoHistoryPath = path.join(this.memoryPath, 'self-improvement-videos.json');
      if (fs.existsSync(videoHistoryPath)) {
        const videoHistory = JSON.parse(fs.readFileSync(videoHistoryPath, 'utf8'));
        perfData.totalVideosTracked = (perfData.totalVideosTracked || 0) + videoHistory.length;
        perfData.lastUpdated = new Date().toISOString();
        perfData.lastVideo = videoHistory[videoHistory.length - 1] || null;
        this._saveMemory('performance-metrics', perfData);
        this.logger.info(`Updated performance metrics (${videoHistory.length} self-improvement videos tracked)`);
      }
    } catch (error) {
      this.logger.warn(`Failed to update performance metrics: ${error.message}`);
    }

    // Send Discord review summary with details about what changed
    const changeSummary = changeLog
      .map(c => `📝 ${c.filePath}: ${c.description} (${c.changes} changes)`)
      .join('\n');

    await this._sendDiscordNotification('daily', {
      videos: [],
      countries: [],
      totalVideos: stats.totalVideosPosted,
      errors: [],
      title: '🌙 Midnight Review Complete',
      message: `Changes made:\n${changeSummary || '  No code changes — strategy analysis only'}\n\nReview cycle finished.`,
    });

    return {
      ...result,
      codeChanges: changeLog,
      evolverLog: changeLog,
    };
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

// ─── Global Error Handlers ──────────────────────────────────────────────
// Prevent EISDIR and other internal library stream errors from crashing GH Actions.
// These are non-fatal: the pipeline should continue and report errors gracefully.
process.on('uncaughtException', (error) => {
  console.error(`⚠️ [Global] Uncaught exception (non-fatal): ${error.message}`);
  console.error(error.stack?.split('\n').slice(0, 4).join('\n'));
});

process.on('unhandledRejection', (reason) => {
  console.error(`⚠️ [Global] Unhandled rejection (non-fatal): ${reason?.message || reason}`);
});

// ─── Start ──────────────────────────────────────────────────────────────

const runner = new GitHubActionsRunner();
runner.run().catch(error => {
  console.error(`\n❌ Fatal error: ${error.message}`);
  process.exit(1);
});