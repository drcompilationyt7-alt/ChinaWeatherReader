/**
 * Titles, hashtags, tags, playlists, credits and description text for the Asian pop
 * culture formats (emoji, trivia, wyr, city, character, idol, opening, cityphoto, vtuber,
 * duel, scene, voice, song). `when` limits a template to the plan's topic; `style` is the
 * template's title style (keyword / speed / challenge / fans / casual / ramp): core/metadata
 * learns which style works across all formats and the template rotates inside it.
 * No sad / crying emoji (the owner does not want them). popKeyword() is what people search for
 * each format (the title keyword the metadata optimizer checks).
 * Nothing here may contain < or > (YouTube rejects them): clean() strips them.
 */
const TOPIC_LABEL = {
  anime: 'Anime', 'kpop song': 'K-Pop Song', kpop: 'K-Pop', vtubers: 'VTuber', cdrama: 'C-Drama & Movie',
  cities: 'Asia City', food: 'Asian Food', CN: 'China', JP: 'Japan', KR: 'Korea', mixed: 'Asian Pop',
};
const clean = s => String(s == null ? '' : s).replace(/[<>]/g, '');
const shortShow = t => String(t || '').split(/:| -/)[0].replace(/\s*\((tv|\d{4})\)$/i, '').trim();
const CITY_ADJ = { CN: 'Chinese', JP: 'Japanese', KR: 'Korean' };
const CITY_FLAG = { CN: '🇨🇳', JP: '🇯🇵', KR: '🇰🇷' };
const n = p => p.rounds.length;
const is = (...topics) => p => topics.includes(p.topic);

