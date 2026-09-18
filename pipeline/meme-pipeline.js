/**
 * Meme acapella pipeline (Mr. WorldWideWebster): "<song> acapella" shorts in
 * the style of the fastest-growing meme channels, with an Asian / anime twist.
 *
 *   1. song: live trends ("<song> acapella" titles on YouTube in the last two
 *      weeks) + a curated list of meme songs, anime openings and K-pop hits;
 *      Thompson sampling on how our own uploads of each song did, and a hit
 *      gets remade (the same song with new clips) like the channels we copy
 *   2. audio: core/meme/acapella.py isolates the vocal and stacks our own
 *      bass / beatbox / harmony layers on it
 *   3. clips: core/meme/clip_finder.py cuts funny Asian / anime moments,
 *      skipping channels whose clips got our videos blocked
 *   4. edit: core/meme/render_meme.py cuts the clips on the beat, 1 -> 2 -> 4
 *      on screen as the layers come in
 *
 * memory/meme-history.json keeps every upload for the learners.
 */
const path = require('path');
const fs = require('fs');
const { execFile } = require('child_process');
const { Logger } = require('../core/logger');
const { loadStats } = require('../core/performance-tracker');

const logger = new Logger('MemeAcapella');
const ROOT = path.join(__dirname, '..');
const MEM = path.resolve(ROOT, process.env.MEMORY_DIR || 'memory');
const HISTORY_FILE = path.join(MEM, 'meme-history.json');

// name shown in the title -> YouTube search for the original song. Asia only:
// anime / J-pop, K-pop and Asian viral songs (the channel's niche).
const SONGS = [
  // anime openings / J-pop
  { name: 'idol', query: 'YOASOBI Idol official audio', theme: 'anime' },
  { name: 'bling bang bang born', query: 'Creepy Nuts Bling-Bang-Bang-Born official audio', theme: 'anime' },
  { name: 'otonoke', query: 'Creepy Nuts Otonoke official audio', theme: 'anime' },
  { name: 'kick back', query: 'Kenshi Yonezu KICK BACK official audio', theme: 'anime' },
  { name: 'unravel', query: 'TK from Ling tosite sigure unravel official audio', theme: 'anime' },
  { name: 'gurenge', query: 'LiSA Gurenge official audio', theme: 'anime' },
  { name: 'blue bird', query: 'Ikimonogakari Blue Bird official audio', theme: 'anime' },
  { name: 'kaikai kitan', query: 'Eve Kaikai Kitan official audio', theme: 'anime' },
  { name: 'specialz', query: 'King Gnu SPECIALZ official audio', theme: 'anime' },
  { name: 'cha la head cha la', query: 'Cha-La Head-Cha-La Dragon Ball Z opening', theme: 'anime' },
  { name: 'we are', query: 'One Piece opening We Are Hiroshi Kitadani', theme: 'anime' },
  { name: 'pokemon theme', query: 'Pokemon theme song original English', theme: 'anime' },
  { name: 'night dancer', query: 'imase NIGHT DANCER official audio', theme: 'jpop' },
  { name: 'shinunoga e-wa', query: 'Fujii Kaze Shinunoga E-Wa official audio', theme: 'jpop' },
  // K-pop
  { name: 'apt', query: 'ROSE Bruno Mars APT official audio', theme: 'kpop' },
  { name: 'gangnam style', query: 'PSY Gangnam Style official audio', theme: 'kpop' },
  { name: 'golden', query: 'HUNTR/X Golden KPop Demon Hunters official audio', theme: 'kpop' },
  { name: 'soda pop', query: 'Saja Boys Soda Pop KPop Demon Hunters official audio', theme: 'kpop' },
  { name: 'super shy', query: 'NewJeans Super Shy official audio', theme: 'kpop' },
  { name: 'hype boy', query: 'NewJeans Hype Boy official audio', theme: 'kpop' },
  { name: 'magnetic', query: 'ILLIT Magnetic official audio', theme: 'kpop' },
  { name: 'supernova', query: 'aespa Supernova official audio', theme: 'kpop' },
  { name: 'how you like that', query: 'BLACKPINK How You Like That official audio', theme: 'kpop' },
  { name: 'dynamite', query: 'BTS Dynamite official audio', theme: 'kpop' },
  // Chinese
  { name: 'yi jian mei', query: 'Fei Yu-ching Yi Jian Mei 一剪梅 费玉清', theme: 'chinese' },
  { name: 'mo li hua', query: '茉莉花 Mo Li Hua Jasmine Flower Chinese folk song', theme: 'chinese' },
  { name: 'gong xi gong xi', query: '恭喜恭喜 Gong Xi Gong Xi Chinese New Year song', theme: 'chinese' },
  // Asian viral
  { name: 'linggang guli guli', query: 'linggang guli guli guli wacha song', theme: 'asian' },
];
// one theme per short, chosen from the song: every clip in it follows that theme
const CLIP_THEMES = {
  anime: ['anime'], jpop: ['anime', 'japanese'], kpop: ['kpop'], chinese: ['chinese'], asian: ['funny', 'cute'],
};
const EMOJI_PAIRS = ['😭,✌️', '🥹,🥹', '😭,😭', '✌️,😭', '😳,😳', '🥲,🥲', '🗣️,🗣️', '😭,🥀'];

