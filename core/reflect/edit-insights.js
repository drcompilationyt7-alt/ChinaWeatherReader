/**
 * "Why did it do well?" for Zero Yen Otaku (stem edits + sound edits): attribution on our own uploads, small and explainable.
 *
 *   const ins = computeInsights({ stats, history });     // also writes <MEMORY_DIR>/edit-insights.json
 *   songBonus(ins, song)        -> draw bonus for pickSong (artist / band / singer, vibe, anime / group, genre)
 *   titlePattern(title, facts)  -> "{song} acapella {artist} {e} #{tag}" (the title's structure, the pattern id)
 *   mimicTemplates(ins, pool)   -> the winning title patterns of a pool, as metadata templates
 *
 * Model: y = lscore (log views per day, the metric pickSong already learns from). Each upload's residual is y minus the median of
 * the uploads posted within +-4 days of it (the channel jumped ~50x on 2026-10-10: a time-local baseline keeps "posted after the
 * jump" from looking like a feature). Per feature value: shrunk mean residual, sum(r) / (n + K) with K = 4 (strong shrinkage: one
 * upload barely moves it). The top lists need n >= 2.
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..', '..');
const MEM = () => path.resolve(ROOT, process.env.MEMORY_DIR || 'memory');
const FILE = () => path.join(MEM(), 'edit-insights.json');
const K = 4;
const WINDOW_DAYS = 4;
const readJson = (f, d) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return d; } };
const lower = s => String(s || '').toLowerCase().trim();
const median = xs => { const s = [...xs].sort((a, b) => a - b); return s.length ? (s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2) : 0; };
const r2 = x => Math.round(x * 100) / 100;
const EMOJI_RUN = /(?:\p{Extended_Pictographic}\u{FE0F}?(?:\u{200D}\p{Extended_Pictographic}\u{FE0F}?)*\s*)+/gu;

// artist / band / singer and the song's vibe (hype | bright | dark | emotional | chill) for the curated stem songs;
// auto-added songs carry their artist (config/song-candidates.json), their vibe stays unknown
const SONG_INFO = {
  idol: ['YOASOBI', 'hype'], 'bling bang bang born': ['Creepy Nuts', 'hype'], otonoke: ['Creepy Nuts', 'hype'], 'kick back': ['Kenshi Yonezu', 'hype'],
  unravel: ['TK from Ling tosite sigure', 'dark'], gurenge: ['LiSA', 'hype'], 'blue bird': ['Ikimonogakari', 'bright'], 'kaikai kitan': ['Eve', 'dark'],
  specialz: ['King Gnu', 'dark'], 'cha la head cha la': ['Hironobu Kageyama', 'bright'], 'we are': ['Hiroshi Kitadani', 'bright'],
  'pokemon theme': ['Jason Paige', 'bright'], 'kiss of death': ['Mika Nakashima', 'dark'], 'hey world': ['Yuka Iguchi', 'bright'],
  'nameless story': ['Takuma Terashima', 'bright'], 'chika dance': ['Konomi Kohara', 'bright'], 'love dramatic': ['Masayuki Suzuki', 'bright'],
  'seishun complex': ['Kessoku Band', 'hype'], 99: ['MOB CHOIR', 'hype'], rise: ['MADKID', 'hype'], clattanoia: ['OxT', 'dark'],
  'goya no machiawase': ['Hello Sleepwalkers', 'hype'], 'iro kousui': ['Yoh Kamiyama', 'emotional'], 'hana ni natte': ['Ryokuoushoku Shakai', 'bright'],
  'good morning world': ['BURNOUT SYNDROMES', 'hype'], resonance: ['T.M.Revolution', 'hype'], 'sorairo days': ['Shoko Nakagawa', 'hype'],
  alive: ['ClariS', 'bright'], highest: ['OxT', 'dark'], 'hanaichi monme': ['Vaundy', 'hype'], redo: ['Konomi Suzuki', 'dark'],
  'mixed nuts': ['Official HIGE DANdism', 'bright'], imagination: ['SPYAIR', 'hype'], 'hacking to the gate': ['Kanako Ito', 'dark'],
  colors: ['FLOW', 'hype'], again: ['YUI', 'emotional'], 'crossing field': ['LiSA', 'hype'], 'chaos ga kiwamaru': ['UNISON SQUARE GARDEN', 'hype'],
  'hikaru nara': ['Goose house', 'emotional'], 'night dancer': ['imase', 'chill'], 'shinunoga e-wa': ['Fujii Kaze', 'chill'],
  'yoru ni kakeru': ['YOASOBI', 'bright'], odoriko: ['Vaundy', 'chill'], apt: ['ROSÉ', 'bright'], 'gangnam style': ['PSY', 'hype'],
  golden: ['HUNTR/X', 'hype'], 'soda pop': ['Saja Boys', 'bright'], 'super shy': ['NewJeans', 'chill'], 'hype boy': ['NewJeans', 'chill'],
  magnetic: ['ILLIT', 'bright'], supernova: ['aespa', 'hype'], 'how you like that': ['BLACKPINK', 'hype'], dynamite: ['BTS', 'bright'],
  'pink venom': ['BLACKPINK', 'hype'], 'love dive': ['IVE', 'bright'], antifragile: ['LE SSERAFIM', 'hype'], cupid: ['FIFTY FIFTY', 'chill'],
  "god's menu": ['Stray Kids', 'hype'], eta: ['NewJeans', 'bright'],
};
// sound edits: the music pool's vibe word -> the same scale
const SOUND_VIBE = { impact: 'hype', hype: 'hype', aura: 'dark', dark: 'dark', sad: 'emotional', emotional: 'emotional', chill: 'chill', dreamy: 'chill' };

/** {artist, vibe} of a song entry (SONGS / approved candidates) or a bare name. */
function songInfo(s) {
  const name = lower(typeof s === 'string' ? s : s && s.name);
  const known = SONG_INFO[name] || [];
  const obj = typeof s === 'object' && s ? s : {};
  const artist = obj.artist || known[0] || (obj.kind === 'group' ? obj.source : null);
  return { artist: artist ? lower(artist) : null, vibe: obj.vibe || known[1] || null };
}

