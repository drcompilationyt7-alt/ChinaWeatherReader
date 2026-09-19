/**
 * What the metadata optimizer can choose from, per pool (= channel / content type).
 *
 * Five dimensions, each a set of bandit arms (core/metadata/learner.js learns which ones earn views):
 *   titleStyle  how the YouTube title is written: a template, or a style the LLM writes in
 *               (llm: true) with a template arm as fallback when the LLM is down
 *   ovStyle     the title drawn on the video: <= 26 characters, no emoji (drawn as squares)
 *   hashtagSet  1-3 specific hashtags, and whether one goes in the title
 *   emojiSet    an energetic emoji family (title end + images next to the on-video title)
 *   descHook    the description's first line (the part search results show)
 *
 * Template facts: {song} {source} {artist} {character} {group} {subject} {kw} are lowercase
 * (the channels' meme voice), {Source} {Song} keep their case, {sourceTag} ... are hashtag
 * slugs, {suggest} is a real YouTube autocomplete phrase, {e} the emoji, {e1} the first one.
 * The weekly step (core/metadata/evolve.js) adds arms to these seeds and retires losers;
 * seeds added here later simply join the pool.
 */
const { slugTag, contains } = require('./rules');

const lower = s => (s == null || s === '' ? null : String(s).toLowerCase().trim());
const THEME_TAG = { anime: 'anime', kpop: 'kpop', jpop: 'jpop', chinese: 'cpop', japanese: 'jpop' };
const EDIT_TAG = { anime: 'animeedit', kpop: 'kpopedit', jpop: 'amv', chinese: 'edit', japanese: 'amv' };

// energetic only (no sad / crying faces); "a,b" = two emoji, drawn as images next to the on-video title
const EDIT_EMOJI = [
  { id: 'fire', pool: ['🔥,🎧', '⚡,🔥', '🔥,🔥', '🎶,🔥'] },
  { id: 'music', pool: ['🎧,✨', '🎶,🎧', '💿,✨', '🎧,🎶'] },
  { id: 'spark', pool: ['✨,⚡', '⚡,⚡', '✨,🔥'] },
  { id: 'moai', pool: ['🗿', '🗿,🔥'] },
];

// ─── content types ───────────────────────────────────────────────

