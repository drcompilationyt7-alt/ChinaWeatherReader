#!/usr/bin/env node

/**
 * Mr. WorldWideWebster — Gemini-First Runner (v2.0)
 * 
 * Modes:
 *   --mode daily     = Type 1 clip pipeline (6am)
 *   --mode explainer = Type 2 explainer pipeline (8am)  
 *   --mode nightly   = Trend bank updates
 *   --mode temp      = Temp Explainer - channel shorts reposter
 *   --mode quiz      = World Quiz: original geography quiz shorts
 *   --count N        = (quiz) shorts per run, scheduled QUIZ_SPACING_HOURS apart
 *   --country X      = Override country pick
 */

const path = require('path');
const fs = require('fs');
const config = require('./config');
const { Logger } = require('./logger');
const { getGeminiService } = require('./gemini-service');
const { getGeminiCLI } = require('./gemini-cli-runner');
const { syncPerformance, loadInsights } = require('./performance-tracker');
const learn = require('./learning-models');

const logger = new Logger('GHRunner');

const ALL_COUNTRIES = config.countries || [
  'China', 'Japan', 'South Korea', 'Thailand', 'Vietnam',
  'India', 'Indonesia', 'Brazil', 'Mexico', 'France',
  'Germany', 'Italy', 'Spain', 'UK', 'Egypt',
  'Nigeria', 'Australia', 'Global'
];

class DailyRunner {
  constructor() {
    this.memory = {};
    this.memoryPath = path.join(__dirname, '..', 'memory');
    this.youtubeBridge = null;
    this.gemini = null;
  }

  async initialize() {
    logger.header('ASIAN EDITS — Gemini-First Runner v2.0');
    this.gemini = getGeminiService();
    const stats = this.gemini.getStats();
    logger.info(`Gemini: ${stats.keysLoaded} API keys loaded`);

    const cli = getGeminiCLI();
    logger.info(`Gemini CLI: ${cli.isAvailable() ? '✅' : '❌'}`);

    this._loadMemory();

    try {
      const { YouTubeBridge } = require('../youtube-automation/youtube-bridge');
      this.youtubeBridge = new YouTubeBridge();
      await this.youtubeBridge.initialize();
      logger.info(`YouTube: ${this.youtubeBridge.isAuthenticated() ? '✅' : '❌'}`);
    } catch (e) {
      logger.warn(`YouTube Bridge: ${e.message}`);
    }

    logger.success('Initialized');
  }

  _loadMemory() {
    if (!fs.existsSync(this.memoryPath)) fs.mkdirSync(this.memoryPath, { recursive: true });
    const fp = path.join(this.memoryPath, 'channel-memory.json');
    try {
      if (fs.existsSync(fp)) {
        this.memory = JSON.parse(fs.readFileSync(fp, 'utf8'));
        logger.info(`Memory: ${this.memory.totalVideosPosted || 0} videos posted`);
        return;
      }
    } catch {}
    this.memory = { channelName: 'Asian Edits', totalVideosPosted: 0, countriesUsedThisWeek: [] };
    this._saveMemory();
  }

  _saveMemory() {
    fs.writeFileSync(path.join(this.memoryPath, 'channel-memory.json'), JSON.stringify(this.memory, null, 2));
  }

  _pickCountry(overrideCountry) {
    if (overrideCountry) return overrideCountry;
    const used = this.memory.countriesUsedThisWeek || [];
    const available = ALL_COUNTRIES.filter(c => !used.includes(c));
    const pool = available.length > 0 ? available : ALL_COUNTRIES;
    return [...pool].sort(() => Math.random() - 0.5)[0];
  }