const POP_TITLE_TEMPLATES = {
  emoji: [
    { id: 'emoji-anime-guess', style: 'keyword', text: () => 'Guess the Anime From Emojis 🍜🦊', when: is('anime') },
    { id: 'emoji-anime-otaku', style: 'fans', text: p => `Only Real Otakus Get ${n(p)}/${n(p)} 🔥`, when: is('anime') },
    { id: 'emoji-anime-casual', style: 'casual', text: () => 'guess the anime from emojis 👀✌️', when: is('anime') },
    { id: 'emoji-anime-ramp', style: 'ramp', text: () => 'Anime Emoji Quiz: Easy to IMPOSSIBLE 🔥', when: is('anime') },
    { id: 'emoji-kpop-guess', style: 'keyword', text: () => 'Guess the K-Pop Song From Emojis 🎶', when: is('kpop song') },
    { id: 'emoji-kpop-fans', style: 'fans', text: p => `Only Real K-Pop Fans Get ${n(p)}/${n(p)} 💜`, when: is('kpop song') },
    { id: 'emoji-kpop-casual', style: 'casual', text: () => 'guess the kpop song from emojis 🎶✌️', when: is('kpop song') },
  ],
  trivia: [
    { id: 'trivia-fans', style: 'fans', text: p => `${TOPIC_LABEL[p.topic]} Quiz: Only Real Fans Get ${n(p)}/${n(p)} 🔥`, when: is('kpop', 'anime', 'vtubers') },
    { id: 'trivia-howwell', style: 'challenge', text: p => `How Well Do You Know ${p.topic === 'kpop' ? 'K-Pop' : p.topic === 'anime' ? 'Anime' : p.topic === 'vtubers' ? 'VTubers' : p.topic === 'cdrama' ? 'Chinese Movies' : 'Asia'}? 🤔` },
    { id: 'trivia-ramp', style: 'ramp', text: p => `${TOPIC_LABEL[p.topic]} Quiz: Easy to IMPOSSIBLE 🔥` },
    { id: 'trivia-casual', style: 'casual', text: p => `${p.topic === 'kpop' ? 'kpop' : p.topic === 'anime' ? 'anime' : p.topic === 'vtubers' ? 'vtuber' : 'asia'} quiz but it gets hard fast 🔥` },
    { id: 'trivia-holo', style: 'fans', text: () => 'Only True Hololive Fans Get This 🦈', when: p => p.topic === 'vtubers' && p.rounds.some(r => /hololive|gura|pekora|marine/i.test(JSON.stringify(r))) },
  ],
  wyr: [
    { id: 'wyr-edition', style: 'keyword', text: p => `Would You Rather: ${TOPIC_LABEL[p.topic]} Edition 😳` },
    { id: 'wyr-casual', style: 'casual', text: p => `would you rather (${p.topic === 'kpop' ? 'kpop' : p.topic === 'anime' ? 'anime' : p.topic} edition) 🔥✌️` },
    { id: 'wyr-hard', style: 'challenge', text: () => 'The Hardest Would You Rather 🤯' },
    { id: 'wyr-anime-powers', style: 'keyword', text: () => 'Would You Rather: Anime Powers ⚡', when: is('anime') },
    { id: 'wyr-idol', style: 'keyword', text: () => 'Would You Rather: K-Pop Idol Life 💜', when: is('kpop') },
    { id: 'wyr-food', style: 'keyword', text: () => 'Would You Rather: Asian Food Edition 🍜', when: is('food') },
  ],
  character: [
    { id: 'char-guess', style: 'keyword', text: () => 'Guess the Anime Character 🤔' },
    { id: 'char-otaku', style: 'fans', text: p => `Only Real Otakus Get ${n(p)}/${n(p)} 🔥` },
    { id: 'char-casual', style: 'casual', text: () => 'guess the anime character 🔥✌️' },
    { id: 'char-blur', style: 'speed', text: () => 'Guess the Anime Character Before It Unblurs 👀' },
    { id: 'char-ramp', style: 'ramp', text: () => 'Anime Character Quiz: Easy to IMPOSSIBLE 🔥' },
  ],
  idol: [
    { id: 'idol-guess', style: 'keyword', text: () => 'Guess the K-Pop Idol 💜' },
    { id: 'idol-fans', style: 'fans', text: p => `Only Real K-Pop Fans Get ${n(p)}/${n(p)} 💜` },
    { id: 'idol-casual', style: 'casual', text: () => 'guess the kpop idol 🔥✌️' },
    { id: 'idol-blur', style: 'speed', text: () => 'Name the K-Pop Idol Before It Unblurs 👀' },
  ],
  opening: [
    { id: 'op-3sec', style: 'speed', text: () => 'Guess the Anime Opening in 3 Seconds 🎶' },
    { id: 'op-otaku', style: 'fans', text: p => `Only Real Otakus Get ${n(p)}/${n(p)} 🎧` },
    { id: 'op-casual', style: 'casual', text: () => 'guess the anime opening 🔥✌️' },
    { id: 'op-ramp', style: 'ramp', text: () => 'Anime Opening Quiz: Easy to IMPOSSIBLE 🔥' },
  ],
  cityphoto: [
    { id: 'cphoto-guess', style: 'keyword', text: () => 'Guess the Asian City From One Photo 📸' },
    { id: 'cphoto-name', style: 'challenge', text: p => `Can You Name These ${n(p)} Asian Cities? 🏙️` },
    { id: 'cphoto-casual', style: 'casual', text: () => 'guess the city from the photo 🔥✌️' },
    { id: 'cphoto-ramp', style: 'ramp', text: () => 'Asia City Quiz: Easy to IMPOSSIBLE 🔥' },
    { id: 'cphoto-travel', style: 'fans', text: p => `Only Travel Nerds Get ${n(p)}/${n(p)} ✈️` },
  ],
  vtuber: [
    { id: 'vt-guess', style: 'keyword', text: () => 'Guess the VTuber 🤔' },
    { id: 'vt-fans', style: 'fans', text: p => `Only Real VTuber Fans Get ${n(p)}/${n(p)} 🔥` },
    { id: 'vt-casual', style: 'casual', text: () => 'guess the vtuber 🔥✌️' },
    { id: 'vt-blur', style: 'speed', text: () => 'Name the VTuber Before It Unblurs 👀' },
    { id: 'vt-ramp', style: 'ramp', text: () => 'VTuber Quiz: Easy to IMPOSSIBLE 🔥' },
    { id: 'vt-holo', style: 'fans', text: () => 'Only True Hololive Fans Get This 🦈', when: p => p.rounds.filter(r => /hololive/i.test(r.agency || '')).length >= 3 },
  ],
  duel: [
    { id: 'duel-pick', style: 'keyword', text: () => 'Who Would You Pick? Anime Edition ⚔️' },
    { id: 'duel-vs', style: 'keyword', text: p => `${p.rounds[0].a.name} or ${p.rounds[0].b.name}? Fans Voted 🤔`, when: p => (p.rounds[0].a.name + p.rounds[0].b.name).length <= 30 },
    { id: 'duel-casual', style: 'casual', text: () => 'who would you pick? (anime edition) 🔥✌️' },
    { id: 'duel-agree', style: 'challenge', text: () => 'Do You Agree With Anime Fans? 🔥' },
    { id: 'duel-fans', style: 'challenge', text: () => 'Anime Fans Picked... Do You Agree? 😳' },
  ],
  scene: [
    { id: 'scene-4sec', style: 'keyword', text: () => 'Guess the Anime From One Scene 🎬' },
    { id: 'scene-otaku', style: 'fans', text: p => `Only Real Otakus Get ${n(p)}/${n(p)} 🎬` },
    { id: 'scene-casual', style: 'casual', text: () => 'guess the anime from 4 seconds 🔥✌️' },
    { id: 'scene-ramp', style: 'ramp', text: () => 'Anime Scene Quiz: Easy to IMPOSSIBLE 🔥' },
  ],
  voice: [
    { id: 'voice-guess', style: 'keyword', text: () => 'Guess the Anime Character by Their Voice 🎧' },
    { id: 'voice-fans', style: 'fans', text: p => `Only Real Fans Know All ${n(p)} Voices 🎧` },
    { id: 'voice-casual', style: 'casual', text: () => 'guess the anime character by voice 🔥✌️' },
    { id: 'voice-ramp', style: 'ramp', text: () => 'Anime Voice Quiz: Easy to IMPOSSIBLE 🔥' },
  ],
  song: [
    { id: 'song-3sec', style: 'speed', text: () => 'Guess the Song in 3 Seconds 🎶' },
    { id: 'song-casual', style: 'casual', text: () => 'guess the song in 3 seconds 🔥✌️' },
    { id: 'song-ramp', style: 'ramp', text: p => `${p.topic === 'kpop' ? 'K-Pop' : p.topic === 'anime' ? 'Anime' : 'Asian Pop'} Song Quiz: Easy to IMPOSSIBLE 🔥` },
    { id: 'song-kpop', style: 'speed', text: () => 'Guess the K-Pop Song in 3 Seconds 🎧', when: is('kpop') },
    { id: 'song-kpop-fans', style: 'fans', text: p => `Only Real K-Pop Fans Get ${n(p)}/${n(p)} 💜`, when: is('kpop') },
    { id: 'song-anime', style: 'speed', text: () => 'Guess the Anime Song in 3 Seconds 🎧', when: is('anime') },
    { id: 'song-anime-otaku', style: 'fans', text: p => `Only Real Otakus Get ${n(p)}/${n(p)} 🎶`, when: is('anime') },
    { id: 'song-mixed', style: 'keyword', text: () => 'K-Pop, Anime or J-Pop? Name the Song 🎶', when: is('mixed') },
  ],
  city: [
    { id: 'city-guess', style: 'keyword', text: p => `Guess the ${CITY_ADJ[p.topic]} City From the Map ${CITY_FLAG[p.topic]}` },
    { id: 'city-find', style: 'challenge', text: p => `Can You Find These ${CITY_ADJ[p.topic]} Cities? 🗺️` },
    { id: 'city-casual', style: 'casual', text: p => `most people fail this ${TOPIC_LABEL[p.topic].toLowerCase()} map quiz 🔥` },
    { id: 'city-ramp', style: 'ramp', text: p => `${TOPIC_LABEL[p.topic]} Map Quiz: Easy to IMPOSSIBLE 🔥` },
  ],
};

