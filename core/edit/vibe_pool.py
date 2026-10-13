#!/usr/bin/env python3
"""
Vibe pools for the sound edit and the cinematic slow-mo edit (format: core/edit/refs/pool-format.md, vibes:
core/edit/vibes.json). Clips come only through core/edit/library.py (its gates + the per-shot safety gate), then this
file adds the pool's own colour / mood gate, de-duplicates moments and keeps the used ledger.

    python core/edit/vibe_pool.py status [--json]
    python core/edit/vibe_pool.py refresh --preset sound|slowmo|all [--vibe V] [--budget-min 300] [--staging DIR]
                                          [--queries N] [--local-only]
    python core/edit/vibe_pool.py songs --preset sound|slowmo|all          (fetch / register the vibe songs)
    python core/edit/vibe_pool.py validate [--dir DIR]
    python core/edit/vibe_pool.py promote --staging DIR
    python core/edit/vibe_pool.py prune
    python core/edit/vibe_pool.py mark-used --result result.json
    python core/edit/vibe_pool.py import --preset P --vibe V --shots shots.json [...]   (a finished library run)

Env: EDIT_POOL_DIR (default <repo>/output/edit-pools), MEMORY_DIR (ledger dir, default <repo>/memory), EDIT_CACHE,
MEME_PYTHON / sys.executable for the library runs, YT_COOKIES. The last stdout line of every command is one JSON object.
"""
import argparse
import datetime as _dt
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
VIBES_FILE = os.path.join(HERE, 'vibes.json')
VERSION = 1


def log(*a):
    print('[vibe_pool]', *a, file=sys.stderr, flush=True)


def now():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def pool_root():
    return os.path.abspath(os.environ.get('EDIT_POOL_DIR') or os.path.join(ROOT, 'output', 'edit-pools'))


def ledger_path():
    mem = os.environ.get('MEMORY_DIR') or 'memory'
    mem = mem if os.path.isabs(mem) else os.path.join(ROOT, mem)
    return os.path.join(mem, 'edit-pool-used.json')


def read_json(p, dflt):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return dflt


def write_json(p, d):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(tmp, p)


def load_vibes():
    return read_json(VIBES_FILE, {})


def vibe_dir(preset, vibe, root=None):
    return os.path.join(root or pool_root(), preset, vibe)


def load_ledger():
    d = read_json(ledger_path(), None) or {}
    d.setdefault('version', VERSION)
    d.setdefault('items', {})
    d.setdefault('moments', {})
    return d


def save_ledger(d):
    write_json(ledger_path(), d)


def load_manifest(preset, vibe, kind='clips', root=None):
    p = os.path.join(vibe_dir(preset, vibe, root), f'{kind}.json')
    d = read_json(p, None) or {}
    d.setdefault('version', VERSION)
    d.setdefault('preset', preset)
    d.setdefault('vibe', vibe)
    d.setdefault('items', [])
    return d


def save_manifest(preset, vibe, d, kind='clips', root=None):
    d['built'] = now()
    write_json(os.path.join(vibe_dir(preset, vibe, root), f'{kind}.json'), d)


def key(preset, vibe, kind, iid):
    return f'{preset}/{vibe}/{kind}/{iid}'


# ------------------------------------------------------------------ moments (never show one scene twice)

def _overlaps(a0, a1, b0, b1, pad=1.0):
    return a0 < b1 + pad and b0 < a1 + pad


def moment_taken(source_id, s, e, ledger, items=()):
    for m in ledger.get('moments', {}).get(source_id, []):
        if _overlaps(s, e, m[0], m[1]):
            return True
    for it in items:
        if it.get('source_id') == source_id and it.get('status') != 'rejected' \
                and _overlaps(s, e, float(it.get('start') or 0), float(it.get('end') or 0)):
            return True
    return False


# ------------------------------------------------------------------ blocklist (gore / unsafe moments, forever)

# Source moments rejected for SAFETY (blood, gore, injury, nudity) are written here and never come back: not by a
# re-import at a slightly different start, not after `promote` drops the rejected manifest rows. Committed.
BLOCKLIST_FILE = os.path.join(HERE, 'assets', 'pool-blocklist.json')
# Owner 2026-10-12: blood / gore is ALLOWED in these edits -> it is no longer a safety reason (no blocklist entry).
UNSAFE_WORDS = ('nude', 'nsfw', 'underwear', 'sexual', 'shirtless', 'minor', 'subtitle', 'hentai', 'object in mouth')
GORE_IS_SAFETY = False


