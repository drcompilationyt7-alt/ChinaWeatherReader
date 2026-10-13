/**
 * Edit pipelines for Zero Yen Otaku: song / dance / character / K-pop / sound edit Shorts and the weekly
 * 16:9 long edit. The stem ("<song> acapella") edit stays in pipeline/meme-pipeline.js.
 *
 *   1. content: the edit type (given, or Thompson sampling over our own uploads of each type), then the
 *      song and subject: songs are Japanese anime songs, J-pop and K-pop only (meme-pipeline SONGS +
 *      edit favourites below); characters come from core/quiz/assets/anime-characters.json (nsfw-flagged
 *      characters and shows skipped; attractiveness framing only for characters whose youngest listed
 *      age is 18+); K-pop member edits from the group's songs; dance edits from anime dance scenes
 *   2. audio: the song from YouTube (+ its "most replayed" heatmap), analysed once by core/edit/music.py
 *   3. clips: core/edit/library.py (source / character / mood / dance mode; no subtitles, no dialogue,
 *      crisp and striking, NudeNet + SigLIP safety)
 *   4. metadata: core/metadata (keyword first: "<character> edit", "<anime> edit", "<song> edit"; 1-3
 *      hashtags; energetic emoji only; the on-video text never has emoji)
 *   5. plan: core/edit/planners.py (energy -> shots, cuts on the beat, the type's reference style)
 *   6. render: core/edit/engine.py --plan plan.json --out video.mp4
 *
 *   const { runEditPipeline, recordUpload } = require('./edit-pipeline');
 *   const r = await runEditPipeline({ outputDir, youtube, type: 'character' });   // or { long: true }
 *   // r: {videoPath, title, description, tags, categoryId, playlistTitle, comment, meme: {...}} like runMemePipeline
 *   recordUpload(r, upload);   // memory/meme-history.json (shorts) or memory/edit-long-history.json, + the experiment log
 *
 * Env: EDIT_TYPE, EDIT_TYPES (enabled types, comma list), EDIT_LONG=true, EDIT_LONG_FLAVOR, EDIT_SONG,
 * EDIT_CHARACTER, EDIT_TEMPLATE, EDIT_SEED, EDIT_SONG_FILE (+ EDIT_HEATMAP_FILE: a local song, no download),
 * EDIT_CACHE (library cache; songs are cached in $EDIT_CACHE/songs), EDIT_LIBRARY_ARGS (extra library args),
 * MEME_PYTHON, YT_COOKIES, WATERMARK.
 */
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { execFile } = require('child_process');
const { Logger } = require('../core/logger');
const { loadStats } = require('../core/performance-tracker');
const { decay, appendDecision, thompsonDraw } = require('../core/experiment-log');
const { optimizeMetadata, recordMetadataChoice, buildDescription } = require('../core/metadata');
const { SONGS } = require('./meme-pipeline');

const logger = new Logger('EditStudio');
const ROOT = path.join(__dirname, '..');
const MEM = () => path.resolve(ROOT, process.env.MEMORY_DIR || 'memory');
const HISTORY_FILE = () => path.join(MEM(), 'meme-history.json');          // shorts share the channel's daily guard
const LONG_HISTORY_FILE = () => path.join(MEM(), 'edit-long-history.json'); // the weekly 16:9 video

const TYPES = ['song', 'dance', 'character', 'kpop', 'sound'];
const CONTENT_TYPE = { song: 'song-edit', dance: 'dance-edit', character: 'character-edit', kpop: 'kpop-edit', sound: 'sound-edit',
  long: 'long-edit' };
const LONG_FLAVORS = ['song', 'character', 'dance', 'kpop'];