  async _uploadToYouTube(videoData) {
    if (!this.youtubeBridge?.isAuthenticated()) {
      logger.warn('YouTube not authenticated — skipping upload');
      return null;
    }
    try {
      const uploadParams = {
        videoPath: videoData.videoPath,
        title: videoData.title,
        description: videoData.description,
        tags: videoData.tags || ['asian edits', 'shorts'],
        categoryId: videoData.categoryId,
        localizations: videoData.localizations,
      };
      // Pass publishAt (per video, else from env) if set
      if (videoData.publishAt || process.env.PUBLISH_AT) {
        uploadParams.publishAt = videoData.publishAt || process.env.PUBLISH_AT;
      }
      const r = await this.youtubeBridge.uploadVideo(uploadParams);
      logger.success(`Uploaded: ${r.url}`);

      if (videoData.playlistTitle && r.videoId) {
        await this.youtubeBridge.addToPlaylist(r.videoId, videoData.playlistTitle,
          'A new world quiz every day from Mr. WorldWideWebster. How many can you get?');
      }

      // Post the full description as a comment (visible on Shorts where descriptions are often hidden)
      // Works for both Type 1 and Temp Type 2 pipelines; quiz shorts post a short question instead
      if ((videoData.comment || videoData.description) && r.videoId) {
        logger.info('Posting description as comment...');
        const channelHandle = process.env.YOUTUBE_HANDLE || '@Mr.WorldWideWebster';
        const commentText = videoData.comment || `${videoData.description}\n\n— ${channelHandle}`;
        const commentResult = await this.youtubeBridge.postComment(r.videoId, commentText);
        if (commentResult) {
          logger.success(`Comment posted: ${commentResult.commentId}`);
        } else {
          logger.warn('Failed to post description comment');
        }
      }

      return r;
    } catch (e) {
      logger.error(`Upload failed: ${e.message}`);
      return null;
    }
  }

  /**
   * Track a newly uploaded video in the posted-videos pool
   * so daily-boost can pick it up later.
   */
  _trackPostedVideo(url, title, country, extra = {}) {
    if (!url) return;
    try {
      const { BoostEngine } = require('../boost/boost-engine');
      const engine = new BoostEngine();
      engine.addPostedVideo(url, title, country, extra);
      logger.success(`Tracked posted video: ${url.substring(0, 50)}`);
    } catch (e) {
      logger.warn(`Track posted video error: ${e.message}`);
    }
  }

  async _sendDiscord(data) {
    try {
      const { DiscordBridge } = require('../discord/discord-bridge');
      const bridge = new DiscordBridge();
      await bridge.sendDailySummary(data);
      try { await bridge.destroy(); } catch {}
    } catch {}
  }

