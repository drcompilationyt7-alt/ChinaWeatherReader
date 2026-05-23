/**
 * Mr. WorldWideWebster - AI CREATE Pipeline
 * 
 * Creates original content from scratch:
 * - Comparisons (US vs UK, Chinese vs American, etc.)
 * - News summaries
 * - Culture explainers
 * - Trend analysis
 * - Music genre breakdowns ("What is UK Drill?", "What is Chinese Pop?")
 * 
 * This is the most AI-intensive path - it writes scripts, generates visuals,
 * creates TTS, and compiles everything into a video.
 */
const path = require('path');
const fs = require('fs');
const { Logger } = require('../core/logger');

class AICreatePipeline {
  constructor() {
    this.logger = new Logger('AICreatePipeline');
  }

  /**
   * Process content through the AI CREATE pipeline
   */
  async processCreate(params) {
    const { sourceContent, contentType, decision, outputDir, ai, config } = params;
    const safeId = Date.now();
    const basePath = path.join(outputDir, `ai_create_${safeId}`);

    if (!fs.existsSync(basePath)) {
      fs.mkdirSync(basePath, { recursive: true });
    }

    this.logger.info(`Creating original content: ${contentType}`);

    // Step 1: Generate the script based on content type
    const script = await this._generateScript(sourceContent, contentType, ai);
    this.logger.info(`Script generated: ${script.title}`);

    // Step 2: Save script
    const scriptPath = path.join(config.paths.scripts, `ai_create_${safeId}_script.txt`);
    fs.writeFileSync(scriptPath, script.fullScript);

    // Step 3: Generate TTS audio
    const audioResult = await this._generateAudio(script, ai, basePath);
    this.logger.info(`Audio generated`);

    // Step 4: Generate visuals
    let visuals = [];
    if (contentType === 'comparison' || contentType === 'explainer') {
      visuals = await this._generateVisuals(script, ai, config, basePath);
      this.logger.info(`Visuals generated: ${visuals.length} images`);
    }

    const result = {
      type: 'ai_create',
      contentType: contentType,
      title: script.title,
      script: script,
      scriptPath: scriptPath,
      audioFiles: audioResult,
      visuals: visuals,
      outputPath: basePath,
      duration: script.estimatedDuration,
      videoFile: `${basePath}.mp4`,
      metadata: {
        sourceTitle: sourceContent?.title || 'Original content',
        contentType: contentType,
        generatedAt: new Date().toISOString(),
      },
    };

    this.logger.success(`AI Create pipeline complete: "${script.title}"`);
    return result;
  }

