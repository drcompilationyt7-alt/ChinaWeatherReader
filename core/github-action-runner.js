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

  /**
   * Validate that required API keys are present and not placeholders
   */
  _validateKeys() {
    const openrouterKey = process.env.OPENROUTER_API_KEY || '';
    const openaiKey = process.env.OPENAI_API_KEY || '';

    if (!openrouterKey && !openaiKey) {
      this.logger.warn('No API keys found — AI features will fail');
      this.logger.warn('Set OPENROUTER_API_KEY in GitHub Secrets');
      return;
    }

    // Check for placeholder keys
    const placeholders = ['sk-your-', 'your-', 'placeholder', 'sk-or-your'];
    for (const key of [openrouterKey, openaiKey]) {
      if (!key) continue;
      for (const placeholder of placeholders) {
        if (key.includes(placeholder)) {
          this.logger.warn(`⚠️  Placeholder API key detected (contains "${placeholder}")`);
          this.logger.warn('  Replace with a real key in GitHub Secrets');
          break;
        }
      }
    }

    this.logger.info(`OpenRouter key length: ${openrouterKey.length} chars`);
    this.logger.info(`OpenAI key present: ${openaiKey.length > 0}`);
  }

  /**
   * Run a quick Hermes smoke test to verify the CLI actually produces output
   */
  async _smokeTestHermes() {
    if (!this.agent || !this.agent.isAvailable || !this.agent.isAvailable()) {
      this.logger.warn('Hermes not available for smoke test');
      return false;
    }

    this.logger.info('Running Hermes smoke test...');
    try {
      const result = await this.agent.run(
        'Reply ONLY with the word: hello',
        { verbose: false, maxSteps: 1 }
      );

      const output = (result.output || '').trim();
      const hasOutput = output.length > 0;
      
      // Check for critical errors that indicate failure
      const hasCriticalError = 
        output.toLowerCase().includes('traceback') ||
        output.includes('AuthError') ||
        output.toLowerCase().includes('error:') ||
        output.includes('No inference provider configured');

      if (hasOutput && !hasCriticalError) {
        this.logger.info(`✅ Hermes smoke test passed: "${output.substring(0, 50)}"`);
        return true;
      } else if (hasCriticalError) {
        this.logger.error('⚠️  Hermes smoke test failed with critical error');
        this.logger.error(`  Error in output: ${output.substring(0, 300)}`);
        return false;
      } else {
        this.logger.warn('⚠️  Hermes smoke test returned empty output');
        this.logger.warn('  Hermes will still be used, but may produce no results');
        return false;
      }

    } catch (error) {
      this.logger.warn(`Hermes smoke test failed: ${error.message}`);
      return false;
    }
  }


  /**
   * Extract URLs from text using a broad regex
   * Catches URLs with or without protocol, various formats
   * Filters out obvious placeholder/fake URLs
   */
  _extractUrls(text) {
    if (!text) return [];
    // Broad URL regex: http(s)://anything-not-whitespace-not-quote
    const urlRegex = /https?:\/\/[^\s"'<>(){}[\]\\^`|]+/gi;
    const matches = text.match(urlRegex);
    
    if (!matches) return [];
    
    // Filter out placeholder/fake URLs that Hermes might generate when it fails
    const validUrls = matches.filter(url => {
      // Skip URLs with obvious placeholder patterns
      if (url.includes('xxx')) return false;
      if (url.includes('abcdefg')) return false;
      if (url.includes('BV1xxx')) return false;
      
      // Skip YouTube URLs with invalid video ID patterns (must be 11 chars)
      const youtubeMatch = url.match(/youtube\.com\/watch\?v=([a-zA-Z0-9_-]+)/i);
      if (youtubeMatch) {
        const videoId = youtubeMatch[1];
        if (videoId.length !== 11) return false;
        if (/^(.)\1+$/.test(videoId)) return false;
      }
      
      // Skip Bilibili URLs with placeholder BV numbers
      const bilibiliMatch = url.match(/bilibili\.com\/video\/(BV[a-zA-Z0-9]+)/i);
      if (bilibiliMatch) {
        const bvId = bilibiliMatch[1];
        if (bvId.length < 10) return false;
        if (bvId.toLowerCase().includes('xxx')) return false;
      }
      
      return true;
    });
    
    return validUrls;
  }

  async initialize() {
    this.logger.header('🤖 Mr. WorldWideWebster — GitHub Actions');

    this._validateKeys();

    this.ai = new AIService();
    await this.ai.waitForInit();

    // PRIMARY: Official Hermes CLI from Nous Research
    const hermesCLI = new HermesCLIWrapper();
    if (hermesCLI.isAvailable()) {
      this.agent = hermesCLI;
      this.logger.success('✅ Using official Hermes CLI as primary agent');
      await this._smokeTestHermes();
    } else {
      this.logger.info('Falling back to built-in Hermes JS agent...');
      const { HermesAgentWithScraping } = require('../hermes-agent/agent-tools');
      this.agent = new HermesAgentWithScraping(this.ai);
      this.logger.info('Using built-in Hermes JS agent (custom code)');
    }

    this._loadMemory();

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

        if (contentHistory.videos.length > 200) {
          contentHistory.videos = contentHistory.videos.slice(-200);
        }
        this._saveMemory('content-history', contentHistory);
      }

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
   * PRIMARY: Search YouTube using yt-dlp directly (always works, no browser needed)
   */
  async _searchYouTubeWithYtDlp(query, maxResults = 5) {
    const { execSync } = require('child_process');
    this.logger.info(`Searching YouTube with yt-dlp: "${query}"`);

    try {
      const cmd = `yt-dlp --flat-playlist --dump-json "ytsearch${maxResults}:${query}" 2>/dev/null`;
      const output = execSync(cmd, { timeout: 30000, maxBuffer: 5 * 1024 * 1024 }).toString().trim();
      
      if (!output) {
        this.logger.warn('yt-dlp search returned no results');
        return [];
      }

      const results = output.split('\n').filter(Boolean).map(line => {
        try {
          const parsed = JSON.parse(line);
          return {
            url: `https://www.youtube.com/watch?v=${parsed.id}`,
            title: parsed.title || 'YouTube video',
            platform: 'youtube',
            country: 'Global',
          };
        } catch {
          return null;
        }
      }).filter(Boolean);

      this.logger.success(`yt-dlp found ${results.length} videos`);
      return results;
    } catch (error) {
      this.logger.warn(`yt-dlp search failed: ${error.message}`);
      return [];
    }
  }

  /**
   * Download a YouTube video by URL using yt-dlp, trim to short
   */
  async _downloadAndTrimYoutubeVideo(videoUrl, videoTitle) {
    const { execSync } = require('child_process');
    
    this.logger.info(`Downloading YouTube video: ${videoUrl}`);
    
    const outputDir = config.paths.clips;
    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }
    
    const outputTemplate = path.join(outputDir, `yt_${Date.now()}_%(id)s.%(ext)s`);
    
    try {
      const dlCmd = `yt-dlp -f "best[height<=720][ext=mp4]/best[height<=720]" -o "${outputTemplate}" "${videoUrl}" --no-playlist --max-filesize 50M 2>&1`;
      const dlOutput = execSync(dlCmd, { timeout: 120000, maxBuffer: 10 * 1024 * 1024 }).toString();
      
      // Find the downloaded file
      const files = fs.readdirSync(outputDir).filter(f => f.startsWith('yt_') && f.endsWith('.mp4'));
      const downloadedFile = files.sort((a, b) => 
        fs.statSync(path.join(outputDir, b)).mtimeMs - fs.statSync(path.join(outputDir, a)).mtimeMs
      )[0];
      
      if (!downloadedFile) {
        this.logger.warn('yt-dlp download produced no output file');
        return null;
      }
      
      const fullPath = path.join(outputDir, downloadedFile);
      this.logger.success(`✅ Downloaded: ${fullPath}`);
      
      // Trim to short (30 seconds starting at 3s)
      const shortPath = path.join(outputDir, `short_${Date.now()}.mp4`);
      const clipPipeline = require('../clipping/clip-pipeline');
      
      const trimmedPath = await clipPipeline.trimToShort({
        videoPath: fullPath,
        startTime: 3,
        duration: 30,
        outputPath: shortPath,
      });
      
      if (!trimmedPath || !fs.existsSync(trimmedPath)) {
        this.logger.warn('Trim produced no output');
        return null;
      }
      
      this.logger.success(`✅ Trimmed to short: ${trimmedPath}`);
      
      return {
        trimmedPath,
        title: (videoTitle || 'Trending video').substring(0, 100),
      };
    } catch (error) {
      this.logger.warn(`Download/trim failed: ${error.message}`);
      return null;
    }
  }

  /**
   * Batch search multiple trending topics concurrently using yt-dlp
   */
  async _batchSearchTrendingTopics() {
    const topics = [
      'viral dance 2026',
      'street food around the world',
      'satisfying video',
      'beautiful nature moments',
      'funny animal',
      'travel moments',
      'amazing talent',
      'cooking delicious food',
      'sports highlights',
      'music performance',
    ];
    
    const channelMemory = this.memory['channel-memory'] || {};
    const usedTopics = channelMemory.usedTopics || [];
    const available = topics.filter(t => !usedTopics.includes(t));
    
    const selected = available.length >= 3 
      ? available.sort(() => Math.random() - 0.5).slice(0, 3)
      : topics.sort(() => Math.random() - 0.5).slice(0, 3);
    
    this.logger.info(`Batch searching ${selected.length} topics: ${selected.join(', ')}`);
    
    const allResults = [];
    for (const topic of selected) {
      try {
        const results = await this._searchYouTubeWithYtDlp(topic, 3);
        for (const r of results) {
          allResults.push({ ...r, searchTopic: topic });
        }
      } catch (err) {
        this.logger.warn(`Search "${topic}" failed: ${err.message}`);
      }
    }
    
    if (!channelMemory.usedTopics) channelMemory.usedTopics = [];
    for (const topic of selected) {
      if (!channelMemory.usedTopics.includes(topic)) {
        channelMemory.usedTopics.push(topic);
      }
    }
    if (channelMemory.usedTopics.length > 20) {
      channelMemory.usedTopics = channelMemory.usedTopics.slice(-20);
    }
    this.memory['channel-memory'] = channelMemory;
    
    this.logger.success(`Batch search found ${allResults.length} total videos`);
    return allResults;
  }

  /**
   * MODE: daily — Create content using yt-dlp as primary source
   */
  async runDaily() {
    this.logger.header('🌅 DAILY: Content Creation + Upload + Boost');

    const errors = [];
    const uploadedVideos = [];

    // Step 1: Batch search trending topics via yt-dlp (PRIMARY)
    this.logger.info('Step 1: Searching trending content via yt-dlp...');
    
    const trendingResults = await this._batchSearchTrendingTopics();
    const foundUrls = trendingResults.map(r => ({
      url: r.url,
      title: r.title,
      topic: r.searchTopic,
    }));
    
    this.memory['trending-urls'] = foundUrls.map(u => u.url);
    
    if (foundUrls.length > 0) {
      this.logger.success(`✅ Found ${foundUrls.length} trending URLs via yt-dlp`);
    } else {
      this.logger.warn('No trending URLs found via yt-dlp');
    }
    
    // Also try Hermes for content IDEAS (non-blocking)
    if (this.agent && this.agent.isAvailable && this.agent.isAvailable()) {
      try {
        this.logger.info('Asking Hermes for content ideas (non-blocking)...');
        const trendsResult = await this.agent.run(
          `Give me 3 video content ideas for Mr. WorldWideWebster channel.
          Focus on countries: Japan, Nigeria, Brazil, India, Mexico.
          Just list 3 ideas, no URLs needed.`,
          { verbose: false, maxSteps: 2, timeout: 120000 }
        );
        if (trendsResult && trendsResult.output) {
          this.logger.info(`Hermes ideas: ${trendsResult.output.substring(0, 200)}`);
        }
      } catch (err) {
        this.logger.info(`Hermes research skipped: ${err.message}`);
      }
    }

    // Step 2: Download + upload a trending clip
    this.logger.info('Step 2: Creating trending clip short...');
    
    let clipResult = null;
    for (const video of foundUrls) {
      if (clipResult) break;
      
      this.logger.info(`Downloading: ${video.title.substring(0, 60)}...`);
      const downloadResult = await this._downloadAndTrimYoutubeVideo(video.url, video.title);
      
      if (downloadResult) {
        const uploadResult = await this._uploadToYouTube({
          videoPath: downloadResult.trimmedPath,
          title: downloadResult.title,
          description: `🔥 ${downloadResult.title}\n\n🌍 Bringing the world to you`,
          type: 'shorts',
          tags: ['mr worldwidewebster', 'shorts', 'trending', 'viral', 'youtube'],
        });
        
        if (uploadResult) {
          clipResult = { title: downloadResult.title, url: uploadResult.url, type: 'clip', platform: 'youtube' };
          await this._boostVideo(uploadResult.url, 50);
        }
      }
    }
    
    if (clipResult) {
      uploadedVideos.push(clipResult);
      this.logger.success(`✅ Clip uploaded: ${clipResult.title}`);
    } else {
      errors.push('Clip: No video could be downloaded/uploaded');
    }

    // Step 3: Create explainer video
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
    
    this.logger.info(`Searching YouTube for ${format.category} content from ${country}...`);
    
    let foundContentUrl = null;
    let foundContentPlatform = 'web';
    
    // Use yt-dlp as primary for explainer content too
    try {
      const ytResults = await this._searchYouTubeWithYtDlp(`${country} ${format.category}`, 3);
      if (ytResults.length > 0) {
        foundContentUrl = ytResults[0].url;
        foundContentPlatform = 'youtube';
        this.logger.info(`Found YouTube video for explainer: ${foundContentUrl}`);
      }
    } catch (err) {
      this.logger.warn(`Search for explainer failed: ${err.message}`);
    }
    
    // Optional: try Hermes for non-YouTube platforms
    if (!foundContentUrl && this.agent && this.agent.isAvailable && this.agent.isAvailable()) {
      try {
        this.logger.info('Asking Hermes for non-YouTube URL...');
        const hermesResult = await this.agent.run(
          `Find ONE viral video URL about ${format.category} from ${country}.
          Search TikTok, Bilibili, Douyin, or Instagram.
          Return ONLY the raw URL.`,
          { verbose: false, maxSteps: 1, timeout: 60000 }
        );
        if (hermesResult && hermesResult.output) {
          const urls = this._extractUrls(hermesResult.output);
          if (urls.length > 0) {
            foundContentUrl = urls[0];
            foundContentPlatform = urls[0].includes('bilibili') ? 'bilibili' : 
                                  urls[0].includes('tiktok') ? 'tiktok' : 'web';
            this.logger.info(`Hermes found URL: ${foundContentUrl}`);
          }
        }
      } catch (err) {
        this.logger.info(`Hermes search skipped: ${err.message}`);
      }
    }
    
    const explainTopic = {
      country, format,
      title: `${format.prompt} (${country} edition) 🌍`,
      hermesContent: foundContentUrl ? { platform: foundContentPlatform, url: foundContentUrl, query: `${country} ${format.category}` } : null
    };

    this.logger.info(`Creating explainer: "${explainTopic.title}"`);

    const explainPipeline = require('../explainer/explain-pipeline');
    let explainResult;
    try {
      explainResult = await explainPipeline.processExplain({
        sourceContent: {
          title: explainTopic.title,
          platform: foundContentPlatform,
          url: foundContentUrl || null,
          description: `Exploring ${explainTopic.country} ${explainTopic.format.category}`,
          duration: 60, hasSpeech: true, isVisual: true, languageDetected: 'english',
        },
        explainThing: `${explainTopic.country} ${explainTopic.format.category}`,
        explainCategory: explainTopic.format.category,
        decision: { path: 'explain', confidence: 90, reasoning: `Popular ${explainTopic.format.category} from ${explainTopic.country}` },
        outputDir: config.paths.explainers,
        ai: this.ai, config: config,
        hermesAgent: this.agent,
      });

      this.logger.success(`✅ Explainer created: ${explainResult.title}`);

      const uploadVideoPath = explainResult.videoFile;
      if (uploadVideoPath && fs.existsSync(uploadVideoPath)) {
        const uploadResult = await this._uploadToYouTube({
          videoPath: uploadVideoPath,
          title: explainResult.title,
          description: `${explainResult.title}\n\n🌍 Bringing the world to you\n\n#${explainTopic.country} #${explainTopic.format.category} #shorts #worldwidewebster`,
          type: 'shorts', country: explainTopic.country,
          tags: ['mr worldwidewebster', 'shorts', explainTopic.country.toLowerCase(), explainTopic.format.category],
        });

        if (uploadResult) {
          uploadedVideos.push({ title: explainResult.title, url: uploadResult.url, videoId: uploadResult.videoId, type: 'explainer', country: explainTopic.country });
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

    if (!channelMemory.countriesUsedThisWeek) channelMemory.countriesUsedThisWeek = [];
    if (explainTopic?.country && !channelMemory.countriesUsedThisWeek.includes(explainTopic.country)) {
      channelMemory.countriesUsedThisWeek.push(explainTopic.country);
    }
    if (channelMemory.countriesUsedThisWeek.length > 14) {
      channelMemory.countriesUsedThisWeek = channelMemory.countriesUsedThisWeek.slice(-14);
    }

    const trendingLog = this.memory['trending-log'];
    trendingLog.lastUpdated = new Date().toISOString();
    trendingLog.trends = [
      ...trendingResults.slice(0, 10).map(r => ({ country: 'Global', trend: r.title.substring(0, 200), timestamp: new Date().toISOString() })),
      ...trendingLog.trends.slice(0, 50),
    ];

    this._saveMemory('channel-memory', channelMemory);
    this._saveMemory('trending-log', trendingLog);
    this._saveMemory('content-history', {
      videos: [
        ...(this.memory['content-history']?.videos || []),
        ...uploadedVideos.map(v => ({ title: v.title, type: v.type || 'shorts', country: v.country || 'Global', url: v.url, createdAt: new Date().toISOString() })),
      ].slice(-100),
    });

    await this._sendDiscordNotification('daily', {
      videos: uploadedVideos, countries: channelMemory.countriesUsedThisWeek,
      totalVideos: channelMemory.totalVideosPosted, errors: errors,
    });

    this.logger.header('DAILY SUMMARY');
    this.logger.info(`Videos uploaded: ${uploadedVideos.length}`);
    if (uploadedVideos.length > 0) {
      uploadedVideos.forEach(v => this.logger.info(`  📺 [${v.type}] ${v.title} → ${v.url}`));
    }
    if (errors.length > 0) {
      errors.forEach(e => this.logger.warn(`  ❌ ${e}`));
    }

    return { explainResult, uploadedVideos, errors };
  }

  async runWeekly(customTopic) {
    this.logger.header('🎬 WEEKLY: Landscape Compilation Videos');

    try {
      const { WeeklyRunner } = require('../landscape/weekly-runner');
      const runner = new WeeklyRunner();

      const options = { count: '2', type: 'auto', 'skip-research': 'false' };

      if (customTopic) {
        options.type = 'compilation';
        process.env.MWW_CUSTOM_TOPIC = customTopic;
        this.logger.info(`Custom topic: ${customTopic}`);
      }

      const result = await runner.run(options);

      const uploadedVideos = [];
      if (result.results && result.results.length > 0) {
        for (const videoResult of result.results) {
          if (videoResult.videoPath && fs.existsSync(videoResult.videoPath)) {
            const uploadResult = await this._uploadToYouTube({
              videoPath: videoResult.videoPath,
              title: videoResult.title || customTopic || 'Weekly Landscape Video',
              description: `${videoResult.title}\n\n🌍 Bringing the world to you.\n\n#global #travel #culture #landscape`,
              type: 'landscape',
              tags: ['mr worldwidewebster', 'global', 'travel', 'culture', 'landscape'],
            });
            if (uploadResult) {
              uploadedVideos.push({ title: videoResult.title, url: uploadResult.url, type: 'landscape' });
            }
          }
        }
      }

      const channelMemory = this.memory['channel-memory'];
      channelMemory.totalVideosPosted = (channelMemory.totalVideosPosted || 0) + uploadedVideos.length;
      this._saveMemory('channel-memory', channelMemory);

      await this._sendDiscordNotification('weekly', {
        weekRange: `Week of ${new Date().toLocaleDateString()}`,
        videos: uploadedVideos.map(v => ({ title: v.title, views: 'New' })),
        countries: channelMemory.countriesUsedThisWeek || [],
        stats: { 'Total Videos': channelMemory.totalVideosPosted, 'Countries Covered': (channelMemory.countriesUsedThisWeek || []).length, 'Videos Created': result.succeeded || 0 },
      });

      this.logger.success(`✅ Weekly landscape pipeline finished: ${result.succeeded} videos`);
      return { topic: customTopic || 'auto-generated', videos: uploadedVideos, result };
    } catch (error) {
      this.logger.error(`Weekly pipeline failed: ${error.message}`);
      const topic = customTopic || this._pickWeeklyTopic();
      const scriptsDir = config.paths.scripts;
      if (!fs.existsSync(scriptsDir)) fs.mkdirSync(scriptsDir, { recursive: true });
      const scriptPath = path.join(scriptsDir, `weekly_${Date.now()}_script.txt`);
      fs.writeFileSync(scriptPath, `Weekly topic: ${topic}\nLandscape pipeline was unavailable.`);
      return { topic, scriptPath, error: error.message };
    }
  }

  _pickWeeklyTopic() {
    const weeklyTopics = [
      'Street Food from Every Continent — What Each Country Eats',
      'How 10 Different Countries React to the Same Music',
      'US English vs UK English vs Australian English',
      'Internet Censorship Differences Around the World',
      'How Different Countries Celebrate the Same Holiday',
      'School Lunch Around the World',
      'Public Transportation in Tokyo vs London vs NYC',
      'The Most Popular Social Media App in Every Country',
      'How Different Countries Handle Weather Disasters',
      'Viral Dances from Around the World',
      'What Dating Looks Like in 10 Different Countries',
      'Most Expensive and Cheapest Countries to Live In',
      'How Different Countries Use AI in Daily Life',
      'The Biggest Stadiums Around the World',
      'National Animals and What They Say About Each Country',
    ];
    return weeklyTopics[Math.floor(Math.random() * weeklyTopics.length)];
  }

  async runReview() {
    this.logger.header('🌙 MIDNIGHT: Self-Improvement Review');

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

    this.logger.header('STEP 1: Performance Analysis');
    this.logger.info(`Total videos: ${stats.totalVideosPosted}, Countries this week: ${stats.countriesUsedThisWeek.length}`);

    let perfData = { totalVideosTracked: 0, recommendations: ['No performance data yet'] };
    try {
      const perfPath = path.join(this.memoryPath, 'performance-metrics.json');
      if (fs.existsSync(perfPath)) {
        perfData = JSON.parse(fs.readFileSync(perfPath, 'utf8'));
      }
    } catch {
      this.logger.warn('Could not read performance metrics');
    }

    this.logger.header('STEP 2: Hermes Agent Strategy Design');
    const brandGuidelines = evolver.brandGuardian.getGuidelines();

    const result = await this.agent.run(
      `You are the SELF-IMPROVEMENT module for Mr. WorldWideWebster YouTube channel.

CHANNEL IDENTITY: "Mr. WorldWideWebster" — shows people what's trending around the world.

CURRENT STATE:
- Total videos posted: ${stats.totalVideosPosted}
- Countries covered this week: ${JSON.stringify(stats.countriesUsedThisWeek)}
- Best performing formats: ${JSON.stringify(stats.bestPerformingFormats)}
- Trends tracked: ${stats.trendsFound}

BRAND GUIDELINES:
- Allowed content types: ${brandGuidelines.allowedContentTypes.join(', ')}
- Preferred title formulas: ${JSON.stringify(brandGuidelines.titleRules.preferredFormulas)}
- Max title length: ${brandGuidelines.titleRules.maxLength} chars
- Must avoid: ${JSON.stringify(brandGuidelines.titleRules.forbiddenPatterns)}
- Voice tone: ${brandGuidelines.voice.tone}

YOUR WORKFLOW:
PHASE 1: ANALYZE - Call analyze_performance, get_brand_guidelines, read memory files
PHASE 2: IMPROVE CODE - Call edit_source_code (dryRun:true first!)
PHASE 3: CREATE TEST VIDEO - Call create_and_post_video with new strategy
PHASE 4: COMMIT - Call commit_improvements with descriptive message`,
      { verbose: true, maxSteps: 15 }
    );

    this.logger.success(`✅ Hermes agent completed: ${result.stepsCount} steps`);

    const changeLog = evolver.getChangeLog();
    if (changeLog.length > 0) {
      this.logger.info(`Committing ${changeLog.length} changes...`);
      evolver.commitChanges('🌙 Midnight self-improvements');
    }

    channelMemory.countriesUsedThisWeek = [];
    this._saveMemory('channel-memory', channelMemory);

    return { ...result, codeChanges: changeLog, evolverLog: changeLog };
  }

  async run() {
    await this.initialize();

    const args = process.argv.slice(2);
    const modeIndex = args.indexOf('--mode');
    const mode = modeIndex !== -1 ? args[modeIndex + 1] : 'daily';

    const topicIndex = args.indexOf('--topic');
    const customTopic = topicIndex !== -1 ? args.slice(topicIndex + 1).join(' ') : null;

    switch (mode) {
      case 'daily': await this.runDaily(); break;
      case 'weekly': await this.runWeekly(customTopic); break;
      case 'review': await this.runReview(); break;
      default:
        console.log(`Unknown mode: ${mode}. Use: daily, weekly, or review`);
        process.exit(1);
    }

    this.logger.success(`🎉 ${mode.toUpperCase()} pipeline completed successfully`);
  }
}

process.on('uncaughtException', (error) => {
  console.error(`⚠️ [Global] Uncaught exception: ${error.message}`);
});

process.on('unhandledRejection', (reason) => {
  console.error(`⚠️ [Global] Unhandled rejection: ${reason?.message || reason}`);
});

const runner = new GitHubActionsRunner();
runner.run().catch(error => {
  console.error(`\n❌ Fatal error: ${error.message}`);
  process.exit(1);
});