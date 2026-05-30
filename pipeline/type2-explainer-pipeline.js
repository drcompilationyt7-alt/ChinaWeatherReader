/**
 * Type 2 Pipeline — World Explainer Short
 * 
 * 8-stage production pipeline for original scripted explainer videos.
 * 
 * Stage 1: Planning Agent (Gemini CLI) → Storyboard
 * Stage 2: Sourcing Agent (Gemini API)  → Search queries
 * Stage 3: Download + QA Loop           → Sourced clips
 * Stage 4: TTS Processing                → Voiceover audio
 * Stage 5: Editor Agent (Gemini CLI)     → Editing manifest
 * Stage 6: Review Agent (OpenRouter)     → Manifest approval
 * Stage 7: Rendering Engine (FFmpeg)     → Raw .mp4
 * Stage 8: Post-Processing (Smart Crop + TikTok Captions + QA)
 * 
 * Stage 8 reuses Type 1's smart-cropper (YOLO subject detection),
 * smart-editor (TikTok word-perfect captions via whisper), and
 * frame-qa (final Gemini CLI review).
 */
const path = require('path');
const fs = require('fs');
const { Logger } = require('../core/logger');
const { getGeminiService } = require('../core/gemini-service');
const { getGeminiCLI } = require('../core/gemini-cli-runner');
const { pickCountry, generateTopicGuidance } = require('../core/explainer-topics');
const { generateAllLines } = require('../core/tts-engine');
const { searchWithQueries, downloadVideo, getVideoMetadata, sliceClip } = require('../core/explainer-downloader');
const { render } = require('../core/video-compiler');
const { smartCrop, extractFrames } = require('../core/smart-cropper');
const { smartEdit } = require('../core/smart-editor');
const { validateOutput, geminiReview } = require('../core/frame-qa');

const logger = new Logger('Type2Pipeline');

const SKILLS_DIR = path.join(__dirname, '..', 'skills');

function loadSkill(name) {
  const skillPath = path.join(SKILLS_DIR, name);
  return fs.existsSync(skillPath) ? fs.readFileSync(skillPath, 'utf8') : null;
}

/**
 * Stage 1: Planning Agent
 * Generates a viral explainer concept + storyboard using Gemini CLI
 */
async function stage1_planning(country, topicGuidance) {
  logger.header('STAGE 1: PLANNING AGENT');

  const planningSkill = loadSkill('planning-agent.md');
  if (!planningSkill) logger.warn('planning-agent.md not found');

  const userPrompt = `Generate a short-form explainer video plan.

Country: ${country}
Angle: ${topicGuidance.angle} (${topicGuidance.angleDescription})
Example topic: "${topicGuidance.topicExample}"

The video should be 30-60 seconds long. Use the 4-phase pacing structure:
Phase 1 (0-5s): Visual Hook
Phase 2 (5-15s): Evidence Montage  
Phase 3 (15-25s): Explanation
Phase 4 (25s-end): Funny Contrast

Generate a compelling topic about ${country} that will grab viewers' attention.
${topicGuidance.angle === 'positive' ? 'Focus on fascinating/impressive aspects.' : 'Focus on interesting/controversial aspects.'}

Return STRICT JSON with topic_title, hook_summary, total_duration_seconds, country, angle, and clips array.
Each clip must have: clip_id, phase, start_time, end_time, visual_direction, voiceover.`;

  const gemini = getGeminiService();
  const result = await gemini.chat(planningSkill || '', userPrompt, {
    temperature: 0.9,
    maxTokens: 4096,
  });

  if (!result) {
    logger.error('Planning agent returned no result');
    return null;
  }

  // Parse JSON from response
  try {
    const jsonMatch = result.match(/\{[\s\S]*\}/);
    if (jsonMatch) {
      const storyboard = JSON.parse(jsonMatch[0]);
      logger.success(`Topic: "${storyboard.topic_title}" (${storyboard.total_duration_seconds || '?'}s, ${storyboard.clips?.length || 0} clips)`);
      
      // Log the full storyboard for review
      logger.info(`=== STORYBOARD ===`);
      logger.info(`Title: ${storyboard.topic_title}`);
      logger.info(`Hook: ${storyboard.hook_summary}`);
      logger.info(`Country: ${storyboard.country} | Angle: ${storyboard.angle}`);
      for (const clip of (storyboard.clips || [])) {
        logger.info(`  Clip ${clip.clip_id} (${clip.start_time}-${clip.end_time}s) [${clip.phase}]: ${clip.voiceover?.substring(0, 60)}...`);
      }
      logger.info(`=== END STORYBOARD ===`);

      return storyboard;
    }
  } catch (e) {
    logger.error(`JSON parse failed: ${e.message}`);
    logger.warn(`Raw: ${result.substring(0, 300)}`);
  }

  return null;
}

