/**
 * Selftest of the automatic song top-up gates (pipeline/song-topup.js), offline: a fake YouTube search and a fake proposer.
 *   node scripts/song-topup-selftest.js
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const T = fs.mkdtempSync(path.join(os.tmpdir(), 'topup-test-'));
process.env.MEMORY_DIR = path.join(T, 'memory');
process.env.SONG_CANDIDATES_FILE = path.join(T, 'cand.json');
process.env.META_LLM = 'off';
fs.mkdirSync(process.env.MEMORY_DIR, { recursive: true });
const top = require('../pipeline/song-topup');
const al = require('../core/quiz/assets/anime-list.json');
const list = al.anime || Object.values(al)[0];
const nsfwShow = list.find(x => x.nsfw).title;
const okShow = list.find(x => !x.nsfw && x.title === 'Attack on Titan') || list.find(x => !x.nsfw);

let fails = 0;
const check = (c, w) => { console.log((c ? 'PASS  ' : 'FAIL  ') + w); if (!c) fails++; };
const NOW = new Date('2026-11-03T05:00:00Z');
const day = d => new Date(NOW.getTime() - d * 86400000).toISOString().slice(0, 10).replace(/-/g, '');

// fake world: song "name" -> {views, trend, clips}
const world = {};
const add = (name, o) => { world[name] = { views: 5e6, trend: 5, clips: 20, ...o }; };
const cand = (name, o = {}) => ({ name, query: `Artist ${name} official audio`, artist: 'Artist', theme: 'anime', source: okShow.title, kind: 'anime', aliases: [], ...o });
const fewShow = list.find(x => !x.nsfw && x.title !== okShow.title).title;
// source quality per clip source: n videos of a given height / title; the SD / subtitled / edit sources must not count
const secondShow = list.find(x => !x.nsfw && x.title !== okShow.title && x.title !== fewShow).title;
const thirdShow = list.find(x => !x.nsfw && ![okShow.title, fewShow, secondShow].includes(x.title)).title;
const fourthShow = list.find(x => !x.nsfw && ![okShow.title, fewShow, secondShow, thirdShow].includes(x.title)).title;
const clipsBySource = { [okShow.title]: { n: 14, h: 1080, t: 'scene' }, [fewShow]: { n: 3, h: 1080, t: 'scene' },
  [secondShow]: { n: 30, h: 480, t: 'scene' }, [thirdShow]: { n: 30, h: 1080, t: 'eng sub scene' }, [fourthShow]: { n: 14, h: 1080, t: 'dirty scene' },
  'Test C-Pop Star': { n: 14, h: 1080, t: 'official MV' }, 'Test Drama': { n: 14, h: 1080, t: 'scene' }, 'Sub Anime Name': { n: 0, h: 0, t: '' } };
const QUALITY_Q = /creditless|official MV|official clips|anime scenes|performance stage|drama scenes|OST MV|official live clip|official trailer|stage 4K/;
async function search(q, { byDate } = {}) {
  if (Object.keys(clipsBySource).some(k => q.startsWith(k))) { const e = Object.entries(clipsBySource).find(([k]) => q.startsWith(k)); const o = e[1]; return Array.from({ length: o.n }, (_, i) => ({ id: e[0].slice(0, 3) + 'c' + i, title: `${o.t} ${i}`, channel: 'c', views: 90000, duration: 100, height: o.h, description: '' })); }
  const name = Object.keys(world).find(n => q.toLowerCase().includes(n));
  if (!name) {
    const src = q.toLowerCase();
    const w = Object.entries(world).find(([, v]) => v.srcKey && src.includes(v.srcKey));
    if (!w) return [];
  }
  const w = world[name];
  if (!w) return [];
  if (/ (edit|acapella)$/.test(q)) return Array.from({ length: w.trend }, (_, i) => ({ id: 't' + i, title: `${name} edit`, channel: 'c', views: 1000, duration: 30, uploadDate: day(i * 5) }));
  return [{ id: 'off_' + name.replace(/ /g, ''), title: `Artist - ${name} (Official Audio)`, channel: 'Artist Official', views: w.views, duration: 200, uploadDate: w.released || '20240101' }];
}
const probeLog = [];
const probe = async r => { probeLog.push(r.id); return r.title.startsWith('dirty') ? { ok: true, clean: false, reasons: ['burned-in text (0.00 top / 0.12 bottom)'] } : { ok: true, clean: true, reasons: [] }; };
const deps = props => ({ propose: async () => props, search, probe, now: () => NOW, stats: { videos: [{ videoId: 'BV', regionBlocked: 80 }] } });

(async () => {
  add('sd only'); add('subbed only'); add('dirty previews'); add('cpop song'); add('drama ost'); add('latin song'); add('old hit', { trend: 0, views: 2e8 }); add('mid hit', { trend: 0, views: 5e7 }); add('new hit', { trend: 0, released: '20261005' });
  add('good song'); add('low views', { views: 20000 }); add('no trend', { trend: 1 }); add('few clips', { clips: 3 });
  add('nsfw song'); add('unknown song'); add('gurenge'); add('blocked one');
  for (let i = 0; i < 12; i++) add('bulk ' + String.fromCharCode(97 + i));
  const hist = { uploads: [{ song: 'blocked one', videoId: 'BV' }] };
  fs.writeFileSync(path.join(process.env.MEMORY_DIR, 'meme-history.json'), JSON.stringify(hist));
  fs.writeFileSync(path.join(T, 'blocklist-note'), '');
  const props = [cand('good song'), cand('low views'), cand('no trend'), cand('few clips', { source: fewShow }), cand('sd only', { source: secondShow }), cand('subbed only', { source: thirdShow }),
    cand('dirty previews', { source: fourthShow }),
    cand('cpop song', { theme: 'chinese', kind: 'group', source: 'Test C-Pop Star' }), cand('drama ost', { theme: 'chinese', kind: 'drama', source: 'Test Drama' }),
    cand('new hit'), cand('old hit'), cand('mid hit'), cand('latin song', { theme: 'latin', kind: 'group', source: 'Test C-Pop Star' }), cand('nsfw song', { source: nsfwShow }),
    cand('unknown song', { source: 'Totally Made Up Anime 99' }), cand('gurenge'), cand('alias hit', { name: 'jujutsu kaisen' }),
    cand('blocked one'), cand('wrong theme', { theme: 'western' })];
  const out = await top.runTopup(deps(props));
  const v = n => (out.report.find(r => r.name === n) || {}).verdict;
  const why = n => (out.report.find(r => r.name === n) || {}).why || '';
  check(v('good song') === 'approved', 'a song passing every gate is approved');
  check(v('low views') === 'rejected' && /views/.test(why('low views')), 'low official views rejected');
  check(v('no trend') === 'rejected' && /trend/.test(why('no trend')), 'no recent trend rejected');
  check(v('few clips') === 'rejected' && /source quality/.test(why('few clips')), 'too few HD sources rejected');
  check(v('sd only') === 'rejected' && /source quality/.test(why('sd only')), '480p-only sources do not count');
  check(v('subbed only') === 'rejected' && /source quality/.test(why('subbed only')), 'subtitled / edit sources do not count');
  check(v('dirty previews') === 'rejected' && /sample check/.test(why('dirty previews')) && /burned-in/.test(why('dirty previews')), 'sample previews with burned-in text reject the song');
  check(v('cpop song') === 'approved' && v('drama ost') === 'approved', 'Chinese songs (artist channel / C-drama source) can pass');
  check(v('new hit') === 'approved', 'a brand-new release passes the trend gate without edits yet');
  check(v('old hit') === 'approved' && /evergreen/.test(JSON.stringify(out.report.find(r => r.name === 'old hit').gates.popular)), 'an evergreen hit (>= 100M views) passes on popularity alone');
  check(v('mid hit') === 'rejected', 'a 50M song with no recent edits still fails');
  check(v('latin song') === 'rejected' && /Chinese/.test(why('latin song')), 'anything outside K-pop / Chinese / J-pop / anime is rejected');
  check(probeLog.length > 0 && probeLog.every(id => typeof id === 'string'), 'the sample check ran on the best sources');
  check(v('nsfw song') === 'rejected' && /NSFW/.test(why('nsfw song')), 'NSFW source rejected');
  check(v('unknown song') === 'rejected' && /AniList/.test(why('unknown song')), 'source without an AniList entry rejected');
  check(v('gurenge') === 'rejected' && /already/.test(why('gurenge')), 'duplicate of the curated list rejected');
  check(v('jujutsu kaisen') === 'rejected', 'an alias of an existing song rejected');
  check(v('wrong theme') === 'rejected', 'non-Asian theme rejected');
  const file = JSON.parse(fs.readFileSync(process.env.SONG_CANDIDATES_FILE, 'utf8'));
  check(file.approved.length === 5 && file.approved[0].probation === true && file.approved[0].approvedAt === '2026-11-03', 'approved song stored on probation');
  const rep = JSON.parse(fs.readFileSync(path.join(process.env.MEMORY_DIR, 'song-topup-2026-11.json'), 'utf8'));
  check(rep.report.every(r => r.why) && rep.report.length === props.length, 'report logs a reason for every candidate');

  // blocked in our own history (region-blocked stats)
  const b = top.blockedSongs({ videos: [{ videoId: 'BV', regionBlocked: 80 }] }, hist);
  check(b.has('blocked one'), 'songs blocked in many regions in our history are remembered');

  // monthly limit
  fs.rmSync(process.env.SONG_CANDIDATES_FILE);
  const bulk = Array.from({ length: 12 }, (_, i) => cand('bulk ' + String.fromCharCode(97 + i)));
  const o2 = await top.runTopup(deps(bulk));
  check(o2.approved.length === 10 && o2.report.filter(r => r.verdict === 'deferred').length === 2, 'at most 10 new songs a month (the rest deferred)');
  const o3 = await top.runTopup(deps([cand('good song')]));
  check(o3.approved.length === 0, 'the monthly limit also holds across runs in the same month');

  // retirement
  const f2 = { approved: [{ name: 'loser', probation: true }, { name: 'winner' }, { name: 'young' }], retired: [], rejected: [] };
  const ups = [], vids = [];
  const mk = (song, n, l) => { for (let i = 0; i < n; i++) { ups.push({ song, videoId: song + i }); vids.push({ videoId: song + i, lscore: l, ageDays: 20 }); } };
  mk('loser', 4, 0.4); mk('winner', 4, 2.0); mk('young', 2, 0.1); mk('other', 10, 1.5);
  const lose = top.findLosers(f2, { videos: vids }, { uploads: ups });
  check(lose.length === 1 && lose[0].name === 'loser', 'only a song with enough uploads far below the channel mean is retired');

  console.log(fails ? `\n${fails} FAILED` : '\nall passed');
  fs.rmSync(T, { recursive: true, force: true });
  process.exit(fails ? 1 : 0);
})();
