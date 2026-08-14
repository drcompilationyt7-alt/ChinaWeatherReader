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
 *   9. Return result (memory saved by runner after successful upload)
 * 
 * Retry: Steps 1-4 (dialogue check) are wrapped in a retry loop
 * so we keep trying different channels until we find one with dialogue.
 */
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const { Logger } = require('../core/logger');
const { getGeminiService } = require('../core/gemini-service');
const { getGeminiCLI } = require('../core/gemini-cli-runner');
const { getVideoMetadata } = require('../core/explainer-downloader');
const { detectDialogue } = require('../core/smart-editor');
const { addWatermark } = require('../core/watermark');
const { validateOutput, geminiReview } = require('../core/frame-qa');

const logger = new Logger('TempExplainerPipeline');

const MEMORY_FILE = path.join(__dirname, '..', 'memory', 'temp-explainer-memory.json');
const CHANNEL_SOURCES_FILE = path.join(__dirname, '..', 'config', 'channel-sources.json');
const SHORTS_W = 1080;
const SHORTS_H = 1920;

// Type 1 matching watermark constants
const LOGO_SIZE = 80;
const MARGIN_RIGHT = 20;
const MARGIN_BOTTOM = 80;
const FONT_SIZE = 28;
const TEXT = '@Mr.WorldWideWebster';

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
 * Load dedup memory — resilient to corrupt JSON
 */
