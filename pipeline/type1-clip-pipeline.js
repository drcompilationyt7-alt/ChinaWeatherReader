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
  return { keywords: [country.toLowerCase()], suffix: '#shorts #tiktok #reels', songs: [] };
}

/**
 * Generate search queries using 3 methods
 * 1. Trend bank keywords
 * 2. LLM-generated queries
 * 3. LLM + trend bank hybrid
 */
async function generateQueries(country, gemini, trendBank) {
  const allQueries = [];

  // Method 1: Direct trend bank keywords
  const bankQueries = trendBank.keywords.slice(0, 3).map(kw => `${kw} ${trendBank.suffix}`);
  allQueries.push(...bankQueries);
  logger.info(`Method 1 (trend bank): ${bankQueries.length} queries`);

  // Method 2: LLM-generated
  const llmQueries = await gemini.generateQueries(country, [], 3);
  if (Array.isArray(llmQueries)) {
    for (const q of llmQueries) {
      const query = q.includes('#shorts') ? q : `${q} ${trendBank.suffix}`;
      if (!allQueries.includes(query)) allQueries.push(query);
    }
    logger.info(`Method 2 (LLM): ${llmQueries.length} queries`);
  }

  // Method 3: LLM + trend bank hybrid
  const hybridQueries = await gemini.generateQueries(country, trendBank.keywords, 3);
  if (Array.isArray(hybridQueries)) {
    for (const q of hybridQueries) {
      const query = q.includes('#shorts') ? q : `${q} ${trendBank.suffix}`;
      if (!allQueries.includes(query)) allQueries.push(query);
    }
    logger.info(`Method 3 (LLM+bank): ${hybridQueries.length} queries`);
  }

  logger.success(`Total queries generated: ${allQueries.length}`);
  return allQueries;
}

/**
 * Search YouTube for videos — 2-pass approach
 * Pass 1: Quick flat search to get video IDs
 * Pass 2: Fetch full metadata (likes, comments, views) for each candidate
 * Returns candidates enriched with engagement data
 */
