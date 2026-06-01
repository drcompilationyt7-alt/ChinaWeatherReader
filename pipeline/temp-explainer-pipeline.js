/**
 * Temp Explainer Pipeline — Channel Shorts Reposter
 * 
 * Picks a channel from a curated list per region, finds an old Short (>=3 months),
 * downloads it losslessly, uses Gemini to identify the country, overlays a flag
 * emoji at top-center (YOLO-verified to not block content), generates new metadata,
 * and returns the final video for upload.
 * 
 * Flow:
 *   1. Pick region → pick channel → scrape Shorts feed
 *   2. Filter by age (>=3 months) and dedup (memory)
 *   3. Download best quality (no re-encode)
 *   4. Gemini identifies country from video + title + description
 *   5. Download flag emoji PNG (twemoji CDN)
 *   6. YOLO verifies subject position → overlay flag top-center for first 4s
 *   7. Gemini generates new title + description
 *   8. Final QA (validateOutput + geminiReview)
 *   9. Save used video ID to memory
 */
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const { Logger } = require('../core/logger');
const { getGeminiService } = require('../core/gemini-service');
const { getGeminiCLI } = require('../core/gemini-cli-runner');
const { getVideoMetadata } = require('../core/explainer-downloader');
const { validateOutput, geminiReview } = require('../core/frame-qa');

const logger = new Logger('TempExplainerPipeline');

const MEMORY_FILE = path.join(__dirname, '..', 'memory', 'temp-explainer-memory.json');
const CHANNEL_SOURCES_FILE = path.join(__dirname, '..', 'config', 'channel-sources.json');
const SHORTS_W = 1080;
const SHORTS_H = 1920;

/**
 * Convert country name to flag emoji (e.g. "United States" → "🇺🇸")
 */
