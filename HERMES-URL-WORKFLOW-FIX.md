# ✅ HERMES URL-BASED WORKFLOW - FIXED

## Problem Solved
Hermes (local Ollama model) was failing to output proper JSON, causing the pipeline to fail with "AI did not return valid platform/query" errors.

## Solution: URL-Only Approach

### Key Changes

#### 1. **Hermes Research Step** (`github-action-runner.js` line 431-473)
- **OLD**: Asked Hermes to return complex JSON with platform, country, title, url, type
- **NEW**: Asks Hermes to find 3-5 raw URLs from different countries
- **Prompt**: "Find 3-5 viral video URLs... Return ONLY the URL in this format: URL: https://..."
- **Extraction**: Uses regex to pull URLs from whatever text Hermes outputs
- **Storage**: Saves URLs to `memory['trending-urls']` for later use

#### 2. **Clip Creation** (`github-action-runner.js` line 273-380)
- **First**: Tries to download URLs from Hermes research using `UniversalDownloader`
- **Fallback**: If Hermes URLs fail, asks Hermes again for a single URL
- **Ultimate Fallback**: Random platform + query selection
- **Benefit**: Direct URL download is more reliable than search-based discovery

#### 3. **Explain Pipeline** (`explainer/explain-pipeline.js` line 57-133)
- **Step 1**: Download from provided URL if available
- **Step 2**: If no URL, ask Hermes to find matching content for the script topic
- **Step 3**: Fallback to free visual generation/search
- **Benefit**: Hermes finds relevant viral content that matches the AI-written script

## Workflow Summary

```
┌─────────────────────────────────────────────────────────────┐
│  DAILY PIPELINE                                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  STEP 1: Hermes Research (Ollama)                           │
│  ├─ Prompt: "Find 3-5 viral video URLs from different       │
│  │         countries"                                        │
│  ├─ Output: Raw URLs extracted via regex                    │
│  └─ Store: memory['trending-urls'] = [url1, url2, ...]      │
│                                                              │
│  STEP 2: Clip Creation                                      │
│  ├─ Try: Download from Hermes URLs                          │
│  ├─ Else: Ask Hermes for one URL → Download                 │
│  └─ Else: Random platform search → Download                 │
│                                                              │
│  STEP 3: Explainer Creation                                 │
│  ├─ OpenRouter: Write script (analysis task)                │
│  ├─ Hermes (Ollama): Find matching video URL                │
│  ├─ Download: Use UniversalDownloader                       │
│  └─ Compile: Audio + Video → Final Short                    │
│                                                              │
└─────────────────────────────────────────────────────────────┘

MODEL SEPARATION:
├─ Hermes (Local Ollama): Web research, URL finding, downloading
└─ OpenRouter: Script writing, analysis, decision making
```

## Benefits

1. **No JSON Parsing Issues**: Regex URL extraction works even if Hermes talks too much
2. **Simpler Prompts**: "Just give me the URL" is easier for small models
3. **Direct Downloads**: URLs → UniversalDownloader is more reliable than search
4. **Graceful Fallbacks**: Multiple fallback levels ensure pipeline always completes
5. **Model Separation**: Local Ollama for research, OpenRouter for analysis

## Testing

Run the daily workflow:
```bash
node core/github-action-runner.js --mode daily
```

Expected log output:
```
[GHAction] ℹ️  Step 1: Hermes researching global trends...
[GHAction] ℹ️  Asking Hermes to find trending content with URLs...
[GHAction] ✅ Extracted 3 URLs from Hermes research
[GHAction] ℹ️  Found 3 trending URLs: https://bilibili.com/..., https://tiktok.com/...
[GHAction] ℹ️  Step 2: Downloading trending clip...
[GHAction] ℹ️  Using 3 URLs from Hermes research...
[GHAction] ℹ️  Attempting to download: https://bilibili.com/video/BV1...
[GHAction] ✅ Downloaded video from bilibili: ./output/temp/video.mp4
```

## Files Modified

1. `/workspace/core/github-action-runner.js`
   - Line 431-473: Hermes research with URL extraction
   - Line 273-380: Clip creation with URL-first approach

2. `/workspace/explainer/explain-pipeline.js`
   - Line 57-133: Visual asset generation with Hermes URL search

## Environment Variables Required

```bash
# For Hermes (Local Ollama)
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:latest  # or your preferred model

# For OpenRouter (Analysis/Scripts)
OPENROUTER_API_KEY=your_key_here

# For CamoFox (Web Scraping)
CAMOFOX_URL=http://localhost:9377
```
