#!/usr/bin/env node
/**
 * Mr. WorldWideWebster — Weekly Landscape Video Runner
 *
 * This is the main orchestrator script for the weekly landscape video pipeline.
 * It's called by the `landscape-weekly.yml` GitHub Actions workflow.
 *
 * Workflow:
 * 1. (Optional) Research trending categories with CompetitorResearcher
 * 2. Pick the best topic/category based on research
 * 3. Search for/download clips from multiple sources
 * 4. Generate AI script for the video
 * 5. Find matching background music
 * 6. Compile the landscape video with CompilationPipeline
 * 7. Schedule staggered uploads to YouTube
 *
 * Usage:
 *   node landscape/weekly-runner.js --count 2 --type auto
 *
 * Options:
 *   --count          Number of videos to create (default: 2)
 *   --type           Video type: auto, compilation, versus, listicle, cinematic
 *   --skip-research  Skip competitor research (true/false, default: false)
 */
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const { Logger } = require('../core/logger');

// Add project root to require path
const projectRoot = path.join(__dirname, '..');

class WeeklyRunner {
  constructor() {
    this.logger = new Logger('WeeklyRunner');
    this.outputDir = path.join(projectRoot, 'output', 'landscape');
    if (!fs.existsSync(this.outputDir)) {
      fs.mkdirSync(this.outputDir, { recursive: true });
    }
  }

  /**
   * Main entry point
   */
  async run(options = {}) {
    const count = parseInt(options.count) || 2;
    const videoType = options.type || 'auto';
    const skipResearch = options['skip-research'] === 'true';

    this.logger.header('🎬 WEEKLY LANDSCAPE VIDEO PIPELINE');
    this.logger.info(`Count: ${count} | Type: ${videoType} | Skip research: ${skipResearch}`);

    const results = [];

    // Step 1: Research (unless skipped)
    let researchData = null;
    if (!skipResearch) {
      researchData = await this._performResearch();
    }

    // Step 2: Determine what videos to create
    const videoPlans = this._planVideos(count, videoType, researchData);
    this.logger.info(`Planned ${videoPlans.length} videos:`);
    for (const plan of videoPlans) {
      this.logger.info(`  - ${plan.title} (${plan.type})`);
    }

    // Step 3: Create each video
    for (let i = 0; i < videoPlans.length; i++) {
      this.logger.header(`Creating video ${i + 1}/${videoPlans.length}: "${videoPlans[i].title}"`);

      try {
        // 3a. Search for clips
        const clips = await this._findClipsForTopic(videoPlans[i]);
        this.logger.success(`Found ${clips.length} clips`);

        // 3b. Download clips
        const downloadedClips = await this._downloadClips(clips, videoPlans[i]);
        this.logger.success(`Downloaded ${downloadedClips.length} clips`);

        // 3c. Generate script (or use provided)
        const script = await this._generateScript(videoPlans[i]);

        // 3d. Create the video
        const { CompilationPipeline } = require('./compilation-pipeline');
        const pipeline = new CompilationPipeline();

        const videoResult = await pipeline.createVideo({
          title: videoPlans[i].title,
          videoType: videoPlans[i].type,
          clipPaths: downloadedClips,
          script: script ? { fullScript: script, estimatedDuration: 120 } : null,
          musicMood: videoPlans[i].mood || 'chill',
        });

        this.logger.success(`Video created: ${videoResult.videoPath}`);

        // 3e. Upload to YouTube (scheduled)
        try {
          const uploadResult = await this._scheduleUpload(videoResult, i, videoPlans.length);
          results.push({
            ...videoResult,
            uploadResult,
          });
        } catch (uploadError) {
          this.logger.warn(`Upload failed: ${uploadError.message}`);
          results.push({
            ...videoResult,
            uploadError: uploadError.message,
          });
        }
      } catch (error) {
        this.logger.error(`Video ${i + 1} failed: ${error.message}`);
        results.push({ error: error.message, plan: videoPlans[i] });
      }
    }

    // Step 4: Save metadata
    this._saveMetadata(results);

    return {
      results,
      totalVideos: results.length,
      succeeded: results.filter(r => r.videoPath).length,
      failed: results.filter(r => r.error).length,
    };
  }