  /**
   * Generate script based on content type
   */
  async _generateScript(content, contentType, ai) {
    const contentTitle = content?.title || 'Global trends';
    const contentPlatform = content?.platform || 'around the world';

    let systemPrompt = '';

    switch (contentType) {
      case 'comparison':
        systemPrompt = `You are a script writer for "Mr. WorldWideWebster", a YouTube channel that compares and contrasts cultural trends from around the world.

Create a 45-60 second YouTube Shorts script that compares two things.

FORMAT:
1. HOOK: Start with an interesting difference/similarity
2. BREAKDOWN: Alternating points comparing A vs B
3. KEY INSIGHT: The most surprising difference
4. VERDICT: Which one wins and why
5. CTA: "Which side are you on? Comment below. Follow Mr. WorldWideWebster!"

CONTENT TO COMPARE: ${contentTitle} from ${contentPlatform}

Respond with JSON:
{
  "title": "US vs UK: [Comparison] 🇺🇸🇬🇧",
  "estimatedDuration": 50,
  "segments": [
    {"segmentNumber": 1, "duration": 5, "text": "Hook line...", "type": "hook"},
    {"segmentNumber": 2, "duration": 10, "text": "Point 1...", "type": "point"},
    {"segmentNumber": 3, "duration": 10, "text": "Point 2...", "type": "point"},
    {"segmentNumber": 4, "duration": 8, "text": "Insight...", "type": "insight"},
    {"segmentNumber": 5, "duration": 7, "text": "Verdict...", "type": "verdict"},
    {"segmentNumber": 6, "duration": 10, "text": "CTA...", "type": "cta"}
  ],
  "fullScript": "Complete script text..."
}`;
        break;

      case 'explainer':
        systemPrompt = `You are a script writer for "Mr. WorldWideWebster". Create a 45-60 second explainer about global culture.

FORMAT: Hook → Context → Key points → Fun facts → CTA

TOPIC: ${contentTitle}
SOURCE: ${contentPlatform}

Respond with JSON:
{
  "title": "[Topic] Explained 🌍",
  "estimatedDuration": 50,
  "segments": [
    {"segmentNumber": 1, "duration": 5, "text": "Hook...", "type": "hook"},
    {"segmentNumber": 2, "duration": 10, "text": "Context...", "type": "context"},
    {"segmentNumber": 3, "duration": 15, "text": "Key points...", "type": "points"},
    {"segmentNumber": 4, "duration": 8, "text": "Fun fact...", "type": "fun_fact"},
    {"segmentNumber": 5, "duration": 10, "text": "CTA...", "type": "cta"}
  ],
  "fullScript": "Complete script..."
}`;
        break;

      case 'news_summary':
        systemPrompt = `You are a script writer for "Mr. WorldWideWebster". Create a 30-45 second news summary from around the world.

FORMAT: Headline → What happened → Why it matters → CTA

NEWS: ${contentTitle}
SOURCE: ${contentPlatform}

Respond with JSON:
{
  "title": "🌍 World News: [Headline]",
  "estimatedDuration": 35,
  "segments": [
    {"segmentNumber": 1, "duration": 5, "text": "Headline hook...", "type": "headline"},
    {"segmentNumber": 2, "duration": 15, "text": "What happened...", "type": "details"},
    {"segmentNumber": 3, "duration": 10, "text": "Why it matters...", "type": "impact"},
    {"segmentNumber": 4, "duration": 5, "text": "CTA...", "type": "cta"}
  ],
  "fullScript": "Complete script..."
}`;
        break;

      case 'listicle':
        systemPrompt = `You are a script writer for "Mr. WorldWideWebster". Create a 45-60 second listicle.

FORMAT: Hook → Item 1 → Item 2 → Item 3 → Bonus → CTA

TOPIC: ${contentTitle}

Respond with JSON:
{
  "title": "Top 3 [Topic] Around the World 🌎",
  "estimatedDuration": 50,
  "segments": [
    {"segmentNumber": 1, "duration": 5, "text": "Hook...", "type": "hook"},
    {"segmentNumber": 2, "duration": 10, "text": "Number 3...", "type": "item"},
    {"segmentNumber": 3, "duration": 10, "text": "Number 2...", "type": "item"},
    {"segmentNumber": 4, "duration": 10, "text": "Number 1...", "type": "item"},
    {"segmentNumber": 5, "duration": 8, "text": "Bonus...", "type": "bonus"},
    {"segmentNumber": 6, "duration": 7, "text": "CTA...", "type": "cta"}
  ],
  "fullScript": "Complete script..."
}`;
        break;

      default:
        systemPrompt = `You are a script writer for "Mr. WorldWideWebster". Create a 40-50 second YouTube Shorts script.

TOPIC: ${contentTitle}

Respond with JSON:
{
  "title": "Interesting content from around the world 🌍",
  "estimatedDuration": 45,
  "segments": [
    {"segmentNumber": 1, "duration": 5, "text": "Hook...", "type": "hook"},
    {"segmentNumber": 2, "duration": 20, "text": "Content...", "type": "content"},
    {"segmentNumber": 3, "duration": 10, "text": "Wrap up...", "type": "wrap"},
    {"segmentNumber": 4, "duration": 10, "text": "CTA...", "type": "cta"}
  ],
  "fullScript": "Complete script..."
}`;
    }

    try {
      const scriptData = await ai.chatJSON(systemPrompt, `Create a ${contentType} script about: ${contentTitle}`, {
        useScriptModel: true,
        temperature: 0.7,
      });

      // Build full script text
      let fullScript = `${scriptData.title}\n`;
      fullScript += `━`.repeat(50) + '\n\n';
      if (scriptData.segments) {
        for (const seg of scriptData.segments) {
          fullScript += `[${seg.duration}s] ${seg.type.toUpperCase()}: "${seg.text}"\n\n`;
        }
      }
      fullScript += `\n[FOLLOW Mr. WorldWideWebster!]`;
      scriptData.fullScript = scriptData.fullScript || fullScript;

      return scriptData;
    } catch (error) {
      return {
        title: `Global trends from ${contentPlatform}`,
        estimatedDuration: 45,
        segments: [{ segmentNumber: 1, duration: 45, text: `Check out this amazing content from ${contentPlatform}!`, type: 'content' }],
        fullScript: `[45s] CONTENT: "Check out this amazing content from ${contentPlatform}!"\n\n[FOLLOW Mr. WorldWideWebster!]`,
      };
    }
  }

