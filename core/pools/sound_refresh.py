"""
GROW-ONLY refresh of the SOUND pool on GitHub (owner 2026-10-17; .github/workflows/edit-pool-refresh.yml). 2026-10-20: NIGHTLY
until the pinned pool reaches its target (SOUND_POOL_TARGET), STAGED (stage -> edit-pools-staging; reviewed at session start;
import = apply-staged on CI), then monthly.

The daily sound edit (sound_11b) only uses EYE-APPROVED pinned hits (core/edit/assets/sound-hit-allowlist.json, SOUND_ALLOW_ONLY=1),
so a refresh is worth something only when it adds new pinned hits. This is the local pool-growth loop of 2026-10-13..17 with the
by-eye step replaced by a vision model and stricter automatic bars:

  mine    (per shard) source discovery (yt-dlp search, spare-account cookies, --js-runtimes node, paced, every searched query /
          seen video remembered in memory/edit-pool-fetch.json) -> core/pools/hit_scan.py scan (dense mining: production transient
          detector -> scene room -> core/edit/sound_visible.py VISIBLE-SOURCE gate, strict) -> cut (<= 3 s minis) -> source files
          deleted -> core/pools/hit_vision.py (two vision models must name ONE obvious action with confidence >= 0.85, contact
          for punches, no logo / text / FX-only / face / gesture) -> core/edit/hit_import.safety (NudeNet every 0.125 s, SigLIP
          unsafe-vs-safe, burned-in text) -> WORK/out (approved minis + verdicts + strips) = the shard artifact
  merge   (swap job, on the pulled live pool) de-duplicated against the live pool / the used ledger / the blocklists / each other
          (source moment overlap + 5-frame picture signature) -> new manifest rows (status fresh, reviewed = auto) + allowlist pins.
          GROW-ONLY: nothing already in the pool is retired or changed.
  sharpen AI sharpening of the new clips: Kaggle GPU (scripts/edit-upscale-kaggle.py, Mon-Thu UTC, monthly budget) when the
          KAGGLE_API_TOKEN secret is set, else CPU (core/edit/upscale_pool.py --device cpu) within --budget-min; the rest stay 1080p
  check   QUALITY BASELINE = the approved sound_11b pins (core/edit/assets/sound-pins-baseline.json): a new clip below the
          baseline's 10th percentile on sharpness (1080-line normalised, frames around the hit) or brightness is dropped again
          (row, pin, file); batch medians are reported
  sheet   contact sheet of the new hits (one dense strip per clip, the hit frame red) for the owner (artifact)
  baseline  compute the baseline from the pinned clips of a pool (run once on the approved pool; committed)

    python core/pools/sound_refresh.py mine --work W --shard 0/4 [--budget-min 240] [--max-sources 12] [--queries 6]
    python core/pools/sound_refresh.py merge --pool P --from SHARD_DIR ... --new NEW.json
    python core/pools/sound_refresh.py sharpen|check|sheet --pool P --new NEW.json [--budget-min M] [--out F]
    python core/pools/sound_refresh.py baseline --pool P [--out F]
    python core/pools/sound_refresh.py stage --pool P --new NEW.json --out DIR [--sheets 'report/new-hits*.jpg']
    python core/pools/sound_refresh.py apply-staged --pool P --tars T.tar ... [--reject id,id]
    python core/pools/sound_refresh.py pull-staged [--out DIR]
    python core/pools/sound_refresh.py sharpen-staged [--work DIR] [--dry]   (Kaggle GPU, Mon-Thu: the staged quality.needs_sharpen clips)
"""
import argparse
import datetime as _dt
import glob
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (os.path.join(ROOT, 'core', 'edit'), os.path.join(ROOT, 'core', 'meme'), HERE):
    sys.path.insert(0, _p)

FETCH_STATE = os.path.join(ROOT, os.environ.get('MEMORY_DIR', 'memory'), 'edit-pool-fetch.json')
ALLOW_P = os.path.join(ROOT, 'core', 'edit', 'assets', 'sound-hit-allowlist.json')
HIT_BLOCK_P = os.path.join(ROOT, 'core', 'edit', 'assets', 'sound-hit-blocklist.json')
BASELINE_P = os.environ.get('SOUND_PINS_BASELINE') or os.path.join(ROOT, 'core', 'edit', 'assets', 'sound-pins-baseline.json')
CRISP_TAGS = {'clap', 'object', 'chain'}
PREFILTER = os.environ.get('SOUND_PREFILTER', '1') != '0'
# 2026-10-27: the daily sound edit is the BEAT REMAKE; its clean-window gate (sound_edit.remake_check: a build-up free of other
# distinct transients, picture after the hit, no held frames) dropped 27 of the 36 fresh pins AFTER vision + owner review
# (the sound short fell back to stem 10-23..27). Checked here before the paid vision call; SOUND_REMAKE_GATE=0 turns it off.
REMAKE_GATE = os.environ.get('SOUND_REMAKE_GATE', '1') != '0'
TEXT_MAX = float(os.environ.get('SOUND_TEXT_MAX', '0.02'))       # per-clip burned-in text band (= hit_import.safety)
# dense mining (2026-10-17: short build-up + hit + quick cut doubled the yield per source)
DENSE = {'HIT_SCAN_SPACING': '1.5', 'HIT_SCAN_PRE_MIN': '0.3', 'HIT_SCAN_POST_MIN': '0.2', 'HIT_SCAN_PER_MIN': '16', 'HIT_SCAN_CAP': '20'}
# query generator: shows that gave eye-approved hits first, then more action shows; the suffixes that worked (fetch14-24)
SHOWS = ['Megalobox', 'Kengan Ashura', 'Baki', 'Wind Breaker', 'Blue Eye Samurai', 'Sword of the Stranger', 'Fire Force', 'Hellsing Ultimate',
         'Cyberpunk Edgerunners', 'Lazarus', 'Solo Leveling', 'Dandadan', 'Jujutsu Kaisen', 'Lycoris Recoil', 'Black Lagoon', 'Jormungand',
         'Cowboy Bebop', 'Trigun Stampede', 'Chainsaw Man', 'Spy x Family', 'Demon Slayer', 'Bleach Thousand-Year Blood War', 'Vinland Saga',
         'Samurai Champloo', 'Hajime no Ippo', 'Mob Psycho 100', 'One Punch Man', 'Hunter x Hunter', 'Attack on Titan', 'Sakamoto Days',
         'Kaiju No. 8', 'Hell\'s Paradise', 'Dorohedoro', 'Akame ga Kill', 'Gangsta', 'Psycho-Pass', 'Fate Zero', 'Fate Stay Night UBW',
         'Tokyo Revengers', 'Tokyo Ghoul', 'Devilman Crybaby', 'Castlevania', 'Ninja Scroll', 'Afro Samurai', 'Golden Kamuy', 'Dr. Stone',
         'Mashle', 'Blue Lock', 'Ranking of Kings', 'Frieren', 'Kill la Kill', 'Gurren Lagann', 'Promare', 'Sword Art Online', 'Re:Zero',
         'Fullmetal Alchemist Brotherhood', 'Rurouni Kenshin', 'Code Geass', 'Mobile Suit Gundam', 'Ghost in the Shell', 'Akira',
         'Great Pretender', 'Banana Fish', 'Bungo Stray Dogs', 'Undead Unluck', 'Tower of God', 'The God of High School', 'Baki Hanma',
         'Record of Ragnarok', 'Kakegurui', 'Detective Conan', 'Lupin III', 'Golgo 13', 'Darker than Black', 'Noir', 'Canaan']
