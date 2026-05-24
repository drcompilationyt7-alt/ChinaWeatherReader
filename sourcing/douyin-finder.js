/**
 * Douyin Video Finder
 * Searches Douyin (Chinese TikTok) via web API. No key needed.
 */
const axios = require('axios');

async function findVideos(query, maxResults = 3) {
  try {
    const url = `https://www.douyin.com/search/${encodeURIComponent(query)}?type=video`;
    const resp = await axios.get(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
      },
      timeout: 10000,
    });

    const urls = [...resp.data.matchAll(/https?:\/\/[^"'\s]*douyin[^"'\s]*\/video\/\d+/gi)].map(m => m[0]);
    return [...new Set(urls)].slice(0, maxResults).map(url => ({
      url,
      title: 'Douyin video',
      platform: 'douyin',
    }));
  } catch (err) {
    console.error(`[DouyinFinder] Error: ${err.message.substring(0, 100)}`);
    return [];
  }
}

module.exports = { findVideos };
