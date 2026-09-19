/**
 * Packaging rules shared by the metadata optimizer: what a title, an on-video title, a hashtag
 * set or a description line may contain, and the helpers to render the arm templates.
 *
 *   titles        <= 70 characters, no < or > (YouTube rejects them), the core keyword present,
 *                 no invented numbers ("99% fail"), no fake claims, never sexual, no sad / crying emoji,
 *                 "IMPOSSIBLE" only when the video has a hard round
 *   on-video      <= 26 characters, Latin letters only and NO emoji: the video font cannot draw
 *                 emoji (they render as squares); the renderer pastes emoji next to it as images
 *   hashtags      1-3 specific ones; generic reach tags (#viral #fyp ...) are refused
 *
 * Templates: {name} is replaced by a fact; a template with a missing fact does not render
 * (the arm is not eligible for this video); [[ ... ]] is an optional part, dropped when a
 * fact inside it is missing.
 */

// sad / crying faces: never (the owner: "what is that sad face emoji")
const SAD_EMOJI = ['😭', '😢', '🥲', '😿', '😞', '😔', '😟', '😥', '😪', '😓', '☹', '🙁', '😩', '😫', '💔', '🥺', '😰',
  '😦', '😧', '😨', '😣', '😖', '🤧', '😾'];
// energetic ones a proposed emoji set may use
const ENERGETIC_EMOJI = ['🔥', '⚡', '🎧', '✨', '🎶', '💿', '🗿', '🎵', '🎤', '💥', '🚀', '🏆', '👀', '🤯', '💜', '🌍', '🌏',
  '🌎', '🚩', '🗺️', '🏛️', '⏱️', '🎬', '📸', '✈️', '🍜', '🦊', '👥', '🤔', '⚔️', '😳', '📏', '🏙️', '✌️', '🎌', '🦈', '🧠',
  '👑', '💯', '🎸', '🥁', '🎹', '🌟', '💫', '😎', '🤩', '🙌', '👏', '💃', '🕺', '🎮', '🍡', '🗾', '🇯🇵', '🇰🇷', '🇨🇳'];
// hashtags that say nothing about the video: the algorithm ignores them, viewers read them as spam
const GENERIC_TAGS = new Set(['viral', 'fyp', 'foryou', 'foryoupage', 'fy', 'trending', 'trend', 'explore', 'explorepage',
  'xyzbca', 'like', 'likes', 'subscribe', 'sub', 'follow', 'blowup', 'viralvideo', 'viralshorts', 'shortsfeed',
  'youtubeshorts', 'shortvideo', 'ytshorts', 'reels', 'tiktok', 'capcut']);

// (\u{FE0F} variation selector, \u{200D} zero-width joiner, \u{20E3} keycap)
const EMOJI_RE = /\p{Regional_Indicator}{2}|\p{Extended_Pictographic}(?:\u{FE0F}|\u{20E3})?(?:\u{200D}\p{Extended_Pictographic}\u{FE0F}?)*/gu;
const EMOJI_CHAR_RE = /[\p{Extended_Pictographic}\p{Regional_Indicator}\u{200D}\u{FE0F}\u{20E3}]/gu;
const HAS_EMOJI_RE = /[\p{Extended_Pictographic}\p{Regional_Indicator}\u{20E3}]/u;
const ACCENTS_RE = /[\u{300}-\u{36F}]/gu;
const VS16 = String.fromCodePoint(0xFE0F);

