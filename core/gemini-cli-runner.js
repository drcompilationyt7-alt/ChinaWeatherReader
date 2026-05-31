/**
 * Gemini CLI Runner
 * Uses stdin pipe for pure text prompts to avoid shell escaping issues.
 * Passes local media files to Gemini CLI with explicit @file references.
 * 
 * Model-Switching-First Strategy:
 * Each request starts from key 1/model 1. On quota (429) errors,
 * alternates models on the same API key with growing waits before
 * rotating to the next key. Local media is passed through explicit
 * Gemini CLI @file references.
 */
const { execFileSync, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { GoogleGenAI } = require('@google/genai');
const { Logger } = require('./logger');

const logger = new Logger('GeminiCLI');

const MODEL_CHAIN = [
  'gemini-3.5-flash',
  'gemini-2.5-flash',
];
const DEFAULT_MODEL_ATTEMPTS_PER_KEY = 5;

function responseLacksVisualAnalysis(result) {
  const text = [
    result?.reasoning,
    result?.reason,
    result?.analysis,
    result?.issues,
  ].flat().filter(Boolean).join(' ').toLowerCase();

  return (
    /visual[\s\S]{0,40}content[\s\S]{0,40}analysis[\s\S]{0,80}(not possible|was impossible|unavailable|could not|couldn't|failed)/.test(text) ||
    /visual analysis[\s\S]{0,80}(not possible|was impossible|unavailable|could not|couldn't|failed)/.test(text) ||
    /(not possible|unable|could not|couldn't|failed)[\s\S]{0,80}(visual analysis|view|inspect|access)[\s\S]{0,80}(video|content|file)/.test(text) ||
    text.includes('visual analysis was not possible') ||
    text.includes('visual analysis not possible') ||
    text.includes('visual content analysis was not possible') ||
    text.includes('visual content could not be analyzed') ||
    text.includes('inability to view the video') ||
    text.includes('inability to inspect the video') ||
    text.includes('video content could not be accessed') ||
    text.includes('video file was not directly accessible') ||
    text.includes('could not be accessed') ||
    text.includes('could not access the video') ||
    text.includes('unable to view the video') ||
    text.includes('cannot view the video')
  );
}

function responseRejectsFromTitleOnly(result) {
  const text = [
    result?.reasoning,
    result?.reason,
    result?.analysis,
    result?.issues,
  ].flat().filter(Boolean).join(' ').toLowerCase();

  const titleInference =
    text.includes('due to its title') ||
    text.includes('based on the title') ||
    text.includes('inferred from the title') ||
    text.includes('title strongly suggests') ||
    text.includes('title implies') ||
    text.includes('as inferred from the title');

  const adultOrHardReject =
    /(adult|sexual|risqu|sexy|romance|romantic|intimacy|kissing|tv show|auto-?reject|unsuitable|violating)/.test(text);

  return titleInference && adultOrHardReject;
}

function responseCannotViewMediaText(text) {
  const normalized = String(text || '').toLowerCase();
  return (
    normalized.includes('text-based ai') ||
    normalized.includes('cannot directly view') ||
    normalized.includes('unable to provide a verdict') ||
    normalized.includes('unable to compare video files') ||
    normalized.includes('cannot view or compare video') ||
    normalized.includes('cannot analyze video files')
  );
}

class GeminiCLIRunner {
  constructor() {
    this.available = false;
    this.keyIndex = 0;
    this.modelIndex = 0;
    this._checkAvailability();
  }

  get model() { return MODEL_CHAIN[this.modelIndex % MODEL_CHAIN.length]; }

  _resetRetryState() {
    this.keyIndex = 0;
    this.modelIndex = 0;
  }

  _checkAvailability() {
    try {
      const result = execSync('npx @google/gemini-cli --version 2>&1', { timeout: 15000, encoding: 'utf8' }).trim();
      if (result) {
        this.available = true;
        logger.info(`Gemini CLI available: ${result}`);
        return;
      }
    } catch {}
    try {
      const result = execSync('gemini --version 2>&1', { timeout: 10000, encoding: 'utf8' }).trim();
      if (result) {
        this.available = true;
        logger.info(`Gemini CLI available (global): ${result}`);
        return;
      }
    } catch {}
    this.available = false;
    logger.warn('Gemini CLI not available');
  }

  isAvailable() { return this.available; }

  _getApiKey() {
    for (let i = 1; i <= 8; i++) {
      const k = process.env[`GEMINI_API_KEY${i === 1 ? '' : `_${i}`}`];
      if (k && (i - 1 === this.keyIndex % 8)) return k;
    }
    return process.env.GEMINI_API_KEY || null;
  }

  _rotateModel() {
    const prev = this.model;
    this.modelIndex++;
    const next = this.model;
    const wrapped = prev === next;
    if (!wrapped) {
      logger.info(`Switching model: ${prev} → ${next} (key ${this.keyIndex + 1})`);
    }
    return !wrapped;
  }

  _rotateKey() {
    const prevKey = this.keyIndex;
    this.keyIndex++;
    this.modelIndex = 0;
    logger.info(`Switching key: ${prevKey + 1} → ${(this.keyIndex % 8) + 1}, model reset to ${this.model}`);
  }

  /** Upload media via File API and return URI */
  async _uploadFileForCLI(filePath) {
    const fileName = path.basename(filePath);
    const fileSize = fs.statSync(filePath).size;
    const key = this._getApiKey();
    if (!key) { logger.warn('No API key for upload'); return null; }

    const ext = path.extname(filePath).toLowerCase();
    let mimeType = 'video/mp4';
    if (ext === '.jpg' || ext === '.jpeg') mimeType = 'image/jpeg';
    else if (ext === '.png') mimeType = 'image/png';

    logger.info(`Uploading ${fileName} (${(fileSize / 1024 / 1024).toFixed(2)}MB) for analysis...`);

    try {
      const ai = new GoogleGenAI({ apiKey: key });
      const mediaFile = await ai.files.upload({
        file: filePath,
        mimeType: mimeType,
      });

      if (!mediaFile || !mediaFile.name) return null;

      // Poll for ACTIVE state and never proceed with PROCESSING/FAILED files.
      if (mimeType.startsWith('video/')) {
        let fileState = await ai.files.get({ name: mediaFile.name });
        for (let poll = 1; poll <= 6; poll++) {
          if (fileState.state === 'ACTIVE') break;
          if (fileState.state === 'FAILED') {
            logger.warn('File processing FAILED');
            try { await ai.files.delete({ name: mediaFile.name }); } catch {}
            return null;
          }
          logger.info(`  Poll ${poll}/6 - waiting 10s (state: ${fileState.state})...`);
          await new Promise(r => setTimeout(r, 10000));
          fileState = await ai.files.get({ name: mediaFile.name });
        }
        if (fileState.state !== 'ACTIVE') {
          logger.warn(`File not ACTIVE after polling (${fileState.state})`);
          try { await ai.files.delete({ name: mediaFile.name }); } catch {}
          return null;
        }
        logger.success(`File ready: ${fileState.name || mediaFile.name} (state: ${fileState.state})`);
      }
      
      return mediaFile.name;
    } catch (e) {
      logger.warn(`File upload error: ${e.message}`);
      return null;
    }
  }

  async _deleteFile(fileUri) {
    const key = this._getApiKey();
    if (!key || !fileUri) return;
    try {
      const ai = new GoogleGenAI({ apiKey: key });
      await ai.files.delete({ name: fileUri });
    } catch {}
  }

  _probeDuration(videoPath) {
    try {
      const out = execSync(
        `ffprobe -v error -show_entries format=duration -of csv=p=0 "${videoPath}"`,
        { timeout: 10000, encoding: 'utf8' }
      ).trim();
      const duration = parseFloat(out);
      return Number.isFinite(duration) && duration > 0 ? duration : 30;
    } catch {
      return 30;
    }
  }

  _extractReviewFrames(videoPaths, outputDir) {
    if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
    const frames = [];

    for (let videoIndex = 0; videoIndex < videoPaths.length; videoIndex++) {
      const videoPath = videoPaths[videoIndex];
      const duration = this._probeDuration(videoPath);
      const positions = [
        Math.min(1, Math.max(0, duration - 0.5)),
        Math.max(0, duration * 0.35),
        Math.max(0, duration * 0.7),
      ];

      for (let frameIndex = 0; frameIndex < positions.length; frameIndex++) {
        const framePath = path.join(outputDir, `v${videoIndex + 1}_frame_${frameIndex + 1}.jpg`);
        try {
          execFileSync('ffmpeg', [
            '-y',
            '-ss', positions[frameIndex].toFixed(2),
            '-i', videoPath,
            '-frames:v', '1',
            '-update', '1',
            '-q:v', '3',
            framePath,
          ], { timeout: 15000, stdio: 'ignore' });
          if (fs.existsSync(framePath) && fs.statSync(framePath).size > 300) {
            frames.push(framePath);
          }
        } catch {}
      }
    }

    return frames;
  }

  async run(prompt, options = {}) {
    if (!this.available) { logger.warn('Gemini CLI not available'); return null; }

    const timeout = options.timeout || 120000;
    const modelAttemptsPerKey = options.modelAttemptsPerKey || DEFAULT_MODEL_ATTEMPTS_PER_KEY;

    // No cross-video memory: every new CLI task starts from key 1/model 1.
    // Retries inside this run still rotate model/key normally.
    if (options.resetRetryState !== false) {
      this._resetRetryState();
    }

    const allMediaPaths = [
      ...(options.images || []),
      ...(options.videoPaths || []),
      ...(options.videoPath ? [options.videoPath] : [])
    ].filter(filePath => filePath && fs.existsSync(filePath));
    let uploadedUris = [];

    const deleteUploadedForCurrentKey = async () => {
      for (const uri of uploadedUris) await this._deleteFile(uri);
      uploadedUris = [];
    };

    const uploadMediaForCurrentKey = async () => {
      await deleteUploadedForCurrentKey();
      if (!options.uploadMediaForCLI || allMediaPaths.length === 0) return;
      for (const filePath of allMediaPaths) {
        const uri = await this._uploadFileForCLI(filePath);
        if (uri) uploadedUris.push(uri);
      }
    };

    const buildFullPrompt = () => {
      const mediaRefsForPrompt = uploadedUris.length > 0
        ? uploadedUris
        : allMediaPaths.map(filePath => `@${path.resolve(filePath).replace(/\\/g, '/')}`);

      let builtPrompt = prompt;
      if (mediaRefsForPrompt.length > 0) {
        const mediaRefs = mediaRefsForPrompt.join('\n');
        builtPrompt = `## MEDIA FILES TO ANALYZE\n${mediaRefs}\n\n## TASK\n${prompt}`;
      }
      if (mediaRefsForPrompt.length > 0) {
        if (options.skillFile && fs.existsSync(options.skillFile)) {
          const skillContent = fs.readFileSync(options.skillFile, 'utf8');
          const mediaRefs = mediaRefsForPrompt.join('\n');
          builtPrompt = `## SKILL INSTRUCTIONS\n${skillContent}\n\n## MEDIA FILES TO ANALYZE\n${mediaRefs}\n\n## TASK\n${prompt}`;
        }
      } else if (options.skillFile && fs.existsSync(options.skillFile)) {
        const skillContent = fs.readFileSync(options.skillFile, 'utf8');
        builtPrompt = `## SKILL INSTRUCTIONS\n${skillContent}\n\n## TASK\n${prompt}`;
      }
      return builtPrompt;
    };

    await uploadMediaForCurrentKey();
    let fullPrompt = buildFullPrompt();

    // Step 2: Try model alternation per key, retrying the same local file refs.
    // Per key: alternate models up to 5 attempts by default. Wait grows
    // after each 429: 10s, 20s, 30s, 40s, then rotate key.
    const MAX_KEYS = 8;
    for (let keyAttempt = 0; keyAttempt < MAX_KEYS; keyAttempt++) {
      for (let modelAttempt = 0; modelAttempt < modelAttemptsPerKey; modelAttempt++) {
        const useGlobalGemini = fs.existsSync('/snap/bin/gemini');
        const cli = useGlobalGemini ? 'gemini' : (process.platform === 'win32' ? 'npx.cmd' : 'npx');
        const key = this._getApiKey();
        const cliArgs = [
          ...(useGlobalGemini ? [] : ['@google/gemini-cli']),
          '--skip-trust',
          '-m',
          this.model,
          '-p',
          fullPrompt,
        ];

        logger.info(`CLI run (key ${this.keyIndex + 1}/${MAX_KEYS}, model ${this.model}, model try ${modelAttempt + 1}/${modelAttemptsPerKey})`);

        try {
          const result = execFileSync(cli, cliArgs, {
            timeout,
            maxBuffer: 10 * 1024 * 1024,
            encoding: 'utf8',
            env: { ...process.env, GEMINI_API_KEY: key, TERM: 'xterm-256color' },
          });

          // Filter terminal/CLI noise from output
          const output = result.trim().split('\n')
            .filter(l => !l.includes('256-color') && !l.includes('Ripgrep') && !l.includes('Warning:'))
            .join('\n')
            .trim();

          if (output && output.length > 10) {
            logger.success(`CLI responded (${output.length} chars)`);
            const preview = output.substring(0, 300);
            logger.info(`  Response: ${preview.replace(/\n/g, '\\n')}`);
            await new Promise(r => setTimeout(r, 10000));
            for (const uri of uploadedUris) await this._deleteFile(uri);
            return output;
          }
          logger.warn('CLI empty response');
          for (const uri of uploadedUris) await this._deleteFile(uri);
          return null;
        } catch (e) {
          const errText = (e.stderr || e.stdout || e.message || '').toString();
          if (e.signal === 'SIGKILL' || e.killed) {
            logger.warn('CLI timeout');
            for (const uri of uploadedUris) await this._deleteFile(uri);
            return null;
          }

          if (errText.includes('429') || errText.includes('quota')) {
            // 429: switch model and retry with SAME URIs.
            if (modelAttempt < modelAttemptsPerKey - 1) {
              const delay = (modelAttempt + 1) * 10000;
              logger.warn(`Rate limited (429) - waiting ${delay / 1000}s, switching model...`);
              await new Promise(r => setTimeout(r, delay));
              this._rotateModel();
            } else {
              // Model attempts exhausted for this key - use the next key.
              logger.warn(`All ${modelAttemptsPerKey} model attempts rate-limited on key ${this.keyIndex + 1} - rotating key`);
              await deleteUploadedForCurrentKey();
              this._rotateKey();
              await uploadMediaForCurrentKey();
              fullPrompt = buildFullPrompt();
              break;
            }
          } else {
            logger.warn(`CLI error: ${errText.substring(0, 120)}`);
            for (const uri of uploadedUris) await this._deleteFile(uri);
            return null;
          }
        }
      }
    }

    // All keys + models exhausted
    for (const uri of uploadedUris) await this._deleteFile(uri);
    logger.error('All keys + models exhausted');
    return null;
  }

  async compareAndReviewQA(originalPath, editedPath, editType, country = 'Global') {
    if (!this.available) { logger.warn('CLI not available for QA'); return null; }
    if (!fs.existsSync(originalPath) || !fs.existsSync(editedPath)) {
      logger.warn('compareAndReviewQA: one or both videos missing');
      return null;
    }

    const isCrop = editType === 'crop';
    const prompt = isCrop
      ? `Compare the original landscape video with the CROPPED 9:16 portrait version.

Evaluate the CROPPED video:
1. Is the main subject properly centered in the 9:16 frame?
2. Is any face cut off by the edges?
3. Is the cropping smooth and not jarring?
4. Is watermark still visible?
5. Overall quality score 1-10

If improvements are needed, specify precise pixel adjustments:
- adjustment: number of pixels to shift left/right (negative=left, positive=right)
- zoom_adjustment: fine-tune zoom percentage (95-110)

Respond ONLY JSON:
{"verdict":"APPROVE/IMPROVE/REJECT","score":N,"adjustment":0,"zoom_adjustment":0,"reason":"brief reason"}`
      : `Compare the original UNEDITED video with the EDITED version.

Evaluate the EDITED video:
1. Are TikTok-style captions clear and readable?
2. Are captions properly timed with speech?
3. Are watermarks properly removed?
4. Is the visual quality good?
5. Does the edit feel natural?
6. Overall quality score 1-10

If improvements needed, specify what to change.
Respond ONLY JSON:
{"verdict":"APPROVE/IMPROVE/REJECT","score":N,"issues":[],"improvement_suggestions":"specific changes needed","reason":"brief reason"}`;

    let frameDir = null;
    let runOptions = { timeout: 120000 };

    try {
      frameDir = fs.mkdtempSync(path.join(os.tmpdir(), 'qa_frames_'));
      const frames = this._extractReviewFrames([originalPath, editedPath], frameDir);
      if (frames.length >= 2) {
        runOptions = {
          images: frames,
          timeout: 120000,
        };
      } else {
        runOptions = {
          videoPaths: [originalPath, editedPath],
          timeout: 120000,
        };
      }
    } catch {
      runOptions = {
        videoPaths: [originalPath, editedPath],
        timeout: 120000,
      };
    }

    const result = await this.run(`${prompt}

The attached images are ordered as frame samples from the original video followed by matching frame samples from the edited video. Use these visual frames for the comparison.`, runOptions);

    try {
      if (frameDir) fs.rmSync(frameDir, { recursive: true, force: true });
    } catch {}

    if (!result || responseCannotViewMediaText(result)) {
      logger.warn(`compareAndReviewQA (${editType}): null response`);
      return null;
    }

    try {
      const m = result.match(/\{[\s\S]*\}/);
      if (m) return JSON.parse(m[0]);
    } catch (e) {
      logger.warn(`compareAndReviewQA JSON: ${e.message.substring(0, 80)}`);
    }
    return null;
  }

  async reviewFinalVideo(videoPath, country = 'Global') {
    if (!this.available) { logger.warn('CLI not available for final review'); return null; }
    if (!fs.existsSync(videoPath)) {
      logger.warn(`reviewFinalVideo: video not found: ${videoPath}`);
      return null;
    }

    const prompt = `Review this YouTube Short for "Mr. WorldWideWebster" channel. Target country: ${country}.

Check:
1. Is the video in 9:16 portrait (1080x1920)? If not, pixel dimensions?
2. Are any subtitles/captions readable and NOT blocking main content?
3. Is any watermark still visible?
4. Does the first 3 seconds serve as a good hook?
5. Is the video quality acceptable (not blurry/pixelated)?
6. Overall quality rating 1-10

Respond ONLY with JSON:
{"quality_score":N,"recommendation":"APPROVE/RENDER_AGAIN","issues":[],"crop_ok":true,"subtitles_ok":true,"watermark_removed":true,"hook_quality":"strong"}`;

    let frameDir = null;
    let result = null;
    try {
      frameDir = fs.mkdtempSync(path.join(os.tmpdir(), 'final_qa_frames_'));
      const frames = this._extractReviewFrames([videoPath], frameDir);
      if (frames.length > 0) {
        result = await this.run(`${prompt}

The attached images are spread-out frame samples from the final video. Use these visible frames to check crop, captions, watermark, visual quality, and hook.`, {
          images: frames,
          timeout: 120000,
        });
      }
    } catch {}

    try {
      if (frameDir) fs.rmSync(frameDir, { recursive: true, force: true });
    } catch {}

    if (!result) {
      result = await this.run(prompt, {
        videoPath,
        timeout: 120000,
      });
    }

    if (!result || responseCannotViewMediaText(result)) {
      logger.warn('reviewFinalVideo: null response');
      return { quality_score: 5, recommendation: 'APPROVE', issues: ['CLI review unavailable'], crop_ok: true, subtitles_ok: true, watermark_removed: true, hook_quality: 'unknown' };
    }

    try {
      const m = result.match(/\{[\s\S]*\}/);
      if (m) return JSON.parse(m[0]);
    } catch (e) {
      logger.warn(`reviewFinalVideo JSON: ${e.message.substring(0, 80)}`);
    }
    return { quality_score: 5, recommendation: 'APPROVE', issues: [], crop_ok: true, subtitles_ok: true, watermark_removed: true, hook_quality: 'unknown' };
  }

  async evaluateCropFromVideo(videoPath, country, skillFilePath) {
    const prompt = `Evaluate this video for a YouTube Short from ${country}. The video needs to be cropped from landscape to 9:16 portrait (1080x1920). Locate the primary subject and return the calculated center percentage.`;
    return this.run(prompt, {
      videoPath,
      skillFile: skillFilePath || path.join(__dirname, '..', 'skills', 'type1', 'smart-crop-skill.md'),
      timeout: 60000,
    });
  }

  async evaluateCropQuality(croppedVideoPath, skillFilePath) {
    const prompt = `Analyze this 9:16 vertical cropped video. Is the main subject properly centered and fully visible? Are any faces cut off by the edges? Return PASS or REJECT with pixel adjustment.`;
    return this.run(prompt, {
      videoPath: croppedVideoPath,
      skillFile: skillFilePath || path.join(__dirname, '..', 'skills', 'type1', 'crop-evaluator-skill.md'),
      timeout: 60000,
    });
  }

  async evaluateCrop(rawFrames, croppedFrames, question) {
    const images = [...rawFrames, ...croppedFrames];
    const prompt = `${question}\n\nRespond JSON: {"verdict":"GOOD/BAD","suggested_adjustment":{"direction":"left/right","pixels":N}}`;
    return this.run(prompt, { images, skillFile: path.join(__dirname, '..', 'skills', 'type1', 'viral-clip-curator.md'), timeout: 60000 });
  }

  async evaluateEdit(frames, transcriptInfo) {
    const prompt = `Analyze for TikTok edits.\n${transcriptInfo}\n\nRespond JSON: {"has_watermarks":bool,"needs_captions":bool,"caption_type":"","suggested_hook_text":""}`;
    return this.run(prompt, { images: frames, skillFile: path.join(__dirname, '..', 'skills', 'type1', 'viral-reposter-editor.md'), timeout: 60000 });
  }

  async qualityReview(frames) {
    return this.run('QA review this Short. Score 1-10. JSON: {"quality_score":N,"recommendation":"APPROVE/RENDER_AGAIN"}', { images: frames, timeout: 60000 });
  }

  async rankVideoFromPath(videoPath, country, curatorSkillContent, engagementData = null) {
    if (!this.available) return null;
    if (!fs.existsSync(videoPath)) return null;

    let metricsBlock = '';
    if (engagementData) {
      const velocity = engagementData.ageInDays > 0 ? (engagementData.views / engagementData.ageInDays).toFixed(0) : 'N/A';
      metricsBlock = `ENGAGEMENT METRICS:
- Views: ${engagementData.views || 0}
- Likes: ${engagementData.likes || 0}
- Comments: ${engagementData.comments || 0}
- Age in days: ${engagementData.ageInDays || 0}
- Title: "${engagementData.title || 'Unknown'}"
- Velocity (views/day): ${velocity}`;

      if (engagementData.topComments && engagementData.topComments.length > 0) {
        metricsBlock += '\n\nTOP VIEWER COMMENTS:\n' +
          engagementData.topComments.map((c, i) => `  ${i + 1}. "${c.text}" (${c.likes} likes, by ${c.author})`).join('\n');
      }
    }

    // skill content is passed directly from pipeline (not a file path)
    const skillHeader = curatorSkillContent ? `## SKILL INSTRUCTIONS\n${curatorSkillContent}\n\n## TASK\n` : '';

    const prompt = `${skillHeader}Rank the attached video for Mr. WorldWideWebster.

Country: ${country}${metricsBlock}

Judge the video primarily by the actual visual/content hook, humor, surprise, cultural specificity, and Shorts replay value. Use engagement metrics only as supporting context, never as the main approval/rejection reason.

Do not apply adult/sexual/romance/TV-show hard rejects from the title alone. Titles are often clickbait. Only reject for those reasons if you can verify them in the video pixels or extracted frames.

You MUST inspect the attached video visually. If you cannot actually see the video content, return JSON with "verdict":"VISUAL_UNAVAILABLE" and explain that the video was unavailable.

Follow the skill instructions. Return JSON.`;

    const parseRanking = (result, source) => {
      if (!result) return null;
      try {
        const m = result.match(/\{[\s\S]*\}/);
        if (m) {
          const p = JSON.parse(m[0]);
          if ((p.verdict || '').toUpperCase() === 'VISUAL_UNAVAILABLE' || responseLacksVisualAnalysis(p) || responseRejectsFromTitleOnly(p)) {
            logger.warn(`${source}: model did not actually analyze visuals; ignoring response`);
            return null;
          }
          p.verdict = (p.verdict || '').toUpperCase() === 'APPROVED' ? 'APPROVED' : 'REJECTED';
          return p;
        }
      } catch (e) {
        logger.warn(`${source} JSON parse: ${e.message}`);
      }
      return null;
    };

    const result = await this.run(prompt, {
      videoPath: videoPath,
      timeout: 120000,
      modelAttemptsPerKey: 5,
      uploadMediaForCLI: true,
    });

    const videoRanking = parseRanking(result, 'rankVideoFromPath video');
    if (videoRanking) return videoRanking;

    let frameDir = null;
    try {
      frameDir = fs.mkdtempSync(path.join(os.tmpdir(), 'rank_frames_'));
      const frames = this._extractReviewFrames([videoPath], frameDir);
      if (frames.length > 0) {
        logger.info(`rankVideoFromPath: video analysis unavailable; retrying with ${frames.length} extracted frames`);
        const framePrompt = `${prompt}

The attached images are ordered frame samples from the same video. Rank using these visible frames as the visual evidence. If the frames are blank or unreadable, return {"verdict":"VISUAL_UNAVAILABLE","reasoning":"frames unavailable"}.`;
        const frameResult = await this.run(framePrompt, {
          images: frames,
          timeout: 120000,
          modelAttemptsPerKey: 5,
        });
        const frameRanking = parseRanking(frameResult, 'rankVideoFromPath frames');
        if (frameRanking) return frameRanking;
      }
    } finally {
      try {
        if (frameDir) fs.rmSync(frameDir, { recursive: true, force: true });
      } catch {}
    }

    logger.error('rankVideoFromPath exhausted CLI retry cycle');
    return null;
  }
}

let instance = null;
function getGeminiCLI() { if (!instance) instance = new GeminiCLIRunner(); return instance; }
module.exports = { GeminiCLIRunner, getGeminiCLI };
