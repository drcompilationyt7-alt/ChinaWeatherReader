#!/usr/bin/env python3
"""
Vertical reframe of a finished 16:9 sound edit master into a real 9:16 Short (owner 2026-10-12: "real shorts, not a
landscape clip letterboxed inside 9:16"; the references are rot90 / letterboxed, the owner overrules them).

Per shot (the plan's t0 / t1, hard cuts only, so the crop may jump at a cut and never inside a shot):
  - the subject centre is tracked on frames sampled every 1/10 s: anime faces (deepghs YOLOv8n, library.AnimeFaces;
    the largest confident faces weigh most), else the action centre (frame difference + edge energy, column profile);
  - the track becomes ONE smooth move per shot: a hold when the subject stays put (range < 6 % of the width), else an
    eased pan (smoothstep) from where the subject starts to where it ends, clamped so the 9:16 window stays inside;
  - a shot whose action is wider than the window (two faces far apart, or the action spread over the frame) gets a
    slow eased pan across the action rather than a crop that cuts it in half. No bars, no zoom: the preset has none.
The crop (607.5 x 1080 of 1920 x 1080) is scaled to 1080 x 1920 (Lanczos) with a light unsharp; audio is copied.
Picture timing is untouched, so the hit sync stays exactly as rendered.

    python core/edit/reframe.py --in master.mp4 --plan plan.json --out vertical.mp4 [--threads 2]
"""
import argparse
import json
import os
import subprocess
import sys

import cv2
import numpy as np

cv2.setNumThreads(int(os.environ.get('EDIT_THREADS') or 2))

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT_W, OUT_H = 1080, 1920


def log(*a):
    print('[reframe]', *a, file=sys.stderr, flush=True)


def _probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=width,height,r_frame_rate', '-of', 'json', path], capture_output=True, text=True)
    s = json.loads(r.stdout)['streams'][0]
    n, d = s['r_frame_rate'].split('/')
    return int(s['width']), int(s['height']), float(n) / float(d)


def _frames(path, w, h, scale=None):
    vf = ['-vf', f'scale={scale[0]}:{scale[1]}'] if scale else []
    W, H = scale or (w, h)
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', path, *vf, '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'],
                         stdout=subprocess.PIPE)
    n = W * H * 3
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        yield np.frombuffer(b, np.uint8).reshape(H, W, 3)
    p.wait()


def _face_det():
    try:
        import library as LIB
        mp = LIB.model_path('anime_face')
        return LIB.AnimeFaces(mp) if mp else None
    except Exception as e:  # noqa: BLE001
        log('no face detector:', e)
        return None


def _centre(frame, prev, det):
    """(centre x 0-1, spread 0-1, kind) of the subject in one analysis frame."""
    if det is not None:
        try:
            fs = [f for f in det(frame, conf=0.4) if f[2] * f[3] >= 0.004]
        except Exception:  # noqa: BLE001
            fs = []
        if fs:
            wts = np.array([f[2] * f[3] * f[4] for f in fs])
            cx = np.array([f[0] + f[2] / 2 for f in fs])
            c = float((wts * cx).sum() / wts.sum())
            big = [f for f in fs if f[2] * f[3] >= 0.35 * fs[0][2] * fs[0][3]]
            spread = float(max(f[0] + f[2] for f in big) - min(f[0] for f in big))
            return c, spread, 'face'
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
    e = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1))
    m = e / (e.mean() + 1e-6)
    if prev is not None:
        d = np.abs(g - prev)
        m = 0.35 * m + 1.0 * d / (d.mean() + 1e-6)
    col = m.mean(0)
    col = np.convolve(col, np.ones(9) / 9, 'same')
    col = np.maximum(col - np.percentile(col, 40), 0)
    if col.sum() <= 1e-6:
        return 0.5, 0.0, 'none'
    x = np.linspace(0, 1, len(col))
    c = float((col * x).sum() / col.sum())
    cs = np.cumsum(col) / col.sum()
    spread = float(np.interp(0.85, cs, x) - np.interp(0.15, cs, x))
    return c, spread, 'action'


# a pan is only smooth if it is slow: the sound edit's shots last 0.3-0.6 s, and a 25 %-of-width move inside one is a
# whip, not a pan. The move is capped at PAN_MAX of the master width per second (centred on the subject's median), so
# a short shot is an almost static crop (as the dance refs' 9:16 crops are) and only the longer ones drift.
PAN_MAX = float(os.environ.get('REFRAME_PAN_MAX', '0.12'))


_WH = {}


def _focus_master(s):
    """The shot's sound-source x (sound_visible focus_x, 0-1 of the clip's content box) in master coordinates."""
    fx = s.get('focus_x')
    if fx is None or not (s.get('visible') or {}).get('ok', True) or not s.get('sfx'):
        return None
    p = s.get('path')
    if p not in _WH:
        try:
            _WH[p] = _probe(p)[:2]
        except Exception:  # noqa: BLE001
            _WH[p] = (1920, 1080)
    w, h = _WH[p]
    x0, y0, x1, y1 = s.get('content_box') or [0, 0, 1, 1]
    ca = (w * (x1 - x0)) / max(1.0, h * (y1 - y0))
    if s.get('layout') == 'letterbox' or ca > 16 / 9:
        return 0.5 + (float(fx) - 0.5) * min(ca, 16 / 9) / (16 / 9) if s.get('layout') == 'letterbox' else             0.5 + (float(fx) - 0.5) * ca / (16 / 9)
    return float(fx)


