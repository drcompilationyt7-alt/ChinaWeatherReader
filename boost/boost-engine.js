#!/usr/bin/env node

/**
 * Mr. WorldWideWebster — Boost Engine v5
 *
 * Human-like view booster with parallel incognito sessions.
 * Each session = unique user identity (different proxy, UA, device, behavior).
 * Views spread over 2 hours with Gaussian delay distribution.
 * No hard timeout — runs until target views reached or all sessions done.
 *
 * NOTE: Currently disabled for testing. Set config.boost.enabled = true or
 * BOOST_ENABLED=true env var to enable.
 *
 * Usage:
 *   node boost/boost-engine.js --url "https://youtube.com/watch?v=xxx" [options]
 */
const puppeteer = require('puppeteer');
const { Logger } = require('../core/logger');
const { getFreeProxies } = require('../core/proxy-scraper');

const logger = new Logger('BoostEngine');

// Pool of realistic user agents (desktop + mobile + tablet mix)
const USER_AGENTS = [
  // Windows Chrome
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
  // Windows Firefox
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
  // Windows Edge
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0',
  // macOS Safari
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  // Linux Chrome
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
  'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0',
  // Opera
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 OPR/109.0.0.0',
  // Mobile iPhone
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1',
  // Mobile Android Chrome
  'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.113 Mobile Safari/537.36',
  'Mozilla/5.0 (Linux; Android 13; Samsung Galaxy S23) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.118 Mobile Safari/537.36',
  // iPad
  'Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
];

const ACCEPT_LANGUAGES = [
  'en-US,en;q=0.9',
  'en-GB,en;q=0.8,en-US;q=0.6',
  'en-CA,en;q=0.8,fr-CA;q=0.5',
  'en-AU,en;q=0.8',
  'en-IN,en;q=0.8,hi;q=0.5',
  'en,fr;q=0.8,de;q=0.5',
  'en,es;q=0.8,pt;q=0.5',
  'en-US,en;q=0.7,es;q=0.3',
  'en-GB,en;q=0.9,fr;q=0.4',
];

// Platform-specific viewport profiles
const VIEWPORT_PROFILES = [
  // Desktop
  { width: 1920, height: 1080 },
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
  { width: 1536, height: 864 },
  { width: 1280, height: 720 },
  { width: 1600, height: 900 },
  // Mobile
  { width: 390, height: 844, isMobile: true, hasTouch: true },   // iPhone 14 Pro
  { width: 430, height: 932, isMobile: true, hasTouch: true },   // iPhone 15 Pro Max
  { width: 412, height: 915, isMobile: true, hasTouch: true },   // Android large
  { width: 360, height: 780, isMobile: true, hasTouch: true },   // Android small
  { width: 768, height: 1024, isMobile: false },                 // iPad portrait
  { width: 1024, height: 768, isMobile: false },                 // iPad landscape
];

class BoostEngine {
  constructor() {
    this.videoUrl = null;
    this.targetViews = 75;
    this.browser = null;
    this.totalViews = 0;
    this._proxyList = [];
    this._proxyIndex = 0;
    this._startedAt = null;
    this._sessionsRunning = 0;
    this._completed = false;
  }

