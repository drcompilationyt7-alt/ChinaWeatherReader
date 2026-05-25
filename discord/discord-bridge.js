#!/usr/bin/env node

/**
 * Mr. WorldWideWebster — Discord Notification Bridge
 * Shows total views and top 3 most viewed videos.
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

  async initialize() {
    if (!this.token) { this.logger.warn('DISCORD_BOT_TOKEN not set'); return false; }
    this.logger.info('Initializing Discord bot...');
    try {
      this.client = new Client({ intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMessages, GatewayIntentBits.MessageContent, GatewayIntentBits.DirectMessages] });
      this.client.on('error', () => {});
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('Discord login timed out')), 15000);
        this.client.once('ready', () => { clearTimeout(timeout); this.logger.success(`Discord bot connected: ${this.client.user?.tag || 'Unknown'}`); this.ready = true; resolve(); });
        this.client.once('error', (e) => { clearTimeout(timeout); reject(e); });
        this.client.login(this.token).catch(reject);
      });
      return true;
    } catch (error) { this.logger.error(`Discord bot failed: ${error.message}`); this.ready = false; return false; }
  }

  async _getTarget() {
    if (!this.client || !this.ready) return null;
    if (this.targetUserId) { try { const user = await this.client.users.fetch(this.targetUserId); if (user) { const dm = await user.createDM(); this.logger.info(`Sending to DM: ${user.tag}`); return dm; } } catch {} }
    if (this.targetChannelId) { try { const channel = await this.client.channels.fetch(this.targetChannelId); if (channel && channel.isTextBased()) return channel; } catch {} }
    try {
      const guild = this.client.guilds.cache.first();
      if (guild) { const channels = guild.channels.cache.filter(c => c.isTextBased()); if (channels.size > 0) return channels.first(); }
    } catch {}
    return null;
  }

  async sendDailySummary(data) {
    if (!this.ready) { const ok = await this.initialize(); if (!ok) return false; }
    const channel = await this._getTarget();
    if (!channel) { this.logger.error('No Discord target'); return false; }

    const videos = data.videos || [];
    const countries = data.countries || [];
    const errors = data.errors || [];
    const totalViews = data.totalViews || 0;

    const embed = new EmbedBuilder()
      .setColor(0x00FF00)
      .setTitle('🌅 Mr. WorldWideWebster — Daily Summary')
      .setDescription('Here\'s what I created today:')
      .setTimestamp()
      .setFooter({ text: 'Mr. WorldWideWebster Bot', iconURL: 'https://cdn3.emoji.gg/emojis/7031-globe.gif' });

    if (videos.length > 0) {
      const videoList = videos.map((v, i) =>
        `**${i + 1}.** ${v.title}\n   📺 ${v.url || 'Uploaded'}\n   👁️ ${v.views || 0} views\n   🌍 ${v.country || 'Global'}`
      ).join('\n\n');
      embed.addFields({ name: `🎬 Videos Created (${videos.length})`, value: videoList.substring(0, 1024), inline: false });
    } else {
      embed.addFields({ name: '🎬 Videos Created', value: 'No videos created today', inline: false });
    }

    if (totalViews > 0) {
      embed.addFields({ name: '👁️ Total Views Today', value: `${totalViews.toLocaleString()}`, inline: true });
    }

    if (videos.length > 0) {
      const sorted = [...videos].sort((a, b) => (b.views || 0) - (a.views || 0)).slice(0, 3);
      const topList = sorted.map((v, i) => `**#${i + 1}** ${v.title} — 👁️ ${v.views || 0} views`).join('\n');
      embed.addFields({ name: '🏆 Top 3 Most Viewed', value: topList, inline: false });
    }

    if (countries.length > 0) {
      embed.addFields({ name: '🌍 Countries Covered', value: countries.join(', ').substring(0, 1024), inline: true });
    }
    if (data.totalVideos !== undefined) {
      embed.addFields({ name: '📊 Total Videos Posted', value: `${data.totalVideos}`, inline: true });
    }
    if (errors.length > 0) {
      embed.addFields({ name: '⚠️ Errors', value: errors.slice(0, 3).map(e => `❌ ${e}`).join('\n').substring(0, 1024), inline: false });
    }

    try { await channel.send({ embeds: [embed] }); this.logger.success('Daily summary sent to Discord'); return true; }
    catch (error) { this.logger.error(`Failed to send: ${error.message}`); return false; }
  }

  async destroy() { if (this.client) { try { await this.client.destroy(); this.ready = false; } catch {} } }
}

async function main() {
  const args = process.argv.slice(2);
  if (args.includes('--test')) {
    const bridge = new DiscordBridge(); const ok = await bridge.initialize();
    if (ok) { await bridge.sendMessage('✅ Mr. WorldWideWebster Discord Bot is online!'); console.log('✅ Test sent!'); await bridge.destroy(); process.exit(0); }
    else { console.error('❌ Failed'); process.exit(1); }
  }
  if (args.includes('--daily')) {
    const idx = args.indexOf('--daily') + 1;
    const data = idx < args.length ? JSON.parse(args[idx]) : {};
    const bridge = new DiscordBridge(); await bridge.initialize(); await bridge.sendDailySummary(data); await bridge.destroy(); process.exit(0);
  }
  console.log('Usage: node discord/discord-bridge.js --test | --daily');
}
if (require.main === module) { main().catch(e => { console.error(e.message); process.exit(1); }); }
module.exports = { DiscordBridge };
