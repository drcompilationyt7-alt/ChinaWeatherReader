"""Music pool for the CINEMATIC SLOW-MO edit (Zero Yen Otaku, a WOMEN BEAUTY edit): romantic / dreamy / pretty famous songs,
played regular or SLOWED.

Same design as core/edit/music_pool.py (the sound edit's pool; this module reuses its yt-dlp, official-upload search, audio
cache, Thompson-sampling pick, cooldown, mark_used / record_performance / retirement), with its own pool file, analysis and
gates:

  pool file   core/edit/assets/slowmo-music-pool.json   (records: id, song, artist, video_id, views, slowed_uploads, tempo,
              speed, section {start, hero, end, score} in ORIGINAL song seconds, scores, total, status, last_used, perf).
              NO LYRICS are stored, ever: lyrics are fetched and aligned only at render time (lyrics.py, LYRICS_NO_STORE=1);
              the gate only checks that LRCLIB has them (a boolean, nothing written).
  pick(history)        -> {id, song, artist, audio_path, speed, section_start, section_hero, section_end, ...}
                          never the previous song, 30-day cooldown (MUSIC_COOLDOWN_DAYS), Thompson sampling, probation -0.15
  mark_used(id) / record_performance(id, lscore) / retire_underperformers(pool, channel_mean)
  analyze(path)        tempo, calm / emotion scores, the best section: a hook / chorus start (the HERO, where the picture
                       starts after the words-on-black intro) with a sung line in the ~6 s before it, 30-45 s once slowed
  topup()              monthly: candidates (LLM: trending beauty-edit songs + seed list) -> beauty_fit -> gates -> probation (max SLOWMO_TOPUP_MAX a month)

Gates: official upload >= 3M views (famous), 120-420 s, lyrics available (sung words for the intro), calm score >= 0.35
(slow / mid tempo, soft percussion, sustained harmony: songs that work slowed), best section score >= 0.4 with no quiet
hole > 1.5 s in the body.  total = .45 calm + .25 section + .2 popularity + .1 slowed trend ("<song> slowed" uploads).
Speed: 0.95 (< 95 BPM), 0.9 (< 120), 0.85 (faster): the slowed version, pitch follows (song_fx.make_version).

  python core/edit/slowmo_music.py selftest | seed [N] | topup [--dry-run] | show | pick | analyze FILE
"""
import json
import math
import os
import random
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import music_pool as MP  # noqa: E402

POOL = os.environ.get('SLOWMO_MUSIC_POOL_FILE') or os.path.join(HERE, 'assets', 'slowmo-music-pool.json')
SR = 22050
HOP = 512
MIN_VIEWS = float(os.environ.get('SLOWMO_MIN_VIEWS', 3e6))
MIN_CALM = float(os.environ.get('SLOWMO_MIN_CALM', 0.35))
MIN_SECTION = float(os.environ.get('SLOWMO_MIN_SECTION', 0.4))
MAX_PER_MONTH = int(os.environ.get('SLOWMO_TOPUP_MAX', 8))
INTRO_S = 5.5                     # words on black before the hero (slowed seconds)
BODY_LO, BODY_HI = 26.0, 40.0     # picture after the hero (slowed seconds): Shorts of 30-50 s
SLOWED_RE = re.compile(r'slowed|reverb', re.I)


def log(*a):
    print('[slowmo_music]', *a, flush=True)


def load_pool(path=None):
    p = MP.load_pool(path or POOL)
    p['note'] = ('Music pool for the cinematic SLOW-MO edit (core/edit/slowmo_music.py). No lyrics, ever (fetched and aligned at render '
                 'time only). Audio fetched at render time. section = original-song seconds: start (words-on-black intro), hero (the '
                 'picture starts), end; played slowed by speed.')
    return p


def save_pool(p, path=None):
    MP.save_pool(p, path or POOL)


def speed_for(bpm):
    return 0.95 if bpm < 95 else (0.9 if bpm < 120 else 0.85)


