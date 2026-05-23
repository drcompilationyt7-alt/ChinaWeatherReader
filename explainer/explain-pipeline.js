/**
 * Mr. WorldWideWebster - EXPLAIN Pipeline
 * 
 * The "What is this...?" viral format.
 * 
 * Structure:
 * [0-3s]  Visual of the thing + "What is this...?" (Curious Voice)
 * [3-6s]  "This is [NAME]." (Explainer Voice)
 * [6-20s] Details, context, interesting facts
 * [20-25s] Comparison to something familiar
 * [25-30s] CTA / Follow
 * 
 * Categories: food, music, dance, trend, place, culture, product, other
 */
const path = require('path');
const fs = require('fs');
const { Logger } = require('../core/logger');

class ExplainPipeline {
  constructor() {
    this.logger = new Logger('ExplainPipeline');
  }

  /**
   * Process content through the EXPLAIN pipeline
   */
  async processExplain(params) {
    const { sourceContent, explainThing, explainCategory, decision, outputDir, ai, config } = params;
    const safeId = Date.now();
    const basePath = path.join(outputDir, `explain_${safeId}`);

    this.logger.info(`Creating "What is this?" explainer for: ${explainThing} (${explainCategory})`);

    // Step 1: Generate the script
    const script = await this._generateExplainScript(sourceContent, explainThing, explainCategory, ai);
    this.logger.info(`Script generated: ${script.title}`);

    // Step 2: Save script — ensure scripts directory exists
    const scriptsDir = config.paths.scripts;
    if (!fs.existsSync(scriptsDir)) {
      fs.mkdirSync(scriptsDir, { recursive: true });
    }
    const scriptPath = path.join(scriptsDir, `explain_${safeId}_script.txt`);
    fs.writeFileSync(scriptPath, script.fullScript);
    this.logger.info(`Script saved to: ${scriptPath}`);

    // Step 3: Generate two-voice audio (Curious + Explainer)
    const audioResult = await this._generateTwoVoiceAudio(script, ai, config, basePath);
    this.logger.info(`Two-voice audio generated`);

    // Step 4: Generate visual assets if needed
    let visuals = [];
    if (!sourceContent.thumbnailUrl && explainCategory === 'other') {
      visuals = await this._generateVisuals(script, explainThing, ai, config, basePath);
    } else {
      visuals = [sourceContent.thumbnailUrl || null];
    }

    // Step 5: Compile the final video using ffmpeg (free)
    const videoFile = `${basePath}.mp4`;
    const compiled = await this._compileVideo(audioResult, visuals, basePath, videoFile, script);

    const result = {
      type: 'explain',
      title: script.title,
      explainThing: explainThing,
      explainCategory: explainCategory,
      script: script,
      audioFiles: audioResult,
      visuals: visuals,
      scriptPath: scriptPath,
      outputPath: basePath,
      duration: script.estimatedDuration,
      videoFile: compiled || videoFile,
      metadata: {
        sourceTitle: sourceContent.title,
        sourcePlatform: sourceContent.platform,
        generatedAt: new Date().toISOString(),
        category: explainCategory,
      },
    };

    this.logger.success(`Explain pipeline complete: "${script.title}"`);
    return result;
  }