const FORMAT_HASHTAG = {
  character: '#anime #animequiz #quiz', idol: '#kpop #kpopquiz #quiz', opening: '#anime #animeopening #quiz',
  cityphoto: '#asia #travel #quiz', vtuber: '#vtuber #hololive #quiz', duel: '#anime #wouldyourather #quiz',
  scene: '#anime #animequiz #quiz', voice: '#anime #animequiz #quiz',
};
const SONG_HASHTAG = { kpop: '#kpop #guessthesong #quiz', anime: '#anime #animesongs #quiz', mixed: '#kpop #jpop #guessthesong' };
const SONG_TAGS = {
  kpop: ['guess the kpop song', 'kpop quiz', 'kpop songs', 'guess the song', 'kpop'],
  anime: ['guess the anime song', 'anime opening quiz', 'anime songs', 'guess the song', 'anime'],
  mixed: ['guess the song', 'kpop songs', 'anime songs', 'jpop', 'cpop', 'music quiz'],
};
const COUNTRY = { CN: 'China', JP: 'Japan', KR: 'South Korea' };
const HASHTAG = {
  anime: '#anime #animequiz #quiz', 'kpop song': '#kpop #kpopquiz #quiz', kpop: '#kpop #kpopquiz #quiz',
  vtubers: '#vtuber #hololive #quiz', cdrama: '#cdrama #chinesedrama #quiz', cities: '#asia #travel #quiz',
  food: '#asianfood #wouldyourather #quiz', CN: '#china #geography #quiz', JP: '#japan #geography #quiz', KR: '#korea #geography #quiz',
};
const TOPIC_TAGS = {
  anime: ['anime quiz', 'guess the anime', 'anime trivia', 'otaku', 'anime', 'manga'],
  'kpop song': ['kpop quiz', 'guess the kpop song', 'kpop', 'kpop songs', 'kpop trivia'],
  kpop: ['kpop quiz', 'kpop trivia', 'kpop', 'kpop idols', 'kpop fandom'],
  vtubers: ['vtuber quiz', 'vtuber', 'hololive', 'nijisanji', 'vtuber trivia'],
  cdrama: ['cdrama', 'chinese drama', 'c-drama quiz', 'chinese movies'],
  cities: ['asia quiz', 'china', 'japan', 'korea', 'travel quiz'],
  food: ['asian food', 'would you rather food', 'ramen', 'sushi', 'kbbq'],
  CN: ['china map quiz', 'chinese cities', 'china geography', 'guess the city'],
  JP: ['japan map quiz', 'japanese cities', 'japan geography', 'guess the city'],
  KR: ['korea map quiz', 'korean cities', 'korea geography', 'guess the city'],
  asia: ['asia quiz', 'guess the city', 'city quiz', 'travel quiz', 'china', 'japan', 'korea'],
};
const BASE_TAGS = ['asian pop quiz', 'quiz', 'trivia', 'shorts quiz', 'asian pop culture'];

