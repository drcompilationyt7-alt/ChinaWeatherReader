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
   * Use Hermes (local Qwen3 8B) to generate content when OpenRouter is exhausted.
   */
  async _hermesGenerate(prompt, timeout = 60000) {
    if (!this.hermes || !this.hermes.isAvailable()) return null;
    try {
      const result = await this.hermes.chat(prompt, { timeout });
      if (result.success && result.output) {
        return result.output.trim();
      }
      return null;
    } catch { return null; }
  }

  /**
   * Generate voiceover: try OpenRouter first, fallback to Hermes.
   */
  async _generateVoiceover(country, transcriptText) {
    // Try OpenRouter
    try {
      const ctx = transcriptText
        ? `You narrate for Mr. WorldWideWebster. Video from ${country}. Content: "${transcriptText.substring(0, 500)}". Write ONE sentence (8-15 words). Return ONLY sentence.`
        : `Write ONE sentence for a video from ${country}.`;
      const result = await Promise.race([
        this.ai.chat(ctx, { useCheapModel: true, temperature: 0.7 }),
        new Promise(r => setTimeout(() => r(''), 8000))
      ]);
      if (result?.length > 5) {
        const cleaned = result.replace(/["']/g, '').trim().substring(0, 120);
        if (!this._hasProfanity(cleaned)) return cleaned;
      }
    } catch {}

    // Fallback: Hermes local model
    this.logger.info('OpenRouter failed for voiceover — Hermes generating...');
    const hermesResult = await this._hermesGenerate(
      `Write ONE short sentence (8-15 words) introducing a video from ${country} for Mr. WorldWideWebster channel. Example: "Watch this amazing dance from Japan!" Return ONLY the sentence.`,
      30000
    );
    if (hermesResult?.length > 5) {
      const cleaned = hermesResult.replace(/["']/g, '').trim().substring(0, 120);
      if (!this._hasProfanity(cleaned)) return cleaned;
    }

    return `Check out this clip from ${country}`;
  }

  /**
   * Generate title: try OpenRouter first, fallback to Hermes, then original.
   */
  async _generateTitle(country, transcriptText, originalTitle) {
    // Try OpenRouter
    try {
      const td = await this.ai.chatJSON(
        `Generate YouTube Shorts title+description. Country: ${country}\n${transcriptText ? `Transcript: "${transcriptText.substring(0, 500)}"` : ''}\nTitle: catchy, max 70 chars. Description: 3-4 sentences. Hashtags. Return JSON.`,
        `Title for ${country}`, { useCheapModel: true, temperature: 0.7 }
      );
      if (td?.title?.length > 3) {
        return { title: td.title.substring(0, 100), description: td.description || '' };
      }
    } catch {}

    // Fallback: Hermes title
    this.logger.info('OpenRouter failed for title — Hermes generating...');
    try {
      const hTitle = await this._hermesGenerate(
        `Generate a YouTube Shorts title (max 70 chars) for a video from ${country}. ${transcriptText ? `Content starts with: "${transcriptText.substring(0, 200)}"` : ''} Return ONLY the title. Example: "🔥 Amazing Dance From Japan #shorts"`,
        30000
      );
      if (hTitle?.length > 3 && hTitle.length < 100) {
        const cleaned = hTitle.replace(/["']/g, '').trim().substring(0, 100);
        if (!this._hasProfanity(cleaned)) {
          return { title: cleaned, description: `Amazing content from ${country}! Follow Mr. WorldWideWebster for more! #shorts #${country.toLowerCase()} #worldwide` };
        }
      }
    } catch {}

    // Ultimate fallback: original title
    this.logger.warn('All title generators failed — using original');
    return { title: originalTitle.substring(0, 100), description: `Amazing content from ${country}! Follow Mr. WorldWideWebster! #shorts #${country.toLowerCase()} #worldwide` };
  }

  /**
   * Burn subtitles using SRT file + ffmpeg subtitles filter.
   * Much more reliable than drawtext — handles all special chars correctly.
   * Positioned at bottom center for Shorts.
   */
  _burnSubtitles(videoPath, outputPath, text) {
    if (!text) return false;
    try {
      // Word-wrap to max 30 chars per line, max 3 lines for shorts
      const lines = [];
      let cur = '';
      for (const w of text.split(' ')) {
        if ((cur + ' ' + w).length > 30) { lines.push(cur); cur = w; }
        else { cur = cur ? cur + ' ' + w : w; }
      }
      if (cur) lines.push(cur);
      const displayLines = lines.slice(0, 3);

      // Write .srt file (no escaping issues — SRT is plain text)
      const srtPath = videoPath.replace('.mp4', '_caption.srt');
      const srtContent = `1\n00:00:00,000 --> 00:00:30,000\n${displayLines.join('\n')}\n`;
      fs.writeFileSync(srtPath, srtContent, 'utf8');

      // Use ffmpeg subtitles filter — reliable with all chars
      const cmd = `ffmpeg -y -i "${videoPath}" -vf "subtitles='${srtPath.replace(/'/g, "'\\\\''")}':force_style='FontName=Arial,FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=80,Alignment=2'" -c:v libx264 -preset ultrafast -crf 23 -c:a copy "${outputPath}" 2>/dev/null`;
      execSync(cmd, { timeout: 60000, maxBuffer: 50*1024*1024 });

      // Cleanup srt
      try { fs.unlinkSync(srtPath); } catch {}

      const exists = fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000;
      if (exists) this.logger.success(`Captions burned (SRT): ${path.basename(outputPath)}`);
      return exists;
    } catch (e) {
      this.logger.warn(`Caption burn failed: ${e.message.substring(0, 100)}`);
      return false;
    }
  }

  /**
   * Detect country from content. Uses guarantee hints:
   * - Title explicitly mentions country name
   * - Channel name (from yt-dlp) has country-specific words
   * - Audio language detected by whisper
   * Never rejects — always returns a country.
   */
  async _detectCountry(transcript, title, expected, sourceUrl) {
    let country = expected;
    let confidence = 50;
    let reasons = [];
    let channelName = '';

    // 1. Guarantee hint: title explicitly mentions another country?
    if (title) {
      for (const c of this.allC) {
        const lowerTitle = title.toLowerCase();
        const lowerCountry = c.toLowerCase();
        // Check if title contains country name (word boundary)
        if (new RegExp(`\\b${lowerCountry}\\b`, 'i').test(lowerTitle) ||
            title.includes('🇦🇺') || title.includes('🇧🇷') || title.includes('🇨🇳') ||
            title.includes('🇯🇵') || title.includes('🇰🇷') || title.includes('🇹🇭') ||
            title.includes('🇮🇳') || title.includes('🇩🇪') || title.includes('🇫🇷') ||
            title.includes('🇪🇬') || title.includes('🇲🇽') || title.includes('🇳🇬')) {
          const flagMap = {'🇦🇺':'Australia','🇧🇷':'Brazil','🇨🇳':'China','🇯🇵':'Japan','🇰🇷':'South Korea','🇹🇭':'Thailand','🇮🇳':'India','🇩🇪':'Germany','🇫🇷':'France','🇪🇬':'Egypt','🇲🇽':'Mexico','🇳🇬':'Nigeria'};
          const titleMatch = this.allC.find(cc => lowerTitle.includes(cc.toLowerCase())) ||
            Object.entries(flagMap).find(([f]) => title.includes(f))?.[1];
          if (titleMatch && titleMatch !== expected) {
            country = titleMatch;
            confidence = 85;
            reasons.push(`Title mentions "${titleMatch}"`);
            this.logger.info(`🇨🇮 Title HINT: "${titleMatch}" in title → correcting ${expected} → ${country}`);
            return { country, confidence, changed: true, reasons };
          }
        }
      }
    }

    // 2. Audio language from whisper
    if (transcript?.language) {
      for (const [c, langs] of Object.entries(this.countryLanguages)) {
        if (langs.includes(transcript.language)) {
          if (c !== expected) {
            country = c;
            confidence = 80;
            reasons.push(`Audio language "${transcript.language}" matches ${c}`);
          } else {
            confidence = 75;
            reasons.push(`Audio language "${transcript.language}" confirms ${c}`);
          }
          break;
        }
      }
    }

    // 3. yt-dlp metadata for channel name
    try {
      const url = sourceUrl || '';
      if (url) {
        const meta = execSync(`yt-dlp --dump-json --no-download "${url}" 2>/dev/null`, { timeout: 10000, encoding: 'utf8', maxBuffer: 1024*1024 }).trim();
        if (meta) {
          const p = JSON.parse(meta.split('\n')[0]);
          if (p.channel) channelName = p.channel;
          if (p.language) {
            for (const [c, langs] of Object.entries(this.countryLanguages)) {
              if (langs.includes(p.language)) {
                reasons.push(`YT meta lang "${p.language}" ${c}`);
                if (c !== country) { country = c; confidence = 75; }
                break;
              }
            }
          }
          // Check channel name for country hints
          if (channelName) {
            for (const c of this.allC) {
              if (channelName.toLowerCase().includes(c.toLowerCase()) && c !== country) {
                country = c;
                confidence = 80;
                reasons.push(`Channel "${channelName.substring(0, 30)}" suggests ${c}`);
                break;
              }
            }
          }
        }
      }
    } catch {}

    // 4. Hermes final cross-check
    if (this.hermes && this.hermes.isAvailable() && confidence < 70) {
      try {
        const input = `What country? Title: "${(title || '').substring(0, 150)}"\nAudio: ${transcript?.language || '?'}\nTranscript: "${(transcript?.text || '').substring(0, 200)}"\nExpected: ${expected}\nIf unsure say: ${expected}`;
        const c = await this.hermes.chat(input, { timeout: 15000 });
        if (c.success && c.output) {
          for (const cc of this.allC) {
            if (c.output.toUpperCase().includes(cc.toUpperCase()) && cc !== country) {
              country = cc;
              reasons.push(`Hermes suggests ${cc}`);
              confidence = 65;
              break;
            }
          }
        }
      } catch {}
    }

    const changed = country !== expected;
    this.logger.info(`🇨🇮 ${changed ? 'CORRECTED' : 'CONFIRMED'}: ${country} (was ${expected}) — ${confidence}%${reasons.length ? ` — ${reasons.join('; ')}` : ''}`);
    return { country, confidence, changed, reasons };
  }

  async initialize() {
    this.logger.header('Mr. WorldWideWebster — OpenRouter + Hermes Fallback + SRT Captions');
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
      if (this.hermes.isAvailable()) this.logger.success('Hermes CLI ready — fallback + country detection');
      else this.logger.info('Hermes CLI not available');
    } catch (e) { this.logger.info('Hermes CLI not installed'); }
    this.logger.success('Initialized');
  }

  _loadMemory() {
    if (!fs.existsSync(this.memoryPath)) fs.mkdirSync(this.memoryPath, { recursive: true });
    // Minimal memory: only what Hermes needs to evolve
    const defaultMem = {
      channelName: 'Mr. WorldWideWebster',
      totalVideosPosted: 0,
      countriesUsedThisWeek: [],
      hermesNotes: []  // Key insights only, kept short
    };
    const fp = path.join(this.memoryPath, 'channel-memory.json');
    if (fs.existsSync(fp)) {
      try {
        const existing = JSON.parse(fs.readFileSync(fp, 'utf8'));
        this.memory['channel-memory'] = {
          channelName: existing.channelName || defaultMem.channelName,
          totalVideosPosted: existing.totalVideosPosted || 0,
          countriesUsedThisWeek: existing.countriesUsedThisWeek || [],
          hermesNotes: (existing.hermesNotes || []).slice(-20)  // Keep last 20 notes max
        };
        // Remove old content-history
        const oldHistory = path.join(this.memoryPath, 'content-history.json');
        if (fs.existsSync(oldHistory)) fs.unlinkSync(oldHistory);
      } catch { this.memory['channel-memory'] = defaultMem; fs.writeFileSync(fp, JSON.stringify(defaultMem, null, 2)); }
    } else { this.memory['channel-memory'] = defaultMem; fs.writeFileSync(fp, JSON.stringify(defaultMem, null, 2)); }
  }
  _saveMemory() {
    const fp = path.join(this.memoryPath, 'channel-memory.json');
    fs.writeFileSync(fp, JSON.stringify(this.memory['channel-memory'], null, 2));
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
      // Wait 5 minutes for YouTube to process the upload
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
    // Try OpenRouter first
    try {
      const result = await this.ai.chat(`Translate to natural English. Return ONLY translation.`, text, { useCheapModel: true, temperature: 0.3 });
      if (result?.length > 3) return result.replace(/["']/g, '').trim().substring(0, 200);
    } catch {}
    // Fallback to Hermes
    this.logger.info('OpenRouter translation failed — Hermes translating...');
    const hResult = await this._hermesGenerate(`Translate this to English: "${text.substring(0, 300)}"`, 30000);
    if (hResult?.length > 3) return hResult.replace(/["']/g, '').trim().substring(0, 200);
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

    // Step 2-4: Search, rank, download (10 → top 3 → download)
    this.logger.info('Step 2: Searching...');
    const allUrls = await findUrlsForQueries(queries, 10);
    if (!allUrls.length) return { uploadedVideos: [], errors: ['No URLs'] };

    this.logger.info('Step 3: Ranking...');
    const { top3 } = await rankVideos(allUrls, queries[0] || '', this.ai);
    if (!top3.length) top3.push(...allUrls.slice(0, 3));

    // If we downloaded less than 3, we'll search more to always get 3
    let downloaded = await downloadVideos(top3, config.paths.clips);
    while (downloaded.length < 3 && downloaded.length < allUrls.length) {
      const extraRank = allUrls.filter(u => !top3.includes(u));
      if (!extraRank.length) break;
      const extra = await downloadVideos([extraRank[0]], config.paths.clips);
      downloaded.push(...extra);
    }

    this.logger.info(`Downloaded ${downloaded.length} videos (target: 3)`);

    // Step 5: Process each
    const { createShort } = require('./clip-editor');
    const shorts = [];

    for (let i = 0; i < downloaded.length; i++) {
      const v = downloaded[i];
      const originalCountry = countries[i] || countries[0] || 'Global';
      const originalTitle = v.sourceUrl?.title || v.title || `${originalCountry} Clip`;
      const sourceUrl = v.sourceUrl || '';
      this.logger.info(`=== Video ${i+1}: expected ${originalCountry} ===`);

      // Transcribe
      let transcript = null;
      try { transcript = await this._transcribeAudio(v.path); } catch {}

      // Profanity check
      if (transcript && this._hasProfanity(transcript.text)) {
        this.logger.warn(`PROFANITY — skip`);
        errors.push(`Profanity`);
        continue;
      }

      // Detect country (corrects, never rejects)
      const detected = await this._detectCountry(transcript, originalTitle, originalCountry, sourceUrl);
      const country = detected.country;

      // Generate voiceover (OpenRouter → Hermes fallback)
      const voiceoverText = await this._generateVoiceover(country, transcript?.text);

      // TTS for voiceover
      let voiceoverPath = null;
      try {
        const vDir = path.join(config.paths.assets, 'voiceovers');
        if (!fs.existsSync(vDir)) fs.mkdirSync(vDir, { recursive: true });
        const vPath = path.join(vDir, `vo_${Date.now()}_${i}.mp3`);
        execSync(`edge-tts --voice "en-US-JennyNeural" --text "${voiceoverText.replace(/"/g, '\\"')}" --write-media "${vPath}" 2>/dev/null`, { timeout: 30000 });
        if (fs.existsSync(vPath) && fs.statSync(vPath).size > 1000) voiceoverPath = vPath;
      } catch {}

      // Translate if non-English (OpenRouter → Hermes fallback)
      let englishSubtitle = null;
      if (transcript?.isNonEnglish) {
        englishSubtitle = await this._translateText(transcript.text);
      }

      // Skip non-English translation for music-only videos (whisper returns "nn" or very short text)
      if (transcript?.isNonEnglish && (!transcript.text || transcript.text.length < 10)) {
        this.logger.info('Music/no speech detected — no translation needed');
        englishSubtitle = null;
      }

      // Trim + scale + cap bitrate for low-res sources to avoid huge files
      let startTime = 5;
      try {
        const info = execSync(`ffprobe -i "${v.path}" -show_entries stream=start_time -of csv=p=0 2>/dev/null | head -1`, { timeout: 5000, encoding: 'utf8' }).trim();
        if (info && parseFloat(info) > 0 && parseFloat(info) < 30) startTime = parseFloat(info);
      } catch {}

      const outputPath = path.join(config.paths.clips, `short_${Date.now()}.mp4`);
      try {
        const result = await createShort(v.path, {
          startTime,
          duration: 30,
          countryText: country,
          voiceoverPath,
          outputPath
        });
        if (result) {
          let finalPath = result;
          if (englishSubtitle) {
            this.logger.info(`Adding English captions: "${englishSubtitle.substring(0, 80)}..."`);
            const subbedPath = result.replace('.mp4', '_captioned.mp4');
            if (this._burnSubtitles(result, subbedPath, englishSubtitle)) {
              try { fs.unlinkSync(result); } catch {}
              finalPath = subbedPath;
            }
          }
          shorts.push({ path: finalPath, country, voiceoverText, transcript: transcript?.text, originalTitle, url: '', hasCaptions: !!englishSubtitle });
        }
      } catch (e) { this.logger.warn(`Create short failed: ${e.message}`); }
    }

    this.logger.success(`Created ${shorts.length} Shorts`);

    // If not enough shorts, pad with what we have
    if (shorts.length === 0) {
      this.logger.warn('No shorts created — nothing to upload');
      return { uploadedVideos: [], errors: ['No shorts created'] };
    }

    // Step 6: Generate titles + upload + boost (with 5 min delay)
    for (let i = 0; i < shorts.length; i++) {
      const s = shorts[i];
      try {
        const targetTitle = await this._generateTitle(s.country, s.transcript, s.originalTitle);

        if (this._hasProfanity(targetTitle.title) || this._hasProfanity(targetTitle.description)) {
          targetTitle.title = `${s.country} Clip #shorts`;
          targetTitle.description = `Amazing content from ${s.country}. Follow Mr. WorldWideWebster!`;
        }

        const uploadResult = await this._uploadToYouTube({
          videoPath: s.path,
          title: targetTitle.title,
          description: targetTitle.description,
          tags: ['mr worldwidewebster', 'shorts', s.country.toLowerCase()]
        });

        if (uploadResult) {
          uploaded.push({ title: targetTitle.title, url: uploadResult.url, country: s.country, captions: s.hasCaptions });
          await this._boostVideo(uploadResult.url);  // Has 5 min sleep inside
        } else {
          errors.push(`Upload failed: ${targetTitle.title}`);
        }
      } catch (e) { errors.push(`Upload error: ${e.message}`); }
    }

    // Save minimal memory
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

    // Summary with URLs
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

    // Save Hermes insights to memory
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