// ─── songs ───────────────────────────────────────────────────────
// Japanese anime songs, J-pop and K-pop only (the owner's rule). mood steers which edit type uses them:
// hype / aura (fights, character edits), dance (dance edits), soft (slow-motion and soft character edits).
const MOOD = {
  idol: 'dance', 'chika dance': 'dance', 'bling bang bang born': 'dance', otonoke: 'dance', 'mixed nuts': 'dance',
  'night dancer': 'dance', odoriko: 'dance', 'super shy': 'dance', 'hype boy': 'dance', magnetic: 'dance', eta: 'dance',
  apt: 'dance', 'gangnam style': 'dance', golden: 'dance', 'soda pop': 'dance', cupid: 'dance', 'love dive': 'dance',
  dynamite: 'dance', antifragile: 'dance', 'love dramatic': 'soft', 'hikaru nara': 'soft', 'hana ni natte': 'soft',
  'shinunoga e-wa': 'soft', unravel: 'aura', 'kick back': 'hype', specialz: 'aura', 'kaikai kitan': 'hype', gurenge: 'hype',
  'yoru ni kakeru': 'soft', supernova: 'hype', 'how you like that': 'aura', 'pink venom': 'aura', "god's menu": 'hype',
};
const GROUP_ALIASES = {
  jjk: ['jujutsu kaisen', 'jjk', '呪術廻戦', 'gojo', 'itadori', 'sukuna', 'megumi', 'geto', 'toji'],
  ds: ['demon slayer', 'kimetsu no yaiba', '鬼滅の刃', 'tanjiro', 'nezuko', 'zenitsu', 'inosuke', 'rengoku', 'muichiro'],
  kpdh: ['kpop demon hunters', 'k-pop demon hunters', 'huntr/x', 'huntrix', 'rumi', 'mira', 'zoey', 'saja boys', 'jinu'],
};
const EXTRA_SONGS = [
  // KPop Demon Hunters (the soundtrack edits are built on)
  { name: 'your idol', query: 'Saja Boys Your Idol KPop Demon Hunters official audio', artist: 'Saja Boys', theme: 'kpop',
    source: 'KPop Demon Hunters', kind: 'anime', aliases: GROUP_ALIASES.kpdh, mood: 'aura' },
  { name: "how it's done", query: "HUNTR/X How It's Done KPop Demon Hunters official audio", artist: 'HUNTR/X', theme: 'kpop',
    source: 'KPop Demon Hunters', kind: 'anime', aliases: GROUP_ALIASES.kpdh, mood: 'hype' },
  { name: 'takedown', query: 'HUNTR/X Takedown KPop Demon Hunters official audio', artist: 'HUNTR/X', theme: 'kpop',
    source: 'KPop Demon Hunters', kind: 'anime', aliases: GROUP_ALIASES.kpdh, mood: 'hype' },
  // anime songs edits are made on
  { name: 'silhouette', query: 'KANA-BOON Silhouette official audio', artist: 'KANA-BOON', theme: 'anime', source: 'Naruto', kind: 'anime',
    aliases: ['naruto', 'naruto shippuden', 'ナルト', 'sasuke', 'kakashi', 'itachi'], mood: 'hype' },
  { name: 'shinzou wo sasageyo', query: 'Linked Horizon Shinzou wo Sasageyo official audio', artist: 'Linked Horizon', theme: 'anime',
    source: 'Attack on Titan', kind: 'anime', aliases: ['attack on titan', 'shingeki no kyojin', '進撃の巨人', 'eren', 'levi', 'mikasa'], mood: 'aura' },
  { name: 'where our blue is', query: 'Tatsuya Kitani Where Our Blue Is Ao no Sumika official audio', artist: 'Tatsuya Kitani',
    theme: 'anime', source: 'Jujutsu Kaisen', kind: 'anime', aliases: GROUP_ALIASES.jjk, mood: 'aura' },
  { name: 'inferno', query: 'Mrs. GREEN APPLE Inferno official audio', artist: 'Mrs. GREEN APPLE', theme: 'anime', source: 'Fire Force',
    kind: 'anime', aliases: ['fire force', 'enen no shouboutai', '炎炎ノ消防隊', 'shinra', 'arthur'], mood: 'hype' },
  { name: 'cry baby', query: 'Official HIGE DANdism Cry Baby official audio', artist: 'Official HIGE DANdism', theme: 'anime',
    source: 'Tokyo Revengers', kind: 'anime', aliases: ['tokyo revengers', '東京リベンジャーズ', 'mikey', 'draken', 'takemichi'], mood: 'aura' },
  { name: 'zankyosanka', query: 'Aimer Zankyosanka official audio', artist: 'Aimer', theme: 'anime', source: 'Demon Slayer', kind: 'anime',
    aliases: GROUP_ALIASES.ds, mood: 'hype' },
  { name: 'kizuna no kiseki', query: 'MAN WITH A MISSION milet Kizuna no Kiseki official audio', artist: 'MAN WITH A MISSION x milet',
    theme: 'anime', source: 'Demon Slayer', kind: 'anime', aliases: GROUP_ALIASES.ds, mood: 'hype' },
  { name: 'peace sign', query: 'Kenshi Yonezu Peace Sign official audio', artist: 'Kenshi Yonezu', theme: 'anime', source: 'My Hero Academia',
    kind: 'anime', aliases: ['my hero academia', 'boku no hero academia', 'mha', 'deku', 'bakugo', 'todoroki', 'all might'], mood: 'hype' },
  { name: 'black catcher', query: 'Vickeblanka Black Catcher official audio', artist: 'Vickeblanka', theme: 'anime', source: 'Black Clover',
    kind: 'anime', aliases: ['black clover', 'ブラッククローバー', 'asta', 'yuno'], mood: 'hype' },
  { name: 'yuusha', query: 'YOASOBI Yuusha The Brave official audio', artist: 'YOASOBI', theme: 'anime', source: 'Frieren', kind: 'anime',
    aliases: ['frieren', 'sousou no frieren', '葬送のフリーレン', 'fern', 'stark', 'himmel'], mood: 'soft' },
  { name: 'sparkle', query: 'RADWIMPS Sparkle official audio', artist: 'RADWIMPS', theme: 'anime', source: 'Your Name', kind: 'anime',
    aliases: ['your name', 'kimi no na wa', '君の名は', 'taki', 'mitsuha'], mood: 'soft' },
  { name: 'tank', query: 'Seatbelts Tank! Cowboy Bebop opening', artist: 'Seatbelts', theme: 'anime', source: 'Cowboy Bebop', kind: 'anime',
    aliases: ['cowboy bebop', 'カウボーイビバップ', 'spike spiegel', 'faye valentine'], mood: 'hype' },
  // J-pop
  { name: 'koi', query: 'Gen Hoshino Koi official audio', artist: 'Gen Hoshino', theme: 'jpop', source: 'Gen Hoshino', kind: 'group',
    aliases: ['gen hoshino', '星野源', 'koi dance'], mood: 'dance' },
  // K-pop
  { name: 'whiplash', query: 'aespa Whiplash official audio', theme: 'kpop', source: 'aespa', kind: 'group', mood: 'hype' },
  { name: 'drama', query: 'aespa Drama official audio', theme: 'kpop', source: 'aespa', kind: 'group', mood: 'hype' },
  { name: 'smart', query: 'LE SSERAFIM Smart official audio', theme: 'kpop', source: 'LE SSERAFIM', kind: 'group', mood: 'dance' },
  { name: 'easy', query: 'LE SSERAFIM Easy official audio', theme: 'kpop', source: 'LE SSERAFIM', kind: 'group', mood: 'aura' },
  { name: 'i am', query: 'IVE I AM official audio', theme: 'kpop', source: 'IVE', kind: 'group', mood: 'hype' },
  { name: 'after like', query: 'IVE After LIKE official audio', theme: 'kpop', source: 'IVE', kind: 'group', mood: 'dance' },
  { name: 'ditto', query: 'NewJeans Ditto official audio', theme: 'kpop', source: 'NewJeans', kind: 'group', mood: 'soft' },
  { name: 'attention', query: 'NewJeans Attention official audio', theme: 'kpop', source: 'NewJeans', kind: 'group', mood: 'dance' },
  { name: 'shut down', query: 'BLACKPINK Shut Down official audio', theme: 'kpop', source: 'BLACKPINK', kind: 'group', mood: 'aura' },
  { name: 'fancy', query: 'TWICE FANCY official audio', theme: 'kpop', source: 'TWICE', kind: 'group', mood: 'dance',
    aliases: ['twice', '트와이스', 'nayeon', 'jihyo', 'momo', 'sana', 'tzuyu', 'mina', 'dahyun', 'chaeyoung', 'jeongyeon'] },
  { name: 'queencard', query: '(G)I-DLE Queencard official audio', theme: 'kpop', source: '(G)I-DLE', kind: 'group', mood: 'hype',
    aliases: ['(g)i-dle', 'gidle', 'i-dle', '여자아이들', 'soyeon', 'miyeon', 'minnie', 'yuqi', 'shuhua'] },
  { name: 'maniac', query: 'Stray Kids MANIAC official audio', theme: 'kpop', source: 'Stray Kids', kind: 'group', mood: 'hype' },
  { name: 'wannabe', query: 'ITZY WANNABE official audio', theme: 'kpop', source: 'ITZY', kind: 'group', mood: 'dance',
    aliases: ['itzy', '있지', 'yeji', 'ryujin', 'chaeryeong', 'lia', 'yuna'] },
];
// K-pop members (member edits: one idol in every beat-section shot)
const KPOP_MEMBERS = {
  NewJeans: ['Minji', 'Hanni', 'Danielle', 'Haerin', 'Hyein'], aespa: ['Karina', 'Winter', 'Giselle', 'Ningning'],
  IVE: ['Wonyoung', 'Yujin', 'Gaeul', 'Rei', 'Liz', 'Leeseo'], 'LE SSERAFIM': ['Chaewon', 'Sakura', 'Yunjin', 'Kazuha', 'Eunchae'],
  BLACKPINK: ['Jennie', 'Lisa', 'Jisoo', 'Rosé'], ILLIT: ['Yunah', 'Minju', 'Moka', 'Wonhee', 'Iroha'],
  'Stray Kids': ['Felix', 'Hyunjin', 'Bang Chan', 'Han', 'Lee Know', 'Changbin', 'Seungmin', 'I.N'],
  BTS: ['Jungkook', 'Jimin', 'V', 'Suga', 'J-Hope', 'Jin', 'RM'], TWICE: ['Nayeon', 'Jihyo', 'Momo', 'Sana', 'Tzuyu', 'Mina', 'Dahyun', 'Chaeyoung'],
  ITZY: ['Yeji', 'Ryujin', 'Chaeryeong', 'Yuna'], '(G)I-DLE': ['Soyeon', 'Miyeon', 'Minnie', 'Yuqi', 'Shuhua'],
};
// anime dance scenes (dance.json clip_selection.seeds; nsfw-flagged shows are filtered out at run time)
const DANCE_SOURCES = [
  { source: 'Kaguya-sama', query: 'Kaguya-sama Chika', aliases: ['kaguya-sama', 'kaguya sama', 'chika', 'fujiwara chika'],
    songs: ['chika dance', 'love dramatic'] },
  { source: 'Oshi no Ko', query: 'Oshi no Ko', aliases: ['oshi no ko', '推しの子', 'ai hoshino', 'ruby hoshino', 'b komachi'],
    songs: ['idol'] },
  { source: 'KPop Demon Hunters', query: 'KPop Demon Hunters', aliases: GROUP_ALIASES.kpdh,
    songs: ['golden', 'soda pop', 'your idol', "how it's done", 'takedown'] },
  { source: 'Mashle', query: 'Mashle', aliases: ['mashle', 'マッシュル', 'mash burnedead'],
    songs: ['bling bang bang born'] },
  { source: 'Spy x Family', query: 'Spy x Family', aliases: ['spy x family', 'spy family', 'anya', 'yor', 'loid'],
    songs: ['mixed nuts'] },
  { source: 'Idoly Pride', query: 'Idoly Pride', aliases: ['idoly pride', 'アイドリープライド'] },
  { source: 'Shine Post', query: 'Shine Post', aliases: ['shine post', 'シャインポスト'] },
  { source: 'Love Live', query: 'Love Live', aliases: ['love live', 'ラブライブ', 'aqours', "μ's"] },
  { source: 'Uma Musume', query: 'Uma Musume', aliases: ['uma musume', 'ウマ娘'] },
  { source: 'The Idolmaster', query: 'Idolmaster', aliases: ['idolmaster', 'アイドルマスター'] },
  { source: 'Zombie Land Saga', query: 'Zombie Land Saga', aliases: ['zombie land saga', 'ゾンビランドサガ', 'franchouchou'] },
  { source: 'Lucky Star', query: 'Lucky Star', aliases: ['lucky star', 'らき☆すた', 'konata'] },
];
// crisp hits for sound edits (punches, swords, snaps, stomps)
const SOUND_SOURCES = ['Jujutsu Kaisen', 'Demon Slayer', 'Chainsaw Man', 'One Piece', 'Naruto', 'Solo Leveling', 'Dandadan',
  'Sakamoto Days', 'Spy x Family', 'Blue Lock', 'Haikyuu', 'Mob Psycho 100', 'Hunter x Hunter', 'Bleach', 'My Hero Academia'];
