"""Music pool + song-section picker for the SOUND edit (Zero Yen Otaku).

Pool file: core/edit/assets/sound-music-pool.json (no lyrics, ever).  Audio is cached under output/music-pool/audio
(gitignored); on GitHub the audio is fetched at render time with fetch_audio().

  pick_song(history)  -> {song, artist, id, audio_path_or_query, section_start, section_end, tempo, ...}
  mark_used(id)       record a use (last_used = now) ; record_performance(id, lscore) feeds Thompson sampling
  analyze(path)       tempo, drum scores and the best 25-35 s section of a song
  topup()             monthly: candidates (LLM + trending + seed) -> gates -> probation -> retirement
  seed / selftest     python core/edit/music_pool.py seed|topup|selftest|show|analyze FILE

Scores (0..1): drum_clarity (HPSS percussive energy x onset peakedness), regularity (drum onsets on the 16th grid),
tempo_stability (segments agreeing with the global grid, period and phase), drum_share (onsets that are drum hits),
popularity (log views), trend (sound-edit / beat-sync uploads naming the song).
Section picker: windows start on bars; scored on drum-onset density, bar-to-bar regularity, energy, hook-ness
(chroma self-similarity: the bars that repeat most, at the loud end), a loudness boundary at the start, phrase alignment;
penalised for quiet breaks (a quiet run > 1 s is rejected).  See core/edit/refs/new-types-progress.md "Music pool".
"""
import datetime as dt
import glob
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
POOL = os.environ.get('MUSIC_POOL_FILE') or os.path.join(HERE, 'assets', 'sound-music-pool.json')
CACHE = os.environ.get('MUSIC_POOL_CACHE') or os.path.join(ROOT, 'output', 'music-pool', 'audio')
MEMDIR = os.environ.get('MEMORY_DIR') or os.path.join(ROOT, 'memory')
SR = 22050
HOP = 256
BPM_LO, BPM_HI = 85.0, 140.0
SEC_LO, SEC_HI, SEC_TARGET = 25.0, 35.0, 30.0
COOLDOWN_DAYS = float(os.environ.get('MUSIC_COOLDOWN_DAYS', 30))
MAX_PER_MONTH = int(os.environ.get('MUSIC_TOPUP_MAX', 8))
MIN_VIEWS = float(os.environ.get('MUSIC_MIN_VIEWS', 2e6))
MIN_AUDIO = float(os.environ.get('MUSIC_MIN_AUDIO', 0.5))
MIN_SECTION = float(os.environ.get('MUSIC_MIN_SECTION', 0.45))
RETIRE_AFTER, RETIRE_MARGIN = 4, 0.5
TREND_RE = re.compile(r'beat\s*sync|sound\s*edit|sfx\s*edit|hit\s*sync|sound\s*effect|beat\s*edit|sync(ed)?\s*to\s*the\s*beat|edit audio', re.I)


def log(*a):
    print('[music_pool]', *a, flush=True)


def norm(s):
    s = re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower().replace('&', ' and '))
    return ' '.join(re.sub(r'(official|audio|video|lyrics?|feat|ft|hd|mv|music|remastered)', ' ', s).split())


def now():
    return dt.datetime.now(dt.timezone.utc)


def iso(d=None):
    return (d or now()).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(s):
    try:
        return dt.datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


# ------------------------------------------------------------------ pool file
def load_pool(path=None):
    path = path or POOL
    try:
        with open(path, encoding='utf-8') as f:
            p = json.load(f)
    except Exception:
        p = {}
    p.setdefault('version', 1)
    p.setdefault('note', 'Music pool for the sound edit. No lyrics. Audio is fetched at render time; section = best 25-35 s whole-bar window.')
    p.setdefault('songs', [])
    p.setdefault('rejected', [])
    return p


