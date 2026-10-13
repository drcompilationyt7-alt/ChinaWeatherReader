#!/usr/bin/env python3
"""
blur_flow 's2rz' variant: S2rzMsl0N7k (the owner's 3D-camera example) turned into rules, plus the text helpers the
remake and the planner share.  Remake round 3 (core/edit/refs/remake-blur_flow-S2rz.md).

Helpers (used by the remake generator and by plan_flow_s2rz):
  vocal_onsets(song, a, b)            harmonic-band onsets of the song's vocal stem (s of the song file)
  snap_items(items, onsets, ...)      each word's first ink onto the nearest sung onset within +-150 ms, 30 ms lead
  ink_width(style, text, box, W, H)   the measured ink width of a word (text.render), canvas fractions
  clamp_items(items, style, W, H)     the whole word stays inside 0.04-0.96 W
  face_boxes(plan, times, ...)        anime face boxes on the rendered picture (engine --dump without text)
  place_off_faces(items, faces, ...)  a word never sits on a face: moved to the nearest face-free spot
  pick_anims(items, cuts, rng)        every word gets an entry and an exit (spread / letter_streak / ring_flare,
                                      blur_out / zoom_through); words alive across a whip / zoom-through take its blur

The planner (plan_flow_s2rz) maps the remake's shot table (refs/blur_flow_s2rz_template.json, made by
make_template) onto any song through its beat grid, anchored on the drop - the same way hype_i4f.py does for i4f.
"""
import json
import math
import os
import random
import re
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
TEMPLATE = os.path.join(HERE, 'refs', 'blur_flow_s2rz_template.json')


def log(msg):
    print(f'[flow_s2rz] {msg}', file=sys.stderr, flush=True)


# ------------------------------------------------------------------------------------------ text timing / geometry

def vocal_onsets(song, a, b, threads=4):
    """Harmonic-band onsets (250-3500 Hz log flux of the HPSS harmonic part) of the vocal stem of song[a, b], in song
    seconds, with their prominences."""
    import lyrics as LY
    import lyric_check as LC
    v = LY.vocal_stem(song, float(a), float(b), threads)
    t, pr = LC.harmonic_onsets(v, 16000)
    return [(float(x) + float(a), float(p)) for x, p in zip(t, pr)]


def snap_items(items, onsets, offset=0.0, win=0.15, lead=0.03, min_prom=0.2, key='t0'):
    """Move each item's first ink onto the strongest sung onset within +-win of it (output seconds = song seconds
    - offset), `lead` s before it. The whole item moves (t_out / t1 shift with it). Returns the moves in ms."""
    on = [(t - offset, p) for t, p in onsets if p >= min_prom]
    moves = []
    for it in items:
        t0 = float(it[key])
        c = [(abs(t - (t0 + lead)), -p, t) for t, p in on if abs(t - (t0 + lead)) <= win]
        if not c:
            continue
        # nearest first, the stronger one on a near tie (within 40 ms)
        c.sort()
        best = c[0]
        for d, np_, t in c[1:]:
            if d - best[0] < 0.04 and np_ < best[1]:
                best = (d, np_, t)
        nt = best[2] - lead
        dt = nt - t0
        for k in ('t0', 't_out', 't1'):
            if k in it:
                it[k] = round(float(it[k]) + dt, 3)
        it['snap_ms'] = round(dt * 1000)
        moves.append(round(dt * 1000))
    return moves


_INK = {}


def ink_width(style, text, box, W=1920, H=1080):
    """Measured ink width (fraction of W) of `text` drawn in `style` with a box height of `box` x H."""
    key = (style, text, round(box, 4), W, H)
    if key in _INK:
        return _INK[key]
    import text as T
    import lyric_text as LT
    size = box * H / LT._box_ratio(style, text)
    sp = T.render(text, style, size, max_w=int(0.96 * W), min_size=10)
    a = sp.img[..., 3] > 60
    cols = np.nonzero(a.any(axis=0))[0]
    w = (cols.max() - cols.min() + 1) / W if len(cols) else 0.0
    _INK[key] = float(w)
    return _INK[key]


def clamp_items(items, style, W=1920, H=1080, lo=0.04, hi=0.96, slide_pad=True):
    """Keep every word's whole measured ink inside [lo, hi] x W, including where its hold slide carries it; a word
    wider than the frame span is scaled down."""
    for it in items:
        box = float(it['box'])
        w = ink_width(style, it['text'], box, W, H) * 1.06           # the spread-in overshoot settles at 1.0-1.1
        if w > hi - lo:
            it['box'] = round(box * (hi - lo) / w, 4)
            w = hi - lo
        slide = 0.0
        if slide_pad and it.get('hold_slide'):
            tau = float(it.get('hold_tau', 0.35))
            dur = float(it['t1']) - float(it['t0'])
            slide = float(it['hold_slide'][0]) * tau * (1 - math.exp(-dur / tau))
        x = float(it['x'])
        x0, x1 = x - w / 2 + min(0.0, slide), x + w / 2 + max(0.0, slide)
        if x0 < lo:
            x += lo - x0
        elif x1 > hi:
            x -= x1 - hi
        it['x'] = round(x, 4)
    return items


# ------------------------------------------------------------------------------------------ faces

_FACES = None


def _face_model():
    global _FACES
    if _FACES is None:
        import library as LIB
        p = LIB.model_path('anime_face')
        _FACES = LIB.AnimeFaces(p) if p else False
    return _FACES or None


def face_boxes(plan_path, times, work, cpus=4, engine=None, py=None):
    """{t: [(x0, y0, x1, y1), ...]} canvas fractions: the plan's picture at each t, rendered by the engine with no
    text overlays (engine --dump), read by the anime face detector."""
    import cv2
    plan = json.load(open(plan_path, encoding='utf-8'))
    plan['overlays'] = [o for o in plan.get('overlays') or [] if o.get('type') not in ('lyric_text', 'text')]
    os.makedirs(work, exist_ok=True)
    pp = os.path.join(work, 'faces_plan.json')
    json.dump(plan, open(pp, 'w', encoding='utf-8'))
    ts = sorted(set(round(float(t), 3) for t in times))
    dump = os.path.join(work, 'faces_dump')
    need = [t for t in ts if not os.path.exists(os.path.join(dump, f'f_{t:08.3f}.png'))]
    if need:
        cmd = [py or sys.executable, engine or os.path.join(HERE, 'engine.py'), '--plan', pp, '--out', os.path.join(work, 'x.mp4'),
               '--dump', dump, '--times', ','.join(f'{t:.3f}' for t in need), '--cpus', str(cpus)]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    det = _face_model()
    out = {}
    for t in ts:
        img = cv2.imread(os.path.join(dump, f'f_{t:08.3f}.png'))
        if img is None or det is None:
            out[t] = []
            continue
        out[t] = [(x, y, x + w, y + h) for x, y, w, h, c in det(img, conf=0.3)]
    return out


def place_off_faces(items, faces, style, W=1920, H=1080, cap_over_footage=0.20, grow=0.25, plate_win=()):
    """A word never sits on a face (the reference puts its words beside the subject or on plates). Items whose ink
    box meets a (grown) face box at its first ink or mid-hold move to the nearest face-free position (same row first,
    then the rows above / below); words over footage are capped at cap_over_footage x H. Returns the moved items."""
    moved = []
    for it in items:
        if any(a <= float(it['t0']) < b for a, b in plate_win):
            continue
        if float(it['box']) > cap_over_footage:
            it['box'] = cap_over_footage
        fs = []
        for t in (float(it['t0']) + 0.05, (float(it['t0']) + float(it['t1'])) / 2):
            fs += faces.get(round(t, 3), [])
        if not fs:
            continue
        gb = []
        for x0, y0, x1, y1 in fs:
            gw, gh = (x1 - x0) * grow, (y1 - y0) * grow
            gb.append((x0 - gw, y0 - gh, x1 + gw, y1 + gh))
        w = ink_width(style, it['text'], float(it['box']), W, H) * 1.06
        h = float(it['box']) * 1.3

        def hits(x, y):
            return any(x - w / 2 < b[2] and b[0] < x + w / 2 and y - h / 2 < b[3] and b[1] < y + h / 2 for b in gb)
        x, y = float(it['x']), float(it['y'])
        if not hits(x, y):
            continue
        best = None
        for yy in np.arange(0.14, 0.88, 0.02):
            for xx in np.arange(0.04 + w / 2, 0.96 - w / 2 + 1e-6, 0.01):
                if hits(xx, yy):
                    continue
                d = abs(xx - x) + 1.6 * abs(yy - y)
                if best is None or d < best[0]:
                    best = (d, xx, yy)
        if best is None:
            it['box'] = round(float(it['box']) * 0.7, 4)
            continue
        it['x'], it['y'] = round(float(best[1]), 4), round(float(best[2]), 4)
        it['moved_off_face'] = True
        moved.append(it)
    return moved


