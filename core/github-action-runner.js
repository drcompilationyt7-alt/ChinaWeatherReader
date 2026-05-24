#!/usr/bin/env node

const path = require('path');
const fs = require('fs');
const config = require('./config');
const { AIService } = require('./ai-service');
const { Logger } = require('./logger');
const { findUrlsForQueries } = require('../sourcing/finder-controller');
const { downloadVideos } = require('./downloader');
const { rankVideos, generateExplainerContent } = require('./url-ranker');

class GitHubActionsRunner {
  constructor() {
    this.logger = new Logger('GHAction');
    this.ai = null;
    this.memory = {};
    this.memoryPath = path.join(__dirname, '..', 'memory');
    this.youtubeBridge = null;
    this.queries = [];
    this.countries = [];
  }

  async initialize() {
    this.logger.header('Mr. WorldWideWebster Pipeline v4');
    this.ai = new AIService();
    await this.ai.waitForInit();
    this._loadMemory();
    try {
      const { YouTubeBridge } = require('../youtube-automation/youtube-bridge');
      this.youtubeBridge = new YouTubeBridge();
      await this.youtubeBridge.initialize();
    } catch (e) { this.logger.warn(`YouTube bridge: ${e.message}`); }
    this.logger.success('Initialized');
  }

  _loadMemory() {
    if (!fs.existsSync(this.memoryPath)) fs.mkdirSync(this.memoryPath, { recursive: true });
    const defs = {
      'channel-memory.json': { channelName: 'Mr. WorldWideWebster', totalVideosPosted: 0, countriesUsedThisWeek: [], usedTopics: [] },
      'content-history.json': { videos: [] },
    };
    for (const [f, d] of Object.entries(defs)) {
      const fp = path.join(this.memoryPath, f);
      if (fs.existsSync(fp)) {
        try { this.memory[f.replace('.json', '')] = JSON.parse(fs.readFileSync(fp, 'utf8')); } catch { this.memory[f.replace('.json', '')] = d; fs.writeFileSync(fp, JSON.stringify(d, null, 2)); }
      } else { this.memory[f.replace('.json', '')] = d; fs.writeFileSync(fp, JSON.stringify(d, null, 2)); }
    }
  }
  _saveMemory(k, d) { fs.writeFileSync(path.join(this.memoryPath, `${k}.json`), JSON.stringify(d, null, 2)); this.memory[k] = d; }

  async _uploadToYouTube(v) {
    if (!this.youtubeBridge?.isAuthenticated()) return null;
    try {
      const r = await this.youtubeBridge.uploadVideo({ videoPath: v.videoPath, title: v.title, description: v.description, tags: v.tags || ['mr worldwidewebster', 'shorts'] });
      this.logger.success(`Uploaded: ${r.url}`);
      return r;
    } catch (e) { this.logger.error(`Upload: ${e.message}`); return null; }
  }
  async _sendDiscord(type, data) {
    try {
      const { DiscordBridge } = require('../discord/discord-bridge');
      const b = new DiscordBridge();
      if (type === 'daily') await b.sendDailySummary(data);
      await b.destroy();
    } catch {}
  }

  async _generateQueries() {
    this.logger.info('Step 1: AI generating queries...');
    const ch = this.memory['channel-memory'] || {};
    const used = ch.countriesUsedThisWeek || [];
    const all = ['Nigeria','Japan','Germany','Brazil','India','Mexico','UK','South Korea','Egypt','Italy','Spain','Thailand','Vietnam','France','Australia','China','Indonesia'];
    const avail = all.filter(c => !used.includes(c));
    const c1 = avail.length > 0 ? avail[Math.floor(Math.random()*avail.length)] : all[Math.floor(Math.random()*all.length)];
    const c2 = all[Math.floor(Math.random()*all.length)];
    const c3 = all[Math.floor(Math.random()*all.length)];
    try {
      const r = await this.ai.chatJSON(`Generate 5 YouTube search queries for MEME/STREAMER/EXPLAINER videos from ${c1}, ${c2}, ${c3}. Return JSON array.`, `5 queries`, { useScriptModel:true, temperature:0.8 });
      const qs = Array.isArray(r) ? r.slice(0,5) : (r.queries ? r.queries.slice(0,5) : [`${c1} viral`,`${c2} trend`]);
      this.queries = qs; this.countries = [c1, c2, c3];
      this.logger.success(`Queries: ${qs.join(' | ')}`);
      return { queries: qs, countries: [c1, c2, c3] };
    } catch { this.queries = [`${c1} meme`,`${c2} viral`,`${c3} trend`]; this.countries = [c1, c2, c3]; return { queries: this.queries, countries: this.countries }; }
  }

