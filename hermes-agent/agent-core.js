/**
 * Mr. WorldWideWebster — Hermes Agent Core
 * 
 * An AI agent system that uses tool-calling models (Hermes 3, Qwen, etc.)
 * via OpenRouter to autonomously:
 * - Browse the web for content
 * - Make decisions about what to create
 * - Run commands on the VPS
 * - Learn and save reusable skills
 * 
 * The agent uses function calling to interact with tools, meaning the LLM
 * decides when to use tools, not the code. This makes it very flexible.
 */
const fs = require('fs');
const path = require('path');
const config = require('../core/config');
const { Logger } = require('../core/logger');

class HermesAgent {
  constructor(aiService) {
    this.ai = aiService;
    this.logger = new Logger('HermesAgent');
    this.tools = {};
    this.skills = {};
    this.conversationHistory = [];
    this.maxHistoryLength = 20;
    
    this._registerTools();
    this._loadSkills();
  }

  /**
   * Register all available tools the agent can use
   */
  _registerTools() {
    const tools = {
      // ─── Web Tools ───────────────────────────────────────────────────
      web_search: {
        description: 'Search the web for information. Returns top results with titles and snippets.',
        parameters: { query: 'string' },
        execute: async (args) => {
          this.logger.info(`🔍 Web search: "${args.query}"`);
          try {
            const axios = require('axios');
            const response = await axios.get(`https://api.duckduckgo.com/?q=${encodeURIComponent(args.query)}&format=json`);
            return JSON.stringify(response.data, null, 2).substring(0, 5000);
          } catch {
            return `Search results for "${args.query}": (simulated) Found trending content about this topic across multiple platforms.`;
          }
        }
      },

      web_browse: {
        description: 'Browse a web page and extract its text content.',
        parameters: { url: 'string' },
        execute: async (args) => {
          this.logger.info(`🌐 Browsing: ${args.url}`);
          try {
            const axios = require('axios');
            const response = await axios.get(args.url, { timeout: 15000 });
            // Strip HTML tags and return first 5000 chars
            const text = response.data.replace(/<[^>]*>/g, '').substring(0, 5000);
            return text;
          } catch (error) {
            return `Failed to browse ${args.url}: ${error.message}`;
          }
        }
      },

      // ─── Content Discovery Tools ────────────────────────────────────
      discover_trending: {
        description: 'Discover trending content from a specific platform. Platforms: bilibili, tiktok, news, douyin, rednote, twitter',
        parameters: { platform: 'string', count: 'number' },
        execute: async (args) => {
          this.logger.info(`📡 Discovering: ${args.platform}`);
          try {
            const sourceModule = require(`../sourcing/${args.platform}-scraper`);
            const items = await sourceModule.fetchTrending(config);
            return JSON.stringify(items.slice(0, args.count || 5), null, 2);
          } catch {
            return `Platform "${args.platform}" not available yet. Try: bilibili, tiktok, news`;
          }
        }
      },

      // ─── Content Creation Tools ──────────────────────────────────────
      write_script: {
        description: 'Write a video script for a given topic and content type. Content types: explainer, comparison, news_summary, listicle',
        parameters: { topic: 'string', contentType: 'string' },
        execute: async (args) => {
          this.logger.info(`✍️ Writing script: ${args.topic}`);
          const { AIService } = require('../core/ai-service');
          const ai = new AIService();
          const { DecisionEngine } = require('../core/decision-engine');
          const engine = new DecisionEngine(ai);
          const result = await engine.generateTitle(
            { title: args.topic, platform: 'agent' },
            { path: 'ai_create', contentType: args.contentType }
          );
          return JSON.stringify({ generatedTitle: result }, null, 2);
        }
      },

      download_video: {
        description: 'Download a video from ANY platform (YouTube, Bilibili, TikTok, Douyin, Instagram, Twitter/X, Facebook, Reddit, and 1700+ more). Uses yt-dlp with Puppeteer and API fallbacks.',
        parameters: { url: 'string', outputDir: 'string' },
        execute: async (args) => {
          this.logger.info(`📥 Universal download: ${args.url}`);
          try {
            const { UniversalDownloader } = require('../sourcing/universal-downloader');
            const downloader = new UniversalDownloader();
            const result = await downloader.download(args.url, {
              outputDir: args.outputDir || config.paths.output,
              maxHeight: 720,
            });
            if (result.success) {
              return `✅ Downloaded from ${result.platform} using ${result.method}: ${result.filePath}\nTitle: ${result.title || 'Unknown'}`;
            }
            return `❌ All download methods failed for ${args.url}. The platform may require special handling.`;
          } catch (error) {
            return `❌ Download failed: ${error.message}`;
          }
        }
      },

      // ─── VPS / System Tools ──────────────────────────────────────────
      run_command: {
        description: 'Run a shell command on the local machine or VPS. Use for: git operations, file management, starting services.',
        parameters: { command: 'string', cwd: 'string' },
        execute: async (args) => {
          this.logger.info(`💻 Running: ${args.command}`);
          const { exec } = require('child_process');
          const { promisify } = require('util');
          const execAsync = promisify(exec);
          try {
            const options = { timeout: 60000, maxBuffer: 10 * 1024 * 1024 };
            if (args.cwd) options.cwd = args.cwd;
            const { stdout, stderr } = await execAsync(args.command, options);
            return stdout || stderr || '(no output)';
          } catch (error) {
            return `Command failed: ${error.message}`;
          }
        }
      },

      read_file: {
        description: 'Read the contents of a file.',
        parameters: { path: 'string', maxLines: 'number' },
        execute: async (args) => {
          try {
            const content = fs.readFileSync(args.path, 'utf8');
            const lines = content.split('\n');
            const max = args.maxLines || 100;
            return lines.slice(0, max).join('\n');
          } catch (error) {
            return `Read failed: ${error.message}`;
          }
        }
      },

      write_file: {
        description: 'Write content to a file. Used for saving scripts, configs, and creating new skills.',
        parameters: { path: 'string', content: 'string' },
        execute: async (args) => {
          try {
            const dir = path.dirname(args.path);
            if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
            fs.writeFileSync(args.path, args.content, 'utf8');
            return `Written to ${args.path} (${args.content.length} bytes)`;
          } catch (error) {
            return `Write failed: ${error.message}`;
          }
        }
      },

      // ─── Skill Management Tools ──────────────────────────────────────
      create_skill: {
        description: 'Create a new reusable skill. Skills are JSON files that define automated workflows.',
        parameters: { name: 'string', description: 'string', steps: 'array' },
        execute: async (args) => {
          const skill = {
            name: args.name,
            description: args.description,
            steps: args.steps,
            createdAt: new Date().toISOString(),
            version: 1,
          };
          const skillPath = path.join(config.paths.skills, `${args.name}.json`);
          fs.writeFileSync(skillPath, JSON.stringify(skill, null, 2));
          this.skills[args.name] = skill;
          return `Skill "${args.name}" created with ${args.steps.length} steps`;
        }
      },

      list_skills: {
        description: 'List all saved skills.',
        parameters: {},
        execute: async () => {
          const skillList = Object.entries(this.skills).map(([name, skill]) => ({
            name,
            description: skill.description,
            steps: skill.steps.length,
          }));
          return JSON.stringify(skillList, null, 2);
        }
      },

      run_skill: {
        description: 'Execute a saved skill by name.',
        parameters: { name: 'string', params: 'object' },
        execute: async (args) => {
          const skill = this.skills[args.name];
          if (!skill) return `Skill "${args.name}" not found`;
          
          this.logger.info(`⚡ Running skill: ${args.name}`);
          const results = [];
          for (const step of skill.steps) {
            this.logger.info(`  Step: ${step}`);
            results.push({ step, status: 'executed', note: `Would execute: ${step}` });
          }
          return JSON.stringify({ skill: args.name, steps: results.length, results }, null, 2);
        }
      },
    };

    this.tools = tools;
    this.logger.info(`Registered ${Object.keys(tools).length} tools`);
  }

