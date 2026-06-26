/**
 * Gemini Service — Free AI Brain for Mr. WorldWideWebster
 * Uses Google AI Studio (free tier) with 14-key rotation.
 * Free tier: 15 RPM, 1M tokens/day per key, 14 keys = 210 RPM
 * 
 * Supports:
 * - YouTube URL video analysis (file_data.file_uri)
 * - Video File API upload for fallback
 * - Model fallback chain: gemini-3.5-flash → gemini-2.5-flash
 * - Exponential backoff on 503 (capped at 30s)
 * - Key rotation first, model rotation only after all keys exhausted
 */
const axios = require('axios');
const { Logger } = require('./logger');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { getOllamaProvider } = require('../providers/ollama-provider');
const { getOpenRouterQA } = require('./openrouter-qa');

const logger = new Logger('GeminiService');
const GEMINI_BASE = 'https://generativelanguage.googleapis.com/v1beta';
const GEMINI_UPLOAD = 'https://generativelanguage.googleapis.com/upload/v1beta';
const MIN_DELAY = 2000;
const INLINE_VIDEO_LIMIT_BYTES = 18 * 1024 * 1024;

const MODEL_CHAIN = [
  'gemini-3.5-flash',
  'gemini-2.5-flash',
];

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

class GeminiService {
  constructor() {
    this.keys = [];
    this.currentKeyIndex = 0;
    this.currentModelIndex = 0;
    this.requestCount = 0;
    this.lastResetTime = Date.now();
    this._loadKeys();
  }

  get model() { return MODEL_CHAIN[this.currentModelIndex % MODEL_CHAIN.length]; }

  _loadKeys() {
    for (let i = 1; i <= 14; i++) {
      const key = process.env[`GEMINI_API_KEY${i === 1 ? '' : `_${i}`}`];
      if (key) this.keys.push(key);
    }
    if (this.keys.length === 0 && process.env.GEMINI_API_KEY) this.keys.push(process.env.GEMINI_API_KEY);
    logger.info(`Loaded ${this.keys.length} Gemini API keys, models: ${MODEL_CHAIN.join(' → ')}`);
  }

  _getKey() { return this.keys.length ? this.keys[this.currentKeyIndex % this.keys.length] : null; }

  _rotateKey() {
    if (this.keys.length <= 1) return;
    const prev = this.currentKeyIndex;
    this.currentKeyIndex = (this.currentKeyIndex + 1) % this.keys.length;
    // If we wrapped around to 0, all keys were tried — rotate model
    if (this.currentKeyIndex === 0 && prev !== 0) {
      this._rotateModel();
    }
  }

  _rotateModel() {
    const prev = MODEL_CHAIN[this.currentModelIndex % MODEL_CHAIN.length];
    this.currentModelIndex = (this.currentModelIndex + 1) % MODEL_CHAIN.length;
    const next = this.model;
    if (prev !== next) {
      logger.info(`All keys exhausted for ${prev} — switching model to: ${next}`);
    }
  }

