#!/usr/bin/env python3
"""Slow-mo clip pool growth (GROW-ONLY) - runs on GitHub Actions (.github/workflows/slowmo-pool-mine.yml) or on the owner PC.

    python core/pools/slowmo_grow.py mine --work W [--shard K/N] [--budget-min 200] [--batch 8] [--state S.json]
        sources: twixtor / raw clip packs of ~70 female characters + mood packs, then creditless OP / ED (low yield), searched with
        yt-dlp (cookies = YT_COOKIES, copied to a private jar per call, --js-runtimes node, 22-32 s between downloads, 1080p video
        only) -> core/edit/library.py --local (every library gate incl. the per-shot SAFETY gate: NudeNet + SigLIP, minors strict,
        burned-in text; cap EDIT_LIBRARY_CAP=6 per source) -> vibe_pool.import_shots into a STAGING copy of the live manifests
        (dedupe vs the pool, the ledger, the blocklists; slow-mo talk gate = strong signals only) -> cine_vibe.gate (the planner's
        hard rejects incl. the 2026-10-18 talking-mouth fix: mouth motion AND openness) -> rejects dropped, sources deleted.
        Output W/out/: <vibe>/clips/<id>.mp4 (+ the QA sidecars), candidates.json {vibe: [manifest items]}, sheets/*.jpg,
        state.json (queries done / sources seen, merged by the workflow and kept on the staging release).
    python core/pools/slowmo_grow.py need [--target 60]        prints go / stop: is any slow-mo vibe under target (live reviewed clean
                                                              clips + staged candidates)? the nightly schedule stops itself there
    python core/pools/slowmo_grow.py pull-staged [--dir output/edit-pools/_staged]
        downloads the not-yet-pulled candidate tars of the release `edit-pools-staging` (gh), unpacks them and writes numbered
        review sheets (DIR/review/<vibe>_NN.jpg + index.json) for the BY-EYE review
    python core/pools/slowmo_grow.py push-manifests      owner PC: live slow-mo manifests (no media) + the PC grow state -> staging release
    python core/pools/slowmo_grow.py publish --artifacts DIR --out DIR [--state S] [--dry]   CI: shard outputs -> tars -> release
    python core/pools/slowmo_grow.py import-staged [--reject ID,ID or N,N] [--dir ...] [--delete-remote]
        imports every pulled candidate into the live pool (output/edit-pools/slowmo/<vibe>) with a 'reviewed' stamp; rejected ones
        are listed with status 'rejected' (never mined again); --delete-remote removes the imported tars from the release.

Load: IDLE priority, 4 threads, one library run at a time, RAM gate (SLOWMO_GROW_MIN_GB, default 4 on the PC, 0 on CI).
"""
import argparse
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
EDIT = os.path.join(ROOT, 'core', 'edit')
sys.path.insert(0, EDIT)
LIVE = os.path.abspath(os.environ.get('EDIT_POOL_DIR') or os.path.join(ROOT, 'output', 'edit-pools'))
PY = sys.executable
VIBES = ('summer_glow', 'dreamy_bloom')
RELEASE = os.environ.get('SLOWMO_STAGING_RELEASE', 'edit-pools-staging')
STATE_ASSET = 'slowmo-grow-state.json'

CHARS = ['Marin Kitagawa', 'Mai Sakurajima', 'Zero Two', 'Kaoruko Waguri', 'Frieren', 'Fern Frieren', 'Violet Evergarden',
         'Chizuru Mizuhara', 'Miku Nakano', 'Nino Nakano', 'Rem Re Zero', 'Emilia Re Zero', 'Makima', 'Yor Forger',
         'Shinobu Kocho', 'Mitsuri Kanroji', 'Kaguya Shinomiya', 'Alya Roshidere', 'Ai Hoshino', 'Kana Arima',
         'Akane Kurokawa', 'Mikasa Ackerman', 'Asuna', 'Hinata Hyuga', 'Shikimori', 'Nagatoro', 'Chisato Nishikigi',
         'Takina Inoue', 'Mitsuha Your Name', 'Hina Weathering With You', 'Suzume', 'Raphtalia', 'Esdeath', 'Albedo',
         'Megumin', 'Tohru Honda', 'Hori Kyoko', 'Komi Shouko', 'Nezuko', 'Kurumi Tokisaki', 'Yuki Suou', 'Maomao',
         'Chika Fujiwara', 'Ichika Nakano', 'Shouko Nishimiya', 'Kaori Miyazono', 'Taiga Aisaka', 'Mio Akiyama',
         'Yumeko Jabami', 'Power Chainsaw Man', 'Nami One Piece', 'Robin One Piece', 'Boa Hancock', 'Lucy Edgerunners',
         'Rebecca Edgerunners', 'Makise Kurisu', 'Holo', 'Saber Fate', 'Rin Tohsaka', 'Elaina Wandering Witch',
         'Hitagi Senjougahara', 'Mai Sakurajima bunny', 'Erina Nakiri', 'Ruka Sarashina', 'Sumi Sakurasawa',
         'Kaori Miyazono violin', 'Nanami Aoyama', 'Mahiru Shiina', 'Ui Shigure', 'Kitagawa Marin cosplay']
SUFFIXES = ['twixtor clips for edits 4k', '4k twixtor', 'scenepack 4k raw', 'twixtor 60fps clips']
MOODS = ['anime girl cherry blossom twixtor 4k', 'anime girl summer beach twixtor 4k', 'anime girl snow twixtor 4k',
         'anime girl hair wind twixtor clips', 'makoto shinkai scenes 4k twixtor',
         'anime girl smile twixtor clips 4k', 'anime girl night lights twixtor 4k', 'anime girl rain twixtor 4k']
