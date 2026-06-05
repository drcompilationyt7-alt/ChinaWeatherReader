/**
 * Mr. WorldWideWebster — YouTube Automation Bridge
 * 
 * Integrates the youtube-automation-agent-master's publishing and analytics
 * capabilities into Mr. WorldWideWebster's automation system.
 * 
 * Provides:
 * 1. YouTube OAuth authentication
 * 2. Video uploading with thumbnails + captions
 * 3. Channel analytics
 * 4. Publishing schedule optimization
 * 
 * Uses the youtube-automation-agent-master code at:
 *   C:\Users\Home\Downloads\yt ai\youtube-automation-agent-master
 */
const { google } = require('googleapis');
const path = require('path');
const fs = require('fs');
const { Logger } = require('../core/logger');
const config = require('../core/config');

class YouTubeBridge {
  constructor() {
    this.logger = new Logger('YouTubeBridge');
    this.youtube = null;
    this.oauth2Client = null;
    this.authenticated = false;
    this.credentialsPath = path.resolve(__dirname, '..', '..', 'youtube-automation-agent-master', 'config');
  }

  /**
   * Initialize YouTube API with OAuth
   */
  async initialize() {
    this.logger.info('Initializing YouTube bridge...');

    // Try to load existing credentials from youtube-automation-agent-master
    const credsLoaded = await this._loadExistingCredentials();
    
    if (!credsLoaded) {
      this.logger.warn('YouTube credentials not found. Run: node youtube-automation/setup-youtube');
      return false;
    }

    this.logger.success('YouTube bridge initialized');
    return true;
  }

  /**
   * Load credentials from youtube-automation-agent-master/config/
   */
  async _loadExistingCredentials() {
    try {
      // Check if the youtube-automation-agent-master config exists
      const credsPath = path.join(this.credentialsPath, 'credentials.json');
      const tokensPath = path.join(this.credentialsPath, 'tokens.json');

      if (!fs.existsSync(credsPath) || !fs.existsSync(tokensPath)) {
        // Try .env based credentials
        if (config.youtube.clientId && config.youtube.clientSecret) {
          this.logger.info('Using .env YouTube credentials');
          return await this._setupFromEnv();
        }
        return false;
      }

      const credentials = JSON.parse(fs.readFileSync(credsPath, 'utf8'));
      const tokens = JSON.parse(fs.readFileSync(tokensPath, 'utf8'));

      if (!credentials.youtube || !tokens.youtube) {
        return false;
      }

      this.oauth2Client = new google.auth.OAuth2(
        credentials.youtube.client_id,
        credentials.youtube.client_secret,
        credentials.youtube.redirect_uris?.[0] || 'http://localhost:8080/oauth2callback'
      );

      this.oauth2Client.setCredentials(tokens.youtube);
      this.youtube = google.youtube({ version: 'v3', auth: this.oauth2Client });
      this.authenticated = true;

      // Verify by making a test call
      const test = await this.youtube.channels.list({ part: 'snippet', mine: true });
      this.logger.success(`Authenticated as: ${test.data.items?.[0]?.snippet?.title || 'YouTube user'}`);

      return true;
    } catch (error) {
      this.logger.error(`Failed to load YouTube credentials: ${error.message}`);
      return false;
    }
  }

