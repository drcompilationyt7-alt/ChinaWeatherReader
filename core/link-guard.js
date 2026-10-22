/**
 * No external links in anything we post (descriptions, localized descriptions, comments).
 * 2026-10-22: YouTube removed a quiz and warned the channel over a description link to a fan-art site that also
 * hosts adult content. Links to YouTube itself (our own short <-> long-form links) are kept; everything else
 * (URLs, bare domains like anilist.co / x.com) is removed. Same idea as pipeline/pop-quiz-meta.js stripLinks.
 */
const TLDS = 'com|net|org|us|jp|kr|cn|tw|hk|io|co|me|tv|gg|moe|be|ly|app|info|xyz|ai|uk|de|fm|link|site|to|cc|ru|fr|ca|au|in|dev|page|art|club|top|edu|gov';
const URL_RE = /(?:https?:\/\/|www\.)(?:[^\s<>()"]|\([^\s<>()"]*\))+(?<![.,;:!?'])/gi;
const DOM_RE = new RegExp(`(?<![@\\w.-])(?:[a-z0-9-]+\\.)+(?:${TLDS})\\b(?:\\/[^\\s<>()"]*)?`, 'gi');
const YT_RE = /^(?:https?:\/\/)?(?:www\.|m\.)?(?:youtube\.com|youtu\.be)(?:\/|$)/i;

function stripLinks(text, { keepYouTube = true } = {}) {
  if (text == null) return text;
  const sub = m => (keepYouTube && YT_RE.test(m) ? m : ' ');
  return String(text).split('\n').map((line) => {
    const s = line.replace(URL_RE, sub).replace(DOM_RE, sub);
    if (s === line) return line;
    const t = s.replace(/\(\s*\)|\[\s*\]/g, ' ').replace(/[ \t]{2,}/g, ' ').replace(/\s+([,.;:!?])/g, '$1').replace(/\s*[,;]\s*$/, '').trimEnd();
    // a line that was just "Source: <link>" goes entirely
    return /\w/.test(t) && !/^[^\n]{0,40}(?::|\bvia|\bat|\bhere)\s*[^\w\s]*$/i.test(t.trim()) ? t : null;
  }).filter(l => l !== null).join('\n').replace(/\n{3,}/g, '\n\n');
}

/** Localizations object {lang: {title, description}} with the descriptions stripped. */
function stripLocalizations(loc, opts) {
  if (!loc) return loc;
  return Object.fromEntries(Object.entries(loc).map(([k, v]) => [k, v && v.description != null ? { ...v, description: stripLinks(v.description, opts) } : v]));
}

module.exports = { stripLinks, stripLocalizations };
