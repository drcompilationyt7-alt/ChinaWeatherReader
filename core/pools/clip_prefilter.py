#!/usr/bin/env python3
"""Cheap CPU PRE-FILTERS for the nightly clip miners (2026-10-21), run on the runner BEFORE staging so the by-eye review sees
mostly good clips. Decoding is ffmpeg -> numpy (no cv2 needed: the owner PC python has none). Thresholds were calibrated on the
by-eye labels of 2026-10-19..21 (every approved clip must pass; see new-types-progress.md, "Pre-filters").

  slow-mo  slowmo_check(clip, item)   -> (ok, reasons, feats)
           - stutter: duplicate / held frames (twixtor packs frame-doubled to 60 fps, OP / ED held drawings), from the
             per-frame luma differences at the clip's own frame rate
           - talking: the library's mouth / talk signals (portrait sidecar + manifest row)
           - face: visible in most frames (portrait sidecar)
           - colour: garish (a big strongly-red share, blown-out highlights)
  sound    sound_check(mini, hit_t)   -> (ok, reasons, feats)
           - the visual impact (motion-energy peak) within +-2 frames of the transient (not a marker after the contact)
           - the action is big enough (moving area), near the centre and compact at the hit (not a wide chaotic shot)
           - a scene cut in the 0.5 s before the hit is measured and reported (gate off: approved quick cuts have one)

    python core/pools/clip_prefilter.py slowmo CLIP.mp4 [--item ROW.json]
    python core/pools/clip_prefilter.py sound MINI.mp4 --hit-t 1.2
    python core/pools/clip_prefilter.py selftest
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np

W, H = 160, 90

# ---- calibrated thresholds (2026-10-21; env overrides for experiments) ----
def _f(k, d):
    return float(os.environ.get(k, d))


SM = {
    'stutter': _f('PF_SM_STUTTER', 0.22),          # repeated-frame share + jump-frame share (approved max 0.217)
    'talk_mouth': _f('PF_SM_TALK_MOUTH', 0.5),     # library rules.talk_mouth (approved max 0.494)
    'mouth_flap': _f('PF_SM_MOUTH_FLAP', 0.1),     # (approved max 0.048)
    'talk_sig': _f('PF_SM_TALK_SIG', 0.55),        # portrait sidecar SigLIP 'talk' (approved max 0.529)
    'face_frac': _f('PF_SM_FACE_FRAC', 0.75),      # share of sampled frames with a face (approved min 0.8)
    'red': _f('PF_SM_RED', 0.18),                  # share of strongly red pixels: garish (approved max 0.172)
    'blown': _f('PF_SM_BLOWN', 0.13),              # share of blown-out pixels (approved max 0.126)
}
SN = {
    # calibrated 2026-10-21 on 55 approved / 112 rejected minis (hit-calib + the 10-21 batch): every approved one passes
    'peak_frames': _f('PF_SN_PEAK_FRAMES', 2),     # motion peak within +- this many frames of the hit ...
    'peak_rel': _f('PF_SN_PEAK_REL', 0.20),        # ... the window max must reach this share of the local max (approved min 0.21)
    'area_min': _f('PF_SN_AREA_MIN', 0.10),        # moving-area share at the hit (approved min 0.106)
    'spread_max': _f('PF_SN_SPREAD_MAX', 0.42),    # spatial spread of the motion at the hit: wide chaotic shot (approved max 0.41)
    'centre_max': _f('PF_SN_CENTRE_MAX', 0.30),    # motion centroid distance from the centre (approved max 0.291)
    'cut_pre': _f('PF_SN_CUT_PRE', 0.5),           # scene cut in this many seconds before the hit: REPORTED only (7 / 55
                                                   # approved minis have one: the quick cut into the action is fine)
    'cut_gate': os.environ.get('PF_SN_CUT_GATE', '0') == '1',
}


def probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=avg_frame_rate,r_frame_rate',
                        '-show_entries', 'format=duration', '-of', 'json', path], capture_output=True, text=True)
    d = json.loads(r.stdout or '{}')
    s = (d.get('streams') or [{}])[0]
    fps = 0.0
    for k in ('avg_frame_rate', 'r_frame_rate'):
        try:
            a, b = str(s.get(k) or '0/1').split('/')
            fps = float(a) / float(b or 1)
        except (ValueError, ZeroDivisionError):
            fps = 0.0
        if fps > 0:
            break
    return fps or 30.0, float((d.get('format') or {}).get('duration') or 0)


def frames(path, t0=None, t1=None, w=W, h=H):
    """grey frames (N, h, w) float32 at the clip's own frame rate (no resampling: duplicates must stay visible)"""
    cmd = ['ffmpeg', '-v', 'error', '-nostdin']
    if t0 is not None:
        cmd += ['-ss', f'{max(0.0, t0):.3f}']
    cmd += ['-i', path]
    if t1 is not None:
        cmd += ['-t', f'{max(0.05, t1 - max(0.0, t0 or 0)):.3f}']
    cmd += ['-vf', f'scale={w}:{h}:flags=area,format=gray', '-vsync', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'gray', '-']
    r = subprocess.run(cmd, capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8)
    n = a.size // (w * h)
    return a[:n * w * h].reshape(n, h, w).astype(np.float32)


