const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('../core/logger');

class HermesCLIWrapper {
  constructor() {
    this.logger = new Logger('HermesCLI');
    this.cliAvailable = false;
    this.camofoxAvailable = null; // null = not checked yet
    this._checkCLI();
  }

  _checkCLI() {
    try {
      const version = execSync(
        'hermes --version',
        { timeout: 5000 }
      )
      .toString()
      .trim();

      this.cliAvailable = true;

      this.logger.info(
        `Official Hermes CLI detected: ${version}`
      );

    } catch {

      this.cliAvailable = false;

      this.logger.warn(
        'Official Hermes CLI not found — JS fallback enabled'
      );
    }
  }

  isAvailable() {
    return this.cliAvailable;
  }

  /**
   * Check if Camofox browser service is available
   */
  async _checkCamofox() {
    if (this.camofoxAvailable !== null) return this.camofoxAvailable;
    
    try {
      const camofoxUrl = process.env.CAMOFOX_URL || 'http://localhost:9377';
      execSync(
        `curl -sf ${camofoxUrl}/health`,
        { timeout: 5000, stdio: 'pipe' }
      );
      this.camofoxAvailable = true;
      this.logger.info(`Camofox browser available at ${camofoxUrl}`);
    } catch {
      this.camofoxAvailable = false;
      this.logger.warn('Camofox browser not available — disabling browser tool for Hermes');
    }
    return this.camofoxAvailable;
  }

  /**
   * Run Hermes with a task prompt.
   * Uses execSync (shell) instead of spawnSync because Hermes v0.14
   * requires a TTY/PTY to display output. spawnSync returns empty.
   */
  async run(task, options = {}) {

    if (!this.cliAvailable) {
      throw new Error(
        'Hermes CLI unavailable'
      );
    }

    this.logger.header(
      'OFFICIAL HERMES CLI AGENT'
    );

    this.logger.info(
      `Task: ${task.substring(0,120)}`
    );

    try {

      const tempDir = path.join(
        __dirname,
        '..',
        'output',
        'temp'
      );

      fs.mkdirSync(
        tempDir,
        { recursive:true }
      );

      const taskFile = path.join(
        tempDir,
        `task_${Date.now()}.txt`
      );

      fs.writeFileSync(
        taskFile,
        task
      );

      this.logger.info(
        `Task written: ${taskFile}`
      );

      // Check Camofox availability and adjust tools accordingly
      const camofoxOk = await this._checkCamofox();
      const toolsList = camofoxOk 
        ? 'web,terminal,skills,browser' 
        : 'web,terminal,skills';

      this.logger.info(`Camofox available: ${camofoxOk} — using tools: ${toolsList}`);

      // Build the shell command. execSync runs through a shell which provides
      // proper TTY handling. Hermes v0.14 returns empty output via spawnSync.
      // NOTE: -z flag is the correct zero-turn non-interactive mode (top-level flag).
      // Do NOT add "chat" subcommand — it conflicts with -z.
      // Do NOT add --yolo as a subcommand flag — it's a top-level flag.
      // The tools list must be quoted per Hermes CLI docs.
      const escapedTask = task
        .replace(/\\/g, '\\\\')
        .replace(/"/g, '\\"')
        .replace(/\$/g, '\\$')
        .replace(/`/g, '\\`');

      const cmd = `hermes -z "${escapedTask}" -t "${toolsList}" --yolo`;

      this.logger.info(
        'Executing Hermes...'
      );

      this.logger.info(
        `Command: ${cmd.substring(0, 200)}`
      );

      // Use execSync which runs through the shell (provides TTY)
      // This is CRITICAL — spawnSync causes Hermes to return empty output
      const maxTimeout = 360000; // 6 minutes - Hermes needs time on first run
      
      const stdout = execSync(cmd, {
        env: {
          ...process.env,
          // Hermes uses Ollama for browsing/research tasks
          OLLAMA_HOST: process.env.OLLAMA_HOST || 'http://localhost:11434',
          OLLAMA_MODEL: process.env.HERMES_OLLAMA_MODEL || process.env.OLLAMA_MODEL || 'llama3.2',
          // Do NOT clear cloud keys — Hermes config at ~/.hermes/config.yaml
          // already points to the custom Ollama provider. Let Hermes resolve
          // its own provider chain from config without interference.
          CAMOFOX_URL: process.env.CAMOFOX_URL || 'http://localhost:9377',
          HERMES_VERBOSE: '1',
        },
        timeout: maxTimeout,
        maxBuffer: 20 * 1024 * 1024,
        stdio: ['pipe', 'pipe', 'pipe'],
      });

      const output = stdout.toString() || '';

      // ── Log output diagnostics ──
      this.logger.info('═══════════════════════════════════════════');
      this.logger.info('HERMES FULL OUTPUT DIAGNOSTICS:');
      this.logger.info(`output length: ${output.length} chars`);
      this.logger.info('═══════════════════════════════════════════');

      if (output) {
        this.logger.info('--- HERMES OUTPUT ---');
        const outputPreview = output.substring(0, 10000);
        console.log(outputPreview);
        if (output.length > 10000) {
          console.log(`... (${output.length - 10000} more chars)`);
        }
      } else {
        this.logger.warn('Hermes output is EMPTY');
      }

      this.logger.success('Hermes completed');

      return {
        success: true,
        output: output.trim(),
        fullOutput: output,
        agent: 'hermes-cli',
        steps: 1,
      };

    } catch (err) {
      // execSync throws on non-zero exit. But we might still have output.
      const output = err.stdout || err.stderr || '';

      if (output) {
        this.logger.info('--- HERMES OUTPUT (from error) ---');
        console.log(output.substring(0, 5000));
        this.logger.success('Hermes completed (partial output)');
        return {
          success: true,
          output: output.toString().trim(),
          fullOutput: output.toString(),
          agent: 'hermes-cli',
          steps: 1,
        };
      }

      this.logger.error('Hermes failed');
      this.logger.error(err.message);

      return {
        success: false,
        error: err.message,
        output: '',
        shouldFallback: true,
        agent: 'hermes-cli',
        steps: 0,
      };
    }
  }

  async chat(prompt) {
    return this.run(prompt);
  }

  async getInfo() {
    if (!this.cliAvailable) {
      return { available: false };
    }

    try {
      const version = execSync('hermes --version', { timeout: 5000 })
        .toString()
        .trim();
      return { available: true, version };
    } catch {
      return { available: true, version: 'unknown' };
    }
  }
}

module.exports = {
  HermesCLIWrapper
};