const MOOD_QUERY = { hype: 'fight', aura: 'aura', dance: 'dance', soft: 'sunset scenery', cute: 'cute' };

// ─── helpers ─────────────────────────────────────────────────────

const norm = s => String(s || '').toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
const pick = (xs, rng = Math.random) => xs[Math.floor(rng() * xs.length)];
function readJson(file, dflt) { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return dflt; } }

/** Every show / character flagged nsfw in the quiz asset lists (the owner's content rule). */
function nsfwSets() {
  const al = readJson(path.join(ROOT, 'core', 'quiz', 'assets', 'anime-list.json'), {});
  const list = al.anime || Object.values(al)[0] || [];
  const shows = new Set(list.filter(x => x.nsfw).flatMap(x => [norm(x.title), norm(x.romaji)]).filter(Boolean));
  const ac = readJson(path.join(ROOT, 'core', 'quiz', 'assets', 'anime-characters.json'), {});
  const chars = new Set((ac.characters || []).filter(x => x.nsfw).map(x => norm(x.name)));
  return { shows, chars };
}

function showFlagged(name, aliases, sets) {
  return [name, ...(aliases || [])].map(norm).some(n => n && sets.shows.has(n));
}

function editSongs(sets = nsfwSets()) {
  const seen = new Set();
  const out = [];
  for (const s of [...SONGS, ...EXTRA_SONGS]) {
    if (seen.has(s.name)) continue;
    seen.add(s.name);
    if (s.kind === 'anime' && showFlagged(s.source, s.aliases, sets)) continue;  // e.g. Darling in the FranXX, Horimiya
    out.push({ ...s, mood: s.mood || MOOD[s.name] || (s.theme === 'kpop' ? 'dance' : 'hype') });
  }
  return out;
}

/** "17-18" -> 17, "15-16 (series), 18 (movie)" -> 15, null -> null: the youngest listed age. */
function minAge(age) {
  const nums = String(age || '').match(/\d+/g);
  return nums ? Math.min(...nums.map(Number)) : null;
}

function prettyShow(s) {
  const t = String(s || '').trim();
  return t === t.toUpperCase() ? t.toLowerCase().replace(/\b[a-z]/g, c => c.toUpperCase()) : t;
}

