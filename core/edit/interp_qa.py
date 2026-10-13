"""Slow-motion interpolation artefact gate (cinematic_slowmo round 10; critics: hair strands doubled / ghosted on a Frieren
shot, a mouth that morphs and smears on a Kaguya shot, popping frames on the last shot).

risk(path)  -> dict(bad_frac, p90, spike, pairs): for pairs of consecutive DISTINCT drawings of the clip (held duplicates
skipped, like footage.py does before it interpolates) the forward-backward flow consistency |f_ab(p) + f_ba(p + f_ab(p))|
at 320 px; `bad_frac` = share of pixels above max(1.2 px, 12 % of the flow length) (occlusions / thin moving lines /
failed matches: the places a warped in-between ghosts), median over the sampled pairs; `p90` the 90th percentile over pairs;
`spike` = max step of the drawing-to-drawing motion over 3x its local median (a pose pop left un-interpolated).
Cached next to the clip (.interp.json)."""
import json
import os

import numpy as np

CACHE_VERSION = 3


def _frames(path, max_s=6.0, w=320):
    import cv2
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    out, i = [], 0
    while True:
        ok, f = cap.read()
        if not ok or i / fps > max_s:
            break
        g = cv2.cvtColor(cv2.resize(f, (w, max(8, int(f.shape[0] * w / f.shape[1]))), interpolation=cv2.INTER_AREA),
                         cv2.COLOR_BGR2GRAY)
        out.append((i / fps, g))
        i += 1
    return out, fps


def consistency(ga, gb, dis=None):
    """(bad mask float32, mean |flow|) for one pair of grey frames."""
    import cv2
    dis = dis or cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    fab = dis.calc(ga, gb, None)
    fba = dis.calc(gb, ga, None)
    h, w = ga.shape
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    back = cv2.remap(fba, gx + fab[..., 0], gy + fab[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    err = np.linalg.norm(fab + back, axis=2)
    mag = np.linalg.norm(fab, axis=2)
    bad = (err > np.maximum(1.2, 0.12 * mag)).astype(np.float32)
    return bad, float(np.median(mag))


def roll_rate(fr, fps):
    """Median signed in-plane rotation of the whole picture in degrees per 1/30 s (LK corner tracks over 3-frame steps ->
    similarity fit with RANSAC; the largest rigid motion wins, so a moving character does not move the estimate when the
    background dominates).  The critics measured a Kaguya take rolling -0.146 deg / frame; _sX2 stays under 0.047."""
    import cv2
    if len(fr) < 8:
        return 0.0
    rates = []
    for i in range(0, len(fr) - 3, 3):
        a, b = fr[i][1], fr[i + 3][1]
        p0 = cv2.goodFeaturesToTrack(a, 200, 0.01, 8)
        if p0 is None or len(p0) < 12:
            continue
        p1, st, _ = cv2.calcOpticalFlowPyrLK(a, b, p0, None, winSize=(21, 21), maxLevel=3)
        ok = st.ravel() == 1
        if ok.sum() < 12:
            continue
        M, inl = cv2.estimateAffinePartial2D(p0[ok], p1[ok], method=cv2.RANSAC, ransacReprojThreshold=1.5)
        if M is None or inl is None or inl.sum() < 10:
            continue
        ang = np.degrees(np.arctan2(M[1, 0], M[0, 0]))
        dt = (fr[i + 3][0] - fr[i][0]) or 0.1
        rates.append(ang / (dt * 30.0))
    return float(np.median(rates)) if rates else 0.0


def risk(path, t0=0.0, t1=4.0, use_cache=True):
    cp = path + '.interp.json'
    try:
        if use_cache and os.path.exists(cp) and os.path.getmtime(cp) >= os.path.getmtime(path):
            d = json.load(open(cp, encoding='utf-8'))
            if d.get('v') == CACHE_VERSION:
                return d
    except Exception:  # noqa: BLE001
        pass
    import cv2
    fr, fps = _frames(path, max_s=max(t1 + 0.5, 4.0))
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    # distinct drawings: a frame that differs from the last kept one by > 0.6 grey levels
    keep = [fr[0]] if fr else []
    for t, g in fr[1:]:
        if float(np.mean(np.abs(g.astype(np.float32) - keep[-1][1].astype(np.float32)))) > 0.6:
            keep.append((t, g))
    fracs, mags = [], []
    step = max(1, len(keep) // 14)
    for k in range(0, len(keep) - 1, step):
        (ta, ga), (tb, gb) = keep[k], keep[k + 1]
        if tb - ta > 0.5:
            continue
        bad, mg = consistency(ga, gb, dis)
        fracs.append(float(bad.mean()))
        mags.append(mg / 320.0 * 100.0 / max(1e-3, (tb - ta) * 30.0))
    # drawing-to-drawing motion steps (all pairs, cheap: mean abs diff) for the pose-pop spike
    diffs = np.array([float(np.mean(np.abs(keep[i + 1][1].astype(np.float32) - keep[i][1].astype(np.float32))))
                      for i in range(len(keep) - 1)]) if len(keep) > 3 else np.zeros(1)
    if len(diffs) > 5:
        from scipy.ndimage import median_filter
        loc = median_filter(diffs, 7, mode='nearest') + 0.5
        spike = float(np.max(diffs / loc))
    else:
        spike = 1.0
    roll = roll_rate(fr, fps)
    res = dict(v=CACHE_VERSION, roll=round(roll, 4), bad_frac=round(float(np.median(fracs)), 4) if fracs else 0.0,
               p90=round(float(np.percentile(fracs, 90)), 4) if fracs else 0.0,
               spike=round(spike, 2), pairs=len(fracs), distinct=len(keep), n=len(fr),
               drawing_fps=round(len(keep) / max(1e-3, (fr[-1][0] - fr[0][0])), 1) if len(fr) > 1 else 0.0)
    try:
        json.dump(res, open(cp, 'w', encoding='utf-8'))
    except OSError:
        pass
    return res


if __name__ == '__main__':
    import sys
    for p in sys.argv[1:]:
        print(os.path.basename(os.path.dirname(p)) + '/' + os.path.basename(p), json.dumps(risk(p, use_cache=False)))