  async _callAPI(contents, options = {}) {
    // Total attempts per cycle = (keys × models) + 1 extra
    const attemptsPerCycle = (this.keys.length || 1) * MODEL_CHAIN.length + 1;
    // Use 3 full retry cycles with escalating cooldown
    const MAX_CYCLES = 3;
    const CYCLE_COOLDOWNS = [0, 60000, 120000]; // cycle 0: no wait, cycle 1: 60s, cycle 2: 120s global cooldown
    let lastError = null;

    for (let cycle = 0; cycle < MAX_CYCLES; cycle++) {
      // Apply global cooldown before this cycle (except first)
      if (CYCLE_COOLDOWNS[cycle] > 0) {
        logger.warn(`All keys exhausted — waiting ${CYCLE_COOLDOWNS[cycle] / 1000}s global cooldown before retry cycle ${cycle + 1}/${MAX_CYCLES}`);
        await new Promise(r => setTimeout(r, CYCLE_COOLDOWNS[cycle]));
      }

      for (let attempt = 0; attempt < attemptsPerCycle; attempt++) {
        const key = this._getKey();
        if (!key) { logger.warn('No API keys available'); return null; }

        try {
          const body = {
            contents,
            generationConfig: {
              temperature: options.temperature || 0.7,
              maxOutputTokens: options.maxTokens || 2048,
              topP: options.topP || 0.9,
            },
          };
          if (options.systemInstruction) {
            body.systemInstruction = { parts: [{ text: options.systemInstruction }] };
          }

          const url = `${GEMINI_BASE}/models/${this.model}:generateContent?key=${key}`;
          const resp = await axios.post(url, body, {
            headers: { 'Content-Type': 'application/json' },
            timeout: options.timeout || 120000,
          });

          this.requestCount++;
          const candidates = resp.data?.candidates;

          if (!candidates || candidates.length === 0) {
            const blockReason = resp.data?.promptFeedback?.blockReason;
            logger.warn(`Gemini empty candidates (model: ${this.model}, block: ${blockReason || 'unknown'}, key: ${this.currentKeyIndex + 1})`);
            this._rotateKey();
            await new Promise(r => setTimeout(r, MIN_DELAY));
            continue;
          }

          const parts = candidates[0].content?.parts;
          if (parts && parts.length > 0) return parts.map(p => p.text || '').join('');
          return null;
        } catch (error) {
          lastError = error;
          const status = error.response?.status;
          const errText = error.response?.data?.error?.message || error.message;

          logger.warn(`Key ${this.currentKeyIndex + 1} model ${this.model} error (status ${status}): ${(errText || '').substring(0, 120)}`);

          if (errText?.includes('not in an ACTIVE state')) {
            logger.warn('Gemini file is not ACTIVE at generation time - falling back to another visual path');
            return null;
          }

          if (status === 403 || errText?.includes('forbidden') || errText?.includes('not allowed') || errText?.includes('permission') || errText?.includes('private video')) {
            logger.warn('Video access denied or file usage denied - not retrying other keys/models for this media file');
            return null;
          }

          if (status === 429 || errText?.includes('quota') || errText?.includes('RESOURCE_EXHAUSTED')) {
            const delay = Math.min(MIN_DELAY * (attempt + 1), 30000);
            logger.warn(`Key ${this.currentKeyIndex + 1} rate limited — rotating, waiting ${delay}ms`);
            this._rotateKey();
            await new Promise(r => setTimeout(r, delay));
            continue;
          }

          if (status === 503 || errText?.includes('high demand') || errText?.includes('temporarily') || errText?.includes('spikes') || errText?.includes('unavailable') || errText?.includes('deadline') || errText?.includes('write EPIPE')) {
            // Sequential backoff: 10s, 20s, 30s cap — gives Gemini time to recover
            const backoffTimes = [10000, 20000, 30000];
            const boIdx = Math.min(attempt, backoffTimes.length - 1);
            const backoff = backoffTimes[boIdx] + Math.floor(Math.random() * 500);
            logger.warn(`Model ${this.model} overloaded — backoff ${Math.round(backoff)}ms (attempt ${attempt + 1}), then rotating key`);
            this._rotateKey();
            await new Promise(r => setTimeout(r, backoff));
            continue;
          }

          if (status === 400) {
            logger.warn(`Key ${this.currentKeyIndex + 1} model ${this.model} 400 error — rotating`);
            this._rotateKey();
            await new Promise(r => setTimeout(r, MIN_DELAY));
            continue;
          }

          this._rotateKey();
          await new Promise(r => setTimeout(r, MIN_DELAY));
        }
      }

      // If we finish a cycle and still have errors, log we're going for another cycle
      if (cycle < MAX_CYCLES - 1) {
        logger.warn(`Cycle ${cycle + 1}/${MAX_CYCLES} exhausted all keys/models — preparing retry with cooldown`);
      }
    }

    logger.error(`All keys + models exhausted after ${MAX_CYCLES} retry cycles. Last: ${(lastError?.message || '').substring(0, 80)}`);
    return null;
  }