  async runDaily(overrideCountry, skipRanking = false, searchQuery = null) {
    logger.header('DAILY: Type 1 Clip Pipeline (Channel-Sourced)');

    // The new pipeline picks 10 random channels, classifies countries via Gemini,
    // and selects the video whose country has been posted the LEAST this week.
    const country = this._pickCountry(overrideCountry);
    logger.info(`Starting country hint: ${country}`);

    for (const dir of [config.paths.clips, config.paths.assets]) {
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    }

    // ─── Self-learning: refresh what works on our channel ────────
    // Pulls our uploads' stats, scores them and saves insights that the
    // pipeline uses for candidate selection and title picking.
    let insights = null;
    try {
      insights = await syncPerformance(this.youtubeBridge);
    } catch (e) {
      logger.warn(`Performance sync failed: ${(e.message || '').substring(0, 80)}`);
      insights = loadInsights();
    }
    if (insights) logger.info(`Learning: ${insights.summaryLine}`);
    const learningLine = insights ? insights.summaryLine : undefined;
    // stream the newly scored shorts into the online views predictor (River)
    try { learn.riverUpdate(); } catch (e) { logger.warn(`River update skipped: ${(e.message || '').substring(0, 60)}`); }

    const { runType1Pipeline } = require('../pipeline/type1-clip-pipeline');
    let result;
    try {
      result = await runType1Pipeline({
        outputDir: config.paths.clips,
        countriesUsedThisWeek: this.memory.countriesUsedThisWeek || [],
      });
    } catch (e) {
      logger.error(`Pipeline crash: ${e.message}`);
      result = { success: false, error: e.message };
    }

    if (!result.success) {
      logger.error(`Pipeline: ${result.error}`);
      await this._sendDiscord({ videos: [], countries: this.memory.countriesUsedThisWeek || [], totalVideos: this.memory.totalVideosPosted || 0, errors: [result.error], learning: learningLine });
      return { uploadedVideos: [], errors: [result.error] };
    }

    logger.info('Uploading...');
    const uploadResult = await this._uploadToYouTube(result);
    const uploaded = [];

    if (uploadResult) {
      uploaded.push({ title: result.title, url: uploadResult.url, country: result.country, geminiScore: result.geminiScore, editType: result.editType });
      this.memory.totalVideosPosted = (this.memory.totalVideosPosted || 0) + 1;
      if (!this.memory.countriesUsedThisWeek) this.memory.countriesUsedThisWeek = [];
      if (!this.memory.countriesUsedThisWeek.includes(result.country)) this.memory.countriesUsedThisWeek.push(result.country);
      if (this.memory.countriesUsedThisWeek.length > 7) this.memory.countriesUsedThisWeek = this.memory.countriesUsedThisWeek.slice(-7);
      this._saveMemory();
      this._trackPostedVideo(uploadResult.url, result.title, result.country, {
        videoId: uploadResult.videoId || null,
        sourceChannel: result.sourceChannel || null,
        sourceUrl: result.sourceUrl || null,
        sourceTitle: result.sourceTitle || null,
        summary: result.summary || null,
        category: result.category || null,
        eggs: result.eggs || [],
        titleSource: result.titleSource || null,
        watermark: result.watermarkMode || null,
      });
    }

    await this._sendDiscord({ videos: uploaded, countries: this.memory.countriesUsedThisWeek || [], totalVideos: this.memory.totalVideosPosted || 0, errors: [], learning: learningLine });

    // ─── Weekly: re-compile the DSPy title program against our stats ──
    // Runs after the upload so it never delays posting; the new program is
    // used from the next run on.
    try { learn.dspyOptimizeIfDue({ insights }); } catch (e) { logger.warn(`DSPy optimization skipped: ${(e.message || '').substring(0, 60)}`); }

    logger.header('SUMMARY');
    if (uploaded.length > 0) {
      logger.success(`✅ 1 short uploaded — "${result.title}"`);
      logger.success(`🌍 ${result.country} | ⭐ ${result.geminiScore}/10 | 🎬 ${result.editType} | 📺 @${result.sourceChannel || 'channel'}`);
    } else {
      logger.warn('⚠ Upload skipped');
    }

    return { uploadedVideos: uploaded, errors: [], exitCode: uploaded.length > 0 ? 0 : 1 };
  }

