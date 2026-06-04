#!/usr/bin/env node

/**
 * Mr. WorldWideWebster — Boost Engine v7
 *
 * Fetches existing channel videos via yt-dlp, then boosts them
 * with human-like parallel sessions. Each session = unique identity
 * (proxy, UA, device, behavior). Proxy pool is tested before use.
 * 
 * Target: 1000 views per video with varied watch behavior:
 * - Some watch full 30s-3min, some watch twice
 * - Some bounce quickly, some browse YouTube first
 * - Simulated scrolling after viewing
 * - Sessions spread over time with Gaussian distribution
 *
 * Usage:
 *   node boost/boost-engine.js --channel "https://www.youtube.com/@channel"
 *   node boost/boost-engine.js --url "https://youtube.com/watch?v=xxx" --views 1000
 */
const { execSync } = require('child_process');
const puppeteer = require('puppeteer');
const { Logger } = require('../core/logger');

const logger = new Logger('BoostEngine');

const USER_AGENTS = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
  'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 OPR/109.0.0.0',
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1',
  'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.113 Mobile Safari/537.36',
  'Mozilla/5.0 (Linux; Android 13; Samsung Galaxy S23) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.118 Mobile Safari/537.36',
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

const VIEWPORT_PROFILES = [
  { width: 1920, height: 1080 },
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
  { width: 1536, height: 864 },
  { width: 1280, height: 720 },
  { width: 1600, height: 900 },
  { width: 390, height: 844, isMobile: true, hasTouch: true },
  { width: 430, height: 932, isMobile: true, hasTouch: true },
  { width: 412, height: 915, isMobile: true, hasTouch: true },
  { width: 360, height: 780, isMobile: true, hasTouch: true },
  { width: 768, height: 1024, isMobile: false },
  { width: 1024, height: 768, isMobile: false },
  { width: 820, height: 1180, isMobile: false },
];

class BoostEngine {
  constructor() {
    this.videoUrls = [];     // URLs to boost (fetched from channel or given)
    this.targetViews = 1000; // per video
    this.browser = null;
    this.totalViews = 0;
    this._proxyList = [];
    this._proxyIndex = 0;
    this._startedAt = null;
    this._completed = false;
  }

  async run(params = {}) {
    // Resolve channel URL from params or env var (YOUTUBE_USERNAME)
    let channelUrl = params.channel;
    if (!channelUrl && process.env.YOUTUBE_USERNAME) {
      channelUrl = `https://www.youtube.com/@${process.env.YOUTUBE_USERNAME}`;
      logger.info(`Using channel from YOUTUBE_USERNAME env: ${channelUrl}`);
    }

    if (channelUrl) {
      this.videoUrls = await this._fetchChannelVideos(channelUrl);
      if (this.videoUrls.length === 0) {
        logger.error('No videos found on channel');
        return { success: false, views: 0, error: 'No videos on channel' };
      }
      logger.success(`Found ${this.videoUrls.length} videos on channel`);
    } else if (params.url) {
      this.videoUrls = [params.url];
    }

    if (params.views) this.targetViews = Math.max(parseInt(params.views) || 1000, 100);

    // Target 1000 per video
    this.targetViews = 1000;

    if (this.videoUrls.length === 0) {
      logger.error('No URLs to boost. Use --channel <url> or --url <url>');
      return { success: false, views: 0 };
    }

    // Build and test proxy pool
    await this._buildProxyPool();

    logger.header('BOOST ENGINE v7');
    logger.info(`Videos to boost: ${this.videoUrls.length}`);
    logger.info(`Target per video: ${this.targetViews} views`);
    logger.info(`Total targets: ${this.videoUrls.length * this.targetViews} views`);
    logger.info(`Working proxies: ${this._proxyList.length}`);

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
    logger.info(`Total Views: ${this.totalViews}`);
    const elapsed = ((Date.now() - this._startedAt) / 1000 / 60).toFixed(1);
    logger.info(`Duration: ${elapsed} minutes`);

    return { success: this.totalViews > 0, views: this.totalViews };
  }

  /**
   * Fetch existing videos from YouTube channel using yt-dlp
   */
  async _fetchChannelVideos(channelUrl) {
    logger.info(`Fetching videos from channel: ${channelUrl}`);
    try {
      const out = execSync(
        `yt-dlp --flat-playlist --dump-json --playlist-end 20 "${channelUrl}" 2>/dev/null`,
        { timeout: 30000, encoding: 'utf8', maxBuffer: 5 * 1024 * 1024 }
      ).toString().trim();
      const lines = out.split('\n').filter(Boolean);
      const urls = [];
      for (const line of lines) {
        try {
          const p = JSON.parse(line);
          if (p.id && p.duration && p.duration <= 180) { // under 3 min = Shorts
            urls.push(`https://www.youtube.com/watch?v=${p.id}`);
          }
        } catch {}
      }
      // Shuffle to randomize boost order
      for (let i = urls.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [urls[i], urls[j]] = [urls[j], urls[i]];
      }
      return urls.slice(0, 10); // max 10 videos
    } catch (e) {
      logger.warn(`Channel fetch failed: ${e.message.substring(0, 100)}`);
      return [];
    }
  }

