#!/usr/bin/env node

/**
 * Mr. WorldWideWebster — GitHub Actions Runner
 *
 * REVISED PIPELINE (v3):
 * Step 1: OpenRouter AI generates TARGETED search queries for your brand
 * Step 2: Playwright searches YouTube, Bilibili, TikTok for real URLs
 * Step 3: yt-dlp downloads the videos
 * Step 4: Nemotron (vision model) watches & ranks videos, picks explainer candidate
 * Step 5: ClipEditor edits based on type (meme/streamer/explainer)
 * Step 6: Upload to YouTube
 */
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const config = require('./config');
const { AIService } = require('./ai-service');
const { Logger } = require('./logger');

class GitHubActionsRunner {
  constructor() {
    this.logger = new Logger('GHAction');
    this.ai = null;
    this.memory = {};
    this.memoryPath = path.join(__dirname, '..', 'memory');
    this.youtubeBridge = null;
    this.discordBridge = null;
    this.clipEditor = null;
  }

  async initialize() {
    this.logger.header('🤖 Mr. WorldWideWebster — Pipeline v3');

    const openrouterKey = process.env.OPENROUTER_API_KEY || '';
    if (!openrouterKey) {
      this.logger.warn('No OPENROUTER_API_KEY found — AI features will fail');
    } else {
      this.logger.info(`OpenRouter key: ${openrouterKey.length} chars`);
    }

    // Initialize OpenRouter AI service
    this.ai = new AIService();
    await this.ai.waitForInit();

    // Initialize ClipEditor
    const { ClipEditor } = require('./clip-editor');
    this.clipEditor = new ClipEditor();

    // Load memory
    this._loadMemory();

    // YouTube bridge
    try {
      const { YouTubeBridge } = require('../youtube-automation/youtube-bridge');
      this.youtubeBridge = new YouTubeBridge();
      await this.youtubeBridge.initialize();
    } catch (error) {
      this.logger.warn(`YouTube bridge: ${error.message}`);
    }

    this.logger.success('Pipeline initialized');
  }

  _loadMemory() {
    if (!fs.existsSync(this.memoryPath)) {
      fs.mkdirSync(this.memoryPath, { recursive: true });
    }
    const memoryFiles = {
      'channel-memory.json': {
        channelName: 'Mr. WorldWideWebster',
        totalVideosPosted: 0,
        lastCountryUsed: '',
        countriesUsedThisWeek: [],
        usedTopics: [],
        createdAt: new Date().toISOString(),
      },
      'trending-log.json': { lastUpdated: new Date().toISOString(), trends: [] },
      'content-history.json': { videos: [] },
    };
    for (const [file, defaults] of Object.entries(memoryFiles)) {
      const filePath = path.join(this.memoryPath, file);
      if (fs.existsSync(filePath)) {
        try {
          this.memory[file.replace('.json', '')] = JSON.parse(fs.readFileSync(filePath, 'utf8'));
        } catch {
          this.memory[file.replace('.json', '')] = defaults;
          fs.writeFileSync(filePath, JSON.stringify(defaults, null, 2));
        }
      } else {
        this.memory[file.replace('.json', '')] = defaults;
        fs.writeFileSync(filePath, JSON.stringify(defaults, null, 2));
      }
    }
  }

  _saveMemory(key, data) {
    const filePath = path.join(this.memoryPath, `${key}.json`);
    fs.writeFileSync(filePath, JSON.stringify(data, null, 2));
    this.memory[key] = data;
  }

