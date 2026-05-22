/**
 * Mr. WorldWideWebster — Universal Video Downloader
 *
 * Downloads videos from 1700+ platforms using yt-dlp (primary)
 * with multiple fallback strategies for tricky sites.
 *
 * Supported platforms:
 * - YouTube, Bilibili, Douyin, TikTok, Instagram, Twitter/X
 * - Facebook, Twitch, Vimeo, Reddit, and 1700+ more via yt-dlp
 * - Instagram Reels, Stories, Posts
 * - Any direct video URL
 *
 * Fallbacks (when yt-dlp fails):
 * 1. Puppeteer page scrape — find video URL in page source
 * 2. Direct HTTP download — for direct video links
 * 3. Snaptik / SSSTikTok API — for TikTok-specific content
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const axios = require('axios');
const { Logger } = require('../core/logger');

class UniversalDownloader {
  constructor() {
    this.logger = new Logger('UniversalDownloader');
  }

  /**
   * Detect which platform a URL belongs to
   */
  _detectPlatform(url) {
    if (!url) return 'unknown';
    const urlLower = url.toLowerCase();

    if (urlLower.includes('youtube.com') || urlLower.includes('youtu.be')) return 'youtube';
    if (urlLower.includes('bilibili.com') || urlLower.includes('b23.tv')) return 'bilibili';
    if (urlLower.includes('douyin.com')) return 'douyin';
    if (urlLower.includes('tiktok.com')) return 'tiktok';
    if (urlLower.includes('instagram.com')) return 'instagram';
    if (urlLower.includes('twitter.com') || urlLower.includes('x.com')) return 'twitter';
    if (urlLower.includes('facebook.com') || urlLower.includes('fb.watch')) return 'facebook';
    if (urlLower.includes('twitch.tv')) return 'twitch';
    if (urlLower.includes('vimeo.com')) return 'vimeo';
    if (urlLower.includes('reddit.com') || urlLower.includes('redd.it')) return 'reddit';
    if (urlLower.includes('xiaohongshu.com') || urlLower.includes('rednote')) return 'rednote';
    if (urlLower.includes('weibo.com')) return 'weibo';
    if (urlLower.endsWith('.mp4') || urlLower.endsWith('.webm') || urlLower.endsWith('.mov')) return 'direct_video';

    return 'unknown';
  }

  /**
   * Main download function with multiple fallback strategies
   *
   * @param {string} url - Video URL to download
   * @param {Object} options - { outputDir, maxHeight, cookiesFile }
   * @returns {Promise<Object>} - { success, filePath, platform, method, title }
   */
  async download(url, options = {}) {
    const outputDir = options.outputDir || config?.paths?.output || path.join(__dirname, '..', 'output');
    const maxHeight = options.maxHeight || 720;
    const platform = this._detectPlatform(url);

    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }

    this.logger.info(`Downloading from ${platform}: ${url.substring(0, 80)}`);

    // Strategy 1: yt-dlp (works for 1700+ sites)
    try {
      const result = await this._downloadWithYtDlp(url, outputDir, maxHeight, platform);
      if (result.success) return result;
      this.logger.warn(`yt-dlp failed for ${platform}, trying fallback...`);
    } catch (error) {
      this.logger.warn(`yt-dlp error: ${error.message}`);
    }

    // Strategy 2: Puppeteer page scrape (for sites yt-dlp can't handle)
    if (['instagram', 'tiktok', 'bilibili', 'douyin', 'rednote', 'weibo'].includes(platform)) {
      try {
        const result = await this._downloadWithPuppeteer(url, outputDir, platform);
        if (result.success) return result;
      } catch (error) {
        this.logger.warn(`Puppeteer fallback failed for ${platform}: ${error.message}`);
      }
    }

    // Strategy 3: Direct HTTP download (if URL itself is a video file)
    if (platform === 'direct_video' || url.match(/\.(mp4|webm|mov|avi)$/i)) {
      try {
        const result = await this._downloadDirect(url, outputDir);
        if (result.success) return result;
      } catch (error) {
        this.logger.warn(`Direct download failed: ${error.message}`);
      }
    }

    // Strategy 4: TikTok/Instagram API fallback (free scrapers)
    if (platform === 'tiktok') {
      try {
        const result = await this._downloadTikTokScraper(url, outputDir);
        if (result.success) return result;
      } catch (error) {
        this.logger.warn(`TikTok scraper fallback failed: ${error.message}`);
      }
    }

    // Strategy 5: Try yt-dlp with different format selection
    try {
      const result = await this._downloadWithYtDlp(url, outputDir, maxHeight, platform, true);
      if (result.success) return result;
    } catch (error) {
      this.logger.warn(`yt-dlp alternative format failed: ${error.message}`);
    }

    this.logger.error(`All download methods failed for: ${url}`);
    return { success: false, error: 'All download methods failed', platform, url };
  }

  /**
   * Primary method: yt-dlp (supports 1700+ sites)
   */
  async _downloadWithYtDlp(url, outputDir, maxHeight, platform, useAltFormat = false) {
    const safeTimestamp = Date.now();
    const outputTemplate = path.join(outputDir, `%(extractor)s_${safeTimestamp}_%(id)s.%(ext)s`);

    // Build yt-dlp command
    let cmd = `yt-dlp`;
    
    // Format selection
    if (useAltFormat) {
      // Alternative: try best available, any format
      cmd += ` -f "bestvideo[height<=${maxHeight}]+bestaudio/best[height<=${maxHeight}]" --merge-output-format mp4`;
    } else if (platform === 'bilibili' || platform === 'douyin') {
      // These platforms often need specific handling
      cmd += ` -f "best[height<=${maxHeight}]"`;
    } else {
      cmd += ` -f "bestvideo[height<=${maxHeight}]+bestaudio/best[height<=${maxHeight}]" --merge-output-format mp4`;
    }

    cmd += ` --no-playlist --no-warnings --print title`;
    cmd += ` -o "${outputTemplate}" "${url}"`;

    try {
      this.logger.info(`Running yt-dlp for ${platform}...`);
      const output = execSync(cmd, { timeout: 300000, maxBuffer: 10 * 1024 * 1024 }).toString().trim();
      const lines = output.split('\n').filter(Boolean);

      // yt-dlp prints the title first, then the file path
      const title = lines[0] || `Video from ${platform}`;

      // Find the downloaded file
      const files = fs.readdirSync(outputDir)
        .filter(f => f.includes(String(safeTimestamp)))
        .map(f => path.join(outputDir, f));

      if (files.length > 0) {
        this.logger.success(`✅ yt-dlp downloaded: ${title}`);
        return {
          success: true,
          filePath: files[0],
          platform,
          method: 'yt-dlp',
          title,
          url,
        };
      }

      // If no file found with timestamp, look for any recently modified files
      const recentFiles = fs.readdirSync(outputDir)
        .filter(f => f.endsWith('.mp4') || f.endsWith('.webm'))
        .map(f => ({ name: f, time: fs.statSync(path.join(outputDir, f)).mtimeMs }))
        .sort((a, b) => b.time - a.time)
        .slice(0, 3);

      if (recentFiles.length > 0) {
        this.logger.success(`✅ yt-dlp downloaded (found recently): ${recentFiles[0].name}`);
        return {
          success: true,
          filePath: path.join(outputDir, recentFiles[0].name),
          platform,
          method: 'yt-dlp',
          title: title || recentFiles[0].name,
          url,
        };
      }

      throw new Error('yt-dlp completed but no output file found');
    } catch (error) {
      throw new Error(`yt-dlp ${platform}: ${error.message}`);
    }
  }

  /**
   * Puppeteer fallback: scrape page for video URL, then download
   * Works for Instagram, Bilibili, and sites that embed video players
   */
  async _downloadWithPuppeteer(url, outputDir, platform) {
    const puppeteer = require('puppeteer');
    const browser = await puppeteer.launch({
      headless: 'new',
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
    });

    try {
      const page = await browser.newPage();
      await page.setUserAgent(
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
        '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
      );

      this.logger.info(`Puppeteer browsing: ${platform}`);
      await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 });
      await new Promise(r => setTimeout(r, 3000));

      // Extract video URL from page
      const videoUrl = await page.evaluate(() => {
        // Try common video element patterns
        const video = document.querySelector('video');
        if (video) {
          // Check for src attribute
          if (video.src && video.src.startsWith('http')) return video.src;
          // Check for source children
          const source = video.querySelector('source');
          if (source && source.src) return source.src;
        }
        // Check for data attributes (common in social media)
        const allElements = document.querySelectorAll('[data-video-src], [data-url]');
        for (const el of allElements) {
          const val = el.getAttribute('data-video-src') || el.getAttribute('data-url');
          if (val && val.startsWith('http')) return val;
        }
        // Check for meta tags (og:video)
        const meta = document.querySelector('meta[property="og:video"]');
        if (meta) return meta.getAttribute('content');
        // Check for JSON-LD
        const scripts = document.querySelectorAll('script[type="application/ld+json"]');
        for (const script of scripts) {
          try {
            const data = JSON.parse(script.textContent);
            if (data?.video?.contentUrl) return data.video.contentUrl;
          } catch {}
        }
        return null;
      });

      if (videoUrl) {
        this.logger.info(`Found video URL via Puppeteer: ${videoUrl.substring(0, 60)}`);
        const safeId = Date.now();
        const ext = '.mp4';
        const outputPath = path.join(outputDir, `${platform}_puppeteer_${safeId}${ext}`);

        // Download the video file
        const writer = fs.createWriteStream(outputPath);
        const response = await axios({
          method: 'GET',
          url: videoUrl,
          responseType: 'stream',
          timeout: 60000,
          headers: {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': url,
          },
        });

        response.data.pipe(writer);
        await new Promise((resolve, reject) => {
          writer.on('finish', () => {
            if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 1000) {
              resolve();
            } else {
              reject(new Error('Downloaded file is empty'));
            }
          });
          writer.on('error', reject);
        });

        this.logger.success(`✅ Puppeteer downloaded: ${platform} video`);
        await browser.close();
        return {
          success: true,
          filePath: outputPath,
          platform,
          method: 'puppeteer',
          title: `Video from ${platform}`,
          url,
        };
      }

      // Try taking a screenshot as last resort (at least we got the visual)
      const screenshotPath = path.join(outputDir, `${platform}_screenshot_${Date.now()}.png`);
      await page.screenshot({ path: screenshotPath, fullPage: true });
      this.logger.info(`Screenshot saved as fallback: ${screenshotPath}`);

      await browser.close();
      throw new Error(`No video URL found on ${platform} page`);
    } catch (error) {
      await browser.close().catch(() => {});
      throw error;
    }
  }

  /**
   * Direct HTTP download for direct video URLs
   */
  async _downloadDirect(url, outputDir) {
    const safeId = Date.now();
    const ext = path.extname(url.split('?')[0]) || '.mp4';
    const outputPath = path.join(outputDir, `direct_${safeId}${ext}`);

    this.logger.info(`Direct download: ${url.substring(0, 60)}`);

    const writer = fs.createWriteStream(outputPath);
    const response = await axios({
      method: 'GET',
      url: url,
      responseType: 'stream',
      timeout: 120000,
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': url,
      },
    });

    response.data.pipe(writer);
    await new Promise((resolve, reject) => {
      writer.on('finish', resolve);
      writer.on('error', reject);
    });

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 1000) {
      this.logger.success(`✅ Direct download complete`);
      return {
        success: true,
        filePath: outputPath,
        platform: 'direct',
        method: 'direct',
        title: `Direct download ${safeId}`,
        url,
      };
    }
    throw new Error('Direct download file is empty');
  }

  /**
   * TikTok-specific scraper (free, no API key)
   * Uses tikwm.com public API (free)
   */
  async _downloadTikTokScraper(url, outputDir) {
    this.logger.info('Trying TikTok scraper fallback...');

    try {
      // tikwm.com provides a free TikTok video download API
      const response = await axios.post('https://www.tikwm.com/api/', 
        new URLSearchParams({ url, count: 12, cursor: 0, web: 1, hd: 1 }),
        {
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
          },
          timeout: 15000,
        }
      );

      const data = response.data;
      if (data?.data?.play) {
        const videoUrl = data.data.play;
        const safeId = Date.now();
        const outputPath = path.join(outputDir, `tiktok_api_${safeId}.mp4`);

        const writer = fs.createWriteStream(outputPath);
        const videoResponse = await axios({
          method: 'GET',
          url: videoUrl,
          responseType: 'stream',
          timeout: 60000,
          headers: { 'User-Agent': 'Mozilla/5.0' },
        });
        videoResponse.data.pipe(writer);
        await new Promise((resolve, reject) => {
          writer.on('finish', resolve);
          writer.on('error', reject);
        });

        if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 1000) {
          this.logger.success(`✅ TikTok scraper downloaded`);
          return {
            success: true,
            filePath: outputPath,
            platform: 'tiktok',
            method: 'tikwm_api',
            title: data.data.title || 'TikTok video',
            url,
          };
        }
      }
      throw new Error('TikTok API returned no video URL');
    } catch (error) {
      throw new Error(`TikTok scraper: ${error.message}`);
    }
  }
}

// Store config reference
let config;
try {
  config = require('../core/config');
} catch {}

module.exports = { UniversalDownloader };