def pick_anims(items, shots, rng, hero_times=(), streak_times=(), ring_times=()):
    """S2rz's three designed entries + an exit for every word (critic round 3: 'words appear rather than being
    animated'): letter_streak where the reference streams letters in (streak_times) and on ~40 % of the others,
    ring_flare on the hero word (ring_times), spread otherwise; exit zoom_through when the word ends on a cut whose
    transition is a zoom-through / flash, blur_out otherwise; a word alive across a whip / zoom-through cut takes
    that cut's directional blur (mblur)."""
    cuts = [(float(s['t0']), s.get('transition_in') or 'hard') for s in shots[1:]]
    for it in items:
        t0 = float(it['t0'])
        if any(abs(t0 - x) < 0.25 for x in ring_times):
            it['anim_in'] = 'ring_flare'
            it['dur_in'] = 0.3
        elif any(abs(t0 - x) < 0.25 for x in streak_times) or rng.random() < 0.4:
            it['anim_in'] = 'letter_streak'
            it['dur_in'] = 0.2
            it['streak_from'] = round(rng.choice([-1, 1]) * rng.uniform(0.3, 0.42), 3)
            it['streak_frames'] = rng.choice([4, 5])
        else:
            it['anim_in'] = 'spread'
        t1 = float(it['t1'])
        end_cut = [tr for c, tr in cuts if abs(c - t1) < 0.08]
        if end_cut and end_cut[0] in ('zoom_through', 'flash_white'):
            it['anim_out'], it['dur_out'] = 'zoom_through', 0.1
        else:
            it['anim_out'], it['dur_out'] = 'blur_out', round(rng.uniform(0.1, 0.167), 3)
        it['t_out'] = round(max(t0 + 0.1, t1 - float(it['dur_out'])), 3)
        mb = []
        for c, tr in cuts:
            if t0 < c < t1 and tr in ('whip_slide', 'zoom_through'):
                mb.append([round(c - 0.1, 3), round(c + 0.12, 3), 0.05 if tr == 'whip_slide' else 0.035, 0.0])
        if mb:
            it['mblur'] = mb
    return items


# ------------------------------------------------------------------------------------------ template (from the remake)

TIME_KEYS = ('t0', 't1', 'cut', 'at', 'drop', 't_close', 't_open')
WEAK = {'with', 'that', 'this', 'when', 'what', 'they', 'them', 'from', 'have', 'just', 'like', 'youre', 'were', 'will',
        'your', 'into', 'then', 'there', 'their', 'cause', 'gonna', 'wanna', 'yeah', 'baby'}


def make_template(plan_path, out=TEMPLATE, t_ref0=12.333):
    """The remake plan (reference seconds) -> the preset's shot table: per shot its kind, transition + params, speed,
    look, plate kind, the camera path as per-frame increments (so it resamples onto any shot length), the clip slot it
    shares with other shots (the reference replays its own footage), the clip's framing; the lyric slots (geometry and
    animation, no words), the song edits and the picture overlays - everything from t_ref0 (the boy close-up that opens
    the short form) to the end."""
    p = json.load(open(plan_path, encoding='utf-8'))
    meta = {}
    for s in p['shots']:
        d = os.path.dirname(s['path'])
        mp = os.path.join(d, 'shots.json')
        if os.path.exists(mp) and d not in meta:
            m = json.load(open(mp, encoding='utf-8'))
            for c in (m.get('shots') if isinstance(m, dict) else m):
                meta[os.path.normcase(os.path.abspath(c['path']))] = c
            meta[d] = True
    rows = []
    for s in p['shots']:
        if s['t1'] <= t_ref0 + 1e-3:
            continue
        k = np.asarray(s['camera']['keys'], float)
        sl_ = float((s.get('ref') or {}).get('slide') or 0.0)      # round 5: the incoming slide (x0 e^(-t/0.15)) is a row parameter, not a pan
        if sl_:
            k = k.copy()
            k[:, 2] = k[:, 2] - sl_ * np.exp(-k[:, 0] / 0.15)
        n = max(2, int(round((s['t1'] - s['t0']) * 30)) + 1)
        u = np.linspace(0.0, s['t1'] - s['t0'], n)
        # round 5: 7-column keys carry the plane swing [.., yaw, pitch] (critics: perspective on every move)
        cum = np.stack([np.interp(u, k[:, 0], k[:, j]) for j in range(1, min(7, k.shape[1]))], axis=1)
        inc = np.diff(cum, axis=0)
        base = os.path.basename(s['path'])
        plate = 'wordplate' if base.startswith('wordplate') else ('embers' if base.startswith('embers') else
                                                                  ('hyper' if base.startswith(('hyperspace', 'intro_plate', 'star_card')) else None))
        c = meta.get(os.path.normcase(os.path.abspath(s['path']))) or {}
        rows.append(dict(t0=round(max(t_ref0, s['t0']), 4), t1=round(s['t1'], 4), kind=(s.get('ref') or {}).get('kind', ''),
                         transition=s['transition_in'] if s['t0'] >= t_ref0 - 1e-3 else 'hard',
                         params=s.get('transition_params') or {},
                         speed={k2: s[k2] for k2 in ('speed', 'rate', 'velocity', 'ramp') if k2 in s},
                         look=(None if str(s.get('look') or '').startswith(('s2rz_punch_x', 's2rz_night_x')) else s.get('look')), plate=plate,   # (the remake's per-shot luma looks are its own)
                         slot=None if plate else (((s.get('ref') or {}).get('kind') or '').replace(' ', '_')
                                                  if (s.get('ref') or {}).get('kind') in ('outro hero', 'end card')
                                                  else os.path.basename(os.path.dirname(s['path'])) + '/' + base),
                         framing=c.get('framing') or 'closeup', cam_inc=np.round(inc, 5).tolist(), slide=round(sl_, 4),
                         zoom=s['camera']['zoom'], interp=s.get('interp', 'flow'),
                         ref_effect=(s.get('ref') or {}).get('effect')))
    items = []
    for o in p['overlays']:
        if o.get('type') == 'lyric_text':
            for it in o['items']:
                items.append({k2: v for k2, v in it.items() if k2 not in ('text', 'snap_ms', 'moved_off_face')})
    ovs = [o for o in p['overlays'] if o.get('type') in ('light_streak', 'light_leak', 'letterbox') and o['t1'] > t_ref0]
    # round 6: the hook (dialogue shots + the star card before the entry) keeps its typed-caption geometry; the words are the
    # song's own sung words typed on their syllables (no dialogue clip needed)
    hook_text = [{k2: v for k2, v in o.items() if k2 != 'text'} for o in p['overlays']
                 if o.get('type') == 'text' and o.get('anim') == 'type' and o['t1'] > t_ref0 and o['t0'] < 12.4 and o['t0'] >= t_ref0 - 0.5]
    hook_fade = [o for o in p['overlays'] if o.get('type') == 'fade' and o.get('dir') == 'in' and o['t0'] >= t_ref0 - 0.1 and o['t0'] < 12.4]
    fx = [o for o in p.get('song_fx') or [] if max([float(o[k2]) for k2 in TIME_KEYS if k2 in o] or [0]) >= t_ref0]
    T = dict(reference='S2rzMsl0N7k', source_plan=os.path.basename(plan_path), P=0.625, drop=15.98, t_ref0=t_ref0,
             duration=float(p['duration']), look=p.get('look'),
             shutter=(p.get('style_overrides') or {}).get('camera', {}).get('shutter', 0.35),
             rows=rows, lyric_items=items, overlays=ovs, song_fx=fx, hook_text=hook_text, hook_fade=hook_fade,
             rules=('the remake copied shot for shot, as rules: the cut grid in beats (holds of 2-3 beats on quiet bars, one '
                    'per felt beat on loud bars, a 3-beat rest per 8-beat phrase, no halving before the drop), per-shot '
                    'camera paths (push-in / pull-out alternating, 8-22 %/frame peaks, pull-outs out of the flash bloom), '
                    'flash / blur bridge gain 1 + 0.5 e^(-t/0.1) gone by 200 ms, word / ember plates, lyric slots'))
    json.dump(T, open(out, 'w', encoding='utf-8'), indent=0)
    return T


