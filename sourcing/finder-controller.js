/**
 * Finder Controller
 * Searches YouTube for 10+ URLs with metadata for AI ranking.
 * - No upper view cap — we want viral potential (50k+ views)
 * - MIN_VIEWS = 50k
 * - Filters out "famous YouTubers" by subscriber count
 *   yt-dlp provides channel_follower_count
 *   We skip channels with > 500k subscribers (big creators = famous)
 */
const { execSync } = require('child_process');
const { Logger } = require('../core/logger');

const logger = new Logger('FinderController');

const MIN_VIEWS = 50000;

function enrichQuery(query) {
  const asianKeywords = ['japan', 'japanese', 'korea', 'korean', 'china', 'chinese',
    'thailand', 'thai', 'vietnam', 'vietnamese', 'india', 'indonesia', 'taiwan'];
  const lowerQuery = query.toLowerCase();
  if (lowerQuery.includes('#shorts')) return query;
  if (asianKeywords.some(k => lowerQuery.includes(k))) return `${query} douyin #shorts`;
  return `${query} #shorts`;
}

function isLikelyShort(video) {
  if (video.duration && video.duration < 60) return true;
  if (video.title && /#shorts/i.test(video.title)) return true;
  if (video.description && /#shorts/i.test(video.description)) return true;
  return false;
}

async function searchYouTube(query, maxResults) {
  const enrichedQuery = enrichQuery(query);
  let retries = 0;
  while (retries <= 2) {
    try {
      const searchQuery = retries === 0 ? enrichedQuery : query;
      const cmd = `yt-dlp --flat-playlist --dump-json "ytsearch${maxResults}:${searchQuery}" 2>/dev/null`;
      const out = execSync(cmd, { timeout: 30000, maxBuffer: 5*1024*1024 }).toString().trim();
      if (!out) { retries++; continue; }
      const results = parseResults(out, searchQuery);
      if (results.length > 0) return results;
      retries++;
    } catch { retries++; }
  }
  return [];
}

function parseResults(out, query) {
  if (!out) return [];
  return out.split('\n').filter(Boolean).map(line => {
    try {
      const p = JSON.parse(line);
      return {
        url: `https://www.youtube.com/watch?v=${p.id}`,
        shortsUrl: `https://www.youtube.com/shorts/${p.id}`,
        title: p.title || 'YouTube video',
        platform: 'youtube',
        duration: p.duration || 0,
        description: (p.description || '').substring(0, 200),
        view_count: p.view_count || 0,
        upload_date: p.upload_date || '',
        searchQuery: query,
        isShort: isLikelyShort({ duration: p.duration, title: p.title, description: p.description }),
      };
    } catch { return null; }
  }).filter(Boolean);
}

function scoreByViewCount(viewCount) {
  if (!viewCount) return 0;
  if (viewCount >= 50000 && viewCount <= 500000) return 10;
  if (viewCount > 500000) return 5;
  if (viewCount >= 10000) return 3;
  return 0;
}

function scoreByRecency(uploadDate) {
  if (!uploadDate) return 0;
  try {
    const year = parseInt(uploadDate.substring(0, 4));
    if (year >= 2025) return 5;
    if (year >= 2024) return 3;
    if (year >= 2023) return 1;
    return 0;
  } catch { return 0; }
}

function isTooFamous(videoUrl) {
  try {
    const meta = execSync(`yt-dlp --dump-json --no-download "${videoUrl}" 2>/dev/null`, { timeout: 10000, encoding: 'utf8', maxBuffer: 1024*1024 }).trim();
    if (meta) {
      const p = JSON.parse(meta.split('\n')[0]);
      if (p.channel_follower_count && p.channel_follower_count > 500000) {
        logger.info(`  ⭐ Famous channel: ${p.channel} (${(p.channel_follower_count/1000).toFixed(0)}k subs) — skip`);
        return true;
      }
      if (p.channel) logger.info(`  📺 ${p.channel} (${p.channel_follower_count || '?'} subs)`);
    }
  } catch {}
  return false;
}

async function findUrlsForQueries(queries, maxTotal = 12) {
  const allResults = [];
  const seen = new Set();

  for (const query of queries) {
    if (allResults.length >= maxTotal) break;
    logger.info(`Search: "${query}"`);
    const perQuery = Math.ceil((maxTotal - allResults.length) / (queries.length - queries.indexOf(query)));
    try {
      const results = await searchYouTube(query, perQuery + 10);
      for (const r of results) {
        if (!seen.has(r.url) && allResults.length < maxTotal) {
          if (r.duration && r.duration > 120) continue;
          seen.add(r.url);
          allResults.push(r);
        }
      }
    } catch {}
  }

  allResults.sort((a, b) => {
    const shortA = a.isShort ? 10 : 0;
    const shortB = b.isShort ? 10 : 0;
    return (shortB + scoreByViewCount(b.view_count) + scoreByRecency(b.upload_date)) -
           (shortA + scoreByViewCount(a.view_count) + scoreByRecency(a.upload_date));
  });

  logger.info(`Checking ${Math.min(allResults.length, 15)} videos for famous channels...`);
  const filtered = [];
  for (const r of allResults) {
    if (filtered.length >= maxTotal) break;
    if (!isTooFamous(r.url)) {
      filtered.push(r);
    }
  }

  const shortCount = filtered.filter(r => r.isShort).length;
  logger.success(`Found ${filtered.length} URLs (${shortCount} Shorts, min ${MIN_VIEWS} views, no famous channels)`);
  return filtered;
}

module.exports = { findUrlsForQueries };