/** Channel "Zero Yen Otaku": the stem edit (today's daily short). */
const STEM_EDIT = {
  channel: 'meme',
  channelName: 'Zero Yen Otaku (anime, J-pop and K-pop edit Shorts)',
  lowerOnVideo: true,
  describe: f => `The song "${f.song}"${f.artist ? ` by ${f.artist}` : ''} is rebuilt from its real stems, one layer at a time `
    + `(the vocals alone first, then + drums, + bass, + the melody), each layer over a new clip${f.source ? ` of ${f.source}` : ''}; `
    + `the video's tiles multiply as layers come in, then the full song drops with a beat-synced ${f.source ? `${f.source} ` : ''}edit.`,
  keyword: f => `${lower(f.song)} acapella`,
  must: f => [[lower(f.song)], ['acapella', 'a cappella']],
  searchSeeds: f => [`${lower(f.song)} acapella`, f.source && `${lower(f.source)} edit`],
  // an autocomplete phrase can be used as the title when it has these
  suggestMust: f => [[lower(f.song)], ['acapella', 'a cappella']],
  cta: () => 'which layer hit the hardest? 👇',
  comments: ['which layer hit the hardest? 🎧👇', 'what song should get the stems treatment next? 🔥👇',
    'vocals, drums or bass: which layer carried? ⚡👇'],
  tags: f => ['acapella', `${lower(f.song)} acapella`, lower(f.song), f.source, f.source && `${f.source} edit`, 'stems', 'memes'],
  seeds: {
    titleStyle: [
      { id: 'kw-emoji', template: '{song} acapella {e}' },
      { id: 'kw-source', template: '{song} acapella ({source} edit) {e}' },
      { id: 'kw-suggest', template: '{suggest} {e}' },
      { id: 'llm-hook', llm: true, fallback: 'kw-emoji', example: '{song} acapella but the drums come in {e}',
        guide: 'the keyword "<song> acapella" first, then a short hype or curiosity tail about the stems building up or the drop, lowercase meme voice' },
      { id: 'llm-free', llm: true, fallback: 'kw-source', example: '{song} acapella ({source} edit) {e}',
        guide: 'your best algorithm-aware title for this exact Short: the keyword first, may name the anime or group' },
    ],
    ovStyle: [
      { id: 'ov-kw', templates: ['{song} acapella', '{song}'] },
      { id: 'ov-layers', templates: ['{song} layer by layer', 'layer by layer'] },
      { id: 'ov-drop', templates: ['wait for the drop'] },
      { id: 'ov-question', templates: ['which layer hits hardest?'] },
      { id: 'ov-llm', llm: true, fallback: 'ov-kw', example: 'the drop goes crazy',
        guide: 'lowercase meme voice or bold hype, readable in half a second, makes the viewer stay for the drop; may use the song name' },
    ],
    hashtagSet: [
      { id: 'niche3', desc: ['acapella', '{sourceTag}', '{themeTag}'] },
      { id: 'title-source', title: ['{sourceTag}'], desc: ['acapella', '{themeTag}'], fallback: 'niche3' },
      { id: 'edit-community', desc: ['{sourceTag}', '{editTag}', 'acapella'] },
      { id: 'minimal', desc: ['{sourceTag}'] },
    ],
    emojiSet: EDIT_EMOJI,
    descHook: [
      // the first three are the old HOOKS (same ids, same text), so their history keeps counting
      { id: 'layers', template: "{song} acapella but it's layer by layer {e1}[[ ({Source} edit at the end)]]" },
      { id: 'drop', template: 'wait for the drop {e1} {song} acapella[[, then a {Source} edit]]' },
      { id: 'question', template: 'which layer hits the hardest? {e1} {song} acapella[[ + {Source} edit]]' },
      { id: 'stems', template: '{song} acapella: the real stems come in one at a time, then the full song drops[[ with a {Source} edit]] {e1}' },
      { id: 'llm-line', llm: true, fallback: 'layers',
        guide: 'one plain sentence, the keyword "<song> acapella" first, saying what the viewer gets (the stems building up, then the drop)' },
    ],
  },
  samples: [
    { song: 'kick back', artist: 'Kenshi Yonezu', source: 'Chainsaw Man', kind: 'anime', theme: 'anime' },
    { song: 'bling bang bang born', source: 'Mashle', kind: 'anime', theme: 'anime' },
    { song: 'apt', source: 'ROSÉ', kind: 'group', theme: 'kpop' },
    { song: 'hana ni natte', source: 'The Apothecary Diaries', kind: 'anime', theme: 'anime' },
    { song: 'idol', source: null, kind: 'anime', theme: 'anime' },
  ],
};

