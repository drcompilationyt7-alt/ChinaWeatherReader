const { execSync } = require('child_process');
const { Logger } = require('../core/logger');

class HermesCLIWrapper {
  constructor() {
    this.logger = new Logger('HermesCLI');

    this.cliAvailable = false;
    this.camofoxAvailable = null;

    this._checkCLI();
  }

  _checkCLI() {
    try {
      const version = execSync(
        'hermes --version',
        {
          timeout: 10000,
          encoding: 'utf8'
        }
      ).trim();

      this.cliAvailable = true;

      this.logger.info(
        `Official Hermes CLI detected: ${version}`
      );

    } catch (e) {

      this.cliAvailable = false;

      this.logger.warn(
        `Hermes CLI unavailable: ${e.message}`
      );
    }
  }

  isAvailable() {
    return this.cliAvailable;
  }

  async _checkCamofox() {

    if (this.camofoxAvailable !== null)
      return this.camofoxAvailable;

    const url =
      process.env.CAMOFOX_URL ||
      'http://localhost:9377';

    try {

      this.logger.info(
        `Checking Camofox at ${url}`
      );

      execSync(
        `curl -sf ${url}/health`,
        {
          timeout:5000,
          stdio:'pipe'
        }
      );

      execSync(
        `curl -sf ${url}/json/version`,
        {
          timeout:5000,
          stdio:'pipe'
        }
      );

      this.camofoxAvailable=true;

      this.logger.info(
        'Camofox browser fully available'
      );

    } catch(e){

      this.camofoxAvailable=false;

      this.logger.warn(
        `Camofox unavailable: ${e.message}`
      );
    }

    return this.camofoxAvailable;
  }

