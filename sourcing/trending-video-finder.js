/**
 * Mr. WorldWideWebster — Trending Video Finder
 * 
 * Uses Puppeteer to directly scrape trending videos from:
 * - Bilibili (China)
 * - Douyin (Chinese TikTok)
 * - Xiaohongshu/Rednote (Chinese lifestyle)
 * - TikTok (global)
 * - Instagram Reels
 * - YouTube Shorts
 * 
 * Returns actual video URLs that can be downloaded with yt-dlp or puppeteer
 */
const puppeteer = require('puppeteer');
const { Logger } = require('../core/logger');

class TrendingVideoFinder {
  constructor() {
    this.logger = new Logger('TrendingFinder');
    this.browser = null;
  }

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
          '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ],
      });
      return true;
    } catch (error) {
      this.logger.error(`Failed to launch browser: ${error.message}`);
      return false;
    }
  }

  /**
   * Find trending videos from a specific platform
   * @param {string} platform - 'bilibili', 'douyin', 'tiktok', 'instagram', 'rednote', 'youtube'
   * @param {string} query - Search query in appropriate language
   * @returns {Promise<Array>} - Array of {url, title, platform, country}
   */
  async findTrendingVideos(platform, query) {
    const browserOk = await this._launchBrowser();
    if (!browserOk) {
      this.logger.warn('Browser not available');
      return [];
    }

    this.logger.info(`Finding trending videos on ${platform}: "${query}"`);

    switch (platform) {
      case 'bilibili':
        return await this._findBilibili(query);
      case 'douyin':
        return await this._findDouyin(query);
      case 'rednote':
      case 'xiaohongshu':
        return await this._findRednote(query);
      case 'tiktok':
        return await this._findTikTok(query);
      case 'instagram':
        return await this._findInstagram(query);
      case 'youtube':
        return await this._findYouTube(query);
      default:
        this.logger.warn(`Unknown platform: ${platform}`);
        return [];
    }
  }

  async _findBilibili(query) {
    const results = [];
    const page = await this.browser.newPage();
    
    try {
      // Bilibili search
      const searchUrl = `https://search.bilibili.com/all?keyword=${encodeURIComponent(query)}`;
      await page.goto(searchUrl, { waitUntil: 'networkidle2', timeout: 30000 });
      await new Promise(r => setTimeout(r, 3000));

      // Extract video links
      const videos = await page.evaluate(() => {
        const items = [];
        document.querySelectorAll('.video-item').forEach(div => {
          const link = div.querySelector('a[href*="/video/"]');
          const title = div.querySelector('.title');
          if (link && title) {
            items.push({
              url: link.href.startsWith('http') ? link.href : 'https:' + link.href,
              title: title.textContent.trim(),
            });
          }
        });
        return items.slice(0, 5);
      });

      videos.forEach(v => {
        results.push({
          url: v.url,
          title: v.title,
          platform: 'bilibili',
          country: 'China',
        });
      });

      this.logger.success(`Found ${results.length} Bilibili videos`);
    } catch (error) {
      this.logger.warn(`Bilibili search failed: ${error.message}`);
    } finally {
      await page.close();
    }

    return results;
  }

  async _findDouyin(query) {
    const results = [];
    const page = await this.browser.newPage();
    
    try {
      // Douyin web version search
      const searchUrl = `https://www.douyin.com/search/${encodeURIComponent(query)}`;
      
      await page.setUserAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1');
      await page.goto(searchUrl, { waitUntil: 'networkidle2', timeout: 30000 });
      await new Promise(r => setTimeout(r, 4000));

      // Extract video links from douyin
      const videos = await page.evaluate(() => {
        const items = [];
        // Douyin uses various selectors for video cards
        document.querySelectorAll('[data-e2e="feed-content"], .common-video-card, a[href*="/video/"]').forEach(el => {
          const link = el.tagName === 'A' ? el : el.closest('a[href*="/video/"]');
          const title = el.querySelector('.title, .desc') || el;
          if (link && title) {
            const href = link.getAttribute('href');
            if (href && !items.find(i => i.url?.includes(href.split('/').pop()))) {
              items.push({
                url: href.startsWith('http') ? href : 'https://www.douyin.com' + href,
                title: title.textContent?.trim().substring(0, 100) || 'Douyin video',
              });
            }
          }
        });
        return items.slice(0, 5);
      });

      videos.forEach(v => {
        if (v.url && !results.find(r => r.url === v.url)) {
          results.push({
            url: v.url,
            title: v.title,
            platform: 'douyin',
            country: 'China',
          });
        }
      });

      this.logger.success(`Found ${results.length} Douyin videos`);
    } catch (error) {
      this.logger.warn(`Douyin search failed: ${error.message}`);
    } finally {
      await page.close();
    }

    return results;
  }

  async _findRednote(query) {
    const results = [];
    const page = await this.browser.newPage();
    
    try {
      // Xiaohongshu/Rednote search
      const searchUrl = `https://www.xiaohongshu.com/search_result?keyword=${encodeURIComponent(query)}&source=web_search_result_notes`;
      await page.goto(searchUrl, { waitUntil: 'networkidle2', timeout: 30000 });
      await new Promise(r => setTimeout(r, 3000));

      // Extract note/video links
      const videos = await page.evaluate(() => {
        const items = [];
        document.querySelectorAll('.note-item, [class*="note"]').forEach(div => {
          const link = div.querySelector('a[href*="/explore/"]');
          const title = div.querySelector('.title, .desc');
          if (link && title) {
            items.push({
              url: link.href.startsWith('http') ? link.href : 'https://www.xiaohongshu.com' + link.href,
              title: title.textContent.trim().substring(0, 100),
            });
          }
        });
        return items.slice(0, 5);
      });

      videos.forEach(v => {
        results.push({
          url: v.url,
          title: v.title,
          platform: 'rednote',
          country: 'China',
        });
      });

      this.logger.success(`Found ${results.length} Rednote videos`);
    } catch (error) {
      this.logger.warn(`Rednote search failed: ${error.message}`);
    } finally {
      await page.close();
    }

    return results;
  }

  async _findTikTok(query) {
    const results = [];
    const page = await this.browser.newPage();
    
    try {
      // TikTok search
      const searchUrl = `https://www.tiktok.com/search?q=${encodeURIComponent(query)}`;
      await page.goto(searchUrl, { waitUntil: 'networkidle2', timeout: 30000 });
      await new Promise(r => setTimeout(r, 4000));

      // Extract video links
      const videos = await page.evaluate(() => {
        const items = [];
        document.querySelectorAll('a[href*="/video/"]').forEach(a => {
          const href = a.getAttribute('href');
          const title = a.textContent?.trim().substring(0, 100) || 'TikTok video';
          if (href && !items.find(i => i.url?.includes(href.split('/').pop()))) {
            items.push({
              url: href.startsWith('http') ? href : 'https://www.tiktok.com' + href,
              title: title,
            });
          }
        });
        return items.slice(0, 5);
      });

      videos.forEach(v => {
        if (!results.find(r => r.url === v.url)) {
          results.push({
            url: v.url,
            title: v.title,
            platform: 'tiktok',
            country: 'Global',
          });
        }
      });

      this.logger.success(`Found ${results.length} TikTok videos`);
    } catch (error) {
      this.logger.warn(`TikTok search failed: ${error.message}`);
    } finally {
      await page.close();
    }

    return results;
  }

  async _findInstagram(query) {
    const results = [];
    const page = await this.browser.newPage();
    
    try {
      // Instagram reels search (requires login, so we'll try hashtag search)
      const searchUrl = `https://www.instagram.com/explore/tags/${encodeURIComponent(query.replace(/\s+/g, ''))}/`;
      await page.goto(searchUrl, { waitUntil: 'networkidle2', timeout: 30000 });
      await new Promise(r => setTimeout(r, 3000));

      // Extract reel links
      const videos = await page.evaluate(() => {
        const items = [];
        document.querySelectorAll('a[href*="/reel/"]').forEach(a => {
          const href = a.getAttribute('href');
          if (href && !items.find(i => i.url?.includes(href.split('/').pop()))) {
            items.push({
              url: href.startsWith('http') ? href : 'https://www.instagram.com' + href,
              title: 'Instagram Reel',
            });
          }
        });
        return items.slice(0, 5);
      });

      videos.forEach(v => {
        if (!results.find(r => r.url === v.url)) {
          results.push({
            url: v.url,
            title: v.title,
            platform: 'instagram',
            country: 'Global',
          });
        }
      });

      this.logger.success(`Found ${results.length} Instagram reels`);
    } catch (error) {
      this.logger.warn(`Instagram search failed: ${error.message}`);
    } finally {
      await page.close();
    }

    return results;
  }

  async _findYouTube(query) {
    const results = [];
    const page = await this.browser.newPage();
    
    try {
      // YouTube Shorts search
      const searchUrl = `https://www.youtube.com/results?search_query=${encodeURIComponent(query)}&sp=EgQIAhAB`;
      await page.goto(searchUrl, { waitUntil: 'networkidle2', timeout: 30000 });
      await new Promise(r => setTimeout(r, 3000));

      // Extract video links
      const videos = await page.evaluate(() => {
        const items = [];
        document.querySelectorAll('a[href*="/watch?v="]').forEach(a => {
          const href = a.getAttribute('href');
          const title = a.querySelector('#video-title')?.textContent?.trim();
          if (href && title && !items.find(i => i.url?.includes(href.split('=')[1]))) {
            items.push({
              url: href.startsWith('http') ? href : 'https://www.youtube.com' + href,
              title: title.substring(0, 100),
            });
          }
        });
        return items.slice(0, 5);
      });

      videos.forEach(v => {
        if (!results.find(r => r.url === v.url)) {
          results.push({
            url: v.url,
            title: v.title,
            platform: 'youtube',
            country: 'Global',
          });
        }
      });

      this.logger.success(`Found ${results.length} YouTube videos`);
    } catch (error) {
      this.logger.warn(`YouTube search failed: ${error.message}`);
    } finally {
      await page.close();
    }

    return results;
  }

  async destroy() {
    if (this.browser) {
      try { await this.browser.close(); } catch {}
      this.browser = null;
    }
  }
}

module.exports = { TrendingVideoFinder };