/** Generic edit spec for the new edit types (core/edit, pipeline/edit-pipeline.js). */
function editSpec({ describe, keyword, must, subject, extra = {} }) {
  return {
    channel: 'meme',
    channelName: 'Zero Yen Otaku (anime, J-pop and K-pop edit Shorts)',
    lowerOnVideo: true,
    describe,
    keyword: f => lower(f.keyword) || keyword(f),
    must: f => (f.keyword ? [[lower(f.keyword)]] : must(f)),
    subject,
    searchSeeds: f => [lower(f.keyword) || keyword(f), f.song && `${lower(subject(f) || '')} ${lower(f.song)}`.trim()],
    suggestMust: f => (f.keyword ? [[lower(f.keyword)]] : must(f)),
    cta: () => 'rate this edit 1-10 👇',
    comments: ['rate this edit 1-10 🔥👇', 'who should get an edit next? ⚡👇', 'what song should the next edit use? 🎧👇'],
    tags: f => [lower(f.keyword) || keyword(f), subject(f), f.song, f.artist, f.source, f.source && `${f.source} edit`,
      THEME_TAG[f.theme] && `${THEME_TAG[f.theme]} edit`, 'edit', 'amv'],
    seeds: {
      titleStyle: [
        { id: 'kw-emoji', template: '{kw} {e}' },
        { id: 'kw-song', template: '{kw} | {withSong} {e}' },  // song names are searched with edits ("gojo edit specialz")
        { id: 'kw-suggest', template: '{suggest} {e}' },
        { id: 'llm-hook', llm: true, fallback: 'kw-emoji', example: '{kw} goes crazy {e}',
          guide: 'the keyword first, then a short hype tail in lowercase meme voice' },
        { id: 'llm-free', llm: true, fallback: 'kw-song', example: '{kw} | {withSong} {e}',
          guide: 'your best algorithm-aware title for this exact Short, keyword first' },
      ],
      ovStyle: [
        { id: 'ov-kw', templates: ['{kw}', '{subject}'] },
        { id: 'ov-song', templates: ['{subject} x {song}', '{song}'] },
        { id: 'ov-llm', llm: true, fallback: 'ov-kw', example: '{subject} goes crazy',
          guide: 'lowercase meme voice or bold hype, readable in half a second' },
      ],
      hashtagSet: [
        { id: 'niche3', desc: ['{subjectTag}', '{editTag}', '{themeTag}'] },
        { id: 'title-subject', title: ['{subjectTag}'], desc: ['{editTag}', '{themeTag}'], fallback: 'niche3' },
        { id: 'minimal', desc: ['{subjectTag}', '{editTag}'] },
      ],
      emojiSet: EDIT_EMOJI,
      descHook: [
        { id: 'kw-line', template: '{kw}[[ on {withSong}]][[ by {artist}]] {e1}' },
        { id: 'rate', template: '{kw}[[ on {withSong}]]: rate it 1-10 {e1}', noCta: true },  // it already asks
        { id: 'llm-line', llm: true, fallback: 'kw-line', guide: 'one plain sentence, the keyword first, saying what the viewer gets' },
      ],
    },
    ...extra,
  };
}

const SONG_EDIT = editSpec({
  describe: f => `An AMV-style edit of ${f.source || 'anime clips'}, every cut synced to the beat of "${f.song}"${f.artist ? ` by ${f.artist}` : ''}.`,
  keyword: f => (f.source ? `${lower(f.source)} edit` : `${lower(f.song)} edit`),
  must: f => [[lower(f.source || f.song)], ['edit', 'amv']],
  subject: f => lower(f.source || f.song),
  extra: { samples: [{ song: 'specialz', source: 'Jujutsu Kaisen', theme: 'anime' }, { song: 'kick back', source: 'Chainsaw Man', theme: 'anime' }] },
});
const CHARACTER_EDIT = editSpec({
  describe: f => `An edit of ${f.character}${f.source ? ` from ${f.source}` : ''}, beat-synced to "${f.song}".`,
  keyword: f => `${lower(f.character)} edit`,
  must: f => [[lower(f.character)], ['edit', 'amv']],
  subject: f => lower(f.character),
  extra: { samples: [{ character: 'Gojo', source: 'Jujutsu Kaisen', song: 'specialz', theme: 'anime' }] },
});
const DANCE_EDIT = editSpec({
  describe: f => `A dance edit: ${f.group || f.source || 'the group'} performing "${f.song}", cut to the beat.`,
  keyword: f => (f.song ? `${lower(f.song)} dance` : `${lower(f.group || f.source)} dance`),
  must: f => [[lower(f.song || f.group || f.source)], ['dance', 'choreo', 'choreography']],
  subject: f => lower(f.group || f.source),
  extra: { samples: [{ song: 'super shy', group: 'NewJeans', theme: 'kpop' }] },
});
const KPOP_EDIT = editSpec({
  describe: f => `A K-pop edit of ${f.member ? `${f.member} (${f.group})` : f.group || f.source}, beat-synced to "${f.song}".`,
  keyword: f => `${lower(f.member || f.group || f.source)} edit`,
  must: f => [[lower(f.member || f.group || f.source)], ['edit']],
  subject: f => lower(f.member || f.group || f.source),
  extra: { samples: [{ group: 'aespa', member: 'Karina', song: 'supernova', theme: 'kpop' }] },
});
const SOUND_EDIT = editSpec({
  describe: f => `A sound-design edit of ${f.source}: every hit, whoosh and bass drop lands on a cut${f.song ? `, on "${f.song}"` : ''}.`,
  keyword: f => `${lower(f.source)} edit`,
  must: f => [[lower(f.source)], ['edit', 'amv']],
  subject: f => lower(f.character || f.source),
  extra: { samples: [{ source: 'Demon Slayer', song: 'gurenge', theme: 'anime' }] },
});
const LONG_EDIT = editSpec({
  describe: f => `A long 16:9 compilation of the week's ${f.source || 'anime and K-pop'} edits.`,
  keyword: f => (f.source ? `${lower(f.source)} edits` : 'anime edits'),
  must: f => [[f.source ? lower(f.source) : 'edits'], ['edit', 'edits', 'amv']],
  subject: f => lower(f.source || 'anime'),
  extra: { long: true, maxTitle: 100, samples: [{ source: 'Jujutsu Kaisen', theme: 'anime' }, { source: null, theme: 'anime' }] },
});

