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
  }

  async _checkAvailability() {
    try {
      const { stdout } = await execAsync('edge-tts --help 2>&1');
      this.available = stdout.toLowerCase().includes('usage') || stdout.toLowerCase().includes('edge-tts');
    } catch {
      // Try pip-based edge-tts
      try {
        await execAsync('python3 -m edge_tts --help 2>&1');
        this.available = true;
      } catch {
        this.logger.warn('edge-tts not found. Install with: pip install edge-tts');
        this.available = false;
      }
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
    const rate = options.rate || '+0%';
    const pitch = options.pitch || '+0Hz';

    try {
      // Try using the edge-tts Python package (most reliable)
      const escapedText = text.replace(/"/g, '\\"').replace(/'/g, "\\'");
      const cmd = `edge-tts --voice "${voice}" --rate "${rate}" --pitch "${pitch}" --text "${escapedText}" --write-media "${outputPath}" 2>&1`;

      await execAsync(cmd, { timeout: 60000, maxBuffer: 10 * 1024 * 1024 });
      
      if (fs.existsSync(outputPath)) {
        this.logger.info(`Edge-TTS generated: ${path.basename(outputPath)}`);
        return outputPath;
      }

      throw new Error('Output file not created');
    } catch (error) {
      this.logger.warn(`Edge-TTS failed (${error.message}), trying fallback...`);
      return await this._fallbackTTS(text, outputPath, voice);
    }
  }

  /**
   * Fallback to Node.js say.js or simple method
   */
  async _fallbackTTS(text, outputPath, voice) {
    try {
      // Try Node.js say package
      const say = require('say');
      return new Promise((resolve, reject) => {
        say.export(text, voice || 'Microsoft Zira Desktop', 1, outputPath, (err) => {
          if (err) {
            // Last resort: create a text file noting TTS wasn't available
            this.logger.warn('All TTS methods failed, creating placeholder');
            fs.writeFileSync(outputPath + '.txt', text);
            resolve(outputPath + '.txt');
          } else {
            resolve(outputPath);
          }
        });
      });
    } catch {
      // Absolute last resort
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
      'curious': 'en-US-JennyNeural',  // Female, higher pitch
      'explainer': 'en-US-GuyNeural',  // Male, lower voice
    };

    const pitchMap = {
      'curious': '+30Hz',   // Higher pitch for curious
      'explainer': '-10Hz', // Deeper for explainer
    };

    const files = [];
    for (let i = 0; i < scenes.length; i++) {
      const scene = scenes[i];
      const voiceType = scene.voice || 'explainer';
      const voice = voiceMap[voiceType] || 'en-US-JennyNeural';
      const pitch = pitchMap[voiceType] || '+0Hz';
      const outputFile = path.join(outputDir, `scene_${String(i + 1).padStart(2, '0')}_${voiceType}.mp3`);

      try {
        await this.textToSpeech(scene.dialogue, outputFile, { 
          voice, 
          pitch,
          rate: voiceType === 'curious' ? '+10%' : '+0%',
        });
        files.push({
          scene: i + 1,
          voice: voiceType,
          file: outputFile,
          dialogue: scene.dialogue,
        });
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
      const { stdout } = await execAsync('edge-tts --list-voices 2>&1');
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