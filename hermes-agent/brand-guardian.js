/**
 * Mr. WorldWideWebster — Brand Guardian
 *
 * Validates proposed content strategy changes against brand guidelines.
 * Ensures the Hermes agent never commits changes that violate the channel's
 * identity, ethical bounds, or visual style.
 *
 * Used by the midnight self-improvement review to gate code changes
 * before they are applied.
 */
const fs = require('fs');
const path = require('path');
const { Logger } = require('../core/logger');

class BrandGuardian {
  constructor() {
    this.logger = new Logger('BrandGuardian');
    this.guidelines = null;
    this._loadGuidelines();
  }

  _loadGuidelines() {
    try {
      const guidelinesPath = path.join(__dirname, '..', 'config', 'brand-guidelines.json');
      if (fs.existsSync(guidelinesPath)) {
        this.guidelines = JSON.parse(fs.readFileSync(guidelinesPath, 'utf8'));
        this.logger.info(`Loaded brand guidelines (${Object.keys(this.guidelines).length} sections)`);
      } else {
        this.logger.warn('No brand-guidelines.json found — using permissive defaults');
        this.guidelines = this._getDefaultGuidelines();
      }
    } catch (error) {
      this.logger.error(`Failed to load brand guidelines: ${error.message}`);
      this.guidelines = this._getDefaultGuidelines();
    }
  }

  _getDefaultGuidelines() {
    return {
      allowedContentTypes: ['clip', 'voiceover', 'explain', 'ai_create', 'landscape'],
      countryDiversity: { minNewCountriesPerWeek: 3, maxSameCountryPerWeek: 1, prioritizeCountries: [] },
      titleRules: { maxLength: 60, requireCuriosityGap: true, forbiddenPatterns: [], preferredFormulas: [] },
      ethicalBounds: { noMisinformation: true, noHateContent: true, noPoliticalDivision: true, transformOriginalContent: true },
      voice: { tone: 'curious, global, educational', forbiddenTopics: [] },
    };
  }

  /**
   * Validate a proposed code change against brand guidelines
   * @param {Object} proposal - { filePath, description, changeType, diff }
   * @returns {Object} { valid: boolean, violations: string[], warnings: string[] }
   */
  validateCodeChange(proposal) {
    const violations = [];
    const warnings = [];
    const allowedDirs = ['core', 'sourcing', 'config', 'hermes-agent', 'memory'];

    // Check file path is in allowed directories
    const normalizedPath = proposal.filePath.replace(/\\/g, '/');
    const isAllowed = allowedDirs.some(dir => normalizedPath.includes(`/${dir}/`) || normalizedPath.startsWith(`${dir}/`));
    if (!isAllowed) {
      violations.push(`File path "${proposal.filePath}" is outside allowed directories: ${allowedDirs.join(', ')}`);
    }

    // Check description mentions a valid content type
    if (proposal.description) {
      const mentionedTypes = this.guidelines.allowedContentTypes.filter(t =>
        proposal.description.toLowerCase().includes(t.toLowerCase())
      );
      if (mentionedTypes.length === 0 && proposal.changeType !== 'config' && proposal.changeType !== 'memory') {
        warnings.push('Change description does not mention any of the allowed content types');
      }
    }

    // Check for forbidden patterns in titles if this is a title formula change
    if (proposal.changeType === 'title_formula' || proposal.changeType === 'title') {
      for (const pattern of this.guidelines.titleRules.forbiddenPatterns) {
        if (proposal.description && proposal.description.toLowerCase().includes(pattern.toLowerCase())) {
          violations.push(`Title contains forbidden pattern: "${pattern}"`);
        }
      }
    }

    // Check ethical bounds
    if (proposal.changeType === 'sourcing') {
      for (const [key, value] of Object.entries(this.guidelines.ethicalBounds)) {
        if (value === true && proposal.description && !proposal.description.toLowerCase().includes('ethical')) {
          warnings.push(`Ensure change respects brand ethical bound: ${key}`);
        }
      }
    }

    this.logger.info(`Code change validation: ${violations.length > 0 ? 'REJECTED' : 'APPROVED'} (${violations.length} violations, ${warnings.length} warnings)`);
    return { valid: violations.length === 0, violations, warnings };
  }

