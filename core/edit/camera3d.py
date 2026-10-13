#!/usr/bin/env python3
"""
camera3d: a true 3D camera for the edit engine - the picture on a plane (or on depth layers) seen by a virtual
camera with a focal length, orbit / swing (yaw, pitch), roll, dolly and lateral travel, rendered with ONE
perspective resample per layer and a perspective-correct motion blur along the 3D path.

Camera tuple (the engine's 4-tuple extended; a 4-tuple is still a valid camera and costs nothing):
    (lns, roll, dx, dy,  yaw, pitch, dolly, px, py)
    lns roll dx dy   the existing 2D camera (ln scale, roll deg, shift in canvas-width fractions)
    yaw pitch        deg: the scene turns about the PIVOT (the subject point on the focus plane) - on a flat plate a
                     card swinging in space (Y / X axis), on depth layers an orbit around the subject
    dolly            ln of the distance ratio: > 0 the camera travels toward the focus plane (a flat plate zooms,
                     depth layers spread: near layers grow faster than far ones); dolly with lns = -dolly is the
                     dolly zoom (vertigo) on layered plates
    px py            lateral camera travel in canvas-width fractions AT THE FOCUS PLANE: a flat plate does not move,
                     depth layers slide against each other (2.5D parallax); the subject stays where it is
Field of view: 40 deg horizontal (After Effects' default 50 mm camera) unless a shot / transition asks otherwise.

Depth (2.5D, OPT-IN only: plan "camera3d_depth": true or shot camera "depth": true): Depth Anything V2 small (Apache-2.0, ONNX, onnxruntime on the CPU, ~0.5 s per source drawing at
518 px on 2 threads), cached per source drawing (memory + <edit-cache>/depth/*.npz). The plate is split into 3 rigid
layers (far / mid / near: Otsu on the per-shot normalised disparity, thresholds smoothed over time), the far layer
is the whole plate with the nearer regions inpainted (Telea at 1/4 resolution, blurred) so a revealed edge shows
background, never a stretched copy of the subject - no per-pixel warping, so no rubber sheets. When the model is
missing, depth falls back to the IS-Net subject matte (matte.py) as a 2-layer plate, and then to a flat plate.

    python core/edit/camera3d.py --selftest        # geometry checks (pivot fixed, dolly = zoom on a plate, ...)
"""
import math
import os
import sys
import tempfile
import threading

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
HFOV = 40.0
NF = 9
ZERO9 = (0.0,) * NF
LNS, ROLL, DX, DY, YAW, PITCH, DOLLY, PX, PY = range(NF)


# ------------------------------------------------------------------ tuples

def ext(c):
    c = tuple(float(x) for x in c)
    return c + (0.0,) * (NF - len(c)) if len(c) < NF else c[:NF]


def is3d(c, eps=1e-6):
    return len(c) > 4 and any(abs(float(x)) > eps for x in c[4:NF])


def add(a, b):
    """Sum of two cameras; stays a 4-tuple when both are 2D (the engine's old path is untouched)."""
    if len(a) <= 4 and len(b) <= 4:
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2], a[3] + b[3])
    a, b = ext(a), ext(b)
    return tuple(x + y for x, y in zip(a, b))


def flat(c):
    return tuple(c[:4])


def needs_depth(c, eps=1e-4):
    """Layers only matter when the camera travels (parallax / dolly) or orbits."""
    return len(c) > 4 and (abs(c[PX]) > eps or abs(c[PY]) > eps or abs(c[DOLLY]) > eps or abs(c[YAW]) > eps
                           or abs(c[PITCH]) > eps)


# ------------------------------------------------------------------ geometry

def focal(cw, hfov=HFOV):
    return (cw / 2.0) / math.tan(math.radians(hfov) / 2)


def _R(yaw, pitch):
    y, p = math.radians(yaw), math.radians(pitch)
    Ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    return Ry @ Rx


