/**
 * URL Ranker
 * Uses OpenRouter AI to rank video URLs by relevance/viral potential
 * using ONLY title, description, and duration - no video download needed.
 * Then picks top 3 and identifies which one should be an explainer.
 */
const { Logger } = require('./logger');

const logger = new Logger('URLRanker');

/**
 * Rank a list of video URLs using their metadata
 * @param {Array} videos - [{url, title, platform}, ...]
 * @param {string} query - Original search query
 * @param {Object} ai - AIService instance
 * @returns {Object} { top3: [...], explainer: {...}, clips: [...] }
 */
async function rankVideos(videos, query, ai) {
  if (videos.length === 0) return { top3: [], explainer: null, clips: [] };

  try {
    // Build a compact list for the AI
    const videoList = videos.map((v, i) => `[${i+1}] "${(v.title||'').substring(0, 80)}" (${v.platform})`).join('\n');
    
    const result = await ai.chatJSON(
      `You are a content curator for Mr. WorldWideWebster - a channel showing global viral content.

Search query: "${query}"

Videos found:\n${videoList}

Task: Rank these videos by how well they fit this channel. Return JSON:
{
  "ranked": [1, 3, 5, ...], // video indices sorted by best fit (best first)
  "explainer_index": 3,      // which of the top 3 would make the best explainer (or null)
  "reasons": ["why vid 1 is best", "why vid 2", ...]  // one reason per ranked video
}

Criteria:
- Memes/streamer moments > explainer content > general videos
- Shorter videos (<10 min) preferred
- Videos with clear titles about trends/culture/dances/food/music score higher
- Videos that look like they can be trimmed to a 30-60s short
- Prefer videos from different creators and countries`,
      `Rank these ${videos.length} videos for "${query}"`,
      { useCheapModel: true, temperature: 0.3 }
    );

    const ranked = result.ranked || [];
    const reasons = result.reasons || [];
    const explainerIdx = result.explainer_index;

    // Convert 1-based indices to 0-based
    const rankedVideos = ranked
      .map(idx => videos[idx - 1])
      .filter(Boolean)
      .slice(0, 3);
    
    const top3 = rankedVideos.map((v, i) => ({
      ...v,
      rankReason: reasons[i] || ''
    }));
    
    const explainer = explainerIdx ? top3.find((_, i) => ranked.indexOf(explainerIdx) === i) : top3[top3.length - 1];
    const clips = top3.filter(v => v !== explainer);
    
    // Publish reasons
    top3.forEach((v, i) => {
      logger.info(`  #${i+1}: ${(v.title||'').substring(0, 50)} - ${v.rankReason}`);
    });
    if (explainer && top3.length > 1) {
      logger.info(`  Explainer: ${(explainer.title||'').substring(0, 50)}`);
    }

    return { top3, explainer, clips };
  } catch (e) {
    logger.warn(`Ranking failed: ${e.message.substring(0, 80)}, using first 3`);
    const top3 = videos.slice(0, 3);
    return { top3, explainer: top3[top3.length - 1], clips: top3.slice(0, -1) };
  }
}

/**
 * Generate an explainer script and matching search query
 */
async function generateExplainerContent(video, ai) {
  const title = video.title || 'content';
  try {
    const result = await ai.chatJSON(
      `You write scripts for Mr. WorldWideWebster explainer shorts.

Video title: "${title}"
Platform: ${video.platform || 'web'}

Generate:
1. A 15-20 second explainer script like:
"What is this? This is [NAME]. It's from [COUNTRY] and here's why it's special. [1 interesting fact]."

2. A search query to find matching video clips on YouTube (for background visuals)
Example: if the video is about xiao long bao, search "making xiao long bao" or "xiao long bao cooking"

Return JSON:
{
  "script": "The full script text",
  "search_query": "search query for finding matching clips",
  "explainer_text": "What is this? This is [NAME]",
  "category": "food|music|dance|trend|culture|other"
}`,
      `Generate explainer for ${title}`,
      { useScriptModel: true, temperature: 0.7 }
    );
    return result;
  } catch {
    return {
      script: `What is this? This is ${title.substring(0, 50)}. It's amazing global content!`,
      search_query: title.substring(0, 60),
      explainer_text: `What is this? This is ${title.substring(0, 30)}...`,
      category: 'other'
    };
  }
}

module.exports = { rankVideos, generateExplainerContent };
