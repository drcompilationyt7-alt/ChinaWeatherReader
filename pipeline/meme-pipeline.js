/**
 * Meme acapella pipeline (Zero Yen Otaku, formerly Mr. WorldWideWebster): "<song> acapella" shorts in
 * the style of the fastest-growing meme channels, with an Asian / anime twist.
 *
 *   1. song: live trends ("<song> acapella" titles on YouTube in the last two
 *      weeks) + a curated list of meme songs, anime openings and K-pop hits;
 *      Thompson sampling on how our own uploads of each song did, and a hit
 *      gets remade (the same song with new clips) like the channels we copy
 *   2. audio: core/meme/acapella.py isolates the vocal and stacks our own
 *      bass / beatbox / harmony layers on it, then the original song drops
 *      in where the stack stops
 *   3. clips: core/meme/clip_finder.py cuts every clip from the song's own
 *      source (the anime whose opening it is, the K-pop group who sings it):
 *      one per layer in the layer's vibe, plus shots for the edit on the
 *      drop (performances of the song itself are synced to it); a small
 *      vision model (clip_judge.py) double-checks them
 *   4. edit: core/meme/render_meme.py tiles 1 -> 2 -> 3 -> 4 as the layers
 *      come in, then an AMV-style beat-cut edit on the original song
 *
 * memory/meme-history.json keeps every upload for the learners.
 */
const path = require('path');
const fs = require('fs');
const { execFile } = require('child_process');
const { Logger } = require('../core/logger');
const { loadStats } = require('../core/performance-tracker');
const { decay, appendDecision } = require('../core/experiment-log');

const logger = new Logger('MemeAcapella');
const ROOT = path.join(__dirname, '..');
const MEM = path.resolve(ROOT, process.env.MEMORY_DIR || 'memory');
const HISTORY_FILE = path.join(MEM, 'meme-history.json');

