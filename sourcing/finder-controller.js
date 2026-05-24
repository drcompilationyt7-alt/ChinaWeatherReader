/**
 * Finder Controller
 * Bilibili: ONLY for Chinese content (China viral, Chinese meme, etc.)
 * YouTube: for ALL other countries
 */
const { execSync } = require('child_process');
const { Logger } = require('../core/logger');

const logger = new Logger('FinderController');

async function searchBilibili(query, maxResults) {
  try {
    const { findVideos } = require('./bilibili-finder');
    const results = await findVideos(query, maxResults);
    // Only use Bilibili results if query is about China
    const isChina = query.toLowerCase().includes('china') || 
                    query.toLowerCase().includes('chinese') ||
                    query.toLowerCase().includes('bilibili') ||
                    query.includes('\u4e2d\u56fd') ||
                    query.includes('\u4e2d\u6587');
    if (!isChina && results.length > 0) {
      logger.warn(`Bilibili returned ${results.length} results but query isn't Chinese - filtering out`);
      return [];
    }
    return results;
  } catch (e) {
    return [];
  }
}

async function searchYouTube(query, maxResults) {
  try {
    const cmd = `yt-dlp --flat-playlist --dump-json "ytsearch${maxResults}:${query}" 2>/dev/null`;
    const out = execSync(cmd, { timeout: 30000, maxBuffer: 5*1024*1024 }).toString().trim();
    if (!out) return [];
    return out.split('\n').filter(Boolean).map(line => {
      try {
        const p = JSON.parse(line);
        return { url: `https://www.youtube.com/watch?v=${p.id}`, title: p.title || 'YouTube', platform: 'youtube' };
      } catch { return null; }
    }).filter(Boolean);
  } catch {
    return [];
  }
}

async function searchAll(query, maxTotal = 5) {
  const results = [];
  const addedUrls = new Set();

  async function addFrom(promise) {
    if (results.length >= maxTotal) return;
    const videos = await promise;
    for (const v of videos) {
      if (results.length >= maxTotal) break;
      if (!addedUrls.has(v.url)) {
        addedUrls.add(v.url);
        results.push(v);
        logger.info(`  [${v.platform}] ${(v.title||'').substring(0, 50)}`);
      }
    }
  }

  const isChina = query.toLowerCase().includes('china') || query.toLowerCase().includes('chinese');
  
  if (isChina) {
    // For Chinese content: Bilibili first, YouTube fallback
    await addFrom(searchBilibili(query, 3));
    if (results.length < 3) await addFrom(searchYouTube(query, 3));
  } else {
    // For everything else: YouTube only
    await addFrom(searchYouTube(query, 5));
  }

  return results;
}

async function findUrlsForQueries(queries, maxTotal = 5) {
  const allResults = [];
  const seen = new Set();

  for (const query of queries) {
    if (allResults.length >= maxTotal) break;
    logger.info(`Search: "${query}"`);
    const results = await searchAll(query, maxTotal - allResults.length);
    for (const r of results) {
      if (!seen.has(r.url)) {
        seen.add(r.url);
        allResults.push(r);
      }
    }
  }

  const platforms = [...new Set(allResults.map(r => r.platform))].join(', ');
  logger.success(`Found ${allResults.length} URLs from ${platforms}`);
  return allResults;
}

module.exports = { searchAll, findUrlsForQueries };
