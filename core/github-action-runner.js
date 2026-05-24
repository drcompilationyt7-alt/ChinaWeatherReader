#!/usr/bin/env node

const path = require('path');
const fs = require('fs');
const config = require('./config');
const { AIService } = require('./ai-service');
const { Logger } = require('./logger');
const { findUrlsForQueries } = require('../sourcing/finder-controller');
const { downloadVideos } = require('./downloader');

class GitHubActionsRunner {
  constructor() {
    this.logger = new Logger('GHAction');
    this.ai = null;
    this.memory = {};
    this.memoryPath = path.join(__dirname, '..', 'memory');
    this.youtubeBridge = null;
    this.clipEditor = null;
  }

  async initialize() {
    this.logger.header('Mr. WorldWideWebster Pipeline v3');
    const key = process.env.OPENROUTER_API_KEY || '';
    if (!key) this.logger.warn('No OPENROUTER_API_KEY');
    else this.logger.info(`OpenRouter key: ${key.length} chars`);

    this.ai = new AIService();
    await this.ai.waitForInit();
    const { ClipEditor } = require('./clip-editor');
    this.clipEditor = new ClipEditor();
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
    const defaults = {
      'channel-memory.json': { channelName: 'Mr. WorldWideWebster', totalVideosPosted: 0, countriesUsedThisWeek: [], usedTopics: [] },
      'trending-log.json': { trends: [] },
      'content-history.json': { videos: [] },
    };
    for (const [file, def] of Object.entries(defaults)) {
      const fp = path.join(this.memoryPath, file);
      if (fs.existsSync(fp)) {
        try { this.memory[file.replace('.json', '')] = JSON.parse(fs.readFileSync(fp, 'utf8')); }
        catch { this.memory[file.replace('.json', '')] = def; fs.writeFileSync(fp, JSON.stringify(def, null, 2)); }
      } else { this.memory[file.replace('.json', '')] = def; fs.writeFileSync(fp, JSON.stringify(def, null, 2)); }
    }
  }

  _saveMemory(key, data) {
    fs.writeFileSync(path.join(this.memoryPath, `${key}.json`), JSON.stringify(data, null, 2));
    this.memory[key] = data;
  }

  // Generate proper YouTube description with country context
  async _generateDescription(videoTitle, country, category) {
    try {
      const desc = await this.ai.chat(
        `You write YouTube Shorts descriptions for Mr. WorldWideWebster channel.
Channel niche: Global viral content, culture, trends from different countries.

Write a short description (max 3 sentences) for this video:
Title: ${videoTitle}
Country: ${country}
Category: ${category}

Include:
- What country/region this content is from
- Brief context about what's happening
- Call to follow

Example:
"🇳🇬 Hot from Nigeria! Watch this viral dance trend taking over Lagos right now. 

🌍 Bringing the world to you - follow Mr. WorldWideWebster for daily global content!"`,
        `Write description for ${videoTitle} (${country})`,
        { useCheapModel: true, temperature: 0.7 }
      );
      return desc.trim();
    } catch {
      return `🌍 From ${country} / ${videoTitle}. Follow for more global content!`;
    }
  }

  async _generateQueries() {
    this.logger.info('Step 1: AI generating queries...');
    const ch = this.memory['channel-memory'] || {};
    const used = ch.countriesUsedThisWeek || [];
    const all = ['Nigeria','Japan','Germany','Brazil','India','Mexico','UK','South Korea','Egypt','Italy','Spain','Thailand','Vietnam','France','Australia','China','Indonesia','Turkey','Russia','Argentina'];
    const avail = all.filter(c => !used.includes(c));
    const c1 = avail.length > 0 ? avail[Math.floor(Math.random() * avail.length)] : all[Math.floor(Math.random() * all.length)];
    const c2 = all[Math.floor(Math.random() * all.length)];
    const c3 = all[Math.floor(Math.random() * all.length)];
    try {
      const r = await this.ai.chatJSON(`Generate 5 search queries for trending MEME, STREAMER, and EXPLAINER videos from ${c1}, ${c2}, ${c3}. Return ONLY a JSON array of 5 strings.`, `5 queries for ${c1}, ${c2}, ${c3}`, { useScriptModel: true, temperature: 0.8 });
      const qs = Array.isArray(r) ? r.slice(0,5) : (r.queries ? r.queries.slice(0,5) : [`${c1} viral`,`${c2} trend`,`${c3} dance`,`funny moments`,`streamer highlights`]);
      this.logger.success(`Queries: ${qs.join(' | ')}`);
      return { queries: qs, countries: [c1, c2, c3] };
    } catch { return { queries: [`${c1} viral`,`${c2} food`,`${c3} dance`,`funny moments`,`streamer best`], countries: [c1, c2, c3] }; }
  }