  /**
   * Run the boost sequence with parallel sessions.
   * Each session runs in its own incognito context with unique identity.
   * Sessions are started with staggered delays (0 to 2 hours) so views
   * spread naturally over time.
   */
  async run(params = {}) {
    if (params.url) this.videoUrl = params.url;
    if (params.views) this.targetViews = Math.min(Math.max(parseInt(params.views) || 75, 10), 200);

    this._parseConfig();

    if (!this.videoUrl) {
      logger.error('No URL provided. Use --url <youtube-url>');
      return { success: false, views: 0 };
    }

    // Load proxies
    try {
      this._proxyList = await getFreeProxies();
      logger.info(`Loaded ${this._proxyList.length} proxies for rotation`);
    } catch (e) {
      logger.warn(`Proxy load failed: ${e.message} — will use direct connections`);
      this._proxyList = [];
    }

    logger.header('🚀 BOOST ENGINE v5');
    logger.info(`Target URL: ${this.videoUrl}`);
    logger.info(`Target Views: ${this.targetViews}`);
    logger.info(`Proxies: ${this._proxyList.length}`);
    logger.info(`Spread: 2 hours max`);

    if (!await this._launchBrowser()) {
      return { success: false, views: 0, error: 'Browser launch failed' };
    }

    this._startedAt = Date.now();

    try {
      await this._executeViews();
    } catch (error) {
      logger.error(`Boost error: ${error.message}`);
    } finally {
      await this._cleanup();
    }

    logger.header('BOOST SUMMARY');
    logger.info(`Video: ${this.videoUrl}`);
    logger.info(`Total Views: ${this.totalViews}/${this.targetViews}`);
    const elapsed = ((Date.now() - this._startedAt) / 1000 / 60).toFixed(1);
    logger.info(`Duration: ${elapsed} minutes`);

    return {
      success: this.totalViews > 0,
      views: this.totalViews,
      targetViews: this.targetViews,
      reachedTarget: this.totalViews >= this.targetViews,
    };
  }

  _parseConfig() {
    const args = process.argv.slice(2);

    if (!this.videoUrl) {
      const urlIdx = args.indexOf('--url');
      if (urlIdx !== -1) this.videoUrl = args[urlIdx + 1];
    }

    const viewsFromArgs = args.indexOf('--views') !== -1 ? parseInt(args[args.indexOf('--views') + 1]) : null;
    const viewsFromEnv = process.env.BOOST_MAX_VIEWS ? parseInt(process.env.BOOST_MAX_VIEWS) : null;
    this.targetViews = Math.min(Math.max(viewsFromArgs || viewsFromEnv || 75, 10), 200);
  }

  /**
   * Gaussian random helper (Box-Muller transform)
   * Returns value with mean ~0, stddev ~1
   */
  _gaussianRandom() {
    let u = 0, v = 0;
    while (u === 0) u = Math.random();
    while (v === 0) v = Math.random();
    return Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
  }

  /**
   * Generate a random delay between sessions with Gaussian distribution
   * Mean: ~90 seconds, StdDev: ~45 seconds, Clamped: 10-300 seconds
   */
  _randomSessionDelay() {
    const meanSec = 90;
    const stdDevSec = 45;
    const raw = meanSec + this._gaussianRandom() * stdDevSec;
    return Math.max(10, Math.min(300, Math.round(raw)));
  }

  /**
   * Generate a random watch time with Gaussian distribution
   * Mean: 35 seconds, StdDev: 15 seconds, Clamped: 15-90 seconds
   */
  _randomWatchTime() {
    const mean = 35;
    const stdDev = 15;
    const raw = mean + this._gaussianRandom() * stdDev;
    return Math.max(15, Math.min(90, Math.round(raw)));
  }

