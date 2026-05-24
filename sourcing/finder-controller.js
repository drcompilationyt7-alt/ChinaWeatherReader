/**
 * Finder Controller
 * Searches YouTube for 10 URLs with metadata for AI ranking.
 */
const { execSync } = require('child_process');
const { Logger } = require('../core/logger');

const logger = new Logger('FinderController');

async function searchYouTube(query, maxResults) {
  try {
    // Use yt-dlp with dump-json to get full metadata (title, duration, description)
    const cmd = `yt-dlp --flat-playlist --dump-json "ytsearch${maxResults}:${query}" 2>/dev/null`;
    const out = execSync(cmd, { timeout: 30000, maxBuffer: 5*1024*1024 }).toString().trim();
    if (!out) return [];
    return out.split('\n').filter(Boolean).map(line => {
      try {
        const p = JSON.parse(line);
        return {
          url: `https://www.youtube.com/watch?v=${p.id}`,
          title: p.title || 'YouTube video',
          platform: 'youtube',
          duration: p.duration || 0,
          description: (p.description || '').substring(0, 200),
          view_count: p.view_count || 0,
        };
      } catch { return null; }
    }).filter(Boolean);
  } catch {
    return [];
  }
}

async function findUrlsForQueries(queries, maxTotal = 10) {
  const allResults = [];
  const seen = new Set();

  for (const query of queries) {
    if (allResults.length >= maxTotal) break;
    logger.info(`Search: "${query}"`);
    const perQuery = Math.ceil((maxTotal - allResults.length) / (queries.length - queries.indexOf(query)));
    try {
      const results = await searchYouTube(query, perQuery + 2);
      for (const r of results) {
        if (!seen.has(r.url) && allResults.length < maxTotal) {
          // Skip videos > 30 min
          if (r.duration && r.duration > 1800) continue;
          seen.add(r.url);
          allResults.push(r);
        }
      }
    } catch {}
  }

  logger.success(`Found ${allResults.length} YouTube URLs`);
  return allResults;
}

module.exports = { findUrlsForQueries };
