/**
 * Mr. WorldWideWebster — Official Hermes CLI Wrapper
 *
 * Uses `hermes chat -z "prompt"` (Hermes CLI v0.14.0+)
 * The `run` command was removed in newer versions.
 *
 * Install: curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('../core/logger');

class HermesCLIWrapper {
  constructor() {
    this.logger = new Logger('HermesCLI');
    this.cliAvailable = false;
    this._checkCLI();
  }

  _checkCLI() {
    try {
      const version = execSync('hermes --version 2>/dev/null', { timeout: 5000 }).toString().trim();
      this.cliAvailable = true;
      this.logger.info(`Official Hermes CLI detected: ${version}`);
    } catch {
      this.cliAvailable = false;
      this.logger.warn('Official Hermes CLI not found — will fall back to built-in Hermes JS agent');
    }
  }

  isAvailable() {
    return this.cliAvailable;
  }

  /**
   * Run a task using the official Hermes CLI via `hermes chat -z`
   * Hermes v0.14.0 removed the `run` command; use `chat -z` for one-shot prompts.
   */
  async run(task, options = {}) {
    if (!this.cliAvailable) {
      throw new Error('Hermes CLI not available.');
    }

    this.logger.header('OFFICIAL HERMES CLI AGENT');
    this.logger.info(`Task: ${task.substring(0, 100)}`);

    try {
      // Write task to temp file for reference
      const taskFile = path.join(__dirname, '..', 'output', 'temp', `hermes_task_${Date.now()}.md`);
      const taskDir = path.dirname(taskFile);
      if (!fs.existsSync(taskDir)) {
        fs.mkdirSync(taskDir, { recursive: true });
      }

      const taskContent = `# Mr. WorldWideWebster - Autonomous Task

## Channel Identity
Mr. WorldWideWebster is a YouTube channel that shows people what's trending around the world.
Content types: Clip (viral moments), Voiceover (translated), Explain ("What is this...?"), AI Create (comparisons, news, original content)

## System Context
- You are running in a GitHub Actions environment (Ubuntu Linux)
- You have access to: yt-dlp, ffmpeg, python3, node.js
- Your AI model is configured via OPENROUTER_API_KEY
- The repo is at: /home/runner/work/mr-worldwidewebster/mr-worldwidewebster

## Task
${task}

## Instructions
1. Use your tools to accomplish this task step by step
2. Search the web for trending international content
3. Report back what was accomplished as JSON
4. Save any important findings to the memory/ directory`;

      fs.writeFileSync(taskFile, taskContent);
      this.logger.info(`Task written to: ${taskFile}`);

      // Hermes v0.14.0 uses `hermes chat -z "prompt"` (no `run` command)
      // Use --yolo to skip confirmation prompts
      const escapedTask = task.replace(/"/g, '\\"').replace(/\$/g, '\\$').replace(/`/g, '\\`');
      const cmd = `hermes chat -z "${escapedTask}" --yolo 2>&1`;
      this.logger.info(`Executing: hermes chat -z "..."`);

      const output = execSync(cmd, {
        timeout: 300000, // 5 minutes
        maxBuffer: 10 * 1024 * 1024,
        env: {
          ...process.env,
          OPENROUTER_API_KEY: process.env.OPENROUTER_API_KEY || '',
        },
      }).toString();

      this.logger.success('Hermes CLI task completed');

      return {
        success: true,
        output: output.substring(0, 10000),
        fullOutput: output,
        steps: 1,
        agent: 'hermes-cli',
      };
    } catch (error) {
      this.logger.error(`Hermes CLI failed: ${error.message}`);

      // Check if we got partial output before the error
      if (error.stdout) {
        const partialOut = error.stdout.toString();
        // Only treat as partial if it looks like real output (not just help/usage text)
        if (partialOut.length > 200 && !partialOut.includes('Usage:') && !partialOut.includes('Commands:') && !partialOut.includes('hermes: error:')) {
          this.logger.info('Partial output before error captured');
          return {
            success: true,
            output: partialOut.substring(0, 10000),
            partial: true,
            steps: 1,
            agent: 'hermes-cli',
          };
        }
      }

      // Signal that caller should fall back to JS agent
      this.logger.warn('Hermes CLI run failed — caller should fall back to built-in Hermes JS agent');
      return {
        success: false,
        error: error.message,
        output: '',
        agent: 'hermes-cli',
        steps: 0,
        shouldFallback: true,
      };
    }
  }

  async chat(prompt) {
    if (!this.cliAvailable) return null;
    try {
      const escaped = prompt.replace(/"/g, '\\"').replace(/\$/g, '\\$');
      const output = execSync(`hermes chat -z "${escaped}" --yolo 2>&1`, {
        timeout: 120000,
        maxBuffer: 5 * 1024 * 1024,
      }).toString();
      return output;
    } catch {
      return null;
    }
  }

  async getInfo() {
    if (!this.cliAvailable) return { available: false };
    try {
      const version = execSync('hermes --version 2>/dev/null', { timeout: 5000 }).toString();
      return { available: true, version: version.trim() };
    } catch {
      return { available: true, error: 'Could not get version' };
    }
  }
}

module.exports = { HermesCLIWrapper };