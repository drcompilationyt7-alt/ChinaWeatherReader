#!/usr/bin/env python3
"""
Footage for the edit engine: one analysis pass per clip and a frame store that decodes exactly the
source ranges the plan needs, graded, straight into memory (no JPEG intermediates).

- analyze(path): text bands (burned-in subtitles / credits, trimmed like edit_fx.text_bands), mean
  luma (per-shot exposure), luma percentiles (auto-levels for the K-pop look), where the detail sits
  across the frame (the subject to crop around) and an optional subject-x track (dance follow crop).
- Footage(path, grade, crop, work size): frames at "work" resolution = the native crop, or scaled
  down (lanczos) when the output needs less. The engine then maps them onto the output with ONE
  warp (crop + scale + camera), so every output frame is resampled once.
- Anime holds each drawing for 2-3 frames: repeats are detected and in-between times are
  synthesised with DIS optical flow (both directions, at <= 640 px, flow grids cached), so slow
  motion shows a new frame every output frame. interp='native' shows the source frames as they are
  (sound edits), 'blend' cross-fades (cheap).
"""
import math
import os
import subprocess
import threading

import cv2
import numpy as np
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gpu as _gpu  # noqa: E402  NVDEC decode of big sources when usable (no-op on CI)

IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')


def probe(path):
    """(width, height, fps, duration, has_audio) of a video (or an image: fps 1, duration 1e6)."""
    if path.lower().endswith(IMAGE_EXT):
        img = cv2.imread(path)
        h, w = img.shape[:2] if img is not None else (720, 1280)
        return w, h, 1.0, 1e6, False
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,width,height,r_frame_rate,avg_frame_rate',
                        '-show_entries', 'format=duration', '-of', 'json', path], capture_output=True, text=True)
    import json
    try:
        d = json.loads(r.stdout or '{}')
    except ValueError:
        d = {}
    w, h, fps, audio = 1280, 720, 30.0, False
    for s in d.get('streams', []):
        if s.get('codec_type') == 'video' and s.get('width'):
            w, h = int(s['width']), int(s['height'])
            for key in ('r_frame_rate', 'avg_frame_rate'):
                try:
                    n, dd = s.get(key, '0/1').split('/')
                    v = float(n) / float(dd or 1)
                    if 1 <= v <= 240:
                        fps = v
                        break
                except (ValueError, ZeroDivisionError):
                    pass
        elif s.get('codec_type') == 'audio':
            audio = True
    try:
        dur = float(d.get('format', {}).get('duration') or 0) or 5.0
    except ValueError:
        dur = 5.0
    return w, h, fps, dur, audio


class Prep:
    """What the analysis pass learnt about one clip."""

    def __init__(self, path):
        self.path = path
        self.w, self.h, self.fps, self.duration, self.has_audio = probe(path)
        self.bands = (0.0, 0.0)
        self.mean = 0.42
        self.p02, self.p98 = 0.02, 0.98
        self.focus = (0.5, 0.42)
        self.track = None
        self.palette = None
        self.sat = None          # HSV saturation mean 0..1 (grade normalisation, styles.adapt_look)
        self.warm = None         # mean (R - B) / 255
        self.magenta = None      # mean ((R + B) / 2 - G) / 255

    def exposure(self, target=0.42):
        """(gamma for ffmpeg eq, bloom scale): bright shots come down toward the target, dark ones up a little."""
        m = min(0.95, max(0.05, self.mean))
        g = math.log(target) / math.log(m)
        g = min(1.45, max(0.8, g))
        bloom = min(1.0, max(0.35, (0.62 - m) / 0.22))
        return 1.0 / g, bloom


_PREP = {}


