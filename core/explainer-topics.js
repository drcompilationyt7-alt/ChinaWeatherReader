/**
 * Explainer Topics — Country + Topic Angle Generator
 * 
 * Generates random country picks and provides guidance to the Planning Agent.
 * Maintains 50/50 positive/negative angle distribution.
 * Keeps track of recently used countries to avoid repeats.
 */
const { Logger } = require('./logger');

const logger = new Logger('ExplainerTopics');

const ALL_COUNTRIES = [
  'China', 'Japan', 'South Korea', 'Thailand', 'Vietnam',
  'India', 'Indonesia', 'Brazil', 'Mexico', 'France',
  'Germany', 'Italy', 'Spain', 'UK', 'Egypt',
  'Nigeria', 'Australia', 'Global'
];

/**
 * Topic angle guidance for each country (both positive and negative)
 * These serve as examples/hints for the Planning Agent to build upon
 */
const TOPIC_EXAMPLES = {
  'China': {
    positive: [
      'Why China is so futuristic',
      'Why China has the best high-speed rail',
      'Why China builds cities in 10 years',
      'Why China electric cars dominate',
      'Why Chinese mobile payments are everywhere',
    ],
    negative: [
      'Why China has ghost cities',
      'Why China social credit system is controversial',
      'Why China internet is heavily censored',
      'Why China air pollution is so bad',
      'Why China demographic crisis is serious',
    ],
  },
  'Japan': {
    positive: [
      'Why Japan vending machines are everywhere',
      'Why Japan trains are the most punctual',
      'Why Japanese customer service is legendary',
      'Why Japanese convenience stores are incredible',
    ],
    negative: [
      'Why Japan work culture is so extreme',
      'Why Japan has a loneliness crisis',
      'Why Japan birth rate is collapsing',
      'Why Japan debt is the highest in the world',
    ],
  },
  'South Korea': {
    positive: [
      'Why South Korea internet is the fastest',
      'Why Korean skincare is world famous',
      'Why South Korea beauty standards drive innovation',
      'Why K-pop conquered the world',
    ],
    negative: [
      'Why South Korea plastic surgery is so common',
      'Why South Korea has a dating crisis',
      'Why Korean work culture is brutal',
      'Why South Korea has the lowest birth rate',
    ],
  },
  'India': {
    positive: [
      'Why India is the world biggest democracy',
      'Why India IT industry dominates globally',
      'Why India has the largest diaspora',
      'Why Indian weddings are legendary',
      'Why Indian street food is globally loved',
    ],
    negative: [
      'Why India street food hygiene is a problem',
      'Why India overpopulation is a crisis',
      'Why Indian cities have extreme pollution',
      'Why India public transport is overcrowded',
      'Why India garbage problem is out of control',
    ],
  },
  'Brazil': {
    positive: [
      'Why Brazil carnival is legendary',
      'Why Brazilian football produces legends',
      'Why Brazilian parties are the best',
      'Why Brazilian beaches are world class',
    ],
    negative: [
      'Why Brazil favelas are dangerous',
      'Why Brazil amazon deforestation is alarming',
      'Why Brazil economic inequality is extreme',
      'Why Brazilian crime rates are so high',
    ],
  },
  'Thailand': {
    positive: [
      'Why Thai street food is the best in the world',
      'Why Thailand tourism is unmatched',
      'Why Thai massage is legendary',
      'Why Thai islands are paradise',
    ],
    negative: [
      'Why Thailand scams target tourists',
      'Why Thailand air pollution is dangerous',
      'Why Thailand stray dogs are everywhere',
      'Why Thailand political instability continues',
    ],
  },
  'Egypt': {
    positive: [
      'Why Egypt ancient pyramids are still mysterious',
      'Why Egyptian food is underrated',
      'Why Egypt economy is rapidly growing',
      'Why Egyptian history fascinates the world',
    ],
    negative: [
      'Why Egypt overpopulation is a crisis',
      'Why Egypt tourist scams are common',
      'Why Egypt waste management is failing',
      'Why Egypt traffic is absolute chaos',
    ],
  },
  'Nigeria': {
    positive: [
      'Why Nigerian Afrobeats took over the world',
      'Why Nigerian film industry Nollywood is massive',
      'Why Nigerian comedians are hilarious',
      'Why Nigerian entrepreneurship thrives',
    ],
    negative: [
      'Why Nigeria internet scams are infamous',
      'Why Nigeria oil pollution is devastating',
      'Why Nigeria poverty persists despite oil',
      'Why Nigerian electricity is unreliable',
    ],
  },
  'Mexico': {
    positive: [
      'Why Mexican cuisine is UNESCO heritage',
      'Why Mexican celebrations are legendary',
      'Why Mexican wresting culture is unique',
      'Why Mexican beaches are stunning',
    ],
    negative: [
      'Why Mexico drug cartel violence persists',
      'Why Mexico water crisis is severe',
      'Why Mexican police corruption is notorious',
      'Why Mexico air pollution is dangerous',
    ],
  },
  'France': {
    positive: [
      'Why French food is world famous',
      'Why French fashion dominates globally',
      'Why French wine culture is unmatched',
      'Why French art and museums are legendary',
    ],
    negative: [
      'Why French bureaucracy is famously awful',
      'Why French strikes are constant',
      'Why French taxes are extremely high',
      'Why France pension crisis is boiling over',
    ],
  },
  'Vietnam': {
    positive: [
      'Why Vietnamese coffee culture is unique',
      'Why Vietnamese street food is incredible',
      'Why Vietnam economy is booming',
      'Why Vietnamese landscapes are breathtaking',
    ],
    negative: [
      'Why Vietnam has a serious karaoke problem',
      'Why Vietnam traffic is absolute chaos',
      'Why Vietnam pollution in cities is bad',
      'Why Vietnam corruption is widespread',
    ],
  },
  // Default for countries without specific examples
};