/**
 * Stage 2: Sourcing Agent
 * Translates storyboard visual directions into YouTube search queries
 */
async function stage2_sourcing(storyboard) {
  logger.header('STAGE 2: SOURCING AGENT');

  const sourcingSkill = loadSkill('sourcing-agent.md');
  const clipDescriptions = (storyboard.clips || []).map(c => ({
    clip_id: c.clip_id,
    visual_direction: c.visual_direction,
  }));

  const userPrompt = `Convert these visual directions into YouTube search queries for finding raw footage and compilation videos.

Storyboard clips:
${JSON.stringify(clipDescriptions, null, 2)}

For each clip, generate:
- 2 YouTube search queries (3-6 words each)
- 1 fallback query (broader terms)

Return STRICT JSON: { "clips": [{ "clip_id": N, "yt_queries": [...], "fallback_query": "..." }] }`;

  const gemini = getGeminiService();
  const result = await gemini.chat(sourcingSkill || '', userPrompt, {
    temperature: 0.3,
    maxTokens: 2048,
  });

  if (!result) {
    // Fallback: generate generic queries
    logger.warn('Sourcing agent failed — using fallback queries');
    return (storyboard.clips || []).map(c => ({
      clip_id: c.clip_id,
      yt_queries: [`${storyboard.country} ${c.phase} footage`, `${storyboard.country} ${c.phase} compilation`],
      fallback_query: `${storyboard.country} viral clip`,
    }));
  }

  try {
    const jsonMatch = result.match(/\{[\s\S]*\}/);
    if (jsonMatch) {
      const queries = JSON.parse(jsonMatch[0]);
      if (queries.clips && queries.clips.length > 0) {
        logger.success(`Generated queries for ${queries.clips.length} clips`);
        return queries.clips;
      }
    }
  } catch (e) {
    logger.warn(`Sourcing parse failed: ${e.message}`);
  }

  // Fallback
  return (storyboard.clips || []).map(c => ({
    clip_id: c.clip_id,
    yt_queries: [`${storyboard.country} ${c.phase} footage`, `${storyboard.country} ${c.phase} compilation`],
    fallback_query: `${storyboard.country} viral clip`,
  }));
}

/**
 * Stage 3: Download + QA Loop (mirrors Type 1's URL-based ranking)
 * Sources footage for each clip by:
 *   1. Searching YouTube for each query
 *   2. For each search result: send the YouTube URL to Gemini via file_data.file_uri
 *      (same mechanism as Type 1's gemini.rankVideo) — Gemini fetches the preview itself
 *   3. If MATCHED → download the video → accept it
 *   4. If COMPILATION_FOUND → download → slice → accept
 *   5. If REJECTED → try next result, or use revised_queries on retry
 * 
 * No download happens before Gemini matching — saves bandwidth and API calls.
 */