  /**
   * Validate a proposed video title against brand guidelines
   * @param {string} title
   * @returns {Object} { valid, violations, suggestions }
   */
  validateTitle(title) {
    const violations = [];
    const suggestions = [];

    if (!title || title.trim().length === 0) {
      return { valid: false, violations: ['Title is empty'], suggestions: ['Use one of the preferred title formulas'] };
    }

    // Max length check
    if (title.length > this.guidelines.titleRules.maxLength) {
      violations.push(`Title exceeds ${this.guidelines.titleRules.maxLength} characters (${title.length})`);
      suggestions.push(`Shorten by ${title.length - this.guidelines.titleRules.maxLength} characters`);
    }

    // Require curiosity gap
    if (this.guidelines.titleRules.requireCuriosityGap) {
      const curiosityIndicators = ['?', '...', 'why', 'how', 'truth', 'real', 'secret', 'best', 'worst'];
      const hasCuriosity = curiosityIndicators.some(i => title.toLowerCase().includes(i));
      if (!hasCuriosity) {
        suggestions.push('Add a curiosity gap (use "?" or "..." or "why/how")');
      }
    }

    // Check forbidden patterns
    for (const pattern of this.guidelines.titleRules.forbiddenPatterns) {
      if (title.toLowerCase().includes(pattern.toLowerCase())) {
        violations.push(`Title contains forbidden pattern: "${pattern}"`);
      }
    }

    // Recommend emoji
    if (this.guidelines.titleRules.requireEmoji && !/[🎵🌍🔥🇨🇳🇯🇵🇰🇷🇧🇷🇳🇬🇩🇪🇫🇷🇮🇹🇪🇸🇹🇭🇻🇳🇮🇳]/.test(title)) {
      suggestions.push('Add a relevant emoji flag or visual symbol');
    }

    this.logger.info(`Title validation: ${violations.length > 0 ? 'REJECTED' : 'PASSED'} (${title.substring(0, 50)})`);
    return { valid: violations.length === 0, violations, suggestions };
  }

  /**
   * Validate a content strategy proposal
   * @param {Object} proposal - { countries, contentTypes, schedule }
   * @returns {Object} { valid, violations, warnings }
   */
  validateStrategy(proposal) {
    const violations = [];
    const warnings = [];

    // Country diversity
    if (proposal.countries && proposal.countries.length > 0) {
      if (proposal.countries.length < this.guidelines.countryDiversity.minNewCountriesPerWeek) {
        warnings.push(`Only ${proposal.countries.length} countries — brand recommends at least ${this.guidelines.countryDiversity.minNewCountriesPerWeek} new countries per week`);
      }

      // Check for duplicates
      const counts = {};
      for (const c of proposal.countries) {
        counts[c] = (counts[c] || 0) + 1;
        if (counts[c] > this.guidelines.countryDiversity.maxSameCountryPerWeek) {
          violations.push(`Country "${c}" appears more than ${this.guidelines.countryDiversity.maxSameCountryPerWeek} time(s) this week`);
        }
      }
    }

    // Content types
    if (proposal.contentTypes && proposal.contentTypes.length > 0) {
      const invalidTypes = proposal.contentTypes.filter(t => !this.guidelines.allowedContentTypes.includes(t));
      if (invalidTypes.length > 0) {
        violations.push(`Invalid content types: ${invalidTypes.join(', ')}. Allowed: ${this.guidelines.allowedContentTypes.join(', ')}`);
      }
    }

    // Schedule
    if (proposal.schedule) {
      if (proposal.schedule.maxPerDay > this.guidelines.postingSchedule.maxPerDay) {
        violations.push(`Schedule maxPerDay (${proposal.schedule.maxPerDay}) exceeds brand max (${this.guidelines.postingSchedule.maxPerDay})`);
      }
    }

    this.logger.info(`Strategy validation: ${violations.length > 0 ? 'REJECTED' : 'APPROVED'}`);
    return { valid: violations.length === 0, violations, warnings };
  }

  /**
   * Reload guidelines from disk (in case they were updated)
   */
  reload() {
    this._loadGuidelines();
  }

  /**
   * Get the current guidelines (for agent context)
   */
  getGuidelines() {
    return JSON.parse(JSON.stringify(this.guidelines));
  }
}

module.exports = { BrandGuardian };