function characterPool(sets = nsfwSets()) {
  const ac = readJson(path.join(ROOT, 'core', 'quiz', 'assets', 'anime-characters.json'), {});
  return (ac.characters || [])
    .filter(c => c && c.name && c.anime && !c.nsfw && !sets.chars.has(norm(c.name)) && !sets.shows.has(norm(c.anime)))
    .map(c => ({ ...c, show: prettyShow(c.anime), minAge: minAge(c.age) }))
    .sort((a, b) => (b.favourites || 0) - (a.favourites || 0))
    .slice(0, 150);
}

/** The songs of an anime (the character's own openings first). */
function songsOfShow(show, songs) {
  const n = norm(show);
  return songs.filter(s => s.kind === 'anime' && (norm(s.source) === n || (s.aliases || []).some(a => norm(a) === n)
    || (n.length > 5 && (norm(s.source).includes(n) || n.includes(norm(s.source))))));
}

/** A short name people search ("gojo edit"): an alias of the show's songs that is part of the name, else the name. */
function searchName(c, songs) {
  const nameWords = norm(c.name).split(' ');
  for (const s of songsOfShow(c.show, songs)) {
    for (const a of s.aliases || []) {
      const an = norm(a);
      if (an && an.split(' ').length <= 2 && an.split(' ').every(w => nameWords.includes(w))) return a;
    }
  }
  return c.name;
}