  /**
   * Generate the "What is this?" script with two-voice dialog
   */
  async _generateExplainScript(content, thing, category, ai) {
    const systemPrompt = `You are a script writer for "Mr. WorldWideWebster", a YouTube channel that introduces global culture to English-speaking audiences.

You are creating a "What is this?" style short-form video (30-60 seconds).

FORMAT RULES:
- [VOICE 1 - CURIOUS]: The "What is this?" voice — excited, curious, slightly amazed, speaks in short phrases
- [VOICE 2 - EXPLAINER]: The knowledgeable voice — calm, informative, authoritative
- Keep it under 60 seconds total
- Start with a hook that makes people stop scrolling
- End with a call to action to follow the channel

STRUCTURE:
1. HOOK (0-4s): [CURIOUS] "What is this... [thing]?" — over a close-up visual of the thing
2. REVEAL (4-8s): [EXPLAINER] "This is [NAME]. [Quick definition]"
3. CONTEXT (8-20s): [EXPLAINER or CURIOUS] Where it's from, why it's special, what makes it unique
4. FUN FACT (20-28s): The most interesting/weird/surprising fact
5. COMPARISON (28-35s): "To put it in perspective..." — compare to something the audience knows
6. WRAP (35-45s): [CURIOUS] Recap + [EXPLAINER] Final thought
7. CTA (45-50s+): Follow Mr. WorldWideWebster for more discoveries

CATEGORY GUIDES:
- food: Describe taste, texture, how it's eaten, cultural significance
- music: Genre, origin, what makes it unique, popular artists
- dance: Where it started, key moves, why it went viral
- trend: What it is, how it started, who's doing it
- place: Location, what makes it special, tourist appeal
- culture: Tradition, history, significance to locals
- product: What it does, why it's popular, cost
- other: General interesting thing

CONTENT TO EXPLAIN:
Thing: ${thing}
Category: ${category}
Source: ${content.platform || 'unknown'}: ${content.title || 'Unknown'}

Respond with JSON:
{
  "title": "What is this [thing]? 🌍",
  "estimatedDuration": 45,
  "scenes": [
    {
      "sceneNumber": 1,
      "duration": 4,
      "visualDescription": "Close up of [thing]",
      "voice": "curious",
      "dialogue": "What is this... [thing]?"
    },
    {
      "sceneNumber": 2,
      "duration": 5,
      "visualDescription": "Wider shot showing [thing] in context",
      "voice": "explainer",
      "dialogue": "This is [NAME]. [Quick definition]"
    }
  ],
  "fullScript": "The complete script text combining all dialogue"
}`;

    const userMessage = `Create a "What is this?" explainer script about: ${thing}`;

    try {
      const scriptData = await ai.chatJSON(systemPrompt, userMessage, { 
        useScriptModel: true, 
        temperature: 0.7,
      });

      // Also generate a plain text version of the full script
      let fullScript = `"WHAT IS THIS?" EXPLAINER\n`;
      fullScript += `Title: ${scriptData.title}\n`;
      fullScript += `Thing: ${thing}\n`;
      fullScript += `Category: ${category}\n`;
      fullScript += `Duration: ~${scriptData.estimatedDuration}s\n`;
      fullScript += `━`.repeat(50) + '\n\n';
      
      if (scriptData.scenes) {
        for (const scene of scriptData.scenes) {
          fullScript += `[${scene.duration}s] ${scene.voice.toUpperCase()}:\n`;
          fullScript += `[Visual: ${scene.visualDescription}]\n`;
          fullScript += `"${scene.dialogue}"\n\n`;
        }
      }

      fullScript += `\n[FOLLOW Mr. WorldWideWebster for more global discoveries!]`;

      scriptData.fullScript = fullScript;

      return scriptData;
    } catch (error) {
      this.logger.error(`Script generation failed: ${error.message}`);
      // Fallback script
      return {
        title: `What is this ${thing}? 🌍`,
        estimatedDuration: 45,
        scenes: [
          { sceneNumber: 1, duration: 4, visualDescription: `Close up of ${thing}`, voice: 'curious', dialogue: `What is this... ${thing}?` },
          { sceneNumber: 2, duration: 10, visualDescription: `${thing} in its natural setting`, voice: 'explainer', dialogue: `This is something amazing from ${content.platform || 'around the world'}!` },
          { sceneNumber: 3, duration: 10, visualDescription: `Details of ${thing}`, voice: 'curious', dialogue: `That's incredible! Tell me more.` },
          { sceneNumber: 4, duration: 8, visualDescription: `${thing} being used/enjoyed`, voice: 'explainer', dialogue: `It's popular because... well, you can see why!` },
          { sceneNumber: 5, duration: 8, visualDescription: `Comparison shot`, voice: 'explainer', dialogue: `To put it in perspective, it's like nothing you've ever seen before.` },
        ],
        fullScript: `[4s] CURIOUS: "What is this... ${thing}?"\n[10s] EXPLAINER: "This is something amazing from ${content.platform || 'around the world'}!"\n[10s] CURIOUS: "That's incredible! Tell me more."\n[8s] EXPLAINER: "It's popular because... well, you can see why!"\n[8s] EXPLAINER: "To put it in perspective, it's like nothing you've ever seen before."\n\n[FOLLOW Mr. WorldWideWebster for more global discoveries!]`,
      };
    }
  }

