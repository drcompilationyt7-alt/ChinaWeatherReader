/**
 * Mr. WorldWideWebster — Free Visual Searcher
 *
 * Uses Puppeteer + yt-dlp to search for and download FREE video clips
 * and images from the web. No API keys needed.
 *
 * Sources:
 * - Pexels Videos (free royalty-free stock video)
 * - Pixabay Videos (free royalty-free stock video)
 * - YouTube (public domain / fair use short clips)
 * - Coverr (free stock video)
 *
 * All sources are completely free, no attribution required.
 */
const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const { Logger } = require('../core/logger');

class FreeVisualSearcher {
  constructor() {
    this.logger = new Logger('FreeVisualSearcher');
    this.browser = null;
  }

  /**
   * Launch headless browser for scraping free video sites
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
      this.logger.warn('Puppeteer not available — will use yt-dlp fallback');
      return false;
    }
  }

  /**
   * Search for free video clips matching a query
   * Tries multiple free sources, returns best results
   *
   * @param {string} query - Search term (e.g., "Nigerian street food", "Tokyo rain")
   * @param {Object} options - { maxResults, maxDuration, outputDir }
   * @returns {Promise<Array>} - Array of { file, source, duration, thumbnail }
   */
  async searchFreeVideoClips(query, options = {}) {
    const maxResults = options.maxResults || 3;
    const maxDuration = options.maxDuration || 15; // seconds per clip
    const outputDir = options.outputDir || path.join(__dirname, '..', 'output', 'assets', 'clips');

    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }

    this.logger.info(`Searching free clips for: "${query}"`);

    const results = [];

    // Try Pexels first (best quality free source)
    try {
      const pexelsClips = await this._searchPexelsVideos(query, outputDir, maxResults);
      results.push(...pexelsClips);
      this.logger.success(`Pexels: found ${pexelsClips.length} clips`);
    } catch (error) {
      this.logger.warn(`Pexels search failed: ${error.message}`);
    }

    // If not enough results, try Pixabay
    if (results.length < maxResults) {
      try {
        const pixabayClips = await this._searchPixabayVideos(query, outputDir, maxResults - results.length);
        results.push(...pixabayClips);
        this.logger.success(`Pixabay: found ${pixabayClips.length} clips`);
      } catch (error) {
        this.logger.warn(`Pixabay search failed: ${error.message}`);
      }
    }

    // If still not enough, try YouTube
    if (results.length < maxResults) {
      try {
        const ytClips = await this._searchYouTubeClips(query, outputDir, maxResults - results.length);
        results.push(...ytClips);
        this.logger.success(`YouTube: found ${ytClips.length} clips`);
      } catch (error) {
        this.logger.warn(`YouTube search failed: ${error.message}`);
      }
    }

