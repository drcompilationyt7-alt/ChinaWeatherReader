#!/usr/bin/env python3
"""
hype_typography planner in the i4fSruIkkxk (PJUNKIE) structure, for ANY song and clip pool.

The remake (core/edit/refs/remake-hype_typography.md) copied the reference shot for shot; this module turns what was
copied into rules. core/edit/refs/hype_i4f_template.json (made by refs/hype_i4f_template.py) holds the reference's shot
table in its own seconds with its beat period and drop; every time here is mapped through the beat grid
(beat = (t_ref - drop_ref) / P_ref) onto the new song, anchored on the new song's drop:

  intro (~35 beats)   typed sung line over a slow bright hand / detail shot, a flash into a two-shot with a typed
                      caption, the music card (title / artist / typed line), a wide pretty shot with the song title's
                      letters flying in, a dark close-up with the channel card, a happy close-up with 4 key sung words
                      on the syllable, a dark wide, a grab, a split card, 4 frames fade + black gap (0.63 s at 119 BPM)
  drop (16 beats)     one shot per beat, each with the reference's own per-frame camera (push / pull punches, pans,
                      rolls, accelerating into the cut, arriving fast), a flash_smear / hype blur bridge / bar wipe /
                      3D card at every cut, a soft 5 kHz whoosh on every cut, the sung words 1-2 per beat
  replay (16 beats)   the drop block again (the reference's own structure), then a white-out and the channel logo
  song edits          intro duck + swell-in, swells into hero cuts, riser + impact on the drop, low-pass muffles,
                      fade-out (song_fx ops, the same as the remake)

Short form (opts length < 25 s): starts 13 beats before the drop (the happy close-up with its key words).

    plan = plan_hype_i4f(A, shots, opts)     # A = music.py analysis, shots = library shots, opts as planners.py
"""
import json
import math
import os
import random
import re

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, 'refs', 'hype_i4f_template.json')

LN_CAP, TX_CAP, TY_CAP, ROT_CAP, ZOOM_CAP = 1.1, 0.24, 0.11, 8.0, 2.8   # round 3: 2.3 held the i4f punches to 55-75 % of their speed
WARM_T = 18.0
CHORUS_FAST_BELOW, CHORUS_RATE = 0.8, 1.4
CREAM = (162, 195, 240)
PEACH = (135, 175, 230)


def log(msg):
    import sys
    print(f'[hype_i4f] {msg}', file=sys.stderr, flush=True)


def load_template(path=TEMPLATE):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


# ------------------------------------------------------------------------------------------ clip measures (cv2)

def _cv():
    import cv2
    return cv2


def clip_flow(path, a, b, rate=1.0, cache={}):
    """Median DIS flow of the clip window at `rate` (% W per 30 fps frame, the owner's motion meter)."""
    key = (path, round(a, 2), round(b, 2), round(rate, 2))
    if key in cache:
        return cache[key]
    cv2 = _cv()
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps / 30.0 * rate)))
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, a) * 1000.0)
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)
    prev, out, i = None, [], 0
    while True:
        ok, f = cap.read()
        if not ok or (i / fps) > (b - a) + 0.05:
            break
        if i % step == 0:
            h = int(f.shape[0] * 270 / f.shape[1])
            g = cv2.cvtColor(cv2.resize(f, (270, h)), cv2.COLOR_BGR2GRAY)
            if prev is not None:
                out.append(float(np.median(np.linalg.norm(dis.calc(prev, g, None), axis=2))) / 270 * 100)
            prev = g
        i += 1
    v = float(np.median(out)) if out else 0.3
    cache[key] = v
    return v


def clip_stats(path, t, cache={}):
    """(r - b, luma) of the clip frame at t."""
    key = (path, round(t, 1))
    if key not in cache:
        cv2 = _cv()
        cap = cv2.VideoCapture(path)
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
        ok, f = cap.read()
        cache[key] = (float(f[..., 2].mean() - f[..., 0].mean()), float(f.mean())) if ok else (30.0, 110.0)
    return cache[key]


# ------------------------------------------------------------------------------------------ clip choice

def bucket(c):
    return {'closeup': 'cu', 'medium': 'med', 'wide': 'wide'}.get(c.get('framing') or 'medium', 'med')


