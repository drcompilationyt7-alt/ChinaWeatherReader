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
    this.hermes = null;
    this.memory = {};
    this.memoryPath = path.join(__dirname, '..', 'memory');
    this.hermesMemoryPath = path.join(__dirname, '..', 'hermes-memory');
    this.youtubeBridge = null;
    this.queries = [];
    this.countries = [];

    this.bannedWords = [
      'fuck', 'shit', 'bitch', 'asshole', 'motherfucker', 'dick', 'cunt',
      'pussy', 'bastard', 'whore', 'slut', 'damn', 'cock', 'nigger', 'nigga',
      'faggot', 'retard', 'chink', 'spic', 'kike', 'gook', 'raghead',
      'cracker', 'tranny', 'dyke', 'twat'
    ];
  }

  _hasProfanity(text) {
    if (!text) return null;
    const lower = text.toLowerCase();
    for (const word of this.bannedWords) {
      const regex = new RegExp(`\\b${word}\\b`, 'i');
      if (regex.test(lower)) return word;
    }
    const leetPatterns = [
      /\bf[u4]ck\b/i, /\bf[u4]cking\b/i, /\bsh[i1!]t\b/i,
      /\bb[i1!]tch\b/i, /\bb[a4]st[a4]rd\b/i, /\bwh[o0]re\b/i,
      /\bn[i1!]gg[a4e3]\b/i, /\bc[u4]nt\b/i
    ];
    for (const pattern of leetPatterns) {
      const m = lower.match(pattern);
      if (m) return m[0];
    }
    return null;
  }

  async initialize() {
    this.logger.header('Mr. WorldWideWebster Pipeline v7 — Hermes CLI Brain');
    this.logger.info(`OpenRouter keys: KEY=${!!process.env.OPENROUTER_API_KEY} KEY_2=${!!process.env.OPENROUTER_API_KEY_2} KEY_3=${!!process.env.OPENROUTER_API_KEY_3} KEY_4=${!!process.env.OPENROUTER_API_KEY_4}`);

    this.ai = new AIService();
    await this.ai.waitForInit();
    this._loadMemory();

    try {
      const { YouTubeBridge } = require('../youtube-automation/youtube-bridge');
      this.youtubeBridge = new YouTubeBridge();
      await this.youtubeBridge.initialize();
    } catch (e) { this.logger.warn(`YouTube bridge: ${e.message}`); }

    // Initialize Hermes CLI wrapper (uses local Ollama model + native browser)
    try {
      const { HermesCLIWrapper } = require('../hermes-agent/hermes-cli-wrapper');
      this.hermes = new HermesCLIWrapper();
      if (this.hermes.isAvailable()) {
        this.logger.success(`Hermes CLI ready — local model, provider: ${this.hermes.browserProvider}`);
      } else {
        this.logger.warn('Hermes CLI not available — evaluation will use basic AI');
      }
    } catch (e) {
      this.logger.warn(`Hermes CLI wrapper: ${e.message}`);
    }

    this.logger.success('Initialized');
  }

  _loadMemory() {
    if (!fs.existsSync(this.memoryPath)) fs.mkdirSync(this.memoryPath, { recursive: true });
    const defs = {
      'channel-memory.json': { channelName: 'Mr. WorldWideWebster', totalVideosPosted: 0, countriesUsedThisWeek: [], usedTopics: [], hermesNotes: [] },
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
      return null;
    }
  }

  async _boostVideo(videoUrl) {
    if (!videoUrl) return;
    try {
      this.logger.info(`Boosting: ${videoUrl}`);
      const { BoostEngine } = require('../boost/boost-engine');
      const result = await new BoostEngine().run({ url: videoUrl, views: parseInt(process.env.BOOST_MAX_VIEWS) || 75 });
      if (result.success) this.logger.success(`Boosted ${result.views} views`);
    } catch (e) { this.logger.warn(`Boost failed: ${e.message}`); }
  }

  async _boostOldVideos() {
    const history = this.memory['content-history'];
    if (!history?.videos) return;
    const oneWeekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
    for (const v of history.videos.filter(v => (v.type === 'shorts' || v.type === 'explainer') && new Date(v.uploadedAt || v.createdAt || 0).getTime() < oneWeekAgo)) {
      if (v.url) await this._boostVideo(v.url);
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
      execSync(`ffmpeg -y -i "${videoPath}" -t 60 -vn -acodec libmp3lame -ab 64k "${audioPath}" 2>/dev/null`, { timeout: 30000 });
      if (!fs.existsSync(audioPath) || fs.statSync(audioPath).size < 1000) return null;

      const pyPath = audioPath.replace(/\\/g, '\\\\');
      const pyCmd = `python3 -c "
from faster_whisper import WhisperModel
import json
model = WhisperModel('base', device='cpu', compute_type='int8')
segments, info = model.transcribe('${pyPath}')
text = ' '.join(seg.text for seg in segments)
result = {'text': text[:1000], 'language': info.language, 'probability': info.language_probability}
print(json.dumps(result))
" 2>&1`;
      const output = execSync(pyCmd, { timeout: 120000, encoding: 'utf8', maxBuffer: 10*1024*1024 }).toString().trim();
      try { fs.unlinkSync(audioPath); } catch {}
      if (output && !output.includes('Error') && !output.includes('Traceback')) {
        try {
          const parsed = JSON.parse(output);
          const text = parsed.text || '';
          const lang = parsed.language || 'en';
          const isNonEnglish = lang !== 'en' && lang !== 'english';
          return { text, language: lang, isNonEnglish };
        } catch { return { text: output, language: 'en', isNonEnglish: false }; }
      }
      return null;
    } catch (error) {
      this.logger.warn(`Transcription failed: ${error.message.substring(0, 100)}`);
      try { fs.unlinkSync(audioPath); } catch {}
      return null;
    }
  }

  _burnSubtitles(videoPath, outputPath, subtitleText) {
    if (!subtitleText) return false;
    try {
      const safeText = subtitleText.replace(/'/g, "'\\\\''").replace(/[:\\]/g, '\\\\$&');
      const lines = [];
      let currentLine = '';
      for (const word of subtitleText.split(' ')) {
        if ((currentLine + ' ' + word).length > 35) { lines.push(currentLine); currentLine = word; }
        else { currentLine = currentLine ? currentLine + ' ' + word : word; }
      }
      if (currentLine) lines.push(currentLine);
      const displayLines = lines.slice(0, 3).join('\\\\N');

      execSync(
        `ffmpeg -y -i "${videoPath}" -vf "drawtext=text='${displayLines}':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=8:x=(w-text_w)/2:y=h-text_h-80:enable='between(t,0,30)'" -c:v libx264 -preset ultrafast -crf 23 -c:a copy "${outputPath}" 2>/dev/null`,
        { timeout: 60000, maxBuffer: 50*1024*1024 }
      );
      return fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000;
    } catch { return false; }
  }

  /**
   * Hermes CLI evaluates videos using local Ollama model.
   * Returns ranked list with scores and notes.
   */
  async _hermesEvaluateVideos(downloaded, transcripts) {
    if (!this.hermes || !this.hermes.isAvailable() || downloaded.length === 0) {
      return downloaded.map((v, i) => ({ index: i, score: 50, order: i + 1, issues: [], hook: `Check out this clip`, notes: 'No Hermes' }));
    }

    try {
      this.logger.info('Hermes CLI evaluating videos (local Ollama model)...');

      const videoData = downloaded.map((v, i) => ({
        index: i,
        title: v.title || 'unknown',
        url: v.sourceUrl || v.path,
        view_count: v.view_count || '?',
        transcript: transcripts[i]?.text?.substring(0, 200) || null,
        language: transcripts[i]?.language || 'unknown',
      }));

      // Use Hermes CLI chat to evaluate — it runs on local Ollama
      const result = await this.hermes.chat(
        `You are evaluating YouTube videos for Mr. WorldWideWebster channel. ` +
        `For each video, rate engagement 0-100 and suggest a hook sentence. ` +
        `Videos:\n${JSON.stringify(videoData, null, 2)}\n\n` +
        `Return JSON array: [{ "index": 0, "score": 85, "hook": "hook sentence", "notes": "why" }]`,
        { timeout: 120000 }
      );

      if (result.success && result.output) {
        const jsonMatch = result.output.match(/\[[\s\S]*\]/);
        if (jsonMatch) {
          const evaluation = JSON.parse(jsonMatch[0]);
          if (Array.isArray(evaluation)) {
            this.logger.success(`Hermes evaluates: ${evaluation.map(e => `#${e.index}=${e.score}`).join(', ')}`);
            return evaluation;
          }
        }
      }

      this.logger.warn('Hermes evaluation unparseable, default order');
      return downloaded.map((v, i) => ({ index: i, score: 50, order: i + 1, issues: [], hook: `Check out this clip`, notes: 'Fallback' }));
    } catch (error) {
      this.logger.warn(`Hermes evaluation failed: ${error.message}`);
      return downloaded.map((v, i) => ({ index: i, score: 50, order: i + 1, issues: [], hook: `Check out this clip`, notes: 'Fallback' }));
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
        `Generate 5 YouTube search queries for SHORT/DOUYIN-STYLE videos from ${c1}, ${c2}, ${c3}. For Asian countries use "douyin" style. Return JSON array of strings`,
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
    this.logger.header('DAILY: Hermes CLI Brain → Find → Evaluate → Create → Upload');
    const errors = [];
    const uploaded = [];

    const { queries, countries } = await this._generateQueries();
    this.logger.info('Step 2: Searching 10 URLs...');
    const allUrls = await findUrlsForQueries(queries, 10);
    if (allUrls.length === 0) return { uploadedVideos: [], errors: ['No URLs found'] };
    allUrls.forEach((u, i) => this.logger.info(`  URL ${i+1}: ${(u.title||'').substring(0,50)} | ${u.view_count || '?'} views${u.isShort ? ' 📱' : ''}`));
    this.logger.info('Step 3: AI ranking...');
    const { top3 } = await rankVideos(allUrls, queries[0] || '', this.ai);
    if (top3.length === 0) top3.push(...allUrls.slice(0, 3));
    top3.forEach((v, i) => this.logger.info(`  #${i+1}: ${(v.title||'').substring(0,60)} | ${v.view_count || '?'} views${v.isShort ? ' 📱' : ''}`));
    this.logger.info('Step 4: Downloading...');
    const downloaded = await downloadVideos(top3, config.paths.clips);
    if (downloaded.length === 0) this.logger.warn('No videos downloaded');

    // Transcribe for Hermes evaluation
    this.logger.info('Step 5: Transcribing for Hermes evaluation...');
    const transcripts = [];
    const downloadsWithMeta = [];
    for (let i = 0; i < downloaded.length; i++) {
      const v = downloaded[i];
      try {
        const t = await this._transcribeAudio(v.path);
        transcripts.push(t);
        downloadsWithMeta.push({ ...v, transcript: t?.text || null, language: t?.language || 'unknown', isNonEnglish: t?.isNonEnglish || false });
      } catch {
        transcripts.push(null);
        downloadsWithMeta.push({ ...v, transcript: null, language: 'unknown', isNonEnglish: false });
      }
    }

    // Hermes CLI evaluates
    this.logger.info('Step 6: Hermes CLI evaluating with local Ollama...');
    const evaluations = await this._hermesEvaluateVideos(downloadsWithMeta, transcripts);
    for (const ev of evaluations) {
      this.logger.info(`  Hermes #${ev.order || '?'} (${ev.score}/100): ${ev.notes?.substring(0, 60) || ''}`);
    }

    const sortedEvals = [...evaluations].sort((a, b) => (b.score || 0) - (a.score || 0));

    this.logger.info('Step 7: Creating Shorts...');
    const { createShort } = require('./clip-editor');
    const shorts = [];

    for (let i = 0; i < downloadsWithMeta.length; i++) {
      const ev = sortedEvals[i] || evaluations[i] || { index: i, score: 50, hook: '' };
      const v = downloadsWithMeta[ev.index];
      const query = queries[ev.index] || queries[0] || '';
      const country = countries[ev.index] || countries[0] || 'Global';
      const originalTitle = v.sourceUrl?.title || v.title || '';

      this.logger.info(`=== Short #${i+1}: ${country} (Hermes: ${ev.score}) ===`);
      let voiceoverPath = null;
      let voiceoverText = '';
      let englishSubtitle = null;

      if (v.transcript) {
        const badWord = this._hasProfanity(v.transcript);
        if (badWord) { this.logger.warn(`⛔ PROFANITY "${badWord}" — SKIP ${country}`); errors.push(`Profanity (${badWord})`); continue; }

        if (v.isNonEnglish) {
          try {
            englishSubtitle = await this.ai.chat(`Translate to natural English. Return ONLY translation.`, v.transcript, { useCheapModel: true, temperature: 0.3 });
            if (englishSubtitle?.length > 3) englishSubtitle = englishSubtitle.replace(/["']/g, '').trim().substring(0, 200);
            else englishSubtitle = null;
          } catch {}
        }
      }

      try {
        voiceoverText = ev.hook || '';
        if (!voiceoverText || voiceoverText.length < 5) {
          const ctx = v.transcript
            ? `You narrate for Mr. WorldWideWebster. Video from ${country}. Content: "${v.transcript.substring(0, 300)}". Write ONE sentence (8-15 words). Return ONLY the sentence.`
            : `Write ONE sentence (8-15 words) for a video from ${country}.`;
          try {
            voiceoverText = await Promise.race([this.ai.chat(ctx, { useCheapModel: true, temperature: 0.7 }), new Promise((_, r) => setTimeout(() => r(''), 8000))]);
            if (!voiceoverText?.length > 5) voiceoverText = `Check out this clip from ${country}`;
            voiceoverText = voiceoverText.replace(/["']/g, '').trim().substring(0, 120);
            if (this._hasProfanity(voiceoverText)) voiceoverText = `Check out this clip from ${country}`;
          } catch { voiceoverText = `Check out this clip from ${country}`; }
        }

        const vDir = path.join(config.paths.assets, 'voiceovers');
        if (!fs.existsSync(vDir)) fs.mkdirSync(vDir, { recursive: true });
        const vPath = path.join(vDir, `vo_${Date.now()}_${i}.mp3`);
        try {
          execSync(`edge-tts --voice "en-US-JennyNeural" --text "${voiceoverText.replace(/"/g, '\\"')}" --write-media "${vPath}" 2>/dev/null`, { timeout: 30000 });
          if (fs.existsSync(vPath) && fs.statSync(vPath).size > 1000) voiceoverPath = vPath;
        } catch {}
      } catch {}

      let startTime = 5;
      try {
        const info = execSync(`ffprobe -i "${v.path}" -show_entries stream=start_time -of csv=p=0 2>/dev/null | head -1`, { timeout: 5000, encoding: 'utf8' }).trim();
        if (info && parseFloat(info) > 0 && parseFloat(info) < 30) startTime = parseFloat(info);
      } catch {}

      const outputPath = path.join(config.paths.clips, `short_${Date.now()}.mp4`);
      try {
        const result = await createShort(v.path, { startTime, duration: 30, query, countryText: country, voiceoverPath, explainerText: voiceoverText, outputPath });
        if (result) {
          let finalPath = result;
          if (v.isNonEnglish && englishSubtitle) {
            const subbedPath = result.replace('.mp4', '_captioned.mp4');
            if (this._burnSubtitles(result, subbedPath, englishSubtitle)) {
              try { fs.unlinkSync(result); } catch {}
              finalPath = subbedPath;
            }
          }
          shorts.push({ path: finalPath, query, country, voiceoverText, transcript: v.transcript, originalTitle, hermesScore: ev.score });
        }
      } catch (e) { this.logger.warn(`Create short failed: ${e.message}`); }
    }

    this.logger.success(`Created ${shorts.length} Shorts`);

    for (const s of shorts) {
      try {
        const country = s.country;
        let title = s.originalTitle || `${country} Clip`;
        let description = `Amazing content from ${country}. Follow Mr. WorldWideWebster for more!`;
        try {
          const td = await this.ai.chatJSON(
            `Generate YouTube Shorts title+description. Country: ${country}${s.transcript ? `\nContent: "${s.transcript.substring(0, 300)}"` : ''}\nTitle: max 70 chars. Description: 3-4 sentences. Return JSON.`,
            `Title for ${country}`, { useCheapModel: true, temperature: 0.7 }
          );
          title = (td.title || s.originalTitle).substring(0, 100);
          description = td.description || description;
        } catch { this.logger.warn(`LLM failed, using original title`); }

        if (this._hasProfanity(title) || this._hasProfanity(description)) {
          title = `${country} Clip #shorts`;
          description = `Amazing content from ${country}.`;
        }

        const uploadResult = await this._uploadToYouTube({ videoPath: s.path, title, description, tags: ['mr worldwidewebster', 'shorts', country.toLowerCase()] });
        if (uploadResult) {
          uploaded.push({ title, url: uploadResult.url, country, hermesScore: s.hermesScore });
          await this._boostVideo(uploadResult.url);
        } else { errors.push(`Upload failed: ${title}`); }
      } catch (e) { this.logger.error(`Upload error: ${e.message}`); errors.push(`Upload error: ${e.message}`); }
    }

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

    // Hermes saves memory to its own system
    if (this.hermes && this.hermes.isAvailable() && uploaded.length > 0) {
      try {
        this.logger.info('Hermes CLI saving to memory system...');
        await this.hermes.chat(
          `Store in memory: Today we uploaded ${uploaded.length} shorts for Mr. WorldWideWebster. ` +
          `Countries: ${JSON.stringify(countries)}. ` +
          `Videos: ${JSON.stringify(uploaded.map(u => ({ title: u.title, url: u.url })))}. ` +
          `Suggest next countries and topics.`,
          { timeout: 60000 }
        );
        this.logger.success('Hermes memory updated');
      } catch (e) { this.logger.warn(`Hermes memory: ${e.message}`); }
    }

    await this._sendDiscord('daily', { videos: uploaded, countries: cm.countriesUsedThisWeek, totalVideos: cm.totalVideosPosted, errors });

    this.logger.header('SUMMARY');
    this.logger.info(`URLs: ${allUrls.length} | Downloads: ${downloaded.length} | Shorts: ${shorts.length} | Uploaded: ${uploaded.length}`);
    if (errors.length) errors.forEach(e => this.logger.warn(`  ${e}`));
    return { uploadedVideos: uploaded, errors };
  }

  /**
   * Nightly investigation using Hermes CLI (local Ollama model, web browsing).
   */
  async runNightly() {
    this.logger.header('🌙 NIGHTLY: Hermes CLI Investigation (local model + web browser)');

    if (!this.hermes || !this.hermes.isAvailable()) {
      this.logger.error('Hermes CLI required for nightly mode');
      return;
    }

    const channelMem = this.memory['channel-memory'] || {};
    this.logger.info(`Total videos: ${channelMem.totalVideosPosted || 0}`);
    this.logger.info(`Countries this week: ${(channelMem.countriesUsedThisWeek || []).join(', ')}`);

    // Hermes CLI runs autonomously — it uses its own local Ollama model
    // and built-in web browser (no OpenRouter needed)
    const result = await this.hermes.chat(
      `You are Hermes, the autonomous brain for Mr. WorldWideWebster YouTube channel. ` +
      `NIGHTLY INVESTIGATION:\n\n` +
      `1. BROWSE YouTube for trending shorts from NEW countries we haven't used yet\n` +
      `2. ANALYZE what formats (douyin, meme, streamer, explainer) are performing\n` +
      `3. GENERATE 10 fresh search queries for tomorrow's daily run\n` +
      `4. UPDATE memory with findings\n\n` +
      `Current state:\n` +
      `- Total videos: ${channelMem.totalVideosPosted || 0}\n` +
      `- Countries used: ${(channelMem.countriesUsedThisWeek || []).join(', ')}\n` +
      `- Focus on finding UNDISCOVERED countries and FRESH content formats.`,
      { timeout: 300000 }
    );

    this.logger.success(`Nightly investigation: ${result.success ? '✅' : '❌'}`);
    if (result.output) this.logger.info(`Hermes says: ${result.output.substring(0, 500)}`);

    await this._sendDiscord('daily', {
      videos: [],
      investigation: result.output?.substring(0, 1000),
      countries: channelMem.countriesUsedThisWeek,
      totalVideos: channelMem.totalVideosPosted,
      errors: [],
    });

    return result;
  }

  async run() {
    await this.initialize();
    const args = process.argv.slice(2);
    const mi = args.indexOf('--mode');
    const mode = mi !== -1 ? args[mi+1] : 'daily';

    if (mode === 'daily') await this.runDaily();
    else if (mode === 'nightly' || mode === 'review') await this.runNightly();
    else { console.log(`Unknown: ${mode}`); process.exit(1); }
    this.logger.success('Done');
  }
}

process.on('uncaughtException', e => console.error(`${e.message}`));
process.on('unhandledRejection', r => console.error(`${r?.message||r}`));
new GitHubActionsRunner().run().catch(e => { console.error(`Fatal: ${e.message}`); process.exit(1); });