  async _uploadToYouTube(videoData) {
    if (!this.youtubeBridge || !this.youtubeBridge.isAuthenticated()) {
      this.logger.warn('YouTube not authenticated — skipping upload');
      return null;
    }
    this.logger.info(`Uploading: "${videoData.title}"`);
    try {
      const result = await this.youtubeBridge.uploadVideo({
        videoPath: videoData.videoPath,
        title: videoData.title,
        description: videoData.description || `${videoData.title}\n\n🌍 Bringing the world to you`,
        tags: videoData.tags || ['mr worldwidewebster', 'shorts'],
        thumbnailPath: videoData.thumbnailPath,
      });
      this.logger.success(`✅ Uploaded: ${result.url}`);
      
      const contentHistory = this.memory['content-history'];
      if (contentHistory) {
        contentHistory.videos.push({
          title: videoData.title, type: videoData.type || 'shorts',
          url: result.url, videoId: result.videoId,
        });
        if (contentHistory.videos.length > 200) contentHistory.videos = contentHistory.videos.slice(-200);
        this._saveMemory('content-history', contentHistory);
      }
      return result;
    } catch (error) {
      this.logger.error(`Upload failed: ${error.message}`);
      return null;
    }
  }

  async _sendDiscordNotification(type, data) {
    try {
      const { DiscordBridge } = require('../discord/discord-bridge');
      const bridge = new DiscordBridge();
      let ok = false;
      switch (type) {
        case 'daily': ok = await bridge.sendDailySummary(data); break;
        case 'alert': ok = await bridge.sendAlert(data.title, data.message); break;
        default: ok = await bridge.sendMessage(data);
      }
      await bridge.destroy();
    } catch (error) {
      this.logger.warn(`Discord: ${error.message}`);
    }
  }

  /**
   * STEP 1: AI generates targeted search queries for your channel niche
   */
  async _generateQueries() {
    this.logger.info('Step 1: AI generating targeted search queries...');
    
    const channelMemory = this.memory['channel-memory'] || {};
    const usedCountries = channelMemory.countriesUsedThisWeek || [];
    
    const allCountries = [
      'Nigeria', 'Japan', 'Germany', 'Australia', 'France', 'Brazil',
      'Thailand', 'India', 'Mexico', 'UK', 'South Korea', 'Egypt',
      'Italy', 'Spain', 'South Africa', 'Argentina', 'Turkey', 'Vietnam'
    ];
    const available = allCountries.filter(c => !usedCountries.includes(c));
    const country1 = available.length > 0 ? available[Math.floor(Math.random() * available.length)] : allCountries[Math.floor(Math.random() * allCountries.length)];
    const country2 = allCountries[Math.floor(Math.random() * allCountries.length)];
    const country3 = allCountries[Math.floor(Math.random() * allCountries.length)];
    
    try {
      const prompt = `You are a content researcher for "Mr. WorldWideWebster" - a channel that shows global viral content.

Generate 5 search queries to find trending videos from these 3 countries: ${country1}, ${country2}, ${country3}.

Focus on finding:
- MEME videos (funny/relatable moments, text overlay only)
- STREAMER moments (reaction clips, live stream highlights)
- Explainer candidates ("What is this?" - music genres, dances, food, trends)

Prioritize:
- Videos with decent engagement (views, comments)
- New and fast-growing content
- Authentic cultural moments

Return ONLY a JSON array of 5 strings, each being a search query.
Example: ["Nigeria viral dance challenge", "Tokyo street food trend", "UK drill reaction"]`;
      
      const result = await this.ai.chatJSON(
        prompt,
        `Generate 5 trending search queries for ${country1}, ${country2}, ${country3}.`,
        { useScriptModel: true, temperature: 0.8 }
      );
      
      const queries = Array.isArray(result) ? result.slice(0, 5) : 
                      result.queries ? result.queries.slice(0, 5) :
                      [`${country1} viral trend`, `${country2} viral video`, `${country3} trending`, `global meme compilation`, `streamer funny moments`];
      
      this.logger.success(`Generated ${queries.length} targeted queries: ${queries.join(' | ')}`);
      return { queries, countries: [country1, country2, country3] };
    } catch (error) {
      this.logger.warn(`Query generation failed: ${error.message}, using defaults`);
      return {
        queries: [
          `${country1} viral dance`, `${country2} street food`, `${country3} music trend`,
          `funny moments compilation`, `streamer best moments`
        ],
        countries: [country1, country2, country3]
      };
    }
  }

