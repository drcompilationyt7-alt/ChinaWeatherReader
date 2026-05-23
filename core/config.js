/**
 * Mr. WorldWideWebster - Configuration Loader
 * Loads environment variables and provides typed access to config
 * Supports OpenRouter (primary), OpenAI (fallback), and Gemini
 * 
 * Multi-Key Support: Set OPENROUTER_API_KEY_2, OPENROUTER_API_KEY_3, OPENROUTER_API_KEY_4
 * as GitHub Secrets for automatic key rotation when one hits rate/credit limits.
 */
require('dotenv').config();
const path = require('path');

const config = {
  // --- AI Provider ---
  provider: {
    name: process.env.AI_PROVIDER || 'openrouter',  // openrouter, openai, gemini
  },

  // --- OpenRouter Settings (Multi-Key Supported) ---
  openrouter: {
    apiKey: process.env.OPENROUTER_API_KEY,
    // Additional API keys for multi-key rotation (keys 2-4)
    // Set these as GitHub Secrets:
    //   OPENROUTER_API_KEY_2, OPENROUTER_API_KEY_3, OPENROUTER_API_KEY_4
    // The provider auto-rotates through keys when one hits rate/credit limits (402/429)
    defaultModel: process.env.OPENROUTER_DEFAULT_MODEL || 'openrouter/owl-alpha',
    scriptModel: process.env.OPENROUTER_SCRIPT_MODEL || 'openrouter/owl-alpha',
    agentModel: process.env.OPENROUTER_AGENT_MODEL || 'openrouter/owl-alpha',
    imageModel: process.env.OPENROUTER_IMAGE_MODEL || 'black-forest-labs/flux-schnell',
    freeModel: process.env.OPENROUTER_FREE_MODEL || 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
  },

  // --- OpenAI Settings (Fallback) ---
  openai: {
    apiKey: process.env.OPENAI_API_KEY,
    model: process.env.OPENAI_MODEL || 'gpt-4o-mini',
    scriptModel: process.env.OPENAI_SCRIPT_MODEL || 'gpt-4o',
  },

  // --- Gemini Settings (Fallback) ---
  gemini: {
    apiKey: process.env.GEMINI_API_KEY,
    model: process.env.GEMINI_MODEL || 'gemini-2.5-flash',
  },

  // --- TTS Settings ---
  tts: {
    provider: process.env.TTS_PROVIDER || 'edge',  // edge (free), openai, elevenlabs
    edgeVoiceCurious: process.env.EDGE_TTS_VOICE_CURIOUS || 'en-US-JennyNeural',
    edgeVoiceExplainer: process.env.EDGE_TTS_VOICE_EXPLAINER || 'en-US-GuyNeural',
    edgeVoiceNews: process.env.EDGE_TTS_VOICE_NEWS || 'en-GB-SoniaNeural',
    elevenLabsKey: process.env.ELEVENLABS_API_KEY,
    elevenLabsVoiceCurious: process.env.ELEVENLABS_VOICE_CURIOUS || '21m00Tcm4TlvDq8ikWAM',
    elevenLabsVoiceExplainer: process.env.ELEVENLABS_VOICE_EXPLAINER || 'TxGEqnHWrfWFTfGW9XjX',
  },

  // --- Discord Settings ---
  discord: {
    botToken: process.env.DISCORD_BOT_TOKEN,
    channelId: process.env.DISCORD_CHANNEL_ID,
    userId: process.env.DISCORD_USER_ID,
  },

  // --- Boost (View Bot) Settings ---
  boost: {
    enabled: process.env.BOOST_ENABLED === 'true',
    maxViews: parseInt(process.env.BOOST_MAX_VIEWS) || 100,
    minViews: parseInt(process.env.BOOST_MIN_VIEWS) || 50,
    minWatchSec: parseInt(process.env.BOOST_MIN_WATCH_SEC) || 30,
    maxWatchSec: parseInt(process.env.BOOST_MAX_WATCH_SEC) || 90,
    spreadMinMinutes: parseInt(process.env.BOOST_SPREAD_MIN) || 15,
    spreadMaxMinutes: parseInt(process.env.BOOST_SPREAD_MAX) || 45,
  },

  // --- YouTube Settings ---
  youtube: {
    apiKey: process.env.YOUTUBE_API_KEY,
    clientId: process.env.YOUTUBE_CLIENT_ID,
    clientSecret: process.env.YOUTUBE_CLIENT_SECRET,
    region: process.env.YOUTUBE_REGION || 'US',
    privacyStatus: process.env.DEFAULT_PRIVACY_STATUS || 'public',
    maxUploadsPerDay: parseInt(process.env.MAX_UPLOADS_PER_DAY) || 5,
  },

  // --- Channel Info ---
  channel: {
    name: process.env.CHANNEL_NAME || 'Mr. WorldWideWebster',
    tagline: process.env.CHANNEL_TAGLINE || 'Bringing the world to you',
    targetAudience: process.env.TARGET_AUDIENCE || 'Global culture enthusiasts, travelers, people curious about international trends',
  },

  // --- Content Sourcing ---
  sourcing: {
    enabledSources: (process.env.ENABLED_SOURCES || 'bilibili,tiktok,news').split(',').map(s => s.trim()),
    schedule: process.env.SOURCE_SCHEDULE || '0 */6 * * *',
  },

  // --- Paths ---
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
    skills: path.resolve(__dirname, '..', 'hermes-agent', 'skills'),
  },
};

module.exports = config;