def analyze(path, want_track=False):
    """One small decode (4 fps, 320 px, colour) -> Prep. Cached per path."""
    key = (path, want_track)
    if key in _PREP:
        return _PREP[key]
    p = Prep(path)
    if path.lower().endswith(IMAGE_EXT):
        img = cv2.imread(path)
        if img is not None:
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
            p.mean = float(g.mean())
            p.p02, p.p98 = float(np.percentile(g, 2)), float(np.percentile(g, 98))
        _PREP[key] = p
        return p
    hh = int(round(320 * p.h / max(1, p.w) / 2) * 2)
    # ~40+ samples spread over the whole clip (credits / subtitles may appear anywhere in a long source)
    rate = 4.0 if p.duration <= 12 else max(0.5, 48.0 / p.duration)
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-an', '-vf', f'fps={rate},scale=320:{hh}', '-frames:v', '160',
                        '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'], capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8)
    n = a.size // (320 * hh * 3)
    if n >= 2:
        frames = a[:n * 320 * hh * 3].reshape(n, hh, 320, 3)
        gray = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]).astype(np.float32)
        p.bands = _text_bands(gray[::max(1, len(gray) // 48)])
        y0 = int(p.bands[0] * hh) if 0 < p.bands[0] <= 0.3 else 0
        y1 = hh - int(p.bands[1] * hh) if 0 < p.bands[1] <= 0.3 else hh
        body = gray[:, y0:max(y0 + 2, y1)] / 255
        p.mean = float(body.mean())
        p.p02 = float(np.percentile(body[::2, ::2, ::2], 2))
        p.p98 = float(np.percentile(body[::2, ::2, ::2], 98))
        p.focus = (_focus_x(body), 0.42)
        if want_track:
            p.track = _track_x(body, rate) if rate >= 2 else None
        mid = frames[len(frames) // 2]
        p.palette = mid
        try:
            sub = frames[::max(1, len(frames) // 16), y0:max(y0 + 2, y1):2, ::2]
            hsv = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2HSV) for f in sub]).astype(np.float32)
            bb, gg, rr = (sub[..., i].astype(np.float32) for i in range(3))
            p.sat = float(hsv[..., 1].mean() / 255.0)
            p.warm = float((rr - bb).mean() / 255.0)
            p.magenta = float(((rr + bb) / 2 - gg).mean() / 255.0)
        except Exception:
            pass
    _PREP[key] = p
    return p


def _text_bands(f):
    """Same detector as edit_fx.text_bands (subtitle / credit lines at the top and bottom), on 320 px gray frames."""
    if len(f) < 3:
        return 0.0, 0.0
    hh = f.shape[1]
    edges = np.abs(np.diff(f, axis=2))[:, :-1, :] + np.abs(np.diff(f, axis=1))[:, :, :-1]
    strong = (edges > 60) & (f[:, :-1, :-1] > 180)
    still = strong[1:] & strong[:-1] & (np.abs(f[1:, :-1, :-1] - f[:-1, :-1, :-1]) < 12)
    persist = still.mean(axis=0)
    on = persist > 0.3
    rise = np.diff(on.astype(np.int8), axis=1) > 0
    segs = rise.sum(axis=1)
    cover = on.sum(axis=1)
    letters = (segs >= 6) & (cover / np.maximum(segs, 1) < 7) & (cover > 0.02 * on.shape[1])
    dense = np.convolve(letters.astype(float), np.ones(3), mode='same') >= 2
    txt = np.where(dense)[0]
    bottom = txt[txt > hh * 0.55]
    top = txt[txt < hh * 0.40]
    b = float(min(0.45, (hh - bottom.min()) / hh + 0.02)) if len(bottom) else 0.0
    t = float(min(0.45, top.max() / hh + 0.03)) if len(top) else 0.0
    return t, b


def _focus_x(g):
    """Where the detail sits across the frame (0..1), pulled toward the centre."""
    col = (np.abs(np.diff(g, axis=2))[:, :-1, :] + np.abs(np.diff(g, axis=1))[:, :, :-1]).mean(axis=(0, 1))
    x = (np.arange(len(col)) + 0.5) / len(col)
    col = col * np.exp(-0.5 * ((x - 0.5) / 0.28) ** 2)
    k = max(1, int(round(0.32 * len(col))))
    win = np.convolve(col, np.ones(k), mode='valid')
    c = (int(np.argmax(win)) + k / 2) / len(col)
    return float(np.clip(0.5 + 0.8 * (c - 0.5), 0.0, 1.0))


def _track_x(g, rate):
    """Subject x over time from the motion centroid (for the dance follow crop): [(t, x)] at 2 Hz, smoothed."""
    if len(g) < 3:
        return None
    d = np.abs(np.diff(g, axis=0)).mean(axis=1)             # (n-1, w) motion per column
    x = (np.arange(d.shape[1]) + 0.5) / d.shape[1]
    cs = []
    for k in range(len(d)):
        wgt = d[k] ** 2
        cs.append(float((wgt * x).sum() / max(1e-6, wgt.sum())) if wgt.sum() > 1e-3 else 0.5)
    cs = np.array(cs)
    k = max(1, int(rate * 1.2))
    sm = np.convolve(np.pad(cs, (k, k), mode='edge'), np.ones(2 * k + 1) / (2 * k + 1), mode='valid')
    ts = (np.arange(len(sm)) + 1) / rate
    return [(float(t), float(v)) for t, v in zip(ts[::2], sm[::2])]


class Footage:
    """
    Graded frames of one clip at work resolution, decoded on demand by source frame index
    (ffmpeg input seeking is frame-exact on these files), kept in memory until released.
    """

    def __init__(self, path, vf_grade, crop, work_size, prep=None):
        self.path = path
        self.prep = prep or analyze(path)
        self.fps = self.prep.fps
        self.is_image = path.lower().endswith(IMAGE_EXT)
        self.n_total = 1 if self.is_image else max(1, int(round(self.prep.duration * self.fps)))
        self.crop = crop                    # (x, y, w, h) in source px
        self.size = work_size               # (w, h) of the stored frames
        self.vf_grade = vf_grade or ''
        self.frames = {}                    # source index -> BGR uint8
        self.rep = {}                       # source index -> index of its drawing (first frame of the hold)
        self.small = {}                     # drawing index -> small gray for duplicate / cut checks
        self.cut_after = set()
        self._flows = {}
        self._dis = None
        self._grid = None
        self.gate = False          # cinematic_slowmo round 10: forward-backward flow consistency gate in _flow_mix
        self._bad = {}
        self.decoded = 0
        self._lock = threading.RLock()            # the engine prefetches upcoming shots in a background thread

    # ------------------------------------------------------------ decoding
    def graph(self):
        """filter_complex: geometry (crop, lanczos scale) into [g0], then the grade fragment (which starts at [g0])."""
        x, y, w, h = self.crop
        geo = []
        if (x, y, w, h) != (0, 0, self.prep.w, self.prep.h):
            geo.append(f'crop={w}:{h}:{x}:{y}')
        if (w, h) != tuple(self.size):
            geo.append(f'scale={self.size[0]}:{self.size[1]}:flags=lanczos')
        g = f"[0:v]{','.join(geo) or 'null'}"
        if self.vf_grade:
            return g + '[g0];' + self.vf_grade
        return g

    def _hw(self):
        if getattr(self, '_hw_off', False):
            return []
        if not hasattr(self, '_hw_args'):
            try:
                self._hw_args = _gpu.hwdec(self.path, self.prep.w, self.prep.h)
            except Exception:
                self._hw_args = []
        return self._hw_args

    def _decode(self, i0, n):
        fw, fh = self.size
        cmd = ['ffmpeg', '-v', 'error']
        hw = [] if self.is_image else self._hw()      # NVDEC for >= 1080p sources (3x less CPU time); [] on CI / no GPU
        cmd0 = list(cmd)
        cmd += hw
        if not self.is_image and i0 > 0:
            cmd += ['-ss', f'{max(0.0, (i0 - 0.02) / self.fps):.6f}']
            cmd0 += ['-ss', f'{max(0.0, (i0 - 0.02) / self.fps):.6f}']
        tail = ['-i', self.path, '-an', '-filter_complex', self.graph(),
                '-frames:v', str(n), '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-']
        r = subprocess.run(cmd + tail, capture_output=True)
        a = np.frombuffer(r.stdout, np.uint8)
        k = a.size // (fw * fh * 3)
        if k == 0 and hw:                               # the GPU decode failed: the same call on the CPU
            self._hw_off = True
            r = subprocess.run(cmd0 + tail, capture_output=True)
            a = np.frombuffer(r.stdout, np.uint8)
            k = a.size // (fw * fh * 3)
        if k == 0:
            if r.stderr:
                print('[footage] ffmpeg:', r.stderr.decode(errors='ignore')[-300:], flush=True)
            return []
        self.decoded += k
        return list(a[:k * fw * fh * 3].reshape(k, fh, fw, 3))

    def ensure(self, i0, i1):
        """Decode source frames [i0, i1) that are not in memory (one ffmpeg call per missing run)."""
        with self._lock:
            self._ensure(i0, i1)

    def _ensure(self, i0, i1):
        i0 = max(0, int(i0))
        i1 = min(self.n_total, int(i1))
        if self.is_image:
            i0, i1 = 0, 1
        idx = i0
        while idx < i1:
            if idx in self.frames:
                idx += 1
                continue
            j = idx
            # read ahead: one ffmpeg call per ~1.5 s of source instead of one per frame
            limit = min(self.n_total, max(i1, idx + max(24, int(self.fps * 1.5))))
            while j < limit and j not in self.frames:
                j += 1
            got = self._decode(idx, j - idx)
            if not got:
                # past the real end of the stream: repeat the last frame we have
                last = max(self.frames) if self.frames else None
                for q in range(idx, j):
                    if last is not None:
                        self.frames[q] = self.frames[last]
                        self.rep[q] = self.rep.get(last, last)
                    else:
                        self.frames[q] = np.zeros((self.size[1], self.size[0], 3), np.uint8)
                        self.rep[q] = q
                idx = j
                continue
            for q, fr in enumerate(got):
                self._add(idx + q, fr)
            for q in range(idx + len(got), j):          # stream shorter than probed
                self._add(q, got[-1], dup=True)
            idx = j
        if self.is_image:
            self.n_total = 1

    def _add(self, i, fr, dup=False):
        self.frames[i] = fr
        if dup:
            self.rep[i] = self.rep.get(i - 1, i)
            return
        g = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (96, max(2, int(96 * fr.shape[0] / fr.shape[1]))),
                       interpolation=cv2.INTER_AREA).astype(np.int16)
        prev = self.rep.get(i - 1)
        if prev is not None and prev in self.small:
            d = float(np.abs(g - self.small[prev]).mean())
            if d < 1.0:
                self.rep[i] = prev
                return
            if d > 38:
                self.cut_after.add(prev)
        self.rep[i] = i
        self.small[i] = g

    def release(self, keep):
        """Drop frames outside the [i0, i1) ranges still needed."""
        if self.is_image:
            return
        with self._lock:
            self._release(keep)

    def _release(self, keep):
        drop = [i for i in self.frames if not any(a - 2 <= i < b + 2 for a, b in keep)]
        for i in drop:
            self.frames.pop(i, None)
            self.rep.pop(i, None)
            self.small.pop(i, None)
        # a kept frame whose drawing (the first frame of its hold) was dropped becomes its own drawing again: the
        # same clip used by two shots far apart released the hold's first frame and frame() hit a KeyError
        for i, a in list(self.rep.items()):
            if a not in self.frames:
                self.rep[i] = i
                if i not in self.small and i in self.frames:
                    fr = self.frames[i]
                    self.small[i] = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (96, max(2, int(96 * fr.shape[0] / fr.shape[1]))),
                                               interpolation=cv2.INTER_AREA).astype(np.int16)
        for k in [k for k in self._flows if k[0] not in self.frames]:
            self._flows.pop(k, None)

    # ------------------------------------------------------------ frames
    def index(self, t):
        return int(math.floor(max(0.0, t) * self.fps + 1e-6))

    def native(self, t):
        if self.is_image:
            self.ensure(0, 1)
            return self.frames[0]
        i = min(self.n_total - 1, self.index(t))
        self.ensure(i, i + 1)
        return self.frames[i]

    def _next_drawing(self, a, limit=10):
        """First source index after drawing a that shows a different drawing (decoding ahead if needed)."""
        j = a + 1
        end = min(self.n_total, a + 1 + limit)
        while j < end:
            if j not in self.frames:
                self.ensure(j, min(self.n_total, j + 8))
            if j not in self.rep:
                return None
            if self.rep[j] == j:
                return j
            j += 1
        return None

    def frame(self, t, interp='flow'):
        """The picture at clip time t (s): source drawing, a flow-synthesised in-between, or a cross-fade."""
        if self.is_image or interp == 'native':
            return self.native(t)
        i = min(self.n_total - 1, self.index(t))
        self.ensure(i, i + 2)
        a = self.rep.get(i, i)
        b = self._next_drawing(a)
        if b is None:
            return self.frames[a]
        ta, tb = a / self.fps, b / self.fps
        x = (t - ta) / max(1e-6, tb - ta)
        if x < 0.04:
            return self.frames[a]
        if x > 0.96:
            return self.frames[b]
        A, B = self.frames[a], self.frames[b]
        if a in self.cut_after or (b - 1) in self.cut_after:
            return A if x < 0.5 else B
        if interp == 'blend':
            return cv2.addWeighted(A, 1 - x, B, x, 0)
        return self._flow_mix(a, b, A, B, x)

    def _flow(self, a, b, A, B):
        key = (a, b)
        if key not in self._flows:
            if self._dis is None:
                self._dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
            h, w = A.shape[:2]
            f = min(1.0, 512.0 / max(w, h))
            sw, sh = max(8, int(w * f)), max(8, int(h * f))
            ga = cv2.cvtColor(cv2.resize(A, (sw, sh), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            gb = cv2.cvtColor(cv2.resize(B, (sw, sh), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            fab = self._dis.calc(ga, gb, None)
            fba = self._dis.calc(gb, ga, None)
            if len(self._flows) > 6:
                self._flows.pop(next(iter(self._flows)))
            # upsampled once per drawing pair (slow motion reuses a pair for many output frames)
            fab = cv2.resize(fab, (w, h), interpolation=cv2.INTER_LINEAR) * np.float32(1.0 / f)
            fba = cv2.resize(fba, (w, h), interpolation=cv2.INTER_LINEAR) * np.float32(1.0 / f)
            self._flows[key] = (fab, fba, 1.0)
        return self._flows[key]

    def _flow_mix(self, a, b, A, B, x):
        fab, fba, up = self._flow(a, b, A, B)
        h, w = A.shape[:2]
        if self._grid is None or self._grid.shape[:2] != (h, w):
            gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
            self._grid = np.dstack([gx, gy])
        grid = self._grid
        fa, fb = fab, fba
        ma = cv2.scaleAdd(fa, float(-x * up), grid)          # 2-channel maps in one C pass
        mb = cv2.scaleAdd(fb, float(-(1 - x) * up), grid)
        wa = cv2.remap(A, ma, None, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        wb = cv2.remap(B, mb, None, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        blend = cv2.addWeighted(wa, 1 - x, wb, x, 0)
        # where the two warps disagree (fast limbs, occlusions) the flow failed: take the nearer warp
        sw, sh = max(8, w // 4), max(8, h // 4)
        diff = cv2.absdiff(cv2.resize(wa, (sw, sh), interpolation=cv2.INTER_AREA),
                           cv2.resize(wb, (sw, sh), interpolation=cv2.INTER_AREA)).max(axis=2)
        m = cv2.GaussianBlur((diff > 38).astype(np.float32), (0, 0), 1.2)
        if self.gate:
            # round 10 (critics: doubled hair strands, a mouth that morphs between frames): where the forward and backward
            # flows disagree (|f_ab(p) + f_ba(p + f_ab(p))| over 0.75 px at quarter size and 12 % of the flow) the warp is
            # not trusted either: those pixels take the nearer source warp instead of the blend of two (ghost) warps
            bad = self._bad.get((a, b))
            if bad is None or bad.shape != m.shape:
                qa_, qb_ = cv2.resize(fab, (sw, sh), interpolation=cv2.INTER_AREA) * (sw / float(w)),                     cv2.resize(fba, (sw, sh), interpolation=cv2.INTER_AREA) * (sw / float(w))
                gq = np.dstack(np.meshgrid(np.arange(sw, dtype=np.float32), np.arange(sh, dtype=np.float32)))
                back = cv2.remap(qb_, gq[..., 0] + qa_[..., 0], gq[..., 1] + qa_[..., 1], cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REPLICATE)
                err = np.linalg.norm(qa_ + back, axis=2)
                mag = np.linalg.norm(qa_, axis=2)
                bad = cv2.GaussianBlur((err > np.maximum(0.75, 0.12 * mag)).astype(np.float32), (0, 0), 1.5)
                if len(self._bad) > 6:
                    self._bad.pop(next(iter(self._bad)))
                self._bad[(a, b)] = bad
            m = np.maximum(m, np.minimum(1.0, 1.6 * bad))
        if float(m.mean()) < 0.002:
            return blend
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
        near = wa if x < 0.5 else wb
        return cv2.blendLinear(blend, near, 1 - m, m)


_AUDIO = {}


def clip_audio(path, sr=44100):
    """The clip's own sound as float64 (2, n) at sr, or None (cached)."""
    if path in _AUDIO:
        return _AUDIO[path]
    if path.lower().endswith(IMAGE_EXT):
        _AUDIO[path] = None
        return None
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-vn', '-ac', '2', '-ar', str(sr), '-f', 'f32le', '-'],
                       capture_output=True)
    a = np.frombuffer(r.stdout, dtype=np.float32)
    out = a.reshape(-1, 2).T.astype(np.float64) if a.size > sr // 5 else None
    _AUDIO[path] = out
    return out


def file_exists(path):
    return bool(path) and os.path.exists(path)
