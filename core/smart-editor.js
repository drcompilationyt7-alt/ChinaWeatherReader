/**
 * Smart Editor — TikTok-Style Editing with Gemini CLI Feedback Loop
 * 
 * Determines what edits are needed based on content type:
 * - Talking video → TikTok-style captions
 * - Non-English talking → Captions with translation
 * - Dance/music only → No captions, just visual edit
 * 
 * Uses Gemini CLI to:
 * 1. Analyze frames for watermark detection
 * 2. Decide caption strategy
 * 3. Generate ffmpeg edit commands
 * 4. QA review the output
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const config = require('./config');
const { Logger } = require('./logger');
const { getGeminiCLI } = require('./gemini-cli-runner');
const { getGeminiService } = require('./gemini-service');
const { getOpenRouterQA } = require('./openrouter-qa');
const { extractFrames } = require('./smart-cropper');

const logger = new Logger('SmartEditor');

/**
 * Detect if video has spoken dialogue using whisper.cpp
 * @returns {Object} - { hasDialogue, wordCount, language, transcript }
 */
async function detectDialogue(videoPath) {
  const tmpDir = path.dirname(videoPath);
  const audioPath = path.join(tmpDir, `dialogue_${Date.now()}.mp3`);

  try {
    // Extract audio
    execSync(
      `ffmpeg -y -i "${videoPath}" -t 30 -vn -acodec libmp3lame -ab 64k "${audioPath}" 2>/dev/null`,
      { timeout: 30000 }
    );

    if (!fs.existsSync(audioPath) || fs.statSync(audioPath).size < 1000) {
      return { hasDialogue: false, wordCount: 0, language: 'unknown', transcript: '' };
    }

    // Use standalone whisper transcribe script (more reliable than inline Python)
    const whisperScript = path.join(__dirname, 'whisper-transcribe.py');
    let output = null;
    let retries = 2;
    while (retries > 0) {
      try {
        output = execSync(
          `python3 "${whisperScript}" "${audioPath}" 2>&1`,
          { timeout: 120000, encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 }
        ).toString().trim();
        break;
      } catch (e) {
        retries--;
        logger.warn(`Whisper attempt failed (${retries} retries left): ${(e.message || '').substring(0, 60)}`);
        if (retries > 0) {
          // Small delay before retry
          execSync(`sleep 2`, { timeout: 5000, stdio: 'ignore' });
        }
      }
    }

    try {
      fs.unlinkSync(audioPath);
    } catch {}

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
    logger.warn(`Dialogue detection failed: ${e.message.substring(0, 60)}`);
    try { fs.unlinkSync(audioPath); } catch {}
  }

  return { hasDialogue: false, wordCount: 0, language: 'unknown', transcript: '' };
}

/**
 * Generate ASS subtitle content for TikTok-style captions
 * Splits text into 1-2 word blocks with timing from whisper word timestamps
 */
function generateTikTokCaptions(transcript, words, totalDuration) {
  if (!transcript || transcript.length < 3) return null;

  // If we have word-level timestamps from whisper, use them
  if (words && words.length > 0) {
    return generateTimedCaptions(words, totalDuration);
  }

  // Fallback: split transcript into blocks and distribute evenly
  const text = transcript.replace(/[^\w\s]/g, '').trim();
  const wordsList = text.split(/\s+/).filter(w => w.length > 0);
  if (wordsList.length === 0) return null;

  const blocks = [];
  let i = 0;
  while (i < wordsList.length) {
    if (i + 1 < wordsList.length && wordsList[i].length + wordsList[i + 1].length < 12) {
      blocks.push(wordsList[i] + ' ' + wordsList[i + 1]);
      i += 2;
    } else {
      blocks.push(wordsList[i]);
      i += 1;
    }
  }

  const timePerBlock = totalDuration / blocks.length;
  let assContent = buildAssHeader();
  let blockStart = 0;

  for (const block of blocks) {
    const blockEnd = Math.min(totalDuration, blockStart + timePerBlock);
    assContent += `Dialogue: 0,${formatAssTime(blockStart)},${formatAssTime(blockEnd)},TikTok,${block.replace(/"/g, '\\"')}\n`;
    blockStart = blockEnd;
  }

  return assContent;
}