  async chat(systemPrompt, userMessage, opts = {}) {
    return this._callAPI([{ role: 'user', parts: [{ text: userMessage }] }], { ...opts, systemInstruction: systemPrompt });
  }

  async chatJSON(systemPrompt, userMessage, opts = {}) {
    const r = await this.chat(systemPrompt + '\n\nIMPORTANT: Respond ONLY with valid JSON.', userMessage, opts);
    if (!r) return null;
    try {
      const m = r.match(/\{[\s\S]*\}/);
      if (m) return JSON.parse(m[0]);
      const a = r.match(/\[[\s\S]*\]/);
      if (a) return JSON.parse(a[0]);
    } catch (e) { logger.warn(`JSON parse: ${e.message.substring(0, 60)}`); }
    return null;
  }

  async rankVideo(url, country, curatorSkill, engagementData = null) {
    let metricsBlock = '';
    if (engagementData) {
      const velocity = engagementData.ageInDays > 0 ? (engagementData.views / engagementData.ageInDays).toFixed(0) : 'N/A';

      metricsBlock = `
ENGAGEMENT METRICS:
- Views: ${engagementData.views || 0}
- Likes: ${engagementData.likes || 0}
- Comments: ${engagementData.comments || 0}
- Age in days: ${engagementData.ageInDays || 0}
- Title: "${engagementData.title || 'Unknown'}"
- Velocity (views/day): ${velocity}`;

      if (engagementData.topComments && engagementData.topComments.length > 0) {
        const commentLines = engagementData.topComments
          .map((c, i) => `  ${i + 1}. "${c.text}" (${c.likes} likes, by ${c.author})`)
          .join('\n');
        metricsBlock += `\n\nTOP VIEWER COMMENTS:\n${commentLines}`;
      }
    }

    const prompt = `Rank this video for Mr. WorldWideWebster channel.

Country: ${country}${metricsBlock}

Judge the video primarily by the actual visual/content hook, humor, surprise, cultural specificity, and Shorts replay value. Use engagement metrics only as supporting context, never as the main approval/rejection reason.

Do not apply adult/sexual/romance/TV-show hard rejects from the title alone. Titles are often clickbait. Only reject for those reasons if you can verify them in the video pixels or extracted frames.

You MUST inspect the attached video visually. If you cannot actually see the video content, return JSON with "verdict":"VISUAL_UNAVAILABLE" and explain that the video was unavailable.

Follow the viral-clip-curator skill instructions in system prompt. Return JSON.`;

    const contents = [{
      role: 'user',
      parts: [
        { file_data: { file_uri: url } },
        { text: prompt }
      ]
    }];

    const response = await this._callAPI(contents, {
      systemInstruction: curatorSkill,
      temperature: 0.3,
      maxTokens: 1024,
      timeout: 120000,
    });

    if (!response) {
      logger.warn(`rankVideo: null for "${url.substring(0, 50)}"`);
      return null;
    }

    try {
      const m = response.match(/\{[\s\S]*\}/);
      if (m) {
          const p = JSON.parse(m[0]);
        if ((p.verdict || '').toUpperCase() === 'VISUAL_UNAVAILABLE' || responseLacksVisualAnalysis(p) || responseRejectsFromTitleOnly(p)) {
          logger.warn('rankVideo: model did not actually analyze video; ignoring response');
          return null;
        }
        p.verdict = (p.verdict || '').toUpperCase() === 'APPROVED' ? 'APPROVED' : 'REJECTED';
        return p;
      }
    } catch (e) {
      logger.warn(`rankVideo JSON: ${e.message.substring(0, 80)}`);
      logger.warn(`Raw: ${response.substring(0, 200)}`);
    }
    return null;
  }