# ------------------------------------------------------------------------------------------ clip quality (blur_flow pool)

_QC = {}


def clip_quality(path):
    """(mean luma, p98 luma, HSV saturation p90 0-1, Laplacian var at 480 px) of 3 frames of the clip."""
    if path in _QC:
        return _QC[path]
    import cv2
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    L, P98, S, SH = [], [], [], []
    for fi in ([int(n * q) for q in (0.2, 0.5, 0.8)] if n > 3 else [0]):
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, f = cap.read()
        if not ok:
            continue
        f = cv2.resize(f, (480, int(480 * f.shape[0] / f.shape[1])), interpolation=cv2.INTER_AREA)
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        L.append(g.mean())
        P98.append(np.percentile(g, 98))
        S.append(np.percentile(hsv[..., 1], 90) / 255.0)
        SH.append(cv2.Laplacian(g, cv2.CV_64F).var())
    q = (float(np.mean(L)) if L else 100.0, float(np.mean(P98)) if P98 else 200.0, float(np.mean(S)) if S else 0.5,
         float(np.median(SH)) if SH else 500.0)
    _QC[path] = q
    return q


_WARM = {}


def clip_warm(path):
    """mean R - mean B of 3 frames (0-255): > ~35 is an orange / sunset cast (round 5 critic: 'Rem in front of a burning, strongly orange backdrop')."""
    if path in _WARM:
        return _WARM[path]
    import cv2
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    v = []
    for fi in ([int(n * q) for q in (0.2, 0.5, 0.8)] if n > 3 else [0]):
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, f = cap.read()
        if ok:
            f = cv2.resize(f, (160, 90), interpolation=cv2.INTER_AREA).astype(np.float32)
            v.append(float(f[..., 2].mean() - f[..., 0].mean()))
    _WARM[path] = float(np.mean(v)) if v else 0.0
    return _WARM[path]


def pool_ok(c):
    """blur_flow pool gate (critics round 3): no blown / hot clips (mean luma > 175, or p98 > 250 with saturation p90
    > 0.8: the magenta concert washes), no murky ones (mean luma < 40), no soft ones (Laplacian < 400 at 480 px)."""
    L, p98, s90, sh = clip_quality(c['path'])
    if L < 40 or L > 175 or sh < 400:
        return False
    if p98 > 250 and s90 > 0.8:
        return False
    return True


# ------------------------------------------------------------------------------------------ the planner

def camera_check(shots, body_from=0.5):
    """Per-shot camera amplitude and direction (the critics' gate): ln-scale range per shot (p50 >= 0.25), the
    push-in / pull-out sequence, peak |d ln s| per frame (p50 / p90)."""
    rng_, net, pk = [], [], []
    for s in shots:
        if s['t0'] < body_from or 'plate' in str((s.get('ref') or {}).get('slot')) or 'embers' in str((s.get('ref') or {}).get('slot')):
            continue
        k = np.asarray(s['camera']['keys'], float)
        if len(k) < 2:
            continue
        ln = k[:, 1]
        rng_.append(float(ln.max() - ln.min()))
        net.append(float(ln[-1] - ln[0]))
        pk.append(float((np.abs(np.diff(ln)) / np.maximum(1e-3, np.diff(k[:, 0])) / 30.0).max()))
    seq = ''.join('+' if x > 0.02 else ('-' if x < -0.02 else '0') for x in net)
    flips = sum(1 for a, b in zip(seq, seq[1:]) if a != b and '0' not in (a, b))
    return dict(range_p50=round(float(np.median(rng_)), 3) if rng_ else 0.0, dir_seq=seq, dir_flips=flips,
                ins=seq.count('+'), outs=seq.count('-'), peak_p50=round(float(np.median(pk)), 3) if pk else 0.0,
                peak_p90=round(float(np.percentile(pk, 90)), 3) if pk else 0.0,
                ok=bool(rng_) and (float(np.median(rng_)) >= 0.25 or (float(np.median(rng_)) >= 0.12 and float(np.median(pk)) >= 0.05)))
    # round 5: a folded path (in / out pulses under the close-up zoom cap) has a small range but the reference's speed


def _level_ok(path, ms, dur=2.0, floor=-30.0):
    import librosa
    y, sr = librosa.load(path, sr=22050, mono=True, offset=ms, duration=dur)
    win = int(0.25 * sr)
    lv = [20 * math.log10(max(1e-9, float(np.sqrt(np.mean(y[i:i + win] ** 2))))) for i in range(0, len(y) - win + 1, win)]
    return (bool(lv) and min(lv) >= floor), (min(lv) if lv else None)