async function stage3_downloadQA(storyboard, queryData, tmpDir) {
  logger.header('STAGE 3: DOWNLOAD + QA LOOP');

  const qaSkill = loadSkill('qa-smart-clips.md');
  const gemini = getGeminiService();
  const clipsDir = path.join(tmpDir, 'sourced_clips');
  if (!fs.existsSync(clipsDir)) fs.mkdirSync(clipsDir, { recursive: true });

  const approvedClips = [];
  const clipTargets = storyboard.clips || [];

  for (let i = 0; i < clipTargets.length; i++) {
    const clip = clipTargets[i];
    const clipDescription = {
      clip_id: clip.clip_id,
      visual_direction: clip.visual_direction,
      phase: clip.phase,
      voiceover: clip.voiceover,
    };
    const queryEntry = queryData.find(q => q.clip_id === clip.clip_id) || {};
    const queries = queryEntry.yt_queries || [`${storyboard.country} ${clip.phase}`];
    const fallback = queryEntry.fallback_query || `${storyboard.country} footage`;

    logger.info(`--- Clip ${clip.clip_id}: "${clip.voiceover?.substring(0, 50)}..." ---`);

    let matched = false;
    let retries = 0;
    const maxRetries = 3;
    let currentQueries = [...queries];

    while (!matched && retries < maxRetries) {
      retries++;
      logger.info(`  Search attempt ${retries}/${maxRetries} with: "${currentQueries[0]}"`);

      // Search YouTube
      const results = searchWithQueries(currentQueries, 5);

      if (results.length === 0) {
        logger.warn(`  No results found`);
        if (retries < maxRetries) {
          currentQueries = [fallback, `${storyboard.country} ${clip.phase}`];
          continue;
        }
        break;
      }

      // Try each result via URL-based Gemini matching (no download yet)
      let qaResult = null;

      for (const result of results) {
        logger.info(`  Trying URL: "${result.title.substring(0, 50)}" (${result.duration}s)`);

        // Step 1: Ask Gemini if this YouTube URL matches the storyboard clip
        // Uses file_data.file_uri — same mechanism as Type 1's rankVideo()
        const qaMatch = await gemini.matchVideoClip(result.url, clipDescription, qaSkill || '');

        if (!qaMatch) {
          logger.warn(`  URL matching returned null (API/keys exhausted) — trying next result`);
          continue;
        }

        const resultType = qaMatch.result || 'REJECTED';
        logger.info(`  URL match: ${resultType} — ${qaMatch.reasoning?.substring(0, 80) || ''}`);

        if (resultType === 'MATCHED') {
          // Step 2: Download the matched video
          logger.info(`  MATCHED — downloading...`);
          const downloadedPath = downloadVideo(result.url, clipsDir);
          if (!downloadedPath) {
            logger.warn(`  Download failed — trying next result`);
            continue;
          }
          const meta = getVideoMetadata(downloadedPath);

          approvedClips.push({
            clip_id: clip.clip_id,
            videoPath: downloadedPath,
            duration: meta.duration,
            sourceTitle: result.title,
            sourceUrl: result.url,
            action: 'use_full',
          });
          matched = true;
          logger.success(`  ✅ Clip ${clip.clip_id}: MATCHED — ${(fs.statSync(downloadedPath).size / 1024 / 1024).toFixed(1)}MB`);
          break;
        }

        if (resultType === 'COMPILATION_FOUND' && qaMatch.target_slice_start) {
          // Step 2: Download the compilation, then slice
          logger.info(`  COMPILATION_FOUND — downloading for slicing...`);
          const downloadedPath = downloadVideo(result.url, clipsDir);
          if (!downloadedPath) {
            logger.warn(`  Download failed — trying next result`);
            continue;
          }

          const slicedPath = path.join(clipsDir, `sliced_${clip.clip_id}.mp4`);
          const sliced = sliceClip(downloadedPath, slicedPath, qaMatch.target_slice_start, qaMatch.target_slice_end);
          if (sliced) {
            approvedClips.push({
              clip_id: clip.clip_id,
              videoPath: sliced,
              duration: 30, // approximate slice duration
              sourceTitle: result.title,
              sourceUrl: result.url,
              action: 'sliced',
            });
            matched = true;
            try { fs.unlinkSync(downloadedPath); } catch {}
            logger.success(`  ✅ Clip ${clip.clip_id}: COMPILATION_FOUND — sliced successfully`);
            break;
          }
          // Slice failed — clean up and try next
          try { fs.unlinkSync(downloadedPath); } catch {}
          continue;
        }

        // REJECTED: try next result, update queries if revised provided
        if (qaMatch.revised_queries?.length > 0) {
          currentQueries = qaMatch.revised_queries;
        }
      }

      if (matched) break;
      currentQueries = [fallback, `${storyboard.country} general`];
    }

    if (!matched) {
      logger.warn(`  ❌ Clip ${clip.clip_id}: No match found after ${maxRetries} attempts — skipping`);
    }
  }

  logger.success(`Sourcing complete: ${approvedClips.length}/${clipTargets.length} clips approved`);
  return approvedClips;
}