  /**
   * STEP 2: Browser search using Playwright across platforms
   */
  async _searchVideos(queries) {
    this.logger.info('Step 2: Browser search for real video URLs...');
    
    const allUrls = [];
    
    // Try Playwright browser search (most reliable for non-YouTube)
    if (this.ai && this.ai.browserSearch) {
      try {
        for (const query of queries.slice(0, 3)) {
          this.logger.info(`Browser searching: "${query}"`);
          const urls = await this.ai.browserSearch(
            ['youtube', 'bilibili', 'tiktok'],
            query,
            { maxUrls: 2 }
          );
          for (const url of urls) {
            if (!allUrls.includes(url)) allUrls.push(url);
          }
          if (allUrls.length >= 5) break;
        }
      } catch (error) {
        this.logger.warn(`Playwright search failed: ${error.message}`);
      }
    }
    
    // Fallback: yt-dlp YouTube search with anti-block flags
    if (allUrls.length < 3) {
      for (const query of queries) {
        if (allUrls.length >= 5) break;
        try {
          // Use player_client=android to bypass bot blocks
          const cmd = `yt-dlp --extractor-args "youtube:player_client=android" --flat-playlist --dump-json "ytsearch3:${query}" 2>/dev/null`;
          const output = execSync(cmd, { timeout: 30000, maxBuffer: 5 * 1024 * 1024 }).toString().trim();
          if (output) {
            const lines = output.split('\n').filter(Boolean);
            for (const line of lines) {
              try {
                const parsed = JSON.parse(line);
                const url = `https://www.youtube.com/watch?v=${parsed.id}`;
                if (!allUrls.includes(url)) {
                  allUrls.push({ url, title: parsed.title || 'YouTube video', source: 'yt-dlp' });
                }
              } catch {}
            }
          }
        } catch (err) {
          this.logger.warn(`yt-dlp search "${query}" failed: ${err.message}`);
        }
      }
    }
    
    this.logger.success(`Found ${allUrls.length} video URLs total`);
    return allUrls;
  }

  /**
   * STEP 3: Download videos using yt-dlp
   */
  async _downloadVideos(urls) {
    this.logger.info('Step 3: Downloading videos...');
    
    const outputDir = config.paths.clips;
    if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
    
    const downloadedVideos = [];
    
    for (let i = 0; i < Math.min(urls.length, 5); i++) {
      const entry = typeof urls[i] === 'string' ? { url: urls[i], title: `Video ${i + 1}` } : urls[i];
      
      this.logger.info(`Downloading [${i + 1}/${Math.min(urls.length, 5)}]: ${entry.url.substring(0, 80)}`);
      
      const outputTemplate = path.join(outputDir, `vid_${i}_${Date.now()}_%(id)s.%(ext)s`);
      
      try {
        // Use player_client=android + limit to 720p mp4
        const dlCmd = `yt-dlp --extractor-args "youtube:player_client=android" -f "best[height<=720][ext=mp4]/best[height<=720]" -o "${outputTemplate}" "${entry.url}" --no-playlist --max-filesize 100M 2>&1`;
        execSync(dlCmd, { timeout: 180000, maxBuffer: 10 * 1024 * 1024 });
        
        // Find the downloaded file
        const files = fs.readdirSync(outputDir)
          .filter(f => f.startsWith(`vid_${i}_`) && (f.endsWith('.mp4') || f.endsWith('.webm')))
          .sort((a, b) => fs.statSync(path.join(outputDir, b)).mtimeMs - fs.statSync(path.join(outputDir, a)).mtimeMs);
        
        if (files.length > 0) {
          const filePath = path.join(outputDir, files[0]);
          downloadedVideos.push({
            path: filePath,
            title: entry.title || `Video ${i + 1}`,
            sourceUrl: entry.url,
          });
          this.logger.success(`  ✅ Downloaded: ${files[0]}`);
        }
      } catch (error) {
        this.logger.warn(`  ❌ Download failed: ${error.message}`);
      }
    }
    
    this.logger.success(`Downloaded ${downloadedVideos.length} videos`);
    return downloadedVideos;
  }

