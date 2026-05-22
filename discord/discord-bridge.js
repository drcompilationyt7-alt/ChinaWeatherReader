#!/usr/bin/env node

/**
 * Mr. WorldWideWebster — Discord Notification Bridge
 *
 * Uses the persistent DISCORD_BOT_TOKEN from GitHub Secrets to send
 * daily summaries, weekly reports, and error notifications to your
 * Discord server. The bot identity persists across GH Action runs
 * because the same token is used every time.
 *
 * Usage:
 *   node discord/discord-bridge.js --test              # Test connection
 *   node discord/discord-bridge.js --daily <json>     # Send daily summary
 *   node discord/discord-bridge.js --weekly <json>    # Send weekly report
 *   node discord/discord-bridge.js --alert <message>  # Send error alert
 *
 * Token comes from: process.env.DISCORD_BOT_TOKEN
 * Set it in GitHub Secrets (Settings → Secrets and variables → Actions)
 */
const { Client, GatewayIntentBits, EmbedBuilder } = require('discord.js');
const { Logger } = require('../core/logger');

class DiscordBridge {
  constructor() {
    this.logger = new Logger('DiscordBot');
    this.client = null;
    this.token = process.env.DISCORD_BOT_TOKEN || null;
    this.targetChannelId = process.env.DISCORD_CHANNEL_ID || null;
    this.targetUserId = process.env.DISCORD_USER_ID || null;
    this.ready = false;
  }

  /**
   * Initialize the Discord bot connection
   */
  async initialize() {
    if (!this.token) {
      this.logger.warn('DISCORD_BOT_TOKEN not set — Discord notifications disabled');
      this.logger.warn('Set it in GitHub Secrets: Settings → Secrets and variables → Actions');
      return false;
    }

    this.logger.info('Initializing Discord bot...');

    try {
      this.client = new Client({
        intents: [
          GatewayIntentBits.Guilds,
          GatewayIntentBits.GuildMessages,
          GatewayIntentBits.MessageContent,
          GatewayIntentBits.DirectMessages,
        ],
      });

      // Wait for the client to be ready
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('Discord login timed out')), 15000);

        this.client.once('ready', () => {
          clearTimeout(timeout);
          this.logger.success(`Discord bot connected: ${this.client.user?.tag || 'Unknown'}`);
          this.ready = true;
          resolve();
        });

        this.client.once('error', (error) => {
          clearTimeout(timeout);
          reject(error);
        });

