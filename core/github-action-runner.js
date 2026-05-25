#!/usr/bin/env node

const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const config = require('./config');
const { AIService } = require('./ai-service');
const { Logger } = require('./logger');
const { findUrlsForQueries } = require('../sourcing/finder-controller');
const { downloadVideos } = require('./downloader');
const { rankVideos } = require('./url-ranker');

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

    this.bannedWords = [
      'fuck', 'shit', 'bitch', 'asshole', 'motherfucker', 'dick', 'cunt',
      'pussy', 'bastard', 'whore', 'slut', 'damn', 'cock', 'nigger', 'nigga',
      'faggot', 'retard', 'chink', 'spic', 'kike', 'gook', 'raghead',
      'cracker', 'tranny', 'dyke', 'twat'
    ];

    // Country -> expected language code mapping
    this.countryLanguages = {
      'Japan': ['ja','jpn'], 'China': ['zh','zho','cmn','yue','wuu'],
      'South Korea': ['ko','kor'], 'Thailand': ['th','tha'],
      'Vietnam': ['vi','vie'], 'India': ['hi','hin','ta','tam','te','tel','bn','ben','mr','mar','gu','guj'],
      'Indonesia': ['id','ind','ms','msa'],
      'Brazil': ['pt','por'], 'Mexico': ['es','spa'],
      'France': ['fr','fra'], 'Germany': ['de','deu'],
      'Italy': ['it','ita'], 'Spain': ['es','spa'],
      'Egypt': ['ar','ara'], 'Nigeria': ['en','ha','hau','yo','yor'],
      'UK': ['en'], 'Australia': ['en']
    };
  }

  _hasProfanity(text) {
    if (!text) return null;
    const lower = text.toLowerCase();
    for (const word of this.bannedWords) {
      if (new RegExp(`\\b${word}\\b`, 'i').test(lower)) return word;
    }
    for (const p of [/\bf[u4]ck\b/i, /\bf[u4]cking\b/i, /\bsh[i1!]t\b/i, /\bb[i1!]tch\b/i, /\bb[a4]st[a4]rd\b/i, /\bwh[o0]re\b/i, /\bn[i1!]gg[a4e3]\b/i, /\bc[u4]nt\b/i]) {
      const m = lower.match(p);
      if (m) return m[0];
    }
    return null;
  }

  /**
   * Hermes quality check — NO JSON. Uses plain text parsing.
   * Prompt asks for format: "PASS 85 Great hook" or "FAIL 30 Too boring"
   */
  async _hermesCheck(contentType, content, context) {
    if (!this.hermes || !this.hermes.isAvailable()) {
      return { passed: true, score: 70, feedback: 'Hermes unavailable' };
    }
    try {
      const prompt = `You are a quality checker for Mr. WorldWideWebster YouTube channel.
Check this ${contentType}.

CONTEXT: ${context || ''}

CONTENT:
${(content || '').substring(0, 600)}

Respond with format:
PASS or FAIL
Score 0-100
Brief reason

Example:
PASS
85
Good hook, matches the video

Example:
FAIL
30
Too generic, doesn't grab attention`;

      const result = await this.hermes.chat(prompt, { timeout: 30000 });
      if (result.success && result.output) {
        const out = result.output.trim();
        const passed = out.toUpperCase().startsWith('PASS');
        const scoreMatch = out.match(/\b(\d{1,3})\b/);
        const score = scoreMatch ? parseInt(scoreMatch[1]) : 50;
        // Feedback is everything after the score line
        const lines = out.split('\n').filter(l => l.trim());
        const feedback = lines.length > 2 ? lines.slice(2).join(' ').substring(0, 200) : 'No details';
        return { passed, score: Math.min(100, Math.max(0, score)), feedback };
      }
      return { passed: true, score: 70, feedback: 'No output from Hermes' };
    } catch (e) {
      this.logger.warn(`Hermes check failed: ${e.message}`);
      return { passed: true, score: 70, feedback: 'Check error' };
    }
  }

  /**
   * Verify a video actually matches the expected country.
   * Uses: transcript language, yt-dlp title analysis, Hermes cross-check.
   */
  async _verifyCountryMatch(videoUrl, expectedCountry, transcript, title) {
    let confidence = 50;
    let reasons = [];

    // 1. Transcript language check
    if (transcript?.language) {
      const expectedLangs = this.countryLanguages[expectedCountry] || [];
      if (expectedLangs.length > 0) {
        if (expectedLangs.includes(transcript.language)) {
          confidence += 25;
          reasons.push(`Audio language ${transcript.language} matches ${expectedCountry}`);
        } else if (transcript.language === 'en' && !expectedLangs.includes('en')) {
          confidence -= 15;
          reasons.push(`Audio is English but ${expectedCountry} expected non-English`);
        }
      }
    }

    // 2. Try yt-dlp for declared language + channel metadata
    try {
      const meta = execSync(`yt-dlp --dump-json --no-download "${videoUrl}" 2>/dev/null`, { timeout: 15000, encoding: 'utf8', maxBuffer: 2*1024*1024 }).trim();
      if (meta) {
        const p = JSON.parse(meta.split('\n')[0]);
        if (p.language) {
          const expectedLangs = this.countryLanguages[expectedCountry] || [];
          if (expectedLangs.includes(p.language)) confidence += 15;
          else confidence -= 10;
          reasons.push(`YouTube declared language: ${p.language}`);
        }
        if (p.channel) {
          // Channel name often hints at origin
          reasons.push(`Channel: ${p.channel.substring(0, 50)}`);
        }
      }
    } catch {}

    // 3. Hermes final cross-check (local model, free)
    if (this.hermes && this.hermes.isAvailable()) {
      try {
        const check = await this.hermes.chat(
          `Does this video match ${expectedCountry}? Answer only YES or NO.
Title: "${(title || '').substring(0, 200)}"
Audio language: ${transcript?.language || 'unknown'}
Transcript start: "${(transcript?.text || '').substring(0, 200)}"`,
          { timeout: 20000 }
        );
        if (check.success && check.output) {
          if (check.output.toUpperCase().includes('YES')) confidence += 20;
          else if (check.output.toUpperCase().includes('NO')) confidence -= 25;
        }
      } catch {}
    }

    const match = confidence >= 50;
    this.logger.info(`Country match for ${expectedCountry}: ${match ? '✅' : '⛔'} (${confidence}%) — ${reasons.join('; ') || 'No data'}`);
    return { match, confidence, reasons };
  }

  async initialize() {
    this.logger.header('Mr. WorldWideWebster Pipeline — OpenRouter Works, Hermes Checks');
    this.logger.info(`OpenRouter keys: ${['', '_2', '_3', '_4'].map(s => process.env['OPENROUTER_API_KEY' + s] ? '✅' : '❌').join(' ')}`);

    this.ai = new AIService();
    await this.ai.waitForInit();
    this._loadMemory();

    try {
      const { YouTubeBridge } = require('../youtube-automation/youtube-bridge');
      this.youtubeBridge = new YouTubeBridge();
      await this.youtubeBridge.initialize();
    } catch (e) { this.logger.warn(`YouTube bridge: ${e.message}`); }

    try {
      const { HermesCLIWrapper } = require('../hermes-agent/hermes-cli-wrapper');
      this.hermes = new HermesCLIWrapper();
      if (this.hermes.isAvailable()) {
        this.logger.success('Hermes CLI quality checker ready');
      } else {
        this.logger.info('Hermes CLI not available');
      }
    } catch (e) {
      this.logger.info('Hermes CLI not installed');
    }
    this.logger.success('Initialized');
  }

  _loadMemory() {
    if (!fs.existsSync(this.memoryPath)) fs.mkdirSync(this.memoryPath, { recursive: true });
    for (const [f, d] of Object.entries({
      'channel-memory.json': { channelName: 'Mr. WorldWideWebster', totalVideosPosted: 0, countriesUsedThisWeek: [], usedTopics: [] },
      'content-history.json': { videos: [] },
    })) {
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
    } catch (e) { this.logger.error(`Upload FAILED: ${e.message}`); return null; }
  }

  async _boostVideo(url) {
    if (!url) return;
    try {
      this.logger.info(`Boosting: ${url}`);
      const r = await new (require('../boost/boost-engine').BoostEngine)().run({ url, views: parseInt(process.env.BOOST_MAX_VIEWS) || 75 });
      if (r.success) this.logger.success(`Boosted ${r.views} views`);
    } catch (e) { this.logger.warn(`Boost: ${e.message}`); }
  }

  async _sendDiscord(type, data) {
    try { const b = new (require('../discord/discord-bridge').DiscordBridge)(); if (type === 'daily') await b.sendDailySummary(data); await b.destroy(); } catch {}
  }

  async _transcribeAudio(videoPath) {
    const dir = path.join(config.paths.assets, 'audio');
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    const audioPath = path.join(dir, `audio_${Date.now()}.mp3`);
    try {
      execSync(`ffmpeg -y -i "${videoPath}" -t 60 -vn -acodec libmp3lame -ab 64k "${audioPath}" 2>/dev/null`, { timeout: 30000 });
      if (!fs.existsSync(audioPath) || fs.statSync(audioPath).size < 1000) return null;
      const pyPath = audioPath.replace(/\\/g, '\\\\');
      const output = execSync(`python3 -c "
from faster_whisper import WhisperModel
import json
model = WhisperModel('base', device='cpu', compute_type='int8')
segments, info = model.transcribe('${pyPath}')
text = ' '.join(seg.text for seg in segments)
print(json.dumps({'text': text[:1000], 'language': info.language}))
" 2>&1`, { timeout: 120000, encoding: 'utf8', maxBuffer: 10*1024*1024 }).toString().trim();
      try { fs.unlinkSync(audioPath); } catch {}
      if (output && !output.includes('Error') && !output.includes('Traceback')) {
        try {
          const p = JSON.parse(output);
          return { text: p.text || '', language: p.language || 'en', isNonEnglish: p.language !== 'en' && p.language !== 'english' };
        } catch { return { text: output, language: 'en', isNonEnglish: false }; }
      }
      return null;
    } catch { try { fs.unlinkSync(audioPath); } catch {} return null; }
  }

  _burnSubtitles(videoPath, outputPath, text) {
    if (!text) return false;
    try {
      const lines = [];
      let cur = '';
      for (const w of text.split(' ')) {
        if ((cur + ' ' + w).length > 35) { lines.push(cur); cur = w; } else { cur = cur ? cur + ' ' + w : w; }
      }
      if (cur) lines.push(cur);
      execSync(`ffmpeg -y -i "${videoPath}" -vf "drawtext=text='${lines.slice(0, 3).join('\\\\N')}':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=8:x=(w-text_w)/2:y=h-text_h-80" -c:v libx264 -preset ultrafast -crf 23 -c:a copy "${outputPath}" 2>/dev/null`, { timeout: 60000 });
      return fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000;
    } catch { return false; }
  }

  async runDaily() {
    this.logger.header('DAILY: OpenRouter works, Hermes quality-checks');
    const errors = [];
    const uploaded = [];

    // Step 1: OpenRouter generates queries
    this.logger.info('Step 1: OpenRouter generating queries...');
    const ch = this.memory['channel-memory'] || {};
    const used = ch.countriesUsedThisWeek || [];
    const allC = ['Nigeria','Germany','Brazil','Mexico','UK','Egypt','Italy','Spain','France','Australia','Japan','South Korea','China','Thailand','Vietnam','India','Indonesia'];
    const avail = allC.filter(c => !used.includes(c));
    const countries = [avail.length ? avail[Math.floor(Math.random()*avail.length)] : allC[Math.floor(Math.random()*allC.length)], allC[Math.floor(Math.random()*allC.length)], allC[Math.floor(Math.random()*allC.length)]];
    const fallbackQ = countries.map(c => ['Japan','South Korea','China','Thailand','Vietnam','India','Indonesia'].includes(c) ? `${c} douyin` : c);
    let queries = fallbackQ;
    try {
      const r = await Promise.race([this.ai.chatJSON(`Generate 5 YouTube search queries for SHORT videos from ${countries[0]}, ${countries[1]}, ${countries[2]}. Asian countries: use "douyin". Return JSON array.`, 'queries', { useCheapModel: true, temperature: 0.8 }), new Promise((_, rj) => setTimeout(() => rj(new Error('timeout')), 12000))]);
      queries = Array.isArray(r) ? r.slice(0, 5) : fallbackQ;
    } catch {}
    this.logger.success(`Queries: ${queries.join(' | ')}`);

    // Step 2: Search
    this.logger.info('Step 2: Searching...');
    const allUrls = await findUrlsForQueries(queries, 10);
    if (!allUrls.length) return { uploadedVideos: [], errors: ['No URLs'] };

    // Step 3: OpenRouter ranks
    this.logger.info('Step 3: OpenRouter ranking...');
    const { top3 } = await rankVideos(allUrls, queries[0] || '', this.ai);
    if (!top3.length) top3.push(...allUrls.slice(0, 3));

    // Step 4: Download
    this.logger.info('Step 4: Downloading...');
    const downloaded = await downloadVideos(top3, config.paths.clips);

    // Step 5: Process each video
    this.logger.info('Step 5: Transcribe, country-match, translate, Hermes checks...');
    const { createShort } = require('./clip-editor');
    const shorts = [];

    for (let i = 0; i < downloaded.length; i++) {
      const v = downloaded[i];
      const country = countries[i] || countries[0] || 'Global';
      const originalTitle = v.sourceUrl?.title || v.title || '';
      const sourceUrl = v.sourceUrl || '';
      this.logger.info(`=== Video ${i+1}: ${country} ===`);

      // Transcribe
      let transcript = null;
      try { transcript = await this._transcribeAudio(v.path); } catch {}

      // Profanity check
      if (transcript) {
        const badWord = this._hasProfanity(transcript.text);
        if (badWord) { this.logger.warn(`⛛ PROFANITY "${badWord}" — skip ${country}`); errors.push(`Profanity ${country}`); continue; }
      }

      // Country matching — verify video actually matches expected country
      const countryMatch = await this._verifyCountryMatch(sourceUrl, country, transcript, originalTitle);
      if (!countryMatch.match) {
        this.logger.warn(`⛔ Country mismatch for ${country} — skipping (confidence ${countryMatch.confidence}%)`);
        errors.push(`Country mismatch ${country}: ${countryMatch.reasons.join('; ')}`);
        continue;
      }

      // Hermes quick check
      const check = await this._hermesCheck('video', originalTitle, `Country: ${country}`);
      if (!check.passed) {
        this.logger.warn(`Hermes rejected: ${check.feedback} — skip`);
        errors.push(`Hermes rejected ${country}: ${check.feedback}`);
        continue;
      }

      // OpenRouter: generate voiceover
      let voiceoverText = '';
      try {
        const ctx = transcript?.text
          ? `You narrate for Mr. WorldWideWebster. Video from ${country}. Content: "${transcript.text.substring(0, 300)}". Write ONE sentence (8-15 words). Return ONLY the sentence.`
          : `Write ONE sentence for a video from ${country}.`;
        voiceoverText = await Promise.race([this.ai.chat(ctx, { useCheapModel: true, temperature: 0.7 }), new Promise(r => setTimeout(() => r(''), 8000))]);
        if (!voiceoverText?.length > 5) voiceoverText = `Check out this clip from ${country}`;
        voiceoverText = voiceoverText.replace(/["']/g, '').trim().substring(0, 120);
        if (this._hasProfanity(voiceoverText)) voiceoverText = `Check out this clip from ${country}`;
      } catch { voiceoverText = `Check out this clip from ${country}`; }

      // Hermes: check voiceover quality
      const voCheck = await this._hermesCheck('voiceover text', voiceoverText, `Country: ${country}, transcript: ${(transcript?.text || '').substring(0, 200)}`);
      if (!voCheck.passed) {
        this.logger.warn(`Hermes rejected voiceover: ${voCheck.feedback} — regenerating`);
        try {
          voiceoverText = await Promise.race([this.ai.chat(`You narrate for Mr. WorldWideWebster. Video from ${country}. Write ONE exciting sentence (8-15 words).`, { useCheapModel: true, temperature: 0.8 }), new Promise(r => setTimeout(() => r(''), 8000))]);
          voiceoverText = (voiceoverText || `Check out this clip from ${country}`).replace(/["']/g, '').trim().substring(0, 120);
        } catch { voiceoverText = `Check out this clip from ${country}`; }
      }

      // TTS
      let voiceoverPath = null;
      try {
        const vDir = path.join(config.paths.assets, 'voiceovers');
        if (!fs.existsSync(vDir)) fs.mkdirSync(vDir, { recursive: true });
        const vPath = path.join(vDir, `vo_${Date.now()}_${i}.mp3`);
        execSync(`edge-tts --voice "en-US-JennyNeural" --text "${voiceoverText.replace(/"/g, '\\"')}" --write-media "${vPath}" 2>/dev/null`, { timeout: 30000 });
        if (fs.existsSync(vPath) && fs.statSync(vPath).size > 1000) voiceoverPath = vPath;
      } catch {}

      // OpenRouter: translate if non-English
      let englishSubtitle = null;
      if (transcript?.isNonEnglish) {
        try {
          englishSubtitle = await this.ai.chat(`Translate to natural English. Return ONLY translation.`, transcript.text, { useCheapModel: true, temperature: 0.3 });
          if (englishSubtitle?.length > 3) englishSubtitle = englishSubtitle.replace(/["']/g, '').trim().substring(0, 200);
        } catch {}
      }

      // Trim + scale to shorts
      let startTime = 5;
      try {
        const info = execSync(`ffprobe -i "${v.path}" -show_entries stream=start_time -of csv=p=0 2>/dev/null | head -1`, { timeout: 5000, encoding: 'utf8' }).trim();
        if (info && parseFloat(info) > 0 && parseFloat(info) < 30) startTime = parseFloat(info);
      } catch {}

      const outputPath = path.join(config.paths.clips, `short_${Date.now()}.mp4`);
      try {
        const result = await createShort(v.path, { startTime, duration: 30, countryText: country, voiceoverPath, outputPath });
        if (result) {
          let finalPath = result;
          if (englishSubtitle) {
            const subbedPath = result.replace('.mp4', '_captioned.mp4');
            if (this._burnSubtitles(result, subbedPath, englishSubtitle)) {
              try { fs.unlinkSync(result); } catch {}
              finalPath = subbedPath;
            }
          }
          shorts.push({ path: finalPath, country, voiceoverText, transcript: transcript?.text, originalTitle, sourceUrl });
        }
      } catch (e) { this.logger.warn(`Create short failed: ${e.message}`); }
    }

    this.logger.success(`Created ${shorts.length} Shorts`);

    // Step 6: OpenRouter generates titles+descriptions, Hermes checks
    for (const s of shorts) {
      try {
        const country = s.country;
        const originalTitle = s.originalTitle || `${country} Clip`;
        let title = originalTitle;
        let description = '';

        // Try OpenRouter first for title + full description
        try {
          const td = await this.ai.chatJSON(
            `Generate YouTube Shorts title+description. Country: ${country}
${s.transcript ? `Transcript: "${s.transcript.substring(0, 400)}"` : ''}
Title: catchy, max 70 chars.
Description: 3-4 sentences explaining what's interesting about this video. Include hashtags.
Return JSON: {"title":"...","description":"..."}`,
            `Title for ${country}`, { useCheapModel: true, temperature: 0.7 }
          );
          title = (td.title || originalTitle).substring(0, 100);
          description = td.description || `Amazing content from ${country}. 🇨🇮 Follow for more global shorts! #shorts #${country.toLowerCase()} #worldwide`;
          this.logger.success(`OpenRouter title: "${title}"`);
        } catch {
          // OpenRouter failed — try Hermes with local model
          this.logger.warn(`OpenRouter failed for title — trying Hermes`);
          try {
            const hResult = await this.hermes.chat(
              `Generate a YouTube Shorts title (max 70 chars) for a video from ${country}. Transcript: "${(s.transcript || '').substring(0, 300)}". Return ONLY the title.`,
              { timeout: 30000 }
            );
            if (hResult.success && hResult.output) {
              title = hResult.output.trim().replace(/["']/g, '').substring(0, 100);
            }
          } catch {}
          description = `Amazing content from ${country}! 🇨🇮 Follow Mr. WorldWideWebster for more global shorts! #shorts #${country.toLowerCase()} #worldwide`;
        }

        // Hermes checks title + description quality
        const titleCheck = await this._hermesCheck('YouTube title', title, `Country: ${country}`);
        if (!titleCheck.passed && titleCheck.score < 40) {
          this.logger.warn(`Hermes rejected title (${titleCheck.score}): ${titleCheck.feedback} — using original`);
          title = originalTitle.substring(0, 100);
        }

        const descCheck = await this._hermesCheck('YouTube description', description, `Country: ${country}`);
        if (!descCheck.passed) {
          this.logger.warn(`Hermes suggests improving description: ${descCheck.feedback}`);
        }

        // Final profanity check
        if (this._hasProfanity(title) || this._hasProfanity(description)) {
          title = `${country} Clip #shorts`;
          description = `Amazing content from ${country}. Follow Mr. WorldWideWebster! #shorts`;
        }

        const uploadResult = await this._uploadToYouTube({ videoPath: s.path, title, description, tags: ['mr worldwidewebster', 'shorts', country.toLowerCase()] });
        if (uploadResult) {
          uploaded.push({ title, url: uploadResult.url, country });
          await this._boostVideo(uploadResult.url);
        } else { errors.push(`Upload failed: ${title}`); }
      } catch (e) { errors.push(`Upload error: ${e.message}`); }
    }

    // Save memory
    const cm = this.memory['channel-memory'];
    cm.totalVideosPosted = (cm.totalVideosPosted || 0) + uploaded.length;
    if (!cm.countriesUsedThisWeek) cm.countriesUsedThisWeek = [];
    for (const c of countries) { if (!cm.countriesUsedThisWeek.includes(c)) cm.countriesUsedThisWeek.push(c); }
    if (cm.countriesUsedThisWeek.length > 14) cm.countriesUsedThisWeek = cm.countriesUsedThisWeek.slice(-14);
    this._saveMemory('channel-memory', cm);
    await this._sendDiscord('daily', { videos: uploaded, countries: cm.countriesUsedThisWeek, totalVideos: cm.totalVideosPosted, errors });

    this.logger.header('SUMMARY');
    this.logger.info(`URLs: ${allUrls.length} | Shorts: ${shorts.length} | Uploaded: ${uploaded.length}`);
    errors.forEach(e => this.logger.warn(`  ${e}`));
    return { uploadedVideos: uploaded, errors };
  }

  async runNightly() {
    this.logger.header('🌙 NIGHTLY: Hermes investigates, suggests strategy');
    if (!this.hermes || !this.hermes.isAvailable()) {
      this.logger.warn('Hermes not available');
      return;
    }
    const cm = this.memory['channel-memory'] || {};
    this.logger.info(`Total: ${cm.totalVideosPosted || 0} | Countries: ${(cm.countriesUsedThisWeek || []).join(', ')}`);

    const result = await this.hermes.chat(
      `NIGHTLY INVESTIGATION for Mr. WorldWideWebster.

Current:
- Videos: ${cm.totalVideosPosted || 0}
- Countries used: ${(cm.countriesUsedThisWeek || []).join(', ')}

Tasks:
1. Suggest 5 NEW countries to try tomorrow
2. Suggest 10 search queries for tomorrow
3. What content formats are winning?

Respond with format:
NEW COUNTRIES: ...
QUERIES: ...
FORMATS: ...
STRATEGY: ...`,
      { timeout: 300000 }
    );

    this.logger.success(`Investigation complete`);
    if (result.output) this.logger.info(`Hermes: ${result.output.substring(0, 500)}`);
    await this._sendDiscord('daily', { videos: [], investigation: result.output?.substring(0, 1000), countries: cm.countriesUsedThisWeek, totalVideos: cm.totalVideosPosted, errors: [] });
    return result;
  }

  async run() {
    await this.initialize();
    const args = process.argv.slice(2);
    const mode = args.indexOf('--mode') !== -1 ? args[args.indexOf('--mode') + 1] : 'daily';
    if (mode === 'daily') await this.runDaily();
    else if (mode === 'nightly' || mode === 'review') await this.runNightly();
    else { console.log(`Unknown: ${mode}`); process.exit(1); }
    this.logger.success('Done');
  }
}

process.on('uncaughtException', e => console.error(e.message));
process.on('unhandledRejection', r => console.error(r?.message || r));
new GitHubActionsRunner().run().catch(e => { console.error(`Fatal: ${e.message}`); process.exit(1); });
