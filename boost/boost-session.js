#!/usr/bin/env node
/**
 * Boost Session Worker — runs a single view session as a child process.
 * Spawned by boost-engine.js for each view.
 *
 * Uses YouTube's embed endpoint (/embed/ID) which is lighter and has
 * fewer bot detection signals. Falls back to no proxy if none available.
 *
 * argv: [videoUrl, proxyStr, ua, viewportW, viewportH, isMobile, behaviorSeed, watchSec]
 *
 * Behavior profiles for YouTube Shorts:
 * - 15% quick bounce (3-8s watch, scroll past)
 * - 25% short watch (10-20s)
 * - 30% full watch (20-40s)
 * - 20% full + rewatch (40-60s)
 * - 10% full + scroll to comments (50-60s + 5s)
 *
 * Extra behaviors:
 * - 25% browse YouTube first
 * - 40% scroll after video
 * - 15% click suggested next
 * - 10% like the video
 * - 5% subscribe to channel
 */
const puppeteer = require('puppeteer');

const [videoUrl, proxyStr, ua, viewportW, viewportH, isMobileStr, behaviorSeed, watchSec] = process.argv.slice(2);
const isMobile = isMobileStr === 'true';
const viewport = { width: parseInt(viewportW) || 390, height: parseInt(viewportH) || 844 };
const watchTime = parseInt(watchSec) || 20;

// Seeded random based on behaviorSeed
let seed = parseInt(behaviorSeed) || Date.now();
function seededRandom() {
  seed = (seed * 9301 + 49297) % 233280;
  return seed / 233280;
}
const rand = seededRandom;

async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

/**
 * Extract video ID from various YouTube URL formats
 */
function extractVideoId(url) {
  if (!url) return null;
  const patterns = [
    /(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/shorts\/)([a-zA-Z0-9_-]{11})/,
    /^([a-zA-Z0-9_-]{11})$/,
  ];
  for (const p of patterns) {
    const m = url.match(p);
    if (m) return m[1];
  }
  return null;
}

async function runSession() {
  let browser = null;
  let context = null;
  let page = null;

  try {
    const browserArgs = [
      '--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
      '--disable-gpu', '--disable-web-security',
      '--disable-features=IsolateOrigins,site-per-process',
      '--disable-blink-features=AutomationControlled',
      '--window-size=1920,1080',
      '--disable-infobars',
    ];

    // Use proxy if provided and valid
    const useProxy = proxyStr && proxyStr !== 'null' && proxyStr !== 'undefined' && proxyStr !== '';
    if (useProxy) {
      browserArgs.push(`--proxy-server=${proxyStr}`);
    }

    browser = await puppeteer.launch({
      headless: 'new',
      args: browserArgs,
      defaultViewport: null,
    });

    context = await browser.createBrowserContext();
    page = await context.newPage();

    // Set identity
    if (ua && ua !== 'null' && ua !== 'undefined') {
      await page.setUserAgent(ua);
    }
    await page.setViewport(viewport);
    if (isMobile) {
      await page.setExtraHTTPHeaders({
        'Accept-Language': rand() < 0.5 ? 'en-US,en;q=0.9' : 'en-GB,en;q=0.8,en-US;q=0.6',
      });
    }

    // Override webdriver detection
    await page.evaluateOnNewDocument(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
      Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
      // Override chrome runtime
      window.chrome = { runtime: {} };
    });

    // 25% chance: browse YouTube first
    if (rand() < 0.25) {
      try {
        const searchTerms = ['shorts', 'trending', 'funny videos', 'viral', 'music', 'comedy', 'dance', 'pranks', 'cute animals'];
        const term = searchTerms[Math.floor(rand() * searchTerms.length)];
        const homeUrl = isMobile
          ? `https://m.youtube.com/results?search_query=${term}`
          : `https://www.youtube.com/results?search_query=${term}`;
        await page.goto(homeUrl, { waitUntil: 'domcontentloaded', timeout: 15000 }).catch(() => {});
        await sleep(2000 + rand() * 3000);
        for (let s = 0; s < 2 + Math.floor(rand() * 3); s++) {
          await page.evaluate(() => window.scrollBy(0, 100 + Math.random() * 400)).catch(() => {});
          await sleep(500 + rand() * 1500);
        }
      } catch {}
    }

    // Use embed endpoint for lighter page and less bot detection
    const videoId = extractVideoId(videoUrl);
    const embedUrl = videoId
      ? `https://www.youtube.com/embed/${videoId}?autoplay=1&mute=1&loop=1`
      : videoUrl;

    // Load the video page
    await page.goto(embedUrl, { waitUntil: 'domcontentloaded', timeout: 20000 }).catch(() => {});

    // Wait for the video to actually start
    await sleep(3000 + rand() * 2000);

    // Try to find and interact with the video player
    try {
      // Wait for iframe or video element
      const videoSelector = 'video';
      const el = await page.$(videoSelector);
      if (el) {
        // Click to ensure playing
        try { await el.click(); await sleep(500); } catch {}
      }
    } catch {}

    // Try to click play on the embed player
    try {
      for (const sel of ['video', '.html5-video-player', '.ytp-play-button']) {
        const el = await page.$(sel);
        if (el) { try { await el.click(); await sleep(500); } catch {} break; }
      }
    } catch {}

    // Watch the video for watchTime seconds with human-like behavior
    const checkInterval = 3000;
    const checks = Math.floor(watchTime * 1000 / checkInterval);
    for (let c = 0; c < checks; c++) {
      await sleep(checkInterval);
      try {
        // Random mouse movement
        await page.mouse.move(
          50 + rand() * (viewport.width - 100),
          50 + rand() * Math.min(viewport.height, 500)
        );
      } catch {}
      // Every few checks, scroll a bit
      if (c > 0 && c % 2 === 0) {
        try { await page.evaluate(() => window.scrollBy(0, 50 + Math.random() * 100)); } catch {}
      }
    }

    // 40% chance: scroll after watching
    if (rand() < 0.40) {
      try {
        for (let s = 0; s < 1 + Math.floor(rand() * 3); s++) {
          await page.evaluate(() => window.scrollBy(0, 100 + Math.random() * 300)).catch(() => {});
          await sleep(500 + rand() * 1500);
        }
      } catch {}
    }

    // Done - report success if we didn't hit an obvious bot page
    const pageTitle = await page.title().catch(() => '');
    let pageUrl = '';
    try { pageUrl = page.url(); } catch {}
    const isBotPage = pageTitle.includes('captcha') || pageTitle.includes('unusual traffic') || pageUrl.includes('consent');

    if (page) try { await page.close(); } catch {}
    if (context) try { await context.close(); } catch {}
    if (browser) try { await browser.close(); } catch {}

    if (isBotPage) {
      process.send({ success: false, error: 'Bot page detected (consent/captcha)', videoUrl });
    } else {
      process.send({ success: true, watchTime, videoUrl });
    }
    process.exit(0);
  } catch (error) {
    if (page) try { await page.close(); } catch {}
    if (context) try { await context.close(); } catch {}
    if (browser) try { await browser.close(); } catch {}

    process.send({ success: false, error: error.message.substring(0, 100), videoUrl });
    process.exit(1);
  }
}

runSession();