def _block_category(why):
    w = why.lower()
    if 'subtitle' in w:
        return 'subtitles_text'
    if 'minor' in w:
        return 'minor'
    if 'hentai' in w:
        return 'explicit_source'
    return 'sexual_content'


def load_blocklist():
    return read_json(BLOCKLIST_FILE, {'version': 1, 'moments': {}})


def blocked(source_id, s, e, bl=None):
    bl = bl if bl is not None else load_blocklist()
    return any(_overlaps(s, e, m[0], m[1]) for m in bl.get('moments', {}).get(str(source_id), []))


def sync_blocklist(root=None):
    """Adds every manifest item rejected for a safety reason to the blocklist; deletes its media files."""
    bl = load_blocklist()
    mom = bl.setdefault('moments', {})
    n_new, n_files = 0, 0
    for p, pd in load_vibes()['presets'].items():
        for vn in pd['vibes']:
            man = load_manifest(p, vn, root=root)
            for it in man['items']:
                why = str(it.get('reject_reason') or '')
                if it.get('status') not in ('rejected', 'retired') or not any(w in why.lower() for w in UNSAFE_WORDS):
                    continue
                sid, a, b = str(it.get('source_id')), float(it.get('start') or 0), float(it.get('end') or 0)
                lst = mom.setdefault(sid, [])
                if not any(abs(m[0] - a) < 0.05 and abs(m[1] - b) < 0.05 for m in lst):
                    lst.append([a, b, why[:80], _block_category(why)])
                    n_new += 1
                for g in glob.glob(os.path.join(vibe_dir(p, vn, root), it['file']) + '*'):
                    os.remove(g)
                    n_files += 1
    bl['updated'] = now()
    write_json(BLOCKLIST_FILE, bl)
    return {'added': n_new, 'files_removed': n_files, 'sources': len(mom)}


# ------------------------------------------------------------------ colour gate (no grey / green / milky faces)