  /**
   * STEP 4: Analyze videos with Nemotron, rank them, pick explainer candidate
   */
  async _analyzeAndRankVideos(videos) {
    this.logger.info('Step 4: Nemotron analyzing & ranking videos...');
    
    if (videos.length === 0) return { ranked: [], explainer: null, clips: [] };
    
    const videoAnalyses = [];
    
    for (let i = 0; i < videos.length; i++) {
      const video = videos[i];
      this.logger.info(`Analyzing video ${i + 1}/${videos.length}: ${video.title.substring(0, 50)}...`);
      
      try {
        // Send video to Nemotron vision model for analysis
        const analysis = await this.ai.chatWithVideo(
          `You are a content curator for a channel that shows global viral content.
Analyze this video and return JSON with:
- type: "meme" | "streamer" | "explainer" | "other"
- rank: 1-10 (viral potential)
- category: "music" | "dance" | "food" | "comedy" | "reaction" | "trend" | "culture" | "other"
- title_en: Brief English title
- description: 1 sentence what this video is about
- has_text_needed: true/false (would a translation text overlay help?)
- explainer_text: If type is explainer, short "What is this? This is..." text
- best_start_time: Best starting timestamp in seconds for a short (0-10)
- duration_needed: How many seconds needed for the short (15-30)
- audio_description: brief desc of the audio/music in the clip`,
          video.path,
          `What is this video? Rate its viral potential for a global audience channel.`,
          { useVideo: true, temperature: 0.3 }
        );
        
        let parsed;
        try {
          const cleaned = analysis.replace(/```json/g, '').replace(/```/g, '').trim();
          parsed = JSON.parse(cleaned);
        } catch {
          const jsonMatch = analysis.match(/\{[\s\S]*\}/);
          parsed = jsonMatch ? JSON.parse(jsonMatch[0]) : { type: 'other', rank: 5 };
        }
        
        videoAnalyses.push({
          ...video,
          analysis: parsed,
          index: i,
        });
        
        this.logger.info(`  Rank ${parsed.rank || '?'}/10 | Type: ${parsed.type || 'other'} | ${parsed.title_en || ''}`);
      } catch (error) {
        this.logger.warn(`  Analysis failed: ${error.message}`);
        videoAnalyses.push({ ...video, analysis: { type: 'other', rank: 5 }, index: i });
      }
    }
    
    // Sort by rank descending
    videoAnalyses.sort((a, b) => (b.analysis?.rank || 0) - (a.analysis?.rank || 0));
    
    // Pick top 3
    const top3 = videoAnalyses.slice(0, 3);
    
    // Find best explainer candidate
    const explainer = top3.find(v => v.analysis?.type === 'explainer') || 
                      top3.find(v => v.analysis?.type === 'other' && v.analysis?.rank >= 6) ||
                      top3[0];
    
    // Remaining are clip candidates
    const clips = top3.filter(v => v !== explainer);
    
    this.logger.success('Ranking complete:');
    top3.forEach((v, i) => {
      this.logger.info(`  #${i + 1}: [${v.analysis?.type}] ${v.analysis?.title_en || v.title} (rank: ${v.analysis?.rank})`);
    });
    
    return { ranked: top3, explainer, clips };
  }

