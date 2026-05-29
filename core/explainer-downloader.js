/**
 * Explainer Downloader — YouTube Search + Download for Footage Sourcing
 * 
 * Searches YouTube for compilation/raw footage matching storyboard clip
 * descriptions. Downloads candidates and returns metadata for QA.
 * 
 * Loop (per clip):
 *   1. Search YouTube with queries from Sourcing Agent
 *   2. Get metadata for top results
 *   3. Download the best candidate
 *   4. Return to pipeline for QA review
 *   5. If rejected → try fallback queries (max 3 retries)
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('ExplainerDownloader');

/**
 * Search YouTube for a query, return matching video metadata
 */
function searchYouTube(query, maxResults = 5) {
  try {
    const cmd = `yt-dlp --flat-playlist --dump-json "ytsearch${maxResults}:${query}" 2>/dev/null`;
    const out = execSync(cmd, { timeout: 30000, maxBuffer: 5 * 1024 * 1024 }).toString().trim();
    if (!out) return [];

    return out.split('\n').filter(Boolean).map(line => {
      try {
        const p = JSON.parse(line);
        return {
          id: p.id,
          url: `https://www.youtube.com/watch?v=${p.id}`,
          title: p.title || 'YouTube video',
          duration: p.duration || 0,
          view_count: p.view_count || 0,
          channel: p.channel || p.uploader || 'Unknown',
          description: (p.description || '').substring(0, 300),
        };
      } catch { return null; }
    }).filter(Boolean);
  } catch (e) {
    logger.warn(`Search failed for "${query}": ${e.message.substring(0, 60)}`);
    return [];
  }
}

/**
 * Search with multiple queries, dedup results
 */
function searchWithQueries(queries, maxTotal = 5) {
  const allResults = [];
  const seen = new Set();

  for (const query of queries.slice(0, 3)) {
    const results = searchYouTube(query, maxTotal);
    for (const r of results) {
      if (!seen.has(r.id) && allResults.length < maxTotal) {
        // Prefer shorter videos (compilations under 30s are rare, so accept up to 10 min)
        if (r.duration > 0 && r.duration < 600) {
          seen.add(r.id);
          allResults.push(r);
        }
      }
    }
    if (allResults.length >= maxTotal) break;
  }

  // Sort by: shorter = better (closer to our need), then by views
  allResults.sort((a, b) => {
    const aScore = (a.duration < 60 ? 50 : 0) + Math.min(a.view_count || 0, 1000000) / 100000;
    const bScore = (b.duration < 60 ? 50 : 0) + Math.min(b.view_count || 0, 1000000) / 100000;
    return bScore - aScore;
  });

  return allResults;
}

/**
 * Download a video by URL
 * @param {string} url - YouTube URL
 * @param {string} outputDir - Where to save
 * @returns {string|null} - Path to downloaded file
 */
function downloadVideo(url, outputDir) {
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });

  const outputFile = path.join(outputDir, `source_${Date.now()}_%(id)s.%(ext)s`);

  const strategies = [
    { name: 'default', format: '-f "bestvideo[height<=720]+bestaudio/best" --merge-output-format mp4' },
    { name: 'android', args: '--extractor-args "youtube:player_client=android"', format: '-f "best"' },
  ];

  for (const s of strategies) {
    try {
      const cookieArg = fs.existsSync('/tmp/yt_cookies.txt') ? '--cookies "/tmp/yt_cookies.txt"' : '';
      const cmd = `yt-dlp ${cookieArg} ${s.args || ''} ${s.format} ` +
        `-o "${outputFile}" "${url}" ` +
        `--no-playlist --max-filesize 200M --socket-timeout 30 --retries 2 --force-ipv4`;
      
      execSync(cmd, { timeout: 180000, maxBuffer: 200 * 1024 * 1024 });

      const files = fs.readdirSync(outputDir)
        .filter(f => (f.endsWith('.mp4') || f.endsWith('.webm')) && fs.statSync(path.join(outputDir, f)).size > 50000)
        .sort((a, b) => fs.statSync(path.join(outputDir, b)).mtimeMs - fs.statSync(path.join(outputDir, a)).mtimeMs);

      if (files.length > 0) {
        const fp = path.join(outputDir, files[0]);
        logger.success(`Downloaded: ${(fs.statSync(fp).size / 1024 / 1024).toFixed(1)}MB — ${files[0]}`);
        return fp;
      }
    } catch (e) {
      logger.warn(`Download ${s.name} failed: ${e.message.substring(0, 60)}`);
    }
  }

  return null;
}

/**
 * Get video metadata for QA (duration, dimensions)
 */
function getVideoMetadata(videoPath) {
  try {
    const out = execSync(
      `ffprobe -v error -select_streams v:0 -show_entries stream=width,height,duration -of csv=p=0 "${videoPath}" 2>/dev/null`,
      { timeout: 10000, encoding: 'utf8' }
    ).trim();
    const parts = out.split(',').map(s => parseFloat(s.trim()));
    if (parts.length >= 2) {
      return { width: parts[0], height: parts[1], duration: parts[2] || 30 };
    }
  } catch {}
  return { width: 0, height: 0, duration: 30 };
}

/**
 * Slice a segment from a compilation video using FFmpeg
 * @param {string} inputPath - Source compilation file
 * @param {string} outputPath - Where to save the sliced clip
 * @param {string} startTime - Start timestamp (HH:MM:SS.mm)
 * @param {string} endTime - End timestamp
 * @returns {string|null} - Path to sliced file
 */
function sliceClip(inputPath, outputPath, startTime, endTime) {
  if (!fs.existsSync(inputPath)) return null;

  logger.info(`Slicing: ${path.basename(inputPath)} from ${startTime} to ${endTime}`);

  try {
    execSync(
      `ffmpeg -y -ss ${startTime} -i "${inputPath}" -to ${endTime} ` +
      `-c:v libx264 -preset fast -crf 18 -c:a aac -b:a 192k ` +
      `-pix_fmt yuv420p "${outputPath}" 2>/dev/null`,
      { timeout: 120000 }
    );

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 50000) {
      logger.success(`Sliced: ${outputPath.split(/[\\/]/).pop()} (${startTime} → ${endTime})`);
      return outputPath;
    }
  } catch (e) {
    logger.warn(`Slicing failed: ${e.message.substring(0, 60)}`);
  }

  return null;
}

module.exports = { searchYouTube, searchWithQueries, downloadVideo, getVideoMetadata, sliceClip };