function loadHistory() {
  try { return JSON.parse(fs.readFileSync(HISTORY_FILE, 'utf8')); } catch { return { uploads: [] }; }
}

function saveHistory(h) {
  fs.mkdirSync(MEM, { recursive: true });
  fs.writeFileSync(HISTORY_FILE, JSON.stringify(h, null, 1));
}

function recordUpload(result, upload) {
  const h = loadHistory();
  h.uploads.push({
    videoId: upload.videoId, url: upload.url, postedAt: new Date().toISOString(), publishAt: upload.publishAt || null,
    song: result.meme.song, theme: result.meme.theme, emoji: result.meme.emoji, title: result.title,
    clipIds: result.meme.clipIds, clipChannels: result.meme.clipChannels, followUpOf: result.meme.followUpOf || undefined,
  });
  saveHistory(h);
}

function gaussian() {
  let u = 0, v = 0;
  while (!u) u = Math.random();
  while (!v) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function scored(stats, history) {
  const byId = new Map(((stats && stats.videos) || []).map(v => [v.videoId, v]));
  return history.uploads.map(u => ({ ...u, stat: byId.get(u.videoId) }))
    .filter(u => u.stat && typeof u.stat.lscore === 'number' && (u.stat.ageDays || 0) >= 1.5);
}

/** Channels whose clips were in videos YouTube blocked in many countries. */
function blockedChannels(stats, history) {
  const out = new Set();
  for (const u of scored(stats, history)) if ((u.stat.regionBlocked || 0) >= 30) for (const c of u.clipChannels || []) out.add(c);
  return [...out];
}

/** Trending "<song> acapella" titles from the last two weeks (costs ~100 API units). */
async function trendingSongs(youtube) {
  if (!youtube || process.env.MEME_TRENDS === 'off') return [];
  try {
    const after = new Date(Date.now() - 14 * 86400000).toISOString();
    const r = await youtube.search.list({ part: ['snippet'], q: 'acapella', type: ['video'], videoDuration: 'short', order: 'viewCount', publishedAfter: after, maxResults: 50 });
    const counts = new Map();
    for (const it of r.data.items || []) {
      const t = String(it.snippet.title || '').toLowerCase().replace(/&#39;/g, "'");
      const m = t.match(/^([a-z0-9' .&x-]{3,30}?)\s+(?:acapella|a cappella)/);
      if (!m) continue;
      const name = m[1].trim().replace(/\s+/g, ' ');
      counts.set(name, (counts.get(name) || 0) + 1);
    }
    // the channel is Asia-only: trends just boost songs already on our Asian list
    return [...counts.entries()].filter(([name, c]) => c >= 2 && SONGS.some(x => x.name === name))
      .sort((a, b) => b[1] - a[1]).slice(0, 8)
      .map(([name, c]) => ({ ...SONGS.find(x => x.name === name), trendCount: c }));
  } catch (e) {
    logger.warn(`Trend search failed: ${(e.message || '').slice(0, 80)}`);
    return [];
  }
}

function pickSong(stats, history, trends) {
  const done = scored(stats, history);
  // remake a hit: a song whose last upload beat the median 3x, at most 5 remakes, once a day at most
  if (done.length >= 4 && Math.random() < 0.5) {
    const med = [...done].sort((a, b) => a.stat.lscore - b.stat.lscore)[Math.floor(done.length / 2)].stat.lscore;
    const hit = done.filter(u => Math.expm1(u.stat.lscore) >= 3 * Math.max(0.5, Math.expm1(med)) && u.stat.views >= 500)
      .sort((a, b) => b.stat.lscore - a.stat.lscore)[0];
    if (hit && history.uploads.filter(u => u.song === hit.song).length < 6) {
      const s = [...SONGS, ...trends].find(x => x.name === hit.song) || { name: hit.song, query: `${hit.song} song`, theme: hit.theme };
      return { ...s, why: `remake of hit "${hit.title}" (${hit.stat.views} views)`, followUpOf: hit.videoId };
    }
  }
  const recent = new Set(history.uploads.slice(-4).map(u => u.song));
  const prior = done.length ? done.reduce((a, u) => a + u.stat.lscore, 0) / done.length : 0;
  let best = null;
  const seen = new Set();
  for (const s of [...trends, ...SONGS]) {
    if (recent.has(s.name) || seen.has(s.name)) continue;
    seen.add(s.name);
    const mine = done.filter(u => u.song === s.name).map(u => u.stat.lscore);
    const n = mine.length;
    const mean = n ? (mine.reduce((a, b) => a + b, 0) + prior) / (n + 1) : prior + (s.trendCount ? 0.4 : 0);
    const draw = mean + gaussian() * 0.7 / Math.sqrt(n + 1);
    if (!best || draw > best.draw) best = { ...s, draw };
  }
  return { ...best, why: best.trendCount ? `trending (${best.trendCount} recent acapella shorts)` : 'thompson' };
}

function run(cmd, args, timeoutMin) {
  return new Promise((resolve, reject) => {
    execFile(cmd, args, { timeout: timeoutMin * 60000, maxBuffer: 64 * 1024 * 1024, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } },
      (err, stdout, stderr) => {
        const line = String(stdout || '').trim().split('\n').filter(Boolean).pop();
        let res = null;
        try { res = line ? JSON.parse(line) : null; } catch {}
        if (err || !res || res.ok === false) return reject(new Error(((res && res.reason) || String(stderr || err?.message || 'failed')).slice(-400)));
        resolve(res);
      });
  });
}

/**
 * One short meme caption per clip, written from the source video's title, so
 * the viewer knows what they are looking at ("bro did not see that coming 😭").
 * Clips that already carry burned-in captions keep their own.
 */
function cleanTitle(t) {
  const words = String(t)
    .replace(/#[^\s#]+/g, ' ')                         // hashtags
    .replace(/[\[(【「].*?[\])】」]/g, ' ')              // bracketed tags
    .replace(/(shorts?|funny|viral|fyp|foryou|tiktok|douyin|compilation|part \d+|ep\.? ?\d+)/gi, ' ')
    .replace(/[|~_*]+/g, ' ')
    .replace(/\s+/g, ' ').trim().toLowerCase().split(' ');
  const out = words.slice(0, 7).join(' ');
  return out.length >= 6 ? out : '';
}

async function captionClips(manifestPath, theme) {
  let clips;
  try { clips = JSON.parse(fs.readFileSync(manifestPath, 'utf8')); } catch { return 0; }
  const list = Array.isArray(clips) ? clips : clips.clips;
  const need = list.map((c, i) => ({ i, title: c.source_title || '', ch: c.source_channel || '' })).filter(x => !list[x.i].has_caption && x.title);
  if (!need.length) return 0;
  let out = null;
  try {
    const { getGeminiService } = require('../core/gemini-service');
    const g = getGeminiService();
    if (g && g.getStats().keysLoaded > 0) {
      const sys = 'You write tiny meme captions for clips in a YouTube Shorts meme compilation (theme: ' + theme + '). '
        + 'Lowercase, 2-7 words, Gen-Z meme voice, max one emoji, describe the funny/cute moment so a viewer instantly gets it. '
        + 'Never sexual, never mocking accents, looks or ethnicity. If the title is unclear, write a neutral reaction caption.';
      const msg = need.map(x => `${x.i}. "${x.title}" (channel: ${x.ch})`).join('\n') + '\nReturn JSON: {"captions": {"<index>": "caption"}}';
      out = await Promise.race([g.chatJSON(sys, msg), new Promise(r => setTimeout(() => r(null), 60000))]);
    }
  } catch {}
  let n = 0;
  const caps = (out && out.captions) || {};
  for (const x of need) {
    let c = String(caps[x.i] || '').trim();
    if (!c || c.length > 60) c = cleanTitle(x.title);  // no LLM: a tidied source title still gives context
    if (c) { list[x.i].caption = c; n++; }
  }
  fs.writeFileSync(manifestPath, JSON.stringify(clips, null, 1));
  return n;
}

function python() { return process.env.MEME_PYTHON || (process.platform === 'win32' ? 'python' : 'python3'); }

async function downloadSong(song, workDir) {
  const out = path.join(workDir, 'song.%(ext)s');
  const base = ['-m', 'yt_dlp', '--no-playlist', '--no-warnings'];
  if (process.env.YT_COOKIES && fs.existsSync(process.env.YT_COOKIES)) base.push('--cookies', process.env.YT_COOKIES);
  const search = `ytsearch1:${song.query}`;
  const info = await new Promise(res => execFile(python(), [...base, '-J', '--match-filter', 'duration > 60 & duration < 420', search],
    { maxBuffer: 64 * 1024 * 1024, timeout: 120000 }, (e, so) => { try { res(JSON.parse(so)); } catch { res(null); } }));
  const entry = info && (info.entries ? info.entries[0] : info);
  if (!entry) throw new Error(`no song found for "${song.query}"`);
  if (entry.heatmap) fs.writeFileSync(path.join(workDir, 'heatmap.json'), JSON.stringify(entry.heatmap));
  await new Promise((res, rej) => execFile(python(), [...base, '-f', 'bestaudio', '-x', '--audio-format', 'mp3', '-o', out,
    `https://www.youtube.com/watch?v=${entry.id}`], { timeout: 300000 }, e => (e ? rej(e) : res())));
  return { audio: path.join(workDir, 'song.mp3'), heatmap: entry.heatmap ? path.join(workDir, 'heatmap.json') : null, source: entry.id, sourceTitle: entry.title };
}

/**
 * @param {{outputDir: string, youtube?: object}} opts
 * @returns {Promise<Object>} upload-ready result
 */
async function runMemePipeline(opts = {}) {
  const outputDir = opts.outputDir || path.join(ROOT, 'output', 'meme');
  const work = path.join(outputDir, `work-${Date.now()}`);
  fs.mkdirSync(work, { recursive: true });
  const stats = loadStats();
  const history = loadHistory();

  const trends = await trendingSongs(opts.youtube);
  if (trends.length) logger.info(`Trending acapellas: ${trends.map(t => `${t.name} (${t.trendCount})`).join(', ')}`);
  const forced = process.env.MEME_SONG && (SONGS.find(x => x.name === process.env.MEME_SONG.toLowerCase())
    || { name: process.env.MEME_SONG, query: `${process.env.MEME_SONG} song`, theme: 'mixed' });
  const song = forced ? { ...forced, why: 'forced' } : pickSong(stats, history, trends);
  logger.info(`Song: ${song.name} (${song.why})`);

  const t0 = Date.now();
  const src = await downloadSong(song, work);
  logger.info(`Source audio: ${src.sourceTitle}`);
  const acapArgs = [path.join(ROOT, 'core', 'meme', 'acapella.py'), '--audio', src.audio, '--out-dir', path.join(work, 'acapella')];
  if (src.heatmap) acapArgs.push('--heatmap', src.heatmap);
  const acap = await run(python(), acapArgs, 15);
  logger.success(`Acapella: ${acap.bpm} bpm, ${acap.duration}s`);

  const recentClips = history.uploads.slice(-30).flatMap(u => u.clipIds || []);
  const usedFile = path.join(work, 'used.json');
  fs.writeFileSync(usedFile, JSON.stringify(recentClips));
  const blockFile = path.join(work, 'blocked.json');
  const blocked = blockedChannels(stats, history);
  fs.writeFileSync(blockFile, JSON.stringify(blocked));
  if (blocked.length) logger.info(`Skipping ${blocked.length} clip channels that got videos blocked`);
  const options = CLIP_THEMES[song.theme] || ['funny', 'cute'];
  const theme = options[Math.floor(Math.random() * options.length)];
  logger.info(`Clip theme: ${theme}`);
  const finderArgs = [path.join(ROOT, 'core', 'meme', 'clip_finder.py'), '--out-dir', path.join(work, 'clips'), '--count', '16',
    '--theme', theme, '--used', usedFile, '--block', blockFile];
  if (process.env.YT_COOKIES && fs.existsSync(process.env.YT_COOKIES)) finderArgs.push('--cookies', process.env.YT_COOKIES);
  const clips = await run(python(), finderArgs, 12);
  logger.success(`Clips: ${clips.count}`);
  const captioned = await captionClips(clips.manifest, theme);
  if (captioned) logger.info(`Captioned ${captioned} clips`);

  const emoji = EMOJI_PAIRS[Math.floor(Math.random() * EMOJI_PAIRS.length)];
  const caption = `${song.name} acapella`;
  const outPath = path.join(outputDir, `meme-${Date.now()}.mp4`);
  const timeline = acap.timeline || path.join(work, 'acapella', 'timeline.json');
  const audio = acap.acapella || acap.mix || path.join(work, 'acapella', 'acapella.wav');
  const res = await run(python(), [path.join(ROOT, 'core', 'meme', 'render_meme.py'), '--audio', audio, '--timeline', timeline,
    '--clips', clips.manifest, '--title', caption, '--emoji', emoji, '--out', outPath], 20);
  logger.success(`Rendered ${res.duration}s, ${res.cuts} cuts in ${Math.round((Date.now() - t0) / 1000)}s`);

  const title = `${caption} ${emoji.split(',').join('')}`;
  const tagsByTheme = { anime: ['anime', 'anime memes', 'anime funny moments'], kpop: ['kpop', 'kpop memes', 'kpop funny moments'],
    japanese: ['japan', 'japanese memes', 'funny japan'], chinese: ['china', 'douyin', 'chinese memes'],
    funny: ['asian memes', 'funny asian', 'douyin'], cute: ['cute', 'cute asian', 'cute animals'] };
  return {
    success: true,
    videoPath: res.path,
    title,
    description: `${caption} but it's layer by layer ${emoji.split(',')[0]}\n\nwhich layer hit the hardest? 👇\n\n`
      + (res.channelsUsed.length ? `clips from: ${res.channelsUsed.map(c => c.trim()).join(', ')}\n` : '')
      + `#acapella #${{ anime: 'anime', kpop: 'kpop', japanese: 'japan', chinese: 'douyin', cute: 'cute' }[theme] || 'asianmemes'} #shorts`,
    tags: ['acapella', `${song.name} acapella`, song.name, 'memes', 'funny', ...(tagsByTheme[theme] || [])],
    categoryId: '23',
    playlistTitle: `${theme} acapella`,
    comment: 'which layer was the best? 😭',
    country: 'Global',
    category: `meme-${theme}`,
    editType: 'meme',
    meme: { song: song.name, theme, emoji, clipIds: res.clipsUsed, clipChannels: res.channelsUsed, followUpOf: song.followUpOf || null, source: src.source },
    workDir: work,
  };
}

module.exports = { runMemePipeline, recordUpload, loadHistory, pickSong, trendingSongs, cleanTitle, SONGS };
