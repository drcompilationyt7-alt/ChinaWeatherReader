#!/usr/bin/env python3
"""
Effects for the drop edit (render_meme.py Edit): what makes a beat-cut
montage feel like an After Effects "edit" instead of a slideshow.

- prepare_shot(): one ffmpeg pass per shot: trims burned-in text bands,
  lays the shot out (full-width "letterbox", native sharpness; or "fill"
  around the action), sharpens (CAS), grades (S-curve, split toning,
  vibrance) and adds bloom to the highlights. Frames are kept at the
  source rate; ShotFrames blends neighbours for smooth speed ramps.
- Camera: sub-pixel affine moves at 60 fps: a slow push-in per shot,
  beat punches with overshoot, a decaying shake on phrase starts, whips;
  rendered with motion blur (several sub-frame warps averaged) whenever the
  camera moves fast.
- transitions: whip (directional blur slide), zoom blur (radial), flash
  with a light leak, dip to black.
- Overlays: an animated two-colour gradient from the anime's own palette
  (soft light), film grain, vignette, a glowing title card.

Everything is numpy + OpenCV on the CPU (GitHub Actions has no GPU).
"""
import glob
import math
import os
import subprocess
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
LOOKS = {
    # S-curve + split toning (cool shadows, warm highlights) + vibrance; bloom opacity; sharpening
    'cinematic': dict(curve="0/0 0.10/0.03 0.35/0.26 0.7/0.74 0.92/0.97 1/1", sat=1.42, gamma=0.93,
                      balance='rs=-0.04:gs=-0.01:bs=0.09:rh=0.07:gh=0.02:bh=-0.06', bloom=0.45, bloom_at=0.74, cas=0.6),
    'dreamy': dict(curve="0/0.03 0.25/0.22 0.6/0.66 0.9/0.96 1/1", sat=1.32, gamma=0.98,
                   balance='rs=0.04:bs=0.06:rm=0.04:bm=-0.02:rh=0.07:bh=-0.03', bloom=0.6, bloom_at=0.66, cas=0.45),
    'hype': dict(curve="0/0 0.15/0.04 0.5/0.5 0.85/0.95 1/1", sat=1.6, gamma=0.88,
                 balance='rs=-0.05:bs=0.09:rh=0.07:bh=-0.07', bloom=0.3, bloom_at=0.82, cas=0.75),
}
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'quiz', 'assets', 'fonts')


# ---------------------------------------------------------------- shot preparation

def probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=width,height,r_frame_rate', '-of', 'csv=p=0', path], capture_output=True, text=True)
    try:
        w, h, rate = r.stdout.strip().split(',')[:3]
        n, d = rate.split('/')
        return int(w), int(h), float(n) / float(d or 1)
    except Exception:
        return 1280, 720, 30.0


