#!/usr/bin/env node

/**
 * Mr. WorldWideWebster — Boost Engine v8
 *
 * Fetches 5 RANDOM shorts from the channel, then boosts them
 * with human-like parallel sessions via child process workers.
 *
 * Target: 1000 TOTAL views distributed randomly across 5 videos
 * Proxy pool: target 100, 20min timeout, stream as validated
 * Sessions: forked child processes (boost-session.js)
 *
 * Usage:
 *   node boost/boost-engine.js --channel "https://www.youtube.com/@channel"
 *   node boost/boost-engine.js --url "https://youtube.com/watch?v=xxx" --views 1000
 */
const { execSync, fork } = require('child_process');
const path = require('path');
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
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1',
  'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.113 Mobile Safari/537.36',
  'Mozilla/5.0 (Linux; Android 13; Samsung Galaxy S23) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.118 Mobile Safari/537.36',
  'Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 OPR/109.0.0.0',
];

const VIEWPORT_PROFILES = [
  { width: 1920, height: 1080 },      // Desktop
  { width: 1366, height: 768 },        // Desktop
  { width: 390, height: 844, isMobile: true },   // iPhone 14 Pro
  { width: 430, height: 932, isMobile: true },   // iPhone 15 Pro Max
  { width: 412, height: 915, isMobile: true },   // Android
  { width: 360, height: 780, isMobile: true },   // Android small
  { width: 768, height: 1024 },        // iPad
  { width: 390, height: 844, isMobile: true },   // More mobile weight
  { width: 414, height: 896, isMobile: true },   // iPhone 11
  { width: 393, height: 852, isMobile: true },   // iPhone 15
];

// Watch time distribution for YouTube Shorts (30-60s videos)
function randomWatchTime(rand) {
  const roll = rand();
  if (roll < 0.15) return 3 + Math.floor(rand() * 6);    // 15% quick bounce (3-8s)
  if (roll < 0.40) return 10 + Math.floor(rand() * 11);  // 25% short watch (10-20s)
  if (roll < 0.70) return 20 + Math.floor(rand() * 21);  // 30% full watch (20-40s)
  if (roll < 0.90) return 40 + Math.floor(rand() * 21);  // 20% full + rewatch (40-60s)
  return 50 + Math.floor(rand() * 11);                     // 10% full + comments (50-60s)
}

class BoostEngine {
  constructor() {
    this.videoUrls = [];
    this.targetViews = 1000;  // TOTAL across all videos
    this.totalViews = 0;
    this._proxyPool = [];      // accumulated working proxies
    this._proxyResolve = null; // resolver for next proxy
    this._proxyWaitQueue = []; // waiting consumers
    this._startedAt = null;
    this._completed = false;
  }

