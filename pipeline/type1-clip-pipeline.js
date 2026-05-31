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
 * 4. Download best video → Transcribe (whisper.cpp)
 * 5. Smart crop with Gemini CLI feedback loop
 * 6. Smart edit (TikTok captions / translation / visual only)
 * 7. Add signature voiceover + flag
 * 8. QA review → Upload
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

const logger = new Logger('Type1Pipeline');

const SHORTS_W = 1080;
const SHORTS_H = 1920;

const COUNTRY_QUERY_PRESETS = {
  China: [
    'Chinese waiter dance Douyin',
    'Chinese restaurant waiter dancing',
    'Chinese street dance Douyin',
    'Chinese public square dance viral',
    'Douyin Subject Three dance China',
    'Kemusan dance Guangxi China',
    'Chinese group livestream dance',
    'Tuanbo group dance China',
    'Chinese mall dance performance',
    'Chinese school dance performance Douyin',
    'Chinese traditional hanfu dance Douyin',
    'Chinese festival dance street',
    'Kuaishou Chinese comedy skit',
    'Bilibili Chinese dance cover',
    'Xiaohongshu Chinese street fashion dance',
    'Chinese security guard dancing',
    'Chinese delivery guy dancing',
    'Chinese uncle auntie dancing',
    'Chinese village dance viral',
    'Chinese Douyin funny moments',
  ],
  Japan: [
    'Japanese game show funny moments',
    'Japan convenience store viral',
    'Japanese street food funny vendor',
    'Tokyo street interview funny',
    'Shibuya street fashion viral',
    'Japanese arcade funny moments',
    'Japanese mascot funny moments',
    'NicoNico dance cover Japan',
    'Japanese train station funny moments',
    'Japanese school festival performance',
    'Japan vending machine weird viral',
    'Japanese ramen chef viral',
    'Japanese dance challenge public',
    'Japanese comedy skit viral',
    'Japan 2000s nostalgic edit',
  ],
  'South Korea': [
    'Korean street interview funny',
    'Korean convenience store viral',
    'Korean street food funny moment',
    'Korean public random play dance',
    'K-pop dance challenge Korea',
    'Hongdae busking dance viral',
    'Korean couple comedy skit',
    'Korean school performance viral',
    'Korean mukbang funny moments',
    'Korean beauty transformation funny',
    'Korean gym comedy skit',
    'Korean cafe worker funny',
    'Korean drama meme short',
    'Korean auntie funny moment',
    'Seoul street fashion viral',
  ],
  UK: [
    'British comedy skit viral',
    'UK roadman funny moments',
    'London street interview funny',
    'British pub banter viral',
    'UK drill meme compilation',
    'British school funny moments',
    'UK football banter funny',
    'British roast battle short',
    'Manchester funny street interview',
    'London chicken shop funny',
    'British train station funny moment',
    'UK vs US slang funny',
    'British grandma funny moment',
    'UK council estate comedy skit',
    'British weather meme short',
  ],
  Nigeria: [
    'Nigerian comedy skit viral',
    'Nollywood reaction meme short',
    'Nigerian wedding dance viral',
    'Lagos street interview funny',
    'Nigerian auntie funny moments',
    'Nigerian pastor funny meme',
    'Nigerian market funny moment',
    'Afrobeats dance challenge Nigeria',
    'Nigerian school comedy skit',
    'Nigerian street food funny',
    'Nigerian relationship comedy short',
    'Nigerian TikTok funny moments',
    'African comedy skit Nigeria',
    'Lagos traffic funny moments',
    'Nigerian mum funny skit',
  ],
  Africa: [
    'African comedy skit viral',
    'funniest African videos compilation',
    'African dance challenge viral',
    'Amapiano dance challenge South Africa',
    'Nigerian comedy skit viral',
    'Ghanaian comedy skit viral',
    'South African taxi funny moments',
    'African wedding dance viral',
    'Nollywood reaction meme short',
    'African auntie funny moments',
    'African street interview funny',
    'Afrobeats dance challenge',
    'African school comedy skit',
    'African market funny moment',
    'African football celebration funny',
  ],
  'South Africa': [
    'South African comedy skit viral',
    'Amapiano dance challenge South Africa',
    'South African taxi funny moments',
    'Cape Town street interview funny',
    'Mzansi funny moments',
    'South African school dance viral',
    'South African wedding dance viral',
    'South African football fan funny',
    'South African auntie funny moments',
    'South African TikTok funny moments',
  ],
  India: [
    'Indian street food funny vendor',
    'Bollywood dance challenge viral',
    'Indian wedding dance viral',
    'Indian comedy skit viral',
    'Mumbai street interview funny',
    'Indian cricket fan funny moments',
    'Indian train funny moment',
    'Indian school performance viral',
    'Indian auntie funny skit',
    'Indian street performer viral',
  ],
  Brazil: [
    'Brazilian funk dance challenge',
    'Brazil football skills funny',
    'Brazilian favela comedy skit',
    'Brazil beach funny moments',
    'Brazilian street interview funny',
    'Brazilian WhatsApp meme viral',
    'Brazil samba dance viral',
    'Brazilian food vendor funny',
    'Brazilian football fan funny',
    'Brazilian comedy short viral',
  ],
  Mexico: [
    'Mexican comedy skit viral',
    'Mexican street food funny vendor',
    'Mexico cumbia dance challenge',
    'Mexican soccer fan funny moments',
    'Mexican mom funny skit',
    'Mexican street interview funny',
    'Mexican quinceanera dance viral',
    'Mexican lucha libre funny moments',
    'Mexican banda dance viral',
    'Mexican relationship comedy short',
  ],
  France: [
    'French comedy skit viral',
    'Paris street interview funny',
    'French bakery viral moment',
    'French fashion street viral',
    'French protest funny moments',
    'French cafe funny moment',
    'French football fan funny',
    'French street food viral',
  ],
  Germany: [
    'German comedy skit viral',
    'Germany Autobahn funny moments',
    'German efficiency meme short',
    'German street interview funny',
    'German football fan funny',
    'German Christmas market viral',
    'German train station funny moment',
    'German beer festival funny',
  ],
  Italy: [
    'Italian comedy skit viral',
    'Italian grandma funny moments',
    'Italian street food viral',
    'Italian hand gestures funny',
    'Italian football fan funny',
    'Rome street interview funny',
    'Italian pasta chef viral',
    'Italian family comedy skit',
  ],
  Spain: [
    'Spanish comedy skit viral',
    'Spain football fan funny',
    'Spanish street interview funny',
    'Flamenco street performance viral',
    'Spanish tapas funny moment',
    'Madrid funny street interview',
    'Spanish beach funny moments',
    'Spanish family comedy short',
  ],
  Thailand: [
    'Thai street food funny vendor',
    'Thailand comedy skit viral',
    'Thai market funny moment',
    'Songkran funny moments',
    'Muay Thai funny moment',
    'Thailand tuk tuk funny',
    'Thai school performance viral',
    'Thai dance challenge viral',
  ],
  Vietnam: [
    'Vietnamese street food funny vendor',
    'Vietnam motorbike traffic funny',
    'Vietnamese comedy skit viral',
    'Vietnam school performance viral',
    'Vietnamese market funny moment',
    'Vietnamese dance challenge viral',
    'Hanoi street interview funny',
    'Vietnam cafe viral moment',
  ],
  Indonesia: [
    'Indonesian comedy skit viral',
    'Indonesia dangdut dance viral',
    'Indonesian street food funny',
    'Indonesian live selling funny',
    'Jakarta street interview funny',
    'Indonesian school comedy skit',
    'Indonesian family funny moments',
    'Indonesian TikTok viral funny',
  ],
  Egypt: [
    'Egyptian comedy skit viral',
    'Cairo street interview funny',
    'Egyptian wedding dance viral',
    'Egyptian street food funny',
    'Egyptian football fan funny',
    'Egyptian market funny moment',
    'Egyptian taxi funny moments',
    'Egyptian TikTok funny moments',
  ],
  Turkey: [
    'Turkish ice cream prank viral',
    'Turkish barber funny moments',
    'Istanbul street food viral',
    'Turkish comedy skit viral',
    'Turkish bazaar funny moment',
    'Turkish tea funny moments',
    'Turkish wedding dance viral',
    'Turkish street interview funny',
  ],
  Colombia: [
    'Colombian comedy skit viral',
    'Colombia salsa dance viral',
    'Medellin street interview funny',
    'Colombian football fan funny',
    'Colombian street food funny',
    'Colombian family comedy short',
  ],
  Peru: [
    'Peruvian comedy skit viral',
    'Peru street food funny',
    'Peruvian dance viral',
    'Lima street interview funny',
    'Peruvian market funny moment',
    'Peruvian football fan funny',
  ],
  Argentina: [
    'Argentinian comedy skit viral',
    'Argentina football fan funny',
    'Buenos Aires street interview funny',
    'Argentinian mate funny moment',
    'Argentinian tango street viral',
    'Argentinian roast comedy short',
  ],
  Chile: [
    'Chilean comedy skit viral',
    'Chile street interview funny',
    'Chilean football fan funny',
    'Chilean slang funny moments',
    'Chile completo food funny',
    'Chilean family comedy short',
  ],
  Portugal: [
    'Portuguese comedy skit viral',
    'Lisbon street interview funny',
    'Portugal football fan funny',
    'Portuguese street food viral',
    'Portuguese grandma funny moments',
    'Portugal surf funny moments',
  ],
  Russia: [
    'Russian dashcam funny moments',
    'Russian comedy skit viral',
    'Russian hardbass funny moments',
    'Russian street interview funny',
    'Russian winter funny moments',
    'Russian invention funny viral',
  ],
  Canada: [
    'Canadian comedy skit viral',
    'Canada winter funny moments',
    'Toronto street interview funny',
    'Canadian hockey fan funny',
    'Canadian food review funny',
    'Canadian politeness meme short',
  ],
  Australia: [
    'Australian comedy skit viral',
    'Australian tradie funny moments',
    'Australia beach funny moments',
    'Australian accent funny interview',
    'Sydney street interview funny',
    'Australian football fan funny',
  ],
};