/**
 * Stage 4: TTS Processing
 */
async function stage4_tts(storyboard, tmpDir) {
  logger.header('STAGE 4: TTS PROCESSING');

  const ttsDir = path.join(tmpDir, 'tts');
  const results = await generateAllLines(storyboard.clips || [], ttsDir);

  // Build duration map
  const durationMap = {};
  for (const r of results) {
    durationMap[r.clip_id] = r.duration;
  }

  logger.success(`TTS: ${results.filter(r => r.audioFile).length} lines, ${Object.values(durationMap).reduce((a, b) => a + b, 0).toFixed(1)}s total`);

  return { ttsDir, durationMap };
}

/**
 * Stage 5: Editor Agent
 * Generates the frame-by-frame editing manifest
 * Sends sourced video clips to Gemini CLI as visual media for better manifest generation
 */
async function stage5_editor(storyboard, approvedClips, ttsData) {
  logger.header('STAGE 5: EDITOR AGENT');

  const editorSkill = loadSkill('editor-agent.md');

  // Build assets info for the editor
  const assetsInfo = approvedClips.map(c => ({
    clip_id: c.clip_id,
    source: c.videoPath.split(/[\\/]/).pop(),
    duration_seconds: c.duration,
    action: c.action,
  }));

  const audioInfo = (storyboard.clips || []).map(c => ({
    clip_id: c.clip_id,
    source: `tts_${String(c.clip_id).padStart(2, '0')}.mp3`,
    duration_seconds: ttsData.durationMap[c.clip_id] || 2,
  }));

  const userPrompt = `Generate a precise editing manifest from this storyboard and assets.

STORYBOARD:
${JSON.stringify({
  topic_title: storyboard.topic_title,
  total_duration_seconds: storyboard.total_duration_seconds,
  clips: (storyboard.clips || []).map(c => ({
    clip_id: c.clip_id,
    start_time: c.start_time,
    end_time: c.end_time,
    voiceover: c.voiceover,
    phase: c.phase,
  })),
}, null, 2)}

VIDEO ASSETS:
${JSON.stringify(assetsInfo, null, 2)}

AUDIO DURATIONS:
${JSON.stringify(audioInfo, null, 2)}

Generate a STRICT JSON editing manifest with global_settings and timeline array.
Each timeline entry needs: start_time, end_time, video (source, action, duration), audio (source, duration), captions (array with text, start, end, highlight), and effects.

For captions: display 1-3 words at a time. Highlight key nouns/verbs.
For pacing: clips should change every 2-3 seconds.
If TTS is longer than video, loop/slow down/hold final frame.`;

  // Use Gemini CLI for editor (handles complex structured output better)
  // Pass the actual video clips as media so Gemini can visually see them
  const geminiCLI = getGeminiCLI();
  let manifest = null;

  if (geminiCLI.isAvailable()) {
    const videoPaths = approvedClips.map(c => c.videoPath).filter(p => fs.existsSync(p));
    logger.info(`Sending ${videoPaths.length} video clips to Gemini CLI for visual analysis...`);
    
    const result = await geminiCLI.run(userPrompt, {
      skillFile: path.join(SKILLS_DIR, 'editor-agent.md'),
      timeout: 120000,
      videoPaths: videoPaths,
    });
    if (result) {
      try {
        const jsonMatch = result.match(/\{[\s\S]*\}/);
        if (jsonMatch) manifest = JSON.parse(jsonMatch[0]);
      } catch {}
    }
  }

  // Fallback to REST API
  if (!manifest) {
    const gemini = getGeminiService();
    const result = await gemini.chat(editorSkill || '', userPrompt, { temperature: 0.3, maxTokens: 8192 });
    if (result) {
      try {
        const jsonMatch = result.match(/\{[\s\S]*\}/);
        if (jsonMatch) manifest = JSON.parse(jsonMatch[0]);
      } catch {}
    }
  }

  if (manifest) {
    logger.success(`Editor manifest: ${manifest.timeline?.length || 0} timeline segments`);
    logger.info(`=== EDIT MANIFEST ===`);
    for (const seg of (manifest.timeline || [])) {
      logger.info(`  [${seg.start_time}-${seg.end_time}] ${seg.video?.source} → ${(seg.captions || []).map(c => c.text).join(' | ').substring(0, 60)}`);
    }
    logger.info(`=== END MANIFEST ===`);
  } else {
    logger.error('Editor agent failed to produce manifest');
  }

  return manifest;
}