def colour_gate(path, vibe_kind):
    """Middle + two quarter frames: black point, saturation, colour cast of the centre (where the face usually is).
    Returns (ok, scores). Beauty vibes are strict (rich, not milky, no green / grey skin); sound vibes only reject
    washed-out / grey footage."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    frames = []
    for q in (0.3, 0.5, 0.7):
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(n * q)))
        ok, f = cap.read()
        if ok:
            frames.append(f)
    cap.release()
    if not frames:
        return False, {'error': 'decode'}
    bp, sat, lum, cast_g, csat = [], [], [], [], []
    for f in frames:
        f = cv2.resize(f, (320, int(320 * f.shape[0] / f.shape[1])))
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        lab = cv2.cvtColor(f, cv2.COLOR_BGR2LAB).astype(np.float32)
        y = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        bp.append(float(np.percentile(y, 2)))
        lum.append(float(np.median(y)))
        sat.append(float(np.median(hsv[..., 1])))
        h, w = y.shape
        c = lab[h // 5: 4 * h // 5, w // 4: 3 * w // 4]
        cs = hsv[h // 5: 4 * h // 5, w // 4: 3 * w // 4, 1]
        # skin-ish pixels of the centre: L 90..235, a/b near warm; the cast is the mean a* (green < 128)
        L, A, B = c[..., 0], c[..., 1], c[..., 2]
        m = (L > 90) & (L < 235) & (B > 128)
        cast_g.append(float(np.mean(A[m]) - 128.0) if m.sum() > 200 else 0.0)
        csat.append(float(np.median(cs)))
    sc = {'black_point': round(float(np.median(bp)), 1), 'sat': round(float(np.median(sat)), 1),
          'luma': round(float(np.median(lum)), 1), 'skin_a': round(float(np.median(cast_g)), 2),
          'centre_sat': round(float(np.median(csat)), 1)}
    if vibe_kind == 'slowmo':
        # hard reject only what a per-clip levels fix cannot save (calibrated on 40 beauty twixtor shots 2026-10-12:
        # hazy-but-pretty pastel shots have black points 50-80 and are fixed by the renderer's per-clip levels; a green
        # cast (Frieren OP sky, a* -30) or a blown / near-grey frame is not)
        ok = (sc['black_point'] <= 90 and sc['sat'] >= 30 and sc['centre_sat'] >= 20 and 50 <= sc['luma'] <= 235
              and sc['skin_a'] >= -8.0)
        sc['milky'] = bool(sc['black_point'] > 45 or sc['sat'] < 55)
    else:
        ok = sc['black_point'] <= 60 and sc['sat'] >= 30 and sc['luma'] >= 35
    sc['face_ok'] = bool(ok)
    return bool(ok), sc


# ------------------------------------------------------------------ import a library run into a vibe

SUBJECT_KEYS = ('subject', 'gender', 'female', 'subject_female')


def _talking(s):
    """A talking mouth: library's mouth-flap test (mouth moving while the eyes hold) or the SigLIP talk / talk_mouth
    probabilities (floats 0-1, not booleans)."""
    r = s.get('rules') or {}
    return (float(s.get('mouth_flap') or 0) >= 0.2 or float(s.get('talk') or 0) >= 0.45
            or float(r.get('talk_mouth') or 0) >= 0.45 or float(s.get('speech_ratio') or 0) >= 0.3)


def _subject(s):
    r = s.get('rules') or {}
    if isinstance(r.get('female'), (int, float)):
        f, m, g = float(r.get('female') or 0), float(r.get('male') or 0), float(r.get('group') or 0)
        if max(f, m, g) < 0.4:
            return 'none'
        return 'female' if f >= max(m, g) else ('male' if m >= g else 'group')
    for k in ('female_score', 'female'):
        v = s.get(k)
        if isinstance(v, (int, float)):
            return 'female' if v >= 0.5 else 'male'
    v = s.get('subject')
    return v if isinstance(v, str) else None


def import_shots(preset, vibe, shots_json, root=None, query=None):
    """Copy the gate-passed shots of one library run into the vibe pool. Returns (added, skipped reasons)."""
    V = load_vibes()['presets'][preset]
    d = read_json(shots_json, {})
    shots = d.get('shots') if isinstance(d, dict) else d
    if not shots:
        return 0, {'empty': 1}
    man = load_manifest(preset, vibe, root=root)
    led = load_ledger()
    have = {it['id'] for it in man['items']}
    bl = load_blocklist()
    # the same moment must not sit in two vibes of a preset either (it could be shown twice)
    others = [it for vn in V['vibes'] if vn != vibe for it in load_manifest(preset, vn, root=root)['items']]
    vd = vibe_dir(preset, vibe, root)
    os.makedirs(os.path.join(vd, 'clips'), exist_ok=True)
    added, skip = 0, {}

    def no(r):
        skip[r] = skip.get(r, 0) + 1

    for s in shots:
        p = s.get('path')
        if not p or not os.path.exists(p):
            no('missing')
            continue
        if s.get('has_text') or (s.get('text_band') and any(float(x) > 0.02 for x in s.get('text_band') or [0])):
            no('text')
            continue
        if preset == 'slowmo' and _talking(s):
            no('talk')
            continue
        sid = str(s.get('source_id') or '')
        if not sid or sid.startswith('local'):
            # a --local run: the source file is named <youtube id>.<ext> (the cache layout); keep the real id so the
            # moment check matches the same scene fetched over the network
            stem = os.path.splitext(os.path.basename(str(s.get('source_title') or s.get('source') or '')))[0]
            sid = stem if len(stem) == 11 else (sid or hashlib.md5(p.encode()).hexdigest()[:11])
        st = float(s.get('start') or 0.0)
        en = float(s.get('end') or (st + float(s.get('duration') or 0)))
        iid = f'c_{sid}_{int(round(st * 1000))}'
        if iid in have or key(preset, vibe, 'clip', iid) in led['items']:
            no('dup_id')
            continue
        if moment_taken(sid, st, en, led, man['items'] + others):
            no('moment_used')
            continue
        if blocked(sid, st, en, bl):
            no('blocked')
            continue
        if preset == 'sound' and not s.get('has_audio', True):
            no('no_audio')
            continue
        # sound pool: clips without a clean hit are kept as FILL shots (the references' song-only beat shots, ~46 %
        # of their shots); sound_edit.render_vibe decides per clip (percussive-layer extraction may still find a hit)
        ok, cs = colour_gate(p, preset)
        if not ok:
            no('colour')
            continue
        dst_rel = f'clips/{iid}.mp4'
        shutil.copy2(p, os.path.join(vd, dst_rel))
        for side in ('.portrait.json', '.flow.json'):
            if os.path.exists(p + side):
                shutil.copy2(p + side, os.path.join(vd, dst_rel + side))
        it = {k: v for k, v in s.items() if k not in ('path',) and not k.startswith('_')}
        it.update({
            'id': iid, 'file': dst_rel, 'source_id': sid, 'start': round(st, 3), 'end': round(en, 3),
            'moment': f'{sid}#{int(st)}', 'source_url': f'https://youtu.be/{sid}' if len(sid) == 11 else None,
            'subject': _subject(s), 'scores': dict(cs, aesthetic=s.get('aesthetic'), sharpness=s.get('sharpness'),
                                                    motion=s.get('motion'), taste=s.get('taste')),
            'talk': _talking(s),
            'safety': {'gate': 'pass', 'checked': 'library per-shot (NudeNet every kept frame + SigLIP)'},
            'sfx': [{'t': float(t)} for t in (s.get('sfx_hits') or s.get('hits') or [])] if preset == 'sound' else [],
            'query': query or s.get('query') or d.get('query'),
            'status': 'fresh', 'added': now()[:10], 'used_at': None, 'used_in': None,
        })
        man['items'].append(it)
        have.add(iid)
        added += 1
    save_manifest(preset, vibe, man, root=root)
    return added, skip


# ------------------------------------------------------------------ refresh (library runs, resumable)

def _lib_cmd(L, mode, query, out_dir, budget_s, local_only=False):
    py = os.environ.get('MEME_PYTHON') or sys.executable
    cmd = [py, os.path.join(HERE, 'library.py'), '--mode', mode, '--query', query, '--kind', L.get('kind', 'anime'),
           '--count', str(L.get('count', 16)), '--min-dur', str(L.get('min_dur', 2)), '--max-dur', str(L.get('max_dur', 6)),
           '--preset', L['preset'], '--out-dir', out_dir, '--budget', str(int(budget_s))]
    if L.get('with_audio'):
        cmd.append('--with-audio')
    if L.get('need_sfx'):
        cmd.append('--need-sfx')
    os.makedirs(out_dir, exist_ok=True)
    # used moments are filtered at import (moment_taken), not by --used: that would drop whole sources
    if local_only:
        cmd.append('--local-only')
    if os.environ.get('YT_COOKIES') and os.path.exists(os.environ['YT_COOKIES']):
        cmd += ['--cookies', os.environ['YT_COOKIES']]
    return cmd


def refresh(presets, vibe=None, budget_min=300.0, root=None, max_queries=None, local_only=False, shard=None):
    V = load_vibes()['presets']
    root = root or pool_root()
    end = time.time() + budget_min * 60
    report = []
    jobs = []
    for p in presets:
        for vn, vd in V[p]['vibes'].items():
            if vibe and vn != vibe:
                continue
            for qi, (mode, q) in enumerate(vd['queries'][:max_queries] if max_queries else vd['queries']):
                jobs.append((p, vn, qi, mode, q))
    # interleave the vibes so a time-boxed run grows every pool
    jobs.sort(key=lambda j: (j[2], j[0], j[1]))
    if shard:                                   # CI matrix: shard k of n takes every n-th query (monthly refresh)
        jobs = [j for i, j in enumerate(jobs) if i % shard[1] == shard[0]]
    for p, vn, qi, mode, q in jobs:
        left = end - time.time()
        if left < 120:
            log('budget used up')
            break
        vdir = vibe_dir(p, vn, root)
        state_p = os.path.join(vdir, '.refresh-state.json')
        state = read_json(state_p, {})
        month = now()[:7]
        sk = f'{mode}:{q}'
        if state.get(sk, {}).get('month') == month:
            continue
        out = os.path.join(root, '_lib', p, vn, f'q{qi:02d}')
        L = V[p]['library']
        cmd = _lib_cmd(L, mode, q, out, min(1500, left - 60), local_only)
        log(f'{p}/{vn}: library {mode} "{q}"')
        t0 = time.time()
        try:
            subprocess.run(cmd, timeout=min(1800, left), check=False,
                           env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        except subprocess.TimeoutExpired:
            log('library timed out:', q)
        sj = os.path.join(out, 'shots.json')
        added, skip = (import_shots(p, vn, sj, root, query=q) if os.path.exists(sj) else (0, {'no_manifest': 1}))
        state[sk] = {'month': month, 'added': added, 'skip': skip, 'secs': round(time.time() - t0)}
        write_json(state_p, state)
        report.append({'pool': f'{p}/{vn}', 'query': q, 'added': added, 'skip': skip})
        log(f'{p}/{vn}: +{added} {skip}')
    write_index(root)
    return report


# ------------------------------------------------------------------ songs

def resolve_sound(sid, entry, vdir):
    """Local file, else the pool's sounds/ copy, else a yt-dlp download by query. Returns an absolute path or None."""
    home = os.path.expanduser('~')
    for rel in entry.get('local') or []:
        p = rel if os.path.isabs(rel) else os.path.join(home, rel)
        if os.path.exists(p):
            return p
    os.makedirs(os.path.join(vdir, 'sounds'), exist_ok=True)
    have = glob.glob(os.path.join(vdir, 'sounds', sid + '.*'))
    have = [h for h in have if not h.endswith('.json')]
    if have:
        return have[0]
    shared = glob.glob(os.path.join(pool_root(), '_sounds', sid + '.*'))
    shared = [h for h in shared if not h.endswith('.json')]
    if shared:
        return shared[0]
    out = os.path.join(pool_root(), '_sounds')
    os.makedirs(out, exist_ok=True)
    py = os.environ.get('MEME_PYTHON') or sys.executable
    cmd = [py, '-m', 'yt_dlp', '--no-playlist', '--no-warnings', '-f', 'bestaudio[ext=m4a]/bestaudio', '-o',
           os.path.join(out, sid + '.%(ext)s'), '--match-filter', 'duration > 60 & duration < 420',
           f'ytsearch1:{entry["query"]}']
    if os.environ.get('YT_COOKIES') and os.path.exists(os.environ['YT_COOKIES']):
        cmd[3:3] = ['--cookies', os.environ['YT_COOKIES']]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    got = [h for h in glob.glob(os.path.join(out, sid + '.*')) if not h.endswith(('.json', '.part'))]
    if not got:
        log('song download failed:', sid, (r.stderr or '')[-300:])
        return None
    return got[0]


