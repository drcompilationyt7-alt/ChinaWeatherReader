/**
 * Mr. WorldWideWebster — Competitor & Trend Researcher
 *
 * Hermes uses this to research what's working on YouTube Shorts, TikTok,
 * Instagram, and Bilibili by category. It analyzes winning formats,
 * title patterns, hooks, and engagement rates so the agent can make
 * data-informed content decisions.
 *
 * Categories researched:
 * - Architecture/cityscapes (e.g., "China's Future Cities 🇨🇳")
 * - Meme videos (viral clips + quick text overlays)
 * - Streamer clips (Twitch/YouTube highlights)
 * - Explainer shorts ("What is this...?")
 * - Compilation/landscape (longer compilations)
 * - Versus/comparison ("US vs UK Music")
 * - Listicles ("Top 10 [x] Around the World")
 *
 * All scraping uses Puppeteer (headless) + yt-dlp — no API keys.
 */
const puppeteer = require('puppeteer');
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const { Logger } = require('../core/logger');

class CompetitorResearcher {
  constructor() {
    this.logger = new Logger('CompetitorResearcher');
    this.browser = null;
    this.resultsDir = path.join(__dirname, '..', 'memory', 'research');
    if (!fs.existsSync(this.resultsDir)) {
      fs.mkdirSync(this.resultsDir, { recursive: true });
    }
  }

