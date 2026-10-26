#!/usr/bin/env python3
"""Reference-profile quality gate + manga-panel detector for the ranking compilation (2026-10-26).

Owner rule (2026-10-25 late): a source edit must be ON PAR with ALL the reference edits we downloaded and analysed
(the 4 ranking refs + the owner's edit refs in Desktop/edit-refs) in QUALITY and in the theme's VIBE: real effects,
tight song sync, 3D camera moves, text work. Plain / calm footage fails, even for romance (soft but edited).
And: prefer ANIME-FOOTAGE edits over manga-panel edits.

Per window [s, e] of an edit, one decode pass measures
  effects   flashes / shakes / zooms per s (ranking_score), frame-difference energy
  camera    per 0.1 s homography between frames of one shot: zoom (log scale), roll (rotation), 3D tilt
            (the perspective terms: AE 3D-layer / camera moves), the share of time the camera is moving
  text      OCR at 2 fps: share of frames with overlay text, share of text frames where the text moves / changes
  sync      cuts per s, the share of cuts on a beat / onset (ranking_score)
  rhythm    shot-length variation (bursts + holds), the share of very short shots (< 0.25 s, flash cuts)
  grade     saturation, contrast, colour variation over time (grade energy)
  manga     per 2 fps frame: grey + screentone (halftone high-frequency energy in mid-grey areas), panel borders
            (long straight gutter lines), speech bubbles (white rounded blobs with dark text inside)
THEMES: action (power lists + action / isekai / sports genre lists), beauty (waifu lists), romance (romance / sad /
comedy / horror genre lists). The profile of a theme = robust quantiles of its reference windows (PROFILE_JSON,
built by `build`). A candidate scores 0-10 by its closeness: per feature 1.0 at or above the theme's reference median,
falling to 0 at the reference floor (q10 minus a margin); a few vibe caps (romance: no action-level shaking). Group
means, weighted (GROUP_W).

    python core/edit/ranking_profile.py build            # reference features -> refs/ranking-ref-profile.json
    python core/edit/ranking_profile.py calibrate        # the calibration table (refs, Toji / Gojo, Horimiya ...)
    python core/edit/ranking_profile.py score <mp4> [--s S --e E --theme action]
"""
import argparse
import json
import os
import sys

THREADS = int(os.environ.get('RANKING_PROFILE_THREADS') or 4)
os.environ.setdefault('OMP_NUM_THREADS', str(THREADS))
import cv2
import numpy as np

cv2.setNumThreads(THREADS)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import ranking_score as RS  # noqa: E402

PROFILE_JSON = os.path.join(HERE, 'refs', 'ranking-ref-profile.json')
REFS = os.path.join(os.path.expanduser('~'), 'Desktop', 'edit-refs')
CACHE = os.path.join(ROOT, 'output', 'ranking-proto', 'cache', 'profile')

# the gate (calibrated 2026-10-26, see `calibrate` and the study section 12)
REF_MIN = {'action': 5.5, 'beauty': 5.0, 'romance': 5.0}
HAND_MIN = 5.5              # camera work or text: raw Bluray 4.0 / Black Flash raw 3.2 / creditless OP 5.2 / Horimiya 2.2
EFFECTS_MIN = 3.5           # old Yuji (owner: not eventful) 2.5
MANGA_MAX = 0.35            # >= this share of manga-looking frames = a manga-panel edit (fallback only, rank penalty)

FEATS = {   # feature -> group
    'flashes_per_s': 'effects', 'shakes_per_s': 'effects', 'zooms_per_s': 'effects', 'motion': 'effects',
    'cam_zoom': 'camera', 'cam_roll': 'camera', 'cam_tilt': 'camera', 'cam_active': 'camera',
    'text_share': 'text', 'text_anim': 'text',
    'cuts_per_s': 'sync', 'beat_sync': 'sync',
    'shot_cv': 'rhythm', 'flash_cuts': 'rhythm',
    'sat': 'grade', 'contrast': 'grade', 'color_var': 'grade',
}
GROUP_W = {'effects': 0.26, 'camera': 0.22, 'sync': 0.2, 'rhythm': 0.1, 'text': 0.1, 'grade': 0.12}
# vibe caps (theme -> feature -> multiple of the reference q90 above which it counts against the vibe)
VIBE_CAP = {'romance': {'shakes_per_s': 2.0, 'flashes_per_s': 2.5}}