  /**
   * Generate TTS audio for the script
   */
  async _generateAudio(script, ai, basePath) {
    const audioDir = path.join(basePath, 'audio');
    if (!fs.existsSync(audioDir)) fs.mkdirSync(audioDir, { recursive: true });

    const audioFiles = [];
    for (const segment of script.segments || []) {
      const outputFile = path.join(audioDir, `seg_${String(segment.segmentNumber).padStart(2, '0')}.mp3`);
      try {
        // Use 'en-US-GuyNeural' which is valid for both Edge-TTS and as fallback name
        await ai.textToSpeech(segment.text, outputFile, { voice: 'en-US-GuyNeural' });
        if (fs.existsSync(outputFile)) {
          audioFiles.push({ segment: segment.segmentNumber, file: outputFile, duration: segment.duration });
        }
      } catch (error) {
        this.logger.warn(`TTS failed for segment ${segment.segmentNumber}: ${error.message}`);
        // Write text file as placeholder so pipeline can continue
        const placeholderFile = outputFile + '.txt';
        fs.writeFileSync(placeholderFile, segment.text, 'utf8');
        audioFiles.push({ segment: segment.segmentNumber, file: placeholderFile, duration: segment.duration });
      }
    }

    return audioFiles;
  }

  /**
   * Generate visuals for the script using FREE video clips
   * Searches Pexels/Pixabay/YouTube for relevant B-roll footage
   */
  async _generateVisuals(script, ai, config, basePath) {
    const visuals = [];
    const assetsDir = path.join(config.paths.assets, `ai_create_${Date.now()}`);
    if (!fs.existsSync(assetsDir)) fs.mkdirSync(assetsDir, { recursive: true });

    this.logger.info('Searching free video clips for AI-created content...');

    // Get search queries from the script segments
    const searchQueries = [];
    for (const segment of (script.segments || [])) {
      if (segment.type === 'cta') continue;
      // Extract key topics from each segment
      const text = segment.text || '';
      // Use the first 50 chars as a search query
      const query = text.replace(/[^a-zA-Z\s]/g, '').trim().split(' ').slice(0, 6).join(' ');
      if (query.length > 10 && !searchQueries.includes(query)) {
        searchQueries.push(query);
      }
    }

    // Also use the script title as a backup query
    if (searchQueries.length === 0 && script.title) {
      searchQueries.push(script.title.replace(/[^a-zA-Z\s]/g, '').trim());
    }

    try {
      const { FreeVisualSearcher } = require('../hermes-agent/free-visual-searcher');
      const searcher = new FreeVisualSearcher();

      // Search for clips using the first meaningful query
      const mainQuery = searchQueries[0] || 'global culture travel';
      this.logger.info(`Searching clips for: "${mainQuery}"`);

      const clips = await searcher.searchFreeVideoClips(mainQuery, {
        maxResults: Math.min(searchQueries.length, 3),
        maxDuration: 15,
        outputDir: assetsDir,
      });

      for (const clip of clips) {
        visuals.push(clip.file);
        this.logger.info(`  ✅ Added visual: ${clip.source} - ${clip.file}`);
      }

      // If more segments than clips, search additional queries
      if (clips.length < searchQueries.length && searchQueries.length > 1) {
        for (let i = 1; i < searchQueries.length && clips.length < 4; i++) {
          const extraClips = await searcher.searchFreeVideoClips(searchQueries[i], {
            maxResults: 1,
            maxDuration: 10,
            outputDir: assetsDir,
          });
          for (const clip of extraClips) {
            visuals.push(clip.file);
          }
        }
      }

      await searcher.destroy();
    } catch (error) {
      this.logger.warn(`Could not find free visuals: ${error.message}`);
    }

    if (visuals.length === 0) {
      this.logger.warn('No free visuals found — video will use text overlay only');
    }

    return visuals;
  }
}

module.exports = new AICreatePipeline();