# ------------------------------------------------------------------ analysis
def analyze(path=None, y=None):
    """-> {tempo, duration, speed, scores{calm, perc_share, onset_rate, sustain}, calm, section{start, hero, end, score, parts},
    sections[top3]} (seconds of the ORIGINAL song)."""
    import librosa
    if y is None:
        y = MP.load_audio(path, SR)
    dur = len(y) / SR
    yh, yp = librosa.effects.hpss(y, margin=1.0)
    ep, eh = float(np.mean(yp ** 2)), float(np.mean(yh ** 2))
    perc = ep / (ep + eh + 1e-12)
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    tempo, beats = librosa.beat.beat_track(onset_envelope=env, sr=SR, hop_length=HOP)
    bpm = float(np.atleast_1d(tempo)[0]) or 90.0
    while bpm > 135:
        bpm /= 2
    while bpm < 55:
        bpm *= 2
    bt = librosa.frames_to_time(beats, sr=SR, hop_length=HOP)
    pk = librosa.onset.onset_detect(onset_envelope=env, sr=SR, hop_length=HOP, units='time')
    onset_rate = len(pk) / max(1.0, dur)
    # sustain: how much of the harmonic energy is held (low frame-to-frame chroma change)
    ch = librosa.feature.chroma_stft(y=yh, sr=SR, hop_length=HOP * 4)
    chn = ch / (np.linalg.norm(ch, axis=0, keepdims=True) + 1e-9)
    sustain = float(np.clip(np.median(np.sum(chn[:, 1:] * chn[:, :-1], axis=0)), 0, 1))
    calm = (0.35 * float(np.clip(1 - (perc - 0.12) / 0.35, 0, 1)) + 0.25 * float(np.clip((135 - bpm) / 65, 0, 1))
            + 0.2 * float(np.clip(1 - (onset_rate - 1.5) / 3.5, 0, 1)) + 0.2 * float(np.clip((sustain - 0.6) / 0.35, 0, 1)))
    # loudness (0.5 s) and chroma self-similarity (the hook = the bars that repeat most)
    hop_s = 0.5
    n = int(dur / hop_s)
    r = np.array([np.sqrt(np.mean(y[int(i * hop_s * SR):int((i + 1) * hop_s * SR)] ** 2) + 1e-12) for i in range(n)])
    rdb = 20 * np.log10(r)
    ref_db = float(np.percentile(rdb, 75))
    C = librosa.feature.chroma_cqt(y=yh, sr=SR, hop_length=HOP) if dur < 600 else ch
    ct = librosa.frames_to_time(np.arange(C.shape[1]), sr=SR, hop_length=HOP)
    cs = np.stack([C[:, (ct >= i * hop_s) & (ct < (i + 1) * hop_s)].mean(axis=1) if ((ct >= i * hop_s) & (ct < (i + 1) * hop_s)).any()
                   else np.zeros(12) for i in range(n)])
    cs = cs / (np.linalg.norm(cs, axis=1, keepdims=True) + 1e-9)
    S = cs @ cs.T
    rep = np.array([np.sort(np.r_[S[i, :max(0, i - 8)], S[i, i + 8:]])[-6:].mean() if n > 20 else 0.5 for i in range(n)])
    speed = speed_for(bpm)
    rows = []
    for h in bt:
        if h < INTRO_S * speed + 1.0 or h > dur - BODY_LO * speed - 2.0:
            continue
        i = int(h / hop_s)
        pre, post = rdb[max(0, i - 6):i], rdb[i:i + 6]
        if not len(pre) or not len(post):
            continue
        jump = float(np.mean(post) - np.mean(pre))
        L = min(BODY_HI * speed, dur - h - 1.5)
        if L < BODY_LO * speed:
            continue
        b = rdb[i:i + int(L / hop_s)]
        hole = 0.0
        run = 0
        for x in b:
            run = run + 1 if x < ref_db - 15 else 0
            hole = max(hole, run * hop_s)
        a0 = int((h - INTRO_S * speed) / hop_s)
        intro_loud = float(np.mean(rdb[a0:i]) - ref_db) if i > a0 else -30.0
        energy = float(np.clip((np.mean(b) - ref_db + 8) / 8, 0, 1))
        hook = float(np.clip((np.mean(rep[i:i + int(L / hop_s)]) - 0.6) / 0.35, 0, 1))
        sc = (0.35 * hook + 0.3 * energy + 0.2 * float(np.clip(jump / 4, 0, 1)) + 0.15 * float(np.clip((intro_loud + 14) / 10, 0, 1))
              - (1.0 if hole > 1.5 else 0.0))
        rows.append(dict(start=round(float(h - INTRO_S * speed), 2), hero=round(float(h), 2), end=round(float(h + L), 2), score=round(sc, 3),
                         parts=dict(hook=round(hook, 2), energy=round(energy, 2), jump_db=round(jump, 1), intro_db=round(intro_loud, 1),
                                    hole_s=hole)))
    rows.sort(key=lambda x: -x['score'])
    top = []
    for x in rows:                                   # distinct sections (>= 8 s apart)
        if all(abs(x['hero'] - t['hero']) >= 8 for t in top):
            top.append(x)
        if len(top) == 3:
            break
    return dict(tempo=round(bpm, 1), duration=round(dur, 1), speed=speed, calm=round(calm, 3),
                scores=dict(calm=round(calm, 3), perc_share=round(perc, 3), onset_rate=round(onset_rate, 2), sustain=round(sustain, 3)),
                section=top[0] if top else None, sections=top)


