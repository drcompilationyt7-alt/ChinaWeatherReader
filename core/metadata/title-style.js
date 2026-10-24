/**
 * Shorts title style per channel (research 2026-10-24: C:/Users/Home/Desktop/review-shorts/TITLES-2026-10-24.md).
 *
 * Why these limits:
 *   - the Shorts player shows about 40 characters of the title before "..." (no official number; vendor measurements);
 *     the title matters mostly for search / browse / suggested, the feed is decided by the first second (YouTube help
 *     answer 16089387, 16090438). So the subject keyword must be in the first ~30 characters and the rest must be short.
 *   - competitor Shorts (sampled 2026-10-24, yt-dlp metadata): the visible text (hashtags removed) has a median of
 *     28-36 characters for anime / K-pop edits, ~42 for quiz Shorts and ~42-47 for China Shorts.
 *   - our own quiz data: keyword-first titles (median 649 views, n=28) beat "Only Real Otakus Get 3/3" (336, n=4);
 *     length alone showed no effect once the format is accounted for (small sample).
 *
 * Every Shorts title of a channel goes through check(); generators fall back to a template that passes.
 * Long-form (16:9) titles are not checked here (they keep the 100-character YouTube limit).
 */
const { len, stripEmoji, emojiList, hashtagsIn, norm, contains } = require('./rules');

const STYLES = {
  // Zero Yen Otaku: "<song / anime / character> <keyword> <2-4 word hook>" in the meme voice
  meme: {
    maxLen: 52, coreMax: 42, target: 34, maxEmoji: 1, maxHashtags: 1, maxWords: 8, frontWithin: 25, maxCapsWords: 1,
    guide: 'The SONG name is always in the title (people search "<song> edit"); the subject first (song, anime or character) '
      + 'with the search keyword ("<song> acapella", "<song> anime edit", "<character> edit | <song>"), "(slowed)" for a slowed song if it fits, '
      + 'then at most a 2-4 word hook. 20-42 characters before the emoji. One emoji at most, at the end. '
      + 'No filler ("stem build up", "goes insane on", "but every frame is"), no list of names.',
  },
  // Asian Pop Quiz: "<searchable quiz keyword>: <short challenge>" or a question that names the format
  quiz: {
    maxLen: 50, coreMax: 45, target: 38, maxEmoji: 1, maxHashtags: 1, maxWords: 9, frontWithin: 30, maxCapsWords: 1,
    guide: 'The searchable quiz keyword in the first 30 characters ("Guess the Anime Character", "Country Shape Quiz", '
      + '"Can You Name These 3 Asian Cities?"), then at most a short challenge. 25-45 characters before the emoji. '
      + 'One emoji at most. A fans / otaku challenge must still name the format ("Anime Opening Quiz: Only Otakus Get 3/3").',
  },
  // Mainland Unpacked: a plain documentary headline with China (or the place / company) up front
  china: {
    maxLen: 55, coreMax: 55, target: 44, maxEmoji: 1, maxHashtags: 0, maxWords: 10, frontWithin: 30, maxCapsWords: 0,
    front: ['china', 'chinese', 'beijing', 'shanghai', 'shenzhen', 'hong kong', 'mainland'],
    guide: 'One factual headline, 30-50 characters, with China / Chinese (or the city / company) in the first 30 characters. '
      + 'No #shorts (YouTube detects Shorts by format; the tag only uses up the visible characters), no hashtags, '
      + 'no all-caps, no claim the video does not prove.',
  },
};

// words that only add length ("stem build up aespa edit", "goes insane on believer", "but every frame is an anime girl")
const FILLER_RE = /\b(stem build( up)?|build up|stems? stack up|goes (insane|crazy|hard) on|but every frame is|every frame is an?|official (video|audio|mv)|full version|must watch)\b/i;
// generic reach tags and the tag YouTube does not need any more
const SHORTS_TAG_RE = /#(shorts?|ytshorts|youtubeshorts|shortsfeed|shortvideo)\b/i;
// short acronyms are not shouting (BTS, IVE, NCT, EV, AI, PLA, GDP)
const capsWords = core => (core.match(/\b[\p{Lu}]{5,}\b/gu) || []);

