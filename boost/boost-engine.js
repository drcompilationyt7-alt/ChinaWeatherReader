#!/usr/bin/env node

/**
 * Mr. WorldWideWebster — Boost Engine
 *
 * Headless Puppeteer-based view booster that runs inside GitHub Actions
 * after a video is uploaded. Simulates organic view behavior.
 *
 * FIXED v2: When called programmatically via engine.run({url,views}),
 * the url param is stored BEFORE _parseConfig() runs so argv doesn't override it.
 *
 * Usage:
 *   node boost/boost-engine.js --url "https://youtube.com/watch?v=xxx" [options]
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
    this._urlSetViaParam = false;
    this._viewsSetViaParam = false;
  }

  /**
   * Run the boost sequence.
   * @param {Object} params - { url, views }
   */
  async run(params = {}) {
    // Store params BEFORE any argv parsing
    if (params.url) {
      this.videoUrl = params.url;
      this._urlSetViaParam = true;
    }
    if (params.views) {
      this.targetViews = Math.min(Math.max(parseInt(params.views) || 75, 10), 200);
      this._viewsSetViaParam = true;
    }

    // Parse argv for any missing values only
    this._parseConfig();

    if (!this.videoUrl) {
      this.logger.error('No URL provided. Use --url <youtube-url>');
      return { success: false, views: 0 };
    }

    this.logger.header('🚀 BOOST ENGINE');
    this.logger.info(`Target URL: ${this.videoUrl}`);
    this.logger.info(`Target Views: ${this.targetViews}`);
    this.logger.info(`Spread: ${this.spreadMinMinutes}-${this.spreadMaxMinutes} min`);
    this.logger.info(`Watch Time: ${this.minViewSec}-${this.maxViewSec}s per session`);

    if (this.isDryRun) {
      this.logger.info('🔍 DRY RUN — No views will be simulated');
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

  _parseConfig() {
    const args = process.argv.slice(2);

    // Only read from argv if NOT already set via programmatic params
    if (!this._urlSetViaParam) {
      const urlIndex = args.indexOf('--url');
      if (urlIndex !== -1) this.videoUrl = args[urlIndex + 1];
    }

    if (!this._viewsSetViaParam) {
      const viewsIndex = args.indexOf('--views');
      const viewsFromArgs = viewsIndex !== -1 ? parseInt(args[viewsIndex + 1]) : null;
      const viewsFromEnv = process.env.BOOST_MAX_VIEWS ? parseInt(process.env.BOOST_MAX_VIEWS) : null;
      this.targetViews = Math.min(Math.max(viewsFromArgs || viewsFromEnv || 75, 10), 200);
    }

    // Clamp
    this.targetViews = Math.min(Math.max(this.targetViews, 10), 200);

    // Time config (always from argv)
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

  async _launchBrowser() {
    try {
      this.logger.info('Launching headless Chrome...');
      this.browser = await puppeteer.launch({
        headless: 'new',
        args: [
          '--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
          '--disable-gpu', '--disable-web-security',
          '--disable-features=IsolateOrigins,site-per-process',
          '--window-size=1920,1080',
        ],
        defaultViewport: { width: 1920, height: 1080, deviceScaleFactor: 1 },
      });
      this.logger.success('Browser launched');
      return true;
    } catch (error) {
      this.logger.error(`Browser launch failed: ${error.message}`);
      return false;
    }
  }

  async _executeViews() {
    const viewsPerBatch = 5;
    const batches = Math.ceil(this.targetViews / viewsPerBatch);
    const delayBetweenBatches = Math.floor(
      ((this.spreadMaxMinutes - this.spreadMinMinutes) * 60 * 1000) / batches
    );
    this.logger.info(`Will run ${batches} batches of ${viewsPerBatch} views each`);

    for (let batch = 0; batch < batches; batch++) {
      const viewsThisBatch = Math.min(viewsPerBatch, this.targetViews - this.totalViews);
      if (viewsThisBatch <= 0) break;
      this.logger.info(`\n📦 Batch ${batch + 1}/${batches} — ${viewsThisBatch} views`);

      const promises = [];
      for (let i = 0; i < viewsThisBatch; i++) {
        promises.push(this._singleViewSession(batch * viewsPerBatch + i + 1));
      }
      const batchResults = await Promise.allSettled(promises);
      const succeeded = batchResults.filter(r => r.status === 'fulfilled' && r.value).length;
      this.totalViews += succeeded;
      this.logger.info(`✅ Batch done: ${succeeded}/${viewsThisBatch} (total: ${this.totalViews}/${this.targetViews})`);

      if (batch < batches - 1) {
        const jitter = Math.random() * 30000;
        const waitMs = delayBetweenBatches + jitter;
        this.logger.info(`⏳ Waiting ${Math.round(waitMs / 1000)}s...`);
        await this._sleep(waitMs);
      }
    }
  }

  async _singleViewSession(sessionNum) {
    let page = null;
    try {
      page = await this.browser.newPage();
      await page.setUserAgent(
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
      );
      const w = Math.floor(Math.random() * 200) + 1280;
      const h = Math.floor(Math.random() * 200) + 720;
      await page.setViewport({ width: w, height: h });

      this.logger.info(`   👁️ Session #${sessionNum}: Loading...`);
      await page.goto(this.videoUrl, { waitUntil: 'networkidle2', timeout: 30000 });
      await this._sleep(3000 + Math.random() * 4000);

      try {
        const selectors = ['video', '.html5-video-player', '#movie_player', 'ytd-player'];
        for (const sel of selectors) {
          const el = await page.$(sel);
          if (el) { await el.click(); break; }
        }
      } catch {}

      try {
        await page.evaluate(() => window.scrollBy(0, 400 + Math.random() * 600));
      } catch {}

      const watchSec = Math.floor(Math.random() * (this.maxViewSec - this.minViewSec) + this.minViewSec);
      this.logger.info(`   👁️ Session #${sessionNum}: Watching ${watchSec}s...`);
      await this._sleep(watchSec * 1000);

      if (Math.random() < 0.2) {
        try {
          const links = await page.$$('ytd-compact-video-renderer a#thumbnail');
          if (links.length > 0) {
            await links[Math.floor(Math.random() * links.length)].click();
            await this._sleep(5000 + Math.random() * 5000);
          }
        } catch {}
      }

      await page.close();
      this.logger.info(`   ✅ Session #${sessionNum}: Done`);
      return true;
    } catch (error) {
      this.logger.warn(`   ❌ Session #${sessionNum}: ${error.message}`);
      if (page) try { await page.close(); } catch {}
      return false;
    }
  }

  _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  async _cleanup() {
    if (this.browser) {
      try { await this.browser.close(); this.logger.info('Browser closed'); } catch {}
    }
  }
}

// ─── CLI Entry Point ──────────────────────────────────────────────────────
if (require.main === module) {
  const engine = new BoostEngine();
  if (process.argv.includes('--help') || process.argv.includes('-h') || !process.argv.includes('--url')) {
    console.log(`
Usage: node boost/boost-engine.js --url "https://youtube.com/watch?v=xxx" [options]
Options:
  --url <url>          YouTube video URL (required)
  --views <number>     Target views (default: 75, max: 200)
  --min-view-sec <n>   Minimum watch seconds (default: 30)
  --max-view-sec <n>   Maximum watch seconds (default: 90)
  --spread-min <n>     Min minutes to spread (default: 15)
  --spread-max <n>     Max minutes to spread (default: 45)
  --dry-run            Report what would happen
    `);
    process.exit(0);
  }
  engine.run().then(r => {
    if (r.success) { console.log(`\n✅ ${r.views} views`); process.exit(0); }
    else { console.error(`\n❌ Failed`); process.exit(1); }
  }).catch(e => { console.error(e.message); process.exit(1); });
}

module.exports = { BoostEngine };
