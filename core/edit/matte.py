"""
Subject mattes from a real segmentation model (the GrabCut in layouts.subject_matte flickered and returned None
on most frames - critic: text_behind_subject 4/10, cutout_prereveal 3/10).

Models (ONNX, run with onnxruntime on the CPU, both IS-Net, input 1024x1024, one sigmoid map out):
    anime    skytnt/anime-seg  isnetis.onnx            (Apache-2.0)   anime / illustration characters
    general  rembg             isnet-general-use.onnx  (MIT / Apache) live action: K-pop, dance practice rooms

They live in $EDIT_MODELS or <tempdir>/edit-cache/models and are downloaded once when missing (Hugging Face /
GitHub releases - not YouTube, so the paced download lock does not apply). `pip install onnxruntime` is the only
dependency; when it or the model is missing every function returns None and callers fall back to GrabCut.

    segment(img, kind='auto', size=640) -> float32 mask (h, w) in 0..1, or None
    Temporal(kind, ema=0.6)             -> .update(img, key) a per-overlay matte smoothed over time (EMA 0.6,
                                           2 px feather), evaluated at most once per 1/15 s
    sticker_outline(mask, width_px, jag) -> the jagged white sticker ring around a cutout (kpop mFHp6Dd2ISk)

CLI:  python core/edit/matte.py IMAGE [--kind anime|general|auto] [--out mask.png]
Timing (640 px input): ~0.35 s per matte on 4 threads, ~0.6 s on 2.
"""
import os
import sys
import tempfile
import threading
import urllib.request

import cv2
import numpy as np

MODELS = {
    'anime': ('isnetis.onnx', 'https://huggingface.co/skytnt/anime-seg/resolve/main/isnetis.onnx'),
    'general': ('isnet-general-use.onnx', 'https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx'),
}
_SESS = {}
_LOCK = threading.Lock()


def model_dir():
    return os.environ.get('EDIT_MODELS') or os.path.join(tempfile.gettempdir(), 'edit-cache', 'models')


def _session(kind):
    with _LOCK:
        if kind in _SESS:
            return _SESS[kind]
        sess = None
        try:
            import onnxruntime as ort
            name, url = MODELS[kind]
            path = os.path.join(model_dir(), name)
            if not os.path.exists(path) or os.path.getsize(path) < 1e6:
                if os.environ.get('EDIT_MODELS_OFFLINE'):
                    raise FileNotFoundError(path)
                os.makedirs(model_dir(), exist_ok=True)
                tmp = path + '.part'
                urllib.request.urlretrieve(url, tmp)
                os.replace(tmp, path)
            so = ort.SessionOptions()
            so.intra_op_num_threads = int(os.environ.get('EDIT_MATTE_THREADS', '2'))
            so.inter_op_num_threads = 1
            sess = ort.InferenceSession(path, sess_options=so, providers=['CPUExecutionProvider'])
        except Exception as e:  # noqa: BLE001
            sys.stderr.write('matte: %s model unavailable (%s) - falling back to GrabCut\n' % (kind, str(e)[:120]))
        _SESS[kind] = sess
        return sess


def looks_anime(img):
    """Cel animation has hard ink lines: the 90th-percentile Sobel magnitude at 256 px is 190-230 on anime plates
    and 50-130 on live action (measured on six plates of both kinds)."""
    g = cv2.cvtColor(cv2.resize(img, (256, 144), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)
    mag = np.sqrt(gx * gx + gy * gy)
    return float(np.percentile(mag, 90)) > 160.0


def _run(sess, img, size):
    h, w = img.shape[:2]
    x = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)[..., ::-1].astype(np.float32) / 255.0
    inp = sess.get_inputs()[0]
    if 'general' in (sess._model_path or ''):
        x = (x - 0.5) / 1.0
    x = np.transpose(x, (2, 0, 1))[None]
    y = sess.run(None, {inp.name: x})[0]
    m = np.squeeze(y).astype(np.float32)
    lo, hi = float(m.min()), float(m.max())
    if hi - lo > 1e-6 and (lo < 0.0 or hi > 1.0):
        m = (m - lo) / (hi - lo)
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)