def text_bands(path, samples=8):
    """
    Burned-in text at the top and the bottom of the frame (subtitles, credits, Shorts captions like
    "POV: ..."): (top, bottom) fractions of the height to cut away, 0 where there is none. Text is
    found as sharp, bright, outlined strokes that hold still between neighbouring samples while the
    picture moves (lyric lines change every few seconds).
    """
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-an', '-vf', f'fps=4,scale=320:-2,format=gray', '-frames:v', str(samples * 2),
                        '-f', 'rawvideo', '-'], capture_output=True)
    w, h, _ = probe(path)
    hh = int(round(320 * h / w / 2) * 2)
    a = np.frombuffer(r.stdout, np.uint8)
    if a.size < 320 * hh * 3:
        return 0.0, 0.0
    f = a[:a.size // (320 * hh) * 320 * hh].reshape(-1, hh, 320).astype(np.float32)
    edges = np.abs(np.diff(f, axis=2))[:, :-1, :] + np.abs(np.diff(f, axis=1))[:, :, :-1]
    strong = (edges > 60) & (f[:, :-1, :-1] > 180)          # bright glyph strokes (subtitles, logos)
    still = strong[1:] & strong[:-1] & (np.abs(f[1:, :-1, :-1] - f[:-1, :-1, :-1]) < 12)
    persist = still.mean(axis=0)
    on = persist > 0.3
    # letters: a row of text is many short bright strokes; window frames, lamps and sun rays are a few
    # long edges. Count stroke segments and their mean length per row.
    rise = np.diff(on.astype(np.int8), axis=1) > 0
    segs = rise.sum(axis=1)
    cover = on.sum(axis=1)
    letters = (segs >= 6) & (cover / np.maximum(segs, 1) < 7) & (cover > 0.02 * on.shape[1])
    # and a line of text is several consecutive such rows
    dense = np.convolve(letters.astype(float), np.ones(3), mode='same') >= 2
    txt = np.where(dense)[0]
    bottom = txt[txt > hh * 0.55]
    top = txt[txt < hh * 0.40]
    b = float(min(0.45, (hh - bottom.min()) / hh + 0.02)) if len(bottom) else 0.0
    t = float(min(0.45, top.max() / hh + 0.03)) if len(top) else 0.0
    return t, b


def text_band(path, samples=8):
    """Bottom burned-in text band only (kept for callers that trim the bottom)."""
    return text_bands(path, samples)[1]


def exposure(path, target=0.42):
    """
    Per-shot exposure: (gamma for ffmpeg eq, bloom scale). Bright shots (a pink stage, a white
    sky) come down toward the target mean so the grade and bloom don't turn them milky;
    dark ones are lifted a little. Returns (1.0, 1.0) if the shot can't be read.
    """
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-an', '-vf', 'fps=2,scale=160:-2,format=gray',
                        '-f', 'rawvideo', '-'], capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8)
    if a.size < 1000:
        return 1.0, 1.0
    m = float(a.mean()) / 255
    m = min(0.95, max(0.05, m))
    g = math.log(target) / math.log(m)           # out = in ** g maps the mean onto the target
    g = min(1.45, max(0.8, g))
    bloom = min(1.0, max(0.35, (0.62 - m) / 0.22))  # less glow on already-bright shots
    return 1.0 / g, bloom                          # eq's gamma brightens above 1


def prepare_shot(clip, workdir, layout='letterbox', look='cinematic', cx=0.5):
    """Grade, sharpen and lay out one shot; returns ShotFrames."""
    w, h, fps = probe(clip['path'])
    lk = LOOKS.get(look, LOOKS['cinematic'])
    ex_gamma, bloom_k = exposure(clip['path'])
    x0, y0, x1, y1 = clip.get('content_box') or [0, 0, 1, 1]
    top, band = text_bands(clip['path'])
    y1 = min(y1, 1 - band) if 0 < band <= 0.3 else y1   # bigger "bands" are false alarms: never crop half
    y0 = max(y0, top) if 0 < top <= 0.3 else y0
    crop = f'crop=iw*{x1 - x0:.4f}:ih*{y1 - y0:.4f}:iw*{x0:.4f}:ih*{y0:.4f},' if (x1 - x0) * (y1 - y0) < 0.995 else ''
    aspect = (w * (x1 - x0)) / max(1, h * (y1 - y0))
    if layout == 'fill' or aspect < 1.0:  # vertical sources always fill
        geo = (f'scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,'
               f'crop={W}:{H}:max(0\\,min(iw-{W}\\,{cx:.3f}*iw-{W // 2})):(ih-{H})/2')
        size = (W, H)
    else:
        # full width, near-native sharpness: a 16:9 shot is zoomed 1.22 (1080 x 740 visible) to lose the edges
        sh = int(round(W / aspect * 1.22 / 2) * 2)
        sw = int(round(W * 1.22 / 2) * 2)
        geo = f'scale={sw}:{sh}:flags=lanczos,crop={W}:{sh}'
        size = (W, sh)
    vf = (f'[0:v]{crop}{geo},eq=gamma={ex_gamma:.3f},cas={lk["cas"]},curves=all=\'{lk["curve"]}\','
          f'eq=saturation={lk["sat"]}:gamma={lk["gamma"]},colorbalance={lk["balance"]},split[a][b];'
          f'[b]curves=all=\'0/0 {lk["bloom_at"]}/0 1/1\',gblur=sigma=24[g];'
          f'[a][g]blend=all_mode=screen:all_opacity={lk["bloom"] * bloom_k:.3f}')
    d = tempfile.mkdtemp(prefix='shot-', dir=workdir)
    subprocess.run(['ffmpeg', '-v', 'error', '-i', clip['path'], '-an', '-filter_complex', vf, '-q:v', '2',
                    os.path.join(d, '%05d.jpg')], check=False)
    return ShotFrames(sorted(glob.glob(os.path.join(d, '*.jpg'))), fps, size, band)