// ─── title structure ─────────────────────────────────────────────

/** A title's structure: song / artist / source -> placeholders, emoji runs -> {e}, hashtags -> #{tag}. */
function titlePattern(title, facts = {}) {
  let t = ` ${lower(title)} `;
  const sub = (val, ph) => {
    const v = lower(val);
    if (!v || v.length < 2) return;
    const esc = v.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    t = t.replace(new RegExp(`(^|[^\\p{L}\\p{N}])${esc}(?=[^\\p{L}\\p{N}]|$)`, 'gu'), `$1${ph}`);
  };
  t = t.replace(/#[\p{L}\p{N}_]+/gu, ' #{tag} ');
  // longest first ("yoru ni kakeru" before "yoru")
  for (const [v, ph] of [[facts.song, '{song}'], [facts.artist, '{artist}'], [facts.source, '{source}']].sort((a, b) => lower(b[0]).length - lower(a[0]).length)) sub(v, ph);
  t = t.replace(EMOJI_RUN, ' {e} ');
  return t.replace(/\s+/g, ' ').replace(/\(\s+/g, '(').replace(/\s+\)/g, ')').trim();
}

/** The hook phrase: what the title says after the keyword ("but the beat drops"), or null. */
function hookOf(pattern) {
  const m = String(pattern).match(/(?:acapella|edit)\s+((?:but|when|goes|is|hits|with|on)\b[^{#]*)/);
  return m ? m[1].trim() : null;
}

function features(u, songsByName) {
  const ct = u.contentType || 'stem-edit';
  const s = songsByName.get(lower(u.song)) || { name: u.song };
  const info = songInfo(s);
  const facts = { song: u.song, artist: info.artist, source: u.clipSource || s.source };
  const pat = u.meta && u.meta.titlePattern ? u.meta.titlePattern : titlePattern(u.title || '', facts);
  const emojis = (String(u.title || '').match(/\p{Extended_Pictographic}/gu) || []);
  const d = new Date(u.publishAt || u.postedAt || 0);
  const vibe = ct === 'sound-edit' ? (SOUND_VIBE[u.vibe] || u.vibe || null) : info.vibe;
  const f = {
    type: ct,
    song: lower(u.song),
    artist: info.artist,
    // a group's source is its artist (counted once, as the artist)
    source: ct === 'stem-edit' && s.kind !== 'group' ? lower(facts.source) || null : null,
    genre: ct === 'stem-edit' ? (s.theme || u.theme || null) : null,
    vibe,
    bpm: u.bpm ? (u.bpm < 100 ? '<100' : u.bpm < 130 ? '100-129' : '130+') : null,
    titlePattern: pat,
    hook: hookOf(pat),
    titleTag: /#\{tag\}/.test(pat) ? 'yes' : 'no',
    titleArtist: /\{artist\}/.test(pat) ? 'yes' : 'no',
    emoji: emojis.includes('🗿') ? 'moai' : emojis.includes('🎧') ? 'headphones' : emojis.includes('🔥') ? 'fire' : emojis.length ? 'other' : 'none',
    titleLen: String(u.title || '').length < 35 ? 'short' : String(u.title || '').length < 50 ? 'mid' : 'long',
    remake: u.followUpOf ? 'yes' : 'no',
    weekday: Number.isFinite(d.getTime()) && d.getTime() > 0 ? ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'][d.getUTCDay()] : null,
  };
  return f;
}

function loadSongs() {
  let songs = [];
  try { songs = require('../../pipeline/meme-pipeline').SONGS || []; } catch {}
  return new Map(songs.map(s => [lower(s.name), s]));
}

/**
 * Attribution over every scored upload (age >= 1.5 days).
 * @returns {object} {at, n, baseline, effects: {feature: {value: {effect, n, mean}}}, top, bottom, why, titlePatterns, rows}
 */
function computeInsights({ stats = null, history = null, songs = null, write = true } = {}) {
  const st = stats || readJson(path.join(MEM(), 'performance-stats.json'), { videos: [] });
  const h = history || readJson(path.join(MEM(), 'meme-history.json'), { uploads: [] });
  const byName = songs ? new Map(songs.map(s => [lower(s.name), s])) : loadSongs();
  const byId = new Map(((st && st.videos) || []).map(v => [v.videoId, v]));
  const rows = (h.uploads || []).map(u => ({ u, s: byId.get(u.videoId) }))
    .filter(x => x.s && Number.isFinite(x.s.lscore) && (x.s.ageDays || 0) >= 1.5)
    .map(x => ({ ...x, t: new Date(x.u.publishAt || x.u.postedAt).getTime(), y: x.s.lscore }));
  for (const r of rows) {
    let near = rows.filter(o => o !== r && Math.abs(o.t - r.t) <= WINDOW_DAYS * 86400000);
    if (near.length < 3) near = rows.filter(o => o !== r).sort((a, b) => Math.abs(a.t - r.t) - Math.abs(b.t - r.t)).slice(0, 4);
    r.base = near.length ? median(near.map(o => o.y)) : r.y;
    r.resid = r.y - r.base;
    r.f = features(r.u, byName);
  }
  const effects = {};
  for (const r of rows) {
    for (const [k, v] of Object.entries(r.f)) {
      if (v === null || v === undefined) continue;
      const g = ((effects[k] = effects[k] || {})[v] = effects[k][v] || { n: 0, s: 0 });
      g.n++; g.s += r.resid;
    }
  }
  const flat = [];
  for (const [k, vals] of Object.entries(effects)) {
    for (const [v, g] of Object.entries(vals)) {
      g.effect = r2(g.s / (g.n + K)); g.mean = r2(g.s / g.n); delete g.s;
      if (k !== 'type' && k !== 'weekday' && k !== 'titleLen') flat.push({ feature: k, value: v, effect: g.effect, n: g.n });
    }
  }
  const strong = flat.filter(x => x.n >= 2);
  const top = strong.filter(x => x.effect > 0).sort((a, b) => b.effect - a.effect).slice(0, 10);
  const bottom = strong.filter(x => x.effect < 0).sort((a, b) => a.effect - b.effect).slice(0, 8);
  const eff = (k, v) => (effects[k] && effects[k][v] ? effects[k][v].effect : 0);
  const why = [...rows].sort((a, b) => b.resid - a.resid).slice(0, 3).map(r => {
    const drivers = Object.entries(r.f).filter(([k, v]) => v && !['type', 'weekday', 'titleLen', 'song'].includes(k))
      .map(([k, v]) => ({ k, v, e: eff(k, v) })).filter(x => x.e > 0.02).sort((a, b) => b.e - a.e).slice(0, 3);
    return `"${r.u.title}" (${r.s.views} views, ${r.resid >= 0 ? '+' : ''}${r2(r.resid)} vs its week): `
      + (drivers.length ? drivers.map(x => `${x.k} ${x.v} (${x.e >= 0 ? '+' : ''}${x.e})`).join(', ') : 'no shared driver yet (song-specific)');
  });
  // title patterns per content type, best first, with one real example each
  const titlePatterns = {};
  for (const r of rows) {
    const ct = r.f.type;
    const p = r.f.titlePattern;
    const list = (titlePatterns[ct] = titlePatterns[ct] || {});
    const g = (list[p] = list[p] || { n: 0, s: 0, example: r.u.title });
    g.n++; g.s += r.resid;
    if (r.resid > (g.bestResid ?? -Infinity)) { g.bestResid = r.resid; g.example = r.u.title; }
  }
  for (const ct of Object.keys(titlePatterns)) {
    titlePatterns[ct] = Object.entries(titlePatterns[ct]).map(([pattern, g]) => ({ pattern, effect: r2(g.s / (g.n + K)), n: g.n, example: g.example }))
      .sort((a, b) => b.effect - a.effect);
  }
  const out = {
    at: new Date().toISOString(), n: rows.length,
    model: `residual = lscore - median lscore of uploads within +-${WINDOW_DAYS} days; effect = sum(residual) / (n + ${K})`,
    top, bottom, why, titlePatterns, effects,
    rows: rows.map(r => ({ videoId: r.u.videoId, song: r.u.song, views: r.s.views, resid: r2(r.resid), why: r.u.whyMode || undefined, pattern: r.f.titlePattern })),
  };
  if (write) {
    try { fs.mkdirSync(MEM(), { recursive: true }); fs.writeFileSync(FILE(), JSON.stringify(out, null, 1)); } catch {}
  }
  return out;
}

function loadInsights() {
  return readJson(FILE(), null);
}

/** Learned-feature bonus for a candidate song (similar artist / vibe / anime / genre), clipped to [-0.6, 0.8]. */
function songBonus(ins, s) {
  if (!ins || !ins.effects) return 0;
  const e = ins.effects;
  const info = songInfo(s);
  const get = (k, v) => (v && e[k] && e[k][lower(v)] ? e[k][lower(v)].effect : (v && e[k] && e[k][v] ? e[k][v].effect : 0));
  const b = get('artist', info.artist) + get('vibe', info.vibe) + get('source', s.source) + 0.5 * get('genre', s.theme);
  return Math.max(-0.6, Math.min(0.8, b));
}

/** Stem / sound pool -> the history contentType. */
const poolType = pool => String(pool || '').split('/')[1] || 'stem-edit';

/**
 * The winning title patterns of a pool as metadata templates ({song} {artist} {source} {e} #{sourceTag}), effect > 0 first.
 * Each: {pattern, template, effect, n}.
 */
function mimicTemplates(ins, pool) {
  const list = (ins && ins.titlePatterns && ins.titlePatterns[poolType(pool)]) || [];
  const tagVar = poolType(pool) === 'stem-edit' ? '{sourceTag}' : '{subjectTag}';
  return list.filter(p => p.effect > 0 && /\{(song|artist|source)\}/.test(p.pattern))
    .map(p => ({ ...p, template: p.pattern.replace(/#\{tag\}/g, `#${tagVar}`) }));
}

/**
 * "Similar to hit X" seeds for the monthly song top-up: a strong artist / vibe / anime whose unused songs are running out.
 * Returns [{feature, value, hit, effect, n, have}].
 */
function similarSeeds(ins, songs, history, { minEffect = 0.15, maxHave = 2 } = {}) {
  if (!ins || !ins.top) return [];
  const used = new Set((history.uploads || []).slice(-30).map(u => lower(u.song)));
  const out = [];
  for (const t of ins.top.filter(x => ['artist', 'source', 'vibe'].includes(x.feature) && x.effect >= minEffect)) {
    const match = s => (t.feature === 'artist' ? songInfo(s).artist === t.value : t.feature === 'vibe' ? songInfo(s).vibe === t.value : lower(s.source) === t.value);
    const have = songs.filter(s => match(s) && !used.has(lower(s.name))).length;
    if (t.feature === 'vibe' || have > maxHave) continue;   // a vibe is too broad to seed by name; plenty left = no need
    const hitRow = (ins.rows || []).filter(r => {
      const s = songs.find(x => lower(x.name) === lower(r.song));
      return s && match(s);
    }).sort((a, b) => b.resid - a.resid)[0];
    out.push({ feature: t.feature, value: t.value, hit: hitRow ? hitRow.song : null, effect: t.effect, n: t.n, have });
  }
  return out.slice(0, 4);
}

/** One line for the weekly reflection. */
function summaryLine(ins) {
  if (!ins || !ins.n) return 'Insights: no scored uploads yet.';
  const f = x => `${x.feature} ${x.value} (${x.effect >= 0 ? '+' : ''}${x.effect}, n=${x.n})`;
  const mix = {};
  for (const r of ins.rows || []) if (r.why) mix[r.why] = (mix[r.why] || 0) + 1;
  return `Insights (${ins.n} scored uploads): driving views ${ins.top.slice(0, 3).map(f).join('; ') || '-'}; holding back `
    + `${ins.bottom.slice(0, 2).map(f).join('; ') || '-'}${Object.keys(mix).length ? `; picks ${Object.entries(mix).map(([k, v]) => `${k} ${v}`).join(', ')}` : ''}.`;
}

module.exports = { computeInsights, loadInsights, songBonus, songInfo, titlePattern, hookOf, mimicTemplates, similarSeeds, summaryLine, features, SONG_INFO, FILE };