# ------------------------------------------------------------------ slow-mo
def stutter_stats(fr):
    """repeated frames (a near-zero change while the neighbourhood moves: frame-doubled twixtor packs, held drawings) and
    jump frames (a change far above the neighbourhood: the drawing changes every 2nd / 3rd frame), from consecutive luma
    differences at the clip's own frame rate. A slow but smooth (interpolated) clip scores ~0; a still clip is not flagged."""
    if len(fr) < 6:
        return {'rep': 1.0, 'jump': 1.0, 'stutter': 2.0, 'n': int(len(fr))}
    d = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))
    loc = np.array([np.median(d[max(0, i - 4):i + 5]) for i in range(len(d))])
    mov = loc > 0.25
    rep = float(((d < 0.3 * loc) & mov).mean())
    jump = float(((d > 3.0 * loc) & mov).mean())
    return {'rep': round(rep, 3), 'jump': round(jump, 3), 'stutter': round(rep + jump, 3), 'med_diff': round(float(np.median(d)), 3),
            'n': int(len(fr))}


def colour_stats(clip, w=96, h=54):
    """share of strongly red and of blown-out pixels over the clip (4 frames / s)"""
    r = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', clip, '-vf', f'fps=4,scale={w}:{h}', '-f', 'rawvideo', '-pix_fmt',
                        'rgb24', '-'], capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8)
    n = a.size // (w * h * 3)
    if not n:
        return {'red': None, 'blown': None}
    x = a[:n * w * h * 3].reshape(n, h, w, 3).astype(np.float32) / 255
    R, G, B = x[..., 0], x[..., 1], x[..., 2]
    return {'red': round(float(((R > G + 0.3) & (R > B + 0.3)).mean()), 3), 'blown': round(float((x.mean(-1) > 0.97).mean()), 3)}


def _sidecar(clip):
    try:
        with open(clip + '.portrait.json', encoding='utf-8') as f:
            d = json.load(f)
        return next(iter(d.values())) if d else {}
    except (OSError, ValueError, StopIteration):
        return {}


def slowmo_feats(clip, item=None):
    item = item or {}
    pr = _sidecar(clip)
    rules = item.get('rules') or {}
    f = stutter_stats(frames(clip))
    f.update(colour_stats(clip))
    f.update(face_frac=pr.get('face_frac'), face_edge=pr.get('face_edge'), face_h=pr.get('face_h'),
             talk_sig=(pr.get('sig') or {}).get('talk'), talk_mouth=rules.get('talk_mouth'), mouth_flap=item.get('mouth_flap'))
    return f


