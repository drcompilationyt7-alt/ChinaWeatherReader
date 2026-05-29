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

    // Run whisper.cpp
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
 * Generate SRT subtitle content for translated text
 */
function generateTranslatedSRT(transcript, translatedText, totalDuration) {
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
  let srt = '';
  let blockStart = 0;
  let index = 1;

  for (const block of blocks) {
    const blockEnd = Math.min(totalDuration, blockStart + timePerBlock);
    srt += `${index}\n`;
    srt += `${formatSrtTime(blockStart)} --> ${formatSrtTime(blockEnd)}\n`;
    srt += `${block}\n\n`;
    blockStart = blockEnd;
    index++;
  }

  return srt;
}

function formatSrtTime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.floor((seconds % 1) * 1000);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')},${String(ms).padStart(3, '0')}`;
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
  } else {
    logger.info('Edit type: NONE (dance/music only — no captions)');
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
    let subContent = null;
    let subPath = null;

    if (needsTranslation && translatedText) {
      // Use translated SRT for non-English speech
      subContent = generateTranslatedSRT(dialogue.transcript, translatedText, duration);
      subPath = path.join(tmpDir, `captions_${Date.now()}.srt`);
    } else if (editType === 'tiktok_captions' && dialogue.transcript) {
      // Use TikTok-style ASS for English speech
      subContent = generateTikTokCaptions(dialogue.transcript, dialogue.words, duration);
      subPath = path.join(tmpDir, `captions_${Date.now()}.ass`);
    }

    if (subContent && subPath) {
      fs.writeFileSync(subPath, subContent, 'utf8');
      const escapedPath = subPath.replace(/\\/g, '/').replace(/'/g, "'\\\\''");

      if (subPath.endsWith('.ass')) {
        videoFilters.push(`ass='${escapedPath}'`);
      } else {
        videoFilters.push(`subtitles='${escapedPath}'`);
      }

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
    `-c:v libx264 -preset fast -crf 20 -c:a aac -b:a 128k ` +
    `-pix_fmt yuv420p -shortest "${outputPath}" 2>/dev/null`;

  logger.info(`Executing edit command...`);

  try {
    execSync(cmd, { timeout: 180000, maxBuffer: 50 * 1024 * 1024 });

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
      logger.success(`Edited: ${(fs.statSync(outputPath).size / 1024 / 1024).toFixed(1)}MB`);

      // Step 5: QA review with Gemini CLI
      let qaFrames = null;
      if (geminiCLI.isAvailable()) {
        qaFrames = extractFrames(outputPath, path.join(tmpDir, `qa_gemini_${Date.now()}`), framePositions);
        if (qaFrames.length > 0) {
          const review = await geminiCLI.qualityReview(qaFrames);
          if (review) {
            try {
              const jsonMatch = review.match(/\{[\s\S]*\}/);
              if (jsonMatch) {
                const qa = JSON.parse(jsonMatch[0]);
                logger.info(`Gemini CLI QA: ${qa.quality_score}/10 — ${qa.recommendation}`);

                if (qa.recommendation === 'RENDER_AGAIN' && qa.issues?.length > 0) {
                  logger.warn(`Gemini CLI QA issues: ${qa.issues.join(', ')}`);
                }
              }
            } catch {}
          }
          try { fs.rmSync(path.join(tmpDir, `qa_gemini_${Date.now()}`), { recursive: true, force: true }); } catch {}
        }
      }

      // ─── Step 5b: OpenRouter nano QA (non-directional second opinion) ─────
      try {
        const qa2 = getOpenRouterQA();
        const qaDir2 = path.join(tmpDir, `qa_openrouter_${Date.now()}`);
        const orFrames = extractFrames(outputPath, qaDir2, framePositions);
        if (orFrames.length > 0) {
          const qaResult = await qa2.checkEdit(orFrames, editType, country);
          if (qaResult) {
            if (qaResult.yes === false || (qaResult.issues && qaResult.issues.length > 0)) {
              logger.warn(`OpenRouter QA flags edit issues: ${(qaResult.issues || ['Unknown']).join('; ')}`);
            } else {
              logger.success(`OpenRouter QA: edit looks good`);
            }
            if (qaResult.notes) {
              logger.info(`OpenRouter QA note: ${qaResult.notes.substring(0, 120)}`);
            }
          }
        }
        try { fs.rmSync(qaDir2, { recursive: true, force: true }); } catch {}
      } catch (qaError) {
        // QA is non-blocking
        logger.warn(`OpenRouter QA error: ${qaError.message.substring(0, 60)}`);
      }

      // Cleanup edit frames
      try { fs.rmSync(frameDir, { recursive: true, force: true }); } catch {}

      return {
        success: true,
        outputPath,
        hasCaptions: needsCaptions,
        editType,
        hookText: editPlan.suggested_hook_text || null,
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

module.exports = { smartEdit, detectDialogue, generateTikTokCaptions, generateTranslatedSRT };