  /**
   * Build proxy pool with verification
   */
  async _buildProxyPool() {
    const raw = [];
    const seenIps = new Set();

    // Source 1: Proxifly
    try {
      const axios = require('axios');
      const resp = await axios.get('https://api.proxifly.dev/proxy?country=all&type=http&limit=50&format=json', {
        timeout: 10000,
        headers: { 'User-Agent': 'Mozilla/5.0' }
      });
      const data = resp.data;
      const list = data.proxies || data.data || (Array.isArray(data) ? data : []);
      for (const p of list) {
        const ip = p.ip || p.host;
        const port = p.port;
        if (ip && port && !seenIps.has(ip)) {
          seenIps.add(ip);
          raw.push({ ip, port: String(port) });
        }
      }
      logger.info(`Proxifly: ${list.length} raw proxies`);
    } catch (e) {
      logger.warn(`Proxifly: ${e.message.substring(0, 60)}`);
    }

    // Source 2: proxyscrape
    try {
      const axios = require('axios');
      const resp = await axios.get('https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all', { timeout: 5000 });
      const lines = resp.data.split('\n').filter(Boolean);
      for (const line of lines.slice(0, 100)) {
        const [ip, port] = line.trim().split(':');
        if (ip && port && !seenIps.has(ip)) {
          seenIps.add(ip);
          raw.push({ ip, port });
        }
      }
      logger.info(`proxyscrape: ${lines.length} raw`);
    } catch {}

    // Verify proxies concurrently (ping/connect test)
    logger.info(`Testing ${raw.length} proxies...`);
    const testConcurrency = 10;
    const verified = [];

    for (let i = 0; i < raw.length; i += testConcurrency) {
      const batch = raw.slice(i, i + testConcurrency);
      const results = await Promise.allSettled(
        batch.map(p => this._testProxy(p))
      );
      for (const r of results) {
        if (r.status === 'fulfilled' && r.value) {
          verified.push(r.value);
        }
      }
      if ((i + testConcurrency) % 50 === 0 || i + testConcurrency >= raw.length) {
        logger.info(`  Proxy test: ${verified.length} working / ${i + testConcurrency} tested`);
      }
    }

    // Shuffle verified proxies
    for (let i = verified.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [verified[i], verified[j]] = [verified[j], verified[i]];
    }

    this._proxyList = verified;
    logger.success(`Working proxies: ${verified.length}/${raw.length}`);
  }

  /**
   * Test if a proxy works by connecting to a fast endpoint
   */
  async _testProxy(proxy) {
    const net = require('net');
    const timeout = 5000;
    return new Promise(resolve => {
      const sock = new net.Socket();
      sock.setTimeout(timeout);
      sock.on('connect', () => {
        sock.destroy();
        resolve(proxy);
      });
      sock.on('error', () => {
        sock.destroy();
        resolve(false);
      });
      sock.on('timeout', () => {
        sock.destroy();
        resolve(false);
      });
      sock.connect(parseInt(proxy.port), proxy.ip);
    });
  }

  _gaussianRandom() {
    let u = 0, v = 0;
    while (u === 0) u = Math.random();
    while (v === 0) v = Math.random();
    return Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
  }

  _getProxy() {
    if (this._proxyList.length === 0) return null;
    const p = this._proxyList[this._proxyIndex % this._proxyList.length];
    this._proxyIndex++;
    return `http://${p.ip}:${p.port}`;
  }

  _getRandomURL() {
    if (this.videoUrls.length === 0) return null;
    // Weight toward videos that need more views
    return this.videoUrls[Math.floor(Math.random() * this.videoUrls.length)];
  }