function gaussian() {
  let u = 0, v = 0;
  while (!u) u = Math.random();
  while (!v) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

// ─── history + learning ──────────────────────────────────────────

function loadHistory(long = false) { return readJson(long ? LONG_HISTORY_FILE() : HISTORY_FILE(), { uploads: [] }); }

function saveHistory(h, long = false) {
  fs.mkdirSync(MEM(), { recursive: true });
  fs.writeFileSync(long ? LONG_HISTORY_FILE() : HISTORY_FILE(), JSON.stringify(h, null, 1));
}

function scored(stats, history) {
  const byId = new Map(((stats && stats.videos) || []).map(v => [v.videoId, v]));
  const out = (history.uploads || []).map(u => ({ ...u, stat: byId.get(u.videoId) }))
    .filter(u => u.stat && typeof u.stat.lscore === 'number' && (u.stat.ageDays || 0) >= 1.5);
  const useReward = out.length && out.every(u => typeof u.stat.reward === 'number');
  return out.map(u => ({ ...u, learn: useReward ? u.stat.reward : u.stat.lscore, w: decay(u.stat.ageDays) }));
}

/**
 * Which content type next: Thompson sampling on how our own uploads of each type did (a type with no
 * uploads starts from the channel mean), never the same type twice in a row when there is a choice.
 * types: e.g. ['stem', 'song', 'character'] ('stem' = the meme-pipeline stem edit, content type 'stem-edit').
 */
function pickEditType({ stats = loadStats(), history = loadHistory(), types = null } = {}) {
  const enabled = (types || (process.env.EDIT_TYPES ? process.env.EDIT_TYPES.split(',') : TYPES)).map(s => s.trim()).filter(Boolean);
  const ct = t => (t === 'stem' ? 'stem-edit' : CONTENT_TYPE[t] || `${t}-edit`);
  const done = scored(stats, history);
  const prior = done.length ? done.reduce((a, u) => a + u.learn, 0) / done.length : 0;
  const last = (history.uploads || []).slice(-1)[0];
  const lastCt = last ? (last.contentType || 'stem-edit') : null;
  let best = null;
  for (const t of enabled) {
    if (enabled.length > 1 && ct(t) === lastCt) continue;
    const obs = done.filter(u => (u.contentType || 'stem-edit') === ct(t)).map(u => ({ r: u.learn, ageDays: u.stat.ageDays }));
    const d = thompsonDraw(obs, prior);
    if (!best || d.draw > best.draw) best = { type: t, ...d };
  }
  return best || { type: enabled[0] || 'song', draw: 0, mean: 0, n: 0 };
}

/** Song choice: Thompson on our uploads of each song (pooled with its source), recent songs / sources skipped. */
function pickSong(candidates, stats, history, { avoidRecent = 6 } = {}) {
  const done = scored(stats, history);
  const recent = new Set((history.uploads || []).slice(-avoidRecent).map(u => u.song));
  const channelPrior = done.length ? done.reduce((a, u) => a + u.learn, 0) / done.length : 0;
  const wmean = obs => { const n = obs.reduce((a, u) => a + u.w, 0); return n ? obs.reduce((a, u) => a + u.w * u.learn, 0) / n : null; };
  let best = null;
  for (const s of candidates) {
    if (recent.has(s.name) && candidates.length > avoidRecent) continue;
    const srcObs = done.filter(u => u.clipSource && u.clipSource === s.source);
    const prior = srcObs.length ? (wmean(srcObs) + channelPrior) / 2 : channelPrior;
    const mine = done.filter(u => u.song === s.name);
    const n = mine.reduce((a, u) => a + u.w, 0);
    const mean = (mine.reduce((a, u) => a + u.w * u.learn, 0) + prior) / (n + 1);
    const draw = mean + gaussian() * 0.7 / Math.sqrt(n + 1);
    if (!best || draw > best.draw) best = { ...s, draw };
  }
  return best || pick(candidates);
}

// ─── content choice per type ─────────────────────────────────────

function forcedSong(songs) {
  const want = process.env.EDIT_SONG && norm(process.env.EDIT_SONG);
  return want ? songs.find(s => norm(s.name) === want) || null : null;
}

/**
 * The song, subject and library request for one video.
 * Returns {type, song, subject, library: {...}, fallbacks: [...], facts, contentType, planner: {...}, why}.
 */
function chooseContent(type, { stats, history, flavor = null, rng = Math.random }) {
  const sets = nsfwSets();
  const songs = editSongs(sets);
  const fs_ = forcedSong(songs);
  const recentTitles = (history.uploads || []).slice(-30).map(u => u.title).filter(Boolean);
  const recentSources = new Set((history.uploads || []).slice(-5).map(u => u.clipSource).filter(Boolean));
  const t = type === 'long' ? (flavor || 'song') : type;

  if (t === 'character') {
    const pool = characterPool(sets);
    const recentChars = new Set((history.uploads || []).slice(-25).map(u => u.character).filter(Boolean));
    const wanted = process.env.EDIT_CHARACTER && pool.find(c => norm(c.name) === norm(process.env.EDIT_CHARACTER));
    const fresh = pool.filter(c => !recentChars.has(c.name));
    // popularity-weighted pick among the top characters we have not done lately
    const cands = (fresh.length ? fresh : pool).slice(0, 60);
    const tot = cands.reduce((a, c) => a + Math.sqrt(c.favourites || 1), 0);
    let r = rng() * tot;
    let c = wanted || cands.find(x => (r -= Math.sqrt(x.favourites || 1)) <= 0) || cands[0];
    if (!c) throw new Error('no characters to edit');
    const adult = c.minAge != null && c.minAge >= 18;
    // attractiveness framing (soft romance, lingering close-ups) only for adults; everyone else aura / cool
    const framing = adult && rng() < 0.5 ? 'attractive' : 'aura';
    const own = songsOfShow(c.show, songs);
    const moods = framing === 'attractive' ? ['soft', 'aura', 'dance'] : ['hype', 'aura'];
    const song = fs_ || (own.length && rng() < 0.6 ? pickSong(own, stats, history, { avoidRecent: 2 })
      : pickSong(songs.filter(s => moods.includes(s.mood)), stats, history));
    const short = searchName(c, songs);
    const nameParts = norm(c.name).split(' ').filter(w => w.length >= 3);
    return {
      type, song, contentType: CONTENT_TYPE[type],
      subject: { character: c.name, characterShort: short, show: c.show, image: c.image, adult, framing, age: c.age || null },
      library: { mode: 'character', query: c.name, aliases: [...new Set([c.show, short, ...nameParts])].join(','),
        kind: 'anime', count: type === 'long' ? 50 : 16, minDur: 1.5, maxDur: 8, refImages: c.image ? [c.image] : [] },
      fallbacks: [],
      facts: { character: short, source: c.show, song: song.name, artist: song.artist, theme: 'anime', kind: 'anime',
        aliases: [c.name, short, c.show], recentTitles },
      planner: { character: short, framing, subtitle: c.show },
      clipSource: c.show,
      why: `character ${c.name} (${c.show}, ${adult ? 'adult' : 'age ' + (c.age || '?')}, ${framing})`,
    };
  }

  if (t === 'kpop') {
    const kp = songs.filter(s => s.theme === 'kpop' && s.kind === 'group');
    const song = fs_ && fs_.theme === 'kpop' ? fs_ : pickSong(kp.filter(s => !recentSources.has(s.source)).length
      ? kp.filter(s => !recentSources.has(s.source)) : kp, stats, history);
    const members = KPOP_MEMBERS[song.source] || [];
    const member = members.length && rng() < 0.65 ? pick(members, rng) : null;
    const group = song.source;
    return {
      type, song, contentType: CONTENT_TYPE[type],
      subject: { group, member },
      library: member
        ? { mode: 'character', query: member, aliases: [group, ...(song.aliases || []).filter(a => norm(a) !== norm(member))].join(','), kind: 'group',
          count: type === 'long' ? 50 : 16, minDur: 1.5, maxDur: 12 }
        : { mode: 'source', query: group, aliases: (song.aliases || [group]).join(','), kind: 'group',
          count: type === 'long' ? 50 : 16, minDur: 1.5, maxDur: 12 },
      fallbacks: member ? [{ mode: 'source', query: group, aliases: (song.aliases || [group]).join(','), kind: 'group', count: 16, minDur: 1.5,
        maxDur: 12, dropMember: true }] : [],
      facts: { group, member, song: song.name, artist: group, source: group, theme: 'kpop', kind: 'group', aliases: song.aliases,
        recentTitles },
      planner: { group, subtitle: member ? group : null },
      clipSource: group,
      why: `K-pop ${member ? `${member} (${group})` : group} on ${song.name}`,
    };
  }

  if (t === 'dance') {
    const srcs = DANCE_SOURCES.filter(d => !showFlagged(d.source, d.aliases, sets) && !recentSources.has(d.source));
    const d = pick(srcs.length ? srcs : DANCE_SOURCES.filter(x => !showFlagged(x.source, x.aliases, sets)), rng);
    const own = songs.filter(s => (d.songs || []).includes(s.name));
    const song = fs_ || (own.length && rng() < 0.5 ? pick(own, rng) : pickSong(songs.filter(s => s.mood === 'dance'), stats, history));
    const ownSong = (d.songs || []).includes(song.name);
    const others = srcs.filter(x => x !== d);
    return {
      type, song, contentType: CONTENT_TYPE[type],
      subject: { source: d.source },
      library: { mode: 'dance', query: d.query, aliases: d.aliases.join(','), kind: 'anime', count: type === 'long' ? 40 : 10, minDur: 2.5,
        maxDur: 12 },
      fallbacks: others.slice(0, 1).map(o => ({ mode: 'dance', query: o.query, aliases: o.aliases.join(','), kind: 'anime', count: 10,
        minDur: 2.5, maxDur: 12, source: o.source })),
      facts: { song: song.name, artist: song.artist, source: d.source, group: d.source, theme: 'anime', kind: 'anime',
        keyword: ownSong ? `${song.name} dance` : `${d.source} dance edit`, aliases: d.aliases, recentTitles },
      planner: { subtitle: d.source },
      clipSource: d.source,
      why: `dance: ${d.source} on ${song.name}`,
    };
  }

  if (t === 'sound') {
    const src = pick(SOUND_SOURCES.filter(s => !recentSources.has(s) && !showFlagged(s, [], sets)), rng) || 'Jujutsu Kaisen';
    const own = songsOfShow(src, songs);
    const song = fs_ || (own.length && rng() < 0.6 ? pick(own, rng) : pickSong(songs.filter(s => ['hype', 'dance'].includes(s.mood)), stats, history));
    return {
      type, song, contentType: CONTENT_TYPE[type],
      subject: { source: src },
      library: { mode: 'source', query: src, aliases: (own[0] && own[0].aliases || [src]).join(','), kind: 'anime', count: 28, minDur: 0.8,
        maxDur: 4, withAudio: true, needSfx: true },
      fallbacks: [{ mode: 'mood', query: 'fight', aliases: '', kind: 'anime', count: 28, minDur: 0.8, maxDur: 4,
        withAudio: true, needSfx: true, multi: true }],
      facts: { source: src, song: song.name, artist: song.artist, theme: 'anime', kind: 'anime', keyword: `${src} sound edit`,
        aliases: [src], recentTitles },
      planner: { subtitle: src },
      clipSource: src,
      why: `sound edit: ${src} hits on ${song.name}`,
    };
  }

  // song edit (and the long "song" flavor): one song, the best shots of its anime / group, or a mood mix
  const pool = songs.filter(s => s.kind === 'anime' || s.theme !== 'kpop');
  const song = fs_ || pickSong(pool.filter(s => !recentSources.has(s.source)).length ? pool.filter(s => !recentSources.has(s.source)) : pool,
    stats, history);
  const mix = !song.source || (song.kind === 'group' && song.theme !== 'kpop') || rng() < 0.25;
  const count = type === 'long' ? 55 : 16;
  const moodLib = { mode: 'mood', query: MOOD_QUERY[song.mood] || MOOD_QUERY.hype, aliases: '', kind: 'anime', count, minDur: 1.5, maxDur: 8,
    multi: true };
  const srcLib = { mode: 'source', query: song.source, aliases: (song.aliases || []).join(','), kind: song.kind || 'anime', count, minDur: 1.5,
    maxDur: type === 'long' ? 10 : 8 };
  const lib = mix ? moodLib : srcLib;
  return {
    type, song, contentType: CONTENT_TYPE[type],
    subject: { source: mix ? null : song.source },
    library: lib,
    fallbacks: mix ? [] : [moodLib],
    facts: { song: song.name, artist: song.artist, source: mix ? null : song.source, kind: song.kind, theme: song.theme,
      query: song.query, aliases: song.aliases, recentTitles,
      ...(type === 'long' ? { keyword: mix ? `${song.name} amv` : `${song.source} amv` } : {}) },
    planner: { subtitle: mix ? null : song.source },
    clipSource: mix ? null : song.source,
    why: mix ? `song edit: ${song.name} (${song.mood} mix of many anime)` : `song edit: ${song.name} (${song.source})`,
  };
}

// ─── running python ──────────────────────────────────────────────

function python() { return process.env.MEME_PYTHON || (process.platform === 'win32' ? 'python' : 'python3'); }

/**
 * Run a python step; resolves with its last stdout JSON line ({} when a step prints none but exits 0).
 * partialOk: a result with ok false still resolves when it names a manifest (the library's "fewer shots than asked").
 */
function runPy(args, timeoutMin, { allowNoJson = false, partialOk = false } = {}) {
  return new Promise((resolve, reject) => {
    execFile(python(), args, { timeout: timeoutMin * 60000, maxBuffer: 128 * 1024 * 1024, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } },
      (err, stdout, stderr) => {
        const lines = String(stdout || '').trim().split('\n').filter(Boolean);
        let res = null;
        for (let i = lines.length - 1; i >= 0 && !res; i--) { try { res = JSON.parse(lines[i]); } catch {} }
        const partial = partialOk && res && res.manifest && fs.existsSync(res.manifest);
        if (!partial && (err || (res && res.ok === false) || (!res && !allowNoJson))) {
          return reject(new Error(((res && (res.reason || res.error)) || String(stderr || err?.message || 'failed')).slice(-500)));
        }
        resolve(res || {});
      });
  });
}