const SEXUAL_RE = /\b(sexy|sexiest|sexual|hot girls?|hottest girls?|thicc|thick girls?|nsfw|nude|nudes|naked|lewd|hentai|ecchi|onlyfans|boobs?|booty|butt|seductive|thirst ?trap|18\+|r18|oppai|fan ?service|bikini|lingerie|body count)\b/i;
const CLAIM_RE = /\b(gone wrong|not clickbait|no clickbait|you won'?t believe|leaked|official (video|audio|mv)|banned video|must watch|100% real|real footage|world record|breaks the internet|broke the internet|guaranteed)\b/i;
const FAKE_STAT_RE = [
  /\d+(?:[.,]\d+)?\s?%/,                                    // "99%", "1 %"
  /\b\d+(?:[.,]\d+)?\s?percent\b/i,
  /\b(?:only|just)\s+(?:1|one|2|two|3|5|10)\s+(?:in|out of)\s+\d/i,  // "only 1 in 1000"
  /\b\d+(?:[.,]\d+)?\s?(?:k|m|b|million|billion|thousand)\s+(?:views|people|fans|viewers)\b/i,
  /\b(?:everyone|nobody|no one|99)\s+(?:fails|failed|gets? (?:it|this) wrong)\b/i,
];

const len = s => [...String(s || '')].length;
const clean = s => String(s == null ? '' : s).replace(/[<>]/g, '').replace(/\s+/g, ' ').trim();
const stripEmoji = s => String(s || '').replace(EMOJI_CHAR_RE, '').replace(/\s+/g, ' ').trim();
const emojiList = s => String(s || '').match(EMOJI_RE) || [];
const hashtagsIn = s => (String(s || '').match(/#[\p{L}\p{N}_]+/gu) || []).map(t => t.slice(1));

/** lowercase, accents and punctuation removed ("Rosé", "K-Pop!" -> "rose", "k pop") */
function norm(s) {
  return String(s || '').toLowerCase().normalize('NFD').replace(ACCENTS_RE, '')
    .replace(EMOJI_CHAR_RE, ' ').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
}
const squash = s => norm(s).replace(/ /g, '');
/** true if `phrase` occurs in `text` (spacing and punctuation ignored: "k-pop" = "kpop") */
const contains = (text, phrase) => !!squash(phrase) && squash(text).includes(squash(phrase));

/** "Chainsaw Man" -> "chainsawman", "ROSÉ" -> "rose" ('' when unusable as a hashtag) */
function slugTag(s) {
  const t = String(s || '').normalize('NFD').replace(ACCENTS_RE, '').replace(/[^A-Za-z0-9]/g, '').toLowerCase();
  return t.length >= 2 && t.length <= 30 ? t : '';
}

function hasSad(s) {
  const t = String(s || '');
  return SAD_EMOJI.some(e => t.includes(e));
}

/** Replace sad / crying emoji with an energetic one (keeps the template's rhythm). */
function unsad(s, repl = '🔥') {
  let t = String(s || '');
  for (const e of SAD_EMOJI) t = t.split(e + VS16).join(repl).split(e).join(repl);
  return t.replace(/(🔥)(?:\s*🔥)+/gu, '$1').replace(/\s+/g, ' ').trim();
}

/**
 * Fill a template. Returns null when a required {fact} is missing.
 * {name} -> vars.name; [[ ... ]] optional segment; {e} -> vars.e (emoji, may be '').
 */
function fill(template, vars) {
  if (template == null) return null;
  const val = k => {
    const v = vars[k];
    return v === undefined || v === null || v === false || (typeof v === 'string' && !v.trim() && k !== 'e') ? null : String(v);
  };
  let missing = false;
  let out = String(template).replace(/\[\[([\s\S]*?)\]\]/g, (_, seg) => {
    let segMissing = false;
    const r = seg.replace(/\{(\w+)\}/g, (m, k) => { const v = val(k); if (v === null) segMissing = true; return v || ''; });
    return segMissing ? '' : r;
  });
  out = out.replace(/\{(\w+)\}/g, (m, k) => { const v = val(k); if (v === null) { missing = true; return ''; } return v; });
  if (missing) return null;
  return out.replace(/\s+([,.!?)])/g, '$1').replace(/\(\s+/g, '(').replace(/\s+/g, ' ').trim();
}

/** Placeholders a template uses ({e} excluded). */
function placeholders(template) {
  return [...new Set((String(template || '').match(/\{(\w+)\}/g) || []).map(m => m.slice(1, -1)).filter(k => k !== 'e'))];
}

/**
 * Validate a title. rules: {maxLen, must: [[alternatives], ...], allowImpossible, forbidden: [answers], maxHashtags}
 * Returns {ok, reasons}.
 */
function validateTitle(title, rules = {}) {
  const t = String(title || '');
  const reasons = [];
  const maxLen = rules.maxLen || 70;
  if (!t.trim()) return { ok: false, reasons: ['empty'] };
  if (len(t) > maxLen) reasons.push(`too long (${len(t)} > ${maxLen})`);
  if (len(stripEmoji(t)) < 6) reasons.push('too short');
  if (/[<>]/.test(t)) reasons.push('contains < or >');
  if (/[\r\n]/.test(t)) reasons.push('line break');
  if (hasSad(t)) reasons.push('sad / crying emoji');
  if (SEXUAL_RE.test(t)) reasons.push('sexual / suggestive');
  if (CLAIM_RE.test(t)) reasons.push('clickbait claim');
  if (FAKE_STAT_RE.some(re => re.test(stripEmoji(t).replace(/#[\p{L}\p{N}_]+/gu, '')))) reasons.push('invented statistic');
  if (/\bimpossible\b/i.test(t) && !rules.allowImpossible) reasons.push('"impossible" without a hard round');
  if (/\b(part|pt\.?)\s*[2-9]\b/i.test(t) && !rules.allowPart) reasons.push('fake part number');
  const tags = hashtagsIn(t);
  if (tags.length > (rules.maxHashtags ?? 3)) reasons.push('too many hashtags');
  if (tags.some(x => GENERIC_TAGS.has(x.toLowerCase()))) reasons.push('generic hashtag');
  if (emojiList(t).length > 3) reasons.push('too many emoji');
  const letters = stripEmoji(t).replace(/#[\p{L}\p{N}_]+/gu, '').replace(/[^\p{L}]/gu, '');
  if (letters.length > 12 && letters.replace(/[^\p{Lu}]/gu, '').length / letters.length > 0.7) reasons.push('all caps');
  for (const group of rules.must || []) {
    const alts = [].concat(group).filter(Boolean);
    if (alts.length && !alts.some(a => contains(t, a))) reasons.push(`missing keyword "${alts[0]}"`);
  }
  for (const a of rules.forbidden || []) {
    if (a && squash(a).length > 3 && contains(t, a)) { reasons.push('reveals an answer'); break; }
  }
  return { ok: !reasons.length, reasons };
}

/** On-video title: short, instantly readable, Latin letters only, no emoji (they render as squares). */
function validateOnVideo(text, rules = {}) {
  const t = String(text || '');
  const reasons = [];
  if (!t.trim()) return { ok: false, reasons: ['empty'] };
  if (len(t) > (rules.maxLen || 26)) reasons.push(`too long (${len(t)} > ${rules.maxLen || 26})`);
  if (len(t) < 3) reasons.push('too short');
  if (HAS_EMOJI_RE.test(t)) reasons.push('emoji in the video text');
  if (!/^[\p{Script=Latin}\p{N} .,!?'’&:;()\-+/]+$/u.test(t)) reasons.push('characters the video font cannot draw');
  if (/[<>#]/.test(t)) reasons.push('contains < > or #');
  if (SEXUAL_RE.test(t)) reasons.push('sexual / suggestive');
  if (CLAIM_RE.test(t)) reasons.push('clickbait claim');
  if (FAKE_STAT_RE.some(re => re.test(t))) reasons.push('invented statistic');
  if (/\bimpossible\b/i.test(t) && !rules.allowImpossible) reasons.push('"impossible" without a hard round');
  for (const a of rules.forbidden || []) {
    if (a && squash(a).length > 3 && contains(t, a)) { reasons.push('reveals an answer'); break; }
  }
  return { ok: !reasons.length, reasons };
}

/** A description / comment line: plain text, no sad emoji, no claims, no hashtags. */
function validateLine(text, rules = {}) {
  const t = String(text || '');
  const reasons = [];
  if (!t.trim()) return { ok: false, reasons: ['empty'] };
  if (len(t) > (rules.maxLen || 200)) reasons.push('too long');
  if (/[<>]/.test(t)) reasons.push('contains < or >');
  if (hasSad(t)) reasons.push('sad / crying emoji');
  if (SEXUAL_RE.test(t)) reasons.push('sexual / suggestive');
  if (CLAIM_RE.test(t)) reasons.push('clickbait claim');
  if (FAKE_STAT_RE.some(re => re.test(t))) reasons.push('invented statistic');
  if (/#[\p{L}\p{N}_]+/u.test(t) && !rules.allowHashtags) reasons.push('hashtag in the line');
  if (/https?:\/\//i.test(t)) reasons.push('link');
  if (/\bimpossible\b/i.test(t) && !rules.allowImpossible) reasons.push('"impossible" without a hard round');
  for (const group of rules.must || []) {
    const alts = [].concat(group).filter(Boolean);
    if (alts.length && !alts.some(a => contains(t, a))) reasons.push(`missing keyword "${alts[0]}"`);
  }
  for (const a of rules.forbidden || []) {
    if (a && squash(a).length > 3 && contains(t, a)) { reasons.push('reveals an answer'); break; }
  }
  return { ok: !reasons.length, reasons };
}

/** A hashtag (without #) a proposed set may use. */
function validTag(tag) {
  const t = String(tag || '').replace(/^#/, '');
  return /^[a-z0-9]{2,30}$/.test(t) && !GENERIC_TAGS.has(t);
}

/** An emoji a proposed set may use: energetic list only. */
function validEmoji(e) {
  const t = String(e || '').split(VS16).join('');
  return !!t && !hasSad(t) && ENERGETIC_EMOJI.some(x => x.split(VS16).join('') === t);
}

module.exports = {
  SAD_EMOJI, ENERGETIC_EMOJI, GENERIC_TAGS,
  len, clean, stripEmoji, emojiList, hashtagsIn, norm, squash, contains, slugTag, hasSad, unsad,
  fill, placeholders, validateTitle, validateOnVideo, validateLine, validTag, validEmoji,
};
