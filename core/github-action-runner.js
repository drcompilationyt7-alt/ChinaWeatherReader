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

  /**
   * Transcribe audio using local faster-whisper (FREE, no API key needed)
   * Uses python3 -c command directly (no temp scripts) for reliability
   */
  async _transcribeAudio(videoPath) {
    const audioDir = path.join(config.paths.assets, 'audio');
    if (!fs.existsSync(audioDir)) fs.mkdirSync(audioDir, { recursive: true });

    const audioPath = path.join(audioDir, `audio_${Date.now()}.mp3`);

    try {
      this.logger.info('Extracting audio for transcription...');
      execSync(
        `ffmpeg -y -i "${videoPath}" -t 60 -vn -acodec libmp3lame -ab 64k "${audioPath}" 2>/dev/null`,
        { timeout: 30000 }
      );

      if (!fs.existsSync(audioPath) || fs.statSync(audioPath).size < 1000) {
        this.logger.warn('Audio extraction produced empty file');
        return null;
      }

      this.logger.info('Transcribing with faster-whisper (free, local)...');

      // Use python3 -c directly instead of writing temp scripts
      // Audio path needs proper escaping for Python
      const pyPath = audioPath.replace(/\\/g, '\\\\');
      const pyCmd = `python3 -c "
from faster_whisper import WhisperModel
model = WhisperModel('base', device='cpu', compute_type='int8')
segments, info = model.transcribe('${pyPath}', language='en')
text = ' '.join(seg.text for seg in segments)
print(text[:1000] if text else '')
" 2>&1`;

      const output = execSync(pyCmd, { timeout: 120000, encoding: 'utf8', maxBuffer: 10*1024*1024 }).toString().trim();

      // Cleanup audio file
      try { fs.unlinkSync(audioPath); } catch {}

      if (output && !output.includes('Error') && !output.includes('Traceback')) {
        this.logger.success(`Transcription: "${output.substring(0, 100)}..."`);
        return output.trim();
      }

      this.logger.warn(`Whisper returned no text: ${output.substring(0, 100)}`);
      return null;

    } catch (error) {
      this.logger.warn(`Transcription failed: ${error.message.substring(0, 100)}`);
      try { fs.unlinkSync(audioPath); } catch {}
      // Don't fail pipeline - just skip transcription
      return null;
    }
  }

  async _generateQueries() {
    this.logger.info('Step 1: Generating queries...');
    const ch = this.memory['channel-memory'] || {};
    const used = ch.countriesUsedThisWeek || [];

    // NOTE: The finder-controller.js will add #shorts suffix automatically!
    const asianCountries = ['Japan','South Korea','China','Thailand','Vietnam','India','Indonesia'];
    const nonAsianCountries = ['Nigeria','Germany','Brazil','Mexico','UK','Egypt','Italy','Spain','France','Australia'];
    const all = [...nonAsianCountries, ...asianCountries];
    const avail = all.filter(c => !used.includes(c));
    const c1 = avail.length > 0 ? avail[Math.floor(Math.random()*avail.length)] : all[Math.floor(Math.random()*all.length)];
    const c2 = all[Math.floor(Math.random()*all.length)];
    const c3 = all[Math.floor(Math.random()*all.length)];

    const getSuffix = (country) => {
      // finder-controller's enrichQuery() adds #shorts to ALL queries
      // For Asian: "Japan douyin #shorts" after enrichQuery
      // For non-Asian: "Germany #shorts" after enrichQuery
      if (asianCountries.includes(country)) {
        return `${country} douyin`;
      }
      return `${country}`;
    };

    const fallbackQueries = [
      getSuffix(c1),
      getSuffix(c2),
      getSuffix(c3),
      `${c1} viral`,
      `${c2} trending`
    ];

    try {
      const aiPromise = this.ai.chatJSON(
        `Generate 5 YouTube search queries for SHORT/DOUYIN-STYLE MEME/STREAMER/EXPLAINER videos from ${c1}, ${c2}, ${c3}. 
For Asian countries (Japan, Korea, China, India, Thailand, Vietnam, Indonesia), use "douyin" style. For others keep simple but trending.
Return JSON array of strings like: ["Japan douyin", "Nigeria", "Brazil"]`,
        `5 queries`, { useCheapModel: true, temperature: 0.8 }
      );

      const timeoutPromise = new Promise((_, reject) =>
        setTimeout(() => reject(new Error('AI query generation timed out')), 12000)
      );

      const r = await Promise.race([aiPromise, timeoutPromise]);
      const qs = Array.isArray(r) ? r.slice(0,5) : (r.queries ? r.queries.slice(0,5) : fallbackQueries);
      this.queries = qs; this.countries = [c1, c2, c3];
      this.logger.success(`Queries: ${qs.join(' | ')}`);
      return { queries: qs, countries: [c1, c2, c3] };
    } catch {
      this.queries = fallbackQueries;
      this.countries = [c1, c2, c3];
      this.logger.success(`Fallback Queries: ${fallbackQueries.join(' | ')}`);
      return { queries: fallbackQueries, countries: [c1, c2, c3] };
    }
  }

  async runDaily() {
    this.logger.header('DAILY: 10 URLs -> AI Rank -> Download Top 3 -> Shorts -> Upload');
    const errors = [];
    const uploaded = [];

    const { queries, countries } = await this._generateQueries();

    this.logger.info('Step 2: Searching 10 URLs with metadata...');
    const allUrls = await findUrlsForQueries(queries, 10);

    if (allUrls.length === 0) {
      this.logger.error('No URLs found');
      return { uploadedVideos: [], errors: ['No URLs found'] };
    }

    allUrls.forEach((u, i) => {
      this.logger.info(`  URL ${i+1}: ${(u.title||'').substring(0, 50)} | views: ${u.view_count || '?'}${u.isShort ? ' 📱' : ''}`);
    });

    this.logger.info('Step 3: AI ranking URLs...');
    const { top3, explainer } = await rankVideos(allUrls, queries[0] || '', this.ai);

    if (top3.length === 0) {
      this.logger.warn('AI ranking failed, using first 3');
      top3.push(...allUrls.slice(0, 3));
    }

    top3.forEach((v, i) => this.logger.info(`  #${i+1}: ${(v.title||'').substring(0, 60)} | views: ${v.view_count || '?'}${v.isShort ? ' 📱' : ''}`));

    this.logger.info('Step 4: Downloading top 3 ranked videos...');
    const downloaded = await downloadVideos(top3, config.paths.clips);

    if (downloaded.length === 0) {
      this.logger.warn('No videos downloaded');
    }

    this.logger.info('Step 5: Creating Shorts with free local whisper transcription + AI voiceover...');
    const { createShort } = require('./clip-editor');
    const dir = config.paths.clips;
    const shorts = [];

    for (let i = 0; i < downloaded.length; i++) {
      const v = downloaded[i];
      const query = queries[i] || queries[0] || '';
      const country = countries[i] || countries[0] || 'Global';

      this.logger.info(`=== Processing short #${i+1}: ${country} ===`);

      let voiceoverPath = null;
      let voiceoverText = '';
      let transcript = null;

      // Step 5a: Transcribe with local free whisper
      try {
        transcript = await this._transcribeAudio(v.path);
        if (transcript) {
          this.logger.success(`Transcript: ${transcript.substring(0, 120)}`);
        }
      } catch (txError) {
        this.logger.warn(`Transcription error: ${txError.message}`);
      }

      // Step 5b: Generate voiceover from transcript + LLM
      try {
        let contextPrompt;

        if (transcript) {
          contextPrompt = `You are a narrator for "Mr. WorldWideWebster".
This is a video from ${country}. Here's what's happening (transcribed):
"${transcript}"

Write ONE short sentence (8-15 words) naturally introducing this video. Examples:
- "Watch this hilarious moment from ${country}"
- "Check out this viral clip from ${country}"
- "This ${country} short is going viral"

Match the tone of the content. Return ONLY the sentence.`;
        } else {
          contextPrompt = `Write ONE short sentence (8-15 words) introducing a video from ${country}. Return ONLY the sentence. Example: "Check out this funny clip from ${country}"`;
        }

        try {
          const voiceoverPromise = this.ai.chat(contextPrompt, { useCheapModel: true, temperature: 0.7 });
          const timeoutPromise = new Promise((_, reject) =>
            setTimeout(() => reject(new Error('Voiceover AI timed out')), 8000)
          );
          voiceoverText = await Promise.race([voiceoverPromise, timeoutPromise]);

          if (!voiceoverText || voiceoverText.length < 5) {
            voiceoverText = `Check out this clip from ${country}`;
          }
          voiceoverText = voiceoverText.replace(/["']/g, '').trim();
          if (voiceoverText.length > 120) voiceoverText = voiceoverText.substring(0, 117) + '...';
        } catch {
          voiceoverText = `Check out this clip from ${country}`;
        }

        // TTS
        const vDir = path.join(config.paths.assets, 'voiceovers');
        if (!fs.existsSync(vDir)) fs.mkdirSync(vDir, { recursive: true });

        try {
          const safeText = voiceoverText.replace(/"/g, '\\"');
          const vPath = path.join(vDir, `vo_${Date.now()}_${i}.mp3`);
          const cmd = `edge-tts --voice "en-US-JennyNeural" --text "${safeText}" --write-media "${vPath}" 2>/dev/null`;
          execSync(cmd, { timeout: 30000 });
          if (fs.existsSync(vPath) && fs.statSync(vPath).size > 1000) {
            voiceoverPath = vPath;
            this.logger.success(`Voiceover for #${i+1}: "${voiceoverText}"`);
          }
        } catch (ttsError) {
          this.logger.warn(`TTS failed for #${i+1}: ${ttsError.message}`);
        }
      } catch (aiError) {
        this.logger.warn(`AI voiceover failed for #${i+1}: ${aiError.message}`);
      }

      let startTime = 5;
      try {
        const info = execSync(
          `ffprobe -i "${v.path}" -show_entries stream=start_time -of csv=p=0 2>/dev/null | head -1`,
          { timeout: 5000, encoding: 'utf8' }
        ).trim();
        if (info && parseFloat(info) > 0 && parseFloat(info) < 30) startTime = parseFloat(info);
      } catch {}

      const outputPath = path.join(dir, `short_${Date.now()}.mp4`);
      const result = await createShort(v.path, {
        type: 'clip_with_voiceover',
        startTime,
        duration: 30,
        query,
        countryText: country,
        voiceoverPath,
        explainerText: voiceoverText,
        outputPath,
      });

      if (result) {
        shorts.push({
          path: result,
          query,
          country,
          hasVoiceover: !!voiceoverPath,
          voiceoverText,
          transcript,
          sourceUrl: v.sourceUrl
        });
      }
    }

    this.logger.success(`Created ${shorts.length} Shorts with ${shorts.filter(s => s.hasVoiceover).length} voiceovers`);

    for (const s of shorts) {
      const query = s.query || '';
      const country = s.country;
      try {
        const titlePrompt = s.transcript
          ? `You write for Mr. WorldWideWebster. Generate YouTube Shorts title+description.
Country: ${country}
Query: "${query}"
Video content: "${s.transcript.substring(0, 300)}"

Title: Catchy, max 70 chars, with ${country} flag emoji at start.
Description: 3-4 descriptive sentences about the content. Call to follow.
Return JSON: {"title":"...","description":"..."}`
          : `You write for Mr. WorldWideWebster. Generate YouTube Shorts title+description.
Country: ${country}
Query: "${query}"

Title: catchy, max 70 chars, with ${country} flag emoji. Description: 2-3 sentences.
Return JSON: {"title":"...","description":"..."}`;

        const td = await this.ai.chatJSON(
          titlePrompt,
          `Title for ${country} short`,
          { useCheapModel: true, temperature: 0.7 }
        );

        const title = (td.title || `\ud83c\udf0d ${query}`).substring(0, 100);
        const description = td.description || `\ud83c\udf0d From ${country}. Follow Mr. WorldWideWebster for more!`;

        const r = await this._uploadToYouTube({
          videoPath: s.path,
          title,
          description,
          tags: ['mr worldwidewebster', 'shorts', country.toLowerCase()].filter(Boolean),
        });
        if (r) uploaded.push({ title, url: r.url, type: 'clip_with_voiceover' });
        else errors.push(`Upload failed: ${title}`);
      } catch {}
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
    this.logger.info(`URLs: ${allUrls.length} | Downloaded: ${downloaded.length} | Shorts: ${shorts.length} (${shorts.filter(s => s.hasVoiceover).length} with voiceover) | Uploaded: ${uploaded.length}`);
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