def layer_h(canvas, pivot, c, rho=1.0, hfov=HFOV, keep_pivot=True, pz=0.0):
    """
    3x3 homography canvas -> canvas for a fronto-parallel layer at relative inverse depth rho (1 = the focus plane,
    > 1 nearer, < 1 farther) under the 3D part of camera c (yaw, pitch, dolly, px, py). The 2D part of c is the
    engine's affine and is applied before this.
    """
    cw, ch = canvas
    f = focal(cw, hfov)
    D = f
    K = np.array([[f, 0, cw / 2.0], [0, f, ch / 2.0], [0, 0, 1.0]])
    Ki = np.linalg.inv(K)
    yaw, pitch, dolly, px, py = c[YAW], c[PITCH], c[DOLLY], c[PX], c[PY]
    R = _R(yaw, pitch)
    P = np.array([(pivot[0] - cw / 2.0) / f * D, (pivot[1] - ch / 2.0) / f * D, D * (1 + pz)])   # pz: axis behind the plane (cube)
    T = np.array([-px * cw, -py * cw, -D * (1 - math.exp(-dolly))])
    t = (np.eye(3) - R) @ P + T
    n = np.array([0.0, 0.0, 1.0])

    def hz(Z):
        Z2 = Z + t[2]
        if Z2 < 0.12 * D:                       # a near layer never reaches the lens (clamp, no fold-over)
            t2 = t.copy()
            t2[2] = 0.12 * D - Z
            return K @ (R + np.outer(t2, n) / Z) @ Ki
        return K @ (R + np.outer(t, n) / Z) @ Ki

    H = hz(D / max(1e-3, rho))
    if keep_pivot:
        q = hz(D) @ np.array([pivot[0], pivot[1], 1.0])
        q = q[:2] / q[2]
        S = np.array([[1, 0, pivot[0] - q[0]], [0, 1, pivot[1] - q[1]], [0, 0, 1.0]])
        H = S @ H
    return H


def zoom_about(z, pivot):
    return np.array([[z, 0, pivot[0] * (1 - z)], [0, z, pivot[1] * (1 - z)], [0, 0, 1.0]])


def cover_zoom(Htot, src_size, canvas, pivot, zmax=2.5):
    """Smallest extra zoom about the pivot so the plate covers the whole canvas (no edge ever shows)."""
    sw, sh = src_size
    cw, ch = canvas
    corners = np.array([[0, 0], [cw, 0], [0, ch], [cw, ch], [cw / 2, 0], [cw / 2, ch], [0, ch / 2], [cw, ch / 2]], np.float64)

    def ok(z):
        H = zoom_about(z, pivot) @ Htot
        try:
            Hi = np.linalg.inv(H)
        except np.linalg.LinAlgError:
            return False
        p = np.c_[corners, np.ones(len(corners))] @ Hi.T
        if np.any(p[:, 2] <= 1e-9):
            return False
        x, y = p[:, 0] / p[:, 2], p[:, 1] / p[:, 2]
        tol = 0.5
        return bool(np.all((x >= -tol) & (x <= sw + tol) & (y >= -tol) & (y <= sh + tol)))

    if ok(1.0):
        return 1.0
    lo, hi = 1.0, zmax
    if not ok(hi):
        return hi
    for _ in range(18):
        mid = 0.5 * (lo + hi)
        if ok(mid):
            hi = mid
        else:
            lo = mid
    return hi


# ------------------------------------------------------------------ depth

_SESS = None
_SLOCK = threading.Lock()
_DCACHE = {}
_DMAX = 400


def _model_path():
    try:
        sys.path.insert(0, HERE)
        import matte
        d = matte.model_dir()
    except Exception:  # noqa: BLE001
        d = os.path.join(tempfile.gettempdir(), 'edit-cache', 'models')
    return os.path.join(d, 'depth-anything-v2-small.onnx')


URL = 'https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx'


def _session():
    global _SESS
    with _SLOCK:
        if _SESS is not None:
            return _SESS or None
        try:
            import onnxruntime as ort
            path = _model_path()
            if not os.path.exists(path) or os.path.getsize(path) < 1e7:
                if os.environ.get('EDIT_MODELS_OFFLINE'):
                    raise FileNotFoundError(path)
                import urllib.request
                os.makedirs(os.path.dirname(path), exist_ok=True)
                urllib.request.urlretrieve(URL, path + '.part')
                os.replace(path + '.part', path)
            so = ort.SessionOptions()
            so.intra_op_num_threads = int(os.environ.get('EDIT_DEPTH_THREADS', '2'))
            so.inter_op_num_threads = 1
            _SESS = ort.InferenceSession(path, so, providers=['CPUExecutionProvider'])
        except Exception as e:  # noqa: BLE001
            print('[camera3d] depth model unavailable: %s' % e, file=sys.stderr)
            _SESS = False
        return _SESS or None