  /**
   * Perform competitor research to find the best categories
   */
  async _performResearch() {
    this.logger.info('Step 1: Performing competitor research...');

    try {
      const { CompetitorResearcher } = require('../hermes-agent/competitor-researcher');
      const researcher = new CompetitorResearcher();
      const allResults = await researcher.researchAllCategories();
      await researcher.destroy();

      // Find the category with highest avg views
      let bestCategory = 'compilation';
      let bestViews = 0;

      for (const [cat, analysis] of Object.entries(allResults)) {
        const avgViews = analysis.avgViews || 0;
        this.logger.info(`  ${cat}: avg ${avgViews.toLocaleString()} views`);
        if (avgViews > bestViews) {
          bestViews = avgViews;
          bestCategory = cat;
        }
      }

      this.logger.success(`Best performing category: "${bestCategory}" (${bestViews.toLocaleString()} avg views)`);
      return { bestCategory, allResults };
    } catch (error) {
      this.logger.warn(`Research failed: ${error.message}`);
      return null;
    }
  }

  /**
   * Plan what videos to create based on research and available data
   */
  _planVideos(count, type, researchData) {
    const plans = [];

    // Category → title/mood mapping
    const categoryInfo = {
      architecture: {
        titles: [
          "World's Most Futuristic Cities 🇨🇳🇦🇪",
          "Mind-Blowing Architecture Around the World 🌍",
          "The Future of City Design Is Already Here",
          "Most Beautiful Buildings on Earth 🏗️",
        ],
        mood: 'cinematic',
        searchTerms: ['futuristic city architecture', 'amazing buildings', 'city skyline timelapse'],
      },
      meme: {
        titles: [
          'Funniest Viral Moments This Week 😂',
          'Internet Memes That Broke the Internet 🔥',
          'Best Meme Compilation You Needed Today',
        ],
        mood: 'funny',
        searchTerms: ['viral funny moments compilation', 'best memes compilation'],
      },
      explainer: {
        titles: [
          'How [Topic] Actually Works 🤯',
          'Things You Never Knew Existed 🌏',
          'The Craziest Facts About [Topic]',
        ],
        mood: 'chill',
        searchTerms: ['interesting facts compilation', 'how things work documentary'],
      },
      compilation: {
        titles: [
          'Best of [Topic] Compilation 🌟',
          'Most Amazing Moments Caught on Camera 📸',
          'Epic Compilation You Have to See 🔥',
        ],
        mood: 'cinematic',
        searchTerms: ['amazing compilation', 'best moments compilation', 'satisfying video compilation'],
      },
      versus: {
        titles: [
          'US vs UK: [Topic] Comparison 🇺🇸🇬🇧',
          'Which Country Does It Better? 🌍',
          '[Country A] vs [Country B]: The Ultimate Comparison',
        ],
        mood: 'intense',
        searchTerms: ['vs comparison', 'countries comparison video', 'us vs uk comparison'],
      },
      listicle: {
        titles: [
          'Top 10 [Topic] Around the World 🌎',
          '5 Things You Won\'t Believe Exist 🔥',
          'The Ultimate Ranking of [Topic] 🏆',
        ],
        mood: 'upbeat',
        searchTerms: ['top 10 compilation', 'best of list video', 'ranking video compilation'],
      },
      cinematic: {
        titles: [
          'Earth From Above: [Location] 🇯🇵',
          'Cinematic Journey Through [Country] 🎬',
          'Nature\'s Most Beautiful Creations 🌿',
        ],
        mood: 'cinematic',
        searchTerms: ['cinematic aerial footage', 'nature documentary compilation', 'beautiful landscapes'],
      },
    };

    let categories = [];

    if (type === 'auto') {
      // Use research if available
      if (researchData?.bestCategory) {
        categories.push(researchData.bestCategory);
      }
      // Add some variety
      const allCats = Object.keys(categoryInfo);
      const shuffled = allCats.sort(() => Math.random() - 0.5);
      for (const cat of shuffled) {
        if (!categories.includes(cat)) {
          categories.push(cat);
        }
      }
    } else {
      // Use specified type
      categories = [type];
      // Add a second different type if needed
      if (count > 1) {
        const others = Object.keys(categoryInfo).filter(c => c !== type);
        categories.push(others[Math.floor(Math.random() * others.length)]);
      }
    }

    // Create plans
    for (let i = 0; i < Math.min(count, categories.length); i++) {
      const cat = categories[i];
      const info = categoryInfo[cat] || categoryInfo.compilation;
      const titleTemplate = info.titles[Math.floor(Math.random() * info.titles.length)];

      plans.push({
        title: titleTemplate.replace('[Topic]', this._getTopicForCategory(cat)),
        type: cat,
        mood: info.mood,
        searchTerms: info.searchTerms,
        category: cat,
      });
    }

    return plans;
  }