def register_songs(presets, root=None):
    D = load_vibes()
    out = []
    for p in presets:
        for vn, vd in D['presets'][p]['vibes'].items():
            vdir = vibe_dir(p, vn, root)
            man = load_manifest(p, vn, 'sounds', root)
            by = {it['id']: it for it in man['items']}
            for sid in vd['songs']:
                e = D['sounds'][sid]
                path = resolve_sound(sid, e, vdir)
                it = by.get(sid) or {'id': sid, 'status': 'fresh', 'used_at': None}
                it.update({'title': e['title'], 'artist': e['artist'], 'query': e['query'], 'kind': e.get('kind', 'song'),
                           'file': path, 'treatment': e.get('treatment') or {'speed': 1.0}, 'fits': e.get('fits'),
                           'licence': D.get('licence_note'), 'reuse': 'song', 'cooldown_days': 21,
                           'section': it.get('section')})
                by[sid] = it
                out.append({'pool': f'{p}/{vn}', 'song': sid, 'file': path})
            # only the vibe's current songs stay (a song removed from vibes.json, e.g. off-niche, is dropped)
            man['items'] = [by[sid] for sid in vd['songs'] if by.get(sid, {}).get('file')]
            save_manifest(p, vn, man, 'sounds', root)
    return out


# ------------------------------------------------------------------ selection (vibe_edit.py) and the ledger