// ─── the song ────────────────────────────────────────────────────

async function downloadSong(song, workDir) {
  if (process.env.EDIT_SONG_FILE && fs.existsSync(process.env.EDIT_SONG_FILE)) {
    const hm = process.env.EDIT_HEATMAP_FILE && fs.existsSync(process.env.EDIT_HEATMAP_FILE) ? process.env.EDIT_HEATMAP_FILE : null;
    return { audio: process.env.EDIT_SONG_FILE, heatmap: hm, source: null, sourceTitle: path.basename(process.env.EDIT_SONG_FILE) };
  }
  // a song cache (the library's cache folder) so local reruns never hit YouTube twice for one song
  const cacheRoot = process.env.EDIT_CACHE ? path.join(process.env.EDIT_CACHE, 'songs') : null;
  const key = crypto.createHash('md5').update(song.query).digest('hex').slice(0, 12);
  if (cacheRoot) {
    const a = path.join(cacheRoot, `${key}.mp3`);
    const m = path.join(cacheRoot, `${key}.json`);
    if (fs.existsSync(a) && fs.existsSync(m)) {
      const meta = readJson(m, {});
      return { audio: a, heatmap: meta.heatmap && fs.existsSync(meta.heatmap) ? meta.heatmap : null, source: meta.source, sourceTitle: meta.sourceTitle };
    }
  }
  const base = ['-m', 'yt_dlp', '--no-playlist', '--no-warnings'];
  if (process.env.YT_COOKIES && fs.existsSync(process.env.YT_COOKIES)) base.push('--cookies', process.env.YT_COOKIES);
  const info = await new Promise(res => execFile(python(), [...base, '-J', '--match-filter', 'duration > 60 & duration < 420', `ytsearch1:${song.query}`],
    { maxBuffer: 64 * 1024 * 1024, timeout: 120000 }, (e, so) => { try { res(JSON.parse(so)); } catch { res(null); } }));
  const entry = info && (info.entries ? info.entries[0] : info);
  if (!entry) throw new Error(`no song found for "${song.query}"`);
  const dir = cacheRoot || workDir;
  fs.mkdirSync(dir, { recursive: true });
  const stem = cacheRoot ? key : 'song';
  let heatmap = null;
  if (entry.heatmap) {
    heatmap = path.join(dir, `${stem}.heatmap.json`);
    fs.writeFileSync(heatmap, JSON.stringify(entry.heatmap));
  }
  await new Promise((res, rej) => execFile(python(), [...base, '-f', 'bestaudio', '-x', '--audio-format', 'mp3', '-o', path.join(dir, `${stem}.%(ext)s`),
    `https://www.youtube.com/watch?v=${entry.id}`], { timeout: 300000 }, e => (e ? rej(e) : res())));
  const audio = path.join(dir, `${stem}.mp3`);
  if (cacheRoot) fs.writeFileSync(path.join(dir, `${key}.json`), JSON.stringify({ heatmap, source: entry.id, sourceTitle: entry.title }));
  return { audio, heatmap, source: entry.id, sourceTitle: entry.title };
}

// ─── the clips ───────────────────────────────────────────────────

function blockedChannels(stats, history) {
  const out = new Set();
  for (const u of scored(stats, history)) if ((u.stat.regionBlocked || 0) >= 30) for (const c of u.clipChannels || []) out.add(c);
  return [...out];
}

