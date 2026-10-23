/**
 * Topic / format consistency of quiz narrator lines and title cards (rules: core/quiz/line-rules.json, the same rule set as
 * core/quiz/line_check.py, which swaps failing lines before the TTS). 2026-10-23: a K-pop song emoji quiz said
 * "Guess the anime from these emojis!".
 *
 *   problems(plan, text, {kind, name, others, n}) -> reasons ([] = fine)
 *     kind 'narration' (intro / outro / reveal / pool line), 'screen' (title card), 'content' (trivia question, wyr option,
 *     duel question: counts and placeholders only). name / others: a reveal says its own answer and no other round's.
 *   checkSpoken(plan, spoken, screen) -> problems of a rendered video's lines (render result .spoken / .screen)
 */
const fs = require('fs');
const path = require('path');

const RULES = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'core', 'quiz', 'line-rules.json'), 'utf8'));
// Python regex -> JS: the rule file only uses syntax both understand (\b, groups, ?, |); flags: case-insensitive
const CATS = Object.fromEntries(Object.entries(RULES.categories).map(([k, v]) => [k, new RegExp(v, 'i')]));
const EDS = Object.fromEntries(Object.entries(RULES.editions).map(([k, v]) => [k, new RegExp(v, 'i')]));
const NUM = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10 };
const NUMS = `\\d+|${Object.keys(NUM).join('|')}`;
const COUNT = new RegExp(`\\b(${NUMS})\\s+(?:\\w+\\s+)?(?:${RULES.count_nouns})\\b`, 'gi');
const ALL_N = new RegExp(`\\ball\\s+(${NUMS})\\b`, 'gi');
const SLASH = /\b(\d+)\s*\/\s*(\d+)\b/g;
const CAPITAL_OK = new RegExp(RULES.capital_ok);
const esc = s => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const editionId = plan => (plan.edition && (plan.edition.id || plan.edition)) || null;

/** Word categories a line of this quiz may use. */
function allowed(plan) {
  const t = RULES.topic[plan.topic];
  if (t === '*') return new Set(Object.keys(CATS));
  return new Set([...(RULES.format[plan.format] || []), ...(t || []), ...(RULES.format_topic[`${plan.format}|${plan.topic}`] || [])]);
}

const num = s => (/^\d+$/.test(s) ? Number(s) : NUM[s.toLowerCase()]);

function problems(plan, text, { kind = 'narration', name = null, others = [], n = null } = {}) {
  const s = String(text || '').trim();
  if (!s) return [];
  const out = [];
  if (/[{}]|\bundefined\b|\bNaN\b/.test(s)) out.push('placeholder left in the line');
  n = n != null ? n : ((plan.rounds || []).length);
  let body = s;
  if (name) {
    if (!s.toLowerCase().includes(String(name).toLowerCase())) out.push(`reveal does not say its answer "${name}"`);
    body = body.replace(new RegExp(esc(name), 'gi'), ' ');
  }
  for (const o of others || []) {
    if (o && (!name || !String(name).toLowerCase().includes(String(o).toLowerCase())) && new RegExp(`\\b${esc(o)}\\b`, 'i').test(body)) {
      out.push(`names another round's answer "${o}"`);
    }
  }
  if (kind !== 'content') {
    const ok = allowed(plan);
    for (const [k, rx] of Object.entries(CATS)) {
      const m = !ok.has(k) && body.match(rx);
      if (m) out.push(`wrong ${k} word "${m[0]}" for ${plan.format} / ${plan.topic}`);
    }
  }
  if (kind === 'narration') {
    const ed = editionId(plan);
    for (const [k, rx] of Object.entries(EDS)) if (k !== ed && rx.test(body)) out.push(`names the ${k} edition (${ed ? `${ed} edition` : 'plain video'})`);
  }
  for (const m of body.matchAll(COUNT)) {
    const v = num(m[1]);
    if (v != null && v !== n && v > 1 && !(plan.format === 'duel' && v === 2)) out.push(`says "${m[0]}" but the video has ${n} rounds`);
  }
  for (const m of body.matchAll(ALL_N)) { const v = num(m[1]); if (v != null && v !== n) out.push(`says "${m[0]}" but the video has ${n} rounds`); }
  for (const m of body.matchAll(SLASH)) if (m[1] === m[2] && Number(m[1]) !== n) out.push(`says "${m[0]}" but the video has ${n} rounds`);
  return out;
}

/** A rendered video's spoken lines ([{slot, kind, line, name, others}]) and title card ([title, subtitle]). */
function checkSpoken(plan, spoken, screen) {
  const out = [];
  for (const l of spoken || []) {
    for (const p of problems(plan, l.line, { kind: l.kind, name: l.name, others: l.others })) out.push(`${l.slot} "${String(l.line).slice(0, 60)}": ${p}`);
  }
  for (const t of screen || []) for (const p of problems(plan, t, { kind: 'screen' })) out.push(`screen "${t}": ${p}`);
  return out;
}

/** Capitalised words an AI line may use mid-sentence (topic / place / edition words, not names). */
const capitalOk = w => CAPITAL_OK.test(w);

module.exports = { problems, checkSpoken, allowed, capitalOk, RULES };