  /**
   * Load saved skills from disk
   */
  _loadSkills() {
    const skillsDir = config.paths.skills;
    if (!fs.existsSync(skillsDir)) {
      fs.mkdirSync(skillsDir, { recursive: true });
      return;
    }

    const files = fs.readdirSync(skillsDir).filter(f => f.endsWith('.json'));
    for (const file of files) {
      try {
        const skill = JSON.parse(fs.readFileSync(path.join(skillsDir, file), 'utf8'));
        this.skills[skill.name] = skill;
      } catch (error) {
        this.logger.warn(`Failed to load skill ${file}: ${error.message}`);
      }
    }
    this.logger.info(`Loaded ${Object.keys(this.skills).length} skills`);
  }

  /**
   * Run the agent with a task prompt
   * @param {string} task — What the agent should do
   * @param {Object} options — { maxSteps, verbose }
   * @returns {Promise<Object>} — { result, steps, output }
   */
  async run(task, options = {}) {
    const maxSteps = options.maxSteps || 10;
    const verbose = options.verbose ?? true;

    this.logger.header(`HERMES AGENT: ${task.substring(0, 80)}`);

    const systemPrompt = `You are Hermes Agent — the autonomous system service powering "Mr. WorldWideWebster," an AI YouTube channel that discovers and repurposes global content.

===== SYSTEM SERVICE IDENTITY =====
You are NOT a chatbot. You are an automated system service that runs in GitHub Actions. 
- You receive tasks and execute them autonomously using tools
- You make decisions without asking for permission
- You report results when done
- You persist skills and learnings via git commits

===== CAPABILITIES =====
You have access to the following tools:
${Object.keys(this.tools).map(k => `- ${k}: ${this.tools[k].description}`).join('\n')}

===== TOOL USAGE RULES =====
1. BREAK DOWN complex tasks into small tool calls
2. THINK about which tool is best for each step
3. CALL ONE TOOL at a time, then analyze the result
4. ITERATE until the task is complete

===== RESPONSE FORMAT =====
To call a tool, respond with EXACTLY this JSON:
{"tool": "tool_name", "args": {"param": "value"}, "thought": "why I'm calling this"}

To report completion, respond with:
{"result": "summary of what was accomplished", "output": "any output data"}

===== WORKFLOW =====
1. ANALYZE the task and plan your approach
2. SEARCH/BROWSE the web for relevant content
3. DISCOVER and EXTRACT valuable content
4. CREATE scripts, download videos, or generate assets
5. REPORT what was accomplished
6. SAVE learnings as reusable skills when possible

Always begin by analyzing the task. Be decisive — use tools proactively.`;

    const messages = [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: task }
    ];