def pref_for(kind, chorus):
    """Score of a clip for a reference slot kind (framing order, mood, motion): the preset's exemplar taste first."""
    if kind.startswith('cu') or kind.startswith('ecu'):
        order = ['cu', 'med', 'wide']
    elif kind.startswith('wide') or kind.startswith('action_wide') or kind.startswith('hand'):
        order = ['wide', 'med', 'cu']
    elif kind.startswith('duo') or kind.startswith('split'):
        order = ['med', 'cu', 'wide']
    else:
        order = ['med', 'wide', 'cu']

    def pref(c):
        s = float(c.get('taste', c.get('score', 0.5)) or 0.5)
        fl = float(c.get('_flow') or 0.3)
        tags = set(c.get('tags') or [])
        mot = float(c.get('motion') or 0.5)
        pretty = float(c.get('pretty') or 0.5)
        luma = float(c.get('luma') or 100.0)
        if chorus:
            s += 0.9 * min(1.0, fl / 2.0) + 0.3 * mot       # the reference's chorus footage moves 1.5-3.7 % W / frame
        elif 'calm' in kind or 'hand' in kind or 'dark' in kind:
            s += 0.2 * (1 - min(1.0, fl / 1.5))
        if 'happy' in kind and ('cute' in tags or (c.get('scene') or '') in ('smile', 'laugh', 'wink')):
            s += 0.3
        if 'action' in kind:
            s += 0.6 * mot
        if 'dark' in kind:
            s += 0.25 * (1 - min(1.0, luma / 120.0))
        if 'bright' in kind:
            s += 0.25 * min(1.0, luma / 140.0)
        if 'pretty' in kind or kind.startswith('cu'):
            s += 0.3 * pretty - (0.5 if pretty < 0.5 else 0.0)
        if 'hand' in kind:
            s += 0.2 * (1 - mot)
        elif not c.get('faces') and 'action' not in kind and 'wide' not in kind:
            s -= 0.4
        if 'duo' in kind and int(c.get('faces') or 0) >= 2:
            s += 0.4
        return s
    return order, pref


def assign(clips, needs, rng):
    used, out, by_src = set(), {}, {}
    for slot, (kind, need, chorus) in needs.items():
        order, pref = pref_for(kind, chorus)
        best = None
        for b in order:
            cands = [c for c in clips if bucket(c) == b and c['path'] not in used and float(c.get('duration', 0)) >= need]
            if cands:
                best = max(cands, key=lambda c: pref(c) - 0.25 * by_src.get(c.get('source_id'), 0) + rng.uniform(0, 0.05))
                break
        if best is None:
            cands = [c for c in clips if c['path'] not in used] or clips
            best = max(cands, key=lambda c: pref(c) - 0.25 * by_src.get(c.get('source_id'), 0))
        used.add(best['path'])
        by_src[best.get('source_id')] = by_src.get(best.get('source_id'), 0) + 1
        out[slot] = best
    return out


def speed_for(spd, ramp, dur, post):
    if post:
        if ramp == 'slow->fast' and dur >= 0.4:
            return dict(speed='velocity', rate=1.0, velocity=dict(kind='u_shape', lo=0.8, hi_in=0.8, hi_out=1.8, tau_in=0.05, tau_out=0.15))
        return dict(speed='normal', rate=1.0)
    if spd == 'still':
        return dict(speed='slowmo', rate=0.3)
    base = {'smooth_slow': 0.5, 'smooth': 0.75, 'native': 1.0}.get(spd, 1.0)
    if dur < 0.4:
        return dict(speed='normal', rate=round(max(base, 1.0), 2))
    if ramp == 'fast->slow':
        return dict(speed='velocity', rate=base, velocity=dict(kind='u_shape', lo=0.55, hi_in=1.6, hi_out=1.0, tau_in=0.12, tau_out=0.1))
    if ramp == 'dip':
        return dict(speed='ramp', ramp='dip', rate=base)
    if ramp == 'slow->fast':
        return dict(speed='velocity', rate=base, velocity=dict(kind='u_shape', lo=0.6, hi_in=0.6, hi_out=1.9, tau_in=0.05, tau_out=0.15))
    if spd in ('smooth_slow', 'smooth'):
        return dict(speed='slowmo', rate=base)
    return dict(speed='normal', rate=1.0)


# ------------------------------------------------------------------------------------------ camera