        this.client.login(this.token).catch(reject);
      });

      return true;
    } catch (error) {
      this.logger.error(`Discord bot failed: ${error.message}`);
      this.ready = false;
      return false;
    }
  }

  /**
   * Send message to target: DM if DISCORD_USER_ID set, otherwise use channel
   */
  async _getTarget() {
    if (!this.client || !this.ready) return null;

    // Priority 1: DM user by their ID (Installation flow)
    if (this.targetUserId) {
      try {
        const user = await this.client.users.fetch(this.targetUserId);
        if (user) {
          // Create DM channel
          const dmChannel = await user.createDM();
          this.logger.info(`Sending to DM: ${user.tag}`);
          return dmChannel;
        }
      } catch (error) {
        this.logger.warn(`Could not DM user ${this.targetUserId}: ${error.message}`);
        this.logger.warn('Make sure the bot can DM you (see GITHUB-SECRETS-SETUP.md)');
      }
    }

    // Priority 2: Specific channel ID
    if (this.targetChannelId) {
      try {
        const channel = await this.client.channels.fetch(this.targetChannelId);
        if (channel && channel.isTextBased()) return channel;
      } catch {
        this.logger.warn(`Configured channel ${this.targetChannelId} not found, searching...`);
      }
    }

    // Fallback: find the first text channel in the first guild
    try {
      const guild = this.client.guilds.cache.first();
      if (guild) {
        const channels = guild.channels.cache.filter(c => c.isTextBased());
        if (channels.size > 0) {
          const channel = channels.first();
          this.logger.info(`Using channel: #${channel.name} in ${guild.name}`);
          return channel;
        }
      }
    } catch (error) {
      this.logger.error(`Failed to find channel: ${error.message}`);
    }

    return null;
  }

  /**
   * Send a daily summary of what was created and uploaded
   * @param {Object} data - { videos, countries, pipelineResults, errors }
   */
  async sendDailySummary(data) {
    if (!this.ready) {
      const ok = await this.initialize();
      if (!ok) return false;
    }

    const channel = await this._getTarget();
    if (!channel) {
      this.logger.error('No Discord target (DM or channel) available to send message');
      return false;
    }

    const videos = data.videos || [];
    const countries = data.countries || [];
    const errors = data.errors || [];

    const embed = new EmbedBuilder()
      .setColor(0x00FF00)
      .setTitle('🌅 Mr. WorldWideWebster — Daily Summary')
      .setDescription('Here\'s what I created today:')
      .setTimestamp()
      .setFooter({ text: 'Mr. WorldWideWebster Bot', iconURL: 'https://cdn3.emoji.gg/emojis/7031-globe.gif' });

    if (videos.length > 0) {
      const videoList = videos.map((v, i) =>
        `**${i + 1}.** ${v.title}\n   📺 ${v.url || 'Uploaded'}\n   🎭 Type: ${v.type || 'clip'}\n   🌍 ${v.country || 'Global'}`
      ).join('\n\n');

      embed.addFields({
        name: `🎬 Videos Created (${videos.length})`,
        value: videoList.substring(0, 1024),
        inline: false,
      });
    } else {
      embed.addFields({
        name: '🎬 Videos Created',
        value: 'No videos created today',
        inline: false,
      });
    }

    if (countries.length > 0) {
      embed.addFields({
        name: '🌍 Countries Covered',
        value: countries.join(', ').substring(0, 1024),
        inline: true,
      });
    }

    if (data.totalVideos !== undefined) {
      embed.addFields({
        name: '📊 Total Videos Posted',
        value: `${data.totalVideos}`,
        inline: true,
      });
    }

    if (errors.length > 0) {
      const errorText = errors.slice(0, 3).map(e => `❌ ${e}`).join('\n').substring(0, 1024);
      embed.addFields({
        name: '⚠️ Errors',
        value: errorText || 'None',
        inline: false,
      });
    }

    try {
      await channel.send({ embeds: [embed] });
      this.logger.success('Daily summary sent to Discord');
      return true;
    } catch (error) {
      this.logger.error(`Failed to send Discord message: ${error.message}`);
      return false;
    }
  }

  /**
   * Send a weekly performance report
   * @param {Object} data - { weekRange, videos, countries, stats }
   */
  async sendWeeklyReport(data) {
    if (!this.ready) {
      const ok = await this.initialize();
      if (!ok) return false;
    }

    const channel = await this._getTarget();
    if (!channel) {
      this.logger.error('No Discord target available');
      return false;
    }

    const embed = new EmbedBuilder()
      .setColor(0x9B59B6)
      .setTitle('📺 Mr. WorldWideWebster — Weekly Report')
      .setDescription(data.weekRange || 'This week\'s performance')
      .setTimestamp()
      .setFooter({ text: 'Mr. WorldWideWebster Bot' });

    if (data.stats) {
      const statsText = Object.entries(data.stats)
        .map(([key, value]) => `**${key}**: ${value}`)
        .join('\n');

      embed.addFields({
        name: '📊 Statistics',
        value: statsText.substring(0, 1024),
        inline: false,
      });
    }

    if (data.videos && data.videos.length > 0) {
      const videoText = data.videos.slice(0, 5).map((v, i) =>
        `**${i + 1}.** ${v.title} — ${v.views || 'N/A'} views`
      ).join('\n');

      embed.addFields({
        name: `🎥 Videos This Week (${data.videos.length})`,
        value: videoText.substring(0, 1024),
        inline: false,
      });
    }

    if (data.countries && data.countries.length > 0) {
      embed.addFields({
        name: '🌍 Countries Explored',
        value: data.countries.join(', ').substring(0, 1024),
        inline: false,
      });
    }

    try {
      await channel.send({ embeds: [embed] });
      this.logger.success('Weekly report sent to Discord');
      return true;
    } catch (error) {
      this.logger.error(`Failed to send weekly report: ${error.message}`);
      return false;
    }
  }

  /**
   * Send an error alert
   * @param {string} title - Short error title
   * @param {string} details - Detailed error message
   */
  async sendAlert(title, details) {
    if (!this.ready) {
      const ok = await this.initialize();
      if (!ok) return false;
    }

    const channel = await this._getTarget();
    if (!channel) return false;

    const embed = new EmbedBuilder()
      .setColor(0xFF0000)
      .setTitle(`🚨 ${title}`)
      .setDescription(details?.substring(0, 2000) || 'No details')
      .setTimestamp()
      .setFooter({ text: 'Mr. WorldWideWebster Alert' });

    try {
      await channel.send({ embeds: [embed] });
      this.logger.success('Alert sent to Discord');
      return true;
    } catch (error) {
      this.logger.error(`Failed to send alert: ${error.message}`);
      return false;
    }
  }

  /**
   * Send a simple status message
   */
  async sendMessage(text) {
    if (!this.ready) {
      const ok = await this.initialize();
      if (!ok) return false;
    }

    const channel = await this._getTarget();
    if (!channel) return false;

    try {
      await channel.send(text);
      return true;
    } catch (error) {
      this.logger.error(`Failed to send message: ${error.message}`);
      return false;
    }
  }

  /**
   * Clean up the bot connection
   */
  async destroy() {
    if (this.client) {
      try {
        await this.client.destroy();
        this.ready = false;
      } catch {
        // Ignore cleanup errors
      }
    }
  }
}