function popPlaylist(p) {
  if (p.format === 'song') return p.topic === 'kpop' ? 'Guess the K-Pop Song' : p.topic === 'anime' ? 'Guess the Anime Song' : 'Guess the Song: K-Pop, Anime & J-Pop';
  if (p.format === 'cityphoto') return 'Guess the Asian City From a Photo';
  if (p.format === 'vtuber') return 'Guess the VTuber';
  if (p.format === 'duel') return 'Anime Duels: Who Would You Pick?';
  if (p.format === 'scene') return 'Guess the Anime From a Scene';
  if (p.format === 'voice') return 'Guess the Anime Character by Voice';
  if (p.format === 'character') return 'Guess the Anime Character';
  if (p.format === 'idol') return 'Guess the K-Pop Idol';
  if (p.format === 'opening') return 'Guess the Anime Opening';
  if (p.format === 'wyr') return 'Would You Rather: Asia Edition';
  if (p.format === 'city') return 'Guess the City From the Map';
  if (p.format === 'emoji') return p.topic === 'anime' ? 'Guess the Anime From Emojis' : 'Guess the K-Pop Song From Emojis';
  return `${TOPIC_LABEL[p.topic] || 'Asian Pop'} Quiz`;
}

function popHook(p) {
  if (p.format === 'song') return `Can you name all ${n(p)} songs from 3 seconds of each?`;
  if (p.format === 'cityphoto') return `Can you name all ${n(p)} Asian cities from a single photo?`;
  if (p.format === 'vtuber') return `Can you name all ${n(p)} VTubers before the picture gets sharp?`;
  if (p.format === 'duel') return 'Pick one each round, then see who anime fans actually picked on AniList.';
  if (p.format === 'scene') return `Can you name all ${n(p)} anime from 4 seconds of their opening?`;
  if (p.format === 'voice') return `Can you name all ${n(p)} anime characters just from their voice?`;
  if (p.format === 'wyr') return 'Pick one for each round.';
  if (p.format === 'city') return `Can you place all ${n(p)} ${CITY_ADJ[p.topic]} cities on the map?`;
  if (p.format === 'emoji') return `Can you guess all ${n(p)} from just 4 emojis?`;
  if (p.format === 'character') return `Can you name all ${n(p)} anime characters before the picture gets sharp?`;
  if (p.format === 'idol') return `Can you name all ${n(p)} K-pop idols before the photo gets sharp?`;
  if (p.format === 'opening') return `Can you name the anime from 3 seconds of its opening?`;
  return `How many of these ${n(p)} can you get right?`;
}

