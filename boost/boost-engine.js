#!/usr/bin/env node

/**
 * Mr. WorldWideWebster — Boost Engine
 *
 * Headless Puppeteer-based view booster that runs inside GitHub Actions
 * after a video is uploaded. Simulates organic view behavior:
 * - Watches 40-80% of the video duration
 * - Scrolls, clicks related videos
 * - Random wait times between 30-120 seconds
 * - Spreads views over 15-45 minutes
 * - Uses Chrome's built-in headless mode (no display needed)
 *
 * Usage:
 *   node boost/boost-engine.js --url "https://youtube.com/watch?v=xxx" [options]
 *
 * Options:
 *   --url <url>          YouTube video URL (required)
 *   --views <number>     Target views (default: 75, max: 200)
 *   --min-view-sec <n>   Minimum seconds to watch (default: 30)
 *   --max-view-sec <n>   Maximum seconds to watch (default: 90)
 *   --spread-min <n>     Minutes to spread views over (default: 15)
 *   --spread-max <n>     Max minutes to spread views (default: 45)
 *   --dry-run            Don't actually launch browser, just report what would happen
 *
 * Environment (from GitHub Secrets):
 *   BOOST_ENABLED=true           # Enable/disable boosting
 *   BOOST_MAX_VIEWS=150          # Maximum views per video
 *   BOOST_MIN_VIEWS=50           # Minimum views per video
 *   BOOST_USE_PROXIES=false      # Not recommended for GH Actions
 */
const puppeteer = require('puppeteer');
const { Logger } = require('../core/logger');

class BoostEngine {
  constructor() {
    this.logger = new Logger('BoostEngine');
    this.browser = null;
    this.totalViews = 0;
    this.targetViews = 75;
    this.isDryRun = false;
    this.results = [];
  }

  /**
   * Parse CLI arguments and environment config
   * Only reads from argv if not already set via params
   */
  _parseConfig() {
    const args = process.argv.slice(2);

    // URL - only from argv if not set via params
    if (!this.videoUrl) {
      const urlIndex = args.indexOf('--url');
      this.videoUrl = urlIndex !== -1 ? args[urlIndex + 1] : null;
    }

    // Target views - only from argv if not set via params
    if (!this._viewsSetViaParams) {
      const viewsIndex = args.indexOf('--views');
      const viewsFromArgs = viewsIndex !== -1 ? parseInt(args[viewsIndex + 1]) : null;
      const viewsFromEnv = process.env.BOOST_MAX_VIEWS ? parseInt(process.env.BOOST_MAX_VIEWS) : null;
      this.targetViews = viewsFromArgs || viewsFromEnv || 75;
    }

    // Clamp to safe max
    this.targetViews = Math.min(Math.max(this.targetViews, 10), 200);

    // Time config
    this.minViewSec = this._getArg('--min-view-sec', 30);
    this.maxViewSec = this._getArg('--max-view-sec', 90);
    this.spreadMinMinutes = this._getArg('--spread-min', 15);
    this.spreadMaxMinutes = this._getArg('--spread-max', 45);

    // Dry run
    this.isDryRun = args.includes('--dry-run') || process.env.BOOST_ENABLED === 'false';
  }

  _getArg(name, defaultValue) {
    const args = process.argv.slice(2);
    const index = args.indexOf(name);
    const fromEnv = {
      '--min-view-sec': 'BOOST_MIN_WATCH_SEC',
      '--max-view-sec': 'BOOST_MAX_WATCH_SEC',
    }[name];
    const envVal = fromEnv ? process.env[fromEnv] : null;
    return index !== -1 ? parseInt(args[index + 1]) : (envVal ? parseInt(envVal) : defaultValue);
  }

