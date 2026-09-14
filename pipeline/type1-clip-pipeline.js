/**
 * Type 1 Pipeline — Channel-Sourced Viral Short
 * 
 * Instead of querying YouTube search, this pipeline pulls from a curated
 * pool of 50+ channels. It picks 10 random channels, scrapes 1 old short
 * (>=1 month) from each, Gemini classifies each video's country, then
 * selects the video whose country has been posted the LEAST this week.
 * 
 * Flow:
 * 1. Load channel pool → Pick 10 random channels
 * 2. Scrape each channel's Shorts feed → Get 1 old short each (dedup by usedVideoIds)
 * 3. Download all 10 candidates in parallel
 * 4. Gemini classifies each video's country
 * 5. Pick video from the least-covered country this week
 * 6. Smart Cut → Render (easter eggs, watermark, captions, FFV1) → QA → Upload
 *
 * Note: no country flag / "edit from {country}" voice line is burned into
 * the video any more — the country only drives selection and metadata.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const config = require('../core/config');
const { Logger } = require('../core/logger');
const { getGeminiService } = require('../core/gemini-service');
const { getGeminiCLI } = require('../core/gemini-cli-runner');
const { getOpenRouterQA } = require('../core/openrouter-qa');
const { probeVideo, extractFrames, generateDynamicCropFilter, computeCropDimensions } = require('../core/smart-cropper');
const { smartClipAndCrop } = require('../core/ai-clipper');
const { smartEdit, detectDialogue } = require('../core/smart-editor');
const { validateOutput, geminiReview } = require('../core/frame-qa');
const { addWatermark } = require('../core/watermark');
const { planEasterEggs, buildEasterEggFilters } = require('../core/easter-eggs');
const { loadInsights, rankTitles, countryWeight, channelWeight, categoryWeight, predictTitleScores, postedDedupList } = require('../core/performance-tracker');
const { applyWatermarkPass } = require('../core/watermark-cover');

const logger = new Logger('Type1Pipeline');

const SHORTS_W = 1080;
const SHORTS_H = 1920;
// content categories Gemini picks from (also learned per category by the performance tracker)
const NICHE_CATEGORIES = ['kpop', 'jpop', 'cpop', 'dance', 'anime', 'cosplay', 'food', 'street', 'comedy', 'drama', 'cute', 'beauty', 'fashion', 'travel', 'sports', 'music', 'other'];
const CHANNEL_POOL_FILE = path.join(__dirname, '..', 'config', 'channel-pool.json');
const CHANNEL_MEMORY_FILE = path.join(__dirname, '..', 'memory', 'type1-channel-memory.json');

// ─── Channel Pool Memory ─────────────────────────────────────

function loadChannelMemory() {
  try {
    if (fs.existsSync(CHANNEL_MEMORY_FILE)) {
      const raw = fs.readFileSync(CHANNEL_MEMORY_FILE, 'utf8');
      try {
        return JSON.parse(raw);
      } catch (parseErr) {
        logger.warn(`Corrupted channel memory JSON (${parseErr.message}) — resetting`);
        fs.writeFileSync(CHANNEL_MEMORY_FILE, JSON.stringify({ usedVideoIds: [], usedChannels: [], lastRun: null }, null, 2));
      }
    }
  } catch (e) {
    logger.warn(`Channel memory load: ${e.message}`);
  }
  return { usedVideoIds: [], usedChannels: [], lastRun: null };
}

function saveChannelMemory(memory) {
  try {
    if (!fs.existsSync(path.dirname(CHANNEL_MEMORY_FILE))) {
      fs.mkdirSync(path.dirname(CHANNEL_MEMORY_FILE), { recursive: true });
    }
    fs.writeFileSync(CHANNEL_MEMORY_FILE, JSON.stringify(memory, null, 2));
    logger.success(`Channel memory saved: ${memory.usedVideoIds.length} used video IDs`);
  } catch (e) {
    logger.warn(`Channel memory save: ${e.message}`);
  }
}

// ─── Step 1: Load channel pool ──────────────────────────────────

function loadChannelPool() {
  try {
    if (fs.existsSync(CHANNEL_POOL_FILE)) {
      const data = JSON.parse(fs.readFileSync(CHANNEL_POOL_FILE, 'utf8'));
      const rawChannels = data.channels || [];
      // Only enabled (Asian edits) channels are active. Non-Asian channels remain in
      // the file (marked "enabled": false) but are excluded from the active pool.
      const channels = rawChannels.filter(ch => ch.enabled !== false);
      const disabledCount = rawChannels.length - channels.length;
      logger.info(`Loaded ${channels.length} enabled (Asian) channels from pool (${disabledCount} disabled)`);
      return channels;
    }
  } catch (e) {
    logger.error(`Channel pool load: ${e.message}`);
  }
  return [];
}

// ─── Step 2: Pick 10 random (unused) channels ──────────────────

function pickRandomChannels(channelPool, memory) {
  const usableChannels = [];
  for (const ch of channelPool) {
    if (!(memory.usedChannels || []).includes(ch.url)) {
      usableChannels.push(ch);
    }
  }
  // If we don't have 10 unused channels, recycle all channels
  if (usableChannels.length < 10) {
    logger.warn(`Only ${usableChannels.length} unused channels — recycling all`);
    return [...channelPool].sort(() => Math.random() - 0.5).slice(0, 10);
  }
  const shuffled = [...usableChannels].sort(() => Math.random() - 0.5);
  const picked = shuffled.slice(0, 10);
  logger.info(`Picked 10 channels from ${usableChannels.length} unused`);
  return picked;
}

// ─── Step 3: Scrape a channel's Shorts feed for old shorts ──────

function scrapeChannelShorts(channelInfo, memory) {
  const handle = String(channelInfo.handle || '').replace(/^@/, '');
  logger.info(`Scraping @${handle} for old shorts...`);

  // Videos must be at least 1 month old
  const oneMonthAgo = new Date();
  oneMonthAgo.setMonth(oneMonthAgo.getMonth() - 1);
  const dateBefore = oneMonthAgo.toISOString().split('T')[0].replace(/-/g, '');

  try {
    const cmd = `yt-dlp --flat-playlist --dump-json ` +
      `--datebefore ${dateBefore} ` +
      `--playlist-end 50 ` +
      `--match-filter "!is_live & !upcoming" ` +
      `"${channelInfo.url}" 2>&1`;

    const out = execSync(cmd, { timeout: 30000, maxBuffer: 5 * 1024 * 1024, encoding: 'utf8' }).toString().trim();

    if (!out) {
      logger.warn(`No shorts older than 1 month for @${handle}`);
      return null;
    }

    const lines = out.split('\n').filter(Boolean);
    logger.info(`@${handle}: ${lines.length} shorts >= 1 month old`);

    const candidates = [];
    for (const line of lines) {
      try {
        const p = JSON.parse(line);
        if (p.id && !(memory.usedVideoIds || []).includes(p.id)) {
          candidates.push({
            id: p.id,
            url: `https://www.youtube.com/watch?v=${p.id}`,
            shortsUrl: `https://www.youtube.com/shorts/${p.id}`,
            title: p.title || 'YouTube Short',
            duration: p.duration || 0,
            upload_date: p.upload_date || '',
            view_count: p.view_count || 0,
            channelHandle: handle,
            description: '', // will be enriched after download
          });
        }
      } catch {}
    }

    logger.info(`@${handle}: ${candidates.length} candidates after dedup`);

    if (candidates.length === 0) {
      logger.warn(`@${handle}: all shorts already used — marking channel as used`);
      if (!memory.usedChannels) memory.usedChannels = [];
      memory.usedChannels.push(channelInfo.url);
      saveChannelMemory(memory);
      return null;
    }

    const picked = candidates[Math.floor(Math.random() * candidates.length)];
    logger.info(`@${handle} → "${(picked.title || 'Untitled').substring(0, 50)}" (${picked.duration}s, ${picked.upload_date || 'unknown'})`);
    return picked;
  } catch (e) {
    logger.warn(`@${handle} scrape failed: ${(e.message || '').substring(0, 80)}`);
    return null;
  }
}

// ─── Step 4: Gather 10 candidates from 10 channels ─────────────

function collectCandidates(channels, memory) {
  const results = [];
  for (const ch of channels) {
    const video = scrapeChannelShorts(ch, memory);
    if (video) {
      results.push(video);
    } else {
      logger.warn(`Failed to get a short from @${String(ch.handle || '').replace(/^@/, '')}`);
    }
    if (results.length >= 10) break;
  }
  logger.info(`Collected ${results.length} candidates from ${channels.length} channels`);
  return results;
}

// ─── Step 5: Probe video dimensions ────────────────────────────

function probeVideoDims(fp) {
  try {
    const out = execSync(`ffprobe -v error -select_streams v:0 -show_entries stream=width,height,duration -of csv=p=0 "${fp}"`, { timeout: 10000, encoding: 'utf8' }).trim();
    const [width, height, duration] = out.split(',').map(s => Number.parseFloat(s.trim()));
    if (Number.isFinite(width) && Number.isFinite(height)) return { width: Math.round(width), height: Math.round(height), duration: Number.isFinite(duration) ? duration : 0 };
  } catch {}
  return { width: 0, height: 0, duration: 0 };
}

// ─── Step 6: Download a candidate (for country classification) ──

function downloadForClassify(video, tmpDir) {
  const outputStem = `classify_${Date.now()}_${video.id}`;
  const outputTemplate = path.join(tmpDir, `${outputStem}.%(ext)s`);
  const url = video.shortsUrl || video.url;
  logger.info(`  Downloading ${video.id} for classification...`);

  const strategies = [
    { name: 'web_best', args: '--extractor-args "youtube:player_client=web"', format: '-f "bestvideo[height<=480]+bestaudio/best" --merge-output-format mp4' },
    { name: 'default_best', args: '', format: '-f "bestvideo[height<=480]+bestaudio/best" --merge-output-format mp4' },
  ];

  for (const s of strategies) {
    try {
      const hasCookies = fs.existsSync('/tmp/yt_cookies.txt');
      const cookieArg = hasCookies ? '--cookies "/tmp/yt_cookies.txt"' : '';
      const cmd = `yt-dlp ${cookieArg} ${s.args} ${s.format} -o "${outputTemplate}" "${url}" --no-playlist --socket-timeout 30 --retries 2 --force-ipv4 2>&1`;
      execSync(cmd, { timeout: 120000, maxBuffer: 50 * 1024 * 1024 });
      const files = fs.readdirSync(tmpDir).filter(f => f.startsWith(outputStem) && (f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mkv')) && fs.statSync(path.join(tmpDir, f)).size > 50000).sort((a, b) => fs.statSync(path.join(tmpDir, b)).mtimeMs - fs.statSync(path.join(tmpDir, a)).mtimeMs);
      if (files.length > 0) {
        const fp = path.join(tmpDir, files[0]);
        const sizeMb = (fs.statSync(fp).size / 1024 / 1024).toFixed(1);
        logger.info(`  Downloaded ${video.id} (${sizeMb}MB, ${s.name})`);
        return fp;
      }
    } catch (e) {
      logger.warn(`  Download ${video.id} ${s.name} failed: ${(e.message || '').substring(0, 60)}`);
    }
  }
  return null;
}

// ─── Step 7: Gemini classifies country from video + title ──────

async function classifyCountryForCandidate(videoPath, candidate, gemini) {
  logger.info(`  Classifying country for ${candidate.id}...`);

  const tmpDir = path.dirname(videoPath);
  const frameDir = path.join(tmpDir, `country_frames_${Date.now()}_${candidate.id}`);
  try { fs.mkdirSync(frameDir, { recursive: true }); } catch {}

  const dims = probeVideoDims(videoPath);
  const duration = dims.duration || 30;
  const positions = [1, Math.max(2, duration * 0.3), Math.max(3, duration * 0.6), Math.max(4, duration - 2)].filter(p => p < duration);

  const frames = [];
  for (const pos of positions) {
    const framePath = path.join(frameDir, `frame_${Math.round(pos)}.jpg`);
    try {
      execSync(`ffmpeg -y -ss ${pos.toFixed(2)} -i "${videoPath}" -vframes 1 -q:v 2 "${framePath}" 2>/dev/null`, { timeout: 10000 });
      if (fs.existsSync(framePath) && fs.statSync(framePath).size > 1000) {
        frames.push(framePath);
      }
    } catch {}
  }

  let country = candidate.countryGuess || null;
  let confidence = 5;
  // content descriptors used by the niche gate and the similarity-based pick
  let category = 'other';
  let summary = '';
  let asianNiche = null;      // true / false / null (unknown)
  let nicheConfidence = 0;

  const applyParsed = (p, label) => {
    if (!p) return;
    if (p.country) { country = p.country; confidence = p.confidence || 3; }
    if (typeof p.category === 'string' && p.category.trim()) category = p.category.trim().toLowerCase();
    if (typeof p.summary === 'string' && p.summary.trim()) summary = p.summary.trim().substring(0, 200);
    if (typeof p.asianNiche === 'boolean') { asianNiche = p.asianNiche; nicheConfidence = Number(p.nicheConfidence) || 5; }
    logger.info(`  ${candidate.id}${label}: ${country} (${confidence}/10) ${category} asian=${asianNiche === null ? '?' : asianNiche}${summary ? ` — ${summary.substring(0, 60)}` : ''}`);
  };

  if (frames.length >= 2) {
    const prompt = `Analyze these frames from a YouTube Shorts video.

Video Title: "${candidate.title || 'Unknown'}"

1. Identify the country or region MOST LIKELY shown in this video. Look at landmarks, architecture, scenery, signs and writing, food, clothing, cultural elements, the title, people's appearance. If unsure use "World" with low confidence.
2. Describe the content in one short English sentence (max 20 words) — what happens, who, where.
3. Pick ONE category: ${NICHE_CATEGORIES.join(', ')}.
4. Decide whether it fits an "Asian edits" channel: Asian setting (East / Southeast / South Asia) OR Asian culture, people or media such as k-pop, j-pop, c-pop, anime, manga, k-drama, Chinese/Japanese/Korean/Thai/Vietnamese/Indian/Filipino daily life, food, dance, fashion. Western sports, Western celebrities or generic content with no Asian connection does NOT fit.

Return STRICT JSON: {"country": "Country Name", "confidence": 0-10, "reasoning": "brief", "summary": "one sentence", "category": "one of the list", "asianNiche": true|false, "nicheConfidence": 0-10}`;

    const result = await gemini.analyzeFrames(frames, prompt,
      'You are a geography and pop-culture expert who curates an Asian edits YouTube Shorts channel. Identify countries and content from video frames.');

    try { fs.rmSync(frameDir, { recursive: true, force: true }); } catch {}

    if (result) {
      try {
        const m = result.match(/\{[\s\S]*\}/);
        if (m) applyParsed(JSON.parse(m[0]), '');
      } catch (e) {
        logger.warn(`  Country parse error for ${candidate.id}: ${e.message.substring(0, 50)}`);
      }
    }
  } else {
    try { fs.rmSync(frameDir, { recursive: true, force: true }); } catch {}
  }

  // Fallback: text-only Gemini
  if (!country || confidence < 3 || asianNiche === null) {
    logger.info(`  Falling back to title-only for ${candidate.id}...`);
    const textResult = await gemini.chat(
      `You curate an "Asian edits" YouTube Shorts channel. From a video title alone, identify the country, a one-sentence summary, a category (${NICHE_CATEGORIES.join(', ')}) and whether it fits the Asian niche (Asian setting, people, culture or media such as k-pop / anime). Return STRICT JSON: {"country": "Country Name", "confidence": 0-10, "summary": "...", "category": "...", "asianNiche": true|false, "nicheConfidence": 0-10}`,
      `Video title: "${candidate.title || 'Unknown'}"${candidate.channelHandle ? ` (from channel @${candidate.channelHandle})` : ''}`,
      { temperature: 0.3, maxTokens: 300 }
    );
    if (textResult) {
      try {
        const m = textResult.match(/\{[\s\S]*\}/);
        if (m) {
          const p = JSON.parse(m[0]);
          // keep the frame-based country if it was confident, but take the niche verdict
          if (country && confidence >= 3) { delete p.country; delete p.confidence; }
          applyParsed(p, ' (text)');
        }
      } catch {}
    }
  }

  if (!country) {
    country = 'World';
    confidence = 1;
  }
  if (!NICHE_CATEGORIES.includes(category)) category = 'other';

  logger.info(`  → ${candidate.id} classified as: ${country} (${confidence}/10), ${category}, asianNiche=${asianNiche === null ? 'unknown' : asianNiche}`);
  return { country, confidence, category, summary, asianNiche, nicheConfidence };
}

// ─── Step 8: Pick the candidate whose country is least covered this week ──

function pickLeastCoveredCandidate(classifications, countriesUsedThisWeek, insights = null) {
  if (classifications.length === 0) return null;

  const weekCounts = {};
  for (const c of (countriesUsedThisWeek || [])) {
    weekCounts[c] = (weekCounts[c] || 0) + 1;
  }

  // Coverage keeps countries rotating. Learned weights from our own channel
  // stats (country / source channel / category) and how similar the clip is
  // to what has performed (simNorm, 0..1) pull the odds towards proven
  // content — but the final pick is SAMPLED from a softmax over the scores,
  // so a strong candidate is likely, never certain, and other clips still
  // get their turn.
  const learning = !!(insights && insights.reliable);
  const scored = classifications.map(c => {
    const coverage = weekCounts[c.country] || 0;
    const cw = learning ? countryWeight(insights, c.country) : 1;
    const hw = learning ? channelWeight(insights, c.candidate && c.candidate.channelHandle) : 1;
    const kw = learning ? categoryWeight(insights, c.category) : 1;
    const sim = typeof c.simNorm === 'number' ? c.simNorm : 0.5;
    const score = -coverage * 0.8
      + (cw - 1) * 1.0 + (hw - 1) * 0.6 + (kw - 1) * 0.6
      + (learning ? (sim - 0.5) * 1.2 : 0)
      + (c.confidence || 0) / 40;
    return { ...c, coverage, cw, hw, kw, sim, score };
  }).sort((a, b) => b.score - a.score);

  // softmax sampling (temperature 0.35): best candidate gets the highest odds
  const temp = 0.35;
  const maxScore = scored[0].score;
  const weights = scored.map(s => Math.exp((s.score - maxScore) / temp));
  const total = weights.reduce((a, b) => a + b, 0);
  const probs = weights.map(w => w / total);
  let r = Math.random(), idx = 0;
  for (let i = 0; i < probs.length; i++) { r -= probs[i]; if (r <= 0) { idx = i; break; } idx = i; }
  const winner = scored[idx];

  logger.info(`Country coverage this week: ${JSON.stringify(weekCounts)}`);
  logger.info(`Candidates: ${scored.slice(0, 6).map((s, i) => `${s.country}/@${s.candidate && s.candidate.channelHandle}${s.category ? '/' + s.category : ''} score ${s.score.toFixed(2)} p=${(probs[i] * 100).toFixed(0)}%${learning ? ` (x${s.cw}/x${s.hw}/x${s.kw} sim ${s.sim.toFixed(2)})` : ''}`).join(' | ')}`);
  logger.success(`Picking: ${winner.country} (${winner.coverage}x this week, confidence ${winner.confidence}/10, score ${winner.score.toFixed(2)}, odds ${(probs[idx] * 100).toFixed(0)}%)`);
  return winner;
}

// ═══════════════════════════════════════════════════════════════════
// PRESERVED FUNCTIONS (unchanged from original Type 1)
// ═══════════════════════════════════════════════════════════════════

// Universal hashtags for cross-platform reach
const UNIVERSAL_HASHTAGS = '#asianedits #kpop #anime #edits';

function buildCountryHashtags(country) {
  const safe = String(country || '').toLowerCase().replace(/[^a-z0-9]+/g, '').replace(/^(the|a|an)$/, '');
  return `${UNIVERSAL_HASHTAGS} #${safe} #viral`;
}

const COUNTRY_METADATA_PROFILES = {
  China: { titleBase: 'China', tags: ['china', 'viral', 'funny'], hashtags: buildCountryHashtags('China') },
  Japan: { titleBase: 'Japan', tags: ['japan', 'viral', 'funny'], hashtags: buildCountryHashtags('Japan') },
  'South Korea': { titleBase: 'Korea', tags: ['south korea', 'viral', 'funny'], hashtags: buildCountryHashtags('Korea') },
  UK: { titleBase: 'The UK', tags: ['uk', 'viral', 'funny'], hashtags: buildCountryHashtags('UK') },
  Nigeria: { titleBase: 'Nigeria', tags: ['nigeria', 'viral', 'funny'], hashtags: buildCountryHashtags('Nigeria') },
  Africa: { titleBase: 'Africa', tags: ['africa', 'viral', 'funny'], hashtags: buildCountryHashtags('Africa') },
  'South Africa': { titleBase: 'South Africa', tags: ['south africa', 'viral', 'funny'], hashtags: buildCountryHashtags('South Africa') },
};

function buildFallbackMetadata(country, bestVideo, dialogue) {
  const reasoning = (bestVideo.reasoning || '').toLowerCase();
  const sourceTitle = bestVideo.title || '';
  const profile = COUNTRY_METADATA_PROFILES[country] || { titleBase: country, tags: [country.toLowerCase(), 'viral', 'funny'], hashtags: buildCountryHashtags(country) };

  if (sourceTitle && sourceTitle.length > 5) {
    const fallbackDesc = bestVideo.reasoning
      ? bestVideo.reasoning.split('.').slice(0, 2).join('.').substring(0, 180)
      : `A viral ${country} short.`;
    return {
      title: sourceTitle.substring(0, 50),
      description: `${fallbackDesc}\n\nWould you stop and watch this?\n\n${profile.hashtags}`,
      tags: ['shorts', 'viral', 'asian edits', 'kpop', 'anime'].concat(profile.tags),
    };
  }

  let hook = `${profile.titleBase} Street Moment`;
  if (reasoning.includes('waiter') || /waiter/i.test(sourceTitle)) hook = 'This Waiter Started Dancing';
  else if (reasoning.includes('dance') || /dance|douyin|kemusan|subject three|amapiano|k-pop|kpop|salsa|cumbia/i.test(sourceTitle)) hook = `${profile.titleBase} Dance Hits Different`;
  else if (reasoning.includes('funny') || reasoning.includes('comedy') || /funny|comedy|skit|meme/i.test(sourceTitle)) hook = `${profile.titleBase} Has Main Character Energy`;
  else if (reasoning.includes('street food') || /food|ramen|taco|kebab|market|vendor/i.test(sourceTitle)) hook = `${profile.titleBase} Street Food Goes Crazy`;
  else if (reasoning.includes('hanfu') || /hanfu/i.test(sourceTitle)) hook = 'This Hanfu Moment Is Unreal';
  else if (reasoning.includes('football') || /football|soccer/i.test(sourceTitle)) hook = `${profile.titleBase} Football Fans Are Different`;
  else if (reasoning.includes('interview') || /street interview/i.test(sourceTitle)) hook = `${profile.titleBase} Street Interviews Are Unhinged`;
  const shortTranscript = (dialogue?.transcript || '').trim();
  const descriptionHook = bestVideo.reasoning ? bestVideo.reasoning.split('.').slice(0, 2).join('.').substring(0, 180) : `A viral ${country} short with a visual hook from the first seconds.`;
  return { title: hook.substring(0, 50), description: `${descriptionHook}\n\nWould you stop and watch this?\n\n${profile.hashtags}`, tags: ['shorts', 'viral', 'asian edits', 'kpop', 'anime'].concat(profile.tags).concat(shortTranscript ? ['asian edits'] : []) };
}

// ─── Quality check ──────────────────────────────────────────────

function assessVideoQuality(videoPath) {
  try {
    const out = execSync(
      `python3 "${path.join(__dirname, '..', 'core', 'video-quality.py')}" "${videoPath}" --start 0 --duration 12 --interval 5`,
      { timeout: 60000, encoding: 'utf8', maxBuffer: 1024 * 1024 }
    ).toString().trim();
    const lines = out.split('\n').filter(l => l.startsWith('{'));
    if (lines.length > 0) return JSON.parse(lines[lines.length - 1]);
  } catch (e) {
    logger.warn(`Quality assess failed: ${e.message.substring(0, 80)}`);
  }
  return { verdict: 'accept', laplacian_avg: -1, musiq_avg: -1, edge_density_avg: -1, frame_count: 0, rejection_reasons: [] };
}

// ─── Smart Cut ──────────────────────────────────────────────────

function smartCut(videoPath, duration) {
  logger.info('Smart Cut: analyzing video for best segment...');
  
  let probePath = videoPath;
  const isMkv = videoPath.endsWith('.mkv') || videoPath.endsWith('.webm');
  if (isMkv) {
    probePath = videoPath.replace(/\.\w+$/, `_probe_${Date.now()}.mp4`);
    try {
      logger.info('Creating MP4 probe for highlight detection (avoids opus/MKV incompatibility)...');
      execSync(`ffmpeg -y -i "${videoPath}" -t 120 -vf scale=640:-1 -c:v libx264 -preset ultrafast -crf 23 -c:a aac -b:a 64k "${probePath}"`, { timeout: 60000 });
      if (!(fs.existsSync(probePath) && fs.statSync(probePath).size > 50000)) {
        probePath = videoPath;
      } else {
        logger.info(`Probe MP4 created: ${(fs.statSync(probePath).size / 1024 / 1024).toFixed(1)}MB`);
      }
    } catch {
      probePath = videoPath;
    }
  }

  try {
    const hlPath = path.join(__dirname, '..', 'core', 'highlight-detector.py');
    if (fs.existsSync(hlPath)) {
      const hlCmd = `python3 "${hlPath}" "${probePath}" --output-json 2>&1`;
      const hlOut = execSync(hlCmd, { timeout: 300000, maxBuffer: 10 * 1024 * 1024, encoding: 'utf8' }).toString().trim();
      const outLines = hlOut.split('\n').filter(Boolean);
      const result = JSON.parse(outLines[outLines.length - 1]);
      if (result.action === 'extract' && result.start >= 0 && result.duration > 0) {
        logger.success(`Smart Cut: best segment ${result.start}s -> ${result.end}s (${result.duration}s)`);
        if (probePath !== videoPath) try { fs.unlinkSync(probePath); } catch {}
        return { start: result.start, end: result.end };
      }
    }
  } catch (e) {
    logger.warn(`Smart cut analysis failed: ${(e.message || '').substring(0, 200)}`);
  }
  
  if (probePath !== videoPath) try { fs.unlinkSync(probePath); } catch {}

  if (duration > 120) {
    const mid = duration / 2;
    const fallback = { start: Math.max(0, mid - 20), end: Math.min(duration, mid + 20) };
    logger.info(`Smart Cut fallback: ${fallback.start}s > ${fallback.end}s`);
    return fallback;
  } else {
    return { start: 0, end: duration };
  }
}

// ─── Download Best Video ────────────────────────────────────────

function downloadBestVideo(video, outputDir) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const outputStem = `source_${Date.now()}`;
  const outputTemplate = path.join(outputDir, `${outputStem}.%(ext)s`);
  const url = video.shortsUrl || video.url;
  logger.info(`Downloading best: ${url}`);
  let bestFallback = null;
  const strategies = [
    { name: 'web_best', args: '--extractor-args "youtube:player_client=web"', format: '-f "bestvideo+bestaudio/best" --merge-output-format mkv' },
    { name: 'default_best', args: '', format: '-f "bestvideo+bestaudio/best" --merge-output-format mkv' },
    { name: 'android_best', args: '--extractor-args "youtube:player_client=android"', format: '-f "bestvideo+bestaudio/best" --merge-output-format mkv' },
    { name: 'fallback_mp4', args: '', format: '-f "bestvideo+bestaudio/best" --merge-output-format mp4' },
  ];
  for (const s of strategies) {
    try {
      const hasCookies = fs.existsSync('/tmp/yt_cookies.txt');
      const cookieArg = (hasCookies && !s.name.includes('android')) ? '--cookies "/tmp/yt_cookies.txt"' : '';
      const cmd = `yt-dlp ${cookieArg} ${s.args} ${s.format} -o "${outputTemplate}" "${url}" --no-playlist --socket-timeout 30 --retries 3 --force-ipv4 --remote-components ejs:github`;
      execSync(cmd, { timeout: 180000, maxBuffer: 200 * 1024 * 1024 });
      const files = fs.readdirSync(outputDir).filter(f => f.startsWith(outputStem) && (f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mkv')) && fs.statSync(path.join(outputDir, f)).size > 50000).sort((a, b) => fs.statSync(path.join(outputDir, b)).mtimeMs - fs.statSync(path.join(outputDir, a)).mtimeMs);
      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        const dims = probeVideoDims(fp);
        const sizeMb = (fs.statSync(fp).size / 1024 / 1024).toFixed(1);
        logger.success(`Downloaded: ${files[0]} (${sizeMb}MB, ${dims.width}x${dims.height}, strategy: ${s.name})`);
        if (!bestFallback || Math.max(dims.width, dims.height) > Math.max(bestFallback.dims.width, bestFallback.dims.height)) bestFallback = { path: fp, dims, strategy: s.name };
        if (Math.max(dims.width || 0, dims.height || 0) < 720) { logger.warn(`Downloaded source is only ${dims.width}x${dims.height}; trying next quality strategy`); continue; }
        return fp;
      }
    } catch (e) { logger.warn(`Download strategy ${s.name} failed: ${e.message.substring(0, 60)}`); }
  }
  if (bestFallback?.path && fs.existsSync(bestFallback.path)) { logger.warn(`Using best available source: ${bestFallback.dims.width}x${bestFallback.dims.height} (${bestFallback.strategy})`); return bestFallback.path; }
  logger.error(`All download strategies failed for ${url}`);
  return null;
}

// ─── Transcribe Audio ───────────────────────────────────────────

async function transcribeAudio(videoPath, tmpDir) {
  const audioPath = path.join(tmpDir, `audio_${Date.now()}.mp3`);
  try {
    execSync(`ffmpeg -y -i "${videoPath}" -t 60 -vn -acodec libmp3lame -ab 64k "${audioPath}" 2>/dev/null`, { timeout: 30000 });
    if (!fs.existsSync(audioPath) || fs.statSync(audioPath).size < 1000) return { hasDialogue: false, wordCount: 0, language: 'unknown', transcript: '', words: [] };
    const pyPath = audioPath.replace(/\\/g, '\\\\');
    const output = execSync(`python3 -c "
from faster_whisper import WhisperModel
import json
model = WhisperModel('base', device='cpu', compute_type='int8')
segments, info = model.transcribe('${pyPath}', word_timestamps=True)
text = ' '.join(seg.text for seg in segments)
words = []
for seg in segments:
    if seg.words:
        for w in seg.words:
            words.append({'word': w.word, 'start': w.start, 'end': w.end})
print(json.dumps({'text': text[:1000], 'language': info.language, 'word_count': len(text.split()), 'words': words}))
" 2>&1`, { timeout: 120000, encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 }).toString().trim();
    try { fs.unlinkSync(audioPath); } catch {}
    if (output && !output.includes('Error') && !output.includes('Traceback')) {
      const p = JSON.parse(output);
      return { hasDialogue: p.word_count > 5, wordCount: p.word_count || 0, language: p.language || 'en', transcript: p.text || '', words: p.words || [] };
    }
  } catch (e) { logger.warn(`Transcription failed: ${e.message.substring(0, 60)}`); try { fs.unlinkSync(audioPath); } catch {} }
  return { hasDialogue: false, wordCount: 0, language: 'unknown', transcript: '', words: [] };
}

// ─── Crop Offset ────────────────────────────────────────────────

function getCropOffset(videoPath, srcW, srcH, tmpDir) {
  const yoloDir = path.join(tmpDir, `yolo_crop_${Date.now()}`);
  fs.mkdirSync(yoloDir, { recursive: true });
  const dims = probeVideoDims(videoPath);
  const dur = dims.duration || 30;
  const positions = [];
  for (let t = 1.5; t < dur - 1; t += 1.5) positions.push(t);
  const subjectCenters = [];
  for (const pos of positions) {
    const framePath = path.join(yoloDir, `frame_${pos}.jpg`);
    try {
      execSync(`ffmpeg -y -ss ${pos.toFixed(2)} -i "${videoPath}" -vframes 1 -q:v 2 "${framePath}" 2>/dev/null`, { timeout: 10000 });
      if (fs.existsSync(framePath) && fs.statSync(framePath).size > 1000) {
        const yoloOut = execSync(`python3 "${path.join(__dirname, '..', 'core', 'yolo-crop.py')}" "${framePath}"`, { timeout: 30000, encoding: 'utf8' }).toString().trim();
        const result = JSON.parse(yoloOut);
        if (result.subject !== 'none' && result.center_x >= 0) {
          subjectCenters.push(result.center_x);
        }
      }
    } catch {}
  }
  try { fs.rmSync(yoloDir, { recursive: true, force: true }); } catch {}
  if (subjectCenters.length === 0) return 0;
  const avgCenterX = subjectCenters.reduce((a, b) => a + b, 0) / subjectCenters.length;
  const targetHeight = 1920;
  const targetWidth = 1080;
  const scaleFactor = targetHeight / srcH;
  const scaledWidth = Math.round(srcW * scaleFactor);
  const maxCropX = scaledWidth - targetWidth;
  const cropX = avgCenterX * scaleFactor - (targetWidth / 2);
  const offset = Math.max(0, Math.min(Math.round(cropX), maxCropX));
  logger.info(`YOLO crop offset: ${offset}px (from ${subjectCenters.length} samples)`);
  return offset;
}

// ─── Build Combined Filter ──────────────────────────────────────

/**
 * Build the whole video filter graph: crop/scale to 9:16 → (captions) →
 * easter egg overlays → watermark → final scale.
 *
 * @param {Object|null} eggPlan        result of planEasterEggs() (may be empty)
 * @param {number} eggFirstInputIdx    ffmpeg input index of the first egg file (-1 = none)
 * @returns {{ filterComplex: string, videoOut: string, eggInputArgs: string }}
 */