def theme_of(topic_key=None, T=None):
    k = (topic_key or '').lower()
    if 'waifu' in k:
        return 'beauty'
    if any(x in k for x in ('romance', 'sad', 'comedy', 'horror')):
        return 'romance'
    if T is not None and T.get('pace') == 'calm':
        return 'romance'
    return 'action'


# ------------------------------------------------------------------ measurement

def _homography(a, b):
    p = cv2.goodFeaturesToTrack(a, 150, 0.01, 8)
    if p is None or len(p) < 12:
        return None
    q, st, _ = cv2.calcOpticalFlowPyrLK(a, b, p, None, winSize=(15, 15), maxLevel=2)
    ok = st.ravel() == 1
    if ok.sum() < 12:
        return None
    H, inl = cv2.findHomography(p[ok], q[ok], cv2.RANSAC, 2.0)
    if H is None or inl is None or inl.mean() < 0.45:
        return None
    return H


def _manga(c):
    """One frame (BGR, ~480 wide) -> dict of manga cues (0/1 flags + numbers)."""
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    sat = float(hsv[..., 1].mean())
    g = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    bl = cv2.GaussianBlur(g, (7, 7), 0)
    mid = (bl > 70) & (bl < 200)
    hf = np.abs(g.astype(np.int16) - bl.astype(np.int16))
    tone = float(hf[mid].mean()) if mid.mean() > 0.05 else 0.0       # halftone dots: high-frequency in mid-grey
    white = float((g > 235).mean())
    ed = cv2.Canny(g, 60, 160)
    lines = cv2.HoughLinesP(ed, 1, np.pi / 180, 80, minLineLength=int(0.35 * min(h, w)), maxLineGap=4)
    straight = 0
    if lines is not None:
        for x1, y1, x2, y2 in lines.reshape(-1, 4):
            if abs(x1 - x2) <= 2 or abs(y1 - y2) <= 2:                  # axis-aligned = a panel border / gutter
                straight += 1
    # speech bubbles: white blobs, 0.4-12 % of the frame, round-ish (solidity), dark marks (text) inside
    bub = 0
    n, lab, stats, _ = cv2.connectedComponentsWithStats((g > 225).astype(np.uint8), 8)
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if not (0.004 * h * w <= area <= 0.12 * h * w) or x == 0 or y == 0 or x + ww >= w or y + hh >= h:
            continue
        m = (lab[y:y + hh, x:x + ww] == i).astype(np.uint8)
        cnt, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnt:
            continue
        hull = cv2.contourArea(cv2.convexHull(cnt[0]))
        filled = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        holes = float(((filled > 0) & (m == 0)).sum()) / max(1, area)
        if hull and area / hull > 0.8 and 0.01 < holes < 0.4 and 0.4 < ww / max(1, hh) < 3.5:
            bub += 1
    grey = sat < 28
    flag = grey and (tone > 9.0 or straight >= 3 or bub >= 1)
    return {'grey': grey, 'tone': round(tone, 2), 'lines': straight, 'bubbles': bub, 'white': round(white, 3),
            'manga': bool(flag)}


