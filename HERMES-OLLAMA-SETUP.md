# Hermes Agent with Ollama Setup Guide

## Overview

This configuration separates AI responsibilities:

- **Hermes Agent (Web Research)** → Uses **Ollama** (local model) for browsing the web, finding trends, and scraping content
- **OpenRouter** → Used for analysis, script creation, explainers, and all other AI tasks

## Why This Architecture?

1. **Cost Efficiency**: Ollama is free and runs locally - perfect for frequent web research queries
2. **Privacy**: Web research data stays local when using Ollama
3. **Best Tool for Job**: OpenRouter has superior models for creative writing and analysis
4. **Speed**: Local Ollama model responds quickly for simple research tasks

## Setup Instructions

### 1. Install Ollama

```bash
# macOS/Linux
curl -fsSL https://ollama.ai/install.sh | sh

# Windows - download from https://ollama.ai/download
```

### 2. Pull a Model

```bash
# Recommended lightweight model for Hermes web research
ollama pull llama3.2

# Alternative models (choose based on your needs):
# ollama pull mistral       # Good balance of speed/quality
# ollama pull codellama     # Better for code-related tasks
# ollama pull llama3.1      # Larger, more capable model
```

### 3. Configure Environment Variables

Add these to your `.env` file or GitHub Secrets:

```bash
# ═══════════════════════════════════════════════════
# HERMES AGENT — OLLAMA CONFIGURATION
# ═══════════════════════════════════════════════════

# Ollama host URL (default is local)
OLLAMA_HOST=http://localhost:11434

# Model for Hermes web research (lightweight, fast)
OLLAMA_MODEL=llama3.2

# Optional: Override specifically for Hermes agent
HERMES_OLLAMA_MODEL=llama3.2

# ═══════════════════════════════════════════════════
# OPENROUTER — For Analysis & Script Creation
# ═══════════════════════════════════════════════════

OPENROUTER_API_KEY=sk-or-your-key-here
OPENROUTER_DEFAULT_MODEL=openrouter/owl-alpha
OPENROUTER_SCRIPT_MODEL=openrouter/owl-alpha
```

### 4. GitHub Actions Configuration

In your GitHub repository, go to **Settings → Secrets and variables → Actions** and add:

| Secret Name | Value |
|-------------|-------|
| `OLLAMA_HOST` | `http://localhost:11434` (or your remote Ollama server) |
| `OLLAMA_MODEL` | `llama3.2` |
| `HERMES_OLLAMA_MODEL` | `llama3.2` (optional override) |
| `OPENROUTER_API_KEY` | Your OpenRouter API key |

> **Note**: For GitHub Actions, you'll need to run Ollama on a server that's accessible from GitHub Actions runners, or use a self-hosted runner with Ollama installed locally.

### 5. Verify Setup

```bash
# Test Ollama is running
curl http://localhost:11434/api/tags

# Test Hermes CLI with Ollama
hermes -z "What is 2+2?" -t terminal chat --yolo
```

## How It Works

### Hermes CLI Wrapper (`hermes-agent/hermes-cli-wrapper.js`)

The wrapper now:
1. Sets `OLLAMA_HOST` and `OLLAMA_MODEL` environment variables
2. **Clears** `OPENROUTER_API_KEY` variables before calling Hermes CLI
3. This forces Hermes to use Ollama instead of OpenRouter

### Other Pipelines (Explain, Scripts, etc.)

All other code continues to use `AIService` which connects to OpenRouter for:
- Script generation
- Content analysis
- Title/description creation
- Translation tasks

## Troubleshooting

### "AI did not return valid JSON" Error

This can happen when Ollama returns imperfect JSON. The system now has enhanced parsing that:
1. Extracts JSON from wrapped responses
2. Logs parse failures for debugging
3. Falls back to random selection if parsing fails

To improve JSON quality:
- Use a larger model: `ollama pull llama3.1`
- Update `HERMES_OLLAMA_MODEL=llama3.1` in your `.env`

### Hermes CLI Not Using Ollama

Check that:
1. Ollama is running: `ollama list`
2. Environment variables are set correctly
3. No `OPENROUTER_API_KEY` is being passed to Hermes (the wrapper clears these)

### Connection Refused Errors

If using remote Ollama:
```bash
# On Ollama server, allow external connections
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

Then update your client:
```bash
OLLAMA_HOST=http://your-server-ip:11434
```

## Model Recommendations

| Task | Recommended Model | Size | Speed |
|------|------------------|------|-------|
| Web Research (Hermes) | `llama3.2` | 3B | ⚡⚡⚡ |
| Web Research (Better) | `mistral` | 7B | ⚡⚡ |
| Script Writing | OpenRouter `owl-alpha` | N/A | ⚡⚡ |
| Analysis | OpenRouter `owl-alpha` | N/A | ⚡⚡ |

## Cost Comparison

| Provider | Cost per 1K tokens | Monthly Estimate* |
|----------|-------------------|-------------------|
| Ollama (Local) | $0.00 | $0.00 |
| OpenRouter (Owl-Alpha) | $0.00 (free tier) | $0.00 |
| OpenRouter (Premium) | ~$0.0001 | ~$5-10 |

*Estimate based on 1000 research queries + 500 script generations per month

## Next Steps

1. ✅ Install Ollama and pull `llama3.2`
2. ✅ Update `.env` with Ollama settings
3. ✅ Add secrets to GitHub Actions
4. ✅ Test with a small research task
5. ✅ Monitor logs for JSON parsing issues