def segment(img, kind='auto', size=640, feather=2.0):
    """Float mask of the subject (0..1, feathered `feather` px at 1080 wide), or None when no model can run."""
    if kind == 'auto':
        kind = 'anime' if looks_anime(img) else 'general'
    sess = _session(kind)
    if sess is None:
        return None
    try:
        m = _run(sess, img, int(size) // 32 * 32)
    except Exception as e:  # noqa: BLE001  (fixed-size exports: retry at the model's own 1024)
        try:
            m = _run(sess, img, 1024)
        except Exception:
            sys.stderr.write('matte: inference failed (%s)\n' % str(e)[:120])
            return None
    m = np.clip((m - 0.35) / 0.4, 0, 1)
    if feather > 0:
        m = cv2.GaussianBlur(m, (0, 0), max(0.6, feather * img.shape[1] / 1080.0))
    cover = float(m.mean())
    if cover < 0.02 or cover > 0.92:
        return None
    return m


class Temporal:
    """A matte that follows a moving shot without flicker: EMA 0.6 of the model output, at most one run per 1/15 s."""

    def __init__(self, kind='auto', ema=0.6, size=512):
        self.kind, self.ema, self.size = kind, float(ema), size
        self.m, self.key = None, None

    def update(self, img, t):
        key = int(round(t * 15.0))            # 15 Hz + EMA: ~1.5 s per run on 2 threads keeps a 1 s title under a minute
        if key == self.key and self.m is not None:
            return self.m
        if self.kind == 'auto':
            self.kind = 'anime' if looks_anime(img) else 'general'
        m = segment(img, self.kind, self.size)
        if m is None:
            return self.m
        if self.m is None or self.key is None or abs(key - self.key) > 3 or self.m.shape != m.shape:
            self.m = m
        else:
            self.m = self.ema * m + (1 - self.ema) * self.m
        self.key = key
        return self.m


def sticker_outline(mask, width_px, jag=0.35, seed=0):
    """
    kpop.json sticker_cutout_text: the cutout wears a WHITE ring of ~0.8 %W whose outer edge is hand-cut - jagged,
    straight-ish scissor facets rather than a smooth dilation. Returns the float ring mask INCLUDING the subject.
    """
    h, w = mask.shape[:2]
    hard = (mask > 0.5).astype(np.uint8)
    k = max(1, int(round(width_px)))
    ring = cv2.dilate(hard, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    cs, _ = cv2.findContours(ring, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out = np.zeros((h, w), np.uint8)
    rng = np.random.default_rng(seed)
    for c in cs:
        if cv2.contourArea(c) < 0.002 * w * h:
            continue
        step = max(3, int(width_px * 2.2))
        pts = c[::step, 0, :].astype(np.float32)
        if len(pts) < 4:
            continue
        ctr = pts.mean(0)
        d = pts - ctr
        n = d / np.maximum(1e-3, np.linalg.norm(d, axis=1, keepdims=True))
        pts = pts + n * (rng.uniform(0.0, jag, (len(pts), 1)).astype(np.float32) * width_px * 2.0)
        cv2.fillPoly(out, [np.round(pts).astype(np.int32)], 255, cv2.LINE_AA)
    return np.maximum(out.astype(np.float32) / 255.0, mask)


if __name__ == '__main__':
    import argparse
    import time
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('--kind', default='auto')
    ap.add_argument('--out', default='')
    a = ap.parse_args()
    im = cv2.imread(a.image)
    t0 = time.time()
    mm = segment(im, a.kind)
    t1 = time.time()
    mm = segment(im, a.kind)
    print('anime' if looks_anime(im) else 'general', 'first %.2f s, warm %.2f s' % (t1 - t0, time.time() - t1),
          None if mm is None else round(float(mm.mean()), 3))
    if a.out and mm is not None:
        ring = sticker_outline(mm, 0.008 * im.shape[1])
        vis = im.astype(np.float32) * mm[..., None] + 255.0 * (ring - mm).clip(0, 1)[..., None] + 40 * (1 - ring[..., None])
        cv2.imwrite(a.out, np.clip(vis, 0, 255).astype(np.uint8))
