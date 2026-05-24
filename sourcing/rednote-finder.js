/**
 * RedNote (Xiaohongshu) Video Finder
 * Searches via web. No key needed, may be partial.
 */
const axios = require('axios');

async function findVideos(query, maxResults = 3) {
  try {
    const url = `https://www.xiaohongshu.com/search_result?keyword=${encodeURIComponent(query)}&type=video`;
    const resp = await axios.get(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml',
      },
      timeout: 10000,
    });

    const urls = [...resp.data.matchAll(/https?:\/\/[^"'\s]*xiaohongshu[^"'\s]*\/explore\/[a-zA-Z0-9]+/gi)].map(m => m[0]);
    return [...new Set(urls)].slice(0, maxResults).map(url => ({
      url,
      title: 'RedNote video',
      platform: 'rednote',
    }));
  } catch (err) {
    console.error(`[RedNoteFinder] Error: ${err.message.substring(0, 100)}`);
    return [];
  }
}

module.exports = { findVideos };