def camera_keys(inc, dur, fps=30.0):
    """The reference's per-frame increments resampled onto a shot of `dur` seconds (the shape is stretched in time,
    the per-frame speed kept), capped to what our plates take; returns (keys, zoom headroom)."""
    n_new = max(1, int(round(dur * fps)))
    inc = np.asarray(inc, dtype=float).reshape(-1, 4) if len(inc) else np.zeros((1, 4))
    src = np.linspace(0, len(inc) - 1, n_new)
    rs = np.stack([np.interp(src, np.arange(len(inc)), inc[:, j]) for j in range(4)], axis=1) * (30.0 / fps)
    cum = np.vstack([np.zeros((1, 4)), np.cumsum(rs, axis=0)])
    arr = np.column_stack([np.arange(len(cum)) / fps, cum])
    lo, hi = float(arr[:, 1].min()), float(arr[:, 1].max())
    dl = np.diff(arr[:, 1])
    osc = min(dl[dl > 0].sum(), -dl[dl < 0].sum()) / max(1e-6, np.abs(dl).sum()) if len(dl) else 0.0
    if hi - lo > LN_CAP and len(arr) > 12 and osc > 0.25:
        # round 3: an in / out punch sequence (i4f 11.0-13.6: +-8-10 % per frame on every beat) must keep its per-frame
        # speed: take out the slow drift of the path (0.5 s moving average) before any scaling
        w = 15
        pad = np.concatenate([np.full(w, arr[0, 1]), arr[:, 1], np.full(w, arr[-1, 1])])
        lp = np.convolve(pad, np.ones(2 * w + 1) / (2 * w + 1), mode='same')[w:-w]
        arr[:, 1] = arr[:, 1] - lp + lp[0]
        lo, hi = float(arr[:, 1].min()), float(arr[:, 1].max())
    if hi - lo > LN_CAP:
        arr[:, 1] *= LN_CAP / (hi - lo)
    for j, cap in ((2, TX_CAP), (3, TY_CAP), (4, ROT_CAP)):
        m = float(np.abs(arr[:, j]).max())
        if m > cap:
            arr[:, j] *= cap / m
    zoom = 1.0
    for _ in range(4):
        need = np.exp(-arr[:, 1]) * np.maximum(1 + 2.0 * np.abs(arr[:, 2]), 1 + 3.56 * np.abs(arr[:, 3])) * (1 + 0.012 * np.abs(arr[:, 4]))
        zoom = float(max(1.0, need.max())) * 1.01
        if zoom <= ZOOM_CAP:
            break
        arr[:, 1:5] *= math.log(ZOOM_CAP) / math.log(zoom)
    keys = [[round(float(r[0]), 4), round(float(r[1]), 5), round(float(r[2]), 5), round(float(r[3]), 5), round(float(r[4]), 3)] for r in arr]
    return keys, zoom


def synth_keys(dur, rng, fps=30.0, v=0.012):
    """A hype punch for a shot without a valid reference path: fastest at +100 ms, decaying (general-av 3.2)."""
    n = int(round(dur * fps))
    k = np.arange(n, dtype=float)
    shape = np.where(k < 3, 0.4 + 0.6 * k / 3.0, np.exp(-(k - 3) / 5.0)) + 0.3
    sgn = 1.0 if rng.random() < 0.7 else -1.0
    inc = np.column_stack([sgn * v * shape / 0.29, v * 0.3 * shape * (1 if rng.random() < 0.5 else -1), np.zeros(n), np.zeros(n)])
    return camera_keys(inc.tolist(), dur, fps)


# ------------------------------------------------------------------------------------------ timing

def pick_drop(A, P, pre_beats=36.0, post_beats=38.0, want=None):
    """The song's drop the edit is built around: a 'drop' / 'chorus' section start with the biggest loudness jump that
    leaves room for the intro before it and the two chorus blocks after it."""
    dur = float(A.get('duration') or 0)
    if want is not None:
        return float(want)
    cands = []
    for s in A.get('sections') or []:
        st = float(s['start'])
        if st - pre_beats * P < 0.3 or st + post_beats * P > dur:
            continue
        sc = float(s.get('jump') or 0) + (3.0 if s.get('label') in ('drop', 'chorus') else 0.0) + 2.0 * float(s.get('energy') or 0)
        cands.append((sc, st))
    for d in A.get('drops') or []:
        st = float(d['t'] if isinstance(d, dict) and 't' in d else (d.get('start') if isinstance(d, dict) else d))
        if st - pre_beats * P >= 0.3 and st + post_beats * P <= dur:
            cands.append((6.0, st))
    if not cands:
        return min(max(pre_beats * P + 0.3, dur * 0.4), max(0.3, dur - post_beats * P))
    return max(cands)[1]


class BeatMap:
    """Reference seconds -> song seconds through the beat grid, anchored at the drops."""

    def __init__(self, beats, drop_song, P_new, P_ref, drop_ref):
        b = np.asarray(beats, dtype=float)
        if len(b) < 4:
            b = np.arange(0.0, drop_song + 200 * P_new, P_new)
        self.b = b
        self.i0 = float(np.interp(drop_song, b, np.arange(len(b))))
        self.P_new, self.P_ref, self.drop_ref = P_new, P_ref, drop_ref

    def song(self, t_ref):
        k = self.i0 + (t_ref - self.drop_ref) / self.P_ref
        n = len(self.b)
        if k < 0:
            return float(self.b[0] + k * self.P_new)
        if k > n - 1:
            return float(self.b[-1] + (k - n + 1) * self.P_new)
        return float(np.interp(k, np.arange(n), self.b))


def _norm(s):
    return re.sub(r"[^a-z]", '', str(s).lower())


STOP = {'a', 'an', 'the', 'to', 'of', 'and', 'or', 'in', 'on', 'at', 'is', 'it', 'be', 'i', 'im', 'my', 'me', 'you', 'your',
        'oh', 'uh', 'ah', 'yeah', 'la', 'na', 'ooh', 'for', 'with', 'so', 'but', 'that', 'this', 'its', 'do', 'dont'}


