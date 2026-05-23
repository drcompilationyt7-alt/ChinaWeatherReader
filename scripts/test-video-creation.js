#!/usr/bin/env node

/**
 * Local test: Verify the explain pipeline produces a video file.
 * Tests the full path: script generation → TTS → video compilation.
 * No API keys needed — uses text-overlay fallback when TTS unavailable.
 */

const path = require('path');
const fs = require('fs');

// Minimal mock AI service
class MockAIService {
  constructor() {
    this.tts = null; // Simulate no TTS available
  }

  async chatJSON(systemPrompt, userMessage) {
    // Return a mock script without needing an API key
    return {
      title: 'What is this dance? (Egypt edition) 🌍',
      estimatedDuration: 30,
      scenes: [
        {
          sceneNumber: 1,
          duration: 5,
          visualDescription: 'Close up of Egyptian dance',
          voice: 'curious',
          dialogue: 'What is this... Egyptian dance?',
        },
        {
          sceneNumber: 2,
          duration: 8,
          visualDescription: 'Wider shot of dancers',
          voice: 'explainer',
          dialogue: 'This is Egyptian folk dance, a tradition dating back thousands of years.',
        },
        {
          sceneNumber: 3,
          duration: 7,
          visualDescription: 'Details of footwork',
          voice: 'curious',
          dialogue: 'That is incredible! Tell me more.',
        },
        {
          sceneNumber: 4,
          duration: 5,
          visualDescription: 'Group celebration',
          voice: 'explainer',
          dialogue: 'It is performed at weddings and festivals across Egypt.',
        },
        {
          sceneNumber: 5,
          duration: 5,
          visualDescription: 'Beautiful sunset over pyramids',
          voice: 'explainer',
          dialogue: 'To put it in perspective, it is as iconic as the pyramids themselves.',
        },
      ],
      fullScript: 'Mock script for testing',
    };
  }

  async textToSpeech(text, outputPath, options = {}) {
    // Simulate TTS unavailable — write .txt placeholder
    const txtPath = outputPath + '.txt';
    fs.writeFileSync(txtPath, text);
    return txtPath;
  }
}

async function main() {
  console.log('═══════════════════════════════════════════');
  console.log('  TEST: Explain Pipeline Video Creation');
  console.log('═══════════════════════════════════════════\n');

  const outputDir = path.join(__dirname, '..', 'output', 'test');
  if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
  }

  const ai = new MockAIService();
  const explainPipeline = require('../explainer/explain-pipeline');

  const params = {
    sourceContent: {
      title: 'Egyptian Folk Dance',
      platform: 'test',
      description: 'Traditional Egyptian dance',
      duration: 30,
      hasSpeech: false,
      isVisual: true,
      languageDetected: 'english',
    },
    explainThing: 'Egyptian dance',
    explainCategory: 'dance',
    decision: {
      path: 'explain',
      confidence: 90,
      reasoning: 'Test',
    },
    outputDir,
    ai,
    config: {
      paths: {
        scripts: path.join(outputDir, 'scripts'),
        assets: path.join(outputDir, 'assets'),
      },
    },
  };

  console.log('Running explain pipeline...\n');

  try {
    const result = await explainPipeline.processExplain(params);

    console.log('\n═══════════════════════════════════════════');
    console.log('  RESULT');
    console.log('═══════════════════════════════════════════');
    console.log(`Title: ${result.title}`);
    console.log(`Video file: ${result.videoFile}`);
    console.log(`Script path: ${result.scriptPath}`);

    if (result.videoFile && fs.existsSync(result.videoFile)) {
      const stats = fs.statSync(result.videoFile);
      console.log(`File size: ${(stats.size / 1024).toFixed(1)} KB`);
      console.log('\n✅ SUCCESS: Video file was created!');
      console.log(`\nOpen it: ${result.videoFile}`);
    } else {
      console.log('\n❌ FAIL: No video file produced');
      console.log('videoFile value:', result.videoFile);
      process.exit(1);
    }
  } catch (error) {
    console.error('\n❌ FAIL:', error.message);
    console.error(error.stack);
    process.exit(1);
  }
}

main();