  /**
   * Launch headless browser
   */
  async _launchBrowser() {
    if (this.browser) return true;
    try {
      this.browser = await puppeteer.launch({
        headless: 'new',
        args: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-gpu',
        ],
      });
      return true;
    } catch (error) {
      this.logger.error(`Failed to launch browser: ${error.message}`);
      this.logger.warn('Puppeteer not available — using yt-dlp search fallback');
      return false;
    }
  }

  /**
   * MAIN ENTRY: Research winning formats for a given category
   * @param {string} category - e.g., "architecture", "meme", "streamer", "explainer", "compilation", "versus", "listicle"
   * @param {Object} options - { maxResults, platforms }
   * @returns {Promise<Object>} - { findings, topPatterns, recommendations }
   */
  async researchCategory(category, options = {}) {
    const maxResults = options.maxResults || 10;
    const platforms = options.platforms || ['youtube', 'tiktok', 'instagram'];

    this.logger.info(`Researching category: "${category}" on ${platforms.join(', ')}`);

    // Build search queries based on category
    const searchQueries = this._buildSearchQueries(category);
    this.logger.info(`Search queries: ${searchQueries.join(', ')}`);

    const allFindings = [];

    // Search YouTube Shorts
    if (platforms.includes('youtube')) {
      try {
        const ytResults = await this._searchYouTubeShorts(searchQueries, maxResults);
        allFindings.push(...ytResults);
        this.logger.success(`YouTube: found ${ytResults.length} results`);
      } catch (error) {
        this.logger.warn(`YouTube research failed: ${error.message}`);
      }
    }

    // Search TikTok (via web scrape)
    if (platforms.includes('tiktok')) {
      try {
        const ttResults = await this._searchTikTok(searchQueries, maxResults);
        allFindings.push(...ttResults);
        this.logger.success(`TikTok: found ${ttResults.length} results`);
      } catch (error) {
        this.logger.warn(`TikTok research failed: ${error.message}`);
      }
    }

    // Search Instagram (via web scrape)
    if (platforms.includes('instagram')) {
      try {
        const igResults = await this._searchInstagram(searchQueries, maxResults);
        allFindings.push(...igResults);
        this.logger.success(`Instagram: found ${igResults.length} results`);
      } catch (error) {
        this.logger.warn(`Instagram research failed: ${error.message}`);
      }
    }

    // Analyze findings for patterns
    const analysis = this._analyzeFindings(allFindings, category);

    // Save results to memory
    this._saveResearch(category, analysis);

    return analysis;
  }

  /**
   * Build search queries for each category type
   */
  _buildSearchQueries(category) {
    const queryMap = {
      architecture: [
        'amazing architecture shorts',
        'future cities youtube shorts',
        'cool buildings shorts viral',
        'travel architecture shorts',
        'city skyline shorts',
      ],
      meme: [
        'funny viral shorts today',
        'best meme shorts',
        'trending meme compilation',
        'relatable shorts viral',
      ],
      streamer: [
        'streamer moments shorts',
        'twitch highlights shorts',
        'streamer funny moments viral',
        'gaming shorts viral',
      ],
      explainer: [
        'things you didnt know shorts',
        'how things work shorts',
        'interesting facts shorts viral',
        'educational shorts popular',
      ],
      compilation: [
        'best of compilation shorts',
        'amazing moments compilation',
        'satisfying compilation viral',
        'cinematic compilation shorts',
      ],
      versus: [
        'vs comparison shorts viral',
        'which is better shorts',
        'countries comparison shorts',
        'music comparison shorts',
      ],
      listicle: [
        'top 10 shorts viral',
        'best of list shorts',
        'top 5 shorts popular',
        'ranking shorts viral',
      ],
    };

    // Default fallback
    return queryMap[category] || [
      `${category} shorts viral`,
      `best ${category} shorts`,
      `trending ${category} shorts`,
    ];
  }

  /**
   * Search YouTube Shorts via yt-dlp search
   */
  async _searchYouTubeShorts(queries, maxResults) {
    const results = [];
    const perQuery = Math.max(2, Math.floor(maxResults / queries.length));

    for (const query of queries) {
      try {
        const searchCmd = `yt-dlp --flat-playlist --dump-json "ytsearch${perQuery * 2}:${query}" 2>nul`;
        let output;
        try {
          output = execSync(searchCmd, { timeout: 20000, maxBuffer: 5 * 1024 * 1024 }).toString();
        } catch {
          continue;
        }

        const entries = output.trim().split('\n').filter(Boolean).map(line => {
          try { return JSON.parse(line); } catch { return null; }
        }).filter(Boolean);

        for (const entry of entries) {
          // Get view count from yt-dlp metadata if available
          const viewCount = entry.view_count || 0;
          const duration = entry.duration || 0;

          results.push({
            platform: 'youtube',
            title: entry.title || 'Unknown',
            url: `https://www.youtube.com/watch?v=${entry.id}`,
            videoId: entry.id,
            channel: entry.channel || entry.uploader || 'Unknown',
            views: viewCount,
            duration: duration,
            query: query,
            category: this._categorizeByTitle(entry.title || ''),
          });
        }
      } catch (error) {
        this.logger.warn(`YouTube search failed for "${query}": ${error.message}`);
      }
    }

    // Sort by views descending, take top results
    return results.sort((a, b) => b.views - a.views).slice(0, maxResults);
  }

  /**
   * Search TikTok via web scraping
   */
  async _searchTikTok(queries, maxResults) {
    const results = [];
    const browserOk = await this._launchBrowser();
    if (!browserOk) return results;

    const page = await this.browser.newPage();
    const perQuery = Math.max(1, Math.floor(maxResults / queries.length));

    for (const query of queries.slice(0, 2)) {
      try {
        await page.goto(
          `https://www.tiktok.com/search/video?q=${encodeURIComponent(query)}`,
          { waitUntil: 'networkidle2', timeout: 15000 }
        );
        await new Promise(r => setTimeout(r, 3000));

        // Extract video info from TikTok search results
        const videos = await page.evaluate(() => {
          const items = [];
          const links = document.querySelectorAll('a[href*="/video/"]');
          links.forEach(a => {
            const href = a.getAttribute('href');
            const title = a.getAttribute('title') || 
                          a.querySelector('span')?.textContent || 
                          'TikTok video';
            if (href && !items.some(i => i.url === href)) {
              items.push({ url: `https://www.tiktok.com${href}`, title });
            }
          });
          return items.slice(0, 10);
        });

        for (const video of videos) {
          results.push({
            platform: 'tiktok',
            title: video.title,
            url: video.url,
            views: 0, // TikTok doesn't expose views in HTML easily
            duration: 0,
            query: query,
            category: this._categorizeByTitle(video.title),
          });
        }
      } catch (error) {
        this.logger.warn(`TikTok search failed for "${query}": ${error.message}`);
      }
    }

    await page.close();
    return results.slice(0, maxResults);
  }

  /**
   * Search Instagram via web scraping
   */
  async _searchInstagram(queries, maxResults) {
    // Instagram is heavily locked down — use fallback to yt-dlp search for reels
    const results = [];
    
    for (const query of queries.slice(0, 1)) {
      try {
        // yt-dlp can search Instagram if logged in, otherwise fallback
        const searchCmd = `yt-dlp --flat-playlist --dump-json "igsearch:${query}" 2>nul`;
        let output;
        try {
          output = execSync(searchCmd, { timeout: 15000, maxBuffer: 2 * 1024 * 1024 }).toString();
        } catch {
          continue;
        }

        const entries = output.trim().split('\n').filter(Boolean).map(line => {
          try { return JSON.parse(line); } catch { return null; }
        }).filter(Boolean);

        for (const entry of entries.slice(0, maxResults)) {
          results.push({
            platform: 'instagram',
            title: entry.title || 'Instagram reel',
            url: entry.url || `https://www.instagram.com/reel/${entry.id}/`,
            videoId: entry.id,
            channel: entry.channel || entry.uploader || 'Unknown',
            views: entry.view_count || 0,
            duration: entry.duration || 0,
            query: query,
            category: this._categorizeByTitle(entry.title || ''),
          });
        }
      } catch {
        // Instagram search might fail — that's OK
      }
    }

    return results;
  }

  /**
   * Attempt to categorize a video by its title
   */
  _categorizeByTitle(title) {
    const lower = (title || '').toLowerCase();
    
    if (lower.match(/vs|versus|comparison|better|which/)) return 'versus';
    if (lower.match(/top \d+|best \d+|ranking|\d+ (things|ways|reasons)/)) return 'listicle';
    if (lower.match(/meme|funny|funny moment|relatable/)) return 'meme';
    if (lower.match(/architecture|city|building|skyline|design|futuristic/)) return 'architecture';
    if (lower.match(/streamer|twitch|highlight|clip|moment/)) return 'streamer';
    if (lower.match(/explain|how to|what is|did you know|facts|educational/)) return 'explainer';
    if (lower.match(/compilation|best of|compilation|amazing/)) return 'compilation';
    
    return 'general';
  }

  /**
   * Analyze raw findings to extract patterns and recommendations
   */
  _analyzeFindings(findings, category) {
    const patterns = {
      titleFormats: {},
      avgViews: 0,
      topChannels: {},
      categoryDistribution: {},
      durationRange: { min: 999, max: 0 },
      recommendations: [],
    };

    if (findings.length === 0) {
      patterns.recommendations.push(
        `No results found for "${category}" — try broader search terms`,
        `Consider looking at trending content from Mr. WorldWideWebster's existing niche`
      );
      return patterns;
    }

    // Analyze title patterns
    for (const f of findings) {
      const title = f.title || '';
      
      // Detect title patterns
      if (title.includes('|') || title.includes('—') || title.includes('–')) {
        patterns.titleFormats['pipe_separator'] = (patterns.titleFormats['pipe_separator'] || 0) + 1;
      }
      if (title.match(/^TOP \d+|^\d+ /i)) {
        patterns.titleFormats['numbered_list'] = (patterns.titleFormats['numbered_list'] || 0) + 1;
      }
      if (title.match(/VS|vs|VERSUS|versus/)) {
        patterns.titleFormats['versus'] = (patterns.titleFormats['versus'] || 0) + 1;
      }
      if (title.match(/[?]/)) {
        patterns.titleFormats['question'] = (patterns.titleFormats['question'] || 0) + 1;
      }
      if (title.match(/🇺🇸|🇬🇧|🇨🇳|🌍|🌎|🌏/)) {
        patterns.titleFormats['emoji_opening'] = (patterns.titleFormats['emoji_opening'] || 0) + 1;
      }

      // Track channel frequency
      if (f.channel) {
        patterns.topChannels[f.channel] = (patterns.topChannels[f.channel] || 0) + 1;
      }

      // Category distribution
      patterns.categoryDistribution[f.category] = (patterns.categoryDistribution[f.category] || 0) + 1;

      // Duration range
      if (f.duration > 0) {
        patterns.durationRange.min = Math.min(patterns.durationRange.min, f.duration);
        patterns.durationRange.max = Math.max(patterns.durationRange.max, f.duration);
      }

      // Accumulate view count
      patterns.avgViews += f.views || 0;
    }

    patterns.avgViews = Math.round(patterns.avgViews / findings.length);

    // Generate recommendations based on analysis
    const topFormat = Object.entries(patterns.titleFormats)
      .sort((a, b) => b[1] - a[1])[0];

    if (topFormat) {
      patterns.recommendations.push(
        `Most common title format: "${topFormat[0]}" (${topFormat[1]} out of ${findings.length} videos)`
      );
    }

    if (patterns.avgViews > 10000) {
      patterns.recommendations.push(
        `High engagement category! Avg views: ${patterns.avgViews.toLocaleString()}`
      );
    }

    patterns.recommendations.push(
      `Found ${findings.length} videos across ${new Set(findings.map(f => f.platform)).size} platforms`,
      `Top channels in this niche: ${Object.keys(patterns.topChannels).slice(0, 5).join(', ')}`
    );

    return {
      category,
      collectedAt: new Date().toISOString(),
      totalFindings: findings.length,
      avgViews: patterns.avgViews,
      titlePatterns: patterns.titleFormats,
      topChannels: Object.entries(patterns.topChannels)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 10)
        .map(([name, count]) => ({ name, count })),
      durationRange: patterns.durationRange,
      recommendations: patterns.recommendations,
      topResults: findings.slice(0, 5),
    };
  }

  /**
   * Save research findings to memory file for persistence
   */
  _saveResearch(category, analysis) {
    const filePath = path.join(this.resultsDir, `${category}-research.json`);
    
    // Load existing research
    let existing = [];
    try {
      if (fs.existsSync(filePath)) {
        existing = JSON.parse(fs.readFileSync(filePath, 'utf8'));
        if (!Array.isArray(existing)) existing = [];
      }
    } catch { existing = []; }

    // Keep last 30 entries
    existing.push(analysis);
    if (existing.length > 30) existing = existing.slice(-30);

    fs.writeFileSync(filePath, JSON.stringify(existing, null, 2));
    this.logger.info(`Research saved to ${filePath}`);
  }

  /**
   * Get the latest research for a category
   */
  getLatestResearch(category) {
    const filePath = path.join(this.resultsDir, `${category}-research.json`);
    try {
      if (fs.existsSync(filePath)) {
        const data = JSON.parse(fs.readFileSync(filePath, 'utf8'));
        return Array.isArray(data) ? data[data.length - 1] : data;
      }
    } catch {}
    return null;
  }

  /**
   * Quick research all categories at once (for daily run)
   */
  async researchAllCategories() {
    const categories = ['architecture', 'meme', 'streamer', 'explainer', 'compilation', 'versus', 'listicle'];
    const allResults = {};

    for (const category of categories) {
      this.logger.info(`Researching category: ${category}`);
      allResults[category] = await this.researchCategory(category, { maxResults: 5 });
      
      // Save integrated trending log
      this._updateTrendingLog(category, allResults[category]);
    }

    return allResults;
  }

  /**
   * Update the global trending-log.json with research insights
   */
  _updateTrendingLog(category, analysis) {
    const logPath = path.join(__dirname, '..', 'memory', 'trending-log.json');
    
    let log = { trends: [] };
    try {
      if (fs.existsSync(logPath)) {
        log = JSON.parse(fs.readFileSync(logPath, 'utf8'));
      }
    } catch { log = { trends: [] }; }

    log.trends.push({
      category,
      date: new Date().toISOString().split('T')[0],
      avgViews: analysis.avgViews,
      recommendations: analysis.recommendations?.slice(0, 3) || [],
      topFormat: Object.keys(analysis.titlePatterns || {}).slice(0, 3),
    });

    // Keep last 100 entries
    if (log.trends.length > 100) log.trends = log.trends.slice(-100);

    fs.writeFileSync(logPath, JSON.stringify(log, null, 2));
  }

  /**
   * Clean up
   */
  async destroy() {
    if (this.browser) {
      try { await this.browser.close(); } catch {}
      this.browser = null;
    }
  }
}

module.exports = { CompetitorResearcher };