  /**
   * STEP 5: Edit videos using ClipEditor
   */
  async _editVideos(ranked, explainer, clips) {
    this.logger.info('Step 5: Editing videos...');
    
    const outputDir = config.paths.clips;
    const editedVideos = [];
    
    // Edit top clip videos (meme/streamer)
    for (const clip of clips) {
      const type = clip.analysis?.type === 'streamer' ? 'streamer' : 'clip';
      const startTime = clip.analysis?.best_start_time || 5;
      const duration = clip.analysis?.duration_needed || 20;
      const textOverlay = clip.analysis?.has_text_needed && clip.analysis?.description 
        ? clip.analysis.description : '';
      
      const outputPath = path.join(outputDir, `edited_clip_${Date.now()}_${clip.index}.mp4`);
      
      const result = await this.clipEditor.editVideo(clip.path, {
        type,
        startTime,
        duration,
        textOverlay,
        outputPath,
      });
      
      if (result) {
        editedVideos.push({
          path: result,
          title: clip.analysis?.title_en || clip.title,
          type: type,
          tags: ['mr worldwidewebster', 'shorts', clip.analysis?.category || 'trending'].filter(Boolean),
        });
      }
    }
    
    // Edit explainer video
    if (explainer) {
      const startTime = explainer.analysis?.best_start_time || 3;
      const duration = explainer.analysis?.duration_needed || 25;
      const explainerText = explainer.analysis?.explainer_text || `What is this? This is ${explainer.analysis?.title_en || 'global content'}`;
      
      // Generate voiceover for explainer
      const voiceoverDir = path.join(config.paths.assets, 'voiceovers');
      if (!fs.existsSync(voiceoverDir)) fs.mkdirSync(voiceoverDir, { recursive: true });
      const voiceoverPath = path.join(voiceoverDir, `explainer_${Date.now()}.mp3`);
      
      let voiceoverResult = null;
      try {
        voiceoverResult = await this.clipEditor.generateVoiceover(explainerText, voiceoverPath);
      } catch {
        this.logger.warn('Voiceover generation failed, using text-only explainer');
      }
      
      const outputPath = path.join(outputDir, `edited_explain_${Date.now()}.mp4`);
      
      const result = await this.clipEditor.editVideo(explainer.path, {
        type: 'explainer',
        startTime,
        duration,
        voiceoverPath: voiceoverResult,
        voiceoverDuration: 5,
        textOverlay: explainerText,
        outputPath,
      });
      
      if (result) {
        editedVideos.push({
          path: result,
          title: explainer.analysis?.title_en || explainer.title,
          type: 'explainer',
          tags: ['mr worldwidewebster', 'shorts', 'explainer', explainer.analysis?.category || 'trending'].filter(Boolean),
          description: `${explainerText}\n\n🌍 Bringing the world to you`,
        });
      }
    }
    
    this.logger.success(`Edited ${editedVideos.length} videos`);
    return editedVideos;
  }