function getFlagEmoji(country) {
  const isoMap = {
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
  let iso = isoMap[country];
  if (!iso) {
    for (const [name, code] of Object.entries(isoMap)) {
      if (country.toLowerCase().includes(name.toLowerCase()) || name.toLowerCase().includes(country.toLowerCase())) {
        iso = code; break;
      }
    }
  }
  if (!iso) return '🌍';
  return String.fromCodePoint(...iso.split('').map(c => 0x1F1E6 + c.charCodeAt(0) - 65));
}

/**
 * Load dedup memory
 */
function loadMemory() {
  try {
    if (fs.existsSync(MEMORY_FILE)) {
      return JSON.parse(fs.readFileSync(MEMORY_FILE, 'utf8'));
    }
  } catch (e) {
    logger.warn(`Memory load: ${e.message}`);
  }
  return { usedVideoIds: [], usedChannels: [], lastRun: null };
}

/**
 * Save dedup memory
 */
function saveMemory(memory) {
  try {
    if (!fs.existsSync(path.dirname(MEMORY_FILE))) {
      fs.mkdirSync(path.dirname(MEMORY_FILE), { recursive: true });
    }
    fs.writeFileSync(MEMORY_FILE, JSON.stringify(memory, null, 2));
    logger.success(`Memory saved: ${memory.usedVideoIds.length} used video IDs`);
  } catch (e) {
    logger.warn(`Memory save: ${e.message}`);
  }
}

/**
 * Load channel sources config
 */
function loadChannelSources() {
  try {
    if (fs.existsSync(CHANNEL_SOURCES_FILE)) {
      return JSON.parse(fs.readFileSync(CHANNEL_SOURCES_FILE, 'utf8'));
    }
  } catch (e) {
    logger.error(`Channel sources load: ${e.message}`);
  }
  return null;
}

/**
 * Step 1: Pick a random region and random channel from that region.
 * Avoids recently used channels if possible.
 */
function pickChannel(memory) {
  const sources = loadChannelSources();
  if (!sources) {
    logger.error('No channel sources configured');
    return null;
  }

  const regions = Object.keys(sources);
  const shuffledRegions = [...regions].sort(() => Math.random() - 0.5);

  for (const region of shuffledRegions) {
    const regionData = sources[region];
    const channels = regionData.channels;

    // Filter out recently used channels
    const usableChannels = channels.filter(c => !(memory.usedChannels || []).includes(c));
    const pool = usableChannels.length > 0 ? usableChannels : channels;

    const channelUrl = pool[Math.floor(Math.random() * pool.length)];
    const handleMatch = channelUrl.match(/@([\w-]+)/);
    const handle = handleMatch ? handleMatch[1] : channelUrl;

    logger.info(`Picked region: ${region} (${regionData.name}) → channel: @${handle}`);
    return { region, regionName: regionData.name, channelUrl, handle };
  }

  return null;
}

/**
 * Step 2: Scrape channel Shorts feed and find a video >=1 year old not in usedVideoIds
 */
function findOldShort(channelInfo, memory) {
  logger.info(`Scraping Shorts from @${channelInfo.handle}...`);

  const threeMonthsAgo = new Date();
  threeMonthsAgo.setMonth(threeMonthsAgo.getMonth() - 3);
  const dateBefore = threeMonthsAgo.toISOString().split('T')[0].replace(/-/g, '');

  try {
    const cmd = `yt-dlp --flat-playlist --dump-json ` +
      `--datebefore ${dateBefore} ` +
      `--playlist-end 50 ` +
      `--match-filter "!is_live & !upcoming" ` +
      `"${channelInfo.channelUrl}" 2>&1`;

    const out = execSync(cmd, { timeout: 30000, maxBuffer: 5 * 1024 * 1024, encoding: 'utf8' }).toString().trim();

    if (!out) {
      logger.warn(`No shorts older than 1 year for @${channelInfo.handle}`);
      return null;
    }

    const lines = out.split('\n').filter(Boolean);
    logger.info(`Found ${lines.length} shorts older than 1 year`);

    const candidates = [];
    for (const line of lines) {
      try {
        const p = JSON.parse(line);
        if (p.id && !(memory.usedVideoIds || []).includes(p.id)) {
          candidates.push({
            id: p.id,
            url: `https://www.youtube.com/shorts/${p.id}`,
            title: p.title || 'YouTube Short',
            duration: p.duration || 0,
            upload_date: p.upload_date || '',
            view_count: p.view_count || 0,
          });
        }
      } catch {}
    }

    logger.info(`After dedup: ${candidates.length} candidates`);

    if (candidates.length === 0) {
      logger.warn('All shorts from this channel have been used — marking channel as used');
      if (!memory.usedChannels) memory.usedChannels = [];
      memory.usedChannels.push(channelInfo.channelUrl);
      saveMemory(memory);
      return null;
    }

    const picked = candidates[Math.floor(Math.random() * candidates.length)];
    logger.info(`Picked: "${(picked.title || 'Untitled').substring(0, 50)}" (${picked.duration}s, ${picked.upload_date || 'unknown'})`);
    return picked;
  } catch (e) {
    logger.warn(`Channel scrape failed: ${(e.message || '').substring(0, 80)}`);
    return null;
  }
}

/**
 * Step 3: Download at max quality, prefer 1080p
 */
function downloadMaxQuality(video, outputDir) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });

  const outputFile = path.join(outputDir, `source_${video.id}.mp4`);
  logger.info(`Downloading: ${video.url} (1080p)`);

  // Sort by resolution (prefer 1080p) for best quality
  const strategies = [
    {
      name: 'web_best',
      args: '--extractor-args "youtube:player_client=web"',
      format: '-f "bestvideo+bestaudio/best" --merge-output-format mkv',
    },
    {
      name: 'default_best',
      args: '',
      format: '-f "bestvideo+bestaudio/best" --merge-output-format mkv',
    },
    {
      name: 'android_best',
      args: '--extractor-args "youtube:player_client=android"',
      format: '-f "bestvideo+bestaudio/best" --merge-output-format mkv',
    },
    {
      name: 'fallback_mp4',
      args: '',
      format: '-f "bestvideo+bestaudio/best" --merge-output-format mp4',
    },
  ];

  for (const s of strategies) {
    try {
      const hasCookies = fs.existsSync('/tmp/yt_cookies.txt');
      // Don't pass cookies with android client (it rejects them)
      const cookieArg = (hasCookies && !s.name.includes('android')) ? '--cookies "/tmp/yt_cookies.txt"' : '';

      const cmd = `yt-dlp ${cookieArg} ${s.args} ${s.format} ` +
        `-o "${outputFile}" "${video.url}" ` +
        `--no-playlist --socket-timeout 30 --retries 3 --force-ipv4 ` +
        `--remote-components ejs:github 2>&1`;

      execSync(cmd, { timeout: 300000, maxBuffer: 200 * 1024 * 1024 });

      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000)
        .sort((a, b) => fs.statSync(path.join(outputDir, b)).mtimeMs - fs.statSync(path.join(outputDir, a)).mtimeMs);

      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        logger.success(`Downloaded: ${files[0]} (${(fs.statSync(fp).size / 1024 / 1024).toFixed(1)}MB)`);
        return fp;
      }
    } catch (e) {
      logger.warn(`Strategy ${s.name} failed: ${(e.message || '').substring(0, 60)}`);
    }
  }

  logger.error(`All download strategies failed`);
  return null;
}