/**
 * Generate TikTok-style ASS captions for translated text (non-English → English)
 * Same TikTok styling as original speech captions.
 */
function generateTranslatedTikTokCaptions(translatedText, totalDuration) {
  if (!translatedText || translatedText.length < 3) return null;

  const words = translatedText.split(/\s+/).filter(w => w.length > 0);
  const blocks = [];
  let i = 0;
  while (i < words.length) {
    if (i + 1 < words.length && words[i].length + words[i + 1].length < 14) {
      blocks.push(words[i] + ' ' + words[i + 1]);
      i += 2;
    } else {
      blocks.push(words[i]);
      i += 1;
    }
  }

  const timePerBlock = totalDuration / blocks.length;
  let assContent = buildAssHeader();
  let blockStart = 0;

  for (const block of blocks) {
    const blockEnd = Math.min(totalDuration, blockStart + timePerBlock);
    assContent += `Dialogue: 0,${formatAssTime(blockStart)},${formatAssTime(blockEnd)},TikTok,${block.replace(/"/g, '\\"')}\n`;
    blockStart = blockEnd;
  }

  return assContent;
}

/**
 * Generate timed captions from whisper word timestamps
 */
function generateTimedCaptions(words, totalDuration) {
  const blocks = [];
  let i = 0;

  while (i < words.length) {
    // Group 1-2 words per block
    if (i + 1 < words.length && words[i].word.length + words[i + 1].word.length < 12) {
      blocks.push({
        text: words[i].word + ' ' + words[i + 1].word,
        start: words[i].start,
        end: words[i + 1].end,
      });
      i += 2;
    } else {
      blocks.push({
        text: words[i].word,
        start: words[i].start,
        end: words[i].end,
      });
      i += 1;
    }
  }

  let assContent = buildAssHeader();

  for (const block of blocks) {
    assContent += `Dialogue: 0,${formatAssTime(block.start)},${formatAssTime(block.end)},TikTok,${block.text.replace(/"/g, '\\"')}\n`;
  }

  return assContent;
}

/**
 * Build ASS subtitle header with TikTok-style formatting
 */
function buildAssHeader() {
  return `[Script Info]
ScriptType: v4.00+
Collisions: Normal
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BorderStyle, Outline, Shadow, Alignment, MarginV, MarginL, MarginR
Style: TikTok,Arial Black,52,&H0000FFFF,&H00000000,1,3,0,2,350,30,30

[Events]
Format: Layer, Start, End, Style, Text
`;
}

/**
 * Format seconds to ASS timestamp (H:MM:SS.CC)
 */
function formatAssTime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const cs = Math.floor((s - Math.floor(s)) * 100);
  return `${h}:${String(m).padStart(2, '0')}:${String(Math.floor(s)).padStart(2, '0')}.${String(cs).padStart(2, '0')}`;
}

/**
 * Main editing function with Gemini CLI feedback loop
 * 
 * @param {string} videoPath - Cropped 9:16 video (input)
 * @param {string} outputPath - Final edited video (output)
 * @param {Object} options - { country, dialogue, translatedText, transcript }
 * @returns {Object} - { success, outputPath, hasCaptions, editType }
 */
