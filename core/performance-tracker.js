/**
 * Performance Tracker — the channel's own feedback loop.
 *
 * Every daily run:
 *   1. pulls the channel's uploads + public stats from the YouTube Data API
 *      (views / likes / comments; YouTube Analytics watch-time metrics are
 *      added automatically when the OAuth token has the analytics scope)
 *   2. joins them with what we know about each post (country, source channel,
 *      easter eggs, ...) from memory/posted-videos.json
 *   3. scores every video by "views per day in its first 2 weeks", then
 *      distils that into insights:
 *        - which countries / source channels over- or under-perform
 *        - which title patterns lift performance (emoji, POV:, questions, ...)
 *        - the best and worst titles as concrete examples
 *   4. saves memory/performance-stats.json + memory/performance-insights.json
 *
 * The insights are then used in two places:
 *   - gemini-service.generateTitle(): the summary goes into the prompt and
 *     Gemini returns several title options
 *   - rankTitles(): the options are ranked by a k-NN prediction over past
 *     titles (sentence-transformers MiniLM embeddings via
 *     core/title-similarity.py, with a pure-Python fallback) blended with the
 *     pattern heuristics, and the pipeline keeps the best one
 *   - pickBestCandidate() in the type-1 pipeline uses the country / channel
 *     weights to break ties between candidate clips
 *
 * Everything here is free and runs on a GitHub Actions runner.
 *
 * CLI:
 *   node core/performance-tracker.js --sync     # fetch + recompute + save
 *   node core/performance-tracker.js --report   # print the current insights
 */
const path = require('path');
const fs = require('fs');
const { execFileSync } = require('child_process');
const { Logger } = require('./logger');

const logger = new Logger('Performance');

const MEMORY_DIR = path.join(__dirname, '..', 'memory');
const STATS_FILE = path.join(MEMORY_DIR, 'performance-stats.json');
const INSIGHTS_FILE = path.join(MEMORY_DIR, 'performance-insights.json');
const POSTED_FILE = path.join(MEMORY_DIR, 'posted-videos.json');
const SIMILARITY_SCRIPT = path.join(__dirname, 'title-similarity.py');

const VELOCITY_WINDOW_DAYS = 14;   // views/day is measured over at most the first 14 days
const MIN_AGE_HOURS = 36;          // younger videos have too little data (first-day spikes)
const MIN_SAMPLE = 8;              // below this we don't trust the insights
const MIN_GROUP_N = 3;             // a country / channel needs this many videos before its weight applies
const MIN_PATTERN_N = 5;           // a title pattern needs this many videos on both sides
const SMOOTHING_K = 3;             // Bayesian prior weight for per-country / per-channel means
// All comparisons use log1p(score) so one viral outlier cannot dominate the
// averages on a small channel; raw views / velocity are kept for display.

// ─── helpers ──────────────────────────────────────────────────────

function readJson(file, dflt) {
  try { if (fs.existsSync(file)) return JSON.parse(fs.readFileSync(file, 'utf8')); } catch {}
  return dflt;
}

function writeJson(file, data) {
  if (!fs.existsSync(path.dirname(file))) fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(data, null, 2));
}

function videoIdFromUrl(url) {
  const m = String(url || '').match(/(?:v=|shorts\/|youtu\.be\/)([A-Za-z0-9_-]{11})/);
  return m ? m[1] : null;
}

function parseIsoDuration(iso) {
  const m = String(iso || '').match(/PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?/);
  if (!m) return 0;
  return (parseInt(m[1] || 0, 10) * 3600) + (parseInt(m[2] || 0, 10) * 60) + parseInt(m[3] || 0, 10);
}

function mean(arr) { return arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0; }

const EMOJI_RE = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{1F1E6}-\u{1F1FF}]/u;
const COUNTRY_WORDS = ['china', 'chinese', 'japan', 'japanese', 'korea', 'korean', 'thailand', 'thai', 'vietnam', 'india', 'indian', 'indonesia', 'philippines', 'filipino', 'malaysia', 'taiwan', 'hong kong', 'singapore', 'tokyo', 'seoul', 'bangkok', 'asia', 'asian'];