const COUNTRY_RELEVANCE = {
  China: {
    positive: [
      'china', 'chinese', 'douyin', 'bilibili', 'kuaishou', 'xiaohongshu',
      'hanfu', 'guangxi', 'kemusan', 'subject three', 'tuanbo', 'mandarin',
      'beijing', 'shanghai', 'sichuan', 'cantonese', 'waiter', 'restaurant',
      'street dance', 'square dance',
    ],
    negative: [
      'india', 'bollywood', 'chammak', 'shambhavi', 'xml status', 'alight motion',
      'moliy', 'silent addy', 'shake it to the max', 'sweet body', 'hot yoga',
      'loli', 'no bra', 'onlyfans',
    ],
  },
  Japan: {
    positive: ['japan', 'japanese', 'tokyo', 'shibuya', 'osaka', 'anime', 'ramen', 'niconico', 'kawaii', 'arcade', 'mascot'],
    negative: ['china', 'chinese', 'korea', 'korean', 'india', 'bollywood', 'onlyfans', 'no bra'],
  },
  'South Korea': {
    positive: ['korea', 'korean', 'seoul', 'hongdae', 'k-pop', 'kpop', 'kimchi', 'mukbang', 'kdrama', 'k-drama'],
    negative: ['japan', 'japanese', 'china', 'chinese', 'india', 'bollywood', 'onlyfans', 'no bra'],
  },
  UK: {
    positive: ['uk', 'british', 'england', 'english', 'london', 'manchester', 'roadman', 'drill', 'pub', 'football', 'britain'],
    negative: ['america', 'american', 'india', 'bollywood', 'onlyfans', 'no bra'],
  },
  Nigeria: {
    positive: ['nigeria', 'nigerian', 'lagos', 'afrobeats', 'nollywood', 'yoruba', 'igbo', 'african'],
    negative: ['india', 'bollywood', 'america', 'american', 'onlyfans', 'no bra'],
  },
  Africa: {
    positive: ['africa', 'african', 'nigeria', 'nigerian', 'ghana', 'ghanaian', 'kenya', 'south africa', 'amapiano', 'afrobeats', 'nollywood'],
    negative: ['india', 'bollywood', 'america', 'american', 'onlyfans', 'no bra'],
  },
  'South Africa': {
    positive: ['south africa', 'south african', 'mzansi', 'amapiano', 'cape town', 'johannesburg', 'pretoria'],
    negative: ['india', 'bollywood', 'america', 'american', 'onlyfans', 'no bra'],
  },
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

const COUNTRY_ALIASES = {
  Korea: 'South Korea',
  'United Kingdom': 'UK',
  Britain: 'UK',
  England: 'UK',
  African: 'Africa',
};

function countryKey(country) {
  return COUNTRY_ALIASES[country] || country;
}

function isGenericCountryQuery(query, country) {
  const normalized = query.trim().toLowerCase();
  const names = [country, countryKey(country)]
    .filter(Boolean)
    .flatMap(name => {
      const lower = String(name).toLowerCase();
      return lower === 'south korea' ? [lower, 'korea', 'korean'] : [lower];
    });
  const genericTails = ['', 'viral', 'dance', 'shorts', 'funny', 'music', 'best', 'trends'];

  return names.some(name => genericTails.some(tail => normalized === `${name}${tail ? ` ${tail}` : ''}`));
}

function isCountryRelevantCandidate(candidate, country) {
  const rules = COUNTRY_RELEVANCE[countryKey(country)];
  if (!rules) return true;

  const text = [
    candidate.title,
    candidate.description,
    candidate.channel,
    candidate.searchQuery,
  ].filter(Boolean).join(' ').toLowerCase();

  if (rules.negative.some(term => text.includes(term))) return false;
  return rules.positive.some(term => text.includes(term));
}

function buildFallbackMetadata(country, bestVideo, dialogue) {
  const reasoning = (bestVideo.reasoning || '').toLowerCase();
  const sourceTitle = bestVideo.title || '';
  const profile = COUNTRY_METADATA_PROFILES[countryKey(country)] || {
    titleBase: country,
    tags: [country.toLowerCase(), `${country.toLowerCase()} culture`],
    hashtags: `#shorts #${String(country).toLowerCase().replace(/[^a-z0-9]/g, '')} #viral`,
  };

  let hook = `${profile.titleBase} Street Moment`;
  if (reasoning.includes('waiter') || /waiter/i.test(sourceTitle)) hook = 'This Waiter Started Dancing';
  else if (reasoning.includes('dance') || /dance|douyin|kemusan|subject three|amapiano|k-pop|kpop|salsa|cumbia/i.test(sourceTitle)) hook = `${profile.titleBase} Dance Hits Different`;
  else if (reasoning.includes('funny') || reasoning.includes('comedy') || /funny|comedy|skit|meme/i.test(sourceTitle)) hook = `${profile.titleBase} Has Main Character Energy`;
  else if (reasoning.includes('street food') || /food|ramen|taco|kebab|market|vendor/i.test(sourceTitle)) hook = `${profile.titleBase} Street Food Goes Crazy`;
  else if (reasoning.includes('hanfu') || /hanfu/i.test(sourceTitle)) hook = 'This Hanfu Moment Is Unreal';
  else if (reasoning.includes('football') || /football|soccer/i.test(sourceTitle)) hook = `${profile.titleBase} Football Fans Are Different`;
  else if (reasoning.includes('interview') || /street interview/i.test(sourceTitle)) hook = `${profile.titleBase} Street Interviews Are Unhinged`;

  const shortTranscript = (dialogue?.transcript || '').trim();
  const descriptionHook = bestVideo.reasoning
    ? bestVideo.reasoning.split('.').slice(0, 2).join('.').substring(0, 180)
    : `A viral ${country} short with a visual hook from the first seconds.`;

  return {
    title: hook.substring(0, 50),
    description: `${descriptionHook}\n\nWould you stop and watch this?\n\n${profile.hashtags}`,
    tags: ['shorts', 'viral', 'mr worldwidewebster'].concat(profile.tags).concat(
      shortTranscript ? ['global trends'] : []
    ),
  };
}

/**
 * Fetch top comments from a YouTube video using yt-dlp
 * Returns array of { text, likes, author } objects
 */
async function fetchTopComments(url, maxComments = 3) {
  try {
    const cmd = `yt-dlp --write-comments --extractor-args "youtube:max_comments=${maxComments},comment_sort=top" --dump-json --no-download "${url}" 2>&1`;
    const out = execSync(cmd, { timeout: 20000, maxBuffer: 2 * 1024 * 1024 }).toString().trim();
    if (!out || out.includes('ERROR') || out.includes('WARNING')) return [];

    const meta = JSON.parse(out.split('\n')[0]);
    const comments = (meta.comments || [])
      .slice(0, maxComments)
      .filter(c => c.text)
      .map(c => ({
        text: (c.text || '').substring(0, 200),
        likes: c.like_count || 0,
        author: (c.author || 'Unknown').substring(0, 30),
      }));
    return comments;
  } catch (e) {
    return [];
  }
}

/**
 * Load trend bank for a country
 */
function loadTrendBank(country) {
  const fileName = country.toLowerCase().replace(/ /g, '-');
  const bankPath = path.join(__dirname, '..', 'config', 'trend-banks', `${fileName}.json`);

  if (fs.existsSync(bankPath)) {
    try {
      const bank = JSON.parse(fs.readFileSync(bankPath, 'utf8'));
      const activeKeywords = bank.keywords
        .filter(k => k.status === 'active')
        .map(k => k.term);
      logger.info(`Loaded trend bank for ${country}: ${activeKeywords.length} keywords`);
      return { keywords: activeKeywords, suffix: bank.querySuffix, songs: bank.trendingSongs || [] };
    } catch (e) {
      logger.warn(`Failed to load trend bank for ${country}: ${e.message}`);
    }
  }

  // Fallback
  return { keywords: [country.toLowerCase()], suffix: '#shorts', songs: [] };
}

/**
 * Generate search queries using 3 methods
 * 1. Trend bank keywords
 * 2. LLM-generated queries
 * 3. LLM + trend bank hybrid
 */
async function generateQueries(country, gemini, trendBank) {
  // Use LLM + trend bank hybrid with query generator skill — generate 15-20 diverse queries
  const queries = await gemini.generateQueries(country, trendBank.keywords, 20);
  const allQueries = [];
  const key = countryKey(country);
  const presetQueries = COUNTRY_QUERY_PRESETS[key] || [];

  for (const q of [...presetQueries, ...(Array.isArray(queries) ? queries : [])]) {
    if (typeof q !== 'string') continue;
    if (presetQueries.length > 0 && isGenericCountryQuery(q, country)) {
      continue;
    }

    // Strip any existing hashtags (LLM sometimes adds #tiktok #reels #douyin despite instructions)
    const clean = q.replace(/#\w+/g, '').trim();
    if (!clean) continue;

    // Always append the country trend-bank suffix.
    const query = `${clean} ${trendBank.suffix}`;
    if (!allQueries.includes(query)) allQueries.push(query);
  }

  logger.info(`Preset+LLM queries: ${allQueries.length} queries`);
  logger.success(`Total queries generated: ${allQueries.length}`);
  return allQueries;
}

/**
 * Search YouTube — Bulk Fetch + Random Batch
 * 
 * Fetches raw search results, enriches metrics when available, then randomly
 * picks a batch for Gemini. Gemini decides content quality; metrics are support.
 */
async function searchYouTube(queries, targetCount = 15, country = null) {
  const seen = new Set();
  const cookieArg = fs.existsSync('/tmp/yt_cookies.txt') ? '--cookies "/tmp/yt_cookies.txt"' : '';
  const allResults = [];

  for (const query of queries) {
    logger.info(`Searching for: "${query}"`);

    try {
      // Fetch 100 results via fast flat playlist (no per-video metadata yet)
      const searchCmd = `yt-dlp --flat-playlist --dump-json "ytsearch100:${query}" 2>&1`;

      const out = execSync(searchCmd, { timeout: 30000, maxBuffer: 10 * 1024 * 1024 }).toString().trim();
      if (!out) {
        logger.info('No results from this query');
        continue;
      }

      const lines = out.split('\n').filter(Boolean);
      logger.info(`Raw results: ${lines.length} videos`);

      // Fisher-Yates shuffle for random selection
      for (let i = lines.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [lines[i], lines[j]] = [lines[j], lines[i]];
      }

      // Take first 5 from shuffled list for metadata enrichment
      const selected = lines.slice(0, 5);
      let addedFromQuery = 0;

      for (const line of selected) {
        try {
          const p = JSON.parse(line);
          if (p.id && !seen.has(p.id)) {
            seen.add(p.id);

            // Fetch detailed metadata only for this selected video
            const metaCmd = `yt-dlp ${cookieArg} --dump-json --no-download "https://www.youtube.com/watch?v=${p.id}" 2>&1`;
            let metaOut;
            try {
              metaOut = execSync(metaCmd, { timeout: 15000, maxBuffer: 1024 * 1024 }).toString().trim();
            } catch {}

            let meta = {};
            if (metaOut && !metaOut.includes('ERROR')) {
              try { meta = JSON.parse(metaOut.split('\n')[0]); } catch {}
            }

            allResults.push({
              id: p.id,
              url: `https://www.youtube.com/watch?v=${p.id}`,
              shortsUrl: `https://www.youtube.com/shorts/${p.id}`,
              title: meta.title || p.title || 'YouTube video',
              duration: meta.duration || p.duration || 0,
              searchQuery: query,
              view_count: meta.view_count || p.view_count || 0,
              channel_follower_count: meta.channel_follower_count || 0,
              like_count: meta.like_count || 0,
              comment_count: meta.comment_count || 0,
              channel: meta.channel || meta.uploader || p.channel || 'Unknown',
              description: (meta.description || '').substring(0, 300),
              upload_date: meta.upload_date || p.upload_date || '',
            });
            addedFromQuery++;
          }
        } catch {}
      }

      if (addedFromQuery > 0) {
        logger.info(`  → ${addedFromQuery} enriched candidates from this query (random from 100 results)`);
      }
    } catch (e) {
      logger.warn(`Search failed for "${query}": ${(e.message || '').substring(0, 200)}`);
    }
  }

  for (let i = allResults.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [allResults[i], allResults[j]] = [allResults[j], allResults[i]];
  }
  const finalResults = allResults.slice(0, targetCount);

  logger.success(`Search complete: ${allResults.length} raw candidates collected; randomly selected ${finalResults.length} for Gemini`);

  // ─── Debug: Log support metrics for selected candidates ────────────
  if (finalResults.length > 0) {
    logger.info('── Random Gemini batch (top 5 shown) ──');
    finalResults.slice(0, 5).forEach((c, i) => {
      const ageDays = c.upload_date
        ? Math.max(1, Math.floor((Date.now() - new Date(
            c.upload_date.substring(0, 4),
            c.upload_date.substring(4, 6) - 1,
            c.upload_date.substring(6, 8)
          ).getTime()) / 86400000))
        : 'N/A';
      logger.info(`  #${i + 1} "${c.title.substring(0, 40)}"`);
      logger.info(`       Views: ${c.view_count?.toLocaleString() || 0} | Likes: ${c.like_count?.toLocaleString() || 0} | Comments: ${c.comment_count?.toLocaleString() || 0}`);
      logger.info(`       Age: ${ageDays}d | Duration: ${c.duration}s | Query: ${c.searchQuery.substring(0, 50)}`);
    });
    logger.info('──────────────────────────────────────────');
  }

  return finalResults;
}

/**
 * Keep the hook for older call sites, but do not pre-filter Type 1 batches.
 * Gemini should rank the visual content; engagement metrics are support only.
 */
function filterCandidates(candidates) {
  logger.info(`No pre-Gemini filter: passing ${candidates.length} random raw candidates to Gemini`);
  return candidates;
}

/**
 * Rank a single video by downloading a short preview and sending the
 * actual MP4 to Gemini for visual ranking.
 * Returns a valid ranking result or null (no result possible).
 */
async function rankSingleVideo(candidate, country, gemini, geminiCLI, curatorSkill, tmpDir) {
  const ageInDays = candidate.upload_date
    ? Math.max(1, Math.floor((Date.now() - new Date(
        candidate.upload_date.substring(0, 4),
        candidate.upload_date.substring(4, 6) - 1,
        candidate.upload_date.substring(6, 8)
      ).getTime()) / 86400000))
    : 30;
  const engagementData = {
    views: candidate.view_count || 0,
    likes: candidate.like_count || 0,
    comments: candidate.comment_count || 0,
    ageInDays,
    title: candidate.title || 'YouTube video',
    topComments: candidate.topComments || [],
  };

  const commentsLog = engagementData.topComments.length > 0
    ? `, ${engagementData.topComments.length} top comments`
    : '';
  logger.info(`  Engagement: ${engagementData.views} views, ${engagementData.likes} likes, ${engagementData.comments} comments, ${engagementData.ageInDays}d old${commentsLog}`);

  // Always download a short clip for ranking so Gemini sees the actual video.
  logger.info(`  Step 1 — downloading truncated clip for visual ranking...`);

  // For ranking: download only 8-20s section to save time/bandwidth
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
      const cmd = `yt-dlp ${cookieArg} ${s.args} ${s.format} ${s.sections} ` +
        `-o "${outputFile}" "${url}" ` +
        `--no-playlist --socket-timeout 30 --retries 2 --force-ipv4 --remote-components ejs:github 2>&1`;

      execSync(cmd, { timeout: 120000, maxBuffer: 50 * 1024 * 1024 });
      if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 50000) {
        dlPath = outputFile;
        break;
      }
    } catch (e) {
      logger.warn(`  Ranking download ${s.name} failed: ${(e.message || '').substring(0, 60)}`);
    }
  }

  if (!dlPath) {
    logger.warn(`  Download failed — cannot rank this video`);
    return null;
  }

  // Step 2: Try Gemini API with inline video data for small ranking clips.
  logger.info(`  Step 2 — Analyzing MP4 with Gemini API...`);
  let result = await gemini.rankVideoFile(dlPath, country, curatorSkill, engagementData);

  // Step 3: If API failed, try Gemini CLI using an explicit local @file ref.
  if (result === null && geminiCLI && geminiCLI.isAvailable()) {
    logger.info(`  Step 3 — API failed, trying Gemini CLI with local file ref...`);
    result = await geminiCLI.rankVideoFromPath(dlPath, country, curatorSkill, engagementData);
  }

  // Cleanup downloaded file
  try { fs.unlinkSync(dlPath); } catch {}

  if (result === null) {
    logger.warn(`  All ranking methods failed for this video — skipping`);
    return null;
  }

  return { result, candidate };
}