def slowmo_verdict(f):
    why = []
    if f['stutter'] > SM['stutter']:
        why.append(f"stutter (repeat {f['rep']:.2f} + jump {f['jump']:.2f})")
    if ((f.get('talk_mouth') or 0) > SM['talk_mouth'] or (f.get('mouth_flap') or 0) > SM['mouth_flap']
            or (f.get('talk_sig') or 0) > SM['talk_sig']):
        why.append(f"talking (talk_mouth {f.get('talk_mouth')}, flap {f.get('mouth_flap')}, sig {f.get('talk_sig')})")
    if f.get('face_frac') is not None and f['face_frac'] < SM['face_frac']:
        why.append(f"face hidden ({f['face_frac']})")
    if (f.get('red') or 0) > SM['red'] or (f.get('blown') or 0) > SM['blown']:
        why.append(f"garish (red {f.get('red')}, blown {f.get('blown')})")
    return not why, why


def slowmo_check(clip, item=None):
    f = slowmo_feats(clip, item)
    ok, why = slowmo_verdict(f)
    return ok, why, f


# ------------------------------------------------------------------ sound
def sound_feats(mini, hit_t):
    fps, dur = probe(mini)
    t0 = max(0.0, hit_t - 0.9)
    fr = frames(mini, t0, min(dur or hit_t + 0.4, hit_t + 0.4))
    if len(fr) < 6:
        return {'n': int(len(fr))}
    hi = int(round((hit_t - t0) * fps))
    hi = min(max(hi, 1), len(fr) - 1)
    d = np.abs(np.diff(fr, axis=0))                   # d[i] = change into frame i+1
    e = d.mean(axis=(1, 2))
    # scene cut: a one-frame change far above the local level (and big in absolute terms)
    pre = int(round(SN['cut_pre'] * fps))
    lo = max(0, hi - pre - 1)
    seg = e[lo:max(lo + 1, hi - 1)]
    base = float(np.median(e)) + 1e-3
    cut_at = None
    for i in range(lo, max(lo, hi - 1)):
        if e[i] > max(28.0, 4.0 * base):
            # a hist check would be nicer; a big mean change + low correlation is a cut
            a, b = fr[i] - fr[i].mean(), fr[i + 1] - fr[i + 1].mean()
            corr = float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))
            if corr < 0.45:
                cut_at = round((i + 1 - hi) / fps, 3)
                break
    # impact peak: the motion energy around the hit vs the local maximum
    k = int(SN['peak_frames'])
    win = e[max(0, hi - 1 - k):min(len(e), hi + k)]
    loc = e[max(0, hi - 1 - int(0.35 * fps)):min(len(e), hi + int(0.25 * fps))]
    peak_rel = float(win.max() / (loc.max() + 1e-6)) if len(win) and len(loc) else 0.0
    # moving area, centroid and spread at the hit (the changes into frames hi-2 .. hi)
    m = d[max(0, hi - 3):hi + 1].max(axis=0)
    thr = max(10.0, float(np.percentile(m, 50)) + 6.0)
    mask = m > thr
    area = float(mask.mean())
    if mask.any():
        ys, xs = np.nonzero(mask)
        wts = m[mask]
        cx, cy = float((xs * wts).sum() / wts.sum()) / W, float((ys * wts).sum() / wts.sum()) / H
        centre = float(np.hypot(cx - 0.5, cy - 0.5))
        spread = float(np.sqrt(((((xs / W) - cx) ** 2) * wts).sum() / wts.sum() + ((((ys / H) - cy) ** 2) * wts).sum() / wts.sum()))
    else:
        cx = cy = centre = spread = None
    return {'n': int(len(fr)), 'fps': round(fps, 2), 'cut_before': cut_at, 'peak_rel': round(peak_rel, 3), 'area': round(area, 3),
            'centre': None if centre is None else round(centre, 3),
            'spread': None if spread is None else round(spread, 3), 'cx': cx and round(cx, 3), 'cy': cy and round(cy, 3),
            'energy_hit': round(float(e[min(hi - 1, len(e) - 1)]), 2), 'energy_med': round(base, 2)}