async function searchYouTube(queries, videosPerQuery = 6) {
  const allResults = [];
  const seen = new Set();

  // ─── Pass 1: Quick flat search ─────────────────────────────────────
  for (const query of queries) {
    try {
      logger.info(`Searching: "${query}"`);
      const searchCount = Math.min(videosPerQuery + 4, 15);
      const cmd = `yt-dlp --flat-playlist --dump-json "ytsearch${searchCount}:${query}" 2>/dev/null`;
      const out = execSync(cmd, { timeout: 30000, maxBuffer: 5 * 1024 * 1024 }).toString().trim();

      if (!out) continue;

      const lines = out.split('\n').filter(Boolean);

      for (const line of lines) {
        try {
          const p = JSON.parse(line);
          if (p.is_live) continue;
          if (p.duration && p.duration > 120) continue;

          const watchUrl = `https://www.youtube.com/watch?v=${p.id}`;

          if (seen.has(p.id)) continue;
          seen.add(p.id);

          allResults.push({
            id: p.id,
            url: watchUrl,
            shortsUrl: `https://www.youtube.com/shorts/${p.id}`,
            title: p.title || 'YouTube video',
            duration: p.duration || 0,
            searchQuery: query,
            // These will be filled in Pass 2
            view_count: 0,
            channel_follower_count: 0,
            like_count: 0,
            comment_count: 0,
            channel: p.channel || p.uploader || 'Unknown',
            description: (p.description || '').substring(0, 300),
            upload_date: p.upload_date || '',
          });
        } catch {}
      }
    } catch (e) {
      logger.warn(`Search failed for "${query}": ${e.message.substring(0, 60)}`);
    }

    await new Promise(r => setTimeout(r, 300));
  }

  logger.info(`Pass 1 complete: ${allResults.length} raw candidates found`);

  if (allResults.length === 0) return [];

  // ─── Pass 2: Fetch full metadata for all candidates ────────────────
  // Limit to top 20 by title relevance to avoid too many API calls
  const toEnrich = allResults.slice(0, 20);
  let enriched = 0;
  let fetchErrors = 0;

  for (const candidate of toEnrich) {
    try {
      // Try with cookies if available
      const cookieArg = fs.existsSync('/tmp/yt_cookies.txt') ? '--cookies "/tmp/yt_cookies.txt"' : '';
      const metaCmd = `yt-dlp ${cookieArg} --dump-json --no-download "${candidate.url}" 2>&1`;
      const metaOut = execSync(metaCmd, { timeout: 15000, maxBuffer: 1024 * 1024 }).toString().trim();
      if (metaOut && !metaOut.includes('ERROR') && !metaOut.includes('WARNING')) {
        const meta = JSON.parse(metaOut.split('\n')[0]);
        candidate.view_count = meta.view_count || 0;
        candidate.channel_follower_count = meta.channel_follower_count || 0;
        candidate.like_count = meta.like_count || 0;
        candidate.comment_count = meta.comment_count || 0;
        candidate.channel = meta.channel || meta.uploader || candidate.channel;
        candidate.duration = meta.duration || candidate.duration;
        candidate.description = (meta.description || '').substring(0, 300);
        enriched++;
      } else {
        fetchErrors++;
        if (fetchErrors <= 3) {
          logger.warn(`Meta fetch error for "${candidate.title.substring(0, 40)}": ${metaOut?.substring(0, 100) || 'empty'}`);
        }
      }
    } catch (e) {
      fetchErrors++;
    }

    // Small delay between metadata fetches
    await new Promise(r => setTimeout(r, 200));
  }

  logger.success(`Pass 2 complete: ${enriched}/${toEnrich.length} enriched with metadata (${fetchErrors} errors)`);

  // If metadata fetch failed for ALL candidates, keep them anyway and log warning
  // This means engagement filter will pass them through based on level 3 (broad)
  if (enriched === 0 && toEnrich.length > 0) {
    logger.warn('Metadata fetch failed for all candidates — YouTube may require fresh cookies or auth');
    logger.warn('Candidates will be passed to filter without engagement data');
  }

  return toEnrich;
}

/**
 * Filter candidates by engagement metrics (likes + comments)
 * Progressively relaxes until we have at least 5 candidates for ranking.
 */
function filterCandidates(candidates) {
  // Check if we got engagement data or metadata failed
  const hasEngagementData = candidates.some(c => c.like_count > 0 || c.comment_count > 0);

  if (!hasEngagementData) {
    logger.warn('No engagement data available — skipping engagement filter, passing all to Gemini for ranking');
    logger.warn(`Passing ${candidates.length} candidates to Gemini un-filtered`);
    return candidates.filter(c => c.duration <= 120); // Only filter by duration
  }

  // Level 1: Strict (100+ likes, 30+ comments, not famous)
  let filtered = candidates.filter(c => {
    if (c.like_count < 100) return false;
    if (c.comment_count < 30) return false;
    if (c.channel_follower_count > 500000) return false;
    if (c.duration > 120) return false;
    return true;
  });

  if (filtered.length >= 5) {
    logger.info(`Level 1 (strict: 100 likes, 30 comments): ${filtered.length} candidates`);
    return filtered;
  }

  // Level 2: Relaxed (50+ likes, 15+ comments)
  filtered = candidates.filter(c => {
    if (c.like_count < 50) return false;
    if (c.comment_count < 15) return false;
    if (c.channel_follower_count > 1000000) return false;
    if (c.duration > 120) return false;
    return true;
  });

  if (filtered.length >= 5) {
    logger.info(`Level 2 (relaxed: 50 likes, 15 comments): ${filtered.length} candidates`);
    return filtered;
  }

  // Level 3: Broad (any likes, any comments, not a huge channel)
  filtered = candidates.filter(c => {
    if (c.like_count < 1 && c.comment_count < 1) return false;
    if (c.channel_follower_count > 5000000) return false;
    if (c.duration > 120) return false;
    return true;
  });

  logger.info(`Level 3 (broad): ${filtered.length} candidates`);
  return filtered;
}