/** Channel "Asian Pop Quiz": every quiz format shares one pool, so one upload a day is enough to learn. */
const QUIZ = {
  channel: 'quiz',
  channelName: 'Asian Pop Quiz (anime, K-pop and Asia quiz Shorts)',
  describe: f => `A ${f.n}-round ${f.format} quiz (${f.topicLabel || f.topic || f.theme}): ${f.roundsText || 'guess before the answer is revealed'}.`,
  keyword: f => lower(f.keyword),
  must: f => [[].concat(f.any && f.any.length ? f.any : [lower(f.keyword)])],
  searchSeeds: f => [lower(f.keyword)],
  suggestMust: f => [[lower(f.keyword)]],
  cta: f => f.cta || 'Comment your score 👇',
  comments: ['What did you score? 🏆 Which one got you? 👇'],
  tags: f => [lower(f.keyword), 'quiz'],
  seeds: {
    // template styles: the pipeline's templates carry a `style` (pipeline/world-quiz-pipeline.js, pop-quiz-meta.js)
    titleStyle: [
      { id: 'keyword', fromTemplates: true },
      { id: 'speed', fromTemplates: true },
      { id: 'challenge', fromTemplates: true },
      { id: 'fans', fromTemplates: true },
      { id: 'casual', fromTemplates: true },
      { id: 'ramp', fromTemplates: true },
      { id: 'llm', llm: true, fallback: 'keyword', example: '{Keyword}: Can You Get {n}/{n}? {e}',
        guide: 'the searchable keyword first, then a short challenge or curiosity tail; honest, no numbers you cannot back up, never reveal an answer' },
    ],
    hashtagSet: [
      { id: 'format3', desc: ['{fmt1}', '{fmt2}', '{fmt3}'] },
      { id: 'two', desc: ['{fmt1}', '{fmt2}'] },
      { id: 'title-tag', title: ['{fmt1}'], desc: ['{fmt2}', '{fmt3}'], fallback: 'format3' },
    ],
    emojiSet: [
      { id: 'template', keep: true },  // the template's own emoji (sad ones replaced)
      { id: 'fire', pool: ['🔥'] },
      { id: 'topic', pool: ['{topicEmoji}'] },
      { id: 'none', pool: [''] },
    ],
    descHook: [
      { id: 'challenge', template: '{hook} {cta}' },
      { id: 'keyword', template: '{Keyword} ({n} rounds). {hook} {cta}' },
      { id: 'no-peek', template: '{hook} No pausing, no peeking 👀 {cta}', notFor: ['wyr', 'duel'] },  // nothing to peek at there
    ],
  },
  samples: [
    { format: 'flag', keyword: 'flag quiz', Keyword: 'Flag Quiz', any: ['flag'], n: 5, topic: 'world', topicLabel: 'World', topicEmoji: '🌍',
      formatTags: '#flagquiz #geography #quiz', hook: 'How many of these 5 flags can you name before the timer runs out?', hard: true },
    { format: 'song', keyword: 'guess the kpop song', Keyword: 'Guess the K-Pop Song', any: ['song', 'kpop', 'k-pop'], n: 5, topic: 'kpop',
      topicLabel: 'K-Pop', topicEmoji: '🎶', formatTags: '#kpop #guessthesong #quiz', hook: 'Can you name all 5 songs from 3 seconds of each?' },
    { format: 'character', keyword: 'guess the anime character', Keyword: 'Guess the Anime Character', any: ['anime', 'character'], n: 3,
      topic: 'anime', topicLabel: 'Anime', topicEmoji: '🎬', formatTags: '#anime #animequiz #quiz',
      hook: 'Can you name all 3 anime characters before the picture gets sharp?' },
  ],
};

