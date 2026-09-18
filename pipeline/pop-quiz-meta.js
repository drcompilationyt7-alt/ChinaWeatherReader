/**
 * Titles, hashtags, tags, playlists and description text for the Asian pop
 * culture formats (emoji, trivia, wyr, city). The world-quiz pipeline picks a
 * title template with the same Thompson sampling it uses for the geography
 * formats; `when` limits a template to the plan's topic.
 */
const TOPIC_LABEL = {
  anime: 'Anime', 'kpop song': 'K-Pop Song', kpop: 'K-Pop', vtubers: 'VTuber', cdrama: 'C-Drama & Movie',
  cities: 'Asia City', food: 'Asian Food', CN: 'China', JP: 'Japan', KR: 'Korea',
};
const CITY_ADJ = { CN: 'Chinese', JP: 'Japanese', KR: 'Korean' };
const CITY_FLAG = { CN: '🇨🇳', JP: '🇯🇵', KR: '🇰🇷' };
const n = p => p.rounds.length;
const is = (...topics) => p => topics.includes(p.topic);

const POP_TITLE_TEMPLATES = {
  emoji: [
    { id: 'emoji-anime-guess', text: () => 'Guess the Anime From Emojis 🍜🦊', when: is('anime') },
    { id: 'emoji-anime-otaku', text: p => `Only Real Otakus Get ${n(p)}/${n(p)} 😭`, when: is('anime') },
    { id: 'emoji-anime-casual', text: () => 'guess the anime from emojis 😭✌️', when: is('anime') },
    { id: 'emoji-anime-ramp', text: () => 'Anime Emoji Quiz: Easy to IMPOSSIBLE 🔥', when: is('anime') },
    { id: 'emoji-kpop-guess', text: () => 'Guess the K-Pop Song From Emojis 🎶', when: is('kpop song') },
    { id: 'emoji-kpop-fans', text: p => `Only Real K-Pop Fans Get ${n(p)}/${n(p)} 💜`, when: is('kpop song') },
    { id: 'emoji-kpop-casual', text: () => 'guess the kpop song from emojis 😭✌️', when: is('kpop song') },
  ],
  trivia: [
    { id: 'trivia-fans', text: p => `${TOPIC_LABEL[p.topic]} Quiz: Only Real Fans Get ${n(p)}/${n(p)} 🔥`, when: is('kpop', 'anime', 'vtubers') },
    { id: 'trivia-howwell', text: p => `How Well Do You Know ${p.topic === 'kpop' ? 'K-Pop' : p.topic === 'anime' ? 'Anime' : p.topic === 'vtubers' ? 'VTubers' : p.topic === 'cdrama' ? 'Chinese Movies' : 'Asia'}? 🤔` },
    { id: 'trivia-ramp', text: p => `${TOPIC_LABEL[p.topic]} Quiz: Easy to IMPOSSIBLE 🔥` },
    { id: 'trivia-casual', text: p => `${p.topic === 'kpop' ? 'kpop' : p.topic === 'anime' ? 'anime' : p.topic === 'vtubers' ? 'vtuber' : 'asia'} quiz but it gets hard fast 😭` },
    { id: 'trivia-holo', text: () => 'Only True Hololive Fans Get This 🦈', when: p => p.topic === 'vtubers' && p.rounds.some(r => /hololive|gura|pekora|marine/i.test(JSON.stringify(r))) },
  ],
  wyr: [
    { id: 'wyr-edition', text: p => `Would You Rather: ${TOPIC_LABEL[p.topic]} Edition 😳` },
    { id: 'wyr-casual', text: p => `would you rather (${p.topic === 'kpop' ? 'kpop' : p.topic === 'anime' ? 'anime' : p.topic} edition) 😭✌️` },
    { id: 'wyr-hard', text: () => 'The Hardest Would You Rather 😭' },
    { id: 'wyr-anime-powers', text: () => 'Would You Rather: Anime Powers ⚡', when: is('anime') },
    { id: 'wyr-idol', text: () => 'Would You Rather: K-Pop Idol Life 💜', when: is('kpop') },
    { id: 'wyr-food', text: () => 'Would You Rather: Asian Food Edition 🍜', when: is('food') },
  ],
  character: [
    { id: 'char-guess', text: () => 'Guess the Anime Character 🤔' },
    { id: 'char-otaku', text: p => `Only Real Otakus Get ${n(p)}/${n(p)} 😭` },
    { id: 'char-casual', text: () => 'guess the anime character 😭✌️' },
    { id: 'char-blur', text: () => 'Guess the Anime Character Before It Unblurs 👀' },
    { id: 'char-ramp', text: () => 'Anime Character Quiz: Easy to IMPOSSIBLE 🔥' },
  ],
  idol: [
    { id: 'idol-guess', text: () => 'Guess the K-Pop Idol 💜' },
    { id: 'idol-fans', text: p => `Only Real K-Pop Fans Get ${n(p)}/${n(p)} 😭` },
    { id: 'idol-casual', text: () => 'guess the kpop idol 😭✌️' },
    { id: 'idol-blur', text: () => 'Name the K-Pop Idol Before It Unblurs 👀' },
  ],
  opening: [
    { id: 'op-3sec', text: () => 'Guess the Anime Opening in 3 Seconds 🎶' },
    { id: 'op-otaku', text: p => `Only Real Otakus Get ${n(p)}/${n(p)} 🎧` },
    { id: 'op-casual', text: () => 'guess the anime opening 😭✌️' },
    { id: 'op-ramp', text: () => 'Anime Opening Quiz: Easy to IMPOSSIBLE 🔥' },
  ],
  city: [
    { id: 'city-guess', text: p => `Guess the ${CITY_ADJ[p.topic]} City From the Map ${CITY_FLAG[p.topic]}` },
    { id: 'city-find', text: p => `Can You Find These ${CITY_ADJ[p.topic]} Cities? 🗺️` },
    { id: 'city-casual', text: p => `most people fail this ${TOPIC_LABEL[p.topic].toLowerCase()} map quiz 😭` },
    { id: 'city-ramp', text: p => `${TOPIC_LABEL[p.topic]} Map Quiz: Easy to IMPOSSIBLE 🔥` },
  ],
};

