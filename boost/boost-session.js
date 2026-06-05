#!/usr/bin/env node
/**
 * Boost Session Worker — runs a single puppeteer view session as a child process.
 * Spawned by boost-engine.js for each proxy/view.
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
    ];

    // Use proxy if provided
    if (proxyStr && proxyStr !== 'null' && proxyStr !== 'undefined') {
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
    await page.evaluateOnNewDocument(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    });

    // 25% chance: browse YouTube first (search, scroll, then find target)
    if (rand() < 0.25) {
      try {
        const searchTerms = ['shorts', 'trending', 'funny videos', 'viral', 'music', 'comedy', 'dance', 'pranks', 'cute animals'];
        const term = searchTerms[Math.floor(rand() * searchTerms.length)];
        const homeUrl = isMobile
          ? `https://m.youtube.com/results?search_query=${term}`
          : `https://www.youtube.com/results?search_query=${term}`;
        await page.goto(homeUrl, { waitUntil: 'domcontentloaded', timeout: 15000 });
        await sleep(2000 + rand() * 3000);
        // Scroll through results
        for (let s = 0; s < 2 + Math.floor(rand() * 3); s++) {
          await page.evaluate(() => window.scrollBy(0, 100 + Math.random() * 400));
          await sleep(500 + rand() * 1500);
        }
      } catch {}
    }

    // Load the actual video
    const vUrl = isMobile && !videoUrl.includes('m.youtube')
      ? videoUrl.replace('www.youtube.com', 'm.youtube.com')
      : videoUrl;
    await page.goto(vUrl, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await sleep(1000 + rand() * 2000);

    // Try to click play
    try {
      for (const sel of ['video', '.html5-video-player', '#movie_player', '.ytp-play-button']) {
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
      // Every few checks, scroll a bit (like a human glancing at comments)
      if (c > 0 && c % 2 === 0) {
        try { await page.evaluate(() => window.scrollBy(0, 50 + Math.random() * 100)); } catch {}
      }
    }

    // 40% chance: scroll to comments/related after watching
    if (rand() < 0.40) {
      try {
        for (let s = 0; s < 1 + Math.floor(rand() * 3); s++) {
          await page.evaluate(() => window.scrollBy(0, 100 + Math.random() * 300));
          await sleep(500 + rand() * 1500);
        }
      } catch {}
    }

    // 15% chance: click a suggested video
    if (rand() < 0.15) {
      try {
        const selectors = ['ytd-compact-video-renderer a#thumbnail', 'a.ytd-compact-video-renderer', 'ytd-rich-item-renderer a#thumbnail'];
        for (const sel of selectors) {
          const links = await page.$$(sel);
          if (links.length > 1) {
            const idx = 1 + Math.floor(rand() * Math.min(links.length - 1, 3));
            await links[idx].click();
            await sleep(2000 + rand() * 3000);
            break;
          }
        }
      } catch {}
    }

    // 10% chance: like the video
    if (rand() < 0.10) {
      try {
        const likeBtnSelectors = ['button[aria-label*="like"]', '.ytd-toggle-button-renderer', '#top-level-buttons ytd-toggle-button-renderer'];
        for (const sel of likeBtnSelectors) {
          const likeBtn = await page.$(sel);
          if (likeBtn) { try { await likeBtn.click(); await sleep(500); } catch {} break; }
        }
      } catch {}
    }

    // 5% chance: subscribe to channel
    if (rand() < 0.05) {
      try {
        const subBtnSelectors = ['#subscribe-button button', 'ytd-subscribe-button-renderer button', 'ytd-subscribe-button-renderer'];
        for (const sel of subBtnSelectors) {
          const subBtn = await page.$(sel);
          if (subBtn) { try { await subBtn.click(); await sleep(500); } catch {} break; }
        }
      } catch {}
    }

    // Done - report success
    if (page) try { await page.close(); } catch {}
    if (context) try { await context.close(); } catch {}
    if (browser) try { await browser.close(); } catch {}

    process.send({ success: true, watchTime, videoUrl });
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