def save_pool(p, path=None):
    path = path or POOL
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(p, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ------------------------------------------------------------------ audio + analysis
def load_audio(path, sr=SR):
    ff = shutil.which('ffmpeg') or 'ffmpeg'
    r = subprocess.run([ff, '-v', 'error', '-i', path, '-ac', '1', '-ar', str(sr), '-f', 'f32le', '-'], capture_output=True)
    y = np.frombuffer(r.stdout, dtype=np.float32)
    if len(y) < sr * 20:
        raise ValueError(f'cannot decode {path}')
    return y


def _peaks(env, thr, min_gap):
    idx = []
    for i in range(1, len(env) - 1):
        if env[i] >= thr and env[i] >= env[i - 1] and env[i] > env[i + 1] and (not idx or i - idx[-1] >= min_gap):
            idx.append(i)
    return np.array(idx, int)


def _grid_fit(env, t0, t1, bpm, lo=0.97, hi=1.03, steps=61):
    """(period, phase_time) constant grid on the percussive envelope between t0 and t1 (seconds)."""
    fs = SR / HOP
    a, b = int(t0 * fs), min(len(env), int(t1 * fs))
    e = env[a:b]
    if len(e) < 20:
        return 60 / bpm, t0, 0.0
    e = e / (e.max() + 1e-9)
    t = np.arange(len(e)) / fs
    span = len(e) / fs
    best = (-1.0, 60 / bpm, 0.0)
    for P in (60 / bpm) * np.linspace(lo, hi, steps):
        ph = np.arange(0, P, 0.004)
        k = np.arange(max(1, int(span / P) - 1))
        sc = np.interp(ph[:, None] + k[None] * P, t, e).mean(axis=1)
        i = int(np.argmax(sc))
        if sc[i] > best[0]:
            best = (float(sc[i]), float(P), float(ph[i]))
    return best[1], t0 + best[2], best[0]


def analyze(path=None, y=None, want_section=True):
    """-> dict(tempo, tempo_raw, bar_s, downbeat, duration, scores{...}, audio_score, section{start,end,bars,score,parts}, sections[top3])."""
    import librosa
    if y is None:
        y = load_audio(path)
    dur = len(y) / SR
    yh, yp = librosa.effects.hpss(y, margin=1.0)
    fs = SR / HOP
    env_p = librosa.onset.onset_strength(y=yp, sr=SR, hop_length=HOP)
    env_h = librosa.onset.onset_strength(y=yh, sr=SR, hop_length=HOP)
    env_f = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    n = len(env_p)
    ft = np.arange(n) / fs
    # tempo: librosa estimate and its octave relatives, scored by the grid on the percussive envelope, folded into 85-140
    t1 = float(np.atleast_1d(librosa.beat.tempo(onset_envelope=env_p, sr=SR, hop_length=HOP))[0]) or 110.0
    best, bpm = -1.0, t1
    for f in (1, 0.5, 2, 2 / 3, 1.5, 0.75, 4 / 3):
        c = t1 * f
        if not 60 <= c <= 200:
            continue
        sc = _grid_fit(env_p, 0, dur, c, 0.98, 1.02, 21)[2]
        pen = 1.0 if BPM_LO <= c <= BPM_HI else 0.85
        if sc * pen > best:
            best, bpm = sc * pen, c
    tempo_raw = bpm
    while bpm > BPM_HI and bpm / 2 >= BPM_LO - 3:
        bpm /= 2
    while bpm < BPM_LO and bpm * 2 <= BPM_HI + 3:
        bpm *= 2
    P, ph, gscore = _grid_fit(env_p, 0, dur, bpm)
    bpm_fit = 60 / P
    # --- drum onsets
    med = float(np.median(env_p)) + 1e-9
    pk = _peaks(env_p, max(med * 2.0, np.percentile(env_p, 75)), int(0.08 * fs))
    strong = pk[env_p[pk] >= np.percentile(env_p[pk], 40)] if len(pk) > 8 else pk
    st = strong / fs
    step = P / 4                                   # a 16th
    dev = np.abs(((st - ph + step / 2) % step) - step / 2)
    regularity = float(np.mean(dev <= 0.035)) if len(st) else 0.0
    # tempo stability: 10 s segments agree with the global grid in period and phase
    ok, tot = 0, 0
    for s0 in np.arange(0, max(1.0, dur - 10), 5.0):
        Ps, phs, sc = _grid_fit(env_p, s0, s0 + 10, bpm_fit, 0.985, 1.015, 31)
        if sc < 0.05:
            continue
        tot += 1
        perr = abs(((phs - ph + P / 2) % P) - P / 2)
        ok += int(abs(Ps / P - 1) < 0.015 and perr < 0.1 * P)
    stability = ok / tot if tot else 0.0
    # drum clarity: percussive energy share and how peaked the percussive envelope is
    ep, eh = float(np.mean(yp ** 2)), float(np.mean(yh ** 2))
    perc_ratio = ep / (ep + eh + 1e-12)
    peaked = float(np.clip((np.percentile(env_p, 98) / med - 4) / 14, 0, 1))
    clarity = float(0.5 * np.clip((perc_ratio - 0.1) / 0.35, 0, 1) + 0.5 * peaked)
    # share of the full-mix onsets that are drum hits
    fp = _peaks(env_f, max(float(np.median(env_f)) * 1.8, np.percentile(env_f, 70)), int(0.08 * fs))
    if len(fp):
        sp = env_p / (np.percentile(env_p, 95) + 1e-9)
        sh = env_h / (np.percentile(env_h, 95) + 1e-9)
        r = np.array([sp[max(0, i - 2):i + 3].max() / (sp[max(0, i - 2):i + 3].max() + sh[max(0, i - 2):i + 3].max() + 1e-9) for i in fp])
        drum_share = float(np.mean(r > 0.45))
    else:
        drum_share = 0.0
    scores = {'drum_clarity': clarity, 'regularity': regularity, 'tempo_stability': stability, 'drum_share': drum_share}
    audio = 0.35 * clarity + 0.25 * regularity + 0.2 * stability + 0.2 * drum_share
    out = {'tempo': round(bpm_fit, 2), 'tempo_raw': round(tempo_raw, 2), 'bar_s': round(4 * P, 4), 'duration': round(dur, 2),
           'scores': {k: round(v, 3) for k, v in scores.items()}, 'audio_score': round(float(audio), 3),
           'perc_ratio': round(perc_ratio, 3), 'drum_onsets_per_s': round(len(st) / dur, 2)}
    if want_section:
        feats = _section_features(y, yp, env_p, ph, P, dur, st)
        out['downbeat'] = round(feats['down'], 3)
        secs = pick_sections(feats)
        out['sections'] = secs[:3]
        if secs:
            out['section'] = secs[0]
    return out


def _bar_features(y, env_p, bars, P, st):
    import librosa
    n = len(bars) - 1
    fs = SR / HOP
    rms_t = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    rt = np.arange(len(rms_t)) * 512 / SR
    db = 20 * np.log10(rms_t + 1e-5)
    chroma = librosa.feature.chroma_stft(y=y, sr=SR, hop_length=512, n_fft=2048)
    return rt, db, chroma, fs, n


def _section_features(y, yp, env_p, ph, P, dur, st):
    """Per-bar tables for the picker.  The downbeat phase (which of the 4 beats starts the bar) comes from the kick band
    on beats 1 / 3 against the backbeat on 2 / 4."""
    import librosa
    fs = SR / HOP
    S = np.abs(librosa.stft(yp, n_fft=1024, hop_length=HOP)) ** 2
    fr = librosa.fft_frequencies(sr=SR, n_fft=1024)
    low = np.log1p(S[fr < 150].sum(0))
    low = np.maximum(0, np.diff(low, prepend=low[0]))
    mid = np.log1p(S[(fr > 1500) & (fr < 6000)].sum(0))
    mid = np.maximum(0, np.diff(mid, prepend=mid[0]))
    beats = ph + np.arange(int(-ph / (P) ) - 1, int((dur - ph) / P) + 1) * P
    beats = beats[(beats >= 0) & (beats < dur - 0.1)]
    bi = (beats * fs).astype(int)
    bi = bi[bi < len(low)]
    sc = []
    for k in range(4):
        a = bi[k::4]
        b = bi[(k + 2) % 4::4]
        sc.append(low[a].mean() + low[bi[(k + 2) % 4::4]].mean() * 0.8 - low[bi[(k + 1) % 4::4]].mean() * 0.4 + mid[bi[(k + 1) % 4::4]].mean() * 0.5 + mid[bi[(k + 3) % 4::4]].mean() * 0.5)
    k0 = int(np.argmax(sc))
    down = float(beats[k0]) if len(beats) > k0 else ph
    bars = down + np.arange(int(-down / (4 * P)) - 1, int((dur - down) / (4 * P)) + 1) * 4 * P
    bars = bars[bars >= -0.01]
    bars = bars[bars <= dur]
    rt, db, chroma, _, _ = _bar_features(y, env_p, bars, P, st)
    nb = len(bars) - 1
    t = np.arange(len(env_p)) / fs
    dens, reg, rms, chm = [], [], [], []
    per = max(8, int(round(4 * P * fs)))
    for i in range(nb):
        a, b = bars[i], bars[i + 1]
        dens.append(float(((st >= a) & (st < b)).sum() / (b - a)))
        e = np.interp(a + np.arange(per) / per * (b - a), t, env_p)
        reg.append(e)
        m = (rt >= a) & (rt < b)
        rms.append(float(db[m].mean()) if m.any() else -80.0)
        c = chroma[:, m].mean(axis=1) if m.any() else np.zeros(12)
        chm.append(c / (np.linalg.norm(c) + 1e-9))
    reg = np.array(reg)
    corr = np.zeros(nb)
    for i in range(nb - 1):
        a, b = reg[i], reg[i + 1]
        corr[i] = max(0.0, float(np.corrcoef(a, b)[0, 1])) if a.std() > 1e-6 and b.std() > 1e-6 else 0.0
    corr[nb - 1:] = corr[nb - 2] if nb > 1 else 0
    chm = np.array(chm)
    sim = chm @ chm.T if nb else np.zeros((0, 0))
    # hook-ness: how many bars far away (>= 4 bars) look like this one, weighted to loud bars (choruses repeat and are loud)
    rep = np.zeros(nb)
    for i in range(nb):
        far = np.abs(np.arange(nb) - i) >= 4
        rep[i] = float((sim[i][far] > 0.93).mean()) if far.any() else 0.0
    return {'bars': bars, 'dens': np.array(dens), 'corr': corr, 'rms': np.array(rms), 'rep': rep, 'P': P, 'down': down,
            'rt': rt, 'db': db, 'dur': dur, 'st': st, 'env': env_p, 'fs': fs}


def _sec_bars(P):
    """Bar counts whose length is in 25-35 s: the one nearest 30 s, preferring multiples of 4 (a phrase), then 2."""
    bar = 4 * P
    opts = [n for n in range(2, 40) if SEC_LO <= n * bar <= SEC_HI]
    if not opts:
        opts = [max(2, int(round(SEC_TARGET / bar)))]
    return sorted(opts, key=lambda n: abs(n * bar - SEC_TARGET) + (0 if n % 4 == 0 else 1.2 if n % 2 == 0 else 2.5))[:2]


def _score_windows(F, ncount):
    bars, P = F['bars'], F['P']
    nb = len(bars) - 1
    p95 = np.percentile(F['rms'], 95) if nb else -20
    rows = []
    if nb < ncount + 1:
        return rows
    rms_rank = np.argsort(np.argsort(F['rms'])) / max(1, nb - 1)
    chorus = F['rep'] * (0.5 + 0.5 * rms_rank)           # repeated and loud
    c_hi = np.percentile(chorus, 90) + 1e-9
    L = ncount * 4 * P
    for i in range(0, nb - ncount):
        a, b = bars[i], bars[i + ncount]
        if b > F['dur'] - 1.0:
            break
        d = F['dens'][i:i + ncount]
        density = float(np.mean(np.clip(1 - np.abs(d - 3.6) / 3.0, 0, 1)))        # drum onsets / s: 3-4 is the sweet spot
        regu = float(np.mean(F['corr'][i:i + ncount]))
        energy = float(np.clip((F['rms'][i:i + ncount].mean() - p95 + 10) / 10, 0, 1))
        hook = float(np.clip(chorus[i:i + ncount].mean() / c_hi, 0, 1))
        pre = F['rms'][max(0, i - 2):i].mean() if i > 0 else None
        post = F['rms'][i:i + 2].mean()
        bnd = float(np.clip((post - pre) / 6.0, 0, 1)) if pre is not None else 0.4
        phrase = 1.0 if i % 8 == 0 else 0.7 if i % 4 == 0 else 0.0
        # quiet breaks: 1 s frames well below the window's loud level, and the longest run
        m = (F['rt'] >= a) & (F['rt'] < b)
        db = F['db'][m]
        if len(db) < 10:
            continue
        sm = np.convolve(db, np.ones(43) / 43, mode='same')              # ~1 s at hop 512
        lvl = np.percentile(db, 85)
        quiet = sm < lvl - 14
        run = best_run = 0
        for q in quiet:
            run = run + 1 if q else 0
            best_run = max(best_run, run)
        longest = best_run * 512 / SR
        brk = float(quiet.mean())
        # a drum-less stretch: bars with < 1 drum onset / s
        dead = float((d < 1.0).mean())
        pen = 0.8 * brk + 0.5 * dead + (1.0 if longest > 1.0 else 0.0)
        # the sound edit needs a clean drum pattern all the way through
        sc = 0.22 * density + 0.18 * regu + 0.14 * energy + 0.24 * hook + 0.10 * bnd + 0.06 * phrase + 0.06 * (1 - dead) - pen
        rows.append({'i': i, 'start': float(a), 'end': float(b), 'bars': ncount, 'score': float(sc), 'density': density,
                     'regularity': regu, 'energy': energy, 'hook': hook, 'boundary': bnd, 'dead_bars': dead, 'break_s': longest,
                     'drums_per_s': float(d.mean())})
    return rows


def pick_sections(F, top=3):
    """Best windows (whole bars from a bar start, 25-35 s), best first, non-overlapping."""
    rows = []
    for n in _sec_bars(F['P']):
        rows += _score_windows(F, n)
    rows.sort(key=lambda r: -r['score'])
    out = []
    for r in rows:
        if all(r['end'] <= o['start'] or r['start'] >= o['end'] for o in out):
            out.append({'start': round(r['start'], 3), 'end': round(r['end'], 3), 'bars': r['bars'], 'score': round(r['score'], 3),
                        'parts': {k: round(r[k], 3) for k in ('density', 'regularity', 'energy', 'hook', 'boundary', 'dead_bars', 'break_s', 'drums_per_s')}})
        if len(out) >= top:
            break
    return out


def window_report(F, start, length):
    """The features of an arbitrary window (reference study): rank of its score among all bar-aligned windows of that length."""
    bars = F['bars']
    n = max(2, int(round(length / (4 * F['P']))))
    rows = _score_windows(F, n)
    if not rows:
        return None
    i = int(np.argmin(np.abs(np.array([r['start'] for r in rows]) - start)))
    r = rows[i]
    rank = 1 + sum(1 for x in rows if x['score'] > r['score'])
    off = ((start - F['down']) / (4 * F['P']))
    return {'start_bar_offset': round(off - round(off), 2), 'bar_index': int(round(off)), 'rank': f'{rank}/{len(rows)}',
            **{k: round(r[k], 2) for k in ('score', 'density', 'regularity', 'energy', 'hook', 'boundary', 'drums_per_s', 'break_s')}}


# ------------------------------------------------------------------ YouTube (yt-dlp, audio only)
def _cookie_copy():
    src = os.environ.get('YT_COOKIES') or os.environ.get('MUSIC_COOKIES')
    if src and os.path.exists(src):
        fd, p = tempfile.mkstemp(suffix='.txt')
        os.close(fd)
        shutil.copy(src, p)
        return p
    return None


def _ytdlp(args, timeout=240):
    jar = _cookie_copy()
    cmd = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-warnings', '--force-ipv4', '--retries', '2',
           '--js-runtimes', os.environ.get('YT_JS_RUNTIMES', 'node')]
    if jar:
        cmd += ['--cookies', jar]
    try:
        r = subprocess.run(cmd + args, capture_output=True, text=True, timeout=timeout, encoding='utf-8', errors='replace')
        time.sleep(float(os.environ.get('MUSIC_PACE', 4)) + random.random() * 2)
        if r.returncode and not r.stdout.strip():
            log('yt-dlp:', (r.stderr or '').strip().splitlines()[-1][:160] if r.stderr else r.returncode)
        return r.stdout
    except Exception as e:
        log('yt-dlp failed:', str(e)[:120])
        return ''
    finally:
        if jar:
            try:
                os.remove(jar)
            except OSError:
                pass


