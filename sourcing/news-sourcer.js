/**
 * Mr. WorldWideWebster - News Sourcer
 * 
 * Fetches global news for AI-powered news summaries.
 * In production, use NewsAPI.org, Google News RSS, or other news APIs.
 */
const { Logger } = require('../core/logger');
const logger = new Logger('News');

module.exports = {
  async fetchTrending(config) {
    logger.info('Fetching global news...');

    // In production: use NewsAPI (https://newsapi.org) or Google News RSS
    return [
      {
        url: 'https://news.example.com/china-tech-boom-2025',
        title: "China's Tech Boom: How AI is Changing Daily Life in 2025",
        description: 'From facial recognition支付的 (payments) to AI-driven healthcare, China is leading the AI revolution in everyday life.',
        duration: 60,
        hasSpeech: true,
        isVisual: false,
        languageDetected: 'english',
        thumbnailUrl: null,
        engagementScore: 80,
      },
      {
        url: 'https://news.example.com/africa-startups',
        title: 'African Startups Raising Billions - Here is What They Are Building',
        description: 'The African tech ecosystem is booming with fintech, agritech, and healthtech startups attracting global investment.',
        duration: 60,
        hasSpeech: true,
        isVisual: false,
        languageDetected: 'english',
        thumbnailUrl: null,
        engagementScore: 75,
      },
      {
        url: 'https://news.example.com/japan-pop-culture-global',
        title: 'How Japanese Pop Culture Took Over the World in 2025',
        description: 'Anime, manga, J-pop, and Japanese food are more popular globally than ever before.',
        duration: 60,
        hasSpeech: true,
        isVisual: false,
        languageDetected: 'english',
        thumbnailUrl: null,
        engagementScore: 85,
      },
      {
        url: 'https://news.example.com/europe-social-media-trends',
        title: 'Europe New Social Media Laws: What Changes for Users?',
        description: 'The EU Digital Services Act is changing how social media works in Europe - and it might come to the US next.',
        duration: 60,
        hasSpeech: true,
        isVisual: false,
        languageDetected: 'english',
        thumbnailUrl: null,
        engagementScore: 70,
      },
      {
        url: 'https://news.example.com/south-korea-beauty-standards',
        title: 'South Korea Beauty Standards: Why K-Beauty is Different',
        description: 'Korean beauty standards are changing globally - from skincare routines to makeup trends, here what makes K-Beauty unique.',
        duration: 60,
        hasSpeech: true,
        isVisual: false,
        languageDetected: 'english',
        thumbnailUrl: null,
        engagementScore: 78,
      },
    ];
  },
};