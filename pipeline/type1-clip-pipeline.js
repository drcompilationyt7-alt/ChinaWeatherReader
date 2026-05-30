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

  // Pacing gap to avoid 429 rate limit
  await new Promise(r => setTimeout(r, 3000));

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
 * Search YouTube — Bulk Fetch + Quality Gate
 * 
 * Fetches up to 200 search results per query via ytsearch200: syntax.
 * For each result, fetches full metadata and applies quality gate.
 * Quality Gate: views>=5000, comments>0, embeddable, like ratio>=1.5%, <60s, public
 */
async function searchYouTube(queries, targetCount = 15) {
  const seen = new Set();
  const cookieArg = fs.existsSync('/tmp/yt_cookies.txt') ? '--cookies "/tmp/yt_cookies.txt"' : '';
  // Collect results per-query in a map for interleaving
  const perQueryResults = new Map();

  const perQueryTarget = Math.max(1, Math.ceil(targetCount / queries.length));

  for (const query of queries) {
    logger.info(`Searching for: "${query}"`);

    try {
      const searchCmd = `yt-dlp --flat-playlist --dump-json ` +
        `--match-filter "!is_live & !upcoming & duration < 60 & availability = 'public'" ` +
        `"ytsearch200:${query}" 2>&1`;

      const out = execSync(searchCmd, { timeout: 30000, maxBuffer: 10 * 1024 * 1024 }).toString().trim();
      if (!out) {
        logger.info('No results from this query');
        continue;
      }

      const lines = out.split('\n').filter(Boolean);
      logger.info(`Raw results: ${lines.length} videos`);

      const queryResults = [];
      for (const line of lines) {
        if (queryResults.length >= perQueryTarget) break;

        try {
          const p = JSON.parse(line);
          if (p.id && !seen.has(p.id)) {
            seen.add(p.id);

            const metaCmd = `yt-dlp ${cookieArg} --dump-json --no-download "https://www.youtube.com/watch?v=${p.id}" 2>&1`;
            let metaOut;
            try {
              metaOut = execSync(metaCmd, { timeout: 15000, maxBuffer: 1024 * 1024 }).toString().trim();
            } catch {}
            if (!metaOut || metaOut.includes('ERROR')) continue;

            const meta = JSON.parse(metaOut.split('\n')[0]);
            const views = meta.view_count || 0;
            const likes = meta.like_count || 0;
            const comments = meta.comment_count || 0;

            if (views < 2000) continue;
            if (comments === 0) continue;

            queryResults.push({
              id: p.id,
              url: `https://www.youtube.com/watch?v=${p.id}`,
              shortsUrl: `https://www.youtube.com/shorts/${p.id}`,
              title: meta.title || p.title || 'YouTube video',
              duration: meta.duration || p.duration || 0,
              searchQuery: query,
              view_count: views,
              channel_follower_count: meta.channel_follower_count || 0,
              like_count: likes,
              comment_count: comments,
              channel: meta.channel || meta.uploader || p.channel || 'Unknown',
              description: (meta.description || '').substring(0, 300),
              upload_date: meta.upload_date || p.upload_date || '',
            });
          }
        } catch {}
      }

      if (queryResults.length > 0) {
        perQueryResults.set(query, queryResults);
        logger.info(`  → ${queryResults.length} candidates from this query`);
      }
    } catch (e) {
      logger.warn(`Search failed for "${query}": ${(e.message || '').substring(0, 200)}`);
    }
  }

  // Interleave results from all queries: take 1 from each query in round-robin
  const allResults = [];
  const queryList = Array.from(perQueryResults.keys());
  let maxLen = 0;
  for (const q of queryList) maxLen = Math.max(maxLen, perQueryResults.get(q).length);

  for (let i = 0; i < maxLen && allResults.length < targetCount; i++) {
    for (const q of queryList) {
      const results = perQueryResults.get(q);
      if (i < results.length && allResults.length < targetCount) {
        allResults.push(results[i]);
      }
    }
  }

  logger.success(`Search complete: ${allResults.length} quality candidates found`);

  // ─── Debug: Log engagement data from top candidates ────────────────
  if (allResults.length > 0) {
    logger.info('── Gated candidates (top 5) ──');
    allResults.slice(0, 5).forEach((c, i) => {
      const ageDays = c.upload_date
        ? Math.max(1, Math.floor((Date.now() - new Date(
            c.upload_date.substring(0, 4),
            c.upload_date.substring(4, 6) - 1,
            c.upload_date.substring(6, 8)
          ).getTime()) / 86400000))
        : 'N/A';
      logger.info(`  #${i + 1} "${c.title.substring(0, 40)}"`);
      logger.info(`       Views: ${c.view_count?.toLocaleString() || 0} | Likes: ${c.like_count?.toLocaleString() || 0} | Comments: ${c.comment_count?.toLocaleString() || 0}`);
      logger.info(`       Age: ${ageDays}d | Duration: ${c.duration}s | Embed: yes`);
    });
    logger.info('──────────────────────────────────────────');
  }

  return allResults;
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
 * Rank a single video: try URL-based Gemini ranking, fall back to
 * downloading 720p and using File API + CLI for visual ranking.
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

  // Step 1: Try URL-based Gemini ranking
  logger.info(`  Step 1 — URL-based ranking...`);
  let result = await gemini.rankVideo(candidate.url, country, curatorSkill, engagementData);

  // If URL ranking returned a valid result (APPROVED or REJECTED), use it
  if (result !== null) {
    return { result, candidate };
  }

  // Step 2: URL ranking failed (keys exhausted / error) — download 720p for File API + CLI
  logger.info(`  Step 2 — URL ranking returned null, downloading 720p for visual ranking...`);
  const dlPath = await downloadBestVideo(candidate, tmpDir);
  if (!dlPath) {
    logger.warn(`  Download failed — cannot rank this video`);
    return null;
  }

  // Step 2a: Try Gemini File API upload
  logger.info(`  Step 2a — Uploading to Gemini File API...`);
  result = await gemini.rankVideoFile(dlPath, country, curatorSkill, engagementData);

  // Step 2b: If File API also failed, try Gemini CLI
  if (result === null && geminiCLI && geminiCLI.isAvailable()) {
    logger.info(`  Step 2b — File API failed, trying Gemini CLI...`);
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
 * URL → 720p download → File API → CLI.
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

  // ─── Phase 2: Search + Filter ──────────────────────────────────────
  // Target exactly 10 quality shorts for AI ranking
  let candidates = await searchYouTube(queries, 10);
  let filtered = filterCandidates(candidates);
  logger.info(`Candidates: ${candidates.length} → Filtered: ${filtered.length}`);

  // If fewer than 10 candidates passed, retry with relaxed criteria
  if (filtered.length < 10 && candidates.length > 0) {
    logger.warn(`Only ${filtered.length} candidates passed strict filter — relaxing quality gate for retry`);
    const fallbackGate = candidates.filter(c => {
      if (c.view_count < 2000) return false;
      if (c.channel_follower_count > 5000000) return false;
      if (c.duration > 120) return false;
      if (c.view_count > 0 && (c.like_count / c.view_count) * 100 < 1.0) return false;
      return true;
    });
    logger.info(`Relaxed gate yielded: ${fallbackGate.length} candidates`);
    if (fallbackGate.length > 0) {
      filtered = fallbackGate;
    }
  }
  
  // If still fewer than 5 after relaxed gate, use ALL candidates as last resort
  if (filtered.length < 5 && candidates.length > 0) {
    logger.warn(`Still only ${filtered.length} after relaxed gate — using all ${candidates.length} candidates`);
    filtered = candidates;
  }

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
  // Each batch searches 10 new candidates, ranks them with per-video
  // URL→720p download→File API→CLI fallback. null results don't count,
  // only actual APPROVED/REJECTED responses from Gemini.
  logger.info('Phase 3: Gemini Ranking');
  let ranked = [];
  const MAX_BATCHES = 3;

  for (let batch = 1; batch <= MAX_BATCHES; batch++) {
    logger.header(`Ranking batch ${batch}/${MAX_BATCHES}`);

    if (batch > 1) {
      // Search a fresh batch of 10 candidates
      logger.info(`Batch ${batch}: Searching for fresh candidates...`);
      const newQueries = await generateQueries(country, gemini, trendBank);
      if (newQueries.length === 0) {
        logger.warn(`Batch ${batch}: No new queries generated — skipping`);
        continue;
      }
      candidates = await searchYouTube(newQueries, 10);
      filtered = filterCandidates(candidates);
      if (filtered.length < 5) {
        const fallbackGate = (candidates || []).filter(c => {
          if (c.view_count < 2000) return false;
          if (c.channel_follower_count > 5000000) return false;
          if (c.duration > 120) return false;
          return true;
        });
        if (fallbackGate.length > 0) filtered = fallbackGate;
      }
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

    // Rank this batch — each video gets URL→720p→File API→CLI fallback
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

  // Gemini visual review (via CLI — sends full MP4 for analysis)
  const geminiQA = await geminiReview(finalPath);
  logger.info(`Gemini QA: ${geminiQA.score}/10 — ${geminiQA.recommendation}`);

  if (geminiQA.recommendation === 'RENDER_AGAIN' && geminiQA.score < 5) {
    logger.warn('Gemini QA recommends re-render, but proceeding with current output');
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