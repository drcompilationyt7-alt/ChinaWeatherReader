/**
 * LLM half of the nightly quiz pool mining (core/quiz/quiz_pool_grow.py calls this): writes CANDIDATE items, never touches
 * the live bank. Every item is validated and fact-checked by a second call (pop-quiz-content.js propose); the miner then
 * fetches their pictures, and Claude reviews everything before import.
 *
 *   node pipeline/quiz-pool-llm.js <spec.json> <out.json>
 *   spec: {"jobs": [{"fmt": "emoji"|"trivia"|"wyr", "topic": "...", "n": 8, "edition": "halloween"?}],
 *          "songs": {"n": 8}?, "avoid": {"emoji": [...], "trivia": [...], "wyr": [...], "song": ["title|artist", ...]}}
 *   out:  {"items": {"emoji": [...], "trivia": [...], "wyr": [...], "song": [...]}, "log": [...]}
 *
 * K-pop songs: title, artist, year and difficulty only. NEVER lyrics (the prompt forbids them and the validator drops any
 * field longer than a title).
 */
const fs = require('fs');
const { propose, loadBank, loadThemes, llmJSON, resetLLM } = require('./pop-quiz-content');
const BATCH = 6;   // items per LLM call (long lists came back cut off mid-JSON)

const SONG_SYS = 'You help a YouTube Shorts music quiz channel pick K-pop songs. Only real, officially released songs with an '
  + 'official music video on YouTube. Never write or quote lyrics. English only.';

async function proposeSongs(n, avoid) {
  const r = await llmJSON(SONG_SYS, `List ${n} K-pop songs that were big hits (2012-2025) and are NOT in this list: ${avoid.slice(-150).join('; ')}. `
    + 'Mix boy groups, girl groups and soloists. Each: "title" (official English / romanised title as on the official MV), "artist" '
    + '(as credited), "year", "difficulty" 1-3 (1 = everyone knows it). No lyrics, no descriptions.\n'
    + 'Return JSON: {"items": [{"title": "", "artist": "", "year": 2020, "difficulty": 1}]}');
  const ok = it => it && typeof it.title === 'string' && typeof it.artist === 'string' && it.title.length >= 1 && it.title.length <= 40
    && it.artist.length <= 40 && !/[<>\n]/.test(it.title + it.artist) && Number(it.year) >= 1995 && Number(it.year) <= 2026;
  const seen = new Set(avoid.map(a => a.toLowerCase()));
  const cand = (r && Array.isArray(r.items) ? r.items : []).filter(ok).filter(it => !seen.has(`${it.title}|${it.artist}`.toLowerCase()));
  if (!cand.length) return [];
  const v = await llmJSON('You are a strict fact-checker for a K-pop quiz. When unsure, reject.',
    'For each: is it a real released song by exactly that artist (title spelled as on the official release), released in that year '
    + `(+-1), with an official music video?\n${cand.map((it, i) => `${i}. "${it.title}" by ${it.artist} (${it.year})`).join('\n')}\n`
    + 'Return JSON: {"ok": [indexes that pass]}');
  const keep = new Set(((v && v.ok) || []).map(Number));
  return cand.filter((_, i) => keep.has(i)).map(it => ({
    title: it.title.trim(), artist: it.artist.trim(), type: 'kpop', year: Number(it.year),
    difficulty: Math.min(3, Math.max(1, Number(it.difficulty) || 2)),
    query: `${it.artist.trim()} ${it.title.trim()} M/V`,
    match: [[it.title.trim().toLowerCase()], [it.artist.trim().toLowerCase()]],
  }));
}

async function main() {
  const [specFile, outFile] = process.argv.slice(2);
  const spec = JSON.parse(fs.readFileSync(specFile, 'utf8'));
  const bank = loadBank();
  const themes = loadThemes().themes || {};
  const out = { items: { emoji: [], trivia: [], wyr: [], song: [] }, log: [] };
  for (const j of spec.jobs || []) {
    const ed = j.edition && themes[j.edition] ? { ...themes[j.edition], id: j.edition } : null;
    let total = 0;
    for (let left = j.n || 8; left > 0; left -= BATCH) {
      resetLLM();
      try {
        const avoid = [...((spec.avoid || {})[j.fmt] || []), ...out.items[j.fmt].map(it => (j.fmt === 'emoji' ? it.answer : j.fmt === 'trivia' ? it.question : `${it.a} / ${it.b}`))];
        const got = await propose(bank, j.fmt, j.topic, Math.min(BATCH, left), ed, avoid);
        out.items[j.fmt].push(...got.map(it => ({ ...it, mined: new Date().toISOString().slice(0, 10) })));
        total += got.length;
      } catch (e) { out.log.push(`${j.fmt}/${j.topic}: failed ${String(e.message || e).slice(0, 80)}`); }
    }
    out.log.push(`${j.fmt}/${j.topic}${ed ? `/${ed.id}` : ''}: ${total}`);
  }
  if (spec.songs && spec.songs.n) {
    const have = (bank.song || []).map(s => `${s.title}|${s.artist}`);
    for (let left = spec.songs.n; left > 0; left -= 10) {
      resetLLM();
      try {
        const got = await proposeSongs(Math.min(10, left), [...have, ...((spec.avoid || {}).song || []), ...out.items.song.map(s => `${s.title}|${s.artist}`)]);
        out.items.song.push(...got);
      } catch (e) { out.log.push(`song/kpop: failed ${String(e.message || e).slice(0, 80)}`); }
    }
    out.log.push(`song/kpop: ${out.items.song.length}`);
  }
  fs.writeFileSync(outFile, JSON.stringify(out, null, 1));
  console.log(JSON.stringify({ ok: true, log: out.log }));
}

main().catch(e => { console.error(e); process.exit(1); });