SUFFIXES = ['fight scene', 'best fight scenes', 'gunfight scene', 'sword fight scene', 'fight scene 4K', 'all fights']
# 2026-10-20 nightly growth: more suffixes once the first ones are used up; then the old queries again (new uploads; every
# seen video is remembered, so a re-search only fetches new ones)
SUFFIXES_MORE = ['final fight scene', 'punch scene', 'sword clash scene', 'action scene 4K', 'fight 60fps', 'boss fight scene']
# 2026-10-21 yield push: more popular action shows + the fight-compilation / sakuga suffixes (searched after the lists above)
SHOWS_MORE = ['Jujutsu Kaisen Shibuya Incident', 'Jujutsu Kaisen 0', 'Demon Slayer Infinity Castle', 'Demon Slayer Swordsmith Village',
              'Demon Slayer Entertainment District', 'Chainsaw Man Reze Arc', 'One Punch Man Season 3', 'Mob Psycho 100 III',
              'Bleach TYBW', 'Solo Leveling Season 2', 'Kaiju No 8', 'Sakamoto Days', 'Dandadan Season 2', 'Frieren Beyond Journeys End',
              'Vinland Saga Season 2', 'Attack on Titan Final Season', 'My Hero Academia', 'Naruto Shippuden', 'Boruto', 'Dragon Ball Super',
              'One Piece Wano', 'One Piece Egghead', 'Black Clover', 'Blue Exorcist', 'Fire Force Season 3', 'Gachiakuta', 'Wistoria',
              'Shangri-La Frontier', 'Ninja Kamui', 'Hells Paradise', 'Mashle Magic and Muscles', 'Spy x Family Code White',
              'Lord of Mysteries', 'Tougen Anki', 'Kagurabachi', 'Gintama', 'JoJos Bizarre Adventure', 'Berserk 1997', 'Trigun Stampede',
              'Pluto', 'Ghost of Tsushima anime', 'Sword Art Online Alicization', 'Overlord', 'That Time I Got Reincarnated as a Slime',
              'The Eminence in Shadow', 'Classroom of the Elite', 'Haikyuu', 'Kengan Ashura Season 2', 'Baki Hanma Season 2', 'Kill Bill anime']
SUFFIXES_SAKUGA = ['best fight', 'sakuga', 'fight scene 4k 60fps', 'sakuga fight scene', 'best fight scene 4K', 'fight scene HD']


def log(*a):
    print('[sound_refresh]', *a, file=sys.stderr, flush=True)


def read_json(p, d):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return d


def write_json(p, d):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, 'item') else str(o))
    os.replace(p + '.tmp', p)


def today():
    return _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d')


# ------------------------------------------------------------------ queries / fetch state

POPULAR = ['Jujutsu Kaisen', 'Demon Slayer', 'Chainsaw Man', 'One Punch Man', 'Mob Psycho 100', 'Bleach Thousand-Year Blood War',
           'Solo Leveling', 'Kaiju No. 8', 'Sakamoto Days', 'Dandadan', 'Frieren', 'Vinland Saga', 'Attack on Titan']


def all_queries():
    # 2026-10-21: the popular action shows x the fight-compilation / sakuga suffixes first, then the original list, then the rest
    qs = [f'{s} {x}' for x in ['best fight', 'sakuga', 'fight scene 4K', 'best fight scenes'] for s in POPULAR + SHOWS_MORE[:16]]
    qs += [f'{s} {x}' for x in SUFFIXES + SUFFIXES_MORE for s in SHOWS]
    qs += [f'{s} {x}' for x in SUFFIXES[:2] + SUFFIXES_SAKUGA[:2] for s in SHOWS_MORE]
    qs += [f'{s} {x}' for x in SUFFIXES_SAKUGA for s in SHOWS]
    qs += [f'{s} {x}' for x in SUFFIXES[2:] + SUFFIXES_MORE + SUFFIXES_SAKUGA[2:] for s in SHOWS_MORE]
    seen = set()
    return [q for q in qs if not (q in seen or seen.add(q))]


def pick_queries(shard, n_shards, n, state):
    """n queries per shard. With SOUND_VID_SHARD=1 (the nightly CI, 2026-10-21) every shard gets the SAME n * n_shards queries and
    takes only its own videos (crc32(video id) % n_shards, hit_scan HIT_SCAN_VID_SHARD): all results of every query are covered,
    no video is downloaded twice."""
    done = set(state.get('queries') or {})
    todo = [q for q in all_queries() if q not in done]
    if len(todo) < n * n_shards:
        # every query searched once: rotate through all of them again by day (never an empty night)
        aq = all_queries()
        off = (_dt.datetime.now(_dt.timezone.utc).toordinal() * n * n_shards) % len(aq)
        todo += [q for q in aq[off:] + aq[:off] if q not in todo]
    if os.environ.get('SOUND_VID_SHARD') == '1':
        return todo[:n * n_shards]
    return todo[shard::n_shards][:n]


# ------------------------------------------------------------------ mine (one shard)

def mine(work, shard=0, n_shards=1, budget_min=240.0, max_sources=12, n_queries=6, judge=True):
    """TIME-BASED (2026-10-24: the nightly used 45 min of its budget): batches of MINE_BATCH_Q queries -> fetch -> scan -> cut ->
    prune -> judge, repeated until the budget is near (no new batch with < MINE_FETCH_RESERVE_MIN left), the query list
    (n_queries) or the source cap (max_sources) is used up, or a bot check. Sources are pruned after every batch (disk stays small);
    the shard artifact is written after every batch, so a hard stop still leaves the judged clips."""
    import hit_scan as HS
    t0 = time.time()
    os.makedirs(work, exist_ok=True)
    state = read_json(FETCH_STATE, {'seen': {}, 'queries': {}})
    st = read_json(os.path.join(work, 'fetch.json'), {'seen': {}, 'queries': {}})
    st['seen'].update(state.get('seen') or {})
    st['queries'].update(state.get('queries') or {})
    write_json(os.path.join(work, 'fetch.json'), st)
    qs = pick_queries(shard, n_shards, n_queries, state)
    if os.environ.get('SOUND_VID_SHARD') == '1':
        os.environ['HIT_SCAN_VID_SHARD'] = f'{shard}/{n_shards}'
    log(f'shard {shard}/{n_shards}: {len(qs)} queries, <= {max_sources} sources, {budget_min:.0f} min: {qs[:12]} ...')
    rep = {'shard': shard, 'queries': qs, 'started': today(), 'batches': 0, 'sources': 0, 'minis': 0}
    os.environ.update({k: os.environ.get(k) or v for k, v in DENSE.items()})
    out = os.path.join(work, 'out')
    os.makedirs(os.path.join(out, 'mini'), exist_ok=True)
    os.makedirs(os.path.join(out, 'strips'), exist_ok=True)
    batch_q = int(os.environ.get('MINE_BATCH_Q', '4'))
    fetch_reserve = float(os.environ.get('MINE_FETCH_RESERVE_MIN', str(min(30.0, budget_min * 0.2))))

    def left():
        return budget_min - (time.time() - t0) / 60

    # sources rejected earlier for burned-in text only: retried first-class (a few per batch) now that text is judged per clip
    retry = HS.text_retry_ids(st['seen'])
    if os.environ.get('HIT_SCAN_VID_SHARD'):
        k, n = (int(x) for x in os.environ['HIT_SCAN_VID_SHARD'].split('/'))
        retry = [v for v in retry if __import__('zlib').crc32(v.encode()) % n == k]
    retry_n = int(os.environ.get('MINE_RETRY_PER_BATCH', '3'))
    rep['text_retry_pending'] = len(retry)
    got, qi, ri = 0, 0, 0
    while (qi < len(qs) or ri < len(retry)) and got < max_sources and left() > fetch_reserve:
        bq = qs[qi:qi + batch_q]
        br = retry[ri:ri + retry_n]
        qi, ri = qi + len(bq), ri + len(br)
        st2 = HS.fetch(work, bq, per_query=int(os.environ.get('REFRESH_PER_QUERY', '2')), max_sources=max_sources - got,
                       budget_min=max(3.0, left() - fetch_reserve), retry_ids=br)
        rep['text_retried'] = ri
        got += int(st2.get('got') or 0)
        rep['batches'] += 1
        rep['queries_searched'] = sum(1 for q in qs[:qi] if q in st2.get('queries', {}))
        rep['sources'] = got
        HS.scan(work, [os.path.join(work, 'src', '*__all.mkv')], max_per_source=int(os.environ.get('REFRESH_PER_SOURCE', '14')),
                budget_min=max(3.0, left() - fetch_reserve / 2))
        rep['minis'] = HS.cut(work)
        HS.prune_sources(work)
        if judge:
            _judge(work, out, rep, t0, budget_min)
        _write_shard(work, out, rep, t0)
        log(f"batch {rep['batches']}: {got} sources, {rep['minis']} minis, {rep.get('approved', 0)} approved, {left():.0f} min left")
        if st2.get('_blocked'):
            rep['stopped'] = 'bot-block (' + str(st2['_blocked']) + ')'
            log('bot check: no more fetching tonight')
            break
    return _write_shard(work, out, rep, t0)