def disparity(img, key=None, side=518):
    """Relative inverse depth (larger = nearer) of a BGR picture at <= side px (multiples of 14), float32, or None."""
    if key is not None and key in _DCACHE:
        return _DCACHE[key]
    s = _session()
    if s is None:
        return None
    h, w = img.shape[:2]
    k = side / float(max(w, h))
    W, H = max(14, int(round(w * k / 14)) * 14), max(14, int(round(h * k / 14)) * 14)
    x = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)[:, :, ::-1].astype(np.float32) / 255.0
    x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    d = s.run(None, {'pixel_values': x.transpose(2, 0, 1)[None].astype(np.float32)})[0][0].astype(np.float32)
    if key is not None:
        if len(_DCACHE) > _DMAX:
            _DCACHE.pop(next(iter(_DCACHE)))
        _DCACHE[key] = d
    return d


class ShotDepth:
    """Per-shot layering state: normalisation and thresholds smoothed over time so the layers never flicker."""

    def __init__(self, strength=0.45, n_layers=3):
        self.strength = float(strength)
        self.n = int(n_layers)
        self.lo = self.hi = None
        self.th = None
        self.fill_cache = {}

    def layers(self, img, key, pivot_src, max_shift_px):
        """
        [(picture, alpha or None, rho, bbox)] back to front, in the picture's own (work) pixel space; alpha is
        float32 0..1 at the picture's size, bbox (x0, y0, x1, y1) where alpha > 0 (None = whole plate).
        pivot_src: the subject point in work px (its depth is the focus plane).
        """
        d = disparity(img, key)
        if d is None:
            return None
        h, w = img.shape[:2]
        lo, hi = float(np.percentile(d, 3)), float(np.percentile(d, 97))
        if self.lo is None:
            self.lo, self.hi = lo, hi
        else:
            self.lo, self.hi = 0.7 * self.lo + 0.3 * lo, 0.7 * self.hi + 0.3 * hi
        dn = np.clip((d - self.lo) / max(1e-6, self.hi - self.lo), 0, 1)
        u8 = (dn * 255).astype(np.uint8)
        t1, _ = cv2.threshold(u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        low = u8[u8 < t1]
        t0 = cv2.threshold(low, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0] if low.size > 100 else t1 * 0.5
        th = np.array([t0, t1], float) / 255.0
        self.th = th if self.th is None else 0.75 * self.th + 0.25 * th
        th = self.th
        dh, dw = dn.shape
        pxs = int(np.clip(pivot_src[0] / w * dw, 0, dw - 1))
        pys = int(np.clip(pivot_src[1] / h * dh, 0, dh - 1))
        r = max(2, dw // 30)
        dfocus = float(np.median(dn[max(0, pys - r):pys + r + 1, max(0, pxs - r):pxs + r + 1]))
        band = 0.035
        near = np.clip((dn - th[1]) / band + 0.5, 0, 1).astype(np.float32)
        mid = (np.clip((dn - th[0]) / band + 0.5, 0, 1) * (1 - near)).astype(np.float32) if self.n >= 3 else np.zeros_like(near)
        rho = lambda m: 1.0 + self.strength * (m - dfocus)  # noqa: E731
        mf = float(dn[(near + mid) < 0.5].mean()) if ((near + mid) < 0.5).any() else th[0] * 0.5
        mm = float(dn[mid > 0.5].mean()) if (mid > 0.5).any() else (th[0] + th[1]) / 2
        mn = float(dn[near > 0.5].mean()) if (near > 0.5).any() else min(1.0, th[1] + 0.1)
        hole = ((near + mid) > 0.02).astype(np.uint8)
        k = int(max(1, round(max_shift_px / w * dw))) * 2 + 3
        hole = cv2.dilate(hole, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        out = [(self._fill(img, hole, key), None, rho(mf), None)]
        for m, rr, on in ((mid, rho(mm), self.n >= 3), (near, rho(mn), True)):
            if not on or float(m.mean()) < 0.004:
                continue
            ys, xs = np.nonzero(m > 0.01)
            sx, sy = w / float(dw), h / float(dh)
            box = (max(0, int((xs.min() - 1) * sx)), max(0, int((ys.min() - 1) * sy)),
                   min(w, int((xs.max() + 2) * sx)), min(h, int((ys.max() + 2) * sy)))
            a = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
            out.append((img, a, rr, box))
        return out

    def _fill(self, img, hole_small, key):
        """The far layer: the plate with everything nearer inpainted (1/8 resolution Telea, blurred, feathered in)."""
        if key is not None and key in self.fill_cache:
            return self.fill_cache[key]
        h, w = img.shape[:2]
        q = 8
        sm = cv2.resize(img, (max(8, w // q), max(8, h // q)), interpolation=cv2.INTER_AREA)
        hm = cv2.resize(hole_small, (sm.shape[1], sm.shape[0]), interpolation=cv2.INTER_NEAREST)
        fill = cv2.inpaint(sm, hm, 4, cv2.INPAINT_TELEA)
        fill = cv2.GaussianBlur(fill, (0, 0), 1.0)
        big = cv2.resize(fill, (w, h), interpolation=cv2.INTER_LINEAR)
        m = cv2.resize(cv2.GaussianBlur(hm.astype(np.float32), (0, 0), 0.8), (w, h), interpolation=cv2.INTER_LINEAR)
        out = cv2.blendLinear(big, img, m, 1.0 - m)
        if key is not None:
            if len(self.fill_cache) > 8:
                self.fill_cache.pop(next(iter(self.fill_cache)))
            self.fill_cache[key] = out
        return out


# ------------------------------------------------------------------ rendering

def _roi_warp(img, alpha, H, box, size):
    """Warp only the canvas rectangle a partial layer can reach (its alpha box mapped by H)."""
    cw, ch = size
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box
    pts = np.array([[x0, y0, 1], [x1, y0, 1], [x0, y1, 1], [x1, y1, 1]], np.float64) @ H.T
    if np.any(pts[:, 2] <= 1e-9):
        X0, Y0, X1, Y1 = 0, 0, cw, ch
    else:
        xs, ys = pts[:, 0] / pts[:, 2], pts[:, 1] / pts[:, 2]
        X0, Y0 = max(0, int(xs.min()) - 2), max(0, int(ys.min()) - 2)
        X1, Y1 = min(cw, int(math.ceil(xs.max())) + 2), min(ch, int(math.ceil(ys.max())) + 2)
        # a layer cut by the plate edge continues past it (reflected): its rectangle runs to the canvas edge
        if x0 <= 2:
            X0 = 0
        if y0 <= 2:
            Y0 = 0
        if x1 >= w - 2:
            X1 = cw
        if y1 >= h - 2:
            Y1 = ch
    if X1 - X0 < 2 or Y1 - Y0 < 2:
        return None
    T = np.array([[1, 0, -X0], [0, 1, -Y0], [0, 0, 1.0]]) @ H
    wsz = (X1 - X0, Y1 - Y0)
    w = cv2.warpPerspective(img, T, wsz, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
    # a layer that touches the plate edge (the ground, a character cut by the frame) continues past it: moving it
    # must never open a gap at the frame edge
    a = cv2.warpPerspective(alpha, T, wsz, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
    return (X0, Y0, X1, Y1), w, a


def compose(layers, Hs, size):
    """Back-to-front composite of warped layers (uint8, alpha blends only inside each layer's rectangle)."""
    base = None
    for (img, alpha, _rho, box), H in zip(layers, Hs):
        if alpha is None:
            base = cv2.warpPerspective(img, H, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
            continue
        r = _roi_warp(img, alpha, H, box or (0, 0, img.shape[1], img.shape[0]), size)
        if r is None:
            continue
        (X0, Y0, X1, Y1), w, a = r
        sub = base[Y0:Y1, X0:X1]
        base[Y0:Y1, X0:X1] = cv2.blendLinear(w, sub, a, 1.0 - a)
    return base


def parallax_px(c, canvas, pivot, spread=0.3):
    """How far (px) layers 0.7 and 1.3 would separate under camera c: 0 on a flat move."""
    if not needs_depth(c):
        return 0.0
    cw, ch = canvas
    p = np.array([[0, 0, 1], [cw, 0, 1], [0, ch, 1], [cw, ch, 1], [cw / 2, ch / 2, 1]], np.float64).T
    a = layer_h(canvas, pivot, c, 1 - spread) @ p
    b = layer_h(canvas, pivot, c, 1 + spread) @ p
    return float(np.abs(a[:2] / a[2] - b[:2] / b[2]).max())


def render(img, M, cams, canvas, pivot, depth=None, key=None, cover=True, hfov=HFOV):
    """
    The plate `img` (work px) seen by the 3D cameras `cams` = [(M_2x3, cam9), ...], one per shutter sample (their
    2D parts are the engine's affines M). depth: ShotDepth or None (flat plate). Returns the uint8 canvas.
    """
    cw, ch = canvas
    lay = None
    if depth is not None:
        shift = max(parallax_px(c, canvas, pivot) for _, c in cams)
        if shift > 1.0:
            # the biggest parallax in the shutter bounds the reveal the far layer must fill
            Mi = cv2.invertAffineTransform(cams[len(cams) // 2][0])
            piv_src = Mi @ np.array([pivot[0], pivot[1], 1.0])
            k = abs(float(Mi[0, 0])) + abs(float(Mi[0, 1]))                  # canvas px -> work px
            lay = depth.layers(img, key, piv_src, shift * k * depth.strength / 0.3 + 6)
    if lay is None:
        lay = [(img, None, 1.0, None)]
    acc = None
    for Mq, c in cams:
        M3 = np.vstack([Mq, [0, 0, 1.0]])
        Hs = [layer_h(canvas, pivot, c, L[2], hfov) @ M3 for L in lay]
        if cover:
            z = cover_zoom(Hs[0], (img.shape[1], img.shape[0]), canvas, pivot)
            if z > 1.0:
                Z = zoom_about(z, pivot)
                Hs = [Z @ H for H in Hs]
        fr = compose(lay, Hs, (cw, ch))
        if len(cams) == 1:
            return fr
        acc = fr.astype(np.uint16) if acc is None else acc + fr
    return (acc // len(cams)).astype(np.uint8)


def corner_travel(Ha, Hb, size):
    """Largest corner / centre displacement (px) between two homographies of the same plate."""
    w, h = size
    p = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1], [w / 2, h / 2, 1]], np.float64).T
    a, b = Ha @ p, Hb @ p
    a, b = a[:2] / a[2], b[:2] / b[2]
    return float(np.abs(a - b).max())


# ------------------------------------------------------------------ engine hook

MOVES3D = ('orbit', 'parallax', 'dolly_zoom', 'tilt', 'push_depth', 'swing')


def shot_move(c, base, u, e, t):
    """
    The shot camera's 3D move on top of its 2D camera `base` (a 4-tuple), or `base` itself when the shot has none.
    shot camera {"move3d": {"kind": orbit | parallax | dolly_zoom | tilt | push_depth | swing, "amount", "dir": +-1,
    "ease": "io" | "out" | "lin"}} with u = shot progress 0..1 and e = the style's eased progress:
      orbit       yaw = amount deg * (e - .5) around the subject (depth layers turn at their own depth)
      parallax    lateral travel px = amount (canvas widths at the focus plane) * (e - .5): layers slide
      dolly_zoom  dolly = amount * e, lns -= amount * e (the subject keeps its size, the world stretches)
      tilt        pitch = amount deg * (e - .5)
      push_depth  dolly = amount * e (near layers grow faster than far ones: a push INTO the picture)
      swing       yaw = amount deg * (1 - e) (a flat plate settling from a turned card)
    """
    m = c.get('move3d') if isinstance(c, dict) else None
    if not m:
        return base
    kind = m.get('kind', 'orbit')
    amt = float(m.get('amount', 0.0))
    d = float(m.get('dir', 1.0))
    ease = m.get('ease')
    if ease == 'lin':
        e = u
    elif ease == 'out':
        e = 1 - (1 - u) ** 2
    out = list(ext(base))
    if kind == 'orbit':
        out[YAW] += d * amt * (e - 0.5)
    elif kind == 'parallax':
        out[PX] += d * amt * (e - 0.5)
        out[PY] += float(m.get('amount_y', 0.0)) * (e - 0.5)
    elif kind == 'dolly_zoom':
        out[DOLLY] += amt * e
        out[LNS] -= amt * e
    elif kind == 'tilt':
        out[PITCH] += d * amt * (e - 0.5)
    elif kind == 'push_depth':
        out[DOLLY] += amt * e
    elif kind == 'swing':
        out[YAW] += d * amt * (1 - e)
    return tuple(out)


def shot_depth(eng, shot):
    """The shot's ShotDepth (created on first use), or None when the plan / shot switched layering off."""
    c = shot.get('_cam') or {}
    # OPT-IN (owner, 2026-09-26, after the lab's cut-out / stretched-face parallax demos): depth layering only when a
    # plan or shot asks for it. By default a 3D camera turns the picture as ONE plane - the perspective / plane swings
    # the references' homography fits show - on the real moving footage.
    want = c.get('depth', eng.plan.get('camera3d_depth', False))
    if want is False or os.environ.get('EDIT_NO_DEPTH'):
        return None
    if '_depth' not in shot:
        strength = float(want) if isinstance(want, (int, float)) and not isinstance(want, bool) else 0.45
        shot['_depth'] = ShotDepth(strength=strength)
    return shot['_depth']


def place(eng, shot, t, size, c, camf, layout, img, foot, panel=None):
    """engine.place() for a camera with 3D terms: one perspective resample per depth layer, 3D motion blur."""
    fps = eng.fps
    M = eng._matrix(shot, foot, size, flat(c), t, panel, layout)
    cam = shot.get('_cam') or {}
    focus = (panel or {}).get('focus') or cam.get('focus') or (foot.prep.focus[0], 0.45)
    ww, wh = foot.size
    pivot = M @ np.array([focus[0] * ww, focus[1] * wh, 1.0])
    pivot = (float(np.clip(pivot[0], 0, size[0])), float(np.clip(pivot[1], 0, size[1])))
    samples = [(M, ext(c))]
    thr = float(eng.style['camera'].get('motion_blur', 6.0))
    if camf is not None and thr < 1e6:
        dt = float(eng.style['camera'].get('shutter', 1.0)) / fps     # style shutter fraction (default: the full frame)
        ca, cb = camf(t - 0.49 * dt), camf(t + 0.49 * dt)
        Ma = eng._matrix(shot, foot, size, flat(ca), t, panel, layout)
        Mb = eng._matrix(shot, foot, size, flat(cb), t, panel, layout)
        Ha = layer_h(size, pivot, ext(ca)) @ np.vstack([Ma, [0, 0, 1]])
        Hb = layer_h(size, pivot, ext(cb)) @ np.vstack([Mb, [0, 0, 1]])
        move = corner_travel(Ha, Hb, foot.size) * (1080.0 / max(size))
        if move > thr:
            n = int(min(12, max(3, move / 4)))                       # render budget is hours now: smoother blur
            samples = []
            for q in range(n):
                cq = camf(t + (q / (n - 1) - 0.5) * 0.98 * dt)            # the camera itself, sampled along its 3D path
                samples.append((eng._matrix(shot, foot, size, flat(cq), t, panel, layout), ext(cq)))
    depth = shot_depth(eng, shot) if panel is None else None
    key = None
    if depth is not None:
        tm = (panel or shot)['_tm']
        i = foot.index(tm(t)) if not foot.is_image else 0
        key = (foot.path, int(foot.rep.get(i, i)))
    out = render(img, M, samples, size, pivot, depth=depth, key=key, cover=layout not in ('fit', 'letterbox', 'fit_black'))
    return out


# ------------------------------------------------------------------ plane transitions (both pictures as cards)

def card_h(canvas, pivot, yaw=0.0, pitch=0.0, roll=0.0, dolly=0.0, shift=(0.0, 0.0), pz=0.0, hfov=HFOV):
    """Homography of a full-canvas picture turned in space about an axis through the pivot (pz: axis depth behind
    the picture in units of the camera distance - 0 = a card / door, 0.5 * width / distance = a cube face)."""
    cw, ch = canvas
    c = ext((0, 0, 0, 0, yaw, pitch, dolly, 0, 0))
    H = layer_h(canvas, pivot, c, 1.0, hfov, keep_pivot=False, pz=pz)
    if roll:
        Rm = cv2.getRotationMatrix2D((pivot[0], pivot[1]), roll, 1.0)
        H = H @ np.vstack([Rm, [0, 0, 1]])
    if shift != (0.0, 0.0):
        H = np.array([[1, 0, shift[0] * cw], [0, 1, shift[1] * cw], [0, 0, 1.0]]) @ H
    return H


def facing(H, canvas):
    """True when the turned picture still shows its front (its corner order is not mirrored)."""
    cw, ch = canvas
    p = np.array([[0, 0, 1], [cw, 0, 1], [0, ch, 1]], np.float64) @ H.T
    if np.any(p[:, 2] <= 1e-9):
        return False
    q = p[:, :2] / p[:, 2:3]
    return float(np.cross(q[1] - q[0], q[2] - q[0])) > 0


def card(img, canvas, pivot, yaw=0.0, pitch=0.0, roll=0.0, dolly=0.0, shift=(0.0, 0.0), hfov=HFOV, pz=0.0, samples=None):
    """
    A full-canvas picture as a card in space (swing / flip / cube faces). samples: a list of parameter dicts over the
    frame's shutter (perspective-correct motion blur: the card itself is re-projected at each sample). Returns
    (picture uint8, coverage alpha float32 0..1, centre-sample homography).
    """
    cw, ch = canvas
    base = dict(yaw=yaw, pitch=pitch, roll=roll, dolly=dolly, shift=shift, pz=pz)
    lst = samples or [base]
    acc = None
    macc = None
    H0 = None
    ones = np.full((ch, cw), 1.0, np.float32)
    for k, prm in enumerate(lst):
        q = dict(base, **prm)
        H = card_h(canvas, pivot, q['yaw'], q['pitch'], q['roll'], q['dolly'], q['shift'], q['pz'], hfov)
        if k == len(lst) // 2:
            H0 = H
        if not facing(H, canvas):
            w = np.zeros((ch, cw, 3), np.uint8)
            m = np.zeros((ch, cw), np.float32)
        else:
            w = cv2.warpPerspective(img, H, (cw, ch), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            m = cv2.warpPerspective(ones, H, (cw, ch), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        if len(lst) == 1:
            return w, m, H
        acc = w.astype(np.float32) if acc is None else acc + w
        macc = m if macc is None else macc + m
    n = float(len(lst))
    m = macc / n
    # premultiplied average: colour = sum(c * a) / sum(a) where covered
    col = acc / np.maximum(macc[..., None], 1e-3)
    return np.clip(col, 0, 255).astype(np.uint8), m, H0


def over(bg, fg, a):
    """fg over bg with alpha a (float 0..1)."""
    return cv2.blendLinear(fg, bg, a, 1.0 - a)


# ------------------------------------------------------------------ self test

def selftest():
    cv = (1080, 1920)
    piv = (540.0, 800.0)
    c0 = ext((0, 0, 0, 0, 0, 0, 0, 0, 0))
    assert np.allclose(layer_h(cv, piv, c0), np.eye(3), atol=1e-9)
    # dolly on the focus plane = a zoom about the pivot
    c = ext((0, 0, 0, 0, 0, 0, math.log(1.2), 0, 0))
    H = layer_h(cv, piv, c)
    assert np.allclose(H / H[2, 2], zoom_about(1.2, piv), atol=1e-6), H
    # lateral travel: the focus plane does not move, a near layer moves, a far one moves the other way
    c = ext((0, 0, 0, 0, 0, 0, 0, 0.05, 0))
    p = np.array([100.0, 300.0, 1.0])
    f0 = layer_h(cv, piv, c, 1.0) @ p
    fn = layer_h(cv, piv, c, 1.3) @ p
    ff = layer_h(cv, piv, c, 0.7) @ p
    assert abs(f0[0] / f0[2] - 100) < 1e-6
    assert (fn[0] / fn[2] - 100) * (ff[0] / ff[2] - 100) < 0
    # yaw keeps the pivot, makes a keystone (the two vertical edges get different heights)
    c = ext((0, 0, 0, 0, 20, 0, 0, 0, 0))
    H = layer_h(cv, piv, c)
    q = H @ np.array([piv[0], piv[1], 1.0])
    assert np.allclose(q[:2] / q[2], piv, atol=1e-6)
    tl, bl = H @ [0, 0, 1], H @ [0, 1920, 1]
    tr, br = H @ [1080, 0, 1], H @ [1080, 1920, 1]
    hl = bl[1] / bl[2] - tl[1] / tl[2]
    hr = br[1] / br[2] - tr[1] / tr[2]
    assert abs(hl - hr) > 50, (hl, hr)
    # cover zoom: a 20 deg swing needs > 1, no swing needs exactly 1
    z = cover_zoom(H, (1080, 1920), cv, piv)
    assert z > 1.01, z
    assert cover_zoom(np.eye(3), (1080, 1920), cv, piv) == 1.0
    print('camera3d selftest ok (pivot fixed, dolly = zoom on a plate, parallax sign, keystone, cover zoom %.3f)' % z)


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        selftest()