# ------------------------------------------------------------------ gates + records
def has_lyrics(song, artist):
    """LRCLIB has the song's lyrics (checked, never stored: LYRICS_NO_STORE)."""
    os.environ.setdefault('LYRICS_NO_STORE', '1')
    try:
        import lyrics as LY
        return bool(LY.fetch(song, artist or '').get('lines'))
    except Exception as e:  # noqa: BLE001
        log('lyrics check failed:', type(e).__name__)
        return False


def slowed_count(song, artist, search=MP.yt_search):
    ns = MP.norm(song)
    rows = search(f'{song} {artist} slowed reverb', 15)
    return sum(1 for r in rows if ns in MP.norm(r['title']) and SLOWED_RE.search(r['title']))


def total(an, views, slowed):
    sec = (an.get('section') or {}).get('score') or 0.0
    return round(0.45 * an['calm'] + 0.25 * float(np.clip(sec, 0, 1)) + 0.2 * MP.pop_score(views)
                 + 0.1 * float(np.clip(slowed / 8, 0, 1)), 3)


def gate(an, views, lyr):
    if views < MIN_VIEWS:
        return f'views {views} < {MIN_VIEWS:.0f}'
    if not lyr:
        return 'no lyrics on LRCLIB (the intro shows the sung words)'
    if not 120 <= an['duration'] <= 420:
        return f"duration {an['duration']} s"
    if an['calm'] < MIN_CALM:
        return f"not calm / emotional enough (calm {an['calm']})"
    sec = an.get('section')
    if not sec or sec['score'] < MIN_SECTION:
        return f"no good section (best {sec['score'] if sec else None})"
    return None


def make_record(song, artist, up, slowed, an, source, status='probation'):
    sec = an['section']
    return {
        'id': re.sub(r'[^a-z0-9]+', '_', f'{artist}_{song}'.lower()).strip('_')[:60],
        'song': song, 'artist': artist, 'video_id': up['id'], 'url': f"https://www.youtube.com/watch?v={up['id']}",
        'query': f'{artist} {song} official audio', 'views': up['views'], 'slowed_uploads': slowed, 'source': source,
        'tempo': an['tempo'], 'bar_s': round(240.0 / an['tempo'], 3), 'speed': an['speed'], 'duration': an['duration'],
        'section': dict(sec, alternatives=[[s['start'], s['hero'], s['end'], s['score']] for s in an['sections'][1:]]),
        'scores': dict(an['scores'], popularity=round(MP.pop_score(up['views']), 3), slowed=round(float(np.clip(slowed / 8, 0, 1)), 3),
                       section=sec['score']),
        'total': total(an, up['views'], slowed), 'lyrics': 'render-time only',
        'status': status, 'added': MP.iso(), 'last_used': None, 'uses': 0, 'perf': {'n': 0, 'sum': 0.0},
    }