def fresh_clips(preset, vibe, root=None):
    root = root or pool_root()
    man = load_manifest(preset, vibe, root=root)
    led = load_ledger()
    vd = vibe_dir(preset, vibe, root)
    out = []
    reviewed_only = os.environ.get('EDIT_POOL_REVIEWED_ONLY', '1') != '0'
    bl = load_blocklist()
    for it in man['items']:
        if it.get('status') != 'fresh' or key(preset, vibe, 'clip', it['id']) in led['items']:
            continue
        # the library's gore gate missed blood shots (2026-10-12): a clip is shown only after a visual review
        # (core/edit/pool_review.py sheets -> 'reviewed' stamp); EDIT_POOL_REVIEWED_ONLY=0 turns this off
        if reviewed_only and not it.get('reviewed'):
            continue
        if moment_taken(it['source_id'], float(it['start']), float(it['end']), led):
            continue
        if blocked(it['source_id'], float(it['start']), float(it['end']), bl):
            continue
        p = os.path.join(vd, it['file'])
        if not os.path.exists(p):
            continue
        s = dict(it)
        s['path'] = p
        s['pool_id'] = it['id']
        out.append(s)
    return out


def song_ok(preset, vibe, root=None):
    man = load_manifest(preset, vibe, 'sounds', root)
    led = load_ledger()
    ok = []
    for it in man['items']:
        u = led['items'].get(key(preset, vibe, 'sound', it['id']))
        last = (u or {}).get('used_at') or it.get('used_at')
        if last:
            age = (_dt.datetime.now(_dt.timezone.utc) - _dt.datetime.fromisoformat(last.replace('Z', '+00:00'))).days
            if age < int(it.get('cooldown_days') or 21):
                continue
        if it.get('file') and os.path.exists(it['file']):
            ok.append(it)
    return ok