const SPECS = {
  'meme/stem-edit': STEM_EDIT,
  'meme/song-edit': SONG_EDIT,
  'meme/character-edit': CHARACTER_EDIT,
  'meme/dance-edit': DANCE_EDIT,
  'meme/kpop-edit': KPOP_EDIT,
  'meme/sound-edit': SOUND_EDIT,
  'meme/long-edit': LONG_EDIT,
  'quiz/quiz': QUIZ,
};

const CHANNEL_ALIASES = { zeroyenotaku: 'meme', 'zero yen otaku': 'meme', edit: 'meme', edits: 'meme', asianpopquiz: 'quiz', 'asian pop quiz': 'quiz', pop: 'quiz' };
const CT_ALIASES = { meme: 'stem-edit', acapella: 'stem-edit', stems: 'stem-edit', 'stem edit': 'stem-edit', amv: 'song-edit', edit: 'song-edit',
  character: 'character-edit', dance: 'dance-edit', kpop: 'kpop-edit', sound: 'sound-edit', long: 'long-edit', weekly: 'long-edit' };

function channelKey(channel) {
  const c = String(channel || 'meme').toLowerCase().replace(/^@/, '');
  return CHANNEL_ALIASES[c] || c;
}

/** "meme" + "stem-edit" -> "meme/stem-edit"; quiz formats all map to "quiz/quiz". */
function poolKey(channel, contentType) {
  const ch = channelKey(channel);
  if (ch === 'quiz') return 'quiz/quiz';
  const ct = String(contentType || 'stem-edit').toLowerCase();
  return `${ch}/${CT_ALIASES[ct] || ct}`;
}

/** The spec for a pool (unknown edit types get the song-edit spec, keyed by facts.keyword). */
function specFor(pool) {
  return SPECS[pool] || (pool.startsWith('quiz/') ? QUIZ : SONG_EDIT);
}

/** Template facts for a spec. */
function varsFor(spec, f) {
  const subject = spec.subject ? spec.subject(f) : lower(f.source);
  const fmt = String(f.formatTags || '').split(/\s+/).map(t => t.replace(/^#/, '')).filter(Boolean);
  return {
    song: lower(f.song), Song: f.song || null,
    source: lower(f.source), Source: f.source || null,
    artist: lower(f.artist), character: lower(f.character), group: lower(f.group), member: lower(f.member),
    subject, kw: spec.keyword(f) || null,
    withSong: f.song && !contains(spec.keyword(f) || '', f.song) ? lower(f.song) : null,  // the song, unless the keyword has it
    sourceTag: slugTag(f.source), songTag: slugTag(f.song), subjectTag: slugTag(subject),
    themeTag: THEME_TAG[f.theme] || null, editTag: EDIT_TAG[f.theme] || 'amv',
    suggest: f.suggestTitle || null,
    // quiz
    keyword: lower(f.keyword), Keyword: f.Keyword || f.keyword || null, n: f.n || null, topicLabel: f.topicLabel || null,
    topicEmoji: f.topicEmoji || null, hook: f.hook || null, cta: f.cta || (spec.cta ? spec.cta(f) : null),
    fmt1: fmt[0] || null, fmt2: fmt[1] || null, fmt3: fmt[2] || null,
  };
}

module.exports = { SPECS, STEM_EDIT, QUIZ, EDIT_EMOJI, poolKey, channelKey, specFor, varsFor, lower };