def add_candidate(pool, song, artist, source, search=MP.yt_search, analyze_fn=None, fetch_fn=None, lyr_fn=None, slowed_fn=None):
    key = MP.norm(song)
    if any(MP.norm(s['song']) == key for s in pool['songs']):
        return 'dup', 'already in the pool'
    if any(MP.norm(s['song']) == key for s in pool['rejected']):
        return 'dup', 'rejected before'
    up = MP.find_official(song, artist, search)
    if not up:
        return 'rejected', 'no matching official upload'
    if up['views'] < MIN_VIEWS:
        pool['rejected'].append({'song': song, 'artist': artist, 'at': MP.iso(), 'why': f"views {up['views']}"})
        return 'rejected', f"views {up['views']}"
    lyr = (lyr_fn or has_lyrics)(song, artist)
    if not lyr:
        pool['rejected'].append({'song': song, 'artist': artist, 'at': MP.iso(), 'why': 'no lyrics'})
        return 'rejected', 'no lyrics on LRCLIB'
    path = (fetch_fn or MP.fetch_audio)({'video_id': up['id']})
    if not path:
        return 'error', 'audio download failed'
    an = (analyze_fn or analyze)(path)
    why = gate(an, up['views'], lyr)
    if why:
        pool['rejected'].append({'song': song, 'artist': artist, 'at': MP.iso(), 'why': why})
        return 'rejected', why
    sl = (slowed_fn or (lambda s, a: slowed_count(s, a, search)))(song, artist)
    rec = make_record(song, artist, up, sl, an, source)
    if os.environ.get('MUSIC_KEEP_AUDIO', '1') == '0':
        try:
            os.remove(path)
        except OSError:
            pass
    pool['songs'].append(rec)
    return 'added', f"calm {an['calm']} section {rec['section']['start']}-{rec['section']['end']} (hero {rec['section']['hero']}) total {rec['total']}"


# ------------------------------------------------------------------ pick / use / learn (music_pool's logic on this pool file)
VERSION_SEP = '@v'


def versions(rec):
    """the song's two options (owner 2026-10-18: regular AND slowed): [1.0, the slowed speed]"""
    sl = float(rec.get('speed') or speed_for(rec['tempo']))
    return [1.0, sl] if sl < 0.995 else [1.0]


def version_id(song_id, speed):
    return f'{song_id}{VERSION_SEP}{float(speed):g}'


def split_id(vid):
    """'jvke_golden_hour@v0.85' -> ('jvke_golden_hour', 0.85); a plain id -> (id, None)"""
    if VERSION_SEP in str(vid):
        a, b = str(vid).rsplit(VERSION_SEP, 1)
        try:
            return a, float(b)
        except ValueError:
            return str(vid), None
    return str(vid), None


def pick(history=None, fetch=False, rng=None, pool_path=None, when=None, exclude=None):
    """the next slow-mo song AND version. Every song is two candidates (speed 1.0 and its slowed speed, ~50 / 50 by equal priors);
    the cooldown / no-repeat unit is song + version (history ids 'id@v0.85'), and the last video's song is never picked again in
    either version. exclude: base ids to skip (default env SLOWMO_EXCLUDE_SONGS, comma separated: the CI repair loop's
    'another song' attempt, a song without lyrics). -> pick_song's dict + {version_id, speed, section_hero}"""
    import tempfile
    rng = rng or random.Random()
    pool = load_pool(pool_path)
    hist = list(history or [])
    last = split_id((hist[-1].get('id') if isinstance(hist[-1], dict) else hist[-1]) if hist else '')[0]
    if exclude is None:
        exclude = {split_id(x.strip())[0] for x in os.environ.get('SLOWMO_EXCLUDE_SONGS', '').split(',') if x.strip()}
    exclude = set(exclude or ())
    if exclude and len([r for r in pool['songs'] if r['id'] not in exclude]) >= 1:
        pool = dict(pool, songs=[r for r in pool['songs'] if r['id'] not in exclude])
    virt = []
    for rec in pool['songs']:
        if last and rec['id'] == last and len(pool['songs']) > 1:
            continue
        lu = rec.get('version_last_used') or {}
        for sp in versions(rec):
            v = dict(rec, id=version_id(rec['id'], sp), song=f"{rec['song']} {VERSION_SEP}{sp:g}", speed=sp,
                     last_used=lu.get(f'{sp:g}'), base_id=rec['id'])
            virt.append(v)
    fd, tmp = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    try:
        MP.save_pool(dict(pool, songs=virt), tmp)
        s = MP.pick_song(hist, pool_path=tmp, fetch=False, rng=rng, when=when)
    finally:
        os.remove(tmp)
    base, sp = split_id(s['id'])
    rec = next(x for x in pool['songs'] if x['id'] == base)
    path = MP.fetch_audio(rec) if fetch else None
    s.update(id=base, version_id=s['id'], song=rec['song'], speed=sp, section_hero=rec['section'].get('hero'),
             audio_path=path or s.get('audio_path'), audio_path_or_query=path or s.get('audio_path_or_query'))
    return s


