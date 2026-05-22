/**
 * Mr. WorldWideWebster — Background Music Finder
 *
 * Hermes uses this to find and download FREE background music
 * that fits the mood/type of any video being created.
 *
 * Sources (all free, no attribution):
 * - Pixabay Music (free royalty-free music)
 * - YouTube Audio Library (free music for creators)
 * - Free Music Archive (public domain/CC)
 *
 * Music is categorized by mood so Hermes can match it intelligently:
 * - cinematic  → epic, orchestral, dramatic
 * - upbeat     → happy, energetic, positive
 * - chill      → lo-fi, ambient, relaxing
 * - intense    → action, suspense, thrilling
 * - funny      → comedy, playful, quirky
 * - emotional  → sad, touching, inspiring
 *
 * All downloads use yt-dlp or direct HTTP — no API keys needed.
 */
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const axios = require('axios');
const { Logger } = require('../core/logger');

class MusicFinder {
  constructor() {
    this.logger = new Logger('MusicFinder');
    this.musicDir = path.join(__dirname, '..', 'output', 'assets', 'music');
    if (!fs.existsSync(this.musicDir)) {
      fs.mkdirSync(this.musicDir, { recursive: true });
    }
  }

  /**
   * MAIN ENTRY: Find background music matching a video's mood/category
   * @param {Object} params - { mood, category, duration, videoTitle }
   * @returns {Promise<Object>} - { filePath, title, artist, duration, source }
   */
  async findMusic(params) {
    const mood = params.mood || this._inferMoodFromCategory(params.category || 'general', params.videoTitle || '');
    const targetDuration = params.duration || 60;

    this.logger.info(`Finding music for mood: "${mood}" (target: ${targetDuration}s)`);

    // Try Pixabay Music first (best free source)
    try {
      const result = await this._searchPixabayMusic(mood, targetDuration);
      if (result) {
        this.logger.success(`Found music: "${result.title}" by ${result.artist} (${result.source})`);
        return result;
      }
    } catch (error) {
      this.logger.warn(`Pixabay music search failed: ${error.message}`);
    }

    // Fallback: YouTube Audio Library search
    try {
      const result = await this._searchYouTubeAudioLibrary(mood, targetDuration);
      if (result) {
        this.logger.success(`Found music: "${result.title}" by ${result.artist} (${result.source})`);
        return result;
      }
    } catch (error) {
      this.logger.warn(`YouTube Audio Library search failed: ${error.message}`);
    }

    // Last resort: generic royalty-free search via yt-dlp
    try {
      const result = await this._searchGenericRoyaltyFree(mood, targetDuration);
      if (result) {
        this.logger.success(`Found music: "${result.title}" by ${result.artist} (${result.source})`);
        return result;
      }
    } catch (error) {
      this.logger.warn(`Generic music search failed: ${error.message}`);
    }

    this.logger.warn('No music found — video will have no background music');
    return null;
  }

  /**
   * Infer the mood of a video based on its category and title
   */
  _inferMoodFromCategory(category, title) {
    const lowerTitle = (title || '').toLowerCase();
    
    // Category-based mood mapping
    const categoryMoods = {
      architecture: 'cinematic',
      cityscape: 'cinematic',
      travel: 'cinematic',
      meme: 'funny',
      funny: 'funny',
      comedy: 'funny',
      streamer: 'upbeat',
      gaming: 'intense',
      explainer: 'chill',
      educational: 'chill',
      compilation: 'cinematic',
      versus: 'intense',
      comparison: 'intense',
      listicle: 'upbeat',
      news: 'emotional',
      sad: 'emotional',
      emotional: 'emotional',
      inspirational: 'emotional',
    };

    // Check title keywords
    if (lowerTitle.match(/epic|cinematic|amazing|beautiful|stunning|incredible/)) return 'cinematic';
    if (lowerTitle.match(/funny|hilarious|meme|comedy|lol|laugh/)) return 'funny';
    if (lowerTitle.match(/sad|emotional|touching|heartbreaking|beautiful story/)) return 'emotional';
    if (lowerTitle.match(/intense|action|epic battle|fight|suspense|thrilling/)) return 'intense';
    if (lowerTitle.match(/chill|relax|ambient|lo-fi|calm|peaceful/)) return 'chill';
    if (lowerTitle.match(/upbeat|happy|energetic|fun|party|vibrant/)) return 'upbeat';

    // Fall back to category
    for (const [cat, mood] of Object.entries(categoryMoods)) {
      if (category === cat || lowerTitle.includes(cat)) return mood;
    }

    return 'chill'; // Default safe mood
  }

