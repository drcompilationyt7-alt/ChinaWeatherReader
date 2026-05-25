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

/**
 * Country trend keywords — curated base trends per country.
 * Hermes updates these nightly by browsing Shorts.
 * Daily runner blends base + trending keywords for queries.
 */
const BASE_TREND_KEYWORDS = {
  'China': ['chinese trend', 'beautiful Chinese girl', 'Chinese love story', 'colour wheel trend', 'douyin'],
  'Japan': ['japanese trend', 'japanese fashion', 'japanese street', 'kawaii', 'japan vlog'],
  'South Korea': ['kpop', 'blackpink', 'bts', 'korean fashion', 'korean makeup', 'seoul'],
  'Thailand': ['thai trend', 'thai street food', 'bangkok', 'thai girl'],
  'Vietnam': ['vietnam trend', 'hanoi', 'saigon', 'vietnam street'],
  'India': ['indian trend', 'bollywood', 'mumbai', 'delhi', 'indian wedding'],
  'Indonesia': ['indonesian trend', 'jakarta', 'bali', 'indonesia viral'],
  'Brazil': ['brazil trend', 'funk', 'rio', 'brazil dance', 'samba'],
  'Mexico': ['mexico trend', 'mexico dance', 'latin', 'ciudad de mexico'],
  'France': ['france trend', 'paris', 'french fashion', 'fendi'],
  'Italy': ['italy trend', 'italian fashion', 'fendi', 'milan', 'rome', 'prada'],
  'Germany': ['germany trend', 'berlin', 'german', 'munich'],
  'Spain': ['spain trend', 'barcelona', 'madrid', 'spanish dance'],
  'UK': ['uk trend', 'london', 'british', 'uk viral'],
  'Egypt': ['egypt trend', 'cairo', 'arabic', 'egypt viral'],
  'Nigeria': ['nigeria trend', 'lagos', 'afrobeat', 'nigeria dance', 'naija'],
  'Australia': ['australia trend', 'sydney', 'melbourne', 'aussie']
};

