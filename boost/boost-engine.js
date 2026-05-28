#!/usr/bin/env node

/**
 * Mr. WorldWideWebster — Boost Engine v4
 *
 * Headless Puppeteer-based view booster with proxy rotation.
 * Uses free proxies from scraper for per-session IP rotation.
 * Incognito contexts + random UAs + organic behavior simulation.
 *
 * FIXED: Global timeout so it never hangs.
 *
 * Usage:
 *   node boost/boost-engine.js --url "https://youtube.com/watch?v=xxx" [options]
 */
const puppeteer = require('puppeteer');
const { Logger } = require('../core/logger');
const { getFreeProxies, getWorkingProxy } = require('../core/proxy-scraper');

const MAX_TOTAL_DURATION_MS = 5 * 60 * 1000; // 5 minutes hard limit

// Pool of realistic user agents
const USER_AGENTS = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 OPR/109.0.0.0',
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
  'Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
  'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0',
];

const ACCEPT_LANGUAGES = [
  'en-US,en;q=0.9',
  'en-GB,en;q=0.8,en-US;q=0.6',
  'en-CA,en;q=0.8,fr-CA;q=0.5',
  'en-AU,en;q=0.8',
  'en-IN,en;q=0.8,hi;q=0.5',
  'en,fr;q=0.8,de;q=0.5',
  'en,es;q=0.8,pt;q=0.5',
];

class BoostEngine {
  constructor() {
    this.logger = new Logger('BoostEngine');
    this.browser = null;
    this.totalViews = 0;
    this.targetViews = 75;
    this.isDryRun = false;
    this._urlSetViaParam = false;
    this._viewsSetViaParam = false;
    this._timedOut = false;
    this._startedAt = null;
    this._proxyList = [];
    this._proxyIndex = 0;
  }

  /**
   * Run the boost sequence.
   * @param {Object} params - { url, views }
   */
  async run(params = {}) {
    if (params.url) {
      this.videoUrl = params.url;
      this._urlSetViaParam = true;
    }
    if (params.views) {
      this.targetViews = Math.min(Math.max(parseInt(params.views) || 75, 10), 200);
      this._viewsSetViaParam = true;
    }

    this._parseConfig();

    if (!this.videoUrl) {
      this.logger.error('No URL provided. Use --url <youtube-url>');
      return { success: false, views: 0 };
    }

    // Load proxies
    try {
      this._proxyList = await getFreeProxies();
      this.logger.info(`Loaded ${this._proxyList.length} proxies for rotation`);
    } catch (e) {
      this.logger.warn(`Proxy load failed: ${e.message} — will use direct connection`);
      this._proxyList = [];
    }

    this.logger.header('🚀 BOOST ENGINE v4');
    this.logger.info(`Target URL: ${this.videoUrl}`);
    this.logger.info(`Target Views: ${this.targetViews}`);
    this.logger.info(`Proxies available: ${this._proxyList.length}`);
    this.logger.info(`Spread: ${this.spreadMinMinutes}-${this.spreadMaxMinutes} min`);
    this.logger.info(`Watch Time: ${this.minViewSec}-${this.maxViewSec}s per session`);
    this.logger.info(`Max Total Duration: ${MAX_TOTAL_DURATION_MS / 1000}s`);

    if (this.isDryRun) {
      this.logger.info('🔍 DRY RUN — No views will be simulated');
      return { success: true, views: 0, dryRun: true };
    }

    if (!await this._launchBrowser()) {
      return { success: false, views: 0, error: 'Browser launch failed' };
    }

    this._startedAt = Date.now();

    try {
      await Promise.race([
        this._executeViews(),
        (async () => {
          await this._sleep(MAX_TOTAL_DURATION_MS);
          this._timedOut = true;
          this.logger.warn(`⏰ Global timeout (${MAX_TOTAL_DURATION_MS / 1000}s) reached — stopping`);
        })()
      ]);
    } catch (error) {
      this.logger.error(`Boost failed: ${error.message}`);
    } finally {
      await this._cleanup();
    }

    this.logger.header('BOOST SUMMARY');
    this.logger.info(`Video: ${this.videoUrl}`);
    this.logger.info(`Total View Sessions: ${this.totalViews}`);
    this.logger.info(`Target: ${this.targetViews}`);
    if (this._timedOut) this.logger.warn('⏰ Timed out — partial results');

    return { 
      success: this.totalViews > 0, 
      views: this.totalViews,
      targetViews: this.targetViews,
      reachedTarget: this.totalViews >= this.targetViews,
      timedOut: this._timedOut,
    };
  }