  /**
   * Search Pixabay Music for free tracks matching a mood
   * Pixabay has a free music API (no key required for search)
   */
  async _searchPixabayMusic(mood, targetDuration) {
    this.logger.info('Searching Pixabay Music...');

    const moodToGenre = {
      cinematic: 'cinematic',
      upbeat: 'upbeat',
      chill: 'ambient',
      intense: 'dramatic',
      funny: 'comedy',
      emotional: 'sad',
    };

    const genre = moodToGenre[mood] || mood;

    // Pixabay has a free search API for music
    const url = `https://pixabay.com/api/v1/music/?q=${encodeURIComponent(genre)}&per_page=5`;
    
    try {
      const response = await axios.get(url, { timeout: 15000 });
      const tracks = response.data?.hits || [];

      // Find a track close to our target duration
      let bestTrack = null;
      let bestDiff = Infinity;

      for (const track of tracks) {
        const trackDuration = track.duration || track.seconds || 0;
        const diff = Math.abs(trackDuration - targetDuration);
        if (diff < bestDiff) {
          bestDiff = diff;
          bestTrack = track;
        }
      }

      if (!bestTrack) return null;

      // Download the track
      const audioUrl = bestTrack.audio_url || bestTrack.preview_url || bestTrack.url;
      if (!audioUrl) return null;

      const ext = path.extname(audioUrl.split('?')[0]) || '.mp3';
      const outputFile = path.join(this.musicDir, `pixabay_${mood}_${Date.now()}${ext}`);
      
      await this._downloadFile(audioUrl, outputFile);

      if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 1000) {
        return {
          filePath: outputFile,
          title: bestTrack.title || `Pixabay ${mood} track`,
          artist: bestTrack.user || bestTrack.author || 'Pixabay',
          duration: bestTrack.duration || bestTrack.seconds || 0,
          source: 'pixabay',
          mood: mood,
          url: bestTrack.pageURL || audioUrl,
        };
      }
    } catch (error) {
      this.logger.warn(`Pixabay API error: ${error.message}`);
    }