SHOWS_ED = ['Kaguya-sama Love is War', 'Bunny Girl Senpai', 'My Dress-Up Darling', 'Frieren', 'Violet Evergarden', 'Your Lie in April',
            'Horimiya', 'Toradora', 'Oshi no Ko', 'Rent-a-Girlfriend', 'The Quintessential Quintuplets', 'The Fragrant Flower Blooms with Dignity',
            'Roshidere Alya', 'Spy x Family', 'Re Zero', 'Darling in the Franxx', 'Akebi-chan no Sailor-fuku', 'Kimi ni Todoke',
            'Komi Cant Communicate', 'Tamako Market', 'Hyouka', 'Fruits Basket', 'The Dangers in My Heart', 'Tonikawa', 'Shikimori',
            'Lycoris Recoil', 'Bocchi the Rock', 'Chainsaw Man', 'Classroom of the Elite', 'Clannad', 'Anohana', 'Your Name',
            'A Silent Voice', 'Weathering With You', 'Suzume', 'The Apothecary Diaries', 'Kaoru Hana wa Rin to Saku', 'Blue Box', 'Makeine']
BAD = re.compile(r'\bamv\b|\bmmv\b|reaction|lyric|\bsubs?\b|#shorts|nightcore|hentai|ecchi|nsfw|18\+|\btiktok\b|'
                 r'speed ?paint|cosplay|\bmmd\b|\breal life\b|ai art|\bai\b', re.I)
EDIT_RE = re.compile(r'\bedit\b', re.I)
GOOD = re.compile(r'twixtor|twixter|scene ?pack|raw|clips|4k|60 ?fps|clean|textless', re.I)
# 2026-10-20 yield fix: OP / ED sources hold drawings, live action / music videos are not anime
REJECT = re.compile(r'creditless|\bopening\b|\bending\b|\bop ?\d*\b|\bed ?\d*\b|\bncop|\bnced|movie ?trailer|live[- ]?action|'
                    r'\bdrone\b|fireworks|music video|\bmv\b', re.I)
ANIME_WORD = re.compile(r'anime|アニメ|\bepisodes?\b|\bep\.? ?\d+|\bseason\b|\bs\d\b|scene ?pack|twixtor|twixter', re.I)
_GENERIC = {'zero', 're', 'one', 'piece', 'anime', 'girl', 'bunny', 'violin', 'cosplay', 'with', 'you', 'your', 'name', 'the', 'and',
            'love', 'free', 'orange', 'given', 'aria', 'wave', 'clips', 'sailor', 'moon', 'power', 'saber', 'fate', 'holo', 'kyoto'}


_NAMES = []


def known_names():
    """lower-case show / character names (full names + single name words >= 5 letters) from the query generator's lists"""
    if not _NAMES:
        full = set()
        for lst in (CHARS, SHOWS_ED, ANIME_DREAMY, ANIME_SUMMER):
            for x in lst:
                x = x.lower()
                full.add(x)
                full.update(w for w in re.split(r'[^a-z0-9]+', x) if len(w) >= 5 and w not in _GENERIC)
        _NAMES.extend(sorted(full))
    return _NAMES


def anime_title(t):
    """title filter: an anime signal (word, or a known show / character) and none of the REJECT words"""
    t = str(t or '')
    if REJECT.search(t):
        return False
    if ANIME_WORD.search(t):
        return True
    tl = t.lower()
    return any(n in tl for n in known_names())


def vid_shard(vid, n):
    """deterministic (crc32, not the salted hash()) shard of a video id: no two shards download the same video"""
    return zlib.crc32(str(vid).encode()) % max(1, n)


def log(*a):
    print(time.strftime('%H:%M:%S'), '[slowmo_grow]', *a, file=sys.stderr, flush=True)


def rj(p, d):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return d


def wj(p, d):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(p + '.tmp', p)


def low_priority():
    try:
        import psutil
        psutil.Process().nice(psutil.IDLE_PRIORITY_CLASS if os.name == 'nt' else 19)
    except Exception:  # noqa: BLE001
        pass


def wait_ram():
    need = float(os.environ.get('SLOWMO_GROW_MIN_GB', '0' if os.environ.get('CI') else '4'))
    if need <= 0:
        return
    import psutil
    while psutil.virtual_memory().available / 2 ** 30 < need:
        log(f'waiting for RAM ({psutil.virtual_memory().available / 2 ** 30:.1f} GB free)')
        time.sleep(60)


# ---- query generation: anime x visual terms x source types, per vibe look (rotating, see nightly_queries) ----
ANIME_DREAMY = ['Your Name', 'Weathering With You', 'Suzume', 'Garden of Words', '5 Centimeters per Second', 'A Silent Voice',
                'Violet Evergarden', 'Your Lie in April', 'Anohana', 'Clannad', 'Hyouka', 'Tamako Market', 'Kimi ni Todoke',
                'Fruits Basket', 'The Fragrant Flower Blooms with Dignity', 'Frieren', 'Horimiya', 'Sound Euphonium', 'Liz and the Blue Bird',
                'Free Kyoto Animation', 'Nichijou', 'K-On', 'Lucky Star', 'Tsurune', 'Love Live', 'Bocchi the Rock',
                'Wotakoi', 'Ao Haru Ride', 'Maquia', 'Josee the Tiger and the Fish', 'Orange anime', 'Given anime', 'Akebi-chan Sailor Uniform',
                'Cardcaptor Sakura', 'Sailor Moon', 'Princess Tutu', 'Ouran High School Host Club', 'Snow White with the Red Hair', 'Mushishi']