def _judge(work, out, rep, t0, budget_min):
    import hit_vision as HV
    import hit_import as HI
    import clip_prefilter as CP
    import edit_fx as FX
    side = read_json(os.path.join(work, 'mini.json'), {})
    verdicts = read_json(os.path.join(out, 'verdicts.json'), {})
    approved = read_json(os.path.join(out, 'approved.json'), {})
    for name, m in side.items():
        if name in verdicts:
            continue
        if (time.time() - t0) / 60 > budget_min:
            log('budget used up before every mini was judged')
            break
        p = os.path.join(work, 'mini', name + '.mp4')
        if not os.path.isfile(p):
            continue
        # 2026-10-24 PER-CLIP burned-in text (the source is no longer rejected for it): the <= 3 s mini IS the hit window;
        # text anywhere in it = reject, before the paid vision call (hit_import.safety checks it again)
        try:
            tb = FX.text_bands(p)
        except Exception:  # noqa: BLE001
            tb = (0.0, 0.0)
        if max(float(tb[0] or 0), float(tb[1] or 0)) > TEXT_MAX:
            verdicts[name] = {'text': [round(float(tb[0]), 3), round(float(tb[1]), 3)]}
            rep['text_rejects'] = rep.get('text_rejects', 0) + 1
            write_json(os.path.join(out, 'verdicts.json'), verdicts)
            log(f'{name}: rejected (burned-in text {tb[0]:.2f} top / {tb[1]:.2f} bottom)')
            continue
        if REMAKE_GATE:
            # the beat remake must be able to show this hit at all (else the pin is dead on arrival in the planner)
            try:
                import sound_edit as SE
                rok, rwhy, rev = SE.remake_check(p, float(m['hit_t']), cache_dir=os.path.join(work, 'evcache'))
            except Exception as e:  # noqa: BLE001
                rok, rwhy, rev = True, [f'check error {str(e)[:100]}'], None
            if not rok:
                verdicts[name] = {'remake': {'why': rwhy, 'pre_room': (rev or {}).get('pre_room'),
                                             'post_room': (rev or {}).get('post_room'), 'xs': (rev or {}).get('xs', [])[:3]}}
                rep['remake_rejects'] = rep.get('remake_rejects', 0) + 1
                write_json(os.path.join(out, 'verdicts.json'), verdicts)
                log(f"{name}: rejected (beat remake: {', '.join(rwhy)})")
                continue
        if PREFILTER:
            # 2026-10-21 cheap CPU pre-filter (impact at the hit, action size / centre / spread) BEFORE the paid vision call
            try:
                pok, pwhy, pf = CP.sound_check(p, float(m['hit_t']))
            except Exception as e:  # noqa: BLE001
                pok, pwhy, pf = True, [], {'error': str(e)[:120]}
            if not pok:
                verdicts[name] = {'prefilter': {'why': pwhy, 'feats': pf}}
                rep['prefilter_rejects'] = rep.get('prefilter_rejects', 0) + 1
                write_json(os.path.join(out, 'verdicts.json'), verdicts)
                log(f"{name}: rejected (prefilter: {'; '.join(pwhy)[:90]})")
                continue
        try:
            v = HV.judge(p, float(m['hit_t']), m.get('tag'), tmp=os.path.join(work, 'vtmp'))
        except Exception as e:  # noqa: BLE001
            v = {'ok': False, 'why': f'vision error {str(e)[:120]}', 'votes': []}
        rec = {'vision': {k: v.get(k) for k in ('ok', 'word', 'why', 'votes', 'score', 'strict')}}
        if v.get('ok'):
            try:
                ok, info = HI.safety(p)
            except Exception as e:  # noqa: BLE001
                ok, info = False, {'why': f'safety error {str(e)[:120]}'}
            rec['safety'] = info
            if ok:
                shutil.copy2(p, os.path.join(out, 'mini', name + '.mp4'))
                if v.get('strip') and os.path.isfile(v['strip']):
                    shutil.copy2(v['strip'], os.path.join(out, 'strips', name + '.jpg'))
                approved[name] = dict(m, word=v['word'], safety=info, vision_score=v.get('score'), vision_strict=v.get('strict'),
                                      vision=[{'model': x['model'], 'conf': (x.get('answer') or {}).get('confidence'),
                                               'note': (x.get('answer') or {}).get('note')} for x in v.get('votes') or []])
        verdicts[name] = rec
        write_json(os.path.join(out, 'verdicts.json'), verdicts)
        write_json(os.path.join(out, 'approved.json'), approved)
        log(f"{name}: {'APPROVED ' + str(v.get('word')) if name in approved else 'rejected'} ({str(v.get('why'))[:90]})")
    rep.update(judged=len(verdicts), approved=len(approved))


def _write_shard(work, out, rep, t0):
    fst = read_json(os.path.join(work, 'fetch.json'), {'seen': {}, 'queries': {}})
    write_json(os.path.join(out, 'fetch.json'), fst)
    rep.update(minutes=round((time.time() - t0) / 60, 1),
               scan={sid: s.get('stats') for sid, s in read_json(os.path.join(work, 'scan.json'), {'sources': {}})['sources'].items()})
    write_json(os.path.join(out, 'report.json'), rep)
    return rep


# ------------------------------------------------------------------ merge (grow-only import)