const wyrLabel = (r, side) => r[side === 'a' ? 'labelA' : 'labelB'] || r[side];

function popAnswers(p) {
  return p.rounds.map((r, i) => {
    if (p.format === 'wyr') {
      const side = ['a', 'b'].includes(r.pick) ? r.pick : null;
      return `${i + 1}. ${r.a} OR ${r.b}${side ? ` (my pick: ${wyrLabel(r, side)})` : ''}`;
    }
    if (p.format === 'song') return `${i + 1}. ${r.title} by ${r.artist}${r.anime || r.anime_film ? ` (${shortShow(r.anime || r.anime_film)})` : ''}`;
    if (p.format === 'trivia') return `${i + 1}. ${r.question} → ${r.options[r.answer]}`;
    if (p.format === 'character') return `${i + 1}. ${r.name} (${r.anime})`;
    if (p.format === 'idol') return `${i + 1}. ${r.name}${r.group ? ` (${r.group})` : ''}`;
    if (p.format === 'opening' || p.format === 'scene') return `${i + 1}. ${r.title}`;
    if (p.format === 'cityphoto') return `${i + 1}. ${r.name}, ${COUNTRY[r.iso2] || r.iso2}${r.fact ? ` (${r.fact})` : ''}`;
    if (p.format === 'vtuber') return `${i + 1}. ${r.name} (${r.agency}${r.graduated ? ', graduated' : ''})`;
    if (p.format === 'voice') return `${i + 1}. ${r.name} (${r.anime})`;
    if (p.format === 'duel') {
      const [w, l] = r.winner === 'a' ? [r.a, r.b] : [r.b, r.a];
      return `${i + 1}. ${r.question} ${r.a.name} or ${r.b.name}: fans picked ${w.name} `
        + `(${w.favourites.toLocaleString('en-US')} vs ${l.favourites.toLocaleString('en-US')} AniList favorites)`;
    }
    return `${i + 1}. ${r.answer}${r.fact ? ` (${r.fact})` : ''}`;
  }).join('\n');
}

const yt = id => `https://youtu.be/${id}`;

