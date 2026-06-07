/**
 * Type 1 Pipeline — Meme/Trend/Clip Short
 * 
 * The main pipeline for finding viral clips from around the world
 * and reposting them with minimal, smart edits.
 * 
 * Flow:
 * 1. Pick country → Load trend bank → Generate queries
 * 2. Search YouTube → Download candidates
 * 3. Gemini ranks URLs (API) → Pick best video
 * 4. Download best video → Smart Cut → Analyze temp
 * 5. Redownload → Combined FFmpeg: Cut → Dynamic Crop → Captions → Sig → Wm → FFV1
 * 6. QA review → Upload
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('../core/logger');
const { getGeminiService } = require('../core/gemini-service');
const { getGeminiCLI } = require('../core/gemini-cli-runner');
const { getOpenRouterQA } = require('../core/openrouter-qa');
const { probeVideo, extractFrames, generateDynamicCropFilter, computeCropDimensions } = require('../core/smart-cropper');
const { smartClipAndCrop } = require('../core/ai-clipper');
const { smartEdit, detectDialogue } = require('../core/smart-editor');
const { validateOutput, geminiReview } = require('../core/frame-qa');
const { addWatermark } = require('../core/watermark');

const logger = new Logger('Type1Pipeline');

const SHORTS_W = 1080;
const SHORTS_H = 1920;

const COUNTRY_QUERY_PRESETS = {
  China: ['Chinese waiter dance Douyin', 'Chinese street dance Douyin', 'Chinese public square dance viral', 'Douyin Subject Three dance China', 'Kemusan dance Guangxi China', 'Chinese group livestream dance', 'Tuanbo group dance China', 'Chinese mall dance performance', 'Chinese school dance performance Douyin', 'Chinese traditional hanfu dance Douyin', 'Chinese festival dance street', 'Kuaishou Chinese comedy skit', 'Bilibili Chinese dance cover', 'Xiaohongshu Chinese street fashion dance', 'Chinese security guard dancing', 'Chinese delivery guy dancing', 'Chinese uncle auntie dancing', 'Chinese village dance viral', 'Chinese Douyin funny moments'],
  Japan: ['Japanese game show funny moments', 'Japan convenience store viral', 'Japanese street food funny vendor', 'Tokyo street interview funny', 'Shibuya street fashion viral', 'Japanese arcade funny moments', 'Japanese mascot funny moments', 'NicoNico dance cover Japan', 'Japanese train station funny moments', 'Japanese school festival performance', 'Japan vending machine weird viral', 'Japanese ramen chef viral', 'Japanese dance challenge public', 'Japanese comedy skit viral', 'Japan 2000s nostalgic edit'],
  'South Korea': ['Korean street interview funny', 'Korean convenience store viral', 'Korean street food funny moment', 'Korean public random play dance', 'K-pop dance challenge Korea', 'Hongdae busking dance viral', 'Korean couple comedy skit', 'Korean school performance viral', 'Korean mukbang funny moments', 'Korean beauty transformation funny', 'Korean gym comedy skit', 'Korean cafe worker funny', 'Korean drama meme short', 'Korean auntie funny moment', 'Seoul street fashion viral'],
  UK: ['British comedy skit viral', 'UK roadman funny moments', 'London street interview funny', 'British pub banter viral', 'UK drill meme compilation', 'British school funny moments', 'UK football banter funny', 'British roast battle short', 'Manchester funny street interview', 'London chicken shop funny', 'British train station funny moment', 'UK vs US slang funny', 'British grandma funny moment', 'UK council estate comedy skit', 'British weather meme short'],
  Nigeria: ['Nigerian comedy skit viral', 'Nollywood reaction meme short', 'Nigerian wedding dance viral', 'Lagos street interview funny', 'Nigerian auntie funny moments', 'Nigerian pastor funny meme', 'Nigerian market funny moment', 'Afrobeats dance challenge Nigeria', 'Nigerian school comedy skit', 'Nigerian street food funny', 'Nigerian relationship comedy short', 'Nigerian TikTok funny moments', 'African comedy skit Nigeria', 'Lagos traffic funny moments', 'Nigerian mum funny skit'],
  Africa: ['African comedy skit viral', 'funniest African videos compilation', 'African dance challenge viral', 'Amapiano dance challenge South Africa', 'Nigerian comedy skit viral', 'Ghanaian comedy skit viral', 'South African taxi funny moments', 'African wedding dance viral', 'Nollywood reaction meme short', 'African auntie funny moments', 'African street interview funny', 'Afrobeats dance challenge', 'African school comedy skit', 'African market funny moment', 'African football celebration funny'],
  'South Africa': ['South African comedy skit viral', 'Amapiano dance challenge South Africa', 'South African taxi funny moments', 'Cape Town street interview funny', 'Mzansi funny moments', 'South African school dance viral', 'South African wedding dance viral', 'South African football fan funny', 'South African auntie funny moments', 'South African TikTok funny moments'],
  India: ['Indian street food funny vendor', 'Bollywood dance challenge viral', 'Indian wedding dance viral', 'Indian comedy skit viral', 'Mumbai street interview funny', 'Indian cricket fan funny moments', 'Indian train funny moment', 'Indian school performance viral', 'Indian auntie funny skit', 'Indian street performer viral'],
  Brazil: ['Brazilian funk dance challenge', 'Brazil football skills funny', 'Brazilian favela comedy skit', 'Brazil beach funny moments', 'Brazilian street interview funny', 'Brazilian WhatsApp meme viral', 'Brazil samba dance viral', 'Brazilian food vendor funny', 'Brazilian football fan funny', 'Brazilian comedy short viral'],
  Mexico: ['Mexican comedy skit viral', 'Mexican street food funny vendor', 'Mexico cumbia dance challenge', 'Mexican soccer fan funny moments', 'Mexican mom funny skit', 'Mexican street interview funny', 'Mexican quinceanera dance viral', 'Mexican lucha libre funny moments', 'Mexican banda dance viral', 'Mexican relationship comedy short'],
  France: ['French comedy skit viral', 'Paris street interview funny', 'French bakery viral moment', 'French fashion street viral', 'French protest funny moments', 'French cafe funny moment', 'French football fan funny', 'French street food viral'],
  Germany: ['German comedy skit viral', 'Germany Autobahn funny moments', 'German efficiency meme short', 'German street interview funny', 'German football fan funny', 'German Christmas market viral', 'German train station funny moment', 'German beer festival funny'],
  Italy: ['Italian comedy skit viral', 'Italian grandma funny moments', 'Italian street food viral', 'Italian hand gestures funny', 'Italian football fan funny', 'Rome street interview funny', 'Italian pasta chef viral', 'Italian family comedy skit'],
  Spain: ['Spanish comedy skit viral', 'Spain football fan funny', 'Spanish street interview funny', 'Flamenco street performance viral', 'Spanish tapas funny moment', 'Madrid funny street interview', 'Spanish beach funny moments', 'Spanish family comedy short'],
  Thailand: ['Thai street food funny vendor', 'Thailand comedy skit viral', 'Thai market funny moment', 'Songkran funny moments', 'Muay Thai funny moment', 'Thailand tuk tuk funny', 'Thai school performance viral', 'Thai dance challenge viral'],
  Vietnam: ['Vietnamese street food funny vendor', 'Vietnam motorbike traffic funny', 'Vietnamese comedy skit viral', 'Vietnam school performance viral', 'Vietnamese market funny moment', 'Vietnamese dance challenge viral', 'Hanoi street interview funny', 'Vietnam cafe viral moment'],
  Indonesia: ['Indonesian comedy skit viral', 'Indonesia dangdut dance viral', 'Indonesian street food funny', 'Indonesian live selling funny', 'Jakarta street interview funny', 'Indonesian school comedy skit', 'Indonesian family funny moments', 'Indonesian TikTok viral funny'],
  Egypt: ['Egyptian comedy skit viral', 'Cairo street interview funny', 'Egyptian wedding dance viral', 'Egyptian street food funny', 'Egyptian football fan funny', 'Egyptian market funny moment', 'Egyptian taxi funny moments', 'Egyptian TikTok funny moments'],
  Turkey: ['Turkish ice cream prank viral', 'Turkish barber funny moments', 'Istanbul street food viral', 'Turkish comedy skit viral', 'Turkish bazaar funny moment', 'Turkish tea funny moments', 'Turkish wedding dance viral', 'Turkish street interview funny'],
  Colombia: ['Colombian comedy skit viral', 'Colombia salsa dance viral', 'Medellin street interview funny', 'Colombian football fan funny', 'Colombian street food funny', 'Colombian family comedy short'],
  Peru: ['Peruvian comedy skit viral', 'Peru street food funny', 'Peruvian dance viral', 'Lima street interview funny', 'Peruvian market funny moment', 'Peruvian football fan funny'],
  Argentina: ['Argentinian comedy skit viral', 'Argentina football fan funny', 'Buenos Aires street interview funny', 'Argentinian mate funny moment', 'Argentinian tango street viral', 'Argentinian roast comedy short'],
  Chile: ['Chilean comedy skit viral', 'Chile street interview funny', 'Chilean football fan funny', 'Chilean slang funny moments', 'Chile completo food funny', 'Chilean family comedy short'],
  Portugal: ['Portuguese comedy skit viral', 'Lisbon street interview funny', 'Portugal football fan funny', 'Portuguese street food viral', 'Portuguese grandma funny moments', 'Portugal surf funny moments'],
  Russia: ['Russian dashcam funny moments', 'Russian comedy skit viral', 'Russian hardbass funny moments', 'Russian street interview funny', 'Russian winter funny moments', 'Russian invention funny viral'],
  Canada: ['Canadian comedy skit viral', 'Canada winter funny moments', 'Toronto street interview funny', 'Canadian hockey fan funny', 'Canadian food review funny', 'Canadian politeness meme short'],
  Australia: ['Australian comedy skit viral', 'Australian tradie funny moments', 'Australia beach funny moments', 'Australian accent funny interview', 'Sydney street interview funny', 'Australian football fan funny'],
};

const COUNTRY_RELEVANCE = {
  China: { positive: ['china', 'chinese', 'douyin', 'bilibili', 'kuaishou', 'xiaohongshu', 'hanfu', 'guangxi', 'kemusan', 'subject three', 'tuanbo', 'mandarin', 'beijing', 'shanghai', 'sichuan', 'cantonese', 'waiter', 'restaurant', 'street dance', 'square dance'], negative: ['india', 'bollywood', 'chammak', 'shambhavi', 'xml status', 'alight motion', 'moliy', 'silent addy', 'shake it to the max', 'sweet body', 'hot yoga', 'loli', 'no bra', 'onlyfans'] },
  Japan: { positive: ['japan', 'japanese', 'tokyo', 'shibuya', 'osaka', 'anime', 'ramen', 'niconico', 'kawaii', 'arcade', 'mascot'], negative: ['china', 'chinese', 'korea', 'korean', 'india', 'bollywood', 'onlyfans', 'no bra'] },
  'South Korea': { positive: ['korea', 'korean', 'seoul', 'hongdae', 'k-pop', 'kpop', 'kimchi', 'mukbang', 'kdrama', 'k-drama'], negative: ['japan', 'japanese', 'china', 'chinese', 'india', 'bollywood', 'onlyfans', 'no bra'] },
  UK: { positive: ['uk', 'british', 'england', 'english', 'london', 'manchester', 'roadman', 'drill', 'pub', 'football', 'britain'], negative: ['america', 'american', 'india', 'bollywood', 'onlyfans', 'no bra'] },
  Nigeria: { positive: ['nigeria', 'nigerian', 'lagos', 'afrobeats', 'nollywood', 'yoruba', 'igbo', 'african'], negative: ['india', 'bollywood', 'america', 'american', 'onlyfans', 'no bra'] },
  Africa: { positive: ['africa', 'african', 'nigeria', 'nigerian', 'ghana', 'ghanaian', 'kenya', 'south africa', 'amapiano', 'afrobeats', 'nollywood'], negative: ['india', 'bollywood', 'america', 'american', 'onlyfans', 'no bra'] },
  'South Africa': { positive: ['south africa', 'south african', 'mzansi', 'amapiano', 'cape town', 'johannesburg', 'pretoria'], negative: ['india', 'bollywood', 'america', 'american', 'onlyfans', 'no bra'] },
};

// Universal hashtags for cross-platform reach — target people who DON'T know this content yet
const UNIVERSAL_HASHTAGS = '#shorts #tiktok #reels #instagram';

function buildCountryHashtags(country) {
  // Always include the country so people curious about that place find it
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

const COUNTRY_ALIASES = { Korea: 'South Korea', 'United Kingdom': 'UK', Britain: 'UK', England: 'UK', African: 'Africa' };

function countryKey(country) { return COUNTRY_ALIASES[country] || country; }

function isGenericCountryQuery(query, country) {
  const normalized = query.trim().toLowerCase();
  const names = [country, countryKey(country)].filter(Boolean).flatMap(name => { const lower = String(name).toLowerCase(); return lower === 'south korea' ? [lower, 'korea', 'korean'] : [lower]; });
  const genericTails = ['', 'viral', 'dance', 'shorts', 'funny', 'music', 'best', 'trends'];
  return names.some(name => genericTails.some(tail => normalized === `${name}${tail ? ` ${tail}` : ''}`));
}

function isCountryRelevantCandidate(candidate, country) {
  const rules = COUNTRY_RELEVANCE[countryKey(country)];
  if (!rules) return true;
  const text = [candidate.title, candidate.description, candidate.channel, candidate.searchQuery].filter(Boolean).join(' ').toLowerCase();
  if (rules.negative.some(term => text.includes(term))) return false;
  return rules.positive.some(term => text.includes(term));
}

function buildFallbackMetadata(country, bestVideo, dialogue) {
  const reasoning = (bestVideo.reasoning || '').toLowerCase();
  const sourceTitle = bestVideo.title || '';
  const profile = COUNTRY_METADATA_PROFILES[countryKey(country)] || { titleBase: country, tags: [country.toLowerCase(), 'viral', 'funny'], hashtags: buildCountryHashtags(country) };
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
  return { title: hook.substring(0, 50), description: `${descriptionHook}\n\nWould you stop and watch this?\n\n${profile.hashtags}`, tags: ['shorts', 'viral', 'mr worldwidewebster'].concat(profile.tags).concat(shortTranscript ? ['global trends'] : []) };
}

async function fetchTopComments(url, maxComments = 3) {
  try {
    const cmd = `yt-dlp --write-comments --extractor-args "youtube:max_comments=${maxComments},comment_sort=top" --dump-json --no-download "${url}" 2>&1`;
    const out = execSync(cmd, { timeout: 20000, maxBuffer: 2 * 1024 * 1024 }).toString().trim();
    if (!out || out.includes('ERROR') || out.includes('WARNING')) return [];
    const meta = JSON.parse(out.split('\n')[0]);
    return (meta.comments || []).slice(0, maxComments).filter(c => c.text).map(c => ({ text: (c.text || '').substring(0, 200), likes: c.like_count || 0, author: (c.author || 'Unknown').substring(0, 30) }));
  } catch { return []; }
}

function loadTrendBank(country) {
  const fileName = country.toLowerCase().replace(/ /g, '-');
  const bankPath = path.join(__dirname, '..', 'config', 'trend-banks', `${fileName}.json`);
  if (fs.existsSync(bankPath)) {
    try {
      const bank = JSON.parse(fs.readFileSync(bankPath, 'utf8'));
      const activeKeywords = bank.keywords.filter(k => k.status === 'active').map(k => k.term);
      logger.info(`Loaded trend bank for ${country}: ${activeKeywords.length} keywords`);
      return { keywords: activeKeywords, suffix: bank.querySuffix, songs: bank.trendingSongs || [] };
    } catch (e) { logger.warn(`Failed to load trend bank for ${country}: ${e.message}`); }
  }
  return { keywords: [country.toLowerCase()], suffix: '#shorts', songs: [] };
}

async function generateQueries(country, gemini, trendBank) {
  const queries = await gemini.generateQueries(country, trendBank.keywords, 20);
  const allQueries = [];
  const key = countryKey(country);
  const presetQueries = COUNTRY_QUERY_PRESETS[key] || [];
  for (const q of [...presetQueries, ...(Array.isArray(queries) ? queries : [])]) {
    if (typeof q !== 'string') continue;
    if (presetQueries.length > 0 && isGenericCountryQuery(q, country)) continue;
    const clean = q.replace(/#\w+/g, '').trim();
    if (!clean) continue;
    const query = `${clean} ${trendBank.suffix}`;
    if (!allQueries.includes(query)) allQueries.push(query);
  }
  logger.info(`Preset+LLM queries: ${allQueries.length} queries`);
  logger.success(`Total queries generated: ${allQueries.length}`);
  return allQueries;
}

async function searchYouTube(queries, targetCount = 15, country = null) {
  const seen = new Set();
  const cookieArg = fs.existsSync('/tmp/yt_cookies.txt') ? '--cookies "/tmp/yt_cookies.txt"' : '';
  const allResults = [];
  for (const query of queries) {
    logger.info(`Searching for: "${query}"`);
    try {
      const searchCmd = `yt-dlp --flat-playlist --dump-json "ytsearch100:${query}" 2>&1`;
      const out = execSync(searchCmd, { timeout: 30000, maxBuffer: 10 * 1024 * 1024 }).toString().trim();
      if (!out) { logger.info('No results from this query'); continue; }
      const lines = out.split('\n').filter(Boolean);
      logger.info(`Raw results: ${lines.length} videos`);
      const shorts = []; const nonShorts = [];
      for (const line of lines) {
        try {
          const p = JSON.parse(line);
          if (p.id) {
            if (p.duration && p.duration <= 60) {
              const height = p.height || 0;
              const tier = height >= 1080 ? 3 : height >= 720 ? 2 : height >= 480 ? 1 : 0;
              shorts.push({ line, tier, height });
            } else { nonShorts.push(line); }
          }
        } catch {}
      }
      shorts.sort((a, b) => b.tier - a.tier);
      const shuffledShorts = [];
      for (let tier = 3; tier >= 0; tier--) {
        const tierItems = shorts.filter(s => s.tier === tier).map(s => s.line);
        for (let i = tierItems.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [tierItems[i], tierItems[j]] = [tierItems[j], tierItems[i]]; }
        shuffledShorts.push(...tierItems);
      }
      for (let i = nonShorts.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [nonShorts[i], nonShorts[j]] = [nonShorts[j], nonShorts[i]]; }
      const tierCounts = { '1080p': shorts.filter(s => s.tier >= 3).length, '720p': shorts.filter(s => s.tier === 2).length, 'lower': shorts.filter(s => s.tier < 2).length };
      logger.info(`Shorts quality: ${tierCounts['1080p']} 1080p, ${tierCounts['720p']} 720p, ${tierCounts['lower']} lower`);
      const selectedFromShorts = shuffledShorts.slice(0, 5);
      const selectedFromNonShorts = nonShorts.slice(0, Math.max(0, 5 - selectedFromShorts.length));
      const selected = [...selectedFromShorts, ...selectedFromNonShorts];
      logger.info(`Selected: ${selectedFromShorts.length} shorts + ${selectedFromNonShorts.length} non-shorts`);
      let addedFromQuery = 0;
      for (const line of selected) {
        try {
          const p = JSON.parse(line);
          if (p.id && !seen.has(p.id)) {
            seen.add(p.id);
            const metaCmd = `yt-dlp ${cookieArg} --dump-json --no-download "https://www.youtube.com/watch?v=${p.id}" 2>&1`;
            let metaOut;
            try { metaOut = execSync(metaCmd, { timeout: 15000, maxBuffer: 1024 * 1024 }).toString().trim(); } catch {}
            let meta = {};
            if (metaOut && !metaOut.includes('ERROR')) { try { meta = JSON.parse(metaOut.split('\n')[0]); } catch {} }
            allResults.push({ id: p.id, url: `https://www.youtube.com/watch?v=${p.id}`, shortsUrl: `https://www.youtube.com/shorts/${p.id}`, title: meta.title || p.title || 'YouTube video', duration: meta.duration || p.duration || 0, searchQuery: query, view_count: meta.view_count || p.view_count || 0, channel_follower_count: meta.channel_follower_count || 0, like_count: meta.like_count || 0, comment_count: meta.comment_count || 0, channel: meta.channel || meta.uploader || p.channel || 'Unknown', description: (meta.description || '').substring(0, 300), upload_date: meta.upload_date || p.upload_date || '' });
            addedFromQuery++;
          }
        } catch {}
      }
      if (addedFromQuery > 0) logger.info(`  --> ${addedFromQuery} enriched candidates from this query`);
    } catch (e) { logger.warn(`Search failed for "${query}": ${(e.message || '').substring(0, 200)}`); }
  }
  for (let i = allResults.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [allResults[i], allResults[j]] = [allResults[j], allResults[i]]; }
  const finalResults = allResults.slice(0, targetCount);
  logger.success(`Search complete: ${allResults.length} raw candidates collected; randomly selected ${finalResults.length} for Gemini`);
  return finalResults;
}

function filterCandidates(candidates) { logger.info(`No pre-Gemini filter: passing ${candidates.length} random raw candidates to Gemini`); return candidates; }

/**
 * Run quality check on a video file using video-quality.py
 * @param {string} videoPath - Path to the downloaded clip
 * @returns {Object} - { verdict, laplacian_avg, musiq_avg, edge_density_avg, reasons }
 */
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