  escapeShell(str='') {
    return str
      .replace(/\\/g,'\\\\')
      .replace(/"/g,'\\"')
      .replace(/\$/g,'\\$')
      .replace(/`/g,'\\`')
      .replace(/\n/g,' ');
  }

  async run(task, options={}) {

    if (!this.cliAvailable) {

      return {
        success:false,
        error:'Hermes unavailable',
        output:'',
        shouldFallback:true
      };

    }

    this.logger.header(
      'OFFICIAL HERMES CLI AGENT'
    );

    this.logger.info(
      `Task: ${task.substring(0,150)}`
    );

    try {

      const camofoxOk =
        await this._checkCamofox();

      const tools = camofoxOk
        ? 'web,terminal,skills,browser'
        : 'web,terminal,skills';

      this.logger.info(
        `Using tools: ${tools}`
      );

      const escapedTask =
        this.escapeShell(task);

      // Use -z flag for zero-turn non-interactive mode (top-level flag)
      // Do NOT use 'chat' subcommand - it conflicts with -z
      // Do NOT add --yolo as a subcommand flag - it's a top-level flag
      // The tools list must be quoted per Hermes CLI docs
      const cmd = [
        'hermes',
        '-z',
        `"${escapedTask}"`,
        '-t',
        `"${tools}"`,
        '--yolo'
      ].join(' ');

      this.logger.info(
        'Executing Hermes...'
      );

      this.logger.info(
        `Command: ${cmd.substring(0,250)}`
      );

      const maxTimeout =
        options.timeout ||
        360000;

      let output='';

      try {

        // Build isolated environment for Hermes - DO NOT inherit cloud API keys
        // This prevents Hermes from auto-selecting OpenRouter/OpenAI/Anthropic
        // and forces it to use the local Ollama config in ~/.hermes/config.yaml
        const hermesEnv = {
          // Essential system variables with GitHub Actions defaults
          PATH: process.env.PATH || '/usr/local/bin:/usr/bin:/bin',
          HOME: process.env.HOME || '/home/runner',
          USER: process.env.USER || 'runner',
          LANG: process.env.LANG || 'en_US.UTF-8',
          TERM: process.env.TERM || 'xterm',
          PWD: process.env.PWD || process.env.HOME || '/home/runner',

          // Hermes-specific variables for local Ollama
          OLLAMA_HOST: process.env.OLLAMA_HOST || 'http://localhost:11434',
          OLLAMA_MODEL: process.env.OLLAMA_MODEL || 'llama3.2:3b',

          // Camofox browser URL
          CAMOFOX_URL: process.env.CAMOFOX_URL || 'http://localhost:9377',

          // CRITICAL: Tell Hermes where config.yaml is located
          HERMES_CONFIG: `${process.env.HOME || '/home/runner'}/.hermes/config.yaml`,
          
          // Point to .env file for any additional config
          HERMES_ENV: `${process.env.HOME || '/home/runner'}/.hermes/.env`,

          // Verbose mode for debugging
          HERMES_VERBOSE: '1',
          
          // Pass cloud API keys as fallback providers
          // Hermes will try Ollama first (from config.yaml), then fall back to these
          ...(process.env.OPENROUTER_API_KEY && { OPENROUTER_API_KEY: process.env.OPENROUTER_API_KEY }),
          ...(process.env.OPENAI_API_KEY && { OPENAI_API_KEY: process.env.OPENAI_API_KEY }),
          ...(process.env.ANTHROPIC_API_KEY && { ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY }),
          ...(process.env.GEMINI_API_KEY && { GEMINI_API_KEY: process.env.GEMINI_API_KEY }),
          ...(process.env.DEEPSEEK_API_KEY && { DEEPSEEK_API_KEY: process.env.DEEPSEEK_API_KEY }),
        };

        const apiKeysStatus = [
          process.env.OPENROUTER_API_KEY ? 'OpenRouter' : '',
          process.env.OPENAI_API_KEY ? 'OpenAI' : '',
          process.env.ANTHROPIC_API_KEY ? 'Anthropic' : ''
        ].filter(Boolean).join(', ') || 'none';
        this.logger.info(`Hermes env: Ollama=${hermesEnv.OLLAMA_HOST}, API keys=${apiKeysStatus}`);

        output = execSync(
          cmd,
          {
            env: hermesEnv,
            timeout: maxTimeout,
            encoding: 'utf8',
            maxBuffer: 50 * 1024 * 1024,
            stdio: 'pipe'
          }
        );

      }
      catch(err){

        output=
          (err.stdout || '')+
          '\n'+
          (err.stderr || '');

      }

      output=String(output||'').trim();

      // Check for critical errors that indicate failure
      const hasCriticalError = 
        output.toLowerCase().includes('traceback') ||
        output.includes('AuthError') ||
        output.toLowerCase().includes('error:') ||
        output.includes('No inference provider configured');

      this.logger.info(
        '═══════════════════════════'
      );

      this.logger.info(
        `Output length: ${output.length}`
      );

      if(output){

        this.logger.info(
          '──── HERMES OUTPUT ────'
        );

        console.log(
          output.substring(
            0,
            15000
          )
        );

        if(output.length>15000){

          console.log(
            `... ${output.length-15000} more chars`
          );
        }

      } else {

        this.logger.warn(
          'Hermes returned empty output'
        );
      }

      return {

        success: output.length > 0 && !hasCriticalError,

        output,

        fullOutput: output,

        shouldFallback: output.length === 0 || hasCriticalError,

        agent:'hermes-cli',

        steps: hasCriticalError ? 0 : 1,

        error: hasCriticalError ? 'Hermes execution failed with critical error' : null
      };

    }
    catch(err){

      this.logger.error(
        err.stack || err.message
      );

      return {

        success:false,

        output:'',

        error:err.message,

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
          'hermes --version',
          {
            timeout:5000,
            encoding:'utf8'
          }
        ).trim();

      return{

        available:true,
        version
      };

    }
    catch{

      return{

        available:true,
        version:'unknown'
      };
    }
  }
}

module.exports={
  HermesCLIWrapper
};