/**
 * Step 4: Gemini identifies country from video frames + title
 */
async function identifyCountry(videoPath, title, gemini) {
  logger.info('Identifying country from video content...');

  const tmpDir = path.dirname(videoPath);
  const frameDir = path.join(tmpDir, `country_frames_${Date.now()}`);
  fs.mkdirSync(frameDir, { recursive: true });

  const meta = getVideoMetadata(videoPath);
  const duration = meta.duration || 30;
  const positions = [1, Math.max(2, duration * 0.3), Math.max(3, duration * 0.6), Math.max(4, duration - 2)];

  const { extractFrames } = require('../core/smart-cropper');
  const frames = extractFrames(videoPath, frameDir, positions);

  let country = null;
  let confidence = 0;

  if (frames.length >= 2) {
    const prompt = `Analyze these frames from a travel/shorts video.

Video Title: "${title || 'Unknown'}"

Identify the country or region shown in this video. Look at:
1. Landmarks, architecture, scenery
2. Signs, writing, language visible
3. Food, clothing, cultural elements
4. The video title for hints

Return STRICT JSON: {"country": "Country Name", "confidence": 0-10, "reasoning": "brief explanation"}`;

    const result = await gemini.analyzeFrames(frames, prompt,
      'You are a geography and travel expert. Identify countries from video content.');

    try { fs.rmSync(frameDir, { recursive: true, force: true }); } catch {}

    if (result) {
      try {
        const m = result.match(/\{[\s\S]*\}/);
        if (m) {
          const p = JSON.parse(m[0]);
          if (p.country && p.confidence >= 4) {
            country = p.country;
            confidence = p.confidence;
            logger.success(`Country: ${country} (${confidence}/10) — ${(p.reasoning || '').substring(0, 100)}`);
            return { country, confidence };
          }
          logger.warn(`Low confidence: ${p.country || 'none'} (${p.confidence}/10)`);
        }
      } catch (e) {
        logger.warn(`Country parse: ${e.message}`);
      }
    }
  } else {
    try { fs.rmSync(frameDir, { recursive: true, force: true }); } catch {}
  }

  // Fallback: text-only title analysis
  logger.info('Falling back to title-based country identification...');
  const textResult = await gemini.chat(
    'You identify countries from YouTube video titles. Return STRICT JSON: {"country": "Country Name"}',
    `Identify the country from this video title: "${title || 'Unknown'}"`,
    { temperature: 0.3, maxTokens: 200 }
  );
  if (textResult) {
    try {
      const m = textResult.match(/\{[\s\S]*\}/);
      if (m) {
        const p = JSON.parse(m[0]);
        if (p.country) {
          country = p.country;
          confidence = 5;
          logger.success(`Country (title fallback): ${country}`);
        }
      }
    } catch {}
  }

  return { country, confidence };
}

