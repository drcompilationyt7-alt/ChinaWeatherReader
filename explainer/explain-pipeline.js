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
 * 
 * FIXES APPLIED:
 * - FFmpeg 6.x: removed invalid 'textw=900' drawtext option (causes "Option not found")
 * - FFmpeg: use textfile= instead of inline text= to avoid escaping issues ("No such filter")
 * - Added fallback chain for scene creation
 * - Multi-key OpenRouter API support
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
    const { sourceContent, explainThing, explainCategory, decision, outputDir, ai, config, hermesAgent } = params;
    const safeId = Date.now();
    const basePath = path.join(outputDir, `explain_${safeId}`);

    this.logger.info(`Creating "What is this?" explainer for: ${explainThing} (${explainCategory})`);

    // Step 1: Generate the script
    const script = await this._generateExplainScript(sourceContent, explainThing, explainCategory, ai);
    this.logger.info(`Script generated: ${script.title}`);

    // Step 2: Save script
    const scriptsDir = config.paths.scripts;
    if (!fs.existsSync(scriptsDir)) {
      fs.mkdirSync(scriptsDir, { recursive: true });
    }
    const scriptPath = path.join(scriptsDir, `explain_${safeId}_script.txt`);
    fs.writeFileSync(scriptPath, script.fullScript);
    this.logger.info(`Script saved to: ${scriptPath}`);

    // Step 3: Generate two-voice audio
    const audioResult = await this._generateTwoVoiceAudio(script, ai, config, basePath);
    this.logger.info(`Two-voice audio generated`);

    // Step 4: Generate visual assets - use Hermes to find/download matching video if URL provided
    let visuals = [];
    let downloadedFromHermes = false;
    
    // First try: Download from provided URL
    if (sourceContent.url && hermesAgent) {
      this.logger.info(`Hermes found URL: ${sourceContent.url}, downloading...`);
      try {
        const { UniversalDownloader } = require('../sourcing/universal-downloader');
        const downloader = new UniversalDownloader();
        
        const downloadResult = await downloader.download(sourceContent.url, {
          outputDir: path.join(config.paths.assets, `explain_${safeId}`),
          maxHeight: 720,
        });
        
        if (downloadResult && downloadResult.filePath) {
          visuals.push(downloadResult.filePath);
          downloadedFromHermes = true;
          this.logger.success(`✅ Downloaded video from Hermes: ${downloadResult.filePath}`);
        }
      } catch (error) {
        this.logger.warn(`Failed to download Hermes URL: ${error.message}`);
      }
    }
    
    // Second try: If no URL provided but we have hermesAgent, ask Hermes to find matching content
    if (!downloadedFromHermes && hermesAgent) {
      this.logger.info('Asking Hermes to find matching viral content for the script topic...');
      try {
        const hermesTask = `Find a viral video URL about: ${explainThing}
        
        Search platforms like Bilibili, TikTok, YouTube, Douyin.
        Find ONE specific video URL that matches this topic.
        Return ONLY the raw URL, nothing else.
        
        Find URL now:`;
        
        const hermesResult = await hermesAgent.run(hermesTask, { verbose: false, maxSteps: 2 });
        
        // Log full Hermes output for debugging
        const hermesOutput = hermesResult?.output || '';
        this.logger.info('═══════════════════════════════════════════');
        this.logger.info('🤖 HERMES RAW OUTPUT (Explain Pipeline Content Search):');
        this.logger.info('═══════════════════════════════════════════');
        console.log(hermesOutput || '[No output]');
        this.logger.info('═══════════════════════════════════════════');
        
        // Save to temp file for debugging
        const fs = require('fs');
        const debugPath = path.join(config.paths.temp, `hermes_explain_debug_${Date.now()}.txt`);
        try {
          fs.mkdirSync(config.paths.temp, { recursive: true });
          fs.writeFileSync(debugPath, hermesOutput);
          this.logger.info(`💾 Full output saved to: ${debugPath}`);
        } catch (e) {
          this.logger.warn(`Could not save debug file: ${e.message}`);
        }
        
        if (hermesResult && hermesResult.output) {
          // Extract URL using regex
          const urlRegex = /(https?:\/\/[^\s<>"{}|\\^`\[\]]+)/gi;
          const matches = hermesResult.output.match(urlRegex);
          
          if (matches && matches.length > 0) {
            const foundUrl = matches[0];
            this.logger.info(`Hermes found matching URL: ${foundUrl}`);
            
            const { UniversalDownloader } = require('../sourcing/universal-downloader');
            const downloader = new UniversalDownloader();
            
            const downloadResult = await downloader.download(foundUrl, {
              outputDir: path.join(config.paths.assets, `explain_${safeId}`),
              maxHeight: 720,
            });
            
            if (downloadResult && downloadResult.filePath) {
              visuals.push(downloadResult.filePath);
              downloadedFromHermes = true;
              this.logger.success(`✅ Downloaded matching video from Hermes search: ${downloadResult.filePath}`);
            }
          }
        }
      } catch (error) {
        this.logger.warn(`Hermes content search failed: ${error.message}`);
      }
    }
    
    // Fallback: Generate/find free visuals
    if (!downloadedFromHermes) {
      this.logger.info('Using fallback visual generation/search...');
      visuals = await this._generateVisuals(script, explainThing, ai, config, basePath);
    }


    // Step 5: Compile the final video
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
        sourceUrl: sourceContent.url,
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
  "title": "What is this [thing]? \u{1F30D}",
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

      // Generate plain text version of the full script
      let fullScript = `"WHAT IS THIS?" EXPLAINER\n`;
      fullScript += `Title: ${scriptData.title}\n`;
      fullScript += `Thing: ${thing}\n`;
      fullScript += `Category: ${category}\n`;
      fullScript += `Duration: ~${scriptData.estimatedDuration}s\n`;
      fullScript += `-`.repeat(50) + '\n\n';
      
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
        title: `What is this ${thing}? \u{1F30D}`,
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
      const voice = voiceType === 'curious' ? 'nova' : 'onyx';
      const outputFile = path.join(audioDir, `scene_${String(scene.sceneNumber).padStart(2, '0')}_${voiceType}.mp3`);

      try {
        const result = await ai.textToSpeech(scene.dialogue, outputFile, { voice });
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
   * Falls back to text-overlay video if no audio is available
   */
  async _compileVideo(audioResult, visuals, basePath, videoFile, script) {
    const allAudio = [
      ...(audioResult?.curious || []),
      ...(audioResult?.explainer || []),
    ];

    if (allAudio.length === 0) {
      this.logger.warn('No audio files — creating text-overlay video instead');
      return await this._createTextOverlayVideo(script, basePath, videoFile);
    }

    try {
      const { execSync } = require('child_process');

      const sorted = [...allAudio].sort((a, b) => a.scene - b.scene);
      const totalInputs = sorted.filter(a => a.file && fs.existsSync(a.file));

      if (totalInputs.length === 0) {
        this.logger.warn('No valid audio files exist — skipping video');
        return null;
      }

      const totalDuration = sorted.reduce((sum, s) => sum + (s.duration || 5), 0);
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
   * Create a text-overlay video when TTS is unavailable.
   * Uses ffmpeg drawtext with textfile= to avoid escaping issues.
   * 
   * CRITICAL FIX: FFmpeg 6.x drawtext does NOT support 'textw' parameter.
   * Using textfile= instead of inline text= to prevent "No such filter" errors
   * caused by special characters (colons, apostrophes) in dialogue text.
   */
  async _createTextOverlayVideo(script, basePath, videoFile) {
    try {
      const { execSync } = require('child_process');

      const scenes = script.scenes || [];
      const totalDuration = scenes.reduce((sum, s) => sum + (s.duration || 5), 0);

      const bgColor = '#1a1a2e';
      const textColor = '#ffffff';
      const accentColor = '#e94560';

      // Create individual scene videos
      const sceneFiles = [];
      for (let i = 0; i < scenes.length; i++) {
        const scene = scenes[i];
        const sceneDuration = scene.duration || 5;
        const sceneFile = `${basePath}_scene_${i}.mp4`;

        // Use textfile= instead of text= to avoid FFmpeg escaping nightmare
        const dialogue = scene.dialogue || '';
        const voiceLabel = (scene.voice || 'explainer').toUpperCase();

        const textFile = `${basePath}_scene_${i}_text.txt`;
        const voiceLabelFile = `${basePath}_scene_${i}_label.txt`;
        const channelFile = `${basePath}_scene_${i}_channel.txt`;

        fs.writeFileSync(textFile, dialogue, 'utf8');
        fs.writeFileSync(voiceLabelFile, voiceLabel, 'utf8');
        fs.writeFileSync(channelFile, 'Mr. WorldWideWebster', 'utf8');

        // Build filter chain using textfile= instead of text=
        // NOTE: textw is NOT a valid drawtext option in FFmpeg 6.x - do NOT use it
        const toPath = p => p.replace(/\\/g, '/');
        const filterParts = [
          `drawtext=textfile='${toPath(voiceLabelFile)}':fontsize=60:fontcolor=${accentColor}:x=(w-text_w)/2:y=200:font=Arial-Bold`,
          `drawtext=textfile='${toPath(textFile)}':fontsize=48:fontcolor=${textColor}:x=(w-text_w)/2:y=(h-text_h)/2:font=Arial:line_spacing=10`,
          `drawtext=textfile='${toPath(channelFile)}':fontsize=36:fontcolor=#888888:x=(w-text_w)/2:y=h-150:font=Arial`
        ];
        const filterComplex = filterParts.join(',');

        const cmd = [
          'ffmpeg -y',
          `-f lavfi -i "color=c=${bgColor}:s=1080x1920:d=${sceneDuration}:r=30"`,
          `-vf "${filterComplex}"`,
          '-c:v libx264 -preset ultrafast -crf 28',
          `"${sceneFile}"`
        ].join(' ');

        try {
          execSync(cmd, { timeout: 30000, maxBuffer: 50 * 1024 * 1024 });
          if (fs.existsSync(sceneFile)) {
            sceneFiles.push(sceneFile);
          }
        } catch (sceneError) {
          this.logger.warn(`Scene ${i} creation failed (trying simplified): ${sceneError.message}`);

          // Fallback: just voice label with inline text (minimal content)
          try {
            const simpleCmd = [
              'ffmpeg -y',
              `-f lavfi -i "color=c=${bgColor}:s=1080x1920:d=${sceneDuration}:r=30"`,
              `-vf "drawtext=text='${voiceLabel}':fontsize=60:fontcolor=${accentColor}:x=(w-text_w)/2:y=200:font=Arial"`,
              '-c:v libx264 -preset ultrafast -crf 28',
              `"${sceneFile}"`
            ].join(' ');
            execSync(simpleCmd, { timeout: 30000 });
            if (fs.existsSync(sceneFile)) {
              sceneFiles.push(sceneFile);
            }
          } catch (fallbackError) {
            this.logger.warn(`Scene ${i} fallback also failed: ${fallbackError.message}`);
          }
        } finally {
          // Clean up text files
          for (const f of [textFile, voiceLabelFile, channelFile]) {
            try { fs.unlinkSync(f); } catch {}
          }
        }
      }

      if (sceneFiles.length === 0) {
        // Ultimate fallback: single static image with just title
        this.logger.warn('All scene creations failed — using single static video');
        const title = (script.title || 'Mr. WorldWideWebster').replace(/[^a-zA-Z0-9 ]/g, '');
        const titleFile = `${basePath}_fallback_title.txt`;
        fs.writeFileSync(titleFile, title, 'utf8');

        const cmd = [
          'ffmpeg -y',
          `-f lavfi -i "color=c=${bgColor}:s=1080x1920:d=${Math.max(totalDuration, 10)}:r=30"`,
          `-vf "drawtext=textfile='${titleFile.replace(/\\/g, '/')}':fontsize=56:fontcolor=${textColor}:x=(w-text_w)/2:y=(h-text_h)/2:font=Arial"`,
          '-c:v libx264 -preset ultrafast -crf 28',
          `"${videoFile}"`
        ].join(' ');
        execSync(cmd, { timeout: 30000 });
        try { fs.unlinkSync(titleFile); } catch {}
      } else {
        // Concatenate all scene videos
        const concatList = sceneFiles.map(f => `file '${f.replace(/'/g, "'\\''")}'`).join('\n');
        const concatFile = `${basePath}_concat.txt`;
        fs.writeFileSync(concatFile, concatList);

        const cmd = [
          'ffmpeg -y',
          `-f concat -safe 0 -i "${concatFile}"`,
          '-c:v libx264 -preset ultrafast -crf 28',
          `"${videoFile}"`
        ].join(' ');
        execSync(cmd, { timeout: 60000, maxBuffer: 50 * 1024 * 1024 });

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
   */
  async _generateVisuals(script, thing, ai, config, basePath) {
    const visuals = [];
    const assetsDir = path.join(config.paths.assets, `explain_${Date.now()}`);
    if (!fs.existsSync(assetsDir)) fs.mkdirSync(assetsDir, { recursive: true });

    this.logger.info(`Searching free video clips for: "${thing}"`);

    try {
      const { FreeVisualSearcher } = require('../hermes-agent/free-visual-searcher');
      const searcher = new FreeVisualSearcher();
      
      const clips = await searcher.searchFreeVideoClips(thing, {
        maxResults: 3,
        maxDuration: 15,
        outputDir: assetsDir,
      });

      for (const clip of clips) {
        visuals.push(clip.file);
        this.logger.info(`  Added visual: ${clip.source} - ${clip.file}`);
      }

      await searcher.destroy();
    } catch (error) {
      this.logger.warn(`Could not find free visuals: ${error.message}`);
      visuals.push(null);
    }

    return visuals;
  }
}

module.exports = new ExplainPipeline();