def mark_used(song_id, when=None, pool_path=None):
    """song_id may carry the version ('id@v0.85'): the song's last_used AND that version's"""
    base, sp = split_id(song_id)
    p = load_pool(pool_path)
    for r in p['songs']:
        if r['id'] == base:
            r['last_used'] = MP.iso(when)
            r['uses'] = r.get('uses', 0) + 1
            if sp is not None:
                r.setdefault('version_last_used', {})[f'{sp:g}'] = MP.iso(when)
    save_pool(p, pool_path)


def record_performance(song_id, lscore, pool_path=None):
    MP.record_performance(split_id(song_id)[0], lscore, pool_path or POOL)


def retire_underperformers(pool, channel_mean):
    return MP.retire_underperformers(pool, channel_mean)


# ------------------------------------------------------------------ candidates + monthly top-up
# owner 2026-10-20: slow-mo is a WOMEN BEAUTY edit -> romantic / dreamy / pretty songs that trend in beauty edits only;
# dark / sad / breakup / grief / ironic songs never (20 dropped then, kept in pool['rejected'] so a top-up never re-adds them)
SEEDS = [  # famous romantic / dreamy songs used in anime beauty edits, played regular and slowed (curated; the gates still decide)
    ('blue', 'yung kai'), ('Golden Hour', 'JVKE'), ('Ditto', 'NewJeans'), ('Shinunoga E-Wa', 'Fujii Kaze'), ('Sparkle', 'RADWIMPS'),
    ('Until I Found You', 'Stephen Sanchez'), ('Die For You', 'The Weeknd'), ('Sweater Weather', 'The Neighbourhood'),
    ('Apocalypse', 'Cigarettes After Sex'), ('Sunsetz', 'Cigarettes After Sex'), ('Night Changes', 'One Direction'),
    ('Perfect', 'Ed Sheeran'), ('Photograph', 'Ed Sheeran'), ('BIRDS OF A FEATHER', 'Billie Eilish'), ('Here With Me', 'd4vd'),
    ('Yellow', 'Coldplay'), ('Those Eyes', 'New West'), ('Kataomoi', 'Aimer'), ('First Love', 'Hikaru Utada'),
    ('Nandemonaiya', 'RADWIMPS'), ('Love wins all', 'IU'), ('Cupid', 'FIFTY FIFTY'), ('Double Take', 'dhruv'),
    ('Sailor Song', 'Gigi Perez'), ('Dandelions', 'Ruth B.'), ('Somewhere Only We Know', 'Keane'),
    # 2026-10-20 additions (beauty-edit staples)
    ('Make You Mine', 'Public'), ('I Wanna Be Yours', 'Arctic Monkeys'), ('Lover', 'Taylor Swift'), ('Enchanted', 'Taylor Swift'),
    ('Moonlight', 'Kali Uchis'), ('Kiss Me', 'Sixpence None the Richer'), ('Bloom', 'The Paper Kites'), ('Pink + White', 'Frank Ocean'),
    ('Super Shy', 'NewJeans'), ('Love Lee', 'AKMU'), ('Marigold', 'Aimyon'), ('Koi', 'Gen Hoshino'), ('Suzume', 'RADWIMPS'),
]

# never a beauty edit: a title / LLM verdict that reads dark, sad, violent or ironic (a cheap first filter; the LLM fit check decides)
UNFIT_RE = re.compile(r'\b(dark|death|dead|die(?!\s+for\s+you)|kill|homicide|murder|ghost|grave|funeral|cry(ing)?|tears|sad|'
                      r'lonely|alone|goodbye|broken|heartbreak|hate|hell|devil|demon|blood|war|suicide|older|lemon)\b', re.I)
FIT_FOCUS = ('romantic, dreamy, pretty or sweet songs that trend in "beauty edits" (slow-motion edits of beautiful women / '
             'anime girls) on TikTok / Shorts, any language')


