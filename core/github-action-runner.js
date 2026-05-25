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

    // Native language keywords for each country
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

  async _verifyCountryMatch(videoUrl, expectedCountry, transcript, title, sourceUrl) {
    let confidence = 50;
    let reasons = [];
    let channelName = '';

    if (transcript?.language) {
      const expectedLangs = this.countryLanguages[expectedCountry] || [];
      if (expectedLangs.length > 0) {
        if (expectedLangs.includes(transcript.language)) {
          confidence += 25; reasons.push(`Audio: ${transcript.language} matches ${expectedCountry}`);
        } else if (transcript.language === 'en' && !expectedLangs.includes('en')) {
          confidence -= 15; reasons.push(`Audio is English but expected non-English`);
        }
      }
    }

    try {
      const url = sourceUrl || videoUrl;
      if (url) {
        const meta = execSync(`yt-dlp --dump-json --no-download "${url}" 2>/dev/null`, { timeout: 15000, encoding: 'utf8', maxBuffer: 2*1024*1024 }).trim();
        if (meta) {
          const p = JSON.parse(meta.split('\n')[0]);
          if (p.language) {
            const el = this.countryLanguages[expectedCountry] || [];
            if (el.includes(p.language)) { confidence += 15; reasons.push(`YT lang: ${p.language}`); }
            else { confidence -= 10; reasons.push(`YT lang ${p.language} != ${expectedCountry}`); }
          }
          if (p.channel) { channelName = p.channel; reasons.push(`Channel: ${p.channel.substring(0, 60)}`); }
          if (p.channel_follower_count) reasons.push(`${p.channel_follower_count} subs`);
        }
      }
    } catch {}

    if (this.hermes && this.hermes.isAvailable()) {
      try {
        const input = `Does this video match ${expectedCountry}? Answer YES or NO.\nTitle: "${(title || '').substring(0, 200)}"\nChannel: ${channelName || '?'}\nAudio: ${transcript?.language || '?'}\nTranscript: "${(transcript?.text || '').substring(0, 250)}"`;
        const c = await this.hermes.chat(input, { timeout: 20000 });
        if (c.success && c.output) {
          if (c.output.toUpperCase().includes('YES')) confidence += 20;
          else if (c.output.toUpperCase().includes('NO')) confidence -= 25;
        }
      } catch {}
    }

    const match = confidence >= 50;
    this.logger.info(`Country ${expectedCountry}: ${match ? '✅' : '⛔'} ${confidence}% — ${reasons.join('; ')}`);
    return { match, confidence, reasons };
  }

  _burnSubtitles(videoPath, outputPath, text) {
    if (!text) return false;
    try {
      const lines = [];
      let cur = '';
      for (const w of text.split(' ')) {
        if ((cur + ' ' + w).length > 35) { lines.push(cur); cur = w; }
        else { cur = cur ? cur + ' ' + w : w; }
      }
      if (cur) lines.push(cur);
      const displayText = lines.slice(0, 3).join('\\n');
      const escaped = displayText.replace(/\\/g, '\\\\').replace(/'/g, "'\\\\''").replace(/:/g, '\\\\:').replace(/,/g, '\\,');
      const cmd = `ffmpeg -y -i "${videoPath}" -vf "drawtext=text='${escaped}':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=10:x=(w-text_w)/2:y=h-text_h-100" -c:v libx264 -preset ultrafast -crf 23 -c:a copy "${outputPath}" 2>/dev/null`;
      execSync(cmd, { timeout: 60000, maxBuffer: 50*1024*1024 });
      const exists = fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000;
      if (exists) this.logger.success(`Subtitles burned: ${path.basename(outputPath)}`);
      return exists;
    } catch (e) { this.logger.warn(`Subtitle burn failed: ${e.message.substring(0, 100)}`); return false; }
  }

  async initialize() {
    this.logger.header('Mr. WorldWideWebster — OpenRouter Works, Hermes Checks');
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
      if (this.hermes.isAvailable()) this.logger.success('Hermes CLI ready');
      else this.logger.info('Hermes CLI not available');
    } catch (e) { this.logger.info('Hermes CLI not installed'); }
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
        try { const p = JSON.parse(output); return { text: p.text || '', language: p.language || 'en', isNonEnglish: p.language !== 'en' && p.language !== 'english' }; }
        catch { return { text: output, language: 'en', isNonEnglish: false }; }
      }
      return null;
    } catch { try { fs.unlinkSync(audioPath); } catch {} return null; }
  }

  async runDaily() {
    this.logger.header('DAILY: OpenRouter works, Hermes checks');
    const errors = [];
    const uploaded = [];

    // Step 1: OpenRouter generates queries — mix of English + native language
    this.logger.info('Step 1: OpenRouter generating bilingual queries...');
    const ch = this.memory['channel-memory'] || {};
    const used = ch.countriesUsedThisWeek || [];
    const allC = ['Nigeria','Germany','Brazil','Mexico','UK','Egypt','Italy','Spain','France','Australia','Japan','South Korea','China','Thailand','Vietnam','India','Indonesia'];
    const avail = allC.filter(c => !used.includes(c));
    const countries = [
      avail.length ? avail[Math.floor(Math.random()*avail.length)] : allC[Math.floor(Math.random()*allC.length)],
      allC[Math.floor(Math.random()*allC.length)],
      allC[Math.floor(Math.random()*allC.length)]
    ];

    // Build native keywords for each selected country
    const nativeKw = countries.map(c => this.nativeKeywords[c] || c);
    const fallbackQ = countries.map((c, i) => {
      const isAsian = ['Japan','South Korea','China','Thailand','Vietnam','India','Indonesia'].includes(c);
      // Mix: one native language query + one English query per country
      const en = isAsian ? `${c} douyin #shorts` : `${c} #shorts`;
      return [nativeKw[i], en];
    }).flat().slice(0, 5);

    let queries = fallbackQ;
    try {
      const prompt = `Generate 5 YouTube search queries for SHORT videos. Mix of English AND native language queries.

Countries: ${countries[0]}, ${countries[1]}, ${countries[2]}

For non-English speaking countries, include queries in their NATIVE language using these keywords:
${countries.map((c, i) => `- ${c}: ${nativeKw[i]}`).join('\n')}

Example for China: "舞蹈 #shorts" (Chinese), "China douyin dance #shorts" (English)
Example for Japan: "面白い動画 #shorts" (Japanese), "Japan funny #shorts" (English)

Return array of 5 strings (mix of native and English).`;
      const r = await Promise.race([this.ai.chatJSON(prompt, 'queries', { useCheapModel: true, temperature: 0.8 }), new Promise((_, rj) => setTimeout(() => rj(new Error('timeout')), 12000))]);
      queries = Array.isArray(r) ? r.slice(0, 5) : fallbackQ;
    } catch {}
    this.logger.success(`Queries (mixed lang): ${queries.join(' | ')}`);

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

      let transcript = null;
      try { transcript = await this._transcribeAudio(v.path); } catch {}

      if (transcript) {
        const badWord = this._hasProfanity(transcript.text);
        if (badWord) { this.logger.warn(`PROFANITY "${badWord}" — skip`); errors.push(`Profanity ${country}`); continue; }
      }

      const countryMatch = await this._verifyCountryMatch(sourceUrl, country, transcript, originalTitle, sourceUrl);
      if (!countryMatch.match) {
        this.logger.warn(`Country mismatch for ${country} — skip`);
        errors.push(`Country mismatch ${country}: ${countryMatch.reasons.join('; ')}`);
        continue;
      }

      const hermesCtx = `Country: ${country}. Title: "${originalTitle.substring(0, 100)}". ${transcript?.text ? `Transcript: "${transcript.text.substring(0, 200)}".` : ''} Audio: ${transcript?.language || '?'}`;
      const check = await this._hermesCheck('video', originalTitle, hermesCtx);
      if (!check.passed) { this.logger.warn(`Hermes rejected: ${check.feedback}`); errors.push(`Hermes rejected ${country}`); continue; }

      // Voiceover
      let voiceoverText = '';
      try {
        const ctx = transcript?.text
          ? `You narrate for Mr. WorldWideWebster. Video from ${country}. Content: "${transcript.text.substring(0, 500)}". Write ONE sentence (8-15 words). Return ONLY sentence.`
          : `Write ONE sentence for a video from ${country}.`;
        voiceoverText = await Promise.race([this.ai.chat(ctx, { useCheapModel: true, temperature: 0.7 }), new Promise(r => setTimeout(() => r(''), 8000))]);
        if (!voiceoverText?.length > 5) voiceoverText = `Check out this clip from ${country}`;
        voiceoverText = voiceoverText.replace(/["']/g, '').trim().substring(0, 120);
        if (this._hasProfanity(voiceoverText)) voiceoverText = `Check out this clip from ${country}`;
      } catch { voiceoverText = `Check out this clip from ${country}`; }

      const voCheck = await this._hermesCheck('voiceover text', voiceoverText, `Country: ${country}`);
      if (!voCheck.passed) {
        try {
          voiceoverText = await Promise.race([this.ai.chat(`You narrate for Mr. WorldWideWebster. Video from ${country}. Write ONE exciting sentence (8-15 words).`, { useCheapModel: true, temperature: 0.8 }), new Promise(r => setTimeout(() => r(''), 8000))]);
          voiceoverText = (voiceoverText || `Check out this clip from ${country}`).replace(/["']/g, '').trim().substring(0, 120);
        } catch { voiceoverText = `Check out this clip from ${country}`; }
      }

      let voiceoverPath = null;
      try {
        const vDir = path.join(config.paths.assets, 'voiceovers');
        if (!fs.existsSync(vDir)) fs.mkdirSync(vDir, { recursive: true });
        const vPath = path.join(vDir, `vo_${Date.now()}_${i}.mp3`);
        execSync(`edge-tts --voice "en-US-JennyNeural" --text "${voiceoverText.replace(/"/g, '\\"')}" --write-media "${vPath}" 2>/dev/null`, { timeout: 30000 });
        if (fs.existsSync(vPath) && fs.statSync(vPath).size > 1000) voiceoverPath = vPath;
      } catch {}

      // Translate if non-English
      let englishSubtitle = null;
      if (transcript?.isNonEnglish) {
        try {
          englishSubtitle = await this.ai.chat(`Translate to natural English. Return ONLY translation.`, transcript.text, { useCheapModel: true, temperature: 0.3 });
          if (englishSubtitle?.length > 3) englishSubtitle = englishSubtitle.replace(/["']/g, '').trim().substring(0, 200);
        } catch {}
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
          if (englishSubtitle) {
            const subbedPath = result.replace('.mp4', '_captioned.mp4');
            if (this._burnSubtitles(result, subbedPath, englishSubtitle)) {
              try { fs.unlinkSync(result); } catch {}
              finalPath = subbedPath;
            }
          }
          shorts.push({ path: finalPath, country, voiceoverText, transcript: transcript?.text, originalTitle, sourceUrl, hasCaptions: !!englishSubtitle });
        }
      } catch (e) { this.logger.warn(`Create short failed: ${e.message}`); }
    }

    this.logger.success(`Created ${shorts.length} Shorts`);

    // Step 6: Generate titles+descriptions
    for (const s of shorts) {
      try {
        const country = s.country;
        const originalTitle = s.originalTitle || `${country} Clip`;
        let title = originalTitle;
        let description = '';

        try {
          const td = await this.ai.chatJSON(
            `Generate YouTube Shorts title+description. Country: ${country}${s.hasCaptions ? ' (English captions)' : ''}
${s.transcript ? `Transcript: "${s.transcript.substring(0, 500)}"` : ''}
Title: catchy, max 70 chars.
Description: 3-4 sentences about what the video shows, why interesting, CTA. Hashtags.
Return JSON.`,
            `Title for ${country}`, { useCheapModel: true, temperature: 0.7 }
          );
          title = (td.title || originalTitle).substring(0, 100);
          description = td.description || `Amazing content from ${country}! Follow for more! #shorts #${country.toLowerCase()} #worldwide`;
        } catch {
          try {
            const hResult = await this.hermes.chat(`Generate YouTube Shorts title (max 70 chars) for ${country}. ${s.transcript ? `Content: "${s.transcript.substring(0, 300)}"` : ''} Return ONLY title.`, { timeout: 30000 });
            if (hResult.success && hResult.output) {
              title = hResult.output.trim().replace(/["']/g, '').substring(0, 100);
              if (!title?.length > 3) title = originalTitle.substring(0, 100);
            }
          } catch {}
          description = `Amazing content from ${country}! ${s.hasCaptions ? 'English captions included. ' : ''}Follow Mr. WorldWideWebster! #shorts #${country.toLowerCase()} #worldwide`;
        }

        const titleCheck = await this._hermesCheck('YouTube title', title, `Country: ${country}`);
        if (!titleCheck.passed && titleCheck.score < 40) title = originalTitle.substring(0, 100);

        if (this._hasProfanity(title) || this._hasProfanity(description)) {
          title = `${country} Clip #shorts`;
          description = `Amazing content from ${country}. Follow Mr. WorldWideWebster! #shorts`;
        }

        const uploadResult = await this._uploadToYouTube({ videoPath: s.path, title, description, tags: ['mr worldwidewebster', 'shorts', country.toLowerCase()] });
        if (uploadResult) { uploaded.push({ title, url: uploadResult.url, country }); await this._boostVideo(uploadResult.url); }
        else { errors.push(`Upload failed: ${title}`); }
      } catch (e) { errors.push(`Upload error: ${e.message}`); }
    }

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
    this.logger.header('🌙 NIGHTLY: Hermes investigates');
    if (!this.hermes || !this.hermes.isAvailable()) { this.logger.warn('Hermes not available'); return; }
    const cm = this.memory['channel-memory'] || {};
    this.logger.info(`Total: ${cm.totalVideosPosted || 0} | Countries: ${(cm.countriesUsedThisWeek || []).join(', ')}`);

    const result = await this.hermes.chat(
      `NIGHTLY INVESTIGATION for Mr. WorldWideWebster.

Current:
- Videos: ${cm.totalVideosPosted || 0}
- Countries: ${(cm.countriesUsedThisWeek || []).join(', ')}

Tasks:
1. Suggest 5 NEW countries for tomorrow
2. Suggest 10 search queries (mix English + native language)
3. What formats are winning?

Respond:
NEW COUNTRIES: ...
QUERIES: ...
FORMATS: ...
STRATEGY: ...`,
      { timeout: 300000 }
    );

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