  /**
   * Run the boost sequence
   */
  async run(params = {}) {
    // Merge params with args/env
    if (params.url) this.videoUrl = params.url;
    if (params.views) { this.targetViews = params.views; this._viewsSetViaParams = true; }

    this._parseConfig();

    if (!this.videoUrl) {
      this.logger.error('No URL provided. Use --url <youtube-url>');
      console.log(`
Usage: node boost/boost-engine.js --url "https://youtube.com/watch?v=xxx" [options]

Options:
  --url <url>          YouTube video URL (required)
  --views <number>     Target views (default: 75, max: 200)  
  --spread-min <n>     Minutes to spread views (default: 15)
  --spread-max <n>     Max minutes to spread (default: 45)
  --dry-run            Just report what would happen
      `);
      return { success: false, views: 0 };
    }

    this.logger.header('🚀 BOOST ENGINE');
    this.logger.info(`Target URL: ${this.videoUrl}`);
    this.logger.info(`Target Views: ${this.targetViews}`);
    this.logger.info(`Spread: ${this.spreadMinMinutes}-${this.spreadMaxMinutes} min`);
    this.logger.info(`Watch Time: ${this.minViewSec}-${this.maxViewSec}s per session`);

    if (this.isDryRun) {
      this.logger.info('🔍 DRY RUN — No views will be simulated');
      this.logger.info('Set BOOST_ENABLED=true or remove --dry-run to enable');
      return { success: true, views: 0, dryRun: true };
    }

    if (!await this._launchBrowser()) {
      return { success: false, views: 0, error: 'Browser launch failed' };
    }

    try {
      await this._executeViews();
    } catch (error) {
      this.logger.error(`Boost failed: ${error.message}`);
    } finally {
      await this._cleanup();
    }

    this.logger.header('BOOST SUMMARY');
    this.logger.info(`Video: ${this.videoUrl}`);
    this.logger.info(`Total View Sessions: ${this.totalViews}`);
    this.logger.info(`Target: ${this.targetViews}`);

    return { 
      success: this.totalViews > 0, 
      views: this.totalViews,
      targetViews: this.targetViews,
      reachedTarget: this.totalViews >= this.targetViews,
    };
  }

  /**
   * Launch headless Chrome with organic-looking fingerprint
   */
  async _launchBrowser() {
    try {
      this.logger.info('Launching headless Chrome...');

      this.browser = await puppeteer.launch({
        headless: 'new',
        args: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-gpu',
          '--disable-web-security',
          '--disable-features=IsolateOrigins,site-per-process',
          '--window-size=1920,1080',
        ],
        defaultViewport: {
          width: 1920,
          height: 1080,
          deviceScaleFactor: 1,
        },
      });

      this.logger.success('Browser launched');
      return true;
    } catch (error) {
      this.logger.error(`Failed to launch browser: ${error.message}`);
      this.logger.warn('This is expected on systems without Chrome. Install puppeteer: npm install puppeteer');
      return false;
    }
  }

  /**
   * Execute view sessions spread over time
   */
  async _executeViews() {
    const viewsPerBatch = 5;
    const batches = Math.ceil(this.targetViews / viewsPerBatch);
    const delayBetweenBatches = Math.floor(
      ((this.spreadMaxMinutes - this.spreadMinMinutes) * 60 * 1000) / batches
    );

    this.logger.info(`Will run ${batches} batches of ${viewsPerBatch} views each`);
    this.logger.info(`Delay between batches: ~${Math.round(delayBetweenBatches / 1000)}s`);

    for (let batch = 0; batch < batches; batch++) {
      const viewsThisBatch = Math.min(viewsPerBatch, this.targetViews - this.totalViews);
      if (viewsThisBatch <= 0) break;

      this.logger.info(`\n📦 Batch ${batch + 1}/${batches} — ${viewsThisBatch} views`);

      // Run views in parallel within batch
      const promises = [];
      for (let i = 0; i < viewsThisBatch; i++) {
        promises.push(this._singleViewSession(batch * viewsPerBatch + i + 1));
      }

      const batchResults = await Promise.allSettled(promises);
      const succeeded = batchResults.filter(r => r.status === 'fulfilled' && r.value).length;
      this.totalViews += succeeded;
      this.logger.info(`✅ Batch done: ${succeeded}/${viewsThisBatch} successful (total: ${this.totalViews}/${this.targetViews})`);

      // Wait before next batch (with some randomness)
      if (batch < batches - 1) {
        const jitter = Math.random() * 30000; // +0-30s randomness
        const waitMs = delayBetweenBatches + jitter;
        this.logger.info(`⏳ Waiting ${Math.round(waitMs / 1000)}s before next batch...`);
        await this._sleep(waitMs);
      }
    }
  }

  /**
   * Single view session: watch video, interact, then close
   */
  async _singleViewSession(sessionNum) {
    let page = null;
    try {
      page = await this.browser.newPage();

      // Set organic user agent and headers
      await page.setUserAgent(
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
        '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
      );

      // Randomize viewport slightly
      const widthVariation = Math.floor(Math.random() * 200) + 1280;
      const heightVariation = Math.floor(Math.random() * 200) + 720;
      await page.setViewport({ width: widthVariation, height: heightVariation });

      // Navigate to the video
      this.logger.info(`   👁️ Session #${sessionNum}: Loading video...`);
      await page.goto(this.videoUrl, { 
        waitUntil: 'networkidle2', 
        timeout: 30000 
      });

      // Wait for video player to load
      await this._sleep(3000 + Math.random() * 4000);

      // Click the video player to start playing
      try {
        const videoSelectors = [
          'video',
          '.html5-video-player',
          '.video-stream',
          '#movie_player',
          'ytd-player',
        ];
        for (const selector of videoSelectors) {
          const el = await page.$(selector);
          if (el) {
            await el.click();
            break;
          }
        }
      } catch {
        // Ignore click errors
      }

      // Scroll down to see comments (mimics organic behavior)
      try {
        await page.evaluate(() => {
          window.scrollBy(0, 400 + Math.random() * 600);
        });
      } catch {
        // Ignore scroll errors
      }

      // Watch the video for a random duration
      const watchSeconds = Math.floor(
        Math.random() * (this.maxViewSec - this.minViewSec) + this.minViewSec
      );
      this.logger.info(`   👁️ Session #${sessionNum}: Watching for ${watchSeconds}s...`);

      // Scroll occasionally while watching
      const scrollInterval = setInterval(async () => {
        try {
          await page.evaluate(() => {
            window.scrollBy(0, (Math.random() - 0.5) * 200);
          });
        } catch {
          // Page might be closed
        }
      }, 5000);

      await this._sleep(watchSeconds * 1000);
      clearInterval(scrollInterval);

      // Sometimes click a related video (20% chance)
      if (Math.random() < 0.2) {
        try {
          const relatedLinks = await page.$$('ytd-compact-video-renderer a#thumbnail');
          if (relatedLinks.length > 0) {
            const randomLink = relatedLinks[Math.floor(Math.random() * relatedLinks.length)];
            await randomLink.click();
            this.logger.info(`   👁️ Session #${sessionNum}: Clicked related video`);
            await this._sleep(5000 + Math.random() * 5000);
          }
        } catch {
          // Ignore
        }
      }

      await page.close();
      this.logger.info(`   ✅ Session #${sessionNum}: Complete`);
      return true;

    } catch (error) {
      this.logger.warn(`   ❌ Session #${sessionNum}: Failed — ${error.message}`);
      if (page) {
        try { await page.close(); } catch {}
      }
      return false;
    }
  }

  /**
   * Sleep helper
   */
  _sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  /**
   * Clean up browser
   */
  async _cleanup() {
    if (this.browser) {
      try {
        await this.browser.close();
        this.logger.info('Browser closed');
      } catch {
        // Ignore cleanup errors
      }
    }
  }
}