  /**
   * Match a YouTube video URL against a storyboard clip description.
   * Uses the same file_data.file_uri mechanism as rankVideo() — Gemini fetches the URL itself.
   * No download or upload needed for the matching step.
   * 
   * @param {string} url - YouTube URL
   * @param {Object} clipDescription - { visual_direction, phase, voiceover }
   * @param {string} qaSkill - QA skill system prompt
   * @returns {Object|null} - { result: "MATCHED"/"COMPILATION_FOUND"/"REJECTED", reasoning, target_slice_start, target_slice_end, revised_queries }
   */
  async matchVideoClip(url, clipDescription, qaSkill) {
    const prompt = `Check if this video matches a storyboard clip.

STORYBOARD CLIP:
${JSON.stringify(clipDescription, null, 2)}

Evaluate if this YouTube video contains footage that matches the storyboard clip's visual_direction.
Return STRICT JSON with: result ("MATCHED"/"COMPILATION_FOUND"/"REJECTED"), reasoning, target_slice_start, target_slice_end, revised_queries.`;

    const contents = [{
      role: 'user',
      parts: [
        { file_data: { file_uri: url } },
        { text: prompt }
      ]
    }];

    const response = await this._callAPI(contents, {
      systemInstruction: qaSkill,
      temperature: 0.2,
      maxTokens: 1024,
      timeout: 60000,
    });

    if (!response) {
      logger.warn(`matchVideoClip: null for "${url.substring(0, 50)}"`);
      return null;
    }

    try {
      const m = response.match(/\{[\s\S]*\}/);
      if (m) {
        const p = JSON.parse(m[0]);
        return {
          result: (p.result || '').toUpperCase(),
          reasoning: p.reasoning || '',
          target_slice_start: p.target_slice_start || '00:00:00.00',
          target_slice_end: p.target_slice_end || '00:00:00.00',
          revised_queries: p.revised_queries || [],
        };
      }
    } catch (e) {
      logger.warn(`matchVideoClip JSON: ${e.message.substring(0, 80)}`);
      logger.warn(`Raw: ${response.substring(0, 200)}`);
    }
    return null;
  }