/**
 * Rank videos via Gemini with per-video fallback chain:
 * 720p preview download → inline video API → CLI local file fallback.
 * No internal fallback — returns empty array if no videos were approved.
 */
async function rankVideos(candidates, country, gemini, geminiCLI, curatorSkill, tmpDir) {
  const ranked = [];

  // Rank top 15
  const sorted = [...candidates].sort((a, b) => b.view_count - a.view_count).slice(0, 15);

  for (const candidate of sorted) {
    logger.info(`Ranking: "${candidate.title.substring(0, 50)}" (${(candidate.view_count / 1000000).toFixed(1)}M views)`);

    const out = await rankSingleVideo(candidate, country, gemini, geminiCLI, curatorSkill, tmpDir);

    if (out === null) {
      logger.warn(`  → No valid ranking obtained for this video`);
    } else if (out.result.verdict === 'APPROVED' && out.result.score >= 6) {
      ranked.push({
        ...out.candidate,
        geminiScore: Math.min(10, Math.max(1, out.result.score)),
        hookScore: out.result.hook_score || 5,
        geminiCountry: out.result.country || country,
        watermarkType: out.result.watermark_type,
        reasoning: out.result.reasoning || '',
      });
      logger.success(`  ✅ Score: ${out.result.score}/10 — ${out.result.reasoning}`);
    } else {
      logger.info(`  ❌ Rejected (score: ${out.result.score}) — ${out.result.reasoning}`);
    }

    // 10s delay between videos to avoid rate limits
    await new Promise(r => setTimeout(r, 10000));
  }

  ranked.sort((a, b) => b.geminiScore - a.geminiScore);
  logger.success(`Ranked: ${ranked.length} approved videos`);
  return ranked;
}