  _getRandomUA() { return USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)]; }
  _getRandomLang() { return ACCEPT_LANGUAGES[Math.floor(Math.random() * ACCEPT_LANGUAGES.length)]; }
  _getRandomViewport() { return VIEWPORT_PROFILES[Math.floor(Math.random() * VIEWPORT_PROFILES.length)]; }
  _isMobile(viewport) { return viewport.width < 600; }

  /**
   * Generate varied watch time: 30s to 180s (3 min)
   * with weighted distribution toward shorter watches
   */
  _randomWatchTime() {
    const roll = Math.random();
    if (roll < 0.15) return 20 + Math.floor(Math.random() * 15);    // 15% quick bounce (20-35s)
    if (roll < 0.35) return 35 + Math.floor(Math.random() * 25);    // 20% short (35-60s)
    if (roll < 0.60) return 60 + Math.floor(Math.random() * 40);    // 25% medium (60-100s)
    if (roll < 0.80) return 100 + Math.floor(Math.random() * 50);   // 20% good (100-150s)
    return 150 + Math.floor(Math.random() * 31);                     // 20% full (150-180s)
  }

  /**
   * Distribution: ~30% watch twice (rewatch with different proxy)
   */
  _shouldRewatch() { return Math.random() < 0.30; }

  async _launchBrowser() {
    try {
      this.browser = await puppeteer.launch({
        headless: 'new',
        args: [
          '--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
          '--disable-gpu', '--disable-web-security',
          '--disable-features=IsolateOrigins,site-per-process',
          '--disable-blink-features=AutomationControlled',
          '--window-size=1920,1080',
        ],
        defaultViewport: null,
      });
      logger.success('Browser launched');
      return true;
    } catch (error) {
      logger.error(`Browser launch: ${error.message}`);
      return false;
    }
  }

  async _executeViews() {
    const sessionPromises = [];

    for (let i = 0; i < this.targetViews; i++) {
      const delayMs = this._getSessionDelay() * 1000;
      const promise = (async () => {
        try {
          await this._sleep(delayMs);
          if (this._completed) return;
          const url = this._getRandomURL();
          if (!url) return;

          const result = await this._singleViewSession(url);
          if (result) {
            this.totalViews++;
            logger.success(`Total: ${this.totalViews}/${this.targetViews}`);

            // ~30% chance to rewatch the same video with different identity
            if (this._shouldRewatch()) {
              await this._sleep(5000 + Math.random() * 10000);
              const rewatch = await this._singleViewSession(url);
              if (rewatch) {
                this.totalViews++;
                logger.success(`Total: ${this.totalViews}/${this.targetViews} (rewatch)`);
              }
            }
          }
        } catch (e) {
          logger.warn(`Session crashed: ${e.message.substring(0, 60)}`);
        }
      })();
      sessionPromises.push(promise);
    }

    logger.info(`Created ${sessionPromises.length} session slots`);
    await Promise.allSettled(sessionPromises);
    this._completed = true;
  }

  _getSessionDelay() {
    // Spread views over ~12 hours with Gaussian distribution, mean 45s apart
    const meanSec = 43; // 1000 views * 43s = ~12 hours
    const stdDevSec = 20;
    const raw = meanSec + this._gaussianRandom() * stdDevSec;
    return Math.max(15, Math.min(300, Math.round(raw)));
  }

  async _singleViewSession(videoUrl) {
    let context = null;
    let page = null;
    try {
      const proxyStr = this._getProxy();
      const ua = this._getRandomUA();
      const viewport = this._getRandomViewport();
      const isMobile = this._isMobile(viewport);
      const lang = this._getRandomLang();

      context = await this.browser.createBrowserContext();
      page = await context.newPage();
      await page.setUserAgent(ua);
      await page.setViewport(viewport);
      await page.setExtraHTTPHeaders({ 'Accept-Language': lang });
      await page.evaluateOnNewDocument(() => {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
      });

      const behavior = Math.random();
      logger.info(`  Session | ${proxyStr ? 'proxy' : 'direct'} | ${viewport.width}x${viewport.height}`);

      // 10%: Quick bounce (immediately scroll and leave)
      if (behavior < 0.10) {
        logger.info(`  Quick bounce`);
        await page.goto(videoUrl, { waitUntil: 'domcontentloaded', timeout: 20000 });
        await this._sleep(3000 + Math.random() * 5000);
        // Scroll a bit before leaving
        try { await page.evaluate(() => window.scrollBy(0, 100 + Math.random() * 300)); } catch {}
        await this._sleep(500 + Math.random() * 1000);
        await page.close(); await context.close();
        return true;
      }

      // 30%: Browse YouTube first (search results, scroll), then go to video
      if (behavior < 0.40) {
        try {
          logger.info(`  Browsing YouTube first...`);
          const searchTerms = ['shorts', 'trending', 'funny videos', 'viral', 'music', 'comedy', 'dance'];
          const term = searchTerms[Math.floor(Math.random() * searchTerms.length)];
          const homeUrl = isMobile
            ? `https://m.youtube.com/results?search_query=${term}`
            : `https://www.youtube.com/results?search_query=${term}`;
          await page.goto(homeUrl, { waitUntil: 'domcontentloaded', timeout: 20000 });
          await this._sleep(2000 + Math.random() * 4000);
          for (let s = 0; s < 2 + Math.floor(Math.random() * 4); s++) {
            await page.evaluate(() => window.scrollBy(0, 200 + Math.random() * 600));
            await this._sleep(800 + Math.random() * 2000);
          }
        } catch {}
      }

      // Load the actual video page
      const vUrl = isMobile && !videoUrl.includes('m.youtube')
        ? videoUrl.replace('www.youtube.com', 'm.youtube.com')
        : videoUrl;
      await page.goto(vUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await this._sleep(2000 + Math.random() * 3000);

      // Try to click play
      try {
        for (const sel of ['video', '.html5-video-player', '#movie_player', '.ytp-play-button']) {
          const el = await page.$(sel);
          if (el) { try { await el.click(); await this._sleep(500); } catch {} break; }
        }
      } catch {}

      // Watch the video with varied watch time
      const watchSec = this._randomWatchTime();
      logger.info(`  Watching ${watchSec}s...`);

      // Simulate watching with occasional mouse movement and scrolling
      const checkInterval = 3000;
      const checks = Math.floor(watchSec * 1000 / checkInterval);
      for (let c = 0; c < checks; c++) {
        await this._sleep(checkInterval);
        try {
          // Random mouse movement across the video player area
          await page.mouse.move(
            50 + Math.random() * (viewport.width - 100),
            50 + Math.random() * Math.min(viewport.height, 600)
          );
        } catch {}
        // Every few checks, scroll down to comments
        if (c > 0 && c % 3 === 0) {
          try { await page.evaluate(() => window.scrollBy(0, 80 + Math.random() * 200)); } catch {}
        }
      }

      // After watching, scroll through comments/suggestions
      if (Math.random() < 0.60) {
        try {
          logger.info(`  Scrolling after video...`);
          for (let s = 0; s < 2 + Math.floor(Math.random() * 4); s++) {
            await page.evaluate(() => window.scrollBy(0, 100 + Math.random() * 400));
            await this._sleep(800 + Math.random() * 2000);
          }
        } catch {}
      }

      // 25% chance to click a suggested video after
      if (Math.random() < 0.25) {
        try {
          const selectors = ['ytd-compact-video-renderer a#thumbnail', 'a.ytd-compact-video-renderer', 'ytd-rich-item-renderer a#thumbnail'];
          for (const sel of selectors) {
            const links = await page.$$(sel);
            if (links.length > 1) {
              const idx = 1 + Math.floor(Math.random() * Math.min(links.length - 1, 4));
              await links[idx].click();
              await this._sleep(2000 + Math.random() * 4000);
              break;
            }
          }
        } catch {}
      }

      await page.close(); await context.close();
      logger.info(`  Done (${watchSec}s watch)`);
      return true;
    } catch (error) {
      logger.warn(`  Session error: ${error.message.substring(0, 80)}`);
      if (page) try { await page.close(); } catch {}
      if (context) try { await context.close(); } catch {}
      return false;
    }
  }

  _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  async _cleanup() {
    await new Promise(r => setTimeout(r, 5000));
    if (this.browser) {
      try { await this.browser.close(); } catch {}
    }
  }
}