// name shown in the title -> YouTube search for the original song. Asia only:
// anime / J-pop, K-pop and Asian viral songs (the channel's niche). Every
// song has a source, and every clip in its short comes from that source:
// the anime whose opening it is, or the group / artist who sings it
// (kind "group": real footage). aliases: character / member names and
// native-script titles, which clip titles must mention.
const SONGS = [
  // anime openings
  { name: 'idol', query: 'YOASOBI Idol official audio', theme: 'anime', source: 'Oshi no Ko', kind: 'anime',
    aliases: ['oshi no ko', '推しの子', 'ai hoshino', 'hoshino ai', 'aqua hoshino', 'ruby hoshino', 'kana arima'] },
  { name: 'bling bang bang born', query: 'Creepy Nuts Bling-Bang-Bang-Born official audio', theme: 'anime', source: 'Mashle', kind: 'anime',
    aliases: ['mashle', 'マッシュル', 'mash burnedead', 'magic and muscles'] },
  { name: 'otonoke', query: 'Creepy Nuts Otonoke official audio', theme: 'anime', source: 'Dandadan', kind: 'anime',
    aliases: ['dandadan', 'dan da dan', 'ダンダダン', 'okarun', 'momo ayase', 'turbo granny'] },
  { name: 'kick back', query: 'Kenshi Yonezu KICK BACK official audio', theme: 'anime', source: 'Chainsaw Man', kind: 'anime',
    aliases: ['chainsaw man', 'チェンソーマン', 'denji', 'makima', 'power', 'aki hayakawa'] },
  { name: 'unravel', query: 'TK from Ling tosite sigure unravel official audio', theme: 'anime', source: 'Tokyo Ghoul', kind: 'anime',
    aliases: ['tokyo ghoul', '東京喰種', 'kaneki', 'touka'] },
  { name: 'gurenge', query: 'LiSA Gurenge official audio', theme: 'anime', source: 'Demon Slayer', kind: 'anime',
    aliases: ['demon slayer', 'kimetsu no yaiba', '鬼滅の刃', 'tanjiro', 'nezuko', 'zenitsu', 'inosuke', 'rengoku'] },
  { name: 'blue bird', query: 'Ikimonogakari Blue Bird official audio', theme: 'anime', source: 'Naruto', kind: 'anime',
    aliases: ['naruto', 'naruto shippuden', 'ナルト', 'sasuke', 'kakashi', 'itachi'] },
  { name: 'kaikai kitan', query: 'Eve Kaikai Kitan official audio', theme: 'anime', source: 'Jujutsu Kaisen', kind: 'anime',
    aliases: ['jujutsu kaisen', 'jjk', '呪術廻戦', 'gojo', 'itadori', 'sukuna', 'megumi', 'nobara'] },
  { name: 'specialz', query: 'King Gnu SPECIALZ official audio', theme: 'anime', source: 'Jujutsu Kaisen', kind: 'anime',
    aliases: ['jujutsu kaisen', 'jjk', '呪術廻戦', 'gojo', 'itadori', 'sukuna', 'toji', 'shibuya'] },
  { name: 'cha la head cha la', query: 'Cha-La Head-Cha-La Dragon Ball Z opening', theme: 'anime', source: 'Dragon Ball Z', kind: 'anime',
    aliases: ['dragon ball', 'dbz', 'ドラゴンボール', 'goku', 'vegeta', 'gohan', 'piccolo'] },
  { name: 'we are', query: 'One Piece opening We Are Hiroshi Kitadani', theme: 'anime', source: 'One Piece', kind: 'anime',
    aliases: ['one piece', 'ワンピース', 'luffy', 'zoro', 'sanji', 'nami'] },
  { name: 'pokemon theme', query: 'Pokemon theme song original English', theme: 'anime', source: 'Pokemon', kind: 'anime',
    aliases: ['pokemon', 'pokémon', 'ポケモン', 'pikachu', 'ash ketchum'] },
  // beyond the giants: loved mid-size and cult anime, so the channel isn't the same five shows every day
  { name: 'kiss of death', query: 'Mika Nakashima HYDE KISS OF DEATH official audio', theme: 'anime', source: 'Darling in the FranXX', kind: 'anime',
    aliases: ['darling in the franxx', 'franxx', 'ダーリン・イン・ザ・フランキス', 'zero two', 'hiro', 'ichigo'] },
  { name: 'hey world', query: 'Yuka Iguchi Hey World DanMachi opening', theme: 'anime', source: 'DanMachi', kind: 'anime',
    aliases: ['danmachi', 'dungeon ni deai', 'pick up girls in a dungeon', 'ダンまち', 'bell cranel', 'hestia', 'ais wallenstein'] },
  { name: 'nameless story', query: 'Takuma Terashima Nameless Story Tensura opening', theme: 'anime', source: 'Reincarnated as a Slime', kind: 'anime',
    aliases: ['reincarnated as a slime', 'tensura', 'tensei shitara slime', '転スラ', 'rimuru', 'milim', 'benimaru', 'veldora'] },
  { name: 'chika dance', query: 'Chikatto Chika Chika Konomi Kohara Kaguya-sama ending', theme: 'anime', source: 'Kaguya-sama', kind: 'anime',
    aliases: ['kaguya-sama', 'kaguya sama', 'love is war', 'かぐや様', 'chika', 'fujiwara chika', 'shirogane', 'kaguya shinomiya'] },
  { name: 'love dramatic', query: 'Masayuki Suzuki Love Dramatic Rikka Ihara official audio', theme: 'anime', source: 'Kaguya-sama', kind: 'anime',
    aliases: ['kaguya-sama', 'kaguya sama', 'love is war', 'かぐや様', 'chika', 'shirogane', 'kaguya shinomiya'] },
  { name: 'seishun complex', query: 'Kessoku Band Seishun Complex official audio', theme: 'anime', source: 'Bocchi the Rock', kind: 'anime',
    aliases: ['bocchi the rock', 'ぼっち・ざ・ろっく', 'bocchi', 'hitori gotoh', 'nijika', 'kessoku band'] },
  { name: '99', query: 'MOB CHOIR 99 Mob Psycho 100 opening', theme: 'anime', source: 'Mob Psycho 100', kind: 'anime',
    aliases: ['mob psycho', 'モブサイコ', 'shigeo', 'reigen', 'dimple'] },
  { name: 'rise', query: 'MADKID RISE Shield Hero opening official audio', theme: 'anime', source: 'Shield Hero', kind: 'anime',
    aliases: ['shield hero', '盾の勇者', 'naofumi', 'raphtalia', 'filo'] },
  { name: 'clattanoia', query: 'OxT Clattanoia Overlord opening', theme: 'anime', source: 'Overlord', kind: 'anime',
    aliases: ['overlord', 'オーバーロード', 'ainz', 'momonga', 'demiurge'] },
  { name: 'goya no machiawase', query: 'Hello Sleepwalkers Goya no Machiawase Noragami opening', theme: 'anime', source: 'Noragami', kind: 'anime',
    aliases: ['noragami', 'ノラガミ', 'yato', 'hiyori', 'yukine'] },
  { name: 'iro kousui', query: 'Yoh Kamiyama Iro Kousui Horimiya opening', theme: 'anime', source: 'Horimiya', kind: 'anime',
    aliases: ['horimiya', 'ホリミヤ', 'hori', 'miyamura'] },
  { name: 'hana ni natte', query: 'Ryokuoushoku Shakai Hana ni Natte Apothecary Diaries opening', theme: 'anime', source: 'The Apothecary Diaries', kind: 'anime',
    aliases: ['apothecary diaries', 'kusuriya', '薬屋のひとりごと', 'maomao', 'jinshi'] },
  { name: 'good morning world', query: 'BURNOUT SYNDROMES Good Morning World Dr. Stone opening', theme: 'anime', source: 'Dr. Stone', kind: 'anime',
    aliases: ['dr. stone', 'dr stone', 'ドクターストーン', 'senku', 'chrome', 'tsukasa'] },
  { name: 'resonance', query: 'T.M.Revolution Resonance Soul Eater opening', theme: 'anime', source: 'Soul Eater', kind: 'anime',
    aliases: ['soul eater', 'ソウルイーター', 'maka', 'death the kid', 'black star'] },
  { name: 'sorairo days', query: 'Shoko Nakagawa Sorairo Days Gurren Lagann opening', theme: 'anime', source: 'Gurren Lagann', kind: 'anime',
    aliases: ['gurren lagann', 'グレンラガン', 'simon', 'kamina'] },
  { name: 'alive', query: 'ClariS ALIVE Lycoris Recoil opening', theme: 'anime', source: 'Lycoris Recoil', kind: 'anime',
    aliases: ['lycoris recoil', 'リコリス・リコイル', 'chisato', 'takina'] },
  { name: 'highest', query: 'OxT HIGHEST Eminence in Shadow opening', theme: 'anime', source: 'The Eminence in Shadow', kind: 'anime',
    aliases: ['eminence in shadow', '陰の実力者', 'cid kagenou', 'shadow garden', 'i am atomic'] },
  { name: 'hanaichi monme', query: 'Vaundy Hanaichi Monme Sakamoto Days opening', theme: 'anime', source: 'Sakamoto Days', kind: 'anime',
    aliases: ['sakamoto days', 'サカモトデイズ', 'taro sakamoto', 'shin asakura'] },
  { name: 'redo', query: 'Konomi Suzuki Redo Re:Zero opening official audio', theme: 'anime', source: 'Re:Zero', kind: 'anime',
    aliases: ['re:zero', 'rezero', 're zero', 'リゼロ', 'subaru', 'emilia', 'rem'] },
  { name: 'mixed nuts', query: 'Official HIGE DANdism Mixed Nuts official audio', theme: 'anime', source: 'Spy x Family', kind: 'anime',
    aliases: ['spy x family', 'spy family', 'スパイファミリー', 'anya', 'loid', 'yor'] },
  { name: 'imagination', query: 'SPYAIR Imagination Haikyuu opening official audio', theme: 'anime', source: 'Haikyuu', kind: 'anime',
    aliases: ['haikyuu', 'haikyu', 'ハイキュー', 'hinata', 'kageyama', 'oikawa'] },
  { name: 'hacking to the gate', query: 'Kanako Ito Hacking to the Gate Steins Gate opening', theme: 'anime', source: 'Steins;Gate', kind: 'anime',
    aliases: ['steins gate', 'steins;gate', 'シュタインズ・ゲート', 'okabe', 'kurisu'] },
  { name: 'colors', query: 'FLOW COLORS Code Geass opening official audio', theme: 'anime', source: 'Code Geass', kind: 'anime',
    aliases: ['code geass', 'コードギアス', 'lelouch', 'suzaku'] },
  { name: 'again', query: 'YUI Again Fullmetal Alchemist Brotherhood opening', theme: 'anime', source: 'Fullmetal Alchemist', kind: 'anime',
    aliases: ['fullmetal alchemist', 'fma', 'brotherhood', '鋼の錬金術師', 'edward elric', 'roy mustang'] },
  { name: 'crossing field', query: 'LiSA crossing field Sword Art Online opening', theme: 'anime', source: 'Sword Art Online', kind: 'anime',
    aliases: ['sword art online', 'sao', 'ソードアート・オンライン', 'kirito', 'asuna'] },
  { name: 'chaos ga kiwamaru', query: 'UNISON SQUARE GARDEN Chaos ga Kiwamaru Blue Lock opening', theme: 'anime', source: 'Blue Lock', kind: 'anime',
    aliases: ['blue lock', 'ブルーロック', 'isagi', 'bachira', 'rin itoshi'] },
  { name: 'hikaru nara', query: 'Goose house Hikaru Nara Your Lie in April opening', theme: 'anime', source: 'Your Lie in April', kind: 'anime',
    aliases: ['your lie in april', 'shigatsu wa kimi no uso', '四月は君の嘘', 'kaori', 'kosei'] },
  // J-pop: the artist
  { name: 'night dancer', query: 'imase NIGHT DANCER official audio', theme: 'jpop', source: 'imase', kind: 'group',
    aliases: ['imase', 'night dancer'] },
  { name: 'shinunoga e-wa', query: 'Fujii Kaze Shinunoga E-Wa official audio', theme: 'jpop', source: 'Fujii Kaze', kind: 'group',
    aliases: ['fujii kaze', '藤井風', 'shinunoga e-wa'] },
  // K-pop: the group
  { name: 'apt', query: 'ROSE Bruno Mars APT official audio', theme: 'kpop', source: 'ROSÉ', kind: 'group',
    aliases: ['rosé', 'rose', '로제', 'blackpink rose', 'apt'] },
  { name: 'gangnam style', query: 'PSY Gangnam Style official audio', theme: 'kpop', source: 'PSY', kind: 'group',
    aliases: ['psy', '싸이', 'gangnam style'] },
  { name: 'golden', query: 'HUNTR/X Golden KPop Demon Hunters official audio', theme: 'kpop', source: 'KPop Demon Hunters', kind: 'anime',
    aliases: ['kpop demon hunters', 'k-pop demon hunters', 'huntr/x', 'huntrix', 'rumi', 'mira', 'zoey', 'saja boys'] },
  { name: 'soda pop', query: 'Saja Boys Soda Pop KPop Demon Hunters official audio', theme: 'kpop', source: 'KPop Demon Hunters', kind: 'anime',
    aliases: ['kpop demon hunters', 'k-pop demon hunters', 'saja boys', 'jinu', 'huntrix', 'rumi'] },
  { name: 'super shy', query: 'NewJeans Super Shy official audio', theme: 'kpop', source: 'NewJeans', kind: 'group',
    aliases: ['newjeans', 'new jeans', '뉴진스', 'minji', 'hanni', 'danielle', 'haerin', 'hyein'] },
  { name: 'hype boy', query: 'NewJeans Hype Boy official audio', theme: 'kpop', source: 'NewJeans', kind: 'group',
    aliases: ['newjeans', 'new jeans', '뉴진스', 'minji', 'hanni', 'danielle', 'haerin', 'hyein'] },
  { name: 'magnetic', query: 'ILLIT Magnetic official audio', theme: 'kpop', source: 'ILLIT', kind: 'group',
    aliases: ['illit', '아일릿', 'wonhee', 'minju', 'moka', 'iroha', 'yunah'] },
  { name: 'supernova', query: 'aespa Supernova official audio', theme: 'kpop', source: 'aespa', kind: 'group',
    aliases: ['aespa', '에스파', 'karina', 'winter', 'giselle', 'ningning'] },
  { name: 'how you like that', query: 'BLACKPINK How You Like That official audio', theme: 'kpop', source: 'BLACKPINK', kind: 'group',
    aliases: ['blackpink', '블랙핑크', 'jennie', 'lisa', 'jisoo', 'rosé'] },
  { name: 'dynamite', query: 'BTS Dynamite official audio', theme: 'kpop', source: 'BTS', kind: 'group',
    aliases: ['bts', '방탄소년단', 'jungkook', 'jimin', 'suga', 'j-hope', 'jin', 'taehyung'] },
  // Chinese
  { name: 'yi jian mei', query: 'Fei Yu-ching Yi Jian Mei 一剪梅 费玉清', theme: 'chinese', source: 'Fei Yu-ching', kind: 'group',
    aliases: ['fei yu-ching', 'fei yu ching', '费玉清', '費玉清', 'yi jian mei', '一剪梅', 'xue hua piao piao'] },
  { name: 'wu ji', query: 'The Untamed OST Wu Ji 无羁 Xiao Zhan Wang Yibo', theme: 'chinese', source: 'The Untamed', kind: 'group',
    aliases: ['the untamed', '陈情令', '陳情令', 'wei wuxian', 'lan wangji', 'xiao zhan', 'wang yibo', '无羁'] },
  { name: 'mojito', query: 'Jay Chou Mojito official audio 周杰倫', theme: 'chinese', source: 'Jay Chou', kind: 'group',
    aliases: ['jay chou', '周杰伦', '周杰倫', 'mojito'] },
  // Asian viral: the trend itself
  { name: 'linggang guli guli', query: 'linggang guli guli guli wacha song', theme: 'asian', source: 'Linggang Guli Guli', kind: 'group',
    aliases: ['linggang guli', 'guli guli', 'wacha'] },
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
    clipSource: result.meme.clipSource || undefined, settings: result.meme.settings || undefined,
  });
  saveHistory(h);
  appendDecision({ vid: upload.videoId, ch: 'meme', title: result.title,
    arm: { song: result.meme.song, source: result.meme.clipSource || null, theme: result.meme.theme, ...(result.meme.settings || {}) },
    mode: result.meme.followUpOf ? 'followup' : (result.meme.why || 'pick') });
}

