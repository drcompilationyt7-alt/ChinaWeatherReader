/**
 * Mr. WorldWideWebster - Decision Engine
 * 
 * The AI brain that analyzes incoming content and decides:
 * - CLIP: Visually entertaining, crop to 9:16 Shorts
 * - VOICEOVER: Needs translation + new narration
 * - EXPLAIN: "What is this...?" format for foreign/unknown things
 * - AI CREATE: Original content about a trend/comparison
 * 
 * Each decision includes: confidence score, reasoning, and suggested title format.
 */
const { Logger } = require('./logger');

class DecisionEngine {
  constructor(aiService) {
    this.ai = aiService;
    this.logger = new Logger('DecisionEngine');
  }

  /**
   * Analyze content and decide the best processing path
   * 
   * @param {Object} content - { platform, title, description, duration, hasAudio, hasSpeech, languageDetected, url, thumbnailUrl }
   * @returns {Object} { path, confidence, reasoning, suggestedTitle }
   */
  async decidePath(content) {
    this.logger.info(`Analyzing content from ${content.platform}: "${content.title?.substring(0, 60)}"`);

    const systemPrompt = `You are the decision engine for "Mr. WorldWideWebster", an AI YouTube channel that shows people what's trending around the world.

Your job: Given a piece of content from a foreign platform, decide the BEST way to repurpose it.

AVAILABLE PATHS:

1. CLIP — "Let the content speak for itself"
   When: The content is VISUALLY entertaining and can be understood without words
   Examples: Funny fails, dance routines, magic tricks, animals, reactions, visual spectacles
   Return: { "path": "clip", "confidence": 85, "reasoning": "...", "suggestedTitle": "...", "hookStrategy": "visual_hook" }

2. VOICEOVER — "Translate and re-narrate"
   When: Someone is SPEAKING in a foreign language, or the audio contains important information that needs translation
   Examples: News reports, educational content, vlogs in Chinese/French/etc., interviews
   Return: { "path": "voiceover", "confidence": 85, "reasoning": "...", "suggestedTitle": "...", "hookStrategy": "translated_hook" }

3. EXPLAIN — "What is this...?"
   When: The content features something your audience would ask "What is this?" about
   Examples: Strange foreign food, unknown music genre, cultural practice, unique product, local trend, celebrity they don't know
   Return: { "path": "explain", "confidence": 85, "reasoning": "...", "suggestedTitle": "...", "explainThing": "the thing being explained", "explainCategory": "food|music|dance|trend|place|culture|product|other" }

4. AI_CREATE — "Create original content about this"
   When: The content is ABOUT a trend/comparison that needs original script + visuals
   Examples: "US songs vs UK songs", "Top Chinese memes explained", "News summary"
   Return: { "path": "ai_create", "confidence": 85, "reasoning": "...", "suggestedTitle": "...", "contentType": "comparison|explainer|news_summary|listicle" }

CONTENT INFORMATION:
- Platform: {platform}
- Title: {title}
- Description: {description}
- Duration: {duration}s
- Has speech audio: {hasSpeech}
- Language detected: {languageDetected}
- Is primarily visual: {isVisual}

Respond with JSON only. Be decisive - pick the single best path.`;

    const userMessage = `Analyze this content and decide the best path for Mr. WorldWideWebster.`;

    try {
      // Build the prompt with actual content
      const filledPrompt = systemPrompt
        .replace('{platform}', content.platform || 'unknown')
        .replace('{title}', content.title || 'Untitled')
        .replace('{description}', (content.description || '').substring(0, 500))
        .replace('{duration}', content.duration || 0)
        .replace('{hasSpeech}', content.hasSpeech !== false)
        .replace('{languageDetected}', content.languageDetected || 'unknown')
        .replace('{isVisual}', content.isVisual !== false);

      const decision = await this.ai.chatJSON(filledPrompt, userMessage, {
        temperature: 0.4,
        useScriptModel: true,
      });

      this.logger.success(`Decision: ${decision.path} (${decision.confidence}%) - ${decision.reasoning}`);
      return decision;
    } catch (error) {
      this.logger.error(`Decision engine failed: ${error.message}`);
      // Fallback: default to CLIP for visual content, EXPLAIN for unknown
      return {
        path: content.isVisual ? 'clip' : 'explain',
        confidence: 50,
        reasoning: `Fallback decision due to AI error: ${error.message}`,
        suggestedTitle: content.title || 'Interesting content from around the world',
        explainThing: 'this content',
        explainCategory: 'other',
      };
    }
  }

  /**
   * Batch analyze multiple content items and rank them
   */
  async rankContent(contentItems) {
    this.logger.info(`Ranking ${contentItems.length} content items`);

    const decisions = [];
    for (const item of contentItems) {
      const decision = await this.decidePath(item);
      decisions.push({
        ...item,
        decision,
        priority: decision.confidence,
      });
    }

    // Sort by confidence score, highest first
    decisions.sort((a, b) => b.priority - a.priority);

    return decisions;
  }

  /**
   * Generate a viral title for the content
   */
  async generateTitle(content, decision) {
    const systemPrompt = `You are a title-writer for "Mr. WorldWideWebster". Create clickable YouTube Shorts titles that make people curious about global content.

Rules:
- Keep it under 60 characters
- Use curiosity gaps: "You won't believe what's trending in China..."
- Use numbers: "3 things Americans don't understand about Japan"
- Use "What is this?" for explainer content
- Use comparisons: "US vs UK: Who does it better?"
- Be specific: name the platform (Bilibili, Douyin, TikTok)
- Don't use clickbait that's misleading

Examples:
- "China's favorite TikTok dance right now 🇨🇳"
- "What is this fruit? (Nigeria edition) 🥭"
- "Bilibili streamer goes viral for THIS 😂"
- "UK Drill vs US Trap: The real difference 🎵"

Path: ${decision.path}
Content: ${content.title}
Platform: ${content.platform}

Return ONLY the title, no quotes, no explanation.`;

    try {
      const title = await this.ai.chat(systemPrompt, 'Generate a viral YouTube Shorts title for this content.', {
        temperature: 0.8,
        maxTokens: 100,
      });
      return title.trim().replace(/^["']|["']$/g, '');
    } catch (error) {
      return content.title || 'Amazing content from around the world';
    }
  }
}

module.exports = { DecisionEngine };