  /**
   * Generate two-voice TTS audio (Curious + Explainer)
   */
  async _generateTwoVoiceAudio(script, ai, config, basePath) {
    const audioFiles = {
      curious: [],
      explainer: [],
      fullMix: path.join(basePath, '_full_audio.mp3'),
    };

    // Ensure audio directory exists
    const audioDir = path.dirname(audioFiles.fullMix);
    if (!fs.existsSync(audioDir)) {
      fs.mkdirSync(audioDir, { recursive: true });
    }

    // Generate audio for each scene
    for (const scene of script.scenes || []) {
      const voiceType = scene.voice === 'curious' ? 'curious' : 'explainer';
      const voice = voiceType === 'curious' ? 'nova' : 'onyx'; // OpenAI TTS voices
      const outputFile = path.join(audioDir, `scene_${String(scene.sceneNumber).padStart(2, '0')}_${voiceType}.mp3`);

      try {
        const result = await ai.textToSpeech(scene.dialogue, outputFile, { voice });
        // Only add if it's a real audio file (not a .txt placeholder)
        if (result && !result.endsWith('.txt')) {
          audioFiles[voiceType].push({
            scene: scene.sceneNumber,
            file: outputFile,
            dialogue: scene.dialogue,
            duration: scene.duration,
          });
        } else {
          this.logger.info(`Scene ${scene.sceneNumber}: TTS unavailable, will use text overlay`);
        }
      } catch (error) {
        this.logger.error(`TTS failed for scene ${scene.sceneNumber}: ${error.message}`);
      }
    }

    return audioFiles;
  }