def merge(pool, shards, new_out):
    os.environ['EDIT_POOL_DIR'] = os.path.abspath(pool)
    import vibe_pool as VP
    import pool_visible as PV
    led, bl = VP.load_ledger(), VP.load_blocklist()
    allow = read_json(ALLOW_P, {'version': 1, 'clips': {}})
    hb = read_json(HIT_BLOCK_P, {'clips': []}).get('clips') or []
    hblock = set(hb.keys() if isinstance(hb, dict) else hb)
    mans = {v: VP.load_manifest('sound', v, root=pool) for v in ('impact', 'crisp')}
    live = [it for m in mans.values() for it in m['items']]
    fetch_state = read_json(FETCH_STATE, {'seen': {}, 'queries': {}})
    new, res = [], {}
    for sd in shards:
        fs = read_json(os.path.join(sd, 'fetch.json'), None)
        if fs:
            fetch_state['seen'].update(fs.get('seen') or {})
            fetch_state['queries'].update(fs.get('queries') or {})
        for name, m in (read_json(os.path.join(sd, 'approved.json'), {}) or {}).items():
            p = os.path.join(sd, 'mini', name + '.mp4')
            if not os.path.isfile(p):
                res[name] = 'missing'
                continue
            tag = m.get('tag')
            vibe = 'crisp' if tag in CRISP_TAGS else 'impact'
            man = mans[vibe]
            sid, st = m['source_id'], float(m['offset'])
            dur = float((m.get('safety') or {}).get('dur') or 3.0)
            en = st + dur
            iid = f'c_{sid}_{int(round(st * 1000))}'
            if iid in {it['id'] for it in live} or iid in hblock:
                res[name] = 'dup_id / hit-blocked'
                continue
            if VP.moment_taken(sid, st, en, led, live):
                res[name] = 'moment_used'
                continue
            if VP.blocked(sid, st, en, bl):
                res[name] = 'blocked'
                continue
            sig = PV.signature(p)
            item = {'id': iid, 'vis': {'sig': sig}}
            if PV.dup_of(item, live):
                res[name] = 'same_picture'
                continue
            vd = VP.vibe_dir('sound', vibe, pool)
            os.makedirs(os.path.join(vd, 'clips'), exist_ok=True)
            rel = f'clips/{iid}.mp4'
            shutil.copy2(p, os.path.join(vd, rel))
            sf = m.get('safety') or {}
            row = {
                'id': iid, 'file': rel, 'source_id': sid, 'start': round(st, 3), 'end': round(en, 3), 'moment': f'{sid}#{int(st)}',
                'source_url': f'https://youtu.be/{sid}' if len(sid) == 11 else None, 'source_title': m.get('title'),
                'source_channel': m.get('channel'), 'views': m.get('views'), 'duration': round(dur, 3), 'has_audio': True,
                'has_text': False, 'text_band': sf.get('text_band'),
                'hit_scan': {'tag': tag, 'score': m.get('score')},
                'safety': {'gate': 'pass', 'checked': 'refresh: NudeNet every 0.125 s + SigLIP unsafe/safe + text bands + vision model',
                           'unsafe': sf.get('unsafe')},
                'auto_hit': {'action': tag, 'word': m.get('word'), 't': m['hit_t'], 'vision': m.get('vision'), 'added': today(),
                             'score': m.get('vision_score'), 'strict': m.get('vision_strict')},
                'reviewed': {'by': 'auto (vision model + safety gates)', 'at': today()},
                'sfx': [{'t': float(m['hit_t'])}], 'query': 'monthly refresh (auto-approved)',
                'status': 'fresh', 'added': today(), 'used_at': None, 'used_in': None,
            }
            man['items'].append(row)
            live.append(dict(row, vis={'sig': sig}))
            allow.setdefault('clips', {})[iid] = [{'t': round(float(m['hit_t']), 3), 'action': tag, 'word': m.get('word'),
                                                    'note': f"auto (vision model) {today()} ({str(m.get('title') or '')[:50]})"}]
            new.append({'id': iid, 'vibe': vibe, 'mini': name, 'strip': os.path.join(sd, 'strips', name + '.jpg'),
                        'score': m.get('vision_score')})
            res[name] = iid
    for v, man in mans.items():
        VP.save_manifest('sound', v, man, root=pool)
    VP.write_index(pool)
    write_json(ALLOW_P, allow)
    write_json(FETCH_STATE, fetch_state)
    write_json(new_out, new)
    log(f'merged {len(new)} new hit clips: {res}')
    return {'new': len(new), 'results': res}


def _new(new_p):
    return read_json(new_p, [])


def _clip_path(pool, n):
    return os.path.join(pool, 'sound', n['vibe'], 'clips', n['id'] + '.mp4')


# ------------------------------------------------------------------ sharpen

def sharpen(pool, new_p, budget_min=240.0):
    news = _new(new_p)
    todo = [n for n in news if not os.path.isfile(_clip_path(pool, n) + '.up.json')]
    if not todo:
        return {'sharpened': 0}
    # 2026-10-24: Kaggle is NOT used inline any more (the KAGGLE_API_TOKEN secret is now set): the near misses are sharpened by
    # `sharpen-staged` (Mon-Thu, shared weekly ledger); a Kaggle session inside the 20 min swap budget timed out with the kernel still
    # running. SOUND_SHARPEN_KAGGLE_INLINE=1 restores the old inline call.
    if os.environ.get('SOUND_SHARPEN_KAGGLE_INLINE') == '1' and os.environ.get('KAGGLE_API_TOKEN') and os.environ.get('KAGGLE_USERNAME'):
        try:                     # 2026-10-24: bounded by the same budget, so the staging after it always finishes in time
            r = subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'edit-upscale-kaggle.py'), 'run', '--pool', pool],
                               timeout=max(60, int(budget_min * 60)))
        except subprocess.TimeoutExpired:
            r = subprocess.CompletedProcess([], 1)
            log('kaggle sharpening over its budget: the rest stay 1080p')
            return {'sharpened': 0, 'via': 'kaggle timeout'}
        if r.returncode == 0:
            todo = [n for n in news if not os.path.isfile(_clip_path(pool, n) + '.up.json')]
            if not todo:
                return {'sharpened': len(news), 'via': 'kaggle'}
        log('kaggle sharpening unavailable / partial: CPU for the rest')
    t0, done = time.time(), 0
    for n in todo:
        if (time.time() - t0) / 60 > budget_min:
            log('CPU sharpening budget used up: the rest stay 1080p')
            break
        # 2026-10-24: bounded by what is left (one clip took 38 min of a 20 min budget); the result is written atomically
        try:
            r = subprocess.run([sys.executable, os.path.join(ROOT, 'core', 'edit', 'upscale_pool.py'), '--in', _clip_path(pool, n),
                                '--device', 'cpu', '--tile', os.environ.get('UPSCALE_TILE', '360')],
                               timeout=max(60, budget_min * 60 - (time.time() - t0)))
        except subprocess.TimeoutExpired:
            log(f"{n['id']}: CPU sharpening over the budget: the rest stay 1080p")
            break
        done += int(r.returncode == 0 and os.path.isfile(_clip_path(pool, n) + '.up.json'))
        log(f"{n['id']}: sharpened {done}/{len(todo)} ({(time.time() - t0) / 60:.0f} min)")
    return {'sharpened': done, 'of': len(todo), 'via': 'cpu', 'minutes': round((time.time() - t0) / 60, 1)}


# ------------------------------------------------------------------ quality metrics / baseline / check