  async rankVideoFile(videoPath, country, curatorSkill, engagementData = null) {
    if (!fs.existsSync(videoPath)) {
      logger.warn(`rankVideoFile: video not found: ${videoPath}`);
      return null;
    }

    const fileSize = fs.statSync(videoPath).size;
    const fileName = path.basename(videoPath);
    const fileBuffer = fs.readFileSync(videoPath);

    let metricsBlock = '';
    if (engagementData) {
      const velocity = engagementData.ageInDays > 0 ? (engagementData.views / engagementData.ageInDays).toFixed(0) : 'N/A';

      metricsBlock = `
ENGAGEMENT METRICS:
- Views: ${engagementData.views || 0}
- Likes: ${engagementData.likes || 0}
- Comments: ${engagementData.comments || 0}
- Age in days: ${engagementData.ageInDays || 0}
- Title: "${engagementData.title || 'Unknown'}"
- Velocity (views/day): ${velocity}`;

      if (engagementData.topComments && engagementData.topComments.length > 0) {
        const commentLines = engagementData.topComments
          .map((c, i) => `  ${i + 1}. "${c.text}" (${c.likes} likes, by ${c.author})`)
          .join('\n');
        metricsBlock += `\n\nTOP VIEWER COMMENTS:\n${commentLines}`;
      }
    }

    const prompt = `Rank this video for Mr. WorldWideWebster channel.

Country: ${country}${metricsBlock}

Judge the video primarily by the actual visual/content hook, humor, surprise, cultural specificity, and Shorts replay value. Use engagement metrics only as supporting context, never as the main approval/rejection reason.

Do not apply adult/sexual/romance/TV-show hard rejects from the title alone. Titles are often clickbait. Only reject for those reasons if you can verify them in the video pixels or extracted frames.

You MUST inspect the attached video visually. If you cannot actually see the video content, return JSON with "verdict":"VISUAL_UNAVAILABLE" and explain that the video was unavailable.

Follow the viral-clip-curator skill instructions in system prompt. Return JSON.`;

    if (fileSize <= INLINE_VIDEO_LIMIT_BYTES) {
      logger.info(`Analyzing ${fileName} (${(fileSize / 1024 / 1024).toFixed(1)}MB) as inline video data...`);
      const contents = [{
        role: 'user',
        parts: [
          { inline_data: { mime_type: 'video/mp4', data: fileBuffer.toString('base64') } },
          { text: prompt }
        ]
      }];

      const response = await this._callAPI(contents, {
        systemInstruction: curatorSkill,
        temperature: 0.3,
        maxTokens: 1024,
        timeout: 120000,
      });

      if (!response) { logger.warn('rankVideoFile inline: returned null'); return null; }

      try {
        const m = response.match(/\{[\s\S]*\}/);
        if (m) {
        const p = JSON.parse(m[0]);
          if ((p.verdict || '').toUpperCase() === 'VISUAL_UNAVAILABLE' || responseLacksVisualAnalysis(p) || responseRejectsFromTitleOnly(p)) {
            logger.warn('rankVideoFile inline: model did not actually analyze video; ignoring response');
            return null;
          }
          p.verdict = (p.verdict || '').toUpperCase() === 'APPROVED' ? 'APPROVED' : 'REJECTED';
          return p;
        }
      } catch (e) { logger.warn(`rankVideoFile inline JSON: ${e.message.substring(0, 80)}`); }
      return null;
    }

    // Helper: upload video with the current key and return the uploaded file object.
    async function uploadWithCurrentKey(displayName, buffer, size) {
      const { GoogleGenAI } = require('@google/genai');
      logger.info(`Uploading ${displayName} (${(size / 1024 / 1024).toFixed(1)}MB) to Gemini File API...`);
      const currentKey = this._getKey();
      if (!currentKey) { logger.warn('No API keys for upload'); return null; }

      const tmpPath = path.join(os.tmpdir(), `gemini_upload_${Date.now()}_${Math.random().toString(36).slice(2, 8)}.mp4`);
      try {
        fs.writeFileSync(tmpPath, buffer);
        const ai = new GoogleGenAI({ apiKey: currentKey });
        const videoFile = await ai.files.upload({
          file: tmpPath,
          mimeType: 'video/mp4',
        });
        try { fs.unlinkSync(tmpPath); } catch {}

        if (!videoFile || !videoFile.name) {
          logger.warn('No file name in response');
          return null;
        }

        let fileState = await ai.files.get({ name: videoFile.name });
        for (let poll = 1; poll <= 6; poll++) {
          if (fileState.state === 'ACTIVE') break;
          if (fileState.state === 'FAILED') {
            logger.warn(`File processing FAILED: ${videoFile.name}`);
            return null;
          }
          logger.info(`File uploaded: ${videoFile.name} - poll ${poll}/6, waiting 10s (state: ${fileState.state})...`);
          await new Promise(r => setTimeout(r, 10000));
          fileState = await ai.files.get({ name: videoFile.name });
        }
        if (fileState.state !== 'ACTIVE') {
          logger.warn(`File not ACTIVE after polling: ${videoFile.name} (${fileState.state})`);
          return null;
        }
        await new Promise(r => setTimeout(r, 3000));
        fileState = await ai.files.get({ name: videoFile.name });
        if (fileState.state !== 'ACTIVE') {
          logger.warn(`File lost ACTIVE state before use: ${videoFile.name} (${fileState.state})`);
          return null;
        }
        logger.success(`File ready: ${videoFile.name}`);
        return { file: fileState, key: currentKey };
      } catch (e) {
        try { fs.unlinkSync(tmpPath); } catch {}
        logger.warn(`File upload error: ${e.message.substring(0, 100)}`);
        return null;
      }
    }

    const uploaded = await uploadWithCurrentKey.call(this, fileName, fileBuffer, fileSize);
    if (!uploaded) return null;

    let currentKey = uploaded.key;
    let uploadedFile = uploaded.file;

    const contents = [{
      role: 'user',
      parts: [
        { file_data: { mime_type: 'video/mp4', file_uri: uploadedFile.uri || uploadedFile.name } },
        { text: prompt }
      ]
    }];

    let response = await this._callAPI(contents, {
      systemInstruction: curatorSkill,
      temperature: 0.3,
      maxTokens: 1024,
      timeout: 120000,
    });

    // If query failed (null), check if key rotated — re-upload and retry once
    if (!response) {
      const newKey = this._getKey();
      if (newKey !== currentKey) {
        logger.warn(`Key rotated (${currentKey} → ${newKey}) — re-uploading video and retrying query`);
        // Delete old file
        try {
          const { GoogleGenAI } = require('@google/genai');
          const da = new GoogleGenAI({ apiKey: currentKey });
          await da.files.delete({ name: uploadedFile.name });
        } catch {}
        // Re-upload with new key
        const reUploaded = await uploadWithCurrentKey.call(this, fileName, fileBuffer, fileSize);
        if (reUploaded) {
          currentKey = reUploaded.key;
          uploadedFile = reUploaded.file;
          contents[0].parts[0].file_data.file_uri = uploadedFile.uri || uploadedFile.name;
          response = await this._callAPI(contents, {
            systemInstruction: curatorSkill,
            temperature: 0.3,
            maxTokens: 1024,
            timeout: 120000,
          });
        }
      }
    }

    // Cleanup
    try {
      const { GoogleGenAI } = require('@google/genai');
      const ca = new GoogleGenAI({ apiKey: currentKey });
      await ca.files.delete({ name: uploadedFile.name });
    } catch {}

    if (!response) { logger.warn('rankVideoFile: returned null'); return null; }

    try {
      const m = response.match(/\{[\s\S]*\}/);
      if (m) {
        const p = JSON.parse(m[0]);
        if ((p.verdict || '').toUpperCase() === 'VISUAL_UNAVAILABLE' || responseLacksVisualAnalysis(p) || responseRejectsFromTitleOnly(p)) {
          logger.warn('rankVideoFile: model did not actually analyze video; ignoring response');
          return null;
        }
        p.verdict = (p.verdict || '').toUpperCase() === 'APPROVED' ? 'APPROVED' : 'REJECTED';
        return p;
      }
    } catch (e) { logger.warn(`rankVideoFile JSON: ${e.message.substring(0, 80)}`); }
    return null;
  }