  /**
   * World Quiz: render and upload `count` original quiz shorts, scheduled
   * QUIZ_SPACING_HOURS apart starting at PUBLISH_AT (or immediately).
   */
  async runQuiz(count = 1) {
    logger.header(`WORLD QUIZ: ${count} short(s)`);
    const outDir = path.join(__dirname, '..', 'output', 'quiz');
    fs.mkdirSync(outDir, { recursive: true });

    // refresh our own stats first: the format / title learners read them
    let insights = null;
    try {
      insights = await syncPerformance(this.youtubeBridge);
    } catch (e) {
      logger.warn(`Performance sync failed: ${(e.message || '').substring(0, 80)}`);
      insights = loadInsights();
    }
    if (insights) logger.info(`Learning: ${insights.summaryLine}`);

    const { runWorldQuizPipeline, recordUpload } = require('../pipeline/world-quiz-pipeline');
    const spacing = parseFloat(process.env.QUIZ_SPACING_HOURS || '4');
    const base = process.env.PUBLISH_AT ? new Date(process.env.PUBLISH_AT) : null;
    const uploaded = [];
    const errors = [];

    for (let k = 0; k < count; k++) {
      let result = null;
      for (let attempt = 1; attempt <= 2 && !result; attempt++) {
        try {
          result = await runWorldQuizPipeline({ outputDir: outDir });
        } catch (e) {
          logger.warn(`Quiz render attempt ${attempt} failed: ${(e.message || '').substring(0, 200)}`);
        }
      }
      if (!result) { errors.push('quiz render failed'); continue; }

      const publishAt = base ? new Date(base.getTime() + k * spacing * 3600000).toISOString() : null;
      const up = await this._uploadToYouTube({ ...result, publishAt });
      if (!up) { errors.push('upload failed'); continue; }
      try { recordUpload(result, { ...up, publishAt }); } catch (e) { logger.warn(`Quiz history save failed: ${e.message}`); }
      this._trackPostedVideo(up.url, result.title, result.country, {
        videoId: up.videoId || null, category: result.category, titleSource: result.quiz.titleTemplate,
        quizFormat: result.quiz.format, quizTheme: result.quiz.theme,
      });
      this.memory.totalVideosPosted = (this.memory.totalVideosPosted || 0) + 1;
      this._saveMemory();
      uploaded.push({ title: result.title, url: up.url, country: result.country, editType: 'quiz', geminiScore: null });
      logger.success(`✅ ${result.quiz.format} quiz uploaded${publishAt ? ` (publishes ${publishAt})` : ''}: "${result.title}"`);
      try { fs.unlinkSync(result.videoPath); } catch {}
    }

    await this._sendDiscord({ videos: uploaded, countries: [], totalVideos: this.memory.totalVideosPosted || 0, errors, learning: insights ? insights.summaryLine : undefined });
    logger.header('SUMMARY');
    logger.info(`${uploaded.length}/${count} quiz shorts uploaded`);
    return { uploadedVideos: uploaded, errors, exitCode: uploaded.length > 0 ? 0 : 1 };
  }

  async runExplainer(overrideCountry) {
    logger.header('EXPLAINER: Type 2 Pipeline');

    const country = this._pickCountry(overrideCountry);
    logger.info(`Country: ${country}`);

    for (const dir of [config.paths.explainers, config.paths.assets]) {
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    }

    const { runType2Pipeline } = require('../pipeline/type2-explainer-pipeline');
    let result;
    try {
      result = await runType2Pipeline({ country, outputDir: config.paths.explainers, memory: this.memory });
    } catch (e) {
      logger.error(`Explainer crash: ${e.message}`);
      result = { success: false, error: e.message };
    }

    if (!result.success) {
      logger.error(`Explainer: ${result.error}`);
      return { uploadedVideos: [], errors: [result.error] };
    }

    const metadata = await this.gemini.generateTitle(result.country, '', result.title);
    const title = metadata?.title || result.title || `${result.country} Explainer`;
    const description = metadata?.description || `An explainer about ${result.country}. Follow Mr. WorldWideWebster! 🌍`;
    const tags = metadata?.tags || ['asian edits', 'explainer', result.country.toLowerCase(), 'shorts'];

    logger.info('Uploading explainer...');
    const uploadResult = await this._uploadToYouTube({
      videoPath: result.videoPath,
      title: title.substring(0, 100),
      description,
      tags,
    });

    const uploaded = [];
    if (uploadResult) {
      uploaded.push({ title, url: uploadResult.url, country: result.country, type: 'explainer' });
      this.memory.totalVideosPosted = (this.memory.totalVideosPosted || 0) + 1;
      this._saveMemory();
      this._trackPostedVideo(uploadResult.url, title, result.country);
    }

    logger.header('SUMMARY');
    if (uploaded.length > 0) {
      logger.success(`✅ Explainer uploaded — "${title}"`);
      logger.success(`🌍 ${result.country} | 📺 ${result.clipsApproved}/${result.totalClips} clips sourced`);
    }

    return { uploadedVideos: uploaded, errors: [], exitCode: uploaded.length > 0 ? 0 : 1 };
  }

