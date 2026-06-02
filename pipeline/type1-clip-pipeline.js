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
 * 5. Redownload → Combined FFmpeg: Cut → Crop → Captions → Sig → Wm → 1440p
 * 6. QA review → Upload
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('../core/logger');
const { getGeminiService } = require('../core/gemini-service');
const { getGeminiCLI } = require('../core/gemini-cli-runner');
const { getOpenRouterQA } = require('../core/openrouter-qa');
const { smartCrop, probeVideo, extractFrames } = require('../core/smart-cropper');
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

const COUNTRY_METADATA_PROFILES = {
  China: { titleBase: 'China', tags: ['douyin', 'china', 'chinese dance'], hashtags: '#shorts #douyin #china' },
  Japan: { titleBase: 'Japan', tags: ['japan', 'japanese comedy', 'tokyo'], hashtags: '#shorts #japan #tokyo' },
  'South Korea': { titleBase: 'Korea', tags: ['south korea', 'korean comedy', 'kpop'], hashtags: '#shorts #korea #seoul' },
  UK: { titleBase: 'The UK', tags: ['uk', 'british comedy', 'london'], hashtags: '#shorts #uk #british' },
  Nigeria: { titleBase: 'Nigeria', tags: ['nigeria', 'african comedy', 'afrobeats'], hashtags: '#shorts #nigeria #africa' },
  Africa: { titleBase: 'Africa', tags: ['africa', 'african comedy', 'afrobeats'], hashtags: '#shorts #africa #funny' },
  'South Africa': { titleBase: 'South Africa', tags: ['south africa', 'amapiano', 'mzansi'], hashtags: '#shorts #southafrica #amapiano' },
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
  const profile = COUNTRY_METADATA_PROFILES[countryKey(country)] || { titleBase: country, tags: [country.toLowerCase(), `${country.toLowerCase()} culture`], hashtags: `#shorts #${String(country).toLowerCase().replace(/[^a-z0-9]/g, '')} #viral` };
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
      logger.info(`Shorts quality: ${tierCounts['1080p']}×1080p, ${tierCounts['720p']}×720p, ${tierCounts['lower']}×lower`);
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
      if (addedFromQuery > 0) logger.info(`  → ${addedFromQuery} enriched candidates from this query`);
    } catch (e) { logger.warn(`Search failed for "${query}": ${(e.message || '').substring(0, 200)}`); }
  }
  for (let i = allResults.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [allResults[i], allResults[j]] = [allResults[j], allResults[i]]; }
  const finalResults = allResults.slice(0, targetCount);
  logger.success(`Search complete: ${allResults.length} raw candidates collected; randomly selected ${finalResults.length} for Gemini`);
  return finalResults;
}

function filterCandidates(candidates) { logger.info(`No pre-Gemini filter: passing ${candidates.length} random raw candidates to Gemini`); return candidates; }

