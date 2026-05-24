/**
 * Finder Controller
 * Priority order (user request):
 * 1. Bilibili (best API)
 * 2. RedNote (Xiaohongshu)
 * 3. Douyin
 * 4. TikTok
 * 5. YouTube (last resort)
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

async function searchRedNote(query, maxResults) {
  try {
    const { findVideos } = require('./rednote-finder');
    return await findVideos(query, maxResults);
  } catch (e) {
    logger.warn(`RedNote: ${e.message.substring(0, 80)}`);
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

async function searchTikTok(query, maxResults) {
  try {
    const { findVideos } = require('./tiktok-finder');
    return await findVideos(query, maxResults);
  } catch (e) {
    logger.warn(`TikTok: ${e.message.substring(0, 80)}`);
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

  // Priority: Bilibili > RedNote > Douyin > TikTok > YouTube
  await addFrom(searchBilibili(query, 2));
  await addFrom(searchRedNote(query, 2));
  await addFrom(searchDouyin(query, 2));
  await addFrom(searchTikTok(query, 2));
  await addFrom(searchYouTube(query, 2));

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