def measure(path, s=None, e=None, crop=None, per_t=None, ocr=True, audio=None):
    """Features of the window [s, e] (s) of `path`. crop = (x0, y0, x1, y1) in 0-1 (the ranking refs: the clip band).
    per_t = {t: [(text, box), ...]} OCR already done (ranking_compilation.text_scan), else OCR at 2 fps."""
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur = n / fps if n else 0
    s = 0.0 if s is None else float(s)
    e = dur if e is None else float(min(e, dur or e))
    step = max(1, int(round(fps / 30)))
    cap.set(cv2.CAP_PROP_POS_MSEC, s * 1000)
    i = int(round(s * fps))
    t, luma, diff, cut, sx, sy, zoom = [], [], [], [], [], [], []
    cam, colr, manga, texts = [], [], [], []
    prev = prev_h = prev_c = None
    last_cam_t = last_m_t = -9.0
    win = cv2.createHanningWindow((160, 90), cv2.CV_32F)
    yy, xx = np.mgrid[0:45, 0:80].astype(np.float32)
    rx, ry = xx - 39.5, yy - 22
    rn = np.sqrt(rx ** 2 + ry ** 2) + 1e-3
    rx, ry = rx / rn, ry / rn
    since_cut = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        tt = i / fps
        i += 1
        if tt >= e:
            break
        if (i - 1) % step:
            continue
        ok, f = cap.retrieve()
        if not ok:
            break
        if crop:
            hh_, ww_ = f.shape[:2]
            f = f[int(crop[1] * hh_):int(crop[3] * hh_), int(crop[0] * ww_):int(crop[2] * ww_)]
        sm = cv2.resize(f, (160, 90), interpolation=cv2.INTER_AREA)
        g = cv2.cvtColor(sm, cv2.COLOR_BGR2GRAY).astype(np.float32)
        hsv = cv2.cvtColor(sm, cv2.COLOR_BGR2HSV)
        hh = cv2.normalize(cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256]), None).flatten()
        t.append(tt)
        luma.append(float(g.mean()))
        c = False
        if prev is None:
            diff.append(0.0); cut.append(0); sx.append(0.0); sy.append(0.0); zoom.append(0.0)
        else:
            diff.append(float(np.abs(g - prev).mean()))
            c = 1 - cv2.compareHist(prev_h, hh, cv2.HISTCMP_CORREL) > 0.45
            cut.append(int(c))
            if c:
                sx.append(0.0); sy.append(0.0); zoom.append(0.0)
            else:
                (dx, dy), _ = cv2.phaseCorrelate(prev, g, win)
                sx.append(float(dx)); sy.append(float(dy))
                a_ = cv2.resize(prev, (80, 45)); b_ = cv2.resize(g, (80, 45))
                fl = cv2.calcOpticalFlowFarneback(a_, b_, None, 0.5, 2, 9, 2, 5, 1.1, 0)
                zoom.append(float((fl[..., 0] * rx + fl[..., 1] * ry).mean()))
        since_cut = 0 if c else since_cut + 1
        colr.append((float(hsv[..., 1].mean()), float(g.std()), *[float(x) for x in sm.reshape(-1, 3).mean(0)]))
        # camera: homography every 0.1 s inside a shot, on a 320-wide grey copy
        if tt - last_cam_t >= 0.099:
            mid = cv2.cvtColor(cv2.resize(f, (320, 180), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            if prev_c is not None and since_cut >= 3 and tt - last_cam_t < 0.2:
                H = _homography(prev_c, mid)
                if H is not None:
                    A = H[:2, :2] / H[2, 2]
                    det = abs(np.linalg.det(A))
                    sc = abs(np.log(max(det, 1e-6))) / 2
                    rot = abs(np.degrees(np.arctan2(A[1, 0] - A[0, 1], A[0, 0] + A[1, 1])))
                    tilt = float(np.hypot(H[2, 0], H[2, 1]) / abs(H[2, 2]) * 320)
                    tr = float(np.hypot(H[0, 2], H[1, 2]) / abs(H[2, 2]) / 320)
                    if sc < 0.5 and rot < 30 and tilt < 2:
                        cam.append((tt, sc, rot, tilt, tr))
            prev_c, last_cam_t = mid, tt
        if tt - last_m_t >= 0.499:
            mf = cv2.resize(f, (480, int(f.shape[0] * 480 / f.shape[1])), interpolation=cv2.INTER_AREA)
            manga.append(_manga(mf))
            if ocr and per_t is None:
                texts.append((tt, _ocr_boxes(mf)))
            last_m_t = tt
        prev, prev_h = g, hh
    cap.release()
    v = {k: np.array(x, np.float32) for k, x in dict(t=t, luma=luma, diff=diff, cut=cut, sx=sx, sy=sy, zoom=zoom).items()}
    v['fps'] = fps / step
    a = audio if audio is not None else RS.audio_feats(path)
    a['db'] = np.maximum(a['db'], -40.0)
    mx = RS.window_metrics(v, a, s, e)
    L = max(1e-3, e - s)
    out = {k: mx[k] for k in ('cuts_per_s', 'beat_sync', 'motion', 'flashes_per_s', 'shakes_per_s', 'zooms_per_s')}
    # camera
    if cam:
        C = np.array(cam)
        out['cam_zoom'] = float(C[:, 1].sum() / L)                     # |log scale| per s
        out['cam_roll'] = float(C[:, 2].sum() / L)                     # degrees per s
        out['cam_tilt'] = float(np.mean(C[:, 3]))                      # perspective (3D) per 0.1 s step
        active = (C[:, 1] > 0.006) | (C[:, 2] > 0.25) | (C[:, 3] > 0.012)
        out['cam_active'] = float(active.sum() * 0.1 / L)
    else:
        out.update(cam_zoom=0.0, cam_roll=0.0, cam_tilt=0.0, cam_active=0.0)
    # rhythm
    ct = v['t'][v['cut'] > 0]
    shots = np.diff(np.concatenate([[s], ct, [e]]))
    shots = shots[shots > 0.02]
    out['shot_cv'] = float(shots.std() / (shots.mean() + 1e-6)) if len(shots) >= 3 else 0.0
    out['flash_cuts'] = float((shots < 0.25).sum() / L)
    # grade
    if colr:
        Cc = np.array(colr)
        out['sat'] = float(Cc[:, 0].mean())
        out['contrast'] = float(Cc[:, 1].mean())
        out['color_var'] = float(np.linalg.norm(Cc[:, 2:].std(0)))
    else:
        out.update(sat=0.0, contrast=0.0, color_var=0.0)
    # text
    if per_t is not None:
        texts = [(tt, [b for _, b in per_t[tt]]) for tt in sorted(per_t) if s <= tt < e]
    tx = [(tt, [b for b in bs if (b[2] - b[0]) * (b[3] - b[1]) > 0.0015]) for tt, bs in texts]
    has = [bool(bs) for _, bs in tx]
    out['text_share'] = float(np.mean(has)) if has else 0.0
    moves = 0
    pairs = 0
    for (t0, b0), (t1, b1) in zip(tx, tx[1:]):
        if b0 and b1:
            pairs += 1
            c0 = np.mean([[(b[0] + b[2]) / 2, (b[1] + b[3]) / 2, (b[2] - b[0])] for b in b0], 0)
            c1 = np.mean([[(b[0] + b[2]) / 2, (b[1] + b[3]) / 2, (b[2] - b[0])] for b in b1], 0)
            if np.abs(c0[:2] - c1[:2]).max() > 0.02 or abs(c0[2] - c1[2]) > 0.15 * max(c0[2], 1e-3) or len(b0) != len(b1):
                moves += 1
    out['text_anim'] = float(moves / len(tx)) if tx else 0.0
    # manga
    out['manga_share'] = float(np.mean([m['manga'] for m in manga])) if manga else 0.0
    out['grey_share'] = float(np.mean([m['grey'] for m in manga])) if manga else 0.0
    out['manga_cues'] = {'tone': float(np.median([m['tone'] for m in manga])) if manga else 0,
                         'lines': float(np.mean([m['lines'] for m in manga])) if manga else 0,
                         'bubbles': float(np.mean([m['bubbles'] > 0 for m in manga])) if manga else 0}
    out['len'] = round(L, 2)
    return {k: (round(x, 4) if isinstance(x, float) else x) for k, x in out.items()}


_OCR = [None]


def _ocr_boxes(img):
    if _OCR[0] is None:
        from rapidocr_onnxruntime import RapidOCR
        try:
            _OCR[0] = RapidOCR(intra_op_num_threads=THREADS)
        except Exception:  # noqa: BLE001
            _OCR[0] = RapidOCR()
    h, w = img.shape[:2]
    res, _ = _OCR[0](img)
    out = []
    for box, txt, conf in res or []:
        if conf < 0.6 or len(txt.strip()) < 2:
            continue
        xs = [p[0] / w for p in box]
        ys = [p[1] / h for p in box]
        out.append([min(xs), min(ys), max(xs), max(ys)])
    return out


# ------------------------------------------------------------------ manga vs anime footage (SigLIP zero-shot)
# The frame cues above (grey + screentone / panel lines / bubbles) miss colour-tinted manga (the Yuji @DivineHotdog edit:
# red / blue duotone line art in 3 vertical panels, cue share 0.02). SigLIP zero-shot over 2 fps frames separates the
# EDITS (2026-10-26, share of frames with p(manga) > 0.5): Yuji @DivineHotdog 0.74 | Gojo 0.23, Yuta @zelsan1 0.35, Toji
# 0.07, Kurisu 0.05, Waguri 0.17, Yuta @PJUNKIE 0.05, Toji @ZyperAmv 0.14. Raw monochrome fight footage also reads as
# manga (Black Flash raw 1.0, Bluray 0.67), but raw footage already fails the reference gate (no editor's hand), and a
# manga flag is only a rank penalty (fallback), never a reject.
MANGA_PROMPTS = ['a black and white manga page', 'a manga panel with ink lines and screentone', 'a colored manga panel',
                 'a comic book page with panels and speech bubbles']
ANIME_PROMPTS = ['a screenshot from an anime episode', 'an anime screencap', 'a frame from an anime music video',
                 'a 3D render']
MANGA_SIG_MIN = 0.5         # share of frames with p(manga) > 0.5


def manga_siglip(sig, frames):
    """library.Siglip + BGR frames (2 fps) -> share of frames that look like manga panels (0-1)."""
    if not frames:
        return 0.0
    T = sig.text(MANGA_PROMPTS + ANIME_PROMPTS)
    lg = sig.images(list(frames)) @ T.T * sig.scale
    p = np.exp(lg - lg.max(1, keepdims=True))
    p = p / p.sum(1, keepdims=True)
    return round(float((p[:, :len(MANGA_PROMPTS)].sum(1) > 0.5).mean()), 3)


# ------------------------------------------------------------------ profile + score

def load_profile(p=PROFILE_JSON):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return None


def score(f, theme, prof=None):
    """Feature dict -> (0-10 closeness to the theme's reference profile, per-group scores 0-10 incl. 'hand').
    Per feature: 1.0 at / above the theme's reference median, 0 at the floor (q10 minus half the q10-q50 gap); a feature
    whose reference median sits at the floor (e.g. text animation in the action refs) is not scored. The camera group
    weighs cam_active (the share of time the camera moves: the editor's hand, raw footage rarely has it) half.
    'hand' = max(camera, text presence): the editor's own work on top of the footage (raw / plain footage lacks both)."""
    prof = prof or load_profile()
    if not prof:
        return None, {}
    P = prof['themes'].get(theme) or prof['themes']['action']
    c_of = {}
    for k in FEATS:
        q = P['q'].get(k)
        if not q:
            continue
        q10, q50, q90 = q['q10'], q['q50'], q['q90']
        floor = max(0.0, q10 - 0.5 * (q50 - q10))
        if q50 - floor < 1e-6:
            continue
        x = float(f.get(k, 0.0))
        c = float(np.clip((x - floor) / (q50 - floor), 0, 1))
        cap = VIBE_CAP.get(theme, {}).get(k)
        if cap and q90 > 0 and x > cap * q90:
            c *= float(np.clip(1 - (x - cap * q90) / (cap * q90), 0.3, 1))
        c_of[k] = c
    g = {}
    for k, gname in FEATS.items():
        if k in c_of:
            g.setdefault(gname, []).append(c_of[k])
    g = {k: float(np.mean(v)) for k, v in g.items()}
    cam_rest = [c_of[k] for k in ('cam_zoom', 'cam_roll', 'cam_tilt') if k in c_of]
    if 'cam_active' in c_of and cam_rest:
        g['camera'] = 0.5 * c_of['cam_active'] + 0.5 * float(np.mean(cam_rest))
    tot = sum(GROUP_W[k] * g.get(k, 0) for k in GROUP_W) / sum(GROUP_W[k] for k in GROUP_W if k in g)
    g['hand'] = max(g.get('camera', 0.0), c_of.get('text_share', 0.0))
    return round(10 * tot, 2), {k: round(10 * v, 1) for k, v in g.items()}


def gate(f, theme, prof=None):
    """-> dict(ref_score, ref_groups, manga, ok, why). Pass: score >= REF_MIN[theme] AND the editor's hand (camera work
    or text) >= HAND_MIN AND effects >= EFFECTS_MIN."""
    sc, gr = score(f, theme, prof)
    # the SigLIP share when measured (evaluate / rescreen), else the frame cues
    manga = (float(f['manga_siglip']) >= MANGA_SIG_MIN) if f.get('manga_siglip') is not None else         float(f.get('manga_share', 0)) >= MANGA_MAX
    if sc is None:
        return {'ref_score': None, 'ref_groups': {}, 'manga': manga, 'ok': True, 'why': 'no_profile'}
    why = []
    if sc < REF_MIN.get(theme, 6.0):
        why.append(f'below_ref_{theme}_{sc}')
    if gr.get('hand', 0) < HAND_MIN:
        why.append(f'plain_footage_hand_{gr.get("hand")}')
    if gr.get('effects', 0) < EFFECTS_MIN:
        why.append(f'few_effects_{gr.get("effects")}')
    return {'ref_score': sc, 'ref_groups': gr, 'manga': manga, 'manga_share': f.get('manga_siglip', f.get('manga_share')),
            'ok': not why,
            'why': ','.join(why)}


# ------------------------------------------------------------------ the reference set

BAND = (0.0, 0.41, 1.0, 0.835)          # the ranking refs' 16:9 clip band in the 3:4 canvas
RANK_SEGS = {'v1tnk8GwWD8': [(0.3, 21.0), (21.6, 41.4), (41.6, 59.5)], 'XSHZhNzRovw': [(0.3, 28.8), (29.1, 52.3)],
             'XQRx0mqFxbc': [(0.3, 24.8), (25.1, 43.8), (44.1, 59.4)], 'BnsZETrlnFA': [(0.3, 33.4), (35.2, 59.4)]}
OWNER_REFS = {   # owner edit refs (anime only; the K-pop refs are live action) -> theme
    'action': ['general/cj12R_cW2ac', 'general/L2c39drDVuk', 'general/m3lv7H6cu_Y', 'general/JQHKYzYDneE',
               'fx/WGvtliE1298', 'sound/FB15TNFAHVU', 'sound/h0HNYocSDE8', 'sound/jKSxAkOS3JY', 'sound/n_NvC1IQUMQ',
               'sound/qgzBYo7gvhE', 'dance/1L7mzyGhXCA'],
    'beauty': ['character/JDguApf568Q', 'character/KSjpckUTItU', 'character/TG_hmVvoWC4', 'character/YH_GH1nk1c0',
               'character/eEPZ7fUpoE0', 'character/mGz9Fm14UfU', 'character/yUuiSey5kmE', 'general/S2rzMsl0N7k',
               'general/ZkWQloS4jtM', 'general/zawBCooJwLw', 'general/_sX2EUexbW8', 'general/GHBmP7aUoUc',
               'dance/ONlH6QaPl-8', 'dance/vnCJ-MhrY-0'],
    'romance': ['general/i4fSruIkkxk', 'character/rfsjEohxVaw'],
}


def ref_windows():
    """(name, theme, path, s, e, crop) of every reference window: the ranking refs' segments (romance), the owner refs
    (whole edit minus the first / last 4 %, at most 24 s around the middle)."""
    out = []
    for vid, segs in RANK_SEGS.items():
        p = os.path.join(REFS, 'ranking', f'ref_{vid}.mp4')
        for k, (s, e) in enumerate(segs):
            out.append((f'rank:{vid}#{k + 1}', 'romance', p, s, e, BAND))
    for th, lst in OWNER_REFS.items():
        for x in lst:
            p = os.path.join(REFS, x + '.mp4')
            if not os.path.exists(p):
                continue
            cap = cv2.VideoCapture(p)
            d = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or 30)
            cap.release()
            s, e = 0.04 * d, 0.96 * d
            if e - s > 24:
                m = (s + e) / 2
                s, e = m - 12, m + 12
            out.append((f'owner:{x}', th, p, round(s, 2), round(e, 2), None))
    return out


def cached_measure(name, path, s, e, crop=None):
    os.makedirs(CACHE, exist_ok=True)
    key = f'{os.path.basename(path)}_{os.path.getsize(path)}_{s:.2f}_{e:.2f}_{bool(crop)}'.replace(':', '_')
    p = os.path.join(CACHE, key + '.json')
    if os.path.exists(p):
        return json.load(open(p, encoding='utf-8'))
    f = measure(path, s, e, crop=crop)
    json.dump(f, open(p, 'w', encoding='utf-8'))
    print(f'measured {name} {s:.1f}-{e:.1f}', file=sys.stderr, flush=True)
    return f


def build():
    rows = []
    for name, th, p, s, e, crop in ref_windows():
        rows.append({'name': name, 'theme': th, 'f': cached_measure(name, p, s, e, crop)})
    themes = {}
    for th in ('action', 'beauty', 'romance'):
        R = [r['f'] for r in rows if r['theme'] == th]
        if th == 'romance':                           # romance refs are few: add the beauty refs (soft + edited)
            R = R + [r['f'] for r in rows if r['theme'] == 'beauty']
        q = {}
        for k in FEATS:
            x = np.array([r.get(k, 0.0) for r in R], float)
            q[k] = {'q10': round(float(np.quantile(x, 0.1)), 4), 'q25': round(float(np.quantile(x, 0.25)), 4),
                    'q50': round(float(np.quantile(x, 0.5)), 4), 'q90': round(float(np.quantile(x, 0.9)), 4)}
        themes[th] = {'n': len(R), 'q': q}
    prof = {'built': __import__('time').strftime('%Y-%m-%d'), 'groups': GROUP_W, 'feats': FEATS, 'themes': themes,
            'refs': [{'name': r['name'], 'theme': r['theme']} for r in rows]}
    json.dump(prof, open(PROFILE_JSON, 'w', encoding='utf-8'), indent=1)
    return prof, rows


# ------------------------------------------------------------------ calibration

SRC = os.path.join(ROOT, 'output', 'ranking-proto', 'src')
POOL = os.path.join(ROOT, 'output', 'edit-pools', 'ranking', 'pool', 'clips')
STAGED = os.path.join(ROOT, 'output', 'edit-pools', '_staged', 'ranking', 'clips')
# (label, theme, path, s, e, expected 'pass' / 'fail' / '' (report only))
CAL_SET = [
    ('Toji @noctryx7 (owner: good)', 'action', os.path.join(POOL, 'r_lynJABr10N4_40660.mp4'), 1.5, 22.54, 'pass'),
    ('Gojo @XenozEdit (owner: good)', 'action', os.path.join(POOL, 'r_thMXb5r792s_27610.mp4'), 1.5, 21.1, 'pass'),
    ('Horimiya @karma7892 (owner: REJECTED)', 'romance', os.path.join(STAGED, 'r_OaYRHPJZiXg_1790.mp4'), 1.5, 19.96, 'fail'),
    ('old Yuji @Dr1zzy (owner: not eventful)', 'action', os.path.join(SRC, 'IryZxcTZgzE.mp4'), 36.5, 55.25, 'fail'),
    ('Yuji @DivineHotdog (manga, fallback)', 'action', os.path.join(POOL, 'r_mY7mgdslbdc_29000.mp4'), 1.5, 24.28, ''),
    ('raw Bluray fight footage pSOH-M3b_5w', 'action', os.path.join(SRC, 'pSOH-M3b_5w.mp4'), 7.96, 27.24, 'fail'),
    ('creditless Blue Box OP UXclUH-r9ag', 'romance', os.path.join(SRC, 'UXclUH-r9ag.mp4'), 41.68, 65.16, 'fail'),
    ('Black Flash raw MIowFAWsIcU', 'action', os.path.join(SRC, 'MIowFAWsIcU.mp4'), 1.83, 21.43, 'fail'),
]


def survey_set():
    """The 2026-10-25 deep-survey sources (their best windows) as report-only rows."""
    out = []
    seen = {os.path.basename(c[2]).split('.')[0] for c in CAL_SET}
    th = {'jjk_strongest': 'action', 'best_waifu': 'beauty', 'romance_peak': 'romance'}
    for f in ('deep-jjk_strongest.json', 'deep-best_waifu.json', 'deep-romance_peak.json'):
        p = os.path.join(REFS, 'ranking', 'survey', f)
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding='utf-8'))
        for R in d['ranks'].values():
            for t in R['top']:
                w = t.get('window') or {}
                if not w.get('s') or t['id'] in seen or not os.path.exists(t.get('path') or ''):
                    continue
                seen.add(t['id'])
                out.append((f'{R["name"]} {t["handle"]} {t["id"]} ({t["verdict"]} {t["why"][:20]})', th[d['topic']],
                            t['path'], w['s'], w['e'], ''))
    return out


