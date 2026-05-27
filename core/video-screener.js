/**
 * AI Video Screener
 * Analyzes videos using frames + audio + OCR to:
 * 1. Confirm/detect country of origin (visual cues, text, language, transcript)
 * 2. Rank video quality for shorts (1-10)
 * Uses OpenRouter nano vision model with rolling memory.
 * Falls back to Qwen3-VL via Ollama if OpenRouter fails.
 * PaddleOCR runs in parallel for on-screen text detection.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('VideoScreener');
const FRAME_COUNT = 6; // 5-8 frames, evenly spaced
const VISION_MODEL = process.env.VISION_MODEL || 'google/gemma-3-27b-it';
const FALLBACK_MODEL = process.env.VISION_FALLBACK || 'llava:7b';

function extractFrames(videoPath, outputDir) {
  try {
    // Get video duration
    const durOut = execSync(
      `ffprobe -i "${videoPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`,
      { timeout: 10000, encoding: 'utf8' }
    ).trim();
    const duration = parseFloat(durOut) || 30;
    
    // Calculate frame interval — grab 6 evenly spaced frames
    const interval = Math.max(0.5, duration / (FRAME_COUNT + 1));
    
    if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
    
    const framePaths = [];
    for (let i = 1; i <= FRAME_COUNT; i++) {
      const time = (interval * i).toFixed(1);
      const outputPath = path.join(outputDir, `frame_${i}.jpg`);
      execSync(
        `ffmpeg -y -ss ${time} -i "${videoPath}" -vframes 1 -q:v 2 "${outputPath}" 2>/dev/null`,
        { timeout: 30000 }
      );
      if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 1000) {
        framePaths.push(outputPath);
      }
    }
    logger.info(`Extracted ${framePaths.length}/${FRAME_COUNT} frames from ${path.basename(videoPath)}`);
    return framePaths;
  } catch (e) {
    logger.warn(`Frame extraction error: ${e.message.substring(0, 60)}`);
    return [];
  }
}

function encodeImageToBase64(imagePath) {
  try {
    const buffer = fs.readFileSync(imagePath);
    return 'data:image/jpeg;base64,' + buffer.toString('base64');
  } catch {
    return null;
  }
}

async function callOpenRouterVision(messages, model = VISION_MODEL) {
  const axios = require('axios');
  const keys = [];
  if (process.env.OPENROUTER_API_KEY) keys.push(process.env.OPENROUTER_API_KEY);
  for (let i = 2; i <= 8; i++) {
    if (process.env[`OPENROUTER_API_KEY_${i}`]) keys.push(process.env[`OPENROUTER_API_KEY_${i}`]);
  }
  
  for (const key of keys) {
    try {
      const response = await axios.post(
        'https://openrouter.ai/api/v1/chat/completions',
        {
          model: model,
          messages: messages,
          max_tokens: 300,
          temperature: 0.3,
        },
        {
          headers: {
            'Authorization': `Bearer ${key}`,
            'Content-Type': 'application/json',
            'HTTP-Referer': 'https://github.com/mr-worldwidewebster',
            'X-Title': 'Mr. WorldWideWebster',
          },
          timeout: 30000,
        }
      );
      if (response.data?.choices?.[0]?.message?.content) {
        return response.data.choices[0].message.content;
      }
    } catch (e) {
      logger.warn(`OpenRouter vision call failed: ${e.message.substring(0, 60)}`);
    }
  }
  return null;
}

async function callOllamaVision(messages, model = 'qwen2.5-vl:7b') {
  try {
    const http = require('http');
    // Ollama doesn't support multi-modal in chat completions API easily
    // We'll use a text-only prompt with frame descriptions instead
    // Build a text description from the visual data
    let textPrompt = '';
    for (const msg of messages) {
      if (msg.role === 'user') {
        const parts = msg.content;
        if (typeof parts === 'string') {
          textPrompt += parts + '\n';
        } else if (Array.isArray(parts)) {
          for (const p of parts) {
            if (p.type === 'text') textPrompt += p.text + '\n';
          }
        }
      }
    }
    
    const data = JSON.stringify({
      model: model || 'qwen2.5:7b',
      prompt: textPrompt,
      stream: false,
      options: { temperature: 0.3, num_predict: 300 }
    });
    
    return new Promise((resolve) => {
      const req = http.request({
        hostname: '127.0.0.1', port: 11434, path: '/api/generate',
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) },
        timeout: 60000
      }, (res) => {
        let body = '';
        res.on('data', chunk => body += chunk);
        res.on('end', () => {
          try {
            const p = JSON.parse(body);
            if (p.response) resolve(p.response.trim());
          } catch {}
          resolve(null);
        });
      });
      req.on('error', () => resolve(null));
      req.on('timeout', () => { req.destroy(); resolve(null); });
      req.write(data);
      req.end();
    });
  } catch {
    return null;
  }
}

async function runPaddleOCR(framePaths) {
  try {
    // Use first 2 frames for OCR (enough for text detection)
    const targets = framePaths.slice(0, 2);
    const results = [];
    
    for (const fp of targets) {
      const pyPath = fp.replace(/\\/g, '\\\\');
      try {
        const output = execSync(
          `python3 -c "
import json
try:
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False, use_gpu=False)
    result = ocr.ocr('${pyPath}', cls=True)
    texts = []
    if result and result[0]:
        for line in result[0]:
            texts.append(line[1][0])
    print(json.dumps({'texts': texts}))
except Exception as e:
    print(json.dumps({'texts': [], 'error': str(e)[:100]}))
" 2>&1`,
          { timeout: 60000, encoding: 'utf8', maxBuffer: 5*1024*1024 }
        ).toString().trim();
        
        // Extract JSON from output (last line)
        const lines = output.split('\n').filter(l => l.startsWith('{'));
        if (lines.length > 0) {
          const parsed = JSON.parse(lines[lines.length - 1]);
          if (parsed.texts && parsed.texts.length > 0) {
            results.push(...parsed.texts);
          }
        }
      } catch {}
    }
    
    if (results.length > 0) {
      logger.info(`OCR detected text: ${results.join(' | ').substring(0, 100)}`);
    }
    return results;
  } catch (e) {
    logger.warn(`PaddleOCR error: ${e.message.substring(0, 60)}`);
    return [];
  }
}

/**
 * Screen a single video using AI vision with rolling memory.
 * @param {Object} video - { path, title, sourceUrl, platform }
 * @param {string} expectedCountry - The country we originally targeted
 * @param {string} transcriptText - Text transcription from whisper
 * @returns {Object} - { detectedCountry, originalCountry, countryCorrected, score, reasoning }
 */