  /**
   * Set up from .env variables as fallback
   */
  async _setupFromEnv() {
    try {
      this.oauth2Client = new google.auth.OAuth2(
        config.youtube.clientId,
        config.youtube.clientSecret,
        'http://localhost:8080/oauth2callback'
      );

      // Apply refresh token if available
      const refreshToken = process.env.YOUTUBE_REFRESH_TOKEN;
      if (refreshToken) {
        this.oauth2Client.setCredentials({
          refresh_token: refreshToken,
        });
        this.logger.info('YouTube refresh token applied');
      } else {
        this.logger.warn('No YOUTUBE_REFRESH_TOKEN set — uploads will fail');
        return false;
      }

      this.youtube = google.youtube({ version: 'v3', auth: this.oauth2Client });
      this.authenticated = true;
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Detect video orientation from the file using ffprobe
   * @param {string} videoPath - Path to the video file
   * @returns {Promise<string>} - 'portrait' (9:16), 'landscape' (16:9), or 'square'
   */
  async _detectOrientation(videoPath) {
    try {
      const { execSync } = require('child_process');
      const probeCmd = `ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "${videoPath}"`;
      const output = execSync(probeCmd, { timeout: 10000 }).toString().trim();
      const parts = output.split(',').map(s => parseInt(s.trim()));
      const width = parts[0];
      const height = parts[1];
      
      if (!width || !height) return 'unknown';
      
      const ratio = width / height;
      
      // Portrait (9:16 or similar vertical aspect)
      if (ratio < 0.7) return 'portrait';
      // Square (1:1 or close)
      if (ratio >= 0.7 && ratio <= 1.3) return 'square';
      // Landscape (16:9 or similar)
      return 'landscape';
    } catch {
      return 'unknown';
    }
  }

  /**
   * Upload a video to YouTube
   * @param {Object} params - { videoPath, title, description, tags, thumbnailPath, captionsPath }
   * @returns {Promise<Object>} - { videoId, url, publishedAt, orientation }
   */
  async uploadVideo(params) {
    if (!this.authenticated) {
      throw new Error('YouTube not authenticated. Run: node youtube-automation/setup-youtube');
    }

    const { videoPath, title, description, tags, thumbnailPath, captionsPath } = params;

    this.logger.info(`Uploading: "${title?.substring(0, 60) || 'Untitled'}"`);

    // Detect orientation for proper categorization
    const orientation = await this._detectOrientation(videoPath);
    this.logger.info(`Video orientation: ${orientation} (${orientation === 'portrait' ? 'YouTube Shorts' : 'Standard landscape video'})`);

    // Prepare video metadata
    // Support scheduled publishing via publishAt parameter
    const privacyStatus = config.youtube.privacyStatus || 'public';
    // Check if publishAt is valid: must be at least 15 minutes in the future
    let publishAt = null;
    let useScheduled = false;
    if (params.publishAt) {
      const scheduleTime = new Date(params.publishAt);
      const minValid = new Date(Date.now() + 15 * 60 * 1000); // 15 min from now
      if (scheduleTime > minValid) {
        publishAt = params.publishAt;
        useScheduled = true;
        this.logger.info(`Scheduled upload at: ${publishAt}`);
      } else {
        this.logger.warn(`publishAt (${params.publishAt}) is too close or in the past — falling back to immediate public publish`);
      }
    } else {
      this.logger.info('No publishAt provided — uploading as public immediately');
    }
    const isScheduled = useScheduled;

    const videoMetadata = {
      snippet: {
        title: title || 'Mr. WorldWideWebster - Global Content',
        description: description || `${title}\n\n🌍 Bringing the world to you\n\nFollow Mr. WorldWideWebster for more global content!`,
        tags: tags || ['mr worldwidewebster', 'global', 'culture', 'international'],
        categoryId: '22',
        defaultLanguage: 'en',
        defaultAudioLanguage: 'en',
      },
      status: {
        privacyStatus: isScheduled ? 'private' : privacyStatus,
        selfDeclaredMadeForKids: false,
      },
    };

    // Add publishAt for scheduled publishing (must be > current time and < 2 weeks out)
    if (isScheduled) {
      videoMetadata.status.publishAt = params.publishAt;
      // YouTube requires scheduled videos to be private initially
      videoMetadata.status.privacyStatus = 'private';
    }

    try {
      // Upload the video
      const parts = isScheduled ? ['snippet', 'status'] : ['snippet', 'status'];
      const response = await this.youtube.videos.insert({
        part: parts,
        requestBody: videoMetadata,
        media: {
          body: fs.createReadStream(videoPath),
          mimeType: 'video/mp4',
        },
      });

      const videoId = response.data.id;
      this.logger.success(`Video uploaded: https://www.youtube.com/watch?v=${videoId}`);

      // Upload thumbnail if provided
      if (thumbnailPath && fs.existsSync(thumbnailPath)) {
        try {
          await this.youtube.thumbnails.set({
            videoId: videoId,
            media: { body: fs.createReadStream(thumbnailPath) },
          });
          this.logger.info('Thumbnail uploaded');
        } catch (thumbError) {
          this.logger.warn(`Thumbnail upload failed: ${thumbError.message}`);
        }
      }

      // Upload captions if provided
      if (captionsPath && fs.existsSync(captionsPath)) {
        try {
          await this.youtube.captions.insert({
            part: ['snippet'],
            requestBody: {
              snippet: {
                videoId: videoId,
                language: 'en',
                name: 'English',
                isDraft: false,
              },
            },
            media: { body: fs.createReadStream(captionsPath) },
          });
          this.logger.info('Captions uploaded');
        } catch (captionError) {
          this.logger.warn(`Captions upload failed: ${captionError.message}`);
        }
      }

      return {
        videoId,
        url: `https://www.youtube.com/watch?v=${videoId}`,
        publishedAt: new Date().toISOString(),
        title: title,
      };
    } catch (error) {
      this.logger.error(`Upload failed: ${error.message}`);
      throw error;
    }
  }

  /**
   * Get channel analytics
   * @returns {Promise<Object>} - { subscribers, totalViews, totalVideos }
   */
  async getChannelStats() {
    if (!this.authenticated) {
      return { subscribers: 0, totalViews: 0, totalVideos: 0 };
    }

    try {
      const response = await this.youtube.channels.list({
        part: ['statistics', 'snippet'],
        mine: true,
      });

      const channel = response.data.items?.[0];
      if (!channel) throw new Error('Channel not found');

      return {
        title: channel.snippet.title,
        subscribers: parseInt(channel.statistics.subscriberCount) || 0,
        totalViews: parseInt(channel.statistics.viewCount) || 0,
        totalVideos: parseInt(channel.statistics.videoCount) || 0,
      };
    } catch (error) {
      this.logger.error(`Failed to get channel stats: ${error.message}`);
      return { subscribers: 0, totalViews: 0, totalVideos: 0 };
    }
  }

  /**
   * Get recent video analytics
   */
  async getVideoAnalytics(videoId) {
    if (!this.authenticated) {
      return null;
    }

    try {
      const response = await this.youtube.videos.list({
        part: ['statistics', 'snippet'],
        id: videoId,
      });

      const video = response.data.items?.[0];
      if (!video) return null;

      return {
        title: video.snippet.title,
        views: parseInt(video.statistics.viewCount) || 0,
        likes: parseInt(video.statistics.likeCount) || 0,
        comments: parseInt(video.statistics.commentCount) || 0,
        publishedAt: video.snippet.publishedAt,
      };
    } catch (error) {
      this.logger.error(`Failed to get video analytics: ${error.message}`);
      return null;
    }
  }

  /**
   * Post a comment on a video
   * @param {string} videoId - The YouTube video ID
   * @param {string} text - The comment text
   * @returns {Promise<Object|null>} - { commentId, text } or null on failure
   */
  async postComment(videoId, text) {
    if (!this.authenticated) {
      this.logger.warn('YouTube not authenticated — cannot post comment');
      return null;
    }

    try {
      const response = await this.youtube.commentThreads.insert({
        part: ['snippet'],
        requestBody: {
          snippet: {
            videoId: videoId,
            topLevelComment: {
              snippet: {
                textOriginal: text,
              },
            },
          },
        },
      });

      const commentId = response.data?.id;
      this.logger.success(`Comment posted: ${commentId}`);
      return { commentId, text };
    } catch (error) {
      this.logger.warn(`Failed to post comment: ${error.message}`);
      return null;
    }
  }

  /**
   * Check authentication status
   */
  isAuthenticated() {
    return this.authenticated;
  }
}

module.exports = { YouTubeBridge };
