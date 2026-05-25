#!/usr/bin/env node

const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
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

    // Bad words that should block upload
    this.bannedWords = [
      'fuck', 'shit', 'bitch', 'asshole', 'motherfucker', 'dick', 'cunt',
      'pussy', 'bastard', 'whore', 'slut', 'damn', 'cock', 'nigger', 'nigga',
      'faggot', 'retard', 'chink', 'spic', 'kike', 'gook', 'raghead',
      'cracker', 'tranny', 'dyke', 'twat'
    ];
  }

  /**
   * Check if text contains banned profanity.
   * Returns the first banned word found, or null if clean.
   */
  _hasProfanity(text) {
    if (!text) return null;
    const lower = text.toLowerCase();
    for (const word of this.bannedWords) {
      const regex = new RegExp(`\\b${word}\\b`, 'i');
      if (regex.test(lower)) {
        return word;
      }
    }
    const leetPatterns = [
      /\bf[u4]ck\b/i, /\bf[u4]cking\b/i, /\bsh[i1!]t\b/i,
      /\bb[i1!]tch\b/i, /\bb[a4]st[a4]rd\b/i, /\bwh[o0]re\b/i,
      /\bn[i1!]gg[a4e3]\b/i, /\bc[u4]nt\b/i
    ];
    for (const pattern of leetPatterns) {
      if (pattern.test(lower)) {
        return lower.match(pattern)[0];
      }
    }
    return null;
  }

  async initialize() {
    this.logger.header('Mr. WorldWideWebster Pipeline v5');
    this.logger.info(`OpenRouter keys in env: KEY=${!!process.env.OPENROUTER_API_KEY} KEY_2=${!!process.env.OPENROUTER_API_KEY_2} KEY_3=${!!process.env.OPENROUTER_API_KEY_3} KEY_4=${!!process.env.OPENROUTER_API_KEY_4}`);

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
    if (!this.youtubeBridge?.isAuthenticated()) {
      this.logger.warn('YouTube not authenticated - skipping upload');
      return null;
    }
    try {
      const r = await this.youtubeBridge.uploadVideo({ videoPath: v.videoPath, title: v.title, description: v.description, tags: v.tags || ['mr worldwidewebster', 'shorts'] });
      this.logger.success(`Uploaded: ${r.url}`);
      return r;
    } catch (e) {
      this.logger.error(`Upload FAILED: ${e.message}`);
      if (e.stack) this.logger.error(`Stack: ${e.stack.substring(0, 300)}`);
      return null;
    }
  }

  async _boostVideo(videoUrl) {
    if (!videoUrl) return;
    try {
      this.logger.info(`Boosting: ${videoUrl}`);
      const { BoostEngine } = require('../boost/boost-engine');
      const engine = new BoostEngine();
      const result = await engine.run({
        url: videoUrl,
        views: parseInt(process.env.BOOST_MAX_VIEWS) || 75,
      });
      if (result.success) {
        this.logger.success(`Boosted ${result.views} views`);
      }
    } catch (e) {
      this.logger.warn(`Boost failed: ${e.message}`);
    }
  }

  async _boostOldVideos() {
    const history = this.memory['content-history'];
    if (!history?.videos) return;
    const oneWeekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
    const oldVideos = history.videos.filter(v => {
      if (v.type !== 'shorts' && v.type !== 'explainer') return false;
      const uploaded = new Date(v.uploadedAt || v.createdAt || 0).getTime();
      return uploaded < oneWeekAgo && uploaded > 0;
    });
    this.logger.info(`Found ${oldVideos.length} videos older than 1 week to boost`);
    for (const v of oldVideos) {
      if (v.url) {
        await this._boostVideo(v.url);
      }
    }
  }

  async _sendDiscord(type, data) {
    try {
      const { DiscordBridge } = require('../discord/discord-bridge');
      const b = new DiscordBridge();
      if (type === 'daily') await b.sendDailySummary(data);
      await b.destroy();
    } catch {}
  }

  async _transcribeAudio(videoPath) {
    const audioDir = path.join(config.paths.assets, 'audio');
    if (!fs.existsSync(audioDir)) fs.mkdirSync(audioDir, { recursive: true });
    const audioPath = path.join(audioDir, `audio_${Date.now()}.mp3`);
    try {
      this.logger.info('Extracting audio for transcription...');
      execSync(`ffmpeg -y -i "${videoPath}" -t 60 -vn -acodec libmp3lame -ab 64k "${audioPath}" 2>/dev/null`, { timeout: 30000 });
      if (!fs.existsSync(audioPath) || fs.statSync(audioPath).size < 1000) return null;
      this.logger.info('Transcribing with faster-whisper...');
      const pyPath = audioPath.replace(/\\/g, '\\\\');
      const pyCmd = `python3 -c "
from faster_whisper import WhisperModel
model = WhisperModel('base', device='cpu', compute_type='int8')
segments, info = model.transcribe('${pyPath}', language='en')
text = ' '.join(seg.text for seg in segments)
print(text[:1000] if text else '')
" 2>&1`;
      const output = execSync(pyCmd, { timeout: 120000, encoding: 'utf8', maxBuffer: 10*1024*1024 }).toString().trim();
      try { fs.unlinkSync(audioPath); } catch {}
      if (output && !output.includes('Error') && !output.includes('Traceback')) {
        this.logger.success(`Transcript: "${output.substring(0, 100)}..."`);
        return output.trim();
      }
      return null;
    } catch (error) {
      this.logger.warn(`Transcription failed: ${error.message.substring(0, 100)}`);
      try { fs.unlinkSync(audioPath); } catch {}
      return null;
    }
  }

  async _generateQueries() {
    this.logger.info('Step 1: Generating queries...');
    const ch = this.memory['channel-memory'] || {};
    const used = ch.countriesUsedThisWeek || [];
    const asianCountries = ['Japan','South Korea','China','Thailand','Vietnam','India','Indonesia'];
    const nonAsianCountries = ['Nigeria','Germany','Brazil','Mexico','UK','Egypt','Italy','Spain','France','Australia'];
    const all = [...nonAsianCountries, ...asianCountries];
    const avail = all.filter(c => !used.includes(c));
    const c1 = avail.length > 0 ? avail[Math.floor(Math.random()*avail.length)] : all[Math.floor(Math.random()*all.length)];
    const c2 = all[Math.floor(Math.random()*all.length)];
    const c3 = all[Math.floor(Math.random()*all.length)];
    const getSuffix = (country) => asianCountries.includes(country) ? `${country} douyin` : `${country}`;
    const fallbackQueries = [getSuffix(c1), getSuffix(c2), getSuffix(c3), `${c1} viral`, `${c2} trending`];
    try {
      const aiPromise = this.ai.chatJSON(
        `Generate 5 YouTube search queries for SHORT/DOUYIN-STYLE MEME/STREAMER/EXPLAINER videos from ${c1}, ${c2}, ${c3}. For Asian countries use "douyin" style. For others keep simple but trending. Return JSON array of strings`,
        `5 queries`, { useCheapModel: true, temperature: 0.8 }
      );
      const timeoutPromise = new Promise((_, reject) => setTimeout(() => reject(new Error('AI timed out')), 12000));
      const r = await Promise.race([aiPromise, timeoutPromise]);
      const qs = Array.isArray(r) ? r.slice(0,5) : (r.queries ? r.queries.slice(0,5) : fallbackQueries);
      this.queries = qs; this.countries = [c1, c2, c3];
      this.logger.success(`Queries: ${qs.join(' | ')}`);
      return { queries: qs, countries: [c1, c2, c3] };
    } catch {
      this.queries = fallbackQueries;
      this.countries = [c1, c2, c3];
      return { queries: fallbackQueries, countries: [c1, c2, c3] };
    }
  }

  async runDaily() {
    this.logger.header('DAILY: 10 URLs -> AI Rank -> Download Top 3 -> Shorts -> Upload + Boost');
    const errors = [];
    const uploaded = [];

    // Step 1-3: Search, rank, download
    const { queries, countries } = await this._generateQueries();
    this.logger.info('Step 2: Searching 10 URLs...');
    const allUrls = await findUrlsForQueries(queries, 10);
    if (allUrls.length === 0) return { uploadedVideos: [], errors: ['No URLs found'] };
    allUrls.forEach((u, i) => this.logger.info(`  URL ${i+1}: ${(u.title||'').substring(0,50)} | views: ${u.view_count || '?'}${u.isShort ? ' 📱' : ''}`));
    this.logger.info('Step 3: AI ranking...');
    const { top3 } = await rankVideos(allUrls, queries[0] || '', this.ai);
    if (top3.length === 0) top3.push(...allUrls.slice(0, 3));
    top3.forEach((v, i) => this.logger.info(`  #${i+1}: ${(v.title||'').substring(0,60)} | views: ${v.view_count || '?'}${v.isShort ? ' 📱' : ''}`));
    this.logger.info('Step 4: Downloading...');
    const downloaded = await downloadVideos(top3, config.paths.clips);
    if (downloaded.length === 0) this.logger.warn('No videos downloaded');

    // Step 5: Create Shorts
    this.logger.info('Step 5: Creating Shorts with free whisper + AI voiceover + Twemoji flag...');
    const { createShort } = require('./clip-editor');
    const dir = config.paths.clips;
    const shorts = [];

    for (let i = 0; i < downloaded.length; i++) {
      const v = downloaded[i];
      const query = queries[i] || queries[0] || '';
      const country = countries[i] || countries[0] || 'Global';
      const originalTitle = v.sourceUrl?.title || v.title || '';

      this.logger.info(`=== Short #${i+1}: ${country} ===`);
      let voiceoverPath = null;
      let voiceoverText = '';
      let transcript = null;

      try { transcript = await this._transcribeAudio(v.path); } catch {}

      // PROFANITY FILTER: Skip upload if transcript contains bad words
      if (transcript) {
        const badWord = this._hasProfanity(transcript);
        if (badWord) {
          this.logger.warn(`⛔ PROFANITY DETECTED in transcript: "${badWord}" — SKIPPING upload for ${country}`);
          this.logger.warn(`Transcript snippet: "${transcript.substring(0, 150)}..."`);
          errors.push(`Profanity blocked (${badWord}) for ${country} video`);
          continue;
        }
      }

      try {
        let contextPrompt = transcript
          ? `You narrate for Mr. WorldWideWebster. Video from ${country}. Content: "${transcript.substring(0, 300)}". Write ONE sentence (8-15 words) introducing it. Return ONLY the sentence.`
          : `Write ONE sentence (8-15 words) introducing a video from ${country}. Return ONLY the sentence.`;
        try {
          const vPromise = this.ai.chat(contextPrompt, { useCheapModel: true, temperature: 0.7 });
          const tPromise = new Promise((_, reject) => setTimeout(() => reject(new Error('VO timed out')), 8000));
          voiceoverText = await Promise.race([vPromise, tPromise]);
          if (!voiceoverText || voiceoverText.length < 5) voiceoverText = `Check out this clip from ${country}`;
          voiceoverText = voiceoverText.replace(/["']/g, '').trim().substring(0, 120);

          if (this._hasProfanity(voiceoverText)) {
            this.logger.warn(`AI generated profanity in voiceover, using safe fallback`);
            voiceoverText = `Check out this clip from ${country}`;
          }
        } catch { voiceoverText = `Check out this clip from ${country}`; }

        const vDir = path.join(config.paths.assets, 'voiceovers');
        if (!fs.existsSync(vDir)) fs.mkdirSync(vDir, { recursive: true });
        const safeText = voiceoverText.replace(/"/g, '\\"');
        const vPath = path.join(vDir, `vo_${Date.now()}_${i}.mp3`);
        try {
          execSync(`edge-tts --voice "en-US-JennyNeural" --text "${safeText}" --write-media "${vPath}" 2>/dev/null`, { timeout: 30000 });
          if (fs.existsSync(vPath) && fs.statSync(vPath).size > 1000) voiceoverPath = vPath;
        } catch {}
      } catch {}

      let startTime = 5;
      try {
        const info = execSync(`ffprobe -i "${v.path}" -show_entries stream=start_time -of csv=p=0 2>/dev/null | head -1`, { timeout: 5000, encoding: 'utf8' }).trim();
        if (info && parseFloat(info) > 0 && parseFloat(info) < 30) startTime = parseFloat(info);
      } catch {}

      const outputPath = path.join(dir, `short_${Date.now()}.mp4`);
      try {
        const result = await createShort(v.path, { startTime, duration: 30, query, countryText: country, voiceoverPath, explainerText: voiceoverText, outputPath });
        if (result) shorts.push({ path: result, query, country, hasVoiceover: !!voiceoverPath, voiceoverText, transcript, sourceUrl: v.sourceUrl, originalTitle });
      } catch (e) { this.logger.warn(`Create short failed: ${e.message}`); }
    }

    this.logger.success(`Created ${shorts.length} Shorts (${shorts.filter(s => s.hasVoiceover).length} with voiceover)`);

    // Step 6: Upload + Boost
    for (const s of shorts) {
      try {
        const query = s.query || '';
        const country = s.country;
        const originalTitle = s.originalTitle || `${country} Clip`;

        // Try AI for title/description, fallback to original title on failure
        let title = '';
        let description = '';
        try {
          const titlePrompt = s.transcript
            ? `You write for Mr. WorldWideWebster. Generate YouTube Shorts title+description.\nCountry: ${country}\nContent: "${s.transcript.substring(0, 300)}"\nTitle: catchy, max 70 chars. Description: 3-4 sentences. Return JSON: {"title":"...","description":"..."}`
            : `You write for Mr. WorldWideWebster. Generate YouTube Shorts title+description.\nCountry: ${country}\nTitle: catchy, max 70 chars. Description: 2-3 sentences. Return JSON.`;
          const td = await this.ai.chatJSON(titlePrompt, `Title for ${country}`, { useCheapModel: true, temperature: 0.7 });
          title = (td.title || originalTitle).substring(0, 100);
          description = td.description || `Amazing content from ${country}. Follow Mr. WorldWideWebster for more!`;
        } catch {
          // LLM FAILED - use original title
          this.logger.warn(`LLM failed for title/description, using original title: "${originalTitle.substring(0, 60)}"`);
          title = originalTitle.substring(0, 100);
          description = `Amazing content from ${country}. Follow Mr. WorldWideWebster for more!`;
        }

        // Final profanity check on title/description before upload
        if (this._hasProfanity(title) || this._hasProfanity(description)) {
          this.logger.warn(`⛔ Profanity in title/description for ${country}, using safe fallback`);
          title = `${country} Clip #shorts`;
          description = `Amazing content from ${country}. Follow Mr. WorldWideWebster for more!`;
        }

        const uploadResult = await this._uploadToYouTube({ videoPath: s.path, title, description, tags: ['mr worldwidewebster', 'shorts', country.toLowerCase()] });
        if (uploadResult) {
          uploaded.push({ title, url: uploadResult.url, country });
          await this._boostVideo(uploadResult.url);
        } else {
          errors.push(`Upload failed: ${title}`);
        }
      } catch (e) {
        this.logger.error(`Upload step error: ${e.message}`);
        errors.push(`Upload error: ${e.message}`);
      }
    }

    // Step 7: Boost old videos (1 week+)
    this.logger.info('Step 7: Boosting older videos...');
    await this._boostOldVideos();

    // Save memory
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
    this.logger.info(`URLs: ${allUrls.length} | Downloaded: ${downloaded.length} | Shorts: ${shorts.length} | Uploaded: ${uploaded.length}`);
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
