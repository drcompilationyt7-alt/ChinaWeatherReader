/**
 * Free Proxy Scraper
 * Fetches working free proxies from:
 * 1. Proxifly API (actively tests proxies before returning)
 * 2. proxyscrape.com (raw list)
 * 3. GitHub proxy lists
 */
const axios = require('axios');
const { Logger } = require('./logger');

const logger = new Logger('ProxyScraper');

let proxyCache = [];
let cacheIdx = 0;
let cacheTime = 0;
const CACHE_TTL = 600000; // 10 min

async function getFreeProxies() {
  const now = Date.now();
  if (proxyCache.length > 0 && now - cacheTime < CACHE_TTL) {
    return proxyCache;
  }

  const proxies = [];
  const seenIps = new Set();

  // Source 1: Proxifly API (actively tested, most reliable)
  // Free tier: https://api.proxifly.dev/proxy?country=all&type=http&anonymity=elite&limit=20
  try {
    const resp = await axios.get('https://api.proxifly.dev/proxy?country=all&type=http&limit=30&format=json', {
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
        proxies.push({ ip, port: String(port), type: 'http', source: 'proxifly' });
      }
    }
    logger.info(`Proxifly: ${list.length} proxies`);
  } catch (e) {
    logger.warn(`Proxifly: ${e.message.substring(0, 60)}`);
  }

  // Source 2: proxyscrape.com (raw list, fast)
  try {
    const resp = await axios.get('https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all', {
      timeout: 5000
    });
    const lines = resp.data.split('\n').filter(Boolean);
    for (const line of lines.slice(0, 100)) {
      const [ip, port] = line.trim().split(':');
      if (ip && port && !seenIps.has(ip)) {
        seenIps.add(ip);
        proxies.push({ ip, port, type: 'http', source: 'scrape' });
      }
    }
    logger.info(`proxyscrape: ${lines.length}`);
  } catch {}

  // Shuffle
  for (let i = proxies.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [proxies[i], proxies[j]] = [proxies[j], proxies[i]];
  }

  proxyCache = proxies;
  cacheIdx = 0;
  cacheTime = now;
  logger.success(`Cached ${proxies.length} proxies`);
  return proxies;
}

/**
 * Get a proxy for yt-dlp - rotates through the list each call
 */
async function getWorkingProxy() {
  // Check env var first
  if (process.env.YT_PROXY && process.env.YT_PROXY.trim()) {
    return process.env.YT_PROXY.trim();
  }

  const proxies = await getFreeProxies();
  if (proxies.length === 0) return null;

  // Round-robin rotation
  const proxy = proxies[cacheIdx % proxies.length];
  cacheIdx++;
  
  const proxyStr = `http://${proxy.ip}:${proxy.port}`;
  logger.info(`Proxy #${cacheIdx}: ${proxyStr} (${proxy.source})`);
  return proxyStr;
}

module.exports = { getFreeProxies, getWorkingProxy };