async function screenVideo(video, expectedCountry, transcriptText) {
  const videoPath = video.path;
  logger.info(`Screening: ${path.basename(videoPath)} (expected: ${expectedCountry})`);
  
  // Step 1: Extract frames
  const framesDir = path.join(path.dirname(videoPath), `screener_frames_${Date.now()}`);
  const framePaths = extractFrames(videoPath, framesDir);
  
  if (framePaths.length === 0) {
    logger.warn('No frames extracted — using transcript-only analysis');
    // Fall back to text-only analysis
    return await screenWithoutFrames(expectedCountry, transcriptText);
  }
  
  // Step 2: Run PaddleOCR in parallel with vision analysis
  const ocrPromise = runPaddleOCR(framePaths);
  
  // Step 3: AI vision with rolling memory across frames
  let rollingSummary = 'Video starts with an empty scene.';
  const messages = [];
  
  for (let i = 0; i < framePaths.length; i++) {
    const imageBase64 = encodeImageToBase64(framePaths[i]);
    if (!imageBase64) continue;
    
    // Build the prompt with accumulated context
    const userContent = [];
    
    if (i === 0) {
      userContent.push({
        type: 'text',
        text: `Analyze this video frame. Previous context: ${rollingSummary}\nDescribe what you see — people (skin tones, clothing, activities), background (buildings, nature, signs), any text or landmarks visible.`
      });
    } else {
      userContent.push({
        type: 'text',
        text: `Previous context: ${rollingSummary}\nNew frame: update the description. What changes do you see? Any country-specific clues (flags, language on signs, famous landmarks, cultural elements)?`
      });
    }
    
    userContent.push({
      type: 'image_url',
      image_url: { url: imageBase64, detail: 'low' }
    });
    
    const response = await callOpenRouterVision([
      { role: 'system', content: 'You are a cultural analyst. Describe frames concisely with country-relevant details.' },
      { role: 'user', content: userContent }
    ]);
    
    if (response) {
      rollingSummary = response;
    }
    
    // Small delay to avoid rate limits
    if (i < framePaths.length - 1) {
      await new Promise(r => setTimeout(r, 500));
    }
  }
  
  // Try OpenRouter first, fallback to Ollama
  let finalAnalysis = null;
  
  // Step 4: Get OCR results
  const ocrTexts = await ocrPromise;
  
  // Step 5: Final analysis with full context
  const finalPrompt = `Sequence summary: ${rollingSummary}` +
    (transcriptText ? `\nAudio transcript: "${transcriptText.substring(0, 300)}"` : '') +
    (ocrTexts.length > 0 ? `\nOn-screen text detected: ${ocrTexts.join(', ')}` : '') +
    `\n\nBased on ALL the above, determine:
1. COUNTRY: What country is this video from? (use ISO country names like the list: China, Japan, South Korea, Thailand, Vietnam, India, Indonesia, Brazil, Mexico, France, Germany, Italy, Spain, UK, Egypt, Nigeria, Australia)
   - Consider: skin tones, clothing, background scenes, landmarks, flags, language in transcript, text on screen
2. VERIFY: Is the detected country ${expectedCountry}? (YES/NO + short reason)
3. SCORE: Rate this video's quality as a YouTube Short (1-10) based on visual interest, music/dialogue, entertainment value
4. NOTES: Any other observations

Format your response as JSON:
{"country":"detected country","matches_expected":true/false,"reason":"short reason","score":score_number,"notes":"observations"}`;
  
  try {
    const result = await callOpenRouterVision([
      { role: 'system', content: 'You analyze video content to detect country of origin. Respond only with valid JSON.' },
      { role: 'user', content: [{ type: 'text', text: finalPrompt }] }
    ]);
    
    if (result) {
      // Extract JSON from response
      const jsonMatch = result.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        const parsed = JSON.parse(jsonMatch[0]);
        finalAnalysis = {
          detectedCountry: parsed.country || expectedCountry,
          matchesExpected: parsed.matches_expected !== false,
          reason: parsed.reason || '',
          score: typeof parsed.score === 'number' ? parsed.score : 5,
          notes: parsed.notes || ''
        };
      }
    }
  } catch (e) {
    logger.warn(`Final analysis error: ${e.message.substring(0, 60)}`);
  }
  
  // Fallback: try Ollama if OpenRouter failed
  if (!finalAnalysis) {
    logger.info('OpenRouter vision failed — trying Ollama fallback');
    const fallbackResult = await callOllamaVision(
      [{ role: 'user', content: finalPrompt }],
      FALLBACK_MODEL
    );
    
    if (fallbackResult) {
      const jsonMatch = fallbackResult.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        try {
          const parsed = JSON.parse(jsonMatch[0]);
          finalAnalysis = {
            detectedCountry: parsed.country || expectedCountry,
            matchesExpected: parsed.matches_expected !== false,
            reason: parsed.reason || '',
            score: typeof parsed.score === 'number' ? parsed.score : 5,
            notes: parsed.notes || ''
          };
        } catch {}
      }
    }
  }
  
  // Step 6: Cleanup frames
  try { fs.rmSync(framesDir, { recursive: true, force: true }); } catch {}
  
  // Step 7: Build result
  if (finalAnalysis) {
    const corrected = !finalAnalysis.matchesExpected && 
      finalAnalysis.detectedCountry && 
      finalAnalysis.detectedCountry !== expectedCountry;
    
    logger.success(`Screened: ${corrected ? `${expectedCountry}→${finalAnalysis.detectedCountry}` : expectedCountry} | Score: ${finalAnalysis.score}/10`);
    
    return {
      detectedCountry: finalAnalysis.detectedCountry || expectedCountry,
      originalCountry: expectedCountry,
      countryCorrected: corrected,
      score: Math.min(10, Math.max(1, finalAnalysis.score)),
      reasoning: finalAnalysis.reason || '',
      notes: finalAnalysis.notes || ''
    };
  }
  
  // If all analysis failed, return conservative result
  logger.warn(`Screening failed completely for ${expectedCountry} — using transcript text clues`);
  return await screenWithoutFrames(expectedCountry, transcriptText);
}