  async _searchVideos(queries) {
    this.logger.info('Step 2: Searching Bilibili, TikTok, Douyin, YouTube...');
    return await findUrlsForQueries(queries, 5);
  }

  async _downloadVideos(urls) {
    this.logger.info('Step 3: Downloading...');
    return await downloadVideos(urls, config.paths.clips);
  }

  // Step 4: Nemotron video analysis + generate translations/descriptions
  async _analyzeAndRankVideos(videos, countries) {
    this.logger.info('Step 4: Analyzing with Nemotron...');
    if (videos.length === 0) return { ranked: [], explainer: null, clips: [] };
    const analyses = [];
    for (let i = 0; i < videos.length; i++) {
      const v = videos[i];
      this.logger.info(`Analyzing ${i+1}/${videos.length}: ${v.title.substring(0,50)}`);
      try {
        const analysis = await this.ai.chatWithVideo(
          `Analyze this video. Return JSON:
- type: "meme"|"streamer"|"explainer"|"other"
- rank: 1-10 (viral potential)
- category: music|dance|food|comedy|reaction|trend|culture|other
- title_en: brief English title
- description: 1 sentence description
- has_dialogue: bool (is someone speaking/talking?)
- language_detected: "english"|"other" (what language is spoken?)
- translation_needed: bool (true if not English)
- subtitle_text: if translation_needed, English translation of what's said
- explainer_text: if explainer type, short "What is this?" text
- best_start_time: seconds (0-10)
- duration_needed: seconds (15-30)
- audio_description: what music/sounds are in the clip`,
          v.path, 'Analyze this video', { useVideo: true, temperature: 0.3 }
        );
        let p;
        try { p = JSON.parse(analysis.replace(/```json?/gi,'').replace(/```/g,'').trim()); } catch { const m = analysis.match(/\{[\s\S]*\}/); p = m ? JSON.parse(m[0]) : { type:'other', rank:5 }; }
        analyses.push({ ...v, analysis: p, index: i });
        this.logger.info(`Rank ${p.rank||'?'}/10 | ${p.type||'?'} | ${p.title_en||''} | Lang:${p.language_detected||'?'}`);
      } catch (e) {
        this.logger.warn(`Analysis: ${e.message.substring(0,80)}`);
        analyses.push({ ...v, analysis:{type:'other',rank:5}, index:i });
      }
    }
    analyses.sort((a,b) => (b.analysis?.rank||0)-(a.analysis?.rank||0));
    const top3 = analyses.slice(0,3);
    const explainer = top3.find(v=>v.analysis?.type==='explainer') || top3.find(v=>v.analysis?.type==='other'&&(v.analysis?.rank||0)>=6) || top3[0];
    return { ranked:top3, explainer, clips:top3.filter(v=>v!==explainer) };
  }

