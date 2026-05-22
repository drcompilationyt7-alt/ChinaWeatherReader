/**
 * Mr. WorldWideWebster — Edge-TTS Provider
 * 
 * Completely FREE text-to-speech using Microsoft Edge's built-in speech engine.
 * Works on Windows without any API keys.
 * Supports 100+ languages with natural-sounding voices.
 * 
 * Uses the edge-tts npm package which calls Microsoft Edge's online TTS API
 * (free, no account needed).
 * 
 * Voices available:
 * - en-US-JennyNeural (female, US English) — default
 * - en-US-GuyNeural (male, US English)
 * - en-GB-SoniaNeural (female, UK English)
 * - zh-CN-XiaoxiaoNeural (female, Chinese)
 * - ja-JP-NanamiNeural (female, Japanese)
 * - ko-KR-SunHiNeural (female, Korean)
 * - fr-FR-DeniseNeural (female, French)
 */
const { exec } = require('child_process');
const { promisify } = require('util');
const path = require('path');
const fs = require('fs');
const { Logger } = require('../core/logger');

const execAsync = promisify(exec);

class EdgeTTSProvider {
  constructor() {
    this.logger = new Logger('EdgeTTS');
    this.available = true; // Assume available, check lazily on first use
    this.useSayFallback = false; // Windows-native TTS fallback
  }

  async _checkAvailability() {
    // Try python3 -m edge_tts first (works even when CLI isn't on PATH)
    try {
      const { stdout } = await execAsync('python3 -m edge_tts --help', { timeout: 10000 });
      if (stdout.toLowerCase().includes('usage') || stdout.toLowerCase().includes('edge-tts')) {
        this.available = true;
        this.logger.info('Edge-TTS available via python3');
        return;
      }
    } catch {
      this.logger.info('Python edge-tts not found via python3');
    }
    // Fallback: try bare edge-tts CLI
    try {
      const { stdout } = await execAsync('edge-tts --help', { timeout: 10000 });
      if (stdout.toLowerCase().includes('usage') || stdout.toLowerCase().includes('edge-tts')) {
        this.available = true;
        this.logger.info('Edge-TTS available via edge-tts CLI');
        return;
      }
    } catch {
      this.logger.info('edge-tts CLI not found');
    }
    // Fallback: try Node.js say module (works natively on Windows)
    try {
      require.resolve('say');
      this.available = true;
      this.useSayFallback = true;
      this.logger.info('Using Node.js say.js for TTS (Windows native fallback)');
    } catch {
      this.logger.warn('No TTS available. Install edge-tts (pip install edge-tts) or say.js (npm install say)');
      this.available = false;
    }
  }

  async _ensureAvailable() {
    if (!this.available) {
      await this._checkAvailability();
    }
  }

  isAvailable() {
    return this.available;
  }

  /**
   * Generate speech from text using Edge-TTS
   * @param {string} text — The text to speak
   * @param {string} outputPath — Where to save the audio file
   * @param {Object} options — { voice, rate, pitch }
   * @returns {Promise<string>} — Path to the generated audio file
   */
  async textToSpeech(text, outputPath, options = {}) {
    await this._ensureAvailable();
    const voice = options.voice || 'en-US-JennyNeural';

    // Try python3 edge-tts first
    try {
      const escapedText = text.replace(/"/g, '\\"').replace(/'/g, "\\'");
      const cmd = `python3 -m edge_tts --voice "${voice}" --text "${escapedText}" --write-media "${outputPath}" 2>&1`;
      await execAsync(cmd, { timeout: 60000, maxBuffer: 10 * 1024 * 1024 });
      if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100) {
        this.logger.info(`Edge-TTS (python3): ${path.basename(outputPath)}`);
        return outputPath;
      }
      throw new Error('Output file not created or empty');
    } catch (error) {
      this.logger.info(`Python edge-tts failed: ${error.message.substring(0, 60)}`);
    }