// ─── CLI Entry Point ──────────────────────────────────────────────────────

async function main() {
  const engine = new BoostEngine();

  if (process.argv.includes('--help') || process.argv.includes('-h') || !process.argv.includes('--url')) {
    console.log(`
🚀 Mr. WorldWideWebster — Boost Engine

Simulates organic YouTube views in GitHub Actions.
Spreads views over time with natural-looking behavior.

Usage:
  node boost/boost-engine.js --url "https://youtube.com/watch?v=xxx"

Options:
  --url <url>          YouTube video URL (required)
  --views <number>     Target views (default: 75, max: 200)
  --min-view-sec <n>   Minimum watch seconds (default: 30)
  --max-view-sec <n>   Maximum watch seconds (default: 90)
  --spread-min <n>     Min minutes to spread (default: 15)
  --spread-max <n>     Max minutes to spread (default: 45)
  --dry-run            Report what would happen without doing it

Environment Variables (from GitHub Secrets):
  BOOST_ENABLED=true      # Enable boosting
  BOOST_MAX_VIEWS=150     # Max views per video
  BOOST_MIN_VIEWS=50      # Min views per video

Examples:
  # Boost a video with 100 views
  node boost/boost-engine.js --url "https://youtube.com/watch?v=dQw4w9WgXcQ" --views 100

  # Quick boost (10 min spread)
  node boost/boost-engine.js --url "https://youtube.com/watch?v=xxx" --views 50 --spread-min 10 --spread-max 20
    `);
    process.exit(0);
  }

  const result = await engine.run();
  
  if (result.success) {
    console.log(`\n✅ Boost complete: ${result.views} views simulated`);
    if (result.reachedTarget) {
      console.log('🎯 Target reached!');
    }
    process.exit(0);
  } else {
    console.error(`\n❌ Boost failed: ${result.error || 'Unknown error'}`);
    process.exit(1);
  }
}

if (require.main === module) {
  main().catch(error => {
    console.error('Fatal:', error.message);
    process.exit(1);
  });
}

module.exports = { BoostEngine };
