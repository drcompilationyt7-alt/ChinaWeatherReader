/**
 * TTS Engine — Line-by-Line Audio Generation
 * 
 * Takes script lines from the storyboard and generates:
 * 1. Individual TTS audio files per line
 * 2. An audio duration manifest for the editor
 * 
 * Uses Edge-TTS (free, high quality, natural voices).
 * Voice: en-US-GuyNeural (male, natural, captivating, handsome)
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('TTSEngine');

const TTS_VOICE = process.env.EDGE_TTS_VOICE_EXPLAINER || 'en-US-GuyNeural';

/**
 * Generate TTS audio for a single line
 * @param {string} text - The text to speak
 * @param {string} outputPath - Where to save the .mp3
 * @returns {string|null} - Path to generated file, or null
 */
function generateLine(text, outputPath) {
  if (!text || text.length < 3) return null;

  try {
    const escapedText = text.replace(/"/g, '\\"').replace(/\n/g, ' ');
    execSync(
      `edge-tts --voice "${TTS_VOICE}" --text "${escapedText}" --write-media "${outputPath}" 2>/dev/null`,
      { timeout: 30000 }
    );

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 500) {
      return outputPath;
    }
  } catch (e) {
    logger.warn(`TTS failed for line: "${text.substring(0, 40)}..." — ${e.message.substring(0, 60)}`);
  }
  return null;
}

/**
 * Get audio duration using ffprobe
 * @param {string} audioPath - Path to audio file
 * @returns {number} - Duration in seconds
 */
function getDuration(audioPath) {
  try {
    const out = execSync(
      `ffprobe -i "${audioPath}" -show_entries format=duration -v quiet -of csv="p=0" 2>/dev/null`,
      { timeout: 5000, encoding: 'utf8' }
    ).trim();
    return parseFloat(out) || 0;
  } catch {
    return 0;
  }
}

/**
 * Generate TTS for all script lines
 * @param {Object[]} clips - Storyboard clips with .voiceover text
 * @param {string} outputDir - Directory to save audio files
 * @returns {Object[]} - Array of { clip_id, voiceover, audioFile, duration }
 */
async function generateAllLines(clips, outputDir) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });

  logger.info(`Generating TTS for ${clips.length} lines (voice: ${TTS_VOICE})`);

  const results = [];

  for (const clip of clips) {
    const { clip_id, voiceover } = clip;
    if (!voiceover || voiceover.length < 3) {
      logger.warn(`Clip ${clip_id}: Empty voiceover, skipping`);
      results.push({ clip_id, voiceover: '', audioFile: null, duration: 0 });
      continue;
    }

    const audioFile = path.join(outputDir, `tts_${String(clip_id).padStart(2, '0')}.mp3`);
    const result = generateLine(voiceover, audioFile);

    if (result) {
      const duration = getDuration(audioFile);
      logger.success(`  Clip ${clip_id}: "${voiceover.substring(0, 40)}..." → ${duration.toFixed(2)}s`);
      results.push({ clip_id, voiceover, audioFile, duration });
    } else {
      logger.warn(`  Clip ${clip_id}: TTS generation failed`);
      results.push({ clip_id, voiceover, audioFile: null, duration: 0 });
    }
  }

  const totalDuration = results.reduce((sum, r) => sum + r.duration, 0);
  logger.success(`TTS complete: ${results.filter(r => r.audioFile).length}/${clips.length} lines, ${totalDuration.toFixed(1)}s total`);

  return results;
}

module.exports = { generateAllLines, generateLine, getDuration };