    return null;
  }

  /**
   * Search YouTube Audio Library via yt-dlp
   * YouTube has a massive library of free music for creators
   */
  async _searchYouTubeAudioLibrary(mood, targetDuration) {
    this.logger.info('Searching YouTube Audio Library...');

    // YouTube Audio Library search terms
    const moodSearchTerms = {
      cinematic: 'cinematic epic royalty free music',
      upbeat: 'upbeat happy royalty free music',
      chill: 'ambient chill royalty free music',
      intense: 'dramatic intense royalty free music',
      funny: 'funny comedy royalty free music',
      emotional: 'emotional sad royalty free music',
    };

    const searchTerm = moodSearchTerms[mood] || `${mood} royalty free music`;
    const safeName = searchTerm.replace(/[^a-zA-Z0-9]/g, '_').substring(0, 50);

    try {
      // Search YouTube via yt-dlp
      const searchCmd = `yt-dlp --flat-playlist --dump-json "ytsearch5:${searchTerm}" 2>nul`;
      let output;
      try {
        output = execSync(searchCmd, { timeout: 20000, maxBuffer: 5 * 1024 * 1024 }).toString();
      } catch {
        return null;
      }

      const entries = output.trim().split('\n').filter(Boolean).map(line => {
        try { return JSON.parse(line); } catch { return null; }
      }).filter(Boolean);

      if (entries.length === 0) return null;

      // Pick the best match — prefer audio-only (music tracks)
      // and match target duration
      let bestEntry = null;
      let bestDiff = Infinity;

      for (const entry of entries) {
        const duration = entry.duration || 0;
        if (duration < 10) continue; // Skip too short
        const diff = Math.abs(duration - targetDuration);
        if (diff < bestDiff) {
          bestDiff = diff;
          bestEntry = entry;
        }
      }

      if (!bestEntry) return null;

      // Download as audio only
      const outputFile = path.join(this.musicDir, `yt_audio_${safeName}_${Date.now()}.mp3`);
      
      execSync(
        `yt-dlp -x --audio-format mp3 -o "${outputFile}" "https://www.youtube.com/watch?v=${bestEntry.id}" 2>nul`,
        { timeout: 90000, stdio: 'ignore' }
      );

      // yt-dlp adds extensions, find the actual file
      const actualFile = this._findDownloadedFile(outputFile);
      
      if (actualFile && fs.existsSync(actualFile) && fs.statSync(actualFile).size > 10000) {
        return {
          filePath: actualFile,
          title: bestEntry.title || `YouTube ${mood} track`,
          artist: bestEntry.channel || bestEntry.uploader || 'YouTube Audio Library',
          duration: bestEntry.duration || 0,
          source: 'youtube-audio-library',
          mood: mood,
          url: `https://www.youtube.com/watch?v=${bestEntry.id}`,
        };
      }
    } catch (error) {
      this.logger.warn(`YouTube Audio Library error: ${error.message}`);
    }

    return null;
  }

  /**
   * Generic royalty-free music search as last resort
   */
  async _searchGenericRoyaltyFree(mood, targetDuration) {
    this.logger.info('Searching generic royalty-free music...');

    const searchTerms = [
      'no copyright music',
      'royalty free music',
      'background music free',
      'free stock music',
    ];

    const moodModifier = mood;
    const query = `${moodModifier} ${searchTerms[Math.floor(Math.random() * searchTerms.length)]}`;

    try {
      const searchCmd = `yt-dlp --flat-playlist --dump-json "ytsearch5:${query}" 2>nul`;
      let output;
      try {
        output = execSync(searchCmd, { timeout: 20000, maxBuffer: 5 * 1024 * 1024 }).toString();
      } catch {
        return null;
      }

      const entries = output.trim().split('\n').filter(Boolean).map(line => {
        try { return JSON.parse(line); } catch { return null; }
      }).filter(Boolean);

      if (entries.length === 0) return null;

      // Pick the first one that's within reasonable duration
      for (const entry of entries) {
        const duration = entry.duration || 0;
        if (duration < 15 || duration > 600) continue;

        const safeTitle = entry.title ? entry.title.replace(/[^a-zA-Z0-9]/g, '_').substring(0, 30) : 'music';
        const outputFile = path.join(this.musicDir, `generic_${safeTitle}_${Date.now()}.mp3`);

        try {
          execSync(
            `yt-dlp -x --audio-format mp3 -o "${outputFile}" "https://www.youtube.com/watch?v=${entry.id}" 2>nul`,
            { timeout: 90000, stdio: 'ignore' }
          );

          const actualFile = this._findDownloadedFile(outputFile);
          
          if (actualFile && fs.existsSync(actualFile) && fs.statSync(actualFile).size > 10000) {
            return {
              filePath: actualFile,
              title: entry.title || 'Royalty free music',
              artist: entry.channel || entry.uploader || 'Unknown',
              duration: duration,
              source: 'generic-youtube',
              mood: mood,
              url: `https://www.youtube.com/watch?v=${entry.id}`,
            };
          }
        } catch {
          continue;
        }
      }
    } catch {
      return null;
    }

    return null;
  }

  /**
   * Find actual downloaded file (yt-dlp may append metadata)
   */
  _findDownloadedFile(basePath) {
    const dir = path.dirname(basePath);
    const basename = path.basename(basePath, '.mp3');
    
    try {
      const files = fs.readdirSync(dir);
      for (const file of files) {
        if (file.includes(basename) && (file.endsWith('.mp3') || file.endsWith('.m4a') || file.endsWith('.opus'))) {
          return path.join(dir, file);
        }
      }
    } catch {}

    // Return base path as fallback
    return basePath + '.mp3';
  }

  /**
   * Download a file via HTTP
   */
  async _downloadFile(url, outputPath) {
    const writer = fs.createWriteStream(outputPath);
    const response = await axios({
      method: 'GET',
      url: url,
      responseType: 'stream',
      timeout: 30000,
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
      },
    });
    response.data.pipe(writer);
    return new Promise((resolve, reject) => {
      writer.on('finish', resolve);
      writer.on('error', reject);
    });
  }

  /**
   * Trim/loop audio to match exact duration using FFmpeg
   */
  async trimToDuration(audioPath, targetDurationSeconds) {
    if (!fs.existsSync(audioPath)) return audioPath;

    const outputPath = audioPath.replace('.mp3', `_trimmed_${targetDurationSeconds}s.mp3`);

    try {
      execSync(
        `ffmpeg -i "${audioPath}" -t ${targetDurationSeconds} -c copy "${outputPath}" 2>&1`,
        { timeout: 30000 }
      );

      if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 1000) {
        // Replace original
        fs.unlinkSync(audioPath);
        fs.renameSync(outputPath, audioPath);
      }
    } catch (error) {
      this.logger.warn(`Audio trim failed: ${error.message}`);
    }

    return audioPath;
  }

  /**
   * Get audio duration using ffprobe
   */
  getAudioDuration(audioPath) {
    try {
      const output = execSync(
        `ffprobe -i "${audioPath}" -show_entries format=duration -v quiet -of csv="p=0"`,
        { timeout: 10000 }
      ).toString().trim();
      return Math.ceil(parseFloat(output) || 0);
    } catch {
      return 0;
    }
  }

  /**
   * Pre-download a set of default music tracks for common moods
   * so they're cached for later use
   */
  async cacheDefaultTracks() {
    const moods = ['cinematic', 'chill', 'upbeat', 'funny'];
    const results = {};

    for (const mood of moods) {
      this.logger.info(`Caching ${mood} music...`);
      const music = await this.findMusic({ mood, duration: 60 });
      if (music) {
        results[mood] = music;
      }
    }

    return results;
  }
}

module.exports = { MusicFinder };