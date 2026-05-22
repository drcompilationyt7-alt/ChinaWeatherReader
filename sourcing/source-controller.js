/**
 * Mr. WorldWideWebster - Source Controller
 * 
 * Orchestrates content discovery from all enabled platforms.
 * Discovers trending content and feeds it to the decision engine.
 */
const { Logger } = require('../core/logger');

class SourceController {
  constructor(aiService, config) {
    this.ai = aiService;
    this.config = config;
    this.logger = new Logger('SourceController');
    this.sources = {};
    this._initializeSources();
  }

  _initializeSources() {
    const enabled = this.config.sourcing.enabledSources;

    if (enabled.includes('bilibili')) {
      this.sources.bilibili = require('./bilibili-scraper');
      this.logger.info('✓ Bilibili source loaded');
    }
    if (enabled.includes('tiktok')) {
      this.sources.tiktok = require('./tiktok-scraper');
      this.logger.info('✓ TikTok source loaded');
    }
    if (enabled.includes('news')) {
      this.sources.news = require('./news-sourcer');
      this.logger.info('✓ News source loaded');
    }
    if (enabled.includes('douyin')) {
      this.sources.douyin = require('./douyin-scraper');
      this.logger.info('✓ Douyin source loaded');
    }
    if (enabled.includes('rednote')) {
      this.sources.rednote = require('./rednote-scraper');
      this.logger.info('✓ RedNote source loaded');
    }
    if (enabled.includes('twitter')) {
      this.sources.twitter = require('./twitter-scraper');
      this.logger.info('✓ Twitter/X source loaded');
    }
  }

  /**
   * Discover trending content from all enabled sources
   */
  async discoverContent() {
    this.logger.header('CONTENT DISCOVERY');
    this.logger.info(`Scanning ${Object.keys(this.sources).length} enabled sources...`);

    const allContent = [];

    for (const [sourceName, sourceModule] of Object.entries(this.sources)) {
      try {
        this.logger.info(`Fetching from ${sourceName}...`);
        const items = await sourceModule.fetchTrending(this.config);
        this.logger.success(`${sourceName}: found ${items.length} items`);
        allContent.push(...items.map(item => ({
          ...item,
          platform: sourceName,
          discoveredAt: new Date().toISOString(),
        })));
      } catch (error) {
        this.logger.error(`${sourceName} fetch failed: ${error.message}`);
      }
    }

    this.logger.success(`Total content discovered: ${allContent.length} items`);
    return allContent;
  }

  /**
   * Get content by category (for AI Create path - topics without URLs)
   */
  async getTopicsForAICreate() {
    this.logger.info('Generating AI-create topic ideas...');

    const topics = [
      // Music comparisons
      { title: 'UK Drill vs US Trap: The Real Difference', platform: 'ai_topic', contentType: 'comparison' },
      { title: 'K-Pop vs J-Pop: What Makes Them Different?', platform: 'ai_topic', contentType: 'comparison' },
      { title: 'Chinese Pop vs American Pop in 2025', platform: 'ai_topic', contentType: 'comparison' },
      { title: 'Afrobeats: Africa Music Taking Over the World', platform: 'ai_topic', contentType: 'explainer' },
      
      // Cultural explainers
      { title: 'Why Bilibili is China YouTube', platform: 'ai_topic', contentType: 'explainer' },
      { title: 'What is Douyin? (China TikTok Explained)', platform: 'ai_topic', contentType: 'explainer' },
      { title: 'RedNote (Xiaohongshu): China Shopping & Lifestyle App', platform: 'ai_topic', contentType: 'explainer' },
      { title: 'What is UK Drill Music? Genre Explained', platform: 'ai_topic', contentType: 'explainer' },
      { title: 'What is K-Pop? A Complete Guide', platform: 'ai_topic', contentType: 'explainer' },
      { title: 'Japanese City Pop: The Genre Taking Over TikTok', platform: 'ai_topic', contentType: 'explainer' },
      
      // Comparisons
      { title: 'US vs UK Slang: Do You Speak American or British?', platform: 'ai_topic', contentType: 'comparison' },
      { title: 'School in Japan vs America: 10 Big Differences', platform: 'ai_topic', contentType: 'comparison' },
      { title: 'Fast Food in China vs America', platform: 'ai_topic', contentType: 'comparison' },
      { title: 'Instagram vs TikTok vs Douyin: Which is Better?', platform: 'ai_topic', contentType: 'comparison' },
      
      // Trending topics
      { title: 'The Biggest Chinese Memes Right Now', platform: 'ai_topic', contentType: 'listicle' },
      { title: 'African Memes Taking Over the Internet', platform: 'ai_topic', contentType: 'listicle' },
      { title: '3 Viral Challenges from Around the World', platform: 'ai_topic', contentType: 'listicle' },
      { title: 'Most Watched Streamers in China Right Now', platform: 'ai_topic', contentType: 'explainer' },
      
      // Explainers (What is this? format)
      { title: 'What is this Chinese fruit? (The Weirdest Fruits in China)', platform: 'ai_topic', contentType: 'explainer' },
      { title: 'What is this dance? (Viral Dances from Around the World)', platform: 'ai_topic', contentType: 'explainer' },
      { title: 'What is this food? (Street Food from Every Continent)', platform: 'ai_topic', contentType: 'explainer' },
    ];

    this.logger.info(`Generated ${topics.length} AI-create topic ideas`);
    return topics.map(t => ({
      url: null,
      title: t.title,
      platform: 'ai_topic',
      contentType: t.contentType,
      description: t.title,
      duration: 60,
      hasSpeech: false,
      isVisual: false,
      languageDetected: 'english',
      thumbnailUrl: null,
      isAITopic: true,
    }));
  }
}

module.exports = { SourceController };