async function rankSingleVideo(candidate, country, gemini, geminiCLI, curatorSkill, tmpDir) {
  const ageInDays = candidate.upload_date ? Math.max(1, Math.floor((Date.now() - new Date(candidate.upload_date.substring(0, 4), candidate.upload_date.substring(4, 6) - 1, candidate.upload_date.substring(6, 8)).getTime()) / 86400000)) : 30;
  const engagementData = { views: candidate.view_count || 0, likes: candidate.like_count || 0, comments: candidate.comment_count || 0, ageInDays, title: candidate.title || 'YouTube video', topComments: candidate.topComments || [] };
  logger.info(`  Engagement: ${engagementData.views} views, ${engagementData.likes} likes, ${engagementData.comments} comments, ${engagementData.ageInDays}d old`);

  const url = candidate.shortsUrl || candidate.url;
  const rankFile = path.join(tmpDir, `rank_${Date.now()}.mp4`);
  const qualityDir = path.join(tmpDir, `quality_${Date.now()}`);
  if (!fs.existsSync(qualityDir)) fs.mkdirSync(qualityDir, { recursive: true });

  // Step 1: Download BOTH clips IN PARALLEL (720p for Gemini + best quality for quality check)
  logger.info(`  Step 1 -- downloading ranking clip + best quality in parallel...`);
  const hasCookies = fs.existsSync('/tmp/yt_cookies.txt');
  const cookieArg = hasCookies ? '--cookies "/tmp/yt_cookies.txt"' : '';

  const downloadPromises = [];

  // Promises A: 720p ranking clip (8-20s segment, fast)
  const rankPromise = (async () => {
    const strategies = [
      { name: 'ranking_web', args: '--extractor-args "youtube:player_client=web"', format: '-f "bestvideo[height<=720]+bestaudio/best" --merge-output-format mp4', sections: '--download-sections "*8-20"' },
      { name: 'ranking_default', args: '', format: '-f "bestvideo[height<=720]+bestaudio/best" --merge-output-format mp4', sections: '--download-sections "*8-20"' },
    ];
    for (const s of strategies) {
      try {
        const cmd = `yt-dlp ${cookieArg} ${s.args} ${s.format} ${s.sections} -o "${rankFile}" "${url}" --no-playlist --socket-timeout 30 --retries 2 --force-ipv4 --remote-components ejs:github 2>&1`;
        execSync(cmd, { timeout: 120000, maxBuffer: 50 * 1024 * 1024 });
        if (fs.existsSync(rankFile) && fs.statSync(rankFile).size > 50000) return rankFile;
      } catch (e) { logger.warn(`  Ranking dl ${s.name} failed: ${(e.message || '').substring(0, 60)}`); }
    }
    return null;
  })();

  // Promises B: Best quality download (for quality assessment)
  const qualityPromise = (async () => {
    const outputStem = `source`;
    const outputTemplate = path.join(qualityDir, `${outputStem}.%(ext)s`);
    const strategies = [
      { name: 'web_best', args: '--extractor-args "youtube:player_client=web"', format: '-f "bestvideo+bestaudio/best" --merge-output-format mkv' },
      { name: 'default_best', args: '', format: '-f "bestvideo+bestaudio/best" --merge-output-format mkv' },
      { name: 'android_best', args: '--extractor-args "youtube:player_client=android"', format: '-f "bestvideo+bestaudio/best" --merge-output-format mkv' },
      { name: 'fallback_mp4', args: '', format: '-f "bestvideo+bestaudio/best" --merge-output-format mp4' },
    ];
    for (const s of strategies) {
      try {
        const cArg = (hasCookies && !s.name.includes('android')) ? cookieArg : '';
        const cmd = `yt-dlp ${cArg} ${s.args} ${s.format} -o "${outputTemplate}" "${url}" --no-playlist --socket-timeout 30 --retries 3 --force-ipv4 --remote-components ejs:github 2>&1`;
        execSync(cmd, { timeout: 180000, maxBuffer: 200 * 1024 * 1024 });
        const files = fs.readdirSync(qualityDir).filter(f => f.startsWith(outputStem) && (f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mkv')) && fs.statSync(path.join(qualityDir, f)).size > 50000).sort((a, b) => fs.statSync(path.join(qualityDir, b)).mtimeMs - fs.statSync(path.join(qualityDir, a)).mtimeMs);
        if (files.length > 0) return path.join(qualityDir, files[0]);
      } catch (e) { logger.warn(`  Quality dl ${s.name} failed: ${(e.message || '').substring(0, 60)}`); }
    }
    return null;
  })();

  const [rankPath, qualityPath] = await Promise.all([rankPromise, qualityPromise]);

  if (!rankPath) { logger.warn(`  Ranking download failed -- cannot rank this video`); try { fs.rmSync(qualityDir, { recursive: true, force: true }); } catch {} return null; }
  logger.info(`  Step 2 -- Analyzing MP4 with Gemini API...`);

  let result = await gemini.rankVideoFile(rankPath, country, curatorSkill, engagementData);
  if (result === null && geminiCLI && geminiCLI.isAvailable()) {
    logger.info(`  Step 3 -- API failed, trying Gemini CLI with local file ref...`);
    result = await geminiCLI.rankVideoFromPath(rankPath, country, curatorSkill, engagementData);
  }

  // Clean up ranking clip
  try { fs.unlinkSync(rankPath); } catch {}

  if (result === null) { logger.warn(`  All ranking methods failed -- skipping`); try { fs.rmSync(qualityDir, { recursive: true, force: true }); } catch {} return null; }

  // ------------------------------------------------------------------
  // Quality check on BEST quality file (not the 720p segment)
  // Runs Laplacian + MUSIQ + edge density
  // ------------------------------------------------------------------
  let qualityPenalty = 0;
  let qualityMetrics = null;
  if (qualityPath && fs.existsSync(qualityPath) && fs.statSync(qualityPath).size > 50000) {
    const quality = assessVideoQuality(qualityPath);
    logger.info(`  Quality (best): lap=${quality.laplacian_avg} musiq=${quality.musiq_avg} edge=${quality.edge_density_avg} → ${quality.verdict.toUpperCase()}`);

    qualityMetrics = {
      laplacian: quality.laplacian_avg,
      musiq: quality.musiq_avg,
      edge_density: quality.edge_density_avg,
    };

    // Apply -4 penalty if quality fails (instead of auto-reject)
    if (quality.verdict === 'reject') {
      const reasons = (quality.rejection_reasons || []).join(', ');
      qualityPenalty = 4;
      logger.warn(`  --> Quality penalty: -${qualityPenalty} (${reasons})`);
    }
    // Clean up quality file
    try { fs.rmSync(qualityDir, { recursive: true, force: true }); } catch {}
  }

  // ------------------------------------------------------------------
  // Apply score: Gemini score reduced by quality penalty
  // ------------------------------------------------------------------
  const originalScore = result.score || 0;
  let finalScore = Math.max(1, originalScore - qualityPenalty);

  // Add shorts boost: +0.5 if video is <= 60s (short-form content is preferred)
  if (candidate.duration > 0 && candidate.duration <= 60) {
    finalScore = Math.min(10, finalScore + 0.5);
    logger.info(`  --> Shorts bonus: +0.5 (duration: ${candidate.duration}s ≤ 60s)`);
  }
  result.quality_penalty = qualityPenalty;
  result.original_score = originalScore;
  result.score = finalScore;
  result.quality_metrics = qualityMetrics;
  result.quality_rejection = qualityPenalty > 0 ? (qualityMetrics ? `lap=${qualityMetrics.laplacian}, edge=${qualityMetrics.edge_density}` : 'quality_fail') : '';

  logger.info(`  Final score: ${finalScore}/10 (original: ${originalScore}${qualityPenalty > 0 ? `, penalty: -${qualityPenalty}` : ''})`);

  return { result, candidate };
}

async function rankVideos(candidates, country, gemini, geminiCLI, curatorSkill, tmpDir) {
  const ranked = [];
  const rejected = []; // Collect rejected videos too, sorted by score desc
  const sorted = [...candidates].sort((a, b) => b.view_count - a.view_count).slice(0, 15);
  for (const candidate of sorted) {
    logger.info(`Ranking: "${candidate.title.substring(0, 50)}" (${(candidate.view_count / 1000000).toFixed(1)}M views)`);
    const out = await rankSingleVideo(candidate, country, gemini, geminiCLI, curatorSkill, tmpDir);
    if (out === null) { logger.warn(`  --> No valid ranking obtained for this video`); }
    else if (out.result.verdict === 'APPROVED' && out.result.score >= 6) {
      ranked.push({ ...out.candidate, geminiScore: Math.min(10, Math.max(1, out.result.score)), hookScore: out.result.hook_score || 5, geminiCountry: out.result.country || country, watermarkType: out.result.watermark_type, reasoning: out.result.reasoning || '' });
      logger.success(`  Score: ${out.result.score}/10 -- ${out.result.reasoning}`);
    } else {
      // Track rejected videos by Gemini score for fallback use
      const rejectionScore = out.result?.score || 0;
      rejected.push({ ...out.candidate, geminiScore: rejectionScore, hookScore: out.result?.hook_score || 1, geminiCountry: country, watermarkType: out.result?.watermark_type, reasoning: out.result?.reasoning || 'No ranking' });
      logger.info(`  Rejected (score: ${out.result.score}) -- ${out.result.reasoning}`);
    }
    await new Promise(r => setTimeout(r, 10000));
  }
  ranked.sort((a, b) => b.geminiScore - a.geminiScore);
  rejected.sort((a, b) => b.geminiScore - a.geminiScore);
  logger.success(`Ranked: ${ranked.length} approved videos, ${rejected.length} rejected`);
  return { ranked, rejected };
}

function probeDownloadedVideo(videoPath) {
  try {
    // Use stream for width/height, but format=duration for correct duration (stream=duration can be wrong for MKV with opus)
    const out = execSync(`ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "${videoPath}"`, { timeout: 10000, encoding: 'utf8' }).trim();
    const [width, height] = out.split(',').map(s => Number.parseFloat(s.trim()));
    // Get duration from format level (reliable for all container types including MKV with opus)
    let duration = 0;
    try {
      const durOut = execSync(`ffprobe -v error -show_entries format=duration -of csv=p=0 "${videoPath}"`, { timeout: 10000, encoding: 'utf8' }).trim();
      duration = Number.parseFloat(durOut);
    } catch {}
    if (Number.isFinite(width) && Number.isFinite(height)) return { width: Math.round(width), height: Math.round(height), duration: Number.isFinite(duration) ? duration : 0 };
  } catch {}
  return { width: 0, height: 0, duration: 0 };
}

async function downloadBestVideo(video, outputDir) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const outputStem = `source_${Date.now()}`;
  const outputTemplate = path.join(outputDir, `${outputStem}.%(ext)s`);
  const url = video.shortsUrl || video.url;
  logger.info(`Downloading: ${url}`);
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
        const dims = probeDownloadedVideo(fp);
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

/**
 * Smart Cut -- Use PySceneDetect + YOLO to find the best segment in the video
 * Returns { start, end } for use in the combined render
 */
function smartCut(videoPath, duration) {
  logger.info('Smart Cut: analyzing video for best segment...');
  
  // MKV with opus audio causes PySceneDetect to crash. Create a lightweight MP4 probe
  // for the highlight detector, keeping the MKV for rendering.
  let probePath = videoPath;
  const isMkv = videoPath.endsWith('.mkv') || videoPath.endsWith('.webm');
  if (isMkv) {
    probePath = videoPath.replace(/\.\w+$/, `_probe_${Date.now()}.mp4`);
    try {
      logger.info('Creating MP4 probe for highlight detection (avoids opus/MKV incompatibility)...');
      execSync(`ffmpeg -y -i "${videoPath}" -t 120 -vf scale=640:-1 -c:v libx264 -preset ultrafast -crf 23 -c:a aac -b:a 64k "${probePath}"`, { timeout: 60000 });
      if (!(fs.existsSync(probePath) && fs.statSync(probePath).size > 50000)) {
        probePath = videoPath; // fallback to original
      } else {
        logger.info(`Probe MP4 created: ${(fs.statSync(probePath).size / 1024 / 1024).toFixed(1)}MB`);
      }
    } catch {
      probePath = videoPath; // fallback to original
    }
  }

  // Use highlight detector if available, else fallback to center
  try {
    const hlPath = path.join(__dirname, '..', 'core', 'highlight-detector.py');
    if (fs.existsSync(hlPath)) {
      logger.info(`Running highlight detector: ${hlPath}`);
      try {
        const pyVer = execSync(`python3 --version`, { timeout: 5000, encoding: 'utf8' }).toString().trim();
        logger.info(`Python: ${pyVer}`);
      } catch (pyErr) {
        logger.warn(`Python check failed: ${(pyErr.message || '').substring(0, 100)}`);
      }
      try {
        const sdCheck = execSync(`python3 -c "from scenedetect import open_video, SceneManager; from scenedetect.detectors import ContentDetector; print('scenedetect OK')"`, { timeout: 10000, encoding: 'utf8' }).toString().trim();
        logger.info(`PySceneDetect: ${sdCheck}`);
      } catch (sdErr) {
        logger.warn(`PySceneDetect not available: ${(sdErr.stderr || sdErr.message || sdErr.stdout || '').toString().substring(0, 200)}`);
      }
      const hlCmd = `python3 "${hlPath}" "${probePath}" --output-json 2>&1`;
      logger.info(`Running: python3 "${hlPath}" "${probePath}" --output-json`);
      const hlOut = execSync(hlCmd, { timeout: 300000, maxBuffer: 10 * 1024 * 1024, encoding: 'utf8' }).toString().trim();
      const outLines = hlOut.split('\n').filter(Boolean);
      for (const line of outLines.slice(0, -1)) {
        logger.info(`  [highlight-detector] ${line}`);
      }
      const result = JSON.parse(outLines[outLines.length - 1]);
      if (result.action === 'extract' && result.start >= 0 && result.duration > 0) {
        logger.success(`Smart Cut: best segment ${result.start}s -> ${result.end}s (${result.duration}s, score: ${result.peak_highlight_score || 'N/A'})`);
        // Cleanup probe MP4
        if (probePath !== videoPath) try { fs.unlinkSync(probePath); } catch {}
        return { start: result.start, end: result.end };
      }
    } else {
      logger.warn(`highlight-detector.py not found at: ${hlPath}`);
    }
  } catch (e) {
    logger.warn(`Smart cut analysis failed:`);
    logger.warn(`  Error: ${(e.message || '').substring(0, 500)}`);
    if (e.stderr) logger.warn(`  Stderr: ${e.stderr.toString().substring(0, 500)}`);
    if (e.stdout) logger.warn(`  Stdout: ${e.stdout.toString().substring(0, 500)}`);
  }
  
  // Cleanup probe MP4 if it was created
  if (probePath !== videoPath) try { fs.unlinkSync(probePath); } catch {}

  if (duration > 120) {
    const mid = duration / 2;
    const fallback = { start: Math.max(0, mid - 30), end: Math.min(duration, mid + 30) };
    logger.info(`Smart Cut fallback: ${fallback.start}s > ${fallback.end}s (duration > 2min, cutting to ~60s)`);
    return fallback;
  } else {
    const fallback = { start: 0, end: duration };
    logger.info(`Smart Cut: video is ${duration.toFixed(1)}s (<= 2min), using full video`);
    return fallback;
  }
}

function getCropOffset(videoPath, srcW, srcH, tmpDir) {
  const yoloDir = path.join(tmpDir, `yolo_crop_${Date.now()}`);
  fs.mkdirSync(yoloDir, { recursive: true });
  const dims = probeDownloadedVideo(videoPath);
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
  logger.info(`YOLO crop offset: ${offset}px (from ${subjectCenters.length} samples, avg center: ${avgCenterX.toFixed(0)})`);
  return offset;
}

function buildCombinedFilter(cropOffsetX, srcW, srcH, hasSubtitles, subPath, hasFlag, flagPath, hasWatermark, wmPath, startDelay, endTime, delayMs, flagInputIdx, wmInputIdx, dynamicCropFilter) {
  const filters = [];
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
  if (hasFlag && flagPath && fs.existsSync(flagPath) && flagInputIdx >= 0) {
    filters.push(`[${flagInputIdx}:v]scale=120:-1,format=rgba[flag]`);
    filters.push(`[${currentLabel}][flag]overlay=(W-w)/2:20:enable='between(t,${startDelay},${endTime})'[v3]`);
    currentLabel = 'v3';
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
  return { filterComplex: filters.join(';'), videoOut: '[vout]' };
}

async function runType1Pipeline(options = {}) {
  let country = options.country;
  const outputDir = options.outputDir || path.join(__dirname, '..', 'output', 'clips');
  const tmpDir = path.join(outputDir, `tmp_${Date.now()}`);

  if (!fs.existsSync(tmpDir)) fs.mkdirSync(tmpDir, { recursive: true });

  logger.header(`TYPE 1 PIPELINE: ${country}`);

  const gemini = getGeminiService();
  const geminiCLI = getGeminiCLI();
  const curatorSkillPath = path.join(__dirname, '..', 'skills', 'type1', 'viral-clip-curator.md');
  const curatorSkill = fs.existsSync(curatorSkillPath) ? fs.readFileSync(curatorSkillPath, 'utf8') : null;

  // ------------------------------------------------------------------------------------------
  // Phase 1: Content Discovery
  // ------------------------------------------------------------------------------------------
  logger.info('Phase 1: Content Discovery');
  
  let queries;
  let trendBank = null;
  if (options.searchQuery) {
    queries = [options.searchQuery];
    logger.info(`Using single test query: "${options.searchQuery}"`);
  } else {
    trendBank = loadTrendBank(country);
    queries = await generateQueries(country, gemini, trendBank);
  }
  
  if (queries.length === 0) { logger.error('No queries generated -- aborting'); return { success: false, error: 'No queries' }; }

  let candidates = await searchYouTube(queries, 6, country);
  let filtered = filterCandidates(candidates);
  logger.info(`Candidates selected for pipeline: ${filtered.length}`);
  if (filtered.length === 0) { logger.error('No raw candidates found -- aborting'); return { success: false, error: 'No candidates' }; }

  logger.info('Fetching top comments for top candidates...');
  const candidatesForComments = filtered.slice(0, Math.min(5, filtered.length));
  for (const cand of candidatesForComments) { cand.topComments = await fetchTopComments(cand.url, 3); }

  // ------------------------------------------------------------------------------------------
  // Phase 2: Gemini Ranking (or skip if requested)
  // ------------------------------------------------------------------------------------------
  let ranked = [];
  let allRejected = []; // Collect all rejected videos across batches
  
  if (options.skipRanking) {
    logger.info('Phase 2: Skipping ranking -- using first search result');
    const firstVideo = filtered[0];
    if (firstVideo) {
      ranked.push({ 
        ...firstVideo, 
        geminiScore: 5, 
        hookScore: 5, 
        geminiCountry: country,
        reasoning: 'First search result (no ranking)'
      });
      logger.success(`Using first result: "${firstVideo.title.substring(0, 50)}"`);
    } else {
      logger.error('No candidates available -- aborting');
      return { success: false, error: 'No candidates' };
    }
  } else {
    logger.info('Phase 2: Gemini Ranking');
    const MAX_BATCHES = 3;
    for (let batch = 1; batch <= MAX_BATCHES; batch++) {
      logger.header(`Ranking batch ${batch}/${MAX_BATCHES}`);
      if (batch > 1) {
        logger.info(`Batch ${batch}: Searching for fresh candidates...`);
        const newQueries = await generateQueries(country, gemini, trendBank);
        if (newQueries.length === 0) { logger.warn(`Batch ${batch}: No new queries generated -- skipping`); continue; }
        candidates = await searchYouTube(newQueries, 6, country);
        filtered = filterCandidates(candidates);
        if (filtered.length < 2) { logger.warn(`Batch ${batch}: Only ${filtered.length} candidates -- not enough to rank`); continue; }
        const newCandsForComments = filtered.slice(0, Math.min(5, filtered.length));
        for (const cand of newCandsForComments) { cand.topComments = await fetchTopComments(cand.url, 3); }
      }
      const result = await rankVideos(filtered, country, gemini, geminiCLI, curatorSkill, tmpDir);
      if (result.ranked.length > 0) { 
        ranked = result.ranked;
        logger.success(`Batch ${batch}: Found ${ranked.length} approved videos -- using best`);
        allRejected = result.rejected;
        break; 
      }
      // Collect rejected from this batch
      allRejected = allRejected.concat(result.rejected);
      logger.warn(`Batch ${batch}: All ${result.rejected.length} videos rejected -- trying next batch`);
    }

    // If after all batches we still have no approved videos, fall back to best rejected
    if (ranked.length === 0) {
      if (allRejected.length > 0) {
        // Sort all rejected by score descending, pick the best one
        allRejected.sort((a, b) => b.geminiScore - a.geminiScore);
        const bestRejected = allRejected[0];
        logger.warn(`All batches exhausted -- using best rejected video (score: ${bestRejected.geminiScore}/10): "${bestRejected.title.substring(0, 50)}"`);
        ranked.push(bestRejected);
      } else {
        // No rejected videos either — last resort: highest view count
        logger.warn('No approved or rejected videos -- using highest-view fallback');
        const shorts = (filtered || candidates || []).filter(c => c.duration <= 60 && c.duration > 0);
        if (shorts.length > 0) { const fb = shorts.sort((a, b) => b.view_count - a.view_count)[0]; ranked.push({ ...fb, geminiScore: 5, hookScore: 5, geminiCountry: country, reasoning: 'Fallback' }); }
        else { logger.error('No fallback candidates -- aborting'); return { success: false, error: 'No approved videos' }; }
      }
    }
  }

  const bestVideo = ranked[0];
  logger.success(`Best video: "${bestVideo.title.substring(0, 50)}" (score: ${bestVideo.geminiScore}/10)`);

  if (bestVideo.geminiCountry && bestVideo.geminiCountry !== country) {
    logger.warn(`Country recategorized: "${country}" -> "${bestVideo.geminiCountry}"`);
    country = bestVideo.geminiCountry;
    logger.info(`   Using "${country}" for signature, metadata, and memory`);
  }

  // ------------------------------------------------------------------------------------------
  // Phase 3: First Download + Smart Cut Analysis
  // ------------------------------------------------------------------------------------------
  logger.info('Phase 3: Download + Smart Cut Analysis');
  const tempDownloadPath = await downloadBestVideo(bestVideo, tmpDir);
  if (!tempDownloadPath) { logger.error('Download failed -- aborting'); return { success: false, error: 'Download failed' }; }

  const tempDims = probeDownloadedVideo(tempDownloadPath);
  const tempDuration = tempDims.duration || 60;

  const cut = smartCut(tempDownloadPath, tempDuration);
  
  const analysisClip = path.join(tmpDir, `analysis_${Date.now()}.mp4`);
  execSync(`ffmpeg -y -ss ${cut.start} -i "${tempDownloadPath}" -to ${cut.end} -c:v libx264 -preset fast -crf 0 -pix_fmt yuv444p -c:a aac -b:a 320k "${analysisClip}"`, { timeout: 240000 });

  const analysisDims = probeDownloadedVideo(analysisClip);
  const dialogue = await transcribeAudio(analysisClip, tmpDir);
  if (gemini.hasProfanity(dialogue.transcript)) { logger.error('Profanity detected -- aborting'); return { success: false, error: 'Profanity detected' }; }

  const clipDuration = Math.min(cut.end - cut.start, analysisDims.duration || 30);
  logger.info('Generating dynamic crop filter with 1 FPS person tracking...');
  const dynamicCropFilter = generateDynamicCropFilter(
    analysisClip, 0, clipDuration,
    analysisDims.width, analysisDims.height, tmpDir
  );
  if (dynamicCropFilter) {
    // Log whether this is a real dynamic crop (time-based) or static
    const isDynamic = dynamicCropFilter.includes('lt(t') || dynamicCropFilter.includes('gte(t');
    if (isDynamic) {
      logger.success(`✅ Dynamic crop filter generated (${dynamicCropFilter.length} chars, dynamic)`);
    } else {
      logger.success(`⚠️  Static crop only (${dynamicCropFilter.length} chars, fallback from dynamic)`);
    }
    // Log the first 200 chars of the actual filter for debugging
    logger.info(`Crop filter preview: ${dynamicCropFilter.substring(0, 200)}`);
  } else {
    logger.warn('Dynamic crop failed -- will use static center crop');
  }
  const cropOffsetX = getCropOffset(analysisClip, analysisDims.width, analysisDims.height, tmpDir);

  let subPath = null;
  let translatedText = null;
  if (dialogue.hasDialogue && dialogue.wordCount > 5) {
    const transcript = (dialogue.transcript || '').toLowerCase();
    const words = transcript.split(/\s+/).filter(w => w.length > 0);
    const wordDensity = words.length / Math.min(cut.end - cut.start, 30);
    const isMusic = wordDensity < 1.0 || words.length < 8;
    
    if (!isMusic) {
      // Translate non-English dialogue using NLLB-200
      let translateArg = '';
      if (dialogue.language && !['en', 'en', 'english', 'english'].includes(dialogue.language)) {
        try {
          logger.info(`Translating from ${dialogue.language || 'unknown'} with NLLB-200...`);
          const nllbOut = execSync(
            `python3 "${path.join(__dirname, '..', 'core', 'nllb-translate.py')}" "${dialogue.transcript}" 2>&1`,
            { timeout: 30000, encoding: 'utf8' }
          ).toString().trim();
          const nllbResult = JSON.parse(nllbOut.split('\n').filter(l => l.startsWith('{'))[0]);
          if (nllbResult.translated_text) {
            translatedText = nllbResult.translated_text;
            translateArg = `--translate "${translatedText.replace(/"/g, '\\"')}"`;
            logger.success(`Translation: ${translatedText.substring(0, 80)}...`);
          }
        } catch (e) {
          logger.warn(`NLLB translation failed: ${(e.message || '').substring(0, 60)} — using Gemini fallback`);
          // Fallback to Gemini translation
          try {
            translatedText = await gemini.translate(dialogue.transcript);
            if (translatedText) translateArg = `--translate "${translatedText.replace(/"/g, '\\"')}"`;
          } catch {}
        }
      }

      subPath = path.join(tmpDir, `captions_${Date.now()}.ass`);
      try {
        const captionOut = execSync(`python3 "${path.join(__dirname, '..', 'core', 'tiktok_captions.py')}" "${analysisClip}" "${subPath}" ${translateArg} 2>&1`, { timeout: 120000, encoding: 'utf8' }).toString().trim();
        const captionResult = JSON.parse(captionOut);
        logger.info(`Captions: ${captionResult.word_count} words${translatedText ? ' (dual-language)' : ''}`);
      } catch {
        logger.warn('Captions failed -- proceeding without');
        subPath = null;
      }
    }
  }

  try { fs.unlinkSync(analysisClip); } catch {}

  // ------------------------------------------------------------------------------------------
  // Phase 4: Redownload Fresh + Combined Render
  // ------------------------------------------------------------------------------------------
  logger.info('Phase 4: Redownload + Combined Render');
  
  const freshSourceDir = path.join(tmpDir, 'fresh_source');
  if (!fs.existsSync(freshSourceDir)) fs.mkdirSync(freshSourceDir, { recursive: true });
  const freshPath = await downloadBestVideo(bestVideo, freshSourceDir);
  if (!freshPath) { logger.error('Redownload failed -- aborting'); return { success: false, error: 'Redownload failed' }; }

  const flagIsoMap = {
    'Nigeria': 'NG', 'Japan': 'JP', 'Germany': 'DE', 'Australia': 'AU',
    'France': 'FR', 'Brazil': 'BR', 'Thailand': 'TH', 'India': 'IN',
    'Mexico': 'MX', 'UK': 'GB', 'United Kingdom': 'GB', 'South Korea': 'KR',
    'Egypt': 'EG', 'Italy': 'IT', 'Spain': 'ES', 'China': 'CN',
    'Global': 'UN', 'Indonesia': 'ID', 'Vietnam': 'VN', 'United States': 'US',
    'USA': 'US', 'America': 'US', 'Canada': 'CA', 'Turkey': 'TR',
    'Russia': 'RU', 'Argentina': 'AR', 'Colombia': 'CO', 'South Africa': 'ZA',
    'Saudi Arabia': 'SA', 'UAE': 'AE', 'United Arab Emirates': 'AE',
    'Singapore': 'SG', 'Malaysia': 'MY', 'Philippines': 'PH', 'Taiwan': 'TW',
    'Hong Kong': 'HK', 'Portugal': 'PT', 'Netherlands': 'NL', 'Sweden': 'SE',
    'Norway': 'NO', 'Denmark': 'DK', 'Finland': 'FI', 'Poland': 'PL',
    'Greece': 'GR', 'Switzerland': 'CH', 'Austria': 'AT', 'Belgium': 'BE',
    'Ireland': 'IE', 'New Zealand': 'NZ', 'Peru': 'PE', 'Chile': 'CL',
  };
  let flagIso = flagIsoMap[country];
  if (!flagIso) {
    for (const [name, code] of Object.entries(flagIsoMap)) {
      if (country.toLowerCase().includes(name.toLowerCase()) || name.toLowerCase().includes(country.toLowerCase())) {
        flagIso = code; break;
      }
    }
  }
  let flagPath = null;
  if (flagIso) {
    flagPath = path.join(tmpDir, `flag_${Date.now()}.png`);
    try {
      const cp1 = 0x1f1e6 + (flagIso.charCodeAt(0) - 65); const cp2 = 0x1f1e6 + (flagIso.charCodeAt(1) - 65);
      const flagFilename = `${cp1.toString(16)}-${cp2.toString(16)}.png`;
      const url = `https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/${flagFilename}`;
      execSync(`curl -sL -o "${flagPath}" "${url}"`, { timeout: 10000 });
      if (!(fs.existsSync(flagPath) && fs.statSync(flagPath).size > 100)) { flagPath = null; }
    } catch { flagPath = null; }
  }

  const wmImagePath = path.join(__dirname, '..', 'core', 'assets', 'mrw-logo.png');
  const hasWatermark = fs.existsSync(wmImagePath);

  const ttsPath = path.join(tmpDir, `signature_${Date.now()}.mp3`);
  let hasSignature = false;
  try {
    execSync(`edge-tts --voice "en-US-AvaMultilingualNeural" --text "Enjoy this clip from ${country}" --write-media "${ttsPath}"`, { timeout: 30000 });
    if (fs.existsSync(ttsPath) && fs.statSync(ttsPath).size >= 1000) hasSignature = true;
  } catch { logger.warn('TTS failed -- skipping signature'); }

  const ttsDuration = hasSignature ? Math.min(5, (() => { try { return parseFloat(execSync(`ffprobe -v error -show_entries format=duration -of csv=p=0 "${ttsPath}"`, { timeout: 5000, encoding: 'utf8' }).trim()); } catch { return 3 } })()) : 0;
  const startDelay = 1.0;
  const endTime = Math.min(startDelay + ttsDuration, clipDuration - 0.5);
  const delayMs = Math.round(startDelay * 1000);

  let nextInputIdx = 1;
  let flagInputIdx = -1;
  let wmInputIdx = -1;
  
  if (hasSignature) {
    nextInputIdx++;
  }
  
  if (flagPath && fs.existsSync(flagPath)) {
    flagInputIdx = nextInputIdx;
    nextInputIdx++;
  }
  
  if (hasWatermark) {
    wmInputIdx = nextInputIdx;
    nextInputIdx++;
  }
  
  const { filterComplex, videoOut } = buildCombinedFilter(cropOffsetX, analysisDims.width, analysisDims.height, !!subPath, subPath, !!flagPath && fs.existsSync(flagPath), flagPath, hasWatermark, wmImagePath, startDelay, endTime, delayMs, flagInputIdx, wmInputIdx, dynamicCropFilter);

  const finalOutput = path.join(tmpDir, `final_${Date.now()}.mkv`);

  let inputs = `-ss ${cut.start} -i "${freshPath}"`;
  let audioFilter = '';
  let audioMap = '-map "[aout]"';
  
  if (hasSignature) {
    inputs += ` -i "${ttsPath}"`;
    if (flagPath && fs.existsSync(flagPath)) {
      inputs += ` -i "${flagPath}"`;
    }
    if (hasWatermark) {
      inputs += ` -i "${wmImagePath}"`;
    }
    audioFilter = `; [0:a]volume=enable='between(t,${startDelay},${endTime})':volume=0.25[ad]; [1:a]adelay=${delayMs}|${delayMs}:all=1[av]; [ad][av]amix=inputs=2:duration=first:dropout_transition=0[aout]`;
  } else {
    audioFilter = '';
    audioMap = '-map 0:a';
  }
  const filterScriptPath = path.join(tmpDir, `filter_${Date.now()}.txt`);
  const fullFilterGraph = `${filterComplex}${audioFilter}`;
  fs.writeFileSync(filterScriptPath, fullFilterGraph, 'utf8');

  const cmd = `ffmpeg -y ${inputs} -to ${clipDuration} -filter_complex_script "${filterScriptPath}" -map "${videoOut}" ${audioMap} -c:v ffv1 -level 3 -coder 1 -context 1 -g 1 -slices 16 -slicecrc 1 -pix_fmt yuv444p10le -c:a flac -ar 48000 -shortest -strict experimental "${finalOutput}"`;

  logger.info('Running combined render...');
  try {
  execSync(cmd, { timeout: 600000, maxBuffer: 500 * 1024 * 1024 });
    try { fs.unlinkSync(filterScriptPath); } catch {}
    if (fs.existsSync(finalOutput) && fs.statSync(finalOutput).size > 100000) {
      logger.success(`Combined render: ${(fs.statSync(finalOutput).size / 1024 / 1024).toFixed(1)}MB`);
    } else {
      logger.error('Combined render produced no output');
      return { success: false, error: 'Render failed' };
    }
  } catch (e) {
    logger.error(`Combined render failed: ${(e.message || '').substring(0, 100)}`);
    return { success: false, error: 'Render failed' };
  }

  const safeCountry = String(country || 'global').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'global';
  const durableFinalPath = path.join(outputDir, `type1_${safeCountry}_${Date.now()}.mkv`);
  try { fs.copyFileSync(finalOutput, durableFinalPath); } catch (e) { logger.error(`Copy failed: ${e.message}`); return { success: false, error: 'Copy failed' }; }
  if (!fs.existsSync(durableFinalPath) || fs.statSync(durableFinalPath).size < 100000) { logger.error('Final video missing or too small'); return { success: false, error: 'Final video copy failed' }; }

  // ------------------------------------------------------------------------------------------
  // Phase 5: QA Review
  // ------------------------------------------------------------------------------------------
  logger.info('Phase 5: QA Review');
  const validation = await validateOutput(durableFinalPath);
  if (!validation.passed) { logger.warn(`Validation issues: ${validation.issues.join(', ')}`); if (validation.score < 4) { logger.error('Validation score too low -- aborting'); return { success: false, error: `Validation failed: ${validation.issues.join('; ')}` }; } }
  const geminiQA = await geminiReview(durableFinalPath);
  logger.info(`Gemini QA: ${geminiQA.score}/10 -- ${geminiQA.recommendation}`);

  // ------------------------------------------------------------------------------------------
  // Phase 6: Generate Metadata
  // ------------------------------------------------------------------------------------------
  logger.info('Phase 6: Generate Metadata (with Gemini + multi-key retry)');
  const commentsText = bestVideo.topComments?.length > 0
    ? bestVideo.topComments.map(c => `  - "${c.text}" (${c.likes} likes)`).join('\n')
    : '';
  const metadataContext = {
    reasoning: bestVideo.reasoning || '',
    searchQuery: bestVideo.searchQuery || '',
    hookScore: bestVideo.hookScore || 5,
    geminiScore: bestVideo.geminiScore || 5,
    editType: 'combined',
    hasCaptions: !!subPath,
    sourceUrl: bestVideo.url || '',
    sourceTitle: bestVideo.title || '',
    viewCount: bestVideo.view_count || 0,
    comments: commentsText,
    hookDescription: bestVideo.reasoning?.split('.').slice(0, 2).join('.') || '',
  };
  const metadata = await gemini.generateTitle(country, dialogue.transcript, bestVideo.title, metadataContext);
  const fallbackMetadata = buildFallbackMetadata(country, bestVideo, dialogue);
  const title = metadata?.title || fallbackMetadata.title;
  let description = metadata?.description || fallbackMetadata.description;
  const tags = metadata?.tags || fallbackMetadata.tags;

  // ------------------------------------------------------------------------------------------
  // Phase 7: Generate Daily Roulette Intro (for description + comment)
  // ------------------------------------------------------------------------------------------
  logger.info('Phase 7: Building Daily Random Roulette intro...');
  const hookDescription = bestVideo.reasoning?.split('.').slice(0, 2).join('.') || '';
  const channelHandle = process.env.YOUTUBE_HANDLE || '@Mr.WorldWideWebster';
  // Only the "Today we have..." line is LLM-generated; the rest is a FIXED template
  const todayLine = await gemini.generateRouletteTodayLine(country, hookDescription, bestVideo.title);
  const todayFallback = `Today we have a viral moment from ${country}!`;
  const rouletteText = (todayLine && todayLine.trim().length > 20) ? todayLine.trim() : todayFallback;

  // FIXED template prepended to description
  const rouletteHeader = `🌍 Daily Random Roulette 🌍
Every day a random clip from a random country — it can be good, it can be bad, but it'll always be interesting. Start your day with a great video and the rest of the day is blessed. Start with a bad one? Well, the day can't get any worse! Either way, we hope it brings a smile to your face (or at least some confusion).

${rouletteText}

If you want to be surprised every day, make sure to subscribe to ${channelHandle}!`;

  // Prepend FIXED roulette intro to description
  description = `${rouletteHeader}\n\n---\n\n${description}`;

  try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}

  logger.header('PIPELINE COMPLETE');
  logger.success(`Video: ${durableFinalPath}`);
  logger.success(`Title: ${title}`);
  logger.success(`Country: ${country}`);
  logger.success(`Gemini Score: ${bestVideo.geminiScore}/10`);

  return { success: true, videoPath: durableFinalPath, title, description, tags, country, geminiScore: bestVideo.geminiScore, editType: 'combined', hasCaptions: !!subPath, sourceUrl: bestVideo.url, rouletteIntro: rouletteText };
}

module.exports = { runType1Pipeline, loadTrendBank, generateQueries, searchYouTube };