  /**
   * Generate a topic for a category
   */
  _getTopicForCategory(category) {
    const topics = {
      architecture: ['Dubai', 'Tokyo', 'Singapore', 'Shanghai', 'New York', 'Future Cities', 'Modern Architecture'],
      meme: ['Internet', 'Viral', 'Trending'],
      explainer: ['Technology', 'Culture', 'Science', 'History', 'Nature'],
      compilation: ['Moments', 'Travel', 'Adventure', 'Wildlife', 'Sports'],
      versus: ['Food', 'Music', 'Culture', 'Lifestyle', 'Technology'],
      listicle: ['Travel Destinations', 'Technologies', 'Cultural Events', 'Natural Wonders'],
      cinematic: ['Japan', 'Iceland', 'Norway', 'New Zealand', 'Switzerland', 'Amazon Rainforest'],
    };

    const catTopics = topics[category] || topics.compilation;
    return catTopics[Math.floor(Math.random() * catTopics.length)];
  }

  /**
   * Find clips for a planned video topic
   */
  async _findClipsForTopic(plan) {
    this.logger.info(`Searching clips for: "${plan.title}"`);

    const allClips = [];

    // Use FreeVisualSearcher for Pexels/Pixabay/YouTube clips
    try {
      const { FreeVisualSearcher } = require('../hermes-agent/free-visual-searcher');
      const searcher = new FreeVisualSearcher();

      // Search for each search term
      for (const term of (plan.searchTerms || []).slice(0, 2)) {
        const clips = await searcher.searchFreeVideoClips(term, {
          maxResults: 3,
          maxDuration: 30,
          outputDir: path.join(this.outputDir, `temp_clips_${Date.now()}`),
        });
        allClips.push(...clips.map(c => c.file));
      }

      await searcher.destroy();
    } catch (error) {
      this.logger.warn(`Free visual search failed: ${error.message}`);
    }

    // If not enough clips, try downloading from YouTube directly (UniversalDownloader)
    if (allClips.length < 2) {
      try {
        const { UniversalDownloader } = require('../sourcing/universal-downloader');
        const downloader = new UniversalDownloader();

        const ytSearchCmd = `yt-dlp --flat-playlist --dump-json "ytsearch5:${plan.searchTerms?.[0] || plan.title}" 2>nul`;
        let output;
        try {
          output = execSync(ytSearchCmd, { timeout: 20000, maxBuffer: 5 * 1024 * 1024 }).toString();
        } catch {
          output = '';
        }

        const entries = output.trim().split('\n').filter(Boolean).map(line => {
          try { return JSON.parse(line); } catch { return null; }
        }).filter(Boolean);

        for (const entry of entries.slice(0, 5 - allClips.length)) {
          try {
            const videoUrl = `https://www.youtube.com/watch?v=${entry.id}`;
            const result = await downloader.download(videoUrl, {
              outputDir: path.join(this.outputDir, `yt_dl_${Date.now()}`),
              maxHeight: 720,
            });
            if (result.success && result.filePath) {
              allClips.push(result.filePath);
              this.logger.info(`  ✅ YouTube clip: ${entry.title?.substring(0, 40)}`);
            }
          } catch {
            this.logger.warn(`  YouTube download failed for ${entry.id}`);
          }
        }
      } catch (error) {
        this.logger.warn(`YouTube search failed: ${error.message}`);
      }
    }

    return allClips.filter(Boolean);
  }

  /**
   * Download clips from URLs
   */
  async _downloadClips(clips, plan) {
    // Clips from FreeVisualSearcher are already downloaded
    // This method validates they exist
    const valid = [];
    for (const clip of clips) {
      if (fs.existsSync(clip)) {
        valid.push(clip);
      } else {
        this.logger.warn(`Clip not found on disk: ${clip}`);
      }
    }
    return valid;
  }

