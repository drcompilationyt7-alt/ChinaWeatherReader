# HERMES AGENT CONFIGURATION SUMMARY

## Overview
Your system is now configured with the following architecture:

### 🤖 Hermes Agent (Local Ollama)
- **Purpose**: Web research, content discovery, finding trending videos
- **Model**: Uses local Ollama (llama3.2 by default)
- **Configuration**: Set via `OLLAMA_HOST` and `HERMES_OLLAMA_MODEL` environment variables
- **Files Modified**: 
  - `/workspace/hermes-agent/hermes-cli-wrapper.js`

### 🧠 OpenRouter (Analysis & Script Creation)
- **Purpose**: Script writing, analysis, explainers, content creation
- **Model**: Uses OpenRouter API for high-quality text generation
- **Configuration**: Set via `OPENROUTER_API_KEY` environment variable
- **Files Using OpenRouter**:
  - `/workspace/explainer/explain-pipeline.js` (script generation)
  - `/workspace/core/ai-service.js` (general AI tasks)

---

## Key Changes Made

### 1. Enhanced JSON Parsing for Hermes/Ollama Output
**File**: `core/github-action-runner.js`

**Problem**: Ollama models sometimes return JSON wrapped in markdown or extra text, causing parsing failures.

**Solution**: Implemented multi-strategy JSON extraction:
- Strategy 1: Look for JSON with required fields (`platform` + `query`)
- Strategy 2: Extract any JSON object between curly braces
- Strategy 3: Parse entire output as JSON

**Code Location**: Lines 307-335 in `_createClipShort()`

### 2. Hermes Content Discovery for Explainer Pipeline
**File**: `core/github-action-runner.js`

**New Feature**: Before creating an explainer video, Hermes now:
1. Searches for specific viral content matching the topic
2. Returns platform, query, and direct URL if found
3. Passes this information to the explain pipeline

**Code Location**: Lines 515-542

### 3. Explain Pipeline Video Download
**File**: `explainer/explain-pipeline.js`

**New Feature**: When Hermes finds a specific video URL:
1. Pipeline attempts to download it using `UniversalDownloader`
2. If successful, uses the downloaded video as visual asset
3. Falls back to free stock footage if download fails

**Code Location**: Lines 57-81 in `processExplain()`

### 4. Better Logging
**Files**: Both `hermes-cli-wrapper.js` and `github-action-runner.js`

**Improvements**:
- Logs when output contains JSON structure
- Shows raw output preview for debugging
- Reports which JSON extraction strategy succeeded
- Logs Hermes-found URLs before downloading

---

## Workflow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    DAILY PIPELINE                           │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┴──────────────────────┐
        │                                            │
        ▼                                            ▼
┌───────────────────┐                    ┌─────────────────────┐
│  STEP 1: CLIP     │                    │  STEP 2: EXPLAINER  │
│  (Trending Video) │                    │  (Script + Visual)  │
└───────────────────┘                    └─────────────────────┘
        │                                            │
        ▼                                            ▼
┌───────────────────┐                    ┌─────────────────────┐
│ Hermes (Ollama)   │                    │ Hermes (Ollama)     │
│ Decides platform  │                    │ Finds content       │
│ & search query    │                    │ Returns URL         │
└───────────────────┘                    └─────────────────────┘
        │                                            │
        ▼                                            ▼
┌───────────────────┐                    ┌─────────────────────┐
│ TrendingVideoFinder│                   │ Explain Pipeline    │
│ Scrapes platform  │                    │ OpenRouter writes   │
│ Downloads video   │                    │ script              │
└───────────────────┘                    └─────────────────────┘
                                                  │
                                                  ▼
                                         ┌─────────────────────┐
                                         │ If URL found:       │
                                         │ UniversalDownloader │
                                         │ downloads it        │
                                         └─────────────────────┘
                                                  │
                                                  ▼
                                         ┌─────────────────────┐
                                         │ Compile video with  │
                                         │ audio + visuals     │
                                         └─────────────────────┘
```

---

## Environment Variables Required

```bash
# For Hermes (Local Ollama)
OLLAMA_HOST=http://localhost:11434
HERMES_OLLAMA_MODEL=llama3.2  # or your preferred model
OLLAMA_MODEL=llama3.2

# For OpenRouter (Analysis/Scripts)
OPENROUTER_API_KEY=your_key_here

# For web scraping
CAMOFOX_URL=http://localhost:9377  # if using CamoFox browser
```

---

## Error Handling Improvements

### JSON Parsing Errors
- Multiple fallback strategies ensure Hermes responses are parsed correctly
- Detailed logging shows what went wrong
- Graceful fallback to random selection if AI fails completely

### Video Download Errors
- If Hermes returns a URL but download fails, pipeline falls back to:
  1. FreeVisualSearcher (Pexels, Pixabay, YouTube clips)
  2. Text-overlay video if no visuals available

### Model Availability
- Hermes CLI checks for Ollama availability
- Falls back to built-in JS agent if CLI unavailable
- OpenRouter calls have try-catch with fallback scripts

---

## Testing Recommendations

1. **Test Hermes alone**:
   ```bash
   node -e "const {HermesCLIWrapper} = require('./hermes-agent/hermes-cli-wrapper'); const h = new HermesCLIWrapper(); h.run('Find trending videos on TikTok Japan').then(console.log);"
   ```

2. **Test clip creation**:
   ```bash
   node -e "const runner = require('./core/github-action-runner'); const r = new runner(); r.initialize().then(() => r._createClipShort()).then(console.log);"
   ```

3. **Test explainer with Hermes content**:
   Run the daily pipeline and check logs for:
   - "Hermes found:" messages
   - "Downloaded video from Hermes" success messages
   - JSON extraction strategy used

---

## Troubleshooting

### "AI did not return valid platform/query"
- Check Ollama is running: `curl http://localhost:11434/api/tags`
- Verify model is available: `ollama list`
- Check Hermes verbose logs for raw output

### Video download fails
- Ensure yt-dlp is installed: `yt-dlp --version`
- Check network connectivity to target platform
- Some platforms may block automated downloads

### JSON parsing still fails
- Enable verbose logging: set `HERMES_VERBOSE=1`
- Check raw output in logs
- May need to adjust prompt to be more strict about JSON-only response

---

## Next Steps

1. Ensure Ollama is running with desired model
2. Test the pipeline end-to-end
3. Monitor logs for JSON parsing success rates
4. Adjust prompts if needed based on actual Ollama output patterns
