/**
 * Mr. WorldWideWebster - Setup Script
 * 
 * Guides the user through initial configuration.
 * Creates .env file and installs dependencies.
 */
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const ENV_EXAMPLE_PATH = path.join(__dirname, '..', '.env.example');
const ENV_PATH = path.join(__dirname, '..', '.env');

console.log(`
╔════════════════════════════════════════════════════════╗
║        Mr. WorldWideWebster - Setup Wizard             ║
║        "Bringing the world to you"                    ║
╚════════════════════════════════════════════════════════╝
`);

// ─── Step 1: Create .env if needed ──────────────────────────────────────────

if (!fs.existsSync(ENV_PATH)) {
  console.log('📝 Creating .env file from .env.example...');
  
  if (fs.existsSync(ENV_EXAMPLE_PATH)) {
    const envContent = fs.readFileSync(ENV_EXAMPLE_PATH, 'utf8');
    fs.writeFileSync(ENV_PATH, envContent);
    console.log('✅ .env file created! Open it and add your API keys.');
  } else {
    console.error('❌ .env.example not found!');
    process.exit(1);
  }
} else {
  console.log('✅ .env file already exists');
}

// ─── Step 2: Install npm dependencies ──────────────────────────────────────

console.log('\n📦 Installing npm dependencies...');
try {
  execSync('npm install', { cwd: path.join(__dirname, '..'), stdio: 'inherit' });
  console.log('✅ npm dependencies installed');
} catch (error) {
  console.error('❌ npm install failed. Try running: npm install');
}

// ─── Step 3: Check OpenAI Key ──────────────────────────────────────────────

console.log('\n🔑 Checking API keys...');
const envConfig = fs.readFileSync(ENV_PATH, 'utf8');

if (envConfig.includes('sk-your-openai-api-key-here') || !envConfig.includes('OPENAI_API_KEY=')) {
  console.log('⚠️  OPENAI_API_KEY not set!');
  console.log('   Get one at: https://platform.openai.com/api-keys');
  console.log('   Then add it to your .env file');
} else {
  console.log('✅ OPENAI_API_KEY found');
}

// ─── Step 4: Check Python / AI-Youtube-Shorts-Generator ───────────────────

console.log('\n🐍 Checking Python clipping module...');
try {
  execSync('python --version', { stdio: 'pipe' });
  console.log('✅ Python detected');
  
  const shortsGenPath = path.join(__dirname, '..', '..', 'AI-Youtube-Shorts-Generator-main');
  if (fs.existsSync(shortsGenPath)) {
    console.log('✅ AI-Youtube-Shorts-Generator found');
    console.log('   Install its dependencies:');
    console.log(`   cd "${shortsGenPath}"`);
    console.log('   pip install -r requirements.txt');
  } else {
    console.log('⚠️  AI-Youtube-Shorts-Generator not in parent directory');
    console.log('   This is optional - the system will use fallback clipping');
  }
} catch {
  console.log('⚠️  Python not found. Install Python 3.10+ for clipping features');
}

// ─── Step 5: Check ffmpeg ─────────────────────────────────────────────────

console.log('\n🎬 Checking ffmpeg...');
try {
  execSync('ffmpeg -version', { stdio: 'pipe' });
  console.log('✅ ffmpeg detected');
} catch {
  console.log('⚠️  ffmpeg not found');
  console.log('   Install it for video processing:');
  console.log('   https://ffmpeg.org/download.html');
}

// ─── Summary ───────────────────────────────────────────────────────────────

console.log(`
╔════════════════════════════════════════════════════════╗
║                     SETUP COMPLETE                     ║
╚════════════════════════════════════════════════════════╝

Next steps:
1. Edit your .env file with your API keys
2. Run: node core/index.js --help
3. Run: node core/index.js

Example: Create AI content about a topic:
  node core/index.js --topic "UK Drill vs US Trap Music"

Example: Run full pipeline:
  node core/index.js

Example: Source content only:
  node core/index.js --source
`);