  async analyzeFrames(framePaths, question, skillPrompt) {
    const parts = [{ text: question }];
    for (const fp of framePaths) {
      try {
        if (fs.existsSync(fp)) {
          const buf = fs.readFileSync(fp);
          parts.push({ inlineData: { mimeType: fp.endsWith('.png') ? 'image/png' : 'image/jpeg', data: buf.toString('base64') } });
        }
      } catch {}
    }
    if (parts.length <= 1) return null;
    return this._callAPI([{ role: 'user', parts }], { systemInstruction: skillPrompt, temperature: 0.3, maxTokens: 1024 });
  }

  async generateQueries(country, tKeys, count = 20) {
    // Load query generator skill for rich, specific queries
    const skillPath = path.join(__dirname, '..', 'skills', 'type1', 'query-generator.md');
    let skillContent = '';
    try {
      if (fs.existsSync(skillPath)) {
        skillContent = fs.readFileSync(skillPath, 'utf8');
      }
    } catch (e) {}
    
    const tl = tKeys?.length ? `\nRelevant trend keywords for ${country}: ${tKeys.join(', ')}` : '';
    const userMessage = `Generate ${count} search queries for finding viral clips from ${country}.${tl}\n\nReturn ONLY a JSON array of strings, no markdown.`;
    
    return this.chatJSON(skillContent || 'You generate YouTube Shorts search queries.', userMessage, { temperature: 0.9 });
  }