/**
 * Stage 6: Review Agent
 * OpenRouter reviews the manifest for pacing/retention issues
 */
async function stage6_review(manifest) {
  logger.header('STAGE 6: REVIEW AGENT');

  const reviewerSkill = loadSkill('reviewer-agent.md');
  const { getOpenRouterQA } = require('../core/openrouter-qa');
  const qa = getOpenRouterQA();

  const manifestText = JSON.stringify(manifest, null, 2);
  const userPrompt = `Review this video editing manifest for maximum retention and pacing:

${manifestText.substring(0, 3000)}

Evaluate: pacing, audio/visual sync, caption engagement, hook impact.
Return STRICT JSON: {"status": "APPROVED" or "REVISION_NEEDED", "feedback": ["item1", "item2", ...]}`;

  // Use OpenRouter via the QA module (same system, different prompt)
  const result = await qa.ask([], userPrompt);

  if (result && result.status) {
    logger.info(`Review: ${result.status}`);
    if (result.feedback?.length > 0) {
      for (const f of result.feedback) {
        logger.info(`  Feedback: ${f}`);
      }
    }
  } else {
    // If review fails, approve by default
    logger.warn('Review agent unavailable — auto-approving manifest');
    return { status: 'APPROVED', feedback: [] };
  }

  return result;
}

/**
 * Stage 7: Rendering Engine
 */
async function stage7_render(manifest, approvedClips, ttsDir, outputPath) {
  logger.header('STAGE 7: RENDERING ENGINE');

  // Create assets directory symlink or copy references
  const assetsDir = path.dirname(approvedClips[0]?.videoPath || '');

  const result = await render(manifest, assetsDir, ttsDir, outputPath);

  if (result) {
    const sizeMB = (fs.statSync(result).size / 1024 / 1024).toFixed(1);
    logger.success(`Rendered: ${result.split(/[\\/]/).pop()} (${sizeMB}MB)`);
  } else {
    logger.error('Rendering failed');
  }

  return result;
}

/**
 * Stage 8: Post-Processing
 * Reuses Type 1's smartCrop, smartEdit (TikTok captions), and QA
 * 
 * Flow:
 *   8a. Smart crop to 9:16 (YOLO subject detection + Gemini CLI feedback)
 *   8b. Smart edit (TikTok word-perfect captions via whisper)
 *   8c. Final QA review (automated validation + Gemini CLI visual review)
 */