class GitHubActionsRunner {
  constructor() {
    this.logger = new Logger('GHAction');
    this.ai = null;
    this.hermes = null;
    this.memory = {};
    this.memoryPath = path.join(__dirname, '..', 'memory');
    this.youtubeBridge = null;

    this.bannedWords = [
      'fuck', 'shit', 'bitch', 'asshole', 'motherfucker', 'dick', 'cunt',
      'pussy', 'bastard', 'whore', 'slut', 'damn', 'cock', 'nigger', 'nigga',
      'faggot', 'retard', 'chink', 'spic', 'kike', 'gook', 'raghead',
      'cracker', 'tranny', 'dyke', 'twat'
    ];

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

    this.allC = Object.keys(BASE_TREND_KEYWORDS);
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
   * Get trend-mixed query set for a country.
   * Blends: base nativeKeywords + learned trendingKeywords from memory + random from BASE_TREND_KEYWORDS.
   */
  _getTrendingQueriesForCountries(countries) {
    const ch = this.memory['channel-memory'] || {};
    const trending = ch.trendingKeywords || {};  // Learned from nightly Hermes

    const queries = [];
    for (const c of countries) {
      const base = BASE_TREND_KEYWORDS[c] || [c];
      const learned = trending[c] || [];

      // Pick 2 from base + 1 from learned + 1 native keyword
      const picks = [
        ...base.slice(0, 2),
        ...(learned.length > 0 ? [learned[Math.floor(Math.random() * learned.length)]] : []),
        c.toLowerCase()
      ];

      // Shuffle and add #shorts
      for (const p of picks.sort(() => Math.random() - 0.5).slice(0, 3)) {
        const q = `${p} #shorts`;
        if (!queries.includes(q)) queries.push(q);
      }
    }

    return queries.slice(0, 6);
  }

  async _ollamaGenerate(prompt, options = {}) {
    try {
      const http = require('http');
      const data = JSON.stringify({
        model: process.env.OLLAMA_MODEL || 'qwen2.5:7b',
        prompt: prompt,
        stream: false,
        options: { temperature: options.temperature || 0.7, num_predict: options.maxTokens || 200 }
      });
      return new Promise((resolve) => {
        const req = http.request({
          hostname: '127.0.0.1', port: 11434, path: '/api/generate', method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) },
          timeout: options.timeout || 60000
        }, (res) => {
          let body = '';
          res.on('data', chunk => body += chunk);
          res.on('end', () => {
            try {
              const parsed = JSON.parse(body);
              const result = (parsed.response || '').trim();
              if (result) { this.logger.success(`Ollama: "${result.substring(0, 120)}..."`); return resolve(result); }
            } catch {}
            this.logger.warn(`Ollama raw: ${body.substring(0, 200)}`);
            resolve(null);
          });
        });
        req.on('error', (e) => { this.logger.warn(`Ollama: ${e.message}`); resolve(null); });
        req.on('timeout', () => { req.destroy(); resolve(null); });
        req.write(data);
        req.end();
      });
    } catch (e) { this.logger.warn(`Ollama: ${e.message}`); return null; }
  }

  async _generateVoiceover(country, transcriptText) {
    try {
      const ctx = transcriptText
        ? `You narrate for Mr. WorldWideWebster. Video from ${country}. Content: "${transcriptText.substring(0, 500)}". Write ONE sentence (8-15 words). Return ONLY sentence.`
        : `Write ONE sentence for a video from ${country}.`;
      const r = await Promise.race([this.ai.chat(ctx, { useCheapModel: true, temperature: 0.7 }), new Promise(r => setTimeout(() => r(''), 8000))]);
      if (r?.length > 5) { const c = r.replace(/["']/g, '').trim().substring(0, 120); if (!this._hasProfanity(c)) return c; }
    } catch {}
    this.logger.info('OpenRouter failed — Ollama voiceover...');
    const result = await this._ollamaGenerate(`Write ONE short sentence (8-15 words) introducing a video from ${country}.`, { temperature: 0.8, maxTokens: 100 });
    if (result?.length > 5 && !this._hasProfanity(result)) return result.replace(/["']/g, '').trim().substring(0, 120);
    return `Check out this clip from ${country}`;
  }

  async _generateTitle(country, transcriptText, originalTitle) {
    try {
      const td = await this.ai.chatJSON(
        `Generate YouTube Shorts title+description. Country: ${country}\n${transcriptText ? `Transcript: "${transcriptText.substring(0, 500)}"` : ''}\nTitle: catchy, max 70 chars. Description: 3-4 sentences. Hashtags. Return JSON.`,
        `Title for ${country}`, { useCheapModel: true, temperature: 0.7 }
      );
      if (td?.title?.length > 3) return { title: td.title.substring(0, 100), description: td.description || '' };
    } catch {}
    this.logger.info('OpenRouter failed — Ollama title...');
    const prompt = `Generate a YouTube Shorts title (max 70 chars) for a video from ${country}. ${transcriptText ? `Content: "${transcriptText.substring(0, 200)}"` : ''}`;
    const result = await this._ollamaGenerate(prompt, { temperature: 0.8, maxTokens: 100 });
    if (result?.length > 5 && result.length < 100) {
      const cleaned = result.replace(/["']/g, '').trim().substring(0, 100);
      if (!this._hasProfanity(cleaned)) return { title: cleaned, description: `Amazing content from ${country}! Follow Mr. WorldWideWebster! #shorts #${country.toLowerCase()} #worldwide` };
    }
    this.logger.warn('All title generators failed — using original');
    return { title: originalTitle.substring(0, 100), description: `Amazing content from ${country}! Follow Mr. WorldWideWebster! #shorts #${country.toLowerCase()} #worldwide` };
  }

  _burnSubtitles(videoPath, outputPath, text) {
    if (!text) return false;
    try {
      const lines = [];
      let cur = '';
      for (const w of text.split(' ')) { if ((cur + ' ' + w).length > 30) { lines.push(cur); cur = w; } else { cur = cur ? cur + ' ' + w : w; } }
      if (cur) lines.push(cur);
      const displayLines = lines.slice(0, 3);
      const srtPath = videoPath.replace('.mp4', '_caption.srt');
      fs.writeFileSync(srtPath, `1\n00:00:00,000 --> 00:00:30,000\n${displayLines.join('\n')}\n`, 'utf8');
      execSync(`ffmpeg -y -i "${videoPath}" -vf "subtitles='${srtPath.replace(/'/g, "'\\\\''")}':force_style='FontName=Arial,FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=80,Alignment=2'" -c:v libx264 -preset ultrafast -crf 23 -c:a copy "${outputPath}" 2>/dev/null`, { timeout: 60000, maxBuffer: 50*1024*1024 });
      try { fs.unlinkSync(srtPath); } catch {}
      const exists = fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000;
      if (exists) this.logger.success(`Captions burned: "${text.substring(0, 60)}..."`);
      return exists;
    } catch (e) { this.logger.warn(`Caption failed: ${e.message.substring(0, 100)}`); return false; }
  }

  async _detectCountry(transcript, title, expected, sourceUrl) {
    let country = expected;
    let confidence = 50;
    let reasons = [];

    if (title) {
      for (const c of this.allC) {
        if (new RegExp(`\\b${c.toLowerCase()}\\b`).test(title.toLowerCase()) || /[🇦🇺🇧🇷🇨🇳🇯🇵🇰🇷🇹🇭🇮🇳🇩🇪🇫🇷🇪🇬🇲🇽🇳🇬]/.test(title)) {
          if (c !== expected) { this.logger.info(`🇨🇮 Title "${c}" → ${expected}→${c}`); return { country: c, confidence: 85, changed: true, reasons: [`Title: ${c}`] }; }
        }
      }
    }
    if (transcript?.language) {
      for (const [c, langs] of Object.entries(this.countryLanguages)) {
        if (langs.includes(transcript.language)) { country = c; confidence = 75; reasons.push(`Audio ${transcript.language} = ${c}`); break; }
      }
    }
    try {
      if (sourceUrl) {
        const meta = execSync(`yt-dlp --dump-json --no-download "${sourceUrl}" 2>/dev/null`, { timeout: 10000, encoding: 'utf8', maxBuffer: 1024*1024 }).trim();
        if (meta) {
          const p = JSON.parse(meta.split('\n')[0]);
          if (p.language) { for (const [c, langs] of Object.entries(this.countryLanguages)) { if (langs.includes(p.language)) { country = c; confidence = 75; break; } } }
          if (p.channel) { for (const c of this.allC) { if (p.channel.toLowerCase().includes(c.toLowerCase())) { country = c; confidence = 80; break; } } }
        }
      }
    } catch {}
    const changed = country !== expected;
    this.logger.info(`🇨🇮 ${changed ? 'CORRECTED' : 'OK'}: ${country} (${confidence}%)${reasons.length ? ' — ' + reasons.join('; ') : ''}`);
    return { country, confidence, changed, reasons };
  }

  /**
   * Hermes learns trending keywords by browsing Shorts.
   * Saves to channel-memory.trendingKeywords for daily use.
   */
  async _hermesLearnTrends(countries) {
    if (!this.hermes || !this.hermes.isAvailable()) return;
    this.logger.info('Hermes browsing Shorts for trending keywords...');

    for (const country of countries) {
      try {
        const result = await this.hermes.chat(
          `BROWSE YouTube Shorts trending for ${country}. Find 3-5 CURRENT trending keywords/topics (specific: "colour wheel trend", "fendi", "kpop", not generic like "dance"). Return ONLY comma-separated keywords: keyword1, keyword2, keyword3`,
          { timeout: 120000 }
        );

        if (result.success && result.output) {
          this.logger.info(`Hermes trends for ${country}: ${result.output.substring(0, 150)}`);
          // Parse comma-separated keywords
          const keywords = result.output.split(',').map(k => k.trim().replace(/^[\s"']+|[\s"']+$/g, '')).filter(k => k.length > 2);

          if (keywords.length > 0) {
            const cm = this.memory['channel-memory'] || {};
            if (!cm.trendingKeywords) cm.trendingKeywords = {};
            if (!cm.trendingKeywords[country]) cm.trendingKeywords[country] = [];

            // Merge with existing, dedup, keep max 10 per country
            const existing = cm.trendingKeywords[country];
            const merged = [...new Set([...keywords, ...existing])].slice(0, 10);
            cm.trendingKeywords[country] = merged;
            this.memory['channel-memory'] = cm;
            this._saveMemory();
            this.logger.success(`Saved ${keywords.length} trends for ${country}`);
          }
        }
      } catch (e) {
        this.logger.warn(`Hermes browse ${country}: ${e.message}`);
      }
    }
  }

  async initialize() {
    this.logger.header('Mr. WorldWideWebster — Trend-Driven Pipeline');
    this.logger.info(`OpenRouter keys: ${['', '_2', '_3', '_4'].map(s => process.env['OPENROUTER_API_KEY' + s] ? '✅' : '❌').join(' ')}`);

    try {
      const http = require('http');
      await new Promise(r => { http.get('http://127.0.0.1:11434/api/tags', () => { r(true); }).on('error', () => r(false)); });
      this.logger.info('Ollama: OK');
    } catch { this.logger.info('Ollama: unreachable'); }

    this.ai = new AIService();
    await this.ai.waitForInit();
    this._loadMemory();

    try {
      const { YouTubeBridge } = require('../youtube-automation/youtube-bridge');
      this.youtubeBridge = new YouTubeBridge();
      await this.youtubeBridge.initialize();
    } catch (e) { this.logger.warn(`YouTube: ${e.message}`); }

    try {
      const { HermesCLIWrapper } = require('../hermes-agent/hermes-cli-wrapper');
      this.hermes = new HermesCLIWrapper();
      if (this.hermes.isAvailable()) this.logger.success('Hermes: ready — trend learning + detection');
      else this.logger.info('Hermes: not available');
    } catch (e) { this.logger.info('Hermes: not installed'); }

    // Log current learned trends
    const ch = this.memory['channel-memory'] || {};
    if (ch.trendingKeywords) {
      const total = Object.values(ch.trendingKeywords).flat().length;
      this.logger.info(`Loaded ${total} learned trend keywords from memory`);
    }

    this.logger.success('Initialized');
  }

  _loadMemory() {
    if (!fs.existsSync(this.memoryPath)) fs.mkdirSync(this.memoryPath, { recursive: true });
    const defaultMem = { channelName: 'Mr. WorldWideWebster', totalVideosPosted: 0, countriesUsedThisWeek: [], hermesNotes: [], trendingKeywords: {} };
    const fp = path.join(this.memoryPath, 'channel-memory.json');
    if (fs.existsSync(fp)) {
      try {
        const existing = JSON.parse(fs.readFileSync(fp, 'utf8'));
        this.memory['channel-memory'] = {
          channelName: existing.channelName || 'Mr. WorldWideWebster',
          totalVideosPosted: existing.totalVideosPosted || 0,
          countriesUsedThisWeek: existing.countriesUsedThisWeek || [],
          hermesNotes: (existing.hermesNotes || []).slice(-20),
          trendingKeywords: existing.trendingKeywords || {}
        };
        const old = path.join(this.memoryPath, 'content-history.json');
        if (fs.existsSync(old)) fs.unlinkSync(old);
      } catch { this.memory['channel-memory'] = defaultMem; fs.writeFileSync(fp, JSON.stringify(defaultMem, null, 2)); }
    } else { this.memory['channel-memory'] = defaultMem; fs.writeFileSync(fp, JSON.stringify(defaultMem, null, 2)); }
  }
  _saveMemory() { fs.writeFileSync(path.join(this.memoryPath, 'channel-memory.json'), JSON.stringify(this.memory['channel-memory'], null, 2)); }

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
      this.logger.info('Waiting 5 min...');
      await new Promise(r => setTimeout(r, 300000));
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
        try { const p = JSON.parse(output); return { text: p.text || '', language: p.language || 'en', isNonEnglish: p.language !== 'en' && p.language !== 'english' }; }
        catch { return { text: output, language: 'en', isNonEnglish: false }; }
      }
      return null;
    } catch { try { fs.unlinkSync(audioPath); } catch {} return null; }
  }

  async _translateText(text) {
    if (!text) return null;
    try {
      const r = await this.ai.chat(`Translate to natural English: "${text.substring(0, 300)}" Return ONLY translation.`, text, { useCheapModel: true, temperature: 0.3 });
      if (r?.length > 3) return r.replace(/["']/g, '').trim().substring(0, 200);
    } catch {}
    this.logger.info('OpenRouter failed — Ollama translating...');
    const result = await this._ollamaGenerate(`Translate this to English. Return ONLY translation:\n${text.substring(0, 300)}`, { temperature: 0.3, maxTokens: 300 });
    if (result?.length > 3 && !result.includes('Translate this')) return result.replace(/["']/g, '').trim().substring(0, 200);
    return null;
  }

  async runDaily() {
    this.logger.header('DAILY: Trends → Find → Detect → Create → Upload + Boost');
    const errors = [];
    const uploaded = [];

    // Step 1: Pick countries + generate trend-driven queries
    this.logger.info('Step 1: Trend-driven queries...');
    const ch = this.memory['channel-memory'] || {};
    const used = ch.countriesUsedThisWeek || [];
    const avail = this.allC.filter(c => !used.includes(c));
    const countries = [
      avail.length ? avail[Math.floor(Math.random()*avail.length)] : this.allC[Math.floor(Math.random()*this.allC.length)],
      this.allC[Math.floor(Math.random()*this.allC.length)],
      this.allC[Math.floor(Math.random()*this.allC.length)]
    ];

    // Blend base trends + learned trends + native keywords
    let queries = this._getTrendingQueriesForCountries(countries);

    // Log what trends we're using
    const trending = ch.trendingKeywords || {};
    for (const c of countries) {
      const learned = trending[c] || [];
      if (learned.length > 0) {
        this.logger.info(`   ${c} trends: ${learned.join(', ')}`);
      }
    }

    this.logger.success(`Queries: ${queries.join(' | ')}`);

    // Step 2-5: Search, rank, download
    this.logger.info('Step 2: Searching...');
    const allUrls = await findUrlsForQueries(queries, 10);
    if (!allUrls.length) return { uploadedVideos: [], errors: ['No URLs'] };

    this.logger.info('Step 3: Ranking...');
    const { top3 } = await rankVideos(allUrls, queries[0] || '', this.ai);
    if (!top3.length) top3.push(...allUrls.slice(0, 3));

    let downloaded = await downloadVideos(top3, config.paths.clips);
    while (downloaded.length < 3 && downloaded.length < allUrls.length) {
      const extra = allUrls.filter(u => !top3.includes(u));
      if (!extra.length) break;
      const more = await downloadVideos([extra[0]], config.paths.clips);
      downloaded.push(...more);
    }
    this.logger.info(`Downloaded ${downloaded.length} videos`);

    // Step 6: Process each
    const { createShort } = require('./clip-editor');
    const shorts = [];

    for (let i = 0; i < downloaded.length; i++) {
      const v = downloaded[i];
      const originalCountry = countries[i] || countries[0] || 'Global';
      const originalTitle = v.sourceUrl?.title || v.title || `${originalCountry} Clip`;
      const sourceUrl = v.sourceUrl || '';
      this.logger.info(`=== Video ${i+1}: ${originalCountry} ===`);

      let transcript = null;
      try { transcript = await this._transcribeAudio(v.path); } catch {}

      if (transcript && this._hasProfanity(transcript.text)) { this.logger.warn(`PROFANITY — skip`); errors.push(`Profanity`); continue; }

      const detected = await this._detectCountry(transcript, originalTitle, originalCountry, sourceUrl);
      const country = detected.country;

      const voiceoverText = await this._generateVoiceover(country, transcript?.text);

      let voiceoverPath = null;
      try {
        const vDir = path.join(config.paths.assets, 'voiceovers');
        if (!fs.existsSync(vDir)) fs.mkdirSync(vDir, { recursive: true });
        const vPath = path.join(vDir, `vo_${Date.now()}_${i}.mp3`);
        execSync(`edge-tts --voice "en-US-JennyNeural" --text "${voiceoverText.replace(/"/g, '\\"')}" --write-media "${vPath}" 2>/dev/null`, { timeout: 30000 });
        if (fs.existsSync(vPath) && fs.statSync(vPath).size > 1000) voiceoverPath = vPath;
      } catch {}

      let englishSubtitle = null;
      if (transcript?.isNonEnglish && transcript.text?.length > 10) {
        englishSubtitle = await this._translateText(transcript.text);
      }

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
          if (englishSubtitle && englishSubtitle.length > 5 && !englishSubtitle.startsWith('Query:')) {
            const subbedPath = result.replace('.mp4', '_captioned.mp4');
            if (this._burnSubtitles(result, subbedPath, englishSubtitle)) {
              try { fs.unlinkSync(result); } catch {}; finalPath = subbedPath;
            }
          }
          shorts.push({ path: finalPath, country, voiceoverText, transcript: transcript?.text, originalTitle, hasCaptions: !!englishSubtitle && !englishSubtitle.startsWith('Query:') });
        }
      } catch (e) { this.logger.warn(`Create short failed: ${e.message}`); }
    }

    this.logger.success(`Created ${shorts.length} Shorts`);
    if (shorts.length === 0) return { uploadedVideos: [], errors: ['No shorts'] };

    // Step 7: Upload
    for (let i = 0; i < shorts.length; i++) {
      const s = shorts[i];
      try {
        const targetTitle = await this._generateTitle(s.country, s.transcript, s.originalTitle);
        if (this._hasProfanity(targetTitle.title) || this._hasProfanity(targetTitle.description)) {
          targetTitle.title = `${s.country} Clip #shorts`;
          targetTitle.description = `Amazing content from ${s.country}. Follow Mr. WorldWideWebster!`;
        }
        this.logger.success(`Title: "${targetTitle.title}"`);
        const uploadResult = await this._uploadToYouTube({ videoPath: s.path, title: targetTitle.title, description: targetTitle.description, tags: ['mr worldwidewebster', 'shorts', s.country.toLowerCase()] });
        if (uploadResult) { uploaded.push({ title: targetTitle.title, url: uploadResult.url, country: s.country, captions: s.hasCaptions }); await this._boostVideo(uploadResult.url); }
        else { errors.push(`Upload failed: ${targetTitle.title}`); }
      } catch (e) { errors.push(`Upload error: ${e.message}`); }
    }

    // Save memory
    const cm = this.memory['channel-memory'] || {};
    cm.totalVideosPosted = (cm.totalVideosPosted || 0) + uploaded.length;
    if (!cm.countriesUsedThisWeek) cm.countriesUsedThisWeek = [];
    for (const u of uploaded) { if (u.country && !cm.countriesUsedThisWeek.includes(u.country)) cm.countriesUsedThisWeek.push(u.country); }
    if (cm.countriesUsedThisWeek.length > 14) cm.countriesUsedThisWeek = cm.countriesUsedThisWeek.slice(-14);
    this.memory['channel-memory'] = cm; this._saveMemory();

    await this._sendDiscord('daily', { videos: uploaded, countries: cm.countriesUsedThisWeek, totalVideos: cm.totalVideosPosted, errors });

    this.logger.header('SUMMARY');
    this.logger.success(`✅ ${uploaded.length} posted:`);
    for (const u of uploaded) this.logger.success(`   ${u.country}: ${u.title} → ${u.url}${u.captions ? ' (captioned)' : ''}`);
    errors.forEach(e => this.logger.warn(`  ⚠ ${e}`));
    return { uploadedVideos: uploaded, errors };
  }

  async runNightly() {
    this.logger.header('🌙 NIGHTLY: Hermes discovers trends + learns keywords');
    if (!this.hermes || !this.hermes.isAvailable()) { this.logger.warn('Hermes not available'); return; }
    const cm = this.memory['channel-memory'] || {};
    this.logger.info(`Total: ${cm.totalVideosPosted || 0} | Countries: ${(cm.countriesUsedThisWeek || []).join(', ')}`);

    // Hermes investigates AND learns trending keywords
    const result = await this.hermes.chat(
      `NIGHTLY INVESTIGATION for Mr. WorldWideWebster channel.\n\nCurrent state: Videos: ${cm.totalVideosPosted || 0} | Countries this week: ${(cm.countriesUsedThisWeek || []).join(', ')}\n\nTASKS:\n1. Browse YouTube Shorts to find CURRENT trending topics in these countries\n   - For each country, list 3-5 specific trending keywords (like "colour wheel trend", "fendi", "kpop")\n2. Suggest 10 fresh search queries for tomorrow (mix English + native language + trends)\n3. What formats are winning in short-form right now?\n\nRESPOND FORMAT:\nCOUNTRIES: [5 new country suggestions]\nTRENDS: China: keyword1, keyword2, keyword3 | Japan: keyword1, keyword2 | ...\nQUERIES: query1, query2, query3, ...\nFORMATS: format1, format2, format3\nSTRATEGY: brief strategy\n\nIMPORTANT: Trends must be SPECIFIC. "beautiful Chinese girl" yes. "dance" NO.`,
      { timeout: 300000 }
    );

    // Parse and save trends from Hermes output
    if (result.success && result.output) {
      // Save raw output to hermesNotes
      if (!cm.hermesNotes) cm.hermesNotes = [];
      cm.hermesNotes.push({ date: new Date().toISOString().split('T')[0], insight: result.output.substring(0, 500) });
      if (cm.hermesNotes.length > 20) cm.hermesNotes = cm.hermesNotes.slice(-20);

      // Parse trends: "China: keyword1, keyword2 | Japan: keyword1"
      const trendsSection = result.output.match(/TRENDS:[\s\S]*?(?=QUERIES:|$)/i);
      if (trendsSection) {
        this.logger.info('Parsing trends from Hermes output...');
        if (!cm.trendingKeywords) cm.trendingKeywords = {};

        // Split by country (pipe separator or newline)
        const parts = trendsSection[0].split(/[|\n]/).map(s => s.trim()).filter(s => s && !s.toUpperCase().startsWith('TRENDS'));
        for (const part of parts) {
          const colonIndex = part.indexOf(':');
          if (colonIndex > 0) {
            const countryName = part.substring(0, colonIndex).trim();
            const keywords = part.substring(colonIndex + 1).split(',').map(k => k.trim().replace(/^[\s"']+|[\s"']+$/g, '')).filter(k => k.length > 2 && !k.includes(':'));

            // Match country name to our list
            const matchedCountry = this.allC.find(c => c.toLowerCase() === countryName.toLowerCase() || countryName.toLowerCase().includes(c.toLowerCase()));
            if (matchedCountry && keywords.length > 0) {
              if (!cm.trendingKeywords[matchedCountry]) cm.trendingKeywords[matchedCountry] = [];
              const merged = [...new Set([...keywords, ...cm.trendingKeywords[matchedCountry]])].slice(0, 10);
              cm.trendingKeywords[matchedCountry] = merged;
              this.logger.success(`🌋 Learned ${keywords.length} trends for ${matchedCountry}: ${keywords.join(', ')}`);
            }
          }
        }
      }

      this.memory['channel-memory'] = cm;
      this._saveMemory();
    }

    this.logger.success(`Nightly: ${result.success ? '✅' : '❌'}`);
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
