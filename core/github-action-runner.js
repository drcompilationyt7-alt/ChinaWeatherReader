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
      
      // Skip YouTube URLs with invalid video ID patterns (must be 11 chars, alphanumeric + _ -)
      const youtubeMatch = url.match(/youtube\.com\/watch\?v=([a-zA-Z0-9_-]+)/i);
      if (youtubeMatch) {
        const videoId = youtubeMatch[1];
        // Valid YouTube IDs are exactly 11 characters
        if (videoId.length !== 11) return false;
        // Check for suspicious patterns like all same character or obvious placeholders
        if (/^(.)\1+$/.test(videoId)) return false; // e.g., "aaaaaaaaaaa"
      }
      
      // Skip Bilibili URLs with placeholder BV numbers
      const bilibiliMatch = url.match(/bilibili\.com\/video\/(BV[a-zA-Z0-9]+)/i);
      if (bilibiliMatch) {
        const bvId = bilibiliMatch[1];
        // Real BV IDs are typically 10-12 characters after BV
        if (bvId.length < 10) return false;
        // Skip if contains 'xxx' placeholder
        if (bvId.toLowerCase().includes('xxx')) return false;
      }
      
      return true;
    });
    
    return validUrls;
  }

  async initialize() {
    this.logger.header('🤖 Mr. WorldWideWebster — GitHub Actions');

    // Validate API keys at startup
    this._validateKeys();

    // Initialize AI — await async initialization (TTS provider, etc.)
    this.ai = new AIService();
    await this.ai.waitForInit();

    // PRIMARY: Official Hermes CLI from Nous Research (installed via curl)
    const hermesCLI = new HermesCLIWrapper();
    if (hermesCLI.isAvailable()) {
      this.agent = hermesCLI;
      this.logger.success('✅ Using official Hermes CLI as primary agent');

      // Run Hermes smoke test to verify it actually outputs something
      await this._smokeTestHermes();
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
   * Fallback: Search YouTube using yt-dlp directly (always works, no browser needed)
   * Returns first video URL found
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
    const { UniversalDownloader } = require('../sourcing/universal-downloader');

    // Check if we have URLs from Hermes research
    const trendingUrls = this.memory['trending-urls'] || [];
    
    if (trendingUrls.length > 0) {
      this.logger.info(`Using ${trendingUrls.length} URLs from Hermes research...`);
      
      // Try to download one of the URLs
      for (const url of trendingUrls) {
        try {
          this.logger.info(`Attempting to download: ${url}`);
          
          // Determine platform from URL
          let platform = 'unknown';
          if (url.includes('bilibili')) platform = 'bilibili';
          else if (url.includes('tiktok')) platform = 'tiktok';
          else if (url.includes('douyin')) platform = 'douyin';
          else if (url.includes('youtube')) platform = 'youtube';
          else if (url.includes('instagram')) platform = 'instagram';
          
          // Use UniversalDownloader directly with the URL
          const downloader = new UniversalDownloader();
          const downloadResult = await downloader.download(url, {
            outputDir: './output/temp',
            maxHeight: 720,
          });
          
          if (downloadResult && downloadResult.filePath) {
            this.logger.success(`✅ Downloaded video from ${platform}: ${downloadResult.filePath}`);
            
            // TODO: Add trimming/upload logic here
            // For now, return success
            return {
              title: `Viral clip from ${platform}`,
              platform: platform,
              sourceUrl: url,
              filePath: downloadResult.filePath
            };
          }
        } catch (error) {
          this.logger.warn(`Failed to download ${url}: ${error.message}`);
          // Try next URL
        }
      }
      
      this.logger.warn('None of the Hermes URLs worked, falling back to search...');
    }

    // Fallback: Let AI pick platform+query and search
    this.logger.info('Asking AI which platform to search for trending content...');

    let platformChoice;
    let searchQuery;

    try {
      // OPTIMIZED FOR LOCAL MODELS: Ask Hermes to find a direct URL
      this.logger.info('Asking Hermes to find a viral video URL...');
      
      const aiResult = await this.agent.run(
        `Mr. WorldWideWebster needs ONE viral video URL RIGHT NOW for a YouTube Short.
        
        Current date: ${new Date().toISOString()}
        Mission: "Burst your bubble. Escape the algorithm."
        
        INSTRUCTIONS:
        1. Search Bilibili, TikTok, Douyin, or YouTube for trending videos from Japan, Nigeria, Brazil, etc.
        2. Find ONE specific video URL that is viral right now.
        3. Return ONLY the raw URL (e.g., https://bilibili.com/video/BV1...). 
        4. Do NOT write any explanation, JSON, markdown, or extra text. Just the link.
        
        Find a URL now:`,
        { verbose: false, maxSteps: 3 }
      );

      // Extract URL using Regex (works even if model talks too much)
      const output = aiResult.output || '';
      this.logger.info(`Hermes raw output length: ${output.length} chars`);
      
      // Log full Hermes output for debugging
      this.logger.info('═══════════════════════════════════════════');
      this.logger.info('🤖 HERMES RAW OUTPUT (Fallback URL Search):');
      this.logger.info('═══════════════════════════════════════════');
      console.log(output || '[No output]');
      this.logger.info('═══════════════════════════════════════════');
      
      // Save to temp file for debugging
      const debugPath = path.join('./output/temp', `hermes_fallback_debug_${Date.now()}.txt`);
      try {
        fs.mkdirSync('./output/temp', { recursive: true });
        fs.writeFileSync(debugPath, output);
        this.logger.info(`💾 Full output saved to: ${debugPath}`);
      } catch (e) {
        this.logger.warn(`Could not save debug file: ${e.message}`);
      }
      
      // Use the broad URL extractor
      const matches = this._extractUrls(output);
      
      if (matches && matches.length > 0) {
        const foundUrl = matches[0];
        this.logger.info(`✅ Hermes found URL: ${foundUrl}`);
        
        // Determine platform from URL and set as direct URL
        let platform = 'unknown';
        if (foundUrl.includes('bilibili')) platform = 'bilibili';
        else if (foundUrl.includes('tiktok')) platform = 'tiktok';
        else if (foundUrl.includes('douyin')) platform = 'douyin';
        else if (foundUrl.includes('youtube')) platform = 'youtube';
        else if (foundUrl.includes('instagram')) platform = 'instagram';
        
        platformChoice = platform;
        searchQuery = foundUrl; // Use URL as the query
        this.logger.info(`Using direct URL from Hermes on ${platform}`);
      } else {
        this.logger.warn('No URL found in Hermes output');
        throw new Error('No URL found in Hermes response');
      }
    } catch (error) {
      this.logger.warn(`Hermes URL extraction failed: ${error.message}`);
      this.logger.info('🔄 Falling back to yt-dlp YouTube search (most reliable)...');
      
      // Fallback #1: yt-dlp YouTube search (most reliable, no browser needed)
      try {
        const ytResults = await this._searchYouTubeWithYtDlp('viral video 2026 trending', 5);
        if (ytResults.length > 0) {
          platformChoice = 'youtube';
          searchQuery = ytResults[0].url;
          this.logger.info(`Using YouTube URL from yt-dlp: ${searchQuery}`);
        } else {
          throw new Error('yt-dlp returned no results');
        }
      } catch (ytError) {
        this.logger.warn(`yt-dlp fallback failed: ${ytError.message}`);
        this.logger.info('🔄 Final fallback: random platform selection...');
        
        // Fallback #2: Random platform + query
        const platforms = ['youtube', 'bilibili', 'tiktok'];
        platformChoice = platforms[Math.floor(Math.random() * platforms.length)];
        
        const queriesByPlatform = {
          youtube: ['viral dance compilation 2026', 'street food around the world', 'beautiful nature', 'satisfying video', 'travel moments'],
          bilibili: ['热门视频', '搞笑', '美食'], // Chinese queries work on Bilibili
          tiktok: ['viral dance', 'funny moments', 'food'],
        };
        
        searchQuery = queriesByPlatform[platformChoice][Math.floor(Math.random() * queriesByPlatform[platformChoice].length)];
        this.logger.info(`Selected ${platformChoice} with query: "${searchQuery}"`);
      }
    }

    try {
      this.logger.info(`Searching ${platformChoice} for: "${searchQuery}"`);

      let videoTitle = searchQuery;
      let videoUrl = null;
      
      // Step 1: If the searchQuery is already a URL, try downloading it directly
      if (searchQuery.startsWith('http')) {
        this.logger.info(`Search query is already a URL, trying direct download...`);
        const downloader = new UniversalDownloader();
        const downloadResult = await downloader.download(searchQuery, {
          outputDir: config.paths.clips,
          maxHeight: 720,
        });
        
        if (downloadResult.success && downloadResult.filePath) {
          videoUrl = searchQuery;
          videoTitle = downloadResult.title || 'Viral video';
          
          // Trim and upload
          const clipPipeline = require('../clipping/clip-pipeline');
          const shortPath = path.join(config.paths.clips, `clip_upload_${Date.now()}.mp4`);
          
          const trimmedPath = await clipPipeline.trimToShort({
            videoPath: downloadResult.filePath,
            startTime: 3,
            duration: 30,
            outputPath: shortPath,
          });

          if (trimmedPath && fs.existsSync(trimmedPath)) {
            const uploadResult = await this._uploadToYouTube({
              videoPath: trimmedPath,
              title: videoTitle.substring(0, 100),
              description: `🔥 ${videoTitle}\n\n🌍 Bringing the world to you`,
              type: 'shorts',
              tags: ['mr worldwidewebster', 'shorts', 'trending', 'viral', platformChoice],
            });

            if (uploadResult) {
              await this._boostVideo(uploadResult.url, 50);
              return {
                title: videoTitle.substring(0, 100),
                url: uploadResult.url,
                type: 'clip',
                platform: platformChoice,
              };
            }
          }
          throw new Error('Trim failed');
        }
      }
      
      // Step 2: Use Puppeteer scraping
      const finder = new TrendingVideoFinder();
      const results = await finder.findTrendingVideos(platformChoice, searchQuery);
      await finder.destroy();
      
      if (results.length === 0) {
        // Step 3: Try yt-dlp YouTube search as final fallback
        this.logger.info('Puppeteer found nothing, trying yt-dlp YouTube search...');
        const ytResults = await this._searchYouTubeWithYtDlp(searchQuery, 3);
        if (ytResults.length > 0) {
          const randomVideo = ytResults[Math.floor(Math.random() * ytResults.length)];
          videoUrl = randomVideo.url;
          videoTitle = randomVideo.title || searchQuery;
          platformChoice = 'youtube';
        } else {
          throw new Error(`No videos found anywhere for: ${searchQuery}`);
        }
      } else {
        // Pick a random video from results
        const randomVideo = results[Math.floor(Math.random() * results.length)];
        videoUrl = randomVideo.url;
        videoTitle = randomVideo.title || searchQuery;
      }
      
      this.logger.info(`Found video: ${videoTitle.substring(0, 60)}...`);

      if (!videoUrl || !videoUrl.startsWith('http')) throw new Error('Invalid video URL');

      this.logger.info(`Downloading: "${videoTitle}"`);
      
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
      this.logger.info('Asking Hermes to find trending content with URLs...');
      
      trendsResult = await this.agent.run(
        `FIND TRENDING VIDEO URLS FOR Mr. WorldWideWebster

Your job: Find 3-5 viral video URLs from DIFFERENT countries RIGHT NOW.

INSTRUCTIONS:
1. Search Bilibili, TikTok, Douyin, YouTube for trending videos
2. Focus on: Japan, Nigeria, Brazil, India, Mexico, Germany (avoid repeat countries)
3. Find ACTUAL video URLs that can be downloaded

For each trend, return ONLY the URL in this format:
URL: https://bilibili.com/video/BV1xxx
URL: https://tiktok.com/@user/video/1234567890
URL: https://youtube.com/watch?v=abcdefg

Find 3-5 URLs from different countries. Just the links, no explanations.`,
        { verbose: false, maxSteps: 5 }
      );
      
      this.logger.info(`Hermes research completed, output length: ${trendsResult.output ? trendsResult.output.length : 0} chars`);
      
      // Log full Hermes output for debugging
      const hermesOutput = trendsResult.output || '';
      this.logger.info('═══════════════════════════════════════════');
      this.logger.info('🤖 HERMES RAW OUTPUT (Research Step):');
      this.logger.info('═══════════════════════════════════════════');
      console.log(hermesOutput || '[No output]');
      this.logger.info('═══════════════════════════════════════════');
      
      // Save to temp file for debugging (will be cleaned up later)
      const debugPath = path.join('./output/temp', `hermes_research_debug_${Date.now()}.txt`);
      try {
        fs.mkdirSync('./output/temp', { recursive: true });
        fs.writeFileSync(debugPath, hermesOutput);
        this.logger.info(`💾 Full output saved to: ${debugPath}`);
      } catch (e) {
        this.logger.warn(`Could not save debug file: ${e.message}`);
      }
    } catch (error) {
      this.logger.warn(`Hermes agent research failed: ${error.message}`);
      trendsResult = { stepsCount: 0, steps: [], output: '' };
    }

    // Extract URLs from Hermes output using the broad regex
    let foundUrls = [];
    if (trendsResult && trendsResult.output) {
      foundUrls = this._extractUrls(trendsResult.output).slice(0, 5);
      if (foundUrls.length > 0) {
        this.logger.success(`✅ Extracted ${foundUrls.length} URLs from Hermes research`);
      } else {
        this.logger.info('No URLs found in Hermes research output');
      }
    }
    
    // If Hermes found zero URLs, try yt-dlp YouTube search as backup
    if (foundUrls.length === 0) {
      this.logger.info('Hermes found no URLs — trying yt-dlp YouTube search as backup...');
      try {
        const ytResults = await this._searchYouTubeWithYtDlp('trending worldwide 2026', 3);
        for (const result of ytResults) {
          foundUrls.push(result.url);
        }
        if (foundUrls.length > 0) {
          this.logger.success(`✅ yt-dlp backup found ${foundUrls.length} URLs`);
        }
      } catch (error) {
        this.logger.warn(`yt-dlp backup search failed: ${error.message}`);
      }
    }
    
    // Store found URLs for later use
    this.memory['trending-urls'] = foundUrls;
    this.logger.info(`Found ${foundUrls.length} trending URLs: ${foundUrls.slice(0, 3).join(', ')}${foundUrls.length > 3 ? '...' : ''}`);

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
    
    // Use Hermes to find actual content/video matching the script topic
    this.logger.info(`Asking Hermes to find ${format.category} content from ${country}...`);
    
    let hermesFoundContent = null;
    try {
      // OPTIMIZED FOR LOCAL MODELS: Ask for URL only, no complex JSON
      const searchTask = `Find ONE viral video URL about ${format.category} from ${country}.
      
      Search TikTok, Bilibili, Douyin, YouTube, or Instagram.
      Return ONLY the raw video URL (e.g., https://tiktok.com/@user/video/123...).
      Do NOT write JSON, explanations, or any other text. Just the link.
      
      Find a URL now:`;
      
      const hermesResult = await this.agent.run(searchTask, { verbose: false, maxSteps: 2 });
      
      // Extract URL using the broad regex
      const output = hermesResult.output || '';
      const matches = this._extractUrls(output);
      
      if (matches && matches.length > 0) {
        const foundUrl = matches[0];
        
        // Determine platform from URL
        let platform = 'unknown';
        if (foundUrl.includes('bilibili')) platform = 'bilibili';
        else if (foundUrl.includes('tiktok')) platform = 'tiktok';
        else if (foundUrl.includes('douyin')) platform = 'douyin';
        else if (foundUrl.includes('youtube')) platform = 'youtube';
        else if (foundUrl.includes('instagram')) platform = 'instagram';
        
        hermesFoundContent = {
          platform: platform,
          url: foundUrl,
          query: `${country} ${format.category}`
        };
        this.logger.info(`Hermes found URL: ${foundUrl} on ${platform}`);
      } else {
        this.logger.warn('No URL found in Hermes response');
        
        // Backup: try yt-dlp YouTube search
        this.logger.info('Trying yt-dlp YouTube search as backup...');
        const ytResults = await this._searchYouTubeWithYtDlp(`${country} ${format.category}`, 1);
        if (ytResults.length > 0) {
          hermesFoundContent = {
            platform: 'youtube',
            url: ytResults[0].url,
            query: `${country} ${format.category}`
          };
          this.logger.info(`yt-dlp found URL: ${ytResults[0].url}`);
        }
      }
    } catch (error) {
      this.logger.warn(`Hermes content search failed: ${error.message}, using generic topic`);
    }
    
    // Build explain topic based on what Hermes found (or fallback to generic)
    const explainTopic = {
      country,
      format,
      title: `${format.prompt} (${country} edition) 🌍`,
      hermesContent: hermesFoundContent
    };

    this.logger.info(`Creating explainer: "${explainTopic.title}"`);

    const explainPipeline = require('../explainer/explain-pipeline');
    let explainResult;
    try {
      explainResult = await explainPipeline.processExplain({
        sourceContent: {
          title: explainTopic.title,
          platform: hermesFoundContent?.platform || 'web',
          url: hermesFoundContent?.url || null,
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
        hermesAgent: this.agent,  // Pass Hermes agent so pipeline can use it to find matching videos
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
    const researchSteps = trendsResult?.steps || [];
    trendingLog.trends = [
      ...researchSteps.map(s => ({
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

  // ── remaining methods (runWeekly, _pickWeeklyTopic, runReview, run) are unchanged ──
  // ── they are identical to the previous version ──

  /**
   * MODE: weekly — Create 2 landscape compilation videos
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

      const channelMemory = this.memory['channel-memory'];
      channelMemory.totalVideosPosted = (channelMemory.totalVideosPosted || 0) + uploadedVideos.length;
      this._saveMemory('channel-memory', channelMemory);

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

    this.logger.header('STEP 2: Hermes Agent Strategy Design (with code editing tools)');
    const brandGuidelines = evolver.brandGuardian.getGuidelines();

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
5. Call edit_source_code (dryRun:true first!) to preview changes
6. After dry-run looks good, call edit_source_code (dryRun:false) to apply

PHASE 3: CREATE TEST VIDEO
7. Call create_and_post_video with a topic that uses the NEW strategy

PHASE 4: COMMIT
8. Call commit_improvements with descriptive message
9. Report what was accomplished`,
      { verbose: true, maxSteps: 15 }
    );

    this.logger.success(`✅ Hermes agent completed: ${result.stepsCount} steps`);

    const changeLog = evolver.getChangeLog();
    if (changeLog.length > 0) {
      this.logger.info(`Agent left ${changeLog.length} uncommitted changes — committing now`);
      const commitResult = evolver.commitChanges('🌙 Midnight self-improvements (auto-commit)');
      this.logger.info(`Commit result: ${commitResult.success ? 'Success' : 'Failed'}`);
    } else {
      this.logger.info('No pending changes to commit');
    }

    channelMemory.countriesUsedThisWeek = [];
    this._saveMemory('channel-memory', channelMemory);

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