// ─── CLI Entry Point ──────────────────────────────────────────────────────

async function main() {
  const args = process.argv.slice(2);

  if (args.includes('--test')) {
    const bridge = new DiscordBridge();
    const ok = await bridge.initialize();
    if (ok) {
      await bridge.sendMessage('✅ **Mr. WorldWideWebster Discord Bot is online!**\nI\'ll send daily summaries and alerts here.');
      console.log('✅ Test message sent! Check your Discord server.');
      await bridge.destroy();
      process.exit(0);
    } else {
      console.error('❌ Discord bot failed to connect. Check your DISCORD_BOT_TOKEN.');
      process.exit(1);
    }
  }

  if (args.includes('--daily')) {
    const dataIndex = args.indexOf('--daily') + 1;
    const data = dataIndex < args.length ? JSON.parse(args[dataIndex]) : {};
    const bridge = new DiscordBridge();
    await bridge.initialize();
    await bridge.sendDailySummary(data);
    await bridge.destroy();
    process.exit(0);
  }

  if (args.includes('--weekly')) {
    const dataIndex = args.indexOf('--weekly') + 1;
    const data = dataIndex < args.length ? JSON.parse(args[dataIndex]) : {};
    const bridge = new DiscordBridge();
    await bridge.initialize();
    await bridge.sendWeeklyReport(data);
    await bridge.destroy();
    process.exit(0);
  }

  if (args.includes('--alert')) {
    const msgIndex = args.indexOf('--alert') + 1;
    const message = msgIndex < args.length ? args[msgIndex] : 'Unknown alert';
    const bridge = new DiscordBridge();
    await bridge.initialize();
    await bridge.sendAlert('Pipeline Alert', message);
    await bridge.destroy();
    process.exit(0);
  }

  console.log(`
Usage:
  node discord/discord-bridge.js --test                   # Test connection
  node discord/discord-bridge.js --daily '{"videos":[]}'  # Send daily summary
  node discord/discord-bridge.js --weekly '{"stats":{}}'  # Send weekly report
  node discord/discord-bridge.js --alert 'Error message'  # Send error alert
  `);
}

if (require.main === module) {
  main().catch(error => {
    console.error('Fatal:', error.message);
    process.exit(1);
  });
}

module.exports = { DiscordBridge };