/**
 * Rank videos using Gemini File API (downloads MP4, uploads, watches video)
 * This is the REAL "Gemini watches the video" approach.
 * URL-based ranking does NOT work for actual video analysis.
 */
async function rankVideos(candidates, country, gemini, curatorSkill, tmpDir) {
  const ranked = [];

  // Rank top 15 — some will fail from API limits
  const sorted = [...candidates].sort((a, b) => b.view_count - a.view_count).slice(0, 15);

  for (const candidate of sorted) {
    logger.info(`Ranking: "${candidate.title.substring(0, 50)}" (${(candidate.view_count / 1000000).toFixed(1)}M views)`);

    // Download the video first
    logger.info(`Downloading for visual analysis...`);
    const dlPath = await downloadBestVideo(candidate, tmpDir);
    if (!dlPath) {
      logger.warn('Download failed for this candidate — skipping');
      continue;
    }

    // Upload to Gemini File API — Gemini WATCHES the video
    logger.info(`Uploading to Gemini File API for actual video analysis...`);
    const result = await gemini.rankVideoFile(dlPath, country, curatorSkill);

    // Cleanup
    try { fs.unlinkSync(dlPath); } catch {}

    if (result && result.verdict === 'APPROVED' && result.score >= 6) {
      ranked.push({
        ...candidate,
        geminiScore: Math.min(10, Math.max(1, result.score)),
        hookScore: result.hook_score || 5,
        geminiCountry: result.country || country,
        watermarkType: result.watermark_type,
        reasoning: result.reasoning || '',
      });
      logger.success(`  ✅ File API Score: ${result.score}/10 — ${result.reasoning?.substring(0, 60)}`);
    } else if (result) {
      logger.info(`  ❌ Rejected (score: ${result.score || '?'}) — ${result.reasoning?.substring(0, 60) || ''}`);
    } else {
      logger.warn('  File API returned null');
    }

    // 40s delay between calls
    await new Promise(r => setTimeout(r, 40000));
  }

  ranked.sort((a, b) => b.geminiScore - a.geminiScore);
  logger.success(`File API ranked: ${ranked.length} approved videos`);

  // If File API ranking returned nothing, fallback to highest views
  if (ranked.length === 0) {
    logger.warn('No videos approved by File API — using highest-view as fallback');
    const shorts = sorted.filter(c => c.duration <= 60 && c.duration > 0);
    if (shorts.length > 0) {
      ranked.push({ ...shorts[0], geminiScore: 5, hookScore: 5, geminiCountry: country });
    }
  }
  return ranked;
}

/**
 * Download a video using yt-dlp
 */