/**
 * Step 5: Download country flag emoji PNG from twemoji CDN, scale to 120x120
 */
async function downloadFlag(country, tmpDir) {
  const isoMap = {
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

  let iso = isoMap[country];
  if (!iso) {
    for (const [name, code] of Object.entries(isoMap)) {
      if (country.toLowerCase().includes(name.toLowerCase()) || name.toLowerCase().includes(country.toLowerCase())) {
        iso = code;
        break;
      }
    }
  }

  if (!iso) {
    logger.warn(`No ISO code for: ${country}`);
    return null;
  }

  const flagFile = path.join(tmpDir, `flag_${iso}.png`);
  try {
    const cp1 = 0x1f1e6 + (iso.charCodeAt(0) - 65);
    const cp2 = 0x1f1e6 + (iso.charCodeAt(1) - 65);
    const flagFilename = `${cp1.toString(16)}-${cp2.toString(16)}.png`;
    const url = `https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/${flagFilename}`;

    const axios = require('axios');
    const response = await axios({ method: 'GET', url, responseType: 'stream', timeout: 10000 });
    const writer = fs.createWriteStream(flagFile);
    response.data.pipe(writer);
    await new Promise((resolve, reject) => { writer.on('finish', resolve); writer.on('error', reject); });

    if (fs.existsSync(flagFile) && fs.statSync(flagFile).size > 100) {
      const scaledFlag = path.join(tmpDir, `flag_${iso}_scaled.png`);
      execSync(
        `ffmpeg -y -i "${flagFile}" -vf "scale=150:150:flags=lanczos" "${scaledFlag}" 2>/dev/null`,
        { timeout: 10000 }
      );
      const result = (fs.existsSync(scaledFlag) && fs.statSync(scaledFlag).size > 100) ? scaledFlag : flagFile;
      logger.success(`Flag: ${iso} (${(fs.statSync(result).size / 1024).toFixed(1)}KB)`);
      return result;
    }
  } catch (e) {
    logger.warn(`Flag download: ${(e.message || '').substring(0, 60)}`);
  }
  return null;
}

/**
 * Step 6: Overlay flag at top-center for first few seconds
 * Appears at top-center (x=480, y=20) for first 4 seconds of the video.
 * If video is not 9:16, first scales+crops to 1080x1920 then overlays.
 * YOLO check (if available) adjusts flag Y to avoid overlapping subjects.
 */
async function overlayFlag(videoPath, flagPath, outputPath, country, tmpDir) {
  logger.info('Overlaying flag at top-center...');

  let videoDuration = 30;
  try {
    const durOut = execSync(
      `ffprobe -i "${videoPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`,
      { timeout: 5000, encoding: 'utf8' }
    ).trim();
    if (durOut) videoDuration = parseFloat(durOut);
  } catch {}

  const flagDuration = Math.min(4, videoDuration - 1);
  const flagWidth = 150;
  const flagHeight = 150;
  const flagX = Math.floor((SHORTS_W - flagWidth) / 2); // centered: (1080-150)/2 = 465
  const flagY = 20;

  // Quick YOLO check for flag placement (skip silently if unavailable)
  let adjustedY = flagY;
  try {
    if (fs.existsSync(path.join(__dirname, '..', 'core', 'yolo-crop.py'))) {
      const yoloDir = path.join(tmpDir, `yolo_flag_${Date.now()}`);
      fs.mkdirSync(yoloDir, { recursive: true });
      const framePath = path.join(yoloDir, 'flag_check.jpg');
      execSync(
        `ffmpeg -y -ss 1 -i "${videoPath}" -vframes 1 -q:v 2 "${framePath}" 2>/dev/null`,
        { timeout: 10000 }
      );
      if (fs.existsSync(framePath) && fs.statSync(framePath).size > 1000) {
        const yoloOut = execSync(
          `python3 "${path.join(__dirname, '..', 'core', 'yolo-crop.py')}" "${framePath}" 2>&1`,
          { timeout: 15000, encoding: 'utf8' }
        ).toString().trim();
        const yoloResult = JSON.parse(yoloOut);
        if (yoloResult.subject !== 'none') {
          const bbox = yoloResult.bbox || { y: 0, h: 0 };
          const subjectBottom = (bbox.y || 0) + (bbox.h || 0);
          if (subjectBottom < flagY + flagHeight + 100) {
            adjustedY = Math.min(subjectBottom + 20, 800);
            logger.info(`YOLO: adjusted flag Y to ${adjustedY}px`);
          }
        }
      }
      try { fs.rmSync(yoloDir, { recursive: true, force: true }); } catch {}
    }
  } catch {}

  // Check dimensions for scaling
  const srcDims = getVideoMetadata(videoPath);
  const isShortsSize = Math.abs(srcDims.width / srcDims.height - SHORTS_W / SHORTS_H) < 0.05;

  // Build FFmpeg filter:
  // If not 9:16, scale+crop first (tag output as [bg]), then overlay flag
  // If already 9:16, just overlay flag directly
  // Gentle colorkey removes green anti-aliasing fringe from twemoji edges
  // without affecting actual video content (low similarity threshold)
  let overlayFilter;
  if (!isShortsSize || srcDims.width !== SHORTS_W || srcDims.height !== SHORTS_H) {
    overlayFilter =
      `[0:v]scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos:force_original_aspect_ratio=increase,pad=${SHORTS_W}:${SHORTS_H}:(ow-iw)/2:(oh-ih)/2:color=black[bg];` +
      `[1:v]colorkey=0x00FF00:0.05:0.05,format=rgba[flag];` +
      `[bg][flag]overlay=${flagX}:${adjustedY}:enable='between(t,0,${flagDuration})'`;
  } else {
    overlayFilter =
      `[1:v]colorkey=0x00FF00:0.05:0.05,format=rgba[flag];` +
      `[0:v][flag]overlay=${flagX}:${adjustedY}:enable='between(t,0,${flagDuration})'`;
  }

  const outPath = outputPath || videoPath.replace(/\.\w+$/, '_flagged.mp4');

  try {
    execSync(
      `ffmpeg -y -i "${videoPath}" -i "${flagPath}" ` +
      `-filter_complex "${overlayFilter}" ` +
      `-c:v libx264 -preset veryslow -crf 0 -c:a aac -b:a 320k -pix_fmt yuv444p -shortest "${outPath}"`,
      { timeout: 180000 }
    );

    if (fs.existsSync(outPath) && fs.statSync(outPath).size > 100000) {
      const sizeMB = (fs.statSync(outPath).size / 1024 / 1024).toFixed(1);
      logger.success(`Flag overlayed: ${sizeMB}MB`);
      return outPath;
    }
  } catch (e) {
    logger.warn(`Flag overlay failed: ${(e.message || '').substring(0, 80)}`);
  }

  // Fallback: return original
  return videoPath;
}

/**
 * Step 7: Gemini generates new title and description using the same
 * generateTitle() method as Type 1 pipeline — with full key rotation.
 * Falls back to OpenRouter only after all Gemini keys/models exhausted.
 */
async function generateMetadata(country, originalTitle, gemini) {
  logger.info('Generating new title and description...');

  const flagEmoji = getFlagEmoji(country);

  // Use Type 1's generateTitle which has 2 retry cycles × 8 keys × 2 models
  // This exhausts ALL Gemini capacity before falling back
  const metadataContext = {
    sourceUrl: null,
    originalTitle: originalTitle,
  };
  let result = await gemini.generateTitle(country, '', originalTitle, metadataContext);

  // If Gemini fully exhausted, try OpenRouter as final fallback
  if (!result || !result.title) {
    logger.warn('All Gemini keys/models exhausted for metadata — trying OpenRouter fallback');
    const { getOpenRouterQA } = require('../core/openrouter-qa');
    const qa = getOpenRouterQA();

    const skillPath = path.join(__dirname, '..', 'skills', 'viral-metadata-generator.md');
    let systemPrompt = '';
    try {
      if (fs.existsSync(skillPath)) {
        systemPrompt = fs.readFileSync(skillPath, 'utf8');
      }
    } catch (e) {}
    systemPrompt = systemPrompt || `You write YouTube Shorts titles and descriptions for a travel channel called "Mr. WorldWideWebster".
Title: max 50 chars, emoji-heavy, curiosity gap, mentions the country. Description: hook + engagement CTA + 3 hashtags.
Do NOT reference the original video title/channel. Add "Follow Mr. WorldWideWebster" with globe emoji at the end of description.`;

    const userMessage = `Generate a YouTube Shorts title and description for a travel video from ${country}.
Original video title: "${originalTitle || 'Unknown'}"
Country: ${country}

Vibe/Tone: exciting, travel, discovery
Source/Category: ${country.toLowerCase()} travel shorts

Return STRICT JSON: {"title": "...", "description": "...", "tags": ["tag1", "tag2", "tag3"]}`;

    const orResult = await qa.chat(
      systemPrompt + '\n\nIMPORTANT: Respond ONLY with valid JSON.',
      userMessage,
      { temperature: 0.8, maxTokens: 512, timeout: 30000 }
    );
    if (orResult) {
      try {
        const m = orResult.match(/\{[\s\S]*\}/);
        if (m) result = JSON.parse(m[0]);
      } catch (e) {
        logger.warn(`OpenRouter JSON parse failed: ${e.message.substring(0, 50)}`);
      }
    }
  }

  if (result && result.title) {
    const title = result.title.substring(0, 50);
    let description = result.description || `Amazing travel short from ${country}! Follow Mr. WorldWideWebster for more! 🌍✈️`;
    if (!description.includes('Mr. WorldWideWebster')) {
      description += `\n\nFollow Mr. WorldWideWebster for more! ${flagEmoji}🌍✈️`;
    }
    const tags = result.tags || ['mr worldwidewebster', 'shorts', country.toLowerCase(), 'travel'];
    logger.success(`Title: "${title}"`);
    return { title, description, tags };
  }

  // Ultimate fallback — should never happen
  logger.error('All LLM providers exhausted for metadata — using fallback');
  return {
    title: `${country} Travel Short 🔥`.substring(0, 50),
    description: `Incredible scenes from ${country}. Follow Mr. WorldWideWebster for global travel content! ${flagEmoji}🌍✈️`,
    tags: ['mr worldwidewebster', 'shorts', country.toLowerCase(), 'travel'],
  };
}

/**
 * Main Temp Explainer Pipeline Entry Point
 * 
 * @param {Object} options
 * @param {string} options.outputDir - Output directory
 * @param {Object} options.memory - Shared memory object (optional)
 * @returns {Object} - { success, videoPath, title, description, tags, country }
 */
async function runTempExplainerPipeline(options = {}) {
  const outputDir = options.outputDir || path.join(__dirname, '..', 'output', 'temp-explainer');
  const tmpDir = path.join(outputDir, `tmp_${Date.now()}`);
  if (!fs.existsSync(tmpDir)) fs.mkdirSync(tmpDir, { recursive: true });

  logger.header('TEMP EXPLAINER PIPELINE');
  logger.info(`Output: ${outputDir}`);

  const gemini = getGeminiService();
  const memory = loadMemory();
  const geminiCLI = getGeminiCLI();

  // ─── Step 1: Pick Channel ──────────────────────────────────────────
  logger.header('STEP 1: PICK CHANNEL');
  const channelInfo = pickChannel(memory);
  if (!channelInfo) {
    logger.error('No channels available');
    return { success: false, error: 'No channels' };
  }
  logger.info(`Channel: @${channelInfo.handle} (${channelInfo.region})`);

  // ─── Step 2: Find Old Short ────────────────────────────────────────
  logger.header('STEP 2: FIND OLD SHORT');
  const video = findOldShort(channelInfo, memory);
  if (!video) {
    logger.warn('No old shorts — trying different channel');
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}
    return { success: false, error: 'No old shorts found' };
  }

  // ─── Step 3: Download Max Quality ──────────────────────────────────
  logger.header('STEP 3: DOWNLOAD');
  const downloadedPath = downloadMaxQuality(video, tmpDir);
  if (!downloadedPath) {
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}
    return { success: false, error: 'Download failed' };
  }

  // ─── Step 4: Identify Country ──────────────────────────────────────
  logger.header('STEP 4: IDENTIFY COUNTRY');
  const { country, confidence } = await identifyCountry(downloadedPath, video.title, gemini);
  if (!country || confidence < 4) {
    logger.warn(`Country not confidently identified (${country || 'none'}) — using region`);
    // Fallback: use region as country
    const finalCountry = channelInfo.region === 'World' ? 'Global' : channelInfo.region;
    logger.info(`Using region as country: ${finalCountry}`);
  }

  const finalCountry = country || (channelInfo.region === 'World' ? 'Global' : channelInfo.region);
  logger.success(`Final country: ${finalCountry}`);

  const { addWatermark } = require('../core/watermark');

  // ─── Step 5: Download Flag ─────────────────────────────────────────
  logger.header('STEP 5: DOWNLOAD FLAG');
  const flagPath = await downloadFlag(finalCountry, tmpDir);

  // ─── Step 6: Overlay Flag ──────────────────────────────────────────
  logger.header('STEP 6: OVERLAY FLAG');
  let flaggedPath = downloadedPath;
  if (flagPath) {
    flaggedPath = path.join(tmpDir, `flagged_${video.id}.mp4`);
    flaggedPath = await overlayFlag(downloadedPath, flagPath, flaggedPath, finalCountry, tmpDir);
  } else {
    logger.warn('No flag available — skipping overlay');
  }

  // ─── Step 7: Generate Metadata ─────────────────────────────────────
  logger.header('STEP 7: GENERATE METADATA');
  const metadata = await generateMetadata(finalCountry, video.title, gemini);

  // ─── Step 8: Final QA ──────────────────────────────────────────────
  logger.header('STEP 8: FINAL QA');

  const validation = await validateOutput(flaggedPath);
  if (!validation.passed) {
    logger.warn(`QA issues: ${validation.issues.join(', ')}`);
  }

  // Gemini CLI visual review (if available)
  if (geminiCLI.isAvailable()) {
    const gqa = await geminiReview(flaggedPath);
    logger.info(`Gemini QA: ${gqa.score}/10 — ${gqa.recommendation}`);
  }

  // ─── Step 9: Save Memory ──────────────────────────────────────────
  logger.header('STEP 9: SAVE MEMORY');
  memory.usedVideoIds.push(video.id);
  memory.lastRun = new Date().toISOString();
  saveMemory(memory);

  // ─── Step 8b: Watermark ──────────────────────────────────────────
  logger.header('STEP 8b: ADD WATERMARK');
  const watermarkedPath = path.join(tmpDir, `watermarked_${video.id}.mp4`);
  const wmResult = await addWatermark(flaggedPath, watermarkedPath);
  const finalOutputPath = wmResult || flaggedPath;

  // Copy to output with clean name
  const finalPath = path.join(outputDir, `temp_${video.id}.mp4`);
  try {
    fs.copyFileSync(finalOutputPath, finalPath);
  } catch (e) {
    logger.warn(`Copy failed: ${e.message}`);
  }

  // Cleanup tmp
  try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}

  logger.header('PIPELINE COMPLETE');
  logger.success(`Video: ${finalPath}`);
  logger.success(`Source: @${channelInfo.handle} (${video.id})`);
  logger.success(`Country: ${finalCountry}`);
  logger.success(`Title: ${metadata.title}`);

  return {
    success: true,
    videoPath: finalPath,
    title: metadata.title,
    description: metadata.description,
    tags: metadata.tags,
    country: finalCountry,
    sourceId: video.id,
    sourceChannel: channelInfo.handle,
    sourceUrl: video.url,
  };
}

module.exports = { runTempExplainerPipeline };