  /**
   * Generate a script for the video using AI
   */
  async _generateScript(plan) {
    this.logger.info(`Generating script for: "${plan.title}"`);

    // Use the AIService to generate the script
    try {
      const { AIService } = require('../core/ai-service');
      const ai = new AIService();

      const prompt = `Create a 60-90 second script for a "${plan.type}" video titled "${plan.title}" for Mr. WorldWideWebster YouTube channel.

Style: Informative but entertaining, global perspective.
Format:
- Hook (5-10s): Grab attention
- Content (40-60s): Main content with interesting facts/observations
- Wrap-up (10-15s): Conclusion with perspective
- CTA (5-10s): "Follow Mr. WorldWideWebster for more global content!"

Write only the script text, no JSON. Make it conversational and engaging.`;

      const script = await ai.chat(prompt, 'Write a compelling video script.', {
        temperature: 0.7,
        maxTokens: 1000,
      });

      return script;
    } catch (error) {
      this.logger.warn(`Script generation failed: ${error.message}`);
      // Fallback script
      return `Welcome to Mr. WorldWideWebster! Today we're exploring ${plan.title}. From amazing locations to incredible stories, the world is full of surprises. ${plan.searchTerms?.[0] || 'Travel'} brings us closer to understanding different cultures and perspectives. Thanks for watching! Follow Mr. WorldWideWebster for more global content!`;
    }
  }

  /**
   * Schedule video for YouTube upload (staggered)
   */
  async _scheduleUpload(videoResult, videoIndex, totalVideos) {
    this.logger.info(`Scheduling upload for video ${videoIndex + 1}/${totalVideos}...`);

    // Calculate staggered publish times
    // First video: 2 days from now (Monday/Tuesday)
    // Second video: 4 days from now (Wednesday/Thursday)
    // Third video: 6 days from now (Friday/Saturday)
    const daysOffset = 2 + (videoIndex * 2);
    const publishDate = new Date();
    publishDate.setDate(publishDate.getDate() + daysOffset);
    publishDate.setHours(14, 0, 0, 0); // 2 PM

    const { YouTubeBridge } = require('../youtube-automation/youtube-bridge');
    const bridge = new YouTubeBridge();
    const initialized = await bridge.initialize();

    if (!initialized) {
      this.logger.warn('YouTube not authenticated - skipping upload');
      return { scheduled: false, reason: 'not authenticated' };
    }

    const uploadResult = await bridge.uploadVideo({
      videoPath: videoResult.videoPath,
      title: videoResult.title,
      description: `${videoResult.title}\n\n🌍 Bringing the world to you.\n\nFollow Mr. WorldWideWebster for more amazing global content!\n\n#global #travel #culture #${videoResult.videoType}`,
      tags: ['mr worldwidewebster', 'global', 'travel', 'culture', videoResult.videoType],
      publishAt: publishDate.toISOString(),
    });

    this.logger.success(`Scheduled: ${uploadResult.url} (publishes ${publishDate.toISOString()})`);

    return {
      scheduled: true,
      videoId: uploadResult.videoId,
      url: uploadResult.url,
      publishAt: publishDate.toISOString(),
    };
  }

  /**
   * Save pipeline metadata to disk
   */
  _saveMetadata(results) {
    const metadata = {
      runDate: new Date().toISOString(),
      totalVideos: results.length,
      videos: results.map(r => ({
        title: r.title,
        videoPath: r.videoPath,
        duration: r.duration,
        videoType: r.videoType,
        uploadUrl: r.uploadResult?.url,
        publishAt: r.uploadResult?.publishAt,
        error: r.error,
      })),
    };

    const metadataDir = path.join(this.outputDir, 'metadata');
    if (!fs.existsSync(metadataDir)) {
      fs.mkdirSync(metadataDir, { recursive: true });
    }

    const metadataPath = path.join(metadataDir, `run_${Date.now()}.json`);
    fs.writeFileSync(metadataPath, JSON.stringify(metadata, null, 2));
    this.logger.info(`Metadata saved: ${metadataPath}`);
  }
}

// CLI entry point
if (require.main === module) {
  const args = {};
  process.argv.slice(2).forEach(arg => {
    const [key, value] = arg.split('=');
    if (key.startsWith('--')) {
      args[key.slice(2)] = value || 'true';
    }
  });

  const runner = new WeeklyRunner();
  runner.run(args)
    .then(result => {
      console.log(JSON.stringify(result, null, 2));
      process.exit(result.failed > 0 ? 1 : 0);
    })
    .catch(error => {
      console.error('Pipeline failed:', error.message);
      process.exit(1);
    });
}

module.exports = { WeeklyRunner };