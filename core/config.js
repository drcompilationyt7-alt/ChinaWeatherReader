/**
 * Mr. WorldWideWebster - Configuration Loader
 * Loads environment variables and provides typed access to config
 * Supports OpenRouter (primary), OpenAI (fallback), and Gemini
 * 
 * Multi-Key Support: Set OPENROUTER_API_KEY_2 through OPENROUTER_API_KEY_8
 * as GitHub Secrets for automatic key rotation when one hits rate/credit limits.
 * 
 * TTS: Default voice is en-US-AvaMultilingualNeural (cute, soft, human-like).
 */
require('dotenv').config();
const path = require('path');

const config = {
  provider: {
    name: process.env.AI_PROVIDER || 'gemini',
  },

  // ─── Gemini (Primary AI Brain — FREE) ──────────────────────────────
  gemini: {
    // Up to 8 API keys for rotation (all free from Google AI Studio)
    apiKeys: [
      process.env.GEMINI_API_KEY,
      process.env.GEMINI_API_KEY_2,
      process.env.GEMINI_API_KEY_3,
      process.env.GEMINI_API_KEY_4,
      process.env.GEMINI_API_KEY_5,
      process.env.GEMINI_API_KEY_6,
      process.env.GEMINI_API_KEY_7,
      process.env.GEMINI_API_KEY_8,
    ].filter(Boolean),
    model: process.env.GEMINI_MODEL || 'gemini-2.5-flash',
  },

  // ─── Legacy providers (kept for backward compatibility) ────────────
  openrouter: {
    apiKey: process.env.OPENROUTER_API_KEY,
    defaultModel: process.env.OPENROUTER_DEFAULT_MODEL || 'openrouter/owl-alpha',
  },

  openai: {
    apiKey: process.env.OPENAI_API_KEY,
    model: process.env.OPENAI_MODEL || 'gpt-4o-mini',
  },

  tts: {
    provider: process.env.TTS_PROVIDER || 'edge',
    edgeVoiceCurious: process.env.EDGE_TTS_VOICE_CURIOUS || 'en-US-AvaMultilingualNeural',
    edgeVoiceExplainer: process.env.EDGE_TTS_VOICE_EXPLAINER || 'en-US-GuyNeural',
    edgeVoiceNews: process.env.EDGE_TTS_VOICE_NEWS || 'en-GB-SoniaNeural',
    elevenLabsKey: process.env.ELEVENLABS_API_KEY,
    elevenLabsVoiceCurious: process.env.ELEVENLABS_VOICE_CURIOUS || '21m00Tcm4TlvDq8ikWAM',
    elevenLabsVoiceExplainer: process.env.ELEVENLABS_VOICE_EXPLAINER || 'TxGEqnHWrfWFTfGW9XjX',
  },

  discord: {
    botToken: process.env.DISCORD_BOT_TOKEN,
    channelId: process.env.DISCORD_CHANNEL_ID,
    userId: process.env.DISCORD_USER_ID,
  },

  boost: {
    enabled: process.env.BOOST_ENABLED === 'true',
    maxViews: parseInt(process.env.BOOST_MAX_VIEWS) || 100,
    minViews: parseInt(process.env.BOOST_MIN_VIEWS) || 50,
    minWatchSec: parseInt(process.env.BOOST_MIN_WATCH_SEC) || 30,
    maxWatchSec: parseInt(process.env.BOOST_MAX_WATCH_SEC) || 90,
    spreadMinMinutes: parseInt(process.env.BOOST_SPREAD_MIN) || 15,
    spreadMaxMinutes: parseInt(process.env.BOOST_SPREAD_MAX) || 45,
  },

  youtube: {
    apiKey: process.env.YOUTUBE_API_KEY,
    clientId: process.env.YOUTUBE_CLIENT_ID,
    clientSecret: process.env.YOUTUBE_CLIENT_SECRET,
    region: process.env.YOUTUBE_REGION || 'US',
    privacyStatus: process.env.DEFAULT_PRIVACY_STATUS || 'public',
    maxUploadsPerDay: parseInt(process.env.MAX_UPLOADS_PER_DAY) || 5,
  },

  channel: {
    name: process.env.CHANNEL_NAME || 'Mr. WorldWideWebster',
    tagline: process.env.CHANNEL_TAGLINE || 'Bringing the world to you',
    targetAudience: process.env.TARGET_AUDIENCE || 'Global culture enthusiasts, travelers, people curious about international trends',
  },

  sourcing: {
    enabledSources: (process.env.ENABLED_SOURCES || 'bilibili,tiktok,news').split(',').map(s => s.trim()),
    schedule: process.env.SOURCE_SCHEDULE || '0 */6 * * *',
  },

  // ─── Country rotation ─────────────────────────────────────────────
  countries: [
    'China', 'Japan', 'South Korea', 'Thailand', 'Vietnam',
    'India', 'Indonesia', 'Brazil', 'Mexico', 'France',
    'Germany', 'Italy', 'Spain', 'UK', 'Egypt',
    'Nigeria', 'Australia', 'Global'
  ],

  // ─── Pipeline settings ────────────────────────────────────────────
  pipeline: {
    // Type 1: Meme/trend/clip shorts
    type1: {
      videosPerQuery: 4,
      maxCandidates: 15,
      minViews: 500000,
      maxSubscribers: 500000,
      minGeminiScore: 7,
      shortsPerDay: 1,
    },
    // Type 2: World explainer shorts
    type2: {
      minDuration: 30,
      maxDuration: 60,
      targetPhases: ['hook', 'evidence', 'explanation', 'contrast'],
      maxSourcingRetries: 3,
      maxReviewIterations: 3,
      rendering: {
        crf: 15,
        preset: 'slow',
        fps: 30,
      },
    },
  },

  paths: {
    root: path.resolve(__dirname, '..'),
    output: path.resolve(__dirname, '..', process.env.OUTPUT_DIR || 'output'),
    clips: path.resolve(__dirname, '..', 'output', 'clips'),
    voiceovers: path.resolve(__dirname, '..', 'output', 'voiceovers'),
    explainers: path.resolve(__dirname, '..', 'output', 'explainers'),
    aiCreated: path.resolve(__dirname, '..', 'output', 'ai-created'),
    longForm: path.resolve(__dirname, '..', 'output', 'long-form'),
    assets: path.resolve(__dirname, '..', 'output', 'assets'),
    temp: path.resolve(__dirname, '..', 'output', 'temp'),
    audio: path.resolve(__dirname, '..', 'output', 'audio'),
    scripts: path.resolve(__dirname, '..', 'output', 'scripts'),
    skills: path.resolve(__dirname, '..', 'skills'),
    trendBanks: path.resolve(__dirname, '..', 'config', 'trend-banks'),
  },
};

module.exports = config;