def yt_search(q, n=8):
    out = _ytdlp(['--flat-playlist', '--skip-download', '--print', '%(id)s\t%(view_count)s\t%(duration)s\t%(title)s\t%(channel)s', f'ytsearch{n}:{q}'])
    rows = []
    for l in out.splitlines():
        p = l.split('\t')
        if len(p) >= 4 and len(p[0]) == 11:
            v = p[1] if p[1] not in ('NA', '') else '0'
            d = p[2] if p[2] not in ('NA', '') else '0'
            rows.append({'id': p[0], 'views': int(float(v)), 'duration': float(d), 'title': p[3], 'channel': p[4] if len(p) > 4 else ''})
    return rows


def find_official(song, artist, search=yt_search):
    """Best upload of the song's audio: title has the song and the artist, 60-420 s, official / Topic / VEVO first, most views.
    views = the max views among matching uploads (the official video usually has more than the audio upload)."""
    ns, na = norm(song), norm(artist)
    rows = search(f'{artist} {song} official audio', 8)
    good = []
    for r in rows:
        t = norm(r['title'] + ' ' + r['channel'])
        if ns not in t or not (60 <= r['duration'] <= 420):
            continue
        if na.split()[0] not in t:
            continue
        if re.search(r'cover|karaoke|slowed|sped|nightcore|8d|remix|live|reaction|instrumental|tutorial|edit', r['title'], re.I):
            continue
        r['rank'] = (2 if re.search(r'topic|vevo|official|records|music', r['channel'], re.I) else 0) + (1 if 'audio' in r['title'].lower() else 0)
        good.append(r)
    if not good:
        return None
    best = sorted(good, key=lambda r: (-r['rank'], -r['views']))[0]
    best['views'] = max(r['views'] for r in good)
    return best


