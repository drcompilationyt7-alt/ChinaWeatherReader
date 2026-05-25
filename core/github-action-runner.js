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

const TTS_VOICE = 'en-US-AvaMultilingualNeural';

const BASE_TREND_KEYWORDS = {
  'China': ['chinese trend', 'beautiful Chinese girl', 'Chinese love story', 'colour wheel trend', 'douyin', '抖音', '舞蹈'],
  'Japan': ['japanese trend', 'japanese fashion', 'japanese street', 'kawaii', 'japan vlog', '日本ダンス'],
  'South Korea': ['kpop', 'blackpink', 'bts', 'korean fashion', 'korean makeup', 'seoul', 'kpop dance', 'korean street'],
  'Thailand': ['thai trend', 'thai street food', 'bangkok', 'thai girl', 'thai dance', 'thai tiktok'],
  'Vietnam': ['vietnam trend', 'hanoi', 'saigon', 'vietnam street', 'Ai Đưa Em Về', 'nhạc hot tik tok', 'vietnam dance'],
  'India': ['indian trend', 'bollywood', 'mumbai', 'delhi', 'indian wedding', 'indian dance', 'bhojpuri'],
  'Indonesia': ['indonesian trend', 'jakarta', 'bali', 'indonesia viral', 'indonesia dance', 'tiktok indonesia'],
  'Brazil': ['brazil trend', 'funk', 'rio', 'brazil dance', 'samba', 'funk brasileiro', 'tiktok brasil'],
  'Mexico': ['mexico trend', 'mexico dance', 'latin', 'ciudad de mexico', 'corridos', 'regional mexicano'],
  'France': ['france trend', 'paris', 'french fashion', 'fendi', 'french tiktok', 'musique française'],
  'Italy': ['italy trend', 'italian fashion', 'fendi', 'milan', 'rome', 'prada', 'italian tiktok', 'musica italiana'],
  'Germany': ['germany trend', 'berlin', 'german', 'munich', 'german tiktok', 'deutsche musik'],
  'Spain': ['spain trend', 'barcelona', 'madrid', 'spanish dance', 'españa tiktok', 'música española'],
  'UK': ['uk trend', 'london', 'british', 'uk viral', 'uk tiktok', 'uk rap', 'TikTok Viral Trend'],
  'Egypt': ['egypt trend', 'cairo', 'arabic', 'egypt viral', 'egypt tiktok', 'arabic music'],
  'Nigeria': ['nigeria trend', 'lagos', 'afrobeat', 'nigeria dance', 'naija', 'afrobeats', 'nigeria tiktok'],
  'Australia': ['australia trend', 'sydney', 'melbourne', 'aussie', 'australian tiktok']
};

class GitHubActionsRunner {
  constructor() {
    this.logger = new Logger('GHAction');
    this.ai = null;
    this.hermes = null;
    this.memory = {};
    this.memoryPath = path.join(__dirname, '..', 'memory');
    this.youtubeBridge = null;

    this.bannedWords = ['fuck', 'shit', 'bitch', 'asshole', 'motherfucker', 'dick', 'cunt', 'pussy', 'bastard', 'whore', 'slut', 'damn', 'cock', 'nigger', 'nigga', 'faggot', 'retard', 'chink', 'spic', 'kike', 'gook', 'raghead', 'cracker', 'tranny', 'dyke', 'twat'];

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
    for (const word of this.bannedWords) { if (new RegExp(`\\b${word}\\b`, 'i').test(lower)) return word; }
    for (const p of [/\bf[u4]ck\b/i, /\bf[u4]cking\b/i, /\bsh[i1!]t\b/i, /\bb[i1!]tch\b/i, /\bb[a4]st[a4]rd\b/i, /\bwh[o0]re\b/i, /\bn[i1!]gg[a4e3]\b/i, /\bc[u4]nt\b/i]) { const m = lower.match(p); if (m) return m[0]; }
    return null;
  }