  // Step 5: Edit videos - ensure shorts format, add subtitles if needed
  async _editVideos(_, explainer, clips) {
    this.logger.info('Step 5: Editing...');
    const dir = config.paths.clips;
    const edited = [];

    for (const c of clips) {
      // If video has dialogue in non-English, use translated text as overlay
      const textOverlay = c.analysis?.translation_needed && c.analysis?.subtitle_text 
        ? c.analysis.subtitle_text 
        : (c.analysis?.has_text_needed && c.analysis?.description ? c.analysis.description : '');

      const r = await this.clipEditor.editVideo(c.path, {
        type: c.analysis?.type==='streamer' ? 'streamer' : 'clip',
        startTime: c.analysis?.best_start_time || 5,
        duration: Math.min(c.analysis?.duration_needed || 20, 60), // Max 60s for shorts
        textOverlay,
        outputPath: path.join(dir, `clip_${Date.now()}.mp4`),
      });
      if (r) edited.push({ path:r, title:c.analysis?.title_en||c.title, type:'clip', platform:c.platform, analysis:c.analysis });
    }

    if (explainer) {
      const expText = explainer.analysis?.explainer_text || `What is this? ${explainer.analysis?.title_en||'global content'}`;
      const vDir = path.join(config.paths.assets, 'voiceovers');
      if (!fs.existsSync(vDir)) fs.mkdirSync(vDir, { recursive: true });
      let vPath = null;
      try { vPath = await this.clipEditor.generateVoiceover(expText, path.join(vDir, `exp_${Date.now()}.mp3`)); } catch {}
      
      // For explainer: add subtitle text if translation needed
      const textOverlay = explainer.analysis?.translation_needed && explainer.analysis?.subtitle_text 
        ? explainer.analysis.subtitle_text 
        : (explainer.analysis?.has_text_needed ? explainer.analysis?.description : '');

      const r = await this.clipEditor.editVideo(explainer.path, {
        type:'explainer',
        startTime: explainer.analysis?.best_start_time || 3,
        duration: Math.min(explainer.analysis?.duration_needed || 25, 60),
        voiceoverPath: vPath,
        voiceoverDuration: 5,
        textOverlay,
        outputPath: path.join(dir, `explain_${Date.now()}.mp4`),
      });
      if (r) edited.push({ path:r, title:explainer.analysis?.title_en||explainer.title, type:'explainer', platform:explainer.platform, analysis:explainer.analysis });
    }

    this.logger.success(`Edited ${edited.length} videos`);
    return edited;
  }

  async runDaily() {
    this.logger.header('DAILY: Search -> Download -> Analyze -> Edit -> Upload');
    const errors = [];
    const uploaded = [];

    const { queries, countries } = await this._generateQueries();
    const urls = await this._searchVideos(queries);
    const downloaded = await this._downloadVideos(urls);
    const { ranked, explainer, clips } = await this._analyzeAndRankVideos(downloaded, countries);
    const edited = await this._editVideos(ranked, explainer, clips);

    for (const v of edited) {
      // Generate proper description with country context
      const country = countries?.[0] || 'Global';
      const category = v.analysis?.category || 'trending';
      const description = await this._generateDescription(v.title, country, category);
      
      const r = await this._uploadToYouTube({
        videoPath: v.path,
        title: v.title.substring(0, 100),
        description,
        type: v.type,
        tags: ['mr worldwidewebster', 'shorts', country.toLowerCase(), category, v.platform].filter(Boolean),
      });
      if (r) uploaded.push({ title: v.title, url: r.url, type: v.type, platform: v.platform });
      else errors.push(`Upload failed: ${v.title}`);
    }

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
    this.logger.info(`URLs: ${urls.length} | Downloaded: ${downloaded.length} | Edited: ${edited.length} | Uploaded: ${uploaded.length}`);
    if (urls.length > 0) {
      const platforms = [...new Set(urls.map(u => u.platform))].join(', ');
      this.logger.info(`Platforms: ${platforms}`);
    }
    if (errors.length) errors.forEach(e => this.logger.warn(`  ${e}`));
    return { uploadedVideos: uploaded, errors };
  }

  async _uploadToYouTube(v) {
    if (!this.youtubeBridge || !this.youtubeBridge.isAuthenticated()) return null;
    try {
      const r = await this.youtubeBridge.uploadVideo({
        videoPath: v.videoPath,
        title: v.title,
        description: v.description || `${v.title}\n\n\ud83c\udf0d Bringing the world to you`,
        tags: v.tags || ['mr worldwidewebster', 'shorts'],
      });
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

  async run() {
    await this.initialize();
    const args = process.argv.slice(2);
    const mi = args.indexOf('--mode');
    const mode = mi !== -1 ? args[mi+1] : 'daily';
    if (mode === 'daily') await this.runDaily();
    else { console.log(`Unknown: ${mode}`); process.exit(1); }
    this.logger.success(`Done: ${mode}`);
  }
}

process.on('uncaughtException', e => console.error(`${e.message}`));
process.on('unhandledRejection', r => console.error(`${r?.message||r}`));
new GitHubActionsRunner().run().catch(e => { console.error(`Fatal: ${e.message}`); process.exit(1); });