function loadMemory() {
  try {
    if (fs.existsSync(MEMORY_FILE)) {
      const raw = fs.readFileSync(MEMORY_FILE, 'utf8');
      try {
        return JSON.parse(raw);
      } catch (parseErr) {
        logger.warn(`Corrupted memory JSON (${parseErr.message}) — resetting`);
        // Reset corrupted file
        try {
          fs.writeFileSync(MEMORY_FILE, JSON.stringify({ usedVideoIds: [], usedChannels: [], lastRun: null }, null, 2));
        } catch {}
      }
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

  // FIFA / football source removed — football channels now live, disabled,
  // in config/channel-pool.json. Only Asian regions remain in
  // channel-sources.json, so we pick randomly among them below.
  const regions = Object.keys(sources);
  const shuffledRegions = [...regions].sort(() => Math.random() - 0.5);

  for (const region of shuffledRegions) {
    const regionData = sources[region];
    const channels = regionData.channels;

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

  const fourMonthsAgo = new Date();
  fourMonthsAgo.setMonth(fourMonthsAgo.getMonth() - 4);
  const dateBefore = fourMonthsAgo.toISOString().split('T')[0].replace(/-/g, '');

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
 * Step 3: Download at max quality (exact copy of Type 1's downloadBestVideo)
 */
function downloadMaxQuality(video, outputDir) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
  const outputStem = `source_${Date.now()}`;
  const outputTemplate = path.join(outputDir, `${outputStem}.%(ext)s`);
  const url = video.url;
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

function probeVideoDims(fp) {
  try {
    const out = execSync(`ffprobe -v error -select_streams v:0 -show_entries stream=width,height,duration -of csv=p=0 "${fp}"`, { timeout: 10000, encoding: 'utf8' }).trim();
    const [width, height, duration] = out.split(',').map(s => Number.parseFloat(s.trim()));
    if (Number.isFinite(width) && Number.isFinite(height)) return { width: Math.round(width), height: Math.round(height), duration: Number.isFinite(duration) ? duration : 0 };
  } catch {}
  return { width: 0, height: 0, duration: 0 };
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
 * Step 5: Download country flag emoji PNG from twemoji CDN, scale to 150x150
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

    // Use curl to download the raw 72x72 PNG (no pre-scaling — let the overlay filter handle it, matching Type 1's approach)
    execSync(`curl -sL -o "${flagFile}" "${url}"`, { timeout: 10000 });

    if (fs.existsSync(flagFile) && fs.statSync(flagFile).size > 100) {
      logger.success(`Flag: ${iso} (${(fs.statSync(flagFile).size / 1024).toFixed(1)}KB)`);
      return flagFile;
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
  // No colorkey needed — twemoji PNGs have proper alpha transparency
  // Uses format=rgba to preserve alpha channel in the filter chain
  let overlayFilter;
  if (!isShortsSize || srcDims.width !== SHORTS_W || srcDims.height !== SHORTS_H) {
    overlayFilter =
      `[0:v]scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos:force_original_aspect_ratio=increase,pad=${SHORTS_W}:${SHORTS_H}:(ow-iw)/2:(oh-ih)/2:color=black[bg];` +
      `[1:v]scale=120:-1,format=rgba[flag];` +
      `[bg][flag]overlay=${flagX}:${adjustedY}:enable='between(t,0,${flagDuration})'`;
  } else {
    overlayFilter =
      `[1:v]scale=120:-1,format=rgba[flag];` +
      `[0:v][flag]overlay=${flagX}:${adjustedY}:enable='between(t,0,${flagDuration})'`;
  }

  const outPath = outputPath || videoPath.replace(/\.\w+$/, '_flagged.mp4');

  try {
    execSync(
      `ffmpeg -y -i "${videoPath}" -i "${flagPath}" ` +
      `-filter_complex "${overlayFilter}" ` +
      `-c:v ffv1 -level 3 -coder 1 -context 1 -g 1 -slices 16 -slicecrc 1 -pix_fmt yuv444p10le -c:a flac -ar 48000 -shortest -strict experimental "${outPath}"`,
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
 * Now accepts transcript and metadataContext like Type 1.
 */
async function generateMetadata(country, transcript, originalTitle, gemini, metadataContext) {
  logger.info('Generating new title and description...');

  const flagEmoji = getFlagEmoji(country);

  // Use Type 1's generateTitle which has 2 retry cycles — 8 keys — 2 models
  // This exhausts ALL Gemini capacity before falling back
  const context = metadataContext || {
    sourceUrl: null,
    originalTitle: originalTitle,
  };
  let result = await gemini.generateTitle(country, transcript || '', originalTitle, context);

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
    systemPrompt = systemPrompt || `You write YouTube Shorts titles and descriptions for an Asian edits channel called "Asian Edits".
Title: max 50 chars, emoji-heavy, curiosity gap, mentions the country. Description: hook + engagement CTA + 3 hashtags.
Do NOT reference the original video title/channel. Add "Follow Asian Edits" with globe emoji at the end of the description.`;

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
    let description = result.description || `Amazing travel short from ${country}! Follow Asian Edits for more! 🌍✈️`;
    if (!description.includes('Asian Edits')) {
      description += `\n\nFollow Asian Edits for more! ${flagEmoji}🌍✈️`;
    }
    const tags = result.tags || ['asian edits', 'shorts', country.toLowerCase(), 'travel'];
    logger.success(`Title: "${title}"`);
    return { title, description, tags };
  }

  // Ultimate fallback — should never happen
  logger.error('All LLM providers exhausted for metadata — using fallback');
  return {
    title: `${country} Travel Short 🔥`.substring(0, 50),
    description: `Incredible scenes from ${country}. Follow Asian Edits for more Asian content! ${flagEmoji}🌍✈️`,
    tags: ['asian edits', 'shorts', country.toLowerCase(), 'travel'],
  };
}

/**
 * Main Temp Explainer Pipeline Entry Point
 * 
 * Wraps Steps 1-4 (channel pick → find short → download → dialogue check)
 * in a retry loop so we keep trying different videos until we find one
 * with dialogue. Returns failure only after all retries exhausted.
 * 
 * @param {Object} options
 * @param {string} options.outputDir - Output directory
 * @param {Object} options.memory - Shared memory object (optional)
 * @returns {Object} - { success, videoPath, title, description, tags, country }
 */
async function runTempExplainerPipeline(options = {}) {
  const outputDir = options.outputDir || path.join(__dirname, '..', 'output', 'temp-explainer');
  const tmpBaseDir = path.join(outputDir, `tmp_${Date.now()}`);
  if (!fs.existsSync(tmpBaseDir)) fs.mkdirSync(tmpBaseDir, { recursive: true });

  logger.header('TEMP EXPLAINER PIPELINE');
  logger.info(`Output: ${outputDir}`);

  const gemini = getGeminiService();
  const memory = loadMemory();
  const geminiCLI = getGeminiCLI();

  // Retry loop: keep trying different channels/videos until we find one with dialogue
  const MAX_RETRIES = 5;
  let lastError = '';

  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    const tmpDir = path.join(tmpBaseDir, `attempt_${attempt}`);
    if (!fs.existsSync(tmpDir)) fs.mkdirSync(tmpDir, { recursive: true });

    logger.header(`ATTEMPT ${attempt}/${MAX_RETRIES}`);
    logger.header(`STEP 1: PICK CHANNEL`);
    const channelInfo = pickChannel(memory);
    if (!channelInfo) {
      lastError = 'No channels available';
      logger.warn(`Attempt ${attempt}: ${lastError}`);
      continue;
    }
    logger.info(`Channel: @${channelInfo.handle} (${channelInfo.region})`);

    logger.header(`STEP 2: FIND OLD SHORT`);
    const video = findOldShort(channelInfo, memory);
    if (!video) {
      lastError = 'No old shorts found';
      logger.warn(`Attempt ${attempt}: ${lastError}`);
      try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}
      continue;
    }

    logger.header(`STEP 3: DOWNLOAD`);
    const downloadedPath = downloadMaxQuality(video, tmpDir);
    if (!downloadedPath) {
      lastError = 'Download failed';
      logger.warn(`Attempt ${attempt}: ${lastError}`);
      try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}
      continue;
    }

    // ─── STEP 4: EXPLAINER DIALOGUE CHECK ───────────────────────────
    logger.header(`STEP 4: EXPLAINER DIALOGUE CHECK`);
    let hasDialogue = false;
    let dialogueTranscript = '';
    let originalDescription = video.description || video.title || '';
    try {
      const dialogueCheck = await detectDialogue(downloadedPath);
      hasDialogue = dialogueCheck.hasDialogue && dialogueCheck.wordCount > 5;
      dialogueTranscript = dialogueCheck.transcript || '';
      if (hasDialogue) {
        logger.success(`Dialogue detected: ${dialogueCheck.wordCount} words (${dialogueCheck.language})`);
      } else {
        logger.warn(`No dialogue detected (${dialogueCheck.wordCount} words) — not an explainer video`);
      }
    } catch (e) {
      logger.warn(`Dialogue check failed: ${(e.message || '').substring(0, 60)} — proceeding anyway`);
      hasDialogue = true; // If we can't check, assume it has dialogue
    }

    if (!hasDialogue) {
      lastError = 'No dialogue — not an explainer video';
      logger.warn(`Attempt ${attempt}: ${lastError}`);
      // Mark video as used so we don't try it again
      memory.usedVideoIds.push(video.id);
      memory.lastRun = new Date().toISOString();
      saveMemory(memory);
      try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}
      continue; // Try next attempt
    }

    // ─── If we get here, dialogue was detected — proceed with rest of pipeline ───

    // ─── STEP 5: IDENTIFY COUNTRY ────────────────────────────────────
    logger.header('STEP 5: IDENTIFY COUNTRY');
    const { country, confidence } = await identifyCountry(downloadedPath, video.title, gemini);
    if (!country || confidence < 4) {
      logger.warn(`Country not confidently identified (${country || 'none'}) — using region`);
      const finalCountry = channelInfo.region === 'World' ? 'Global' : channelInfo.region;
      logger.info(`Using region as country: ${finalCountry}`);
    }

    const finalCountry = country || (channelInfo.region === 'World' ? 'Global' : channelInfo.region);
    logger.success(`Final country: ${finalCountry}`);

    // ─── STEP 6: DOWNLOAD FLAG ───────────────────────────────────────
    logger.header('STEP 6: DOWNLOAD FLAG');
    const flagPath = await downloadFlag(finalCountry, tmpDir);
    if (flagPath) logger.success('Flag downloaded for combined render');
    
    // ─── STEP 7: COMBINED RENDER (Flag → Watermark → FFV1 1080x1920) ─
    logger.header('STEP 7: COMBINED RENDER (Flag + Watermark + 1080p FFV1)');
    
    const wmImagePath = path.join(__dirname, '..', 'core', 'assets', 'mrw-logo.png');
    const hasWatermark = fs.existsSync(wmImagePath);
    const srcDims = getVideoMetadata(downloadedPath);
    const isShortsSize = Math.abs(srcDims.width / srcDims.height - SHORTS_W / SHORTS_H) < 0.05
      && srcDims.width >= SHORTS_W && srcDims.height >= SHORTS_H;
    
    const flagDuration = Math.min(4, (srcDims.duration || 30) - 1);
    const flagWidth = 150;
    const flagHeight = 150;
    const flagX = Math.floor((SHORTS_W - flagWidth) / 2);
    const flagY = 20;
    
    // Build filter (matching Type 1 style)
    const filterParts = [];
    
    // Step 1: Scale to 9:16 if needed (pillarbox)
    let currentLabel = '0:v';
    if (!isShortsSize || srcDims.width !== SHORTS_W || srcDims.height !== SHORTS_H) {
      filterParts.push(`[0:v]scale=${SHORTS_W}:${SHORTS_H}:flags=lanczos:force_original_aspect_ratio=increase,pad=${SHORTS_W}:${SHORTS_H}:(ow-iw)/2:(oh-ih)/2:color=black[v1]`);
      currentLabel = 'v1';
    }
    
    // Step 2: Flag overlay (input 1)
    if (flagPath) {
      filterParts.push(`[1:v]scale=120:-1,format=rgba[flag]`);
      filterParts.push(`[${currentLabel}][flag]overlay=${flagX}:${flagY}:enable='between(t,0,${flagDuration})'[v2]`);
      currentLabel = 'v2';
    }
    
    // Step 3: Watermark matching Type 1 (logo size 80, bottom-right, 40% alpha)
    const wmIdx = flagPath ? 2 : 1;
    if (hasWatermark) {
      filterParts.push(`[${wmIdx}:v]scale=${LOGO_SIZE}:${LOGO_SIZE}:force_original_aspect_ratio=decrease,format=rgba,colorchannelmixer=aa=0.4[wm]`);
      filterParts.push(`[${currentLabel}][wm]overlay=W-w-${MARGIN_RIGHT}:H-h-${MARGIN_BOTTOM}:format=auto,drawtext=text='${TEXT}':fontcolor=white@0.40:fontsize=${FONT_SIZE}:x=W-tw-${MARGIN_RIGHT}:y=H-th-${Math.round(MARGIN_BOTTOM/2)}:shadowcolor=black@0.40:shadowx=1:shadowy=1[v3]`);
      currentLabel = 'v3';
    }
    
    // Step 4: Final output at 1080x1920 (no 1440p upscale — matching Type 1)
    filterParts.push(`[${currentLabel}]null[vout]`);
    
    const filterComplex = filterParts.join(';');
    
    // Build inputs
    let inputs = `-i "${downloadedPath}"`;
    if (flagPath) inputs += ` -i "${flagPath}"`;
    if (hasWatermark) inputs += ` -i "${wmImagePath}"`;
    
    const combinedOutput = path.join(tmpDir, `combined_${Date.now()}.mkv`);
    
    try {
      // Use Type 1's FFV1 rendering settings
      const cmd = `ffmpeg -y ${inputs} -filter_complex "${filterComplex}" -map "[vout]" -map 0:a -c:v ffv1 -level 3 -coder 1 -context 1 -g 1 -slices 16 -slicecrc 1 -pix_fmt yuv444p10le -c:a flac -ar 48000 -shortest -strict experimental "${combinedOutput}"`;
      execSync(cmd, { timeout: 180000 });
      if (fs.existsSync(combinedOutput) && fs.statSync(combinedOutput).size > 100000) {
        logger.success(`Combined render: ${(fs.statSync(combinedOutput).size / 1024 / 1024).toFixed(1)}MB`);
      } else {
        logger.warn('Combined render produced no output — using original');
        try { fs.copyFileSync(downloadedPath, combinedOutput); } catch {}
      }
    } catch (e) {
      logger.warn(`Combined render failed: ${(e.message || '').substring(0, 80)} — using original`);
      try { fs.copyFileSync(downloadedPath, combinedOutput); } catch {}
    }
    
    const finalOutputPath = combinedOutput;

    // ─── STEP 8: GENERATE METADATA (Type 1 style: transcript + original description + CTA) ──
    logger.header('STEP 8: GENERATE METADATA');
    // Build metadataContext matching Type 1's format
    const metadataContext = {
      sourceUrl: video.url || '',
      sourceTitle: video.title || '',
      originalDescription: originalDescription,
      viewCount: video.view_count || 0,
    };
    const metadata = await generateMetadata(finalCountry, dialogueTranscript, video.title, gemini, metadataContext);

    // ─── STEP 9: FINAL QA ─────────────────────────────────────────────
    logger.header('STEP 9: FINAL QA');

    const validation = await validateOutput(finalOutputPath);
    if (!validation.passed) {
      logger.warn(`QA issues: ${validation.issues.join(', ')}`);
    }

    // Gemini CLI visual review (if available)
    if (geminiCLI.isAvailable()) {
      const gqa = await geminiReview(finalOutputPath);
      logger.info(`Gemini QA: ${gqa.score}/10 — ${gqa.recommendation}`);
    }

    // Copy to output with clean name
    const finalPath = path.join(outputDir, `temp_${video.id}.mp4`);
    try {
      fs.copyFileSync(finalOutputPath, finalPath);
    } catch (e) {
      logger.warn(`Copy failed: ${e.message}`);
    }

    // Cleanup tmp for this attempt
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}

    // Description is fully generated by Gemini via generateMetadata() above
    // (generateMetadata already ensures a "Follow Asian Edits" CTA is present)
    const finalDescription = metadata.description || '';

    logger.header('PIPELINE COMPLETE');
    logger.success(`Video: ${finalPath}`);
    logger.success(`Source: @${channelInfo.handle} (${video.id})`);
    logger.success(`Country: ${finalCountry}`);
    logger.success(`Title: ${metadata.title}`);

    // Return success — memory is saved by the runner after upload
    return {
      success: true,
      videoPath: finalPath,
      title: metadata.title,
      description: finalDescription,
      tags: metadata.tags,
      country: finalCountry,
      sourceId: video.id,
      sourceChannel: channelInfo.handle,
      sourceUrl: video.url,
    };
  }

  // All retries exhausted — return failure
  try { fs.rmSync(tmpBaseDir, { recursive: true, force: true }); } catch {}
  logger.error(`All ${MAX_RETRIES} attempts failed. Last error: ${lastError}`);
  return { success: false, error: lastError || 'All retries exhausted' };
}

module.exports = { runTempExplainerPipeline };