  /**
   * Generate only the "Today we have..." line for the Daily Random Roulette.
   * Everything else in the intro is a FIXED template.
   * @param {string} country - The country the video is from
   * @param {string} hookDescription - Brief summary of what's happening
   * @param {string} sourceTitle - Original video title
   * @returns {Promise<string>} - Just the "Today we have..." sentence
   */
  async generateRouletteTodayLine(country, hookDescription, sourceTitle) {
    const prompt = `You are writing ONE sentence for a "Daily Random Roulette" YouTube Short.
    
Country: ${country}
Video hook: ${hookDescription || 'a viral moment'}
Original title: "${sourceTitle || 'Unknown'}"

Write ONLY ONE sentence starting with "Today we have" that describes what's in this video in a fun, engaging way.
Example: "Today we have a waiter in China who suddenly starts dancing like nobody is watching!"
Example: "Today we have a soccer fan in Brazil with the most creative celebration you'll ever see."
Example: "Today we have a street food vendor in Thailand making the crispiest spring rolls that'll make you drool."

Keep it under 20 words. Just the one sentence, no extra text, no hashtags.`;

    const result = await this.chat(null, prompt, { temperature: 0.8, maxTokens: 150 });
    // Validate: must be a complete "Today we have..." sentence (>20 chars, starts with "Today we have")
    if (result && /^today we have/i.test(result.trim()) && result.trim().length > 20) {
      return result.trim();
    }
    logger.warn(`generateRouletteTodayLine returned incomplete/invalid response: "${(result || '').substring(0, 60)}" — using fallback`);
    return null; // Null triggers the fallback in the pipeline
  }