async function rankSingleVideo(candidate, country, gemini, geminiCLI, curatorSkill, tmpDir) {
  const ageInDays = candidate.upload_date ? Math.max(1, Math.floor((Date.now() - new Date(candidate.upload_date.substring(0, 4), candidate.upload_date.substring(4, 6) - 1, candidate.upload_date.substring(6, 8)).getTime()) / 86400000)) : 30;
  const engagementData = { views: candidate.view_count || 0, likes: candidate.like_count || 0, comments: candidate.comment_count || 0, ageInDays, title: candidate.title || 'YouTube video', topComments: candidate.topComments || [] };
  logger.info(`  Engagement: ${engagementData.views} views, ${engagementData.likes} likes, ${engagementData.comments} comments, ${engagementData.ageInDays}d old`);
  logger.info(`  Step 1 — downloading truncated clip for visual ranking...`);
  let dlPath = null;
  const rankingStrategies = [
    { name: 'ranking_web', args: '--extractor-args "youtube:player_client=web"', format: '-f "bestvideo[height<=720]+bestaudio/best" --merge-output-format mp4', sections: '--download-sections "*8-20"' },
    { name: 'ranking_default', args: '', format: '-f "bestvideo[height<=720]+bestaudio/best" --merge-output-format mp4', sections: '--download-sections "*8-20"' },
  ];
  const outputFile = path.join(tmpDir, `rank_${Date.now()}.mp4`);
  for (const s of rankingStrategies) {
    try {
      const hasCookies = fs.existsSync('/tmp/yt_cookies.txt');
      const cookieArg = hasCookies ? '--cookies "/tmp/yt_cookies.txt"' : '';
      const url = candidate.shortsUrl || candidate.url;
      const cmd = `yt-dlp ${cookieArg} ${s.args} ${s.format} ${s.sections} -o "${outputFile}" "${url}" --no-playlist --socket-timeout 30 --retries 2 --force-ipv4 --remote-components ejs:github 2>&1`;
      execSync(cmd, { timeout: 120000, maxBuffer: 50 * 1024 * 1024 });
      if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 50000) { dlPath = outputFile; break; }
    } catch (e) { logger.warn(`  Ranking download ${s.name} failed: ${(e.message || '').substring(0, 60)}`); }
  }
  if (!dlPath) { logger.warn(`  Download failed — cannot rank this video`); return null; }
  logger.info(`  Step 2 — Analyzing MP4 with Gemini API...`);
  let result = await gemini.rankVideoFile(dlPath, country, curatorSkill, engagementData);
  if (result === null && geminiCLI && geminiCLI.isAvailable()) {
    logger.info(`  Step 3 — API failed, trying Gemini CLI with local file ref...`);
    result = await geminiCLI.rankVideoFromPath(dlPath, country, curatorSkill, engagementData);
  }
  try { fs.unlinkSync(dlPath); } catch {}
  if (result === null) { logger.warn(`  All ranking methods failed for this video — skipping`); return null; }
  return { result, candidate };
}

async function rankVideos(candidates, country, gemini, geminiCLI, curatorSkill, tmpDir) {
  const ranked = [];
  const sorted = [...candidates].sort((a, b) => b.view_count - a.view_count).slice(0, 15);
  for (const candidate of sorted) {
    logger.info(`Ranking: "${candidate.title.substring(0, 50)}" (${(candidate.view_count / 1000000).toFixed(1)}M views)`);
    const out = await rankSingleVideo(candidate, country, gemini, geminiCLI, curatorSkill, tmpDir);
    if (out === null) { logger.warn(`  → No valid ranking obtained for this video`); }
    else if (out.result.verdict === 'APPROVED' && out.result.score >= 6) {
      ranked.push({ ...out.candidate, geminiScore: Math.min(10, Math.max(1, out.result.score)), hookScore: out.result.hook_score || 5, geminiCountry: out.result.country || country, watermarkType: out.result.watermark_type, reasoning: out.result.reasoning || '' });
      logger.success(`  ✅ Score: ${out.result.score}/10 — ${out.result.reasoning}`);
    } else { logger.info(`  ❌ Rejected (score: ${out.result.score}) — ${out.result.reasoning}`); }
    await new Promise(r => setTimeout(r, 10000));
  }
  ranked.sort((a, b) => b.geminiScore - a.geminiScore);
  logger.success(`Ranked: ${ranked.length} approved videos`);
  return ranked;
}