async function stage8_postprocess(renderedPath, country, tmpDir, outputDir) {
  logger.header('STAGE 8: POST-PROCESSING');

  if (!renderedPath || !fs.existsSync(renderedPath)) {
    logger.error('No rendered video to post-process');
    return null;
  }

  // ─── Stage 8a: Smart Crop ──────────────────────────────────────────
  logger.info('Stage 8a: Smart Crop (YOLO subject detection)');
  const croppedPath = path.join(tmpDir, `cropped_${Date.now()}.mp4`);

  // Get duration of rendered video
  let videoDuration = 60;
  try {
    const durOut = require('child_process').execSync(
      `ffprobe -i "${renderedPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`,
      { timeout: 10000, encoding: 'utf8' }
    ).trim();
    if (durOut) videoDuration = parseFloat(durOut);
  } catch {}

  const cropResult = await smartCrop(renderedPath, croppedPath, {
    country,
    duration: Math.min(videoDuration, 60),
    startTime: 0,
  });

  if (!cropResult.success) {
    logger.error('Smart crop failed — using rendered video as-is');
    return renderedPath;
  }
  logger.success(`Smart crop complete: ${croppedPath}`);

  // ─── Stage 8b: Smart Edit (TikTok Captions) ────────────────────────
  logger.info('Stage 8b: Smart Edit (TikTok-style word-perfect captions)');
  const editedPath = path.join(tmpDir, `edited_${Date.now()}.mp4`);

  // smartEdit will:
  // 1. Extract audio → whisper transcribe (picks up TTS voiceover perfectly)
  // 2. Generate word-timed TikTok-style .ASS subtitles (yellow, black outline, 1-2 words)
  // 3. Burn captions into video
  // 4. Run Gemini CLI QA feedback loop on edits

  const editResult = await smartEdit(croppedPath, editedPath, {
    country,
    duration: Math.min(videoDuration, 60),
    // No dialogue/transcript passed — smartEdit transcribes from the video's audio
    // which already has the TTS voiceover baked in
  });

  if (!editResult.success) {
    logger.warn('Smart edit failed — using cropped video without captions');
    return croppedPath;
  }
  logger.success(`Smart edit complete: ${editResult.editType}, captions: ${editResult.hasCaptions}`);

  // ─── Stage 8c: Final QA ────────────────────────────────────────────
  logger.info('Stage 8c: Final QA Review');

  // Automated validation
  const validation = await validateOutput(editedPath);
  if (!validation.passed) {
    logger.warn(`Validation issues: ${validation.issues.join(', ')}`);
    if (validation.score < 4) {
      logger.error('Validation score too low — returning cropped but not edited');
      return croppedPath;
    }
  }

  // Gemini CLI visual review (full video QA)
  const geminiQA = await geminiReview(editedPath);
  logger.info(`Gemini QA: ${geminiQA.score}/10 — ${geminiQA.recommendation}`);
  logger.info(`  Crop: ${geminiQA.cropOk ? 'OK' : 'Issues'} | Subtitles: ${geminiQA.subtitlesOk ? 'OK' : 'Issues'}`);
  logger.info(`  Watermarks: ${geminiQA.watermarkRemoved ? 'Removed' : 'Present'} | Hook: ${geminiQA.hookQuality}`);

  // Final output: copy to output dir with clean name
  const finalPath = path.join(outputDir, `explainer_final_${Date.now()}.mp4`);
  try {
    fs.copyFileSync(editedPath, finalPath);
    logger.success(`Final video: ${finalPath.split(/[\\/]/).pop()} (${(fs.statSync(finalPath).size / 1024 / 1024).toFixed(1)}MB)`);
    return finalPath;
  } catch (e) {
    logger.warn(`Copy failed: ${e.message}`);
    return editedPath;
  }
}

/**
 * Main Type 2 Pipeline Entry Point
 */