def plan_flow_s2rz(A, shots, opts):
    """blur_flow 's2rz' edit on any song: the remake's shot table (template) mapped through the song's beat grid,
    anchored on its drop (hype_i4f.BeatMap), clips picked per slot through the pool gate, plates rendered for the sung
    words, the lyric slots filled with the sung words on their syllables, the song edits mapped in time."""
    import hype_i4f as HI
    import plates_s2rz as PL
    T = json.load(open(opts.get('template_path') or TEMPLATE, encoding='utf-8'))
    rng = random.Random(opts.get('seed'))
    fps = int(opts.get('fps') or 30)
    aspect = opts.get('aspect') or '16:9'
    W, H = (1920, 1080) if aspect == '16:9' else (1080, 1920)
    P_ref, drop_ref, t_ref0, dur_ref = float(T['P']), float(T['drop']), float(T['t_ref0']), float(T['duration'])
    beats_all = [float(b) for b in A.get('beats') or []]
    P = float(A.get('beat_period') or (np.median(np.diff(beats_all)) if len(beats_all) > 2 else 0.6))
    while P < 0.45:
        P *= 2.0
    while P > 0.8:
        P /= 2.0
    step = max(1, int(round(P / float(A.get('beat_period') or P))))
    beats = beats_all[::step] if step > 1 else beats_all
    pre = (drop_ref - t_ref0) / P_ref
    post = (dur_ref - drop_ref) / P_ref
    drop_song = HI.pick_drop(A, P, pre_beats=pre + 0.5, post_beats=post, want=opts.get('drop_at'))
    downs = [float(d) for d in A.get('downbeats') or []]
    if downs and opts.get('drop_at') is None:
        dn = min(downs, key=lambda d: abs(d - drop_song))
        if abs(dn - drop_song) < 2 * P:
            drop_song = dn
    bm = HI.BeatMap(beats, drop_song, P, P_ref, drop_ref)
    ms = max(0.0, bm.song(t_ref0))
    # the song must be audible from the first frame (critic round 3: 2.25 s of near silence): >= -30 dBFS in every
    # 0.25 s of the first 2 s, else the edit starts later in the song (whole beats, the structure keeps its mapping)
    lvl = None
    try:
        for _ in range(8):
            ok, lvl = _level_ok(A['path'], ms)
            if ok:
                break
            log(f'song quiet at the start ({lvl:.1f} dBFS): starting {P:.2f} s later')
            ms += P
    except Exception as e:  # noqa: BLE001
        log(f'start level check skipped: {e}')

    def M(t_ref):
        return round(bm.song(t_ref) - ms, 4)

    DUR = M(dur_ref)
    rows = [dict(r) for r in T['rows'] if M(r['t1']) > 0.02]
    tail = [r for r in rows if r.get('slot') in ('end_card', 'outro_hero') or 'end card' in str(r.get('kind'))]
    if tail:
        t_card = M(min(r['t0'] for r in tail))
        _plate_end = any(r.get('plate') == 'hyper' and 'end plate' in str(r.get('kind')) for r in rows)
        DUR = min(DUR, t_card + float(opts.get('end_card_s', 5.0 if _plate_end else 3.2)))
        rows = [r for r in rows if M(r['t0']) < DUR - 0.05]
    # ------------------------------------------------ clips (the blur_flow pool gate, then the preset's taste score)
    clips = [dict(c) for c in shots if c.get('path') and os.path.exists(c['path'])]
    all_clips = list(clips)
    good = [c for c in clips if pool_ok(c)]
    log(f'pool: {len(good)} of {len(clips)} clips pass the blur_flow gate')
    if len(good) >= 8:
        clips = good
    needs = {}
    if rows and not rows[0].get('plate'):
        rows[0]['slot'] = 'hook'           # round 5: the opening close-up is its own clip (not the remake's replayed slot)
    for ra, rb in zip(rows[:-1], rows[1:]):
        if rb.get('slot') and rb.get('slot') == ra.get('slot'):
            rb['slot'] = str(rb['slot']) + '#b'     # back-to-back rows of one remake clip: two clips here (a short pool clip
            #                                         would otherwise loop for 3-4 s: critic 'the hook is dead')
    if opts.get('no_reuse', True):
        # round 5 (viewer critic: the same three clips twice 9 s apart, 'clips looped together'): the reference replays its own
        # footage (slots 22-34 replay 5-17); the planner gives every replay row its own clip
        seen_t, n_rep = {}, {}
        for r in rows:
            sl = r.get('slot')
            if not sl or r['plate'] or str(sl) in ('end_card', 'outro_hero'):
                continue
            if sl in seen_t and M(r['t0']) - seen_t[sl] > 3.0:
                n_rep[sl] = n_rep.get(sl, 0) + 1
                r['slot'] = str(sl) + '#r%d' % n_rep[sl]
                sl2 = r['slot']
            else:
                sl2 = sl
            seen_t[sl2 if sl2 != sl else sl] = M(r['t1'])
            if sl2 != sl:
                seen_t[sl] = M(r['t1'])
    for r in rows:
        if r['plate']:
            continue
        d = (M(r['t1']) - M(r['t0'])) * float(r['speed'].get('rate', 1.0)) + 0.3
        k, nd = needs.get(r['slot'], (r, 0.0))
        needs[r['slot']] = (k, nd + d)
    used, picked, by_src, use_n = set(), {}, {}, {}
    order_slots = sorted(needs, key=lambda s: min(r['t0'] for r in rows if r['slot'] == s))
    for slot in order_slots:
        r, need = needs[slot]
        fr = {'closeup': 'closeup', 'medium': 'medium', 'wide': 'wide'}.get(r['framing'], 'closeup')

        def score(c, r=r, need=need, fr=fr):
            s = float(c.get('taste') or c.get('score') or 0.5)
            s += 0.35 if (c.get('framing') or 'medium') == fr else 0.0
            s += 0.15 * min(1.0, float(c.get('duration') or 0) / max(0.5, need))
            s += 0.2 if int(c.get('faces') or 0) >= 1 else -0.2
            # the owner: attractive, moving shots - never a talking head (talk = the library's mouth / dialogue score)
            s -= 0.5 * max(0.0, float(c.get('talk') or 0) - 0.5)
            mo_ = c.get('mood')
            if mo_:                      # round 5: the SigLIP mood vet (r7/mood_pool.py): bright / happy up, menace / blood / rage / gloom down
                s += 0.7 * float(mo_.get('good', 0.0)) - 0.8 * float(mo_.get('bad', 0.0)) - 0.4 * float(mo_.get('gloom', 0.0))
            try:
                wm_ = clip_warm(c['path'])
                s -= (0.025 if slot == order_slots[0] else 0.01) * max(0.0, wm_ - (20.0 if slot == order_slots[0] else 32.0))
            except Exception:  # noqa: BLE001
                pass
            s += (0.6 if 'outro' in r['kind'] or 'laugh' in r['kind'] else 0.25) * min(1.0, float(c.get('motion') or 0.3))
            if 'male' in r['kind'] or 'face' in r['kind']:
                s += 0.2 * float(c.get('big_face') or 0)
            if slot == order_slots[0]:
                # round 5 (critic: the hook was a hair-ornament crop): the first body shot is a face close-up
                bf = float(c.get('big_face') or 0)
                s += 3.0 * min(bf, 0.15) + (0.4 if (c.get('framing') or '') == 'closeup' else -0.4) - (1.0 if bf < 0.03 else 0.0)
            s -= 0.3 * by_src.get(c.get('source_id'), 0)
            return s + rng.uniform(0, 0.08)
        # round 5: a clip appears at most twice in the edit (the reference replays its own footage at most twice): a
        # slot that replays takes a clip nobody else uses; when the gated pool runs out, the ungated clips come next
        n_rows = sum(1 for rr in rows if rr.get('slot') == slot)
        # round 5 (viewer critic: the same three clips twice 9 s apart): a clip is never replayed while an unused one exists
        # (opts no_reuse=False restores the old 'at most twice')
        cands = [c for c in clips if c['path'] not in used and use_n.get(c['path'], 0) == 0] if opts.get('no_reuse', True) else []
        if not cands:
            cands = [c for c in clips if c['path'] not in used and use_n.get(c['path'], 0) + n_rows <= max(2, n_rows)]
        if not cands:
            cands = [c for c in all_clips if c['path'] not in used and use_n.get(c['path'], 0) + n_rows <= max(2, n_rows)]
        if not cands:
            cands = [c for c in all_clips if use_n.get(c['path'], 0) + n_rows <= max(2, n_rows)] or clips
        best = max(cands, key=lambda c: score(c) - 0.6 * use_n.get(c['path'], 0))
        if 'end' in str(slot) and 'hook' in picked:
            best = picked['hook']              # round 5: the end card bookends the opening face
        used.add(best['path'])
        use_n[best['path']] = use_n.get(best['path'], 0) + n_rows
        by_src[best.get('source_id')] = by_src.get(best.get('source_id'), 0) + 1
        picked[slot] = best
    # ------------------------------------------------ sung words (output seconds)
    stub = dict(music=A['path'], music_start=ms, duration=DUR, planner=dict(preset='blur_flow'))
    ws = opts.get('_sung_words')
    if ws is None and opts.get('song'):
        try:
            import lyric_text as LT
            ws = LT.sung_words(stub, opts['song'], opts.get('artist') or '')
        except Exception as e:  # noqa: BLE001
            log(f'sung words unavailable: {e}')
            ws = []
    ws = [w for w in (ws or []) if w.get('text') and HI._norm(w['text'])]
    work = opts.get('work_dir') or os.path.join(os.environ.get('TEMP') or '/tmp', 'edit-cache', 's2rz')
    os.makedirs(work, exist_ok=True)
    used_w = set()
    # ------------------------------------------------ shots
    shots_out, src_pos = [], {}
    embers = None
    for r in rows:
        t0, t1 = max(0.0, M(r['t0'])), min(DUR, M(r['t1']))
        if t1 - t0 < 0.5 / fps:
            continue
        sp = dict(r['speed']) or dict(speed='normal', rate=1.0)
        tr, tp = r['transition'], dict(r['params'])
        if not shots_out:
            tr, tp = 'hard', {}
        look = r.get('look')
        if r['plate'] == 'wordplate':
            # the plate carries one strong sung word: a content word of 4+ letters near the plate, else any word
            i = None
            if ws:
                for span in (1.0, 2.2, 3.5):
                    cand = [j for j, w_ in enumerate(ws) if j not in used_w and abs(float(w_['t_start']) - t0) <= span
                            and len(HI._norm(w_['text'])) >= 4 and HI._norm(w_['text']) not in HI.STOP | WEAK]
                    i = min(cand, key=lambda j: abs(float(ws[j]['t_start']) - (t0 + 0.1)) - 0.08 * len(HI._norm(ws[j]['text'])),
                            default=None)
                    if i is not None:
                        break
            word = re.sub(r"[^A-Za-z']", '', ws[i]['text']) if i is not None else ''
            if i is not None:
                used_w.add(i)
            if len(word) < 2:
                word = (opts.get('title') or 'LOVE').split()[0]
            pth = os.path.join(work, f'wordplate_{len(shots_out):02d}_{HI._norm(word)[:12]}.mp4')
            if not os.path.exists(pth):
                PL.word_plate(pth, word[:10], 'upper', max(12, int(round((t1 - t0) * 30)) + 2), seed=3 + len(shots_out))
            path, src_in, cd = pth, 0.0, (t1 - t0) + 0.1
            sp = dict(speed='normal', rate=1.0)
        elif r['plate'] == 'hyper':
            pth = os.path.join(work, 'star_card.mp4')
            if not os.path.exists(pth):
                PL.star_plate(pth, 96, seed=4, speed=-0.45)
            path, src_in, cd = pth, 0.0, 96 / 30.0
            sp = dict(speed='normal', rate=1.0)
        elif r['plate'] == 'embers':
            if embers is None:
                embers = os.path.join(work, 'embers.mp4')
                if not os.path.exists(embers):
                    PL.ember_plate(embers, 34, seed=1)
            path, src_in, cd = embers, 0.0, 34 / 30.0
            sp = dict(speed='normal', rate=1.0)
        else:
            c = picked[r['slot']]
            path = c['path']
            cd = float(c.get('duration') or 2.0)
            rate = float(sp.get('rate', 1.0))
            need = (t1 - t0) * rate
            if r['slot'] in src_pos:
                src_in = src_pos[r['slot']]
                if src_in + need > cd - 0.05:
                    src_in = max(0.0, cd - 0.05 - need)
            else:
                pk = float(c.get('peak') or cd * 0.4)
                src_in = max(0.0, min(pk - 0.35 * need, cd - need - 0.1))
            if src_in + need > cd - 0.04:
                rate = max(0.25, (cd - 0.06) / max(1e-3, t1 - t0))
                src_in = max(0.0, cd - 0.05 - (t1 - t0) * rate)
                sp = dict(speed='slowmo', rate=round(rate, 3)) if rate < 0.98 else dict(speed='normal', rate=round(rate, 3))
            src_pos[r['slot']] = min(cd - 0.3, src_in + (t1 - t0) * rate * 0.6)
        inc = r['cam_inc']
        ang = None
        if inc and len(inc[0]) >= 6:
            ang = np.cumsum(np.vstack([np.zeros((1, 2)), np.asarray(inc, float)[:, 4:6]]), axis=0)
            inc = [v[:4] for v in inc]
        if not shots_out:
            # the hook: the opening close-up pushes twice as hard (critic round 3: >= 1.0 flow in the first 2 s)
            inc = [[v[0] * 2.0, v[1] * 1.5, v[2], v[3] * 1.5] for v in inc]
        keys, zoom = HI.camera_keys(inc, t1 - t0, fps)
        if r['zoom'] > zoom:
            # the template's paths are the remake's already-capped paths: keep their headroom (camera_keys caps at 2.8x;
            # the pull-outs out of the flash bloom need up to 6.5x)
            zoom = min(float(r['zoom']), 6.5)
            keys, _ = _uncapped_keys(inc, t1 - t0, fps)
        if ang is not None and len(ang) > 1:
            # the angles are absolute (deg), resampled onto this shot's length
            xs = np.linspace(0.0, 1.0, len(ang))
            keys = [list(kk[:5]) + [round(float(np.interp(j / max(1, len(keys) - 1), xs, ang[:, 0])), 3),
                                    round(float(np.interp(j / max(1, len(keys) - 1), xs, ang[:, 1])), 3)]
                    for j, kk in enumerate(keys)]
            zoom = min(6.5, zoom * (1 + 0.013 * float(np.abs(ang).max())))
        if not r['plate']:
            # round 5 (new-edit sheet: 4 s of a cheek / a chin at 3.6-4.7x): the deepest crop depends on the CLIP's own
            # framing - a close-up stays under 2.4x, a medium under 3.0x (the path is folded to fit, never the plate)
            fr_ = picked[r['slot']].get('framing') or 'closeup'
            zc_ = float((opts.get('zoom_caps') or {}).get(fr_, {'closeup': 2.4, 'medium': 3.0}.get(fr_, 3.0)))
            # the remake's pans (up to 0.6 W, the engine keeps the plate in frame) walk a close-up's face off the screen
            # (new-edit sheet: a cheek for 2 s): pans scaled to <= 0.20 W / 0.09 on close-ups, 0.26 / 0.12 otherwise (the face focus keeps the face in)
            pcx, pcy = (0.20, 0.09) if fr_ == 'closeup' else (0.26, 0.12)
            for j_, cap_ in ((2, pcx), (3, pcy)):
                m_ = max(abs(float(kk[j_])) for kk in keys)
                if m_ > cap_:
                    keys = [list(kk[:j_]) + [round(float(kk[j_]) * cap_ / m_, 5)] + list(kk[j_ + 1:]) for kk in keys]
            ka = np.asarray([kk[1] for kk in keys], float)
            lo_, hi_ = float(ka.min()), float(ka.max())
            tf = max(1.0, zoom / math.exp(max(0.0, -lo_)))
            L = math.log(max(1.02, zc_ / tf))
            if zoom * math.exp(max(0.0, hi_)) > zc_ * 1.001 and hi_ - lo_ > L:
                # FOLD the path instead of shrinking it (shrinking killed the per-frame speed: camera gate range 0.14,
                # motion 0.48): the ln path runs through a smooth cosine fold inside [0, L], so every push that would
                # pass the cap turns into a pull - the reference's in / out pulses - at the same per-frame speed
                x0 = float(keys[0][1])
                keys = [[kk[0], round(L / 2 * (1 - math.cos(math.pi * ((kk[1] - x0) / L + 0.5))), 5)] + list(kk[2:])
                        for kk in keys]
                zoom = round(tf, 4)
            elif zoom * math.exp(max(0.0, hi_)) > zc_ * 1.001:
                sh_ = math.log(zoom * math.exp(max(0.0, hi_)) / zc_)
                zoom = round(max(1.0, tf * math.exp(max(0.0, -lo_)), zoom / math.exp(sh_)), 4)       # the range fits: only the base zoom comes down
        sl_ = float(r.get('slide') or 0.0) * float(opts.get('slide_gain', 1.0))
        if sl_ and not r['plate']:
            # round 5: the incoming plate enters displaced by x0 and settles (tau 0.15 s); the engine's mirrored border shows the repeated
            # edge as in the reference (24.80); never folded / capped like a pan
            keys = [[kk[0], kk[1], round(kk[2] + sl_ * math.exp(-kk[0] / 0.15), 5)] + list(kk[3:]) for kk in keys]
        sh = dict(path=path, t0=round(t0, 4), t1=round(t1, 4), src_in=round(src_in, 3), transition_in=tr, transition_params=tp,
                  layout='fill', interp=r.get('interp', 'flow') if not r['plate'] else 'native',
                  camera=dict(move='static', amount=0.0, pan=[0.0, 0.0], roll=0.0, zoom=round(zoom, 4), handheld=0.0, bounce=0.0,
                              follow=False, accents='none', keys=keys),
                  handover=False, clip_duration=round(cd, 3), ref=dict(kind=r['kind'], ref_t0=r['t0'], slot=r['slot'] or r['plate'],
                                                                  effect=r.get('ref_effect')))
        sh.update(sp)
        if look:
            sh['look'] = look
        if not r['plate']:
            # round 5: zoom about the character's face (the engine's default centre is 0.45 H: a push into a face
            # whose eyes sit high kept only the cheek / chin)
            foc = _clip_face_focus(path, src_in + 0.5 * (t1 - t0) * float(sp.get('rate', 1.0) or 1.0))
            if foc:
                sh['camera']['focus'] = foc
                sh['camera']['focus_scaled'] = True
        if not r['plate']:
            c = picked[r['slot']]
            sh.update(source_id=c.get('source_id'), source_title=c.get('source_title'), source_channel=c.get('source_channel'))
        shots_out.append(sh)
    shots_out[0]['t0'] = 0.0
    timing = _snap_cuts(A['path'], ms, DUR, shots_out, lead=float(opts.get('cut_lead', 0.03)))
    log(f"cuts on drum hits: {timing}")
    if timing.get('share_60ms', 1.0) < 0.7 and not opts.get('no_timing_gate'):
        raise RuntimeError(f's2rz timing gate failed: {timing}')
    for a_, b_ in zip(shots_out[:-1], shots_out[1:]):
        a_['t1'] = b_['t0']
    shots_out[-1]['t1'] = round(DUR, 4)
    for sh in shots_out:
        d = sh['t1'] - sh['t0']
        cd = float(sh.get('clip_duration') or 0)
        r_ = float(sh.get('rate') or 1.0)
        if sh.get('speed') == 'velocity':
            r_ *= 1.3                        # the u-shaped velocity reads up to ~1.3x the base rate on average
        if cd and sh['src_in'] + d * r_ > cd - 0.02:
            r_ = max(0.05, (cd - 0.04 - sh['src_in']) / max(d, 1e-3))
            if r_ < 0.2:
                sh['src_in'] = max(0.0, cd - 0.04 - 0.2 * d)
                r_ = (cd - 0.04 - sh['src_in']) / max(d, 1e-3)
            sh['speed'], sh['rate'] = ('slowmo' if r_ < 0.98 else 'normal'), round(max(0.05, r_), 3)
            sh.pop('velocity', None)
            sh.pop('ramp', None)
        r_ = float(sh.get('rate') or 1.0)
        if cd and sh['src_in'] + d > cd + 0.02:
            # the clip is read slower than 1x: an explicit clip-time curve (check_plan / the engine read it exactly)
            n = max(2, int(math.ceil(d * fps)) + 1)
            sh['speed_curve'] = [[round(min(d, k / fps), 4), round(min(cd - 0.02, sh['src_in'] + min(d, k / fps) * r_), 4)]
                                 for k in range(n)]
    # ------------------------------------------------ overlays
    ovs = []
    title = (opts.get('title') or '').strip()
    # round 6: with a hook (template from the dialogue shots) the name flies in over the hero entry, not over the hook
    entry_t = max(0.0, M(12.333)) if t_ref0 < 12.0 else 0.0
    _nx = [sh_['t0'] for sh_ in shots_out if sh_['t0'] > entry_t + 0.3]
    first_end = (_nx[0] if _nx else min(entry_t + 1.5, DUR)) if entry_t > 0.5 else (shots_out[1]['t0'] if len(shots_out) > 1 else min(1.5, DUR))
    if title:
        # the reference's big name over the opening close-up (12.33 s: the name flies in, ~20 % H caps, low right)
        ovs.append(dict(type='text', text=title, style='s2rz_title', size=min(0.2, 1.3 / max(6, len(title))) / 0.72,
                        x=0.60, y=0.80, color=[150, 205, 255], anim='letters_fly', stagger=0.03, t0=round(entry_t + 0.05, 3), t1=round(first_end, 3)))
    for o in T['overlays']:
        o = dict(o)
        o['t0'], o['t1'] = max(0.0, M(o['t0'])), min(DUR, M(o['t1']))
        if o['t1'] - o['t0'] > 0.05:
            if o.get('type') == 'text' and o.get('style') in ('s2rz_caption', 's2rz_title') and not opts.get('no_card_wave'):
                o.setdefault('wave', [7.0, 0.8, 1.6, 0.7])    # 2026-10-01: the reference's typed cards roll ~3 deg and slide
            ovs.append(o)
    if entry_t > 0.5:
        for g in T.get('hook_text') or []:
            a_, b_ = M(float(g['t0'])), M(float(g['t1']))
            cand = [w_ for w_ in (ws or []) if a_ - 0.5 <= float(w_['t_start']) <= b_ and re.sub(r"[^A-Za-z']", '', str(w_['text']))]
            if not cand:
                continue
            take, n_ch = [], 0
            for w_ in cand:
                tx_ = re.sub(r"[^A-Za-z0-9' ]", '', str(w_['text'])).strip()
                if take and n_ch + len(tx_) + 1 > 22:
                    break
                take.append(tx_)
                n_ch += len(tx_) + 1
            text = ' '.join(take).strip()
            _cw = {re.sub(r"[^a-z]", '', w__.lower()) for w__ in take if len(re.sub(r"[^A-Za-z]", '', w__)) >= 3}
            _cw -= {'ooh', 'ohh', 'yeah', 'hey', 'nah', 'whoa', 'mmm', 'ohoh', 'ohohoh', 'ohohohoh'}
            if len(text) < 2 or len(_cw) < 1 or any(o_.get('type') == 'text' and o_.get('text') == text[0].upper() + text[1:] for o_ in ovs):
                continue                      # 'oh oh oh' / repeats are not a caption
            text = text[0].upper() + text[1:]
            last_t = float(cand[len(take) - 1].get('t_end') or (float(cand[len(take) - 1]['t_start']) + 0.4))
            first_t = float(cand[0]['t_start'])
            cps = float(np.clip(len(text) / max(0.5, last_t - max(0.0, first_t - 0.05)), 8.0, 18.0))
            t0_ = max(0.05, last_t - len(text) / cps)       # typed so the last letter lands with the last sung word
            o = dict(g, text=text, t0=round(t0_, 3), t1=round(max(b_, last_t + 0.5), 3), cps=round(cps, 1))
            # round 5 (viewer critic: two near-identical caption lines stacked over the eyes; the name card faded in under a typed
            # caption): one caption line at a time, and it is gone before the entry hit where the name card flies in
            o['t1'] = round(min(float(o['t1']), max(float(o['t0']) + 0.5, entry_t - 0.03)), 3)
            if any(float(o_['t0']) < float(o['t1']) and float(o['t0']) < float(o_['t1']) for o_ in ovs if o_.get('_hookcap')):
                continue
            o['_hookcap'] = True
            ovs.append(o)
        for g in T.get('hook_fade') or []:
            ovs.append(dict(g, t0=round(M(float(g['t0'])), 3), t1=round(M(float(g['t1'])), 3)))
    for o_ in ovs:
        o_.pop('_hookcap', None)
    # the ending: our name on the end card (its last ~1.9 s), a fade to black over the last 0.8 s
    ovs.append(dict(type='text', text=(opts.get('watermark') or 'ZERO YEN OTAKU').lstrip('@').upper(), style='s2rz_title',
                    size=0.11 / 0.72, x=0.5, y=float(opts.get('end_name_y', 0.47 if str((shots_out[-1].get('ref') or {}).get('slot')) == 'hyper' else 0.80)), anim='letters_fly', stagger=0.03, t0=round(max(0.0, DUR - 1.85), 3),   # round 5: lower third, never over the eyes
                    t1=round(DUR, 3)))
    ovs.append(dict(type='fade', t0=round(DUR - 0.8, 3), t1=round(DUR, 3), color='black', dir='out'))
    # lyric slots: the template's geometry and animation, the sung word nearest each slot (content words first)
    items = []
    plates = [(s['t0'], s['t1']) for s in shots_out if str((s.get('ref') or {}).get('slot')) in ('wordplate', 'embers')]
    if ws or (opts.get('body_fallback', True) and opts.get('title')):
        skipped = []
        for g in T['lyric_items']:
            tt = M(float(g['t0']))
            if tt < 0.3 or tt > DUR - 2.2:
                continue
            i = HI.key_word_index(ws, tt - 0.35, tt + 0.35, used_w) if ws else None
            if i is None:
                skipped.append(g)
                continue
            txt = re.sub(r"[^A-Za-z0-9' ]", '', str(ws[i]['text'])).upper().strip()
            if not txt:
                skipped.append(g)
                continue
            used_w.add(i)
            t_s = float(ws[i]['t_start'])
            dur = max(0.4, (M(float(g['t1'])) - M(float(g['t0']))))
            it = {k: v for k, v in g.items() if k not in ('t0', 't1', 't_out', 'mblur')}
            it.update(text=txt, t0=round(t_s - 0.03, 3), t1=round(t_s - 0.03 + dur, 3), sung=round(t_s, 3), word_index=i)
            items.append(it)
        # round 5 (viewer critic: the drop carried one body word on an instrumental drop; the reference shows a designed word on
        # ~70 % of its body seconds): when the aligner leaves the template's word slots empty, the song's own hook words (the
        # content words it sang anywhere in the edit, else the title) fill them on the beat nearest each slot
        min_w = int(opts.get('min_body_words', 10 if DUR >= 25 else 3))
        if len(items) < min_w and skipped and opts.get('body_fallback', True):
            bank = []
            for w_ in ws or []:
                n_ = HI._norm(w_['text'])
                tx_ = re.sub(r"[^A-Za-z']", '', str(w_['text'])).upper()
                if len(n_) >= 4 and n_ not in (HI.STOP | WEAK) and tx_ and tx_ not in bank:
                    bank.append(tx_)
            for w_ in re.split(r'\s+', ((opts.get('title') or '') + ' ' + (opts.get('song') or '')).upper()):
                tx_ = re.sub(r"[^A-Z']", '', w_)
                if len(tx_) >= 4 and tx_ not in bank:
                    bank.append(tx_)
            gridb = [b_ - ms for b_ in beats if 0.3 <= b_ - ms <= DUR - 2.0]
            nfb, k_ = 0, 0
            for g in sorted(skipped, key=lambda g_: float(g_['t0'])):
                if not bank or len(items) >= max(min_w + 4, 14):
                    break
                tt = M(float(g['t0']))
                tb = min(gridb, key=lambda b_: abs(b_ - tt)) if gridb else tt
                if abs(tb - tt) > 0.35:
                    tb = tt
                if any(abs(float(o_['t0']) - tb) < 0.9 for o_ in items) or any(a_ - 0.2 <= tb <= b_ for a_, b_ in plates):
                    continue
                txt = bank[k_ % len(bank)]
                k_ += 1
                dur = max(0.4, (M(float(g['t1'])) - M(float(g['t0']))))
                it = {k: v for k, v in g.items() if k not in ('t0', 't1', 't_out', 'mblur')}
                it.update(text=txt, t0=round(tb - 0.03, 3), t1=round(tb - 0.03 + dur, 3), sung=None, word_index=-1, _fb=True)
                items.append(it)
                nfb += 1
            log(f'body words: {len(items) - nfb} sung + {nfb} hook words on the beat (bank {bank[:8]})')
        for it in items:
            for a_, b_ in plates:
                if it['t0'] < a_ < it['t1']:
                    it['t1'] = round(a_, 3)
        items = [it for it in items if it['t1'] - it['t0'] >= 0.3 and not any(a_ <= it['t0'] < b_ for a_, b_ in plates)]
        try:
            on = vocal_onsets(A['path'], max(0.0, ms - 0.5), ms + DUR + 0.5)
            snap_items([i_ for i_ in items if not i_.get('_fb')], on, offset=ms)
        except Exception as e:  # noqa: BLE001
            log(f'onset snap skipped: {e}')
        pick_anims(items, shots_out, rng)
        items.sort(key=lambda it: float(it['t0']))
        for a_, b_ in zip(items[:-1], items[1:]):
            if float(a_['t1']) > float(b_['t0']) - 1.0 / fps:
                a_['t1'] = round(float(b_['t0']) - 1.0 / fps, 3)
                a_['t_out'] = round(max(float(a_['t0']) + 0.1, float(a_['t1']) - float(a_.get('dur_out', 0.1))), 3)
        items = [it for it in items if float(it['t1']) - float(it['t0']) >= (0.6 if it.get('anim_in') == 'spread' else 0.35)]
        for it in items:
            if it.get('anim_in') == 'spread' and rng.random() < 0.6:
                it.update(anim_in='letter_pop', pop0=1.3, pop_frames=4, stagger=1.0 / 30)
        for it in items:
            it['box'] = min(float(it['box']), 0.20)
        clamp_items(items, 's2rz_word', W, H)
    if items:
        import lyric_text as LT
        lt = dict(type='lyric_text', preset='blur_flow', style='s2rz_word', t0=min(i['t0'] for i in items),
                  t1=max(i['t1'] for i in items), items=items)
        LT.resolve_overlaps(lt)
        items = lt['items']
        # 2026-10-01 text style library (refs/text-animation.md): new-song edits pick one blur_flow text recipe per
        # edit (s2rz_zoomdrift = the reference look keeps the items as built; the Love Again recipes replace the entry /
        # exit and the font); opts['text_recipe'] forces one, 'none' keeps the reference look
        try:
            import text_fx as TFX
            _rec = None if opts.get('text_recipe') == 'none' else TFX.vary(lt, 'blur_flow', opts.get('seed'), forced=opts.get('text_recipe'))
            if _rec and _rec != 's2rz_zoomdrift':
                for it in items:
                    for k_ in ('hold_slide', 'hold_tau', 'hold_wobble', 'hold_hz', 'hold_creep', 'hold_drift', 'hold_bob',
                               'hold_phase', 'pop0', 'pop_frames', 'stagger'):
                        it.pop(k_, None)
                clamp_items(items, lt['style'], W, H, slide_pad=False)
                LT.resolve_overlaps(lt)
            if _rec:
                log(f'text recipe: {_rec}')
                lt['recipe'] = _rec
        except Exception as e:  # noqa: BLE001
            log(f'text recipe skipped: {e}')
        ovs.append(lt)
    # ------------------------------------------------ song edits (the remake's, mapped; nothing placed before 0.3 s)
    song_fx = [dict(op='fade', t0=0.0, t1=0.08, db0=-12.0, db1=0.0, then_out=False)]
    for o in T['song_fx']:
        o = dict(o)
        for k in TIME_KEYS:
            if k in o:
                o[k] = M(float(o[k]))
        tmin = min(float(o[k]) for k in TIME_KEYS if k in o)
        if o['op'] in ('duck', 'band') and 't0' in o:
            if float(o.get('t1', 0)) <= 0.3:
                continue
            o['t0'] = max(0.0, float(o['t0']))
        elif o['op'] == 'fade' and o.get('then_out'):
            o['t0'], o['t1'] = round(DUR - 2.8, 3), round(DUR - 0.1, 3)
        elif tmin < 0.3:
            continue
        song_fx.append(o)
    if not opts.get('legacy_transition_sound'):
        # 2026-09-29 transition-sound study (refs/transition-sound.md): the reference's added sounds measured with stem
        # separation (flash-cut cycle none / breakdown / sub hit / swell, a swell into the drop, the bright crash on the
        # blur cut that ends a breakdown) replace the template's estimated impacts / riser / wash / sub swell / bass cuts
        try:
            import song_fx as SFX2
            song_fx = [o for o in song_fx if o.get('op') not in ('impact', 'riser', 'wash', 'sub_swell_cut', 'bass_cut',
                                                                 'noise_crash', 'sub_hit', 'breath_riser')
                       and 'breakdown' not in str(o.get('why') or '')]
            _tmap = {'flash_decay': 'flash_white', 'whip_blur': 'whip_slide'}   # round 5 names -> the recipe's cut kinds
            song_fx += SFX2.transition_sound_ops('s2rz', [dict(t=float(s['t0']), tr=(s.get('ref') or {}).get('effect') or _tmap.get(s.get('transition_in'), s.get('transition_in') or 'hard'))
                                                          for s in shots_out[1:]], DUR, drop=M(drop_ref),
                                                 seed=int(opts.get('seed') or 0))
            cut_ts = sorted(float(s['t0']) for s in shots_out[1:])
            for o in song_fx:
                if o.get('op') == 'noise_crash':
                    # round 6 (critic: bright columns / 1 s of white noise over the drop on the new edit): never more than +3 dB
                    # over the NEW song's own highs, never longer than 250 ms
                    o['hi_db'] = round(min(float(o.get('hi_db', 0.0)), 3.0), 2)
                    o['t12'] = round(min(float(o.get('t12', 0.1)), 0.25), 3)
                    o['t6'] = round(min(float(o.get('t6', 0.05)), 0.12), 3)
                if o.get('op') == 'noise_crash':
                    prev = [c for c in cut_ts if c <= float(o['at']) + 1e-3]
                    if prev and float(o['at']) - float(o.get('rise', 0.15)) < prev[-1]:
                        o['rise'] = round(max(0.03, float(o['at']) - prev[-1]), 3)     # onset never before the switch
        except Exception as e:  # noqa: BLE001
            log(f'transition sounds skipped: {e}')
    if entry_t > 0.5 and not opts.get('no_hook_audio'):
        # the hook is a quiet, muffled bed (Love Again: the intro through a low-pass that opens into the drop): -9 dB and
        # a 16 kHz-wide shelf cut until the entry hit, opening over 0.35 s ON the hit -> the loudness range the reference has
        # round 5 (viewer critic: 4.5 s of music at -33..-40 dB, nothing to hear): the bed sits at -5 dB and the muffle is milder
        song_fx += [dict(op='duck', t0=0.0, t1=round(entry_t - 0.04, 3), depth_db=float(opts.get('hook_duck_db', -5.0)), attack=0.0, release=0.12, why='hook: quiet bed'),
                    dict(op='lowpass', t_close=-0.5, close_s=0.01, t_open=round(entry_t + 0.3, 3), open_s=0.35, hi_db=float(opts.get('hook_hi_db', -9.0)), mid_db=float(opts.get('hook_mid_db', -3.0)),
                         why='hook: muffled intro opening into the entry')]
    grid = [round(b - ms, 4) for b in beats if -0.01 <= b - ms <= DUR + 0.5]
    dgrid = [round(b - ms, 4) for b in downs if -0.01 <= b - ms <= DUR + 0.5]
    plan = dict(aspect=aspect, fps=fps, width=W, height=H, duration=round(DUR, 3), music=A.get('path'), music_start=round(ms, 4),
                clip_audio='none', sfx='none', audio_design='none', song_fx=song_fx, fade_out=0.0,
                style='flow_blur', look=T.get('look') or 's2rz_guard', grade=T.get('look') or 's2rz_guard', frame='full',
                preset='blur_flow', group='general', template='flow_blur', seed=opts.get('seed'), lead='none', handover=False,
                drive=False, particles=False, watermark=None,
                style_overrides=dict(end=dict(kind='cut', dur=0.0), text=dict(title_when='none'),
                                     audio=dict(lufs=float(opts.get('lufs', -13.2)), ceiling_db=float(opts.get('ceiling_db', -0.5))),   # the reference's own master (S2rz: -13.2 LUFS, +0.6 dBTP)
                                     camera=dict(handheld=0.0, shutter=float(T.get('shutter') or 0.35))),
                beats=grid, downbeats=dgrid, drop=M(drop_ref), beat_period=P, shots=shots_out, overlays=ovs,
                planner=dict(type='song', template='flow_blur', flow_variant='s2rz', structure='S2rz template',
                             reference=T['reference'], P=P, drop_song=round(drop_song, 3), preset='blur_flow', start_level_db=lvl,
                             lyric_text_done=True, lyric_text=dict(preset='blur_flow', track='s2rz', items=len(items)),
                             slots={s: os.path.basename(c['path']) for s, c in picked.items()},
                             camera_check=camera_check(shots_out)))
    if not plan['planner']['camera_check']['ok']:
        raise RuntimeError(f"s2rz camera gate failed: {plan['planner']['camera_check']}")
    return plan


