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
    this.logger.header('Mr. WorldWideWebster Pipeline v7 — Hermes Brain');
    this.logger.info(`OpenRouter keys: KEY=${!!process.env.OPENROUTER_API_KEY} KEY_2=${!!process.env.OPENROUTER_API_KEY_2} KEY_3=${!!process.env.OPENROUTER_API_KEY_3} KEY_4=${!!process.env.OPENROUTER_API_KEY_4}`);

    this.ai = new AIService();
    await this.ai.waitForInit();
    this._loadMemory();

    // Initialize YouTube bridge
    try {
      const { YouTubeBridge } = require('../youtube-automation/youtube-bridge');
      this.youtubeBridge = new YouTubeBridge();
      await this.youtubeBridge.initialize();
    } catch (e) { this.logger.warn(`YouTube bridge: ${e.message}`); }

    // Initialize Hermes Agent for brain/memory/decision making
    try {
      const { HermesAgent } = require('../hermes-agent/agent-core');
      this.hermes = new HermesAgent(this.ai);
      this.logger.success('Hermes Agent loaded — brain ready');
    } catch (e) {
      this.logger.warn(`Hermes Agent not available: ${e.message}`);
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
      const result = await engine.run({ url: videoUrl, views: parseInt(process.env.BOOST_MAX_VIEWS) || 75 });
      if (result.success) this.logger.success(`Boosted ${result.views} views`);
    } catch (e) { this.logger.warn(`Boost failed: ${e.message}`); }
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
    for (const v of oldVideos) { if (v.url) await this._boostVideo(v.url); }
  }

  async _sendDiscord(type, data) {
    try {
      const { DiscordBridge } = require('../discord/discord-bridge');
      const b = new DiscordBridge();
      if (type === 'daily') await b.sendDailySummary(data);
      await b.destroy();
    } catch {}
  }

  /**
   * Transcribe audio with language detection via faster-whisper.
   */
  async _transcribeAudio(videoPath) {
    const audioDir = path.join(config.paths.assets, 'audio');
    if (!fs.existsSync(audioDir)) fs.mkdirSync(audioDir, { recursive: true });
    const audioPath = path.join(audioDir, `audio_${Date.now()}.mp3`);
    try {
      this.logger.info('Extracting audio for transcription...');
      execSync(`ffmpeg -y -i "${videoPath}" -t 60 -vn -acodec libmp3lame -ab 64k "${audioPath}" 2>/dev/null`, { timeout: 30000 });
      if (!fs.existsSync(audioPath) || fs.statSync(audioPath).size < 1000) return null;

      this.logger.info('Transcribing with faster-whisper (auto language)...');
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
          this.logger.success(`Transcript (${lang}): "${text.substring(0, 80)}..."`);
          return { text, language: lang, isNonEnglish };
        } catch {
          this.logger.success(`Transcript: "${output.substring(0, 100)}..."`);
          return { text: output, language: 'en', isNonEnglish: false };
        }
      }
      return null;
    } catch (error) {
      this.logger.warn(`Transcription failed: ${error.message.substring(0, 100)}`);
      try { fs.unlinkSync(audioPath); } catch {}
      return null;
    }
  }

  /**
   * Burn English subtitles at the bottom of a shorts video using ffmpeg drawtext.
   */
  _burnSubtitles(videoPath, outputPath, subtitleText) {
    if (!subtitleText) return false;
    try {
      this.logger.info('Burning English subtitles onto video...');
      const safeText = subtitleText.replace(/'/g, "'\\\\''").replace(/[:\\]/g, '\\\\$&');
      const words = subtitleText.split(' ');
      const lines = [];
      let currentLine = '';
      for (const word of words) {
        if ((currentLine + ' ' + word).length > 35) {
          lines.push(currentLine);
          currentLine = word;
        } else {
          currentLine = currentLine ? currentLine + ' ' + word : word;
        }
      }
      if (currentLine) lines.push(currentLine);
      const displayLines = lines.slice(0, 3).join('\\\\N');

      const drawtextFilter =
        `drawtext=text='${displayLines}':` +
        `fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:` +
        `fontsize=28:fontcolor=white:` +
        `box=1:boxcolor=black@0.6:boxborderw=8:` +
        `x=(w-text_w)/2:y=h-text_h-80:` +
        `enable='between(t,0,30)'`;

      execSync(
        `ffmpeg -y -i "${videoPath}" -vf "${drawtextFilter}" -c:v libx264 -preset ultrafast -crf 23 -c:a copy "${outputPath}" 2>/dev/null`,
        { timeout: 60000, maxBuffer: 50*1024*1024 }
      );
      return fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000;
    } catch (error) {
      this.logger.warn(`Subtitle burn failed: ${error.message.substring(0, 80)}`);
      return false;
    }
  }

  /**
   * Have Hermes evaluate downloaded videos using transcript + yt-dlp metadata.
   * Returns ranked list with scores and notes.
   */
  async _hermesEvaluateVideos(downloaded, transcripts) {
    if (!this.hermes || downloaded.length === 0) {
      return downloaded.map((v, i) => ({ index: i, score: 50, notes: 'No Hermes available', path: v.path, sourceUrl: v.sourceUrl }));
    }

    try {
      this.logger.info('Hermes is analyzing transcripts + metadata to rank videos...');

      const videoData = downloaded.map((v, i) => ({
        index: i,
        title: v.title || 'unknown',
        url: v.sourceUrl || v.path,
        path: v.path,
        view_count: v.view_count || '?',
        duration: v.duration || '?',
        isShort: v.isShort || false,
        transcript: transcripts[i] || null,
      }));

      const result = await this.hermes.run(
        `EVALUATE VIDEOS for Mr. WorldWideWebster channel.

We downloaded these ${downloaded.length} videos for today's shorts. For each one, analyze the metadata and transcript and decide:
1. Is this video engaging enough for YouTube Shorts? (0-100 score)
2. Is the content understandable without watching the original (good for foreign content)?
3. Does the transcript contain anything problematic?
4. What's the best ordering (1st, 2nd, 3rd)?
5. What hook sentence would work best for this video?

Videos:
${JSON.stringify(videoData, null, 2)}

Return a JSON array: [{ "index": 0, "score": 85, "order": 1, "issues": [], "hook": "sentence", "notes": "why" }, ...]
Sort by score descending.`, 
        { maxSteps: 3, verbose: false }
      );

      // Parse the result - agent-core.js puts result in the result field
      let evaluation;
      try {
        const jsonMatch = result.result.match(/\[[\s\S]*\]/);
        if (jsonMatch) evaluation = JSON.parse(jsonMatch[0]);
      } catch {}

      if (evaluation && Array.isArray(evaluation)) {
        this.logger.success(`Hermes evaluated: ${evaluation.map(e => `#${e.index}=${e.score}pts`).join(', ')}`);
        return evaluation;
      }

      // Fallback: default ranking
      this.logger.warn('Hermes evaluation unparseable, using default order');
      return downloaded.map((v, i) => ({ 
        index: i, score: 50, order: i + 1, issues: [], hook: `Check out this clip`, notes: 'Default order'
      }));
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
    this.logger.header('DAILY: Hermes Brain → Find → Evaluate → Create → Upload + Store Memory');
    const errors = [];
    const uploaded = [];

    // Step 1-4: Queries → Search → Rank → Download
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

    // Step 5: Transcribe all downloads for Hermes evaluation
    this.logger.info('Step 5: Transcribing for Hermes evaluation...');
    const transcripts = [];
    const downloadsWithMeta = [];
    for (let i = 0; i < downloaded.length; i++) {
      const v = downloaded[i];
      try {
        const t = await this._transcribeAudio(v.path);
        transcripts.push(t);
        downloadsWithMeta.push({
          ...v,
          transcript: t?.text || null,
          language: t?.language || 'unknown',
          isNonEnglish: t?.isNonEnglish || false,
        });
      } catch {
        transcripts.push(null);
        downloadsWithMeta.push({ ...v, transcript: null, language: 'unknown', isNonEnglish: false });
      }
    }

    // Step 6: Hermes evaluates ALL downloaded videos using transcripts + metadata
    this.logger.info('Step 6: Hermes brain evaluating video quality...');
    const evaluations = await this._hermesEvaluateVideos(downloadsWithMeta, transcripts);

    // Log Hermes evaluation
    for (const ev of evaluations) {
      const v = downloadsWithMeta[ev.index];
      const country = countries[ev.index] || countries[0] || 'Global';
      this.logger.info(`  Hermes #${ev.order || '?'} (${ev.score}/100): ${country} — ${ev.notes?.substring(0, 60) || ''}`);
    }

    // Reorder based on Hermes scores (optimistic: sort by score desc)
    const sortedEvals = [...evaluations].sort((a, b) => (b.score || 0) - (a.score || 0));

    // Step 7: Create Shorts
    this.logger.info('Step 7: Creating Shorts with Hermes-recommended hooks...');
    const { createShort } = require('./clip-editor');
    const dir = config.paths.clips;
    const shorts = [];

    for (let i = 0; i < downloadsWithMeta.length; i++) {
      // Use Hermes evaluation to pick the best hook/ordering
      const ev = sortedEvals[i] || evaluations[i] || { index: i, score: 50, hook: '' };
      const v = downloadsWithMeta[ev.index];
      const query = queries[ev.index] || queries[0] || '';
      const country = countries[ev.index] || countries[0] || 'Global';
      const originalTitle = v.sourceUrl?.title || v.title || '';

      this.logger.info(`=== Short #${i+1}: ${country} (Hermes score: ${ev.score}) ===`);
      let voiceoverPath = null;
      let voiceoverText = '';
      let isNonEnglish = v.isNonEnglish;
      let englishSubtitle = null;

      // Profanity filter
      if (v.transcript) {
        const badWord = this._hasProfanity(v.transcript);
        if (badWord) {
          this.logger.warn(`⛔ PROFANITY "${badWord}" — SKIPPING ${country}`);
          errors.push(`Profanity (${badWord}) for ${country}`);
          continue;
        }

        // If non-English, translate for captions
        if (isNonEnglish) {
          this.logger.info(`Detected ${v.language} — translating to English for captions`);
          try {
            englishSubtitle = await this.ai.chat(
              `Translate the following ${v.language} text to natural English. Return ONLY the translation, no explanation.`,
              v.transcript,
              { useCheapModel: true, temperature: 0.3 }
            );
            if (englishSubtitle?.length > 3) {
              englishSubtitle = englishSubtitle.replace(/["']/g, '').trim().substring(0, 200);
            } else { englishSubtitle = null; }
          } catch { englishSubtitle = null; }
        }
      }

      // Use Hermes-recommended hook, or generate one
      try {
        voiceoverText = ev.hook || '';
        if (!voiceoverText || voiceoverText.length < 5) {
          const contextPrompt = v.transcript
            ? `You narrate for Mr. WorldWideWebster. Video from ${country}. Content: "${v.transcript.substring(0, 300)}". Write ONE sentence (8-15 words) introducing it. Return ONLY the sentence.`
            : `Write ONE sentence (8-15 words) introducing a video from ${country}. Return ONLY the sentence.`;
          try {
            const vPromise = this.ai.chat(contextPrompt, { useCheapModel: true, temperature: 0.7 });
            const tPromise = new Promise((_, reject) => setTimeout(() => reject(new Error('VO timed out')), 8000));
            voiceoverText = await Promise.race([vPromise, tPromise]);
            if (!voiceoverText?.length > 5) voiceoverText = `Check out this clip from ${country}`;
            voiceoverText = voiceoverText.replace(/["']/g, '').trim().substring(0, 120);
            if (this._hasProfanity(voiceoverText)) voiceoverText = `Check out this clip from ${country}`;
          } catch { voiceoverText = `Check out this clip from ${country}`; }
        }

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
        if (result) {
          let finalPath = result;
          if (isNonEnglish && englishSubtitle) {
            const subbedPath = result.replace('.mp4', '_captioned.mp4');
            const burned = this._burnSubtitles(result, subbedPath, englishSubtitle);
            if (burned) {
              try { fs.unlinkSync(result); } catch {}
              finalPath = subbedPath;
            }
          }
          shorts.push({ path: finalPath, query, country, hasVoiceover: !!voiceoverPath, voiceoverText, transcript: v.transcript, sourceUrl: v.sourceUrl, originalTitle, hermesScore: ev.score });
        }
      } catch (e) { this.logger.warn(`Create short failed: ${e.message}`); }
    }

    this.logger.success(`Created ${shorts.length} Shorts (Hermes sorted)`);

    // Step 8: Upload + Boost
    for (const s of shorts) {
      try {
        const country = s.country;
        const originalTitle = s.originalTitle || `${country} Clip`;
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
          this.logger.warn(`LLM failed for title, using original: "${originalTitle.substring(0, 60)}"`);
          title = originalTitle.substring(0, 100);
          description = `Amazing content from ${country}. Follow Mr. WorldWideWebster for more!`;
        }

        if (this._hasProfanity(title) || this._hasProfanity(description)) {
          title = `${country} Clip #shorts`;
          description = `Amazing content from ${country}. Follow Mr. WorldWideWebster for more!`;
        }

        const uploadResult = await this._uploadToYouTube({ videoPath: s.path, title, description, tags: ['mr worldwidewebster', 'shorts', country.toLowerCase()] });
        if (uploadResult) {
          uploaded.push({ title, url: uploadResult.url, country, hermesScore: s.hermesScore });
          await this._boostVideo(uploadResult.url);
        } else {
          errors.push(`Upload failed: ${title}`);
        }
      } catch (e) {
        this.logger.error(`Upload error: ${e.message}`);
        errors.push(`Upload error: ${e.message}`);
      }
    }

    // Step 9: Boost old videos
    this.logger.info('Step 9: Boosting older videos...');
    await this._boostOldVideos();

    // Step 10: Hermes stores learnings
    if (this.hermes && uploaded.length > 0) {
      try {
        this.logger.info('Hermes saving daily learnings to memory...');
        await this.hermes.run(
          `STORE MEMORY for Mr. WorldWideWebster.

Today we uploaded ${uploaded.length} shorts:
${JSON.stringify(uploaded.map(u => ({ title: u.title, country: u.country, url: u.url, hermesScore: u.hermesScore })), null, 2)}

Countries used: ${JSON.stringify(countries)}

Tasks:
1. Save this as a skill called "daily_shorts_${new Date().toISOString().split('T')[0]}"
2. Update memory files with what we learned today
3. Write a brief note about what worked and what didn't
4. Suggest which countries/topics to try next time

Use the write_file tool to update memory/channel-memory.json with an updated hermesNotes array.`,
          { maxSteps: 5, verbose: false }
        );
        this.logger.success('Hermes memory stored');
      } catch (e) {
        this.logger.warn(`Hermes memory save: ${e.message}`);
      }
    }

    // Save basic memory
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

  /**
   * Midnight investigation: Hermes reviews performance, improves strategy.
   */
  async runNightly() {
    this.logger.header('🌙 NIGHTLY: Hermes Self-Improvement Investigation');

    if (!this.hermes) {
      this.logger.error('Hermes Agent required for nightly mode');
      return;
    }

    const history = this.memory['content-history'] || { videos: [] };
    const channelMem = this.memory['channel-memory'] || {};

    this.logger.info(`Total videos posted: ${channelMem.totalVideosPosted || 0}`);
    this.logger.info(`Countries used this week: ${(channelMem.countriesUsedThisWeek || []).join(', ')}`);

    // Hermes runs autonomous investigation
    const result = await this.hermes.run(
      `NIGHTLY INVESTIGATION for Mr. WorldWideWebster.

Current state:
- Total videos: ${channelMem.totalVideosPosted || 0}
- Countries used this week: ${(channelMem.countriesUsedThisWeek || []).join(', ')}
- Hermes notes from previous runs: ${JSON.stringify(channelMem.hermesNotes || [])}

INVESTIGATION TASKS:
1. Read memory/channel-memory.json and memory/content-history.json to understand our channel
2. Browse the web to find trending content formats and countries that are performing well
3. Analyze what types of content (douyin, meme, streamer, explainer) work best for short-form
4. Generate 10 NEW search queries for tomorrow's daily run — recommend new countries
5. Create/update a reusable skill called "content_strategy" with our learnings
6. Write an updated channel-memory.json with hermesNotes containing analysis and recommendations
7. Suggest any code improvements that would make tomorrow's run better

Focus on: identifying untapped countries, fresh content formats, and avoiding content that didn't work.`,
      { maxSteps: 12, verbose: true }
    );

    this.logger.success(`Nightly investigation complete: ${result.stepsCount} steps`);
    this.logger.info(`Hermes result: ${result.result?.substring(0, 300)}`);

    // Send Discord summary
    await this._sendDiscord('daily', {
      videos: [],
      investigation: result.result?.substring(0, 1000),
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

    if (mode === 'daily') {
      await this.runDaily();
    } else if (mode === 'nightly' || mode === 'review') {
      await this.runNightly();
    } else {
      console.log(`Unknown mode: ${mode}. Use --mode daily or --mode nightly`);
      process.exit(1);
    }

    this.logger.success('Done');
  }
}

process.on('uncaughtException', e => console.error(`${e.message}`));
process.on('unhandledRejection', r => console.error(`${r?.message||r}`));
new GitHubActionsRunner().run().catch(e => { console.error(`Fatal: ${e.message}`); process.exit(1); });