async function runLibrary(lib, work, { long, used, blocked, tag }) {
  const outDir = path.join(work, `library${tag ? '-' + tag : ''}`);
  const args = [path.join(ROOT, 'core', 'edit', 'library.py'), '--mode', lib.mode, '--query', lib.query, '--kind', lib.kind || 'anime',
    '--count', String(lib.count || 12), '--min-dur', String(lib.minDur || 2), '--max-dur', String(lib.maxDur || 8), '--out-dir', outDir,
    '--used', used, '--block', blocked];
  if (lib.aliases) args.push('--aliases', lib.aliases);
  if (lib.withAudio) args.push('--with-audio');
  if (lib.needSfx) args.push('--need-sfx');
  if (lib.refImages && lib.refImages.length) args.push('--char-ref-images', lib.refImages.join(','));
  if (long) args.push('--budget', '2400');
  if (process.env.YT_COOKIES && fs.existsSync(process.env.YT_COOKIES)) args.push('--cookies', process.env.YT_COOKIES);
  if (process.env.EDIT_LIBRARY_ARGS) args.push(...process.env.EDIT_LIBRARY_ARGS.split(' ').filter(Boolean));
  const res = await runPy(args, long ? 50 : 18, { partialOk: true });
  const manifest = res.manifest || path.join(outDir, 'shots.json');
  const shots = (readJson(manifest, {}).shots || []);
  return { ...res, manifest, shots };
}

const MIN_SHOTS = { song: 6, dance: 3, character: 5, kpop: 5, sound: 8, long: 20 };

// ─── the pipeline ────────────────────────────────────────────────

/**
 * @param {{outputDir?: string, youtube?: object, type?: string, long?: boolean, flavor?: string}} opts
 * @returns {Promise<object>} upload-ready result (the runMemePipeline shape)
 */
async function runEditPipeline(opts = {}) {
  const long = !!(opts.long || process.env.EDIT_LONG === 'true');
  const outputDir = opts.outputDir || path.join(ROOT, 'output', 'edit');
  const work = path.join(outputDir, `work-${Date.now()}`);
  fs.mkdirSync(work, { recursive: true });
  const stats = loadStats();
  const history = loadHistory(long);
  const shortHistory = long ? loadHistory(false) : history;
  const type = long ? 'long' : (opts.type || process.env.EDIT_TYPE || pickEditType({ stats, history }).type);
  if (!long && !TYPES.includes(type)) throw new Error(`unknown edit type "${type}" (${TYPES.join(', ')})`);
  let flavor = null;
  if (long) {
    flavor = opts.flavor || process.env.EDIT_LONG_FLAVOR;
    if (!flavor) {
      const recent = new Set((history.uploads || []).slice(-2).map(u => u.flavor));
      const done = scored(stats, history);
      const prior = done.length ? done.reduce((a, u) => a + u.learn, 0) / done.length : 0;
      let best = null;
      for (const f of LONG_FLAVORS.filter(x => !recent.has(x))) {
        const d = thompsonDraw(done.filter(u => u.flavor === f).map(u => ({ r: u.learn, ageDays: u.stat.ageDays })), prior);
        if (!best || d.draw > best.draw) best = { f, draw: d.draw };
      }
      flavor = best ? best.f : 'song';
    }
  }
  // songs / subjects avoid what the channel posted lately (shorts and long alike)
  const content = chooseContent(type, { stats, history: { uploads: [...(shortHistory.uploads || []), ...(long ? history.uploads || [] : [])] }, flavor });
  const song = content.song;
  logger.info(`Edit ${long ? `long (${flavor})` : type}: ${content.why}; song "${song.name}"`);
  const t0 = Date.now();

  // 1. the song + its analysis
  const src = await downloadSong(song, work);
  logger.info(`Song audio: ${src.sourceTitle}`);
  const analysis = path.join(work, 'music.json');
  const mus = await runPy([path.join(ROOT, 'core', 'edit', 'music.py'), '--audio', src.audio, '--out', analysis,
    ...(src.heatmap ? ['--heatmap', src.heatmap] : [])], 12);
  logger.info(`Music: ${mus.bpm} bpm (${mus.tracker}), ${Math.round(mus.duration)} s, sections ${(mus.sections || []).join(' ')}`);

  // 2. the clips (one fallback request when too few pass the quality gates)
  const usedFile = path.join(work, 'used.json');
  const recentIds = [...(shortHistory.uploads || []), ...(long ? history.uploads || [] : [])].slice(-30).flatMap(u => u.clipIds || []);
  fs.writeFileSync(usedFile, JSON.stringify(recentIds));
  const blockFile = path.join(work, 'blocked.json');
  fs.writeFileSync(blockFile, JSON.stringify(blockedChannels(stats, shortHistory)));
  const need = MIN_SHOTS[type] || 6;
  let lib = null;
  let libReq = content.library;
  const reqs = [content.library, ...(content.fallbacks || [])];
  for (let i = 0; i < reqs.length; i++) {
    try {
      const r = await runLibrary(reqs[i], work, { long, used: usedFile, blocked: blockFile, tag: i ? `fb${i}` : '' });
      logger.info(`Library (${reqs[i].mode} "${reqs[i].query}"): ${r.shots.length} shots`);
      if (r.shots.length >= need) { lib = r; libReq = reqs[i]; break; }
      if (!lib || r.shots.length > lib.shots.length) { lib = r; libReq = reqs[i]; }
    } catch (e) {
      logger.warn(`Library (${reqs[i].mode} "${reqs[i].query}") failed: ${(e.message || '').slice(0, 200)}`);
    }
  }
  if (!lib || lib.shots.length < Math.max(3, Math.ceil(need / 2))) {
    throw new Error(`not enough good clips (${lib ? lib.shots.length : 0}) for "${content.library.query}"`);
  }
  // a fallback changes what the video is about (the metadata must stay true)
  if (libReq !== content.library) {
    if (libReq.dropMember) { content.facts.member = null; content.subject.member = null; }
    if (libReq.multi) { content.facts.source = null; content.clipSource = null; content.planner.subtitle = null; }
    if (libReq.source) { content.facts.source = libReq.source; content.facts.group = libReq.source; content.facts.keyword = `${libReq.source} dance edit`;
      content.clipSource = libReq.source; content.planner.subtitle = libReq.source; }
    if (type === 'song' && libReq.mode === 'mood') { content.facts.source = null; content.clipSource = null; content.planner.subtitle = null; }
  }

  // 3. metadata (keyword-first title, 1-3 hashtags, energetic emoji; the on-video text is Latin only)
  const meta = await optimizeMetadata({ channel: 'meme', contentType: content.contentType, facts: content.facts });
  logger.info(`Metadata: "${meta.title}", on video "${meta.onVideoTitle}" (${Object.entries(meta.arms).map(([k, v]) => `${k} ${v}`).join(', ')})`);

  // 4. the plan
  const planPath = path.join(work, 'plan.json');
  const plannerType = type === 'long' ? 'long' : type;
  const pArgs = [path.join(ROOT, 'core', 'edit', 'planners.py'), '--type', plannerType, '--audio', src.audio, '--analysis', analysis,
    '--shots', lib.manifest, '--out', planPath, '--watermark', process.env.WATERMARK || '@zeroyenotaku', '--song', song.name];
  if (src.heatmap) pArgs.push('--heatmap', src.heatmap);
  if (song.artist) pArgs.push('--artist', song.artist);
  if (meta.onVideoTitle) pArgs.push('--title', meta.onVideoTitle);
  if (content.planner.subtitle) pArgs.push('--subtitle', content.planner.subtitle);
  if (content.planner.character) pArgs.push('--character', content.planner.character);
  if (content.planner.group) pArgs.push('--group', content.planner.group);
  if (content.planner.framing) pArgs.push('--framing', content.planner.framing);
  if (type === 'long') pArgs.push('--flavor', flavor);
  if (process.env.EDIT_TEMPLATE) pArgs.push('--template', process.env.EDIT_TEMPLATE);
  // Named preset pipelines (core/edit/presets.py): "this pipeline for these clips, that pipeline for those
  // clips". EDIT_PRESET pins one; otherwise the planner matches the song analysis and the clip pool against
  // every preset's selection signals. The chosen preset is logged and carried as an experiment factor.
  pArgs.push('--preset', process.env.EDIT_PRESET || 'auto');
  if (process.env.EDIT_SEED) pArgs.push('--seed', process.env.EDIT_SEED);
  const planned = await runPy(pArgs, type === 'sound' ? 12 : 5);
  logger.info(`Plan: ${planned.preset ? `${planned.preset} / ` : ''}${planned.template} (${planned.style}, ${planned.grade}), `
    + `${planned.duration} s, ${planned.shots} shots, `
    + `transitions ${Object.entries(planned.transitions || {}).map(([k, v]) => `${k} ${v}`).join(', ')}`);

  // 5. the render
  const outPath = path.join(outputDir, `edit-${type}${long ? `-${flavor}` : ''}-${Date.now()}.mp4`);
  const rendered = await runPy([path.join(ROOT, 'core', 'edit', 'engine.py'), '--plan', planPath, '--out', outPath], long ? 95 : 22,
    { allowNoJson: true });
  const videoPath = rendered.path || rendered.out || outPath;
  if (!fs.existsSync(videoPath)) throw new Error('the engine wrote no video');
  logger.success(`Rendered ${path.basename(videoPath)} in ${Math.round((Date.now() - t0) / 1000)} s`);

  const planJson = readJson(planPath, {});
  const clipIds = planned.clips || [...new Set((planJson.shots || []).map(s => s.source_id).filter(Boolean))];
  const clipChannels = planned.channels || [...new Set((planJson.shots || []).map(s => s.source_channel).filter(Boolean))];
  const credits = clipChannels.length ? `clips from: ${clipChannels.map(c => String(c).trim()).join(', ')}` : '';
  const settings = { style: planned.style, template: planned.template, preset: planned.preset || undefined,
    grade: planned.grade, frame: planJson.frame || null,
    shots: planned.shots, duration: planned.duration, libraryMode: libReq.mode, framing: content.planner.framing || undefined };
  const theme = song.theme === 'kpop' || type === 'kpop' ? 'kpop' : 'anime';
  const playlist = { song: 'anime edits', dance: 'dance edits', character: 'character edits', kpop: 'kpop edits', sound: 'sound edits',
    long: 'full edits' }[type];
  return {
    success: true,
    videoPath,
    title: meta.title,
    description: buildDescription(meta, credits),
    tags: meta.tags,
    categoryId: theme === 'kpop' ? '10' : '1',
    playlistTitle: playlist,
    comment: meta.comment,
    country: 'Global',
    category: `edit-${type}`,
    editType: 'edit',
    long,
    meme: {
      song: song.name, theme, emoji: meta.emoji, clipIds, clipChannels, followUpOf: null, source: src.source,
      clipSource: content.clipSource, settings, why: content.why, onVideoTitle: meta.onVideoTitle, meta: meta.arms, metaChoice: meta.choice,
      contentType: content.contentType, style: planned.style, template: planned.template, preset: planned.preset || undefined,
      flavor: flavor || undefined,
      character: content.subject.character || undefined, member: content.subject.member || undefined, group: content.subject.group || undefined,
      long,
    },
    workDir: work,
  };
}