def trend_count(song, artist, search=yt_search):
    ns = norm(song)
    rows = search(f'{song} {artist} beat sync sound edit', 15)
    return sum(1 for r in rows if ns in norm(r['title']) and TREND_RE.search(r['title']))


def fetch_audio(rec, search=None):
    """Local path of the song's audio (cached), downloaded with yt-dlp if missing; None if it cannot be fetched."""
    os.makedirs(CACHE, exist_ok=True)
    vid = rec.get('video_id')
    if vid:
        have = [f for f in glob.glob(os.path.join(CACHE, vid + '.*')) if not f.endswith('.part')]
        if have:
            return have[0]
        _ytdlp(['--no-playlist', '-f', 'bestaudio[ext=m4a]/bestaudio', '-o', os.path.join(CACHE, vid + '.%(ext)s'),
                f'https://www.youtube.com/watch?v={vid}'], timeout=420)
        have = [f for f in glob.glob(os.path.join(CACHE, vid + '.*')) if not f.endswith('.part')]
        if have:
            return have[0]
    return None


# ------------------------------------------------------------------ scoring + records
def pop_score(views):
    return float(np.clip((math.log10(max(views, 1)) - 5.5) / 2.5, 0, 1))


def total_score(audio, views, trend):
    return round(0.65 * audio + 0.20 * pop_score(views) + 0.15 * float(np.clip(trend / 6, 0, 1)), 3)


def make_record(song, artist, up, trend, an, source, status='probation'):
    sec = an.get('section') or {}
    return {
        'id': re.sub(r'[^a-z0-9]+', '_', f'{artist}_{song}'.lower()).strip('_')[:60],
        'song': song, 'artist': artist, 'video_id': up['id'], 'url': f"https://www.youtube.com/watch?v={up['id']}",
        'query': f'{artist} {song} official audio', 'views': up['views'], 'trend_uploads': trend, 'source': source,
        'tempo': an['tempo'], 'bar_s': an['bar_s'], 'downbeat': an.get('downbeat'), 'duration': an['duration'],
        'section': {'start': sec.get('start'), 'end': sec.get('end'), 'bars': sec.get('bars'), 'score': sec.get('score'),
                    'parts': sec.get('parts'), 'alternatives': [[s['start'], s['end'], s['score']] for s in an.get('sections', [])[1:]]},
        'scores': {**an['scores'], 'audio': an['audio_score'], 'popularity': round(pop_score(up['views']), 3),
                   'trend': round(float(np.clip(trend / 6, 0, 1)), 3), 'section': sec.get('score')},
        'total': total_score(an['audio_score'], up['views'], trend),
        'status': status, 'added': iso(), 'last_used': None, 'uses': 0, 'perf': {'n': 0, 'sum': 0.0},
    }


