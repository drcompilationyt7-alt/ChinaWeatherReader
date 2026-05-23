const { spawnSync, execSync } = require('child_process');
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

      /*
       Hermes v0.14 syntax:

       hermes -z "prompt" \
         -t "web,terminal,skills,browser" \
         chat --yolo
      */

      const args = [

        '-z',
        task,

        '-t',
        'web,terminal,skills,browser',

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
          timeout:300000,
          maxBuffer:20*1024*1024
        }
      );

      if(result.stdout){

        this.logger.info(
          'Hermes stdout:'
        );

        const outputPreview = result.stdout.substring(0, 10000);
        console.log(outputPreview);
        
        // Log if output looks like it contains JSON
        if (outputPreview.includes('{') && outputPreview.includes('}')) {
          this.logger.info('Output appears to contain JSON structure');
        }
      }

      if(result.stderr){

        this.logger.warn(
          'Hermes stderr:'
        );

        console.log(
          result.stderr.substring(
            0,
            10000
          )
        );
      }

      if(result.status !== 0){

        throw new Error(
          `Exit code ${result.status}`
        );
      }

      this.logger.success(
        'Hermes completed'
      );

      return {

        success:true,

        output:
          result.stdout,

        fullOutput:
          result.stdout,

        agent:
          'hermes-cli',

        steps:1
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