/** Log an upload: history (the daily / weekly guards read it) + the experiment log + the metadata learner. */
function recordUpload(result, upload) {
  const m = result.meme;
  const long = !!(result.long || m.long);
  const h = loadHistory(long);
  h.uploads.push({
    videoId: upload.videoId, url: upload.url, postedAt: new Date().toISOString(), publishAt: upload.publishAt || null,
    contentType: m.contentType, song: m.song, theme: m.theme, emoji: m.emoji, title: result.title, clipIds: m.clipIds,
    clipChannels: m.clipChannels, clipSource: m.clipSource || undefined, settings: m.settings, onVideoTitle: m.onVideoTitle || undefined,
    meta: m.meta || undefined, style: m.style, template: m.template, flavor: m.flavor, character: m.character, member: m.member,
    group: m.group, long: long || undefined,
  });
  saveHistory(h, long);
  appendDecision({
    vid: upload.videoId, ch: 'meme', title: result.title,
    arm: { contentType: m.contentType, style: m.style, template: m.template, preset: m.preset || null, song: m.song, source: m.clipSource || null, theme: m.theme,
      character: m.character || null, member: m.member || null, flavor: m.flavor || null, long, ...(m.settings || {}), ...(m.meta || {}) },
    mode: m.why || 'pick',
  });
  if (m.metaChoice) recordMetadataChoice(m.metaChoice, { videoId: upload.videoId });
}

module.exports = {
  runEditPipeline, recordUpload, loadHistory, pickEditType, chooseContent, editSongs, characterPool, nsfwSets, minAge,
  TYPES, CONTENT_TYPE, EXTRA_SONGS, KPOP_MEMBERS, DANCE_SOURCES, SOUND_SOURCES,
};
