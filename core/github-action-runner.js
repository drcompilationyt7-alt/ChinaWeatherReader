#!/usr/bin/env node

/**
 * Mr. WorldWideWebster — Gemini-First Runner (v2.0)
 * 
 * Modes:
 *   --mode daily     = Type 1 clip pipeline (6am)
 *   --mode explainer = Type 2 explainer pipeline (8am)  
 *   --mode nightly   = Trend bank updates
 *   --country X      = Override country pick
 */

const path = require('path');
const fs = require('fs');
const config = require('./config');
const { Logger } = require('./logger');
const { getGeminiService } = require('./gemini-service');
const { getGeminiCLI } = require('./gemini-cli-runner');

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
    logger.header('MR. WORLDWIDEWEBSTER — Gemini-First Runner v2.0');
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
    this.memory = { channelName: 'Mr. WorldWideWebster', totalVideosPosted: 0, countriesUsedThisWeek: [] };
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
      const r = await this.youtubeBridge.uploadVideo({
        videoPath: videoData.videoPath,
        title: videoData.title,
        description: videoData.description,
        tags: videoData.tags || ['mr worldwidewebster', 'shorts'],
      });
      logger.success(`Uploaded: ${r.url}`);
      return r;
    } catch (e) {
      logger.error(`Upload failed: ${e.message}`);
      return null;
    }
  }

  async _boostVideo(url) {
    if (!url) return;
    try {
      logger.info('Waiting 30s settle before boost...');
      await new Promise(r => setTimeout(r, 30000));
      const { BoostEngine } = require('../boost/boost-engine');
      const engine = new BoostEngine();
      const result = await engine.run({ url, views: parseInt(process.env.BOOST_MAX_VIEWS) || 75 });
      if (result.success) logger.success(`Boosted ${result.views} views`);
      else logger.warn(`Boost: ${result.error || 'no views'}`);
    } catch (e) {
      logger.warn(`Boost error: ${e.message}`);
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

  async runDaily(overrideCountry) {
    logger.header('DAILY: Type 1 Clip Pipeline');

    const country = this._pickCountry(overrideCountry);
    logger.info(`Country: ${country}`);

    for (const dir of [config.paths.clips, config.paths.assets]) {
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    }

    const { runType1Pipeline } = require('../pipeline/type1-clip-pipeline');
    let result;
    try {
      result = await runType1Pipeline({ country, outputDir: config.paths.clips });
    } catch (e) {
      logger.error(`Pipeline crash: ${e.message}`);
      result = { success: false, error: e.message };
    }

    if (!result.success) {
      logger.error(`Pipeline: ${result.error}`);
      await this._sendDiscord({ videos: [], countries: this.memory.countriesUsedThisWeek || [], totalVideos: this.memory.totalVideosPosted || 0, errors: [result.error] });
      return { uploadedVideos: [], errors: [result.error] };
    }

    logger.info('Uploading...');
    const uploadResult = await this._uploadToYouTube(result);
    const uploaded = [];

    if (uploadResult) {
      uploaded.push({ title: result.title, url: uploadResult.url, country: result.country, geminiScore: result.geminiScore, editType: result.editType });
      this.memory.totalVideosPosted = (this.memory.totalVideosPosted || 0) + 1;
      if (!this.memory.countriesUsedThisWeek) this.memory.countriesUsedThisWeek = [];
      if (!this.memory.countriesUsedThisWeek.includes(country)) this.memory.countriesUsedThisWeek.push(country);
      if (this.memory.countriesUsedThisWeek.length > 7) this.memory.countriesUsedThisWeek = this.memory.countriesUsedThisWeek.slice(-7);
      this._saveMemory();
      await this._boostVideo(uploadResult.url);
    }

    await this._sendDiscord({ videos: uploaded, countries: this.memory.countriesUsedThisWeek || [], totalVideos: this.memory.totalVideosPosted || 0, errors: [] });

    logger.header('SUMMARY');
    if (uploaded.length > 0) {
      logger.success(`✅ 1 short uploaded — "${result.title}"`);
      logger.success(`🌍 ${country} | ⭐ ${result.geminiScore}/10 | 🎬 ${result.editType}`);
    } else {
      logger.warn('⚠ Upload skipped');
    }

    return { uploadedVideos: uploaded, errors: [], exitCode: uploaded.length > 0 ? 0 : 1 };
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

    // Generate title + upload
    const metadata = await this.gemini.generateTitle(result.country, '', result.title);
    const title = metadata?.title || result.title || `${result.country} Explainer`;
    const description = metadata?.description || `An explainer about ${result.country}. Follow Mr. WorldWideWebster! 🌍`;
    const tags = metadata?.tags || ['mr worldwidewebster', 'explainer', result.country.toLowerCase(), 'shorts'];

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
      await this._boostVideo(uploadResult.url);
    }

    logger.header('SUMMARY');
    if (uploaded.length > 0) {
      logger.success(`✅ Explainer uploaded — "${title}"`);
      logger.success(`🌍 ${result.country} | 📺 ${result.clipsApproved}/${result.totalClips} clips sourced`);
    }

    return { uploadedVideos: uploaded, errors: [], exitCode: uploaded.length > 0 ? 0 : 1 };
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
    let exitCode = 0;

    try {
      if (mode === 'daily') {
        const result = await this.runDaily(countryArg);
        exitCode = result.exitCode || 0;
      } else if (mode === 'explainer') {
        const result = await this.runExplainer(countryArg);
        exitCode = result.exitCode || 0;
      } else if (mode === 'nightly') {
        await this.runNightly();
      } else {
        console.log(`Unknown: ${mode}. Use daily, explainer, or nightly`);
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