def gate(an, views, min_views=None):
    """-> reason string if rejected, else None."""
    if not (BPM_LO - 3 <= an['tempo'] <= BPM_HI + 3):
        return f"tempo {an['tempo']} outside {BPM_LO:.0f}-{BPM_HI:.0f}"
    if an['audio_score'] < MIN_AUDIO:
        return f"drums not clear enough (audio {an['audio_score']})"
    sec = an.get('section')
    if not sec or sec['score'] < MIN_SECTION:
        return f"no good 25-35 s section (best {sec['score'] if sec else None})"
    if sec['parts']['break_s'] > 1.0:
        return 'best section has a long quiet break'
    if views < (MIN_VIEWS if min_views is None else min_views):
        return f'views {views} < {MIN_VIEWS:.0f}'
    return None


def add_candidate(pool, song, artist, source, search=yt_search, analyze_fn=None, fetch_fn=None, trend_fn=None, log_rows=None):
    """Run all gates for one candidate and add it on probation.  Returns (status, reason)."""
    key = norm(song)
    for s in pool['songs']:
        if norm(s['song']) == key:
            return 'dup', 'already in the pool'
    for s in pool['rejected']:
        if norm(s['song']) == key:
            return 'dup', 'rejected before: ' + s.get('why', '')
    up = find_official(song, artist, search)
    if not up:
        return 'rejected', 'no matching official upload'
    if up['views'] < MIN_VIEWS:
        pool['rejected'].append({'song': song, 'artist': artist, 'at': iso(), 'why': f"views {up['views']}"})
        return 'rejected', f"views {up['views']} < {MIN_VIEWS:.0f}"
    tr = (trend_fn or (lambda s, a: trend_count(s, a, search)))(song, artist)
    path = (fetch_fn or fetch_audio)({'video_id': up['id']})
    if not path:
        return 'error', 'audio download failed'
    an = (analyze_fn or analyze)(path)
    why = gate(an, up['views'])
    if why:
        pool['rejected'].append({'song': song, 'artist': artist, 'at': iso(), 'why': why})
        return 'rejected', why
    rec = make_record(song, artist, up, tr, an, source)
    if os.environ.get('MUSIC_KEEP_AUDIO', '1') == '0':
        try:
            os.remove(path)
        except OSError:
            pass
    pool['songs'].append(rec)
    return 'added', f"audio {an['audio_score']} section {rec['section']['start']}-{rec['section']['end']} total {rec['total']}"


# ------------------------------------------------------------------ pick_song
def _recent(history):
    """[(id, datetime|None)] oldest first from a history of ids / dicts({song|id, at|date|last_used})."""
    out = []
    for h in history or []:
        if isinstance(h, str):
            out.append((norm(h), None))
        elif isinstance(h, dict):
            k = h.get('id') or h.get('song') or h.get('title') or ''
            d = h.get('at') or h.get('date') or h.get('last_used')
            dd = None
            if isinstance(d, str):
                dd = parse_iso(d) or parse_iso(d[:10] + 'T00:00:00Z') if len(d) >= 10 else None
            out.append((norm(k), dd))
    return out


def _sample(s, rng):
    perf = s.get('perf') or {}
    n = perf.get('n', 0)
    prior = float(s.get('total', 0.5))
    mean = (perf['sum'] / n) if n else prior
    sd = 0.25 / math.sqrt(n + 1)
    v = rng.gauss(0.5 * prior + 0.5 * mean if n else prior, sd)
    return v - (0.15 if s.get('status') == 'probation' and n < 2 else 0.0)