function buildCombinedFilter(cropOffsetX, srcW, srcH, hasSubtitles, subPath, hasWatermark, wmPath, wmInputIdx, dynamicCropFilter, eggPlan, eggFirstInputIdx) {
  const filters = [];
  let eggInputArgs = '';
  let currentLabel = '0:v';
  if (dynamicCropFilter) {
    filters.push(`${dynamicCropFilter}[v1]`);
  } else {
    const targetHeight = 1920;
    const targetWidth = 1080;
    const ratio = srcW / srcH;
    const TARGET_RATIO = targetWidth / targetHeight;
    let filterStr;
    if (Math.abs(ratio - TARGET_RATIO) < 0.05) {
      filterStr = `scale=${targetWidth}:${targetHeight}:flags=lanczos:force_original_aspect_ratio=increase,crop=${targetWidth}:${targetHeight}`;
    } else if (ratio > TARGET_RATIO) {
      const sh = targetHeight;
      const sw = Math.floor(sh * ratio / 2) * 2;
      filterStr = `scale=${sw}:${sh}:flags=lanczos,crop=${targetWidth}:${targetHeight}:${cropOffsetX}:0`;
    } else {
      const sw = targetWidth;
      const sh = Math.floor(sw / ratio / 2) * 2;
      filterStr = `scale=${sw}:${sh}:flags=lanczos,crop=${targetWidth}:${targetHeight}:0:${Math.floor((sh - targetHeight) / 4) * 2}`;
    }
    filters.push(`${filterStr}[v1]`);
  }
  currentLabel = 'v1';
  if (hasSubtitles && subPath && fs.existsSync(subPath)) {
    const escPath = subPath.replace(/\\/g, '/').replace(/'/g, "'\\\\''");
    filters.push(`[${currentLabel}]ass='${escPath}'[v2]`);
    currentLabel = 'v2';
  }
  // Easter eggs go on before the watermark so the watermark always stays on top.
  // The frame is already 1080x1920 here in both crop modes.
  if (eggPlan && Array.isArray(eggPlan.eggs) && eggPlan.eggs.length > 0 && eggFirstInputIdx >= 0) {
    const eggs = buildEasterEggFilters(eggPlan, eggFirstInputIdx, currentLabel);
    filters.push(...eggs.filters);
    eggInputArgs = eggs.inputArgs;
    currentLabel = eggs.outLabel;
  }
  if (hasWatermark && wmPath && fs.existsSync(wmPath) && wmInputIdx >= 0) {
    const LOGO_SIZE = 80;
    const MARGIN_RIGHT = 20;
    const MARGIN_BOTTOM = 80;
    const FONT_SIZE = 28;
    const TEXT = '@Mr.WorldWideWebster';
    filters.push(`[${wmInputIdx}:v]scale=${LOGO_SIZE}:${LOGO_SIZE}:force_original_aspect_ratio=decrease,format=rgba,colorchannelmixer=aa=0.4[wm]`);
    filters.push(`[${currentLabel}][wm]overlay=W-w-${MARGIN_RIGHT}:H-h-${MARGIN_BOTTOM}:format=auto,drawtext=text='${TEXT}':fontcolor=white@0.40:fontsize=${FONT_SIZE}:x=W-tw-${MARGIN_RIGHT}:y=H-th-${Math.round(MARGIN_BOTTOM/2)}:shadowcolor=black@0.40:shadowx=1:shadowy=1[v4]`);
    currentLabel = 'v4';
  }
  if (!dynamicCropFilter) {
    filters.push(`[${currentLabel}]scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos[vout]`);
  } else {
    filters.push(`[${currentLabel}]null[vout]`);
  }
  return { filterComplex: filters.join(';'), videoOut: '[vout]', eggInputArgs };
}

// ═══════════════════════════════════════════════════════════════════
// MAIN PIPELINE
// ═══════════════════════════════════════════════════════════════════

async function runType1Pipeline(options = {}) {
  const outputDir = options.outputDir || path.join(__dirname, '..', 'output', 'clips');
  const tmpBaseDir = path.join(outputDir, `tmp_${Date.now()}`);
  if (!fs.existsSync(tmpBaseDir)) fs.mkdirSync(tmpBaseDir, { recursive: true });

  logger.header('TYPE 1 PIPELINE (Channel-Sourced)');

  const gemini = getGeminiService();
  const geminiCLI = getGeminiCLI();
  const memory = loadChannelMemory();

  // Subtitles/captions are DISABLED by default via config (Asian edits niche)
  const captionsEnabled = !!(config.captions && config.captions.enabled === true);
  if (!captionsEnabled) logger.info('Captions/subtitles DISABLED by config — no subtitle generation');

  // ─── Phase 1: Pick 10 channels & scrape 1 short each ──────────
  logger.header('Phase 1: Channel Pool → 10 Candidates');

  const channelPool = loadChannelPool();
  if (channelPool.length === 0) {
    logger.error('No channels in pool — aborting');
    return { success: false, error: 'Empty channel pool' };
  }

  const pickedChannels = pickRandomChannels(channelPool, memory);
  logger.info(`Channels: ${pickedChannels.map(c => c.handle).join(', ')}`);

  const candidates = collectCandidates(pickedChannels, memory);
  if (candidates.length === 0) {
    logger.error('No candidates found — aborting');
    return { success: false, error: 'No candidates' };
  }

  logger.success(`Phase 1 complete: ${candidates.length} candidates`);

  // ─── Phase 2: Download & Classify Countries ───────────────────
  logger.header('Phase 2: Country Classification');

  const classifications = [];
  const classifyTmpDir = path.join(tmpBaseDir, 'classify');
  if (!fs.existsSync(classifyTmpDir)) fs.mkdirSync(classifyTmpDir, { recursive: true });

  for (let i = 0; i < candidates.length; i++) {
    const candidate = candidates[i];
    logger.info(`[${i + 1}/${candidates.length}] Classifying ${candidate.id} (@${candidate.channelHandle})...`);

    const dlPath = downloadForClassify(candidate, classifyTmpDir);
    if (!dlPath) {
      logger.warn(`  Download failed for ${candidate.id} — skipping`);
      continue;
    }

    const { country, confidence, category, summary, asianNiche, nicheConfidence } = await classifyCountryForCandidate(dlPath, candidate, gemini);

    // Enrich candidate with classification result
    candidate.geminiCountry = country;
    candidate.geminiScore = Math.min(10, Math.max(1, Math.round(confidence)));
    candidate.hookScore = 5;
    candidate.category = category;
    candidate.summary = summary;

    classifications.push({ candidate, country, confidence, category, summary, asianNiche, nicheConfidence });

    // Clean up downloaded file
    try { fs.unlinkSync(dlPath); } catch {}
  }

  logger.info(`Classified ${classifications.length}/${candidates.length} candidates`);

  if (classifications.length === 0) {
    logger.error('No candidates could be classified — aborting');
    try { fs.rmSync(tmpBaseDir, { recursive: true, force: true }); } catch {}
    return { success: false, error: 'No classified candidates' };
  }

  // ─── Phase 3: Niche gate → similarity → weighted pick ─────────
  logger.header('Phase 3: Select Candidate (niche gate + learned preferences)');

  const countriesUsedThisWeek = options.countriesUsedThisWeek || [];
  const performanceInsights = loadInsights();
  if (performanceInsights && performanceInsights.reliable) {
    logger.info(`Learning from ${performanceInsights.sampleSize} past shorts (typical ${performanceInsights.typicalScore} views/day)`);
  } else {
    logger.info('Not enough channel performance data yet — using coverage-only selection');
  }

  // 3a. Niche gate: everything we post must fit the Asian niche
  const nicheOk = classifications.filter(c => c.asianNiche !== false);
  const nicheDropped = classifications.length - nicheOk.length;
  if (nicheDropped > 0) logger.info(`Niche gate: dropped ${nicheDropped} non-Asian candidate(s): ${classifications.filter(c => c.asianNiche === false).map(c => `@${c.candidate.channelHandle} (${c.country})`).join(', ')}`);
  if (nicheOk.length === 0) {
    logger.error('No candidate fits the Asian niche today — aborting (nothing off-niche gets posted)');
    try { fs.rmSync(tmpBaseDir, { recursive: true, force: true }); } catch {}
    return { success: false, error: 'No Asian-niche candidates' };
  }

  // 3b. Similarity to what already worked + near-duplicate check against what we posted
  let eligible = nicheOk;
  try {
    const texts = nicheOk.map(c => c.summary || c.candidate.title || '');
    const pred = predictTitleScores(texts, performanceInsights, postedDedupList());
    if (pred && Array.isArray(pred.predictions)) {
      const byText = new Map(pred.predictions.map(p => [p.title, p]));
      const preds = nicheOk.map((c, i) => byText.get(texts[i]) || null);
      const vals = preds.map(p => (p && typeof p.predicted === 'number') ? p.predicted : null).filter(v => v !== null);
      const lo = vals.length ? Math.min(...vals) : 0, hi = vals.length ? Math.max(...vals) : 0;
      nicheOk.forEach((c, i) => {
        const p = preds[i];
        c.dupSim = p ? (p.dupSim || 0) : 0;
        c.dupOf = p ? p.dupOf : null;
        c.simNorm = (p && typeof p.predicted === 'number' && hi > lo) ? (p.predicted - lo) / (hi - lo) : 0.5;
      });
      const dups = nicheOk.filter(c => c.dupSim >= 0.9);
      if (dups.length) logger.info(`Dedup: ${dups.length} near-duplicate(s) of earlier posts skipped: ${dups.map(c => `"${(c.summary || c.candidate.title || '').substring(0, 40)}" ~ "${String(c.dupOf || '').substring(0, 40)}"`).join(' | ')}`);
      const fresh = nicheOk.filter(c => c.dupSim < 0.9);
      if (fresh.length) eligible = fresh;
    }
  } catch (e) {
    logger.warn(`Similarity step skipped: ${(e.message || '').substring(0, 80)}`);
  }

  const winner = pickLeastCoveredCandidate(eligible, countriesUsedThisWeek, performanceInsights);

  if (!winner) {
    logger.error('No winner selected — aborting');
    try { fs.rmSync(tmpBaseDir, { recursive: true, force: true }); } catch {}
    return { success: false, error: 'No winner' };
  }

  const bestVideo = winner.candidate;
  const country = winner.country;
  logger.success(`Winner: @${bestVideo.channelHandle} "${(bestVideo.title || '').substring(0, 50)}" → ${country}`);

  // Clean up classify tmp
  try { fs.rmSync(classifyTmpDir, { recursive: true, force: true }); } catch {}

  // ─── Phase 4: Download + Smart Cut Analysis ───────────────────
  logger.header('Phase 4: Download + Smart Cut');

  const tempDownloadPath = downloadBestVideo(bestVideo, tmpBaseDir);
  if (!tempDownloadPath) {
    logger.error('Download failed — aborting');
    try { fs.rmSync(tmpBaseDir, { recursive: true, force: true }); } catch {}
    return { success: false, error: 'Download failed' };
  }

  const tempDims = probeVideoDims(tempDownloadPath);
  const tempDuration = tempDims.duration || 60;

  const cut = smartCut(tempDownloadPath, tempDuration);

  const analysisClip = path.join(tmpBaseDir, `analysis_${Date.now()}.mp4`);
  execSync(`ffmpeg -y -ss ${cut.start} -i "${tempDownloadPath}" -to ${cut.end} -c:v libx264 -preset fast -crf 0 -pix_fmt yuv444p -c:a aac -b:a 320k "${analysisClip}"`, { timeout: 7200000 });

  const analysisDims = probeVideoDims(analysisClip);
  const dialogue = await transcribeAudio(analysisClip, tmpBaseDir);
  if (gemini.hasProfanity(dialogue.transcript)) {
    logger.error('Profanity detected — aborting');
    try { fs.rmSync(tmpBaseDir, { recursive: true, force: true }); } catch {}
    return { success: false, error: 'Profanity detected' };
  }

  const clipDuration = Math.min(cut.end - cut.start, analysisDims.duration || 30);
  logger.info('Generating dynamic crop filter with 1 FPS person tracking...');
  const dynamicCropFilter = generateDynamicCropFilter(
    analysisClip, 0, clipDuration,
    analysisDims.width, analysisDims.height, tmpBaseDir
  );
  if (dynamicCropFilter) {
    const isDynamic = dynamicCropFilter.includes('lt(t') || dynamicCropFilter.includes('gte(t');
    if (isDynamic) {
      logger.success(`Dynamic crop filter generated (${dynamicCropFilter.length} chars)`);
    } else {
      logger.success(`Static crop only (${dynamicCropFilter.length} chars)`);
    }
  } else {
    logger.warn('Dynamic crop failed — will use static center crop');
  }
  const cropOffsetX = getCropOffset(analysisClip, analysisDims.width, analysisDims.height, tmpBaseDir);

  // ─── Captions ──────────────────────────────────────────────────
  let subPath = null;
  let translatedText = null;
  if (!captionsEnabled && dialogue.hasDialogue && dialogue.wordCount > 5) {
    logger.info('Captions/subtitles disabled by config — skipping subtitle generation');
  }
  if (captionsEnabled && dialogue.hasDialogue && dialogue.wordCount > 5) {
    const transcript = (dialogue.transcript || '').toLowerCase();
    const words = transcript.split(/\s+/).filter(w => w.length > 0);
    const wordDensity = words.length / Math.min(cut.end - cut.start, 30);
    const isMusic = wordDensity < 1.0 || words.length < 8;

    if (!isMusic) {
      let translateArg = '';
      if (dialogue.language && !['en', 'en', 'english', 'english'].includes(dialogue.language)) {
        try {
          const openrouter = getOpenRouterQA();
          if (openrouter) {
            logger.info(`Translating from ${dialogue.language || 'unknown'}...`);
            const orTranslation = await openrouter.translate(dialogue.transcript);
            if (orTranslation && orTranslation.trim().length > 3) {
              translatedText = orTranslation;
              translateArg = `--translate "${orTranslation.replace(/"/g, '\\"')}"`;
              logger.success(`OpenRouter translation: "${orTranslation.substring(0, 80)}..."`);
            }
          }
        } catch (e) {
          logger.warn(`OpenRouter translation failed: ${(e.message || '').substring(0, 60)}`);
        }

        if (!translatedText) {
          try {
            logger.info('Trying NLLB-200...');
            const nllbOut = execSync(
              `python3 "${path.join(__dirname, '..', 'core', 'nllb-translate.py')}" "${dialogue.transcript}" 2>&1`,
              { timeout: 10800000, encoding: 'utf8' }
            ).toString().trim();
            const nllbResult = JSON.parse(nllbOut.split('\n').filter(l => l.startsWith('{'))[0]);
            if (nllbResult.translated_text) {
              translatedText = nllbResult.translated_text;
              translateArg = `--translate "${translatedText.replace(/"/g, '\\"')}"`;
              logger.success(`NLLB translation: ${translatedText.substring(0, 80)}...`);
            }
          } catch (e) {
            logger.warn(`NLLB translation failed: ${(e.message || '').substring(0, 60)}`);
          }
        }

        if (!translatedText) {
          try {
            logger.info('Trying Gemini translation...');
            const geminiTranslation = await gemini.translate(dialogue.transcript);
            if (geminiTranslation) {
              translatedText = geminiTranslation;
              translateArg = `--translate "${geminiTranslation.replace(/"/g, '\\"')}"`;
              logger.success(`Gemini translation: ${geminiTranslation.substring(0, 80)}...`);
            }
          } catch {}
        }
      }

      if (dialogue.language && !['en', 'en', 'english', 'english'].includes(dialogue.language) && !translatedText && !translateArg) {
        logger.warn(`All translation methods failed — skipping captions for ${dialogue.language} content`);
      } else {
        subPath = path.join(tmpBaseDir, `captions_${Date.now()}.ass`);
        try {
          const captionOut = execSync(`python3 "${path.join(__dirname, '..', 'core', 'tiktok_captions.py')}" "${analysisClip}" "${subPath}" ${translateArg || ''} 2>&1`, { timeout: 120000, encoding: 'utf8' }).toString().trim();
          const captionResult = JSON.parse(captionOut);
          logger.info(`Captions: ${captionResult.word_count} words${translatedText ? ' (dual-language)' : ''}`);
        } catch {
          logger.warn('Captions failed — proceeding without');
          subPath = null;
        }
      }
    }
  }

  try { fs.unlinkSync(analysisClip); } catch {}

  // ─── Phase 5: Redownload + Combined Render ───────────────────
  logger.header('Phase 5: Redownload + Combined Render');

  const freshSourceDir = path.join(tmpBaseDir, 'fresh_source');
  if (!fs.existsSync(freshSourceDir)) fs.mkdirSync(freshSourceDir, { recursive: true });
  const freshPath = downloadBestVideo(bestVideo, freshSourceDir);
  if (!freshPath) {
    logger.error('Redownload failed — aborting');
    try { fs.rmSync(tmpBaseDir, { recursive: true, force: true }); } catch {}
    return { success: false, error: 'Redownload failed' };
  }

  // ─── Watermark ─────────────────────────────────────────────────
  // Applied in a second lossless pass AFTER this render (core/watermark-cover.js):
  // it first looks for the source's own watermark and covers that spot with
  // ours, otherwise it uses the usual bottom-right position.
  const wmImagePath = path.join(__dirname, '..', 'core', 'assets', 'mrw-logo.png');
  const hasWatermark = false;

  // ─── Easter eggs (random tiny cartoon overlays) ────────────────
  // Inputs: 0 = source clip, then egg clips.
  let nextInputIdx = 1;
  let wmInputIdx = -1;
  if (hasWatermark) {
    wmInputIdx = nextInputIdx;
    nextInputIdx++;
  }

  let eggPlan = { eggs: [] };
  try {
    eggPlan = planEasterEggs({ clipDuration, ...config.easterEggs });
  } catch (e) {
    logger.warn(`Easter egg planning failed — rendering without: ${(e.message || '').substring(0, 80)}`);
  }
  const eggFirstInputIdx = eggPlan.eggs.length > 0 ? nextInputIdx : -1;
  nextInputIdx += eggPlan.eggs.length;
  logger.info(`Easter eggs: ${eggPlan.eggs.length > 0 ? eggPlan.eggs.map(e => `${e.id}@${e.startAt}s`).join(', ') : 'none'}`);

  const { filterComplex, videoOut, eggInputArgs } = buildCombinedFilter(cropOffsetX, analysisDims.width, analysisDims.height, !!subPath, subPath, hasWatermark, wmImagePath, wmInputIdx, dynamicCropFilter, eggPlan, eggFirstInputIdx);

  const finalOutput = path.join(tmpBaseDir, `final_${Date.now()}.mkv`);

  let inputs = `-ss ${cut.start} -i "${freshPath}"`;
  if (hasWatermark) inputs += ` -i "${wmImagePath}"`;
  inputs += eggInputArgs || '';
  // Original audio only — egg clips are silent and no voice line is mixed in.
  const audioMap = '-map 0:a?';

  const filterScriptPath = path.join(tmpBaseDir, `filter_${Date.now()}.txt`);
  fs.writeFileSync(filterScriptPath, filterComplex, 'utf8');

  const cmd = `ffmpeg -y ${inputs} -to ${clipDuration} -filter_complex_script "${filterScriptPath}" -map "${videoOut}" ${audioMap} -c:v ffv1 -level 3 -coder 1 -context 1 -g 1 -slices 16 -slicecrc 1 -pix_fmt yuv444p10le -c:a flac -ar 48000 -shortest -strict experimental "${finalOutput}"`;

  logger.info('Running combined render...');
  try {
    execSync(cmd, { timeout: 600000, maxBuffer: 500 * 1024 * 1024 });
    try { fs.unlinkSync(filterScriptPath); } catch {}
    if (fs.existsSync(finalOutput) && fs.statSync(finalOutput).size > 100000) {
      logger.success(`Combined render: ${(fs.statSync(finalOutput).size / 1024 / 1024).toFixed(1)}MB`);
    } else {
      logger.error('Combined render produced no output');
      try { fs.rmSync(tmpBaseDir, { recursive: true, force: true }); } catch {}
      return { success: false, error: 'Render failed' };
    }
  } catch (e) {
    logger.error(`Combined render failed: ${(e.message || '').substring(0, 100)}`);
    try { fs.rmSync(tmpBaseDir, { recursive: true, force: true }); } catch {}
    return { success: false, error: 'Render failed' };
  }

  // ─── Watermark pass (cover the source's mark, or default spot) ─
  let watermarkMode = 'none';
  let watermarkedOutput = finalOutput;
  try {
    const wmOut = path.join(tmpBaseDir, `final_wm_${Date.now()}.mkv`);
    const wmRes = await applyWatermarkPass(finalOutput, wmOut, { gemini, tmpDir: tmpBaseDir });
    if (wmRes.ok) { watermarkedOutput = wmOut; watermarkMode = wmRes.mode; }
    else logger.warn('Watermark pass failed — continuing without watermark');
  } catch (e) {
    logger.warn(`Watermark pass error: ${(e.message || '').substring(0, 80)}`);
  }

  const safeCountry = String(country || 'global').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'global';
  const durableFinalPath = path.join(outputDir, `type1_${safeCountry}_${Date.now()}.mkv`);
  try { fs.copyFileSync(watermarkedOutput, durableFinalPath); } catch (e) { logger.error(`Copy failed: ${e.message}`); try { fs.rmSync(tmpBaseDir, { recursive: true, force: true }); } catch {} return { success: false, error: 'Copy failed' }; }
  if (!fs.existsSync(durableFinalPath) || fs.statSync(durableFinalPath).size < 100000) { logger.error('Final video missing or too small'); try { fs.rmSync(tmpBaseDir, { recursive: true, force: true }); } catch {} return { success: false, error: 'Final video copy failed' }; }

  // ─── Phase 6: QA Review ───────────────────────────────────────
  logger.header('Phase 6: QA Review');
  const validation = await validateOutput(durableFinalPath);
  if (!validation.passed) { logger.warn(`Validation issues: ${validation.issues.join(', ')}`); if (validation.score < 4) { logger.error('Validation score too low -- aborting'); try { fs.rmSync(tmpBaseDir, { recursive: true, force: true }); } catch {} return { success: false, error: `Validation failed: ${validation.issues.join('; ')}` }; } }
  const geminiQA = await geminiReview(durableFinalPath);
  logger.info(`Gemini QA: ${geminiQA.score}/10 -- ${geminiQA.recommendation}`);

  // ─── Phase 7: Generate Metadata ───────────────────────────────
  logger.header('Phase 7: Generate Metadata');
  const metadataContext = {
    reasoning: `Classified as ${country}`,
    searchQuery: bestVideo.title || '',
    hookScore: 5,
    geminiScore: bestVideo.geminiScore || 5,
    editType: 'combined',
    hasCaptions: !!subPath,
    sourceUrl: bestVideo.url || '',
    sourceTitle: bestVideo.title || '',
    viewCount: bestVideo.view_count || 0,
    comments: '',
    hookDescription: `An Asian edit from ${country}`,
  };
  const metadata = await gemini.generateTitle(country, dialogue.transcript, bestVideo.title, metadataContext);
  const fallbackMetadata = buildFallbackMetadata(country, bestVideo, dialogue);
  let title = metadata?.title || fallbackMetadata.title;
  let titleSource = metadata?.title ? 'gemini' : 'fallback';
  let description = metadata?.description || fallbackMetadata.description;
  const tags = metadata?.tags || fallbackMetadata.tags;

  // ─── Self-learning title pick ─────────────────────────────────
  // Gemini returns a title + alternatives; rank them against what has
  // performed on OUR channel (k-NN over past titles + learned patterns).
  try {
    const options = [metadata?.title, ...(Array.isArray(metadata?.titleOptions) ? metadata.titleOptions : [])]
      .filter(t => typeof t === 'string' && t.trim().length > 5);
    if (performanceInsights && performanceInsights.reliable && options.length > 1) {
      const ranked = rankTitles(options, performanceInsights);
      logger.info(`Title ranking: ${ranked.map(r => `"${r.title.substring(0, 40)}"=${r.score}`).join(' | ')}`);
      if (ranked.length && ranked[0].title !== title) {
        logger.success(`Learned pick: "${ranked[0].title}" over "${title}"`);
        title = ranked[0].title;
        titleSource = 'ranked';
      }
    }
  } catch (e) {
    logger.warn(`Title ranking skipped: ${(e.message || '').substring(0, 80)}`);
  }

  // ─── Phase 8: Daily Roulette Intro ────────────────────────────
  logger.header('Phase 8: Building Daily Random Roulette intro...');
  const hookDescription = `An Asian edit from ${country}`;
  const channelHandle = process.env.YOUTUBE_HANDLE || '@Mr.WorldWideWebster';
  const todayLine = await gemini.generateRouletteTodayLine(country, hookDescription, bestVideo.title);
  const todayFallback = `Today we have a fresh Asian edit from ${country}!`;
  const rouletteText = (todayLine && todayLine.trim().length > 20) ? todayLine.trim() : todayFallback;

  const rouletteHeader = `🎌 Asian Edits Daily 🎌
Every day a fresh Asian edit — K-pop, anime, dance and aesthetic clips from across Asia. Curated edits that hit different, guaranteed.

${rouletteText}

If you want daily Asian edits, make sure to subscribe to ${channelHandle}!`;

  description = `${rouletteHeader}\n\n---\n\n${description}`;

  // ─── Save Memory ──────────────────────────────────────────────
  if (!memory.usedVideoIds) memory.usedVideoIds = [];
  memory.usedVideoIds.push(bestVideo.id);
  memory.lastRun = new Date().toISOString();
  saveChannelMemory(memory);

  // Cleanup
  try { fs.rmSync(tmpBaseDir, { recursive: true, force: true }); } catch {}

  logger.header('PIPELINE COMPLETE');
  logger.success(`Video: ${durableFinalPath}`);
  logger.success(`Title: ${title}`);
  logger.success(`Country: ${country}`);
  logger.success(`Source: @${bestVideo.channelHandle}`);

  return {
    success: true, videoPath: durableFinalPath, title, description, tags, country,
    geminiScore: bestVideo.geminiScore || 5, editType: 'combined', hasCaptions: !!subPath,
    sourceUrl: bestVideo.url, sourceChannel: bestVideo.channelHandle, rouletteIntro: rouletteText,
    // bookkeeping for the performance tracker / dedup
    sourceTitle: bestVideo.title || '',
    summary: winner.summary || '',
    category: winner.category || 'other',
    eggs: eggPlan.eggs.map(e => e.id),
    titleSource,
    watermarkMode,
  };
}

module.exports = { runType1Pipeline, buildCombinedFilter, pickLeastCoveredCandidate };