if (require.main === module) {
  const engine = new BoostEngine();
  const args = process.argv.slice(2);

  if (args.includes('--help') || args.includes('-h') || (!args.includes('--url') && !args.includes('--channel'))) {
    console.log(`
Usage:
  node boost/boost-engine.js --channel "https://www.youtube.com/@channel"
  node boost/boost-engine.js --url "https://youtube.com/watch?v=xxx" [--views 1000]

Options:
  --channel <url>     YouTube channel to boost (fetches videos via yt-dlp)
  --url <url>         Single video URL to boost
  --views <number>    Target views per video (default: 1000)
  --help              Show this help

Examples:
  node boost/boost-engine.js --channel "https://www.youtube.com/@drcompilationyt7"
  node boost/boost-engine.js --url "https://youtube.com/watch?v=abc123" --views 500
    `);
    process.exit(0);
  }

  const params = {};
  const urlIdx = args.indexOf('--url');
  if (urlIdx !== -1) params.url = args[urlIdx + 1];
  const channelIdx = args.indexOf('--channel');
  if (channelIdx !== -1) params.channel = args[channelIdx + 1];
  const viewsIdx = args.indexOf('--views');
  if (viewsIdx !== -1) params.views = parseInt(args[viewsIdx + 1]);

  engine.run(params).then(r => {
    if (r.success) { console.log(`\nDone: ${r.views} views`); process.exit(0); }
    else { console.error(`\nFailed`); process.exit(1); }
  }).catch(e => { console.error(e.message); process.exit(1); });
}

module.exports = { BoostEngine };