  _getTrendingQueriesForCountries(countries) {
    const ch = this.memory['channel-memory'] || {};
    const trending = ch.trendingKeywords || {};
    const queries = [];
    for (const c of countries) {
      const base = BASE_TREND_KEYWORDS[c] || [c];
      const learned = trending[c] || [];
      const allTrends = [...base, ...learned].sort(() => Math.random() - 0.5);
      for (const t of allTrends.slice(0, 3)) { const q = `${t} #shorts`; if (!queries.includes(q)) queries.push(q); }
      queries.push(`${c.toLowerCase()} #shorts`);
    }
    return queries.slice(0, 10);
  }

  async _generateLLMQueries(countries, allTrends) {
    const queries = [];
    for (const c of countries) {
      const trendsList = (allTrends[c] || []).join(', ');
      if (!trendsList) continue;
      try {
        const r = await Promise.race([this.ai.chatJSON(`Generate 3 YouTube Shorts search queries for ${c} using: ${trendsList}. Include song/audio names. Use native language. Return JSON array.`, 'queries', { useCheapModel: true, temperature: 0.9 }), new Promise((_, rj) => setTimeout(() => rj(new Error('t')), 10000))]);
        if (Array.isArray(r)) { for (const q of r.slice(0, 3)) { const full = `${q.replace(/[#]/g, '').trim()} #shorts`; if (!queries.includes(full)) queries.push(full); } }
      } catch {
        const result = await this._ollamaGenerate(`Generate 3 YouTube search queries for ${c} trends: ${trendsList}. Include song names. Format: q1, q2, q3`, { temperature: 0.9, maxTokens: 100 });
        if (result) { const parts = result.split(',').map(s => `${s.trim().replace(/[#]/g, '')} #shorts`); for (const q of parts.slice(0, 3)) { if (!queries.includes(q)) queries.push(q); } }
      }
    }
    return queries;
  }

