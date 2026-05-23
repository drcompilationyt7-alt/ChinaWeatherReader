const { spawnSync, execSync } = require('child_process');
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
      const healthResult = execSync(
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

      const env = {

        ...process.env,

        // ═══════════════════════════════════════════════════════
        //  HERMES AGENT → OLLAMA (Local Model for Web Research)
        // ═══════════════════════════════════════════════════════
        // Hermes uses Ollama for browsing/research tasks
        OLLAMA_HOST:
          process.env.OLLAMA_HOST ||
          'http://localhost:11434',
        
        OLLAMA_MODEL:
          process.env.HERMES_OLLAMA_MODEL ||
          process.env.OLLAMA_MODEL ||
          'llama3.2',

        // Clear OpenRouter keys so Hermes CLI uses Ollama instead
        OPENROUTER_API_KEY: '',
        OPENROUTER_API_KEY_2: '',
        OPENROUTER_API_KEY_3: '',
        OPENROUTER_API_KEY_4: '',

        CAMOFOX_URL:
          process.env.CAMOFOX_URL ||
          'http://localhost:9377',

        HERMES_VERBOSE:'1'
      };

      // Check Camofox availability and adjust tools accordingly
      const camofoxOk = await this._checkCamofox();
      const toolsList = camofoxOk 
        ? 'web,terminal,skills,browser' 
        : 'web,terminal,skills';

      this.logger.info(`Camofox available: ${camofoxOk} — using tools: ${toolsList}`);

      const args = [

        '-z',
        task,

        '-t',
        toolsList,

        'chat',

        '--yolo'
      ];

      this.logger.info(
        'Executing Hermes...'
      );

      this.logger.info(
        `CAMOFOX_URL=${env.CAMOFOX_URL}`
      );

      this.logger.info(
        `Command: hermes ${args.join(' ')}`
      );

      const result = spawnSync(
        'hermes',
        args,
        {
          env,
          encoding:'utf8',
          timeout:120000,         // 2 min timeout (was 300s)
          maxBuffer:20*1024*1024
        }
      );

      // ── CRITICAL: Log EVERYTHING Hermes produced ──
      this.logger.info('═══════════════════════════════════════════');
      this.logger.info('HERMES FULL OUTPUT DIAGNOSTICS:');
      this.logger.info(`Exit code: ${result.status}`);
      this.logger.info(`stdout length: ${(result.stdout || '').length} chars`);
      this.logger.info(`stderr length: ${(result.stderr || '').length} chars`);
      this.logger.info('═══════════════════════════════════════════');

      if(result.stdout){
        this.logger.info('--- HERMES STDOUT ---');
        const outputPreview = result.stdout.substring(0, 10000);
        console.log(outputPreview);
        if (result.stdout.length > 10000) {
          console.log(`... (${result.stdout.length - 10000} more chars)`);
        }
      } else {
        this.logger.warn('Hermes stdout is EMPTY');
      }

      if(result.stderr){
        this.logger.warn('--- HERMES STDERR ---');
        console.log(result.stderr.substring(0, 10000));
        if (result.stderr.length > 10000) {
          console.log(`... (${result.stderr.length - 10000} more chars)`);
        }
      }

      // Combine stdout + stderr for URL extraction
      const combinedOutput = (result.stdout || '') + '\n' + (result.stderr || '');

      if(result.status !== 0){
        this.logger.warn(`Hermes exited with code ${result.status}`);
        // Even on non-zero exit, return what we got
        return {
          success: true,  // Don't trigger fallback if we got URLs
          output: combinedOutput.trim(),
          fullOutput: combinedOutput,
          stderr: result.stderr || '',
          agent: 'hermes-cli',
          steps: 1,
          exitCode: result.status,
        };
      }

      this.logger.success(
        'Hermes completed'
      );

      return {

        success:true,

        output:
          combinedOutput.trim(),

        fullOutput:
          combinedOutput,

        stderr:
          result.stderr || '',

        agent:
          'hermes-cli',

        steps:1,

        exitCode: result.status,
      };

    }
    catch(err){

      this.logger.error(
        'Hermes failed'
      );

      this.logger.error(
        err.stack || err.message
      );

      return {

        success:false,

        error:err.message,

        output:'',

        shouldFallback:true,

        agent:'hermes-cli',

        steps:0
      };
    }
  }

  async chat(prompt){

    return this.run(prompt);

  }

  async getInfo(){

    if(!this.cliAvailable){

      return {
        available:false
      };

    }

    try{

      const version=
      execSync(
        'hermes --version'
      )
      .toString()
      .trim();

      return {

        available:true,
        version

      };

    }
    catch{

      return {

        available:true,
        version:'unknown'

      };

    }
  }
}

module.exports = {
  HermesCLIWrapper
};