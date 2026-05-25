/**
 * Finder Controller
 * Searches YouTube for 10 URLs with metadata for AI ranking.
 * FIXES:
 * - Added view count filtering to prefer videos around 100k views
 * - Added "short" keyword to queries for shorts content
 * - Automatically appends "short" to search queries
 */
const { execSync } = require('child_process');
const { Logger } = require('../core/logger');

const logger = new Logger('FinderController');

// View count preferences: prefer videos with ~100k views
const MIN_VIEWS = 50000;
const MAX_VIEWS = 500000;
const IDEAL_VIEWS = 100000;

/**
 * Enrich queries with "short" suffix for Shorts-style content.
 * For Asian country queries, use "douyin short" to find Douyin-style shorts.
 */
function enrichQuery(query) {
  const asianKeywords = ['japan', 'japanese', 'korea', 'korean', 'china', 'chinese', 
    'thailand', 'thai', 'vietnam', 'vietnamese', 'india', 'indonesia', 'taiwan'];
  const lowerQuery = query.toLowerCase();
  
  // Already has "short" in it
  if (lowerQuery.includes('short')) return query;
  
  // Check if it's an Asian country - use "douyin short"
  const isAsian = asianKeywords.some(k => lowerQuery.includes(k));
  if (isAsian) {
    return `${query} douyin short`;
  }
  
  // Default: append "short"
  return `${query} short`;
}

async function searchYouTube(query, maxResults) {
  // Enrich query with "short" for Shorts content
  const enrichedQuery = enrichQuery(query);
  
  try {
    // Use yt-dlp with dump-json to get full metadata (title, duration, description, view_count)
    const cmd = `yt-dlp --flat-playlist --dump-json "ytsearch${maxResults}:${enrichedQuery}" 2>/dev/null`;
    const out = execSync(cmd, { timeout: 30000, maxBuffer: 5*1024*1024 }).toString().trim();
    if (!out) {
      // Fallback to original query if enriched query fails
      logger.info(`Enriched query returned nothing, trying original: "${query}"`);
      const fallbackCmd = `yt-dlp --flat-playlist --dump-json "ytsearch${maxResults}:${query}" 2>/dev/null`;
      const fallbackOut = execSync(fallbackCmd, { timeout: 30000, maxBuffer: 5*1024*1024 }).toString().trim();
      if (!fallbackOut) return [];
      return parseResults(fallbackOut, query);
    }
    const results = parseResults(out, enrichedQuery);
    
    // If enriched query returned results, add some from original query too for variety
    if (results.length < maxResults) {
      try {
        const fallbackCmd = `yt-dlp --flat-playlist --dump-json "ytsearch${maxResults}:${query}" 2>/dev/null`;
        const fallbackOut = execSync(fallbackCmd, { timeout: 30000, maxBuffer: 5*1024*1024 }).toString().trim();
        if (fallbackOut) {
          const fallbackResults = parseResults(fallbackOut, query);
          results.push(...fallbackResults);
        }
      } catch {}
    }
    
    return results;
  } catch {
    return [];
  }
}

function parseResults(out, query) {
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
        searchQuery: query,
      };
    } catch { return null; }
  }).filter(Boolean);
}

/**
 * Score videos by how close their view count is to ideal (~100k)
 */
function scoreByViews(viewCount) {
  if (!viewCount) return 0;
  const diff = Math.abs(viewCount - IDEAL_VIEWS);
  // Higher score = closer to ideal
  return Math.max(0, 1 - (diff / IDEAL_VIEWS));
}

async function findUrlsForQueries(queries, maxTotal = 10) {
  const allResults = [];
  const seen = new Set();

  for (const query of queries) {
    if (allResults.length >= maxTotal) break;
    logger.info(`Search: "${query}"`);
    const perQuery = Math.ceil((maxTotal - allResults.length) / (queries.length - queries.indexOf(query)));
    try {
      const results = await searchYouTube(query, perQuery + 5); // Fetch extra for filtering
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

  // Score and sort by view count proximity to 100k
  allResults.sort((a, b) => {
    const scoreA = scoreByViews(a.view_count);
    const scoreB = scoreByViews(b.view_count);
    return scoreB - scoreA;
  });

  // Log view count distribution
  logger.info(`URLs found with view counts: ${allResults.map(r => r.view_count || '?').join(', ')}`);
  
  // Prefer results with views in our target range, but keep others as fallback
  const idealResults = allResults.filter(r => r.view_count >= MIN_VIEWS && r.view_count <= MAX_VIEWS);
  const otherResults = allResults.filter(r => !r.view_count || r.view_count < MIN_VIEWS || r.view_count > MAX_VIEWS);
  
  // Take top from ideal first, then fill with others
  const sortedResults = [...idealResults, ...otherResults].slice(0, maxTotal);
  
  logger.success(`Found ${sortedResults.length} YouTube URLs (${idealResults.length} with ~${Math.round(IDEAL_VIEWS/1000)}k views)`);
  return sortedResults;
}

module.exports = { findUrlsForQueries };
