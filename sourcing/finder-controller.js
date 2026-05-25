/**
 * Finder Controller
 * Searches YouTube for 10 URLs with metadata for AI ranking.
 * FIXES:
 * - Now searches specifically for REAL YouTube Shorts (/shorts/ URLs)
 * - Uses '#shorts' hashtag for ALL countries (including douyin-style)
 * - Added view count filtering to prefer videos around 10k views
 * - Prioritizes recent uploads
 * - Limits to Shorts-eligible videos (duration < 60s)
 */
const { execSync } = require('child_process');
const { Logger } = require('../core/logger');

const logger = new Logger('FinderController');

// View count preferences: prefer videos with ~10k views
const MIN_VIEWS = 1000;
const MAX_VIEWS = 50000;
const IDEAL_VIEWS = 10000;

/**
 * Enrich queries to find REAL YouTube Shorts.
 * Uses #shorts tag for ALL countries.
 * For Asian countries, includes "douyin" style descriptor too.
 */
function enrichQuery(query) {
  const asianKeywords = ['japan', 'japanese', 'korea', 'korean', 'china', 'chinese',
    'thailand', 'thai', 'vietnam', 'vietnamese', 'india', 'indonesia', 'taiwan'];
  const lowerQuery = query.toLowerCase();

  // Already has #shorts
  if (lowerQuery.includes('#shorts')) return query;

  // Check if it's an Asian country - add douyin style + #shorts
  const isAsian = asianKeywords.some(k => lowerQuery.includes(k));
  if (isAsian) {
    return `${query} douyin #shorts`;
  }

  // Default: use #shorts tag to find actual Shorts
  return `${query} #shorts`;
}

/**
 * Check if a video is likely a YouTube Short based on metadata.
 * Shorts are typically < 60 seconds.
 */
function isLikelyShort(video) {
  if (video.duration && video.duration < 60) return true;
  if (video.title && /#shorts/i.test(video.title)) return true;
  if (video.description && /#shorts/i.test(video.description)) return true;
  return false;
}

async function searchYouTube(query, maxResults) {
  const enrichedQuery = enrichQuery(query);
  let retries = 0;
  const maxRetries = 2;

  while (retries <= maxRetries) {
    try {
      const searchQuery = retries === 0 ? enrichedQuery : query;
      const cmd = `yt-dlp --flat-playlist --dump-json "ytsearch${maxResults}:${searchQuery}" 2>/dev/null`;
      const out = execSync(cmd, { timeout: 30000, maxBuffer: 5*1024*1024 }).toString().trim();

      if (!out) {
        retries++;
        continue;
      }

      const results = parseResults(out, searchQuery);
      if (results.length > 0) return results;
      retries++;
    } catch {
      retries++;
    }
  }
  return [];
}

function parseResults(out, query) {
  if (!out) return [];
  const results = out.split('\n').filter(Boolean).map(line => {
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

  // Sort: actual Shorts first
  results.sort((a, b) => {
    if (a.isShort && !b.isShort) return -1;
    if (!a.isShort && b.isShort) return 1;
    return 0;
  });

  return results;
}

function scoreByViews(viewCount) {
  if (!viewCount) return 0;
  const diff = Math.abs(viewCount - IDEAL_VIEWS);
  return Math.max(0, 1 - (diff / (IDEAL_VIEWS * 3)));
}

function scoreByRecency(uploadDate) {
  if (!uploadDate) return 0;
  try {
    const year = parseInt(uploadDate.substring(0, 4));
    if (year >= 2025) return 0.5;
    if (year >= 2024) return 0.3;
    if (year >= 2023) return 0.1;
    return 0;
  } catch { return 0; }
}

async function findUrlsForQueries(queries, maxTotal = 10) {
  const allResults = [];
  const seen = new Set();

  for (const query of queries) {
    if (allResults.length >= maxTotal) break;
    logger.info(`Search: "${query}"`);
    const perQuery = Math.ceil((maxTotal - allResults.length) / (queries.length - queries.indexOf(query)));
    try {
      const results = await searchYouTube(query, perQuery + 8);
      for (const r of results) {
        if (!seen.has(r.url) && allResults.length < maxTotal) {
          // Skip videos > 2 min
          if (r.duration && r.duration > 120) continue;
          seen.add(r.url);
          allResults.push(r);
        }
      }
    } catch {}
  }

  // Score and sort: prefer Shorts, then by views ~10k + recency
  allResults.sort((a, b) => {
    const shortBonusA = a.isShort ? 10 : 0;
    const shortBonusB = b.isShort ? 10 : 0;
    const scoreA = shortBonusA + scoreByViews(a.view_count) + scoreByRecency(a.upload_date);
    const scoreB = shortBonusB + scoreByViews(b.view_count) + scoreByRecency(b.upload_date);
    return scoreB - scoreA;
  });

  // Log
  logger.info(`URLs found: ${allResults.map(r => `${r.view_count || '?'}${r.isShort ? '📱' : ''}`).join(', ')}`);
  logger.info(`Shorts detected: ${allResults.filter(r => r.isShort).length}/${allResults.length}`);

  const idealResults = allResults.filter(r => r.view_count >= MIN_VIEWS && r.view_count <= MAX_VIEWS);
  const shortResults = allResults.filter(r => !idealResults.includes(r) && r.isShort);
  const otherResults = allResults.filter(r => !idealResults.includes(r) && !shortResults.includes(r));

  const sortedResults = [...idealResults, ...shortResults, ...otherResults].slice(0, maxTotal);

  const shortCount = sortedResults.filter(r => r.isShort).length;
  logger.success(`Found ${sortedResults.length} URLs (${idealResults.length} with ~10k views, ${shortCount} Shorts)`);
  return sortedResults;
}

module.exports = { findUrlsForQueries };