async function smartEdit(videoPath, outputPath, options = {}) {
  const country = options.country || 'Global';
  const dialogue = options.dialogue || { hasDialogue: false, wordCount: 0, language: 'en', transcript: '' };
  const translatedText = options.translatedText || null;
  const duration = options.duration || 30;
  const tmpDir = path.dirname(outputPath);

  logger.info(`Smart editing: ${path.basename(videoPath)} (country: ${country})`);

  // Step 1: Determine edit type based on dialogue
  let editType = 'none'; // dance/music only
  let needsCaptions = false;
  let needsTranslation = false;

  if (dialogue.hasDialogue && dialogue.wordCount > 5) {
    // Check if transcript is actually music lyrics vs speech
    // Music lyrics tend to have short words, repetition, low word density
    const transcript = (dialogue.transcript || '').toLowerCase();
    const words = transcript.split(/\s+/).filter(w => w.length > 0);
    const wordDensity = duration > 0 ? words.length / duration : 0;
    
    // Music lyrics detection heuristics:
    // 1. Low word density (< 1 word per 2 seconds = sparse lyrics)
    // 2. High repetition of same short phrases
    // 3. Very short total transcript (< 10 words for 30s video)
    const isMusic = wordDensity < 1.0 || words.length < 8;
    
    if (isMusic) {
      editType = 'none';
      needsCaptions = false;
      needsTranslation = false;
      logger.info(`Edit type: NONE (music/lyrics detected — ${words.length} words, ${wordDensity.toFixed(2)} words/sec)`);
    } else {
      const isNonEnglish = dialogue.language !== 'en' && dialogue.language !== 'english';
      if (isNonEnglish) {
        editType = 'translation';
        needsCaptions = true;
        needsTranslation = true;
        logger.info(`Edit type: TRANSLATION (${dialogue.language} → English captions)`);
      } else {
        editType = 'tiktok_captions';
        needsCaptions = true;
        logger.info(`Edit type: TIKTOK CAPTIONS (English speech detected)`);
      }
    }
  } else {
    logger.info('Edit type: NONE (dance/music only — no captions)');
  }

  // Captions/subtitles are disabled in config → no subtitle generation or burn-in
  const captionsEnabled = config.captions && config.captions.enabled === true;
  if (!captionsEnabled) {
    if (needsCaptions) {
      logger.info('Subtitles/captions DISABLED by config — visual edit only, skipping subtitle generation');
    }
    needsCaptions = false;
    needsTranslation = false;
    if (editType === 'tiktok_captions' || editType === 'translation') editType = 'visual_edit';
  }

  // Step 2: Detect watermarks via Gemini CLI
  const geminiCLI = getGeminiCLI();
  const frameDir = path.join(tmpDir, `edit_frames_${Date.now()}`);
  const framePositions = [2, Math.floor(duration / 2), Math.max(3, duration - 3)];
  const frames = extractFrames(videoPath, frameDir, framePositions);

  let watermarkInfo = { has_watermarks: false };
  let editPlan = {};

  if (frames.length > 0 && geminiCLI.isAvailable()) {
    const editAnalysis = await geminiCLI.evaluateEdit(frames, 
      `Transcript: "${(dialogue.transcript || '').substring(0, 200)}"\n` +
      `Language: ${dialogue.language}\n` +
      `Has dialogue: ${dialogue.hasDialogue}\n` +
      `Country: ${country}`
    );

    if (editAnalysis) {
      try {
        const jsonMatch = editAnalysis.match(/\{[\s\S]*\}/);
        if (jsonMatch) {
          editPlan = JSON.parse(jsonMatch[0]);
          watermarkInfo = {
            has_watermarks: editPlan.has_watermarks || false,
            positions: editPlan.watermark_positions || [],
          };
          logger.info(`Watermarks detected: ${watermarkInfo.has_watermarks}`);
          logger.info(`Hook text: ${editPlan.suggested_hook_text || 'none'}`);
        }
      } catch {}
    }
  }

  // Step 3: Build the ffmpeg command
  const videoFilters = [];
  const audioFilters = [];

  // 3a: Visual transform (zoom to remove watermarks or add uniqueness)
  const zoomPercent = watermarkInfo.has_watermarks ? 107 : 105;
  const zoom = zoomPercent / 100;
  const zw = Math.floor(1080 * zoom / 2) * 2;
  const zh = Math.floor(1920 * zoom / 2) * 2;
  videoFilters.push(`scale=${zw}:${zh}:flags=lanczos,crop=1080:1920:(iw-1080)/2:(ih-1920)/2`);

  // 3b: Color adjustment
  videoFilters.push('eq=contrast=1.05:saturation=1.1');

  // 3c: Burn in captions if needed
  if (needsCaptions) {
    let subPath = null;

    if (needsTranslation && translatedText) {
      // Use TikTok-style ASS for translated captions (JS-based, evenly timed)
      const subContent = generateTranslatedTikTokCaptions(translatedText, duration);
      if (subContent) {
        subPath = path.join(tmpDir, `captions_${Date.now()}.ass`);
        fs.writeFileSync(subPath, subContent, 'utf8');
      }
    } else if (editType === 'tiktok_captions' && dialogue.transcript) {
      // Use Python script for word-perfect TikTok captions via faster-whisper
      subPath = path.join(tmpDir, `captions_${Date.now()}.ass`);
      try {
        const captionOut = execSync(
          `python3 "${path.join(__dirname, 'tiktok_captions.py')}" "${videoPath}" "${subPath}" 2>&1`,
          { timeout: 120000, encoding: 'utf8' }
        ).toString().trim();
        const captionResult = JSON.parse(captionOut);
        logger.info(`TikTok captions: ${captionResult.word_count} words, ${captionResult.ass_file}`);
      } catch (e) {
        logger.warn(`TikTok caption generation failed (${(e.message || '').substring(0, 60)}) — falling back to JS captions`);
        const subContent = generateTikTokCaptions(dialogue.transcript, dialogue.words, duration);
        if (subContent) {
          subPath = path.join(tmpDir, `captions_${Date.now()}.ass`);
          fs.writeFileSync(subPath, subContent, 'utf8');
        } else {
          subPath = null;
        }
      }
    }

    if (subPath && fs.existsSync(subPath)) {
      const escapedPath = subPath.replace(/\\/g, '/').replace(/'/g, "'\\\\''");
      videoFilters.push(`ass='${escapedPath}'`);
      logger.info(`Subtitles: ${subPath.split(/[\\/]/).pop()}`);
    }
  }

  // 3d: Audio boost
  audioFilters.push('volume=1.5');

  // Step 4: Build and execute ffmpeg command
  const vf = videoFilters.join(',');
  const af = audioFilters.join(',');

  const cmd = `ffmpeg -y -i "${videoPath}" ` +
    `-vf "${vf}" -af "${af}" ` +
    `-c:v libx264 -preset medium -crf 0 -c:a aac -b:a 320k ` +
    `-pix_fmt yuv444p -shortest "${outputPath}" 2>/dev/null`;

  logger.info(`Executing edit command...`);

  try {
  execSync(cmd, { timeout: 300000, maxBuffer: 500 * 1024 * 1024 });

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Edited: ${(fs.statSync(outputPath).size / 1024 / 1024).toFixed(1)}MB`);

      // Step 5: QA feedback loop using Gemini CLI with original vs edited MP4 (max 3 iterations)
      let currentOutput = outputPath;
      let finalHookText = editPlan.suggested_hook_text || null;
      const MAX_EDIT_QA_ITERATIONS = 3;

      for (let qaIter = 1; qaIter <= MAX_EDIT_QA_ITERATIONS; qaIter++) {
        logger.info(`--- Edit QA iteration ${qaIter}/${MAX_EDIT_QA_ITERATIONS} ---`);

        if (!geminiCLI.isAvailable()) {
          logger.info('Gemini CLI not available — skipping QA');
          break;
        }

        // If not first iteration, re-path the output
        const qaOutputPath = qaIter > 1
          ? path.join(tmpDir, `edited_qa_${Date.now()}_${qaIter}.mp4`)
          : currentOutput;

        // Send original (uncropped, unedited videoPath) + current edited to CLI for comparison
        const qaResult = await geminiCLI.compareAndReviewQA(videoPath, qaOutputPath, 'edit', country);

        if (!qaResult) {
          logger.warn('  Edit QA returned null — proceeding with current version');
          break;
        }

        logger.info(`  Edit QA verdict: ${qaResult.verdict} (score: ${qaResult.score}/10)`);

        if (qaResult.verdict === 'APPROVE') {
          logger.success(`  ✅ Edit approved at iteration ${qaIter}`);
          currentOutput = qaOutputPath;
          break;
        }

        if (qaResult.verdict === 'IMPROVE' && qaIter < MAX_EDIT_QA_ITERATIONS && qaResult.improvement_suggestions) {
          logger.warn(`  Improvement needed: ${qaResult.improvement_suggestions.substring(0, 200)}`);

          // Apply improvements by adjusting the ffmpeg command based on suggestions
          let adjZoom = zoomPercent;
          let adjContrast = 1.05;
          let adjSaturation = 1.1;
          let adjVolume = 1.5;

          // Parse suggestions for adjustments (if any)
          const sug = (qaResult.improvement_suggestions || '').toLowerCase();
          if (sug.includes('zoom') || sug.includes('crop')) adjZoom = 108; // Slightly more zoom
          if (sug.includes('bright') || sug.includes('contrast')) adjContrast = 1.1;
          if (sug.includes('saturat')) adjSaturation = 1.15;
          if (sug.includes('volume') || sug.includes('loud')) adjVolume = 2.0;

          const adjZw = Math.floor(1080 * (adjZoom / 100) / 2) * 2;
          const adjZh = Math.floor(1920 * (adjZoom / 100) / 2) * 2;

          const adjVf = [
            `scale=${adjZw}:${adjZh}:flags=lanczos,crop=1080:1920:(iw-1080)/2:(ih-1920)/2`,
            `eq=contrast=${adjContrast.toFixed(2)}:saturation=${adjSaturation.toFixed(2)}`
          ];
          // Use needsCaptions instead of undefined subContent
          if (needsCaptions && subPath && fs.existsSync(subPath)) {
            const escPath = subPath.replace(/\\/g, '/').replace(/'/g, "'\\\\''");
            adjVf.push(subPath.endsWith('.ass') ? `ass='${escPath}'` : `subtitles='${escPath}'`);
          }
          const adjAf = adjVolume !== 1.5 ? `volume=${adjVolume.toFixed(1)}` : 'volume=1.5';

          const adjCmd = `ffmpeg -y -i "${videoPath}" ` +
            `-vf "${adjVf.join(',')}" -af "${adjAf}" ` +
            `-c:v libx264 -preset medium -crf 0 -c:a aac -b:a 320k ` +
            `-pix_fmt yuv444p -shortest "${qaOutputPath}" 2>/dev/null`;

          logger.info(`  Re-rendering with adjustments...`);
          try {
            execSync(adjCmd, { timeout: 180000, maxBuffer: 50 * 1024 * 1024 });
            if (fs.existsSync(qaOutputPath) && fs.statSync(qaOutputPath).size > 100000) {
              logger.success(`  Re-rendered: ${(fs.statSync(qaOutputPath).size / 1024 / 1024).toFixed(1)}MB`);
              currentOutput = qaOutputPath;
              continue; // Re-QA in next iteration
            }
          } catch (reRenderError) {
            logger.warn(`  Re-render failed: ${reRenderError.message.substring(0, 60)}`);
            break;
          }
        }

        // REJECT or can't improve — use current version
        if (qaResult.verdict === 'REJECT') {
          logger.warn(`  Edit REJECTED — using current version anyway`);
        }
        break; // Only continue if IMPROVE + re-render + not last iteration
      }

      // Cleanup temp files
      try { fs.rmSync(frameDir, { recursive: true, force: true }); } catch {}
      if (currentOutput !== outputPath) {
        try { fs.copyFileSync(currentOutput, outputPath); } catch {}
      }

      return {
        success: true,
        outputPath,
        hasCaptions: needsCaptions,
        editType,
        hookText: finalHookText,
      };
    }
  } catch (e) {
    logger.warn(`Edit failed: ${e.message.substring(0, 100)}`);
  }

  // Fallback: copy original
  try {
    fs.copyFileSync(videoPath, outputPath);
    return { success: true, outputPath, hasCaptions: false, editType: 'fallback_copy' };
  } catch {}

  return { success: false, outputPath: null };
}

module.exports = { smartEdit, detectDialogue, generateTikTokCaptions, generateTranslatedTikTokCaptions };
