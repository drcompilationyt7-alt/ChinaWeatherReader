/**
 * Mr. WorldWideWebster — Official Hermes CLI Wrapper
 *
 * Primary agent system. Uses the official `hermes` CLI from Nous Research.
 * Falls back to the built-in Hermes JS agent if CLI is not available.
 *
 * The official Hermes is installed in GitHub Actions via:
 *   curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
 *
 * It provides a permanent CLI tool that can:
 * - Browse the web autonomously
 * - Write and execute code
 * - Use tools and APIs
 * - Remember context across sessions
 * - Run as a system service
 */
const { execSync, exec } = require('child_process');
const { promisify } = require('util');
const path = require('path');
const fs = require('fs');
const { Logger } = require('../core/logger');

class HermesCLIWrapper {
  constructor() {
    this.logger = new Logger('HermesCLI');
    this.cliAvailable = false;
    this._checkCLI();
  }

  /**
   * Check if the official hermes CLI is installed
   */
  _checkCLI() {
    try {
      const version = execSync('hermes --version 2>/dev/null', { timeout: 5000 }).toString().trim();
      this.cliAvailable = true;
      this.logger.info(`Official Hermes CLI detected: ${version}`);
    } catch {
      this.cliAvailable = false;
      this.logger.warn('Official Hermes CLI not found — will fall back to built-in Hermes JS agent');
      this.logger.warn('Install via: curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash');
    }
  }

  isAvailable() {
    return this.cliAvailable;
  }

  /**
   * Run a task using the official Hermes CLI
   * @param {string} task - What the agent should do
   * @param {Object} options - { maxSteps, verbose, model }
   * @returns {Promise<Object>}
   */
  async run(task, options = {}) {
    if (!this.cliAvailable) {
      throw new Error('Hermes CLI not available. Install with the curl command.');
    }

    const maxSteps = options.maxSteps || 15;
    const verbose = options.verbose ?? true;

    this.logger.header('OFFICIAL HERMES CLI AGENT');
    this.logger.info(`Task: ${task.substring(0, 100)}`);

    try {
      // Create a temporary task file for Hermes to execute
      const taskFile = path.join(__dirname, '..', 'output', 'temp', `hermes_task_${Date.now()}.md`);
      const taskDir = path.dirname(taskFile);
      if (!fs.existsSync(taskDir)) {
        fs.mkdirSync(taskDir, { recursive: true });
      }

      // Write the task with context about our channel
      const taskContent = `# Mr. WorldWideWebster - Autonomous Task

## Channel Identity
Mr. WorldWideWebster is a YouTube channel that shows people what's trending around the world.
Content types: Clip (viral moments), Voiceover (translated), Explain ("What is this...?"), AI Create (comparisons, news, original content)

## System Context
- You are running in a GitHub Actions environment (Ubuntu Linux)
- You have access to: yt-dlp, ffmpeg, python3, node.js, puppeteer (Chrome headless)
- Your AI model is configured via OPENROUTER_API_KEY
- The repo is at: /home/runner/work/mr-worldwidewebster/mr-worldwidewebster

## Task
${task}

## Instructions
1. Use your tools to accomplish this task step by step
2. Search the web for trending international content
3. Download videos, write scripts, or create content as needed
4. Report back what was accomplished
5. Save any important findings to the memory/ directory`;

      fs.writeFileSync(taskFile, taskContent);
      this.logger.info(`Task written to: ${taskFile}`);

      // Execute the task via Hermes CLI
      // hermes run reads a task file and executes it autonomously
      const cmd = `hermes run "${taskFile}" --max-steps ${maxSteps} 2>&1`;
      this.logger.info(`Executing: hermes run`);

      const output = execSync(cmd, {
        timeout: maxSteps * 60000, // ~1 min per step max
        maxBuffer: 10 * 1024 * 1024,
        env: {
          ...process.env,
          OPENROUTER_API_KEY: process.env.OPENROUTER_API_KEY,
          HERMES_MODEL: 'openrouter/owl-alpha',
        },
      }).toString();

      this.logger.success('Hermes CLI task completed');

      return {
        success: true,
        output: output.substring(0, 10000),
        fullOutput: output,
        steps: maxSteps,
        agent: 'hermes-cli',
      };
    } catch (error) {
      this.logger.error(`Hermes CLI failed: ${error.message}`);

      // Check if we got partial output before the error
      if (error.stdout) {
        this.logger.info('Partial output before error captured');
        return {
          success: true,
          output: error.stdout.toString().substring(0, 10000),
          partial: true,
          steps: maxSteps,
          agent: 'hermes-cli',
        };
      }

      // Return fallback instruction
      return {
        success: false,
        error: error.message,
        output: `Hermes CLI failed: ${error.message}. Try the built-in Hermes JS agent as fallback.`,
        agent: 'hermes-cli',
        steps: 0,
      };
    }
  }

  /**
   * Call Hermes CLI directly with a prompt
   */
  async chat(prompt) {
    if (!this.cliAvailable) return null;

    try {
      const output = execSync(`hermes chat "${prompt.replace(/"/g, '\\"')}" 2>&1`, {
        timeout: 60000,
        maxBuffer: 5 * 1024 * 1024,
      }).toString();
      return output;
    } catch {
      return null;
    }
  }

  /**
   * List available tools/models from Hermes CLI
   */
  async getInfo() {
    if (!this.cliAvailable) return { available: false };

    try {
      const config = execSync('hermes config list 2>&1', { timeout: 5000 }).toString();
      const tools = execSync('hermes tools 2>&1', { timeout: 5000 }).toString();
      return {
        available: true,
        config: config.trim(),
        tools: tools.trim(),
      };
    } catch {
      return { available: true, error: 'Could not list config' };
    }
  }
}

module.exports = { HermesCLIWrapper };