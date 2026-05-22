/**
 * Mr. WorldWideWebster - System Test
 * Tests that all modules load correctly
 */
const chalk = require('chalk');

console.log(chalk.cyan.bold('\n🔍 Mr. WorldWideWebster - System Test\n'));

let passed = 0;
let failed = 0;

function test(module, name) {
  try {
    const mod = require(module);
    // If it's a class, try instantiation
    if (typeof mod === 'function' && mod.toString().includes('class')) {
      try { new mod({}); } catch (e) { /* constructor may need params */ }
    }
    console.log(chalk.green(`  ✅ ${name}`));
    passed++;
  } catch (err) {
    console.log(chalk.red(`  ❌ ${name} — ${err.message}`));
    failed++;
  }
}

function testInstance(module, name) {
  try {
    const mod = require(module);
    console.log(chalk.green(`  ✅ ${name}`));
    passed++;
  } catch (err) {
    console.log(chalk.red(`  ❌ ${name} — ${err.message}`));
    failed++;
  }
}

console.log(chalk.yellow('═══ Core Modules ═══'));
test('../core/config', 'Config');
test('../core/logger', 'Logger');
test('../core/ai-service', 'AI Service');
test('../core/decision-engine', 'Decision Engine');
test('../core/content-router', 'Content Router');
test('../core/index', 'Main Orchestrator');

console.log(chalk.yellow('\n═══ Providers ═══'));
test('../providers/openrouter-provider', 'OpenRouter Provider');
test('../providers/edge-tts-provider', 'Edge-TTS Provider');

console.log(chalk.yellow('\n═══ Pipelines ═══'));
test('../clipping/clip-pipeline', 'Clip Pipeline');
testInstance('../voiceover/voiceover-pipeline', 'Voiceover Pipeline');
testInstance('../explainer/explain-pipeline', 'Explain Pipeline');
testInstance('../ai-creator/ai-create-pipeline', 'AI Create Pipeline');

console.log(chalk.yellow('\n═══ Hermes Agent ═══'));
test('../hermes-agent/agent-core', 'Hermes Agent Core');

console.log(chalk.yellow('\n═══ Long-Form ═══'));
test('../long-form/slideshow-compiler', 'Slideshow Compiler');

console.log(chalk.yellow('\n═══ Sourcing ═══'));
test('../sourcing/source-controller', 'Source Controller');
test('../sourcing/bilibili-scraper', 'Bilibili Scraper');
test('../sourcing/tiktok-scraper', 'TikTok Scraper');
test('../sourcing/news-sourcer', 'News Sourcer');
test('../sourcing/douyin-scraper', 'Douyin Scraper');
test('../sourcing/rednote-scraper', 'RedNote Scraper');
test('../sourcing/twitter-scraper', 'Twitter Scraper');

console.log(chalk.yellow('\n═══ Setup ═══'));
test('../scripts/setup', 'Setup Script');

console.log(chalk.cyan.bold(`\n📊 Results: ${passed} passed, ${failed} failed out of ${passed + failed} tests\n`));

if (failed > 0) {
  process.exit(1);
}