def mark_used(preset, vibe, clip_ids, clips_by_id=None, sound_id=None, video='', role='short', root=None):
    led = load_ledger()
    t = now()
    man = load_manifest(preset, vibe, root=root)
    by = {it['id']: it for it in man['items']}
    for cid in clip_ids:
        led['items'][key(preset, vibe, 'clip', cid)] = {'used_at': t, 'video': os.path.basename(video), 'role': role}
        it = by.get(cid)
        if it:
            it['status'], it['used_at'], it['used_in'] = 'used', t, os.path.basename(video)
            led['moments'].setdefault(it['source_id'], []).append([it['start'], it['end'], t])
    if sound_id:
        led['items'][key(preset, vibe, 'sound', sound_id)] = {'used_at': t, 'video': os.path.basename(video), 'role': role}
    save_ledger(led)
    save_manifest(preset, vibe, man, root=root)


def status(root=None):
    D = load_vibes()
    root = root or pool_root()
    out = {}
    for p, pd in D['presets'].items():
        for vn in pd['vibes']:
            man = load_manifest(p, vn, root=root)
            fr = fresh_clips(p, vn, root)
            ev = sum(max(1, len(it.get('sfx') or [])) for it in fr) if p == 'sound' else None
            songs = song_ok(p, vn, root)
            need = pd.get('min_fresh_clips', 70)
            per_run = 70 if p == 'sound' else 45
            st = {'fresh': len(fr), 'total': len(man['items']),
                  'used': sum(1 for it in man['items'] if it.get('status') == 'used'),
                  'female': sum(1 for it in fr if it.get('subject') == 'female'),
                  'hits': sum(1 for it in fr if (it.get('vis') or {}).get('hit')) if p == 'sound' else None,   # visible-source hits (core/pools/pool_visible.py)
                  'events': ev, 'songs_available': [s['id'] for s in songs], 'runs_left': len(fr) // per_run,
                  'ready': len(fr) >= need and bool(songs) and (ev is None or ev >= pd.get('min_fresh_events', 0))}
            out[f'{p}/{vn}'] = st
    return out


def write_index(root=None):
    root = root or pool_root()
    write_json(os.path.join(root, 'index.json'), {'version': VERSION, 'updated': now(), 'pools': status(root)})


def validate(root=None):
    root = root or pool_root()
    D = load_vibes()
    led = load_ledger()
    problems = []
    for p, pd in D['presets'].items():
        for vn in pd['vibes']:
            man = load_manifest(p, vn, root=root)
            seen = {}
            for it in man['items']:
                if it.get('status') != 'fresh':
                    continue
                f = os.path.join(vibe_dir(p, vn, root), it['file'])
                if not os.path.exists(f) or os.path.getsize(f) < 1000:
                    problems.append(f'{p}/{vn} {it["id"]}: file missing')
                if (it.get('safety') or {}).get('gate') != 'pass':
                    problems.append(f'{p}/{vn} {it["id"]}: safety gate not passed')
                if key(p, vn, 'clip', it['id']) in led['items']:
                    problems.append(f'{p}/{vn} {it["id"]}: fresh in manifest but used in the ledger')
                for o in seen.get(it['source_id'], []):
                    if _overlaps(float(it['start']), float(it['end']), o[0], o[1], pad=0.0):
                        problems.append(f'{p}/{vn} {it["id"]}: same moment twice')
                seen.setdefault(it['source_id'], []).append((float(it['start']), float(it['end'])))
    st = status(root)
    return {'ok': not problems, 'problems': problems[:50], 'status': st}


def promote(staging):
    live = pool_root()
    D = load_vibes()
    moved = {}
    for p, pd in D['presets'].items():
        for vn in pd['vibes']:
            sman = load_manifest(p, vn, root=staging)
            lman = load_manifest(p, vn, root=live)
            keep = [it for it in lman['items'] if it.get('status') == 'fresh']
            ids = {it['id'] for it in keep}
            for it in sman['items']:
                if it.get('status') == 'fresh' and it['id'] not in ids:
                    src = os.path.join(vibe_dir(p, vn, staging), it['file'])
                    dst = os.path.join(vibe_dir(p, vn, live), it['file'])
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    keep.append(it)
            lman['items'] = keep
            save_manifest(p, vn, lman, root=live)
            for kind in ('sounds',):
                s = os.path.join(vibe_dir(p, vn, staging), f'{kind}.json')
                if os.path.exists(s):
                    shutil.copy2(s, os.path.join(vibe_dir(p, vn, live), f'{kind}.json'))
            moved[f'{p}/{vn}'] = len(keep)
    prune()
    write_index(live)
    return moved