  async generateTitle(country, transcript, origTitle, context = {}) {
    // Load viral-metadata-generator skill
    const skillPath = path.join(__dirname, '..', 'skills', 'viral-metadata-generator.md');
    let skillContent = '';
    try {
      if (fs.existsSync(skillPath)) {
        skillContent = fs.readFileSync(skillPath, 'utf8');
      }
    } catch (e) {}

    const visualParts = [
      `Country: ${country}`,
      context.reasoning ? `Gemini visual ranking: ${context.reasoning}` : '',
      context.searchQuery ? `Discovery query: ${context.searchQuery}` : '',
      context.hookScore ? `Hook score: ${context.hookScore}/10` : '',
      context.geminiScore ? `Overall score: ${context.geminiScore}/10` : '',
      context.editType ? `Edit type: ${context.editType}` : '',
      context.hasCaptions !== undefined ? `Captions added: ${context.hasCaptions}` : '',
      context.sourceTitle ? `Original video title: "${context.sourceTitle}"` : '',
      context.viewCount ? `Original view count: ${context.viewCount}` : '',
      context.hookDescription ? `Visual hook: ${context.hookDescription}` : '',
      context.comments ? `Top viewer comments:\n${context.comments}` : '',
      transcript && transcript.length > 10 ? `Transcript excerpt: "${transcript.substring(0, 250)}"` : '',
    ].filter(Boolean);

    let visualSummary = visualParts.join('\n') || `A viral moment from ${country}`;
    if (origTitle) {
      visualSummary += `\n\nOriginal title: "${origTitle}"`;
    }

    const systemPrompt = skillContent || 'You are an expert YouTube Shorts metadata writer. Create clickable, engaging titles and descriptions for viral clips. Return JSON: {"title":"...","description":"...","tags":[...]}';

    const userPrompt = `Create a YouTube Shorts title, description, and tags for this clip.

VIDEO CONTEXT:
${visualSummary}

REQUIREMENTS:
- Title must be clickable, specific to the actual visual content (not generic like "${country} Clip")
- Description should describe the hook and end with a call to action + hashtags
- Tags should be 5-10 relevant keywords
- If transcript is available, use it to understand what's happening
- If original title is available, use it for context but create a NEW better title
- If comments are available, understand what viewers found interesting

Return ONLY valid JSON: {"title":"...","description":"...","tags":["tag1","tag2",...]}`;

    // Dedicated retry loop with 10s delay between keys
    const maxCycles = 2;
    const keysCount = this.keys.length;
    
    for (let cycle = 0; cycle < maxCycles; cycle++) {
      for (let ki = 0; ki < keysCount; ki++) {
        // Set current key
        this.currentKeyIndex = (this.currentKeyIndex + 1) % keysCount;
        const apiKey = this.keys[this.currentKeyIndex];
        if (!apiKey) continue;

        if (cycle > 0 || ki > 0) {
          await new Promise(r => setTimeout(r, 10000)); // 10s delay between key attempts
        }

        const models = MODEL_CHAIN;
        for (const model of models) {
          try {
            const result = await this.chatJSON(systemPrompt, userPrompt, { temperature: 0.8, maxTokens: 512, model });
            if (result && result.title && result.title.length > 5) {
              logger.success(`Title generated (key ${this.currentKeyIndex + 1}, model ${model}): "${result.title.substring(0, 50)}"`);
              return result;
            }
          } catch (e) {
            logger.warn(`Title gen fail key ${this.currentKeyIndex + 1} model ${model}: ${e.message.substring(0, 60)}`);
          }
        }
      }
      logger.info(`Title gen: cycle ${cycle + 1}/${maxCycles} exhausted, waiting before retry`);
      if (cycle < maxCycles - 1) {
        await new Promise(r => setTimeout(r, 15000));
      }
    }

    // ─── LAST RESORT: Local Ollama fallback (Gemma 4, etc.) ────
    logger.warn('All Gemini+OpenRouter keys exhausted — trying local Ollama fallback');
    const ollama = getOllamaProvider();
    try {
      if (ollama.isAvailable()) {
        const ollamaResult = await ollama.generateJSON(systemPrompt, userPrompt, {
          temperature: 0.8,
          maxTokens: 512,
        });
        if (ollamaResult && ollamaResult.title && ollamaResult.title.length > 5) {
          logger.success(`Title generated via local Ollama: "${ollamaResult.title.substring(0, 50)}"`);
          return ollamaResult;
        }
        logger.warn('Ollama title generation returned invalid result');
      } else {
        logger.info('Ollama not available — checking if we can start it...');
        await ollama.checkAvailability();
        if (ollama.isAvailable()) {
          const ollamaResult = await ollama.generateJSON(systemPrompt, userPrompt, {
            temperature: 0.8,
            maxTokens: 512,
          });
          if (ollamaResult && ollamaResult.title && ollamaResult.title.length > 5) {
            logger.success(`Title generated via local Ollama (lazy init): "${ollamaResult.title.substring(0, 50)}"`);
            return ollamaResult;
          }
        }
      }
    } catch (e) {
      logger.warn(`Ollama title gen failed: ${e.message.substring(0, 80)}`);
    }

    // ─── LAST RESORT: OpenRouter fallback ────
    logger.warn('Ollama unavailable — trying OpenRouter as final fallback');
    try {
      const openrouter = getOpenRouterQA();
      const orResult = await openrouter.generateTitleAndHashtags(origTitle || context.sourceTitle, null, country);
      if (orResult && orResult.title && orResult.title.length > 5) {
        logger.success(`Title generated via OpenRouter: "${orResult.title.substring(0, 50)}"`);
        return {
          title: orResult.title,
          description: `${orResult.title}\n\nWhat do you think of this? 👇\n\n${(orResult.hashtags || ['#' + country.toLowerCase().replace(/\s/g, ''), '#shorts', '#viral']).map(t => t.startsWith('#') ? t : '#' + t).join(' ')}`,
          tags: orResult.hashtags || [],
        };
      }
      logger.warn('OpenRouter title generation returned null');
    } catch (e) {
      logger.warn(`OpenRouter title gen failed: ${e.message.substring(0, 80)}`);
    }

    logger.warn('All keys exhausted for title generation — using fallback');
    return null;
  }

  async translate(text) {
    if (!text || text.length < 3) return text;
    return this.chat('Translate to English. Return ONLY translation.', text, { temperature: 0.3, maxTokens: 512 });
  }

  hasProfanity(text) {
    if (!text) return false;
    return ['fuck', 'shit', 'bitch', 'asshole', 'motherfucker', 'dick', 'cunt', 'pussy', 'bastard', 'whore', 'slut', 'nigger', 'nigga', 'faggot', 'retard', 'chink']
      .some(w => new RegExp(`\\b${w}\\b`, 'i').test(text.toLowerCase()));
  }

  getStats() {
    return { keysLoaded: this.keys.length, currentKey: this.currentKeyIndex + 1, requestsThisSession: this.requestCount };
  }
}

let instance = null;
function getGeminiService() { if (!instance) instance = new GeminiService(); return instance; }
module.exports = { GeminiService, getGeminiService };
