const { execSync, spawnSync } = require('child_process');
const { Logger } = require('../core/logger');

class HermesCLIWrapper {
  constructor() {
    this.logger = new Logger('HermesCLI');

    this.cliAvailable = false;
    this.browserProvider = null;
    this.browserCapabilities = {};

    this._checkCLI();
    this._detectBrowserProvider();
  }

  _checkCLI() {
    try {
      const version = execSync(
        'hermes --version',
        {
          encoding: 'utf8',
          timeout: 10000
        }
      ).trim();

      this.cliAvailable = true;

      this.logger.info(
        `Hermes CLI detected (${version})`
      );

    } catch (err) {

      this.cliAvailable = false;

      this.logger.warn(
        `Hermes unavailable: ${err.message}`
      );
    }
  }

  _detectBrowserProvider() {

    try {

      if(process.env.BROWSERBASE_API_KEY){

        this.browserProvider='browserbase';

      } else if(process.env.BROWSER_USE_API_KEY){

        this.browserProvider='browser-use';

      } else if(process.env.FIRECRAWL_API_KEY){

        this.browserProvider='firecrawl';

      } else if(process.env.CAMOFOX_URL){

        this.browserProvider='camofox';

      } else if(process.env.BROWSER_CDP_URL){

        this.browserProvider='cdp';

      } else {

        this.browserProvider='local';
      }

      this.browserCapabilities={

        screenshots:true,
        forms:true,
        vision:true,

        persistence:
          process.env.CAMOFOX_MANAGED_PERSISTENCE==='true',

        recording:
          process.env.BROWSER_RECORD_SESSIONS==='true',

        cdp:
          this.browserProvider==='cdp'
      };

      this.logger.info(
        `Browser provider: ${this.browserProvider}`
      );

    } catch(err){

      this.logger.warn(
        err.message
      );
    }

  }

  isAvailable(){
    return this.cliAvailable;
  }

  buildEnvironment(){

    const home=
      process.env.HOME || '/home/runner';

    const env={

      PATH:
        process.env.PATH,

      HOME:
        home,

      USER:
        process.env.USER,

      LANG:
        process.env.LANG || 'en_US.UTF-8',

      TERM:
        process.env.TERM || 'xterm',

      PWD:
        process.cwd(),

      HERMES_CONFIG:
        process.env.HERMES_CONFIG ||
        `${home}/.hermes/config.yaml`,

      HERMES_ENV:
        process.env.HERMES_ENV ||
        `${home}/.hermes/.env`,

      HERMES_VERBOSE:
        process.env.HERMES_VERBOSE || '1',

      OLLAMA_HOST:
        process.env.OLLAMA_HOST ||
        'http://localhost:11434',

      OLLAMA_MODEL:
        process.env.OLLAMA_MODEL ||
        'llama3.2:3b'
    };

    const allowed=[

      'OPENAI_API_KEY',
      'OPENROUTER_API_KEY',
      'ANTHROPIC_API_KEY',
      'GEMINI_API_KEY',
      'DEEPSEEK_API_KEY',

      'BROWSERBASE_API_KEY',
      'BROWSERBASE_PROJECT_ID',

      'BROWSER_USE_API_KEY',

      'FIRECRAWL_API_KEY',
      'FIRECRAWL_API_URL',

      'CAMOFOX_URL',
      'CAMOFOX_USER_ID',
      'CAMOFOX_SESSION_KEY',
      'CAMOFOX_ADOPT_EXISTING_TAB',

      'BROWSER_CDP_URL',

      'BROWSER_RECORD_SESSIONS'
    ];

    for(const key of allowed){

      if(process.env[key]){

        env[key]=process.env[key];
      }
    }

    return env;
  }

  buildArguments(task){

    const args=[
      '-z',
      task,
      '--yolo'
    ];

    return args;
  }

  detectFailure(output=''){

    const text=
      output.toLowerCase();

    const failures=[

      'traceback',
      'autherror',
      'provider error',
      'no inference provider configured',
      'fatal:',
      'panic:'
    ];

    return failures.some(
      x=>text.includes(x)
    );
  }

  async run(task,options={}){

    if(!this.cliAvailable){

      return{

        success:false,
        shouldFallback:true,
        error:'Hermes unavailable'
      };
    }

    try{

      const args=
        this.buildArguments(task);

      const timeout=
        options.timeout ||
        360000;

      this.logger.header(
        'HERMES AGENT'
      );

      this.logger.info(
        `Provider: ${this.browserProvider}`
      );

      this.logger.info(
        `Task: ${task.substring(0,200)}`
      );

      const result=
        spawnSync(
          'hermes',
          args,
          {
            encoding:'utf8',
            timeout,
            maxBuffer:
              100*1024*1024,
            env:
              this.buildEnvironment()
          }
        );

      const output=
        (
          (result.stdout||'')+
          '\n'+
          (result.stderr||'')
        ).trim();

      const failed=
        result.error ||
        this.detectFailure(output);

      return{

        success:!failed,

        output,

        fullOutput:output,

        shouldFallback:
          failed,

        provider:
          this.browserProvider,

        browser:
          this.browserCapabilities,

        exitCode:
          result.status,

        agent:
          'hermes-cli',

        error:
          failed
          ? (
            result.error?.message ||
            'Hermes execution failed'
          )
          : null
      };

    } catch(err){

      this.logger.error(
        err.stack ||
        err.message
      );

      return{

        success:false,
        shouldFallback:true,
        output:'',
        error:err.message,
        agent:'hermes-cli'
      };
    }
  }

  async chat(prompt){
    return this.run(prompt);
  }

  async getInfo(){

    return{

      available:
        this.cliAvailable,

      provider:
        this.browserProvider,

      browser:
        this.browserCapabilities
    };
  }

}

module.exports={
  HermesCLIWrapper
};