def prune(root=None):
    root = root or pool_root()
    D = load_vibes()
    led = load_ledger()
    n = 0
    for p, pd in D['presets'].items():
        for vn in pd['vibes']:
            man = load_manifest(p, vn, root=root)
            keep = []
            for it in man['items']:
                used = it.get('status') in ('used', 'retired', 'rejected') or key(p, vn, 'clip', it['id']) in led['items']
                if used:
                    f = os.path.join(vibe_dir(p, vn, root), it['file'])
                    for g in glob.glob(f + '*'):
                        os.remove(g)
                        n += 1
                    it['status'] = 'retired'
                keep.append(it)
            # retired items stay listed (ids are never re-imported) but without files
            man['items'] = keep
            save_manifest(p, vn, man, root=root)
    return n


# ------------------------------------------------------------------ strict gore gate over the whole pool

GORE_EXTRA = ['blood on a face', 'a bleeding anime character', 'blood dripping from a wound', 'blood splashed on a wall',
              'a character coughing up blood', 'a severed limb', 'a corpse lying in blood', 'an injured bloody body']
CLEAN_EXTRA = ['a character with red hair', 'red eyes glowing', 'a red sunset sky', 'red energy effects in a fight',
               'a clean fight scene', 'a character in a red outfit', 'red flower petals']
GORE_MARGIN = 4.0           # max over 8 frames of (best gore prompt - best clean prompt) logit. WEAK signal (2026-10-12
                            # calibration: the one confirmed blood clip scored 2.86 while clean beauty shots reached
                            # 4.5): it only FLAGS clips; every refresh needs the visual review sheets (review-sheets)


def gore_check(presets=('sound', 'slowmo'), root=None, n=8, apply=True):
    """Re-judge every FRESH pool clip on n frames with SigLIP (clip_judge prompts + GORE_EXTRA / CLEAN_EXTRA): any frame
    whose gore-vs-clean share >= GORE_FRAME_MAX rejects the clip (status 'rejected', reject_reason). Returns the report."""
    sys.path.insert(0, os.path.join(ROOT, 'core', 'meme'))
    import numpy as np
    import clip_judge as CJ
    from PIL import Image
    import cv2
    J = CJ.Judge(check_vibe=False)
    torch = J.torch
    gore_p, clean_p = CJ.GORE + GORE_EXTRA, CJ.CLEAN + CLEAN_EXTRA
    with torch.inference_mode():
        t = J.proc(text=gore_p + clean_p, padding='max_length', return_tensors='pt')
        te = CJ._tensor(J.model.get_text_features(**t))
        te = te / te.norm(dim=-1, keepdim=True)
    ng = len(gore_p)
    rep = []
    D = load_vibes()['presets']
    for p in presets:
        for vn in D[p]['vibes']:
            man = load_manifest(p, vn, root=root)
            for it in man['items']:
                if it.get('status') != 'fresh':
                    continue
                f = os.path.join(vibe_dir(p, vn, root), it['file'])
                cap = cv2.VideoCapture(f)
                N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                imgs = []
                for k in range(n):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, int((k + 0.5) * N / n))
                    ok, fr = cap.read()
                    if ok:
                        imgs.append(Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)).resize((448, 252)))
                cap.release()
                if not imgs:
                    continue
                with torch.inference_mode():
                    x = J.proc(images=imgs, return_tensors='pt')
                    e = CJ._tensor(J.model.get_image_features(**x))
                    e = e / e.norm(dim=-1, keepdim=True)
                    lg = ((e @ te.T) * J.model.logit_scale.exp() + J.model.logit_bias).numpy()
                g, c = lg[:, :ng].max(1), lg[:, ng:].max(1)
                margin = float((g - c).max())               # raw logit margin (the sigmoid saturates on SigLIP)
                red = []
                for im in imgs:
                    hsv = cv2.cvtColor(np.asarray(im)[:, :, ::-1], cv2.COLOR_BGR2HSV)
                    h_, s_, v_ = hsv[..., 0], hsv[..., 1], hsv[..., 2]
                    red.append(float((((h_ <= 6) | (h_ >= 174)) & (s_ >= 150) & (v_ >= 45) & (v_ <= 190)).mean()))
                mx = margin
                it.setdefault('safety', {}).update(gore_margin=round(margin, 2), blood_red=round(max(red), 4))
                if margin >= GORE_MARGIN or (margin >= GORE_MARGIN - 3 and max(red) >= 0.02):
                    rep.append({'pool': f'{p}/{vn}', 'id': it['id'], 'margin': round(margin, 2), 'red': round(max(red), 4),
                                'title': it.get('source_title')})
                    it['safety']['gore_flag'] = True       # owner 2026-10-12: gore allowed -> flag only, never a reject
                    if apply and GORE_IS_SAFETY:
                        it['status'] = 'rejected'
                        it['reject_reason'] = f'gore gate (margin {margin:.1f}, red {max(red):.3f})'
            save_manifest(p, vn, man, root=root)
    return rep