_FOC = {}


def _clip_face_focus(path, t):
    """[x, y] (clip fractions) to zoom about: the biggest anime face at clip time t, its eye line (upper 40 % of the
    box); None without a face."""
    key = (path, round(float(t), 1))
    if key in _FOC:
        return _FOC[key]
    out = None
    try:
        import cv2
        det = _face_model()
        cap = cv2.VideoCapture(path)
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t)) * 1000.0)
        ok, img = cap.read()
        if ok and det is not None:
            fs = det(img, conf=0.3)
            if fs:
                x, y, w, h, _ = fs[0]
                out = [round(min(0.85, max(0.15, x + w / 2)), 3), round(min(0.8, max(0.2, y + 0.42 * h)), 3)]
    except Exception:  # noqa: BLE001
        out = None
    _FOC[key] = out
    return out


def _snap_cuts(path, ms, dur, shots, lead=0.03, win=0.2, min_len=0.12):
    """Round 5: every cut moves onto the song's own kick / snare (song_fx.drum_onsets), `lead` s before it, when one
    lies within +-win; the first flash / whip cut of the edit moves onto the song's entry step (the first +8 dB jump
    of the 0.25 s level in the first 6 s) when it is within 1.5 s of it. Returns timing stats."""
    import subprocess
    import song_fx as SFX
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{ms:.3f}', '-t', f'{dur + 0.5:.3f}', '-i', path, '-ac', '2',
                          '-ar', '44100', '-f', 'f32le', '-'], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32).reshape(-1, 2).T.copy()
    on = np.array(SFX.drum_onsets(x, 44100), float)
    if len(on) < 4:
        return dict(onsets=int(len(on)))
    m = x.mean(axis=0)
    h = int(0.25 * 44100)
    lv = np.array([20 * math.log10(max(1e-9, float(np.sqrt(np.mean(m[i:i + h] ** 2))))) for i in range(0, min(len(m), int(6 * 44100)) - h, h // 2)])
    entry = None
    for k in range(2, len(lv)):
        if lv[k] - lv[:k - 1].max() >= 8.0 and lv[k] > -24:
            t_e = k * 0.125
            c = on[(on >= t_e - 0.1) & (on <= t_e + 0.35)]
            entry = float(c[0]) if len(c) else t_e
            break
    moved = []
    for i in range(1, len(shots)):
        t = float(shots[i]['t0'])
        k = int(np.argmin(np.abs(on - t)))
        if abs(on[k] - t) <= win:
            nt = float(on[k]) - lead
            lo = float(shots[i - 1]['t0']) + min_len
            hi = (float(shots[i + 1]['t0']) - min_len) if i + 1 < len(shots) else dur - min_len
            if lo < nt < hi:
                shots[i]['t0'] = round(nt, 4)
                moved.append(round((nt - t) * 1000))
    if entry is not None:
        fl = [i for i in range(1, len(shots)) if str(shots[i].get('transition_in') or '').startswith(('flash', 'whip'))]
        if fl and abs(float(shots[fl[0]]['t0']) - entry) <= 1.5:
            i = fl[0]
            nt = entry - lead
            lo = float(shots[i - 1]['t0']) + min_len
            hi = (float(shots[i + 1]['t0']) - min_len) if i + 1 < len(shots) else dur - min_len
            if lo < nt < hi:
                shots[i]['t0'] = round(nt, 4)
    cuts = np.array([float(s['t0']) for s in shots[1:]])
    d = np.array([float(on[np.argmin(np.abs(on - c))] - c) for c in cuts])
    return dict(onsets=int(len(on)), moved_ms_p50=float(np.median(np.abs(moved))) if moved else 0.0, n_moved=len(moved),
                lead_ms_p50=round(float(np.median(d)) * 1000, 1), share_60ms=round(float(np.mean(np.abs(d) <= 0.06)), 3),
                entry=round(entry, 3) if entry is not None else None)


def _uncapped_keys(inc, dur, fps=30.0):
    """The template's per-frame increments resampled onto `dur` (shape stretched, per-frame speed kept) without the
    hype caps (the remake already capped them for our footage)."""
    n_new = max(1, int(round(dur * fps)))
    inc = np.asarray(inc, dtype=float).reshape(-1, 4) if len(inc) else np.zeros((1, 4))
    src = np.linspace(0, len(inc) - 1, n_new)
    rs = np.stack([np.interp(src, np.arange(len(inc)), inc[:, j]) for j in range(4)], axis=1) * (30.0 / fps) * (len(inc) / n_new)
    cum = np.vstack([np.zeros((1, 4)), np.cumsum(rs, axis=0)])
    keys = [[round(k / fps, 4)] + [round(float(v), 5) for v in cum[k]] for k in range(len(cum))]
    return keys, None