ANIME_SUMMER = ['Mai Sakurajima Bunny Girl Senpai', 'My Dress-Up Darling', 'Rent-a-Girlfriend', 'Quintessential Quintuplets', 'Toradora',
                'Kaguya-sama Love is War', 'Oshi no Ko', 'Spy x Family', 'Darling in the Franxx', 'Komi Cant Communicate', 'Lycoris Recoil',
                'Tonikawa', 'Shikimori', 'The Dangers in My Heart', 'Alya Roshidere', 'Blue Box', 'Nagatoro', 'Monogatari Series',
                'Date A Live', 'Love is Like a Cocktail', 'Kobayashi Dragon Maid', 'Hello World', 'Summer Time Rendering', 'Birdie Wing',
                'Yamada-kun and the Seven Witches', 'Idolmaster', 'Love Live Sunshine', 'Wave Listen to Me', 'Aharen-san',
                'Weathering With You', 'Your Name', 'Tamako Love Story', 'Hanasaku Iroha', 'Free Iwatobi Swim Club', 'Non Non Biyori',
                'Yuru Camp', 'Amanchu', 'Aria', 'Gochiusa', 'Kyoto Animation']
TERMS_DREAMY = ['cherry blossoms', 'soft rain', 'flower field', 'pastel sky', 'looking back', 'hair in the wind', 'smile', 'petals falling',
                'lanterns night', 'dreamy bokeh', 'spring', 'umbrella rain', 'pastel soft light', 'night rain', 'moonlight', 'flowers',
                'sakura petals', 'dreamy', 'ethereal glow', 'soft pastel night', 'moonlight flowers', 'rain window night']
TERMS_SUMMER = ['sunset', 'golden hour', 'ocean', 'beach', 'festival fireworks', 'hair in the wind', 'smile', 'looking back',
                'night city', 'summer sky', 'sunlight', 'seaside train']
# weighted by the 2026-10-20 yield (twixtor 4k 0.81 sources / query); no AMV, no scenery (live action) types
SRC_TYPES = ['twixtor 4k', 'twixtor 4k clips', 'twixtor 60fps', 'scenepack 4k', 'raw scenepack 4k', '4k anime scenes', '60fps clips']
KEEP_DAYS = 30          # a query is not reused within this many days ...
NIGHT_MIN = {'dreamy_bloom': 200, 'summer_glow': 100}     # ... unless fewer than this many are left for the vibe (oldest used first)
DREAMY_SHARE = 0.65
LEGACY_DATE = '2026-10-18'                                  # queries_done entries without a date: the first night's list


def _legacy_queries():
    qs = [f'{c} {SUFFIXES[i % 2]}' for i, c in enumerate(CHARS)] + MOODS
    qs += [f'{c} {SUFFIXES[2 + i % 2]}' for i, c in enumerate(CHARS)]
    return qs


def query_table():
    """[(query, vibe)] - every candidate query once; the old fixed list is kept (tagged summer_glow, its pack style is vibe-neutral)"""
    tab, seen = [], set()
    for q in _legacy_queries():
        if q not in seen:
            seen.add(q)
            tab.append((q, 'summer_glow'))
    for vibe, shows, terms in (('dreamy_bloom', ANIME_DREAMY, TERMS_DREAMY), ('summer_glow', ANIME_SUMMER, TERMS_SUMMER)):
        for sh in shows:
            for t in terms:
                for ty in SRC_TYPES:
                    q = f'{sh} {t} {ty}'
                    if q not in seen:
                        seen.add(q)
                        tab.append((q, vibe))
    return tab


def all_queries():
    return [q for q, _ in query_table()]


def used_dates(st):
    """{query: last-used date}; legacy undated entries count as used on LEGACY_DATE"""
    d = {q: LEGACY_DATE for q in st.get('queries_done') or []}
    d.update(st.get('query_log') or {})
    return d


def nightly_queries(st, today=None, seed=None):
    """tonight's ordered query list (every shard computes the same list from the same state; shard k takes i % n == k).
    Per vibe: queries not used in the last KEEP_DAYS days, seeded shuffle by date; if fewer than NIGHT_MIN are left, the oldest
    used ones are added back. dreamy_bloom gets DREAMY_SHARE of the slots (interleaved evenly so every shard gets the mix)."""
    today = today or time.strftime('%Y-%m-%d', time.gmtime())
    rng = random.Random(f'{seed or ""}{today}')
    used = used_dates(st)
    cutoff = time.strftime('%Y-%m-%d', time.gmtime(time.mktime(time.strptime(today, '%Y-%m-%d')) - KEEP_DAYS * 86400))
    tab = query_table()
    pools = {}
    for vibe in ('dreamy_bloom', 'summer_glow'):
        qs = sorted(q for q, v in tab if v == vibe)
        fresh = [q for q in qs if q not in used or used[q] <= cutoff]
        if len(fresh) < NIGHT_MIN[vibe]:
            old = sorted((q for q in qs if q in used and used[q] > cutoff), key=lambda q: (used[q], q))
            fresh += old[:NIGHT_MIN[vibe] - len(fresh)]
        rng.shuffle(fresh)
        pools[vibe] = fresh
    out, i = [], 0
    d, s = pools['dreamy_bloom'], pools['summer_glow']
    while d or s:
        take_d = int((i + 1) * DREAMY_SHARE) > int(i * DREAMY_SHARE)
        src = d if (take_d and d) or not s else s
        out.append(src.pop(0))
        i += 1
    return out