  /**
   * Main daily pipeline
   */
  async runDaily() {
    this.logger.header('🌅 DAILY PIPELINE v3: AI Search → Download → Analyze → Edit → Upload');
    
    const errors = [];
    const uploadedVideos = [];

    // STEP 1: Generate targeted search queries
    const { queries, countries } = await this._generateQueries();
    
    // STEP 2: Search for real URLs via Playwright + yt-dlp
    const urls = await this._searchVideos(queries);
    
    // STEP 3: Download videos
    const downloadedVideos = await this._downloadVideos(urls);
    
    // STEP 4: Analyze with Nemotron, rank, pick explainer
    const { ranked, explainer, clips } = await this._analyzeAndRankVideos(downloadedVideos);
    
    // STEP 5: Edit videos
    const editedVideos = await this._editVideos(ranked, explainer, clips);
    
    // STEP 6: Upload to YouTube
    for (const video of editedVideos) {
      const uploadResult = await this._uploadToYouTube({
        videoPath: video.path,
        title: video.title.substring(0, 100),
        description: video.description || `🔥 ${video.title}\n\n🌍 Bringing the world to you`,
        type: video.type,
        tags: video.tags,
      });
      if (uploadResult) {
        uploadedVideos.push({ title: video.title, url: uploadResult.url, type: video.type });
      }
    }
    
    // Update memory
    const channelMemory = this.memory['channel-memory'];
    channelMemory.totalVideosPosted = (channelMemory.totalVideosPosted || 0) + uploadedVideos.length;
    if (countries && countries.length > 0) {
      if (!channelMemory.countriesUsedThisWeek) channelMemory.countriesUsedThisWeek = [];
      for (const c of countries) {
        if (!channelMemory.countriesUsedThisWeek.includes(c)) {
          channelMemory.countriesUsedThisWeek.push(c);
        }
      }
      if (channelMemory.countriesUsedThisWeek.length > 14) {
        channelMemory.countriesUsedThisWeek = channelMemory.countriesUsedThisWeek.slice(-14);
      }
    }
    if (queries && queries.length > 0) {
      if (!channelMemory.usedTopics) channelMemory.usedTopics = [];
      for (const q of queries) {
        if (!channelMemory.usedTopics.includes(q)) {
          channelMemory.usedTopics.push(q);
        }
      }
      if (channelMemory.usedTopics.length > 30) {
        channelMemory.usedTopics = channelMemory.usedTopics.slice(-30);
      }
    }
    this._saveMemory('channel-memory', channelMemory);
    
    // Discord notification
    await this._sendDiscordNotification('daily', {
      videos: uploadedVideos,
      countries: channelMemory.countriesUsedThisWeek,
      totalVideos: channelMemory.totalVideosPosted,
      errors,
    });
    
    // Summary
    this.logger.header('DAILY SUMMARY');
    this.logger.info(`Search queries: ${queries.length}`);
    this.logger.info(`URLs found: ${urls.length}`);
    this.logger.info(`Downloaded: ${downloadedVideos.length}`);
    this.logger.info(`Edited: ${editedVideos.length}`);
    this.logger.info(`Uploaded: ${uploadedVideos.length}`);
    if (errors.length > 0) errors.forEach(e => this.logger.warn(`  ❌ ${e}`));
    
    return { uploadedVideos, errors };
  }

  async runWeekly(customTopic) {
    this.logger.header('🎬 WEEKLY: Landscape Compilation');
    try {
      const { WeeklyRunner } = require('../landscape/weekly-runner');
      const runner = new WeeklyRunner();
      const options = { count: '2', type: customTopic ? 'compilation' : 'auto', 'skip-research': 'false' };
      if (customTopic) process.env.MWW_CUSTOM_TOPIC = customTopic;
      const result = await runner.run(options);
      const uploadedVideos = [];
      if (result.results) {
        for (const v of result.results) {
          if (v.videoPath && fs.existsSync(v.videoPath)) {
            const r = await this._uploadToYouTube({ videoPath: v.videoPath, title: v.title || 'Weekly Video', type: 'landscape' });
            if (r) uploadedVideos.push({ title: v.title, url: r.url, type: 'landscape' });
          }
        }
      }
      const m = this.memory['channel-memory'];
      m.totalVideosPosted = (m.totalVideosPosted || 0) + uploadedVideos.length;
      this._saveMemory('channel-memory', m);
      this.logger.success(`Weekly done: ${result.succeeded} videos`);
      return { videos: uploadedVideos, result };
    } catch (error) {
      this.logger.error(`Weekly failed: ${error.message}`);
    }
  }

  _pickWeeklyTopic() {
    const topics = [
      'Street Food from Every Continent', 'How Different Countries React to Music',
      'US vs UK vs Australian English', 'Internet Censorship Around the World',
      'How Different Countries Celebrate Holidays', 'School Lunch Around the World',
      'Public Transport: Tokyo vs London vs NYC', 'Most Popular Social Media by Country',
    ];
    return topics[Math.floor(Math.random() * topics.length)];
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
      default:
        console.log(`Unknown mode: ${mode}`);
        process.exit(1);
    }
    this.logger.success(`🎉 ${mode.toUpperCase()} pipeline completed`);
  }
}

process.on('uncaughtException', (error) => {
  console.error(`⚠️ Uncaught: ${error.message}`);
});
process.on('unhandledRejection', (reason) => {
  console.error(`⚠️ Rejection: ${reason?.message || reason}`);
});

new GitHubActionsRunner().run().catch(error => {
  console.error(`\n❌ Fatal: ${error.message}`);
  process.exit(1);
});