// Production settings tried at random every day (independently), so each upload says a little about
// each of them; the weekly report compares them (core/reflect/weekly-report.js)
const SETTINGS = {
  drop: [10, 14, 18],            // seconds of the original song after the stack
  edit: [4, 6, 8],               // shots gathered for the drop edit
  hashtag: [true, false],        // #source in the title
  hook: ['layers', 'drop', 'question'],
  look: ['cinematic', 'dreamy', 'hype'],  // the drop edit's colour grade (core/meme/edit_fx.py LOOKS)
};
const HOOKS = {
  layers: (caption, e, src) => `${caption} but it's layer by layer ${e}${src ? ` (${src} edit at the end)` : ''}`,
  drop: (caption, e, src) => `wait for the drop ${e} ${caption}${src ? `, then a ${src} edit` : ''}`,
  question: (caption, e, src) => `which layer hits the hardest? ${e} ${caption}${src ? ` + ${src} edit` : ''}`,
};

function pickSettings() {
  const out = {};
  for (const [k, vals] of Object.entries(SETTINGS)) out[k] = vals[Math.floor(Math.random() * vals.length)];
  if (process.env.MEME_DROP) out.drop = Number(process.env.MEME_DROP);
  return out;
}

function gaussian() {
  let u = 0, v = 0;
  while (!u) u = Math.random();
  while (!v) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function scored(stats, history) {
  const byId = new Map(((stats && stats.videos) || []).map(v => [v.videoId, v]));
  const out = history.uploads.map(u => ({ ...u, stat: byId.get(u.videoId) }))
    .filter(u => u.stat && typeof u.stat.lscore === 'number' && (u.stat.ageDays || 0) >= 1.5);
  // learn from the fixed-checkpoint reward once every upload has one; older results count less
  const useReward = out.length && out.every(u => typeof u.stat.reward === 'number');
  return out.map(u => ({ ...u, learn: useReward ? u.stat.reward : u.stat.lscore, w: decay(u.stat.ageDays) }));
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
  const bySong = new Map(SONGS.map(x => [x.name, x.source]));
  const recentSources = new Set(history.uploads.slice(-7).map(u => u.clipSource || bySong.get(u.song)).filter(Boolean));
  const channelPrior = done.length ? done.reduce((a, u) => a + u.learn, 0) / done.length : 0;
  const wmean = obs => { const n = obs.reduce((a, u) => a + u.w, 0); return n ? obs.reduce((a, u) => a + u.w * u.learn, 0) / n : null; };
  let best = null;
  const seen = new Set();
  for (const s of [...trends, ...SONGS]) {
    if (recent.has(s.name) || seen.has(s.name) || (s.source && recentSources.has(s.source))) continue;
    seen.add(s.name);
    // pooled prior: a new song starts from how its anime / group did, else the channel
    const srcObs = s.source ? done.filter(u => (u.clipSource || bySong.get(u.song)) === s.source) : [];
    const prior = srcObs.length ? (wmean(srcObs) + channelPrior) / 2 : channelPrior;
    const mine = done.filter(u => u.song === s.name);
    const n = mine.reduce((a, u) => a + u.w, 0);
    const mean = (mine.reduce((a, u) => a + u.w * u.learn, 0) + prior) / (n + 1) + (!mine.length && s.trendCount ? 0.4 : 0);
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
  // keep the first meaningful chunk ("Levi vs Beast Titan - Attack on Titan S3" -> "levi vs beast titan")
  const chunk = String(t).replace(/#[^\s#]+/g, ' ').replace(/[\[(【「].*?[\])】」]/g, ' ')
    .split(/\s[-|–—:]\s|\s*\|\s*|｜/).map(x => x.trim()).find(x => x.length >= 4) || '';
  // captions are drawn in a Latin font for a global audience: CJK-only titles need the LLM
  if (/[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/.test(chunk)) return '';
  const words = chunk
    .replace(/\b(shorts?|funny|viral|fyp|foryou|tiktok|douyin|compilation|part \d+|ep\.? ?\d+|season \d+|s\d+)\b/gi, ' ')
    .replace(/[|~_*]+/g, ' ').replace(/\s+/g, ' ').trim().toLowerCase().split(' ');
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

  const settings = pickSettings();
  logger.info(`Settings: drop ${settings.drop}s, ${settings.edit} edit shots, hashtag ${settings.hashtag ? 'on' : 'off'}, hook ${settings.hook}, look ${settings.look}`);
  const t0 = Date.now();
  const src = await downloadSong(song, work);
  logger.info(`Source audio: ${src.sourceTitle}`);
  const acapArgs = [path.join(ROOT, 'core', 'meme', 'acapella.py'), '--audio', src.audio, '--out-dir', path.join(work, 'acapella'),
    '--drop', String(settings.drop)];
  if (src.heatmap) acapArgs.push('--heatmap', src.heatmap);
  const acap = await run(python(), acapArgs, 15);
  logger.success(`Acapella: ${acap.bpm} bpm, ${acap.duration}s${acap.drop ? `, original song from ${acap.drop.source_start}s` : ''}`);

  const recentClips = history.uploads.slice(-30).flatMap(u => u.clipIds || []);
  const usedFile = path.join(work, 'used.json');
  fs.writeFileSync(usedFile, JSON.stringify(recentClips));
  const blockFile = path.join(work, 'blocked.json');
  const blocked = blockedChannels(stats, history);
  fs.writeFileSync(blockFile, JSON.stringify(blocked));
  if (blocked.length) logger.info(`Skipping ${blocked.length} clip channels that got videos blocked`);
  const options = CLIP_THEMES[song.theme] || ['funny', 'cute'];
  // the kind of footage (the judge checks anime vs real): the source's kind decides it
  const theme = song.source ? (song.kind === 'anime' ? 'anime' : { kpop: 'kpop', jpop: 'japanese', chinese: 'chinese' }[song.theme] || 'funny')
    : options[Math.floor(Math.random() * options.length)];
  const baseArgs = [path.join(ROOT, 'core', 'meme', 'clip_finder.py'), '--out-dir', path.join(work, 'clips'),
    '--theme', theme, '--used', usedFile, '--block', blockFile];
  if (process.env.YT_COOKIES && fs.existsSync(process.env.YT_COOKIES)) baseArgs.push('--cookies', process.env.YT_COOKIES);
  let clips = null;
  if (song.source) {
    logger.info(`Clip source: ${song.source} (${song.kind})`);
    const srcArgs = [...baseArgs, '--source', song.source, '--source-kind', song.kind || 'anime',
      '--aliases', (song.aliases || []).join(','), '--song-name', song.name, '--edit', String(settings.edit)];
    if (acap.drop) {
      srcArgs.push('--song-audio', src.audio, '--song-start', String(acap.drop.source_start),
        '--song-len', String(Math.round((acap.drop.source_end - acap.drop.source_start) * 1000) / 1000));
    }
    try {
      clips = await run(python(), srcArgs, 14);
      logger.success(`Clips from ${song.source}: ${clips.count} tiles, ${clips.edit_count || 0} edit shots (${clips.aligned_count || 0} synced to the song)`);
    } catch (e) {
      logger.warn(`Not enough ${song.source} clips (${(e.message || '').slice(0, 100)}), using ${theme} clips`);
    }
  }
  if (!clips) {
    logger.info(`Clip theme: ${theme}`);
    clips = await run(python(), baseArgs, 12);
    logger.success(`Clips: ${clips.count}`);
  }
  // second opinion from a small vision model (SigLIP): vibe, theme, quality, safety
  try {
    const judged = await run(python(), [path.join(ROOT, 'core', 'meme', 'clip_judge.py'), '--clips', clips.manifest, '--theme', theme,
      ...(clips.source ? ['--no-vibe'] : [])], 8);
    if (judged.skipped) logger.warn(`Clip judge skipped: ${judged.skipped}`);
    else logger.info(`Clip judge kept ${judged.kept}, dropped ${(judged.rejected || []).length}: ${(judged.rejected || []).map(r => r[1]).join('; ')}`);
  } catch (e) {
    logger.warn(`Clip judge failed (clips used unjudged): ${(e.message || '').slice(0, 120)}`);
  }
  const captioned = await captionClips(clips.manifest, theme);
  if (captioned) logger.info(`Captioned ${captioned} clips`);

  const emoji = EMOJI_PAIRS[Math.floor(Math.random() * EMOJI_PAIRS.length)];
  const caption = `${song.name} acapella`;
  const outPath = path.join(outputDir, `meme-${Date.now()}.mp4`);
  const timeline = acap.timeline || path.join(work, 'acapella', 'timeline.json');
  const audio = acap.acapella || acap.mix || path.join(work, 'acapella', 'acapella.wav');
  const renderArgs = [path.join(ROOT, 'core', 'meme', 'render_meme.py'), '--audio', audio, '--timeline', timeline,
    '--clips', clips.manifest, '--title', caption, '--emoji', emoji, '--out', outPath];
  if (clips.source) renderArgs.push('--subtitle', song.source);
  renderArgs.push('--look', settings.look, '--layout', 'letterbox');
  const res = await run(python(), renderArgs, 20);
  logger.success(`Rendered ${res.duration}s, ${res.cuts} cuts (${res.editShots || 0} in the drop edit, ${res.editAligned || 0} synced, `
    + `${res.soundEditHits || 0} sound hits) in ${Math.round((Date.now() - t0) / 1000)}s`);

  const tag = clips.source && settings.hashtag ? song.source.normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^A-Za-z0-9]/g, '').toLowerCase() : '';
  const title = `${caption} ${emoji.split(',').join('')}${tag ? ` #${tag}` : ''}`;
  const tagsByTheme = { anime: ['anime', 'anime memes', 'anime funny moments'], kpop: ['kpop', 'kpop memes', 'kpop funny moments'],
    japanese: ['japan', 'japanese memes', 'funny japan'], chinese: ['china', 'douyin', 'chinese memes'],
    funny: ['asian memes', 'funny asian', 'douyin'], cute: ['cute', 'cute asian', 'cute animals'] };
  return {
    success: true,
    videoPath: res.path,
    title,
    description: HOOKS[settings.hook](caption, emoji.split(',')[0], clips.source ? song.source : null)
      + `\n\nwhich layer hit the hardest? 👇\n\n`
      + (res.channelsUsed.length ? `clips from: ${res.channelsUsed.map(c => c.trim()).join(', ')}\n` : '')
      + `#acapella #${{ anime: 'anime', kpop: 'kpop', japanese: 'japan', chinese: 'douyin', cute: 'cute' }[theme] || 'asianmemes'} #shorts`,
    tags: ['acapella', `${song.name} acapella`, song.name, ...(clips.source ? [song.source, `${song.source} edit`] : []), 'memes',
      ...(tagsByTheme[theme] || [])],
    categoryId: '23',
    playlistTitle: `${theme} acapella`,
    comment: 'which layer was the best? 😭',
    country: 'Global',
    category: `meme-${theme}`,
    editType: 'meme',
    meme: { song: song.name, theme, emoji, clipIds: res.clipsUsed, clipChannels: res.channelsUsed, followUpOf: song.followUpOf || null,
      source: src.source, clipSource: clips.source ? song.source : null, settings, why: song.why },
    workDir: work,
  };
}

module.exports = { runMemePipeline, recordUpload, loadHistory, pickSong, trendingSongs, cleanTitle, SONGS };