def title_selftest():
    ok = ['Frieren Beyond Journey End twixtor 4k', 'Mai Sakurajima scenepack 4k', 'Anime girl summer 60fps clips', 'Kaguya-sama Episode 5 4K raw',
          'アニメ 4K scenes', 'Zero Two Darling in the Franxx 4k clips', 'Your Name Mitsuha scenes 4k', 'Marin Kitagawa edit clips 4k']
    bad = ['Frieren Opening Creditless 4K', 'Clannad ED NCED 60fps', 'Toradora OP 1 NCOP anime', 'Avengers movie trailer 4k clips',
           'Tokyo city drone 4k clips', 'New Year fireworks 4k anime', 'Official Music Video 4k 60fps', 'Taylor Swift MV 4k',
           'Live action Spy x Family 4k clips', 'Ocean sunset 4k raw clips', 'Movieclips beach scenes 4k']
    for t in ok:
        assert anime_title(t), f'should pass: {t}'
    for t in bad:
        assert not anime_title(t), f'should reject: {t}'
    assert sum(1 for i in range(2000) if vid_shard(f'v{i}', 3) == 0) in range(550, 780)
    assert all(vid_shard('dQw4w9WgXcQ', 3) == vid_shard('dQw4w9WgXcQ', 3) for _ in range(3))


def selftest(nights=60, per_night=120):
    """60 simulated nights: the list never runs dry, queries rotate, dreamy_bloom gets >= 50 % of the work"""
    st, vib = {'queries_done': [], 'query_log': {}}, dict(query_table())
    cnt = {'dreamy_bloom': 0, 'summer_glow': 0}
    t0 = time.mktime(time.strptime('2026-10-19', '%Y-%m-%d'))
    for n in range(nights):
        day = time.strftime('%Y-%m-%d', time.gmtime(t0 + n * 86400))
        qs = nightly_queries(st, day)
        assert qs, f'night {n}: 0 queries left'
        assert nightly_queries(st, day) == qs, 'not deterministic'
        shards = [qs[k::3] for k in range(3)]
        assert sum(len(x) for x in shards) == len(qs) and all(shards), 'shard layout'
        for q in qs[:per_night]:
            st['query_log'][q] = day
            cnt[vib[q]] += 1
    title_selftest()
    ids = [f'vid{j:06d}x' for j in range(600)]       # video-id shards: disjoint, complete, roughly even
    parts = [{i for i in ids if vid_shard(i, 3) == k} for k in range(3)]
    assert not (parts[0] & parts[1] or parts[0] & parts[2] or parts[1] & parts[2]) and set().union(*parts) == set(ids)
    assert all(len(x) > 140 for x in parts), 'uneven id shards'
    share = cnt['dreamy_bloom'] / sum(cnt.values())
    assert share >= 0.5, f'dreamy share {share:.2f}'
    print(json.dumps({'ok': True, 'nights': nights, 'queries_total': len(vib), 'used_distinct': len(st['query_log']),
                      'dreamy_share': round(share, 3)}))


def ytdlp(args, timeout=240):
    cmd = [PY, '-m', 'yt_dlp', '--no-warnings', '--js-runtimes', os.environ.get('YT_JS_RUNTIMES', 'node')]
    if os.environ.get('YT_FORCE_IPV4'):
        cmd.append('--force-ipv4')
    jar = None
    ck = os.environ.get('YT_COOKIES')
    if ck and os.path.exists(ck):
        fd, jar = tempfile.mkstemp(suffix='.txt')
        os.close(fd)
        shutil.copy2(ck, jar)                     # a private jar per call: the account file is never rewritten / printed
        cmd += ['--cookies', jar]
    try:
        return subprocess.run(cmd + args, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    finally:
        if jar:
            try:
                os.remove(jar)
            except OSError:
                pass


def search(q):
    r = ytdlp(['--flat-playlist', '-J', f'ytsearch12:{q}'], timeout=120)
    try:
        return (json.loads(r.stdout) or {}).get('entries') or []
    except ValueError:
        return []


def pick(entries, seen, k=0, n=1):
    out = []
    for e in entries:
        vid, t = e.get('id'), str(e.get('title') or '')
        d = e.get('duration') or 0
        if not vid or vid in seen or vid_shard(vid, n) != k:
            continue
        if not anime_title(t):
            continue
        if not (60 <= d <= 1200) or BAD.search(t) or (EDIT_RE.search(t) and not re.search(r'for edit', t, re.I)):
            continue
        if not GOOD.search(t) or (e.get('view_count') or 0) < 500:
            continue
        out.append(e)
    return out[:2]


_last_dl = [0.0]


def download(vid, dst):
    gap = 22 + random.uniform(0, 10) - (time.time() - _last_dl[0])
    if gap > 0:
        time.sleep(gap)
    _last_dl[0] = time.time()
    r = ytdlp(['--no-playlist', '-f', 'bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080][vcodec^=vp9]/bv*[height<=1080]',
               '--max-filesize', '900M', '-o', os.path.join(dst, f'{vid}.%(ext)s'), f'https://www.youtube.com/watch?v={vid}'],
              timeout=1200)
    fs = [f for f in glob.glob(os.path.join(dst, vid + '.*')) if not f.endswith('.part')]
    if not fs:
        err = (r.stderr or '').strip().splitlines()[-1:] if r.stderr else []
        log(f'download {vid} failed: {err[0][:160] if err else r.returncode}')
    return fs[0] if fs else None


def run_library(src_dir, out_dir, n_src, cache):
    wait_ram()
    env = dict(os.environ, OMP_NUM_THREADS='4', MKL_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4', TORCH_NUM_THREADS='4',
               EDIT_THREADS='4', EDIT_LIBRARY_CAP='6', PYTHONIOENCODING='utf-8')
    cmd = [PY, os.path.join(EDIT, 'library.py'), '--mode', 'mood', '--query', 'beautiful anime girl', '--kind', 'anime',
           '--count', str(12 * n_src), '--min-dur', '2', '--max-dur', '6', '--preset', 'cinematic_slowmo', '--out-dir', out_dir,
           '--budget', '9000', '--local', os.path.join(src_dir, '*.mp4') + ',' + os.path.join(src_dir, '*.webm'), '--local-only',
           '--analyse-secs', '600', '--workers', '2', '--cache-dir', cache, '--cache-max-mb', '3000']
    t0 = time.time()
    with open(out_dir + '.log', 'w', encoding='utf-8') as lf:
        subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env, timeout=4 * 3600)
    log(f'library {n_src} sources: {time.time() - t0:.0f} s')
    return os.path.join(out_dir, 'shots.json')