def main():
    ap = argparse.ArgumentParser(description='vibe pools (core/edit/refs/pool-format.md)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('status')
    s.add_argument('--json', action='store_true')
    r = sub.add_parser('refresh')
    r.add_argument('--preset', default='all')
    r.add_argument('--vibe')
    r.add_argument('--budget-min', type=float, default=300)
    r.add_argument('--staging')
    r.add_argument('--queries', type=int)
    r.add_argument('--local-only', action='store_true')
    r.add_argument('--shard', help='K/N: only every N-th query, offset K (CI matrix)')
    g = sub.add_parser('songs')
    g.add_argument('--preset', default='all')
    v = sub.add_parser('validate')
    v.add_argument('--dir')
    pr = sub.add_parser('promote')
    pr.add_argument('--staging', required=True)
    sub.add_parser('prune')
    sub.add_parser('block-sync')
    m = sub.add_parser('mark-used')
    m.add_argument('--result', required=True)
    gc = sub.add_parser('gore-check')
    gc.add_argument('--preset', default='all')
    gc.add_argument('--apply', action='store_true', help='reject flagged clips (default: report only)')
    im = sub.add_parser('import')
    im.add_argument('--preset', required=True)
    im.add_argument('--vibe', required=True)
    im.add_argument('--shots', nargs='+', required=True)
    a = ap.parse_args()
    presets = ['sound', 'slowmo'] if getattr(a, 'preset', 'all') in ('all', None) else [a.preset]
    if a.cmd == 'status':
        st = status()
        if not a.json:
            for k, v in st.items():
                log(f'{k:22s} fresh {v["fresh"]:4d} (female {v["female"]}) events {v["events"]} songs {v["songs_available"]} '
                    f'runs_left {v["runs_left"]} ready {v["ready"]}')
        print(json.dumps({'ok': True, 'pools': st}))
    elif a.cmd == 'refresh':
        root = a.staging or None
        if root:
            # staging starts from the live manifests so de-duplication sees the live pool
            for p in presets:
                for vn in load_vibes()['presets'][p]['vibes']:
                    src = vibe_dir(p, vn)
                    for kind in ('clips', 'sounds'):
                        f = os.path.join(src, f'{kind}.json')
                        if os.path.exists(f) and not os.path.exists(os.path.join(vibe_dir(p, vn, root), f'{kind}.json')):
                            os.makedirs(vibe_dir(p, vn, root), exist_ok=True)
                            d = read_json(f, {})
                            d['items'] = [dict(it, status='retired' if it.get('status') != 'fresh' else 'live')
                                          for it in d.get('items', [])] if kind == 'clips' else d.get('items', [])
                            write_json(os.path.join(vibe_dir(p, vn, root), f'{kind}.json'), d)
        shard = tuple(int(x) for x in a.shard.split('/')) if a.shard else None
        rep = refresh(presets, a.vibe, a.budget_min, root, a.queries, a.local_only, shard)
        print(json.dumps({'ok': True, 'report': rep, 'status': status(root)}))
    elif a.cmd == 'songs':
        print(json.dumps({'ok': True, 'songs': register_songs(presets)}))
    elif a.cmd == 'validate':
        r = validate(a.dir)
        print(json.dumps(r))
        sys.exit(0 if r['ok'] else 1)
    elif a.cmd == 'promote':
        print(json.dumps({'ok': True, 'pools': promote(a.staging)}))
    elif a.cmd == 'prune':
        print(json.dumps({'ok': True, 'removed': prune()}))
    elif a.cmd == 'mark-used':
        res = read_json(a.result, {})
        for role in ('short', 'long'):
            ids = res.get(f'clips_{role}') or (res.get('clips_used') if role == 'short' else None) or []
            if ids:
                mark_used(res['preset'], res['vibe'], ids, sound_id=res.get('sound_used'),
                          video=res.get(role) or '', role=role)
        print(json.dumps({'ok': True}))
    elif a.cmd == 'gore-check':
        rep = gore_check(presets, apply=a.apply)
        print(json.dumps({'ok': True, 'rejected': rep, 'blocklist': sync_blocklist() if a.apply else None}))
    elif a.cmd == 'block-sync':
        print(json.dumps({'ok': True, 'blocklist': sync_blocklist()}))
    elif a.cmd == 'import':
        tot = {}
        for sj in a.shots:
            n, sk = import_shots(a.preset, a.vibe, sj)
            tot[sj] = {'added': n, 'skip': sk}
        write_index()
        print(json.dumps({'ok': True, 'import': tot}))


if __name__ == '__main__':
    main()