def calibrate(with_survey=True):
    prof, rows = build()
    table = []
    for r in rows:
        sc, gr = score(r['f'], r['theme'], prof)
        table.append(('REF ' + r['name'], r['theme'], sc, gr, r['f'].get('manga_share'), 'pass'))
    for label, th, p, s, e, exp in CAL_SET + (survey_set() if with_survey else []):
        if not os.path.exists(p):
            table.append((label, th, None, {}, None, exp))
            continue
        f = cached_measure(label, p, s, e)
        sc, gr = score(f, th, prof)
        table.append((label, th, sc, gr, f.get('manga_share'), exp))
    return prof, table


def _ok(sc, gr, th):
    return sc >= REF_MIN[th] and gr.get('hand', 0) >= HAND_MIN and gr.get('effects', 0) >= EFFECTS_MIN


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['build', 'calibrate', 'score', 'fill'])
    ap.add_argument('--part', type=int, default=0)
    ap.add_argument('--parts', type=int, default=1)
    ap.add_argument('path', nargs='?')
    ap.add_argument('--s', type=float)
    ap.add_argument('--e', type=float)
    ap.add_argument('--theme', default='action')
    ap.add_argument('--json')
    a = ap.parse_args()
    try:
        import psutil
        psutil.Process().nice(psutil.IDLE_PRIORITY_CLASS)
    except Exception:  # noqa: BLE001
        pass
    if a.cmd == 'fill':                      # fill the measurement cache in parallel workers (calibrate reads it)
        jobs = [(n, p, s, e, c) for n, _, p, s, e, c in ref_windows()] +                [(lab, p, s, e, None) for lab, _, p, s, e, _ in CAL_SET + survey_set() if os.path.exists(p)]
        for k, (n, p, s, e, c) in enumerate(jobs):
            if k % a.parts == a.part:
                cached_measure(n, p, s, e, c)
        return
    if a.cmd == 'build':
        prof, _ = build()
        print(json.dumps(prof['themes'], indent=1))
    elif a.cmd == 'calibrate':
        prof, table = calibrate()
        for label, th, sc, gr, ms, exp in table:
            ok = sc is not None and _ok(sc, gr, th)
            flag = '' if not exp else ('OK' if ok == (exp == 'pass') else 'WRONG')
            print(f'{label[:62]:62s} {th:7s} {sc if sc is not None else "-":>5} '
                  f'{"PASS" if ok else "fail"} manga {ms if ms is not None else "-"} {exp:4s} {flag} {gr}')
        if a.json:
            json.dump(table, open(a.json, 'w', encoding='utf-8'), indent=1, default=str)
    else:
        f = measure(a.path, a.s, a.e)
        print(json.dumps(f), json.dumps(gate(f, a.theme)))


if __name__ == '__main__':
    main()