def beauty_fit(cands, ask=MP.llm_ask):
    """owner 2026-10-20: slow-mo is a women beauty edit. Seeds are curated; LLM proposals pass the title filter and an LLM
    verdict (fit only when romantic / dreamy / pretty; not sad, breakup, grief, dark, violent or ironic). No LLM = no proposals."""
    out = [c for c in cands if c.get('source') == 'seed']
    other = [c for c in cands if c.get('source') != 'seed' and not UNFIT_RE.search(c['song'])]
    if not other:
        return out
    try:
        lst = '; '.join(f"{c['song']} - {c['artist']}" for c in other)
        txt = ask('For each song decide if it fits a WOMEN BEAUTY edit (slow-motion clips of beautiful women / anime girls): fit = '
                  'romantic, dreamy, pretty, sweet, admiring or in love; NOT fit = sad, breakup, heartbreak, grief, lonely, dark, '
                  'violent, creepy, ironic or joke songs, or songs about a man. Use the overall meaning of the song (do not quote '
                  f'lyrics). Songs: {lst}. Reply JSON only: {{"songs":[{{"song":"title","artist":"artist","fit":true}}]}}')
        m = re.search(r'\{.*\}', txt or '', re.S)
        ver = {MP.norm(x.get('song', '')): bool(x.get('fit')) for x in (json.loads(m.group(0)).get('songs') or [])} if m else {}
    except Exception as e:  # noqa: BLE001
        log('beauty-fit check failed, proposals skipped:', repr(e)[:160])
        return out
    for c in other:
        if ver.get(MP.norm(c['song'])):
            out.append(c)
        else:
            log(f"not a beauty-edit song (skipped): {c['artist']} - {c['song']}")
    return out


def propose(n, have, ask=MP.llm_ask):
    out = []
    for focus in (FIT_FOCUS,
                  'romantic / dreamy pop and indie love songs that are famous as "slowed + reverb" versions AND as sped / regular '
                  'versions in aesthetic beauty edits',
                  'romantic or dreamy anime, J-pop, K-pop and C-pop songs used in anime girl beauty edits on Shorts / TikTok',
                  'songs CURRENTLY trending (this month) in TikTok / Shorts beauty edits and "pretty girl" aesthetic edits'):
        txt = ask(f'List {n // 4 + 2} famous songs for {focus}. Slow or mid tempo, sung (not instrumental), romantic / dreamy / '
                  'pretty, instantly recognisable. NOT sad, breakup, grief, dark or ironic songs. '
                  f'Do not repeat these: {"; ".join(sorted(have)[:120])}. '
                  'Reply JSON only: {"songs":[{"song":"title","artist":"main artist"}]}. No lyrics.')
        out += [{**x, 'source': 'llm'} for x in MP.parse_songs(txt)]
    return out


def topup(dry=False, seed_only=False, limit=None, pool_path=None, search=MP.yt_search, ask=MP.llm_ask, **fns):
    pool = load_pool(pool_path)
    month = MP.now().strftime('%Y-%m')
    added_m = sum(1 for s in pool['songs'] if (s.get('added') or '').startswith(month) and s.get('source') != 'seed')
    cap = limit if limit is not None else (999 if seed_only else MAX_PER_MONTH - added_m)
    have = [s['song'] for s in pool['songs']] + [s['song'] for s in pool['rejected']]
    cands = [{'song': a, 'artist': b, 'source': 'seed'} for a, b in SEEDS]
    if not seed_only:
        cands = propose(24, have, ask) + cands
    cands = beauty_fit(cands, ask)
    seen, uniq = set(), []
    for c in cands:
        if MP.norm(c['song']) not in seen:
            seen.add(MP.norm(c['song']))
            uniq.append(c)
    report, n_added = [], 0
    for c in uniq:
        if n_added >= cap:
            break
        try:
            st, why = add_candidate(pool, c['song'], c['artist'], c.get('source', 'seed'), search, **fns)
        except Exception as e:  # noqa: BLE001
            st, why = 'error', f'{type(e).__name__}: {e}'[:160]
        log(f"{st:8s} {c['artist']} - {c['song']}: {why}")
        if st != 'dup':
            report.append({'song': c['song'], 'artist': c['artist'], 'source': c.get('source'), 'status': st, 'why': why})
        n_added += st == 'added'
        if not dry and st in ('added', 'rejected'):
            save_pool(pool, pool_path)
    if not dry:
        save_pool(pool, pool_path)
        os.makedirs(MP.MEMDIR, exist_ok=True)
        with open(os.path.join(MP.MEMDIR, f'slowmo-music-topup-{month}.json'), 'w', encoding='utf-8') as f:
            json.dump({'at': MP.iso(), 'added': n_added, 'verdicts': report}, f, indent=1, ensure_ascii=False)
    log(f'added {n_added}, pool {len(pool["songs"])}')
    return {'added': n_added, 'report': report}