async function downloadBestVideo(video, outputDir) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });

  const outputFile = path.join(outputDir, `source_${Date.now()}.mp4`);
  const url = video.shortsUrl || video.url;

  logger.info(`Downloading: ${url}`);

  const strategies = [
    { name: 'web', args: '--extractor-args "youtube:player_client=web"', format: '-f "bestvideo[height<=1080]+bestaudio/best" --merge-output-format mp4' },
    { name: 'default', args: '', format: '-f "bestvideo+bestaudio/best" --merge-output-format mp4' },
    { name: 'android', args: '--extractor-args "youtube:player_client=android"', format: '-f "best"' },
  ];

  for (const s of strategies) {
    try {
      const hasCookies = fs.existsSync('/tmp/yt_cookies.txt');
      const cookieArg = hasCookies ? '--cookies "/tmp/yt_cookies.txt"' : '';

      const cmd = `yt-dlp ${cookieArg} ${s.args} ${s.format} ` +
        `--download-sections "*0-60" -o "${outputFile}" "${url}" ` +
        `--no-playlist --max-filesize 150M --socket-timeout 30 --retries 3 --force-ipv4`;

      execSync(cmd, { timeout: 180000, maxBuffer: 200 * 1024 * 1024 });

      // Check for output file
      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000)
        .sort((a, b) => fs.statSync(path.join(outputDir, b)).mtimeMs - fs.statSync(path.join(outputDir, a)).mtimeMs);

      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        logger.success(`Downloaded: ${files[0]} (${(fs.statSync(fp).size / 1024 / 1024).toFixed(1)}MB)`);
        return fp;
      }
    } catch (e) {
      logger.warn(`Download strategy ${s.name} failed: ${e.message.substring(0, 60)}`);
    }
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

  // Generate TTS
  const ttsPath = path.join(tmpDir, `signature_${Date.now()}.mp3`);
  try {
    execSync(
      `edge-tts --voice "en-US-AvaMultilingualNeural" --text "Enjoy this clip from ${country}" --write-media "${ttsPath}" 2>/dev/null`,
      { timeout: 30000 }
    );
  } catch (e) {
    logger.warn(`TTS failed: ${e.message.substring(0, 60)}`);
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
      `ffprobe -i "${ttsPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`,
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
      const response = await require('axios')({ method: 'GET', url, responseType: 'stream', timeout: 10000 });
      const writer = fs.createWriteStream(flagFile);
      response.data.pipe(writer);
      await new Promise((resolve, reject) => { writer.on('finish', resolve); writer.on('error', reject); });
      if (fs.existsSync(flagFile) && fs.statSync(flagFile).size > 100) hasFlag = true;
    } catch {}
  }

  // Get video duration
  let videoDuration = 30;
  try {
    const durOut = execSync(
      `ffprobe -i "${videoPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`,
      { timeout: 5000, encoding: 'utf8' }
    ).trim();
    if (durOut) videoDuration = parseFloat(durOut);
  } catch {}

  // Mix: original audio ducked + signature TTS + optional flag
  try {
    const startDelay = Math.max(1, videoDuration - ttsDuration - 2); // Place near end

    if (hasFlag) {
      const filterComplex =
        `[0:v][1:v]overlay=(W-w)/2:160:enable='between(t,${startDelay},${startDelay + ttsDuration})'[v];` +
        `[0:a]volume=enable='between(t,${startDelay},${startDelay + ttsDuration})':volume=0.2[ad];` +
        `[1:a]adelay=${startDelay * 1000}[av];[ad][av]amix=inputs=2:duration=first[a]`;

      execSync(
        `ffmpeg -y -i "${videoPath}" -i "${ttsPath}" -i "${flagFile}" ` +
        `-filter_complex "${filterComplex}" -map "[v]" -map "[a]" ` +
        `-c:v libx264 -preset fast -crf 20 -c:a aac -shortest "${outputPath}" 2>/dev/null`,
        { timeout: 120000 }
      );
    } else {
      const filterComplex =
        `[0:a]volume=enable='between(t,${startDelay},${startDelay + ttsDuration})':volume=0.2[ad];` +
        `[1:a]adelay=${startDelay * 1000}[av];[ad][av]amix=inputs=2:duration=first[a]`;

      execSync(
        `ffmpeg -y -i "${videoPath}" -i "${ttsPath}" ` +
        `-filter_complex "${filterComplex}" -map 0:v -map "[a]" ` +
        `-c:v copy -c:a aac -shortest "${outputPath}" 2>/dev/null`,
        { timeout: 120000 }
      );
    }

    // Cleanup
    try { fs.unlinkSync(ttsPath); } catch {}
    try { if (hasFlag) fs.unlinkSync(flagFile); } catch {}

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Signature added: ${(fs.statSync(outputPath).size / 1024 / 1024).toFixed(1)}MB`);
      return true;
    }
  } catch (e) {
    logger.warn(`Signature overlay failed: ${e.message.substring(0, 100)}`);
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
  const country = options.country;
  const outputDir = options.outputDir || path.join(__dirname, '..', 'output', 'clips');
  const tmpDir = path.join(outputDir, `tmp_${Date.now()}`);

  if (!fs.existsSync(tmpDir)) fs.mkdirSync(tmpDir, { recursive: true });

  logger.header(`TYPE 1 PIPELINE: ${country}`);

  const gemini = getGeminiService();
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

  // ─── Phase 2: Search + Filter ──────────────────────────────────────
  logger.info('Phase 2: Search YouTube');
  const candidates = await searchYouTube(queries, 6);
  const filtered = filterCandidates(candidates);
  logger.info(`Candidates: ${candidates.length} → Filtered: ${filtered.length}`);

  if (filtered.length === 0) {
    // filterCandidates already tried 3 progressive levels.
    // If still 0, log what the candidates' engagement looks like and abort.
    if (candidates.length > 0) {
      logger.warn('Candidate engagement snapshot (top 5):');
      candidates.slice(0, 5).forEach(c => {
        logger.warn(`  "${c.title.substring(0, 40)}" — ${c.like_count} likes, ${c.comment_count} comments, ${c.channel_follower_count} subs`);
      });
    }
    logger.error('No candidates passed any filter level — aborting');
    return { success: false, error: 'No candidates' };
  }

  // ─── Phase 3: Gemini Ranking ───────────────────────────────────────
  logger.info('Phase 3: Gemini Ranking');
  const ranked = await rankVideos(filtered, country, gemini, curatorSkill, tmpDir);

  if (ranked.length === 0) {
    // Video File Upload Ranking (Gemini actually WATCHES the video)
    logger.warn('Gemini URL ranking failed — downloading videos for actual visual analysis');
    const top3 = filtered.slice(0, 3);
    const videoRanked = [];

    for (const cand of top3) {
      logger.info(`Downloading for video analysis: "${cand.title.substring(0, 50)}"`);
      const dlPath = await downloadBestVideo(cand, tmpDir);
      if (!dlPath) continue;

      // Upload video to Gemini File API and let it WATCH the video
      logger.info(`Uploading to Gemini File API for visual ranking...`);
      const result = await gemini.rankVideoFile(dlPath, country, curatorSkill);

      if (result && result.verdict === 'APPROVED' && result.score >= 5) {
        videoRanked.push({
          ...cand,
          geminiScore: Math.min(10, Math.max(1, result.score)),
          hookScore: result.hook_score || 5,
          geminiCountry: result.country || country,
          watermarkType: result.watermark_type,
          reasoning: result.reasoning || '',
        });
        logger.success(`  Video rank: ${result.score}/10 — ${result.reasoning?.substring(0, 60) || ''}`);
      } else if (result) {
        logger.info(`  Rejected by video analysis: ${result.score || '?'}/10 — ${result.reasoning?.substring(0, 60) || ''}`);
      } else {
        logger.warn('  Video analysis returned null');
      }

      // Cleanup downloaded file
      try { fs.unlinkSync(dlPath); } catch {}
    }

    if (videoRanked.length > 0) {
      videoRanked.sort((a, b) => b.geminiScore - a.geminiScore);
      ranked.push(videoRanked[0]);
      logger.success(`Video-based winner: "${ranked[0].title.substring(0, 50)}" (score: ${ranked[0].geminiScore}/10)`);
    } else {
      // Ultimate fallback: highest view count
      logger.warn('Video analysis failed for all — using highest-view as fallback');
      const shorts = filtered.filter(c => c.duration <= 60 && c.duration > 0);
      if (shorts.length > 0) {
        const fb = shorts.sort((a, b) => b.view_count - a.view_count)[0];
        ranked.push({ ...fb, geminiScore: 5, hookScore: 5, geminiCountry: country });
      } else {
        logger.error('No fallback candidates — aborting');
        return { success: false, error: 'No approved videos' };
      }
    }
  }

  const bestVideo = ranked[0];
  logger.success(`Best video: "${bestVideo.title.substring(0, 50)}" (score: ${bestVideo.geminiScore}/10)`);

  // ─── Phase 4: Download + Transcribe ────────────────────────────────
  logger.info('Phase 4: Download + Transcribe');
  const downloadedPath = await downloadBestVideo(bestVideo, tmpDir);

  if (!downloadedPath) {
    logger.error('Download failed — aborting');
    return { success: false, error: 'Download failed' };
  }

  const dialogue = await transcribeAudio(downloadedPath, tmpDir);

  // Check for profanity
  if (gemini.hasProfanity(dialogue.transcript)) {
    logger.error('Profanity detected — aborting');
    return { success: false, error: 'Profanity detected' };
  }

  // ─── Phase 5: Smart Crop ───────────────────────────────────────────
  logger.info('Phase 5: Smart Crop');
  const croppedPath = path.join(tmpDir, `cropped_${Date.now()}.mp4`);
  const cropResult = await smartCrop(downloadedPath, croppedPath, {
    country,
    duration: Math.min(bestVideo.duration || 30, 60),
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
    duration: Math.min(bestVideo.duration || 30, 60),
  });

  if (!editResult.success) {
    logger.error('Edit failed — aborting');
    return { success: false, error: 'Edit failed' };
  }

  // ─── Phase 7: Signature ────────────────────────────────────────────
  logger.info('Phase 7: Add Signature');
  const finalPath = path.join(outputDir, `short_${Date.now()}.mp4`);
  const sigResult = await addSignature(editedPath, finalPath, country, tmpDir);

  if (!sigResult) {
    logger.error('Signature failed — aborting');
    return { success: false, error: 'Signature failed' };
  }

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

  // Gemini visual review (via REST API frames)
  const qaFrames = extractFrames(finalPath, path.join(tmpDir, 'qa_final'), [3, Math.floor((bestVideo.duration || 30) / 2), Math.max(5, (bestVideo.duration || 30) - 3)]);
  if (qaFrames.length > 0) {
    const geminiQA = await geminiReview(qaFrames);
    logger.info(`Gemini QA: ${geminiQA.score}/10 — ${geminiQA.recommendation}`);

    if (geminiQA.recommendation === 'RENDER_AGAIN' && geminiQA.score < 5) {
      logger.warn('Gemini QA recommends re-render, but proceeding with current output');
    }
  }

  // ─── OpenRouter nano final review (non-directional, "are you sure?") ──
  try {
    const orQA = getOpenRouterQA();
    const orDir = path.join(tmpDir, `qa_final_or`);
    const orFrames = extractFrames(finalPath, orDir, [3, Math.floor((bestVideo.duration || 30) / 2), Math.max(5, (bestVideo.duration || 30) - 3)]);
    if (orFrames.length > 0) {
      const orResult = await orQA.finalReview(orFrames, country);
      if (orResult) {
        if (orResult.ready === false) {
          logger.warn(`OpenRouter final review: NOT READY — ${(orResult.issues || []).join('; ')}`);
        } else {
          logger.success(`OpenRouter final review: ready to upload (score: ${orResult.score || '?'}/10)`);
        }
        if (orResult.notes) {
          logger.info(`OpenRouter note: ${orResult.notes.substring(0, 150)}`);
        }
      }
    }
    try { fs.rmSync(orDir, { recursive: true, force: true }); } catch {}
  } catch (orError) {
    // Non-blocking
    logger.warn(`OpenRouter final review error: ${orError.message.substring(0, 60)}`);
  }

  // ─── Phase 9: Generate Metadata ────────────────────────────────────
  logger.info('Phase 9: Generate Metadata');
  const metadata = await gemini.generateTitle(country, dialogue.transcript, bestVideo.title);
  const title = metadata?.title || `${country} Clip 🔥`;
  const description = metadata?.description || `Amazing viral clip from ${country}! Follow Mr. WorldWideWebster for more global trends! 🌍`;
  const tags = metadata?.tags || ['mr worldwidewebster', 'shorts', country.toLowerCase(), 'viral', 'tiktok'];

  // Cleanup tmp
  try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}

  logger.header('PIPELINE COMPLETE');
  logger.success(`Video: ${finalPath}`);
  logger.success(`Title: ${title}`);
  logger.success(`Country: ${country}`);
  logger.success(`Gemini Score: ${bestVideo.geminiScore}/10`);
  logger.success(`Edit Type: ${editResult.editType}`);
  logger.success(`Captions: ${editResult.hasCaptions}`);

  return {
    success: true,
    videoPath: finalPath,
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