async function screenWithoutFrames(expectedCountry, transcriptText) {
  // Use transcript text to detect country from language
  let detected = expectedCountry;
  let score = 5;
  
  if (transcriptText) {
    // Simple language-based detection
    const langMap = {
      'China': ['zh', 'zho', 'cmn', 'chinese', 'mandarin', 'cantonese'],
      'Japan': ['ja', 'jpn', 'japanese'],
      'South Korea': ['ko', 'kor', 'korean'],
      'Thailand': ['th', 'tha', 'thai'],
      'Vietnam': ['vi', 'vie', 'vietnamese'],
      'India': ['hi', 'hin', 'hindi', 'tamil', 'telugu', 'bengali'],
      'Indonesia': ['id', 'ind', 'indonesian', 'malay'],
      'Brazil': ['pt', 'por', 'portuguese'],
      'Mexico': ['es', 'spa', 'spanish'],
      'France': ['fr', 'fra', 'french'],
      'Germany': ['de', 'deu', 'german'],
      'Italy': ['it', 'ita', 'italian'],
      'Spain': ['es', 'spa', 'spanish'],
      'Egypt': ['ar', 'ara', 'arabic'],
      'Nigeria': ['en', 'ha', 'hau', 'yoruba'],
      'UK': ['en'],
      'Australia': ['en']
    };
    
    // Check for country mentions in text
    for (const [country, langs] of Object.entries(langMap)) {
      for (const lang of langs) {
        if (transcriptText.toLowerCase().includes(lang) || transcriptText.toLowerCase().includes(country.toLowerCase())) {
          detected = country;
          break;
        }
      }
      if (detected !== expectedCountry) break;
    }
  }
  
  return {
    detectedCountry: detected,
    originalCountry: expectedCountry,
    countryCorrected: detected !== expectedCountry,
    score: score,
    reasoning: 'Transcript-based analysis only (no frames available)',
    notes: ''
  };
}

/**
 * Screen multiple videos in parallel and return results.
 * @param {Array} videos - Array of { path, title, sourceUrl, platform }
 * @param {string} expectedCountry - The country targeted
 * @param {Array} transcriptTexts - Array of transcript strings (parallel to videos)
 * @returns {Array} - Array of screening results sorted by score descending
 */
async function screenVideos(videos, expectedCountry, transcriptTexts) {
  const promises = videos.map((v, i) => 
    screenVideo(v, expectedCountry, transcriptTexts[i] || null)
  );
  
  const results = await Promise.all(promises);
  
  // Attach results to videos
  const screened = videos.map((v, i) => ({
    video: v,
    screening: results[i]
  }));
  
  // Sort by score descending
  screened.sort((a, b) => (b.screening?.score || 0) - (a.screening?.score || 0));
  
  return screened;
}

module.exports = { screenVideo, screenVideos, extractFrames, runPaddleOCR };