def metrics(path, hit_t):
    """sharpness (Laplacian variance at 1080 lines, median of the frames hit-0.3 .. hit+0.1) and brightness (mean grey)."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    sh, lu, hi = [], [], []
    for dt in (-0.3, -0.2, -0.1, 0.0, 0.1):
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round((hit_t + dt) * fps))))
        ok, f = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        h0 = g.shape[0]
        if g.shape[0] != 1080:
            g = cv2.resize(g, (int(round(g.shape[1] * 1080 / g.shape[0])), 1080), interpolation=cv2.INTER_AREA)
        sh.append(float(cv2.Laplacian(g, cv2.CV_64F).var()))
        lu.append(float(g.mean()))
        hi.append(float(np.percentile(g, 95)))
    cap.release()
    if not sh:
        return None
    return {'sharp': round(float(np.median(sh)), 1), 'luma': round(float(np.median(lu)), 1), 'p95': round(float(np.median(hi)), 1),
            'h': int(h0)}


def baseline(pool, out=None):
    import numpy as np
    allow = read_json(ALLOW_P, {'clips': {}})['clips']
    rows = []
    for v in ('impact', 'crisp'):
        man = read_json(os.path.join(pool, 'sound', v, 'clips.json'), {'items': []})
        for it in man['items']:
            if it['id'] not in allow or it.get('auto_hit'):
                continue
            p = os.path.join(pool, 'sound', v, it['file'])
            if not os.path.isfile(p):
                continue
            mt = metrics(p, float(allow[it['id']][0]['t']))
            if mt:
                rows.append(mt)
    if len(rows) < 20:
        raise SystemExit(f'baseline: only {len(rows)} pinned clips with files (need >= 20): pull the pool first')
    q = lambda k, p: round(float(np.percentile([r[k] for r in rows], p)), 1)
    b = {'version': 1, 'built': today(), 'source': 'the eye-approved pins of the sound_11b pool (owner-approved 2026-10-17)',
         'n': len(rows), 'sharp': {'p10': q('sharp', 10), 'p25': q('sharp', 25), 'p50': q('sharp', 50)},
         'luma': {'p10': q('luma', 10), 'p25': q('luma', 25), 'p50': q('luma', 50)}}
    write_json(out or BASELINE_P, b)
    return b


# 2026-10-24 NEAR MISSES (sound yield round 2): the hard p10 cut dropped 10 of 13 clips on 10-24 (7 soft, 3 dark), most of them a
# little under the bar. Measured on those clips (the pool's Real-ESRGAN realesr-animevideov3 x2, metric at 1080 lines): sharpening
# multiplies the hit-window sharpness by ~1.9-3.7 (5.7 -> 11.1, 7.3 -> 13.6, ~15 -> 41-55). So:
#   (a 720p clip gains only x1.1-1.4: 6.4 -> 7.1, 3.5 -> 5.0, so a soft 720p clip is dropped as before)
#   soft  sharp >= SOFT_FIX_MIN (5.5 = p10 / 1.9), >= 1000 lines, not sharpened yet -> KEPT, flagged quality.needs_sharpen (sharpened on Kaggle
#         Mon-Thu by `sharpen-staged`; until then it waits staged); below that, or still soft after sharpening -> dropped
#   dark  luma >= DARK_FIX_MIN (26) with real highlights (p95 >= 70) -> a gamma lift (ffmpeg eq, <= 1.6) right here on the CPU to
#         the baseline p10 + 4, re-measured, flagged quality.graded; darker / flat -> dropped
SOFT_FIX_MIN = float(os.environ.get('SOUND_SOFT_FIX_MIN', '5.5'))
DARK_FIX_MIN = float(os.environ.get('SOUND_DARK_FIX_MIN', '26'))
DARK_P95_MIN = float(os.environ.get('SOUND_DARK_P95_MIN', '70'))


def grade(path, gamma):
    """in-place gamma lift (video re-encoded crf 16, audio copied); True when the clip was replaced"""
    tmp = path + '.grade.mp4'
    r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-nostdin', '-i', path, '-vf', f'eq=gamma={gamma:.3f}', '-c:v', 'libx264', '-crf', '16',
                        '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-threads', '2', '-c:a', 'copy', '-movflags', '+faststart', tmp],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(tmp):
        return False
    os.replace(tmp, path)
    return True


def classify(path, t, b):
    """-> (verdict 'pass' | 'needs_sharpen' | 'drop', why, metrics, quality flags); grades a fixable dark clip in place"""
    import math
    mt = metrics(path, t) if os.path.isfile(path) else None
    if not mt:
        return 'drop', 'unreadable', mt, {}
    q = {}
    sharpened = os.path.isfile(path + '.up.json')
    if mt['luma'] < b['luma']['p10']:
        if mt['luma'] < DARK_FIX_MIN or mt.get('p95', 255) < DARK_P95_MIN:
            return 'drop', f"dark ({mt['luma']} < fix floor {DARK_FIX_MIN} or flat, p95 {mt.get('p95')})", mt, q
        target = b['luma']['p10'] + 4
        gamma = min(1.6, max(1.05, math.log(max(mt['luma'], 1) / 255) / math.log(target / 255)))
        if grade(path, gamma):
            m2 = metrics(path, t) or mt
            q['graded'] = {'gamma': round(gamma, 3), 'luma_before': mt['luma'], 'luma_after': m2['luma']}
            mt = dict(m2, luma_before=mt['luma'])
        if mt['luma'] < b['luma']['p10']:
            return 'drop', f"dark after the grade ({mt['luma']} < baseline p10 {b['luma']['p10']})", mt, q
    if mt['sharp'] < b['sharp']['p10']:
        if sharpened or mt['sharp'] < SOFT_FIX_MIN or mt.get('h', 1080) < 1000:
            tail = ' after sharpening' if sharpened else (f', {mt.get("h")}p: sharpening barely helps' if mt.get('h', 1080) < 1000
                                                          else f', < fix floor {SOFT_FIX_MIN}')
            return 'drop', f"soft ({mt['sharp']} < baseline p10 {b['sharp']['p10']}{tail})", mt, q
        q['needs_sharpen'] = True
        return 'needs_sharpen', f"soft ({mt['sharp']} < p10 {b['sharp']['p10']}): Kaggle sharpening fixes it", mt, q
    return 'pass', None, mt, q


def check(pool, new_p, report_p=None):
    import numpy as np
    b = read_json(BASELINE_P, None)
    if not b:
        raise SystemExit(f'no baseline at {BASELINE_P}: run `sound_refresh.py baseline --pool P` on the approved pool')
    os.environ['EDIT_POOL_DIR'] = os.path.abspath(pool)
    import vibe_pool as VP
    allow = read_json(ALLOW_P, {'clips': {}})
    news, keep, drop = _new(new_p), [], []
    quality = {}
    for n in news:
        p = _clip_path(pool, n)
        t = float(allow['clips'].get(n['id'], [{'t': 1.2}])[0]['t'])
        verdict, why, mt, q = classify(p, t, b)
        n['metrics'] = mt
        n['quality'] = dict(q, verdict=verdict, metrics=mt)
        quality[n['id']] = n['quality']
        (drop if verdict == 'drop' else keep).append(dict(n, why=why))
    for v in ('impact', 'crisp'):                    # the quality flags go into the rows (staged with them, shown on the sheet)
        man = VP.load_manifest('sound', v, root=pool)
        hit = False
        for it in man['items']:
            if it['id'] in quality:
                it['quality'] = quality[it['id']]
                hit = True
        if hit:
            VP.save_manifest('sound', v, man, root=pool)
    for n in drop:                                   # undo the import of this clip only (grow-only: old clips untouched)
        man = VP.load_manifest('sound', n['vibe'], root=pool)
        man['items'] = [it for it in man['items'] if it['id'] != n['id']]
        VP.save_manifest('sound', n['vibe'], man, root=pool)
        allow['clips'].pop(n['id'], None)
        for f in (_clip_path(pool, n), _clip_path(pool, n) + '.up.json'):
            if os.path.isfile(f):
                os.remove(f)
    write_json(ALLOW_P, allow)
    VP.write_index(pool)
    ok = [n for n in keep if n['quality']['verdict'] == 'pass']
    med = lambda k: round(float(np.median([n['metrics'][k] for n in ok])), 1) if ok else None
    rep = {'baseline': b, 'new_checked': len(news), 'kept': len(keep),
           'needs_sharpen': [n['id'] for n in keep if n['quality'].get('needs_sharpen')],
           'graded': [n['id'] for n in keep if n['quality'].get('graded')],
           'dropped': [{'id': n['id'], 'why': n['why']} for n in drop],
           'batch_median': {'sharp': med('sharp'), 'luma': med('luma')},
           'batch_vs_baseline_p50': {k: (round(med(k) / b[k]['p50'], 2) if ok else None) for k in ('sharp', 'luma')},
           'pins_total': len(allow['clips'])}
    keep.sort(key=lambda n: -(n.get('score') or 0))  # review order: the vision score, best first
    write_json(new_p, keep)
    if report_p:
        write_json(report_p, rep)
    return rep


# ------------------------------------------------------------------ contact sheet for the owner

def sheet(pool, new_p, out):
    import cv2
    import numpy as np
    import hit_vision as HV
    allow = read_json(ALLOW_P, {'clips': {}})['clips']
    rows = []
    # 2026-10-24: best vision score first (fast review); the label carries the score and the quality flags
    for n in sorted(_new(new_p), key=lambda n: -(n.get('score') or 0)):
        p = _clip_path(pool, n)
        if not os.path.isfile(p):
            continue
        t = float((allow.get(n['id']) or [{'t': 1.2}])[0]['t'])
        q = n.get('quality') or {}
        flags = ' '.join(x for x in ('NEEDS SHARPEN' if q.get('needs_sharpen') else '', 'graded' if q.get('graded') else '') if x)
        tmp = os.path.join(os.path.dirname(out), '_sheet_tmp')
        os.makedirs(tmp, exist_ok=True)
        st, _ = HV.strip(p, t, os.path.join(tmp, n['id'] + '.jpg'), cols=18, cw=160)
        if not st:
            continue
        img = cv2.imread(st)
        word = (allow.get(n['id']) or [{}])[0].get('word') or ''
        lab = np.zeros((26, img.shape[1], 3), np.uint8)
        cv2.putText(lab, f"{n['id']}  {word}  score {n.get('score') or '-'}  {flags}", (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        rows.append(np.vstack([lab, img]))
    if not rows:
        return None
    w = max(r.shape[1] for r in rows)
    rows = [np.pad(r, ((0, 0), (0, w - r.shape[1]), (0, 0))) for r in rows]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    for k in range(0, len(rows), 12):                  # 12 clips per page
        cv2.imwrite(out.replace('.jpg', f'-{k // 12:02d}.jpg'), np.vstack(rows[k:k + 12]), [cv2.IMWRITE_JPEG_QUALITY, 82])
    return len(rows)


# ------------------------------------------------------------------ staging (2026-10-20: nightly growth, reviewed before import)
# The nightly job no longer pushes to the live pool: the new clips (merged + sharpened + baseline-checked on a CI copy of the pool)
# go to the release `edit-pools-staging` as sound-cand-<date>-n<N>.tar. Claude reviews the contact sheets at session start
# (`pull-staged`), then the workflow's import mode (`apply-staged`, on CI where the full pool is) adds them minus the rejects.

STAGE_TAG = os.environ.get('EDIT_POOLS_STAGING_TAG', 'edit-pools-staging')


def stage(pool, new_p, out_dir, sheets=()):
    import tarfile
    allow = read_json(ALLOW_P, {'clips': {}})['clips']
    news = _new(new_p)
    rows, al = {}, {}
    for v in ('impact', 'crisp'):
        man = read_json(os.path.join(pool, 'sound', v, 'clips.json'), {'items': []})
        ids = {n['id'] for n in news if n['vibe'] == v}
        for it in man['items']:
            if it['id'] in ids and os.path.isfile(_clip_path(pool, {'id': it['id'], 'vibe': v})):
                rows[it['id']] = {'vibe': v, 'row': it}
                al[it['id']] = allow.get(it['id']) or []
    if not rows:
        log('stage: nothing new to stage')
        return None
    order = [n['id'] for n in news]
    rows = {k: rows[k] for k in sorted(rows, key=lambda k: order.index(k) if k in order else len(order))}
    os.makedirs(out_dir, exist_ok=True)
    name = f"sound-cand-{today().replace('-', '')}-n{len(rows)}.tar"
    tp = os.path.join(out_dir, name)
    meta = os.path.join(out_dir, '_stage')
    os.makedirs(meta, exist_ok=True)
    write_json(os.path.join(meta, 'rows.json'), rows)
    write_json(os.path.join(meta, 'allow.json'), al)
    with tarfile.open(tp, 'w') as t:
        t.add(os.path.join(meta, 'rows.json'), 'rows.json')
        t.add(os.path.join(meta, 'allow.json'), 'allow.json')
        for cid, r in rows.items():
            t.add(_clip_path(pool, {'id': cid, 'vibe': r['vibe']}), f"clips/{r['vibe']}/{cid}.mp4")
        for f in sheets:
            for g in sorted(glob.glob(f)):
                t.add(g, 'sheets/' + os.path.basename(g))
    log(f'staged {len(rows)} new hit clips -> {tp}')
    return {'tar': tp, 'clips': len(rows), 'ids': sorted(rows)}


def apply_staged(pool, tars, reject=()):
    """(CI, on the pulled FULL live pool) add the staged clips minus the rejects: media + manifest rows + allowlist pins."""
    import tarfile
    os.environ['EDIT_POOL_DIR'] = os.path.abspath(pool)
    import vibe_pool as VP
    reject = {x.strip() for x in reject if x and x.strip()}
    allow = read_json(ALLOW_P, {'version': 1, 'clips': {}})
    mans = {v: VP.load_manifest('sound', v, root=pool) for v in ('impact', 'crisp')}
    have = {it['id'] for m in mans.values() for it in m['items']}
    added, skipped = [], []
    for tp in tars:
        tmp = tp + '.x'
        with tarfile.open(tp) as t:
            t.extractall(tmp)
        rows = read_json(os.path.join(tmp, 'rows.json'), {})
        al = read_json(os.path.join(tmp, 'allow.json'), {})
        for cid, r in rows.items():
            if cid in reject or cid in have:
                skipped.append(cid)
                continue
            src = os.path.join(tmp, 'clips', r['vibe'], cid + '.mp4')
            if not os.path.isfile(src):
                skipped.append(cid)
                continue
            vd = VP.vibe_dir('sound', r['vibe'], pool)
            os.makedirs(os.path.join(vd, 'clips'), exist_ok=True)
            shutil.copy2(src, os.path.join(vd, 'clips', cid + '.mp4'))
            row = dict(r['row'], reviewed={'by': 'auto gates + Claude review of the staged contact sheet', 'at': today()})
            mans[r['vibe']]['items'].append(row)
            if al.get(cid):
                allow.setdefault('clips', {})[cid] = al[cid]
            have.add(cid)
            added.append(cid)
        shutil.rmtree(tmp, ignore_errors=True)
    for v, man in mans.items():
        VP.save_manifest('sound', v, man, root=pool)
    VP.write_index(pool)
    write_json(ALLOW_P, allow)
    log(f'apply-staged: {len(added)} added, {len(skipped)} skipped (rejected / already in / missing)')
    return {'added': len(added), 'skipped': skipped, 'pins': len(allow.get('clips') or {})}


def pull_staged(dest):
    """(PC / session start) download the staged sound tars and extract their contact sheets + rows for the review"""
    import tarfile
    os.makedirs(dest, exist_ok=True)
    out = subprocess.run(['gh', 'release', 'view', STAGE_TAG, '--json', 'assets', '-q', '.assets[].name'],
                         capture_output=True, text=True, timeout=120)
    names = [n for n in out.stdout.split() if n.startswith('sound-cand-')]
    got = []
    for n in names:
        tp = os.path.join(dest, n)
        if not os.path.isfile(tp):
            subprocess.run(['gh', 'release', 'download', STAGE_TAG, '--pattern', n, '--dir', dest], check=True, timeout=1800)
        x = os.path.join(dest, n[:-4])
        with tarfile.open(tp) as t:
            t.extractall(x, members=[m for m in t.getmembers() if m.name.startswith('sheets/') or m.name.endswith('.json')])
        got.append({'tar': n, 'dir': x, 'clips': len(read_json(os.path.join(x, 'rows.json'), {}))})
    try:                                              # zyo-clipscore: a ranked list for the review (no-op while off / unscored)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import clipscore
        clipscore.review_hook('sound', tar_names=names, out_txt=os.path.join(dest, 'clipscore-review.txt'))
    except ImportError:
        pass
    return got


# ------------------------------------------------------------------ Kaggle sharpening of the staged near misses (2026-10-24)
# check() keeps clips a little too soft as quality.needs_sharpen (CPU sharpening is ~38 min per clip on the runner). This mode
# (.github/workflows/sound-sharpen-kaggle.yml, Monday-Thursday only) takes them from the staged tars on edit-pools-staging, sharpens
# them on a Kaggle GPU (scripts/edit-upscale-kaggle.py, kernel <user>/edit-upscale-refresh; never a cwr-* / apq-* kernel),
# re-measures them against the baseline and re-uploads each tar (sharp ones flagged quality.sharpened, still-soft ones removed).
# GPU time is booked in the SHARED weekly ledger clipscore-ledger.json (cap ZYO_WEEK_MIN, 1200 min = 20 h, with zyo-clipscore).

LEDGER_ASSET = 'clipscore-ledger.json'
WEEK_MIN = float(os.environ.get('ZYO_WEEK_MIN', '1200'))
SHARPEN_SESSION_MIN = float(os.environ.get('SOUND_SHARPEN_SESSION_MIN', '40'))


def kaggle_day_ok(now=None):
    """Monday-Thursday on both UTC and US Eastern (UTC-5 is the safe side of the Sunday / Friday edges)"""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return now.weekday() <= 3 and (now - _dt.timedelta(hours=5)).weekday() <= 3


def _week(now=None):
    y, w, _ = (now or _dt.datetime.now(_dt.timezone.utc)).isocalendar()
    return f'{y}-W{w:02d}'


def _ledger(tmp):
    os.makedirs(tmp, exist_ok=True)
    r = subprocess.run(['gh', 'release', 'download', STAGE_TAG, '--pattern', LEDGER_ASSET, '--dir', tmp, '--clobber'],
                       capture_output=True, text=True, timeout=300)
    return read_json(os.path.join(tmp, LEDGER_ASSET), {}) if r.returncode == 0 else {}


def _book(tmp, minutes, stats):
    lg = _ledger(tmp)
    lg.setdefault(_week(), []).append({'at': _dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds'), 'kind': 'sound-sharpen',
                                       'minutes': round(minutes, 1), 'gpu_hours': round(minutes / 60, 3), 'stats': stats})
    lg['_week_totals'] = {k: round(sum(x.get('minutes', 0) for x in v) / 60, 2) for k, v in lg.items() if k.startswith('20')}
    p = os.path.join(tmp, LEDGER_ASSET)
    write_json(p, lg)
    subprocess.run(['gh', 'release', 'upload', STAGE_TAG, p, '--clobber'], capture_output=True, timeout=300)


def sharpen_staged(work, dry=False):
    import tarfile
    os.makedirs(work, exist_ok=True)
    if not dry:
        if not kaggle_day_ok():
            return {'skipped': 'Friday-Sunday: Kaggle belongs to the owner\'s other project; near misses wait staged'}
        if not (os.environ.get('KAGGLE_USERNAME') and (os.environ.get('KAGGLE_API_TOKEN') or os.path.isfile(
                os.path.expanduser('~/.kaggle/access_token')))):
            return {'skipped': 'no Kaggle credentials (KAGGLE_API_TOKEN secret / KAGGLE_USERNAME variable)'}
        used = sum(x.get('minutes', 0) for x in _ledger(os.path.join(work, '_ledger')).get(_week(), []))
        if WEEK_MIN - used < SHARPEN_SESSION_MIN + 10:
            return {'skipped': f'weekly Kaggle cap: {used:.0f} of {WEEK_MIN:.0f} min used'}
    out = subprocess.run(['gh', 'release', 'view', STAGE_TAG, '--json', 'assets', '-q', '.assets[].name'], capture_output=True, text=True,
                         timeout=120)
    tars = [n for n in out.stdout.split() if n.startswith('sound-cand-') and n.endswith('.tar')]
    pool = os.path.join(work, 'pool')
    shutil.rmtree(pool, ignore_errors=True)
    todo, metas = {}, {}
    for n in tars:
        d = os.path.join(work, n[:-4])
        shutil.rmtree(d, ignore_errors=True)
        subprocess.run(['gh', 'release', 'download', STAGE_TAG, '--pattern', n, '--dir', work, '--clobber'], check=True, timeout=1800)
        with tarfile.open(os.path.join(work, n)) as t:
            t.extractall(d, members=[m for m in t.getmembers() if m.isfile() and '..' not in m.name and not m.name.startswith('/')])
        rows = read_json(os.path.join(d, 'rows.json'), {})
        want = [cid for cid, r in rows.items() if (r['row'].get('quality') or {}).get('needs_sharpen')
                and not (r['row'].get('quality') or {}).get('sharpened')]
        if not want:
            continue
        metas[n] = d
        for cid in want:
            v = rows[cid]['vibe']
            os.makedirs(os.path.join(pool, 'sound', v, 'clips'), exist_ok=True)
            shutil.copy2(os.path.join(d, 'clips', v, cid + '.mp4'), os.path.join(pool, 'sound', v, 'clips', cid + '.mp4'))
            todo[cid] = (n, v)
    if not todo:
        return {'tars': len(tars), 'near_misses': 0}
    for v in {v for _, v in todo.values()}:
        write_json(os.path.join(pool, 'sound', v, 'clips.json'),
                   {'items': [{'id': cid, 'file': f'clips/{cid}.mp4', 'status': 'fresh'} for cid, (_, vv) in todo.items() if vv == v]})
    log(f'sharpen-staged: {len(todo)} near misses in {len(metas)} tars')
    if dry:
        return {'tars': len(tars), 'near_misses': len(todo), 'dry': True}
    t0 = time.time()
    env = dict(os.environ, EDIT_UPSCALE_ALL='1', EDIT_UPSCALE_MONTH_MIN='100000', EDIT_UPSCALE_SESSION_MIN=str(int(SHARPEN_SESSION_MIN)),
               MEMORY_DIR=os.path.relpath(os.path.join(work, '_mem'), ROOT))
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'edit-upscale-kaggle.py'), 'run', '--pool', pool,
                        '--out', os.path.join(work, 'bundle')], env=env, timeout=int((SHARPEN_SESSION_MIN + 40) * 60))
    minutes = (time.time() - t0) / 60
    b = read_json(BASELINE_P, None)
    res = {'near_misses': len(todo), 'kaggle_rc': r.returncode, 'minutes': round(minutes, 1), 'sharpened': [], 'still_soft': [],
           'not_done': []}
    per_tar = {}
    for cid, (n, v) in todo.items():
        cp = os.path.join(pool, 'sound', v, 'clips', cid + '.mp4')
        d = metas[n]
        if not os.path.isfile(cp + '.up.json'):
            res['not_done'].append(cid)
            continue
        al = read_json(os.path.join(d, 'allow.json'), {})
        t = float((al.get(cid) or [{'t': 1.2}])[0]['t'])
        mt = metrics(cp, t)
        good = bool(mt and b and mt['sharp'] >= b['sharp']['p10'])
        per_tar.setdefault(n, []).append((cid, v, good, mt))
        res['sharpened' if good else 'still_soft'].append(cid)
    for n, items in per_tar.items():
        d = metas[n]
        rows = read_json(os.path.join(d, 'rows.json'), {})
        al = read_json(os.path.join(d, 'allow.json'), {})
        for cid, v, good, mt in items:
            if good:
                shutil.copy2(os.path.join(pool, 'sound', v, 'clips', cid + '.mp4'), os.path.join(d, 'clips', v, cid + '.mp4'))
                q = rows[cid]['row'].setdefault('quality', {})
                q.update(needs_sharpen=False, sharpened={'where': 'kaggle', 'at': today(), 'metrics': mt}, verdict='pass')
            else:
                rows.pop(cid, None)
                al.pop(cid, None)
                try:
                    os.remove(os.path.join(d, 'clips', v, cid + '.mp4'))
                except OSError:
                    pass
        live = subprocess.run(['gh', 'release', 'view', STAGE_TAG, '--json', 'assets', '-q', '.assets[].name'], capture_output=True,
                              text=True, timeout=120).stdout.split()
        if n not in live:
            log(f'{n}: imported / removed meanwhile: not re-uploaded')
            continue
        write_json(os.path.join(d, 'rows.json'), rows)
        write_json(os.path.join(d, 'allow.json'), al)
        tp = os.path.join(work, 'up', n)
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        with tarfile.open(tp, 'w') as t:
            for root, _, fs in os.walk(d):
                for f in sorted(fs):
                    t.add(os.path.join(root, f), os.path.relpath(os.path.join(root, f), d).replace(os.sep, '/'))
        subprocess.run(['gh', 'release', 'upload', STAGE_TAG, tp, '--clobber'], check=True, timeout=1800)
        log(f'{n}: re-staged ({sum(1 for x in items if x[2])} sharpened, {sum(1 for x in items if not x[2])} still soft -> removed)')
    if r.returncode == 0 or minutes > 2:
        _book(os.path.join(work, '_ledger'), minutes, {k: (len(v) if isinstance(v, list) else v) for k, v in res.items()})
    return res


def selftest():
    """query list + video-id shard layout (2026-10-21 yield push); the pre-filter has its own selftest (clip_prefilter.py)"""
    import zlib
    qs = all_queries()
    assert len(qs) == len(set(qs)) and len(qs) > 2000, len(qs)
    assert qs[0].startswith(POPULAR[0]) and any('sakuga' in q for q in qs[:60])
    st = {'seen': {}, 'queries': {q: 1 for q in qs[:100]}}
    old = os.environ.get('SOUND_VID_SHARD')
    os.environ['SOUND_VID_SHARD'] = '1'
    try:
        lists = [pick_queries(k, 6, 10, st) for k in range(6)]
        os.environ['SOUND_VID_SHARD'] = '0'
        sep = [pick_queries(k, 6, 10, st) for k in range(6)]
    finally:
        if old is None:
            os.environ.pop('SOUND_VID_SHARD', None)
        else:
            os.environ['SOUND_VID_SHARD'] = old
    assert all(x == lists[0] for x in lists) and len(lists[0]) == 60 and not set(lists[0]) & set(qs[:100])
    ids = [f'v{i:07d}xx' for i in range(1200)]
    parts = [{i for i in ids if zlib.crc32(i.encode()) % 6 == k} for k in range(6)]
    assert sum(map(len, parts)) == len(ids) and all(len(p) > 140 for p in parts)
    assert all(len(x) == 10 for x in sep) and len({q for x in sep for q in x}) == 60
    return {'ok': True, 'queries': len(qs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['mine', 'merge', 'sharpen', 'check', 'sheet', 'baseline', 'queries', 'stage', 'apply-staged',
                                    'pull-staged', 'sharpen-staged', 'selftest'])
    ap.add_argument('--tars', nargs='*')
    ap.add_argument('--reject', default='')
    ap.add_argument('--sheets', nargs='*', default=[])
    ap.add_argument('--work')
    ap.add_argument('--shard', default='0/1')
    ap.add_argument('--budget-min', type=float, default=240.0)
    ap.add_argument('--max-sources', type=int, default=12)
    ap.add_argument('--queries', type=int, default=6)
    ap.add_argument('--no-judge', action='store_true')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--pool', default=os.environ.get('EDIT_POOL_DIR') or os.path.join(ROOT, 'output', 'edit-pools'))
    ap.add_argument('--from', dest='shards', nargs='*')
    ap.add_argument('--new')
    ap.add_argument('--out')
    a = ap.parse_args()
    k, n = (int(x) for x in a.shard.split('/'))
    if a.cmd == 'selftest':
        r = selftest()
    elif a.cmd == 'mine':
        r = mine(a.work, k, n, a.budget_min, a.max_sources, a.queries, not a.no_judge)
    elif a.cmd == 'queries':
        r = pick_queries(k, n, a.queries, read_json(FETCH_STATE, {'seen': {}, 'queries': {}}))
    elif a.cmd == 'merge':
        r = merge(a.pool, a.shards or [], a.new)
    elif a.cmd == 'sharpen':
        r = sharpen(a.pool, a.new, a.budget_min)
    elif a.cmd == 'check':
        r = check(a.pool, a.new, a.out)
    elif a.cmd == 'sheet':
        r = {'clips': sheet(a.pool, a.new, a.out)}
    elif a.cmd == 'stage':
        r = stage(a.pool, a.new, a.out, a.sheets)
    elif a.cmd == 'apply-staged':
        r = apply_staged(a.pool, a.tars or [], a.reject.replace(' ', ',').split(','))
    elif a.cmd == 'sharpen-staged':
        r = sharpen_staged(a.work or os.path.join(ROOT, 'output', 'sound-sharpen-staged'), a.dry)
    elif a.cmd == 'pull-staged':
        r = pull_staged(a.out or os.path.join(ROOT, 'output', 'edit-pools', '_staged_sound'))
    else:
        r = baseline(a.pool, a.out)
    print(json.dumps(r, indent=1, default=str)[:20000])


if __name__ == '__main__':
    main()