def plan_track(samples, half, dur=1.0):
    """samples [(u 0-1 within the shot, cx, spread, kind)] -> (c_start, c_end) of the shot's eased move."""
    lo, hi = half, 1 - half
    if not samples:
        return 0.5, 0.5
    u = np.array([s[0] for s in samples])
    c = np.array([s[1] for s in samples])
    sp = np.array([s[2] for s in samples])
    if len(c) >= 3:
        c = np.array([np.median(c[max(0, i - 1):i + 2]) for i in range(len(c))])
    k = max(1, len(c) // 3)
    a, b = float(np.median(c[:k])), float(np.median(c[-k:]))
    wide = float(np.median(sp)) > 2 * half * 1.15
    if wide:
        # the action is wider than the window: a slow pan across it, in the direction the subject drifts
        span = min(float(np.median(sp)), 0.6)
        mid = float(np.median(c))
        d = 1 if b >= a else -1
        a, b = mid - d * span / 4, mid + d * span / 4
    elif abs(b - a) < 0.06:
        a = b = float(np.median(c))
    lim = PAN_MAX * max(0.05, dur)
    if abs(b - a) > lim:
        mid = float(np.median(c)) if not wide else (a + b) / 2
        d = 1 if b >= a else -1
        a, b = mid - d * lim / 2, mid + d * lim / 2
    return float(np.clip(a, lo, hi)), float(np.clip(b, lo, hi))


def reframe(src, plan_path, out, threads=2, crf=17):
    with open(plan_path, encoding='utf-8') as f:
        plan = json.load(f)
    w, h, fps = _probe(src)
    if w < h:
        raise RuntimeError('the master is not landscape')
    cw = h * OUT_W / OUT_H                  # crop width in master pixels (607.5 at 1080p)
    half = cw / w / 2
    shots = sorted(plan['shots'], key=lambda s: s['t0'])
    bounds = [(float(s['t0']), float(s['t1'])) for s in shots]
    det = _face_det()
    aw, ah = 640, int(round(640 * h / w / 2) * 2)
    step = max(1, int(round(fps / 10)))
    samples = [[] for _ in shots]
    prev, si = None, 0
    for i, fr in enumerate(_frames(src, w, h, (aw, ah))):
        t = i / fps
        while si < len(bounds) - 1 and t >= bounds[si][1]:
            si, prev = si + 1, None
        if i % step:
            continue
        t0, t1 = bounds[si]
        c, sp, kind = _centre(fr, prev, det)
        prev = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).astype(np.float32)
        samples[si].append(((t - t0) / max(1e-3, t1 - t0), c, sp, kind))
    tracks = [plan_track(s, half, t1 - t0) for s, (t0, t1) in zip(samples, bounds)]
    # owner 2026-10-12: the crop centres on what MAKES THE SOUND at the hit (sound_visible focus_x of the clip), held
    # for the shot (shots are 0.3-0.6 s); a hit-less shot keeps the face / action track above
    n_focus = 0
    for k, s in enumerate(shots):
        fx = _focus_master(s)
        if fx is None:
            continue
        a, b = tracks[k]
        c = float(np.clip(fx, half, 1 - half))
        # a face track that already holds the source inside the window is kept (less jump between shots)
        if abs((a + b) / 2 - fx) > half * 0.6:
            tracks[k] = (c, c)
            n_focus += 1
    log(f'focus on the sound source: {n_focus} of {sum(1 for s in shots if _focus_master(s) is not None)} hit shots re-centred')
    log('tracks:', ' '.join(f'{a:.2f}>{b:.2f}' for a, b in tracks))

    enc = subprocess.Popen(['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{OUT_W}x{OUT_H}',
                            '-r', f'{fps:.6f}', '-i', '-', '-i', src, '-map', '0:v', '-map', '1:a?', '-c:v', 'libx264',
                            '-preset', 'medium', '-crf', str(crf), '-pix_fmt', 'yuv420p', '-threads', str(threads),
                            '-c:a', 'copy', '-movflags', '+faststart', out], stdin=subprocess.PIPE)
    si = 0
    for i, fr in enumerate(_frames(src, w, h)):
        t = i / fps
        while si < len(bounds) - 1 and t >= bounds[si][1]:
            si += 1
        t0, t1 = bounds[si]
        u = float(np.clip((t - t0) / max(1e-3, t1 - t0), 0, 1))
        e = u * u * (3 - 2 * u)
        a, b = tracks[si]
        cx = (a + (b - a) * e) * w
        x0 = float(np.clip(cx - cw / 2, 0, w - cw))
        ix = int(np.floor(x0))
        crop = fr[:, ix:min(w, ix + int(np.ceil(cw)) + 1)]
        # sub-pixel position: shift by the fractional part with an affine warp (no jitter on slow pans)
        M = np.float32([[OUT_H / h, 0, -(x0 - ix) * OUT_H / h], [0, OUT_H / h, 0]])
        v = cv2.warpAffine(crop, M, (OUT_W, OUT_H), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)
        bl = cv2.GaussianBlur(v, (0, 0), 1.2)
        v = cv2.addWeighted(v, 1.35, bl, -0.35, 0)
        enc.stdin.write(v.tobytes())
    enc.stdin.close()
    if enc.wait() != 0 or not os.path.exists(out):
        raise RuntimeError('reframe encode failed')
    return {'out': out, 'tracks': tracks}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='src', required=True)
    ap.add_argument('--plan', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--threads', type=int, default=2)
    a = ap.parse_args()
    r = reframe(a.src, a.plan, a.out, a.threads)
    print(json.dumps({'ok': True, 'out': r['out']}))


if __name__ == '__main__':
    main()