async function runType2Pipeline(options = {}) {
  const outputDir = options.outputDir || path.join(__dirname, '..', 'output', 'explainers');
  const tmpDir = path.join(outputDir, `tmp_${Date.now()}`);
  if (!fs.existsSync(tmpDir)) fs.mkdirSync(tmpDir, { recursive: true });

  const memory = options.memory || {};

  logger.header('TYPE 2 EXPLAINER PIPELINE');
  logger.info(`Output: ${outputDir}`);

  // ─── Stage 0: Topic Generation ────────────────────────────────────
  logger.header('STAGE 0: TOPIC GENERATION');
  const country = options.country || pickCountry(memory);
  const topicGuidance = generateTopicGuidance(country);
  logger.info(`Country: ${country} | Angle: ${topicGuidance.angle}`);
  logger.info(`Example: "${topicGuidance.topicExample}"`);

  // ─── Stage 1: Planning ────────────────────────────────────────────
  const storyboard = await stage1_planning(country, topicGuidance);
  if (!storyboard || !storyboard.clips || storyboard.clips.length === 0) {
    logger.error('No storyboard generated');
    return { success: false, error: 'No storyboard' };
  }

  // ─── Stage 2: Sourcing ────────────────────────────────────────────
  const queryData = await stage2_sourcing(storyboard);

  // ─── Stage 3: Download + QA ───────────────────────────────────────
  const approvedClips = await stage3_downloadQA(storyboard, queryData, tmpDir);
  if (approvedClips.length === 0) {
    logger.error('No clips sourced');
    return { success: false, error: 'No clips sourced' };
  }

  // ─── Stage 4: TTS ─────────────────────────────────────────────────
  const ttsData = await stage4_tts(storyboard, tmpDir);

  // ─── Stage 5: Editor ──────────────────────────────────────────────
  let manifest = await stage5_editor(storyboard, approvedClips, ttsData);
  if (!manifest) {
    logger.error('No editor manifest');
    return { success: false, error: 'No editor manifest' };
  }

  // ─── Stage 6: Review Loop ─────────────────────────────────────────
  for (let reviewIter = 0; reviewIter < 3; reviewIter++) {
    const review = await stage6_review(manifest);
    
    if (review.status === 'APPROVED') {
      logger.success(`Manifest approved after ${reviewIter + 1} review(s)`);
      break;
    }

    if (reviewIter < 2 && review.feedback?.length > 0) {
      logger.info(`Revision ${reviewIter + 1}: Applying ${review.feedback.length} feedback items`);
      
      // Send back to editor for revision
      const editorSkill = loadSkill('editor-agent.md');
      const revisionPrompt = `REVISE this editing manifest based on feedback.

Previous manifest:
${JSON.stringify(manifest, null, 2).substring(0, 4000)}

Feedback to address:
${review.feedback.map(f => `- ${f}`).join('\n')}

Return the COMPLETE revised manifest as STRICT JSON.`;

      const geminiCLI = getGeminiCLI();
      let revisedManifest = null;

      if (geminiCLI.isAvailable()) {
        const result = await geminiCLI.run(revisionPrompt, { skillFile: path.join(SKILLS_DIR, 'editor-agent.md'), timeout: 120000 });
        if (result) {
          try {
            const jsonMatch = result.match(/\{[\sS]*\}/);
            if (jsonMatch) revisedManifest = JSON.parse(jsonMatch[0]);
          } catch {}
        }
      }

      if (revisedManifest) {
        manifest = revisedManifest;
        logger.success(`Manifest revised based on feedback`);
      }
    } else {
      logger.warn('No actionable feedback — proceeding with current manifest');
      break;
    }
  }

  // ─── Stage 7: Render ──────────────────────────────────────────────
  const renderedPath = path.join(tmpDir, `explainer_raw_${Date.now()}.mp4`);
  const rendered = await stage7_render(manifest, approvedClips, ttsData.ttsDir, renderedPath);

  if (!rendered) {
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}
    return { success: false, error: 'Render failed' };
  }

  // ─── Stage 8: Post-Process (Crop + Captions + QA) ─────────────────
  const finalPath = await stage8_postprocess(rendered, country, tmpDir, outputDir);

  // ─── Cleanup ──────────────────────────────────────────────────────
  try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}

  if (finalPath && fs.existsSync(finalPath)) {
    const result = {
      success: true,
      videoPath: finalPath,
      title: storyboard.topic_title,
      country,
      angle: topicGuidance.angle,
      clipsApproved: approvedClips.length,
      totalClips: (storyboard.clips || []).length,
      manifestPath: renderedPath.replace('.mp4', '_manifest.json'),
    };
    
    logger.header('PIPELINE COMPLETE');
    logger.success(`Video: ${finalPath}`);
    logger.success(`Title: ${result.title}`);
    logger.success(`Country: ${country}`);
    logger.success(`Angle: ${topicGuidance.angle}`);
    
    return result;
  }

  return { success: false, error: 'Post-processing failed' };
}

module.exports = { runType2Pipeline };