def gate_child(shots, work):
    """cine_vibe.gate in a child process (its SigLIP / face models must not stay resident next to the library runs)"""
    jp = os.path.join(work, '_gate_in.json')
    wj(jp, shots)
    code = ('import json, sys; sys.path.insert(0, %r); import cine_vibe as C; '
            'json.dump(C.gate(json.load(open(%r, encoding="utf-8"))), open(%r, "w", encoding="utf-8"))') % (EDIT, jp, jp + '.out')
    if os.path.exists(jp + '.out'):
        os.remove(jp + '.out')
    subprocess.run([PY, '-c', code], env=dict(os.environ, OMP_NUM_THREADS='4', TORCH_NUM_THREADS='4', PYTHONIOENCODING='utf-8'),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return rj(jp + '.out', None)


def seed_staging_pool(pool):
    """the live slow-mo manifests copied into the staging pool root (the import dedupes against them; media stay live)"""
    for v in VIBES:
        d = os.path.join(pool, 'slowmo', v)
        os.makedirs(os.path.join(d, 'clips'), exist_ok=True)
        for f in ('clips.json', 'sounds.json'):
            src = os.path.join(LIVE, 'slowmo', v, f)
            if os.path.exists(src) and not os.path.exists(os.path.join(d, f)):
                shutil.copy2(src, os.path.join(d, f))


def import_and_gate(VP, shots_json, pool, srcmap, work, counts, target):
    d = rj(shots_json, {})
    shots = d.get('shots') if isinstance(d, dict) else d
    if not shots:
        return {}
    for s in shots:                                  # local runs: the real youtube id from the file name
        stem = os.path.splitext(os.path.basename(str(s.get('source_title') or s.get('source') or '')))[0]
        if stem in srcmap:
            s['source_id'] = stem
            s['source_title'] = srcmap[stem]['title']
            s['source_channel'] = srcmap[stem].get('channel')
    split = {v: [] for v in VIBES}
    for s in shots:
        v = 'summer_glow' if float(s.get('luma') or 120) >= 105 else 'dreamy_bloom'
        o = VIBES[1 - VIBES.index(v)]
        if counts.get(v, 0) >= target and counts.get(o, 0) < target:
            v = o
        split[v].append(s)
    res = {}
    for v, ss in split.items():
        if not ss:
            continue
        tmp = shots_json + f'.{v}.json'
        wj(tmp, {'shots': ss, 'query': d.get('query')})
        before = {it['id'] for it in VP.load_manifest('slowmo', v, root=pool)['items']}
        added, skip = VP.import_shots('slowmo', v, tmp, root=pool, query='slowmo_grow ' + time.strftime('%Y-%m'))
        man = VP.load_manifest('slowmo', v, root=pool)
        new = [it for it in man['items'] if it['id'] not in before]
        vd = VP.vibe_dir('slowmo', v, pool)
        g = gate_child([dict(it, path=os.path.join(vd, it['file']), pool_id=it['id']) for it in new], work) if new else {}
        ok = 0
        if g is None:
            log(f'{v}: gate failed - {len(new)} new clips dropped from staging (media kept out, rows marked)')
        for it in new:
            rs = (g or {}).get(it['id']) or ([] if g is not None else ['gate_failed'])
            if rs:
                it['status'] = 'cine_reject'
                it['cine_reject'] = rs
                for f in glob.glob(os.path.join(vd, it['file']) + '*'):
                    os.remove(f)
            else:
                it['staged'] = time.strftime('%Y-%m-%d')
                ok += 1
        VP.save_manifest('slowmo', v, man, root=pool)
        counts[v] = counts.get(v, 0) + ok
        res[v] = dict(added=added, clean=ok, skip=skip)
        log(f'{v}: +{added} imported, {ok} clean, skip {skip}')
    return res


def sheet(paths_labels, out, cols=3):
    import cv2
    import numpy as np
    W, H = 200, 112
    tiles = []
    for p, lab in paths_labels:
        c = cv2.VideoCapture(p)
        N = int(c.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fr = []
        for k in range(4):
            c.set(cv2.CAP_PROP_POS_FRAMES, int((k + 0.5) * N / 4))
            ok, f = c.read()
            fr.append(cv2.resize(f, (W, H)) if ok else np.zeros((H, W, 3), np.uint8))
        c.release()
        t = np.vstack([np.zeros((20, 4 * W, 3), np.uint8), np.hstack(fr)])
        cv2.putText(t, lab[:70], (4, 15), 0, 0.45, (0, 255, 255), 1)
        tiles.append(t)
    if not tiles:
        return
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    g = np.vstack([np.hstack(tiles[r:r + cols]) for r in range(0, len(tiles), cols)])
    cv2.imwrite(out, g, [cv2.IMWRITE_JPEG_QUALITY, 82])


def cmd_mine(a):
    low_priority()
    import vibe_pool as VP
    work = os.path.abspath(a.work)
    pool = os.path.join(work, 'pool')
    out = os.path.join(work, 'out')
    os.makedirs(out, exist_ok=True)
    seed_staging_pool(pool)
    live_ids = {v: {it['id'] for it in VP.load_manifest('slowmo', v, root=pool)['items']} for v in VIBES}
    st = rj(a.state, {}) if a.state else {}
    st.setdefault('queries_done', [])
    st.setdefault('sources', {})
    k, n = (int(x) for x in a.shard.split('/'))
    seen = set(st['sources']) | {it.get('source_id') for v in VIBES for it in VP.load_manifest('slowmo', v, root=pool)['items']}
    qs = [q for i, q in enumerate(nightly_queries(st)) if i % n == k]
    log(f'shard {k}/{n}: {len(qs)} queries left')
    end = time.time() + a.budget_min * 60
    counts = {v: 0 for v in VIBES}
    batches = []
    bi = 0
    while time.time() < end - 15 * 60 and qs:
        src = os.path.join(work, f'src{bi:02d}')
        os.makedirs(src, exist_ok=True)
        srcmap = {}
        while qs and len(srcmap) < a.batch and time.time() < end - 25 * 60:
            q = qs.pop(0)
            es = pick(search(q), seen, k, n)
            st['queries_done'].append(q)
            st.setdefault('query_log', {})[q] = time.strftime('%Y-%m-%d', time.gmtime())
            time.sleep(4)
            for e in es:
                if len(srcmap) >= a.batch:
                    break
                seen.add(e['id'])
                f = download(e['id'], src)
                st['sources'][e['id']] = {'q': q, 'ok': bool(f), 'at': time.strftime('%Y-%m-%d')}
                if f:
                    srcmap[e['id']] = {'title': e.get('title'), 'channel': e.get('channel') or e.get('uploader')}
                    log(f"got {e['id']} {str(e.get('title'))[:60]!r}")
        if not srcmap:
            break
        sj = run_library(src, os.path.join(work, f'lib{bi:02d}'), len(srcmap), os.path.join(work, 'cache'))
        res = import_and_gate(VP, sj, pool, srcmap, work, counts, 10 ** 6) if os.path.exists(sj) else {}
        batches.append({'i': bi, 'sources': list(srcmap), 'res': res})
        shutil.rmtree(src, ignore_errors=True)                       # sources deleted after cutting
        shutil.rmtree(os.path.join(work, 'cache', 'src'), ignore_errors=True)
        shutil.rmtree(os.path.join(work, f'lib{bi:02d}'), ignore_errors=True)
        wj(os.path.join(out, 'state.json'), st)
        bi += 1
    # the staged candidates: clean new items + media + sidecars + sheets
    cands = {}
    for v in VIBES:
        vd = VP.vibe_dir('slowmo', v, pool)
        new = [it for it in VP.load_manifest('slowmo', v, root=pool)['items'] if it['id'] not in live_ids[v] and it.get('status') == 'fresh']
        od = os.path.join(out, v, 'clips')
        os.makedirs(od, exist_ok=True)
        for it in new:
            for f in glob.glob(os.path.join(vd, it['file']) + '*'):
                shutil.copy2(f, od)
        cands[v] = new
        os.makedirs(os.path.join(out, 'sheets'), exist_ok=True)
        for i in range(0, len(new), 24):
            sheet([(os.path.join(od, os.path.basename(it['file'])), f"{it['id']} {str(it.get('source_title'))[:40]}") for it in new[i:i + 24]],
                  os.path.join(out, 'sheets', f'{v}_s{k}_{i // 24:02d}.jpg'))
    rejected = {v: [it['id'] for it in VP.load_manifest('slowmo', v, root=pool)['items'] if it['id'] not in live_ids[v]
                    and it.get('status') == 'cine_reject'] for v in VIBES}
    wj(os.path.join(out, 'candidates.json'), {'shard': a.shard, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                              'candidates': cands, 'cine_rejected_ids': rejected, 'batches': batches})
    wj(os.path.join(out, 'state.json'), st)
    log(f'staged: ' + ', '.join(f'{v} {len(c)}' for v, c in cands.items()))
    print(json.dumps({'ok': True, 'staged': {v: len(c) for v, c in cands.items()}, 'batches': len(batches)}))


def _gh(args, check=True):
    return subprocess.run(['gh'] + args, capture_output=True, text=True, check=check)


def staged_assets():
    r = _gh(['release', 'view', RELEASE, '--json', 'assets', '--jq', '.assets[].name'], check=False)
    return [x for x in r.stdout.split() if x.startswith('slowmo-cand-') and x.endswith('.tar')] if r.returncode == 0 else []


def staged_counts():
    """{vibe: candidates} on the staging release, from the tar names slowmo-cand-<date>-s<k>-sg<N>-db<M>.tar"""
    out = {v: 0 for v in VIBES}
    for x in staged_assets():
        m = re.search(r'-sg(\d+)-db(\d+)\.tar$', x)
        if m:
            out['summer_glow'] += int(m.group(1))
            out['dreamy_bloom'] += int(m.group(2))
    return out


def cmd_need(a):
    """go = some vibe is under target: live clean reviewed clips (manifest rows; media may be absent on CI) + staged candidates"""
    import vibe_pool as VP
    live = {v: sum(1 for it in VP.load_manifest('slowmo', v)['items'] if it.get('status') in ('fresh', 'used') and it.get('reviewed'))
            for v in VIBES}
    staged = staged_counts()
    go = any(live[v] + staged[v] < a.target for v in VIBES)
    log(f'live {live} staged {staged} target {a.target}')
    print('go' if go else 'stop')


MANIFESTS_ASSET = 'slowmo-live-manifests.tar'


def cmd_push_manifests(a):
    """owner PC: upload the live slow-mo MANIFESTS (no media; the go-live pool upload is separate) + the grow state to the staging
    release, so the GitHub miner dedupes against the live pool and never re-mines a seen source"""
    d = tempfile.mkdtemp(prefix='slowmo-man-')
    tp = os.path.join(d, MANIFESTS_ASSET)
    with tarfile.open(tp, 'w') as t:
        for v in VIBES:
            for f in ('clips.json', 'sounds.json'):
                src = os.path.join(LIVE, 'slowmo', v, f)
                if os.path.exists(src):
                    t.add(src, arcname=f'slowmo/{v}/{f}')
    st = rj(os.path.join(LIVE, '_grow_slowmo', 'state.json'), {})        # the PC miner's state (queries done, sources seen)
    remote = os.path.join(d, STATE_ASSET)
    if _gh(['release', 'download', RELEASE, '--pattern', STATE_ASSET, '--dir', d], check=False).returncode != 0:
        wj(remote, {})
    merged = merge_states([rj(remote, {}), {'queries_done': st.get('queries_done') or [], 'sources': st.get('sources') or {}}])
    wj(remote, merged)
    if _gh(['release', 'view', RELEASE], check=False).returncode != 0:
        _gh(['release', 'create', RELEASE, '--title', 'edit pools staging (slow-mo candidates)', '--notes',
             'Nightly slow-mo clip candidates (core/pools/slowmo_grow.py, .github/workflows/slowmo-pool-mine.yml). Reviewed by eye '
             'on the owner PC (pull-staged / import-staged) before they join the live pool.', '--prerelease'])
    _gh(['release', 'upload', RELEASE, tp, remote, '--clobber'])
    print(json.dumps({'ok': True, 'uploaded': [MANIFESTS_ASSET, STATE_ASSET], 'sources_seen': len(merged['sources'])}))


def merge_states(states):
    out = {'queries_done': [], 'sources': {}, 'query_log': {}}
    for st in states:
        for q, dt in (st.get('query_log') or {}).items():
            out['query_log'][q] = max(dt, out['query_log'].get(q, ''))
        for q in st.get('queries_done') or []:
            if q not in out['queries_done']:
                out['queries_done'].append(q)
        out['sources'].update(st.get('sources') or {})
    return out


def cmd_publish(a):
    """CI: the shards' outputs -> one tar per shard (named with its counts, duplicates across shards dropped) + the merged state,
    uploaded to the staging release"""
    arts = sorted(glob.glob(os.path.join(a.artifacts, '*', 'candidates.json')))
    states = [rj(a.state, {})] if a.state and os.path.exists(a.state) else []
    seen, tars, total = set(), [], {v: 0 for v in VIBES}
    os.makedirs(a.out, exist_ok=True)
    day = time.strftime('%Y%m%d', time.gmtime())
    for cj in arts:
        base = os.path.dirname(cj)
        states.append(rj(os.path.join(base, 'state.json'), {}))
        c = rj(cj, {})
        cnt = {}
        for v in VIBES:
            keep = []
            for it in (c.get('candidates') or {}).get(v, []):
                if it['id'] in seen:
                    for f in glob.glob(os.path.join(base, v, 'clips', os.path.basename(it['file']) + '*')):
                        os.remove(f)
                    continue
                seen.add(it['id'])
                keep.append(it)
            c.setdefault('candidates', {})[v] = keep
            cnt[v] = len(keep)
            total[v] += len(keep)
        wj(cj, c)
        if not sum(cnt.values()):
            continue
        k = str(c.get('shard', '0/1')).split('/')[0]
        name = f"slowmo-cand-{day}-s{k}-sg{cnt['summer_glow']}-db{cnt['dreamy_bloom']}.tar"
        with tarfile.open(os.path.join(a.out, name), 'w') as t:
            for f in ['candidates.json'] + [os.path.relpath(x, base) for v in VIBES for x in glob.glob(os.path.join(base, v, 'clips', '*'))]:
                t.add(os.path.join(base, f), arcname=f.replace(os.sep, '/'))
        tars.append(os.path.join(a.out, name))
    wj(os.path.join(a.out, STATE_ASSET), merge_states(states))
    if not a.dry:
        _gh(['release', 'upload', RELEASE] + tars + [os.path.join(a.out, STATE_ASSET), '--clobber'])
    print(json.dumps({'ok': True, 'tars': [os.path.basename(x) for x in tars], 'staged': total, 'dry': bool(a.dry)}))


def cmd_pull(a):
    d = os.path.abspath(a.dir)
    os.makedirs(d, exist_ok=True)
    pulled = rj(os.path.join(d, 'pulled.json'), {'assets': []})
    todo = [x for x in staged_assets() if x not in pulled['assets']]
    for name in todo:
        _gh(['release', 'download', RELEASE, '--pattern', name, '--dir', d, '--clobber'])
        sub = os.path.join(d, name[:-4])
        os.makedirs(sub, exist_ok=True)
        with tarfile.open(os.path.join(d, name)) as t:
            for m in t.getmembers():
                if m.name.startswith('/') or '..' in m.name:
                    continue                              # never outside the folder
                t.extract(m, sub)
        os.remove(os.path.join(d, name))
        pulled['assets'].append(name)
        log(f'pulled {name}')
    wj(os.path.join(d, 'pulled.json'), pulled)
    # one numbered review list over every pulled, not yet imported candidate
    rows = []
    for cj in sorted(glob.glob(os.path.join(d, 'slowmo-cand-*', 'candidates.json'))):
        base = os.path.dirname(cj)
        if os.path.exists(os.path.join(base, 'IMPORTED')):
            continue
        for v, items in (rj(cj, {}).get('candidates') or {}).items():
            for it in items:
                p = os.path.join(base, v, 'clips', os.path.basename(it['file']))
                if os.path.exists(p):
                    rows.append({'n': len(rows), 'vibe': v, 'id': it['id'], 'path': p, 'dir': base})
    rv = os.path.join(d, 'review')
    shutil.rmtree(rv, ignore_errors=True)
    os.makedirs(rv)
    for i in range(0, len(rows), 24):
        sheet([(r['path'], f"{r['n']}  {r['vibe']}  {r['id']}") for r in rows[i:i + 24]], os.path.join(rv, f'review_{i // 24:02d}.jpg'))
    wj(os.path.join(rv, 'index.json'), rows)
    print(json.dumps({'ok': True, 'pulled': todo, 'to_review': len(rows), 'sheets': rv}))


def cmd_import(a):
    import vibe_pool as VP
    d = os.path.abspath(a.dir)
    rows = rj(os.path.join(d, 'review', 'index.json'), [])
    rej = {x.strip() for x in (a.reject or '').split(',') if x.strip()}
    rej_ids = {r['id'] for r in rows if r['id'] in rej or str(r['n']) in rej}
    stamp = time.strftime('%Y-%m-%d') + ' eye pull-staged'
    added, rejected = 0, 0
    dirs = sorted({r['dir'] for r in rows})
    for base in dirs:
        cj = rj(os.path.join(base, 'candidates.json'), {})
        for v, items in (cj.get('candidates') or {}).items():
            man = VP.load_manifest('slowmo', v)
            have = {it['id'] for it in man['items']}
            vd = VP.vibe_dir('slowmo', v)
            os.makedirs(os.path.join(vd, 'clips'), exist_ok=True)
            for it in items:
                if it['id'] in have:
                    continue
                it = dict(it, reviewed=stamp)
                if it['id'] in rej_ids:
                    it['status'] = 'rejected'
                    it['review_note'] = 'eye (pull-staged review)'
                    rejected += 1
                else:
                    for f in glob.glob(os.path.join(base, v, 'clips', os.path.basename(it['file']) + '*')):
                        shutil.copy2(f, os.path.join(vd, 'clips'))
                    added += 1
                man['items'].append(it)
                have.add(it['id'])
            for rid in (cj.get('cine_rejected_ids') or {}).get(v, []):
                if rid not in have:                      # remembered so it is never mined again (no media)
                    man['items'].append({'id': rid, 'status': 'cine_reject', 'file': f'clips/{rid}.mp4', 'source_id': rid[2:13],
                                         'start': 0, 'end': 0, 'moment': rid})
                    have.add(rid)
            VP.save_manifest('slowmo', v, man)
        open(os.path.join(base, 'IMPORTED'), 'w').close()
        if a.delete_remote:
            _gh(['release', 'delete-asset', RELEASE, os.path.basename(base) + '.tar', '-y'], check=False)
    VP.write_index()
    print(json.dumps({'ok': True, 'imported': added, 'rejected': rejected,
                      'clean_now': {v: len(VP.fresh_clips('slowmo', v, rotation=False)) for v in VIBES}}))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    m = sp.add_parser('mine')
    m.add_argument('--work', required=True)
    m.add_argument('--shard', default='0/1')
    m.add_argument('--budget-min', type=float, default=200)
    m.add_argument('--batch', type=int, default=8)
    m.add_argument('--state')
    sp.add_parser('selftest')
    n = sp.add_parser('need')
    n.add_argument('--target', type=int, default=60)
    p = sp.add_parser('pull-staged')
    p.add_argument('--dir', default=os.path.join(LIVE, '_staged'))
    i = sp.add_parser('import-staged')
    i.add_argument('--dir', default=os.path.join(LIVE, '_staged'))
    i.add_argument('--reject', default='')
    i.add_argument('--delete-remote', action='store_true')
    pm = sp.add_parser('push-manifests')
    pb = sp.add_parser('publish')
    pb.add_argument('--artifacts', required=True)
    pb.add_argument('--out', required=True)
    pb.add_argument('--state')
    pb.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'selftest':
        return selftest()
    {'mine': cmd_mine, 'need': cmd_need, 'pull-staged': cmd_pull, 'import-staged': cmd_import,
     'push-manifests': cmd_push_manifests, 'publish': cmd_publish}[a.cmd](a)


if __name__ == '__main__':
    main()
