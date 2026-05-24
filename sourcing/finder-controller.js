/**
 * Finder Controller
 * Orchestrates multi-platform video search with priority order:
 * 1. Bilibili (API, most reliable for Chinese/Asian content)
 * 2. TikTok (web search, global trends)
 * 3. Douyin (Chinese TikTok)
 * 4. RedNote (lifestyle/fashion)
 * 5. YouTube (via yt-dlp, global fallback)
 */
const { execSync } = require('child_process');
const { Logger } = require('../core/logger');

const logger = new Logger('FinderController');

async function searchBilibili(query, maxResults) {
  try {
    const { findVideos } = require('./bilibili-finder');
    return await findVideos(query, maxResults);
  } catch (e) {
    logger.warn(`Bilibili: ${e.message.substring(0, 80)}`);
    return [];
  }
}

async function searchTikTok(query, maxResults) {
  try {
    const { findVideos } = require('./tiktok-finder');
    return await findVideos(query, maxResults);
  } catch (e) {
    logger.warn(`TikTok: ${e.message.substring(0, 80)}`);
    return [];
  }
}

async function searchDouyin(query, maxResults) {
  try {
    const { findVideos } = require('./douyin-finder');
    return await findVideos(query, maxResults);
  } catch (e) {
    logger.warn(`Douyin: ${e.message.substring(0, 80)}`);
    return [];
  }
}

async function searchRedNote(query, maxResults) {
  try {
    const { findVideos } = require('./rednote-finder');
    return await findVideos(query, maxResults);
  } catch (e) {
    logger.warn(`RedNote: ${e.message.substring(0, 80)}`);
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
  } catch (e) {
    logger.warn(`YouTube: ${e.message.substring(0, 80)}`);
    return [];
  }
}

/**
 * Search all platforms for a query, with priority ordering.
 * Bilibili is tried first since it has the most reliable free API.
 */
async function searchAll(query, maxTotal = 5) {
  const results = [];
  const addedUrls = new Set();

  async function addFrom(source) {
    if (results.length >= maxTotal) return;
    const videos = await source; 
    for (const v of videos) {
      if (results.length >= maxTotal) break;
      if (!addedUrls.has(v.url)) {
        addedUrls.add(v.url);
        results.push(v);
        logger.info(`  [${v.platform}] ${v.title.substring(0, 50)}`);
      }
    }
  }

  // Bilibili first (most reliable API)
  await addFrom(searchBilibili(query, 2));
  // Then TikTok
  await addFrom(searchTikTok(query, 2));
  // Then Douyin
  await addFrom(searchDouyin(query, 2));
  // Then RedNote
  await addFrom(searchRedNote(query, 2));
  // YouTube last (yt-dlp fallback)
  await addFrom(searchYouTube(query, 2));

  return results;
}

/**
 * Search multiple queries across platforms, deduplicate, return best URLs
 */
async function findUrlsForQueries(queries, maxTotal = 5) {
  const allResults = [];
  const seen = new Set();

  for (const query of queries) {
    if (allResults.length >= maxTotal) break;
    logger.info(`Searching: "${query}"`);
    const results = await searchAll(query, maxTotal - allResults.length);
    for (const r of results) {
      if (!seen.has(r.url)) {
        seen.add(r.url);
        allResults.push(r);
      }
    }
  }

  logger.success(`Found ${allResults.length} URLs total (from ${[...new Set(allResults.map(r => r.platform))].join(', ')})`);
  return allResults;
}

module.exports = { searchAll, findUrlsForQueries };
