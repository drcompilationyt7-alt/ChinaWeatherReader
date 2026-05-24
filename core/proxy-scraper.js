/**
 * Free Proxy Scraper
 * Fetches free proxies from public proxy lists for yt-dlp download routing.
 * Returns proxies immediately - yt-dlp handles testing internally.
 */
const axios = require('axios');
const { Logger } = require('./logger');

const logger = new Logger('ProxyScraper');

// Cache for fetched proxies
let proxyCache = [];
let cacheTime = 0;
const CACHE_TTL = 300000; // 5 min

async function getFreeProxies() {
  const now = Date.now();
  if (proxyCache.length > 0 && now - cacheTime < CACHE_TTL) {
    return proxyCache;
  }

  const proxies = [];

  // Source 1: proxyscrape.com free API (fastest, just raw list)
  try {
    const resp = await axios.get('https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=all', { timeout: 5000 });
    const lines = resp.data.split('\n').filter(Boolean);
    for (const line of lines.slice(0, 100)) {
      const [ip, port] = line.trim().split(':');
      if (ip && port) proxies.push({ ip, port, type: 'http', source: 'scrape' });
    }
    logger.info(`proxyscrape: ${lines.length}`);
  } catch {}

  // Source 2: github raw proxy list (fast)
  try {
    const resp = await axios.get('https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt', { timeout: 5000 });
    const lines = resp.data.split('\n').filter(Boolean);
    for (const line of lines.slice(0, 50)) {
      const [ip, port] = line.trim().split(':');
      if (ip && port && !proxies.find(p => p.ip === ip)) proxies.push({ ip, port, type: 'http', source: 'github' });
    }
    logger.info(`github: ${lines.length}`);
  } catch {}

  // Deduplicate by IP
  const unique = proxies.filter((p, i) => proxies.findIndex(x => x.ip === p.ip) === i);
  
  // Shuffle to randomize which proxy gets used first
  for (let i = unique.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [unique[i], unique[j]] = [unique[j], unique[i]];
  }

  proxyCache = unique;
  cacheTime = now;
  logger.success(`Cached ${unique.length} proxies`);
  return unique;
}

/**
 * Get a proxy - returns one immediately without testing
 * yt-dlp will test it internally (--proxy flag)
 */
async function getWorkingProxy() {
  // Check env var first (user prefers this)
  if (process.env.YT_PROXY && process.env.YT_PROXY.trim()) {
    logger.info('Using YT_PROXY from env');
    return process.env.YT_PROXY.trim();
  }

  // Get from cache or scrape
  const proxies = await getFreeProxies();
  
  if (proxies.length === 0) {
    logger.warn('No proxies found');
    return null;
  }

  // Return one immediately (shuffled, random)
  const proxy = proxies[Math.floor(Math.random() * proxies.length)];
  const proxyStr = `http://${proxy.ip}:${proxy.port}`;
  logger.info(`Trying proxy: ${proxyStr} (${proxy.source})`);
  return proxyStr;
}

module.exports = { getFreeProxies, getWorkingProxy };
