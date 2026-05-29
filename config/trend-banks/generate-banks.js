#!/usr/bin/env node
/**
 * Generates trend bank JSON files for all countries
 * Run once: node config/trend-banks/generate-banks.js
 */
const fs = require('fs');
const path = require('path');

const countries = {
  'China': {
    keywords: ['chinese trend', 'beautiful Chinese girl', 'Chinese love story', 'colour wheel trend', 'douyin', '抖音', '舞蹈', 'chinese street dance', '中国街舞', 'douyin viral 2026'],
    suffix: '#shorts #tiktok #reels #douyin',
    notes: 'China-specific queries MUST include #douyin in addition to standard hashtags'
  },
  'Japan': {
    keywords: ['japanese trend', 'japanese fashion', 'japanese street', 'kawaii', 'japan vlog', '日本ダンス', 'japanese dance viral', 'tokyo night life'],
    suffix: '#shorts #tiktok #reels',
    notes: 'Japanese content often features fashion, food, and street culture'
  },
  'South Korea': {
    keywords: ['kpop', 'blackpink', 'bts', 'korean fashion', 'korean makeup', 'seoul', 'kpop dance', 'korean street', 'korean dance challenge'],
    suffix: '#shorts #tiktok #reels',
    notes: 'K-pop and Korean fashion dominate this category'
  },
  'Thailand': {
    keywords: ['thai trend', 'thai street food', 'bangkok', 'thai girl', 'thai dance', 'thai tiktok', 'thai comedy', 'muay thai viral'],
    suffix: '#shorts #tiktok #reels',
    notes: 'Thai content ranges from comedy to food to martial arts'
  },
  'Vietnam': {
    keywords: ['vietnam trend', 'hanoi', 'saigon', 'vietnam street', 'Ai Đưa Em Về', 'nhạc hot tik tok', 'vietnam dance', 'vietnamese viral'],
    suffix: '#shorts #tiktok #reels',
    notes: 'Vietnamese music trends are huge on TikTok'
  },
  'India': {
    keywords: ['indian trend', 'bollywood', 'mumbai', 'delhi', 'indian wedding', 'indian dance', 'bhojpuri', 'indian comedy viral'],
    suffix: '#shorts #tiktok #reels',
    notes: 'Indian content is diverse — Bollywood, comedy, dance, weddings'
  },
  'Indonesia': {
    keywords: ['indonesian trend', 'jakarta', 'bali', 'indonesia viral', 'indonesia dance', 'tiktok indonesia', 'indonesian comedy'],
    suffix: '#shorts #tiktok #reels',
    notes: 'Indonesian TikTok is massive — dance and comedy dominate'
  },
  'Brazil': {
    keywords: ['brazil trend', 'funk', 'rio', 'brazil dance', 'samba', 'funk brasileiro', 'tiktok brasil', 'brazilian meme'],
    suffix: '#shorts #tiktok #reels',
    notes: 'Brazilian funk and dance content is globally viral'
  },
  'Mexico': {
    keywords: ['mexico trend', 'mexico dance', 'latin', 'ciudad de mexico', 'corridos', 'regional mexicano', 'mexican viral'],
    suffix: '#shorts #tiktok #reels',
    notes: 'Regional Mexican music and dance trends are huge'
  },
  'France': {
    keywords: ['france trend', 'paris', 'french fashion', 'fendi', 'french tiktok', 'musique française', 'french viral'],
    suffix: '#shorts #tiktok #reels',
    notes: 'French fashion and Parisian lifestyle content'
  },
  'Germany': {
    keywords: ['germany trend', 'berlin', 'german', 'munich', 'german tiktok', 'deutsche musik', 'german viral'],
    suffix: '#shorts #tiktok #reels',
    notes: 'German content includes tech, cars, and party culture'
  },
  'Italy': {
    keywords: ['italy trend', 'italian fashion', 'fendi', 'milan', 'rome', 'prada', 'italian tiktok', 'musica italiana'],
    suffix: '#shorts #tiktok #reels',
    notes: 'Italian fashion, food, and lifestyle content'
  },
  'Spain': {
    keywords: ['spain trend', 'barcelona', 'madrid', 'spanish dance', 'españa tiktok', 'música española', 'flamenco viral'],
    suffix: '#shorts #tiktok #reels',
    notes: 'Spanish dance and music content'
  },
  'UK': {
    keywords: ['uk trend', 'london', 'british', 'uk viral', 'uk tiktok', 'uk rap', 'TikTok Viral Trend', 'london street'],
    suffix: '#shorts #tiktok #reels',
    notes: 'UK drill, grime, and London street culture'
  },
  'Egypt': {
    keywords: ['egypt trend', 'cairo', 'arabic', 'egypt viral', 'egypt tiktok', 'arabic music', 'mahraganat'],
    suffix: '#shorts #tiktok #reels',
    notes: 'Egyptian mahraganat music and comedy are viral'
  },
  'Nigeria': {
    keywords: ['nigeria trend', 'lagos', 'afrobeat', 'nigeria dance', 'naija', 'afrobeats', 'nigeria tiktok', 'nigerian meme'],
    suffix: '#shorts #tiktok #reels',
    notes: 'Afrobeats and Nigerian dance challenges are global'
  },
  'Australia': {
    keywords: ['australia trend', 'sydney', 'melbourne', 'aussie', 'australian tiktok', 'australia wildlife', 'aussie slang'],
    suffix: '#shorts #tiktok #reels',
    notes: 'Australian wildlife, slang, and outdoor lifestyle'
  },
  'Global': {
    keywords: ['viral trend', 'trending now', 'world viral', 'internet culture', 'global meme', 'trend alert'],
    suffix: '#shorts #tiktok #reels',
    notes: 'Generic global queries when no specific country is targeted'
  }
};

const dir = __dirname;
for (const [country, data] of Object.entries(countries)) {
  const fileName = country.toLowerCase().replace(/ /g, '-');
  const bank = {
    country,
    lastUpdated: new Date().toISOString().split('T')[0],
    querySuffix: data.suffix,
    keywords: data.keywords.map(k => ({
      term: k,
      added: new Date().toISOString().split('T')[0],
      status: 'active'
    })),
    trendingSongs: [],
    blockedKeywords: [],
    notes: data.notes
  };

  const filePath = path.join(dir, `${fileName}.json`);
  fs.writeFileSync(filePath, JSON.stringify(bank, null, 2));
  console.log(`Created: ${fileName}.json (${data.keywords.length} keywords)`);
}

console.log(`\nTotal: ${Object.keys(countries).length} trend banks created`);