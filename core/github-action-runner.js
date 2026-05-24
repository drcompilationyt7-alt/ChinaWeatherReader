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
  }

  async initialize() {
    this.logger.header('Mr. WorldWideWebster Pipeline v3');
    const key = process.env.OPENROUTER_API_KEY || '';
    if (!key) this.logger.warn('No OPENROUTER_API_KEY');

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
    const defaults = {
      'channel-memory.json': { channelName: 'Mr. WorldWideWebster', totalVideosPosted: 0, countriesUsedThisWeek: [], usedTopics: [] },
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
    } catch { return { queries: [`${c1} viral`,`${c2} food`,`${c3} dance`], countries: [c1, c2, c3] }; }
  }

  async _searchVideos(queries) {
    this.logger.info('Step 2: Searching Bilibili...');
    return await findUrlsForQueries(queries, 5);
  }

  async _downloadVideos(urls) {
    this.logger.info('Step 3: Downloading...');
    return await downloadVideos(urls, config.paths.clips);
  }

  async _analyzeVideos(videos, countries) {
    this.logger.info('Step 4: Analyzing with Nemotron...');
    if (videos.length === 0) return [];
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
- title_en: brief English title for the Short
- description: 1 sentence what this video shows
- creator_name: name of content creator/person in video (if recognizable)
- content_name: name of meme, song, dance, trend (if known)
- has_dialogue: bool
- language_detected: "english"|"other"
- translation_needed: bool
- subtitle_text: English translation if not English
- explainer_text: if explainer, short "What is this?" text
- best_start_time: seconds (0-10) where the content starts
- duration_needed: seconds (15-30) for the clip
- audio_description: what music/sounds`,
          v.path, 'Analyze', { useVideo: true, temperature: 0.3 }
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
    return analyses;
  }

  async _createShorts(videos, countries) {
    this.logger.info('Step 5: Creating Shorts...');
    const { createShort, generateVoiceover } = require('./clip-editor');
    const dir = config.paths.clips;
    const shorts = [];

    const country = countries?.[0] || 'Global';

    for (const v of videos.slice(0, 3)) {
      const a = v.analysis || {};
      const titleForShort = a.title_en || a.content_name || v.title || 'Global content';
      const introText = a.type === 'explainer' ? 'What is this?' : 'Here we present you';
      const titleText = a.creator_name 
        ? `${a.creator_name} - ${titleForShort}`.substring(0, 60)
        : titleForShort.substring(0, 60);
      
      // Generate voiceover for explainer
      let voiceoverPath = null;
      const expText = a.explainer_text || `What is this? ${titleForShort}`;
      if (a.type === 'explainer') {
        const vDir = path.join(config.paths.assets, 'voiceovers');
        if (!fs.existsSync(vDir)) fs.mkdirSync(vDir, { recursive: true });
        try { voiceoverPath = await generateVoiceover(expText, path.join(vDir, `vo_${Date.now()}.mp3`)); } catch {}
      }

      const outputPath = path.join(dir, `short_${Date.now()}.mp4`);
      const result = await createShort(v.path, {
        type: a.type || 'clip',
        startTime: a.best_start_time || 5,
        duration: Math.min(a.duration_needed || 25, 60),
        introText,
        titleText,
        countryText: country,
        textOverlay: a.translation_needed && a.subtitle_text ? a.subtitle_text : '',
        voiceoverPath,
        outputPath,
      });

      if (result) {
        shorts.push({
          path: result,
          title: `${titleForShort} #Shorts`.substring(0, 100),
          type: a.type || 'clip',
          analysis: a,
          country,
        });
      }
    }

    this.logger.success(`Created ${shorts.length} Shorts`);
    return shorts;
  }

  async _generateDescription(videoTitle, country, category, contentName, creatorName) {
    try {
      return await this.ai.chat(
        `Write a YouTube Shorts description for Mr. WorldWideWebster.
Title: ${videoTitle}
Country: ${country}
Category: ${category}
Creator: ${creatorName || 'Unknown'}
Content: ${contentName || 'Global trend'}

Write 2-3 sentences. Include country flag, what this content is, and follow CTA.`,
        `Description for ${videoTitle}`,
        { useCheapModel: true, temperature: 0.7 }
      );
    } catch {
      return `\ud83c\udf0d From ${country}: ${videoTitle}. Follow Mr. WorldWideWebster for daily global content!`;
    }
  }

  async runDaily() {
    this.logger.header('DAILY: Bilibili -> Download -> Analyze -> Create Shorts -> Upload');
    const errors = [];
    const uploaded = [];

    const { queries, countries } = await this._generateQueries();
    const urls = await this._searchVideos(queries);
    const downloaded = await this._downloadVideos(urls);
    const analyzed = await this._analyzeVideos(downloaded, countries);
    const shorts = await this._createShorts(analyzed, countries);

    for (const s of shorts) {
      const a = s.analysis || {};
      const desc = await this._generateDescription(s.title, s.country, a.category || 'trending', a.content_name, a.creator_name);
      
      const r = await this._uploadToYouTube({
        videoPath: s.path,
        title: s.title,
        description: desc,
        type: 'shorts',
        tags: ['mr worldwidewebster', 'shorts', s.country.toLowerCase(), a.category || 'trending', a.type || 'clip'].filter(Boolean),
      });
      if (r) uploaded.push({ title: s.title, url: r.url, type: s.type });
      else errors.push(`Upload failed: ${s.title}`);
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
    this.logger.info(`URLs: ${urls.length} | Downloaded: ${downloaded.length} | Shorts: ${shorts.length} | Uploaded: ${uploaded.length}`);
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
    this.logger.success(`Done: ${mode}`);
  }
}

process.on('uncaughtException', e => console.error(`${e.message}`));
process.on('unhandledRejection', r => console.error(`${r?.message||r}`));
new GitHubActionsRunner().run().catch(e => { console.error(`Fatal: ${e.message}`); process.exit(1); });