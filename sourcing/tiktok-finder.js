/**
 * TikTok Video Finder
 * Searches TikTok via web API. No API key needed.
 */
const axios = require('axios');

async function findVideos(query, maxResults = 3) {
  try {
    const url = `https://www.tiktok.com/search?q=${encodeURIComponent(query)}`;
    const resp = await axios.get(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
      },
      timeout: 10000,
    });

    // Extract video URLs from HTML using regex
    const videoUrls = [...resp.data.matchAll(/https?:\/\/[^"'\s]+tiktok[^"'\s]*\/video\/\d+/gi)].map(m => m[0]);
    return [...new Set(videoUrls)].slice(0, maxResults).map(url => ({
      url,
      title: 'TikTok video',
      platform: 'tiktok',
    }));
  } catch (err) {
    console.error(`[TikTokFinder] Error: ${err.message.substring(0, 100)}`);
    return [];
  }
}

module.exports = { findVideos };