/** The part of a title the viewer reads: emoji and hashtags removed. */
const coreOf = t => stripEmoji(String(t || '').replace(/#[\p{L}\p{N}_]+/gu, ' ')).replace(/\s+/g, ' ').trim();

/** Index of the first of `phrases` in the text (spacing / punctuation ignored), -1 if none. */
function frontIndex(core, phrases) {
  const n = norm(core);
  let best = -1;
  for (const p of phrases) {
    const k = norm(p);
    if (!k) continue;
    const i = n.indexOf(k);
    if (i >= 0 && (best < 0 || i < best)) best = i;
  }
  return best;
}

/**
 * Style problems of a Shorts title for a channel ([] = fine).
 * o.front: phrases one of which must appear in the first `frontWithin` characters (the search keyword / subject).
 * o.require: phrases that must all appear in the visible text (the song name on Zero Yen Otaku).
 */
function styleReasons(title, channel, o = {}) {
  const st = typeof channel === 'object' ? channel : STYLES[channel];
  if (!st) return [];
  const t = String(title || '');
  const core = coreOf(t);
  const out = [];
  if (len(t) > st.maxLen) out.push(`style: over ${st.maxLen} characters (${len(t)})`);
  if (len(core) > st.coreMax) out.push(`style: text over ${st.coreMax} characters (${len(core)})`);
  if (emojiList(t).length > st.maxEmoji) out.push(`style: more than ${st.maxEmoji} emoji`);
  if (hashtagsIn(t).length > st.maxHashtags) out.push(`style: more than ${st.maxHashtags} hashtag${st.maxHashtags === 1 ? '' : 's'}`);
  if (SHORTS_TAG_RE.test(t)) out.push('style: #shorts or a generic Shorts tag');
  if (core.split(/\s+/).filter(Boolean).length > st.maxWords) out.push(`style: more than ${st.maxWords} words`);
  if (FILLER_RE.test(core)) out.push(`style: filler ("${core.match(FILLER_RE)[0]}")`);
  if (capsWords(core).length > st.maxCapsWords) out.push('style: shouting (too many all-caps words)');
  const words = norm(core).split(' ').filter(w => w.length >= 4 && !/^(edit|quiz|only|this|that|with|from|your)$/.test(w));
  if (new Set(words).size < words.length) out.push('style: repeats a word');
  // o.require: phrases that must be in the visible text (Zero Yen Otaku: the song name; people search "<song> edit")
  for (const r of [].concat(o.require || []).filter(Boolean)) {
    if (!contains(core, r)) out.push(`style: no "${r}" in the visible text`);
  }
  const front = [].concat(o.front || [], st.front || []).filter(Boolean);
  if (front.length) {
    const i = frontIndex(core, front);
    if (i < 0) out.push(`style: no subject keyword ("${front[0]}")`);
    else if (i > st.frontWithin) out.push(`style: keyword too late (char ${i} > ${st.frontWithin}; the player cuts at ~40)`);
  }
  return out;
}

function check(title, channel, o = {}) {
  const reasons = styleReasons(title, channel, o);
  return { ok: !reasons.length, reasons };
}

/** Keep only the first n emoji of a title (the rest of the text untouched). */
function capEmoji(title, n = 1) {
  let seen = 0;
  return String(title || '').replace(/\p{Regional_Indicator}{2}|\p{Extended_Pictographic}(?:\u{FE0F}|\u{20E3})?(?:\u{200D}\p{Extended_Pictographic}\u{FE0F}?)*/gu,
    m => (++seen <= n ? m : '')).replace(/\s+/g, ' ').trim();
}

/** Remove #shorts-type tags (never useful in a title). */
const dropShortsTag = t => String(t || '').replace(new RegExp(SHORTS_TAG_RE.source, 'gi'), ' ').replace(/\s+/g, ' ').trim();

/** The first candidate that passes the channel style (after capping emoji / dropping #shorts); null if none. */
function firstFit(candidates, channel, o = {}) {
  const st = STYLES[channel];
  for (const c0 of candidates) {
    if (!c0) continue;
    const c = capEmoji(dropShortsTag(c0), st ? st.maxEmoji : 3);
    if (check(c, channel, o).ok) return c;
  }
  return null;
}

/** "GLASSES EDITION" -> "Glasses Edition" (articles stay lowercase). */
function titleCase(s) {
  return String(s || '').toLowerCase().replace(/(^|[\s(/-])(\p{L})/gu, (m, a, b) => a + b.toUpperCase())
    .replace(/(?<=\s)(A|An|The|Of|In|On|To|By|Or|And)\b/g, w => w.toLowerCase());
}

module.exports = { STYLES, styleReasons, check, capEmoji, dropShortsTag, firstFit, coreOf, titleCase, FILLER_RE, frontIndex, contains };