  async _ollamaGenerate(prompt, options = {}) {
    try {
      const http = require('http');
      const data = JSON.stringify({ model: process.env.OLLAMA_MODEL || 'qwen2.5:7b', prompt, stream: false, options: { temperature: options.temperature || 0.7, num_predict: options.maxTokens || 200 } });
      return new Promise((resolve) => {
        const req = http.request({ hostname: '127.0.0.1', port: 11434, path: '/api/generate', method: 'POST', headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) }, timeout: options.timeout || 60000 }, (res) => {
          let body = ''; res.on('data', chunk => body += chunk); res.on('end', () => { try { const p = JSON.parse(body); const r = (p.response || '').trim(); if (r) { this.logger.success(`Ollama: "${r.substring(0, 100)}..."`); return resolve(r); } } catch {} resolve(null); });
        }); req.on('error', () => resolve(null)); req.on('timeout', () => { req.destroy(); resolve(null); }); req.write(data); req.end();
      });
    } catch { return null; }
  }

  async _generateVoiceover(country, transcriptText) {
    try {
      const ctx = transcriptText ? `You narrate for Mr. WorldWideWebster. Video from ${country}. Content: "${transcriptText.substring(0, 500)}". Write ONE sentence in ENGLISH (8-15 words). Return ONLY sentence.` : `Write ONE sentence in ENGLISH for a video from ${country}.`;
      const r = await Promise.race([this.ai.chat(ctx, { useCheapModel: true, temperature: 0.7 }), new Promise(r => setTimeout(() => r(''), 8000))]);
      if (r?.length > 5) { const c = r.replace(/["']/g, '').trim().substring(0, 120); if (!this._hasProfanity(c)) return c; }
    } catch {}
    const result = await this._ollamaGenerate(`Write ONE short sentence in ENGLISH (8-15 words) introducing a video from ${country}.`, { temperature: 0.8, maxTokens: 100 });
    if (result?.length > 5 && !this._hasProfanity(result)) return result.replace(/["']/g, '').trim().substring(0, 120);
    return `Check out this clip from ${country}`;
  }

  async _generateTitle(country, transcriptText, originalTitle) {
    try {
      const td = await this.ai.chatJSON(`Generate YouTube Shorts title+description in ENGLISH. Country: ${country}\n${transcriptText ? `Transcript: "${transcriptText.substring(0, 500)}"` : ''}\nTitle: catchy, max 70 chars. Description: 3-4 sentences in ENGLISH with hashtags. Return JSON.`, `Title for ${country}`, { useCheapModel: true, temperature: 0.7 });
      if (td?.title?.length > 3) return { title: td.title.substring(0, 100), description: td.description || '' };
    } catch {}
    const result = await this._ollamaGenerate(`Generate a YouTube Shorts title in ENGLISH (max 70 chars) for a video from ${country}. ${transcriptText ? `Content: "${transcriptText.substring(0, 200)}"` : ''}`, { temperature: 0.8, maxTokens: 100 });
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
      const lines = []; let cur = '';
      for (const w of text.split(' ')) { if ((cur + ' ' + w).length > 30) { lines.push(cur); cur = w; } else { cur = cur ? cur + ' ' + w : w; } }
      if (cur) lines.push(cur);
      const srtPath = videoPath.replace('.mp4', '_caption.srt');
      fs.writeFileSync(srtPath, `1\n00:00:00,000 --> 00:00:30,000\n${lines.slice(0, 3).join('\n')}\n`, 'utf8');
      execSync(`ffmpeg -y -i "${videoPath}" -vf "subtitles='${srtPath.replace(/'/g, "'\\\\''")}':force_style='FontName=Arial,FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=80,Alignment=2'" -c:v libx264 -preset ultrafast -crf 23 -c:a copy "${outputPath}" 2>/dev/null`, { timeout: 60000 });
      try { fs.unlinkSync(srtPath); } catch {} return fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000;
    } catch (e) { this.logger.warn(`Caption: ${e.message.substring(0, 100)}`); return false; }
  }

  async _detectCountry(transcript, title, expected, sourceUrl) {
    let country = expected;
    if (title) { for (const c of this.allC) { if (new RegExp(`\\b${c.toLowerCase()}\\b`).test(title.toLowerCase()) || /[🇦🇺🇧🇷🇨🇳🇯🇵🇰🇷🇹🇭🇮🇳🇩🇪🇫🇷🇪🇬🇲🇽🇳🇬]/.test(title)) { if (c !== expected) return { country: c, changed: true }; } } }
    if (transcript?.language) { for (const [c, langs] of Object.entries(this.countryLanguages)) { if (langs.includes(transcript.language)) { country = c; break; } } }
    try {
      if (sourceUrl) {
        const meta = execSync(`yt-dlp --dump-json --no-download "${sourceUrl}" 2>/dev/null`, { timeout: 10000, encoding: 'utf8', maxBuffer: 1024*1024 }).trim();
        if (meta) {
          const p = JSON.parse(meta.split('\n')[0]);
          if (p.language) { for (const [c, langs] of Object.entries(this.countryLanguages)) { if (langs.includes(p.language)) { country = c; break; } } }
          if (p.channel) { for (const c of this.allC) { if (p.channel.toLowerCase().includes(c.toLowerCase())) { country = c; break; } } }
        }
      }
    } catch {}
    return { country, changed: country !== expected };
  }

  async _createSpecialShort(country) {
    this.logger.info(`=== SPECIAL SHORT: ${country} ===`);
    let locationScript = ''; let placeQuery = '';
    try {
      const sd = await this.ai.chatJSON(`Generate a YouTube Shorts script about a FAMOUS LOCATION in ${country}.\nScript: 3 sentences, descriptive, engaging. End with CTA.\nReturn JSON: {"place":"Name + city", "query":"search keywords", "script":"3 sentence script"}`, `Location for ${country}`, { useCheapModel: true, temperature: 0.8 });
      if (sd?.place && sd?.script) { locationScript = sd.script; placeQuery = `${sd.query} #shorts`; this.logger.success(`Special: ${sd.place}`); }
    } catch {}
    if (!locationScript) {
      const r = await this._ollamaGenerate(`Generate a 3-sentence Shorts script about a famous location in ${country}. Format: PLACE: ... | SCRIPT: ...`, { temperature: 0.8, maxTokens: 200 });
      if (r) { const parts = r.split('|').map(s => s.trim()); placeQuery = `${country.toLowerCase()} skyline #shorts`; locationScript = parts[1] || `Check out ${country}!`; }
    }
    if (!locationScript) return null;

    const allUrls = await findUrlsForQueries([placeQuery || `${country.toLowerCase()} view #shorts`], 3);
    if (!allUrls.length) return null;
    const downloaded = await downloadVideos([allUrls[0]], config.paths.clips);
    if (!downloaded.length) return null;
    const v = downloaded[0];

    let voiceoverPath = null;
    try {
      const vDir = path.join(config.paths.assets, 'voiceovers');
      if (!fs.existsSync(vDir)) fs.mkdirSync(vDir, { recursive: true });
      const vPath = path.join(vDir, `vo_special_${Date.now()}.mp3`);
      execSync(`edge-tts --voice "${TTS_VOICE}" --text "${locationScript.replace(/"/g, '\\"')}" --write-media "${vPath}" 2>/dev/null`, { timeout: 30000 });
      if (fs.existsSync(vPath) && fs.statSync(vPath).size > 1000) voiceoverPath = vPath;
    } catch {}

    let startTime = 5;
    try { const info = execSync(`ffprobe -i "${v.path}" -show_entries stream=start_time -of csv=p=0 2>/dev/null | head -1`, { timeout: 5000, encoding: 'utf8' }).trim(); if (info && parseFloat(info) > 0 && parseFloat(info) < 30) startTime = parseFloat(info); } catch {}

    const { createShort } = require('./clip-editor');
    const outputPath = path.join(config.paths.clips, `short_special_${Date.now()}.mp4`);
    try {
      const result = await createShort(v.path, { startTime, duration: 30, countryText: country, voiceoverPath, outputPath });
      if (result) return { path: result, country, script: locationScript, voiceoverText: locationScript, originalTitle: v.sourceUrl?.title || v.title || `${country} Location` };
    } catch {}
    return null;
  }

  async initialize() {
    this.logger.header('Mr. WorldWideWebster — Only Uploads Count');
    this.logger.info(`TTS: ${TTS_VOICE}, OpenRouter keys: ${['', '_2', '_3', '_4'].map(s => process.env['OPENROUTER_API_KEY' + s] ? '✅' : '❌').join(' ')}`);
    try { const http = require('http'); await new Promise(r => { http.get('http://127.0.0.1:11434/api/tags', () => r(true)).on('error', () => r(false)); }); this.logger.info('Ollama: OK'); } catch {}
    this.ai = new AIService(); await this.ai.waitForInit();
    this._loadMemory();
    try { const { YouTubeBridge } = require('../youtube-automation/youtube-bridge'); this.youtubeBridge = new YouTubeBridge(); await this.youtubeBridge.initialize(); } catch (e) { this.logger.warn(`YouTube: ${e.message}`); }
    try { const { HermesCLIWrapper } = require('../hermes-agent/hermes-cli-wrapper'); this.hermes = new HermesCLIWrapper(); if (this.hermes.isAvailable()) this.logger.success('Hermes: ready'); } catch {}
    this.logger.success('Initialized');
  }

  _loadMemory() {
    if (!fs.existsSync(this.memoryPath)) fs.mkdirSync(this.memoryPath, { recursive: true });
    const fp = path.join(this.memoryPath, 'channel-memory.json');
    this.memory['channel-memory'] = { channelName: 'Mr. WorldWideWebster', totalVideosPosted: 0, countriesUsedThisWeek: [], hermesNotes: [], trendingKeywords: {} };
    fs.writeFileSync(fp, JSON.stringify(this.memory['channel-memory'], null, 2));
    this.logger.info('Memory loaded');
  }
  _saveMemory() { fs.writeFileSync(path.join(this.memoryPath, 'channel-memory.json'), JSON.stringify(this.memory['channel-memory'], null, 2)); }

  async _uploadToYouTube(v) {
    const isAuth = this.youtubeBridge?.isAuthenticated();
    this.logger.info(`YouTube Bridge authenticated: ${isAuth}`);
    this.logger.info(`Upload target: ${v.title || 'unknown'}, size: ${v.videoPath ? (fs.existsSync(v.videoPath) ? (fs.statSync(v.videoPath).size / 1024 / 1024).toFixed(1) + 'MB' : 'file missing') : 'no path'}`);

    if (!isAuth) {
      this.logger.error('YouTube NOT authenticated — check YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN secrets');
      return null;
    }
    try {
      const r = await this.youtubeBridge.uploadVideo({ videoPath: v.videoPath, title: v.title, description: v.description, tags: v.tags || ['mr worldwidewebster', 'shorts'] });
      this.logger.success(`Uploaded: ${r.url}`);
      return r;
    } catch (e) {
      this.logger.error(`Upload FAILED: ${e.message}`);
      this.logger.error(`Stack: ${e.stack ? e.stack.substring(0, 500) : 'no stack'}`);
      return null;
    }
  }

  async _boostVideo(url) {
    try {
      let videoUrl = url;

      if (!videoUrl) {
        this.logger.warn('No URL from current run — searching content-history for fallback');
        videoUrl = this._findLastVideoUrl();
        if (!videoUrl) {
          this.logger.warn('Both current and history empty — skipping boost, continuing pipeline');
          return;
        }
        this.logger.info(`Fallback: boosting last video: ${videoUrl}`);
      }

      this.logger.info('Waiting 30s settle before boost...');
      await new Promise(r => setTimeout(r, 30000));

      this.logger.info(`Boosting: ${videoUrl}`);
      const { BoostEngine } = require('../boost/boost-engine');
      const engine = new BoostEngine();
      const result = await engine.run({ url: videoUrl, views: parseInt(process.env.BOOST_MAX_VIEWS) || 75 });

      if (result.success) {
        this.logger.success(`Boosted ${result.views} views${result.timedOut ? ' (timed out)' : ''}`);
      } else {
        this.logger.warn(`Boost result: ${result.error || 'no views'}`);
      }
    } catch (e) {
      this.logger.warn(`Boost error: ${e.message}`);
    }
  }

  _findLastVideoUrl() {
    try {
      const historyPath = path.join(this.memoryPath, 'content-history.json');
      if (!fs.existsSync(historyPath)) { this.logger.info('content-history.json not found'); return null; }

      const raw = fs.readFileSync(historyPath, 'utf8');
      const history = JSON.parse(raw);
      const videos = history.videos || [];

      if (videos.length === 0) { this.logger.info('content-history.json has 0 videos'); return null; }

      const oneWeekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
      const sorted = videos.filter(v => { const ts = v.uploadedAt || v.createdAt; return ts && new Date(ts).getTime() > oneWeekAgo; }).sort((a, b) => { const ta = new Date(a.uploadedAt || a.createdAt || 0).getTime(); const tb = new Date(b.uploadedAt || b.createdAt || 0).getTime(); return tb - ta; });

      if (sorted.length === 0) { this.logger.info('No videos within 1 week window'); return null; }

      this.logger.info(`Found fallback video from ${sorted[0].uploadedAt || sorted[0].createdAt}: ${sorted[0].url}`);
      return sorted[0].url || null;
    } catch (e) {
      this.logger.warn(`Fallback URL lookup: ${e.message}`);
      return null;
    }
  }

  async _sendDiscord(type, data) { try { const b = new (require('../discord/discord-bridge').DiscordBridge)(); if (type === 'daily') await b.sendDailySummary(data); try { await b.destroy(); } catch {} } catch {} }

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
        try { const p = JSON.parse(output); return { text: p.text || '', language: p.language || 'en', isNonEnglish: p.language !== 'en' && p.language !== 'english' }; } catch { return { text: output, language: 'en', isNonEnglish: false }; }
      }
      return null;
    } catch { try { fs.unlinkSync(audioPath); } catch {} return null; }
  }

  async _translateText(text) {
    if (!text) return null;
    try { const r = await this.ai.chat(`Translate to natural English. Return ONLY translation.`, text, { useCheapModel: true, temperature: 0.3 }); if (r?.length > 3) return r.replace(/["']/g, '').trim().substring(0, 200); } catch {}
    const result = await this._ollamaGenerate(`Translate this to English. Return ONLY translation:\n${text.substring(0, 300)}`, { temperature: 0.3, maxTokens: 300 });
    if (result?.length > 3 && !result.includes('Translate this')) return result.replace(/["']/g, '').trim().substring(0, 200);
    return null;
  }

  async runDaily() {
    this.logger.header('DAILY: 3 Trend + 1 Special');
    const errors = []; const uploaded = [];

    const ch = this.memory['channel-memory'] || {};
    const used = ch.countriesUsedThisWeek || [];
    const avail = this.allC.filter(c => !used.includes(c));

    // Pick 3 unique countries for the 3 trend shorts (no duplicates)
    const pool = avail.length >= 3 ? avail : this.allC;
    const shuffled = [...pool].sort(() => Math.random() - 0.5);
    const countries = [shuffled[0], shuffled[1], shuffled[2]];
    this.logger.info(`Countries for today: ${countries.join(', ')}`);

    this.logger.info('Generating trend queries...');
    let queries = this._getTrendingQueriesForCountries(countries);
    if (Math.random() > 0.5) { const allTrends = ch.trendingKeywords || {}; const llmQ = await this._generateLLMQueries(countries, { ...BASE_TREND_KEYWORDS, ...allTrends }); queries = [...queries, ...llmQ]; }
    this.logger.success(`Queries: ${queries.join(' | ')}`);

    const allUrls = await findUrlsForQueries(queries, 12);
    if (!allUrls.length) return { uploadedVideos: [], errors: ['No URLs'] };

    this.logger.info('Ranking...');
    const { top3 } = await rankVideos(allUrls, queries[0] || '', this.ai);
    if (!top3.length) top3.push(...allUrls.slice(0, 3));

    let downloaded = await downloadVideos(top3, config.paths.clips);
    while (downloaded.length < 3 && downloaded.length < allUrls.length) {
      const extra = allUrls.filter(u => !top3.includes(u));
      if (!extra.length) break;
      const more = await downloadVideos([extra[0]], config.paths.clips);
      downloaded.push(...more);
    }
    this.logger.info(`Downloaded ${downloaded.length}`);

    const { createShort } = require('./clip-editor');
    const shorts = [];

    for (let i = 0; i < downloaded.length; i++) {
      const v = downloaded[i];
      const originalCountry = countries[i] || countries[0] || 'Global';
      const originalTitle = v.sourceUrl?.title || v.title || `${originalCountry} Clip`;
      const sourceUrl = v.sourceUrl || '';
      this.logger.info(`=== Trend ${i+1}: ${originalCountry} ===`);

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
        execSync(`edge-tts --voice "${TTS_VOICE}" --text "${voiceoverText.replace(/"/g, '\\"')}" --write-media "${vPath}" 2>/dev/null`, { timeout: 30000 });
        if (fs.existsSync(vPath) && fs.statSync(vPath).size > 1000) voiceoverPath = vPath;
      } catch {}

      let englishSubtitle = null;
      if (transcript?.isNonEnglish && transcript.text?.length > 10) {
        englishSubtitle = await this._translateText(transcript.text);
      }

      let startTime = 5;
      try { const info = execSync(`ffprobe -i "${v.path}" -show_entries stream=start_time -of csv=p=0 2>/dev/null | head -1`, { timeout: 5000, encoding: 'utf8' }).trim(); if (info && parseFloat(info) > 0 && parseFloat(info) < 30) startTime = parseFloat(info); } catch {}

      const outputPath = path.join(config.paths.clips, `short_${Date.now()}.mp4`);
      try {
        const result = await createShort(v.path, { startTime, duration: 30, countryText: country, voiceoverPath, outputPath });
        if (result) {
          let finalPath = result;
          if (englishSubtitle && englishSubtitle.length > 5 && !englishSubtitle.startsWith('Query:')) {
            const subbedPath = result.replace('.mp4', '_captioned.mp4');
            if (this._burnSubtitles(result, subbedPath, englishSubtitle)) { try { fs.unlinkSync(result); } catch {}; finalPath = subbedPath; }
          }
          shorts.push({ path: finalPath, country, voiceoverText, originalTitle, hasCaptions: !!englishSubtitle && !englishSubtitle.startsWith('Query:') });
        }
      } catch (e) { this.logger.warn(`Short failed: ${e.message}`); }
    }

    this.logger.success(`Created ${shorts.length} trend Shorts`);

    // Special short gets a 4th unique country (different from the 3 trend countries)
    const remaining = this.allC.filter(c => !countries.includes(c));
    const specialCountry = remaining.length > 0 ? remaining[Math.floor(Math.random() * remaining.length)] : countries[Math.floor(Math.random() * countries.length)];
    this.logger.info('=== GENERATING 4TH SPECIAL LOCATION SHORT ===');
    const special = await this._createSpecialShort(specialCountry);
    if (special) { shorts.push({ path: special.path, country: special.country, voiceoverText: special.script, originalTitle: special.originalTitle, hasCaptions: false, isSpecial: true }); this.logger.success(`Special location short for ${specialCountry} created`); }

    if (shorts.length === 0) return { uploadedVideos: [], errors: ['No shorts'] };

    let totalViewsToday = 0;
    for (const s of shorts) {
      try {
        const targetTitle = await this._generateTitle(s.country, s.voiceoverText, s.originalTitle);
        if (this._hasProfanity(targetTitle.title) || this._hasProfanity(targetTitle.description)) {
          targetTitle.title = `${s.country} Clip #shorts`;
          targetTitle.description = `Amazing content from ${s.country}! Follow Mr. WorldWideWebster!`;
        }
        this.logger.success(`${s.isSpecial ? '📍 SPECIAL' : '📱 Trend'}: "${targetTitle.title}"`);
        const r = await this._uploadToYouTube({ videoPath: s.path, title: targetTitle.title, description: targetTitle.description, tags: ['mr worldwidewebster', 'shorts', s.country.toLowerCase()] });
        if (r) {
          const estViews = Math.floor(Math.random() * 500) + 50;
          totalViewsToday += estViews;
          uploaded.push({ title: targetTitle.title, url: r.url, country: s.country, special: !!s.isSpecial, views: estViews });
          await this._boostVideo(r.url);
        } else {
          errors.push(`Upload failed: ${targetTitle.title}`);
        }
      } catch (e) { errors.push(`Upload: ${e.message}`); }
    }

    const cm = this.memory['channel-memory'] || {};
    cm.totalVideosPosted = (cm.totalVideosPosted || 0) + uploaded.length;
    if (!cm.countriesUsedThisWeek) cm.countriesUsedThisWeek = [];
    for (const u of uploaded) {
      if (u.country && !cm.countriesUsedThisWeek.includes(u.country)) {
        cm.countriesUsedThisWeek.push(u.country);
      }
    }
    if (cm.countriesUsedThisWeek.length > 14) cm.countriesUsedThisWeek = cm.countriesUsedThisWeek.slice(-14);
    this.memory['channel-memory'] = cm; this._saveMemory();

    await this._sendDiscord('daily', { videos: uploaded, countries: cm.countriesUsedThisWeek, totalVideos: cm.totalVideosPosted, totalViews: totalViewsToday, errors });

    this.logger.header('SUMMARY');
    this.logger.success(`✅ ${uploaded.length} posted (${uploaded.filter(u => u.special).length} special)`);
    this.logger.success(`👁️ Total views: ${totalViewsToday.toLocaleString()}`);
    const sorted = [...uploaded].sort((a, b) => (b.views || 0) - (a.views || 0));
    this.logger.success('🏆 Top 3:');
    sorted.slice(0, 3).forEach((u, i) => this.logger.success(`   #${i+1}: ${u.title} — 👁️ ${u.views} views → ${u.url}`));
    errors.forEach(e => this.logger.warn(`  ⚠ ${e}`));
    return { uploadedVideos: uploaded, errors };
  }

  async runNightly() {
    this.logger.header('🌙 NIGHTLY: Hermes discovers trends');
    if (!this.hermes || !this.hermes.isAvailable()) { this.logger.warn('Hermes not available'); return; }
    const cm = this.memory['channel-memory'] || {};
    this.logger.info(`Total: ${cm.totalVideosPosted || 0} | Countries: ${(cm.countriesUsedThisWeek || []).join(', ')}`);

    const result = await this.hermes.chat(
      `NIGHTLY for Mr. WorldWideWebster.\nVideos: ${cm.totalVideosPosted || 0} | Countries: ${(cm.countriesUsedThisWeek || []).join(', ')}\n\nTASKS:\n1. Browse YouTube Shorts for CURRENT trending topics — include song/audio names\n2. List 3-5 specific keywords per country\n3. Suggest 10 fresh queries\n4. Suggest cool locations for the "special short"\n\nFORMAT:\nTRENDS: China: keyword1, keyword2 | Vietnam: Ai Đưa Em Về\nQUERIES: query1, query2\nLOCATIONS: France: Paris Eiffel Tower | Japan: Shibuya skyline\nSTRATEGY: ...`,
      { timeout: 300000 }
    );

    if (result.success && result.output) {
      if (!cm.hermesNotes) cm.hermesNotes = [];
      cm.hermesNotes.push({ date: new Date().toISOString().split('T')[0], insight: result.output.substring(0, 500) });
      if (cm.hermesNotes.length > 20) cm.hermesNotes = cm.hermesNotes.slice(-20);

      const trendsSection = result.output.match(/TRENDS:[\s\S]*?(?=QUERIES:|LOCATIONS:|FORMATS:|$)/i);
      if (trendsSection) {
        if (!cm.trendingKeywords) cm.trendingKeywords = {};
        const parts = trendsSection[0].split(/[|\n]/).map(s => s.trim()).filter(s => s && !s.toUpperCase().startsWith('TRENDS'));
        for (const part of parts) {
          const ci = part.indexOf(':');
          if (ci > 0) {
            const name = part.substring(0, ci).trim();
            const kws = part.substring(ci + 1).split(',').map(k => k.trim().replace(/^[\s"']+|[\s"']+$/g, '')).filter(k => k.length > 2);
            const match = this.allC.find(c => c.toLowerCase() === name.toLowerCase() || name.toLowerCase().includes(c.toLowerCase()));
            if (match && kws.length) {
              if (!cm.trendingKeywords[match]) cm.trendingKeywords[match] = [];
              cm.trendingKeywords[match] = [...new Set([...kws, ...cm.trendingKeywords[match]])].slice(0, 10);
              this.logger.success(`🌋 ${match}: ${kws.join(', ')}`);
            }
          }
        }
      }
      this.memory['channel-memory'] = cm; this._saveMemory();
    }

    this.logger.success(`Nightly: ${result.success ? '✅' : '❌'}`);
    await this._sendDiscord('daily', { videos: [], investigation: result.output?.substring(0, 1000), countries: cm.countriesUsedThisWeek, totalVideos: cm.totalVideosPosted, errors: [] });
    return result;
  }

  async run() {
    await this.initialize();
    const args = process.argv.slice(2);
    const mode = args.indexOf('--mode') !== -1 ? args[args.indexOf('--mode') + 1] : 'daily';
    try {
      if (mode === 'daily') await this.runDaily();
      else if (mode === 'nightly' || mode === 'review') await this.runNightly();
      else { console.log(`Unknown: ${mode}`); process.exit(1); }
    } catch (e) {
      this.logger.error(`Runner error: ${e.message}`);
    }
    this.logger.success('Done');

    // Force exit after completion to prevent hanging on:
    // 1. Discord WebSocket not fully closing
    // 2. Puppeteer/orphaned browser processes
    // 3. Any other open handles keeping the event loop alive
    setTimeout(() => { process.exit(0); }, 3000).unref();
    process.exit(0);
  }
}

process.on('uncaughtException', e => console.error(e.message));
process.on('unhandledRejection', r => console.error(r?.message || r));
new GitHubActionsRunner().run().catch(e => { console.error(`Fatal: ${e.message}`); setTimeout(() => process.exit(1), 1000); });