class ShotFrames:
    """
    Frames of a prepared shot at any time t. Anime holds each drawing for 2-3
    frames, so repeats are dropped first (keeping their real times); in-between
    moments are synthesised with DIS optical flow (both directions, half
    resolution), which is smooth slow motion without the ghosting of a plain
    cross-fade. Across a scene cut inside the clip there is no flow: the nearer
    frame is shown.
    """

    def __init__(self, paths, fps, size, text_band=0.0):
        self.fps, self.size, self.text_band = fps, size, text_band
        self.all_paths = paths
        self._cache, self._flows = {}, {}
        self._dis = None
        # unique drawings: drop frames (almost) identical to the previous kept one
        self.paths, self.times, self.cut_after = [], [], set()
        prev = None
        for k, p in enumerate(paths):
            g = cv2.imread(p, cv2.IMREAD_REDUCED_GRAYSCALE_4)
            if g is None:
                continue
            if prev is not None:
                d = float(np.abs(g.astype(np.int16) - prev.astype(np.int16)).mean())
                if d < 1.2:
                    continue
                if d > 38:  # a cut inside the clip: never morph across it
                    self.cut_after.add(len(self.paths) - 1)
            self.paths.append(p)
            self.times.append(k / fps)
            prev = g
        self.times = np.array(self.times) if self.times else np.zeros(1)

    @property
    def duration(self):
        return len(self.all_paths) / self.fps if self.all_paths else 0.0

    def _load(self, k):
        k = max(0, min(len(self.paths) - 1, k))
        if k not in self._cache:
            if len(self._cache) > 10:
                self._cache.pop(next(iter(self._cache)))
            self._cache[k] = cv2.imread(self.paths[k], cv2.IMREAD_COLOR)
        return self._cache[k]

    def _flow(self, k):
        """Flows k->k+1 and k+1->k at full size (computed at half resolution, cached)."""
        if k not in self._flows:
            if self._dis is None:
                self._dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
            a, b = self._load(k), self._load(k + 1)
            h, w = a.shape[:2]
            ga = cv2.cvtColor(cv2.resize(a, (w // 2, h // 2), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            gb = cv2.cvtColor(cv2.resize(b, (w // 2, h // 2), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            fab = cv2.resize(self._dis.calc(ga, gb, None), (w, h)) * 2
            fba = cv2.resize(self._dis.calc(gb, ga, None), (w, h)) * 2
            if len(self._flows) > 6:
                self._flows.pop(next(iter(self._flows)))
            self._flows[k] = (fab, fba)
        return self._flows[k]

    def frame(self, t):
        if not self.paths:
            return np.zeros((self.size[1], self.size[0], 3), np.uint8)
        t = max(0.0, t)
        k = int(np.searchsorted(self.times, t, side='right')) - 1
        k = max(0, min(len(self.paths) - 1, k))
        if k + 1 >= len(self.paths):
            return self._load(k)
        t0, t1 = self.times[k], self.times[k + 1]
        a = float((t - t0) / max(1e-6, t1 - t0))
        if a < 0.04:
            return self._load(k)
        if a > 0.96:
            return self._load(k + 1)
        if k in self.cut_after:
            return self._load(k if a < 0.5 else k + 1)
        A, B = self._load(k), self._load(k + 1)
        fab, fba = self._flow(k)
        h, w = A.shape[:2]
        gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
        f32 = np.float32
        wa = cv2.remap(A, (gx - f32(a) * fab[..., 0]).astype(f32), (gy - f32(a) * fab[..., 1]).astype(f32),
                       cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        wb = cv2.remap(B, (gx - f32(1 - a) * fba[..., 0]).astype(f32), (gy - f32(1 - a) * fba[..., 1]).astype(f32),
                       cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        blend = cv2.addWeighted(wa, 1 - a, wb, a, 0)
        # where the warps disagree (fast limbs, occlusions) the flow failed: take the nearer warp outright
        diff = cv2.absdiff(wa, wb).max(axis=2)
        mask = cv2.GaussianBlur((diff > 38).astype(np.float32), (0, 0), 3)[..., None]
        if float(mask.mean()) < 0.002:
            return blend
        near = wa if a < 0.5 else wb
        return (blend * (1 - mask) + near * mask).astype(np.uint8)


# ---------------------------------------------------------------- camera

def ease_io(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def punch(dt, amp, tau=0.16, overshoot=0.25):
    """Scale bump after a hit: fast rise, exponential settle with a small overshoot."""
    if dt < 0:
        return 0.0
    rise = min(1.0, dt / 0.035)
    return amp * rise * math.exp(-dt / tau) * (1 - overshoot * math.sin(min(math.pi, dt / tau * 2.2)))


def smooth_noise(t, seed, freq):
    """Cheap band-limited noise in [-1, 1] (sum of detuned sines)."""
    return (math.sin(t * freq * 6.283 + seed) * 0.6 + math.sin(t * freq * 1.618 * 6.283 + seed * 1.7) * 0.3
            + math.sin(t * freq * 2.71 * 6.283 + seed * 0.3) * 0.1)


class Camera:
    """Per-frame affine: scale, rotation (deg), offset (px); summed from layered animations."""

    def __init__(self, beats, downbeats, phrases, strong_beats, drop_start):
        self.beats, self.downbeats, self.phrases = beats, downbeats, phrases
        self.strong = strong_beats
        self.drop_start = drop_start
        self.shots = []

    def params(self, t, shot):
        """
        Every shot keeps moving: a push-in or pull-out (alternating), a slow tilt and pan drift,
        a small bounce on every beat and a big one on the strong beats; shakes on phrase starts.
        """
        span = max(0.3, shot['t1'] - shot['t0'])
        u = ease_io((t - shot['t0']) / span)
        push = shot.get('push', 0.1)
        s = shot.get('zoom', 1.0) * ((1.0 + push * u) if shot.get('push_in', True) else (1.0 + push * (1 - u)))
        dx = shot.get('drift', 0.0) * (u - 0.5) * W
        dy = shot.get('drift_y', 0.0) * (u - 0.5) * H * 0.3
        rot = shot.get('tilt', 0.0) * (u - 0.5)
        # every beat: a small bounce; strong beats (hits, downbeats) a big one
        for b in self.beats:
            if 0 <= t - b < 0.4:
                s *= 1 + punch(t - b, 0.018, tau=0.12)
        for b, g in self.strong:
            if 0 <= t - b < 0.6:
                s *= 1 + punch(t - b, 0.035 + 0.06 * g)
        # the drop
        sd = t - self.drop_start
        if 0 <= sd < 0.9:
            s *= 1 + punch(sd, 0.32, tau=0.3)   # the drop: the biggest hit of the short
        # shake on phrase starts and the drop (decaying, ~9 Hz, with a little roll)
        for p in self.phrases + [self.drop_start]:
            dt = t - p
            if 0 <= dt < 0.45:
                a = (1 - dt / 0.45) ** 2
                dx += a * 24 * smooth_noise(t, p * 13.1, 9)
                dy += a * 18 * smooth_noise(t, p * 7.7 + 3, 8)
                rot += a * 1.1 * smooth_noise(t, p * 3.3 + 1, 6)
        # transitions that move the camera: spin-in, zoom-through (in), flash-zoom (in)
        since = t - shot['t0']
        tr = shot.get('fx')
        if tr == 'spin' and since < 0.22:
            k = 1 - ease_io(since / 0.22)
            rot += shot.get('spin_dir', 1) * 28 * k
            s *= 1 + 0.35 * k
        elif tr in ('zoomthrough', 'flashzoom') and since < 0.18:
            k = 1 - ease_io(since / 0.18)
            s *= 1 + (0.6 if tr == 'zoomthrough' else 0.22) * k
        # zoom-through (out): the outgoing shot rushes forward into the cut
        nxt = shot.get('next_fx')
        to_cut = shot['t1'] - t
        if nxt == 'zoomthrough' and to_cut < 0.1:
            s *= 1 + 0.6 * ease_io(1 - to_cut / 0.1)
        elif nxt == 'spin' and to_cut < 0.08:
            rot -= shot.get('spin_dir', 1) * 14 * ease_io(1 - to_cut / 0.08)
        return s, rot, dx, dy


def warp(img, s, rot, dx, dy, out_size):
    """Scale/rotate about the output centre plus an offset; sub-pixel, no integer jitter."""
    ih, iw = img.shape[:2]
    ow, oh = out_size
    m = cv2.getRotationMatrix2D((iw / 2, ih / 2), rot, s)
    m[0, 2] += (ow - iw) / 2 + dx
    m[1, 2] += (oh - ih) / 2 + dy
    return cv2.warpAffine(img, m, (ow, oh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)


def motion_blurred(img, cam_fn, t, dt, out_size, samples=5, threshold=6.0):
    """Average several sub-frame warps when the camera moves fast (screen-space px per frame)."""
    p0, p1 = cam_fn(t - dt / 2), cam_fn(t + dt / 2)
    move = abs(p1[2] - p0[2]) + abs(p1[3] - p0[3]) + abs(p1[0] - p0[0]) * W * 0.5 + abs(p1[1] - p0[1]) * 12
    if move < threshold:
        return warp(img, *cam_fn(t), out_size)
    n = int(min(16, max(3, move / 4)))
    acc = np.zeros((out_size[1], out_size[0], 3), np.float32)
    for i in range(n):
        acc += warp(img, *cam_fn(t - dt / 2 + dt * i / (n - 1)), out_size)
    return (acc / n).astype(np.uint8)


# ---------------------------------------------------------------- transitions

def directional_blur(img, length, horizontal=True):
    length = int(max(1, length))
    if length < 3:
        return img
    k = np.zeros((length, length), np.float32)
    if horizontal:
        k[length // 2, :] = 1.0 / length
    else:
        k[:, length // 2] = 1.0 / length
    return cv2.filter2D(img, -1, k)


def zoom_blur(img, strength, steps=6):
    """Radial blur: average of progressively scaled copies about the centre."""
    if strength < 0.01:
        return img
    h, w = img.shape[:2]
    acc = img.astype(np.float32)
    for i in range(1, steps):
        s = 1 + strength * i / steps
        m = cv2.getRotationMatrix2D((w / 2, h / 2), 0, s)
        acc += cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
    return (acc / steps).astype(np.uint8)


WHIP_T = 0.16


def whip(incoming, outgoing, since, direction):
    """
    Whip pan between two shots: the outgoing one slides out as the incoming one
    slides in right behind it (edge to edge), smeared along the move in
    proportion to its speed. None once the whip is over.
    """
    if since >= WHIP_T or outgoing is None:
        return None
    h, w = incoming.shape[:2]
    k = ease_io(since / WHIP_T)
    k_next = ease_io(min(1.0, (since + 1 / 60) / WHIP_T))
    off = int(round(direction * w * (1 - k)))          # incoming shot's left edge
    speed = abs(k_next - k) * w                          # px per frame
    canvas = np.zeros_like(incoming)
    if off >= 0:
        canvas[:, off:] = incoming[:, :w - off]
        canvas[:, :off] = outgoing[:, w - off:] if off else canvas[:, :0]
    else:
        canvas[:, :w + off] = incoming[:, -off:]
        canvas[:, w + off:] = outgoing[:, :-off]
    return directional_blur(canvas, min(140, speed * 1.2))


# ---------------------------------------------------------------- overlays

def palette(frames, k=2):
    """Two vivid colours that define the shots (for the gradient overlay), BGR."""
    px = np.concatenate([cv2.resize(f, (48, 48)).reshape(-1, 3) for f in frames if f is not None]).astype(np.float32)
    hsv = cv2.cvtColor(px.reshape(1, -1, 3).astype(np.uint8), cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.float32)
    vivid = px[(hsv[:, 1] > 90) & (hsv[:, 2] > 90)]
    if len(vivid) < 50:
        return [(255, 80, 200), (255, 180, 60)]  # pink / cyan-ish default (BGR)
    _, labels, centers = cv2.kmeans(vivid, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0), 3,
                                    cv2.KMEANS_PP_CENTERS)
    order = np.argsort(-np.bincount(labels.ravel(), minlength=k))
    return [tuple(int(c) for c in centers[i]) for i in order]


class Overlays:
    """Precomputed full-frame layers: vignette, grain, the palette gradient (a few angles)."""

    def __init__(self, colors, size=(W, H), grain=5.0, seed=7):
        w, h = size
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
        self.vig = (1 - 0.32 * np.clip((r - 0.35) / 0.65, 0, 1) ** 1.7)[..., None].astype(np.float32)
        rng = np.random.default_rng(seed)
        self.grain = [rng.normal(0, grain, (h // 2, w // 2, 1)).astype(np.float32) for _ in range(4)]
        self.grads = []
        c0, c1 = np.array(colors[0], np.float32), np.array(colors[-1], np.float32)
        for ang in np.linspace(0, math.pi, 12, endpoint=False):
            u = ((xx - w / 2) * math.cos(ang) + (yy - h / 2) * math.sin(ang)) / (0.5 * math.hypot(w, h)) * 0.5 + 0.5
            u = np.clip(u, 0, 1)[..., None]
            self.grads.append((c0 * (1 - u) + c1 * u).astype(np.float32))

    def apply(self, img, t, strength=0.34):
        f = img.astype(np.float32) / 255.0
        x = t * 0.8
        i, fr = int(x) % len(self.grads), x - int(x)
        g = (self.grads[i] * (1 - fr) + self.grads[(i + 1) % len(self.grads)] * fr) / 255.0
        # soft light
        soft = np.where(g < 0.5, 2 * f * g + f * f * (1 - 2 * g), 2 * f * (1 - g) + np.sqrt(np.clip(f, 0, 1)) * (2 * g - 1))
        f = f * (1 - strength) + soft * strength
        f *= self.vig
        n = self.grain[int(t * 24) % len(self.grain)]
        f += cv2.resize(n, (f.shape[1], f.shape[0]))[..., None] / 255.0
        return np.clip(f * 255, 0, 255).astype(np.uint8)


def light_leak(size, t, seed, color=(80, 170, 255)):
    """A warm moving blob for flashes (BGR, float 0..1), screen-blended by the caller."""
    w, h = size
    small = np.zeros((h // 8, w // 8, 3), np.float32)
    cx = (0.2 + 0.6 * ((seed * 0.37 + t * 0.8) % 1.0)) * small.shape[1]
    cy = (0.3 + 0.4 * ((seed * 0.61) % 1.0)) * small.shape[0]
    cv2.circle(small, (int(cx), int(cy)), int(small.shape[1] * 0.45), [c / 255 for c in color], -1)
    small = cv2.GaussianBlur(small, (0, 0), small.shape[1] * 0.18)
    return cv2.resize(small, (w, h))


class Particles:
    """Drifting glowing embers / sparkles in the palette colours (quarter resolution, screen-blended)."""

    def __init__(self, colors, n=70, seed=11, size=(W, H)):
        rng = np.random.default_rng(seed)
        self.size = size
        self.pos = rng.uniform(0, 1, (n, 2)).astype(np.float32)
        self.vel = np.stack([rng.uniform(-0.02, 0.02, n), rng.uniform(-0.09, -0.03, n)], 1).astype(np.float32)
        self.rad = rng.uniform(0.8, 2.6, n).astype(np.float32)
        self.phase = rng.uniform(0, 6.28, n).astype(np.float32)
        self.col = [colors[i % len(colors)] for i in range(n)]

    def layer(self, t, burst=0.0):
        w, h = self.size[0] // 4, self.size[1] // 4
        out = np.zeros((h, w, 3), np.float32)
        p = (self.pos + self.vel * t) % 1.0
        tw = 0.55 + 0.45 * np.sin(t * 5.0 + self.phase)
        for i in range(len(p)):
            c = [float(min(255, v * 0.6 + 110) / 255 * tw[i] * (1 + burst)) for v in self.col[i]]
            cv2.circle(out, (int(p[i, 0] * w), int(p[i, 1] * h)), int(self.rad[i] * (1 + burst)), c, -1, cv2.LINE_AA)
        glow = cv2.GaussianBlur(out, (0, 0), 2.2)
        return cv2.resize(out + glow * 1.5, self.size)


def ring(size, progress, color=(255, 255, 255)):
    """An expanding shockwave ring (float BGR 0..1 layer)."""
    w, h = size[0] // 4, size[1] // 4
    out = np.zeros((h, w, 3), np.float32)
    r = int((0.05 + 0.9 * ease_io(progress)) * max(w, h) * 0.7)
    th = max(1, int(6 * (1 - progress)))
    cv2.circle(out, (w // 2, h // 2), r, [float(c) / 255 * (1 - progress) for c in color], th, cv2.LINE_AA)
    out = out + cv2.GaussianBlur(out, (0, 0), 4) * 2
    return cv2.resize(out, size)


def glitch(img, t, amount):
    """RGB split plus a few horizontal slices shoved sideways."""
    h, w = img.shape[:2]
    out = img.copy()
    px = int(14 * amount)
    if px:
        out[:, px:, 2] = img[:, :-px, 2]
        out[:, :-px, 0] = img[:, px:, 0]
    rng = np.random.default_rng(int(t * 60))
    for _ in range(int(6 * amount)):
        y0 = int(rng.uniform(0, h - 20))
        hh = int(rng.uniform(8, h * 0.08))
        sh = int(rng.uniform(-60, 60) * amount)
        out[y0:y0 + hh] = np.roll(out[y0:y0 + hh], sh, axis=1)
    return out


def defocus(img, amount):
    """Lens-style blur for blur transitions (fast: blur a small copy)."""
    if amount < 0.02:
        return img
    h, w = img.shape[:2]
    small = cv2.resize(img, (w // 4, h // 4), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 1 + 7 * amount)
    return cv2.addWeighted(img, 1 - min(1.0, amount * 1.3), cv2.resize(small, (w, h)), min(1.0, amount * 1.3), 0)


def screen(img, layer, amount):
    f = img.astype(np.float32) / 255.0
    out = 1 - (1 - f) * (1 - np.clip(layer * amount, 0, 1))
    return np.clip(out * 255, 0, 255).astype(np.uint8)


def title_card(text, size=150):
    """Big glowing title (RGBA PIL image) for the drop: the anime / group name."""
    path = os.path.join(FONT_DIR, 'LilitaOne-Regular.ttf')
    font = ImageFont.truetype(path, size) if os.path.exists(path) else ImageFont.load_default()
    text = text.upper()
    d0 = ImageDraw.Draw(Image.new('RGBA', (10, 10)))
    bb = d0.textbbox((0, 0), text, font=font, stroke_width=4)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    scale = min(1.0, 980 / max(1, tw))
    if scale < 1.0:
        font = ImageFont.truetype(path, int(size * scale)) if os.path.exists(path) else font
        bb = d0.textbbox((0, 0), text, font=font, stroke_width=4)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
    pad = 80
    img = Image.new('RGBA', (tw + 2 * pad, th + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((pad - bb[0], pad - bb[1]), text, font=font, fill=(255, 255, 255, 255), stroke_width=4, stroke_fill=(20, 10, 40, 255))
    glow = img.filter(ImageFilter.GaussianBlur(22))
    out = Image.new('RGBA', img.size, (0, 0, 0, 0))
    for _ in range(2):
        out.alpha_composite(glow)
    out.alpha_composite(img)
    return out
