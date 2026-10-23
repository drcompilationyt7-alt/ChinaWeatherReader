#!/usr/bin/env python3
"""
Independent judge for CINEMATIC SLOW-MO edits (cinematic_slowmo, @zeroyenotaku Shorts): measures a render the same way as
the slow-mo reference (edit-refs/general/_sX2EUexbW8.mp4, "GOLDEN HOUR", 9:16 rot90, words on black then slowed shots) and
writes evidence + a 0-10 scorecard. The sound-edit criteria (hits, causes, beat remake) do NOT apply to this type.

    python core/edit/judge_slowmo.py CANDIDATE OUT_DIR [--plan plan.json] [--label slowmo_3] [--ref REF.mp4]
           [--no-vocals]

Criteria (each 0-10; PASS = every criterion >= 8 and overall (mean) >= 8.5):
  1 smooth     optical-flow slow motion: no duplicate (repeated) frames, no judder (frame-step spikes / drops against the
               local motion), no frozen run > 0.2 s inside a shot (owner: no frozen clips), measured at full fps
  2 beauty     per body shot: sharpness (Laplacian, 640 px) vs the reference's shots, colour (saturation, black point:
               no milky / grey shots), subject (a face, female-first for the beauty vibes: the clips' portrait QA), framing
  3 lyrics     sung words shown (never the vibe name): each word's first legible frame vs the vocal onsets of the edit's
               own audio (Demucs vocals, LYRICS_NO_STORE), legibility (letter size vs the reference, smear-in frames,
               on-screen time, contrast on black)
  4 mix        the slowed song: loudness vs the reference (-6.9 LUFS), true peak <= -1 dBTP, intro duck under the words,
               clean start (no click), fade out at the end, no dropouts, slowed (plan speed 0.85-0.97), music only
  5 layout     1080x1920, the full 16:9 master rotated 90 deg (plan), the upright 'rotate phone' corner hint in the first
               seconds (and gone after it), the watermark, no black bars, NO SHAKE (global high-frequency jitter)
  6 length     25-50 s
  7 variety    distinct sources per shot, max 2 shots per source, no clip twice, no near-duplicate shots, colour spread
  11 waves     (2026-10-19 round 3, INDEPENDENT of the planner: own loudness / YIN pitch / timbre / whisper words) every
               switch on a wave start of the sung line, none mid-hold / mid-phrase, no strong wave start missed; any
               failure = NOT PASS. Evidence: waves.png (timeline), waves.md (per-cut table), cutframes-NN.jpg
  8 cuts       (2026-10-19) the detected cuts against the musical moments of the edit's OWN audio (music_moments.py: the
               main beat of the song's real meter, phrase starts, sung syllables >= 0.25, accents): share on the meter
               and on a moment vs the reference's rule (refs/slowmo-judge-ref.json cut_rule: 100 % / 100 %, the moment
               ~66 ms after the switch frame), the first clip on a phrase / syllable, not every beat (<= 55 % 1-beat shots)
  9 no_sfx     (2026-10-19) the mix against the song version it plays (plan music / music_start, sample-aligned): the
               per-frame spectral residual the song does not explain (an SFX / riser / whoosh), gates / stutters in the
               gain track, gain jumps > 6 dB; slow-mo is the song only (smooth level changes allowed)
 10 holds      (2026-10-19 round 2, owner: never cut while the singer holds a note / word) the cuts against the held notes of
               the edit's OWN audio (music_moments.holds: sung notes on the Demucs vocal stem, pitch + energy stability;
               held chords / swells in the vocal gaps): cuts inside a hold must be 0 (each one costs 4 points, any one
               fails), + the cut -> next musical moment offsets (next sung onset <= 0.35 s, else the next accompaniment
               attack) vs the reference's (refs/slowmo-judge-ref.json hold_rule) as a two-sample KS distance

Outputs in OUT_DIR: report.json, report.md, shots-NN.jpg (5 frames per shot), words.jpg (entry frame and the next 3 frames
of every word, + the vocal onset), hint.jpg, motion-NN.jpg (consecutive frames of the least smooth stretches),
cuts-holds.png (waveform + vocal pitch, holds shaded, every cut marked: green clear / red inside a hold).
The reference's measurements are cached in core/edit/refs/slowmo-judge-ref.json (CI needs no reference file).
Light by design: IDLE priority, 2 threads, frames streamed at 640 px height.
"""
import argparse
import glob
import io
import json
import math
import os
import subprocess
import sys

for _k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    os.environ.setdefault(_k, '2')
os.environ.setdefault('LYRICS_NO_STORE', '1')

import numpy as np  # noqa: E402

try:
    import psutil  # noqa: E402
    psutil.Process().nice(psutil.IDLE_PRIORITY_CLASS if os.name == 'nt' else 19)
except Exception:  # noqa: BLE001
    pass

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REF_DEFAULT = os.path.join(os.path.expanduser('~'), 'Desktop', 'edit-refs', 'general', '_sX2EUexbW8.mp4')
REF_CACHE = os.path.join(HERE, 'refs', 'slowmo-judge-ref.json')
THREADS = '2'
FREEZE_THR = float(os.environ.get('JUDGE_FREEZE_THR', '0.5'))      # same still-run rule as judge_sound (90 px grey)
FREEZE_MAX = float(os.environ.get('JUDGE_FREEZE_MAX_S', '0.2'))
LEN_LO, LEN_HI = 25.0, 50.0
PASS_EACH, PASS_ALL = 8.0, 8.5
HINT_THR = float(os.environ.get('JUDGE_HINT_THR', '10'))     # hint lines brighter than the ring around them (grey levels)
VOC_PROM = float(os.environ.get('JUDGE_VOCAL_PROM', '0.12'))  # a vocal onset (Demucs vocals of the edit) at the word; soft legato onsets are weak
WM_THR = float(os.environ.get('JUDGE_WM_THR', '0.06'))   # glyph-detail correlation


def ffprobe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=width,height,r_frame_rate:format=duration', '-of', 'json', path], capture_output=True, text=True)
    d = json.loads(r.stdout)
    st = d['streams'][0]
    a, b = st['r_frame_rate'].split('/')
    return int(st['width']), int(st['height']), float(a) / float(b), float(d['format']['duration'])


def lin(x, bad, good):
    if x is None:
        return 0.0
    if bad == good:
        return 10.0
    return float(np.clip(10 * (x - bad) / (good - bad), 0, 10))


def band(x, lo, hi, lo0, hi0):
    if x is None:
        return 0.0
    if lo <= x <= hi:
        return 10.0
    return lin(x, lo0, lo) if x < lo else lin(x, hi0, hi)


# ------------------------------------------------------------------------------------------------ picture scan
def _line_masks(fr_on, thr=60):
    """bright thin-line mask of an overlay drawn on black + a ring hugging it on both sides"""
    from scipy.ndimage import binary_dilation
    m = fr_on.astype(np.float32).max(axis=2) > thr
    if not m.any():
        return None, None
    return m, binary_dilation(m, iterations=2) & ~m


def _hint_masks(w, h):
    """outline / icon / text mask of the rotate-phone hint (rotate_hint.apply on black) and the ring around its lines"""
    import rotate_hint as RH
    return _line_masks(RH.apply(np.zeros((h, w, 3), np.uint8), 1.5, shows=[(0.0, 5.0)]))


_WM = {}


def _wm_masks(t, w, h, rot90, W=1080, H=1920):
    """the channel watermark's glyph template at time t (watermark.apply on black at the DELIVERED size W x H, the rot90 placement
    for rotated Shorts, then scaled to the scan size w x h): (box, zero-mean fine-detail template, dilated mask)"""
    import cv2
    import watermark as WM
    from scipy.ndimage import binary_dilation, gaussian_filter
    k = (round(t * 30) / 30.0, w, h, rot90)
    if k in _WM:
        return _WM[k]
    if len(_WM) > 64:
        _WM.clear()
    txt = os.environ.get('EDIT_WATERMARK', '@zeroyenotaku')
    if rot90:
        up = np.zeros((W, H, 3), np.uint8)
        fr = np.ascontiguousarray(np.rot90(WM.apply(up, k[0], txt, WM.WM_SAFE_ROT90), -1))
    else:
        fr = WM.apply(np.zeros((H, W, 3), np.uint8), k[0], txt)
    tg = cv2.resize(fr.astype(np.float32).max(axis=2), (w, h), interpolation=cv2.INTER_AREA)
    m = tg > 12
    if not m.any():
        _WM[k] = (None, None, None)
        return _WM[k]
    ys, xs = np.nonzero(binary_dilation(m, iterations=3))
    box = (ys.min(), ys.max() + 1, xs.min(), xs.max() + 1)
    tp = tg[box[0]:box[1], box[2]:box[3]]
    tp = tp - gaussian_filter(tp, 2.0)
    tp = (tp - tp.mean()) / (tp.std() + 1e-6)
    _WM[k] = (box, tp, binary_dilation(m, iterations=3))
    return _WM[k]