# ------------------------------------------------------------------ selftest (synthetic, no network)
def _synth(bpm=80, seed=0, drums=False):
    rng = np.random.RandomState(seed)
    beat = 60 / bpm
    dur = 150.0
    t = np.arange(int(dur * SR)) / SR
    y = np.zeros_like(t, dtype=np.float32)
    chords = [220, 196, 174.6, 196]
    for k in range(int(dur / (4 * beat))):
        a, b = int(k * 4 * beat * SR), int((k + 1) * 4 * beat * SR)
        f0 = chords[k % 4]
        lvl = 0.12 if (k * 4 * beat) < 50 else 0.35          # verse soft, chorus (from 50 s) loud
        tt = t[a:b] - t[a]
        y[a:b] += lvl * (np.sin(2 * np.pi * f0 * tt) + 0.5 * np.sin(2 * np.pi * f0 * 1.5 * tt) + 0.3 * np.sin(2 * np.pi * f0 * 2 * tt))
    if drums:
        for k in range(int(dur / beat * 2)):
            i = int(k * beat / 2 * SR)
            y[i:i + 800] += (rng.randn(800) * np.exp(-np.arange(800) / 120)).astype(np.float32) * 0.8
    return (y / (np.abs(y).max() + 1e-9) * 0.8).astype(np.float32)


def selftest():
    import tempfile
    a = analyze(y=_synth(80))
    b = analyze(y=_synth(128, drums=True))
    log('calm ballad', a['calm'], a['section'], '| busy', b['calm'])
    assert a['calm'] > b['calm'], 'a soft ballad must be calmer than a busy drum track'
    assert a['section'] and 44 <= a['section']['hero'] <= 60, f"the hero should sit on the chorus start (~50 s): {a['section']}"
    assert a['speed'] == 0.95 and speed_for(110) == 0.9 and speed_for(128) == 0.85
    # gates
    assert gate(a, 1e6, True).startswith('views')
    assert 'lyrics' in gate(a, 5e6, False)
    # pick / cooldown on a temp pool
    fd, pp = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    up = {'id': 'x' * 11, 'views': 5e7}
    pool = {'songs': [], 'rejected': []}
    for i in range(4):
        r = make_record(f'song {i}', 'artist', dict(up, id=f'{i}' * 11), 3, a, 'seed')
        r['status'] = 'active'
        pool['songs'].append(r)
    save_pool(pool, pp)
    rng = random.Random(1)
    s1 = pick([], rng=rng, pool_path=pp)
    mark_used(s1['version_id'], pool_path=pp)
    s2 = pick([{'id': s1['version_id'], 'at': MP.iso()}], rng=rng, pool_path=pp)
    assert s2['id'] != s1['id'] and s2['speed'] in (1.0, 0.95) and s2['section_hero'] is not None
    sp = [pick([], rng=random.Random(k), pool_path=pp)['speed'] for k in range(200)]
    assert 0.3 < sp.count(1.0) / len(sp) < 0.7, f'regular / slowed ~50 / 50: {sp.count(1.0)} of {len(sp)}'
    # the same song in its other version is allowed after the cooldown of THAT version only
    p0 = load_pool(pp)
    assert p0['songs'][[x['id'] for x in p0['songs']].index(s1['id'])]['version_last_used'], 'version use recorded'
    for _ in range(2):
        mark_used(s2['id'], pool_path=pp)
    record_performance(s1['id'], 8.0, pool_path=pp)
    p = load_pool(pp)
    assert not any(k in x for x in p['songs'] for k in ('lines', 'words', 'text', 'lrc')), 'no lyrics in the pool file'
    os.remove(pp)
    log('selftest ok')


def main(argv):
    cmd = argv[1] if len(argv) > 1 else 'selftest'
    if cmd == 'selftest':
        selftest()
    elif cmd == 'seed':
        topup(seed_only=True, limit=int(argv[2]) if len(argv) > 2 else None)
    elif cmd == 'topup':
        topup(dry='--dry-run' in argv)
    elif cmd == 'analyze':
        print(json.dumps(analyze(argv[2]), indent=1))
    elif cmd == 'pick':
        print(json.dumps(pick([]), indent=1, ensure_ascii=False))
    elif cmd == 'show':
        for s in sorted(load_pool()['songs'], key=lambda s: -s['total']):
            sec = s['section']
            print(f"{s['artist']} - {s['song']} | {s['tempo']} bpm x{s['speed']} | {sec['start']}-{sec['end']} s hero {sec['hero']} | "
                  f"calm {s['scores']['calm']} total {s['total']} | {s['views'] / 1e6:.0f}M | {s['status']}")
    else:
        print(__doc__)


if __name__ == '__main__':
    main(sys.argv)