/** Title features used for the pattern analysis and the heuristic score. */
const TITLE_FEATURES = {
  'short (<= 40 chars)': t => t.length <= 40,
  'has emoji': t => EMOJI_RE.test(t),
  'question': t => /\?/.test(t),
  'meme opener (POV / When / Bro / Me when / Wait)': t => /^(pov|when|bro|me when|wait|nobody|this)\b/i.test(t.trim()),
  'has number': t => /\d/.test(t),
  'ALL CAPS word': t => /\b[A-Z]{3,}\b/.test(t),
  'mentions place': t => COUNTRY_WORDS.some(w => t.toLowerCase().includes(w)),
  'ellipsis / cliffhanger': t => /(\.\.\.|…)/.test(t),
  'quoted words': t => /["“”]/.test(t),
};

// ─── 1. Fetch ─────────────────────────────────────────────────────

function normalizeVideo(v) {
  return {
    videoId: v.id,
    title: v.snippet?.title || '',
    publishedAt: v.snippet?.publishedAt || null,
    tags: v.snippet?.tags || [],
    privacyStatus: v.status?.privacyStatus || 'unknown',
    durationSec: parseIsoDuration(v.contentDetails?.duration),
    views: parseInt(v.statistics?.viewCount || 0, 10),
    likes: parseInt(v.statistics?.likeCount || 0, 10),
    comments: parseInt(v.statistics?.commentCount || 0, 10),
  };
}

/**
 * All uploads of the authenticated channel with public statistics.
 * @param {Object} bridge  initialized YouTubeBridge (uses its googleapis client)
 */
async function fetchChannelVideos(bridge, max = 300) {
  const yt = bridge && bridge.youtube;
  if (!yt) throw new Error('YouTube bridge not authenticated');

  const ch = await yt.channels.list({ part: ['contentDetails', 'statistics', 'snippet'], mine: true });
  const channel = ch.data.items && ch.data.items[0];
  const uploads = channel && channel.contentDetails && channel.contentDetails.relatedPlaylists && channel.contentDetails.relatedPlaylists.uploads;
  if (!uploads) throw new Error('Uploads playlist not found');

  const ids = [];
  let pageToken;
  do {
    const r = await yt.playlistItems.list({ part: ['contentDetails'], playlistId: uploads, maxResults: 50, pageToken });
    for (const it of r.data.items || []) if (it.contentDetails && it.contentDetails.videoId) ids.push(it.contentDetails.videoId);
    pageToken = r.data.nextPageToken;
  } while (pageToken && ids.length < max);

  const videos = [];
  for (let i = 0; i < ids.length; i += 50) {
    const r = await yt.videos.list({ part: ['snippet', 'statistics', 'status', 'contentDetails'], id: ids.slice(i, i + 50).join(','), maxResults: 50 });
    for (const v of r.data.items || []) videos.push(normalizeVideo(v));
  }

  return {
    channel: {
      title: channel.snippet && channel.snippet.title,
      subscribers: parseInt((channel.statistics && channel.statistics.subscriberCount) || 0, 10),
      totalViews: parseInt((channel.statistics && channel.statistics.viewCount) || 0, 10),
      totalVideos: parseInt((channel.statistics && channel.statistics.videoCount) || 0, 10),
    },
    videos,
  };
}

/**
 * Watch-time metrics from the YouTube Analytics API. Needs the
 * yt-analytics.readonly scope on the OAuth token; returns null otherwise.
 */
async function tryFetchAnalytics(bridge, videoIds) {
  if (!bridge || !bridge.oauth2Client || videoIds.length === 0) return null;
  try {
    const { google } = require('googleapis');
    const ya = google.youtubeAnalytics({ version: 'v2', auth: bridge.oauth2Client });
    const end = new Date();
    const start = new Date(end.getTime() - 120 * 86400000);
    const fmt = d => d.toISOString().slice(0, 10);
    const out = {};
    for (let i = 0; i < videoIds.length; i += 50) {
      const batch = videoIds.slice(i, i + 50);
      const r = await ya.reports.query({
        ids: 'channel==MINE',
        startDate: fmt(start),
        endDate: fmt(end),
        metrics: 'views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,shares,subscribersGained',
        dimensions: 'video',
        filters: `video==${batch.join(',')}`,
        maxResults: 50,
      });
      const cols = (r.data.columnHeaders || []).map(c => c.name);
      for (const row of r.data.rows || []) {
        const rec = {};
        cols.forEach((c, k) => { rec[c] = row[k]; });
        out[rec.video] = {
          minutesWatched: rec.estimatedMinutesWatched || 0,
          avgViewDurationSec: rec.averageViewDuration || 0,
          avgViewPct: rec.averageViewPercentage || 0,
          shares: rec.shares || 0,
          subscribersGained: rec.subscribersGained || 0,
        };
      }
    }
    logger.success(`YouTube Analytics: watch-time metrics for ${Object.keys(out).length} videos`);
    return out;
  } catch (e) {
    logger.info(`YouTube Analytics API not available (${(e.message || '').substring(0, 60)}) — using public stats only`);
    return null;
  }
}

// ─── 2. Join with our own bookkeeping ─────────────────────────────

function loadPostedIndex() {
  const posted = readJson(POSTED_FILE, { videos: [] });
  const idx = {};
  for (const p of posted.videos || []) {
    const id = videoIdFromUrl(p.url);
    if (id) idx[id] = p;
  }
  return idx;
}

// ─── 3. Score + insights ──────────────────────────────────────────

function scoreVideo(v, now = Date.now()) {
  const published = v.publishedAt ? new Date(v.publishedAt).getTime() : now;
  const ageDays = Math.max(0.02, (now - published) / 86400000);
  const window = Math.min(Math.max(ageDays, 0.5), VELOCITY_WINDOW_DAYS);
  const velocity = v.views / window;
  const engagement = v.views > 0 ? (v.likes * 2 + v.comments * 5) / Math.max(v.views, 50) : 0;
  // watch-time bonus when analytics are available (avgViewPct 0-100+)
  const retention = v.analytics && v.analytics.avgViewPct ? Math.min(1.5, v.analytics.avgViewPct / 80) : 1;
  const score = velocity * (1 + Math.min(engagement, 0.25)) * retention;
  return {
    ageDays: Math.round(ageDays * 10) / 10,
    velocity: Math.round(velocity * 10) / 10,
    engagement: Math.round(engagement * 1000) / 1000,
    score: Math.round(score * 10) / 10,
    lscore: Math.round(Math.log1p(score) * 1000) / 1000,
  };
}

/** Per-group geometric-mean performance relative to the channel (weight 0.6..1.6). */
function groupStats(videos, keyFn, globalLogMean) {
  const groups = {};
  for (const v of videos) {
    const key = keyFn(v);
    if (!key) continue;
    if (!groups[key]) groups[key] = { n: 0, sum: 0, lsum: 0 };
    groups[key].n++;
    groups[key].sum += v.score;
    groups[key].lsum += v.lscore;
  }
  const out = {};
  for (const [key, g] of Object.entries(groups)) {
    const smoothedLog = (g.lsum + SMOOTHING_K * globalLogMean) / (g.n + SMOOTHING_K);
    const ratio = Math.exp(smoothedLog - globalLogMean);
    out[key] = {
      n: g.n,
      meanScore: Math.round((g.sum / g.n) * 10) / 10,
      weight: g.n >= MIN_GROUP_N ? Math.round(Math.min(1.6, Math.max(0.6, ratio)) * 100) / 100 : 1,
    };
  }
  return out;
}

/** Lift of each title feature = geometric mean with the feature / without it. */
function titlePatterns(videos) {
  const patterns = [];
  for (const [name, test] of Object.entries(TITLE_FEATURES)) {
    const withF = videos.filter(v => test(v.title));
    const without = videos.filter(v => !test(v.title));
    if (withF.length < MIN_PATTERN_N || without.length < MIN_PATTERN_N) continue;
    const lift = Math.exp(mean(withF.map(v => v.lscore)) - mean(without.map(v => v.lscore)));
    patterns.push({ name, n: withF.length, lift: Math.round(Math.min(3, Math.max(0.33, lift)) * 100) / 100 });
  }
  return patterns.sort((a, b) => b.lift - a.lift);
}

function buildPromptSummary(ins) {
  if (!ins || ins.sampleSize < MIN_SAMPLE) return '';
  const lines = [];
  const small = ins.sampleSize < 30 || (ins.channel && ins.channel.totalViews < 20000);
  lines.push(`Based on ${ins.sampleSize} of our own recent shorts (score = views per day in the first 2 weeks, typical short gets ${ins.typicalScore}/day)${small ? ' — small channel so far, treat these as hints not rules' : ''}:`);
  if (ins.topTitles.length) {
    lines.push('Titles that performed BEST for us:');
    for (const t of ins.topTitles.slice(0, 6)) lines.push(`  - "${t.title}" (${t.views.toLocaleString()} views, ${t.country || 'unknown'})`);
  }
  if (ins.bottomTitles.length) {
    lines.push('Titles that performed WORST for us:');
    for (const t of ins.bottomTitles.slice(0, 3)) lines.push(`  - "${t.title}" (${t.views.toLocaleString()} views)`);
  }
  const good = ins.titlePatterns.filter(p => p.lift >= 1.15);
  const bad = ins.titlePatterns.filter(p => p.lift <= 0.85);
  if (good.length) lines.push(`Patterns that WORK for this channel: ${good.map(p => `${p.name} (x${p.lift})`).join(', ')}`);
  if (bad.length) lines.push(`Patterns that UNDER-perform: ${bad.map(p => `${p.name} (x${p.lift})`).join(', ')}`);
  const countries = Object.entries(ins.countries).filter(([, s]) => s.n >= MIN_GROUP_N).sort((a, b) => b[1].weight - a[1].weight);
  if (countries.length >= 2) {
    lines.push(`Best performing countries: ${countries.slice(0, 3).map(([c, s]) => `${c} (x${s.weight}, ${s.n} shorts)`).join(', ')}; weakest: ${countries.slice(-2).map(([c, s]) => `${c} (x${s.weight})`).join(', ')}`);
  }
  return lines.join('\n');
}

function computeInsights(videos, channel = null, now = Date.now()) {
  const posted = loadPostedIndex();
  const usable = [];
  for (const v of videos) {
    if (v.privacyStatus !== 'public') continue;
    const ageHours = v.publishedAt ? (now - new Date(v.publishedAt).getTime()) / 3600000 : 0;
    if (ageHours < MIN_AGE_HOURS) continue;
    const p = posted[v.videoId] || {};
    const scored = scoreVideo(v, now);
    usable.push({
      videoId: v.videoId,
      title: v.title,
      publishedAt: v.publishedAt,
      views: v.views, likes: v.likes, comments: v.comments,
      durationSec: v.durationSec,
      analytics: v.analytics || null,
      country: p.country || null,
      sourceChannel: p.sourceChannel || null,
      category: p.category || null,
      eggs: p.eggs || null,
      ...scored,
    });
  }
  usable.sort((a, b) => b.score - a.score);
  const globalMean = mean(usable.map(v => v.score));
  const globalLogMean = mean(usable.map(v => v.lscore));
  const typicalScore = Math.round(Math.expm1(globalLogMean) * 10) / 10;   // geometric mean = "typical" short
  const pick = v => ({ videoId: v.videoId, title: v.title, views: v.views, score: v.score, country: v.country, ageDays: v.ageDays });

  const insights = {
    updatedAt: new Date(now).toISOString(),
    channel: channel || null,
    sampleSize: usable.length,
    reliable: usable.length >= MIN_SAMPLE,
    globalMeanScore: Math.round(globalMean * 10) / 10,
    typicalScore,
    topTitles: usable.slice(0, 10).map(pick),
    bottomTitles: usable.slice(-5).reverse().map(pick),
    titlePatterns: titlePatterns(usable),
    countries: groupStats(usable, v => v.country, globalLogMean),
    channels: groupStats(usable, v => v.sourceChannel, globalLogMean),
    categories: groupStats(usable, v => v.category, globalLogMean),
    eggs: groupStats(usable.filter(v => Array.isArray(v.eggs)), v => (v.eggs.length ? 'with eggs' : 'no eggs'), globalLogMean),
    // k-NN targets are log scores so a single outlier cannot dominate
    history: usable.map(v => ({ title: v.title, score: v.lscore })),
  };
  insights.promptSummary = buildPromptSummary(insights);
  insights.summaryLine = usable.length
    ? `Best recent: "${usable[0].title}" (${usable[0].views.toLocaleString()} views, ${usable[0].score}/day) — typical short ${typicalScore}/day over ${usable.length} shorts`
    : 'No public shorts with stats yet';
  return { insights, scoredVideos: usable };
}

// ─── 4. Sync (the daily entry point) ──────────────────────────────

async function syncPerformance(bridge) {
  if (!bridge || !bridge.isAuthenticated || !bridge.isAuthenticated()) {
    logger.warn('YouTube not authenticated — performance sync skipped');
    return loadInsights();
  }
  const { channel, videos } = await fetchChannelVideos(bridge);
  logger.info(`Fetched ${videos.length} uploads for ${channel.title || 'channel'} (${channel.subscribers} subs, ${channel.totalViews.toLocaleString()} views)`);
  const analytics = await tryFetchAnalytics(bridge, videos.map(v => v.videoId));
  if (analytics) for (const v of videos) if (analytics[v.videoId]) v.analytics = analytics[v.videoId];

  const { insights, scoredVideos } = computeInsights(videos, channel);
  writeJson(STATS_FILE, { updatedAt: insights.updatedAt, channel, videos: scoredVideos });
  writeJson(INSIGHTS_FILE, insights);
  logger.success(`Insights: ${insights.sampleSize} scored shorts, typical ${insights.typicalScore} views/day${insights.reliable ? '' : ' (not enough data yet — insights not applied)'}`);
  if (insights.topTitles[0]) logger.info(`Top: "${insights.topTitles[0].title}" ${insights.topTitles[0].views} views`);
  return insights;
}

function loadInsights() {
  const ins = readJson(INSIGHTS_FILE, null);
  return ins && ins.sampleSize >= 0 ? ins : null;
}

function loadStats() { return readJson(STATS_FILE, null); }

// ─── 5. Using the insights ────────────────────────────────────────

function countryWeight(insights, country) {
  if (!insights || !insights.reliable || !country) return 1;
  const direct = insights.countries[country];
  if (direct) return direct.weight;
  const key = Object.keys(insights.countries).find(k => k.toLowerCase() === String(country).toLowerCase());
  return key ? insights.countries[key].weight : 1;
}

function channelWeight(insights, handle) {
  if (!insights || !insights.reliable || !handle) return 1;
  const h = String(handle).replace(/^@/, '').toLowerCase();
  const key = Object.keys(insights.channels).find(k => k.replace(/^@/, '').toLowerCase() === h);
  return key ? insights.channels[key].weight : 1;
}

function categoryWeight(insights, category) {
  if (!insights || !insights.reliable || !category || !insights.categories) return 1;
  const c = String(category).toLowerCase();
  const key = Object.keys(insights.categories).find(k => k.toLowerCase() === c);
  return key ? insights.categories[key].weight : 1;
}

/** Source titles / summaries of everything already posted (for near-duplicate checks). */
function postedDedupList(limit = 400) {
  const posted = readJson(POSTED_FILE, { videos: [] });
  const out = [];
  for (const p of (posted.videos || []).slice(-limit)) {
    if (p.sourceTitle) out.push(String(p.sourceTitle));
    if (p.summary) out.push(String(p.summary));
    if (!p.sourceTitle && !p.summary && p.title) out.push(String(p.title));
  }
  return out;
}

/** Heuristic 0..1 score from the learned title patterns. */
function scoreTitleHeuristic(title, insights) {
  if (!title) return 0;
  if (!insights || !insights.reliable || !insights.titlePatterns.length) return 0.5;
  let acc = 0, n = 0;
  for (const p of insights.titlePatterns) {
    const test = TITLE_FEATURES[p.name];
    if (!test) continue;
    const has = test(title);
    // reward having good patterns and lacking bad ones
    acc += has ? (p.lift - 1) : (1 - p.lift) * 0.5;
    n++;
  }
  const raw = n ? acc / n : 0;                   // roughly -0.5 .. +0.5
  return Math.max(0, Math.min(1, 0.5 + raw));
}

function pythonCommand() {
  for (const cmd of [process.env.PYTHON_BIN, 'python3', 'python'].filter(Boolean)) {
    try { execFileSync(cmd, ['-c', 'import sys'], { stdio: 'ignore', timeout: 10000 }); return cmd; } catch {}
  }
  return null;
}

/**
 * k-NN prediction of the score for each candidate text (embedding similarity
 * to past titles). `dedup` is an optional list of already-posted source
 * titles / summaries; each prediction then carries dupSim / dupOf.
 */
function predictTitleScores(candidates, insights, dedup = null) {
  if (!fs.existsSync(SIMILARITY_SCRIPT)) return null;
  const history = insights && insights.reliable ? insights.history : [];
  if (history.length === 0 && !(Array.isArray(dedup) && dedup.length)) return null;
  const py = pythonCommand();
  if (!py) return null;
  try {
    const input = JSON.stringify({ history, candidates, dedup: Array.isArray(dedup) ? dedup : [] });
    const out = execFileSync(py, [SIMILARITY_SCRIPT], { input, encoding: 'utf8', timeout: 180000, maxBuffer: 16 * 1024 * 1024, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    const parsed = JSON.parse(out.trim().split('\n').pop());
    if (parsed && Array.isArray(parsed.predictions)) {
      logger.info(`Title similarity backend: ${parsed.backend}`);
      return parsed;
    }
  } catch (e) {
    logger.warn(`Title similarity failed: ${(e.message || '').substring(0, 80)}`);
  }
  return null;
}

/**
 * Rank candidate titles. Returns [{ title, score, heuristic, predicted, extra }] best first.
 * Components (each 0..1, averaged over the ones available):
 *   heuristic  learned title patterns            (needs reliable insights)
 *   knn        similarity-weighted past scores   (needs reliable insights)
 *   extra      any external 0..1 score per title, e.g. the River predictor
 * With nothing available every candidate gets the same score (order kept).
 */
function rankTitles(candidates, insights, { extra = null } = {}) {
  const unique = [...new Set((candidates || []).map(t => String(t || '').trim()).filter(t => t.length > 3))];
  const reliable = !!(insights && insights.reliable);
  if (unique.length <= 1 || (!reliable && !(extra && extra.size))) {
    return unique.map(title => ({ title, score: 0.5, heuristic: 0.5, predicted: null, extra: null }));
  }
  const heur = reliable ? unique.map(t => scoreTitleHeuristic(t, insights)) : null;
  const pred = reliable ? predictTitleScores(unique, insights) : null;
  const predicted = unique.map(t => {
    const p = pred && pred.predictions.find(x => x.title === t);
    return p && typeof p.predicted === 'number' ? p.predicted : null;
  });
  const valid = predicted.filter(x => typeof x === 'number');
  const lo = valid.length ? Math.min(...valid) : 0, hi = valid.length ? Math.max(...valid) : 1;
  const ranked = unique.map((title, i) => {
    const parts = [];
    if (heur) parts.push(heur[i]);
    if (valid.length) parts.push(hi > lo ? (predicted[i] - lo) / (hi - lo) : 0.5);
    const ex = extra && extra.has(title) ? extra.get(title) : null;
    if (typeof ex === 'number') parts.push(ex);
    const score = parts.length ? parts.reduce((a, b) => a + b, 0) / parts.length : 0.5;
    return { title, score: Math.round(score * 1000) / 1000, heuristic: heur ? Math.round(heur[i] * 1000) / 1000 : null, predicted: predicted[i], extra: ex };
  });
  ranked.sort((a, b) => b.score - a.score);
  return ranked;
}

// ─── CLI ──────────────────────────────────────────────────────────

function printReport(ins) {
  if (!ins) { console.log('No insights yet — run with --sync first.'); return; }
  console.log(`\nPerformance insights (updated ${ins.updatedAt})`);
  console.log(`Scored shorts: ${ins.sampleSize} | typical ${ins.typicalScore} views/day (mean ${ins.globalMeanScore}) | reliable: ${ins.reliable}`);
  console.log('\nTop titles:');
  for (const t of ins.topTitles) console.log(`  ${String(t.views).padStart(8)} views  ${String(t.score).padStart(7)}/day  ${t.country || '-'}  "${t.title}"`);
  console.log('\nBottom titles:');
  for (const t of ins.bottomTitles) console.log(`  ${String(t.views).padStart(8)} views  ${String(t.score).padStart(7)}/day  ${t.country || '-'}  "${t.title}"`);
  console.log('\nTitle patterns (lift vs channel average):');
  for (const p of ins.titlePatterns) console.log(`  x${p.lift}  (${p.n})  ${p.name}`);
  console.log('\nCountries:');
  for (const [c, s] of Object.entries(ins.countries).sort((a, b) => b[1].weight - a[1].weight)) console.log(`  x${s.weight}  n=${s.n}  avg ${s.meanScore}/day  ${c}`);
  if (Object.keys(ins.channels).length) {
    console.log('\nSource channels:');
    for (const [c, s] of Object.entries(ins.channels).sort((a, b) => b[1].weight - a[1].weight)) console.log(`  x${s.weight}  n=${s.n}  avg ${s.meanScore}/day  ${c}`);
  }
  if (Object.keys(ins.eggs || {}).length) {
    console.log('\nEaster eggs:');
    for (const [c, s] of Object.entries(ins.eggs)) console.log(`  x${s.weight}  n=${s.n}  avg ${s.meanScore}/day  ${c}`);
  }
  console.log('\nPrompt summary:\n' + (ins.promptSummary || '(not enough data yet)') + '\n');
}

async function main() {
  const args = process.argv.slice(2);
  if (args.includes('--sync')) {
    const { YouTubeBridge } = require('../youtube-automation/youtube-bridge');
    const bridge = new YouTubeBridge();
    await bridge.initialize();
    const ins = await syncPerformance(bridge);
    printReport(ins);
    return;
  }
  if (args.includes('--rank')) {
    const titles = args.slice(args.indexOf('--rank') + 1);
    console.log(rankTitles(titles, loadInsights()));
    return;
  }
  printReport(loadInsights());
}

if (require.main === module) {
  main().then(() => process.exit(0)).catch(e => { console.error(e.message); process.exit(1); });
}

module.exports = {
  syncPerformance, fetchChannelVideos, tryFetchAnalytics, computeInsights,
  loadInsights, loadStats, loadPostedIndex, buildPromptSummary,
  countryWeight, channelWeight, categoryWeight, postedDedupList,
  scoreTitleHeuristic, predictTitleScores, rankTitles,
  TITLE_FEATURES, MIN_SAMPLE,
};
