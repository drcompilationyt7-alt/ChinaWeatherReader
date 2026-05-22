#!/bin/bash
# =============================================================================
# Mr. WorldWideWebster — VPS Deployment Script
# Run this on your VPS to deploy the 24/7 autonomous YouTube channel.
#
# Usage:
#   ssh user@your-vps "bash -s" < deploy-vps.sh
#   Or copy to VPS and run: bash deploy-vps.sh
# =============================================================================

set -e

echo "╔════════════════════════════════════════════════════════╗"
echo "║   Mr. WorldWideWebster - VPS Auto-Deploy              ║"
echo "╚════════════════════════════════════════════════════════╝"

# ─── Configuration ──────────────────────────────────────────────────────────
REPO_URL=""                    # Your Git repo URL (optional)
APP_DIR="$HOME/mr-worldwidewebster"
NODE_VERSION="18"

echo ""
echo "[1/6] Installing system dependencies..."

# Install Node.js if not present
if ! command -v node &> /dev/null; then
    echo "  → Installing Node.js $NODE_VERSION..."
    curl -fsSL https://deb.nodesource.com/setup_$NODE_VERSION.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi

# Install FFmpeg if not present
if ! command -v ffmpeg &> /dev/null; then
    echo "  → Installing FFmpeg..."
    sudo apt-get update && sudo apt-get install -y ffmpeg
fi

# Install PM2 globally for process management
if ! command -v pm2 &> /dev/null; then
    echo "  → Installing PM2 process manager..."
    npm install -g pm2
fi

# Install Python + yt-dlp if needed
if ! command -v yt-dlp &> /dev/null; then
    echo "  → Installing yt-dlp..."
    sudo apt-get install -y python3 python3-pip
    pip3 install yt-dlp
fi

# Install edge-tts for free voiceover
pip3 install edge-tts 2>/dev/null || echo "  → edge-tts already installed or unavailable"

echo ""
echo "[2/6] Setting up application..."

# Clone or update
if [ ! -d "$APP_DIR" ]; then
    mkdir -p "$APP_DIR"
    echo "  → Created $APP_DIR"
    echo "  ⚠️  You need to copy your files here!"
    echo "     Use: scp -r mr-worldwidewebster/* user@vps:$APP_DIR/"
else
    echo "  → App directory exists at $APP_DIR"
fi

cd "$APP_DIR"

echo ""
echo "[3/6] Installing Node.js dependencies..."
npm install --production

echo ""
echo "[4/6] Setting up environment..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "  ⚠️  EDIT YOUR .env FILE with your API keys!"
    echo "     nano $APP_DIR/.env"
    echo "     Set OPENROUTER_API_KEY=sk-or-..."
else
    echo "  → .env already exists"
fi

echo ""
echo "[5/6] Configuring PM2 for 24/7 auto-restart..."
pm2 delete mww 2>/dev/null || true

# Start the automation scheduler
pm2 start core/automation-scheduler.js \
    --name mww \
    --max-memory-restart 512M \
    --cron-restart="0 4 * * *" \
    --log "$APP_DIR/logs/app.log" \
    --error "$APP_DIR/logs/error.log" \
    --merge-logs

# Save PM2 config so it restarts on VPS reboot
pm2 save
pm2 startup 2>/dev/null || true

echo ""
echo "[6/6] Setting up log rotation..."
pm2 install pm2-logrotate
pm2 set pm2-logrotate:max_size 50M
pm2 set pm2-logrotate:retain 7
pm2 set pm2-logrotate:compress true

echo ""
echo "╔════════════════════════════════════════════════════════╗"
echo "║   ✅ DEPLOYMENT COMPLETE                              ║"
echo "╚════════════════════════════════════════════════════════╝"
echo ""
echo "What's running:"
echo "  📡 Content sourcing every 6 hours"
echo "  ⚡ Queue processing every hour"
echo "  🧠 Hermes Agent strategy review daily"
echo "  🔥 Trend discovery daily at 6 AM"
echo "  📤 YouTube upload daily at 9 AM"
echo ""
echo "Useful commands:"
echo "  pm2 status              → See running processes"
echo "  pm2 logs mww            → Watch live output"
echo "  pm2 monit               → Dashboard"
echo "  pm2 restart mww         → Restart the agent"
echo "  pm2 stop mww            → Stop the agent"
echo "  nano $APP_DIR/.env      → Edit API keys"
echo ""
echo "Initial setup:"
echo "  1. Edit your .env file with API keys"
echo "  2. Run: pm2 restart mww"
echo "  3. Watch: pm2 logs mww"
echo ""