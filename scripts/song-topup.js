/**
 * Monthly AUTOMATIC top-up of the stem edit's song list: candidates from the LLM chain (core/metadata/llm.js), checked by the
 * quality gates of pipeline/song-topup.js (niche, popular + trending, producible, not a duplicate, monthly limit), approved
 * songs go to config/song-candidates.json "approved" on probation, the report to memory/song-topup-YYYY-MM.json.
 *
 *   node scripts/song-topup.js [--dry-run]     (dry run: gates run, nothing is written)
 */
const llm = require('../core/metadata/llm');
const top = require('../pipeline/song-topup');

const dry = process.argv.includes('--dry-run');

async function propose(n, have) {
  const system = 'You curate songs for an anime / J-pop / K-pop edit channel. Only real, existing songs; Japanese anime openings / endings / inserts, J-pop, K-pop and Chinese (C-pop / Mandopop / C-drama OST / donghua) only. Nothing else.';
  const user = `Name ${n} songs that are trending or popular for edits right now (this month and last), from anime openings or endings, J-pop, K-pop or Chinese music. `
    + `Not these (already used): ${have.slice(0, 150).join(', ')}.\n`
    + 'Return JSON {"songs":[{"name":"short lowercase title","query":"Artist Title official audio","artist":"Artist","native":"title in its native script for Chinese / Japanese / Korean songs, else omit","theme":"anime|jpop|kpop|chinese",'
    + '"source":"exact anime title (English or romaji), or the group / artist (official channel) for J-pop, K-pop and C-pop, or the C-drama / donghua title for a drama OST","kind":"anime|group|drama",'
    + '"aliases":["lowercase character / member names and native-script title that clip titles would mention"]}]}';
  const r = await llm.chatJSON(system, user, { timeoutMs: 90000, temperature: 0.7, maxTokens: 4000 });
  // drop template echoes and malformed rows
  return r && Array.isArray(r.songs) ? r.songs.filter(s => s && s.name && s.query && !/[|]|short lowercase title|Artist Title/i.test(`${s.name} ${s.query} ${s.source}`)) : [];
}

(async () => {
  const out = await top.runTopup({ propose, search: top.ytSearch, probe: process.env.TOPUP_PROBE === 'off' ? null : top.probeSource, now: () => new Date() }, { write: !dry });
  console.log(`candidates ${out.candidates}, approved ${out.approved.length}, retired ${out.retired.length} (LLM: ${llm.provider() || 'none'})${dry ? ' [dry run]' : ''}`);
  for (const r of out.report) console.log(`  ${r.verdict.padEnd(8)} ${r.name} | ${r.why}`);
})().catch(e => { console.error(`song top-up failed (the list stays as it is): ${e.message}`); process.exit(0); });