function getTopicExamples(country) {
  const specific = TOPIC_EXAMPLES[country];
  if (specific) return specific;
  // Return generic for unlisted countries
  return {
    positive: [
      `Why ${country} is amazing`,
      `Why ${country} culture is fascinating`,
      `Why ${country} should be on your bucket list`,
      `Why ${country} food is underrated`,
    ],
    negative: [
      `Why ${country} has serious issues`,
      `Why ${country} economy is struggling`,
      `Why ${country} social problems are complex`,
      `Why ${country} challenges are massive`,
    ],
  };
}

/**
 * Pick a random country, avoiding recent repeats
 */
function pickCountry(memory) {
  const used = memory?.countriesUsedThisWeek || [];
  const available = ALL_COUNTRIES.filter(c => !used.includes(c));
  const pool = available.length > 0 ? available : ALL_COUNTRIES;
  const shuffled = [...pool].sort(() => Math.random() - 0.5);
  const picked = shuffled[0];

  logger.info(`Picked country: ${picked}`);
  return picked;
}

/**
 * Generate topic guidance for the Planning Agent
 * @param {string} country - The selected country
 * @returns {Object} - { country, angle, topicExample, angleDescription }
 */
function generateTopicGuidance(country) {
  const angle = Math.random() < 0.5 ? 'positive' : 'negative';
  const examples = getTopicExamples(country);
  const availableTopics = examples[angle];
  const topicExample = availableTopics[Math.floor(Math.random() * availableTopics.length)];

  const angleDescription = angle === 'positive'
    ? `focus on the amazing, impressive, or fascinating aspects of ${country}`
    : `focus on the concerning, problematic, or controversial issues in ${country}`;

  logger.info(`Angle: ${angle} — example: "${topicExample}"`);

  return {
    country,
    angle,
    topicExample,
    angleDescription,
    allExamples: examples,
  };
}

module.exports = { pickCountry, generateTopicGuidance, getTopicExamples, ALL_COUNTRIES };