    // Fallback: try bare edge-tts CLI
    try {
      const escapedText = text.replace(/"/g, '\\"').replace(/'/g, "\\'");
      const cmd = `edge-tts --voice "${voice}" --text "${escapedText}" --write-media "${outputPath}" 2>&1`;
      await execAsync(cmd, { timeout: 60000, maxBuffer: 10 * 1024 * 1024 });
      if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100) {
        this.logger.info(`Edge-TTS (CLI): ${path.basename(outputPath)}`);
        return outputPath;
      }
    } catch (cliError) {
      this.logger.info(`CLI edge-tts failed: ${cliError.message.substring(0, 60)}`);
    }

    // Fallback: Node.js say.js (works natively on Windows without Python)
    if (this.useSayFallback) {
      try {
        const say = require('say');
        const voiceName = voice === 'en-US-JennyNeural' ? 'Microsoft Zira Desktop' : 'Microsoft David Desktop';
        return await new Promise((resolve, reject) => {
          say.export(text, voiceName, 1, outputPath, (err) => {
            if (!err && fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100) {
              this.logger.info(`say.js TTS: ${path.basename(outputPath)}`);
              resolve(outputPath);
            } else {
              reject(err || new Error('say.js output empty'));
            }
          });
        });
      } catch (sayError) {
        this.logger.info(`say.js failed: ${sayError.message.substring(0, 60)}`);
      }
    }

    // Last resort: write text placeholder so pipeline can still proceed
    this.logger.warn('All TTS methods failed — writing text placeholder');
    const placeholderPath = outputPath + '.txt';
    fs.writeFileSync(placeholderPath, text);
    return placeholderPath;
  }

  /**
   * Fallback to Node.js say.js or simple method
   */
  async _fallbackTTS(text, outputPath, voice) {
    try {
      const say = require('say');
      return new Promise((resolve, reject) => {
        say.export(text, voice || 'Microsoft Zira Desktop', 1, outputPath, (err) => {
          if (err) {
            fs.writeFileSync(outputPath + '.txt', text);
            resolve(outputPath + '.txt');
          } else {
            resolve(outputPath);
          }
        });
      });
    } catch {
      fs.writeFileSync(outputPath + '.txt', text);
      return outputPath + '.txt';
    }
  }

  /**
   * Generate two-voice dialog (for "What is this?" format)
   * @param {Array} scenes — [{ voice: 'curious'|'explainer', dialogue: string, duration: number }]
   * @param {string} outputDir
   * @returns {Object} — { files: Array, fullMix: string }
   */
  async generateDialogAudio(scenes, outputDir) {
    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }

    const voiceMap = {
      'curious': 'en-US-JennyNeural',
      'explainer': 'en-US-GuyNeural',
    };

    const files = [];
    for (let i = 0; i < scenes.length; i++) {
      const scene = scenes[i];
      const voiceType = scene.voice || 'explainer';
      const voice = voiceMap[voiceType] || 'en-US-JennyNeural';
      const outputFile = path.join(outputDir, `scene_${String(i + 1).padStart(2, '0')}_${voiceType}.mp3`);

      try {
        const result = await this.textToSpeech(scene.dialogue, outputFile, { voice });
        // Only add if it's a real audio file (not a .txt placeholder)
        if (result.endsWith('.mp3')) {
          files.push({
            scene: i + 1,
            voice: voiceType,
            file: outputFile,
            dialogue: scene.dialogue,
          });
        }
      } catch (error) {
        this.logger.error(`Failed to generate scene ${i + 1}: ${error.message}`);
      }
    }

    return files;
  }

  /**
   * List available voices
   */
  async listVoices() {
    try {
      const { stdout } = await execAsync('python3 -m edge_tts --list-voices 2>&1');
      return stdout.split('\n')
        .filter(line => line.includes('en-') || line.includes('zh-') || line.includes('ja-') || line.includes('ko-') || line.includes('fr-'))
        .map(line => {
          const parts = line.trim().split(/\s+/);
          return { name: parts[0], gender: parts[1] || 'unknown', locale: parts[2] || 'unknown' };
        });
    } catch {
      return [
        { name: 'en-US-JennyNeural', gender: 'Female', locale: 'en-US' },
        { name: 'en-US-GuyNeural', gender: 'Male', locale: 'en-US' },
      ];
    }
  }
}

module.exports = { EdgeTTSProvider };