  async run(params = {}) {
    // Resolve channel URL from YOUTUBE_HANDLE or YOUTUBE_USERNAME
    let channelUrl = params.channel;
    if (!channelUrl) {
      const handle = process.env.YOUTUBE_HANDLE || process.env.YOUTUBE_USERNAME;
      if (handle) {
        const cleanHandle = handle.replace(/^@/, '');
        channelUrl = `https://www.youtube.com/@${cleanHandle}`;
        logger.info(`Using channel from env: ${channelUrl}`);
      }
    }

    if (channelUrl) {
      this.videoUrls = await this._fetchRandomVideos(channelUrl, 5);
      if (this.videoUrls.length === 0) {
        logger.error('No videos found on channel');
        return { success: false, views: 0, error: 'No videos on channel' };
      }
      logger.success(`Found ${this.videoUrls.length} videos, boosting with random distribution`);
    } else if (params.url) {
      this.videoUrls = [params.url];
    }

    if (params.views) this.targetViews = parseInt(params.views) || 1000;

    // Distribute views randomly across videos (weighted random)
    const videoWeights = this.videoUrls.map(() => Math.random() * 0.5 + 0.5); // 0.5-1.0
    const totalWeight = videoWeights.reduce((a, b) => a + b, 0);
    this.videoTargets = videoWeights.map(w => Math.round((w / totalWeight) * this.targetViews));
    // Adjust rounding
    const sum = this.videoTargets.reduce((a, b) => a + b, 0);
    if (sum > 0) this.videoTargets[0] += this.targetViews - sum;
    logger.info(`Distribution: ${this.videoUrls.map((v, i) => `${this.videoTargets[i]} views`).join(', ')}`);

    logger.header('BOOST ENGINE v8');
    logger.info(`Videos: ${this.videoUrls.length}`);
    logger.info(`Total target: ${this.targetViews} views`);

    // Start proxy gathering in background (target 50, 10min timeout)
    const proxyGatherPromise = this._buildProxyPool(10 * 60 * 1000);

    // Start consuming proxies and spawning sessions
    const startTime = Date.now();
    this._startedAt = startTime;
    const sessionPromises = [];

    // Seed for deterministic randomness
    let masterSeed = Date.now();

    let consecutiveFailures = 0;
    const MAX_CONSECUTIVE_FAILURES = 30;

    while (this.totalViews < this.targetViews && (Date.now() - startTime) < 25 * 60 * 1000) {
      // Check if too many consecutive failures — fall back to no-proxy mode
      if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
        logger.warn(`${consecutiveFailures} consecutive failures — switching to no-proxy mode`);
        break;
      }

      // Wait for a proxy to become available (with 30s timeout)
      const proxy = await this._waitForProxyWithTimeout(30000);
      if (!proxy) {
        logger.warn('No proxy available within 30s — trying without proxy');
        break;
      }

      // Pick a random video weighted by remaining views needed
      const remainingViews = this.videoTargets.map((t, i) => ({
        url: this.videoUrls[i],
        remaining: t - (this._viewsPerVideo[i] || 0),
      })).filter(v => v.remaining > 0);

      if (remainingViews.length === 0) break; // all done

      // Weighted random pick
      const totalRemaining = remainingViews.reduce((a, b) => a + b.remaining, 0);
      let pickRoll = Math.random() * totalRemaining;
      let picked = remainingViews[0];
      for (const rv of remainingViews) {
        pickRoll -= rv.remaining;
        if (pickRoll <= 0) { picked = rv; break; }
      }

      // Build session args
      masterSeed++;
      const ua = USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
      const vp = VIEWPORT_PROFILES[Math.floor(Math.random() * VIEWPORT_PROFILES.length)];
      const watchSec = randomWatchTime(Math.random);
      const proxyStr = proxy ? `http://${proxy.ip}:${proxy.port}` : '';

      // Fork child worker
      const sessionPromise = this._spawnSession(picked.url, proxyStr, ua, vp, masterSeed, watchSec)
        .then(result => {
          if (result) {
            consecutiveFailures = 0; // reset on success
          } else {
            consecutiveFailures++;
          }
          return result;
        });
      sessionPromises.push(sessionPromise);

      // Log progress
      if (this.totalViews % 10 === 0 || sessionPromises.length % 50 === 0) {
        logger.info(`Active sessions: ${sessionPromises.length}, total: ${this.totalViews}/${this.targetViews}, failures: ${consecutiveFailures}`);
      }

      // Stagger spawns 1-3s apart
      await new Promise(r => setTimeout(r, 1000 + Math.random() * 2000));
    }

    // No-proxy fallback: use current IP (GitHub runner) directly
    if (this.totalViews < this.targetViews) {
      const remaining = this.targetViews - this.totalViews;
      const remainingCount = Math.min(remaining, 20); // limit no-proxy attempts
      logger.info(`No-proxy fallback: trying ${remainingCount} views without proxy...`);

      // Wait a moment for proxy gathering to stop
      await new Promise(r => setTimeout(r, 2000));

      for (let i = 0; i < remainingCount && this.totalViews < this.targetViews; i++) {
        masterSeed++;
        const ua = USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
        const vp = VIEWPORT_PROFILES[Math.floor(Math.random() * VIEWPORT_PROFILES.length)];
        const watchSec = randomWatchTime(Math.random);

        sessionPromises.push(
          this._spawnSession(this.videoUrls[0], '', ua, vp, masterSeed, watchSec)
        );

        // Longer stagger for no-proxy to avoid bot detection patterns
        await new Promise(r => setTimeout(r, 3000 + Math.random() * 5000));
      }
    }

    // Mark as done and wait for remaining sessions
    this._completed = true;
    try { await Promise.race([proxyGatherPromise, new Promise(r => setTimeout(r, 5000))]); } catch {}

    if (sessionPromises.length > 0) {
      logger.info(`Waiting for ${sessionPromises.length} sessions to finish...`);
      await Promise.allSettled(sessionPromises);
    }

