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
    this.queries = [];
    this.countries = [];
  }

  async initialize() {
    this.logger.header('Mr. WorldWideWebster Pipeline v3');
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
        try { this.memory[f.replace('.json', '')] = JSON.parse(fs.readFileSync(fp, 'utf8')); }
        catch { this.memory[f.replace('.json', '')] = d; fs.writeFileSync(fp, JSON.stringify(d, null, 2)); }
      } else { this.memory[f.replace('.json', '')] = d; fs.writeFileSync(fp, JSON.stringify(d, null, 2)); }
    }
  }
  _saveMemory(k, d) { fs.writeFileSync(path.join(this.memoryPath, `${k}.json`), JSON.stringify(d, null, 2)); this.memory[k] = d; }

  async _uploadToYouTube(v) {
    if (!this.youtubeBridge?.isAuthenticated()) return null;
    try {
      const r = await this.youtubeBridge.uploadVideo({
        videoPath: v.videoPath, title: v.title, description: v.description,
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
    const all = ['Nigeria','Japan','Germany','Brazil','India','Mexico','UK','South Korea','Egypt','Italy','Spain','Thailand','Vietnam','France','Australia','China','Indonesia'];
    const avail = all.filter(c => !used.includes(c));
    const c1 = avail.length > 0 ? avail[Math.floor(Math.random()*avail.length)] : all[Math.floor(Math.random()*all.length)];
    const c2 = all[Math.floor(Math.random()*all.length)];
    const c3 = all[Math.floor(Math.random()*all.length)];
    try {
      const r = await this.ai.chatJSON(`Generate 5 search queries for trending MEME/STREAMER/EXPLAINER videos from ${c1}, ${c2}, ${c3}. Return JSON array.`, `5 queries`, { useScriptModel:true, temperature:0.8 });
      const qs = Array.isArray(r) ? r.slice(0,5) : (r.queries ? r.queries.slice(0,5) : [`${c1} viral`,`${c2} trend`,`${c3} dance`]);
      this.queries = qs;
      this.countries = [c1, c2, c3];
      this.logger.success(`Queries: ${qs.join(' | ')}`);
      return { queries: qs, countries: [c1, c2, c3] };
    } catch { this.queries = [`${c1} viral`,`${c2} food`,`${c3} dance`]; this.countries = [c1, c2, c3]; return { queries: this.queries, countries: this.countries }; }
  }

  async _searchVideos(q) {
    this.logger.info('Step 2: Searching...');
    return await findUrlsForQueries(q, 5);
  }

  async _downloadVideos(u) {
    this.logger.info('Step 3: Downloading...');
    return await downloadVideos(u, config.paths.clips);
  }

  async _analyzeVideos(v) {
    this.logger.info('Step 4: Analyzing with Nemotron...');
    if (v.length === 0) return [];
    const analyses = [];
    for (let i = 0; i < v.length; i++) {
      const vv = v[i];
      this.logger.info(`Analyzing ${i+1}/${v.length}: ${vv.title.substring(0,50)}`);
      try {
        const p = await this.ai.chatWithVideo(
          `Return JSON: type:"meme"|"streamer"|"explainer"|"other", rank:1-10, category, title_en, description, creator_name, content_name, has_dialogue, language_detected, translation_needed, subtitle_text, explainer_text, best_start_time:0-10, duration_needed:15-30, audio_description`,
          vv.path, 'Analyze', { useVideo:true, temperature:0.3 }
        );
        let parsed;
        try { parsed = JSON.parse(p.replace(/```json?/gi,'').replace(/```/g,'').trim()); } catch { const m = p.match(/\{[\s\S]*\}/); parsed = m ? JSON.parse(m[0]) : { type:'other', rank:5 }; }
        analyses.push({ ...vv, analysis: parsed, index: i });
        this.logger.info(`Rank ${parsed.rank||'?'} | ${parsed.type||'?'} | ${parsed.title_en||''}`);
      } catch (e) {
        this.logger.warn(`Analysis: ${e.message.substring(0,80)}`);
        analyses.push({ ...vv, analysis:{type:'other',rank:5}, index:i });
      }
    }
    analyses.sort((a,b) => (b.analysis?.rank||0)-(a.analysis?.rank||0));
    return analyses;
  }

  async _createShorts(videos) {
    this.logger.info('Step 5: Creating Shorts...');
    const { createShort, generateVoiceover } = require('./clip-editor');
    const dir = config.paths.clips;
    const shorts = [];

    for (let i = 0; i < Math.min(videos.length, 3); i++) {
      const v = videos[i];
      const a = v.analysis || {};
      const query = this.queries[i] || '';
      
      // Generate voiceover for explainer
      let voiceoverPath = null;
      const expText = a.explainer_text || `What is this? ${a.title_en || 'global content'}`;
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
        query,
        countryText: this.countries[0] || 'Global',
        voiceoverPath,
        outputPath,
      });

      if (result) {
        shorts.push({ path: result, title: '', type: a.type || 'clip', analysis: a, query, country: this.countries[0] || 'Global' });
      }
    }
    this.logger.success(`Created ${shorts.length} Shorts`);
    return shorts;
  }

  async _generateTitleAndDesc(a, country, query) {
    const title_en = a.title_en || a.content_name || 'Global content';
    try {
      const result = await this.ai.chatJSON(
        `You write for Mr. WorldWideWebster channel. Generate a YouTube Shorts title and description.

Original query: "${query}"
Country: ${country}
Category: ${a.category || 'trending'}
Type: ${a.type || 'clip'}
Content: ${title_en}
Creator: ${a.creator_name || 'Unknown'}

Rules:
- Title: Catchy, clickable, max 70 chars. Include country flag emoji.
- Description: 2-3 sentences. Mention the country, what the content is, and a call to follow.
- Do NOT use the original query as the title. Be creative.

Return JSON: { "title": "...", "description": "..." }`,
        `Title & description for ${title_en}`,
        { useScriptModel: true, temperature: 0.8 }
      );
      return { title: result.title?.substring(0,100) || `${title_en} #Shorts`, description: result.description || `From ${country} \ud83c\udf0d` };
    } catch {
      const flag = { Nigeria:'\ud83c\uddf3\ud83c\uddec', Japan:'\ud83c\uddef\ud83c\uddf5', Germany:'\ud83c\udde9\ud83c\uddea', Brazil:'\ud83c\udde7\ud83c\uddf7', India:'\ud83c\uddee\ud83c\uddf3', UK:'\ud83c\uddec\ud83c\udde7', China:'\ud83c\udde8\ud83c\uddf3' }[country] || '\ud83c\udf0d';
      return { title: `${flag} ${title_en} #Shorts`.substring(0,100), description: `${flag} From ${country}: ${title_en}. Follow for more global content!` };
    }
  }

  async runDaily() {
    this.logger.header('DAILY: Search -> Download -> Analyze -> Create Shorts -> Upload');
    const errors = [];
    const uploaded = [];

    const { queries, countries } = await this._generateQueries();
    const urls = await this._searchVideos(queries);
    const downloaded = await this._downloadVideos(urls);
    const analyzed = await this._analyzeVideos(downloaded);
    const shorts = await this._createShorts(analyzed);

    for (const s of shorts) {
      const { title, description } = await this._generateTitleAndDesc(s.analysis, s.country, s.query);
      
      const r = await this._uploadToYouTube({
        videoPath: s.path,
        title,
        description,
        tags: ['mr worldwidewebster', 'shorts', s.country.toLowerCase(), s.analysis?.category || 'trending', s.type].filter(Boolean),
      });
      if (r) uploaded.push({ title, url: r.url, type: s.type });
      else errors.push(`Upload failed: ${title}`);
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
    this.logger.success(`Done`);
  }
}

process.on('uncaughtException', e => console.error(`${e.message}`));
process.on('unhandledRejection', r => console.error(`${r?.message||r}`));
new GitHubActionsRunner().run().catch(e => { console.error(`Fatal: ${e.message}`); process.exit(1); });