    let steps = [];
    let finalResult = '';

    for (let i = 0; i < maxSteps; i++) {
      // Get response from LLM
      const model = config.openrouter?.agentModel || 'nousresearch/hermes-3-70b';
      const response = await this.ai.chat(
        messages.map(m => m.content).join('\n\n'),
        'Continue with the next step.',
        { model, temperature: 0.3, maxTokens: 2000 }
      );

      // Try to parse as JSON tool call
      let parsed;
      try {
        const jsonMatch = response.match(/\{[\s\S]*\}/);
        if (jsonMatch) parsed = JSON.parse(jsonMatch[0]);
      } catch {}

      if (!parsed) {
        // Not a tool call — might be final response
        finalResult = response;
        if (verbose) this.logger.info(`🤖 Agent: ${response.substring(0, 200)}`);
        break;
      }

      if (parsed.result) {
        finalResult = parsed.result;
        if (verbose) this.logger.success(`✅ ${parsed.result}`);
        if (parsed.output) finalResult += '\n' + parsed.output;
        break;
      }

      if (parsed.tool && this.tools[parsed.tool]) {
        if (verbose) this.logger.info(`🔧 Tool: ${parsed.tool} | ${parsed.thought || ''}`);
        
        try {
          const toolResult = await this.tools[parsed.tool].execute(parsed.args || {});
          steps.push({ tool: parsed.tool, args: parsed.args, result: toolResult });
          
          messages.push(
            { role: 'assistant', content: JSON.stringify(parsed) },
            { role: 'system', content: `Tool "${parsed.tool}" returned: ${toolResult.substring(0, 2000)}` }
          );
          
          if (verbose) this.logger.info(`  ↳ ${toolResult.substring(0, 150)}`);
        } catch (error) {
          this.logger.error(`Tool ${parsed.tool} failed: ${error.message}`);
          messages.push(
            { role: 'assistant', content: JSON.stringify(parsed) },
            { role: 'system', content: `Tool "${parsed.tool}" failed: ${error.message}` }
          );
        }
      } else {
        if (verbose) this.logger.warn(`Unknown tool: ${parsed.tool}`);
        break;
      }
    }

    this.logger.success(`Agent completed in ${steps.length} steps`);

    return {
      task,
      steps,
      stepsCount: steps.length,
      result: finalResult,
      skills: Object.keys(this.skills),
    };
  }

  /**
   * Create a new skill from a demonstration
   */
  async createSkill(name, description, steps) {
    return await this.tools.create_skill.execute({ name, description, steps });
  }

  /**
   * Run a saved skill
   */
  async runSkill(name, params = {}) {
    const skill = this.skills[name];
    if (!skill) throw new Error(`Skill "${name}" not found`);
    
    this.logger.info(`Running skill: ${name}`);
    const results = [];
    
    for (const step of skill.steps) {
      this.logger.info(`  Step: ${step}`);
      // Execute step description as a task for the agent
      const result = await this.run(`Execute this step: ${step}`, { maxSteps: 3, verbose: false });
      results.push(result);
    }
    
    return { skill: name, results };
  }
}

module.exports = { HermesAgent };