def pick_song(history=None, pool_path=None, fetch=False, rng=None, when=None):
    """The song for the next sound edit.  Never the previous song (history[-1], or the pool's most recent last_used), none used
    within COOLDOWN_DAYS (pool last_used and dated history entries), Thompson sampling on quality + performance, probation -0.15.
    Does not write the pool (call mark_used(id) when the edit is made).  fetch=True downloads the audio now (GitHub render time).
    -> {id, song, artist, audio_path_or_query, audio_path, query, url, section_start, section_end, tempo, bar_s, downbeat, status}"""
    rng = rng or random.Random()
    pool = load_pool(pool_path)
    t_now = when or now()
    live = [s for s in pool['songs'] if s.get('status') in ('active', 'probation') and (s.get('section') or {}).get('start') is not None]
    if not live:
        raise RuntimeError('music pool is empty (run: python core/edit/music_pool.py seed)')
    rec = _recent(history)
    last_id = rec[-1][0] if rec else ''
    if not last_id:
        used = [(parse_iso(s['last_used']), s) for s in live if s.get('last_used')]
        last_id = norm(max(used, key=lambda x: x[0])[1]['song']) if used else ''
    cutoff = t_now - dt.timedelta(days=COOLDOWN_DAYS)
    recent_dated = {k for k, d in rec if d and d >= cutoff}
    recent_undated = {k for k, d in rec[-12:] if not d}          # an undated history counts as the last 12 picks

    def blocked(s):
        ks = {norm(s['song']), norm(s['id']), norm(f"{s['artist']} {s['song']}")}
        if ks & {last_id}:
            return True
        if ks & (recent_dated | recent_undated):
            return True
        lu = parse_iso(s['last_used']) if s.get('last_used') else None
        return bool(lu and lu >= cutoff)

    pool_ok = [s for s in live if not blocked(s)]
    if not pool_ok:                                              # cooldown exhausted: the least recently used, never the last one
        rest = [s for s in live if norm(s['song']) != last_id and norm(s['id']) != last_id] or live
        pool_ok = sorted(rest, key=lambda s: s.get('last_used') or '')[:max(1, len(rest) // 3)]
    s = max(pool_ok, key=lambda x: _sample(x, rng))
    path = fetch_audio(s) if fetch else None
    if not path and s.get('video_id'):
        have = glob.glob(os.path.join(CACHE, s['video_id'] + '.*'))
        path = next((f for f in have if not f.endswith('.part')), None)
    sec = s['section']
    return {'id': s['id'], 'song': s['song'], 'artist': s['artist'], 'audio_path_or_query': path or s['query'], 'audio_path': path,
            'query': s['query'], 'url': s.get('url'), 'video_id': s.get('video_id'), 'section_start': sec['start'], 'section_end': sec['end'],
            'tempo': s['tempo'], 'bar_s': s['bar_s'], 'downbeat': s.get('downbeat'), 'status': s['status'], 'total': s['total']}


def mark_used(song_id, when=None, pool_path=None):
    p = load_pool(pool_path)
    for s in p['songs']:
        if s['id'] == song_id or norm(s['song']) == norm(song_id):
            s['last_used'] = iso(when)
            s['uses'] = s.get('uses', 0) + 1
    save_pool(p, pool_path)


def record_performance(song_id, lscore, pool_path=None):
    p = load_pool(pool_path)
    for s in p['songs']:
        if s['id'] == song_id:
            s.setdefault('perf', {'n': 0, 'sum': 0.0})
            s['perf']['n'] += 1
            s['perf']['sum'] += float(lscore)
            if s['status'] == 'probation' and s['perf']['n'] >= 2:
                s['status'] = 'active'
    save_pool(p, pool_path)


def retire_underperformers(pool, channel_mean):
    n = 0
    for s in pool['songs']:
        pf = s.get('perf') or {}
        if s['status'] in ('active', 'probation') and s.get('source') != 'curated' and pf.get('n', 0) >= RETIRE_AFTER \
                and pf['sum'] / pf['n'] < channel_mean - RETIRE_MARGIN:
            s['status'] = 'retired'
            n += 1
    return n


# ------------------------------------------------------------------ candidates + monthly top-up
SEEDS = [  # (song, artist) famous songs with a clear drum pattern, 85-140 BPM (curated seed list; the gates still decide)
    ('Shape of You', 'Ed Sheeran'), ('Fake', 'The Tech Thieves'), ('HandClap', 'Fitz and The Tantrums'), ('Wellerman', 'Nathan Evans'),
    ('Time Back', 'Bad Style'), ('KICK BACK', 'Kenshi Yonezu'), ('Bling-Bang-Bang-Born', 'Creepy Nuts'), ('Whiplash', 'aespa'),
    ('Shut Down', 'BLACKPINK'), ('ANTIFRAGILE', 'LE SSERAFIM'), ('Uptown Funk', 'Mark Ronson'), ('Smooth Criminal', 'Michael Jackson'),
    ('Billie Jean', 'Michael Jackson'), ('bad guy', 'Billie Eilish'), ('Believer', 'Imagine Dragons'), ('Seven Nation Army', 'The White Stripes'),
    ('Another One Bites the Dust', 'Queen'), ('Dance Monkey', 'Tones and I'), ('Gangnam Style', 'PSY'), ("Don't Start Now", 'Dua Lipa'),
    ('Levitating', 'Dua Lipa'), ('Blinding Lights', 'The Weeknd'), ('Stronger', 'Kanye West'), ('Lose Yourself', 'Eminem'),
    ('Sunflower', 'Post Malone'), ('Old Town Road', 'Lil Nas X'), ('Crazy in Love', 'Beyonce'), ('Lean On', 'Major Lazer'),
    ('Despacito', 'Luis Fonsi'), ('Havana', 'Camila Cabello'), ('Poker Face', 'Lady Gaga'), ('Bad Romance', 'Lady Gaga'),
    ('Radioactive', 'Imagine Dragons'), ('Thunderstruck', 'AC/DC'), ('Superstition', 'Stevie Wonder'), ('Closer', 'The Chainsmokers'),
    ('Titanium', 'David Guetta'), ('Toxic', 'Britney Spears'), ('Can\'t Hold Us', 'Macklemore'), ('Mockingbird', 'Eminem'),
    ('Sicko Mode', 'Travis Scott'), ('Stay', 'The Kid LAROI'), ('Heat Waves', 'Glass Animals'), ('Montero', 'Lil Nas X'),
    ('Pump It', 'Black Eyed Peas'), ('Rockstar', 'Post Malone'), ('Humble', 'Kendrick Lamar'), ('Cheap Thrills', 'Sia'),
]


def _http_json(url, body, headers, timeout=60):
    req = urllib.request.Request(url, json.dumps(body).encode(), {'Content-Type': 'application/json', **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def llm_ask(prompt):
    """Gemini JSON (GEMINI_API_KEY, _2.._8), then OpenRouter; raw text or ''."""
    keys = [os.environ.get('GEMINI_API_KEY')] + [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(2, 9)]
    for k in [k for k in keys if k]:
        try:
            m = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
            j = _http_json(f'https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={k}',
                           {'contents': [{'parts': [{'text': prompt}]}], 'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0.9}}, {})
            return j['candidates'][0]['content']['parts'][0]['text']
        except Exception as e:
            log('gemini failed:', type(e).__name__)
    for k in [os.environ.get('OPENROUTER_API_KEY'), os.environ.get('OPENROUTER_API_KEY_2')]:
        if k:
            try:
                j = _http_json('https://openrouter.ai/api/v1/chat/completions', {'model': os.environ.get('OPENROUTER_MODEL', 'meta-llama/llama-3.3-70b-instruct:free'),
                                                                                'messages': [{'role': 'user', 'content': prompt}]}, {'Authorization': 'Bearer ' + k})
                return j['choices'][0]['message']['content']
            except Exception as e:
                log('openrouter failed:', type(e).__name__)
    return ''


def parse_songs(text):
    if not text:
        return []
    s = re.sub(r'```(json)?', '', text)
    for m in (re.search(r'\{[\s\S]*\}', s), re.search(r'\[[\s\S]*\]', s)):
        if m:
            try:
                j = json.loads(re.sub(r',\s*([}\]])', r'\1', m.group(0)))
                a = j if isinstance(j, list) else j.get('songs')
                if isinstance(a, list):
                    return [x for x in a if isinstance(x, dict) and x.get('song') and x.get('artist')]
            except Exception:
                pass
    return [{'song': a, 'artist': b} for a, b in re.findall(r'"song"\s*:\s*"([^"]+)"\s*,\s*"artist"\s*:\s*"([^"]+)"', s)]


def propose(n, have, ask=llm_ask):
    """Candidates from the LLM: famous songs good for beat-sync sound edits."""
    out = []
    for focus in ('hip-hop, pop and EDM hits with a heavy kick and snare from the last 15 years',
                  'songs that are currently trending in sound-edit / beat-sync / anime-edit Shorts and TikTok (any language)',
                  'classic rock, funk and disco drum-driven hits everybody recognises'):
        txt = ask(f'List {n // 3 + 2} famous songs for {focus}. Needs a punchy clear drum pattern (kick / snare / hats), steady tempo 85-140 BPM, '
                  f'instantly recognisable, popular in sound-edit and beat-sync videos. Do not repeat these: {"; ".join(sorted(have)[:60])}. '
                  'Reply JSON only: {"songs":[{"song":"title","artist":"main artist"}]}. No lyrics.')
        out += [{**x, 'source': 'llm'} for x in parse_songs(txt)]
    return out


def trending(search=yt_search):
    """Songs named in recent sound-edit / beat-sync upload titles (via the LLM extractor, if keys exist)."""
    titles = []
    for q in ('sound edit beat sync anime', 'anime sound effect edit song', 'beat sync edit shorts'):
        titles += [r['title'] for r in search(q, 12)]
    if not titles:
        return []
    txt = llm_ask('These are YouTube titles of sound-edit videos. List the commercial songs they use (only if the title names the song and artist or '
                  'the song is obvious). Reply JSON only: {"songs":[{"song":"","artist":""}]}. Titles: ' + ' | '.join(titles)[:3000])
    return [{**x, 'source': 'trending'} for x in parse_songs(txt)]


def topup(dry=False, seed_only=False, limit=None, pool_path=None, search=yt_search, ask=llm_ask, analyze_fn=None, fetch_fn=None, trend_fn=None, channel_mean=None):
    """Monthly: candidates (LLM + trending + seed) -> gates -> probation; retire losers; report to memory/sound-music-topup-YYYY-MM.json."""
    pool = load_pool(pool_path)
    month = now().strftime('%Y-%m')
    added_this_month = sum(1 for s in pool['songs'] if (s.get('added') or '').startswith(month) and s.get('source') != 'seed')
    cap = limit if limit is not None else (999 if seed_only else MAX_PER_MONTH - added_this_month)
    have = [s['song'] for s in pool['songs']] + [s['song'] for s in pool['rejected']]
    cands = []
    if seed_only:
        cands = [{'song': a, 'artist': b, 'source': 'seed'} for a, b in SEEDS]
    else:
        cands += propose(24, have, ask)
        try:
            cands += trending(search)
        except Exception as e:
            log('trending failed:', e)
        cands += [{'song': a, 'artist': b, 'source': 'seed'} for a, b in SEEDS]   # last: only fills when the LLM gives nothing new
        seen, uniq = set(), []
        for c in cands:
            if norm(c['song']) not in seen:
                seen.add(norm(c['song']))
                uniq.append(c)
        cands = uniq
    report, n_added = [], 0
    for c in cands:
        if n_added >= cap:
            break
        try:
            st, why = add_candidate(pool, c['song'], c['artist'], c.get('source', 'seed'), search, analyze_fn, fetch_fn, trend_fn)
        except Exception as e:
            st, why = 'error', f'{type(e).__name__}: {e}'[:160]
        log(f"{st:8s} {c['artist']} - {c['song']}: {why}")
        if st != 'dup':
            report.append({'song': c['song'], 'artist': c['artist'], 'source': c.get('source'), 'status': st, 'why': why})
        n_added += st == 'added'
        if not dry and st in ('added', 'rejected'):
            save_pool(pool, pool_path)
    retired = retire_underperformers(pool, channel_mean) if channel_mean is not None else 0
    if not dry:
        save_pool(pool, pool_path)
        os.makedirs(MEMDIR, exist_ok=True)
        with open(os.path.join(MEMDIR, f'sound-music-topup-{month}.json'), 'w', encoding='utf-8') as f:
            json.dump({'at': iso(), 'added': n_added, 'retired': retired, 'verdicts': report}, f, indent=1, ensure_ascii=False)
    log(f'added {n_added}, retired {retired}, pool {len(pool["songs"])}')
    return {'added': n_added, 'retired': retired, 'report': report}


# ------------------------------------------------------------------ selftest (synthetic songs, no network)
def _synth(bpm=100, bars=48, seed=0):
    """A fake song: quiet pad intro (8 bars), verse with a thin hat (16), chorus with kick/snare/hat + a chord riff (16), break (4), chorus (4)."""
    rng = np.random.RandomState(seed)
    beat = 60 / bpm
    n = int(bars * 4 * beat * SR)
    y = np.zeros(n, np.float32)
    t = np.arange(n) / SR

    def add(at, snd):
        i = int(at * SR)
        y[i:i + len(snd)] += snd[:max(0, n - i)]
    kt = np.arange(int(0.18 * SR)) / SR
    kick = (np.sin(2 * np.pi * (50 + 90 * np.exp(-kt * 30)) * kt) * np.exp(-kt * 14)).astype(np.float32) * 0.9
    sn = (rng.randn(int(0.15 * SR)) * np.exp(-np.arange(int(0.15 * SR)) / SR * 25)).astype(np.float32) * 0.5
    hat = (rng.randn(int(0.04 * SR)) * np.exp(-np.arange(int(0.04 * SR)) / SR * 90)).astype(np.float32) * 0.15
    chords = [(220, 277, 330), (196, 247, 294), (175, 220, 262), (196, 247, 294)]
    for b in range(bars):
        if b < 8:
            sec = 'intro'
        elif b < 24:
            sec = 'verse'
        elif b < 40:
            sec = 'chorus'
        elif b < 44:
            sec = 'break'
        else:
            sec = 'chorus'
        t0 = b * 4 * beat
        ch = chords[(b // 2) % 4]
        i0, i1 = int(t0 * SR), int((t0 + 4 * beat) * SR)
        amp = {'intro': 0.12, 'verse': 0.10, 'chorus': 0.2, 'break': 0.08}[sec]
        for f in ch:
            y[i0:i1] += amp * np.sin(2 * np.pi * f * t[i0:i1]).astype(np.float32)
        for k in range(4):
            tb = t0 + k * beat
            if sec in ('chorus',):
                add(tb, kick if k % 2 == 0 else sn)
                add(tb + beat / 2, hat)
                add(tb + beat / 4, hat * 0.6)
            elif sec == 'verse':
                add(tb + beat / 2, hat)
    return y / (np.abs(y).max() + 1e-9) * 0.8


def selftest():
    import tempfile as tf
    y = _synth()
    an = analyze(y=y)
    assert 90 <= an['tempo'] <= 110, an['tempo']
    sec = an['section']
    assert 25 <= sec['end'] - sec['start'] <= 35, sec
    bar = an['bar_s']
    assert abs(((sec['start'] - an['downbeat']) / bar) - round((sec['start'] - an['downbeat']) / bar)) < 0.05, 'section not on a bar start'
    assert sec['start'] >= 8 * bar * 0.99 + 0.0 and sec['start'] < 40 * bar, f'section should sit in the chorus, got {sec}'
    log('selftest synth:', an['tempo'], 'bpm', sec['start'], '-', sec['end'], 'audio', an['audio_score'])
    # pick_song: no repeat in a row, cooldown, dead drums rejected
    d = tf.mkdtemp()
    pp = os.path.join(d, 'pool.json')
    pool = load_pool(pp)
    for i in range(5):
        r = {'id': f's{i}', 'song': f'Song {i}', 'artist': 'A', 'video_id': 'x' * 11, 'url': 'u', 'query': f'A song {i}', 'tempo': 100, 'bar_s': 2.4, 'downbeat': 0.1,
             'section': {'start': 10.0 + i, 'end': 40.0 + i}, 'total': 0.6, 'status': 'active', 'last_used': None, 'perf': {'n': 0, 'sum': 0}, 'source': 'seed'}
        pool['songs'].append(r)
    save_pool(pool, pp)
    last = None
    seen = set()
    for k in range(40):
        s = pick_song([last] if last else [], pool_path=pp, rng=random.Random(k), when=now())
        assert s['id'] != last, 'same song twice in a row'
        last = s['id']
        seen.add(last)
        mark_used(last, pool_path=pp)
    assert len(seen) == 5
    # cooldown: every song used just now, so the pick falls back to the least recently used and still not the last
    s = pick_song(['s0'], pool_path=pp, rng=random.Random(1))
    assert s['id'] != 's0'
    # a song used 40 d ago is eligible again, one used 5 d ago is not
    pool = load_pool(pp)
    for i, s_ in enumerate(pool['songs']):
        s_['last_used'] = iso(now() - dt.timedelta(days=40 if i == 3 else 5))
    save_pool(pool, pp)
    for k in range(10):
        assert pick_song(['s0'], pool_path=pp, rng=random.Random(k))['id'] == 's3'
    # top-up with fakes: a good song is added on probation, a low-view one and a drumless one are rejected
    fake_search = lambda q, n=8: [{'id': 'a' * 11, 'views': 9e7 if 'Good' in q else 1e4 if 'Low' in q else 9e7, 'duration': 200,
                                   'title': q.replace(' official audio', '').replace(' beat sync sound edit', '') + ' (Official Audio)', 'channel': 'A - Topic'}]
    fake_an = lambda p: analyze(y=_synth(bpm=100) if 'good' in p else (np.random.RandomState(0).randn(SR * 120) * 0.1).astype(np.float32))
    pool = load_pool(pp)
    pool['songs'] = []
    save_pool(pool, pp)
    old = os.environ.get('MEMORY_DIR')
    globals()['MEMDIR'] = d
    r1 = []
    for song, path in (('Good Song', 'good'), ('Low Views', 'good'), ('Noise Song', 'bad')):
        p2 = load_pool(pp)
        st, why = add_candidate(p2, song, 'A', 'seed', fake_search, fake_an, lambda rec, path=path: path, lambda s, a: 3)
        save_pool(p2, pp)
        r1.append(st)
        log('selftest topup', song, st, why)
    assert r1 == ['added', 'rejected', 'rejected'], r1
    p2 = load_pool(pp)
    assert p2['songs'][0]['status'] == 'probation' and p2['songs'][0]['section']['start'] is not None
    assert add_candidate(p2, 'Good Song', 'A', 'seed', fake_search, fake_an, lambda r: 'good')[0] == 'dup'
    # retirement
    p2['songs'][0]['perf'] = {'n': 4, 'sum': 0.4 * 4}
    assert retire_underperformers(p2, 1.2) == 1 and p2['songs'][0]['status'] == 'retired'
    assert parse_songs('```json\n{"songs":[{"song":"A","artist":"B",}]}\n```')[0]['song'] == 'A'
    shutil.rmtree(d, ignore_errors=True)
    log('selftest OK')


# ------------------------------------------------------------------ CLI
def main(argv):
    cmd = argv[1] if len(argv) > 1 else 'selftest'
    if cmd == 'selftest':
        selftest()
    elif cmd == 'seed':
        topup(seed_only=True, limit=int(argv[2]) if len(argv) > 2 else None)
    elif cmd == 'topup':
        topup(dry='--dry-run' in argv)
    elif cmd == 'analyze':
        a = analyze(argv[2])
        print(json.dumps(a, indent=1))
    elif cmd == 'pick':
        print(json.dumps(pick_song([]), indent=1, ensure_ascii=False))
    elif cmd == 'regate':      # re-apply the current gate to the pool (after tuning); failures move to 'rejected'
        p = load_pool()
        keep = []
        for r in p['songs']:
            an = {'tempo': r['tempo'], 'audio_score': r['scores']['audio'], 'section': {'score': r['section']['score'], 'parts': r['section']['parts']}}
            why = gate(an, r['views'])
            if why:
                p['rejected'].append({'song': r['song'], 'artist': r['artist'], 'at': iso(), 'why': why, 'scores': r['scores']})
                log('regate rejects', r['song'], why)
            else:
                keep.append(r)
        p['songs'] = keep
        save_pool(p)
    elif cmd == 'show':
        for s in sorted(load_pool()['songs'], key=lambda s: -s['total']):
            print(f"{s['artist']} - {s['song']} | {s['tempo']} bpm | {s['section']['start']}-{s['section']['end']} s | audio {s['scores']['audio']} total {s['total']} | {s['views']/1e6:.0f}M | {s['status']}")
    else:
        print(__doc__)


if __name__ == '__main__':
    main(sys.argv)