  _getProxy() {
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

  _getRandomViewport() {
    return VIEWPORT_PROFILES[Math.floor(Math.random() * VIEWPORT_PROFILES.length)];
  }

  /**
   * Determine if this session is mobile (affects YouTube page behavior)
   */
  _isMobile(viewport) {
    return viewport.width < 600;
  }

  async _launchBrowser() {
    try {
      logger.info('Launching headless Chrome...');
      this.browser = await puppeteer.launch({
        headless: 'new',
        args: [
          '--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
          '--disable-gpu', '--disable-web-security',
          '--disable-features=IsolateOrigins,site-per-process',
          '--disable-blink-features=AutomationControlled',
          '--window-size=1920,1080',
        ],
        defaultViewport: null, // We set per-context viewport
      });
      logger.success('Browser launched');
      return true;
    } catch (error) {
      logger.error(`Browser launch: ${error.message}`);
      return false;
    }
  }

  /**
   * Execute all view sessions in parallel with staggered start times.
   * Each session is independent — runs in its own incognito context.
   * No timeout — all sessions will eventually complete or fail naturally.
   */
  async _executeViews() {
    // Create all session promises with staggered delays
    const sessionPromises = [];

    for (let i = 0; i < this.targetViews; i++) {
      const delayMs = this._randomSessionDelay() * 1000;
      const sessionNum = i + 1;

      // Create a promise that waits then runs the session
      const promise = (async () => {
        // Increment running counter
        this._sessionsRunning++;

        try {
          // Wait for staggered start
          await this._sleep(delayMs);

          // Check if we already have enough views
          if (this.totalViews >= this.targetViews || this._completed) {
            this._sessionsRunning--;
            return;
          }

          // Run the session
          const result = await this._singleViewSession(sessionNum);
          if (result) {
            this.totalViews++;
            logger.success(`✅ Total: ${this.totalViews}/${this.targetViews}`);
          }
        } catch (e) {
          logger.warn(`Session ${sessionNum} crashed: ${e.message.substring(0, 60)}`);
        } finally {
          this._sessionsRunning--;
        }
      })();

      sessionPromises.push(promise);
    }

    logger.info(`Created ${sessionPromises.length} session promises with staggered delays`);

    // Wait for all sessions to complete (no timeout)
    await Promise.allSettled(sessionPromises);
    this._completed = true;
  }

  async _singleViewSession(sessionNum) {
    let context = null;
    let page = null;

    try {
      const proxyStr = this._getProxy();
      const ua = this._getRandomUA();
      const viewport = this._getRandomViewport();
      const isMobile = this._isMobile(viewport);
      const lang = this._getRandomLang();

      // Create incognito context
      context = await this.browser.createBrowserContext();

      // Create page
      page = await context.newPage();

      // Set user agent (different per session)
      await page.setUserAgent(ua);

      // Set viewport (randomized per session)
      await page.setViewport(viewport);

      // Set language
      await page.setExtraHTTPHeaders({ 'Accept-Language': lang });

      // Bypass webdriver detection
      await page.evaluateOnNewDocument(() => {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
      });

      // Determine behavior profile for this session
      const behavior = Math.random();

      logger.info(`   👁️ #${sessionNum} | ${proxyStr ? '🔒' : '🔓'} | ${viewport.width}x${viewport.height} | ${isMobile ? '📱' : '💻'}`);

      // 10% chance: bounce — view <15 seconds then leave
      if (behavior < 0.10) {
        logger.info(`   👁️ #${sessionNum}: Quick bounce`);
        await page.goto(this.videoUrl, { waitUntil: 'domcontentloaded', timeout: 20000 });
        await this._sleep(5000 + Math.random() * 8000);
        await page.close();
        await context.close();
        return true; // Still counts as a view (just a short one)
      }

      // 30% chance: browse YouTube homepage first (organic discovery)
      if (behavior < 0.40) {
        try {
          logger.info(`   👁️ #${sessionNum}: Browsing YouTube first...`);
          const searchQuery = this.videoUrl.includes('shorts') ? 'shorts' : 'trending';
          const homeUrl = isMobile
            ? `https://m.youtube.com/results?search_query=${searchQuery}`
            : `https://www.youtube.com/results?search_query=${searchQuery}`;

          await page.goto(homeUrl, { waitUntil: 'domcontentloaded', timeout: 20000 });
          await this._sleep(2000 + Math.random() * 4000);

          // Scroll a bit
          for (let s = 0; s < 2 + Math.floor(Math.random() * 3); s++) {
            await page.evaluate(() => window.scrollBy(0, 300 + Math.random() * 500));
            await this._sleep(1000 + Math.random() * 2000);
          }
        } catch {}
      }

      // 15% chance: search for related content first
      if (behavior >= 0.40 && behavior < 0.55) {
        try {
          const randomQuery = ['viral', 'trending', 'shorts', 'funny', 'amazing'][Math.floor(Math.random() * 5)];
          const searchUrl = isMobile
            ? `https://m.youtube.com/results?search_query=${randomQuery}`
            : `https://www.youtube.com/results?search_query=${randomQuery}`;

          await page.goto(searchUrl, { waitUntil: 'domcontentloaded', timeout: 20000 });
          await this._sleep(3000 + Math.random() * 5000);
        } catch {}
      }

      // Load the actual target video
      logger.info(`   👁️ #${sessionNum}: Loading video...`);
      const videoUrl = isMobile && !this.videoUrl.includes('m.youtube')
        ? this.videoUrl.replace('www.youtube.com', 'm.youtube.com')
        : this.videoUrl;

      await page.goto(videoUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await this._sleep(2000 + Math.random() * 3000);

      // Click to play (if autoplay didn't trigger)
      try {
        const playSelectors = [
          'video',
          '.html5-video-player',
          '#movie_player',
          'ytd-player',
          'button.ytp-large-play-button',
          '.ytp-play-button',
        ];
        for (const sel of playSelectors) {
          const el = await page.$(sel);
          if (el) {
            try { await el.click(); await this._sleep(500); } catch {}
            break;
          }
        }
      } catch {}

      // Scroll like a real user
      try {
        const scrollCount = 1 + Math.floor(Math.random() * 4);
        for (let s = 0; s < scrollCount; s++) {
          const scrollAmount = isMobile ? 100 + Math.random() * 200 : 200 + Math.random() * 400;
          await page.evaluate(() => window.scrollBy(0, scrollAmount));
          await this._sleep(500 + Math.random() * 1500);
        }
      } catch {}

      // Watch the video with Gaussian-distributed time
      const watchSec = this._randomWatchTime();
      logger.info(`   👁️ #${sessionNum}: Watching ${watchSec}s...`);

      // We don't actually wait the full watch time — we check periodically
      // and simulate interaction
      const checkInterval = 3000;
      const checks = Math.floor(watchSec * 1000 / checkInterval);

      for (let c = 0; c < checks; c++) {
        await this._sleep(checkInterval);

        // Random mouse movement every check
        try {
          const x = 100 + Math.random() * (viewport.width - 200);
          const y = 100 + Math.random() * (viewport.height - 200);
          await page.mouse.move(x, y);
        } catch {}

        // Every 3rd check: scroll a little
        if (c % 3 === 0) {
          try {
            await page.evaluate(() => window.scrollBy(0, 50 + Math.random() * 150));
          } catch {}
        }
      }

      // 20% chance: click a related video after watching
      if (Math.random() < 0.20) {
        try {
          const relatedSelectors = [
            'ytd-compact-video-renderer a#thumbnail',
            '.ytd-compact-video-renderer a',
            'a[href*="/watch?v="]',
          ];
          for (const sel of relatedSelectors) {
            const links = await page.$$(sel);
            if (links.length > 1) {
              // Pick a random link (not the first one — that's often the same video)
              const link = links[1 + Math.floor(Math.random() * Math.min(links.length - 1, 5))];
              await link.click();
              await this._sleep(2000 + Math.random() * 4000);
              break;
            }
          }
        } catch {}
      }

      await page.close();
      await context.close();
      logger.info(`   ✅ #${sessionNum}: Done (${watchSec}s watch)`);
      return true;

    } catch (error) {
      logger.warn(`   ❌ #${sessionNum}: ${error.message.substring(0, 80)}`);
      if (page) try { await page.close(); } catch {}
      if (context) try { await context.close(); } catch {}
      return false;
    }
  }

  _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  async _cleanup() {
    // Wait for all sessions to finish
    logger.info('Waiting for sessions to finish...');
    await new Promise(r => setTimeout(r, 5000));

    if (this.browser) {
      try { await this.browser.close(); logger.info('Browser closed'); } catch {}
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
  --help               Show this help
    `);
    process.exit(0);
  }
  engine.run().then(r => {
    if (r.success) { console.log(`\n✅ ${r.views} views (target: ${r.targetViews})`); process.exit(0); }
    else { console.error(`\n❌ Failed`); process.exit(1); }
  }).catch(e => { console.error(e.message); process.exit(1); });
}

module.exports = { BoostEngine };