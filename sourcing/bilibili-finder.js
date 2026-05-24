/**
 * Bilibili Video Finder
 * Uses Bilibili's public search API - no API key needed, returns JSON directly
 * API: api.bilibili.com/x/web-interface/search/type?search_type=video&keyword=QUERY
 */
const axios = require('axios');

async function findVideos(query, maxResults = 3) {
  try {
    const url = `https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword=${encodeURIComponent(query)}&page=1`;
    const resp = await axios.get(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.bilibili.com/',
      },
      timeout: 10000,
    });

    const results = resp.data?.data?.result || [];
    return results.slice(0, maxResults).map(v => ({
      url: `https://www.bilibili.com/video/${v.bvid}`,
      title: v.title?.replace(/<[^>]+>/g, '') || 'Bilibili video',
      platform: 'bilibili',
      duration: v.duration,
      playCount: v.play,
    }));
  } catch (err) {
    console.error(`[BilibiliFinder] API error: ${err.message.substring(0, 100)}`);
    return [];
  }
}

module.exports = { findVideos };