def scan(path, hint=True, wm_on=True):
    import cv2
    W, H, fps, dur = ffprobe(path)
    ah = 640
    aw = int(round(W * ah / H / 2)) * 2
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-threads', THREADS, '-i', path, '-vf', f'scale={aw}:{ah}:flags=area',
                          '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE)
    fb = aw * ah * 3
    sw = 90
    sh = int(round(sw * ah / aw))
    hm, hr = _hint_masks(aw, ah) if hint and H > W else (None, None)
    # 2026-10-19: a spatial null for the hint (the same masks shifted by ~1/20 of the frame): bright line-art behind the
    # corner (3d: a close-up's hair highlights) lit the plain line-vs-ring contrast with no hint on screen
    hnull = ([(np.roll(hm, (dy, dx), axis=(0, 1)), np.roll(hr, (dy, dx), axis=(0, 1)))
              for dy, dx in ((ah // 18, 0), (-ah // 18, 0), (0, aw // 12), (ah // 25, aw // 16))] if hm is not None else [])
    rot = H > W
    K = dict(small=[], tiny=[], luma=[], sat=[], sharp=[], p2=[], dark=[], ink=[], hint=[], shift=[], wm=[])
    edge_acc, prev = None, None
    while True:
        buf = p.stdout.read(fb)
        if len(buf) < fb:
            break
        arr = np.frombuffer(buf, np.uint8).reshape(ah, aw, 3)
        f = arr.astype(np.float32)
        g = f @ np.array([0.299, 0.587, 0.114], np.float32)
        lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
        K['sharp'].append(float(lap.var()))
        K['luma'].append(float(g.mean() / 255))
        mx, mn = f.max(axis=2), f.min(axis=2)
        K['sat'].append(float(((mx - mn) / (mx + 1)).mean()))
        K['p2'].append(float(np.percentile(g[::4, ::4], 2) / 255))
        dk = float((g < 30).mean())
        K['dark'].append(dk)
        ink = None
        wm, wr, wd = _wm_masks(len(K['dark']) / fps, aw, ah, rot, W, H) if wm_on else (None, None, None)
        if wm is not None:
            # normalised correlation of the frame's fine detail with the watermark glyphs at their placement
            from scipy.ndimage import gaussian_filter
            y0, y1, x0, x1 = wm
            reg = g[y0:y1, x0:x1]
            reg = reg - gaussian_filter(reg, 2.0)
            reg = (reg - reg.mean()) / (reg.std() + 1e-6)
            sc = float((reg * wr).mean())
            # null: the same template on the same rows shifted sideways (fine detail of the picture alone)
            dxs = (x1 - x0) * 3 if x1 + (x1 - x0) * 3 < aw else -(x1 - x0) * 3
            nul = g[y0:y1, x0 + dxs:x1 + dxs]
            nul = nul - gaussian_filter(nul, 2.0)
            nul = (nul - nul.mean()) / (nul.std() + 1e-6)
            K['wm'].append(sc - abs(float((nul * wr).mean())) if nul.shape == wr.shape else sc)
        else:
            K['wm'].append(np.nan)
        if dk > 0.75:
            im = mx > 90
            if hm is not None:
                im &= ~(hm | hr)                     # the hint is not a word
            if wd is not None:
                im &= ~wd                            # nor the watermark
            if im.mean() > 0.0008:
                ys, xs = np.nonzero(im)
                x0, x1, y0, y1 = np.percentile(xs, 1), np.percentile(xs, 99), np.percentile(ys, 1), np.percentile(ys, 99)
                sub = g[int(y0):int(y1) + 1, int(x0):int(x1) + 1]
                ls = 4 * sub[1:-1, 1:-1] - sub[:-2, 1:-1] - sub[2:, 1:-1] - sub[1:-1, :-2] - sub[1:-1, 2:] if min(sub.shape) > 3 else np.zeros(1)
                ink = dict(area=round(float(im.mean()), 5), box=[round(x0 / aw, 4), round(y0 / ah, 4), round(x1 / aw, 4), round(y1 / ah, 4)],
                           sharp=round(float(ls.var()), 1), lum=round(float(g[im].mean()), 1), mask=np.asarray(
                               Image.fromarray(im.astype(np.uint8) * 255).resize((sw, sh), Image.BILINEAR)) > 60)
        K['ink'].append(ink)
        if hm is not None:
            h0 = float(g[hm].mean() - g[hr].mean())
            hn = float(np.median([g[a_].mean() - g[b_].mean() for a_, b_ in hnull]))
            K['hint'].append(h0 - max(0.0, hn))
        else:
            K['hint'].append(None)
        im_ = Image.fromarray(arr)
        sm = np.asarray(im_.convert('L').resize((sw, sh), Image.BILINEAR), np.uint8)
        K['small'].append(sm)
        K['tiny'].append(np.asarray(im_.resize((12, int(round(12 * ah / aw))), Image.BILINEAR), np.uint8))
        g2 = cv2.resize(g, (aw // 2, ah // 2), interpolation=cv2.INTER_AREA)
        if prev is not None:
            (dx, dy), resp = cv2.phaseCorrelate(prev, g2)
            K['shift'].append((dx / (aw // 2), dy / (ah // 2), resp))
        else:
            K['shift'].append((0.0, 0.0, 0.0))
        prev = g2
        e = (np.abs(np.diff(sm.astype(np.int16), axis=0))[:, :-1] + np.abs(np.diff(sm.astype(np.int16), axis=1))[:-1]) > 40
        edge_acc = e.astype(np.uint16) if edge_acc is None else edge_acc + e
    p.wait()
    n = len(K['small'])
    v = dict(path=path, W=W, H=H, fps=fps, dur=dur, n=n, aw=aw, ah=ah, small=np.stack(K['small']), tiny=np.stack(K['tiny']),
             edge_persist=edge_acc / max(1, n), ink=K['ink'], hint=np.array([np.nan if x is None else x for x in K['hint']]),
             shift=np.array(K['shift']), wm=np.array(K['wm']))
    for k in ('luma', 'sat', 'sharp', 'p2', 'dark'):
        v[k] = np.array(K[k])
    return v


def detect_cuts(v):
    """hard cuts (judge_sound's detector: a big frame difference that persists one 24 fps step either side)"""
    import judge_sound as JS
    return JS.detect_cuts(v)


# ------------------------------------------------------------------------------------------------ measures
def segments(v, cuts):
    """shots between cuts; text = words-on-black frames (dark), fade = the luma falling into the end"""
    fps, n = v['fps'], v['n']
    b = [0] + list(cuts) + [n]
    out = []
    for a, c in zip(b[:-1], b[1:]):
        if c - a < 2:
            continue
        dk = float(np.median(v['dark'][a:c]))
        out.append(dict(a=a, b=c, start=round(a / fps, 3), dur=round((c - a) / fps, 3), text=dk > 0.75))
    return out


def smooth_metrics(v, segs):
    """duplicate frames, judder and frozen runs inside the picture shots (cut +-1 frame, black / text frames and the final fade
    excluded). d_t = mean |grey diff| (90 px) frame t vs t-1; a DUPLICATE = d_t ~ 0 while the local motion is not;
    JUDDER = d_t off the local median by > 2.6x (a step / a drop); FROZEN = a still run (judge_sound rule) > 0.2 s."""
    f = v['small'].astype(np.int16)
    fps = v['fps']
    d = np.r_[0.0, np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))]
    dup, jud, frames, worst, frozen, still_max = [], [], 0, [], [], 0.0
    fade_from = v['n']
    lum = v['luma']
    for k in range(v['n'] - 2, max(0, v['n'] - int(3 * fps)), -1):     # end fade: luma falling monotonically to black
        if lum[k] > lum[k + 1] + 0.002 or lum[k + 1] < 0.03:
            fade_from = k
        else:
            break
    for s in segs:
        if s['text']:
            continue
        a, b = s['a'] + 2, min(s['b'] - 1, fade_from)
        for t in range(a, b):
            if v['dark'][t] > 0.9:
                continue
            frames += 1
            w = d[max(a, t - 4):min(b, t + 5)]
            med = float(np.median(w))
            if med > 0.35 and d[t] < 0.06 * med + 0.03:
                dup.append(round(t / fps, 3))
            elif med > 0.35 and (d[t] > 2.6 * med or d[t] < med / 2.6):
                jud.append(round(t / fps, 3))
        # still runs inside the shot
        k = a
        while k < b - 1:
            m = k + 1
            while m < b and np.abs(f[m] - f[k]).mean() < FREEZE_THR:
                m += 1
            L = (m - k) / fps
            still_max = max(still_max, L if m - k > 1 else 0.0)
            if L > FREEZE_MAX and m - k > 1:
                frozen.append([round(k / fps, 3), round(L, 3)])
            k = m
        # per-shot motion level (for the sheets: the least smooth stretch)
        if b > a + 4:
            seg = d[a:b]
            worst.append((float(np.std(np.diff(seg)) / (np.mean(seg) + 0.05)), s['start'], s['a'], s['b']))
    motion = d[[t for s in segs if not s['text'] for t in range(s['a'] + 2, min(s['b'] - 1, fade_from))]] if segs else d
    return dict(frames=frames, duplicates=dup, judder=jud, judder_pct=round(100 * len(jud) / max(1, frames), 2),
                frozen=frozen, still_max_s=round(still_max, 3), fade_from_s=round(fade_from / fps, 3),
                motion_p50_p90=[round(float(np.percentile(motion, q)), 3) for q in (50, 90)] if len(motion) else [0, 0],
                worst=sorted(worst, reverse=True)[:3])


def picture_metrics(v, segs, fade_from):
    shots = []
    for s in segs:
        if s['text']:
            continue
        a, b = s['a'], min(s['b'], int(fade_from * v['fps'])) if fade_from else s['b']
        if b - a < 3:
            continue
        shots.append(dict(start=s['start'], dur=s['dur'], sharp=round(float(np.median(v['sharp'][a:b])), 1),
                          sat=round(float(np.median(v['sat'][a:b])), 3), luma=round(float(np.median(v['luma'][a:b])), 3),
                          p2=round(float(np.median(v['p2'][a:b])), 3), mid=(a + b) // 2))
    sig = []
    for s in shots:
        t = v['tiny'][s['mid']].astype(np.float32).ravel()
        sig.append((t - t.mean()) / (t.std() + 1e-6))
    dups = [[shots[i]['start'], shots[j]['start']] for i in range(len(shots)) for j in range(i + 2, len(shots))
            if float(np.dot(sig[i], sig[j]) / len(sig[i])) > 0.9]
    col = np.array([v['tiny'][s['mid']].reshape(-1, 3).mean(axis=0) for s in shots]) if shots else np.zeros((1, 3))
    return dict(shots=shots, near_duplicate_pairs=dups, colour_spread=round(float(col.std(axis=0).mean()), 1),
                sharp_median=round(float(np.median([s['sharp'] for s in shots])), 1) if shots else 0.0,
                sharp_p10=round(float(np.percentile([s['sharp'] for s in shots], 10)), 1) if shots else 0.0,
                sat_median=round(float(np.median([s['sat'] for s in shots])), 3) if shots else 0.0,
                p2_median=round(float(np.median([s['p2'] for s in shots])), 3) if shots else 0.0)


def word_events(v):
    """words on black: a new word = ink appears, or the ink mask changes (IoU < 0.25 vs 3 frames back)"""
    fps = v['fps']
    ink = v['ink']
    ev = []
    cur = None
    gap = 0
    for t, x in enumerate(ink):
        if x is None:
            # 2026-10-22 (blue dry run 6): a one-letter word ('I') smearing in drops under the ink threshold for a frame or
            # two and came back as a second entry (2.233 + 2.333 s). A gap of <= 3 frames whose ink returns where it was
            # is the same word; a longer gap ends it
            if cur:
                gap += 1
                if gap > 3:
                    cur['end'] = t - gap + 1
                    ev.append(cur)
                    cur, gap = None, 0
            continue
        if cur is not None and gap:
            m0 = cur.get('last_mask')
            if m0 is not None:
                inter, uni = (m0 & x['mask']).sum(), (m0 | x['mask']).sum()
                if uni and inter / uni < 0.2:
                    cur['end'] = t - gap
                    ev.append(cur)
                    cur = None
        gap = 0
        new = cur is None
        if cur is not None and t >= 3 and ink[t - 3] is not None:
            m0, m1 = ink[t - 3]['mask'], x['mask']
            inter, uni = (m0 & m1).sum(), (m0 | m1).sum()
            iou = inter / uni if uni else 1.0
            pa = ink[t - 1]['area'] if ink[t - 1] is not None else 0.0
            rs = cur['sharps'][-6:]
            smear_in = len(rs) >= 6 and np.median(rs) > 300 and x['sharp'] < 0.25 * np.median(rs)   # a new word's smear frame
            if iou < 0.3 or (iou < 0.6 and x['area'] > 1.4 * pa) or smear_in:
                # a jump to a different word (not the smear settling): the previous word ends here
                if t - cur['a'] > 4:
                    cur['end'] = t
                    ev.append(cur)
                    new = True
        if new:
            cur = dict(a=t, area_max=x['area'], sharps=[], boxes=[], lums=[])
        cur['area_max'] = max(cur['area_max'], x['area'])
        cur['last_mask'] = x['mask']
        cur['sharps'].append(x['sharp'])
        cur['boxes'].append(x['box'])
        cur['lums'].append(x['lum'])
    if cur:
        cur['end'] = len(ink) - gap
        ev.append(cur)
    # 2026-10-23 (blue v2 dry run): a word's smear-in (a tall motion-blurred streak, never legible) came out as its own
    # 0.2 s entry before the word ("6 entries vs 5 planned" + a 'short' word). A short entry that never gets sharp and is
    # followed at once by a sharp one is that word's entrance: merged into it.
    fps_ = float(v['fps'])
    merged = []
    for e in ev:
        if merged:
            p = merged[-1]
            n_p = p['end'] - p['a']
            hs = float(np.median(e['sharps'][min(len(e['sharps']) - 1, 3):] or e['sharps']))
            if n_p <= int(0.3 * fps_) and e['a'] - p['end'] <= 2 and max(p['sharps'] or [0]) < 0.6 * hs:
                e = dict(e, a=p['a'], sharps=p['sharps'] + e['sharps'], boxes=p['boxes'] + e['boxes'], lums=p['lums'] + e['lums'],
                         area_max=max(p['area_max'], e['area_max']))
                merged[-1] = e
                continue
        merged.append(e)
    ev = merged
    out = []
    for e in ev:
        n = e['end'] - e['a']
        if n < 2:
            continue
        hold_sharp = float(np.median(e['sharps'][min(n - 1, 3):] or e['sharps']))
        legible = next((i for i, s in enumerate(e['sharps']) if s >= 0.6 * hold_sharp), 0)
        bx = np.median(np.array(e['boxes'][min(n - 1, 3):] or e['boxes']), axis=0)
        bw, bh = bx[2] - bx[0], (bx[3] - bx[1]) * v['ah'] / v['aw']          # in units of the frame width
        out.append(dict(t=round(e['a'] / fps, 3), t_legible=round((e['a'] + legible) / fps, 3), smear_frames=int(legible),
                        on_s=round(n / fps, 3), letter_h=round(float(min(bw, bh)), 4), length=round(float(max(bw, bh)), 4),
                        ink_luma=round(float(np.median(e['lums'])), 1), frame=e['a']))
    return out


def vocal_onsets(path, t1):
    """onsets (s) of the edit's own vocals over [0, t1] (Demucs htdemucs + lyrics.vocal_onsets; nothing stored)"""
    import lyrics as LY
    v16 = LY.vocal_stem(path, 0.0, float(t1), threads=2)
    on, prom, env, rms, hop = LY.vocal_onsets(v16)
    return [[round(float(x), 3), round(float(p), 2)] for x, p in zip(on, prom) if p >= 0.12], float(np.percentile(rms, 95))


def audio_metrics(path, dur, intro_end):
    import judge_sound as JS
    L = JS.loudness(path)
    y = JS.load_audio(path, 22050)
    sr = 22050
    hop = int(0.05 * sr)
    n = len(y) // hop
    r = np.sqrt(np.mean(y[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    rdb = 20 * np.log10(r)
    t = np.arange(n) * 0.05
    body = (t >= intro_end + 0.5) & (t <= dur - 2.0)
    body_db = float(np.median(rdb[body])) if body.any() else float(np.median(rdb))
    intro_db = float(np.median(rdb[(t >= 0.3) & (t < intro_end - 0.2)])) if intro_end > 1.0 else None
    drop = [round(float(x), 2) for x in t[body & (rdb < body_db - 30)]]
    first = float(np.abs(y[:int(0.01 * sr)]).max()) if len(y) else 0.0
    end_db = float(np.median(rdb[t >= dur - 0.35])) if n else 0.0
    return dict(lufs=L['lufs_i'], dbtp=L['true_peak_dbtp'], lra=L['lra'], body_db=round(body_db, 1),
                intro_duck_db=round(body_db - intro_db, 1) if intro_db is not None else None,
                dropouts=drop[:10], first_10ms_peak=round(first, 4), end_vs_body_db=round(end_db - body_db, 1))


def cut_alignment(path, cuts, dur):
    """8 cuts: the edit's cut times against the musical moments of its OWN audio (the version actually heard;
    music_moments: the main beat of the real meter, phrase starts, sung syllables, accents)"""
    import music_moments as MM
    M = MM.moments(path, 0.0, dur, vocals=True, threads=2)
    _LAST_H['_M:' + path] = M
    rows, st = MM.align(list(cuts), M)
    b = list(cuts) + [dur]
    lens = [(y - x) / max(0.3, float(M['main'])) for x, y in zip(b[:-2], b[1:-1])]      # body shots between two cuts
    st['one_beat_share'] = round(float(np.mean([l_ <= 1.2 for l_ in lens])), 3) if lens else 0.0
    st['main'], st['k'] = M['main'], M['k']
    return dict(rows=rows, stats=st)


_LAST_H = {}


def hold_offsets(rows):
    """per cut: the time to the next musical moment (the next sung onset when <= 0.35 s, else the next accompaniment attack
    <= 0.2 s, else 0.4 = none)"""
    out = []
    for r in rows:
        if r['to_next_onset'] is not None and 0.0 <= r['to_next_onset'] <= 0.35:
            out.append(float(r['to_next_onset']))
        elif r['acc_attack']:
            out.append(float(r['acc_attack'][0]))
        else:
            out.append(0.4)
    return out


def hold_alignment(path, cuts, dur, M=None):
    """10 holds: the cuts against the held notes / chords of the edit's OWN audio (music_moments.holds)"""
    import music_moments as MM
    H = MM.holds(path, 0.0, dur, threads=2, M=M)
    rows = MM.cut_report(list(cuts), H, end=dur)
    _LAST_H[path] = H
    offs = hold_offsets(rows)
    return dict(rows=rows, in_hold=[r['cut'] for r in rows if r['in_hold']], offsets=offs, null=MM.hold_null(H, cuts[0] if cuts else 0.0, dur),
                n_holds=len(H['vocal_holds']), forbid=H['forbid'])


def sfx_residual(path, music, m_start, dur):
    """9 no SFX: the mix against the song version it plays (plan music / music_start). Per 23 ms frame the magnitude
    spectrum of the mix is fitted by a gain x the song's (robust to the limiter / AAC / sub-sample shifts); the residual
    = what the song does not explain (an SFX, riser, whoosh, clip audio). The gain track shows gates / stutters (a dip
    of > 8 dB under its running median for 0.07-0.8 s) and jumps (> 6 dB within 70 ms) outside the end fade."""
    import librosa
    from scipy.ndimage import median_filter
    import music_moments as MM
    sr = 22050
    y = MM.load(path, 0.0, dur, sr)
    pad = min(0.3, float(m_start))
    s = MM.load(music, float(m_start) - pad, dur + 2 * pad, sr)
    a, b = int(min(6.0, dur / 4) * sr), int(min(dur - 2.0, 18.0) * sr)
    seg = y[a:b]
    nseg = np.linalg.norm(seg) + 1e-9

    def corr(lag):
        ref = s[a + lag:a + lag + len(seg)]
        if len(ref) < len(seg):
            return -1e9
        return float(np.dot(seg, ref) / ((np.linalg.norm(ref) + 1e-9) * nseg))
    # coarse: the onset envelopes (hop 64 = 2.9 ms; a raw-waveform grid locks onto a beat-periodic false peak), then
    # sample-exact on the waveform within +-64 samples
    oy = librosa.onset.onset_strength(y=seg.astype(np.float32), sr=sr, hop_length=64)
    osg = librosa.onset.onset_strength(y=s[a:a + len(seg) + int(2 * pad * sr) + 64].astype(np.float32), sr=sr, hop_length=64)
    cs = [(float(np.corrcoef(oy, osg[k:k + len(oy)])[0, 1]), k) for k in range(0, int(2 * pad * sr / 64) + 1)
          if len(osg[k:k + len(oy)]) == len(oy)]
    k0 = max(cs)[1] * 64 if cs else int(pad * sr)
    best = max(((corr(lag), lag) for lag in range(max(0, k0 - 64), k0 + 65)), default=(0.0, k0))
    s = s[best[1]:best[1] + len(y)]
    if len(s) < len(y):
        s = np.pad(s, (0, len(y) - len(s)))
    Y = np.abs(librosa.stft(y.astype(np.float32), n_fft=2048, hop_length=512))
    S_ = np.abs(librosa.stft(s.astype(np.float32), n_fft=2048, hop_length=512))
    n = min(Y.shape[1], S_.shape[1])
    Y, S_ = Y[:, :n], S_[:, :n]
    t = np.arange(n) * 512 / sr
    g = (Y * S_).sum(0) / ((S_ * S_).sum(0) + 1e-12)
    res = ((Y - g[None] * S_) ** 2).sum(0) / ((Y ** 2).sum(0) + 1e-12)
    res_db = 10 * np.log10(res + 1e-9)
    y_db = 10 * np.log10((Y ** 2).mean(0) + 1e-12)
    s_db = 10 * np.log10((S_ ** 2).mean(0) + 1e-12)
    loud = (y_db > np.percentile(y_db, 95) - 40) | (s_db > np.percentile(s_db, 95) - 40)
    core = loud & (t >= 0.15) & (t <= dur - 1.6)

    def runs(mask, lo, hi):
        out, i = [], 0
        while i < n:
            if mask[i]:
                j = i
                while j < n and mask[j]:
                    j += 1
                d_ = float(t[j - 1] - t[i]) + 512 / sr
                if lo <= d_ <= hi:
                    out.append((i, j, d_))
                i = j
            else:
                i += 1
        return out
    ev = [dict(t=round(float(t[i]), 2), dur=round(d_, 2), res_db=round(float(res_db[i:j].max()), 1))
          for i, j, d_ in runs(core & (res_db > -8.0), 0.065, 99.0)]
    gdb = 20 * np.log10(np.maximum(g, 1e-4))
    sl = s_db > np.percentile(s_db, 95) - 35        # the song itself is sounding (its own pauses are not gates)
    med = median_filter(gdb, 43, mode='nearest')    # ~1 s running median
    dip = sl & (gdb < med - 8.0) & (t >= 0.15) & (t <= dur - 1.6)
    gates = [dict(t=round(float(t[i]), 2), dur=round(d_, 2), depth_db=round(float((med[i:j] - gdb[i:j]).max()), 1))
             for i, j, d_ in runs(dip, 0.065, 0.8)]
    loud_s = s_db > np.percentile(s_db, 95) - 15
    jumps = []
    for k in range(3, n):
        # both sides with the song well up (a compressor / limiter recovering over the song's own near-silent gap is not a jump)
        # and the jump lands OFF the running median (a return to the normal level after the song's own gap is not one)
        if loud_s[k] and loud_s[k - 3] and 0.2 <= t[k] <= dur - 1.6 and abs(gdb[k] - gdb[k - 3]) > 6.0                 and abs(gdb[k] - med[k]) > 3.0:
            if not jumps or t[k] - jumps[-1] > 0.3:
                jumps.append(round(float(t[k]), 2))
    return dict(lag_ms=round((best[1] / sr - pad) * 1000, 1), corr=round(best[0], 3),
                res_db_median=round(float(np.median(res_db[core])), 1) if core.any() else None,
                res_db_p95=round(float(np.percentile(res_db[core], 95)), 1) if core.any() else None,
                sfx_events=ev[:12], gates=gates[:12], gain_jumps=jumps[:12])

def shake_metric(v, segs):
    """global high-frequency jitter: the frame-to-frame translation (phase correlation, % of the width) minus its 5-frame
    moving average, RMS over the picture shots (camera moves / slow push-ins are smooth and cancel out)"""
    from scipy.ndimage import uniform_filter1d
    res = []
    for s in segs:
        if s['text'] or s['b'] - s['a'] < 10:
            continue
        sh = v['shift'][s['a'] + 1:s['b']]
        ok = sh[:, 2] > 0.1
        dx = np.where(ok, sh[:, 0], 0.0) * 100
        dy = np.where(ok, sh[:, 1], 0.0) * 100
        hz = np.r_[dx - uniform_filter1d(dx, 5, mode='nearest'), dy - uniform_filter1d(dy, 5, mode='nearest')]
        res.append(float(np.sqrt(np.mean(hz ** 2))))
    if not res:
        return 0.0
    # the MEDIAN shot: a camera shake is in every shot; one juddering take (criterion 1) is not a shake
    return round(float(np.median(res)), 4)


# ------------------------------------------------------------------------------------------------ plan / pool
def plan_info(plan_path):
    if not plan_path or not os.path.exists(plan_path):
        return None
    with open(plan_path, encoding='utf-8') as f:
        p = json.load(f)
    import re
    m = re.search(r'@v([0-9.]+)\.', str(p.get('music') or ''))
    tob = max([float(o.get('t1') or 0) for o in p.get('overlays') or [] if o.get('type') == 'text_on_black'] or [0.0])
    shots = [s for s in p.get('shots') or [] if s.get('path') and float(s.get('t1') or 0) > tob + 0.05]   # not the placeholder under the black intro
    words = [it for o in p.get('overlays') or [] if o.get('type') == 'lyric_text' for it in o.get('items') or []]
    return dict(frame=p.get('frame'), deliver=p.get('deliver'), clip_audio=p.get('clip_audio'), sfx=p.get('sfx'),
                music=p.get('music'), music_start=p.get('music_start'),
                lrc_id=(((p.get('planner') or {}).get('lyric_text') or {}).get('lyrics') or {}).get('id'),
                cut_times=sorted({round(float(s_['t0']), 3) for s_ in p.get('shots') or [] if float(s_.get('t0') or 0) > 0.05}),
                speed=float(m.group(1)) if m else None, shots=shots, lyric_items=len(words),
                lyric_flags=[bool(it.get('lyric', True)) for it in words],
                sung=[float(it.get('sung', it.get('t0', 0))) for it in words],
                # lyric_text re-triggers a word held longer than max_hold on the beats (same sung time as the item before):
                # a deliberate punch of the held word, not a new sung word
                phrase=((p.get('planner') or {}).get('lyric_text') or {}).get('phrase_check'),
                n_items=len(words), item_t0=[float(it['t0']) for it in words if it.get('lyric', True)],
                retrig=[float(it['t0']) for k, it in enumerate(words) if k and abs(float(it.get('sung', -1)) - float(words[k - 1].get('sung', -2))) < 0.02])


def pool_lookup(paths):
    """pool items for the plan's clip files (any vibe manifest that lists the file name)"""
    root = os.environ.get('EDIT_POOL_DIR') or os.path.join(os.path.dirname(os.path.dirname(HERE)), 'output', 'edit-pools')
    by = {}
    for man in glob.glob(os.path.join(root, '*', '*', 'clips.json')):
        try:
            with open(man, encoding='utf-8') as f:
                for it in json.load(f).get('items') or []:
                    by[os.path.basename(it.get('file') or '')] = it
        except (OSError, ValueError):
            pass
    return [by.get(os.path.basename(p)) for p in paths]


def portrait_of(path):
    try:
        with open(path + '.portrait.json', encoding='utf-8') as f:
            d = json.load(f)
        return next(iter(d.values()))
    except (OSError, ValueError, StopIteration):
        return None


# ------------------------------------------------------------------------------------------------ scorecard
def scorecard(C, R, P, items):
    S, E = {}, {}
    sm = C['smooth']
    s_dup = lin(len(sm['duplicates']), 4, 0)
    s_jud = lin(sm['judder_pct'], 4.0, max(0.6, R['smooth']['judder_pct'] * 1.5))
    s_frz = 10.0 if not sm['frozen'] else lin(max(d for _, d in sm['frozen']), 0.8, FREEZE_MAX)
    S['1_smooth'] = round(0.5 * min(s_dup, s_jud, s_frz) + 0.5 * np.mean([s_dup, s_jud, s_frz]), 2)
    E['1_smooth'] = (f"{sm['frames']} picture frames: {len(sm['duplicates'])} duplicate {sm['duplicates'][:6]}, judder "
                     f"{sm['judder_pct']} % (ref {R['smooth']['judder_pct']} %) {sm['judder'][:6]}, frozen > {FREEZE_MAX} s "
                     f"{sm['frozen'][:4]}, longest still {sm['still_max_s']} s")
    # 2 beauty
    pc, pr = C['picture'], R['picture']
    sh_ratio = pc['sharp_median'] / max(1.0, pr['sharp_median'])
    p10_ratio = pc['sharp_p10'] / max(1.0, pr['sharp_p10'])
    s_sharp = 0.6 * lin(sh_ratio, 0.3, 0.7) + 0.4 * lin(p10_ratio, 0.2, 0.5)
    milky = [s['start'] for s in pc['shots'] if s['p2'] > 0.17 and s['sat'] < 0.8 * pr['sat_median']]
    grey = [s['start'] for s in pc['shots'] if s['sat'] < 0.45 * pr['sat_median']]
    oversat = [s['start'] for s in pc['shots'] if s['sat'] > 2.0 * pr['sat_median']]
    s_col = max(0.0, band(pc['sat_median'] / max(0.01, pr['sat_median']), 0.8, 1.5, 0.45, 2.2)
                - 2.5 * len(milky) - 2.5 * len(grey) - 1.5 * len(oversat))
    face, fem, framing = [], [], []
    for it, sh in zip(items or [], (P or {}).get('shots') or []):
        q = portrait_of(sh['path']) or {}
        fh = float(q.get('face_h') or 0.0)
        face.append(fh >= 0.12)
        framing.append(0.15 <= fh <= 0.8)
        f_ = (it or {}).get('subject')
        fem.append(f_ == 'female' or (f_ is None and float((q.get('sig') or {}).get('female') or 0) >= 0.5))
    if face:
        s_subj = 0.5 * lin(np.mean(face), 0.5, 0.85) + 0.5 * lin(np.mean(fem), 0.4, 0.75)
        s_frame = lin(np.mean(framing), 0.5, 0.85)
    else:
        s_subj = s_frame = None
    parts = [s_sharp, s_col] + ([s_subj, s_frame] if s_subj is not None else [])
    S['2_beauty'] = round(float(np.mean(parts)), 2)
    E['2_beauty'] = (f"sharp median {pc['sharp_median']} / p10 {pc['sharp_p10']} (ref {pr['sharp_median']} / {pr['sharp_p10']}) "
                     f"-> {s_sharp:.1f}; sat {pc['sat_median']} (ref {pr['sat_median']}), black point p2 {pc['p2_median']}, milky "
                     f"{milky} grey {grey} oversat {oversat} -> {s_col:.1f}; " +
                     (f"face {np.mean(face):.2f} female {np.mean(fem):.2f} -> {s_subj:.1f}; framing {np.mean(framing):.2f} -> {s_frame:.1f}"
                      if face else 'subject: no plan (n/a)'))
    # 3 lyrics
    W_ = C['words']
    if not W_:
        S['3_lyrics'] = 0.0
        E['3_lyrics'] = 'no words on screen (owner: the song lyrics are shown)'
    else:
        on = [o for o, pr_ in C.get('vocal_onsets') or [] if pr_ >= VOC_PROM]
        if on:
            errs = []
            rt = (P or {}).get('retrig') or []
            for w in [w for w in W_ if not any(abs(w['t'] - r) <= 0.12 for r in rt)]:
                dt = [w['t_legible'] - o for o in on]
                errs.append(min(dt, key=abs))
            good = [-0.15 <= e <= 0.25 for e in errs]     # readable from 150 ms before to 250 ms after the sung onset (smear-in lands on it)
            s_time = lin(float(np.mean(good)), 0.4, 0.85)
            terr = f"word - vocal onset (s) {[round(e, 2) for e in errs]} ({int(sum(good))}/{len(good)} within -0.15..+0.25)"
        else:
            s_time, terr = 7.0, 'vocal onsets not measured (--no-vocals)'
        LWc = C.get('lyric_words') or {}
        if LWc.get('rows'):
            # 2026-10-23: per word (which word, its own sung onset; the nearest-any-onset check above passed blue v2 with 3 of
            # 5 words 0.4-0.8 s late): on time = first ink within LW_TOL of (onset - LW_LEAD); a word on another note caps it
            # at 5 (NOT PASS); a sung intro word that never shows costs 2
            rows_ = LWc['rows']
            okf = float(np.mean([r_['ok'] for r_ in rows_]))
            s_time = lin(okf, 0.5, 1.0)
            wrong = [r_['entry'] for r_ in rows_ if r_['wrong_note']]
            if wrong:
                s_time = min(s_time, 5.0)
            s_time = max(0.0, s_time - 2.0 * len(LWc.get('dropped') or []))
            terr = (f"per word (ink - sung onset, s) {[r_['err'] for r_ in rows_]} -> {int(sum(r_['ok'] for r_ in rows_))}/{len(rows_)} "
                    f"within {LW_TOL:.2f} of a {LW_LEAD:.2f} lead, other note {wrong}, dropped words {LWc.get('dropped')}")
        rh = [w['letter_h'] for w in R['words']] or [0.055]
        lh = float(np.median([w['letter_h'] for w in W_]))
        s_size = band(lh / float(np.median(rh)), 0.8, 1.35, 0.5, 1.9)
        short = [w['t'] for w in W_[:-1] if w['on_s'] < 0.25]     # _sX2's own words hold 0.28-0.32 s (2026-10-23; was 0.35)
        smear_max = max([w['smear_frames'] / R['fps'] for w in R['words']] or [0.2]) + 0.05
        smear = [w['t'] for w in W_ if w['smear_frames'] / C['fps'] > smear_max]
        dim = [w['t'] for w in W_ if w['ink_luma'] < 45]
        s_leg = max(0.0, s_size - 2.0 * len(short) - 1.5 * len(smear) - 2.0 * len(dim))
        flags = (P or {}).get('lyric_flags') or []
        not_lyric = flags and not all(flags)
        # owner 2026-10-18: the shown words are the lyric text in order - no word twice in a row, no sung word missing. The render
        # writes the verdict (lyric_text.phrase_check, no words stored); on screen, one detected word entry per planned item
        ph = (P or {}).get('phrase')
        ph_bad = bool(ph) and not ph.get('ok')
        # the frame detector's entry count (it splits a smear-in / merges a short word); with the per-word check (plan item
        # starts + which word) the count is that check's business
        ent_bad = bool(P) and P.get('n_items') and abs(len(W_) - int(P['n_items'])) > 0 and not LWc.get('rows')
        S['3_lyrics'] = round(0.0 if (not_lyric or ph_bad) else (0.6 * s_time + 0.4 * s_leg) - (2.0 if ent_bad else 0.0), 2)
        E['3_lyrics'] = (f"{len(W_)} words at {[w['t'] for w in W_]}; {terr} -> {s_time:.1f}; letter height {lh:.3f} W "
                         f"(ref {np.median(rh):.3f}), shorter than 0.25 s {short}, smear > {smear_max:.2f} s {smear}, dim {dim} -> {s_leg:.1f}"
                         + ('; NON-LYRIC words on screen' if not_lyric else '')
                         + (f"; lyric sequence: {ph.get('why')} ({ph.get('shown_items')} items / {ph.get('lyric_words')} sung words)" if ph else
                            '; lyric sequence: not recorded (render before 2026-10-18)')
                         + (f"; {len(W_)} word entries on screen vs {P.get('n_items')} planned" if ent_bad else ''))
    # 4 mix
    A, RA = C['audio'], R['audio']
    s_lufs = band(A['lufs'], RA['lufs'] - 2.6, RA['lufs'] + 1.0, RA['lufs'] - 7.0, RA['lufs'] + 3.5)
    s_tp = lin(A['dbtp'], 0.5, -1.0)
    s_duck = band(A['intro_duck_db'], -1.0, 16.0, -5.0, 22.0) if A['intro_duck_db'] is not None and C['words'] else 10.0
    s_click = lin(A['first_10ms_peak'], 0.3, 0.05)
    s_end = lin(A['end_vs_body_db'], -3.0, -12.0)
    s_drop = lin(len(A['dropouts']), 4, 0)
    spd = (P or {}).get('speed')
    s_slow = band(spd, 0.85, 1.0, 0.75, 1.05) if spd else 8.0     # owner 2026-10-18: regular (1.0) or slowed versions
    s_only = 10.0 if (P is None or ((P.get('clip_audio') in (None, 'none')) and P.get('sfx') in (None, 'none'))) else 4.0
    S['4_mix'] = round(0.5 * min(s_lufs, s_tp, s_end, s_slow, s_only) + 0.5 * float(np.mean([s_lufs, s_tp, s_duck, s_click, s_end, s_drop, s_slow, s_only])), 2)
    E['4_mix'] = (f"{A['lufs']} LUFS (ref {RA['lufs']}) -> {s_lufs:.1f}; {A['dbtp']} dBTP -> {s_tp:.1f}; intro duck "
                  f"{A['intro_duck_db']} dB (ref {RA['intro_duck_db']}) -> {s_duck:.1f}; first 10 ms peak {A['first_10ms_peak']} -> "
                  f"{s_click:.1f}; end {A['end_vs_body_db']} dB vs body -> {s_end:.1f}; dropouts {A['dropouts']} -> {s_drop:.1f}; "
                  f"song speed {spd} -> {s_slow:.1f}; music only -> {s_only:.0f}")
    # 5 layout
    L5 = C['layout']
    s_size = 10.0 if (C['W'], C['H']) == (1080, 1920) else (6.0 if C['H'] > C['W'] else 0.0)
    rot = (P or {}).get('frame')
    s_rot = 10.0 if (P is None or str(rot).startswith('rot90') or str((P.get('deliver') or {}).get('mode', '')).startswith('rot90')) else 0.0
    s_hint = 10.0 if L5['hint_on'] >= 0.7 and L5['hint_after'] <= 0.15 else lin(L5['hint_on'], 0.1, 0.7) * (1.0 if L5['hint_after'] <= 0.15 else 0.6)
    s_wm = lin(L5['wm_on'], 0.05, 0.4)          # pale frames hide the 42 % white glyphs from the detector; the reference (no mark) 0.3 %
    s_bars = lin(L5['bar_rows_pct'], 6.0, 1.0)
    s_shake = lin(C['shake'], max(0.12, R['shake'] * 3), max(0.04, R['shake'] * 1.3))
    S['5_layout'] = round(0.5 * min(s_size, s_rot, s_hint, s_shake) + 0.5 * float(np.mean([s_size, s_rot, s_hint, s_wm, s_bars, s_shake])), 2)
    E['5_layout'] = (f"{C['W']}x{C['H']} -> {s_size:.0f}; frame {rot} -> {s_rot:.0f}; hint visible in {L5['hint_on']:.0%} of 0.4-4.5 s, "
                     f"{L5['hint_after']:.0%} of 6 s-mid -> {s_hint:.1f}; watermark visible in {L5['wm_on']:.0%} of frames -> {s_wm:.1f}; "
                     f"bars {L5['bar_rows_pct']} % -> {s_bars:.1f}; shake jitter {C['shake']} %W (ref {R['shake']}) -> {s_shake:.1f}")
    # 6 length
    S['6_length'] = round(band(C['dur'], LEN_LO, LEN_HI, 20.0, 58.0), 2)
    E['6_length'] = f"{C['dur']:.2f} s (owner range {LEN_LO:.0f}-{LEN_HI:.0f} s)"
    # 7 variety
    if items:
        srcs = [(it or {}).get('source_id') or os.path.basename(sh['path']) for it, sh in zip(items, P['shots'])]
        clips = [os.path.basename(sh['path']) for sh in P['shots']]
        from collections import Counter
        cs = Counter(srcs)
        ratio = len(cs) / max(1, len(srcs))
        rep = len(clips) - len(set(clips))
        s_src = lin(ratio, 0.4, 0.75)
        s_cap = 10.0 if max(cs.values()) <= 2 else lin(max(cs.values()), 5, 2)
        s_rep = 10.0 if rep == 0 else 0.0
        e_src = f"{len(cs)} sources / {len(srcs)} shots (max {max(cs.values())} per source) -> {s_src:.1f} / {s_cap:.1f}; clip repeats {rep}"
    else:
        s_src = s_cap = s_rep = 8.0
        e_src = 'no plan: sources n/a'
    s_dupv = lin(len(pc['near_duplicate_pairs']), 3, 0)
    s_colv = lin(pc['colour_spread'], 4.0, 0.7 * max(8.0, pr['colour_spread']))
    S['7_variety'] = round(0.5 * min(s_src, s_cap, s_rep) + 0.5 * float(np.mean([s_src, s_cap, s_rep, s_dupv, s_colv])), 2)
    E['7_variety'] = f"{e_src}; near-duplicate shots {pc['near_duplicate_pairs']} -> {s_dupv:.1f}; colour spread {pc['colour_spread']} (ref {pr['colour_spread']}) -> {s_colv:.1f}"
    # 8 cuts on the used audio's musical moments (owner 2026-10-19), the rule measured on _sX2 (R['cut_rule'])
    CA = C.get('cut_align')
    rule = R.get('cut_rule') or dict(on_meter=1.0, on_moment=1.0, offset_med=0.066, phrase_or_vocal=0.833)
    if CA:
        st = CA['stats']
        s_met = lin(st['on_meter'], 0.5, 0.9 * rule['on_meter'])
        WA_ = C.get('waves')
        met_note = ''
        if WA_ is not None:
            # round 3 (owner 2026-10-19, "momentum"): the switch belongs to the START of the wave; a sung attack off the
            # tracked main beat (rubato, a syncopated word; the owner called 3e's 17.27 s release cut "on the beat" where
            # the tracker says phase 0.43) is not off-meter. A cut on neither the meter nor a wave start still counts.
            okw = {round(r['cut'], 2) for r in WA_['rows'] if r['ok'] and ('wave start' in r['what'] or 'release' in r['what'])}
            ca_rows = C['cut_align']['rows']
            mw = float(np.mean([(r['phase'] is not None and r['phase'] <= 0.15) or round(r['cut'], 2) in okw
                                for r in ca_rows])) if ca_rows else 0.0
            s_met = lin(mw, 0.5, 0.9 * rule['on_meter'])
            met_note = f' (on the meter or on a sung wave start {mw:.0%})'
        s_mom = lin(st['on_moment'], 0.5, 0.85 * rule['on_moment'])
        ro = float(rule.get('offset_med') or 0.066)
        s_off = band(st['offset_med'], ro - 0.05, ro + 0.05, ro - 0.15, ro + 0.15)
        s_every = lin(st['one_beat_share'], 0.8, 0.55)          # owner: not every beat
        first = CA['rows'][0]['kind'] if CA['rows'] else 'none'
        s_hero = 10.0 if first in ('phrase', 'vocal') else (6.0 if first in ('accent', 'bar') else 2.0)
        S['8_cuts'] = round(0.5 * min(s_met, s_mom) + 0.5 * float(np.mean([s_met, s_mom, s_off, s_every, s_hero])), 2)
        off_rows = [(r['cut'], r['kind'], r['phase']) for r in CA['rows'] if r['kind'] in ('none', 'beat') or (r['phase'] or 0) > 0.15]
        E['8_cuts'] = (f"{st['n']} cuts vs the edit's own audio (main beat {st['main']} s = {st['k']} x the fast pulse): on the meter "
                       f"{st['on_meter']:.0%}{met_note} (ref {rule['on_meter']:.0%}) -> {s_met:.1f}; on a musical moment {st['on_moment']:.0%} "
                       f"(ref {rule['on_moment']:.0%}; phrase / sung syllable {st['phrase_or_vocal']:.0%}) -> {s_mom:.1f}; moment after "
                       f"the switch median {st['offset_med']} s (ref {ro}) -> {s_off:.1f}; 1-beat shots {st['one_beat_share']:.0%} -> "
                       f"{s_every:.1f}; first clip on {first} -> {s_hero:.0f}; off-moment / off-meter cuts {off_rows[:8]}")
    else:
        S['8_cuts'], E['8_cuts'] = 0.0, 'cut alignment not measured'
    # 9 no SFX: the mix is the song (gain changes only); no gate / stutter / riser / whoosh anywhere
    X = C.get('sfx')
    if X:
        nev = len(X['sfx_events']) + len(X['gates']) + len(X['gain_jumps'])
        s_res = lin(X['res_db_median'], -8.0, -16.0)
        S['9_no_sfx'] = round(max(0.0, min(s_res, 10.0 - 4.0 * nev)), 2)
        E['9_no_sfx'] = (f"mix vs the song version (lag {X['lag_ms']} ms, corr {X['corr']}): residual median {X['res_db_median']} dB / "
                         f"p95 {X['res_db_p95']} dB -> {s_res:.1f}; unexplained sound events {X['sfx_events']}, gates / stutters "
                         f"{X['gates']}, gain jumps {X['gain_jumps']}")
    else:
        S['9_no_sfx'], E['9_no_sfx'] = 0.0, 'not measured (needs --plan with the music path)'
    # 10 holds: no cut inside a held note / chord (owner 2026-10-19 round 2), offsets to the next moment like the reference
    HA = C.get('holds')
    hr = R.get('hold_rule')
    if HA:
        import music_moments as MM
        nin = len(HA['in_hold'])
        s_hold = max(0.0, 10.0 - 4.0 * nin)
        ks = MM.ks_distance(HA['offsets'], hr['offsets']) if hr else None
        s_ks = lin(ks, 0.8, 0.4) if ks is not None else 8.0
        # round 3 (2026-10-20): the KS part runs on the PLANNER's analysis (music_moments), the shared analysis that passed
        # 3e; the independent criterion 11 now judges the timing, so KS is shown for information and only cuts inside a
        # hold count here (a consistency guard)
        S['10_holds'] = round(s_hold if C.get('waves') is not None else min(s_hold, 0.5 * (s_hold + s_ks)), 2)
        if float((C.get('waves') or {}).get('tscale') or 1.0) < 1.0:
            # 2026-10-20: music_moments.holds() is calibrated on the slowed 0.85 audio like waves() was; on a faster version
            # its holds end too late (3i's "slow" switch 17.87 s = the approved 3g switch, 0.08 s before the next onset,
            # read as inside "You"). Criterion 11 judges holds at the reference tempo: 10 is information only here.
            S['10_holds'] = 10.0
        E['10_holds'] = (f"{len(HA['rows'])} cuts, inside a hold {nin} {HA['in_hold'][:10]} (a random switch would hit one "
                         f"{HA['null']:.0%} of the time; ref 0 / {len((hr or {}).get('offsets') or [])}) -> {s_hold:.1f}; cut -> next "
                         f"moment offset median {np.median(HA['offsets']):.3f} s (ref {np.median(hr['offsets']) if hr else 'n/a'}), "
                         f"KS distance {ks if ks is None else round(ks, 3)} -> {s_ks:.1f}" + (' (info only since round 3)' if C.get('waves') is not None else '')
                         + (' (info only: a faster version than the 0.85 calibration, criterion 11 judges the holds at the reference tempo)'
                            if float((C.get('waves') or {}).get('tscale') or 1.0) < 1.0 else ''))
    else:
        S['10_holds'], E['10_holds'] = 0.0, 'hold alignment not measured'
    # 11 waves (owner 2026-10-19 round 3, "momentum"; INDEPENDENT of the planner's analysis): every switch on a wave start
    # (or a release / a vocal gap), none mid-hold / mid-phrase, no strong wave start without a switch
    WA = C.get('waves')
    if WA is not None:
        nb, nm = len(WA['bad']), len(WA['missed'])
        S['11_waves'] = round(max(0.0, 10.0 - 4.0 * nb - 3.0 * nm), 2)
        E['11_waves'] = (f"{len(WA['rows'])} cuts: mid-wave {nb} {WA['bad'][:8]}, missed wave starts {nm} "
                         f"{[m['t'] for m in WA['missed']][:8]} (cut table: waves.md, timeline: waves.png)")
    else:
        S['11_waves'], E['11_waves'] = 0.0, 'wave judge not measured'
    # 12 A/V sync (2026-10-20): the rendered cuts vs the rendered audio's strong wave starts, > SYNC_TOL systematic = fail
    SY = C.get('sync')
    if SY is not None:
        av = SY.get('av') or {}
        lg = [x for x in av.get('lags_ms') or [] if x[2] >= 0.5]       # windows where the song is clearly identified
        av_lag = max([abs(x[1]) for x in lg] or [0.0]) / 1000.0
        av_cut = max([abs(x[2]) for x in av.get('cut_err') or []] or [0.0])
        SY['av_lag_max_s'], SY['av_cut_max_s'] = round(av_lag, 3), round(av_cut, 3)
        if av_lag > SYNC_TOL or av_cut > SYNC_TOL:
            SY['ok'] = False
        LS = SY.get('lines') or {}
        if LS.get('off'):
            SY['ok'] = False               # a lyric line start off by > SYNC_TOL (or without a switch) is heard as off-sync
        S['12_sync'] = round(max(0.0, 10.0 - 1.5 * len(SY['off']) - 10.0 * max(0.0, SY['mean_abs'] - 0.05)
                                 - 10.0 * max(0.0, SY['median_abs'] - 0.05)), 2) if SY['ok'] else 0.0
        w_ = SY['worst']
        E['12_sync'] = (f"{SY['n']} strong wave starts with a switch near: |error| median {SY['median_abs']} s, mean "
                        f"{SY['mean_abs']} s (tol {SYNC_TOL}), off-sync {len(SY['off'])} {SY['off'][:8]}, longest off run {SY['run']}"
                        + (f"; worst {w_['start']:.2f} s ({w_['word'] or '?'}) switch {w_['cut']:.2f} err {w_['err']:+.2f} s" if w_ else '')
                        + f" (tempo scale {(C.get('waves') or {}).get('tscale')}); render vs plan: audio lag max "
                        f"{SY.get('av_lag_max_s')} s over {len((SY.get('av') or {}).get('lags_ms') or [])} windows, cut drift max "
                        f"{SY.get('av_cut_max_s')} s"
                        + (f"; lyric line starts {len(LS.get('rows') or [])}, off-sync {LS.get('off')}"
                           + ''.join(f" [{r_['line'][:18]!r} {r_['t']:.2f} attack {r_['attack']} switch {r_['cut']} err {r_['err']}]"
                                     for r_ in LS.get('rows') or [] if r_['t'] in LS.get('off'))
                           if LS else '; lyric line starts n/a'))
    else:
        S['12_sync'], E['12_sync'] = 0.0, 'A/V sync not measured'
    overall = round(float(np.mean(list(S.values()))), 2)
    ok = all(x >= PASS_EACH for x in S.values()) and overall >= PASS_ALL
    if WA is not None and (WA['bad'] or WA['missed']):
        ok = False
    if SY is not None and not SY['ok']:
        ok = False                         # a systematic A/V offset fails whatever the average says
    TO = C.get('tail_outro')
    if TO is not None:
        S['13_hold_outro'] = round(max(0.0, 10.0 - 4.0 * len(TO['bad'])), 2)
        E['13_hold_outro'] = (f"held-note tails {TO['tails']}, outro from {TO['outro']} s; cuts inside {len(TO['bad'])}: "
                              + ('; '.join(f'{c:.2f} {w}' for c, w in TO['bad']) or 'none'))
        if TO['bad']:
            ok = False                     # a cut inside a held note's tail / in the outro fails (owner rule)
        overall = round(float(np.mean(list(S.values()))), 2)
        ok = ok and overall >= PASS_ALL and all(x >= PASS_EACH for x in S.values())
    else:
        S['13_hold_outro'], E['13_hold_outro'] = 0.0, 'hold tail / outro check not measured'
        ok = False                         # any mid-wave cut or missed strong wave start fails (owner rule)
    GC = C.get('groove')
    if GC is not None and GC.get('groove'):
        sh = float(GC['share'])
        S['14_groove'] = round(max(0.0, min(10.0, 10.0 - 5.0 * max(0.0, sh - 0.10))), 2)
        kinds = [r_['kind'] for r_ in GC['rows']]
        E['14_groove'] = (f"groove song (pulse {GC['pulse']}, beat {GC.get('period')} s): {len(kinds)} body cuts, on the beat "
                          f"{kinds.count('beat')}, tight on the sung attack {kinds.count('tight')}, release / gap {kinds.count('other')}, "
                          f"LOOSE {len(GC['loose'])} ({sh:.0%}, max {JG_LOOSE_MAX:.0%}) {GC['loose'][:10]}")
        if sh > JG_LOOSE_MAX and len(GC['loose']) >= JG_LOOSE_MIN:
            ok = False
            S['14_groove'] = min(S['14_groove'], 5.0)
        overall = round(float(np.mean(list(S.values()))), 2)
        ok = ok and overall >= PASS_ALL and all(x >= PASS_EACH for x in S.values())
    elif GC is not None:
        E['14_groove'] = f"no steady pulse (pulse {GC.get('pulse')} < {JG_PULSE}): the ballad rule (11 / 12) only"
    return S, E, overall, ok


# ------------------------------------------------------------------------------------------------ 11 waves (independent)
# Owner 2026-10-19 round 3 ("momentum"): one clip carries one wave of the sung line; the switch sits at the START of the
# next wave; never a switch inside a held note / a phrase, never a quick filler clip before a hold ends, and never a new
# wave without a switch. 3e passed criterion 10 because the judge used the planner's own hold analysis. This check does
# NOT use music_moments (the planner's analysis): its own loudness (vocal-band mel energy, 20 ms), its own pitch tracker
# (YIN, not pyin), its own attack detector (the level climbing >= JW_RISE dB out of a local minimum, or a voice entering
# after silence) and faster-whisper MEDIUM word starts on the vocal stem (the lyric words, for the table and to confirm
# an attack). Nothing is stored except the shared Demucs stem cache.
JW_HOP = 0.02
JW_ACT = -28.0           # voice active (dB re the p95 frame of the vocal band)
JW_RISE = 6.0            # an attack: the level climbs this much within 0.16 s out of the minimum of the 0.30 s before
JW_STRONG = 9.0          # a strong attack (a new word / line nobody may miss): this rise, or a voice after >= 0.2 s silence
JW_HIT = (-0.10, 0.15)   # a switch is ON a wave start from 0.15 s before the previous note falls to 0.10 s after the new one
                         # reaches half its climb (the reference: the switch leads the sung onset by 0.03-0.16 s)
JW_MISS = (-0.30, 0.12)  # a strong attack with no switch in [its fall - 0.30, its climb + 0.12] is missed
JW_JOIN = 0.45           # boundaries this close are one wave start (the breath / consonant and the attack)
JW_CARRY = 0.25          # a missed wave start must carry: >= this long within 10 dB of its peak (0.6 s window)
JW_MIN_SHOT = 0.70       # an attack closer than this after a switch cannot take another one (it is that shot's own start)
JW_SAME = 0.8            # semitones: the same held note on both sides of a dip
JW_HELD_DIP = 11.0       # ... unless the level falls this far (a new word on the same pitch)
JW_TIMBRE = 1.3          # ... or the timbre changes this much (x median)
JW_JUMP = 0.8            # semitones between two steady sung pitches = a legato attack (_sX2 den -> hour: ~1)
JW_RELEASE = 7.0         # a long held note (>= 1.2 s) that falls this much (0.3 s means) and goes on quieter: a release
JW_REF_SPEED = 0.85      # every JW_* duration was calibrated on SLOWED 0.85 audio (_sX2, 3e, 3g). 2026-10-20 (owner on 3h, the
                         # REGULAR version: "after 15 s the timing is 1-2 s off"): at speed 1.0 the syllables are 15 % shorter,
                         # so a cut one syllable early / late still sat on "a" wave start and JW_MIN_SHOT excused the word it
                         # missed (3h passed 9.94). A faster version is judged at the reference tempo: its MIX is resampled
                         # (pitch follows) before Demucs and the event times are mapped back.
SYNC_TOL = 0.15          # 12_sync: the switch -> wave-start error the edit may show systematically (s)
# 14 groove v2 (2026-10-23, owner on the "blue" dry run: "the transition timing is off, the momentum logic needs updating").
# Measured with this judge's own analysis on the rendered audio of real edits of "blue" (6LM8-q3PnVs 1.5 M views, Ulco4K08-u4,
# EEdpjFviGJg; 1CFWede1erg = a fast-cut AMV, 49 cuts in 41 s, and MS3S7SaPJN0 = an MV whose cuts sit a constant 0.16 s early,
# an upload A/V offset, are reported but not used to set the bar) vs our round-6 render (refs/new-types-progress.md, 2026-10-23):
# - a groove song is cut TIGHT on the sung attack: the cut sits 0..0.06 s before the attack (6LM8 median 0.02-0.03 s) or on the
#   beat itself (cut - beat -0.07..+0.03 s). Our r6 sat 0.08-0.11 s before the attack on 10 / 23 cuts (the 0.10 s lead and the
#   beat - 0.065 s snap calibrated on Golden Hour, whose soft legato onsets hide a 3-frame lead; blue's short percussive words
#   do not): 6LM8 1 / 15 loose, Ulco 2 / 7, EEdp 2 / 5, ours 8 / 23.
# - the momentum unit of a groove song is the PHRASE: the refs leave about half the word-level starts uncut (a shot carries
#   2-8 beats, up to ~6 s over several words; they skip the legato / stepwise continuations), so a strong word start is only
#   'missed' when the shot over it already runs >= JG_MISS_SHOT s. Ballads (Golden Hour) keep the approved every-wave rule.
JG_PULSE = 0.45          # pulse clarity (autocorrelation peak of the percussive onsets, 0.3-1.3 s) of a groove song
                         # (blue windows 0.43-0.56 on 5 / 6 edits; Golden Hour 0.29 / 0.34, Until I Found You 0.23 / 0.43)
JG_TIGHT = (-0.06, 0.07) # (s) attack - switch for a switch tight on the sung attack (refs: 6LM8 all 13 within it)
JG_BEAT = (-0.07, 0.03)  # (s) switch - beat for a switch on the beat
JG_LOOSE_MAX = 0.30      # more loose switches than this share of the judged body cuts (and >= JG_LOOSE_MIN) = NOT PASS
JG_LOOSE_MIN = 3
JG_MISS_SHOT = 6.0       # groove: a strong word start without a switch is 'missed' only when the shot over it is this long
SYNC_NEAR = 0.80         # a strong wave start and a switch this close belong together (the switch was meant for it)


def _jw_runs(mask):
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def _jw_features(v16, sr=16000):
    import librosa
    from scipy.ndimage import median_filter
    hop = int(JW_HOP * sr)
    S = librosa.feature.melspectrogram(y=v16.astype(np.float32), sr=sr, n_fft=1024, hop_length=hop, n_mels=40,
                                       fmin=250.0, fmax=4000.0)
    e = S.sum(axis=0)
    lv = 10 * np.log10(e / (np.percentile(e, 95) + 1e-12) + 1e-7)
    lv = median_filter(lv, 3)
    f0 = librosa.yin(v16.astype(np.float32), fmin=80.0, fmax=1000.0, sr=sr, frame_length=1024, hop_length=hop)[:len(lv)]
    f0 = np.pad(f0, (0, max(0, len(lv) - len(f0))))
    m = librosa.hz_to_midi(np.maximum(f0, 1.0))
    m[lv < JW_ACT] = np.nan
    # timbre change (MFCC 2-14, 0.16 s before vs after, / median): a new syllable changes the vowel / consonant colour,
    # a tremolo dip inside a held vowel does not (3e "hour": <= 1.2 inside, 1.4-3.0 at word starts)
    M = librosa.feature.mfcc(y=v16.astype(np.float32), sr=sr, n_mfcc=14, hop_length=hop, n_fft=1024)[1:, :len(lv)]
    tim = np.zeros(len(lv))
    for i in range(12, M.shape[1] - 12):
        tim[i] = float(np.linalg.norm(M[:, i - 10:i - 2].mean(1) - M[:, i + 3:i + 11].mean(1)))
    tim /= float(np.median(tim[tim > 0])) + 1e-9
    return dict(lv=lv, midi=m, tim=tim, n=len(lv))


def _jw_words(v16):
    try:
        from faster_whisper import WhisperModel
        mdl = WhisperModel('medium', device='cpu', compute_type='int8', cpu_threads=2)
        segs, _ = mdl.transcribe(v16, language='en', word_timestamps=True, vad_filter=False, beam_size=3,
                                 condition_on_previous_text=False)
        return [(round(float(w.start), 2), round(float(w.end), 2), w.word.strip()) for s in segs for w in (s.words or [])]
    except Exception as e:  # noqa: BLE001
        print('[judge_slowmo] waves: no whisper words:', repr(e)[:160], file=sys.stderr)
        return []


def _jw_tscale(speed):
    try:
        r = JW_REF_SPEED / float(speed)
    except (TypeError, ValueError, ZeroDivisionError):
        return 1.0
    return round(r, 4) if r < 0.97 else 1.0


def _jw_ref_stem(path, dur, r):
    """Demucs vocals of the rendered file's audio resampled to play 1 / r times slower (the reference tempo)"""
    import tempfile
    import soundfile as sf
    import lyrics as LY
    from fractions import Fraction
    from scipy.signal import resample_poly
    y = LY.load_audio(path, 0.0, float(dur), 44100, 2).astype(np.float64)
    fr_ = Fraction(1.0 / r).limit_denominator(400)
    y = resample_poly(y, fr_.numerator, fr_.denominator, axis=-1)
    y *= 0.89 / max(1e-9, float(np.max(np.abs(y))))
    fd, wp = tempfile.mkstemp(suffix='.wav', prefix='judge-reftempo-')
    os.close(fd)
    try:
        sf.write(wp, y.T.astype(np.float32), 44100)
        return LY.vocal_stem(wp, 0.0, float(dur) / r, threads=2)
    finally:
        try:
            os.remove(wp)
        except OSError:
            pass


def _jw_clean(x):
    """finite pitch frames within 3 st of their median (YIN octave slips dropped)"""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if not len(x):
        return x
    return x[np.abs(x - np.median(x)) <= 3.0]


def wave_events(path, dur, words=True, speed=None):
    """the sung line of the edit's own audio as attacks (wave starts) and holds -> dict(attacks [(t, rise dB, strong,
    why)], releases [t], F, words). speed = the song version's playback speed: a faster version than JW_REF_SPEED is
    measured at the reference tempo (all times mapped back to the rendered file's seconds; F['hop'] = seconds per frame)"""
    import lyrics as LY
    r = _jw_tscale(speed)
    v16 = LY.vocal_stem(path, 0.0, float(dur), threads=2) if r >= 1.0 else _jw_ref_stem(path, dur, r)
    F = _jw_features(v16)
    lv, n = F['lv'], F['n']
    fr = 1.0 / JW_HOP
    act = lv >= JW_ACT
    mid = F['midi']

    def _pm(a, b):
        x = mid[max(0, a):max(0, b)]
        x = x[np.isfinite(x)]
        return float(np.median(x)) if len(x) >= 4 else None
    # boundaries between two sung sounds: a dip (the level falls >= JW_RISE under the note before and climbs >= JW_RISE
    # again within 0.6 s: a consonant, a breath, a swell starting soft) or a voice entering after silence. Each is an
    # interval [the previous note starts to fall, the new one reaches half its climb]: a switch inside it (or just before)
    # is ON the wave start
    from scipy.signal import find_peaks as _fpk
    w5, w6 = int(0.5 * fr), int(0.6 * fr)
    cand = []
    for i in _fpk(-lv, distance=4)[0]:
        if i < 3 or i > n - 3:
            continue
        left = float(lv[max(0, i - w5):i].max())
        right = float(lv[i:i + w6].max())
        drop, rise = left - float(lv[i]), right - float(lv[i])
        if min(drop, rise) < JW_RISE or right < JW_ACT + 6:
            continue
        pb, pa = _pm(i - int(0.35 * fr), i - 3), _pm(i + 3, i + int(0.35 * fr))
        if pb is not None and pa is not None and abs(pa - pb) <= JW_SAME and min(drop, rise) < JW_HELD_DIP \
                and float(F['tim'][max(0, i - 8):i + 9].max()) < JW_TIMBRE:
            continue                       # (same pitch, shallow, same vowel colour: still the held note)
        j0 = i
        while j0 > max(0, i - w5) and lv[j0 - 1] < left - 3.0:
            j0 -= 1
        j1 = i
        while j1 < min(n - 1, i + w6) and lv[j1] < lv[i] + 0.5 * rise:
            j1 += 1
        cand.append([j0, j1, min(drop, rise), False, 'attack'])
    for ra, rb in _jw_runs(act):
        if ra >= int(0.2 * fr) and not act[max(0, ra - int(0.2 * fr)):ra].any() and rb - ra >= int(0.15 * fr):
            pk_ = float(lv[ra:ra + w6].max())
            j1 = ra
            while j1 < ra + w6 and lv[j1] < pk_ - 6.0:
                j1 += 1                    # (a line starts on a breath / consonant: its attack is where it gets loud)
            cand.append([ra, j1, pk_ - JW_ACT, True, 'phrase'])
    # a sung pitch jump between two steady notes (legato, no dip): an attack too (_sX2 "den" -> "hour", 3e "you" 20.2 s)
    for i in range(int(0.3 * fr), n - int(0.3 * fr)):
        if not act[i] or any(c[0] - int(0.3 * fr) <= i <= c[1] + int(0.3 * fr) for c in cand):
            continue
        a_ = mid[i + 2:i + 2 + int(0.14 * fr)]
        b_ = mid[i - int(0.30 * fr):i - 3]
        if np.isfinite(a_).sum() < 5 or np.isfinite(b_).sum() < 5:
            continue
        # (YIN octave slips - single frames 6-12 st off, 3i "You" 17.1 s - are not the note: dropped before the test)
        a_, b_, b7 = _jw_clean(a_), _jw_clean(b_), _jw_clean(b_[-7:])
        if min(len(a_), len(b7)) < 4 or len(b_) < 5:
            continue
        if float(np.max(a_) - np.min(a_)) > 0.6 or float(np.max(b7) - np.min(b7)) > 0.6:
            continue                       # (both notes steady: a glide into a note is not two attacks)
        if abs(float(np.median(a_)) - float(np.median(b7))) >= JW_JUMP and                 abs(float(np.median(a_)) - float(np.median(b_))) >= JW_JUMP:
            cand.append([i - 3, i + 2, 0.0, False, 'pitch'])
    cand.sort()
    A = []
    for c in cand:                         # overlapping / touching intervals are one boundary
        if A and c[0] <= A[-1][1] + 2:
            A[-1][1] = max(A[-1][1], c[1])
            A[-1][2] = max(A[-1][2], c[2])
            A[-1][3] = A[-1][3] or c[3]
            if c[4] == 'phrase':
                A[-1][4] = 'phrase'
            continue
        A.append(list(c))
    out = [(round(j1 * JW_HOP, 3), round(r, 1), bool(s or r >= JW_STRONG), why, round(j0 * JW_HOP, 3))
           for j0, j1, r, s, why in A]
    # releases: a long held note falls >= JW_RELEASE and goes on (owner: the switch at the end of the held "hour" is good)
    rel = []
    q = int(0.3 * fr)
    for i in range(int(1.2 * fr), n - q - 2):
        if not act[i - int(1.2 * fr):i].all() or not act[i + 2:i + q].mean() > 0.8:
            continue
        drop = float(lv[i - q:i].mean() - lv[i + 2:i + 2 + q].mean())
        if drop >= JW_RELEASE and not any(abs(i * JW_HOP - x[0]) < 1.0 for x in out if x[0] <= i * JW_HOP) \
                and not any(abs(i * JW_HOP - t) < 0.5 for t in rel):
            rel.append(round(i * JW_HOP, 3))
    W = _jw_words(v16) if words else []
    # a whisper word start within 0.3 s confirms an attack as a word (strong when it rises >= JW_RISE + 1)
    out = [(t, rr, st or (rr >= JW_RISE + 1.0 and any(abs(t - w[0]) <= 0.3 for w in W)), why, t0) for t, rr, st, why, t0 in out]
    if r < 1.0:                            # reference-tempo seconds -> the rendered file's seconds
        out = [(round(t * r, 3), rr, st, why, round(t0 * r, 3)) for t, rr, st, why, t0 in out]
        rel = [round(t * r, 3) for t in rel]
        W = [(round(a * r, 2), round(b * r, 2), w) for a, b, w in W]
    F['hop'] = JW_HOP * r
    return dict(attacks=out, releases=rel, F=F, words=W, dur=float(dur), tscale=r)


def _jw_groove(path, dur):
    """the rendered mix's percussive pulse (own code: HPSS, onset autocorrelation, beat tracking on the percussive part)
    -> dict(groove, pulse, period, beats)"""
    import librosa
    import lyrics as LY
    sr = 22050
    y = LY.load_audio(path, 0.0, float(dur), sr, 1)[0].astype(np.float32)
    _, yp = librosa.effects.hpss(y)
    env = librosa.onset.onset_strength(y=yp, sr=sr, hop_length=512)
    z = (env - env.mean()) / (env.std() + 1e-9)
    ac = np.correlate(z, z, 'full')[len(z) - 1:] / max(1, len(z))
    fr = sr / 512.0
    lo, hi = int(0.3 * fr), int(1.3 * fr)
    k = lo + int(np.argmax(ac[lo:hi])) if len(ac) > hi else lo
    pulse = float(ac[k]) if len(ac) > k else 0.0
    _, bt = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=512, units='time', tightness=200)
    bt = np.asarray(bt, float)
    per = float(np.median(np.diff(bt))) if len(bt) > 2 else k / fr
    return dict(groove=bool(pulse >= JG_PULSE), pulse=round(pulse, 3), period=round(per, 3), beats=[round(float(x), 3) for x in bt])


def groove_check(E, G, cuts, body_from, rows=None, end_guard=1.0, dur=None):
    """14: each body switch ON a main beat, TIGHT on a sung attack, or a release / vocal-gap switch; else loose"""
    if not G or not G.get('groove'):
        return dict(groove=False, pulse=(G or {}).get('pulse'), rows=[], loose=[], share=0.0)
    bt = np.asarray(G['beats'], float)
    other = {r_['cut'] for r_ in rows or [] if not str(r_['what']).startswith('wave start')}
    res = []
    for c in cuts:
        if c < body_from + 0.3 or (dur is not None and c > dur - end_guard):
            continue
        near = [a[0] - c for a in E['attacks'] if a[2] and -0.15 <= a[0] - c <= 0.40]
        if any(JG_TIGHT[0] <= x <= JG_TIGHT[1] for x in near):
            k = 'tight'
        elif len(bt) and JG_BEAT[0] <= c - float(bt[np.argmin(np.abs(bt - c))]) <= JG_BEAT[1]:
            k = 'beat'
        elif not near or round(c, 3) in other:
            k = 'other'                    # no sung attack it announces (a vocal gap / release switch): not judged here
        else:
            k = 'loose'
        nxt = [a[0] - c for a in E['attacks'] if -0.2 <= a[0] - c <= 0.5]
        res.append(dict(cut=round(c, 3), kind=k, attack_after=round(min(nxt, key=abs), 3) if nxt else None))
    loose = [r_['cut'] for r_ in res if r_['kind'] == 'loose']
    n = len([r_ for r_ in res if r_['kind'] != 'other'])
    return dict(groove=True, pulse=G['pulse'], period=G['period'], rows=res, loose=loose,
                share=round(len(loose) / max(1, n), 3))


def _jw_state(E, t):
    """what the voice is doing at t: ('gap'|'hold'|'phrase', since the last attack s, until the next attack s)"""
    F = E['F']
    i = int(round(t / F.get('hop', JW_HOP)))
    if i >= F['n'] or F['lv'][min(F['n'] - 1, i)] < JW_ACT:
        return 'gap', None, None
    prev = [a[0] for a in E['attacks'] if a[0] <= t]
    nxt = [a[4] for a in E['attacks'] if a[4] > t]
    m = F['midi'][max(0, i - 8):i + 9]
    m = m[np.isfinite(m)]
    stable = len(m) >= 8 and float(np.percentile(m, 75) - np.percentile(m, 25)) <= 0.8
    return ('hold' if stable else 'phrase'), (t - prev[-1]) if prev else None, (nxt[0] - t) if nxt else None


def wave_judge(path, cuts, dur, body_from=None, end_guard=1.0, E=None, speed=None):
    """per cut: the attack it announces / the state it falls in; MISSED strong attacks. -> dict(rows, missed, bad, E, sync)"""
    if E is None:
        E = wave_events(path, dur, speed=speed)
    if 'groove' not in E:
        try:
            E['groove'] = _jw_groove(path, dur) if os.environ.get('JUDGE_GROOVE', '1') != '0' else None
        except Exception as e:  # noqa: BLE001
            print('[judge_slowmo] groove analysis failed:', repr(e)[:200], file=sys.stderr)
            E['groove'] = None
    tsc = float(E.get('tscale') or 1.0)
    hop = E['F'].get('hop', JW_HOP)
    cuts = sorted(cuts)
    grv = bool((E.get('groove') or {}).get('groove'))
    body_from = cuts[0] if body_from is None and cuts else (body_from or 0.0)
    rows, bad = [], []
    for k, c in enumerate(cuts):
        hit = [a for a in E['attacks'] if a[4] - JW_HIT[1] <= c <= a[0] - JW_HIT[0]]
        if not hit and grv:
            # groove v2: a switch up to 0.15 s ahead of a strong sung attack announces it (a legato onset has no fall to
            # time against); how tight it is, is criterion 14's question (it counts there as loose beyond 0.07 s)
            hit = [a for a in E['attacks'] if a[2] and JW_HIT[0] <= a[0] - c <= JW_HIT[1]]
        rel = [r for r in E['releases'] if -0.15 <= r - c <= 0.22]
        near = min(E['attacks'], key=lambda a: abs(a[0] - c)) if E['attacks'] else None
        gap = round(near[0] - c, 3) if near else None
        st, since, until = _jw_state(E, c + 0.08)
        word = next((w[2] for w in E['words'] if w[0] - 0.3 <= c + 0.1 <= w[1] + 0.1), '')
        if hit:
            what = 'wave start (%s, +%.0f dB)' % (hit[0][3], hit[0][1])
            ok = True
        elif rel:
            what = 'release (the held note falls away)'
            ok = True
        elif st == 'gap':
            ok = until is None or until <= 0.6 or k == len(cuts) - 1
            what = 'vocal gap' + ('' if ok else f' (next attack {until:.2f} s later)')
        else:
            what = ('mid-hold' if st == 'hold' else 'mid-phrase') + (f' ({since:.2f} s into it, next attack in {until:.2f} s)'
                                                                     if since is not None and until is not None else '')
            ok = False
        rows.append(dict(cut=round(c, 3), what=what, ok=ok, gap_to_wave_start=gap, word=_mask(word)))
        if not ok:
            bad.append(round(c, 3))
    missed = []
    for t, r, strong, why, t0 in E['attacks']:
        if not strong or t < body_from + 0.3 or t > dur - end_guard:
            continue
        if any(t0 + JW_MISS[0] <= c <= t + JW_MISS[1] for c in cuts):
            continue
        if any(t < a[4] <= t + JW_JOIN * tsc and any(a[4] - JW_HIT[1] <= c <= a[0] - JW_HIT[0] for c in cuts)
               for a in E['attacks']):
            continue                       # (a breath / pick-up right before the attack the switch is on: the same wave start)
        last = max([c for c in cuts if c <= t] or [-9.0])
        if t - last < JW_MIN_SHOT * tsc:
            continue                       # (too close after a switch to take another: that shot starts with it)
        if grv and t - last < JG_MISS_SHOT:
            continue                       # groove song: one shot carries a phrase of several words (the real edits, 14)
        i = int(round(t / hop))
        pk = float(E['F']['lv'][i:i + int(0.4 / JW_HOP)].max())
        if pk < -15.0:
            continue                       # a faint line (a fading tail): not a wave nobody may miss
        seg_ = E['F']['lv'][i:i + int(0.6 / JW_HOP)]
        if float((seg_ >= pk - 10.0).sum()) * JW_HOP < JW_CARRY:
            continue                       # a blip (an ad-lib syllable in a fading tail, 3h 27.98 s): it carries no wave
        word = next((w[2] for w in E['words'] if abs(w[0] - t) <= 0.35), '')
        missed.append(dict(t=round(t, 3), rise_db=r, why=why, word=_mask(word), prev_cut=round(last, 3)))
    try:
        TO = tail_outro_check(path, E, cuts, dur, body_from)
    except Exception as e:  # noqa: BLE001
        print('[judge_slowmo] tail / outro check failed:', repr(e)[:200], file=sys.stderr)
        TO = None
    if TO is not None:
        # inside a held note's decay tail (an echo / ad-lib over the ringing chord) or in the outro no switch is wanted
        # (owner 2026-10-20 on 3i): not 'missed'
        missed = [m for m in missed if not any(a_ + 0.3 < m['t'] < e_ for a_, e_ in TO['tails'])
                  and not (TO['outro'] is not None and m['t'] > TO['outro'] + 0.5)]
    GC = groove_check(E, E.get('groove'), cuts, body_from, rows, end_guard, dur)
    return dict(rows=rows, missed=missed, bad=bad, E=E, sync=sync_check(E, cuts, dur, body_from, end_guard), tail_outro=TO,
                groove=GC)


def av_drift(path, music, m_start, dur, plan_cuts=None, cuts=None, win=2.0):
    """the literal A/V sync of the RENDERED file: its audio against the song version it plays (plan music / music_start),
    lag per 2 s window across the edit (a constant lag = an offset, a growing one = drift: segment joins, a wrong speed
    mapping), and its detected cuts against the plan's cut times. -> dict(lags_ms [(t, lag)], cut_err [(plan, found, err)])"""
    import lyrics as LY
    sr = 8000
    y = LY.load_audio(path, 0.0, float(dur), sr, 1)[0].astype(np.float64)
    ref = LY.load_audio(music, float(m_start) - 1.0, float(m_start) + float(dur) + 1.0, sr, 1)[0].astype(np.float64)
    lags = []
    for t in np.arange(1.0, float(dur) - win - 0.5, 4.0):
        seg = y[int(t * sr):int((t + win) * sr)]
        if float(np.std(seg)) < 1e-4:
            continue
        r0 = int((t + 1.0 - 1.0) * sr)                # ref index of output t - 1 s (the ref starts 1 s before m_start)
        rs = ref[r0:r0 + len(seg) + 2 * sr]
        if len(rs) < len(seg) + 2 * sr:
            continue
        n = 1 << int(np.ceil(np.log2(len(rs) + len(seg))))
        cc = np.fft.irfft(np.fft.rfft(rs, n) * np.conj(np.fft.rfft(seg, n)), n)[:2 * sr + 1]
        k = int(np.argmax(cc))
        nrm = float(np.linalg.norm(seg) * np.linalg.norm(rs[k:k + len(seg)]) + 1e-9)
        lags.append((round(float(t), 1), round((k - sr) / sr * 1000.0, 1), round(float(cc[k]) / nrm, 3)))
    ce = []
    if plan_cuts and cuts:
        for pc in plan_cuts:
            f = min(cuts, key=lambda c: abs(c - pc))
            ce.append((round(pc, 3), round(f, 3), round(f - pc, 3)))
    return dict(lags_ms=lags, cut_err=ce)


def line_sync(E, cuts, dur, lrc_id, m_start, speed, body_from, end_guard=1.0):
    """12 A/V sync, the word-level anchor (2026-10-20): every synced LYRIC LINE start in the body is a new wave nobody may
    miss (owner on 3h: "the part 'you' should switch but it is 1 s delay" = the line "You slow down time"). The line times
    come from LRCLIB (render-time only, never stored) on the ORIGINAL song: edit seconds = T / speed - music_start; each is
    refined to the first sung attack of the rendered audio within [T - 0.25, T + 0.40] (LRC lines are ~0.1-0.3 s loose).
    error = how far the nearest switch (within 1 s) sits outside [that attack's fall - 0.15, its climb + 0.10].
    -> dict(rows [(line, t, attack, cut, err)], off) or None when no synced lyrics are available"""
    try:
        import lyrics as LY
        c = LY._get(f'get/{int(lrc_id)}', {})
        rec = LY._record(c, 'lrclib:id') if c else None
    except Exception as e:  # noqa: BLE001
        print('[judge_slowmo] line sync: no lyrics:', repr(e)[:160], file=sys.stderr)
        return None
    if not rec or not rec.get('synced'):
        return None
    rows = []
    for L in rec['lines']:
        if L.get('t') is None or not str(L.get('text') or '').strip():
            continue
        if not any(ch.isalnum() for ch in str(L['text'])):
            continue                       # an instrumental marker line ('♫♫♫', blue 39.64 s): nothing is sung there
        t = float(L['t']) / float(speed or 1.0) - float(m_start)
        if t < body_from + 0.3 or t > dur - end_guard:
            continue
        att = [a for a in E['attacks'] if t - 0.25 <= a[0] <= t + 0.40]
        a = min(att, key=lambda a_: a_[0]) if att else None
        lo, hi = (a[4] - JW_HIT[1], a[0] - JW_HIT[0]) if a else (t - 0.25, t + 0.15)
        near = [c_ for c_ in cuts if abs(c_ - t) <= 1.0]
        if near:
            cut = min(near, key=lambda c_: 0.0 if lo <= c_ <= hi else min(abs(c_ - lo), abs(c_ - hi)))
            err = 0.0 if lo <= cut <= hi else (cut - hi if cut > hi else cut - lo)
        else:
            cut, err = None, None
        rows.append(dict(line=f"line {len(rows) + 1} ({len(str(L['text']).split())} words)", t=round(t, 3), attack=round(a[0], 3) if a else None,
                         cut=None if cut is None else round(cut, 3), err=None if err is None else round(err, 3)))
    off = [r_ for r_ in rows if r_['err'] is None or abs(r_['err']) > SYNC_TOL]
    return dict(rows=rows, off=[r_['t'] for r_ in off])


TO_SIM, TO_BREAK, TO_LINE_END, TO_DROP = 0.80, 0.40, 1.0, 12.0


def tail_outro_check(path, E, cuts, dur, body_from):
    """13 holds + outro (owner 2026-10-20 on 3i: "30 s it transitions but it shouldn't, it is on the hoouuurrr hold; the violin
    part: just end with that one clip till the end"). Measured on the RENDERED file, own code (not music_moments):
    (a) a strong sung attack whose line ENDS (no strong loud attack within TO_LINE_END s after its sustain) holds while the
    harmony of the mix (harmonic chroma, cosine >= TO_SIM to the held part, broken only after TO_BREAK s) rings on it and
    until a new sung line (a loud attack that carries >= 0.7 s within 10 dB): a cut inside = BAD;
    (b) the outro = the low band (40-160 Hz) drops >= TO_DROP dB in the last 12 s and stays down to the end: a cut more than
    0.5 s after its start = BAD. -> dict(tails [(a, end)], outro, bad [(cut, why)])"""
    import librosa
    import lyrics as LY
    sr = 22050
    y = LY.load_audio(path, 0.0, float(dur), sr, 1)[0].astype(np.float32)
    yh = librosa.effects.harmonic(y)
    hop_c = 0.1
    Cc = librosa.feature.chroma_cqt(y=yh, sr=sr, hop_length=int(hop_c * sr))
    Cc = Cc / (np.linalg.norm(Cc, axis=0, keepdims=True) + 1e-9)
    F = E['F']
    hop = F.get('hop', JW_HOP)
    lv = F['lv']

    def peak(t, w=0.4):
        i = int(round(t / hop))
        return float(lv[i:i + max(1, int(w / JW_HOP))].max()) if i < len(lv) else -99.0

    def carry(t):
        i = int(round(t / hop))
        seg = lv[i:i + int(0.9 / JW_HOP)]
        return float((seg >= float(seg.max()) - 6.0).sum()) * hop if len(seg) else 0.0
    att = sorted([a for a in E['attacks'] if a[0] >= body_from + 0.3], key=lambda a: a[0])
    loud = [a for a in att if peak(a[0]) >= -15.0]            # any sung attack (a new word keeps the line going)
    tails = []
    for a in [a_ for a_ in loud if a_[2]]:
        t = a[0]
        if any(z[0] < t < z[1] for z in tails):
            continue
        nx = [b for b in att if b[0] > t]
        t_next = nx[0][4] if nx else float(dur)
        i0, i1 = int(round(t / hop)), int(round(t_next / hop))
        seg = lv[i0:max(i0 + 1, i1)]
        sus = t + (int(np.where(seg >= float(seg.max()) - 6.0)[0][-1]) * hop if len(seg) else 0.0)
        if any(sus < b[4] <= sus + TO_LINE_END for b in loud):
            continue
        j0, j1 = int((t + 0.1) / hop_c), max(int((t + 0.1) / hop_c) + 2, int(sus / hop_c))
        if j1 >= Cc.shape[1]:
            continue
        ref = Cc[:, j0:j1].mean(axis=1)
        ref /= np.linalg.norm(ref) + 1e-9
        from scipy.ndimage import uniform_filter1d
        sim = uniform_filter1d(ref @ Cc, 5)
        end, j, run = float(dur), int(sus / hop_c), 0
        while j < Cc.shape[1]:
            run = run + 1 if sim[j] < TO_SIM else 0
            if run * hop_c >= TO_BREAK:
                end = (j - run + 1) * hop_c
                break
            j += 1
        for b in loud:
            if sus < b[0] < end and carry(b[0]) >= 0.7:
                end = b[4]
                break
        if end - sus >= 0.6:
            tails.append((round(t, 3), round(end, 3)))
    # outro: the band drops out for the rest of the edit
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=1024))
    f = librosa.fft_frequencies(sr=sr, n_fft=2048)
    lo = 20 * np.log10(np.sqrt((S[(f >= 40) & (f <= 160)] ** 2).mean(axis=0)) + 1e-6)
    hh = 1024 / sr
    outro = None
    for i in range(int(max(1.5, float(dur) - 12.0) / hh), int((float(dur) - 2.0) / hh)):
        before = float(np.mean(lo[max(0, i - int(1.5 / hh)):i]))
        after = lo[i:max(i + 1, len(lo) - int(0.5 / hh))]
        if before - float(np.percentile(after, 90)) >= TO_DROP:
            outro = round(i * hh, 3)
            break
    if outro is not None:                  # the outro starts a new section: a tail stops there
        tails = [(a_, min(e_, outro)) for a_, e_ in tails if a_ < outro]
    bad = []
    for c in cuts:
        z = [tz for tz in tails if tz[0] + 0.3 < c < tz[1] - 0.15]
        if z:
            bad.append((round(c, 3), f'inside the held note from {z[0][0]:.2f} s (its decay rings to {z[0][1]:.2f} s)'))
        elif outro is not None and c > outro + 0.5:
            bad.append((round(c, 3), f'inside the outro (from {outro:.2f} s: one clip to the end)'))
    return dict(tails=tails, outro=outro, bad=bad)


def sync_check(E, cuts, dur, body_from, end_guard=1.0):
    """12 A/V sync (owner 2026-10-20, 3h: "the switch at 'you' comes 1 s late, at 'time' a bit early"): the RENDERED
    file's cuts against the strong wave starts of the RENDERED file's own audio. Each strong wave start in the body (it
    carries, peak >= -15 dB) and the nearest switch within SYNC_NEAR: the error = how far that switch sits outside the
    start's hit window [the fall - 0.15, the climb + 0.10] (+ late, - early, 0 on it). A start with no switch near is
    criterion 11's 'missed'. Fails on a systematic error: |error| median or mean > SYNC_TOL, or two off-sync starts in a
    row (a section that drifts). -> dict(rows, n, median_abs, mean_abs, off, worst, run, ok)"""
    hop = E['F'].get('hop', JW_HOP)
    tsc = float(E.get('tscale') or 1.0)
    rows = []
    for t, rr, strong, why, t0 in E['attacks']:
        if not strong or t < body_from + 0.3 or t > dur - end_guard:
            continue
        i = int(round(t / hop))
        pk = float(E['F']['lv'][i:i + int(0.4 / JW_HOP)].max())
        if pk < -15.0:
            continue
        lo, hi = t0 - JW_HIT[1], t - JW_HIT[0]

        def _e(c_):
            return 0.0 if lo <= c_ <= hi else (c_ - hi if c_ > hi else c_ - lo)
        if any((abs(a[0] - t) <= JW_JOIN * tsc or t < a[4] <= t + JW_JOIN * tsc)
               and any(a[4] - JW_HIT[1] <= c_ <= a[0] - JW_HIT[0] for c_ in cuts)
               for a in E['attacks'] if a[0] != t):
            continue                       # (a breath / consonant / pick-up of a start whose switch is on: one wave start)
        # 2026-10-21 (CI dry run "blue" @v0.9): the same excuses as criterion 11 - an attack closer than JW_MIN_SHOT after
        # a switch that sits ON another strong start belongs to that shot (12.96 "is" 0.55 s after "sky", switched)
        # groove v2: a shot that starts ON a strong start carries the phrase's next words (up to JG_MISS_SHOT)
        carry = JG_MISS_SHOT if (E.get('groove') or {}).get('groove') else JW_MIN_SHOT * tsc
        prev_on = [c_ for c_ in cuts if 0.0 <= t - c_ < carry
                   and any(a[2] and a[0] != t and a[4] - JW_HIT[1] <= c_ <= a[0] - JW_HIT[0] for a in E['attacks'])]
        if prev_on:
            continue
        near = [c for c in cuts if abs(c - t) <= SYNC_NEAR]
        if not near:
            continue                       # (no switch meant for it: criterion 11 'missed' when it carries)
        c = min(near, key=lambda c_: abs(_e(c_)))
        word = next((w[2] for w in E['words'] if abs(w[0] - t) <= 0.35), '')
        rows.append(dict(start=round(t, 3), fall=round(t0, 3), cut=round(c, 3), err=round(_e(c), 3), word=_mask(word), why=why))
    errs = np.array([abs(r_['err']) for r_ in rows]) if rows else np.zeros(0)
    med = float(np.median(errs)) if len(errs) else 0.0
    mean = float(np.mean(errs)) if len(errs) else 0.0
    off = [r_['start'] for r_ in rows if abs(r_['err']) > SYNC_TOL]
    run_, best = 0, 0
    for r_ in rows:
        run_ = run_ + 1 if abs(r_['err']) > SYNC_TOL else 0
        best = max(best, run_)
    ok = med <= SYNC_TOL and mean <= SYNC_TOL and best < 2
    return dict(rows=rows, n=len(rows), median_abs=round(med, 3), mean_abs=round(mean, 3), off=off,
                worst=max(rows, key=lambda r_: abs(r_['err'])) if rows else None, run=best, ok=bool(ok))


def _mask(w):
    """owner rule: no lyrics in files / logs - a heard word is reported as its first letter + length ('w4')"""
    w = str(w or '').strip()
    return f"{w[0]}{len(w)}" if w else ''


def wave_table_md(WJ, label):
    L = [f'# {label}: cuts vs the sung line (independent wave judge)', '',
         '| cut (s) | sung there | what happens | gap to nearest wave start (s) | verdict |', '|---|---|---|---|---|']
    for r in WJ['rows']:
        L.append(f"| {r['cut']:.2f} | {r['word']} | {r['what']} | {r['gap_to_wave_start']} | {'ok' if r['ok'] else 'BAD'} |")
    L += ['', f"**MISSED wave starts ({len(WJ['missed'])})**", '']
    L += [f"- {m['t']:.2f} s ({m['word'] or '?'}; +{m['rise_db']} dB {m['why']}; previous cut {m['prev_cut']:.2f})" for m in WJ['missed']] or ['- none']
    L += ['', f"Bad cuts: {WJ['bad'] or 'none'}"]
    TO = WJ.get('tail_outro')
    if TO:
        L += ['', f"## Held notes + outro (13): tails {TO['tails']}, outro from {TO['outro']} s", '']
        L += [f"- cut {c:.2f}: {w} **BAD**" for c, w in TO['bad']] or ['- no cut inside a held note or the outro']
    SY = WJ.get('sync')
    if SY and SY.get('av'):
        L += ['', '## Render vs plan (12): audio lag vs the song version per window (t s: lag ms, corr), cuts plan -> found (err s)', '',
              ', '.join(f"{t:.0f}: {l:+.0f} ({c})" for t, l, c in SY['av']['lags_ms']), '',
              ', '.join(f"{a:.2f}->{b:.2f} ({e:+.3f})" for a, b, e in SY['av']['cut_err'])]
    if SY and SY.get('lines'):
        L += ['', '## Lyric line starts (12): every new sung line is a wave start that must get its switch', '',
              '| line | LRC start (s) | first attack (s) | switch (s) | error (s) |', '|---|---|---|---|---|']
        L += [f"| {r_['line']} | {r_['t']:.2f} | {r_['attack']} | {r_['cut']} | {r_['err']}"
              f"{' **OFF**' if r_['t'] in SY['lines']['off'] else ''} |" for r_ in SY['lines']['rows']]
    GC = WJ.get('groove')
    if GC and GC.get('groove'):
        L += ['', f"## Groove (14): pulse {GC['pulse']}, beat {GC.get('period')} s - every body switch on a main beat or tight "
                  f"on the sung attack ({JG_TIGHT[0]:+.2f}..{JG_TIGHT[1]:+.2f} s before it); loose {len(GC['loose'])} "
                  f"({GC['share']:.0%}, max {JG_LOOSE_MAX:.0%})", '',
              '| cut (s) | kind | nearest sung attack - cut (s) |', '|---|---|---|']
        L += [f"| {r_['cut']:.2f} | {r_['kind']}{' **LOOSE**' if r_['kind'] == 'loose' else ''} | {r_['attack_after']} |"
              for r_ in GC['rows']]
    elif GC is not None:
        L += ['', f"## Groove (14): no steady pulse (pulse {GC.get('pulse')}): the ballad rule only"]
    if SY:
        L += ['', f"## A/V sync (12):{'OK' if SY['ok'] else 'FAIL'} - |error| median {SY['median_abs']} s, mean {SY['mean_abs']} s, "
                  f"off-sync starts {len(SY['off'])}, longest run {SY['run']} (tol {SYNC_TOL} s)", '',
              '| strong wave start (s) | fall (s) | switch (s) | error (s, + late / - early) | word |', '|---|---|---|---|---|']
        L += [f"| {r_['start']:.2f} | {r_['fall']:.2f} | {r_['cut']:.2f} | {r_['err']:+.2f}{' **OFF**' if abs(r_['err']) > SYNC_TOL else ''} | {r_['word']} |"
              for r_ in SY['rows']]
    return '\n'.join(L) + '\n'


def wave_plot(WJ, cuts, out, title):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    E = WJ['E']
    F = E['F']
    D = E['dur']
    t = np.arange(F['n']) * F.get('hop', JW_HOP)
    fig, ax = plt.subplots(2, 1, figsize=(max(16, D * 0.75), 6.5), sharex=True)
    ax[0].plot(t, F['lv'], color='#b22', lw=0.8)
    ax[0].set_ylim(-45, 3)
    ax[0].axhline(JW_ACT, color='#aaa', lw=0.6, ls=':')
    ax[0].set_ylabel('vocal band dB')
    ax[1].plot(t, F['midi'], '.', ms=1.8, color='#235')
    ax[1].set_ylabel('vocal pitch (YIN)')
    yl = ax[1].get_ylim()
    for a, r, st, why, a0 in E['attacks']:
        for x in ax:
            x.axvspan(a0, a, color='#7a2fbf' if st else '#bba3d9', alpha=0.35 if st else 0.25, lw=0)
            x.axvline(a, color='#7a2fbf' if st else '#bba3d9', lw=1.0 if st else 0.6, ls='-' if st else ':')
    for r in E['releases']:
        ax[0].axvline(r, color='#e08000', lw=1.2, ls='--')
    for w0, w1, w in E['words']:
        ax[1].text(w0, yl[1] - 1, _mask(w), fontsize=8, rotation=60, va='top')
    for r in WJ['rows']:
        col = '#090' if r['ok'] else '#e00'
        for x in ax:
            x.axvline(r['cut'], color=col, lw=2.0)
        ax[0].text(r['cut'], 1.5, f"{r['cut']:.2f}", fontsize=7, color=col, rotation=90, va='top')
    for m in WJ['missed']:
        for x in ax:
            x.axvspan(m['t'] - 0.08, m['t'] + 0.08, color='#ff3', alpha=0.8)
        ax[0].text(m['t'], -44, 'MISSED', fontsize=8, color='#a60', rotation=90, va='bottom')
    ax[1].set_xticks(np.arange(0, D + 0.01, 1.0))
    ax[1].set_xticks(np.arange(0, D + 0.01, 0.25), minor=True)
    for x in ax:
        x.grid(axis='x', which='major', alpha=0.35)
    ax[1].set_xlim(0, D)
    ax[0].set_title(f"{title}: green = switch on a wave start / release / gap, red = mid-wave, yellow = MISSED wave start, "
                    f"purple = attacks (solid strong), orange = release")
    fig.tight_layout()
    fig.savefig(out, dpi=72)
    plt.close(fig)


# ------------------------------------------------------------------------------------------------ evidence sheets
def _font(sz):
    for f in ('arial.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(f, sz)
        except OSError:
            pass
    return ImageFont.load_default()


def grab(path, ts, w=180):
    """frames at times ts (s) as PIL images (one ffmpeg call per frame, accurate seek)"""
    out = []
    for t in ts:
        r = subprocess.run(['ffmpeg', '-v', 'error', '-threads', THREADS, '-ss', f'{max(0.0, t):.3f}', '-i', path, '-frames:v', '1',
                            '-vf', f'scale={w}:-2', '-f', 'image2pipe', '-vcodec', 'png', '-'], capture_output=True)
        try:
            out.append(Image.open(io.BytesIO(r.stdout)).convert('RGB'))
        except Exception:  # noqa: BLE001
            out.append(Image.new('RGB', (w, int(w * 16 / 9)), (40, 0, 0)))
    return out


def sheet(path, rows, out, title, w=180):
    """rows = [(label, [times])]: one row of frames per entry"""
    if not rows:
        return
    fr = [grab(path, ts, w) for _, ts in rows]
    h = fr[0][0].height
    cols = max(len(x) for x in fr)
    S = Image.new('RGB', (cols * (w + 4) + 4, len(rows) * (h + 22) + 30), (18, 18, 18))
    d = ImageDraw.Draw(S)
    d.text((6, 6), title, fill=(240, 240, 240), font=_font(15))
    for i, ((lab, ts), ims) in enumerate(zip(rows, fr)):
        y = 30 + i * (h + 22)
        d.text((6, y), lab, fill=(255, 220, 120), font=_font(13))
        for j, (t, im) in enumerate(zip(ts, ims)):
            S.paste(im, (4 + j * (w + 4), y + 18))
            d.text((8 + j * (w + 4), y + 20), f'{t:.2f}', fill=(255, 255, 255), font=_font(12))
    S.save(out, quality=88)


def evidence(C, out_dir, label):
    path = C['path']
    fps = C['fps']
    shots = C['picture']['shots']
    for k in range(0, len(shots), 6):
        rows = [(f"shot @{s['start']:.2f} ({s['dur']:.2f} s) sharp {s['sharp']:.0f} sat {s['sat']:.2f} p2 {s['p2']:.2f}",
                 [s['start'] + s['dur'] * f for f in (0.08, 0.3, 0.5, 0.7, 0.92)]) for s in shots[k:k + 6]]
        sheet(path, rows, os.path.join(out_dir, f'shots-{k // 6:02d}.jpg'), f'{label}: picture shots')
    if C['words']:
        rows = [(f"word @{w['t']:.2f} legible {w['t_legible']:.2f} on {w['on_s']:.2f} s h {w['letter_h']:.3f}",
                 [w['t'] + i / fps for i in (0, 1, 2, 3, 6)]) for w in C['words']]
        sheet(path, rows, os.path.join(out_dir, 'words.jpg'), f'{label}: words (entry frame + next frames)')
    sheet(path, [('rotate hint', [0.3, 1.5, 3.0, 4.6, 6.0, C['dur'] * 0.55 + 1.0])], os.path.join(out_dir, 'hint.jpg'),
          f'{label}: rotate-phone hint')
    if _LAST_H.get(path) is not None:
        import music_moments as MM
        MM.plot(_LAST_H[path], C['cuts'], os.path.join(out_dir, 'cuts-holds.png'), f'{label}')
    if _LAST_H.get('_W:' + path) is not None:
        WJ = _LAST_H['_W:' + path]
        wave_plot(WJ, C['cuts'], os.path.join(out_dir, 'waves.png'), label)
        with open(os.path.join(out_dir, 'waves.md'), 'w', encoding='utf-8') as f:
            f.write(wave_table_md(WJ, label))
        # 20-frame sheets around every cut (10 frames before, 10 after)
        fps = C['fps']
        for k in range(0, len(C['cuts']), 4):
            rows = []
            for c in C['cuts'][k:k + 4]:
                rows += [(f'cut {c:.2f}: 10 frames before', [c + (j - 10) / fps for j in range(10)]),
                         ('10 frames after', [c + j / fps for j in range(10)])]
            sheet(path, rows, os.path.join(out_dir, f'cutframes-{k // 4:02d}.jpg'), f'{label}: around each cut', w=120)
    for i, (_, st, a, b) in enumerate(C['smooth']['worst'][:2]):
        m = (a + b) // 2
        sheet(path, [(f'least smooth shot @{st:.2f}: 10 consecutive frames', [(m + j) / fps for j in range(0, 5)]),
                     ('', [(m + j) / fps for j in range(5, 10)])], os.path.join(out_dir, f'motion-{i:02d}.jpg'),
              f'{label}: motion at full fps')


# ------------------------------------------------------------------------------------------------ main
# 3_lyrics per word (2026-10-23, owner on blue v2: "the lyrics is still a bit off" while 3_lyrics scored 8.73): the old
# timing check took the NEAREST vocal onset of any word (-0.15..+0.25 s), so a word shown on the next note of a melisma
# passed. This check knows WHICH word: the synced LRC words around the intro, the start word from whisper medium on the
# rendered vocals, both CTC models on the exact words + the stem's word-start events (lyric_check.intro_ensemble's DP),
# and the frame each new word appears (a jump of the word band). Words are never stored or printed: indices only.
LW_LEAD = 0.03            # _sX2: first ink 0.00-0.06 s before the sung onset (median 0.035)
LW_TOL = 0.08             # |ink - (onset - LW_LEAD)| within this = on time
LW_WRONG_NOTE = 0.25      # farther than this = the word sits on another note (NOT PASS)


def word_swaps(path, t1, thr=6.0):
    """(times of the frames where a new word lands on the black intro, word-band ink end) from a light decode"""
    W_, H_ = 270, 480
    r = subprocess.run(['ffmpeg', '-v', 'error', '-t', f'{float(t1):.3f}', '-i', path, '-vf', f'scale={W_}:{H_}', '-f', 'rawvideo',
                        '-pix_fmt', 'rgb24', '-'], capture_output=True, check=True).stdout
    _, _, fps, _ = ffprobe(path)
    F = np.frombuffer(r, np.uint8).reshape(-1, H_, W_, 3)
    band = F[:, int(0.40 * H_):int(0.62 * H_)].max(axis=3).astype(np.float32)
    ink = (band > 110)
    wide = np.array([(lambda xs: (xs[-1] - xs[0]) / W_ if len(xs) else 0.0)(np.where(m.any(0))[0]) for m in ink])
    full = ink.mean(axis=(1, 2)) > 0.6                         # the picture is in (the body)
    sw = []
    for i in range(1, len(band)):
        if full[i]:
            break
        d = float(np.abs(band[i] - band[i - 1]).mean())
        inter = float((ink[i] & ink[i - 1]).sum())
        uni = float((ink[i] | ink[i - 1]).sum()) or 1.0
        if d >= thr and ink[i].any() and (ink[i].mean() > 1.3 * ink[i - 1].mean() or inter / uni < 0.5) and (not sw or i / fps - sw[-1] > 0.12):
            sw.append(i / fps)
    last = max([i / fps for i in range(len(band)) if wide[i] >= 0.2 and not full[i]] or [0.0])
    return sw, last


def lyric_word_timing(path, intro_end, lrc_id, m_start, speed, entries=None):
    """-> dict(rows=[{entry, ink, word, onset, err}], dropped=[word idx], ...) or None (no synced lyrics / no words)"""
    import difflib
    import re
    import lyrics as LY
    import lyric_check as LC
    c = LY._get(f'get/{int(lrc_id)}', {})
    rec = LY._record(c, 'lrclib:id') if c else None
    if not rec or not rec.get('synced'):
        return None
    sp = float(speed or 1.0)
    L = [(float(x['t']) / sp - float(m_start), str(x.get('text') or '')) for x in rec['lines'] if x.get('t') is not None]
    L = [x for x in L if any(ch.isalnum() for ch in x[1])]
    first = max([k for k, x in enumerate(L) if x[0] <= 0.0] or [0])
    lines = [x for k, x in enumerate(L) if k >= first and x[0] < intro_end]
    words = [re.sub(r"[^A-Z']", '', LY.romanise(w)) for _, tx in lines for w in tx.split()]
    words = [w for w in words if w]
    if not words:
        return None
    t1 = float(intro_end) + 0.3
    v16 = LY.vocal_stem(path, 0.0, t1, threads=2)
    heard = _jw_words(v16)
    hw = [(a_, re.sub(r"[^A-Z']", '', LY.romanise(w_))) for a_, _, w_ in heard if a_ < intro_end]
    hw = [x for x in hw if x[1]]
    sm = difflib.SequenceMatcher(None, [x[1] for x in hw], words, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    if not blocks:
        return dict(error='the recogniser heard none of the lyric words of the intro')
    s = max(0, blocks[0].b - blocks[0].a)
    e = blocks[-1].b + blocks[-1].size              # one past the last heard lyric word
    ws_idx = list(range(s, max(e, s + 1)))
    cands = [[] for _ in ws_idx]
    for b in blocks:
        for q in range(b.size):
            j = b.b + q - s
            if 0 <= j < len(cands):
                cands[j].append(('weak', float(hw[b.a + q][0])))
    log = {}
    LC.ctc_candidates(v16, [words[j] for j in ws_idx], cands, log)
    if os.environ.get("JUDGE_LW_DEBUG"):
        print("[lw] shape", [(len(words[j]), words[j][:1] in "AEIOU", words[j][-1:] in "AEIOUY") for j in ws_idx], file=sys.stderr)
    if any(not c_ for c_ in cands):
        return dict(error='no candidate time for some word', log=log)
    ev = LC._events(v16, LY.SR)
    if os.environ.get("JUDGE_LW_DEBUG"):
        print("[lw] cands", [[x if not isinstance(x, tuple) else ("a", round(x[1], 2)) for x in c_] for c_ in cands], "events", [(round(t, 2), round(x, 2)) for t, x in ev], file=sys.stderr)
    pick = LC.assign(cands, [t for t, _ in ev], [x for _, x in ev], gaps=list(LC._GAPS)) if ev else None
    if pick is None:
        return dict(error='no monotone word assignment', log=log)
    on = [float(p) for p in pick]
    swaps, ink_end = word_swaps(path, intro_end + 0.05)
    src = 'frames'
    if entries:
        # the plan's item starts, on the frame the renderer first draws them (verified on blue v2: 0.382 -> 0.400,
        # 0.865 -> 0.867, 3.26 -> 3.267 = the frame jumps); the frame detector is the fallback without a plan
        fps_ = ffprobe(path)[2]
        swaps, src = [math.ceil(float(x) * fps_ - 1e-6) / fps_ for x in entries], 'plan'
    # entries -> words: monotone, cost |ink - (onset - lead)|; a skipped word right after the previous entry's word
    # (<= 0.35 s) rides in that item (a merged pair), otherwise it is a dropped word
    n, m = len(swaps), len(on)
    INF = 1e9
    D = np.full((n, m), INF)
    P = np.full((n, m), -1, int)

    def drop_cost(a_, b_):                      # words a_..b_-1 skipped
        c_ = 0.0
        for j in range(a_, b_):
            prev_on = on[a_ - 1] if a_ > 0 else -9.0
            if on[j] < 0.15 or on[j] > intro_end - 0.45 or on[j] - prev_on <= 0.35:
                continue
            c_ += 1.0
        return c_
    for j in range(m):
        D[0, j] = abs(swaps[0] - (on[j] - LW_LEAD)) + drop_cost(0, j) if n else INF
    for k in range(1, n):
        for j in range(k, m):
            best, bj = INF, -1
            for i in range(k - 1, j):
                x = D[k - 1, i] + drop_cost(i + 1, j)
                if x < best:
                    best, bj = x, i
            D[k, j] = best + abs(swaps[k] - (on[j] - LW_LEAD))
            P[k, j] = bj
    if not n:
        return dict(error='no words detected on screen', log=log)
    j = int(np.argmin([D[-1, x] + drop_cost(x + 1, m) for x in range(m)]))
    asg = [0] * n
    for k in range(n - 1, -1, -1):
        asg[k] = j
        j = int(P[k, j])
    rows, used = [], set(asg)
    for k, j in enumerate(asg):
        err = swaps[k] - on[j]
        rows.append(dict(entry=k, ink=round(swaps[k], 3), word=j, onset=round(on[j], 3), err=round(err, 3),
                         ok=abs(err + LW_LEAD) <= LW_TOL, wrong_note=abs(err + LW_LEAD) > LW_WRONG_NOTE))
    dropped = []
    for j in range(m):
        if j in used:
            continue
        prev = max([u for u in used if u < j] or [-1])
        if on[j] < 0.15 or on[j] > intro_end - 0.45 or (prev >= 0 and on[j] - on[prev] <= 0.35):
            continue
        dropped.append(j)
    errs = [r_['err'] for r_ in rows]
    return dict(rows=rows, dropped=dropped, n_words=m, onsets=[round(x, 3) for x in on], last_ink=round(ink_end, 3), entries_from=src,
                last_word_end_gap=None, mean=round(float(np.mean(errs)), 3), max_abs=round(float(np.max(np.abs(errs))), 3),
                n_off=int(sum(not r_['ok'] for r_ in rows)), log=log)


def measure(path, plan=None, vocals=True):
    v = scan(path)
    cuts, _ = detect_cuts(v)
    segs = segments(v, cuts)
    sm = smooth_metrics(v, segs)
    pic = picture_metrics(v, segs, sm['fade_from_s'])
    words = word_events(v)
    intro_end = next((s['start'] for s in segs if not s['text']), 0.0)
    C = dict(path=path, W=v['W'], H=v['H'], fps=v['fps'], dur=v['dur'], n=v['n'], cuts=[round(c / v['fps'], 3) for c in cuts],
             n_shots=len([s for s in segs if not s['text']]), intro_end=intro_end, smooth=sm, picture=pic, words=words)
    C['audio'] = audio_metrics(path, v['dur'], intro_end if words else 0.0)
    C['shake'] = shake_metric(v, segs)
    t = np.arange(v['n']) / v['fps']
    hint = v['hint']
    on_w = (t >= 0.4) & (t <= 4.5) & ~np.isnan(hint)
    aft = (t >= 6.0) & (t <= v['dur'] * 0.5) & ~np.isnan(hint)
    wm = v['wm']
    dark_rows = (v['small'].mean(axis=2) < 12).mean(axis=0) > 0.9
    C['layout'] = dict(hint_on=round(float((hint[on_w] > HINT_THR).mean()), 3) if on_w.any() else 0.0,
                       hint_after=round(float((hint[aft] > HINT_THR).mean()), 3) if aft.any() else 0.0,
                       hint_score_p50=[round(float(np.median(hint[on_w])), 1) if on_w.any() else None,
                                       round(float(np.median(hint[aft])), 1) if aft.any() else None],
                       wm_on=round(float((wm[~np.isnan(wm)] > WM_THR).mean()), 3) if (~np.isnan(wm)).any() else 0.0,
                       wm_score_p50=round(float(np.nanmedian(wm)), 2) if (~np.isnan(wm)).any() else None,
                       bar_rows_pct=round(float(dark_rows.mean() * 100), 1))
    try:
        C['cut_align'] = cut_alignment(path, C['cuts'], v['dur'])
    except Exception as e:  # noqa: BLE001
        print('[judge_slowmo] cut alignment failed:', repr(e)[:200], file=sys.stderr)
    try:
        C['holds'] = hold_alignment(path, C['cuts'], v['dur'], _LAST_H.get('_M:' + path))
    except Exception as e:  # noqa: BLE001
        print('[judge_slowmo] hold alignment failed:', repr(e)[:200], file=sys.stderr)
    try:
        WJ = wave_judge(path, C['cuts'], v['dur'], speed=(plan or {}).get('speed'))
        _LAST_H['_W:' + path] = WJ
        C['sync'] = WJ['sync']
        C['tail_outro'] = WJ.get('tail_outro')
        if plan and plan.get('music') and os.path.exists(str(plan['music'])) and plan.get('music_start') is not None:
            pcs = [float(x) for x in plan.get('cut_times') or []]
            C['sync']['av'] = av_drift(path, plan['music'], float(plan['music_start']), v['dur'], pcs, C['cuts'])
        if plan and plan.get('lrc_id') and plan.get('music_start') is not None and C['cuts']:
            C['sync']['lines'] = line_sync(WJ['E'], C['cuts'], v['dur'], plan['lrc_id'], float(plan['music_start']),
                                           plan.get('speed') or 1.0, C['cuts'][0])
        C['waves'] = dict(rows=WJ['rows'], missed=WJ['missed'], bad=WJ['bad'], tscale=WJ['E'].get('tscale'),
                          attacks=[list(map(float, a[:2])) + [bool(a[2]), a[3], float(a[4])] for a in WJ['E']['attacks']],
                          releases=WJ['E']['releases'], words=[(a_, b_, _mask(w_)) for a_, b_, w_ in WJ['E']['words']])
        C['groove'] = WJ.get('groove')
    except Exception as e:  # noqa: BLE001
        print('[judge_slowmo] wave judge failed:', repr(e)[:200], file=sys.stderr)
    if plan and plan.get('music') and os.path.exists(str(plan['music'])) and plan.get('music_start') is not None:
        try:
            C['sfx'] = sfx_residual(path, plan['music'], float(plan['music_start']), v['dur'])
        except Exception as e:  # noqa: BLE001
            print('[judge_slowmo] sfx residual failed:', repr(e)[:200], file=sys.stderr)
    if vocals and words:
        try:
            C['vocal_onsets'], _ = vocal_onsets(path, min(v['dur'], max(w['t'] for w in words) + 1.5))
        except Exception as e:  # noqa: BLE001
            print('[judge_slowmo] vocal onsets failed:', repr(e)[:200], file=sys.stderr)
    if vocals and words and plan and plan.get('lrc_id') and plan.get('music_start') is not None:
        try:
            C['lyric_words'] = lyric_word_timing(path, intro_end or (max(w['t'] for w in words) + 1.0), plan['lrc_id'],
                                                 float(plan['music_start']), plan.get('speed') or 1.0,
                                                 entries=plan.get('item_t0'))
        except Exception as e:  # noqa: BLE001
            print('[judge_slowmo] lyric word timing failed:', repr(e)[:200], file=sys.stderr)
    for w in C['words']:
        w.pop('frame', None)
    return C


def ref_measures(ref):
    if os.path.exists(REF_CACHE) and (not ref or not os.path.exists(ref)):
        with open(REF_CACHE, encoding='utf-8') as f:
            return json.load(f)
    if os.path.exists(REF_CACHE):
        with open(REF_CACHE, encoding='utf-8') as f:
            R = json.load(f)
        if R.get('_src') == os.path.basename(ref):
            if 'cut_rule' not in R:
                # 2026-10-19: the reference's cuts against its own song (music_moments), the rule criterion 8 copies
                ca = cut_alignment(ref, R['cuts'], R['dur'])
                R['cut_rule'] = dict(ca['stats'], rows=ca['rows'])
                with open(REF_CACHE, 'w', encoding='utf-8') as f:
                    json.dump(R, f, indent=1, default=float)
            if 'hold_rule' not in R or os.environ.get('JUDGE_REF_REFRESH'):
                # 2026-10-19 round 2: the reference's cuts against the held notes of its own song (criterion 10)
                ha = hold_alignment(ref, R['cuts'], R['dur'])
                R['hold_rule'] = dict(in_hold=ha['in_hold'], offsets=ha['offsets'], null=ha['null'], rows=ha['rows'])
                with open(REF_CACHE, 'w', encoding='utf-8') as f:
                    json.dump(R, f, indent=1, default=float)
            return R
    R = measure(ref, None, vocals=False)
    R['_src'] = os.path.basename(ref)
    R['picture']['shots'] = R['picture']['shots']
    with open(REF_CACHE, 'w', encoding='utf-8') as f:
        json.dump(R, f, indent=1, default=float)
    return R


def write_md(path, rep):
    L = [f"# Slow-mo judge: {rep['label']}", '', f"candidate: {rep['candidate']}", f"reference: {rep['reference']}", '',
         f"**overall {rep['overall']} -> {'PASS' if rep['pass'] else 'NOT PASS'}** (bar: every criterion >= {PASS_EACH}, overall >= {PASS_ALL})", '',
         '| criterion | score | evidence |', '|---|---|---|']
    for k, s in rep['scores'].items():
        L.append(f"| {k} | {s} | {rep['evidence'][k]} |")
    L += ['', 'By eye before delivery: shots-NN.jpg (beauty / colour / subject), words.jpg (legibility), hint.jpg, motion-NN.jpg '
          '(full-fps smoothness).']
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')


def run(a):
    os.makedirs(a.out, exist_ok=True)
    R = ref_measures(a.ref)
    P = plan_info(a.plan)
    items = pool_lookup([s['path'] for s in P['shots']]) if P else None
    C = measure(a.candidate, P, vocals=not a.no_vocals)
    S, E, overall, ok = scorecard(C, R, P, items)
    rep = dict(label=a.label or os.path.basename(a.candidate), candidate=a.candidate, reference=R.get('_src'), scores=S,
               evidence=E, overall=overall, **{'pass': ok}, cand=C, plan=dict(P or {}, shots=len((P or {}).get('shots') or [])))
    with open(os.path.join(a.out, 'report.json'), 'w', encoding='utf-8') as f:
        json.dump(rep, f, indent=1, default=lambda x: x.tolist() if hasattr(x, 'tolist') else float(x))
    write_md(os.path.join(a.out, 'report.md'), rep)
    if not a.no_sheets:
        evidence(C, a.out, rep['label'])
    print(json.dumps(dict(scores=S, overall=overall, **{'pass': ok})))
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('candidate')
    ap.add_argument('out')
    ap.add_argument('--plan')
    ap.add_argument('--label')
    ap.add_argument('--ref', default=REF_DEFAULT)
    ap.add_argument('--no-vocals', action='store_true')
    ap.add_argument('--no-sheets', action='store_true')
    run(ap.parse_args())


if __name__ == '__main__':
    main()