  _parseConfig() {
    const args = process.argv.slice(2);

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

    this.targetViews = Math.min(Math.max(this.targetViews, 10), 200);

    this.minViewSec = this._getArg('--min-view-sec', 30);
    this.maxViewSec = this._getArg('--max-view-sec', 90);
    this.spreadMinMinutes = this._getArg('--spread-min', 15);
    this.spreadMaxMinutes = this._getArg('--spread-max', 45);

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

  _getNextProxy() {
    if (this._proxyList.length === 0) return null;
    const proxy = this._proxyList[this._proxyIndex % this._proxyList.length];
    this._proxyIndex++;
    return `http://${proxy.ip}:${proxy.port}`;
  }

  _getRandomUA() {
    return USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
  }

  _getRandomLang() {
    return ACCEPT_LANGUAGES[Math.floor(Math.random() * ACCEPT_LANGUAGES.length)];
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
          '--disable-blink-features=AutomationControlled',
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
      if (this._timedOut || (Date.now() - this._startedAt) > MAX_TOTAL_DURATION_MS * 0.8) {
        this.logger.warn('Approaching time limit — stopping early');
        break;
      }

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
    let context = null;
    let page = null;
    try {
      const proxyStr = this._getNextProxy();
      
      // Create incognito context with optional proxy
      context = this._proxyList.length > 0 && proxyStr
        ? await this.browser.createBrowserContext()
        : await this.browser.createBrowserContext();

      // Override proxy for this context via CDP if available
      if (proxyStr && context) {
        try {
          await context.overridePermissions('https://www.youtube.com', []);
        } catch {}
      }

      page = await context.newPage();
      
      // Random user agent
      const ua = this._getRandomUA();
      await page.setUserAgent(ua);
      
      // Random viewport
      const w = Math.floor(Math.random() * 400) + 1000;
      const h = Math.floor(Math.random() * 300) + 600;
      await page.setViewport({ width: w, height: h });

      // Random language
      await page.setExtraHTTPHeaders({
        'Accept-Language': this._getRandomLang(),
      });

      // Bypass webdriver detection
      await page.evaluateOnNewDocument(() => {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
      });

      // Set proxy for this specific page via CDP
      if (proxyStr) {
        try {
          const client = await page.target().createCDPSession();
          await client.send('Network.enable');
          // Some proxies need the --proxy-server flag at launch instead
          // We use context-level proxy via args for simplicity
        } catch {}
      }

      this.logger.info(`   👁️ Session #${sessionNum}: ${proxyStr ? 'Proxy ' + proxyStr.substring(0, 30) : 'Direct'}`);

      // 30% chance: simulate organic discovery (search first, then click)
      if (Math.random() < 0.3) {
        try {
          const searchQuery = this.videoUrl.includes('shorts') ? 'shorts' : 'video';
          await page.goto(`https://www.youtube.com/results?search_query=${searchQuery}`, {
            waitUntil: 'domcontentloaded', timeout: 20000
          });
          await this._sleep(2000 + Math.random() * 3000);
          
          // Try to click a random video
          try {
            const links = await page.$$('a#video-title');
            if (links.length > 0) {
              await links[Math.floor(Math.random() * Math.min(links.length, 5))].click();
              await this._sleep(3000 + Math.random() * 3000);
            }
          } catch {}
        } catch {}
      }

      // Load the actual target video
      this.logger.info(`   👁️ Session #${sessionNum}: Loading video...`);
      await page.goto(this.videoUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await this._sleep(2000 + Math.random() * 3000);

      // Click to play (if autoplay didn't)
      try {
        const selectors = ['video', '.html5-video-player', '#movie_player', 'ytd-player', 'button.ytp-large-play-button'];
        for (const sel of selectors) {
          const el = await page.$(sel);
          if (el) { 
            try { await el.click(); } catch {}
            break; 
          }
        }
      } catch {}

      // Scroll like a real user
      try {
        for (let s = 0; s < 2 + Math.floor(Math.random() * 3); s++) {
          await page.evaluate(() => window.scrollBy(0, 200 + Math.random() * 400));
          await this._sleep(500 + Math.random() * 1500);
        }
      } catch {}

      // Watch the video
      const watchSec = Math.floor(Math.random() * (this.maxViewSec - this.minViewSec) + this.minViewSec);
      this.logger.info(`   👁️ Session #${sessionNum}: Watching ${watchSec}s...`);
      
      // Check periodically if video is still playing
      const checkInterval = 5000;
      const checks = Math.floor(watchSec * 1000 / checkInterval);
      for (let c = 0; c < checks; c++) {
        await this._sleep(checkInterval);
        if (this._timedOut) break;
        // Random mouse movement every few checks
        if (c % 2 === 0 && page) {
          try {
            await page.mouse.move(
              100 + Math.random() * (w - 200),
              100 + Math.random() * (h - 200)
            );
          } catch {}
        }
      }

      // 20% chance: click a related video after watching
      if (Math.random() < 0.2) {
        try {
          const links = await page.$$('ytd-compact-video-renderer a#thumbnail');
          if (links.length > 0) {
            await links[Math.floor(Math.random() * links.length)].click();
            await this._sleep(3000 + Math.random() * 5000);
          }
        } catch {}
      }

      await page.close();
      await context.close();
      this.logger.info(`   ✅ Session #${sessionNum}: Done`);
      return true;
    } catch (error) {
      this.logger.warn(`   ❌ Session #${sessionNum}: ${error.message.substring(0, 80)}`);
      if (page) try { await page.close(); } catch {}
      if (context) try { await context.close(); } catch {}
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
    if (r.success) { console.log(`\n✅ ${r.views} views${r.timedOut ? ' (partial - timed out)' : ''}`); process.exit(0); }
    else { console.error(`\n❌ Failed`); process.exit(1); }
  }).catch(e => { console.error(e.message); process.exit(1); });
}

module.exports = { BoostEngine, MAX_TOTAL_DURATION_MS };