def key_word_index(ws, t_lo, t_hi, used):
    """Index of the best word to show sung in [t_lo, t_hi): content words first, then longer ones."""
    best, bs = None, -1e9
    for i, w in enumerate(ws):
        t = float(w['t_start'])
        if not (t_lo <= t < t_hi) or i in used:
            continue
        n = _norm(w.get('text'))
        if len(n) < 2:
            continue
        sc = len(n) * 0.2 + (0.0 if n in STOP else 1.5) - 0.3 * (t - t_lo)
        if sc > bs:
            best, bs = i, sc
    return best


# ------------------------------------------------------------------------------------------ the planner

def plan_hype_i4f(A, shots, opts):
    T = load_template()
    rng = random.Random(opts.get('seed'))
    fps = int(opts.get('fps') or 30)
    aspect = opts.get('aspect') or '16:9'
    P_ref, drop_ref, dur_ref = float(T['P']), float(T['drop']), float(T['duration'])
    beats_all = [float(b) for b in A.get('beats') or []]
    P = float(A.get('beat_period') or (np.median(np.diff(beats_all)) if len(beats_all) > 2 else 0.5))
    # felt pulse: fold very fast / slow grids into the reference's 100-140 BPM feel
    while P < 0.42:
        P *= 2.0
    while P > 0.75:
        P /= 2.0
    step = max(1, int(round(P / float(A.get('beat_period') or P))))
    beats = beats_all[::step] if step > 1 else beats_all
    short = float(opts.get('length') or 99) < 25.0
    drop_song = pick_drop(A, P, pre_beats=(13.5 if short else 36.0), want=opts.get('drop_at'))
    # snap to the nearest downbeat
    downs = [float(d) for d in A.get('downbeats') or []]
    if downs and opts.get('drop_at') is None:
        dn = min(downs, key=lambda d: abs(d - drop_song))
        if abs(dn - drop_song) < 2 * P:
            drop_song = dn
    bm = BeatMap(beats, drop_song, P, P_ref, drop_ref)
    t_ref0 = 11.033 if short else 0.0
    L_want = float(opts.get('length') or 0)
    if not short and L_want >= 25.0 and bm.song(dur_ref) - bm.song(0.0) > L_want + 0.3:
        # a slower song stretches the i4f structure: start later in the intro so the edit keeps the wanted length
        for r in T['rows']:
            if r['slot'] and bm.song(dur_ref) - bm.song(r['t0']) <= L_want + 0.3:
                t_ref0 = r['t0']
                break
    ms = bm.song(t_ref0)
    if ms < 0:
        ms = 0.0

    def M(t_ref):                       # reference seconds -> output seconds of the new edit
        return round(bm.song(t_ref) - ms, 4)

    rows = [r for r in T['rows'] if r['t0'] >= t_ref0 - 1e-6]
    DUR = M(dur_ref)
    cuts_all = sorted(set(M(r['t0']) for r in rows if M(r['t0']) > 0))

    # clips
    clips = [dict(c) for c in shots if c.get('path') and os.path.exists(c['path'])]
    if len(clips) < 6:
        raise RuntimeError(f'hype_i4f: only {len(clips)} usable clips')
    for c in clips:
        cd = float(c.get('duration') or 2.0)
        pk = float(c.get('peak') or cd * 0.4)
        try:
            c['_flow'] = clip_flow(c['path'], max(0.0, pk - 0.5), min(cd, pk + 0.6))
        except Exception:  # noqa: BLE001
            c['_flow'] = 0.3
    needs = {}
    for r in rows:
        if not r['slot']:
            continue
        t0, t1 = M(r['t0']), M(r['t1'])
        post = r['t0'] >= drop_ref - 0.01
        sp = speed_for(r.get('speed'), r.get('ramp'), t1 - t0, post)
        d = (t1 - t0) * sp.get('rate', 1.0) * 1.2 + 0.7
        k0, need0, ch0 = needs.get(r['slot'], (r['kind'], 0.0, False))
        chorus = ch0 or (post and not r['extra'].get('layout') and 'calm' not in r['kind'])
        needs[r['slot']] = (r['kind'], need0 + d if r['extra'].get('cont') else max(need0, d), chorus)
    for slot in [s for s, v in needs.items() if v[0] == 'split']:
        needs[slot + '_p'] = ('cu', needs[slot][1], False)
        needs[slot] = ('cu', needs[slot][1], False)
    excl = [x.replace(chr(92), '/') for x in (opts.get('exclude') or [])]
    if excl:
        clips = [c for c in clips if not any(c['path'].replace(chr(92), '/').endswith(x) for x in excl)]
    picked = assign(clips, needs, rng)
    for slot, suf in (opts.get('slots') or {}).items():      # hand-picked clips (the remake)
        hit = [c for c in clips if c['path'].replace(chr(92), '/').endswith(suf)]
        if hit:
            picked[slot] = hit[0]

    shots_out, src_pos = [], {}
    for r in rows:
        if not r['slot']:
            continue
        c = picked[r['slot']]
        t0, t1 = max(0.0, M(r['t0'])), M(r['t1'])
        if t1 - t0 < 0.5 / fps:
            continue
        post = r['t0'] >= drop_ref - 0.01
        sp = speed_for(r.get('speed'), r.get('ramp'), t1 - t0, post)
        if post and sp.get('speed') == 'normal' and float(c.get('_flow') or 0.3) < CHORUS_FAST_BELOW:
            # the reference's chorus footage moves 1.5-3.7 % W / frame; a slow (Twixtor) plate plays faster
            sp = dict(speed='normal', rate=CHORUS_RATE)
        rate = sp.get('rate', 1.0)
        need = (t1 - t0) * rate + 0.25
        cd = float(c.get('duration') or 4.0)
        peak = float(c.get('peak') or cd * 0.4)
        slot = r['slot']
        if r['extra'].get('cont') and slot in src_pos and shots_out:
            src_in = src_pos[slot]
        elif r['extra'].get('replay') and slot + '_first' in src_pos:
            src_in = src_pos[slot + '_first']
        else:
            src_in = max(0.0, min(peak - 0.35 * need, cd - need - 0.15))
            src_pos[slot + '_first'] = src_in
        # fit the clip: move the in point back, then slow the plate down (never past its end, never a freeze)
        dur = t1 - t0
        curve = None
        if src_in + dur * rate > cd - 0.04:
            src_in = max(0.0, cd - 0.04 - dur * rate)
            if src_in + dur * rate > cd - 0.04:
                rate = max(0.3, (cd - 0.04) / max(dur, 1e-3))
                src_in = max(0.0, cd - 0.04 - dur * rate)
        if src_in + dur > cd + 0.02:
            n = max(2, int(math.ceil(dur * fps)) + 1)
            curve = [[round(min(dur, k / fps), 4), round(min(cd - 0.02, src_in + min(dur, k / fps) * rate), 4)] for k in range(n)]
            sp = dict(speed='normal', rate=round(rate, 3))
        src_pos[slot] = min(cd - 0.3, src_in + dur * rate)
        if r['cam_valid'] >= 0.5 and (t1 - t0) >= 0.3:
            keys, zoom = camera_keys(r['cam_inc'], t1 - t0, fps)
            mode = 'ref'
        else:
            keys, zoom = synth_keys(t1 - t0, rng, fps, v=0.035 if post else 0.012)
            mode = 'synth'
        acc = [dict(at=round(t0 + 0.02, 3), kind='shake', amount=1.2)] if r['extra'].get('shake') else 'none'
        tr, tp = r['transition'], dict(r['params'])
        if tr in ('flash_white', 'invert_strobe', 'dip_black_strobe') and r['t0'] < 33.8:
            tp = dict(frames=min(3, int(tp.get('frames') or 1)), invert=(tr == 'invert_strobe'))
            tr = 'flash_smear'
        if r['extra'].get('layout') in ('split2', 'card3d'):
            tp['white_cap'] = 0.83
        if not shots_out:
            tr, tp = 'hard', {}
        rb, _lum = clip_stats(c['path'], src_in + 0.3)
        look = T['grade']['post' if post else 'pre'] + (T['grade']['warm_suffix'] if rb < WARM_T else '')
        sh = dict(path=c['path'], t0=round(t0, 4), t1=round(t1, 4), src_in=round(src_in, 3), transition_in=tr,
                  transition_params=tp, layout=r['extra'].get('layout', 'fill'), interp='flow',
                  camera=dict(move='static', amount=0.0, pan=[0.0, 0.0], roll=0.0, zoom=round(zoom, 4), handheld=0.0,
                              bounce=0.0, follow=False, accents=acc, keys=keys),
                  handover=False, look=look, source_id=c.get('source_id'), source_title=c.get('source_title'),
                  source_channel=c.get('source_channel'), clip_duration=cd, role='drop' if post else 'intro',
                  ref=dict(kind=r['kind'], slot=slot, ref_t0=r['t0'], cam_mode=mode))
        if r['extra'].get('layout') == 'split2':
            pc = picked.get(slot + '_p') or c
            sh['panels'] = [dict(path=pc['path'], src_in=round(max(0.0, float(pc.get('peak') or 0.5) - 0.3), 3), rate=rate)]
        if r['extra'].get('layout') == 'card3d':
            sh['card3d'] = dict(legacy=False)
        sh.update(sp)
        if curve:
            sh['speed_curve'] = curve
        shots_out.append(sh)
    shots_out[0]['t0'] = 0.0
    # the black gap before the drop and the black logo tail are overlay scenes (text_on_black) over the previous
    # shot: the shot list itself stays continuous
    for a_, b_ in zip(shots_out[:-1], shots_out[1:]):
        a_['t1'] = b_['t0']
    shots_out[-1]['t1'] = round(DUR, 4)
    for sh in shots_out:            # a shot stretched under a black scene reads its clip slower, never past the end
        d = sh['t1'] - sh['t0']
        cd = float(sh.get('clip_duration') or 0)
        if cd and sh['src_in'] + d > cd + 0.02 and not sh.get('speed_curve'):
            r_ = max(0.05, min(float(sh.get('rate') or 1.0), (cd - 0.04 - sh['src_in']) / max(d, 1e-3)))
            n = max(2, int(math.ceil(d * fps)) + 1)
            sh['speed_curve'] = [[round(min(d, k / fps), 4), round(sh['src_in'] + min(d, k / fps) * r_, 4)] for k in range(n)]
        elif sh.get('speed_curve') and sh['speed_curve'][-1][0] < d - 1e-3:
            cv = sh['speed_curve']
            r_ = (cv[-1][1] - cv[0][1]) / max(cv[-1][0], 1e-3) if len(cv) > 1 else 0.5
            last = cv[-1]
            room = max(0.0, cd - 0.03 - last[1]) if cd else 1e9
            extra = d - last[0]
            r2 = min(r_, room / max(extra, 1e-3))
            cv.append([round(d, 4), round(last[1] + extra * r2, 4)])

    # ------------------------------------------------------------------ overlays (reference geometry, new words)
    chan = (opts.get('watermark') or opts.get('channel') or 'ZERO YEN OTAKU').upper()
    song = opts.get('song') or opts.get('title') or ''
    artist = opts.get('artist') or ''
    show = (opts.get('caption') or opts.get('subtitle') or '').upper()
    stub = dict(music=A.get('path'), music_start=ms, duration=DUR)
    ws = opts.get('_sung_words')
    if ws is None and song:
        try:
            import lyric_text as LT
            ws = LT.sung_words(stub, song, artist)
        except Exception as e:  # noqa: BLE001
            log(f'sung words unavailable: {e}')
            ws = []
    ws = [w for w in (ws or []) if w.get('text')]

    def line_at(t):
        if not ws:
            return ''
        i = int(np.argmin([abs(float(w['t_start']) - t) for w in ws]))
        lid = ws[i].get('line_id')
        return ' '.join(w['text'] for w in ws if w.get('line_id') == lid) if lid is not None else ws[i]['text']

    ovs = []

    def add(t_ref, o):
        if t_ref >= t_ref0 - 1e-6:
            ovs.append(o)
    if not short:
        ln = line_at(M(0.6))
        if ln:
            add(0.27, dict(type='text', text=ln, style='lyric', size=0.10 / 0.72 * 0.85, x=0.33, y=0.52, anim='type', cps=20,
                           t0=M(0.27), t1=M(1.62), color=(244, 251, 255)))
        add(1.62, dict(type='particles', kind='sparkles', n=40, opacity=0.6, t0=M(1.62), t1=M(2.6)))
        add(1.62, dict(type='light_leak', t0=M(1.62), t1=M(2.1), x=0.5, y=0.35))
        if show:
            add(2.62, dict(type='text', text=show[:28], style='wide_thin', size=0.108 / 0.72 * 0.7, x=0.37, y=0.726, anim='type',
                           cps=14, t0=M(2.62), t1=M(4.333), color=(250, 246, 252)))
        if song:
            card_clip = picked.get('happy') or picked.get('maid')
            add(4.333, dict(type='music_card', t0=M(4.333), t1=M(5.4), title=song, artist=artist, line=line_at(M(4.5)),
                            channel=chan.title(), cards=[dict(path=card_clip['path'], clip_t=float(card_clip.get('peak') or 1.0))]))
            add(6.87, dict(type='text', text=song.upper()[:22], style='wide_thin', size=0.20 / 0.72 * min(1.0, 12.0 / max(6, len(song))),
                           x=0.5, y=0.5, anim='letters_fly', stagger=0.045, t0=M(6.87), t1=M(8.033), color=(230, 218, 237)))
        add(8.4, dict(type='text', text='FROM ' + chan, style='lyric', size=0.12 / 0.72, x=0.42, y=0.565, anim='type', cps=14,
                      t0=M(8.4), t1=M(10.2), color=(121, 157, 204)))
        add(10.33, dict(type='text', text=' '.join(chan.split()[:2]), style='lyric_hype', size=0.23 / 0.72, x=0.47, y=0.55,
                        anim='slam', t0=M(10.33), t1=M(11.033), color=PEACH))
    add(14.33, dict(type='text', text='@' + chan.replace(' ', '').lower(), style='tiny', size=0.044 / 0.98, x=0.527, y=0.944,
                    anim='type', cps=20, t0=M(14.33), t1=M(15.467)))
    add(16.0, dict(type='text', text=chan, style='lyric', size=0.05 / 0.72, x=0.65, y=0.833, anim='fade', t0=M(16.0), t1=M(17.07),
                   color=(66, 83, 102)))
    add(16.3, dict(type='text', text='BEST DUO' if len({c.get('source_title') for c in picked.values()}) > 1 else 'BEST SCENES',
                   style='lyric', size=0.064 / 0.72, x=0.497, y=0.876, anim='type', cps=16, t0=M(16.3), t1=M(17.07)))
    add(16.93, dict(type='fade', t0=M(16.93), t1=M(17.07), color='black', dir='out'))
    add(17.07, dict(type='text_on_black', t0=M(17.07), t1=M(17.7), text='', words=[], style='wide_thin', leak=False))
    for tt in (17.733, 25.8):
        add(tt, dict(type='text', text=chan.split()[0], style='lyric_hype', size=0.30 / 0.72, x=0.5, y=0.6, anim='letters_3d',
                     t0=M(tt), t1=M(tt + 0.47), color=CREAM))
    for tt in (22.25, 30.317):
        add(tt, dict(type='fade', t0=M(tt), t1=M(tt + 0.45), color='black', dir='out'))
    add(33.9, dict(type='text_on_black', t0=M(33.9), t1=DUR, text=chan, style='wide_thin', size_px=int(0.075 * 1080 / 0.72 * 0.5), leak=False))

    # sung words: the reference's key-word slots before the drop, then 1 word per chorus shot on its syllable
    items_c, items_i, used = [], [], set()
    geo = [g for g in T['lyric_items'] if g['t'] >= drop_ref]
    pre = [g for g in T['lyric_items'] if g['t'] < drop_ref]
    for g in pre:
        if g['t'] < t_ref0:
            continue
        i = key_word_index(ws, M(g['t']) - 0.45, M(g['t']) + 0.45, used)
        if i is not None:
            used.add(i)
            (items_i if g['ink'] else items_c).append((i, g, 1))
    k = 0
    chorus_shots = [s for s in shots_out if s['t0'] >= M(drop_ref) - 0.01 and s['t1'] - s['t0'] >= 0.3 and s['t0'] < M(33.8)]
    for s in chorus_shots:
        i = key_word_index(ws, s['t0'] - 0.08, s['t1'] - 0.15, used)
        if i is None:
            continue
        used.add(i)
        g = geo[k % len(geo)]
        k += 1
        n = 2 if (g['n'] >= 2 and i + 1 < len(ws) and float(ws[i + 1]['t_start']) < s['t1'] - 0.1) else 1
        (items_i if g['ink'] else items_c).append((i, g, n))
    starts = sorted(float(ws[i]['t_start']) for i, _, _ in items_c + items_i)
    for items, st in ((items_c, 'lyric_hype_wide'), (items_i, 'lyric_hype_wide_ink')):
        its = []
        for i, g, n in sorted(items, key=lambda x: x[0]):
            t0 = float(ws[i]['t_start'])
            nc = min([c for c in cuts_all if c > t0 + 0.12], default=DUR)
            nxt = min([x for x in starts if x > t0 + 0.05], default=DUR + 1.0)
            # one word on screen at a time (the reference replaces a word, it never stacks two big ones)
            end = min(nc, t0 + 1.6, nxt - 0.02)
            text = ' '.join(w['text'] for w in ws[i:i + n])
            it = dict(text=text, sung=round(t0, 4), t0=round(t0, 4), x=g['x'], y=g['y'], box=round(g['h'] * 0.72, 4),
                      anim_in='spread', dur_in=round(rng.uniform(0.20, 0.30), 3), anim_out='cut', dur_out=0.1,
                      scale0=round(rng.uniform(1.0, 1.3), 3), rot0=0.0, lyric=True,
                      t_out=round(end - 0.05, 4), t1=round(min(DUR, end + 0.05), 4), word_index=i)
            if g['h'] >= 0.6:
                it.update(scale0=1.6, dur_in=0.25)
            its.append(it)
        if its:
            _ov = dict(type='lyric_text', t0=min(i['t0'] for i in its), t1=max(i['t1'] for i in its),
                       preset='hype_typography', style=st, look={}, items=its)
            # 2026-10-01 text style library (refs/text-animation.md): new-song edits pick one hype_typography text recipe
            # per edit (the same pick for the clean and the ink track); opts['text_recipe'] forces one, 'none' keeps i4f's
            try:
                if opts.get('text_recipe') != 'none' and not opts.get('slots'):     # hand-picked slots = the remake: keeps i4f's look
                    import text_fx as TFX
                    TFX.vary(_ov, 'hype_typography', opts.get('seed'), forced=opts.get('text_recipe'))
            except Exception:  # noqa: BLE001
                pass
            ovs.append(_ov)

    # ------------------------------------------------------------------ song edits (general-av 2.4, in beats)
    def sf(t_ref):
        return M(t_ref)
    song_fx = []
    if not short:
        song_fx += [dict(op='duck', t0=sf(1.5), t1=sf(1.68), depth_db=-15.7, attack=0.02, release=0.02),
                    dict(op='fade', t0=sf(1.68), t1=sf(3.3), db0=-15.7, db1=0.0, then_out=False),
                    dict(op='swell', cut=sf(11.033), depth_db=7.0, dur=0.44)]
    song_fx += [dict(op='swell', cut=sf(13.667), depth_db=7.0, dur=0.6),
                dict(op='riser', drop=sf(17.7), dur=0.7, bb_db=10.0, low_mix=0.4, seed=3),
                dict(op='impact', at=round(sf(17.7) + 0.08, 3), cut=sf(17.7), anchor=False, t_peak_db=18.0, bright=2.0, drive=4.0,
                     dur=0.3, intensity=0.3, tail_tau=0.05, seed=4),
                dict(op='lowpass', t_close=sf(21.83), close_s=0.06, t_open=sf(23.24), open_s=0.34, hi_db=-26.0, mid_db=-16.0),
                dict(op='swell', cut=sf(23.133), depth_db=6.7, dur=0.58),
                dict(op='lowpass', t_close=sf(29.90), close_s=0.06, t_open=sf(31.31), open_s=0.34, hi_db=-26.0, mid_db=-16.0),
                dict(op='swell', cut=sf(31.2), depth_db=6.7, dur=0.58),
                dict(op='swell', cut=sf(32.3), depth_db=6.8, dur=0.54),
                dict(op='fade', t0=sf(34.4), t1=DUR, db0=0.0, db1=-45.0, then_out=True)]
    if t_ref0 > 0:
        song_fx.insert(0, dict(op='fade', t0=0.0, t1=0.25, db0=-20.0, db1=0.0, then_out=False))
    if opts.get('legacy_transition_sound'):
        for c in cuts_all:
            if M(drop_ref) - 0.01 <= c < DUR - 3.0:
                song_fx.append(dict(op='whoosh5k', cut=round(c, 3), band_db=12.0, peak_after=round(rng.uniform(0.07, 0.17), 3),
                                    seed=int(c * 100) % 997))
    else:
        # 2026-09-29 stem study (refs/transition-sound.md): the per-cut whoosh is not in i4f; what IS added are 808 sub
        # booms after the four blur-bridge cuts that open the held verse shots (reference 5.60 / 8.033 / 11.033 / 13.667 s)
        try:
            import song_fx as SFX2
            for k, t_ref in enumerate((5.60, 8.033, 11.033, 13.667)):
                tb = M(t_ref)
                if 0.3 < tb < DUR - 1.0:
                    song_fx += SFX2._tsnd(SFX2.TRANSITION_SOUND['i4f']['verse_boom'], round(tb, 3), 500 + k)
        except Exception as e:  # noqa: BLE001
            log(f'i4f verse booms skipped: {e}')
    song_fx = [o for o in song_fx if all((not isinstance(v, float)) or v >= -0.01 for kk, v in o.items() if kk in ('t0', 'cut', 'drop', 'at', 't_close'))]

    grid = [round(b - ms, 4) for b in beats if -0.01 <= b - ms <= DUR + 0.5]
    dgrid = [round(b - ms, 4) for b in downs if -0.01 <= b - ms <= DUR + 0.5]
    be = A.get('beat_energy') or []
    energy = []
    for b in grid:
        j = int(np.argmin(np.abs(np.array(beats_all) - (b + ms)))) if beats_all else 0
        energy.append(round(float(be[j]) if j < len(be) else 0.7, 3))
    plan = dict(aspect=aspect, fps=fps, duration=round(DUR, 3), music=A.get('path'), music_start=round(ms, 4),
                clip_audio='none', sfx='none', audio_design='none', song_fx=song_fx, fade_out=0.0,
                style='hype_flash', look='show_palette', frame='full', preset='hype_typography', group='general',
                template='hype_typography', seed=opts.get('seed'), lead='none', handover=False, drive=False, particles=False,
                style_overrides=dict(end=dict(kind='cut', dur=0.0), audio=dict(lufs=-8.0, ceiling_db=-1.0),
                                     text=dict(title_when='none'), camera=dict(handheld=0.0, phrase_shake=0.0, entry_roll=0.0)),
                beats=grid, downbeats=dgrid, energy=energy, drop=M(drop_ref), beat_period=P,
                shots=shots_out, overlays=ovs,
                planner=dict(type='song', template='hype_typography', structure='i4f', reference=T['reference'], P=P,
                             drop_song=round(drop_song, 3), short=short, lyric_text_done=bool(items_c or items_i),
                             lyric_text=dict(preset='hype_typography', track='i4f', items=len(items_c) + len(items_i)),
                             slots={s: os.path.basename(c['path']) for s, c in picked.items()}))
    if aspect == '16:9':
        plan.update(width=1920, height=1080)
    return plan