function probeDownloadedVideo(videoPath) {
  try {
    const out = execSync(`ffprobe -v error -select_streams v:0 -show_entries stream=width,height,duration -of csv=p=0 "${videoPath}"`, { timeout: 10000, encoding: 'utf8' }).trim();
    const [width, height, duration] = out.split(',').map(s => Number.parseFloat(s.trim()));
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
 * Smart Cut — Use PySceneDetect + YOLO to find the best segment in the video
 * Returns { start, end, cropOffsetX } for use in the combined render
 */
function smartCut(videoPath, duration) {
  logger.info('Smart Cut: analyzing video for best segment...');
  
  // Use highlight detector if available, else fallback to center
  try {
    const hlPath = path.join(__dirname, '..', 'core', 'highlight-detector.py');
    if (fs.existsSync(hlPath)) {
      const hlCmd = `python3 "${hlPath}" --output-json "${videoPath}" 2>&1`;
      const hlOut = execSync(hlCmd, { timeout: 300000, maxBuffer: 10 * 1024 * 1024, encoding: 'utf8' }).toString().trim();
      const lines = hlOut.split('\n').filter(Boolean);
      const result = JSON.parse(lines[lines.length - 1]);
      if (result.action === 'extract' && result.start >= 0 && result.duration > 0) {
        logger.success(`Smart Cut: best segment ${result.start}s → ${result.end}s (${result.duration}s, score: ${result.peak_highlight_score || 'N/A'})`);
        return { start: result.start, end: result.end };
      }
    }
  } catch (e) { logger.warn(`Smart cut analysis failed: ${(e.message || '').substring(0, 80)}`); }
  
  // Fallback: use middle 45s
  const mid = duration / 2;
  const fallback = { start: Math.max(0, mid - 22.5), end: Math.min(duration, mid + 22.5) };
  logger.info(`Smart Cut fallback: ${fallback.start}s → ${fallback.end}s`);
  return fallback;
}

/**
 * Get YOLO subject center from a video for crop offset
 */
function getCropOffset(videoPath, srcW, srcH, tmpDir) {
  const yoloDir = path.join(tmpDir, `yolo_crop_${Date.now()}`);
  fs.mkdirSync(yoloDir, { recursive: true });
  const dims = probeDownloadedVideo(videoPath);
  const dur = dims.duration || 30;
  
  // Sample every 1.5s
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

/**
 * Build the combined FFmpeg filter for Type 1 pipeline
 * @param {number} cropOffsetX - Horizontal crop offset for YOLO subject centering
 * @param {number} srcW - Source width
 * @param {number} srcH - Source height
 * @param {boolean} hasSubtitles - Whether subtitles exist
 * @param {string|null} subPath - Path to subtitles .ass file
 * @param {boolean} hasFlag - Whether flag is available (independent of TTS signature)
 * @param {string|null} flagPath - Path to flag PNG image
 * @param {boolean} hasWatermark - Whether watermark logo exists
 * @param {string|null} wmPath - Path to watermark logo image
 * @param {number} startDelay - When TTS signature starts (seconds)
 * @param {number} endTime - When TTS signature ends (seconds)
 * @param {number} delayMs - TTS delay in milliseconds
 * @param {number} flagInputIdx - FFmpeg input index for flag image (pass -1 if no flag)
 * @param {number} wmInputIdx - FFmpeg input index for watermark image (pass -1 if no watermark)
 */
function buildCombinedFilter(cropOffsetX, srcW, srcH, hasSubtitles, subPath, hasFlag, flagPath, hasWatermark, wmPath, startDelay, endTime, delayMs, flagInputIdx, wmInputIdx) {
  const filters = [];
  let currentLabel = '0:v';
  
  // 1. Smart Cut is handled by -ss/-to on input, so we start with crop
  const targetHeight = 1920;
  const targetWidth = 1080;
  const ratio = srcW / srcH;
  const TARGET_RATIO = targetWidth / targetHeight;
  
  let filterStr;
  if (Math.abs(ratio - TARGET_RATIO) < 0.05) {
    // Already close to 9:16
    filterStr = `scale=${targetWidth}:${targetHeight}:flags=lanczos:force_original_aspect_ratio=increase,crop=${targetWidth}:${targetHeight}`;
  } else if (ratio > TARGET_RATIO) {
    // Landscape
    const sh = targetHeight;
    const sw = Math.floor(sh * ratio / 2) * 2;
    filterStr = `scale=${sw}:${sh}:flags=lanczos,crop=${targetWidth}:${targetHeight}:${cropOffsetX}:0`;
  } else {
    // Portrait
    const sw = targetWidth;
    const sh = Math.floor(sw / ratio / 2) * 2;
    filterStr = `scale=${sw}:${sh}:flags=lanczos,crop=${targetWidth}:${targetHeight}:0:${Math.floor((sh - targetHeight) / 4) * 2}`;
  }
  
  filters.push(`${filterStr}[v1]`);
  currentLabel = 'v1';
  
  // 2. Subtitles (if any)
  if (hasSubtitles && subPath && fs.existsSync(subPath)) {
    const escPath = subPath.replace(/\\/g, '/').replace(/'/g, "'\\\\''");
    filters.push(`[${currentLabel}]ass='${escPath}'[v2]`);
    currentLabel = 'v2';
  }
  
  // 3. Flag overlay at top-center — synced with TTS "Enjoy this clip from [country]"
  if (hasFlag && flagPath && fs.existsSync(flagPath) && flagInputIdx >= 0) {
    filters.push(`[${flagInputIdx}:v]scale=120:-1,format=rgba[flag]`);
    // Flag at top-center: (1080-120)/2 = 480, y=20, synced with TTS time window
    filters.push(`[${currentLabel}][flag]overlay=(W-w)/2:20:enable='between(t,${startDelay},${endTime})'[v3]`);
    currentLabel = 'v3';
  }
  
  // 4. Watermark (logo + text)
  if (hasWatermark && wmPath && fs.existsSync(wmPath) && wmInputIdx >= 0) {
    const LOGO_SIZE = Math.max(30, Math.round(srcH * 0.04));
    const MARGIN_RIGHT = 20;
    const MARGIN_BOTTOM = Math.floor(srcH * 0.09);
    const FONT_SIZE = Math.max(12, Math.round(srcH * 0.015));
    const TEXT = '@Mr.WorldWideWebster';
    filters.push(`[${currentLabel}][${wmInputIdx}:v]overlay=W-w-${MARGIN_RIGHT}:H-h-${MARGIN_BOTTOM}:format=auto,drawtext=text='${TEXT}':fontcolor=white@0.55:fontsize=${FONT_SIZE}:x=W-tw-${MARGIN_RIGHT}:y=H-th-${MARGIN_RIGHT-10}:shadowcolor=black@0.55:shadowx=1:shadowy=1[v4]`);
    currentLabel = 'v4';
  }
  
  // 5. Scale to 1440p for VP9
  filters.push(`[${currentLabel}]scale=2560:1440:flags=lanczos[vout]`);
  
  return { filterComplex: filters.join(';'), videoOut: '[vout]' };
}

/**
 * Main Type 1 Pipeline
 */
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

  // ─── Phase 1: Content Discovery ────────────────────────────────────
  logger.info('Phase 1: Content Discovery');
  const trendBank = loadTrendBank(country);
  const queries = await generateQueries(country, gemini, trendBank);
  if (queries.length === 0) { logger.error('No queries generated — aborting'); return { success: false, error: 'No queries' }; }

  let candidates = await searchYouTube(queries, 6, country);
  let filtered = filterCandidates(candidates);
  logger.info(`Candidates selected for Gemini: ${filtered.length}`);
  if (filtered.length === 0) { logger.error('No raw candidates found — aborting'); return { success: false, error: 'No candidates' }; }

  logger.info('Fetching top comments for top candidates...');
  const candidatesForComments = filtered.slice(0, Math.min(5, filtered.length));
  for (const cand of candidatesForComments) { cand.topComments = await fetchTopComments(cand.url, 3); }

  // ─── Phase 2: Gemini Ranking ──────────────────────────────────────
  logger.info('Phase 2: Gemini Ranking');
  let ranked = [];
  const MAX_BATCHES = 3;
  for (let batch = 1; batch <= MAX_BATCHES; batch++) {
    logger.header(`Ranking batch ${batch}/${MAX_BATCHES}`);
    if (batch > 1) {
      logger.info(`Batch ${batch}: Searching for fresh candidates...`);
      const newQueries = await generateQueries(country, gemini, trendBank);
      if (newQueries.length === 0) { logger.warn(`Batch ${batch}: No new queries generated — skipping`); continue; }
      candidates = await searchYouTube(newQueries, 6, country);
      filtered = filterCandidates(candidates);
      if (filtered.length < 2) { logger.warn(`Batch ${batch}: Only ${filtered.length} candidates — not enough to rank`); continue; }
      const newCandsForComments = filtered.slice(0, Math.min(5, filtered.length));
      for (const cand of newCandsForComments) { cand.topComments = await fetchTopComments(cand.url, 3); }
    }
    ranked = await rankVideos(filtered, country, gemini, geminiCLI, curatorSkill, tmpDir);
    if (ranked.length > 0) { logger.success(`Batch ${batch}: Found ${ranked.length} approved videos — using best`); break; }
    logger.warn(`Batch ${batch}: All videos rejected or unrankable — trying next batch`);
  }

  if (ranked.length === 0) {
    logger.warn('All batches exhausted — using highest-view fallback');
    const shorts = (filtered || candidates || []).filter(c => c.duration <= 60 && c.duration > 0);
    if (shorts.length > 0) { const fb = shorts.sort((a, b) => b.view_count - a.view_count)[0]; ranked.push({ ...fb, geminiScore: 5, hookScore: 5, geminiCountry: country }); logger.warn(`Fallback: highest-view video "${fb.title.substring(0, 50)}" (score: 5/10)`); }
    else { logger.error('No fallback candidates — aborting'); return { success: false, error: 'No approved videos' }; }
  }

  const bestVideo = ranked[0];
  logger.success(`Best video: "${bestVideo.title.substring(0, 50)}" (score: ${bestVideo.geminiScore}/10)`);

  if (bestVideo.geminiCountry && bestVideo.geminiCountry !== country) {
    logger.warn(`⚠️  Country recategorized: "${country}" → "${bestVideo.geminiCountry}"`);
    country = bestVideo.geminiCountry;
    logger.info(`   Using "${country}" for signature, metadata, and memory`);
  }

  // ─── Phase 3: First Download + Smart Cut Analysis ────────────────
  logger.info('Phase 3: Download + Smart Cut Analysis');
  const tempDownloadPath = await downloadBestVideo(bestVideo, tmpDir);
  if (!tempDownloadPath) { logger.error('Download failed — aborting'); return { success: false, error: 'Download failed' }; }

  const tempDims = probeDownloadedVideo(tempDownloadPath);
  const tempDuration = tempDims.duration || 60;

  // Smart Cut: find best segment
  const cut = smartCut(tempDownloadPath, tempDuration);
  
  // Create temp clip for analysis
  const analysisClip = path.join(tmpDir, `analysis_${Date.now()}.mp4`);
  execSync(`ffmpeg -y -ss ${cut.start} -i "${tempDownloadPath}" -to ${cut.end} -c:v libx264 -preset fast -crf 0 -pix_fmt yuv444p -c:a aac -b:a 320k "${analysisClip}"`, { timeout: 120000 });

  // Analyze the temp clip
  const analysisDims = probeDownloadedVideo(analysisClip);
  const dialogue = await transcribeAudio(analysisClip, tmpDir);
  if (gemini.hasProfanity(dialogue.transcript)) { logger.error('Profanity detected — aborting'); return { success: false, error: 'Profanity detected' }; }

  // Get crop offset from analysis
  const cropOffsetX = getCropOffset(analysisClip, analysisDims.width, analysisDims.height, tmpDir);

  // Get transcript for captions
  let translatedText = null;
  if (dialogue.hasDialogue && dialogue.language !== 'en' && dialogue.language !== 'english') {
    translatedText = await gemini.translate(dialogue.transcript);
  }

  // Generate captions if needed
  let subPath = null;
  if (dialogue.hasDialogue && dialogue.wordCount > 5) {
    // Check if it's music vs speech
    const transcript = (dialogue.transcript || '').toLowerCase();
    const words = transcript.split(/\s+/).filter(w => w.length > 0);
    const wordDensity = words.length / Math.min(cut.end - cut.start, 30);
    const isMusic = wordDensity < 1.0 || words.length < 8;
    
    if (!isMusic) {
      subPath = path.join(tmpDir, `captions_${Date.now()}.ass`);
      try {
        const captionOut = execSync(`python3 "${path.join(__dirname, '..', 'core', 'tiktok_captions.py')}" "${analysisClip}" "${subPath}" 2>&1`, { timeout: 120000, encoding: 'utf8' }).toString().trim();
        const captionResult = JSON.parse(captionOut);
        logger.info(`Captions: ${captionResult.word_count} words`);
      } catch {
        logger.warn('Captions failed — proceeding without');
        subPath = null;
      }
    }
  }

  // Clean up analysis temp
  try { fs.unlinkSync(analysisClip); } catch {}

  // ─── Phase 4: Redownload Fresh + Combined Render ──────────────────
  logger.info('Phase 4: Redownload + Combined Render');
  
  // Redownload the source for final render
  const freshSourceDir = path.join(tmpDir, 'fresh_source');
  if (!fs.existsSync(freshSourceDir)) fs.mkdirSync(freshSourceDir, { recursive: true });
  const freshPath = await downloadBestVideo(bestVideo, freshSourceDir);
  if (!freshPath) { logger.error('Redownload failed — aborting'); return { success: false, error: 'Redownload failed' }; }

  // Download flag
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

  // Get watermark logo
  const wmImagePath = path.join(__dirname, '..', 'core', 'assets', 'mrw-logo.png');
  const hasWatermark = fs.existsSync(wmImagePath);

  // Get TTS
  const ttsPath = path.join(tmpDir, `signature_${Date.now()}.mp3`);
  let hasSignature = false;
  try {
    execSync(`edge-tts --voice "en-US-AvaMultilingualNeural" --text "Enjoy this clip from ${country}" --write-media "${ttsPath}"`, { timeout: 30000 });
    if (fs.existsSync(ttsPath) && fs.statSync(ttsPath).size >= 1000) hasSignature = true;
  } catch { logger.warn('TTS failed — skipping signature'); }

  const ttsDuration = hasSignature ? Math.min(5, (() => { try { return parseFloat(execSync(`ffprobe -v error -show_entries format=duration -of csv=p=0 "${ttsPath}"`, { timeout: 5000, encoding: 'utf8' }).trim()); } catch { return 3 } })()) : 0;
  const clipDuration = cut.end - cut.start;
  // Signature "Enjoy this clip from [country]" + flag starts after 1s preview
  const startDelay = 1.0; // 1s delay for visual preview first
  const endTime = Math.min(startDelay + ttsDuration, clipDuration - 0.5);
  const delayMs = Math.round(startDelay * 1000);

  // Build combined filter — track FFmpeg input indices dynamically
  const freshDims = probeDownloadedVideo(freshPath);
  let nextInputIdx = 1; // index 0 is the video
  let flagInputIdx = -1;
  let wmInputIdx = -1;
  
  // TTS input
  if (hasSignature) {
    nextInputIdx++; // TTS will be input 1
  }
  
  // Flag image input
  if (flagPath && fs.existsSync(flagPath)) {
    flagInputIdx = nextInputIdx;
    nextInputIdx++;
  }
  
  // Watermark image input
  if (hasWatermark) {
    wmInputIdx = nextInputIdx;
    nextInputIdx++;
  }
  
  const { filterComplex, videoOut } = buildCombinedFilter(cropOffsetX, freshDims.width, freshDims.height, !!subPath, subPath, !!flagPath && fs.existsSync(flagPath), flagPath, hasWatermark, wmImagePath, startDelay, endTime, delayMs, flagInputIdx, wmInputIdx);

  const finalOutput = path.join(tmpDir, `final_${Date.now()}.mp4`);

  // Build ffmpeg inputs
  let inputs = `-ss ${cut.start} -i "${freshPath}"`;
  let audioFilter = '';
  let audioMap = '-map "[aout]"';
  
  // Add TTS input and audio mixing if signature
  if (hasSignature) {
    inputs += ` -i "${ttsPath}"`;
    
    // Flag image (next input index after TTS = 1)
    if (flagPath && fs.existsSync(flagPath)) {
      inputs += ` -i "${flagPath}"`;
    }
    
    // Watermark image
    if (hasWatermark) {
      inputs += ` -i "${wmImagePath}"`;
    }
    
    // Build audio filter for signature ducking + mix
    audioFilter = `; [0:a]volume=enable='between(t,${startDelay},${endTime})':volume=0.25[ad]; [1:a]adelay=${delayMs}:all=1[av]; [ad][av]amix=inputs=2:duration=first:dropout_transition=0[a]`;
  } else {
    audioFilter = '';
    audioMap = '-map 0:a';
  }

  const cmd = `ffmpeg -y ${inputs} -to ${clipDuration} -filter_complex "${filterComplex}${audioFilter}" -map "${videoOut}" ${audioMap} -c:v libx264 -preset veryslow -crf 0 -pix_fmt yuv444p -c:a aac -b:a 320k -shortest "${finalOutput}"`;

  logger.info('Running combined render...');
  try {
    execSync(cmd, { timeout: 300000, maxBuffer: 500 * 1024 * 1024 });
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

  // Move to output
  const safeCountry = String(country || 'global').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'global';
  const durableFinalPath = path.join(outputDir, `type1_${safeCountry}_${Date.now()}.mp4`);
  try { fs.copyFileSync(finalOutput, durableFinalPath); } catch (e) { logger.error(`Copy failed: ${e.message}`); return { success: false, error: 'Copy failed' }; }
  if (!fs.existsSync(durableFinalPath) || fs.statSync(durableFinalPath).size < 100000) { logger.error('Final video missing or too small'); return { success: false, error: 'Final video copy failed' }; }

  // ─── Phase 5: QA Review ────────────────────────────────────────────
  logger.info('Phase 5: QA Review');
  const validation = await validateOutput(durableFinalPath);
  if (!validation.passed) { logger.warn(`Validation issues: ${validation.issues.join(', ')}`); if (validation.score < 4) { logger.error('Validation score too low — aborting'); return { success: false, error: `Validation failed: ${validation.issues.join('; ')}` }; } }
  const geminiQA = await geminiReview(durableFinalPath);
  logger.info(`Gemini QA: ${geminiQA.score}/10 — ${geminiQA.recommendation}`);

  // ─── Phase 6: Generate Metadata ────────────────────────────────────
  logger.info('Phase 6: Generate Metadata');
  const metadataContext = { reasoning: bestVideo.reasoning, searchQuery: bestVideo.searchQuery, hookScore: bestVideo.hookScore, geminiScore: bestVideo.geminiScore, editType: 'combined', hasCaptions: !!subPath, sourceUrl: bestVideo.url };
  const metadata = await gemini.generateTitle(country, dialogue.transcript, bestVideo.title, metadataContext);
  const fallbackMetadata = buildFallbackMetadata(country, bestVideo, dialogue);
  const title = metadata?.title || fallbackMetadata.title;
  const description = metadata?.description || fallbackMetadata.description;
  const tags = metadata?.tags || fallbackMetadata.tags;

  // Cleanup tmp
  try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}

  logger.header('PIPELINE COMPLETE');
  logger.success(`Video: ${durableFinalPath}`);
  logger.success(`Title: ${title}`);
  logger.success(`Country: ${country}`);
  logger.success(`Gemini Score: ${bestVideo.geminiScore}/10`);

  return { success: true, videoPath: durableFinalPath, title, description, tags, country, geminiScore: bestVideo.geminiScore, editType: 'combined', hasCaptions: !!subPath, sourceUrl: bestVideo.url };
}

module.exports = { runType1Pipeline, loadTrendBank, generateQueries, searchYouTube };