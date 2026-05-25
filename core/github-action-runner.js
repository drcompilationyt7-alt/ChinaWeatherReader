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

    this.nativeKeywords = {
      'Japan': '日本語 ダンス 面白い トレンド',
      'China': '舞蹈 搞笑 中国 抖音',
      'South Korea': '한국 댄스 웃긴',
      'Thailand': 'ไทย ตลก เต้น',
      'Vietnam': 'việt nam nhảy hài',
      'India': 'भारत नृत्य मज़ेदार',
      'Indonesia': 'indonesia lucu menari',
      'Brazil': 'brasil dança engraçado',
      'Mexico': 'méxico baile gracioso',
      'France': 'france danse drôle',
      'Germany': 'deutschland tanzen lustig',
      'Italy': 'italia ballo divertente',
      'Spain': 'españa baile gracioso',
      'Egypt': 'مصر رقص مضحك',
      'Nigeria': 'nigeria dance funny',
      'UK': 'uk funny viral',
      'Australia': 'australia funny viral'
    };

    this.allC = ['Nigeria','Germany','Brazil','Mexico','UK','Egypt','Italy','Spain','France','Australia','Japan','South Korea','China','Thailand','Vietnam','India','Indonesia'];
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
   * Call local Ollama API directly (Qwen2.5 7B) — much cleaner than Hermes CLI for generation.
   * Returns the model's output text or null.
   */
  async _ollamaGenerate(prompt, options = {}) {
    try {
      const http = require('http');
      const data = JSON.stringify({
        model: process.env.OLLAMA_MODEL || 'qwen2.5:7b',
        prompt: prompt,
        stream: false,
        options: {
          temperature: options.temperature || 0.7,
          num_predict: options.maxTokens || 200,
        }
      });

      return new Promise((resolve) => {
        const req = http.request({
          hostname: '127.0.0.1',
          port: 11434,
          path: '/api/generate',
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) },
          timeout: options.timeout || 60000
        }, (res) => {
          let body = '';
          res.on('data', chunk => body += chunk);
          res.on('end', () => {
            try {
              const parsed = JSON.parse(body);
              const result = (parsed.response || '').trim();
              if (result) {
                this.logger.success(`Ollama: "${result.substring(0, 120)}..."`);
                return resolve(result);
              }
            } catch {}
            this.logger.warn(`Ollama raw: ${body.substring(0, 200)}`);
            resolve(null);
          });
        });
        req.on('error', (e) => { this.logger.warn(`Ollama API: ${e.message}`); resolve(null); });
        req.on('timeout', () => { req.destroy(); resolve(null); });
        req.write(data);
        req.end();
      });
    } catch (e) {
      this.logger.warn(`Ollama: ${e.message}`);
      return null;
    }
  }

  /**
   * Generate voiceover: OpenRouter → Ollama direct → default
   */
  async _generateVoiceover(country, transcriptText) {
    // OpenRouter
    try {
      const ctx = transcriptText
        ? `You narrate for Mr. WorldWideWebster. Video from ${country}. Content: "${transcriptText.substring(0, 500)}". Write ONE sentence (8-15 words). Return ONLY sentence.`
        : `Write ONE sentence for a video from ${country}.`;
      const r = await Promise.race([this.ai.chat(ctx, { useCheapModel: true, temperature: 0.7 }), new Promise(r => setTimeout(() => r(''), 8000))]);
      if (r?.length > 5) { const c = r.replace(/["']/g, '').trim().substring(0, 120); if (!this._hasProfanity(c)) return c; }
    } catch {}

    // Ollama direct
    this.logger.info('OpenRouter failed — Ollama Qwen generating voiceover...');
    const prompt = `Write ONE short sentence (8-15 words) introducing a video from ${country}. Example: "Watch this amazing dance from Japan!"`;
    const result = await this._ollamaGenerate(prompt, { temperature: 0.8, maxTokens: 100 });
    if (result && result.length > 5 && !this._hasProfanity(result)) {
      return result.replace(/["']/g, '').trim().substring(0, 120);
    }
    return `Check out this clip from ${country}`;
  }

  /**
   * Generate title: OpenRouter → Ollama direct → original
   */
  async _generateTitle(country, transcriptText, originalTitle) {
    // OpenRouter
    try {
      const td = await this.ai.chatJSON(
        `Generate YouTube Shorts title+description. Country: ${country}\n${transcriptText ? `Transcript: "${transcriptText.substring(0, 500)}"` : ''}\nTitle: catchy, max 70 chars. Description: 3-4 sentences. Hashtags. Return JSON.`,
        `Title for ${country}`, { useCheapModel: true, temperature: 0.7 }
      );
      if (td?.title?.length > 3) return { title: td.title.substring(0, 100), description: td.description || '' };
    } catch {}

    // Ollama direct
    this.logger.info('OpenRouter failed — Ollama Qwen generating title...');
    const prompt = `Generate a YouTube Shorts title (max 70 chars) for a video from ${country}. ${transcriptText ? `Content: "${transcriptText.substring(0, 200)}"` : ''} Title only.`;
    const result = await this._ollamaGenerate(prompt, { temperature: 0.8, maxTokens: 100 });
    if (result && result.length > 5 && result.length < 100) {
      const cleaned = result.replace(/["']/g, '').trim().substring(0, 100);
      if (!this._hasProfanity(cleaned)) {
        return { title: cleaned, description: `Amazing content from ${country}! Follow Mr. WorldWideWebster for more! #shorts #${country.toLowerCase()} #worldwide` };
      }
    }

    this.logger.warn('All title generators failed — using original');
    return { title: originalTitle.substring(0, 100), description: `Amazing content from ${country}! Follow Mr. WorldWideWebster! #shorts #${country.toLowerCase()} #worldwide` };
  }

  _burnSubtitles(videoPath, outputPath, text) {
    if (!text) return false;
    try {
      const lines = [];
      let cur = '';
      for (const w of text.split(' ')) {
        if ((cur + ' ' + w).length > 30) { lines.push(cur); cur = w; }
        else { cur = cur ? cur + ' ' + w : w; }
      }
      if (cur) lines.push(cur);
      const displayLines = lines.slice(0, 3);

      const srtPath = videoPath.replace('.mp4', '_caption.srt');
      const srtContent = `1\n00:00:00,000 --> 00:00:30,000\n${displayLines.join('\n')}\n`;
      fs.writeFileSync(srtPath, srtContent, 'utf8');

      const cmd = `ffmpeg -y -i "${videoPath}" -vf "subtitles='${srtPath.replace(/'/g, "'\\\\''")}':force_style='FontName=Arial,FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=80,Alignment=2'" -c:v libx264 -preset ultrafast -crf 23 -c:a copy "${outputPath}" 2>/dev/null`;
      execSync(cmd, { timeout: 60000, maxBuffer: 50*1024*1024 });

      try { fs.unlinkSync(srtPath); } catch {}
      const exists = fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000;
      if (exists) this.logger.success(`Captions: ${path.basename(outputPath)} — "${text.substring(0, 60)}..."`);
      return exists;
    } catch (e) {
      this.logger.warn(`Caption burn failed: ${e.message.substring(0, 100)}`);
      return false;
    }
  }

  async _detectCountry(transcript, title, expected, sourceUrl) {
    let country = expected;
    let confidence = 50;
    let reasons = [];

    // 1. Title hint: explicit country name or flag emoji
    if (title) {
      const lowerTitle = title.toLowerCase();
      for (const c of this.allC) {
        if (new RegExp(`\\b${c.toLowerCase()}\\b`).test(lowerTitle) ||
            title.includes('🇦🇺') || title.includes('🇧🇷') || title.includes('🇨🇳') ||
            title.includes('🇯🇵') || title.includes('🇰🇷') || title.includes('🇹🇭') ||
            title.includes('🇮🇳') || title.includes('🇩🇪') || title.includes('🇫🇷') ||
            title.includes('🇪🇬') || title.includes('🇲🇽') || title.includes('🇳🇬')) {
          if (c !== expected) {
            this.logger.info(`🇨🇮 Title hint "${c}" — correcting ${expected} → ${c}`);
            return { country: c, confidence: 85, changed: true, reasons: [`Title mentions ${c}`] };
          }
        }
      }
    }

    // 2. Audio language
    if (transcript?.language) {
      for (const [c, langs] of Object.entries(this.countryLanguages)) {
        if (langs.includes(transcript.language)) {
          countries = c; confidence = 75; reasons.push(`Audio "${transcript.language}" = ${c}`);
          break;
        }
      }
    }

    // 3. yt-dlp channel
    try {
      if (sourceUrl) {
        const meta = execSync(`yt-dlp --dump-json --no-download "${sourceUrl}" 2>/dev/null`, { timeout: 10000, encoding: 'utf8', maxBuffer: 1024*1024 }).trim();
        if (meta) {
          const p = JSON.parse(meta.split('\n')[0]);
          if (p.language) {
            for (const [c, langs] of Object.entries(this.countryLanguages)) {
              if (langs.includes(p.language)) { if (c !== country) { country = c; } confidence = 75; reasons.push(`YT lang "${p.language}" = ${c}`); break; }
            }
          }
          if (p.channel) {
            for (const c of this.allC) {
              if (p.channel.toLowerCase().includes(c.toLowerCase()) && c !== country) {
                country = c; confidence = 80; reasons.push(`Channel "${p.channel.substring(0, 20)}" hints ${c}`);
                break;
              }
            }
          }
        }
      }
    } catch {}

    const changed = country !== expected;
    this.logger.info(`🇨🇮 ${changed ? 'CORRECTED' : 'CONFIRMED'}: ${country} (${confidence}%)${reasons.length ? ' — ' + reasons.join('; ') : ''}`);
    return { country, confidence, changed, reasons };
  }

  async initialize() {
    this.logger.header('Mr. WorldWideWebster — OpenRouter + Ollama Fallback + SRT Captions');
    this.logger.info(`OpenRouter keys: ${['', '_2', '_3', '_4'].map(s => process.env['OPENROUTER_API_KEY' + s] ? '✅' : '❌').join(' ')}`);

    // Test Ollama connectivity
    try {
      const http = require('http');
      const result = await new Promise(r => {
        http.get('http://127.0.0.1:11434/api/tags', (res) => {
          let b = ''; res.on('data', c => b += c); res.on('end', () => r(true));
        }).on('error', () => r(false));
      });
      this.logger.info(result ? 'Ollama API reachable at localhost:11434' : 'Ollama not reachable');
    } catch { this.logger.info('Ollama not available'); }

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
      if (this.hermes.isAvailable()) this.logger.success('Hermes CLI ready — country detection');
      else this.logger.info('Hermes CLI not available');
    } catch (e) { this.logger.info('Hermes CLI not installed'); }
    this.logger.success('Initialized');
  }

  _loadMemory() {
    if (!fs.existsSync(this.memoryPath)) fs.mkdirSync(this.memoryPath, { recursive: true });
    const defaultMem = {
      channelName: 'Mr. WorldWideWebster',
      totalVideosPosted: 0,
      countriesUsedThisWeek: [],
      hermesNotes: []
    };
    const fp = path.join(this.memoryPath, 'channel-memory.json');
    if (fs.existsSync(fp)) {
      try {
        const existing = JSON.parse(fs.readFileSync(fp, 'utf8'));
        this.memory['channel-memory'] = {
          channelName: existing.channelName || defaultMem.channelName,
          totalVideosPosted: existing.totalVideosPosted || 0,
          countriesUsedThisWeek: existing.countriesUsedThisWeek || [],
          hermesNotes: (existing.hermesNotes || []).slice(-20)
        };
        const oldHistory = path.join(this.memoryPath, 'content-history.json');
        if (fs.existsSync(oldHistory)) fs.unlinkSync(oldHistory);
      } catch { this.memory['channel-memory'] = defaultMem; fs.writeFileSync(fp, JSON.stringify(defaultMem, null, 2)); }
    } else { this.memory['channel-memory'] = defaultMem; fs.writeFileSync(fp, JSON.stringify(defaultMem, null, 2)); }
  }
  _saveMemory() {
    fs.writeFileSync(path.join(this.memoryPath, 'channel-memory.json'), JSON.stringify(this.memory['channel-memory'], null, 2));
  }

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
      this.logger.info('Waiting 5 min for YouTube processing...');
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
    // OpenRouter
    try {
      const r = await this.ai.chat(`Translate to natural English: "${text.substring(0, 300)}" Return ONLY translation.`, text, { useCheapModel: true, temperature: 0.3 });
      if (r?.length > 3) return r.replace(/["']/g, '').trim().substring(0, 200);
    } catch {}
    // Ollama direct
    this.logger.info('OpenRouter failed — Ollama Qwen translating...');
    const prompt = `Translate this to English. Return ONLY the translation:\n${text.substring(0, 300)}`;
    const result = await this._ollamaGenerate(prompt, { temperature: 0.3, maxTokens: 300 });
    if (result && result.length > 3 && !result.includes('Translate this to English')) {
      return result.replace(/["']/g, '').trim().substring(0, 200);
    }
    this.logger.warn(`Translation failed, raw: "${(result || '').substring(0, 100)}"`);
    return null;
  }

  async runDaily() {
    this.logger.header('DAILY: Find → Detect → Create → Upload + Boost');
    const errors = [];
    const uploaded = [];

    // Step 1: Bilingual queries
    this.logger.info('Step 1: Bilingual queries...');
    const ch = this.memory['channel-memory'] || {};
    const used = ch.countriesUsedThisWeek || [];
    const avail = this.allC.filter(c => !used.includes(c));
    const countries = [
      avail.length ? avail[Math.floor(Math.random()*avail.length)] : this.allC[Math.floor(Math.random()*this.allC.length)],
      this.allC[Math.floor(Math.random()*this.allC.length)],
      this.allC[Math.floor(Math.random()*this.allC.length)]
    ];

    const nativeKw = countries.map(c => this.nativeKeywords[c] || c);
    const fallbackQ = countries.map((c, i) => {
      const isAsian = ['Japan','South Korea','China','Thailand','Vietnam','India','Indonesia'].includes(c);
      return [nativeKw[i], isAsian ? `${c} douyin #shorts` : `${c} #shorts`];
    }).flat().slice(0, 5);

    let queries = fallbackQ;
    try {
      const prompt = `Generate 5 YouTube search queries for SHORT videos. Mix English + native language.\nCountries: ${countries.join(', ')}\nNative keywords:\n${countries.map((c, i) => `- ${c}: ${nativeKw[i]}`).join('\n')}\nReturn array of 5 strings.`;
      const r = await Promise.race([this.ai.chatJSON(prompt, 'queries', { useCheapModel: true, temperature: 0.8 }), new Promise((_, rj) => setTimeout(() => rj(new Error('timeout')), 12000))]);
      queries = Array.isArray(r) ? r.slice(0, 5) : fallbackQ;
    } catch {}
    this.logger.success(`Queries: ${queries.join(' | ')}`);

    // Step 2-4: Search, rank, download
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

    // Step 5: Process each
    const { createShort } = require('./clip-editor');
    const shorts = [];

    for (let i = 0; i < downloaded.length; i++) {
      const v = downloaded[i];
      const originalCountry = countries[i] || countries[0] || 'Global';
      const originalTitle = v.sourceUrl?.title || v.title || `${originalCountry} Clip`;
      const sourceUrl = v.sourceUrl || '';
      this.logger.info(`=== Video ${i+1}: expected ${originalCountry} ===`);

      let transcript = null;
      try { transcript = await this._transcribeAudio(v.path); } catch {}

      if (transcript && this._hasProfanity(transcript.text)) {
        this.logger.warn(`PROFANITY — skip`);
        errors.push(`Profanity`);
        continue;
      }

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

      // Translate if non-English and has actual speech (> 10 chars)
      let englishSubtitle = null;
      if (transcript?.isNonEnglish && transcript.text && transcript.text.length > 10) {
        englishSubtitle = await this._translateText(transcript.text);
      } else if (transcript?.isNonEnglish) {
        this.logger.info('Short/no speech — skip translation');
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
            this.logger.info(`Adding captions: "${englishSubtitle.substring(0, 80)}..."`);
            const subbedPath = result.replace('.mp4', '_captioned.mp4');
            if (this._burnSubtitles(result, subbedPath, englishSubtitle)) {
              try { fs.unlinkSync(result); } catch {}
              finalPath = subbedPath;
            }
          }
          shorts.push({ path: finalPath, country, voiceoverText, transcript: transcript?.text, originalTitle, url: '', hasCaptions: !!englishSubtitle && !englishSubtitle.startsWith('Query:') });
        }
      } catch (e) { this.logger.warn(`Create short failed: ${e.message}`); }
    }

    this.logger.success(`Created ${shorts.length} Shorts`);

    if (shorts.length === 0) {
      this.logger.warn('No shorts created');
      return { uploadedVideos: [], errors: ['No shorts'] };
    }

    // Step 6: Generate titles + upload
    for (let i = 0; i < shorts.length; i++) {
      const s = shorts[i];
      try {
        const targetTitle = await this._generateTitle(s.country, s.transcript, s.originalTitle);

        if (this._hasProfanity(targetTitle.title) || this._hasProfanity(targetTitle.description)) {
          targetTitle.title = `${s.country} Clip #shorts`;
          targetTitle.description = `Amazing content from ${s.country}. Follow Mr. WorldWideWebster!`;
        }

        this.logger.success(`Title: "${targetTitle.title}"`);
        this.logger.info(`Desc: ${targetTitle.description.substring(0, 100)}...`);

        const uploadResult = await this._uploadToYouTube({
          videoPath: s.path, title: targetTitle.title, description: targetTitle.description,
          tags: ['mr worldwidewebster', 'shorts', s.country.toLowerCase()]
        });

        if (uploadResult) {
          uploaded.push({ title: targetTitle.title, url: uploadResult.url, country: s.country, captions: s.hasCaptions });
          await this._boostVideo(uploadResult.url);
        } else {
          errors.push(`Upload failed: ${targetTitle.title}`);
        }
      } catch (e) { errors.push(`Upload error: ${e.message}`); }
    }

    // Save memory
    const cm = this.memory['channel-memory'] || {};
    cm.totalVideosPosted = (cm.totalVideosPosted || 0) + uploaded.length;
    if (!cm.countriesUsedThisWeek) cm.countriesUsedThisWeek = [];
    for (const u of uploaded) {
      if (u.country && !cm.countriesUsedThisWeek.includes(u.country)) cm.countriesUsedThisWeek.push(u.country);
    }
    if (cm.countriesUsedThisWeek.length > 14) cm.countriesUsedThisWeek = cm.countriesUsedThisWeek.slice(-14);
    this.memory['channel-memory'] = cm;
    this._saveMemory();

    await this._sendDiscord('daily', { videos: uploaded, countries: cm.countriesUsedThisWeek, totalVideos: cm.totalVideosPosted, errors });

    this.logger.header('SUMMARY');
    this.logger.success(`✅ Posted ${uploaded.length} shorts:`);
    for (const u of uploaded) {
      this.logger.success(`   ${u.country}: ${u.title} → ${u.url}${u.captions ? ' (captioned)' : ''}`);
    }
    errors.forEach(e => this.logger.warn(`  ⚠ ${e}`));
    return { uploadedVideos: uploaded, errors };
  }

  async runNightly() {
    this.logger.header('🌙 NIGHTLY: Hermes investigates');
    if (!this.hermes || !this.hermes.isAvailable()) { this.logger.warn('Hermes not available'); return; }
    const cm = this.memory['channel-memory'] || {};
    this.logger.info(`Total: ${cm.totalVideosPosted || 0} | Countries: ${(cm.countriesUsedThisWeek || []).join(', ')}`);

    const result = await this.hermes.chat(
      `NIGHTLY INVESTIGATION for Mr. WorldWideWebster.\nCurrent: Videos: ${cm.totalVideosPosted || 0} | Countries: ${(cm.countriesUsedThisWeek || []).join(', ')}\n\nTasks:\n1. Suggest 5 NEW countries\n2. Suggest 10 queries (mix English + native language)\n3. What formats are winning?\n\nRespond:\nNEW COUNTRIES: ...\nQUERIES: ...\nFORMATS: ...\nSTRATEGY: ...`,
      { timeout: 300000 }
    );

    this.logger.info(`Hermes raw output (first 300): ${(result.output || '').substring(0, 300)}`);

    if (result.success && result.output) {
      if (!cm.hermesNotes) cm.hermesNotes = [];
      cm.hermesNotes.push({ date: new Date().toISOString().split('T')[0], insight: result.output.substring(0, 500) });
      if (cm.hermesNotes.length > 20) cm.hermesNotes = cm.hermesNotes.slice(-20);
      this.memory['channel-memory'] = cm;
      this._saveMemory();
    }

    this.logger.success(`Investigation: ${result.success ? '✅' : '❌'}`);
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