  async runTempExplainer(overrideCountry) {
    logger.header('TEMP EXPLAINER: Channel Shorts Reposter');

    for (const dir of [path.join(__dirname, '..', 'output', 'temp-explainer'), config.paths.assets]) {
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    }

    const { runTempExplainerPipeline } = require('../pipeline/temp-explainer-pipeline');

    // Retry loop: try up to 3 full pipeline runs to ensure we post exactly 1 video
    const MAX_PIPELINE_RETRIES = 3;
    let lastError = '';

    for (let attempt = 1; attempt <= MAX_PIPELINE_RETRIES; attempt++) {
      logger.header(`TEMP PIPELINE ATTEMPT ${attempt}/${MAX_PIPELINE_RETRIES}`);

      let result;
      try {
        result = await runTempExplainerPipeline({ outputDir: path.join(__dirname, '..', 'output', 'temp-explainer') });
      } catch (e) {
        logger.error(`Temp pipeline crash: ${e.message}`);
        result = { success: false, error: e.message };
      }

      if (!result.success) {
        lastError = result.error || 'Pipeline failed';
        logger.warn(`Attempt ${attempt}: ${lastError}`);
        continue;
      }

      logger.info('Uploading temp explainer...');
      const uploadResult = await this._uploadToYouTube({
        videoPath: result.videoPath,
        title: result.title.substring(0, 100),
        description: result.description,
        tags: result.tags || ['asian edits', 'shorts', result.country.toLowerCase()],
      });

      if (uploadResult) {
        // Save video to memory AFTER successful upload
        try {
          const memory = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'memory', 'temp-explainer-memory.json'), 'utf8'));
          if (!memory.usedVideoIds) memory.usedVideoIds = [];
          if (!memory.usedChannels) memory.usedChannels = [];
          if (result.sourceId && !memory.usedVideoIds.includes(result.sourceId)) {
            memory.usedVideoIds.push(result.sourceId);
          }
          memory.lastRun = new Date().toISOString();
          fs.writeFileSync(path.join(__dirname, '..', 'memory', 'temp-explainer-memory.json'), JSON.stringify(memory, null, 2));
          logger.success(`Memory saved: ${memory.usedVideoIds.length} used video IDs`);
        } catch (e) {
          logger.warn(`Memory save failed: ${(e.message || '').substring(0, 60)}`);
        }

        // Save to channel-memory.json as well
        const uploaded = [{ title: result.title, url: uploadResult.url, country: result.country, type: 'temp' }];
        this.memory.totalVideosPosted = (this.memory.totalVideosPosted || 0) + 1;
        if (!this.memory.countriesUsedThisWeek) this.memory.countriesUsedThisWeek = [];
        if (!this.memory.countriesUsedThisWeek.includes(result.country)) this.memory.countriesUsedThisWeek.push(result.country);
        if (this.memory.countriesUsedThisWeek.length > 7) this.memory.countriesUsedThisWeek = this.memory.countriesUsedThisWeek.slice(-7);
        this._saveMemory();
        this._trackPostedVideo(uploadResult.url, result.title, result.country);

        logger.header('SUMMARY');
        logger.success(`✅ Temp video uploaded — "${result.title}"`);
        logger.success(`🌍 ${result.country} | 📺 Source: @${result.sourceChannel}`);

