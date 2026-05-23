/**
 * EdgeTTSProvider.js
 * Robust FREE TTS provider for Mr. WorldWideWebster
 * 
 * Uses Microsoft Edge's TTS engine via edge-tts Python package.
 * No API keys needed — completely free.
 * 
 * Fixes:
 * - Mapped 'onyx'/'nova' voice names to valid Edge-TTS voices
 * - Validates output file exists and isn't empty
 * - Handles Windows (py) vs Linux (python3) python commands
 */
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('../core/logger');

class EdgeTTSProvider {
  constructor() {
    this.logger = new Logger('EdgeTTS');
    this.available = null;
  }

  async _ensureAvailable() {
    if (this.available !== null) {
      return this.available;
    }
    return await this.checkAvailability();
  }

  async checkAvailability() {
    if (this.available !== null) {
      return this.available;
    }

    try {
      const python = process.platform === 'win32' ? 'py' : 'python3';
      const result = await this._runCommand(python, ['-m', 'edge_tts', '--help'], 10000);
      this.available = result.success;

      if (result.success) {
        this.logger.info('Edge-TTS detected');
      } else {
        this.logger.warn('edge-tts missing. Install with: pip install edge-tts');
      }

      return this.available;
    } catch {
      this.available = false;
      return false;
    }
  }

  isAvailable() {
    return this.available === true;
  }

  _runCommand(command, args, timeout = 60000) {
    return new Promise((resolve) => {
      const child = spawn(command, args, { shell: false });

      let stdout = '';
      let stderr = '';
      child.stdout.on('data', d => stdout += d.toString());
      child.stderr.on('data', d => stderr += d.toString());

      const timer = setTimeout(() => {
        child.kill();
        resolve({ success: false, stderr: 'Timeout' });
      }, timeout);

      child.on('close', code => {
        clearTimeout(timer);
        resolve({ success: code === 0, stdout, stderr });
      });
    });
  }

  async textToSpeech(text, outputPath, options = {}) {
    const available = await this.checkAvailability();
    if (!available) {
      throw new Error('Edge-TTS unavailable. Install with: pip install edge-tts');
    }

    // Map common voice names to valid Edge-TTS voices
    const voiceMap = {
      curious: 'en-US-JennyNeural',
      explainer: 'en-US-GuyNeural',
      nova: 'en-US-JennyNeural',
      onyx: 'en-US-GuyNeural',
      encore: 'en-US-AriaNeural',
      fable: 'en-GB-SoniaNeural',
    };

    const voice = voiceMap[options.voice] || options.voice || 'en-US-JennyNeural';

    this.logger.info(`Generating TTS with voice: ${voice}`);

    const python = process.platform === 'win32' ? 'py' : 'python3';

    const result = await this._runCommand(python, [
      '-m', 'edge_tts',
      '--voice', voice,
      '--text', text,
      '--write-media', outputPath,
    ], 120000);

    if (!result.success) {
      throw new Error(result.stderr || 'Edge-TTS failed');
    }

    if (!fs.existsSync(outputPath)) {
      throw new Error('Audio file missing after TTS generation');
    }

    const size = fs.statSync(outputPath).size;
    if (size < 1000) {
      throw new Error(`Audio file too small (${size} bytes) — TTS likely failed`);
    }

    this.logger.info(`Audio generated: ${path.basename(outputPath)} (${size} bytes)`);
    return outputPath;
  }

  /**
   * Generate two-voice dialog (for "What is this?" format)
   */
  async generateDialogAudio(scenes, outputDir) {
    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }

    const files = [];
    for (const scene of scenes) {
      const voiceType = scene.voice === 'curious' ? 'curious' : 'explainer';
      const outputFile = path.join(outputDir, `scene_${scene.sceneNumber}.mp3`);
      try {
        await this.textToSpeech(scene.dialogue, outputFile, { voice: voiceType });
        files.push({ scene: scene.sceneNumber, file: outputFile, dialogue: scene.dialogue });
      } catch (error) {
        this.logger.warn(`Scene ${scene.sceneNumber} TTS failed: ${error.message}`);
        // Write text placeholder so pipeline can continue
        const txtFile = outputFile + '.txt';
        fs.writeFileSync(txtFile, scene.dialogue, 'utf8');
        files.push({ scene: scene.sceneNumber, file: txtFile, dialogue: scene.dialogue });
      }
    }
    return files;
  }
}

module.exports = { EdgeTTSProvider };