def sound_verdict(f):
    if f.get('n', 0) < 6:
        return True, []                               # undecodable here: fail open, the vision gate still judges it
    why = []
    if SN['cut_gate'] and f.get('cut_before') is not None:
        why.append(f"cut {f['cut_before']} s before the hit")
    if f['peak_rel'] < SN['peak_rel']:
        why.append(f"impact not at the hit (peak {f['peak_rel']})")
    if f['area'] < SN['area_min']:
        why.append(f"action too small (area {f['area']})")
    if (f.get('spread') or 0) > SN['spread_max']:
        why.append(f"wide chaotic shot (spread {f['spread']})")
    if f.get('centre') is not None and f['centre'] > SN['centre_max']:
        why.append(f"off-centre (centre {f['centre']})")
    return not why, why


def sound_check(mini, hit_t):
    f = sound_feats(mini, float(hit_t))
    ok, why = sound_verdict(f)
    return ok, why, f


# ------------------------------------------------------------------ selftest (synthetic clips, no media needed)
def _synth(path, kind, fps=30, n=60):
    """kind: smooth / doubled / cut / centre_hit / edge_hit"""
    yy, xx = np.mgrid[0:H, 0:W]
    out = []
    for i in range(n):
        if kind == 'doubled':
            j = i - i // 3                            # every 3rd frame repeats the previous one (24 -> 30 / 60 pull-down)
        else:
            j = i
        img = np.full((H, W), 90, np.float32) + 30 * np.sin((xx + 2.0 * j) / 9.0)
        if kind == 'cut' and i >= n // 2 - 5:
            img = np.full((H, W), 200, np.float32) - 40 * np.cos((yy + j) / 5.0)
        if kind in ('centre_hit', 'edge_hit', 'cut') and abs(i - n // 2) <= 1:
            cx = W // 2 if kind != 'edge_hit' else 8
            img[max(0, H // 2 - 24):H // 2 + 24, max(0, cx - 24):cx + 24] = 255
        out.append(np.clip(img, 0, 255).astype(np.uint8))
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'gray', '-s', f'{W}x{H}', '-r', str(fps), '-i', '-',
                          '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', path], stdin=subprocess.PIPE)
    p.communicate(np.stack(out).tobytes())
    return path


def selftest():
    import tempfile
    d = tempfile.mkdtemp(prefix='pf-')
    s = stutter_stats(frames(_synth(os.path.join(d, 's.mp4'), 'smooth')))
    dd = stutter_stats(frames(_synth(os.path.join(d, 'd.mp4'), 'doubled')))
    assert s['stutter'] < 0.05, s
    assert dd['stutter'] > 0.25, dd
    assert slowmo_verdict(dict(s, red=0.0, blown=0.0))[0]
    assert not slowmo_verdict(dict(dd, red=0.0, blown=0.0))[0]
    assert not slowmo_verdict(dict(s, red=0.3, blown=0.0))[0]
    assert not slowmo_verdict(dict(s, talk_mouth=0.9))[0]
    assert not slowmo_verdict(dict(s, face_frac=0.4))[0]
    c = sound_feats(_synth(os.path.join(d, 'c.mp4'), 'centre_hit'), 1.0)
    e = sound_feats(_synth(os.path.join(d, 'e.mp4'), 'edge_hit'), 1.0)
    k = sound_feats(_synth(os.path.join(d, 'k.mp4'), 'cut'), 1.0)
    assert c['centre'] < 0.1, c
    assert e['centre'] > 0.30, e
    assert sound_verdict(c)[0] and not sound_verdict(e)[0]
    assert k['cut_before'] is not None, k
    print(json.dumps({'ok': True, 'smooth': s, 'doubled': dd, 'centre': c, 'edge': e, 'cut': k}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['slowmo', 'sound', 'selftest'])
    ap.add_argument('path', nargs='?')
    ap.add_argument('--item')
    ap.add_argument('--hit-t', type=float)
    a = ap.parse_args()
    if a.cmd == 'selftest':
        return selftest()
    if a.cmd == 'slowmo':
        item = json.load(open(a.item, encoding='utf-8')) if a.item else None
        ok, why, f = slowmo_check(a.path, item)
    else:
        ok, why, f = sound_check(a.path, a.hit_t)
    print(json.dumps({'ok': ok, 'why': why, 'feats': f}))


if __name__ == '__main__':
    main()