        return { uploadedVideos: uploaded, errors: [], exitCode: 0 };
      } else {
        lastError = 'Upload failed';
        logger.warn(`Attempt ${attempt}: Upload failed — retrying pipeline`);
      }
    }

    logger.error(`All ${MAX_PIPELINE_RETRIES} pipeline attempts failed. Last error: ${lastError}`);
    return { uploadedVideos: [], errors: [lastError], exitCode: 1 };
  }

  async runNightly() {
    logger.header('NIGHTLY: Trend Bank Update');
    const gemini = this.gemini;
    for (const country of ALL_COUNTRIES.slice(0, 5)) {
      try {
        const bankPath = path.join(config.paths.trendBanks, `${country.toLowerCase().replace(/ /g, '-')}.json`);
        if (!fs.existsSync(bankPath)) continue;
        const bank = JSON.parse(fs.readFileSync(bankPath, 'utf8'));
        const currentKeywords = bank.keywords.filter(k => k.status === 'active').map(k => k.term);
        const newKeywords = await gemini.generateQueries(country, currentKeywords, 5);
        if (Array.isArray(newKeywords)) {
          const today = new Date().toISOString().split('T')[0];
          for (const kw of newKeywords) {
            const clean = kw.replace(/#shorts|#tiktok|#reels|#douyin/gi, '').trim();
            if (clean.length > 3 && !currentKeywords.includes(clean)) {
              bank.keywords.push({ term: clean, added: today, status: 'active' });
              logger.info(`  + ${country}: ${clean}`);
            }
          }
          bank.lastUpdated = today;
          fs.writeFileSync(bankPath, JSON.stringify(bank, null, 2));
        }
        await new Promise(r => setTimeout(r, 2000));
      } catch (e) {
        logger.warn(`Nightly failed for ${country}: ${e.message.substring(0, 60)}`);
      }
    }
    logger.success('Nightly update complete');
  }

  async run() {
    await this.initialize();
    const args = process.argv.slice(2);
    const mode = args.includes('--mode') ? args[args.indexOf('--mode') + 1] : 'daily';
    const countryArg = args.includes('--country') ? args[args.indexOf('--country') + 1] : null;
    const publishAt = args.includes('--publish-at') ? args[args.indexOf('--publish-at') + 1] : null;
    const isPrivate = args.includes('--private');
    let exitCode = 0;

    const skipRanking = args.includes('--skip-ranking');
    const count = args.includes('--count') ? Math.max(1, parseInt(args[args.indexOf('--count') + 1], 10) || 1) : 1;
    const searchQuery = args.includes('--search-query') ? args[args.indexOf('--search-query') + 1] : null;

    try {
      // Set privacy status env var if --private is passed
      if (isPrivate) {
        process.env.DEFAULT_PRIVACY_STATUS = 'private';
        logger.info('Privacy set to: private');
      }

      // Store publishAt for upload steps to consume
      if (publishAt) {
        // Check if publish time has already passed — if so, upload public immediately
        const now = new Date();
        const publishTime = new Date(publishAt);
        if (publishTime <= now) {
          logger.info(`Publish time (${publishAt}) already passed — uploading as public immediately`);
          process.env.DEFAULT_PRIVACY_STATUS = 'public';
          // Clear PUBLISH_AT so youtube-bridge doesn't schedule it
          delete process.env.PUBLISH_AT;
        } else {
          process.env.PUBLISH_AT = publishAt;
          logger.info(`Publish scheduled at: ${publishAt}`);
        }
      }

      if (mode === 'daily') {
        const result = await this.runDaily(countryArg, skipRanking, searchQuery);
        exitCode = result.exitCode || 0;
      } else if (mode === 'explainer') {
        const result = await this.runExplainer(countryArg);
        exitCode = result.exitCode || 0;
      } else if (mode === 'quiz') {
        const result = await this.runQuiz(count);
        exitCode = result.exitCode || 0;
      } else if (mode === 'nightly') {
        await this.runNightly();
      } else if (mode === 'temp') {
        const result = await this.runTempExplainer(countryArg);
        exitCode = result.exitCode || 0;
      } else {
        console.log(`Unknown: ${mode}. Use daily, quiz, explainer, nightly, or temp`);
        exitCode = 1;
      }
    } catch (e) {
      logger.error(`Runner error: ${e.message}`);
      exitCode = 1;
    }

    logger.success('Done');
    setTimeout(() => process.exit(exitCode), 3000).unref();
    process.exit(exitCode);
  }
}

process.on('uncaughtException', e => console.error(e.message));
process.on('unhandledRejection', r => console.error(r?.message || r));
new DailyRunner().run().catch(e => { console.error(`Fatal: ${e.message}`); setTimeout(() => process.exit(1), 1000); });