/**
 * Download a video using yt-dlp
 */
function probeDownloadedVideo(videoPath) {
  try {
    const out = execSync(
      `ffprobe -v error -select_streams v:0 -show_entries stream=width,height,duration -of csv=p=0 "${videoPath}"`,
      { timeout: 10000, encoding: 'utf8' }
    ).trim();
    const [width, height, duration] = out.split(',').map(s => Number.parseFloat(s.trim()));
    if (Number.isFinite(width) && Number.isFinite(height)) {
      return { width: Math.round(width), height: Math.round(height), duration: Number.isFinite(duration) ? duration : 0 };
    }
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
    {
      name: 'web_1080_mp4',
      args: '--extractor-args "youtube:player_client=web"',
      format: '-f "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b" -S "res:1080,fps,vcodec:h264,acodec:m4a,ext:mp4:m4a" --merge-output-format mp4',
    },
    {
      name: 'default_1080_best',
      args: '',
      format: '-f "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b" -S "res:1080,fps,br" --merge-output-format mp4',
    },
    {
      name: 'android_1080',
      args: '--extractor-args "youtube:player_client=android"',
      format: '-f "bv*[height<=1080]+ba/b[height<=1080]/best" -S "res:1080,fps,br" --merge-output-format mp4',
    },
    {
      name: 'absolute_best_last_resort',
      args: '',
      format: '-f "bv*+ba/b" -S "res,fps,br" --merge-output-format mp4',
    },
  ];

  for (const s of strategies) {
    try {
      const hasCookies = fs.existsSync('/tmp/yt_cookies.txt');
      // Don't pass cookies with android client (it rejects them)
      const cookieArg = (hasCookies && !s.name.includes('android')) ? '--cookies "/tmp/yt_cookies.txt"' : '';

      const cmd = `yt-dlp ${cookieArg} ${s.args} ${s.format} ` +
        `-o "${outputTemplate}" "${url}" ` +
        `--no-playlist --socket-timeout 30 --retries 3 --force-ipv4 --remote-components ejs:github`;

      execSync(cmd, { timeout: 180000, maxBuffer: 200 * 1024 * 1024 });

      // Only accept files produced by this source download. Do not accidentally
      // pick low-res ranking previews from the same temp directory.
      const files = fs.readdirSync(outputDir)
        .filter(f => f.startsWith(outputStem) && (f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mkv')) && fs.statSync(path.join(outputDir, f)).size > 50000)
        .sort((a, b) => fs.statSync(path.join(outputDir, b)).mtimeMs - fs.statSync(path.join(outputDir, a)).mtimeMs);

      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        const dims = probeDownloadedVideo(fp);
        const sizeMb = (fs.statSync(fp).size / 1024 / 1024).toFixed(1);
        logger.success(`Downloaded: ${files[0]} (${sizeMb}MB, ${dims.width}x${dims.height}, strategy: ${s.name})`);
        if (!bestFallback || Math.max(dims.width, dims.height) > Math.max(bestFallback.dims.width, bestFallback.dims.height)) {
          bestFallback = { path: fp, dims, strategy: s.name };
        }
        if (Math.max(dims.width || 0, dims.height || 0) < 720) {
          logger.warn(`Downloaded source is only ${dims.width}x${dims.height}; trying next quality strategy`);
          continue;
        }
        return fp;
      }
    } catch (e) {
      logger.warn(`Download strategy ${s.name} failed: ${e.message.substring(0, 60)}`);
    }
  }

  if (bestFallback?.path && fs.existsSync(bestFallback.path)) {
    logger.warn(`Using best available source despite low resolution: ${bestFallback.dims.width}x${bestFallback.dims.height} (${bestFallback.strategy})`);
    return bestFallback.path;
  }

  logger.error(`All download strategies failed for ${url}`);
  return null;
}

/**
 * Transcribe audio using whisper.cpp (faster-whisper)
 */
async function transcribeAudio(videoPath, tmpDir) {
  const audioPath = path.join(tmpDir, `audio_${Date.now()}.mp3`);

  try {
    execSync(
      `ffmpeg -y -i "${videoPath}" -t 60 -vn -acodec libmp3lame -ab 64k "${audioPath}" 2>/dev/null`,
      { timeout: 30000 }
    );

    if (!fs.existsSync(audioPath) || fs.statSync(audioPath).size < 1000) {
      return { hasDialogue: false, wordCount: 0, language: 'unknown', transcript: '', words: [] };
    }

    const pyPath = audioPath.replace(/\\/g, '\\\\');
    const output = execSync(
      `python3 -c "
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
" 2>&1`,
      { timeout: 120000, encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 }
    ).toString().trim();

    try { fs.unlinkSync(audioPath); } catch {}

    if (output && !output.includes('Error') && !output.includes('Traceback')) {
      const p = JSON.parse(output);
      return {
        hasDialogue: p.word_count > 5,
        wordCount: p.word_count || 0,
        language: p.language || 'en',
        transcript: p.text || '',
        words: p.words || [],
      };
    }
  } catch (e) {
    logger.warn(`Transcription failed: ${e.message.substring(0, 60)}`);
    try { fs.unlinkSync(audioPath); } catch {}
  }

  return { hasDialogue: false, wordCount: 0, language: 'unknown', transcript: '', words: [] };
}

/**
 * Add signature voiceover and flag overlay
 * "Enjoy this clip from {country}" + flag emoji on top
 */
async function addSignature(videoPath, outputPath, country, tmpDir) {
  logger.info(`Adding signature: "Enjoy this clip from ${country}"`);

  // Probe input video for streams
  let hasAudio = true;
  let videoDuration = 30;
  try {
    const probeOut = execSync(
      `ffprobe -v error -show_entries stream=codec_type -of csv=p=0 "${videoPath}"`,
      { timeout: 10000, encoding: 'utf8' }
    ).trim();
    const streamTypes = probeOut.split('\n').filter(Boolean);
    hasAudio = streamTypes.includes('audio');
    // Get video duration
    const durOut = execSync(
      `ffprobe -v error -show_entries format=duration -of csv=p=0 "${videoPath}"`,
      { timeout: 5000, encoding: 'utf8' }
    ).trim();
    if (durOut) videoDuration = parseFloat(durOut);
  } catch (e) {
    logger.warn(`Failed to probe video: ${(e.message || '').substring(0, 60)}`);
  }

  // Generate TTS
  const ttsPath = path.join(tmpDir, `signature_${Date.now()}.mp3`);
  try {
    execSync(
      `edge-tts --voice "en-US-AvaMultilingualNeural" --text "Enjoy this clip from ${country}" --write-media "${ttsPath}"`,
      { timeout: 30000 }
    );
  } catch (e) {
    logger.warn(`TTS failed: ${(e.message || '').substring(0, 60)}`);
    // Fallback: just copy without signature
    try { fs.copyFileSync(videoPath, outputPath); } catch {}
    return fs.existsSync(outputPath);
  }

  if (!fs.existsSync(ttsPath) || fs.statSync(ttsPath).size < 1000) {
    try { fs.copyFileSync(videoPath, outputPath); } catch {}
    return fs.existsSync(outputPath);
  }

  // Get TTS duration
  let ttsDuration = 3;
  try {
    const durOut = execSync(
      `ffprobe -v error -show_entries format=duration -of csv=p=0 "${ttsPath}"`,
      { timeout: 5000, encoding: 'utf8' }
    ).trim();
    if (durOut) ttsDuration = Math.min(parseFloat(durOut), 5);
  } catch {}

  // Get country flag emoji file
  const flagFile = path.join(tmpDir, `flag_${Date.now()}.png`);
  const isoMap = {
    'Nigeria': 'NG', 'Japan': 'JP', 'Germany': 'DE', 'Australia': 'AU',
    'France': 'FR', 'Brazil': 'BR', 'Thailand': 'TH', 'India': 'IN',
    'Mexico': 'MX', 'UK': 'GB', 'South Korea': 'KR', 'Egypt': 'EG',
    'Italy': 'IT', 'Spain': 'ES', 'China': 'CN', 'Global': 'UN',
    'Indonesia': 'ID', 'Vietnam': 'VN',
  };
  const iso = isoMap[country];
  let hasFlag = false;

  if (iso) {
    try {
      const cp1 = 0x1f1e6 + (iso.charCodeAt(0) - 65);
      const cp2 = 0x1f1e6 + (iso.charCodeAt(1) - 65);
      const flagFilename = `${cp1.toString(16)}-${cp2.toString(16)}.png`;
      const url = `https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/${flagFilename}`;
      const response = await require('axios')('GET', url, {
        responseType: 'stream',
        timeout: 10000,
        validateStatus: status => status === 200,
      });
      const writer = fs.createWriteStream(flagFile);
      response.data.pipe(writer);
      await new Promise((resolve, reject) => { writer.on('finish', resolve); writer.on('error', reject); });
      // Validate flag PNG: must be > 100 bytes and begin with PNG header
      if (fs.existsSync(flagFile) && fs.statSync(flagFile).size > 100) {
        const header = fs.readFileSync(flagFile, null).slice(0, 8).toString('latin1');
        if (header.startsWith('\x89PNG')) hasFlag = true;
      }
    } catch (e) {
      logger.warn(`Flag download failed: ${(e.message || '').substring(0, 60)}`);
    }
  }

  if (!hasFlag) {
    logger.info('No flag overlay available — using TTS-only signature');
  }

  // Build ffmpeg command based on available streams
  const startDelay = Math.min(1, Math.max(0, videoDuration - ttsDuration - 0.5));
  const endTime = startDelay + ttsDuration;
  const delayMs = Math.round(startDelay * 1000);

    try {
      let ffmpegCmd;
      if (hasFlag && hasAudio) {
        // Full: flag overlay + audio duck + TTS mix
        const filterComplex =
          `[2:v]scale=144:-1[flag];` +
          `[0:v][flag]overlay=(W-w)/2:160:enable='between(t,${startDelay},${endTime})'[v];` +
          `[0:a]volume='if(between(t,${startDelay},${endTime}),0.25,1)'[ad];` +
          `[1:a]adelay=${delayMs}:all=1[av];[ad][av]amix=inputs=2:duration=first:dropout_transition=0[a]`;

        ffmpegCmd =
          `ffmpeg -y -i "${videoPath}" -i "${ttsPath}" -i "${flagFile}" ` +
          `-filter_complex "${filterComplex}" -map "[v]" -map "[a]" ` +
          `-c:v libx264 -preset fast -crf 0 -pix_fmt yuv444p -c:a aac -shortest "${outputPath}"`;
      } else if (hasFlag && !hasAudio) {
        // No original audio: just overlay flag + TTS as main audio
        const filterComplex =
          `[2:v]scale=144:-1[flag];` +
          `[0:v][flag]overlay=(W-w)/2:160:enable='between(t,${startDelay},${endTime})'[v]`;

        ffmpegCmd =
          `ffmpeg -y -i "${videoPath}" -i "${ttsPath}" -i "${flagFile}" ` +
          `-filter_complex "${filterComplex}" -map "[v]" -map 1:a ` +
          `-c:v libx264 -preset fast -crf 0 -pix_fmt yuv444p -c:a aac -shortest "${outputPath}"`;
      } else if (!hasFlag && hasAudio) {
        // No flag: just audio duck + TTS mix
        const filterComplex =
          `[0:a]volume='if(between(t,${startDelay},${endTime}),0.25,1)'[ad];` +
          `[1:a]adelay=${delayMs}:all=1[av];[ad][av]amix=inputs=2:duration=first:dropout_transition=0[a]`;

        ffmpegCmd =
          `ffmpeg -y -i "${videoPath}" -i "${ttsPath}" ` +
          `-filter_complex "${filterComplex}" -map 0:v -map "[a]" ` +
          `-c:v copy -c:a aac -shortest "${outputPath}"`;
      } else {
        // No flag, no original audio: just TTS audio over video
        ffmpegCmd =
          `ffmpeg -y -i "${videoPath}" -i "${ttsPath}" ` +
          `-map 0:v -map 1:a -c:v copy -c:a aac -shortest "${outputPath}"`;
      }

    const stderr = execSync(ffmpegCmd, { timeout: 120000, encoding: 'utf8' });
    const stderrStr = (stderr || '').toString();

    // Cleanup
    try { fs.unlinkSync(ttsPath); } catch {}
    try { if (hasFlag) fs.unlinkSync(flagFile); } catch {}

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Signature added: ${(fs.statSync(outputPath).size / 1024 / 1024).toFixed(1)}MB`);
      return true;
    }

    // ffmpeg returned 0 but output is too small — log stderr for debugging
    if (stderrStr.includes('error') || stderrStr.includes('Error')) {
      logger.warn(`ffmpeg warning: ${stderrStr.substring(0, 200)}`);
    }
  } catch (e) {
    const stderrMsg = (e.stderr || e.message || '').substring(0, 100);
    logger.warn(`Signature overlay failed: ${stderrMsg}`);
  }

  // Fallback: copy without signature
  try {
    fs.copyFileSync(videoPath, outputPath);
    return true;
  } catch {}

  return false;
}

/**
 * Main Type 1 Pipeline
 * 
 * @param {Object} options - { country, outputDir }
 * @returns {Object} - { success, videoPath, title, description, country }
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

  if (queries.length === 0) {
    logger.error('No queries generated — aborting');
    return { success: false, error: 'No queries' };
  }

  // ─── Phase 2: Search + Random Batch ────────────────────────────────
  // Target 6 random raw candidates per batch, 3 batches total.
  let candidates = await searchYouTube(queries, 6, country);
  let filtered = filterCandidates(candidates);
  logger.info(`Candidates selected for Gemini: ${filtered.length}`);

  if (filtered.length === 0) {
    logger.error('No raw candidates found — aborting');
    return { success: false, error: 'No candidates' };
  }

  // ─── Phase 2b: Fetch Top Comments ──────────────────────────────────
  logger.info('Fetching top comments for top candidates...');
  const candidatesForComments = filtered.slice(0, Math.min(5, filtered.length));
  for (const cand of candidatesForComments) {
    cand.topComments = await fetchTopComments(cand.url, 3);
    if (cand.topComments.length > 0) {
      logger.info(`  "${cand.title.substring(0, 40)}" → ${cand.topComments.length} comments fetched`);
    } else {
      logger.info(`  "${cand.title.substring(0, 40)}" → no comments (private/disabled)`);
    }
  }

  // ─── Phase 3: Gemini Ranking (with batch retry loop ×3) ───────────
  // Each batch searches 6 new candidates, ranks them with per-video
  // 720p preview download→inline video API→CLI fallback. null results don't count,
  // only actual APPROVED/REJECTED responses from Gemini.
  logger.info('Phase 3: Gemini Ranking');
  let ranked = [];
  const MAX_BATCHES = 3;

  for (let batch = 1; batch <= MAX_BATCHES; batch++) {
    logger.header(`Ranking batch ${batch}/${MAX_BATCHES}`);

    if (batch > 1) {
      // Search a fresh batch of 6 candidates
      logger.info(`Batch ${batch}: Searching for fresh candidates...`);
      const newQueries = await generateQueries(country, gemini, trendBank);
      if (newQueries.length === 0) {
        logger.warn(`Batch ${batch}: No new queries generated — skipping`);
        continue;
      }
      candidates = await searchYouTube(newQueries, 6, country);
      filtered = filterCandidates(candidates);
      if (filtered.length < 2) {
        logger.warn(`Batch ${batch}: Only ${filtered.length} candidates — not enough to rank`);
        continue;
      }

      // Fetch top comments for new batch
      const newCandsForComments = filtered.slice(0, Math.min(5, filtered.length));
      for (const cand of newCandsForComments) {
        cand.topComments = await fetchTopComments(cand.url, 3);
      }
    }

    // Rank this batch — each video gets 720p preview→inline video API→CLI fallback
    ranked = await rankVideos(filtered, country, gemini, geminiCLI, curatorSkill, tmpDir);

    if (ranked.length > 0) {
      logger.success(`Batch ${batch}: Found ${ranked.length} approved videos — using best`);
      break;
    }

    logger.warn(`Batch ${batch}: All videos rejected or unrankable — trying next batch`);
  }

  // Ultimate fallback: highest-view from last batch
  if (ranked.length === 0) {
    logger.warn('All batches exhausted — using highest-view fallback');
    const shorts = (filtered || candidates || []).filter(c => c.duration <= 60 && c.duration > 0);
    if (shorts.length > 0) {
      const fb = shorts.sort((a, b) => b.view_count - a.view_count)[0];
      ranked.push({ ...fb, geminiScore: 5, hookScore: 5, geminiCountry: country });
      logger.warn(`Fallback: highest-view video "${fb.title.substring(0, 50)}" (score: 5/10)`);
    } else {
      logger.error('No fallback candidates — aborting');
      return { success: false, error: 'No approved videos' };
    }
  }

  const bestVideo = ranked[0];
  logger.success(`Best video: "${bestVideo.title.substring(0, 50)}" (score: ${bestVideo.geminiScore}/10)`);

  // ─── Country Recategorization ──────────────────────────────────────
  // Gemini detected the video's actual country — override if different
  if (bestVideo.geminiCountry && bestVideo.geminiCountry !== country) {
    logger.warn(`⚠️  Country recategorized: "${country}" → "${bestVideo.geminiCountry}"`);
    logger.warn(`   Gemini detected actual origin of "${bestVideo.title.substring(0, 40)}"`);
    country = bestVideo.geminiCountry;
    logger.info(`   Using "${country}" for signature, metadata, and memory`);
  }

  // ─── Phase 4: Download + Transcribe ────────────────────────────────
  logger.info('Phase 4: Download + Transcribe');
  const downloadedPath = await downloadBestVideo(bestVideo, tmpDir);

  if (!downloadedPath) {
    logger.error('Download failed — aborting');
    return { success: false, error: 'Download failed' };
  }

  // ─── Phase 4b: Highlight Detection (for videos > 2 min) ────────────
  // Run YOLO + OpenCV scoring pipeline to find the most interesting 30-60s segment
  let workingVideoPath = downloadedPath;
  const workingVideoDuration = probeDownloadedVideo(downloadedPath).duration || 0;
  if (workingVideoDuration > 120) {
    logger.info('Phase 4b: Highlight Detection');
    logger.info(`Video is ${workingVideoDuration.toFixed(0)}s — running YOLO highlight detector...`);
    try {
      const hlCmd = `python3 "${path.join(__dirname, '..', 'core', 'highlight-detector.py')}" --output-json "${downloadedPath}" 2>&1`;
      const hlOut = execSync(hlCmd, { timeout: 600000, maxBuffer: 10 * 1024 * 1024, encoding: 'utf8' }).toString().trim();
      // Last line is JSON output
      const lines = hlOut.split('\n').filter(Boolean);
      const hlResult = JSON.parse(lines[lines.length - 1]);

      if (hlResult.action === 'extract' && hlResult.start >= 0 && hlResult.duration > 0) {
        logger.success(`Highlight detected: ${hlResult.start}s → ${hlResult.end}s (${hlResult.duration}s, score: ${hlResult.peak_highlight_score || 'N/A'})`);
        const clippedPath = path.join(tmpDir, `highlight_${Date.now()}.mp4`);
        execSync(
          `ffmpeg -y -ss ${hlResult.start} -i "${downloadedPath}" -to ${hlResult.end} ` +
          `-c:v libx264 -preset fast -crf 0 -pix_fmt yuv444p -c:a aac -b:a 320k "${clippedPath}"`,
          { timeout: 180000 }
        );
        if (fs.existsSync(clippedPath) && fs.statSync(clippedPath).size > 100000) {
          workingVideoPath = clippedPath;
          logger.success(`Highlight clip extracted: ${(fs.statSync(clippedPath).size / 1024 / 1024).toFixed(1)}MB`);
        }
      } else {
        logger.info(`Highlight detector returned: ${hlResult.action} — using full video`);
      }
    } catch (e) {
      logger.warn(`Highlight detection failed: ${(e.message || '').substring(0, 80)} — using full video`);
    }
  } else {
    logger.info(`Video is ${workingVideoDuration.toFixed(0)}s — under 2 min, using full video`);
  }

  const dialogue = await transcribeAudio(workingVideoPath, tmpDir);

  // Check for profanity
  if (gemini.hasProfanity(dialogue.transcript)) {
    logger.error('Profanity detected — aborting');
    return { success: false, error: 'Profanity detected' };
  }

  // ─── Phase 5: Smart Crop ───────────────────────────────────────────
  logger.info('Phase 5: Smart Crop');
  const croppedPath = path.join(tmpDir, `cropped_${Date.now()}.mp4`);
  // Use the working video (full or highlight clip) and probe its actual duration
  const cropDuration = probeDownloadedVideo(workingVideoPath).duration || 30;
  const cropResult = await smartCrop(workingVideoPath, croppedPath, {
    country,
    duration: Math.min(cropDuration, 60),
    startTime: 3,
  });

  if (!cropResult.success) {
    logger.error('Crop failed — aborting');
    return { success: false, error: 'Crop failed' };
  }

  // ─── Phase 6: Smart Edit ───────────────────────────────────────────
  logger.info('Phase 6: Smart Edit');
  let translatedText = null;
  if (dialogue.hasDialogue && dialogue.language !== 'en' && dialogue.language !== 'english') {
    translatedText = await gemini.translate(dialogue.transcript);
  }

  const editedPath = path.join(tmpDir, `edited_${Date.now()}.mp4`);
  const editResult = await smartEdit(croppedPath, editedPath, {
    country,
    dialogue,
    translatedText,
    duration: Math.min(cropDuration, 60),
  });

  if (!editResult.success) {
    logger.error('Edit failed — aborting');
    return { success: false, error: 'Edit failed' };
  }

  // ─── Phase 7: Signature ────────────────────────────────────────────
  logger.info('Phase 7: Add Signature');
  const sigOutput = path.join(tmpDir, `signed_${Date.now()}.mp4`);
  const sigResult = await addSignature(editedPath, sigOutput, country, tmpDir);

  if (!sigResult) {
    logger.warn('Signature failed - preserving edited video without signature');
    try {
      fs.copyFileSync(editedPath, sigOutput);
    } catch (e) {
      logger.error(`Could not preserve edited video after signature failure: ${e.message}`);
      return { success: false, error: 'Signature failed' };
    }
  }

  // ─── Phase 7b: Watermark ──────────────────────────────────────────
  logger.info('Phase 7b: Add Watermark');
  const { addWatermark } = require('../core/watermark');
  const wmPath = path.join(tmpDir, `watermarked_${Date.now()}.mp4`);
  const wmResult = await addWatermark(sigOutput, wmPath);
  const tempFinalPath = wmResult || sigOutput;

  const safeCountry = String(country || 'global').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'global';
  const durableFinalPath = path.join(outputDir, `type1_${safeCountry}_${Date.now()}.mp4`);

  try {
    fs.copyFileSync(tempFinalPath, durableFinalPath);
  } catch (e) {
    logger.error(`Failed to preserve final video before QA/upload: ${e.message}`);
    return { success: false, error: 'Failed to preserve final video' };
  }

  if (!fs.existsSync(durableFinalPath) || fs.statSync(durableFinalPath).size < 100000) {
    logger.error('Durable final video missing or too small');
    return { success: false, error: 'Final video copy failed' };
  }
  const finalPath = durableFinalPath;

  // ─── Phase 8: QA Review ────────────────────────────────────────────
  logger.info('Phase 8: QA Review');

  // Automated validation
  const validation = await validateOutput(finalPath);
  if (!validation.passed) {
    logger.warn(`Validation issues: ${validation.issues.join(', ')}`);
    // Continue anyway if score is above 4 (some issues are acceptable)
    if (validation.score < 4) {
      logger.error('Validation score too low — aborting');
      return { success: false, error: `Validation failed: ${validation.issues.join('; ')}` };
    }
  }

  // Gemini visual review (via CLI — sends full MP4 for analysis)
  const geminiQA = await geminiReview(finalPath);
  logger.info(`Gemini QA: ${geminiQA.score}/10 — ${geminiQA.recommendation}`);

  if (geminiQA.recommendation === 'RENDER_AGAIN' && geminiQA.score < 5) {
    logger.warn('Gemini QA recommends re-render, but proceeding with current output');
  }

  // ─── Phase 9: Generate Metadata ────────────────────────────────────
  logger.info('Phase 9: Generate Metadata');
  const metadataContext = {
    reasoning: bestVideo.reasoning,
    searchQuery: bestVideo.searchQuery,
    hookScore: bestVideo.hookScore,
    geminiScore: bestVideo.geminiScore,
    editType: editResult.editType,
    hasCaptions: editResult.hasCaptions,
    sourceUrl: bestVideo.url,
  };
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
  logger.success(`Edit Type: ${editResult.editType}`);
  logger.success(`Captions: ${editResult.hasCaptions}`);

  return {
    success: true,
    videoPath: durableFinalPath,
    title,
    description,
    tags,
    country,
    geminiScore: bestVideo.geminiScore,
    editType: editResult.editType,
    hasCaptions: editResult.hasCaptions,
    sourceUrl: bestVideo.url,
  };
}

module.exports = { runType1Pipeline, loadTrendBank, generateQueries, searchYouTube };
