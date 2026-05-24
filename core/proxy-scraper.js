/**
 * Free Proxy Scraper
 * Fetches working free proxies from public proxy lists.
 * Used to route yt-dlp downloads through to bypass CI IP blocks.
 * Sources: free-proxy-list.net, sslproxies.org, etc.
 */
const axios = require('axios');
const { Logger } = require('./logger');

const logger = new Logger('ProxyScraper');

/**
 * Fetch free proxies from multiple public sources
 */
async function getFreeProxies() {
  const proxies = [];

  // Source 1: free-proxy-list.net
  try {
    const resp = await axios.get('https://free-proxy-list.net/', {
      headers: { 'User-Agent': 'Mozilla/5.0' },
      timeout: 10000,
    });
    const html = resp.data;
    // Parse the proxy table
    const rows = [...html.matchAll(/<tr><td>([\d.]+)<\/td><td>(\d+)<\/td><td>[^<]*<\/td><td[^>]*>([^<]*)<\/td>/g)];
    for (const row of rows.slice(0, 50)) {
      const ip = row[1];
      const port = row[2];
      const type = row[3].toLowerCase().includes('https') ? 'https' : 'http';
      proxies.push({ ip, port, type, source: 'free-proxy-list' });
    }
    logger.info(`Found ${rows.length} proxies from free-proxy-list.net`);
  } catch (e) {
    logger.warn(`free-proxy-list: ${e.message.substring(0, 60)}`);
  }

  // Source 2: sslproxies.org
  try {
    const resp = await axios.get('https://www.sslproxies.org/', {
      headers: { 'User-Agent': 'Mozilla/5.0' },
      timeout: 10000,
    });
    const html = resp.data;
    const rows = [...html.matchAll(/<tr><td>([\d.]+)<\/td><td>(\d+)<\/td><td[^>]*>([^<]*)<\/td>/g)];
    for (const row of rows.slice(0, 50)) {
      const ip = row[1];
      const port = row[2];
      const type = 'https';
      if (!proxies.find(p => p.ip === ip && p.port === port)) {
        proxies.push({ ip, port, type, source: 'sslproxies' });
      }
    }
    logger.info(`Found ${rows.length} proxies from sslproxies.org`);
  } catch (e) {
    logger.warn(`sslproxies: ${e.message.substring(0, 60)}`);
  }

  // Source 3: proxyscrape.com free API
  try {
    const resp = await axios.get('https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all', {
      timeout: 10000,
    });
    const lines = resp.data.split('\n').filter(Boolean);
    for (const line of lines.slice(0, 50)) {
      const [ip, port] = line.trim().split(':');
      if (ip && port && !proxies.find(p => p.ip === ip && p.port === port)) {
        proxies.push({ ip, port, type: 'http', source: 'proxyscrape' });
      }
    }
    logger.info(`Found ${lines.length} proxies from proxyscrape.com`);
  } catch (e) {
    logger.warn(`proxyscrape: ${e.message.substring(0, 60)}`);
  }

  // Deduplicate
  const unique = proxies.filter((p, i) => proxies.findIndex(x => x.ip === p.ip && x.port === p.port) === i);
  logger.success(`Total unique proxies: ${unique.length}`);
  return unique;
}

/**
 * Test a single proxy by connecting to a known URL
 */
async function testProxy(proxy, testUrl = 'https://www.google.com', timeout = 5000) {
  try {
    const proxyUrl = `http://${proxy.ip}:${proxy.port}`;
    await axios.get(testUrl, {
      proxy: { host: proxy.ip, port: parseInt(proxy.port), protocol: 'http' },
      timeout,
      headers: { 'User-Agent': 'Mozilla/5.0' },
    });
    return true;
  } catch {
    return false;
  }
}

/**
 * Get a working proxy for yt-dlp
 * Returns proxy URL string like "http://ip:port" or null
 */
async function getWorkingProxy() {
  // Check if there's a cached working proxy from the env
  if (process.env.YT_PROXY) {
    logger.info(`Using YT_PROXY from env: ${process.env.YT_PROXY.substring(0, 30)}...`);
    return process.env.YT_PROXY;
  }

  logger.info('Scraping free proxies...');
  const proxies = await getFreeProxies();
  
  if (proxies.length === 0) {
    logger.warn('No proxies found');
    return null;
  }

  // Test a few proxies (test first 15)
  logger.info(`Testing ${Math.min(15, proxies.length)} proxies...`);
  const testResults = await Promise.allSettled(
    proxies.slice(0, 15).map(async (proxy) => {
      const working = await testProxy(proxy);
      return { proxy, working };
    })
  );

  const working = testResults
    .filter(r => r.status === 'fulfilled' && r.value.working)
    .map(r => r.value.proxy);

  if (working.length > 0) {
    const proxy = working[Math.floor(Math.random() * working.length)];
    const proxyStr = `http://${proxy.ip}:${proxy.port}`;
    logger.success(`Using proxy: ${proxyStr} (from ${proxy.source})`);
    return proxyStr;
  }

  logger.warn('No working proxies found among tested ones');
  return null;
}

module.exports = { getFreeProxies, getWorkingProxy, testProxy };