const FORMAT_HASHTAG = {
  character: '#anime #animequiz #quiz', idol: '#kpop #kpopquiz #quiz', opening: '#anime #animeopening #quiz',
};
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
};
const BASE_TAGS = ['asian pop quiz', 'quiz', 'trivia', 'shorts quiz', 'asian pop culture'];

function popPlaylist(p) {
  if (p.format === 'character') return 'Guess the Anime Character';
  if (p.format === 'idol') return 'Guess the K-Pop Idol';
  if (p.format === 'opening') return 'Guess the Anime Opening';
  if (p.format === 'wyr') return 'Would You Rather: Asia Edition';
  if (p.format === 'city') return 'Guess the City From the Map';
  if (p.format === 'emoji') return p.topic === 'anime' ? 'Guess the Anime From Emojis' : 'Guess the K-Pop Song From Emojis';
  return `${TOPIC_LABEL[p.topic] || 'Asian Pop'} Quiz`;
}

function popHook(p) {
  if (p.format === 'wyr') return 'Pick one for each round.';
  if (p.format === 'city') return `Can you place all ${n(p)} ${CITY_ADJ[p.topic]} cities on the map?`;
  if (p.format === 'emoji') return `Can you guess all ${n(p)} from just 4 emojis?`;
  if (p.format === 'character') return `Can you name all ${n(p)} anime characters before the picture gets sharp?`;
  if (p.format === 'idol') return `Can you name all ${n(p)} K-pop idols before the photo gets sharp?`;
  if (p.format === 'opening') return `Can you name the anime from 3 seconds of its opening?`;
  return `How many of these ${n(p)} can you get right?`;
}

function popAnswers(p) {
  return p.rounds.map((r, i) => {
    if (p.format === 'wyr') return `${i + 1}. ${r.a} OR ${r.b}`;
    if (p.format === 'trivia') return `${i + 1}. ${r.question} → ${r.options[r.answer]}`;
    if (p.format === 'character') return `${i + 1}. ${r.name} (${r.anime})`;
    if (p.format === 'idol') return `${i + 1}. ${r.name}${r.group ? ` (${r.group})` : ''}`;
    if (p.format === 'opening') return `${i + 1}. ${r.title}`;
    return `${i + 1}. ${r.answer}${r.fact ? ` (${r.fact})` : ''}`;
  }).join('\n');
}

function popDescription(p) {
  const head = p.format === 'wyr' ? `${popHook(p)} Comment A or B for each 👇` : `${popHook(p)} Comment your score 👇`;
  const label = p.format === 'wyr' ? 'The choices' : 'Answers (no peeking!)';
  const credits = p.format === 'idol'
    ? `Photos (Wikimedia Commons): ${p.rounds.map(r => `${r.name}: ${r.credit}`).join('; ')}\n`
    : p.format === 'character' || p.format === 'opening' ? 'Character art and covers via AniList.\n'
      : p.format === 'city' ? 'Map data: Natural Earth. Emoji art: Noto Emoji (Apache-2.0).\n' : 'Emoji art: Noto Emoji (Apache-2.0).\n';
  return `${head}\n\n🍡 Asian Pop Quiz: anime, K-pop and Asia quizzes, a new one every day.\n\n`
    + `${label}:\n${popAnswers(p)}\n\n${credits}`
    + (FORMAT_HASHTAG[p.format] || HASHTAG[p.topic] || '#quiz');
}

function popTags(p) {
  const names = ['emoji', 'city', 'character', 'idol', 'opening'].includes(p.format) ? p.rounds.map(r => String(r.answer || r.name || r.title).split(' (')[0]) : [];
  const out = [];
  let len = 0;
  for (const t of [...(TOPIC_TAGS[p.topic] || []), ...BASE_TAGS, ...names]) {
    if (out.includes(t) || len + t.length + 3 > 480) continue;
    out.push(t);
    len += t.length + 3;
  }
  return out;
}

function popComment(p) {
  return p.format === 'wyr'
    ? 'Which would you pick? Drop your A/B answers 👇'
    : `What did you score out of ${n(p)}? 🏆 Which one got you? 👇`;
}

const popHashtags = p => FORMAT_HASHTAG[p.format] || HASHTAG[p.topic] || '#quiz';

module.exports = { POP_TITLE_TEMPLATES, popPlaylist, popHook, popAnswers, popDescription, popTags, popComment, popHashtags, HASHTAG };