    const elapsed = ((Date.now() - startTime) / 1000 / 60).toFixed(1);
    logger.success(`Boost complete: ${this.totalViews} views in ${elapsed} min`);
    return { success: this.totalViews > 0, views: this.totalViews };
  }

  async _spawnSession(videoUrl, proxyStr, ua, vp, seed, watchSec) {
    const childPath = path.join(__dirname, 'boost-session.js');
    const args = [
      videoUrl,
      proxyStr || 'null',
      ua,
      String(vp.width),
      String(vp.height),
      String(!!vp.isMobile),
      String(seed),
      String(watchSec),
    ];

    return new Promise(resolve => {
      const child = fork(childPath, args, { stdio: 'pipe' });

      let resolved = false;
      const timeout = setTimeout(() => {
        if (!resolved) {
          resolved = true;
          try { child.kill(); } catch {}
          resolve(false);
        }
      }, 90000); // 90s max per session

      child.on('message', msg => {
        if (resolved) return;
        resolved = true;
        clearTimeout(timeout);
        if (msg && msg.success) {
          this.totalViews++;
          // Track per video (lazy init)
          if (!this._viewsPerVideo) this._viewsPerVideo = {};
          this._viewsPerVideo[msg.videoUrl] = (this._viewsPerVideo[msg.videoUrl] || 0) + 1;
          logger.info(`  View ${this.totalViews}/${this.targetViews} | ${msg.watchTime}s | ${videoUrl.substring(0, 40)}`);
        } else {
          logger.warn(`  Session failed: ${(msg && msg.error) || 'unknown'}`);
        }
        resolve(msg && msg.success);
      });

      child.on('error', () => {
        if (!resolved) { resolved = true; clearTimeout(timeout); resolve(false); }
      });

      child.on('exit', (code) => {
        if (!resolved) { resolved = true; clearTimeout(timeout); resolve(code === 0); }
      });
    });
  }

  /**
   * Get the next available proxy, waiting if pool is empty.
   * Returns null if pool is exhausted.
   */
  _waitForProxy() {
    if (this._proxyPool.length > 0) {
      return this._proxyPool.shift();
    }
    // If pool is empty and gathering is done, return null
    if (this._proxyGatheringDone) return null;

    return new Promise(resolve => {
      this._proxyWaitQueue.push(resolve);
    });
  }

  /**
   * Wait for proxy with a timeout. Returns null if timed out.
   */
  async _waitForProxyWithTimeout(timeoutMs) {
    if (this._proxyPool.length > 0) {
      return this._proxyPool.shift();
    }
    if (this._proxyGatheringDone) return null;

    return Promise.race([
      new Promise(resolve => {
        this._proxyWaitQueue.push(resolve);
      }),
      new Promise(resolve => setTimeout(() => resolve(null), timeoutMs)),
    ]);
  }

  /**
   * Add a proxy to the pool, resolving any waiting consumers.
   */
  _addProxy(proxy) {
    this._proxyPool.push(proxy);
    if (this._proxyWaitQueue.length > 0) {
      const resolve = this._proxyWaitQueue.shift();
      resolve(this._proxyPool.shift());
    }
  }

  /**
   * Build proxy pool with 20-minute hard timeout.
   * Streams verified proxies as they become available.
   */
  async _buildProxyPool(timeoutMs = 20 * 60 * 1000) {
    const raw = [];
    const seenIps = new Set();
    const deadline = Date.now() + timeoutMs;
    const targetCount = 100;

    // Source 1: Proxifly
    try {
      const axios = require('axios');
      const resp = await axios.get(
        'https://api.proxifly.dev/proxy?country=all&type=http&limit=50&format=json',
        { timeout: 10000, headers: { 'User-Agent': 'Mozilla/5.0' } }
      );
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
      logger.info(`Proxifly: ${list.length} raw`);
    } catch (e) {
      logger.warn(`Proxifly: ${e.message.substring(0, 60)}`);
    }

    // Source 2: proxyscrape
    try {
      const axios = require('axios');
      const resp = await axios.get(
        'https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all',
        { timeout: 5000 }
      );
      const lines = resp.data.split('\n').filter(Boolean);
      for (const line of lines.slice(0, 150)) {
        const [ip, port] = line.trim().split(':');
        if (ip && port && !seenIps.has(ip)) {
          seenIps.add(ip);
          raw.push({ ip, port });
        }
      }
      logger.info(`Proxyscrape: ${lines.length} raw`);
    } catch {}

    logger.info(`Testing ${raw.length} proxies (deadline: ${timeoutMs / 1000}s)`);

    // Test proxies concurrently — add each to pool immediately when verified
    const testConcurrency = 15;
    let testedCount = 0;
    let verifiedCount = 0;

    for (let i = 0; i < raw.length && Date.now() < deadline; i += testConcurrency) {
      const batch = raw.slice(i, i + testConcurrency);
      const results = await Promise.allSettled(
        batch.map(p => this._testAndAddProxy(p))
      );

      for (const r of results) {
        if (r.status === 'fulfilled' && r.value) {
          verifiedCount++;
          this._addProxy(r.value);
        }
      }

      testedCount += batch.length;
      if (testedCount % 30 === 0) {
        logger.info(`  Proxies: ${verifiedCount} working / ${testedCount} tested, pool: ${this._proxyPool.length}`);
      }

      // Early exit if we hit target
      if (verifiedCount >= targetCount) {
        logger.success(`Reached target of ${targetCount} proxies`);
        break;
      }
    }

    this._proxyGatheringDone = true;
    // Resolve any remaining waiters with null (no more proxies coming)
    while (this._proxyWaitQueue.length > 0) {
      this._proxyWaitQueue.shift()(null);
    }

    const finalCount = this._proxyPool.length + verifiedCount;
    if (finalCount < targetCount) {
      logger.warn(`Proxy pool: ${finalCount}/${targetCount} (below target, using what we have)`);
    }
    if (finalCount < 50) {
      logger.warn(`Fewer than 50 proxies — boost may be slow`);
    }

    logger.success(`Proxy pool ready: ${finalCount} verified (${testedCount} tested)`);
  }

  /**
   * Test a proxy and return it if working, false if not.
   * Uses a quick TCP connect test.
   */
  async _testAndAddProxy(proxy) {
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

  /**
   * Fetch 5 RANDOM shorts from a YouTube channel using yt-dlp.
   */
  async _fetchRandomVideos(channelUrl, count = 5) {
    logger.info(`Fetching videos from: ${channelUrl}`);
    try {
      const out = execSync(
        `yt-dlp --flat-playlist --dump-json --playlist-end 30 "${channelUrl}" 2>/dev/null`,
        { timeout: 30000, encoding: 'utf8', maxBuffer: 5 * 1024 * 1024 }
      ).toString().trim();
      const lines = out.split('\n').filter(Boolean);
      const shorts = [];

      for (const line of lines) {
        try {
          const p = JSON.parse(line);
          // Filter to Shorts (<60s)
          if (p.id && p.duration && p.duration <= 60) {
            shorts.push(`https://www.youtube.com/watch?v=${p.id}`);
          }
        } catch {}
      }

      if (shorts.length === 0) {
        logger.warn('No shorts found on channel');
        return [];
      }

      // Pick random count from available shorts
      const shuffled = [...shorts];
      for (let i = shuffled.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
      }

      const selected = shuffled.slice(0, Math.min(count, shuffled.length));
      logger.success(`Selected ${selected.length} random shorts from ${shorts.length} available`);
      return selected;
    } catch (e) {
      logger.warn(`Channel fetch failed: ${e.message.substring(0, 100)}`);
      return [];
    }
  }

  _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
}

if (require.main === module) {
  const engine = new BoostEngine();
  const args = process.argv.slice(2);

  if (args.includes('--help') || args.includes('-h') || (!args.includes('--url') && !args.includes('--channel') && !process.env.YOUTUBE_HANDLE && !process.env.YOUTUBE_USERNAME)) {
    console.log(`
Usage:
  node boost/boost-engine.js --channel "https://www.youtube.com/@channel"
  node boost/boost-engine.js --url "https://youtube.com/watch?v=xxx" [--views 1000]

Or set YOUTUBE_HANDLE (or YOUTUBE_USERNAME) env var for automatic channel discovery.

Options:
  --channel <url>     YouTube channel to boost (optional, uses env var otherwise)
  --url <url>         Single video URL to boost
  --views <number>    Total target views across all videos (default: 1000)
  --help              Show this help
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