  /**
   * Compile a simple explainer video using ffmpeg from audio files
   * Creates a static image with text overlay + audio track
   * Falls back to text-overlay video if no audio is available
   */
  async _compileVideo(audioResult, visuals, basePath, videoFile, script) {
    const allAudio = [
      ...(audioResult?.curious || []),
      ...(audioResult?.explainer || []),
    ];

    // If no audio files were actually generated (TTS unavailable), create text-overlay video
    if (allAudio.length === 0) {
      this.logger.warn('No audio files — creating text-overlay video instead');
      return await this._createTextOverlayVideo(script, basePath, videoFile);
    }

    // Try to use ffmpeg to combine audio into a video
    try {
      const { execSync } = require('child_process');
      const fs = require('fs');

      // Create a concat file listing all audio clips in order
      const concatFile = path.join(basePath, '_audio_list.txt');

      // Sort audio files by scene number
      const sorted = [...allAudio].sort((a, b) => a.scene - b.scene);

      // Build a filter_complex that concatenates audio clips
      const audioInputs = sorted
        .filter(a => a.file && fs.existsSync(a.file))
        .map((a, i) => `[${i}:a]`).join('');

      const totalInputs = sorted.filter(a => a.file && fs.existsSync(a.file));

      if (totalInputs.length === 0) {
        this.logger.warn('No valid audio files exist — skipping video');
        return null;
      }

      // Generate a simple black background with text
      const title = (script.title || 'Mr. WorldWideWebster').replace(/"/g, '\\"');
      const totalDuration = sorted.reduce((sum, s) => sum + (s.duration || 5), 0);

      // Create video from audio using ffmpeg:
      // 1. Concat all audio files
      // 2. Add a colored background with title text
      const cmd = `ffmpeg -y -f lavfi -i "color=c=#1a1a2e:s=1080x1920:d=${Math.max(totalDuration, 10)}:r=30" ${totalInputs.map((a, i) => `-i "${a.file}"`).join(' ')} -filter_complex "${totalInputs.map((a, i) => `[${i + 1}:a]`).join('')} concat=n=${totalInputs.length}:v=0:a=1[audio]" -map "0:v" -map "[audio]" -c:v libx264 -preset ultrafast -crf 28 -c:a aac -shortest "${videoFile}"`;

      this.logger.info('Compiling video with ffmpeg...');
      execSync(cmd, { timeout: 120000, maxBuffer: 50 * 1024 * 1024 });

      if (fs.existsSync(videoFile)) {
        this.logger.info(`Video compiled: ${videoFile}`);
        return videoFile;
      }
    } catch (error) {
      this.logger.warn(`Video compilation failed: ${error.message}`);
    }
    return null;
  }

  /**
   * Create a text-overlay video when TTS is unavailable
   * Uses ffmpeg to generate a video with text scenes on a colored background
   */
  async _createTextOverlayVideo(script, basePath, videoFile) {
    try {
      const { execSync } = require('child_process');
      const fs = require('fs');

      const scenes = script.scenes || [];
      const totalDuration = scenes.reduce((sum, s) => sum + (s.duration || 5), 0);
      const safeTitle = (script.title || 'Mr. WorldWideWebster').replace(/'/g, "'\\''");

      // Build a filter_complex that creates text scenes
      // Each scene shows the dialogue text on a colored background for its duration
      const bgColor = '#1a1a2e';
      const textColor = '#ffffff';
      const accentColor = '#e94560';

      // Create individual scene videos
      const sceneFiles = [];
      for (let i = 0; i < scenes.length; i++) {
        const scene = scenes[i];
        const sceneDuration = scene.duration || 5;
        const sceneFile = `${basePath}_scene_${i}.mp4`;
        const dialogue = (scene.dialogue || '').replace(/'/g, "'\\''").replace(/:/g, '\\:');
        const voiceLabel = (scene.voice || 'explainer').toUpperCase();

        // Create a colored background with text overlay
        const cmd = `ffmpeg -y -f lavfi -i "color=c=${bgColor}:s=1080x1920:d=${sceneDuration}:r=30" ` +
          `-vf "drawtext=text='${voiceLabel}':fontsize=60:fontcolor=${accentColor}:x=(w-text_w)/2:y=200:font=Arial-Bold,drawtext=text='${dialogue}':fontsize=48:fontcolor=${textColor}:x=(w-text_w)/2:y=(h-text_h)/2:font=Arial:textw=900,drawtext=text='Mr. WorldWideWebster':fontsize=36:fontcolor=#888888:x=(w-text_w)/2:y=h-150:font=Arial" ` +
          `-c:v libx264 -preset ultrafast -crf 28 "${sceneFile}"`;

        try {
          execSync(cmd, { timeout: 30000 });
          if (fs.existsSync(sceneFile)) {
            sceneFiles.push(sceneFile);
          }
        } catch (sceneError) {
          this.logger.warn(`Scene ${i} creation failed: ${sceneError.message}`);
        }
      }

      if (sceneFiles.length === 0) {
        // Ultimate fallback: single static image
        this.logger.warn('All scene creations failed — using single static video');
        const cmd = `ffmpeg -y -f lavfi -i "color=c=${bgColor}:s=1080x1920:d=${Math.max(totalDuration, 10)}:r=30" ` +
          `-vf "drawtext=text='${safeTitle}':fontsize=56:fontcolor=${textColor}:x=(w-text_w)/2:y=(h-text_h)/2:font=Arial:textw=900,drawtext=text='Follow Mr. WorldWideWebster':fontsize=36:fontcolor=#888888:x=(w-text_w)/2:y=h-150:font=Arial" ` +
          `-c:v libx264 -preset ultrafast -crf 28 "${videoFile}"`;
        execSync(cmd, { timeout: 30000 });
      } else {
        // Concatenate all scene videos
        const concatList = sceneFiles.map(f => `file '${f}'`).join('\n');
        const concatFile = `${basePath}_concat.txt`;
        fs.writeFileSync(concatFile, concatList);

        const cmd = `ffmpeg -y -f concat -safe 0 -i "${concatFile}" -c:v libx264 -preset ultrafast -crf 28 "${videoFile}"`;
        execSync(cmd, { timeout: 60000 });

        // Cleanup scene files
        for (const f of sceneFiles) {
          try { fs.unlinkSync(f); } catch {}
        }
        try { fs.unlinkSync(concatFile); } catch {}
      }

      if (fs.existsSync(videoFile)) {
        this.logger.success(`Text-overlay video created: ${videoFile}`);
        return videoFile;
      }
    } catch (error) {
      this.logger.error(`Text-overlay video creation failed: ${error.message}`);
    }
    return null;
  }

  /**
   * Generate visual assets for the explainer using FREE video clips
   * Searches Pexels/Pixabay/YouTube for relevant B-roll footage
   */
  async _generateVisuals(script, thing, ai, config, basePath) {
    const visuals = [];
    const assetsDir = path.join(config.paths.assets, `explain_${Date.now()}`);
    if (!fs.existsSync(assetsDir)) fs.mkdirSync(assetsDir, { recursive: true });

    this.logger.info(`Searching free video clips for: "${thing}"`);

    try {
      const { FreeVisualSearcher } = require('../hermes-agent/free-visual-searcher');
      const searcher = new FreeVisualSearcher();
      
      // Search for relevant free video clips based on the thing being explained
      const clips = await searcher.searchFreeVideoClips(thing, {
        maxResults: 3,
        maxDuration: 15,
        outputDir: assetsDir,
      });

      for (const clip of clips) {
        visuals.push(clip.file);
        this.logger.info(`  ✅ Added visual: ${clip.source} - ${clip.file}`);
      }

      await searcher.destroy();
    } catch (error) {
      this.logger.warn(`Could not find free visuals: ${error.message}`);
      
      // Fallback: create a simple text-based visual
      try {
        const { execSync } = require('child_process');
        // Generate a simple color frame with text using ffmpeg
        const fallbackPath = path.join(assetsDir, 'fallback_title.png');
        // We'll just note the fallback — the video compiler can add text overlays
        visuals.push(null);
      } catch (fallbackError) {
        this.logger.warn(`Fallback visual also failed: ${fallbackError.message}`);
      }
    }

    return visuals;
  }
}

module.exports = new ExplainPipeline();