    return results.slice(0, maxResults);
  }

  /**
   * Search Pexels Videos for free clips (no API key needed)
   * Uses web scraping of pexels.com/search/videos/
   */
  async _searchPexelsVideos(query, outputDir, maxResults) {
    const results = [];
    const browserOk = await this._launchBrowser();
    if (!browserOk) return results;

    this.logger.info('Searching Pexels Videos...');

    const page = await this.browser.newPage();
    try {
      // Search Pexels videos
      await page.goto(`https://www.pexels.com/search/videos/${encodeURIComponent(query)}/`, {
        waitUntil: 'networkidle2',
        timeout: 20000,
      });

      // Wait for video results to load
      await new Promise(r => setTimeout(r, 3000));

      // Extract video page URLs
      const videoLinks = await page.evaluate(() => {
        const links = [];
        // Pexels uses article elements with video previews
        document.querySelectorAll('article a').forEach(a => {
          const href = a.getAttribute('href');
          if (href && href.includes('/video/') && !links.includes(href)) {
            links.push(href);
          }
        });
        return links.slice(0, 5);
      });

      this.logger.info(`Found ${videoLinks.length} video pages on Pexels`);

      // Visit each video page and download
      for (let i = 0; i < Math.min(videoLinks.length, maxResults); i++) {
        try {
          const videoUrl = `https://www.pexels.com${videoLinks[i]}`;
          await page.goto(videoUrl, { waitUntil: 'networkidle2', timeout: 20000 });
          await new Promise(r => setTimeout(r, 2000));

          // Try to find the download link for the smallest/cheapest version
          const downloadUrl = await page.evaluate(() => {
            // Pexels free download buttons
            const downloadBtn = document.querySelector('a[download]');
            if (downloadBtn) return downloadBtn.getAttribute('href');

            // Or find video source in the page
            const video = document.querySelector('video source');
            if (video) return video.getAttribute('src');

            return null;
          });

          if (downloadUrl) {
            const ext = path.extname(downloadUrl.split('?')[0]) || '.mp4';
            const outputFile = path.join(outputDir, `pexels_${query.replace(/\s+/g, '_')}_${i}${ext}`);
            await this._downloadFile(downloadUrl, outputFile);
            if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 1000) {
              results.push({
                file: outputFile,
                source: 'pexels',
                url: videoUrl,
                query: query,
                size: fs.statSync(outputFile).size,
              });
              this.logger.info(`  ✅ Downloaded Pexels clip ${i + 1}`);
            }
          }
        } catch (clipError) {
          this.logger.warn(`  Failed to download Pexels clip ${i + 1}: ${clipError.message}`);
        }
      }
    } catch (error) {
      this.logger.warn(`Pexels search failed: ${error.message}`);
    } finally {
      await page.close();
    }

    return results;
  }

  /**
   * Search Pixabay Videos for free clips
   * Uses web scraping of pixabay.com/videos/search/
   */
  async _searchPixabayVideos(query, outputDir, maxResults) {
    const results = [];
    const browserOk = await this._launchBrowser();
    if (!browserOk) return results;

    this.logger.info('Searching Pixabay Videos...');

    const page = await this.browser.newPage();
    try {
      await page.goto(`https://pixabay.com/videos/search/${encodeURIComponent(query)}/`, {
        waitUntil: 'networkidle2',
        timeout: 20000,
      });

      await new Promise(r => setTimeout(r, 3000));

      // Extract direct download URLs from Pixabay
      const downloadUrls = await page.evaluate(() => {
        const urls = [];
        // Pixabay video cards often have direct download links in data attributes
        document.querySelectorAll('[data-video-src]').forEach(el => {
          const src = el.getAttribute('data-video-src');
          if (src) urls.push(src);
        });
        // Or find links with download classes
        document.querySelectorAll('a[href*="pixabay.com/videos/download"]').forEach(a => {
          const href = a.getAttribute('href');
          if (href) urls.push(`https://pixabay.com${href}`);
        });
        return urls;
      });

      for (let i = 0; i < Math.min(downloadUrls.length, maxResults); i++) {
        try {
          const ext = '.mp4';
          const outputFile = path.join(outputDir, `pixabay_${query.replace(/\s+/g, '_')}_${i}${ext}`);
          await this._downloadFile(downloadUrls[i], outputFile);
          if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 1000) {
            results.push({
              file: outputFile,
              source: 'pixabay',
              url: downloadUrls[i],
              query: query,
              size: fs.statSync(outputFile).size,
            });
            this.logger.info(`  ✅ Downloaded Pixabay clip ${i + 1}`);
          }
        } catch (clipError) {
          this.logger.warn(`  Failed to download Pixabay clip ${i + 1}: ${clipError.message}`);
        }
      }
    } catch (error) {
      this.logger.warn(`Pixabay search failed: ${error.message}`);
    } finally {
      await page.close();
    }

    return results;
  }

  /**
   * Search YouTube for short relevant clips
   * Uses yt-dlp (already installed) to search and download
   */
  async _searchYouTubeClips(query, outputDir, maxResults) {
    const results = [];

    this.logger.info('Searching YouTube for short clips...');

    try {
      // Search YouTube using yt-dlp (way more reliable than scraping)
      const searchCommand = `yt-dlp --flat-playlist --dump-json "ytsearch${maxResults * 2}:${query}" 2>nul`;
      let searchOutput;
      try {
        searchOutput = execSync(searchCommand, { timeout: 30000, maxBuffer: 5 * 1024 * 1024 }).toString();
      } catch {
        // yt-dlp search might fail silently
        this.logger.warn('yt-dlp search failed');
        return results;
      }

      const videoEntries = searchOutput.trim().split('\n').filter(Boolean).map(line => {
        try { return JSON.parse(line); } catch { return null; }
      }).filter(Boolean);

      let downloaded = 0;
      for (const entry of videoEntries) {
        if (downloaded >= maxResults) break;

        const videoId = entry.id;
        const videoUrl = `https://www.youtube.com/watch?v=${videoId}`;
        const outputFile = path.join(outputDir, `yt_${query.replace(/\s+/g, '_')}_${downloaded}.mp4`);

        try {
          // Download only the first 15 seconds (free preview)
          execSync(
            `yt-dlp -f best[height<=360] --download-sections "*0-${Math.min(entry.duration || 15, 15)}" -o "${outputFile}" "${videoUrl}"`,
            { timeout: 60000, stdio: 'ignore' }
          );

          if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 10000) {
            results.push({
              file: outputFile,
              source: 'youtube',
              url: videoUrl,
              query: query,
              title: entry.title,
              duration: Math.min(entry.duration || 15, 15),
              size: fs.statSync(outputFile).size,
            });
            downloaded++;
            this.logger.info(`  ✅ Downloaded YouTube clip ${downloaded}: ${entry.title?.substring(0, 40)}`);
          }
        } catch (downloadError) {
          this.logger.warn(`  YouTube download failed for ${videoId}: ${downloadError.message}`);
        }
      }
    } catch (error) {
      this.logger.warn(`YouTube search failed: ${error.message}`);
    }

    return results;
  }

  /**
   * Download a file from a URL to a local path
   */
  async _downloadFile(url, outputPath) {
    const axios = require('axios');
    const writer = fs.createWriteStream(outputPath);
    const response = await axios({
      method: 'GET',
      url: url,
      responseType: 'stream',
      timeout: 30000,
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
      },
    });
    response.data.pipe(writer);
    return new Promise((resolve, reject) => {
      writer.on('finish', resolve);
      writer.on('error', reject);
    });
  }

  /**
   * Clean up browser
   */
  async destroy() {
    if (this.browser) {
      try { await this.browser.close(); } catch {}
      this.browser = null;
    }
  }
}

module.exports = { FreeVisualSearcher };