function popCredits(p) {
  const clips = p.rounds.filter(r => r.source && r.source.video);
  switch (p.format) {
    case 'song': return `Song clips from: ${clips.map(r => `${r.title} ${yt(r.source.video)}`).join('; ')}. All songs belong to their artists and labels.\n`;
    case 'emoji': return (clips.length ? `Songs on the reveals: ${clips.map(r => `${r.song || (p.topic === 'anime' ? `${r.answer} opening`
      : String(r.answer).split(' (')[0])} ${yt(r.source.video)}`).join('; ')}. `
      + 'Covers via AniList. ' : '') + 'Emoji art: Noto Emoji (Apache-2.0).\n';
    case 'wyr': {
      const ph = p.rounds.filter(r => r.photoCredit && r.photoCredit.credit);
      return 'The picks are my own opinion, not a poll. '
        + (ph.length ? `Photos: ${ph.map(r => `${r.photoCredit.option}: ${r.photoCredit.credit}${r.photoCredit.page ? ` ${r.photoCredit.page}` : ''}`).join('; ')}. ` : '')
        + 'Emoji art: Noto Emoji (Apache-2.0).\n';
    }
    case 'idol': return `Photos (Wikimedia Commons): ${p.rounds.map(r => `${r.name}: ${r.credit}`).join('; ')}\n`;
    case 'cityphoto': return `Photos (Wikimedia Commons, licences as listed): ${p.rounds.map(r => `${r.name}: ${r.credit}${r.page ? ` ${r.page}` : ''}`).join('; ')}\n`;
    case 'vtuber': return 'Official VTuber portraits via the Virtual YouTuber Wiki (virtualyoutuber.fandom.com). '
      + 'All characters belong to their agencies and creators.\n';
    case 'duel': return 'Character art and favorite counts via AniList (anilist.co).\n';
    case 'scene': return `Clips from the creditless openings: ${p.rounds.map(r => (r.source && r.source.video ? `${r.title} ${yt(r.source.video)}` : r.title)).join('; ')}. Covers via AniList.\n`;
    case 'voice': return `Voice clips from: ${p.rounds.map(r => (r.source && r.source.video ? `${r.name} ${yt(r.source.video)}` : r.name)).join('; ')}. Character art via AniList.\n`;
    case 'character': case 'opening': return 'Character art and covers via AniList.\n';
    case 'city': return 'Map data: Natural Earth. Emoji art: Noto Emoji (Apache-2.0).\n';
    default: return 'Emoji art: Noto Emoji (Apache-2.0).\n';
  }
}

/** The comment prompt after the description's hook line. */
const popCta = p => (p.format === 'wyr' ? 'Comment A or B for each 👇' : p.format === 'duel' ? 'Comment your picks 👇' : 'Comment your score 👇');

/** opts.head / opts.hashtags: the metadata optimizer's first line and hashtag set (else the defaults). */
function popDescription(p, opts = {}) {
  const head = opts.head || `${popHook(p)} ${popCta(p)}`;
  const label = p.format === 'wyr' ? 'The choices' : p.format === 'duel' ? 'The fans picked' : 'Answers (no peeking!)';
  return clean(`${head}\n\n🍡 Asian Pop Quiz: anime, K-pop and Asia quizzes, a new one every day.\n\n`
    + `${label}:\n${popAnswers(p)}\n\n${popCredits(p)}`
    + (opts.hashtags || popHashtags(p)));
}

/**
 * What people type to find this quiz: {keyword, Keyword (title case), any (a title must contain one), emoji}.
 * LLM-written titles must contain one of `any`.
 */
