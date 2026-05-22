/**
 * Mr. WorldWideWebster — Code Evolver
 *
 * Gives the Hermes agent the ability to:
 * 1. Read source code files with context
 * 2. Propose specific code edits that improve content strategy
 * 3. Validate edits against brand guidelines via BrandGuardian
 * 4. Apply edits to source files
 * 5. Generate a test video using the new strategy
 * 6. Push everything to git
 *
 * This is the engine behind the "video-before-commit" self-improvement loop.
 */
const fs = require('fs');
const path = require('path');
const { execSync, exec } = require('child_process');
const { promisify } = require('util');
const { Logger } = require('../core/logger');
const { BrandGuardian } = require('./brand-guardian');

const execAsync = promisify(exec);

class CodeEvolver {
  constructor(options = {}) {
    this.logger = new Logger('CodeEvolver');
    this.brandGuardian = new BrandGuardian();
    this.repoRoot = options.repoRoot || path.resolve(__dirname, '..');
    this.allowedDirs = ['core', 'sourcing', 'config', 'hermes-agent', 'memory', 'clipping', 'explainer', 'voiceover', 'ai-creator', 'landscape', 'providers'];
    this.changeLog = [];
  }

  /**
   * Read source code with context (file overview, structure, relevant sections)
   * @param {string} filePath - relative to repo root
   * @param {Object} options - { maxLines, contextLines }
   * @returns {Object} { filePath, content, lines, structure }
   */
  readSource(filePath, options = {}) {
    const absolutePath = path.join(this.repoRoot, filePath);
    const maxLines = options.maxLines || 200;

    if (!fs.existsSync(absolutePath)) {
      return { error: `File not found: ${filePath}` };
    }

    const content = fs.readFileSync(absolutePath, 'utf8');
    const lines = content.split('\n');
    const totalLines = lines.length;

    // Extract code structure (function/class definitions)
    const structure = [];
    const classRegex = /^(class|function)\s+(\w+)/m;
    const funcRegex = /^\s*(async\s+)?(\w+)\s*\(/gm;
    const defRegex = /^(export\s+)?(class|function|const|let|var)\s+(\w+)/gm;

    let match;
    while ((match = defRegex.exec(content)) !== null) {
      structure.push({
        type: match[2],
        name: match[3],
        line: content.substring(0, match.index).split('\n').length,
      });
    }

    // Return with line limit
    const truncated = totalLines > maxLines;
    const displayContent = truncated ? lines.slice(0, maxLines).join('\n') + `\n\n... (${totalLines - maxLines} more lines omitted)` : content;

    this.logger.info(`Read source: ${filePath} (${totalLines} lines${truncated ? `, showing ${maxLines}` : ''})`);
    return {
      filePath,
      content,
      lines: totalLines,
      truncated,
      structure,
      displayContent,
      lastModified: fs.statSync(absolutePath).mtime,
    };
  }

  /**
   * Propose a code edit - validates against brand rules, returns a diff preview
   * @param {Object} proposal - { filePath, description, changeType, searchPattern, replacement }
   * @returns {Object} { valid, violations, warnings, diff, filePath }
   */
  proposeEdit(proposal) {
    const { filePath, description, changeType, searchPattern, replacement } = proposal;

    // Validate file path is allowed
    const normalized = filePath.replace(/\\/g, '/');
    const isAllowed = this.allowedDirs.some(dir => normalized.startsWith(dir) || normalized.includes(`/${dir}/`));
    if (!isAllowed) {
      return {
        valid: false,
        violations: [`File "${filePath}" is not in an editable directory. Allowed: ${this.allowedDirs.join(', ')}`],
        warnings: [],
        diff: null,
        filePath,
      };
    }

    // Validate against brand guidelines
    const brandResult = this.brandGuardian.validateCodeChange({
      filePath,
      description: description || changeType,
      changeType,
    });

    if (!brandResult.valid) {
      return { valid: false, violations: brandResult.violations, warnings: brandResult.warnings, diff: null, filePath };
    }

    // Read file and generate diff preview
    const absolutePath = path.join(this.repoRoot, filePath);
    if (!fs.existsSync(absolutePath)) {
      return { valid: false, violations: [`File not found: ${filePath}`], warnings: [], diff: null, filePath };
    }

    const currentContent = fs.readFileSync(absolutePath, 'utf8');

    // Check if the search pattern exists in the file
    if (searchPattern) {
      if (!currentContent.includes(searchPattern)) {
        return {
          valid: false,
          violations: [`Search pattern not found in ${filePath}:\n  Pattern: "${searchPattern.substring(0, 100)}..."`],
          warnings: [`File content does not contain the expected pattern. This might mean the file has been modified since it was read.`],
          diff: null,
          filePath,
        };
      }
    }

    // Generate preview of changes
    let modifiedContent;
    if (searchPattern) {
      modifiedContent = currentContent.replace(searchPattern, replacement);
    } else {
      modifiedContent = replacement; // Full file replacement
    }

    // Simple diff preview (shows first few changed lines)
    const currentLines = currentContent.split('\n');
    const newLines = modifiedContent.split('\n');
    const diffPreview = [];
    const maxDiffLines = 20;
    let changes = 0;

    for (let i = 0; i < Math.max(currentLines.length, newLines.length); i++) {
      if (currentLines[i] !== newLines[i]) {
        if (changes < maxDiffLines) {
          if (i < currentLines.length) diffPreview.push(`- ${currentLines[i]}`);
          if (i < newLines.length) diffPreview.push(`+ ${newLines[i]}`);
        }
        changes++;
      }
    }

    if (changes > maxDiffLines) {
      diffPreview.push(`... (${changes - maxDiffLines} more changes)`);
    }

    this.logger.info(`Edit proposed: ${filePath} (${changes} line changes, ${brandResult.warnings.length} warnings)`);
    return {
      valid: true,
      violations: [],
      warnings: brandResult.warnings,
      diff: diffPreview.join('\n'),
      changes,
      filePath,
      modifiedContent, // Not applied yet — for preview only
    };
  }

  /**
   * Apply a validated code edit to disk
   * @param {Object} proposal - same as proposeEdit
   * @returns {Object} { success, error, filePath, changes }
   */
  applyEdit(proposal) {
    const validated = this.proposeEdit(proposal);
    if (!validated.valid) {
      return { success: false, error: `Validation failed: ${validated.violations.join(', ')}`, filePath: proposal.filePath };
    }

    try {
      const absolutePath = path.join(this.repoRoot, proposal.filePath);
      let currentContent = fs.readFileSync(absolutePath, 'utf8');
      let modifiedContent;

      if (proposal.searchPattern) {
        modifiedContent = currentContent.replace(proposal.searchPattern, proposal.replacement);
      } else {
        modifiedContent = proposal.replacement;
      }

      // Write backup first
      const backupPath = absolutePath + '.bak';
      fs.writeFileSync(backupPath, currentContent);

      // Write new content
      fs.writeFileSync(absolutePath, modifiedContent, 'utf8');

      // Log the change
      const change = {
        timestamp: new Date().toISOString(),
        filePath: proposal.filePath,
        description: proposal.description || proposal.changeType,
        changeType: proposal.changeType,
        changes: validated.changes,
      };
      this.changeLog.push(change);

      // Remove backup
      fs.unlinkSync(backupPath);

      this.logger.success(`Edit applied: ${proposal.filePath} (${validated.changes} changes)`);
      return {
        success: true,
        filePath: proposal.filePath,
        changes: validated.changes,
        description: proposal.description,
        changeType: proposal.changeType,
      };
    } catch (error) {
      this.logger.error(`Failed to apply edit to ${proposal.filePath}: ${error.message}`);
      return { success: false, error: error.message, filePath: proposal.filePath };
    }
  }

  /**
   * Rollback the last N changes
   * @param {number} count - number of changes to roll back
   * @returns {Object} { success, rolledBack, errors }
   */
  rollback(count = 1) {
    const rolledBack = [];
    const errors = [];

    for (let i = 0; i < Math.min(count, this.changeLog.length); i++) {
      const change = this.changeLog.pop();
      try {
        // We can't guarantee a clean rollback without git, so warn
        this.logger.warn(`Manual rollback needed for: ${change.filePath} (change #${this.changeLog.length + 1})`);
        errors.push(`Change to ${change.filePath} requires manual rollback via git checkout`);
      } catch (error) {
        errors.push(error.message);
      }
      rolledBack.push(change);
    }

    return { success: errors.length === 0, rolledBack, errors };
  }

  /**
   * Generate a test video using the new strategy to prove it works
   * @param {Object} options - { topic, contentType, country, aiService, config }
   * @returns {Object} { success, videoPath, title, url }
   */
  async createAndPostVideo(options) {
    const { topic, contentType, country, aiService, config } = options;

    this.logger.header('🎬 Creating test video with new strategy');
    this.logger.info(`Topic: ${topic}, Type: ${contentType}, Country: ${country || 'Global'}`);

    try {
      // Validate title against brand guidelines
      const titleValidation = this.brandGuardian.validateTitle(topic);
      if (!titleValidation.valid) {
        this.logger.warn(`Title has brand violations: ${titleValidation.violations.join(', ')}`);
        this.logger.info('Proceeding with warnings — agent will see these');
      }

      // Check if we have the YouTube bridge available
      let youTubeBridge = null;
      try {
        const { YouTubeBridge } = require('../youtube-automation/youtube-bridge');
        youTubeBridge = new YouTubeBridge();
        await youTubeBridge.initialize();
      } catch {
        this.logger.warn('YouTube bridge not available — video will be saved locally');
      }

      // Create the video using the appropriate pipeline
      const { ContentRouter } = require('../core/content-router');
      const router = new ContentRouter(aiService, config);

      const contentPayload = {
        title: topic,
        platform: 'self-improvement',
        description: `Auto-generated by midnight self-improvement using new strategy.\nContent type: ${contentType}\nCountry: ${country || 'Global'}`,
        duration: 30,
        hasSpeech: false,
        isVisual: true,
        languageDetected: 'english',
      };

      const decisionPayload = {
        path: contentType === 'explain' ? 'explain' : 'ai_create',
        confidence: 85,
        reasoning: `Midnight self-improvement: testing ${contentType} strategy`,
        suggestedTitle: topic,
        explainThing: contentType === 'explain' ? `trending content from ${country || 'around the world'}` : undefined,
        explainCategory: contentType === 'explain' ? 'trend' : undefined,
        contentType: contentType === 'explain' ? 'explainer' : contentType,
      };

      const result = await router.route(contentPayload, decisionPayload);

      // If YouTube bridge is available, upload
      let uploadResult = null;
      if (youTubeBridge && youTubeBridge.isAuthenticated() && result.output && result.output.videoFile) {
        this.logger.info('Uploading test video to YouTube...');
        try {
          uploadResult = await youTubeBridge.uploadVideo({
            videoPath: result.output.videoFile,
            title: topic,
            description: `${topic}\n\n🌍 Midnight self-improvement test video\nDemonstrates new content strategy\n\n#worldwidewebster #${contentType} #${(country || 'global').toLowerCase().replace(/\s+/g, '')}`,
            tags: ['mr worldwidewebster', 'shorts', contentType, (country || 'global').toLowerCase()],
          });
          this.logger.success(`✅ Test video uploaded: ${uploadResult.url}`);
        } catch (uploadError) {
          this.logger.warn(`Upload failed: ${uploadError.message}`);
        }
      }

      const output = {
        success: result.status === 'completed',
        videoPath: result.output?.videoFile || null,
        title: topic,
        url: uploadResult?.url || null,
        videoId: uploadResult?.videoId || null,
        pipelineResult: result.status,
        brandValidations: titleValidation,
      };

      // Save video metadata to memory for the commit
      this._saveVideoMetadata(output);

      this.logger.success(`Test video ${output.success ? 'CREATED' : 'FAILED'}: ${topic}`);
      return output;
    } catch (error) {
      this.logger.error(`Test video creation failed: ${error.message}`);
      return {
        success: false,
        error: error.message,
        videoPath: null,
        title: topic,
        url: null,
      };
    }
  }

  /**
   * Save video metadata so the commit captures it
   */
  _saveVideoMetadata(videoData) {
    try {
      const memoryPath = path.join(this.repoRoot, 'memory', 'self-improvement-videos.json');
      let history = [];
      if (fs.existsSync(memoryPath)) {
        history = JSON.parse(fs.readFileSync(memoryPath, 'utf8'));
      }
      history.push({
        ...videoData,
        createdAt: new Date().toISOString(),
        appliedChanges: [...this.changeLog],
      });
      // Keep last 50 entries
      if (history.length > 50) history = history.slice(-50);
      fs.writeFileSync(memoryPath, JSON.stringify(history, null, 2));
    } catch (error) {
      this.logger.warn(`Failed to save video metadata: ${error.message}`);
    }
  }

  /**
   * Commit all changes (code edits + video metadata) to git
   * @param {string} message - commit message
   * @returns {Object} { success, committed, message }
   */
  commitChanges(message) {
    const changes = [...this.changeLog];
    if (changes.length === 0) {
      this.logger.info('No changes to commit');
      return { success: true, committed: false, message: 'No changes to commit' };
    }

    try {
      // Stage all changes
      execSync('git add -A', { cwd: this.repoRoot, timeout: 10000 });
      this.logger.info('Staged all changes');

      // Check if there's anything to commit
      const status = execSync('git status --porcelain', { cwd: this.repoRoot, timeout: 5000 }).toString().trim();
      if (!status) {
        this.logger.info('No changes to commit after staging');
        return { success: true, committed: false, message: 'No changes detected' };
      }

      // Build summary of changes for the commit message
      const changeSummary = changes
        .map(c => `  - ${c.filePath}: ${c.description} (${c.changes} changes)`)
        .join('\n');

      const fullMessage = `${message}\n\nChanges made:\n${changeSummary}\n\nSelf-improvement cycle complete.`;

      // Commit
      execSync(`git commit -m "${fullMessage.replace(/"/g, '\\"')}"`, { cwd: this.repoRoot, timeout: 15000 });
      this.logger.success(`Committed ${changes.length} changes`);

      // Push
      try {
        execSync('git push', { cwd: this.repoRoot, timeout: 30000 });
        this.logger.success('Changes pushed to remote');
      } catch (pushError) {
        // Push might fail in GitHub Actions with token issues — that's OK
        this.logger.warn(`Push failed (non-fatal): ${pushError.message}`);
        this.logger.warn('Changes are committed locally and will be pushed by the workflow');
      }

      return {
        success: true,
        committed: true,
        message: fullMessage,
        changes: changes.length,
        changeSummary,
      };
    } catch (error) {
      this.logger.error(`Commit failed: ${error.message}`);
      return { success: false, committed: false, error: error.message };
    }
  }

  /**
   * Get a summary of all changes made this session
   */
  getChangeLog() {
    return [...this.changeLog];
  }

  /**
   * Clear the change log (after successful commit)
   */
  clearChangeLog() {
    this.changeLog = [];
  }
}

module.exports = { CodeEvolver };