  async runDaily() {
    this.logger.header('DAILY: 10 URLs -> AI Rank -> Download Top 3 -> Shorts -> Upload');
    const errors = [];
    const uploaded = [];

    // Step 1: Generate queries
    const { queries, countries } = await this._generateQueries();

    // Step 2: Search 10 URLs with metadata
    this.logger.info('Step 2: Searching 10 URLs with metadata...');
    const allUrls = await findUrlsForQueries(queries, 10);
    
    if (allUrls.length === 0) {
      this.logger.error('No URLs found');
      return { uploadedVideos: [], errors: ['No URLs found'] };
    }
    
    // Step 3: AI ranks URLs (no download needed)
    this.logger.info('Step 3: AI ranking URLs...');
    const { top3, explainer } = await rankVideos(allUrls, queries[0] || '', this.ai);
    
    if (top3.length === 0) {
      this.logger.warn('AI ranking failed, using first 3');
      top3.push(...allUrls.slice(0, 3));
    }
    
    // Log why each was chosen
    top3.forEach((v, i) => this.logger.info(`  #${i+1}: ${(v.title||'').substring(0, 60)}`));

    // Step 4: Download top 3 videos
    this.logger.info('Step 4: Downloading top 3 ranked videos...');
    const downloaded = await downloadVideos(top3, config.paths.clips);

    if (downloaded.length === 0) {
      this.logger.warn('No videos downloaded - upload failure expected');
    }

    // Step 5: Create Shorts (NO Nemotron - use AI ranking + smart timing)
    this.logger.info('Step 5: Creating Shorts...');
    const { createShort, generateVoiceover } = require('./clip-editor');
    const dir = config.paths.clips;
    const shorts = [];

    for (let i = 0; i < downloaded.length; i++) {
      const v = downloaded[i];
      const query = queries[i] || queries[0] || '';
      const country = countries[0] || 'Global';
      
      // Check if this is the explainer candidate
      const isExplainer = explainer && v.sourceUrl === explainer.url;
      
      // Generate voiceover for explainer
      let voiceoverPath = null;
      let explainerText = '';
      if (isExplainer) {
        this.logger.info(`Generating explainer for #${i+1}...`);
        try {
          const expContent = await generateExplainerContent(v, this.ai);
          explainerText = expContent.explainer_text || `What is this? ${expContent.content_name || v.title || 'global content'}`;
          
          // Generate voiceover
          const vDir = path.join(config.paths.assets, 'voiceovers');
          if (!fs.existsSync(vDir)) fs.mkdirSync(vDir, { recursive: true });
          try {
            // Use edge-tts for voiceover
            const safeText = explainerText.replace(/"/g, '\\"');
            const vPath = path.join(vDir, `exp_${Date.now()}.mp3`);
            const cmd = `edge-tts --voice "en-US-JennyNeural" --text "${safeText}" --write-media "${vPath}" 2>/dev/null`;
            require('child_process').execSync(cmd, { timeout: 30000 });
            if (fs.existsSync(vPath) && fs.statSync(vPath).size > 1000) voiceoverPath = vPath;
          } catch {}
        } catch {}
      }

      // Detect smart start time by finding where audio starts
      let startTime = 5; // default
      try {
        // Use ffprobe to detect first audio activity
        const info = require('child_process').execSync(
          `ffprobe -i "${v.path}" -show_entries stream=start_time -of csv=p=0 2>/dev/null | head -1`,
          { timeout: 5000, encoding: 'utf8' }
        ).trim();
        if (info && parseFloat(info) > 0 && parseFloat(info) < 30) startTime = parseFloat(info);
      } catch {}

      const outputPath = path.join(dir, `short_${Date.now()}.mp4`);
      const result = await createShort(v.path, {
        type: isExplainer ? 'explainer' : 'clip',
        startTime,
        duration: 30,
        query,
        countryText: country,
        voiceoverPath,
        explainerText,
        outputPath,
      });

      if (result) {
        shorts.push({ path: result, query, country, isExplainer, sourceUrl: v.sourceUrl });
      }
    }

    this.logger.success(`Created ${shorts.length} Shorts`);

    // Step 6: Upload with AI-generated titles/descriptions
    for (const s of shorts) {
      const query = s.query || '';
      const country = s.country;
      try {
        const td = await this.ai.chatJSON(
          `You write for Mr. WorldWideWebster. Generate YouTube Shorts title+desc.
Query: "${query}"
Country: ${country}
Is Explainer: ${s.isExplainer}

Title: catchy, max 70 chars, with flag emoji. Description: 2-3 sentences.
Return JSON: {"title":"...","description":"..."}`,
          `Title for ${query}`,
          { useCheapModel: true, temperature: 0.8 }
        );
        
        const title = (td.title || `\ud83c\udf0d ${query}`).substring(0, 100);
        const description = td.description || `\ud83c\udf0d From ${country}. Follow for more global content!`;
        
        const r = await this._uploadToYouTube({
          videoPath: s.path,
          title,
          description,
          tags: ['mr worldwidewebster', 'shorts', country.toLowerCase()].filter(Boolean),
        });
        if (r) uploaded.push({ title, url: r.url, type: s.isExplainer ? 'explainer' : 'clip' });
        else errors.push(`Upload failed: ${title}`);
      } catch {}
    }

    // Update memory
    const cm = this.memory['channel-memory'];
    cm.totalVideosPosted = (cm.totalVideosPosted||0) + uploaded.length;
    if (countries) {
      if (!cm.countriesUsedThisWeek) cm.countriesUsedThisWeek = [];
      for (const c of countries) { if (!cm.countriesUsedThisWeek.includes(c)) cm.countriesUsedThisWeek.push(c); }
      if (cm.countriesUsedThisWeek.length > 14) cm.countriesUsedThisWeek = cm.countriesUsedThisWeek.slice(-14);
    }
    this._saveMemory('channel-memory', cm);
    await this._sendDiscord('daily', { videos: uploaded, countries: cm.countriesUsedThisWeek, totalVideos: cm.totalVideosPosted, errors });

    this.logger.header('SUMMARY');
    this.logger.info(`URLs: ${allUrls.length} | Ranked & Downloaded: ${downloaded.length} | Shorts: ${shorts.length} | Uploaded: ${uploaded.length}`);
    if (errors.length) errors.forEach(e => this.logger.warn(`  ${e}`));
    return { uploadedVideos: uploaded, errors };
  }

  async run() {
    await this.initialize();
    const args = process.argv.slice(2);
    const mi = args.indexOf('--mode');
    const mode = mi !== -1 ? args[mi+1] : 'daily';
    if (mode === 'daily') await this.runDaily();
    else { console.log(`Unknown: ${mode}`); process.exit(1); }
    this.logger.success('Done');
  }
}

process.on('uncaughtException', e => console.error(`${e.message}`));
process.on('unhandledRejection', r => console.error(`${r?.message||r}`));
new GitHubActionsRunner().run().catch(e => { console.error(`Fatal: ${e.message}`); process.exit(1); });