function popKeyword(p) {
  const k = (keyword, Keyword, any, emoji) => ({ keyword, Keyword, any, emoji });
  const label = TOPIC_LABEL[p.topic] || 'Asian Pop';
  switch (p.format) {
    case 'emoji': return p.topic === 'anime' ? k('guess the anime from emojis', 'Guess the Anime From Emojis', ['anime', 'otaku'], '🎬')
      : k('guess the kpop song from emojis', 'Guess the K-Pop Song From Emojis', ['kpop', 'k-pop'], '🎶');
    case 'trivia': return k(`${label.toLowerCase()} quiz`, `${label} Quiz`, ['quiz', label.toLowerCase()], '🧠');
    case 'wyr': return k('would you rather', 'Would You Rather', ['would you rather'], '🤔');
    case 'character': return k('guess the anime character', 'Guess the Anime Character', ['anime', 'otaku'], '🎬');
    case 'idol': return k('guess the kpop idol', 'Guess the K-Pop Idol', ['kpop', 'k-pop', 'idol'], '💜');
    case 'opening': return k('guess the anime opening', 'Guess the Anime Opening', ['opening', 'anime', 'otaku'], '🎶');
    case 'cityphoto': return k('guess the city', 'Guess the Asian City', ['city', 'cities'], '📸');
    case 'vtuber': return k('guess the vtuber', 'Guess the VTuber', ['vtuber', 'hololive'], '🦊');
    case 'duel': return k('who would you pick anime', 'Who Would You Pick', ['anime', 'pick'], '⚔️');
    case 'scene': return k('guess the anime', 'Guess the Anime From One Scene', ['anime', 'otaku'], '🎬');
    case 'voice': return k('guess the anime character by voice', 'Guess the Anime Character by Voice', ['voice', 'voices'], '🎧');
    case 'song': return p.topic === 'kpop' ? k('guess the kpop song', 'Guess the K-Pop Song', ['song'], '🎶')
      : p.topic === 'anime' ? k('guess the anime song', 'Guess the Anime Song', ['song', 'otaku'], '🎶') : k('guess the song', 'Guess the Song', ['song'], '🎶');
    case 'city': return k(`${(CITY_ADJ[p.topic] || 'asian').toLowerCase()} map quiz`, `${CITY_ADJ[p.topic] || 'Asian'} Map Quiz`,
      ['city', 'cities', 'map'], CITY_FLAG[p.topic] || '🗺️');
    default: return k(`${label.toLowerCase()} quiz`, `${label} Quiz`, ['quiz'], '🔥');
  }
}

function popTags(p) {
  const names = p.format === 'duel' ? p.rounds.flatMap(r => [r.a.name, r.b.name])
    : p.format === 'song' ? p.rounds.flatMap(r => [r.title, r.artist])
      : ['emoji', 'city', 'character', 'idol', 'opening', 'cityphoto', 'vtuber', 'scene', 'voice'].includes(p.format)
        ? p.rounds.map(r => String(r.answer || r.name || r.title).split(' (')[0]) : [];
  const out = [];
  let len = 0;
  const topicTags = p.format === 'song' ? SONG_TAGS[p.topic] || SONG_TAGS.mixed : TOPIC_TAGS[p.topic] || [];
  for (const t0 of [...topicTags, ...BASE_TAGS, ...names]) {
    const t = clean(t0).replace(/,/g, '').trim();
    if (!t || out.includes(t) || len + t.length + 3 > 480) continue;
    out.push(t);
    len += t.length + 3;
  }
  return out;
}

function popComment(p) {
  if (p.format === 'duel') return 'Did the fans get it right? Who would you have picked? 👇';
  return p.format === 'wyr'
    ? 'Did you agree with my picks? Drop your A/B answers 👇'
    : `What did you score out of ${n(p)}? 🏆 Which one got you? 👇`;
}

const popHashtags = p => (p.format === 'song' ? SONG_HASHTAG[p.topic] || SONG_HASHTAG.mixed : FORMAT_HASHTAG[p.format] || HASHTAG[p.topic] || '#quiz');

module.exports = { POP_TITLE_TEMPLATES, popPlaylist, popHook, popAnswers, popDescription, popTags, popComment, popHashtags, popKeyword,
  popCta, HASHTAG, TOPIC_LABEL, clean };
